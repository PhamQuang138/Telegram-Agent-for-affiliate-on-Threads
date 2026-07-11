from json import JSONDecodeError
import logging

from fastapi import Depends, FastAPI, Header, HTTPException, Request
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
from app.services.admin_curated_links import cleanup_expired_admin_links
from app.services.threads_repository import get_post_by_slug, get_post_link_by_slug, log_click
from app.telegram_bot import build_application

app = FastAPI(title="POD Bot Tracking API")
_telegram_application: Application | None = None
logger = logging.getLogger(__name__)


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
