"""
Phase 140: Deep analysis of fade_switch_priority — block vs selective allow.
Review-only; no production changes.
"""

from __future__ import annotations

import csv
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.fade_switch_policy_review import (
    PNL_EPS,
    _pnl_current,
    _pnl_keep_old,
    _priority_allow,
)
from research.hybrid_fade_switch_policy_review import (
    SCENARIO_D as SCENARIO_PRIORITY,
    analyze_hybrid_fade_switch_policies,
    load_candidate_events,
)
from research.mfe_mae_exit_review import as_float, parse_ts
from research.replay_fidelity_review import _norm_session_id

SCENARIO_C = "C_priority_current"
SCENARIO_D = "D_ultra_conservative"
SCENARIO_E = "E_selective_allow"

DETAIL_COLS = (
    "session_id",
    "old_symbol",
    "new_symbol",
    "old_exit_reason",
    "old_pnl_after_switch",
    "new_pnl_after_switch",
    "delta_new_minus_old",
    "new_quality",
    "new_favorable",
    "new_momentum",
    "old_mfe_pct",
    "old_quality_at_exit",
    "old_momentum_at_exit",
    "old_favorable_at_exit",
    "old_breakdown_before_exit",
    "old_reaccelerated_after_exit",
    "old_post_fade_breakdown",
    "old_post_fade_reacceleration",
    "new_candidate_rank",
    "new_vol_liq",
    "switch_classification",
    "current_pnl_proxy",
    "keep_old_pnl_proxy",
    "priority_allow_switch",
    "block_outcome",
    "block_delta_vs_current",
)


