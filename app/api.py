from json import JSONDecodeError
import logging
import json
import random
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request
import httpx
from pydantic import BaseModel
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import Application

from app.db import get_db, init_db
from app.config import get_settings
from app.db import SessionLocal
from app.models import AppSetting
from app.services.daily_link_cleanup import cleanup_expired_daily_links
from app.services.admin_curated_links import categories_for_type, cleanup_expired_admin_links, get_links_for_delivery
from app.services.threads_repository import get_post_by_slug, get_post_link_by_slug, log_click
from app.telegram_bot import _channel_link_keyboard, _channel_link_post_text, build_application

app = FastAPI(title="POD Bot Tracking API")
_telegram_application: Application | None = None
logger = logging.getLogger(__name__)
AUTO_RANDOM_LAST_RUN_KEY = "auto_random_links:last_run_ts"
AUTO_RANDOM_HISTORY_KEY = "auto_random_links:recent_pairs"


class DemandIntakeBody(BaseModel):
    platform: str = "threads"
    url: str | None = None
    text: str
    author_username: str | None = None


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/api/health")
def api_health() -> dict:
    settings = get_settings()
    database_ok = False
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            database_ok = True
    except Exception:
        database_ok = False
    return {
        "status": "ok" if database_ok else "degraded",
        "database": database_ok,
        "telegram_webhook_mode": bool(settings.telegram_use_webhook or settings.vercel),
        "vercel": bool(settings.vercel),
    }


async def get_telegram_application() -> Application:
    global _telegram_application
    if _telegram_application is None:
        _telegram_application = build_application()
        await _telegram_application.initialize()
        await _telegram_application.start()
    return _telegram_application


async def process_telegram_update(payload: dict) -> None:
    application = await get_telegram_application()
    update = Update.de_json(payload, application.bot)
    await application.process_update(update)


def claim_telegram_update(update_id: int | None) -> bool:
    if update_id is None:
        return True
    key = f"telegram_update:{update_id}"
    with SessionLocal() as db:
        if db.get(AppSetting, key):
            return False
        db.add(AppSetting(key=key, value="processing", updated_at=""))
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


