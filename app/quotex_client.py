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
        session_token: str = "",
        session_cookies: str = "",
    ) -> None:
        self.email = email
        self.password = password
        self.lang = lang
        self.host = host
        self.user_agent = user_agent
        self.proxy_url = proxy_url.strip()
        self.wss_url = wss_url.strip()
        self.root_path = root_path
        self.session_token = session_token.strip()
        self.session_cookies = session_cookies.strip()
        self._client: Optional[Any] = None
        self.connected = False

    async def connect(self) -> None:
        if not self.email or not self.password:
            raise QuotexUnavailable("QUOTEX_EMAIL o QUOTEX_PASSWORD no estan configurados.")

        try:
            from pyquotex.stable_api import Quotex
            self._patch_pyquotex_http_transport()
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
        if self.session_token:
            self._client.set_session(
                user_agent=self.user_agent,
                cookies=self.session_cookies or None,
                ssid=self.session_token,
            )
        check_connect, message = await self._client.connect()
        if not check_connect:
            raise QuotexUnavailable(self._explain_connection_error(str(message or "No se pudo conectar con Quotex.")))
        self.connected = True

    def _patch_pyquotex_http_transport(self) -> None:
        try:
            from curl_cffi import requests as curl_requests
            import pyquotex.api as api_module
            import pyquotex.network.login as login_module
            import pyquotex.network.navigator as navigator_module
        except Exception:
            LOGGER.exception("No se pudo preparar transporte curl_cffi para pyquotex")
            return

        original_browser = navigator_module.Browser
        original_login = login_module.Login

        class CurlResponseAdapter:
            def __init__(adapter_self, response: Any) -> None:
                adapter_self._response = response
                adapter_self.status_code = int(getattr(response, "status_code", 0) or 0)
                adapter_self.headers = getattr(response, "headers", {})
                adapter_self.cookies = getattr(response, "cookies", {})
                adapter_self.content = getattr(response, "content", b"")
                adapter_self.text = getattr(response, "text", "")
                adapter_self.url = getattr(response, "url", "")
                adapter_self.reason_phrase = getattr(response, "reason", "") or ""
                adapter_self.is_success = 200 <= adapter_self.status_code < 300

            def json(adapter_self) -> Any:
                return adapter_self._response.json()

        class CurlAsyncSession:
            def __init__(session_self) -> None:
                session_self._session = curl_requests.AsyncSession(
                    impersonate="chrome124",
                    proxy=self.proxy_url or None,
                )
                session_self.cookies = session_self._session.cookies
                session_self.is_closed = False

            async def request(session_self, method: str, url: str, **kwargs: Any) -> Any:
                if session_self.is_closed:
                    raise RuntimeError("CurlAsyncSession is closed")
                kwargs.pop("verify", None)
                follow_redirects = kwargs.pop("follow_redirects", True)
                kwargs.setdefault("allow_redirects", follow_redirects)
                response = await session_self._session.request(method, url, **kwargs)
                return CurlResponseAdapter(response)

            async def aclose(session_self) -> None:
                if not session_self.is_closed:
                    await session_self._session.close()
                    session_self.is_closed = True

        class ProxiedBrowser(original_browser):  # type: ignore[misc, valid-type]
            def __init__(browser_self, *args: Any, **kwargs: Any) -> None:
                if self.proxy_url:
                    kwargs.setdefault("proxies", self.proxy_url)
                super().__init__(*args, **kwargs)
                browser_self._client = CurlAsyncSession()

        class ProxiedLogin(original_login):  # type: ignore[misc, valid-type]
            def __init__(login_self, *args: Any, **kwargs: Any) -> None:
                if self.proxy_url:
                    kwargs.setdefault("proxies", self.proxy_url)
                super().__init__(*args, **kwargs)
                login_self._client = CurlAsyncSession()

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
