"""
Phase416: Post-Phase414 historical shadow rebaseline (research only).

Compares Baseline A vs Baseline B (no_overlap_replace backfill) over 20260529-20260616
and re-computes selected shadow / equity simulations that are directly trade-input driven.

Constraints: no Runtime/YAML/Entry/Exit/Order changes. Recompute only.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _norm_symbol
from research.phase382_capital_constrained_backtest import _parse_ts, _write_csv
from research.phase400_holding_time_audit import hold_seconds, normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase409_boundary_forward_shadow import evaluate_boundary_shadow_trade
from research.phase410_duplicate_reentry_audit import _phase409_skip_reason
from research.phase413_no_overlap_replace_backfill import collapse_overlap_replace_chains

JST = ZoneInfo("Asia/Tokyo")

PERIOD_START = "20260529"
PERIOD_END = "20260616"

PHASE399_TRADES_CSV = "phase399_historical_position_cap_backfill_trades.csv"

COMPARISON_FIELDS = [
    "module",
    "metric",
    "baseline_a",
    "baseline_b",
    "delta_b_minus_a",
    "status",
    "note",
]

DAILY_FIELDS = [
    "day",
    "a_trade_count",
    "b_trade_count",
    "a_overlap_replaced_review_count",
    "b_overlap_replaced_review_count",
    "a_median_hold_sec",
    "b_median_hold_sec",
    "a_boundary_eligible_count",
    "b_boundary_eligible_count",
    "a_phase409_would_hit_count",
    "b_phase409_would_hit_count",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _bool(val: Any) -> bool:
    return str(val or "").strip().lower() in ("true", "1", "yes")


def _float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _ensure_hold_sec(trade: Mapping[str, Any]) -> float:
    hs = trade.get("hold_sec")
    if hs not in (None, ""):
        return _float(hs)
    return hold_seconds(str(trade.get("entry_time") or ""), str(trade.get("exit_time") or ""))


def _chronological_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    sort_keys = [
        (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST), i)
        for i, t in enumerate(trades)
    ]
    order = [i for _, i in sorted(sort_keys, key=lambda x: (x[0], x[1]))]
    return [_float(trades[i].get("pnl_yen_100_float") or trades[i].get("pnl_yen_100") or 0) for i in order]


def _counts_by_bucket(trades: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {"overlap_replaced_review": 0, "stop_hit": 0, "trailing_mfe": 0, "session_close": 0}
    for t in trades:
        bucket = normalize_exit_reason(str(t.get("exit_reason") or t.get("close_reason") or ""))
        if bucket == "overlap_replaced":
            out["overlap_replaced_review"] += 1
        elif bucket == "stop_hit":
            out["stop_hit"] += 1
        elif bucket == "trailing_mfe":
            out["trailing_mfe"] += 1
        elif bucket == "session_close":
            out["session_close"] += 1
    return out


def _basic_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0) for t in trades]
    holds = [float(_ensure_hold_sec(t)) for t in trades]
    chron = _chronological_pnls(trades)
    dd = _max_drawdown_yen(chron)
    counts = _counts_by_bucket(trades)
    win_rate = round(100.0 * sum(1 for p in pnls if p > 0) / max(1, len(pnls)), 2)
    return {
        "trade_count": len(trades),
        "total_pnl_yen_100": round(sum(pnls), 2),
        "pf": _pf(chron),
        "maxdd_yen_100": dd,
        "final_equity_yen": round(1_500_000.0 + sum(pnls), 2),
        "win_rate": win_rate,
        "avg_hold_sec": round(sum(holds) / max(1, len(holds)), 2) if holds else 0.0,
        "median_hold_sec": round(median(holds), 2) if holds else 0.0,
        **counts,
    }


def load_baseline_a_trades(repo_root: Path) -> list[dict[str, Any]]:
    """
    Baseline A: Phase399 position-cap accepted history + 20260616 structural_trades.
    """
    out: list[dict[str, Any]] = []
    p399 = repo_root / "results" / "reports" / PHASE399_TRADES_CSV
    for r in _read_csv_rows(p399):
        day = str(r.get("day") or "")
        if not (PERIOD_START <= day <= "20260615"):
            continue
        if not _bool(r.get("position_cap_accepted")):
            continue
        t = dict(r)
        t["symbol"] = _norm_symbol(str(t.get("symbol") or ""))
        t["pnl_yen_100_float"] = _float(t.get("pnl_yen_100"))
        t["exit_reason"] = t.get("exit_reason") or t.get("close_reason") or ""
        t["hold_sec"] = _ensure_hold_sec(t)
        out.append(t)

    # 20260616 from Runtime artifacts
    from research.phase409_boundary_forward_shadow import load_structural_trades_for_day

    for t in load_structural_trades_for_day(repo_root, "20260616"):
        out.append(dict(t))

    out.sort(
        key=lambda r: (
            str(r.get("day") or ""),
            str(r.get("session") or ""),
            _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        )
    )
    return out


def load_baseline_b_trades(baseline_a: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """
    Baseline B: Phase413 no_overlap_replace backfill history derived from baseline A.
    """
    # Collapse per-day chains (to keep day boundaries stable)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for t in baseline_a:
        day = str(t.get("day") or "")
        if not (PERIOD_START <= day <= PERIOD_END):
            continue
        by_day.setdefault(day, []).append(dict(t))
    shadow_all: list[dict[str, Any]] = []
    for day in sorted(by_day.keys()):
        shadow_day, _ = collapse_overlap_replace_chains(by_day[day])
        shadow_all.extend(shadow_day)
    shadow_all.sort(
        key=lambda r: (
            str(r.get("day") or ""),
            _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        )
    )
    return shadow_all


def compute_phase409_boundary_shadow(
    trades: Sequence[Mapping[str, Any]],
    *,
    repo_root: Path,
    reports_dir: Path,
) -> dict[str, Any]:
    policy_path = reports_dir / "phase405_time_boundary_policy.csv"
    from research.phase406_portfolio_adoption import load_phase405_boundary_policy

    boundary_rules = load_phase405_boundary_policy(policy_path)
    session_cache: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    eligible = 0
    would_hit = 0
    eval_failed = 0
    for t in trades:
        hold = float(_ensure_hold_sec(t))
        if hold >= 300:
            eligible += 1
        try:
            row = evaluate_boundary_shadow_trade(
                t,
                repo_root=repo_root,
                session_cache=session_cache,
                boundary_rules=boundary_rules,
            )
        except Exception:
            row = None
        if row is None:
            eval_failed += 1
            continue
        rows.append(row)
        if "boundary" in str(row.get("shadow_exit_reason") or ""):
            would_hit += 1
    # chronological PnLs by exit_time
    rows_sorted = sorted(
        rows,
        key=lambda r: (_parse_ts(str(r.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST)),
    )
    baseline_pnls = [float(r["baseline_pnl_yen_100"]) for r in rows_sorted]
    shadow_pnls = [float(r["shadow_pnl_yen_100"]) for r in rows_sorted]
    baseline_pf = _pf(baseline_pnls)
    shadow_pf = _pf(shadow_pnls)
    baseline_dd = _max_drawdown_yen(baseline_pnls)
    shadow_dd = _max_drawdown_yen(shadow_pnls)
    return {
        "trade_count": len(trades),
        "eligible_count": eligible,
        "would_hit_count": would_hit,
        "eval_failed_count": eval_failed,
        "baseline_total_pnl_yen_100": round(sum(baseline_pnls), 2),
        "shadow_total_pnl_yen_100": round(sum(shadow_pnls), 2),
        "delta_pnl_yen_100": round(sum(shadow_pnls) - sum(baseline_pnls), 2),
        "baseline_pf": baseline_pf,
        "shadow_pf": shadow_pf,
        "baseline_maxdd_yen_100": baseline_dd,
        "shadow_maxdd_yen_100": shadow_dd,
    }


def compute_phase273_forward_shadow(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from research.phase271_leverage_attribution_and_robustness import simulate_audited
    from research.phase273_live_config_forward_shadow_logger import (
        LIVE_CONFIG_CANDIDATES,
        compute_candidate_summary,
        resolve_current_recommendation,
    )

    period_days = sorted({str(t.get("day") or "") for t in trades if t.get("day")})
    summaries: list[dict[str, Any]] = []
    for cand in LIVE_CONFIG_CANDIDATES:
        sim = simulate_audited(
            trades,
            starting_equity=int(cand["starting_equity"]),
            leverage=float(cand["leverage"]),
            cap=int(cand["cap"]),
            stop_policy=str(cand["stop_policy"]),
        )
        summaries.append(
            compute_candidate_summary(sim, candidate=cand, period_days=period_days, trades=trades)
        )
    return {
        "period_day_count": len(period_days),
        "recommended_candidate_key": resolve_current_recommendation(summaries),
        "candidate_summaries": summaries,
    }


def compute_phase274_transition_shadow(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    from research.phase274_live_config_auto_transition_shadow import compute_adoption_verdict, simulate_auto_transition

    period_days = sorted({str(t.get("day") or "") for t in trades if t.get("day")})
    sim = simulate_auto_transition(trades)
    adoption = compute_adoption_verdict(metrics=sim, day_count=len(period_days))
    return {
        "period_day_count": len(period_days),
        "transition_summary": {
            "current_equity": sim.get("final_equity"),
            "active_policy_band": sim.get("active_policy_band"),
            "transition_day_to_2000k": sim.get("transition_day_to_2000k"),
            "max_drawdown_pct": sim.get("max_drawdown_pct"),
            "profit_factor": sim.get("profit_factor"),
        },
        "adoption_verdict": adoption,
    }


def compute_phase263_equity_dynamic_stop(
    trades: Sequence[Mapping[str, Any]],
    *,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Recompute Phase263 using supplied trades only (no disk scan).
    """
    from research.equity_dynamic_stop_shadow import (
        aggregate_summary_rows,
        build_entry_level_rows,
        build_verdict,
        load_period_entries,
        resolve_period_days,
    )

    trades_by_day: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        day = str(t.get("day") or "")
        if not day:
            continue
        row = dict(t)
        row["symbol"] = _norm_symbol(str(row.get("symbol") or ""))
        if row.get("pnl_yen_100") is None:
            row["pnl_yen_100"] = _float(row.get("pnl_yen_100_float") or 0)
        trades_by_day.setdefault(day, []).append(row)
    period_days = resolve_period_days(trades_by_day)
    base_entries = load_period_entries(
        trades_by_day,
        period_days=period_days,
        repo_root=repo_root,
    )
    entry_rows = build_entry_level_rows(base_entries)
    summary_rows = aggregate_summary_rows(entry_rows, base_entries)
    verdict = build_verdict(summary_rows=summary_rows, period_days=period_days, entry_count=len(base_entries))
    return {
        "period_days": period_days,
        "base_entry_count": len(base_entries),
        "verdict": verdict,
        "summary_by_equity_risk_pct": summary_rows,
    }


