"""V8: SAME_DAY_AM_FROZEN_UNIVERSE single authority; post-bind mutation removed."""
from __future__ import annotations

import csv
import inspect
from datetime import date
from pathlib import Path

import pytest

from runner.am_pm_daily_runner import (
    DailyRunnerOptions,
    DailyRunnerState,
    build_am_universe,
    _run_daily_runner_body,
)
from small_paper.day_fixed_am_registration import (
    AM_UNIVERSE_REUSED_FROZEN,
    FROZEN_AM_UNIVERSE_MISMATCH,
    FROZEN_AM_UNIVERSE_SOURCE_DRIFT,
    POST_BIND_UNIVERSE_MUTATION,
    SAME_DAY_AM_FROZEN_AUTHORITY,
    bind_same_day_am_desired_universe,
    canonical_membership_sha,
    detect_frozen_source_csv_drift,
    freeze_same_day_am_universe,
    load_am_canonical_50,
    load_am_csv_from_disk,
    load_frozen_am_universe,
    load_frozen_summary,
    reuse_frozen_am_universe,
)
from small_paper.ingress_control_channel import write_desired_universe
from small_paper.kabu_registration_authority import (
    select_registration_safe_probe_symbol,
    verify_exact50_membership,
    write_actual_regist_snapshot,
)
from small_paper.market_capture_registration import write_registration_manifest
from small_paper.market_ingress_service import MarketIngressService
from small_paper.v1r_native_entry_live import resolve_day_fixed_am_runtime_universe


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


def _freeze(tmp_path: Path, day: str, symbols: list[str]) -> dict:
    path = _write_am_csv(tmp_path, day, symbols)
    return freeze_same_day_am_universe(
        tmp_path,
        day,
        symbols=symbols,
        source_path=str(path),
    )


def _state(tmp_path: Path, day: str) -> DailyRunnerState:
    return DailyRunnerState(
        options=DailyRunnerOptions(day_stamp=day, skip_kabu=True, skip_safety=True, dry_run_only=True),
        repo_root=tmp_path,
        native_root=tmp_path,
        reports_dir=tmp_path / "results" / "reports",
        push_root=tmp_path / "data" / "push_jsonl",
        trade_date=date(int(day[:4]), int(day[4:6]), int(day[6:8])),
    )


