from collections import Counter


def build_diversity_key(post: dict) -> str:
    return "|".join(
        str(post.get(field) or "unknown").lower().strip()
        for field in ["persona_id", "angle_id", "hook_type", "product_category"]
    )


def should_reduce_repetition(
    candidate: dict,
    recent_posts: list[dict],
    max_same_key_ratio: float = 0.35,
) -> dict:
    key = candidate.get("diversity_key") or build_diversity_key(candidate)
    if not recent_posts:
        return {"passed": True, "reason": "no recent posts", "repetition_ratio": 0.0}

    keys = [post.get("diversity_key") or build_diversity_key(post) for post in recent_posts]
    ratio = Counter(keys)[key] / max(1, len(keys))
    return {
        "passed": ratio <= max_same_key_ratio,
        "reason": "ok" if ratio <= max_same_key_ratio else "too many recent posts share this persona/angle/hook",
        "repetition_ratio": round(ratio, 3),
    }
