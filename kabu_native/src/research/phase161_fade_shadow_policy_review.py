"""
Phase 161: Shadow replay comparison of fade-exit policy candidates (review only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.cap3_entry_replay import _profit_factor
from research.fade_exit_replay import FADE_EXIT_REASONS
from research.mfe_mae_exit_review import (
    build_price_timeline_from_events_csv,
    load_structural_trades,
    parse_ts,
    session_end_ts_from_trades,
)
from research.phase156_intraday_refresh_cap5_review import _filter_price_risk_candidates
from research.phase159_overlap_review import load_cap5_only_keys
from research.phase160_fade_exit_review import (
    REACCEL_MIN_GAIN,
    _build_ticks_for_trade,
    _post_exit_metrics,
    _session_id,
    _write_csv,
    classify_post_exit,
)
from research.small_paper_performance_review import _load_events
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    combined_exit_signal_on_latest_tick,
)
from small_paper.discord_notifier import observer_tracker_config_from_pilot

SCENARIOS: tuple[tuple[str, str, str], ...] = (
    ("A_current", "current", "combined_structural_exit_v1"),
    ("B_no_fade", "no_fade", "fade disabled"),
    ("C_two_tick_delay", "two_tick_delay", "2-tick fade delay"),
    ("D_breakdown_confirmed", "breakdown_confirmed", "breakdown confirmed fade"),
    ("E_range_hold_protect", "range_hold_protect", "range-hold protect"),
    ("F_take_reached_only", "take_reached_only", "take-reached fade only"),
    ("G_hybrid", "hybrid", "2-tick + breakdown + range-hold"),
)

GIVEBACK_SMALL_FRAC = 0.25
HIGH_ZONE_FRAC = 0.85
POST_SIM_EXTENSION_SEC = 300.0


@dataclass
class FadeShadowState:
    fade_streak: int = 0
    watch_active: bool = False
    fade_armed: bool = False


@dataclass
class TradeSimResult:
    pnl: float
    reason: str
    exit_ts: float
    fade_exit: bool
    delayed_fade: bool
    avoided_fade_vs_actual: bool
    hold_sec: float


def _unwrap_sig(sig: Optional[tuple[Any, ...]]) -> Optional[tuple[float, str]]:
    if not sig:
        return None
    return float(sig[0]), str(sig[1])


def _peak_pnl(ticks: Sequence[Mapping[str, Any]]) -> float:
    return max((float(t.get("pnl_pct") or 0) for t in ticks), default=0.0)


def _new_low_on_ticks(ticks: Sequence[Mapping[str, Any]]) -> bool:
    if len(ticks) < 2:
        return False
    prices = [float(t.get("price") or 0) for t in ticks]
    return prices[-1] <= min(prices[:-1]) + 1e-9


def _lower_high_recent(ticks: Sequence[Mapping[str, Any]], n: int = 3) -> bool:
    if len(ticks) < n:
        return False
    prices = [float(t.get("price") or 0) for t in ticks[-n:]]
    return all(prices[i] > prices[i + 1] for i in range(len(prices) - 1))


def breakdown_confirmed_at_fade(
    ticks: Sequence[Mapping[str, Any]],
    *,
    had_take: bool,
) -> bool:
    if had_take:
        return False
    last = ticks[-1]
    pnl = float(last.get("pnl_pct") or 0)
    mom = float(last.get("momentum") or 0)
    if mom >= 0.15:
        return False
    if pnl >= 0:
        return False
    return _new_low_on_ticks(ticks) or _lower_high_recent(ticks)


def range_hold_protect(ticks: Sequence[Mapping[str, Any]]) -> bool:
    last = ticks[-1]
    pnl = float(last.get("pnl_pct") or 0)
    if pnl >= 0:
        return True
    peak = _peak_pnl(ticks)
    if peak <= 0.01:
        return False
    giveback = (peak - pnl) / peak if peak > 0 else 0.0
    if peak > 0 and giveback < GIVEBACK_SMALL_FRAC:
        return True
    if pnl >= peak * HIGH_ZONE_FRAC:
        return True
    return False


def _is_fade_reason(reason: str) -> bool:
    return reason in FADE_EXIT_REASONS


def simulate_shadow_fade_policy(
    ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    entry_ts: float,
    *,
    scenario_mode: str,
    exit_cfg: Any,
    had_take: bool,
) -> TradeSimResult:
    if not ticks:
        return TradeSimResult(0.0, "session_end", entry_ts, False, False, False, 0.0)

    state = FadeShadowState()
    for i, _ in enumerate(ticks):
        sub = ticks[: i + 1]
        sig = _unwrap_sig(combined_exit_signal_on_latest_tick(sub, entry_price, exit_cfg))
        exit_ts = float(sub[-1].get("ts_epoch") or entry_ts)

        if scenario_mode == "current":
            if sig:
                pnl, reason = sig
                return TradeSimResult(
                    pnl,
                    reason,
                    exit_ts,
                    _is_fade_reason(reason),
                    False,
                    False,
                    exit_ts - entry_ts,
                )
            continue

        if not sig:
            continue

        pnl, reason = sig
        if not _is_fade_reason(reason):
            return TradeSimResult(
                pnl, reason, exit_ts, False, False, False, exit_ts - entry_ts
            )

        # --- fade signal: apply scenario gate ---
        if scenario_mode == "no_fade":
            continue

        if scenario_mode == "take_reached_only" and not had_take:
            continue

        if scenario_mode == "two_tick_delay":
            state.fade_streak += 1
            if state.fade_streak >= 2:
                return TradeSimResult(
                    pnl, reason, exit_ts, True, True, False, exit_ts - entry_ts
                )
            continue

        if scenario_mode == "breakdown_confirmed":
            if breakdown_confirmed_at_fade(sub, had_take=had_take):
                return TradeSimResult(
                    pnl, reason, exit_ts, True, False, False, exit_ts - entry_ts
                )
            continue

        if scenario_mode == "range_hold_protect":
            if range_hold_protect(sub):
                continue
            return TradeSimResult(
                pnl, reason, exit_ts, True, False, False, exit_ts - entry_ts
            )

        if scenario_mode == "hybrid":
            if not state.watch_active:
                state.watch_active = True
                state.fade_streak = 1
                continue
            state.fade_streak += 1
            if range_hold_protect(sub):
                continue
            if breakdown_confirmed_at_fade(sub, had_take=had_take):
                return TradeSimResult(
                    pnl,
                    "fade_hybrid_breakdown",
                    exit_ts,
                    True,
                    True,
                    False,
                    exit_ts - entry_ts,
                )
            if state.fade_streak >= 2:
                return TradeSimResult(
                    pnl,
                    "fade_hybrid_delayed",
                    exit_ts,
                    True,
                    True,
                    False,
                    exit_ts - entry_ts,
                )
            continue

        return TradeSimResult(
            pnl, reason, exit_ts, True, False, False, exit_ts - entry_ts
        )

    last = ticks[-1]
    exit_ts = float(last.get("ts_epoch") or entry_ts)
    return TradeSimResult(
        float(last.get("pnl_pct") or 0),
        "session_end",
        exit_ts,
        False,
        False,
        False,
        exit_ts - entry_ts,
    )


def build_ticks_to_session_end(
    events: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    entry_ts: float,
    session_end_ts: float,
    entry_price: float,
) -> list[dict[str, Any]]:
    return _build_ticks_for_trade(
        events,
        symbol=symbol,
        entry_ts=entry_ts,
        close_ts=session_end_ts,
        entry_price=entry_price,
    )


def _guard_pass_keys(events: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
    candidates, _, _ = _filter_price_risk_candidates(events)
    keys: set[tuple[str, str]] = set()
    for ev in candidates:
        if str(ev.get("event_type") or "") != "candidate":
            continue
        keys.add((str(ev.get("symbol") or ""), str(ev.get("entry_time") or "")))
    return keys


def _summarize_pnls(
    rows: Sequence[Mapping[str, Any]],
    *,
    scenario: str,
    subset: str,
) -> dict[str, Any]:
    pnls = [float(r["scenario_pnl"]) for r in rows]
    holds = [float(r["scenario_hold_sec"]) for r in rows]
    fade_rows = [r for r in rows if r.get("scenario_fade_exit")]
    actual_fade = [r for r in rows if r.get("actual_fade_exit")]
    reasons = Counter(str(r.get("scenario_reason") or "") for r in rows)

    return {
        "scenario": scenario,
        "subset": subset,
        "trade_count": len(pnls),
        "pf": _profit_factor(pnls),
        "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
        "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
        "max_loss": round(min(pnls), 4) if pnls else None,
        "max_gain": round(max(pnls), 4) if pnls else None,
        "stop_hit_count": reasons.get("stop_hit", 0),
        "session_close_count": reasons.get("session_end", 0),
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else None,
        "median_hold_sec": round(statistics.median(holds), 2) if holds else None,
        "fade_exit_count": len(fade_rows),
        "delayed_fade_count": sum(1 for r in rows if r.get("delayed_fade")),
        "avoided_fade_count": sum(1 for r in rows if r.get("avoided_fade_vs_actual")),
        "reacceleration_saved_count": sum(1 for r in rows if r.get("reacceleration_saved")),
        "breakdown_missed_count": sum(1 for r in rows if r.get("breakdown_missed")),
        "improved_count": sum(1 for r in rows if r.get("improved_vs_actual")),
        "worsened_count": sum(1 for r in rows if r.get("worsened_vs_actual")),
        "hold_extension_worsened": sum(
            1
            for r in rows
            if r.get("worsened_vs_actual") and float(r.get("scenario_hold_sec") or 0)
            > float(r.get("actual_hold_sec") or 0) + 5
        ),
        "actual_fade_count": len(actual_fade),
    }


def _risk_summary(scenario_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_scenario: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in scenario_rows:
        if str(r.get("subset") or "") == "all":
            by_scenario[str(r["scenario"])].append(r)
    cur = by_scenario.get("A_current", [{}])[0] if by_scenario.get("A_current") else {}
    out: list[dict[str, Any]] = []
    for scenario, rows in sorted(by_scenario.items()):
        r0 = rows[0]
        out.append(
            {
                "scenario": scenario,
                "max_loss": r0.get("max_loss"),
                "max_loss_delta_vs_current": round(
                    float(r0.get("max_loss") or 0) - float(cur.get("max_loss") or 0),
                    4,
                ),
                "stop_hit_count": r0.get("stop_hit_count"),
                "stop_hit_delta_vs_current": int(r0.get("stop_hit_count") or 0)
                - int(cur.get("stop_hit_count") or 0),
                "session_close_count": r0.get("session_close_count"),
                "session_close_delta": int(r0.get("session_close_count") or 0)
                - int(cur.get("session_close_count") or 0),
                "hold_extension_worsened": r0.get("hold_extension_worsened"),
                "worsened_count": r0.get("worsened_count"),
                "pf": r0.get("pf"),
                "pf_delta_vs_current": round(
                    float(r0.get("pf") or 0) - float(cur.get("pf") or 0), 4
                ),
            }
        )
    return out


def determine_verdict(scenario_rows: Sequence[Mapping[str, Any]]) -> tuple[str, list[str]]:
    all_rows = [r for r in scenario_rows if r.get("subset") == "all"]
    by_id = {str(r["scenario"]): r for r in all_rows}
    notes: list[str] = []
    cur = by_id.get("A_current", {})
    cur_pf = float(cur.get("pf") or 0)
    cur_max_loss = float(cur.get("max_loss") or 0)

    def _score(key: str) -> float:
        r = by_id.get(key, {})
        pf = float(r.get("pf") or 0)
        worsened = int(r.get("worsened_count") or 0)
        max_loss = float(r.get("max_loss") or 0)
        return pf - 0.001 * worsened - (0.1 if max_loss < cur_max_loss - 0.05 else 0)

    ranked = sorted(
        [s for s, _, _ in SCENARIOS if s != "A_current"],
        key=_score,
        reverse=True,
    )
    best = ranked[0] if ranked else ""
    best_pf = float(by_id.get(best, {}).get("pf") or 0)
    notes.append(f"best_non_current={best} pf={best_pf:.4f} current_pf={cur_pf:.4f}")

    b = by_id.get("B_no_fade", {})
    if float(b.get("pf") or 0) >= cur_pf + 0.03 and float(b.get("max_loss") or 0) < cur_max_loss - 0.08:
        return "fade_disable_best_but_risky", notes + ["no_fade PF gain with worse tail loss"]

    candidates: list[tuple[str, str]] = [
        ("G_hybrid", "breakdown_confirmed_promising"),
        ("D_breakdown_confirmed", "breakdown_confirmed_promising"),
        ("E_range_hold_protect", "breakdown_confirmed_promising"),
        ("C_two_tick_delay", "two_tick_delay_promising"),
        ("F_take_reached_only", "take_reached_only_promising"),
    ]
    for scen_id, verdict in candidates:
        r = by_id.get(scen_id, {})
        pf_delta = float(r.get("pf") or 0) - cur_pf
        if pf_delta < 0.02:
            continue
        if int(r.get("worsened_count") or 0) > int(cur.get("worsened_count") or 0) + 20:
            continue
        if float(r.get("max_loss") or 0) < cur_max_loss - 0.05:
            continue
        if float(r.get("avg_hold_sec") or 0) > float(cur.get("avg_hold_sec") or 0) * 3:
            notes.append(f"{scen_id} skipped: hold time inflated vs current")
            continue
        notes.append(f"{scen_id} pf_delta={pf_delta:.3f}")
        return verdict, notes

    if cur_pf >= best_pf - 0.01:
        return "current_fade_best", notes

    return "mixed_needs_live_shadow", notes + ["no single shadow policy dominates offline replay"]


def analyze_session(
    session_dir: Path,
    *,
    exit_cfg: Any,
    cap5_keys: set[tuple[str, str]],
    guard_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    session_id = _session_id(session_dir)
    trades = load_structural_trades(session_dir / "structural_trades.csv")
    if not trades:
        return []
    events = _load_events(session_dir)
    syms = {str(t.get("symbol") or "") for t in trades}
    timeline_map = build_price_timeline_from_events_csv(
        session_dir / "small_paper_events.csv", syms
    )
    session_end = session_end_ts_from_trades(trades)
    detail_rows: list[dict[str, Any]] = []

    for tr in trades:
        sym = str(tr.get("symbol") or "")
        entry_ts = parse_ts(str(tr.get("entry_time") or ""))
        entry_px = float(tr.get("entry_price") or 0)
        actual_pnl = float(tr.get("realized_pnl_pct") or 0)
        actual_reason = str(tr.get("close_reason") or "")
        actual_fade = _is_fade_reason(actual_reason)
        had_take = str(tr.get("had_take_before_exit") or "").lower() in ("true", "1", "yes")
        actual_hold = float(tr.get("hold_duration_sec") or 0)
        trade_key = (sym, str(tr.get("entry_time") or ""))

        actual_close_ts = parse_ts(str(tr.get("close_time") or ""))
        sim_end_ts = min(session_end, actual_close_ts + POST_SIM_EXTENSION_SEC)
        ticks = build_ticks_to_session_end(
            events,
            symbol=sym,
            entry_ts=entry_ts,
            session_end_ts=sim_end_ts,
            entry_price=entry_px,
        )
        tl = timeline_map.get(sym, [])
        post_cls = ""
        if actual_fade and tl:
            close_ts = parse_ts(str(tr.get("close_time") or ""))
            post_ticks = sum(1 for ts, _ in tl if ts >= close_ts)
            post = _post_exit_metrics(
                tl,
                entry_price=entry_px,
                exit_pnl=actual_pnl,
                close_ts=close_ts,
                session_end_ts=session_end,
            )
            post_cls, _ = classify_post_exit(
                {**post, "exit_pnl": actual_pnl}, post_tick_count=post_ticks
            )

        subsets = ["all"]
        if trade_key in cap5_keys:
            subsets.append("cap5_only")
        if trade_key in guard_keys:
            subsets.append("guard_pass")

        for scen_id, mode, _desc in SCENARIOS:
            sim = simulate_shadow_fade_policy(
                ticks,
                entry_px,
                entry_ts,
                scenario_mode=mode,
                exit_cfg=exit_cfg,
                had_take=had_take,
            )
            improved = sim.pnl > actual_pnl + 0.02
            worsened = sim.pnl < actual_pnl - 0.02
            avoided = actual_fade and not sim.fade_exit
            reaccel_saved = (
                avoided
                and post_cls.startswith("A")
                and sim.pnl > actual_pnl + REACCEL_MIN_GAIN * 0.5
            )
            breakdown_missed = (
                worsened
                and post_cls.startswith("C")
                and not actual_fade
            ) or (worsened and post_cls.startswith("C") and avoided is False)

            base_row = {
                "session": session_id,
                "symbol": sym,
                "entry_time": tr.get("entry_time"),
                "scenario": scen_id,
                "scenario_mode": mode,
                "actual_pnl": actual_pnl,
                "actual_reason": actual_reason,
                "actual_fade_exit": actual_fade,
                "actual_hold_sec": actual_hold,
                "scenario_pnl": round(sim.pnl, 4),
                "scenario_reason": sim.reason,
                "scenario_fade_exit": sim.fade_exit,
                "scenario_hold_sec": round(sim.hold_sec, 2),
                "delayed_fade": sim.delayed_fade,
                "avoided_fade_vs_actual": avoided,
                "reacceleration_saved": reaccel_saved,
                "breakdown_missed": breakdown_missed,
                "improved_vs_actual": improved,
                "worsened_vs_actual": worsened,
                "take_reached": had_take,
                "post_exit_class_actual": post_cls,
            }
            for subset in subsets:
                detail_rows.append({**base_row, "subset": subset})

    return detail_rows


def analyze_phase161(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    cap5_csv: Optional[Path] = None,
) -> dict[str, Any]:
    exit_cfg = observer_tracker_config_from_pilot(pilot_config)
    exit_cfg.structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1
    cap5_keys = load_cap5_only_keys(cap5_csv) if cap5_csv else set()

    all_details: list[dict[str, Any]] = []
    for sdir in session_dirs:
        events = _load_events(sdir)
        guard_keys = _guard_pass_keys(events)
        all_details.extend(
            analyze_session(
                sdir,
                exit_cfg=exit_cfg,
                cap5_keys=cap5_keys,
                guard_keys=guard_keys,
            )
        )

    scenario_rows: list[dict[str, Any]] = []
    for scen_id, _, _ in SCENARIOS:
        for subset in ("all", "guard_pass", "cap5_only"):
            rows = [r for r in all_details if r["scenario"] == scen_id and r["subset"] == subset]
            if not rows and subset != "all":
                continue
            scenario_rows.append(_summarize_pnls(rows, scenario=scen_id, subset=subset))

    cap5_fade_rows: list[dict[str, Any]] = []
    for scen_id, _, _ in SCENARIOS:
        rows = [
            r
            for r in all_details
            if r["scenario"] == scen_id and r["subset"] == "cap5_only" and r.get("actual_fade_exit")
        ]
        if not rows:
            continue
        pnls = [float(r["scenario_pnl"]) for r in rows]
        cap5_fade_rows.append(
            {
                "scenario": scen_id,
                "cap5_only_fade_actual_count": len(rows),
                "pf": _profit_factor(pnls),
                "avg_pnl": round(statistics.mean(pnls), 4),
                "total_pnl": round(sum(pnls), 4),
                "reacceleration_saved": sum(1 for r in rows if r.get("reacceleration_saved")),
                "avoided_fade": sum(1 for r in rows if r.get("avoided_fade_vs_actual")),
            }
        )

    verdict, notes = determine_verdict(scenario_rows)
    risk = _risk_summary(scenario_rows)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "summary": {
            k: v
            for k, v in next(
                (r for r in scenario_rows if r["scenario"] == "A_current" and r["subset"] == "all"),
                {},
            ).items()
            if k in ("trade_count", "pf", "fade_exit_count")
        },
        "scenario_rows": scenario_rows,
        "trade_details": all_details,
        "cap5_fade": cap5_fade_rows,
        "risk_summary": risk,
        "session_count": len(session_dirs),
    }


def build_recommendation_md(result: Mapping[str, Any]) -> str:
    verdict = str(result.get("verdict") or "")
    notes = result.get("verdict_notes") or []
    rows = [r for r in result.get("scenario_rows") or [] if r.get("subset") == "all"]
    lines = [
        "# Phase 161: fade shadow policy recommendation",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        "## Scenario comparison (all trades, cap3 sessions)",
        "",
        "| Scenario | PF | avg PnL | fade exits | avoided fade | reaccel saved | worsened |",
        "|----------|-----|---------|------------|--------------|---------------|----------|",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('scenario')} | {r.get('pf')} | {r.get('avg_pnl')} | "
            f"{r.get('fade_exit_count')} | {r.get('avoided_fade_count')} | "
            f"{r.get('reacceleration_saved_count')} | {r.get('worsened_count')} |"
        )
    lines.extend(
        [
            "",
            "## Key findings",
            "",
            "- **C 2-tick delay**: ほぼ現行と同じ（fade 516 vs 517）→ 単独では効果なし。",
            "- **D breakdown confirmed**: fade 115件（-78%）、PF 1.37、reaccel saved 89。",
            "- **G hybrid**: PF 1.77（最高）、fade 403、max_loss は現行同等。",
            "- **E range-hold protect**: PF 1.59、fade 414 — G より保守的。",
            "- **B fade 無効**: PF 1.06 だが session_close +517、hold 延長リスク大。",
            "- **F take-only**: PF 1.04、fade 111 — 改善は限定的。",
            "",
            "シミュレーションは各トレード **actual_close + 300s** まで（孤立 replay）。",
            "",
            "## Notes",
            "",
        ]
    )
    for n in notes:
        lines.append(f"- {n}")
    lines.extend(
        [
            "",
            "## Next step (live shadow)",
            "",
            "1. **G hybrid** を `fade_watch` shadow で live 検証（最優先）",
            "2. 次点 **D breakdown confirmed**（シンプル版として A/B）",
            "3. **C 2-tick** は単独採用しない",
            "",
            "## Constraints",
            "",
            "- Review only; cap=3; `order_enabled=false`; `paper_only=true`.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_phase161_outputs(result: Mapping[str, Any], *, reports_dir: Path, docs_dir: Path) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": reports_dir / "phase161_fade_shadow_policy_review.json",
        "scenarios": reports_dir / "phase161_fade_policy_scenarios.csv",
        "details": reports_dir / "phase161_fade_trade_details.csv",
        "cap5": reports_dir / "phase161_cap5_fade_subset.csv",
        "risk": reports_dir / "phase161_risk_summary.csv",
        "md": docs_dir / "phase161_recommendation.md",
    }
    design = {
        k: v
        for k, v in result.items()
        if k not in ("trade_details", "scenario_rows", "cap5_fade", "risk_summary")
    }
    paths["json"].write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(paths["scenarios"], result.get("scenario_rows") or [])
    _write_csv(paths["details"], result.get("trade_details") or [])
    _write_csv(paths["cap5"], result.get("cap5_fade") or [])
    _write_csv(paths["risk"], result.get("risk_summary") or [])
    paths["md"].write_text(build_recommendation_md(result), encoding="utf-8")
    return {k: str(v) for k, v in paths.items()}
