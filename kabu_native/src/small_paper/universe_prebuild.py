"""Phase687W15B — Automatic AM universe prebuild for checked runner.

Uses existing ``runner.am_pm_daily_runner.build_am_universe`` (no selection fork).
Never falls back to a previous trading day's universe CSV.
"""

from __future__ import annotations

import csv
import json
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.market_capture_registration import (
    FileLock,
    RegistrationLockError,
    candidate_universe_paths,
    load_symbols_from_universe_csv,
)

JST = ZoneInfo("Asia/Tokyo")

EXPECTED_SYMBOLS = 50
EXPECTED_CORE = 10
EXPECTED_DYNAMIC = 40
UNIVERSE_MODE = "core10-dynamic40-price-risk-filter-shadow"
PREBUILD_LOCK_NAME = "universe_prebuild.lock"


def _parse_day(day_stamp: str) -> date:
    return date(int(day_stamp[:4]), int(day_stamp[4:6]), int(day_stamp[6:8]))


def is_weekday_jst(day_stamp: str) -> bool:
    """Existing convention: Mon–Fri calendar day (no invented JPX holiday calendar)."""
    return _parse_day(day_stamp).weekday() < 5


def previous_weekday(day_stamp: str) -> str:
    """Previous Mon–Fri calendar day (Mon → Fri). Not a full exchange holiday calendar."""
    d = _parse_day(day_stamp)
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def am_universe_path(native_root: Path, day_stamp: str) -> Path:
    return (
        Path(native_root)
        / "results"
        / "reports"
        / f"universe_core10_dynamic40_price_risk_am_{day_stamp}.csv"
    )


def features_path(native_root: Path, day_stamp: str) -> Path:
    return Path(native_root) / "results" / "reports" / f"features_{day_stamp}.csv"


def prebuild_lock_path(native_root: Path) -> Path:
    d = Path(native_root) / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d / PREBUILD_LOCK_NAME


def _slot_counts(path: Path) -> tuple[int, int, int, int, list[str]]:
    """Return core, dynamic, total, duplicates, symbols."""
    if not path.is_file():
        return 0, 0, 0, 0, []
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append({k: str(v or "") for k, v in row.items()})
    syms: list[str] = []
    for row in rows:
        sym = ""
        for key in ("symbol", "Symbol", "code", "Code"):
            if row.get(key):
                sym = str(row[key]).strip()
                break
        if not sym and row:
            sym = str(next(iter(row.values())) or "").strip()
        syms.append(sym)
    core = sum(1 for r in rows if str(r.get("universe_slot") or "").lower() == "core")
    dyn = sum(1 for r in rows if str(r.get("universe_slot") or "").lower() == "dynamic")
    nonempty = [s for s in syms if s]
    dup = len(nonempty) - len(set(nonempty))
    return core, dyn, len(rows), dup, syms


