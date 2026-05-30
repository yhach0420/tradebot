"""
Phase187: VWAP adverse cluster analysis (review only).

Hypothesis: immediate post-entry adverse move (r30/r60 < 0) on high-VWAP entries
is the harmful cluster — not VWAP deviation alone.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from research.phase181_entry_expectancy_review import (
    _float,
    _load_events,
    _mean,
    _pair_trades,
    _parse_ts,
    _pf,
    _price_at_offset,
    _return_pct,
)
from research.phase185_vwap_dev_shadow_candidate_multisession_review import (
    FOCUS_SYMBOLS,
    OBSERVER_EXIT_SESSIONS,
    REFERENCE_SESSIONS,
    VWAP_DEV_THRESHOLD_B,
    VwapReviewTrade,
    _bounded_ticks_for_trades,
    _day_stamp_from_session,
    _price_series_from_push,
    _session_id,
    discover_sessions,
    load_session_trades,
)

VWAP_BANDS = (
    ("2.5_3.0", 2.5, 3.0),
    ("3.0_4.0", 3.0, 4.0),
    ("4.0_plus", 4.0, 999.0),
)


@dataclass
class CandidateTrade:
    session_id: str
    day_stamp: str
    symbol: str
    entry_time: str
    entry_ts: float
    close_ts: float
    pnl_pct: float
    exit_reason: str
    entry_vwap_dev_pct: float
    r30_sec: Optional[float]
    r60_sec: Optional[float]
    mfe_pct: Optional[float]

    @property
    def is_candidate(self) -> bool:
        return self.entry_vwap_dev_pct >= VWAP_DEV_THRESHOLD_B

    @property
    def is_false_positive(self) -> bool:
        return self.is_candidate and self.pnl_pct > 0

    def vwap_band(self) -> Optional[str]:
        v = self.entry_vwap_dev_pct
        for name, lo, hi in VWAP_BANDS:
            if lo <= v < hi:
                return name
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "day_stamp": self.day_stamp,
            "symbol": self.symbol,
            "entry_time": self.entry_time,
            "pnl_pct": round(self.pnl_pct, 4),
            "exit_reason": self.exit_reason,
            "entry_vwap_dev_pct": round(self.entry_vwap_dev_pct, 4),
            "r30_sec": self.r30_sec,
            "r60_sec": self.r60_sec,
            "mfe_pct": self.mfe_pct,
            "vwap_band": self.vwap_band(),
        }


def _close_ts_map_structural(session_dir: Path) -> dict[tuple[str, str], float]:
    path = session_dir / "structural_trades.csv"
    out: dict[tuple[str, str], float] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "").strip()
            ent = str(row.get("entry_time") or "").strip()
            if not sym or not ent:
                continue
            ent_ts = _parse_ts(ent)
            close_ts = _parse_ts(str(row.get("close_time") or "")) or ent_ts + 300
            out[(sym, ent)] = close_ts
    return out


def _close_ts_map_observer(session_dir: Path) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for acc, ex in _pair_trades(_load_events(session_dir)):
        sym = str(acc.get("symbol") or "")
        ent = str(acc.get("entry_time") or "")
        ent_ts = _parse_ts(ent)
        ex_ts = _parse_ts(str(ex.get("exit_time") or "")) or ent_ts + 300
        out[(sym, ent)] = ex_ts
    return out


def _compute_r60(
    *,
    entry_ts: float,
    entry_px: float,
    close_ts: float,
    push_ticks: Sequence[tuple[float, dict[str, Any]]],
) -> Optional[float]:
    if entry_px <= 0:
        return None
    series = _price_series_from_push(push_ticks)
    r60 = _return_pct(entry_px, _price_at_offset(series, entry_ts, entry_px, 60, end_ts=close_ts))
    return round(r60, 4) if r60 is not None else None


def _entry_px_map(session_dir: Path) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    path = session_dir / "structural_trades.csv"
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sym = str(row.get("symbol") or "").strip()
                ent = str(row.get("entry_time") or "").strip()
                if sym and ent:
                    out[(sym, ent)] = _float(row.get("entry_price")) or 0.0
        return out
    for acc, ex in _pair_trades(_load_events(session_dir)):
        sym = str(acc.get("symbol") or "")
        ent = str(acc.get("entry_time") or "")
        out[(sym, ent)] = _float(ex.get("entry_price")) or _float(acc.get("current_price")) or 0.0
    return out


def load_enriched_trades(
    repo_root: Path,
    base: Path,
    *,
    all_trades_by_session: dict[str, list[VwapReviewTrade]] | None = None,
) -> list[CandidateTrade]:
    session_dirs, _ = discover_sessions(base)
    out: list[CandidateTrade] = []

    for sdir in session_dirs:
        day_stamp = _day_stamp_from_session(sdir)
        sid = _session_id(sdir, base)
        y = f"{day_stamp[:4]}-{day_stamp[4:6]}-{day_stamp[6:8]}"
        push_dir = repo_root / "kabu_native" / "data" / "push_jsonl" / y

        close_map = (
            _close_ts_map_structural(sdir)
            if (sdir / "structural_trades.csv").is_file()
            else _close_ts_map_observer(sdir)
        )
        entry_px_map = _entry_px_map(sdir)

        if all_trades_by_session is not None and sid in all_trades_by_session:
            trades = all_trades_by_session[sid]
        else:
            trades = load_session_trades(sdir, repo_root=repo_root, base=base)

        candidates_raw = [t for t in trades if t.entry_vwap_dev_pct is not None and t.entry_vwap_dev_pct >= VWAP_DEV_THRESHOLD_B]
        if not candidates_raw:
            continue

        tick_cache: dict[str, list[tuple[float, dict[str, Any]]]] = {}
        for sym in {t.symbol for t in candidates_raw}:
            tick_cache[sym] = _bounded_ticks_for_trades(
                push_dir, sym, [t.entry_ts for t in candidates_raw if t.symbol == sym]
            )

        for t in candidates_raw:
            dev = float(t.entry_vwap_dev_pct)
            close_ts = close_map.get((t.symbol, t.entry_time), t.entry_ts + 300)
            entry_px = entry_px_map.get((t.symbol, t.entry_time), 0.0)
            r60 = _compute_r60(
                entry_ts=t.entry_ts,
                entry_px=entry_px,
                close_ts=close_ts,
                push_ticks=tick_cache.get(t.symbol, []),
            )
            out.append(
                CandidateTrade(
                    session_id=sid,
                    day_stamp=day_stamp,
                    symbol=t.symbol,
                    entry_time=t.entry_time,
                    entry_ts=t.entry_ts,
                    close_ts=close_ts,
                    pnl_pct=t.pnl_pct,
                    exit_reason=t.exit_reason,
                    entry_vwap_dev_pct=dev,
                    r30_sec=t.r30_sec,
                    r60_sec=r60,
                    mfe_pct=t.mfe_pct,
                )
            )
    return out


def _summarize(trades: Sequence[CandidateTrade | VwapReviewTrade]) -> dict[str, Any]:
    if not trades:
        return {"trade_count": 0}
    pnls = [t.pnl_pct for t in trades]
    pf = _pf(pnls)
    n = len(trades)
    stop = sum(1 for t in trades if t.exit_reason == "stop_hit")
    trail = sum(1 for t in trades if t.exit_reason == "trailing_mfe_exit")
    return {
        "trade_count": n,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(_mean(pnls) or 0.0, 4),
        "profit_factor": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "stop_hit_count": stop,
        "stop_hit_rate": round(stop / n, 4),
        "trailing_mfe_exit_count": trail,
        "trailing_mfe_exit_rate": round(trail / n, 4),
        "avg_r30_sec": round(
            _mean([getattr(t, "r30_sec", None) for t in trades if getattr(t, "r30_sec", None) is not None])
            or 0,
            4,
        ),
        "avg_r60_sec": round(
            _mean([getattr(t, "r60_sec", None) for t in trades if getattr(t, "r60_sec", None) is not None])
            or 0,
            4,
        ),
        "avg_entry_vwap_dev_pct": round(
            _mean(
                [
                    getattr(t, "entry_vwap_dev_pct", None)
                    for t in trades
                    if getattr(t, "entry_vwap_dev_pct", None) is not None
                ]
            )
            or 0,
            4,
        ),
    }


def _feature_profile(trades: Sequence[CandidateTrade]) -> dict[str, Any]:
    if not trades:
        return {"trade_count": 0}
    reasons = Counter(t.exit_reason for t in trades)
    return {
        "trade_count": len(trades),
        "avg_entry_vwap_dev_pct": round(_mean([t.entry_vwap_dev_pct for t in trades]), 4),
        "avg_r30_sec": round(
            _mean([t.r30_sec for t in trades if t.r30_sec is not None]) or 0, 4
        ),
        "avg_r60_sec": round(
            _mean([t.r60_sec for t in trades if t.r60_sec is not None]) or 0, 4
        ),
        "avg_mfe_pct": round(_mean([t.mfe_pct for t in trades if t.mfe_pct is not None]) or 0, 4)
        if any(t.mfe_pct is not None for t in trades)
        else None,
        "avg_pnl_pct": round(_mean([t.pnl_pct for t in trades]) or 0, 4),
        "stop_hit_rate": round(
            sum(1 for t in trades if t.exit_reason == "stop_hit") / len(trades), 4
        ),
        "trailing_mfe_exit_rate": round(
            sum(1 for t in trades if t.exit_reason == "trailing_mfe_exit") / len(trades), 4
        ),
        "exit_reason_counts": dict(reasons),
        "vwap_band_counts": dict(Counter(t.vwap_band() for t in trades if t.vwap_band())),
    }


def _split_r30(trades: Sequence[CandidateTrade]) -> dict[str, Any]:
    lt0 = [t for t in trades if t.r30_sec is not None and t.r30_sec < 0]
    ge0 = [t for t in trades if t.r30_sec is not None and t.r30_sec >= 0]
    unknown = [t for t in trades if t.r30_sec is None]
    return {
        "r30_lt_0": _summarize(lt0),
        "r30_gte_0": _summarize(ge0),
        "r30_unknown": _summarize(unknown),
        "pf_delta_lt0_minus_gte0": None,
    }


def _split_r60(trades: Sequence[CandidateTrade]) -> dict[str, Any]:
    lt0 = [t for t in trades if t.r60_sec is not None and t.r60_sec < 0]
    ge0 = [t for t in trades if t.r60_sec is not None and t.r60_sec >= 0]
    unknown = [t for t in trades if t.r60_sec is None]
    return {
        "r60_lt_0": _summarize(lt0),
        "r60_gte_0": _summarize(ge0),
        "r60_unknown": _summarize(unknown),
    }


def _vwap_bands(trades: Sequence[CandidateTrade]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, lo, hi in VWAP_BANDS:
        grp = [t for t in trades if lo <= t.entry_vwap_dev_pct < hi]
        out[name] = {
            "range_pct": [lo, hi if hi < 100 else None],
            **_summarize(grp),
        }
    return out


def _focus_by_symbol(trades: Sequence[CandidateTrade]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for sym in sorted(FOCUS_SYMBOLS):
        grp = [t for t in trades if t.symbol == sym]
        if not grp:
            out[sym] = {"trade_count": 0}
            continue
        winners = [t for t in grp if t.pnl_pct > 0]
        losers = [t for t in grp if t.pnl_pct <= 0]
        out[sym] = {
            "aggregate": _summarize(grp),
            "r30_lt_0": _summarize([t for t in grp if t.r30_sec is not None and t.r30_sec < 0]),
            "r30_gte_0": _summarize([t for t in grp if t.r30_sec is not None and t.r30_sec >= 0]),
            "winners": _summarize(winners),
            "losers": _summarize(losers),
        }
    return out


def _winner_loser_split(trades: Sequence[CandidateTrade]) -> dict[str, Any]:
    winners = [t for t in trades if t.pnl_pct > 0]
    losers = [t for t in trades if t.pnl_pct <= 0]
    return {
        "winners": {
            **_summarize(winners),
            "features": _feature_profile(winners),
        },
        "losers": {
            **_summarize(losers),
            "features": _feature_profile(losers),
        },
    }


def _post_hoc_from_candidates(
    all_trades: Sequence[VwapReviewTrade],
    candidates: Sequence[CandidateTrade],
) -> dict[str, Any]:
    cand_keys = {(t.symbol, t.entry_time) for t in candidates}
    cand_r30_neg = {
        (t.symbol, t.entry_time) for t in candidates if t.r30_sec is not None and t.r30_sec < 0
    }
    cand_r60_neg = {
        (t.symbol, t.entry_time) for t in candidates if t.r60_sec is not None and t.r60_sec < 0
    }

    def key(t: VwapReviewTrade) -> tuple[str, str]:
        return (t.symbol, t.entry_time)

    kept_a = list(all_trades)
    kept_b = [t for t in all_trades if key(t) not in cand_keys]
    kept_c = [t for t in all_trades if key(t) not in cand_r30_neg]
    kept_d = [t for t in all_trades if key(t) not in cand_r60_neg]

    sum_a = _summarize(kept_a)
    sum_b = _summarize(kept_b)
    sum_c = _summarize(kept_c)
    sum_d = _summarize(kept_d)

    def delta(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k in ("trade_count", "total_pnl_pct", "profit_factor", "stop_hit_rate", "trailing_mfe_exit_rate"):
            a, b = base.get(k), other.get(k)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                out[k] = round(float(b) - float(a), 4)
        return out

    return {
        "A_current": {
            "description": "current (all trades)",
            **sum_a,
            "excluded_count": 0,
        },
        "B_vwap_only": {
            "description": "post-hoc exclude vwap_shadow_reject_candidate (entry_vwap_dev_pct >= 2.5%)",
            **sum_b,
            "excluded_count": len(all_trades) - len(kept_b),
            "delta_vs_A": delta(sum_a, sum_b),
        },
        "C_vwap_plus_r30_lt_0": {
            "description": "post-hoc exclude candidate with r30_sec < 0",
            **sum_c,
            "excluded_count": len(all_trades) - len(kept_c),
            "delta_vs_A": delta(sum_a, sum_c),
        },
        "D_vwap_plus_r60_lt_0": {
            "description": "post-hoc exclude candidate with r60_sec < 0",
            **sum_d,
            "excluded_count": len(all_trades) - len(kept_d),
            "delta_vs_A": delta(sum_a, sum_d),
        },
    }


def evaluate_vwap_adverse_cluster_analysis(*, repo_root: Path) -> dict[str, Any]:
    base = repo_root / "kabu_native" / "results" / "small_paper"
    session_dirs, excluded = discover_sessions(base)

    all_trades: list[VwapReviewTrade] = []
    trades_by_session: dict[str, list[VwapReviewTrade]] = {}
    for sdir in session_dirs:
        sid = _session_id(sdir, base)
        loaded = load_session_trades(sdir, repo_root=repo_root, base=base)
        trades_by_session[sid] = loaded
        all_trades.extend(loaded)

    candidates = load_enriched_trades(repo_root, base, all_trades_by_session=trades_by_session)
    false_positives = [t for t in candidates if t.is_false_positive]

    r30_split = _split_r30(candidates)
    pf_lt = (r30_split.get("r30_lt_0") or {}).get("profit_factor")
    pf_ge = (r30_split.get("r30_gte_0") or {}).get("profit_factor")
    if isinstance(pf_lt, (int, float)) and isinstance(pf_ge, (int, float)):
        r30_split["pf_delta_lt0_minus_gte0"] = round(float(pf_lt) - float(pf_ge), 4)

    post_hoc = _post_hoc_from_candidates(all_trades, candidates)
    pf_a = (post_hoc.get("A_current") or {}).get("profit_factor")
    pf_b = (post_hoc.get("B_vwap_only") or {}).get("profit_factor")
    pf_c = (post_hoc.get("C_vwap_plus_r30_lt_0") or {}).get("profit_factor")
    pf_d = (post_hoc.get("D_vwap_plus_r60_lt_0") or {}).get("profit_factor")

    r30_lt = r30_split.get("r30_lt_0") or {}
    r30_ge = r30_split.get("r30_gte_0") or {}
    adverse_cluster_supported = (
        isinstance(pf_lt, (int, float))
        and isinstance(pf_ge, (int, float))
        and float(pf_lt) < float(pf_ge) - 0.1
    )

    return {
        "phase": 187,
        "mode": "vwap_adverse_cluster_analysis",
        "hypothesis": (
            "Harmful cluster is high VWAP entry followed by immediate adverse move (r30/r60 < 0), "
            "not VWAP deviation alone."
        ),
        "constraints": {
            "hard_reject": False,
            "shadow_review_only": True,
            "no_single_day_optimization": True,
            "fixed_comparisons_only": True,
        },
        "fixed_thresholds": {
            "vwap_shadow_reject_min_pct": VWAP_DEV_THRESHOLD_B,
            "r30_adverse_max": 0.0,
            "r60_adverse_max": 0.0,
            "vwap_bands_pct": [[2.5, 3.0], [3.0, 4.0], [4.0, None]],
        },
        "reference_session_set": list(REFERENCE_SESSIONS) + list(OBSERVER_EXIT_SESSIONS),
        "session_count_included": len(session_dirs),
        "excluded_sessions": excluded,
        "all_trade_count": len(all_trades),
        "candidate_trade_count": len(candidates),
        "within_candidate_analysis": {
            "r30_split": r30_split,
            "r60_split": _split_r60(candidates),
            "vwap_bands": _vwap_bands(candidates),
            "focus_symbols": _focus_by_symbol(candidates),
            "false_positive": {
                "count": len(false_positives),
                "rate_of_candidates": round(len(false_positives) / max(1, len(candidates)), 4),
                "common_features": _feature_profile(false_positives),
                "trades": [t.to_dict() for t in false_positives],
            },
            "winner_loser_split": _winner_loser_split(candidates),
        },
        "post_hoc_scenarios": {
            "A_vwap_only": post_hoc.get("B_vwap_only"),
            "B_vwap_plus_r30": post_hoc.get("C_vwap_plus_r30_lt_0"),
            "C_vwap_plus_r60": post_hoc.get("D_vwap_plus_r60_lt_0"),
            "baseline_A_current": post_hoc.get("A_current"),
        },
        "verdict": {
            "adverse_r30_cluster_worse_than_non_adverse": adverse_cluster_supported,
            "candidate_r30_lt_0_pf": pf_lt,
            "candidate_r30_gte_0_pf": pf_ge,
            "post_hoc_pf_A_current": pf_a,
            "post_hoc_pf_vwap_only_exclude": pf_b,
            "post_hoc_pf_vwap_plus_r30_exclude": pf_c,
            "post_hoc_pf_vwap_plus_r60_exclude": pf_d,
            "best_post_hoc_scenario_by_pf": max(
                [
                    ("A_current", pf_a),
                    ("B_vwap_only", pf_b),
                    ("C_vwap_plus_r30", pf_c),
                    ("D_vwap_plus_r60", pf_d),
                ],
                key=lambda x: float(x[1]) if isinstance(x[1], (int, float)) else -1,
            )[0],
            "note": "Shadow/review only; no hard reject implemented.",
        },
        "candidate_trades": [t.to_dict() for t in candidates],
    }
