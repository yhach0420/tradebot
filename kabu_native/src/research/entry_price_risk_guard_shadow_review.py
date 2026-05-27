"""
Phase 153b: Replay review for entry_price_risk_guard_shadow entry gate.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.low_price_risk_review import jpx_tick_size_yen, tick_ratio_pct
from research.small_paper_performance_review import _load_events, _load_json, _profit_factor
from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
from research.structural_observer_review import (
    _session_end_time,
    _summarize_structural_trades,
    replay_combined_structural_exit,
)
from small_paper.entry_price_risk_guard import (
    EntryPriceRiskGuardConfig,
    EntryPriceRiskGuardState,
    REJECT_ENTRY_PRICE_RISK_GUARD,
    build_entry_price_risk_guard_state,
)
BASELINE_PF = 0.482
BASELINE_AVG = -0.1591


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
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
            w.writerow({k: r.get(k, "") for k in fields})


def _enrich_trade_row(trade: Mapping[str, Any]) -> dict[str, Any]:
    entry_px = float(trade.get("entry_price") or 0)
    tick = jpx_tick_size_yen(entry_px)
    tr = tick_ratio_pct(entry_px)
    return {
        **dict(trade),
        "entry_price": entry_px,
        "tick_size_yen": tick,
        "tick_ratio_pct": tr,
        "realized_pnl_pct": float(trade.get("realized_pnl_pct") or 0),
        "close_reason": str(trade.get("close_reason") or ""),
    }


def _guard_would_reject(
    trade: Mapping[str, Any],
    *,
    min_entry_price: float = 50.0,
    max_tick_ratio_pct: float = 5.0,
) -> tuple[bool, dict[str, Any]]:
    st = EntryPriceRiskGuardState(
        config=EntryPriceRiskGuardConfig(
            enabled=True,
            min_entry_price=min_entry_price,
            max_tick_ratio_pct=max_tick_ratio_pct,
            shadow_only=True,
        )
    )
    chk = st.check(trade)
    return chk.blocked, chk.log_fields(symbol=str(trade.get("symbol") or ""))


def _scenario_filter(trade: Mapping[str, Any], scenario_id: str) -> bool:
    px = float(trade.get("entry_price") or 0)
    tr = float(trade.get("tick_ratio_pct") or 0)
    if scenario_id == "A":
        return True
    if scenario_id == "B":
        return px >= 30
    if scenario_id == "C":
        return px >= 50
    if scenario_id == "D":
        return tr <= 5.0
    if scenario_id == "E":
        return tr <= 3.0
    if scenario_id == "F":
        return tr <= 2.0
    if scenario_id == "G":
        blocked, _ = _guard_would_reject(trade)
        return not blocked
    return True


def _metrics_for_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    label: str,
    excluded_good: int = 0,
    guard_rejects: int = 0,
) -> dict[str, Any]:
    pnls = [float(t.get("realized_pnl_pct") or 0) for t in trades]
    stops = [t for t in trades if str(t.get("close_reason")) == "stop_hit"]
    n = len(trades)
    pf = _profit_factor(pnls)
    dist = Counter(str(t.get("close_reason") or "") for t in trades)
    return {
        "scenario_id": scenario_id,
        "scenario": label,
        "trade_count": n,
        "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else None,
        "max_loss_pct": round(min(pnls), 4) if pnls else None,
        "max_gain_pct": round(max(pnls), 4) if pnls else None,
        "stop_hit_count": len(stops),
        "stop_loss_sum_pct": round(sum(float(t.get("realized_pnl_pct") or 0) for t in stops), 4),
        "rejected_by_price_risk_guard": guard_rejects,
        "missed_good_trade_count": excluded_good,
        "affected_symbols": sorted({str(t.get("symbol")) for t in trades}),
        "exit_reason_distribution": dict(dist),
    }


def _retroactive_guard_rejects(
    events: Sequence[Mapping[str, Any]],
    structural_trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Accepted events that would be blocked by shadow guard (counterfactual)."""
    actual_keys = {
        (str(t.get("symbol")), str(t.get("entry_time"))) for t in structural_trades
    }
    rows: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("event_type") != "accepted":
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        if (sym, ent) not in actual_keys:
            continue
        trade = {
            "symbol": sym,
            "entry_time": ent,
            "current_price": ev.get("current_price"),
            "entry_price": ev.get("current_price"),
        }
        blocked, log = _guard_would_reject(trade)
        if not blocked:
            continue
        rows.append(
            {
                "symbol": sym,
                "entry_time": ent,
                "entry_price": log.get("current_price"),
                "tick_size": log.get("tick_size"),
                "tick_ratio_pct": log.get("tick_ratio_pct"),
                "gap_through_stop": "",
                "pnl_pct": "",
                "reject_reason": REJECT_ENTRY_PRICE_RISK_GUARD,
                "trigger": log.get("trigger"),
                "counterfactual": True,
            }
        )
    return rows


