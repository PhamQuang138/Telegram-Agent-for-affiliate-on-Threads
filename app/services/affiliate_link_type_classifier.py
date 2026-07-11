from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from app.config import get_settings

LINK_TYPES_PATH = Path(__file__).resolve().parents[2] / "data" / "affiliate_link_types.json"

TYPE_SOURCE_FIELDS = (
    "loai hoa hong",
    "loai chien dich",
    "nhom chien dich",
    "ten chien dich",
    "campaign type",
    "commission type",
    "loai uu dai",
    "nguon link",
    "danh muc link",
)
CAMPAIGN_NAME_FIELDS = (
    "ten chien dich",
    "ten uu dai",
    "campaign name",
    "offer name",
    "promotion name",
)


def normalize_text(value: str | None) -> str:
    value = (value or "").replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def load_link_types() -> list[dict]:
    return json.loads(LINK_TYPES_PATH.read_text(encoding="utf-8"))


def valid_link_type_ids() -> set[str]:
    return {item["id"] for item in load_link_types()}


def get_default_link_type_id() -> str:
    configured = getattr(get_settings(), "daily_default_link_type", "shopee_commission")
    return configured if configured in valid_link_type_ids() else "shopee_commission"


def link_type_by_id(link_type_id: str | None) -> dict | None:
    for item in load_link_types():
        if item["id"] == link_type_id:
            return item
    return None


def link_type_name(link_type_id: str | None) -> str:
    item = link_type_by_id(link_type_id)
    return item["name"] if item else (link_type_id or "unknown")


def short_code_for_type(link_type_id: str) -> str:
    item = link_type_by_id(link_type_id)
    if item and item.get("short_code"):
        return item["short_code"]
    return link_type_id[:2]


def link_type_id_from_code(code: str) -> str | None:
    for item in load_link_types():
        if item.get("short_code") == code or item["id"] == code:
            return item["id"]
    return None


def classify_affiliate_link_type(
    row: dict,
    filename: str | None = None,
    sheet_name: str | None = None,
    default_link_type_id: str | None = None,
) -> dict:
    normalized_row = {normalize_text(str(key)): str(value or "").strip() for key, value in row.items()}
    default_id = default_link_type_id if default_link_type_id in valid_link_type_ids() else get_default_link_type_id()

    candidates: list[tuple[str, str, str, int]] = []
    for field in TYPE_SOURCE_FIELDS:
        value = normalized_row.get(field, "")
        if value:
            candidates.append((value, field, "campaign type column", 95))

    for field in CAMPAIGN_NAME_FIELDS:
        value = normalized_row.get(field, "")
        if value:
            candidates.append((value, field, "campaign/offer name", 80))

    if filename:
        candidates.append((Path(filename).name, "filename", "filename", 65))
    if sheet_name:
        candidates.append((sheet_name, "sheet_name", "sheet name", 60))
    if default_id:
        default = link_type_by_id(default_id)
        if default:
            candidates.append((default["name"], "admin_default", "admin selected default", 55))

    for value, source_field, reason, confidence in candidates:
        matched = _match_value(value)
        if matched:
            return {
                "link_type_id": matched["id"],
                "link_type_name": matched["name"],
                "confidence": confidence,
                "matched_value": value,
                "source_field": source_field,
                "reason": reason,
            }

    fallback = link_type_by_id(default_id) or link_type_by_id("shopee_commission") or load_link_types()[0]
    return {
        "link_type_id": fallback["id"],
        "link_type_name": fallback["name"],
        "confidence": 20,
        "matched_value": "",
        "source_field": "fallback",
        "reason": "default link type",
    }


def _match_value(value: str) -> dict | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    for item in load_link_types():
        terms = [item["id"], item["name"], item.get("short_code", ""), *item.get("aliases", [])]
        for term in terms:
            term_norm = normalize_text(term)
            if term_norm and (normalized == term_norm or term_norm in normalized):
                return item
    return None
