from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace

from app.learning import LearningDecision
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
    engine.settings = SimpleNamespace(
        advantage_filter_enabled=True,
        advantage_filter_min_win_rate=60.0,
        advantage_filter_min_samples=30,
        advantage_filter_min_factor_score=4,
    )
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

    def test_post_target_consolidation_blocks_lateral_market(self) -> None:
        wallet = VirtualBalanceSummary(post_target_consolidation=True, high_confidence_threshold=4)
        engine = make_engine(wallet)
        signal = make_signal(4)
        context = {"factor_score": 4, "trend_label": "lateral"}

        approved, updated = engine._apply_balance_rules(signal, context)

        self.assertIsNone(approved)
        self.assertEqual(updated["stake_amount"], 0)
        self.assertIn("consolidacion post-meta", updated["reason"])

    def test_post_target_consolidation_blocks_below_high_confidence(self) -> None:
        wallet = VirtualBalanceSummary(post_target_consolidation=True, high_confidence_threshold=4)
        engine = make_engine(wallet)
        signal = make_signal(3)
        context = {"factor_score": 3}

        approved, updated = engine._apply_balance_rules(signal, context)

        self.assertIsNone(approved)
        self.assertEqual(updated["confidence"], "discarded")
        self.assertIn("alta confianza 4/6", updated["reason"])

    def test_post_target_consolidation_allows_high_confidence_with_capped_stake(self) -> None:
        wallet = VirtualBalanceSummary(post_target_consolidation=True, high_confidence_threshold=4)
        engine = make_engine(wallet)
        signal = make_signal(4)
        context = {"factor_score": 4}

        approved, updated = engine._apply_balance_rules(signal, context)

        self.assertIs(approved, signal)
        self.assertEqual(signal.stake_amount, 10000)
        self.assertEqual(updated["stake_amount"], 10000)
        self.assertIn("consolidacion post-meta", updated["market_message"])

    def test_advantage_filter_blocks_when_not_enough_samples(self) -> None:
        engine = make_engine(VirtualBalanceSummary())
        signal = make_signal(4)
        decision = LearningDecision(True, 0.70, 12, "test")

        reason = engine._advantage_filter_block_reason(signal, decision)

        self.assertIn("12 muestras similares", reason)

    def test_advantage_filter_blocks_when_estimate_is_below_target(self) -> None:
        engine = make_engine(VirtualBalanceSummary())
        signal = make_signal(4)
        decision = LearningDecision(True, 0.58, 50, "test")

        reason = engine._advantage_filter_block_reason(signal, decision)

        self.assertIn("historico 58.0%", reason)

    def test_advantage_filter_allows_when_edge_is_strong_enough(self) -> None:
        engine = make_engine(VirtualBalanceSummary())
        signal = make_signal(4)
        decision = LearningDecision(True, 0.61, 50, "test")

        reason = engine._advantage_filter_block_reason(signal, decision)

        self.assertEqual(reason, "")

    def test_advantage_filter_can_be_disabled(self) -> None:
        engine = make_engine(VirtualBalanceSummary())
        engine.settings.advantage_filter_enabled = False
        signal = make_signal(1)
        decision = LearningDecision(True, 0.30, 1, "test")

        reason = engine._advantage_filter_block_reason(signal, decision)

        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()
