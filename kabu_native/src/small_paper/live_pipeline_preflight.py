"""
Phase506: Live PUSH-compatible ENTRY pipeline preflight (no Kabu connection).

Exercises the same code path as pilot_runner._process_push_payload (post-freshness)
using float epoch-second price rings — the format that triggered Phase505 failure.
"""

from __future__ import annotations

import tempfile
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from research.exposure_gate import (
    REJECT_CLASSIC_LATE_CHASE_RSI_OVER80,
    REJECT_ENTRY_QUALITY_GUARD_SPREAD,
    REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT,
    REJECT_REENTRY_RSI_GUARD_BELOW60,
)
from small_paper.config import SmallPaperPilotConfig, load_pilot_config
from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields
from small_paper.entry_scan_controller import (
    compute_entry_freshness,
    evaluate_entry_data_freshness,
)
from small_paper.board_imbalance_shadow import compute_entry_order_book_imbalance_field
from small_paper.extended_entry_shadow import (
    append_price_tick,
    compute_entry_high_break_recent_field,
    tick_ts_from_payload,
)
from small_paper.live_feature_bridge import LiveFeatureBridge
from small_paper.live_writer import LiveSessionWriter
from small_paper.pilot_runner import (
    EVENT_FIELDS,
    _LiveRunState,
    _PushPipelineContext,
    _candidate_trade_from_push,
    _enrich_trade_for_pullback_guard,
    _evaluate_gate_entry,
    _symbol_from_push,
)

JST = ZoneInfo("Asia/Tokyo")
PREFLIGHT_VERDICT = "preflight_ready"
PHASE525_RUNTIME_VERDICT = "phase525_reentry_rsi_guard_runtime_ready"
PHASE528_RUNTIME_VERDICT = "phase528_entry_quality_guard_runtime_ready"

DEFAULT_CONFIG_REL = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)


@dataclass
class PreflightCaseResult:
    case_id: str
    ok: bool = False
    error: str = ""
    uses_float_epoch_timestamps: bool = False
    tick_ts_type: str = ""
    ring_ts_type: str = ""
    rsi14: Optional[float] = None
    late_chase_flag: Optional[bool] = None
    full_exposure_gate_reached: bool = False
    decision_accept: Optional[bool] = None
    decision_reason: str = ""
    classic_guard_enabled: bool = True
    reentry_guard_enabled: bool = True
    seed_prior_stop_exit: bool = False
    entry_quality_guard_enabled: Optional[bool] = None
    spread_bps: Optional[float] = None
    update_count_before_entry: Optional[int] = None
    notes: str = ""


