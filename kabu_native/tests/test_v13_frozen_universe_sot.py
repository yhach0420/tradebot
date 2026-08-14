"""V13: frozen artifact is Runtime SoT; PM rebuild must not write AM source."""
from __future__ import annotations

import csv
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runner.am_pm_daily_runner import _build_pm_universe_price_risk
from small_paper.day_fixed_am_registration import (
    FROZEN_AM_UNIVERSE_MISMATCH,
    FROZEN_AM_UNIVERSE_SOURCE_DRIFT,
    SAME_DAY_AM_FROZEN_AUTHORITY,
    bind_same_day_am_desired_universe,
    canonical_membership_sha,
    detect_frozen_source_csv_drift,
    file_sha256,
    freeze_same_day_am_universe,
    load_am_canonical_50,
    load_frozen_am_universe,
    load_frozen_summary,
    publish_runtime_desired_universe,
    reuse_frozen_am_universe,
)
from small_paper.v1r_native_entry_live import (
    boot_v1r_native_entry,
    reset_native_entry_for_tests,
    resolve_day_fixed_am_runtime_universe,
)
from small_paper.v1r_prospective_day_gate import is_valid_prospective_day
from universe.core10_dynamic40_price_risk import build_price_risk_universes

FROZEN_SHA_20260814 = "6de1fc3aa1543872fb962ff8d26a7408d219175b575cba52d627ba3627acac3a"
NATIVE = Path(__file__).resolve().parents[1]
FROZEN_JSON_20260814 = NATIVE / "runtime" / "same_day_am_frozen_universe_20260814.json"


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


def _freeze(root: Path, day: str, symbols: list[str]) -> dict:
    path = _write_am_csv(root, day, symbols)
    return freeze_same_day_am_universe(root, day, symbols=symbols, source_path=str(path))


def _dummy_rows(symbols: list[str], session: str) -> list[dict]:
    out = []
    for i, bare in enumerate(symbols):
        slot = "core" if i < 10 else "dynamic"
        out.append(
            {
                "symbol": f"{bare}.T",
                "symbol_key": f"{bare}@1",
                "exchange": "1",
                "passed": "True",
                "source_bucket": "core10_discord" if slot == "core" else "vol_liq_dynamic40",
                "selected_reason": slot,
                "universe_slot": slot,
                "rank": str(i + 1),
                "am_pm_session": session,
            }
        )
    return out


def _v12_resolver_source_drift_fatal(loaded: dict, frozen: dict) -> dict:
    """Characterization of V12: provenance source drift emptied native universe."""
    if frozen.get("present") and loaded.get("source_drift"):
        return {
            "ok": False,
            "reason": FROZEN_AM_UNIVERSE_SOURCE_DRIFT,
            "symbols": list(loaded.get("symbols") or []),
        }
    return {"ok": True, "reason": "", "symbols": list(loaded.get("symbols") or [])}


def test_case_a_pm_builder_does_not_write_am_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = "20260814"
    am = _am_syms()
    frozen = _freeze(tmp_path, day, am)
    assert frozen["ok"] is True
    am_path = tmp_path / "results" / "reports" / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    sha0 = file_sha256(am_path)
    mtime0 = am_path.stat().st_mtime
    bytes0 = am_path.read_bytes()

    pm_syms = _am_syms(start=2000)

    def _pm(**_k):
        return _dummy_rows(pm_syms, "pm"), [], []

    monkeypatch.setattr(
        "universe.core10_dynamic40_price_risk.build_pm_universe_price_risk", _pm
    )
    src = inspect.getsource(_build_pm_universe_price_risk)
    assert "write_am=False" in src

    reports = tmp_path / "results" / "reports"
    build = build_price_risk_universes(
        reports_dir=reports,
        day_stamp=day,
        core_symbols=[f"{s}.T" for s in am[:10]],
        feature_rows=[{"symbol": f"{am[0]}.T", "close": "100"}],
        symbol_meta={},
        push_day_dir=tmp_path / "push",
        write_am=False,
        write_pm=True,
    )
    assert build["write_am"] is False
    assert am_path.read_bytes() == bytes0
    assert file_sha256(am_path) == sha0
    assert am_path.stat().st_mtime == mtime0
    pm_path = Path(build["pm_output"])
    assert pm_path.is_file()
    assert pm_path != am_path
    summary = load_frozen_summary(tmp_path)
    assert int(summary.get("post_bind_universe_mutation_count") or 0) == 0


