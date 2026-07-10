from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import ThreadsPost
from app.services.threads_account_service import get_threads_account, load_threads_accounts
from app.services.threads_api_client import ThreadsApiError, get_user_threads


def sync_account_posts(account_name: str, limit: int = 50) -> dict:
    account = get_threads_account(account_name)
    result = {"account_name": account["name"], "fetched": 0, "matched": 0, "created_external": 0, "skipped": 0, "errors": []}
    try:
        rows = get_user_threads(account, limit=limit)
    except ThreadsApiError as exc:
        result["errors"].append(str(exc))
        return result

    result["fetched"] = len(rows)
    with SessionLocal() as db:
        for item in rows:
            media_id = str(item.get("id") or "").strip()
            if not media_id:
                result["skipped"] += 1
                continue
            post = db.scalar(select(ThreadsPost).where(ThreadsPost.threads_post_id == media_id))
            if post:
                post.posted_account_name = account["name"]
                post.posted_account_user_id = account["user_id"]
                post.status = "posted"
                result["matched"] += 1
                continue
            if not get_settings().import_external_threads_posts:
                result["skipped"] += 1
                continue
            db.add(
                ThreadsPost(
                    keyword="external Threads post",
                    product_name="",
                    content=str(item.get("text") or "")[:2000],
                    cta="",
                    hashtags="[]",
                    status="posted",
                    quality_score=0,
                    content_type="external",
                    content_goal="reach",
                    target_platform="threads",
                    threads_post_id=media_id,
                    posted_account_name=account["name"],
                    posted_account_user_id=account["user_id"],
                    created_at=_parse_dt(item.get("timestamp")) or datetime.now(timezone.utc),
                )
            )
            result["created_external"] += 1
        db.commit()
    return result


def sync_all_accounts_posts(limit_per_account: int = 50) -> dict:
    results = []
    for account in load_threads_accounts():
        if not account.get("enabled"):
            continue
        results.append(sync_account_posts(account["name"], limit=limit_per_account))
    return {"accounts": results, "errors": [error for row in results for error in row.get("errors", [])]}


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
