"""PB1 V4 Frozen Old Confirmation blind historical validation. Runtime 0/0/0."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_indicator_exit.isolation import advanced
from research.fixed_daytrade_universe_v1.secrets import assert_no_secret
from research.pb1_v4_clarified_machine_correction_v4.bind import bind_prior
from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.spec import source_sha256 as v4_source_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import FROZEN_IDENTITY
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.inventory import (
    file_inventory,
    source_inventory_sha,
)
from research.pb1_v4_frozen_old_confirmation_blind_validation import (
    ANALYSIS_ID,
    CASE_FAIL,
    CASE_HASH,
    CASE_PASS,
    EXPECTED_MACHINE_SHA256,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    NEXT_STOP,
    PROGRAM_ID,
)
from research.pb1_v4_frozen_old_confirmation_blind_validation.adjudicate import adjudicate
from research.pb1_v4_frozen_old_confirmation_blind_validation.dates import partition_dates
from research.pb1_v4_frozen_old_confirmation_blind_validation.gate import identity_gate
from research.pb1_v4_frozen_old_confirmation_blind_validation.isolation import (
    CACHE,
    OUT,
    V4_SRC,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_frozen_old_confirmation_blind_validation.precommit import blind_precommit
from research.pb1_v4_frozen_old_confirmation_blind_validation.publish import build_answers, build_sheets, write_artifacts
from research.pb1_v4_frozen_old_confirmation_blind_validation.spec import source_sha256 as harness_sha256
from research.pb1_v4_prospective_semantic_validation_preflight.scans import scan_v4_and_harness

JST = ZoneInfo("Asia/Tokyo")


def _fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    if not root.is_dir():
        return ""
    files = sorted(p for p in root.glob("*.py") if p.is_file())
    for p in files:
        h.update(p.name.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def _safety(*, opened: bool, leak: bool) -> dict[str, Any]:
    return {
        "V4_CHANGED": False,
        "SPEC_CHANGED": False,
        "THRESHOLD_RETUNED": False,
        "FROZEN_VALIDATION_OPENED": bool(leak),
        "PROSPECTIVE_DATA_OPENED": False,
        "PNL_USED": False,
        "MFE_MAE_USED": False,
        "FUTURE_OUTCOME_USED": False,
        "OLD_CONFIRMATION_OPENED": bool(opened),
        "submit/cancel/live": "0/0/0",
        "orders_submit": 0,
        "orders_cancel": 0,
        "orders_live": 0,
    }


def _finish(report: dict[str, Any]) -> int:
    report["answers"] = build_answers(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    write_artifacts(report, sheets)
    ans = dict(report.get("answers") or {})
    print(f"VERDICT {ans.get('VERDICT')}", flush=True)
    print(f"NEXT {ans.get('NEXT')}", flush=True)
    print("STOP", flush=True)
    verdict = str(ans.get("VERDICT") or "")
    if verdict == CASE_HASH:
        return 2
    if verdict == CASE_PASS:
        return 0
    return 1


def republish_from_existing() -> int:
    """Re-score against the locked precommit. Does not re-run Frozen V4."""
    from research.pb1_v4_frozen_old_confirmation_blind_validation.publish import read_sheet_dicts

    path = OUT / "report.json"
    xlsx = OUT / "audit.xlsx"
    report = json.loads(path.read_text(encoding="utf-8"))
    why = read_sheet_dicts(xlsx, "Candidate_Days")
    for r in why:
        for k in (
            "WHY_THIS_STOCK",
            "OPENING_DRIVE_REACHED",
            "OPENING_DRIVE_LIVE",
            "LOCATION_IDENTIFIED",
            "THESIS_REACHED",
            "THESIS_LIVE",
            "THESIS_LOST",
            "E0",
            "E1",
            "same_bar_entry",
            "backdating",
        ):
            v = r.get(k)
            if isinstance(v, str):
                r[k] = v.strip().lower() in {"true", "1", "yes"}
    zeros = dict((report.get("adjudication") or {}).get("REQUIRED_ZERO") or {})
    inv_raw = {
        "ACTIVE_without_SEED": int(zeros.get("ACTIVE_without_SEED") or 0),
        "LOCATION_without_ACTIVE": int(zeros.get("LOCATION_without_ACTIVE") or 0),
        "THESIS_READY_without_LOCATION": int(zeros.get("THESIS_READY_without_LOCATION") or 0),
        "THESIS_LIVE_without_ACTIVE_LIVE": int(zeros.get("THESIS_LIVE_without_ACTIVE_LIVE") or 0),
        "EXECUTION_READY_without_THESIS_LIVE": int(zeros.get("EXECUTION_READY_without_THESIS_LIVE") or 0),
        "THESIS_REVIVED_AFTER_LOSS": int(zeros.get("THESIS_REVIVED_AFTER_LOSS") or 0),
        "E0_emit_while_not_THESIS_LIVE": int(zeros.get("E0_WHILE_THESIS_NOT_LIVE") or 0),
        "E1_emit_while_not_THESIS_LIVE": int(zeros.get("E1_WHILE_THESIS_NOT_LIVE") or 0),
        "1m_created_seed": int(zeros.get("1M_CREATED_SEED") or 0),
        "1M_changed_direction_n": int(zeros.get("1M_CREATED_DIRECTION") or 0),
        "1m_revived_thesis": int(zeros.get("1M_REVIVED_THESIS") or 0),
    }
    elig = []
    for row in list(report.get("eligibility") or []):
        complete_n = int(row.get("complete_symbol_n") or 0)
        elig.append(
            {
                **row,
                "ok": complete_n >= 1,
                "INELIGIBLE_SESSION": complete_n < 1,
                "reason": None if complete_n >= 1 else row.get("reason"),
                "candidate_unit": "symbol-date",
                "prospective_min_active_not_applied": True,
            }
        )
    e0 = [r for r in why if r.get("E0")]
    e1 = [r for r in why if r.get("E1")]
    sliced = {
        "funnel_days": why,
        "e0_events": e0,
        "e1_events": e1,
        "invariants_raw": inv_raw,
        "hidden1m": dict(report.get("hidden1m") or {}),
        "counts": dict(report.get("counts") or {}),
        "same_bar_entry_n": int(zeros.get("SAME_BAR_ENTRY") or 0),
    }
    adj = adjudicate(sliced=sliced, eligibility=elig, leak_holdout=False)
    adj["frozen_v4_rerun"] = False
    adj["eligibility_gate"] = "symbol-date completeness; prospective MIN_ACTIVE=80 not applied"
    report["eligibility"] = elig
    report["adjudication"] = adj
    report["candidate_days"] = why
    report["decision"] = {
        "VERDICT": adj.get("VERDICT"),
        "NEXT": adj.get("NEXT"),
        "scans_ok": bool((report.get("scans") or {}).get("ok")),
        "frozen_v4_executed_once": True,
        "republished_after_eligibility_unit_correction": True,
    }
    report["OLD_CONFIRMATION_OPENED"] = True
    report["DEVELOPMENT_EXPOSED_AFTER_REVIEW"] = True
    print("REPUBLISH_FROM_EXISTING_WALK frozen_v4_not_rerun", flush=True)
    return _finish(report)


def main() -> int:
    if os.environ.get("PB1_V4_CONF_REPUBLISH") == "1":
        return republish_from_existing()
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_FROZEN_OLD_CONFIRMATION_BLIND_VALIDATION FV_SEALED PROSPECTIVE_SEALED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    now = datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S%z")
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    v4_fp_before = _fingerprint(V4_SRC)
    gate = identity_gate()
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    if not gate.get("ok"):
        report = {
            "program_id": PROGRAM_ID,
            "analysis_id": ANALYSIS_ID,
            "created_at": now,
            "identity": gate,
            "OLD_CONFIRMATION_OPENED": False,
            "decision": {"VERDICT": CASE_HASH, "NEXT": NEXT_STOP, "reason": "identity_mismatch"},
            "adjudication": {"DEVELOPMENT_EXPOSED_AFTER_REVIEW": False, "failure_cases": [{"kind": "IDENTITY", **gate}]},
            "precommit": {},
            "eligibility": [],
            "candidate_days": [],
            "hidden1m": {},
            "safety": _safety(opened=False, leak=False),
            "hashes": {
                "machine_sha": gate.get("machine_sha"),
                "expected_machine_sha": EXPECTED_MACHINE_SHA256,
                "source_inventory_sha": gate.get("source_inventory_sha"),
                "expected_source_inventory_sha": EXPECTED_SOURCE_INVENTORY_SHA256,
            },
        }
        return _finish(report)

    bind = bind_prior()
    if not bind.get("ok"):
        report = {
            "program_id": PROGRAM_ID,
            "analysis_id": ANALYSIS_ID,
            "created_at": now,
            "identity": gate,
            "OLD_CONFIRMATION_OPENED": False,
            "decision": {"VERDICT": CASE_HASH, "NEXT": NEXT_STOP, "reason": "bind_failed"},
            "adjudication": {"DEVELOPMENT_EXPOSED_AFTER_REVIEW": False, "failure_cases": [{"kind": "BIND", "reason": bind.get("reason")}]},
            "precommit": {},
            "eligibility": [],
            "candidate_days": [],
            "hidden1m": {},
            "safety": _safety(opened=False, leak=False),
        }
        return _finish(report)

    part = partition_dates(bind)
    symbols = list(bind.get("symbols") or [])
    pre = blind_precommit(
        machine_sha=str(gate.get("machine_sha")),
        source_inventory_sha=str(gate.get("source_inventory_sha")),
        confirmation_n=int(part.get("confirmation_n") or 0),
        lookback_n=int(part.get("lookback_n") or 0),
        lookback_first=part.get("lookback_first"),
        lookback_last=part.get("lookback_last"),
        confirmation_first=part.get("confirmation_first"),
        confirmation_last=part.get("confirmation_last"),
        symbol_n=len(symbols),
    )
    (CACHE / "precommit.json").write_text(json.dumps(pre, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PRECOMMIT_SHA256 {pre.get('PRECOMMIT_SHA256')}", flush=True)
    if not part.get("ok"):
        report = {
            "program_id": PROGRAM_ID,
            "analysis_id": ANALYSIS_ID,
            "created_at": now,
            "identity": gate,
            "OLD_CONFIRMATION_OPENED": False,
            "decision": {"VERDICT": CASE_FAIL, "NEXT": NEXT_STOP, "reason": part.get("reason")},
            "adjudication": {"DEVELOPMENT_EXPOSED_AFTER_REVIEW": False, "failure_cases": [{"kind": "DATE_PARTITION", **part}]},
            "precommit": pre,
            "eligibility": [],
            "candidate_days": [],
            "hidden1m": {},
            "safety": _safety(opened=False, leak=False),
            "partition": {k: v for k, v in part.items() if "dates" not in k or k in ("lookback_n", "confirmation_n")},
        }
        return _finish(report)

    from research.pb1_v4_frozen_old_confirmation_blind_validation.walk_conf import (
        eligibility_rows,
        filter_confirmation,
        open_confirmation_minutes,
        walk_frozen,
    )

    loaded = open_confirmation_minutes(
        symbols=symbols,
        lookback_dates=list(part.get("lookback_dates") or []),
        confirmation_dates=list(part.get("confirmation_dates") or []),
        frozen_validation_dates=list(part.get("frozen_validation_dates") or []),
    )
    if not loaded.get("ok"):
        report = {
            "program_id": PROGRAM_ID,
            "analysis_id": ANALYSIS_ID,
            "created_at": now,
            "identity": gate,
            "OLD_CONFIRMATION_OPENED": bool(loaded.get("OLD_CONFIRMATION_OPENED")),
            "decision": {"VERDICT": CASE_FAIL, "NEXT": NEXT_STOP, "reason": loaded.get("reason")},
            "adjudication": {
                "DEVELOPMENT_EXPOSED_AFTER_REVIEW": bool(loaded.get("OLD_CONFIRMATION_OPENED")),
                "failure_cases": [{"kind": "LOAD", "reason": loaded.get("reason")}],
            },
            "precommit": pre,
            "eligibility": [],
            "candidate_days": [],
            "hidden1m": {},
            "safety": _safety(opened=bool(loaded.get("OLD_CONFIRMATION_OPENED")), leak=bool(loaded.get("fv_hit"))),
        }
        return _finish(report)

    minutes = loaded["minutes"]
    elig = eligibility_rows(
        minutes=minutes,
        symbols=symbols,
        confirmation_dates=list(part.get("confirmation_dates") or []),
    )
    print(
        f"ELIGIBLE_SESSIONS {sum(1 for r in elig if r.get('ok'))}/{len(elig)}",
        flush=True,
    )
    walked = walk_frozen(
        bind=bind,
        minutes=minutes,
        walk_dates=list(part.get("walk_dates") or []),
        symbols=symbols,
    )
    if not walked.get("ok"):
        report = {
            "program_id": PROGRAM_ID,
            "analysis_id": ANALYSIS_ID,
            "created_at": now,
            "identity": gate,
            "OLD_CONFIRMATION_OPENED": True,
            "DEVELOPMENT_EXPOSED_AFTER_REVIEW": True,
            "decision": {"VERDICT": CASE_FAIL, "NEXT": NEXT_STOP, "reason": walked.get("reason") or "walk_failed"},
            "adjudication": {"DEVELOPMENT_EXPOSED_AFTER_REVIEW": True, "failure_cases": [{"kind": "WALK", "reason": walked.get("reason")}]},
            "precommit": pre,
            "eligibility": elig,
            "candidate_days": [],
            "hidden1m": {},
            "safety": _safety(opened=True, leak=False),
        }
        return _finish(report)

    (CACHE / "walked_meta.json").write_text(
        json.dumps(
            {
                "ok": True,
                "funnel_n": len(walked.get("funnel_days") or []),
                "setup_n": len(walked.get("setups") or []),
                "e0_n": len(walked.get("e0_events") or []),
                "e1_n": len(walked.get("e1_events") or []),
                "counts": walked.get("counts"),
                "same_bar_entry_n": walked.get("same_bar_entry_n"),
                "HIDDEN_1M_THESIS_PARITY": walked.get("HIDDEN_1M_THESIS_PARITY"),
            },
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
    sliced = filter_confirmation(walked, confirmation_dates=list(part.get("confirmation_dates") or []))
    leak = bool(sliced.get("leak_dates")) or bool(loaded.get("fv_hit")) or bool(loaded.get("prospective_hit"))
    adj = adjudicate(sliced=sliced, eligibility=elig, leak_holdout=leak)
    scans = scan_v4_and_harness(Path(__file__).resolve().parent)
    v4_fp_after = _fingerprint(V4_SRC)
    if v4_fp_after != v4_fp_before:
        adj["ok"] = False
        adj["VERDICT"] = CASE_FAIL
        adj["failure_cases"] = list(adj.get("failure_cases") or []) + [{"kind": "V4_SOURCE_MUTATED"}]
        adj["new_version_required"] = True
    after = snapshot(phase="POST")
    iso = advanced(before, after)
    safety = _safety(opened=True, leak=leak)
    safety["V4_fingerprint_unchanged"] = v4_fp_after == v4_fp_before
    safety["harness_scan_ok"] = bool(scans.get("ok"))
    safety["v4_source_sha"] = v4_source_sha256()
    safety["v4_machine_sha"] = v4_machine_sha256()
    safety["inventory_n"] = len(file_inventory())
    safety["source_inventory_sha"] = source_inventory_sha(file_inventory())
    safety["harness_sha"] = harness_sha256()
    report = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": now,
        "frozen_identity": FROZEN_IDENTITY,
        "identity": gate,
        "precommit": pre,
        "partition": {
            "lookback_n": part.get("lookback_n"),
            "lookback_first": part.get("lookback_first"),
            "lookback_last": part.get("lookback_last"),
            "confirmation_n": part.get("confirmation_n"),
            "confirmation_first": part.get("confirmation_first"),
            "confirmation_last": part.get("confirmation_last"),
            "frozen_validation_n": part.get("frozen_validation_n"),
            "lookback_role": part.get("lookback_role"),
        },
        "eligibility": elig,
        "adjudication": adj,
        "hidden1m": sliced.get("hidden1m") or {},
        "candidate_days": [r for r in list(sliced.get("funnel_days") or []) if r.get("WHY_THIS_STOCK")],
        "OLD_CONFIRMATION_OPENED": True,
        "DEVELOPMENT_EXPOSED_AFTER_REVIEW": True,
        "decision": {
            "VERDICT": adj.get("VERDICT") if scans.get("ok") else CASE_FAIL,
            "NEXT": adj.get("NEXT") if (adj.get("ok") and scans.get("ok")) else (adj.get("NEXT") if not adj.get("ok") else NEXT_STOP),
            "scans_ok": bool(scans.get("ok")),
        },
        "safety": safety,
        "hashes": {
            "machine_sha": gate.get("machine_sha"),
            "source_inventory_sha": gate.get("source_inventory_sha"),
            "PRECOMMIT_SHA256": pre.get("PRECOMMIT_SHA256"),
            "v4_source_sha": v4_source_sha256(),
            "harness_sha": harness_sha256(),
            "v4_fingerprint_before": v4_fp_before,
            "v4_fingerprint_after": v4_fp_after,
        },
        "scans": scans,
        "isolation": {"overlap_n": overlap, "capture_delta": iso.get("capture_delta") if isinstance(iso, dict) else None},
        "counts": dict(sliced.get("counts") or {}),
    }
    return _finish(report)


if __name__ == "__main__":
    raise SystemExit(main())
