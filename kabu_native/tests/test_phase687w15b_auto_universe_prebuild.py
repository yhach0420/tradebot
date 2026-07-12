"""Phase687W15B — automatic universe prebuild tests."""

from __future__ import annotations

import csv
import json
import threading
import time
from pathlib import Path

import pytest

from small_paper.market_capture_registration import candidate_universe_paths, resolve_universe_symbols
from small_paper.universe_prebuild import (
    EXPECTED_SYMBOLS,
    am_universe_path,
    find_valid_existing_universe,
    is_weekday_jst,
    previous_weekday,
    run_universe_prebuild,
    validate_universe_sot,
)


def _write_universe(path: Path, *, n: int = 50, core: int = 10, day: str = "20260713", session: str = "am", dup: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(n):
        slot = "core" if i < core else "dynamic"
        sym = f"{1000 + i}.T"
        if dup and i == n - 1:
            sym = f"{1000}.T"
        rows.append(
            {
                "symbol": sym,
                "symbol_key": f"{1000 + i}@1",
                "exchange": "1",
                "passed": "True",
                "source_bucket": "core10_discord" if slot == "core" else "vol_liq_dynamic40",
                "selected_reason": "core" if slot == "core" else "dynamic",
                "universe_slot": slot,
                "rank": str(i + 1),
                "volatility_liquidity_score": "1.0",
                "am_pm_session": session,
                "close_price": "1000",
                "tick_size": "1",
            }
        )
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def test_previous_weekday_monday_is_friday():
    assert previous_weekday("20260713") == "20260710"  # Mon -> Fri


def test_weekday_check():
    assert is_weekday_jst("20260713") is True
    assert is_weekday_jst("20260711") is False  # Saturday


def test_valid_existing_skips_generator(tmp_path: Path):
    day = "20260713"
    path = am_universe_path(tmp_path, day)
    _write_universe(path, day=day)
    calls = {"n": 0}

    def boom(**kwargs):
        calls["n"] += 1
        raise AssertionError("generator must not run")

    out = run_universe_prebuild(
        repo_root=tmp_path,
        native_root=tmp_path,
        trading_date=day,
        build_fn=boom,
    )
    assert out["ok"] is True
    assert out["verdict"] == "existing_valid"
    assert out["existing_or_generated"] == "existing"
    assert calls["n"] == 0
    assert out["symbol_count"] == 50


def test_missing_csv_auto_generates(tmp_path: Path):
    day = "20260713"
    am = am_universe_path(tmp_path, day)

    def gen(**kwargs):
        _write_universe(am, day=day)
        # also write a refresh so priority resolver can find something
        return {
            "ok": True,
            "generator_exit_code": 0,
            "feature_source_path": str(tmp_path / "features.csv"),
            "am_csv": str(am),
        }

    out = run_universe_prebuild(
        repo_root=tmp_path,
        native_root=tmp_path,
        trading_date=day,
        build_fn=gen,
        enable_intraday_refresh=False,
    )
    assert out["ok"] is True
    assert out["verdict"] == "generated"
    assert out["symbol_count"] == 50
    resolved = resolve_universe_symbols(tmp_path, day, allow_empty=False)
    assert resolved["ok"] and resolved["symbol_count"] == 50


def test_empty_csv_regenerates(tmp_path: Path):
    day = "20260713"
    am = am_universe_path(tmp_path, day)
    am.parent.mkdir(parents=True, exist_ok=True)
    am.write_text("symbol\n", encoding="utf-8")

    def gen(**kwargs):
        _write_universe(am, day=day)
        return {"ok": True, "generator_exit_code": 0, "feature_source_path": "", "am_csv": str(am)}

    out = run_universe_prebuild(
        repo_root=tmp_path, native_root=tmp_path, trading_date=day, build_fn=gen, enable_intraday_refresh=False
    )
    assert out["ok"] and out["verdict"] == "generated"


def test_49_symbols_invalid_then_regenerate(tmp_path: Path):
    day = "20260713"
    am = am_universe_path(tmp_path, day)
    _write_universe(am, n=49, core=10, day=day)
    val = validate_universe_sot(am, trading_date=day, require_session="am")
    assert val["ok"] is False

    def gen(**kwargs):
        _write_universe(am, n=50, day=day)
        return {"ok": True, "generator_exit_code": 0, "feature_source_path": "", "am_csv": str(am)}

    out = run_universe_prebuild(
        repo_root=tmp_path, native_root=tmp_path, trading_date=day, build_fn=gen, enable_intraday_refresh=False
    )
    assert out["ok"] and out["symbol_count"] == 50


def test_51_symbols_block_or_regen(tmp_path: Path):
    day = "20260713"
    am = am_universe_path(tmp_path, day)
    _write_universe(am, n=51, core=10, day=day)
    assert validate_universe_sot(am, trading_date=day, require_session="am")["ok"] is False

    def gen_fail(**kwargs):
        # regenerate still 51 → validation fails
        _write_universe(am, n=51, core=10, day=day)
        return {"ok": True, "generator_exit_code": 0, "feature_source_path": "", "am_csv": str(am)}

    out = run_universe_prebuild(
        repo_root=tmp_path, native_root=tmp_path, trading_date=day, build_fn=gen_fail, enable_intraday_refresh=False
    )
    assert out["ok"] is False
    assert out["verdict"] == "universe_validation_failed"


def test_duplicate_symbol_block(tmp_path: Path):
    day = "20260713"
    am = am_universe_path(tmp_path, day)
    _write_universe(am, n=50, day=day, dup=True)
    val = validate_universe_sot(am, trading_date=day, require_session="am")
    assert val["ok"] is False
    assert val["duplicate_count"] >= 1


def test_wrong_date_block(tmp_path: Path):
    day = "20260713"
    # Write yesterday's filename into reports — must not be accepted for today
    wrong = tmp_path / "results" / "reports" / "universe_core10_dynamic40_price_risk_am_20260710.csv"
    _write_universe(wrong, day="20260710")
    chosen, _ = find_valid_existing_universe(tmp_path, day)
    assert chosen is None
    val = validate_universe_sot(wrong, trading_date=day, require_session="am")
    assert val["ok"] is False
    assert val["reason"] == "wrong_trading_date"


def test_generator_failure_block(tmp_path: Path):
    day = "20260713"

    def gen(**kwargs):
        return {"ok": False, "generator_exit_code": 2, "error": "features_missing_or_too_small", "feature_source_path": ""}

    out = run_universe_prebuild(
        repo_root=tmp_path, native_root=tmp_path, trading_date=day, build_fn=gen, enable_intraday_refresh=False
    )
    assert out["ok"] is False
    assert out["verdict"] == "universe_generation_failed"


def test_features_missing_block(tmp_path: Path):
    day = "20260713"

    def gen(**kwargs):
        return {"ok": False, "generator_exit_code": 1, "error": "features_missing_or_too_small", "feature_source_path": ""}

    out = run_universe_prebuild(
        repo_root=tmp_path, native_root=tmp_path, trading_date=day, build_fn=gen, enable_intraday_refresh=False
    )
    assert "features_missing" in str(out.get("error_reason") or out.get("generator_detail") or out)


def test_weekend_non_trading_day(tmp_path: Path):
    out = run_universe_prebuild(
        repo_root=tmp_path,
        native_root=tmp_path,
        trading_date="20260711",  # Sat
        build_fn=lambda **k: (_ for _ in ()).throw(AssertionError("no gen")),
    )
    assert out["ok"] is False
    assert out["verdict"] == "non_trading_day"


def test_synthetic_skip(tmp_path: Path):
    out = run_universe_prebuild(
        repo_root=tmp_path,
        native_root=tmp_path,
        trading_date="20260711",
        allow_synthetic=True,
        build_fn=lambda **k: (_ for _ in ()).throw(AssertionError("no gen")),
    )
    assert out["ok"] and out["verdict"] == "synthetic_skip"


def test_idempotent_rerun(tmp_path: Path):
    day = "20260713"
    am = am_universe_path(tmp_path, day)
    gens = {"n": 0}

    def gen(**kwargs):
        gens["n"] += 1
        _write_universe(am, day=day)
        return {"ok": True, "generator_exit_code": 0, "feature_source_path": "", "am_csv": str(am)}

    a = run_universe_prebuild(
        repo_root=tmp_path, native_root=tmp_path, trading_date=day, build_fn=gen, enable_intraday_refresh=False
    )
    b = run_universe_prebuild(
        repo_root=tmp_path, native_root=tmp_path, trading_date=day, build_fn=gen, enable_intraday_refresh=False
    )
    assert a["ok"] and b["ok"]
    assert gens["n"] == 1
    assert b["verdict"] == "existing_valid"


def test_no_previous_day_fallback(tmp_path: Path):
    day = "20260713"
    prev = previous_weekday(day)
    prev_path = am_universe_path(tmp_path, prev)
    _write_universe(prev_path, day=prev)
    # Today missing — must NOT pick prev even though valid
    chosen, _ = find_valid_existing_universe(tmp_path, day)
    assert chosen is None
    for p in candidate_universe_paths(tmp_path, day):
        assert prev not in p.name or day in p.name


def test_resolver_priority_unchanged(tmp_path: Path):
    day = "20260713"
    paths = candidate_universe_paths(tmp_path, day)
    names = [p.name for p in paths]
    assert names[0].startswith("universe_core10_dynamic40_price_risk_pm_refresh1430_")
    assert "am_" in names[3]


def test_dual_lock_serializes(tmp_path: Path):
    day = "20260713"
    am = am_universe_path(tmp_path, day)
    results: list[dict] = []
    gens = {"n": 0}

    def gen(**kwargs):
        gens["n"] += 1
        time.sleep(0.1)
        if not am.is_file():
            _write_universe(am, day=day)
        return {"ok": True, "generator_exit_code": 0, "feature_source_path": "", "am_csv": str(am)}

    def worker():
        results.append(
            run_universe_prebuild(
                repo_root=tmp_path,
                native_root=tmp_path,
                trading_date=day,
                build_fn=gen,
                enable_intraday_refresh=False,
            )
        )

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert len(results) == 2
    assert all(r.get("ok") for r in results)
    assert gens["n"] == 1
    assert sum(1 for r in results if r.get("verdict") == "generated") == 1
    assert sum(1 for r in results if r.get("verdict") == "existing_valid") == 1


def test_pythonpath_helper_includes_src():
    from small_paper.paper_trade_checked_runner import default_pythonpath

    pp = default_pythonpath()
    assert "src" in pp


def test_checked_runner_step_prebuild_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from small_paper.paper_trade_checked_runner import PaperTradeCheckedRunner

    day = "20260713"
    am = am_universe_path(tmp_path, day)
    _write_universe(am, day=day)
    monkeypatch.setattr(
        "small_paper.paper_trade_checked_runner.trading_date_jst",
        lambda now=None: day,
    )
    r = PaperTradeCheckedRunner(
        repo_root=tmp_path,
        native_root=tmp_path,
        paper_bat=tmp_path / "run_paper_trade.bat",
        config_path=tmp_path / "cfg.yaml",
        skip_paper=True,
        skip_w4s=True,
        capture_synthetic=True,
    )
    r.trading_date = day
    # With synthetic, prebuild skips generation; also existing valid would pass without synthetic
    assert r.step_universe_prebuild() is True
    assert r.universe_prebuild.get("ok") is True
