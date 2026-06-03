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

    def __init__(
        self,
        email: str,
        password: str,
        lang: str = "es",
        host: str = "qxbroker.com",
        user_agent: str = "Mozilla/5.0 (X11; Linux x86_64; rv:119.0) Gecko/20100101 Firefox/119.0",
        proxy_url: str = "",
        wss_url: str = "",
        root_path: str = "/tmp/quotex",
    ) -> None:
        self.email = email
        self.password = password
        self.lang = lang
        self.host = host
        self.user_agent = user_agent
        self.proxy_url = proxy_url.strip()
        self.wss_url = wss_url.strip()
        self.root_path = root_path
        self._client: Optional[Any] = None
        self.connected = False

    async def connect(self) -> None:
        if not self.email or not self.password:
            raise QuotexUnavailable("QUOTEX_EMAIL o QUOTEX_PASSWORD no estan configurados.")

        try:
            from pyquotex.stable_api import Quotex
            self._patch_pyquotex_proxy()
        except ImportError as exc:
            raise QuotexUnavailable(
                "Instala pyquotex: pip install git+https://github.com/cleitonleonel/pyquotex.git"
            ) from exc

        self._client = Quotex(
            email=self.email,
            password=self.password,
            host=self.host,
            lang=self.lang,
            user_agent=self.user_agent,
            root_path=self.root_path,
            proxies=self.proxy_url or None,
            wss_url_override=self.wss_url or None,
        )
        check_connect, message = await self._client.connect()
        if not check_connect:
            raise QuotexUnavailable(self._explain_connection_error(str(message or "No se pudo conectar con Quotex.")))
        self.connected = True

    def _patch_pyquotex_proxy(self) -> None:
        if not self.proxy_url:
            return
        try:
            import pyquotex.api as api_module
            import pyquotex.network.login as login_module
            import pyquotex.network.navigator as navigator_module
        except Exception:
            LOGGER.exception("No se pudo preparar proxy para pyquotex")
            return

        original_browser = navigator_module.Browser
        original_login = login_module.Login

        class ProxiedBrowser(original_browser):  # type: ignore[misc, valid-type]
            def __init__(browser_self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("proxies", self.proxy_url)
                super().__init__(*args, **kwargs)

        class ProxiedLogin(original_login):  # type: ignore[misc, valid-type]
            def __init__(login_self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("proxies", self.proxy_url)
                super().__init__(*args, **kwargs)

        api_module.Browser = ProxiedBrowser
        api_module.Login = ProxiedLogin
        login_module.Browser = ProxiedBrowser
        login_module.Login = ProxiedLogin

    @staticmethod
    def _explain_connection_error(message: str) -> str:
        if "403" in message or "Forbidden" in message:
            return (
                "HTTP 403 Forbidden: Quotex/Cloudflare bloqueo la IP del servidor. "
                "En Render suele requerir QUOTEX_PROXY_URL con un proxy permitido."
            )
        return message

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
        asset_name = await self._resolve_asset(asset)
        end_from_time = int(time.time())
        offset = max(timeframe * count, timeframe)

        try:
            raw_candles = await self._client.get_candles(
                asset_name,
                end_from_time=end_from_time,
                offset=offset,
                period=timeframe,
            )
        except TypeError:
            raw_candles = await self._client.get_candles(asset_name, end_from_time, offset, timeframe)
        except Exception:
            self.connected = False
            raise

        normalized = [self._normalize_candle(candle, timeframe) for candle in raw_candles or []]
        normalized = [candle for candle in normalized if candle.open > 0 and candle.high >= candle.low]
        normalized.sort(key=lambda item: item.timestamp)
        return normalized[-count:]

    async def _resolve_asset(self, asset: str) -> str:
        if self._client is None:
            return asset
        get_available_asset = getattr(self._client, "get_available_asset", None)
        if get_available_asset is None:
            return asset
        try:
            asset_name, asset_data = await get_available_asset(asset, force_open=True)
            if asset_data and len(asset_data) > 2 and not asset_data[2]:
                raise QuotexUnavailable(f"El activo {asset} no esta abierto en Quotex.")
            return asset_name or asset
        except QuotexUnavailable:
            raise
        except Exception:
            LOGGER.exception("No se pudo verificar disponibilidad de %s", asset)
            return asset

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
