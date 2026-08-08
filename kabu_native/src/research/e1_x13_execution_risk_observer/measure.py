"""Pure Fixed-100 execution risk measurement — OBSERVER_ONLY; no ENTRY blocking."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

LOT = 100
BOARD_FRESH_MAX_SEC = 3.0
QTY_MIN = 100

STATUS_PASS = "EXECUTION_RISK_OBSERVER_PASS"
STATUS_FAIL = "EXECUTION_RISK_OBSERVER_FAIL"
STATUS_NE = "EXECUTION_RISK_OBSERVER_NOT_EVALUABLE"


@dataclass
class MeasurementInput:
    symbol: str
    event_time: Any
    best_bid: Optional[float]
    best_ask: Optional[float]
    best_bid_qty: Optional[float]
    best_ask_qty: Optional[float]
    bid_time: Any = None
    ask_time: Any = None
    reference_price: Optional[float] = None
    tick_size: Optional[float] = None
    board_age_sec: Optional[float] = None
    rolling_spread_cost_p95: Optional[float] = None
    rolling_down_bid_jump_p95: Optional[float] = None
    rolling_executable_loss_5s_p95: Optional[float] = None


@dataclass
class MeasurementOutput:
    symbol: str
    event_time: Any
    one_lot_notional_yen: Optional[float]
    one_tick_risk_yen_100: Optional[float]
    current_spread_cost_yen_100: Optional[float]
    estimated_execution_risk_yen: Optional[float]
    board_age_sec: Optional[float]
    bid_depth_pass: Optional[bool]
    ask_depth_pass: Optional[bool]
    board_freshness_pass: Optional[bool]
    measurement_status: str
    reason_codes: list[str] = field(default_factory=list)
    # explicit separation — never conflate
    execution_risk: Optional[float] = None
    strategy_loss_risk: str = "unresolved"
    total_trade_risk: str = "unresolved"
    mode: str = "OBSERVER_ONLY"
    enforcement: bool = False
    entry_blocking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def measure(inp: MeasurementInput) -> MeasurementOutput:
    """Compute observer metrics. Does not grant ENTRY permission."""
    reasons: list[str] = []
    bid, ask = inp.best_bid, inp.best_ask
    if bid is None:
        reasons.append("NO_BID")
    if ask is None:
        reasons.append("NO_ASK")

    spread_cost = None
    if bid is not None and ask is not None:
        if ask < bid:
            reasons.append("INVALID_SPREAD")
        else:
            spread_cost = max(ask - bid, 0.0) * LOT

    one_lot = (ask * LOT) if ask is not None else None
    one_tick = (inp.tick_size * LOT) if inp.tick_size is not None else None
    if inp.tick_size is None:
        reasons.append("TICK_SIZE_UNRESOLVED")
    if inp.reference_price is None:
        reasons.append("REFERENCE_PRICE_UNRESOLVED")

    bid_depth_pass = None
    ask_depth_pass = None
    if inp.best_bid_qty is None:
        reasons.append("BID_DEPTH_LT_100")  # missing treated as fail depth
        bid_depth_pass = False
    else:
        bid_depth_pass = inp.best_bid_qty >= QTY_MIN
        if not bid_depth_pass:
            reasons.append("BID_DEPTH_LT_100")
    if inp.best_ask_qty is None:
        reasons.append("ASK_DEPTH_LT_100")
        ask_depth_pass = False
    else:
        ask_depth_pass = inp.best_ask_qty >= QTY_MIN
        if not ask_depth_pass:
            reasons.append("ASK_DEPTH_LT_100")

    board_age = inp.board_age_sec
    board_fresh = None
    if board_age is None:
        reasons.append("BOARD_TIME_MISSING")
        board_fresh = False
    else:
        board_fresh = board_age <= BOARD_FRESH_MAX_SEC
        if not board_fresh:
            reasons.append("BOARD_STALE")

    comps = [
        inp.rolling_spread_cost_p95,
        inp.rolling_down_bid_jump_p95,
        inp.rolling_executable_loss_5s_p95,
    ]
    comps_f = [float(c) for c in comps if c is not None]
    if len(comps_f) < 1:
        reasons.append("RISK_HISTORY_INSUFFICIENT")
        est = None
    else:
        est = max(comps_f)

    # status
    hard_ne = {"NO_BID", "NO_ASK", "TICK_SIZE_UNRESOLVED", "RISK_HISTORY_INSUFFICIENT"}
    if hard_ne & set(reasons) or est is None or one_lot is None:
        status = STATUS_NE
    elif reasons:
        status = STATUS_FAIL
    else:
        status = STATUS_PASS

    return MeasurementOutput(
        symbol=inp.symbol,
        event_time=inp.event_time,
        one_lot_notional_yen=one_lot,
        one_tick_risk_yen_100=one_tick,
        current_spread_cost_yen_100=spread_cost,
        estimated_execution_risk_yen=est,
        board_age_sec=board_age,
        bid_depth_pass=bid_depth_pass,
        ask_depth_pass=ask_depth_pass,
        board_freshness_pass=board_fresh,
        measurement_status=status,
        reason_codes=reasons,
        execution_risk=est,
        strategy_loss_risk="unresolved",
        total_trade_risk="unresolved",
    )


def required_capital_by_notional(one_lot_notional: float, frac: float = 0.15) -> float:
    return one_lot_notional / frac


def required_capital_by_execution_risk(est_risk: float, frac: float = 0.0025) -> float:
    return est_risk / frac