def test_case_b_provenance_source_drift_does_not_empty_universe(tmp_path: Path) -> None:
    day = "20260814"
    am = _am_syms()
    frozen = _freeze(tmp_path, day, am)
    _write_am_csv(tmp_path, day, _am_syms(start=9000))
    loaded = load_am_canonical_50(tmp_path, day)
    assert loaded["ok"] is True
    assert loaded["provenance_source_drift"] is True
    assert loaded["symbols"] == am
    assert loaded["canonical_membership_sha"] == frozen["canonical_membership_sha"]
    drift = detect_frozen_source_csv_drift(tmp_path, day)
    assert drift["drift"] is True
    assert drift["fatal"] is False
    assert drift["ok"] is True
    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    assert resolved["ok"] is True
    assert resolved["provenance_source_drift"] is True
    assert resolved["symbols"] == am
    assert resolved["symbol_count"] == 50
    reset_native_entry_for_tests()
    eng = boot_v1r_native_entry(
        universe=list(resolved["symbols"]),
        universe_source=str(resolved.get("source") or ""),
    )
    assert eng.ready is True
    assert len(eng.universe) == 50
    assert "EMPTY_UNIVERSE" not in str(eng.fail_reason or "")
    reset_native_entry_for_tests()


def test_case_c_frozen_csv_byte_change_fail_closed(tmp_path: Path) -> None:
    day = "20260814"
    am = _am_syms()
    _freeze(tmp_path, day, am)
    csv_path = tmp_path / "results" / "reports" / f"same_day_am_frozen_universe_{day}.csv"
    csv_path.write_bytes(csv_path.read_bytes() + b"\n")
    frozen = load_frozen_am_universe(tmp_path, day)
    assert frozen["ok"] is False
    assert frozen["reason"] == FROZEN_AM_UNIVERSE_MISMATCH
    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    assert resolved["ok"] is False
    reset_native_entry_for_tests()
    eng = boot_v1r_native_entry(universe=[], universe_source=f"unresolved:{resolved.get('reason')}")
    assert eng.ready is False
    reset_native_entry_for_tests()


def test_case_d_frozen_json_membership_change_fail_closed(tmp_path: Path) -> None:
    day = "20260814"
    am = _am_syms()
    _freeze(tmp_path, day, am)
    path = tmp_path / "runtime" / f"same_day_am_frozen_universe_{day}.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["canonical_symbols"] = _am_syms(start=3000)
    body["canonical_membership_sha"] = canonical_membership_sha(body["canonical_symbols"])
    body["runtime_membership_sha"] = body["canonical_membership_sha"]
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    frozen = load_frozen_am_universe(tmp_path, day)
    assert frozen["ok"] is False
    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    assert resolved["ok"] is False


def test_case_e_pm_screening_differs_v1r_and_kabu_stay_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = "20260814"
    am = _am_syms()
    frozen = _freeze(tmp_path, day, am)
    pm = _am_syms(start=4000)

    def _pm(**_k):
        return _dummy_rows(pm, "pm"), [], []

    monkeypatch.setattr(
        "universe.core10_dynamic40_price_risk.build_pm_universe_price_risk", _pm
    )
    reports = tmp_path / "results" / "reports"
    build = build_price_risk_universes(
        reports_dir=reports,
        day_stamp=day,
        core_symbols=[f"{s}.T" for s in pm[:10]],
        feature_rows=[{"symbol": f"{pm[0]}.T", "close": "100"}],
        symbol_meta={},
        push_day_dir=tmp_path / "push",
        write_am=False,
        write_pm=True,
    )
    pm_path = Path(build["pm_output"])
    assert pm_path.is_file()
    pm_text = pm_path.read_text(encoding="utf-8")
    assert "4000.T" in pm_text
    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    assert resolved["ok"] is True
    assert resolved["symbols"] == am
    assert canonical_membership_sha(resolved["symbols"]) == frozen["canonical_membership_sha"]
    pub = publish_runtime_desired_universe(tmp_path, day, fallback_symbols=pm)
    assert pub["ok"] is True
    assert pub["symbols"] == am
    refused = bind_same_day_am_desired_universe(tmp_path, day, symbols=pm)
    assert refused["ok"] is False


def test_case_f_pm_direct_start_reuses_frozen50(tmp_path: Path) -> None:
    day = "20260814"
    am = _am_syms()
    frozen = _freeze(tmp_path, day, am)
    reused = reuse_frozen_am_universe(native_root=tmp_path, trading_date=day, repo_root=tmp_path)
    assert reused["ok"] is True
    assert reused["reason"] == "AM_UNIVERSE_REUSED_FROZEN"
    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    assert resolved["ok"] is True
    assert resolved["symbol_count"] == 50
    assert resolved["canonical_membership_sha"] == frozen["canonical_membership_sha"]
    reset_native_entry_for_tests()
    eng = boot_v1r_native_entry(universe=list(resolved["symbols"]), universe_source=resolved["source"])
    assert eng.ready is True
    assert len(eng.universe) == 50
    reset_native_entry_for_tests()


