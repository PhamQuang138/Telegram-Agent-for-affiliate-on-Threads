from __future__ import annotations

import argparse

import httpx

from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Set Telegram webhook for POD Bot.")
    parser.add_argument("--url", default="", help="Webhook URL. Defaults to TELEGRAM_WEBHOOK_URL.")
    parser.add_argument("--secret", default="", help="Secret token. Defaults to TELEGRAM_WEBHOOK_SECRET.")
    parser.add_argument("--drop-pending-updates", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    token = settings.telegram_bot_token
    url = args.url or settings.telegram_webhook_url
    secret = args.secret or settings.telegram_webhook_secret
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required.")
    if not url:
        raise SystemExit("TELEGRAM_WEBHOOK_URL or --url is required.")

    payload = {
        "url": url,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": args.drop_pending_updates,
    }
    if secret:
        payload["secret_token"] = secret

    response = httpx.post(f"https://api.telegram.org/bot{token}/setWebhook", json=payload, timeout=30)
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
