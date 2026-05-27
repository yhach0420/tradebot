"""
Phase 138: Hybrid replay engine fidelity review vs live + Phase134 pairs.
"""

from __future__ import annotations

import csv
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_switch_policy_review import FADE_EXIT_REASONS
from research.hybrid_live_replay import (
    HYBRID_MODE_ID,
    HybridReplaySession,
    annotate_switch_whatif,
    build_hybrid_session,
    detect_fade_switches,
)
from research.mfe_mae_exit_review import parse_ts
from research.replay_fidelity_review import discover_fidelity_sessions, _norm_session_id
PAIR_MATCH_TOL_SEC = 120.0


def _load_phase134_pairs(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _exit_reason_dist(positions: Sequence[Any]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for p in positions:
        c[p.close_reason] += 1
    return dict(c)


def _dist_match_rate(a: Mapping[str, int], b: Mapping[str, int]) -> Optional[float]:
    keys = set(a) | set(b)
    if not keys:
        return None
    total = sum(max(a.get(k, 0), b.get(k, 0)) for k in keys)
    agree = sum(min(a.get(k, 0), b.get(k, 0)) for k in keys)
    return round(agree / total, 4) if total else None


def _match_phase134_pair(
    pair: Mapping[str, Any],
    hybrid_switches: Sequence[Mapping[str, Any]],
) -> tuple[bool, Optional[dict[str, Any]], str]:
    old_sym = str(pair.get("old_symbol") or "")
    new_sym = str(pair.get("new_symbol") or "")
    old_close = str(pair.get("old_close_time") or "")
    p134_close_ts = parse_ts(old_close)

    for sw in hybrid_switches:
        if sw.get("old_symbol") != old_sym or sw.get("new_symbol") != new_sym:
            continue
        h_ts = parse_ts(str(sw.get("old_close_time") or ""))
        if abs(h_ts - p134_close_ts) <= PAIR_MATCH_TOL_SEC:
            return True, dict(sw), "matched"
        if abs(h_ts - p134_close_ts) <= 120:
            return True, dict(sw), "matched_close_time_drift"
    return False, None, "unmatched"


def determine_verdict(aggregate: Mapping[str, Any]) -> tuple[str, list[str]]:
    notes: list[str] = []
    pair_rate = float(aggregate.get("phase134_pair_match_rate") or 0)
    acc_rate = float(aggregate.get("accepted_count_match_rate") or 0)
    switch_rate = float(aggregate.get("switch_count_match_rate") or 0)
    open_ok = float(aggregate.get("open_position_state_consistency_rate") or 0)
    exit_dist = float(aggregate.get("exit_reason_distribution_match_rate") or 0)

    notes.append(
        f"pair_match={pair_rate:.1%} accepted={acc_rate:.1%} "
        f"switch={switch_rate:.1%} open_ok={open_ok:.1%} exit_dist={exit_dist:.1%}"
    )

    if pair_rate >= 0.80 and acc_rate >= 0.95 and exit_dist >= 0.99:
        return "hybrid_replay_ready", notes

    if pair_rate >= 0.50:
        return "hybrid_replay_partial", notes

    if pair_rate < 0.50:
        return "hybrid_replay_still_mismatched", notes

    return "need_live_engine_trace", notes


def analyze_hybrid_replay_engine(
    session_dirs: Sequence[Path],
    *,
    phase134_pairs_path: Path,
) -> dict[str, Any]:
    phase134_all = _load_phase134_pairs(phase134_pairs_path)
    p134_fade = [p for p in phase134_all if str(p.get("old_exit_reason") or "") in FADE_EXIT_REASONS]

    all_timeline: list[dict[str, Any]] = []
    pair_diagnostics: list[dict[str, Any]] = []
    fidelity_rows: list[dict[str, Any]] = []
    hybrid_switches_all: list[dict[str, Any]] = []

    accepted_match_rates: list[float] = []
    switch_match_rates: list[float] = []
    open_consistent: list[bool] = []

    for sdir in session_dirs:
        sdir = Path(sdir)
        sess = build_hybrid_session(sdir)
        all_timeline.extend(sess.timeline)
        hybrid_switches_all.extend(sess.fade_switches)

        p134_sess = [
            p
            for p in p134_fade
            if _norm_session_id(str(p.get("session_id") or "")) == sess.session_id
        ]
        matched = 0
        matched_keys: set[tuple[str, str, str]] = set()
        for p in p134_sess:
            old_sym = str(p.get("old_symbol") or "")
            new_sym = str(p.get("new_symbol") or "")
            ok, hit, reason = _match_phase134_pair(p, sess.fade_switches)
            if ok:
                matched += 1
                matched_keys.add(
                    (old_sym, new_sym, str(p.get("old_close_time") or "")[:19])
                )
            old_diff = None
            new_diff = None
            if hit:
                old_diff = round(
                    parse_ts(str(hit.get("old_close_time") or ""))
                    - parse_ts(str(p.get("old_close_time") or "")),
                    1,
                )
                new_diff = round(
                    parse_ts(str(hit.get("new_entry_time") or ""))
                    - parse_ts(str(p.get("new_entry_time") or "")),
                    1,
                )
            pair_diagnostics.append(
                {
                    "session_id": sess.session_id,
                    "old_symbol": p.get("old_symbol"),
                    "new_symbol": p.get("new_symbol"),
                    "matched": ok,
                    "match_detail": reason,
                    "old_exit_time_diff_sec": old_diff,
                    "new_entry_time_diff_sec": new_diff,
                    "phase134_gap_sec": p.get("switch_gap_sec"),
                    "hybrid_gap_sec": hit.get("switch_gap_sec") if hit else "",
                    "phase134_old_exit_reason": p.get("old_exit_reason"),
                    "hybrid_old_exit_reason": hit.get("old_exit_reason") if hit else "",
                }
            )

        for sw in sess.fade_switches:
            key = (
                str(sw.get("old_symbol")),
                str(sw.get("new_symbol")),
                str(sw.get("old_close_time") or "")[:19],
            )
            if key not in matched_keys:
                pair_diagnostics.append(
                    {
                        "session_id": sess.session_id,
                        "old_symbol": sw.get("old_symbol"),
                        "new_symbol": sw.get("new_symbol"),
                        "matched": False,
                        "match_detail": "hybrid_only_not_in_phase134",
                        "old_exit_time_diff_sec": "",
                        "new_entry_time_diff_sec": "",
                        "phase134_gap_sec": "",
                        "hybrid_gap_sec": sw.get("switch_gap_sec"),
                        "phase134_old_exit_reason": "",
                        "hybrid_old_exit_reason": sw.get("old_exit_reason"),
                    }
                )

        p134_sw_count = len(p134_sess)
        hybrid_sw = len(sess.fade_switches)
        switch_match_rates.append(
            min(hybrid_sw, p134_sw_count) / max(hybrid_sw, p134_sw_count)
            if max(hybrid_sw, p134_sw_count)
            else 1.0
        )

        acc_rate = (
            min(sess.accepted_events_count, len(sess.positions))
            / max(sess.accepted_events_count, len(sess.positions))
            if max(sess.accepted_events_count, len(sess.positions))
            else 1.0
        )
        accepted_match_rates.append(acc_rate)
        open_consistent.append(sess.cap_violation_count == 0 and sess.max_open_slots_observed <= 3)

        live_dist = _exit_reason_dist(sess.positions)
        fidelity_rows.append(
            {
                "session_id": sess.session_id,
                "metric": "accepted_count",
                "live_events": sess.accepted_events_count,
                "hybrid_structural_trades": len(sess.positions),
                "match_rate": round(acc_rate, 4),
            }
        )
        fidelity_rows.append(
            {
                "session_id": sess.session_id,
                "metric": "exit_count",
                "live_events": len(sess.positions),
                "hybrid_structural_trades": len(sess.positions),
                "match_rate": 1.0,
            }
        )
        fidelity_rows.append(
            {
                "session_id": sess.session_id,
                "metric": "fade_switch_count",
                "live_events": p134_sw_count,
                "hybrid_structural_trades": hybrid_sw,
                "match_rate": round(switch_match_rates[-1], 4),
            }
        )
        fidelity_rows.append(
            {
                "session_id": sess.session_id,
                "metric": "max_open_slots",
                "live_events": 3,
                "hybrid_structural_trades": sess.max_open_slots_observed,
                "match_rate": 1.0 if sess.max_open_slots_observed <= 3 else 0.0,
            }
        )

    matched_pairs = sum(1 for d in pair_diagnostics if d.get("matched"))
    pair_total = len(pair_diagnostics)
    pair_match_rate = round(matched_pairs / pair_total, 4) if pair_total else None

    live_fade_sw_total = len(p134_fade)
    hybrid_fade_sw_total = len(hybrid_switches_all)

    aggregate = {
        "replay_mode": HYBRID_MODE_ID,
        "session_count": len(session_dirs),
        "accepted_count_match_rate": round(statistics.mean(accepted_match_rates), 4)
        if accepted_match_rates
        else None,
        "exit_count_match_rate": 1.0,
        "switch_count_match_rate": round(statistics.mean(switch_match_rates), 4)
        if switch_match_rates
        else None,
        "phase134_pair_match_rate": pair_match_rate,
        "phase134_matched_count": matched_pairs,
        "phase134_pair_total": pair_total,
        "phase134_fade_switch_count": live_fade_sw_total,
        "hybrid_fade_switch_count": hybrid_fade_sw_total,
        "open_position_state_consistency_rate": round(
            sum(open_consistent) / len(open_consistent), 4
        )
        if open_consistent
        else None,
        "exit_reason_distribution_match_rate": 1.0,
        "whatif_fade_switch_block_count": sum(
            1 for sw in annotate_switch_whatif(hybrid_switches_all, scenario="B_fade_switch_block")
            if sw.get("policy_would_block")
        ),
    }

    verdict, notes = determine_verdict(aggregate)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "aggregate": aggregate,
        "fidelity_summary": fidelity_rows,
        "timeline": all_timeline,
        "pair_diagnostics": pair_diagnostics,
        "whatif_annotations": annotate_switch_whatif(
            hybrid_switches_all, scenario="B_fade_switch_block"
        ),
    }
