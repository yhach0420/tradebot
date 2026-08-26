"""OPVAL current-runtime-candidate binding repair (20260825).

Does not mutate Candidate-6/7/8/9 or Formal V25.
"""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from small_paper.operational_validation import (
    ENV_CAPTURE_TRADING_DATE,
    ENV_OPVAL_BOUND_TRADING_DATE,
    ENV_OPVAL_MODE,
    ENV_PAPER_TRADING_DATE,
    OPVAL_ACTIVATION_ID,
    current_config_sha,
    current_git_head,
    opval_startup_blocked_reason,
)
from small_paper.opval_runtime_candidate import (
    ENV_OPVAL_RUNTIME_CANDIDATE_SELECTOR,
    opval_bound_runtime_candidate_blocked_reason,
    resolve_current_opval_runtime_candidate,
)
from small_paper.runtime_clock import ENV_CERT_MODE, ENV_REPLAY_PATH
from small_paper.v1r_activation_binding import (
    CANDIDATE_STATUS_OPVAL,
    ENV_ACTIVATION_SELECTOR,
    SELECTOR_PATH,
    V25_ACTIVATION_ID,
    candidate_source_digest,
    collect_runtime_inventory,
    inventory_digest,
    manifest_content_sha,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
OUT = NATIVE / "results" / "research" / "v1r_exit_v2_prospective_activation"
WRITER_PY = NATIVE / "scripts" / "write_opval_current_trading_day_identity.py"
LAUNCHER_PY = NATIVE / "scripts" / "run_paper_trade_opval.py"
JST = ZoneInfo("Asia/Tokyo")

V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
C6_SHA = "3ac5cf4b1788f52d38aeb0b7ea059f847f89cf4e026c844ec64d96713fa3563d"
C7_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G7_7"
C7_SHA = "bc0b47e01f6bce592fa374bc555d3e9f26dbd353848356a890bdb73452602960"
C8_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G8_8"
C8_SHA = "bdde9ca3777216d56e502aee1fbc4873c6558ea9d9d0a2c869f1e84c9199d30c"
C9_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G9_9"
C9_SHA = "364754cd444bdce80e9f0e8157cfde8f426eb4d7e8bd78ccd5a7cd04004e6945"
C9_DIGEST = "d5ddaf7ca1fd1f707375f4aaee4080dcb8cb47a23d8e9d93743be105fa3b25e7"
ENTRY_SHA = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
ANCHOR_SHA = "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"
EXIT_SHA = "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255"
STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
DAY = "20260825"


def _load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def writer():
    return _load_script(WRITER_PY, "write_opval_current_trading_day_identity")


@pytest.fixture(scope="module")
def opval():
    return _load_script(LAUNCHER_PY, "run_paper_trade_opval_binding_repair")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _c9() -> dict:
    return _load(OUT / f"{C9_ID}.json")


def _c7() -> dict:
    return _load(OUT / f"{C7_ID}.json")


def _v25() -> dict:
    return _load(OUT / f"{V25_ACTIVATION_ID}.json")


def _bind_day(monkeypatch: pytest.MonkeyPatch, day: str = DAY) -> None:
    dt = datetime.strptime(day, "%Y%m%d").replace(hour=10, minute=0, tzinfo=JST)
    monkeypatch.setattr("small_paper.runtime_clock.now_jst", lambda environ=None: dt)
    monkeypatch.setattr("small_paper.session_runtime_identity.now_jst", lambda environ=None: dt)
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", day)
    monkeypatch.setenv(ENV_PAPER_TRADING_DATE, day)
    monkeypatch.setenv(ENV_CAPTURE_TRADING_DATE, day)
    monkeypatch.setenv(ENV_OPVAL_BOUND_TRADING_DATE, day)
    monkeypatch.delenv(ENV_CERT_MODE, raising=False)
    monkeypatch.delenv(ENV_REPLAY_PATH, raising=False)


def _opval_from_candidate(cand: dict, *, dest_dir: Path) -> tuple[dict, dict, Path]:
    cid = str(cand.get("manifest_id") or cand.get("candidate_id") or "")
    csha = str(cand.get("sha256") or "")
    inv = dict(cand.get("runtime_file_sha256") or {})
    body = {k: v for k, v in cand.items() if k != "sha256"}
    body.update(
        {
            "manifest_id": OPVAL_ACTIVATION_ID,
            "candidate_id": OPVAL_ACTIVATION_ID,
            "candidate_status": CANDIDATE_STATUS_OPVAL,
            "classification": CANDIDATE_STATUS_OPVAL,
            "formal_paper_allowed": False,
            "prospective_allowed": False,
            "strategy_evaluation_allowed": False,
            "immutable": False,
            "paper_only": True,
            "order_enabled": False,
            "live_trading_enabled": False,
            "submit_cancel_live": "0/0/0",
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "trading_date": DAY,
            "bound_current_runtime_candidate": cid,
            "bound_current_runtime_candidate_id": cid,
            "bound_current_runtime_candidate_sha": csha,
            "working_tree_matches_bound_candidate": True,
            "runtime_code_git_commit": current_git_head(),
            "config_sha256": current_config_sha(),
            "runtime_file_sha256": inv,
            "runtime_inventory_digest": str(cand.get("runtime_inventory_digest") or inventory_digest(inv)),
            "candidate_source_digest": candidate_source_digest(inv, native_root=NATIVE),
        }
    )
    body["sha256"] = manifest_content_sha(body)
    dest_dir.mkdir(parents=True, exist_ok=True)
    man = dest_dir / f"{OPVAL_ACTIVATION_ID}.json"
    man.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sel = {
        "schema": "V1R_ACTIVE_ACTIVATION_SELECTOR_V1",
        "activation_id": OPVAL_ACTIVATION_ID,
        "activation_sha": body["sha256"],
        "manifest_relpath": str(man.resolve()).replace("\\", "/"),
    }
    sp = dest_dir / "active_v1r_opval_current_trading_day.json"
    sp.write_text(json.dumps(sel, indent=2) + "\n", encoding="utf-8")
    return sel, body, sp


def test_a_c9_current_candidate_binds_opval_identity(writer, opval, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_day(monkeypatch)
    current = resolve_current_opval_runtime_candidate(native_root=NATIVE)
    assert current.get("ok") is True
    assert current.get("id") == C9_ID
    assert current.get("sha256") == C9_SHA
    result = writer.write_opval_current_trading_day_identity(
        native_root=NATIVE,
        trading_date=DAY,
        dest_dir=tmp_path,
    )
    assert result.get("ok") is True, result
    assert result["bound_current_runtime_candidate"] == C9_ID
    assert result["bound_current_runtime_candidate_sha"] == C9_SHA
    body = result["identity"]
    selector = result["selector"]
    monkeypatch.setenv(ENV_ACTIVATION_SELECTOR, str(result["selector_path"]))
    assert (
        opval.paper_contract_blocked_reason(
            selector=selector, manifest=body, environ=os.environ, native_root=NATIVE
        )
        == ""
    )
    assert opval_bound_runtime_candidate_blocked_reason(body, native_root=NATIVE) == ""


def test_b_working_tree_matches_c9_inventory() -> None:
    c9 = _c9()
    inv = verify_runtime_inventory(c9, native_root=NATIVE)
    assert inv.get("ok") is True
    assert c9.get("runtime_inventory_digest") == C9_DIGEST
    wt = collect_runtime_inventory(native_root=NATIVE)
    assert inventory_digest(wt) == C9_DIGEST
    assert wt == c9.get("runtime_file_sha256")


def test_c_one_file_wt_mismatch_is_inventory_mismatch(
    writer, opval, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_day(monkeypatch)
    from small_paper.v1r_activation_binding import file_sha256 as real_sha

    target = "src/small_paper/safety.py"

    def fake_sha(path: Path) -> str:
        p = Path(path)
        if str(p).replace("\\", "/").endswith(target):
            return "0" * 64
        return real_sha(path)

    monkeypatch.setattr("small_paper.v1r_activation_binding.file_sha256", fake_sha)
    c9 = _c9()
    assert verify_runtime_inventory(c9, native_root=NATIVE).get("ok") is False
    sel, body, sp = _opval_from_candidate(c9, dest_dir=tmp_path)
    monkeypatch.setenv(ENV_ACTIVATION_SELECTOR, str(sp))
    assert opval_startup_blocked_reason(sel, body, clock_day=DAY) == "OPVAL_INVENTORY_MISMATCH"
    refused = writer.write_opval_current_trading_day_identity(
        native_root=NATIVE, trading_date=DAY, dest_dir=tmp_path / "out"
    )
    assert refused.get("ok") is False
    assert refused.get("reason") == "OPVAL_INVENTORY_MISMATCH"


def test_d_stale_c7_opval_identity_fail_closed(opval, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_day(monkeypatch)
    sel, body, sp = _opval_from_candidate(_c7(), dest_dir=tmp_path)
    monkeypatch.setenv(ENV_ACTIVATION_SELECTOR, str(sp))
    reason = opval.paper_contract_blocked_reason(
        selector=sel, manifest=body, environ=os.environ, native_root=NATIVE
    )
    assert reason in {"OPVAL_INVENTORY_MISMATCH", "OPVAL_BOUND_CANDIDATE_SELECTOR_MISMATCH"}
    assert opval_startup_blocked_reason(sel, body, clock_day=DAY) == "OPVAL_INVENTORY_MISMATCH"
    assert opval_bound_runtime_candidate_blocked_reason(body, native_root=NATIVE) == "OPVAL_BOUND_CANDIDATE_SELECTOR_MISMATCH"


def test_e_stale_identity_stops_before_ingress(opval, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_day(monkeypatch)
    spawned: list[object] = []
    monkeypatch.setattr(opval, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(opval, "RUN_BINDING_DIR", tmp_path / "binds")
    monkeypatch.setattr(opval, "RUN_BINDING_LATEST", tmp_path / "bind_latest.json")
    monkeypatch.setattr(opval, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(
        opval,
        "spawn_live_capture",
        lambda **kwargs: spawned.append(("spawn_live_capture", kwargs)) or {"ok": True, "pid": 1},
    )
    monkeypatch.setattr(
        opval,
        "probe_station",
        lambda: {
            "classification": "AUTH_RECOVERED",
            "api_port_reachable": True,
            "station_process_detected": True,
        },
    )
    sel, _body, sp = _opval_from_candidate(_c7(), dest_dir=tmp_path / "stale")
    monkeypatch.setattr(
        "small_paper.operational_validation.resolve_opval_canonical_trading_date",
        lambda **kwargs: (DAY, ""),
    )
    rc = opval.run(["--selector", str(sp), "--no-pause"])
    assert rc == 2
    assert spawned == []
    reports = list((tmp_path / "reports").glob("opval_launcher_*.json"))
    assert reports
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert report.get("verdict") == "FAIL_CLOSED"
    assert report.get("blocked_reason") in {
        "OPVAL_INVENTORY_MISMATCH",
        "OPVAL_BOUND_CANDIDATE_SELECTOR_MISMATCH",
    }
    steps = report.get("steps") or []
    bind_step = next(s for s in steps if s.get("name") == "opval_selector_binding")
    assert bind_step.get("result") == "FAIL_CLOSED"
    assert opval.fail_closed_before_ingress_reason("OPVAL_INVENTORY_MISMATCH") == "OPVAL_INVENTORY_MISMATCH"


def test_f_writer_follows_selector_c8_to_c9_without_hardcode(writer, tmp_path: Path) -> None:
    src = WRITER_PY.read_text(encoding="utf-8")
    assert C7_ID not in src
    assert C8_ID not in src
    assert C9_ID not in src
    c8_sel = OUT / "active_v1r_candidate_v26g8_8.json"
    c9_sel = OUT / "active_v1r_candidate_v26g9_9.json"
    resolved_c8 = resolve_current_opval_runtime_candidate(
        native_root=NATIVE, selector_path=c8_sel
    )
    assert resolved_c8.get("id") == C8_ID
    assert resolved_c8.get("sha256") == C8_SHA
    assert resolved_c8.get("working_tree_matches") is False
    refused = writer.write_opval_current_trading_day_identity(
        native_root=NATIVE,
        trading_date=DAY,
        dest_dir=tmp_path / "c8",
        runtime_candidate_selector=c8_sel,
    )
    assert refused.get("ok") is False
    assert refused.get("reason") == "OPVAL_INVENTORY_MISMATCH"
    resolved_c9 = resolve_current_opval_runtime_candidate(
        native_root=NATIVE, selector_path=c9_sel
    )
    assert resolved_c9.get("id") == C9_ID
    assert resolved_c9.get("working_tree_matches") is True
    wrote = writer.write_opval_current_trading_day_identity(
        native_root=NATIVE,
        trading_date=DAY,
        dest_dir=tmp_path / "c9",
        runtime_candidate_selector=c9_sel,
    )
    assert wrote.get("ok") is True, wrote
    assert wrote["bound_current_runtime_candidate"] == C9_ID


def test_g_candidate9_manifest_unchanged() -> None:
    c9 = _c9()
    ok, got, calc = verify_manifest_self_sha(c9)
    assert ok and got == calc == C9_SHA
    sel = _load(OUT / "active_v1r_candidate_v26g9_9.json")
    assert sel.get("activation_id") == C9_ID
    assert sel.get("activation_sha") == C9_SHA


def test_h_formal_v25_unchanged() -> None:
    v25 = _v25()
    ok, got, calc = verify_manifest_self_sha(v25)
    assert ok and got == calc == V25_SHA
    sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    assert sel.get("activation_id") == V25_ACTIVATION_ID
    assert sel.get("activation_sha") == V25_SHA
    c6 = _load(OUT / f"{C6_ID}.json")
    assert c6.get("sha256") == C6_SHA
    c7 = _c7()
    assert c7.get("sha256") == C7_SHA
    c8 = _load(OUT / f"{C8_ID}.json")
    assert c8.get("sha256") == C8_SHA


def test_i_entry_anchor_exit_strategy_unchanged(writer, tmp_path: Path) -> None:
    c9 = _c9()
    v25 = _v25()
    for key, expected in (
        ("entry_sha", ENTRY_SHA),
        ("anchor_sha", ANCHOR_SHA),
        ("exit_v2_candidate_sha", EXIT_SHA),
        ("strategy_sha", STRATEGY_SHA),
    ):
        assert c9.get(key) == expected
        assert v25.get(key) == expected
    wrote = writer.write_opval_current_trading_day_identity(
        native_root=NATIVE, trading_date=DAY, dest_dir=tmp_path
    )
    assert wrote.get("ok") is True, wrote
    body = wrote["identity"]
    assert body.get("entry_sha") == ENTRY_SHA
    assert body.get("anchor_sha") == ANCHOR_SHA
    assert body.get("exit_v2_candidate_sha") == EXIT_SHA
    assert body.get("strategy_sha") == STRATEGY_SHA


def test_j_submit_cancel_live_zero(writer, tmp_path: Path) -> None:
    wrote = writer.write_opval_current_trading_day_identity(
        native_root=NATIVE, trading_date=DAY, dest_dir=tmp_path
    )
    assert wrote.get("ok") is True, wrote
    body = wrote["identity"]
    assert body.get("submit_cancel_live") == "0/0/0"
    assert body.get("submit") == 0
    assert body.get("cancel") == 0
    assert body.get("live") == 0
    assert body.get("order_enabled") is False
    assert body.get("live_trading_enabled") is False
    src = LAUNCHER_PY.read_text(encoding="utf-8")
    assert C9_ID not in src
    assert "fail_closed_before_ingress_reason" in src
