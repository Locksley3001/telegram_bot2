from __future__ import annotations

import asyncio
import unittest

from app.models import BalanceEvent, VirtualBalanceSummary, utc_now
from app.telegram_notifier import TelegramNotifier


class FakeBot:
    def __init__(self) -> None:
        self.messages = []

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


class TelegramNotifierTests(unittest.TestCase):
    def test_summary_text_includes_virtual_balance_when_available(self) -> None:
        batch = [
            {"status": "win", "asset": "EURUSD-OTC", "direction": "CALL", "score": 8},
            {"status": "loss", "asset": "GBPUSD-OTC", "direction": "PUT", "score": 7},
            {"status": "push", "asset": "BTCUSD-OTC", "direction": "CALL", "score": 9},
            {"status": "win", "asset": "ETHUSD-OTC", "direction": "PUT", "score": 8},
            {"status": "loss", "asset": "USDJPY-OTC", "direction": "CALL", "score": 6},
        ]

        text = TelegramNotifier._summary_text(batch, 3, virtual_balance=50000)

        self.assertIn("RESUMEN CADA 5 OPERACIONES #3", text)
        self.assertIn("Ganadas: 2", text)
        self.assertIn("Perdidas: 2", text)
        self.assertIn("Empates: 1", text)
        self.assertIn("Saldo virtual: $50.000", text)

    def test_summary_text_works_without_virtual_balance_for_older_calls(self) -> None:
        batch = [
            {"status": "win", "asset": "EURUSD-OTC", "direction": "CALL", "score": 8},
        ]

        text = TelegramNotifier._summary_text(batch, 1)

        self.assertNotIn("Saldo virtual:", text)

    def test_balance_event_text_includes_target_count_with_green_checks(self) -> None:
        event = BalanceEvent(
            timestamp=utc_now(),
            mark="[META #3]",
            result="reinicio",
            profit=0,
            balance=50000,
            note="Meta alcanzada; patrones ganadores consolidados antes de reiniciar.",
        )

        text = TelegramNotifier._balance_event_text(event)

        self.assertIn("\u2705\u2705\u2705 META ALCANZADA \u2705\u2705\u2705", text)
        self.assertIn("Metas totales: 3", text)
        self.assertIn("Saldo reiniciado: $50.000", text)

    def test_balance_event_text_includes_bankruptcy_count_with_red_crosses(self) -> None:
        event = BalanceEvent(
            timestamp=utc_now(),
            mark="[QUIEBRA #2]",
            result="reinicio",
            profit=50000,
            balance=50000,
            note="Saldo quedo por debajo de $10.000; reinicio y proteccion reforzada.",
        )

        text = TelegramNotifier._balance_event_text(event)

        self.assertIn("\u274c\u274c\u274c QUIEBRA DEL SISTEMA \u274c\u274c\u274c", text)
        self.assertIn("Quiebras totales: 2", text)
        self.assertIn("Saldo reiniciado: $50.000", text)

    def test_send_balance_events_does_not_duplicate_events(self) -> None:
        bot = FakeBot()
        notifier = TelegramNotifier("123:ABC", "chat")
        notifier._bot = bot
        wallet = VirtualBalanceSummary(
            history=[
                BalanceEvent(timestamp=utc_now(), mark="GANANCIA", result="ganada", profit=8500, balance=58500),
                BalanceEvent(timestamp=utc_now(), mark="[META #1]", result="reinicio", profit=0, balance=50000),
            ]
        )

        async def run_test() -> None:
            await notifier.send_balance_events(wallet)
            await notifier.send_balance_events(wallet)

        asyncio.run(run_test())

        self.assertEqual(len(bot.messages), 1)
        self.assertIn("Metas totales: 1", bot.messages[0]["text"])

    def test_remember_balance_events_prevents_historical_send(self) -> None:
        bot = FakeBot()
        notifier = TelegramNotifier("123:ABC", "chat")
        notifier._bot = bot
        event = BalanceEvent(timestamp=utc_now(), mark="[QUIEBRA #1]", result="reinicio", profit=50000, balance=50000)
        wallet = VirtualBalanceSummary(history=[event])

        notifier.remember_balance_events(wallet.history)

        async def run_test() -> None:
            await notifier.send_balance_events(wallet)

        asyncio.run(run_test())

        self.assertEqual(bot.messages, [])


if __name__ == "__main__":
    unittest.main()
