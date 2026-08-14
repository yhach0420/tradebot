"""V6: registration ownership, drift recovery, pre-warmup vs live-flow semantics."""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from small_paper.day_fixed_am_registration import bind_same_day_am_desired_universe
from small_paper.ingress_control_channel import write_desired_universe
from small_paper.kabu_registration_authority import (
    EXPECTED_PRE_WARMUP,
    LIVE_RUNTIME_FLOW_PASS,
    OWNER_MARKET_INGRESS,
    POST_INGRESS_COMMIT_UNREGISTER_ALL,
    PRE_WARMUP_CONNECTIVITY_PASS,
    REGISTRATION_DRIFT_DETECTED,
    REGISTRATION_DRIFT_REPUT,
    evaluate_live_runtime_flow,
    evaluate_pre_warmup_connectivity,
    forbid_post_ingress_unregister_all,
    ingress_owns_kabu_registration,
    is_pre_warmup,
    post_ingress_unregister_executed_count,
    verify_exact50_membership,
    write_actual_regist_snapshot,
    write_registration_owner,
)
from small_paper.market_capture_registration import write_registration_manifest
from small_paper.market_ingress_service import MarketIngressService

JST = ZoneInfo("Asia/Tokyo")


def _am_syms(n: int = 50, *, start: int = 1000) -> list[str]:
    return [f"{start + i}" for i in range(n)]


def _write_am_csv(root: Path, day: str, symbols: list[str]) -> Path:
    path = root / "results" / "reports" / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, bare in enumerate(symbols):
        slot = "core" if i < 10 else "dynamic"
        rows.append(
            {
                "symbol": f"{bare}.T",
                "symbol_key": f"{bare}@1",
                "exchange": "1",
                "passed": "True",
                "source_bucket": "core10_discord" if slot == "core" else "vol_liq_dynamic40",
                "selected_reason": slot,
                "universe_slot": slot,
                "rank": str(i + 1),
                "am_pm_session": "am",
            }
        )
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


class _FakePush:
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, int]]] = []
        self.regist: list[tuple[str, int]] = []

    def register(self, symbols_spec: list[tuple[str, int]]) -> dict:
        self.regist = [(str(s), int(ex)) for s, ex in symbols_spec]
        self.calls.append(list(symbols_spec))
        return {
            "RegistNum": len(self.regist),
            "Symbols": [{"Symbol": s, "Exchange": int(ex)} for s, ex in self.regist],
            "RegistList": [{"Symbol": s, "Exchange": int(ex)} for s, ex in self.regist],
        }

    def unregister_all(self) -> dict:
        self.regist = []
        return {"RegistNum": 0, "Symbols": [], "RegistList": []}

    def fetch_regist_list(self) -> dict:
        return {
            "ok": True,
            "readonly": True,
            "reason": "push_fetch_regist_list",
            "symbols": [s for s, _ in self.regist],
            "http_status": 200,
        }


def _svc(tmp_path: Path, day: str) -> MarketIngressService:
    return MarketIngressService(
        native_root=tmp_path,
        trading_date=day,
        synthetic=False,
        enable_tcp_bus=False,
    )


def _bind_day(tmp_path: Path, day: str, symbols: list[str]) -> None:
    _write_am_csv(tmp_path, day, symbols)
    bind_same_day_am_desired_universe(tmp_path, day, symbols=symbols)


def test_case_a_legacy_unregister_only_before_ingress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKET_INGRESS_V2", "1")
    day = "20260813"
    info = ingress_owns_kabu_registration(tmp_path, day)
    assert info["owned"] is False
    gate = forbid_post_ingress_unregister_all(tmp_path, day, caller="safety.preclear")
    assert gate["allow"] is True
    assert gate["blocked"] is False
    assert post_ingress_unregister_executed_count(tmp_path, day) == 0

    calls: list[str] = []

    def _unreg(push, **kwargs):
        calls.append("unregister_all")
        return {"ok": True, "response": {"RegistList": []}, "regist_num": 0}

    monkeypatch.setattr("api.kabu_register.unregister_all_until_zero", _unreg)
    monkeypatch.setattr(
        "api.kabu_register.push_client_from_repo",
        lambda root: (_FakePush(), None, None),
    )
    from api.kabu_register import clear_register_before_session

    out = clear_register_before_session(tmp_path)
    assert out.get("ok") is True
    assert out.get("skipped") is not True
    assert calls == ["unregister_all"]
    assert post_ingress_unregister_executed_count(tmp_path, day) == 0