def test_case_b_session_symbols_pm_diff_does_not_block_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from small_paper.pilot_runner import _init_v1r_native_entry_for_live

    day = "20260814"
    am = _am_syms()
    _freeze(tmp_path, day, am)
    pm = _am_syms(start=5000)
    monkeypatch.setattr("small_paper.v1r_live_dual_lane.live_primary_enabled", lambda: True)
    writer = SimpleNamespace(output_dir=tmp_path / "live", append_error=lambda *_a, **_k: None)
    (tmp_path / "live").mkdir()
    state = SimpleNamespace(v1r_native_entry_blocked=False, v1r_native_block_reason="", v1r_day_fixed_universe=[])
    wiring = _init_v1r_native_entry_for_live(
        state=state,
        writer=writer,
        native_root=tmp_path,
        trading_date=day,
        session_symbols=pm,
    )
    assert wiring["ready"] is True
    assert wiring["native_universe_count"] == 50
    assert wiring["resolved"]["ok"] is True
    assert wiring["resolved"].get("screening_session_diff") is True
    reset_native_entry_for_tests()


def test_8_14_exact_regression_v12_empty_v13_frozen50(tmp_path: Path) -> None:
    day = "20260814"
    assert FROZEN_JSON_20260814.is_file()
    real = json.loads(FROZEN_JSON_20260814.read_text(encoding="utf-8"))
    am = [str(s).replace(".T", "") for s in real["canonical_symbols"]]
    assert len(am) == 50
    assert canonical_membership_sha(am) == FROZEN_SHA_20260814
    frozen = _freeze(tmp_path, day, am)
    assert frozen["canonical_membership_sha"] == FROZEN_SHA_20260814
    _write_am_csv(tmp_path, day, _am_syms(start=8000))
    loaded = load_am_canonical_50(tmp_path, day)
    v12 = _v12_resolver_source_drift_fatal(loaded, {"present": True})
    assert v12["ok"] is False
    assert v12["reason"] == FROZEN_AM_UNIVERSE_SOURCE_DRIFT
    resolved = resolve_day_fixed_am_runtime_universe(native_root=tmp_path, trading_date=day)
    assert resolved["ok"] is True
    assert resolved["symbol_count"] == 50
    assert resolved["canonical_membership_sha"] == FROZEN_SHA_20260814
    assert resolved["provenance_source_drift"] is True
    reset_native_entry_for_tests()
    eng = boot_v1r_native_entry(universe=list(resolved["symbols"]), universe_source=resolved["source"])
    assert eng.ready is True
    assert len(eng.universe) == 50
    reset_native_entry_for_tests()


def test_write_am_after_freeze_is_contract_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    day = "20260814"
    am = _am_syms()
    _freeze(tmp_path, day, am)
    am_path = tmp_path / "results" / "reports" / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    sha0 = file_sha256(am_path)

    def _am(**_k):
        return _dummy_rows(_am_syms(start=7000), "am"), [], []

    monkeypatch.setattr(
        "universe.core10_dynamic40_price_risk.build_am_universe_price_risk", _am
    )
    build_price_risk_universes(
        reports_dir=tmp_path / "results" / "reports",
        day_stamp=day,
        core_symbols=[f"{s}.T" for s in am[:10]],
        feature_rows=[{"symbol": f"{am[0]}.T", "close": "100"}],
        symbol_meta={},
        push_day_dir=tmp_path / "push",
        write_am=True,
        write_pm=False,
    )
    assert file_sha256(am_path) == sha0
    summary = load_frozen_summary(tmp_path)
    assert int(summary.get("post_bind_universe_mutation_count") or 0) >= 1


def test_legacy_20260814_freeze_json_still_ok() -> None:
    frozen = load_frozen_am_universe(NATIVE, "20260814")
    assert frozen["ok"] is True
    assert frozen["canonical_membership_sha"] == FROZEN_SHA_20260814
    assert frozen["runtime_count"] == 50
    resolved = resolve_day_fixed_am_runtime_universe(native_root=NATIVE, trading_date="20260814")
    assert resolved["ok"] is True
    assert resolved["canonical_membership_sha"] == FROZEN_SHA_20260814
    assert resolved["symbol_count"] == 50
    assert is_valid_prospective_day("20260814") is False


def test_strategy_precommit_unchanged() -> None:
    from small_paper.v1r_exit_v2_activation_gate import PRECOMMIT_SHA, STRATEGY_SHA

    assert STRATEGY_SHA == "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
    assert PRECOMMIT_SHA == "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"


def test_v12_activation_bytes_immutable() -> None:
    p = (
        NATIVE
        / "results"
        / "research"
        / "v1r_exit_v2_prospective_activation"
        / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V12.json"
    )
    body = p.read_text(encoding="utf-8")
    assert "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V12" in body
    assert "934c082e402020b9ac2d4b3c7d240a06bc67ebf9cd11d8d1d9b04214f5f11982" in body
