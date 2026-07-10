from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import ThreadsDemandAction, ThreadsDemandOpportunity, ThreadsPostLink
from app.services.demand_comment_generator import generate_demand_comment
from app.services.demand_product_matcher import match_products_for_demand
from app.services.purchase_intent import classify_purchase_intent
from app.services.threads_account_service import get_threads_account, load_threads_accounts, select_account_for_post
from app.services.threads_api_client import ThreadsApiError, publish_reply, search_keyword
from app.services.trend_service import get_trending_keywords

INTENT_PHRASES = [
    "xin link",
    "cho xin link",
    "mua ở đâu",
    "mọi người recommend",
    "nên mua loại nào",
    "có mẫu nào",
    "tìm giúp",
    "cần tìm",
    "loại nào ổn",
    "có ai biết chỗ mua",
    "dưới 100k",
    "dưới 200k",
    "budget",
    "giá bao nhiêu",
    "có shop nào",
    "nên chọn",
    "tư vấn mua",
]


def build_scan_keywords(manual_keyword: str | None = None, limit: int = 30) -> list[str]:
    seeds: list[str] = []
    if manual_keyword:
        seeds.append(manual_keyword.strip())
    with SessionLocal() as db:
        product_rows = list(db.scalars(select(ThreadsPostLink.product_name).order_by(ThreadsPostLink.id.desc()).limit(200)))
        seeds.extend(_product_keyword(name) for name in product_rows)
        try:
            seeds.extend(item["keyword"] for item in get_trending_keywords(db, limit=10))
        except Exception:
            pass
    seeds = [seed for seed in dict.fromkeys(_clean(seed) for seed in seeds) if seed]
    if manual_keyword:
        phrases = ["xin link", "mua ở đâu", "recommend", "nên mua loại nào", "dưới 200k"]
        keywords = [manual_keyword.strip(), *[f"{phrase} {manual_keyword.strip()}" for phrase in phrases]]
    else:
        keywords = []
        for seed in seeds[:10]:
            for phrase in ["xin link", "mua ở đâu", "recommend", "nên mua", "dưới 200k"]:
                keywords.append(f"{phrase} {seed}")
    keywords.extend(INTENT_PHRASES[:5])
    return list(dict.fromkeys(keyword for keyword in keywords if keyword.strip()))[: max(1, limit)]


def scan_threads_demand(
    account_name: str,
    keywords: list[str],
    limit_per_keyword: int = 20,
    max_opportunities: int = 10,
) -> dict:
    settings = get_settings()
    result = {
        "account_name": account_name,
        "keywords_scanned": [],
        "posts_fetched": 0,
        "opportunities_created": 0,
        "duplicates_skipped": 0,
        "low_intent_skipped": 0,
        "errors": [],
    }
    if not settings.threads_demand_scanner_enabled:
        result["errors"].append("THREADS_DEMAND_SCANNER_ENABLED=false")
        return result
    try:
        account = get_threads_account(account_name)
    except Exception as exc:
        result["errors"].append(str(exc))
        return result

    created = 0
    for keyword in keywords:
        if created >= max_opportunities:
            break
        result["keywords_scanned"].append(keyword)
        try:
            rows = search_keyword(account, keyword, limit=limit_per_keyword)
        except ThreadsApiError as exc:
            result["errors"].append(f"{keyword}: {exc}")
            continue
        result["posts_fetched"] += len(rows)
        for row in rows:
            if created >= max_opportunities:
                break
            normalized = _normalize_result(row, keyword)
            if not normalized["external_post_id"]:
                continue
            if str(normalized.get("author_id") or "") == str(account.get("user_id") or ""):
                result["duplicates_skipped"] += 1
                continue
            with SessionLocal() as db:
                if db.scalar(select(ThreadsDemandOpportunity).where(ThreadsDemandOpportunity.external_post_id == normalized["external_post_id"])):
                    result["duplicates_skipped"] += 1
                    continue
                if normalized.get("author_username"):
                    similar_rows = list(
                        db.scalars(
                            select(ThreadsDemandOpportunity)
                            .where(ThreadsDemandOpportunity.author_username == normalized["author_username"])
                            .order_by(ThreadsDemandOpportunity.id.desc())
                            .limit(20)
                        )
                    )
                    if any(_similar(row.source_text_excerpt, normalized["text"]) > 0.80 for row in similar_rows):
                        result["duplicates_skipped"] += 1
                        continue
            intent = classify_purchase_intent(normalized["text"], keyword)
            if not intent["eligible"] or intent["purchase_intent_score"] < settings.threads_demand_min_score:
                result["low_intent_skipped"] += 1
                continue
            products = match_products_for_demand(intent["category"], intent["normalized_query"], intent["constraints"], limit=settings.threads_demand_max_links_per_comment)
            if not products:
                result["low_intent_skipped"] += 1
                continue
            comment = generate_demand_comment(normalized, intent, products)
            if not comment["comment"] or comment["quality_score"] < 60:
                result["low_intent_skipped"] += 1
                continue
            with SessionLocal() as db:
                opportunity = ThreadsDemandOpportunity(
                    external_post_id=normalized["external_post_id"],
                    author_id=normalized.get("author_id") or None,
                    author_username=normalized.get("author_username") or None,
                    permalink=normalized.get("permalink") or None,
                    source_text_excerpt=normalized["text"][:500],
                    matched_keyword=keyword,
                    intent=intent["intent"],
                    purchase_intent_score=float(intent["purchase_intent_score"]),
                    category=intent["category"],
                    normalized_query=intent["normalized_query"],
                    constraints_json=json.dumps(intent["constraints"], ensure_ascii=False),
                    matched_products_json=json.dumps(products, ensure_ascii=False),
                    suggested_comment=comment["comment"],
                    status="new",
                    scan_account_name=account["name"],
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.threads_demand_opportunity_ttl_hours),
                )
                db.add(opportunity)
                db.flush()
                _log_action(db, opportunity.id, "created", account["name"], "ok", f"keyword={keyword}")
                db.commit()
            created += 1
            result["opportunities_created"] += 1
    with SessionLocal() as db:
        _log_action(db, None, "scanned", account["name"], "ok", json.dumps(result, ensure_ascii=False)[:500])
        db.commit()
    return result


