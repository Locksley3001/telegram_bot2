from functools import lru_cache
from typing import List

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(encoding="utf-8-sig")


class Settings(BaseSettings):
    iq_option_email: str = Field(default="", alias="IQ_OPTION_EMAIL")
    iq_option_password: str = Field(default="", alias="IQ_OPTION_PASSWORD")
    # CONFIGURACION_MANUAL_REQUERIDA: solo completar si IQ Option pide 2FA/SMS.
    iq_option_2fa_code: str = Field(default="", alias="IQ_OPTION_2FA_CODE")
    iq_option_balance_mode: str = Field(default="PRACTICE", alias="IQ_OPTION_BALANCE_MODE")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    markets: str = Field(
        default="EURUSD-OTC,GBPUSD-OTC,USDJPY-OTC,BTCUSD-OTC,ETHUSD-OTC,NVDA/AMD-OTC,SOLUSD-OTC",
        alias="MARKETS",
    )
    default_timeframe: int = Field(default=60, alias="DEFAULT_TIMEFRAME")
    poll_interval_seconds: float = Field(default=0.75, alias="POLL_INTERVAL_SECONDS")
    candle_count: int = Field(default=80, alias="CANDLE_COUNT")
    signal_cooldown_seconds: int = Field(default=45, alias="SIGNAL_COOLDOWN_SECONDS")
    data_dir: str = Field(default="data", alias="DATA_DIR")
    signal_history_limit: int = Field(default=500, alias="SIGNAL_HISTORY_LIMIT")
    api_signal_limit: int = Field(default=500, alias="API_SIGNAL_LIMIT")
    learning_enabled: bool = Field(default=True, alias="LEARNING_ENABLED")
    learning_min_history: int = Field(default=30, alias="LEARNING_MIN_HISTORY")
    learning_min_win_rate: float = Field(default=58.0, alias="LEARNING_MIN_WIN_RATE")
    learning_min_rule_samples: int = Field(default=5, alias="LEARNING_MIN_RULE_SAMPLES")
    learning_min_similarity_samples: int = Field(default=4, alias="LEARNING_MIN_SIMILARITY_SAMPLES")
    learning_exploration_interval: int = Field(default=20, alias="LEARNING_EXPLORATION_INTERVAL")
    broker_trading_enabled: bool = Field(default=False, alias="BROKER_TRADING_ENABLED")
    broker_trade_entry_window_seconds: float = Field(default=3.0, alias="BROKER_TRADE_ENTRY_WINDOW_SECONDS")
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_service_key: str = Field(default="", alias="SUPABASE_SERVICE_KEY")
    supabase_generic_key: str = Field(default="", alias="SUPABASE_KEY")
    supabase_anon_key: str = Field(default="", alias="SUPABASE_ANON_KEY")
    supabase_state_enabled: bool = Field(default=True, alias="SUPABASE_STATE_ENABLED")
    supabase_state_table: str = Field(default="bot_state_files", alias="SUPABASE_STATE_TABLE")
    supabase_versions_table: str = Field(default="bot_state_file_versions", alias="SUPABASE_VERSIONS_TABLE")
    supabase_bootstrap_local: bool = Field(default=False, alias="SUPABASE_BOOTSTRAP_LOCAL")
    supabase_remote_save_interval_seconds: float = Field(default=60.0, alias="SUPABASE_REMOTE_SAVE_INTERVAL_SECONDS")
    supabase_versioning_enabled: bool = Field(default=False, alias="SUPABASE_VERSIONING_ENABLED")
    supabase_version_interval_seconds: float = Field(default=3600.0, alias="SUPABASE_VERSION_INTERVAL_SECONDS")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    @field_validator("markets", mode="before")
    @classmethod
    def parse_markets(cls, value: object) -> str:
        if isinstance(value, list):
            return ",".join(str(market).strip() for market in value if str(market).strip())
        if isinstance(value, str):
            return value
        return "EURUSD-OTC,GBPUSD-OTC,USDJPY-OTC,BTCUSD-OTC,ETHUSD-OTC,NVDA/AMD-OTC,SOLUSD-OTC"

    @field_validator("default_timeframe")
    @classmethod
    def validate_timeframe(cls, value: int) -> int:
        allowed = {30, 45, 60, 120, 180, 300}
        return value if value in allowed else 60

    @property
    def market_list(self) -> List[str]:
        return [market.strip() for market in self.markets.split(",") if market.strip()]

    @property
    def supabase_key(self) -> str:
        return (
            self.supabase_service_role_key
            or self.supabase_service_key
            or self.supabase_generic_key
            or self.supabase_anon_key
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
