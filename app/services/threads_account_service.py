from __future__ import annotations

import os
from itertools import count

from app.config import get_settings

_ROUND_ROBIN = count()


class ThreadsAccountError(RuntimeError):
    pass


def load_threads_accounts() -> list[dict]:
    accounts_env = os.getenv("THREADS_ACCOUNTS", "").strip()
    accounts: list[dict] = []

    if accounts_env:
        for raw_name in accounts_env.split(","):
            name = raw_name.strip()
            if not name:
                continue
            prefix = f"THREADS_{name.upper()}_"
            accounts.append(
                _account(
                    name=name,
                    display_name=os.getenv(prefix + "NAME", name),
                    user_id=os.getenv(prefix + "USER_ID", ""),
                    access_token=os.getenv(prefix + "ACCESS_TOKEN", ""),
                    persona=os.getenv(prefix + "PERSONA", ""),
                    topics=os.getenv(prefix + "TOPICS", ""),
                    source="named",
                )
            )
        return accounts

    settings = get_settings()
    token_2 = os.getenv("THREADS_ACCESS_TOKEN_2", "").strip()
    user_id_2 = os.getenv("THREADS_USER_ID_2", "").strip()
    legacy_name = "acc1" if token_2 or user_id_2 else "default"
    legacy = _account(
        name=legacy_name,
        display_name=os.getenv("THREADS_ACC1_NAME", legacy_name),
        user_id=os.getenv("THREADS_ACC1_USER_ID", settings.threads_user_id),
        access_token=os.getenv("THREADS_ACC1_ACCESS_TOKEN", settings.threads_access_token),
        persona=os.getenv("THREADS_ACC1_PERSONA", os.getenv("THREADS_PERSONA", "")),
        topics=os.getenv("THREADS_ACC1_TOPICS", os.getenv("THREADS_TOPICS", "")),
        source="legacy",
    )
    accounts.append(legacy)

    if token_2 or user_id_2:
        accounts.append(
            _account(
                name="acc2",
                display_name=os.getenv("THREADS_ACC2_NAME", os.getenv("THREADS_USERNAME_2", "acc2")),
                user_id=os.getenv("THREADS_ACC2_USER_ID", user_id_2),
                access_token=os.getenv("THREADS_ACC2_ACCESS_TOKEN", token_2),
                persona=os.getenv("THREADS_ACC2_PERSONA", os.getenv("THREADS_PERSONA_2", "")),
                topics=os.getenv("THREADS_ACC2_TOPICS", os.getenv("THREADS_TOPICS_2", "")),
                source="legacy_2",
            )
        )

    return accounts


def get_threads_account(account_name: str | None = None) -> dict:
    accounts = load_threads_accounts()
    valid = [account for account in accounts if account["enabled"]]
    if not valid:
        raise ThreadsAccountError("No valid Threads account configured. Set THREADS_ACCESS_TOKEN/THREADS_USER_ID or THREADS_ACCOUNTS.")

    if account_name:
        wanted = account_name.strip().lower()
        for account in accounts:
            if account["name"].lower() == wanted:
                if not account["enabled"]:
                    raise ThreadsAccountError(f"Threads account '{account_name}' is missing access_token or user_id.")
                return account
        raise ThreadsAccountError(f"Threads account '{account_name}' not found.")

    return valid[next(_ROUND_ROBIN) % len(valid)]


def select_account_for_post(post: dict, accounts: list[dict]) -> dict:
    valid = [account for account in accounts if account.get("enabled")]
    if not valid:
        raise ThreadsAccountError("No valid Threads account configured.")

    text = " ".join(
        str(part or "")
        for part in [
            post.get("keyword"),
            post.get("product_name"),
            post.get("persona"),
            post.get("persona_id"),
            post.get("angle"),
            post.get("content"),
        ]
    ).lower()

    scored: list[tuple[int, dict]] = []
    for account in valid:
        score = 0
        persona = str(account.get("persona") or "").lower()
        topics = [str(topic).lower() for topic in account.get("topics", [])]
        if persona and persona in text:
            score += 6
        score += sum(3 for topic in topics if topic and topic in text)
        scored.append((score, account))

    best_score = max(score for score, _account_item in scored)
    if best_score > 0:
        best = [account for score, account in scored if score == best_score]
        return best[next(_ROUND_ROBIN) % len(best)]

    return valid[next(_ROUND_ROBIN) % len(valid)]


def _account(
    *,
    name: str,
    display_name: str,
    user_id: str,
    access_token: str,
    persona: str,
    topics: str,
    source: str,
) -> dict:
    clean_topics = [topic.strip() for topic in topics.split(",") if topic.strip()]
    return {
        "name": name,
        "display_name": display_name or name,
        "user_id": (user_id or "").strip(),
        "access_token": (access_token or "").strip(),
        "persona": (persona or "").strip(),
        "topics": clean_topics,
        "enabled": bool((user_id or "").strip() and (access_token or "").strip()),
        "source": source,
    }
