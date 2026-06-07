from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Dict, Iterable, List

from app.models import Candle, PerformanceBucket, PerformanceSummary, Signal, SignalOutcome, utc_now


class PerformanceTracker:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: Dict[str, SignalOutcome] = {}
        self._loaded_ok = True
        self._load()

    def register_signal(self, signal: Signal) -> None:
        if signal.id in self.records:
            return
        asset = self._normalize_asset(signal.asset)
        self.records[signal.id] = SignalOutcome(
            id=signal.id,
            asset=asset,
            direction=signal.direction,
            score=signal.score,
            strength=signal.strength,
            continuity=signal.continuity,
            exhaustion=signal.exhaustion,
            cci=signal.cci,
            entry_price=signal.price,
            timeframe=signal.timeframe,
            suggested_expiration=signal.suggested_expiration,
            created_at=signal.created_at,
            expires_at=signal.created_at + timedelta(seconds=signal.suggested_expiration),
            main_reason=signal.main_reason,
        )
        self._save()

    def evaluate(self, asset: str, candles: List[Candle]) -> List[SignalOutcome]:
        asset = self._normalize_asset(asset)
        pending = [
            record
            for record in self.records.values()
            if record.asset == asset and record.status == "pending" and record.expires_at <= utc_now()
        ]
        if not pending:
            return []

        closed = [candle for candle in candles if candle.is_closed]
        changed = False
        resolved_records: List[SignalOutcome] = []
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
            self._save()
        return resolved_records

    def summary(self) -> PerformanceSummary:
        records = list(self.records.values())
        resolved = [record for record in records if record.status != "pending"]
        wins = sum(1 for record in resolved if record.status == "win")
        losses = sum(1 for record in resolved if record.status == "loss")
        pushes = sum(1 for record in resolved if record.status == "push")
        pending = sum(1 for record in records if record.status == "pending")
        avg_score = sum(record.score for record in records) / len(records) if records else 0.0
        return PerformanceSummary(
            total=len(records),
            resolved=len(resolved),
            wins=wins,
            losses=losses,
            pushes=pushes,
            pending=pending,
            win_rate=self._win_rate(wins, losses),
            avg_score=round(avg_score, 2),
            by_market=self._buckets(records, "asset"),
            by_direction=self._buckets(records, "direction"),
            recent_results=sorted(records, key=lambda record: record.created_at, reverse=True)[:60],
        )

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            records = payload.get("records", [])
            self.records = {
                record["id"]: self._normalize_record(SignalOutcome.model_validate(record))
                for record in records
                if isinstance(record, dict) and record.get("id")
            }
            self._save()
        except Exception:
            self._loaded_ok = False
            backup = self.path.with_suffix(f"{self.path.suffix}.bak")
            if backup.exists():
                try:
                    payload = json.loads(backup.read_text(encoding="utf-8"))
                    records = payload.get("records", [])
                    self.records = {
                        record["id"]: self._normalize_record(SignalOutcome.model_validate(record))
                        for record in records
                        if isinstance(record, dict) and record.get("id")
                    }
                    self._loaded_ok = True
                    return
                except Exception:
                    pass
            self.records = {}

    def _save(self) -> None:
        if not self._loaded_ok and self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "records": [
                record.model_dump(mode="json")
                for record in sorted(self.records.values(), key=lambda item: item.created_at)
            ]
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        backup_path = self.path.with_suffix(f"{self.path.suffix}.bak")
        temp_path.write_text(encoded, encoding="utf-8")
        if self.path.exists():
            backup_path.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        temp_path.replace(self.path)

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
            resolved = [record for record in items if record.status != "pending"]
            wins = sum(1 for record in resolved if record.status == "win")
            losses = sum(1 for record in resolved if record.status == "loss")
            pushes = sum(1 for record in resolved if record.status == "push")
            pending = sum(1 for record in items if record.status == "pending")
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
        return record

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
