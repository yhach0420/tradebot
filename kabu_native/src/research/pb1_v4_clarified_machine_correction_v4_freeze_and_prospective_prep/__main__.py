"""Freeze V4 and precommit prospective contract. Runtime 0/0/0. No prospective open."""
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
from research.pb1_v4_clarified_machine_correction_v2.definitions import machine_sha256 as correction_v2_sha256
from research.pb1_v4_clarified_machine_correction_v3.definitions import machine_sha256 as correction_v3_sha256
from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.spec import source_sha256 as v4_source_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import (
    ANALYSIS_ID,
    EXPECTED_SPEC_SHA,
    EXPECTED_V2_SHA,
    EXPECTED_V3_SHA,
    EXPECTED_V4_MACHINE_SHA256,
    EXPECTED_V4_SOURCE_SHA256,
    PROGRAM_ID,
)
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.analyze import decide
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.bind import bind_prior
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.inventory import freeze_identity
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.isolation import (
    CACHE,
    CLARIFIED_SPEC_SRC,
    OUT,
    READY_SPEC_SRC,
    V2_SRC,
    V3_SRC,
    V4_SRC,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.ledger import build_ledger
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.nbar import gate0
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.precommit import prospective_precommit
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.publish import (
    SHEET_ORDER,
    build_answers,
    build_sheets,
    write_artifacts,
)
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.spec import source_sha256
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256

JST = ZoneInfo("Asia/Tokyo")


def _fingerprint(root: Path, *, py_only: bool = False) -> str:
    h = hashlib.sha256()
    if not root.is_dir() and not root.is_file():
        return ""
    if root.is_file():
        h.update(root.read_bytes())
        return h.hexdigest()
    if py_only:
        files = sorted(p for p in root.glob("*.py") if p.is_file())
    else:
        files = sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    for p in files:
        rel = p.relative_to(root)
        h.update(str(rel).encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def _safety(*, nbar: dict[str, Any], frozen: bool) -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "SPEC_CHANGED": False,
        "V4_CHANGED": False,
        "V4_FROZEN": bool(frozen),
        "OLD_CONFIRMATION_OPENED": False,
        "FROZEN_VALIDATION_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "FUTURE_OUTCOME_USED": False,
        "PNL_USED": False,
        "MFE_MAE_USED": False,
        "THRESHOLD_RETUNED": False,
        "NEW_NUMERIC_CUTOFF_ADDED": False,
        "N_BAR_EXPIRY_USED": bool(nbar.get("N_BAR_EXPIRY_USED")),
        "HIDDEN_N_BAR_DEATH_PATH_FOUND": bool(nbar.get("HIDDEN_N_BAR_DEATH_PATH_FOUND")),
        "GRID_SEARCH": False,
        "submit/cancel/live": "0/0/0",
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    set_research_priority_below_normal()
    print("SAFETY submit/cancel/live=0/0/0 PB1_V4_FREEZE_AND_PROSPECTIVE_PREP FROZEN_VAL CLOSED CONFIRMATION CLOSED PROSPECTIVE CLOSED", flush=True)
    print(f"PROGRAM_ID {PROGRAM_ID}", flush=True)
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or before.get("ACTIVE_CAPTURE_PATH") or ""),
        str((before.get("paper") or {}).get("session_dir") or before.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    fps = {
        k: _fingerprint(p, py_only=bool(k.endswith("_src")))
        for k, p in {
            "v4_src": V4_SRC,
            "v3_src": V3_SRC,
            "v2_src": V2_SRC,
            "clarified_src": CLARIFIED_SPEC_SRC,
            "ready_src": READY_SPEC_SRC,
        }.items()
    }
    bind = bind_prior()
    if not bind.get("ok"):
        raise RuntimeError(f"BIND_FAILED {bind.get('reason')}")
    if spec_sha256() != EXPECTED_SPEC_SHA:
        raise RuntimeError("SPEC_SHA_CHANGED")
    if v4_machine_sha256() != EXPECTED_V4_MACHINE_SHA256:
        raise RuntimeError("V4_MACHINE_SHA_CHANGED")
    if v4_source_sha256() != EXPECTED_V4_SOURCE_SHA256:
        raise RuntimeError("V4_SOURCE_SHA_CHANGED")
    if correction_v2_sha256() != EXPECTED_V2_SHA:
        raise RuntimeError("CORRECTION_V2_SHA_CHANGED")
    if correction_v3_sha256() != EXPECTED_V3_SHA:
        raise RuntimeError("CORRECTION_V3_SHA_CHANGED")
    frozen_at = datetime.now(JST).isoformat()
    nbar = gate0()
    print(
        f"GATE0 ok={nbar.get('ok')} N_BAR_EXPIRY_USED={nbar.get('N_BAR_EXPIRY_USED')} "
        f"HIDDEN={nbar.get('HIDDEN_N_BAR_DEATH_PATH_FOUND')} EXEC_DEATH={nbar.get('EXECUTABLE_DEATH_CONDITION')}",
        flush=True,
    )
    identity = freeze_identity(frozen_at=frozen_at)
    ledger = build_ledger()
    print(f"LEDGER n={ledger.get('CONTAMINATED_SYMBOL_DATE_N')} ok={ledger.get('ok')} 88={ledger.get('semantic_88_n')}", flush=True)
    precommit = prospective_precommit(
        frozen_at=frozen_at,
        machine_sha=str(identity.get("machine_sha") or ""),
        source_inventory_sha=str(identity.get("source_inventory_sha") or ""),
        ledger_n=int(ledger.get("CONTAMINATED_SYMBOL_DATE_N") or 0),
    )
    decision = decide(bind_ok=bool(bind.get("ok")), nbar=nbar, identity=identity, ledger=ledger)
    fps_after = {
        k: _fingerprint(p, py_only=bool(k.endswith("_src")))
        for k, p in {
            "v4_src": V4_SRC,
            "v3_src": V3_SRC,
            "v2_src": V2_SRC,
            "clarified_src": CLARIFIED_SPEC_SRC,
            "ready_src": READY_SPEC_SRC,
        }.items()
    }
    if fps != fps_after:
        raise RuntimeError(f"PRIOR_MUTATED { {k: fps[k] != fps_after[k] for k in fps} }")
    if v4_machine_sha256() != EXPECTED_V4_MACHINE_SHA256:
        raise RuntimeError("V4_MACHINE_SHA_CHANGED_AFTER")
    after = snapshot(phase="POST")
    safety = _safety(nbar=nbar, frozen=bool(decision.get("V4_FROZEN")))
    report = {
        "PROGRAM_ID": PROGRAM_ID,
        "ANALYSIS_ID": ANALYSIS_ID,
        "hashes": {
            "PREP_SOURCE_SHA256": source_sha256(),
            "SPEC_SHA256": spec_sha256(),
            "PB1_V4_CLARIFIED_MACHINE_CORRECTION_V4_SHA256": v4_machine_sha256(),
            "PB1_V4_SOURCE_SHA256": v4_source_sha256(),
            "CORRECTION_V2_SHA256": EXPECTED_V2_SHA,
            "CORRECTION_V3_SHA256": EXPECTED_V3_SHA,
            "PRECOMMIT_SHA256": precommit.get("PRECOMMIT_SHA256"),
            "SOURCE_INVENTORY_SHA256": identity.get("source_inventory_sha"),
        },
        "bind": bind,
        "nbar": nbar,
        "identity": identity,
        "ledger": {k: v for k, v in ledger.items() if k != "rows"} | {"rows": list(ledger.get("rows") or [])},
        "precommit": precommit,
        "decision": decision,
        "safety": safety,
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "DISCOVERY_TIMESTAMP": frozen_at,
        "spec_changed": False,
        "v4_changed": False,
        "prospective_event_consumed": False,
        "future_economic_outcome_used": False,
        "parent_fingerprints_unchanged": fps == fps_after,
    }
    report["answers"] = build_answers(report)
    CACHE.mkdir(parents=True, exist_ok=True)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    if tuple(sheets.keys()) != SHEET_ORDER:
        raise RuntimeError("SHEET_ORDER_MISMATCH")
    write_artifacts(report, sheets)
    print(f"OUT {OUT}", flush=True)
    print(f"VERDICT {decision.get('VERDICT')}", flush=True)
    print(f"NEXT {decision.get('NEXT')}", flush=True)
    ans = dict(report.get("answers") or {})
    for k in (
        "SPEC_CHANGED",
        "V4_CHANGED",
        "V4_FROZEN",
        "OLD_CONFIRMATION_OPENED",
        "FROZEN_VALIDATION_OPENED",
        "PROSPECTIVE_DATA_OPENED",
        "FUTURE_OUTCOME_USED",
        "PNL_USED",
        "MFE_MAE_USED",
        "THRESHOLD_RETUNED",
        "NEW_NUMERIC_CUTOFF_ADDED",
        "N_BAR_EXPIRY_USED",
        "HIDDEN_N_BAR_DEATH_PATH_FOUND",
        "submit/cancel/live",
    ):
        print(f"{k} = {ans.get(k)}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
