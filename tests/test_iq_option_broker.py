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


class IQOptionBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_realtime_candles_are_normalized_from_snapshot_values(self) -> None:
        broker = IQOptionBroker("", "")
        broker._client = FakeClient()
        broker._connected = True

        candles = await broker.get_realtime_candles("EURUSD-OTC", 60)

        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].open, 1.0)
        self.assertEqual(candles[0].close, 1.1)


if __name__ == "__main__":
    unittest.main()
