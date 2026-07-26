"""Common reclaim candidate table + nested F0–F5 filters (evaluation repair only)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from research.canonical_fcr_exact_method.opportunity import first_valid_ask
from research.canonical_fcr_exact_method.state_machine import Episode, build_episodes
from research.canonical_fcr_exact_method.loader import Tick
from research.canonical_fcr_incremental_integrity.constants import ARMS, FROZEN, PARENT, TRAIN_DAY
from research.canonical_fcr_incremental_integrity.loader import exec_parts


@dataclass
class ReclaimCandidate:
    reclaim_candidate_id: str
    episode_id: str
    impulse_id: str
    day: str
    symbol: str
    stream_key: str
    reclaim_cross_event_seq: int
    reclaim_cross_idx: int
    reclaim_cross_time: datetime
    reclaim_level: float
    reclaim_level_created_at: Optional[datetime]
    pullback_low: Optional[float]
    initial_impulse_high: Optional[float]
    common_decision_idx: int
    common_decision_time: datetime
    common_decision_event_seq: int
    # stage flags at common decision (past-only)
    trend_context_pass: bool
    pullback_pass: bool
    selling_exhausted_pass: bool
    buy_flow_pass: bool
    reclaim_cross_pass: bool
    reclaim_hold_2events_pass: bool
    liquidity_pass: bool
    quote_quality_pass: bool
    ask_qty_100_pass: bool
    # native timing diagnostics (not for matched increment)
    native_trend_time: Optional[datetime] = None
    native_pullback_time: Optional[datetime] = None
    native_exhaust_time: Optional[datetime] = None
    native_buy_time: Optional[datetime] = None
    native_reclaim_time: Optional[datetime] = None
    # execution at common anchor
    entry_execution_idx: Optional[int] = None
    entry_execution_time: Optional[datetime] = None
    entry_execution_price: Optional[float] = None
    exec_ok: bool = False
    snapshot_hash: str = ""


@dataclass
class ArmRow:
    arm_id: str
    parent_arm_id: Optional[str]
    reclaim_candidate_id: str
    episode_id: str
    parent_candidate_id: Optional[str]
    common_decision_time: datetime
    entry_execution_time: Optional[datetime]
    entry_execution_price: Optional[float]
    filter_pass: bool
    filter_fail_reason: str
    causal_fields_snapshot_hash: str
    stream_key: str
    entry_idx: int
    day: str
    symbol: str
    impulse_id: str


def _seq(t: Tick) -> int:
    return int(getattr(t, "event_seq", t.idx))


def _episode_id(day: str, symbol: str, start_idx: int, start_time: datetime, start_seq: int) -> str:
    # no entry timestamp
    return f"{day}|{symbol}|imp{start_seq}|{start_time.isoformat()}"


def _hash_flags(d: dict[str, Any]) -> str:
    blob = json.dumps(d, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def run_frozen_episodes(streams: dict[str, list[Tick]], days: list[str]) -> list[Episode]:
    out: list[Episode] = []
    p = FROZEN
    for key, ticks in streams.items():
        if key.split("|")[0] not in days:
            continue
        eps = build_episodes(
            key, ticks,
            slope_min=p["slope_min"],
            pb_frac_lo=p["pb_lo"],
            pb_frac_hi=p["pb_hi"],
            new_low_stop_sec=p["new_low_stop_sec"],
            buy_ratio=p["buy_ratio"],
            freq_accel=p["freq_accel"],
            reclaim_hold_events=p["reclaim_hold_events"],
            expiry_exh_to_buy=p["expiry_exh_to_buy"],
            expiry_buy_to_reclaim=p["expiry_buy_to_reclaim"],
            spread_max_bps=p["spread_max_bps"],
        )
        # rewrite episode_id without entry timestamp; stable from impulse start
        for ep in eps:
            st = ticks[ep.start_idx]
            ep.episode_id = _episode_id(ep.day, ep.symbol, ep.start_idx, ep.start_time, _seq(st))
            ep.impulse_id = f"{ep.day}|{ep.symbol}|imp{_seq(st)}"
        out.extend(eps)
    return out


def build_reclaim_candidates(
    streams: dict[str, list[Tick]],
    episodes: Sequence[Episode],
    *,
    hold_events: int = 2,
) -> list[ReclaimCandidate]:
    """One row per episode reclaim cross that can observe hold_events after cross."""
    by_key: dict[str, list[Episode]] = {}
    for ep in episodes:
        by_key.setdefault(ep.stream_key, []).append(ep)

    out: list[ReclaimCandidate] = []
    for key, eps in by_key.items():
        ticks = streams[key]
        for ep in eps:
            if ep.reclaim_level is None:
                continue
            # find first cross after level created / after buy if buy occurred
            start_scan = ep.trend_idx or ep.start_idx
            if ep.reclaim_level_created_at is not None:
                for i in range(ep.start_idx, len(ticks)):
                    if ticks[i].ts >= ep.reclaim_level_created_at:
                        start_scan = i
                        break
            # causal: cross must be after buy_flow if buy_flow happened; else after level freeze
            min_cross = max(start_scan, (ep.buy_idx or -1) + 1 if ep.buy_idx is not None else start_scan)
            # also allow cross after level freeze without buy for nested base table
            min_cross = start_scan
            cross_idx = None
            lvl = float(ep.reclaim_level)
            for j in range(min_cross, len(ticks) - hold_events):
                px = ticks[j].px
                if px is None:
                    continue
                if px > lvl:
                    # ensure level was frozen before this event
                    if ep.reclaim_level_created_at is not None and ticks[j].ts < ep.reclaim_level_created_at:
                        continue
                    cross_idx = j
                    break
            if cross_idx is None:
                continue
            # common decision = 2nd causal event after cross
            dec = cross_idx + hold_events
            if dec >= len(ticks):
                continue
            # hold: next hold_events events all above level (observed by decision)
            above = 0
            for k in range(cross_idx + 1, cross_idx + 1 + hold_events):
                if ticks[k].px is not None and ticks[k].px > lvl:
                    above += 1
            hold_ok = above >= hold_events
            parts = exec_parts(ticks[dec])
            fill = first_valid_ask(ticks, dec, min_delay=0.0)
            if fill is None:
                fill = first_valid_ask(ticks, dec, min_delay=0.001)
            exec_idx = exec_t = exec_px = None
            ok_exec = False
            if fill is not None:
                exec_idx, exec_px, _ = fill
                exec_t = ticks[exec_idx].ts
                ok_exec = True

            def before(idx: Optional[int]) -> bool:
                return idx is not None and idx <= dec

            trend_ok = before(ep.trend_idx) or ep.flags.get("has_trend", False)
            pb_ok = before(ep.pullback_idx)
            exh_ok = before(ep.exhaust_idx)
            buy_ok = before(ep.buy_idx)
            # reclaim cross always true for this row; for F5 also require SM reclaim after buy when buy exists
            reclaim_cross = True

            flags_snap = {
                "trend": trend_ok, "pullback": pb_ok, "exh": exh_ok, "buy": buy_ok,
                "hold": hold_ok, **parts, "lvl": lvl, "cross": cross_idx, "dec": dec,
            }
            rid = f"{ep.day}|{ep.symbol}|{ep.episode_id}|{_seq(ticks[cross_idx])}"
            out.append(ReclaimCandidate(
                reclaim_candidate_id=rid,
                episode_id=ep.episode_id,
                impulse_id=ep.impulse_id,
                day=ep.day,
                symbol=ep.symbol,
                stream_key=key,
                reclaim_cross_event_seq=_seq(ticks[cross_idx]),
                reclaim_cross_idx=cross_idx,
                reclaim_cross_time=ticks[cross_idx].ts,
                reclaim_level=lvl,
                reclaim_level_created_at=ep.reclaim_level_created_at,
                pullback_low=ep.pullback_low,
                initial_impulse_high=ep.impulse_high,
                common_decision_idx=dec,
                common_decision_time=ticks[dec].ts,
                common_decision_event_seq=_seq(ticks[dec]),
                trend_context_pass=bool(trend_ok),
                pullback_pass=bool(pb_ok),
                selling_exhausted_pass=bool(exh_ok),
                buy_flow_pass=bool(buy_ok),
                reclaim_cross_pass=reclaim_cross,
                reclaim_hold_2events_pass=hold_ok,
                liquidity_pass=parts["liquidity_pass"],
                quote_quality_pass=parts["quote_quality_pass"],
                ask_qty_100_pass=parts["ask_qty_100_pass"],
                native_trend_time=ticks[ep.trend_idx].ts if ep.trend_idx is not None else None,
                native_pullback_time=ticks[ep.pullback_idx].ts if ep.pullback_idx is not None else None,
                native_exhaust_time=ticks[ep.exhaust_idx].ts if ep.exhaust_idx is not None else None,
                native_buy_time=ticks[ep.buy_idx].ts if ep.buy_idx is not None else None,
                native_reclaim_time=ticks[cross_idx].ts,
                entry_execution_idx=exec_idx,
                entry_execution_time=exec_t,
                entry_execution_price=exec_px,
                exec_ok=ok_exec,
                snapshot_hash=_hash_flags(flags_snap),
            ))
    return out


def arm_passes(c: ReclaimCandidate, arm: str) -> tuple[bool, str]:
    if not c.exec_ok or c.entry_execution_idx is None:
        return False, "no_execution"
    if arm == "F0_RECLAIM_BASE":
        return True, ""
    if arm == "F1_TREND":
        return (c.trend_context_pass, "" if c.trend_context_pass else "trend")
    if arm == "F2_PULLBACK":
        ok = c.trend_context_pass and c.pullback_pass
        return ok, "" if ok else "pullback"
    if arm == "F3_EXHAUSTION":
        ok = c.trend_context_pass and c.pullback_pass and c.selling_exhausted_pass
        return ok, "" if ok else "exhaustion"
    if arm == "F4_BUY_FLOW":
        ok = c.trend_context_pass and c.pullback_pass and c.selling_exhausted_pass and c.buy_flow_pass
        return ok, "" if ok else "buy_flow"
    if arm == "F5_FULL_FCR":
        ok = (
            c.trend_context_pass and c.pullback_pass and c.selling_exhausted_pass and c.buy_flow_pass
            and c.reclaim_cross_pass and c.reclaim_hold_2events_pass
            and c.liquidity_pass and c.quote_quality_pass and c.ask_qty_100_pass
        )
        return ok, "" if ok else "f5_gate"
    return False, "unknown_arm"


def materialize_arms(cands: Sequence[ReclaimCandidate]) -> dict[str, list[ArmRow]]:
    """Nested boolean filters: select F0 (one impulse one entry), then pure subsets."""
    ordered = sorted(cands, key=lambda c: (c.day, c.symbol, c.common_decision_time, c.reclaim_candidate_id))
    # F0 base: chronological first executable reclaim per impulse
    f0_cands: list[ReclaimCandidate] = []
    used_imp: set[str] = set()
    for c in ordered:
        ok, _ = arm_passes(c, "F0_RECLAIM_BASE")
        if not ok or c.impulse_id in used_imp:
            continue
        f0_cands.append(c)
        used_imp.add(c.impulse_id)

    def rows_for(arm: str, pool: Sequence[ReclaimCandidate]) -> list[ArmRow]:
        out: list[ArmRow] = []
        parent = PARENT[arm]
        for c in pool:
            ok, reason = arm_passes(c, arm)
            if not ok:
                continue
            out.append(ArmRow(
                arm_id=arm,
                parent_arm_id=parent,
                reclaim_candidate_id=c.reclaim_candidate_id,
                episode_id=c.episode_id,
                parent_candidate_id=c.reclaim_candidate_id if parent else None,
                common_decision_time=c.common_decision_time,
                entry_execution_time=c.entry_execution_time,
                entry_execution_price=c.entry_execution_price,
                filter_pass=True,
                filter_fail_reason=reason,
                causal_fields_snapshot_hash=c.snapshot_hash,
                stream_key=c.stream_key,
                entry_idx=int(c.entry_execution_idx),
                day=c.day,
                symbol=c.symbol,
                impulse_id=c.impulse_id,
            ))
        return out

    # cascade: each arm filters the previous arm's candidate objects
    tables: dict[str, list[ArmRow]] = {}
    pool: list[ReclaimCandidate] = list(f0_cands)
    arm_pools: dict[str, list[ReclaimCandidate]] = {"F0_RECLAIM_BASE": pool}
    tables["F0_RECLAIM_BASE"] = rows_for("F0_RECLAIM_BASE", pool)
    for arm in ("F1_TREND", "F2_PULLBACK", "F3_EXHAUSTION", "F4_BUY_FLOW", "F5_FULL_FCR"):
        pool = [c for c in pool if arm_passes(c, arm)[0]]
        arm_pools[arm] = pool
        tables[arm] = rows_for(arm, pool)
    return tables


def audit_arm_nesting(tables: dict[str, list[ArmRow]]) -> dict[str, Any]:
    ids = {a: {r.reclaim_candidate_id for r in tables[a]} for a in ARMS}
    checks = {
        "F1_subset_F0": ids["F1_TREND"] <= ids["F0_RECLAIM_BASE"],
        "F2_subset_F1": ids["F2_PULLBACK"] <= ids["F1_TREND"],
        "F3_subset_F2": ids["F3_EXHAUSTION"] <= ids["F2_PULLBACK"],
        "F4_subset_F3": ids["F4_BUY_FLOW"] <= ids["F3_EXHAUSTION"],
        "F5_subset_F4": ids["F5_FULL_FCR"] <= ids["F4_BUY_FLOW"],
    }
    counts = {a: len(ids[a]) for a in ARMS}
    mono = (
        counts["F5_FULL_FCR"] <= counts["F4_BUY_FLOW"] <= counts["F3_EXHAUSTION"]
        <= counts["F2_PULLBACK"] <= counts["F1_TREND"] <= counts["F0_RECLAIM_BASE"]
    )
    ok = all(checks.values()) and mono
    return {
        "checks": checks,
        "counts": counts,
        "monotonic_counts": mono,
        "verdict": "ARM_NESTING_PASS" if ok else "ARM_NESTING_BLOCKED",
    }


def audit_parent_lineage(tables: dict[str, list[ArmRow]], cands: Sequence[ReclaimCandidate]) -> dict[str, Any]:
    by_id = {c.reclaim_candidate_id: c for c in cands}
    child_without_parent = 0
    parent_id_mismatch = 0
    common_anchor_mismatch = 0
    entry_time_mismatch = 0
    execution_price_mismatch = 0
    for arm in ARMS:
        parent = PARENT[arm]
        if parent is None:
            continue
        parent_ids = {r.reclaim_candidate_id: r for r in tables[parent]}
        for r in tables[arm]:
            if r.reclaim_candidate_id not in parent_ids:
                child_without_parent += 1
                continue
            pr = parent_ids[r.reclaim_candidate_id]
            if r.parent_candidate_id != pr.reclaim_candidate_id:
                parent_id_mismatch += 1
            if r.common_decision_time != pr.common_decision_time:
                common_anchor_mismatch += 1
            if r.entry_execution_time != pr.entry_execution_time:
                entry_time_mismatch += 1
            if r.entry_execution_price != pr.entry_execution_price:
                execution_price_mismatch += 1
            # also vs candidate table
            c = by_id.get(r.reclaim_candidate_id)
            if c and r.common_decision_time != c.common_decision_time:
                common_anchor_mismatch += 1
    ok = child_without_parent == parent_id_mismatch == common_anchor_mismatch == entry_time_mismatch == execution_price_mismatch == 0
    return {
        "child_without_parent": child_without_parent,
        "parent_id_mismatch": parent_id_mismatch,
        "common_anchor_mismatch": common_anchor_mismatch,
        "entry_time_mismatch": entry_time_mismatch,
        "execution_price_mismatch": execution_price_mismatch,
        "verdict_parent": "PARENT_LINEAGE_PASS" if ok else "PARENT_LINEAGE_BLOCKED",
        "verdict_anchor": "COMMON_ANCHOR_PASS" if common_anchor_mismatch == 0 and entry_time_mismatch == 0 and execution_price_mismatch == 0 else "COMMON_ANCHOR_BLOCKED",
    }


def audit_state_stage_nesting(episodes: Sequence[Episode]) -> dict[str, Any]:
    """Unique episode reach counts — monotonic stage nesting (flags ∪ states)."""
    eps = [e for e in episodes if e.day == TRAIN_DAY]

    def reached(ep: Episode, state: str) -> bool:
        if state in ep.states:
            return True
        # flag fallback for causal stage reach (no double-count across episodes)
        fl = ep.flags or {}
        return {
            "TREND_CONTEXT": fl.get("has_trend"),
            "PULLBACK_DETECTED": fl.get("has_pullback"),
            "SELLING_EXHAUSTED": fl.get("has_exhaustion"),
            "BUY_FLOW_CONFIRMED": fl.get("has_buy_flow"),
            "RECLAIM_TRIGGERED": fl.get("has_reclaim") or ep.status == "ENTRY_READY",
            "ENTRY_READY": ep.status == "ENTRY_READY",
        }.get(state, False)

    stages = [
        "TREND_CONTEXT", "PULLBACK_DETECTED", "SELLING_EXHAUSTED",
        "BUY_FLOW_CONFIRMED", "RECLAIM_TRIGGERED", "ENTRY_READY",
    ]
    counts = {}
    sets = {}
    for st in stages:
        s = {e.episode_id for e in eps if reached(e, st)}
        sets[st] = s
        counts[st] = len(s)
    nest = (
        sets["ENTRY_READY"] <= sets["RECLAIM_TRIGGERED"] <= sets["BUY_FLOW_CONFIRMED"]
        <= sets["SELLING_EXHAUSTED"] <= sets["PULLBACK_DETECTED"] <= sets["TREND_CONTEXT"]
    )
    mono = (
        counts["ENTRY_READY"] <= counts["RECLAIM_TRIGGERED"] <= counts["BUY_FLOW_CONFIRMED"]
        <= counts["SELLING_EXHAUSTED"] <= counts["PULLBACK_DETECTED"] <= counts["TREND_CONTEXT"]
    )
    ok = nest and mono
    return {"counts": counts, "nested": nest, "verdict": "STATE_STAGE_NESTING_PASS" if ok else "STATE_STAGE_NESTING_BLOCKED"}


def audit_f5_spread_spec() -> dict[str, Any]:
    """Existing code has absolute spread_max_bps only; no spread_not_widening."""
    from pathlib import Path
    sm = Path(__file__).resolve().parents[1] / "canonical_fcr_exact_method" / "state_machine.py"
    src = sm.read_text(encoding="utf-8")
    has_widening = "spread_not_widening" in src or "非拡大" in src
    has_abs = "spread_max_bps" in src
    none_means = (
        "absolute_spread_cap disabled when spread_max_bps is None; "
        "does NOT implement spread_not_widening; "
        "quote_quality + ask_qty_100 remain via exec_ok"
    )
    gate_present = has_widening  # required semantic for F5 per integrity phase
    return {
        "spread_not_widening_defined": has_widening,
        "absolute_spread_cap_defined": has_abs,
        "spread_max_bps_frozen": FROZEN["spread_max_bps"],
        "spread_max_bps_none_means": none_means,
        "quote_quality_via_exec_ok": True,
        "ask_qty_100_via_exec_ok": True,
        "F5_SPREAD_GATE": "F5_SPREAD_GATE_PRESENT" if gate_present else "F5_SPREAD_GATE_MISSING",
        "F5_SPEC_CONFORMANCE": (
            "F5_SPEC_CONFORMANCE_PASS" if gate_present else "F5_SPEC_CONFORMANCE_BLOCKED"
        ),
        "note": "No inventing spread_not_widening; frozen SoT left absolute cap None.",
    }
