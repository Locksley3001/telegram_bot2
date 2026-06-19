from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace

from app.market_engine import MarketEngine
from app.models import BrokerTrade, Signal, SignalOutcome, utc_now


class FakeTradeExecutor:
    def __init__(self, *, enabled: bool = True, entry_window_seconds: float = 12.0) -> None:
        self.enabled = enabled
        self.entry_window_seconds = entry_window_seconds
        self.calls: list[tuple[str, int]] = []
        self.trades = {}

    async def execute_due(self, asset, records, broker):
        records = list(records)
        self.calls.append((asset, len(records)))
        return []


class PlacingTradeExecutor(FakeTradeExecutor):
    async def execute_due(self, asset, records, broker):
        records = list(records)
        self.calls.append((asset, [record.id for record in records]))
        trade = BrokerTrade(
            signal_id=records[0].id,
            status="placed",
            asset=records[0].asset,
            direction=records[0].direction,
            stake_amount=records[0].stake_amount,
            expiration_seconds=records[0].suggested_expiration,
            balance_mode="PRACTICE",
            requested_at=utc_now(),
            placed_at=utc_now(),
        )
        self.trades[trade.signal_id] = trade
        return [trade]


class FakePerformance:
    def __init__(self, record: SignalOutcome) -> None:
        self.records = {record.id: record}
        self.aborted: list[tuple[str, str]] = []

    def abort_record(self, record_id: str, reason: str):
        self.aborted.append((record_id, reason))
        record = self.records[record_id]
        record.status = "aborted"
        record.abort_reason = reason
        return record


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


def make_outcome(signal: Signal) -> SignalOutcome:
    entry_at = signal.pending_execution_at or signal.created_at
    return SignalOutcome(
        id=signal.id,
        asset=signal.asset,
        direction=signal.direction,
        score=signal.score,
        strength=signal.strength,
        continuity=signal.continuity,
        exhaustion=signal.exhaustion,
        cci=signal.cci,
        entry_price=0.0,
        entry_at=entry_at,
        stake_amount=signal.stake_amount,
        payout_rate=0.85,
        status="pending",
        timeframe=signal.timeframe,
        suggested_expiration=signal.suggested_expiration,
        created_at=signal.created_at,
        expires_at=entry_at + timedelta(seconds=signal.suggested_expiration),
        main_reason=signal.main_reason,
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

    async def test_execute_signal_at_entry_uses_same_history_record(self) -> None:
        signal = make_signal(pending_delta_seconds=-0.1)
        record = make_outcome(signal)
        engine = object.__new__(MarketEngine)
        engine.settings = SimpleNamespace(poll_interval_seconds=0.01)
        engine.trade_executor = PlacingTradeExecutor(enabled=True, entry_window_seconds=12.0)
        engine.performance = SimpleNamespace(records={record.id: record})
        engine.broker = object()

        await engine._execute_signal_at_entry("EURUSD-OTC", record.id)

        self.assertEqual(engine.trade_executor.calls, [("EURUSD-OTC", [record.id])])

    async def test_execute_signal_at_entry_aborts_when_broker_misses_window(self) -> None:
        signal = make_signal(pending_delta_seconds=-2.0)
        record = make_outcome(signal)
        record.status = "pending"
        engine = object.__new__(MarketEngine)
        engine.settings = SimpleNamespace(poll_interval_seconds=0.01)
        engine.trade_executor = FakeTradeExecutor(enabled=True, entry_window_seconds=0.5)
        engine.performance = FakePerformance(record)
        engine.broker = object()

        await engine._execute_signal_at_entry("EURUSD-OTC", record.id)

        self.assertEqual(record.status, "aborted")
        self.assertIn("BROKER NO EJECUTO", record.abort_reason)


if __name__ == "__main__":
    unittest.main()
