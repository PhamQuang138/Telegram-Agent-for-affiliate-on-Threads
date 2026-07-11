from __future__ import annotations

from app.config import get_settings

DEFAULT_FLAGS: dict[str, bool] = {
    "daily_link_catalog": True,
    "threads_engagement_posts": True,
    "telegram_group": True,
    "telegram_channel": False,
    "daily_link_auto_cleanup": True,
    "demand_scanner": False,
    "manual_demand_intake": False,
    "purchase_intent": False,
    "product_matcher": False,
    "comment_generator": False,
    "opportunity_queue": False,
    "approval_queue": False,
    "threads_publisher": True,
    "manual_copy_mode": True,
    "basic_analytics": True,
    "content_engine": True,
    "engagement_posts": True,
    "learning_engine": False,
    "topic_memory": False,
    "persona_optimizer": False,
    "hook_optimizer": False,
    "cta_optimizer": False,
    "diversity_engine": False,
    "marketplace_intelligence": False,
    "trend_fusion": False,
    "google_trends": False,
    "threads_trend_provider": False,
    "instagram_provider": False,
    "facebook_provider": False,
    "x_provider": False,
    "reddit_provider": False,
    "cross_platform_publisher": False,
    "threads_background_sync": False,
    "threads_auto_scanner": False,
}


def is_feature_enabled(name: str) -> bool:
    key = _normalize(name)
    settings = get_settings()
    attr = f"enable_{key}"
    if hasattr(settings, attr):
        return bool(getattr(settings, attr))
    return DEFAULT_FLAGS.get(key, False)


def feature_snapshot() -> dict:
    enabled = []
    frozen = []
    for name in sorted(DEFAULT_FLAGS):
        if is_feature_enabled(name):
            enabled.append(name)
        else:
            frozen.append(name)
    return {"enabled": enabled, "frozen": frozen}


def _normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_").removeprefix("enable_")
