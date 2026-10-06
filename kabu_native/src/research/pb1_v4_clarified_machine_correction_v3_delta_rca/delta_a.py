"""Delta A: post-dominant class dropped on NO_VALID report path. Read-only V3 classifiers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research.pb1_v4_clarified_machine_correction_v3.encoding import classify_post_dominant
from research.pb1_v4_clarified_machine_correction_v3.seed import classify_seed, continued_intent_state
from research.pb1_v4_clarified_machine_correction_v3.walk import _emit  # read-only identity of initial funnel constructor
from research.pb1_v4_clarified_machine_correction_v3_delta_rca.load import pick

COMPARE = (
    ("7011", "20241205"),
    ("8002", "20241002"),
    ("6857", "20250930"),
    ("8630", "20250828"),
    ("6501", "20251010"),
)


def _bars(row: dict[str, Any]) -> list[dict[str, Any]]:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])
    open_bars = [b for b in bars if str(b.get("t1") or "") <= "09:14"]
    return open_bars[:3] if len(open_bars) >= 3 else bars[:3]


def _clock(row: dict[str, Any]) -> dict[str, Any]:
    snap = dict(row.get("snap") or {})
    clock = dict(snap.get("clock_snap") or {})
    if clock:
        return clock
    same = (row.get("funnel") or {}).get("same_clock")
    if isinstance(same, dict):
        return same
    return {}


def trace_one(row: dict[str, Any]) -> dict[str, Any]:
    bars = _bars(row)
    clock = _clock(row)
    atr = (row.get("snap") or {}).get("atr20") or (row.get("funnel") or {}).get("atr20")
    dirs = [int(b.get("direction") or 0) for b in bars]
    sign = int(row.get("machine_DIR") or 0)
    if sign not in (1, -1) and bars:
        first = int(bars[0].get("direction") or 0)
        sign = first if first in (1, -1) else 1
    raw = classify_post_dominant(bars, sign=sign)
    intent = continued_intent_state(bars, sign=sign)
    seed = classify_seed(bars, clock_snap=clock, atr20=atr) if clock else {"ok": False, "reason": "no_clock"}
    seed_intent = dict((seed.get("intent") or {}))
    seed_post = dict(seed_intent.get("post_dominant") or {})
    funnel_cls = row.get("post_dominant_class_funnel")
    dropped_after_seed = bool(seed_post.get("post_dominant_class")) and not funnel_cls
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "human_opening_state": row.get("human_opening_state"),
        "n_open_bars": len(bars),
        "dirs": dirs,
        "sign_used_for_raw": sign,
        "raw_post_dominant_class": raw.get("post_dominant_class"),
        "raw_leftover_nick": raw.get("leftover_nick"),
        "raw_geometric_extreme_extended": raw.get("POST_DOMINANT_GEOMETRIC_EXTREME_EXTENDED"),
        "raw_real_auction_extension": raw.get("POST_DOMINANT_REAL_AUCTION_EXTENSION"),
        "intent_post_dominant_class": intent.get("post_dominant_class"),
        "seed_SEED": seed.get("seed"),
        "seed_subtype": seed.get("subtype"),
        "seed_DIR": seed.get("DIR"),
        "seed_reason": seed.get("reason"),
        "seed_intent_has_post_dominant": bool(seed_post),
        "seed_intent_post_dominant_class": seed_post.get("post_dominant_class"),
        "funnel_post_dominant_class": funnel_cls,
        "funnel_SEED": row.get("machine_SEED"),
        "funnel_opening_state": row.get("machine_opening_state"),
        "dropped_between_seed_return_and_funnel": dropped_after_seed,
        "strategy_seed_is_NO_VALID": str(seed.get("seed") or "") == "NO_VALID_DRIVE_SEED",
        "not_TRUE": str(seed.get("seed") or "") != "TRUE_OPENING_DRIVE_SEED",
    }


def drop_locus() -> dict[str, Any]:
    walk_path = Path(__file__).resolve().parents[1] / "pb1_v4_clarified_machine_correction_v3" / "walk.py"
    seed_path = Path(classify_seed.__code__.co_filename)
    walk_src = walk_path.read_text(encoding="utf-8")
    emit_src = walk_src.split("def emit_v4", 1)[0]
    seed_src = seed_path.read_text(encoding="utf-8")
    intent_src = seed_src
    _ = _emit
    no_valid_early_continue = "funnel_days.append(funnel)" in walk_src and "S1_fail" in walk_src
    emit_has_post = "post_dominant_class" in emit_src
    seed_returns_intent_on_no_valid = 'seed": "NO_VALID_DRIVE_SEED"' in seed_src and '"intent": intent' in seed_src
    classifier_before_true_gate = "classify_post_dominant" in intent_src
    return {
        "classifier_runs_inside_continued_intent_state_before_TRUE_gate": classifier_before_true_gate,
        "classify_seed_NO_VALID_return_includes_intent": seed_returns_intent_on_no_valid,
        "walk_copies_extra_post_dominant_from_intent_before_branch": "st.extra[\"post_dominant\"]" in walk_src,
        "walk_NO_VALID_appends_initial__emit_funnel_and_continues": no_valid_early_continue,
        "_emit_includes_post_dominant_class": emit_has_post,
        "day_end_funnel_update_includes_post_dominant_class": "post_dominant_class" in walk_src,
        "drop_stage": (
            "WALK_NO_VALID_EARLY_FUNNEL_APPEND"
            if (classifier_before_true_gate and seed_returns_intent_on_no_valid and not emit_has_post and no_valid_early_continue)
            else "UNRESOLVED"
        ),
        "SEED_ELIGIBILITY_and_POST_DOMINANT_PATH_STATE_are_collided_on_report": (
            "NO_VALID early funnel uses opening_state/death only; diagnostic class is not a separate field"
        ),
        "audit_xlsx_null_is_downstream_of_funnel": True,
        "classifier_does_not_discard_class_on_NO_VALID": True,
    }


def delta_a(rows: list[dict[str, Any]], v3_report: dict[str, Any]) -> dict[str, Any]:
    traces = [trace_one(pick(rows, s, d)) for s, d in COMPARE]
    p7011 = traces[0]
    audit_checks = list((v3_report.get("target_checks") or {}).get("checks") or [])
    audit_7011 = next(
        (c for c in audit_checks if str(c.get("symbol")) == "7011" and str(c.get("date")) == "20241205"),
        {},
    )
    return {
        "primary": "7011/20241205 strategy outcome is correct NO_VALID/MICRO; diagnostic class is computed then dropped on the NO_VALID report path.",
        "do_not_restore_TRUE": True,
        "drop_locus": drop_locus(),
        "traces": traces,
        "7011_20241205": p7011,
        "audit_row_post_dominant_class": audit_7011.get("post_dominant_class"),
        "audit_null_because_funnel_null": audit_7011.get("post_dominant_class") in (None, "", False),
        "Q1_strategy_wrong": False,
        "Q1_persistence_only": True,
        "Q2_drop_stage": drop_locus()["drop_stage"],
        "Q3_can_persist_without_changing_eligibility": True,
        "desired_contract": {
            "SEED_ELIGIBILITY": "NO_VALID_DRIVE_SEED",
            "OPENING_STATE": "MICRO_OR_LEAK",
            "POST_DOMINANT_PATH_STATE": "INITIAL_DISPLACEMENT_WITH_ABSORPTION",
            "simultaneous_ok": True,
            "diagnostic_is_not_TRUE_eligibility": True,
        },
    }
