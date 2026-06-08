from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from app.models import LearningSummary, Signal, SignalOutcome


@dataclass(frozen=True)
class RuleMatch:
    key: str
    label: str
    weight: float
    specificity: int


@dataclass(frozen=True)
class LearningDecision:
    allowed: bool
    estimated_win_rate: float
    evidence_samples: int
    reason: str


class SignalLearningSystem:
    """Learns which signal contexts deserve to be traded from resolved outcomes."""

    def __init__(
        self,
        path: Path,
        *,
        enabled: bool = True,
        min_history: int = 30,
        min_win_rate: float = 58.0,
        min_rule_samples: int = 5,
        min_similarity_samples: int = 4,
        exploration_interval: int = 20,
    ) -> None:
        self.path = path
        self.enabled = enabled
        self.min_history = max(1, min_history)
        self.min_win_rate = max(1.0, min(95.0, min_win_rate))
        self.min_rule_samples = max(1, min_rule_samples)
        self.min_similarity_samples = max(1, min_similarity_samples)
        self.exploration_interval = max(0, exploration_interval)
        self.rules: Dict[str, Dict[str, float]] = {}
        self.resolved_examples = 0
        self.wins = 0
        self.losses = 0
        self.real_examples = 0
        self.shadow_examples = 0
        self.shadow_wins = 0
        self.shadow_losses = 0
        self.updated_at: Optional[datetime] = None
        self.allowed_signals = 0
        self.blocked_signals = 0
        self.exploration_signals = 0
        self.block_recommendations = 0
        self.last_decision = ""
        self._signature = ""
        self._load_decision_counters()

    def rebuild(self, records: Iterable[SignalOutcome]) -> None:
        resolved = [
            record
            for record in records
            if record.status in {"win", "loss"} and record.direction in {"CALL", "PUT"}
        ]
        signature = self._records_signature(resolved)
        if signature == self._signature and self.rules:
            return

        rules: Dict[str, Dict[str, float]] = {}
        for record in resolved:
            result = "wins" if record.status == "win" else "losses"
            for match in self._matches_from_record(record):
                bucket = rules.setdefault(
                    match.key,
                    {
                        "wins": 0.0,
                        "losses": 0.0,
                        "weight": match.weight,
                        "specificity": float(match.specificity),
                        "label": match.label,
                    },
                )
                bucket[result] += 1.0

        self.rules = rules
        self.resolved_examples = len(resolved)
        self.wins = sum(1 for record in resolved if record.status == "win")
        self.losses = sum(1 for record in resolved if record.status == "loss")
        self.real_examples = sum(1 for record in resolved if not record.is_shadow)
        self.shadow_examples = sum(1 for record in resolved if record.is_shadow)
        self.shadow_wins = sum(1 for record in resolved if record.is_shadow and record.status == "win")
        self.shadow_losses = sum(1 for record in resolved if record.is_shadow and record.status == "loss")
        self.updated_at = datetime.now(timezone.utc)
        self._signature = signature
        self._save()

    def decide(self, signal: Signal) -> LearningDecision:
        if not self.enabled:
            decision = LearningDecision(True, 0.0, 0, "Aprendizaje desactivado.")
            self._remember_decision(decision)
            return decision

        if self.resolved_examples < self.min_history:
            rate = self.global_win_rate
            decision = LearningDecision(
                True,
                rate,
                self.resolved_examples,
                f"Aprendizaje observando: {self.resolved_examples}/{self.min_history} casos resueltos.",
            )
            self._remember_decision(decision)
            return decision

        matches = self._matches_from_signal(signal)
        estimate = self._estimate(matches, signal.score)
        required_rate = self._required_rate(signal.score)
        blocking_rule = self._blocking_rule(matches, required_rate)

        if blocking_rule is not None:
            reason = self._format_block_reason(blocking_rule, required_rate)
            decision = LearningDecision(False, self._rule_rate(blocking_rule), int(self._rule_total(blocking_rule)), reason)
            exploration = self._exploration_decision(signal, decision)
            if exploration is not None:
                self.block_recommendations += 1
                self._remember_decision(exploration)
                return exploration
            self._remember_decision(decision)
            return decision

        if estimate["samples"] >= self.min_similarity_samples and estimate["rate"] < required_rate:
            strongest = estimate["primary_rule"]
            reason = (
                f"Aprendizaje bloqueo: casos parecidos {estimate['rate'] * 100:.1f}% "
                f"({estimate['samples']} muestras); minimo {required_rate * 100:.1f}%."
            )
            if strongest is not None:
                reason += f" Patron principal: {strongest['label']}."
            decision = LearningDecision(False, estimate["rate"], estimate["samples"], reason)
            exploration = self._exploration_decision(signal, decision)
            if exploration is not None:
                self.block_recommendations += 1
                self._remember_decision(exploration)
                return exploration
            self._remember_decision(decision)
            return decision

        reason = (
            f"Aprendizaje permite: estimado {estimate['rate'] * 100:.1f}% "
            f"con {estimate['samples']} muestras similares."
        )
        decision = LearningDecision(True, estimate["rate"], estimate["samples"], reason)
        self._remember_decision(decision)
        return decision

    def annotate_signal(self, signal: Signal, decision: LearningDecision) -> Signal:
        if not decision.allowed:
            return signal
        suffix = f"Aprendizaje: {decision.estimated_win_rate * 100:.1f}% historico"
        if suffix in signal.main_reason:
            return signal
        signal.main_reason = f"{signal.main_reason} | {suffix}"
        return signal

    def remember_technical_block(self, reason: str) -> None:
        cleaned = reason.strip()
        if not cleaned:
            return
        self.block_recommendations += 1
        self.last_decision = f"Aprendizaje observa bloqueo tecnico: {cleaned}"
        self._save()

    def summary(self) -> LearningSummary:
        risky = self._risky_patterns()
        return LearningSummary(
            enabled=self.enabled,
            resolved_examples=self.resolved_examples,
            wins=self.wins,
            losses=self.losses,
            real_examples=self.real_examples,
            shadow_examples=self.shadow_examples,
            shadow_wins=self.shadow_wins,
            shadow_losses=self.shadow_losses,
            global_win_rate=round(self.global_win_rate * 100.0, 1),
            min_win_rate=round(self.min_win_rate, 1),
            min_history=self.min_history,
            rules=len(self.rules),
            allowed_signals=self.allowed_signals,
            blocked_signals=self.blocked_signals,
            exploration_signals=self.exploration_signals,
            block_recommendations=self.block_recommendations,
            last_decision=self.last_decision,
            risky_patterns=risky,
            updated_at=self.updated_at,
        )

    @property
    def global_win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total else 0.5

    def _estimate(self, matches: List[RuleMatch], score: int) -> dict:
        prior_rate = self.global_win_rate
        prior_strength = 6.0
        weighted_rate = 0.0
        total_weight = 0.0
        specific_samples = 0
        primary_rule: Optional[dict] = None

        for match in matches:
            rule = self.rules.get(match.key)
            if rule is None:
                continue
            total = self._rule_total(rule)
            if match.key != "global" and total < self.min_rule_samples:
                continue
            smoothed_rate = (rule["wins"] + prior_rate * prior_strength) / (total + prior_strength)
            confidence = min(1.0, total / 14.0)
            weight = float(rule.get("weight", match.weight)) * max(0.35, confidence)
            weighted_rate += smoothed_rate * weight
            total_weight += weight
            if match.key != "global":
                specific_samples = max(specific_samples, int(total))
                if primary_rule is None or self._rule_total(rule) > self._rule_total(primary_rule):
                    primary_rule = rule

        if total_weight <= 0:
            fallback = max(0.01, min(0.99, prior_rate + (score - 7) * 0.015))
            return {"rate": fallback, "samples": 0, "primary_rule": None}
        return {"rate": weighted_rate / total_weight, "samples": specific_samples, "primary_rule": primary_rule}

    def _blocking_rule(self, matches: List[RuleMatch], required_rate: float) -> Optional[dict]:
        worst: Optional[dict] = None
        for match in matches:
            rule = self.rules.get(match.key)
            if rule is None or match.key == "global":
                continue
            total = self._rule_total(rule)
            if total < self.min_rule_samples:
                continue
            rate = self._rule_rate(rule)
            specificity = int(rule.get("specificity", match.specificity))
            if specificity >= 2 and rate <= required_rate - 0.08:
                if worst is None or rate < self._rule_rate(worst):
                    worst = rule
        return worst

    def _required_rate(self, score: int) -> float:
        base = self.min_win_rate / 100.0
        if score >= 10:
            return max(0.50, base - 0.03)
        if score >= 9:
            return max(0.51, base - 0.015)
        if score <= 7:
            return min(0.70, base + 0.02)
        return base

    def _format_block_reason(self, rule: dict, required_rate: float) -> str:
        wins = int(rule["wins"])
        losses = int(rule["losses"])
        total = wins + losses
        rate = self._rule_rate(rule) * 100.0
        return (
            f"Aprendizaje bloqueo: {rule['label']} rinde {rate:.1f}% "
            f"({wins}G/{losses}P, {total} casos); minimo {required_rate * 100:.1f}%."
        )

    def _matches_from_record(self, record: SignalOutcome) -> List[RuleMatch]:
        return self._matches(
            asset=record.asset,
            direction=record.direction,
            timeframe=record.timeframe,
            score=record.score,
            strength=record.strength,
            continuity=record.continuity,
            exhaustion=record.exhaustion,
            cci=record.cci,
        )

    def _matches_from_signal(self, signal: Signal) -> List[RuleMatch]:
        return self._matches(
            asset=signal.asset,
            direction=signal.direction,
            timeframe=signal.timeframe,
            score=signal.score,
            strength=signal.strength,
            continuity=signal.continuity,
            exhaustion=signal.exhaustion,
            cci=signal.cci,
        )

    def _matches(
        self,
        *,
        asset: str,
        direction: str,
        timeframe: int,
        score: int,
        strength: float,
        continuity: float,
        exhaustion: float,
        cci: float,
    ) -> List[RuleMatch]:
        asset = self._normalize_asset(asset)
        direction = direction.upper()
        score_band = f"score:{score}"
        strength_band = self._metric_band("fuerza", strength)
        continuity_band = self._metric_band("continuidad", continuity)
        exhaustion_band = self._metric_band("cansancio", exhaustion)
        cci_side = "sobrecompra" if cci > 0 else "sobreventa"
        cci_band = self._cci_band(cci)
        metric_signature = (
            f"{direction}|{score_band}|{strength_band}|{continuity_band}|{exhaustion_band}|{cci_side}|{cci_band}"
        )
        return [
            RuleMatch("global", "historial completo", 0.45, 0),
            RuleMatch(f"asset:{asset}", f"mercado {asset}", 1.05, 1),
            RuleMatch(f"direction:{direction}", f"direccion {direction}", 0.80, 1),
            RuleMatch(f"timeframe:{timeframe}", f"timeframe {timeframe}s", 0.45, 1),
            RuleMatch(f"score:{score}", f"score {score}/10", 0.70, 1),
            RuleMatch(f"asset_direction:{asset}:{direction}", f"{asset} {direction}", 1.55, 2),
            RuleMatch(f"direction_score:{direction}:{score}", f"{direction} con score {score}/10", 1.05, 2),
            RuleMatch(f"asset_score:{asset}:{score}", f"{asset} con score {score}/10", 1.15, 2),
            RuleMatch(
                f"asset_direction_score:{asset}:{direction}:{score}",
                f"{asset} {direction} score {score}/10",
                1.95,
                3,
            ),
            RuleMatch(
                f"cci:{direction}:{cci_side}:{cci_band}",
                f"{direction} en {cci_side} CCI {cci_band}",
                1.15,
                2,
            ),
            RuleMatch(
                f"strength:{direction}:{strength_band}",
                f"{direction} fuerza {strength_band}",
                0.95,
                2,
            ),
            RuleMatch(
                f"continuity:{direction}:{continuity_band}",
                f"{direction} continuidad {continuity_band}",
                0.85,
                2,
            ),
            RuleMatch(
                f"exhaustion:{direction}:{exhaustion_band}",
                f"{direction} cansancio {exhaustion_band}",
                0.90,
                2,
            ),
            RuleMatch(f"metrics:{metric_signature}", f"perfil {metric_signature}", 2.20, 3),
            RuleMatch(f"asset_metrics:{asset}:{metric_signature}", f"{asset} perfil {metric_signature}", 2.65, 4),
        ]

    def _risky_patterns(self) -> List[str]:
        candidates = []
        for rule in self.rules.values():
            total = self._rule_total(rule)
            if total < self.min_rule_samples:
                continue
            rate = self._rule_rate(rule)
            specificity = int(rule.get("specificity", 0))
            if specificity >= 1 and rate < self.min_win_rate / 100.0:
                candidates.append((rate, total, rule["label"], int(rule["wins"]), int(rule["losses"])))
        candidates.sort(key=lambda item: (item[0], -item[1]))
        return [
            f"{label}: {rate * 100:.1f}% ({wins}G/{losses}P)"
            for rate, _total, label, wins, losses in candidates[:5]
        ]

    def _remember_decision(self, decision: LearningDecision) -> None:
        if decision.allowed:
            self.allowed_signals += 1
            if decision.reason.startswith("Aprendizaje permite exploracion controlada"):
                self.exploration_signals += 1
        else:
            self.blocked_signals += 1
            self.block_recommendations += 1
        self.last_decision = decision.reason
        self._save()

    def _exploration_decision(self, signal: Signal, blocked: LearningDecision) -> Optional[LearningDecision]:
        if self.exploration_interval <= 0:
            return None
        if signal.score < 8:
            return None
        next_blocked_count = self.blocked_signals + 1
        if next_blocked_count % self.exploration_interval != 0:
            return None
        reason = (
            "Aprendizaje permite exploracion controlada: "
            f"senal score {signal.score}/10 despues de {next_blocked_count} bloqueos. "
            f"Motivo original: {blocked.reason}"
        )
        return LearningDecision(True, blocked.estimated_win_rate, blocked.evidence_samples, reason)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "enabled": self.enabled,
            "resolved_examples": self.resolved_examples,
            "wins": self.wins,
            "losses": self.losses,
            "real_examples": self.real_examples,
            "shadow_examples": self.shadow_examples,
            "shadow_wins": self.shadow_wins,
            "shadow_losses": self.shadow_losses,
            "global_win_rate": round(self.global_win_rate * 100.0, 4),
            "min_win_rate": self.min_win_rate,
            "min_history": self.min_history,
            "min_rule_samples": self.min_rule_samples,
            "min_similarity_samples": self.min_similarity_samples,
            "exploration_interval": self.exploration_interval,
            "allowed_signals": self.allowed_signals,
            "blocked_signals": self.blocked_signals,
            "exploration_signals": self.exploration_signals,
            "block_recommendations": self.block_recommendations,
            "last_decision": self.last_decision,
            "signature": self._signature,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "rules": self.rules,
            "risky_patterns": self._risky_patterns(),
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        backup_path = self.path.with_suffix(f"{self.path.suffix}.bak")
        temp_path.write_text(encoded, encoding="utf-8")
        if self.path.exists():
            backup_path.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        temp_path.replace(self.path)

    def _load_decision_counters(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.allowed_signals = int(payload.get("allowed_signals", 0))
            self.blocked_signals = int(payload.get("blocked_signals", 0))
            self.exploration_signals = int(payload.get("exploration_signals", 0))
            self.block_recommendations = int(
                payload.get("block_recommendations", self.blocked_signals + self.exploration_signals)
            )
            self.last_decision = str(payload.get("last_decision", ""))
            self.real_examples = int(payload.get("real_examples", 0))
            self.shadow_examples = int(payload.get("shadow_examples", 0))
            self.shadow_wins = int(payload.get("shadow_wins", 0))
            self.shadow_losses = int(payload.get("shadow_losses", 0))
            self._signature = str(payload.get("signature", ""))
            updated_at = payload.get("updated_at")
            if isinstance(updated_at, str) and updated_at:
                self.updated_at = datetime.fromisoformat(updated_at)
        except Exception:
            self.allowed_signals = 0
            self.blocked_signals = 0
            self.exploration_signals = 0
            self.block_recommendations = 0
            self.last_decision = ""
            self.real_examples = 0
            self.shadow_examples = 0
            self.shadow_wins = 0
            self.shadow_losses = 0
            self._signature = ""

    @staticmethod
    def _records_signature(records: List[SignalOutcome]) -> str:
        digest = hashlib.sha256()
        for record in sorted(records, key=lambda item: item.id):
            digest.update(record.id.encode("utf-8", errors="ignore"))
            digest.update(str(record.status).encode("ascii"))
            digest.update(str(record.result_price).encode("ascii"))
            digest.update(str(record.is_shadow).encode("ascii"))
        return digest.hexdigest()

    @staticmethod
    def _rule_total(rule: dict) -> float:
        return float(rule.get("wins", 0.0)) + float(rule.get("losses", 0.0))

    @classmethod
    def _rule_rate(cls, rule: dict) -> float:
        total = cls._rule_total(rule)
        return float(rule.get("wins", 0.0)) / total if total else 0.0

    @staticmethod
    def _metric_band(name: str, value: float) -> str:
        value = max(0.0, min(10.0, float(value or 0.0)))
        lower = int(math.floor(value))
        upper = min(10, lower + 1)
        return f"{name}:{lower}-{upper}"

    @staticmethod
    def _cci_band(value: float) -> str:
        absolute = abs(float(value or 0.0))
        lower = int(absolute // 25) * 25
        upper = lower + 24
        return f"{lower}-{upper}"

    @staticmethod
    def _normalize_asset(asset: str) -> str:
        return asset.strip().upper().replace(" ", "").replace("_", "-")
