from __future__ import annotations

import json
import re
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
STRONG_OVERRIDE_CATEGORY_IDS = {
    "sports",
    "electronics",
    "beauty",
    "food",
    "mother_baby",
    "health",
    "automotive",
    "pets",
}


def load_categories() -> list[dict]:
    if not CATEGORIES_PATH.exists():
        return [{"id": "other", "label": "Khac", "aliases": ["other"], "keywords": []}]
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
    product_match = _best_product_category(product_name, shop_name)

    for field in CATEGORY_SOURCE_FIELDS:
        raw = normalized_row.get(field, "")
        if not raw:
            continue
        matched = _match_category(raw)
        if not matched:
            continue
        if (
            product_match
            and product_match["score"] >= 5
            and product_match["category"]["id"] in STRONG_OVERRIDE_CATEGORY_IDS
            and product_match["category"]["id"] != matched["id"]
        ):
            category = product_match["category"]
            return {
                "category_id": category["id"],
                "category_name": category.get("label", category["id"]),
                "confidence": min(88, 45 + product_match["score"] * 8),
                "matched_value": product_name,
                "source_field": "product_name",
                "reason": "strong product keyword override",
            }
        return {
            "category_id": matched["id"],
            "category_name": matched.get("label", matched["id"]),
            "confidence": 90,
            "matched_value": raw,
            "source_field": field,
            "reason": "category column",
        }

    if product_match:
        category = product_match["category"]
        return {
            "category_id": category["id"],
            "category_name": category.get("label", category["id"]),
            "confidence": min(80, 40 + product_match["score"] * 10),
            "matched_value": product_name,
            "source_field": "product_name",
            "reason": "rule-based keyword score",
        }

    other = next((item for item in load_categories() if item["id"] == "other"), {"id": "other", "label": "Khac"})
    return {
        "category_id": other["id"],
        "category_name": other.get("label", "Khac"),
        "confidence": 20,
        "matched_value": product_name,
        "source_field": "fallback",
        "reason": "low category confidence",
    }


def _best_product_category(product_name: str = "", shop_name: str = "") -> dict | None:
    haystack = normalize_text(f"{product_name} {shop_name}")
    best = None
    best_score = 0
    for category in load_categories():
        score = 0
        for keyword in category.get("keywords", []):
            normalized = normalize_text(keyword)
            if normalized and _contains_term(haystack, normalized):
                score += 3 if " " in normalized else 1
        for alias in category.get("aliases", []):
            normalized = normalize_text(alias)
            if normalized and _contains_term(haystack, normalized):
                score += 2
        if score > best_score:
            best = category
            best_score = score

    if best and best_score > 0:
        return {"category": best, "score": best_score}
    return None


def _contains_term(haystack: str, term: str) -> bool:
    if not haystack or not term:
        return False
    return re.search(rf"(^|\s){re.escape(term)}($|\s)", haystack) is not None


def _match_category(value: str) -> dict | None:
    normalized = normalize_text(value)
    for category in load_categories():
        terms = [category["id"], category.get("label", ""), *category.get("aliases", [])]
        for term in terms:
            term_norm = normalize_text(term)
            if term_norm and (normalized == term_norm or _contains_term(normalized, term_norm)):
                return category
    return None
