from functools import lru_cache
from typing import List

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    quotex_email: str = Field(default="", alias="QUOTEX_EMAIL")
    quotex_password: str = Field(default="", alias="QUOTEX_PASSWORD")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    markets: str = Field(default="EURUSD_otc,GBPUSD_otc,USDJPY_otc", alias="MARKETS")
    default_timeframe: int = Field(default=60, alias="DEFAULT_TIMEFRAME")
    poll_interval_seconds: float = Field(default=2.0, alias="POLL_INTERVAL_SECONDS")
    candle_count: int = Field(default=80, alias="CANDLE_COUNT")
    signal_cooldown_seconds: int = Field(default=45, alias="SIGNAL_COOLDOWN_SECONDS")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    @field_validator("markets", mode="before")
    @classmethod
    def parse_markets(cls, value: object) -> str:
        if isinstance(value, list):
            return ",".join(str(market).strip() for market in value if str(market).strip())
        if isinstance(value, str):
            return value
        return "EURUSD_otc,GBPUSD_otc,USDJPY_otc"

    @field_validator("default_timeframe")
    @classmethod
    def validate_timeframe(cls, value: int) -> int:
        allowed = {30, 45, 60, 120, 180, 300}
        return value if value in allowed else 60

    @property
    def market_list(self) -> List[str]:
        return [market.strip() for market in self.markets.split(",") if market.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
