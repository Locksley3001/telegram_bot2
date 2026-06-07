from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from telegram import Bot

from app.models import Signal, SignalOutcome

LOGGER = logging.getLogger(__name__)
SUMMARY_BATCH_SIZE = 5


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, state_path: Optional[Path] = None) -> None:
        self.token = token
        self.chat_id = chat_id
        self.state_path = state_path
        self._bot: Optional[Bot] = Bot(token=token) if token and chat_id else None
        self._sent_ids: Set[str] = set()
        self._result_sent_ids: Set[str] = set()
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
            if self._bot is None or signal.id in self._sent_ids:
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

    async def send_outcomes(self, records: List[SignalOutcome]) -> None:
        async with self._state_lock:
            for record in sorted(records, key=lambda item: item.created_at):
                await self._send_outcome_unlocked(record)
            await self._send_due_summaries()

    async def send_outcome(self, record: SignalOutcome) -> None:
        async with self._state_lock:
            await self._send_outcome_unlocked(record)
            await self._send_due_summaries()

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

    def pending_outcomes(self, records: Iterable[SignalOutcome]) -> List[SignalOutcome]:
        return [
            record
            for record in records
            if record.status in {"win", "loss", "push", "aborted"} and record.id not in self._result_sent_ids
        ]

    async def _send_outcome_unlocked(self, record: SignalOutcome) -> None:
        if self._bot is None:
            return
        if record.id in self._result_sent_ids:
            return
        if record.status in {"waiting_entry", "pending"}:
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

    async def _send_due_summaries(self) -> None:
        if self._bot is None:
            return
        while len(self._summary_pending) >= SUMMARY_BATCH_SIZE:
            batch = self._summary_pending[:SUMMARY_BATCH_SIZE]
            text = self._summary_text(batch, self._summaries_sent + 1)
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
    def _summary_text(batch: List[Dict[str, object]], batch_number: int) -> str:
        wins = sum(1 for item in batch if item.get("status") == "win")
        losses = sum(1 for item in batch if item.get("status") == "loss")
        pushes = sum(1 for item in batch if item.get("status") == "push")
        lines = [
            f"RESUMEN CADA 5 OPERACIONES #{batch_number}",
            f"Ganadas: {wins}",
            f"Perdidas: {losses}",
            f"Empates: {pushes}",
            "",
        ]
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

    def _load_state(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._sent_ids = set(str(item) for item in payload.get("signal_sent_ids", []))
            self._result_sent_ids = set(str(item) for item in payload.get("result_sent_ids", []))
            self._summary_pending = [
                item for item in payload.get("summary_pending", []) if isinstance(item, dict) and item.get("id")
            ]
            self._summaries_sent = int(payload.get("summaries_sent", 0))
        except Exception:
            LOGGER.exception("No se pudo cargar el estado de notificaciones Telegram")

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "signal_sent_ids": sorted(self._sent_ids),
            "result_sent_ids": sorted(self._result_sent_ids),
            "summary_pending": self._summary_pending,
            "summaries_sent": self._summaries_sent,
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        temp_path = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
        backup_path = self.state_path.with_suffix(f"{self.state_path.suffix}.bak")
        temp_path.write_text(encoded, encoding="utf-8")
        if self.state_path.exists():
            backup_path.write_text(self.state_path.read_text(encoding="utf-8"), encoding="utf-8")
        temp_path.replace(self.state_path)
