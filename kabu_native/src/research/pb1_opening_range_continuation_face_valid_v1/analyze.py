"""Face-validity summaries. No PnL. No matching. No threshold search from returns."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from research.pb1_opening_range_continuation_face_valid_v1 import (
    CASE_NOT_REP,
    CASE_READY,
    CASE_REBUILD,
    FALSE_BREAK_MERGED,
    IN_PLAY_ABS_GAP_ATR,
    IN_PLAY_TV_WINDOW_PCTL,
    IN_PLAY_XS_RANK_MAX,
    NEXT_FIX,
    NEXT_PATH_TEST,
    NEXT_STOP,
)
from research.pb1_opening_range_continuation_face_valid_v1.charts import sampling_procedure
from research.pb1_opening_range_continuation_face_valid_v1.definitions import STATE_MACHINE_TEXT, machine_sha256


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
    return {
        "purpose": "descriptive measures of genuine activity; not a threshold search from future performance",
        "predeclared_tags": {
            "abs_gap_atr": IN_PLAY_ABS_GAP_ATR,
            "tv_0915_pctl": IN_PLAY_TV_WINDOW_PCTL,
            "xs_rank_pct_max": IN_PLAY_XS_RANK_MAX,
            "rule": "in_play if ANY half-space holds",
            "outcome_tuned": False,
        },
        "or_complete_symbol_days": len(or_ok),
        "abs_gap_atr": pct_summary([r.get("abs_gap_atr") for r in or_ok]),
        "tv_0915": pct_summary([r.get("tv_0915") for r in or_ok]),
        "tv_0915_pctl": pct_summary([r.get("tv_0915_pctl") for r in or_ok]),
        "xs_rank_pct": pct_summary([r.get("xs_rank_pct") for r in or_ok]),
        "in_play_share": float(sum(1 for r in or_ok if r.get("in_play")) / len(or_ok)) if or_ok else None,
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
        "reward_space_plausible_n": int(sum(1 for e in events if e.get("reward_space_plausible"))),
        "already_extended_n": int(sum(1 for e in events if e.get("already_extended"))),
        "follow_through_n": int(sum(1 for e in events if e.get("follow_through"))),
        "counts": counts,
        "false_break_merged": FALSE_BREAK_MERGED,
    }


def decide(body: dict[str, Any]) -> dict[str, Any]:
    human = dict(body.get("human") or {})
    n = int(human.get("reviewed_n") or 0)
    sample_n = int(human.get("sample_n") or body.get("sample_n") or 0)
    clear = int(human.get("CLEAR_INTENDED_SETUP") or 0)
    share = float(clear / n) if n else 0.0
    representable = bool(body.get("or15_representable", True))
    same_bar = int(body.get("same_bar_entry_n") or 0)
    false_break = int(body.get("false_break_n") or 0)
    or_mod = int(body.get("or_modified_after_freeze_n") or 0)
    future = int(body.get("future_outcome_n") or 0)
    setup_n = int((body.get("events_summary") or {}).get("setup_n") or 0)
    if (not representable) or setup_n <= 0:
        verd = CASE_NOT_REP
        nxt = NEXT_STOP
        reason = "or15_continuation_not_constructible_from_native_1m"
    elif same_bar or false_break or or_mod or future:
        verd = CASE_REBUILD
        nxt = NEXT_FIX
        reason = "causal_invariant_broken"
    elif n < 12:
        verd = CASE_REBUILD
        nxt = NEXT_FIX
        reason = "blinded_review_incomplete"
    elif share < 0.50:
        verd = CASE_REBUILD
        nxt = NEXT_FIX
        reason = "clear_intended_share_below_half"
    else:
        verd = CASE_READY
        nxt = NEXT_PATH_TEST
        reason = "face_valid_or_continuation_recognized"
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "reason": reason,
        "sample_n": sample_n,
        "reviewed_n": n,
        "CLEAR_INTENDED_SETUP": clear,
        "QUESTIONABLE": int(human.get("QUESTIONABLE") or 0),
        "NOT_INTENDED_SETUP": int(human.get("NOT_INTENDED_SETUP") or 0),
        "clear_share": share if n else None,
        "most_common_semantic_failure": human.get("most_common_semantic_failure"),
        "future_outcome_used": False,
        "outcome_based_threshold_choice": False,
        "pnl_test": False,
        "matching_run": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "false_break_merged": False,
        "pb2_started": False,
        "pb3_started": False,
        "submit_cancel_live": "0/0/0",
        "SAME_BAR_ENTRY_N": same_bar,
        "new_strategy_run": False,
        "complete_strategy_not_run": True,
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
    body = {
        "or15_representable": True,
        "state_machine": STATE_MACHINE_TEXT,
        "MACHINE_SHA256": machine_sha256(),
        "in_play": inplay,
        "or15_freeze": {
            "bars": "09:00-09:14 completed 1m",
            "known_from": "09:15 after 09:14 completes",
            "later_modification": False,
            "or_modified_after_freeze_n": int(walked.get("or_modified_after_freeze_n") or 0),
        },
        "valid_break": "completed 1m close strictly beyond OR on the opening-impulse side, by 10:00; wick-only is recorded separately and is not a valid continuation break",
        "first_retest": "after at least one fully-away bar on the trend side, the first later bar whose range returns to the already-known OR boundary",
        "hold": "first retest bar closes still on the trend side; a close back through kills the episode and is not converted to false-break reversal",
        "triggers": events_summary.get("trigger_primary"),
        "structural_invalidation": "acceptance back inside OR15 through the defended boundary; default reference is the first-retest extreme, not the opposite OR bound; VWAP persisted as location",
        "target_space": "PDH/PDL, PDC, D5H/D5L, daily SMA25/75 if ahead, VWAP if ahead; not optimized",
        "vwap_role": "location diagnostic at break/retest/trigger (trend_side/testing/opposed); not required",
        "participation": "opening impulse vs retest vs trigger clock-percentile TV; sequence labeled, not gated",
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
        "sample": [
            {
                "sample_id": e.get("sample_id"),
                "symbol": e.get("symbol"),
                "date": e.get("date"),
                "block": e.get("block"),
                "direction": e.get("direction"),
                "in_play": e.get("in_play"),
                "break_t": e.get("break_t"),
                "retest_t": e.get("retest_t"),
                "trigger_t": e.get("trigger_t"),
                "trigger_labels": e.get("trigger_labels"),
                "trigger_primary": e.get("trigger_primary") or (list(e.get("trigger_labels") or [])[:1] or [None])[0],
                "abs_gap_atr": e.get("abs_gap_atr"),
                "tv_0915_pctl": e.get("tv_0915_pctl"),
                "xs_rank_pct": e.get("xs_rank_pct"),
                "daily_bias": e.get("daily_bias"),
                "vwap_trigger": e.get("vwap_trigger"),
                "tv_sequence": e.get("tv_sequence"),
                "nearest_target": e.get("nearest_target"),
                "reward_to_risk": e.get("reward_to_risk"),
                "already_extended": e.get("already_extended"),
            }
            for e in sample
        ],
    }
    body["decision"] = decide(body)
    return body
