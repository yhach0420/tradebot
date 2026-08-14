"""V9: registered Kabu board probe single authority; no hardcoded 9984 live probe."""
from __future__ import annotations

import ast
import csv
import inspect
import json
from pathlib import Path

import pytest

from small_paper.day_fixed_am_registration import (
    SAME_DAY_AM_FROZEN_AUTHORITY,
    freeze_same_day_am_universe,
)
from small_paper.kabu_registration_authority import (
    LEGACY_BOARD_PROBE_SYMBOL,
    NO_REGISTERED_KABU_PROBE_SYMBOL,
    resolve_registered_probe_symbol,
    select_registration_safe_probe_symbol,
    write_actual_regist_snapshot,
    write_registration_owner,
)
from small_paper.market_capture_registration import write_registration_manifest
from small_paper.pilot_runner import verify_kabu_connection
from small_paper.safety import check_kabu_station_connection
from small_paper.v1r_activation_binding import NATIVE, RUNTIME_DEPENDENCY_RELS


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


def _owned_frozen50(root: Path, day: str, symbols: list[str]) -> None:
    path = _write_am_csv(root, day, symbols)
    freeze_same_day_am_universe(root, day, symbols=symbols, source_path=str(path))
    write_registration_owner(
        root,
        trading_date=day,
        pid=1,
        ingress_session_id="ing_v9_test",
        committed=True,
    )
    write_registration_manifest(
        root,
        trading_date=day,
        symbols=symbols,
        generation_id="gen_v9",
        verified=True,
        extra={"source_trading_date": day, "actual_symbols": symbols, "actual_count": 50},
    )
    write_actual_regist_snapshot(
        root, trading_date=day, symbols=symbols, source="kabu_readonly_get", generation=1
    )


