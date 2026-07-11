from __future__ import annotations

import json
from pathlib import Path

from app.services.affiliate_link_type_classifier import normalize_text

CATEGORIES_PATH = Path(__file__).resolve().parents[2] / "data" / "product_categories.json"
CATEGORY_SOURCE_FIELDS = (
    "danh muc san pham",
    "nganh hang",
    "category",
    "product category",
    "category name",
)


def load_categories() -> list[dict]:
    if not CATEGORIES_PATH.exists():
        return [{"id": "other", "label": "Khác", "aliases": ["other"], "keywords": []}]
    return json.loads(CATEGORIES_PATH.read_text(encoding="utf-8"))


def valid_category_ids() -> set[str]:
    return {item["id"] for item in load_categories()}


def category_label(category_id: str) -> str:
    for category in load_categories():
        if category["id"] == category_id:
            return category.get("label") or category_id
    return category_id


def classify_product_category(row: dict, product_name: str = "", shop_name: str = "") -> dict:
    normalized_row = {normalize_text(str(key)): str(value or "").strip() for key, value in row.items()}
    for field in CATEGORY_SOURCE_FIELDS:
        raw = normalized_row.get(field, "")
        if not raw:
            continue
        matched = _match_category(raw)
        if matched:
            return {
                "category_id": matched["id"],
                "category_name": matched.get("label", matched["id"]),
                "confidence": 90,
                "matched_value": raw,
                "source_field": field,
                "reason": "category column",
            }

    haystack = normalize_text(f"{product_name} {shop_name}")
    best = None
    best_score = 0
    for category in load_categories():
        score = 0
        for keyword in category.get("keywords", []):
            normalized = normalize_text(keyword)
            if normalized and normalized in haystack:
                score += 3 if " " in normalized else 1
        for alias in category.get("aliases", []):
            normalized = normalize_text(alias)
            if normalized and normalized in haystack:
                score += 2
        if score > best_score:
            best = category
            best_score = score

    if best and best_score > 0:
        return {
            "category_id": best["id"],
            "category_name": best.get("label", best["id"]),
            "confidence": min(80, 40 + best_score * 10),
            "matched_value": product_name,
            "source_field": "product_name",
            "reason": "rule-based keyword score",
        }

    other = next((item for item in load_categories() if item["id"] == "other"), {"id": "other", "label": "Khác"})
    return {
        "category_id": other["id"],
        "category_name": other.get("label", "Khác"),
        "confidence": 20,
        "matched_value": product_name,
        "source_field": "fallback",
        "reason": "low category confidence",
    }


def _match_category(value: str) -> dict | None:
    normalized = normalize_text(value)
    for category in load_categories():
        terms = [category["id"], category.get("label", ""), *category.get("aliases", [])]
        for term in terms:
            term_norm = normalize_text(term)
            if term_norm and (normalized == term_norm or term_norm in normalized):
                return category
    return None
