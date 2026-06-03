from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from fastapi import WebSocket

from app.analysis import NO_EDGE_MESSAGE, PriceActionAnalyzer
from app.config import Settings
from app.models import AnalysisSnapshot, EngineState, Signal, utc_now
from app.quotex_client import QuotexBroker
from app.telegram_notifier import TelegramNotifier

LOGGER = logging.getLogger(__name__)
ALLOWED_TIMEFRAMES = {30, 45, 60, 120, 180, 300}


class MarketEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.broker = QuotexBroker(
            settings.quotex_email,
            settings.quotex_password,
            host=settings.quotex_host,
            user_agent=settings.quotex_user_agent,
            proxy_url=settings.quotex_proxy_url,
            wss_url=settings.quotex_wss_url,
            root_path=settings.quotex_root_path,
        )
        self.notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        self.analyzer = PriceActionAnalyzer()
        self.active_markets: Set[str] = set(settings.market_list)
        self.known_markets: Set[str] = set(settings.market_list)
        self.timeframe = settings.default_timeframe
        self.snapshots: Dict[str, AnalysisSnapshot] = {}
        self.signals: List[Signal] = []
        self.last_error: Optional[str] = None
        self.broker_status = "iniciando"
        self._last_signal_at: Dict[str, datetime] = {}
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._clients: Set[WebSocket] = set()
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="market-engine")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.broker.disconnect()

    async def subscribe(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        await websocket.send_json(self.state().model_dump(mode="json"))

    def unsubscribe(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def add_market(self, asset: str) -> EngineState:
        cleaned = asset.strip()
        if cleaned:
            async with self._lock:
                self.known_markets.add(cleaned)
                self.active_markets.add(cleaned)
        await self._broadcast()
        return self.state()

    async def remove_market(self, asset: str) -> EngineState:
        async with self._lock:
            self.known_markets.discard(asset)
            self.active_markets.discard(asset)
            self.snapshots.pop(asset, None)
        await self._broadcast()
        return self.state()

    async def set_market_enabled(self, asset: str, enabled: bool) -> EngineState:
        async with self._lock:
            self.known_markets.add(asset)
            if enabled:
                self.active_markets.add(asset)
            else:
                self.active_markets.discard(asset)
        await self._broadcast()
        return self.state()

    async def set_timeframe(self, timeframe: int) -> EngineState:
        if timeframe not in ALLOWED_TIMEFRAMES:
            timeframe = 60
        async with self._lock:
            self.timeframe = timeframe
            self.snapshots.clear()
        await self._broadcast()
        return self.state()

    def state(self) -> EngineState:
        return EngineState(
            connected=self.broker.connected,
            broker_status=self.broker_status,
            markets=sorted(self.known_markets),
            active_markets=sorted(self.active_markets),
            timeframe=self.timeframe,
            snapshots=self.snapshots,
            signals=self.signals[-80:][::-1],
            last_error=self.last_error,
        )

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._ensure_connection()
                markets = sorted(self.active_markets)
                if self.broker.connected and markets:
                    await asyncio.gather(*(self._analyze_market(asset) for asset in markets))
                    self.broker_status = "conectado a Quotex"
                await self._broadcast()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                self.broker_status = "reconectando"
                LOGGER.exception("Ciclo de mercado fallido")
                try:
                    await self.broker.reconnect()
                except Exception as reconnect_exc:
                    self.last_error = str(reconnect_exc)
                    self.broker_status = "sin conexion al broker"
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _ensure_connection(self) -> None:
        if self.broker.connected:
            return
        if not self.settings.quotex_email or not self.settings.quotex_password:
            self.broker_status = "credenciales Quotex no configuradas"
            self.last_error = "Configura QUOTEX_EMAIL y QUOTEX_PASSWORD en .env o en Render."
            return
        try:
            self.broker_status = "conectando a Quotex"
            await self.broker.connect()
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            self.broker_status = "sin conexion al broker"

    async def _analyze_market(self, asset: str) -> None:
        try:
            candles = await self.broker.get_candles(asset, self.timeframe, self.settings.candle_count)
            zones, signal, context = self.analyzer.analyze(asset, self.timeframe, candles)
            snapshot = AnalysisSnapshot(
                asset=asset,
                timeframe=self.timeframe,
                candles=candles[-90:],
                zones=zones,
                signal=signal,
                market_message=context.get("market_message", NO_EDGE_MESSAGE),
                strength=context.get("strength", 1.0),
                continuity=context.get("continuity", 1.0),
                exhaustion=context.get("exhaustion", 1.0),
                updated_at=utc_now(),
            )

            emit_signal: Optional[Signal] = None
            async with self._lock:
                self.snapshots[asset] = snapshot
                if signal is not None and self._can_emit(signal):
                    self.signals.append(signal)
                    self._last_signal_at[self._cooldown_key(signal)] = signal.created_at
                    self.signals = self.signals[-200:]
                    emit_signal = signal
            if emit_signal is not None:
                await self.notifier.send_signal(emit_signal)
        except Exception as exc:
            self.last_error = f"{asset}: {exc}"
            LOGGER.exception("Fallo analizando %s", asset)

    def _can_emit(self, signal: Signal) -> bool:
        if signal.score < 6:
            return False
        previous = self._last_signal_at.get(self._cooldown_key(signal))
        if previous is None:
            return True
        elapsed = (signal.created_at - previous).total_seconds()
        return elapsed >= self.settings.signal_cooldown_seconds

    @staticmethod
    def _cooldown_key(signal: Signal) -> str:
        return f"{signal.asset}:{signal.direction}:{signal.timeframe}"

    async def _broadcast(self) -> None:
        if not self._clients:
            return
        payload = self.state().model_dump(mode="json")
        disconnected: List[WebSocket] = []
        for websocket in list(self._clients):
            try:
                await websocket.send_json(payload)
            except Exception:
                disconnected.append(websocket)
        for websocket in disconnected:
            self.unsubscribe(websocket)
