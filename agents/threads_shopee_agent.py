import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from google import genai
from sqlalchemy.orm import Session

from app.config import get_settings
from app.schemas import ThreadsDraft, ThreadsDraftRequest
from app.services.content_engine import generate_affiliate_content, prompt_payload
from app.services.threads_repository import analytics_context, previous_similar_posts

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "threads_shopee_prompt.txt"
AFFILIATE_ENGINE_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "affiliate_content_engine_prompt.txt"
ENGAGEMENT_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "threads_engagement_prompt.txt"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
PRODUCT_CREATOR_STYLE = (
    "viral đời thường, group-chat funny, product as prop, concrete scene first, "
    "lightly dramatic, no CTA, no price, no catalog listing"
)

SHARED_CREATOR_DIRECTION = """Shared creator direction used across all bot features:
- Write like the same Vietnamese micro-content creator, whether the post is for engagement or Shopee.
- The first sentence needs a concrete scene, petty observation, funny confession, or harmless tension.
- Product posts should still feel like feed posts. The product is only a prop inside the story.
- Avoid generic product words unless grounded in a scene: hợp lý, tiện, đỡ bực, phù hợp, tham khảo.
- Avoid starting with fixed hooks too often, especially Chuyện là, Hot take nhẹ, Mình thấy.
- Do not write an English setup sentence before Vietnamese content.
- Do not list product prices, money amounts, or multiple product names in the post body.
- End as a human thought, joke, question, or tiny confession, not a sales CTA."""

ENGAGEMENT_PERSONAS = {
    "daily": """Persona: Đời thường sắc nhẹ.
Write like a witty Vietnamese creator who notices tiny daily absurdities.
Use humor, self-roast, and relatable scenes. Keep the tone warm, a bit petty, but never mean.
You have casual music literacy: comeback hype, album rollouts, MV drops, concert ticket stress, setlists, charts, streaming culture, fandom inside jokes, playlist moods, bias culture, and the way one chorus can ruin someone's whole workday in a good way.
When the topic is music, sound like someone who actually lives with songs in their day, not a generic entertainment page.""",
    "controversial": """Persona: Gây tranh cãi nhẹ.
Write like a Vietnamese creator who is willing to say the quiet part out loud, but only about low-stakes behavior, taste, habits, fandom reactions, routines, or online manners.
You have real sports/esports literacy: football fan culture, VAR drama, form swings, derby tension, GOAT debates, roster changes, esports meta, draft/ban-pick, choke/comeback, late-night matches, and group chat meltdowns.
You also have music literacy: comeback discourse, chart watching, fan chants, concert ticket wars, setlist debates, album versions, streaming parties, viral snippets, producer tags, live vocals discourse, and playlist identity.
When the topic is sports/esports, sound like someone who actually watches matches, not a generic ragebait page.
When the topic is music, sound like someone who follows fandom behavior, release cycles, and listening habits, not a generic showbiz page.
Allowed: playful teasing, sharper disagreement, calling out behavior, mild sarcasm, and a stronger first sentence.
Sports/esports targets you may tease: overreactions, excuse-making, victory-lap posting, blaming VAR/draft, fan superstition, sleeping at 3 AM for a match, switching narratives after one game.
Music targets you may tease: streaming guilt, fighting over charts, pretending not to care about rankings, judging a song after 15 seconds, overusing the word flop, buying album versions like emotional insurance, and acting normal after losing concert tickets.
Forbidden: insults toward fanbases, teams, players, countries, religions, regions, classes, genders, bodies, health, protected groups, or real private people.
Do not use slurs, threats, dehumanizing words, fake claims, or harassment.
Attack the behavior or opinion, never the identity of a person or group.""",
    "advisor": """Persona: Prompt advice / content strategist đời thường.
Write like a Vietnamese creator who gives sharp, practical content advice without sounding like a marketing teacher.
You have learned from affiliate content skills:
- Trending Content Scout: look for what people already react to, then filter it through a Vietnamese daily-life scene.
- Viral Post Writer: hook first, story over pitch, one clear angle, platform-native wording, no generic claim.
- Content angle thinking: choose one tension such as contradiction, tiny pain, social embarrassment, before/after imagination, or group identity.
- Compliance instinct: do not invent facts, numbers, scandals, or fake proof.

Voice:
- concise, useful, slightly opinionated, like a friend fixing someone's post in a group chat
- can give advice about content, prompts, AI, social posting, affiliate, Threads, fandom, work, lifestyle, or product angles
- make the advice feel specific: mention hook, angle, scene, CTA, link placement, tone, or why a post feels fake
- use mini examples only when they fit, but do not turn the post into a tutorial

Allowed:
- constructive critique
- small frameworks in plain Vietnamese
- contrarian advice about why a post feels boring
- practical prompt-writing observations
- calling out vague content, template voice, lazy hooks, and over-polished copy

Forbidden:
- corporate marketing jargon
- long step-by-step lessons
- pretending to have private data or real analytics
- insulting creators or audiences
- turning every post into "3 tips" unless the topic asks for tips.""",
}


class OpenRouterFreeDailyLimitExceeded(RuntimeError):
    pass


class GeminiQuotaExceeded(RuntimeError):
    pass


class ModelTemporarilyUnavailable(RuntimeError):
    def __init__(self, message: str, cooldown_seconds: int = 120):
        super().__init__(message)
        self.cooldown_seconds = cooldown_seconds


_MODEL_COOLDOWNS: dict[str, float] = {}
MODEL_CHECK_PROMPT = (
    'Return only valid JSON: {"content":"ok","cta":"","hashtags":[],"quality_score":100}'
)


