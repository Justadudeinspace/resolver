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
    use_llm_env: bool = Field(default=False, alias="USE_LLM")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.4, alias="LLM_TEMPERATURE")

    # Rate limiting
    rate_limit_per_user: int = Field(default=5, alias="RATE_LIMIT_PER_USER")
    max_input_length: int = Field(default=2000, alias="MAX_INPUT_LENGTH")

    # Payments security
    invoice_secret: str = Field(default="", alias="INVOICE_SECRET")

    # Feature flags
    feature_v2_personal: bool = Field(default=False, alias="FEATURE_V2_PERSONAL")
    feature_v2_groups: bool = Field(default=False, alias="FEATURE_V2_GROUPS")

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

    _placeholder_markers = ("CHANGE_ME", "YOUR_SECRET_HERE", "REPLACE_ME", "INSERT_SECRET", "EXAMPLE")

    def _is_placeholder(self, value: str) -> bool:
        return any(marker in value for marker in self._placeholder_markers)

    @property
    def bot_token_valid(self) -> bool:
        return bool(self.bot_token and not self._is_placeholder(self.bot_token))

    @property
    def invoice_secret_valid(self) -> bool:
        return bool(
            self.invoice_secret
            and len(self.invoice_secret) >= 32
            and not self._is_placeholder(self.invoice_secret)
        )

    @property
    def use_llm(self) -> bool:
        """Check if LLM should be used (requires env flag and API key)."""
        return bool(self.use_llm_env and self.openai_api_key)


settings = Settings()
