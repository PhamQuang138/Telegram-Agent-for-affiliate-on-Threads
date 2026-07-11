from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import ClickLog, DemandAction, DemandOpportunity
from app.services.threads_account_service import get_threads_account, load_threads_accounts, select_account_for_post
from app.services.threads_api_client import ThreadsApiError, ThreadsPermissionError, ThreadsTokenExpiredError, publish_reply


def list_opportunities(limit: int = 5, status: str = "new") -> list[DemandOpportunity]:
    expire_old()
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(DemandOpportunity)
                .where(DemandOpportunity.status == status)
                .order_by(DemandOpportunity.id.desc())
                .limit(max(1, min(limit, 20)))
            )
        )


def get_opportunity(opportunity_id: int) -> DemandOpportunity | None:
    expire_old()
    with SessionLocal() as db:
        return db.get(DemandOpportunity, opportunity_id)


def approve_opportunity(opportunity_id: int) -> tuple[bool, str]:
    with SessionLocal() as db:
        opp = db.get(DemandOpportunity, opportunity_id)
        if not opp:
            return False, "not found"
        if _is_expired(opp):
            opp.status = "expired"
            _log(db, opp.id, "expired", None, "ok", "")
            db.commit()
            return False, "expired"
        if opp.status not in {"new", "approved"}:
            return False, f"status={opp.status}"
        opp.status = "approved"
        opp.approved_at = datetime.now(timezone.utc)
        _log(db, opp.id, "approved", None, "ok", "")
        db.commit()
    return True, "approved"


def approve_batch(ids: list[int]) -> dict:
    max_batch = get_settings().demand_max_approve_batch
    approved = []
    for opportunity_id in ids[:max_batch]:
        ok, _message = approve_opportunity(opportunity_id)
        if ok:
            approved.append(opportunity_id)
    return {"approved": approved, "max_batch": max_batch}


def skip_opportunity(opportunity_id: int) -> tuple[bool, str]:
    with SessionLocal() as db:
        opp = db.get(DemandOpportunity, opportunity_id)
        if not opp:
            return False, "not found"
        opp.status = "skipped"
        _log(db, opp.id, "skipped", None, "ok", "")
        db.commit()
    return True, "skipped"


def edit_opportunity_comment(opportunity_id: int, comment: str) -> tuple[bool, str]:
    if not comment.strip():
        return False, "comment empty"
    if len(re.findall(r"https?://", comment)) > get_settings().demand_max_links_per_comment:
        return False, "too many links"
    with SessionLocal() as db:
        opp = db.get(DemandOpportunity, opportunity_id)
        if not opp:
            return False, "not found"
        opp.suggested_response = comment.strip()
        _log(db, opp.id, "edited", None, "ok", "")
        db.commit()
    return True, "edited"


def copy_opportunity(opportunity_id: int, approve: bool = False) -> tuple[bool, str, str]:
    with SessionLocal() as db:
        opp = db.get(DemandOpportunity, opportunity_id)
        if not opp:
            return False, "not found", ""
        if approve and opp.status == "new":
            opp.status = "approved"
            opp.approved_at = datetime.now(timezone.utc)
            _log(db, opp.id, "approved", None, "ok", "approveandcopy")
        opp.status = "manual_copied" if opp.status in {"approved", "new"} else opp.status
        opp.response_mode = "manual_copy"
        _log(db, opp.id, "copy_requested", None, "ok", "")
        _log(db, opp.id, "manual_copied", None, "ok", "")
        comment = opp.suggested_response
        db.commit()
    return True, "manual copy", comment


def reply_opportunity(opportunity_id: int, account_name: str | None = None) -> tuple[bool, str, str | None]:
    with SessionLocal() as db:
        opp = db.get(DemandOpportunity, opportunity_id)
        if not opp:
            return False, "not found", None
        if opp.status != "approved":
            return False, "not approved", None
        if _is_expired(opp):
            opp.status = "expired"
            _log(db, opp.id, "expired", None, "ok", "")
            db.commit()
            return False, "expired", None
        if not opp.external_content_id:
            opp.response_mode = "manual_copy"
            db.commit()
            return False, "missing external content id; use manual copy", opp.suggested_response
        if not opp.suggested_response.strip():
            return False, "empty comment", None
        try:
            account = get_threads_account(account_name) if account_name else select_account_for_post(
                {"keyword": opp.category, "content": opp.normalized_query, "content_goal": "affiliate"},
                load_threads_accounts(),
            )
        except Exception as exc:
            opp.response_mode = "manual_copy"
            opp.error_message = str(exc)[:500]
            db.commit()
            return False, str(exc), opp.suggested_response
        allowed, reason = _reply_allowed(db, opp, account)
        if not allowed:
            return False, reason, None
        _log(db, opp.id, "reply_attempted", account["name"], "started", "")
        db.commit()

    try:
        response = publish_reply(account, opp.external_content_id, opp.suggested_response)
    except (ThreadsPermissionError, ThreadsTokenExpiredError, ThreadsApiError) as exc:
        with SessionLocal() as db:
            failed = db.get(DemandOpportunity, opportunity_id)
            if failed:
                failed.response_mode = "manual_copy"
                failed.error_message = str(exc)[:500]
                _log(db, failed.id, "failed", account.get("name"), "manual_copy", str(exc)[:300])
                db.commit()
                return False, "API reply failed; use manual copy", failed.suggested_response
        return False, "API reply failed; use manual copy", None
    except Exception as exc:
        with SessionLocal() as db:
            failed = db.get(DemandOpportunity, opportunity_id)
            if failed:
                failed.status = "failed"
                failed.error_message = str(exc)[:500]
                _log(db, failed.id, "failed", account.get("name"), "error", str(exc)[:300])
                db.commit()
        return False, str(exc), None

    with SessionLocal() as db:
        done = db.get(DemandOpportunity, opportunity_id)
        if done:
            done.status = "replied"
            done.response_mode = "api_reply"
            done.reply_account_name = account["name"]
            done.replied_at = datetime.now(timezone.utc)
            done.external_reply_id = str(response.get("id") or response.get("post_id") or "")
            _log(db, done.id, "replied", account["name"], "ok", done.external_reply_id or "")
            db.commit()
    return True, "replied", None


