from __future__ import annotations

import json
import random
import re

from app.services.content_quality import evaluate_content
from app.services.content_similarity import is_too_similar
from app.services.product_scoring import score_products


def generate_affiliate_content(
    keyword: str | None,
    products: list[dict],
    previous_posts: list[str],
    analytics_context: dict | None = None,
    target_platform: str = "threads",
) -> dict:
    keyword = (keyword or "đồ tiện ích").strip()
    analytics_context = analytics_context or {}
    scored_products = score_products(products, keyword, analytics_context)
    selected = scored_products[: min(4, len(scored_products))]

    need = _choose_need(keyword, selected)
    persona = _choose_persona(keyword, selected, analytics_context)
    angle = _choose_angle(keyword, selected, analytics_context)
    hook_type = _choose_hook(keyword)
    content = _compose_content(keyword, selected, need, persona, angle, hook_type)

    if is_too_similar(content, previous_posts):
        hook_type = "question"
        content = _compose_content(keyword, selected, need, persona, angle, hook_type)

    quality = evaluate_content(
        content,
        [str(item.get("product_name") or "") for item in selected],
        previous_posts,
    )

    return {
        "need": need,
        "persona": persona,
        "angle": angle,
        "hook_type": hook_type,
        "content": content,
        "cta": "",
        "hashtags": _hashtags(keyword),
        "quality_score": int(quality["score"]),
        "reasoning": f"{persona} gặp vấn đề '{need}', nên angle '{angle}' hợp để nhắc sản phẩm tự nhiên.",
        "selected_products": selected,
    }


def prompt_payload(
    keyword: str | None,
    products: list[dict],
    previous_posts: list[str],
    analytics_context: dict | None = None,
    target_platform: str = "threads",
) -> dict:
    local_plan = generate_affiliate_content(keyword, products, previous_posts, analytics_context, target_platform)
    return {
        "keyword": keyword or "",
        "products_json": json.dumps(local_plan["selected_products"], ensure_ascii=False),
        "previous_posts": "\n---\n".join(previous_posts) or "None",
        "analytics_context": json.dumps(analytics_context or {}, ensure_ascii=False),
        "target_platform": target_platform,
        "local_plan": local_plan,
    }


def _choose_need(keyword: str, products: list[dict]) -> str:
    for product in products:
        needs = product.get("possible_needs") or []
        if needs:
            return str(needs[0])
    lower = keyword.lower()
    if any(word in lower for word in ["áo", "outfit", "giày"]):
        return "muốn ra ngoài nhìn gọn mà không phải nghĩ outfit quá lâu"
    if any(word in lower for word in ["bàn", "laptop", "chuột", "văn phòng"]):
        return "góc làm việc có quá nhiều thứ nhỏ gây bực"
    if any(word in lower for word in ["quạt", "nắng", "nóng"]):
        return "ngồi một chỗ mà thời tiết làm tụt mood"
    return "một việc nhỏ trong ngày cứ lặp lại tới mức phát phiền"


def _choose_persona(keyword: str, products: list[dict], analytics_context: dict) -> str:
    top_persona = _top_metric(analytics_context, "personas")
    if top_persona:
        return top_persona
    for product in products:
        personas = product.get("possible_personas") or []
        if personas:
            return str(personas[0])
    if "học" in keyword.lower():
        return "sinh viên"
    if "bóng" in keyword.lower() or "thể thao" in keyword.lower():
        return "người chơi thể thao cuối tuần"
    return "người đi làm thích đồ tiện"


def _choose_angle(keyword: str, products: list[dict], analytics_context: dict) -> str:
    top_angle = _top_metric(analytics_context, "angles")
    if top_angle:
        return top_angle
    for product in products:
        angles = product.get("possible_angles") or []
        if angles:
            return str(angles[0])
    return "vấn đề nhỏ nhưng ai cũng từng gặp"


