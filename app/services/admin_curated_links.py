from __future__ import annotations

import hashlib
import csv
import re
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AdminAffiliateLink, AdminLinkBatch, PrivateLinkRequest
from app.services.affiliate_link_type_classifier import classify_affiliate_link_type, link_type_name, valid_link_type_ids
from app.services.product_category_classifier import category_label, classify_product_category, valid_category_ids

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class AdminCuratedLinkSource:
    source_name = "telegram_admin_message"


@dataclass
class IntakeResult:
    added: int
    duplicates: int
    ignored: int
    batch: AdminLinkBatch | None


@dataclass
class CsvImportResult:
    total_rows: int
    added: int
    duplicates: int
    ignored: int
    price_updates: int
    errors: list[str]
    type_counts: dict[str, int]
    category_counts: dict[str, int]
    batches: list[AdminLinkBatch]


def now_utc() -> datetime:
    return datetime.utcnow()


def admin_ids() -> set[int]:
    ids = set()
    for raw in get_settings().telegram_admin_user_ids.split(","):
        raw = raw.strip()
        if raw.isdigit():
            ids.add(int(raw))
    return ids


def is_admin(user_id: int | None) -> bool:
    return bool(user_id and user_id in admin_ids())


def configured_group_id() -> str:
    raw = get_settings().telegram_community_group_id.strip()
    return raw


def is_configured_group(chat_id: int | str | None) -> bool:
    configured = configured_group_id()
    return bool(configured and chat_id is not None and str(chat_id) == configured)


def expire_active_batches(db: Session) -> list[AdminLinkBatch]:
    timeout = timedelta(minutes=get_settings().link_intake_batch_timeout_minutes)
    cutoff = now_utc() - timeout
    expired = list(
        db.scalars(
            select(AdminLinkBatch).where(AdminLinkBatch.status == "active", AdminLinkBatch.created_at < cutoff)
        )
    )
    for batch in expired:
        batch.status = "expired"
        batch.closed_at = now_utc()
    if expired:
        db.commit()
    return expired