def test_case_a_freeze_ingress_daily_reuse_same50(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    day = "20260813"
    am = _am_syms()
    frozen = _freeze(tmp_path, day, am)
    assert frozen["ok"] is True
    assert frozen["authority"] == SAME_DAY_AM_FROZEN_AUTHORITY
    assert len(frozen["canonical_symbols"]) == 50
    bind = bind_same_day_am_desired_universe(tmp_path, day, symbols=am)
    assert bind["ok"] is True
    svc = _svc(tmp_path, day)
    push = _FakePush()
    svc._push_client = push
    svc._poll_desired_universe()
    assert [s for s, _ in push.regist] == am
    mem = verify_exact50_membership(tmp_path, day, actual_symbols=am)
    assert mem["ok"] is True
    calls: list[int] = []

    def _boom(*_a, **_k):
        calls.append(1)
        raise AssertionError("post-bind build_am_universe must not run")

    monkeypatch.setattr("runner.am_pm_daily_runner.build_am_universe", _boom)
    reused = reuse_frozen_am_universe(native_root=tmp_path, trading_date=day, repo_root=tmp_path)
    assert reused["ok"] is True
    assert reused["reused"] is True
    assert reused["reason"] == AM_UNIVERSE_REUSED_FROZEN
    assert reused["canonical_membership_sha"] == frozen["canonical_membership_sha"]
    assert reused["post_bind_universe_rebuild_count"] == 0
    assert reused["post_bind_universe_mutation_count"] == 0
    assert calls == []
    summary = load_frozen_summary(tmp_path)
    assert int(summary["post_bind_universe_rebuild_count"]) == 0
    svc.writer.close()
    svc.bus.stop()


def test_case_b_price_risk_would_swap_five_but_v1r_does_not_recompute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = "20260813"
    am = _am_syms()
    frozen = _freeze(tmp_path, day, am)
    rebuilds: list[int] = []

    def _rebuild(*_a, **_k):
        rebuilds.append(1)
        return {"am_output": str(tmp_path), "am_rows": [], "am_excluded": ["x"] * 5, "am_replacements": ["y"] * 5}

    monkeypatch.setattr(
        "universe.core10_dynamic40_price_risk.build_price_risk_universes",
        _rebuild,
    )
    monkeypatch.setattr(
        "universe.core10_dynamic40_price_risk.select_dynamic_vol_liq_price_risk",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not recompute")),
    )
    reused = reuse_frozen_am_universe(native_root=tmp_path, trading_date=day, repo_root=tmp_path)
    assert reused["ok"] is True
    assert reused["symbols"] == am
    assert reused["canonical_membership_sha"] == frozen["canonical_membership_sha"]
    assert rebuilds == []
    loaded = load_am_canonical_50(tmp_path, day)
    assert loaded["symbols"] == am
    assert loaded["authority"] == SAME_DAY_AM_FROZEN_AUTHORITY


def test_case_c_csv_overwrite_source_drift_fail_closed_no_new50_put(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms(start=1000)
    new50 = _am_syms(start=2000)
    frozen = _freeze(tmp_path, day, am)
    bind_same_day_am_desired_universe(tmp_path, day, symbols=am)
    svc = _svc(tmp_path, day)
    push = _FakePush()
    svc._push_client = push
    svc._poll_desired_universe()
    assert [s for s, _ in push.regist] == am
    _write_am_csv(tmp_path, day, new50)
    disk = load_am_csv_from_disk(tmp_path, day)
    assert disk["symbols"] == new50
    loaded = load_am_canonical_50(tmp_path, day)
    assert loaded["symbols"] == am
    assert loaded["source_drift"] is True
    assert loaded["reason"] == FROZEN_AM_UNIVERSE_SOURCE_DRIFT
    assert loaded["canonical_membership_sha"] == frozen["canonical_membership_sha"]
    drift = detect_frozen_source_csv_drift(tmp_path, day)
    assert drift["drift"] is True
    assert drift["reason"] == FROZEN_AM_UNIVERSE_SOURCE_DRIFT
    refused = bind_same_day_am_desired_universe(tmp_path, day, symbols=new50)
    assert refused["ok"] is False
    assert refused["reason"] == FROZEN_AM_UNIVERSE_MISMATCH
    assert refused.get("allow_put_new50") is False
    reused = reuse_frozen_am_universe(native_root=tmp_path, trading_date=day, repo_root=tmp_path)
    assert reused["ok"] is True
    assert reused["provenance_source_drift"] is True
    assert reused["symbols"] == am
    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    assert resolved["ok"] is True
    assert resolved["provenance_source_drift"] is True
    assert resolved["symbols"] == am
    assert resolved["reason"] == ""
    before = len(push.calls)
    svc._poll_desired_universe()
    put_sets = [[s for s, _ in call] for call in push.calls]
    assert all(syms != new50 for syms in put_sets)
    assert all(set(syms) == set(am) for syms in put_sets if syms)
    assert len(push.calls) >= before
    assert [s for s, _ in push.regist] == am
    svc.writer.close()
    svc.bus.stop()


def test_case_d_actual_empty_reput_frozen50(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms()
    _freeze(tmp_path, day, am)
    bind_same_day_am_desired_universe(tmp_path, day, symbols=am)
    svc = _svc(tmp_path, day)
    push = _FakePush()
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=am, generation=11, trading_date=day)
    svc._poll_desired_universe()
    assert [s for s, _ in push.regist] == am
    push.regist = []
    out = svc._maybe_register_desired_live(reason="desired_poll")
    assert out.get("skipped") is not True
    assert [s for s, _ in push.regist] == am
    assert canonical_membership_sha([s for s, _ in push.regist]) == canonical_membership_sha(am)
    svc.writer.close()
    svc.bus.stop()


def test_case_e_actual_five_off_restores_frozen50(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms(start=1000)
    drifted = am[5:] + _am_syms(n=5, start=9000)
    _freeze(tmp_path, day, am)
    bind_same_day_am_desired_universe(tmp_path, day, symbols=am)
    svc = _svc(tmp_path, day)
    push = _FakePush()
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=am, generation=22, trading_date=day)
    svc._poll_desired_universe()
    push.regist = [(s, 1) for s in drifted]
    out = svc._maybe_register_desired_live(reason="desired_poll")
    assert out.get("put_executed") is True or out.get("skipped") is not True
    assert [s for s, _ in push.regist] == am
    assert set(s for s, _ in push.regist) != set(drifted)
    svc.writer.close()
    svc.bus.stop()


def test_case_f_daily_pilot_native_share_frozen_membership_sha(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms()
    frozen = _freeze(tmp_path, day, am)
    bind_same_day_am_desired_universe(tmp_path, day, symbols=am)
    write_registration_manifest(
        tmp_path,
        trading_date=day,
        symbols=am,
        generation_id="gen_frozen",
        verified=True,
        extra={"source_trading_date": day, "actual_symbols": am, "actual_count": 50},
    )
    write_actual_regist_snapshot(
        tmp_path, trading_date=day, symbols=am, source="kabu_readonly_get", generation=1
    )
    daily = reuse_frozen_am_universe(native_root=tmp_path, trading_date=day, repo_root=tmp_path)
    loaded = load_am_canonical_50(tmp_path, day)
    probe = select_registration_safe_probe_symbol(tmp_path, day, actual_symbols=am, write_audit=False)
    native = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    sha = frozen["canonical_membership_sha"]
    assert daily["canonical_membership_sha"] == sha
    assert loaded["canonical_membership_sha"] == sha
    assert native["canonical_membership_sha"] == sha
    assert native["ok"] is True
    assert probe["ok"] is True
    assert probe["exact50"] is True
    assert canonical_membership_sha(native["symbols"]) == sha
    assert canonical_membership_sha(daily["symbols"]) == sha


def test_case_g_post_bind_build_am_universe_zero_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = "20260813"
    am = _am_syms()
    _freeze(tmp_path, day, am)
    src = inspect.getsource(_run_daily_runner_body)
    assert "reuse_frozen_am_universe" in src
    calls: list[int] = []

    def _count(*_a, **_k):
        calls.append(1)
        return {"ok": False, "error": "must_not_run"}

    monkeypatch.setattr("runner.am_pm_daily_runner.build_am_universe", _count)
    reused = reuse_frozen_am_universe(native_root=tmp_path, trading_date=day, repo_root=tmp_path)
    assert reused.get("attempted") is True
    am_prep = reused if reused.get("attempted") else build_am_universe(_state(tmp_path, day))
    assert am_prep["ok"] is True
    assert calls == []
    blocked = build_am_universe(_state(tmp_path, day))
    assert blocked["ok"] is False
    assert blocked["reason"] == POST_BIND_UNIVERSE_MUTATION
    disk = load_am_csv_from_disk(tmp_path, day)
    assert disk["symbols"] == am
    frozen = load_frozen_am_universe(tmp_path, day)
    assert frozen["canonical_symbols"] == am


def test_case_h_v7_registration_safe_probe_still_uses_frozen_intersection(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms()
    _freeze(tmp_path, day, am)
    probe = select_registration_safe_probe_symbol(
        tmp_path,
        day,
        actual_symbols=am,
        proposed_symbol="285A@1",
        write_audit=False,
    )
    assert probe["ok"] is False or probe["kabu_probe_symbol_registered"] in (True, False)
    safe = select_registration_safe_probe_symbol(
        tmp_path,
        day,
        actual_symbols=am,
        proposed_symbol=f"{am[0]}@1",
        write_audit=False,
    )
    assert safe["ok"] is True
    assert safe["exact50"] is True
    assert safe["kabu_probe_symbol_registered"] is True
    outside = select_registration_safe_probe_symbol(
        tmp_path,
        day,
        actual_symbols=am,
        proposed_symbol="9984@1",
        write_audit=False,
    )
    assert outside["ok"] is False
    assert outside["reason"] == "probe_symbol_not_in_actual_registered_set"
    assert int(outside["registration_mutation"] or 0) == 0
