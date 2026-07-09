import re

from app.services.content_similarity import is_too_similar


SPAM_WORDS = {
    "mua ngay",
    "sale sốc",
    "sale soc",
    "cam kết",
    "cam ket",
    "tốt nhất",
    "tot nhat",
    "siêu hot",
    "sieu hot",
    "100%",
}

FAKE_CLAIMS = {
    "mình đã mua",
    "minh da mua",
    "mình dùng rồi",
    "minh dung roi",
    "test thử",
    "test thu",
    "nhận hàng",
    "nhan hang",
    "mới mua hôm qua",
    "moi mua hom qua",
}


def _fold(text: str) -> str:
    return text.lower()


def evaluate_content(content: str, product_names: list[str], previous_posts: list[str]) -> dict:
    issues: list[str] = []
    clean = re.sub(r"\s+", " ", content or "").strip()
    folded = _fold(clean)

    if len(clean) < 100:
        issues.append("too_short")
    if len(clean) > 350:
        issues.append("too_long")
    if "shopee.vn" in folded or "s.shopee.vn" in folded:
        issues.append("raw_shopee_link")
    if any(word in folded for word in SPAM_WORDS):
        issues.append("spam_language")
    if any(claim in folded for claim in FAKE_CLAIMS):
        issues.append("unsupported_fake_claim")
    if is_too_similar(clean, previous_posts):
        issues.append("too_similar")
    if product_names and clean.count("\n") > 3:
        issues.append("too_list_like")

    score = max(0, 100 - len(issues) * 18)
    return {"score": score, "issues": issues, "passed": score >= 70 and not issues}
