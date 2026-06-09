from __future__ import annotations

import unittest

from app.telegram_notifier import TelegramNotifier


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


if __name__ == "__main__":
    unittest.main()