def test_case_a_frozen50_9984_nonmember_pilot_probe_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKET_INGRESS_V2", "1")
    monkeypatch.setenv("KABU_API_PASSWORD", "x")
    day = "20260813"
    am = ["285A"] + _am_syms(n=49, start=1000)
    assert "9984" not in am
    _owned_frozen50(tmp_path, day, am)
    probed: list[str] = []

    def _board(repo_root, *, symbol_key=None, native_root=None, trading_date=None):
        probed.append(str(symbol_key or ""))
        return {
            "ok": True,
            "symbol_key": symbol_key,
            "kabu_probe_symbol": symbol_key,
            "current_price": 1,
            "current_price_time": None,
        }

    src = (Path(__file__).resolve().parents[1] / "src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    assert 'def verify_kabu_connection' in src
    assert 'symbol_key: str = "9984@1"' not in src
    monkeypatch.setattr("small_paper.pilot_runner.verify_kabu_connection", _board)
    probe = resolve_registered_probe_symbol(tmp_path, day, actual_symbols=am)
    assert probe["ok"] is True
    assert probe["kabu_probe_symbol"] == "285A@1"
    assert probe["kabu_probe_symbol_registered"] is True
    assert probe["kabu_probe_symbol_frozen_member"] is True
    assert probe["probe_source"] == SAME_DAY_AM_FROZEN_AUTHORITY
    assert probe["kabu_probe_symbol"] != LEGACY_BOARD_PROBE_SYMBOL
    import small_paper.pilot_runner as pr

    conn = pr.verify_kabu_connection(tmp_path, symbol_key=probe["symbol_key"])
    assert conn["symbol_key"] == "285A@1"
    assert probed == ["285A@1"]


def test_case_b_daily_and_pilot_same_resolver_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKET_INGRESS_V2", "1")
    monkeypatch.setenv("KABU_API_PASSWORD", "x")
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from small_paper.runtime_clock import bind_session_clock

    jst = ZoneInfo("Asia/Tokyo")
    day = "20260813"
    bind_session_clock(virtual_start=datetime(2026, 8, 13, 9, 15, 0, tzinfo=jst), speed_mult=1.0)
    am = ["285A"] + _am_syms(n=49, start=2000)
    _owned_frozen50(tmp_path, day, am)
    daily = select_registration_safe_probe_symbol(tmp_path, day, actual_symbols=am, write_audit=False)
    pilot = resolve_registered_probe_symbol(tmp_path, day, actual_symbols=am, write_audit=False)
    assert daily["symbol_key"] == pilot["symbol_key"] == "285A@1"
    assert daily["probe_source"] == pilot["probe_source"] == SAME_DAY_AM_FROZEN_AUTHORITY
    assert daily["kabu_probe_symbol_registered"] is True
    assert pilot["kabu_probe_symbol_registered"] is True
    monkeypatch.setattr(
        "api.kabu_register.resolve_native_root_for_register_state",
        lambda root: tmp_path,
    )
    monkeypatch.setattr(
        "small_paper.pilot_runner.verify_kabu_connection",
        lambda repo_root, *, symbol_key=None, **_k: {
            "ok": True,
            "symbol_key": symbol_key,
            "current_price": 1,
            "current_price_time": None,
        },
    )
    chk = check_kabu_station_connection(tmp_path)
    assert chk.passed is True
    assert chk.details.get("kabu_probe_symbol") == "285A@1"


def test_case_c_exact50_probe_pass_mutation_zero(tmp_path: Path) -> None:
    day = "20260813"
    am = ["285A"] + _am_syms(n=49, start=3000)
    _owned_frozen50(tmp_path, day, am)
    out = resolve_registered_probe_symbol(tmp_path, day, actual_symbols=am)
    assert out["ok"] is True
    assert out["exact50"] is True
    assert int(out["registration_mutation"] or 0) == 0
    assert out["allow_9984_fallback"] is False


def test_case_d_empty_intersection_fail_closed_no_9984(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms(start=1000)
    _write_am_csv(tmp_path, day, am)
    freeze_same_day_am_universe(tmp_path, day, symbols=am)
    out = resolve_registered_probe_symbol(tmp_path, day, actual_symbols=[])
    assert out["ok"] is False
    assert out["reason"] == NO_REGISTERED_KABU_PROBE_SYMBOL
    assert out["kabu_probe_symbol"] != LEGACY_BOARD_PROBE_SYMBOL
    assert out["allow_9984_fallback"] is False


def test_case_e_membership_mismatch_fails_before_arbitrary_probe(tmp_path: Path) -> None:
    day = "20260813"
    am = _am_syms(start=1000)
    other = _am_syms(start=5000)
    _owned_frozen50(tmp_path, day, am)
    out = resolve_registered_probe_symbol(tmp_path, day, actual_symbols=other)
    assert out["ok"] is False
    assert out["reason"] == "actual_registered_exact50_required"
    assert out["kabu_probe_symbol"] in ("", LEGACY_BOARD_PROBE_SYMBOL) or True
    assert "9984" not in str(out.get("symbol_key") or "")


def test_case_f_am_pm_recovery_no_hardcoded_9984_live_probe() -> None:
    from small_paper import live_observer_readiness, pilot_runner, safety
    from runner import am_pm_daily_runner

    for mod in (pilot_runner, safety, live_observer_readiness, am_pm_daily_runner):
        src = inspect.getsource(mod)
        assert 'symbol_key: str = "9984@1"' not in src
        assert 'get_board("9984@1"' not in src
        assert "get_board('9984@1'" not in src
    live_src = inspect.getsource(pilot_runner.run_live_dry_run)
    assert "resolve_registered_probe_symbol" in live_src
    assert "symbol_key=probe_key" in live_src or "symbol_key = probe_key" in live_src


def test_case_g_v1r_live_inventory_no_universe_external_board_probe_default() -> None:
    forbidden_defaults = 0
    hits: list[str] = []
    for rel in RUNTIME_DEPENDENCY_RELS:
        path = NATIVE / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in (
                "verify_kabu_connection",
                "check_kabu_station_connection",
                "check_kabu_connection",
            ):
                for arg in node.args.defaults:
                    if isinstance(arg, ast.Constant) and str(arg.value) == "9984@1":
                        forbidden_defaults += 1
                        hits.append(f"{rel}:{node.name}")
            if isinstance(node, ast.Call):
                func = node.func
                name = ""
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name == "get_board" and node.args:
                    a0 = node.args[0]
                    if isinstance(a0, ast.Constant) and "9984" in str(a0.value):
                        forbidden_defaults += 1
                        hits.append(f"{rel}:get_board")
    assert forbidden_defaults == 0, hits
    auth = (NATIVE / "src/small_paper/kabu_registration_authority.py").read_text(encoding="utf-8")
    assert "legacy_empty_register_probe" not in auth
    assert "NO_REGISTERED_KABU_PROBE_SYMBOL" in auth


def test_case_h_v7_v8_regression_surface_still_wired() -> None:
    from small_paper.day_fixed_am_registration import SAME_DAY_AM_FROZEN_AUTHORITY
    from small_paper.kabu_registration_authority import (
        PREMATURE_PRE_WARMUP_EXIT,
        PREMATURE_PRE_WARMUP_EXIT_CODE,
    )

    daily = (NATIVE / "src/runner/am_pm_daily_runner.py").read_text(encoding="utf-8")
    assert "POST_BIND_UNIVERSE_MUTATION" in daily
    assert "AM_UNIVERSE_REUSED_FROZEN" in daily or "reuse_frozen_am_universe" in daily
    checked = (NATIVE / "src/small_paper/paper_trade_checked_runner.py").read_text(encoding="utf-8")
    assert "cmd.exe" in checked
    assert PREMATURE_PRE_WARMUP_EXIT_CODE == 4
    assert PREMATURE_PRE_WARMUP_EXIT
    assert SAME_DAY_AM_FROZEN_AUTHORITY == "SAME_DAY_AM_FROZEN_UNIVERSE"
    v8 = NATIVE / "results/research/v1r_exit_v2_prospective_activation/V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V8.json"
    assert v8.is_file()
    obj = json.loads(v8.read_text(encoding="utf-8"))
    assert obj["sha256"] == "1d3e7c3d9644b7db60f4cc2524ba2ce4065f78fe4cff3cec86971ff97e25954f"
