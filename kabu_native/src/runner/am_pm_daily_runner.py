"""
Phase 148: Core10 + Dynamic40 AM/PM shadow daily runner (orchestration only).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

log = __import__("logging").getLogger(__name__)

SHADOW_PILOT_YAML = "kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav_vol_liq.yaml"
ENTRY_GUARD_SHADOW_YAML = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_shadow.yaml"
)
TRAILING_MFE_SHADOW_YAML = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
TRAILING_MFE_LOW_LIQ_SHADOW_YAML = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_low_liquidity_shadow.yaml"
)
FADE_HYBRID_SHADOW_YAML = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_fade_hybrid_shadow.yaml"
)
FADE_BREAKDOWN_SHADOW_YAML = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_fade_breakdown_shadow.yaml"
)
EXPECTED_POLICY_LABEL = "q070_cap3_mfe_fav_vol_liq_trial"
ENTRY_GUARD_POLICY_LABEL = "q070_cap3_entry_price_risk_guard_shadow_trial"
TRAILING_MFE_POLICY_LABEL = "q070_cap3_entry_price_risk_guard_trailing_mfe_shadow_trial"
TRAILING_MFE_LOW_LIQ_POLICY_LABEL = (
    "q070_cap3_entry_price_risk_guard_trailing_mfe_low_liquidity_shadow_trial"
)
FADE_HYBRID_POLICY_LABEL = "q070_cap3_entry_price_risk_guard_fade_hybrid_shadow_trial"
FADE_BREAKDOWN_POLICY_LABEL = "q070_cap3_entry_price_risk_guard_fade_breakdown_shadow_trial"
UNIVERSE_MODE_LEGACY = "core10-dynamic40"
UNIVERSE_MODE_PRICE_RISK = "core10-dynamic40-price-risk-filter-shadow"
UNIVERSE_MODE_DEFAULT = UNIVERSE_MODE_PRICE_RISK
FOCUS_SYMBOL_5856 = "5856.T"
FOCUS_SYMBOL_4392 = "4392.T"
REPORTS_REL = "kabu_native/results/reports"


def _dual_write_runtime_artifacts(state: "DailyRunnerState") -> None:
    """Phase392: dual-write runtime report files; legacy reports/ remains canonical."""
    try:
        from storage.results_paths import dual_write_runtime_day_artifacts

        for msg in dual_write_runtime_day_artifacts(
            state.repo_root, state.options.day_stamp
        ):
            if msg.startswith("dual_write_failed"):
                log.warning(msg)
    except Exception as exc:
        log.warning("dual_write_runtime_artifacts error: %s", exc)
SMALL_PAPER_REL = "kabu_native/results/small_paper"
PUSH_ROOT_REL = "kabu_native/data/push_jsonl"
PHASE113_SCRIPT = "kabu_native/scripts/run_phase113_vol_liq_dynamic50_universe.py"
SAFETY_SCRIPT = "kabu_native/scripts/check_small_paper_safety.py"
PILOT_SCRIPT = "kabu_native/scripts/run_small_paper_pilot.py"

PUSH_LIMIT = 50
AM_PM_KIND = Literal["am", "pm"]
SESSION_DIR_PREFIXES = ("live_full_session_", "live_session_")

# PM universe regen at screening start (Phase114 / am_pm_universe)
PM_SCREEN_HHMM = "12:25"
AM_END_HHMM = "11:25"
AM_REFRESH_HHMM = "10:00"
PM_REFRESH_HHMM = "14:30"


@dataclass
class DailyRunnerOptions:
    day_stamp: str
    skip_kabu: bool = False
    skip_safety: bool = False
    skip_am: bool = False
    skip_pm: bool = False
    dry_run_only: bool = False
    poll_interval_sec: float = 5.0
    generate_features: bool = True
    config_rel: str = ENTRY_GUARD_SHADOW_YAML
    universe_mode: str = UNIVERSE_MODE_DEFAULT
    enable_intraday_refresh: bool = False
    exit_policy_shadow: str = ""
    low_liquidity_shadow: bool = False
    pre625_runtime_structure_mode: bool = False
    core_runtime_mode: str = ""


@dataclass
class DailyRunnerState:
    options: DailyRunnerOptions
    repo_root: Path
    native_root: Path
    reports_dir: Path
    push_root: Path
    trade_date: date
    generated_at: str = ""
    preflight: dict[str, Any] = field(default_factory=dict)
    am_prep: dict[str, Any] = field(default_factory=dict)
    am_live: dict[str, Any] = field(default_factory=dict)
    pm_wait: dict[str, Any] = field(default_factory=dict)
    pm_prep: dict[str, Any] = field(default_factory=dict)
    pm_live: dict[str, Any] = field(default_factory=dict)
    sessions: dict[str, Any] = field(default_factory=dict)
    commands: dict[str, Any] = field(default_factory=dict)
    verdict: str = "started"
    verdict_notes: list[str] = field(default_factory=list)
    stopped_reason: Optional[str] = None


def rel_path(repo_root: Path, p: Path) -> str:
    try:
        return str(p.relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def now_jst() -> datetime:
    return datetime.now(JST)


def parse_day_stamp(day_stamp: str) -> date:
    return date(int(day_stamp[:4]), int(day_stamp[4:6]), int(day_stamp[6:8]))


def is_price_risk_universe_mode(mode: str) -> bool:
    return mode == UNIVERSE_MODE_PRICE_RISK


def universe_am_csv(reports_dir: Path, day_stamp: str, *, universe_mode: str = UNIVERSE_MODE_DEFAULT) -> Path:
    if is_price_risk_universe_mode(universe_mode):
        from universe.core10_dynamic40_price_risk import universe_am_price_risk_path

        return universe_am_price_risk_path(reports_dir, day_stamp)
    from universe.core10_dynamic40 import universe_am_path

    return universe_am_path(reports_dir, day_stamp)


def universe_pm_csv(reports_dir: Path, day_stamp: str, *, universe_mode: str = UNIVERSE_MODE_DEFAULT) -> Path:
    if is_price_risk_universe_mode(universe_mode):
        from universe.core10_dynamic40_price_risk import universe_pm_price_risk_path

        return universe_pm_price_risk_path(reports_dir, day_stamp)
    from universe.core10_dynamic40 import universe_pm_path

    return universe_pm_path(reports_dir, day_stamp)


def universe_am_rel(day_stamp: str, *, universe_mode: str = UNIVERSE_MODE_DEFAULT) -> str:
    if is_price_risk_universe_mode(universe_mode):
        return f"kabu_native/results/reports/universe_core10_dynamic40_price_risk_am_{day_stamp}.csv"
    return f"kabu_native/results/reports/universe_core10_dynamic40_am_{day_stamp}.csv"


def universe_pm_rel(day_stamp: str, *, universe_mode: str = UNIVERSE_MODE_DEFAULT) -> str:
    if is_price_risk_universe_mode(universe_mode):
        return f"kabu_native/results/reports/universe_core10_dynamic40_price_risk_pm_{day_stamp}.csv"
    return f"kabu_native/results/reports/universe_core10_dynamic40_pm_{day_stamp}.csv"


def universe_am_refresh_rel(day_stamp: str) -> str:
    return (
        f"kabu_native/results/reports/"
        f"universe_core10_dynamic40_price_risk_am_refresh1000_{day_stamp}.csv"
    )


def universe_pm_refresh_rel(day_stamp: str) -> str:
    return (
        f"kabu_native/results/reports/"
        f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day_stamp}.csv"
    )


def universe_am_refresh_csv(reports_dir: Path, day_stamp: str) -> Path:
    from universe.intraday_refresh import universe_am_refresh_path

    return universe_am_refresh_path(reports_dir, day_stamp)


def universe_pm_refresh_csv(reports_dir: Path, day_stamp: str) -> Path:
    from universe.intraday_refresh import universe_pm_refresh_path

    return universe_pm_refresh_path(reports_dir, day_stamp)


def features_csv(reports_dir: Path, day_stamp: str) -> Path:
    from universe.daily_features import features_csv_path

    return features_csv_path(reports_dir, day_stamp)


def load_symbol_meta(repo_root: Path, native_root: Path) -> dict[str, dict[str, Any]]:
    from universe.dynamic_build import load_dynamic_config, resolve_symbol_master

    cfg = load_dynamic_config(native_root / "configs" / "universe_dynamic_trial.yaml")
    _, entries = resolve_symbol_master(repo_root, cfg.symbol_master_paths)
    symbol_meta: dict[str, dict[str, Any]] = {}
    for e in entries:
        sym = f"{e.parsed.code}.T"
        symbol_meta[sym] = {
            "exchange": e.parsed.exchange,
            "symbol_key": e.parsed.symbol_key,
            "market": e.market,
        }
    return symbol_meta


def ensure_features_csv(
    state: DailyRunnerState,
    *,
    generate: bool,
) -> tuple[Path, list[dict[str, str]]]:
    from universe.daily_features import load_features_csv

    feat_path = features_csv(state.reports_dir, state.options.day_stamp)
    if feat_path.is_file():
        return feat_path, load_features_csv(feat_path)

    if not generate:
        return feat_path, []

    script = state.repo_root / PHASE113_SCRIPT
    cmd = [
        sys.executable,
        str(script),
        "--day-stamp",
        state.options.day_stamp,
        "--trade-date",
        state.trade_date.isoformat(),
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(state.repo_root),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    state.am_prep.setdefault("features_generation", {})
    state.am_prep["features_generation"] = {
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout.strip()[-500:] if proc.stdout else "",
        "stderr_tail": proc.stderr.strip()[-500:] if proc.stderr else "",
    }
    if not feat_path.is_file():
        return feat_path, []
    _dual_write_runtime_artifacts(state)
    return feat_path, load_features_csv(feat_path)


def validate_universe_csv(path: Path, *, expected_session: str) -> dict[str, Any]:
    from universe.core10_dynamic40_shadow import validate_runner_universe

    return validate_runner_universe(path, expected_session=expected_session)


def notify_screening_universe_discord(
    state: DailyRunnerState,
    *,
    session_label: str,
    universe_csv: Path,
) -> dict[str, Any]:
    """Discord trade-notify: initial universe after AM/PM screening CSV is ready."""
    if state.options.dry_run_only:
        return {"sent": False, "skipped": True, "reason": "dry_run_only"}
    from small_paper.config import load_pilot_config
    from small_paper.discord_notifier import discord_notifier_from_pilot
    from storage.symbol_sources import load_symbols, symbols_list

    cfg_path = state.repo_root / state.options.config_rel
    cfg = load_pilot_config(cfg_path)
    if not cfg.discord_enabled:
        return {"sent": False, "skipped": True, "reason": "discord_disabled"}
    notifier = discord_notifier_from_pilot(cfg)
    if not notifier.active:
        return {"sent": False, "skipped": True, "reason": "discord_not_active"}
    syms = load_symbols(universe=universe_csv, native_root=state.native_root)
    watch = symbols_list(syms)
    sent = notifier.notify_universe_screening(
        session_label=session_label,
        watch_symbols=watch,
        day_stamp=state.options.day_stamp,
    )
    return {
        "sent": bool(sent),
        "session_label": session_label,
        "symbol_count": len(watch),
        "universe_csv": rel_path(state.repo_root, universe_csv),
        "trade_notify_webhook_source": notifier.trade_webhook_source(),
    }


def runner_load_check(
    path: Path,
    native_root: Path,
    repo_root: Path,
    *,
    expected_session: str,
) -> dict[str, Any]:
    from storage.symbol_sources import load_symbols

    syms = load_symbols(universe=path, native_root=native_root) if path.is_file() else []
    val = validate_universe_csv(path, expected_session=expected_session)
    return {
        "passed": len(syms) == PUSH_LIMIT and bool(val.get("passed")),
        "symbol_count": len(syms),
        "universe_validation": val,
        "path": rel_path(repo_root, path),
    }


def _validate_price_risk_symbols(am_csv: Path) -> dict[str, Any]:
    import csv

    syms: list[str] = []
    if am_csv.is_file():
        with am_csv.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                syms.append(str(row.get("symbol") or "").strip().upper())
    sym_set = set(syms)
    dup = len(syms) - len(sym_set)
    return {
        "5856_excluded": FOCUS_SYMBOL_5856 not in sym_set,
        "4392_retained": FOCUS_SYMBOL_4392 in sym_set,
        "symbol_count": len(syms),
        "duplicate_count": dup,
        "maintains_50": len(syms) == PUSH_LIMIT and dup == 0,
    }


def _build_am_universe_standard(state: DailyRunnerState, feature_rows: list[dict[str, str]], feat_path: Path) -> dict[str, Any]:
    from universe.core10_dynamic40 import build_am_universe as _build_am, write_universe_csv
    from universe.core_watchlist import load_core_watchlist

    core_symbols, _ = load_core_watchlist(state.repo_root)
    symbol_meta = load_symbol_meta(state.repo_root, state.native_root)
    am_csv = universe_am_csv(
        state.reports_dir, state.options.day_stamp, universe_mode=state.options.universe_mode
    )
    am_rows = _build_am(
        core_symbols=core_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
    )
    write_universe_csv(am_csv, am_rows)
    am_val = validate_universe_csv(am_csv, expected_session="am")
    am_runner = runner_load_check(
        am_csv, state.native_root, state.repo_root, expected_session="am"
    )
    return {
        "ok": bool(am_val.get("passed")) and bool(am_runner.get("passed")),
        "am_csv": rel_path(state.repo_root, am_csv),
        "am_row_count": len(am_rows),
        "universe_validation": am_val,
        "runner_check": am_runner,
        "price_risk_filter_enabled": False,
        "dynamic_price_risk_excluded_count": 0,
        "dynamic_price_risk_replacement_count": 0,
        "core_price_risk_warnings": [],
    }


def _build_am_universe_price_risk(state: DailyRunnerState, feature_rows: list[dict[str, str]], feat_path: Path) -> dict[str, Any]:
    from universe.core10_dynamic40_price_risk import build_price_risk_universes
    from universe.core_watchlist import load_core_watchlist

    core_symbols, _ = load_core_watchlist(state.repo_root)
    symbol_meta = load_symbol_meta(state.repo_root, state.native_root)
    push_day_dir = state.push_root / state.trade_date.isoformat()
    build = build_price_risk_universes(
        reports_dir=state.reports_dir,
        day_stamp=state.options.day_stamp,
        core_symbols=core_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
        push_day_dir=push_day_dir,
    )
    am_csv = Path(build["am_output"])
    am_rows = build.get("am_rows") or []
    am_val = validate_universe_csv(am_csv, expected_session="am")
    am_runner = runner_load_check(
        am_csv, state.native_root, state.repo_root, expected_session="am"
    )
    symbol_checks = _validate_price_risk_symbols(am_csv)
    # Phase167: 4392_retained is informational/caution only (vol_liq rank can drop legitimately).
    ok = (
        bool(am_val.get("passed"))
        and bool(am_runner.get("passed"))
        and symbol_checks.get("5856_excluded")
        and symbol_checks.get("maintains_50")
    )
    return {
        "ok": ok,
        "am_csv": rel_path(state.repo_root, am_csv),
        "am_row_count": len(am_rows),
        "am_rows": am_rows,
        "pm_csv_built": rel_path(state.repo_root, Path(build["pm_output"])),
        "universe_validation": am_val,
        "runner_check": am_runner,
        "price_risk_filter_enabled": True,
        "dynamic_price_risk_excluded_count": len(build.get("am_excluded") or []),
        "dynamic_price_risk_replacement_count": len(build.get("am_replacements") or []),
        "dynamic_price_risk_excluded": build.get("am_excluded"),
        "dynamic_price_risk_replacements": build.get("am_replacements"),
        "core_price_risk_warnings": build.get("core_price_risk_warnings") or [],
        "price_risk_symbol_checks": symbol_checks,
    }


def build_am_universe(state: DailyRunnerState) -> dict[str, Any]:
    feat_path, feature_rows = ensure_features_csv(
        state, generate=state.options.generate_features
    )
    out: dict[str, Any] = {
        "features_path": rel_path(state.repo_root, feat_path),
        "features_exists": feat_path.is_file(),
        "features_row_count": len(feature_rows),
        "universe_mode": state.options.universe_mode,
    }
    if not feat_path.is_file() or len(feature_rows) < 100:
        out["ok"] = False
        out["error"] = "features_missing_or_too_small"
        return out

    if is_price_risk_universe_mode(state.options.universe_mode):
        built = _build_am_universe_price_risk(state, feature_rows, feat_path)
    else:
        built = _build_am_universe_standard(state, feature_rows, feat_path)
    out.update(built)
    _dual_write_runtime_artifacts(state)
    return out


def _build_pm_universe_standard(state: DailyRunnerState, feature_rows: list[dict[str, str]]) -> dict[str, Any]:
    from universe.core10_dynamic40 import build_pm_universe as _build_pm, write_universe_csv
    from universe.core_watchlist import load_core_watchlist

    core_symbols, _ = load_core_watchlist(state.repo_root)
    symbol_meta = load_symbol_meta(state.repo_root, state.native_root)
    push_day_dir = state.push_root / state.trade_date.isoformat()
    pm_csv = universe_pm_csv(
        state.reports_dir, state.options.day_stamp, universe_mode=state.options.universe_mode
    )
    pm_rows = _build_pm(
        core_symbols=core_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
        push_day_dir=push_day_dir,
    )
    write_universe_csv(pm_csv, pm_rows)
    pm_val = validate_universe_csv(pm_csv, expected_session="pm")
    pm_runner = runner_load_check(
        pm_csv, state.native_root, state.repo_root, expected_session="pm"
    )
    return {
        "ok": bool(pm_val.get("passed")) and bool(pm_runner.get("passed")),
        "pm_csv": rel_path(state.repo_root, pm_csv),
        "pm_row_count": len(pm_rows),
        "push_day_dir": rel_path(state.repo_root, push_day_dir),
        "push_day_dir_exists": push_day_dir.is_dir(),
        "universe_validation": pm_val,
        "runner_check": pm_runner,
        "price_risk_filter_enabled": False,
        "dynamic_price_risk_excluded_count": 0,
        "dynamic_price_risk_replacement_count": 0,
    }


def _build_pm_universe_price_risk(state: DailyRunnerState, feature_rows: list[dict[str, str]]) -> dict[str, Any]:
    from universe.core10_dynamic40_price_risk import build_price_risk_universes
    from universe.core_watchlist import load_core_watchlist

    core_symbols, _ = load_core_watchlist(state.repo_root)
    symbol_meta = load_symbol_meta(state.repo_root, state.native_root)
    push_day_dir = state.push_root / state.trade_date.isoformat()
    build = build_price_risk_universes(
        reports_dir=state.reports_dir,
        day_stamp=state.options.day_stamp,
        core_symbols=core_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
        push_day_dir=push_day_dir,
    )
    pm_csv = Path(build["pm_output"])
    pm_rows = build.get("pm_rows") or []
    pm_val = validate_universe_csv(pm_csv, expected_session="pm")
    pm_runner = runner_load_check(
        pm_csv, state.native_root, state.repo_root, expected_session="pm"
    )
    return {
        "ok": bool(pm_val.get("passed")) and bool(pm_runner.get("passed")),
        "pm_csv": rel_path(state.repo_root, pm_csv),
        "pm_row_count": len(pm_rows),
        "push_day_dir": rel_path(state.repo_root, push_day_dir),
        "push_day_dir_exists": push_day_dir.is_dir(),
        "universe_validation": pm_val,
        "runner_check": pm_runner,
        "price_risk_filter_enabled": True,
        "dynamic_price_risk_excluded_count": len(build.get("pm_excluded") or []),
        "dynamic_price_risk_replacement_count": len(build.get("pm_replacements") or []),
        "dynamic_price_risk_excluded": build.get("pm_excluded"),
        "dynamic_price_risk_replacements": build.get("pm_replacements"),
    }


def build_pm_universe(state: DailyRunnerState) -> dict[str, Any]:
    from universe.daily_features import load_features_csv

    feat_path = features_csv(state.reports_dir, state.options.day_stamp)
    if not feat_path.is_file():
        return {
            "ok": False,
            "error": "features_missing",
            "features_path": rel_path(state.repo_root, feat_path),
            "universe_mode": state.options.universe_mode,
        }

    feature_rows = load_features_csv(feat_path)
    if is_price_risk_universe_mode(state.options.universe_mode):
        built = _build_pm_universe_price_risk(state, feature_rows)
    else:
        built = _build_pm_universe_standard(state, feature_rows)
    built["universe_mode"] = state.options.universe_mode
    _dual_write_runtime_artifacts(state)
    return built


def _intraday_refresh_preflight(state: DailyRunnerState, cfg_check: dict[str, Any]) -> Optional[str]:
    if not state.options.enable_intraday_refresh:
        return None
    if not is_price_risk_universe_mode(state.options.universe_mode):
        return "intraday_refresh_requires_price_risk_universe_mode"
    from small_paper.config import load_pilot_config

    cfg = load_pilot_config(state.repo_root / state.options.config_rel)
    from universe.intraday_refresh import check_intraday_refresh_policy

    pol = check_intraday_refresh_policy(
        refresh_enabled=True,
        max_concurrent_positions=int(cfg.max_concurrent_positions),
        register_count=PUSH_LIMIT,
        open_symbols_count=0,
        price_risk_mode=True,
        entry_guard_enabled=bool(getattr(cfg, "entry_price_risk_guard_enabled", False)),
        position_cap_mode=bool(getattr(cfg, "position_cap_mode", False)),
        same_symbol_open_policy=str(getattr(cfg, "same_symbol_open_policy", "") or ""),
        paper_only=bool(cfg.paper_only),
        order_enabled=bool(cfg.order_enabled),
    )
    if not pol.get("ok"):
        return "; ".join(pol.get("issues") or [])
    return None


def build_intraday_refresh_universes(
    state: DailyRunnerState,
    *,
    feature_rows: list[dict[str, str]],
    open_symbols_test: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Build AM/PM refresh CSVs (price-risk mode only)."""
    from universe.core_watchlist import load_core_watchlist
    from universe.intraday_refresh import (
        AM_REFRESH_TIME,
        PM_REFRESH_TIME,
        build_am_refresh_universe_price_risk,
        build_pm_refresh_universe_price_risk,
        merge_register_specs,
        validate_refresh_universe_csv,
        write_refresh_universe_csv,
    )

    day = state.options.day_stamp
    am_refresh_path = universe_am_refresh_csv(state.reports_dir, day)
    pm_refresh_path = universe_pm_refresh_csv(state.reports_dir, day)
    push_day = state.push_root / state.trade_date.isoformat()
    core_symbols, _ = load_core_watchlist(state.repo_root)
    symbol_meta = load_symbol_meta(state.repo_root, state.native_root)
    test_open = list(open_symbols_test or [])

    am_rows, am_meta = build_am_refresh_universe_price_risk(
        core_symbols=core_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
        push_day_dir=push_day,
        open_symbols=(),
    )
    write_refresh_universe_csv(am_refresh_path, am_rows)
    am_val = validate_refresh_universe_csv(am_refresh_path, expected_session="am")

    pm_rows, pm_meta = build_pm_refresh_universe_price_risk(
        core_symbols=core_symbols,
        feature_rows=feature_rows,
        symbol_meta=symbol_meta,
        push_day_dir=push_day,
        open_symbols=(),
    )
    write_refresh_universe_csv(pm_refresh_path, pm_rows)
    pm_val = validate_refresh_universe_csv(pm_refresh_path, expected_session="pm")

    merge_test: dict[str, Any] = {"skipped": True}
    if test_open:
        from universe.intraday_refresh import merge_universe_with_open_symbols

        merged, mmeta = merge_universe_with_open_symbols(
            am_rows,
            open_symbols=test_open,
            feature_rows=feature_rows,
            symbol_meta=symbol_meta,
            session="am",
            refresh_time=AM_REFRESH_TIME,
        )
        _, reg_meta = merge_register_specs(merged, symbol_meta=symbol_meta)
        merge_test = {
            "open_symbols": test_open,
            "merge": mmeta,
            "register": reg_meta,
            "ok": bool(mmeta.get("register_count_ok")) and not mmeta.get("error"),
        }

    am_specs, am_reg = merge_register_specs(am_rows, symbol_meta=symbol_meta)
    pm_specs, pm_reg = merge_register_specs(pm_rows, symbol_meta=symbol_meta)

    ok = bool(am_val.get("ok")) and bool(pm_val.get("ok")) and merge_test.get("ok", True)
    out = {
        "ok": ok,
        "am_refresh_csv": rel_path(state.repo_root, am_refresh_path),
        "pm_refresh_csv": rel_path(state.repo_root, pm_refresh_path),
        "am_refresh_time": AM_REFRESH_TIME,
        "pm_refresh_time": PM_REFRESH_TIME,
        "am_refresh_register_count": am_reg.get("register_count"),
        "pm_refresh_register_count": pm_reg.get("register_count"),
        "am_refresh_open_symbols_count": 0,
        "pm_refresh_open_symbols_count": 0,
        "refresh_register_symbol_count_ok": am_reg.get("register_count_ok")
        and pm_reg.get("register_count_ok"),
        "refresh_universe_duplicate_count": int(am_val.get("duplicate_count") or 0)
        + int(pm_val.get("duplicate_count") or 0),
        "am_validation": am_val,
        "pm_validation": pm_val,
        "am_meta": am_meta,
        "pm_meta": pm_meta,
        "focus_5856_excluded_am": am_meta.get("focus_5856_excluded"),
        "focus_5856_excluded_pm": pm_meta.get("focus_5856_excluded"),
        "open_symbol_merge_test": merge_test,
        "am_register_specs_count": len(am_specs),
        "pm_register_specs_count": len(pm_specs),
    }
    _dual_write_runtime_artifacts(state)
    return out


