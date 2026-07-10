from __future__ import annotations

import re


ASK_LINK = ["xin link", "cho mình link", "cho minh link", "link đâu", "link dau", "gửi link", "gui link", "có link không", "co link khong"]
ASK_WHERE = ["mua ở đâu", "mua o dau", "mua chỗ nào", "mua cho nao", "shop nào", "shop nao", "có chỗ bán", "co cho ban"]
ASK_RECOMMEND = ["recommend", "gợi ý", "goi y", "nên mua loại nào", "nen mua loai nao", "tư vấn", "tu van", "loại nào ổn", "loai nao on", "có mẫu nào", "co mau nao"]
ASK_PRICE = ["bao nhiêu tiền", "bao nhieu tien", "giá bao nhiêu", "gia bao nhieu", "dưới", "duoi", "ngân sách", "ngan sach", "budget"]
PRODUCT_SEARCH = ["đang tìm", "dang tim", "cần tìm", "can tim", "tìm giúp", "tim giup", "có ai biết", "co ai biet"]
BOUGHT_DONE = ["mới mua", "moi mua", "vừa mua", "vua mua", "đã mua", "da mua", "chốt đơn", "chot don"]
SELLER_SPAM = ["tuyển cộng tác viên", "tuyen cong tac vien", "sỉ lẻ", "si le", "inbox shop", "khách sỉ", "khach si", "nhận order", "nhan order"]
SENSITIVE = ["thuốc", "thuoc", "vay tiền", "vay tien", "cờ bạc", "co bac", "18+"]


def classify_purchase_intent(text: str, matched_keyword: str | None = None) -> dict:
    raw = (text or "").strip()
    normalized = _normalize(raw)
    constraints = _constraints(normalized)
    category = _category(normalized, matched_keyword or "")

    if _is_spam_or_unsafe(normalized):
        return _result("general_discussion", 0, category, normalized, constraints, False, "spam, seller, bought-done, or unsafe")
    if not category:
        return _result("general_discussion", 0, "", normalized, constraints, False, "cannot identify product/category")

    intent = "general_discussion"
    score = 0
    if _contains(normalized, ASK_LINK):
        intent, score = "ask_link", 92
    elif _contains(normalized, ASK_WHERE):
        intent, score = "ask_where_to_buy", 88
    elif _contains(normalized, ASK_RECOMMEND):
        intent, score = "ask_recommendation", 84
    elif _contains(normalized, ASK_PRICE):
        intent, score = "ask_price", 78
    elif _contains(normalized, PRODUCT_SEARCH):
        intent, score = "product_search", 74
    elif any(token in normalized for token in ["nên chọn", "nen chon", "so sánh", "so sanh"]):
        intent, score = "compare_products", 72

    if constraints.get("price_max") and score:
        score += 3
    if matched_keyword and _normalize(matched_keyword) in normalized:
        score += 4
    score = min(100, score)
    eligible = score >= 70
    reason = "purchase intent detected" if eligible else "low purchase intent"
    return _result(intent, score, category, _normalized_query(normalized, category), constraints, eligible, reason)


def _result(intent: str, score: float, category: str, query: str, constraints: dict, eligible: bool, reason: str) -> dict:
    return {
        "intent": intent,
        "purchase_intent_score": score,
        "category": category,
        "normalized_query": query,
        "constraints": constraints,
        "eligible": eligible,
        "reason": reason,
    }


def _constraints(text: str) -> dict:
    price_max = None
    match = re.search(r"(?:dưới|duoi|under)\s*(\d+)\s*k", text)
    if match:
        price_max = int(match.group(1)) * 1000
    match = match or re.search(r"(\d+)\s*k\s*(?:đổ lại|do lai|tro xuong|trở xuống)", text)
    if match and price_max is None:
        price_max = int(match.group(1)) * 1000
    audience = None
    for word in ["nam", "nữ", "nu", "sinh viên", "sinh vien", "dân văn phòng", "dan van phong"]:
        if word in text:
            audience = word
            break
    features = []
    for word in ["gấp gọn", "gap gon", "không dây", "khong day", "chống nắng", "chong nang", "co giãn", "co gian", "để bàn", "de ban"]:
        if word in text:
            features.append(word)
    use_case = None
    for word in ["đi học", "di hoc", "đi làm", "di lam", "đá bóng", "da bong", "bàn làm việc", "ban lam viec"]:
        if word in text:
            use_case = word
            break
    return {"price_min": None, "price_max": price_max, "use_case": use_case, "audience": audience, "features": features[:5]}


def _category(text: str, keyword: str) -> str:
    combined = f"{text} {_normalize(keyword)}"
    categories = [
        ("quạt mini", ["quạt", "quat", "quạt mini", "quat mini"]),
        ("đèn học", ["đèn học", "den hoc", "đèn bàn", "den ban"]),
        ("áo khoác", ["áo khoác", "ao khoac"]),
        ("áo thể thao", ["áo đá bóng", "ao da bong", "áo thể thao", "ao the thao"]),
        ("decor bàn học", ["decor", "bàn học", "ban hoc", "bàn làm việc", "ban lam viec"]),
        ("chuột máy tính", ["chuột", "chuot", "mouse"]),
        ("giá đỡ laptop", ["giá đỡ", "gia do", "laptop stand"]),
    ]
    for category, needles in categories:
        if any(needle in combined for needle in needles):
            return category
    tokens = [token for token in combined.split() if len(token) >= 3]
    return " ".join(tokens[-3:])[:80] if any(phrase in combined for phrase in ASK_LINK + ASK_WHERE + ASK_RECOMMEND + PRODUCT_SEARCH) else ""


def _normalized_query(text: str, category: str) -> str:
    noise = ASK_LINK + ASK_WHERE + ASK_RECOMMEND + ASK_PRICE + PRODUCT_SEARCH
    query = text
    for phrase in noise:
        query = query.replace(phrase, " ")
    query = re.sub(r"\s+", " ", query).strip()
    return query or category


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _contains(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def _is_spam_or_unsafe(text: str) -> bool:
    if not text or len(text) < 8:
        return True
    if len(re.findall(r"https?://|www\.", text)) >= 2:
        return True
    if _contains(text, BOUGHT_DONE) and not _contains(text, ASK_LINK + ASK_WHERE + ASK_RECOMMEND + ASK_PRICE + PRODUCT_SEARCH):
        return True
    return _contains(text, SELLER_SPAM) or _contains(text, SENSITIVE)
