from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ThreadsPost, ThreadsPostMetric
from app.services.threads_account_service import get_threads_account, load_threads_accounts
from app.services.threads_api_client import ThreadsApiError, get_post_insights


def sync_post_insights(post_id: int) -> dict:
    with SessionLocal() as db:
        post = db.get(ThreadsPost, post_id)
        if not post or not post.threads_post_id:
            return {"post_id": post_id, "synced": False, "error": "post not found or missing Threads ID"}
        account = get_threads_account(post.posted_account_name) if post.posted_account_name else get_threads_account(None)
        return _sync_one(db, post, account)


def sync_account_insights(account_name: str, limit: int = 50) -> dict:
    account = get_threads_account(account_name)
    result = {"account_name": account["name"], "synced": 0, "skipped": 0, "errors": []}
    with SessionLocal() as db:
        posts = list(
            db.scalars(
                select(ThreadsPost)
                .where(ThreadsPost.status == "posted", ThreadsPost.threads_post_id.is_not(None), ThreadsPost.posted_account_name == account["name"])
                .order_by(ThreadsPost.id.desc())
                .limit(limit)
            )
        )
        for post in posts:
            row = _sync_one(db, post, account)
            if row.get("synced"):
                result["synced"] += 1
            else:
                result["skipped"] += 1
                if row.get("error"):
                    result["errors"].append(f"#{post.id}: {row['error']}")
        _recalculate_account_scores(db, account["name"])
        db.commit()
    return result


def sync_all_accounts_insights(limit_per_account: int = 50) -> dict:
    results = []
    for account in load_threads_accounts():
        if account.get("enabled"):
            results.append(sync_account_insights(account["name"], limit=limit_per_account))
    return {"accounts": results, "errors": [error for row in results for error in row.get("errors", [])]}


def thread_stats(post_id: int) -> dict:
    with SessionLocal() as db:
        post = db.get(ThreadsPost, post_id)
        if not post:
            return {}
        metric = db.scalar(
            select(ThreadsPostMetric)
            .where(ThreadsPostMetric.post_id == post_id)
            .order_by(ThreadsPostMetric.synced_at.desc())
            .limit(1)
        )
        if not metric:
            return {"post_id": post_id, "threads_media_id": post.threads_post_id, "click_count": post.click_count}
        return _metric_dict(metric)


def _sync_one(db, post: ThreadsPost, account: dict) -> dict:
    try:
        insights = get_post_insights(account, post.threads_post_id or "")
    except ThreadsApiError as exc:
        return {"post_id": post.id, "synced": False, "error": str(exc)}
    if not insights:
        return {"post_id": post.id, "synced": False, "error": "no insights returned"}

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
        )
        db.add(metric)
    metric.post_id = post.id
    metric.threads_user_id = account.get("user_id")
    metric.views = _int(insights.get("views") or insights.get("views_count"))
    metric.likes = _int(insights.get("likes") or insights.get("like_count"))
    metric.replies = _int(insights.get("replies") or insights.get("reply_count") or insights.get("comments"))
    metric.reposts = _int(insights.get("reposts") or insights.get("repost_count"))
    metric.quotes = _int(insights.get("quotes") or insights.get("quote_count"))
    metric.shares = _int(insights.get("shares")) if "shares" in insights else None
    metric.click_count = int(post.click_count or 0)
    metric.engagement_rate = _engagement_rate(metric.views, metric.likes, metric.replies, metric.reposts, metric.quotes)
    metric.affiliate_ctr = (metric.click_count / max(metric.views, 1)) if metric.views else None
    metric.synced_at = datetime.now(timezone.utc)
    metric.updated_at = datetime.now(timezone.utc)
    db.flush()
    _recalculate_account_scores(db, account["name"])
    db.commit()
    return {"post_id": post.id, "synced": True, "metrics": _metric_dict(metric)}


def _recalculate_account_scores(db, account_name: str) -> None:
    rows = list(db.scalars(select(ThreadsPostMetric).where(ThreadsPostMetric.account_name == account_name)))
    if not rows:
        return
    max_views = max([row.views or 0 for row in rows] + [1])
    max_engagement = max([row.engagement_rate or 0 for row in rows] + [0.0001])
    max_ctr = max([row.affiliate_ctr or 0 for row in rows] + [0.0001])
    max_intent = max([row.purchase_intent_score or 0 for row in rows] + [1])
    for row in rows:
        normalized_views = ((row.views or 0) / max_views) * 100
        engagement_normalized = ((row.engagement_rate or 0) / max_engagement) * 100
        ctr_normalized = ((row.affiliate_ctr or 0) / max_ctr) * 100 if row.affiliate_ctr is not None else 0
        intent_normalized = ((row.purchase_intent_score or 0) / max_intent) * 100 if row.purchase_intent_score is not None else 0
        if row.purchase_intent_score is None:
            score = ctr_normalized * 0.55 + engagement_normalized * 0.30 + normalized_views * 0.15
        else:
            score = ctr_normalized * 0.45 + engagement_normalized * 0.25 + intent_normalized * 0.20 + normalized_views * 0.10
        row.performance_score = round(max(0, min(100, score)), 3)
        post = db.get(ThreadsPost, row.post_id) if row.post_id else None
        if post:
            post.impression_estimate = row.views or post.impression_estimate
            post.performance_score = row.performance_score


def _engagement_rate(views: int, likes: int, replies: int, reposts: int, quotes: int) -> float | None:
    if not views:
        return None
    return round((likes + replies * 2.0 + reposts * 2.5 + quotes * 3.0) / max(views, 1), 6)


def _metric_dict(metric: ThreadsPostMetric) -> dict:
    return {
        "post_id": metric.post_id,
        "threads_media_id": metric.threads_media_id,
        "account_name": metric.account_name,
        "views": metric.views,
        "likes": metric.likes,
        "replies": metric.replies,
        "reposts": metric.reposts,
        "quotes": metric.quotes,
        "shares": metric.shares,
        "click_count": metric.click_count,
        "affiliate_ctr": metric.affiliate_ctr,
        "engagement_rate": metric.engagement_rate,
        "purchase_intent_score": metric.purchase_intent_score,
        "performance_score": metric.performance_score,
        "synced_at": metric.synced_at.isoformat() if metric.synced_at else "",
    }


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
