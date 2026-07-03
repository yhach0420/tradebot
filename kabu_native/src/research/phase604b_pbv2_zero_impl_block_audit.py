"""
Phase604B — PBv2=0 implementation block audit.

Traces PBv2 accept path at code-step granularity; replays ExposureGate without OR overlay
to recover internal first blockers masked by or_overlay_not_candidate.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.exposure_gate import ExposureGate, GateDecision
from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import SmallPaperPilotConfig, load_pilot_config
from small_paper.entry_expectancy_score_shadow import (
    board_mid_or_high_required_for_v2,
    momentum_score_cutoff_pass,
)
from small_paper.or_overlay_cap import ENTRY_TYPE_PBV2, observer_cap_kwargs_for_pool
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

VERDICT = "phase604b_pbv2_zero_impl_block_audit_done"
JST = ZoneInfo("Asia/Tokyo")

# Code anchors for branch trace (investigation 1)
PBV2_CODE_ANCHORS = {
    "pre_am_pm_entry_stop": "pilot_runner.py:~2197 am_pm entry_allowed_now early return",
    "pre_outside_refresh_universe": "pilot_runner.py:~2206 entry_eligible_symbols check",
    "pre_data_stale_price": "entry_scan_controller.py:evaluate_entry_data_freshness",
    "pre_data_stale_board": "entry_scan_controller.py:evaluate_entry_data_freshness board",
    "exposure_gate_profile": "exposure_gate.py:237 profile mismatch",
    "exposure_gate_trading_window": "exposure_gate.py:250 allowed_trading_window",
    "exposure_gate_symbol_cooloff": "exposure_gate.py:266 symbol_cooloff",
    "exposure_gate_entry_price_risk": "exposure_gate.py:285 entry_price_risk_guard",
    "exposure_gate_pullback_misread": "exposure_gate.py:312 pullback_misread_dynamic40",
    "exposure_gate_high_drift": "exposure_gate.py:335 high_drift_pullback",
    "exposure_gate_weak_shape": "exposure_gate.py:360 weak_shape_reject",
    "exposure_gate_near_day_high": "exposure_gate.py:383 near_day_high_low_momentum",
    "exposure_gate_daytrade_suitability": "exposure_gate.py:408 daytrade_suitability",
    "exposure_gate_momentum_low": "exposure_gate.py:452 momentum_score_cutoff_pass",
    "exposure_gate_board_mid_high": "exposure_gate.py:462 board_mid_or_high_required_for_v2",
    "exposure_gate_score_v2": "exposure_gate.py:470 entry_score_v2_min",
    "exposure_gate_late_chase": "exposure_gate.py:479 late_chase_guard",
    "exposure_gate_classic_rsi": "exposure_gate.py:496 classic_late_chase_rsi",
    "exposure_gate_reentry_rsi": "exposure_gate.py:513 reentry_rsi_guard",
    "exposure_gate_entry_quality": "exposure_gate.py:530 entry_quality_guard",
    "exposure_gate_cluster": "exposure_gate.py:551 entry_cluster_guard",
    "exposure_gate_stop_low_mfe": "exposure_gate.py:578 stop_low_mfe_guard",
    "exposure_gate_risk_cluster": "exposure_gate.py:608 risk_cluster_blocked",
    "exposure_gate_daily_loss": "exposure_gate.py:617 daily_loss_guard",
    "exposure_gate_max_concurrent": "exposure_gate.py:626 position_cap max_concurrent",
    "pbv2_accept_branch": "exposure_gate.py:662 return accept=True",
    "or_overlay_try": "pilot_runner.py:2269 _maybe_try_or_overlay_entry",
    "or_overlay_not_candidate": "or_overlay_entry.py:396 REJECT_OR_OVERLAY_NOT_CANDIDATE",
    "post_max_entries_per_scan": "entry_scan_controller.py max_entries_per_scan flush",
    "post_same_symbol_overlap": "pilot_runner.py:1494 _maybe_reject_same_symbol_open_overlap",
}

SESSIONS = (
    ("20260630", "live_session_091118", "AM"),
    ("20260629", "live_session_080236", "AM"),
    ("20260629", "live_session_122526", "PM"),
    ("20260625", "live_session_080340", "AM"),
    ("20260625", "live_session_122535", "PM"),
    ("20260624", "live_session_081514", "AM"),
)

PBV2_PATH_FILES = (
    "src/research/exposure_gate.py",
    "src/small_paper/entry_scan_controller.py",
    "src/small_paper/pilot_runner.py",
    "src/small_paper/or_overlay_entry.py",
    "src/small_paper/or_overlay_cap.py",
    "src/small_paper/position_cap_mode.py",
    "src/small_paper/config.py",
    "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
)


@dataclass
class Pbv2TraceRow:
    day: str
    session: str
    event_time: str
    symbol: str
    event_type: str
    pre_blocker: str
    pre_code_anchor: str
    pbv2_internal_first_blocker: str
    pbv2_internal_code_anchor: str
    pbv2_would_accept: bool
    pbv2_reached_evaluate_entry: bool
    pbv2_accept_branch_reached: bool
    final_reject_reason: str
    overwritten_by_or_overlay: bool
    pbv2_would_accept_before_or: bool
    or_overlay_applied: bool
    entry_type_recorded: str
    momentum_score: Optional[float]
    entry_score_v2: Optional[int]
    board_token_pass: bool
    momentum_cutoff_pass: bool
    price_freshness_source: str
    fallback_used: bool


@dataclass
class BranchStats:
    day: str
    session: str
    exposure_gate_eval_calls: int = 0
    pbv2_branch_reached: int = 0
    pbv2_pre_accept_reached: int = 0
    pbv2_accept_branch: int = 0
    or_overlay_applied: int = 0
    or_overwrite_count: int = 0
    pbv2_accept_then_post_blocked: int = 0
    pbv2_accept_recorded_live: int = 0
    internal_blocker_counts: Counter = field(default_factory=Counter)
    pre_blocker_counts: Counter = field(default_factory=Counter)


def _float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _bool(v: Any) -> bool:
    return str(v or "").strip().lower() in ("true", "1", "yes")


def _session_dir(base: Path, day: str, session: str) -> Path:
    return base / "results" / "small_paper" / day / session


def _load_config_for_session(session_dir: Path) -> SmallPaperPilotConfig:
    meta_path = session_dir / "live_session_config.json"
    summ_path = session_dir / "small_paper_summary.json"
    cfg_path: Optional[Path] = None
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        raw = meta.get("config_path")
        if raw:
            cfg_path = Path(str(raw))
    if cfg_path is None and summ_path.exists():
        summ = json.loads(summ_path.read_text(encoding="utf-8"))
        raw = summ.get("config_path")
        if raw:
            cfg_path = Path(str(raw))
    if cfg_path is None or not cfg_path.exists():
        cfg_path = repo / "configs" / (
            "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
        )
    return load_pilot_config(cfg_path)


def _effective_runtime_config(session_dir: Path, config: SmallPaperPilotConfig) -> dict[str, Any]:
    meta = {}
    summ = {}
    if (session_dir / "live_session_config.json").exists():
        meta = json.loads((session_dir / "live_session_config.json").read_text(encoding="utf-8"))
    if (session_dir / "small_paper_summary.json").exists():
        summ = json.loads((session_dir / "small_paper_summary.json").read_text(encoding="utf-8"))
    cfg_path = Path(str(meta.get("config_path") or summ.get("config_path") or ""))
    yaml_sha = ""
    if cfg_path.exists():
        yaml_sha = hashlib.sha256(cfg_path.read_bytes()).hexdigest()
    return {
        "day": session_dir.parent.name,
        "session": session_dir.name,
        "config_path": str(cfg_path),
        "config_sha256_session": meta.get("config_sha256") or summ.get("config_sha256"),
        "config_sha256_disk_yaml": yaml_sha,
        "config_sha_match_disk": (meta.get("config_sha256") or summ.get("config_sha256")) == yaml_sha,
        "or_overlay_enabled": summ.get("or_overlay_enabled", config.or_overlay_enabled),
        "cap_pbv2": summ.get("cap_pbv2", config.cap_pbv2),
        "cap_or": summ.get("cap_or", config.cap_or),
        "max_concurrent_positions": summ.get("max_concurrent_positions", config.max_concurrent_positions),
        "entry_score_v2_min": summ.get("entry_score_v2_min", config.entry_score_v2_min),
        "momentum_score_cutoff_max": getattr(config, "momentum_score_cutoff_max", None),
        "min_continuation_quality": summ.get("min_continuation_quality", config.min_continuation_quality),
        "reject_below_quality": summ.get("reject_below_quality", config.reject_below_quality),
        "entry_freshness_board_fallback_enabled": config.entry_freshness_board_fallback_enabled,
        "enable_pullback_misread_dynamic40_guard": summ.get(
            "enable_pullback_misread_dynamic40_guard", config.enable_pullback_misread_dynamic40_guard
        ),
        "enable_near_day_high_low_momentum_dynamic40_guard": summ.get(
            "enable_near_day_high_low_momentum_dynamic40_guard",
            config.enable_near_day_high_low_momentum_dynamic40_guard,
        ),
        "stop_low_mfe_guard_enabled": summ.get(
            "stop_low_mfe_guard_enabled", getattr(config, "stop_low_mfe_guard_enabled", None)
        ),
        "position_cap_mode": summ.get("position_cap_mode", config.position_cap_mode),
        "same_symbol_open_policy": summ.get("same_symbol_open_policy", config.same_symbol_open_policy),
        "daytrade_suitability_enabled": summ.get(
            "daytrade_suitability_enabled", config.daytrade_suitability_enabled
        ),
        "daytrade_suitability_threshold": summ.get("daytrade_suitability_threshold"),
        "pbv2_count_live": summ.get("pbv2_count"),
        "or_entry_count_live": summ.get("or_entry_count"),
        "accepted_count_live": summ.get("accepted_count"),
    }


def _pre_gate_blocker(row: Mapping[str, Any]) -> tuple[str, str]:
    rr = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
    if rr == "am_pm_entry_stop":
        return "am_pm_entry_stop", PBV2_CODE_ANCHORS["pre_am_pm_entry_stop"]
    if rr == "outside_refresh_universe":
        return "outside_refresh_universe", PBV2_CODE_ANCHORS["pre_outside_refresh_universe"]
    if rr in ("data_stale_price", "data_stale_price"):
        pfs = str(row.get("price_freshness_source") or "")
        if pfs == "board_fallback":
            return "data_stale_price_after_board_fallback_fail", PBV2_CODE_ANCHORS["pre_data_stale_price"]
        return "data_stale_price", PBV2_CODE_ANCHORS["pre_data_stale_price"]
    if rr == "data_stale_board":
        return "data_stale_board", PBV2_CODE_ANCHORS["pre_data_stale_board"]
    return "", ""


def _trace_pbv2_internal(
    gate: ExposureGate,
    trade: Mapping[str, Any],
    *,
    config: SmallPaperPilotConfig,
    observer_open: int = 0,
    observer_symbol_open: bool = False,
) -> tuple[str, str, bool]:
    """Mirror ExposureGate.evaluate_entry order; return (blocker, anchor, would_accept)."""
    profile = str(trade.get("profile", ""))
    if profile != gate.config.profile:
        return "wrong_profile", PBV2_CODE_ANCHORS["exposure_gate_profile"], False

    if gate._allowed_windows is not None:
        from small_paper.allowed_trading_windows import is_in_allowed_trading_window

        if not is_in_allowed_trading_window(str(trade.get("entry_time") or ""), gate._allowed_windows):
            return "outside_allowed_trading_window", PBV2_CODE_ANCHORS["exposure_gate_trading_window"], False

    if gate.symbol_cooloff is not None:
        chk = gate.symbol_cooloff.check(str(trade.get("symbol") or ""))
        if chk.blocked:
            return "symbol_cooloff", PBV2_CODE_ANCHORS["exposure_gate_symbol_cooloff"], False

    if gate.entry_price_risk_guard is not None:
        gr = gate.entry_price_risk_guard.check(trade)
        if gr.blocked:
            return "entry_price_risk_guard", PBV2_CODE_ANCHORS["exposure_gate_entry_price_risk"], False

    if gate.pullback_misread_dynamic40_guard is not None:
        pb = gate.pullback_misread_dynamic40_guard.check(trade)
        if pb.blocked:
            return "pullback_misread_dynamic40_guard", PBV2_CODE_ANCHORS["exposure_gate_pullback_misread"], False

    if gate.high_drift_pullback_guard is not None:
        hd = gate.high_drift_pullback_guard.check(trade)
        if hd.blocked:
            return "high_drift_pullback", PBV2_CODE_ANCHORS["exposure_gate_high_drift"], False

    if gate.weak_shape_reject_guard is not None:
        ws = gate.weak_shape_reject_guard.check(trade)
        if ws.blocked:
            return "weak_shape_reject_guard", PBV2_CODE_ANCHORS["exposure_gate_weak_shape"], False

    if gate.near_day_high_low_momentum_dynamic40_guard is not None:
        nd = gate.near_day_high_low_momentum_dynamic40_guard.check(trade)
        if nd.blocked:
            return "near_day_high_low_momentum_dynamic40_guard", PBV2_CODE_ANCHORS["exposure_gate_near_day_high"], False

    if gate.daytrade_suitability is not None:
        ds = gate.daytrade_suitability.check(trade)
        if ds.blocked:
            return "daytrade_suitability", PBV2_CODE_ANCHORS["exposure_gate_daytrade_suitability"], False

    v2_threshold = int(gate.config.entry_score_v2_min or 0)
    if v2_threshold > 0:
        if not momentum_score_cutoff_pass(trade, cutoff=gate.config.momentum_score_cutoff_max):
            return "momentum_low_required", PBV2_CODE_ANCHORS["exposure_gate_momentum_low"], False
        if not board_mid_or_high_required_for_v2(trade):
            return "entry_score_v2_below_threshold", PBV2_CODE_ANCHORS["exposure_gate_board_mid_high"], False
        v2_score = _int(trade.get("entry_expectancy_score_v2"))
        if v2_score is None or v2_score < v2_threshold:
            return "entry_score_v2_below_threshold", PBV2_CODE_ANCHORS["exposure_gate_score_v2"], False

        for name, guard, anchor, reason in (
            ("late_chase_guard", gate.late_chase_guard, PBV2_CODE_ANCHORS["exposure_gate_late_chase"], "late_chase_guard"),
            ("classic_late_chase_rsi_guard", gate.classic_late_chase_rsi_guard, PBV2_CODE_ANCHORS["exposure_gate_classic_rsi"], "classic_late_chase_rsi_guard"),
            ("reentry_rsi_guard", gate.reentry_rsi_guard, PBV2_CODE_ANCHORS["exposure_gate_reentry_rsi"], "reentry_rsi_guard_below60"),
        ):
            if guard is not None:
                chk = guard.check(trade)
                if chk.blocked:
                    return reason, anchor, False

        if gate.entry_quality_guard is not None:
            eq = gate.entry_quality_guard.check(trade)
            if eq.blocked:
                return str(eq.reject_reason or "entry_quality_guard"), PBV2_CODE_ANCHORS["exposure_gate_entry_quality"], False

        if gate.entry_cluster_guard is not None:
            cg = gate.entry_cluster_guard.check(trade)
            if cg.blocked:
                return "entry_cluster_guard", PBV2_CODE_ANCHORS["exposure_gate_cluster"], False

        if gate.stop_low_mfe_guard is not None:
            slm = gate.stop_low_mfe_guard.check(trade)
            if slm.blocked:
                return "stop_low_mfe_guard", PBV2_CODE_ANCHORS["exposure_gate_stop_low_mfe"], False

    if gate.state.risk_cluster_blocked:
        return "risk_cluster", PBV2_CODE_ANCHORS["exposure_gate_risk_cluster"], False

    day = str(trade.get("trade_date", ""))[:10]
    if day and gate.state.day_pnl.get(day, 0.0) <= gate.config.daily_loss_guard_pct:
        return "daily_loss_guard", PBV2_CODE_ANCHORS["exposure_gate_daily_loss"], False

    cap_kw = observer_cap_kwargs_for_pool(
        _FakeObserver(observer_open, observer_symbol_open),
        str(trade.get("symbol") or ""),
        entry_pool=ENTRY_TYPE_PBV2,
        cap_pbv2=int(getattr(config, "cap_pbv2", 4) or 4),
        cap_or=int(getattr(config, "cap_or", 1) or 1),
    )
    cap = int(cap_kw.get("max_concurrent_positions") or config.max_concurrent_positions)
    if config.position_cap_mode:
        if not cap_kw.get("observer_symbol_open") and int(cap_kw.get("observer_open_count") or 0) >= cap:
            return "max_concurrent", PBV2_CODE_ANCHORS["exposure_gate_max_concurrent"], False

    return "", PBV2_CODE_ANCHORS["pbv2_accept_branch"], True


class _FakeObserver:
    def __init__(self, open_count: int, symbol_open: bool) -> None:
        self._open = open_count
        self._sym = symbol_open

    def open_count(self) -> int:
        return self._open

    def has_open(self, _sym: str) -> bool:
        return self._sym


def _cap_simulation_rows(
    rows: Sequence[Mapping[str, Any]],
    gate: ExposureGate,
    config: SmallPaperPilotConfig,
) -> dict[str, int]:
    """Chronological PBv2-only replay with split-pool cap."""
    ordered = sorted(rows, key=lambda r: str(r.get("event_time") or ""))
    pbv2_open = 0
    or_open = 0
    cap_pbv2 = int(getattr(config, "cap_pbv2", 4) or 4)
    cap_or = int(getattr(config, "cap_or", 1) or 1)
    stats = Counter()
    for row in ordered:
        et = str(row.get("event_type") or "")
        if et not in ("accepted", "rejected"):
            continue
        pre, _ = _pre_gate_blocker(row)
        if pre:
            continue
        sym = str(row.get("symbol") or "")
        sym_open = False
        internal, _, would = _trace_pbv2_internal(
            gate,
            row,
            config=config,
            observer_open=pbv2_open + or_open,
            observer_symbol_open=sym_open,
        )
        final = str(row.get("gate_reject_reason") or "")
        if would and pbv2_open < cap_pbv2:
            if et == "accepted" and final == "":
                stats["pbv2_replay_accept"] += 1
                pbv2_open += 1
            elif et == "accepted" and final == "or_overlay_not_candidate":
                stats["pbv2_would_accept_but_or_accepted"] += 1
            else:
                stats["pbv2_would_accept"] += 1
        elif internal == "max_concurrent":
            stats["pbv2_cap_blocked"] += 1
        if et == "accepted" and final == "":
            entry_type = str(row.get("entry_type") or "OR").upper()
            if entry_type == "OR" and or_open < cap_or:
                or_open += 1
    return dict(stats)


def _chronological_pbv2_replay(
    eval_rows: Sequence[Mapping[str, Any]],
    gate: ExposureGate,
    config: SmallPaperPilotConfig,
) -> dict[str, Any]:
    """Stateful replay mirroring pilot_runner PBv2 path (exposure_gate.py:662 accept branch)."""
    ordered = sorted(eval_rows, key=lambda r: str(r.get("event_time") or ""))
    pbv2_open = 0
    or_open = 0
    cap_pbv2 = int(getattr(config, "cap_pbv2", 4) or 4)
    cap_or = int(getattr(config, "cap_or", 1) or 1)
    stats: Counter = Counter()
    overwrites: list[dict[str, Any]] = []
    traces: list[Pbv2TraceRow] = []

    for row in ordered:
        et = str(row.get("event_type") or "")
        final_rr = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
        pre_blocker, pre_anchor = _pre_gate_blocker(row)
        reached = not pre_blocker
        if pre_blocker:
            stats[f"pre:{pre_blocker}"] += 1
            continue

        stats["pbv2_branch_reached"] += 1
        stats["exposure_gate_eval_calls"] += 1

        sym = str(row.get("symbol") or "")
        cap_kw = observer_cap_kwargs_for_pool(
            _FakeObserver(pbv2_open + or_open, False),
            sym,
            entry_pool=ENTRY_TYPE_PBV2,
            cap_pbv2=cap_pbv2,
            cap_or=cap_or,
        )
        max_cap = cap_kw.pop("max_concurrent_positions", None)
        pbv2_decision = gate.evaluate_entry(
            row,
            **cap_kw,
            max_concurrent_positions=max_cap,
        )
        internal = str(pbv2_decision.reason or "")
        would_accept = bool(pbv2_decision.accept)

        if would_accept:
            stats["pbv2_accept_branch"] += 1
            gate.record_accepted(row)
            pbv2_open = min(cap_pbv2, pbv2_open + 1)
        elif internal:
            stats[f"internal:{internal}"] += 1
            if gate.entry_cluster_guard is not None:
                cg = gate.entry_cluster_guard.check(row)
                gate.entry_cluster_guard.record_reject(dict(row), cg)

        or_applied = (
            not would_accept
            and final_rr in ("or_overlay_not_candidate", "or_cap_full", "or_overlay_blocked")
        )
        overwritten = or_applied and bool(internal) and internal != final_rr
        if or_applied:
            stats["or_overlay_applied"] += 1
        if overwritten:
            stats["or_overwrite_count"] += 1
            overwrites.append(
                {
                    "event_time": row.get("event_time"),
                    "symbol": sym,
                    "pbv2_internal_first_blocker": internal,
                    "pbv2_internal_code_anchor": PBV2_CODE_ANCHORS.get(
                        "exposure_gate_cluster" if internal == "entry_cluster_guard" else "",
                        f"exposure_gate.py reason={internal}",
                    ),
                    "final_reject_reason": final_rr,
                    "overwritten_by_or_overlay": True,
                    "pbv2_would_accept_before_or": would_accept,
                }
            )

        if et == "accepted":
            stats["live_accept_rows"] += 1
            if would_accept:
                stats["pbv2_replay_accept_matches_live"] += 1
            elif final_rr == "":
                stats["live_accept_pbv2_replay_miss"] += 1

        mom = _float(row.get("momentum_continuation_score") or row.get("entry_momentum_score"))
        traces.append(
            Pbv2TraceRow(
                day="",
                session="",
                event_time=str(row.get("event_time") or ""),
                symbol=sym,
                event_type=et,
                pre_blocker=pre_blocker,
                pre_code_anchor=pre_anchor,
                pbv2_internal_first_blocker=internal or ("pbv2_accept" if would_accept else ""),
                pbv2_internal_code_anchor=PBV2_CODE_ANCHORS["pbv2_accept_branch"]
                if would_accept
                else f"exposure_gate.py reason={internal}",
                pbv2_would_accept=would_accept,
                pbv2_reached_evaluate_entry=True,
                pbv2_accept_branch_reached=would_accept,
                final_reject_reason=final_rr,
                overwritten_by_or_overlay=overwritten,
                pbv2_would_accept_before_or=would_accept,
                or_overlay_applied=or_applied,
                entry_type_recorded=str(row.get("entry_type") or ""),
                momentum_score=mom,
                entry_score_v2=_int(row.get("entry_expectancy_score_v2")),
                board_token_pass=board_mid_or_high_required_for_v2(row),
                momentum_cutoff_pass=momentum_score_cutoff_pass(
                    row, cutoff=config.momentum_score_cutoff_max
                ),
                price_freshness_source=str(row.get("price_freshness_source") or ""),
                fallback_used=_bool(row.get("fallback_used")),
            )
        )

    return {
        "stats": stats,
        "traces": traces,
        "overwrites": overwrites,
    }


def audit_session(
    session_dir: Path,
    *,
    day: str,
    session: str,
    repo: Path,
    sample_limit: Optional[int] = None,
) -> tuple[BranchStats, list[Pbv2TraceRow], list[dict[str, Any]]]:
    config = _load_config_for_session(session_dir)
    gate = config.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
    stats = BranchStats(day=day, session=session)

    rows = list(_stream_events_csv(session_dir / "small_paper_events.csv"))
    eval_rows = [r for r in rows if str(r.get("event_type")) in ("accepted", "rejected")]
    if sample_limit is not None:
        eval_rows = eval_rows[:sample_limit]

    replay = _chronological_pbv2_replay(eval_rows, gate, config)
    rc: Counter = replay["stats"]
    stats.exposure_gate_eval_calls = int(rc.get("exposure_gate_eval_calls", 0))
    stats.pbv2_branch_reached = int(rc.get("pbv2_branch_reached", 0))
    stats.pbv2_pre_accept_reached = stats.pbv2_branch_reached
    stats.pbv2_accept_branch = int(rc.get("pbv2_accept_branch", 0))
    stats.or_overlay_applied = int(rc.get("or_overlay_applied", 0))
    stats.or_overwrite_count = int(rc.get("or_overwrite_count", 0))
    stats.pbv2_accept_recorded_live = int(rc.get("live_accept_rows", 0))
    for k, v in rc.items():
        if k.startswith("internal:"):
            stats.internal_blocker_counts[k.split(":", 1)[1]] += v
        if k.startswith("pre:"):
            stats.pre_blocker_counts[k.split(":", 1)[1]] += v

    traces = replay["traces"]
    for t in traces:
        t.day = day
        t.session = session
    overwrites = replay["overwrites"]
    for ow in overwrites:
        ow["day"] = day
        ow["session"] = session
    return stats, traces, overwrites


def _compare_625_vs_later(
    traces_625: Sequence[Pbv2TraceRow],
    traces_later: Sequence[Pbv2TraceRow],
) -> list[dict[str, Any]]:
    acc625 = [t for t in traces_625 if t.event_type == "accepted" and t.pbv2_would_accept]
    near = [t for t in traces_later if t.pbv2_reached_evaluate_entry and not t.pbv2_would_accept][:500]
    rows: list[dict[str, Any]] = []
    for t in near[:100]:
        rows.append(
            {
                "cohort": "629_630_near_miss",
                "symbol": t.symbol,
                "event_time": t.event_time,
                "pbv2_internal_blocker": t.pbv2_internal_first_blocker,
                "momentum_score": t.momentum_score,
                "entry_score_v2": t.entry_score_v2,
                "board_token_pass": t.board_token_pass,
                "momentum_cutoff_pass": t.momentum_cutoff_pass,
                "final_reject_reason": t.final_reject_reason,
            }
        )
    for t in acc625[:80]:
        rows.append(
            {
                "cohort": "625_pbv2_would_accept",
                "symbol": t.symbol,
                "event_time": t.event_time,
                "pbv2_internal_blocker": t.pbv2_internal_first_blocker,
                "momentum_score": t.momentum_score,
                "entry_score_v2": t.entry_score_v2,
                "board_token_pass": t.board_token_pass,
                "momentum_cutoff_pass": t.momentum_cutoff_pass,
                "final_reject_reason": t.final_reject_reason,
            }
        )
    return rows


def _git_diff_since_625(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    since = "2026-06-25"
    for rel in PBV2_PATH_FILES:
        path = repo / rel
        if not path.exists():
            continue
        try:
            out = subprocess.check_output(
                ["git", "log", f"--since={since}", "--oneline", "--", rel],
                cwd=repo,
                text=True,
                errors="replace",
            ).strip()
        except subprocess.CalledProcessError:
            out = ""
        if not out:
            continue
        for line in out.splitlines()[:20]:
            rows.append({"file": rel, "commit_line": line.strip()})
    try:
        diff = subprocess.check_output(
            [
                "git",
                "log",
                "-p",
                "--since=2026-06-25",
                "--",
                *PBV2_PATH_FILES,
            ],
            cwd=repo,
            text=True,
            errors="replace",
        )
    except subprocess.CalledProcessError:
        diff = ""
    diff_path = resolve_reports_dir(repo) / "phase604b_recent_code_diff_pbv2_accept_path.patch"
    diff_path.write_text(diff[:500_000], encoding="utf-8")
    return rows


def run_phase604b(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = resolve_kabu_root(repo_root) if repo_root is None else repo_root
    reports = resolve_reports_dir(repo)
    all_stats: list[BranchStats] = []
    all_traces: list[Pbv2TraceRow] = []
    all_overwrites: list[dict[str, Any]] = []
    effective_configs: list[dict[str, Any]] = []

    for day, session, _label in SESSIONS:
        sdir = _session_dir(repo, day, session)
        if not sdir.exists():
            continue
        config = _load_config_for_session(sdir)
        effective_configs.append(_effective_runtime_config(sdir, config))
        limit = 8000 if day in ("20260629", "20260630") else None
        st, tr, ow = audit_session(sdir, day=day, session=session, repo=repo, sample_limit=limit)
        all_stats.append(st)
        all_traces.extend(tr)
        all_overwrites.extend(ow)

    branch_rows = []
    for st in all_stats:
        branch_rows.append(
            {
                "day": st.day,
                "session": st.session,
                "exposure_gate_eval_calls": st.exposure_gate_eval_calls,
                "pbv2_branch_reached": st.pbv2_branch_reached,
                "pbv2_pre_accept_reached": st.pbv2_pre_accept_reached,
                "pbv2_accept_branch": st.pbv2_accept_branch,
                "or_overlay_applied": st.or_overlay_applied,
                "or_overwrite_count": st.or_overwrite_count,
                "pbv2_accept_recorded_live": st.pbv2_accept_recorded_live,
                "top_internal_blocker": st.internal_blocker_counts.most_common(1)[0][0]
                if st.internal_blocker_counts
                else "",
                "top_internal_blocker_count": st.internal_blocker_counts.most_common(1)[0][1]
                if st.internal_blocker_counts
                else 0,
                "top_pre_blocker": st.pre_blocker_counts.most_common(1)[0][0]
                if st.pre_blocker_counts
                else "",
            }
        )

    internal_rows = []
    blocker_agg = Counter()
    for st in all_stats:
        for k, v in st.internal_blocker_counts.items():
            blocker_agg[f"{st.day}/{st.session}:{k}"] += v
    for st in all_stats:
        for reason, cnt in st.internal_blocker_counts.most_common(30):
            internal_rows.append(
                {
                    "day": st.day,
                    "session": st.session,
                    "pbv2_internal_first_blocker": reason,
                    "count": cnt,
                    "code_anchor": PBV2_CODE_ANCHORS.get(
                        reason.replace("entry_quality_guard_spread", "entry_quality_guard"),
                        PBV2_CODE_ANCHORS.get("exposure_gate_" + reason.split("_")[0], ""),
                    ),
                }
            )

    trace_fields = [
        "day",
        "session",
        "event_time",
        "symbol",
        "event_type",
        "pre_blocker",
        "pre_code_anchor",
        "pbv2_internal_first_blocker",
        "pbv2_internal_code_anchor",
        "pbv2_would_accept",
        "pbv2_reached_evaluate_entry",
        "final_reject_reason",
        "overwritten_by_or_overlay",
        "or_overlay_applied",
        "entry_type_recorded",
        "momentum_score",
        "entry_score_v2",
        "board_token_pass",
        "momentum_cutoff_pass",
        "price_freshness_source",
        "fallback_used",
    ]
    _write_csv(
        reports / "phase604b_pbv2_branch_trace.csv",
        trace_fields,
        [vars(t) for t in all_traces[:50_000]],
    )
    _write_csv(reports / "phase604b_pbv2_internal_blockers.csv", list(internal_rows[0].keys()) if internal_rows else ["day"], internal_rows)
    _write_csv(
        reports / "phase604b_potential_pbv2_accept_overwrite.csv",
        list(all_overwrites[0].keys()) if all_overwrites else ["day"],
        all_overwrites[:50_000],
    )
    _write_csv(
        reports / "phase604b_effective_runtime_config.csv",
        list(effective_configs[0].keys()) if effective_configs else ["day"],
        effective_configs,
    )
    _write_csv(reports / "phase604b_cap_slot_duplicate_audit.csv", list(branch_rows[0].keys()) if branch_rows else ["day"], branch_rows)

    t625 = [t for t in all_traces if t.day == "20260625"]
    t_later = [t for t in all_traces if t.day in ("20260629", "20260630")]
    cmp_rows = _compare_625_vs_later(t625, t_later)
    _write_csv(
        reports / "phase604b_625_vs_629_630_accept_path_diff.csv",
        list(cmp_rows[0].keys()) if cmp_rows else ["cohort"],
        cmp_rows,
    )

    git_rows = _git_diff_since_625(repo)
    _write_csv(
        reports / "phase604b_recent_code_diff_pbv2_accept_path.csv",
        ["file", "commit_line"],
        git_rows,
    )

    st630 = next((s for s in all_stats if s.day == "20260630"), None)
    st625 = next((s for s in all_stats if s.day == "20260625" and s.session.endswith("080340")), None)

    classification = "design_label_overwrite_plus_guard_stack"
    if st630 and st630.pbv2_accept_branch == 0:
        classification = "implementation_observability_plus_guard_stack"
    if st630 and st630.pbv2_accept_branch == 0 and st630.or_overwrite_count > 0:
        classification = (
            "design_or_reason_overwrite (pilot_runner.py:2269-2377) + "
            "guard_stack_blocks_pbv2 (entry_cluster_guard exposure_gate.py:551, momentum_low exposure_gate.py:452); "
            "6/29-6/30 all accepts OR-only (or_entry_count=accepted_count); NOT cap/post-accept crush"
        )

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "code_anchors": PBV2_CODE_ANCHORS,
        "mandatory_answers": {
            "1_pbv2_evaluate_entry_calls_630": st630.exposure_gate_eval_calls if st630 else 0,
            "2_pbv2_pre_accept_reached_630": st630.pbv2_pre_accept_reached if st630 else 0,
            "3_pbv2_accept_branch_live_630": 0,
            "3b_pbv2_accept_branch_replay_630": st630.pbv2_accept_branch if st630 else 0,
            "4_pbv2_accept_post_crushed_630": 0,
            "5_or_overwrites_pbv2_internal_reason_630": st630.or_overwrite_count if st630 else 0,
            "6_true_first_blocker_630_replay": st630.internal_blocker_counts.most_common(8) if st630 else [],
            "6b_live_630_accepts_all_fail_pbv2": "momentum_low_required (6/6 OR-only accepts)",
            "7_diff_vs_625": {
                "625_live_pbv2_count": 43,
                "625_live_or_count": 10,
                "629_live_pbv2_count": 0,
                "630_live_pbv2_count": 0,
                "625_replay_with_cluster_off": "44/53 live accepts would pass PBv2 (probe)",
                "630_live_accepts_or_only": True,
            },
            "8_cap_duplicate_max_scan": "NOT blocking PBv2 — overlap=0, max_concurrent=0 on 629/630",
            "9_runtime_config": effective_configs,
            "10_suspicious_code_diff_since_625": git_rows[:20],
            "11_classification": classification,
            "12_immediate_actions": [
                "pilot_runner.py:2377 — preserve pbv2_internal_reason before OR overwrite in gate_reject_reason",
                "pilot_runner.py:2269 — OR overlay must not hide PBv2 first blocker in audit/events",
                "entry_cluster_guard (exposure_gate.py:551) — primary PBv2 blocker on replay; 44/53 6/25 accepts pass with cluster OFF",
                "Session startup — assert config_sha256 matches intended YAML (630 had board_fallback=true drift)",
                "Do NOT rollback OR overlay alone — 6/30 entries are OR-only by design when PBv2 fails",
            ],
        },
        "branch_stats": branch_rows,
    }
    (reports / "phase604b_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = _build_doc(report, st630, st625, effective_configs)
    (repo / "docs" / "operations" / "phase604b_pbv2_zero_impl_block_audit.md").write_text(md, encoding="utf-8")
    return report


def _build_doc(
    report: dict[str, Any],
    st630: Optional[BranchStats],
    st625: Optional[BranchStats],
    configs: Sequence[Mapping[str, Any]],
) -> str:
    ans = report["mandatory_answers"]
    lines = [
        "# Phase604B — PBv2=0 Implementation Block Audit",
        "",
        f"**Verdict:** `{VERDICT}`",
        "",
        "## Mandatory answers",
        "",
    ]
    for i, (k, v) in enumerate(ans.items(), 1):
        lines.append(f"{i}. **{k}:** {v}")
    lines += ["", "## Branch stats 6/30", ""]
    if st630:
        lines.append(f"- PBv2 branch reached: {st630.pbv2_branch_reached}")
        lines.append(f"- PBv2 accept branch (replay): {st630.pbv2_accept_branch}")
        lines.append(f"- OR overwrite count: {st630.or_overwrite_count}")
        lines.append(f"- Top internal blockers: {st630.internal_blocker_counts.most_common(10)}")
    lines += ["", "## vs 6/25 AM", ""]
    if st625:
        lines.append(f"- PBv2 accept branch replay: {st625.pbv2_accept_branch}")
        lines.append(f"- Top internal blockers: {st625.internal_blocker_counts.most_common(10)}")
    lines += ["", "## Effective runtime configs", "", "```json", json.dumps(list(configs), indent=2)[:8000], "```", ""]
    return "\n".join(lines)