def list_opportunities(limit: int = 5, status: str = "new") -> list[ThreadsDemandOpportunity]:
    _expire_old()
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(ThreadsDemandOpportunity)
                .where(ThreadsDemandOpportunity.status == status)
                .order_by(ThreadsDemandOpportunity.id.desc())
                .limit(max(1, min(limit, 20)))
            )
        )


def get_opportunity(opportunity_id: int) -> ThreadsDemandOpportunity | None:
    _expire_old()
    with SessionLocal() as db:
        return db.get(ThreadsDemandOpportunity, opportunity_id)


def approve_opportunity(opportunity_id: int) -> tuple[bool, str]:
    with SessionLocal() as db:
        opp = db.get(ThreadsDemandOpportunity, opportunity_id)
        if not opp:
            return False, "not found"
        if _is_expired(opp):
            opp.status = "expired"
            db.commit()
            return False, "expired"
        if opp.status not in {"new", "approved"}:
            return False, f"status={opp.status}"
        opp.status = "approved"
        opp.approved_at = datetime.now(timezone.utc)
        _log_action(db, opp.id, "approved", None, "ok", "")
        db.commit()
        return True, "approved"


def approve_batch(ids: list[int]) -> dict:
    max_batch = get_settings().threads_demand_max_approve_batch
    ids = ids[:max_batch]
    return {"approved": [item for item in ids if approve_opportunity(item)[0]], "max_batch": max_batch}


def skip_opportunity(opportunity_id: int) -> tuple[bool, str]:
    with SessionLocal() as db:
        opp = db.get(ThreadsDemandOpportunity, opportunity_id)
        if not opp:
            return False, "not found"
        opp.status = "skipped"
        _log_action(db, opp.id, "skipped", None, "ok", "")
        db.commit()
        return True, "skipped"


def edit_opportunity_comment(opportunity_id: int, comment: str) -> tuple[bool, str]:
    if not comment.strip():
        return False, "comment empty"
    if len(re.findall(r"https?://", comment)) > get_settings().threads_demand_max_links_per_comment:
        return False, "too many links"
    with SessionLocal() as db:
        opp = db.get(ThreadsDemandOpportunity, opportunity_id)
        if not opp:
            return False, "not found"
        opp.suggested_comment = comment.strip()
        _log_action(db, opp.id, "edited", None, "ok", "")
        db.commit()
        return True, "edited"


def reply_opportunity(opportunity_id: int, account_name: str | None = None) -> tuple[bool, str]:
    with SessionLocal() as db:
        opp = db.get(ThreadsDemandOpportunity, opportunity_id)
        if not opp:
            return False, "not found"
        if opp.status != "approved":
            return False, "not approved"
        if _is_expired(opp):
            opp.status = "expired"
            db.commit()
            return False, "expired"
        if opp.replied_at or opp.threads_reply_id:
            return False, "already replied"
        if not opp.suggested_comment.strip():
            return False, "empty comment"
        if len(re.findall(r"https?://", opp.suggested_comment)) > get_settings().threads_demand_max_links_per_comment:
            return False, "too many links"
        try:
            account = get_threads_account(account_name) if account_name else select_account_for_post(
                {"keyword": opp.category, "content": opp.normalized_query, "content_goal": "affiliate"},
                load_threads_accounts(),
            )
        except Exception as exc:
            return False, str(exc)
        allowed, reason = _reply_allowed(db, opp, account)
        if not allowed:
            return False, reason
        _log_action(db, opp.id, "reply_attempted", account["name"], "started", "")
        db.commit()

    try:
        response = publish_reply(account, opp.external_post_id, opp.suggested_comment)
    except Exception as exc:
        with SessionLocal() as db:
            failed = db.get(ThreadsDemandOpportunity, opportunity_id)
            if failed:
                failed.status = "failed"
                failed.error_message = str(exc)[:500]
                _log_action(db, failed.id, "failed", account["name"], "error", str(exc)[:500])
                db.commit()
        return False, str(exc)

    with SessionLocal() as db:
        done = db.get(ThreadsDemandOpportunity, opportunity_id)
        if done:
            done.status = "replied"
            done.reply_account_name = account["name"]
            done.replied_at = datetime.now(timezone.utc)
            done.threads_reply_id = str(response.get("id") or response.get("post_id") or "")
            _log_action(db, done.id, "replied", account["name"], "ok", done.threads_reply_id or "")
            db.commit()
    return True, "replied"


