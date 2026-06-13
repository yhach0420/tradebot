#!/usr/bin/env python3
"""
Phase327-lite: VWAP contraction current_vwap_dev_pct threshold sensitivity.

Fixed: entry_vwap_dev_pct > 0.5%, stop/trailing unchanged.
Output: phase327_vwap_contraction_threshold_review.json
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
OUT = REPO / "kabu_native/results/reports/phase327_vwap_contraction_threshold_review.json"
DAY = "20260608"
JST = ZoneInfo("Asia/Tokyo")

ENTRY_VWAP_DEV_MIN_PCT = 0.5
HARD_STOP_PCT = 1.20
TRAILING_ACTIVATE_PCT = 0.80
GIVEBACK_FRAC = 0.50

SCENARIOS = {
    "A": {"label": "current_lte_0p5", "current_vwap_dev_lte_pct": 0.5},
    "B": {"label": "current_lte_0p2_phase325", "current_vwap_dev_lte_pct": 0.2},
    "C": {"label": "current_lte_0p0", "current_vwap_dev_lte_pct": 0.0},
    "D": {"label": "current_lte_neg0p2", "current_vwap_dev_lte_pct": -0.2},
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
    entry_vwap_dev_pct: Optional[float]
    actual_pnl_pct: float
    actual_pnl_yen_100: float
    actual_exit_reason: str


@dataclass
class SimResult:
    exit_reason: str
    exit_price: float
    pnl_pct: float
    pnl_yen_100: float
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


def _vwap_at_entry(entry_price: float, entry_vwap_dev_pct: Optional[float]) -> Optional[float]:
    if entry_vwap_dev_pct is None or entry_price <= 0:
        return None
    return entry_price / (1.0 + entry_vwap_dev_pct / 100.0)


def _current_vwap_dev_pct(price: float, vwap_ref: float) -> float:
    if vwap_ref <= 0:
        return 0.0
    return round((price - vwap_ref) / vwap_ref * 100.0, 4)


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
                out.append(
                    Trade(
                        session=session_label,
                        symbol=str(row.get("symbol") or ""),
                        entry_time=str(row.get("entry_time") or ""),
                        exit_time=str(row.get("exit_time") or row.get("event_time") or ""),
                        entry_price=entry,
                        exit_price=exit_p,
                        entry_vwap_dev_pct=_float(row.get("entry_vwap_dev_pct")),
                        actual_pnl_pct=float(pnl_pct or 0.0),
                        actual_pnl_yen_100=compute_pnl_yen_100(entry, exit_p),
                        actual_exit_reason=str(row.get("exit_reason") or "unknown"),
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


def _contraction_fires(
    *,
    entry_vwap_dev_pct: Optional[float],
    vwap_ref: Optional[float],
    price: float,
    current_threshold_pct: float,
) -> bool:
    if vwap_ref is None or entry_vwap_dev_pct is None:
        return False
    if entry_vwap_dev_pct <= ENTRY_VWAP_DEV_MIN_PCT:
        return False
    return _current_vwap_dev_pct(price, vwap_ref) <= current_threshold_pct


def _simulate_on_path(
    trade: Trade,
    path: list[tuple[datetime, float]],
    *,
    current_threshold_pct: float,
) -> SimResult:
    from replay.pnl_yen import compute_pnl_yen_100

    entry = trade.entry_price
    stop = entry * (1.0 - HARD_STOP_PCT / 100.0)
    vwap_ref = _vwap_at_entry(entry, trade.entry_vwap_dev_pct)
    peak_pnl = 0.0

    for i, (_ts, px) in enumerate(path):
        pnl = _pnl_pct(entry, px)
        peak_pnl = max(peak_pnl, pnl)

        if px <= stop:
            return SimResult(
                exit_reason="stop_hit",
                exit_price=px,
                pnl_pct=pnl,
                pnl_yen_100=compute_pnl_yen_100(entry, px),
                simulation_method="tick_path",
            )

        if peak_pnl >= TRAILING_ACTIVATE_PCT and pnl <= peak_pnl * GIVEBACK_FRAC:
            return SimResult(
                exit_reason="trailing_mfe_exit",
                exit_price=px,
                pnl_pct=pnl,
                pnl_yen_100=compute_pnl_yen_100(entry, px),
                simulation_method="tick_path",
            )

        if i > 0 and _contraction_fires(
            entry_vwap_dev_pct=trade.entry_vwap_dev_pct,
            vwap_ref=vwap_ref,
            price=px,
            current_threshold_pct=current_threshold_pct,
        ):
            return SimResult(
                exit_reason="vwap_contraction_exit",
                exit_price=px,
                pnl_pct=pnl,
                pnl_yen_100=compute_pnl_yen_100(entry, px),
                simulation_method="tick_path",
            )

    return SimResult(
        exit_reason=trade.actual_exit_reason,
        exit_price=trade.exit_price,
        pnl_pct=trade.actual_pnl_pct,
        pnl_yen_100=trade.actual_pnl_yen_100,
        simulation_method="tick_path_fallback_actual",
    )


def _simulate_trade(
    trade: Trade,
    candidates: list[dict[str, Any]],
    *,
    current_threshold_pct: float,
) -> SimResult:
    path = _price_path_for_trade(trade, candidates)
    if len(path) >= 2:
        return _simulate_on_path(trade, path, current_threshold_pct=current_threshold_pct)
    return SimResult(
        exit_reason=trade.actual_exit_reason,
        exit_price=trade.exit_price,
        pnl_pct=trade.actual_pnl_pct,
        pnl_yen_100=trade.actual_pnl_yen_100,
        simulation_method="no_tick_path",
    )


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
        "win_rate": round(len(wins) / len(yens), 4),
        "profit_factor_yen_100": pf,
        "stop_hit_count": int(exit_counts.get("stop_hit", 0)),
        "trailing_mfe_exit_count": int(exit_counts.get("trailing_mfe_exit", 0)),
        "vwap_contraction_exit_count": 0,
    }


def _metrics(
    trades: list[Trade],
    results: list[SimResult],
    *,
    baseline: dict[str, Any],
) -> dict[str, Any]:
    yens = [r.pnl_yen_100 for r in results]
    wins = [y for y in yens if y > 0]
    losses = [y for y in yens if y < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
    exit_counts = Counter(r.exit_reason for r in results)
    actual_yen = sum(t.actual_pnl_yen_100 for t in trades)

    premature = 0
    premature_yen = 0.0
    for t, r in zip(trades, results):
        delta = r.pnl_yen_100 - t.actual_pnl_yen_100
        if delta < -0.01 and (
            r.exit_reason == "vwap_contraction_exit"
            or (t.actual_pnl_yen_100 > 0 and r.pnl_yen_100 < t.actual_pnl_yen_100)
        ):
            premature += 1
            premature_yen += delta

    return {
        "trade_count": len(trades),
        "total_pnl_yen_100": round(sum(yens), 2),
        "avg_pnl_yen_100": round(statistics.mean(yens), 2) if yens else None,
        "win_rate": round(len(wins) / len(yens), 4) if yens else None,
        "profit_factor_yen_100": pf,
        "exit_reason_counts": dict(sorted(exit_counts.items())),
        "stop_hit_count": int(exit_counts.get("stop_hit", 0)),
        "trailing_mfe_exit_count": int(exit_counts.get("trailing_mfe_exit", 0)),
        "vwap_contraction_exit_count": int(exit_counts.get("vwap_contraction_exit", 0)),
        "stop_hit_reduction_vs_actual": int(baseline["stop_hit_count"])
        - int(exit_counts.get("stop_hit", 0)),
        "trailing_mfe_exit_reduction_vs_actual": int(baseline["trailing_mfe_exit_count"])
        - int(exit_counts.get("trailing_mfe_exit", 0)),
        "premature_profit_taking_count": premature,
        "premature_profit_taking_yen_100": round(premature_yen, 2),
        "vs_actual_total_pnl_yen_improvement": round(sum(yens) - actual_yen, 2),
    }


def _pick_best(scenario_metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(
        scenario_metrics.items(),
        key=lambda kv: float(kv[1]["total_pnl_yen_100"]),
        reverse=True,
    )
    best_key, best = ranked[0]
    threshold_map = {k: v["current_vwap_dev_lte_pct"] for k, v in SCENARIOS.items()}
    return {
        "best_current_vwap_dev_pct": threshold_map[best_key],
        "best_scenario": best_key,
        "ranking_by_total_pnl_yen_100": [
            {
                "scenario": k,
                "current_vwap_dev_lte_pct": threshold_map[k],
                "total_pnl_yen_100": m["total_pnl_yen_100"],
                "vs_actual_improvement": m["vs_actual_total_pnl_yen_improvement"],
            }
            for k, m in ranked
        ],
        "scenario_comparisons": [
            {
                "scenario": k,
                "current_vwap_dev_lte_pct": threshold_map[k],
                "label": SCENARIOS[k]["label"],
                **{key: scenario_metrics[k][key] for key in (
                    "total_pnl_yen_100",
                    "profit_factor_yen_100",
                    "win_rate",
                    "stop_hit_reduction_vs_actual",
                    "vwap_contraction_exit_count",
                    "trailing_mfe_exit_reduction_vs_actual",
                    "premature_profit_taking_count",
                    "premature_profit_taking_yen_100",
                    "vs_actual_total_pnl_yen_improvement",
                )},
            }
            for k in SCENARIOS
        ],
        "conclusion": (
            f"Best current_vwap_dev_pct threshold: {threshold_map[best_key]}% "
            f"(total_pnl_yen_100 {best['total_pnl_yen_100']}, "
            f"vs actual {best['vs_actual_total_pnl_yen_improvement']:+.0f})"
        ),
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    trades = _load_trades()
    if not trades:
        print("no trades", file=sys.stderr)
        return 1

    baseline = _actual_baseline(trades)
    session_candidates = {k: _load_session_candidates(v) for k, v in SESSIONS.items()}

    scenario_blocks: dict[str, Any] = {}
    scenario_metrics: dict[str, dict[str, Any]] = {}

    for key, meta in SCENARIOS.items():
        threshold = float(meta["current_vwap_dev_lte_pct"])
        results = [
            _simulate_trade(t, session_candidates.get(t.session, []), current_threshold_pct=threshold)
            for t in trades
        ]
        metrics = _metrics(trades, results, baseline=baseline)
        scenario_metrics[key] = metrics
        scenario_blocks[key] = {
            "label": meta["label"],
            "current_vwap_dev_lte_pct": threshold,
            "rule": (
                f"entry_vwap_dev_pct > {ENTRY_VWAP_DEV_MIN_PCT}% "
                f"AND current_vwap_dev_pct <= {threshold}%"
            ),
            "metrics": metrics,
        }

    verdict = _pick_best(scenario_metrics)

    report = {
        "phase": 327,
        "title": "vwap_contraction_threshold_review",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": (
            "analysis only; entry_vwap_dev_pct > 0.5% fixed; "
            "stop/trailing/entry/momentum/board unchanged; no new indicators"
        ),
        "target_date": DAY,
        "trade_count": len(trades),
        "fixed_policy": {
            "entry_vwap_dev_gt_pct": ENTRY_VWAP_DEV_MIN_PCT,
            "hard_stop_pct": HARD_STOP_PCT,
            "trailing_mfe_activate_pct": TRAILING_ACTIVATE_PCT,
            "trailing_mfe_giveback_frac": GIVEBACK_FRAC,
        },
        "methodology": {
            "vwap_proxy": (
                "vwap_ref = entry_price / (1 + entry_vwap_dev_pct/100) held constant from entry"
            ),
            "simulation_order": [
                "1. stop_hit",
                "2. trailing_mfe_exit",
                "3. vwap_contraction_exit",
                "4. fallback actual exit",
            ],
        },
        "scenarios": SCENARIOS,
        "actual_live_baseline": baseline,
        "results_by_scenario": scenario_blocks,
        "verdict": verdict,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"best={verdict['best_current_vwap_dev_pct']}%")
    for key in ("A", "B", "C", "D"):
        m = scenario_metrics[key]
        th = SCENARIOS[key]["current_vwap_dev_lte_pct"]
        print(
            f"  {key} lte={th}% yen={m['total_pnl_yen_100']} "
            f"vwap_exit={m['vwap_contraction_exit_count']} stop_red={m['stop_hit_reduction_vs_actual']}"
        )
    print(verdict["conclusion"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
