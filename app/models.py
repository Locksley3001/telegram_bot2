from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Direction = Literal["CALL", "PUT", "NONE"]
SignalGrade = Literal["ignore", "weak", "valid", "strong"]
OutcomeStatus = Literal["waiting_entry", "pending", "win", "loss", "push", "aborted"]
BrokerTradeStatus = Literal["placed", "failed"]


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
    factor_score: int = Field(default=0, ge=0, le=6)
    confidence: Literal["high", "low", "discarded"] = "discarded"
    stake_amount: int = 0
    pending_execution_at: Optional[datetime] = None
    analysis_text: str = ""
    is_shadow: bool = False
    blocked_reason: str = ""


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
    factor_score: int = Field(default=0, ge=0, le=6)
    confidence: Literal["high", "low", "discarded"] = "discarded"
    stake_amount: int = 0
    pending_execution_at: Optional[datetime] = None
    analysis_text: str = ""
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
    entry_at: Optional[datetime] = None
    stake_amount: int = 0
    payout_rate: float = 0.85
    result_price: Optional[float] = None
    status: OutcomeStatus = "pending"
    timeframe: int
    suggested_expiration: int
    created_at: datetime
    expires_at: datetime
    resolved_at: Optional[datetime] = None
    main_reason: str
    balance_after: Optional[int] = None
    abort_reason: str = ""
    is_shadow: bool = False
    blocked_reason: str = ""


class BalanceEvent(BaseModel):
    timestamp: datetime
    mark: str
    asset: str = ""
    direction: Direction = "NONE"
    stake_amount: int = 0
    result: str = ""
    profit: int = 0
    balance: int
    note: str = ""


class VirtualBalanceSummary(BaseModel):
    initial_balance: int = 50000
    target_balance: int = 500000
    balance: int = 50000
    safe_stake: int = 20000
    cautious_stake: int = 10000
    payout_rate: float = 0.85
    bankruptcies: int = 0
    targets_hit: int = 0
    consecutive_losses: int = 0
    operations_since_reset: int = 0
    high_confidence_threshold: int = 4
    pause_candles_remaining: int = 0
    post_target_consolidation: bool = False
    post_target_consecutive_wins: int = 0
    last_reset_reason: str = ""
    mode: str = "Proteccion normal"
    history: List[BalanceEvent] = Field(default_factory=list)


class BrokerTrade(BaseModel):
    signal_id: str
    broker_order_id: Optional[str] = None
    status: BrokerTradeStatus
    asset: str
    direction: Direction
    stake_amount: int
    expiration_seconds: int
    balance_mode: str
    requested_at: datetime
    placed_at: Optional[datetime] = None
    error: str = ""


class BrokerTradingSummary(BaseModel):
    enabled: bool = False
    balance_mode: str = "PRACTICE"
    entry_window_seconds: float = 3.0
    total: int = 0
    placed: int = 0
    failed: int = 0
    last_error: str = ""
    recent_trades: List[BrokerTrade] = Field(default_factory=list)


class SupabaseSyncSummary(BaseModel):
    enabled: bool = False
    connected: bool = False
    state_table: str = "bot_state_files"
    versions_table: str = "bot_state_file_versions"
    remote_first: bool = True
    bootstrap_local: bool = False
    remote_save_interval_seconds: float = 60.0
    versioning_enabled: bool = False
    version_interval_seconds: float = 3600.0
    pending_remote_writes: List[str] = Field(default_factory=list)
    skipped_remote_saves: int = 0
    skipped_version_saves: int = 0
    last_error: str = ""
    last_sync_at: Optional[datetime] = None


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
    shadow_total: int = 0
    shadow_resolved: int = 0
    shadow_wins: int = 0
    shadow_losses: int = 0
    shadow_pushes: int = 0
    shadow_pending: int = 0
    shadow_win_rate: float = 0.0
    by_market: List[PerformanceBucket] = Field(default_factory=list)
    by_direction: List[PerformanceBucket] = Field(default_factory=list)
    recent_results: List[SignalOutcome] = Field(default_factory=list)


class LearningSummary(BaseModel):
    enabled: bool = True
    resolved_examples: int = 0
    wins: int = 0
    losses: int = 0
    real_examples: int = 0
    shadow_examples: int = 0
    shadow_wins: int = 0
    shadow_losses: int = 0
    global_win_rate: float = 0.0
    min_win_rate: float = 0.0
    min_history: int = 0
    rules: int = 0
    allowed_signals: int = 0
    blocked_signals: int = 0
    exploration_signals: int = 0
    block_recommendations: int = 0
    last_decision: str = ""
    risky_patterns: List[str] = Field(default_factory=list)
    updated_at: Optional[datetime] = None


class EngineState(BaseModel):
    connected: bool
    broker_status: str
    markets: List[str]
    active_markets: List[str]
    timeframe: int
    snapshots: Dict[str, AnalysisSnapshot]
    signals: List[Signal]
    signal_history_total: int = 0
    performance: PerformanceSummary = Field(default_factory=PerformanceSummary)
    learning: LearningSummary = Field(default_factory=LearningSummary)
    virtual_balance: VirtualBalanceSummary = Field(default_factory=VirtualBalanceSummary)
    broker_trading: BrokerTradingSummary = Field(default_factory=BrokerTradingSummary)
    supabase: SupabaseSyncSummary = Field(default_factory=SupabaseSyncSummary)
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
