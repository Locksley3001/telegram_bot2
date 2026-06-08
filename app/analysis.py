from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from app.models import Candle, CandleMetrics, Signal, SignalOutcome, Zone, utc_now

NO_EDGE_MESSAGE = "MERCADO SIN VENTAJA ESTADISTICA"
CCI_PERIOD = 20
CCI_OVERBOUGHT = 100.0
CCI_OVERSOLD = -100.0
MIN_FORMING_PROGRESS = 0.28


class PriceActionAnalyzer:
    def analyze(self, asset: str, timeframe: int, candles: List[Candle]) -> Tuple[List[Zone], Optional[Signal], dict]:
        usable = [candle for candle in candles if candle.high >= candle.low and candle.open > 0]
        zones = self.detect_zones(usable)
        signal_candles = [candle for candle in usable if candle.is_closed]

        if len(signal_candles) < CCI_PERIOD + 2:
            return zones, None, self._empty_context()

        study_candles = self._study_candles(usable, signal_candles, timeframe)
        metrics = [self._metrics(candle) for candle in study_candles]
        latest = study_candles[-1]
        chosen_side, chosen = self._binary_minute_context(study_candles, metrics, timeframe)

        if chosen_side == 0 or chosen.get("stake_amount", 0) <= 0:
            return zones, None, chosen

        direction = "CALL" if chosen_side == 1 else "PUT"
        grade = "strong" if chosen["confidence"] == "high" else "weak"
        created_at = utc_now()
        pending_execution_at = datetime.fromtimestamp(latest.timestamp + timeframe, tz=timezone.utc)
        signal = Signal(
            id=f"{asset}:{timeframe}:{direction}:{chosen.get('pattern', 'binary1m')}:pending:{int(latest.timestamp)}",
            asset=asset,
            direction=direction,
            score=max(1, min(10, round(chosen["score"]))),
            grade=grade,
            strength=chosen["strength"],
            continuity=chosen["continuity"],
            exhaustion=chosen["exhaustion"],
            cci=chosen["cci"],
            main_reason=chosen["reason"],
            suggested_expiration=60,
            created_at=created_at,
            price=latest.close,
            timeframe=timeframe,
            factor_score=chosen["factor_score"],
            confidence=chosen["confidence"],
            stake_amount=chosen["stake_amount"],
            pending_execution_at=pending_execution_at,
            analysis_text=chosen["analysis_text"],
        )
        return zones, signal, chosen

    def abort_pending_reason(self, record: SignalOutcome, candles: List[Candle]) -> Optional[str]:
        if record.status != "waiting_entry" or record.direction not in {"CALL", "PUT"}:
            return None
        if record.entry_at is None:
            return None

        usable = [candle for candle in candles if candle.high >= candle.low and candle.open > 0]
        entry_candidates = [candle for candle in usable if candle.timestamp >= record.entry_at.timestamp()]
        if not entry_candidates:
            return None

        side = 1 if record.direction == "CALL" else -1
        current = entry_candidates[0]
        context_candles = [candle for candle in usable if candle.timestamp <= current.timestamp]
        if len(context_candles) < CCI_PERIOD + 2:
            return None

        metrics = [self._metrics(candle) for candle in context_candles]
        cci_values = self._cci_series(context_candles, CCI_PERIOD)
        latest_cci = cci_values[-1] if cci_values else 0.0
        previous_cci = cci_values[-2] if len(cci_values) >= 2 else latest_cci
        latest_metric = metrics[-1]
        previous_five = metrics[-6:-1]
        average_body = sum(item.body for item in previous_five) / max(1, len(previous_five))
        is_reversal_setup = "retroceso" in record.id
        pressure_side = -side if is_reversal_setup else side

        opposite_cci_cross = (
            side == 1
            and previous_cci > CCI_OVERBOUGHT
            and latest_cci <= CCI_OVERBOUGHT
        ) or (
            side == -1
            and previous_cci < CCI_OVERSOLD
            and latest_cci >= CCI_OVERSOLD
        )
        if opposite_cci_cross:
            return f"CCI(20) cruzo en sentido contrario al abrir ({latest_cci:.1f})"

        exhaustion_signals = self._binary_exhaustion_signals(pressure_side, context_candles, metrics, cci_values)
        pressure_still_strong = (
            latest_metric.direction == pressure_side
            and latest_metric.body >= average_body * 0.92
            and latest_metric.body_ratio >= 0.42
            and self._clean_impulse(pressure_side, latest_metric)
        )
        pressure_fighting = self._fighting_current_candle(pressure_side, latest_metric, average_body)
        decisive_tired_signals = [
            signal
            for signal in exhaustion_signals
            if signal in {"mecha larga en direccion del trade", "Doji o Pin Bar contrario"}
        ]
        if pressure_fighting:
            decisive_tired_signals.append("vela actual pelea contra el movimiento")
        if is_reversal_setup and pressure_still_strong and not pressure_fighting:
            return "retroceso abortado: el empuje sigue fuerte al abrir"
        if not is_reversal_setup and decisive_tired_signals:
            return f"continuacion abortada: cansancio al abrir ({', '.join(decisive_tired_signals[:3])})"

        opposite_impulse = latest_metric.direction == -side and latest_metric.body >= average_body * 1.2
        if opposite_impulse and latest_metric.body_ratio >= 0.42:
            return "vela inicial invalida la entrada con impulso contrario"

        return None

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

    def _study_candles(self, usable: List[Candle], closed: List[Candle], timeframe: int) -> List[Candle]:
        if not usable:
            return closed
        latest = usable[-1]
        last_closed = closed[-1] if closed else None
        if latest.is_closed or last_closed is None or latest.timestamp <= last_closed.timestamp:
            return closed
        if self._candle_progress(latest, timeframe) < MIN_FORMING_PROGRESS:
            return closed
        return [*closed, latest]

    def _cci_reaction_context(
        self,
        candles: List[Candle],
        metrics: List[CandleMetrics],
        zones: List[Zone],
        timeframe: int,
    ) -> Tuple[int, dict]:
        cci_values = self._cci_series(candles, CCI_PERIOD)
        latest_cci = cci_values[-1] if cci_values else 0.0
        recent_cci = cci_values[-8:] if len(cci_values) >= 8 else cci_values
        latest = candles[-1]
        latest_metric = metrics[-1]
        candle_progress = self._candle_progress(latest, timeframe)
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
        enough_forming_data = latest.is_closed or candle_progress >= MIN_FORMING_PROGRESS
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
        if not enough_forming_data:
            raw_score -= 2.8

        score = max(1.0, min(10.0, raw_score))
        direction_text = "PUT" if signal_side == -1 else "CALL"
        extreme_text = "sobrecompra" if high_extreme else "sobreventa"
        signal_timing = "cerrada" if latest.is_closed else f"en formacion {candle_progress * 100:.0f}%"
        if tired_candle and has_prior_move and enough_forming_data:
            reason = (
                f"CCI(20) en {extreme_text} extrema ({latest_cci:.1f}) con vela de cansancio "
                f"y rechazo ({signal_timing}); entrada contra mercado {direction_text}"
            )
        elif not enough_forming_data:
            reason = f"CCI(20) extremo ({latest_cci:.1f}), esperando mas desarrollo de la vela actual"
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
            "pattern": "cci_reversal" if latest.is_closed else "cci_reversal_live",
            "reason": reason,
            "market_message": NO_EDGE_MESSAGE if score < 7 else "REACCION CCI(20) DETECTADA",
        }

    def _binary_minute_context(
        self,
        candles: List[Candle],
        metrics: List[CandleMetrics],
        timeframe: int,
    ) -> Tuple[int, dict]:
        cci_values = self._cci_series(candles, CCI_PERIOD)
        latest_cci = cci_values[-1] if cci_values else 0.0
        latest = candles[-1]
        latest_metric = metrics[-1]
        recent_cci = cci_values[-10:-1] if len(cci_values) >= 11 else cci_values[:-1]
        high_extreme = latest_cci >= CCI_OVERBOUGHT and (
            not recent_cci or latest_cci >= max(recent_cci) - 5.0 or latest_cci >= 130.0
        )
        low_extreme = latest_cci <= CCI_OVERSOLD and (
            not recent_cci or latest_cci <= min(recent_cci) + 5.0 or latest_cci <= -130.0
        )

        if not high_extreme and not low_extreme:
            context = self._empty_context()
            context.update(
                {
                    "cci": round(latest_cci, 1),
                    "market_message": "ESPERANDO CCI(20) EN EXTREMO OPERABLE",
                    "reason": "CCI(20) no esta rompiendo maximo/minimo relevante",
                    "analysis_text": self._format_binary_analysis(
                        latest,
                        latest_cci,
                        "sin extremo operable",
                        "ausente",
                        "ausente",
                        "no detectado",
                        "sin senales especificas",
                        "lateral",
                        "sin tendencia",
                        "parcial",
                        0,
                        "SENAL DESCARTADA - CCI(20) sin extremo operable",
                        setup_label="observacion",
                    ),
                }
            )
            return 0, context

        pressure_side = 1 if high_extreme else -1
        continuation_side = pressure_side
        reversal_side = -pressure_side
        extreme_label = "sobrecompra rompiendo maximos" if high_extreme else "sobreventa rompiendo minimos"
        previous_five = metrics[-6:-1]
        average_body = sum(item.body for item in previous_five) / max(1, len(previous_five))
        current_clean = self._clean_impulse(pressure_side, latest_metric)
        current_strong = (
            latest_metric.direction == pressure_side
            and latest_metric.body >= average_body * 0.92
            and latest_metric.body_ratio >= 0.42
            and current_clean
        )
        current_fighting = self._fighting_current_candle(pressure_side, latest_metric, average_body)

        last_three = metrics[-3:]
        aligned_three = sum(1 for item in last_three if item.direction == pressure_side)
        last_five = metrics[-5:]
        aligned_five = sum(1 for item in last_five if item.direction == pressure_side)
        continuity_active = aligned_three >= 2 or aligned_five >= 3
        continuity_label = "confirmada" if continuity_active else "ausente"
        continuity = min(10.0, aligned_three * 2.4 + aligned_five * 1.1)

        pressure_strength = self._strength(pressure_side, metrics[-8:])
        exhaustion_signals = self._binary_exhaustion_signals(pressure_side, candles, metrics, cci_values)
        if current_fighting and "vela actual pelea contra el movimiento" not in exhaustion_signals:
            exhaustion_signals.append("vela actual pelea contra el movimiento")
        decisive_tired_signals = [
            signal
            for signal in exhaustion_signals
            if signal
            in {
                "mecha larga en direccion del trade",
                "Doji o Pin Bar contrario",
                "vela actual pelea contra el movimiento",
            }
        ]
        if not current_strong:
            decisive_tired_signals.extend(
                signal for signal in exhaustion_signals if signal == "CCI extremo con perdida de momentum"
            )
        tired_enough = current_fighting or bool(decisive_tired_signals)
        exhaustion_blocked = len(decisive_tired_signals) >= 3
        exhaustion_label = "detectado" if tired_enough else "no detectado"
        exhaustion = min(10.0, len(exhaustion_signals) * 2.7 + (2.0 if current_fighting else 1.0))
        trend_side, trend_label, trend_strength = self._trend_context(candles[-15:])
        if trend_side == 0 and continuity_active:
            trend_side = pressure_side
            trend_label = "alcista" if trend_side == 1 else "bajista"

        price_breakout = self._price_breakout(pressure_side, candles)
        if current_strong and continuity_active and not tired_enough:
            signal_side = continuation_side
            setup_label = "continuacion"
            setup_reason = "CCI extremo con fuerza limpia; se opera a favor del empuje"
        elif continuity_active and tired_enough:
            signal_side = reversal_side
            setup_label = "retroceso"
            setup_reason = "CCI extremo con movimiento cansado; se opera retroceso contra el empuje"
        elif current_strong and trend_side == pressure_side:
            signal_side = continuation_side
            setup_label = "continuacion"
            setup_reason = "tendencia y vela actual sostienen la presion"
        else:
            signal_side = 0
            setup_label = "bloqueada"
            setup_reason = "CCI extremo, pero falta decidir entre continuidad y retroceso"

        direction = "CALL" if signal_side == 1 else "PUT" if signal_side == -1 else "NONE"
        direction_text = "alcista" if signal_side == 1 else "bajista" if signal_side == -1 else "sin direccion"
        trend_relation = (
            "a favor" if trend_side == signal_side else "en contra" if trend_side == -signal_side else "sin tendencia"
        )
        confluence_invalid = bool(
            signal_side == reversal_side and trend_strength >= 2 and current_strong and not tired_enough
        )
        confluence_total = (
            signal_side == continuation_side and current_strong and continuity_active and trend_side == signal_side
        ) or (
            signal_side == reversal_side and tired_enough and continuity_active and trend_side == pressure_side
        )
        confluence_label = "invalida" if confluence_invalid else "total" if confluence_total else "parcial"

        factor_score = 1
        factor_score += 1 if price_breakout else 0
        factor_score += 1 if current_strong or tired_enough else 0
        factor_score += 1 if continuity_active else 0
        factor_score += 1 if (signal_side == continuation_side and trend_side == signal_side) or (
            signal_side == reversal_side and trend_side == pressure_side and tired_enough
        ) else 0
        factor_score += 1 if confluence_total else 0

        score = max(1.0, min(10.0, 1.0 + factor_score * 1.5))
        confidence = "discarded"
        stake_amount = 0
        decision = f"SENAL DESCARTADA - {setup_reason}"
        blockers: List[str] = []
        if signal_side == 0:
            blockers.append(setup_reason)
        if exhaustion_blocked and signal_side == continuation_side:
            blockers.append("demasiado cansancio para continuar tendencia")
        if confluence_invalid:
            blockers.append("tendencia fuerte sin cansancio suficiente para contra-tendencia")

        if blockers:
            decision = f"SENAL DESCARTADA - {'; '.join(blockers)}"
        elif factor_score >= 4:
            confidence = "high"
            stake_amount = 20000
            decision = f"OPERACION {direction} - $20.000 - al abrir siguiente vela ({setup_label})"
        elif factor_score >= 2:
            confidence = "low"
            stake_amount = 10000
            decision = f"OPERACION {direction} - $10.000 - al abrir siguiente vela ({setup_label})"

        if tired_enough:
            exhaustion_detail = ", ".join(exhaustion_signals) if exhaustion_signals else "sin senales especificas"
        elif exhaustion_signals:
            exhaustion_detail = f"contexto extendido no bloqueante: {', '.join(exhaustion_signals)}"
        else:
            exhaustion_detail = "sin senales especificas"
        reason = (
            f"PENDIENTE - Ejecutar al abrir la siguiente vela de 1 minuto. "
            f"CCI(20) en {extreme_label} ({latest_cci:.1f}); setup {setup_label}; {factor_score}/6 factores."
        )
        if confidence == "discarded":
            reason = decision

        strength_label = "alta" if current_strong else "baja" if latest_metric.direction == pressure_side else "ausente"
        analysis_text = self._format_binary_analysis(
            latest,
            latest_cci,
            extreme_label,
            strength_label,
            continuity_label,
            exhaustion_label,
            exhaustion_detail,
            trend_label,
            trend_relation,
            confluence_label,
            factor_score,
            decision,
            body=latest_metric.body,
            average_body=average_body,
            last_three=self._last_three_label(last_three),
            direction_text=direction_text,
            setup_label=setup_label,
        )

        learning_event = None if confidence != "discarded" else f"{setup_reason}; CCI {extreme_label}; {factor_score}/6"
        shadow_direction = direction
        if shadow_direction == "NONE":
            if current_strong:
                shadow_direction = "CALL" if continuation_side == 1 else "PUT"
            elif tired_enough:
                shadow_direction = "CALL" if reversal_side == 1 else "PUT"
            elif trend_side == pressure_side:
                shadow_direction = "CALL" if continuation_side == 1 else "PUT"
            else:
                shadow_direction = "CALL" if reversal_side == 1 else "PUT"
        return signal_side, {
            "score": score,
            "strength": round(pressure_strength if current_strong else max(1.0, pressure_strength * 0.65), 1),
            "continuity": round(continuity, 1),
            "exhaustion": round(exhaustion, 1),
            "cci": round(latest_cci, 1),
            "factor_score": factor_score,
            "confidence": confidence,
            "stake_amount": stake_amount,
            "reason": reason,
            "analysis_text": analysis_text,
            "market_message": decision,
            "pattern": f"cci_extreme_{setup_label}",
            "trend_label": trend_label,
            "trend_relation": trend_relation,
            "confluence": confluence_label,
            "learning_event": learning_event,
            "analysis_candle_ts": int(latest.timestamp),
            "shadow_direction": shadow_direction,
            "shadow_reason": setup_reason,
        }

    @staticmethod
    def _binary_exhaustion_signals(
        side: int,
        candles: List[Candle],
        metrics: List[CandleMetrics],
        cci_values: List[float],
    ) -> List[str]:
        signals: List[str] = []
        latest_metric = metrics[-1]
        wick_in_trade_direction = latest_metric.upper_wick_ratio if side == 1 else latest_metric.lower_wick_ratio
        if wick_in_trade_direction > 0.60:
            signals.append("mecha larga en direccion del trade")

        streak = 0
        for item in reversed(metrics):
            if item.direction == side:
                streak += 1
            else:
                break
        if streak >= 5:
            signals.append("5+ velas consecutivas sin retroceso")

        latest_cci = cci_values[-1] if cci_values else 0.0
        previous_cci = cci_values[-2] if len(cci_values) >= 2 else latest_cci
        cci_extreme = latest_cci > 180 or latest_cci < -180
        cci_losing_momentum = abs(latest_cci) <= abs(previous_cci)
        if cci_extreme and cci_losing_momentum:
            signals.append("CCI extremo con perdida de momentum")

        latest = candles[-1]
        opposite_pin = (
            (side == 1 and latest.close < latest.open and latest_metric.upper_wick_ratio >= 0.45)
            or (side == -1 and latest.close > latest.open and latest_metric.lower_wick_ratio >= 0.45)
        )
        if latest_metric.body_ratio <= 0.10 or opposite_pin:
            signals.append("Doji o Pin Bar contrario")
        return signals

    @staticmethod
    def _clean_impulse(side: int, metric: CandleMetrics) -> bool:
        opposing_wick = metric.lower_wick_ratio if side == 1 else metric.upper_wick_ratio
        return opposing_wick <= 0.32 and metric.body_ratio >= 0.42

    @staticmethod
    def _fighting_current_candle(side: int, metric: CandleMetrics, average_body: float) -> bool:
        opposing_wick = metric.upper_wick_ratio if side == 1 else metric.lower_wick_ratio
        small_body = metric.body <= average_body * 0.78 or metric.body_ratio <= 0.28
        opposite_body = metric.direction == -side and metric.body_ratio >= 0.22
        heavy_rejection = opposing_wick >= 0.38
        return (small_body and heavy_rejection) or opposite_body

    @staticmethod
    def _price_breakout(side: int, candles: List[Candle]) -> bool:
        if len(candles) < 8:
            return False
        latest = candles[-1]
        previous = candles[-8:-1]
        if side == 1:
            return latest.high >= max(candle.high for candle in previous)
        return latest.low <= min(candle.low for candle in previous)

    @staticmethod
    def _trend_context(candles: List[Candle]) -> Tuple[int, str, int]:
        if len(candles) < 10:
            return 0, "lateral", 0
        recent = candles[-15:]
        ranges = [max(item.high - item.low, 0.0) for item in recent]
        avg_range = sum(ranges) / max(1, len(ranges))
        threshold = max(avg_range * 0.45, recent[-1].close * 0.00018)
        high_delta = recent[-1].high - recent[0].high
        low_delta = recent[-1].low - recent[0].low
        if high_delta > threshold and low_delta > threshold:
            strength = 2 if high_delta > avg_range and low_delta > avg_range else 1
            return 1, "alcista", strength
        if high_delta < -threshold and low_delta < -threshold:
            strength = 2 if abs(high_delta) > avg_range and abs(low_delta) > avg_range else 1
            return -1, "bajista", strength
        return 0, "lateral", 0

    @staticmethod
    def _last_three_label(metrics: List[CandleMetrics]) -> str:
        labels = []
        for item in metrics:
            if item.direction == 1:
                labels.append("verde")
            elif item.direction == -1:
                labels.append("roja")
            else:
                labels.append("doji")
        return "/".join(labels)

    @staticmethod
    def _format_binary_analysis(
        candle: Candle,
        cci: float,
        cci_label: str,
        strength_label: str,
        continuity_label: str,
        exhaustion_label: str,
        exhaustion_detail: str,
        trend_label: str,
        trend_relation: str,
        confluence_label: str,
        factor_score: int,
        decision: str,
        *,
        body: float = 0.0,
        average_body: float = 0.0,
        last_three: str = "-",
        direction_text: str = "-",
        setup_label: str = "-",
    ) -> str:
        timestamp = datetime.fromtimestamp(candle.timestamp, tz=timezone.utc).strftime("%H:%M")
        return "\n".join(
            [
                f"[ANALISIS VELA - {timestamp}]",
                f"Setup: {setup_label}",
                f"CCI(20): {cci:.1f} -> {cci_label}",
                f"Fuerza: {strength_label} - cuerpo {body:.6f} vs promedio {average_body:.6f}",
                f"Continuidad: {continuity_label} - ultimas 3 velas {last_three}",
                f"Cansancio: {exhaustion_label} -> {exhaustion_detail}",
                f"Tendencia: {trend_label} - operar {trend_relation}",
                f"Confluencia: {confluence_label}",
                "",
                f"PUNTUACION: {factor_score}/6",
                "",
                "DECISION:",
                f"-> {decision}",
                "",
                "APRENDIZAJE ACTIVO:",
                f"- Evitando: entradas dentro de la vela activa y operaciones con cansancio confirmado ({direction_text}).",
                "- Aprendiendo a evitar: extremos CCI ambiguos donde no se distingue continuidad de retroceso.",
                "- Operando si o si cuando: CCI rompe extremo y la fuerza/continuidad/cansancio definen direccion.",
                "- Jamas opero cuando: saldo insuficiente, confluencia invalida o vela actual contradice el setup.",
            ]
        )

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
    def _candle_progress(candle: Candle, timeframe: int) -> float:
        if candle.is_closed:
            return 1.0
        now = datetime.now(timezone.utc).timestamp()
        elapsed = max(0.0, now - candle.timestamp)
        return max(0.0, min(1.0, elapsed / max(float(timeframe), 1.0)))

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
        if pattern in {"cci_reversal", "cci_reversal_live"}:
            target = 60 if timeframe <= 60 else min(300, timeframe)
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