def build_daily_comparison(
    a_trades: Sequence[Mapping[str, Any]],
    b_trades: Sequence[Mapping[str, Any]],
    *,
    repo_root: Path,
    reports_dir: Path,
) -> list[dict[str, Any]]:
    from research.phase406_portfolio_adoption import load_phase405_boundary_policy

    boundary_rules = load_phase405_boundary_policy(reports_dir / "phase405_time_boundary_policy.csv")
    a_by_day: dict[str, list[dict[str, Any]]] = {}
    b_by_day: dict[str, list[dict[str, Any]]] = {}
    for t in a_trades:
        a_by_day.setdefault(str(t.get("day") or ""), []).append(dict(t))
    for t in b_trades:
        b_by_day.setdefault(str(t.get("day") or ""), []).append(dict(t))

    rows: list[dict[str, Any]] = []
    for day in sorted({d for d in a_by_day.keys() if PERIOD_START <= d <= PERIOD_END}):
        a = a_by_day.get(day, [])
        b = b_by_day.get(day, [])
        a_counts = _counts_by_bucket(a)
        b_counts = _counts_by_bucket(b)
        a_holds = [float(_ensure_hold_sec(t)) for t in a]
        b_holds = [float(_ensure_hold_sec(t)) for t in b]
        # Phase409 eligibility/would-hit (fast path)
        a_eligible = 0
        a_hit = 0
        b_eligible = 0
        b_hit = 0
        sc_a: dict[str, Any] = {}
        sc_b: dict[str, Any] = {}
        for t in a:
            _, ok, hit = _phase409_skip_reason(t, repo_root=repo_root, session_cache=sc_a, boundary_rules=boundary_rules)
            if ok:
                a_eligible += 1
            if hit:
                a_hit += 1
        for t in b:
            _, ok, hit = _phase409_skip_reason(t, repo_root=repo_root, session_cache=sc_b, boundary_rules=boundary_rules)
            if ok:
                b_eligible += 1
            if hit:
                b_hit += 1
        rows.append(
            {
                "day": day,
                "a_trade_count": len(a),
                "b_trade_count": len(b),
                "a_overlap_replaced_review_count": a_counts["overlap_replaced_review"],
                "b_overlap_replaced_review_count": b_counts["overlap_replaced_review"],
                "a_median_hold_sec": round(median(a_holds), 2) if a_holds else 0.0,
                "b_median_hold_sec": round(median(b_holds), 2) if b_holds else 0.0,
                "a_boundary_eligible_count": a_eligible,
                "b_boundary_eligible_count": b_eligible,
                "a_phase409_would_hit_count": a_hit,
                "b_phase409_would_hit_count": b_hit,
            }
        )
    return rows


