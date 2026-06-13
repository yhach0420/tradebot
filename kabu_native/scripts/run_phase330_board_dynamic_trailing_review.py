#!/usr/bin/env python3
"""
Phase330-lite: board-dynamic trailing_mfe activate/giveback counterfactual.

Output: phase330_board_dynamic_trailing_review.json
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
OUT = REPO / "kabu_native/results/reports/phase330_board_dynamic_trailing_review.json"
DAY = "20260608"
JST = ZoneInfo("Asia/Tokyo")

HARD_STOP_PCT = 1.20
BOARD_SPLIT_PCT = 47.62

SCENARIOS = {
    "A": {
        "label": "current_fixed",
        "activate_pct": 0.80,
        "giveback_frac": 0.50,
        "board_dynamic": False,
    },
    "B": {
        "label": "board_dynamic",
        "board_dynamic": True,
        "board_high": {"activate_pct": 1.00, "giveback_frac": 0.60},
        "board_low": {"activate_pct": 0.60, "giveback_frac": 0.40},
    },
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
    entry_imbalance_percentile: Optional[float]
    board_tier: str
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
    activate_pct_used: float
    giveback_frac_used: float
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


def _board_tier(imb: Optional[float]) -> str:
    if imb is None:
        return "board_low"
    return "board_high" if imb >= BOARD_SPLIT_PCT else "board_low"


def _trailing_params(trade: Trade, scenario: dict[str, Any]) -> tuple[float, float]:
    if not scenario.get("board_dynamic"):
        return float(scenario["activate_pct"]), float(scenario["giveback_frac"])
    tier = trade.board_tier
    block = scenario["board_high"] if tier == "board_high" else scenario["board_low"]
    return float(block["activate_pct"]), float(block["giveback_frac"])


def _load_trades() -> list[Trade]:
    from replay.pnl_yen import compute_pnl_yen_100

    out: list[Trade] = []
    for session_label, session_dir in SESSIONS.items():
        path = session_dir / "small_paper_events.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("event_type") != "observer_exit":
                    continue
                entry = _float(row.get("entry_price")) or 0.0
                exit_p = _float(row.get("exit_price")) or _float(row.get("current_price")) or 0.0
                pnl_pct = _float(row.get("pnl_pct"))
                if pnl_pct is None and entry > 0:
                    pnl_pct = (exit_p - entry) / entry * 100.0
                imb = _float(row.get("entry_imbalance_percentile"))
                out.append(
                    Trade(
                        session=session_label,
                        symbol=str(row.get("symbol") or ""),
                        entry_time=str(row.get("entry_time") or ""),
                        exit_time=str(row.get("exit_time") or row.get("event_time") or ""),
                        entry_price=entry,
                        exit_price=exit_p,
                        entry_imbalance_percentile=imb,
                        board_tier=_board_tier(imb),
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
    giveback_frac: float,
) -> SimResult:
    from replay.pnl_yen import compute_pnl_yen_100

    entry = trade.entry_price
    stop = entry * (1.0 - HARD_STOP_PCT / 100.0)
    peak_pnl = 0.0

    for _ts, px in path:
        pnl = _pnl_pct(entry, px)
        peak_pnl = max(peak_pnl, pnl)
        if px <= stop:
            return SimResult(
                exit_reason="stop_hit",
                exit_price=px,
                pnl_pct=pnl,
                pnl_yen_100=compute_pnl_yen_100(entry, px),
                activate_pct_used=activate_pct,
                giveback_frac_used=giveback_frac,
                simulation_method="tick_path",
            )
        if peak_pnl >= activate_pct and pnl <= peak_pnl * giveback_frac:
            return SimResult(
                exit_reason="trailing_mfe_exit",
                exit_price=px,
                pnl_pct=pnl,
                pnl_yen_100=compute_pnl_yen_100(entry, px),
                activate_pct_used=activate_pct,
                giveback_frac_used=giveback_frac,
                simulation_method="tick_path",
            )

    return SimResult(
        exit_reason=trade.actual_exit_reason,
        exit_price=trade.exit_price,
        pnl_pct=trade.actual_pnl_pct,
        pnl_yen_100=trade.actual_pnl_yen_100,
        activate_pct_used=activate_pct,
        giveback_frac_used=giveback_frac,
        simulation_method="tick_path_fallback_actual",
    )


def _simulate_trade(
    trade: Trade,
    candidates: list[dict[str, Any]],
    *,
    scenario: dict[str, Any],
) -> SimResult:
    activate, giveback = _trailing_params(trade, scenario)
    if not scenario.get("board_dynamic") and scenario.get("label") == "current_fixed":
        # Scenario A reproduces live when no counterfactual trailing change on path
        pass
    path = _price_path_for_trade(trade, candidates)
    if len(path) >= 2:
        return _simulate_on_path(trade, path, activate_pct=activate, giveback_frac=giveback)
    return SimResult(
        exit_reason=trade.actual_exit_reason,
        exit_price=trade.exit_price,
        pnl_pct=trade.actual_pnl_pct,
        pnl_yen_100=trade.actual_pnl_yen_100,
        activate_pct_used=activate,
        giveback_frac_used=giveback,
        simulation_method="no_tick_path",
    )


def _session_close_count(exit_counts: Counter) -> int:
    return int(exit_counts.get("morning_session_close", 0)) + int(
        exit_counts.get("afternoon_session_close", 0)
    )


def _metrics(
    trades: list[Trade],
    results: list[SimResult],
    *,
    baseline_results: Optional[list[SimResult]] = None,
) -> dict[str, Any]:
    yens = [r.pnl_yen_100 for r in results]
    wins = [y for y in yens if y > 0]
    losses = [y for y in yens if y < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
    exit_counts = Counter(r.exit_reason for r in results)

    actual_stop = {i for i, t in enumerate(trades) if t.actual_exit_reason == "stop_hit"}
    cf_stop = {i for i, r in enumerate(results) if r.exit_reason == "stop_hit"}
    stop_avoided = len(actual_stop - cf_stop)

    premature = 0
    premature_yen = 0.0
    trailing_increased = 0
    trailing_increased_yen = 0.0

    for i, (t, r) in enumerate(zip(trades, results)):
        delta = r.pnl_yen_100 - t.actual_pnl_yen_100
        if r.exit_reason == "trailing_mfe_exit" and delta < -0.01:
            premature += 1
            premature_yen += delta
        if r.exit_reason == "trailing_mfe_exit" and delta > 0.01:
            trailing_increased += 1
            trailing_increased_yen += delta
        elif t.actual_exit_reason == "trailing_mfe_exit" and delta > 0.01:
            trailing_increased += 1
            trailing_increased_yen += delta

    board_breakdown: dict[str, Any] = {}
    for tier in ("board_high", "board_low"):
        idxs = [i for i, t in enumerate(trades) if t.board_tier == tier]
        tier_yens = [results[i].pnl_yen_100 for i in idxs]
        board_breakdown[tier] = {
            "trade_count": len(idxs),
            "total_pnl_yen_100": round(sum(tier_yens), 2) if tier_yens else 0.0,
            "avg_pnl_yen_100": round(statistics.mean(tier_yens), 2) if tier_yens else None,
            "trailing_mfe_exit_count": sum(
                1 for i in idxs if results[i].exit_reason == "trailing_mfe_exit"
            ),
            "stop_hit_count": sum(1 for i in idxs if results[i].exit_reason == "stop_hit"),
        }

    vs_a = None
    if baseline_results is not None:
        vs_a = round(sum(yens) - sum(r.pnl_yen_100 for r in baseline_results), 2)

    return {
        "trade_count": len(trades),
        "total_pnl_yen_100": round(sum(yens), 2),
        "avg_pnl_yen_100": round(statistics.mean(yens), 2) if yens else None,
        "win_rate": round(len(wins) / len(yens), 4) if yens else None,
        "profit_factor_yen_100": pf,
        "exit_reason_counts": dict(sorted(exit_counts.items())),
        "stop_hit_count": int(exit_counts.get("stop_hit", 0)),
        "trailing_mfe_exit_count": int(exit_counts.get("trailing_mfe_exit", 0)),
        "session_close_count": _session_close_count(exit_counts),
        "overlap_replaced_review_count": int(exit_counts.get("overlap_replaced_review", 0)),
        "stop_hit_avoided_count": stop_avoided,
        "premature_profit_taking_count": premature,
        "premature_profit_taking_yen_100": round(premature_yen, 2),
        "trailing_profit_increased_count": trailing_increased,
        "trailing_profit_increased_yen_100": round(trailing_increased_yen, 2),
        "board_tier_breakdown": board_breakdown,
        "vs_scenario_A_improvement_yen": vs_a,
    }


def _verdict(metrics_a: dict[str, Any], metrics_b: dict[str, Any]) -> dict[str, Any]:
    yen_up = float(metrics_b["total_pnl_yen_100"]) > float(metrics_a["total_pnl_yen_100"])
    pf_a = metrics_a.get("profit_factor_yen_100")
    pf_b = metrics_b.get("profit_factor_yen_100")
    pf_up = pf_b is not None and pf_a is not None and float(pf_b) > float(pf_a)
    stop_down = int(metrics_b["stop_hit_count"]) <= int(metrics_a["stop_hit_count"])
    delta = float(metrics_b["total_pnl_yen_100"]) - float(metrics_a["total_pnl_yen_100"])
    useful = yen_up and (pf_up or stop_down)
    return {
        "board_dynamic_trailing_useful": useful,
        "criteria": {
            "total_pnl_yen_100_improved_vs_A": yen_up,
            "profit_factor_improved_vs_A": pf_up,
            "stop_hit_not_increased": int(metrics_b["stop_hit_count"]) <= int(metrics_a["stop_hit_count"]),
            "stop_hit_avoided_vs_actual": metrics_b["stop_hit_avoided_count"],
        },
        "delta_vs_A_yen": round(delta, 2),
        "conclusion": (
            f"Board-dynamic trailing improves PnL by {delta:+.0f} yen vs fixed 0.8%/50%"
            if useful
            else f"Board-dynamic trailing does not improve vs A (delta {delta:+.0f} yen)"
        ),
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    trades = _load_trades()
    if not trades:
        print("no trades", file=sys.stderr)
        return 1

    session_candidates = {k: _load_session_candidates(v) for k, v in SESSIONS.items()}
    scenario_results: dict[str, list[SimResult]] = {}
    scenario_metrics: dict[str, dict[str, Any]] = {}

    for key, meta in SCENARIOS.items():
        results = [
            _simulate_trade(t, session_candidates.get(t.session, []), scenario=meta)
            for t in trades
        ]
        scenario_results[key] = results

    metrics_a = _metrics(trades, scenario_results["A"])
    metrics_b = _metrics(trades, scenario_results["B"], baseline_results=scenario_results["A"])
    scenario_metrics = {"A": metrics_a, "B": metrics_b}

    report = {
        "phase": 330,
        "title": "board_dynamic_trailing_review",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": (
            "analysis only; trailing activate/giveback board adjustment only; "
            "no entry/stop/new exit/vwap/hold time"
        ),
        "target_date": DAY,
        "trade_count": len(trades),
        "board_split": {
            "threshold_percentile": BOARD_SPLIT_PCT,
            "board_high": "entry_imbalance_percentile >= 47.62",
            "board_low": "entry_imbalance_percentile < 47.62",
            "board_high_count": sum(1 for t in trades if t.board_tier == "board_high"),
            "board_low_count": sum(1 for t in trades if t.board_tier == "board_low"),
        },
        "scenarios": SCENARIOS,
        "results_by_scenario": scenario_metrics,
        "verdict": _verdict(metrics_a, metrics_b),
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"A_yen={metrics_a['total_pnl_yen_100']} B_yen={metrics_b['total_pnl_yen_100']} "
        f"useful={report['verdict']['board_dynamic_trailing_useful']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
