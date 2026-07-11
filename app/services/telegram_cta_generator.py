from __future__ import annotations

import random


TEMPLATES = [
    "Minh gom link moi theo ngay trong group Telegram, ai can thi vao xem nhe:\n{url}",
    "Danh sach link minh de trong group cho de cap nhat, moi nguoi xem o day nhe:\n{url}",
    "Ai can xem link theo tung danh muc thi minh gom trong group nay:\n{url}",
    "May link moi minh gom vao group de do troi bai, can thi vao xem nhe:\n{url}",
    "Group nay minh cap nhat link theo ngay, ai dang can thi ghe xem:\n{url}",
    "Minh de danh sach trong group Telegram cho gon, link group o day:\n{url}",
]


def generate_telegram_cta(
    post_context: dict,
    group_url: str,
    recent_ctas: list[str] | None = None,
) -> str:
    if not group_url:
        raise ValueError("THREADS_TELEGRAM_GROUP_URL is required for Telegram CTA")

    recent = {item.strip() for item in (recent_ctas or []) if item.strip()}
    candidates = [template.format(url=group_url) for template in TEMPLATES]
    fresh = [candidate for candidate in candidates if candidate not in recent]
    if not fresh:
        fresh = candidates

    topic = str(post_context.get("keyword") or post_context.get("topic") or "").strip().lower()
    if topic and any(word in topic for word in ["ban lam viec", "van phong", "setup"]):
        preferred = f"Minh gom may link do nho cho goc lam viec trong group, ai can thi vao xem nhe:\n{group_url}"
        if preferred not in recent:
            return preferred

    return random.choice(fresh)