def run_phase416(*, repo_root: Path, reports_dir: Path) -> dict[str, Any]:
    a = load_baseline_a_trades(repo_root)
    b = load_baseline_b_trades(a)

    result: dict[str, Any] = {
        "phase": 416,
        "generated_at": _now_iso(),
        "period": {"start": PERIOD_START, "end": PERIOD_END},
        "baselines": {
            "A": {"name": "phase399_position_cap_history_plus_20260616", "trade_count": len(a)},
            "B": {"name": "phase413_no_overlap_replace_backfill", "trade_count": len(b)},
        },
        "status": "rebaseline_complete",
        "modules": {},
        "notes": [],
    }

    # Baseline trade-only metrics
    result["modules"]["baseline_trade_metrics"] = {
        "A": _basic_metrics(a),
        "B": _basic_metrics(b),
    }

    comparison_rows: list[dict[str, Any]] = []

    def _emit(module: str, metric: str, a_val: Any, b_val: Any, *, status: str = "ok", note: str = "") -> None:
        da = _float(a_val) if isinstance(a_val, (int, float, str)) and str(a_val).replace(".", "", 1).isdigit() else a_val
        db = _float(b_val) if isinstance(b_val, (int, float, str)) and str(b_val).replace(".", "", 1).isdigit() else b_val
        delta = ""
        if isinstance(da, (int, float)) and isinstance(db, (int, float)):
            delta = round(float(db) - float(da), 4)
        comparison_rows.append(
            {
                "module": module,
                "metric": metric,
                "baseline_a": a_val,
                "baseline_b": b_val,
                "delta_b_minus_a": delta,
                "status": status,
                "note": note,
            }
        )

    # Phase409
    try:
        p409_a = compute_phase409_boundary_shadow(a, repo_root=repo_root, reports_dir=reports_dir)
        p409_b = compute_phase409_boundary_shadow(b, repo_root=repo_root, reports_dir=reports_dir)
        result["modules"]["phase409_boundary_shadow"] = {"A": p409_a, "B": p409_b}
        _emit("phase409", "eligible_count", p409_a["eligible_count"], p409_b["eligible_count"])
        _emit("phase409", "would_hit_count", p409_a["would_hit_count"], p409_b["would_hit_count"])
    except Exception as exc:
        result["modules"]["phase409_boundary_shadow"] = {"status": "insufficient_inputs", "error": str(exc)}

    # Phase273
    try:
        p273_a = compute_phase273_forward_shadow(a)
        p273_b = compute_phase273_forward_shadow(b)
        result["modules"]["phase273_live_config_forward_shadow"] = {"A": p273_a, "B": p273_b}
        _emit("phase273", "recommended_candidate_key", p273_a["recommended_candidate_key"], p273_b["recommended_candidate_key"], status="ok")
    except Exception as exc:
        result["modules"]["phase273_live_config_forward_shadow"] = {"status": "insufficient_inputs", "error": str(exc)}

    # Phase274
    try:
        p274_a = compute_phase274_transition_shadow(a)
        p274_b = compute_phase274_transition_shadow(b)
        result["modules"]["phase274_live_config_auto_transition_shadow"] = {"A": p274_a, "B": p274_b}
        _emit(
            "phase274",
            "adoption_verdict",
            (p274_a.get("adoption_verdict") or {}).get("adoption_verdict"),
            (p274_b.get("adoption_verdict") or {}).get("adoption_verdict"),
        )
    except Exception as exc:
        result["modules"]["phase274_live_config_auto_transition_shadow"] = {"status": "insufficient_inputs", "error": str(exc)}

    # Phase263 (equity dynamic stop shadow)
    try:
        p263_a = compute_phase263_equity_dynamic_stop(a, repo_root=repo_root)
        p263_b = compute_phase263_equity_dynamic_stop(b, repo_root=repo_root)
        result["modules"]["phase263_equity_dynamic_stop_shadow"] = {"A": p263_a, "B": p263_b}
        _emit(
            "phase263",
            "best_policy_at_1p5m",
            (p263_a.get("verdict") or {}).get("best_policy_at_1p5m"),
            (p263_b.get("verdict") or {}).get("best_policy_at_1p5m"),
        )
    except Exception as exc:
        result["modules"]["phase263_equity_dynamic_stop_shadow"] = {"status": "insufficient_inputs", "error": str(exc)}

    # Mark unimplemented targets explicitly
    for missing in (
        "phase262_risk_sizing_shadow",
        "phase255_sector_heat_forward_shadow",
        "phase256_sector_heat_forward_shadow",
        "phase400_to_408_exit_research_rank",
        "phase266_equity_dynamic_stop_shadow",
    ):
        result["modules"].setdefault(missing, {"status": "insufficient_inputs", "note": "not implemented in Phase416 runner"})

    daily_rows = build_daily_comparison(a, b, repo_root=repo_root, reports_dir=reports_dir)

    result["outputs"] = {
        "comparison_rows": len(comparison_rows),
        "daily_rows": len(daily_rows),
    }
    result["_comparison_rows"] = comparison_rows
    result["_daily_rows"] = daily_rows
    return result


