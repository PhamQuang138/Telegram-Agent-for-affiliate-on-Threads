from __future__ import annotations

import re


ASK_LINK = ["xin link", "cho mình link", "cho minh link", "link đâu", "link dau", "ib link", "gửi link", "gui link", "có link không", "co link khong", "để link với", "de link voi"]
ASK_PRICE = ["bao nhiêu", "bao nhieu", "giá sao", "gia sao", "giá nhiêu", "gia nhieu", "mấy tiền", "may tien", "giá bao nhiêu", "gia bao nhieu"]
PRODUCT_INTEREST = ["mua ở đâu", "mua o dau", "mẫu nào", "mau nao", "còn mẫu khác", "con mau khac", "có màu khác", "co mau khac", "dùng cho", "dung cho", "hợp với", "hop voi"]
POSITIVE = ["hay", "ổn", "on", "hợp lý", "hop ly", "thích", "thich", "đúng", "dung", "cần", "can"]
NEGATIVE = ["dở", "do", "chán", "chan", "xấu", "xau", "không ổn", "khong on", "lừa", "lua"]


def analyze_reply(reply_text: str) -> dict:
    text = _normalize(reply_text)
    is_spam = _is_spam(text)
    asks_for_link = _contains(text, ASK_LINK)
    asks_for_price = _contains(text, ASK_PRICE)
    product_interest = _contains(text, PRODUCT_INTEREST)

    intent = "unknown"
    if is_spam:
        intent = "spam"
    elif asks_for_link:
        intent = "ask_link"
    elif asks_for_price:
        intent = "ask_price"
    elif product_interest:
        intent = "product_interest"
    elif _contains(text, NEGATIVE):
        intent = "negative"
    elif _contains(text, POSITIVE):
        intent = "feedback"
    elif len(text.split()) >= 2:
        intent = "conversation"

    sentiment = "neutral"
    if _contains(text, NEGATIVE):
        sentiment = "negative"
    elif asks_for_link or asks_for_price or product_interest or _contains(text, POSITIVE):
        sentiment = "positive"

    confidence = 0.35
    if intent in {"ask_link", "ask_price", "product_interest", "spam"}:
        confidence = 0.85
    elif intent in {"feedback", "negative"}:
        confidence = 0.65

    return {
        "intent": intent,
        "sentiment": sentiment,
        "asks_for_link": asks_for_link,
        "asks_for_price": asks_for_price,
        "product_interest": product_interest,
        "is_spam": is_spam,
        "confidence": confidence,
    }


def calculate_purchase_intent_score(replies: list[dict]) -> float:
    if not replies:
        return 0.0
    score = 0.0
    for reply in replies:
        score += 3.0 if reply.get("asks_for_link") else 0.0
        score += 2.5 if reply.get("asks_for_price") else 0.0
        score += 2.0 if reply.get("product_interest") else 0.0
        score += 0.5 if reply.get("intent") == "feedback" and reply.get("sentiment") == "positive" else 0.0
        score -= 1.0 if reply.get("intent") == "negative" or reply.get("sentiment") == "negative" else 0.0
        score -= 0.5 if reply.get("is_spam") else 0.0
    return round(max(0.0, min(100.0, (score / max(1, len(replies))) * 20)), 3)


def _normalize(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _contains(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def _is_spam(text: str) -> bool:
    if len(text) < 2:
        return True
    if len(re.findall(r"https?://|www\.", text)) >= 2:
        return True
    if re.fullmatch(r"[\W_]+", text):
        return True
    words = text.split()
    if len(words) >= 8 and len(set(words)) <= 2:
        return True
    return False
