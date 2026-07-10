from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ThreadsPost, ThreadsPostMetric, ThreadsReply
from app.services.reply_analysis import analyze_reply, calculate_purchase_intent_score
from app.services.threads_account_service import get_threads_account
from app.services.threads_api_client import ThreadsApiError, get_post_replies
from app.services.threads_insights_service import _recalculate_account_scores


def sync_post_replies(post_id: int) -> dict:
    with SessionLocal() as db:
        post = db.get(ThreadsPost, post_id)
        if not post or not post.threads_post_id:
            return {"post_id": post_id, "synced": 0, "error": "post not found or missing Threads ID"}
        account = get_threads_account(post.posted_account_name) if post.posted_account_name else get_threads_account(None)
        return _sync_replies_for_post(db, post, account)


def sync_account_replies(account_name: str, limit_posts: int = 30) -> dict:
    account = get_threads_account(account_name)
    result = {"account_name": account["name"], "posts": 0, "synced": 0, "errors": []}
    with SessionLocal() as db:
        posts = list(
            db.scalars(
                select(ThreadsPost)
                .where(ThreadsPost.status == "posted", ThreadsPost.threads_post_id.is_not(None), ThreadsPost.posted_account_name == account["name"])
                .order_by(ThreadsPost.id.desc())
                .limit(limit_posts)
            )
        )
        for post in posts:
            row = _sync_replies_for_post(db, post, account)
            result["posts"] += 1
            result["synced"] += int(row.get("synced") or 0)
            if row.get("error"):
                result["errors"].append(f"#{post.id}: {row['error']}")
        db.commit()
    return result


def _sync_replies_for_post(db, post: ThreadsPost, account: dict) -> dict:
    try:
        replies = get_post_replies(account, post.threads_post_id or "", limit=100)
    except ThreadsApiError as exc:
        return {"post_id": post.id, "synced": 0, "error": str(exc)}

    saved = 0
    analyses: list[dict] = []
    now = datetime.now(timezone.utc)
    for item in replies:
        reply_id = str(item.get("id") or "").strip()
        if not reply_id:
            continue
        text = str(item.get("text") or "")[:2000]
        analysis = analyze_reply(text)
        analyses.append(analysis)
        row = db.scalar(
            select(ThreadsReply).where(
                ThreadsReply.account_name == account["name"],
                ThreadsReply.reply_media_id == reply_id,
            )
        )
        if not row:
            row = ThreadsReply(
                post_id=post.id,
                threads_media_id=post.threads_post_id or "",
                reply_media_id=reply_id,
                account_name=account["name"],
            )
            db.add(row)
        row.post_id = post.id
        row.threads_media_id = post.threads_post_id or ""
        row.reply_user_id = str(item.get("user_id") or item.get("from", {}).get("id") or "")[:255] or None
        row.reply_username = str(item.get("username") or item.get("from", {}).get("username") or "")[:255] or None
        row.reply_text = text
        row.intent = analysis["intent"]
        row.sentiment = analysis["sentiment"]
        row.asks_for_link = 1 if analysis["asks_for_link"] else 0
        row.asks_for_price = 1 if analysis["asks_for_price"] else 0
        row.product_interest = 1 if analysis["product_interest"] else 0
        row.is_spam = 1 if analysis["is_spam"] else 0
        row.created_at = _parse_dt(item.get("timestamp"))
        row.synced_at = now
        saved += 1

    score = calculate_purchase_intent_score(analyses)
    metric = db.scalar(
        select(ThreadsPostMetric).where(
            ThreadsPostMetric.account_name == account["name"],
            ThreadsPostMetric.threads_media_id == post.threads_post_id,
        )
    )
    if not metric:
        metric = ThreadsPostMetric(
            post_id=post.id,
            threads_media_id=post.threads_post_id or "",
            account_name=account["name"],
            threads_user_id=account.get("user_id"),
            click_count=int(post.click_count or 0),
        )
        db.add(metric)
    metric.purchase_intent_score = score
    metric.replies = max(metric.replies or 0, len(replies))
    metric.synced_at = now
    _recalculate_account_scores(db, account["name"])
    db.commit()
    return {"post_id": post.id, "synced": saved, "purchase_intent_score": score}


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