def _fallback_draft(request: ThreadsDraftRequest) -> ThreadsDraft:
    keyword = _humanize_topic(request.keyword or request.product_name or "món đồ nhỏ")
    raw_product_context = request.product_name or keyword
    product = _fallback_product_phrase(raw_product_context, keyword)
    scene = _fallback_product_scene(raw_product_context, product)
    has_product_context = _has_product_context(request)
    variants = [
        f"{scene} Lớn rồi mới thấy có những thứ không cần đẹp xuất sắc, chỉ cần xuất hiện đúng lúc mình sắp cáu.",
        f"Nói hơi mất lòng nhưng nhiều cơn bực trong ngày bắt đầu từ mấy thứ rất bé. {product.capitalize()} kiểu này không hào nhoáng, nhưng đúng vai phụ cần có.",
        f"Có những ngày không muốn nâng cấp cuộc đời, chỉ muốn bớt một chuyện lấn cấn trước mắt. {product.capitalize()} nghe nhỏ vậy thôi mà khá đúng mood.",
        f"{scene} Không phải mua để đổi đời, chỉ là để một việc nhỏ thôi đừng tự nhiên thành drama.",
    ]
    if not has_product_context:
        variants = [
            f"Nói hơi mất lòng nhưng {keyword} là kiểu chủ đề chỉ cần thả lên feed là mỗi người tự lôi một phiên bản đời mình ra kể.",
            f"Có vài chuyện không lớn, nhưng đủ làm group chat sáng đèn. {keyword.capitalize()} nghe vậy thôi mà dễ chạm đúng cơn ngứa rất đời.",
            f"Tự nhiên thấy {keyword} buồn cười ở chỗ ai cũng bảo bình thường, xong lại có ý kiến rất riêng.",
        ]
    index_seed = f"{keyword}|{product}"
    index = int(hashlib.sha1(index_seed.encode("utf-8")).hexdigest(), 16) % len(variants)
    return ThreadsDraft(
        content=_trim_complete_sentence(variants[index], 350),
        cta="",
        hashtags=[] if not has_product_context else ["ShopeeFinds"],
        quality_score=55,
    )


def _has_product_context(request: ThreadsDraftRequest) -> bool:
    product_name = (request.product_name or "").strip()
    keyword = (request.keyword or "").strip()
    return bool(
        request.product_url
        or request.affiliate_url
        or re.search(r"(?m)^\s*\d+\.", product_name)
        or (product_name and product_name != keyword)
    )


def _fallback_product_phrase(product_name: str, keyword: str) -> str:
    raw = _remove_price_context(product_name or keyword)
    clean = _humanize_topic(raw)
    lower = raw.lower()

    if any(word in lower for word in ["bóng đá", "tập gym", "thể dục", "yoga", "tạ", "thể thao", "pickleball"]):
        return "vài món đồ thể thao/tập luyện"
    if any(word in lower for word in ["laptop", "chuột", "bàn", "kệ", "dây sạc", "mực in"]):
        return "vài món cho góc làm việc"
    if any(word in lower for word in ["áo", "quần", "khoác", "hoodie", "jean", "kính", "ốp", "nhuộm tóc", "tóc", "set bộ", "sét bộ"]):
        return "vài món chỉnh lại ngoại hình"
    if any(word in lower for word in ["bếp", "hộp cơm", "ly", "bình", "kitchen"]):
        return "vài món đồ sinh hoạt nhỏ"
    if re.search(r"(?m)^\s*\d+\.", product_name or ""):
        return "vài món nhỏ trong list"

    return clean[:70] or "vài món nhỏ"


def _fallback_product_scene(product_name: str, product_phrase: str) -> str:
    lower = (product_name or "").lower()
    if any(word in lower for word in ["kính", "ốp", "iphone", "nhìn trộm"]):
        return "Cái cảm giác đưa điện thoại ra chỗ đông người mà màn hình cứ như bảng tin công cộng thật sự hơi mệt."
    if any(word in lower for word in ["nhuộm tóc", "xám khói", "tóc"]):
        return "Có những hôm chưa muốn thay đời, chỉ muốn đổi cái tóc cho đỡ thấy mình lặp lại."
    if any(word in lower for word in ["áo", "quần", "set bộ", "sét bộ", "khoác", "hoodie", "jean"]):
        return "Tủ đồ không thiếu đồ, chỉ thiếu vài thứ mặc lên khỏi phải đứng đơ trước gương."
    if any(word in lower for word in ["bóng đá", "tập gym", "thể dục", "yoga", "tạ", "thể thao", "pickleball"]):
        return "Có những buổi muốn vận động nhưng não tìm cớ nhanh hơn chân."
    if any(word in lower for word in ["laptop", "chuột", "bàn", "kệ", "dây sạc", "mực in"]):
        return "Bàn làm việc bừa đôi khi không làm mình lười hơn, nhưng làm mình cáu nhanh hơn thật."
    if any(word in lower for word in ["bếp", "hộp cơm", "ly", "bình", "kitchen"]):
        return "Việc nhà không đáng sợ bằng cảm giác cái gì cũng thiếu đúng lúc cần."
    return f"Có vài món như {product_phrase} nhìn qua tưởng nhỏ, nhưng lại chạm đúng một cơn phiền rất đời."


def _fallback_engagement_draft(topic: str) -> ThreadsDraft:
    clean_topic = _sanitize_engagement_topic(_humanize_topic(topic or "đời sống hằng ngày"))
    scene = _fallback_engagement_scene(clean_topic)
    variants = [
        f"Nói thật, {scene}. Người ngoài nhìn vào tưởng hơi làm quá, nhưng có những niềm vui nhỏ đủ cứu mood cả ngày.",
        f"Có những ngày group chat sáng đèn chỉ vì {scene}. Không to tát, nhưng đúng kiểu chuyện nhỏ làm người ta sống lại một chút.",
        f"Hot take nhẹ: {scene} thì người ngoài thấy làm quá, còn người trong cuộc hiểu cảm giác tim chạy nhanh hơn não.",
        f"Mình thích mấy khoảnh khắc như {scene}. Tưởng nói chơi thôi, xong tự nhiên ai cũng lôi một câu chuyện riêng ra góp.",
    ]
    index = int(hashlib.sha1(clean_topic.encode("utf-8")).hexdigest(), 16) % len(variants)
    return ThreadsDraft(
        content=_trim_complete_sentence(variants[index], 260),
        cta="",
        hashtags=[],
        quality_score=55,
    )


