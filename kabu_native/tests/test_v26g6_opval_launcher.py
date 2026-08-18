"""Off-market tests for the temporary OPVAL one-BAT launcher.

Does not manufacture real PUSH, ENTRY, or FILL.
Does not mutate Candidate-6 pinned runtime bytes.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from small_paper.operational_validation import (
    ENV_CAPTURE_TRADING_DATE,
    ENV_OPVAL_BOUND_TRADING_DATE,
    ENV_OPVAL_MODE,
    ENV_PAPER_TRADING_DATE,
    OPVAL_ACTIVATION_ID,
    OPVAL_LEGACY_ACTIVATION_ID,
    OPVAL_LEGACY_PINNED_DATE,
)
from small_paper.runtime_clock import ENV_ARM_FILE, ENV_CERT_MODE, ENV_ENABLED, ENV_REPLAY_PATH, ENV_SKIP_CERT_GATE, ENV_SPEED
from small_paper.v1r_activation_binding import (
    ENV_ACTIVATION_SELECTOR,
    RUNTIME_DEPENDENCY_RELS,
    SELECTOR_PATH,
    V25_ACTIVATION_ID,
    collect_runtime_inventory,
    file_sha256,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
C6_SHA = "3ac5cf4b1788f52d38aeb0b7ea059f847f89cf4e026c844ec64d96713fa3563d"
LAUNCHER_PY = NATIVE / "scripts" / "run_paper_trade_opval.py"
LAUNCHER_BAT = REPO / "run_paper_trade_opval.bat"
CHECKED_BAT = REPO / "run_paper_trade_checked.bat"
C6_MANIFEST = NATIVE / "results" / "research" / "v1r_exit_v2_prospective_activation" / f"{C6_ID}.json"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("run_paper_trade_opval", LAUNCHER_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def opval():
    return _load_launcher()


def _c6() -> dict:
    return json.loads(C6_MANIFEST.read_text(encoding="utf-8"))


def test_launcher_files_are_outside_candidate6_inventory() -> None:
    listed = {str(r).replace("\\", "/") for r in RUNTIME_DEPENDENCY_RELS}
    assert "src/small_paper/operational_validation.py" in listed
    assert "run_paper_trade_opval.bat" not in listed
    assert "scripts/run_paper_trade_opval.py" not in listed
    assert "kabu_native/scripts/run_paper_trade_opval.py" not in listed
    inv = _c6().get("runtime_file_sha256") or {}
    assert "scripts/run_paper_trade_opval.py" not in inv
    assert LAUNCHER_PY.is_file()
    assert LAUNCHER_BAT.is_file()


def test_candidate6_manifest_unchanged_new_working_source() -> None:
    body = _c6()
    ok, got, calc = verify_manifest_self_sha(body)
    assert ok and got == calc == C6_SHA
    launch = body.get("launch_surface_sha256") or {}
    assert file_sha256(CHECKED_BAT) == launch["run_paper_trade_checked.bat"]
    assert file_sha256(NATIVE / "scripts" / "run_paper_trade_checked.ps1") == launch["run_paper_trade_checked.ps1"]
    assert file_sha256(REPO / "run_paper_trade.bat") == launch["run_paper_trade.bat"]
    assert file_sha256(NATIVE / "scripts" / "run_paper_full_day_certification.py") == launch["run_paper_full_day_certification.py"]
    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    assert v25_sel.get("activation_id") == V25_ACTIVATION_ID
    wt = collect_runtime_inventory(native_root=NATIVE)
    # New OPVAL working source changes operational_validation.py only; C6 JSON stays frozen.
    assert wt["src/small_paper/operational_validation.py"] != body["runtime_file_sha256"]["src/small_paper/operational_validation.py"]
    inv = verify_runtime_inventory(body, native_root=NATIVE)
    assert inv.get("ok") is False


def test_bat_syntax_startup_wiring() -> None:
    text = LAUNCHER_BAT.read_text(encoding="utf-8")
    assert "run_paper_trade_opval.py" in text
    assert "PYTHONPATH" in text
    assert "MARKET_INGRESS_V2" in text
    assert CHECKED_BAT.is_file()
    checked = CHECKED_BAT.read_text(encoding="utf-8")
    assert "run_paper_trade_checked.ps1" in checked
    src = LAUNCHER_PY.read_text(encoding="utf-8")
    assert "apply_non_issuer_env" not in src
    assert "allow-paper-without-capture" not in src
    assert "TRADEBOT_OPERATIONAL_VALIDATION_MODE" in src
    assert "second_token_issuer" in src


def test_clean_env_strips_cert_replay_clock(opval) -> None:
    env = {
        ENV_CERT_MODE: "1",
        ENV_SKIP_CERT_GATE: "1",
        ENV_ENABLED: "1",
        ENV_SPEED: "48",
        ENV_ARM_FILE: "x",
        ENV_REPLAY_PATH: "tape.jsonl",
        "MARKET_INPUT_MODE": "REPLAY",
        "TRADEBOT_DEMO_PUSH_E2E": "1",
        "TRADEBOT_INGRESS_REPLAY_NOT_BEFORE": "09:00",
    }
    cleaned = opval.apply_clean_opval_env(env, trading_date="20260819")
    assert ENV_CERT_MODE not in cleaned
    assert ENV_REPLAY_PATH not in cleaned
    assert ENV_ENABLED not in cleaned
    assert cleaned["TRADEBOT_OPERATIONAL_VALIDATION_MODE"] == "1"
    assert cleaned["MARKET_INPUT_MODE"] == "LIVE"
    assert cleaned["TRADEBOT_TRADING_DATE"] == "20260819"
    assert opval.unsafe_env_blocked_reason(cleaned) == ""


def test_safety_gate_certification_replay_clock(opval) -> None:
    base = {ENV_OPVAL_MODE: "1"}
    assert opval.unsafe_env_blocked_reason({**base, ENV_CERT_MODE: "1"}) == "OPVAL_CERTIFICATION_MODE_FORBIDDEN"
    assert opval.unsafe_env_blocked_reason({**base, ENV_REPLAY_PATH: "t"}) == "OPVAL_REPLAY_PATH_FORBIDDEN"
    assert opval.unsafe_env_blocked_reason({**base, ENV_ENABLED: "1"}) == "OPVAL_SESSION_CLOCK_FORBIDDEN"
    assert opval.unsafe_env_blocked_reason({}) == "OPVAL_MODE_REQUIRED"


def test_opval_selector_binding_and_wrong_identity(opval, tmp_path: Path) -> None:
    selector = json.loads(
        (NATIVE / "results" / "research" / "v26g6_targeted_rca" / "active_v1r_opval_20260817.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = json.loads(
        Path(selector["manifest_relpath"]).read_text(encoding="utf-8")
    )
    env = {ENV_OPVAL_MODE: "1", ENV_ACTIVATION_SELECTOR: str(tmp_path / "sel.json"), "TRADEBOT_TRADING_DATE": "20260819"}
    (tmp_path / "sel.json").write_text(json.dumps(selector), encoding="utf-8")
    # Formal V25 selector must not be used for OPVAL Paper.
    v25 = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    assert (
        opval.paper_contract_blocked_reason(selector=v25, manifest=manifest, environ=env, native_root=NATIVE)
        == "OPVAL_FORMAL_SELECTOR_SUBSTITUTION"
    )
    c6_sel = {"activation_id": C6_ID, "activation_sha": C6_SHA}
    assert (
        opval.paper_contract_blocked_reason(selector=c6_sel, manifest=manifest, environ=env, native_root=NATIVE)
        == "OPVAL_CANDIDATE6_FORBIDDEN"
    )


def test_order_enabled_rejection(opval) -> None:
    selector = {"activation_id": OPVAL_ACTIVATION_ID, "activation_sha": "x"}
    manifest = {
        "manifest_id": OPVAL_ACTIVATION_ID,
        "candidate_status": "OPERATIONAL_VALIDATION_ONLY",
        "order_enabled": True,
        "paper_only": True,
        "submit_cancel_live": "0/0/0",
    }
    env = {ENV_OPVAL_MODE: "1", "TRADEBOT_TRADING_DATE": "20260819"}
    assert (
        opval.paper_contract_blocked_reason(selector=selector, manifest=manifest, environ=env, native_root=NATIVE)
        == "OPVAL_ORDER_ENABLED"
    )


def test_legacy_pin_and_new_identity_isolation(opval, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    monkeypatch.delenv(ENV_CERT_MODE, raising=False)
    selector = json.loads(
        (NATIVE / "results" / "research" / "v26g6_targeted_rca" / "active_v1r_opval_20260817.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = json.loads(Path(selector["manifest_relpath"]).read_text(encoding="utf-8"))
    env = {
        ENV_OPVAL_MODE: "1",
        ENV_ACTIVATION_SELECTOR: str(
            NATIVE / "results" / "research" / "v26g6_targeted_rca" / "active_v1r_opval_20260817.json"
        ),
        "TRADEBOT_TRADING_DATE": "20260819",
    }
    reason = opval.paper_contract_blocked_reason(
        selector=selector, manifest=manifest, environ=env, native_root=NATIVE
    )
    assert reason == "OPVAL_LEGACY_IDENTITY_FORBIDDEN"
    assert OPVAL_ACTIVATION_ID == "V1R_EXIT_V2_PAPER_PRIMARY_OPVAL_CURRENT_TRADING_DAY"
    assert OPVAL_LEGACY_PINNED_DATE == "20260817"
    assert OPVAL_LEGACY_ACTIVATION_ID.endswith("20260817")


def test_capture_first_paper_not_started_if_unhealthy(opval) -> None:
    started: list[int] = []
    out = opval.maybe_start_paper(capture_ok=False, start_fn=lambda: started.append(1))
    assert out["started"] is False
    assert out["reason"] == "CAPTURE_NOT_READY"
    assert started == []
    out2 = opval.maybe_start_paper(capture_ok=True, start_fn=lambda: started.append(1) or "ok")
    assert out2["started"] is True
    assert started == [1]


def test_paper_failure_does_not_stop_capture(opval) -> None:
    called = {"n": 0}

    def stop() -> None:
        called["n"] += 1

    out = opval.paper_fail_leaves_capture(stop_capture_fn=stop)
    assert out["capture_left_running"] is True
    assert out["stop_capture_fn_invoked"] is False
    assert called["n"] == 0


def test_capture_push_increasing_requires_real_growth(opval) -> None:
    before = {
        "pid": 1,
        "pid_alive": True,
        "sequence": 10,
        "file_size_bytes": 100,
        "last_push_at": "t0",
        "registered": 50,
        "desired": 50,
        "replay": False,
        "session_clock": False,
        "certification_mode": False,
    }
    after_same = dict(before)
    stagnant = opval.capture_push_increasing(before, after_same)
    assert stagnant["ok"] is False
    assert stagnant["reason"] == "CAPTURE_PUSH_NOT_INCREASING"
    after_up = dict(before, sequence=20, file_size_bytes=200, last_push_at="t1")
    grew = opval.capture_push_increasing(before, after_up)
    assert grew["ok"] is True


def test_station_classification(opval) -> None:
    assert (
        opval.classify_station(api_port_reachable=False, station_process_detected=False)
        == "KABU_STATION_NOT_READY"
    )
    assert opval.classify_station(api_port_reachable=True, auth_deferred=True) == "AUTH_WAITING"
    assert opval.classify_station(token_acquired=True) == "AUTH_RECOVERED"
    assert opval.classify_station(capture_ready=True) == "CAPTURE_READY"
    assert opval.classify_station(paper_ready=True) == "PAPER_READY"


def test_20260818_sealed_day_refuses_spawn(opval) -> None:
    sealed = opval.day_already_sealed(NATIVE, "20260818")
    assert sealed.get("sealed") is True
    assert sealed.get("reason") == "CAPTURE_DAY_ALREADY_SEALED"


def test_no_second_token_issuer_in_launcher_source() -> None:
    src = LAUNCHER_PY.read_text(encoding="utf-8")
    assert "acquire_token_for_readonly" not in src
    assert "KabuNativeRestClient" not in src
    assert "issue_token" not in src
    assert "spawn_ingress_process" in src


def test_cleanup_path_removes_stale_env_only(opval) -> None:
    env = {ENV_CERT_MODE: "1", ENV_REPLAY_PATH: "x", ENV_OPVAL_MODE: "1"}
    cleaned = opval.apply_clean_opval_env(dict(env), trading_date="20260819")
    assert ENV_CERT_MODE not in cleaned
    assert ENV_REPLAY_PATH not in cleaned
    assert cleaned.get("TRADEBOT_OPERATIONAL_VALIDATION_MODE") == "1"
    assert "kabu_station restart" not in LAUNCHER_PY.read_text(encoding="utf-8").lower()
    assert "auto_restart_station" in LAUNCHER_PY.read_text(encoding="utf-8")


def test_run_binding_generation(opval, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opval, "RUN_BINDING_DIR", tmp_path)
    monkeypatch.setattr(opval, "RUN_BINDING_LATEST", tmp_path / "latest.json")
    path = opval.persist_opval_run_binding(
        {
            "working_activation_id": OPVAL_ACTIVATION_ID,
            "working_activation_sha": "abc",
            "source_digest": "def",
            "runtime_inventory_digest": "ghi",
            "resolved_trading_date": "20260819",
            "capture_session_id": "ing_test",
            "capture_run_id": "ingrun_test",
            "paper_stage_run_id": "",
            "paper_run_id": "rtrun_test",
        }
    )
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["schema"] == "V1R_OPVAL_RUN_BINDING_V1"
    assert body["mode"] == "OPERATIONAL_VALIDATION_ONLY"
    assert body["resolved_trading_date"] == "20260819"
    assert body["not_formal_activation"] is True
    assert (tmp_path / "latest.json").is_file()
