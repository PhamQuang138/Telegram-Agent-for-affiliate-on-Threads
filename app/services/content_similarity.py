import re
import unicodedata
from difflib import SequenceMatcher


def _normalize(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _opening(text: str, length: int = 30) -> str:
    return _normalize(text)[:length]


def is_too_similar(new_content: str, previous_posts: list[str], threshold: float = 0.72) -> bool:
    normalized_new = _normalize(new_content)
    if not normalized_new:
        return True

    new_opening = _opening(new_content)
    for previous in previous_posts:
        normalized_previous = _normalize(previous)
        if not normalized_previous:
            continue
        if new_opening and new_opening == _opening(previous):
            return True
        if new_opening and SequenceMatcher(None, new_opening, _opening(previous)).ratio() >= 0.82:
            return True
        if SequenceMatcher(None, normalized_new, normalized_previous).ratio() >= threshold:
            return True
    return False
