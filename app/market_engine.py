from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from fastapi import WebSocket

from app.analysis import NO_EDGE_MESSAGE, PriceActionAnalyzer
from app.config import Settings
from app.iq_option_broker import IQOptionBroker
from app.learning import SignalLearningSystem
from app.models import AnalysisSnapshot, EngineState, Signal, utc_now
from app.performance_tracker import PerformanceTracker
from app.telegram_notifier import TelegramNotifier

LOGGER = logging.getLogger(__name__)
ALLOWED_TIMEFRAMES = {30, 45, 60, 120, 180, 300}
BASE_DIR = Path(__file__).resolve().parent.parent


class MarketEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.broker = IQOptionBroker(
            settings.iq_option_email,
            settings.iq_option_password,
            two_factor_code=settings.iq_option_2fa_code,
            balance_mode=settings.iq_option_balance_mode,
            stream_max_candles=settings.candle_count,
        )
        self.notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        self.analyzer = PriceActionAnalyzer()
        self.performance = PerformanceTracker(BASE_DIR / "data" / "performance.json")
        self.learning = SignalLearningSystem(
            BASE_DIR / "data" / "learning.json",
            enabled=settings.learning_enabled,
            min_history=settings.learning_min_history,
            min_win_rate=settings.learning_min_win_rate,
            min_rule_samples=settings.learning_min_rule_samples,
            min_similarity_samples=settings.learning_min_similarity_samples,
        )
        self.learning.rebuild(self.performance.records.values())
        configured_markets = {self._normalize_market_label(market) for market in settings.market_list}
        configured_markets.discard("")
        self.active_markets: Set[str] = set(configured_markets)
        self.known_markets: Set[str] = set(configured_markets)
        self.timeframe = settings.default_timeframe
        self.snapshots: Dict[str, AnalysisSnapshot] = {}
        self._signals_path = BASE_DIR / "data" / "signals.json"
        self.signals: List[Signal] = self._load_signal_history()
        if not self.signals:
            self.signals = self._signals_from_performance()
        self.last_error: Optional[str] = None
        self.broker_status = "iniciando"
        self._last_signal_at: Dict[str, datetime] = {}
        self._emitted_signal_ids: Set[str] = {signal.id for signal in self.signals}
        self._restore_signal_cooldowns()
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
        cleaned = self._normalize_market_label(asset)
        if cleaned:
            async with self._lock:
                self.known_markets.add(cleaned)
                self.active_markets.add(cleaned)
        await self._broadcast()
        return self.state()

    async def remove_market(self, asset: str) -> EngineState:
        cleaned = self._normalize_market_label(asset)
        async with self._lock:
            self.known_markets.discard(cleaned)
            self.active_markets.discard(cleaned)
            self.snapshots.pop(cleaned, None)
        await self._broadcast()
        return self.state()

    async def set_market_enabled(self, asset: str, enabled: bool) -> EngineState:
        cleaned = self._normalize_market_label(asset)
        async with self._lock:
            self.known_markets.add(cleaned)
            if enabled:
                self.active_markets.add(cleaned)
            else:
                self.active_markets.discard(cleaned)
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
            performance=self.performance.summary(),
            learning=self.learning.summary(),
            last_error=self.last_error,
        )

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._ensure_connection()
                markets = sorted(self.active_markets)
                if self.broker.connected and markets:
                    await asyncio.gather(*(self._analyze_market(asset) for asset in markets))
                    self.broker_status = "conectado a IQ Option"
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
        if not self.settings.iq_option_email or not self.settings.iq_option_password:
            self.broker_status = "credenciales IQ Option no configuradas"
            self.last_error = "CONFIGURACION_MANUAL_REQUERIDA: configura IQ_OPTION_EMAIL y IQ_OPTION_PASSWORD."
            return
        try:
            self.broker_status = "conectando a IQ Option"
            await self.broker.connect()
            self.last_error = None
        except Exception as exc:
            self.last_error = str(exc)
            self.broker_status = "sin conexion al broker"

    async def _analyze_market(self, asset: str) -> None:
        try:
            candles = await self.broker.get_realtime_candles(asset, self.timeframe)
            if len(candles) < 4:
                candles = await self.broker.get_candles(asset, self.timeframe, self.settings.candle_count)
            if self.performance.evaluate(asset, candles):
                self.learning.rebuild(self.performance.records.values())
            zones, signal, context = self.analyzer.analyze(asset, self.timeframe, candles)
            if signal is not None:
                decision = self.learning.decide(signal)
                if decision.allowed:
                    signal = self.learning.annotate_signal(signal, decision)
                else:
                    context["market_message"] = decision.reason
                    context["reason"] = decision.reason
                    signal = None
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
                cci=context.get("cci", 0.0),
                updated_at=utc_now(),
            )

            emit_signal: Optional[Signal] = None
            async with self._lock:
                self.snapshots[asset] = snapshot
                if signal is not None and self._can_emit(signal):
                    self.signals.append(signal)
                    self.performance.register_signal(signal)
                    self._last_signal_at[self._cooldown_key(signal)] = signal.created_at
                    self._emitted_signal_ids.add(signal.id)
                    self.signals = self.signals[-200:]
                    self._save_signal_history()
                    if len(self._emitted_signal_ids) > 1000:
                        self._emitted_signal_ids = set(list(self._emitted_signal_ids)[-500:])
                    emit_signal = signal
            if emit_signal is not None:
                await self.notifier.send_signal(emit_signal)
        except Exception as exc:
            self.last_error = f"{asset}: {exc}"
            LOGGER.exception("Fallo analizando %s", asset)

    def _can_emit(self, signal: Signal) -> bool:
        if signal.score < 7:
            return False
        if signal.id in self._emitted_signal_ids:
            return False
        previous = self._last_signal_at.get(self._cooldown_key(signal))
        if previous is None:
            return True
        elapsed = (signal.created_at - previous).total_seconds()
        return elapsed >= self.settings.signal_cooldown_seconds

    @staticmethod
    def _cooldown_key(signal: Signal) -> str:
        return f"{signal.asset}:{signal.timeframe}"

    @staticmethod
    def _normalize_market_label(asset: str) -> str:
        cleaned = asset.strip().upper().replace(" ", "").replace("_", "-")
        if not cleaned:
            return ""
        if not cleaned.endswith("-OTC"):
            cleaned = f"{cleaned}-OTC"
        aliases = {
            "NVIDIAAMD-OTC": "NVDA/AMD-OTC",
            "NVIDIA/AMD-OTC": "NVDA/AMD-OTC",
            "NVIDIA-AMD-OTC": "NVDA/AMD-OTC",
            "NVDAAMD-OTC": "NVDA/AMD-OTC",
            "NVDA/AMD-OTC": "NVDA/AMD-OTC",
        }
        return aliases.get(cleaned, cleaned)

    def _load_signal_history(self) -> List[Signal]:
        if not self._signals_path.exists():
            return []
        try:
            payload = json.loads(self._signals_path.read_text(encoding="utf-8"))
            signals = [
                Signal.model_validate(item)
                for item in payload.get("signals", [])
                if isinstance(item, dict) and item.get("id")
            ]
            signals.sort(key=lambda signal: signal.created_at)
            return signals[-200:]
        except Exception:
            backup = self._signals_path.with_suffix(f"{self._signals_path.suffix}.bak")
            if not backup.exists():
                return []
            try:
                payload = json.loads(backup.read_text(encoding="utf-8"))
                signals = [
                    Signal.model_validate(item)
                    for item in payload.get("signals", [])
                    if isinstance(item, dict) and item.get("id")
                ]
                signals.sort(key=lambda signal: signal.created_at)
                return signals[-200:]
            except Exception:
                return []

    def _signals_from_performance(self) -> List[Signal]:
        signals: List[Signal] = []
        for record in sorted(self.performance.records.values(), key=lambda item: item.created_at)[-200:]:
            signals.append(
                Signal(
                    id=record.id,
                    asset=record.asset,
                    direction=record.direction,
                    score=record.score,
                    grade="strong" if record.score >= 8 else "valid",
                    strength=record.strength,
                    continuity=record.continuity,
                    exhaustion=record.exhaustion,
                    cci=record.cci,
                    main_reason=record.main_reason,
                    suggested_expiration=record.suggested_expiration,
                    created_at=record.created_at,
                    price=record.entry_price,
                    timeframe=record.timeframe,
                )
            )
        if signals:
            self._save_signal_history(signals)
        return signals

    def _save_signal_history(self, signals: Optional[List[Signal]] = None) -> None:
        self._signals_path.parent.mkdir(parents=True, exist_ok=True)
        source = signals if signals is not None else self.signals
        payload = {
            "signals": [
                signal.model_dump(mode="json")
                for signal in sorted(source, key=lambda item: item.created_at)[-200:]
            ]
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        temp_path = self._signals_path.with_suffix(f"{self._signals_path.suffix}.tmp")
        backup_path = self._signals_path.with_suffix(f"{self._signals_path.suffix}.bak")
        temp_path.write_text(encoded, encoding="utf-8")
        if self._signals_path.exists():
            backup_path.write_text(self._signals_path.read_text(encoding="utf-8"), encoding="utf-8")
        temp_path.replace(self._signals_path)

    def _restore_signal_cooldowns(self) -> None:
        for signal in self.signals[-80:]:
            key = self._cooldown_key(signal)
            previous = self._last_signal_at.get(key)
            if previous is None or signal.created_at > previous:
                self._last_signal_at[key] = signal.created_at

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
