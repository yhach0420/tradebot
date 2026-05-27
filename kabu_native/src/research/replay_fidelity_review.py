"""
Phase 137: Live vs replay fidelity diagnosis (infrastructure / review only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.cap3_entry_replay import SCENARIO_CURRENT, simulate_cap3_entry_replay
from research.cap3_entry_replay_review import (
    _extract_fade_switches,
    _norm_session_id,
)
from research.exposure_gate import ExposureGate, ExposureGateConfig
from research.fade_switch_policy_review import FADE_EXIT_REASONS
from research.mfe_mae_exit_review import load_structural_trades, parse_ts
from research.small_paper_performance_review import _load_events, _load_json
from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
from research.structural_observer_review import (
    _session_end_time,
    replay_combined_structural_exit,
)
from research.switch_old_vs_new_review import MAX_PAIR_SEC, extract_switch_pairs

FADE_SWITCH_REASONS = FADE_EXIT_REASONS
ENTRY_MATCH_TOL_SEC = 30.0
EXIT_MATCH_TOL_SEC = 120.0


def discover_fidelity_sessions(
    small_paper_root: Path,
    *,
    max_sessions: int = 12,
) -> list[Path]:
    found: list[tuple[float, Path]] = []
    if not small_paper_root.is_dir():
        return []
    for trades_path in small_paper_root.glob("**/structural_trades.csv"):
        parent = trades_path.parent
        if (parent / "small_paper_events.csv").is_file() or (
            parent / "small_paper_events.jsonl"
        ).is_file():
            found.append((trades_path.stat().st_mtime, parent))
    found.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    out: list[Path] = []
    for _, p in found:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= max_sessions:
            break
    return out


def _session_id_from_dir(session_dir: Path) -> str:
    if session_dir.parent.parent:
        return _norm_session_id(str(session_dir.relative_to(session_dir.parent.parent)))
    return _norm_session_id(session_dir.name)


def _open_count_at(trades: Sequence[Mapping[str, Any]], ts: float) -> int:
    n = 0
    for t in trades:
        ent = parse_ts(str(t.get("entry_time") or ""))
        ex = parse_ts(str(t.get("close_time") or ""))
        if ent <= ts < ex:
            n += 1
    return n


def _symbols_open_at(trades: Sequence[Mapping[str, Any]], ts: float) -> set[str]:
    out: set[str] = set()
    for t in trades:
        ent = parse_ts(str(t.get("entry_time") or ""))
        ex = parse_ts(str(t.get("close_time") or ""))
        if ent <= ts < ex:
            out.add(str(t.get("symbol") or ""))
    return out


def _has_candidate_near(
    events: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    ts: float,
    window_sec: float = 30.0,
) -> bool:
    for e in events:
        if str(e.get("event_type") or "") != "candidate":
            continue
        if str(e.get("symbol") or "") != symbol:
            continue
        ets = parse_ts(str(e.get("entry_time") or ""))
        if abs(ets - ts) <= window_sec:
            return True
    return False


def _has_accepted_near(
    events: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    ts: float,
    window_sec: float = 30.0,
) -> bool:
    for e in events:
        if str(e.get("event_type") or "") != "accepted":
            continue
        if str(e.get("symbol") or "") != symbol:
            continue
        ets = parse_ts(str(e.get("entry_time") or ""))
        if abs(ets - ts) <= window_sec:
            return True
    return False


def _count_event_types(events: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for e in events:
        c[str(e.get("event_type") or "unknown")] += 1
    return dict(c)


def _exit_reason_dist(trades: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for t in trades:
        c[str(t.get("close_reason") or "")] += 1
    return dict(c)


def _fade_switch_count_live(trades: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for p in extract_switch_pairs_from_trades(trades)
        if str(p.get("old_exit_reason") or "") in FADE_SWITCH_REASONS
    )


def extract_switch_pairs_from_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    session_id: str = "",
) -> list[dict[str, Any]]:
    """Thin wrapper: structural_trades rows as switch pair source."""
    rows = list(trades)
    for t in rows:
        if session_id and not t.get("session_id"):
            t = {**t, "session_id": session_id}
    from research.switch_old_vs_new_review import SWITCH_EXIT_REASONS

    out: list[dict[str, Any]] = []
    for old in rows:
        reason = str(old.get("close_reason") or "")
        if reason not in SWITCH_EXIT_REASONS:
            continue
        old_sym = str(old.get("symbol") or "")
        old_close_ts = parse_ts(str(old.get("close_time") or ""))
        if not old_sym or old_close_ts <= 0:
            continue
        best = None
        best_gap = 1e18
        for new in rows:
            ns = str(new.get("symbol") or "")
            if ns == old_sym:
                continue
            ent = parse_ts(str(new.get("entry_time") or ""))
            gap = ent - old_close_ts
            if 0 <= gap <= MAX_PAIR_SEC and gap < best_gap:
                best = new
                best_gap = gap
        if best:
            out.append(
                {
                    "session_id": session_id,
                    "old_symbol": old_sym,
                    "new_symbol": str(best.get("symbol") or ""),
                    "old_exit_reason": reason,
                    "old_close_time": old.get("close_time"),
                    "new_entry_time": best.get("entry_time"),
                    "switch_gap_sec": round(best_gap, 1),
                }
            )
    return out


def live_session_metrics(session_dir: Path) -> dict[str, Any]:
    session_dir = Path(session_dir)
    sid = _session_id_from_dir(session_dir)
    trades = load_structural_trades(session_dir / "structural_trades.csv")
    events = _load_events(session_dir)
    summary = _load_json(session_dir / "small_paper_summary.json")
    struct_events_path = session_dir / "structural_events.csv"
    has_structural_events = struct_events_path.is_file()

    accepted_events = [e for e in events if str(e.get("event_type") or "") == "accepted"]
    candidate_events = [e for e in events if str(e.get("event_type") or "") == "candidate"]

    exit_reasons = _exit_reason_dist(trades)
    fade_exits = sum(exit_reasons.get(r, 0) for r in FADE_SWITCH_REASONS)

    return {
        "session_id": sid,
        "has_structural_events_csv": has_structural_events,
        "live_accepted_count": len(accepted_events),
        "live_candidate_count": len(candidate_events),
        "live_structural_trade_count": len(trades),
        "live_exit_count": len(trades),
        "live_exit_reason_distribution": exit_reasons,
        "live_overlap_replaced_count": exit_reasons.get("overlap_replaced_review", 0),
        "live_fade_exit_count": fade_exits,
        "live_switch_count_all": len(extract_switch_pairs_from_trades(trades, session_id=sid)),
        "live_fade_switch_count": _fade_switch_count_live(trades),
        "live_max_concurrent_reject_count": int(
            (summary.get("reject_reason_counts") or {}).get("max_concurrent", 0)
        ),
        "live_summary_accepted_count": int(summary.get("accepted_count") or 0),
        "event_type_counts": _count_event_types(events),
        "structural_events_row_count": (
            sum(1 for _ in open(struct_events_path, encoding="utf-8"))
            - 1
            if has_structural_events
            else 0
        ),
    }


def structural_replay_metrics(
    session_dir: Path,
    *,
    pilot_config: Any,
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sid = _session_id_from_dir(session_dir)
    interval = float(getattr(pilot_config, "poll_interval_sec", None) or 5.0)
    session_end = _session_end_time(events)
    trades, log = replay_combined_structural_exit(
        events,
        pilot_config=pilot_config,
        poll_interval_sec=interval,
        session_end=session_end,
        structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    )
    rows = [
        {
            "symbol": t.symbol,
            "entry_time": t.entry_time,
            "close_time": t.close_time,
            "close_reason": t.close_reason,
            "realized_pnl_pct": t.realized_pnl_pct,
        }
        for t in trades
    ]
    exit_reasons = Counter(t.close_reason for t in trades)
    return {
        "session_id": sid,
        "structural_replay_trade_count": len(trades),
        "structural_replay_exit_reason_distribution": dict(exit_reasons),
        "structural_replay_overlap_count": exit_reasons.get("overlap_replaced_review", 0),
        "structural_replay_fade_exit_count": sum(
            exit_reasons.get(r, 0) for r in FADE_SWITCH_REASONS
        ),
        "structural_replay_fade_switch_count": _fade_switch_count_live(rows),
        "structural_replay_event_log_count": len(log),
        "trades": rows,
    }


def cap3_replay_metrics(
    session_dir: Path,
    *,
    pilot_config: Any,
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sid = _session_id_from_dir(session_dir)
    gate_cfg = ExposureGateConfig(
        profile=str(pilot_config.profile),
        min_continuation_quality=float(pilot_config.min_continuation_quality),
        max_concurrent_positions=int(pilot_config.max_concurrent_positions),
        reject_below_quality=bool(pilot_config.reject_below_quality),
        min_above_median_quality=float(getattr(pilot_config, "min_above_median_quality", 0.42)),
    )
    gate = ExposureGate(gate_cfg, allowed_windows=pilot_config.allowed_windows())
    from small_paper.discord_notifier import observer_tracker_config_from_pilot

    exit_cfg = observer_tracker_config_from_pilot(pilot_config)
    exit_cfg.structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1
    session_end = _session_end_time(events)
    res = simulate_cap3_entry_replay(
        events,
        session_id=sid,
        scenario=SCENARIO_CURRENT,
        gate=gate,
        exit_cfg=exit_cfg,
        session_end=session_end,
        session_end_ts=parse_ts(session_end),
    )
    summ = {
        "session_id": sid,
        "cap3_accepted_count": len(res.accepted),
        "cap3_rejected_max_concurrent_count": len(res.rejects),
        "cap3_fade_switch_count": res.switch_count,
        "cap3_closed_trade_count": len(res.closed_positions),
        "result": res,
    }
    return summ


def compare_live_vs_structural_replay(
    live_trades: Sequence[Mapping[str, Any]],
    replay_trades: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
) -> dict[str, Any]:
    live_by_key = {
        (str(t.get("symbol") or ""), str(t.get("entry_time") or "")): t for t in live_trades
    }
    replay_by_key = {
        (str(t.get("symbol") or ""), str(t.get("entry_time") or "")): t
        for t in replay_trades
    }
    mismatches: list[dict[str, Any]] = []
    matched = 0
    for key, lt in live_by_key.items():
        rt = replay_by_key.get(key)
        if not rt:
            mismatches.append(
                {
                    "session_id": session_id,
                    "mismatch_type": "missing_replay_trade",
                    "symbol": key[0],
                    "entry_time": key[1],
                    "live_close_reason": lt.get("close_reason"),
                }
            )
            continue
        matched += 1
        l_close = parse_ts(str(lt.get("close_time") or ""))
        r_close = parse_ts(str(rt.get("close_time") or ""))
        close_diff = abs(l_close - r_close)
        if str(lt.get("close_reason") or "") != str(rt.get("close_reason") or ""):
            mismatches.append(
                {
                    "session_id": session_id,
                    "mismatch_type": "structural_exit_policy_mismatch",
                    "symbol": key[0],
                    "entry_time": key[1],
                    "live_close_reason": lt.get("close_reason"),
                    "replay_close_reason": rt.get("close_reason"),
                    "exit_time_diff_sec": round(close_diff, 1),
                }
            )
        elif close_diff > EXIT_MATCH_TOL_SEC:
            mismatches.append(
                {
                    "session_id": session_id,
                    "mismatch_type": "exit_timing_mismatch",
                    "symbol": key[0],
                    "entry_time": key[1],
                    "live_close_time": lt.get("close_time"),
                    "replay_close_time": rt.get("close_time"),
                    "exit_time_diff_sec": round(close_diff, 1),
                }
            )

    for key in replay_by_key:
        if key not in live_by_key:
            mismatches.append(
                {
                    "session_id": session_id,
                    "mismatch_type": "duplicate_replay_trade",
                    "symbol": key[0],
                    "entry_time": key[1],
                    "replay_close_reason": replay_by_key[key].get("close_reason"),
                }
            )

    live_accepted_keys = set(live_by_key.keys())
    replay_keys = set(replay_by_key.keys())
    return {
        "session_id": session_id,
        "live_trade_count": len(live_trades),
        "replay_trade_count": len(replay_trades),
        "matched_trade_count": matched,
        "trade_match_rate": round(matched / len(live_trades), 4) if live_trades else None,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "replay_only_count": len(replay_keys - live_accepted_keys),
        "live_only_count": len(live_accepted_keys - replay_keys),
    }


def diagnose_phase134_pair(
    pair: Mapping[str, Any],
    *,
    session_id: str,
    live_trades: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    cap3_result: Any,
    sim_switches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    old_sym = str(pair.get("old_symbol") or "")
    new_sym = str(pair.get("new_symbol") or "")
    old_close = str(pair.get("old_close_time") or "")
    new_entry = str(pair.get("new_entry_time") or "")
    old_close_ts = parse_ts(old_close)
    new_entry_ts = parse_ts(new_entry)

    hit = None
    for s in sim_switches:
        if s.get("old_symbol") == old_sym and s.get("new_symbol") == new_sym:
            if abs(parse_ts(str(s.get("old_close_time") or "")) - old_close_ts) <= EXIT_MATCH_TOL_SEC:
                hit = s
                break

    live_old = [
        t
        for t in live_trades
        if str(t.get("symbol") or "") == old_sym
        and abs(parse_ts(str(t.get("close_time") or "")) - old_close_ts) <= EXIT_MATCH_TOL_SEC
    ]
    live_new = [
        t
        for t in live_trades
        if str(t.get("symbol") or "") == new_sym
        and abs(parse_ts(str(t.get("entry_time") or "")) - new_entry_ts) <= ENTRY_MATCH_TOL_SEC
    ]

    cap_at_new = _open_count_at(live_trades, new_entry_ts)
    old_open_at_new = old_sym in _symbols_open_at(live_trades, new_entry_ts)
    new_cand = _has_candidate_near(events, symbol=new_sym, ts=new_entry_ts)
    new_acc = _has_accepted_near(events, symbol=new_sym, ts=new_entry_ts)

    old_exit_diff = None
    new_entry_diff = None
    if hit:
        old_exit_diff = round(
            parse_ts(str(hit.get("old_close_time") or "")) - old_close_ts, 1
        )
        new_entry_diff = round(
            parse_ts(str(hit.get("new_entry_time") or "")) - new_entry_ts, 1
        )

    matched = hit is not None
    reason = ""
    if matched:
        reason = "matched"
    else:
        sim_fade_old = [
            p
            for p in cap3_result.closed_positions
            if p.symbol == old_sym
            and p.close_reason in FADE_SWITCH_REASONS
            and abs(p.close_ts - old_close_ts) <= EXIT_MATCH_TOL_SEC
        ]
        if not live_old:
            reason = "position_state_mismatch"
        elif not sim_fade_old:
            reason = "structural_exit_policy_mismatch"
        elif not new_acc:
            reason = "accepted_timing_mismatch"
        elif cap_at_new >= 3 and not live_new:
            reason = "cap_gate_mismatch"
        elif not _has_candidate_near(events, symbol=old_sym, ts=old_close_ts, window_sec=60):
            reason = "event_density_insufficiency"
        elif not new_cand:
            reason = "missing_candidate_event"
        else:
            sim_new_acc = any(
                str(a.get("symbol")) == new_sym
                and abs(parse_ts(str(a.get("entry_time"))) - new_entry_ts) <= ENTRY_MATCH_TOL_SEC
                for a in cap3_result.accepted
            )
            if not sim_new_acc:
                reason = "position_state_mismatch"
            else:
                reason = "exit_timing_mismatch"

    return {
        "session_id": session_id,
        "old_symbol": old_sym,
        "new_symbol": new_sym,
        "matched": matched,
        "unmatched_reason": reason if not matched else "",
        "phase134_old_exit_reason": pair.get("old_exit_reason"),
        "old_exit_time_diff_sec": old_exit_diff,
        "new_entry_time_diff_sec": new_entry_diff,
        "cap_state_at_new_entry": cap_at_new,
        "old_position_present_at_new_entry": old_open_at_new,
        "new_candidate_present": new_cand,
        "new_accepted_present": new_acc,
        "live_old_trade_found": bool(live_old),
        "live_new_trade_found": bool(live_new),
        "phase134_switch_gap_sec": pair.get("switch_gap_sec"),
    }


def determine_verdict(
    aggregate: Mapping[str, Any],
    *,
    pair_match_rate: Optional[float],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    trade_match = float(aggregate.get("avg_trade_match_rate") or 0)
    accepted_ratio = aggregate.get("accepted_count_ratio")
    switch_ratio = aggregate.get("fade_switch_count_ratio")
    notes.append(
        f"trade_match={trade_match:.1%} accepted_ratio={accepted_ratio} "
        f"switch_ratio={switch_ratio} pair_match={pair_match_rate}"
    )

    if pair_match_rate is not None and pair_match_rate >= 0.75 and trade_match >= 0.7:
        return "replay_fidelity_ready", notes

    if trade_match >= 0.45 or (pair_match_rate or 0) >= 0.4:
        return "replay_mismatch_fixable", notes + [
            "hybrid live-accepted + structural-exit replay recommended"
        ]

    dom = str(aggregate.get("dominant_unmatched_reason") or "")
    if dom == "event_density_insufficiency":
        return "event_density_insufficient", notes

    if float(aggregate.get("structural_events_coverage_rate") or 0) < 0.3:
        return "need_live_engine_trace", notes + ["structural_events.csv sparse or missing"]

    return "replay_mismatch_fixable", notes


def build_fix_plan_md(aggregate: Mapping[str, Any], modes: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Phase137 Replay Engine Fix Plan",
        "",
        "## Problem",
        "",
        "Phase134 counterfactual (+93.48 on fade switches) does not reproduce under",
        "structural-only replay (+0.04) or cap3 entry replay (+0.07).",
        f"Phase134 pair match rate in cap3 replay: **{aggregate.get('phase134_pair_match_rate')}**.",
        "",
        "## Observed fidelity gaps",
        "",
        f"- Avg live vs structural-replay trade match rate: **{aggregate.get('avg_trade_match_rate')}**",
        f"- Accepted count ratio (cap3/live): **{aggregate.get('accepted_count_ratio')}**",
        f"- Fade switch count ratio (cap3/live fade switches): **{aggregate.get('fade_switch_count_ratio')}**",
        f"- Dominant unmatched pair reason: **{aggregate.get('dominant_unmatched_reason')}**",
        "",
        "## Recommended replay modes",
        "",
    ]
    for m in modes:
        lines.append(f"### {m.get('mode_id')}")
        lines.append(f"{m.get('description')}")
        lines.append(f"- Use when: {m.get('use_when')}")
        lines.append("")
    lines.extend(
        [
            "## Implementation order",
            "",
            "1. **hybrid_live_accepted_structural_exit** — default for switch/fade policy reviews",
            "2. **structural_trades_exit_timeline** — validate exit reasons vs live CSV",
            "3. **candidate_reconstruct** — only for missing-event diagnosis",
            "4. Cap state machine synced from live open-slot timeline before entry what-if",
            "",
            "## Not in scope",
            "",
            "- Entry / exit / quality / vol_liq logic changes",
            "- Production pilot YAML changes",
        ]
    )
    return "\n".join(lines) + "\n"


REPLAY_MODES = [
    {
        "mode_id": "live_accepted_entry_timeline",
        "description": "Use `accepted` events from small_paper_events as the only entry triggers; apply ExposureGate + cap=3 at those timestamps.",
        "use_when": "Entry timing fidelity is the primary gap vs Phase134.",
    },
    {
        "mode_id": "structural_trades_exit_timeline",
        "description": "Use live `structural_trades.csv` close_time / close_reason as ground-truth exits; replay only post-exit switch path.",
        "use_when": "Structural exit replay diverges on fade/overlap timing.",
    },
    {
        "mode_id": "candidate_reconstruct",
        "description": "Rebuild entry/exit candidates from full candidate stream (high cost; gate per tick).",
        "use_when": "Diagnosing missing_candidate_event only.",
    },
    {
        "mode_id": "hybrid_live_accepted_structural_exit",
        "description": "Entries from live `accepted`; exits from structural_trades when matched else combined_structural_exit_v1 tick replay.",
        "use_when": "Default for Phase138+ switch/cooldown validation under cap=3.",
    },
]


def analyze_replay_fidelity(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    phase134_pairs_path: Path,
) -> dict[str, Any]:
    phase134_pairs = []
    if phase134_pairs_path.is_file():
        with phase134_pairs_path.open(encoding="utf-8", newline="") as f:
            phase134_pairs = list(csv.DictReader(f))

    per_session: list[dict[str, Any]] = []
    all_mismatches: list[dict[str, Any]] = []
    pair_diagnostics: list[dict[str, Any]] = []
    trade_match_rates: list[float] = []
    live_accepted_total = 0
    cap3_accepted_total = 0
    live_fade_sw_total = 0
    cap3_fade_sw_total = 0
    structural_events_sessions = 0

    for sdir in session_dirs:
        sdir = Path(sdir)
        events = _load_events(sdir)
        if not events:
            continue
        live = live_session_metrics(sdir)
        live_trades = load_structural_trades(sdir / "structural_trades.csv")
        if live.get("has_structural_events_csv"):
            structural_events_sessions += 1

        sreplay = structural_replay_metrics(sdir, pilot_config=pilot_config, events=events)
        cmp = compare_live_vs_structural_replay(
            live_trades, sreplay.get("trades") or [], session_id=live["session_id"]
        )
        all_mismatches.extend(cmp.get("mismatches") or [])
        if cmp.get("trade_match_rate") is not None:
            trade_match_rates.append(float(cmp["trade_match_rate"]))

        cap3 = cap3_replay_metrics(sdir, pilot_config=pilot_config, events=events)
        res = cap3["result"]
        sim_sw = _extract_fade_switches(res)

        live_accepted_total += int(live.get("live_accepted_count") or 0)
        cap3_accepted_total += int(cap3.get("cap3_accepted_count") or 0)
        live_fade_sw_total += int(live.get("live_fade_switch_count") or 0)
        cap3_fade_sw_total += int(cap3.get("cap3_fade_switch_count") or 0)

        p134_sess = [
            p
            for p in phase134_pairs
            if _norm_session_id(str(p.get("session_id") or "")) == live["session_id"]
        ]
        for p in p134_sess:
            pair_diagnostics.append(
                diagnose_phase134_pair(
                    p,
                    session_id=live["session_id"],
                    live_trades=live_trades,
                    events=events,
                    cap3_result=res,
                    sim_switches=sim_sw,
                )
            )

        per_session.append(
            {
                **live,
                "structural_replay_trade_count": sreplay.get("structural_replay_trade_count"),
                "structural_replay_fade_switch_count": sreplay.get(
                    "structural_replay_fade_switch_count"
                ),
                "trade_match_rate": cmp.get("trade_match_rate"),
                "trade_mismatch_count": cmp.get("mismatch_count"),
                "cap3_accepted_count": cap3.get("cap3_accepted_count"),
                "cap3_fade_switch_count": cap3.get("cap3_fade_switch_count"),
                "cap3_rejected_max_concurrent": cap3.get("cap3_rejected_max_concurrent_count"),
            }
        )

    matched_pairs = sum(1 for d in pair_diagnostics if d.get("matched"))
    pair_total = len(pair_diagnostics)
    pair_match_rate = round(matched_pairs / pair_total, 4) if pair_total else None

    unmatch_reasons = Counter(
        str(d.get("unmatched_reason") or "unknown")
        for d in pair_diagnostics
        if not d.get("matched")
    )

    aggregate = {
        "session_count": len(per_session),
        "avg_trade_match_rate": round(statistics.mean(trade_match_rates), 4)
        if trade_match_rates
        else None,
        "accepted_count_ratio": round(cap3_accepted_total / live_accepted_total, 4)
        if live_accepted_total
        else None,
        "fade_switch_count_ratio": round(cap3_fade_sw_total / live_fade_sw_total, 4)
        if live_fade_sw_total
        else None,
        "phase134_pair_match_rate": pair_match_rate,
        "phase134_matched_count": matched_pairs,
        "phase134_pair_total": pair_total,
        "dominant_unmatched_reason": unmatch_reasons.most_common(1)[0][0]
        if unmatch_reasons
        else "",
        "unmatched_reason_distribution": dict(unmatch_reasons),
        "structural_events_coverage_rate": round(
            structural_events_sessions / len(per_session), 4
        )
        if per_session
        else 0,
    }

    verdict, notes = determine_verdict(aggregate, pair_match_rate=pair_match_rate)
    fix_plan = build_fix_plan_md(aggregate, REPLAY_MODES)

    scenario_summary = []
    for row in per_session:
        scenario_summary.append(
            {
                "session_id": row["session_id"],
                "metric": "accepted_count",
                "live": row.get("live_accepted_count"),
                "structural_replay_trades": row.get("structural_replay_trade_count"),
                "cap3_replay": row.get("cap3_accepted_count"),
            }
        )
        scenario_summary.append(
            {
                "session_id": row["session_id"],
                "metric": "fade_switch_count",
                "live": row.get("live_fade_switch_count"),
                "structural_replay": row.get("structural_replay_fade_switch_count"),
                "cap3_replay": row.get("cap3_fade_switch_count"),
            }
        )

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "aggregate": aggregate,
        "sessions": per_session,
        "mismatch_events": all_mismatches,
        "pair_diagnostics": pair_diagnostics,
        "scenario_summary": scenario_summary,
        "fix_plan_md": fix_plan,
        "replay_modes": REPLAY_MODES,
    }
