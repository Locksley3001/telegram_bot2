from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Direction = Literal["CALL", "PUT", "NONE"]
SignalGrade = Literal["ignore", "weak", "valid", "strong"]
OutcomeStatus = Literal["pending", "win", "loss", "push"]


class Candle(BaseModel):
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    is_closed: bool = True


class Zone(BaseModel):
    price: float
    kind: Literal["support", "resistance"]
    touches: int
    strength: float


class Signal(BaseModel):
    id: str
    asset: str
    direction: Direction
    score: int = Field(ge=1, le=10)
    grade: SignalGrade
    strength: float
    continuity: float
    exhaustion: float
    cci: float = 0.0
    main_reason: str
    suggested_expiration: int
    created_at: datetime
    price: float
    timeframe: int


class AnalysisSnapshot(BaseModel):
    asset: str
    timeframe: int
    candles: List[Candle]
    zones: List[Zone]
    signal: Optional[Signal] = None
    market_message: str
    strength: float
    continuity: float
    exhaustion: float
    cci: float = 0.0
    updated_at: datetime


class SignalOutcome(BaseModel):
    id: str
    asset: str
    direction: Direction
    score: int
    strength: float
    continuity: float
    exhaustion: float
    cci: float = 0.0
    entry_price: float
    result_price: Optional[float] = None
    status: OutcomeStatus = "pending"
    timeframe: int
    suggested_expiration: int
    created_at: datetime
    expires_at: datetime
    resolved_at: Optional[datetime] = None
    main_reason: str


class PerformanceBucket(BaseModel):
    name: str
    total: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    pending: int = 0
    win_rate: float = 0.0


class PerformanceSummary(BaseModel):
    total: int = 0
    resolved: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    pending: int = 0
    win_rate: float = 0.0
    avg_score: float = 0.0
    by_market: List[PerformanceBucket] = Field(default_factory=list)
    by_direction: List[PerformanceBucket] = Field(default_factory=list)
    recent_results: List[SignalOutcome] = Field(default_factory=list)


class EngineState(BaseModel):
    connected: bool
    broker_status: str
    markets: List[str]
    active_markets: List[str]
    timeframe: int
    snapshots: Dict[str, AnalysisSnapshot]
    signals: List[Signal]
    performance: PerformanceSummary = Field(default_factory=PerformanceSummary)
    last_error: Optional[str] = None


@dataclass(frozen=True)
class CandleMetrics:
    direction: int
    body: float
    range_size: float
    body_ratio: float
    upper_wick_ratio: float
    lower_wick_ratio: float
    close_position: float


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
