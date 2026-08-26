"""V26-G6 OPERATIONAL_VALIDATION_ONLY Paper startup contract."""
from __future__ import annotations

import json
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
    OPVAL_LEGACY_ACTIVATION_ID,
    OPVAL_LEGACY_PINNED_DATE,
    current_config_sha,
    current_git_head,
    operational_validation_mode,
    opval_startup_blocked_reason,
    opval_trading_date_mismatch_reason,
    resolve_opval_canonical_trading_date,
)
from small_paper.runtime_clock import (
    ENV_ARM_FILE,
    ENV_CERT_MODE,
    ENV_ENABLED,
    ENV_REPLAY_PATH,
    ENV_SKIP_CERT_GATE,
    ENV_SPEED,
    ENV_V0,
)
from small_paper.v1r_activation_binding import (
    CANDIDATE_STATUS_OPVAL,
    CANDIDATE_STATUS_UNCERTIFIED,
    ENV_ACTIVATION_SELECTOR,
    SELECTOR_PATH,
    UNCERTIFIED_NOT_ALLOWED,
    V25_ACTIVATION_ID,
    candidate_source_digest,
    collect_runtime_inventory,
    inventory_digest,
    manifest_content_sha,
    uncertified_paper_blocked_reason,
)
from small_paper.v1r_exit_v2_activation_gate import assert_exit_v2_primary_roles

NATIVE = Path(__file__).resolve().parents[1]
OUT = NATIVE / "results" / "research" / "v1r_exit_v2_prospective_activation"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
C6_SHA = "3ac5cf4b1788f52d38aeb0b7ea059f847f89cf4e026c844ec64d96713fa3563d"
JST = ZoneInfo("Asia/Tokyo")
SESSION_DAY = "20260819"


def _v25() -> dict:
    return json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))


def _opval_body(**overrides: object) -> dict:
    parent = _v25()
    body = {k: v for k, v in parent.items() if k != "sha256"}
    inv = collect_runtime_inventory(native_root=NATIVE)
    body.update(
        {
            "manifest_id": OPVAL_ACTIVATION_ID,
            "candidate_id": OPVAL_ACTIVATION_ID,
            "candidate_status": CANDIDATE_STATUS_OPVAL,
            "formal_paper_allowed": False,
            "prospective_allowed": False,
            "strategy_evaluation_allowed": False,
            "immutable": True,
            "paper_only": True,
            "order_enabled": False,
            "live_trading_enabled": False,
            "submit_cancel_live": "0/0/0",
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "parent_activation_id": V25_ACTIVATION_ID,
            "parent_activation_sha": V25_SHA,
            "runtime_code_git_commit": current_git_head(),
            "config_sha256": current_config_sha(),
            "runtime_file_sha256": inv,
            "runtime_inventory_digest": inventory_digest(inv),
            "candidate_source_digest": candidate_source_digest(inv, native_root=NATIVE),
        }
    )
    body.update(overrides)
    if "runtime_file_sha256" in overrides:
        inv2 = body["runtime_file_sha256"]
        if isinstance(inv2, dict):
            body["runtime_inventory_digest"] = inventory_digest(inv2)
            body["candidate_source_digest"] = candidate_source_digest(inv2, native_root=NATIVE)
    body["sha256"] = manifest_content_sha(body)
    return body


def _write_bound(tmp_path: Path, body: dict) -> Path:
    man = tmp_path / f"{body['manifest_id']}.json"
    man.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sel = {
        "schema": "V1R_ACTIVE_ACTIVATION_SELECTOR_V1",
        "activation_id": body["manifest_id"],
        "activation_sha": body["sha256"],
        "manifest_relpath": str(man.resolve()).replace("\\", "/"),
        "note": "OPVAL identity-only selector; not Formal.",
    }
    sp = tmp_path / "active_opval.json"
    sp.write_text(json.dumps(sel, indent=2) + "\n", encoding="utf-8")
    return sp


