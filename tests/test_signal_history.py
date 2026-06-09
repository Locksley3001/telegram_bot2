from __future__ import annotations

import unittest
from datetime import timedelta

from app.market_engine import MarketEngine
from app.models import Signal, utc_now


def make_signal(index: int) -> Signal:
    created_at = utc_now() + timedelta(seconds=index)
    return Signal(
        id=f"signal:{index}",
        asset="EURUSD-OTC",
        direction="CALL",
        score=8,
        grade="valid",
        strength=5.0,
        continuity=5.0,
        exhaustion=5.0,
        cci=120.0,
        main_reason="test",
        suggested_expiration=60,
        created_at=created_at,
        price=1.0,
        timeframe=60,
        stake_amount=10000,
        pending_execution_at=created_at + timedelta(seconds=60),
    )


class SignalHistoryTests(unittest.TestCase):
    def test_visual_history_rolls_to_500_without_counting_performance_records(self) -> None:
        engine = MarketEngine.__new__(MarketEngine)
        engine._signal_history_limit = 500
        engine.signals = [make_signal(index) for index in range(700)]

        trimmed = engine._trim_signal_history(engine.signals)
        engine.signals = trimmed

        self.assertEqual(len(trimmed), 500)
        self.assertEqual(trimmed[0].id, "signal:200")
        self.assertEqual(trimmed[-1].id, "signal:699")
        self.assertEqual(engine._signal_history_total(), 500)
        self.assertEqual(len(engine._combined_signal_history(limit=500)), 500)


if __name__ == "__main__":
    unittest.main()
