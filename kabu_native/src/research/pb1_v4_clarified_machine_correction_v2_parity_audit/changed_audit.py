"""Classify all 46 changed rows. Final-reject-stayed-reject is not sufficient."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_correction_v2_parity_audit.reconstruct import pick
from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES

CLEAR_TWO_SIDED_KEYS = (
    ("5803", "20250212"),
    ("6963", "20250613"),
    ("5803", "20250709"),
    ("5706", "20250725"),
)
FAILED_OPEN_POS = (("3382", "20241004"), ("7011", "20250523"), ("4063", "20251118"))
FAILED_OPEN_NEG = (("3382", "20241115"), ("6273", "20250120"), ("5802", "20250613"), ("7182", "20251112"))


def _live(st: dict[str, Any]) -> bool:
    return bool(st.get("thesis") or st.get("active"))


def classify_one(ch: dict[str, Any], *, row: dict[str, Any]) -> dict[str, Any]:
    old = dict(ch.get("old_state") or {})
    new = dict(ch.get("new_state") or {})
    human = str(ch.get("human_opening_state") or row.get("human_opening_state") or "")
    pattern = str(ch.get("human_pattern") or row.get("human_pattern") or "")
    key = (str(ch.get("symbol")), str(ch.get("date")))
    diffs = dict(ch.get("diffs") or {})
    only_rename = set(diffs.keys()) <= {"location_A_class", "failed_open_form", "last_progress_class"}
    cause = str(ch.get("rca_cause") or "OTHER_COLLATERAL")

    label = "EXPECTED_REPRESENTATION_CHANGE"
    agree_old = human in VALID_OPENING_STATES and _live(old)
    agree_new = human in VALID_OPENING_STATES and _live(new)
    if key == ("7011", "20241205"):
        label = "SEMANTIC_REGRESSION"
        cause = "ONE_BAR_PATCH_REMOVAL"
        why = "Human MICRO_OR_LEAK became live TRUE+A2+THESIS after removing the standalone 0.55/0.20 pair. Continued-intent followthrough counted the dominant bar itself as n_strong, so ONE_BAR_DOMINATED_WITHOUT_FOLLOWTHROUGH did not fire."
    elif key in CLEAR_TWO_SIDED_KEYS:
        label = "SEMANTIC_REGRESSION"
        cause = "SEED_TWO_SIDED_LOGIC"
        why = "Human CLEAR_CONTINUATION lost live THESIS_READY; parent had thesis. Two-sided path now rejects a committed finish after a counter bar with body>=0.35."
    elif key in FAILED_OPEN_NEG and _live(old) and not _live(new):
        label = "SEMANTIC_IMPROVEMENT"
        cause = "FAILED_OPEN_VISIBLE_ATTEMPT"
        why = "Wide-doji/range FAILED_OPEN live thesis removed; no visible failed-auction path."
    elif key in FAILED_OPEN_POS:
        label = "SEMANTIC_IMPROVEMENT" if _live(new) else "SEMANTIC_REGRESSION"
        cause = "FAILED_OPEN_VISIBLE_ATTEMPT"
        why = "Positive FAILED_OPEN form path."
    elif key in (("9432", "20250402"), ("8058", "20250814")):
        label = "NEEDS_CHART_REVIEW"
        cause = "FAMILY_A_CLEAR_SEMANTICS"
        why = "VWAP A3 blocked, then A2 structural fallback minted A_CLEARED_ZONE. Must check whether A2 is genuine already-cleared identity or A3 bypass."
    elif only_rename:
        label = "BENIGN_STATE_RENAMING"
        why = "Annotation-only change (A_class / form / progress class)."
    elif pattern == "CLEAR_CONTINUATION" and _live(old) and not _live(new):
        label = "SEMANTIC_REGRESSION"
        why = "Human CLEAR_CONTINUATION lost live thesis versus parent."
    elif pattern == "CLEAR_CONTINUATION" and (not _live(old)) and _live(new):
        label = "SEMANTIC_IMPROVEMENT"
        why = "Human CLEAR_CONTINUATION newly reached live thesis."
    elif human in ("MICRO_OR_LEAK", "FLAT_OR_CRAWL", "LATE_RANGE_RESOLUTION", "TWO_SIDED_OPEN") and _live(old) and not _live(new):
        label = "SEMANTIC_IMPROVEMENT"
        why = "Invalid/late human opening no longer carries a live thesis."
    elif human in ("MICRO_OR_LEAK", "FLAT_OR_CRAWL") and (not _live(old)) and _live(new):
        label = "SEMANTIC_REGRESSION"
        why = "MICRO/crawl/flat revived as live thesis."
    elif str(row.get("human_location") or "") == "QUESTIONABLE_LOCATION" and str(new.get("family") or "") == "A_CLEARED_ZONE":
        label = "NEEDS_CHART_REVIEW"
        cause = "FAMILY_A_CLEAR_SEMANTICS"
        why = "A_CLEARED_ZONE under human questionable location."
    else:
        why = "State change without a clean improvement/regression mapping; layer still inspected."
        if human in ("FAILED_OPEN_THEN_REAL_DRIVE", "TRUE_OPENING_DRIVE") and human != str(new.get("opening_state") or ""):
            label = "EXPECTED_REPRESENTATION_CHANGE"
        if pattern in ("QUESTIONABLE", "NOT_CONTINUATION"):
            label = "HUMAN_LABEL_AMBIGUITY" if label == "EXPECTED_REPRESENTATION_CHANGE" else label

    wrong_layer = False
    if human in VALID_OPENING_STATES and not _live(new) and str(new.get("seed") or "").startswith("NO_VALID"):
        wrong_layer = True
    return {
        "rca_id": ch.get("rca_id"),
        "symbol": ch.get("symbol"),
        "date": ch.get("date"),
        "human_pattern": pattern,
        "human_opening_state": human,
        "old_state": old,
        "new_state": new,
        "diffs": diffs,
        "implementation_cause": cause,
        "class": label,
        "why": why,
        "human_agreement_old_live_match": agree_old,
        "human_agreement_new_live_match": agree_new,
        "agreement_improved": (not agree_old) and agree_new,
        "agreement_worsened": agree_old and not agree_new,
        "uncertainty_remains": label in ("NEEDS_CHART_REVIEW", "HUMAN_LABEL_AMBIGUITY", "EXPECTED_REPRESENTATION_CHANGE"),
        "wrong_failure_layer": wrong_layer,
        "final_reject_stayed_reject_not_sufficient": True,
    }


def classify_changed(*, changed: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = []
    by: dict[str, int] = {}
    for ch in list(changed.get("rows") or []):
        rec = classify_one(ch, row=pick(rows, str(ch.get("symbol")), str(ch.get("date"))))
        out.append(rec)
        by[str(rec["class"])] = int(by.get(rec["class"]) or 0) + 1
    cause_sum: dict[str, int] = {}
    for rec in out:
        k = str(rec.get("implementation_cause") or "OTHER_COLLATERAL")
        cause_sum[k] = int(cause_sum.get(k) or 0) + 1
    return {
        "n": len(out),
        "by_class": by,
        "by_implementation_cause": cause_sum,
        "regression_n": int(by.get("SEMANTIC_REGRESSION") or 0),
        "improvement_n": int(by.get("SEMANTIC_IMPROVEMENT") or 0),
        "rows": out,
        "accuracy_is_not_the_criterion": True,
    }
