from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from app.models import SignalOutcome, utc_now
from app.performance_tracker import PerformanceTracker


def make_outcome(index: int, *, status: str = "loss", stake_amount: int = 10000) -> SignalOutcome:
    resolved_at = utc_now() + timedelta(seconds=index)
    return SignalOutcome.model_validate(
        {
            "id": f"record:{index}",
            "asset": "EURUSD-OTC",
            "direction": "CALL",
            "score": 8,
            "strength": 2.0,
            "continuity": 2.0,
            "exhaustion": 1.0,
            "cci": -120.0,
            "entry_price": 1.1,
            "entry_at": resolved_at - timedelta(seconds=60),
            "stake_amount": stake_amount,
            "payout_rate": 0.85,
            "result_price": 1.2 if status == "win" else 1.0,
            "status": status,
            "timeframe": 60,
            "suggested_expiration": 60,
            "created_at": resolved_at - timedelta(seconds=120),
            "expires_at": resolved_at,
            "resolved_at": resolved_at,
            "main_reason": "test",
            "balance_after": None,
            "abort_reason": "",
            "is_shadow": False,
            "blocked_reason": "",
        }
    )


class PerformanceTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "performance.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_balance_at_ten_thousand_is_not_bankruptcy_yet(self) -> None:
        tracker = PerformanceTracker(self.path)
        tracker.records = {
            "loss-1": make_outcome(1, stake_amount=20000),
            "loss-2": make_outcome(2, stake_amount=20000),
        }

        wallet = tracker.virtual_balance()

        self.assertEqual(wallet.balance, 10000)
        self.assertEqual(wallet.bankruptcies, 0)

    def test_balance_below_ten_thousand_counts_as_bankruptcy(self) -> None:
        tracker = PerformanceTracker(self.path)
        tracker.records = {
            "loss-1": make_outcome(1, stake_amount=20000),
            "loss-2": make_outcome(2, stake_amount=20000),
            "win-3": make_outcome(3, status="win", stake_amount=10000),
            "loss-4": make_outcome(4, stake_amount=10000),
        }

        wallet = tracker.virtual_balance()

        self.assertEqual(wallet.balance, 50000)
        self.assertEqual(wallet.bankruptcies, 1)
        self.assertEqual(wallet.consecutive_losses, 0)
        self.assertEqual(wallet.history[-1].mark, "[QUIEBRA #1]")
        self.assertIn("debajo de $10.000", wallet.history[-1].note)


if __name__ == "__main__":
    unittest.main()
