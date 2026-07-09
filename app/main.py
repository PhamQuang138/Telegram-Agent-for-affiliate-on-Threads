import threading

import uvicorn

from app.config import get_settings
from app.telegram_bot import build_application


def run_api() -> None:
    settings = get_settings()
    uvicorn.run("app.api:app", host="0.0.0.0", port=settings.tracking_port, log_level="info")


def main() -> None:
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    build_application().run_polling()


if __name__ == "__main__":
    main()
