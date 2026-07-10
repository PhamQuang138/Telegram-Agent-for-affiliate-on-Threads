from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AppSetting, ThreadsPost

WEIGHTS_PATH = Path("data") / "learned_weights.json"
WEIGHT_GROUPS = ["personas", "angles", "hook_types", "topics", "accounts"]
SETTING_AUTO_LEARNING = "auto_learning_enabled"
SETTING_LAST_LEARNING_RUN = "last_learning_run_at"


def build_learning_profile(min_posts: int = 10, lookback_days: int = 30) -> dict:
    rows = _posted_posts(lookback_days)
    total_posts = len(rows)
    profile = {
        "enough_data": total_posts >= min_posts,
        "total_posts": total_posts,
        "top_personas": [],
        "weak_personas": [],
        "top_angles": [],
        "weak_angles": [],
        "top_hook_types": [],
        "weak_hook_types": [],
        "top_topics": [],
        "weak_topics": [],
        "top_accounts": [],
        "recommendations": [],
        "updated_weights": {group: {} for group in WEIGHT_GROUPS},
    }
    if not profile["enough_data"]:
        profile["recommendations"].append(f"Need at least {min_posts} posted posts; current sample is {total_posts}.")
        return profile

    groups = {
        "personas": _rank_groups(rows, lambda post: post.persona_id or post.persona),
        "angles": _rank_groups(rows, lambda post: post.angle_id or post.angle),
        "hook_types": _rank_groups(rows, lambda post: post.hook_type),
        "topics": _rank_groups(rows, lambda post: post.keyword),
        "accounts": _rank_groups(rows, lambda post: post.posted_account_name),
    }
    for key, ranked in groups.items():
        if key == "personas":
            profile["top_personas"] = ranked[:5]
            profile["weak_personas"] = _weak_groups(ranked, profile["top_personas"])
        elif key == "angles":
            profile["top_angles"] = ranked[:5]
            profile["weak_angles"] = _weak_groups(ranked, profile["top_angles"])
        elif key == "hook_types":
            profile["top_hook_types"] = ranked[:5]
            profile["weak_hook_types"] = _weak_groups(ranked, profile["top_hook_types"])
        elif key == "topics":
            profile["top_topics"] = ranked[:5]
            profile["weak_topics"] = _weak_groups(ranked, profile["top_topics"])
        elif key == "accounts":
            profile["top_accounts"] = ranked[:5]

    if profile["top_personas"]:
        profile["recommendations"].append(f"Prefer persona {profile['top_personas'][0]['name']} lightly.")
    if profile["weak_hook_types"]:
        profile["recommendations"].append(f"Reduce hook type {profile['weak_hook_types'][0]['name']} if it keeps underperforming.")
    profile["updated_weights"] = _weight_delta_from_profile(profile)
    return profile


def update_learned_weights(min_posts: int = 10, lookback_days: int = 30) -> dict:
    profile = build_learning_profile(min_posts=min_posts, lookback_days=lookback_days)
    if not profile["enough_data"]:
        return profile

    current = load_learned_weights()
    for group, deltas in profile["updated_weights"].items():
        current.setdefault(group, {})
        for name, delta in deltas.items():
            current[group][name] = _clamp(float(current[group].get(name, 1.0)) + delta)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    current["sample_size"] = profile["total_posts"]
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**profile, "learned_weights": current}


def load_learned_weights() -> dict:
    base = {"updated_at": "", "sample_size": 0, **{group: {} for group in WEIGHT_GROUPS}}
    if not WEIGHTS_PATH.exists():
        return base
    try:
        data = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = WEIGHTS_PATH.with_suffix(".bak")
        shutil.copyfile(WEIGHTS_PATH, backup)
        return base
    if not isinstance(data, dict):
        return base
    for group in WEIGHT_GROUPS:
        data.setdefault(group, {})
    data.setdefault("updated_at", "")
    data.setdefault("sample_size", 0)
    return data


def compact_learning_profile() -> dict:
    profile = build_learning_profile(min_posts=10, lookback_days=30)
    return {
        "prefer_personas": [item["name"] for item in profile["top_personas"][:3]],
        "avoid_personas": [item["name"] for item in profile["weak_personas"][:3]],
        "prefer_angles": [item["name"] for item in profile["top_angles"][:3]],
        "avoid_angles": [item["name"] for item in profile["weak_angles"][:3]],
        "prefer_hook_types": [item["name"] for item in profile["top_hook_types"][:3]],
        "avoid_hook_types": [item["name"] for item in profile["weak_hook_types"][:3]],
    }


