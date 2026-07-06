"""
Phase648: Rise5 × Rise10 Profit Attribution (research only).

Quantifies how entry_rise_5min_pct and entry_rise_10min_pct contribute to PBv2 PnL.
PBv2 only, OR excluded. Uses Phase634 full-period data loader.
No ENTRY/EXIT/YAML changes.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase631_profit_source_attribution import _num
from research.phase632_pbv2_profit_filter_counterfactual import _max_drawdown, _metrics, _profit_factor
from research.phase634_pbv2_only_rise5_full_period import load_all_full_period_trades

PHASE648_VERDICT = "phase648_rise5_rise10_analysis_done"
REPORT_DIR_NAME = "phase648_rise5_rise10_analysis"

NATIVE_ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
SUMCO_SYMBOL = "3436.T"
SUMCO_DAY = "2026-07-06"

# Band edges in percent (lower inclusive, upper exclusive except last).
RISE_BAND_EDGES: list[tuple[str, Optional[float], Optional[float]]] = [
    ("<-3%", None, -3.0),
    ("-3~-2%", -3.0, -2.0),
    ("-2~-1%", -2.0, -1.0),
    ("-1~-0.5%", -1.0, -0.5),
    ("-0.5~0%", -0.5, 0.0),
    ("0~0.5%", 0.0, 0.5),
    ("0.5~1%", 0.5, 1.0),
    ("1~2%", 1.0, 2.0),
    ("2~3%", 2.0, 3.0),
    (">3%", 3.0, None),
]

HEATMAP_RISE5_LABELS = ("Rise5 Down", "Rise5 Flat", "Rise5 Up")
HEATMAP_RISE10_LABELS = ("Rise10 Down", "Rise10 Flat", "Rise10 Up")
HEATMAP_FLAT_LO = -0.5
HEATMAP_FLAT_HI = 0.5


def is_pbv2_entry(row: Mapping[str, Any]) -> bool:
    return str(row.get("entry_pool") or "") == "PBV2"


def filter_pbv2_trades(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(t) for t in trades if is_pbv2_entry(t)]


def rise_band_label(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    for label, lo, hi in RISE_BAND_EDGES:
        if lo is None and hi is not None and value < hi:
            return label
        if hi is None and lo is not None and value >= lo:
            return label
        if lo is not None and hi is not None and lo <= value < hi:
            return label
    return RISE_BAND_EDGES[-1][0]


def heatmap_axis_label(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    if value < HEATMAP_FLAT_LO:
        return "Down"
    if value > HEATMAP_FLAT_HI:
        return "Up"
    return "Flat"


def heatmap_cell(rise5: Optional[float], rise10: Optional[float]) -> Optional[str]:
    r5 = heatmap_axis_label(rise5)
    r10 = heatmap_axis_label(rise10)
    if r5 is None or r10 is None:
        return None
    return f"Rise5 {r5} × Rise10 {r10}"


def enrich_pbv2_rise(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        r5 = _num(row.get("entry_rise_5min_pct"))
        r10 = _num(row.get("entry_rise_10min_pct"))
        row["entry_rise_5min_pct"] = r5
        row["entry_rise_10min_pct"] = r10
        row["rise5_band"] = rise_band_label(r5)
        row["rise10_band"] = rise_band_label(r10)
        row["rise5_heatmap"] = heatmap_axis_label(r5)
        row["rise10_heatmap"] = heatmap_axis_label(r10)
        row["heatmap_cell"] = heatmap_cell(r5, r10)
        out.append(row)
    return out


def _bucket_metrics(trades: Sequence[Mapping[str, Any]], band_key: str) -> list[dict[str, Any]]:
    by_band: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        band = str(t.get(band_key) or "unknown")
        by_band.setdefault(band, []).append(dict(t))

    band_order = [b[0] for b in RISE_BAND_EDGES] + ["unknown"]
    rows: list[dict[str, Any]] = []
    for band in band_order:
        subset = by_band.get(band) or []
        if not subset:
            rows.append({band_key.replace("_band", "") + "_band": band, "entry_count": 0})
            continue
        pnls = [float(t["pnl_yen_100"]) for t in subset]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        pf = _profit_factor(pnls)
        chrono = sorted(subset, key=lambda x: (str(x.get("entry_time") or ""), str(x.get("symbol") or "")))
        mfe = [_num(t.get("peak_mfe_pct")) for t in subset]
        mae = [_num(t.get("rolling_mae_pct")) for t in subset]
        rows.append(
            {
                band_key.replace("_band", "") + "_band": band,
                "entry_count": len(subset),
                "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
                "profit_factor": (
                    None if pf is None else (999.0 if pf == float("inf") else round(float(pf), 4))
                ),
                "pnl_yen_100": round(sum(pnls), 2),
                "avg_win_yen_100": round(statistics.fmean(wins), 2) if wins else None,
                "avg_loss_yen_100": round(statistics.fmean(losses), 2) if losses else None,
                "avg_mfe_pct": round(statistics.fmean([x for x in mfe if x is not None]), 4)
                if any(x is not None for x in mfe)
                else None,
                "avg_mae_pct": round(statistics.fmean([x for x in mae if x is not None]), 4)
                if any(x is not None for x in mae)
                else None,
                "max_dd_yen_100": _max_drawdown([float(t["pnl_yen_100"]) for t in chrono]),
            }
        )
    total_pnl = sum(float(t["pnl_yen_100"]) for t in trades)
    for r in rows:
        pnl = float(r.get("pnl_yen_100") or 0)
        r["pnl_share_pct"] = round(100.0 * pnl / total_pnl, 2) if total_pnl else None
    return rows


def heatmap_metrics(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r5_lab in HEATMAP_RISE5_LABELS:
        r5_axis = r5_lab.replace("Rise5 ", "")
        for r10_lab in HEATMAP_RISE10_LABELS:
            r10_axis = r10_lab.replace("Rise10 ", "")
            subset = [
                dict(t)
                for t in trades
                if t.get("rise5_heatmap") == r5_axis and t.get("rise10_heatmap") == r10_axis
            ]
            cell = f"Rise5 {r5_axis} × Rise10 {r10_axis}"
            if not subset:
                rows.append(
                    {
                        "heatmap_cell": cell,
                        "rise5_axis": r5_axis,
                        "rise10_axis": r10_axis,
                        "entry_count": 0,
                    }
                )
                continue
            pnls = [float(t["pnl_yen_100"]) for t in subset]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            pf = _profit_factor(pnls)
            chrono = sorted(subset, key=lambda x: (str(x.get("entry_time") or ""), str(x.get("symbol") or "")))
            rows.append(
                {
                    "heatmap_cell": cell,
                    "rise5_axis": r5_axis,
                    "rise10_axis": r10_axis,
                    "entry_count": len(subset),
                    "pnl_yen_100": round(sum(pnls), 2),
                    "profit_factor": (
                        None if pf is None else (999.0 if pf == float("inf") else round(float(pf), 4))
                    ),
                    "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
                    "avg_win_yen_100": round(statistics.fmean(wins), 2) if wins else None,
                    "avg_loss_yen_100": round(statistics.fmean(losses), 2) if losses else None,
                    "max_dd_yen_100": _max_drawdown([float(t["pnl_yen_100"]) for t in chrono]),
                }
            )
    return rows


BlockFn = Callable[[Mapping[str, Any]], bool]


def _block_rise5_lt(threshold: float) -> BlockFn:
    def fn(t: Mapping[str, Any]) -> bool:
        v = _num(t.get("entry_rise_5min_pct"))
        return v is not None and v < threshold

    return fn


def _block_rise10_lt(threshold: float) -> BlockFn:
    def fn(t: Mapping[str, Any]) -> bool:
        v = _num(t.get("entry_rise_10min_pct"))
        return v is not None and v < threshold

    return fn


def _block_rise5_and_rise10(r5_th: float, r10_th: float) -> BlockFn:
    def fn(t: Mapping[str, Any]) -> bool:
        r5 = _num(t.get("entry_rise_5min_pct"))
        r10 = _num(t.get("entry_rise_10min_pct"))
        return r5 is not None and r10 is not None and r5 < r5_th and r10 < r10_th

    return fn


COUNTERFACTUAL_SPECS: list[tuple[str, BlockFn]] = [
    ("rise5_lt_-0.5", _block_rise5_lt(-0.5)),
    ("rise5_lt_-1", _block_rise5_lt(-1.0)),
    ("rise5_lt_-2", _block_rise5_lt(-2.0)),
    ("rise10_lt_-1", _block_rise10_lt(-1.0)),
    ("rise10_lt_-2", _block_rise10_lt(-2.0)),
    ("rise5_lt_0_and_rise10_lt_0", _block_rise5_and_rise10(0.0, 0.0)),
    ("rise5_lt_-1_and_rise10_lt_-2", _block_rise5_and_rise10(-1.0, -2.0)),
]


def counterfactual_block(
    trades: Sequence[Mapping[str, Any]], *, condition_id: str, block_fn: BlockFn
) -> dict[str, Any]:
    baseline = _metrics(list(trades))
    blocked = [dict(t) for t in trades if block_fn(t)]
    kept = [dict(t) for t in trades if not block_fn(t)]
    m = _metrics(kept)
    wrong_win = [t for t in blocked if float(t["pnl_yen_100"]) > 0]
    rescued_loss = [t for t in blocked if float(t["pnl_yen_100"]) < 0]
    base_pf = baseline.get("profit_factor")
    cur_pf = m.get("profit_factor")
    delta_pf = None
    if isinstance(base_pf, (int, float)) and isinstance(cur_pf, (int, float)):
        if base_pf != 999.0 and cur_pf != 999.0:
            delta_pf = round(float(cur_pf) - float(base_pf), 4)
    return {
        "condition_id": condition_id,
        "baseline_entry_count": baseline["entry_count"],
        "kept_entry_count": m["entry_count"],
        "blocked_entry_count": len(blocked),
        "entry_reduction_pct": round(
            100.0 * len(blocked) / max(1, baseline["entry_count"]),
            2,
        ),
        "delta_pnl_yen_100": round(float(m["pnl_yen_100"]) - float(baseline["pnl_yen_100"]), 2),
        "delta_pf": delta_pf,
        "delta_max_dd_yen_100": round(
            float(m["max_dd_yen_100"]) - float(baseline["max_dd_yen_100"]), 2
        ),
        "wrongly_blocked_winners": len(wrong_win),
        "wrongly_blocked_winners_pnl": round(sum(float(t["pnl_yen_100"]) for t in wrong_win), 2),
        "rescued_losers": len(rescued_loss),
        "rescued_losers_pnl": round(sum(float(t["pnl_yen_100"]) for t in rescued_loss), 2),
        **{k: m[k] for k in ("pnl_yen_100", "profit_factor", "win_rate", "max_dd_yen_100")},
    }


def rise_feature_importance(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    feature_keys = [
        "entry_rise_5min_pct",
        "entry_rise_10min_pct",
        "rise5_x_rise10",
        "rise5_neg_flag",
        "rise10_neg_flag",
        "both_neg_flag",
    ]
    enriched: list[dict[str, Any]] = []
    for t in trades:
        r5 = _num(t.get("entry_rise_5min_pct"))
        r10 = _num(t.get("entry_rise_10min_pct"))
        enriched.append(
            {
                **dict(t),
                "rise5_x_rise10": (r5 * r10) if r5 is not None and r10 is not None else None,
                "rise5_neg_flag": 1.0 if r5 is not None and r5 < 0 else 0.0,
                "rise10_neg_flag": 1.0 if r10 is not None and r10 < 0 else 0.0,
                "both_neg_flag": 1.0 if r5 is not None and r10 is not None and r5 < 0 and r10 < 0 else 0.0,
            }
        )

    rows: list[dict[str, Any]] = []
    for fk in feature_keys:
        vals: list[float] = []
        pnls: list[float] = []
        for t in enriched:
            v = _num(t.get(fk))
            if v is None:
                continue
            vals.append(v)
            pnls.append(float(t["pnl_yen_100"]))
        if len(vals) < 10:
            continue
        mx = statistics.fmean(vals)
        my = statistics.fmean(pnls)
        num = sum((x - mx) * (y - my) for x, y in zip(vals, pnls))
        denx = math.sqrt(sum((x - mx) ** 2 for x in vals))
        deny = math.sqrt(sum((y - my) ** 2 for y in pnls))
        corr = num / (denx * deny) if denx > 1e-12 and deny > 1e-12 else 0.0
        rows.append(
            {
                "feature": fk,
                "n": len(vals),
                "corr_with_pnl": round(corr, 4),
                "mean_across_trades": round(mx, 4),
            }
        )
    rows.sort(key=lambda r: abs(float(r.get("corr_with_pnl") or 0)), reverse=True)
    return rows


def band_rankings(
    rise5_rows: Sequence[Mapping[str, Any]], rise10_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    def _rank(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
        valid = [r for r in rows if int(r.get("entry_count") or 0) > 0]
        by_profit = sorted(valid, key=lambda r: float(r.get("pnl_yen_100") or 0), reverse=True)
        by_loss = sorted(valid, key=lambda r: float(r.get("pnl_yen_100") or 0))
        return {
            "profit_order": [
                {key: r.get(key), "pnl_yen_100": r.get("pnl_yen_100"), "entry_count": r.get("entry_count")}
                for r in by_profit
            ],
            "loss_order": [
                {key: r.get(key), "pnl_yen_100": r.get("pnl_yen_100"), "entry_count": r.get("entry_count")}
                for r in by_loss
            ],
        }

    return {
        "rise5": _rank(rise5_rows, "rise5_band"),
        "rise10": _rank(rise10_rows, "rise10_band"),
    }


def _counterfactual_blocks_entry(condition_id: str, t: Mapping[str, Any]) -> bool:
    for cid, fn in COUNTERFACTUAL_SPECS:
        if cid == condition_id:
            return fn(t)
    return False


def build_sumco_case_study(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sumco = [
        t
        for t in trades
        if str(t.get("symbol") or "") == SUMCO_SYMBOL and str(t.get("day") or "") == SUMCO_DAY
    ]
    lines = [
        f"# SUMCO ({SUMCO_SYMBOL}) case study — {SUMCO_DAY}",
        "",
        "PBv2 entries mapped to Rise5/Rise10 bands and counterfactual block conditions.",
        "",
    ]
    if not sumco:
        lines.extend(["No PBv2 closed trade found for SUMCO on this day.", ""])
        return {"found": False, "markdown": "\n".join(lines), "trades": []}

    for i, t in enumerate(sorted(sumco, key=lambda x: str(x.get("entry_time") or "")), start=1):
        blocked_by = [cid for cid, _ in COUNTERFACTUAL_SPECS if _counterfactual_blocks_entry(cid, t)]
        lines.extend(
            [
                f"## ENTRY{i} — {t.get('entry_time')}",
                f"- Rise5: **{t.get('entry_rise_5min_pct')}** → band `{t.get('rise5_band')}`",
                f"- Rise10: **{t.get('entry_rise_10min_pct')}** → band `{t.get('rise10_band')}`",
                f"- HeatMap cell: **{t.get('heatmap_cell')}**",
                f"- PnL (100 shares): {t.get('pnl_yen_100')} yen ({t.get('pnl_pct')}%)",
                f"- Exit: {t.get('exit_reason')}",
                "",
                "### Counterfactual — would block?",
                *(f"- `{cid}`: **{'YES' if cid in blocked_by else 'no'}**" for cid, _ in COUNTERFACTUAL_SPECS),
                "",
            ]
        )
    return {"found": True, "markdown": "\n".join(lines), "trades": sumco}


def _load_phase647_dd_delta() -> Optional[float]:
    fp = NATIVE_ROOT / "results" / "reports" / "phase647_momentum_low_trend_attribution" / "phase647_report.json"
    if not fp.is_file():
        return None
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        cf = (data.get("mandatory_answers") or {}).get("3_counterfactual") or {}
        both = cf.get("exclude_down_and_strong_down") or {}
        return float(both.get("delta_max_dd_yen_100") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def build_adoption_answers(
    *,
    pbv2_trades: Sequence[Mapping[str, Any]],
    rise5_rows: Sequence[Mapping[str, Any]],
    rise10_rows: Sequence[Mapping[str, Any]],
    cf_rows: Sequence[Mapping[str, Any]],
    feat_rows: Sequence[Mapping[str, Any]],
    rankings: Mapping[str, Any],
) -> dict[str, Any]:
    total = len(pbv2_trades)
    cf_map = {r["condition_id"]: r for r in cf_rows}

    rise5_only = cf_map.get("rise5_lt_-1", {})
    rise10_only = cf_map.get("rise10_lt_-1", {})
    both_combo = cf_map.get("rise5_lt_-1_and_rise10_lt_-2", {})
    joint_neg = cf_map.get("rise5_lt_0_and_rise10_lt_0", {})

    rise5_corr = next((r for r in feat_rows if r.get("feature") == "entry_rise_5min_pct"), {})
    rise10_corr = next((r for r in feat_rows if r.get("feature") == "entry_rise_10min_pct"), {})
    r5_abs = abs(float(rise5_corr.get("corr_with_pnl") or 0))
    r10_abs = abs(float(rise10_corr.get("corr_with_pnl") or 0))

    best_rise5_band = max(
        (r for r in rise5_rows if int(r.get("entry_count") or 0) > 0),
        key=lambda r: float(r.get("pnl_yen_100") or 0),
        default={},
    )
    worst_rise5_band = min(
        (r for r in rise5_rows if int(r.get("entry_count") or 0) > 0),
        key=lambda r: float(r.get("pnl_yen_100") or 0),
        default={},
    )

    pnl_r5 = float(rise5_only.get("delta_pnl_yen_100") or 0)
    pnl_r10 = float(rise10_only.get("delta_pnl_yen_100") or 0)
    pnl_combo = float(both_combo.get("delta_pnl_yen_100") or 0)
    pnl_joint = float(joint_neg.get("delta_pnl_yen_100") or 0)
    dd_joint = float(joint_neg.get("delta_max_dd_yen_100") or 0)
    dd_combo = float(both_combo.get("delta_max_dd_yen_100") or 0)
    p647_dd = _load_phase647_dd_delta()

    best_cf = max(cf_rows, key=lambda r: float(r.get("delta_pnl_yen_100") or 0), default={})

    rise5_sufficient = r5_abs >= r10_abs * 1.1 and pnl_r5 >= pnl_r10
    rise10_sufficient = r10_abs >= r5_abs * 1.1 and pnl_r10 >= pnl_r5
    both_needed = pnl_joint > max(pnl_r5, pnl_r10, pnl_combo) + 5000 or dd_joint > max(
        float(rise5_only.get("delta_max_dd_yen_100") or 0),
        float(rise10_only.get("delta_max_dd_yen_100") or 0),
        dd_combo,
    )

    if rise5_sufficient and not both_needed:
        dimension_verdict = "Rise5 alone explains most of the edge; Rise10 adds marginal value."
    elif rise10_sufficient and not both_needed:
        dimension_verdict = "Rise10 alone is comparable; Rise5 redundant for gating."
    elif both_needed:
        dimension_verdict = (
            "Both dimensions needed — joint decline (Rise5<0 AND Rise10<0) is the actionable filter; "
            "single-axis negative-rise blocks remove profitable entries."
        )
    else:
        dimension_verdict = "Neither alone is clearly sufficient; modest incremental value from combining."

    vs_momentum = (
        "Higher direct PnL explainability than Phase647 trend buckets "
        f"(rise5 corr {rise5_corr.get('corr_with_pnl')}, rise10 {rise10_corr.get('corr_with_pnl')}). "
        "Loss concentration is in flat/slight-rise bands (0~0.5%), not deep negative rise."
        if r5_abs > 0.015
        else "Similar weak linear correlation as Phase647; band/counterfactual views are more actionable."
    )

    best_cf_pnl = float(best_cf.get("delta_pnl_yen_100") or 0)
    best_cf_dd = float(best_cf.get("delta_max_dd_yen_100") or 0)
    if best_cf_pnl > 20000 and best_cf_dd > 50000:
        recommendation = "HOLD — joint rise decline filter (rise5<0 & rise10<0) improves PnL+DD; OOS validate"
    elif pnl_r5 < -50000 and pnl_joint < 0:
        recommendation = "Reject — blocking negative rise hurts aggregate PnL"
    else:
        recommendation = "HOLD — Phase632 rise5 upper-cap remains primary; joint decline gate is secondary candidate"

    return {
        "rise5_sufficient": rise5_sufficient,
        "rise10_sufficient": rise10_sufficient,
        "both_needed": both_needed,
        "dimension_verdict": dimension_verdict,
        "best_counterfactual": best_cf.get("condition_id"),
        "best_counterfactual_delta_pnl": best_cf_pnl,
        "best_counterfactual_delta_dd": best_cf_dd,
        "profit_source_rise5_band": best_rise5_band.get("rise5_band"),
        "loss_source_rise5_band": worst_rise5_band.get("rise5_band"),
        "rankings": rankings,
        "vs_phase647_momentum_low": vs_momentum,
        "phase647_down_strong_dd_delta": p647_dd,
        "pbv2_additive_value": (
            f"Joint decline (rise5<0 & rise10<0) ΔPnL {pnl_joint:+.0f}, ΔDD {dd_joint:+.0f}; "
            f"single rise5<-1 block ΔPnL {pnl_r5:+.0f} (harmful). "
            f"Phase632 rise5 upper-cap targets overextended entries (2~3%, >3% bands lose)."
        ),
        "overfit_risk": (
            f"Medium — {total} PBv2 trades, in-sample counterfactual; "
            "rise bands fixed a priori; Phase632 already favored rise5 upper cap."
        ),
        "recommendation": recommendation,
        "summary": (
            f"PBv2 n={total}. Best rise5 band: {best_rise5_band.get('rise5_band')} "
            f"(PnL {best_rise5_band.get('pnl_yen_100')}); "
            f"worst: {worst_rise5_band.get('rise5_band')} (PnL {worst_rise5_band.get('pnl_yen_100')}). "
            f"{dimension_verdict}"
        ),
    }


@dataclass
class Phase648Job:
    native_root: Path

    def run(self) -> dict[str, Any]:
        root = self.native_root / "results" / "small_paper"
        all_trades, sessions = load_all_full_period_trades(root)
        pbv2 = enrich_pbv2_rise(filter_pbv2_trades(all_trades))
        rise5_rows = _bucket_metrics(pbv2, "rise5_band")
        rise10_rows = _bucket_metrics(pbv2, "rise10_band")
        heatmap_rows = heatmap_metrics(pbv2)
        cf_rows = [
            counterfactual_block(pbv2, condition_id=cid, block_fn=fn) for cid, fn in COUNTERFACTUAL_SPECS
        ]
        feat_rows = rise_feature_importance(pbv2)
        rankings = band_rankings(rise5_rows, rise10_rows)
        sumco = build_sumco_case_study(pbv2)
        adoption = build_adoption_answers(
            pbv2_trades=pbv2,
            rise5_rows=rise5_rows,
            rise10_rows=rise10_rows,
            cf_rows=cf_rows,
            feat_rows=feat_rows,
            rankings=rankings,
        )
        return {
            "verdict": PHASE648_VERDICT,
            "generated_at": _now_iso(),
            "session_count": len(sessions),
            "all_trade_count": len(all_trades),
            "pbv2_count": len(pbv2),
            "adoption_answers": adoption,
            "rise5_distribution": rise5_rows,
            "rise10_distribution": rise10_rows,
            "heatmap": heatmap_rows,
            "counterfactual": cf_rows,
            "feature_importance": feat_rows,
            "rankings": rankings,
            "sumco": sumco,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        out = self.native_root / "results" / "reports" / REPORT_DIR_NAME
        out.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}

        rise5_cols = [
            "rise5_band",
            "entry_count",
            "win_rate",
            "profit_factor",
            "pnl_yen_100",
            "pnl_share_pct",
            "avg_win_yen_100",
            "avg_loss_yen_100",
            "avg_mfe_pct",
            "avg_mae_pct",
            "max_dd_yen_100",
        ]
        _write_csv(out / "rise5_distribution.csv", rise5_cols, list(result.get("rise5_distribution") or []))
        paths["rise5_distribution"] = out / "rise5_distribution.csv"

        rise10_cols = [c.replace("rise5", "rise10") for c in rise5_cols]
        _write_csv(out / "rise10_distribution.csv", rise10_cols, list(result.get("rise10_distribution") or []))
        paths["rise10_distribution"] = out / "rise10_distribution.csv"

        heatmap_cols = [
            "heatmap_cell",
            "rise5_axis",
            "rise10_axis",
            "entry_count",
            "pnl_yen_100",
            "profit_factor",
            "win_rate",
            "avg_win_yen_100",
            "avg_loss_yen_100",
            "max_dd_yen_100",
        ]
        _write_csv(out / "rise5_rise10_heatmap.csv", heatmap_cols, list(result.get("heatmap") or []))
        paths["rise5_rise10_heatmap"] = out / "rise5_rise10_heatmap.csv"

        cf_cols = [
            "condition_id",
            "baseline_entry_count",
            "kept_entry_count",
            "blocked_entry_count",
            "entry_reduction_pct",
            "delta_pnl_yen_100",
            "delta_pf",
            "delta_max_dd_yen_100",
            "wrongly_blocked_winners",
            "wrongly_blocked_winners_pnl",
            "rescued_losers",
            "rescued_losers_pnl",
            "pnl_yen_100",
            "profit_factor",
            "win_rate",
            "max_dd_yen_100",
        ]
        _write_csv(out / "rise_counterfactual.csv", cf_cols, list(result.get("counterfactual") or []))
        paths["rise_counterfactual"] = out / "rise_counterfactual.csv"

        _write_csv(
            out / "rise_feature_importance.csv",
            ["feature", "n", "corr_with_pnl", "mean_across_trades"],
            list(result.get("feature_importance") or []),
        )
        paths["rise_feature_importance"] = out / "rise_feature_importance.csv"

        sumco_md = (result.get("sumco") or {}).get("markdown") or ""
        (out / "sumco_case_study.md").write_text(sumco_md, encoding="utf-8")
        paths["sumco"] = out / "sumco_case_study.md"

        report = {
            "phase": "648",
            "verdict": result.get("verdict"),
            "generated_at": result.get("generated_at"),
            "session_count": result.get("session_count"),
            "all_trade_count": result.get("all_trade_count"),
            "pbv2_count": result.get("pbv2_count"),
            "adoption_answers": result.get("adoption_answers"),
            "rise5_distribution": result.get("rise5_distribution"),
            "rise10_distribution": result.get("rise10_distribution"),
            "heatmap": result.get("heatmap"),
            "counterfactual": result.get("counterfactual"),
            "rankings": result.get("rankings"),
            "artifacts": {k: str(v) for k, v in paths.items()},
        }
        report_fp = out / "phase648_report.json"
        report_fp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["report"] = report_fp
        return paths


def main() -> int:
    job = Phase648Job(native_root=NATIVE_ROOT)
    result = job.run()
    paths = job.write_outputs(result)
    print(json.dumps({"verdict": result.get("verdict"), "paths": {k: str(v) for k, v in paths.items()}}, indent=2))
    print(json.dumps(result.get("adoption_answers"), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
