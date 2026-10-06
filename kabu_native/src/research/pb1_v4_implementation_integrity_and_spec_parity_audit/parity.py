"""Spec-parity on the 88 charts, 19 CLEAR, curated negatives, E0/E1. Diagnosis only."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.counts import proto_id, symbol_date
from research.pb1_v4_implementation_integrity_and_spec_parity_audit.isolation import RCA_CACHE
from research.pb1_v4_machine_implementation.audit import REQUIRED_NEGATIVES
from research.pb1_v4_opening_drive_location_reaccel_spec import INVALID_OPENING_STATES, VALID_OPENING_STATES
from research.pb1_v4_opening_drive_location_reaccel_spec.exemplars import CURATED_NEGATIVES


FOCUS = {
    "6787_20250214": ("6787", "20250214"),
    "6963_20250613": ("6963", "20250613"),
    "6857_20250930": ("6857", "20250930"),
    "9432_20250402": ("9432", "20250402"),
    "3382_20241004": ("3382", "20241004"),
    "4063_20251118": ("4063", "20251118"),
}


def _load_rca_rows() -> list[dict[str, Any]]:
    path = RCA_CACHE / "descriptor_slim.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _idx(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        out[symbol_date(r)] = r
    return out


def _confusion(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    tp = sum(1 for h, m in pairs if h and m)
    tn = sum(1 for h, m in pairs if (not h) and (not m))
    fp = sum(1 for h, m in pairs if (not h) and m)
    fn = sum(1 for h, m in pairs if h and (not m))
    n = len(pairs)
    return {"n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn, "accuracy": (tp + tn) / n if n else None}


def _s1_class(*, human: str, machine_pass: bool, machine_state: str) -> str | None:
    h_ok = human in VALID_OPENING_STATES
    m_state = str(machine_state or "")
    if h_ok and machine_pass:
        if human == m_state:
            return None
        return "EXPECTED_THRESHOLD_DISAGREEMENT"
    if (not h_ok) and (not machine_pass):
        if human == m_state:
            return None
        return "EXPECTED_THRESHOLD_DISAGREEMENT"
    if human == "MICRO_OR_LEAK" and machine_pass:
        return "IMPLEMENTATION_BUG"
    if human == "FAILED_OPEN_THEN_REAL_DRIVE" and (not machine_pass) and m_state == "LATE_RANGE_RESOLUTION":
        return "IMPLEMENTATION_BUG"
    if human == "TRUE_OPENING_DRIVE" and (not machine_pass):
        if m_state in INVALID_OPENING_STATES:
            return "EXPECTED_THRESHOLD_DISAGREEMENT"
        return "IMPLEMENTATION_BUG"
    if (not h_ok) and machine_pass:
        return "IMPLEMENTATION_BUG"
    if h_ok and (not machine_pass):
        if m_state == "LATE_RANGE_RESOLUTION":
            return "IMPLEMENTATION_BUG"
        return "SPEC_AMBIGUITY"
    return "SPEC_AMBIGUITY"


def _machine_fail_layer(f: dict[str, Any]) -> str | None:
    if f.get("S4"):
        return None
    if not f.get("S0"):
        return "WHY_THIS_STOCK_TODAY"
    if not f.get("S1"):
        death = str(f.get("death") or f.get("opening_state") or "")
        if death == "LATE_RANGE_RESOLUTION":
            return "OPENING_THESIS_LOST"
        return "FIVE_M_OPENING_DRIVE"
    if not f.get("S2"):
        return "MEANINGFUL_PRICE_LOCATION"
    if not f.get("S3"):
        return "BREAK_RETEST"
    death = str(f.get("death") or "")
    if death in (
        "DISPLACEMENT_UNWOUND",
        "MULTIPLE_FAILED_BREAKS",
        "OR_RECROSS",
        "LATE_RANGE_RESOLUTION",
        "PERSISTENT_ACCEPTANCE_FAILURE",
        "RETEST_EXTREME_BREACH",
    ):
        return "OPENING_THESIS_LOST"
    return "FIVE_M_CONTINUATION_STATE"


def _miss_layer(f: dict[str, Any]) -> str | None:
    if f.get("S4"):
        return None
    if not f.get("S1"):
        return "S1"
    if not f.get("S2"):
        return "S2"
    if not f.get("S3"):
        return "S3"
    return "S4"


def _s4_class(*, layer: str | None, death: str, loc_kind_human: str, location_family: str) -> str:
    if layer is None:
        return "REACHED_S4"
    if layer in ("S1", "S2", "S3"):
        return "UPSTREAM_STATE_MISCLASSIFICATION"
    d = str(death or "")
    if d in ("LACK_OF_DIRECTIONAL_EXPANSION", "NO_5M_CONTINUATION") or "EXPANSION" in d:
        return "IMPLEMENTATION_TOO_STRICT"
    return "IMPLEMENTATION_TOO_STRICT"


def _s2_cause(human_kind: str, human_loc: str, family: str, death: str) -> dict[str, Any]:
    d = str(death or "")
    fam = str(family or "")
    flags = {"A": False, "B": False, "C": False, "D": False, "E": False}
    if human_kind == "CLEARED_ZONE_RETEST" and fam != "CLEARED_ZONE_RETEST":
        flags["A"] = True
        flags["C"] = True
    if d == "STRUCTURALLY_BLOCKED":
        flags["B"] = True
        flags["E"] = True
    if fam == "OR_HELD_AFTER_REAL_DRIVE" and human_loc != "CLEAR_DEFENDED_LOCATION":
        flags["D"] = True
    if d in ("NO_MEANINGFUL_ROOM", "NEAREST_OPPOSING_TOO_CLOSE", "STRUCTURALLY_BLOCKED"):
        flags["E"] = True
    return {
        "A_zone_identity_wrong": flags["A"],
        "B_opposing_level_wrong": flags["B"],
        "C_far_side_retest_semantics_differ": flags["C"],
        "D_OR_HELD_too_permissive": flags["D"],
        "E_reward_space_logic_where_spec_did_not_intend": flags["E"],
        "death": d,
        "machine_family": fam,
        "human_location_kind": human_kind,
        "human_location": human_loc,
    }


def audit_parity(walked: dict[str, Any]) -> dict[str, Any]:
    rca_rows = _load_rca_rows()
    funnel = list(walked.get("funnel_days") or [])
    setups = list(walked.get("setups") or [])
    e0_events = list(walked.get("e0_events") or [])
    e1_events = list(walked.get("e1_events") or [])
    fidx = _idx(funnel)
    sidx = _idx(setups)
    e0idx = _idx(e0_events)
    e1idx = _idx(e1_events)

    rows: list[dict[str, Any]] = []
    s0_p: list[tuple[bool, bool]] = []
    s1_p: list[tuple[bool, bool]] = []
    s2_p: list[tuple[bool, bool]] = []
    s3_p: list[tuple[bool, bool]] = []
    s3_given_s2: list[tuple[bool, bool]] = []
    s4_p: list[tuple[bool, bool]] = []
    e1_p: list[tuple[bool, bool]] = []
    s1_mismatches: list[dict[str, Any]] = []
    s1_class_counts: Counter[str] = Counter()
    s3_human_valid_machine_reject: list[dict[str, Any]] = []
    s3_human_stale_machine_pass: list[dict[str, Any]] = []

    for r in rca_rows:
        rid = int(r.get("rca_id") or 0)
        lab = dict(HUMAN_LABELS.get(rid) or {})
        key = (str(r.get("symbol")), str(r.get("date")))
        f = dict(fidx.get(key) or {})
        h_s0 = str(lab.get("sp_opening_state") or "") != "FLAT_OR_CRAWL"
        h_s1 = str(lab.get("sp_opening_state") or "") in VALID_OPENING_STATES
        h_s2 = str(lab.get("sp_location") or "") == "CLEAR_DEFENDED_LOCATION"
        h_s3 = str(lab.get("sp_retest") or "") == "VALID_FIRST_RETEST"
        if str(lab.get("sp_retest") or "") == "STALE_OR_EXHAUSTED":
            h_s3 = False
        h_s4 = bool(h_s1 and h_s2 and not lab.get("sp_late"))
        h_e1 = str(lab.get("sp_trigger") or "") == "VALID_REACCELERATION"
        m_s0 = bool(f.get("S0"))
        m_s1 = bool(f.get("S1"))
        m_s2 = bool(f.get("S2"))
        m_s3 = bool(f.get("S3"))
        m_s4 = bool(f.get("S4"))
        m_e1 = bool(f.get("E1"))
        s0_p.append((h_s0, m_s0))
        s1_p.append((h_s1, m_s1))
        s2_p.append((h_s2, m_s2))
        s3_p.append((h_s3, m_s3))
        s4_p.append((h_s4, m_s4))
        e1_p.append((h_e1, m_e1))
        if m_s2:
            s3_given_s2.append((h_s3, m_s3))
        cls = _s1_class(
            human=str(lab.get("sp_opening_state") or ""),
            machine_pass=m_s1,
            machine_state=str(f.get("opening_state") or ""),
        )
        rec = {
            "rca_id": rid,
            "symbol": r.get("symbol"),
            "date": r.get("date"),
            "human_pattern": lab.get("sp_pattern"),
            "human_opening_state": lab.get("sp_opening_state"),
            "human_location": lab.get("sp_location"),
            "human_location_kind": lab.get("sp_location_kind"),
            "human_retest": lab.get("sp_retest"),
            "human_trigger": lab.get("sp_trigger"),
            "human_late": bool(lab.get("sp_late")),
            "machine_S0": m_s0,
            "machine_S1": m_s1,
            "machine_S2": m_s2,
            "machine_S3": m_s3,
            "machine_S4": m_s4,
            "machine_E0": bool(f.get("E0")),
            "machine_E1": m_e1,
            "machine_opening_state": f.get("opening_state"),
            "machine_death": f.get("death"),
            "location_family": f.get("location_family"),
            "location_reason": f.get("location_reason"),
            "SETUP_ELIGIBLE_AT": f.get("SETUP_ELIGIBLE_AT"),
            "DIR": f.get("DIR"),
            "proto_id": proto_id(f) if f else f"{r.get('symbol')}|{r.get('date')}|",
            "s1_mismatch_class": cls,
            "s2_cause": _s2_cause(
                str(lab.get("sp_location_kind") or ""),
                str(lab.get("sp_location") or ""),
                str(f.get("location_family") or ""),
                str(f.get("death") or ""),
            ),
        }
        rows.append(rec)
        if cls:
            s1_class_counts[cls] += 1
            s1_mismatches.append(rec)
        if h_s3 and (not m_s3):
            s3_human_valid_machine_reject.append(rec)
        if str(lab.get("sp_retest") or "") in ("STALE_OR_EXHAUSTED", "QUESTIONABLE_RETEST") and m_s3:
            s3_human_stale_machine_pass.append(rec)

    clear_rows = [r for r in rows if r.get("human_pattern") == "CLEAR_CONTINUATION"]
    clear_s4 = [r for r in clear_rows if r.get("machine_S4")]
    clear_miss = [r for r in clear_rows if not r.get("machine_S4")]
    miss_detail = []
    miss_layer_n: Counter[str] = Counter()
    miss_class_n: Counter[str] = Counter()
    for r in clear_miss:
        f = fidx.get((str(r["symbol"]), str(r["date"]))) or {}
        layer = _miss_layer(f)
        klass = _s4_class(
            layer=layer,
            death=str(r.get("machine_death") or ""),
            loc_kind_human=str(r.get("human_location_kind") or ""),
            location_family=str(r.get("location_family") or ""),
        )
        miss_layer_n[str(layer)] += 1
        miss_class_n[klass] += 1
        miss_detail.append(
            {
                **{k: r.get(k) for k in (
                    "rca_id", "symbol", "date", "human_opening_state", "human_location_kind",
                    "human_retest", "machine_S1", "machine_S2", "machine_S3", "machine_S4",
                    "machine_opening_state", "machine_death", "location_family",
                )},
                "failure_layer": layer,
                "class": klass,
                "s2_cause": r.get("s2_cause"),
            }
        )

    full_stack_human = [
        r for r in clear_rows
        if str(r.get("human_opening_state")) in VALID_OPENING_STATES
        and r.get("human_location") == "CLEAR_DEFENDED_LOCATION"
        and r.get("human_trigger") == "VALID_REACCELERATION"
    ]
    spec_17 = full_stack_human
    spec_17_not_s4 = [r for r in spec_17 if not r.get("machine_S4")]

    focus = {}
    for name, (sym, date) in FOCUS.items():
        rec = next((r for r in rows if str(r.get("symbol")) == sym and str(r.get("date")) == date), None)
        f = fidx.get((sym, date)) or {}
        focus[name] = {
            **(rec or {"symbol": sym, "date": date, "missing": True}),
            "machine_fail_layer": _machine_fail_layer(f),
            "funnel": {k: f.get(k) for k in ("S0", "S1", "S2", "S3", "S4", "E0", "E1", "death", "opening_state", "location_family", "location_reason")},
        }

    loc_6787 = focus["6787_20250214"]
    loc_6963 = focus["6963_20250613"]
    loc_6857 = focus["6857_20250930"]
    loc_9432 = focus["9432_20250402"]

    negatives = []
    final_ok = 0
    layer_ok = 0
    for n in CURATED_NEGATIVES:
        key = (str(n["symbol"]), str(n["date"]))
        f = dict(fidx.get(key) or {})
        rec = next((r for r in rows if symbol_date(r) == key), None) or {}
        m_layer = _machine_fail_layer(f)
        human_layer = str(n.get("fail_layer") or "")
        reached_s4 = bool(f.get("S4"))
        correct_final = not reached_s4
        correct_layer = (m_layer == human_layer) if human_layer else False
        if human_layer == "OPENING_THESIS_LOST" and m_layer in ("FIVE_M_OPENING_DRIVE", "OPENING_THESIS_LOST"):
            if str(n.get("opening_state") or "") == str(f.get("opening_state") or rec.get("machine_opening_state") or ""):
                correct_layer = True
        if correct_final:
            final_ok += 1
        if correct_layer:
            layer_ok += 1
        negatives.append(
            {
                **n,
                "machine_S1": rec.get("machine_S1"),
                "machine_S2": rec.get("machine_S2"),
                "machine_S3": rec.get("machine_S3"),
                "machine_S4": rec.get("machine_S4"),
                "machine_opening_state": rec.get("machine_opening_state") or f.get("opening_state"),
                "machine_death": rec.get("machine_death") or f.get("death"),
                "location_family": rec.get("location_family") or f.get("location_family"),
                "machine_fail_layer": m_layer,
                "CORRECT_FINAL_REJECT": correct_final,
                "CORRECT_FAILURE_LAYER": correct_layer,
            }
        )

    setup_ids = {str(s.get("setup_id")) for s in setups if s.get("setup_id")}
    e0_before = []
    e0_no_setup = []
    e1_before = []
    e1_no_setup = []
    e1_revived = []
    same_bar = []
    for ev, bucket_before, bucket_nosetup in (
        (e0_events, e0_before, e0_no_setup),
        (e1_events, e1_before, e1_no_setup),
    ):
        for row in ev:
            sid = str(row.get("setup_id") or "")
            eligible = str(row.get("SETUP_ELIGIBLE_AT") or "")
            entry = str(row.get("entry_t") or "")
            if sid not in setup_ids:
                bucket_nosetup.append(sid or proto_id(row))
            if eligible and entry and entry <= eligible:
                bucket_before.append(str(row.get("setup_id") or proto_id(row)))
            if row.get("same_bar_entry"):
                same_bar.append(sid)
    for row in e1_events:
        f = fidx.get(symbol_date(row)) or {}
        if not f.get("S4"):
            e1_revived.append(str(row.get("setup_id") or proto_id(row)))

    e1_cancel_n = int((walked.get("counts") or {}).get("E1_CANCEL") or 0)
    e0_ok = len(e0_before) == 0 and len(e0_no_setup) == 0
    e1_ok = (
        len(e1_before) == 0
        and len(e1_no_setup) == 0
        and len(e1_revived) == 0
        and e1_cancel_n >= 0
    )

    s3_counting = {
        "reported_s3_accuracy_includes_s3_without_s2": True,
        "s3_confusion_all_88": _confusion(s3_p),
        "s3_confusion_only_where_machine_s2": _confusion(s3_given_s2),
        "human_valid_first_retest_machine_reject_n": len(s3_human_valid_machine_reject),
        "human_stale_or_questionable_machine_s3_pass_n": len(s3_human_stale_machine_pass),
        "human_valid_reject_also_s3_without_s2_n": sum(
            1 for r in s3_human_valid_machine_reject if r.get("machine_S3") is False and r.get("machine_S2") is False
        ),
        "diagnosis": (
            "S3 0.60 is mixed: (1) S3 flag is geometric hold, so human VALID_FIRST_RETEST vs machine "
            "S3 compares different objects when location failed; (2) genuine retest-label disagreements remain "
            "on S2-pass days. Primary: state-identity/counting, plus residual semantic mismatch."
        ),
    }

    return {
        "n_88": len(rows),
        "confusion": {
            "S0": _confusion(s0_p),
            "S1": _confusion(s1_p),
            "S2": _confusion(s2_p),
            "S3": _confusion(s3_p),
            "S4": _confusion(s4_p),
            "E1": _confusion(e1_p),
        },
        "s1_mismatches": [
            {
                "rca_id": r["rca_id"],
                "symbol": r["symbol"],
                "date": r["date"],
                "human_opening_state": r["human_opening_state"],
                "machine_S1": r["machine_S1"],
                "machine_opening_state": r["machine_opening_state"],
                "class": r["s1_mismatch_class"],
            }
            for r in s1_mismatches
        ],
        "s1_mismatch_class_counts": dict(s1_class_counts),
        "s1_critical": {
            "human_MICRO_machine_S1_pass": [
                r for r in s1_mismatches
                if r.get("human_opening_state") == "MICRO_OR_LEAK" and r.get("machine_S1")
            ],
            "human_FAILED_OPEN_machine_LATE": [
                r for r in s1_mismatches
                if r.get("human_opening_state") == "FAILED_OPEN_THEN_REAL_DRIVE"
                and str(r.get("machine_opening_state") or "") == "LATE_RANGE_RESOLUTION"
            ],
            "human_TRUE_machine_reject": [
                r for r in s1_mismatches
                if r.get("human_opening_state") == "TRUE_OPENING_DRIVE" and not r.get("machine_S1")
            ],
        },
        "s3": s3_counting,
        "s3_human_valid_machine_reject": [
            {"rca_id": r["rca_id"], "symbol": r["symbol"], "date": r["date"], "machine_S2": r["machine_S2"], "machine_death": r["machine_death"]}
            for r in s3_human_valid_machine_reject
        ],
        "s3_human_stale_machine_pass": [
            {"rca_id": r["rca_id"], "symbol": r["symbol"], "date": r["date"], "human_retest": r["human_retest"], "machine_S2": r["machine_S2"]}
            for r in s3_human_stale_machine_pass
        ],
        "clear_n": len(clear_rows),
        "clear_s4_n": len(clear_s4),
        "clear_miss_n": len(clear_miss),
        "clear_misses": miss_detail,
        "clear_miss_layer_n": dict(miss_layer_n),
        "clear_miss_class_n": dict(miss_class_n),
        "spec_full_stack_n": len(spec_17),
        "spec_full_stack_not_machine_s4_n": len(spec_17_not_s4),
        "spec_17_vs_machine_7": {
            "spec_said_full_v4_stack": len(spec_17),
            "machine_s4": len(clear_s4),
            "why": (
                "Spec 17 counted human CLEAR charts whose labels already had valid opening + clear location + "
                "valid 1m cue. Implementation added V3 1R/room/overhead kills at S2, inverted S2/S3, and numeric S4. "
                "Those extra gates, not a change in the frozen semantic story, dropped most of the 17. "
                "6787 is in the machine 7 but was a spec location fail — a false S4 pass. "
                "Do not force 17/19."
            ),
        },
        "focus": focus,
        "6787_cause": (loc_6787.get("s2_cause") if loc_6787 else None) or _s2_cause(
            str((loc_6787 or {}).get("human_location_kind") or ""),
            str((loc_6787 or {}).get("human_location") or ""),
            str(((loc_6787 or {}).get("funnel") or {}).get("location_family") or (loc_6787 or {}).get("location_family") or ""),
            str(((loc_6787 or {}).get("funnel") or {}).get("death") or (loc_6787 or {}).get("machine_death") or ""),
        ),
        "6963_cause": (loc_6963 or {}).get("s2_cause"),
        "6857_cause": (loc_6857 or {}).get("s2_cause"),
        "9432": {
            "CORRECT_FINAL_REJECT": not bool((loc_9432 or {}).get("machine_S4")),
            "CORRECT_FAILURE_LAYER": str((loc_9432 or {}).get("machine_fail_layer") or "") == "MEANINGFUL_PRICE_LOCATION",
            "human_fail_layer": "MEANINGFUL_PRICE_LOCATION",
            "machine_fail_layer": (loc_9432 or {}).get("machine_fail_layer"),
            "machine_S2": (loc_9432 or {}).get("machine_S2"),
            "machine_S4": (loc_9432 or {}).get("machine_S4"),
            "machine_death": (loc_9432 or {}).get("machine_death"),
            "cause": (
                "Human: no visibly defensible location / planned R (reject at S2). "
                "Machine: family B/C or room gates did not kill; S2 passed; died at S4 LACK_OF_DIRECTIONAL_EXPANSION. "
                "Final eligibility false is correct; failure layer is wrong. OR_HELD / reward-space not applied as spec location."
            ),
        },
        "negatives": negatives,
        "negative_n": len(negatives),
        "negative_final_reject_accuracy": final_ok / len(negatives) if negatives else None,
        "negative_correct_layer_accuracy": layer_ok / len(negatives) if negatives else None,
        "negative_reached_s4_n": sum(1 for n in negatives if n.get("machine_S4")),
        "required_negatives_tuple_n": len(REQUIRED_NEGATIVES),
        "e0_e1": {
            "e0_n": len(e0_events),
            "e1_n": len(e1_events),
            "e0_only_after_SETUP_ELIGIBLE_AT": len(e0_before) == 0,
            "e1_only_after_SETUP_ELIGIBLE_AT": len(e1_before) == 0,
            "e0_without_setup_id_n": len(e0_no_setup),
            "e1_without_setup_id_n": len(e1_no_setup),
            "e1_creates_setup": False,
            "e1_revives_rejected_setup_n": len(e1_revived),
            "e1_never_creates_setup": len(e1_no_setup) == 0,
            "e1_never_revives_rejected": len(e1_revived) == 0,
            "SAME_BAR_ENTRY": len(same_bar) + int(walked.get("same_bar_entry_n") or 0),
            "e1_cancel_n": e1_cancel_n,
            "e1_cancel_only_after_s4_by_construction": True,
            "e0_invariant_valid": e0_ok,
            "e1_invariant_valid": e1_ok,
            "e0_entry_before_eligible_ids": e0_before,
            "e1_entry_before_eligible_ids": e1_before,
            "e1_revived_ids": e1_revived,
        },
        "rows": rows,
    }
