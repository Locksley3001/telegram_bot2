from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from telegram import Bot

from app.models import BalanceEvent, Signal, SignalOutcome, VirtualBalanceSummary
from app.state_storage import StateStorage

LOGGER = logging.getLogger(__name__)
SUMMARY_BATCH_SIZE = 5


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        state_path: Optional[Path] = None,
        storage: Optional[StateStorage] = None,
    ) -> None:
        self.token = token
        self.chat_id = chat_id
        self.state_path = state_path
        self.storage = storage
        self._bot: Optional[Bot] = Bot(token=token) if token and chat_id else None
        self._sent_ids: Set[str] = set()
        self._result_sent_ids: Set[str] = set()
        self._balance_event_sent_ids: Set[str] = set()
        self._summary_pending: List[Dict[str, object]] = []
        self._summaries_sent = 0
        self._state_lock = asyncio.Lock()
        self.last_error: Optional[str] = None
        self._load_state()

    @property
    def enabled(self) -> bool:
        return self._bot is not None

    async def send_signal(self, signal: Signal) -> None:
        async with self._state_lock:
            if self._bot is None or signal.is_shadow or signal.id in self._sent_ids:
                return

            icon = "\U0001F7E2" if signal.direction == "CALL" else "\U0001F534"
            text = (
                f"{icon} {signal.direction}\n"
                f"Activo: {signal.asset}\n"
                f"Puntuacion: {signal.score}/10\n"
                f"Fuerza: {signal.strength:.1f}/10\n"
                f"Continuidad: {signal.continuity:.1f}/10\n"
                f"Cansancio: {signal.exhaustion:.1f}/10\n"
                f"CCI(20): {signal.cci:.1f}\n"
                f"Razon principal: {signal.main_reason}\n"
                f"Expiracion sugerida: {signal.suggested_expiration}s\n"
                f"Hora exacta: {signal.created_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            try:
                await self._send_message(chat_id=self.chat_id, text=text)
                self._sent_ids.add(signal.id)
                self._save_state()
                self.last_error = None
            except Exception:
                self.last_error = "No se pudo enviar la senal por Telegram."
                LOGGER.exception("No se pudo enviar la senal por Telegram")

    async def send_outcomes(self, records: List[SignalOutcome], *, virtual_balance: Optional[int] = None) -> None:
        async with self._state_lock:
            for record in sorted(records, key=lambda item: item.created_at):
                await self._send_outcome_unlocked(record)
            await self._send_due_summaries(virtual_balance=virtual_balance)

    async def send_outcome(self, record: SignalOutcome, *, virtual_balance: Optional[int] = None) -> None:
        async with self._state_lock:
            await self._send_outcome_unlocked(record)
            await self._send_due_summaries(virtual_balance=virtual_balance)

    async def send_balance_events(self, wallet: VirtualBalanceSummary) -> None:
        async with self._state_lock:
            if self._bot is None:
                return
            for event in wallet.history:
                if not self._is_balance_reset_event(event):
                    continue
                event_id = self._balance_event_id(event)
                if event_id in self._balance_event_sent_ids:
                    continue
                text = self._balance_event_text(event)
                try:
                    await self._send_message(chat_id=self.chat_id, text=text)
                    self._balance_event_sent_ids.add(event_id)
                    self._save_state()
                    self.last_error = None
                except Exception:
                    self.last_error = "No se pudo enviar el evento de saldo por Telegram."
                    LOGGER.exception("No se pudo enviar el evento de saldo por Telegram")
                    return

    def remember_signals(self, signal_ids: Iterable[str]) -> None:
        changed = False
        for signal_id in signal_ids:
            if signal_id and signal_id not in self._sent_ids:
                self._sent_ids.add(signal_id)
                changed = True
        if changed:
            self._save_state()

    def remember_outcomes(self, outcome_ids: Iterable[str]) -> None:
        changed = False
        for outcome_id in outcome_ids:
            if outcome_id and outcome_id not in self._result_sent_ids:
                self._result_sent_ids.add(outcome_id)
                changed = True
        if changed:
            self._save_state()

    def remember_balance_events(self, events: Iterable[BalanceEvent]) -> None:
        changed = False
        for event in events:
            if not self._is_balance_reset_event(event):
                continue
            event_id = self._balance_event_id(event)
            if event_id and event_id not in self._balance_event_sent_ids:
                self._balance_event_sent_ids.add(event_id)
                changed = True
        if changed:
            self._save_state()

    def pending_outcomes(self, records: Iterable[SignalOutcome]) -> List[SignalOutcome]:
        return [
            record
            for record in records
            if not record.is_shadow
            and record.status in {"win", "loss", "push", "aborted"}
            and record.id not in self._result_sent_ids
        ]

    async def _send_outcome_unlocked(self, record: SignalOutcome) -> None:
        if self._bot is None:
            return
        if record.id in self._result_sent_ids:
            return
        if record.is_shadow or record.status in {"waiting_entry", "pending"}:
            return

        text = self._outcome_text(record)
        try:
            await self._send_message(chat_id=self.chat_id, text=text)
            self._result_sent_ids.add(record.id)
            if not any(item.get("id") == record.id for item in self._summary_pending):
                self._summary_pending.append(self._summary_item(record))
            self._save_state()
            self.last_error = None
        except Exception:
            self.last_error = "No se pudo enviar el resultado por Telegram."
            LOGGER.exception("No se pudo enviar el resultado por Telegram")

    async def send_test(self) -> bool:
        if self._bot is None:
            return False
        try:
            await self._send_message(
                chat_id=self.chat_id,
                text=(
                    "TEST IQ Option Signals\n"
                    "Telegram configurado correctamente.\n"
                    "Este mensaje no es una senal de mercado."
                ),
            )
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc) or "No se pudo enviar el test por Telegram."
            LOGGER.exception("No se pudo enviar el test por Telegram")
            return False

    async def _send_message(self, **kwargs) -> None:
        if self._bot is None:
            return

        send_method = self._bot.send_message
        if asyncio.iscoroutinefunction(send_method):
            await send_method(**kwargs)
        else:
            await asyncio.to_thread(send_method, **kwargs)

    async def _send_due_summaries(self, *, virtual_balance: Optional[int] = None) -> None:
        if self._bot is None:
            return
        while len(self._summary_pending) >= SUMMARY_BATCH_SIZE:
            batch = self._summary_pending[:SUMMARY_BATCH_SIZE]
            text = self._summary_text(batch, self._summaries_sent + 1, virtual_balance=virtual_balance)
            try:
                await self._send_message(chat_id=self.chat_id, text=text)
                self._summaries_sent += 1
                self._summary_pending = self._summary_pending[SUMMARY_BATCH_SIZE:]
                self._save_state()
                self.last_error = None
            except Exception:
                self.last_error = "No se pudo enviar el resumen de 5 operaciones por Telegram."
                LOGGER.exception("No se pudo enviar el resumen de 5 operaciones por Telegram")
                return

    @staticmethod
    def _outcome_text(record: SignalOutcome) -> str:
        icon = "\u2705" if record.status == "win" else "\u274c" if record.status == "loss" else "\u26aa"
        label = TelegramNotifier._status_label(record.status)
        result_price = "-" if record.result_price is None else TelegramNotifier._format_price(record.result_price)
        resolved_at = record.resolved_at or datetime.now(record.created_at.tzinfo)
        return (
            f"{icon} RESULTADO: {label}\n"
            f"Activo: {record.asset}\n"
            f"Operacion: {record.direction}\n"
            f"Puntuacion: {record.score}/10\n"
            f"Entrada: {TelegramNotifier._format_price(record.entry_price)}\n"
            f"Salida: {result_price}\n"
            f"Motivo aborto: {record.abort_reason or '-'}\n"
            f"Expiracion: {record.suggested_expiration}s\n"
            f"Hora resultado: {resolved_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    @staticmethod
    def _summary_text(
        batch: List[Dict[str, object]],
        batch_number: int,
        *,
        virtual_balance: Optional[int] = None,
    ) -> str:
        wins = sum(1 for item in batch if item.get("status") == "win")
        losses = sum(1 for item in batch if item.get("status") == "loss")
        pushes = sum(1 for item in batch if item.get("status") == "push")
        lines = [
            f"RESUMEN CADA 5 OPERACIONES #{batch_number}",
            f"Ganadas: {wins}",
            f"Perdidas: {losses}",
            f"Empates: {pushes}",
        ]
        if virtual_balance is not None:
            lines.append(f"Saldo virtual: {TelegramNotifier._format_money(virtual_balance)}")
        lines.append("")
        for index, item in enumerate(batch, start=1):
            label = TelegramNotifier._status_label(str(item.get("status", "")))
            lines.append(
                f"Operacion {index}: {label} | {item.get('asset')} {item.get('direction')} "
                f"score {item.get('score')}/10"
            )
        return "\n".join(lines)

    @staticmethod
    def _summary_item(record: SignalOutcome) -> Dict[str, object]:
        return {
            "id": record.id,
            "asset": record.asset,
            "direction": record.direction,
            "score": record.score,
            "status": record.status,
            "created_at": record.created_at.isoformat(),
            "resolved_at": record.resolved_at.isoformat() if record.resolved_at else None,
            "entry_price": record.entry_price,
            "result_price": record.result_price,
        }

    @staticmethod
    def _is_balance_reset_event(event: BalanceEvent) -> bool:
        mark = event.mark.strip().upper()
        return mark.startswith("[QUIEBRA #") or mark.startswith("[META #")

    @staticmethod
    def _balance_event_id(event: BalanceEvent) -> str:
        return f"{event.timestamp.isoformat()}:{event.mark.strip().upper()}"

    @staticmethod
    def _balance_event_text(event: BalanceEvent) -> str:
        mark = event.mark.strip().upper()
        count = TelegramNotifier._mark_count(mark)
        if mark.startswith("[META #"):
            return (
                f"\u2705\u2705\u2705 META ALCANZADA \u2705\u2705\u2705\n"
                f"Metas totales: {count}\n"
                f"Saldo reiniciado: {TelegramNotifier._format_money(event.balance)}\n"
                f"Detalle: {event.note or 'Meta alcanzada.'}\n"
                f"Hora: {event.timestamp.astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        return (
            f"\u274c\u274c\u274c QUIEBRA DEL SISTEMA \u274c\u274c\u274c\n"
            f"Quiebras totales: {count}\n"
            f"Saldo reiniciado: {TelegramNotifier._format_money(event.balance)}\n"
            f"Detalle: {event.note or 'Saldo por debajo del umbral.'}\n"
            f"Hora: {event.timestamp.astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    @staticmethod
    def _mark_count(mark: str) -> int:
        try:
            return int(mark.split("#", 1)[1].split("]", 1)[0])
        except (IndexError, ValueError):
            return 0

    @staticmethod
    def _status_label(status: str) -> str:
        if status == "win":
            return "GANADA"
        if status == "loss":
            return "PERDIDA"
        if status == "push":
            return "EMPATE"
        if status == "aborted":
            return "ABORTADA"
        return "PENDIENTE"

    @staticmethod
    def _format_price(value: float) -> str:
        return f"{value:.4f}" if abs(value) > 10 else f"{value:.6f}"

    @staticmethod
    def _format_money(value: int) -> str:
        return f"${int(value):,}".replace(",", ".")

    def _load_state(self) -> None:
        if self.state_path is None:
            return
        if self.storage is not None:
            payload = self.storage.load_json(self.state_path.name, self.state_path)
            if payload is None:
                return
        elif not self.state_path.exists():
            return
        else:
            payload = StateStorage._load_local(self.state_path) or {}
        try:
            self._sent_ids = set(str(item) for item in payload.get("signal_sent_ids", []))
            self._result_sent_ids = set(str(item) for item in payload.get("result_sent_ids", []))
            self._balance_event_sent_ids = set(str(item) for item in payload.get("balance_event_sent_ids", []))
            self._summary_pending = [
                item for item in payload.get("summary_pending", []) if isinstance(item, dict) and item.get("id")
            ]
            self._summaries_sent = int(payload.get("summaries_sent", 0))
        except Exception:
            LOGGER.exception("No se pudo cargar el estado de notificaciones Telegram")

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        payload = {
            "signal_sent_ids": sorted(self._sent_ids),
            "result_sent_ids": sorted(self._result_sent_ids),
            "balance_event_sent_ids": sorted(self._balance_event_sent_ids),
            "summary_pending": self._summary_pending,
            "summaries_sent": self._summaries_sent,
        }
        if self.storage is not None:
            self.storage.save_json(self.state_path.name, self.state_path, payload)
        else:
            StateStorage._write_local(self.state_path, payload)