def _fallback_engagement_scene(topic: str) -> str:
    clean = topic.lower()
    if any(word in clean for word in ["nhạc", "bài hát", "album", "concert", "comeback", "mv", "idol", "ca sĩ", "playlist", "chart", "fandom", "bias", "stream"]):
        return "một đoạn nhạc vừa bật lên đã làm group chat đổi mood"
    if any(word in clean for word in ["t1", "faker", "messi", "bóng đá", "worlds", "esports", "trận", "đội", "arg", "thụy sĩ", "cape"]):
        return "một pha sau trận khiến ai cũng muốn lên tiếng"
    if any(word in clean for word in ["deadline", "code", "đi làm", "văn phòng", "sếp"]):
        return "một deadline dí sát gáy trong lúc đầu óc vẫn chưa khởi động"
    if any(word in clean for word in ["2k5", "già", "tuổi"]):
        return "nghe mấy bạn rất trẻ than già mà mình tự kiểm tra lại lưng"
    if any(word in clean for word in ["mưa", "nóng", "lạnh", "thời tiết"]):
        return "thời tiết đổi mood nhanh hơn mình đổi kế hoạch"
    if any(word in clean for word in ["bàn", "phòng", "tủ", "bừa", "dọn"]):
        return "nhìn một góc phòng bừa và tự nhiên muốn làm lại cuộc đời"
    return topic


