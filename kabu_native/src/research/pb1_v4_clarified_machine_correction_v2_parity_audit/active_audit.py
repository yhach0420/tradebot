"""ACTIVE stall replay. Do not infer from progress_log labels alone. No new 3-bar rule."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v2 import REPEATED_NO_EXPANSION_N
from research.pb1_v4_clarified_machine_correction_v2.active import PROGRESS_RESET_CLASSES, classify_progress
from research.pb1_v4_clarified_machine_correction_v2_parity_audit.reconstruct import pick

RESET = set(PROGRESS_RESET_CLASSES)
NONRESET = {"RANGE_DRIFT_EXTREME", "MARGINAL_EXTREME_ONLY", "NO_DIRECTIONAL_PROGRESS"}
FOCUS = (
    ("7011", "20241205"),
    ("8031", "20250225"),
    ("8058", "20250812"),
    ("8630", "20250911"),
    ("9432", "20250402"),
    ("8058", "20250814"),
    ("3382", "20241004"),
    ("7011", "20250523"),
    ("4063", "20251118"),
)


def replay_log(row: dict[str, Any]) -> dict[str, Any]:
    log = list(row.get("progress_log") or [])
    loc_t = str(row.get("location_t") or "")
    stall = 0
    had_renewed = False
    transitions = []
    would_stale_t = None
    consec_nonreset = 0
    max_consec = 0
    for i, ev in enumerate(log):
        cls = str(ev.get("class") or "NO_DIRECTIONAL_PROGRESS")
        resets = bool(ev.get("resets_stall"))
        # Do not trust the stored boolean alone: class membership is the definition.
        resets_by_class = cls in RESET
        t1 = str(ev.get("t1") or "")[:5]
        loc_on = bool(loc_t) and t1 >= loc_t
        stall_before = stall
        if resets_by_class:
            stall = 0
            had_renewed = True
            consec_nonreset = 0
        else:
            stall += 1
            consec_nonreset += 1
            max_consec = max(max_consec, consec_nonreset)
        would = stall >= int(REPEATED_NO_EXPANSION_N) and (loc_on or not had_renewed)
        if would and would_stale_t is None:
            would_stale_t = t1
        transitions.append(
            {
                "t1": t1,
                "progress_class": cls,
                "stored_resets_stall": resets,
                "resets_stall_by_class": resets_by_class,
                "label_class_mismatch": bool(resets) != resets_by_class,
                "stall_before": stall_before,
                "stall_after": stall,
                "location_already_identified": loc_on,
                "had_renewed_auction": had_renewed,
                "would_stale": would,
            }
        )
    death = row.get("machine_death")
    thesis = bool(row.get("machine_THESIS_READY"))
    last_t = str(transitions[-1]["t1"]) if transitions else ""
    log_continued_after_would_stale = bool(would_stale_t) and last_t > str(would_stale_t)
    e0 = str(row.get("e0_entry_t") or "")
    e1 = str(row.get("e1_entry_t") or "")
    exec_t = e1 or e0
    stale_before_exec = bool(would_stale_t) and (not exec_t or str(would_stale_t) < exec_t[:5])
    # C: progress_log continues after the stall-3 stale point while still pre-exec.
    live_freeze = log_continued_after_would_stale and stale_before_exec
    # D: live death likely happened (log stops) but funnel THESIS_READY stays true.
    report_omits_death = (not log_continued_after_would_stale) and stale_before_exec and thesis and death in (None, "")
    freeze = live_freeze or report_omits_death
    stall_advanced = any(int(x["stall_after"]) > int(x["stall_before"]) for x in transitions)
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "machine_SEED": row.get("machine_SEED"),
        "machine_ACTIVE": row.get("machine_ACTIVE"),
        "machine_THESIS_READY": thesis,
        "machine_death": death,
        "location_t": loc_t,
        "n_log": len(log),
        "max_consecutive_nonreset": max_consec,
        "stall_actually_advanced": stall_advanced,
        "would_stale_t": would_stale_t,
        "log_continued_after_would_stale": log_continued_after_would_stale,
        "stale_before_execution": stale_before_exec,
        "live_ACTIVE_death_monitoring_stopped_early": live_freeze,
        "report_omits_later_death": report_omits_death,
        "thesis_report_still_live_after_would_stale": freeze,
        "transitions": transitions,
    }


def independent_progress(row: dict[str, Any]) -> dict[str, Any]:
    """Recompute progress classes from 5m bars. Do not trust log labels."""
    snap = dict(row.get("snap") or {})
    bars = [b for b in list(snap.get("bars") or []) if str(b.get("t1") or "") >= "09:14"]
    sign = int(row.get("machine_DIR") or 0)
    if sign not in (1, -1) or len(bars) < 2:
        return {"ok": False}
    out = []
    prev_ext = bars[0].get("h") if sign > 0 else bars[0].get("l")
    prev_bar = bars[0]
    stall = 0
    for bar in bars[1:]:
        prog = classify_progress(sign=sign, bar=bar, prev_bar=prev_bar, prev_ext=prev_ext)
        cls = str(prog.get("class") or "NO_DIRECTIONAL_PROGRESS")
        stall_before = stall
        if prog.get("resets_stall"):
            stall = 0
            prev_ext = bar.get("h") if sign > 0 else bar.get("l")
        else:
            stall += 1
            if cls in ("RANGE_DRIFT_EXTREME", "MARGINAL_EXTREME_ONLY"):
                ext = bar.get("h") if sign > 0 else bar.get("l")
                if _finite(ext) and _finite(prev_ext):
                    if (sign > 0 and float(ext) > float(prev_ext)) or (sign < 0 and float(ext) < float(prev_ext)):
                        prev_ext = ext
        out.append(
            {
                "t1": bar.get("t1"),
                "class": cls,
                "resets_stall": bool(prog.get("resets_stall")),
                "delta": prog.get("delta"),
                "overlap": prog.get("overlap"),
                "tiny_extreme": prog.get("tiny_extreme"),
                "stall_before": stall_before,
                "stall_after": stall,
            }
        )
        prev_bar = bar
    return {"ok": True, "independent": out}


def active_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active_rows = [
        r
        for r in rows
        if bool(r.get("machine_ACTIVE")) and str(r.get("machine_SEED") or "") in ("TRUE_OPENING_DRIVE_SEED", "FAILED_OPEN_SEED")
    ]
    replays = [replay_log(r) for r in active_rows]
    ge3 = [x for x in replays if int(x.get("max_consecutive_nonreset") or 0) >= 3]
    freeze_n = sum(1 for x in ge3 if x.get("thesis_report_still_live_after_would_stale"))
    live_freeze_n = sum(1 for x in ge3 if x.get("live_ACTIVE_death_monitoring_stopped_early"))
    report_omit_n = sum(1 for x in ge3 if x.get("report_omits_later_death"))
    stall_adv_n = sum(1 for x in ge3 if x.get("stall_actually_advanced"))
    focus = []
    for s, d in FOCUS:
        row = pick(rows, s, d)
        rec = replay_log(row)
        rec["independent"] = independent_progress(row)
        focus.append(rec)
    r6963 = pick(rows, "6963", "20241002")
    # 0.20 / 0.70 correspondence: independent tiny vs class
    tiny_mismatch = 0
    overlap_mismatch = 0
    n_prog = 0
    for r in active_rows:
        ind = independent_progress(r)
        if not ind.get("ok"):
            continue
        for ev in list(ind.get("independent") or []):
            n_prog += 1
            tiny = bool(ev.get("tiny_extreme"))
            ov = ev.get("overlap")
            cls = str(ev.get("class") or "")
            if tiny and cls in RESET:
                tiny_mismatch += 1
            if _finite(ov) and float(ov) >= 0.70 and cls in RESET:
                overlap_mismatch += 1
    return {
        "active_with_valid_seed_n": len(active_rows),
        "ACTIVE_rows_with_ge3_consecutive_nonreset": len(ge3),
        "of_those_stall_actually_advanced_n": stall_adv_n,
        "of_those_thesis_report_still_live_n": freeze_n,
        "of_those_live_monitoring_stopped_early_n": live_freeze_n,
        "of_those_report_omits_later_death_n": report_omit_n,
        "LOCATION_THESIS_freeze_ACTIVE_death_monitoring": freeze_n > 0,
        "ge3_probe_rows": [
            {
                "symbol": x.get("symbol"),
                "date": x.get("date"),
                "max_consecutive_nonreset": x.get("max_consecutive_nonreset"),
                "stall_actually_advanced": x.get("stall_actually_advanced"),
                "would_stale_t": x.get("would_stale_t"),
                "log_continued_after_would_stale": x.get("log_continued_after_would_stale"),
                "stale_before_execution": x.get("stale_before_execution"),
                "live_ACTIVE_death_monitoring_stopped_early": x.get("live_ACTIVE_death_monitoring_stopped_early"),
                "report_omits_later_death": x.get("report_omits_later_death"),
                "machine_death": x.get("machine_death"),
                "machine_THESIS_READY": x.get("machine_THESIS_READY"),
                "freeze": x.get("thesis_report_still_live_after_would_stale"),
            }
            for x in ge3
        ],
        "focus": focus,
        "6963_20241002": {
            "machine_SEED": r6963.get("machine_SEED"),
            "machine_opening_state": r6963.get("machine_opening_state"),
            "machine_ACTIVE": r6963.get("machine_ACTIVE"),
            "machine_THESIS_READY": r6963.get("machine_THESIS_READY"),
            "e1_entry_t": r6963.get("e1_entry_t"),
            "seed_reject_independent_of_ACTIVE_correction": str(r6963.get("machine_SEED") or "") == "NO_VALID_DRIVE_SEED",
            "cannot_prove_ACTIVE_meaningful_progress": True,
        },
        "naturally_ACTIVE_test_used": len(ge3) > 0,
        "progress_020_070": {
            "independent_progress_events_n": n_prog,
            "tiny_classified_as_reset_n": tiny_mismatch,
            "heavy_overlap_classified_as_reset_n": overlap_mismatch,
            "semantic_consistency": tiny_mismatch == 0 and overlap_mismatch == 0,
            "grid_search": False,
        },
        "THESIS_READY_live_dynamic_required": True,
        "funnel_THESIS_READY_uses_thesis_id_not_cleared_on_lose": True,
        "lose_thesis_clears_thesis_id": False,
    }
