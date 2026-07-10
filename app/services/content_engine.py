from __future__ import annotations

import json
import random
import re

from app.services.angle_library import select_angle
from app.services.content_diversity import build_diversity_key, should_reduce_repetition
from app.services.content_quality import evaluate_content
from app.services.content_similarity import is_too_similar
from app.services.hook_library import choose_hook
from app.services.learning_engine import compact_account_learning_context, compact_learning_profile
from app.services.persona_library import select_persona
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
    primary_product = selected[0] if selected else {}

    need = _choose_need(keyword, selected)
    persona_obj = select_persona(keyword, primary_product, analytics_context=analytics_context)
    angle_obj = select_angle(keyword, primary_product, persona_obj, analytics_context)
    hook_obj = choose_hook(_hook_type_for(keyword, persona_obj, angle_obj), avoid=previous_posts[:5])
    persona = str(persona_obj.get("name") or _choose_persona(keyword, selected, analytics_context))
    angle = str(angle_obj.get("name") or _choose_angle(keyword, selected, analytics_context))
    hook_type = str(hook_obj.get("hook_type") or _choose_hook(keyword))
    hook = str(hook_obj.get("hook") or "")
    product_category = _product_category(keyword, selected)
    diversity_key = build_diversity_key(
        {
            "persona_id": persona_obj.get("id"),
            "angle_id": angle_obj.get("id"),
            "hook_type": hook_type,
            "product_category": product_category,
        }
    )
    diversity = should_reduce_repetition(
        {"diversity_key": diversity_key},
        analytics_context.get("recent_posts", []) if isinstance(analytics_context, dict) else [],
    )
    if not diversity["passed"]:
        angle_obj = _alternate_angle(keyword, primary_product, persona_obj, angle_obj, analytics_context)
        angle = str(angle_obj.get("name") or angle)
        hook_obj = choose_hook(None, avoid=[hook])
        hook_type = str(hook_obj.get("hook_type") or hook_type)
        hook = str(hook_obj.get("hook") or hook)
        diversity_key = build_diversity_key(
            {
                "persona_id": persona_obj.get("id"),
                "angle_id": angle_obj.get("id"),
                "hook_type": hook_type,
                "product_category": product_category,
            }
        )

    content = _compose_content(keyword, selected, need, persona, angle, hook_type, hook)

    if is_too_similar(content, previous_posts):
        hook_obj = choose_hook("question", avoid=[hook])
        hook_type = str(hook_obj.get("hook_type") or "question")
        hook = str(hook_obj.get("hook") or hook)
        content = _compose_content(keyword, selected, need, persona, angle, hook_type, hook)

    quality = evaluate_content(
        content,
        [str(item.get("product_name") or "") for item in selected],
        previous_posts,
    )

    return {
        "need": need,
        "persona": persona,
        "angle": angle,
        "hook": hook,
        "persona_id": persona_obj.get("id", ""),
        "angle_id": angle_obj.get("id", ""),
        "hook_type": hook_type,
        "content_type": "affiliate_threads",
        "content_goal": "affiliate",
        "diversity_key": diversity_key,
        "content": content,
        "cta": "",
        "hashtags": _hashtags(keyword),
        "quality_score": int(quality["score"]),
        "reasoning": f"{persona} gặp vấn đề '{need}', nên angle '{angle}' hợp để nhắc sản phẩm tự nhiên.",
        "selected_products": selected,
    }


def generate_content_ideas(
    keyword: str | None,
    products: list[dict],
    analytics_context: dict | None = None,
    count: int = 3,
) -> list[dict]:
    ideas = []
    previous: list[str] = []
    for _index in range(count):
        result = generate_affiliate_content(keyword, products, previous, analytics_context or {})
        previous.append(result["content"])
        ideas.append(
            {
                "keyword": keyword or "đồ tiện ích",
                "need": result["need"],
                "persona": result["persona"],
                "angle": result["angle"],
                "hook": result.get("hook") or "",
                "idea": result["content"],
                "selected_products": result["selected_products"][:2],
            }
        )
    return ideas