@dataclass
class PreflightReport:
    verdict: str = ""
    ready: bool = False
    config_path: str = ""
    config_sha256: str = ""
    cases: list[PreflightCaseResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _now_epoch() -> float:
    return datetime.now(JST).timestamp()


def _iso_now() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def build_normal_preflight_price_ring(
    *,
    entry_ts: float,
    base_price: float,
) -> list[tuple[float, float]]:
    """Gentle drift down: not late_chase, RSI not overstretched, float epoch seconds."""
    ring: list[tuple[float, float]] = []
    for i in range(20):
        ts = entry_ts - float((19 - i) * 60)
        px = base_price * (1.0 - 0.0002 * i)
        ring.append((ts, round(px, 4)))
    return ring


def build_float_epoch_price_ring(
    *,
    entry_ts: float,
    minutes: int,
    base_price: float,
    rise_pct_per_min: float,
) -> list[tuple[float, float]]:
    """Live-compatible ring: (epoch_seconds, price) — never datetime."""
    ring: list[tuple[float, float]] = []
    for i in range(minutes):
        ts = entry_ts - float((minutes - 1 - i) * 60)
        px = base_price * (1.0 + rise_pct_per_min * i / 100.0)
        ring.append((ts, round(px, 4)))
    return ring


def build_live_mock_push_payload(
    *,
    symbol: str,
    price: float,
    entry_ts: Optional[float] = None,
) -> dict[str, Any]:
    """Kabu PUSH-shaped payload; CurrentPriceTime resolves to fresh board/price ages."""
    ts = entry_ts if entry_ts is not None else _now_epoch()
    dt = datetime.fromtimestamp(ts, tz=JST)
    t_iso = dt.isoformat(timespec="milliseconds")
    code = symbol.replace(".T", "")
    vwap = round(price * 0.985, 4)
    return {
        "Symbol": code,
        "CurrentPrice": price,
        "CurrentPriceTime": t_iso,
        "VWAP": vwap,
        "HighPrice": round(price * 1.005, 4),
        "LowPrice": round(price * 0.98, 4),
        "BidPrice": round(price - 1.0, 4),
        "AskPrice": round(price + 1.0, 4),
        "BidQty": 1200,
        "AskQty": 900,
        "BidTime": t_iso,
        "AskTime": t_iso,
        "TradingVolume": 250_000,
        "TradingValue": 800_000_000,
    }


def _align_entry_time_to_allowed_window(trade: dict[str, Any]) -> None:
    """Keep evaluate_entry inside configured AM/PM windows during off-hours preflight."""
    from small_paper.allowed_trading_windows import (
        is_in_allowed_trading_window,
        parse_allowed_trading_windows,
    )

    windows = parse_allowed_trading_windows(None)
    entry_s = str(trade.get("entry_time") or "")
    if entry_s and is_in_allowed_trading_window(entry_s, windows):
        return
    now = datetime.now(JST)
    entry_dt = now.replace(hour=13, minute=30, second=0, microsecond=0)
    if not is_in_allowed_trading_window(entry_dt.isoformat(), windows):
        entry_dt = now.replace(hour=10, minute=0, second=0, microsecond=0)
    trade["entry_time"] = entry_dt.isoformat(timespec="milliseconds")
    ex_ts = entry_dt.timestamp() + 300.0
    trade["exit_time"] = datetime.fromtimestamp(ex_ts, tz=JST).isoformat(timespec="milliseconds")
    trade["trade_date"] = entry_dt.date().isoformat()


def _seed_pbv2_pass_fields(
    trade: dict[str, Any],
    *,
    near_day_distance_pct: float = 2.5,
) -> None:
    """Minimal PBv2-passing fields after live feature enrich (preflight only)."""
    trade.update(
        {
            "momentum_continuation_score": 0.22,
            "entry_momentum_score": 0.22,
            "entry_momentum_continuation_score": 0.22,
            "entry_order_book_imbalance": 0.48,
            "trading_value": 35_000_000_000.0,
            "max_continuation_duration": 420,
            "rolling_mae_pct": 0.0,
            "rolling_mfe_pct": 0.02,
            "current_price": trade.get("current_price") or trade.get("CurrentPrice"),
            "entry_high_break_recent": False,
            "continuation_quality_score": 0.78,
            "daytrade_suitability_score": 58.0,
            "atr_pct": 2.5,
            "intraday_range_pct": 3.0,
            "turnover_proxy": 0.01,
            "entry_near_day_high_pct": near_day_distance_pct,
            "day_high_distance_pct": near_day_distance_pct,
        }
    )
    trade.update(compute_entry_expectancy_score_fields(trade=trade))


def build_high_update_count_price_ring(
    *,
    entry_ts: float,
    minutes: int = 10,
    base_price: float = 2800.0,
    step_yen: float = 5.0,
) -> list[tuple[float, float]]:
    """Monotonic 1m highs → high update_count_before_entry (float epoch seconds)."""
    ring: list[tuple[float, float]] = []
    for i in range(minutes):
        ts = entry_ts - float((minutes - 1 - i) * 60)
        px = base_price + step_yen * i
        ring.append((ts, round(px, 4)))
    return ring


def build_wide_spread_push_payload(
    *,
    symbol: str,
    price: float,
    entry_ts: Optional[float] = None,
    spread_bps_target: float = 80.0,
) -> dict[str, Any]:
    payload = build_live_mock_push_payload(symbol=symbol, price=price, entry_ts=entry_ts)
    half = price * spread_bps_target / 20000.0
    payload["BidPrice"] = round(price - half, 4)
    payload["AskPrice"] = round(price + half, 4)
    return payload


def _make_pipeline_context(
    config: SmallPaperPilotConfig,
    *,
    output_dir: Path,
    classic_guard_enabled: bool,
    reentry_guard_enabled: Optional[bool] = None,
    isolate_reentry_guard: bool = False,
    entry_quality_guard_enabled: Optional[bool] = None,
    isolate_entry_quality_guard: bool = False,
    repo_root: Optional[Path] = None,
) -> _PushPipelineContext:
    gate = config.make_exposure_gate(repo_root=repo_root)
    classic = getattr(gate, "classic_late_chase_rsi_guard", None)
    if classic is not None:
        classic.config.enabled = bool(classic_guard_enabled)
    reentry = getattr(gate, "reentry_rsi_guard", None)
    if reentry is not None and reentry_guard_enabled is not None:
        reentry.config.enabled = bool(reentry_guard_enabled)
    if isolate_reentry_guard:
        gate.high_drift_pullback_guard = None
        gate.weak_shape_reject_guard = None
        gate.near_day_high_low_momentum_dynamic40_guard = None
        gate.late_chase_guard = None
        gate.pullback_misread_dynamic40_guard = None
        gate.entry_price_risk_guard = None
        gate.daytrade_suitability = None
        gate.symbol_cooloff = None
    entry_quality = getattr(gate, "entry_quality_guard", None)
    if entry_quality is not None and entry_quality_guard_enabled is not None:
        entry_quality.config.enabled = bool(entry_quality_guard_enabled)
    if isolate_entry_quality_guard:
        gate.high_drift_pullback_guard = None
        gate.weak_shape_reject_guard = None
        gate.near_day_high_low_momentum_dynamic40_guard = None
        gate.late_chase_guard = None
        gate.pullback_misread_dynamic40_guard = None
        gate.classic_late_chase_rsi_guard = None
        gate.reentry_rsi_guard = None
        gate.entry_price_risk_guard = None
        gate.daytrade_suitability = None
        gate.symbol_cooloff = None

    from small_paper.entry_scan_controller import entry_scan_controller_from_config

    writer = LiveSessionWriter(output_dir, incremental=False, event_fields=EVENT_FIELDS)
    entry_scan = entry_scan_controller_from_config(
        config,
        pipeline_source="live",
        audit_writer=writer.append_entry_scan_audit,
    )
    return _PushPipelineContext(
        config=config,
        gate=gate,
        feature_bridge=LiveFeatureBridge(config.feature_bridge_config()),
        state=_LiveRunState(started_mono=time.monotonic()),
        writer=writer,
        code_to_symbol={},
        source="live",
        pos_fields=(),
        entry_eligible_symbols=set(),
        entry_scan=entry_scan,
        symbol_universe_meta={
            "6976.T": {"universe_slot": "dynamic", "source_bucket": "dynamic40"},
        },
    )


def run_live_pipeline_case(
    *,
    case_id: str,
    config: SmallPaperPilotConfig,
    sym: str = "6976.T",
    price_ring: list[tuple[float, float]],
    payload: Mapping[str, Any],
    classic_guard_enabled: bool = True,
    reentry_guard_enabled: Optional[bool] = None,
    seed_prior_stop_exit: bool = False,
    isolate_reentry_guard: bool = False,
    entry_quality_guard_enabled: Optional[bool] = None,
    isolate_entry_quality_guard: bool = False,
    output_dir: Optional[Path] = None,
    near_day_distance_pct: float = 2.5,
    repo_root: Optional[Path] = None,
) -> PreflightCaseResult:
    """Run one live-shaped ENTRY evaluation; return structured result."""
    result = PreflightCaseResult(
        case_id=case_id,
        classic_guard_enabled=classic_guard_enabled,
        reentry_guard_enabled=(
            reentry_guard_enabled
            if reentry_guard_enabled is not None
            else bool(getattr(config, "reentry_rsi_guard_enabled", False))
        ),
        seed_prior_stop_exit=seed_prior_stop_exit,
    )
    if entry_quality_guard_enabled is not None:
        result.entry_quality_guard_enabled = bool(entry_quality_guard_enabled)
    else:
        result.entry_quality_guard_enabled = bool(
            getattr(config, "entry_quality_guard_enabled", False)
        )
    if not price_ring:
        result.error = "empty price_ring"
        return result
    result.ring_ts_type = type(price_ring[0][0]).__name__
    result.uses_float_epoch_timestamps = result.ring_ts_type == "float"

    own_tmp = output_dir is None
    tmp = output_dir or Path(tempfile.mkdtemp(prefix="live_preflight_"))
    try:
        ctx = _make_pipeline_context(
            config,
            output_dir=tmp,
            classic_guard_enabled=classic_guard_enabled,
            reentry_guard_enabled=reentry_guard_enabled,
            isolate_reentry_guard=isolate_reentry_guard,
            entry_quality_guard_enabled=entry_quality_guard_enabled,
            isolate_entry_quality_guard=isolate_entry_quality_guard,
            repo_root=repo_root,
        )
        ctx.entry_eligible_symbols = {sym}
        ctx.code_to_symbol = {str(payload.get("Symbol") or ""): sym}

        reentry_guard = getattr(ctx.gate, "reentry_rsi_guard", None)
        if seed_prior_stop_exit and reentry_guard is not None:
            reentry_guard.record_exit(
                {
                    "symbol": sym,
                    "exit_reason": "stop_hit",
                    "structural_exit_reason": "stop_hit",
                    "stop_hit": True,
                }
            )

        tick_ts = tick_ts_from_payload(payload)
        result.tick_ts_type = type(tick_ts).__name__
        if result.tick_ts_type != "float":
            result.error = f"tick_ts_from_payload returned {result.tick_ts_type}, expected float"
            return result

        parsed_sym = _symbol_from_push(payload, ctx.code_to_symbol)
        if parsed_sym != sym:
            result.error = f"symbol parse mismatch: {parsed_sym!r} != {sym!r}"
            return result

        probe_ring: list[tuple[float, float]] = []
        append_price_tick(
            probe_ring,
            ts=tick_ts,
            px=float(payload.get("CurrentPrice") or 0),
        )
        if not probe_ring:
            result.error = "append_price_tick did not record tick"
            return result
        ctx.symbol_price_ring[sym] = list(price_ring)

        snapshot = ctx.feature_bridge.update(sym, payload)
        enriched = ctx.feature_bridge.enrich_payload(payload, snapshot)
        push_received_at = _iso_now()
        if not enriched.get("recorded_at"):
            enriched["recorded_at"] = push_received_at
        trade = _candidate_trade_from_push(
            enriched,
            symbol=sym,
            profile=config.profile,
            feature_snapshot=snapshot,
            virtual_hold_sec=float(config.entry_cooldown_sec),
        )
        live_px = payload.get("CurrentPrice")
        if live_px is not None:
            trade.setdefault("CurrentPrice", live_px)
            trade.setdefault("current_price", live_px)

        trade.update(
            compute_entry_high_break_recent_field(
                trade=trade,
                payload=payload,
                price_ring=ctx.symbol_price_ring.get(sym, []),
                entry_ts=tick_ts,
            )
        )
        trade.update(compute_entry_order_book_imbalance_field(payload=enriched))

        freshness = compute_entry_freshness(enriched, pipeline_source=ctx.source)
        stale_reason = None
        if ctx.entry_scan is not None:
            ref_now = datetime.now(JST)
            fresh_decision = evaluate_entry_data_freshness(
                freshness,
                enriched,
                max_price_age_sec=ctx.entry_scan.max_price_age_sec,
                max_board_age_sec=ctx.entry_scan.max_board_age_sec,
                guard_enabled=ctx.entry_scan.freshness_guard_enabled,
                board_fallback_enabled=ctx.entry_scan.board_fallback_enabled,
                max_fallback_spread_bps=ctx.entry_scan.board_fallback_max_spread_bps,
                reference_now=ref_now,
                freshness_semantics_v2_enabled=ctx.entry_scan.freshness_semantics_v2_enabled,
                event_stale_threshold_sec=ctx.entry_scan.event_stale_threshold_sec,
                board_stale_threshold_sec=ctx.entry_scan.board_stale_threshold_sec,
                trade_stale_threshold_sec=ctx.entry_scan.trade_stale_threshold_sec,
                trade_stale_mode=ctx.entry_scan.trade_stale_mode,
            )
            stale_reason = fresh_decision.reject_reason
        if stale_reason:
            result.error = f"freshness blocked preflight: {stale_reason}"
            result.notes = "board/price freshness must pass for pipeline preflight"
            return result

        trade.update(compute_entry_expectancy_score_fields(trade=trade))
        _align_entry_time_to_allowed_window(trade)
        _enrich_trade_for_pullback_guard(ctx, sym=sym, trade=trade, payload=payload)
        _seed_pbv2_pass_fields(trade, near_day_distance_pct=near_day_distance_pct)

        result.rsi14 = trade.get("rsi14") if isinstance(trade.get("rsi14"), (int, float)) else None
        lcf = trade.get("late_chase_flag")
        result.late_chase_flag = bool(lcf) if lcf is not None else None
        sb = trade.get("spread_bps")
        result.spread_bps = float(sb) if isinstance(sb, (int, float)) else None
        uc = trade.get("update_count_before_entry")
        result.update_count_before_entry = int(uc) if isinstance(uc, int) else (
            int(uc) if isinstance(uc, float) else None
        )

        decision = _evaluate_gate_entry(ctx, trade)
        result.full_exposure_gate_reached = True
        result.decision_accept = bool(decision.accept)
        result.decision_reason = str(decision.reason or "")
        result.ok = True
        return result
    except AttributeError as exc:
        if "total_seconds" in str(exc):
            result.error = f"total_seconds AttributeError: {exc}"
        else:
            result.error = f"AttributeError: {exc}\n{traceback.format_exc()}"
        return result
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        return result
    finally:
        if own_tmp:
            try:
                import shutil

                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass


def _case_expectations(case_id: str, result: PreflightCaseResult) -> list[str]:
    errs: list[str] = []
    entry_quality_cases = {
        "entry_quality_pass",
        "entry_quality_spread_block",
        "entry_quality_update_block",
        "entry_quality_guard_disabled",
    }
    if not result.uses_float_epoch_timestamps or result.tick_ts_type != "float":
        errs.append("timestamps must be float epoch seconds")
    if case_id not in entry_quality_cases and result.rsi14 is None:
        errs.append("rsi14 not computed")
    if not result.full_exposure_gate_reached:
        errs.append("full_exposure_gate_reached is False")
    if case_id in entry_quality_cases:
        if result.spread_bps is None:
            errs.append("spread_bps not computed")
        if result.update_count_before_entry is None and case_id != "entry_quality_spread_block":
            errs.append("update_count_before_entry not computed")

    if case_id == "normal_candidate":
        if result.decision_reason == REJECT_CLASSIC_LATE_CHASE_RSI_OVER80:
            errs.append("normal case must not hit classic_late_chase_rsi_over80")
        if result.late_chase_flag is True:
            errs.append("normal case expected late_chase_flag False")
    elif case_id == "late_chase_rsi_block":
        if result.classic_guard_enabled and result.decision_reason != REJECT_CLASSIC_LATE_CHASE_RSI_OVER80:
            errs.append(
                f"expected {REJECT_CLASSIC_LATE_CHASE_RSI_OVER80}, got {result.decision_reason!r}"
            )
        if result.late_chase_flag is not True:
            errs.append("late_chase_flag expected True")
        if result.rsi14 is not None and result.rsi14 < 80.0:
            errs.append(f"rsi14 expected >= 80, got {result.rsi14}")
    elif case_id == "late_chase_guard_disabled":
        if result.decision_reason == REJECT_CLASSIC_LATE_CHASE_RSI_OVER80:
            errs.append("disabled guard must not reject classic_late_chase_rsi_over80")
        if result.classic_guard_enabled:
            errs.append("classic_guard_enabled should be False for this case")
    elif case_id == "reentry_stop_rsi_low":
        if result.reentry_guard_enabled and result.seed_prior_stop_exit:
            if result.decision_reason != REJECT_REENTRY_RSI_GUARD_BELOW60:
                errs.append(
                    f"expected {REJECT_REENTRY_RSI_GUARD_BELOW60}, got {result.decision_reason!r}"
                )
        if result.rsi14 is not None and result.rsi14 > 60.0:
            errs.append(f"rsi14 expected <= 60 for low case, got {result.rsi14}")
    elif case_id == "reentry_stop_rsi_high":
        if result.reentry_guard_enabled and result.seed_prior_stop_exit:
            if result.decision_reason == REJECT_REENTRY_RSI_GUARD_BELOW60:
                errs.append("high RSI re-entry after stop should pass reentry guard")
        if result.rsi14 is not None and result.rsi14 <= 60.0:
            errs.append(f"rsi14 expected > 60 for high case, got {result.rsi14}")
    elif case_id == "first_entry_rsi_low":
        if result.decision_reason == REJECT_REENTRY_RSI_GUARD_BELOW60:
            errs.append("first entry must not hit reentry_rsi_guard_below60")
        if result.seed_prior_stop_exit:
            errs.append("first_entry case must not seed prior stop")
    elif case_id == "reentry_guard_disabled":
        if result.decision_reason == REJECT_REENTRY_RSI_GUARD_BELOW60:
            errs.append("disabled reentry guard must not reject")
        if result.reentry_guard_enabled:
            errs.append("reentry_guard_enabled should be False for this case")
    elif case_id == "entry_quality_pass":
        if result.entry_quality_guard_enabled and result.decision_reason in (
            REJECT_ENTRY_QUALITY_GUARD_SPREAD,
            REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT,
        ):
            errs.append(f"pass case must not hit entry quality guard: {result.decision_reason!r}")
    elif case_id == "entry_quality_spread_block":
        if result.entry_quality_guard_enabled:
            if result.decision_reason != REJECT_ENTRY_QUALITY_GUARD_SPREAD:
                errs.append(
                    f"expected {REJECT_ENTRY_QUALITY_GUARD_SPREAD}, got {result.decision_reason!r}"
                )
        if result.spread_bps is not None and result.spread_bps <= 50.0:
            errs.append(f"spread_bps expected > 50, got {result.spread_bps}")
    elif case_id == "entry_quality_update_block":
        if result.entry_quality_guard_enabled:
            if result.decision_reason != REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT:
                errs.append(
                    f"expected {REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT}, got {result.decision_reason!r}"
                )
        if result.update_count_before_entry is not None and result.update_count_before_entry <= 5:
            errs.append(
                f"update_count expected > 5, got {result.update_count_before_entry}"
            )
    elif case_id == "entry_quality_guard_disabled":
        if result.decision_reason in (
            REJECT_ENTRY_QUALITY_GUARD_SPREAD,
            REJECT_ENTRY_QUALITY_GUARD_UPDATE_COUNT,
        ):
            errs.append("disabled entry quality guard must not reject")
    return errs


def run_live_pipeline_preflight(
    *,
    config_path: Path,
    repo_root: Optional[Path] = None,
    expected_config_sha256: Optional[str] = None,
) -> PreflightReport:
    cfg_path = config_path
    if repo_root and not cfg_path.is_absolute():
        cfg_path = repo_root / cfg_path
    config = load_pilot_config(cfg_path)
    report = PreflightReport(config_path=str(cfg_path))

    from small_paper.config import config_file_sha256

    actual_sha = config_file_sha256(cfg_path)
    report.config_sha256 = actual_sha
    pin_path = cfg_path.parent / "production_config_sha256.pin"
    pin_sha = pin_path.read_text(encoding="utf-8").strip() if pin_path.exists() else ""
    expect = (expected_config_sha256 or pin_sha or "").strip()
    if expect and expect != actual_sha:
        report.errors.append(
            f"config SHA mismatch: disk={actual_sha[:16]}... expected={expect[:16]}... — abort session start"
        )

    from small_paper.live_order_adapter import phase594_preflight_check

    p594_ok, p594_msg = phase594_preflight_check(config)
    if not p594_ok:
        report.errors.append(p594_msg)

    if getattr(config, "entry_cluster_guard_enabled", False):
        from small_paper.entry_cluster_guard import validate_entry_cluster_guard_model

        if repo_root is None:
            report.errors.append("entry_cluster_guard_enabled but repo_root missing for model load")
        else:
            _, cg_errors = validate_entry_cluster_guard_model(config, repo_root=repo_root)
            report.errors.extend(cg_errors)

    if getattr(config, "stop_low_mfe_guard_enabled", False):
        gate = config.make_exposure_gate(repo_root=repo_root)
        if getattr(gate, "stop_low_mfe_guard", None) is None:
            report.errors.append("stop_low_mfe_guard_enabled but ExposureGate.stop_low_mfe_guard is None")

    from small_paper.phase627_preflight import phase627_preflight_checks

    report.errors.extend(phase627_preflight_checks(config, repo_root=repo_root))

    if getattr(config, "exit_shadow_monitor_enabled", False):
        from small_paper.exit_shadow_monitor import SUMMARY_FIELD_KEYS, finalize_session_exit_shadow_monitor_safe
        from small_paper.exit_shadow_monitor import config_from_pilot

        sample = finalize_session_exit_shadow_monitor_safe([], monitor=config_from_pilot(config))
        missing = [k for k in SUMMARY_FIELD_KEYS if k not in sample]
        if missing:
            report.errors.append(f"exit_shadow_monitor summary missing keys: {missing}")

    entry_ts = _now_epoch()
    sym = "6976.T"

    normal_ring = build_normal_preflight_price_ring(entry_ts=entry_ts, base_price=2800.0)
    chase_ring = build_float_epoch_price_ring(
        entry_ts=entry_ts,
        minutes=25,
        base_price=2600.0,
        rise_pct_per_min=0.22,
    )
    normal_px = normal_ring[-1][1]
    chase_px = chase_ring[-1][1]

    specs: list[tuple[str, list[tuple[float, float]], float, bool]] = [
        ("normal_candidate", normal_ring, normal_px, True),
        ("late_chase_rsi_block", chase_ring, chase_px, True),
        ("late_chase_guard_disabled", chase_ring, chase_px, False),
    ]

    for case_id, ring, px, guard_on in specs:
        payload = build_live_mock_push_payload(symbol=sym, price=px, entry_ts=entry_ts)
        result = run_live_pipeline_case(
            case_id=case_id,
            config=config,
            sym=sym,
            price_ring=ring,
            payload=payload,
            classic_guard_enabled=guard_on,
            repo_root=repo_root,
        )
        case_errs = _case_expectations(case_id, result)
        if case_errs:
            result.ok = False
            result.error = "; ".join(case_errs) if not result.error else f"{result.error}; " + "; ".join(case_errs)
        report.cases.append(result)
        if not result.ok:
            report.errors.append(f"{case_id}: {result.error}")

    report.ready = len(report.errors) == 0
    report.verdict = PREFLIGHT_VERDICT if report.ready else "preflight_failed"
    return report


def run_reentry_rsi_guard_preflight(
    *,
    config_path: Path,
    repo_root: Optional[Path] = None,
) -> PreflightReport:
    """Phase525: re-entry RSI guard cases with live float epoch price rings."""
    cfg_path = config_path
    if repo_root and not cfg_path.is_absolute():
        cfg_path = repo_root / cfg_path
    config = load_pilot_config(cfg_path)
    entry_ts = _now_epoch()
    sym = "6976.T"

    low_ring = build_normal_preflight_price_ring(entry_ts=entry_ts, base_price=2800.0)
    high_ring = build_float_epoch_price_ring(
        entry_ts=entry_ts,
        minutes=20,
        base_price=2400.0,
        rise_pct_per_min=0.35,
    )
    low_px = low_ring[-1][1]
    high_px = high_ring[-1][1]

    specs: list[tuple[str, list[tuple[float, float]], float, bool, bool, bool]] = [
        ("reentry_stop_rsi_low", low_ring, low_px, False, True, True),
        ("reentry_stop_rsi_high", high_ring, high_px, False, True, True),
        ("first_entry_rsi_low", low_ring, low_px, False, True, False),
        ("reentry_guard_disabled", low_ring, low_px, False, False, True),
    ]

    report = PreflightReport(config_path=str(cfg_path))
    for case_id, ring, px, classic_on, reentry_on, seed_stop in specs:
        payload = build_live_mock_push_payload(symbol=sym, price=px, entry_ts=entry_ts)
        result = run_live_pipeline_case(
            case_id=case_id,
            config=config,
            sym=sym,
            price_ring=ring,
            payload=payload,
            classic_guard_enabled=classic_on,
            reentry_guard_enabled=reentry_on,
            seed_prior_stop_exit=seed_stop,
            isolate_reentry_guard=True,
        )
        case_errs = _case_expectations(case_id, result)
        if case_errs:
            result.ok = False
            result.error = (
                "; ".join(case_errs) if not result.error else f"{result.error}; " + "; ".join(case_errs)
            )
        report.cases.append(result)
        if not result.ok:
            report.errors.append(f"{case_id}: {result.error}")

    report.ready = len(report.errors) == 0
    report.verdict = PHASE525_RUNTIME_VERDICT if report.ready else "preflight_failed"
    return report


def run_entry_quality_guard_preflight(
    *,
    config_path: Path,
    repo_root: Optional[Path] = None,
) -> PreflightReport:
    """Phase528: entry quality guard cases with live float epoch price rings."""
    cfg_path = config_path
    if repo_root and not cfg_path.is_absolute():
        cfg_path = repo_root / cfg_path
    config = load_pilot_config(cfg_path)
    entry_ts = _now_epoch()
    sym = "6976.T"

    pass_ring = build_normal_preflight_price_ring(entry_ts=entry_ts, base_price=2800.0)
    update_ring = build_high_update_count_price_ring(entry_ts=entry_ts, minutes=10)
    pass_px = pass_ring[-1][1]
    update_px = update_ring[-1][1]

    report = PreflightReport(config_path=str(cfg_path))
    cases: list[tuple[str, list[tuple[float, float]], Mapping[str, Any], bool]] = [
        (
            "entry_quality_pass",
            pass_ring,
            build_live_mock_push_payload(symbol=sym, price=pass_px, entry_ts=entry_ts),
            True,
        ),
        (
            "entry_quality_spread_block",
            pass_ring,
            build_wide_spread_push_payload(symbol=sym, price=pass_px, entry_ts=entry_ts),
            True,
        ),
        (
            "entry_quality_update_block",
            update_ring,
            build_live_mock_push_payload(symbol=sym, price=update_px, entry_ts=entry_ts),
            True,
        ),
        (
            "entry_quality_guard_disabled",
            update_ring,
            build_wide_spread_push_payload(symbol=sym, price=update_px, entry_ts=entry_ts),
            False,
        ),
    ]
    for case_id, ring, payload, guard_on in cases:
        result = run_live_pipeline_case(
            case_id=case_id,
            config=config,
            sym=sym,
            price_ring=ring,
            payload=payload,
            classic_guard_enabled=False,
            reentry_guard_enabled=False,
            entry_quality_guard_enabled=guard_on,
            isolate_entry_quality_guard=True,
        )
        case_errs = _case_expectations(case_id, result)
        if case_errs:
            result.ok = False
            result.error = (
                "; ".join(case_errs) if not result.error else f"{result.error}; " + "; ".join(case_errs)
            )
        report.cases.append(result)
        if not result.ok:
            report.errors.append(f"{case_id}: {result.error}")

    report.ready = len(report.errors) == 0
    report.verdict = PHASE528_RUNTIME_VERDICT if report.ready else "preflight_failed"
    return report


def default_config_path(repo_root: Path) -> Path:
    return repo_root.joinpath(*DEFAULT_CONFIG_REL.split("/"))