def _sanitize_engagement_topic(text: str) -> str:
    clean = re.sub(r"\([^)]*\)", "", str(text))
    clean = re.sub(r"\b(?:lũ|đội lốt|bọn|tụi|rác|óc chó|ngu|câm|biến đi)\b", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+", " ", clean)
    clean = clean.strip(" -–—:;,.")
    return clean or "một chuyện đang gây tranh luận nhẹ"


def _humanize_topic(text: str) -> str:
    clean = " ".join(str(text).split())
    clean = re.sub(r"https?://\S+", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" -–—:;,.")
    return clean[:90] or "một chuyện nhỏ"


def _clean_json(raw: str) -> str:
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")

    if start >= 0 and end > start:
        return cleaned[start : end + 1]

    return cleaned


def _short_debug_text(text: str, limit: int = 240) -> str:
    clean = " ".join(str(text).split())
    return clean[:limit]


def _normalize_hashtag(tag: str) -> str:
    return tag.strip().lstrip("#").replace(" ", "")[:32]


def _trim_complete_sentence(text: str, limit: int = 350) -> str:
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean

    clipped = clean[:limit].rstrip()
    sentence_ends = [match.end() for match in re.finditer(r"[.!?…](?:\s|$)", clipped)]
    last_end = sentence_ends[-1] if sentence_ends else -1
    if last_end >= 80:
        return clipped[:last_end].strip()

    last_comma = max(clipped.rfind(","), clipped.rfind(";"), clipped.rfind(":"))
    if last_comma >= 80:
        return clipped[:last_comma].strip() + "."

    last_space = clipped.rfind(" ")
    if last_space >= 80:
        return clipped[:last_space].strip() + "."

    return clipped.strip()


def _strip_price_mentions(text: str) -> str:
    clean = re.sub(
        r"\b(?:giá|gia|tầm|tam|khoảng|khoang|cỡ|co|chừng|chung)\s*~?\s*\d+(?:[.,]\d+)?\s*(?:k|K|tr|triệu|trieu|nghìn|nghin|ngàn|ngan|đồng|dong|vnd|VND|₫|đ)?\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    clean = re.sub(
        r"(?<!\w)\d+(?:[.,]\d+)?\s*(?:k|K|tr|triệu|trieu|nghìn|nghin|ngàn|ngan|đồng|dong|vnd|VND|₫|đ)(?!\w)",
        "",
        clean,
    )
    clean = re.sub(
        r"(?<!\w)\d{1,3}(?:[.,]\d{3})+\s*(?:đồng|vnd|VND|₫|đ)?(?!\w)",
        "",
        clean,
    )
    clean = re.sub(r"\s+([,.;:!?])", r"\1", clean)
    clean = re.sub(r"\(\s*\)", "", clean)
    return " ".join(clean.split())


def _sanitize_language_artifacts(text: str) -> str:
    replacements = {
        "temporada": "mùa bóng",
        "season": "mùa",
        "result": "kết quả",
    }
    clean = text
    for source, target in replacements.items():
        clean = re.sub(rf"\b{source}\b", target, clean, flags=re.IGNORECASE)

    clean = re.sub(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+", " ", clean)
    clean = clean.translate(str.maketrans("", "", "\"“”‘’«»"))
    clean = _strip_leading_foreign_sentence(clean)
    clean = _strip_leading_foreign_fragment(clean)
    clean = re.sub(r"\s+([,.;:!?])", r"\1", clean)
    clean = re.sub(r"([,.;:!?]){2,}", r"\1", clean)
    clean = re.sub(r"\s{2,}", " ", clean)
    return clean.strip()


def _strip_leading_foreign_sentence(text: str) -> str:
    clean = text.strip()
    for _ in range(2):
        match = re.match(r"^([^.!?。！？]{20,220}[.!?])\s+(.+)$", clean, flags=re.S)
        if not match:
            break
        first_sentence = match.group(1).strip()
        rest = match.group(2).strip()
        if _looks_like_english_sentence(first_sentence) and _has_vietnamese_signal(rest):
            clean = rest
            continue
        break
    return clean


def _strip_leading_foreign_fragment(text: str) -> str:
    clean = text.strip()
    match = re.search(
        r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]",
        clean,
    )
    if not match or match.start() < 20:
        return clean

    prefix = clean[: match.start()].strip(" -–—:;,.")
    rest = clean[match.start() :].strip()
    if _looks_like_english_sentence(prefix) and _has_vietnamese_signal(rest):
        return rest
    return clean


def _looks_like_english_sentence(text: str) -> bool:
    words = re.findall(r"\b[a-zA-Z]{2,}\b", text)
    if len(words) < 5 or _has_vietnamese_signal(text):
        return False

    common_english = {
        "the",
        "of",
        "and",
        "is",
        "are",
        "often",
        "caused",
        "lack",
        "small",
        "practical",
        "items",
        "product",
        "messy",
        "desk",
        "chaos",
        "people",
        "need",
        "because",
        "with",
        "for",
        "standing",
        "front",
        "wardrobe",
        "before",
        "weekend",
        "football",
        "trying",
        "look",
        "like",
        "retired",
        "pro",
    }
    hits = sum(1 for word in words if word.lower() in common_english)
    return hits >= 3 or len(words) >= 9


def _has_vietnamese_signal(text: str) -> bool:
    if re.search(r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", text):
        return True
    return bool(re.search(r"\b(?:mình|không|nhưng|với|người|đang|được|để|vài|món|nhỏ|kiểu|thì|là|có|cho)\b", text.lower()))


def _remove_price_context(text: str) -> str:
    clean = re.sub(
        r"\((?:[^)]*(?:gia|giá|price|₫|vnd|VND|\d+\s*(?:k|K|tr|triệu|trieu|nghìn|nghin|ngàn|ngan|đồng|dong))[^)]*)\)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    clean = _strip_price_mentions(clean)
    clean = re.sub(r"\((?:\s|,|;)*\)", "", clean)
    clean = re.sub(r"\b(?:gia|giá)\s*(?:,|;|\))", "", clean, flags=re.IGNORECASE)
    return " ".join(clean.split())


def _soft_parse_json(raw: str) -> dict:
    cleaned = _clean_json(raw)

    try:
        parsed = json.loads(cleaned)
        return _normalize_parsed_json(parsed if isinstance(parsed, dict) else {})
    except json.JSONDecodeError:
        pass

    def extract_text(field: str, next_field: str | None = None) -> str:
        if next_field:
            pattern = rf'"{field}"\s*:\s*"(.*?)"\s*,\s*"{next_field}"'
        else:
            pattern = rf'"{field}"\s*:\s*"(.*?)"'

        match = re.search(pattern, cleaned, flags=re.S)
        if not match:
            return ""

        return match.group(1).replace("\\n", "\n").replace('\\"', '"').strip()

    hashtags_match = re.search(r'"hashtags"\s*:\s*\[(.*?)\]', cleaned, flags=re.S)
    hashtags = re.findall(r'"([^"]+)"', hashtags_match.group(1)) if hashtags_match else []
    score_match = re.search(r'"quality_score"\s*:\s*(\d+)', cleaned)
    hook = extract_text("hook", "content")
    content = extract_text("content", "cta")

    return _normalize_parsed_json({
        "hook": hook,
        "content": content,
        "cta": extract_text("cta", "hashtags"),
        "hashtags": hashtags,
        "quality_score": int(score_match.group(1)) if score_match else 70,
    })


def _normalize_parsed_json(data: dict) -> dict:
    hook = _json_text(data.get("hook"))
    content = _json_text(
        data.get("content")
        or data.get("post")
        or data.get("body")
        or data.get("text")
    )
    if hook and content and hook.lower() not in content[: max(len(hook) + 8, 40)].lower():
        content = f"{hook} {content}"
    elif hook and not content:
        content = hook

    normalized = dict(data)
    normalized["content"] = content
    normalized["cta"] = _json_text(data.get("cta"))
    return normalized


def _json_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _looks_like_bad_content(content: str) -> bool:
    clean = content.lower()
    bad_phrases = [
        "mình vừa note lại",
        "ai đang tìm món tương tự",
        "khá hợp cho lúc cần đồ tiện",
        "có thể tham khảo thêm",
        "sản phẩm này phù hợp",
        "mua ngay",
        "càng bị nói là chuyện nhỏ",
        "càng bị bảo là chuyện nhỏ",
        "càng dễ thành chuyện khiến cả feed",
        "càng dễ làm cả đám có ý kiến",
        "nghe rất linh tinh, nhưng đúng tần số",
        "mỗi người một hệ điều hành cảm xúc",
        "đủ làm cả đám tự kể chuyện đời mình",
        "mình thích mấy chủ đề kiểu",
        "nghe như nói chơi",
        "lòi ra mỗi người",
        "chủ đề kiểu",
        "mấy món dùng hằng ngày không cần xuất sắc",
        "nghe bình thường vậy mà hợp lý",
        "đừng làm mình bực thêm",
    ]
    return (
        any(phrase in clean for phrase in bad_phrases)
        or _looks_like_foreign_language_artifact(content)
        or _looks_like_fake_product_experience(content)
    )


def _looks_like_fake_product_experience(content: str) -> bool:
    clean = content.lower()
    fake_patterns = [
        r"\b(?:mình\s+)?(?:vừa|mới|đã)\s+(?:mua|đặt|order|chốt|săn)\b",
        r"\b(?:mua|đặt|order|chốt)\s+(?:hôm qua|hôm nay|tuần trước|về rồi)\b",
        r"\bmình\s+(?:đã|vừa|mới)\s+(?:dùng|xài|test|thử|mặc|đeo)\s+(?:rồi|thật|ngoài đời)\b",
    ]
    return any(re.search(pattern, clean, flags=re.IGNORECASE) for pattern in fake_patterns)


def _looks_like_foreign_language_artifact(content: str) -> bool:
    clean = content.strip()
    if not clean:
        return False

    first_sentence = re.match(r"^([^.!?。！？]{20,220}[.!?])", clean, flags=re.S)
    if first_sentence and _looks_like_english_sentence(first_sentence.group(1)):
        return True

    english_words = re.findall(r"\b[a-zA-Z]{3,}\b", clean)
    if len(english_words) < 7:
        return False

    allowed_short_terms = {
        "shopee",
        "threads",
        "iphone",
        "macbook",
        "laptop",
        "hoodie",
        "jean",
        "gym",
        "var",
        "t1",
        "faker",
        "messi",
        "mv",
    }
    suspicious = [word for word in english_words if word.lower() not in allowed_short_terms]
    return len(suspicious) >= 7 and not _has_vietnamese_signal(" ".join(suspicious))


def _looks_like_low_quality_engagement(content: str, topic: str) -> bool:
    clean = content.lower()
    clean_topic = topic.lower().strip()
    blocked_phrases = [
        "món tương tự",
        "có thể tham khảo",
        "link ở",
        "shopee",
        "sản phẩm",
        "mua ngay",
        "cả feed có ý kiến",
        "chủ đề đủ gây tranh luận",
        "đủ làm người ta muốn cãi nhau",
        "đủ làm cả đám",
        "không phải chuyện lớn",
        "không nghiêm trọng",
        "lũ",
        "đội lốt",
        "bọn",
        "tụi",
        "óc chó",
        "ngu",
        "rác",
    ]
    if any(phrase in clean for phrase in blocked_phrases):
        return True

    # Topic stuffing: the model pastes a long topic into a generic sentence.
    if clean_topic and len(clean_topic) > 35 and clean_topic in clean[:140]:
        generic_glue = [
            "hot take nhẹ",
            "nói hơi mất lòng",
            "có những chủ đề",
            "mình bắt đầu nghi ngờ",
            "người ta muốn",
        ]
        if any(glue in clean[:140] for glue in generic_glue):
            return True

    words = clean.split()
    if len(words) < 14:
        return True

    return False


def _looks_like_low_quality_shopee(content: str, request: ThreadsDraftRequest) -> bool:
    clean = content.lower()
    keyword = (request.keyword or "").lower().strip()
    words = clean.split()
    has_group_products = bool(request.product_name and re.search(r"(?m)^\s*\d+\.", request.product_name))
    blocked_phrases = [
        "list 4 mon shopee dang xem",
        "list 4 món shopee đang xem",
        "list 5 mon shopee dang xem",
        "list 5 món shopee đang xem",
        "cả feed có ý kiến",
        "chủ đề đủ gây tranh luận",
        "càng bị nói là chuyện nhỏ",
        "càng dễ thành chuyện",
        "ai đang tìm món tương tự",
        "có thể tham khảo thêm",
    ]
    if any(phrase in clean for phrase in blocked_phrases):
        return True

    if len(content) < 90 or len(words) < 18:
        return True

    if keyword and keyword.startswith("list ") and keyword in clean[:160]:
        return True

    if has_group_products:
        product_mentions = len(re.findall(r"\b(?:áo|quần|tạ|vòng|chuột|kệ|laptop|hộp|bình|giày)\b", clean))
        if product_mentions >= 4:
            return True
        scene_words = [
            "tủ đồ",
            "bàn",
            "phòng",
            "group chat",
            "cuối tuần",
            "đi làm",
            "đi học",
            "trước giờ",
            "sau giờ",
            "ra sân",
            "đá bóng",
            "đá phủi",
            "futsal",
            "deadline",
            "mưa",
            "nóng",
            "lạnh",
        ]
        if not any(word in clean for word in scene_words):
            return True

    return False


def _looks_like_dirty_ragebait(content: str) -> bool:
    clean = content.lower()
    blocked_terms = [
        "muốn đập",
        "đập vào mặt",
        "đấm",
        "tát",
        "vả",
        "choảng",
        "choạng vào mặt",
        "lũ",
        "đội lốt",
        "bọn",
        "tụi",
        "rác",
        "ngu",
        "óc chó",
        "câm",
        "biến đi",
    ]
    return any(term in clean for term in blocked_terms)


def _validate(data: dict, request: ThreadsDraftRequest) -> ThreadsDraft:
    fallback = _fallback_draft(request)
    raw_links = [request.affiliate_url or "", request.product_url or ""]
    content = str(data.get("content") or fallback.content)
    cta = str(data.get("cta") or "")
    for raw_link in raw_links:
        if raw_link:
            content = content.replace(raw_link, "")
            cta = cta.replace(raw_link, "")
    content = _sanitize_language_artifacts(content)
    content = _strip_price_mentions(content)
    content = _trim_complete_sentence(content, 350)
    cta = cta.strip()[:160]
    hashtags = data.get("hashtags")

    if isinstance(hashtags, list):
        clean_tags = [_normalize_hashtag(str(tag)) for tag in hashtags]
        clean_tags = [tag for tag in clean_tags if tag][:3]
    else:
        clean_tags = fallback.hashtags

    try:
        score = int(float(data.get("quality_score", fallback.quality_score)))
    except (TypeError, ValueError):
        score = fallback.quality_score

    if 0 < score <= 10:
        score *= 10

    return ThreadsDraft(
        content=content if len(content) >= 20 else fallback.content,
        cta=cta,
        hashtags=clean_tags,
        quality_score=max(0, min(100, score)),
    )


def _ensure_acceptable_draft(draft: ThreadsDraft, request: ThreadsDraftRequest, provider_name: str) -> ThreadsDraft:
    if _looks_like_bad_content(draft.content):
        raise RuntimeError(f"{provider_name} produced template/fallback-like content")
    if _looks_like_low_quality_shopee(draft.content, request):
        raise RuntimeError(f"{provider_name} produced low-quality Shopee content")
    return draft


def _ensure_acceptable_engagement_draft(
    draft: ThreadsDraft,
    topic: str,
    provider_name: str,
    style: str = "",
    persona: str = "daily",
) -> ThreadsDraft:
    if _looks_like_bad_content(draft.content):
        raise RuntimeError(f"{provider_name} produced template/fallback-like engagement content")
    if _looks_like_low_quality_engagement(draft.content, topic):
        raise RuntimeError(f"{provider_name} produced low-quality engagement content")
    if _looks_like_wrong_engagement_mode(draft.content, style, persona):
        raise RuntimeError(f"{provider_name} produced engagement content that does not match style/persona")
    if _looks_like_dirty_ragebait(draft.content):
        raise RuntimeError(f"{provider_name} produced dirty ragebait")
    return draft


def _looks_like_wrong_engagement_mode(content: str, style: str, persona: str) -> bool:
    clean = content.lower()
    style_clean = (style or "").lower()

    if "ask for advice" in style_clean:
        question_mark = "?" in content or "？" in content
        advice_request = any(
            phrase in clean
            for phrase in [
                "nên",
                "mọi người",
                "xin lời khuyên",
                "cho mình xin",
                "có ai",
                "làm sao",
                "chọn kiểu nào",
                "cứu mình",
                "có cách nào",
            ]
        )
        return not (question_mark and advice_request)

    if "practical advice" in style_clean or persona == "advisor":
        advice_signal = any(
            phrase in clean
            for phrase in [
                "nên",
                "đừng",
                "thử",
                "mấu chốt",
                "vấn đề là",
                "cái sai",
                "một bài",
                "hook",
                "angle",
                "cảnh",
                "người đọc",
                "nghe giả",
                "nghe thật",
            ]
        )
        if "ask for advice" not in style_clean and not advice_signal:
            return True

    if "quote-like" in style_clean:
        words = clean.split()
        if len(words) > 42:
            return True
        if clean.count(".") + clean.count("?") + clean.count("!") > 3:
            return True

    return False


def _generate_openrouter_model(model: str) -> Callable[[str], str]:
    def generate(prompt: str) -> str:
        settings = get_settings()
        url = f"{settings.openrouter_base_url.rstrip('/')}/chat/completions"

        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key.strip()}",
            "Content-Type": "application/json",
            "HTTP-Referer": settings.base_url,
            "X-OpenRouter-Title": "POD Bot",
        }
        messages = [
            {
                "role": "system",
                "content": "Return only the final valid JSON object. Do not include analysis, reasoning, markdown, or explanations.",
            },
            {"role": "user", "content": prompt},
        ]
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1200,
            "response_format": {"type": "json_object"},
            "reasoning": {"exclude": True},
        }
        response = httpx.post(url, headers=headers, json=payload, timeout=25)
        if response.status_code == 400:
            payload.pop("response_format", None)
            payload.pop("reasoning", None)
            response = httpx.post(url, headers=headers, json=payload, timeout=25)
        if response.status_code == 429 and "free-models-per-day" in response.text:
            raise OpenRouterFreeDailyLimitExceeded(response.text[:300])
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            retry_after = _retry_after_seconds(response.text, response.headers.get("Retry-After"))
            raise ModelTemporarilyUnavailable(
                f"OpenRouter:{model} temporarily unavailable HTTP {response.status_code}: {_short_debug_text(response.text, 220)}",
                cooldown_seconds=retry_after,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {_short_debug_text(response.text, 500)}") from exc
        payload = response.json()
        if "choices" not in payload:
            raise RuntimeError(f"OpenRouter response missing choices: {payload}")
        message = payload["choices"][0].get("message", {})
        content = _openrouter_message_content(message)
        if not content:
            raise RuntimeError(f"OpenRouter response missing content: {_short_debug_text(str(payload), 800)}")
        return content

    return generate


def _openrouter_message_content(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if text:
                    chunks.append(str(text))
            elif part:
                chunks.append(str(part))
        return "\n".join(chunks).strip()

    # Some free reasoning models occasionally put the final JSON in a sibling
    # field. We only use these fields when they actually contain a JSON object.
    for field in ("final", "output", "text"):
        value = message.get(field)
        if isinstance(value, str) and "{" in value and "}" in value:
            return value.strip()

    reasoning = message.get("reasoning")
    if isinstance(reasoning, str) and "{" in reasoning and "}" in reasoning:
        return reasoning.strip()

    return ""


def _generate_gemini_model(model: str) -> Callable[[str], str]:
    def generate(prompt: str) -> str:
        settings = get_settings()
        client = genai.Client(api_key=settings.gemini_api_key.strip())
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config={
                        "temperature": 0.7,
                        "max_output_tokens": 1200,
                        "response_mime_type": "application/json",
                    },
                )
                text = _gemini_response_text(response)
                if not text:
                    raise RuntimeError(f"Gemini response missing text: {_gemini_response_summary(response)}")
                return text
            except Exception as exc:
                last_exc = exc
                if _is_gemini_quota_error(exc):
                    raise GeminiQuotaExceeded(_gemini_quota_message(model, exc)) from exc
                if _is_temporary_gemini_error(exc):
                    raise ModelTemporarilyUnavailable(
                        f"{model} high demand/unavailable",
                        cooldown_seconds=_retry_after_seconds(str(exc), None),
                    ) from exc
                raise

        raise RuntimeError(f"Gemini failed: {last_exc}")

    return generate


def _gemini_response_text(response: object) -> str:
    text = getattr(response, "text", None)
    if text:
        return str(text)

    chunks: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(str(part_text))

    return "".join(chunks).strip()


def _gemini_response_summary(response: object) -> str:
    summaries: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        finish_reason = getattr(candidate, "finish_reason", None)
        safety_ratings = getattr(candidate, "safety_ratings", None)
        summaries.append(f"finish_reason={finish_reason}, safety_ratings={safety_ratings}")
    return "; ".join(summaries) or repr(response)[:500]


def _is_temporary_gemini_error(exc: Exception) -> bool:
    message = str(exc)
    return "503" in message or "UNAVAILABLE" in message or "high demand" in message


def _is_gemini_quota_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "RESOURCE_EXHAUSTED" in message
        or "generate_content_free_tier_requests" in message
        or "Quota exceeded" in message
        or "You exceeded your current quota" in message
    )


