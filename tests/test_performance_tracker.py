from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from app.models import Candle, SignalOutcome, utc_now
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

    def test_target_reset_activates_post_target_consolidation(self) -> None:
        tracker = PerformanceTracker(self.path)
        tracker.records = {
            "target": make_outcome(1, status="win", stake_amount=530000),
        }

        wallet = tracker.virtual_balance()

        self.assertEqual(wallet.balance, 50000)
        self.assertEqual(wallet.targets_hit, 1)
        self.assertEqual(wallet.last_reset_reason, "target")
        self.assertTrue(wallet.post_target_consolidation)

    def test_post_target_consolidation_stops_after_two_consecutive_wins(self) -> None:
        tracker = PerformanceTracker(self.path)
        tracker.records = {
            "target": make_outcome(1, status="win", stake_amount=530000),
            "win-2": make_outcome(2, status="win", stake_amount=10000),
            "win-3": make_outcome(3, status="win", stake_amount=10000),
        }

        wallet = tracker.virtual_balance()

        self.assertFalse(wallet.post_target_consolidation)
        self.assertEqual(wallet.post_target_consecutive_wins, 2)
        self.assertEqual(wallet.operations_since_reset, 2)

    def test_bankruptcy_reset_does_not_activate_post_target_consolidation(self) -> None:
        tracker = PerformanceTracker(self.path)
        tracker.records = {
            "target": make_outcome(1, status="win", stake_amount=530000),
            "loss-2": make_outcome(2, stake_amount=50000),
        }

        wallet = tracker.virtual_balance()

        self.assertEqual(wallet.last_reset_reason, "bankruptcy")
        self.assertFalse(wallet.post_target_consolidation)

    def test_virtual_balance_uses_configured_amounts(self) -> None:
        tracker = PerformanceTracker(
            self.path,
            initial_balance=60000,
            target_balance=120000,
            cautious_stake=15000,
            safe_stake=30000,
            payout_rate=0.80,
        )
        tracker.records = {
            "loss-1": make_outcome(1, stake_amount=30000),
            "loss-2": make_outcome(2, stake_amount=30000),
        }

        wallet = tracker.virtual_balance()

        self.assertEqual(wallet.initial_balance, 60000)
        self.assertEqual(wallet.target_balance, 120000)
        self.assertEqual(wallet.cautious_stake, 15000)
        self.assertEqual(wallet.safe_stake, 30000)
        self.assertEqual(wallet.balance, 60000)
        self.assertEqual(wallet.bankruptcies, 1)
        self.assertIn("$15.000", wallet.history[-1].note)

    def test_custom_target_resets_to_custom_initial_balance(self) -> None:
        tracker = PerformanceTracker(
            self.path,
            initial_balance=60000,
            target_balance=90000,
            cautious_stake=15000,
            safe_stake=30000,
            payout_rate=0.80,
        )
        tracker.records = {
            "win-1": make_outcome(1, status="win", stake_amount=40000),
        }

        wallet = tracker.virtual_balance()

        self.assertEqual(wallet.balance, 60000)
        self.assertEqual(wallet.targets_hit, 1)
        self.assertEqual(wallet.last_reset_reason, "target")

    def test_evaluate_resolves_with_entry_candle_close_for_one_minute_trade(self) -> None:
        tracker = PerformanceTracker(self.path)
        now = utc_now().replace(microsecond=0)
        entry_at = now - timedelta(seconds=120)
        record = SignalOutcome.model_validate(
            {
                "id": "BTCUSD-OTC:60:CALL:test",
                "asset": "BTCUSD-OTC",
                "direction": "CALL",
                "score": 8,
                "strength": 2.0,
                "continuity": 2.0,
                "exhaustion": 1.0,
                "cci": -120.0,
                "entry_price": 100.0,
                "entry_at": entry_at,
                "stake_amount": 10000,
                "payout_rate": 0.85,
                "result_price": None,
                "status": "pending",
                "timeframe": 60,
                "suggested_expiration": 60,
                "created_at": entry_at - timedelta(seconds=30),
                "expires_at": entry_at + timedelta(seconds=60),
                "resolved_at": None,
                "main_reason": "test",
                "balance_after": None,
                "abort_reason": "",
                "is_shadow": False,
                "blocked_reason": "",
            }
        )
        tracker.records = {record.id: record}
        candles = [
            Candle(timestamp=entry_at.timestamp(), open=100, high=101, low=98, close=99, is_closed=True),
            Candle(timestamp=(entry_at + timedelta(seconds=60)).timestamp(), open=99, high=104, low=99, close=103, is_closed=True),
        ]

        resolved = tracker.evaluate("BTCUSD-OTC", candles)

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].result_price, 99)
        self.assertEqual(resolved[0].status, "loss")

    def test_abort_record_removes_pending_trade_from_trainable_results(self) -> None:
        tracker = PerformanceTracker(self.path)
        record = make_outcome(1, status="pending")
        record.result_price = None
        record.resolved_at = None
        tracker.records = {record.id: record}

        aborted = tracker.abort_record(record.id, "BROKER NO EJECUTO")

        self.assertIsNotNone(aborted)
        self.assertEqual(record.status, "aborted")
        self.assertEqual(record.result_price, record.entry_price)
        self.assertIn("BROKER NO EJECUTO", record.abort_reason)
        self.assertEqual(tracker.summary().resolved, 0)


if __name__ == "__main__":
    unittest.main()
