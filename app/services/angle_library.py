import json
from pathlib import Path


FALLBACK_ANGLES = [
    {
        "id": "problem_solution",
        "name": "Vấn đề nhỏ -> gợi ý sản phẩm",
        "description": "Dựng tình huống phiền nhẹ rồi nhắc sản phẩm tự nhiên.",
        "best_for": ["đồ tiện ích", "đồ văn phòng"],
    }
]


def load_angles() -> list[dict]:
    path = Path("data") / "angles.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return FALLBACK_ANGLES
    return data if isinstance(data, list) and data else FALLBACK_ANGLES


def select_angle(
    keyword: str | None,
    product: dict | None,
    persona: dict | None,
    analytics_context: dict | None = None,
) -> dict:
    angles = load_angles()
    text = " ".join(
        str(part)
        for part in [
            keyword or "",
            (product or {}).get("product_name") or "",
            " ".join((persona or {}).get("topics", [])),
        ]
    ).lower()
    top_angle = _top_analytics_name(analytics_context or {})

    scored = []
    for angle in angles:
        score = 0
        best_for = [str(item).lower() for item in angle.get("best_for", [])]
        score += sum(8 for item in best_for if item and item in text)
        if top_angle and top_angle in {angle.get("id"), angle.get("name")}:
            score += 3
        scored.append((score, angle))

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored else FALLBACK_ANGLES[0]


def _top_analytics_name(context: dict) -> str:
    rows = context.get("angles") or []
    if not rows:
        return ""
    top = rows[0]
    if int(top.get("posts", 0) or 0) >= 4 and len(rows) > 1:
        return str(rows[1].get("name") or "")
    return str(top.get("name") or "")
