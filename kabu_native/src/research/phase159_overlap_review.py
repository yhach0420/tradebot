"""
Phase 159: Quantitative review of overlap_replaced_review validity (shadow / review only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.cap3_entry_replay import (
    SCENARIO_CURRENT,
    SimPosition,
    _close_position,
    _cross_symbol_cooldown_blocks,
    _profit_factor,
    _sync_gate_slots,
)
from research.exposure_gate import ExposureGate, ExposureGateConfig
from research.fade_switch_policy_review import FADE_EXIT_REASONS
from research.mfe_mae_exit_review import (
    best_pnl_in_window,
    build_price_timeline_from_events_csv,
    discover_sessions,
    load_structural_trades,
    parse_ts,
    pnl_pct,
    session_end_ts_from_trades,
)
from research.phase156_intraday_refresh_cap5_review import _filter_price_risk_candidates
from research.runtime_pilot_policy_review import (
    _build_price_index,
    _candidates_from_events,
    _trade_from_candidate,
)
from research.small_paper_performance_review import _load_events
from research.structural_exit_policies import (
    combined_exit_signal_on_latest_tick,
    tick_from_candidate,
)
from research.structural_observer_review import _session_end_time

OVERLAP_CLOSE = "overlap_replaced_review"
OVERLAP_ALIASES = frozenset(
    {OVERLAP_CLOSE, "overlap_replaced", "overlap_exit", "overlap_replaced_review"}
)
NEUTRAL_DELTA_PCT = 0.03
MIN_PAIRS = 20

REASON_BUCKETS = (
    "quality_decay_exit",
    "momentum_fade_exit",
    "price_momentum_fade_exit",
    "mfe_giveback_exit",
    "favorable_fade_exit",
    "vwap_break_exit",
    "stop_hit",
    "session_end",
    "other",
)

POLICY_SCENARIOS = (
    ("A_current", "current"),
    ("B_no_overlap", "no_overlap"),
    ("C_overlap_loss_only", "loss_only"),
    ("D_overlap_quality_decay_proxy", "quality_decay_proxy"),
    ("E_overlap_mfe_not_reached", "mfe_not_reached"),
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


def _is_overlap_reason(reason: str) -> bool:
    r = str(reason or "").strip()
    return r == OVERLAP_CLOSE or "overlap" in r.lower()


def _classify_exit_reason(reason: str) -> str:
    r = str(reason or "").strip()
    if r in REASON_BUCKETS:
        return r
    if "momentum_fade" in r:
        return "momentum_fade_exit"
    if "quality_decay" in r:
        return "quality_decay_exit"
    if "mfe_giveback" in r:
        return "mfe_giveback_exit"
    if "favorable_fade" in r:
        return "favorable_fade_exit"
    if "price_momentum" in r:
        return "price_momentum_fade_exit"
    return "other"


def _session_id_from_dir(session_dir: Path) -> str:
    if session_dir.parent.name and session_dir.parent.name.isdigit():
        return f"{session_dir.parent.name}/{session_dir.name}"
    return session_dir.name


def _chain_final_reason(trades: Sequence[Mapping[str, Any]], start_idx: int, symbol: str) -> str:
    for j in range(start_idx, len(trades)):
        t = trades[j]
        if str(t.get("symbol") or "") != symbol:
            break
        cr = str(t.get("close_reason") or "")
        if not _is_overlap_reason(cr):
            return cr
    return OVERLAP_CLOSE


def _hold_pnl_at_time(
    timeline: Sequence[tuple[float, float]],
    *,
    entry_price: float,
    entry_ts: float,
    until_ts: float,
) -> float:
    px: Optional[float] = None
    for ts, p in timeline:
        if ts <= until_ts:
            px = p
        else:
            break
    if px is None:
        for ts, p in reversed(timeline):
            if ts >= entry_ts:
                px = p
                break
    if px is None:
        return 0.0
    return pnl_pct(entry_price, px)


def extract_overlap_pairs(
    session_dir: Path,
    trades: Sequence[Mapping[str, Any]],
    timeline: Mapping[str, list[tuple[float, float]]],
    session_end: float,
) -> list[dict[str, Any]]:
    session_id = _session_id_from_dir(session_dir)
    ordered = sorted(trades, key=lambda t: parse_ts(str(t.get("entry_time") or "")))
    pairs: list[dict[str, Any]] = []

    for i, old in enumerate(ordered):
        if not _is_overlap_reason(str(old.get("close_reason") or "")):
            continue
        if i + 1 >= len(ordered):
            continue
        new = ordered[i + 1]
        if str(new.get("symbol") or "") != str(old.get("symbol") or ""):
            continue

        sym = str(old.get("symbol") or "")
        old_entry = str(old.get("entry_time") or "")
        old_exit = str(old.get("close_time") or "")
        new_entry = str(new.get("entry_time") or "")
        new_exit = str(new.get("close_time") or "")
        old_pnl = float(old.get("realized_pnl_pct") or 0)
        new_pnl = float(new.get("realized_pnl_pct") or 0)
        actual_pair = round(old_pnl + new_pnl, 4)

        entry_px = float(old.get("entry_price") or 0)
        old_exit_ts = parse_ts(old_exit)
        new_exit_ts = parse_ts(new_exit)
        sym_tl = timeline.get(sym, [])
        hold_old = _hold_pnl_at_time(
            sym_tl, entry_price=entry_px, entry_ts=parse_ts(old_entry), until_ts=new_exit_ts
        )
        delta = round(actual_pair - hold_old, 4)
        if delta > NEUTRAL_DELTA_PCT:
            verdict = "switch_better"
        elif delta < -NEUTRAL_DELTA_PCT:
            verdict = "hold_better"
        else:
            verdict = "neutral"

        mfe_after = best_pnl_in_window(
            sym_tl,
            base_ts=old_exit_ts,
            entry_price=entry_px,
            window_sec=180.0,
            session_end_ts=session_end,
        )
        new_high_after = best_pnl_in_window(
            sym_tl,
            base_ts=old_exit_ts,
            entry_price=entry_px,
            window_sec=600.0,
            session_end_ts=session_end,
        )

        chain_reason = _chain_final_reason(ordered, i + 1, sym)
        overlap_reason_class = _classify_exit_reason(chain_reason)

        pairs.append(
            {
                "session": session_id,
                "old_symbol": sym,
                "new_symbol": str(new.get("symbol") or ""),
                "old_entry_time": old_entry,
                "old_exit_time": old_exit,
                "old_exit_pnl": old_pnl,
                "new_entry_time": new_entry,
                "new_exit_time": new_exit,
                "new_exit_pnl": new_pnl,
                "holding_seconds": round(parse_ts(new_exit) - parse_ts(old_entry), 1),
                "close_reason": OVERLAP_CLOSE,
                "new_close_reason": str(new.get("close_reason") or ""),
                "chain_final_exit_reason": chain_reason,
                "overlap_reason_class": overlap_reason_class,
                "mfe_pct": float(old.get("mfe_pct") or 0),
                "mae_pct": float(old.get("mae_pct") or 0),
                "actual_pair_pnl": actual_pair,
                "hold_old_pnl": hold_old,
                "delta_switch_vs_hold": delta,
                "switch_verdict": verdict,
                "mfe_after_exit_180s": mfe_after,
                "new_high_after_exit_600s": new_high_after,
                "lost_profit_vs_hold": round(hold_old - actual_pair, 4),
            }
        )
    return pairs


def summarize_pairs(pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not pairs:
        return {"pair_count": 0}
    verdicts = Counter(str(p.get("switch_verdict") or "") for p in pairs)
    deltas = [float(p.get("delta_switch_vs_hold") or 0) for p in pairs]
    actual = [float(p.get("actual_pair_pnl") or 0) for p in pairs]
    hold = [float(p.get("hold_old_pnl") or 0) for p in pairs]
    n = len(pairs)
    return {
        "pair_count": n,
        "switch_better_count": verdicts.get("switch_better", 0),
        "hold_better_count": verdicts.get("hold_better", 0),
        "neutral_count": verdicts.get("neutral", 0),
        "switch_better_rate_pct": round(100.0 * verdicts.get("switch_better", 0) / n, 2),
        "hold_better_rate_pct": round(100.0 * verdicts.get("hold_better", 0) / n, 2),
        "neutral_rate_pct": round(100.0 * verdicts.get("neutral", 0) / n, 2),
        "avg_delta": round(statistics.mean(deltas), 4),
        "median_delta": round(statistics.median(deltas), 4),
        "actual_pair_pf": _profit_factor(actual),
        "hold_old_pf": _profit_factor(hold),
        "pf_delta_hold_minus_actual": round(
            (_profit_factor(hold) or 0) - (_profit_factor(actual) or 0), 4
        )
        if _profit_factor(hold) is not None and _profit_factor(actual) is not None
        else None,
        "total_actual_pair_pnl": round(sum(actual), 4),
        "total_hold_old_pnl": round(sum(hold), 4),
    }


def reason_breakdown(pairs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for p in pairs:
        by[str(p.get("overlap_reason_class") or "other")].append(p)
    rows: list[dict[str, Any]] = []
    for reason, grp in sorted(by.items()):
        s = summarize_pairs(grp)
        rows.append(
            {
                "overlap_reason_class": reason,
                "pair_count": s["pair_count"],
                "switch_better_count": s.get("switch_better_count", 0),
                "hold_better_count": s.get("hold_better_count", 0),
                "switch_better_rate_pct": s.get("switch_better_rate_pct"),
                "avg_delta": s.get("avg_delta"),
                "median_delta": s.get("median_delta"),
            }
        )
    return rows


def load_cap5_only_keys(path: Path) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    if not path.is_file():
        return keys
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("bucket") or "") != "cap5_only":
                continue
            keys.add((str(row.get("symbol") or ""), str(row.get("entry_time") or "")))
    return keys


def cap5_overlap_analysis(
    pairs: Sequence[Mapping[str, Any]],
    cap5_keys: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    if not cap5_keys:
        return [{"note": "no_cap5_only_keys_loaded"}]
    related = [
        p
        for p in pairs
        if (str(p.get("new_symbol")), str(p.get("new_entry_time"))) in cap5_keys
        or (str(p.get("old_symbol")), str(p.get("old_entry_time"))) in cap5_keys
    ]
    s = summarize_pairs(related)
    total_cap5_trades = len(cap5_keys)
    return [
        {
            "cap5_only_trade_count": total_cap5_trades,
            "overlap_pair_count": s.get("pair_count", 0),
            "overlap_rate_vs_cap5_trades_pct": round(
                100.0 * int(s.get("pair_count", 0)) / max(1, total_cap5_trades), 2
            ),
            "switch_better_rate_pct": s.get("switch_better_rate_pct"),
            "hold_better_rate_pct": s.get("hold_better_rate_pct"),
            "avg_delta": s.get("avg_delta"),
            "total_actual_pair_pnl": s.get("total_actual_pair_pnl"),
            "total_hold_old_pnl": s.get("total_hold_old_pnl"),
        }
    ]


def worst_overlap_rows(pairs: Sequence[Mapping[str, Any]], *, top_n: int = 50) -> list[dict[str, Any]]:
    ranked = sorted(
        pairs,
        key=lambda p: float(p.get("lost_profit_vs_hold") or 0),
        reverse=True,
    )[:top_n]
    out: list[dict[str, Any]] = []
    for p in ranked:
        out.append(
            {
                "session": p.get("session"),
                "symbol": p.get("old_symbol"),
                "exit_reason": p.get("chain_final_exit_reason"),
                "actual_pnl": p.get("actual_pair_pnl"),
                "hold_pnl": p.get("hold_old_pnl"),
                "lost_profit": p.get("lost_profit_vs_hold"),
                "mfe_after_exit": p.get("mfe_after_exit_180s"),
                "new_high_after_exit": p.get("new_high_after_exit_600s"),
                "switch_verdict": p.get("switch_verdict"),
                "delta": p.get("delta_switch_vs_hold"),
            }
        )
    return out


def _allow_overlap_replace(
    policy: str,
    *,
    old_pos: SimPosition,
    price: float,
    row: Mapping[str, Any],
) -> bool:
    if policy == "no_overlap":
        return False
    if policy == "current":
        return True
    unreal = pnl_pct(old_pos.entry_price, price)
    mfe = max((pnl_pct(old_pos.entry_price, float(t.get("price") or old_pos.entry_price)) for t in old_pos.rich_ticks), default=unreal)
    if policy == "loss_only":
        return unreal < -NEUTRAL_DELTA_PCT
    if policy == "quality_decay_proxy":
        q = float(row.get("continuation_quality_score") or 0)
        peak_q = max(
            float(t.get("quality") or q) for t in old_pos.rich_ticks
        ) if old_pos.rich_ticks else q
        return unreal < 0 or q < peak_q - 0.05
    if policy == "mfe_not_reached":
        return mfe < 0.10
    return True


def simulate_overlap_policy(
    candidates: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
    max_concurrent: int,
    profile: str,
    exit_cfg: Any,
    session_end: str,
    overlap_policy: str,
    min_quality: float = 0.70,
) -> list[SimPosition]:
    from research.cap3_entry_replay import Cap3ReplayResult

    gate = ExposureGate(
        ExposureGateConfig(
            profile=profile,
            min_continuation_quality=min_quality,
            max_concurrent_positions=max_concurrent,
            reject_below_quality=True,
            min_above_median_quality=0.42,
        )
    )
    result = Cap3ReplayResult(scenario=SCENARIO_CURRENT, session_id=session_id)
    ordered = sorted(candidates, key=lambda e: int(e.get("message_index") or 0))
    open_positions: list[SimPosition] = []
    post_fade: dict[str, Any] = {}
    session_end_ts = parse_ts(session_end)

    def try_close(pos: SimPosition, row: Mapping[str, Any], ts: float) -> None:
        tick = tick_from_candidate(
            dict(row), pos.entry_price, float(row.get("continuation_quality_score") or 0)
        )
        tick["ts_epoch"] = ts
        if "quality" not in tick:
            tick["quality"] = float(row.get("continuation_quality_score") or 0)
        pos.rich_ticks.append(tick)
        sig = combined_exit_signal_on_latest_tick(pos.rich_ticks, pos.entry_price, exit_cfg)
        if not sig:
            return
        _, reason, close_px = sig
        if pos in open_positions:
            open_positions.remove(pos)
        _close_position(
            pos,
            close_time=str(row.get("entry_time") or ""),
            close_ts=ts,
            close_price=close_px,
            reason=reason,
            result=result,
        )

    for row in ordered:
        sym = str(row.get("symbol") or "")
        ent_raw = str(row.get("entry_time") or "")
        ts = parse_ts(ent_raw)
        price = float(row.get("current_price") or 0)
        if not sym:
            continue
        for pos in list(open_positions):
            if pos.symbol == sym and pos.is_open:
                try_close(pos, row, ts)
        if price <= 0:
            continue
        trade = _trade_from_candidate(row)
        _sync_gate_slots(gate, open_positions, horizon_ts=ts)
        decision = gate.evaluate_entry(trade)
        if not decision.accept:
            continue
        ob = {p.symbol: p for p in open_positions if p.is_open}
        if sym in ob:
            old = ob[sym]
            if _allow_overlap_replace(overlap_policy, old_pos=old, price=price, row=row):
                open_positions.remove(old)
                _close_position(
                    old,
                    close_time=ent_raw,
                    close_ts=ts,
                    close_price=price,
                    reason=OVERLAP_CLOSE,
                    result=result,
                )
            else:
                continue
        blocked, _, _ = _cross_symbol_cooldown_blocks(
            post_fade, new_symbol=sym, scenario=SCENARIO_CURRENT
        )
        if blocked:
            continue
        open_positions.append(
            SimPosition(symbol=sym, entry_time=ent_raw, entry_ts=ts, entry_price=price)
        )

    for pos in list(open_positions):
        if not pos.is_open:
            continue
        close_px = pos.entry_price
        if pos.rich_ticks:
            close_px = float(pos.rich_ticks[-1].get("price") or close_px)
        open_positions.remove(pos)
        _close_position(
            pos,
            close_time=session_end,
            close_ts=session_end_ts,
            close_price=close_px,
            reason="session_end",
            result=result,
        )
    return result.closed_positions


def overlap_policy_whatif(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
) -> list[dict[str, Any]]:
    from small_paper.discord_notifier import observer_tracker_config_from_pilot

    exit_cfg = observer_tracker_config_from_pilot(pilot_config)
    profile = str(getattr(pilot_config, "profile", ""))
    rows: list[dict[str, Any]] = []

    for sdir in session_dirs:
        events = _load_events(sdir)
        if not events:
            continue
        candidates, _, _ = _filter_price_risk_candidates(events)
        if not candidates:
            continue
        session_end = _session_end_time(events)
        sid = _session_id_from_dir(sdir)
        for policy_id, policy_key in POLICY_SCENARIOS:
            closed = simulate_overlap_policy(
                candidates,
                session_id=sid,
                max_concurrent=3,
                profile=profile,
                exit_cfg=exit_cfg,
                session_end=session_end,
                overlap_policy=policy_key,
            )
            pnls = [p.realized_pnl_pct for p in closed]
            reasons = Counter(p.close_reason for p in closed)
            overlap_n = sum(
                1 for p in closed if _is_overlap_reason(p.close_reason)
            )
            rows.append(
                {
                    "scenario": policy_id,
                    "policy_key": policy_key,
                    "session_id": sid,
                    "trade_count": len(closed),
                    "total_pnl": round(sum(pnls), 4) if pnls else 0.0,
                    "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
                    "pf": _profit_factor(pnls),
                    "max_loss_pct": round(min(pnls), 4) if pnls else None,
                    "overlap_count": overlap_n,
                }
            )

    agg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        agg[str(r["scenario"])].append(r)
    summary_rows: list[dict[str, Any]] = []
    for scenario, grp in agg.items():
        pnls = [float(x["total_pnl"]) for x in grp]
        trades = sum(int(x["trade_count"]) for x in grp)
        overlap = sum(int(x["overlap_count"]) for x in grp)
        pf_vals = [float(x["pf"]) for x in grp if x.get("pf") is not None]
        avg_vals = [float(x["avg_pnl"]) for x in grp if x.get("avg_pnl") is not None]
        losses = [float(x["max_loss_pct"]) for x in grp if x.get("max_loss_pct") is not None]
        summary_rows.append(
            {
                "scenario": scenario,
                "session_count": len(grp),
                "trade_count": trades,
                "total_pnl": round(sum(pnls), 4),
                "avg_pnl_mean": round(statistics.mean(avg_vals), 4) if avg_vals else None,
                "pf_mean": round(statistics.mean(pf_vals), 4) if pf_vals else None,
                "max_loss_worst": round(min(losses), 4) if losses else None,
                "overlap_count": overlap,
            }
        )
    return summary_rows


def determine_verdict(summary: Mapping[str, Any]) -> tuple[str, list[str]]:
    notes: list[str] = []
    n = int(summary.get("pair_count") or 0)
    if n < MIN_PAIRS:
        return "insufficient_data", [f"overlap_pairs={n} < {MIN_PAIRS}"]
    hold_rate = float(summary.get("hold_better_rate_pct") or 0)
    switch_rate = float(summary.get("switch_better_rate_pct") or 0)
    notes.append(f"hold_better={hold_rate}% switch_better={switch_rate}%")
    notes.append(f"avg_delta={summary.get('avg_delta')} median={summary.get('median_delta')}")
    if hold_rate > 60:
        return "overlap_harmful", notes + ["holding old leg beats switch >60% of pairs"]
    if switch_rate > 60:
        return "overlap_helpful", notes + ["switch beats hold >60% of pairs"]
    return "overlap_mixed", notes + ["hold/switch split 40-60%"]


def build_recommendation_md(
    *,
    verdict: str,
    notes: Sequence[str],
    summary: Mapping[str, Any],
) -> str:
    lines = [
        "# Phase 159: overlap_replaced_review recommendation",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        "## Question",
        "",
        "Does overlap exit destroy profit (vs holding the old leg)?",
        "",
        "## Summary",
        "",
        f"- Overlap pairs analyzed: {summary.get('pair_count')}",
        f"- Hold better: {summary.get('hold_better_rate_pct')}% ({summary.get('hold_better_count')})",
        f"- Switch better: {summary.get('switch_better_rate_pct')}% ({summary.get('switch_better_count')})",
        f"- Neutral: {summary.get('neutral_rate_pct')}%",
        f"- Avg delta (actual_pair - hold_old): {summary.get('avg_delta')}%",
        f"- Total PnL actual pairs: {summary.get('total_actual_pair_pnl')}%",
        f"- Total PnL if held old: {summary.get('total_hold_old_pnl')}%",
        "",
        "## Notes",
        "",
    ]
    for n in notes:
        lines.append(f"- {n}")
    lines.extend(
        [
            "",
            "## Constraints",
            "",
            "- Review only; no production YAML / entry / exit changes.",
            "",
            "## Interpretation",
            "",
            "- Positive `delta` → switch (actual) better than hold-old counterfactual.",
            "- Negative `delta` → overlap harmed vs simply holding.",
            "- Cap5 candidacy (Phase158) is separate from overlap switch quality.",
        ]
    )
    return "\n".join(lines) + "\n"


def analyze_phase159(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    cap5_only_csv: Optional[Path] = None,
) -> dict[str, Any]:
    all_pairs: list[dict[str, Any]] = []
    per_session_counts: list[dict[str, Any]] = []

    for sdir in session_dirs:
        trades_path = sdir / "structural_trades.csv"
        trades = load_structural_trades(trades_path)
        if not trades:
            continue
        events_csv = sdir / "small_paper_events.csv"
        syms = {str(t.get("symbol") or "") for t in trades}
        timeline = build_price_timeline_from_events_csv(events_csv, syms)
        if not any(timeline.values()):
            events = _load_events(sdir)
            idx = _build_price_index(events)
            timeline = {sym: list(v) for sym, v in idx.items()}
        session_end = session_end_ts_from_trades(trades)
        pairs = extract_overlap_pairs(sdir, trades, timeline, session_end)
        all_pairs.extend(pairs)
        per_session_counts.append(
            {
                "session": _session_id_from_dir(sdir),
                "structural_trade_count": len(trades),
                "overlap_pair_count": len(pairs),
            }
        )

    summary = summarize_pairs(all_pairs)
    verdict, notes = determine_verdict(summary)
    cap5_keys = load_cap5_only_keys(cap5_only_csv) if cap5_only_csv else set()

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "verdict_options": {
            "A": "overlap_harmful",
            "B": "overlap_mixed",
            "C": "overlap_helpful",
            "D": "insufficient_data",
        },
        "overlap_summary": summary,
        "per_session": per_session_counts,
        "overlap_events": all_pairs,
        "pair_comparison": all_pairs,
        "reason_breakdown": reason_breakdown(all_pairs),
        "cap5_overlap": cap5_overlap_analysis(all_pairs, cap5_keys),
        "worst50": worst_overlap_rows(all_pairs),
        "policy_whatif": overlap_policy_whatif(session_dirs, pilot_config=pilot_config),
        "methodology": {
            "pair_definition": "structural_trades row with overlap close + immediate same-symbol successor",
            "hold_old_pnl": "mark-to-market old entry at new leg close_time using event price timeline",
            "actual_pair_pnl": "old_exit_pnl + new_exit_pnl",
            "delta": "actual_pair_pnl - hold_old_pnl",
        },
        "constraints": {
            "review_only": True,
            "no_production_yaml": True,
            "no_entry_exit_changes": True,
        },
    }


def write_phase159_outputs(result: Mapping[str, Any], *, reports_dir: Path, docs_dir: Path) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": reports_dir / "phase159_overlap_review.json",
        "events": reports_dir / "phase159_overlap_events.csv",
        "pairs": reports_dir / "phase159_overlap_pair_comparison.csv",
        "summary": reports_dir / "phase159_overlap_summary.csv",
        "reasons": reports_dir / "phase159_overlap_reason_breakdown.csv",
        "cap5": reports_dir / "phase159_cap5_overlap_analysis.csv",
        "worst50": reports_dir / "phase159_overlap_worst50.csv",
        "whatif": reports_dir / "phase159_overlap_policy_whatif.csv",
        "md": docs_dir / "phase159_recommendation.md",
    }
    design = {k: v for k, v in result.items() if k not in ("overlap_events", "pair_comparison", "worst50")}
    paths["json"].write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(paths["events"], result.get("overlap_events") or [])
    _write_csv(paths["pairs"], result.get("pair_comparison") or [])
    _write_csv(paths["summary"], [result.get("overlap_summary") or {}])
    _write_csv(paths["reasons"], result.get("reason_breakdown") or [])
    _write_csv(paths["cap5"], result.get("cap5_overlap") or [])
    _write_csv(paths["worst50"], result.get("worst50") or [])
    _write_csv(paths["whatif"], result.get("policy_whatif") or [])
    paths["md"].write_text(
        build_recommendation_md(
            verdict=str(result.get("verdict") or ""),
            notes=result.get("verdict_notes") or [],
            summary=result.get("overlap_summary") or {},
        ),
        encoding="utf-8",
    )
    return {k: str(v) for k, v in paths.items()}
