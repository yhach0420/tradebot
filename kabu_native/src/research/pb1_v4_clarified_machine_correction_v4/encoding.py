"""Promoted RCA path predicates. Not new strategy concepts. Not 88-searched thresholds.

ONE_BAR_PRIMARY_CAUSE_REFINED = true
Removing the dominant print from n_strong is NOT the V3 followthrough fix.
"""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v4 import LAST_BODY_MIN, TRUE_BODY_FRAC_MIN

# Promoted from Correction V2 RCA reconstruct. Relative path measures, not grid search.
# Saved in the V3 encoding manifest. Do not retune from 88 outcomes.
RCA_MAJORITY_ONE_BAR_SHARE = 0.5  # descriptor: one bar is the majority of opening range
RCA_CLOSE_PROGRESS_UNDO_MAJORITY = 0.5  # middle close gives back a majority of first-bar close progress
RCA_COMPARABLE_OPPOSITE_BODY = 0.50  # middle is itself a committed opposite auction, not a pullback body
RCA_COMPARABLE_OPPOSITE_EXCURSION = 0.35  # middle excursion vs first-leg excursion (same numeric family as TRUE_BODY)
RCA_THIRD_CONTINUATION_NET = 0.70  # third bar net vs first-leg net: new continuation vs leftover dominance
ONE_BAR_SHARE_MAX_RESTORED = False
WEAK_BAR_BODY_MAX_RESTORED = False

POST_DOMINANT_CLASSES = (
    "INITIAL_DISPLACEMENT_ONLY",
    "INITIAL_DISPLACEMENT_WITH_ABSORPTION",
    "INITIAL_DISPLACEMENT_WITH_REAL_FOLLOWTHROUGH",
    "INITIAL_DISPLACEMENT_THEN_COUNTER_AUCTION",
)
COUNTER_CLASSES = (
    "PULLBACK_ORIGINAL_AUCTION_INTACT",
    "MEANINGFUL_COUNTER_AUCTION",
    "TWO_SIDED_COMMITTED_FIGHT",
    "AMBIGUOUS",
)


def _ratio(num: Any, den: Any) -> float | None:
    if not (_finite(num) and _finite(den) and float(den) > 0):
        return None
    return float(num) / float(den)


def _beyond(*, sign: int, px: Any, ref: Any) -> float | None:
    if not (_finite(px) and _finite(ref)):
        return None
    return (float(px) - float(ref)) * float(sign)


def classify_post_dominant(bars: list[dict[str, Any]], *, sign: int) -> dict[str, Any]:
    """FOLLOWTHROUGH is the auction AFTER the dominant initial print.

    POST_DOMINANT_GEOMETRIC_EXTREME_EXTENDED (a nick of the dominant high/low)
    is not POST_DOMINANT_REAL_AUCTION_EXTENSION.
    The dominant print is never followthrough evidence.
    """
    open_bars = list(bars[:3])
    if len(open_bars) < 3:
        return {
            "ok": False,
            "post_dominant_class": "INITIAL_DISPLACEMENT_ONLY",
            "followthrough_real_auction": False,
            "one_bar_without_real_followthrough": False,
        }
    ranges = [float(b["range"]) if _finite(b.get("range")) else -1.0 for b in open_bars]
    if max(ranges) <= 0:
        return {
            "ok": False,
            "post_dominant_class": "INITIAL_DISPLACEMENT_ONLY",
            "followthrough_real_auction": False,
            "one_bar_without_real_followthrough": False,
        }
    di = int(max(range(3), key=lambda i: ranges[i]))
    dom = open_bars[di]
    d_sign = int(dom.get("direction") or 0)
    if d_sign not in (1, -1):
        d_sign = int(sign) if int(sign) in (1, -1) else 1
    share = (max(ranges) / sum(ranges)) if sum(ranges) > 0 else None
    majority = _finite(share) and float(share) > float(RCA_MAJORITY_ONE_BAR_SHARE)
    post = open_bars[di + 1 :]
    d_close = dom.get("c")
    d_ext = dom.get("h") if d_sign > 0 else dom.get("l")
    d_hi, d_lo = dom.get("h"), dom.get("l")
    last_c = post[-1].get("c") if post else d_close
    beyond_close = _beyond(sign=d_sign, px=last_c, ref=d_close)
    post_ext = None
    if post:
        post_ext = (
            max(float(b["h"]) for b in post if _finite(b.get("h")))
            if d_sign > 0
            else min(float(b["l"]) for b in post if _finite(b.get("l")))
        )
    beyond_ext = _beyond(sign=d_sign, px=post_ext, ref=d_ext) if post else None
    inside = True
    if post and _finite(d_hi) and _finite(d_lo):
        for b in post:
            if _finite(b.get("h")) and float(b["h"]) > float(d_hi) + 1e-12:
                inside = False
            if _finite(b.get("l")) and float(b["l"]) < float(d_lo) - 1e-12:
                inside = False
    else:
        inside = bool(post)
    geometric_extreme_extended = bool(beyond_ext is not None and float(beyond_ext) > 0)
    close_prog = bool(beyond_close is not None and float(beyond_close) > 0)
    post_bodies = [float(b["body_over_range"]) if _finite(b.get("body_over_range")) else 0.0 for b in post]
    post_dirs = [int(b.get("direction") or 0) for b in post]
    post_crawl = any(x < float(TRUE_BODY_FRAC_MIN) for x in post_bodies)
    shrinking_leftover = bool(post) and all(_finite(b.get("range")) and float(b["range"]) < float(ranges[di]) for b in post)
    leftover_nick = bool(geometric_extreme_extended and post_crawl and shrinking_leftover)
    post_opp = any(d == -int(d_sign) for d in post_dirs if d != 0)
    # RCA class (promoted). Geometric nick on shrinking leftover is absorption, not followthrough.
    if not post:
        cls = "INITIAL_DISPLACEMENT_ONLY"
    elif post_opp and not close_prog:
        cls = "INITIAL_DISPLACEMENT_THEN_COUNTER_AUCTION"
    elif leftover_nick or ((not geometric_extreme_extended) and inside) or (inside and post_crawl and not close_prog):
        cls = "INITIAL_DISPLACEMENT_WITH_ABSORPTION"
    elif geometric_extreme_extended and close_prog and (not leftover_nick):
        cls = "INITIAL_DISPLACEMENT_WITH_REAL_FOLLOWTHROUGH"
    else:
        cls = "INITIAL_DISPLACEMENT_ONLY"
    real_auction_extension = cls == "INITIAL_DISPLACEMENT_WITH_REAL_FOLLOWTHROUGH"
    one_bar_without = bool(majority and cls != "INITIAL_DISPLACEMENT_WITH_REAL_FOLLOWTHROUGH")
    return {
        "ok": True,
        "dominant_index": di,
        "dominant_direction": d_sign,
        "one_bar_share": share,
        "majority_one_bar": majority,
        "POST_DOMINANT_GEOMETRIC_EXTREME_EXTENDED": geometric_extreme_extended,
        "POST_DOMINANT_REAL_AUCTION_EXTENSION": bool(real_auction_extension and cls == "INITIAL_DISPLACEMENT_WITH_REAL_FOLLOWTHROUGH"),
        "price_merely_stays_inside_dominant_print": bool(inside and not geometric_extreme_extended),
        "leftover_nick": leftover_nick,
        "post_dominant_class": cls,
        "followthrough_real_auction": cls == "INITIAL_DISPLACEMENT_WITH_REAL_FOLLOWTHROUGH",
        "one_bar_without_real_followthrough": one_bar_without,
        "n_strong_not_used_as_followthrough": True,
        "dominant_excluded_from_followthrough_evidence": True,
        "ONE_BAR_PRIMARY_CAUSE_REFINED": True,
    }


def classify_sandwich_counter(bars: list[dict[str, Any]], *, sign: int) -> dict[str, Any]:
    """Pullback vs meaningful counter vs committed fight. Body>=0.35 is not the truth."""
    open_bars = list(bars[:3])
    empty = {
        "is_sandwich": False,
        "semantic_class": None,
        "two_sided_balance": False,
        "middle_only_pullback": False,
    }
    if len(open_bars) < 3:
        return empty
    dirs = [int(b.get("direction") or 0) for b in open_bars]
    if not (dirs[0] in (1, -1) and dirs[1] == -dirs[0] and dirs[2] == dirs[0]):
        return empty
    s0 = int(dirs[0])
    bodies = [float(b["body_over_range"]) if _finite(b.get("body_over_range")) else 0.0 for b in open_bars]
    ranges = [float(b["range"]) if _finite(b.get("range")) else None for b in open_bars]
    nets = [float(b["net"]) if _finite(b.get("net")) else None for b in open_bars]
    first_leg = abs(float(nets[0])) if _finite(nets[0]) else None
    first_exc = abs(float(ranges[0])) if _finite(ranges[0]) else None
    mid_net = abs(float(nets[1])) if _finite(nets[1]) else None
    mid_rng = ranges[1]
    retrace_exc = _ratio(mid_rng, first_exc)
    first_c = open_bars[0].get("c")
    mid_c = open_bars[1].get("c")
    first_o = open_bars[0].get("o")
    undo = None
    if _finite(first_c) and _finite(first_o) and _finite(mid_c):
        first_prog = (float(first_c) - float(first_o)) * float(s0)
        undo_amt = (float(first_c) - float(mid_c)) * float(s0)
        undo = (float(undo_amt) / float(first_prog)) if abs(float(first_prog)) > 1e-12 else None
    undoes = undo is not None and float(undo) >= float(RCA_CLOSE_PROGRESS_UNDO_MAJORITY)
    first_invalid = False
    if _finite(open_bars[0].get("l")) and _finite(open_bars[0].get("h")):
        later_c = [b.get("c") for b in open_bars[1:] if _finite(b.get("c"))]
        if s0 > 0:
            first_invalid = any(float(c) < float(open_bars[0]["l"]) for c in later_c)
        else:
            first_invalid = any(float(c) > float(open_bars[0]["h"]) for c in later_c)
    last_net = abs(float(nets[2])) if _finite(nets[2]) else None
    last_vs_first_net = _ratio(last_net, first_leg)
    intact = (not first_invalid) and (not undoes)
    middle_is_comparable_auction = (
        _finite(retrace_exc)
        and float(retrace_exc) >= float(RCA_COMPARABLE_OPPOSITE_EXCURSION)
        and bodies[1] >= float(RCA_COMPARABLE_OPPOSITE_BODY)
    )
    third_is_new_drive = (
        last_vs_first_net is not None
        and float(last_vs_first_net) >= float(RCA_THIRD_CONTINUATION_NET)
        and bodies[2] >= float(RCA_COMPARABLE_OPPOSITE_BODY)
    )
    middle_only_pullback = bodies[1] < float(TRUE_BODY_FRAC_MIN)
    if intact and not middle_is_comparable_auction:
        sem = "PULLBACK_ORIGINAL_AUCTION_INTACT"
        third_role = "CONTINUATION_AFTER_PULLBACK"
    elif middle_is_comparable_auction and (undoes or first_invalid):
        sem = "TWO_SIDED_COMMITTED_FIGHT"
        third_role = "FINAL_DOMINANCE_AFTER_TWO_SIDED_FIGHT"
    elif middle_is_comparable_auction and intact:
        sem = "MEANINGFUL_COUNTER_AUCTION"
        third_role = "CONTINUATION_AFTER_PULLBACK" if third_is_new_drive else "FINAL_DOMINANCE_AFTER_TWO_SIDED_FIGHT"
    else:
        sem = "AMBIGUOUS"
        third_role = "CONTINUATION_AFTER_PULLBACK" if intact else "FINAL_DOMINANCE_AFTER_TWO_SIDED_FIGHT"
    two_sided = sem in ("MEANINGFUL_COUNTER_AUCTION", "TWO_SIDED_COMMITTED_FIGHT")
    return {
        "is_sandwich": True,
        "semantic_class": sem,
        "third_bar_role": third_role,
        "two_sided_balance": two_sided,
        "middle_only_pullback": bool(middle_only_pullback and intact and not middle_is_comparable_auction),
        "middle_is_comparable_opposite_auction": middle_is_comparable_auction,
        "middle_close_materially_undoes_first_close": undoes,
        "first_directional_auction_remains_structurally_intact": intact,
        "first_directional_auction_invalidated": first_invalid,
        "third_is_new_continuation_drive": third_is_new_drive,
        "counter_excursion_over_first_leg_excursion": retrace_exc,
        "middle_body_over_range": bodies[1],
        "body_035_not_semantic_truth": True,
        "retrace_alone_not_semantic_truth": True,
        "sign_used": s0,
        "drive_sign": int(sign),
    }


def encoding_manifest() -> dict[str, Any]:
    return {
        "ONE_BAR_PRIMARY_CAUSE_REFINED": True,
        "refined_primary_cause": (
            "FOLLOWTHROUGH does not require a genuine post-dominant directional auction. "
            "It can accept geometric extension / close progression inside an absorption or crawl path "
            "as continued directional intent. Excluding the dominant print from n_strong is not the fix."
        ),
        "n_strong_dominant_exclusion_forbidden_as_the_fix": True,
        "ONE_BAR_SHARE_MAX_restored": False,
        "WEAK_BAR_BODY_MAX_restored": False,
        "promoted_rca_predicates": {
            "RCA_MAJORITY_ONE_BAR_SHARE": RCA_MAJORITY_ONE_BAR_SHARE,
            "RCA_CLOSE_PROGRESS_UNDO_MAJORITY": RCA_CLOSE_PROGRESS_UNDO_MAJORITY,
            "RCA_COMPARABLE_OPPOSITE_BODY": RCA_COMPARABLE_OPPOSITE_BODY,
            "RCA_COMPARABLE_OPPOSITE_EXCURSION": RCA_COMPARABLE_OPPOSITE_EXCURSION,
            "RCA_THIRD_CONTINUATION_NET": RCA_THIRD_CONTINUATION_NET,
            "TRUE_BODY_FRAC_MIN_as_crawl_vs_committed": TRUE_BODY_FRAC_MIN,
            "LAST_BODY_MIN": LAST_BODY_MIN,
            "leftover_nick": "geometric new extreme AND a post-dominant crawl bar AND all post ranges < dominant range",
            "POST_DOMINANT_GEOMETRIC_EXTREME_EXTENDED_vs_REAL_AUCTION_EXTENSION": True,
            "middle_is_comparable_opposite_auction": "retrace_exc >= RCA_COMPARABLE_OPPOSITE_EXCURSION AND middle body >= RCA_COMPARABLE_OPPOSITE_BODY",
            "middle_close_materially_undoes_first_close": "undo of first close progress >= RCA_CLOSE_PROGRESS_UNDO_MAJORITY",
            "STALE_committed_opposite_non_contracting": (
                "ACTIVE death STALE_RANGE_RESOLUTION: opposite 5m with body >= RCA_COMPARABLE_OPPOSITE_BODY "
                "and range not contracted vs previous 5m. Not N-bar expiry. Not location-gated."
            ),
        },
        "chosen_from_88_grid": False,
        "LAST_BODY_MIN_used_as_new_share_cutoff": False,
        "v4_delta_a": "POST_DOMINANT_PATH_STATE persists on TRUE/FAILED/NO_VALID without changing SEED/DIR/ACTIVE/LOCATION/THESIS/E0/E1",
        "v4_delta_b": (
            "FAILED_PROBE_PENDING is non-terminal. REAL_BREAKOUT_EXTENSION / MEANINGFUL_DIRECTIONAL_EXTENSION clear it. "
            "FAILED_BREAK_REACCEPTED confirms leftover containment + existing PROGRESS_HEAVY_OVERLAP after opposite response. "
            "COMMITTED_OPPOSITE_NON_CONTRACTING STALE_RANGE_RESOLUTION remains. No new numeric cutoff. No any-overlap death."
        ),
        "v3_thresholds_untouched": {"PROGRESS_TINY_EXTREME_FRAC": 0.20, "PROGRESS_HEAVY_OVERLAP": 0.70, "TRUE_BODY_FRAC_MIN": 0.35},
    }
