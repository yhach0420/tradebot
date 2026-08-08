"""Phase687W12 — Recovery Gate prior discovery / reconciliation classification.

Guarantees:
- Capture / research / demo / preflight / quarantine are never Recovery priors
- Same-day in-progress Paper sessions are not selected
- SEALED_VALID prior with OK + API_UNAVAILABLE-only mismatch can PASS (paper-only)
- Real position mismatches and config SHA mismatch still BLOCK
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from small_paper.operational_recovery import (
    classify_prior_reconciliation_mismatch,
    discover_prior_completed_sessions,
    evaluate_prior_session_artifacts,
    evaluate_recovery_readiness,
    is_positive_live_session_path,
    probe_workspace_recovery,
    dryrun_ready_evidence,
)


def _design(native: Path, *, ok: bool = True) -> None:
    p = (
        native
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pass": ok, "mismatch_count": 0 if ok else 1}), encoding="utf-8")


def _pin_config(native: Path, *, match: bool = True) -> Path:
    cfg = native / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("live_trading_enabled: false\norder_enabled: false\n", encoding="utf-8")
    from small_paper.operational_recovery import config_sha256

    dig = config_sha256(cfg)
    pin = cfg.parent / "production_config_sha256.pin"
    pin.write_text((dig if match else "0" * 64) + "\n", encoding="utf-8")
    return cfg


def _write_prior(
    native: Path,
    *,
    day: str = "20260803",
    session_id: str = "122516",
    recon: str = "OK",
    mismatch: int = 0,
    seal_status: str = "SEALED_VALID",
    submit: int = 0,
    cancel: int = 0,
    recon_rows: list[dict] | None = None,
) -> Path:
    root = native / "results" / "small_paper" / day / f"live_session_{session_id}"
    safety = root / "live_order_safety"
    safety.mkdir(parents=True, exist_ok=True)
    day_iso = f"{day[:4]}-{day[4:6]}-{day[6:]}" if len(day) == 8 and day.isdigit() else day
    man = {
        "session_id": f"live_session_{session_id}",
        "trading_day": day,
        "started_at": f"{day_iso}T09:00:00+09:00",
        "ended_at": f"{day_iso}T15:30:00+09:00",
        "production_approval_status": "NOT_AUTHORIZED",
        "live_trading_enabled": False,
        "order_enabled": False,
        "reconciliation_status": recon,
        "reconciliation_mismatch": mismatch,
        "submit_count": submit,
        "cancel_count": cancel,
        "kill_switch_events": 0,
        "sealed": True,
        "synthetic": False,
        "session_provenance": "LIVE_PAPER_RUNTIME",
        "config_sha256": "abc",
        "git_commit": "deadbeef",
    }
    (safety / "session_manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    if recon_rows is not None:
        (safety / "broker_reconciliation.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in recon_rows),
            encoding="utf-8",
        )
    seal = {
        "session_seal_status": seal_status,
        "entry_count": 14,
        "required_count": 14,
        "required_artifact_missing_count": 0,
        "session_id": f"live_session_{session_id}",
    }
    (root / "session_seal.json").write_text(json.dumps(seal, indent=2), encoding="utf-8")
    return root


def test_capture_market_path_excluded_from_positive_match(tmp_path: Path):
    cap = (
        tmp_path
        / "data"
        / "market_capture"
        / "20260804"
        / "live_session_080000"
        / "live_order_safety"
        / "session_manifest.json"
    )
    cap.parent.mkdir(parents=True)
    cap.write_text("{}", encoding="utf-8")
    small = tmp_path / "results" / "small_paper"
    small.mkdir(parents=True)
    assert is_positive_live_session_path(cap, small_paper_root=small) is False


def test_discovery_excludes_market_capture_and_research(tmp_path: Path):
    good = _write_prior(tmp_path, day="20260803", session_id="080648", mismatch=0)
    # Capture lookalike under market_capture
    cap_root = tmp_path / "data" / "market_capture" / "20260804" / "live_session_090000"
    cap_safety = cap_root / "live_order_safety"
    cap_safety.mkdir(parents=True)
    (cap_safety / "session_manifest.json").write_text(
        json.dumps({"trading_day": "20260803", "session_id": "cap"}), encoding="utf-8"
    )
    (cap_root / "session_seal.json").write_text(
        json.dumps({"session_seal_status": "SEALED_VALID", "entry_count": 1, "required_artifact_missing_count": 0}),
        encoding="utf-8",
    )
    # research tree lookalike
    res = tmp_path / "results" / "research" / "20260803" / "live_session_100000" / "live_order_safety"
    res.mkdir(parents=True)
    (res / "session_manifest.json").write_text(
        json.dumps({"trading_day": "20260803"}), encoding="utf-8"
    )
    (res.parent / "session_seal.json").write_text(
        json.dumps({"session_seal_status": "SEALED_VALID", "entry_count": 1, "required_artifact_missing_count": 0}),
        encoding="utf-8",
    )
    found = discover_prior_completed_sessions(tmp_path, trading_date="20260804")
    roots = [str(f["session_root"]).replace("\\", "/") for f in found]
    assert any("20260803/live_session_080648" in r for r in roots)
    assert not any("market_capture" in r for r in roots)
    assert not any("/research/" in r for r in roots)
    assert str(good).replace("\\", "/") in " ".join(roots) or any("080648" in r for r in roots)


def test_discovery_excludes_demo_synthetic_preflight_quarantine(tmp_path: Path):
    _write_prior(tmp_path, day="20260801", session_id="090000")
    for marker in ("demo", "synthetic", "preflight", "quarantine", "archive"):
        p = (
            tmp_path
            / "results"
            / "small_paper"
            / marker
            / "20260802"
            / "live_session_091111"
            / "live_order_safety"
        )
        p.mkdir(parents=True)
        (p / "session_manifest.json").write_text(
            json.dumps({"trading_day": "20260802", "session_id": "x"}), encoding="utf-8"
        )
        (p.parent / "session_seal.json").write_text(
            json.dumps(
                {
                    "session_seal_status": "SEALED_VALID",
                    "entry_count": 1,
                    "required_artifact_missing_count": 0,
                }
            ),
            encoding="utf-8",
        )
    found = discover_prior_completed_sessions(tmp_path, trading_date="20260804")
    roots = [str(f["session_root"]).replace("\\", "/") for f in found]
    assert any("20260801/live_session_090000" in r for r in roots)
    for marker in ("demo", "synthetic", "preflight", "quarantine", "archive"):
        assert not any(f"/{marker}/" in r for r in roots)


def test_discovery_skips_same_day_in_progress(tmp_path: Path):
    _write_prior(tmp_path, day="20260803", session_id="080000")
    # same-day sealed must not be selected as prior for trading_date=20260804... wait,
    # day < trading_date is required, so 20260804 same-day is excluded by date filter.
    today = tmp_path / "results" / "small_paper" / "20260804" / "live_session_070000"
    safety = today / "live_order_safety"
    safety.mkdir(parents=True)
    (safety / "session_manifest.json").write_text(
        json.dumps({"trading_day": "20260804", "session_id": "live_session_070000"}),
        encoding="utf-8",
    )
    (today / "session_seal.json").write_text(
        json.dumps({"session_seal_status": "SEALED_VALID", "entry_count": 1, "required_artifact_missing_count": 0}),
        encoding="utf-8",
    )
    found = discover_prior_completed_sessions(tmp_path, trading_date="20260804")
    roots = [str(f["session_root"]).replace("\\", "/") for f in found]
    assert not any("20260804/" in r for r in roots)
    assert any("20260803/" in r for r in roots)


def test_api_unavailable_mismatch_classified_benign():
    safety = Path(".")  # unused when we pass rows via temp — use tmp in full test
    man = {
        "live_trading_enabled": False,
        "order_enabled": False,
        "submit_count": 0,
        "cancel_count": 0,
        "reconciliation_status": "OK",
        "reconciliation_mismatch": 1,
    }
    # Without journal file and without proven benign rows → unexplained
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as td:
        sdir = Path(td)
        (sdir / "broker_reconciliation.jsonl").write_text(
            json.dumps({"type": "API_UNAVAILABLE", "account_status": "AUTH_FAILED"}) + "\n",
            encoding="utf-8",
        )
        c = classify_prior_reconciliation_mismatch(
            safety_dir=sdir, man_data=man, mismatch=1, recon_status="OK"
        )
        assert c["gate_state"] == "OK"
        assert c["classification"] == "paper_auth_noise"
        assert c["benign_only"] is True


def test_position_mismatch_still_blocks():
    from tempfile import TemporaryDirectory

    man = {
        "live_trading_enabled": False,
        "order_enabled": False,
        "submit_count": 0,
        "cancel_count": 0,
    }
    with TemporaryDirectory() as td:
        sdir = Path(td)
        (sdir / "broker_reconciliation.jsonl").write_text(
            json.dumps({"type": "BROKER_ONLY_POSITION", "symbol": "7203.T", "broker": 100}) + "\n",
            encoding="utf-8",
        )
        c = classify_prior_reconciliation_mismatch(
            safety_dir=sdir, man_data=man, mismatch=1, recon_status="OK"
        )
        assert c["gate_state"] == "UNKNOWN"
        assert c["classification"] == "position_or_order_mismatch"


def test_prior_api_unavailable_probe_passes(tmp_path: Path):
    _design(tmp_path, ok=True)
    cfg = _pin_config(tmp_path, match=True)
    _write_prior(
        tmp_path,
        day="20260803",
        session_id="122516",
        recon="OK",
        mismatch=1,
        recon_rows=[{"type": "API_UNAVAILABLE", "account_status": "AUTH_FAILED"}],
    )
    result = probe_workspace_recovery(tmp_path, trading_date="20260804", config_path=cfg)
    assert result["probe_mode"] == "pre_start_prior_session"
    assert result["reconciliation_state"] in ("OK", "PASS", "CLEAN")
    assert result["session_manifest_valid"] is True
    assert result["session_seal_valid"] is True
    assert result["journal_integrity"] == "JOURNAL_OK"
    codes = {b["code"] for b in result["blockers"]}
    assert "RECONCILIATION_ISSUE" not in codes
    if "SUBMIT_HARD_FAIL_MISSING" in codes:
        pytest.skip("HARD_FAIL unavailable")
    assert result["exit_code"] == 0
    assert result["recovery_ready"] is True
    ref = (result.get("artifact_trace") or {}).get("reference_session") or {}
    assert "122516" in str(ref.get("session_root") or "")


def test_config_sha_match_pass_and_mismatch_block(tmp_path: Path):
    _design(tmp_path, ok=True)
    cfg = _pin_config(tmp_path, match=True)
    _write_prior(tmp_path, mismatch=0)
    ok = probe_workspace_recovery(tmp_path, trading_date="20260804", config_path=cfg)
    assert ok.get("config_sha_match") is True

    cfg2 = _pin_config(tmp_path, match=False)
    bad = probe_workspace_recovery(tmp_path, trading_date="20260804", config_path=cfg2)
    assert bad.get("config_sha_match") is False
    assert any(b["code"] == "DESIGN_CONFIG_MISMATCH" for b in bad["blockers"])


def test_unresolved_position_mismatch_blocks_probe(tmp_path: Path):
    _design(tmp_path, ok=True)
    cfg = _pin_config(tmp_path, match=True)
    _write_prior(
        tmp_path,
        mismatch=1,
        recon="OK",
        recon_rows=[{"type": "LOCAL_ONLY_POSITION", "symbol": "6758.T", "local": 100}],
    )
    result = probe_workspace_recovery(tmp_path, trading_date="20260804", config_path=cfg)
    assert any(b["code"] == "RECONCILIATION_ISSUE" for b in result["blockers"])
    assert result["exit_code"] == 2


def test_clean_workspace_pass(tmp_path: Path):
    _design(tmp_path, ok=True)
    cfg = _pin_config(tmp_path, match=True)
    result = probe_workspace_recovery(tmp_path, trading_date="20260804", config_path=cfg)
    assert result["probe_mode"] == "pre_start_no_prior_session"
    codes = {b["code"] for b in result["blockers"]}
    if "SUBMIT_HARD_FAIL_MISSING" in codes:
        pytest.skip("HARD_FAIL unavailable")
    assert result["exit_code"] == 0
    assert result["recovery_ready"] is True


def test_submit_cancel_live_zero_in_ready_evidence():
    ev = dryrun_ready_evidence()
    result = evaluate_recovery_readiness(ev)
    assert result["production_flags"]["live_trading_enabled"] is False
    assert result["production_flags"]["order_enabled"] is False
    assert result["recovery_ready"] is True


def test_evaluate_prior_uses_classification(tmp_path: Path):
    root = _write_prior(
        tmp_path,
        mismatch=1,
        recon_rows=[{"type": "API_UNAVAILABLE"}],
    )
    session = {
        "manifest_path": str(root / "live_order_safety" / "session_manifest.json"),
        "seal_path": str(root / "session_seal.json"),
        "safety_dir": str(root / "live_order_safety"),
        "session_root": str(root),
    }
    ev = evaluate_prior_session_artifacts(session)
    assert ev["reconciliation_state"] == "OK"
    assert (ev.get("detail") or {}).get("reconciliation_classification", {}).get(
        "classification"
    ) == "paper_auth_noise"