def _bind_session(monkeypatch: pytest.MonkeyPatch, day: str = SESSION_DAY) -> None:
    dt = datetime.strptime(day, "%Y%m%d").replace(hour=10, minute=0, tzinfo=JST)
    monkeypatch.setattr("small_paper.runtime_clock.now_jst", lambda environ=None: dt)
    monkeypatch.setattr("small_paper.session_runtime_identity.now_jst", lambda environ=None: dt)
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", day)
    monkeypatch.setenv(ENV_CAPTURE_TRADING_DATE, day)
    monkeypatch.setenv(ENV_PAPER_TRADING_DATE, day)
    monkeypatch.setenv(ENV_OPVAL_BOUND_TRADING_DATE, day)


def _clean_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        ENV_CERT_MODE,
        ENV_SKIP_CERT_GATE,
        ENV_ENABLED,
        ENV_V0,
        ENV_SPEED,
        ENV_ARM_FILE,
        ENV_REPLAY_PATH,
        "TRADEBOT_SESSION_CLOCK_STOP",
        "TRADEBOT_SESSION_CLOCK_REAL_T0",
        "TRADEBOT_INGRESS_REPLAY_NOT_BEFORE",
        "MARKET_INPUT_MODE",
    ):
        monkeypatch.delenv(k, raising=False)
    _bind_session(monkeypatch, SESSION_DAY)


def _sel_man(
    tmp_path: Path,
    body: dict,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[dict, dict]:
    sp = _write_bound(tmp_path, body)
    if monkeypatch is not None:
        monkeypatch.setenv(ENV_ACTIVATION_SELECTOR, str(sp))
    selector = json.loads(sp.read_text(encoding="utf-8"))
    return selector, body


@pytest.fixture(autouse=True)
def _no_cert(monkeypatch: pytest.MonkeyPatch):
    _clean_live_env(monkeypatch)
    yield


def test_opval_mode_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_OPVAL_MODE, raising=False)
    assert operational_validation_mode() is False
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    assert operational_validation_mode() is True


def test_opval_pass_exact_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == ""
    r = assert_exit_v2_primary_roles()
    assert r.ok is True, r.reason
    assert r.identity.get("paper_mode") == CANDIDATE_STATUS_OPVAL
    assert r.identity.get("INVALID_FOR_STRATEGY_EVALUATION") is True
    assert r.identity.get("NOT_PROSPECTIVE_DAY1") is True


def test_opval_reject_wrong_sha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    selector["activation_sha"] = "0" * 64
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_SELECTOR_SHA_MISMATCH"


def test_opval_reject_wrong_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    inv = collect_runtime_inventory(native_root=NATIVE)
    rel = next(iter(inv))
    inv[rel] = ("0" if inv[rel][0] != "0" else "1") + inv[rel][1:]
    body = _opval_body(runtime_file_sha256=inv)
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_INVENTORY_MISMATCH"


def test_opval_reject_wrong_source_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    body = _opval_body()
    body["candidate_source_digest"] = "0" * 64
    body["sha256"] = manifest_content_sha(body)
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_SOURCE_DIGEST_MISMATCH"


def test_opval_reject_formal_selector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    body = _opval_body()
    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    assert opval_startup_blocked_reason(v25_sel, body) == "OPVAL_FORMAL_SELECTOR_SUBSTITUTION"
    monkeypatch.setenv(ENV_ACTIVATION_SELECTOR, str(SELECTOR_PATH))
    selector, manifest = _sel_man(tmp_path, body)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_FORMAL_SELECTOR_SUBSTITUTION"


def test_opval_reject_certification_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    monkeypatch.setenv(ENV_CERT_MODE, "1")
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_CERTIFICATION_MODE_FORBIDDEN"


def test_opval_reject_skip_cert_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    monkeypatch.setenv(ENV_SKIP_CERT_GATE, "1")
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_SKIP_CERT_GATE_FORBIDDEN"


def test_opval_reject_replay_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    monkeypatch.setenv(ENV_REPLAY_PATH, str(tmp_path / "tape.jsonl"))
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_REPLAY_PATH_FORBIDDEN"


def test_opval_reject_session_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    monkeypatch.setenv(ENV_ENABLED, "1")
    monkeypatch.setenv(ENV_V0, "2026-08-12T08:50:00.000+09:00")
    monkeypatch.setenv(ENV_SPEED, "48.0")
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_SESSION_CLOCK_FORBIDDEN"


