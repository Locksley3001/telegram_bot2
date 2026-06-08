from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from app.models import Candle


class BrokerUnavailable(RuntimeError):
    pass


class BrokerInterface(ABC):
    @property
    @abstractmethod
    def connected(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def reconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_candles(self, asset: str, timeframe: int, count: int) -> List[Candle]:
        raise NotImplementedError

    @abstractmethod
    async def get_available_assets(self) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    async def get_current_price(self, asset: str) -> Optional[float]:
        raise NotImplementedError

    @abstractmethod
    async def get_payout(self, asset: str, timeframe: int) -> Optional[float]:
        raise NotImplementedError

    @abstractmethod
    async def place_option_trade(self, asset: str, direction: str, amount: int, expiration_seconds: int) -> tuple[bool, str]:
        raise NotImplementedError
