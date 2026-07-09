import re


PERSONA_HINTS = {
    "bàn": "dân văn phòng",
    "laptop": "người làm việc bàn giấy",
    "áo": "người thích outfit gọn",
    "quạt": "người ngồi bàn làm việc",
    "đèn": "học sinh, sinh viên",
    "balo": "sinh viên hoặc dân đi làm",
    "decor": "người thích phòng gọn đẹp",
    "bóng": "người chơi thể thao cuối tuần",
}


def _tokens(text: str) -> set[str]:
    return {token for token in re.sub(r"[^\w\s]", " ", text.lower()).split() if len(token) >= 2}


def _price_number(value: str | None) -> float | None:
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None
    return float(digits)


def _commission_number(value: str | None) -> float:
    if not value:
        return 0
    match = re.search(r"\d+(?:[.,]\d+)?", value)
    return float(match.group(0).replace(",", ".")) if match else 0


def _possible_needs(name: str) -> list[str]:
    lower = name.lower()
    needs = []
    if any(word in lower for word in ["quạt", "chống nắng", "áo khoác"]):
        needs.append("đỡ khó chịu vì thời tiết")
    if any(word in lower for word in ["bàn", "kệ", "laptop", "chuột", "đèn"]):
        needs.append("góc làm việc bớt bừa và dễ tập trung hơn")
    if any(word in lower for word in ["áo", "giày", "balo", "túi"]):
        needs.append("ra ngoài nhìn gọn mà không phải nghĩ quá lâu")
    return needs or ["một món nhỏ giải quyết việc lặt vặt hằng ngày"]


def score_products(
    products: list[dict],
    keyword: str | None,
    analytics_context: dict | None = None,
) -> list[dict]:
    keyword_tokens = _tokens(keyword or "")
    click_keywords = {
        str(item.get("keyword", "")).lower(): int(item.get("clicks", 0))
        for item in (analytics_context or {}).get("top_posts", [])
    }

    scored: list[dict] = []
    for product in products:
        item = dict(product)
        name = str(item.get("product_name") or item.get("name") or "")
        name_tokens = _tokens(name)
        price = _price_number(item.get("price"))
        commission = _commission_number(item.get("commission_rate") or item.get("commission"))

        score = 35
        reasons = []
        if keyword_tokens and keyword_tokens & name_tokens:
            score += 20
            reasons.append("match keyword")
        if len(name_tokens) >= 4:
            score += 10
            reasons.append("product name has context")
        if price and price <= 300000:
            score += 12
            reasons.append("easy-buy price range")
        elif price and price <= 800000:
            score += 7
            reasons.append("mid price range")
        if commission:
            score += min(12, commission)
            reasons.append("commission signal")
        for click_keyword, clicks in click_keywords.items():
            if click_keyword and click_keyword in name.lower():
                score += min(15, clicks * 2)
                reasons.append("click history")
                break

        needs = _possible_needs(name)
        personas = [
            persona
            for hint, persona in PERSONA_HINTS.items()
            if hint in name.lower() or hint in (keyword or "").lower()
        ] or ["người thích đồ tiện nhưng ghét cảm giác bị bán hàng"]

        item["score"] = min(100, round(score, 1))
        item["score_reason"] = ", ".join(reasons) or "baseline catalog signal"
        item["possible_needs"] = needs
        item["possible_personas"] = personas[:3]
        item["possible_angles"] = [
            "đời sống hằng ngày",
            "vấn đề nhỏ nhưng gây bực",
            "setup gọn hơn",
        ]
        scored.append(item)

    return sorted(scored, key=lambda row: row["score"], reverse=True)
