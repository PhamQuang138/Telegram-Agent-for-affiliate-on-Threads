from __future__ import annotations

import re

from app.config import get_settings

FORBIDDEN = ["mua ngay", "sale sốc", "sale soc", "đảm bảo", "dam bao", "mình dùng rồi", "minh dung roi", "shop uy tín", "shop uy tin", "hàng xịn", "hang xin", "rẻ nhất", "re nhat"]


def generate_demand_comment(original_post: dict, intent: dict, products: list[dict]) -> dict:
    max_links = max(1, min(get_settings().threads_demand_max_links_per_comment, 4))
    issues: list[str] = []
    usable = [item for item in products if item.get("affiliate_url")][:max_links]
    if not usable:
        return {"comment": "", "product_count": 0, "quality_score": 0, "issues": ["no_products"]}

    for count in range(len(usable), 0, -1):
        selected = usable[:count]
        intro = _intro(intent)
        lines = [intro, ""]
        for index, product in enumerate(selected, start=1):
            lines.append(f"{index}. {_short_name(product.get('name') or '')}")
            lines.append(f"   {product.get('affiliate_url')}")
        comment = "\n".join(lines).strip()
        if len(comment) <= 900 or count <= 2:
            issues.extend(_issues(comment))
            score = 85 - len(issues) * 15
            return {"comment": comment, "product_count": count, "quality_score": max(0, score), "issues": issues}
    return {"comment": "", "product_count": 0, "quality_score": 0, "issues": ["cannot_fit"]}


def _intro(intent: dict) -> str:
    category = intent.get("category") or "món này"
    price = (intent.get("constraints") or {}).get("price_max")
    if price:
        return f"Nếu bạn đang tìm {category} quanh tầm đó thì mình thấy vài mẫu khá gần nhu cầu, xem lại thông số và giá hiện tại nhé:"
    if intent.get("intent") == "ask_recommendation":
        return f"Mình thấy vài lựa chọn khá gần nhu cầu {category}, bạn xem thêm thông số rồi so thử nhé:"
    if intent.get("intent") == "ask_where_to_buy":
        return f"Có vài mẫu liên quan tới {category} để bạn tham khảo, giá và thông số xem trong link cho chắc nha:"
    return f"Mình gom vài mẫu gần với thứ bạn đang tìm, bạn xem thử có hợp không nha:"


def _short_name(name: str) -> str:
    name = re.sub(r"\[[^\]]+\]", " ", name or "")
    name = re.sub(r"\b(sale|flash|freeship|rẻ vô đối|chính hãng)\b", " ", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" -")
    return name[:72].rstrip()


def _issues(comment: str) -> list[str]:
    lowered = comment.lower()
    found = [f"forbidden:{word}" for word in FORBIDDEN if word in lowered]
    if len(re.findall(r"https?://", comment)) > 4:
        found.append("too_many_links")
    if "#" in comment:
        found.append("hashtag")
    return found
