from __future__ import annotations

import unittest

from app.iq_option_broker import IQOptionBroker


class MutatingValues(dict):
    def values(self):
        yield {
            "from": 1,
            "open": 1.0,
            "close": 1.1,
            "max": 1.2,
            "min": 0.9,
            "volume": 10,
        }
        self["new"] = {"from": 2}
        raise RuntimeError("dictionary keys changed during iteration")


class FakeClient:
    def start_candles_stream(self, asset: str, timeframe: int, count: int) -> None:
        return None

    def get_realtime_candles(self, asset: str, timeframe: int):
        return MutatingValues(
            {
                1: {
                    "from": 1,
                    "open": 1.0,
                    "close": 1.1,
                    "max": 1.2,
                    "min": 0.9,
                    "volume": 10,
                }
            }
        )


class FakeBuyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[float, str, str, int]] = []

    def buy(self, amount: float, asset: str, action: str, duration: int):
        self.calls.append((amount, asset, action, duration))
        if asset in {"USDJPY-OTC", "BTCUSD-OTC-op", "BTCUSD-OTC"}:
            return False, "Cannot purchase an option (the asset is not available at the moment)."
        return True, "order-1"


class IQOptionBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_realtime_candles_are_normalized_from_snapshot_values(self) -> None:
        broker = IQOptionBroker("", "")
        broker._client = FakeClient()
        broker._connected = True

        candles = await broker.get_realtime_candles("EURUSD-OTC", 60)

        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].open, 1.0)
        self.assertEqual(candles[0].close, 1.1)

    async def test_place_option_trade_retries_slash_fx_asset_name(self) -> None:
        client = FakeBuyClient()
        broker = IQOptionBroker("", "")
        broker._client = client
        broker._connected = True

        success, detail = await broker.place_option_trade("USDJPY-OTC", "CALL", 20000, 60)

        self.assertTrue(success)
        self.assertEqual(detail, "order-1")
        self.assertEqual(
            client.calls,
            [
                (20000.0, "USDJPY-OTC", "call", 1),
                (20000.0, "USD/JPY-OTC", "call", 1),
            ],
        )

    async def test_place_option_trade_retries_btc_otc_without_op_suffix_and_slash(self) -> None:
        client = FakeBuyClient()
        broker = IQOptionBroker("", "")
        broker._client = client
        broker._connected = True

        success, detail = await broker.place_option_trade("BTCUSD-OTC", "CALL", 10000, 60)

        self.assertTrue(success)
        self.assertEqual(detail, "order-1")
        self.assertEqual(
            client.calls,
            [
                (10000.0, "BTCUSD-OTC-op", "call", 1),
                (10000.0, "BTCUSD-OTC", "call", 1),
                (10000.0, "BTC/USD-OTC", "call", 1),
            ],
        )


if __name__ == "__main__":
    unittest.main()