def test_case_b_daily_runner_after_ingress_put_no_unregister(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from small_paper.runtime_clock import (
        ENV_ARM_FILE,
        ENV_ENABLED,
        ENV_SPEED,
        ENV_STOP,
        ENV_T0,
        ENV_V0,
        bind_session_clock,
    )

    monkeypatch.setenv("MARKET_INGRESS_V2", "1")
    for k in (ENV_ENABLED, ENV_V0, ENV_T0, ENV_SPEED, ENV_STOP, ENV_ARM_FILE):
        if k in os.environ:
            monkeypatch.setenv(k, os.environ[k])
        else:
            monkeypatch.delenv(k, raising=False)
    bind_session_clock(virtual_start=datetime(2026, 8, 13, 9, 0, tzinfo=JST), speed_mult=1.0)
    day = "20260813"
    am = _am_syms()
    _bind_day(tmp_path, day, am)
    write_registration_owner(
        tmp_path,
        trading_date=day,
        pid=os.getpid(),
        ingress_session_id="ing_test",
        committed=True,
    )
    write_registration_manifest(
        tmp_path,
        trading_date=day,
        symbols=am,
        generation_id=f"gen_{day}_1",
        owner=OWNER_MARKET_INGRESS,
        extra={"actual_symbols": am, "actual_count": 50, "registered_count": 50},
    )
    write_actual_regist_snapshot(
        tmp_path, trading_date=day, symbols=am, source="kabu_put_response", generation=1
    )

    calls: list[str] = []
    monkeypatch.setattr(
        "api.kabu_register.unregister_all_until_zero",
        lambda *a, **k: calls.append("unregister_all") or {"ok": True},
    )
    from api.kabu_register import clear_register_before_session
    from runner.am_pm_daily_runner import kabu_clear_stale_registrations

    out = clear_register_before_session(tmp_path)
    assert out.get("skipped") is True
    assert out.get("reason") == "INGRESS_OWNS_KABU_REGISTRATION"
    assert out.get(POST_INGRESS_COMMIT_UNREGISTER_ALL) == 0
    assert calls == []
    assert post_ingress_unregister_executed_count(tmp_path, day) == 0

    class _Opt:
        day_stamp = day
        skip_kabu = False
        dry_run_only = False

    class _St:
        options = _Opt()
        repo_root = tmp_path

    cleared = kabu_clear_stale_registrations(_St(), label="preflight_before_am")
    assert cleared.get("skipped") is True
    assert cleared.get("reason") == "INGRESS_OWNS_KABU_REGISTRATION"
    assert calls == []
    mem = verify_exact50_membership(
        tmp_path, day, actual_symbols=am, require_actual_kabu=True
    )
    assert mem["ok"] is True
    assert mem["actual_n"] == 50


def test_case_c_external_empty_drift_same_generation_reput(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms()
    _bind_day(tmp_path, day, am)
    svc = _svc(tmp_path, day)
    push = _FakePush()
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=am, generation=11, trading_date=day)
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    assert [s for s, _ in push.regist] == am

    push.regist = []  # external wipe; generation unchanged
    out = svc._maybe_register_desired_live(reason="desired_poll")
    assert out.get("skipped") is not True
    assert len(push.calls) == 2
    assert [s for s, _ in push.regist] == am
    audit = (tmp_path / "data" / "market_capture" / day / "registration_authority_audit.jsonl").read_text(
        encoding="utf-8"
    )
    assert REGISTRATION_DRIFT_DETECTED in audit
    assert REGISTRATION_DRIFT_REPUT in audit
    svc.writer.close()
    svc.bus.stop()


def test_case_d_38_symbol_drift_restores_desired_50(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms()
    wrong = _am_syms(start=5000)
    _bind_day(tmp_path, day, am)
    svc = _svc(tmp_path, day)
    push = _FakePush()
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=am, generation=22, trading_date=day)
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    push.regist = [(s, 1) for s in wrong]
    out = svc._maybe_register_desired_live(reason="desired_poll")
    assert out.get("put_executed") is True or out.get("skipped") is not True
    assert len(push.calls) == 2
    assert [s for s, _ in push.regist] == am
    assert set(s for s, _ in push.regist) != set(wrong)
    svc.writer.close()
    svc.bus.stop()


def test_case_e_exact50_no_unnecessary_put(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms()
    _bind_day(tmp_path, day, am)
    svc = _svc(tmp_path, day)
    push = _FakePush()
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=am, generation=33, trading_date=day)
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    svc._poll_desired_universe()
    svc._poll_desired_universe()
    out = svc._maybe_register_desired_live(reason="repeat")
    assert out.get("skipped") is True
    assert out.get("reason") == "same_desired_generation_already_registered"
    assert len(push.calls) == 1
    svc.writer.close()
    svc.bus.stop()


def test_case_f_pre_warmup_consumer_false_is_expected() -> None:
    now = datetime(2026, 8, 13, 7, 35, tzinfo=JST)
    assert is_pre_warmup(now=now, warmup_hhmm="08:50") is True
    ev = evaluate_pre_warmup_connectivity(
        kabu_token_ok=True,
        same_day_am50=True,
        actual_kabu_exact50=True,
        ingress_resident=True,
        receiver_resident=True,
        registration_drift=False,
        post_registration_unregister=0,
        consumer_connected=False,
        paper_consumer_last_ack=0,
        wait_until_session=True,
    )
    assert ev["ok"] is True
    assert ev["verdict"] == PRE_WARMUP_CONNECTIVITY_PASS
    assert ev["consumer_status"] == EXPECTED_PRE_WARMUP
    assert ev["blockers"] == []


def test_case_g_after_warmup_requires_tcp_ack_heartbeat() -> None:
    now = datetime(2026, 8, 13, 8, 51, tzinfo=JST)
    assert is_pre_warmup(now=now, warmup_hhmm="08:50") is False
    ev = evaluate_live_runtime_flow(
        consumer_connected=True,
        consumer_ready=True,
        transport="TCP",
        raw_forward=True,
        publisher_forward=True,
        ack_forward_or_catchup=True,
        heartbeat_continuous=True,
        native_ready=True,
        primary_resident=True,
    )
    assert ev["ok"] is True
    assert ev["verdict"] == LIVE_RUNTIME_FLOW_PASS
    fail = evaluate_live_runtime_flow(
        consumer_connected=False,
        consumer_ready=False,
        transport="",
        raw_forward=False,
        publisher_forward=False,
        ack_forward_or_catchup=False,
        heartbeat_continuous=False,
        native_ready=True,
        primary_resident=True,
    )
    assert fail["ok"] is False
    assert "consumer_connected" in fail["blockers"]


def test_self_record_50_actual_empty_is_not_ready(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms()
    _bind_day(tmp_path, day, am)
    write_registration_manifest(
        tmp_path,
        trading_date=day,
        symbols=am,
        generation_id=f"gen_{day}_1",
        owner=OWNER_MARKET_INGRESS,
        extra={"actual_symbols": am, "actual_count": 50, "registered_count": 50},
    )
    mem = verify_exact50_membership(
        tmp_path, day, actual_symbols=[], require_actual_kabu=True
    )
    assert mem["ok"] is False
    assert mem["reason"] == "actual_kabu_empty_self_record_mismatch"
    assert mem["self_record_n"] == 50
    assert mem["actual_n"] == 0