def reply_batch(ids: list[int], account_name: str | None = None) -> dict:
    rows = []
    for opportunity_id in ids[: get_settings().demand_max_reply_batch]:
        ok, message, comment = reply_opportunity(opportunity_id, account_name)
        rows.append({"id": opportunity_id, "ok": ok, "message": message, "comment": comment})
    return {"results": rows}


def opstats() -> dict:
    with SessionLocal() as db:
        total = int(db.scalar(select(func.count(DemandOpportunity.id))) or 0)
        statuses = dict(
            db.execute(
                select(DemandOpportunity.status, func.count(DemandOpportunity.id))
                .group_by(DemandOpportunity.status)
            ).all()
        )
        intents = db.execute(
            select(DemandOpportunity.intent, func.count(DemandOpportunity.id))
            .group_by(DemandOpportunity.intent)
            .order_by(func.count(DemandOpportunity.id).desc())
            .limit(5)
        ).all()
        categories = db.execute(
            select(DemandOpportunity.category, func.count(DemandOpportunity.id))
            .group_by(DemandOpportunity.category)
            .order_by(func.count(DemandOpportunity.id).desc())
            .limit(5)
        ).all()
        clicks = int(db.scalar(select(func.count(ClickLog.id))) or 0)
    return {
        "total": total,
        "statuses": {str(key): int(value) for key, value in statuses.items()},
        "clicks": clicks,
        "top_intents": [{"name": str(row[0]), "count": int(row[1])} for row in intents],
        "top_categories": [{"name": str(row[0]), "count": int(row[1])} for row in categories],
    }


def expire_old() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        rows = list(db.scalars(select(DemandOpportunity).where(DemandOpportunity.status.in_(["new", "approved"]))))
        changed = False
        for row in rows:
            if _is_expired(row):
                row.status = "expired"
                _log(db, row.id, "expired", None, "ok", "")
                changed = True
        if changed:
            db.commit()


def _reply_allowed(db, opp: DemandOpportunity, account: dict) -> tuple[bool, str]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    actions = list(
        db.scalars(
            select(DemandAction).where(DemandAction.action == "replied", DemandAction.account_name == account["name"])
        )
    )
    if sum(1 for action in actions if _dt_after(action.created_at, cutoff)) >= settings.demand_max_replies_per_account_per_day:
        return False, "daily reply limit reached"
    recent_cutoff = now - timedelta(minutes=settings.demand_reply_cooldown_minutes)
    recent = db.scalar(
        select(DemandOpportunity)
        .where(
            DemandOpportunity.author_username == opp.author_username,
            DemandOpportunity.reply_account_name == account["name"],
            DemandOpportunity.replied_at >= recent_cutoff,
        )
        .limit(1)
    )
    if recent:
        return False, "cooldown for this author"
    same = db.scalar(
        select(DemandOpportunity).where(
            DemandOpportunity.platform == opp.platform,
            DemandOpportunity.external_content_id == opp.external_content_id,
            DemandOpportunity.status == "replied",
        )
    )
    if same:
        return False, "already replied to this opportunity"
    return True, "ok"


def _is_expired(opp: DemandOpportunity) -> bool:
    if not opp.expires_at:
        return False
    now = datetime.now(timezone.utc)
    expires_at = opp.expires_at
    if expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)
    return expires_at < now


def _dt_after(value: datetime | None, cutoff: datetime) -> bool:
    if not value:
        return True
    if value.tzinfo is None and cutoff.tzinfo is not None:
        cutoff = cutoff.replace(tzinfo=None)
    return value >= cutoff


def _log(db, opportunity_id: int | None, action: str, account_name: str | None, result: str, details: str) -> None:
    db.add(
        DemandAction(
            opportunity_id=opportunity_id,
            action=action,
            account_name=account_name,
            result=result,
            details=_redact(details),
        )
    )


def _redact(value: str) -> str:
    return re.sub(r"https?://\\S+", "[url]", (value or "")[:500])
