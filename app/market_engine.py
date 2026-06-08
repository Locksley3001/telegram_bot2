from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from fastapi import WebSocket

from app.analysis import NO_EDGE_MESSAGE, PriceActionAnalyzer
from app.broker_trade_executor import BrokerTradeExecutor
from app.config import Settings
from app.iq_option_broker import IQOptionBroker
from app.learning import SignalLearningSystem
from app.models import AnalysisSnapshot, EngineState, Signal, SignalOutcome, utc_now
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
        self._data_dir = self._data_path(settings)
        self._signal_history_limit = max(1, settings.signal_history_limit)
        self._api_signal_limit = max(1, settings.api_signal_limit)
        self.notifier = TelegramNotifier(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            self._data_dir / "telegram_notifications.json",
        )
        self.analyzer = PriceActionAnalyzer()
        self.performance = PerformanceTracker(self._data_dir / "performance.json")
        self.trade_executor = BrokerTradeExecutor(
            self._data_dir / "broker_trades.json",
            enabled=settings.broker_trading_enabled,
            balance_mode=settings.iq_option_balance_mode,
            entry_window_seconds=settings.broker_trade_entry_window_seconds,
        )
        self.learning = SignalLearningSystem(
            self._data_dir / "learning.json",
            enabled=settings.learning_enabled,
            min_history=settings.learning_min_history,
            min_win_rate=settings.learning_min_win_rate,
            min_rule_samples=settings.learning_min_rule_samples,
            min_similarity_samples=settings.learning_min_similarity_samples,
            exploration_interval=settings.learning_exploration_interval,
        )
        self.learning.rebuild(self.performance.records.values())
        configured_markets = {self._normalize_market_label(market) for market in settings.market_list}
        configured_markets.discard("")
        self.active_markets: Set[str] = set(configured_markets)
        self.known_markets: Set[str] = set(configured_markets)
        self.timeframe = settings.default_timeframe
        self.snapshots: Dict[str, AnalysisSnapshot] = {}
        self._signals_path = self._data_dir / "signals.json"
        self.signals: List[Signal] = self._load_signal_history()
        if not self.signals:
            self.signals = self._signals_from_performance()
        self.notifier.remember_signals(signal.id for signal in self.signals)
        self.notifier.remember_outcomes(
            record.id
            for record in self.performance.records.values()
            if not record.is_shadow and record.status in {"win", "loss", "push", "aborted"}
        )
        self.last_error: Optional[str] = None
        self.broker_status = "iniciando"
        self._last_signal_at: Dict[str, datetime] = {}
        self._last_learning_event: Dict[str, str] = {}
        self._shadow_registered_ids: Set[str] = set()
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
        signals = self._combined_signal_history(limit=self._api_signal_limit)
        return EngineState(
            connected=self.broker.connected,
            broker_status=self.broker_status,
            markets=sorted(self.known_markets),
            active_markets=sorted(self.active_markets),
            timeframe=self.timeframe,
            snapshots=self.snapshots,
            signals=signals[::-1],
            signal_history_total=self._signal_history_total(),
            performance=self.performance.summary(),
            learning=self.learning.summary(),
            virtual_balance=self.performance.virtual_balance(timeframe=self.timeframe),
            broker_trading=self.trade_executor.summary(),
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
            resolved_records = self.performance.evaluate(asset, candles, self.analyzer.abort_pending_reason)
            executed_trades = await self.trade_executor.execute_due(asset, self.performance.records.values(), self.broker)
            if executed_trades:
                LOGGER.info("Operaciones enviadas a IQ Option para %s: %s", asset, [trade.signal_id for trade in executed_trades])
            if resolved_records:
                self.learning.rebuild(self.performance.records.values())
            pending_outcomes = self.notifier.pending_outcomes(self.performance.records.values())
            if pending_outcomes:
                await self.notifier.send_outcomes(pending_outcomes)
            zones, signal, context = self.analyzer.analyze(asset, self.timeframe, candles)
            if signal is not None:
                signal, context = self._apply_balance_rules(signal, context)
            if signal is not None:
                decision = self.learning.decide(signal)
                if decision.allowed:
                    signal = self.learning.annotate_signal(signal, decision)
                else:
                    self._register_shadow_signal(signal, decision.reason)
                    context["market_message"] = decision.reason
                    context["reason"] = decision.reason
                    signal = None
            elif context.get("shadow_signal"):
                self._register_shadow_signal(context["shadow_signal"], str(context.get("reason") or context.get("market_message") or "senal bloqueada"))
                if context.get("learning_event"):
                    self._remember_learning_event(asset, context)
            elif context.get("learning_event"):
                self._register_shadow_from_context(asset, context)
                self._remember_learning_event(asset, context)
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
                factor_score=context.get("factor_score", 0),
                confidence=context.get("confidence", "discarded"),
                stake_amount=context.get("stake_amount", 0),
                pending_execution_at=context.get("pending_execution_at") or (signal.pending_execution_at if signal else None),
                analysis_text=context.get("analysis_text", ""),
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
                    self.signals = self._trim_signal_history(self.signals)
                    self._save_signal_history()
                    if len(self._emitted_signal_ids) > 1000:
                        self._emitted_signal_ids = {item.id for item in self.signals[-500:]}
                    emit_signal = signal
            if emit_signal is not None:
                await self.notifier.send_signal(emit_signal)
        except Exception as exc:
            self.last_error = f"{asset}: {exc}"
            LOGGER.exception("Fallo analizando %s", asset)

    def _remember_learning_event(self, asset: str, context: dict) -> None:
        event = str(context.get("learning_event") or "").strip()
        if not event:
            return
        key = f"{asset}:{context.get('analysis_candle_ts', '')}"
        if self._last_learning_event.get(asset) == key:
            return
        self._last_learning_event[asset] = key
        self.learning.remember_technical_block(f"{asset}: {event}")

    def _register_shadow_signal(self, signal: Signal, reason: str) -> None:
        shadow_id = f"shadow:{signal.id}"
        if shadow_id in self._shadow_registered_ids or shadow_id in self.performance.records:
            return
        self.performance.register_shadow_signal(signal, reason)
        self._shadow_registered_ids.add(shadow_id)

    def _register_shadow_from_context(self, asset: str, context: dict) -> None:
        direction = context.get("shadow_direction")
        if direction not in {"CALL", "PUT"}:
            return
        candle_ts = int(context.get("analysis_candle_ts") or datetime.now(timezone.utc).timestamp())
        signal = Signal(
            id=f"{asset}:{self.timeframe}:{direction}:{context.get('pattern', 'shadow')}:blocked:{candle_ts}",
            asset=asset,
            direction=direction,
            score=max(1, min(10, round(float(context.get("score", 1.0))))),
            grade="ignore",
            strength=float(context.get("strength", 1.0)),
            continuity=float(context.get("continuity", 1.0)),
            exhaustion=float(context.get("exhaustion", 1.0)),
            cci=float(context.get("cci", 0.0)),
            main_reason=str(context.get("reason") or context.get("market_message") or "senal bloqueada"),
            suggested_expiration=60,
            created_at=utc_now(),
            price=0.0,
            timeframe=self.timeframe,
            factor_score=int(context.get("factor_score", 0)),
            confidence="discarded",
            stake_amount=0,
            pending_execution_at=datetime.fromtimestamp(candle_ts + self.timeframe, tz=timezone.utc),
            analysis_text=str(context.get("analysis_text") or ""),
        )
        self._register_shadow_signal(signal, str(context.get("shadow_reason") or context.get("learning_event") or "senal bloqueada"))

    def _can_emit(self, signal: Signal) -> bool:
        if signal.stake_amount < 10000:
            return False
        if signal.id in self._emitted_signal_ids:
            return False
        previous = self._last_signal_at.get(self._cooldown_key(signal))
        if previous is None:
            return True
        elapsed = (signal.created_at - previous).total_seconds()
        return elapsed >= self.settings.signal_cooldown_seconds

    def _apply_balance_rules(self, signal: Signal, context: dict) -> tuple[Optional[Signal], dict]:
        wallet = self.performance.virtual_balance(timeframe=self.timeframe)
        reason_suffixes: List[str] = []

        if wallet.balance < 10000:
            context["shadow_signal"] = signal
            return self._block_signal(
                context,
                "SENAL DESCARTADA - saldo inferior a $10.000; modo proteccion activo",
            )
        if wallet.pause_candles_remaining > 0:
            context["shadow_signal"] = signal
            return self._block_signal(
                context,
                f"PAUSA - esperando {wallet.pause_candles_remaining} vela(s) por racha perdedora",
            )
        if wallet.bankruptcies >= 2 and context.get("trend_label") == "lateral":
            context["shadow_signal"] = signal
            return self._block_signal(
                context,
                "SENAL DESCARTADA - Quiebra #2 activa: no se opera mercado lateral",
            )

        factor_score = int(context.get("factor_score", signal.factor_score))
        if factor_score >= wallet.high_confidence_threshold:
            stake = 20000
            confidence = "high"
        elif factor_score >= 2:
            stake = 10000
            confidence = "low"
        else:
            context["shadow_signal"] = signal
            return self._block_signal(context, "SENAL DESCARTADA - puntuacion insuficiente")

        if wallet.balance < 20000 and stake > 10000:
            stake = 10000
            confidence = "low"
            reason_suffixes.append("saldo bajo: apuesta limitada a $10.000")
        if wallet.bankruptcies >= 4 and wallet.operations_since_reset < 10 and stake > 10000:
            stake = 10000
            confidence = "low"
            reason_suffixes.append("Quiebra #4+: maximo $10.000 en las primeras 10 operaciones")
        if stake > wallet.balance:
            stake = 10000 if wallet.balance >= 10000 else 0
            confidence = "low"
            reason_suffixes.append("saldo disponible limita la apuesta")
        if stake < 10000:
            context["shadow_signal"] = signal
            return self._block_signal(context, "SENAL DESCARTADA - saldo insuficiente para $10.000")

        signal.stake_amount = stake
        signal.confidence = confidence
        context["stake_amount"] = stake
        context["confidence"] = confidence
        context["pending_execution_at"] = signal.pending_execution_at
        decision = f"OPERACION {signal.direction} - ${stake:,.0f} - al abrir siguiente vela".replace(",", ".")
        if reason_suffixes:
            decision = f"{decision} ({'; '.join(reason_suffixes)})"
            signal.main_reason = f"{signal.main_reason} | {'; '.join(reason_suffixes)}"
            if signal.analysis_text:
                signal.analysis_text = f"{signal.analysis_text}\n[MODO: {wallet.mode}]"
                context["analysis_text"] = signal.analysis_text
        context["market_message"] = decision
        return signal, context

    @staticmethod
    def _block_signal(context: dict, reason: str) -> tuple[None, dict]:
        context["market_message"] = reason
        context["reason"] = reason
        context["stake_amount"] = 0
        context["confidence"] = "discarded"
        analysis_text = context.get("analysis_text", "")
        if analysis_text:
            context["analysis_text"] = f"{analysis_text}\n-> {reason}"
        return None, context

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

    @staticmethod
    def _data_path(settings: Settings) -> Path:
        configured = Path(settings.data_dir).expanduser()
        preferred = configured if configured.is_absolute() else BASE_DIR / configured
        fallback = BASE_DIR / "data"
        temp_fallback = Path(tempfile.gettempdir()) / "trading-bot-data"
        for candidate in (preferred, fallback, temp_fallback):
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe = candidate / ".write-test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                if candidate != preferred:
                    LOGGER.warning("DATA_DIR %s no es escribible; usando %s.", preferred, candidate)
                return candidate
            except OSError:
                continue
        return preferred

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
            return self._trim_signal_history(signals)
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
                return self._trim_signal_history(signals)
            except Exception:
                return []

    def _signals_from_performance(self) -> List[Signal]:
        signals: List[Signal] = []
        real_records = [record for record in self.performance.records.values() if not record.is_shadow]
        for record in sorted(real_records, key=lambda item: item.created_at)[
            -self._signal_history_limit :
        ]:
            signals.append(self._signal_from_record(record))
        if signals:
            self._save_signal_history(signals)
        return signals

    def _combined_signal_history(self, limit: int = 200) -> List[Signal]:
        by_id: Dict[str, Signal] = {signal.id: signal for signal in self.signals}
        for record in self.performance.records.values():
            if record.is_shadow:
                continue
            by_id.setdefault(record.id, self._signal_from_record(record))
        return sorted(by_id.values(), key=lambda signal: signal.created_at)[-limit:]

    def _signal_history_total(self) -> int:
        real_record_ids = {record.id for record in self.performance.records.values() if not record.is_shadow}
        return len({signal.id for signal in self.signals} | real_record_ids)

    def _trim_signal_history(self, signals: List[Signal]) -> List[Signal]:
        by_id: Dict[str, Signal] = {}
        for signal in sorted(signals, key=lambda item: item.created_at):
            by_id[signal.id] = signal
        return sorted(by_id.values(), key=lambda item: item.created_at)[-self._signal_history_limit :]

    @staticmethod
    def _signal_from_record(record: SignalOutcome) -> Signal:
        return Signal(
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
            stake_amount=record.stake_amount,
            pending_execution_at=record.entry_at,
        )

    def _save_signal_history(self, signals: Optional[List[Signal]] = None) -> None:
        self._signals_path.parent.mkdir(parents=True, exist_ok=True)
        source = signals if signals is not None else self.signals
        payload = {
            "signals": [
                signal.model_dump(mode="json")
                for signal in self._trim_signal_history(source)
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
