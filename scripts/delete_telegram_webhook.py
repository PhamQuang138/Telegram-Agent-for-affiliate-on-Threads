from __future__ import annotations

import argparse

import httpx

from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete Telegram webhook for local polling.")
    parser.add_argument("--drop-pending-updates", action="store_true")
    args = parser.parse_args()

    token = get_settings().telegram_bot_token
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required.")

    response = httpx.post(
        f"https://api.telegram.org/bot{token}/deleteWebhook",
        json={"drop_pending_updates": args.drop_pending_updates},
        timeout=30,
    )
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