def enrich_old_features_from_candidates(
    pair: Mapping[str, Any],
    candidate_events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    old_sym = str(pair.get("old_symbol") or "")
    old_ts = parse_ts(str(pair.get("old_close_time") or ""))
    snap: Optional[dict[str, Any]] = None
    best_d = 1e18
    snap_window = 15
    for e in candidate_events:
        ts = parse_ts(str(e.get("event_time") or e.get("entry_time") or ""))
        d = abs(ts - old_ts)
        sym = str(e.get("symbol") or "")
        if sym == old_sym and d <= snap_window and d < best_d:
            best_d = d
            snap = dict(e)
    out: dict[str, Any] = {
        "old_quality_at_exit": as_float(pair.get("old_pnl_at_exit")),
        "old_momentum_at_exit": None,
        "old_favorable_at_exit": None,
    }
    if snap:
        out["old_quality_at_exit"] = as_float(snap.get("continuation_quality_score")) or out[
            "old_quality_at_exit"
        ]
        out["old_momentum_at_exit"] = as_float(snap.get("momentum_continuation_score"))
        out["old_favorable_at_exit"] = as_float(snap.get("favorable_continuation"))
    return out


def _post_fade_flags(
    pair: Mapping[str, Any],
    old_timeline: Sequence[tuple[float, float]],
) -> dict[str, bool]:
    from research.hybrid_fade_switch_policy_review import _cooldown_allow_phase139

    _, reason, _ = _cooldown_allow_phase139(pair, old_timeline)
    return {
        "old_post_fade_breakdown": reason == "old_breakdown_confirmed",
        "old_post_fade_reacceleration": reason == "old_reacceleration_confirmed",
    }


def _block_outcome(pair: Mapping[str, Any]) -> tuple[str, float]:
    cur = float(pair.get("current_pnl_proxy") or 0)
    keep = float(pair.get("keep_old_pnl_proxy") or 0)
    delta = round(keep - cur, 4)
    if delta > PNL_EPS:
        return "block_helped", delta
    if delta < -PNL_EPS:
        return "block_hurt", delta
    return "block_neutral", delta


def _enrich_pair_for_analysis(
    pair: Mapping[str, Any],
    *,
    candidate_events: Sequence[Mapping[str, Any]],
    old_timeline: Sequence[tuple[float, float]],
) -> dict[str, Any]:
    row = dict(pair)
    row.update(enrich_old_features_from_candidates(pair, candidate_events))
    row.update(_post_fade_flags(pair, old_timeline))
    outcome, delta = _block_outcome(row)
    row["block_outcome"] = outcome
    row["block_delta_vs_current"] = delta
    row["block_was_correct"] = outcome == "block_helped"
    row["missed_good_new"] = (
        str(row.get("switch_classification") or "") == "switch_correct"
        and outcome == "block_hurt"
    )
    row["avoided_bad_new"] = (
        str(row.get("switch_classification") or "") == "switch_wrong"
        and outcome == "block_helped"
    )
    return row


def _detail_row(pair: Mapping[str, Any]) -> dict[str, Any]:
    return {k: pair.get(k) for k in DETAIL_COLS}


SelectiveRule = tuple[str, Callable[[Mapping[str, Any]], bool], str]


def _selective_rules() -> list[SelectiveRule]:
    def q(r: Mapping[str, Any]) -> float:
        return float(r.get("new_quality") or 0)

    def oq(r: Mapping[str, Any]) -> float:
        return float(r.get("old_quality_at_exit") or r.get("old_pnl_at_exit") or 0)

    def nm(r: Mapping[str, Any]) -> float:
        return float(r.get("new_momentum") or 0)

    def om(r: Mapping[str, Any]) -> float:
        return float(r.get("old_momentum_at_exit") or 0)

    def nf(r: Mapping[str, Any]) -> float:
        return float(r.get("new_favorable") or 0)

    def of_(r: Mapping[str, Any]) -> float:
        return float(r.get("old_favorable_at_exit") or 0)

    def nr(r: Mapping[str, Any]) -> int:
        return int(r.get("new_candidate_rank") or 99)

    return [
        (
            "quality_margin_05",
            lambda r: q(r) >= oq(r) + 0.05 and nr(r) <= 5,
            "new_quality > old_quality + 0.05, rank<=5",
        ),
        (
            "momentum_margin_025",
            lambda r: nm(r) >= om(r) + 0.025 and q(r) >= 0.70,
            "new_momentum > old_momentum + 0.025",
        ),
        (
            "favorable_margin_01",
            lambda r: nf(r) >= of_(r) + 0.01 and q(r) >= 0.70,
            "new_favorable > old_favorable + 0.01",
        ),
        (
            "breakdown_no_reaccel_rank3",
            lambda r: bool(r.get("old_breakdown_before_exit"))
            and not bool(r.get("old_reaccelerated_after_exit"))
            and nr(r) <= 3
            and q(r) >= 0.72,
            "old_breakdown, no reaccel, rank<=3",
        ),
        (
            "post_fade_breakdown_strong_new",
            lambda r: bool(r.get("old_post_fade_breakdown"))
            and q(r) >= 0.72
            and nm(r) >= 0.40
            and not bool(r.get("old_range_hold_before_exit")),
            "post-fade breakdown + strong new",
        ),
        (
            "rank_top3_quality_72",
            lambda r: nr(r) <= 3 and q(r) >= 0.72 and nm(r) >= 0.38,
            "rank<=3, quality>=0.72",
        ),
        (
            "composite_strong_new",
            lambda r: q(r) >= oq(r) + 0.05
            and nm(r) >= om(r) + 0.02
            and nf(r) >= of_(r)
            and nr(r) <= 5
            and not bool(r.get("old_range_hold_before_exit")),
            "quality+momentum+favorable margins, rank<=5",
        ),
        (
            "quality_gap_and_rank",
            lambda r: _priority_allow(r, "quality_gap_and_rank"),
            "Phase139 priority rule (baseline)",
        ),
        (
            "score_margin",
            lambda r: _priority_allow(r, "score_margin"),
            "score_new > score_old + 0.12",
        ),
        (
            "strict_quality_momentum",
            lambda r: _priority_allow(r, "strict_quality_momentum"),
            "strict quality+momentum, no range/breakdown",
        ),
    ]


def _scenario_metrics(
    rows: Sequence[Mapping[str, Any]],
    allow_fn: Callable[[Mapping[str, Any]], bool],
    scenario_id: str,
) -> dict[str, Any]:
    pnls: list[float] = []
    deltas: list[float] = []
    allow_count = 0
    missed_good = avoided_bad = 0
    for r in rows:
        cur = _pnl_current(r)
        keep = _pnl_keep_old(r)
        allow = allow_fn(r)
        pnl = cur if allow else keep
        pnls.append(pnl)
        deltas.append(pnl - cur)
        if allow:
            allow_count += 1
        else:
            truth = str(r.get("switch_classification") or "")
            d = pnl - cur
            if truth == "switch_wrong" and d > PNL_EPS:
                avoided_bad += 1
            if truth == "switch_correct" and d < -PNL_EPS:
                missed_good += 1

    truths = [str(r.get("switch_classification") or "") for r in rows]
    return {
        "scenario_id": scenario_id,
        "fade_switch_count": len(rows),
        "total_pnl_proxy": round(sum(pnls), 4),
        "avg_pnl_proxy": round(statistics.mean(pnls), 4) if pnls else None,
        "delta_total_vs_A_current": round(sum(deltas), 4),
        "avg_delta": round(statistics.mean(deltas), 4) if deltas else None,
        "allow_count": allow_count,
        "block_count": len(rows) - allow_count,
        "missed_good_new": missed_good,
        "avoided_bad_new": avoided_bad,
        "correct_rate": round(sum(1 for t in truths if t == "switch_correct") / len(truths), 4)
        if truths
        else None,
        "wrong_rate": round(sum(1 for t in truths if t == "switch_wrong") / len(truths), 4)
        if truths
        else None,
    }


def _evaluate_selective_rules(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rule_id, fn, desc in _selective_rules():
        m = _scenario_metrics(rows, fn, rule_id)
        out.append(
            {
                "rule_id": rule_id,
                "description": desc,
                **m,
            }
        )
    out.sort(key=lambda x: float(x.get("delta_total_vs_A_current") or -1e9), reverse=True)
    return out


def _blocked_breakdown(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    blocked = [r for r in rows if not r.get("priority_allow_switch")]
    cls = Counter(str(r.get("switch_classification") or "") for r in blocked)
    outcomes = Counter(str(r.get("block_outcome") or "") for r in blocked)
    return {
        "blocked_count": len(blocked),
        "block_helped_count": outcomes.get("block_helped", 0),
        "block_hurt_count": outcomes.get("block_hurt", 0),
        "block_neutral_count": outcomes.get("block_neutral", 0),
        "missed_good_new": sum(1 for r in blocked if r.get("missed_good_new")),
        "avoided_bad_new": sum(1 for r in blocked if r.get("avoided_bad_new")),
        "both_bad_count": cls.get("both_bad", 0),
        "both_good_count": cls.get("both_good", 0),
        "switch_correct_count": cls.get("switch_correct", 0),
        "switch_wrong_count": cls.get("switch_wrong", 0),
        "block_correct_rate": round(outcomes.get("block_helped", 0) / len(blocked), 4)
        if blocked
        else None,
    }


def _allowed_analysis(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    allowed = [r for r in rows if r.get("priority_allow_switch")]
    sessions = {str(r.get("session_id") or "") for r in allowed}
    new_syms = {str(r.get("new_symbol") or "") for r in allowed}
    total_gain_vs_block = sum(
        float(r.get("current_pnl_proxy") or 0) - float(r.get("keep_old_pnl_proxy") or 0)
        for r in allowed
    )
    return {
        "allowed_count": len(allowed),
        "unique_sessions": len(sessions),
        "unique_new_symbols": len(new_syms),
        "sessions": sorted(sessions),
        "new_symbols": sorted(new_syms),
        "all_switch_correct": all(
            str(r.get("switch_classification") or "") == "switch_correct" for r in allowed
        ),
        "total_gain_vs_block_pnl": round(total_gain_vs_block, 4),
        "avg_gain_vs_block": round(total_gain_vs_block / len(allowed), 4) if allowed else None,
    }


def determine_verdict(
    scenarios: Sequence[Mapping[str, Any]],
    rule_candidates: Sequence[Mapping[str, Any]],
    allowed_meta: Mapping[str, Any],
) -> tuple[str, list[str]]:
    by_id = {s["scenario_id"]: s for s in scenarios}
    notes: list[str] = []

    a = by_id.get("A_current") or {}
    b = by_id.get("B_full_block") or {}
    c = by_id.get(SCENARIO_C) or {}
    d = by_id.get(SCENARIO_D) or {}
    e = by_id.get(SCENARIO_E) or {}

    b_delta = float(b.get("delta_total_vs_A_current") or 0)
    c_delta = float(c.get("delta_total_vs_A_current") or 0)
    e_delta = float(e.get("delta_total_vs_A_current") or 0)
    allow_n = int(c.get("allow_count") or 0)
    e_allow = int(e.get("allow_count") or 0)

    notes.append(
        f"B_delta={b_delta:.2f} C_delta={c_delta:.2f} E_delta={e_delta:.2f} "
        f"C_allow={allow_n} E_allow={e_allow}"
    )

    # Priority 3 allows: same session/symbol cluster
    if allow_n <= 5 and int(allowed_meta.get("unique_sessions") or 0) <= 1:
        notes.append(
            f"priority allows only {allow_n} in {allowed_meta.get('unique_sessions')} session(s)"
        )
        extra_vs_block = float(c.get("total_pnl_proxy") or 0) - float(b.get("total_pnl_proxy") or 0)
        notes.append(f"priority_extra_vs_block={extra_vs_block:.2f}")

        if extra_vs_block > 10 and allow_n <= 3:
            return "priority_rule_too_brittle", notes + [
                "3 allows cluster in one session; gain vs block not generalizable"
            ]

    best_rule = rule_candidates[0] if rule_candidates else {}
    best_rule_delta = float(best_rule.get("delta_total_vs_A_current") or 0)
    best_rule_allow = int(best_rule.get("allow_count") or 0)

    if b_delta <= 1.0 and c_delta <= 1.0:
        return "current_switch_best", notes + ["no policy beats current meaningfully"]

    # Block sufficient if within 5 pnl of best and selective adds <5 vs block
    block_total = float(b.get("total_pnl_proxy") or 0)
    best_total = max(
        float(c.get("total_pnl_proxy") or 0),
        float(e.get("total_pnl_proxy") or 0),
        block_total,
    )
    if best_total - block_total < 5.0 and b_delta > 50:
        return "fade_switch_block_sufficient", notes + [
            "full block captures most gain; selective margin too small"
        ]

    if (
        e_delta > b_delta + 3
        and e_allow >= 5
        and int(allowed_meta.get("unique_sessions") or 0) > 1
    ):
        return "selective_priority_promising", notes + ["selective allow beats block with spread"]

    if best_rule_delta > b_delta + 5 and best_rule_allow >= 5:
        return "selective_priority_promising", notes + [
            f"rule {best_rule.get('rule_id')} promising"
        ]

    if allow_n <= 5 and c_delta > b_delta + 5:
        return "priority_rule_too_brittle", notes + [
            "priority beats block but allow count too low for generalization"
        ]

    if b_delta >= c_delta - 2:
        return "fade_switch_block_sufficient", notes + ["block nearly matches priority"]

    return "selective_priority_promising", notes + ["priority margin over block warrants study"]


def analyze_fade_switch_priority(
    session_dirs: Sequence[Path],
    *,
    phase139_pairs_path: Optional[Path] = None,
    phase139_pairs_csv: Optional[Path] = None,
) -> dict[str, Any]:
    from research.mfe_mae_exit_review import build_price_timeline_from_events_csv

    pairs_raw: list[dict[str, Any]]
    base_scenarios: list[dict[str, Any]] = []

    csv_path = phase139_pairs_csv
    if csv_path and csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            pairs_raw = list(csv.DictReader(f))
        for row in pairs_raw:
            row["priority_allow_switch"] = str(row.get("priority_allow_switch") or "").lower() == "true"
            row["old_breakdown_before_exit"] = str(
                row.get("old_breakdown_before_exit") or ""
            ).lower() == "true"
            row["old_reaccelerated_after_exit"] = str(
                row.get("old_reaccelerated_after_exit") or ""
            ).lower() == "true"
            row["old_range_hold_before_exit"] = str(
                row.get("old_range_hold_before_exit") or ""
            ).lower() == "true"
    else:
        base = analyze_hybrid_fade_switch_policies(
            session_dirs,
            phase134_pairs_path=phase139_pairs_path,
        )
        pairs_raw = base["pairs"]
        base_scenarios = base.get("scenarios") or []

    session_dirs = [Path(s) for s in session_dirs]
    session_by_id: dict[str, Path] = {}
    for sdir in session_dirs:
        sid = _norm_session_id(
            str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        )
        session_by_id[sid] = Path(sdir)

    pairs_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in pairs_raw:
        pairs_by_session[_norm_session_id(str(p.get("session_id") or ""))].append(dict(p))

    enriched: list[dict[str, Any]] = []
    for sid, ps in pairs_by_session.items():
        sdir = session_by_id.get(sid)
        if not sdir:
            enriched.extend(ps)
            continue
        candidate_events = load_candidate_events(sdir)
        old_symbols = {str(p.get("old_symbol") or "") for p in ps if str(p.get("old_symbol") or "")}
        old_tl_map = build_price_timeline_from_events_csv(
            sdir / "small_paper_events.csv", old_symbols
        )
        for p in ps:
            old_sym = str(p.get("old_symbol") or "")
            enriched.append(
                _enrich_pair_for_analysis(
                    p,
                    candidate_events=candidate_events,
                    old_timeline=old_tl_map.get(old_sym, []),
                )
            )

    allowed_rows = [r for r in enriched if r.get("priority_allow_switch")]
    blocked_rows = [r for r in enriched if not r.get("priority_allow_switch")]

    rule_candidates = _evaluate_selective_rules(enriched)
    best_rule = rule_candidates[0] if rule_candidates else {}
    best_rule_id = str(best_rule.get("rule_id") or "composite_strong_new")
    best_fn: Callable[[Mapping[str, Any]], bool] = lambda r: False
    for rid, fn, _ in _selective_rules():
        if rid == best_rule_id:
            best_fn = fn
            break

    always_block = lambda r: False
    always_allow = lambda r: True
    priority_fn = lambda r: bool(r.get("priority_allow_switch"))

    scenarios = [
        _scenario_metrics(enriched, always_allow, "A_current"),
        _scenario_metrics(enriched, always_block, "B_full_block"),
        _scenario_metrics(enriched, priority_fn, SCENARIO_C),
        _scenario_metrics(enriched, always_block, SCENARIO_D),
        _scenario_metrics(enriched, best_fn, SCENARIO_E),
    ]

    allowed_meta = _allowed_analysis(enriched)
    blocked_meta = _blocked_breakdown(enriched)
    verdict, notes = determine_verdict(scenarios, rule_candidates, allowed_meta)

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "fade_switch_count": len(enriched),
        "allowed_switch_analysis": allowed_meta,
        "blocked_switch_analysis": blocked_meta,
        "allowed_switch_details": [_detail_row(r) for r in allowed_rows],
        "blocked_switch_details": [_detail_row(r) for r in blocked_rows],
        "rule_candidates": rule_candidates,
        "best_selective_rule_id": best_rule_id,
        "scenarios": scenarios,
        "phase139_reference": {
            "pair_count": len(enriched),
            "priority_allow_count": len(allowed_rows),
            "priority_total_pnl": next(
                (
                    s["total_pnl_proxy"]
                    for s in base_scenarios
                    if s.get("scenario_id") == SCENARIO_PRIORITY
                ),
                None,
            ),
            "loaded_from_csv": bool(csv_path and csv_path.is_file()),
        },
    }