def test_opval_reject_historical_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", "20260812")
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_HISTORICAL_DATE"


def test_opval_reject_submit_cancel_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    body = _opval_body(submit_cancel_live="1/0/0")
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_SUBMIT_CANCEL_LIVE"
    body = _opval_body(submit=1)
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_SUBMIT"
    body = _opval_body(cancel=1)
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_CANCEL"
    body = _opval_body(live=1)
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_LIVE"


def test_opval_reject_order_enablement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    body = _opval_body(order_enabled=True)
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_ORDER_ENABLED"
    body = _opval_body(live_trading_enabled=True)
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_LIVE_TRADING_ENABLED"


def test_opval_reject_paper_only_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    body = _opval_body(paper_only=False)
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_PAPER_ONLY_REQUIRED"


def test_opval_reject_arm_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    monkeypatch.setenv(ENV_ARM_FILE, str(tmp_path / "session_clock_arm.json"))
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_SESSION_CLOCK_FORBIDDEN"


def test_opval_is_not_a_bypass_keyword() -> None:
    src = (NATIVE / "src/small_paper/operational_validation.py").read_text(encoding="utf-8")
    gate = (NATIVE / "src/small_paper/v1r_exit_v2_activation_gate.py").read_text(encoding="utf-8")
    for blob in (src, gate):
        assert "skip_activation_check" not in blob
        assert "ignore_inventory" not in blob
        assert "force_ready" not in blob
        assert "allow_uncertified" not in blob


def test_opval_identity_without_mode_still_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_OPVAL_MODE, raising=False)
    body = _opval_body()
    assert uncertified_paper_blocked_reason(body, certification=False) == UNCERTIFIED_NOT_ALLOWED
    monkeypatch.setenv(ENV_ACTIVATION_SELECTOR, str(_write_bound(tmp_path, body)))
    r = assert_exit_v2_primary_roles()
    assert r.ok is False
    assert UNCERTIFIED_NOT_ALLOWED in r.reason


def test_opval_not_a_generic_uncertified_allow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    parent = _v25()
    body = {k: v for k, v in parent.items() if k != "sha256"}
    inv = collect_runtime_inventory(native_root=NATIVE)
    body.update(
        {
            "manifest_id": "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_TEST",
            "candidate_id": "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_TEST",
            "candidate_status": CANDIDATE_STATUS_UNCERTIFIED,
            "formal_paper_allowed": False,
            "paper_only": True,
            "order_enabled": False,
            "live_trading_enabled": False,
            "submit_cancel_live": "0/0/0",
            "runtime_file_sha256": inv,
            "runtime_inventory_digest": inventory_digest(inv),
            "candidate_source_digest": candidate_source_digest(inv, native_root=NATIVE),
        }
    )
    body["sha256"] = manifest_content_sha(body)
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_FORMAL_SELECTOR_SUBSTITUTION"


def test_opval_canonical_current_trading_date_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    day, reason = resolve_opval_canonical_trading_date(clock_day=SESSION_DAY)
    assert reason == ""
    assert day == SESSION_DAY
    assert opval_startup_blocked_reason(selector, manifest, clock_day=SESSION_DAY) == ""
    assert (
        opval_trading_date_mismatch_reason(
            resolved=SESSION_DAY,
            capture_trading_date=SESSION_DAY,
            paper_trading_date=SESSION_DAY,
            bound_trading_date=SESSION_DAY,
        )
        == ""
    )


def test_opval_reject_legacy_20260817_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", OPVAL_LEGACY_PINNED_DATE)
    monkeypatch.setenv(ENV_CAPTURE_TRADING_DATE, OPVAL_LEGACY_PINNED_DATE)
    monkeypatch.setenv(ENV_PAPER_TRADING_DATE, OPVAL_LEGACY_PINNED_DATE)
    monkeypatch.setenv(ENV_OPVAL_BOUND_TRADING_DATE, OPVAL_LEGACY_PINNED_DATE)
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest, clock_day=SESSION_DAY) == "OPVAL_HISTORICAL_DATE"


def test_opval_reject_legacy_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    selector["activation_id"] = OPVAL_LEGACY_ACTIVATION_ID
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_LEGACY_IDENTITY_FORBIDDEN"


