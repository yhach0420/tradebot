"""
Phase 166: simple fade-breakdown shadow policy review (replay).

Compare:
 A combined_structural_exit_v1
 B fade_hybrid_shadow (Phase162)
 C fade_breakdown_shadow (Phase166)
 D fade_disable_shadow reference
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_exit_replay import FADE_EXIT_REASONS
from research.phase156_intraday_refresh_cap5_review import _filter_price_risk_candidates
from research.phase159_overlap_review import load_cap5_only_keys
from research.small_paper_performance_review import _load_events
from research.structural_observer_review import _summarize_structural_trades, replay_combined_structural_exit
from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
from research.fade_hybrid_shadow import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_DISABLE_SHADOW,
    POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_BREAKDOWN_SHADOW,
)


SCENARIOS: tuple[tuple[str, str], ...] = (
    ("A_combined_v1", POLICY_COMBINED_STRUCTURAL_EXIT_V1),
    ("B_fade_hybrid_shadow", POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_HYBRID_SHADOW),
    ("C_fade_breakdown_shadow", POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_BREAKDOWN_SHADOW),
    ("D_fade_disable_shadow", POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_DISABLE_SHADOW),
)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _session_id(session_dir: Path) -> str:
    if session_dir.parent.name.isdigit():
        return f"{session_dir.parent.name}/{session_dir.name}"
    return session_dir.name


def _guard_pass_keys(events: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    candidates, _, _ = _filter_price_risk_candidates(events)
    keys: set[tuple[str, str]] = set()
    for ev in candidates:
        if str(ev.get("event_type") or "") != "candidate":
            continue
        keys.add((str(ev.get("symbol") or ""), str(ev.get("entry_time") or "")))
    return keys


def _trade_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return str(row.get("symbol") or ""), str(row.get("entry_time") or "")


def _summ_row(trades: Sequence[Any], *, scenario: str, subset: str) -> dict[str, Any]:
    met = _summarize_structural_trades(trades)
    pnls = [float(t.realized_pnl_pct) for t in trades]
    holds = [float(t.hold_duration_sec) for t in trades]
    reasons = Counter(str(t.close_reason or "") for t in trades)

    fade_exit = sum(1 for t in trades if str(t.close_reason or "") in FADE_EXIT_REASONS)
    fade_deferred = sum(1 for t in trades if bool(getattr(t, "fade_watch_entered", False)))
    breakdown_exit = sum(
        1 for t in trades if str(t.close_reason or "") in ("fade_breakdown_confirmed", "fade_hybrid_breakdown")
    )
    range_hold_prot = sum(1 for t in trades if bool(getattr(t, "fade_watch_range_hold_protected", False)))
    second_fade_ignored = sum(1 for t in trades if int(getattr(t, "second_fade_ignored_count", 0) or 0) > 0)

    return {
        "scenario": scenario,
        "subset": subset,
        "trade_count": met.get("structural_trade_count"),
        "pf": met.get("structural_pf"),
        "avg_pnl": met.get("structural_avg_pnl"),
        "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
        "max_loss": round(min(pnls), 4) if pnls else None,
        "stop_hit_count": reasons.get("stop_hit", 0),
        "session_close_count": reasons.get("session_end", 0)
        + reasons.get("morning_session_close", 0)
        + reasons.get("afternoon_session_close", 0),
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else None,
        "median_hold_sec": round(statistics.median(holds), 2) if holds else None,
        "fade_exit_count": fade_exit,
        "fade_deferred_count": fade_deferred,
        "breakdown_exit_count": breakdown_exit,
        "range_hold_protect_count": range_hold_prot,
        "second_fade_ignored_count": second_fade_ignored,
    }


def analyze_phase166(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    cap5_csv: Optional[Path] = None,
) -> dict[str, Any]:
    cap5_keys = load_cap5_only_keys(cap5_csv) if cap5_csv else set()
    poll = float(getattr(pilot_config, "live_poll_interval_sec", 5.0) or 5.0)
    session_cache: list[tuple[Path, Sequence[Mapping[str, Any]], set[tuple[str, str]]]] = []
    for sdir in session_dirs:
        evs = _load_events(sdir)
        session_cache.append((sdir, evs, _guard_pass_keys(evs)))

    scenario_rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    exit_reasons: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    all_by_scenario: dict[str, list[dict[str, Any]]] = {}

    for scenario, policy in SCENARIOS:
        trades_all: list[Any] = []
        trades_guard: list[Any] = []
        trades_cap5: list[Any] = []

        for sdir, events, guard_keys in session_cache:
            trades, _log = replay_combined_structural_exit(
                events,
                pilot_config=pilot_config,
                poll_interval_sec=poll,
                structural_exit_policy=policy,
            )
            trades_all.extend(trades)
            trades_guard.extend([t for t in trades if (t.symbol, t.entry_time) in guard_keys])
            if cap5_keys:
                trades_cap5.extend([t for t in trades if (t.symbol, t.entry_time) in cap5_keys])

            for t in trades:
                details.append(
                    {
                        "scenario": scenario,
                        "policy": policy,
                        "session": _session_id(sdir),
                        "symbol": t.symbol,
                        "entry_time": t.entry_time,
                        "close_time": t.close_time,
                        "close_reason": t.close_reason,
                        "realized_pnl_pct": t.realized_pnl_pct,
                        "hold_duration_sec": t.hold_duration_sec,
                        "fade_watch_entered": getattr(t, "fade_watch_entered", False),
                        "fade_watch_entry_time": getattr(t, "fade_watch_entry_time", ""),
                        "fade_watch_initial_reason": getattr(t, "fade_watch_initial_reason", ""),
                        "fade_watch_exit_reason": getattr(t, "fade_watch_exit_reason", ""),
                        "fade_hybrid_state": getattr(t, "fade_hybrid_state", ""),
                        "second_fade_ignored_count": getattr(t, "second_fade_ignored_count", 0),
                        "fade_watch_breakdown_confirmed": getattr(t, "fade_watch_breakdown_confirmed", ""),
                        "fade_watch_range_hold_protected": getattr(t, "fade_watch_range_hold_protected", ""),
                    }
                )

        scenario_rows.append(_summ_row(trades_all, scenario=scenario, subset="all"))
        scenario_rows.append(_summ_row(trades_guard, scenario=scenario, subset="guard_pass"))
        if cap5_keys:
            scenario_rows.append(_summ_row(trades_cap5, scenario=scenario, subset="cap5_only"))

        dist = Counter(str(t.close_reason or "") for t in trades_all)
        for reason, cnt in sorted(dist.items(), key=lambda x: (-x[1], x[0])):
            exit_reasons.append({"scenario": scenario, "close_reason": reason, "trade_count": cnt})

        all_by_scenario[scenario] = [d for d in details if d["scenario"] == scenario]

    # improved/worsened vs baseline A (same (symbol, entry_time))
    base = all_by_scenario.get("A_combined_v1") or []
    base_by = {_trade_key(r): r for r in base}
    for r in details:
        b = base_by.get(_trade_key(r))
        if not b:
            continue
        dp = float(r.get("realized_pnl_pct") or 0) - float(b.get("realized_pnl_pct") or 0)
        r["delta_vs_A"] = round(dp, 4)
        r["improved_vs_A"] = dp > 0.02
        r["worsened_vs_A"] = dp < -0.02

    base_row = next(
        (x for x in scenario_rows if x["scenario"] == "A_combined_v1" and x["subset"] == "all"),
        {},
    )
    for x in [r for r in scenario_rows if r["subset"] == "all"]:
        risk_rows.append(
            {
                "scenario": x["scenario"],
                "pf": x.get("pf"),
                "pf_delta_vs_A": round(float(x.get("pf") or 0) - float(base_row.get("pf") or 0), 4),
                "max_loss": x.get("max_loss"),
                "stop_hit_count": x.get("stop_hit_count"),
                "fade_exit_count": x.get("fade_exit_count"),
                "fade_deferred_count": x.get("fade_deferred_count"),
                "breakdown_exit_count": x.get("breakdown_exit_count"),
                "range_hold_protect_count": x.get("range_hold_protect_count"),
            }
        )

    verdict = "fade_breakdown_shadow_ready"
    notes: list[str] = []
    a_pf = float(base_row.get("pf") or 0)
    b_pf = float(next((r.get("pf") for r in scenario_rows if r["scenario"] == "B_fade_hybrid_shadow" and r["subset"] == "all"), 0) or 0)
    c_pf = float(next((r.get("pf") for r in scenario_rows if r["scenario"] == "C_fade_breakdown_shadow" and r["subset"] == "all"), 0) or 0)

    if c_pf <= a_pf + 0.02:
        verdict = "current_fade_best"
        notes.append(f"fade_breakdown PF {c_pf} not above baseline {a_pf}")
    if b_pf > c_pf + 0.03:
        verdict = "fade_hybrid_still_better"
        notes.append("hybrid PF exceeds fade_breakdown")

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "session_count": len(session_dirs),
        "scenario_rows": scenario_rows,
        "trade_details": details,
        "exit_reasons": exit_reasons,
        "risk_summary": risk_rows,
    }


def write_phase166_outputs(result: Mapping[str, Any], *, reports_dir: Path, docs_dir: Path) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": reports_dir / "phase166_fade_breakdown_shadow_review.json",
        "details": reports_dir / "phase166_fade_breakdown_trade_details.csv",
        "reasons": reports_dir / "phase166_fade_breakdown_exit_reasons.csv",
        "risk": reports_dir / "phase166_risk_summary.csv",
        "commands": reports_dir / "phase166_daily_runner_commands.json",
        "md": docs_dir / "phase166_recommendation.md",
    }
    design = {
        k: v
        for k, v in result.items()
        if k not in ("scenario_rows", "trade_details", "exit_reasons", "risk_summary")
    }
    paths["json"].write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(paths["details"], result.get("trade_details") or [])
    _write_csv(paths["reasons"], result.get("exit_reasons") or [])
    _write_csv(paths["risk"], result.get("risk_summary") or [])

    cmd = {
        "fade_breakdown": (
            "python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py "
            "--universe-mode core10-dynamic40-price-risk-filter-shadow "
            "--enable-intraday-refresh "
            "--exit-policy-shadow fade-breakdown"
        )
    }
    paths["commands"].write_text(json.dumps(cmd, ensure_ascii=False, indent=2), encoding="utf-8")

    rows_all = [r for r in (result.get("scenario_rows") or []) if str(r.get("subset")) == "all"]
    lines = [
        "# Phase 166: fade breakdown shadow recommendation",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        "",
        "## Scenario summary (subset=all)",
        "",
        "| Scenario | PF | avg PnL | fade_exit | fade_deferred | breakdown_exit | max_loss | stop_hit |",
        "|----------|----|---------|----------:|--------------:|--------------:|---------:|--------:|",
    ]
    for r in rows_all:
        lines.append(
            f"| {r.get('scenario')} | {r.get('pf')} | {r.get('avg_pnl')} | "
            f"{r.get('fade_exit_count')} | {r.get('fade_deferred_count')} | {r.get('breakdown_exit_count')} | "
            f"{r.get('max_loss')} | {r.get('stop_hit_count')} |"
        )

    lines.extend(["", "## Live shadow command", "", "```powershell", cmd["fade_breakdown"], "```", ""])
    if result.get("verdict_notes"):
        lines.extend(["## Notes", ""])
        for n in result.get("verdict_notes") or []:
            lines.append(f"- {n}")
    lines.extend(["", "## Constraints", "", "- Shadow only; order_enabled=false; paper_only=true; cap=3."])
    paths["md"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}
