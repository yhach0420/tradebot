"""ACTIVE progress event log. No numeric cutoff frozen. No future outcome."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_implementation import REPEATED_NO_EXPANSION_N, UNWIND_FRAC
from research.pb1_v4_clarified_machine_implementation.active import classify_active_loss, opening_net
from research.pb1_v4_clarified_machine_implementation.location import five_m_left

LATE_FOCUS = (
    ("6871", "20250922"),
    ("4519", "20250826"),
    ("5801", "20251022"),
    ("6501", "20250724"),
    ("9501", "20250311"),
    ("8031", "20250225"),
    ("7741", "20250314"),
    ("8630", "20250911"),
    ("6963", "20241002"),
)


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    if not (_finite(a.get("h")) and _finite(a.get("l")) and _finite(b.get("h")) and _finite(b.get("l"))):
        return None
    lo = max(float(a["l"]), float(b["l"]))
    hi = min(float(a["h"]), float(b["h"]))
    span = max(float(a["h"]), float(b["h"])) - min(float(a["l"]), float(b["l"]))
    if span <= 0:
        return None
    return max(0.0, hi - lo) / span


def classify_reset_qualitative(*, delta: float, rng: Any, close_prog: Any, overlap: Any, body: Any, range_vs_prior: Any) -> str:
    """No numeric cutoff search. Qualitative labels from bar structure."""
    tiny_vs_bar = _finite(rng) and float(rng) > 0 and abs(float(delta)) <= 0.20 * float(rng)
    close_stalled = (not _finite(close_prog)) or abs(float(close_prog)) < abs(float(delta)) * 0.25
    heavy_overlap = _finite(overlap) and float(overlap) >= 0.70
    contracted = _finite(range_vs_prior) and float(range_vs_prior) < 1.0
    weak_body = _finite(body) and float(body) < 0.25
    if (not tiny_vs_bar) and _finite(close_prog) and abs(float(close_prog)) > 0 and (not heavy_overlap) and not contracted:
        return "REAL_BREAKOUT_EXTENSION" if _finite(range_vs_prior) and float(range_vs_prior) > 1.0 else "MEANINGFUL_DIRECTIONAL_EXTENSION"
    if heavy_overlap and (tiny_vs_bar or close_stalled):
        return "RANGE_DRIFT_EXTREME"
    if tiny_vs_bar or (close_stalled and weak_body):
        return "MARGINAL_EXTREME_ONLY"
    return "MEANINGFUL_DIRECTIONAL_EXTENSION"


def progress_log(row: dict[str, Any]) -> dict[str, Any]:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])
    sign = int((snap.get("seed_row") or {}).get("DIR") or row.get("machine_DIR") or 0)
    if sign not in (1, -1) or len(bars) < 3:
        return {"ok": False, "reason": "no_sign_or_bars", "events": []}
    clock = dict(snap.get("clock_snap") or {})
    baseline = clock.get("mean_clock_median")
    atr = snap.get("atr20")
    or_high, or_low = snap.get("or_high"), snap.get("or_low")
    open_0900 = bars[0].get("o")
    seed_c = bars[2].get("c")
    drive_ext = None
    peak = None
    no_exp = 0
    left = False
    recross = 0
    both = False
    events = []
    death = None
    death_t = None
    would_stale_without_marginal = False
    counterfactual_stall = 0
    counterfactual_stale_t = None
    prior = None
    gross = 0.0
    for b in bars:
        t1 = str(b.get("t1") or "")[:5]
        if t1 < "09:14":
            continue
        h, l, c = b.get("h"), b.get("l"), b.get("c")
        if not (_finite(h) and _finite(l) and _finite(c)):
            continue
        disp = opening_net(sign=sign, open_0900=open_0900, close_now=c)
        if disp is not None and (peak is None or float(disp) > float(peak)):
            peak = float(disp)
        retrace = (float(peak) - float(disp)) if peak is not None and disp is not None else None
        if t1 > "09:14" and _finite(b.get("range")):
            gross += float(b["range"])
        if _finite(or_high) and _finite(or_low) and five_m_left(sign=sign, bar=b, or_high=float(or_high), or_low=float(or_low)):
            left = True
        if left and _finite(or_high) and _finite(or_low) and float(or_low) <= float(c) <= float(or_high):
            recross += 1
        if _finite(or_high) and _finite(or_low) and float(h) >= float(or_high) and float(l) <= float(or_low):
            both = True
        ext = float(h) if sign > 0 else float(l)
        stall_before = no_exp
        new_ext = False
        delta = None
        kind = None
        prev_ext = drive_ext
        if drive_ext is None:
            drive_ext = ext
            no_exp = 0
        else:
            progressed = (sign > 0 and ext > float(drive_ext)) or (sign < 0 and ext < float(drive_ext))
            if progressed:
                delta = abs(ext - float(drive_ext))
                new_ext = True
                close_prog = (float(c) - float(prior["c"])) * float(sign) if prior and _finite(prior.get("c")) else None
                ov = _overlap(prior, b) if prior else None
                rng_vs = (float(b["range"]) / float(prior["range"])) if prior and _finite(b.get("range")) and _finite(prior.get("range")) and float(prior["range"]) > 0 else None
                kind = classify_reset_qualitative(
                    delta=float(delta),
                    rng=b.get("range"),
                    close_prog=close_prog,
                    overlap=ov,
                    body=b.get("body_over_range"),
                    range_vs_prior=rng_vs,
                )
                if kind in ("REAL_BREAKOUT_EXTENSION", "MEANINGFUL_DIRECTIONAL_EXTENSION"):
                    counterfactual_stall = 0
                else:
                    counterfactual_stall += 1
                drive_ext = ext
                no_exp = 0
            else:
                no_exp += 1
                counterfactual_stall += 1
        if t1 > "09:14" and counterfactual_stall >= int(REPEATED_NO_EXPANSION_N) and counterfactual_stale_t is None:
            counterfactual_stale_t = t1
            would_stale_without_marginal = True
        close_prog = (float(c) - float(prior["c"])) * float(sign) if prior and _finite(prior.get("c")) else None
        net_from_seed = (float(c) - float(seed_c)) * float(sign) if _finite(seed_c) else None
        loss = classify_active_loss(
            sign=sign,
            close=float(c),
            or_high=float(or_high) if _finite(or_high) else 0.0,
            or_low=float(or_low) if _finite(or_low) else 0.0,
            open_0900=open_0900,
            peak_disp=peak,
            wick_only_n=0,
            micro_break_n=0,
            recross_closes=recross,
            left=left,
            five_m_no_expansion_n=no_exp,
            both_or_extremes_revisited=both,
        )
        if t1 > "09:14":
            events.append(
                {
                    "t1": t1,
                    "extreme_before": prev_ext,
                    "extreme_after": ext,
                    "extension_delta": delta,
                    "extension_over_same_clock_5m_baseline": (float(delta) / float(baseline)) if delta is not None and _finite(baseline) and float(baseline) > 0 else None,
                    "extension_over_atr20": (float(delta) / float(atr)) if delta is not None and _finite(atr) and float(atr) > 0 else None,
                    "close_to_close_directional_progress": close_prog,
                    "bar_body_over_range": b.get("body_over_range"),
                    "bar_range_over_same_clock_normal": (float(b["range"]) / float(baseline)) if _finite(b.get("range")) and _finite(baseline) and float(baseline) > 0 else None,
                    "overlap_with_previous_5m": _overlap(prior, b) if prior else None,
                    "net_directional_progress_from_seed": net_from_seed,
                    "gross_path_from_seed": gross,
                    "net_over_gross": (float(net_from_seed) / float(gross)) if _finite(net_from_seed) and gross > 0 else None,
                    "distance_retraced_from_peak_disp": retrace,
                    "or_recross": recross,
                    "local_range_recross_proxy_both_extremes": both,
                    "new_extreme_flag": new_ext,
                    "reset_class": kind,
                    "stall_before": stall_before,
                    "stall_after": no_exp,
                    "active_loss": loss.get("reason"),
                }
            )
        if loss.get("lost") and death is None:
            death = loss.get("reason")
            death_t = t1
        prior = b
    post_n = sum(1 for e in events if e.get("t1") > "09:14")
    return {
        "ok": True,
        "sign": sign,
        "UNWIND_FRAC": UNWIND_FRAC,
        "REPEATED_NO_EXPANSION_N": REPEATED_NO_EXPANSION_N,
        "any_new_extreme_resets_stall": True,
        "v2_any_new_extreme_qualifies": False,
        "v2_meaningful_progress_means": (
            "renewed directional auction: close-to-close continuation of the live drive, "
            "not a wick nick or overlapping range drift that prints a marginally new high/low"
        ),
        "events": events,
        "post_seed_5m_n": post_n,
        "replay_death": death,
        "replay_death_t": death_t,
        "would_stale_without_marginal_resets": would_stale_without_marginal,
        "counterfactual_stale_t": counterfactual_stale_t,
        "reset_classes": [e.get("reset_class") for e in events if e.get("new_extreme_flag")],
        "clock_cutoff": False,
    }


def late_time_alignment(rows: list[dict[str, Any]], logs: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    late = []
    actual_leaks = []
    early_loc = []
    for r in rows:
        human = str(r.get("human_opening_state") or "")
        is_late = bool(r.get("human_late")) or human == "LATE_RANGE_RESOLUTION"
        if not is_late:
            continue
        loc_t = r.get("location_t")
        key = (str(r.get("symbol")), str(r.get("date")))
        log = logs.get(key) or {}
        early = bool(loc_t) and str(loc_t) <= "09:14"
        post = int(log.get("post_seed_5m_n") or 0)
        died = log.get("replay_death")
        ix_t = r.get("e0_entry_t") or r.get("e1_entry_t")
        death_t = log.get("replay_death_t")
        died_before_ix = bool(died) and (not ix_t or (death_t and str(death_t) < str(ix_t)[:5]))
        rec = {
            "rca_id": r.get("rca_id"),
            "symbol": r.get("symbol"),
            "date": r.get("date"),
            "machine_SEED": r.get("machine_SEED"),
            "location_t": loc_t,
            "location_family": r.get("location_family"),
            "seed_valid_at_0915": str(r.get("machine_SEED") or "") in ("TRUE_OPENING_DRIVE_SEED", "FAILED_OPEN_SEED"),
            "location_at_0915": bool(loc_t) and str(loc_t) <= "09:15",
            "post_seed_5m_n": post,
            "replay_death": died,
            "replay_death_t": death_t,
            "would_stale_without_marginal_resets": log.get("would_stale_without_marginal_resets"),
            "counterfactual_stale_t": log.get("counterfactual_stale_t"),
            "would_active_have_died_later_before_actual_interaction": died_before_ix
            or (
                bool(log.get("counterfactual_stale_t"))
                and bool(ix_t)
                and str(log.get("counterfactual_stale_t")) < str(ix_t)[:5]
            )
            or (
                bool(log.get("counterfactual_stale_t"))
                and bool(loc_t)
                and str(log.get("counterfactual_stale_t")) < str(loc_t)
                and str(loc_t) > "09:14"
            ),
            "interaction_or_exec_t": ix_t,
            "observability_ge_3_post_seed_bars": post >= 3,
            "not_a_hard_3_bar_rule": True,
        }
        late.append(rec)
        cf_t = log.get("counterfactual_stale_t")
        leak_after_evolution = (not early) and post >= 3 and (
            (not died)
            or (cf_t and loc_t and str(cf_t) < str(loc_t))
            or (cf_t and ix_t and str(cf_t) < str(ix_t)[:5])
        )
        if early:
            rec["class"] = "LOCATION_AT_0914_NOT_EVIDENCE_ACTIVE_FAILED_TO_DIE_LATER"
            early_loc.append(rec)
        elif leak_after_evolution:
            rec["class"] = "ACTUAL_ACTIVE_LEAK_AFTER_EVOLUTION"
            actual_leaks.append(rec)
        elif died:
            rec["class"] = "ACTIVE_WOULD_HAVE_DIED_LATER"
        else:
            rec["class"] = "INSUFFICIENT_POST_SEED_OBSERVABILITY"
    # Clean ACTIVE-staleness observability: thesis/location after post-seed 5m evolution.
    # 09:14 family-A mints are excluded from this set; they are not later-leak evidence.
    clean = [
        x
        for x in late
        if x.get("observability_ge_3_post_seed_bars")
        and (not x.get("location_t") or str(x.get("location_t")) > "09:14")
    ]
    return {
        "human_late_n": len(late),
        "location_t_0914_n": len(early_loc),
        "do_not_use_0914_as_active_failed_to_die": True,
        "actual_active_leaks_after_time_alignment_n": len(actual_leaks),
        "actual_active_leaks": actual_leaks,
        "clean_active_staleness_set": clean,
        "rows": late,
    }
