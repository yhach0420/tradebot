"""
Phase230: Entry expectancy score shadow (logging only; no hard reject).

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

SUMMARY_FIELD_KEYS = (
    "entry_expectancy_score_shadow_enabled",
    "score5_count",
    "score5_pf",
    "score5_pnl",
    "score6_count",
    "score6_pf",
    "score6_pnl",
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
    "RollingMAE:mid": 2,
    "Duration:high": 2,
    "Momentum:low": 1,
    "Board:mid": 1,
    "Price:high": 1,
    "TV:mid": 1,
}

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


def compute_entry_expectancy_score_fields(*, trade: Mapping[str, Any]) -> dict[str, Any]:
    """Compute Phase229 entry expectancy score at accept (shadow only)."""
    score = 0
    for token, pts in SCORE_POINTS.items():
        lbl = token.split(":", 1)[0]
        tok = _feature_token(lbl, trade)
        if tok == token:
            score += pts
    return {
        "entry_expectancy_score": score,
        "entry_expectancy_score_ge5_flag": score >= SCORE_GE5_THRESHOLD,
        "entry_expectancy_score_ge6_flag": score >= SCORE_GE6_THRESHOLD,
    }


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
class EntryExpectancyScoreCounters:
    score5_count: int = 0
    score6_count: int = 0
    _score5_win_pnl: float = 0.0
    _score5_loss_pnl: float = 0.0
    _score6_win_pnl: float = 0.0
    _score6_loss_pnl: float = 0.0
    score5_stop_hit_count: int = 0
    score6_stop_hit_count: int = 0

    def record_accept(self, fields: Mapping[str, Any]) -> None:
        if fields.get("entry_expectancy_score_ge5_flag"):
            self.score5_count += 1
        if fields.get("entry_expectancy_score_ge6_flag"):
            self.score6_count += 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        pnl = _float(row.get("pnl_pct")) or 0.0
        reason = str(row.get("exit_reason") or "")
        if row.get("entry_expectancy_score_ge5_flag"):
            if pnl > 0:
                self._score5_win_pnl = round(self._score5_win_pnl + pnl, 4)
            elif pnl < 0:
                self._score5_loss_pnl = round(self._score5_loss_pnl + pnl, 4)
            if bool(row.get("stop_hit")) or reason == "stop_hit":
                self.score5_stop_hit_count += 1
        if row.get("entry_expectancy_score_ge6_flag"):
            if pnl > 0:
                self._score6_win_pnl = round(self._score6_win_pnl + pnl, 4)
            elif pnl < 0:
                self._score6_loss_pnl = round(self._score6_loss_pnl + pnl, 4)
            if bool(row.get("stop_hit")) or reason == "stop_hit":
                self.score6_stop_hit_count += 1

    def _pf_from_wl(self, win: float, loss: float) -> Optional[float]:
        gl = abs(loss)
        if gl <= 0:
            return None if win <= 0 else float("inf")
        return round(win / gl, 4)

    def summary_fields(self) -> dict[str, Any]:
        s5_pf = self._pf_from_wl(self._score5_win_pnl, self._score5_loss_pnl)
        s6_pf = self._pf_from_wl(self._score6_win_pnl, self._score6_loss_pnl)
        return {
            "entry_expectancy_score_shadow_enabled": True,
            "phase230_entry_expectancy_shadow": True,
            "score5_count": self.score5_count,
            "score5_pf": s5_pf if s5_pf != float("inf") else s5_pf,
            "score5_pnl": round(self._score5_win_pnl + self._score5_loss_pnl, 4),
            "score6_count": self.score6_count,
            "score6_pf": s6_pf if s6_pf != float("inf") else s6_pf,
            "score6_pnl": round(self._score6_win_pnl + self._score6_loss_pnl, 4),
            "score5_stop_hit_count": self.score5_stop_hit_count,
            "score6_stop_hit_count": self.score6_stop_hit_count,
        }


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
    return {
        "entry_expectancy_score_shadow_enabled": True,
        "phase230_entry_expectancy_shadow": True,
        "score5_count": s5["count"],
        "score5_pf": s5["pf"],
        "score5_pnl": s5["total_pnl"],
        "score6_count": s6["count"],
        "score6_pf": s6["pf"],
        "score6_pnl": s6["total_pnl"],
    }
