"""
Phase 150: Urgent exit-failure what-if for 2026-05-25 AM (review only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.continuation_quality_ranking import continuation_components
from research.research_exit_criteria import _as_float
from research.runtime_pilot_policy_review import _build_price_index, _parse_ts
from research.small_paper_performance_review import (
    _build_trade_lifecycles,
    _load_events,
    _load_json,
    _profit_factor,
    _summarize_trades,
)
from research.structural_exit_design_review import (
    EvalPath,
    _lower_high_on_ticks,
    _path_mfe_mae,
    _vwap_break_on_ticks,
    build_eval_paths,
)
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    TRAILING_GIVEBACK_PCT,
    VWAP_BREAK_PEAK_PNL,
    simulate_structural_policy,
)
from research.structural_observer_review import (
    _legacy_virtual_hold_summary,
    _pnl_pct,
    _session_end_time,
    _summarize_structural_trades,
    replay_combined_structural_exit_v1,
)
from small_paper.discord_notifier import observer_tracker_config_from_pilot

LOWER_HIGH_TICKS = 3
POST_EXIT_HORIZONS = (30, 60, 180)


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    label: str
    policy_key: str


SCENARIOS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec("A", "combined_structural_exit_v1", "combined"),
    ScenarioSpec("B", "legacy_virtual_hold", "legacy"),
    ScenarioSpec("C", "stop_hit_only", "stop_only"),
    ScenarioSpec("D", "stop_hit_session_close_only", "stop_session"),
    ScenarioSpec("E", "disable_momentum_fade_exit", "no_momentum"),
    ScenarioSpec("F", "disable_quality_decay_exit", "no_quality"),
    ScenarioSpec("G", "disable_momentum_and_quality_fade", "no_fade_both"),
    ScenarioSpec("H", "momentum_fade_breakdown_confirmed", "momentum_breakdown"),
    ScenarioSpec("I", "take_observer_as_exit", "take_exit"),
)


def _metrics_row(
    scenario_id: str,
    label: str,
    pnls: Sequence[float],
    *,
    exit_reasons: Optional[Counter[str]] = None,
    note: str = "",
) -> dict[str, Any]:
    pnls_list = list(pnls)
    pf = _profit_factor(pnls_list)
    return {
        "scenario_id": scenario_id,
        "scenario": label,
        "trade_count": len(pnls_list),
        "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "avg_pnl_pct": round(statistics.mean(pnls_list), 4) if pnls_list else None,
        "win_rate": round(sum(1 for p in pnls_list if p > 0) / len(pnls_list), 4) if pnls_list else None,
        "sum_pnl_pct": round(sum(pnls_list), 4) if pnls_list else None,
        "exit_reason_top": exit_reasons.most_common(3) if exit_reasons else [],
        "note": note,
    }


def _simulate_phase150(
    ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    cfg: Any,
    policy_key: str,
    *,
    path: Optional[EvalPath] = None,
) -> tuple[float, str]:
    if not ticks:
        return 0.0, "no_ticks"

    if policy_key == "take_exit" and path and path.take_time and path.take_pnl_pct is not None:
        return float(path.take_pnl_pct), "take_exit"

    if policy_key in ("combined",):
        r = simulate_structural_policy(
            ticks, entry_price, POLICY_COMBINED_STRUCTURAL_EXIT_V1, cfg, allow_session_end=True
        )
        return r if r else (float(ticks[-1].get("pnl_pct") or 0), "session_end")

    if policy_key == "stop_only":
        r = simulate_structural_policy(ticks, entry_price, "stop_only_exit", cfg, allow_session_end=True)
        return r if r else (float(ticks[-1].get("pnl_pct") or 0), "session_end")

    if policy_key == "stop_session":
        stop = entry_price * (1.0 - cfg.hard_stop_pct / 100.0)
        for t in ticks:
            px = float(t.get("price") or entry_price)
            if px <= stop:
                return float(t.get("pnl_pct") or 0), "stop_hit"
        return float(ticks[-1].get("pnl_pct") or 0), "session_end"

    entry = entry_price
    stop = entry * (1.0 - cfg.hard_stop_pct / 100.0)
    peak_q = peak_pnl = peak_mom = peak_fav = 0.0

    for i, t in enumerate(ticks):
        px = float(t.get("price") or entry)
        pnl = float(t.get("pnl_pct") or 0)
        q = float(t.get("quality") or 0)
        mom = float(t.get("momentum") or 0)
        fav = float(t.get("favorable") or 0)
        peak_q = max(peak_q, q)
        peak_pnl = max(peak_pnl, pnl)
        peak_mom = max(peak_mom, mom)
        peak_fav = max(peak_fav, fav)
        tick_slice = ticks[: i + 1]

        if px <= stop:
            return pnl, "stop_hit"

        use_quality = policy_key not in ("no_quality", "no_fade_both")
        use_momentum = policy_key not in ("no_momentum", "no_fade_both")

        if use_quality and q <= peak_q - cfg.take_quality_drop:
            return pnl, "quality_decay_exit"

        if use_momentum:
            if policy_key == "momentum_breakdown":
                mom_weak = peak_mom > 0 and mom < peak_mom * cfg.momentum_weaken_ratio
                if mom_weak and (
                    _lower_high_on_ticks(tick_slice)
                    or _vwap_break_on_ticks(tick_slice, entry)
                ):
                    return pnl, "momentum_fade_exit"
            elif peak_mom > 0 and mom < peak_mom * cfg.momentum_weaken_ratio:
                return pnl, "momentum_fade_exit"

        if policy_key not in ("no_fade_both", "no_momentum", "no_quality", "momentum_breakdown"):
            if peak_fav > 0 and fav < peak_fav * cfg.favorable_fade_ratio:
                return pnl, "favorable_fade_exit"
            if peak_pnl > VWAP_BREAK_PEAK_PNL and pnl < 0:
                return pnl, "vwap_break_exit"
            if peak_pnl > 0 and pnl <= peak_pnl - TRAILING_GIVEBACK_PCT:
                return pnl, "mfe_giveback_exit"

    return float(ticks[-1].get("pnl_pct") or 0), "session_end"


def _path_lookup(paths: Sequence[EvalPath]) -> dict[tuple[str, str], EvalPath]:
    return {(p.symbol, p.entry_time): p for p in paths}


def run_exit_whatif_scenarios(
    paths: Sequence[EvalPath],
    events: Sequence[Mapping[str, Any]],
    *,
    pilot_config: Any,
    actual_trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    cfg = observer_tracker_config_from_pilot(pilot_config)
    path_by_key = _path_lookup(paths)
    rows: list[dict[str, Any]] = []

    # A: actual combined replay trades
    a_pnls = [float(t.get("realized_pnl_pct") or 0) for t in actual_trades]
    a_reasons = Counter(str(t.get("close_reason") or "") for t in actual_trades)
    rows.append(_metrics_row("A", SCENARIOS[0].label, a_pnls, exit_reasons=a_reasons, note="structural_trades.csv"))

    # B: legacy virtual hold
    lifecycles = _build_trade_lifecycles(events)
    leg = _summarize_trades(lifecycles)
    b_pnls = [t.realized_pnl_pct for t in lifecycles]
    rows.append(
        _metrics_row(
            "B",
            SCENARIOS[1].label,
            b_pnls,
            note=f"legacy_pf={leg.get('profit_factor')}",
        )
    )

    for spec in SCENARIOS[2:]:
        pnls: list[float] = []
        reasons: Counter[str] = Counter()
        for t in actual_trades:
            key = (str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            p = path_by_key.get(key)
            if p is None or not p.ticks:
                pnls.append(float(t.get("realized_pnl_pct") or 0))
                reasons[str(t.get("close_reason") or "missing_path")] += 1
                continue
            pnl, reason = _simulate_phase150(
                p.ticks, p.entry_price, cfg, spec.policy_key, path=p
            )
            pnls.append(pnl)
            reasons[reason] += 1
        rows.append(_metrics_row(spec.scenario_id, spec.label, pnls, exit_reasons=reasons))

    return rows


def exit_reason_pnl_breakdown(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_reason: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_reason[str(t.get("close_reason") or "unknown")].append(
            float(t.get("realized_pnl_pct") or 0)
        )

    rows: list[dict[str, Any]] = []
    for reason, pnls in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        pf = _profit_factor(pnls)
        rows.append(
            {
                "close_reason": reason,
                "trade_count": len(pnls),
                "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
                "median_pnl_pct": round(statistics.median(pnls), 4) if pnls else None,
                "sum_pnl_pct": round(sum(pnls), 4),
                "profit_factor": round(pf, 4) if pf not in (None, float("inf")) else pf,
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
            }
        )

    # take_before_exit split
    with_take = [t for t in trades if t.get("take_time") or t.get("had_take_before_exit")]
    without_take = [t for t in trades if t not in with_take]
    for label, subset in (("with_take_before_exit", with_take), ("without_take_before_exit", without_take)):
        pnls = [float(t.get("realized_pnl_pct") or 0) for t in subset]
        pf = _profit_factor(pnls)
        rows.append(
            {
                "close_reason": label,
                "trade_count": len(pnls),
                "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
                "median_pnl_pct": round(statistics.median(pnls), 4) if pnls else None,
                "sum_pnl_pct": round(sum(pnls), 4) if pnls else None,
                "profit_factor": round(pf, 4) if pf not in (None, float("inf")) else pf,
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
            }
        )
    return rows


def _post_exit_best_pnl(
    entry_px: float,
    exit_ts: float,
    price_series: Sequence[tuple[float, float]],
    horizon_sec: float,
) -> Optional[float]:
    end = exit_ts + horizon_sec
    prices = [px for ts, px in price_series if exit_ts < ts <= end]
    if entry_px <= 0 or not prices:
        return None
    return round(max((p - entry_px) / entry_px * 100.0 for p in prices), 4)


def momentum_fade_after_exit_paths(
    trades: Sequence[Mapping[str, Any]],
    paths: Sequence[EvalPath],
    price_index: Mapping[str, list[tuple[float, float]]],
) -> list[dict[str, Any]]:
    path_by_key = _path_lookup(paths)
    rows: list[dict[str, Any]] = []

    for t in trades:
        if str(t.get("close_reason") or "") != "momentum_fade_exit":
            continue
        sym = str(t.get("symbol") or "")
        ent = str(t.get("entry_time") or "")
        close_time = str(t.get("close_time") or "")
        entry_px = float(t.get("entry_price") or 0)
        exit_pnl = float(t.get("realized_pnl_pct") or 0)
        mfe = float(t.get("mfe_pct") or 0)
        p = path_by_key.get((sym, ent))
        ticks = p.ticks if p else []
        exit_ts = _parse_ts(close_time) if close_time else (ticks[-1]["ts_epoch"] if ticks else 0.0)

        breakdown = False
        vwap_break = False
        high_range = False
        if ticks and p:
            breakdown = _lower_high_on_ticks(ticks)
            vwap_break = _vwap_break_on_ticks(ticks, entry_px)
            peak_pnl = max(float(x.get("pnl_pct") or 0) for x in ticks)
            last_pnl = float(ticks[-1].get("pnl_pct") or 0)
            high_range = peak_pnl >= 0.25 and last_pnl >= peak_pnl - 0.12

        series = price_index.get(sym, [])
        post: dict[str, Any] = {}
        for h in POST_EXIT_HORIZONS:
            post[f"best_pnl_{h}s_after_exit"] = _post_exit_best_pnl(entry_px, exit_ts, series, h)

        new_high = any(
            (v is not None and v > exit_pnl + 0.05) for v in post.values()
        )
        mfe_extended = any((v is not None and v > mfe + 0.03) for v in post.values())

        rows.append(
            {
                "symbol": sym,
                "entry_time": ent,
                "close_time": close_time,
                "exit_pnl_pct": exit_pnl,
                "mfe_pct": mfe,
                "had_take_before_exit": bool(t.get("take_time")),
                "take_pnl_pct": t.get("take_pnl_pct"),
                "breakdown_at_exit": breakdown,
                "vwap_break_proxy_at_exit": vwap_break,
                "high_range_at_exit": high_range,
                "new_high_after_exit": new_high,
                "mfe_updated_after_exit": mfe_extended,
                **post,
            }
        )
    return rows


def _fade_reason_stats(fade_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(fade_rows)
    if not n:
        return {}
    return {
        "momentum_fade_exit_count": n,
        "new_high_after_exit_count": sum(1 for r in fade_rows if r.get("new_high_after_exit")),
        "mfe_updated_after_exit_count": sum(1 for r in fade_rows if r.get("mfe_updated_after_exit")),
        "breakdown_at_exit_count": sum(1 for r in fade_rows if r.get("breakdown_at_exit")),
        "vwap_break_at_exit_count": sum(1 for r in fade_rows if r.get("vwap_break_proxy_at_exit")),
        "high_range_at_exit_count": sum(1 for r in fade_rows if r.get("high_range_at_exit")),
        "avg_exit_pnl_pct": round(
            statistics.mean(float(r.get("exit_pnl_pct") or 0) for r in fade_rows), 4
        ),
    }


def determine_phase150_verdict(
    scenarios: Sequence[Mapping[str, Any]],
    *,
    current_pf: float,
) -> tuple[str, list[str]]:
    by_id = {str(r["scenario_id"]): r for r in scenarios}
    notes: list[str] = []

    def pf(sid: str) -> float:
        v = by_id.get(sid, {}).get("structural_pf")
        return float(v) if v is not None else 0.0

    structural_ids = ("A", "C", "D", "E", "F", "G", "H", "I")
    structural = [r for r in scenarios if str(r.get("scenario_id")) in structural_ids]
    best_struct = max(structural, key=lambda r: float(r.get("structural_pf") or 0))
    notes.append(
        f"best_structural scenario={best_struct.get('scenario_id')} "
        f"pf={best_struct.get('structural_pf')} (legacy B={pf('B'):.4f} reference only)"
    )

    if pf("I") >= pf("A") + 0.15 and pf("I") >= max(pf("E"), pf("H"), pf("C")):
        return "take_exit_promising", notes + [
            f"take_observer_as_exit PF={pf('I'):.4f} vs combined={current_pf:.4f}."
        ]

    fade_lift = max(pf("E"), pf("F")) - current_pf
    if fade_lift >= 0.15 and pf("E") >= pf("F"):
        return "disable_fade_exit_promising", notes + [
            f"disable_momentum_fade PF={pf('E'):.4f} (+{fade_lift:.4f} vs combined); "
            "quality_decay alone is weaker (F)."
        ]
    if fade_lift >= 0.15:
        return "disable_fade_exit_promising", notes + [
            f"disable_quality_decay PF={pf('F'):.4f} (+{fade_lift:.4f} vs combined)."
        ]

    if pf("C") > current_pf + 0.05 and pf("C") >= pf("E"):
        return "stop_only_safer", notes + [
            "Stop/session-only slightly better than combined on path replay."
        ]

    if current_pf >= float(best_struct.get("structural_pf") or 0) - 0.02:
        return "current_exit_best", notes + ["Combined matches best structural what-if on replay."]

    return "need_more_data_but_current_exit_risky", notes + [
        f"combined PF={current_pf:.4f} failed; best structural PF={best_struct.get('structural_pf')}; "
        f"legacy_virtual_hold PF={pf('B'):.4f} (not deployable)."
    ]


def build_recommendation_md(
    *,
    verdict: str,
    verdict_notes: Sequence[str],
    scenarios: Sequence[Mapping[str, Any]],
    fade_stats: Mapping[str, Any],
    breakdown_rows: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# Phase 150 — Urgent exit failure review (2026-05-25 AM)",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        "## Summary",
        "",
    ]
    for n in verdict_notes:
        lines.append(f"- {n}")
    lines.extend(["", "## Exit what-if scenarios", "", "| ID | Scenario | Trades | PF | Avg PnL % |", "|---|---|---|---:|---:|"])
    for r in scenarios:
        lines.append(
            f"| {r.get('scenario_id')} | {r.get('scenario')} | {r.get('trade_count')} | "
            f"{r.get('structural_pf')} | {r.get('avg_pnl_pct')} |"
        )
    lines.extend(["", "## Momentum fade flat-exit signals", ""])
    for k, v in sorted(fade_stats.items()):
        lines.append(f"- {k}: {v}")
    if breakdown_rows:
        wrong = sum(
            1
            for r in breakdown_rows
            if r.get("new_high_after_exit") and not r.get("breakdown_at_exit")
        )
        lines.append(f"- likely_premature_fade (new high, no breakdown): {wrong}/{len(breakdown_rows)}")
    lines.extend(
        [
            "",
            "## Emergency candidates (what-if only — not production)",
            "",
            "1. **Disable momentum_fade_exit** — largest loss bucket; many exits show post-exit upside.",
            "2. **Disable quality_decay_exit** — secondary fade source; test together with (1).",
            "3. **Fade only with breakdown** — momentum fade gated on lower-high or VWAP-break proxy.",
            "4. **Take-as-exit** — compare observer TAKE PnL vs structural fade exits.",
            "5. **Stop + session close only** — baseline cap on structure exits; overlap handling unchanged.",
            "",
            "Do not change production YAML until a follow-up shadow session confirms PF lift.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_phase150_urgent_exit_review(
    session_dir: Path,
    *,
    pilot_config: Any,
    reports_dir: Path,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    events = _load_events(session_dir)
    summary = _load_json(session_dir / "small_paper_summary.json") or {}
    session_end = _session_end_time(events)
    interval = float(summary.get("poll_interval_sec") or 5.0)

    trades_path = session_dir / "structural_trades.csv"
    with trades_path.open(encoding="utf-8", newline="") as f:
        actual_trades = list(csv.DictReader(f))

    paths = build_eval_paths(events, session_end=session_end)
    price_index = _build_price_index(events)

    scenarios = run_exit_whatif_scenarios(
        paths, events, pilot_config=pilot_config, actual_trades=actual_trades
    )
    breakdown = exit_reason_pnl_breakdown(actual_trades)
    fade_paths = momentum_fade_after_exit_paths(actual_trades, paths, price_index)
    fade_stats = _fade_reason_stats(fade_paths)

    current_pf = float(
        next((r["structural_pf"] for r in scenarios if r["scenario_id"] == "A"), 0) or 0
    )
    verdict, verdict_notes = determine_phase150_verdict(scenarios, current_pf=current_pf)

    legacy = _legacy_virtual_hold_summary(events)
    review_json = _load_json(session_dir / "structural_observer_review.json") or {}

    report: dict[str, Any] = {
        "phase": 150,
        "mode": "urgent_exit_failure_review",
        "what_if_only": True,
        "session_dir": str(session_dir),
        "session_date": "20260525",
        "official_verdict": review_json.get("official_verdict"),
        "combined_structural_exit_v1": {
            "structural_pf": review_json.get("structural_pf"),
            "structural_avg_pnl": review_json.get("structural_avg_pnl"),
            "structural_trade_count": review_json.get("structural_trade_count"),
            "exit_reason_distribution": review_json.get("exit_reason_distribution"),
        },
        "legacy_virtual_hold": legacy,
        "accepted_count": summary.get("accepted_count"),
        "rejected_count": summary.get("rejected_count"),
        "verdict": verdict,
        "verdict_options": {
            "A": "disable_fade_exit_promising",
            "B": "take_exit_promising",
            "C": "stop_only_safer",
            "D": "current_exit_best",
            "E": "need_more_data_but_current_exit_risky",
        },
        "verdict_notes": verdict_notes,
        "exit_whatif_scenarios": scenarios,
        "fade_exit_stats": fade_stats,
        "constraints": [
            "no_production_yaml_change",
            "what_if_review_only",
            "no_auto_order",
        ],
    }

    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(reports_dir / "phase150_exit_whatif_scenarios.csv", scenarios)
    _write_csv(reports_dir / "phase150_exit_reason_pnl_breakdown.csv", breakdown)
    _write_csv(reports_dir / "phase150_momentum_fade_after_exit_paths.csv", fade_paths)
    (reports_dir / "phase150_urgent_exit_failure_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (reports_dir / "phase150_recommendation.md").write_text(
        build_recommendation_md(
            verdict=verdict,
            verdict_notes=verdict_notes,
            scenarios=scenarios,
            fade_stats=fade_stats,
            breakdown_rows=fade_paths,
        ),
        encoding="utf-8",
    )
    report["output_files"] = {
        "json": str(reports_dir / "phase150_urgent_exit_failure_review.json"),
        "whatif_csv": str(reports_dir / "phase150_exit_whatif_scenarios.csv"),
        "breakdown_csv": str(reports_dir / "phase150_exit_reason_pnl_breakdown.csv"),
        "fade_paths_csv": str(reports_dir / "phase150_momentum_fade_after_exit_paths.csv"),
        "recommendation_md": str(reports_dir / "phase150_recommendation.md"),
    }
    return report


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
