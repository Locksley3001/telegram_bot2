from __future__ import annotations

import logging
from typing import Optional, Set

from telegram import Bot

from app.models import Signal

LOGGER = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self.token = token
        self.chat_id = chat_id
        self._bot: Optional[Bot] = Bot(token=token) if token and chat_id else None
        self._sent_ids: Set[str] = set()

    @property
    def enabled(self) -> bool:
        return self._bot is not None

    async def send_signal(self, signal: Signal) -> None:
        if self._bot is None or signal.id in self._sent_ids:
            return

        icon = "🟢" if signal.direction == "CALL" else "🔴"
        text = (
            f"{icon} {signal.direction}\n"
            f"Activo: {signal.asset}\n"
            f"Puntuación: {signal.score}/10\n"
            f"Fuerza: {signal.strength:.1f}/10\n"
            f"Continuidad: {signal.continuity:.1f}/10\n"
            f"Cansancio: {signal.exhaustion:.1f}/10\n"
            f"Razón principal: {signal.main_reason}\n"
            f"Expiración sugerida: {signal.suggested_expiration}s\n"
            f"Hora exacta: {signal.created_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        try:
            await self._bot.send_message(chat_id=self.chat_id, text=text)
            self._sent_ids.add(signal.id)
        except Exception:
            LOGGER.exception("No se pudo enviar la senal por Telegram")
