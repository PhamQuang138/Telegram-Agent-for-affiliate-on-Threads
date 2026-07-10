import threading

import uvicorn

from app.config import get_settings
from app.services.threads_analytics_scheduler import start_background_sync
from app.telegram_bot import build_application


def run_api() -> None:
    settings = get_settings()
    uvicorn.run("app.api:app", host="0.0.0.0", port=settings.tracking_port, log_level="info")


def main() -> None:
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    start_background_sync()
    build_application().run_polling()


if __name__ == "__main__":
    main()
