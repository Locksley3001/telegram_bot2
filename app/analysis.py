from __future__ import annotations

import math
from typing import List, Optional, Tuple

from app.models import Candle, CandleMetrics, Signal, Zone, utc_now

NO_EDGE_MESSAGE = "MERCADO SIN VENTAJA ESTADÍSTICA"


class PriceActionAnalyzer:
    def analyze(self, asset: str, timeframe: int, candles: List[Candle]) -> Tuple[List[Zone], Optional[Signal], dict]:
        usable = [candle for candle in candles if candle.high >= candle.low and candle.open > 0]
        zones = self.detect_zones(usable)
        if len(usable) < 12:
            return zones, None, self._empty_context()

        metrics = [self._metrics(candle) for candle in usable]
        latest = usable[-1]

        call = self._direction_context(1, usable, metrics, zones)
        put = self._direction_context(-1, usable, metrics, zones)
        chosen_side = 1 if call["score"] >= put["score"] else -1
        chosen = call if chosen_side == 1 else put
        other = put if chosen_side == 1 else call

        if chosen["score"] < 6 or chosen["score"] - other["score"] < 1.4:
            return zones, None, chosen

        direction = "CALL" if chosen_side == 1 else "PUT"
        grade = "strong" if chosen["score"] >= 8 else "valid"
        created_at = utc_now()
        signal = Signal(
            id=f"{asset}:{timeframe}:{direction}:{int(latest.timestamp)}:{int(chosen['score'] * 10)}",
            asset=asset,
            direction=direction,
            score=max(1, min(10, round(chosen["score"]))),
            grade=grade,
            strength=chosen["strength"],
            continuity=chosen["continuity"],
            exhaustion=chosen["exhaustion"],
            main_reason=chosen["reason"],
            suggested_expiration=self._suggest_expiration(timeframe, chosen["score"], chosen["pattern"]),
            created_at=created_at,
            price=latest.close,
            timeframe=timeframe,
        )
        return zones, signal, chosen

    def detect_zones(self, candles: List[Candle]) -> List[Zone]:
        if len(candles) < 16:
            return []

        recent = candles[-60:]
        ranges = [max(c.high - c.low, 0) for c in recent]
        avg_range = sum(ranges) / max(len(ranges), 1)
        tolerance = max(avg_range * 0.55, recent[-1].close * 0.00025)
        candidates: List[Tuple[float, str]] = []

        for index in range(2, len(recent) - 2):
            window = recent[index - 2 : index + 3]
            candle = recent[index]
            if candle.high == max(item.high for item in window):
                candidates.append((candle.high, "resistance"))
            if candle.low == min(item.low for item in window):
                candidates.append((candle.low, "support"))

        zones: List[Zone] = []
        for price, kind in candidates:
            touches = 0
            for candle in recent:
                touched = candle.low - tolerance <= price <= candle.high + tolerance
                reacted_from_support = kind == "support" and touched and candle.close > candle.open
                reacted_from_resistance = kind == "resistance" and touched and candle.close < candle.open
                if reacted_from_support or reacted_from_resistance:
                    touches += 1

            if touches >= 2 and not self._zone_exists(zones, price, kind, tolerance):
                zones.append(
                    Zone(
                        price=price,
                        kind=kind,
                        touches=touches,
                        strength=min(10.0, touches * 2.0),
                    )
                )

        zones.sort(key=lambda zone: zone.strength, reverse=True)
        return zones[:8]

    @staticmethod
    def _zone_exists(zones: List[Zone], price: float, kind: str, tolerance: float) -> bool:
        return any(zone.kind == kind and abs(zone.price - price) <= tolerance for zone in zones)

    def _direction_context(self, side: int, candles: List[Candle], metrics: List[CandleMetrics], zones: List[Zone]) -> dict:
        recent_candles = candles[-8:]
        recent_metrics = metrics[-8:]
        last_metrics = metrics[-4:]

        strength = self._strength(side, recent_metrics)
        continuity = self._continuity(side, recent_candles, recent_metrics)
        exhaustion = self._exhaustion(side, recent_metrics)
        confirmation = self._confirmation(side, last_metrics)
        zone_context = self._zone_context(side, candles[-1], zones)
        forming = self._forming_pressure(side, candles[-1], metrics[-1])
        reversal = self._reversal_context(side, metrics)

        continuation_score = (
            strength * 0.35
            + continuity * 0.35
            + confirmation * 0.12
            + zone_context * 0.08
            + forming * 0.10
            - exhaustion * 0.24
        )

        reversal_score = (
            reversal * 0.42
            + strength * 0.20
            + confirmation * 0.18
            + zone_context * 0.14
            + forming * 0.06
            - max(0.0, continuity - 4.5) * 0.16
        )

        if reversal_score > continuation_score and reversal >= 6.5:
            raw = reversal_score
            pattern = "reversal"
            reason = "reversa confirmada: continuidad previa perdida y fuerza opuesta visible"
        else:
            raw = continuation_score
            pattern = "continuation"
            reason = "continuidad limpia con fuerza dominante y retroceso debil"

        indecision_penalty = self._indecision_penalty(recent_metrics)
        score = max(1.0, min(10.0, raw - indecision_penalty))

        return {
            "score": score,
            "strength": round(strength, 1),
            "continuity": round(continuity, 1),
            "exhaustion": round(exhaustion, 1),
            "confirmation": round(confirmation, 1),
            "zone_context": round(zone_context, 1),
            "forming": round(forming, 1),
            "pattern": pattern,
            "reason": reason,
            "market_message": NO_EDGE_MESSAGE if score < 6 else "VENTAJA ESTADÍSTICA DETECTADA",
        }

    @staticmethod
    def _metrics(candle: Candle) -> CandleMetrics:
        range_size = max(candle.high - candle.low, 1e-9)
        body = abs(candle.close - candle.open)
        raw_direction = 1 if candle.close > candle.open else -1 if candle.close < candle.open else 0
        body_ratio = body / range_size
        if body_ratio < 0.08:
            raw_direction = 0
        upper_wick = candle.high - max(candle.open, candle.close)
        lower_wick = min(candle.open, candle.close) - candle.low
        close_position = (candle.close - candle.low) / range_size
        return CandleMetrics(
            direction=raw_direction,
            body=body,
            range_size=range_size,
            body_ratio=body_ratio,
            upper_wick_ratio=max(0.0, upper_wick / range_size),
            lower_wick_ratio=max(0.0, lower_wick / range_size),
            close_position=close_position,
        )

    @staticmethod
    def _strength(side: int, metrics: List[CandleMetrics]) -> float:
        aligned = [item for item in metrics if item.direction == side]
        if not aligned:
            return 1.0
        clean_bodies = sum(1 for item in aligned if item.body_ratio >= 0.48)
        wick_penalty = 0.0
        for item in aligned:
            wick_penalty += item.upper_wick_ratio if side == 1 else item.lower_wick_ratio
        average_body = sum(item.body_ratio for item in aligned) / len(aligned)
        dominance = len(aligned) / len(metrics)
        score = dominance * 4.2 + average_body * 5.0 + clean_bodies * 0.65 - wick_penalty * 1.15
        return max(1.0, min(10.0, score))

    @staticmethod
    def _continuity(side: int, candles: List[Candle], metrics: List[CandleMetrics]) -> float:
        closes_forward = 0
        for prev, current in zip(candles, candles[1:]):
            if side == 1 and current.close >= prev.close:
                closes_forward += 1
            if side == -1 and current.close <= prev.close:
                closes_forward += 1

        aligned = sum(1 for item in metrics if item.direction == side)
        opposite = [item for item in metrics if item.direction == -side]
        weak_pullbacks = sum(1 for item in opposite if item.body_ratio <= 0.38)
        score = closes_forward * 0.85 + aligned * 0.70 + weak_pullbacks * 0.55
        return max(1.0, min(10.0, score))

    @staticmethod
    def _confirmation(side: int, metrics: List[CandleMetrics]) -> float:
        if not metrics:
            return 1.0
        latest = metrics[-1]
        previous = metrics[-2:] if len(metrics) >= 2 else metrics
        aligned_recent = sum(1 for item in previous if item.direction == side)
        close_quality = latest.close_position if side == 1 else 1 - latest.close_position
        score = aligned_recent * 2.0 + latest.body_ratio * 4.0 + close_quality * 3.0
        return max(1.0, min(10.0, score))

    @staticmethod
    def _exhaustion(side: int, metrics: List[CandleMetrics]) -> float:
        if len(metrics) < 6:
            return 1.0
        aligned = [item for item in metrics if item.direction == side]
        if len(aligned) < 3:
            return 2.5
        early = aligned[: max(1, len(aligned) // 2)]
        late = aligned[-max(1, len(aligned) // 2) :]
        early_body = sum(item.body_ratio for item in early) / len(early)
        late_body = sum(item.body_ratio for item in late) / len(late)
        body_loss = max(0.0, early_body - late_body) * 6.0
        rejection = sum(item.upper_wick_ratio if side == 1 else item.lower_wick_ratio for item in metrics[-4:])
        opposite_attempts = sum(1 for item in metrics[-4:] if item.direction == -side)
        return max(1.0, min(10.0, 1.0 + body_loss + rejection * 1.2 + opposite_attempts * 0.9))

    @staticmethod
    def _reversal_context(side: int, metrics: List[CandleMetrics]) -> float:
        if len(metrics) < 10:
            return 1.0
        previous = metrics[-10:-3]
        current = metrics[-3:]
        prior_opposite = sum(1 for item in previous if item.direction == -side)
        current_side = [item for item in current if item.direction == side and item.body_ratio >= 0.38]
        failed_continue = sum(1 for item in current if item.direction == -side and item.body_ratio <= 0.25)
        opposite_wick = sum(item.lower_wick_ratio if side == 1 else item.upper_wick_ratio for item in previous[-3:])
        score = prior_opposite * 0.85 + len(current_side) * 2.3 + failed_continue * 1.2 + opposite_wick * 1.6
        return max(1.0, min(10.0, score))

    @staticmethod
    def _zone_context(side: int, candle: Candle, zones: List[Zone]) -> float:
        if not zones:
            return 1.0
        price = candle.close
        range_size = max(candle.high - candle.low, price * 0.0002)
        useful = 1.0
        for zone in zones:
            distance = abs(price - zone.price)
            nearby = distance <= range_size * 1.2
            if side == 1 and zone.kind == "support" and nearby:
                useful = max(useful, min(10.0, zone.strength + 2.0))
            if side == -1 and zone.kind == "resistance" and nearby:
                useful = max(useful, min(10.0, zone.strength + 2.0))
        return useful

    @staticmethod
    def _forming_pressure(side: int, candle: Candle, metric: CandleMetrics) -> float:
        close_quality = metric.close_position if side == 1 else 1 - metric.close_position
        score = metric.body_ratio * 5.0 + close_quality * 4.0
        if metric.direction == side:
            score += 1.5
        if candle.is_closed:
            score *= 0.85
        return max(1.0, min(10.0, score))

    @staticmethod
    def _indecision_penalty(metrics: List[CandleMetrics]) -> float:
        dojis = sum(1 for item in metrics if item.body_ratio < 0.18)
        alternations = 0
        directions = [item.direction for item in metrics if item.direction != 0]
        for previous, current in zip(directions, directions[1:]):
            if previous != current:
                alternations += 1
        wick_noise = sum(item.upper_wick_ratio + item.lower_wick_ratio for item in metrics) / max(len(metrics), 1)
        return min(3.0, dojis * 0.35 + alternations * 0.25 + max(0.0, wick_noise - 0.65) * 1.8)

    @staticmethod
    def _suggest_expiration(timeframe: int, score: float, pattern: str) -> int:
        allowed = [30, 45, 60, 120, 180, 300]
        if pattern == "reversal":
            target = 120 if timeframe <= 60 else min(300, timeframe)
        elif score >= 8:
            target = timeframe if timeframe in allowed else 60
        else:
            target = 60 if timeframe <= 60 else min(180, timeframe)
        return min(allowed, key=lambda item: abs(item - target))

    @staticmethod
    def _empty_context() -> dict:
        return {
            "score": 1.0,
            "strength": 1.0,
            "continuity": 1.0,
            "exhaustion": 1.0,
            "market_message": NO_EDGE_MESSAGE,
            "reason": "datos insuficientes desde IQ Option",
            "pattern": "none",
        }
