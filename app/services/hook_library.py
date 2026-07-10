import json
import random
from pathlib import Path

from app.services.learning_engine import load_learned_weights


FALLBACK_HOOKS = {
    "observation": ["Có mấy chuyện nhỏ mà gặp mỗi ngày là đủ mệt..."],
    "question": ["Có ai cũng hay đau đầu vì chuyện này không..."],
    "confession": ["Thú nhận là mình hơi dễ cáu với mấy chuyện lặt vặt..."],
    "problem": ["Vấn đề không lớn, nhưng gặp mỗi ngày thì đủ phiền..."],
}


def load_hooks() -> dict:
    path = Path("data") / "hooks.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return FALLBACK_HOOKS
    return data if isinstance(data, dict) and data else FALLBACK_HOOKS


def choose_hook(hook_type: str | None = None, avoid: list[str] | None = None) -> dict:
    hooks = load_hooks()
    avoid = [item.lower().strip() for item in (avoid or []) if item.strip()]
    weights = load_learned_weights().get("hook_types", {})
    selected_type = hook_type if hook_type in hooks else _weighted_hook_type(list(hooks.keys()), weights)
    candidates = [hook for hook in hooks.get(selected_type, []) if hook.lower().strip() not in avoid]
    if not candidates:
        selected_type = _weighted_hook_type(list(hooks.keys()), weights)
        candidates = hooks.get(selected_type, []) or FALLBACK_HOOKS["observation"]
    return {"hook": random.choice(candidates), "hook_type": selected_type}


def _weighted_hook_type(types: list[str], weights: dict) -> str:
    if not types:
        return "observation"
    weighted = [(hook_type, max(0.1, float(weights.get(hook_type, 1.0)))) for hook_type in types]
    total = sum(weight for _hook_type, weight in weighted)
    pick = random.random() * total
    running = 0.0
    for hook_type, weight in weighted:
        running += weight
        if running >= pick:
            return hook_type
    return types[0]
