from __future__ import annotations

import unittest
from datetime import timedelta

from app.market_engine import MarketEngine
from app.models import Signal, VirtualBalanceSummary, utc_now


class FakePerformance:
    def __init__(self, wallet: VirtualBalanceSummary) -> None:
        self.wallet = wallet

    def virtual_balance(self, *, timeframe: int = 60) -> VirtualBalanceSummary:
        return self.wallet


def make_engine(wallet: VirtualBalanceSummary) -> MarketEngine:
    engine = MarketEngine.__new__(MarketEngine)
    engine.performance = FakePerformance(wallet)
    engine.timeframe = 60
    return engine


def make_signal(factor_score: int) -> Signal:
    created_at = utc_now()
    return Signal(
        id=f"signal:{factor_score}",
        asset="EURUSD-OTC",
        direction="CALL",
        score=8,
        grade="valid",
        strength=5.0,
        continuity=5.0,
        exhaustion=5.0,
        cci=-120.0,
        main_reason="test",
        suggested_expiration=60,
        created_at=created_at,
        price=1.0,
        timeframe=60,
        factor_score=factor_score,
        confidence="high" if factor_score >= 4 else "low",
        stake_amount=20000 if factor_score >= 4 else 10000,
        pending_execution_at=created_at + timedelta(seconds=60),
    )


class BalanceRulesTests(unittest.TestCase):
    def test_two_losses_block_low_confidence_signal(self) -> None:
        wallet = VirtualBalanceSummary(consecutive_losses=2, high_confidence_threshold=4)
        engine = make_engine(wallet)
        signal = make_signal(3)
        context = {"factor_score": 3}

        approved, updated = engine._apply_balance_rules(signal, context)

        self.assertIsNone(approved)
        self.assertEqual(updated["stake_amount"], 0)
        self.assertEqual(updated["confidence"], "discarded")
        self.assertIn("racha de perdidas activa", updated["reason"])
        self.assertIs(updated["shadow_signal"], signal)

    def test_two_losses_allow_high_confidence_with_capped_stake(self) -> None:
        wallet = VirtualBalanceSummary(consecutive_losses=2, high_confidence_threshold=4)
        engine = make_engine(wallet)
        signal = make_signal(4)
        context = {"factor_score": 4}

        approved, updated = engine._apply_balance_rules(signal, context)

        self.assertIs(approved, signal)
        self.assertEqual(signal.stake_amount, 10000)
        self.assertEqual(signal.confidence, "low")
        self.assertEqual(updated["stake_amount"], 10000)
        self.assertIn("racha de perdidas", updated["market_message"])


if __name__ == "__main__":
    unittest.main()
