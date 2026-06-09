from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from app.models import BalanceEvent, Candle, PerformanceBucket, PerformanceSummary, Signal, SignalOutcome, VirtualBalanceSummary, utc_now
from app.state_storage import StateStorage


INITIAL_BALANCE = 50000
TARGET_BALANCE = 500000
SAFE_STAKE = 20000
CAUTIOUS_STAKE = 10000
PAYOUT_RATE = 0.85


class PerformanceTracker:
    def __init__(self, path: Path, storage: Optional[StateStorage] = None) -> None:
        self.path = path
        self.storage = storage
        self.records: Dict[str, SignalOutcome] = {}
        self._loaded_ok = True
        self._load()

    def register_signal(self, signal: Signal) -> None:
        if signal.id in self.records:
            return
        asset = self._normalize_asset(signal.asset)
        entry_at = signal.pending_execution_at or signal.created_at
        self.records[signal.id] = SignalOutcome(
            id=signal.id,
            asset=asset,
            direction=signal.direction,
            score=signal.score,
            strength=signal.strength,
            continuity=signal.continuity,
            exhaustion=signal.exhaustion,
            cci=signal.cci,
            entry_price=0.0,
            entry_at=entry_at,
            stake_amount=signal.stake_amount,
            payout_rate=PAYOUT_RATE,
            status="waiting_entry",
            timeframe=signal.timeframe,
            suggested_expiration=signal.suggested_expiration,
            created_at=signal.created_at,
            expires_at=entry_at + timedelta(seconds=signal.suggested_expiration),
            main_reason=signal.main_reason,
            is_shadow=signal.is_shadow,
            blocked_reason=signal.blocked_reason,
        )
        self._save()

    def register_shadow_signal(self, signal: Signal, blocked_reason: str) -> None:
        shadow = signal.model_copy(deep=True)
        shadow.id = self._shadow_id(shadow.id)
        shadow.stake_amount = 0
        shadow.is_shadow = True
        shadow.blocked_reason = blocked_reason.strip()
        if shadow.blocked_reason and shadow.blocked_reason not in shadow.main_reason:
            shadow.main_reason = f"{shadow.main_reason} | SOMBRA: {shadow.blocked_reason}"
        self.register_signal(shadow)

    def evaluate(
        self,
        asset: str,
        candles: List[Candle],
        abort_checker: Optional[Callable[[SignalOutcome, List[Candle]], Optional[str]]] = None,
    ) -> List[SignalOutcome]:
        asset = self._normalize_asset(asset)
        usable = [candle for candle in candles if candle.high >= candle.low and candle.open > 0]
        closed = [candle for candle in candles if candle.is_closed]
        changed = False
        resolved_records: List[SignalOutcome] = []
        for record in self.records.values():
            if record.asset != asset or record.status != "waiting_entry":
                continue
            entry_candle = self._entry_candle(record, usable)
            if entry_candle is None:
                continue
            abort_reason = None if record.is_shadow else abort_checker(record, usable) if abort_checker is not None else None
            if abort_reason:
                record.entry_price = entry_candle.open
                record.result_price = entry_candle.open
                record.status = "aborted"
                record.resolved_at = utc_now()
                record.abort_reason = abort_reason
                record.main_reason = f"{record.main_reason} | ABORTADA: {abort_reason}"
                changed = True
                resolved_records.append(record)
                continue
            record.entry_price = entry_candle.open
            record.status = "pending"
            changed = True

        pending = [
            record
            for record in self.records.values()
            if record.asset == asset and record.status == "pending" and record.expires_at <= utc_now()
        ]
        if not pending:
            if changed:
                self._save()
            return resolved_records

        for record in pending:
            result_candle = self._result_candle(record, closed)
            if result_candle is None:
                continue
            result_price = result_candle.close
            record.result_price = result_price
            record.resolved_at = utc_now()
            if result_price == record.entry_price:
                record.status = "push"
            elif record.direction == "CALL":
                record.status = "win" if result_price > record.entry_price else "loss"
            elif record.direction == "PUT":
                record.status = "win" if result_price < record.entry_price else "loss"
            else:
                record.status = "push"
            changed = True
            resolved_records.append(record)

        if changed:
            balances = {event.timestamp: event.balance for event in self.virtual_balance().history}
            for record in resolved_records:
                if record.resolved_at in balances:
                    record.balance_after = balances[record.resolved_at]
            self._save()
        return resolved_records

    def summary(self) -> PerformanceSummary:
        records = list(self.records.values())
        real_records = [record for record in records if not record.is_shadow]
        shadow_records = [record for record in records if record.is_shadow]
        resolved = [record for record in real_records if record.status in {"win", "loss", "push", "aborted"}]
        traded = [record for record in resolved if record.status in {"win", "loss", "push"}]
        wins = sum(1 for record in traded if record.status == "win")
        losses = sum(1 for record in traded if record.status == "loss")
        pushes = sum(1 for record in traded if record.status == "push")
        pending = sum(1 for record in real_records if record.status in {"waiting_entry", "pending"})
        avg_score = sum(record.score for record in real_records) / len(real_records) if real_records else 0.0
        shadow_resolved = [record for record in shadow_records if record.status in {"win", "loss", "push"}]
        shadow_wins = sum(1 for record in shadow_resolved if record.status == "win")
        shadow_losses = sum(1 for record in shadow_resolved if record.status == "loss")
        shadow_pushes = sum(1 for record in shadow_resolved if record.status == "push")
        shadow_pending = sum(1 for record in shadow_records if record.status in {"waiting_entry", "pending"})
        return PerformanceSummary(
            total=len(real_records),
            resolved=len(traded),
            wins=wins,
            losses=losses,
            pushes=pushes,
            pending=pending,
            win_rate=self._win_rate(wins, losses),
            avg_score=round(avg_score, 2),
            shadow_total=len(shadow_records),
            shadow_resolved=len(shadow_resolved),
            shadow_wins=shadow_wins,
            shadow_losses=shadow_losses,
            shadow_pushes=shadow_pushes,
            shadow_pending=shadow_pending,
            shadow_win_rate=self._win_rate(shadow_wins, shadow_losses),
            by_market=self._buckets(real_records, "asset"),
            by_direction=self._buckets(real_records, "direction"),
            recent_results=sorted(records, key=lambda record: record.created_at, reverse=True)[:60],
        )

    def virtual_balance(self, *, timeframe: int = 60) -> VirtualBalanceSummary:
        balance = INITIAL_BALANCE
        bankruptcies = 0
        targets_hit = 0
        consecutive_losses = 0
        operations_since_reset = 0
        history: List[BalanceEvent] = []
        last_loss_at: datetime | None = None

        resolved = sorted(
            (
                record
                for record in self.records.values()
                if record.status in {"win", "loss", "push"} and record.stake_amount > 0
            ),
            key=lambda item: item.resolved_at or item.expires_at,
        )

        for record in resolved:
            timestamp = record.resolved_at or record.expires_at
            stake = int(record.stake_amount or 0)
            profit = 0
            if record.status == "win":
                profit = int(round(stake * float(record.payout_rate or PAYOUT_RATE)))
                balance += profit
                consecutive_losses = 0
                last_loss_at = None
                mark = "GANANCIA"
                result = "ganada"
            elif record.status == "loss":
                profit = -stake
                balance = max(0, balance - stake)
                consecutive_losses += 1
                last_loss_at = timestamp
                mark = "PERDIDA"
                result = "perdida"
            else:
                mark = "EMPATE"
                result = "empate"

            operations_since_reset += 1
            history.append(
                BalanceEvent(
                    timestamp=timestamp,
                    mark=mark,
                    asset=record.asset,
                    direction=record.direction,
                    stake_amount=stake,
                    result=result,
                    profit=profit,
                    balance=balance,
                    note=record.main_reason,
                )
            )

            if balance <= 0:
                bankruptcies += 1
                balance = INITIAL_BALANCE
                consecutive_losses = 0
                operations_since_reset = 0
                history.append(
                    BalanceEvent(
                        timestamp=timestamp,
                        mark=f"[QUIEBRA #{bankruptcies}]",
                        stake_amount=0,
                        result="reinicio",
                        profit=INITIAL_BALANCE,
                        balance=balance,
                        note="Saldo llego a 0; reinicio y aprendizaje reforzado.",
                    )
                )
            elif balance >= TARGET_BALANCE:
                targets_hit += 1
                balance = INITIAL_BALANCE
                consecutive_losses = 0
                operations_since_reset = 0
                history.append(
                    BalanceEvent(
                        timestamp=timestamp,
                        mark=f"[META #{targets_hit}]",
                        stake_amount=0,
                        result="reinicio",
                        profit=0,
                        balance=balance,
                        note="Meta alcanzada; patrones ganadores consolidados antes de reiniciar.",
                    )
                )

        high_threshold = 5 if bankruptcies >= 1 else 4
        pause_base = 0
        if bankruptcies >= 3 and consecutive_losses >= 2 and last_loss_at is not None:
            pause_base = 3 + max(0, bankruptcies - 3)
        pause_seconds = pause_base * max(1, timeframe)
        pause_remaining = 0
        if pause_seconds and last_loss_at is not None:
            remaining_seconds = (last_loss_at + timedelta(seconds=pause_seconds) - utc_now()).total_seconds()
            pause_remaining = max(0, int(math.ceil(remaining_seconds / max(1, timeframe))))

        mode = "Proteccion normal"
        if bankruptcies:
            mode = f"Quiebra #{bankruptcies} activo - umbral alta confianza {high_threshold}/6"
        if pause_remaining:
            mode = f"{mode}; pausa {pause_remaining} vela(s)"
        elif balance < CAUTIOUS_STAKE:
            mode = "Modo proteccion - saldo insuficiente"
        elif balance < SAFE_STAKE:
            mode = "Modo proteccion - apuesta maxima 10000"

        return VirtualBalanceSummary(
            balance=balance,
            bankruptcies=bankruptcies,
            targets_hit=targets_hit,
            consecutive_losses=consecutive_losses,
            operations_since_reset=operations_since_reset,
            high_confidence_threshold=high_threshold,
            pause_candles_remaining=pause_remaining,
            mode=mode,
            history=history[-80:],
        )

    def _load(self) -> None:
        if self.storage is not None:
            payload = self.storage.load_json(self.path.name, self.path)
            if payload is None:
                return
            try:
                records = payload.get("records", [])
                self.records = {
                    record["id"]: self._normalize_record(SignalOutcome.model_validate(record))
                    for record in records
                    if isinstance(record, dict) and record.get("id")
                }
                return
            except Exception:
                self._loaded_ok = False
                self.records = {}
                return

        if not self.path.exists():
            return
        try:
            payload = StateStorage._load_local(self.path) or {}
            records = payload.get("records", [])
            self.records = {
                record["id"]: self._normalize_record(SignalOutcome.model_validate(record))
                for record in records
                if isinstance(record, dict) and record.get("id")
            }
            self._save()
        except Exception:
            self._loaded_ok = False
            self.records = {}

    def _save(self) -> None:
        if not self._loaded_ok and self.path.exists():
            return
        payload = {
            "records": [
                record.model_dump(mode="json")
                for record in sorted(self.records.values(), key=lambda item: item.created_at)
            ]
        }
        if self.storage is not None:
            self.storage.save_json(self.path.name, self.path, payload)
        else:
            StateStorage._write_local(self.path, payload)

    @staticmethod
    def _entry_candle(record: SignalOutcome, candles: List[Candle]) -> Candle | None:
        entry_at = record.entry_at or record.created_at
        entry_ts = entry_at.timestamp()
        candidates = [candle for candle in candles if candle.timestamp >= entry_ts]
        return candidates[0] if candidates else None

    @staticmethod
    def _result_candle(record: SignalOutcome, candles: List[Candle]) -> Candle | None:
        expires_ts = record.expires_at.timestamp()
        candidates = [candle for candle in candles if candle.timestamp >= expires_ts]
        return candidates[0] if candidates else None

    @classmethod
    def _buckets(cls, records: Iterable[SignalOutcome], field: str) -> List[PerformanceBucket]:
        grouped: Dict[str, List[SignalOutcome]] = {}
        for record in records:
            key = str(getattr(record, field))
            grouped.setdefault(key, []).append(record)

        buckets: List[PerformanceBucket] = []
        for key, items in grouped.items():
            resolved = [record for record in items if record.status in {"win", "loss", "push"}]
            wins = sum(1 for record in resolved if record.status == "win")
            losses = sum(1 for record in resolved if record.status == "loss")
            pushes = sum(1 for record in resolved if record.status == "push")
            pending = sum(1 for record in items if record.status in {"waiting_entry", "pending"})
            buckets.append(
                PerformanceBucket(
                    name=key,
                    total=len(items),
                    wins=wins,
                    losses=losses,
                    pushes=pushes,
                    pending=pending,
                    win_rate=cls._win_rate(wins, losses),
                )
            )
        buckets.sort(key=lambda bucket: (bucket.total, bucket.win_rate), reverse=True)
        return buckets

    @staticmethod
    def _win_rate(wins: int, losses: int) -> float:
        total = wins + losses
        return round((wins / total) * 100.0, 1) if total else 0.0

    @classmethod
    def _normalize_record(cls, record: SignalOutcome) -> SignalOutcome:
        record.asset = cls._normalize_asset(record.asset)
        if record.status == "waiting_entry" and record.entry_at is None:
            record.entry_at = record.created_at
        if record.stake_amount < 0:
            record.stake_amount = 0
        return record

    @staticmethod
    def _shadow_id(signal_id: str) -> str:
        return signal_id if signal_id.startswith("shadow:") else f"shadow:{signal_id}"

    @staticmethod
    def _normalize_asset(asset: str) -> str:
        cleaned = asset.strip().upper().replace(" ", "").replace("_", "-")
        aliases = {
            "BTC/USD-OTC": "BTCUSD-OTC",
            "BTCUSD-OTC-OP": "BTCUSD-OTC",
            "NVIDIAAMD-OTC": "NVDA/AMD-OTC",
            "NVIDIA/AMD-OTC": "NVDA/AMD-OTC",
            "NVIDIA-AMD-OTC": "NVDA/AMD-OTC",
            "NVDAAMD-OTC": "NVDA/AMD-OTC",
        }
        return aliases.get(cleaned, cleaned)
