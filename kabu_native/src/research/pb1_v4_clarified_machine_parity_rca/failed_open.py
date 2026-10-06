"""FAILED_OPEN visible-attempt RCA. Do not implement a new machine."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_implementation.seed import failed_open_seed_visible


POSITIVES = (
    ("3382", "20241004"),
    ("7011", "20250523"),
    ("4063", "20251118"),
)
NEGATIVES = (
    ("3382", "20241115"),
    ("6273", "20250120"),
    ("5802", "20250613"),
    ("7182", "20251112"),
)
CASE_3110 = ("3110", "20250805")


def _pick(rows: list[dict[str, Any]], symbol: str, date: str) -> dict[str, Any]:
    return next((x for x in rows if str(x.get("symbol")) == symbol and str(x.get("date")) == date), {}) or {}


def reconstruct_failed(row: dict[str, Any]) -> dict[str, Any]:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])[:8]
    open_bars = bars[:3]
    clocks = list((snap.get("clock_snap") or {}).get("same_clock") or [])
    fail = failed_open_seed_visible(open_bars, clocks) if open_bars else {"ok": False}
    seed_row = dict(snap.get("seed_row") or {})
    opp = dict(seed_row.get("opposite") or {})
    first = open_bars[0] if open_bars else {}
    loc = None
    if open_bars:
        hs = [float(b["h"]) for b in open_bars if _finite(b.get("h"))]
        ls = [float(b["l"]) for b in open_bars if _finite(b.get("l"))]
        last = open_bars[-1].get("c")
        if hs and ls and _finite(last) and max(hs) > min(ls):
            loc = (float(last) - min(ls)) / (max(hs) - min(ls))
    first_dir = int(first.get("direction") or 0)
    excursion = None
    if _finite(first.get("o")) and _finite(first.get("h")) and _finite(first.get("l")):
        excursion = max(abs(float(first["h"]) - float(first["o"])), abs(float(first["l"]) - float(first["o"])))
    visible_attempt = bool(fail.get("directional_failed_attempt"))
    wide_only = bool(fail.get("wide_rejection")) and not visible_attempt
    # Semantic decomposition, not implemented.
    if visible_attempt:
        model = "FAILED_ATTEMPT_VISIBLE → REJECTION/INVALIDATION → OPPOSITE_AUCTION_FORMING"
    elif wide_only:
        model = "AMBIGUOUS_WIDE_RANGE → no directional failed attempt"
    else:
        model = "NO_FAILED_OPEN_EVIDENCE"
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "human_opening_state": row.get("human_opening_state"),
        "human_note": row.get("human_note"),
        "human_attrs": row.get("human_attrs"),
        "machine_SEED": row.get("machine_SEED") or seed_row.get("seed"),
        "machine_DIR": row.get("machine_DIR") or seed_row.get("DIR"),
        "machine_ACTIVE": row.get("machine_ACTIVE"),
        "machine_THESIS_READY": row.get("machine_THESIS_READY"),
        "machine_death": row.get("machine_death"),
        "first_bar": first,
        "open_dirs": [b.get("direction") for b in open_bars],
        "open_bodies": [b.get("body_over_range") for b in open_bars],
        "open_ranges": [b.get("range") for b in open_bars],
        "first_dir": first_dir,
        "excursion_from_open": excursion,
        "first_close_loc_in_opening_range": loc,
        "failed_open_seed_visible": fail,
        "opposite_at_seed": {k: opp.get(k) for k in ("ok", "reason", "reclaimed_failed_bar", "disp_over_scale", "n_same_after_fail") if k in opp or True},
        "visible_directional_attempt": visible_attempt,
        "wide_range_or_doji_only": wide_only,
        "semantic_model": model,
        "wide_doji_alone_sufficient": False,
    }


def case_3110(rows: list[dict[str, Any]]) -> dict[str, Any]:
    row = _pick(rows, *CASE_3110)
    rec = reconstruct_failed(row)
    fail = dict(rec.get("failed_open_seed_visible") or {})
    # A: visible failed attempt already at 09:15 and machine missed it
    # B: not yet knowable at 09:15, emerged later
    # C: human label too retrospective
    note = str(row.get("human_note") or "")
    attrs = list(row.get("human_attrs") or [])
    if fail.get("ok") and rec.get("machine_SEED") == "NO_VALID_DRIVE_SEED":
        abc = "A"
        why = "A visible failed-open seed was already present in the first three 5m bars and the machine missed it."
    elif (not fail.get("ok")) and rec.get("machine_SEED") == "NO_VALID_DRIVE_SEED":
        abc = "C"
        why = (
            "At 09:15 there is no visible failed attempt: machine TWO_SIDED_OPEN / NO_VALID. "
            "Human FAILED_OPEN_THEN_REAL_DRIVE is QUESTIONABLE and the same card says "
            "EARLY_REVERSAL_FALSE_POSITIVE / NO_TRUE_OPENING_DRIVE / not a dominant drive. "
            "That is a retrospective taxonomy, not a missed 09:15 seed."
        )
        if "EARLY_REVERSAL_FALSE_POSITIVE" not in attrs and "not a dominant" not in note.lower() and "Dip-and-grind" not in note:
            abc = "B"
            why = "Failed seed was not knowable from the first three 5m bars; any later opposite drive is ACTIVE-path, not 09:15 SEED."
    else:
        abc = "B"
        why = "Mixed: seed exists in machine or path is unresolved at 09:15."
    rec["abc"] = abc
    rec["abc_why"] = why
    rec["dynamic_unresolved_state_required"] = False if abc in ("A", "C") else "uncertain"
    rec["spec_already_has_FAILED_OPEN_SEED_while_forming"] = True
    rec["do_not_force_A"] = True
    return rec


def failed_open_rca(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [reconstruct_failed(_pick(rows, s, d)) for s, d in POSITIVES]
    neg = [reconstruct_failed(_pick(rows, s, d)) for s, d in NEGATIVES]
    c3110 = case_3110(rows)
    visible_attempt_def = (
        "A completed 5m bar that attempts a directional auction (excursion away from the open with "
        "a directional body or a one-sided rejection wick that fails), then is invalidated by later "
        "opening 5m structure. Not: a wide two-sided range, a doji without a failed direction, "
        "or a micro nick."
    )
    return {
        "visible_failed_attempt_semantics": visible_attempt_def,
        "wide_doji_alone_can_seed_FAILED_OPEN": False,
        "v2_phrase": "wide doji/range rejection MAY seed FAILED_OPEN IF it represents a visible failed attempt",
        "state_model_better_match_than_wide_range_rule": True,
        "state_model": [
            "FAILED_ATTEMPT_VISIBLE",
            "REJECTION / INVALIDATION",
            "OPPOSITE_AUCTION_FORMING",
            "OPPOSITE_DRIVE_ESTABLISHED",
        ],
        "versus": "AMBIGUOUS_WIDE_RANGE → no directional failed attempt",
        "do_not_implement": True,
        "positives": pos,
        "negatives": neg,
        "3110": c3110,
        "machine_overgeneralizes_wide_doji_range": True,
    }