def _gemini_quota_message(model: str, exc: Exception) -> str:
    message = str(exc)
    retry_match = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)s", message)
    if not retry_match:
        retry_match = re.search(r"Please retry in ([\d.]+)s", message)
    retry_text = f"; retry after ~{retry_match.group(1)}s" if retry_match else ""

    limit_match = re.search(r"limit:\s*(\d+),\s*model:\s*([\w.\-]+)", message)
    if limit_match:
        return f"{model} quota exhausted ({limit_match.group(1)} free requests/day{retry_text})"
    return f"{model} quota exhausted{retry_text}"


def _retry_after_seconds(message: str, retry_after_header: str | None) -> int:
    if retry_after_header:
        try:
            return max(15, min(900, int(float(retry_after_header))))
        except ValueError:
            pass

    numeric_retry_patterns = [
        r"retryDelay['\"]?\s*:\s*['\"]?(\d+)s",
        r"Please retry in ([\d.]+)s",
    ]
    for pattern in numeric_retry_patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return max(15, min(900, int(float(match.group(1)))))

    keyword_retry_patterns = [
        r"retry shortly",
        r"temporarily rate-limited",
        r"high demand",
        r"UNAVAILABLE",
    ]
    for pattern in keyword_retry_patterns:
        if re.search(pattern, message, flags=re.IGNORECASE):
            return 180
    return 120