def wait_until_hhmm(
    hhmm: str,
    *,
    dry_run_only: bool,
    label: str,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    target_t = dt_time(int(hhmm[:2]), int(hhmm[3:5]))
    if dry_run_only:
        return {"skipped": True, "reason": "dry_run_only", "target": hhmm, "label": label}
    while True:
        n = now_jst()
        if n.time() >= target_t:
            return {
                "skipped": False,
                "reached_at": n.isoformat(timespec="seconds"),
                "target": hhmm,
                "label": label,
            }
        # sleep up to 30s chunks
        sleep_fn(min(30.0, 5.0))


def kabu_clear_stale_registrations(state: DailyRunnerState, *, label: str) -> dict[str, Any]:
    """PUT /unregister/all between AM/PM or at preflight (Phase155)."""
    if state.options.skip_kabu or state.options.dry_run_only:
        return {"skipped": True, "reason": "skip_kabu_or_dry_run_only", "label": label}
    from api.kabu_register import clear_register_before_session

    out = clear_register_before_session(state.repo_root)
    out["label"] = label
    return out


def run_safety_check(state: DailyRunnerState) -> dict[str, Any]:
    cfg_path = state.repo_root / state.options.config_rel
    cmd = [
        sys.executable,
        str(state.repo_root / SAFETY_SCRIPT),
        "--config",
        str(cfg_path),
        "--live",
        "--report-date",
        state.options.day_stamp,
    ]
    proc = subprocess.run(cmd, cwd=str(state.repo_root), capture_output=True, text=True)
    report_path = state.reports_dir / f"small_paper_safety_{state.options.day_stamp}.json"
    report: dict[str, Any] = {}
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    _dual_write_runtime_artifacts(state)
    return {
        "exit_code": proc.returncode,
        "report_path": rel_path(state.repo_root, report_path),
        "overall_pass": report.get("overall_pass"),
        "ready_for_live_session": report.get("ready_for_live_session"),
        "failed_check_ids": report.get("failed_check_ids", []),
        "warnings": report.get("warnings", []),
        "policy_label": report.get("policy_label"),
        "report": report,
    }


def verify_config_safety(state: DailyRunnerState) -> dict[str, Any]:
    from small_paper.config import load_pilot_config

    cfg_path = state.repo_root / state.options.config_rel
    cfg = load_pilot_config(cfg_path)
    config_rel_norm = state.options.config_rel.replace("\\", "/")
    price_risk_mode = is_price_risk_universe_mode(state.options.universe_mode)
    exit_shadow = str(state.options.exit_policy_shadow or "").strip()
    issues: list[str] = []
    cautions: list[str] = []

    if cfg.order_enabled:
        issues.append("order_enabled=true")
    if not cfg.paper_only:
        issues.append("paper_only=false")

    if state.options.enable_intraday_refresh:
        refresh_issue = _intraday_refresh_preflight(state, {})
        if refresh_issue:
            issues.append(refresh_issue)

    if price_risk_mode:
        trailing_cfg = (
            TRAILING_MFE_LOW_LIQ_SHADOW_YAML
            if (exit_shadow == "trailing-mfe" and state.options.low_liquidity_shadow)
            else TRAILING_MFE_SHADOW_YAML
        )
        expected_cfg = (
            FADE_HYBRID_SHADOW_YAML
            if exit_shadow == "fade-hybrid"
            else (
                trailing_cfg
                if exit_shadow == "trailing-mfe"
                else (
                    FADE_BREAKDOWN_SHADOW_YAML
                    if exit_shadow == "fade-breakdown"
                    else ENTRY_GUARD_SHADOW_YAML
                )
            )
        )
        if config_rel_norm != expected_cfg:
            msg = (
                f"price_risk_universe_mode requires {expected_cfg}, "
                f"got {config_rel_norm}"
            )
            if state.options.dry_run_only:
                cautions.append(msg)
            else:
                issues.append(msg)
        if not getattr(cfg, "entry_price_risk_guard_enabled", False):
            if state.options.dry_run_only:
                cautions.append("entry_price_risk_guard_enabled=false")
            else:
                issues.append("entry_price_risk_guard_not_enabled")
        else:
            trailing_label = (
                TRAILING_MFE_LOW_LIQ_POLICY_LABEL
                if (exit_shadow == "trailing-mfe" and state.options.low_liquidity_shadow)
                else TRAILING_MFE_POLICY_LABEL
            )
            expected_label = (
                FADE_HYBRID_POLICY_LABEL
                if exit_shadow == "fade-hybrid"
                else (
                    trailing_label
                    if exit_shadow == "trailing-mfe"
                    else (
                        FADE_BREAKDOWN_POLICY_LABEL
                        if exit_shadow == "fade-breakdown"
                        else ENTRY_GUARD_POLICY_LABEL
                    )
                )
            )
            if cfg.policy_label != expected_label:
                cautions.append(f"expected policy_label={expected_label!r}")
    elif cfg.policy_label != EXPECTED_POLICY_LABEL:
        issues.append(f"unexpected policy_label={cfg.policy_label!r}")

    if exit_shadow == "fade-hybrid":
        if cfg.max_concurrent_positions != 3:
            issues.append("fade_hybrid_shadow_requires_cap3")
        if not str(cfg.policy_label or "").endswith("_trial"):
            issues.append("fade_hybrid_shadow_requires_policy_label_trial")
        if cfg.order_enabled or not cfg.paper_only:
            issues.append("fade_hybrid_shadow_requires_paper_only_no_orders")

    if exit_shadow == "fade-breakdown":
        if cfg.max_concurrent_positions != 3:
            issues.append("fade_breakdown_shadow_requires_cap3")
        if not str(cfg.policy_label or "").endswith("_trial"):
            issues.append("fade_breakdown_shadow_requires_policy_label_trial")
        if cfg.order_enabled or not cfg.paper_only:
            issues.append("fade_breakdown_shadow_requires_paper_only_no_orders")

    if price_risk_mode and (cfg.order_enabled or not cfg.paper_only):
        issues.append("price_risk_shadow_with_live_order_settings")

    return {
        "ok": len(issues) == 0,
        "order_enabled": cfg.order_enabled,
        "paper_only": cfg.paper_only,
        "policy_label": cfg.policy_label,
        "discord_enabled": cfg.discord_enabled,
        "entry_price_risk_guard_enabled": bool(
            getattr(cfg, "entry_price_risk_guard_enabled", False)
        ),
        "universe_mode": state.options.universe_mode,
        "price_risk_filter_enabled": price_risk_mode,
        "issues": issues,
        "cautions": cautions,
    }


def core10_status(state: DailyRunnerState) -> dict[str, Any]:
    from universe.core10_dynamic40_shadow import diagnose_core_watchlist
    from universe.core_watchlist import load_core_watchlist

    core_symbols, _ = load_core_watchlist(state.repo_root)
    diag = diagnose_core_watchlist(state.repo_root, core_symbols, trade_date=state.trade_date)
    stale = bool(diag.get("morning_check_caution") or not diag.get("core_is_today", True))
    return {
        "core_count": diag.get("core_count"),
        "core_is_today": diag.get("core_is_today"),
        "core_last_updated_date": diag.get("core_last_updated_date"),
        "core_stale_warning": diag.get("core_stale_warning", ""),
        "stale_caution": stale,
        "readable_exists": diag.get("readable_exists"),
        "core_symbols": diag.get("core_symbols"),
        "invalid_core_symbols": diag.get("invalid_core_symbols"),
    }


def preflight(state: DailyRunnerState) -> bool:
    """Return True if AM may proceed."""
    pf: dict[str, Any] = {"steps": []}
    cfg_check = verify_config_safety(state)
    pf["config_safety"] = cfg_check
    if cfg_check.get("cautions"):
        pf.setdefault("cautions", []).extend(cfg_check["cautions"])
    if not cfg_check["ok"]:
        state.preflight = pf
        state.verdict = "safety_blocked" if is_price_risk_universe_mode(
            state.options.universe_mode
        ) else "preflight_blocked"
        state.verdict_notes = ["config safety hard stop: " + ", ".join(cfg_check["issues"])]
        state.stopped_reason = "config_safety"
        return False

    core = core10_status(state)
    pf["core10"] = core
    if not core.get("readable_exists"):
        state.preflight = pf
        state.verdict = "preflight_blocked"
        state.verdict_notes = ["Core10 watchlist not readable"]
        state.stopped_reason = "core_watchlist_missing"
        return False
    if core.get("stale_caution"):
        pf["cautions"] = [core.get("core_stale_warning") or "Core10 watchlist stale (not today)"]

    if not state.options.skip_safety:
        safety = run_safety_check(state)
        pf["safety"] = safety
        failed = list(safety.get("failed_check_ids") or [])
        if state.options.skip_kabu:
            failed = [x for x in failed if x not in ("kabu_station_connection", "kabu_api_password")]
        if not safety.get("overall_pass") and failed:
            state.preflight = pf
            state.verdict = "preflight_blocked"
            state.verdict_notes = [f"safety failed: {failed}"]
            state.stopped_reason = "safety_check"
            return False
    else:
        pf["safety"] = {"skipped": True}

    if not state.options.skip_kabu and not state.options.skip_safety:
        report = (pf.get("safety") or {}).get("report") or {}
        kabu_checks = [
            c
            for c in report.get("checks", [])
            if c.get("check_id") in ("kabu_station_connection", "kabu_api_password")
        ]
        kabu_failed = [c for c in kabu_checks if not c.get("passed")]
        if kabu_failed:
            state.preflight = pf
            state.verdict = "preflight_blocked"
            state.verdict_notes = ["kabu connection check failed"]
            state.stopped_reason = "kabu_connection"
            return False

    discord_ok = True
    if not state.options.skip_safety:
        report = (pf.get("safety") or {}).get("report") or {}
        discord_checks = [
            c
            for c in report.get("checks", [])
            if str(c.get("check_id", "")).startswith("discord")
        ]
        discord_failed = [c for c in discord_checks if not c.get("passed")]
        if discord_failed:
            discord_ok = False
            pf["discord"] = {"ok": False, "failed": discord_failed}
    if not discord_ok and not state.options.dry_run_only:
        state.preflight = pf
        state.verdict = "preflight_blocked"
        state.verdict_notes = ["discord webhook check failed"]
        state.stopped_reason = "discord_webhook"
        return False

    if not state.options.skip_kabu and not state.options.dry_run_only:
        pf["kabu_register_clear"] = kabu_clear_stale_registrations(
            state, label="preflight_before_am"
        )
        if not pf["kabu_register_clear"].get("ok") and not pf["kabu_register_clear"].get("skipped"):
            state.preflight = pf
            state.verdict = "preflight_blocked"
            state.verdict_notes = [
                "kabu unregister/all pre-clear failed: "
                + str(pf["kabu_register_clear"].get("error"))
            ]
            state.stopped_reason = "kabu_register_clear"
            return False

    pf["ready"] = True
    state.preflight = pf
    return True


def pilot_command_argv(
    state: DailyRunnerState,
    *,
    session: AM_PM_KIND,
    universe_rel: str,
) -> list[str]:
    cfg_path = state.repo_root / state.options.config_rel
    universe_path = state.repo_root / universe_rel.replace("/", "\\") if "/" in universe_rel else state.repo_root / universe_rel
    argv = [
        sys.executable,
        str(state.repo_root / PILOT_SCRIPT),
        "--dry-run",
        "--source",
        "live",
        "--universe-csv",
        str(universe_path),
        "--config",
        str(cfg_path),
        "--am-pm-session",
        session,
        "--wait-until-session",
        "--poll-interval-sec",
        str(state.options.poll_interval_sec),
        "--output-date",
        state.options.day_stamp,
    ]
    if state.options.enable_intraday_refresh and is_price_risk_universe_mode(
        state.options.universe_mode
    ):
        day = state.options.day_stamp
        refresh_rel = universe_am_refresh_rel(day) if session == "am" else universe_pm_refresh_rel(day)
        refresh_path = state.repo_root / refresh_rel.replace("/", "\\")
        argv.extend(
            [
                "--enable-intraday-refresh",
                "--intraday-refresh-csv",
                str(refresh_path),
            ]
        )
    if state.options.pre625_runtime_structure_mode:
        argv.append("--pre625-runtime-structure-mode")
    if str(state.options.core_runtime_mode or "").strip():
        argv.extend(["--core-runtime-mode", str(state.options.core_runtime_mode).strip()])
    return argv


def build_phase154_shadow_commands(state: DailyRunnerState) -> dict[str, Any]:
    day = state.options.day_stamp
    am_rel = universe_am_rel(day, universe_mode=state.options.universe_mode)
    pm_rel = universe_pm_rel(day, universe_mode=state.options.universe_mode)
    am_argv = pilot_command_argv(state, session="am", universe_rel=am_rel)
    pm_argv = pilot_command_argv(state, session="pm", universe_rel=pm_rel)
    daily_argv = [
        "python",
        "kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py",
        f"--day-stamp {day}",
        f"--universe-mode {UNIVERSE_MODE_PRICE_RISK}",
        f"--config {state.options.config_rel}",
    ]
    return {
        "phase": "154",
        "day_stamp": day,
        "universe_mode": UNIVERSE_MODE_PRICE_RISK,
        "config_rel": state.options.config_rel,
        "price_risk_universe_am_csv": am_rel,
        "price_risk_universe_pm_csv": pm_rel,
        "am_runner_command": " ".join(am_argv),
        "pm_runner_command": " ".join(pm_argv),
        "daily_runner_command": " ".join(daily_argv),
        "dual_defense": {
            "universe_filter": "dynamic close>=50 and tick_ratio<=5%",
            "entry_gate": "entry_price_risk_guard_shadow YAML",
            "core10": "warn_only_not_auto_excluded",
        },
        "dry_run_validation_command": (
            "python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py "
            f"--skip-kabu --skip-safety --dry-run-only --day-stamp {day} "
            f"--universe-mode {UNIVERSE_MODE_PRICE_RISK} "
            f"--config {ENTRY_GUARD_SHADOW_YAML}"
        ),
    }


def build_commands_json(state: DailyRunnerState) -> dict[str, Any]:
    day = state.options.day_stamp
    mode = state.options.universe_mode
    am_rel = universe_am_rel(day, universe_mode=mode)
    pm_rel = universe_pm_rel(day, universe_mode=mode)

    if is_price_risk_universe_mode(mode):
        from universe.core10_dynamic40_price_risk_shadow import shadow_live_commands

        base = shadow_live_commands(am_csv_rel=am_rel, pm_csv_rel=pm_rel)
        base["universe_mode"] = UNIVERSE_MODE_PRICE_RISK
    else:
        from universe.core10_dynamic40_shadow import shadow_live_commands

        base = shadow_live_commands(am_csv_rel=am_rel, pm_csv_rel=pm_rel)

    runner_flags = f"--day-stamp {day} --universe-mode {mode}"
    if mode == UNIVERSE_MODE_LEGACY:
        runner_flags += f" --config {state.options.config_rel}"
    if state.options.enable_intraday_refresh:
        runner_flags += " --enable-intraday-refresh"

    base["daily_runner"] = {
        "universe_mode": mode,
        "config_rel": state.options.config_rel,
        "am_argv": pilot_command_argv(state, session="am", universe_rel=am_rel),
        "pm_argv": pilot_command_argv(state, session="pm", universe_rel=pm_rel),
        "safety_argv": [
            sys.executable,
            str(state.repo_root / SAFETY_SCRIPT),
            "--config",
            str(state.repo_root / state.options.config_rel),
            "--live",
            "--report-date",
            day,
        ],
        "phase148_script": (
            "python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py "
            + runner_flags
        ),
    }
    base["review_commands"] = [
        f"python kabu_native/scripts/check_small_paper_safety.py --live --report-date {day}",
        (
            "python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py "
            + runner_flags
        ),
        (
            "python kabu_native/scripts/run_phase118_core10_dynamic40_pipeline.py "
            f"--day-stamp {day}"
        ),
    ]
    if is_price_risk_universe_mode(mode):
        base["phase154_shadow_commands"] = build_phase154_shadow_commands(state)
    return base


def _apply_core_price_risk_cautions(state: DailyRunnerState) -> None:
    warnings = list(state.am_prep.get("core_price_risk_warnings") or [])
    if not warnings:
        return
    pf = state.preflight
    pf.setdefault("cautions", [])
    for w in warnings:
        sym = w.get("symbol", "")
        reason = w.get("price_risk_reason", "")
        msg = (
            f"core_price_risk_warning: {sym} — {reason} "
            "(Core10 not auto-excluded; entry gate is final defense)"
        )
        if msg not in pf["cautions"]:
            pf["cautions"].append(msg)
    state.verdict_notes.append(f"core_price_risk_warnings={len(warnings)}")


def _apply_price_risk_focus_cautions(state: DailyRunnerState) -> None:
    """Phase167: record focus-symbol drift without blocking universe generation."""
    if not is_price_risk_universe_mode(state.options.universe_mode):
        return
    checks = state.am_prep.get("price_risk_symbol_checks") or {}
    cautions: list[str] = list(state.am_prep.get("price_risk_focus_cautions") or [])
    pf = state.preflight
    pf.setdefault("cautions", [])

    if checks.get("4392_retained") is False:
        msg = (
            f"focus_symbol_caution: {FOCUS_SYMBOL_4392} not in AM universe "
            "(vol_liq rank may be outside Dynamic40; not a hard stop)"
        )
        if msg not in cautions:
            cautions.append(msg)
        if msg not in pf["cautions"]:
            pf["cautions"].append(msg)
        state.verdict_notes.append(f"focus_symbol_caution:{FOCUS_SYMBOL_4392}_not_retained")

    state.am_prep["price_risk_focus_cautions"] = cautions


def diff_new_session_dirs(
    before: list[Path] | set[Path],
    after: list[Path] | set[Path],
) -> list[Path]:
    """Return new session dirs after a pilot run (before/after may be list or set)."""
    before_set = before if isinstance(before, set) else set(before)
    after_set = after if isinstance(after, set) else set(after)
    return sorted(after_set - before_set, key=lambda p: p.name)


def _pilot_finalize_snapshot(
    state: DailyRunnerState,
    *,
    session: AM_PM_KIND,
    session_dir: Optional[Path],
    exit_code: Optional[int],
    stopped_reason: Optional[str],
) -> dict[str, Any]:
    summary_path = (session_dir / "small_paper_summary.json") if session_dir else None
    return {
        "session": session,
        "session_dir": rel_path(state.repo_root, session_dir) if session_dir else None,
        "exit_code": exit_code,
        "stopped_reason": stopped_reason,
        "summary_path_exists": summary_path.is_file() if summary_path else False,
        "recorded_at": now_jst().isoformat(timespec="seconds"),
    }


def _write_pilot_checkpoint(state: DailyRunnerState, *, session: AM_PM_KIND) -> None:
    """Persist partial phase148 state after each pilot subprocess (crash-safe)."""
    key = f"{session}_live"
    live = state.am_live if session == "am" else state.pm_live
    if not live:
        return
    state.sessions[f"{session}_finalize"] = live.get("finalize_snapshot") or {}
    try:
        write_outputs(state)
    except Exception:
        pass


def run_pilot_session(state: DailyRunnerState, *, session: AM_PM_KIND) -> dict[str, Any]:
    if session == "am":
        universe_rel = state.am_prep.get("am_csv", "")
    else:
        universe_rel = state.pm_prep.get("pm_csv", "")
    cmd = pilot_command_argv(state, session=session, universe_rel=universe_rel)
    before_dirs = set(_list_session_dirs(state))
    exit_code: Optional[int] = None
    proc_error: Optional[str] = None
    proc_exc_type: Optional[str] = None

    try:
        proc = subprocess.run(cmd, cwd=str(state.repo_root))
        exit_code = proc.returncode
    except Exception as exc:
        proc_exc_type = type(exc).__name__
        proc_error = str(exc)

    result: dict[str, Any] = {
        "session": session,
        "exit_code": exit_code,
        "command_argv": cmd,
        "command": " ".join(cmd),
        "new_session_dirs": [],
        "session_dir": None,
        "summary_found": False,
        "pilot_ok": False,
        "session_detection_ok": False,
        "warning": None,
        "ok": False,
    }
    if proc_error:
        result["error"] = proc_exc_type
        result["error_message"] = proc_error

    discovered: Optional[Path] = None
    post_error: Optional[str] = None
    try:
        after_dirs = set(_list_session_dirs(state))
        new_dirs = sorted(set(after_dirs) - set(before_dirs), key=lambda p: p.name)
        discovered = discover_session_dir(
            state, kind=session, candidates=new_dirs or list(after_dirs)
        )
        result["new_session_dirs"] = [rel_path(state.repo_root, p) for p in new_dirs]
        result["session_dir"] = rel_path(state.repo_root, discovered) if discovered else None
        summary_ok = bool(
            discovered and (discovered / "small_paper_summary.json").is_file()
        )
        result["summary_found"] = summary_ok
        result["session_detection_ok"] = summary_ok
        pilot_ok = exit_code == 0 and not proc_error
        result["pilot_ok"] = pilot_ok
        result["ok"] = pilot_ok
        if pilot_ok and not summary_ok:
            result["warning"] = (
                "session_dir_not_detected: pilot exited 0 but no matching "
                "small_paper_summary.json (check live_session_* / live_full_session_*)"
            )
    except Exception as exc:
        post_error = f"{type(exc).__name__}: {exc}"
        result["post_pilot_error"] = post_error
        if discovered is None:
            after_dirs_fallback = set(_list_session_dirs(state))
            if after_dirs_fallback:
                discovered = discover_session_dir(
                    state, kind=session, candidates=list(after_dirs_fallback)
                )
                if discovered:
                    result["session_dir"] = rel_path(state.repo_root, discovered)

    stopped = proc_exc_type or ("pilot_exit_nonzero" if exit_code not in (None, 0) else None)
    if post_error:
        stopped = stopped or "post_pilot_finalize_error"
    result["finalize_snapshot"] = _pilot_finalize_snapshot(
        state,
        session=session,
        session_dir=discovered,
        exit_code=exit_code,
        stopped_reason=stopped,
    )
    result["summary_path_exists"] = result["finalize_snapshot"]["summary_path_exists"]
    return result


def _list_session_dirs(state: DailyRunnerState) -> list[Path]:
    base = state.repo_root / SMALL_PAPER_REL / state.options.day_stamp
    if not base.is_dir():
        return []
    return [
        p
        for p in base.iterdir()
        if p.is_dir() and p.name.startswith(SESSION_DIR_PREFIXES)
    ]


def discover_session_dir(
    state: DailyRunnerState,
    *,
    kind: AM_PM_KIND,
    candidates: Optional[list[Path]] = None,
) -> Optional[Path]:
    pool = candidates if candidates is not None else _list_session_dirs(state)
    matched: list[Path] = []
    for p in pool:
        summary_path = p / "small_paper_summary.json"
        if not summary_path.is_file():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        am_pm = summary.get("am_pm_session") or {}
        if isinstance(am_pm, dict) and am_pm.get("kind") == kind:
            matched.append(p)
            continue
        # fallback: infer from session_end time in live_session_config.json
        cfg_path = p / "live_session_config.json"
        if cfg_path.is_file():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                end = str(cfg.get("session_end") or "")
                if kind == "am" and end in ("11:25", "11:30"):
                    matched.append(p)
                elif kind == "pm" and end in ("15:23", "15:30"):
                    matched.append(p)
            except json.JSONDecodeError:
                pass
    if not matched:
        return None
    return max(matched, key=lambda x: x.name)


def build_summary_payload(state: DailyRunnerState) -> dict[str, Any]:
    from small_paper.config import config_file_sha256, load_pilot_config

    cfg = (state.preflight or {}).get("config_safety") or {}
    price_risk = is_price_risk_universe_mode(state.options.universe_mode)
    cfg_path = state.repo_root / state.options.config_rel
    pilot_cfg = load_pilot_config(cfg_path) if cfg_path.is_file() else None
    payload: dict[str, Any] = {
        "phase": 148,
        "day_stamp": state.options.day_stamp,
        "generated_at": state.generated_at,
        "verdict": state.verdict,
        "verdict_notes": state.verdict_notes,
        "stopped_reason": state.stopped_reason,
        "dry_run_only": state.options.dry_run_only,
        "skip_am": state.options.skip_am,
        "skip_pm": state.options.skip_pm,
        "preflight_ok": state.preflight.get("ready"),
        "core_stale_caution": (state.preflight.get("core10") or {}).get("stale_caution"),
        "am_session_dir": state.sessions.get("am_dir"),
        "pm_session_dir": state.sessions.get("pm_dir"),
        "am_universe_csv": state.am_prep.get("am_csv"),
        "pm_universe_csv": state.pm_prep.get("pm_csv"),
        "am_live_ok": state.am_live.get("ok"),
        "pm_live_ok": state.pm_live.get("ok"),
        "am_session_warning": state.sessions.get("am_warning"),
        "pm_session_warning": state.sessions.get("pm_warning"),
        "universe_mode": state.options.universe_mode,
        "config_rel": state.options.config_rel,
        "low_liquidity_shadow": state.options.low_liquidity_shadow,
        "config_path": str(cfg_path) if cfg_path.is_file() else "",
        "config_sha256": config_file_sha256(cfg_path) if cfg_path.is_file() else "",
        "exit_policy_shadow": state.options.exit_policy_shadow or "",
        "intraday_refresh_enabled": bool(state.options.enable_intraday_refresh),
        "pre625_runtime_structure_mode": bool(state.options.pre625_runtime_structure_mode),
        "core_runtime_mode": str(state.options.core_runtime_mode or ""),
        "structural_exit_policy": (
            getattr(pilot_cfg, "structural_exit_policy", "") if pilot_cfg else ""
        ),
        "order_enabled": False,
        "paper_only": True,
        "shadow_only": bool(getattr(pilot_cfg, "shadow_only", False)) if pilot_cfg else True,
        "price_risk_filter_enabled": price_risk or bool(state.am_prep.get("price_risk_filter_enabled")),
        "entry_price_risk_guard_enabled": bool(cfg.get("entry_price_risk_guard_enabled")),
        "core_price_risk_warnings": state.am_prep.get("core_price_risk_warnings") or [],
        "dynamic_price_risk_excluded_count": state.am_prep.get("dynamic_price_risk_excluded_count", 0),
        "dynamic_price_risk_replacement_count": state.am_prep.get(
            "dynamic_price_risk_replacement_count", 0
        ),
        "price_risk_universe_csv": state.am_prep.get("am_csv") if price_risk else None,
        "preflight_cautions": (state.preflight or {}).get("cautions") or [],
        "kabu_register_clear_preflight": (state.preflight or {}).get("kabu_register_clear"),
        "kabu_register_clear_after_am": state.sessions.get("kabu_register_clear_after_am"),
        "kabu_register_clear_before_pm": (state.pm_wait or {}).get(
            "kabu_register_clear_before_pm"
        ),
    }
    if price_risk:
        payload["price_risk_symbol_checks"] = state.am_prep.get("price_risk_symbol_checks")
        payload["price_risk_focus_cautions"] = state.am_prep.get("price_risk_focus_cautions") or []
        payload["phase"] = "148+154+167"
    if state.options.enable_intraday_refresh:
        ref = state.am_prep.get("intraday_refresh") or {}
        payload["phase"] = "148+154+167+157" if price_risk else "148+157"
        payload["intraday_refresh_enabled"] = True
        payload["am_refresh_time"] = ref.get("am_refresh_time")
        payload["pm_refresh_time"] = ref.get("pm_refresh_time")
        payload["am_refresh_universe_csv"] = ref.get("am_refresh_csv")
        payload["pm_refresh_universe_csv"] = ref.get("pm_refresh_csv")
        payload["am_refresh_register_count"] = ref.get("am_refresh_register_count")
        payload["pm_refresh_register_count"] = ref.get("pm_refresh_register_count")
        payload["am_refresh_open_symbols_count"] = ref.get("am_refresh_open_symbols_count")
        payload["pm_refresh_open_symbols_count"] = ref.get("pm_refresh_open_symbols_count")
        payload["refresh_register_symbol_count_ok"] = ref.get("refresh_register_symbol_count_ok")
        payload["refresh_universe_duplicate_count"] = ref.get("refresh_universe_duplicate_count")
    return payload


def write_outputs(state: DailyRunnerState) -> dict[str, str]:
    state.reports_dir.mkdir(parents=True, exist_ok=True)
    day = state.options.day_stamp
    full = {
        "phase": 148,
        "generated_at": state.generated_at,
        "options": {
            "day_stamp": state.options.day_stamp,
            "skip_kabu": state.options.skip_kabu,
            "skip_safety": state.options.skip_safety,
            "skip_am": state.options.skip_am,
            "skip_pm": state.options.skip_pm,
            "dry_run_only": state.options.dry_run_only,
            "poll_interval_sec": state.options.poll_interval_sec,
            "config_rel": state.options.config_rel,
            "universe_mode": state.options.universe_mode,
            "enable_intraday_refresh": state.options.enable_intraday_refresh,
            "low_liquidity_shadow": state.options.low_liquidity_shadow,
        },
        "verdict": state.verdict,
        "verdict_options": {
            "A": "am_pm_daily_runner_ready",
            "B": "preflight_blocked",
            "C": "am_failed",
            "D": "pm_failed",
            "E": "universe_generation_failed",
            "F": "intraday_refresh_shadow_ready",
            "G": "refresh_universe_generation_failed",
        },
        "verdict_notes": state.verdict_notes,
        "stopped_reason": state.stopped_reason,
        "preflight": state.preflight,
        "am_prep": state.am_prep,
        "am_live": state.am_live,
        "pm_wait": state.pm_wait,
        "pm_prep": state.pm_prep,
        "pm_live": state.pm_live,
        "sessions": state.sessions,
        "commands": state.commands,
        "constraints": [
            "no_production_pilot_yaml_change",
            "order_enabled=false",
            "paper_only=true",
            "shadow_dry_run_only",
            "no_auto_order",
            "separate_am_pm_sessions_no_full_session",
        ],
        "bugfix": {
            "phase": "148b_runner_crash_recovery",
            "session_dir_prefixes": list(SESSION_DIR_PREFIXES),
            "pm_continues_on_session_detection_warning": True,
            "pilot_finalize_checkpoint": True,
            "set_diff_for_session_dirs": True,
        },
    }
    paths = {
        "phase148": state.reports_dir / f"phase148_am_pm_daily_runner_{day}.json",
        "summary": state.reports_dir / f"daily_runner_summary_{day}.json",
        "commands": state.reports_dir / f"daily_runner_commands_{day}.json",
    }
    paths["phase148"].write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["summary"].write_text(
        json.dumps(build_summary_payload(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["commands"].write_text(
        json.dumps(state.commands, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if is_price_risk_universe_mode(state.options.universe_mode):
        p154_cmd = state.reports_dir / f"phase154_daily_runner_price_risk_shadow_commands_{day}.json"
        p154_cmd.write_text(
            json.dumps(build_phase154_shadow_commands(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths["phase154_commands"] = p154_cmd
    if state.options.enable_intraday_refresh:
        ref = state.am_prep.get("intraday_refresh") or {}
        p157 = state.reports_dir / "phase157_intraday_refresh_runner_review.json"
        p157.write_text(
            json.dumps(
                {
                    "phase": 157,
                    "generated_at": state.generated_at,
                    "verdict": state.verdict,
                    "day_stamp": day,
                    "intraday_refresh_enabled": True,
                    "universe_mode": state.options.universe_mode,
                    "config_rel": state.options.config_rel,
                    "intraday_refresh": ref,
                    "commands": state.commands,
                    "summary_fields": build_summary_payload(state),
                    "verdict_options": {
                        "A": "intraday_refresh_shadow_ready",
                        "B": "open_symbol_register_merge_failed",
                        "C": "register_count_over_50",
                        "D": "refresh_universe_generation_failed",
                        "E": "safety_blocked",
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        paths["phase157_review"] = p157
    _dual_write_runtime_artifacts(state)
    try:
        from storage.daily_artifact_organizer import organize_daily_artifacts

        organize_daily_artifacts(state.repo_root, day)
    except Exception as exc:
        log.warning("daily_artifact_organizer failed day=%s: %s", day, exc)
    return {k: rel_path(state.repo_root, v) for k, v in paths.items()}


def _pilot_failed_hard(live: dict[str, Any]) -> bool:
    if live.get("error"):
        return True
    return live.get("exit_code", 1) != 0


def _apply_session_detection_warning(
    state: DailyRunnerState,
    *,
    session: AM_PM_KIND,
    live: dict[str, Any],
) -> None:
    warning = live.get("warning")
    if not warning and live.get("pilot_ok") and not live.get("session_detection_ok"):
        warning = "session_dir_not_detected"
    if warning:
        key = f"{session}_warning"
        state.sessions[key] = warning
        state.verdict_notes.append(f"{session.upper()} warning: {warning}")


def run_daily_runner(state: DailyRunnerState) -> int:
    try:
        return _run_daily_runner_body(state)
    except Exception as exc:
        state.stopped_reason = state.stopped_reason or "runner_exception"
        state.verdict_notes.append(f"Unhandled runner exception: {type(exc).__name__}: {exc}")
        if state.verdict == "started":
            state.verdict = "am_failed"
        write_outputs(state)
        return 2


def _run_daily_runner_body(state: DailyRunnerState) -> int:
    state.generated_at = now_jst().isoformat(timespec="seconds")

    if not preflight(state):
        state.commands = build_commands_json(state)
        write_outputs(state)
        return 2

    if not state.options.skip_am:
        state.am_prep = build_am_universe(state)
        _apply_core_price_risk_cautions(state)
        _apply_price_risk_focus_cautions(state)
        if not state.am_prep.get("ok"):
            state.verdict = "universe_generation_failed"
            state.verdict_notes.append("AM universe generation or validation failed")
            state.stopped_reason = "am_universe"
            write_outputs(state)
            return 2

        if state.options.enable_intraday_refresh:
            from universe.daily_features import load_features_csv

            feat_path = features_csv(state.reports_dir, state.options.day_stamp)
            feature_rows = (
                load_features_csv(feat_path) if feat_path.is_file() else []
            )
            open_test: list[str] = []
            for row in state.am_prep.get("am_rows") or []:
                if str(row.get("universe_slot") or "") == "dynamic":
                    sym = str(row.get("symbol") or "")
                    if sym and sym != FOCUS_SYMBOL_5856:
                        open_test = [sym]
                        break
            state.am_prep["intraday_refresh"] = build_intraday_refresh_universes(
                state,
                feature_rows=feature_rows,
                open_symbols_test=open_test,
            )
            if not state.am_prep["intraday_refresh"].get("ok"):
                state.verdict = "refresh_universe_generation_failed"
                state.verdict_notes.append("intraday refresh CSV validation failed")
                state.stopped_reason = "refresh_universe"
                write_outputs(state)
                return 2

        am_csv_path = state.repo_root / str(state.am_prep.get("am_csv") or "")
        state.am_prep["screening_notify"] = notify_screening_universe_discord(
            state,
            session_label="AM Screening",
            universe_csv=am_csv_path,
        )

        if not state.options.dry_run_only:
            try:
                state.am_live = run_pilot_session(state, session="am")
            finally:
                state.sessions["am_dir"] = (state.am_live or {}).get("session_dir")
                state.sessions["am_finalize"] = (state.am_live or {}).get("finalize_snapshot")
                _write_pilot_checkpoint(state, session="am")
                state.sessions["kabu_register_clear_after_am"] = kabu_clear_stale_registrations(
                    state, label="after_am_session"
                )
            if _pilot_failed_hard(state.am_live or {}):
                state.verdict = "am_failed"
                state.verdict_notes.append(
                    f"AM pilot exit={state.am_live.get('exit_code')} "
                    f"error={state.am_live.get('error')}"
                )
                state.stopped_reason = "am_pilot"
                write_outputs(state)
                return 2
            _apply_session_detection_warning(state, session="am", live=state.am_live)
        else:
            state.am_live = {"skipped": True, "reason": "dry_run_only"}

    if state.options.skip_pm:
        state.verdict = "am_pm_daily_runner_ready" if state.am_prep.get("ok") else state.verdict
        if state.options.skip_am and state.options.skip_pm:
            state.verdict = "am_pm_daily_runner_ready"
        state.verdict_notes.append("PM skipped by flag")
        state.commands = build_commands_json(state)
        write_outputs(state)
        return 0 if state.verdict == "am_pm_daily_runner_ready" else 1

    # Wait until PM screening window, then rebuild PM universe
    if not state.options.skip_am and not state.options.dry_run_only:
        state.pm_wait["after_am"] = wait_until_hhmm(
            PM_SCREEN_HHMM, dry_run_only=False, label="pm_screening_start"
        )
    else:
        state.pm_wait["after_am"] = {"skipped": True, "reason": "skip_am_or_dry_run_only"}

    if not state.options.dry_run_only and not state.options.skip_kabu:
        state.pm_wait["kabu_register_clear_before_pm"] = kabu_clear_stale_registrations(
            state, label="before_pm_session"
        )

    state.pm_prep = build_pm_universe(state)
    if not state.pm_prep.get("ok"):
        state.verdict = "universe_generation_failed"
        state.verdict_notes.append("PM universe generation or validation failed")
        state.stopped_reason = "pm_universe"
        write_outputs(state)
        return 2

    pm_csv_path = state.repo_root / str(state.pm_prep.get("pm_csv") or "")
    state.pm_prep["screening_notify"] = notify_screening_universe_discord(
        state,
        session_label="PM Screening",
        universe_csv=pm_csv_path,
    )

    if state.options.enable_intraday_refresh and not state.am_prep.get("intraday_refresh"):
        from universe.daily_features import load_features_csv

        feat_path = features_csv(state.reports_dir, state.options.day_stamp)
        feature_rows = load_features_csv(feat_path) if feat_path.is_file() else []
        state.am_prep["intraday_refresh"] = build_intraday_refresh_universes(
            state, feature_rows=feature_rows, open_symbols_test=()
        )
        if not state.am_prep["intraday_refresh"].get("ok"):
            state.verdict = "refresh_universe_generation_failed"
            state.stopped_reason = "refresh_universe"
            write_outputs(state)
            return 2

    if not state.options.dry_run_only:
        try:
            state.pm_live = run_pilot_session(state, session="pm")
        finally:
            state.sessions["pm_dir"] = (state.pm_live or {}).get("session_dir")
            state.sessions["pm_finalize"] = (state.pm_live or {}).get("finalize_snapshot")
            _write_pilot_checkpoint(state, session="pm")
            state.sessions["kabu_register_clear_after_pm"] = kabu_clear_stale_registrations(
                state, label="after_pm_session"
            )
        if _pilot_failed_hard(state.pm_live or {}):
            state.verdict = "pm_failed"
            state.verdict_notes.append(
                f"PM pilot exit={state.pm_live.get('exit_code')} "
                f"error={state.pm_live.get('error')}"
            )
            state.stopped_reason = "pm_pilot"
            write_outputs(state)
            return 2
        _apply_session_detection_warning(state, session="pm", live=state.pm_live)
    else:
        state.pm_live = {"skipped": True, "reason": "dry_run_only"}

    if state.options.enable_intraday_refresh and state.options.dry_run_only:
        state.verdict = "intraday_refresh_shadow_ready"
    else:
        state.verdict = "am_pm_daily_runner_ready"
    state.verdict_notes.append(
        "AM/PM run complete" + (" (dry_run_only)" if state.options.dry_run_only else "")
    )
    if state.options.enable_intraday_refresh:
        state.verdict_notes.append("intraday_refresh CSVs built; pilot refresh at 10:00/14:30 when live")
    state.commands = build_commands_json(state)
    write_outputs(state)
    return 0


def make_state(repo_root: Path, native_root: Path, options: DailyRunnerOptions) -> DailyRunnerState:
    reports = repo_root / REPORTS_REL
    push = repo_root / PUSH_ROOT_REL
    return DailyRunnerState(
        options=options,
        repo_root=repo_root,
        native_root=native_root,
        reports_dir=reports,
        push_root=push,
        trade_date=parse_day_stamp(options.day_stamp),
    )
