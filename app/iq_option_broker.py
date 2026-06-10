from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional, Set, Tuple

from app.broker_interface import BrokerInterface, BrokerUnavailable
from app.models import Candle

LOGGER = logging.getLogger(__name__)


class IQOptionBroker(BrokerInterface):
    """Adapter around the community iqoptionapi stable_api client."""

    def __init__(
        self,
        email: str,
        password: str,
        two_factor_code: str = "",
        balance_mode: str = "PRACTICE",
        stream_max_candles: int = 120,
    ) -> None:
        self.email = email.strip()
        self.password = password
        self.two_factor_code = two_factor_code.strip()
        self.balance_mode = balance_mode.strip().upper() or "PRACTICE"
        self.stream_max_candles = max(10, stream_max_candles)
        self._client: Optional[Any] = None
        self._connected = False
        self._request_lock = asyncio.Lock()
        self._streams: Set[Tuple[str, int]] = set()

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        if not self.email or not self.password:
            raise BrokerUnavailable(
                "CONFIGURACION_MANUAL_REQUERIDA: configura IQ_OPTION_EMAIL e "
                "IQ_OPTION_PASSWORD en .env o en Render."
            )

        try:
            from iqoptionapi.stable_api import IQ_Option
        except ImportError as exc:
            raise BrokerUnavailable(
                "Instala iqoptionapi: pip install "
                "https://github.com/iqoptionapi/iqoptionapi/archive/refs/heads/master.zip"
            ) from exc

        def _connect() -> None:
            client = IQ_Option(self.email, self.password)
            set_reconnect = getattr(client, "set_max_reconnect", None)
            if callable(set_reconnect):
                set_reconnect(-1)

            status, reason = client.connect()
            if not status and str(reason).upper() == "2FA":
                # CONFIGURACION_MANUAL_REQUERIDA:
                # IQ Option puede exigir codigo 2FA/SMS. Configura
                # IQ_OPTION_2FA_CODE con el codigo vigente y redepliega; el
                # codigo normalmente expira, asi que no se puede automatizar.
                if not self.two_factor_code:
                    raise BrokerUnavailable(
                        "CONFIGURACION_MANUAL_REQUERIDA: IQ Option exige 2FA. "
                        "Configura IQ_OPTION_2FA_CODE con el codigo temporal."
                    )
                status, reason = client.connect_2fa(self.two_factor_code)

            if not status:
                raise BrokerUnavailable(str(reason or "No se pudo conectar con IQ Option."))

            if self.balance_mode in {"PRACTICE", "REAL"}:
                try:
                    client.change_balance(self.balance_mode)
                except Exception:
                    LOGGER.exception("No se pudo seleccionar balance %s", self.balance_mode)

            update_actives = getattr(client, "update_ACTIVES_OPCODE", None)
            if callable(update_actives):
                try:
                    update_actives()
                except Exception:
                    LOGGER.exception("No se pudo actualizar la tabla de activos de IQ Option")

            self._client = client

        await asyncio.to_thread(_connect)
        self._connected = True

    async def disconnect(self) -> None:
        client = self._client
        if client is not None:
            for asset, timeframe in list(self._streams):
                try:
                    await asyncio.to_thread(client.stop_candles_stream, asset, timeframe)
                except Exception:  # pragma: no cover - defensive shutdown
                    LOGGER.exception("Error cerrando stream de IQ Option para %s", asset)
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    await asyncio.to_thread(close)
                except Exception:  # pragma: no cover - defensive shutdown
                    LOGGER.exception("Error cerrando IQ Option")
        self._streams.clear()
        self._client = None
        self._connected = False

    async def reconnect(self) -> None:
        await self.disconnect()
        await self.connect()

    async def get_candles(self, asset: str, timeframe: int, count: int) -> List[Candle]:
        await self._ensure_connection()
        client = self._require_client()
        asset_name = self._normalize_asset_name(asset)

        async with self._request_lock:
            try:
                raw_candles = await asyncio.to_thread(
                    client.get_candles,
                    asset_name,
                    timeframe,
                    count,
                    time.time(),
                )
            except Exception:
                self._connected = False
                raise

        normalized = [self._normalize_candle(candle, timeframe) for candle in raw_candles or []]
        normalized = [candle for candle in normalized if candle.open > 0 and candle.high >= candle.low]
        normalized.sort(key=lambda item: item.timestamp)
        return normalized[-count:]

    async def get_available_assets(self) -> List[str]:
        await self._ensure_connection()
        client = self._require_client()
        async with self._request_lock:
            data = await asyncio.to_thread(client.get_all_open_time)

        assets: Set[str] = set()
        for market in ("turbo", "binary", "digital", "forex", "cfd", "crypto"):
            for asset, details in (data.get(market, {}) or {}).items():
                if isinstance(details, dict) and details.get("open"):
                    assets.add(asset)
        return sorted(assets)

    async def get_current_price(self, asset: str) -> Optional[float]:
        candles = await self.get_candles(asset, 60, 1)
        return candles[-1].close if candles else None

    async def get_payout(self, asset: str, timeframe: int) -> Optional[float]:
        await self._ensure_connection()
        client = self._require_client()
        asset_name = self._normalize_asset_name(asset)
        duration_minutes = max(1, round(timeframe / 60))

        async with self._request_lock:
            for method_name, args in (
                ("get_digital_payout", (asset_name,)),
                ("get_digital_current_profit", (asset_name, duration_minutes)),
            ):
                method = getattr(client, method_name, None)
                if not callable(method):
                    continue
                try:
                    payout = await asyncio.to_thread(method, *args)
                except Exception:
                    LOGGER.exception("No se pudo consultar payout con %s", method_name)
                    continue
                if payout is not None and payout is not False:
                    return float(payout)
        return None

    async def place_option_trade(self, asset: str, direction: str, amount: int, expiration_seconds: int) -> tuple[bool, str]:
        await self._ensure_connection()
        client = self._require_client()
        asset_name = self._normalize_asset_name(asset)
        action = direction.strip().lower()
        if action not in {"call", "put"}:
            return False, f"Direccion invalida para IQ Option: {direction}"
        duration_minutes = max(1, int(math.ceil(max(1, expiration_seconds) / 60)))

        async with self._request_lock:
            try:
                success, order_id = await asyncio.to_thread(
                    client.buy,
                    float(amount),
                    asset_name,
                    action,
                    duration_minutes,
                )
            except Exception:
                self._connected = False
                raise

        if success is True:
            return True, str(order_id)
        detail = str(order_id or "IQ Option rechazo la compra.")
        return False, detail

    async def start_realtime_candles(self, asset: str, timeframe: int) -> None:
        await self._ensure_connection()
        client = self._require_client()
        asset_name = self._normalize_asset_name(asset)
        key = (asset_name, timeframe)
        if key in self._streams:
            return
        async with self._request_lock:
            await asyncio.to_thread(client.start_candles_stream, asset_name, timeframe, self.stream_max_candles)
        self._streams.add(key)

    async def get_realtime_candles(self, asset: str, timeframe: int) -> List[Candle]:
        await self.start_realtime_candles(asset, timeframe)
        client = self._require_client()
        asset_name = self._normalize_asset_name(asset)
        async with self._request_lock:
            raw = await asyncio.to_thread(client.get_realtime_candles, asset_name, timeframe)
            raw_snapshot = (raw or {}).copy()
            raw_candles = list(raw_snapshot.values())
        candles = [self._normalize_candle(candle, timeframe) for candle in raw_candles]
        candles = [candle for candle in candles if candle.open > 0 and candle.high >= candle.low]
        candles.sort(key=lambda item: item.timestamp)
        return candles[-self.stream_max_candles :]

    async def stop_realtime_candles(self, asset: str, timeframe: int) -> None:
        client = self._client
        if client is None:
            return
        asset_name = self._normalize_asset_name(asset)
        key = (asset_name, timeframe)
        if key not in self._streams:
            return
        async with self._request_lock:
            await asyncio.to_thread(client.stop_candles_stream, asset_name, timeframe)
        self._streams.discard(key)

    async def _ensure_connection(self) -> None:
        if self._client is None:
            await self.connect()
            return
        check_connect = getattr(self._client, "check_connect", None)
        if callable(check_connect):
            is_connected = await asyncio.to_thread(check_connect)
            if not is_connected:
                self._connected = False
                await self.reconnect()

    def _require_client(self) -> Any:
        if self._client is None:
            raise BrokerUnavailable("IQ Option no esta conectado.")
        return self._client

    @staticmethod
    def _normalize_asset_name(asset: str) -> str:
        cleaned = asset.strip().replace(" ", "").replace("_", "-")
        if cleaned.lower().endswith("_otc"):
            cleaned = f"{cleaned[:-4]}-OTC"
        upper = cleaned.upper()
        aliases = {
            "BTC/USD-OTC": "BTCUSD-OTC-op",
            "BTCUSD-OTC": "BTCUSD-OTC-op",
            "BTCUSDOTC": "BTCUSD-OTC-op",
            "BTCUSD-OTC-OP": "BTCUSD-OTC-op",
            "ETH/USD-OTC": "ETHUSD-OTC",
            "ETHUSDOTC": "ETHUSD-OTC",
            "SOL/USD-OTC": "SOLUSD-OTC",
            "SOLUSDOTC": "SOLUSD-OTC",
            "NVDAAMD-OTC": "NVDA/AMD-OTC",
            "NVDA/AMD-OTC": "NVDA/AMD-OTC",
            "NVIDIAAMD-OTC": "NVDA/AMD-OTC",
            "NVIDIA/AMD-OTC": "NVDA/AMD-OTC",
            "NVIDIA-AMD-OTC": "NVDA/AMD-OTC",
        }
        return aliases.get(upper, upper)

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
        timestamp = cls._get_value(raw, ("timestamp", "time", "from", "start_time", "date", "at"))
        if isinstance(timestamp, datetime):
            ts = timestamp.replace(tzinfo=timestamp.tzinfo or timezone.utc).timestamp()
        else:
            ts = float(timestamp or datetime.now(timezone.utc).timestamp())
            if ts > 10_000_000_000:
                ts = ts / 1000

        now = datetime.now(timezone.utc).timestamp()
        is_closed = ts + timeframe <= now

        high = cls._get_value(raw, ("high", "h", "max"), 0)
        low = cls._get_value(raw, ("low", "l", "min"), 0)
        return Candle(
            timestamp=ts,
            open=float(cls._get_value(raw, ("open", "o"), 0)),
            high=float(high),
            low=float(low),
            close=float(cls._get_value(raw, ("close", "c"), 0)),
            volume=float(cls._get_value(raw, ("volume", "v"), 0) or 0),
            is_closed=is_closed,
        )
