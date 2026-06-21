"""
Phase230: Entry expectancy score shadow (logging only; no hard reject).

Phase237/250: v1 SCORE_POINTS RollingMAE:mid=0 (Phase236 B). v2 shadow列は mid キー除外で同等。

Fixed Phase229 tertile cutoffs and score weights — not tuned per session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from small_paper.board_imbalance_shadow import pnl_map_from_events

SHADOW_FIELD_KEYS = (
    "entry_expectancy_score",
    "entry_expectancy_score_ge5_flag",
    "entry_expectancy_score_ge6_flag",
)

SHADOW_FIELD_KEYS_V2 = (
    "entry_expectancy_score_v2",
    "entry_expectancy_score_v2_ge5_flag",
    "entry_expectancy_score_v2_ge6_flag",
)

ALL_SHADOW_FIELD_KEYS = SHADOW_FIELD_KEYS + SHADOW_FIELD_KEYS_V2

SUMMARY_FIELD_KEYS = (
    "entry_expectancy_score_shadow_enabled",
    "score5_count",
    "score5_pf",
    "score5_pnl",
    "score6_count",
    "score6_pf",
    "score6_pnl",
)

SUMMARY_FIELD_KEYS_V2 = (
    "phase237_entry_expectancy_score_v2_shadow",
    "score5_v2_count",
    "score5_v2_pf",
    "score5_v2_pnl",
    "score6_v2_count",
    "score6_v2_pf",
    "score6_v2_pnl",
)

# Phase229 tertile cutoffs (2503-trade population, Phase228 discovery).
TERTILE_CUTOFFS: dict[str, dict[str, float]] = {
    "Board": {"p33": 0.437286, "p66": 0.527869},
    "TV": {"p33": 12851022500.0, "p66": 57198185000.0},
    "Momentum": {"p33": 0.2546, "p66": 0.2988},
    "Duration": {"p33": 31.0, "p66": 406.0},
    "RollingMAE": {"p33": -0.000666, "p66": 0.0},
    "Price": {"p33": 2690.0, "p66": 4645.0},
}

# Phase229 work3 score_map (+2 top 20% freq, +1 top 50% freq among target tokens).
SCORE_POINTS: dict[str, int] = {
    "HBRecent:no": 2,
    "RollingMAE:mid": 0,
    "Duration:high": 2,
    "Momentum:low": 1,
    "Board:mid": 1,
    "Price:high": 1,
    "TV:mid": 1,
}

# Phase314: Momentum + Board only (HBRecent/TV/Duration/Price removed from v2).
# Phase452: Board:high scores same as Board:mid (Momentum:low + Board mid|high => 3).
SCORE_POINTS_V2: dict[str, int] = {
    "Momentum:low": 2,
    "Board:mid": 1,
    "Board:high": 1,
}

REQUIRED_V2_TOKENS: frozenset[str] = frozenset({"Momentum:low"})

ENTRY_SCORE_V2_GATE_MIN = 3

SCORE_GE5_THRESHOLD = 5
SCORE_GE6_THRESHOLD = 6


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _boolish(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def _bin_tertile(val: float, p33: float, p66: float) -> str:
    if val <= p33:
        return "low"
    if val <= p66:
        return "mid"
    return "high"


def _feature_token(label: str, trade: Mapping[str, Any]) -> Optional[str]:
    if label == "HBRecent":
        hb = trade.get("entry_high_break_recent")
        if hb is None:
            return None
        return f"HBRecent:{'yes' if _boolish(hb) else 'no'}"
    field_map = {
        "Board": "entry_order_book_imbalance",
        "TV": "trading_value",
        "Momentum": "momentum_continuation_score",
        "Duration": "max_continuation_duration",
        "RollingMAE": "rolling_mae_pct",
        "Price": "current_price",
    }
    fld = field_map.get(label)
    if not fld:
        return None
    v = _float(trade.get(fld))
    if v is None:
        return None
    cuts = TERTILE_CUTOFFS.get(label)
    if not cuts:
        return None
    level = _bin_tertile(v, cuts["p33"], cuts["p66"])
    return f"{label}:{level}"


def active_score_tokens_v2(trade: Mapping[str, Any]) -> list[str]:
    active: list[str] = []
    for token in SCORE_POINTS_V2:
        lbl = token.split(":", 1)[0]
        if _feature_token(lbl, trade) == token:
            active.append(token)
    return active


def momentum_low_required_for_v2(trade: Mapping[str, Any]) -> bool:
    return "Momentum:low" in active_score_tokens_v2(trade)


MOMENTUM_SCORE_CUTOFF_P33 = TERTILE_CUTOFFS["Momentum"]["p33"]


def momentum_score_cutoff_pass(
    trade: Mapping[str, Any],
    *,
    cutoff: Optional[float] = None,
) -> bool:
    """PBv2 explicit low-momentum filter (Phase471: equivalent to Momentum:low token)."""
    v = _float(trade.get("momentum_continuation_score"))
    if v is None:
        return False
    return v <= float(cutoff if cutoff is not None else MOMENTUM_SCORE_CUTOFF_P33)


def board_mid_or_high_required_for_v2(trade: Mapping[str, Any]) -> bool:
    board = _feature_token("Board", trade)
    return board in ("Board:mid", "Board:high")


def _score_fields_from_points(
    trade: Mapping[str, Any],
    score_points: Mapping[str, int],
    *,
    score_key: str,
    ge5_key: str,
    ge6_key: str,
) -> dict[str, Any]:
    score = 0
    for token, pts in score_points.items():
        lbl = token.split(":", 1)[0]
        tok = _feature_token(lbl, trade)
        if tok == token:
            score += pts
    return {
        score_key: score,
        ge5_key: score >= SCORE_GE5_THRESHOLD,
        ge6_key: score >= SCORE_GE6_THRESHOLD,
    }


def compute_entry_expectancy_score_fields(*, trade: Mapping[str, Any]) -> dict[str, Any]:
    """Compute Phase229 score and Phase237 v2 (Scenario B) at accept — shadow only."""
    out = _score_fields_from_points(
        trade,
        SCORE_POINTS,
        score_key="entry_expectancy_score",
        ge5_key="entry_expectancy_score_ge5_flag",
        ge6_key="entry_expectancy_score_ge6_flag",
    )
    out.update(
        _score_fields_from_points(
            trade,
            SCORE_POINTS_V2,
            score_key="entry_expectancy_score_v2",
            ge5_key="entry_expectancy_score_v2_ge5_flag",
            ge6_key="entry_expectancy_score_v2_ge6_flag",
        )
    )
    return out


def enrich_exit_entry_expectancy_fields(
    entry_shadow: Mapping[str, Any],
    *,
    pnl_pct: float,
    exit_reason: str,
) -> dict[str, Any]:
    return {
        "entry_expectancy_score": entry_shadow.get("entry_expectancy_score"),
        "entry_expectancy_score_ge5_flag": bool(entry_shadow.get("entry_expectancy_score_ge5_flag")),
        "entry_expectancy_score_ge6_flag": bool(entry_shadow.get("entry_expectancy_score_ge6_flag")),
        "entry_expectancy_score_v2": entry_shadow.get("entry_expectancy_score_v2"),
        "entry_expectancy_score_v2_ge5_flag": bool(
            entry_shadow.get("entry_expectancy_score_v2_ge5_flag")
        ),
        "entry_expectancy_score_v2_ge6_flag": bool(
            entry_shadow.get("entry_expectancy_score_v2_ge6_flag")
        ),
        "pnl_pct": round(float(pnl_pct), 4),
        "exit_reason": exit_reason,
        "stop_hit": exit_reason == "stop_hit",
        "trailing_mfe_exit": exit_reason == "trailing_mfe_exit",
    }


def _pf(pnls: Sequence[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _cohort_metrics(rows: Sequence[Mapping[str, Any]], flag_key: str) -> dict[str, Any]:
    pnls: list[float] = []
    for row in rows:
        if not row.get(flag_key):
            continue
        pnl = _float(row.get("pnl_pct"))
        if pnl is None:
            continue
        pnls.append(float(pnl))
    pf = _pf(pnls)
    return {
        "count": len(pnls),
        "pf": pf if pf != float("inf") else pf,
        "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
    }


@dataclass
class _CohortAccumulator:
    count: int = 0
    win_pnl: float = 0.0
    loss_pnl: float = 0.0
    stop_hit_count: int = 0

    def record_accept(self, active: bool) -> None:
        if active:
            self.count += 1

    def record_exit(self, active: bool, *, pnl: float, stop_hit: bool) -> None:
        if not active:
            return
        if pnl > 0:
            self.win_pnl = round(self.win_pnl + pnl, 4)
        elif pnl < 0:
            self.loss_pnl = round(self.loss_pnl + pnl, 4)
        if stop_hit:
            self.stop_hit_count += 1

    def summary(self, *, count_key: str, pf_key: str, pnl_key: str, stop_key: str) -> dict[str, Any]:
        gl = abs(self.loss_pnl)
        pf: Optional[float]
        if gl <= 0:
            pf = None if self.win_pnl <= 0 else float("inf")
        else:
            pf = round(self.win_pnl / gl, 4)
        return {
            count_key: self.count,
            pf_key: pf if pf != float("inf") else pf,
            pnl_key: round(self.win_pnl + self.loss_pnl, 4),
            stop_key: self.stop_hit_count,
        }


@dataclass
class EntryExpectancyScoreCounters:
    score5: _CohortAccumulator = field(default_factory=_CohortAccumulator)
    score6: _CohortAccumulator = field(default_factory=_CohortAccumulator)
    score5_v2: _CohortAccumulator = field(default_factory=_CohortAccumulator)
    score6_v2: _CohortAccumulator = field(default_factory=_CohortAccumulator)

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        self.score5.record_accept(bool(fields.get("entry_expectancy_score_ge5_flag")))
        self.score6.record_accept(bool(fields.get("entry_expectancy_score_ge6_flag")))
        self.score5_v2.record_accept(bool(fields.get("entry_expectancy_score_v2_ge5_flag")))
        self.score6_v2.record_accept(bool(fields.get("entry_expectancy_score_v2_ge6_flag")))

    def record_exit(self, row: Mapping[str, Any]) -> None:
        pnl = _float(row.get("pnl_pct")) or 0.0
        reason = str(row.get("exit_reason") or "")
        stop = bool(row.get("stop_hit")) or reason == "stop_hit"
        self.score5.record_exit(
            bool(row.get("entry_expectancy_score_ge5_flag")),
            pnl=pnl,
            stop_hit=stop,
        )
        self.score6.record_exit(
            bool(row.get("entry_expectancy_score_ge6_flag")),
            pnl=pnl,
            stop_hit=stop,
        )
        self.score5_v2.record_exit(
            bool(row.get("entry_expectancy_score_v2_ge5_flag")),
            pnl=pnl,
            stop_hit=stop,
        )
        self.score6_v2.record_exit(
            bool(row.get("entry_expectancy_score_v2_ge6_flag")),
            pnl=pnl,
            stop_hit=stop,
        )

    def summary_fields(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "entry_expectancy_score_shadow_enabled": True,
            "phase230_entry_expectancy_shadow": True,
            "phase237_entry_expectancy_score_v2_shadow": True,
            "phase240_parallel_ge5_observation": True,
        }
        out.update(
            self.score5.summary(
                count_key="score5_count",
                pf_key="score5_pf",
                pnl_key="score5_pnl",
                stop_key="score5_stop_hit_count",
            )
        )
        out.update(
            self.score6.summary(
                count_key="score6_count",
                pf_key="score6_pf",
                pnl_key="score6_pnl",
                stop_key="score6_stop_hit_count",
            )
        )
        out.update(
            self.score5_v2.summary(
                count_key="score5_v2_count",
                pf_key="score5_v2_pf",
                pnl_key="score5_v2_pnl",
                stop_key="score5_v2_stop_hit_count",
            )
        )
        out.update(
            self.score6_v2.summary(
                count_key="score6_v2_count",
                pf_key="score6_v2_pf",
                pnl_key="score6_v2_pnl",
                stop_key="score6_v2_stop_hit_count",
            )
        )
        return out


def finalize_session_entry_expectancy_score(
    accepted_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reconcile exit PnL and return score5/score6 session metrics."""
    exit_by_key = pnl_map_from_events(events)
    for row in accepted_rows:
        key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        ex = exit_by_key.get(key)
        if ex:
            if ex.get("pnl_pct") is not None:
                row["pnl_pct"] = ex["pnl_pct"]
            row["exit_reason"] = ex.get("exit_reason", "")
            row["stop_hit"] = ex.get("stop_hit", False)

    closed = [r for r in accepted_rows if _float(r.get("pnl_pct")) is not None]
    s5 = _cohort_metrics(closed, "entry_expectancy_score_ge5_flag")
    s6 = _cohort_metrics(closed, "entry_expectancy_score_ge6_flag")
    s5_v2 = _cohort_metrics(closed, "entry_expectancy_score_v2_ge5_flag")
    s6_v2 = _cohort_metrics(closed, "entry_expectancy_score_v2_ge6_flag")
    return {
        "entry_expectancy_score_shadow_enabled": True,
        "phase230_entry_expectancy_shadow": True,
        "phase237_entry_expectancy_score_v2_shadow": True,
        "phase240_parallel_ge5_observation": True,
        "score5_count": s5["count"],
        "score5_pf": s5["pf"],
        "score5_pnl": s5["total_pnl"],
        "score6_count": s6["count"],
        "score6_pf": s6["pf"],
        "score6_pnl": s6["total_pnl"],
        "score5_v2_count": s5_v2["count"],
        "score5_v2_pf": s5_v2["pf"],
        "score5_v2_pnl": s5_v2["total_pnl"],
        "score6_v2_count": s6_v2["count"],
        "score6_v2_pf": s6_v2["pf"],
        "score6_v2_pnl": s6_v2["total_pnl"],
    }
