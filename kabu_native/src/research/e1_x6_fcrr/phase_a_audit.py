"""Phase A: SELLING_EXHAUSTED reachability audit (Spec 1.0 fixed thresholds, no economics).

Audits every evaluation event while in PULLBACK_ACTIVE under the frozen v1 machine.
Does NOT change Spec 1.0 thresholds. Does NOT overwrite the frozen reference run.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.e1_x6_fcrr.config import THRESHOLDS
from research.e1_x6_fcrr.decision import push_and_decide
from research.e1_x6_fcrr.features import FeatureBuffer
from research.e1_x6_fcrr.replay import (
    _universe_from_manifest,
    load_day_events,
    load_source_manifest,
)
from research.e1_x6_fcrr.state_machine import Machine, math_isfinite
from research.e1_x6_provisional.analysis_mask import build_mask_index, row_in_analysis_mask
from research.e1_x6_provisional.util import sha256_file, sha256_obj

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
CANDIDATE_ID = "FCRR_R10"  # reference Spec 1.0 retention; SE rules are shared
VOLUME_FLOOR = 1800.0  # frozen reference floor from e1x6_fcrr_20260803_075026_e53466


def _session_of(ts) -> str:
    return "AM" if ts.hour < 12 else "PM"


def _missing_features(feats: dict[str, Any]) -> list[str]:
    keys = [
        "ret_15s", "ret_30s", "down_tick_volume_ratio_15s", "down_tick_volume_ratio_60s",
        "spread_bps", "mid", "bid", "ask", "vwap", "atr_180s",
    ]
    out = []
    for k in keys:
        v = feats.get(k)
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            out.append(k)
    if not feats.get("complete"):
        out.append(f"incomplete:{feats.get('reason') or 'UNKNOWN'}")
    return out


def audit_se_row(
    *,
    machine: Machine,
    t: float,
    feats: dict[str, Any],
    day: str,
    session: str,
    prev_bid: float,
) -> dict[str, Any]:
    """Decompose Spec 1.0 SELLING_EXHAUSTED conditions at one PULLBACK_ACTIVE event."""
    s = THRESHOLDS["selling_exhausted"]
    ep = machine.episode
    assert ep is not None
    missing = _missing_features(feats)
    pb_low = ep.pullback_low
    pb_low_t = ep.pullback_low_t
    sec_since = (t - pb_low_t) if math_isfinite(pb_low_t) else None
    mid = feats.get("mid")
    new_low = bool(
        mid is not None and math_isfinite(pb_low) and float(mid) < pb_low - 1e-12
    )
    no_new_low_30s_pass = bool(
        sec_since is not None and sec_since + 1e-9 >= s["no_new_low_sec"] and not new_low
    )
    r15, r30 = feats.get("ret_15s"), feats.get("ret_30s")
    ret_pass = r15 is not None and r30 is not None and float(r15) >= float(r30) - 1e-12
    d15, d60 = feats.get("down_tick_volume_ratio_15s"), feats.get("down_tick_volume_ratio_60s")
    down_pass = d15 is not None and d60 is not None and float(d15) < float(d60) - 1e-12
    bid = feats.get("bid")
    best_bid_declining = (
        bid is not None and math_isfinite(prev_bid) and float(bid) < prev_bid - 1e-12
    )
    # Spec 1.0 used bid_down_streak>=2 as fail; streak only updates in SE state in v1,
    # so during PULLBACK this is effectively always pass unless carried (reset on CONTEXT).
    bid_pass = machine.bid_down_streak < 2
    spread = feats.get("spread_bps")
    spread_pass = spread is not None and float(spread) <= s["spread_bps_max"] + 1e-12
    event_fresh = feats.get("complete") is True and feats.get("reason") != "STALE"
    board_fresh = event_fresh  # board depth not required in v1; quote freshness proxies

    geo_ok, geo_why = machine._pullback_geometry(t, feats, ep)
    final_pass = bool(
        geo_ok
        and no_new_low_30s_pass
        and ret_pass
        and down_pass
        and bid_pass
        and spread_pass
        and not missing
        and machine._selling_exhausted(t, feats, ep)
    )

    dominant_reject = None
    if missing:
        dominant_reject = "MISSING_FEATURE"
    elif not geo_ok:
        dominant_reject = geo_why or "GEOMETRY_FAIL"
    elif new_low or (sec_since is not None and sec_since < s["no_new_low_sec"]):
        dominant_reject = "NO_NEW_LOW_30S_FAIL"
    elif not ret_pass:
        dominant_reject = "RET15_GE_RET30_FAIL" if (r15 is not None and r30 is not None) else "RET_MISSING"
    elif not down_pass:
        dominant_reject = (
            "DOWN_TICK_DECEL_FAIL" if (d15 is not None and d60 is not None) else "DOWN_TICK_MISSING"
        )
    elif not spread_pass:
        dominant_reject = "SPREAD_FAIL"
    elif not bid_pass:
        dominant_reject = "BID_STREAK_FAIL"

    return {
        "candidate_id": machine.candidate_id,
        "day": day,
        "session": session,
        "symbol": machine.symbol,
        "episode_id": ep.episode_id,
        "event_time": t,
        "current_state": machine.state,
        "state_entered_at": ep.pullback_start_t if math_isfinite(ep.pullback_start_t) else ep.started_at,
        "pullback_low": pb_low if math_isfinite(pb_low) else None,
        "pullback_low_time": pb_low_t if math_isfinite(pb_low_t) else None,
        "seconds_since_pullback_low": sec_since,
        "new_low_this_event": new_low,
        "no_new_low_30s_pass": no_new_low_30s_pass,
        "ret_15s": r15,
        "ret_30s": r30,
        "ret_15_ge_ret_30_pass": bool(ret_pass),
        "down_tick_volume_ratio_15s": d15,
        "down_tick_volume_ratio_60s": d60,
        "down_tick_deceleration_pass": bool(down_pass),
        "best_bid": bid,
        "previous_best_bid": prev_bid if math_isfinite(prev_bid) else None,
        "best_bid_declining": bool(best_bid_declining),
        "best_bid_pass": bool(bid_pass),
        "spread_bps": spread,
        "spread_pass": bool(spread_pass),
        "event_fresh": bool(event_fresh),
        "board_fresh": bool(board_fresh),
        "missing_feature_list": missing,
        "geometry_ok": bool(geo_ok),
        "geometry_reason": geo_why,
        "invalidation_reason": None,
        "selling_exhausted_final_pass": final_pass,
        "dominant_reject": dominant_reject,
        "bid_down_streak": machine.bid_down_streak,
    }


def run_phase_a(days: Optional[tuple[str, ...]] = None) -> dict[str, Any]:
    from research.e1_x6_fcrr.config import DAYS

    days = days or DAYS
    sm = load_source_manifest()
    mask_index = build_mask_index(sm)
    run_id = f"e1x6_fcrr_phase_a_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}"
    store = Path.home() / "e1x6_research_store" / "fcrr" / run_id
    store.mkdir(parents=True, exist_ok=True)
    audit_path = store / "se_event_audit.jsonl"
    trans_path = store / "state_transitions.jsonl"

    # aggregates
    funnel_events = Counter()  # events while in state (dwell)
    funnel_enters = Counter()  # transitions TO state
    unique_episodes: dict[str, set] = defaultdict(set)
    unique_pb_episodes: set[tuple] = set()
    pass_counts = Counter()
    and_surviving = Counter()
    reject_dom = Counter()
    no_new_low_pass_n = 0
    missing_false_n = 0
    pullback_low_updated_n = 0
    ctx_to_pb_n = 0
    state_hold_evidence = []
    day_funnel = defaultdict(Counter)
    context_fail_721: Counter = Counter()
    se_pass_events = 0
    audit_n = 0
    transitions_n = 0
    prev_state_by_sym: dict[str, str] = {}
    state_entered_wall: dict[str, float] = {}

    with audit_path.open("w", encoding="utf-8") as fa, trans_path.open("w", encoding="utf-8") as ft:
        for day in days:
            uni = _universe_from_manifest(sm, day)
            if not uni:
                continue
            print(f"  PhaseA {day} universe={len(uni)}", flush=True)
            events = load_day_events(day, uni)
            bufs = {s: FeatureBuffer() for s in uni}
            machines = {
                s: Machine(
                    symbol=s,
                    candidate_id=CANDIDATE_ID,
                    volume_abs_floor=VOLUME_FLOOR,
                    _retain_transitions=True,
                )
                for s in uni
            }
            last_eval: dict[str, float] = {}
            prev_bid: dict[str, float] = {s: float("nan") for s in uni}
            for t, sym, row in events:
                mask = row_in_analysis_mask(day, row["ts"], mask_index)
                in_mask = bool(mask.get("in_analysis_mask"))
                bucket = int(t // 5.0)
                pb = int(last_eval[sym] // 5.0) if sym in last_eval else None
                evaluate = in_mask and (pb is None or bucket != pb)
                m = machines[sym]
                before = m.state
                before_ep = None if m.episode is None else m.episode.episode_id
                before_pb_low = (
                    None if m.episode is None or not math_isfinite(m.episode.pullback_low)
                    else m.episode.pullback_low
                )
                before_pb_t = (
                    None if m.episode is None or not math_isfinite(m.episode.pullback_low_t)
                    else m.episode.pullback_low_t
                )

                sig, feats = push_and_decide(
                    bufs[sym], m,
                    t=t, bid=row["bid"], ask=row["ask"], vwap=row["vwap"], cum_vol=row["vol"],
                    evaluate=evaluate,
                )
                if not evaluate:
                    continue
                last_eval[sym] = t
                session = _session_of(row["ts"])

                # write transitions emitted this step (actual ledger, not a note)
                n_step = len(m.last_step_tos)
                step_trs = m.transitions[-n_step:] if n_step else []
                snap = {
                    k: feats.get(k)
                    for k in (
                        "mid", "bid", "ask", "vwap", "spread_bps", "ret_15s", "ret_30s",
                        "ret_180s", "atr_180s", "volume_10s", "volume_30s",
                        "down_tick_volume_ratio_15s", "down_tick_volume_ratio_60s",
                        "complete", "reason", "asof_time",
                    )
                }
                for tr in step_trs:
                    transitions_n += 1
                    to = tr.get("to")
                    funnel_enters[to] += 1
                    day_funnel[day][to] += 1
                    if tr.get("from") == "CONTEXT_READY" and to == "PULLBACK_ACTIVE":
                        ctx_to_pb_n += 1
                    eid = tr.get("episode_id")
                    if eid is not None and to in (
                        "CONTEXT_READY", "PULLBACK_ACTIVE", "SELLING_EXHAUSTED",
                        "RECLAIM_CROSSED", "RETENTION_CONFIRMED", "ENTRY_EMITTED",
                    ):
                        unique_episodes[to].add((day, sym, int(eid)))
                    ft.write(json.dumps({
                        "from_state": tr.get("from"),
                        "to_state": to,
                        "event_time": t,
                        "episode_id": eid,
                        "symbol": sym,
                        "day": day,
                        "session": session,
                        "trigger": tr.get("reason"),
                        "feature_snapshot": snap,
                        "candidate_id": CANDIDATE_ID,
                    }, ensure_ascii=False, default=str) + "\n")
                if step_trs:
                    m.transitions.clear()  # ledger already flushed to jsonl

                if m.episode is not None:
                    unique_episodes["ANY"].add((day, sym, m.episode.episode_id))
                    if before == "PULLBACK_ACTIVE" or m.state == "PULLBACK_ACTIVE":
                        unique_pb_episodes.add((day, sym, m.episode.episode_id))

                # dwell audit while remaining in PULLBACK_ACTIVE after this observation
                if m.state == "PULLBACK_ACTIVE" and m.episode is not None:
                    row_a = audit_se_row(
                        machine=m, t=t, feats=feats, day=day, session=session,
                        prev_bid=prev_bid[sym],
                    )
                    if before_pb_low is not None and row_a["pullback_low"] is not None:
                        if float(row_a["pullback_low"]) < float(before_pb_low) - 1e-12:
                            pullback_low_updated_n += 1
                            row_a["new_low_this_event"] = True
                    fa.write(json.dumps(row_a, ensure_ascii=False, default=str) + "\n")
                    audit_n += 1
                    funnel_events["PULLBACK_ACTIVE"] += 1
                    day_funnel[day]["PULLBACK_ACTIVE_EVENTS"] += 1

                    for key, flag in (
                        ("no_new_low_30s_pass", row_a["no_new_low_30s_pass"]),
                        ("ret_15_ge_ret_30_pass", row_a["ret_15_ge_ret_30_pass"]),
                        ("down_tick_deceleration_pass", row_a["down_tick_deceleration_pass"]),
                        ("best_bid_pass", row_a["best_bid_pass"]),
                        ("spread_pass", row_a["spread_pass"]),
                        ("geometry_ok", row_a["geometry_ok"]),
                        ("selling_exhausted_final_pass", row_a["selling_exhausted_final_pass"]),
                    ):
                        if flag:
                            pass_counts[key] += 1
                    if row_a["no_new_low_30s_pass"]:
                        no_new_low_pass_n += 1
                    if row_a["missing_feature_list"]:
                        missing_false_n += 1
                    if row_a["dominant_reject"]:
                        reject_dom[row_a["dominant_reject"]] += 1
                    if row_a["selling_exhausted_final_pass"]:
                        se_pass_events += 1

                    ok = True
                    for nm, fl in (
                        ("geometry", row_a["geometry_ok"]),
                        ("no_new_low_30s", row_a["no_new_low_30s_pass"]),
                        ("ret15_ge_ret30", row_a["ret_15_ge_ret_30_pass"]),
                        ("down_tick_decel", row_a["down_tick_deceleration_pass"]),
                        ("spread", row_a["spread_pass"]),
                        ("best_bid", row_a["best_bid_pass"]),
                    ):
                        ok = ok and bool(fl)
                        if ok:
                            and_surviving[nm] += 1

                    key = f"{day}|{sym}|{m.episode.episode_id}"
                    first_t = state_entered_wall.get(key)
                    if first_t is None:
                        state_entered_wall[key] = t
                    elif t > first_t + 1e-9 and len(state_hold_evidence) < 20:
                        state_hold_evidence.append({
                            "symbol": sym, "day": day,
                            "episode_id": m.episode.episode_id,
                            "earlier_t": first_t,
                            "later_t": t,
                            "dt_sec": t - first_t,
                            "state": "PULLBACK_ACTIVE",
                        })

                # 7/21 context fail sample
                if day == "20260721" and evaluate and before == "IDLE" and m.state == "IDLE":
                    if feats.get("complete"):
                        if not m._context_ready(feats):
                            # first failing check
                            c = THRESHOLDS["context"]
                            order = [
                                ("need_none", all(
                                    feats.get(k) is not None for k in (
                                        "mid", "vwap", "ret_180s", "linear_slope_180s",
                                        "distance_from_session_high", "distance_above_vwap",
                                        "spread_bps", "price_update_count_60s",
                                        "active_volume_windows_120s", "atr_180s",
                                    )
                                )),
                                ("mid_gt_vwap", feats.get("mid") is not None and feats.get("vwap") is not None and feats["mid"] > feats["vwap"]),
                                ("ret_180s_gt_0", feats.get("ret_180s") is not None and feats["ret_180s"] > 0),
                                ("slope_gt_0", feats.get("linear_slope_180s") is not None and feats["linear_slope_180s"] > 0),
                                ("dist_high", feats.get("distance_from_session_high") is not None and feats["distance_from_session_high"] <= c["distance_from_session_high_atr_max"]),
                                ("dist_vwap", feats.get("distance_above_vwap") is not None and feats["distance_above_vwap"] <= c["distance_above_vwap_atr_max"]),
                                ("spread", feats.get("spread_bps") is not None and feats["spread_bps"] <= c["spread_bps_max"]),
                                ("pu60", feats.get("price_update_count_60s") is not None and feats["price_update_count_60s"] >= c["price_update_count_60s_min"]),
                                ("act", feats.get("active_volume_windows_120s") is not None and feats["active_volume_windows_120s"] >= c["active_volume_windows_120s_min"]),
                            ]
                            for name, ok in order:
                                if not ok:
                                    context_fail_721[name] += 1
                                    break
                    else:
                        context_fail_721[f"incomplete:{feats.get('reason')}"] += 1

                if feats.get("bid") is not None:
                    prev_bid[sym] = float(feats["bid"])
                prev_state_by_sym[sym] = m.state

            print(
                f"    enters CONTEXT={day_funnel[day].get('CONTEXT_READY', 0)} "
                f"PB={day_funnel[day].get('PULLBACK_ACTIVE', 0)} "
                f"PB_events={day_funnel[day].get('PULLBACK_ACTIVE_EVENTS', 0)}",
                flush=True,
            )

    answers = {
        "q1_pullback_active_25644_meaning": {
            "answer": "TRANSITION_ENTER_COUNTS",
            "detail": (
                "Frozen-run funnel PULLBACK_ACTIVE=25644 counts state-machine transitions "
                "TO PULLBACK_ACTIVE (enter events), not dwell events and not unique episodes."
            ),
            "phase_a_enter_count": funnel_enters.get("PULLBACK_ACTIVE", 0),
            "phase_a_dwell_event_count": funnel_events.get("PULLBACK_ACTIVE", 0),
        },
        "q2_unique_pullback_episodes": len(unique_pb_episodes),
        "q2_unique_context_episodes": len(unique_episodes.get("CONTEXT_READY", set())),
        "q3_standalone_pass_counts": dict(pass_counts),
        "q4_cumulative_and_surviving": dict(and_surviving),
        "q5_dominant_reject": reject_dom.most_common(15),
        "q6_no_new_low_30s_pass_events": no_new_low_pass_n,
        "q7_missing_feature_events": missing_false_n,
        "q8_pullback_low_updated_events": pullback_low_updated_n,
        "q9_state_hold_evidence": state_hold_evidence[:10],
        "q10_transitions_n_zero_reason": {
            "answer": "PUBLISH_DROPPED_LEDGER",
            "detail": (
                "replay_all_candidates deliberately discarded state_transitions "
                "(transitions.extend(...[:0])) to shrink publish size, while funnel counters "
                "were incremented from Machine.last_step_tos. Not a state-machine bug."
            ),
            "phase_a_transitions_written": transitions_n,
        },
        "q11_context_to_pullback_transitions": ctx_to_pb_n,
        "q12_20260721_context_zero": {
            "frozen_funnel_CONTEXT_READY": 0,
            "phase_a_CONTEXT_READY": day_funnel.get("20260721", {}).get("CONTEXT_READY", 0),
            "first_fail_counts": context_fail_721.most_common(20),
            "detail": (
                "Under Spec 1.0 hard AND context (mid>vwap AND ret180>0 AND slope>0 AND "
                "dist_high/vwap AND spread AND activity), 20260721 never jointly satisfied "
                "CONTEXT_READY on any evaluation observation in the frozen/reference replay."
            ),
        },
    }

    report = {
        "phase": "A_SELLING_EXHAUSTED_REACHABILITY_AUDIT",
        "status": "PHASE_A_COMPLETE",
        "plan_document_id": "E1_X6_VALIDATION_PLAN",
        "plan_version": "1.3",
        "document_id": "E1_X6_FCRR_IMPLEMENTATION_SPEC",
        "document_version": "1.2",
        "reference_run_id": "e1x6_fcrr_20260803_075026_e53466",
        "reference_status": "FCRR_V1_FIXED_THRESHOLD_UNREACHABLE_REFERENCE",
        "phase_a_run_id": run_id,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "candidate_id_audited": CANDIDATE_ID,
        "volume_abs_floor_q50": VOLUME_FLOOR,
        "thresholds_changed": False,
        "economics_opened": False,
        "audit_events_n": audit_n,
        "transitions_n": transitions_n,
        "funnel_enters": dict(funnel_enters),
        "funnel_dwell_events": dict(funnel_events),
        "unique_episodes": {k: len(v) for k, v in unique_episodes.items()},
        "unique_pullback_episodes": len(unique_pb_episodes),
        "se_pass_events": se_pass_events,
        "day_funnel": {d: dict(c) for d, c in day_funnel.items()},
        "answers": answers,
        "artifact_paths": {
            "se_event_audit_jsonl": str(audit_path),
            "state_transitions_jsonl": str(trans_path),
        },
        "safety": {"submit": 0, "cancel": 0, "live": 0},
        "mainline_changed": False,
        "implementation_bug_found": False,
        "implementation_bug_notes": [
            "transitions_n=0 on frozen publish was intentional ledger drop, now corrected for Phase A.",
            "bid_down_streak only updates in SELLING_EXHAUSTED state in v1; during PULLBACK_ACTIVE best_bid_pass is almost always true — documented, not changed (threshold freeze).",
        ],
    }
    (store / "phase_a_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    report["phase_a_report_sha256"] = sha256_file(store / "phase_a_report.json")
    # rewrite with sha
    (store / "phase_a_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    rep = run_phase_a()
    print("PHASE_A_DONE", rep["phase_a_run_id"], flush=True)
    print(json.dumps(rep["answers"], ensure_ascii=False, indent=2, default=str)[:4000], flush=True)
