from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: SecretStr
    demo_mode: bool = False
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str | None = None
    bitrix24_webhook_url: SecretStr | None = None
    bitrix24_entity_mode: Literal["auto", "lead", "deal"] = "auto"
    bitrix24_deal_stage: str = "NEW"
    bitrix24_deal_category_id: int | None = None
    bitrix24_source_id: str | None = None
    database_path: str = "data/bot.db"
    dialogue_history_limit: int = Field(default=12, ge=2, le=50)
    request_timeout_seconds: float = Field(default=30, gt=0, le=120)
    log_level: str = "INFO"

    @field_validator("bitrix24_webhook_url")
    @classmethod
    def normalize_webhook_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        url = value.get_secret_value().strip()
        if not url.startswith(("https://", "http://")):
            raise ValueError("BITRIX24_WEBHOOK_URL must be an HTTP(S) URL")
        if not url.endswith("/"):
            url += "/"
        return SecretStr(url)

    @field_validator("bitrix24_deal_category_id", "bitrix24_source_id", mode="before")
    @classmethod
    def empty_optional_setting_as_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("openai_base_url")
    @classmethod
    def empty_base_url_as_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def require_production_credentials(self) -> "Settings":
        if not self.demo_mode:
            missing = []
            if self.openai_api_key is None:
                missing.append("OPENAI_API_KEY")
            if self.bitrix24_webhook_url is None:
                missing.append("BITRIX24_WEBHOOK_URL")
            if missing:
                raise ValueError(
                    "Required when DEMO_MODE=false: " + ", ".join(missing)
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