def render_report_md(result: Mapping[str, Any]) -> str:
    baselines = result.get("baselines") or {}
    mods = result.get("modules") or {}
    p409 = mods.get("phase409_boundary_shadow") or {}
    p273 = mods.get("phase273_live_config_forward_shadow") or {}
    p274 = mods.get("phase274_live_config_auto_transition_shadow") or {}
    p263 = mods.get("phase263_equity_dynamic_stop_shadow") or {}
    lines: list[str] = []
    lines.append("# Phase416 — Post-Phase414 Historical Shadow Rebaseline")
    lines.append("")
    lines.append("## Conclusion (status)")
    lines.append("")
    lines.append(f"- status: **{result.get('status')}**")
    lines.append("")
    lines.append("## 必須回答")
    lines.append("")
    lines.append(f"- **1. Phase414後の基準履歴はどれか**: Baseline B（`{(baselines.get('B') or {}).get('name')}`）を新基準として扱う。")
    if isinstance(p409, Mapping) and "A" in p409 and "B" in p409:
        a = p409.get("A") or {}
        b = p409.get("B") or {}
        lines.append(
            "- **2. Phase409 Boundary は改善するか悪化するか**: "
            f"eligible {a.get('eligible_count')}→{b.get('eligible_count')}, "
            f"shadowΔPnL {a.get('delta_pnl_yen_100')}→{b.get('delta_pnl_yen_100')}, "
            f"shadow PF {a.get('shadow_pf')}→{b.get('shadow_pf')}, "
            f"shadow maxDD {a.get('shadow_maxdd_yen_100')}→{b.get('shadow_maxdd_yen_100')}."
        )
    else:
        lines.append("- **2. Phase409 Boundary は改善するか悪化するか**: insufficient_inputs")

    if isinstance(p273, Mapping) and "A" in p273 and "B" in p273:
        lines.append(
            "- **3. Phase273/274 の150万円資産推移はどう変わるか**: "
            f"Phase273 recommended={((p273.get('A') or {}).get('recommended_candidate_key'))}→{((p273.get('B') or {}).get('recommended_candidate_key'))}. "
            f"Phase274 adoption={(((p274.get('A') or {}).get('adoption_verdict') or {}).get('adoption_verdict'))}→{(((p274.get('B') or {}).get('adoption_verdict') or {}).get('adoption_verdict'))}."
        )
    else:
        lines.append("- **3. Phase273/274 の150万円資産推移はどう変わるか**: insufficient_inputs")

    if isinstance(p263, Mapping) and "A" in p263 and "B" in p263:
        lines.append(
            "- **4. Phase262/263/266 の採用判断は変わるか**: "
            f"Phase263 best_policy_at_1p5m={((p263.get('A') or {}).get('verdict') or {}).get('best_policy_at_1p5m')}→{((p263.get('B') or {}).get('verdict') or {}).get('best_policy_at_1p5m')}. "
            "Phase262/266 は本 runner では未実装。"
        )
    else:
        lines.append("- **4. Phase262/263/266 の採用判断は変わるか**: Phase262/266 insufficient_inputs")

    lines.append("- **5. Phase400〜408 Exit研究の順位は変わるか**: 本 runner では未実装（再計算が必要）。")
    lines.append("- **6. 以前の採用候補で無効化すべきものはあるか**: Baseline B 前提で再評価が必要（trade_count 母集団が大幅に変化）。")
    lines.append("- **7. 明日以降見るべきshadowはどれか**: Phase409 / Phase273 / Phase274 / Phase263 を継続監視（Phase262/255系は別途 rebaseline 実装が必要）。")
    lines.append("")
    lines.append("## Baselines")
    lines.append("")
    lines.append(f"- A: {(baselines.get('A') or {}).get('name')} (trades={(baselines.get('A') or {}).get('trade_count')})")
    lines.append(f"- B: {(baselines.get('B') or {}).get('name')} (trades={(baselines.get('B') or {}).get('trade_count')})")
    lines.append("")
    lines.append("## Module coverage")
    lines.append("")
    for k, v in sorted(mods.items()):
        if isinstance(v, Mapping) and v.get("status") == "insufficient_inputs":
            lines.append(f"- {k}: insufficient_inputs ({v.get('note') or v.get('error')})")
        else:
            lines.append(f"- {k}: ok")
    lines.append("")
    return "\n".join(lines)


@dataclass
class Phase416Job:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase416_post_no_overlap_shadow_rebaseline_summary.json",
            "comparison": self.reports_dir / "phase416_post_no_overlap_shadow_rebaseline_comparison.csv",
            "daily": self.reports_dir / "phase416_post_no_overlap_shadow_rebaseline_daily.csv",
            "report": self.repo_root / "docs" / "operations" / "phase416_post_no_overlap_shadow_rebaseline_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_phase416(repo_root=self.repo_root, reports_dir=self.reports_dir)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in dict(result).items() if not str(k).startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_csv(paths["comparison"], list(result.get("_comparison_rows") or []), COMPARISON_FIELDS)
        _write_csv(paths["daily"], list(result.get("_daily_rows") or []), DAILY_FIELDS)
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths

