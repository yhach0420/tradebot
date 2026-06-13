#!/usr/bin/env python3
"""
Phase323-lite: trailing_mfe activation sensitivity on 20260608 all observer_exit trades.

Counterfactual analysis only — no logic changes.
Output: phase323_trailing_activation_full_day_review.json
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase323_trailing_activation_full_day_review.json"
DAY = "20260608"
JST = ZoneInfo("Asia/Tokyo")

HARD_STOP_PCT = 1.20
GIVEBACK_FRAC = 0.50
SCENARIOS = {
    "A": {"label": "current", "activate_pct": 0.80},
    "B": {"label": "activate_0p6", "activate_pct": 0.60},
    "C": {"label": "activate_0p4", "activate_pct": 0.40},
}

SESSIONS = {
    "am": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_080642",
    "pm": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_122548",
}


@dataclass
class Trade:
    session: str
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    actual_pnl_pct: float
    actual_pnl_yen_100: float
    actual_exit_reason: str
    peak_mfe_pct: float


@dataclass
class SimResult:
    exit_reason: str
    exit_price: float
    pnl_pct: float
    pnl_yen_100: float
    sim_peak_pnl_pct: float
    changed_from_actual: bool
    simulation_method: str


def _bootstrap() -> None:
    src = REPO / "kabu_native" / "src"
    for p in (src, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _parse_dt(raw: str) -> datetime:
    s = str(raw or "").strip()
    if not s:
        return datetime.min.replace(tzinfo=JST)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.min.replace(tzinfo=JST)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _load_all_observer_exits(session_label: str, session_dir: Path) -> list[Trade]:
    from replay.pnl_yen import compute_pnl_yen_100

    path = session_dir / "small_paper_events.csv"
    if not path.is_file():
        return []
    out: list[Trade] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("event_type") != "observer_exit":
                continue
            entry = _float(row.get("entry_price")) or 0.0
            exit_p = _float(row.get("exit_price")) or _float(row.get("current_price")) or 0.0
            pnl_pct = _float(row.get("pnl_pct"))
            if pnl_pct is None and entry > 0:
                pnl_pct = (exit_p - entry) / entry * 100.0
            out.append(
                Trade(
                    session=session_label,
                    symbol=str(row.get("symbol") or ""),
                    entry_time=str(row.get("entry_time") or ""),
                    exit_time=str(row.get("exit_time") or row.get("event_time") or ""),
                    entry_price=entry,
                    exit_price=exit_p,
                    actual_pnl_pct=float(pnl_pct or 0.0),
                    actual_pnl_yen_100=compute_pnl_yen_100(entry, exit_p),
                    actual_exit_reason=str(row.get("exit_reason") or "unknown"),
                    peak_mfe_pct=float(
                        _float(row.get("peak_mfe_pct"))
                        or _float(row.get("rolling_mfe_pct"))
                        or 0.0
                    ),
                )
            )
    return out


def _load_session_candidates(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "small_paper_events.csv"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("event_type") != "candidate":
                continue
            px = _float(row.get("current_price"))
            if px is None or px <= 0:
                continue
            rows.append(row)
    return rows


def _price_path_for_trade(
    trade: Trade,
    candidates: list[dict[str, Any]],
) -> list[tuple[datetime, float]]:
    ent = _parse_dt(trade.entry_time)
    ex = _parse_dt(trade.exit_time)
    path: list[tuple[datetime, float]] = []
    for row in candidates:
        if str(row.get("symbol") or "") != trade.symbol:
            continue
        ts = _parse_dt(str(row.get("entry_time") or row.get("event_time") or ""))
        if ts < ent or ts > ex:
            continue
        px = _float(row.get("current_price"))
        if px is None or px <= 0:
            continue
        path.append((ts, float(px)))
    path.sort(key=lambda x: x[0])
    if not path:
        path = [(ent, trade.entry_price), (ex, trade.exit_price)]
    else:
        if path[0][0] > ent:
            path.insert(0, (ent, trade.entry_price))
        if path[-1][0] < ex:
            path.append((ex, trade.exit_price))
    return path


def _pnl_pct(entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    return round((price - entry) / entry * 100.0, 4)


def _simulate_on_path(
    trade: Trade,
    path: list[tuple[datetime, float]],
    *,
    activate_pct: float,
) -> SimResult:
    from replay.pnl_yen import compute_pnl_yen_100

    entry = trade.entry_price
    stop = entry * (1.0 - HARD_STOP_PCT / 100.0)
    peak_pnl = 0.0

    for _ts, px in path:
        pnl = _pnl_pct(entry, px)
        peak_pnl = max(peak_pnl, pnl)
        if px <= stop:
            reason = "stop_hit"
            yen = compute_pnl_yen_100(entry, px)
            return SimResult(
                exit_reason=reason,
                exit_price=px,
                pnl_pct=pnl,
                pnl_yen_100=yen,
                sim_peak_pnl_pct=round(peak_pnl, 4),
                changed_from_actual=reason != trade.actual_exit_reason
                or abs(yen - trade.actual_pnl_yen_100) > 0.01,
                simulation_method="tick_path",
            )
        if peak_pnl >= activate_pct and pnl <= peak_pnl * GIVEBACK_FRAC:
            reason = "trailing_mfe_exit"
            yen = compute_pnl_yen_100(entry, px)
            return SimResult(
                exit_reason=reason,
                exit_price=px,
                pnl_pct=pnl,
                pnl_yen_100=yen,
                sim_peak_pnl_pct=round(peak_pnl, 4),
                changed_from_actual=reason != trade.actual_exit_reason
                or abs(yen - trade.actual_pnl_yen_100) > 0.01,
                simulation_method="tick_path",
            )

    return SimResult(
        exit_reason=trade.actual_exit_reason,
        exit_price=trade.exit_price,
        pnl_pct=trade.actual_pnl_pct,
        pnl_yen_100=trade.actual_pnl_yen_100,
        sim_peak_pnl_pct=round(max(peak_pnl, trade.peak_mfe_pct), 4),
        changed_from_actual=False,
        simulation_method="tick_path_fallback_actual",
    )


def _simulate_trade(
    trade: Trade,
    candidates: list[dict[str, Any]],
    *,
    activate_pct: float,
) -> SimResult:
    path = _price_path_for_trade(trade, candidates)
    if len(path) >= 2:
        return _simulate_on_path(trade, path, activate_pct=activate_pct)
    return SimResult(
        exit_reason=trade.actual_exit_reason,
        exit_price=trade.exit_price,
        pnl_pct=trade.actual_pnl_pct,
        pnl_yen_100=trade.actual_pnl_yen_100,
        sim_peak_pnl_pct=trade.peak_mfe_pct,
        changed_from_actual=False,
        simulation_method="no_tick_path",
    )


def _metrics(
    trades: list[Trade],
    results: list[SimResult],
) -> dict[str, Any]:
    pnls = [r.pnl_pct for r in results]
    yens = [r.pnl_yen_100 for r in results]
    wins = [y for y in yens if y > 0]
    losses = [y for y in yens if y < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None

    exit_counts = Counter(r.exit_reason for r in results)
    actual_stop_ids = {i for i, t in enumerate(trades) if t.actual_exit_reason == "stop_hit"}
    cf_stop_ids = {i for i, r in enumerate(results) if r.exit_reason == "stop_hit"}
    stop_avoided = len(actual_stop_ids - cf_stop_ids)

    premature = 0
    premature_yen = 0.0
    trailing_eroded = 0
    trailing_eroded_yen = 0.0
    for t, r in zip(trades, results):
        delta = r.pnl_yen_100 - t.actual_pnl_yen_100
        if r.exit_reason == "trailing_mfe_exit" and delta < -0.01:
            premature += 1
            premature_yen += delta
        if t.actual_exit_reason == "trailing_mfe_exit" and delta < -0.01:
            trailing_eroded += 1
            trailing_eroded_yen += delta

    actual_yen = sum(t.actual_pnl_yen_100 for t in trades)
    cf_yen = sum(yens)

    return {
        "trade_count": len(trades),
        "total_pnl_yen_100": round(cf_yen, 2),
        "avg_pnl_yen_100": round(statistics.mean(yens), 2) if yens else None,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
        "win_rate": round(len(wins) / len(yens), 4) if yens else None,
        "profit_factor_yen_100": pf,
        "gross_profit_yen_100": round(gross_profit, 2),
        "gross_loss_yen_100": round(gross_loss, 2),
        "exit_reason_counts": dict(sorted(exit_counts.items())),
        "trailing_mfe_exit_count": int(exit_counts.get("trailing_mfe_exit", 0)),
        "stop_hit_count": int(exit_counts.get("stop_hit", 0)),
        "stop_hit_avoided_count": stop_avoided,
        "stop_hit_new_count": len(cf_stop_ids - actual_stop_ids),
        "premature_profit_taking_count": premature,
        "premature_profit_taking_yen_100": round(premature_yen, 2),
        "existing_trailing_mfe_exit_eroded_count": trailing_eroded,
        "existing_trailing_mfe_exit_eroded_yen_100": round(trailing_eroded_yen, 2),
        "vs_actual_total_pnl_yen_improvement": round(cf_yen - actual_yen, 2),
        "changed_from_actual_count": sum(1 for r in results if r.changed_from_actual),
    }


def _actual_baseline(trades: list[Trade]) -> dict[str, Any]:
    yens = [t.actual_pnl_yen_100 for t in trades]
    pnls = [t.actual_pnl_pct for t in trades]
    wins = [y for y in yens if y > 0]
    losses = [y for y in yens if y < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
    exit_counts = Counter(t.actual_exit_reason for t in trades)
    return {
        "trade_count": len(trades),
        "total_pnl_yen_100": round(sum(yens), 2),
        "avg_pnl_yen_100": round(statistics.mean(yens), 2),
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(statistics.mean(pnls), 4),
        "win_rate": round(len(wins) / len(yens), 4),
        "profit_factor_yen_100": pf,
        "exit_reason_counts": dict(sorted(exit_counts.items())),
        "trailing_mfe_exit_count": int(exit_counts.get("trailing_mfe_exit", 0)),
        "stop_hit_count": int(exit_counts.get("stop_hit", 0)),
    }


def _trade_row(trade: Trade, result: SimResult, *, activate_pct: float) -> dict[str, Any]:
    return {
        "session": trade.session,
        "symbol": trade.symbol,
        "entry_time": trade.entry_time,
        "exit_time": trade.exit_time,
        "actual_exit_reason": trade.actual_exit_reason,
        "actual_pnl_pct": round(trade.actual_pnl_pct, 4),
        "actual_pnl_yen_100": round(trade.actual_pnl_yen_100, 2),
        "peak_mfe_pct": round(trade.peak_mfe_pct, 4),
        "activate_pct": activate_pct,
        "counterfactual_exit_reason": result.exit_reason,
        "counterfactual_exit_price": round(result.exit_price, 2),
        "counterfactual_pnl_pct": round(result.pnl_pct, 4),
        "counterfactual_pnl_yen_100": round(result.pnl_yen_100, 2),
        "pnl_yen_delta": round(result.pnl_yen_100 - trade.actual_pnl_yen_100, 2),
        "stop_hit_avoided": trade.actual_exit_reason == "stop_hit" and result.exit_reason != "stop_hit",
        "premature_profit_taking": (
            result.exit_reason == "trailing_mfe_exit"
            and result.pnl_yen_100 < trade.actual_pnl_yen_100 - 0.01
        ),
        "existing_trailing_eroded": (
            trade.actual_exit_reason == "trailing_mfe_exit"
            and result.pnl_yen_100 < trade.actual_pnl_yen_100 - 0.01
        ),
        "simulation_method": result.simulation_method,
    }


def _pick_best(
    scenario_metrics: dict[str, dict[str, Any]],
    actual: dict[str, Any],
) -> dict[str, Any]:
    ranked = sorted(
        scenario_metrics.items(),
        key=lambda kv: float(kv[1]["total_pnl_yen_100"]),
        reverse=True,
    )
    best_key, best = ranked[0]
    baseline_a = scenario_metrics["A"]

    comparisons = []
    for key, m in scenario_metrics.items():
        comparisons.append(
            {
                "scenario": key,
                "activate_pct": SCENARIOS[key]["activate_pct"],
                "total_pnl_yen_100": m["total_pnl_yen_100"],
                "vs_actual_improvement": m["vs_actual_total_pnl_yen_improvement"],
                "vs_A_improvement": round(
                    float(m["total_pnl_yen_100"]) - float(baseline_a["total_pnl_yen_100"]), 2
                ),
                "stop_hit_avoided": m["stop_hit_avoided_count"],
                "premature_profit_taking_count": m["premature_profit_taking_count"],
                "existing_trailing_eroded_count": m["existing_trailing_mfe_exit_eroded_count"],
            }
        )

    improves_over_actual = float(best["total_pnl_yen_100"]) > float(actual["total_pnl_yen_100"])
    improves_over_a = float(best["total_pnl_yen_100"]) > float(baseline_a["total_pnl_yen_100"])

    return {
        "best_activate_pct": SCENARIOS[best_key]["activate_pct"],
        "best_scenario": best_key,
        "improves_over_actual_live": improves_over_actual,
        "improves_over_scenario_A": improves_over_a,
        "ranking_by_total_pnl_yen_100": [
            {
                "scenario": k,
                "activate_pct": SCENARIOS[k]["activate_pct"],
                "total_pnl_yen_100": m["total_pnl_yen_100"],
            }
            for k, m in ranked
        ],
        "scenario_comparisons": comparisons,
        "conclusion": _conclusion(best_key, best, actual, baseline_a, improves_over_actual),
    }


def _conclusion(
    best_key: str,
    best: dict[str, Any],
    actual: dict[str, Any],
    baseline_a: dict[str, Any],
    improves_over_actual: bool,
) -> str:
    act_pct = SCENARIOS[best_key]["activate_pct"]
    delta = float(best["total_pnl_yen_100"]) - float(actual["total_pnl_yen_100"])
    if improves_over_actual:
        return (
            f"Lowering activate to {act_pct}% improves full-day total_pnl_yen_100 by "
            f"{delta:+.0f} yen vs actual live ({actual['total_pnl_yen_100']} → {best['total_pnl_yen_100']})"
        )
    if best_key == "A":
        return "Current 0.8% activate is best among tested; lowering threshold does not improve full-day PnL"
    return (
        f"Best tested activate is {act_pct}% but full-day PnL does not beat actual live "
        f"({best['total_pnl_yen_100']} vs {actual['total_pnl_yen_100']})"
    )


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    trades: list[Trade] = []
    session_candidates: dict[str, list[dict[str, Any]]] = {}
    for label, session_dir in SESSIONS.items():
        trades.extend(_load_all_observer_exits(label, session_dir))
        session_candidates[label] = _load_session_candidates(session_dir)

    if not trades:
        print("no observer_exit trades found", file=sys.stderr)
        return 1

    actual = _actual_baseline(trades)
    scenario_blocks: dict[str, Any] = {}
    scenario_metrics: dict[str, dict[str, Any]] = {}

    for key, meta in SCENARIOS.items():
        activate = float(meta["activate_pct"])
        results: list[SimResult] = []
        details: list[dict[str, Any]] = []
        for trade in trades:
            cands = session_candidates.get(trade.session, [])
            sim = _simulate_trade(trade, cands, activate_pct=activate)
            results.append(sim)
            details.append(_trade_row(trade, sim, activate_pct=activate))

        metrics = _metrics(trades, results)
        scenario_metrics[key] = metrics
        scenario_blocks[key] = {
            "label": meta["label"],
            "activate_pct": activate,
            "metrics": metrics,
            "trades": details,
        }

    verdict = _pick_best(scenario_metrics, actual)

    report = {
        "phase": 323,
        "title": "trailing_activation_full_day_review",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": (
            "20260608 all observer_exit trades; counterfactual trailing activate sensitivity; "
            "no entry/stop/giveback/logic changes; analysis only"
        ),
        "target_date": DAY,
        "sessions": {k: str(v.relative_to(REPO)).replace("\\", "/") for k, v in SESSIONS.items()},
        "policy_fixed": {
            "hard_stop_pct": HARD_STOP_PCT,
            "trailing_mfe_giveback_frac": GIVEBACK_FRAC,
        },
        "scenarios": {
            k: {"activate_pct": v["activate_pct"], "label": v["label"]} for k, v in SCENARIOS.items()
        },
        "trade_count": len(trades),
        "actual_live_baseline": actual,
        "results_by_scenario": {k: {"label": v["label"], "metrics": v["metrics"]} for k, v in scenario_blocks.items()},
        "verdict": verdict,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"trades={len(trades)} actual_yen={actual['total_pnl_yen_100']} best={verdict['best_activate_pct']}%")
    for key in ("A", "B", "C"):
        m = scenario_metrics[key]
        print(
            f"  {key} activate={SCENARIOS[key]['activate_pct']}% "
            f"total_yen={m['total_pnl_yen_100']} PF={m['profit_factor_yen_100']} "
            f"stop={m['stop_hit_count']} trail={m['trailing_mfe_exit_count']} "
            f"avoided={m['stop_hit_avoided_count']} premature={m['premature_profit_taking_count']}"
        )
    print(f"conclusion: {verdict['conclusion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