def start_batch(db: Session, admin_user_id: int, group_chat_id: int | str, link_type_id: str, category_id: str) -> AdminLinkBatch:
    if link_type_id not in valid_link_type_ids():
        raise ValueError("invalid link type")
    if category_id not in valid_category_ids():
        raise ValueError("invalid category")
    expire_active_batches(db)
    existing = active_batch_for_admin(db, admin_user_id, group_chat_id, expire=False)
    if existing:
        existing.status = "cancelled"
        existing.closed_at = now_utc()
    batch = AdminLinkBatch(
        admin_user_id=admin_user_id,
        group_chat_id=str(group_chat_id),
        link_type_id=link_type_id,
        category_id=category_id,
        status="active",
        link_count=0,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


def active_batch_for_admin(db: Session, admin_user_id: int, group_chat_id: int | str, expire: bool = True) -> AdminLinkBatch | None:
    if expire:
        expire_active_batches(db)
    return db.scalar(
        select(AdminLinkBatch)
        .where(
            AdminLinkBatch.admin_user_id == admin_user_id,
            AdminLinkBatch.group_chat_id == str(group_chat_id),
            AdminLinkBatch.status == "active",
        )
        .order_by(AdminLinkBatch.id.desc())
        .limit(1)
    )


def ingest_admin_message(db: Session, admin_user_id: int, group_chat_id: int | str, text: str) -> IntakeResult:
    batch = active_batch_for_admin(db, admin_user_id, group_chat_id)
    if not batch:
        return IntakeResult(added=0, duplicates=0, ignored=0, batch=None)
    parsed = parse_link_lines(text, start_index=batch.link_count + 1)
    added = 0
    duplicates = 0
    ignored = 0
    seen_urls = set(
        db.scalars(
            select(AdminAffiliateLink.affiliate_url).where(AdminAffiliateLink.batch_id == batch.id)
        ).all()
    )
    for item in parsed:
        if not item:
            ignored += 1
            continue
        if item["affiliate_url"] in seen_urls:
            duplicates += 1
            continue
        seen_urls.add(item["affiliate_url"])
        content_hash = hashlib.sha256(
            f"{batch.id}|{item['affiliate_url']}".encode("utf-8")
        ).hexdigest()
        link = AdminAffiliateLink(
            batch_id=batch.id,
            admin_user_id=admin_user_id,
            group_chat_id=str(group_chat_id),
            link_type_id=batch.link_type_id,
            category_id=batch.category_id,
            display_name=item["display_name"],
            price=item.get("price") or None,
            affiliate_url=item["affiliate_url"],
            content_hash=content_hash,
            is_active=1,
            expires_at=now_utc() + timedelta(days=get_settings().link_retention_days),
        )
        db.add(link)
        try:
            db.flush()
            added += 1
            batch.link_count += 1
        except IntegrityError:
            db.rollback()
            batch = db.get(AdminLinkBatch, batch.id)
            seen_urls.discard(item["affiliate_url"])
            duplicates += 1
    db.commit()
    return IntakeResult(added=added, duplicates=duplicates, ignored=ignored, batch=batch)


def parse_link_lines(text: str, start_index: int = 1) -> list[dict | None]:
    rows: list[dict | None] = []
    offer_index = start_index
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = URL_RE.search(line)
        if not match:
            rows.append(None)
            continue
        url = match.group(0).strip()
        display_name = ""
        price = ""
        if "|" in line:
            parts = [part.strip() for part in line.split("|")]
            display_name = parts[0] if parts else ""
            if len(parts) >= 3:
                price = parts[1]
        if not display_name:
            before_url = line[: match.start()].strip(" |-:")
            display_name = before_url
        if not display_name:
            display_name = f"Link ưu đãi {offer_index}"
        rows.append({"display_name": display_name[:160], "price": price[:64], "affiliate_url": url})
        offer_index += 1
    return rows


def import_admin_links_csv(
    db: Session,
    csv_path: str | Path,
    admin_user_id: int,
    group_chat_id: int | str,
    forced_link_type_id: str | None = None,
) -> CsvImportResult:
    """Import a mixed Shopee affiliate CSV into channel-ready category batches."""
    path = Path(csv_path)
    batches: dict[tuple[str, str], AdminLinkBatch] = {}
    errors: list[str] = []
    type_counts: dict[str, int] = defaultdict(int)
    category_counts: dict[str, int] = defaultdict(int)
    added = 0
    duplicates = 0
    ignored = 0
    price_updates = 0
    total_rows = 0
    expires_at = now_utc() + timedelta(days=get_settings().link_retention_days)
    seen_in_file: set[tuple[str, str, str]] = set()
    records: list[dict] = []

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV khong co header.")
        for row_number, row in enumerate(reader, start=2):
            total_rows += 1
            row = {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
            product_name = _first_field(row, PRODUCT_NAME_FIELDS)
            affiliate_url = _first_field(row, AFFILIATE_URL_FIELDS)
            if not affiliate_url:
                ignored += 1
                continue
            if not URL_RE.search(affiliate_url):
                ignored += 1
                errors.append(f"Dong {row_number}: khong thay affiliate URL hop le")
                continue
            if not product_name:
                product_name = f"Link ưu đãi {total_rows}"
            price = _first_field(row, PRICE_FIELDS)

            link_type = classify_affiliate_link_type(row, filename=path.name)
            category = classify_product_category(row, product_name=product_name, shop_name=_first_field(row, SHOP_NAME_FIELDS))
            link_type_id = forced_link_type_id if forced_link_type_id in valid_link_type_ids() else link_type["link_type_id"]
            category_id = category["category_id"]
            url = URL_RE.search(affiliate_url).group(0).strip()
            seen_key = (link_type_id, category_id, url)
            if seen_key in seen_in_file:
                duplicates += 1
                continue
            seen_in_file.add(seen_key)
            records.append(
                {
                    "link_type_id": link_type_id,
                    "category_id": category_id,
                    "display_name": product_name[:160],
                    "price": price[:64] if price else "",
                    "affiliate_url": url,
                }
            )

    if not records:
        return CsvImportResult(
            total_rows=total_rows,
            added=0,
            duplicates=duplicates,
            ignored=ignored,
            price_updates=0,
            errors=errors[:8],
            type_counts={},
            category_counts={},
            batches=[],
        )

    existing_by_key: dict[tuple[str, str, str], AdminAffiliateLink] = {}
    urls = sorted({record["affiliate_url"] for record in records})
    for url_chunk in _chunks(urls, 500):
        existing_rows = db.scalars(
            select(AdminAffiliateLink)
            .where(
                AdminAffiliateLink.affiliate_url.in_(url_chunk),
                AdminAffiliateLink.is_active == 1,
                AdminAffiliateLink.expires_at >= now_utc(),
            )
            .order_by(AdminAffiliateLink.created_at.desc(), AdminAffiliateLink.id.desc())
        ).all()
        for link in existing_rows:
            existing_by_key.setdefault((link.link_type_id, link.category_id, link.affiliate_url), link)

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in records:
        key = (record["link_type_id"], record["category_id"], record["affiliate_url"])
        existing = existing_by_key.get(key)
        if existing:
            existing.display_name = record["display_name"] or existing.display_name
            old_price = existing.price or ""
            existing.price = record["price"] or existing.price
            if record["price"] and record["price"] != old_price:
                price_updates += 1
            existing.expires_at = expires_at
            duplicates += 1
            continue
        grouped[(record["link_type_id"], record["category_id"])].append(record)

    for (link_type_id, category_id), group_records in grouped.items():
        batch = AdminLinkBatch(
            admin_user_id=admin_user_id,
            group_chat_id=str(group_chat_id),
            link_type_id=link_type_id,
            category_id=category_id,
            status="completed",
            closed_at=now_utc(),
            link_count=len(group_records),
        )
        db.add(batch)
        batches[(link_type_id, category_id)] = batch
    if batches:
        db.flush()

    links_to_add: list[AdminAffiliateLink] = []
    for (link_type_id, category_id), group_records in grouped.items():
        batch = batches[(link_type_id, category_id)]
        for record in group_records:
            url = record["affiliate_url"]
            links_to_add.append(
                AdminAffiliateLink(
                    batch_id=batch.id,
                    admin_user_id=admin_user_id,
                    group_chat_id=str(group_chat_id),
                    link_type_id=link_type_id,
                    category_id=category_id,
                    display_name=record["display_name"],
                    price=record["price"] or None,
                    affiliate_url=url,
                    content_hash=hashlib.sha256(f"{batch.id}|{url}".encode("utf-8")).hexdigest(),
                    is_active=1,
                    expires_at=expires_at,
                )
            )
            added += 1
            type_counts[link_type_id] += 1
            category_counts[category_id] += 1
    if links_to_add:
        db.add_all(links_to_add)
        db.flush()
    db.commit()
    return CsvImportResult(
        total_rows=total_rows,
        added=added,
        duplicates=duplicates,
        ignored=ignored,
        price_updates=price_updates,
        errors=errors[:8],
        type_counts=dict(type_counts),
        category_counts=dict(category_counts),
        batches=list(batches.values()),
    )


PRODUCT_NAME_FIELDS = (
    "Tên sản phẩm",
    "Tên ưu đãi",
    "Product Name",
    "Name",
    "name",
    "title",
)
AFFILIATE_URL_FIELDS = (
    "Link ưu đãi",
    "Affiliate Link",
    "Affiliate URL",
    "Link tiếp thị",
    "Tracking Link",
    "url",
)
PRICE_FIELDS = (
    "Giá",
    "Gia",
    "Price",
    "price",
    "Giá sau giảm",
    "Gia sau giam",
    "Giá bán",
    "Gia ban",
    "Giá sản phẩm",
    "Gia san pham",
)
SHOP_NAME_FIELDS = (
    "Tên cửa hàng",
    "Shop name",
    "Shop",
    "seller",
)


def _first_field(row: dict, fields: tuple[str, ...]) -> str:
    normalized = {str(key).strip().lower(): str(value or "").strip() for key, value in row.items()}
    for field in fields:
        value = normalized.get(field.lower())
        if value:
            return value
    return ""


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _link_display_name(link: AdminAffiliateLink) -> str:
    return f"{link.display_name} - {link.price}" if link.price else link.display_name


def close_batch(db: Session, admin_user_id: int, group_chat_id: int | str, status: str = "completed") -> AdminLinkBatch | None:
    batch = active_batch_for_admin(db, admin_user_id, group_chat_id)
    if not batch:
        return None
    batch.status = status
    batch.closed_at = now_utc()
    db.commit()
    db.refresh(batch)
    return batch


def set_batch_guide_message(db: Session, batch_id: int, message_id: int | str) -> None:
    batch = db.get(AdminLinkBatch, batch_id)
    if batch:
        batch.guide_message_id = str(message_id)
        db.commit()


def cleanup_expired_admin_links(db: Session, preview: bool = False) -> dict:
    cutoff = now_utc() - timedelta(days=max(1, get_settings().link_retention_days) - 1)
    links = list(
        db.scalars(
            select(AdminAffiliateLink).where(
                AdminAffiliateLink.is_active == 1,
                AdminAffiliateLink.created_at < cutoff,
            )
        )
    )
    expired_requests = list(
        db.scalars(
            select(PrivateLinkRequest).where(
                PrivateLinkRequest.status == "pending",
                PrivateLinkRequest.expires_at < now_utc(),
            )
        )
    )
    if not preview:
        for link in links:
            link.is_active = 0
        for request in expired_requests:
            request.status = "expired"
        db.commit()
    return {"links_deactivated": len(links), "requests_expired": len(expired_requests), "cutoff": cutoff.isoformat()}


def categories_for_type(db: Session, link_type_id: str) -> list[dict]:
    if link_type_id not in valid_link_type_ids():
        return []
    rows = db.execute(
        select(AdminAffiliateLink.category_id, func.count(func.distinct(AdminAffiliateLink.affiliate_url)))
        .where(
            AdminAffiliateLink.link_type_id == link_type_id,
            AdminAffiliateLink.is_active == 1,
            AdminAffiliateLink.expires_at >= now_utc(),
        )
        .group_by(AdminAffiliateLink.category_id)
        .order_by(func.count(func.distinct(AdminAffiliateLink.affiliate_url)).desc())
    ).all()
    return [{"category_id": str(row.category_id), "label": category_label(str(row.category_id)), "count": int(row[1])} for row in rows]


def active_type_counts(db: Session) -> list[dict]:
    rows = db.execute(
        select(AdminAffiliateLink.link_type_id, func.count(func.distinct(AdminAffiliateLink.affiliate_url)))
        .where(
            AdminAffiliateLink.is_active == 1,
            AdminAffiliateLink.expires_at >= now_utc(),
        )
        .group_by(AdminAffiliateLink.link_type_id)
        .order_by(func.count(func.distinct(AdminAffiliateLink.affiliate_url)).desc())
    ).all()
    return [{"link_type_id": str(row[0]), "label": link_type_name(str(row[0])), "count": int(row[1])} for row in rows]


def get_links_for_delivery(
    db: Session,
    link_type_id: str,
    category_id: str,
    limit: int | None = None,
    hard_cap: int | None = None,
) -> list[AdminAffiliateLink]:
    allow_all_categories = link_type_id == "exclusive_offer" and category_id == "all"
    if link_type_id not in valid_link_type_ids() or (not allow_all_categories and category_id not in valid_category_ids()):
        return []
    configured = max(1, get_settings().max_links_per_category)
    requested = max(1, limit if limit is not None else configured)
    cap = max(1, hard_cap if hard_cap is not None else configured)
    max_links = min(requested, cap)
    conditions = [
        AdminAffiliateLink.link_type_id == link_type_id,
        AdminAffiliateLink.is_active == 1,
        AdminAffiliateLink.expires_at >= now_utc(),
    ]
    if not allow_all_categories:
        conditions.append(AdminAffiliateLink.category_id == category_id)
    rows = list(
        db.scalars(
            select(AdminAffiliateLink)
            .where(*conditions)
            .order_by(AdminAffiliateLink.created_at.desc(), AdminAffiliateLink.batch_id.desc(), AdminAffiliateLink.id.desc())
            .limit(max_links * 4)
        )
    )
    unique: list[AdminAffiliateLink] = []
    seen: set[str] = set()
    for link in rows:
        key = link.affiliate_url.strip()
        if key in seen:
            continue
        seen.add(key)
        unique.append(link)
        if len(unique) >= max_links:
            break
    return unique


def create_private_request(db: Session, telegram_user_id: int, group_chat_id: int | str, link_type_id: str, category_id: str) -> PrivateLinkRequest:
    request = PrivateLinkRequest(
        request_token=secrets.token_urlsafe(18),
        telegram_user_id=telegram_user_id,
        group_chat_id=str(group_chat_id or ""),
        link_type_id=link_type_id,
        category_id=category_id,
        status="pending",
        expires_at=now_utc() + timedelta(minutes=30),
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def get_pending_request(db: Session, token: str, telegram_user_id: int) -> PrivateLinkRequest | None:
    request = db.scalar(
        select(PrivateLinkRequest).where(
            PrivateLinkRequest.request_token == token,
            PrivateLinkRequest.telegram_user_id == telegram_user_id,
            PrivateLinkRequest.status == "pending",
        )
    )
    if not request:
        return None
    if request.expires_at < now_utc():
        request.status = "expired"
        db.commit()
        return None
    return request


def complete_private_request(db: Session, request: PrivateLinkRequest, status: str = "completed") -> None:
    request.status = status
    request.completed_at = now_utc()
    db.commit()


def user_request_allowed(db: Session, telegram_user_id: int) -> tuple[bool, str]:
    settings = get_settings()
    since_cooldown = now_utc() - timedelta(seconds=settings.private_link_request_cooldown_seconds)
    recent = db.scalar(
        select(func.count(PrivateLinkRequest.id)).where(
            PrivateLinkRequest.telegram_user_id == telegram_user_id,
            PrivateLinkRequest.created_at >= since_cooldown,
        )
    ) or 0
    if recent:
        return False, "cooldown"
    since_hour = now_utc() - timedelta(hours=1)
    hourly = db.scalar(
        select(func.count(PrivateLinkRequest.id)).where(
            PrivateLinkRequest.telegram_user_id == telegram_user_id,
            PrivateLinkRequest.created_at >= since_hour,
        )
    ) or 0
    if hourly >= settings.private_link_max_requests_per_user_per_hour:
        return False, "hourly"
    return True, ""


def link_stats(db: Session) -> dict:
    total = db.scalar(
        select(func.count(func.distinct(AdminAffiliateLink.affiliate_url))).where(
            AdminAffiliateLink.is_active == 1,
            AdminAffiliateLink.expires_at >= now_utc(),
        )
    ) or 0
    by_type = db.execute(
        select(AdminAffiliateLink.link_type_id, func.count(func.distinct(AdminAffiliateLink.affiliate_url)))
        .where(AdminAffiliateLink.is_active == 1, AdminAffiliateLink.expires_at >= now_utc())
        .group_by(AdminAffiliateLink.link_type_id)
    ).all()
    by_category = db.execute(
        select(AdminAffiliateLink.category_id, func.count(func.distinct(AdminAffiliateLink.affiliate_url)))
        .where(AdminAffiliateLink.is_active == 1, AdminAffiliateLink.expires_at >= now_utc())
        .group_by(AdminAffiliateLink.category_id)
    ).all()
    latest_batch = db.scalar(select(AdminLinkBatch).order_by(AdminLinkBatch.id.desc()).limit(1))
    soon = now_utc() + timedelta(days=1)
    expiring = db.scalar(
        select(func.count(AdminAffiliateLink.id)).where(AdminAffiliateLink.is_active == 1, AdminAffiliateLink.expires_at <= soon)
    ) or 0
    return {
        "total": int(total),
        "by_type": [(link_type_name(str(row[0])), int(row[1])) for row in by_type],
        "by_category": [(category_label(str(row[0])), int(row[1])) for row in by_category],
        "latest_batch": latest_batch,
        "expiring": int(expiring),
    }


def build_private_link_messages(link_type_id: str, category_id: str, links: list[AdminAffiliateLink]) -> list[str]:
    category_name = "Tổng hợp" if category_id == "all" else category_label(category_id)
    header = [
        f"{link_type_name(link_type_id)}",
        f"Danh mục: {category_name}",
        "",
    ]
    footer = [
        "",
        "Danh sách được cập nhật trực tiếp bởi quản trị viên.",
        get_settings().telegram_daily_link_disclosure,
    ]
    lines = header[:]
    for index, link in enumerate(links[:25], start=1):
        candidate = [f"{index}. {_link_display_name(link)}", link.affiliate_url, ""]
        if len("\n".join(lines + candidate + footer)) > 3500 and len(lines) > len(header):
            lines.extend(footer)
            yield_text = "\n".join(lines).strip()
            lines = header[:] + candidate
            yield yield_text
        else:
            lines.extend(candidate)
    lines.extend(footer)
    yield "\n".join(lines).strip()
