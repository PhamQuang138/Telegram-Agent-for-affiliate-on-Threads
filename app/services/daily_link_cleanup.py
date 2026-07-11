from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AffiliateImportBatch, AffiliateProduct, DailyLinkEntry
from app.services.daily_link_catalog import today_local


def cleanup_expired_daily_links(
    retention_days: int = 4,
    reference_date: date | None = None,
    preview: bool = False,
) -> dict:
    settings = get_settings()
    errors: list[str] = []
    retention = max(1, int(retention_days or 4))
    today = reference_date or today_local()
    cutoff = today - timedelta(days=retention - 1)
    cutoff_text = cutoff.isoformat()

    with SessionLocal() as db:
        try:
            expired_entry_ids = list(
                db.scalars(select(DailyLinkEntry.id).where(DailyLinkEntry.import_date < cutoff_text))
            )
            expired_batch_ids = list(
                db.scalars(select(AffiliateImportBatch.id).where(AffiliateImportBatch.import_date < cutoff_text))
            )
            orphan_product_ids = list(
                db.scalars(
                    select(AffiliateProduct.id)
                    .outerjoin(DailyLinkEntry, DailyLinkEntry.product_id == AffiliateProduct.id)
                    .group_by(AffiliateProduct.id)
                    .having(func.count(DailyLinkEntry.id) == 0)
                )
            )

            result = {
                "cutoff_date": cutoff_text,
                "entries_deleted": len(expired_entry_ids),
                "batches_deleted": len(expired_batch_ids),
                "orphan_products_deleted": len(orphan_product_ids) if settings.daily_link_delete_orphan_products else 0,
                "errors": errors,
                "preview": preview,
            }

            if preview:
                return result

            if expired_entry_ids:
                db.execute(delete(DailyLinkEntry).where(DailyLinkEntry.id.in_(expired_entry_ids)))
            if expired_batch_ids:
                db.execute(delete(AffiliateImportBatch).where(AffiliateImportBatch.id.in_(expired_batch_ids)))
            if settings.daily_link_delete_orphan_products and orphan_product_ids:
                db.execute(delete(AffiliateProduct).where(AffiliateProduct.id.in_(orphan_product_ids)))
            db.commit()

            if settings.daily_link_delete_orphan_products:
                later_orphans = list(
                    db.scalars(
                        select(AffiliateProduct.id)
                        .outerjoin(DailyLinkEntry, DailyLinkEntry.product_id == AffiliateProduct.id)
                        .group_by(AffiliateProduct.id)
                        .having(func.count(DailyLinkEntry.id) == 0)
                    )
                )
                if later_orphans:
                    db.execute(delete(AffiliateProduct).where(AffiliateProduct.id.in_(later_orphans)))
                    db.commit()
                    result["orphan_products_deleted"] += len(later_orphans)
            return result
        except Exception as exc:
            db.rollback()
            errors.append(str(exc))
            return {
                "cutoff_date": cutoff_text,
                "entries_deleted": 0,
                "batches_deleted": 0,
                "orphan_products_deleted": 0,
                "errors": errors,
                "preview": preview,
            }
