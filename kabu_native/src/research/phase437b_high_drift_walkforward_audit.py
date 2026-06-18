"""
Phase437B — High Drift robustness walk-forward audit.

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase436_pullback_guard_redesign_shadow import (
    PERIOD_END,
    PERIOD_START,
    STARTING_EQUITY,
    TARGET_SYMBOL,
    _accepted_trades_from_sim,
    _build_price_index,
    _enrich_trades,
    _is_stop,
    _load_accepted_index,
    _max_drawdown_yen,
    _optional_float,
    _scope_dynamic40,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

# Phase436 canonical thresholds (do not change for main evaluation)
DEFAULT_DAY_HIGH_A = 1.2
DEFAULT_DAY_HIGH_B = 1.5
DEFAULT_R10 = -0.15
DEFAULT_R15 = -0.5
DEFAULT_R5_B = -0.5


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def make_high_drift_guard(
    *,
    day_high_a: float = DEFAULT_DAY_HIGH_A,
    day_high_b: float = DEFAULT_DAY_HIGH_B,
    r10_thresh: float = DEFAULT_R10,
    r15_thresh: float = DEFAULT_R15,
    r5_b_thresh: float = DEFAULT_R5_B,
) -> Callable[[Mapping[str, Any]], bool]:
    """Phase436 High Drift with optional threshold overrides (sensitivity only)."""

    def _guard(trade: Mapping[str, Any]) -> bool:
        if not _scope_dynamic40(trade):
            return False
        dist = abs(
            _optional_float(trade.get("day_high_distance_pct"))
            or _optional_float(trade.get("entry_near_day_high_pct"))
            or 0.0
        )
        r5 = _optional_float(trade.get("return_5min_pct") or trade.get("entry_rise_5min_pct"))
        r10 = _optional_float(trade.get("return_10min_pct") or trade.get("entry_rise_10min_pct"))
        r15 = _optional_float(trade.get("return_15min_pct"))
        if dist < day_high_a:
            return False
        if r10 is not None and r10 < r10_thresh:
            if r5 is None:
                return True
            if r5 > r10 and r5 <= 1.0:
                return True
        if dist >= day_high_b:
            if r15 is not None and r15 < r15_thresh and (r5 is None or r5 < 0.2):
                return True
            if r5 is not None and r5 < r5_b_thresh and (r10 is None or r10 < -0.2):
                return True
        return False

    return _guard


def _split_kept(
    trades: Sequence[Mapping[str, Any]],
    block_fn: Callable[[Mapping[str, Any]], bool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        if block_fn(row):
            removed.append(row)
        else:
            kept.append(row)
    return kept, removed


def _metrics_subset(
    trades: Sequence[Mapping[str, Any]],
    *,
    block_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None,
) -> dict[str, Any]:
    if block_fn is None:
        subset = list(trades)
    else:
        subset, _ = _split_kept(trades, block_fn)
    pnls = [_float(t.get("pnl_yen")) for t in subset]
    stops = sum(1 for t in subset if _is_stop(t))
    max_dd, _ = _max_drawdown_yen(subset) if subset else (0.0, 0.0)
    return {
        "trade_count": len(subset),
        "total_pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "stop_count": stops,
        "stop_rate": round(stops / len(subset), 4) if subset else 0.0,
        "max_drawdown_yen": max_dd,
    }


def _trading_days(trades: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted({str(t.get("day") or "") for t in trades if t.get("day")})


def _trades_on_day(trades: Sequence[Mapping[str, Any]], day: str) -> list[dict[str, Any]]:
    return [dict(t) for t in trades if str(t.get("day") or "") == day]


def _walkforward_case(
    trades: Sequence[Mapping[str, Any]],
    *,
    case_id: str,
    train_start: str,
    train_end: str,
    test_day: str,
    block_fn: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    test_trades = _trades_on_day(trades, test_day)
    base = _metrics_subset(test_trades)
    hd = _metrics_subset(test_trades, block_fn=block_fn)
    b_pf = base.get("profit_factor")
    h_pf = hd.get("profit_factor")
    delta_pf = None
    if b_pf is not None and h_pf is not None and b_pf != float("inf") and h_pf != float("inf"):
        delta_pf = round(float(h_pf) - float(b_pf), 4)
    delta_stop = hd["stop_count"] - base["stop_count"]
    return {
        "case_id": case_id,
        "train_start": train_start,
        "train_end": train_end,
        "test_day": test_day,
        "baseline_trade_count": base["trade_count"],
        "baseline_pnl_yen": base["total_pnl_yen"],
        "baseline_pf": base["profit_factor"],
        "baseline_stop_rate": base["stop_rate"],
        "baseline_max_drawdown_yen": base["max_drawdown_yen"],
        "high_drift_trade_count": hd["trade_count"],
        "high_drift_pnl_yen": hd["total_pnl_yen"],
        "high_drift_pf": hd["profit_factor"],
        "high_drift_stop_rate": hd["stop_rate"],
        "high_drift_max_drawdown_yen": hd["max_drawdown_yen"],
        "delta_pnl_yen": round(hd["total_pnl_yen"] - base["total_pnl_yen"], 2),
        "delta_pf": delta_pf,
        "delta_stop_count": delta_stop,
        "test_improved": hd["total_pnl_yen"] > base["total_pnl_yen"],
    }


def _leave_one_day_out(
    trades: Sequence[Mapping[str, Any]],
    *,
    block_fn: Callable[[Mapping[str, Any]], bool],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in _trading_days(trades):
        day_trades = _trades_on_day(trades, day)
        base = _metrics_subset(day_trades)
        hd = _metrics_subset(day_trades, block_fn=block_fn)
        b_pf = base.get("profit_factor")
        h_pf = hd.get("profit_factor")
        delta_pf = None
        if b_pf is not None and h_pf is not None and b_pf != float("inf") and h_pf != float("inf"):
            delta_pf = round(float(h_pf) - float(b_pf), 4)
        rows.append(
            {
                "excluded_day": day,
                "train_days": len(_trading_days(trades)) - 1,
                "test_day": day,
                "baseline_pnl_yen": base["total_pnl_yen"],
                "guard_pnl_yen": hd["total_pnl_yen"],
                "delta_pnl_yen": round(hd["total_pnl_yen"] - base["total_pnl_yen"], 2),
                "delta_pf": delta_pf,
                "delta_stop_count": hd["stop_count"] - base["stop_count"],
            }
        )
    return rows


def _sensitivity_grid(
    trades: Sequence[Mapping[str, Any]],
    *,
    baseline_block: Callable[[Mapping[str, Any]], bool],
) -> list[dict[str, Any]]:
    base_kept, _ = _split_kept(trades, baseline_block)
    base_pnl = sum(_float(t.get("pnl_yen")) for t in base_kept)
    base_pnls = [_float(t.get("pnl_yen")) for t in base_kept]
    base_pf = _pf(base_pnls)

    rows: list[dict[str, Any]] = [
        {
            "sweep_param": "canonical",
            "param_value": "phase436",
            "day_high_a": DEFAULT_DAY_HIGH_A,
            "day_high_b": DEFAULT_DAY_HIGH_B,
            "r10_thresh": DEFAULT_R10,
            "r15_thresh": DEFAULT_R15,
            "trade_count": len(base_kept),
            "total_pnl_yen": round(base_pnl, 2),
            "profit_factor": base_pf,
            "delta_pnl_yen": 0.0,
            "delta_pf": 0.0,
            "removed_count": len(trades) - len(base_kept),
        }
    ]

    def _row(sweep_param: str, param_value: float, block_fn: Callable) -> dict[str, Any]:
        kept, removed = _split_kept(trades, block_fn)
        pnls = [_float(t.get("pnl_yen")) for t in kept]
        pnl = round(sum(pnls), 2)
        pf = _pf(pnls)
        d_pf = None
        if pf is not None and base_pf is not None and pf != float("inf") and base_pf != float("inf"):
            d_pf = round(float(pf) - float(base_pf), 4)
        kwargs: dict[str, float] = {
            "day_high_a": DEFAULT_DAY_HIGH_A,
            "day_high_b": DEFAULT_DAY_HIGH_B,
            "r10_thresh": DEFAULT_R10,
            "r15_thresh": DEFAULT_R15,
        }
        if sweep_param == "day_high_a":
            kwargs["day_high_a"] = param_value
        elif sweep_param == "day_high_b":
            kwargs["day_high_b"] = param_value
        elif sweep_param == "r10_thresh":
            kwargs["r10_thresh"] = param_value
        elif sweep_param == "r15_thresh":
            kwargs["r15_thresh"] = param_value
        return {
            "sweep_param": sweep_param,
            "param_value": param_value,
            **kwargs,
            "trade_count": len(kept),
            "total_pnl_yen": pnl,
            "profit_factor": pf,
            "delta_pnl_yen": round(pnl - base_pnl, 2),
            "delta_pf": d_pf,
            "removed_count": len(removed),
        }

    for v in (1.0, 1.2, 1.5, 2.0):
        if v != DEFAULT_DAY_HIGH_A:
            rows.append(_row("day_high_a", v, make_high_drift_guard(day_high_a=v)))
    for v in (1.0, 1.5, 2.0):
        if v != DEFAULT_DAY_HIGH_B:
            rows.append(_row("day_high_b", v, make_high_drift_guard(day_high_b=v)))
    for v in (-0.10, -0.20):
        rows.append(_row("r10_thresh", v, make_high_drift_guard(r10_thresh=v)))
    for v in (-0.40, -0.60):
        rows.append(_row("r15_thresh", v, make_high_drift_guard(r15_thresh=v)))

    return rows


def _symbol_improvement_contribution(
    trades: Sequence[Mapping[str, Any]],
    *,
    block_fn: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    _, removed = _split_kept(trades, block_fn)
    by_sym: dict[str, float] = defaultdict(float)
    for t in removed:
        sym = str(t.get("symbol") or "")
        by_sym[sym] += _float(t.get("pnl_yen"))

    total_delta = round(-sum(by_sym.values()), 2)
    sym_improve = {s: round(-pnl, 2) for s, pnl in by_sym.items()}
    ranked = sorted(sym_improve.items(), key=lambda kv: kv[1], reverse=True)
    positive_ranked = sorted(
        [(s, v) for s, v in sym_improve.items() if v > 0],
        key=lambda kv: kv[1],
        reverse=True,
    )
    sym6976 = sym_improve.get(TARGET_SYMBOL, 0.0)
    rate6976 = round(sym6976 / total_delta, 4) if abs(total_delta) > 1e-6 else None
    gross_positive = sum(v for v in sym_improve.values() if v > 0)
    gross_negative = sum(v for v in sym_improve.values() if v < 0)

    def _share_positive(n: int) -> Optional[float]:
        if gross_positive <= 1e-6:
            return None
        top = sum(v for _, v in positive_ranked[:n])
        return round(top / gross_positive, 4)

    def _share_of_net_delta(n: int) -> Optional[float]:
        if total_delta <= 1e-6:
            return None
        top = sum(v for _, v in positive_ranked[:n])
        return round(top / total_delta, 4)

    return {
        "total_improvement_yen": total_delta,
        "symbol_6976_improvement_yen": sym6976,
        "symbol_6976_contribution_rate": rate6976,
        "top3_symbols": [s for s, _ in positive_ranked[:3]],
        "top3_contribution_rate": _share_of_net_delta(3),
        "top3_contribution_rate_gross_positive": _share_positive(3),
        "top5_contribution_rate": _share_of_net_delta(5),
        "top5_contribution_rate_gross_positive": _share_positive(5),
        "gross_positive_improvement_yen": round(gross_positive, 2),
        "winner_removal_offset_yen": round(gross_negative, 2),
        "ranked_symbol_improvement": [{"symbol": s, "improvement_yen": v} for s, v in ranked],
    }


def _daily_improvement_distribution(
    trades: Sequence[Mapping[str, Any]],
    *,
    block_fn: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for day in _trading_days(trades):
        day_trades = _trades_on_day(trades, day)
        base = _metrics_subset(day_trades)
        hd = _metrics_subset(day_trades, block_fn=block_fn)
        b_pf = base.get("profit_factor")
        h_pf = hd.get("profit_factor")
        delta_pf = None
        if b_pf is not None and h_pf is not None and b_pf != float("inf") and h_pf != float("inf"):
            delta_pf = round(float(h_pf) - float(b_pf), 4)
        rows.append(
            {
                "day": day,
                "delta_pnl_yen": round(hd["total_pnl_yen"] - base["total_pnl_yen"], 2),
                "delta_pf": delta_pf,
                "delta_stop_rate": round(hd["stop_rate"] - base["stop_rate"], 4),
            }
        )

    deltas = [r["delta_pnl_yen"] for r in rows]
    improved = [r for r in rows if r["delta_pnl_yen"] > 0]
    worsened = [r for r in rows if r["delta_pnl_yen"] < 0]
    flat = [r for r in rows if r["delta_pnl_yen"] == 0]
    max_imp = max(rows, key=lambda r: r["delta_pnl_yen"]) if rows else {}
    max_worse = min(rows, key=lambda r: r["delta_pnl_yen"]) if rows else {}

    return {
        "daily_rows": rows,
        "improved_day_count": len(improved),
        "worsened_day_count": len(worsened),
        "flat_day_count": len(flat),
        "max_improvement_day": max_imp.get("day"),
        "max_improvement_delta_yen": max_imp.get("delta_pnl_yen"),
        "max_worsened_day": max_worse.get("day"),
        "max_worsened_delta_yen": max_worse.get("delta_pnl_yen"),
        "median_delta_pnl_yen": round(statistics.median(deltas), 2) if deltas else 0.0,
        "mean_delta_pnl_yen": round(statistics.mean(deltas), 2) if deltas else 0.0,
    }


def _verdict(
    *,
    wf_cases: Sequence[Mapping[str, Any]],
    loo_rows: Sequence[Mapping[str, Any]],
    sym_contrib: Mapping[str, Any],
    daily_dist: Mapping[str, Any],
    sensitivity: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    case617 = next((c for c in wf_cases if c.get("test_day") == "20260617"), {})
    case618 = next((c for c in wf_cases if c.get("test_day") == "20260618"), {})
    imp617 = bool(case617.get("test_improved"))
    imp618 = bool(case618.get("test_improved"))

    loo_deltas = [_float(r.get("delta_pnl_yen")) for r in loo_rows]
    loo_mean = round(statistics.mean(loo_deltas), 2) if loo_deltas else 0.0

    rate6976 = _float(sym_contrib.get("symbol_6976_contribution_rate"))
    top3_rate = _float(sym_contrib.get("top3_contribution_rate_gross_positive"))
    top_day_share = None
    total_delta = _float(sym_contrib.get("total_improvement_yen"))
    if abs(total_delta) > 1e-6:
        day_deltas = {str(r["day"]): _float(r["delta_pnl_yen"]) for r in daily_dist.get("daily_rows") or []}
        if day_deltas:
            top_day = max(day_deltas.items(), key=lambda kv: abs(kv[1]))
            top_day_share = round(abs(top_day[1]) / abs(total_delta), 4)

    improved_days = int(daily_dist.get("improved_day_count") or 0)
    worsened_days = int(daily_dist.get("worsened_day_count") or 0)

    # Sensitivity: canonical must be best or near-best among sweeps
    canonical = next((r for r in sensitivity if r.get("sweep_param") == "canonical"), {})
    sweep_deltas = [
        _float(r.get("delta_pnl_yen"))
        for r in sensitivity
        if r.get("sweep_param") != "canonical"
    ]
    canon_delta = _float(canonical.get("delta_pnl_yen"))
    sensitivity_fragile = canon_delta > 0 and max(sweep_deltas, default=0) > canon_delta * 1.5

    mandatory = {
        "1_20260617_improved": imp617,
        "2_20260618_improved": imp618,
        "3_leave_one_day_out_mean_delta_yen": loo_mean,
        "4_improved_day_count": improved_days,
        "5_worsened_day_count": worsened_days,
        "6_max_worsened_day": daily_dist.get("max_worsened_day"),
        "7_symbol_6976_contribution_rate": rate6976,
        "8_top3_symbol_contribution_rate": sym_contrib.get("top3_contribution_rate_gross_positive"),
        "8b_top3_net_delta_ratio": sym_contrib.get("top3_contribution_rate"),
        "9_overfit_judgment": (
            "threshold_sensitive"
            if sensitivity_fragile
            else ("high_concentration" if (rate6976 > 0.5 or (top_day_share or 0) > 0.5) else "moderate")
        ),
        "10_runtime_shadow_recommended": False,
        "case617_delta_pnl_yen": case617.get("delta_pnl_yen"),
        "case618_delta_pnl_yen": case618.get("delta_pnl_yen"),
        "top_day_share_of_improvement": top_day_share,
    }

    if rate6976 > 0.6 and (top_day_share or 0) > 0.7:
        verdict = "6976_dependent"
    elif (top_day_share or 0) > 0.8:
        verdict = "single_day_dependent"
    elif sensitivity_fragile:
        verdict = "overfit_risk"
    elif imp617 and loo_mean > 0 and rate6976 < 0.5 and improved_days >= worsened_days:
        verdict = "robust_candidate"
    elif imp617 and loo_mean > 0 and (rate6976 < 0.75 or top3_rate < 0.85):
        verdict = "runtime_shadow_ready"
        mandatory["10_runtime_shadow_recommended"] = True
    elif rate6976 > 0.5:
        verdict = "6976_dependent"
    else:
        verdict = "runtime_shadow_ready" if loo_mean > 0 and imp617 else "overfit_risk"
        mandatory["10_runtime_shadow_recommended"] = verdict == "runtime_shadow_ready"

    mandatory["verdict"] = verdict
    return verdict, mandatory


def run_phase437b_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    trades = _enrich_trades(
        _accepted_trades_from_sim(repo_root),
        kabu_root=kabu,
        accepted_idx=_load_accepted_index(kabu),
        price_idx=_build_price_index(kabu),
    )
    block_fn = make_high_drift_guard()

    wf_rows = [
        _walkforward_case(
            trades,
            case_id="case1",
            train_start=PERIOD_START,
            train_end="20260616",
            test_day="20260617",
            block_fn=block_fn,
        ),
        _walkforward_case(
            trades,
            case_id="case2",
            train_start=PERIOD_START,
            train_end="20260617",
            test_day="20260618",
            block_fn=block_fn,
        ),
    ]

    loo_rows = _leave_one_day_out(trades, block_fn=block_fn)
    sensitivity = _sensitivity_grid(trades, baseline_block=block_fn)
    sym_contrib = _symbol_improvement_contribution(trades, block_fn=block_fn)
    daily_dist = _daily_improvement_distribution(trades, block_fn=block_fn)

    verdict, mandatory = _verdict(
        wf_cases=wf_rows,
        loo_rows=loo_rows,
        sym_contrib=sym_contrib,
        daily_dist=daily_dist,
        sensitivity=sensitivity,
    )

    full_base = _metrics_subset(trades)
    full_hd = _metrics_subset(trades, block_fn=block_fn)

    return {
        "phase": "437B-High-Drift-Robustness-WalkForward",
        "generated_at": _now_iso(),
        "period": f"{PERIOD_START}..{PERIOD_END}",
        "guard_spec": (
            "dynamic40 AND ((day_high>=1.2% AND r10<-0.15% AND r5>r10) "
            "OR (day_high>=1.5% AND (r15<-0.5% OR r5<-0.5%)))"
        ),
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "full_period": {
            "baseline": full_base,
            "high_drift": full_hd,
            "delta_pnl_yen": round(full_hd["total_pnl_yen"] - full_base["total_pnl_yen"], 2),
        },
        "walkforward": wf_rows,
        "leave_one_day_out": loo_rows,
        "sensitivity": sensitivity,
        "symbol_contribution": sym_contrib,
        "daily_distribution": daily_dist,
    }


def _csv_write(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    _write_csv(path, list(rows[0].keys()), rows)


@dataclass
class Phase437BJob:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase437b_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        kabu = resolve_kabu_root(self.repo_root)

        paths = {
            "walkforward": reports / "phase437b_high_drift_walkforward.csv",
            "loo": reports / "phase437b_high_drift_leave_one_day_out.csv",
            "sensitivity": reports / "phase437b_high_drift_sensitivity.csv",
            "summary": reports / "phase437b_high_drift_summary.json",
            "report": kabu / "docs" / "operations" / "phase437b_high_drift_walkforward_report.md",
        }

        _csv_write(paths["walkforward"], result.get("walkforward") or [])
        _csv_write(paths["loo"], result.get("leave_one_day_out") or [])
        _csv_write(paths["sensitivity"], result.get("sensitivity") or [])

        summary = {
            "phase": result.get("phase"),
            "generated_at": result.get("generated_at"),
            "period": result.get("period"),
            "verdict": result.get("verdict"),
            "mandatory_answers": result.get("mandatory_answers"),
            "full_period": result.get("full_period"),
            "walkforward": result.get("walkforward"),
            "symbol_contribution": result.get("symbol_contribution"),
            "daily_distribution": {
                k: v for k, v in (result.get("daily_distribution") or {}).items() if k != "daily_rows"
            },
        }
        paths["summary"].write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        m = result.get("mandatory_answers") or {}
        wf = result.get("walkforward") or []
        sym = result.get("symbol_contribution") or {}
        daily = result.get("daily_distribution") or {}

        lines = [
            "# Phase437B — High Drift Robustness Walk Forward",
            "",
            f"Generated: {result.get('generated_at')}",
            f"Period: {result.get('period')}",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## Mandatory answers",
            "",
            f"1. 20260617 improved: **{m.get('1_20260617_improved')}** (delta {m.get('case617_delta_pnl_yen'):,.0f} yen)",
            f"2. 20260618 improved: **{m.get('2_20260618_improved')}** (delta {m.get('case618_delta_pnl_yen'):,.0f} yen)",
            f"3. LOO mean delta: **{m.get('3_leave_one_day_out_mean_delta_yen'):,.0f} yen**",
            f"4. Improved days: **{m.get('4_improved_day_count')}**",
            f"5. Worsened days: **{m.get('5_worsened_day_count')}**",
            f"6. Max worsened day: **{m.get('6_max_worsened_day')}**",
            f"7. 6976 contribution rate: **{m.get('7_symbol_6976_contribution_rate')}**",
            f"8. Top3 contribution rate: **{m.get('8_top3_symbol_contribution_rate')}**",
            f"9. Overfit judgment: **{m.get('9_overfit_judgment')}**",
            f"10. Runtime shadow recommended: **{m.get('10_runtime_shadow_recommended')}**",
            "",
            "## Part A — Walk Forward",
            "",
            "| case | test_day | baseline PnL | high_drift PnL | delta | delta_pf | delta_stop |",
            "|------|----------|--------------|----------------|-------|----------|------------|",
        ]
        for r in wf:
            lines.append(
                f"| {r.get('case_id')} | {r.get('test_day')} | {r.get('baseline_pnl_yen'):,.0f} | "
                f"{r.get('high_drift_pnl_yen'):,.0f} | {r.get('delta_pnl_yen'):,.0f} | "
                f"{r.get('delta_pf')} | {r.get('delta_stop_count')} |"
            )

        lines.extend(
            [
                "",
                "## Part B — Leave-One-Day-Out (test day delta)",
                "",
                "| day | baseline | guard | delta |",
                "|-----|----------|-------|-------|",
            ]
        )
        for r in result.get("leave_one_day_out") or []:
            lines.append(
                f"| {r.get('excluded_day')} | {r.get('baseline_pnl_yen'):,.0f} | "
                f"{r.get('guard_pnl_yen'):,.0f} | {r.get('delta_pnl_yen'):,.0f} |"
            )

        lines.extend(
            [
                "",
                "## Part C — Threshold sensitivity (full period)",
                "",
                "| param | value | PnL | PF | delta_pnl | removed |",
                "|-------|-------|-----|-----|-----------|---------|",
            ]
        )
        for r in result.get("sensitivity") or []:
            pv = r.get("param_value")
            lines.append(
                f"| {r.get('sweep_param')} | {pv} | {r.get('total_pnl_yen'):,.0f} | "
                f"{r.get('profit_factor')} | {r.get('delta_pnl_yen'):,.0f} | {r.get('removed_count')} |"
            )

        lines.extend(
            [
                "",
                "## Part D — Symbol contribution to improvement",
                "",
                f"- Total improvement: {sym.get('total_improvement_yen'):,.0f} yen",
                f"- 6976: {sym.get('symbol_6976_improvement_yen'):,.0f} yen ({sym.get('symbol_6976_contribution_rate')})",
                f"- Top3 rate (net delta): {sym.get('top3_contribution_rate')} "
            f"(gross positive pool: {sym.get('top3_contribution_rate_gross_positive')})",
            f"- Winner removal offset: {sym.get('winner_removal_offset_yen'):,.0f} yen",
                f"- Top5 rate: {sym.get('top5_contribution_rate')}",
                "",
                "## Part E — Daily distribution",
                "",
                f"- Improved / worsened / flat: {daily.get('improved_day_count')} / "
                f"{daily.get('worsened_day_count')} / {daily.get('flat_day_count')}",
                f"- Max improvement: {daily.get('max_improvement_day')} ({daily.get('max_improvement_delta_yen'):,.0f})",
                f"- Max worsened: {daily.get('max_worsened_day')} ({daily.get('max_worsened_delta_yen'):,.0f})",
                "",
                "Runtime/YAML/Entry/Exit/Order/Discord changes **forbidden** (audit only).",
                "",
            ]
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text("\n".join(lines), encoding="utf-8")
        return paths
