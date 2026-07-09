from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH, override=True, encoding="utf-8-sig")


class Settings(BaseSettings):
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash-lite", alias="GEMINI_MODEL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(default="nvidia/nemotron-3-ultra-550b-a55b:free", alias="OPENROUTER_MODEL")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")
    ai_provider_order: str = Field(default="openrouter,gemini,openai", alias="AI_PROVIDER_ORDER")
    import_generate_limit: int = Field(default=2, alias="IMPORT_GENERATE_LIMIT")
    startup_import_csv_path: str = Field(default="", alias="STARTUP_IMPORT_CSV_PATH")
    startup_generate_limit: int = Field(default=2, alias="STARTUP_GENERATE_LIMIT")
    base_url: str = Field(default="http://localhost:8000", alias="BASE_URL")
    database_url: str = Field(default="sqlite:///./affiliate_agent.db", alias="DATABASE_URL")
    tracking_port: int = Field(default=8000, alias="TRACKING_PORT")
    threads_access_token: str = Field(default="", alias="THREADS_ACCESS_TOKEN")
    threads_user_id: str = Field(default="", alias="THREADS_USER_ID")
    threads_api_base_url: str = Field(default="https://graph.threads.net/v1.0", alias="THREADS_API_BASE_URL")
    include_tracking_link_in_threads: bool = Field(default=False, alias="INCLUDE_TRACKING_LINK_IN_THREADS")
    post_tracking_link_as_reply: bool = Field(default=True, alias="POST_TRACKING_LINK_AS_REPLY")
    comment_link_target: str = Field(default="affiliate", alias="COMMENT_LINK_TARGET")

    model_config = SettingsConfigDict(env_file=str(ENV_PATH), env_file_encoding="utf-8-sig", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