def _provider_on_cooldown(provider_name: str) -> int:
    now = time.time()
    until = _MODEL_COOLDOWNS.get(provider_name, 0)
    if until <= now:
        _MODEL_COOLDOWNS.pop(provider_name, None)
        return 0
    return int(until - now)


def _cooldown_provider(provider_name: str, seconds: int) -> None:
    _MODEL_COOLDOWNS[provider_name] = time.time() + max(15, min(900, seconds))


def _should_cooldown_after_output_error(exc: Exception) -> bool:
    message = str(exc)
    return any(
        phrase in message
        for phrase in [
            "response missing content",
            "response missing choices",
            "produced low-quality",
            "produced template",
            "produced dirty ragebait",
            "Invalid JSON",
        ]
    )


def _gemini_models() -> list[str]:
    settings = get_settings()
    return [
        model.strip()
        for model in settings.gemini_model.split(",")
        if model.strip()
    ] or ["gemini-2.5-flash-lite"]


def _product_style(style: str | None = None) -> str:
    base = (style or "").strip()
    if not base or base.lower() == "natural":
        return PRODUCT_CREATOR_STYLE
    if "product as prop" in base.lower() or "đời thường" in base.lower():
        return base
    return f"{base}; {PRODUCT_CREATOR_STYLE}"


