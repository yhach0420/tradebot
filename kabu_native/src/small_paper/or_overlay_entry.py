"""
Phase538: OR Open Strength Overlay — production runtime entry path.

Adds O_R003_OR overlay (day_high + updates<=8) with OS9 open-strength filter
alongside PBv2 mainline ENTRY. Split CAP: PBv2=4, OR=1 (rollback: or_overlay_enabled=false).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.exposure_gate import (
    REJECT_DAILY_LOSS,
    REJECT_MAX_CONCURRENT,
    REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW,
    REJECT_RISK_CLUSTER,
    REJECT_WRONG_PROFILE,
    GateDecision,
)
from research.phase534_or_open_strength_theory import _filter_allows
from small_paper.entry_quality_guard import compute_update_count_before_entry
from small_paper.or_overlay_cap import (
    ENTRY_TYPE_OR,
    ENTRY_TYPE_PBV2,
    REJECT_OR_CAP_FULL,
    cap_reject_reason_for_pool,
    entry_type_from_trade,
    observer_cap_kwargs_for_pool,
    split_pool_open_counts,
)

JST = ZoneInfo("Asia/Tokyo")

OR_REASON_OPEN_STRENGTH = "open_strength"
OR_REASON_DAY_LEADER = "day_leader"

REJECT_OR_OVERLAY_NOT_CANDIDATE = "or_overlay_not_candidate"
REJECT_OR_OVERLAY_BLOCKED = "or_overlay_blocked"

DEFAULT_CAP_PBV2 = 4
DEFAULT_CAP_OR = 1
DEFAULT_MAX_UPDATE_COUNT_OR = 8
DEFAULT_OPEN_STRENGTH_RANK_MAX = 10
DEFAULT_OPEN_STRENGTH_MINS_MAX = 90.0
DAY_HIGH_NEAR_PCT = 0.25

PHASE538_RUNTIME_VERDICT = "phase538_or_overlay_runtime_adopted"


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _session_open_ts(entry_ts: float) -> float:
    dt = datetime.fromtimestamp(entry_ts, tz=JST)
    open_dt = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    return open_dt.timestamp()


def _minutes_from_open(entry_ts: float) -> float:
    return max(0.0, (entry_ts - _session_open_ts(entry_ts)) / 60.0)


def _vwap_distance_from_trade(trade: Mapping[str, Any]) -> Optional[float]:
    for key in ("vwap_distance", "entry_vwap_dev_pct", "price_vs_vwap"):
        val = _float(trade.get(key))
        if val is not None:
            return val
    return None


def _day_return_pct(*, current: float, prev_close: float) -> Optional[float]:
    if prev_close <= 0 or current <= 0:
        return None
    return round((current - prev_close) / prev_close * 100.0, 6)


def compute_day_return_rank(
    symbol: str,
    *,
    day_returns: Mapping[str, float],
    universe_symbols: Optional[Sequence[str]] = None,
) -> Optional[int]:
    sym = str(symbol or "").strip()
    if not sym or sym not in day_returns:
        return None
    universe = list(universe_symbols or day_returns.keys())
    ranked = sorted(
        (s for s in universe if s in day_returns),
        key=lambda s: float(day_returns[s]),
        reverse=True,
    )
    if sym not in ranked:
        return None
    return ranked.index(sym) + 1


def passes_o_r003_day_high(
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    price_ring: Sequence[tuple[float, float]],
    entry_ts: float,
    max_update_count: int,
) -> bool:
    near = _float(trade.get("entry_near_day_high_pct")) or _float(trade.get("day_high_distance_pct"))
    if near is not None:
        at_high = abs(near) <= DAY_HIGH_NEAR_PCT
    else:
        current = _float(payload.get("CurrentPrice"))
        board_high = _float(payload.get("HighPrice"))
        at_high = (
            current is not None
            and board_high is not None
            and board_high > 0
            and current >= board_high * (1.0 - DAY_HIGH_NEAR_PCT / 100.0)
        )
    if not at_high:
        return False
    updates = trade.get("update_count_before_entry")
    if updates is None and price_ring:
        updates = compute_update_count_before_entry(price_ring, entry_ts=entry_ts)
    if updates is None:
        return False
    return int(updates) <= int(max_update_count)


def resolve_or_reason(row: Mapping[str, Any]) -> Optional[str]:
    if _filter_allows("OS9_open_strength_proxy", row, speed_p75=0.0):
        return OR_REASON_OPEN_STRENGTH
    mins = _float(row.get("minutes_from_open"))
    rank = row.get("day_return_rank")
    if (
        mins is not None
        and mins <= DEFAULT_OPEN_STRENGTH_MINS_MAX
        and rank is not None
        and int(rank) <= DEFAULT_OPEN_STRENGTH_RANK_MAX
    ):
        return OR_REASON_DAY_LEADER
    return None


@dataclass
class OrOverlayConfig:
    enabled: bool = False
    cap_pbv2: int = DEFAULT_CAP_PBV2
    cap_or: int = DEFAULT_CAP_OR
    max_update_count: int = DEFAULT_MAX_UPDATE_COUNT_OR
    open_strength_rank_max: int = DEFAULT_OPEN_STRENGTH_RANK_MAX
    open_strength_mins_max: float = DEFAULT_OPEN_STRENGTH_MINS_MAX


@dataclass
class OrOverlaySessionState:
    config: OrOverlayConfig
    or_entry_count: int = 0
    or_exit_count: int = 0
    or_blocked_count: int = 0
    or_cap_full_count: int = 0
    pbv2_count: int = 0
    or_count: int = 0
    day_return_by_symbol: dict[str, float] = field(default_factory=dict)
    prev_close_by_symbol: dict[str, float] = field(default_factory=dict)

    def summary_fields(
        self,
        *,
        events: Sequence[Mapping[str, Any]],
        observer: Any = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            return {"or_overlay_enabled": False}

        or_exits = [
            e
            for e in events
            if str(e.get("event_type")) == "observer_exit"
            and entry_type_from_trade(e) == ENTRY_TYPE_OR
        ]
        or_pnls = [_float(e.get("pnl_pct")) for e in or_exits]
        or_pnls_f = [p for p in or_pnls if p is not None]
        wins = sum(1 for p in or_pnls_f if p > 0)
        win_rate = round(wins / len(or_pnls_f), 4) if or_pnls_f else 0.0
        gross_profit = sum(p for p in or_pnls_f if p > 0)
        gross_loss = abs(sum(p for p in or_pnls_f if p < 0))
        pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
        realized = round(sum(or_pnls_f), 4) if or_pnls_f else 0.0

        unrealized_vals: list[float] = []
        if observer is not None:
            open_positions = getattr(observer, "open_positions", None)
            positions = open_positions() if callable(open_positions) else []
            for pos in positions:
                if entry_type_from_trade(pos) != ENTRY_TYPE_OR:
                    continue
                upnl = _float(pos.get("unrealized_pnl_pct"))
                if upnl is not None:
                    unrealized_vals.append(upnl)
        unrealized = round(sum(unrealized_vals), 4) if unrealized_vals else 0.0

        _, or_open, _ = split_pool_open_counts(observer)
        cap_or = int(self.config.cap_or)
        or_pool_util = round(or_open / cap_or, 4) if cap_or > 0 else 0.0

        return {
            "or_overlay_enabled": True,
            "cap_pbv2": int(self.config.cap_pbv2),
            "cap_or": cap_or,
            "or_entry_count": self.or_entry_count,
            "or_exit_count": len(or_exits),
            "or_active_positions": or_open,
            "or_realized_pnl": realized,
            "or_unrealized_pnl": unrealized,
            "or_win_rate": win_rate,
            "or_pf": pf,
            "or_blocked_count": self.or_blocked_count,
            "or_cap_full_count": self.or_cap_full_count,
            "pbv2_count": self.pbv2_count,
            "or_count": self.or_count,
            "or_pool_utilization": or_pool_util,
        }

    def record_day_tick(
        self,
        symbol: str,
        *,
        current_price: float,
        prev_close: Optional[float] = None,
    ) -> None:
        sym = str(symbol or "").strip()
        if not sym or current_price <= 0:
            return
        if prev_close is not None and prev_close > 0:
            self.prev_close_by_symbol[sym] = float(prev_close)
        pc = self.prev_close_by_symbol.get(sym)
        if pc is None or pc <= 0:
            return
        ret = _day_return_pct(current=current_price, prev_close=pc)
        if ret is not None:
            self.day_return_by_symbol[sym] = ret

    def record_entry(self, trade: Mapping[str, Any]) -> None:
        et = entry_type_from_trade(trade)
        if et == ENTRY_TYPE_OR:
            self.or_entry_count += 1
            self.or_count += 1
        else:
            self.pbv2_count += 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        if entry_type_from_trade(row) == ENTRY_TYPE_OR:
            self.or_exit_count += 1

    def record_or_blocked(self, reason: str) -> None:
        self.or_blocked_count += 1
        if reason == REJECT_OR_CAP_FULL:
            self.or_cap_full_count += 1


def config_from_pilot(pilot_config: Any) -> OrOverlayConfig:
    return OrOverlayConfig(
        enabled=bool(getattr(pilot_config, "or_overlay_enabled", False)),
        cap_pbv2=int(getattr(pilot_config, "cap_pbv2", DEFAULT_CAP_PBV2) or DEFAULT_CAP_PBV2),
        cap_or=int(getattr(pilot_config, "cap_or", DEFAULT_CAP_OR) or DEFAULT_CAP_OR),
        max_update_count=int(
            getattr(pilot_config, "or_max_update_count", DEFAULT_MAX_UPDATE_COUNT_OR)
            or DEFAULT_MAX_UPDATE_COUNT_OR
        ),
        open_strength_rank_max=int(
            getattr(pilot_config, "or_open_strength_rank_max", DEFAULT_OPEN_STRENGTH_RANK_MAX)
            or DEFAULT_OPEN_STRENGTH_RANK_MAX
        ),
        open_strength_mins_max=float(
            getattr(pilot_config, "or_open_strength_mins_max", DEFAULT_OPEN_STRENGTH_MINS_MAX)
            or DEFAULT_OPEN_STRENGTH_MINS_MAX
        ),
    )


def build_or_overlay_state(pilot_config: Any) -> Optional[OrOverlaySessionState]:
    cfg = config_from_pilot(pilot_config)
    if not cfg.enabled:
        return None
    return OrOverlaySessionState(config=cfg)


def compute_or_overlay_fields(
    *,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    price_ring: Sequence[tuple[float, float]],
    entry_ts: float,
    day_returns: Mapping[str, float],
    universe_symbols: Optional[Sequence[str]] = None,
    max_update_count: int = DEFAULT_MAX_UPDATE_COUNT_OR,
) -> dict[str, Any]:
    sym = str(trade.get("symbol") or "")
    mins = _minutes_from_open(entry_ts)
    vwap = _vwap_distance_from_trade(trade)
    updates = trade.get("update_count_before_entry")
    if updates is None and price_ring:
        updates = compute_update_count_before_entry(price_ring, entry_ts=entry_ts)
    rank = compute_day_return_rank(sym, day_returns=day_returns, universe_symbols=universe_symbols)
    row = {
        "minutes_from_open": round(mins, 4),
        "vwap_distance": vwap,
        "day_return_rank": rank,
        "update_count_before_entry": updates,
        "day_return": day_returns.get(sym),
    }
    o_r003 = passes_o_r003_day_high(
        trade,
        payload,
        price_ring=price_ring,
        entry_ts=entry_ts,
        max_update_count=max_update_count,
    )
    or_reason = resolve_or_reason(row) if o_r003 else None
    return {
        **row,
        "or_o_r003_pass": o_r003,
        "or_reason": or_reason,
        "or_open_strength_candidate": or_reason == OR_REASON_OPEN_STRENGTH,
    }


def _check_or_session_gates(gate: Any, trade: Mapping[str, Any]) -> Optional[str]:
    profile = str(trade.get("profile", ""))
    if profile != gate.config.profile:
        return REJECT_WRONG_PROFILE

    allowed = getattr(gate, "_allowed_windows", None)
    if allowed is not None:
        from small_paper.allowed_trading_windows import is_in_allowed_trading_window

        if not is_in_allowed_trading_window(str(trade.get("entry_time") or ""), allowed):
            return REJECT_OUTSIDE_ALLOWED_TRADING_WINDOW

    if getattr(gate.config, "risk_cluster_block_enabled", True) and gate.state.risk_cluster_blocked:
        return REJECT_RISK_CLUSTER

    day = str(trade.get("trade_date", ""))[:10]
    if (
        getattr(gate.config, "daily_loss_guard_enabled", True)
        and day
        and gate.state.day_pnl.get(day, 0.0) <= gate.config.daily_loss_guard_pct
    ):
        return REJECT_DAILY_LOSS
    return None


def evaluate_or_overlay_entry(
    *,
    gate: Any,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    price_ring: Sequence[tuple[float, float]],
    entry_ts: float,
    observer: Any,
    or_state: OrOverlaySessionState,
    universe_symbols: Optional[Sequence[str]] = None,
) -> GateDecision:
    q = float(trade.get("continuation_quality_score") or 0.0)
    cfg = or_state.config

    session_reject = _check_or_session_gates(gate, trade)
    if session_reject:
        or_state.record_or_blocked(session_reject)
        return GateDecision(
            accept=False,
            reason=session_reject,
            continuation_quality_score=q,
            quality_tier="",
        )

    fields = compute_or_overlay_fields(
        trade=trade,
        payload=payload,
        price_ring=price_ring,
        entry_ts=entry_ts,
        day_returns=or_state.day_return_by_symbol,
        universe_symbols=universe_symbols,
        max_update_count=cfg.max_update_count,
    )
    trade.update(fields)

    if not fields.get("or_o_r003_pass") or not fields.get("or_reason"):
        or_state.record_or_blocked(REJECT_OR_OVERLAY_NOT_CANDIDATE)
        return GateDecision(
            accept=False,
            reason=REJECT_OR_OVERLAY_NOT_CANDIDATE,
            continuation_quality_score=q,
            quality_tier="",
        )

    sym = str(trade.get("symbol") or "")
    cap_kw = observer_cap_kwargs_for_pool(
        observer,
        sym,
        entry_pool=ENTRY_TYPE_OR,
        cap_pbv2=cfg.cap_pbv2,
        cap_or=cfg.cap_or,
    )
    if (
        not cap_kw["observer_symbol_open"]
        and cap_kw["observer_open_count"] >= cap_kw["max_concurrent_positions"]
    ):
        or_state.record_or_blocked(REJECT_OR_CAP_FULL)
        return GateDecision(
            accept=False,
            reason=REJECT_OR_CAP_FULL,
            continuation_quality_score=q,
            quality_tier="",
        )

    trade["entry_type"] = ENTRY_TYPE_OR
    return GateDecision(
        accept=True,
        reason="",
        continuation_quality_score=q,
        quality_tier="",
    )


def pbv2_cap_kwargs(
    config: Any,
    observer: Any,
    symbol: str,
) -> dict[str, Any]:
    if getattr(config, "or_overlay_enabled", False):
        return observer_cap_kwargs_for_pool(
            observer,
            symbol,
            entry_pool=ENTRY_TYPE_PBV2,
            cap_pbv2=int(getattr(config, "cap_pbv2", DEFAULT_CAP_PBV2) or DEFAULT_CAP_PBV2),
            cap_or=int(getattr(config, "cap_or", DEFAULT_CAP_OR) or DEFAULT_CAP_OR),
        )
    from small_paper.position_cap_mode import observer_cap_kwargs

    return observer_cap_kwargs(observer, symbol)


def record_split_cap_reject(
    stats: Any,
    *,
    decision_accept: bool,
    decision_reason: str,
    entry_pool: str,
) -> None:
    if stats is None or decision_accept:
        return
    if decision_reason != REJECT_MAX_CONCURRENT:
        return
    pool = str(entry_pool or ENTRY_TYPE_PBV2).strip().upper()
    if pool == ENTRY_TYPE_OR and decision_reason == REJECT_OR_CAP_FULL:
        return
    stats.record_cap_reject()
