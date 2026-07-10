from __future__ import annotations


def build_reply_suggestion(reply: dict, post: dict) -> dict:
    intent = str(reply.get("intent") or "")
    has_links = bool(post.get("has_links"))
    if reply.get("is_spam"):
        return {"reply_text": "", "should_reply": False, "reason": "spam", "requires_manual_approval": True}
    if intent == "ask_link" or reply.get("asks_for_link"):
        if has_links:
            text = "Có nhé, mình để link trong phần trả lời của bài rồi, bạn xem thử nha."
        else:
            text = "Có, để mình gom link tham khảo rồi gửi lại cho dễ xem nha."
        return {"reply_text": text, "should_reply": True, "reason": "asked for link", "requires_manual_approval": True}
    if intent == "ask_price" or reply.get("asks_for_price"):
        return {
            "reply_text": "Giá có thể thay đổi theo shop, bạn bấm link tham khảo để xem đúng giá hiện tại nha.",
            "should_reply": True,
            "reason": "asked for price",
            "requires_manual_approval": True,
        }
    if intent == "product_interest" or reply.get("product_interest"):
        return {
            "reply_text": "Mình thấy món này hợp để tham khảo, còn chọn mẫu cụ thể thì nên xem thêm ảnh và review trong link.",
            "should_reply": True,
            "reason": "showed product interest",
            "requires_manual_approval": True,
        }
    return {"reply_text": "", "should_reply": False, "reason": "no commercial intent", "requires_manual_approval": True}