def _choose_hook(keyword: str) -> str:
    lower = keyword.lower()
    if any(word in lower for word in ["bóng", "esport", "fan", "t1", "messi", "faker"]):
        return "banter"
    return random.choice(["observation", "confession", "question", "tiny_drama"])


def _compose_content(
    keyword: str,
    products: list[dict],
    need: str,
    persona: str,
    angle: str,
    hook_type: str,
) -> str:
    product_phrase = _product_phrase(products, keyword)
    templates = {
        "question": [
            "Có ai cũng bị {need} không? {product_phrase} kiểu không làm đời đổi màu, nhưng đúng lúc thì đỡ cáu hẳn.",
            "{persona} có cần một món giải quyết {need} không, hay cứ chịu đựng tới khi bực? Mình thấy {product_phrase} hợp vai 'nhỏ mà cứu mood'.",
        ],
        "confession": [
            "Thú nhận là nhiều khi mình không cần món gì quá ghê gớm, chỉ cần bớt cảnh {need}. {product_phrase} nhìn hợp đúng kiểu mua vì đời sống quá nhiều chuyện lặt vặt.",
            "Có những món nhìn bình thường nhưng sinh ra để xử lý {need}. {product_phrase} thuộc nhóm mình sẽ để vào wishlist trước khi lại cáu vì chuyện nhỏ.",
        ],
        "tiny_drama": [
            "Drama người lớn đôi khi không nằm ở công ty, mà nằm ở cảnh {need}. Thấy {product_phrase} mới hiểu có mấy món nhỏ nhìn hiền mà cứu một ngày khá nhiều.",
            "Đỉnh cao mệt mỏi là {need}. Không cần setup hoành tráng, {product_phrase} nhìn như kiểu thêm một món là bớt được một cơn bực.",
        ],
        "banter": [
            "Đi đá bóng/cuối tuần thì ai cũng hô đơn giản, tới lúc chuẩn bị đồ mới thấy {need}. {product_phrase} nhìn hợp kiểu gọn gàng để còn giữ sức tranh luận sau trận.",
            "Fan thể thao có thể cãi nhau 90 phút, nhưng vẫn chịu thua mấy chuyện như {need}. {product_phrase} nhìn hợp để xử lý phần đời thường trước khi ra sân.",
        ],
        "observation": [
            "Mấy chuyện như {need} nghe nhỏ xíu, nhưng gặp mỗi ngày là đủ tụt mood. {product_phrase} nhìn hợp kiểu món phụ trong nhà mà dùng đúng lúc thì thấy đáng tiền.",
            "Không phải lúc nào cần mua món lớn. Nhiều khi chỉ là bớt {need}; {product_phrase} nhìn hợp cho kiểu sống gọn hơn một chút.",
        ],
    }
    template = random.choice(templates.get(hook_type, templates["observation"]))
    return _clean(template.format(need=need, persona=persona, product_phrase=product_phrase))


def _product_phrase(products: list[dict], keyword: str) -> str:
    if not products:
        return f"một món liên quan tới {keyword}"
    names = [_short_name(str(item.get("product_name") or item.get("name") or keyword)) for item in products[:2]]
    if len(names) == 1:
        return names[0]
    return f"{names[0]} hoặc {names[1]}"


def _short_name(name: str) -> str:
    name = re.sub(r"\b\d+[kK]?\b", "", name)
    name = re.sub(r"\s+", " ", name).strip(" -,.")
    words = name.split()
    return " ".join(words[:8]) if len(words) > 8 else name


def _top_metric(analytics_context: dict, key: str) -> str:
    rows = analytics_context.get(key) or []
    if rows and isinstance(rows[0], dict):
        return str(rows[0].get("name") or rows[0].get("value") or "").strip()
    return ""


def _hashtags(keyword: str) -> list[str]:
    lower = keyword.lower()
    if "áo" in lower or "outfit" in lower:
        return ["outfit"]
    if "bàn" in lower or "văn phòng" in lower:
        return ["workdesk"]
    return []


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:350].rsplit(" ", 1)[0] if len(text) > 350 else text