def validate_universe_sot(
    path: Path,
    *,
    trading_date: str,
    require_session: Optional[str] = None,
) -> dict[str, Any]:
    """Strict SoT validation for checked-runner prebuild (fail-closed)."""
    out: dict[str, Any] = {
        "path": str(path) if path else "",
        "trading_date": trading_date,
        "ok": False,
        "symbol_count": 0,
        "core_count": 0,
        "dynamic_count": 0,
        "duplicate_count": 0,
        "empty_symbol_count": 0,
        "reason": "",
        "checks": [],
    }
    if path is None or not path.is_file():
        out["reason"] = "universe_csv_missing"
        return out
    name = path.name
    if trading_date not in name:
        out["reason"] = "wrong_trading_date"
        out["checks"].append({"check": "filename_date", "passed": False, "detail": name})
        return out
    if name.endswith(".tmp") or ".tmp." in name or name.endswith(".partial"):
        out["reason"] = "temp_or_partial_file"
        return out
    try:
        core, dyn, total, dup, syms = _slot_counts(path)
    except Exception as exc:
        out["reason"] = "csv_parse_error"
        out["checks"].append({"check": "parse", "passed": False, "detail": type(exc).__name__})
        return out
    empty_n = sum(1 for s in syms if not s)
    out.update(
        {
            "symbol_count": total,
            "core_count": core,
            "dynamic_count": dyn,
            "duplicate_count": dup,
            "empty_symbol_count": empty_n,
        }
    )
    checks = [
        ("file_exists", True, name),
        ("filename_date", trading_date in name, name),
        ("symbol_count_50", total == EXPECTED_SYMBOLS, f"count={total}"),
        ("core_10", core == EXPECTED_CORE, f"core={core}"),
        ("dynamic_40", dyn == EXPECTED_DYNAMIC, f"dynamic={dyn}"),
        ("no_duplicates", dup == 0, f"dup={dup}"),
        ("no_empty_symbols", empty_n == 0, f"empty={empty_n}"),
        ("not_over_50", total <= EXPECTED_SYMBOLS, f"count={total}"),
    ]
    if require_session:
        try:
            with path.open(encoding="utf-8-sig", newline="") as fh:
                sessions = {str(r.get("am_pm_session") or "") for r in csv.DictReader(fh)}
            checks.append(
                ("am_pm_session", sessions == {require_session}, f"sessions={sessions}")
            )
        except Exception as exc:
            checks.append(("am_pm_session", False, type(exc).__name__))
    out["checks"] = [{"check": c, "passed": p, "detail": d} for c, p, d in checks]
    failed = [c for c, p, _ in checks if not p]
    if failed:
        out["reason"] = "universe_validation_failed"
        out["failed_checks"] = failed
        return out
    # Prefer load_symbols path for registerable uniqueness
    loaded = load_symbols_from_universe_csv(path, limit=EXPECTED_SYMBOLS + 5)
    if len(loaded) != EXPECTED_SYMBOLS:
        out["reason"] = "universe_validation_failed"
        out["failed_checks"] = ["load_symbols_count"]
        out["checks"].append(
            {
                "check": "load_symbols_count",
                "passed": False,
                "detail": f"loaded={len(loaded)}",
            }
        )
        return out
    out["ok"] = True
    out["reason"] = "valid"
    out["symbols"] = loaded
    return out


def find_valid_existing_universe(
    native_root: Path, trading_date: str
) -> tuple[Optional[Path], dict[str, Any]]:
    """Walk resolver priority; return first strictly valid same-day SoT."""
    details: list[dict[str, Any]] = []
    for path in candidate_universe_paths(native_root, trading_date):
        # Never accept a different day's file even if somehow listed.
        if trading_date not in path.name:
            details.append({"path": str(path), "skipped": "wrong_date_in_name"})
            continue
        sess = None
        if "_am_" in path.name or "am_refresh" in path.name:
            sess = "am"
        elif "_pm_" in path.name or "pm_refresh" in path.name:
            sess = "pm"
        val = validate_universe_sot(path, trading_date=trading_date, require_session=sess)
        details.append(val)
        if val.get("ok"):
            return path, {"chosen": str(path), "validation": val, "candidates": details}
    return None, {"chosen": None, "validation": None, "candidates": details}


