from __future__ import annotations

from typing import List, Optional, Tuple

from app.models import Candle, CandleMetrics, Signal, Zone, utc_now

NO_EDGE_MESSAGE = "MERCADO SIN VENTAJA ESTADISTICA"
CCI_PERIOD = 20
CCI_OVERBOUGHT = 100.0
CCI_OVERSOLD = -100.0


class PriceActionAnalyzer:
    def analyze(self, asset: str, timeframe: int, candles: List[Candle]) -> Tuple[List[Zone], Optional[Signal], dict]:
        usable = [candle for candle in candles if candle.high >= candle.low and candle.open > 0]
        zones = self.detect_zones(usable)
        closed = [candle for candle in usable if candle.is_closed]
        signal_candles = closed if len(closed) >= CCI_PERIOD else usable

        if len(signal_candles) < CCI_PERIOD:
            return zones, None, self._empty_context()

        metrics = [self._metrics(candle) for candle in signal_candles]
        latest = signal_candles[-1]
        chosen_side, chosen = self._cci_reaction_context(signal_candles, metrics, zones)

        if chosen_side == 0 or chosen["score"] < 7:
            return zones, None, chosen

        direction = "CALL" if chosen_side == 1 else "PUT"
        grade = "strong" if chosen["score"] >= 8 else "valid"
        created_at = utc_now()
        signal = Signal(
            id=f"{asset}:{timeframe}:{direction}:cci20:{int(latest.timestamp)}",
            asset=asset,
            direction=direction,
            score=max(1, min(10, round(chosen["score"]))),
            grade=grade,
            strength=chosen["strength"],
            continuity=chosen["continuity"],
            exhaustion=chosen["exhaustion"],
            cci=chosen["cci"],
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

    def _cci_reaction_context(self, candles: List[Candle], metrics: List[CandleMetrics], zones: List[Zone]) -> Tuple[int, dict]:
        cci_values = self._cci_series(candles, CCI_PERIOD)
        latest_cci = cci_values[-1] if cci_values else 0.0
        recent_cci = cci_values[-8:] if len(cci_values) >= 8 else cci_values
        latest = candles[-1]
        latest_metric = metrics[-1]
        recent_candles = candles[-8:]
        recent_metrics = metrics[-8:]

        high_extreme = (
            latest_cci >= CCI_OVERBOUGHT
            and recent_cci
            and latest_cci >= max(recent_cci) - 8.0
        )
        low_extreme = (
            latest_cci <= CCI_OVERSOLD
            and recent_cci
            and latest_cci <= min(recent_cci) + 8.0
        )

        if not high_extreme and not low_extreme:
            context = self._empty_context()
            context.update(
                {
                    "cci": round(latest_cci, 1),
                    "market_message": "ESPERANDO CCI(20) EN ZONA EXTREMA",
                    "reason": "CCI(20) fuera de maximo alto/bajo de reaccion",
                }
            )
            return 0, context

        signal_side = -1 if high_extreme else 1
        trend_side = 1 if high_extreme else -1
        strength = self._strength(trend_side, recent_metrics)
        continuity = self._continuity(trend_side, recent_candles, recent_metrics)
        exhaustion = self._exhaustion(trend_side, recent_metrics)
        rejection = self._rejection_score(trend_side, latest_metric)
        zone_context = self._zone_context(signal_side, latest, zones)
        cci_pressure = min(10.0, 6.0 + (abs(latest_cci) - CCI_OVERBOUGHT) / 18.0)

        tired_candle = self._is_exhaustion_rejection(trend_side, latest_metric)
        has_prior_move = continuity >= 4.5 or strength >= 4.8
        raw_score = (
            cci_pressure * 0.32
            + exhaustion * 0.22
            + rejection * 0.24
            + continuity * 0.12
            + zone_context * 0.04
        )

        if tired_candle:
            raw_score += 1.5
        else:
            raw_score -= 2.2
        if has_prior_move:
            raw_score += 0.4
        else:
            raw_score -= 1.4

        score = max(1.0, min(10.0, raw_score))
        direction_text = "PUT" if signal_side == -1 else "CALL"
        extreme_text = "sobrecompra" if high_extreme else "sobreventa"
        if tired_candle and has_prior_move:
            reason = (
                f"CCI(20) en {extreme_text} extrema ({latest_cci:.1f}) con vela de cansancio "
                f"y rechazo; entrada contra mercado {direction_text}"
            )
        else:
            reason = f"CCI(20) extremo ({latest_cci:.1f}), esperando vela de cansancio mas clara"

        return signal_side, {
            "score": score,
            "strength": round(strength, 1),
            "continuity": round(continuity, 1),
            "exhaustion": round(exhaustion, 1),
            "cci": round(latest_cci, 1),
            "confirmation": round(rejection, 1),
            "zone_context": round(zone_context, 1),
            "forming": round(rejection, 1),
            "pattern": "cci_reversal",
            "reason": reason,
            "market_message": NO_EDGE_MESSAGE if score < 7 else "REACCION CCI(20) DETECTADA",
        }

    @staticmethod
    def _cci_series(candles: List[Candle], period: int) -> List[float]:
        typical_prices = [(candle.high + candle.low + candle.close) / 3.0 for candle in candles]
        values: List[float] = []
        for index in range(period - 1, len(typical_prices)):
            window = typical_prices[index - period + 1 : index + 1]
            sma = sum(window) / period
            mean_deviation = sum(abs(price - sma) for price in window) / period
            if mean_deviation <= 1e-12:
                values.append(0.0)
            else:
                values.append((typical_prices[index] - sma) / (0.015 * mean_deviation))
        return values

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
    def _rejection_score(trend_side: int, metric: CandleMetrics) -> float:
        if trend_side == 1:
            wick = metric.upper_wick_ratio
            close_against_trend = 1.0 - metric.close_position
            opposite_body = 1.4 if metric.direction == -1 else 0.0
        else:
            wick = metric.lower_wick_ratio
            close_against_trend = metric.close_position
            opposite_body = 1.4 if metric.direction == 1 else 0.0
        score = wick * 8.0 + close_against_trend * 3.0 + opposite_body
        if metric.body_ratio <= 0.32:
            score += 1.2
        return max(1.0, min(10.0, score))

    @staticmethod
    def _is_exhaustion_rejection(trend_side: int, metric: CandleMetrics) -> bool:
        if trend_side == 1:
            wick_rejection = metric.upper_wick_ratio >= 0.32 and metric.close_position <= 0.62
            opposite_close = metric.direction == -1 and metric.upper_wick_ratio >= 0.18
        else:
            wick_rejection = metric.lower_wick_ratio >= 0.32 and metric.close_position >= 0.38
            opposite_close = metric.direction == 1 and metric.lower_wick_ratio >= 0.18
        weak_body = metric.body_ratio <= 0.42
        return (wick_rejection and weak_body) or opposite_close

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
    def _suggest_expiration(timeframe: int, score: float, pattern: str) -> int:
        allowed = [30, 45, 60, 120, 180, 300]
        if pattern == "cci_reversal":
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
            "cci": 0.0,
            "market_message": NO_EDGE_MESSAGE,
            "reason": "datos insuficientes desde IQ Option",
            "pattern": "none",
        }
