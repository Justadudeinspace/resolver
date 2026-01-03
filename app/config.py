from typing import Optional
import logging
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram
    bot_token: str = Field(default="", alias="BOT_TOKEN")

    # Database
    db_path: str = Field(default="./data/resolver.sqlite3", alias="DB_PATH")

    # LLM (optional)
    use_llm_env: bool = Field(default=True, alias="USE_LLM")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    llm_model: str = Field(default="gpt-4.1-mini", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.4, alias="LLM_TEMPERATURE")

    # Rate limiting
    rate_limit_per_user: int = Field(default=5, alias="RATE_LIMIT_PER_USER")
    max_input_length: int = Field(default=2000, alias="MAX_INPUT_LENGTH")

    # Payments security
    invoice_secret: str = Field(default="", alias="INVOICE_SECRET")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        level = (value or "INFO").upper()
        if level not in valid_levels:
            logging.getLogger(__name__).warning(
                "Invalid LOG_LEVEL '%s', defaulting to INFO", value
            )
            return "INFO"
        return level

    @property
    def use_llm(self) -> bool:
        """Check if LLM should be used (requires env flag and API key)."""
        return bool(self.use_llm_env and self.openai_api_key)


settings = Settings()