def _quarantine_invalid(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    stamp = datetime.now(JST).strftime("%Y%m%dT%H%M%S")
    dest = path.with_name(f"{path.name}.invalid.{stamp}.bak")
    try:
        path.replace(dest)
        return str(dest)
    except OSError:
        try:
            shutil.copy2(path, dest)
            path.unlink(missing_ok=True)
            return str(dest)
        except OSError:
            return None


def _run_build_am_universe(
    *,
    repo_root: Path,
    native_root: Path,
    trading_date: str,
    enable_intraday_refresh: bool = True,
) -> dict[str, Any]:
    from runner.am_pm_daily_runner import (
        TRAILING_MFE_SHADOW_YAML,
        DailyRunnerOptions,
        UNIVERSE_MODE_PRICE_RISK,
        build_am_universe,
        build_intraday_refresh_universes,
        features_csv,
        make_state,
    )
    from universe.daily_features import load_features_csv

    options = DailyRunnerOptions(
        day_stamp=trading_date,
        skip_kabu=True,
        skip_safety=True,
        skip_am=False,
        skip_pm=True,
        dry_run_only=True,
        generate_features=True,
        config_rel=TRAILING_MFE_SHADOW_YAML,
        universe_mode=UNIVERSE_MODE_PRICE_RISK,
        enable_intraday_refresh=enable_intraday_refresh,
    )
    state = make_state(repo_root, native_root, options)
    # Ensure reports_dir resolves under native_root when repo layout is standard.
    expected_reports = Path(native_root) / "results" / "reports"
    if state.reports_dir.resolve() != expected_reports.resolve():
        # Prefer native reports so resolve_universe_symbols sees the same files.
        if expected_reports.parent.is_dir() or True:
            expected_reports.mkdir(parents=True, exist_ok=True)
            state.reports_dir = expected_reports

    started = time.time()
    am_prep = build_am_universe(state)
    refresh: dict[str, Any] = {}
    if enable_intraday_refresh and am_prep.get("ok"):
        feat_path = features_csv(state.reports_dir, trading_date)
        feature_rows = load_features_csv(feat_path) if feat_path.is_file() else []
        open_test: list[str] = []
        for row in am_prep.get("am_rows") or []:
            if str(row.get("universe_slot") or "") == "dynamic":
                sym = str(row.get("symbol") or "")
                if sym:
                    open_test = [sym]
                    break
        refresh = build_intraday_refresh_universes(
            state, feature_rows=feature_rows, open_symbols_test=open_test
        )
    duration = round(time.time() - started, 3)
    feat_path = features_csv(state.reports_dir, trading_date)
    return {
        "ok": bool(am_prep.get("ok"))
        and (not enable_intraday_refresh or bool(refresh.get("ok", True))),
        "am_prep": {k: am_prep.get(k) for k in am_prep if k != "am_rows"},
        "intraday_refresh": {
            k: refresh.get(k) for k in (refresh or {}) if k not in ("am_rows", "pm_rows")
        },
        "feature_source_path": str(feat_path) if feat_path.is_file() else "",
        "features_exists": feat_path.is_file(),
        "duration_sec": duration,
        "generator_exit_code": 0 if am_prep.get("ok") else 1,
        "error": am_prep.get("error") or "",
        "am_csv": am_prep.get("am_csv") or str(am_universe_path(native_root, trading_date)),
    }


def run_universe_prebuild(
    *,
    repo_root: Path,
    native_root: Path,
    trading_date: str,
    allow_synthetic: bool = False,
    force_rebuild: bool = False,
    build_fn: Optional[Callable[..., dict[str, Any]]] = None,
    enable_intraday_refresh: bool = True,
) -> dict[str, Any]:
    """Idempotent prebuild: reuse valid same-day SoT or generate via build_am_universe."""
    started_at = datetime.now(JST).isoformat(timespec="seconds")
    t0 = time.time()
    artifact: dict[str, Any] = {
        "trading_date": trading_date,
        "universe_source": None,
        "existing_or_generated": None,
        "generator_started_at": None,
        "generator_finished_at": None,
        "previous_trading_date": previous_weekday(trading_date),
        "feature_source_path": "",
        "output_path": "",
        "symbol_count": 0,
        "core_count": 0,
        "dynamic_count": 0,
        "duplicate_count": 0,
        "validation_result": {},
        "duration_sec": 0.0,
        "generator_exit_code": None,
        "error_reason": "",
        "verdict": "",
        "ok": False,
        "started_at": started_at,
    }

    if allow_synthetic:
        artifact.update(
            {
                "ok": True,
                "verdict": "synthetic_skip",
                "existing_or_generated": "synthetic",
                "symbol_count": EXPECTED_SYMBOLS,
                "core_count": EXPECTED_CORE,
                "dynamic_count": EXPECTED_DYNAMIC,
                "duration_sec": round(time.time() - t0, 3),
            }
        )
        return artifact

    if not is_weekday_jst(trading_date):
        artifact.update(
            {
                "ok": False,
                "verdict": "non_trading_day",
                "error_reason": "non_trading_day",
                "duration_sec": round(time.time() - t0, 3),
            }
        )
        return artifact

    if not force_rebuild:
        chosen, probe = find_valid_existing_universe(native_root, trading_date)
        if chosen is not None:
            val = probe["validation"]
            artifact.update(
                {
                    "ok": True,
                    "verdict": "existing_valid",
                    "existing_or_generated": "existing",
                    "universe_source": str(chosen),
                    "output_path": str(chosen),
                    "symbol_count": val.get("symbol_count"),
                    "core_count": val.get("core_count"),
                    "dynamic_count": val.get("dynamic_count"),
                    "duplicate_count": val.get("duplicate_count"),
                    "validation_result": val,
                    "feature_source_path": str(features_path(native_root, trading_date))
                    if features_path(native_root, trading_date).is_file()
                    else "",
                    "duration_sec": round(time.time() - t0, 3),
                    "generator_exit_code": 0,
                }
            )
            return artifact

    lock = FileLock(prebuild_lock_path(native_root), timeout_sec=120.0, stale_sec=600.0)
    try:
        lock.acquire()
    except RegistrationLockError as exc:
        artifact.update(
            {
                "ok": False,
                "verdict": "prebuild_lock_timeout",
                "error_reason": str(exc),
                "duration_sec": round(time.time() - t0, 3),
            }
        )
        return artifact

    try:
        # Re-check under lock (another process may have finished).
        if not force_rebuild:
            chosen, probe = find_valid_existing_universe(native_root, trading_date)
            if chosen is not None:
                val = probe["validation"]
                artifact.update(
                    {
                        "ok": True,
                        "verdict": "existing_valid",
                        "existing_or_generated": "existing",
                        "universe_source": str(chosen),
                        "output_path": str(chosen),
                        "symbol_count": val.get("symbol_count"),
                        "core_count": val.get("core_count"),
                        "dynamic_count": val.get("dynamic_count"),
                        "duplicate_count": val.get("duplicate_count"),
                        "validation_result": val,
                        "duration_sec": round(time.time() - t0, 3),
                        "generator_exit_code": 0,
                    }
                )
                return artifact

        am_path = am_universe_path(native_root, trading_date)
        if am_path.is_file():
            q = _quarantine_invalid(am_path)
            artifact["quarantined_invalid"] = q

        artifact["generator_started_at"] = datetime.now(JST).isoformat(timespec="seconds")
        builder = build_fn or _run_build_am_universe
        try:
            gen = builder(
                repo_root=Path(repo_root),
                native_root=Path(native_root),
                trading_date=trading_date,
                enable_intraday_refresh=enable_intraday_refresh,
            )
        except Exception as exc:
            artifact.update(
                {
                    "ok": False,
                    "verdict": "universe_generation_failed",
                    "error_reason": f"{type(exc).__name__}:{exc}",
                    "generator_exit_code": 1,
                    "generator_finished_at": datetime.now(JST).isoformat(timespec="seconds"),
                    "duration_sec": round(time.time() - t0, 3),
                }
            )
            return artifact

        artifact["generator_finished_at"] = datetime.now(JST).isoformat(timespec="seconds")
        artifact["feature_source_path"] = gen.get("feature_source_path") or ""
        artifact["generator_exit_code"] = int(gen.get("generator_exit_code") or 1)

        if not gen.get("ok"):
            artifact.update(
                {
                    "ok": False,
                    "verdict": "universe_generation_failed",
                    "error_reason": gen.get("error") or "universe_generation_failed",
                    "duration_sec": round(time.time() - t0, 3),
                    "generator_detail": {
                        k: gen.get(k) for k in ("am_prep", "error", "features_exists")
                    },
                }
            )
            return artifact

        # Prefer AM SoT for morning; fall back to priority walk for same day.
        chosen, probe = find_valid_existing_universe(native_root, trading_date)
        if chosen is None:
            # Explicit AM path validate for clearer errors
            val = validate_universe_sot(
                am_universe_path(native_root, trading_date),
                trading_date=trading_date,
                require_session="am",
            )
            artifact.update(
                {
                    "ok": False,
                    "verdict": "universe_validation_failed",
                    "error_reason": "universe_validation_failed",
                    "validation_result": val,
                    "symbol_count": val.get("symbol_count"),
                    "core_count": val.get("core_count"),
                    "dynamic_count": val.get("dynamic_count"),
                    "duplicate_count": val.get("duplicate_count"),
                    "output_path": str(am_universe_path(native_root, trading_date)),
                    "duration_sec": round(time.time() - t0, 3),
                    "expected_symbols": EXPECTED_SYMBOLS,
                    "actual_symbols": val.get("symbol_count"),
                }
            )
            return artifact

        val = probe["validation"]
        artifact.update(
            {
                "ok": True,
                "verdict": "generated",
                "existing_or_generated": "generated",
                "universe_source": str(chosen),
                "output_path": str(chosen),
                "symbol_count": val.get("symbol_count"),
                "core_count": val.get("core_count"),
                "dynamic_count": val.get("dynamic_count"),
                "duplicate_count": val.get("duplicate_count"),
                "validation_result": val,
                "duration_sec": round(time.time() - t0, 3),
                "generator_exit_code": 0,
            }
        )
        return artifact
    finally:
        lock.release()


def write_prebuild_artifact(native_root: Path, trading_date: str, payload: Mapping[str, Any]) -> Path:
    out_dir = Path(native_root) / "results" / "reports" / "phase687w15b_auto_universe_prebuild"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"universe_prebuild_{trading_date}.json"
    path.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path
