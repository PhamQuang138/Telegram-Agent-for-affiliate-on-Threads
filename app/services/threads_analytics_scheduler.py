from __future__ import annotations

import threading
import time

from app.config import get_settings
from app.services.learning_engine import update_account_learning_profile
from app.services.threads_account_service import load_threads_accounts
from app.services.threads_insights_service import sync_account_insights
from app.services.threads_reply_service import sync_account_replies

_LOCK = threading.Lock()
_STARTED = False


def start_background_sync() -> bool:
    global _STARTED
    settings = get_settings()
    if not settings.threads_analytics_sync_enabled and not settings.threads_replies_sync_enabled:
        return False
    if _STARTED:
        return False
    _STARTED = True
    thread = threading.Thread(target=_loop, name="threads-analytics-sync", daemon=True)
    thread.start()
    return True


def run_sync_once() -> dict:
    if not _LOCK.acquire(blocking=False):
        return {"started": False, "reason": "sync already running"}
    try:
        settings = get_settings()
        results = []
        for account in load_threads_accounts():
            if not account.get("enabled"):
                continue
            item = {"account_name": account["name"]}
            if settings.threads_analytics_sync_enabled:
                item["insights"] = sync_account_insights(account["name"], limit=50)
            if settings.threads_replies_sync_enabled:
                item["replies"] = sync_account_replies(account["name"], limit_posts=30)
            try:
                item["learning"] = update_account_learning_profile(
                    account["name"],
                    min_posts=settings.threads_learning_min_posts,
                    lookback_days=settings.threads_insights_lookback_days,
                )
            except Exception as exc:
                item["learning_error"] = str(exc)
            results.append(item)
        return {"started": True, "accounts": results}
    finally:
        _LOCK.release()


def _loop() -> None:
    settings = get_settings()
    interval = max(5, settings.threads_analytics_sync_interval_minutes) * 60
    while True:
        time.sleep(interval)
        try:
            run_sync_once()
        except Exception as exc:
            print(f"Threads analytics sync skipped after error: {exc}")
