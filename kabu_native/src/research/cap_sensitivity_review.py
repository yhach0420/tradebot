"""
Phase 132: max_concurrent cap sensitivity review (3/5/7/10) — counterfactual only.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.exposure_cap_whatif_review import (
    PHASE53_MIN_QUALITY,
    _simulate_cap_scenario,
)
from research.mfe_mae_exit_review import discover_sessions, load_structural_trades
from research.runtime_pilot_policy_review import (
    _build_price_index,
    _candidates_from_events,
)
from research.small_paper_performance_review import _load_events, _load_json, _profit_factor

PHASE132_CAPS = (3, 5, 7, 10)
MIN_SESSIONS = 4


def _structural_overlap_count(session_dir: Path) -> int:
    trades = load_structural_trades(session_dir / "structural_trades.csv")
    return sum(1 for t in trades if str(t.get("close_reason") or "") == "overlap_replaced_review")


def _scenario_metrics(
    result: Any,
    *,
    session_id: str,
    total_hq_candidates: int,
) -> dict[str, Any]:
    m = dict(result.metrics)
    pnls = [float(r.get("realized_pnl_pct") or 0) for r in result.accepted_rows]
    total_pnl = round(sum(pnls), 4) if pnls else 0.0
    accepted = int(m.get("accepted_count") or 0)
    rejected_mc = int(m.get("max_concurrent_reject_count") or 0)
    opp_denom = accepted + rejected_mc
    return {
        "session_id": session_id,
        "max_concurrent": result.max_concurrent,
        "accepted_count": accepted,
        "rejected_max_concurrent_count": rejected_mc,
        "overlap_replaced_proxy_count": int(m.get("same_symbol_overlap_accept_count") or 0),
        "overlap_replaced_proxy_rate_pct": m.get("same_symbol_overlap_rate_pct"),
        "total_pnl_proxy": total_pnl,
        "avg_pnl": m.get("avg_pnl_pct"),
        "pf_proxy": m.get("profit_factor"),
        "win_rate": m.get("win_rate"),
        "opportunity_capture_rate": round(accepted / opp_denom, 4) if opp_denom else None,
        "max_concurrent_reject_would_be_pnl_sum": m.get("max_concurrent_reject_would_be_pnl_sum"),
        "concurrent_saturation_rate_pct": m.get("concurrent_saturation_rate_pct"),
        "top_symbol_concentration_pct": m.get("top_symbol_concentration_pct"),
        "evaluations": m.get("evaluations"),
        "hq_candidate_pool": total_hq_candidates,
    }


def _aggregate_cap_rows(per_session_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_cap: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in per_session_rows:
        by_cap[int(row["max_concurrent"])].append(row)

    agg: list[dict[str, Any]] = []
    cap3 = by_cap.get(3, [])
    base_pnl = sum(float(r.get("total_pnl_proxy") or 0) for r in cap3)
    base_overlap = sum(int(r.get("overlap_replaced_proxy_count") or 0) for r in cap3)

    for cap in PHASE132_CAPS:
        rows = by_cap.get(cap, [])
        if not rows:
            continue
        pnls_sum = sum(float(r.get("total_pnl_proxy") or 0) for r in rows)
        accepted = sum(int(r.get("accepted_count") or 0) for r in rows)
        rejected = sum(int(r.get("rejected_max_concurrent_count") or 0) for r in rows)
        overlap = sum(int(r.get("overlap_replaced_proxy_count") or 0) for r in rows)
        would_pnl = sum(float(r.get("max_concurrent_reject_would_be_pnl_sum") or 0) for r in rows)
        pf_vals = [float(r["pf_proxy"]) for r in rows if r.get("pf_proxy") is not None]
        avg_vals = [float(r["avg_pnl"]) for r in rows if r.get("avg_pnl") is not None]
        win_vals = [float(r["win_rate"]) for r in rows if r.get("win_rate") is not None]
        opp_vals = [float(r["opportunity_capture_rate"]) for r in rows if r.get("opportunity_capture_rate") is not None]
        opp_denom = accepted + rejected

        agg.append(
            {
                "max_concurrent": cap,
                "session_count": len(rows),
                "accepted_count": accepted,
                "rejected_max_concurrent_count": rejected,
                "overlap_replaced_proxy_count": overlap,
                "structural_overlap_delta_vs_cap3": overlap - base_overlap if cap != 3 else 0,
                "total_pnl_proxy": round(pnls_sum, 4),
                "total_pnl_delta_vs_cap3": round(pnls_sum - base_pnl, 4) if cap != 3 else 0.0,
                "avg_pnl": round(statistics.mean(avg_vals), 4) if avg_vals else None,
                "pf_proxy": round(statistics.mean(pf_vals), 4) if pf_vals else None,
                "win_rate": round(statistics.mean(win_vals), 4) if win_vals else None,
                "opportunity_capture_rate": round(accepted / opp_denom, 4) if opp_denom else None,
                "avg_opportunity_capture_rate_per_session": round(statistics.mean(opp_vals), 4) if opp_vals else None,
                "rejected_would_be_pnl_sum": round(would_pnl, 4),
                "avg_rejected_would_be_pnl": round(would_pnl / rejected, 4) if rejected else None,
            }
        )
    return agg


def _newly_accepted_keys(
    cap3_rows: Sequence[Mapping[str, Any]],
    capn_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    keys3 = {(r.get("symbol"), r.get("entry_time")) for r in cap3_rows}
    out: list[dict[str, Any]] = []
    for r in capn_rows:
        key = (r.get("symbol"), r.get("entry_time"))
        if key not in keys3:
            out.append(
                {
                    "symbol": r.get("symbol"),
                    "entry_time": r.get("entry_time"),
                    "continuation_quality_score": r.get("continuation_quality_score"),
                    "realized_pnl_pct": r.get("realized_pnl_pct"),
                    "quality_tier": r.get("quality_tier"),
                    "session_bucket": r.get("session_bucket"),
                }
            )
    return out


def determine_verdict(
    aggregate: Sequence[Mapping[str, Any]],
    *,
    session_count: int,
    structural_overlap_total: int,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if session_count < MIN_SESSIONS:
        return "insufficient_data", notes + [f"sessions={session_count}"]

    by_cap = {int(r["max_concurrent"]): r for r in aggregate}
    cap3 = by_cap.get(3)
    if not cap3:
        return "insufficient_data", notes + ["cap3 missing"]

    base_pnl = float(cap3.get("total_pnl_proxy") or 0)
    base_pf = float(cap3.get("pf_proxy") or 0)
    base_overlap = int(cap3.get("overlap_replaced_proxy_count") or 0)
    notes.append(
        f"cap3 pnl={base_pnl:.4f} pf={base_pf:.4f} overlap_proxy={base_overlap} "
        f"structural_overlap={structural_overlap_total}"
    )

    candidates = [by_cap[c] for c in (5, 7, 10) if c in by_cap]
    if not candidates:
        return "insufficient_data", notes

    best = max(candidates, key=lambda r: float(r.get("total_pnl_proxy") or -1e9))
    best_cap = int(best["max_concurrent"])
    best_pnl = float(best.get("total_pnl_proxy") or 0)
    best_pf = float(best.get("pf_proxy") or 0)
    best_overlap = int(best.get("overlap_replaced_proxy_count") or 0)
    pnl_delta = float(best.get("total_pnl_delta_vs_cap3") or 0)
    overlap_delta = best_overlap - base_overlap

    notes.append(
        f"best_higher_cap={best_cap} pnl_delta={pnl_delta:.4f} pf={best_pf:.4f} overlap_delta={overlap_delta}"
    )

    if pnl_delta > 0.5 and best_pf >= base_pf and overlap_delta <= base_overlap * 0.1 + 5:
        return "cap_increase_promising", notes

    if pnl_delta > 0 and best_pf < base_pf * 0.95:
        return "need_position_sizing_model", notes + ["pnl up but PF degrades"]

    if pnl_delta <= 0 and base_pf >= best_pf:
        return "cap3_still_best", notes

    if pnl_delta > 0 and overlap_delta > 10:
        return "need_position_sizing_model", notes + ["overlap increases materially with cap"]

    if pnl_delta > 0:
        return "cap_increase_promising", notes + ["marginal pnl gain"]

    return "cap3_still_best", notes


def analyze_cap_sensitivity(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    min_quality: float = PHASE53_MIN_QUALITY,
    caps: Sequence[int] = PHASE132_CAPS,
) -> dict[str, Any]:
    per_session: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    structural_overlap_total = 0
    newly_accepted_by_cap: dict[int, list[dict[str, Any]]] = {c: [] for c in caps if c != 3}
    accepted_by_session_cap: dict[tuple[str, int], list[dict[str, Any]]] = {}

    for sdir in session_dirs:
        sdir = Path(sdir)
        events = _load_events(sdir)
        if not events:
            continue
        summary = _load_json(sdir / "small_paper_summary.json")
        session_id = (
            str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        )
        structural_overlap_total += _structural_overlap_count(sdir)

        candidates = _candidates_from_events(events)
        hq_candidates = [
            c for c in candidates if float(c.get("continuation_quality_score") or 0) >= min_quality
        ]
        price_index = _build_price_index(events)
        profile = str(summary.get("profile") or getattr(pilot_config, "profile", ""))
        allowed_windows = pilot_config.allowed_windows() if pilot_config else None

        session_results: dict[int, Any] = {}
        for cap in caps:
            result = _simulate_cap_scenario(
                candidates,
                min_quality=min_quality,
                max_concurrent=cap,
                profile=profile,
                price_index=price_index,
                allowed_windows=allowed_windows,
            )
            session_results[cap] = result
            accepted_by_session_cap[(session_id, cap)] = result.accepted_rows
            per_session.append(
                _scenario_metrics(
                    result,
                    session_id=session_id,
                    total_hq_candidates=len(hq_candidates),
                )
            )

        for row in session_results[3].mc_reject_rows:
            rejected_rows.append({**row, "session_id": session_id})

        for cap in caps:
            if cap == 3:
                continue
            newly_accepted_by_cap[cap].extend(
                _newly_accepted_keys(
                    accepted_by_session_cap[(session_id, 3)],
                    accepted_by_session_cap[(session_id, cap)],
                )
            )

    aggregate = _aggregate_cap_rows(per_session)

    newly_accepted_summary: list[dict[str, Any]] = []
    for cap, rows in newly_accepted_by_cap.items():
        if not rows:
            continue
        pnls = [float(r.get("realized_pnl_pct") or 0) for r in rows]
        newly_accepted_summary.append(
            {
                "max_concurrent": cap,
                "newly_accepted_count_vs_cap3": len(rows),
                "newly_accepted_total_pnl_proxy": round(sum(pnls), 4),
                "newly_accepted_avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
                "newly_accepted_win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
                "newly_accepted_pf_proxy": round(_profit_factor(pnls), 4)
                if pnls and _profit_factor(pnls) not in (None, float("inf"))
                else _profit_factor(pnls),
            }
        )

    verdict, notes = determine_verdict(
        aggregate,
        session_count=len({r["session_id"] for r in per_session}),
        structural_overlap_total=structural_overlap_total,
    )

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "session_count": len({r["session_id"] for r in per_session}),
        "caps": list(caps),
        "aggregate": aggregate,
        "per_session": per_session,
        "rejected_due_to_cap": rejected_rows,
        "newly_accepted_vs_cap3": newly_accepted_summary,
        "structural_overlap_replaced_total_cap3_sessions": structural_overlap_total,
        "methodology_note": (
            "Counterfactual ExposureGate resimulation with virtual-hold PnL proxy. "
            "overlap_replaced_proxy_count = same-symbol accept while symbol already open. "
            "structural_overlap_replaced_total from actual structural_trades.csv at observed cap=3."
        ),
    }