def test_opval_reject_future_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", "20260820")
    monkeypatch.setenv(ENV_CAPTURE_TRADING_DATE, "20260820")
    monkeypatch.setenv(ENV_PAPER_TRADING_DATE, "20260820")
    monkeypatch.setenv(ENV_OPVAL_BOUND_TRADING_DATE, "20260820")
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest, clock_day=SESSION_DAY) == "OPVAL_FUTURE_DATE"


def test_opval_reject_weekend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    weekend = "20260822"
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", weekend)
    monkeypatch.setenv(ENV_CAPTURE_TRADING_DATE, weekend)
    monkeypatch.setenv(ENV_PAPER_TRADING_DATE, weekend)
    monkeypatch.setenv(ENV_OPVAL_BOUND_TRADING_DATE, weekend)
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest, clock_day=weekend) == "OPVAL_NON_TRADING_DATE"


def test_opval_reject_exchange_holiday(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    holiday = "20260811"
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", holiday)
    monkeypatch.setenv(ENV_CAPTURE_TRADING_DATE, holiday)
    monkeypatch.setenv(ENV_PAPER_TRADING_DATE, holiday)
    monkeypatch.setenv(ENV_OPVAL_BOUND_TRADING_DATE, holiday)
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest, clock_day=holiday) == "OPVAL_NON_TRADING_DATE"


def test_opval_reject_unresolved_exchange_year(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    future_year = "20270105"
    monkeypatch.setenv("TRADEBOT_TRADING_DATE", future_year)
    monkeypatch.setenv(ENV_CAPTURE_TRADING_DATE, future_year)
    monkeypatch.setenv(ENV_PAPER_TRADING_DATE, future_year)
    monkeypatch.setenv(ENV_OPVAL_BOUND_TRADING_DATE, future_year)
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    assert opval_startup_blocked_reason(selector, manifest, clock_day=future_year) == "OPVAL_TRADING_DATE_UNRESOLVED"


def test_opval_reject_capture_paper_bound_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    monkeypatch.setenv(ENV_CAPTURE_TRADING_DATE, "20260818")
    assert opval_startup_blocked_reason(selector, manifest, clock_day=SESSION_DAY) == "OPVAL_TRADING_DATE_MISMATCH"
    monkeypatch.setenv(ENV_CAPTURE_TRADING_DATE, SESSION_DAY)
    monkeypatch.setenv(ENV_PAPER_TRADING_DATE, "20260818")
    assert opval_startup_blocked_reason(selector, manifest, clock_day=SESSION_DAY) == "OPVAL_TRADING_DATE_MISMATCH"
    monkeypatch.setenv(ENV_PAPER_TRADING_DATE, SESSION_DAY)
    monkeypatch.setenv(ENV_OPVAL_BOUND_TRADING_DATE, "20260818")
    assert opval_startup_blocked_reason(selector, manifest, clock_day=SESSION_DAY) == "OPVAL_TRADING_DATE_MISMATCH"


def test_opval_reject_candidate6_and_v25_substitution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_OPVAL_MODE, "1")
    body = _opval_body()
    selector, manifest = _sel_man(tmp_path, body, monkeypatch)
    selector["activation_id"] = C6_ID
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_CANDIDATE6_FORBIDDEN"
    from small_paper.operational_validation import C7_ID

    selector["activation_id"] = C7_ID
    assert opval_startup_blocked_reason(selector, manifest) == "OPVAL_CANDIDATE7_DIRECT_SELECTOR_FORBIDDEN"
    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    assert opval_startup_blocked_reason(v25_sel, manifest) == "OPVAL_FORMAL_SELECTOR_SUBSTITUTION"


def test_strategy_entry_exit_universe_unchanged_vs_c6() -> None:
    c6 = json.loads((OUT / f"{C6_ID}.json").read_text(encoding="utf-8"))
    v25 = _v25()
    assert c6.get("sha256") == C6_SHA
    assert v25.get("sha256") == V25_SHA
    for key in (
        "strategy_sha",
        "entry_sha",
        "exit_v2_candidate_sha",
        "exit_contract_sha",
        "universe_binding_sha",
        "universe_contract",
        "precommit_sha",
    ):
        assert c6.get(key) == v25.get(key)