def generate_affiliate_content_from_idea(
    keyword: str | None,
    products: list[dict],
    idea: dict,
    previous_posts: list[str],
    analytics_context: dict | None = None,
    target_platform: str = "threads",
) -> dict:
    keyword = (keyword or idea.get("keyword") or "đồ tiện ích").strip()
    analytics_context = analytics_context or {}
    scored_products = score_products(products, keyword, analytics_context)
    selected = scored_products[: min(4, len(scored_products))]
    base = generate_affiliate_content(keyword, products, previous_posts, analytics_context, target_platform)

    content = _clean(str(idea.get("idea") or idea.get("content") or base["content"]))
    if is_too_similar(content, previous_posts) or len(content) < 90:
        content = base["content"]

    quality = evaluate_content(
        content,
        [str(item.get("product_name") or "") for item in selected],
        previous_posts,
    )
    return {
        **base,
        "need": str(idea.get("need") or base["need"]),
        "persona": str(idea.get("persona") or base["persona"]),
        "angle": str(idea.get("angle") or base["angle"]),
        "hook": str(idea.get("hook") or base.get("hook") or ""),
        "content": content,
        "quality_score": int(quality["score"]),
        "reasoning": "Draft được sinh từ idea seed có sẵn need/persona/angle/hook, rồi kiểm tra quality/similarity.",
        "selected_products": selected,
    }