@app.post("/api/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    settings = get_settings()
    if settings.telegram_webhook_secret:
        if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")
    try:
        payload = await request.json()
    except JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    update_id = payload.get("update_id")
    if not claim_telegram_update(int(update_id) if isinstance(update_id, int) else None):
        return {"ok": True, "duplicate": True}
    try:
        await process_telegram_update(payload)
    except Exception as exc:
        logger.exception("Telegram webhook update processing failed")
        return {"ok": False, "processed": False}
    return {"ok": True}


@app.get("/api/cron/cleanup-daily-links")
def cleanup_daily_links_cron(
    x_cron_secret: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    secret: str | None = None,
) -> dict:
    settings = get_settings()
    bearer = authorization.removeprefix("Bearer ").strip() if authorization else ""
    provided = x_cron_secret or bearer or secret or ""
    if settings.cron_secret and provided != settings.cron_secret:
        raise HTTPException(status_code=401, detail="Invalid cron secret")
    legacy = cleanup_expired_daily_links(settings.daily_link_retention_days)
    try:
        with SessionLocal() as db:
            admin = cleanup_expired_admin_links(db)
    except Exception:
        logger.exception("Admin curated cleanup failed")
        admin = {"links_deactivated": 0, "requests_expired": 0, "error": "cleanup_failed"}
    if isinstance(legacy, dict):
        return {**legacy, "admin_curated": admin}
    return {"legacy_daily": legacy, "admin_curated": admin}


def _cron_authorized(x_cron_secret: str | None, authorization: str | None, secret: str | None) -> None:
    settings = get_settings()
    bearer = authorization.removeprefix("Bearer ").strip() if authorization else ""
    provided = x_cron_secret or bearer or secret or ""
    if settings.cron_secret and provided != settings.cron_secret:
        raise HTTPException(status_code=401, detail="Invalid cron secret")


def _should_run_random_link_cron(db: Session) -> tuple[bool, str]:
    settings = get_settings()
    min_seconds = max(1, settings.auto_publish_random_links_min_hours) * 3600
    max_seconds = max(settings.auto_publish_random_links_max_hours, settings.auto_publish_random_links_min_hours) * 3600
    now_ts = int(time.time())
    row = db.get(AppSetting, AUTO_RANDOM_LAST_RUN_KEY)
    last_ts = int(row.value) if row and str(row.value).isdigit() else 0
    elapsed = now_ts - last_ts if last_ts else max_seconds
    if elapsed < min_seconds:
        return False, "too_soon"
    if elapsed >= max_seconds:
        return True, "max_elapsed"
    span = max(1, max_seconds - min_seconds)
    probability = (elapsed - min_seconds) / span
    return random.random() <= probability, "random_window"


def _mark_random_link_cron_run(db: Session) -> None:
    now_ts = str(int(time.time()))
    row = db.get(AppSetting, AUTO_RANDOM_LAST_RUN_KEY)
    if row:
        row.value = now_ts
        row.updated_at = now_ts
    else:
        db.add(AppSetting(key=AUTO_RANDOM_LAST_RUN_KEY, value=now_ts, updated_at=now_ts))
    db.commit()


def _random_link_history(db: Session) -> list[str]:
    row = db.get(AppSetting, AUTO_RANDOM_HISTORY_KEY)
    if not row:
        return []
    try:
        value = json.loads(row.value)
    except (TypeError, JSONDecodeError):
        return []
    return [str(item) for item in value if str(item)]


def _remember_random_link_pairs(db: Session, sent: list[dict]) -> None:
    if not sent:
        return
    settings = get_settings()
    limit = max(3, int(getattr(settings, "auto_publish_random_links_history_size", 12) or 12))
    history = _random_link_history(db)
    new_items = [f"{item['link_type_id']}:{item['category_id']}" for item in sent]
    merged: list[str] = []
    for item in [*new_items, *history]:
        if item not in merged:
            merged.append(item)
    merged = merged[:limit]
    now_ts = str(int(time.time()))
    row = db.get(AppSetting, AUTO_RANDOM_HISTORY_KEY)
    value = json.dumps(merged, ensure_ascii=False)
    if row:
        row.value = value
        row.updated_at = now_ts
    else:
        db.add(AppSetting(key=AUTO_RANDOM_HISTORY_KEY, value=value, updated_at=now_ts))
    db.commit()


def _random_link_candidates(db: Session, count: int) -> list[dict]:
    pairs: list[dict] = []
    for link_type in categories_for_random_types(db):
        for category in categories_for_type(db, link_type):
            pair_key = f"{link_type}:{category['category_id']}"
            pairs.append({"link_type_id": link_type, "category_id": category["category_id"], "count": category["count"], "pair_key": pair_key})
    history = set(_random_link_history(db))
    fresh = [item for item in pairs if item["pair_key"] not in history]
    fallback = [item for item in pairs if item["pair_key"] in history]
    random.shuffle(fresh)
    random.shuffle(fallback)
    return [*fresh, *fallback][:count]


def categories_for_random_types(db: Session) -> list[str]:
    from app.services.admin_curated_links import active_type_counts

    types = [item["link_type_id"] for item in active_type_counts(db)]
    random.shuffle(types)
    return types


def _send_telegram_channel_post(text_value: str, reply_markup: dict) -> dict:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    if not settings.telegram_community_group_id:
        raise RuntimeError("TELEGRAM_COMMUNITY_GROUP_ID missing")
    with httpx.Client(timeout=20) as client:
        response = client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            data={
                "chat_id": settings.telegram_community_group_id,
                "text": text_value,
                "reply_markup": json.dumps(reply_markup, ensure_ascii=False),
                "disable_web_page_preview": "true",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(str(payload))
        return payload


@app.get("/api/cron/publish-random-links")
def publish_random_links_cron(
    x_cron_secret: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    secret: str | None = None,
) -> dict:
    _cron_authorized(x_cron_secret, authorization, secret)
    settings = get_settings()
    if not settings.auto_publish_random_links_enabled:
        return {"ok": True, "skipped": "disabled"}
    with SessionLocal() as db:
        should_run, reason = _should_run_random_link_cron(db)
        if not should_run:
            return {"ok": True, "skipped": reason}
        candidates = _random_link_candidates(db, max(1, settings.auto_publish_random_links_count))
        sent = []
        errors = []
        for item in candidates:
            links = get_links_for_delivery(db, item["link_type_id"], item["category_id"], limit=20, hard_cap=20)
            if not links:
                continue
            try:
                markup = _channel_link_keyboard(item["link_type_id"], item["category_id"]).to_dict()
                payload = _send_telegram_channel_post(
                    _channel_link_post_text(item["link_type_id"], item["category_id"], links),
                    markup,
                )
                sent.append(
                    {
                        "link_type_id": item["link_type_id"],
                        "category_id": item["category_id"],
                        "message_id": ((payload.get("result") or {}).get("message_id")),
                    }
                )
                time.sleep(0.5)
            except Exception as exc:
                logger.exception("Random link publish failed")
                errors.append({"link_type_id": item["link_type_id"], "category_id": item["category_id"], "error": str(exc)})
        if sent:
            _mark_random_link_cron_run(db)
            _remember_random_link_pairs(db, sent)
        return {"ok": True, "reason": reason, "sent": sent, "errors": errors}


@app.get("/go/{slug}")
def redirect_tracking(slug: str, request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    post_link = get_post_link_by_slug(db, slug)
    post = post_link.post if post_link else get_post_by_slug(db, slug)
    affiliate_url = post_link.affiliate_url if post_link else (post.affiliate_url if post else None)

    if not post or not affiliate_url or post.status == "deleted":
        raise HTTPException(status_code=404, detail="Tracking link not found")

    forwarded_for = request.headers.get("x-forwarded-for", "")
    ip = forwarded_for.split(",")[0].strip() if forwarded_for else (request.client.host if request.client else "")

    log_click(
        db,
        post_id=post.id,
        slug=slug,
        referrer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent"),
        ip=ip,
    )

    return RedirectResponse(affiliate_url, status_code=302)


@app.post("/api/demand-intake")
def demand_intake(body: DemandIntakeBody, x_demand_intake_key: str | None = Header(default=None)) -> dict:
    settings = get_settings()
    if not settings.demand_intake_api_enabled or not settings.demand_intake_api_key:
        raise HTTPException(status_code=404, detail="Demand intake API disabled")
    if x_demand_intake_key != settings.demand_intake_api_key:
        raise HTTPException(status_code=401, detail="Invalid demand intake key")
    from app.services.manual_demand_intake import create_manual_demand

    result = create_manual_demand(
        text=body.text,
        url=body.url,
        platform=body.platform,
        author_username=body.author_username,
        intake_source="browser_extension",
    )
    if not result["created"]:
        return result
    return {"ok": True, **result}
