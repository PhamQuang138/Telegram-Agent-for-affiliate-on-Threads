from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AffiliateProduct, DailyLinkEntry
from app.services.affiliate_link_type_classifier import link_type_name, valid_link_type_ids
from app.services.product_category_classifier import category_label, valid_category_ids


def normalize_date_value(import_date: str | date) -> str:
    return import_date.isoformat() if isinstance(import_date, date) else str(import_date)


def get_link_types_for_date(db: Session, import_date: str | date) -> list[dict]:
    target_date = normalize_date_value(import_date)
    rows = db.execute(
        select(AffiliateProduct.link_type_id, func.count(DailyLinkEntry.id))
        .join(AffiliateProduct, AffiliateProduct.id == DailyLinkEntry.product_id)
        .where(DailyLinkEntry.import_date == target_date, AffiliateProduct.is_active == 1)
        .group_by(AffiliateProduct.link_type_id)
        .order_by(func.count(DailyLinkEntry.id).desc())
    ).all()
    return [
        {
            "link_type_id": str(link_type_id),
            "link_type_name": link_type_name(str(link_type_id)),
            "count": int(count),
        }
        for link_type_id, count in rows
    ]


def get_categories_for_date_and_type(db: Session, import_date: str | date, link_type_id: str) -> list[dict]:
    if link_type_id not in valid_link_type_ids():
        return []
    target_date = normalize_date_value(import_date)
    rows = db.execute(
        select(AffiliateProduct.category_id, func.count(DailyLinkEntry.id))
        .join(AffiliateProduct, AffiliateProduct.id == DailyLinkEntry.product_id)
        .where(
            DailyLinkEntry.import_date == target_date,
            AffiliateProduct.link_type_id == link_type_id,
            AffiliateProduct.is_active == 1,
        )
        .group_by(AffiliateProduct.category_id)
        .order_by(func.count(DailyLinkEntry.id).desc())
    ).all()
    return [
        {
            "category_id": str(category_id),
            "label": category_label(str(category_id)),
            "count": int(count),
        }
        for category_id, count in rows
    ]


def get_products_for_date_type_category(
    db: Session,
    import_date: str | date,
    link_type_id: str,
    category_id: str,
    page: int = 1,
    page_size: int = 5,
) -> dict:
    if link_type_id not in valid_link_type_ids() or category_id not in valid_category_ids():
        return {"products": [], "total": 0, "page": max(1, page), "page_size": page_size, "has_next": False}
    target_date = normalize_date_value(import_date)
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 5))
    filters = (
        DailyLinkEntry.import_date == target_date,
        AffiliateProduct.link_type_id == link_type_id,
        AffiliateProduct.category_id == category_id,
        AffiliateProduct.is_active == 1,
    )
    total = db.scalar(
        select(func.count(AffiliateProduct.id))
        .join(DailyLinkEntry, DailyLinkEntry.product_id == AffiliateProduct.id)
        .where(*filters)
    ) or 0
    products = list(
        db.scalars(
            select(AffiliateProduct)
            .join(DailyLinkEntry, DailyLinkEntry.product_id == AffiliateProduct.id)
            .where(*filters)
            .order_by(AffiliateProduct.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "products": products,
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "has_next": page * page_size < int(total),
    }


def update_product_link_type(db: Session, product_id: int, link_type_id: str) -> bool:
    if link_type_id not in valid_link_type_ids():
        return False
    product = db.get(AffiliateProduct, product_id)
    if not product:
        return False
    product.link_type_id = link_type_id
    db.commit()
    return True