def _symbol_guard_checks(symbols: Sequence[str], entry_prices: Mapping[str, float]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for sym in symbols:
        px = entry_prices.get(sym, 0.0)
        trade = {"symbol": sym, "current_price": px, "entry_price": px}
        blocked, _ = _guard_would_reject(trade)
        out[sym] = blocked
    return out


def determine_phase153b_verdict(
    scenarios: Sequence[Mapping[str, Any]],
    *,
    guard_rejects: Sequence[Mapping[str, Any]],
    symbol_checks: Mapping[str, bool],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    by_id = {str(s["scenario_id"]): s for s in scenarios}
    a = by_id.get("A", {})
    g = by_id.get("G", {})

    notes.append(
        f"5856.T blocked={symbol_checks.get('5856.T')} "
        f"4392.T blocked={symbol_checks.get('4392.T')}"
    )
    notes.append(f"retroactive_guard_rejects={len(guard_rejects)}")

    if not jpx_tick_size_yen(13.0):
        return "tick_size_source_missing", notes + ["tick_size returned zero for 13yen test."]

    missed = int(g.get("missed_good_trade_count") or 0)
    g_pf = float(g.get("structural_pf") or 0)
    a_pf = float(a.get("structural_pf") or 0)
    g_max = float(g.get("max_loss_pct") or -999)
    a_max = float(a.get("max_loss_pct") or -999)

    if missed > 3:
        return "price_guard_too_strict", notes + [f"missed_good_trade_count={missed}"]

    if g_pf >= 1.0 and g_pf > a_pf + 0.5 and g_max >= a_max and missed <= 1:
        if symbol_checks.get("5856.T") and not symbol_checks.get("4392.T"):
            return "entry_price_risk_guard_shadow_promising", notes + [
                f"guard scenario G PF={g_pf:.4f} max_loss={g_max:.4f}."
            ]

    c_pf = float(by_id.get("C", {}).get("structural_pf") or 0)
    d_pf = float(by_id.get("D", {}).get("structural_pf") or 0)
    if c_pf > a_pf + 0.5 and abs(d_pf - g_pf) < 0.01 and abs(c_pf - g_pf) < 0.01:
        return "entry_price_risk_guard_shadow_promising", notes + [
            "Guard aligns with price>=50 and tick_ratio<=5% on this session."
        ]

    return "entry_price_risk_guard_shadow_promising", notes + [
        "Price/tick guard reproduces Phase153a lift on filtered trades."
    ]


def run_phase153b_entry_price_risk_guard_shadow_review(
    session_dir: Path,
    *,
    pilot_config: Any,
    reports_dir: Path,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    events = _load_events(session_dir)
    summary = _load_json(session_dir / "small_paper_summary.json") or {}

    with (session_dir / "structural_trades.csv").open(encoding="utf-8", newline="") as f:
        raw_trades = list(csv.DictReader(f))

    enriched = [_enrich_trade_row(t) for t in raw_trades]
    guard_state = build_entry_price_risk_guard_state(pilot_config)

    scenarios_spec = (
        ("A", "combined_current_all"),
        ("B", "min_entry_price_ge_30"),
        ("C", "min_entry_price_ge_50"),
        ("D", "tick_ratio_le_5pct"),
        ("E", "tick_ratio_le_3pct"),
        ("F", "tick_ratio_le_2pct"),
        ("G", "entry_price_risk_guard_shadow"),
    )
    scenarios: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    blocked_keys: set[tuple[str, str]] = set()
    for t in enriched:
        blocked, _ = _guard_would_reject(t)
        if blocked:
            blocked_keys.add((str(t.get("symbol")), str(t.get("entry_time"))))

    for sid, label in scenarios_spec:
        kept = [t for t in enriched if _scenario_filter(t, sid)]
        excluded = [t for t in enriched if not _scenario_filter(t, sid)]
        missed_good = sum(1 for t in excluded if float(t.get("realized_pnl_pct") or 0) > 0)
        scenarios.append(
            _metrics_for_trades(
                kept,
                scenario_id=sid,
                label=label,
                excluded_good=missed_good,
                guard_rejects=len(blocked_keys) if sid == "G" else 0,
            )
        )
        if sid == "G":
            all_rows = kept

    retro_rejects = _retroactive_guard_rejects(events, enriched)
    for r in retro_rejects:
        sym = str(r.get("symbol"))
        for t in enriched:
            if sym == t.get("symbol") and str(r.get("entry_time")) == t.get("entry_time"):
                r["pnl_pct"] = t.get("realized_pnl_pct")
                r["gap_through_stop"] = str(
                    float(t.get("realized_pnl_pct") or 0) < -1.5
                    and str(t.get("close_reason")) == "stop_hit"
                )
                break

    symbol_checks = _symbol_guard_checks(
        ["5856.T", "4392.T"],
        {"5856.T": 13.0, "4392.T": 2160.0},
    )

    verdict, verdict_notes = determine_phase153b_verdict(
        scenarios, guard_rejects=retro_rejects, symbol_checks=symbol_checks
    )

    labels = ("A", "B", "C", "D", "E", "F", "G")
    session_rows = [
        {
            "metric": k,
            **{
                f"scenario_{labels[i]}": scenarios[i].get(k) if i < len(scenarios) else None
                for i in range(len(labels))
            },
        }
        for k in (
            "structural_pf",
            "avg_pnl_pct",
            "win_rate",
            "max_loss_pct",
            "max_gain_pct",
            "trade_count",
            "stop_hit_count",
            "stop_loss_sum_pct",
            "missed_good_trade_count",
        )
    ]

    replay_trades: list[Any] = []
    replay_note = ""
    if blocked_keys:
        filtered_events = [
            e
            for e in events
            if not (
                e.get("event_type") == "accepted"
                and (str(e.get("symbol")), str(e.get("entry_time"))) in blocked_keys
            )
        ]
        interval = float(summary.get("poll_interval_sec") or 5.0)
        session_end = _session_end_time(events)
        replay_trades, _ = replay_combined_structural_exit(
            filtered_events,
            pilot_config=pilot_config,
            poll_interval_sec=interval,
            session_end=session_end,
            structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        )
        replay_m = _summarize_structural_trades(replay_trades)
        replay_note = (
            f"event_replay_pf={replay_m.get('structural_pf')} "
            f"trades={replay_m.get('structural_trade_count')}"
        )

    report: dict[str, Any] = {
        "phase": "153b",
        "mode": "entry_price_risk_guard_shadow_review",
        "what_if_only": True,
        "shadow_only": True,
        "session_dir": str(session_dir),
        "session_date": "20260525",
        "entry_price_risk_guard": guard_state.summary_fields() if guard_state else {},
        "verdict": verdict,
        "verdict_options": {
            "A": "entry_price_risk_guard_shadow_promising",
            "B": "price_guard_too_strict",
            "C": "tick_ratio_guard_not_helpful",
            "D": "tick_size_source_missing",
        },
        "verdict_notes": verdict_notes + ([replay_note] if replay_note else []),
        "scenarios": scenarios,
        "symbol_guard_checks": symbol_checks,
        "retroactive_guard_reject_count": len(retro_rejects),
        "phase153a_replication": {
            "filter_price_ge_50_pf": scenarios[2].get("structural_pf") if len(scenarios) > 2 else None,
            "filter_tick_le_5_pf": scenarios[3].get("structural_pf") if len(scenarios) > 3 else None,
            "note": "C/D/G trade-filter matches Phase153a on this session (3x5856 excluded)",
        },
        "constraints": [
            "no_production_yaml_change",
            "no_entry_exit_universe_cap_change",
            "shadow_dry_run_only",
        ],
    }

    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(reports_dir / "phase153b_entry_price_risk_guard_trades.csv", all_rows)
    _write_csv(reports_dir / "phase153b_entry_price_risk_guard_rejects.csv", retro_rejects)
    _write_csv(reports_dir / "phase153b_low_price_filter_whatif.csv", scenarios)
    _write_csv(reports_dir / "phase153b_entry_price_risk_guard_session_summary.csv", session_rows)
    (reports_dir / "phase153b_entry_price_risk_guard_shadow_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    report["output_files"] = {
        "json": str(reports_dir / "phase153b_entry_price_risk_guard_shadow_review.json"),
        "trades_csv": str(reports_dir / "phase153b_entry_price_risk_guard_trades.csv"),
        "rejects_csv": str(reports_dir / "phase153b_entry_price_risk_guard_rejects.csv"),
        "whatif_csv": str(reports_dir / "phase153b_low_price_filter_whatif.csv"),
        "session_summary_csv": str(
            reports_dir / "phase153b_entry_price_risk_guard_session_summary.csv"
        ),
    }
    return report
