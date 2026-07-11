from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AffiliateImportBatch, AffiliateProduct, DailyLinkEntry

CATEGORIES_PATH = Path(__file__).resolve().parents[2] / "data" / "product_categories.json"


@dataclass
class DailyImportResult:
    import_date: str
    total_rows: int
    new_products: int
    new_entries: int
    duplicate_count: int
    error_count: int
    errors: list[str]
    cleanup: dict | None = None


def today_local() -> date:
    return datetime.now(ZoneInfo(get_settings().daily_link_timezone)).date()


def parse_import_date(value: str | None = None) -> str:
    if not value:
        return today_local().isoformat()
    raw = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    compact = re.search(r"(20\d{2})(\d{2})(\d{2})(?:\d{6})?", raw)
    if compact:
        try:
            return date(int(compact.group(1)), int(compact.group(2)), int(compact.group(3))).isoformat()
        except ValueError:
            pass
    raise ValueError("date must be YYYY-MM-DD, DD/MM/YYYY, or contain YYYYMMDD/ YYYYMMDDHHMMSS")


def infer_import_date_from_path(csv_path: str | Path) -> str | None:
    match = re.search(r"(20\d{2})(\d{2})(\d{2})(?:\d{6})?", Path(csv_path).name)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def display_date(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d").date()
    return parsed.strftime("%d/%m/%Y")


def short_display_date(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d").date()
    return parsed.strftime("%d/%m")


def load_categories() -> list[dict]:
    if not CATEGORIES_PATH.exists():
        return [{"id": "other", "label": "Khac", "keywords": []}]
    return json.loads(CATEGORIES_PATH.read_text(encoding="utf-8"))


def category_label(category_id: str) -> str:
    for category in load_categories():
        if category["id"] == category_id:
            return category.get("label") or category_id
    return category_id


def classify_product(name: str, shop_name: str = "") -> str:
    haystack = _normalize(f"{name} {shop_name}")
    best_id = "other"
    best_score = 0
    for category in load_categories():
        score = 0
        for keyword in category.get("keywords", []):
            normalized = _normalize(keyword)
            if normalized and normalized in haystack:
                score += 2 if " " in normalized else 1
        if score > best_score:
            best_id = category["id"]
            best_score = score
    return best_id


def import_daily_csv(db: Session, csv_path: str | Path, import_date: str | None = None) -> DailyImportResult:
    target_date = parse_import_date(import_date) if import_date else (infer_import_date_from_path(csv_path) or parse_import_date(None))
    path = Path(csv_path)
    total_rows = 0
    new_products = 0
    new_entries = 0
    duplicate_count = 0
    errors: list[str] = []

    batch = AffiliateImportBatch(
        batch_name=path.name,
        import_date=target_date,
        source=str(path),
    )
    db.add(batch)
    db.flush()

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for index, row in enumerate(reader, start=2):
            total_rows += 1
            try:
                normalized = _normalized_row(row)
                item = _row_to_product(normalized)
                if not item["product_name"] or not item["affiliate_url"]:
                    duplicate_count += 1
                    errors.append(f"Row {index}: missing product name or affiliate url")
                    continue
                product, created = upsert_product(db, item)
                if created:
                    new_products += 1
                existing_entry = db.scalar(
                    select(DailyLinkEntry).where(
                        DailyLinkEntry.product_id == product.id,
                        DailyLinkEntry.import_date == target_date,
                    )
                )
                if existing_entry:
                    duplicate_count += 1
                    continue
                db.add(DailyLinkEntry(product_id=product.id, import_date=target_date, batch_id=batch.id))
                db.flush()
                new_entries += 1
            except Exception as exc:
                duplicate_count += 1
                errors.append(f"Row {index}: {exc}")

    batch.total_rows = total_rows
    batch.imported_count = new_entries
    batch.duplicate_count = duplicate_count
    batch.error_count = len(errors)
    db.commit()

    cleanup_result = None
    if get_settings().enable_daily_link_auto_cleanup:
        from app.services.daily_link_cleanup import cleanup_expired_daily_links

        cleanup_result = cleanup_expired_daily_links(
            retention_days=get_settings().daily_link_retention_days,
            reference_date=datetime.strptime(target_date, "%Y-%m-%d").date(),
        )
    return DailyImportResult(
        import_date=target_date,
        total_rows=total_rows,
        new_products=new_products,
        new_entries=new_entries,
        duplicate_count=duplicate_count,
        error_count=len(errors),
        errors=errors,
        cleanup=cleanup_result,
    )


def add_daily_product(db: Session, text: str, import_date: str | None = None) -> DailyImportResult:
    target_date = parse_import_date(import_date)
    parts = [part.strip() for part in text.split("|")]
    if len(parts) < 2:
        raise ValueError("Use: /adddailylink <url> | <name> | <price>")
    first, second = parts[0], parts[1]
    affiliate_url = first if first.startswith("http") else second
    product_name = second if first.startswith("http") else first
    price = parts[2] if len(parts) >= 3 else ""
    item = {
        "product_name": product_name,
        "affiliate_url": affiliate_url,
        "product_url": "",
        "price": price,
        "shop_name": "",
    }
    product, created = upsert_product(db, item)
    duplicate = 0
    new_entries = 0
    db.add(DailyLinkEntry(product_id=product.id, import_date=target_date))
    try:
        db.commit()
        new_entries = 1
    except IntegrityError:
        db.rollback()
        duplicate = 1
    return DailyImportResult(
        import_date=target_date,
        total_rows=1,
        new_products=1 if created else 0,
        new_entries=new_entries,
        duplicate_count=duplicate,
        error_count=0,
        errors=[],
    )


def upsert_product(db: Session, item: dict[str, str]) -> tuple[AffiliateProduct, bool]:
    affiliate_url = item["affiliate_url"].strip()
    product = db.scalar(select(AffiliateProduct).where(AffiliateProduct.affiliate_url == affiliate_url))
    if product:
        changed = False
        for field in ("product_name", "price", "shop_name", "product_url"):
            current = getattr(product, field) or ""
            incoming = (item.get(field) or "").strip()
            if incoming and not current:
                setattr(product, field, incoming)
                changed = True
        if changed:
            product.updated_at = datetime.now()
            db.flush()
        return product, False

    product = AffiliateProduct(
        product_name=item["product_name"].strip(),
        affiliate_url=affiliate_url,
        product_url=(item.get("product_url") or "").strip() or None,
        price=(item.get("price") or "").strip() or None,
        shop_name=(item.get("shop_name") or "").strip() or None,
        category_id=classify_product(item["product_name"], item.get("shop_name") or ""),
        is_active=1,
    )
    db.add(product)
    db.flush()
    return product, True


def recent_dates(db: Session, limit: int = 4) -> list[str]:
    rows = db.execute(
        select(DailyLinkEntry.import_date)
        .group_by(DailyLinkEntry.import_date)
        .order_by(DailyLinkEntry.import_date.desc())
        .limit(limit)
    ).all()
    return [str(row.import_date) for row in rows]


def category_counts(db: Session, import_date: str) -> list[dict]:
    rows = db.execute(
        select(AffiliateProduct.category_id, func.count(DailyLinkEntry.id))
        .join(AffiliateProduct, AffiliateProduct.id == DailyLinkEntry.product_id)
        .where(DailyLinkEntry.import_date == import_date, AffiliateProduct.is_active == 1)
        .group_by(AffiliateProduct.category_id)
        .order_by(func.count(DailyLinkEntry.id).desc())
    ).all()
    return [
        {"category_id": str(category_id), "label": category_label(str(category_id)), "count": int(count)}
        for category_id, count in rows
    ]


def products_for_category(db: Session, import_date: str, category_id: str, limit: int | None = None) -> list[AffiliateProduct]:
    settings = get_settings()
    max_items = limit or settings.daily_max_products_per_category
    return list(
        db.scalars(
            select(AffiliateProduct)
            .join(DailyLinkEntry, DailyLinkEntry.product_id == AffiliateProduct.id)
            .where(
                DailyLinkEntry.import_date == import_date,
                AffiliateProduct.category_id == category_id,
                AffiliateProduct.is_active == 1,
            )
            .order_by(AffiliateProduct.id.desc())
            .limit(max_items)
        )
    )


def daily_stats(db: Session, import_date: str | None = None) -> dict:
    target_date = parse_import_date(import_date)
    total = db.scalar(select(func.count(DailyLinkEntry.id)).where(DailyLinkEntry.import_date == target_date)) or 0
    active = db.scalar(
        select(func.count(DailyLinkEntry.id))
        .join(AffiliateProduct, AffiliateProduct.id == DailyLinkEntry.product_id)
        .where(DailyLinkEntry.import_date == target_date, AffiliateProduct.is_active == 1)
    ) or 0
    return {"import_date": target_date, "total_entries": int(total), "active_entries": int(active), "categories": category_counts(db, target_date)}


def set_daily_product_active(db: Session, product_id: int, active: bool) -> bool:
    product = db.get(AffiliateProduct, product_id)
    if not product:
        return False
    product.is_active = 1 if active else 0
    db.commit()
    return True


def recategorize_product(db: Session, product_id: int, category_id: str) -> bool:
    valid = {category["id"] for category in load_categories()}
    if category_id not in valid:
        return False
    product = db.get(AffiliateProduct, product_id)
    if not product:
        return False
    product.category_id = category_id
    db.commit()
    return True


def build_category_message(import_date: str, category_id: str, products: list[AffiliateProduct]) -> list[str]:
    settings = get_settings()
    per_message = max(1, min(10, settings.daily_links_per_message))
    chunks = [products[index : index + per_message] for index in range(0, len(products), per_message)]
    messages = []
    for chunk_index, chunk in enumerate(chunks, start=1):
        lines = [
            f"{category_label(category_id)}",
            f"Cap nhat ngay {display_date(import_date)}",
            "",
        ]
        offset = (chunk_index - 1) * per_message
        for index, product in enumerate(chunk, start=1 + offset):
            lines.append(f"{index}. {product.product_name}")
            if product.price:
                lines.append(f"   Gia: {product.price}")
            lines.append(f"   Link: {product.affiliate_url}")
            lines.append("")
        lines.append(settings.telegram_daily_link_disclosure)
        messages.append("\n".join(lines).strip())
    return messages


def _row_to_product(row: dict[str, str]) -> dict[str, str]:
    return {
        "product_name": _first_value(row, "ten san pham", "product name", "name"),
        "affiliate_url": _first_value(row, "link uu dai", "affiliate link", "affiliate url", "url"),
        "product_url": _first_value(row, "link san pham", "product link", "product url"),
        "price": _first_value(row, "gia", "price"),
        "shop_name": _first_value(row, "ten cua hang", "shop name"),
    }


def _normalized_row(row: dict[str, str]) -> dict[str, str]:
    return {_normalize(key): (value or "").strip() for key, value in row.items()}


def _first_value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(_normalize(key))
        if value:
            return value.strip()
    return ""


def _normalize(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()