def _generate_with_openai(prompt: str) -> str:
    settings = get_settings()
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"

    response = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {settings.openai_api_key.strip()}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.openai_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if "choices" not in payload:
        raise RuntimeError(f"OpenAI response missing choices: {payload}")
    return payload["choices"][0]["message"]["content"]


def _provider_generators() -> list[tuple[str, Callable[[str], str]]]:
    settings = get_settings()
    providers: list[tuple[str, Callable[[str], str]]] = []
    order = [
        provider.strip().lower()
        for provider in settings.ai_provider_order.split(",")
        if provider.strip()
    ] or ["openrouter", "gemini", "openai"]

    for provider in order:
        if provider == "gemini" and settings.gemini_api_key:
            for model in _gemini_models():
                providers.append((f"Gemini:{model}", _generate_gemini_model(model)))
        elif provider == "openrouter" and settings.openrouter_api_key:
            openrouter_models = [
                model.strip()
                for model in settings.openrouter_model.split(",")
                if model.strip()
            ] or [DEFAULT_OPENROUTER_MODEL]
            for model in openrouter_models:
                providers.append((f"OpenRouter:{model}", _generate_openrouter_model(model)))
        elif provider == "openai" and settings.openai_api_key:
            providers.append((f"OpenAI:{settings.openai_model}", _generate_with_openai))

    return providers


def model_status_snapshot() -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for provider_name, _ in _provider_generators():
        cooldown = _provider_on_cooldown(provider_name)
        rows.append(
            {
                "provider": provider_name,
                "status": "cooldown" if cooldown else "ready",
                "cooldown_seconds": cooldown,
            }
        )
    return rows


def check_model_availability(limit: int | None = None) -> list[dict[str, str | int]]:
    results: list[dict[str, str | int]] = []
    providers = _provider_generators()
    if limit is not None:
        providers = providers[: max(1, limit)]

    skip_openrouter_free = False
    for provider_name, generate in providers:
        if skip_openrouter_free and provider_name.startswith("OpenRouter:"):
            results.append({"provider": provider_name, "status": "skipped", "detail": "OpenRouter free daily limit already hit"})
            continue

        cooldown = _provider_on_cooldown(provider_name)
        if cooldown:
            results.append({"provider": provider_name, "status": "cooldown", "cooldown_seconds": cooldown})
            continue

        start = time.time()
        try:
            raw = generate(MODEL_CHECK_PROMPT)
            parsed = _soft_parse_json(raw)
            if str(parsed.get("content") or "").strip().lower() != "ok":
                raise RuntimeError(f"unexpected check response: {_short_debug_text(raw)}")
            results.append(
                {
                    "provider": provider_name,
                    "status": "ok",
                    "latency_ms": int((time.time() - start) * 1000),
                }
            )
        except OpenRouterFreeDailyLimitExceeded as exc:
            skip_openrouter_free = True
            results.append({"provider": provider_name, "status": "daily_limit", "detail": _short_debug_text(str(exc), 160)})
        except GeminiQuotaExceeded as exc:
            _cooldown_provider(provider_name, 900)
            results.append({"provider": provider_name, "status": "quota", "detail": str(exc)})
        except ModelTemporarilyUnavailable as exc:
            _cooldown_provider(provider_name, exc.cooldown_seconds)
            results.append({"provider": provider_name, "status": "temp_limited", "detail": _short_debug_text(str(exc), 160)})
        except Exception as exc:
            if _should_cooldown_after_output_error(exc):
                _cooldown_provider(provider_name, 300)
            results.append({"provider": provider_name, "status": "error", "detail": _short_debug_text(str(exc), 160)})

    return results


def _request_products(request: ThreadsDraftRequest) -> list[dict]:
    names = [
        re.sub(r"^\d+\.\s*", "", line).strip()
        for line in (request.product_name or request.keyword or "").splitlines()
        if line.strip()
    ]
    if not names:
        names = [request.product_name or request.keyword or "sản phẩm Shopee"]
    return [
        {
            "product_name": _remove_price_context(name),
            "affiliate_url": request.affiliate_url or "",
            "product_url": request.product_url or "",
            "price": request.price or "",
            "shop_name": request.shop_name or "",
            "commission_rate": request.commission_rate or "",
        }
        for name in names[:5]
    ]


def _metadata_from_engine_result(result: dict) -> dict:
    return {
        "need": str(result.get("need") or "")[:500],
        "persona": str(result.get("persona") or "")[:255],
        "angle": str(result.get("angle") or "")[:500],
        "hook_type": str(result.get("hook_type") or "")[:255],
        "story_type": str(result.get("story_type") or result.get("hook_type") or "")[:255],
        "target_platform": "threads",
    }


def _draft_from_engine_result(result: dict, request: ThreadsDraftRequest) -> ThreadsDraft:
    return _validate(
        {
            "content": result.get("content") or "",
            "cta": result.get("cta") or "",
            "hashtags": result.get("hashtags") or [],
            "quality_score": result.get("quality_score") or 70,
        },
        request,
    )


