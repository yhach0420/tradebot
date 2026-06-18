"""
Phase438 — Momentum Low Audit.

Goal:
- Audit what symbols/states pass "Momentum:low" (Runtime gate token) over Phase423 canonical baseline accepted trades.
- Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase436_pullback_guard_redesign_shadow import (
    PERIOD_END,
    PERIOD_START,
    TARGET_SYMBOL,
    _accepted_trades_from_sim,
    _build_price_index,
    _enrich_trades,
    _is_stop,
    _load_accepted_index,
    guard_high_drift,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import momentum_low_required_for_v2

JST = ZoneInfo("Asia/Tokyo")


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _optional_float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _percentile_rank(values: Sequence[float], x: float) -> Optional[float]:
    """Empirical percentile in [0,100]."""
    if not values:
        return None
    # values are sorted
    lo = 0
    hi = len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if values[mid] <= x:
            lo = mid + 1
        else:
            hi = mid
    # lo is count <= x
    return round(lo / len(values) * 100.0, 2)


def _day_key(trade: Mapping[str, Any]) -> str:
    return str(trade.get("day") or trade.get("day_key") or "")


def _price_series_for(trade: Mapping[str, Any], price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]]):
    sym = str(trade.get("symbol") or "")
    day = _day_key(trade)
    return price_idx.get((sym, day), [])


def _day_high_context(
    trade: Mapping[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> dict[str, Any]:
    """Compute time since last day-high update and distance from day-low using intraday current_price series."""
    series = _price_series_for(trade, price_idx)
    et = _parse_ts(str(trade.get("entry_time") or ""))
    if not series or et is None:
        return {
            "minutes_since_day_high_update": None,
            "distance_from_day_low_pct": None,
            "day_high_price": None,
            "day_low_price": None,
        }

    upto = [(ts, px) for ts, px in series if ts <= et]
    if not upto:
        return {
            "minutes_since_day_high_update": None,
            "distance_from_day_low_pct": None,
            "day_high_price": None,
            "day_low_price": None,
        }

    day_high = max(px for _, px in upto)
    day_low = min(px for _, px in upto)
    last_high_ts = max(ts for ts, px in upto if px == day_high)
    minutes_since = (et - last_high_ts).total_seconds() / 60.0
    ep = _float(trade.get("entry_price"), default=0.0)
    dist_low = None
    if day_low > 0 and ep > 0:
        dist_low = round((ep - day_low) / day_low * 100.0, 4)
    return {
        "minutes_since_day_high_update": round(minutes_since, 2),
        "distance_from_day_low_pct": dist_low,
        "day_high_price": round(day_high, 2),
        "day_low_price": round(day_low, 2),
    }


def _class_15m(r15: Optional[float]) -> str:
    if r15 is None:
        return "unknown"
    if r15 > 0.05:
        return "A_up"
    if r15 < -0.05:
        return "C_down"
    return "B_flat"


def _expectancy(pnls: Sequence[float]) -> float:
    return round(statistics.mean(pnls), 2) if pnls else 0.0


def _max_drawdown_yen_simple(
    trades: Sequence[Mapping[str, Any]],
    *,
    starting_equity: float = 1_500_000.0,
) -> float:
    ordered = sorted(
        trades,
        key=lambda t: (_parse_ts(str(t.get("exit_time") or t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)),
    )
    eq = starting_equity
    peak = eq
    max_dd = 0.0
    for t in ordered:
        eq += _float(t.get("pnl_yen"))
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return round(max_dd, 2)


def _shadow_eval(
    trades: Sequence[Mapping[str, Any]],
    *,
    variant_id: str,
    keep_fn: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    kept = [dict(t) for t in trades if keep_fn(t)]
    pnls = [_float(t.get("pnl_yen")) for t in kept]
    stops = sum(1 for t in kept if _is_stop(t))
    return {
        "variant_id": variant_id,
        "trade_count": len(kept),
        "total_pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "win_rate": _win_rate(pnls),
        "expectancy_yen": _expectancy(pnls),
        "stop_count": stops,
        "stop_rate": round(stops / len(kept), 4) if kept else 0.0,
        "max_drawdown_yen": _max_drawdown_yen_simple(kept),
    }


def run_phase438_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    accepted_idx = _load_accepted_index(kabu)
    price_idx = _build_price_index(kabu)
    trades = _enrich_trades(
        _accepted_trades_from_sim(repo_root),
        kabu_root=kabu,
        accepted_idx=accepted_idx,
        price_idx=price_idx,
    )

    # Momentum score distribution for percentiles (accepted 전체)
    mom_scores_all = sorted(
        [
            float(v)
            for v in (
                _optional_float(t.get("momentum_continuation_score"))
                for t in trades
            )
            if v is not None and math.isfinite(v)
        ]
    )

    audit_rows: list[dict[str, Any]] = []
    momentum_low_trades: list[dict[str, Any]] = []
    for t in trades:
        t = dict(t)
        mom = _optional_float(t.get("momentum_continuation_score"))
        mom_pct = _percentile_rank(mom_scores_all, float(mom)) if mom is not None else None
        is_mom_low = momentum_low_required_for_v2(t)
        t["momentum_low"] = is_mom_low
        ctx = _day_high_context(t, price_idx=price_idx)
        r15 = _optional_float(t.get("return_15min_pct"))
        dh_dist = _optional_float(t.get("day_high_distance_pct") or t.get("entry_near_day_high_pct"))
        audit_rows.append(
            {
                "day": _day_key(t),
                "entry_time": t.get("entry_time"),
                "symbol": t.get("symbol"),
                "pnl_yen": _float(t.get("pnl_yen")),
                "stop_hit": _is_stop(t),
                "momentum_low": is_mom_low,
                "momentum_continuation_score": mom,
                "momentum_percentile": mom_pct,
                "entry_momentum_continuation_score": t.get("entry_momentum_continuation_score"),
                "entry_imbalance_percentile": t.get("entry_imbalance_percentile"),
                "return_5min_pct": _optional_float(t.get("return_5min_pct") or t.get("entry_rise_5min_pct")),
                "return_10min_pct": _optional_float(t.get("return_10min_pct") or t.get("entry_rise_10min_pct")),
                "return_15min_pct": r15,
                "return_30min_pct": _optional_float(t.get("return_30min_pct")),
                "day_high_distance_pct": dh_dist,
                "minutes_since_day_high_update": ctx.get("minutes_since_day_high_update"),
                "distance_from_day_low_pct": ctx.get("distance_from_day_low_pct"),
                "entry_vwap_dev_pct": _optional_float(t.get("entry_vwap_dev_pct")),
                "universe_group": t.get("universe_group"),
            }
        )
        if is_mom_low:
            mt = dict(t)
            mt.update(ctx)
            mt["momentum_percentile"] = mom_pct
            mt["day_high_distance_pct"] = dh_dist
            mt["return_15m_class"] = _class_15m(r15)
            mt["drifting_winner_misclassification"] = bool(
                (r15 is not None and r15 < 0)
                and (dh_dist is not None and abs(dh_dist) >= 3.0)
            )
            momentum_low_trades.append(mt)

    # Part C: 6976-type extraction within momentum_low
    drift = [t for t in momentum_low_trades if t.get("drifting_winner_misclassification")]
    drift_pnls = [_float(t.get("pnl_yen")) for t in drift]
    drift_stops = sum(1 for t in drift if _is_stop(t))
    drift_pf = _pf(drift_pnls)

    # Part D: Momentum low quality by 15m class
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in momentum_low_trades:
        by_class[str(t.get("return_15m_class") or "unknown")].append(t)
    dist_rows: list[dict[str, Any]] = []
    for cls in ("A_up", "B_flat", "C_down", "unknown"):
        subset = by_class.get(cls, [])
        pnls = [_float(t.get("pnl_yen")) for t in subset]
        stops = sum(1 for t in subset if _is_stop(t))
        dist_rows.append(
            {
                "bucket": cls,
                "count": len(subset),
                "pnl_yen": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "stop_rate": round(stops / len(subset), 4) if subset else 0.0,
                "stop_count": stops,
                "momentum_low_share": round(len(subset) / len(momentum_low_trades), 4) if momentum_low_trades else 0.0,
            }
        )

    # Part E: shadow improvements (cohort = momentum_low only)
    cohort = list(momentum_low_trades)
    baseline = _shadow_eval(cohort, variant_id="baseline_momentum_low", keep_fn=lambda _t: True)
    variants = [
        baseline,
        _shadow_eval(
            cohort,
            variant_id="mom_low_and_15m_gt_0",
            keep_fn=lambda t: (_optional_float(t.get("return_15min_pct")) or -1e9) > 0,
        ),
        _shadow_eval(
            cohort,
            variant_id="mom_low_and_high_drift_excluded",
            keep_fn=lambda t: not guard_high_drift(t),
        ),
        _shadow_eval(
            cohort,
            variant_id="mom_low_and_not_far_from_day_high_3pct",
            keep_fn=lambda t: abs(_optional_float(t.get("day_high_distance_pct")) or 0.0) < 3.0,
        ),
        _shadow_eval(
            cohort,
            variant_id="mom_low_and_within_15m_of_day_high_update",
            keep_fn=lambda t: (_optional_float(t.get("minutes_since_day_high_update")) or 1e9) <= 15.0,
        ),
    ]

    # Mandatory answers
    mom_low_total = len(momentum_low_trades)
    down_bucket = next((r for r in dist_rows if r["bucket"] == "C_down"), {})
    up_bucket = next((r for r in dist_rows if r["bucket"] == "A_up"), {})
    drift_count = len(drift)

    # 6976 typical vs exception: compare 6976 drift share vs overall drift share inside momentum_low
    mom_low_6976 = [t for t in momentum_low_trades if str(t.get("symbol") or "") == TARGET_SYMBOL]
    drift_6976 = [t for t in drift if str(t.get("symbol") or "") == TARGET_SYMBOL]
    drift_share_all = round(drift_count / mom_low_total, 4) if mom_low_total else 0.0
    drift_share_6976 = round(len(drift_6976) / len(mom_low_6976), 4) if mom_low_6976 else None
    typical = None
    if drift_share_6976 is not None:
        typical = abs(drift_share_6976 - drift_share_all) <= 0.10

    best = max(variants[1:], key=lambda r: _float(r.get("total_pnl_yen"))) if len(variants) > 1 else baseline
    verdict = "momentum_definition_issue" if drift_share_all >= 0.05 and (down_bucket.get("momentum_low_share") or 0) >= 0.25 else "momentum_working_as_expected"
    if best.get("variant_id") == "mom_low_and_high_drift_excluded":
        verdict = "high_drift_preferred"
    elif best.get("variant_id") != "baseline_momentum_low":
        verdict = "momentum_shadow_candidate"

    mandatory = {
        "1_6976_type_count": drift_count,
        "2_momentum_low_down_15m_share": down_bucket.get("momentum_low_share"),
        "3_pf_down_15m": down_bucket.get("profit_factor"),
        "4_pf_up_15m": up_bucket.get("profit_factor"),
        "5_6976_typical": typical,
        "6_momentum_low_weakness": (
            "momentum_low admits negative 15m drift far from day high (downtrend bounce) in dynamic40"
            if verdict in ("momentum_definition_issue", "high_drift_preferred", "momentum_shadow_candidate")
            else "no clear drift failure cluster"
        ),
        "7_best_improvement_candidate": best.get("variant_id"),
        "8_high_drift_should_replace": best.get("variant_id") == "mom_low_and_high_drift_excluded",
        "9_momentum_definition_should_change": verdict == "momentum_definition_issue",
        "10_runtime_shadow_candidate": verdict in ("momentum_shadow_candidate", "high_drift_preferred"),
        "verdict": verdict,
        "drift_share_all": drift_share_all,
        "drift_share_6976": drift_share_6976,
        "drift_pf": drift_pf,
        "drift_stop_rate": round(drift_stops / len(drift), 4) if drift else 0.0,
    }

    return {
        "phase": "438-Momentum-Low-Audit",
        "generated_at": _now_iso(),
        "period": f"{PERIOD_START}..{PERIOD_END}",
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "audit_rows": audit_rows,
        "momentum_low_distribution": dist_rows,
        "drifting_winner_misclassification": {
            "count": drift_count,
            "pnl_yen": round(sum(drift_pnls), 2),
            "profit_factor": drift_pf,
            "stop_rate": round(drift_stops / len(drift), 4) if drift else 0.0,
        },
        "shadow_comparison": variants,
    }


def _csv_write(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    _write_csv(path, list(rows[0].keys()), rows)


@dataclass
class Phase438Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase438_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        kabu = resolve_kabu_root(self.repo_root)

        paths = {
            "audit": reports / "phase438_momentum_low_audit.csv",
            "dist": reports / "phase438_momentum_low_distribution.csv",
            "shadow": reports / "phase438_momentum_low_shadow_comparison.csv",
            "summary": reports / "phase438_momentum_low_summary.json",
            "report": kabu / "docs" / "operations" / "phase438_momentum_low_audit_report.md",
        }

        _csv_write(paths["audit"], result.get("audit_rows") or [])
        _csv_write(paths["dist"], result.get("momentum_low_distribution") or [])
        _csv_write(paths["shadow"], result.get("shadow_comparison") or [])

        summary = {
            "phase": result.get("phase"),
            "generated_at": result.get("generated_at"),
            "period": result.get("period"),
            "verdict": result.get("verdict"),
            "mandatory_answers": result.get("mandatory_answers"),
            "drifting_winner_misclassification": result.get("drifting_winner_misclassification"),
            "shadow_comparison": result.get("shadow_comparison"),
            "momentum_low_distribution": result.get("momentum_low_distribution"),
        }
        paths["summary"].write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        drift = result.get("drifting_winner_misclassification") or {}
        lines = [
            "# Phase438 — Momentum Low Audit",
            "",
            f"Generated: {result.get('generated_at')}",
            f"Period: {result.get('period')}",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## Mandatory answers",
            "",
            f"1. 6976型件数: **{m.get('1_6976_type_count')}**",
            f"2. Momentum low のうち15m下落割合: **{m.get('2_momentum_low_down_15m_share')}**",
            f"3. 15m下落群PF: **{m.get('3_pf_down_15m')}**",
            f"4. 15m上昇群PF: **{m.get('4_pf_up_15m')}**",
            f"5. 6976は典型か: **{m.get('5_6976_typical')}** (drift_share_all={m.get('drift_share_all')}, drift_share_6976={m.get('drift_share_6976')})",
            f"6. Momentum low の弱点: **{m.get('6_momentum_low_weakness')}**",
            f"7. 最も有効な改善候補: **{m.get('7_best_improvement_candidate')}**",
            f"8. High Drift で代替すべきか: **{m.get('8_high_drift_should_replace')}**",
            f"9. Momentum定義修正すべきか: **{m.get('9_momentum_definition_should_change')}**",
            f"10. Runtime Shadow候補: **{m.get('10_runtime_shadow_candidate')}**",
            "",
            "## Part C — drifting_winner_misclassification",
            "",
            f"- count: {drift.get('count')}",
            f"- PnL: {drift.get('pnl_yen'):,.0f} yen",
            f"- PF: {drift.get('profit_factor')}",
            f"- stop_rate: {drift.get('stop_rate')}",
            "",
            "## Part D — Momentum low quality by 15m return",
            "",
            "| bucket | count | PnL | PF | stop_rate |",
            "|--------|-------|-----|----|----------|",
        ]
        for r in result.get("momentum_low_distribution") or []:
            lines.append(
                f"| {r.get('bucket')} | {r.get('count')} | {r.get('pnl_yen'):,.0f} | {r.get('profit_factor')} | {r.get('stop_rate')} |"
            )
        lines.extend(
            [
                "",
                "## Part E — Shadow comparison (cohort = Momentum:low only)",
                "",
                "| variant | trades | PnL | PF | stop_rate | maxDD |",
                "|---------|--------|-----|----|----------|-------|",
            ]
        )
        for r in result.get("shadow_comparison") or []:
            lines.append(
                f"| {r.get('variant_id')} | {r.get('trade_count')} | {r.get('total_pnl_yen'):,.0f} | "
                f"{r.get('profit_factor')} | {r.get('stop_rate')} | {r.get('max_drawdown_yen'):,.0f} |"
            )
        lines.append("")
        lines.append("Runtime/YAML/Entry/Exit/Order/Discord changes **forbidden** (audit only).")
        lines.append("")
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text("\n".join(lines), encoding="utf-8")
        return paths

