#!/usr/bin/env python3
"""
Phase 90: Stability review for q070_cap3_mfe_fav_vol_liq_trial (diagnostic only).
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER = ROOT / "kabu_native" / "results" / "small_paper"
REPORTS = ROOT / "kabu_native" / "results" / "reports"
VOL_LIQ_CFG = ROOT / "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
POLICY_ID = "q070_cap3_mfe_fav_vol_liq_trial"


def _bootstrap() -> None:
    native = ROOT / "kabu_native" / "src"
    for p in (str(ROOT), str(native)):
        if p not in sys.path:
            sys.path.insert(0, p)


def _import_phase84() -> Any:
    path = ROOT / "kabu_native/scripts/run_phase84_vol_liq_trial_review.py"
    name = "phase84_helpers_p90"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    from small_paper.daytrade_suitability import profit_factor

    return profit_factor(pnls)


def _summarize_trades(pnls: Sequence[float]) -> dict[str, Any]:
    if not pnls:
        return {
            "trade_count": 0,
            "pf": None,
            "avg_pnl": None,
            "total_pnl": None,
            "win_rate": None,
        }
    pf = _profit_factor(pnls)
    n = len(pnls)
    return {
        "trade_count": n,
        "pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "avg_pnl": round(statistics.mean(pnls), 4),
        "total_pnl": round(sum(pnls), 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4),
    }


def _top_k_share(sorted_pnls: Sequence[tuple[str, float]], k: int, aggregate_total: float) -> dict[str, Any]:
    top = sorted_pnls[:k]
    top_sum = sum(v for _, v in top)
    share = (top_sum / aggregate_total) if aggregate_total else None
    return {
        "symbols": [s for s, _ in top],
        "combined_total_pnl": round(top_sum, 4),
        "profit_contribution_rate": round(share, 4) if share is not None else None,
    }


def _std(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    return round(statistics.stdev(values), 4)


def _sharpe_like(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mu = statistics.mean(values)
    sd = statistics.stdev(values)
    if sd <= 1e-12:
        return None
    return round(mu / sd, 4)


def _grade_stability(
    session_rows: Sequence[Mapping[str, Any]],
    concentration: Mapping[str, Any],
    robustness: Mapping[str, Any],
) -> tuple[str, str, list[str]]:
    notes: list[str] = []
    n_sess = len(session_rows)
    if n_sess < 3:
        return (
            "fragile",
            f"Only {n_sess} sessions with vol_liq data; insufficient for stability claim",
            notes,
        )

    pfs = [float(r["pf"]) for r in session_rows if r.get("pf") is not None]
    totals = [float(r["total_pnl"]) for r in session_rows if r.get("total_pnl") is not None]
    pf_gt1 = sum(1 for p in pfs if p > 1.0)
    pf_gt1_rate = pf_gt1 / len(pfs) if pfs else 0.0
    pos_sessions = sum(1 for t in totals if t > 0)
    pos_rate = pos_sessions / len(totals) if totals else 0.0

    top1 = float(concentration.get("top1_symbol_profit_contribution_rate") or 0)
    top3 = float(concentration.get("top3_symbols_profit_contribution_rate") or 0)
    max_profit_sess = float(concentration.get("max_profit_session_contribution_rate") or 0)
    max_loss_sess = abs(float(concentration.get("max_loss_session_contribution_rate") or 0))
    pf_std = _float(robustness.get("session_pf_std")) or 999.0

    fragile_flags = 0
    if top1 >= 0.55:
        fragile_flags += 1
        notes.append(f"top1 symbol profit share {top1:.1%} >= 55%")
    if max_profit_sess >= 0.75:
        fragile_flags += 1
        notes.append(f"max profit session share {max_profit_sess:.1%} >= 75%")
    if pf_gt1_rate < 0.5:
        fragile_flags += 1
        notes.append(f"sessions with PF>1 only {pf_gt1_rate:.0%}")
    if pos_rate < 0.5:
        fragile_flags += 1
        notes.append(f"positive total_pnl sessions {pos_rate:.0%}")

    robust_flags = 0
    if pf_gt1_rate >= 0.6 and pos_rate >= 0.6:
        robust_flags += 1
    if top1 < 0.40 and top3 < 0.75:
        robust_flags += 1
    if max_profit_sess < 0.55:
        robust_flags += 1
    if pf_std < 0.5:
        robust_flags += 1
    sharpe = _float(robustness.get("session_total_pnl_sharpe_like"))
    if sharpe is not None and sharpe > 0.35:
        robust_flags += 1

    if fragile_flags >= 2:
        return (
            "fragile",
            "Profit or PF depends heavily on few sessions/symbols; low cross-session consistency",
            notes,
        )
    if robust_flags >= 4 and fragile_flags == 0:
        return (
            "robust",
            "PF and PnL positive across majority of sessions with moderate concentration",
            notes,
        )
    return (
        "acceptable",
        "Mixed session outcomes but not dominated by a single symbol/session",
        notes,
    )


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})


def main() -> int:
    _bootstrap()
    from small_paper.config import load_pilot_config
    from small_paper.daytrade_suitability_gate import build_vol_liq_threshold

    p84 = _import_phase84()
    p71 = p84._load_phase71()
    pilot = load_pilot_config(VOL_LIQ_CFG)

    parser = argparse.ArgumentParser(description="Phase90 vol_liq stability review")
    parser.add_argument("--output-dir", type=Path, default=REPORTS)
    args = parser.parse_args()
    out_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    all_trades: list[dict[str, Any]] = []
    sessions_used: list[str] = []

    for sid in p84.discover_sessions(SMALL_PAPER):
        sdir = SMALL_PAPER / sid
        if not (sdir / "structural_trades.csv").is_file():
            continue
        push_dir = p84.push_dir_for_key(sid)
        if not push_dir or not push_dir.is_dir():
            continue
        raw = p84.load_trades(sdir, p71)
        if not raw:
            continue
        rows = p84.build_metric_rows(raw, push_dir)
        qrows = p84.filter_quality(rows)
        state = build_vol_liq_threshold(pilot, repo_root=ROOT, run_session_key=sid)
        th = state.vol_liq_threshold if state else None
        if th is None:
            continue

        raw_buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for t in raw:
            key = (str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            raw_buckets[key].append(dict(t))

        kept_keys = {
            (str(r["symbol"]), str(r["entry_time"]))
            for r in qrows
            if (_float(r.get("volatility_liquidity_score")) or 0) >= float(th)
        }
        if not kept_keys:
            continue
        sessions_used.append(sid)

        for key in kept_keys:
            queue = raw_buckets.get(key) or []
            if not queue:
                continue
            tr = queue.pop(0)
            pnl = _float(tr.get("realized_pnl_pct"))
            if pnl is None:
                continue
            all_trades.append(
                {
                    "session_id": sid,
                    "symbol": tr.get("symbol"),
                    "entry_time": tr.get("entry_time"),
                    "exit_reason": str(tr.get("close_reason") or "unknown"),
                    "realized_pnl_pct": pnl,
                }
            )

    if not all_trades:
        print("No vol_liq trades", file=sys.stderr)
        return 1

    agg_pnls = [float(t["realized_pnl_pct"]) for t in all_trades]
    aggregate = _summarize_trades(agg_pnls)
    aggregate["session_count"] = len(sessions_used)

    session_rows: list[dict[str, Any]] = []
    by_session: dict[str, list[float]] = defaultdict(list)
    for t in all_trades:
        by_session[str(t["session_id"])].append(float(t["realized_pnl_pct"]))

    for sid in sorted(by_session):
        s = _summarize_trades(by_session[sid])
        session_rows.append({"session_id": sid, **s})

    by_symbol: dict[str, float] = defaultdict(float)
    for t in all_trades:
        by_symbol[str(t["symbol"] or "")] += float(t["realized_pnl_pct"])
    sym_sorted = sorted(by_symbol.items(), key=lambda x: -x[1])
    agg_total = float(aggregate["total_pnl"] or 0)

    top1 = _top_k_share(sym_sorted, 1, agg_total)
    top3 = _top_k_share(sym_sorted, 3, agg_total)
    top5 = _top_k_share(sym_sorted, 5, agg_total)

    by_exit: dict[str, float] = defaultdict(float)
    for t in all_trades:
        by_exit[str(t["exit_reason"] or "")] += float(t["realized_pnl_pct"])
    exit_sorted = sorted(by_exit.items(), key=lambda x: -x[1])
    exit_contrib = [
        {
            "exit_reason": reason,
            "total_pnl": round(pnl, 4),
            "profit_contribution_rate": round(pnl / agg_total, 4) if agg_total else None,
            "trade_count": sum(1 for t in all_trades if t["exit_reason"] == reason),
        }
        for reason, pnl in exit_sorted
    ]

    sess_sorted = sorted(
        [(r["session_id"], float(r["total_pnl"])) for r in session_rows],
        key=lambda x: -x[1],
    )
    pos_sess_sum = sum(v for _, v in sess_sorted if v > 0) or 0.0
    neg_sess_sum = sum(v for _, v in sess_sorted if v < 0) or 0.0
    max_profit_sess = max(sess_sorted, key=lambda x: x[1]) if sess_sorted else ("", 0.0)
    max_loss_sess = min(sess_sorted, key=lambda x: x[1]) if sess_sorted else ("", 0.0)

    session_pf = [float(r["pf"]) for r in session_rows if r.get("pf") is not None]
    session_avg = [float(r["avg_pnl"]) for r in session_rows if r.get("avg_pnl") is not None]
    session_total = [float(r["total_pnl"]) for r in session_rows if r.get("total_pnl") is not None]

    concentration = {
        "aggregate_total_pnl": agg_total,
        "top1_symbol_profit_contribution_rate": top1.get("profit_contribution_rate"),
        "top1_symbols": top1.get("symbols"),
        "top3_symbols_profit_contribution_rate": top3.get("profit_contribution_rate"),
        "top3_symbols": top3.get("symbols"),
        "top5_symbols_profit_contribution_rate": top5.get("profit_contribution_rate"),
        "top5_symbols": top5.get("symbols"),
        "exit_reason_profit_contribution": exit_contrib,
        "max_profit_session_id": max_profit_sess[0],
        "max_profit_session_total_pnl": round(max_profit_sess[1], 4),
        "max_profit_session_contribution_rate": round(max_profit_sess[1] / agg_total, 4)
        if agg_total
        else None,
        "max_profit_session_share_of_positive_pool": round(max_profit_sess[1] / pos_sess_sum, 4)
        if pos_sess_sum > 0 and max_profit_sess[1] > 0
        else None,
        "max_loss_session_id": max_loss_sess[0],
        "max_loss_session_total_pnl": round(max_loss_sess[1], 4),
        "max_loss_session_contribution_rate": round(max_loss_sess[1] / agg_total, 4)
        if agg_total
        else None,
        "max_loss_session_share_of_negative_pool": round(max_loss_sess[1] / neg_sess_sum, 4)
        if neg_sess_sum < 0 and max_loss_sess[1] < 0
        else None,
    }

    robustness = {
        "session_pf_std": _std(session_pf),
        "session_avg_pnl_std": _std(session_avg),
        "session_total_pnl_std": _std(session_total),
        "session_avg_pnl_sharpe_like": _sharpe_like(session_avg),
        "session_total_pnl_sharpe_like": _sharpe_like(session_total),
        "sessions_pf_gt_1_count": sum(1 for p in session_pf if p > 1.0),
        "sessions_pf_gt_1_rate": round(sum(1 for p in session_pf if p > 1.0) / len(session_pf), 4)
        if session_pf
        else None,
        "sessions_positive_total_pnl_count": sum(1 for t in session_total if t > 0),
        "sessions_positive_total_pnl_rate": round(
            sum(1 for t in session_total if t > 0) / len(session_total), 4
        )
        if session_total
        else None,
    }

    stability_grade, grade_rationale, grade_notes = _grade_stability(
        session_rows, concentration, robustness
    )

    concentration_rows: list[dict[str, Any]] = [
        {
            "analysis_type": "symbol_top1",
            "dimension": ",".join(top1.get("symbols") or []),
            "combined_total_pnl": top1.get("combined_total_pnl"),
            "profit_contribution_rate": top1.get("profit_contribution_rate"),
            "aggregate_total_pnl": agg_total,
        },
        {
            "analysis_type": "symbol_top3",
            "dimension": ",".join(top3.get("symbols") or []),
            "combined_total_pnl": top3.get("combined_total_pnl"),
            "profit_contribution_rate": top3.get("profit_contribution_rate"),
            "aggregate_total_pnl": agg_total,
        },
        {
            "analysis_type": "symbol_top5",
            "dimension": ",".join(top5.get("symbols") or []),
            "combined_total_pnl": top5.get("combined_total_pnl"),
            "profit_contribution_rate": top5.get("profit_contribution_rate"),
            "aggregate_total_pnl": agg_total,
        },
        {
            "analysis_type": "session_max_profit",
            "dimension": concentration["max_profit_session_id"],
            "combined_total_pnl": concentration["max_profit_session_total_pnl"],
            "profit_contribution_rate": concentration["max_profit_session_contribution_rate"],
            "share_of_positive_session_pool": concentration["max_profit_session_share_of_positive_pool"],
        },
        {
            "analysis_type": "session_max_loss",
            "dimension": concentration["max_loss_session_id"],
            "combined_total_pnl": concentration["max_loss_session_total_pnl"],
            "profit_contribution_rate": concentration["max_loss_session_contribution_rate"],
            "share_of_negative_session_pool": concentration["max_loss_session_share_of_negative_pool"],
        },
    ]
    for ex in exit_contrib:
        concentration_rows.append(
            {
                "analysis_type": "exit_reason",
                "dimension": ex["exit_reason"],
                "combined_total_pnl": ex["total_pnl"],
                "profit_contribution_rate": ex["profit_contribution_rate"],
                "trade_count": ex["trade_count"],
                "aggregate_total_pnl": agg_total,
            }
        )

    review = {
        "phase": 90,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "policy_id": POLICY_ID,
        "vol_liq_config": str(VOL_LIQ_CFG.relative_to(ROOT)),
        "exit_policy": "combined_structural_exit_v1",
        "purpose": "Assess whether vol_liq trial performance is reproducible across sessions, not propose new tweaks",
        "constraints": {
            "no_logic_change": True,
            "no_yaml_change": True,
            "no_symbol_or_time_tuning": True,
        },
        "population_filter": "quality>=0.70 and volatility_liquidity_top50; structural_trades.csv",
        "sessions_analyzed": sessions_used,
        "aggregate": aggregate,
        "session_breakdown": session_rows,
        "profit_concentration": concentration,
        "robustness_metrics": robustness,
        "stability_grade": stability_grade,
        "stability_rationale": grade_rationale,
        "stability_notes": grade_notes,
        "phase89_reference": "keep_current_exit on min_peak mfe giveback what-if",
        "note": "Diagnostic only.",
    }

    write_csv(out_dir / "phase90_session_breakdown.csv", session_rows)
    write_csv(out_dir / "phase90_concentration_analysis.csv", concentration_rows)
    (out_dir / "phase90_stability_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "stability_grade": stability_grade,
                "stability_rationale": grade_rationale,
                "aggregate_pf": aggregate.get("pf"),
                "sessions": len(sessions_used),
                "output": str(out_dir / "phase90_stability_review.json"),
            },
            ensure_ascii=True,
        )
    )
    print(f"Wrote phase90 outputs under {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
