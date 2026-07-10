from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ThreadsPostLink


def match_products_for_demand(
    category: str,
    normalized_query: str,
    constraints: dict,
    limit: int = 4,
) -> list[dict]:
    query_tokens = _tokens(f"{category} {normalized_query} {' '.join(constraints.get('features') or [])} {constraints.get('use_case') or ''} {constraints.get('audience') or ''}")
    if not query_tokens:
        return []
    price_max = constraints.get("price_max")
    with SessionLocal() as db:
        links = list(db.scalars(select(ThreadsPostLink).order_by(ThreadsPostLink.id.desc()).limit(1000)))

    scored = []
    seen_urls: set[str] = set()
    for link in links:
        if not link.affiliate_url or link.affiliate_url in seen_urls:
            continue
        seen_urls.add(link.affiliate_url)
        product_text = f"{link.product_name} {link.shop_name or ''}"
        product_tokens = set(_tokens(product_text))
        overlap = len(set(query_tokens) & product_tokens)
        if overlap <= 0:
            continue
        score = 45 + overlap * 10
        category_tokens = set(_tokens(category))
        if category_tokens and category_tokens.issubset(product_tokens | set(query_tokens)):
            score += 12
        price = _parse_price(link.price)
        if price_max and price:
            if price <= price_max:
                score += 10
            elif price > price_max * 1.35:
                score -= 18
        for feature in constraints.get("features") or []:
            if set(_tokens(feature)) & product_tokens:
                score += 4
        if constraints.get("audience") and set(_tokens(str(constraints["audience"]))) & product_tokens:
            score += 3
        score = max(0, min(100, score))
        if score < 65:
            continue
        scored.append(
            {
                "product_id": int(link.id),
                "name": link.product_name,
                "price": price,
                "shop_name": link.shop_name or "",
                "affiliate_url": link.affiliate_url,
                "match_score": score,
                "match_reason": f"Khớp {overlap} từ khóa với nhu cầu '{category}'" + (", trong ngân sách" if price_max and price and price <= price_max else ""),
            }
        )
    scored.sort(key=lambda item: item["match_score"], reverse=True)
    return scored[: max(1, min(limit, 4))]


def _tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", (text or "").lower())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.replace("đ", "d")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    stop = {"san", "pham", "shopee", "hang", "gia", "mau", "loai", "cho", "cua", "voi", "nam", "nu"}
    return [token for token in normalized.split() if len(token) >= 2 and token not in stop]


def _parse_price(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None
