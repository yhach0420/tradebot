"""Pre-paper certification is bound to the selected activation."""

from __future__ import annotations

import json
from pathlib import Path

from small_paper.paper_full_day_certification import (
    CURRENT_CERT_FAIL,
    CURRENT_CERT_PASS,
    activation_cert_path,
    classify_cert_artifact,
    evaluate_activation_scoped_certification,
)


def _current() -> dict:
    return {
        "activation_id": "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V3",
        "activation_sha": "abc",
        "candidate_name": "EVENT_TIME_IMPULSE_COMPLETE_STRATEGY_FIXED_ENTRY_SUPPORT_CANDIDATE_V1",
        "execution_family": "X1_IMMEDIATE_ASK",
        "session_executor": "FixedSupportX1SessionExecutor",
        "inventory_digest": "inv",
    }


def _write(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_legacy_v1r_fail_is_not_the_current_result(tmp_path: Path):
    legacy = {
        "verdict": "V1R_RUNTIME_PRE_PAPER_CERTIFICATION_FAIL",
        "activation_target": "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G4_5",
        "activation_sha": "eb952288c4be2dcb586db877f09166d9197564b6658fb72525a66f038e41e8e6",
        "failed_tests": ["PM_DIRECT_START", "WINDOW_B_1120_1245"],
    }
    _write(tmp_path / "paper_runtime_full_day_certification.json", legacy)
    current = _current()
    assert classify_cert_artifact(legacy, current) == "DIFFERENT_ACTIVATION_CERTIFICATION"
    assert evaluate_activation_scoped_certification(cert_dir=tmp_path, current=current) == 2


def test_matching_pass(tmp_path: Path):
    current = _current()
    body = {**current, "verdict": CURRENT_CERT_PASS, "failed_tests": []}
    _write(activation_cert_path(current["activation_id"], cert_dir=tmp_path), body)
    assert evaluate_activation_scoped_certification(cert_dir=tmp_path, current=current) == 0


def test_matching_fail_blocks(tmp_path: Path):
    current = _current()
    body = {**current, "verdict": CURRENT_CERT_FAIL, "failed_tests": ["DEMO_X1_LEDGER"]}
    _write(activation_cert_path(current["activation_id"], cert_dir=tmp_path), body)
    assert evaluate_activation_scoped_certification(cert_dir=tmp_path, current=current) == 2


def test_same_id_different_sha_blocks(tmp_path: Path):
    current = _current()
    body = {**current, "activation_sha": "other", "verdict": CURRENT_CERT_PASS, "failed_tests": []}
    _write(activation_cert_path(current["activation_id"], cert_dir=tmp_path), body)
    assert evaluate_activation_scoped_certification(cert_dir=tmp_path, current=current) == 2
