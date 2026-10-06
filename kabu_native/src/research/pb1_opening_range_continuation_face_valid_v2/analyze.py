"""Face-validity summaries. No PnL. No matching. No threshold search from returns."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from research.pb1_opening_range_continuation_face_valid_v2 import (
    BREAK_BEYOND_ATR_FRAC,
    BREAK_BEYOND_OR_FRAC,
    BREAK_CLOSE_LOC,
    CASE_NOT_REP,
    CASE_READY,
    CASE_REBUILD,
    CLEAN_NET15_FRAC,
    FAILED_SPIKE_FRAC,
    FALSE_BREAK_MERGED,
    GATE_CLEAR_MIN,
    GATE_NOT_MAX,
    GATE_RANGE_NOISE_MAX,
    IN_PLAY_ABS_GAP_ATR,
    IN_PLAY_GAP_AND_TV_GAP,
    IN_PLAY_GAP_AND_TV_PCTL,
    IN_PLAY_GAP_AND_XS_GAP,
    IN_PLAY_GAP_AND_XS_RANK,
    IN_PLAY_TV_AND_XS_PCTL,
    IN_PLAY_TV_AND_XS_RANK,
    IN_PLAY_TV_PCTL_ELEVATED,
    LEAVE_EXT_OR_FRAC,
    MIN_AWAY_BARS,
    NEXT_FIX,
    NEXT_PATH_TEST,
    NEXT_STOP,
    RETEST_FRESHNESS_MIN,
    SAMPLE_N,
    V1_IN_PLAY_SHARE,
)
from research.pb1_opening_range_continuation_face_valid_v2.charts import sampling_procedure
from research.pb1_opening_range_continuation_face_valid_v2.definitions import STATE_MACHINE_TEXT, machine_sha256


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def pct_summary(xs: list[Any]) -> dict[str, Any]:
    a = np.asarray([float(x) for x in xs if _finite(x)], dtype=float)
    if a.size == 0:
        return {"n": 0}
    q = np.quantile(a, [0.10, 0.25, 0.50, 0.75, 0.90])
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "p10": float(q[0]),
        "p25": float(q[1]),
        "p50": float(q[2]),
        "p75": float(q[3]),
        "p90": float(q[4]),
    }


def in_play_report(day_rows: list[dict[str, Any]]) -> dict[str, Any]:
    or_ok = [r for r in day_rows if r.get("or_ok")]
    reasons = Counter(str(r.get("in_play_reason")) for r in or_ok)
    v1_n = int(sum(1 for r in or_ok if r.get("v1_in_play")))
    v2_n = int(sum(1 for r in or_ok if r.get("in_play")))
    xs_alone = int(sum(1 for r in or_ok if r.get("in_play_reason") == "xs_rank_alone_not_in_play"))
    return {
        "purpose": "semantic calibration of genuine activity from blinded V1 charts; not a return search",
        "v1_definition": "gap>=0.30 ATR OR TV clock pctl>=0.50 OR xs rank<=50%",
        "v1_in_play_share": V1_IN_PLAY_SHARE,
        "v1_too_broad_because": (
            "xs rank<=50% is half the cross-section by construction; TV pctl>=0.50 is the own-clock median; "
            "the OR of those two plus a modest gap tagged ordinary/quiet tape as in-play (~81%)."
        ),
        "v2_genuine_in_play": {
            "distinctive_gap_atr": IN_PLAY_ABS_GAP_ATR,
            "own_clock_tv_pctl": IN_PLAY_TV_PCTL_ELEVATED,
            "moderate_gap_and_tv": [IN_PLAY_GAP_AND_TV_GAP, IN_PLAY_GAP_AND_TV_PCTL],
            "elevated_tv_and_top_xs": [IN_PLAY_TV_AND_XS_PCTL, IN_PLAY_TV_AND_XS_RANK],
            "moderate_gap_and_top_xs": [IN_PLAY_GAP_AND_XS_GAP, IN_PLAY_GAP_AND_XS_RANK],
            "xs_rank_alone_sufficient": False,
            "news_required": False,
            "outcome_tuned": False,
        },
        "or_complete_symbol_days": len(or_ok),
        "abs_gap_atr": pct_summary([r.get("abs_gap_atr") for r in or_ok]),
        "tv_0915": pct_summary([r.get("tv_0915") for r in or_ok]),
        "tv_0915_pctl": pct_summary([r.get("tv_0915_pctl") for r in or_ok]),
        "xs_rank_pct": pct_summary([r.get("xs_rank_pct") for r in or_ok]),
        "v1_in_play_n": v1_n,
        "v1_in_play_share_this_walk": float(v1_n / len(or_ok)) if or_ok else None,
        "in_play_n": v2_n,
        "in_play_share": float(v2_n / len(or_ok)) if or_ok else None,
        "xs_rank_alone_not_in_play_n": xs_alone,
        "reason_counts": dict(reasons),
        "news_catalyst": "UNAVAILABLE",
        "future_daily_volume_used": False,
        "ma_used_as_activity_proxy": False,
    }


def event_report(events: list[dict[str, Any]], counts: dict[str, Any]) -> dict[str, Any]:
    by_block = Counter(str(e.get("block")) for e in events)
    by_dir = Counter(str(e.get("direction")) for e in events)
    by_trig = Counter(str(e.get("trigger_primary")) for e in events)
    by_all = Counter()
    for e in events:
        for lab in list(e.get("trigger_labels") or []):
            by_all[str(lab)] += 1
    by_tv = Counter(str(e.get("tv_sequence")) for e in events)
    by_bias = Counter(str(e.get("daily_bias")) for e in events)
    by_vwap = Counter(str(e.get("vwap_trigger")) for e in events)
    by_room = Counter(str(e.get("room_class")) for e in events)
    by_play = Counter(str(e.get("in_play_reason")) for e in events)
    return {
        "setup_n": len(events),
        "in_play_setup_n": int(sum(1 for e in events if e.get("in_play"))),
        "by_block": dict(by_block),
        "by_direction": dict(by_dir),
        "trigger_primary": dict(by_trig),
        "trigger_any": dict(by_all),
        "tv_sequence": dict(by_tv),
        "daily_bias": dict(by_bias),
        "vwap_at_trigger": dict(by_vwap),
        "room_class": dict(by_room),
        "in_play_reason": dict(by_play),
        "reward_space_plausible_n": int(sum(1 for e in events if e.get("reward_space_plausible"))),
        "already_extended_n": int(sum(1 for e in events if e.get("already_extended"))),
        "follow_through_n": int(sum(1 for e in events if e.get("follow_through"))),
        "break_to_retest_minutes": pct_summary([e.get("break_to_retest_minutes") for e in events]),
        "away_n": pct_summary([e.get("away_n") for e in events]),
        "break_beyond": pct_summary([e.get("break_beyond") for e in events]),
        "counts": counts,
        "false_break_merged": FALSE_BREAK_MERGED,
        "clean_opening_impulse_only": True,
        "v1_failed_open_setups_removed_n": int(counts.get("v1_failed_open_setups_removed_n") or 0),
        "v1_stale_setups_removed_n": int(counts.get("v1_stale_setups_removed_n") or 0),
        "v1_in_play_removed_n": int(counts.get("v1_in_play_removed_n") or 0),
        "v1_quiet_setups_removed_n": int(counts.get("v1_quiet_setups_removed_n") or 0),
        "failed_open_n": int(counts.get("failed_open_n") or 0),
        "stale_retest_n": int(counts.get("stale_retest_n") or 0),
        "micro_break_only_n": int(counts.get("micro_break_only_n") or 0),
    }


MATCHING_VARIABLE_ROLES = {
    "in_play_activity_state": "TREATMENT-DEFINING",
    "opening_impulse_path": "TREATMENT-DEFINING",
    "opening_impulse_magnitude": "TREATMENT-DEFINING",
    "opening_impulse_direction": "TREATMENT-DEFINING",
    "or15_location": "TREATMENT-DEFINING",
    "break_quality": "TREATMENT-DEFINING",
    "leave_quality": "TREATMENT-DEFINING",
    "pullback_retest_behavior": "TREATMENT-DEFINING",
    "break_to_retest_minutes": "TREATMENT-DEFINING",
    "trigger_bar": "TREATMENT-DEFINING",
    "daily_sma_alignment": "CONFOUNDER",
    "pdh_pdl_d5_location": "CONFOUNDER",
    "vwap_location": "CONFOUNDER",
    "clock_percentile_tv_sequence": "MEDIATOR",
    "reward_space_at_entry": "NOT_A_MATCHING_COVARIATE_TRADE_QUALITY",
    "future_return": "FORBIDDEN",
    "note": (
        "Do not automatically match away opening impulse magnitude, pullback/retest behavior, "
        "or activity state. Those variables DEFINE PB1. Label before any later matching run."
    ),
}


def decide(body: dict[str, Any]) -> dict[str, Any]:
    human = dict(body.get("human") or {})
    n = int(human.get("reviewed_n") or 0)
    sample_n = int(human.get("sample_n") or body.get("sample_n") or 0)
    clear = int(human.get("CLEAR_OPENING_CONTINUATION") or human.get("CLEAR_INTENDED_SETUP") or 0)
    q = int(human.get("QUESTIONABLE") or 0)
    ni = int(human.get("NOT_OPENING_CONTINUATION") or human.get("NOT_INTENDED_SETUP") or 0)
    share = float(clear / n) if n else 0.0
    not_share = float(ni / n) if n else 0.0
    range_share = human.get("RANGE_NOISE_share")
    range_share_f = float(range_share) if _finite(range_share) else 1.0
    representable = bool(body.get("or15_representable", True))
    same_bar = int(body.get("same_bar_entry_n") or 0)
    false_break = int(body.get("false_break_n") or 0)
    or_mod = int(body.get("or_modified_after_freeze_n") or 0)
    future = int(body.get("future_outcome_n") or 0)
    setup_n = int((body.get("events_summary") or {}).get("setup_n") or 0)
    reused = bool(human.get("v1_sample_reused"))
    if (not representable) or setup_n <= 0:
        verd = CASE_NOT_REP
        nxt = NEXT_STOP
        reason = "or15_continuation_not_constructible_from_native_1m"
    elif same_bar or false_break or or_mod or future:
        verd = CASE_REBUILD
        nxt = NEXT_FIX
        reason = "causal_invariant_broken"
    elif reused:
        verd = CASE_REBUILD
        nxt = NEXT_FIX
        reason = "v1_development_sample_reused"
    elif n < int(SAMPLE_N) or sample_n < int(SAMPLE_N):
        verd = CASE_REBUILD
        nxt = NEXT_FIX
        reason = "independent_verify_sample_incomplete"
    elif share < float(GATE_CLEAR_MIN):
        verd = CASE_REBUILD
        nxt = NEXT_FIX
        reason = "clear_share_below_two_thirds"
    elif not_share > float(GATE_NOT_MAX):
        verd = CASE_REBUILD
        nxt = NEXT_FIX
        reason = "not_intended_not_rare"
    elif range_share_f > float(GATE_RANGE_NOISE_MAX):
        verd = CASE_REBUILD
        nxt = NEXT_FIX
        reason = "range_noise_still_dominant"
    else:
        verd = CASE_READY
        nxt = NEXT_PATH_TEST
        reason = "independent_v2_sample_materially_cleaner_than_v1"
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "reason": reason,
        "sample_n": sample_n,
        "reviewed_n": n,
        "CLEAR_OPENING_CONTINUATION": clear,
        "QUESTIONABLE": q,
        "NOT_OPENING_CONTINUATION": ni,
        "CLEAR_INTENDED_SETUP": clear,
        "NOT_INTENDED_SETUP": ni,
        "clear_share": share if n else None,
        "not_intended_share": not_share if n else None,
        "pattern_face_valid_n": int(human.get("pattern_face_valid_n") or clear),
        "pattern_face_valid_share": human.get("pattern_face_valid_share"),
        "trade_face_valid_n": int(human.get("trade_face_valid_n") or 0),
        "trade_face_valid_share": human.get("trade_face_valid_share"),
        "RANGE_NOISE_share": range_share,
        "FAILED_OPEN_REVERSAL_share": human.get("FAILED_OPEN_REVERSAL_share"),
        "STALE_RETEST_share": human.get("STALE_RETEST_share"),
        "most_common_semantic_failure": human.get("most_common_semantic_failure"),
        "gate": {
            "CLEAR_min": GATE_CLEAR_MIN,
            "NOT_max": GATE_NOT_MAX,
            "RANGE_NOISE_max": GATE_RANGE_NOISE_MAX,
        },
        "future_outcome_used": False,
        "outcome_based_threshold_choice": False,
        "pnl_test": False,
        "matching_run": False,
        "economic_test_run": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "false_break_merged": False,
        "v1_mutated": False,
        "pb2_started": False,
        "pb3_started": False,
        "submit_cancel_live": "0/0/0",
        "SAME_BAR_ENTRY_N": same_bar,
        "new_strategy_run": False,
        "complete_strategy_not_run": True,
        "retest_freshness_minutes": RETEST_FRESHNESS_MIN,
        "freshness_grid_searched": False,
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    sample: list[dict[str, Any]],
    chart_meta: list[dict[str, Any]],
    human: dict[str, Any],
) -> dict[str, Any]:
    events = list(walked.get("events") or [])
    days = list(walked.get("day_rows") or [])
    events_summary = event_report(events, dict(walked.get("counts") or {}))
    inplay = in_play_report(days)
    counts = dict(walked.get("counts") or {})
    body = {
        "or15_representable": True,
        "state_machine": STATE_MACHINE_TEXT,
        "MACHINE_SHA256": machine_sha256(),
        "parent_preserved": {
            "PROGRAM_ID": "PB1_OPENING_RANGE_CONTINUATION_FACE_VALID_V1",
            "PLAYBOOK_MACHINE_SHA256": bind.get("parent_playbook_machine_sha256"),
            "v1_mutated": False,
            "sample_n": bind.get("parent_sample_n"),
            "CLEAR": bind.get("parent_clear"),
            "QUESTIONABLE": bind.get("parent_questionable"),
            "NOT_INTENDED": bind.get("parent_not"),
            "most_common_semantic_failure": bind.get("parent_failure"),
        },
        "in_play": inplay,
        "opening_impulse": {
            "definition": "09:00-09:14 PATH, not 09:14 vs midpoint alone",
            "clean": (
                f"early 5m not opposite, net OR15 > {CLEAN_NET15_FRAC} of OR on that side, "
                "MFE>=MAE (bull) / MAE>=MFE (bear), 09:14 close on that side of OR mid, "
                "and eventual break DIR matches"
            ),
            "failed_open": f"first spike >= {FAILED_SPIKE_FRAC} of OR then reverse through OR by 09:14",
            "emits_only": "CLEAN_OPENING_IMPULSE",
            "open_CLEAN_OPENING_IMPULSE": int(counts.get("open_CLEAN_OPENING_IMPULSE") or 0),
            "open_FAILED_OPEN_REVERSAL": int(counts.get("open_FAILED_OPEN_REVERSAL") or 0),
            "open_RANGE_OPEN": int(counts.get("open_RANGE_OPEN") or 0),
            "open_AMBIGUOUS_OPEN": int(counts.get("open_AMBIGUOUS_OPEN") or 0),
        },
        "or15_freeze": {
            "bars": "09:00-09:14 completed 1m",
            "known_from": "09:15 after 09:14 completes",
            "later_modification": False,
            "or_modified_after_freeze_n": int(walked.get("or_modified_after_freeze_n") or 0),
        },
        "valid_break": (
            f"completed close beyond OR by {BREAK_BEYOND_OR_FRAC} of OR range or "
            f"{BREAK_BEYOND_ATR_FRAC} of ATR20, close location >= {BREAK_CLOSE_LOC} on the trend side; "
            "tiny boundary creep is MICRO_BREAK and does not start a continuation"
        ),
        "leave": (
            f">= {MIN_AWAY_BARS} fully-away bars, or one fully-away bar extending "
            f">= {LEAVE_EXT_OR_FRAC} of OR range; one-bar probe is not a leave"
        ),
        "first_retest": (
            f"after a real leave, first later bar that returns to the frozen OR bound, "
            f"beginning within {RETEST_FRESHNESS_MIN} trading minutes of the valid break. "
            "Later first return = STALE_RETEST; episode ends; no later-retest search."
        ),
        "hold": "first fresh retest close still on the trend side; a close back through kills the episode and is not converted to false-break reversal",
        "triggers": events_summary.get("trigger_primary"),
        "structural_invalidation": "acceptance back inside OR15 through the defended boundary; default reference is the first-retest extreme, not the opposite OR bound; VWAP persisted as location",
        "target_space": "PDH/PDL, PDC, D5H/D5L, daily SMA25/75 if ahead, VWAP if ahead; ROOM_AVAILABLE / ROOM_QUESTIONABLE / NO_OBVIOUS_ROOM at trigger; not optimized",
        "vwap_role": "location diagnostic at break/retest/trigger (trend_side/testing/opposed); not required",
        "participation": "opening impulse vs retest vs trigger clock-percentile TV; sequence labeled, not gated",
        "matching_variable_roles": MATCHING_VARIABLE_ROLES,
        "execution": {
            "entry": "NEXT 1M OPEN after trigger bar completes",
            "SAME_BAR_ENTRY_N": int(walked.get("same_bar_entry_n") or 0),
            "limitation": "minute OHLCV cannot prove queue position or spread",
            "AM_only": True,
        },
        "events_summary": events_summary,
        "sample_n": len(sample),
        "sampling_procedure": sampling_procedure(),
        "chart_meta": chart_meta,
        "human": human,
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "false_break_n": int(walked.get("false_break_n") or 0),
        "or_modified_after_freeze_n": int(walked.get("or_modified_after_freeze_n") or 0),
        "future_outcome_n": int(walked.get("future_outcome_n") or 0),
        "v1_failed_open_reversals_removed": int(counts.get("v1_failed_open_setups_removed_n") or 0),
        "v1_stale_retest_removed": int(counts.get("v1_stale_setups_removed_n") or 0),
        "sample": [
            {
                "sample_id": e.get("sample_id"),
                "symbol": e.get("symbol"),
                "date": e.get("date"),
                "block": e.get("block"),
                "direction": e.get("direction"),
                "in_play": e.get("in_play"),
                "in_play_reason": e.get("in_play_reason"),
                "open_state": e.get("open_state"),
                "open_0900": e.get("open_0900"),
                "close_0904": e.get("close_0904"),
                "close_0914": e.get("close_0914"),
                "first_or_high_t": e.get("first_or_high_t"),
                "first_or_low_t": e.get("first_or_low_t"),
                "mfe_up": e.get("mfe_up"),
                "mae_dn": e.get("mae_dn"),
                "net_5m": e.get("net_5m"),
                "net_or15": e.get("net_or15"),
                "break_t": e.get("break_t"),
                "break_beyond": e.get("break_beyond"),
                "break_body": e.get("break_body"),
                "break_range": e.get("break_range"),
                "break_close_loc": e.get("break_close_loc"),
                "away_n": e.get("away_n"),
                "max_away": e.get("max_away"),
                "retest_t": e.get("retest_t"),
                "break_to_retest_minutes": e.get("break_to_retest_minutes"),
                "trigger_t": e.get("trigger_t"),
                "trigger_labels": e.get("trigger_labels"),
                "trigger_primary": e.get("trigger_primary") or (list(e.get("trigger_labels") or [])[:1] or [None])[0],
                "entry_px": e.get("entry_px"),
                "abs_gap_atr": e.get("abs_gap_atr"),
                "tv_0915_pctl": e.get("tv_0915_pctl"),
                "xs_rank_pct": e.get("xs_rank_pct"),
                "daily_bias": e.get("daily_bias"),
                "vwap_trigger": e.get("vwap_trigger"),
                "tv_sequence": e.get("tv_sequence"),
                "nearest_target": e.get("nearest_target"),
                "structural_risk_px": e.get("structural_risk_px"),
                "reward_px": e.get("reward_px"),
                "reward_to_risk": e.get("reward_to_risk"),
                "room_class": e.get("room_class"),
                "already_extended": e.get("already_extended"),
                "v1_dev_reused": False,
            }
            for e in sample
        ],
    }
    body["decision"] = decide(body)
    return body
