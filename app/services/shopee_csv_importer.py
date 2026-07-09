import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from agents.threads_shopee_agent import generate_threads_shopee_draft
from app.schemas import ThreadsDraftRequest
from app.services.threads_repository import create_group_post, create_post, get_post_by_affiliate_url


@dataclass
class ImportResult:
    created: int
    skipped: int
    errors: list[str]


@dataclass
class ScanResult:
    product_rows: list[dict[str, str]]
    campaign_rows: list[dict[str, str]]
    skipped: int
    errors: list[str]

    @property
    def new_links(self) -> int:
        return len(self.product_rows) + len(self.campaign_rows)


def _clean_campaign_name(value: str) -> str:
    text = re.sub(r"^KOL\s*-\s*", "", value, flags=re.IGNORECASE).strip()
    text = re.sub(r"High commision for Social KOL[_\s-]*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\d{2}\.\d{2}\.\d{4}\s*-\s*\d{2}\.\d{2}\.\d{4}", "", text).strip(" -_")
    text = text.replace("_", " ")
    return re.sub(r"\s+", " ", text).strip() or value.strip()


def _normalize_key(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _normalized_row(row: dict[str, str]) -> dict[str, str]:
    return {_normalize_key(key): value for key, value in row.items()}


def _first_value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value:
            return value.strip()
    return ""


def _chunk(items: list[dict[str, str]], size: int) -> list[list[dict[str, str]]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _product_summary(items: list[dict[str, str]]) -> str:
    lines = []
    for index, item in enumerate(items, start=1):
        meta = []
        if item.get("price"):
            meta.append(f"gia {item['price']}")
        if item.get("shop_name"):
            meta.append(f"shop {item['shop_name']}")
        suffix = f" ({', '.join(meta)})" if meta else ""
        lines.append(f"{index}. {item['product_name']}{suffix}")
    return "\n".join(lines)


def scan_shopee_csv(db: Session, csv_path: str | Path) -> ScanResult:
    path = Path(csv_path)
    skipped = 0
    errors: list[str] = []
    product_rows: list[dict[str, str]] = []
    campaign_rows: list[dict[str, str]] = []

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        for index, row in enumerate(reader, start=2):
            nrow = _normalized_row(row)
            product_name = _first_value(nrow, "ten san pham", "product name")
            campaign_name = _first_value(nrow, "ten uu dai", "campaign name")
            affiliate_url = _first_value(nrow, "link uu dai", "affiliate link")
            product_url = _first_value(nrow, "link san pham", "product link")
            price = _first_value(nrow, "gia", "price")
            shop_name = _first_value(nrow, "ten cua hang", "shop name")
            commission_rate = _first_value(nrow, "ti le hoa hong", "commission rate")
            raw_name = product_name or campaign_name

            if not raw_name or not affiliate_url:
                skipped += 1
                errors.append(f"Row {index}: missing name or affiliate link")
                continue

            if get_post_by_affiliate_url(db, affiliate_url):
                skipped += 1
                continue

            if product_name:
                product_rows.append(
                    {
                        "product_name": product_name,
                        "affiliate_url": affiliate_url,
                        "product_url": product_url,
                        "price": price,
                        "shop_name": shop_name,
                        "commission_rate": commission_rate,
                    }
                )
            else:
                campaign_rows.append(
                    {
                        "name": _clean_campaign_name(campaign_name),
                        "affiliate_url": affiliate_url,
                    }
                )

    return ScanResult(product_rows=product_rows, campaign_rows=campaign_rows, skipped=skipped, errors=errors)


def import_shopee_csv(
    db: Session,
    csv_path: str | Path,
    limit: int | None = None,
    group_size: int = 5,
) -> ImportResult:
    group_size = max(1, min(6, group_size))
    created = 0
    scan = scan_shopee_csv(db, csv_path)

    for group in _chunk(scan.product_rows, group_size):
        if limit is not None and created >= limit:
            break

        summary = _product_summary(group)
        keyword = f"list {len(group)} mon Shopee dang xem"
        draft = generate_threads_shopee_draft(
            db,
            ThreadsDraftRequest(
                keyword=keyword,
                product_name=summary,
                style="viral product-native",
            ),
        )
        create_group_post(
            db,
            keyword=keyword,
            product_name=summary,
            draft=draft,
            links=group,
            status="draft",
        )
        created += 1

    for row in scan.campaign_rows:
        if limit is not None and created >= limit:
            break

        draft = generate_threads_shopee_draft(
            db,
            ThreadsDraftRequest(
                keyword=row["name"],
                product_name=row["name"],
                affiliate_url=row["affiliate_url"],
                style="viral product-native",
            ),
        )
        create_post(
            db,
            keyword=row["name"],
            product_name=row["name"],
            affiliate_url=row["affiliate_url"],
            draft=draft,
            status="draft",
        )
        created += 1

    return ImportResult(created=created, skipped=scan.skipped, errors=scan.errors)