def reply_batch(ids: list[int], account_name: str | None = None) -> dict:
    ids = ids[: get_settings().threads_demand_max_reply_batch]
    rows = []
    for opportunity_id in ids:
        ok, message = reply_opportunity(opportunity_id, account_name)
        rows.append({"id": opportunity_id, "ok": ok, "message": message})
        time.sleep(2)
    return {"results": rows}


def _normalize_result(row: dict, keyword: str) -> dict:
    author = row.get("from") if isinstance(row.get("from"), dict) else {}
    return {
        "platform": "threads",
        "external_post_id": str(row.get("id") or row.get("media_id") or "").strip(),
        "author_id": str(row.get("author_id") or row.get("user_id") or author.get("id") or "").strip(),
        "author_username": str(row.get("author_username") or row.get("username") or author.get("username") or "").strip(),
        "text": str(row.get("text") or row.get("caption") or "").strip(),
        "permalink": str(row.get("permalink") or "").strip(),
        "created_at": str(row.get("timestamp") or row.get("created_at") or "").strip(),
        "matched_keyword": keyword,
    }


def _reply_allowed(db, opp: ThreadsDemandOpportunity, account: dict) -> tuple[bool, str]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    day_start = now - timedelta(hours=24)
    replied_today = int(
        db.scalar(
            select(func.count(ThreadsDemandAction.id)).where(
                ThreadsDemandAction.action == "replied",
                ThreadsDemandAction.account_name == account["name"],
                ThreadsDemandAction.created_at >= day_start,
            )
        )
        or 0
    )
    if replied_today >= settings.threads_demand_max_replies_per_account_per_day:
        return False, "daily reply limit reached"
    cooldown = now - timedelta(minutes=settings.threads_demand_reply_cooldown_minutes)
    recent_author_reply = db.scalar(
        select(ThreadsDemandOpportunity)
        .where(
            ThreadsDemandOpportunity.author_username == opp.author_username,
            ThreadsDemandOpportunity.reply_account_name == account["name"],
            ThreadsDemandOpportunity.replied_at >= cooldown,
        )
        .limit(1)
    )
    if recent_author_reply:
        return False, "cooldown for this author"
    recent_comments = list(
        db.scalars(
            select(ThreadsDemandOpportunity.suggested_comment)
            .where(ThreadsDemandOpportunity.reply_account_name == account["name"], ThreadsDemandOpportunity.replied_at >= day_start)
            .limit(10)
        )
    )
    if any(_similar(opp.suggested_comment, comment) > 0.85 for comment in recent_comments):
        return False, "comment too similar to recent replies"
    existing_same_post = db.scalar(
        select(ThreadsDemandOpportunity).where(
            ThreadsDemandOpportunity.external_post_id == opp.external_post_id,
            ThreadsDemandOpportunity.status == "replied",
        )
    )
    if existing_same_post:
        return False, "already replied to this post"
    return True, "ok"


def _expire_old() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        rows = list(db.scalars(select(ThreadsDemandOpportunity).where(ThreadsDemandOpportunity.status.in_(["new", "approved"]), ThreadsDemandOpportunity.expires_at < now)))
        for row in rows:
            row.status = "expired"
        if rows:
            db.commit()


def _is_expired(opp: ThreadsDemandOpportunity) -> bool:
    if not opp.expires_at:
        return False
    now = datetime.now(timezone.utc)
    expires_at = opp.expires_at
    if expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    return expires_at < now


def _log_action(db, opportunity_id: int | None, action: str, account_name: str | None, result: str, details: str) -> None:
    db.add(
        ThreadsDemandAction(
            opportunity_id=opportunity_id,
            action=action,
            account_name=account_name,
            result=result,
            details=details[:500],
        )
    )


def _product_keyword(name: str) -> str:
    text = _clean(name)
    tokens = [token for token in text.split() if len(token) >= 3 and token not in {"shopee", "chinh", "hang", "sale", "nam", "nu"}]
    return " ".join(tokens[:3])


def _clean(value: str) -> str:
    value = re.sub(r"\[[^\]]+\]|https?://\S+", " ", value or "")
    value = re.sub(r"[^\w\sàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ-]", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()[:100]


def _similar(left: str, right: str) -> float:
    a = set(_clean(left).split())
    b = set(_clean(right).split())
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))
