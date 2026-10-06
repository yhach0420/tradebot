"""Development contamination ledger. Prospective data is not opened."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_face_failure_rca import MANIFEST_FINAL_N, SEMANTIC_RCA_N
from research.pb1_v4_clarified_machine_correction_v4.audit import LATE_ACTIVE_EXAMPLES, PREV_S1_FN, PREV_S1_FP, REQUIRED_REGRESSIONS
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.isolation import FACE_RCA_CACHE, UNSEEN_OUT, V32_OUT, V4_OUT

V4_14 = (
    ("7011", "20241205"),
    ("8002", "20241002"),
    ("6857", "20250930"),
    ("8630", "20250828"),
    ("6501", "20251010"),
    ("6963", "20250613"),
    ("5803", "20250212"),
    ("8031", "20250225"),
    ("7741", "20250314"),
    ("5803", "20250709"),
    ("5706", "20250725"),
    ("3382", "20241004"),
    ("7011", "20250523"),
    ("4063", "20251118"),
)

DELTA_B_6 = (
    ("7011", "20241107"),
    ("7011", "20250523"),
    ("6963", "20250613"),
    ("8630", "20250828"),
    ("8725", "20251125"),
    ("6501", "20250724"),
)


def _add(acc: dict[tuple[str, str], dict[str, Any]], *, symbol: Any, date: Any, reason: str, stage: str) -> None:
    sym = str(symbol or "").strip()
    day = str(date or "").strip()
    if not sym or not day or day.lower() in ("none", "null"):
        return
    key = (sym, day)
    cur = acc.get(key)
    if cur is None:
        acc[key] = {
            "symbol": sym,
            "date": day,
            "reason_exposed": reason,
            "first_exposed_stage": stage,
            "last_used_stage": stage,
            "can_be_prospective": False,
            "stages": [stage],
        }
        return
    if stage not in list(cur.get("stages") or []):
        cur["stages"].append(stage)
    cur["last_used_stage"] = stage
    if reason not in str(cur.get("reason_exposed") or ""):
        cur["reason_exposed"] = f"{cur['reason_exposed']}|{reason}"


def _load_json(path) -> dict[str, Any] | list[Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_key(raw: str) -> tuple[str, str] | None:
    parts = str(raw).split("|")
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def build_ledger() -> dict[str, Any]:
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    missing: list[str] = []

    slim = FACE_RCA_CACHE / "descriptor_slim.json"
    rca_rows = _load_json(slim)
    if not isinstance(rca_rows, list) or not rca_rows:
        missing.append("descriptor_slim_88")
    else:
        for r in rca_rows:
            if not isinstance(r, dict):
                continue
            _add(acc, symbol=r.get("symbol"), date=r.get("date"), reason="semantic_development_88", stage="PB1_V3_2_FACE_FAILURE_RCA")
            _add(acc, symbol=r.get("symbol"), date=r.get("date"), reason="v2_v3_v4_rca_set", stage="PB1_V4_CLARIFIED_MACHINE_CORRECTION_V2_RCA")
            _add(acc, symbol=r.get("symbol"), date=r.get("date"), reason="v2_v3_v4_rca_set", stage="PB1_V4_CLARIFIED_MACHINE_CORRECTION_V3_DELTA_RCA")

    for path in (UNSEEN_OUT / "face_review_exclusion_manifest.json", V32_OUT / "face_review_exclusion_manifest.json"):
        man = _load_json(path)
        if not isinstance(man, dict) or not man:
            continue
        keys = list(man.get("face_review_exclusion_keys") or []) + list(man.get("keys") or [])
        for raw in keys:
            parsed = _parse_key(str(raw))
            if parsed:
                _add(acc, symbol=parsed[0], date=parsed[1], reason="face_review_exclusion_manifest", stage="PB1_V3_2_FACE_REVIEW_MANIFEST")
        for raw in list(man.get("newly_added_keys") or []) + list(man.get("discovery_unseen_keys") or []):
            parsed = _parse_key(str(raw))
            if parsed:
                _add(acc, symbol=parsed[0], date=parsed[1], reason="v32_discovery_unseen_face", stage="PB1_V3_2_DISCOVERY_UNSEEN_FACE_VERIFY")

    for sym, date in V4_14:
        _add(acc, symbol=sym, date=date, reason="v4_14_required_targets", stage="PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4")
    for sym, date in REQUIRED_REGRESSIONS:
        _add(acc, symbol=sym, date=date, reason="v4_required_regressions", stage="PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4")
    for _rid, sym, date, _h in PREV_S1_FP + PREV_S1_FN:
        _add(acc, symbol=sym, date=date, reason="prior_s1_fp_fn_mapping", stage="PB1_V4_IMPLEMENTATION_CORRECTION")
    for sym, date in LATE_ACTIVE_EXAMPLES:
        _add(acc, symbol=sym, date=date, reason="late_active_example", stage="PB1_V4_CLARIFIED_MACHINE_IMPLEMENTATION")
    for sym, date in DELTA_B_6:
        _add(acc, symbol=sym, date=date, reason="v3_to_v4_delta_b_failed_break_reaccepted", stage="PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4")

    v4_rep = _load_json(V4_OUT / "report.json")
    if not isinstance(v4_rep, dict) or not v4_rep:
        missing.append("v4_report")
    else:
        cmp = dict(v4_rep.get("changed_row_manifest") or {})
        for r in list(cmp.get("rows") or []):
            if not isinstance(r, dict):
                continue
            reason = str(r.get("change_reason") or "v3_to_v4_changed_row")
            _add(acc, symbol=r.get("symbol"), date=r.get("date"), reason=f"v3_to_v4_{reason}", stage="PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4")
        for r in list(v4_rep.get("audit_rows") or []):
            if not isinstance(r, dict):
                continue
            _add(acc, symbol=r.get("symbol"), date=r.get("date"), reason="v4_88_audit_row", stage="PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4")

    rows = [acc[k] for k in sorted(acc)]
    n88 = sum(1 for r in rows if "semantic_development_88" in str(r.get("reason_exposed") or ""))
    n_delta_b = sum(1 for r in rows if "delta_b" in str(r.get("reason_exposed") or ""))
    n_manifest = sum(1 for r in rows if "face_review_exclusion_manifest" in str(r.get("reason_exposed") or ""))
    boundary_ok = (not missing) and n88 == int(SEMANTIC_RCA_N) and n_manifest >= 300 and len(rows) >= int(SEMANTIC_RCA_N) and all(r.get("can_be_prospective") is False for r in rows)
    return {
        "ok": boundary_ok,
        "CONTAMINATED_SYMBOL_DATE_N": len(rows),
        "semantic_88_n": n88,
        "manifest_expected_n": int(MANIFEST_FINAL_N),
        "delta_b_tagged_n": n_delta_b,
        "missing_sources": missing,
        "rows": rows,
        "can_be_prospective_n": sum(1 for r in rows if r.get("can_be_prospective")),
        "all_can_be_prospective_false": all(r.get("can_be_prospective") is False for r in rows),
    }
