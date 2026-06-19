from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from app.broker_interface import BrokerInterface
from app.models import BrokerTrade, BrokerTradingSummary, SignalOutcome, utc_now
from app.state_storage import StateStorage

LOGGER = logging.getLogger(__name__)
MIN_ENTRY_WINDOW_SECONDS = 8.0


class BrokerTradeExecutor:
    def __init__(
        self,
        path: Path,
        *,
        enabled: bool,
        balance_mode: str,
        entry_window_seconds: float = 3.0,
        min_stake: int = 10000,
        storage: Optional[StateStorage] = None,
    ) -> None:
        self.path = path
        self.storage = storage
        self.enabled = enabled
        self.balance_mode = balance_mode.strip().upper() or "PRACTICE"
        self.entry_window_seconds = max(MIN_ENTRY_WINDOW_SECONDS, entry_window_seconds)
        self.min_stake = max(1, int(min_stake))
        self.trades: Dict[str, BrokerTrade] = {}
        self.last_error = ""
        self._lock = asyncio.Lock()
        self._loaded_ok = True
        self._load()

    async def set_enabled(self, enabled: bool) -> None:
        async with self._lock:
            self.enabled = enabled
            self._save()

    async def execute_due(
        self,
        asset: str,
        records: Iterable[SignalOutcome],
        broker: BrokerInterface,
    ) -> List[BrokerTrade]:
        if not self.enabled:
            return []
        if getattr(broker, "connected", True) is False:
            return []

        async with self._lock:
            due_records = [record for record in records if self._is_due(asset, record)]
            if not due_records:
                return []

            trades: List[BrokerTrade] = []
            for record in sorted(due_records, key=lambda item: item.entry_at or item.created_at):
                trade = await self._place(record, broker)
                trades.append(trade)
                self.trades[record.id] = trade
                self._save()
            return trades

    def summary(self, limit: int = 30) -> BrokerTradingSummary:
        trades = sorted(self.trades.values(), key=lambda item: item.requested_at)
        placed = sum(1 for trade in trades if trade.status == "placed")
        failed = sum(1 for trade in trades if trade.status == "failed")
        return BrokerTradingSummary(
            enabled=self.enabled,
            balance_mode=self.balance_mode,
            entry_window_seconds=self.entry_window_seconds,
            total=len(trades),
            placed=placed,
            failed=failed,
            last_error=self.last_error,
            recent_trades=trades[-limit:],
        )

    def _is_due(self, asset: str, record: SignalOutcome) -> bool:
        existing = self.trades.get(record.id)
        if existing is not None and existing.status == "placed":
            return False
        if record.asset != asset or record.is_shadow:
            return False
        if record.status not in {"waiting_entry", "pending"}:
            return False
        if record.direction not in {"CALL", "PUT"} or record.stake_amount < self.min_stake:
            return False
        if record.entry_at is None:
            return False

        now = utc_now()
        if record.expires_at <= now:
            return False
        entry_delay = (now - record.entry_at).total_seconds()
        return 0 <= entry_delay <= self.entry_window_seconds

    async def _place(self, record: SignalOutcome, broker: BrokerInterface) -> BrokerTrade:
        requested_at = utc_now()
        try:
            success, detail = await broker.place_option_trade(
                record.asset,
                record.direction,
                int(record.stake_amount),
                int(record.suggested_expiration),
            )
        except Exception as exc:
            LOGGER.exception("No se pudo ejecutar operacion real para %s", record.id)
            self.last_error = str(exc)
            return BrokerTrade(
                signal_id=record.id,
                status="failed",
                asset=record.asset,
                direction=record.direction,
                stake_amount=int(record.stake_amount),
                expiration_seconds=int(record.suggested_expiration),
                balance_mode=self.balance_mode,
                requested_at=requested_at,
                error=str(exc),
            )

        if success:
            self.last_error = ""
            return BrokerTrade(
                signal_id=record.id,
                broker_order_id=detail,
                status="placed",
                asset=record.asset,
                direction=record.direction,
                stake_amount=int(record.stake_amount),
                expiration_seconds=int(record.suggested_expiration),
                balance_mode=self.balance_mode,
                requested_at=requested_at,
                placed_at=utc_now(),
            )

        self.last_error = detail
        return BrokerTrade(
            signal_id=record.id,
            broker_order_id=detail if detail.isdigit() else None,
            status="failed",
            asset=record.asset,
            direction=record.direction,
            stake_amount=int(record.stake_amount),
            expiration_seconds=int(record.suggested_expiration),
            balance_mode=self.balance_mode,
            requested_at=requested_at,
            error=detail,
        )

    def _load(self) -> None:
        if self.storage is not None:
            payload = self.storage.load_json(self.path.name, self.path)
            if payload is None:
                return
            try:
                if "enabled" in payload:
                    self.enabled = bool(payload.get("enabled"))
                self.trades = {
                    trade.signal_id: trade
                    for trade in (
                        BrokerTrade.model_validate(item)
                        for item in payload.get("trades", [])
                        if isinstance(item, dict) and item.get("signal_id")
                    )
                }
                return
            except Exception:
                self._loaded_ok = False
                self.trades = {}
                return

        if not self.path.exists():
            return
        try:
            payload = StateStorage._load_local(self.path) or {}
            if "enabled" in payload:
                self.enabled = bool(payload.get("enabled"))
            self.trades = {
                trade.signal_id: trade
                for trade in (
                    BrokerTrade.model_validate(item)
                    for item in payload.get("trades", [])
                    if isinstance(item, dict) and item.get("signal_id")
                )
            }
        except Exception:
            self._loaded_ok = False
            self.trades = {}

    def _save(self) -> None:
        if not self._loaded_ok and self.path.exists():
            return
        payload = {
            "enabled": self.enabled,
            "balance_mode": self.balance_mode,
            "entry_window_seconds": self.entry_window_seconds,
            "min_stake": self.min_stake,
            "trades": [
                trade.model_dump(mode="json")
                for trade in sorted(self.trades.values(), key=lambda item: item.requested_at)
            ]
        }
        if self.storage is not None:
            self.storage.save_json(self.path.name, self.path, payload)
        else:
            StateStorage._write_local(self.path, payload)