def prompt_payload(
    keyword: str | None,
    products: list[dict],
    previous_posts: list[str],
    analytics_context: dict | None = None,
    target_platform: str = "threads",
    idea_context: dict | None = None,
) -> dict:
    local_plan = (
        generate_affiliate_content_from_idea(keyword, products, idea_context, previous_posts, analytics_context, target_platform)
        if idea_context
        else generate_affiliate_content(keyword, products, previous_posts, analytics_context, target_platform)
    )
    compact_products = [
        {
            "product_name": item.get("product_name") or item.get("name") or "",
            "score_reason": item.get("score_reason") or "",
            "possible_needs": (item.get("possible_needs") or [])[:2],
            "possible_personas": (item.get("possible_personas") or [])[:2],
            "possible_angles": (item.get("possible_angles") or [])[:2],
        }
        for item in local_plan["selected_products"][:3]
    ]
    account_name = str((idea_context or {}).get("account_name") or "").strip()
    learning_context = compact_learning_profile()
    if account_name:
        learning_context["account_profile"] = compact_account_learning_context(account_name)
    return {
        "keyword": keyword or "",
        "products_json": json.dumps(compact_products, ensure_ascii=False),
        "previous_posts": "\n---\n".join(previous_posts) or "None",
        "analytics_context": json.dumps(analytics_context or {}, ensure_ascii=False),
        "learning_context": json.dumps(learning_context, ensure_ascii=False),
        "idea_context": json.dumps(idea_context or {}, ensure_ascii=False),
        "target_platform": target_platform,
        "local_plan": {**local_plan, "selected_products": compact_products},
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


def _hook_type_for(keyword: str, persona: dict, angle: dict) -> str:
    text = " ".join([keyword.lower(), str(persona.get("id", "")), str(angle.get("id", ""))])
    if any(word in text for word in ["văn phòng", "office", "laptop", "bàn"]):
        return "office_life"
    if any(word in text for word in ["sinh viên", "student", "học", "balo"]):
        return "student_life"
    if any(word in text for word in ["mùa", "seasonal", "nắng", "áo khoác", "noel"]):
        return "seasonal"
    if "wishlist" in text:
        return "wishlist"
    if "problem" in text:
        return "problem"
    return random.choice(["observation", "question", "confession", "funny", "minimalism"])


def _alternate_angle(keyword: str, product: dict, persona: dict, current: dict, analytics_context: dict) -> dict:
    from app.services.angle_library import load_angles

    for angle in load_angles():
        if angle.get("id") != current.get("id"):
            return angle
    return current


def _compose_content(
    keyword: str,
    products: list[dict],
    need: str,
    persona: str,
    angle: str,
    hook_type: str,
    hook: str = "",
) -> str:
    product_phrase = _product_phrase(products, keyword)
    hook_prefix = hook.rstrip(". ")
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
    body = template.format(need=need, persona=persona, product_phrase=product_phrase)
    if hook_prefix and hook_prefix.lower() not in body[:80].lower():
        body = f"{hook_prefix} {body}"
    return _clean(body)


def _product_phrase(products: list[dict], keyword: str) -> str:
    if not products:
        return f"một món liên quan tới {keyword}"
    categories = {_product_category(keyword, [item]) for item in products[:4]}
    if len(products) > 1 and len(categories) > 1:
        return _generic_product_phrase(keyword, products)
    names = [
        name
        for name in (_short_name(str(item.get("product_name") or item.get("name") or keyword)) for item in products[:2])
        if name
    ]
    if not names:
        return _generic_product_phrase(keyword, products)
    if any(_looks_like_catalog_name(name) for name in names):
        return _generic_product_phrase(keyword, products)
    if len(names) == 1:
        return names[0]
    return f"{names[0]} hoặc {names[1]}"


def _short_name(name: str) -> str:
    name = re.sub(r"\[[^\]]+\]", " ", name)
    name = re.sub(r"\([^)]*(?:sale|rẻ|freeship|mã|voucher|giảm|chính hãng|hot)[^)]*\)", " ", name, flags=re.I)
    name = re.sub(r"\b(?:rẻ vô đối|sale|freeship|chính hãng|cao cấp|hot|giá rẻ|mã giảm|hàng mới|bán chạy)\b", " ", name, flags=re.I)
    name = re.sub(r"\b(?:\d+\s*xương|\d+\s*chiều|uv|upf\s*\d+|size\s*\w+)\b", " ", name, flags=re.I)
    name = re.sub(r"\b\d+[kK]?\b", "", name)
    name = re.sub(r"\s+", " ", name).strip(" -,.")
    words = name.split()
    return " ".join(words[:8]) if len(words) > 8 else name


def _looks_like_catalog_name(name: str) -> bool:
    clean = name.lower()
    return (
        bool(re.search(r"\b(?:icon|mã|uv|xương|chiều|rẻ|sale|freeship|chính hãng)\b", clean))
        or len(clean.split()) >= 7
    )


def _generic_product_phrase(keyword: str, products: list[dict]) -> str:
    category = _product_category(keyword, products)
    if category == "fashion":
        return "vài món mặc hằng ngày"
    if category == "workdesk":
        return "vài món nhỏ cho góc làm việc"
    if category == "seasonal_utility":
        return "vài món đỡ khó chịu vì thời tiết"
    if category == "student":
        return "vài món nhỏ cho đi học/đi làm"
    return "vài món nhỏ dùng hằng ngày"


def _top_metric(analytics_context: dict, key: str) -> str:
    rows = analytics_context.get(key) or []
    if rows and isinstance(rows[0], dict):
        return str(rows[0].get("name") or rows[0].get("value") or "").strip()
    return ""


def _product_category(keyword: str, products: list[dict]) -> str:
    text = " ".join([keyword, " ".join(str(item.get("product_name", "")) for item in products)]).lower()
    if any(word in text for word in ["áo", "quần", "giày", "outfit"]):
        return "fashion"
    if any(word in text for word in ["bàn", "laptop", "chuột", "đèn", "văn phòng"]):
        return "workdesk"
    if any(word in text for word in ["quạt", "nắng", "nóng"]):
        return "seasonal_utility"
    if any(word in text for word in ["balo", "học", "sinh viên"]):
        return "student"
    return "utility"


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
