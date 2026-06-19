from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from app.broker_trade_executor import BrokerTradeExecutor
from app.models import SignalOutcome, utc_now


class FakeBroker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, int]] = []
        self.connected = True

    async def place_option_trade(self, asset: str, direction: str, amount: int, expiration_seconds: int) -> tuple[bool, str]:
        self.calls.append((asset, direction, amount, expiration_seconds))
        return True, f"order-{len(self.calls)}"


class FlakyBroker(FakeBroker):
    def __init__(self, responses: list[tuple[bool, str]]) -> None:
        super().__init__()
        self.responses = responses

    async def place_option_trade(self, asset: str, direction: str, amount: int, expiration_seconds: int) -> tuple[bool, str]:
        self.calls.append((asset, direction, amount, expiration_seconds))
        return self.responses.pop(0)


def make_record(**overrides: object) -> SignalOutcome:
    now = utc_now()
    payload = {
        "id": "EURUSD-OTC:60:CALL:test",
        "asset": "EURUSD-OTC",
        "direction": "CALL",
        "score": 8,
        "strength": 2.0,
        "continuity": 2.0,
        "exhaustion": 1.0,
        "cci": -120.0,
        "entry_price": 1.1,
        "entry_at": now - timedelta(seconds=1),
        "stake_amount": 10000,
        "payout_rate": 0.85,
        "result_price": None,
        "status": "pending",
        "timeframe": 60,
        "suggested_expiration": 60,
        "created_at": now - timedelta(seconds=60),
        "expires_at": now + timedelta(seconds=59),
        "resolved_at": None,
        "main_reason": "test",
        "balance_after": None,
        "abort_reason": "",
        "is_shadow": False,
        "blocked_reason": "",
    }
    payload.update(overrides)
    return SignalOutcome.model_validate(payload)


class BrokerTradeExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "broker_trades.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_places_due_pending_record_once(self) -> None:
        broker = FakeBroker()
        executor = BrokerTradeExecutor(self.path, enabled=True, balance_mode="PRACTICE")
        record = make_record()

        first = await executor.execute_due("EURUSD-OTC", [record], broker)
        second = await executor.execute_due("EURUSD-OTC", [record], broker)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].status, "placed")
        self.assertEqual(first[0].broker_order_id, "order-1")
        self.assertEqual(second, [])
        self.assertEqual(broker.calls, [("EURUSD-OTC", "CALL", 10000, 60)])

    async def test_places_due_waiting_entry_record(self) -> None:
        broker = FakeBroker()
        executor = BrokerTradeExecutor(self.path, enabled=True, balance_mode="PRACTICE")
        record = make_record(status="waiting_entry")

        trades = await executor.execute_due("EURUSD-OTC", [record], broker)

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].status, "placed")
        self.assertEqual(broker.calls, [("EURUSD-OTC", "CALL", 10000, 60)])

    async def test_places_every_due_record_across_markets(self) -> None:
        broker = FakeBroker()
        executor = BrokerTradeExecutor(self.path, enabled=True, balance_mode="PRACTICE")
        assets = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "BTCUSD-OTC"]
        records = [
            make_record(
                id=f"{asset}:60:CALL:test",
                asset=asset,
                direction="CALL",
            )
            for asset in assets
        ]

        trades = []
        for asset in assets:
            trades.extend(await executor.execute_due(asset, records, broker))

        self.assertEqual(len(trades), len(assets))
        self.assertEqual(
            broker.calls,
            [(asset, "CALL", 10000, 60) for asset in assets],
        )

    async def test_ignores_aborted_record(self) -> None:
        broker = FakeBroker()
        executor = BrokerTradeExecutor(self.path, enabled=True, balance_mode="PRACTICE")
        record = make_record(status="aborted", abort_reason="test abort")

        trades = await executor.execute_due("EURUSD-OTC", [record], broker)

        self.assertEqual(trades, [])
        self.assertEqual(broker.calls, [])

    async def test_ignores_stale_pending_record(self) -> None:
        broker = FakeBroker()
        executor = BrokerTradeExecutor(self.path, enabled=True, balance_mode="PRACTICE", entry_window_seconds=3)
        now = utc_now()
        record = make_record(entry_at=now - timedelta(seconds=20), expires_at=now + timedelta(seconds=40))

        trades = await executor.execute_due("EURUSD-OTC", [record], broker)

        self.assertEqual(trades, [])
        self.assertEqual(broker.calls, [])

    async def test_disabled_executor_does_not_trade(self) -> None:
        broker = FakeBroker()
        executor = BrokerTradeExecutor(self.path, enabled=False, balance_mode="PRACTICE")
        record = make_record()

        trades = await executor.execute_due("EURUSD-OTC", [record], broker)

        self.assertEqual(trades, [])
        self.assertEqual(broker.calls, [])

    async def test_respects_configured_min_stake(self) -> None:
        broker = FakeBroker()
        executor = BrokerTradeExecutor(self.path, enabled=True, balance_mode="PRACTICE", min_stake=15000)
        record = make_record(stake_amount=10000)

        trades = await executor.execute_due("EURUSD-OTC", [record], broker)

        self.assertEqual(trades, [])
        self.assertEqual(broker.calls, [])

    async def test_disconnected_broker_does_not_mark_record_failed(self) -> None:
        broker = FakeBroker()
        broker.connected = False
        executor = BrokerTradeExecutor(self.path, enabled=True, balance_mode="PRACTICE")
        record = make_record()

        trades = await executor.execute_due("EURUSD-OTC", [record], broker)

        self.assertEqual(trades, [])
        self.assertEqual(executor.trades, {})
        self.assertEqual(broker.calls, [])

    async def test_retries_failed_record_while_still_due(self) -> None:
        broker = FlakyBroker([
            (False, "Cannot purchase an option (the asset is not available at the moment)."),
            (True, "order-2"),
        ])
        executor = BrokerTradeExecutor(self.path, enabled=True, balance_mode="PRACTICE")
        record = make_record()

        first = await executor.execute_due("EURUSD-OTC", [record], broker)
        second = await executor.execute_due("EURUSD-OTC", [record], broker)

        self.assertEqual(first[0].status, "failed")
        self.assertEqual(second[0].status, "placed")
        self.assertEqual(second[0].broker_order_id, "order-2")
        self.assertEqual(len(broker.calls), 2)

    async def test_set_enabled_persists_runtime_state(self) -> None:
        executor = BrokerTradeExecutor(self.path, enabled=False, balance_mode="PRACTICE")

        await executor.set_enabled(True)
        restored = BrokerTradeExecutor(self.path, enabled=False, balance_mode="PRACTICE")

        self.assertTrue(restored.enabled)


if __name__ == "__main__":
    unittest.main()
