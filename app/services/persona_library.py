import json
from pathlib import Path


FALLBACK_PERSONAS = [
    {
        "id": "daily_practical",
        "name": "Người sống thực tế",
        "tone": "đời thường, gọn, không phóng đại",
        "topics": ["đồ tiện ích", "đồ văn phòng", "outfit basic"],
        "avoid": ["nói quá", "emoji quá nhiều"],
    }
]


def load_personas() -> list[dict]:
    path = Path("data") / "personas.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return FALLBACK_PERSONAS
    return data if isinstance(data, list) and data else FALLBACK_PERSONAS


def select_persona(
    keyword: str | None,
    product: dict | None,
    trend_context: dict | None = None,
    analytics_context: dict | None = None,
) -> dict:
    personas = load_personas()
    text = " ".join(
        str(part)
        for part in [
            keyword or "",
            (product or {}).get("product_name") or "",
            (trend_context or {}).get("keyword") or "",
        ]
    ).lower()
    top_persona = _top_analytics_name(analytics_context or {})

    scored = []
    for persona in personas:
        score = 0
        topics = [str(topic).lower() for topic in persona.get("topics", [])]
        score += sum(8 for topic in topics if topic and topic in text)
        if top_persona and top_persona in {persona.get("id"), persona.get("name")}:
            score += 3
        scored.append((score, persona))

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score = scored[0][0] if scored else 0
    candidates = [persona for score, persona in scored if score == best_score] or personas
    return candidates[0]


def _top_analytics_name(context: dict) -> str:
    rows = context.get("personas") or []
    if not rows:
        return ""
    top = rows[0]
    if int(top.get("posts", 0) or 0) >= 4 and len(rows) > 1:
        return str(rows[1].get("name") or "")
    return str(top.get("name") or "")
