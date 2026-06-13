#!/usr/bin/env python3
"""
Phase325: VWAP-based EXIT candidate discovery on 20260608 (169 trades).

Counterfactual analysis only — stop/trailing/entry unchanged.
Output: phase325_vwap_exit_discovery.json
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
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase325_vwap_exit_discovery.json"
DAY = "20260608"
JST = ZoneInfo("Asia/Tokyo")

HARD_STOP_PCT = 1.20
TRAILING_ACTIVATE_PCT = 0.80
GIVEBACK_FRAC = 0.50

SCENARIOS = {
    "A": {"label": "current_exit", "rule": "none"},
    "B": {"label": "vwap_touch_exit", "rule": "touch"},
    "C": {"label": "vwap_cross_exit", "rule": "cross"},
    "D": {"label": "vwap_contraction_exit", "rule": "contraction"},
}

SESSIONS = {
    "am": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_080642",
    "pm": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_122548",
}

VwapRule = Literal["none", "touch", "cross", "contraction"]


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
    peak_mfe_pct: float


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


def _vwap_rule_fires(
    *,
    rule: VwapRule,
    entry_vwap_dev_pct: Optional[float],
    vwap_ref: Optional[float],
    price: float,
    prev_price: Optional[float],
) -> bool:
    if rule == "none" or vwap_ref is None:
        return False
    if rule == "touch":
        return bool(entry_vwap_dev_pct is not None and entry_vwap_dev_pct > 0 and price <= vwap_ref)
    if rule == "cross":
        return price < vwap_ref
    if rule == "contraction":
        if entry_vwap_dev_pct is None or entry_vwap_dev_pct <= 0.5:
            return False
        return _current_vwap_dev_pct(price, vwap_ref) <= 0.2
    return False


def _simulate_on_path(
    trade: Trade,
    path: list[tuple[datetime, float]],
    *,
    vwap_rule: VwapRule,
) -> SimResult:
    from replay.pnl_yen import compute_pnl_yen_100

    entry = trade.entry_price
    stop = entry * (1.0 - HARD_STOP_PCT / 100.0)
    vwap_ref = _vwap_at_entry(entry, trade.entry_vwap_dev_pct)
    peak_pnl = 0.0
    prev_px: Optional[float] = None

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

        if vwap_rule != "none" and i > 0:
            if _vwap_rule_fires(
                rule=vwap_rule,
                entry_vwap_dev_pct=trade.entry_vwap_dev_pct,
                vwap_ref=vwap_ref,
                price=px,
                prev_price=prev_px,
            ):
                reason = {
                    "touch": "vwap_touch_exit",
                    "cross": "vwap_cross_exit",
                    "contraction": "vwap_contraction_exit",
                }[vwap_rule]
                return SimResult(
                    exit_reason=reason,
                    exit_price=px,
                    pnl_pct=pnl,
                    pnl_yen_100=compute_pnl_yen_100(entry, px),
                    simulation_method="tick_path",
                )

        prev_px = px

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
    vwap_rule: VwapRule,
) -> SimResult:
    if vwap_rule == "none":
        return SimResult(
            exit_reason=trade.actual_exit_reason,
            exit_price=trade.exit_price,
            pnl_pct=trade.actual_pnl_pct,
            pnl_yen_100=trade.actual_pnl_yen_100,
            simulation_method="actual_live",
        )
    path = _price_path_for_trade(trade, candidates)
    if len(path) >= 2:
        return _simulate_on_path(trade, path, vwap_rule=vwap_rule)
    return SimResult(
        exit_reason=trade.actual_exit_reason,
        exit_price=trade.exit_price,
        pnl_pct=trade.actual_pnl_pct,
        pnl_yen_100=trade.actual_pnl_yen_100,
        simulation_method="no_tick_path",
    )


def _metrics(trades: list[Trade], results: list[SimResult], *, baseline: dict[str, Any]) -> dict[str, Any]:
    yens = [r.pnl_yen_100 for r in results]
    pnls = [r.pnl_pct for r in results]
    wins = [y for y in yens if y > 0]
    losses = [y for y in yens if y < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None

    exit_counts = Counter(r.exit_reason for r in results)
    actual_yen = sum(t.actual_pnl_yen_100 for t in trades)

    premature = 0
    premature_yen = 0.0
    trailing_eroded = 0
    trailing_eroded_yen = 0.0
    for t, r in zip(trades, results):
        delta = r.pnl_yen_100 - t.actual_pnl_yen_100
        if delta < -0.01 and (
            r.exit_reason.startswith("vwap_")
            or (t.actual_pnl_yen_100 > 0 and r.pnl_yen_100 < t.actual_pnl_yen_100)
        ):
            premature += 1
            premature_yen += delta
        if t.actual_exit_reason == "trailing_mfe_exit" and delta < -0.01:
            trailing_eroded += 1
            trailing_eroded_yen += delta

    return {
        "trade_count": len(trades),
        "total_pnl_yen_100": round(sum(yens), 2),
        "avg_pnl_yen_100": round(statistics.mean(yens), 2) if yens else None,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
        "win_rate": round(len(wins) / len(yens), 4) if yens else None,
        "profit_factor_yen_100": pf,
        "gross_profit_yen_100": round(gross_profit, 2),
        "gross_loss_yen_100": round(gross_loss, 2),
        "exit_reason_counts": dict(sorted(exit_counts.items())),
        "stop_hit_count": int(exit_counts.get("stop_hit", 0)),
        "trailing_mfe_exit_count": int(exit_counts.get("trailing_mfe_exit", 0)),
        "vwap_exit_count": sum(v for k, v in exit_counts.items() if k.startswith("vwap_")),
        "stop_hit_reduction_vs_actual": int(baseline.get("stop_hit_count", 0))
        - int(exit_counts.get("stop_hit", 0)),
        "trailing_mfe_exit_reduction_vs_actual": int(baseline.get("trailing_mfe_exit_count", 0))
        - int(exit_counts.get("trailing_mfe_exit", 0)),
        "premature_profit_taking_count": premature,
        "premature_profit_taking_yen_100": round(premature_yen, 2),
        "existing_trailing_mfe_exit_eroded_count": trailing_eroded,
        "existing_trailing_mfe_exit_eroded_yen_100": round(trailing_eroded_yen, 2),
        "vs_actual_total_pnl_yen_improvement": round(sum(yens) - actual_yen, 2),
    }


def _actual_baseline(trades: list[Trade]) -> dict[str, Any]:
    results = [
        SimResult(
            exit_reason=t.actual_exit_reason,
            exit_price=t.exit_price,
            pnl_pct=t.actual_pnl_pct,
            pnl_yen_100=t.actual_pnl_yen_100,
            simulation_method="actual_live",
        )
        for t in trades
    ]
    m = _metrics(trades, results, baseline={"stop_hit_count": 0, "trailing_mfe_exit_count": 0})
    exit_counts = Counter(t.actual_exit_reason for t in trades)
    m["stop_hit_count"] = int(exit_counts.get("stop_hit", 0))
    m["trailing_mfe_exit_count"] = int(exit_counts.get("trailing_mfe_exit", 0))
    m["stop_hit_reduction_vs_actual"] = 0
    m["trailing_mfe_exit_reduction_vs_actual"] = 0
    return m


def _pick_best(scenario_metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(
        scenario_metrics.items(),
        key=lambda kv: float(kv[1]["total_pnl_yen_100"]),
        reverse=True,
    )
    best_key, best = ranked[0]
    rule_map = {"A": "none", "B": "touch", "C": "cross", "D": "contraction"}
    baseline = scenario_metrics["A"]
    return {
        "best_vwap_exit_rule": rule_map[best_key],
        "best_scenario": best_key,
        "best_activate_label": SCENARIOS[best_key]["label"],
        "ranking_by_total_pnl_yen_100": [
            {
                "scenario": k,
                "rule": rule_map[k],
                "label": SCENARIOS[k]["label"],
                "total_pnl_yen_100": m["total_pnl_yen_100"],
                "vs_actual_improvement": m["vs_actual_total_pnl_yen_improvement"],
            }
            for k, m in ranked
        ],
        "vs_baseline_A": [
            {
                "scenario": k,
                "rule": rule_map[k],
                "total_pnl_yen_100": m["total_pnl_yen_100"],
                "improvement_vs_A": round(
                    float(m["total_pnl_yen_100"]) - float(baseline["total_pnl_yen_100"]), 2
                ),
                "stop_hit_reduction": m["stop_hit_reduction_vs_actual"],
                "trailing_reduction": m["trailing_mfe_exit_reduction_vs_actual"],
                "premature_count": m["premature_profit_taking_count"],
                "premature_yen": m["premature_profit_taking_yen_100"],
            }
            for k, m in scenario_metrics.items()
        ],
        "conclusion": _conclusion(best_key, best, baseline),
    }


def _conclusion(best_key: str, best: dict[str, Any], baseline: dict[str, Any]) -> str:
    rule_map = {"A": "none", "B": "touch", "C": "cross", "D": "contraction"}
    delta = float(best["total_pnl_yen_100"]) - float(baseline["total_pnl_yen_100"])
    if best_key == "A" or delta <= 0:
        return "No VWAP exit rule improves full-day PnL vs current exit on this day"
    return (
        f"Best VWAP rule: {rule_map[best_key]} "
        f"(total_pnl_yen_100 {best['total_pnl_yen_100']} vs {baseline['total_pnl_yen_100']}, "
        f"delta {delta:+.0f}, stop_hit_reduction {best['stop_hit_reduction_vs_actual']})"
    )


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    trades = _load_trades()
    if not trades:
        print("no trades", file=sys.stderr)
        return 1

    session_candidates = {k: _load_session_candidates(v) for k, v in SESSIONS.items()}
    baseline = _actual_baseline(trades)

    scenario_metrics: dict[str, dict[str, Any]] = {}
    scenario_blocks: dict[str, Any] = {}

    for key, meta in SCENARIOS.items():
        rule: VwapRule = meta["rule"]  # type: ignore[assignment]
        results = [
            _simulate_trade(t, session_candidates.get(t.session, []), vwap_rule=rule) for t in trades
        ]
        metrics = _metrics(trades, results, baseline=baseline)
        scenario_metrics[key] = metrics
        scenario_blocks[key] = {
            "label": meta["label"],
            "rule": rule,
            "rule_definition": _rule_definition(rule),
            "metrics": metrics,
        }

    verdict = _pick_best(scenario_metrics)

    report = {
        "phase": 325,
        "title": "vwap_exit_discovery",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": (
            "analysis only; entry/stop/trailing/momentum/board unchanged; hold time not used as exit rule"
        ),
        "target_date": DAY,
        "trade_count": len(trades),
        "methodology": {
            "vwap_proxy": (
                "Per-tick VWAP not stored in live session events. "
                "vwap_ref = entry_price / (1 + entry_vwap_dev_pct/100) held constant from entry snapshot."
            ),
            "current_exit_policy": {
                "hard_stop_pct": HARD_STOP_PCT,
                "trailing_mfe_activate_pct": TRAILING_ACTIVATE_PCT,
                "trailing_mfe_giveback_frac": GIVEBACK_FRAC,
            },
            "simulation_order": [
                "1. stop_hit (unchanged)",
                "2. trailing_mfe_exit (unchanged)",
                "3. VWAP rule (B/C/D only)",
                "4. fallback to actual exit if no trigger on tick path",
            ],
            "vwap_rules": {
                "touch": "entry_vwap_dev_pct > 0 AND price <= vwap_ref",
                "cross": "price < vwap_ref (evaluated after entry tick)",
                "contraction": "entry_vwap_dev_pct > 0.5% AND current_vwap_dev_pct <= 0.2%",
            },
        },
        "scenarios": SCENARIOS,
        "actual_live_baseline": baseline,
        "results_by_scenario": scenario_blocks,
        "verdict": verdict,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"trades={len(trades)} best_rule={verdict['best_vwap_exit_rule']}")
    for key in ("A", "B", "C", "D"):
        m = scenario_metrics[key]
        print(
            f"  {key} yen={m['total_pnl_yen_100']} stop={m['stop_hit_count']} "
            f"trail={m['trailing_mfe_exit_count']} vwap={m['vwap_exit_count']} "
            f"premature={m['premature_profit_taking_count']}"
        )
    print(verdict["conclusion"])
    return 0


def _rule_definition(rule: VwapRule) -> str:
    defs = {
        "none": "actual live observer_exit (no counterfactual)",
        "touch": "entry_vwap_dev_pct > 0 AND price <= vwap_ref",
        "cross": "price < vwap_ref",
        "contraction": "entry_vwap_dev_pct > 0.5% AND current_vwap_dev_pct <= 0.2%",
    }
    return defs.get(rule, "")


if __name__ == "__main__":
    raise SystemExit(main())
