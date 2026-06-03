from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional

from app.models import Candle

LOGGER = logging.getLogger(__name__)


class QuotexUnavailable(RuntimeError):
    pass


class QuotexBroker:
    """Thin adapter around cleitonleonel/pyquotex.

    The rest of the application stays broker-agnostic and receives only
    normalized candles observed from Quotex.
    """

    def __init__(self, email: str, password: str, lang: str = "es") -> None:
        self.email = email
        self.password = password
        self.lang = lang
        self._client: Optional[Any] = None
        self.connected = False

    async def connect(self) -> None:
        if not self.email or not self.password:
            raise QuotexUnavailable("QUOTEX_EMAIL o QUOTEX_PASSWORD no estan configurados.")

        try:
            from pyquotex.stable_api import Quotex
        except ImportError as exc:
            raise QuotexUnavailable(
                "Instala pyquotex: pip install git+https://github.com/cleitonleonel/pyquotex.git"
            ) from exc

        self._client = Quotex(email=self.email, password=self.password, lang=self.lang)
        check_connect, message = await self._client.connect()
        if not check_connect:
            raise QuotexUnavailable(str(message or "No se pudo conectar con Quotex."))
        self.connected = True

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # pragma: no cover - defensive shutdown
                LOGGER.exception("Error desconectando Quotex")
        self.connected = False

    async def reconnect(self) -> None:
        await self.disconnect()
        await self.connect()

    async def get_candles(self, asset: str, timeframe: int, count: int) -> List[Candle]:
        if self._client is None or not self.connected:
            await self.connect()

        assert self._client is not None
        end_from_time = int(time.time())
        offset = max(timeframe * count, timeframe)

        try:
            raw_candles = await self._client.get_candles(
                asset,
                end_from_time=end_from_time,
                offset=offset,
                period=timeframe,
            )
        except TypeError:
            raw_candles = await self._client.get_candles(asset, end_from_time, offset, timeframe)
        except Exception:
            self.connected = False
            raise

        normalized = [self._normalize_candle(candle, timeframe) for candle in raw_candles or []]
        normalized = [candle for candle in normalized if candle.open > 0 and candle.high >= candle.low]
        normalized.sort(key=lambda item: item.timestamp)
        return normalized[-count:]

    @staticmethod
    def _get_value(raw: Any, names: Iterable[str], default: Any = None) -> Any:
        for name in names:
            if isinstance(raw, dict) and name in raw:
                return raw[name]
            if hasattr(raw, name):
                return getattr(raw, name)
        return default

    @classmethod
    def _normalize_candle(cls, raw: Any, timeframe: int) -> Candle:
        timestamp = cls._get_value(raw, ("timestamp", "time", "from", "start_time", "date"))
        if isinstance(timestamp, datetime):
            ts = timestamp.replace(tzinfo=timestamp.tzinfo or timezone.utc).timestamp()
        else:
            ts = float(timestamp or datetime.now(timezone.utc).timestamp())
            if ts > 10_000_000_000:
                ts = ts / 1000

        now = datetime.now(timezone.utc).timestamp()
        is_closed = ts + timeframe <= now

        return Candle(
            timestamp=ts,
            open=float(cls._get_value(raw, ("open", "o"), 0)),
            high=float(cls._get_value(raw, ("high", "h", "max"), 0)),
            low=float(cls._get_value(raw, ("low", "l", "min"), 0)),
            close=float(cls._get_value(raw, ("close", "c"), 0)),
            volume=float(cls._get_value(raw, ("volume", "v"), 0) or 0),
            is_closed=is_closed,
        )
