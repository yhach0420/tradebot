"""
Phase649: PBv2 Flat-band Guard Counterfactual (research only).

Validates Phase648 worst cell (Rise5 Flat × Rise10 Flat) as post-PBv2 filter candidate.
PBv2 accepted only; OR excluded. Phase634 full-period loader. No ENTRY/YAML changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase631_profit_source_attribution import _minutes_from_open, _num
from research.phase632_pbv2_profit_filter_counterfactual import (
    _daily_pnl,
    _max_drawdown,
    _metrics,
    _profit_factor,
)
from research.phase634_pbv2_only_rise5_full_period import (
    PRE625_CUTOFF,
    _delta_slice,
    _session_bucket,
    _slice_metrics,
    load_all_full_period_trades,
)

PHASE649_VERDICT = "phase649_flat_band_guard_counterfactual_done"
REPORT_DIR_NAME = "phase649_flat_band_guard"
BLOCKED_MAX_ROWS = 300
SUMCO_SYMBOL = "3436.T"
SUMCO_DAY = "2026-07-06"
PHASE635_RISE5_THRESHOLD = 1.84

NATIVE_ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"

HEATMAP_FLAT_LO = -0.5
HEATMAP_FLAT_HI = 0.5

BlockFn = Callable[[Mapping[str, Any]], bool]
KeepFn = Callable[[Mapping[str, Any]], bool]


def filter_pbv2_trades(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(t) for t in trades if str(t.get("entry_pool") or "") == "PBV2"]


def _in_closed_range(v: float, lo: float, hi: float) -> bool:
    return lo <= v <= hi


def _in_half_open(v: float, lo: float, hi: float) -> bool:
    return lo <= v < hi


def block_flat_cell_only(t: Mapping[str, Any]) -> bool:
    r5 = _num(t.get("entry_rise_5min_pct"))
    r10 = _num(t.get("entry_rise_10min_pct"))
    if r5 is None or r10 is None:
        return False
    return _in_closed_range(r5, HEATMAP_FLAT_LO, HEATMAP_FLAT_HI) and _in_closed_range(
        r10, HEATMAP_FLAT_LO, HEATMAP_FLAT_HI
    )


def block_flat_band_narrow(t: Mapping[str, Any]) -> bool:
    r5 = _num(t.get("entry_rise_5min_pct"))
    r10 = _num(t.get("entry_rise_10min_pct"))
    if r5 is None or r10 is None:
        return False
    return _in_half_open(r5, 0.0, 0.5) and _in_closed_range(r10, -0.5, 0.5)


def block_flat_band_wide(t: Mapping[str, Any]) -> bool:
    r5 = _num(t.get("entry_rise_5min_pct"))
    r10 = _num(t.get("entry_rise_10min_pct"))
    if r5 is None or r10 is None:
        return False
    return _in_half_open(r5, 0.0, 0.5) and _in_closed_range(r10, -0.5, 1.0)


def block_weak_motion_guard(t: Mapping[str, Any]) -> bool:
    r5 = _num(t.get("entry_rise_5min_pct"))
    r10 = _num(t.get("entry_rise_10min_pct"))
    if r5 is None or r10 is None:
        return False
    return abs(r5) < 0.5 and abs(r10) < 0.5


def block_rise5_overheat(t: Mapping[str, Any], threshold: float = 2.0) -> bool:
    r5 = _num(t.get("entry_rise_5min_pct"))
    if r5 is None:
        return False
    return r5 > threshold


def block_flat_plus_overheat(t: Mapping[str, Any]) -> bool:
    return block_flat_band_narrow(t) or block_rise5_overheat(t, 2.0)


def block_phase635_rise5_shadow(t: Mapping[str, Any], threshold: float = PHASE635_RISE5_THRESHOLD) -> bool:
    r5 = _num(t.get("entry_rise_5min_pct"))
    if r5 is None:
        return False
    return r5 > threshold


VARIANT_SPECS: list[tuple[str, str, BlockFn]] = [
    ("baseline", "baseline (no guard)", lambda _t: False),
    ("flat_cell_only", "Rise5 Flat × Rise10 Flat", block_flat_cell_only),
    ("flat_band_narrow", "Rise5 0~0.5% AND Rise10 -0.5~0.5%", block_flat_band_narrow),
    ("flat_band_wide", "Rise5 0~0.5% AND Rise10 -0.5~1.0%", block_flat_band_wide),
    ("weak_motion_guard", "abs(Rise5)<0.5% AND abs(Rise10)<0.5%", block_weak_motion_guard),
    ("flat_plus_overheat", "flat_band_narrow OR Rise5>2%", block_flat_plus_overheat),
]


def keep_fn(block_fn: BlockFn) -> KeepFn:
    return lambda t: not block_fn(t)


def apply_variant(
    trades: Sequence[Mapping[str, Any]],
    *,
    variant_id: str,
    label: str,
    block_fn: BlockFn,
    baseline_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    filt = keep_fn(block_fn)
    kept = [dict(t) for t in trades if filt(t)]
    blocked = [dict(t) for t in trades if block_fn(t)]
    m = _metrics(kept)
    wrong_win = [t for t in blocked if float(t["pnl_yen_100"]) > 0]
    rescued = [t for t in blocked if float(t["pnl_yen_100"]) < 0]

    base_pf = baseline_metrics.get("profit_factor")
    cur_pf = m.get("profit_factor")
    delta_pf = None
    if isinstance(base_pf, (int, float)) and isinstance(cur_pf, (int, float)):
        if base_pf != 999.0 and cur_pf != 999.0:
            delta_pf = round(float(cur_pf) - float(base_pf), 4)

    sumco_blocked = [
        t
        for t in blocked
        if str(t.get("symbol") or "") == SUMCO_SYMBOL and str(t.get("day") or "") == SUMCO_DAY
    ]

    base_pnl = float(baseline_metrics["pnl_yen_100"])
    base_dd = float(baseline_metrics["max_dd_yen_100"])
    base_n = int(baseline_metrics["entry_count"])

    return {
        "variant_id": variant_id,
        "label": label,
        "entry_count": m["entry_count"],
        "blocked_entry_count": len(blocked),
        "entry_reduction_pct": round(100.0 * len(blocked) / max(1, base_n), 2),
        "delta_pnl_yen_100": round(float(m["pnl_yen_100"]) - base_pnl, 2),
        "delta_pf": delta_pf,
        "delta_max_dd_yen_100": round(float(m["max_dd_yen_100"]) - base_dd, 2),
        "profit_factor": m.get("profit_factor"),
        "win_rate": m.get("win_rate"),
        "avg_pnl_yen_100": m.get("avg_pnl_yen_100"),
        "max_dd_yen_100": m.get("max_dd_yen_100"),
        "pnl_yen_100": m.get("pnl_yen_100"),
        "wrongly_blocked_winners": len(wrong_win),
        "wrongly_blocked_winners_pnl": round(sum(float(t["pnl_yen_100"]) for t in wrong_win), 2),
        "rescued_losers": len(rescued),
        "rescued_losers_pnl": round(sum(float(t["pnl_yen_100"]) for t in rescued), 2),
        "sumco_blocked_count": len(sumco_blocked),
        "sumco_blocked_pnl": round(sum(float(t["pnl_yen_100"]) for t in sumco_blocked), 2),
        "_kept_trades": kept,
        "_blocked_trades": blocked,
    }


def daily_breakdown(
    trades: Sequence[Mapping[str, Any]], variants: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    base_daily = _daily_pnl(trades)
    rows: list[dict[str, Any]] = []
    for v in variants:
        vid = str(v["variant_id"])
        if vid == "baseline":
            for day, pnl in base_daily.items():
                rows.append(
                    {
                        "variant_id": vid,
                        "day": day,
                        "baseline_pnl_yen_100": pnl,
                        "kept_pnl_yen_100": pnl,
                        "delta_pnl_yen_100": 0.0,
                        "blocked_count": 0,
                    }
                )
            continue
        kept = list(v.get("_kept_trades") or [])
        kept_daily = _daily_pnl(kept)
        blocked = list(v.get("_blocked_trades") or [])
        blocked_by_day: dict[str, int] = defaultdict(int)
        for t in blocked:
            blocked_by_day[str(t.get("day") or "")] += 1
        for day in sorted(set(base_daily) | set(kept_daily)):
            base_p = float(base_daily.get(day, 0.0))
            kept_p = float(kept_daily.get(day, 0.0))
            rows.append(
                {
                    "variant_id": vid,
                    "day": day,
                    "baseline_pnl_yen_100": base_p,
                    "kept_pnl_yen_100": kept_p,
                    "delta_pnl_yen_100": round(kept_p - base_p, 2),
                    "blocked_count": blocked_by_day.get(day, 0),
                }
            )
    return rows


def symbol_breakdown(
    trades: Sequence[Mapping[str, Any]], variants: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol") or "")].append(dict(t))

    for v in variants:
        vid = str(v["variant_id"])
        if vid == "baseline":
            continue
        filt = keep_fn(_block_fn_for_variant(vid))
        for sym, seq in sorted(by_sym.items()):
            d = _delta_slice(sym, seq, filt)
            rows.append(
                {
                    "variant_id": vid,
                    "symbol": sym,
                    "entry_count": d["baseline_n"],
                    "kept_entry_count": d["kept_n"],
                    "baseline_pnl_yen_100": d["baseline_pnl"],
                    "kept_pnl_yen_100": d["kept_pnl"],
                    "delta_pnl_yen_100": d["delta_pnl"],
                    "delta_pf": round(float(d["kept_pf"] or 0) - float(d["baseline_pf"] or 0), 4)
                    if d["kept_pf"] is not None and d["baseline_pf"] is not None
                    else None,
                }
            )
    return rows


def _block_fn_for_variant(variant_id: str) -> BlockFn:
    for vid, _label, fn in VARIANT_SPECS:
        if vid == variant_id:
            return fn
    return lambda _t: False


def leave_one_symbol_out(
    trades: Sequence[Mapping[str, Any]], variants: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol") or "")].append(dict(t))

    for v in variants:
        vid = str(v["variant_id"])
        if vid == "baseline":
            continue
        total_delta = float(v.get("delta_pnl_yen_100") or 0)
        filt = keep_fn(_block_fn_for_variant(vid))
        sym_deltas: list[tuple[str, float]] = []
        for sym, seq in by_sym.items():
            sym_deltas.append((sym, float(_delta_slice(sym, seq, filt)["delta_pnl"])))
        sym_deltas.sort(key=lambda x: x[1], reverse=True)
        for sym, sym_delta in sym_deltas[:15]:
            if sym_delta <= 0:
                continue
            base_wo = [t for t in trades if t.get("symbol") != sym]
            kept_wo = [t for t in base_wo if filt(t)]
            d_wo = float(_slice_metrics(kept_wo)["pnl_yen_100"]) - float(
                _slice_metrics(base_wo)["pnl_yen_100"]
            )
            rows.append(
                {
                    "variant_id": vid,
                    "excluded_symbol": sym,
                    "symbol_delta_pnl_yen_100": round(sym_delta, 2),
                    "delta_pnl_without_symbol": round(d_wo, 2),
                    "still_positive": d_wo > 0,
                    "share_of_total_delta": (
                        round(abs(sym_delta) / abs(total_delta), 4) if abs(total_delta) > 1e-6 else None
                    ),
                }
            )
    return rows


def blocked_trades_rows(variants: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    per_variant = BLOCKED_MAX_ROWS // max(1, len([v for v in variants if v["variant_id"] != "baseline"]))
    for v in variants:
        vid = str(v["variant_id"])
        if vid == "baseline":
            continue
        blocked = sorted(
            list(v.get("_blocked_trades") or []),
            key=lambda t: float(t["pnl_yen_100"]),
        )
        for t in blocked[:per_variant]:
            rows.append(
                {
                    "variant_id": vid,
                    "day": t.get("day"),
                    "symbol": t.get("symbol"),
                    "entry_time": t.get("entry_time"),
                    "pnl_yen_100": t.get("pnl_yen_100"),
                    "entry_rise_5min_pct": t.get("entry_rise_5min_pct"),
                    "entry_rise_10min_pct": t.get("entry_rise_10min_pct"),
                    "exit_reason": t.get("exit_reason"),
                }
            )
    return rows


def period_session_slices(
    trades: Sequence[Mapping[str, Any]], variant_id: str, block_fn: BlockFn
) -> dict[str, Any]:
    filt = keep_fn(block_fn)
    pre = [t for t in trades if str(t.get("day") or "") < PRE625_CUTOFF]
    post = [t for t in trades if str(t.get("day") or "") >= PRE625_CUTOFF]
    am = [t for t in trades if _session_bucket(t) == "AM"]
    pm = [t for t in trades if _session_bucket(t) == "PM"]
    out: dict[str, Any] = {}
    for key, subset in (("pre625", pre), ("post625", post), ("AM", am), ("PM", pm)):
        if not subset:
            out[f"{key}_delta_pnl_yen_100"] = None
            continue
        out[f"{key}_delta_pnl_yen_100"] = _delta_slice(key, subset, filt)["delta_pnl"]
    return out


def phase635_overlap(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    flat_oh = [t for t in trades if block_flat_plus_overheat(t)]
    rise5_shadow = [t for t in trades if block_phase635_rise5_shadow(t)]
    flat_only = [t for t in trades if block_flat_band_narrow(t)]
    overheat_only = [t for t in trades if block_rise5_overheat(t)]
    overlap_oh_shadow = [
        t
        for t in trades
        if block_flat_plus_overheat(t) and block_phase635_rise5_shadow(t)
    ]
    return {
        "flat_plus_overheat_blocked": len(flat_oh),
        "phase635_rise5_shadow_blocked": len(rise5_shadow),
        "overlap_blocked": len(overlap_oh_shadow),
        "flat_narrow_only_blocked": len(flat_only),
        "overheat_only_blocked": len([t for t in trades if block_rise5_overheat(t) and not block_flat_band_narrow(t)]),
        "competes_with_phase635": len(overlap_oh_shadow) > 0,
        "mostly_complementary": len(flat_only) > len(overlap_oh_shadow),
    }


def build_mandatory_answers(
    *,
    pbv2_trades: Sequence[Mapping[str, Any]],
    variants: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
    loo_rows: Sequence[Mapping[str, Any]],
    overlap: Mapping[str, Any],
) -> dict[str, Any]:
    baseline = next(v for v in variants if v["variant_id"] == "baseline")
    candidates = [v for v in variants if v["variant_id"] != "baseline"]
    best = max(candidates, key=lambda v: float(v.get("delta_pnl_yen_100") or 0), default={})
    best_dd = max(candidates, key=lambda v: float(v.get("delta_max_dd_yen_100") or 0), default={})

    improved = [v for v in candidates if float(v.get("delta_pnl_yen_100") or 0) > 0]
    full_period_improves = len(improved) > 0

    worst_days: list[dict[str, Any]] = []
    if best:
        vid = str(best["variant_id"])
        by_day = [r for r in daily_rows if r.get("variant_id") == vid]
        by_day.sort(key=lambda r: float(r.get("delta_pnl_yen_100") or 0))
        worst_days = list(by_day[:5])

    loo_best = [r for r in loo_rows if r.get("variant_id") == best.get("variant_id")]
    loo_fail = [r for r in loo_best if not r.get("still_positive")]
    max_share = max((float(r.get("share_of_total_delta") or 0) for r in loo_best), default=0.0)

    pre625_ok = all(
        float(period_session_slices(pbv2_trades, v["variant_id"], _block_fn_for_variant(v["variant_id"]))[
            "pre625_delta_pnl_yen_100"
        ] or 0)
        > 0
        for v in improved
    ) if improved else False
    post625_ok = all(
        float(period_session_slices(pbv2_trades, v["variant_id"], _block_fn_for_variant(v["variant_id"]))[
            "post625_delta_pnl_yen_100"
        ] or 0)
        > 0
        for v in improved
    ) if improved else False

    symbol_dependent = max_share > 0.35 or len(loo_fail) > 3

    if float(best.get("delta_pnl_yen_100") or 0) > 20000 and float(best.get("delta_max_dd_yen_100") or 0) > 50000:
        production_candidate = "Yes — shadow then selective ON"
        recommendation = "HOLD — counterfactual positive; proceed to shadow guard"
    elif float(best.get("delta_pnl_yen_100") or 0) > 0:
        production_candidate = "Marginal — shadow only"
        recommendation = "HOLD — modest improvement; shadow before any ON"
    else:
        production_candidate = "No"
        recommendation = "Reject — flat-band guard does not improve full-period PnL"

    return {
        "1_full_period_improves": full_period_improves,
        "1_detail": (
            f"{len(improved)}/{len(candidates)} variants improve ΔPnL; "
            f"baseline PnL {baseline.get('pnl_yen_100')} yen."
        ),
        "2_best_variant": best.get("variant_id"),
        "2_best_variant_delta_pnl": best.get("delta_pnl_yen_100"),
        "2_best_variant_delta_dd": best.get("delta_max_dd_yen_100"),
        "2_best_dd_variant": best_dd.get("variant_id"),
        "3_worst_days": worst_days,
        "3_any_large_worsening_days": any(float(d.get("delta_pnl_yen_100") or 0) < -20000 for d in worst_days),
        "4_symbol_dependent": symbol_dependent,
        "4_max_loo_share": max_share,
        "4_loo_failures_top15": len(loo_fail),
        "5_pbv2_only_valid": True,
        "5_or_excluded_from_dataset": True,
        "6_phase635_overlap": overlap,
        "6_competes_with_rise5_shadow": (
            "Mostly complementary — flat-band targets 0~0.5% rise; "
            f"Phase635 rise5 shadow targets rise5>{PHASE635_RISE5_THRESHOLD}%. "
            f"Overlap blocked={overlap.get('overlap_blocked')}."
        ),
        "7_production_candidate": production_candidate,
        "8_shadow_hook": (
            "New module `src/small_paper/pbv2_flat_band_guard_shadow.py`; "
            "wire at PBv2 accept in `pilot_runner.py` (same hook as `compute_pbv2_rise5_shadow_fields`); "
            "exit enrichment in `observer_position_tracker.py`."
        ),
        "9_end_state": (
            "PBv2 accepted → flat-band shadow guard (PBV2_ONLY) → live paper validation → "
            "optional production ON alongside Phase635 rise5 upper-cap (orthogonal axes)."
        ),
        "recommendation": recommendation,
        "sumco_any_variant_blocks": any(int(v.get("sumco_blocked_count") or 0) > 0 for v in candidates),
    }


@dataclass
class Phase649Job:
    native_root: Path

    def run(self) -> dict[str, Any]:
        root = self.native_root / "results" / "small_paper"
        all_trades, sessions = load_all_full_period_trades(root)
        pbv2 = filter_pbv2_trades(all_trades)
        baseline_metrics = _metrics(pbv2)

        variants: list[dict[str, Any]] = []
        for vid, label, block_fn in VARIANT_SPECS:
            v = apply_variant(
                pbv2,
                variant_id=vid,
                label=label,
                block_fn=block_fn,
                baseline_metrics=baseline_metrics,
            )
            if vid != "baseline":
                v.update(period_session_slices(pbv2, vid, block_fn))
            variants.append(v)

        daily_rows = daily_breakdown(pbv2, variants)
        symbol_rows = symbol_breakdown(pbv2, variants)
        loo_rows = leave_one_symbol_out(pbv2, variants)
        blocked_rows = blocked_trades_rows(variants)
        overlap = phase635_overlap(pbv2)
        answers = build_mandatory_answers(
            pbv2_trades=pbv2,
            variants=variants,
            daily_rows=daily_rows,
            loo_rows=loo_rows,
            overlap=overlap,
        )

        public_variants = []
        for v in variants:
            public_variants.append({k: val for k, val in v.items() if not str(k).startswith("_")})

        return {
            "verdict": PHASE649_VERDICT,
            "generated_at": _now_iso(),
            "session_count": len(sessions),
            "all_trade_count": len(all_trades),
            "pbv2_count": len(pbv2),
            "baseline_metrics": {k: v for k, v in baseline_metrics.items() if k != "profit_factor_raw"},
            "mandatory_answers": answers,
            "variants": public_variants,
            "daily_breakdown": daily_rows,
            "symbol_breakdown": symbol_rows,
            "leave_one_symbol_out": loo_rows,
            "blocked_trades": blocked_rows,
            "phase635_overlap": overlap,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        out = self.native_root / "results" / "reports" / REPORT_DIR_NAME
        out.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}

        variant_cols = [
            "variant_id",
            "label",
            "entry_count",
            "blocked_entry_count",
            "entry_reduction_pct",
            "delta_pnl_yen_100",
            "delta_pf",
            "delta_max_dd_yen_100",
            "profit_factor",
            "win_rate",
            "avg_pnl_yen_100",
            "max_dd_yen_100",
            "pnl_yen_100",
            "wrongly_blocked_winners",
            "wrongly_blocked_winners_pnl",
            "rescued_losers",
            "rescued_losers_pnl",
            "pre625_delta_pnl_yen_100",
            "post625_delta_pnl_yen_100",
            "AM_delta_pnl_yen_100",
            "PM_delta_pnl_yen_100",
            "sumco_blocked_count",
            "sumco_blocked_pnl",
        ]
        _write_csv(out / "phase649_variant_comparison.csv", variant_cols, list(result.get("variants") or []))
        paths["variant_comparison"] = out / "phase649_variant_comparison.csv"

        _write_csv(
            out / "phase649_daily_breakdown.csv",
            [
                "variant_id",
                "day",
                "baseline_pnl_yen_100",
                "kept_pnl_yen_100",
                "delta_pnl_yen_100",
                "blocked_count",
            ],
            list(result.get("daily_breakdown") or []),
        )
        paths["daily_breakdown"] = out / "phase649_daily_breakdown.csv"

        _write_csv(
            out / "phase649_symbol_breakdown.csv",
            [
                "variant_id",
                "symbol",
                "entry_count",
                "kept_entry_count",
                "baseline_pnl_yen_100",
                "kept_pnl_yen_100",
                "delta_pnl_yen_100",
                "delta_pf",
            ],
            list(result.get("symbol_breakdown") or []),
        )
        paths["symbol_breakdown"] = out / "phase649_symbol_breakdown.csv"

        _write_csv(
            out / "phase649_blocked_trades.csv",
            [
                "variant_id",
                "day",
                "symbol",
                "entry_time",
                "pnl_yen_100",
                "entry_rise_5min_pct",
                "entry_rise_10min_pct",
                "exit_reason",
            ],
            list(result.get("blocked_trades") or []),
        )
        paths["blocked_trades"] = out / "phase649_blocked_trades.csv"

        _write_csv(
            out / "phase649_leave_one_symbol_out.csv",
            [
                "variant_id",
                "excluded_symbol",
                "symbol_delta_pnl_yen_100",
                "delta_pnl_without_symbol",
                "still_positive",
                "share_of_total_delta",
            ],
            list(result.get("leave_one_symbol_out") or []),
        )
        paths["leave_one_symbol_out"] = out / "phase649_leave_one_symbol_out.csv"

        report = {
            "phase": "649",
            "verdict": result.get("verdict"),
            "generated_at": result.get("generated_at"),
            "session_count": result.get("session_count"),
            "pbv2_count": result.get("pbv2_count"),
            "baseline_metrics": result.get("baseline_metrics"),
            "mandatory_answers": result.get("mandatory_answers"),
            "variants": result.get("variants"),
            "phase635_overlap": result.get("phase635_overlap"),
            "artifacts": {k: str(v) for k, v in paths.items()},
        }
        report_fp = out / "phase649_report.json"
        report_fp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["report"] = report_fp
        return paths


def main() -> int:
    job = Phase649Job(native_root=NATIVE_ROOT)
    result = job.run()
    paths = job.write_outputs(result)
    print(json.dumps({"verdict": result.get("verdict"), "paths": {k: str(v) for k, v in paths.items()}}, indent=2))
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
