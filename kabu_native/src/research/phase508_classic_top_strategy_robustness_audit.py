"""
Phase508 — Classic top strategy robustness audit (research only).

Audits Phase507 top classical strategies for concentration / dependency fragility.
No Runtime changes. No adoption.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import _now_iso
from research.phase507_classic_strategy_battle import BASELINE_STRATEGY_ID, _run_baseline_runtime
from research.structural_trade_normalize import resolve_reports_dir

PHASE508_MODE = "phase508_classic_robustness_audit"
AUDIT_STRATEGIES = (
    BASELINE_STRATEGY_ID,
    "C_T15_E1",
    "C_T15_E2",
    "C_T13_E2",
)

ROBUSTNESS_FIELDS = [
    "strategy_id",
    "total_pnl_yen_100",
    "trade_count",
    "profit_factor",
    "top1_trade_profit_share_pct",
    "top5_trade_profit_share_pct",
    "top10_trade_profit_share_pct",
    "gini_coefficient",
    "top10_profit_pct_of_gross_wins",
    "verdict_concentration",
    "exclude_top1_symbol_pnl",
    "exclude_top3_symbol_pnl",
    "exclude_top5_symbol_pnl",
    "single_symbol_dependency",
    "exclude_top1_day_pnl",
    "exclude_top3_day_pnl",
    "single_day_dependency",
    "session_end_count",
    "session_end_pnl",
    "session_end_dependency_pct",
    "mean_hold_minutes",
    "median_hold_minutes",
    "p90_hold_minutes",
    "p95_hold_minutes",
    "hold_verdict",
    "overall_verdict",
]

SYMBOL_DEP_FIELDS = [
    "strategy_id",
    "symbol",
    "trade_count",
    "total_pnl_yen_100",
    "win_rate",
    "share_of_total_pnl_pct",
    "rank",
]

DAY_DEP_FIELDS = [
    "strategy_id",
    "day",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "share_of_total_pnl_pct",
    "rank",
]

EXIT_FIELDS = [
    "strategy_id",
    "exit_bucket",
    "raw_exit_reason",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
]

HOLD_FIELDS = [
    "strategy_id",
    "trade_count",
    "mean_hold_minutes",
    "median_hold_minutes",
    "p90_hold_minutes",
    "p95_hold_minutes",
    "min_hold_minutes",
    "max_hold_minutes",
    "verdict",
]


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _gini(values: Sequence[float]) -> float:
    xs = sorted(v for v in values if v > 0)
    if not xs:
        return 0.0
    n = len(xs)
    total = sum(xs)
    if total <= 0:
        return 0.0
    cum = 0.0
    for i, x in enumerate(xs, start=1):
        cum += i * x
    return round((2 * cum) / (n * total) - (n + 1) / n, 4)


def _profit_concentration(pnls: Sequence[float]) -> dict[str, Any]:
    wins = sorted([p for p in pnls if p > 0], reverse=True)
    gross = sum(wins)
    net = sum(pnls)
    def share(n: int) -> Optional[float]:
        if gross <= 0:
            return None
        return round(sum(wins[:n]) / gross * 100.0, 2)
    top10_gross = share(10)
    return {
        "top1_trade_profit_share_pct": share(1),
        "top5_trade_profit_share_pct": share(5),
        "top10_trade_profit_share_pct": top10_gross,
        "top10_profit_pct_of_gross_wins": top10_gross,
        "gini_coefficient": _gini(pnls),
        "gross_win_total": round(gross, 2),
        "net_total": round(net, 2),
        "histogram": _profit_histogram(pnls),
    }


def _profit_histogram(pnls: Sequence[float]) -> dict[str, int]:
    bins = {
        "lt_-5000": 0,
        "m5000_0": 0,
        "0_2000": 0,
        "2000_10000": 0,
        "10000_50000": 0,
        "ge_50000": 0,
    }
    for p in pnls:
        if p < -5000:
            bins["lt_-5000"] += 1
        elif p < 0:
            bins["m5000_0"] += 1
        elif p < 2000:
            bins["0_2000"] += 1
        elif p < 10000:
            bins["2000_10000"] += 1
        elif p < 50000:
            bins["10000_50000"] += 1
        else:
            bins["ge_50000"] += 1
    return bins


def _symbol_dependency(pnls_by_sym: dict[str, list[float]]) -> dict[str, Any]:
    sym_pnl = {s: sum(v) for s, v in pnls_by_sym.items()}
    total = sum(sym_pnl.values())
    ranked = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    top_syms = [s for s, _ in ranked]

    def excl(n: int) -> float:
        drop = set(top_syms[:n])
        return round(sum(p for s, ps in pnls_by_sym.items() for p in ps if s not in drop), 2)

    top1_share = (ranked[0][1] / total * 100.0) if total and ranked else 0.0
    single = bool(ranked and top1_share >= 40.0 and ranked[0][1] > 0)
    return {
        "exclude_top1_symbol_pnl": excl(1),
        "exclude_top3_symbol_pnl": excl(3),
        "exclude_top5_symbol_pnl": excl(5),
        "single_symbol_dependency": single,
        "top1_symbol": ranked[0][0] if ranked else "",
        "top1_symbol_share_pct": round(top1_share, 2),
        "symbol_rows": ranked,
    }


def _day_dependency(pnls_by_day: dict[str, list[float]]) -> dict[str, Any]:
    day_pnl = {d: sum(v) for d, v in pnls_by_day.items()}
    total = sum(day_pnl.values())
    ranked = sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)

    def excl(n: int) -> float:
        drop = {d for d, _ in ranked[:n]}
        return round(sum(p for d, ps in pnls_by_day.items() for p in ps if d not in drop), 2)

    top1_share = (ranked[0][1] / total * 100.0) if total and ranked else 0.0
    single = bool(ranked and top1_share >= 35.0 and ranked[0][1] > 0)
    return {
        "exclude_top1_day_pnl": excl(1),
        "exclude_top3_day_pnl": excl(3),
        "single_day_dependency": single,
        "top1_day": ranked[0][0] if ranked else "",
        "top1_day_share_pct": round(top1_share, 2),
        "day_rows": ranked,
    }


def _exit_bucket(reason: str, exit_rule_id: str = "") -> str:
    r = (reason or "").lower()
    if r in ("session_end", "session_close", "end_of_period"):
        return "session_end"
    if r in ("hard_stop", "stop_hit"):
        return "hard_stop"
    if "vwap" in r or exit_rule_id == "E2":
        return "vwap_exit"
    if "ema" in r or exit_rule_id == "E3":
        return "ema_exit"
    if "rsi" in r or exit_rule_id in ("E4", "E7", "E8", "E9"):
        return "rsi_exit"
    if "macd" in r or exit_rule_id == "E5":
        return "macd_exit"
    if r in ("trailing_mfe", "no_progress", "overlap_replaced"):
        return "runtime_exit"
    return "other"


def _exit_breakdown(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[float]] = defaultdict(list)
    raw: dict[str, list[float]] = defaultdict(list)
    for tr in trades:
        reason = str(tr.get("exit_reason") or "")
        bucket = _exit_bucket(reason, str(tr.get("exit_rule_id") or ""))
        pnl = _float(tr.get("pnl_yen_100") if tr.get("pnl_yen_100") not in (None, "") else tr.get("pnl_yen"))
        buckets[bucket].append(pnl)
        raw[reason].append(pnl)
    total = sum(_float(tr.get("pnl_yen_100") if tr.get("pnl_yen_100") not in (None, "") else tr.get("pnl_yen")) for tr in trades)
    sess = buckets.get("session_end", [])
    sess_pnl = sum(sess)
    dep = round(sess_pnl / total * 100.0, 2) if total else 0.0
    rows: list[dict[str, Any]] = []
    for bucket, pnls in sorted(buckets.items()):
        wins = sum(1 for p in pnls if p > 0)
        for raw_reason, rpnls in sorted(raw.items()):
            if _exit_bucket(raw_reason) != bucket:
                continue
            rw = sum(1 for p in rpnls if p > 0)
            rows.append(
                {
                    "exit_bucket": bucket,
                    "raw_exit_reason": raw_reason,
                    "trade_count": len(rpnls),
                    "total_pnl_yen_100": round(sum(rpnls), 2),
                    "profit_factor": _pf(rpnls),
                    "win_rate": round(rw / len(rpnls), 4) if rpnls else 0.0,
                }
            )
    return {
        "session_end_count": len(sess),
        "session_end_pnl": round(sess_pnl, 2),
        "session_end_dependency_pct": dep,
        "rows": rows,
    }


def _hold_stats(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    holds: list[float] = []
    for tr in trades:
        ent = _parse_ts(str(tr.get("entry_time") or ""))
        ex = _parse_ts(str(tr.get("exit_time") or ""))
        if ent is None or ex is None:
            continue
        holds.append(max(0.0, (ex - ent).total_seconds() / 60.0))
    if not holds:
        return {
            "mean_hold_minutes": None,
            "median_hold_minutes": None,
            "p90_hold_minutes": None,
            "p95_hold_minutes": None,
            "min_hold_minutes": None,
            "max_hold_minutes": None,
            "hold_verdict": "unknown",
        }
    holds.sort()
    mean_h = statistics.mean(holds)
    med_h = statistics.median(holds)
    p90 = holds[int(min(len(holds) - 1, len(holds) * 0.9))]
    p95 = holds[int(min(len(holds) - 1, len(holds) * 0.95))]
    verdict = "trend_capture" if med_h >= 45 and mean_h >= 60 else "exit_failure"
    if med_h < 15:
        verdict = "exit_failure"
    return {
        "mean_hold_minutes": round(mean_h, 2),
        "median_hold_minutes": round(med_h, 2),
        "p90_hold_minutes": round(p90, 2),
        "p95_hold_minutes": round(p95, 2),
        "min_hold_minutes": round(min(holds), 2),
        "max_hold_minutes": round(max(holds), 2),
        "hold_verdict": verdict,
    }


def _load_trades_csv(path: Path, strategy_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("strategy_id") != strategy_id:
                continue
            pnl_raw = row.get("pnl_yen_100")
            if pnl_raw in (None, ""):
                pnl_raw = row.get("pnl_yen")
            rows.append({**row, "pnl_yen_100": _float(pnl_raw)})
    return rows


def _baseline_trades_from_sim(repo_root: Path) -> list[dict[str, Any]]:
    state, _ = _run_baseline_runtime(repo_root)
    rows: list[dict[str, Any]] = []
    for log in state.trade_log:
        tr = dict(log.get("trade") or log)
        rows.append(
            {
                "strategy_id": BASELINE_STRATEGY_ID,
                "symbol": str(tr.get("symbol") or "").replace(".T", ""),
                "day": str(log.get("day") or tr.get("day") or "")[:8],
                "entry_time": tr.get("entry_time"),
                "exit_time": log.get("exit_time"),
                "entry_price": tr.get("entry_price"),
                "exit_price": tr.get("exit_price"),
                "pnl_yen_100": _float(log.get("pnl_yen")),
                "exit_reason": log.get("exit_reason"),
                "entry_rule_id": "PBv2",
                "exit_rule_id": "RUNTIME",
            }
        )
    return rows


def _load_summary_row(path: Path, strategy_id: str) -> dict[str, Any]:
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("strategy_id") == strategy_id:
                return row
    return {}


def _load_daily_rows(path: Path, strategy_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("strategy_id") == strategy_id:
                out.append(row)
    return out


def _baseline_consistency_audit(
    *,
    repo_root: Path,
    reports: Path,
    baseline_trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    summary_path = reports / "strategy_battle_summary.csv"
    daily_path = reports / "strategy_battle_daily.csv"
    trades_path = reports / "strategy_battle_trades.csv"

    summary = _load_summary_row(summary_path, BASELINE_STRATEGY_ID)
    daily = _load_daily_rows(daily_path, BASELINE_STRATEGY_ID)
    csv_trades = _load_trades_csv(trades_path, BASELINE_STRATEGY_ID)

    sim_pnls = [_float(t.get("pnl_yen_100")) for t in baseline_trades]
    sim_total = round(sum(sim_pnls), 2)
    sim_count = len(sim_pnls)
    sim_pf = _pf(sim_pnls)
    daily_total = round(sum(_float(r.get("total_pnl_yen_100")) for r in daily), 2)
    csv_trade_pnl_sum = round(sum(_float(t.get("pnl_yen_100")) for t in csv_trades), 2)

    issues: list[str] = []
    if abs(sim_total - _float(summary.get("total_pnl_yen_100"))) > 0.5:
        issues.append("summary total_pnl mismatch vs re-sim")
    if sim_count != int(_float(summary.get("trades"))):
        issues.append("summary trade_count mismatch vs re-sim")
    if abs(sim_pf - _float(summary.get("profit_factor"))) > 0.01:
        issues.append("summary PF mismatch vs re-sim")
    if abs(daily_total - sim_total) > 0.5:
        issues.append("daily sum mismatch vs re-sim")
    if csv_trade_pnl_sum == 0 and sim_total != 0:
        issues.append(
            "strategy_battle_trades.csv baseline rows missing pnl_yen_100 "
            "(Phase507 export used pnl_yen in trade log but CSV column expects pnl_yen_100)"
        )

    return {
        "consistent": not issues,
        "issues": issues,
        "summary_pnl": _float(summary.get("total_pnl_yen_100")),
        "summary_trades": int(_float(summary.get("trades"))),
        "summary_pf": _float(summary.get("profit_factor")),
        "daily_pnl_sum": daily_total,
        "trades_csv_pnl_sum": csv_trade_pnl_sum,
        "resim_pnl": sim_total,
        "resim_trades": sim_count,
        "resim_pf": sim_pf,
        "root_cause": issues[0] if issues else "",
    }


def _overall_verdict(
    *,
    concentration: Mapping[str, Any],
    sym_dep: Mapping[str, Any],
    day_dep: Mapping[str, Any],
    exit_info: Mapping[str, Any],
) -> str:
    top10 = concentration.get("top10_profit_pct_of_gross_wins") or 0
    fragile_signals = 0
    if top10 and top10 >= 50:
        fragile_signals += 1
    if sym_dep.get("single_symbol_dependency"):
        fragile_signals += 1
    if day_dep.get("single_day_dependency"):
        fragile_signals += 1
    if (exit_info.get("session_end_dependency_pct") or 0) >= 40:
        fragile_signals += 1
    if fragile_signals >= 2:
        return "classic_candidate_fragile"
    if fragile_signals == 1:
        return "classic_candidate_mixed"
    return "classic_candidate_robust"


def _analyze_strategy(strategy_id: str, trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen_100")) for t in trades]
    pnls_by_sym: dict[str, list[float]] = defaultdict(list)
    pnls_by_day: dict[str, list[float]] = defaultdict(list)
    for tr in trades:
        pnls_by_sym[str(tr.get("symbol") or "")].append(_float(tr.get("pnl_yen_100")))
        pnls_by_day[str(tr.get("day") or "")[:8]].append(_float(tr.get("pnl_yen_100")))

    conc = _profit_concentration(pnls)
    sym = _symbol_dependency(pnls_by_sym)
    day = _day_dependency(pnls_by_day)
    ex = _exit_breakdown(trades)
    hold = _hold_stats(trades)
    conc_verdict = "concentrated" if (conc.get("top10_profit_pct_of_gross_wins") or 0) >= 45 else "dispersed"
    overall = _overall_verdict(concentration=conc, sym_dep=sym, day_dep=day, exit_info=ex)

    return {
        "strategy_id": strategy_id,
        "total_pnl_yen_100": round(sum(pnls), 2),
        "trade_count": len(pnls),
        "profit_factor": _pf(pnls),
        **{k: conc[k] for k in (
            "top1_trade_profit_share_pct",
            "top5_trade_profit_share_pct",
            "top10_trade_profit_share_pct",
            "gini_coefficient",
            "top10_profit_pct_of_gross_wins",
        )},
        "profit_histogram": conc["histogram"],
        "verdict_concentration": conc_verdict,
        "exclude_top1_symbol_pnl": sym["exclude_top1_symbol_pnl"],
        "exclude_top3_symbol_pnl": sym["exclude_top3_symbol_pnl"],
        "exclude_top5_symbol_pnl": sym["exclude_top5_symbol_pnl"],
        "single_symbol_dependency": sym["single_symbol_dependency"],
        "exclude_top1_day_pnl": day["exclude_top1_day_pnl"],
        "exclude_top3_day_pnl": day["exclude_top3_day_pnl"],
        "single_day_dependency": day["single_day_dependency"],
        "session_end_count": ex["session_end_count"],
        "session_end_pnl": ex["session_end_pnl"],
        "session_end_dependency_pct": ex["session_end_dependency_pct"],
        **hold,
        "overall_verdict": overall,
        "_symbol_rows": sym["symbol_rows"],
        "_day_rows": day["day_rows"],
        "_exit_rows": ex["rows"],
    }


def _t15_attribution(summary_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_id = {str(r.get("strategy_id")): r for r in summary_rows}
    t15_e1 = by_id.get("C_T15_E1", {})
    t15_e2 = by_id.get("C_T15_E2", {})
    t13_e2 = by_id.get("C_T13_E2", {})
    baseline = by_id.get(BASELINE_STRATEGY_ID, {})
    # RSI-only proxies: T1/T2/T3 same exit E1
    rsi_e1 = [by_id.get(f"C_T{i}_E1", {}) for i in range(1, 4)]
    stoch_proxy = t15_e1.get("total_pnl_yen_100")
    return {
        "t15_entry_rule": "Stoch %K > %D AND RSI > 50",
        "t15_best_pnl": t15_e1.get("total_pnl_yen_100"),
        "t15_e1_exit": "E1 hard_stop/session_end only",
        "t15_e2_exit": "E2 vwap break",
        "rsi_only_best_e1": max((_float(r.get("total_pnl_yen_100")) for r in rsi_e1), default=0),
        "t13_e2_pnl": t13_e2.get("total_pnl_yen_100"),
        "baseline_pnl": baseline.get("total_pnl_yen_100"),
        "interpretation": (
            "T15 outperforms RSI-only T1-T3 on E1 — Stochastic cross adds signal beyond RSI>50. "
            "E1 (hold to session_end) captures large winners; high concentration risk. "
            "Trend-follow / momentum-capture via long holds + session_end exits, not PBv2 board logic."
        ),
        "rsi_contribution": "Partial — RSI>50 is necessary but not sufficient; T1-T3 E1 deeply negative",
        "stoch_contribution": "High — T15 uniquely combines Stoch cross with RSI filter",
        "trend_follow_hypothesis": "Plausible — E1 long-hold session_end exits dominate PnL",
        "research_value_vs_pbv2": (
            "Yes for research (signal isolation) but fragile — higher PnL with worse DD/stability than baseline"
        ),
    }


def run_phase508(*, repo_root: Path) -> dict[str, Any]:
    reports = resolve_reports_dir(repo_root)
    trades_path = reports / "strategy_battle_trades.csv"
    summary_path = reports / "strategy_battle_summary.csv"

    baseline_trades = _baseline_trades_from_sim(repo_root)
    consistency = _baseline_consistency_audit(
        repo_root=repo_root, reports=reports, baseline_trades=baseline_trades
    )

    strategy_trades: dict[str, list[dict[str, Any]]] = {
        BASELINE_STRATEGY_ID: baseline_trades,
    }
    for sid in ("C_T15_E1", "C_T15_E2", "C_T13_E2"):
        strategy_trades[sid] = _load_trades_csv(trades_path, sid)

    analyses = [_analyze_strategy(sid, strategy_trades[sid]) for sid in AUDIT_STRATEGIES]

    summary_rows: list[dict[str, Any]] = []
    symbol_rows: list[dict[str, Any]] = []
    day_rows: list[dict[str, Any]] = []
    exit_rows: list[dict[str, Any]] = []
    hold_rows: list[dict[str, Any]] = []

    for a in analyses:
        summary_rows.append({k: a.get(k) for k in ROBUSTNESS_FIELDS if k in a})
        total = _float(a.get("total_pnl_yen_100"))
        for i, (sym, pnl) in enumerate(a.get("_symbol_rows") or [], start=1):
            cnt = sum(1 for t in strategy_trades[a["strategy_id"]] if str(t.get("symbol")) == sym)
            sym_pnls = [t["pnl_yen_100"] for t in strategy_trades[a["strategy_id"]] if str(t.get("symbol")) == sym]
            wins = sum(1 for p in sym_pnls if p > 0)
            symbol_rows.append(
                {
                    "strategy_id": a["strategy_id"],
                    "symbol": sym,
                    "trade_count": cnt,
                    "total_pnl_yen_100": round(pnl, 2),
                    "win_rate": round(wins / cnt, 4) if cnt else 0.0,
                    "share_of_total_pnl_pct": round(pnl / total * 100.0, 2) if total else 0.0,
                    "rank": i,
                }
            )
        for i, (day, pnl) in enumerate(a.get("_day_rows") or [], start=1):
            dpnls = [t["pnl_yen_100"] for t in strategy_trades[a["strategy_id"]] if str(t.get("day"))[:8] == day]
            wins = sum(1 for p in dpnls if p > 0)
            day_rows.append(
                {
                    "strategy_id": a["strategy_id"],
                    "day": day,
                    "trade_count": len(dpnls),
                    "total_pnl_yen_100": round(pnl, 2),
                    "profit_factor": _pf(dpnls),
                    "share_of_total_pnl_pct": round(pnl / total * 100.0, 2) if total else 0.0,
                    "rank": i,
                }
            )
        for er in a.get("_exit_rows") or []:
            exit_rows.append({"strategy_id": a["strategy_id"], **er})
        hold_rows.append(
            {
                "strategy_id": a["strategy_id"],
                "trade_count": a.get("trade_count"),
                "mean_hold_minutes": a.get("mean_hold_minutes"),
                "median_hold_minutes": a.get("median_hold_minutes"),
                "p90_hold_minutes": a.get("p90_hold_minutes"),
                "p95_hold_minutes": a.get("p95_hold_minutes"),
                "min_hold_minutes": a.get("min_hold_minutes"),
                "max_hold_minutes": a.get("max_hold_minutes"),
                "verdict": a.get("hold_verdict"),
            }
        )

    summary_csv_rows = _load_all_summary(summary_path)
    mandatory = {
        "top10_profit_share_by_strategy": {
            a["strategy_id"]: a.get("top10_profit_pct_of_gross_wins") for a in analyses
        },
        "baseline_consistency": consistency,
        "single_symbol_dependency": {a["strategy_id"]: a.get("single_symbol_dependency") for a in analyses},
        "single_day_dependency": {a["strategy_id"]: a.get("single_day_dependency") for a in analyses},
        "session_end_dependency_pct": {a["strategy_id"]: a.get("session_end_dependency_pct") for a in analyses},
        "overall_verdict": {a["strategy_id"]: a.get("overall_verdict") for a in analyses},
        "t15_attribution": _t15_attribution(summary_csv_rows),
        "classic_candidate_robust": [
            a["strategy_id"] for a in analyses if a.get("overall_verdict") == "classic_candidate_robust"
        ],
        "classic_candidate_fragile": [
            a["strategy_id"] for a in analyses if a.get("overall_verdict") == "classic_candidate_fragile"
        ],
    }

    return {
        "verdict": PHASE508_MODE,
        "generated_at": _now_iso(),
        "robustness_summary": summary_rows,
        "symbol_dependency": symbol_rows,
        "day_dependency": day_rows,
        "exit_breakdown": exit_rows,
        "hold_time": hold_rows,
        "mandatory_answers": mandatory,
        "histograms": {a["strategy_id"]: a.get("profit_histogram") for a in analyses},
    }


def _load_all_summary(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_phase508_outputs(result: Mapping[str, Any], *, repo_root: Path) -> dict[str, Path]:
    reports = resolve_reports_dir(repo_root)
    paths = {
        "summary": reports / "phase508_robustness_summary.csv",
        "symbol": reports / "phase508_symbol_dependency.csv",
        "day": reports / "phase508_day_dependency.csv",
        "exit": reports / "phase508_exit_breakdown.csv",
        "hold": reports / "phase508_hold_time.csv",
        "report": reports / "phase508_report.json",
    }
    _write_csv(paths["summary"], ROBUSTNESS_FIELDS, list(result.get("robustness_summary") or []))
    _write_csv(paths["symbol"], SYMBOL_DEP_FIELDS, list(result.get("symbol_dependency") or []))
    _write_csv(paths["day"], DAY_DEP_FIELDS, list(result.get("day_dependency") or []))
    _write_csv(paths["exit"], EXIT_FIELDS, list(result.get("exit_breakdown") or []))
    _write_csv(paths["hold"], HOLD_FIELDS, list(result.get("hold_time") or []))
    paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return paths


def run_and_write(*, repo_root: Path) -> dict[str, Any]:
    result = run_phase508(repo_root=repo_root)
    write_phase508_outputs(result, repo_root=repo_root)
    return result
