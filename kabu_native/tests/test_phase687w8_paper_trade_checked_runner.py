"""Phase687W8 — One-command Paper Trade checked runner tests (artifact isolation)."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from small_paper.paper_trade_checked_runner import (
    EXISTING_PAPER_BAT_SHA256_BASELINE,
    PaperTradeCheckedRunner,
    VERDICT_POST,
    VERDICT_READY,
    default_pythonpath,
    existing_paper_bat_sha256,
    is_excluded_forward_path,
    qualify_snapshot_path,
    redact_secrets,
    trading_date_jst,
    write_live_forward_session_fixture,
    write_qualified_session_fixture,
    write_synthetic_session_fixture,
)

REPO = Path(__file__).resolve().parents[2]
NATIVE = Path(__file__).resolve().parents[1]
PAPER_BAT = REPO / "run_paper_trade.bat"
CFG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"


@pytest.fixture(autouse=True)
def _isolate_w8_from_leaked_cert_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "TRADEBOT_CERTIFICATION_MODE",
        "TRADEBOT_CERTIFICATION_RUN_ID",
        "TRADEBOT_CERT_STAGE_RUN_ID",
        "TRADEBOT_TRADING_DATE",
        "TRADEBOT_SESSION_CLOCK",
        "TRADEBOT_SESSION_CLOCK_V0",
    ):
        monkeypatch.delenv(key, raising=False)


def _design_pass(native: Path) -> None:
    design = (
        native
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design.parent.mkdir(parents=True, exist_ok=True)
    design.write_text(json.dumps({"pass": True, "mismatch_count": 0}), encoding="utf-8")
    retention = native / "results" / "retention"
    retention.mkdir(parents=True, exist_ok=True)
    (retention / "small_paper_retention_baseline.json").write_text(
        json.dumps({"sessions": []}), encoding="utf-8"
    )


def _ok_runner(cmds: dict[str, int] | None = None, paper_code: int = 0, w4s_verdict: str = "FORWARD_SOAK_IN_PROGRESS"):
    table = {
        "disk_guard_report": 0,
        "prebuild_vol_liq": 0,
        "check_kabu_readonly": 0,
        "check_live_pipeline_preflight": 0,
        "run_production_startup_smoke": 0,
        "check_live_order_recovery": 0,
        "check_live_order_design_consistency": 0,
        "check_production_enablement": 0,
        "run_paper_trade.bat": paper_code,
        "phase687w4s": 0,
    }
    if cmds:
        table.update(cmds)
    calls: list[str] = []

    def run(cmd, env, cwd):
        s = cmd if isinstance(cmd, str) else " ".join(cmd)
        calls.append(s)
        if "disk_guard_report" in s:
            return 0, json.dumps({"disk_state": "OK", "disk_usage_pct": 40.0}), ""
        if "phase687w4s" in s:
            return (
                table.get("phase687w4s", 0),
                json.dumps(
                    {
                        "verdict": w4s_verdict,
                        "aggregate": {
                            "session_count": 99,
                            "readonly_success_sessions": 99,
                            "mapping_loss_total": 0,
                            "duplicate_intent_total": 0,
                            "reservation_leak_total": 0,
                            "submit_total": 0,
                            "cancel_total": 0,
                        },
                    }
                ),
                "",
            )
        for key, code in table.items():
            if key in s:
                return code, "{}\n", ""
        return 0, "", ""

    return run, calls


def test_jst_date_not_fixed():
    assert len(trading_date_jst()) == 8


def test_pythonpath_auto():
    assert "src" in default_pythonpath()


def test_existing_bat_unmodified():
    assert existing_paper_bat_sha256(PAPER_BAT).lower() == EXISTING_PAPER_BAT_SHA256_BASELINE.lower()


def test_wrapper_files_exist():
    assert (REPO / "run_paper_trade_checked.bat").is_file()
    assert (NATIVE / "scripts" / "run_paper_trade_checked.ps1").is_file()


def test_redact_secrets():
    assert "secret123" not in redact_secrets("password=secret123")


def test_w8_test_qualified_three_forward_zero(tmp_path: Path):
    for i in range(3):
        write_qualified_session_fixture(tmp_path / "results" / "_w8_test_qualified" / f"s{i}", session_id=f"T{i}")
    run, _ = _ok_runner()
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, skip_paper=True, skip_w4s=True, config_path=CFG)
    r.paper_exit_code = 0
    post = r.step_post_session(paper_ok=True)
    assert post["forward_qualified_session_count"] == 0
    assert post["test_qualified_session_count"] == 3
    assert post["sessions_collected"] == 0
    assert post["w4s_verdict"] == "NOT_RUN"


def test_synthetic_three_forward_zero(tmp_path: Path):
    for i in range(3):
        write_synthetic_session_fixture(tmp_path / "results" / "synthetic" / f"s{i}", session_id=f"SY{i}")
    run, _ = _ok_runner()
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, skip_paper=True, skip_w4s=True, config_path=CFG)
    r.paper_exit_code = 0
    post = r.step_post_session(paper_ok=True)
    assert post["forward_qualified_session_count"] == 0
    assert post["synthetic_qualified_session_count"] == 3
    assert post["sessions_collected"] == 0


def test_fixture_three_forward_zero(tmp_path: Path):
    for i in range(3):
        write_qualified_session_fixture(tmp_path / "results" / "fixture" / f"s{i}", session_id=f"F{i}")
    run, _ = _ok_runner()
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, skip_paper=True, skip_w4s=True, config_path=CFG)
    r.paper_exit_code = 0
    post = r.step_post_session(paper_ok=True)
    assert post["forward_qualified_session_count"] == 0
    assert post["test_qualified_session_count"] == 3
    assert post["sessions_collected"] == 0


def test_w4s_skipped_sessions_collected_zero(tmp_path: Path):
    write_live_forward_session_fixture(tmp_path / "results" / "paper_sessions" / "live1", session_id="L1")
    run, _ = _ok_runner()
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, skip_paper=True, skip_w4s=True, config_path=CFG)
    r.paper_exit_code = 0
    post = r.step_post_session(paper_ok=True)
    assert post["w4s_verdict"] == "NOT_RUN"
    assert post["sessions_collected"] == 0
    assert post["w4s_call_count"] == 0


def test_w4s_unknown_sessions_collected_zero(tmp_path: Path):
    write_live_forward_session_fixture(tmp_path / "results" / "paper_sessions" / "live1", session_id="L1")
    run, _ = _ok_runner(w4s_verdict="UNKNOWN")
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, skip_paper=True, skip_w4s=False, config_path=CFG)
    r.paper_exit_code = 0
    post = r.step_post_session(paper_ok=True)
    assert post["w4s_verdict"] == "UNKNOWN"
    assert post["sessions_collected"] == 0
    assert post["counted_as_forward_session"] is False


def test_live_seal_14_14_counts_one_when_w4s_runs(tmp_path: Path):
    live_root = NATIVE / "results" / "paper_sessions" / "iso_live_ok"
    test_root = NATIVE / "results" / "_w8_test_qualified" / "iso_mix"
    if live_root.exists():
        shutil.rmtree(live_root, ignore_errors=True)
    if test_root.exists():
        shutil.rmtree(test_root, ignore_errors=True)
    try:
        write_live_forward_session_fixture(live_root, session_id="LIVE")
        for i in range(3):
            write_qualified_session_fixture(test_root / f"t{i}", session_id=f"T{i}")
        run, _ = _ok_runner(w4s_verdict="READONLY_SOAK_IN_PROGRESS")
        r = PaperTradeCheckedRunner(native_root=NATIVE, run_command=run, skip_paper=True, skip_w4s=False, config_path=CFG)
        r.paper_exit_code = 0
        post = r.step_post_session(paper_ok=True)
        assert post["forward_qualified_session_count"] >= 1
        assert post["test_qualified_session_count"] >= 3
        assert post["sessions_collected"] >= 1
        # Must not count the 3 test fixtures as forward
        assert post["sessions_collected"] == post["forward_qualified_session_count"]
        assert post["counted_as_forward_session"] is True
        assert post["result"] == "OK"
    finally:
        shutil.rmtree(live_root, ignore_errors=True)
        shutil.rmtree(test_root, ignore_errors=True)


def test_mixed_roots_only_live_counts(tmp_path: Path):
    base = NATIVE / "results" / "paper_sessions" / "iso_mix_root"
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    try:
        write_live_forward_session_fixture(base / "a", session_id="A")
        write_qualified_session_fixture(NATIVE / "results" / "fixture" / "iso_b", session_id="B")
        write_synthetic_session_fixture(NATIVE / "results" / "synthetic" / "iso_c", session_id="C")
        run, _ = _ok_runner(w4s_verdict="READONLY_SOAK_IN_PROGRESS")
        r = PaperTradeCheckedRunner(native_root=NATIVE, run_command=run, skip_paper=True, skip_w4s=False, config_path=CFG)
        r.paper_exit_code = 0
        post = r.step_post_session(paper_ok=True)
        assert post["forward_qualified_session_count"] >= 1
        assert post["sessions_collected"] == post["forward_qualified_session_count"]
        assert post["synthetic_qualified_session_count"] >= 1
        assert post["test_qualified_session_count"] >= 1
    finally:
        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(NATIVE / "results" / "fixture" / "iso_b", ignore_errors=True)
        shutil.rmtree(NATIVE / "results" / "synthetic" / "iso_c", ignore_errors=True)


def test_path_exclusion_markers():
    assert is_excluded_forward_path(Path("results/_w8_test_qualified/s1/x.json"))[0] is True
    assert is_excluded_forward_path(Path("results/fixture/s1/x.json"))[0] is True
    assert is_excluded_forward_path(Path("results/synthetic/s1/x.json"))[0] is True
    assert is_excluded_forward_path(Path("results/reports/phase687w8/x.json"))[0] is True
    assert is_excluded_forward_path(Path("results/paper_sessions/live/soak.json"))[0] is False


def test_provenance_missing_not_forward(tmp_path: Path):
    snap = write_qualified_session_fixture(tmp_path / "results" / "paper_sessions" / "noprov", session_id="NP")
    # strip provenance to simulate old artifact
    data = json.loads(snap.read_text(encoding="utf-8"))
    for k in ("session_provenance", "synthetic", "fixture", "test_mode", "runtime_session"):
        data.pop(k, None)
    snap.write_text(json.dumps(data), encoding="utf-8")
    man = snap.parent / "session_manifest.json"
    md = json.loads(man.read_text(encoding="utf-8"))
    for k in ("session_provenance", "synthetic", "fixture", "test_mode", "runtime_session"):
        md.pop(k, None)
    man.write_text(json.dumps(md), encoding="utf-8")
    q = qualify_snapshot_path(snap, paper_exit_code=0)
    assert q["seal_qualified"] is True
    assert q["forward_qualified"] is False


def _harness(**kwargs):
    """W9-safe defaults: synthetic capture + operator-stop (no 15:35 wait)."""
    kw = {"capture_synthetic": True, "skip_capture_wait": True, "skip_w4s": True}
    kw.update(kwargs)
    return kw


def test_cache_fail_blocks_paper(tmp_path: Path):
    run, calls = _ok_runner({"prebuild_vol_liq": 1})
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    assert r.paper_call_count == 0
    assert r.paper_blocked_capture_continues is True


def test_readonly_fail_blocks_paper(tmp_path: Path):
    run, _ = _ok_runner({"check_kabu_readonly": 2})
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    assert r.paper_call_count == 0


def test_preflight_fail_blocks_paper(tmp_path: Path):
    run, _ = _ok_runner({"check_live_pipeline_preflight": 1})
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    assert r.paper_call_count == 0
    assert r.paper_blocked_capture_continues is True


def test_smoke_fail_blocks_paper(tmp_path: Path):
    run, _ = _ok_runner({"run_production_startup_smoke": 1})
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    assert r.paper_call_count == 0
    assert r.paper_blocked_capture_continues is True


def test_recovery_fail_blocks_paper(tmp_path: Path):
    run, _ = _ok_runner({"check_live_order_recovery": 1})
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    assert r.paper_call_count == 0
    assert r.paper_blocked_capture_continues is True


def test_disk_critical_blocks_paper(tmp_path: Path):
    def run(cmd, env, cwd):
        s = cmd if isinstance(cmd, str) else " ".join(cmd)
        if "disk_guard_report" in s:
            return 0, json.dumps({"disk_state": "CRITICAL", "disk_usage_pct": 91.0}), ""
        return 0, "{}", ""

    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    assert r.paper_call_count == 0


def test_enablement_not_authorized_does_not_block(tmp_path: Path):
    run, _ = _ok_runner({"check_production_enablement": 3})
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness(skip_w4s=True))
    r.run()
    assert r.paper_call_count == 1
    assert r.blocked is None


def test_paper_abnormal_still_runs_postcheck(tmp_path: Path):
    run, calls = _ok_runner(paper_code=7)
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness(skip_w4s=False))
    code = r.run()
    assert code == 7
    assert r.w4s_call_count == 1
    assert r.post_session.get("counted_as_forward_session") is False


def test_no_auto_retry_on_paper_fail(tmp_path: Path):
    run, calls = _ok_runner(paper_code=1)
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    assert sum(1 for c in calls if "run_paper_trade.bat" in c) == 1


def test_skip_w4s_sets_post_verdict_not_ready(tmp_path: Path):
    run, _ = _ok_runner()
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness(skip_w4s=True))
    code = r.run()
    assert r.verdict == VERDICT_POST
    assert r.post_session.get("w4s_verdict") == "NOT_RUN"
    assert r.post_session.get("sessions_collected") == 0
    assert r.post_session.get("counted_as_forward_session") is False
    assert code == 2  # paper ok (skipped) but post incomplete


def test_exit0_seal_missing_fails(tmp_path: Path):
    safety = tmp_path / "results" / "paper_sessions" / "s" / "live_order_safety"
    safety.mkdir(parents=True)
    (safety / "soak_session_snapshot.json").write_text(
        json.dumps({"session_id": "X", "session_provenance": "LIVE_PAPER_RUNTIME", "runtime_session": True}),
        encoding="utf-8",
    )
    run, _ = _ok_runner()
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, skip_paper=True, skip_w4s=True, config_path=CFG)
    r.paper_exit_code = 0
    post = r.step_post_session(paper_ok=True)
    assert post["counted_as_forward_session"] is False
    assert post["sessions_collected"] == 0


def test_seal_14_13_not_forward(tmp_path: Path):
    snap = write_live_forward_session_fixture(tmp_path / "results" / "paper_sessions" / "bad", session_id="B")
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["session_seal_required_count"] = 13
    snap.write_text(json.dumps(data), encoding="utf-8")
    q = qualify_snapshot_path(snap, paper_exit_code=0)
    assert q["seal_qualified"] is False
    assert q["forward_qualified"] is False


def test_submit_cancel_zero(tmp_path: Path):
    run, _ = _ok_runner()
    _design_pass(tmp_path)
    r = PaperTradeCheckedRunner(native_root=tmp_path, run_command=run, **_harness())
    r.run()
    assert r.post_session.get("actual_submit") == 0
    assert r.post_session.get("actual_cancel") == 0