def get_app_setting(key: str, default: str = "") -> str:
    with SessionLocal() as db:
        row = db.get(AppSetting, key)
        return row.value if row else default


def set_app_setting(key: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as db:
        row = db.get(AppSetting, key)
        if row:
            row.value = value
            row.updated_at = now
        else:
            db.add(AppSetting(key=key, value=value, updated_at=now))
        db.commit()


def auto_learning_enabled() -> bool:
    return get_app_setting(SETTING_AUTO_LEARNING, "off").lower() == "on"


def maybe_run_auto_learning(min_posts: int = 10, lookback_days: int = 30) -> dict | None:
    if not auto_learning_enabled():
        return None
    with SessionLocal() as db:
        total_posted = len(list(db.scalars(select(ThreadsPost).where(ThreadsPost.status == "posted"))))
    if total_posted < min_posts or total_posted % 10 != 0:
        return None
    last = get_app_setting(SETTING_LAST_LEARNING_RUN, "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if datetime.now(timezone.utc) - last_dt < timedelta(hours=6):
                return None
        except ValueError:
            pass
    result = update_learned_weights(min_posts=min_posts, lookback_days=lookback_days)
    set_app_setting(SETTING_LAST_LEARNING_RUN, datetime.now(timezone.utc).isoformat())
    return result


def learning_status() -> dict:
    weights = load_learned_weights()
    return {
        "auto_learning_enabled": auto_learning_enabled(),
        "last_learning_run_at": get_app_setting(SETTING_LAST_LEARNING_RUN, ""),
        "sample_size": int(weights.get("sample_size") or 0),
        "updated_at": weights.get("updated_at", ""),
        "weights": weights,
    }


def _posted_posts(lookback_days: int) -> list[ThreadsPost]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(ThreadsPost)
                .where(ThreadsPost.status == "posted", ThreadsPost.created_at >= cutoff)
                .order_by(ThreadsPost.id.desc())
            )
        )


def _rank_groups(rows: list[ThreadsPost], key_fn) -> list[dict]:
    buckets: dict[str, list[ThreadsPost]] = defaultdict(list)
    for row in rows:
        key = str(key_fn(row) or "").strip()
        if key:
            buckets[key].append(row)
    ranked = []
    for name, items in buckets.items():
        if len(items) < 2:
            continue
        clicks = [int(item.click_count or 0) for item in items]
        avg_clicks = sum(clicks) / len(clicks)
        max_clicks = max(clicks)
        consistency = sum(1 for click in clicks if click > 0) / len(clicks)
        score = avg_clicks * 0.6 + max_clicks * 0.2 + consistency * 0.2
        ranked.append(
            {
                "name": name,
                "posts": len(items),
                "avg_clicks": round(avg_clicks, 3),
                "max_clicks": max_clicks,
                "consistency_score": round(consistency, 3),
                "score": round(score, 3),
            }
        )
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def _weak_groups(ranked: list[dict], top: list[dict]) -> list[dict]:
    top_names = {item["name"] for item in top[:1]}
    bottom = [item for item in reversed(ranked) if item["name"] not in top_names]
    return bottom[:5]


def _weight_delta_from_profile(profile: dict) -> dict:
    mapping = {
        "personas": ("top_personas", "weak_personas"),
        "angles": ("top_angles", "weak_angles"),
        "hook_types": ("top_hook_types", "weak_hook_types"),
        "topics": ("top_topics", "weak_topics"),
        "accounts": ("top_accounts", ""),
    }
    deltas = {group: {} for group in WEIGHT_GROUPS}
    for group, (top_key, weak_key) in mapping.items():
        for item in profile.get(top_key, [])[:3]:
            deltas[group][item["name"]] = 0.15
        if weak_key:
            for item in profile.get(weak_key, [])[:3]:
                deltas[group][item["name"]] = min(deltas[group].get(item["name"], 0), -0.10)
    return deltas


def _clamp(value: float) -> float:
    return round(max(0.5, min(1.8, value)), 3)
