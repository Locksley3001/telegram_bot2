from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace

from app.market_engine import MarketEngine
from app.models import BrokerTrade, Candle, Signal, SignalOutcome, utc_now


class FakeTradeExecutor:
    def __init__(self, *, enabled: bool = True, entry_window_seconds: float = 12.0) -> None:
        self.enabled = enabled
        self.entry_window_seconds = entry_window_seconds
        self.calls: list[tuple[str, int]] = []
        self.all_due_calls: list[list[str]] = []
        self.trades = {}

    async def execute_due(self, asset, records, broker):
        records = list(records)
        self.calls.append((asset, len(records)))
        return []

    async def execute_all_due(self, records, broker):
        records = list(records)
        self.all_due_calls.append([record.id for record in records])
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
    def __init__(self, record: SignalOutcome, *, entry_status: str = "pending") -> None:
        self.records = {record.id: record}
        self.entry_status = entry_status
        self.evaluate_calls = 0

    def evaluate(self, asset, candles, abort_checker=None):
        self.evaluate_calls += 1
        record = next(iter(self.records.values()))
        record.status = self.entry_status
        if self.entry_status == "aborted":
            record.abort_reason = "apertura abortada"
        return [record] if self.entry_status == "aborted" else []


class FakeBroker:
    connected = True

    async def get_realtime_candles(self, asset, timeframe):
        now = utc_now().timestamp()
        return [
            Candle(timestamp=now - 180, open=1.0, high=1.1, low=0.9, close=1.0, is_closed=True),
            Candle(timestamp=now - 120, open=1.0, high=1.1, low=0.9, close=1.0, is_closed=True),
            Candle(timestamp=now - 60, open=1.0, high=1.1, low=0.9, close=1.0, is_closed=True),
            Candle(timestamp=now, open=1.0, high=1.1, low=0.9, close=1.0, is_closed=False),
        ]

    async def get_candles(self, asset, timeframe, count):
        return await self.get_realtime_candles(asset, timeframe)


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
    async def test_execute_due_broker_trades_sends_all_active_markets_as_one_batch(self) -> None:
        engine = object.__new__(MarketEngine)
        engine.trade_executor = FakeTradeExecutor(enabled=True)
        engine.performance = SimpleNamespace(
            records={
                "one": SimpleNamespace(id="one", asset="EURUSD-OTC"),
                "two": SimpleNamespace(id="two", asset="GBPUSD-OTC"),
                "ignored": SimpleNamespace(id="ignored", asset="SOLUSD-OTC"),
            }
        )
        engine.broker = object()

        await engine._execute_due_broker_trades(["EURUSD-OTC", "GBPUSD-OTC"])

        self.assertEqual(engine.trade_executor.calls, [])
        self.assertEqual(engine.trade_executor.all_due_calls, [["one", "two"]])

    async def test_execute_due_broker_trades_skips_when_disabled(self) -> None:
        engine = object.__new__(MarketEngine)
        engine.trade_executor = FakeTradeExecutor(enabled=False)
        engine.performance = SimpleNamespace(records={"one": object()})
        engine.broker = object()

        await engine._execute_due_broker_trades(["EURUSD-OTC"])

        self.assertEqual(engine.trade_executor.calls, [])

    def test_can_emit_does_not_reject_stale_signal_because_broker_is_enabled(self) -> None:
        engine = object.__new__(MarketEngine)
        engine.settings = SimpleNamespace(virtual_cautious_stake=10000, signal_cooldown_seconds=45)
        engine.trade_executor = FakeTradeExecutor(enabled=True, entry_window_seconds=12.0)
        engine._emitted_signal_ids = set()
        engine._last_signal_at = {}

        signal = make_signal(pending_delta_seconds=-20.0)

        self.assertTrue(engine._can_emit(signal))

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

    async def test_execute_signal_at_entry_waits_for_virtual_entry_confirmation(self) -> None:
        signal = make_signal(pending_delta_seconds=-0.1)
        record = make_outcome(signal)
        record.status = "waiting_entry"
        engine = object.__new__(MarketEngine)
        engine.settings = SimpleNamespace(poll_interval_seconds=0.01)
        engine.trade_executor = PlacingTradeExecutor(enabled=True, entry_window_seconds=8.0)
        engine.performance = FakePerformance(record, entry_status="pending")
        engine.analyzer = SimpleNamespace(abort_pending_reason=lambda record, candles: None)
        engine.broker = FakeBroker()

        await engine._execute_signal_at_entry("EURUSD-OTC", record.id)

        self.assertEqual(engine.performance.evaluate_calls, 1)
        self.assertEqual(engine.trade_executor.calls, [("EURUSD-OTC", [record.id])])

    async def test_execute_signal_at_entry_skips_broker_when_virtual_aborts(self) -> None:
        signal = make_signal(pending_delta_seconds=-0.1)
        record = make_outcome(signal)
        record.status = "waiting_entry"
        engine = object.__new__(MarketEngine)
        engine.settings = SimpleNamespace(poll_interval_seconds=0.01)
        engine.trade_executor = FakeTradeExecutor(enabled=True, entry_window_seconds=8.0)
        engine.performance = FakePerformance(record, entry_status="aborted")
        engine.analyzer = SimpleNamespace(abort_pending_reason=lambda record, candles: "apertura abortada")
        engine.broker = FakeBroker()

        await engine._execute_signal_at_entry("EURUSD-OTC", record.id)

        self.assertEqual(record.status, "aborted")
        self.assertEqual(engine.trade_executor.calls, [])


if __name__ == "__main__":
    unittest.main()
