import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import TopicMemory


def is_topic_recently_used(keyword: str, hours: int = 48) -> bool:
    if not keyword.strip():
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    with SessionLocal() as db:
        row = db.scalar(
            select(TopicMemory)
            .where(TopicMemory.keyword == keyword.strip().lower(), TopicMemory.created_at >= cutoff)
            .order_by(TopicMemory.id.desc())
        )
        return row is not None


def get_recent_topics(limit: int = 50) -> list[dict]:
    with SessionLocal() as db:
        rows = list(db.scalars(select(TopicMemory).order_by(TopicMemory.id.desc()).limit(limit)))
        return [_row_to_dict(row) for row in rows]


def record_topic_usage(keyword: str, product_ids: list[int], post_id: int | None = None) -> None:
    if not keyword.strip():
        return
    with SessionLocal() as db:
        db.add(
            TopicMemory(
                keyword=keyword.strip().lower(),
                product_ids_json=json.dumps(product_ids[:10], ensure_ascii=False),
                post_id=post_id,
            )
        )
        db.commit()


def _row_to_dict(row: TopicMemory) -> dict:
    try:
        product_ids = json.loads(row.product_ids_json)
    except json.JSONDecodeError:
        product_ids = []
    return {
        "keyword": row.keyword,
        "product_ids": product_ids,
        "post_id": row.post_id,
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }
