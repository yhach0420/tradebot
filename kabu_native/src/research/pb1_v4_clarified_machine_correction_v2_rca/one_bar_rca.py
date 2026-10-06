"""Post-dominant path after the initial print. No 0.55/0.20 restore. No share search."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v2.seed import continued_intent_state
from research.pb1_v4_clarified_machine_correction_v2_rca.reconstruct import pick

COMPARE = (
    ("7011", "20241205"),
    ("8002", "20241002"),
    ("6787", "20250214"),
    ("9101", "20250807"),
    ("6501", "20250612"),
    ("7012", "20250604"),
    ("6857", "20250930"),
    ("8630", "20250828"),
    ("6501", "20251010"),
)


def _bar_pack(b: dict[str, Any], clocks: list[dict[str, Any]], i: int) -> dict[str, Any]:
    med = (clocks[i] or {}).get("median") if i < len(clocks) else None
    rng = b.get("range")
    ratio = (float(rng) / float(med)) if _finite(rng) and _finite(med) and float(med) > 0 else None
    return {
        "t1": b.get("t1"),
        "dir": int(b.get("direction") or 0),
        "o": b.get("o"),
        "h": b.get("h"),
        "l": b.get("l"),
        "c": b.get("c"),
        "range": rng,
        "body_over_range": b.get("body_over_range"),
        "net": b.get("net"),
        "same_clock_range": ratio,
    }


def _beyond(*, sign: int, px: Any, ref: Any) -> float | None:
    if not (_finite(px) and _finite(ref)):
        return None
    return (float(px) - float(ref)) * float(sign)


def reconstruct_post_dominant(row: dict[str, Any]) -> dict[str, Any]:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])[:3]
    clocks = list((snap.get("clock_snap") or {}).get("same_clock") or [])
    if len(bars) < 3:
        return {"ok": False, "symbol": row.get("symbol"), "date": row.get("date")}
    ranges = [float(b["range"]) if _finite(b.get("range")) else -1.0 for b in bars]
    if max(ranges) <= 0:
        return {"ok": False, "symbol": row.get("symbol"), "date": row.get("date")}
    di = int(max(range(3), key=lambda i: ranges[i]))
    dom = bars[di]
    sign = int(dom.get("direction") or 0)
    if sign not in (1, -1):
        sign = int(row.get("machine_DIR") or 1) or 1
    intent = continued_intent_state(bars, sign=sign)
    packed = [_bar_pack(b, clocks, i) for i, b in enumerate(bars)]
    post = bars[di + 1 :]
    post_p = packed[di + 1 :]
    d_close = dom.get("c")
    d_ext = dom.get("h") if sign > 0 else dom.get("l")
    d_hi, d_lo = dom.get("h"), dom.get("l")
    last_c = post[-1].get("c") if post else d_close
    post_net = _beyond(sign=sign, px=last_c, ref=d_close)
    post_ext = None
    if post:
        ext_now = max(float(b["h"]) for b in post if _finite(b.get("h"))) if sign > 0 else min(
            float(b["l"]) for b in post if _finite(b.get("l"))
        )
        post_ext = ext_now
    beyond_close = post_net
    beyond_ext = _beyond(sign=sign, px=post_ext, ref=d_ext) if post else None
    inside = True
    if post and _finite(d_hi) and _finite(d_lo):
        for b in post:
            if _finite(b.get("h")) and float(b["h"]) > float(d_hi) + 1e-12:
                inside = False
            if _finite(b.get("l")) and float(b["l"]) < float(d_lo) - 1e-12:
                inside = False
    else:
        inside = bool(post)
    new_ext = bool(beyond_ext is not None and float(beyond_ext) > 0)
    close_prog = bool(beyond_close is not None and float(beyond_close) > 0)
    post_bodies = [float(b["body_over_range"]) if _finite(b.get("body_over_range")) else 0.0 for b in post]
    post_committed = any(x >= 0.35 for x in post_bodies)
    post_opp = any(int(b.get("direction") or 0) == -sign for b in post)
    post_crawl = any(x < 0.35 for x in post_bodies)
    shrinking_leftover = bool(post) and all(_finite(b.get("range")) and float(b["range"]) < float(ranges[di]) for b in post)
    leftover_nick = bool(new_ext and post_crawl and shrinking_leftover)
    # Causal classes. Not share>X / body>Y.
    if not post:
        cls = "INITIAL_DISPLACEMENT_ONLY"
    elif post_opp and not close_prog:
        cls = "INITIAL_DISPLACEMENT_THEN_COUNTER_AUCTION"
    elif leftover_nick or ((not new_ext) and inside) or (inside and post_crawl and not close_prog):
        cls = "INITIAL_DISPLACEMENT_WITH_ABSORPTION"
    elif new_ext and close_prog and (not leftover_nick):
        cls = "INITIAL_DISPLACEMENT_WITH_REAL_FOLLOWTHROUGH"
    else:
        cls = "INITIAL_DISPLACEMENT_ONLY"
    share = (max(ranges) / sum(ranges)) if sum(ranges) > 0 else None
    n_strong = int(intent.get("n_strong_bodies") or 0)
    n_strong_ex_dom = int(sum(1 for i, b in enumerate(bars) if i != di and _finite(b.get("body_over_range")) and float(b["body_over_range"]) >= 0.35))
    follow_uses_dom = bool(intent.get("followthrough_continues_auction")) and n_strong_ex_dom == 0 and n_strong >= 1
    return {
        "ok": True,
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "human_opening_state": row.get("human_opening_state"),
        "human_pattern": row.get("human_pattern"),
        "machine_SEED": row.get("machine_SEED"),
        "machine_THESIS_READY": row.get("machine_THESIS_READY"),
        "one_bar_share": share,
        "majority_one_bar": bool(share is not None and float(share) > 0.5),
        "dominant_index": di,
        "DOMINANT_INITIAL_AUCTION": packed[di],
        "POST_DOMINANT_BAR_1": post_p[0] if post_p else None,
        "POST_DOMINANT_BAR_2": post_p[1] if len(post_p) > 1 else None,
        "dominant_direction": sign,
        "dominant_excursion": packed[di].get("range"),
        "dominant_close": d_close,
        "post_dominant_cumulative_net": post_net,
        "post_dominant_close_progression": close_prog,
        "post_dominant_new_extreme": new_ext,
        "distance_beyond_dominant_close": beyond_close,
        "distance_beyond_dominant_extreme": beyond_ext,
        "post_dominant_body_commitment": post_bodies,
        "post_dominant_same_clock_range": [p.get("same_clock_range") for p in post_p],
        "price_merely_stays_inside_dominant_print": bool(inside and not new_ext),
        "price_truly_extends_original_auction": bool(new_ext and close_prog),
        "machine_followthrough": bool(intent.get("followthrough_continues_auction")),
        "machine_n_strong_includes_dominant": n_strong >= 1,
        "n_strong_excluding_dominant": n_strong_ex_dom,
        "followthrough_uses_dominant_print_as_own_evidence": True,
        "this_row_needed_dominant_to_satisfy_n_strong": follow_uses_dom,
        "encoding_n_strong_includes_all_three_bars": True,
        "post_dominant_class": cls,
        "machine_one_bar_without_followthrough": bool(intent.get("one_bar_dominated_without_followthrough")),
        "restored_055_020": False,
    }


def one_bar_rca(rows: list[dict[str, Any]]) -> dict[str, Any]:
    all_heavy = []
    for r in rows:
        rec = reconstruct_post_dominant(r)
        if rec.get("majority_one_bar"):
            all_heavy.append(rec)
    compare = [reconstruct_post_dominant(pick(rows, s, d)) for s, d in COMPARE]
    m7011 = compare[0]
    return {
        "primary": m7011,
        "compare_set": compare,
        "one_bar_heavy_n": len(all_heavy),
        "one_bar_heavy": all_heavy,
        "followthrough_incorrectly_counts_dominant_print": True,
        "7011_post_dominant_class": m7011.get("post_dominant_class"),
        "numeric_share_threshold_required": "no",
        "restored_055_020": False,
        "threshold_search": False,
        "causal_distinction": (
            "FOLLOWTHROUGH is what happens AFTER the dominant initial print. "
            "A continuing directional auction makes a new extreme and a close beyond the dominant close "
            "in the original direction. An ordinary/crawling market after a large print stays inside that print: "
            "later bodies on shrinking bars are not a new auction. The dominant print cannot be its own followthrough."
        ),
        "v2_already_says_large_bar_plus_crawl_is_not_TRUE": True,
        "do_not_force_human_TRUE_to_machine_TRUE": True,
    }
