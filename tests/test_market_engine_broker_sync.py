from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace

from app.market_engine import MarketEngine
from app.models import Signal, utc_now


class FakeTradeExecutor:
    def __init__(self, *, enabled: bool = True, entry_window_seconds: float = 12.0) -> None:
        self.enabled = enabled
        self.entry_window_seconds = entry_window_seconds
        self.calls: list[tuple[str, int]] = []

    async def execute_due(self, asset, records, broker):
        records = list(records)
        self.calls.append((asset, len(records)))
        return []


def make_signal(*, pending_delta_seconds: float = 5.0) -> Signal:
    now = utc_now()
    return Signal(
        id=f"EURUSD-OTC:60:CALL:test:{int(now.timestamp())}",
        asset="EURUSD-OTC",
        direction="CALL",
        score=8,
        grade="valid",
        strength=2.0,
        continuity=2.0,
        exhaustion=1.0,
        cci=-120.0,
        main_reason="test",
        suggested_expiration=60,
        created_at=now,
        price=1.1,
        timeframe=60,
        factor_score=4,
        confidence="low",
        stake_amount=10000,
        pending_execution_at=now + timedelta(seconds=pending_delta_seconds),
    )


class MarketEngineBrokerSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_due_broker_trades_checks_all_markets(self) -> None:
        engine = object.__new__(MarketEngine)
        engine.trade_executor = FakeTradeExecutor(enabled=True)
        engine.performance = SimpleNamespace(records={"one": object(), "two": object()})
        engine.broker = object()

        await engine._execute_due_broker_trades(["EURUSD-OTC", "GBPUSD-OTC"])

        self.assertEqual(
            engine.trade_executor.calls,
            [("EURUSD-OTC", 2), ("GBPUSD-OTC", 2)],
        )

    async def test_execute_due_broker_trades_skips_when_disabled(self) -> None:
        engine = object.__new__(MarketEngine)
        engine.trade_executor = FakeTradeExecutor(enabled=False)
        engine.performance = SimpleNamespace(records={"one": object()})
        engine.broker = object()

        await engine._execute_due_broker_trades(["EURUSD-OTC"])

        self.assertEqual(engine.trade_executor.calls, [])

    def test_can_emit_rejects_stale_signal_when_broker_enabled(self) -> None:
        engine = object.__new__(MarketEngine)
        engine.settings = SimpleNamespace(virtual_cautious_stake=10000, signal_cooldown_seconds=45)
        engine.trade_executor = FakeTradeExecutor(enabled=True, entry_window_seconds=12.0)
        engine._emitted_signal_ids = set()
        engine._last_signal_at = {}

        signal = make_signal(pending_delta_seconds=-20.0)

        self.assertFalse(engine._can_emit(signal))

    def test_can_emit_allows_stale_signal_when_broker_disabled(self) -> None:
        engine = object.__new__(MarketEngine)
        engine.settings = SimpleNamespace(virtual_cautious_stake=10000, signal_cooldown_seconds=45)
        engine.trade_executor = FakeTradeExecutor(enabled=False, entry_window_seconds=12.0)
        engine._emitted_signal_ids = set()
        engine._last_signal_at = {}

        signal = make_signal(pending_delta_seconds=-20.0)

        self.assertTrue(engine._can_emit(signal))


if __name__ == "__main__":
    unittest.main()