def generate_threads_shopee_content(db: Session, request: ThreadsDraftRequest) -> tuple[ThreadsDraft, dict]:
    keyword = (request.keyword or request.product_name or "sản phẩm Shopee").strip()
    products = _request_products(request)
    previous_posts_list = previous_similar_posts(db, keyword)
    context = analytics_context(db)
    local_result = generate_affiliate_content(keyword, products, previous_posts_list, context)
    payload = prompt_payload(keyword, products, previous_posts_list, context)
    prompt = (
        AFFILIATE_ENGINE_PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{keyword}", payload["keyword"])
        .replace("{products_json}", payload["products_json"])
        .replace("{previous_posts}", payload["previous_posts"])
        .replace("{analytics_context}", payload["analytics_context"])
        .replace("{target_platform}", payload["target_platform"])
        + "\n\nLOCAL PLAN TO FOLLOW, NOT COPY VERBATIM:\n"
        + json.dumps(payload["local_plan"], ensure_ascii=False)
        + "\n\n"
        + SHARED_CREATOR_DIRECTION
    )

    providers = _provider_generators()
    skip_openrouter_free = False
    last_error: Exception | None = None
    for provider_name, generate in providers:
        if skip_openrouter_free and provider_name.startswith("OpenRouter:"):
            continue
        cooldown_left = _provider_on_cooldown(provider_name)
        if cooldown_left:
            print(f"Threads Shopee agent {provider_name} cooldown active; skipping for ~{cooldown_left}s")
            continue
        try:
            raw = generate(prompt)
            parsed = _soft_parse_json(raw)
            if not str(parsed.get("content") or "").strip():
                raise RuntimeError(f"{provider_name} response missing content. Raw: {_short_debug_text(raw)}")
            draft = _validate(parsed, request)
            draft = _ensure_acceptable_draft(draft, request, provider_name)
            metadata = _metadata_from_engine_result({**local_result, **parsed})
            return draft, metadata
        except OpenRouterFreeDailyLimitExceeded as exc:
            last_error = exc
            skip_openrouter_free = True
            print(f"Threads Shopee agent {provider_name} free daily limit reached; skipping remaining OpenRouter free models: {exc}")
        except GeminiQuotaExceeded as exc:
            last_error = exc
            _cooldown_provider(provider_name, 900)
            print(f"Threads Shopee agent {provider_name} quota reached; trying next provider: {exc}")
        except ModelTemporarilyUnavailable as exc:
            last_error = exc
            _cooldown_provider(provider_name, exc.cooldown_seconds)
            print(f"Threads Shopee agent {provider_name} temporarily unavailable; trying next provider: {exc}")
        except Exception as exc:
            last_error = exc
            if _should_cooldown_after_output_error(exc):
                _cooldown_provider(provider_name, 300)
            print(f"Threads Shopee agent {provider_name} fallback: {exc}")

    print(f"Threads Shopee content engine local fallback used: {last_error}")
    draft = _draft_from_engine_result(local_result, request)
    try:
        draft = _ensure_acceptable_draft(draft, request, "local content engine")
    except Exception:
        draft = _fallback_draft(request)
    return draft, _metadata_from_engine_result(local_result)


def generate_threads_shopee_draft(db: Session, request: ThreadsDraftRequest) -> ThreadsDraft:
    draft, _metadata = generate_threads_shopee_content(db, request)
    return draft


def generate_threads_engagement_draft(
    db: Session,
    topic: str,
    style: str = "viral",
    persona: str = "daily",
) -> ThreadsDraft:
    clean_topic = _sanitize_engagement_topic((topic or "đời sống hằng ngày").strip())
    previous_posts = "\n---\n".join(previous_similar_posts(db, clean_topic)) or "None"
    persona_prompt = ENGAGEMENT_PERSONAS.get(persona, ENGAGEMENT_PERSONAS["daily"])

    prompt = (
        ENGAGEMENT_PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{topic}", clean_topic)
        .replace("{style}", style or "viral")
        .replace("{persona}", f"{persona_prompt}\n\n{SHARED_CREATOR_DIRECTION}")
        .replace("{previous_posts}", previous_posts)
    )

    providers = _provider_generators()
    request = ThreadsDraftRequest(keyword=clean_topic, product_name=clean_topic, style=style)
    skip_openrouter_free = False
    for provider_name, generate in providers:
        if skip_openrouter_free and provider_name.startswith("OpenRouter:"):
            continue
        cooldown_left = _provider_on_cooldown(provider_name)
        if cooldown_left:
            print(f"Threads engagement agent {provider_name} cooldown active; skipping for ~{cooldown_left}s")
            continue
        try:
            raw = generate(prompt)
            parsed = _soft_parse_json(raw)
            if not str(parsed.get("content") or "").strip():
                raise RuntimeError(f"{provider_name} response missing content. Raw: {_short_debug_text(raw)}")
            draft = _validate(parsed, request)
            draft = _ensure_acceptable_engagement_draft(draft, clean_topic, provider_name, style=style, persona=persona)
            return ThreadsDraft(
                content=_trim_complete_sentence(draft.content, 260),
                cta="",
                hashtags=draft.hashtags[:2],
                quality_score=draft.quality_score,
            )
        except OpenRouterFreeDailyLimitExceeded as exc:
            skip_openrouter_free = True
            print(f"Threads engagement agent {provider_name} free daily limit reached; skipping remaining OpenRouter free models: {exc}")
        except GeminiQuotaExceeded as exc:
            _cooldown_provider(provider_name, 900)
            print(f"Threads engagement agent {provider_name} quota reached; trying next provider: {exc}")
        except ModelTemporarilyUnavailable as exc:
            _cooldown_provider(provider_name, exc.cooldown_seconds)
            print(f"Threads engagement agent {provider_name} temporarily unavailable; trying next provider: {exc}")
        except Exception as exc:
            if _should_cooldown_after_output_error(exc):
                _cooldown_provider(provider_name, 300)
            print(f"Threads engagement agent {provider_name} fallback: {exc}")

    return _fallback_engagement_draft(clean_topic)
