#!/usr/bin/env python3
"""
Phase322-lite: trailing_mfe activation sensitivity on Phase321 stop_hit 41 trades.

Counterfactual only — no replay, no entry/stop changes.
Output: phase322_trailing_activation_sensitivity_review.json
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
PHASE321 = REPO / "kabu_native/results/reports/phase321_stop_hit_mechanism_review.json"
OUT = REPO / "kabu_native/results/reports/phase322_trailing_activation_sensitivity_review.json"
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
class StopTrade:
    session: str
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    actual_pnl_pct: float
    actual_pnl_yen_100: float
    peak_mfe_pct: float


@dataclass
class Counterfactual:
    mfe_ge_activate: bool
    trailing_eligible: bool
    stop_avoided: bool
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


def _load_trades_from_phase321() -> list[StopTrade]:
    data = json.loads(PHASE321.read_text(encoding="utf-8"))
    out: list[StopTrade] = []
    for session_label, block in data.get("sessions", {}).items():
        for row in block.get("trades", []):
            out.append(
                StopTrade(
                    session=session_label,
                    symbol=str(row.get("symbol") or ""),
                    entry_time=str(row.get("entry_time") or ""),
                    exit_time=str(row.get("exit_time") or ""),
                    entry_price=float(row.get("entry_price") or 0.0),
                    exit_price=float(row.get("exit_price") or 0.0),
                    actual_pnl_pct=float(row.get("loss_pct") or 0.0),
                    actual_pnl_yen_100=float(row.get("loss_yen_100") or 0.0),
                    peak_mfe_pct=float(row.get("peak_mfe_pct") or 0.0),
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
    trade: StopTrade,
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
    trade: StopTrade,
    path: list[tuple[datetime, float]],
    *,
    activate_pct: float,
) -> Counterfactual:
    from replay.pnl_yen import compute_pnl_yen_100

    entry = trade.entry_price
    stop = entry * (1.0 - HARD_STOP_PCT / 100.0)
    peak_pnl = 0.0
    mfe_ge_activate = trade.peak_mfe_pct >= activate_pct

    for _ts, px in path:
        pnl = _pnl_pct(entry, px)
        peak_pnl = max(peak_pnl, pnl)
        if px <= stop:
            return Counterfactual(
                mfe_ge_activate=mfe_ge_activate,
                trailing_eligible=False,
                stop_avoided=False,
                exit_reason="stop_hit",
                exit_price=px,
                pnl_pct=pnl,
                pnl_yen_100=compute_pnl_yen_100(entry, px),
                simulation_method="tick_path",
            )
        if peak_pnl >= activate_pct and pnl <= peak_pnl * GIVEBACK_FRAC:
            return Counterfactual(
                mfe_ge_activate=mfe_ge_activate,
                trailing_eligible=True,
                stop_avoided=True,
                exit_reason="trailing_mfe_exit",
                exit_price=px,
                pnl_pct=pnl,
                pnl_yen_100=compute_pnl_yen_100(entry, px),
                simulation_method="tick_path",
            )

    # Path ended without explicit trigger — keep actual stop outcome.
    return Counterfactual(
        mfe_ge_activate=mfe_ge_activate,
        trailing_eligible=mfe_ge_activate,
        stop_avoided=False,
        exit_reason="stop_hit",
        exit_price=trade.exit_price,
        pnl_pct=trade.actual_pnl_pct,
        pnl_yen_100=trade.actual_pnl_yen_100,
        simulation_method="tick_path_fallback_actual",
    )


def _simulate_peak_mfe_proxy(
    trade: StopTrade,
    *,
    activate_pct: float,
) -> Counterfactual:
    """Fallback when tick path unavailable: peak_mfe proxy for giveback exit."""
    from replay.pnl_yen import compute_pnl_yen_100

    mfe_ge = trade.peak_mfe_pct >= activate_pct
    if not mfe_ge:
        return Counterfactual(
            mfe_ge_activate=False,
            trailing_eligible=False,
            stop_avoided=False,
            exit_reason="stop_hit",
            exit_price=trade.exit_price,
            pnl_pct=trade.actual_pnl_pct,
            pnl_yen_100=trade.actual_pnl_yen_100,
            simulation_method="peak_mfe_proxy",
        )
    cf_pnl = round(trade.peak_mfe_pct * GIVEBACK_FRAC, 4)
    cf_px = entry_px = trade.entry_price * (1.0 + cf_pnl / 100.0)
    return Counterfactual(
        mfe_ge_activate=True,
        trailing_eligible=True,
        stop_avoided=True,
        exit_reason="trailing_mfe_exit",
        exit_price=round(cf_px, 2),
        pnl_pct=cf_pnl,
        pnl_yen_100=compute_pnl_yen_100(entry_px, cf_px),
        simulation_method="peak_mfe_proxy",
    )


def _counterfactual_for_trade(
    trade: StopTrade,
    candidates: list[dict[str, Any]],
    *,
    activate_pct: float,
) -> Counterfactual:
    path = _price_path_for_trade(trade, candidates)
    if len(path) >= 2:
        return _simulate_on_path(trade, path, activate_pct=activate_pct)
    return _simulate_peak_mfe_proxy(trade, activate_pct=activate_pct)


def _scenario_summary(
    trades: list[StopTrade],
    results: list[Counterfactual],
    *,
    scenario_key: str,
    activate_pct: float,
) -> dict[str, Any]:
    actual_yen = sum(t.actual_pnl_yen_100 for t in trades)
    cf_yen = sum(r.pnl_yen_100 for r in results)
    avoided = sum(1 for r in results if r.stop_avoided)
    trailing_n = sum(1 for r in results if r.trailing_eligible and r.stop_avoided)
    mfe_ge = sum(1 for r in results if r.mfe_ge_activate)

    stop_loss_improvement_yen = round(cf_yen - actual_yen, 2)
    return {
        "scenario": scenario_key,
        "activate_pct": activate_pct,
        "giveback_frac": GIVEBACK_FRAC,
        "hard_stop_pct": HARD_STOP_PCT,
        "trade_count": len(trades),
        "mfe_ge_activate_count": mfe_ge,
        "trailing_eligible_count": sum(1 for r in results if r.trailing_eligible or r.mfe_ge_activate),
        "stop_hit_avoided_count": avoided,
        "trailing_mfe_exit_count": trailing_n,
        "actual_total_pnl_yen_100": round(actual_yen, 2),
        "counterfactual_total_pnl_yen_100": round(cf_yen, 2),
        "total_pnl_yen_improvement": stop_loss_improvement_yen,
        "avg_actual_pnl_pct": round(statistics.mean(t.actual_pnl_pct for t in trades), 4),
        "avg_counterfactual_pnl_pct": round(statistics.mean(r.pnl_pct for r in results), 4),
        "avg_pnl_pct_improvement": round(
            statistics.mean(r.pnl_pct - t.actual_pnl_pct for t, r in zip(trades, results)), 4
        ),
    }


def _trade_detail(
    trade: StopTrade,
    result: Counterfactual,
    *,
    activate_pct: float,
) -> dict[str, Any]:
    return {
        "session": trade.session,
        "symbol": trade.symbol,
        "entry_time": trade.entry_time,
        "exit_time": trade.exit_time,
        "entry_price": trade.entry_price,
        "actual_exit_price": trade.exit_price,
        "actual_pnl_pct": round(trade.actual_pnl_pct, 4),
        "actual_pnl_yen_100": round(trade.actual_pnl_yen_100, 2),
        "peak_mfe_pct": round(trade.peak_mfe_pct, 4),
        "activate_pct": activate_pct,
        "mfe_ge_activate": result.mfe_ge_activate,
        "trailing_eligible": result.trailing_eligible or result.mfe_ge_activate,
        "stop_hit_avoided": result.stop_avoided,
        "counterfactual_exit_reason": result.exit_reason,
        "counterfactual_exit_price": round(result.exit_price, 2),
        "counterfactual_pnl_pct": round(result.pnl_pct, 4),
        "counterfactual_pnl_yen_100": round(result.pnl_yen_100, 2),
        "pnl_yen_improvement": round(result.pnl_yen_100 - trade.actual_pnl_yen_100, 2),
        "simulation_method": result.simulation_method,
    }


def _pick_best(scenario_summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(
        scenario_summaries.items(),
        key=lambda kv: (
            float(kv[1]["total_pnl_yen_improvement"]),
            int(kv[1]["stop_hit_avoided_count"]),
        ),
        reverse=True,
    )
    best_key, best = ranked[0]
    comparisons = []
    baseline = scenario_summaries["A"]
    for key, summ in scenario_summaries.items():
        comparisons.append(
            {
                "scenario": key,
                "activate_pct": summ["activate_pct"],
                "vs_A_stop_hit_avoided_delta": int(summ["stop_hit_avoided_count"])
                - int(baseline["stop_hit_avoided_count"]),
                "vs_A_total_pnl_yen_improvement_delta": round(
                    float(summ["total_pnl_yen_improvement"])
                    - float(baseline["total_pnl_yen_improvement"]),
                    2,
                ),
            }
        )

    activate_verdict: dict[str, str] = {}
    for pct in (0.8, 0.6, 0.4):
        matches = [k for k, s in scenario_summaries.items() if abs(s["activate_pct"] - pct) < 1e-6]
        if not matches:
            continue
        key = matches[0]
        s = scenario_summaries[key]
        if s["stop_hit_avoided_count"] == 0:
            activate_verdict[f"{pct}%"] = "no_stop_avoidance"
        elif key == best_key:
            activate_verdict[f"{pct}%"] = "best_by_improvement"
        else:
            activate_verdict[f"{pct}%"] = "partial_improvement"

    is_08_too_high = (
        int(baseline["mfe_ge_activate_count"]) == 0
        and any(
            int(scenario_summaries[k]["mfe_ge_activate_count"]) > 0 for k in ("B", "C")
        )
    )

    return {
        "best_activate_pct": best["activate_pct"],
        "best_scenario": best_key,
        "ranking_by_total_pnl_yen_improvement": [
            {"scenario": k, "activate_pct": s["activate_pct"], "improvement_yen": s["total_pnl_yen_improvement"]}
            for k, s in ranked
        ],
        "vs_baseline_A": comparisons,
        "activate_pct_verdict": activate_verdict,
        "is_0p8pct_too_high": is_08_too_high,
        "conclusion": (
            "0.8% activate is too high for these stop_hit trades"
            if is_08_too_high and best_key != "A"
            else (
                "0.8% activate is not the binding issue (lowering threshold adds little)"
                if best_key == "A"
                else "Lower activate threshold helps but 0.8% alone is not the only issue"
            )
        ),
    }


def main() -> int:
    _bootstrap()
    if not PHASE321.is_file():
        print(f"missing {PHASE321}", file=sys.stderr)
        return 1

    trades = _load_trades_from_phase321()
    if not trades:
        print("no trades in phase321 report", file=sys.stderr)
        return 1

    session_candidates = {
        label: _load_session_candidates(path) for label, path in SESSIONS.items()
    }

    scenario_blocks: dict[str, Any] = {}
    scenario_summaries: dict[str, dict[str, Any]] = {}

    for key, meta in SCENARIOS.items():
        activate = float(meta["activate_pct"])
        results: list[Counterfactual] = []
        details: list[dict[str, Any]] = []
        for trade in trades:
            cands = session_candidates.get(trade.session, [])
            cf = _counterfactual_for_trade(trade, cands, activate_pct=activate)
            results.append(cf)
            details.append(_trade_detail(trade, cf, activate_pct=activate))

        summary = _scenario_summary(trades, results, scenario_key=key, activate_pct=activate)
        scenario_summaries[key] = summary
        scenario_blocks[key] = {
            "label": meta["label"],
            "summary": summary,
            "trades": details,
        }

    verdict = _pick_best(scenario_summaries)
    actual_total = round(sum(t.actual_pnl_yen_100 for t in trades), 2)

    report = {
        "phase": 322,
        "title": "trailing_activation_sensitivity_review",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": (
            "Phase321 stop_hit 41 trades only; counterfactual trailing activate sensitivity; "
            "no new replay, no entry/stop changes"
        ),
        "source_report": str(PHASE321.relative_to(REPO)).replace("\\", "/"),
        "target_date": DAY,
        "baseline_policy": {
            "hard_stop_pct": HARD_STOP_PCT,
            "trailing_mfe_activate_pct": 0.80,
            "trailing_mfe_giveback_frac": GIVEBACK_FRAC,
        },
        "scenarios": {k: {"activate_pct": v["activate_pct"], "label": v["label"]} for k, v in SCENARIOS.items()},
        "stop_hit_trade_count": len(trades),
        "actual_baseline": {
            "total_pnl_yen_100": actual_total,
            "avg_pnl_pct": round(statistics.mean(t.actual_pnl_pct for t in trades), 4),
            "all_exit_reason": "stop_hit",
        },
        "results_by_scenario": scenario_blocks,
        "verdict": verdict,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"trades={len(trades)} best_activate={verdict['best_activate_pct']}% "
        f"conclusion={verdict['conclusion']}"
    )
    for key in ("A", "B", "C"):
        s = scenario_summaries[key]
        print(
            f"  {key} activate={s['activate_pct']}% "
            f"mfe_ge={s['mfe_ge_activate_count']} avoided={s['stop_hit_avoided_count']} "
            f"improvement_yen={s['total_pnl_yen_improvement']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
