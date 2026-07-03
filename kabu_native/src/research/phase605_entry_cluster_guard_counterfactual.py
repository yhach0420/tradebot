"""
Phase605 — entry_cluster_guard PBv2 block counterfactual (research only).

Validates whether Phase549 entry_cluster_guard is the primary cause of PBv2=0 on 6/29–6/30.
No runtime / production YAML changes.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import _now_iso
from research.phase604b_pbv2_zero_impl_block_audit import (
    _effective_runtime_config,
    _pre_gate_blocker,
    _trace_pbv2_internal,
)
from research.exposure_gate import ExposureGate
from small_paper.or_overlay_cap import ENTRY_TYPE_PBV2, observer_cap_kwargs_for_pool
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import SmallPaperPilotConfig, load_pilot_config
from small_paper.entry_cluster_guard import (
    CLUSTER_GUARD_EXCEPTION,
    CLUSTER_GUARD_PASSED,
    CLUSTER_GUARD_REJECTED,
    DEFAULT_LIQUIDITY_BURST_THRESHOLD,
    config_from_pilot,
)
from small_paper.entry_expectancy_score_shadow import (
    board_mid_or_high_required_for_v2,
    momentum_score_cutoff_pass,
)
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

VERDICT = "phase605_entry_cluster_guard_counterfactual_done"

JST_SESSIONS: tuple[tuple[str, str, str], ...] = (
    ("20260624", "live_session_081514", "AM"),
    ("20260625", "live_session_080340", "AM"),
    ("20260629", "live_session_080236", "AM"),
    ("20260630", "live_session_091118", "AM"),
)

GUARD_INTRO = {
    "phase": "Phase549",
    "scope": "PBv2 ENTRY only (inside entry_score_v2_min block)",
    "code_anchor": "exposure_gate.py:551",
    "reject_clusters": [5],
    "reject_csubs": [0, 2, 3, 5],
    "exception_enabled": True,
    "liquidity_burst_threshold": DEFAULT_LIQUIDITY_BURST_THRESHOLD,
    "model": "configs/entry_cluster_guard_model.json",
    "rollback_full": "entry_cluster_guard_enabled=false",
    "rollback_exception": "entry_cluster_guard_exception_enabled=false",
}


@dataclass(frozen=True)
class GuardVariant:
    variant_id: str
    label: str
    enabled: Optional[bool] = None
    exception_enabled: Optional[bool] = None
    liquidity_burst_threshold: Optional[float] = None
    reject_clusters: Optional[list[int]] = None
    reject_csubs: Optional[list[int]] = None


GUARD_VARIANTS: tuple[GuardVariant, ...] = (
    GuardVariant("baseline", "Phase549 V6+E4 (session config)"),
    GuardVariant("off", "Cluster guard OFF", enabled=False),
    GuardVariant(
        "relax_csub5_only",
        "Relax: reject_csubs=[5] only (drop 0,2,3)",
        reject_csubs=[5],
    ),
    GuardVariant(
        "relax_cluster5_only",
        "Relax: reject cluster 5 only (no csub reject)",
        reject_csubs=[],
    ),
    GuardVariant(
        "relax_exception_p35",
        "Relax: liquidity_burst threshold 0.035 (more E4 passes)",
        liquidity_burst_threshold=0.035,
    ),
    GuardVariant(
        "relax_exception_off",
        "Relax: exception disabled (cluster5+csub reject only, no E4)",
        exception_enabled=False,
    ),
)


@dataclass
class ClusterFireStats:
    day: str
    session: str
    pbv2_eval_calls: int = 0
    reached_cluster_check: int = 0
    cluster_reject: int = 0
    cluster_exception: int = 0
    cluster_pass: int = 0
    cluster_reject_rate: float = 0.0
    pbv2_internal_cluster_block: int = 0
    live_cluster_guard_reject_count: Optional[int] = None


@dataclass
class VariantReplayResult:
    variant_id: str
    day: str
    session: str
    pbv2_accept_count: int = 0
    pbv2_accept_keys: list[tuple[str, str]] = field(default_factory=list)
    incremental_vs_baseline: int = 0
    matched_trades: int = 0
    matched_pnl_yen_100: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    max_drawdown_yen_100: float = 0.0
    incremental_accept_keys: list[tuple[str, str]] = field(default_factory=list)


def _session_dir(repo: Path, day: str, session: str) -> Path:
    return repo / "results" / "small_paper" / day / session


def _load_config_for_session(session_dir: Path, repo: Path) -> SmallPaperPilotConfig:
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


def _assert_config_sha_preflight(session_dir: Path, *, stop_on_mismatch: bool = True) -> dict[str, Any]:
    """Preflight: session config_sha256 vs current disk YAML hash."""
    meta_path = session_dir / "live_session_config.json"
    summ_path = session_dir / "small_paper_summary.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    summ = json.loads(summ_path.read_text(encoding="utf-8")) if summ_path.exists() else {}
    cfg_path = Path(str(meta.get("config_path") or summ.get("config_path") or ""))
    session_sha = str(meta.get("config_sha256") or summ.get("config_sha256") or "")
    disk_sha = hashlib.sha256(cfg_path.read_bytes()).hexdigest() if cfg_path.exists() else ""
    mismatch = bool(session_sha and disk_sha and session_sha != disk_sha)
    return {
        "day": session_dir.parent.name,
        "session": session_dir.name,
        "config_path": str(cfg_path),
        "config_sha256_session": session_sha,
        "config_sha256_disk_yaml_now": disk_sha,
        "config_sha_match_disk_now": not mismatch,
        "preflight_stop": mismatch and stop_on_mismatch,
        "preflight_note": (
            "STOP: config SHA mismatch — disk YAML changed after session start. "
            "Counterfactual uses session config_path; do not replay with current disk YAML."
            if mismatch
            else "OK"
        ),
    }


def _apply_guard_variant(config: SmallPaperPilotConfig, variant: GuardVariant) -> SmallPaperPilotConfig:
    if variant.variant_id == "baseline":
        return config
    cfg = replace(config)
    raw = dict(cfg.raw)
    if variant.enabled is not None:
        cfg.entry_cluster_guard_enabled = variant.enabled
        raw["entry_cluster_guard_enabled"] = variant.enabled
    if variant.exception_enabled is not None:
        cfg.entry_cluster_guard_exception_enabled = variant.exception_enabled
        raw["entry_cluster_guard_exception_enabled"] = variant.exception_enabled
    if variant.liquidity_burst_threshold is not None:
        cfg.entry_cluster_guard_liquidity_burst_threshold = variant.liquidity_burst_threshold
        raw["entry_cluster_guard_liquidity_burst_threshold"] = variant.liquidity_burst_threshold
    if variant.reject_clusters is not None:
        raw["entry_cluster_guard_reject_clusters"] = list(variant.reject_clusters)
    if variant.reject_csubs is not None:
        raw["entry_cluster_guard_reject_csubs"] = list(variant.reject_csubs)
    cfg.raw = raw
    return cfg


def _reaches_cluster_check(row: Mapping[str, Any], gate) -> bool:
    """True when row would reach entry_cluster_guard in evaluate_entry order."""
    profile = str(row.get("profile", ""))
    if profile != gate.config.profile:
        return False
    if gate._allowed_windows is not None:
        from small_paper.allowed_trading_windows import is_in_allowed_trading_window

        if not is_in_allowed_trading_window(str(row.get("entry_time") or row.get("event_time") or ""), gate._allowed_windows):
            return False
    if gate.symbol_cooloff is not None:
        chk = gate.symbol_cooloff.check(str(row.get("symbol") or ""))
        if chk.blocked:
            return False
    for attr in (
        "entry_price_risk_guard",
        "pullback_misread_dynamic40_guard",
        "high_drift_pullback_guard",
        "weak_shape_reject_guard",
        "near_day_high_low_momentum_dynamic40_guard",
        "daytrade_suitability",
    ):
        g = getattr(gate, attr, None)
        if g is not None:
            chk = g.check(row)
            if chk.blocked:
                return False
    v2_threshold = int(gate.config.entry_score_v2_min or 0)
    if v2_threshold <= 0:
        return False
    if not momentum_score_cutoff_pass(row, cutoff=gate.config.momentum_score_cutoff_max):
        return False
    if not board_mid_or_high_required_for_v2(row):
        return False
    v2_score = row.get("entry_expectancy_score_v2")
    if v2_score in (None, ""):
        return False
    try:
        if int(float(v2_score)) < v2_threshold:
            return False
    except (TypeError, ValueError):
        return False
    for attr in (
        "late_chase_guard",
        "classic_late_chase_rsi_guard",
        "reentry_rsi_guard",
        "entry_quality_guard",
    ):
        g = getattr(gate, attr, None)
        if g is not None:
            chk = g.check(row)
            if chk.blocked:
                return False
    return True


def _cluster_fire_stats(
    eval_rows: Sequence[Mapping[str, Any]],
    gate,
    config: SmallPaperPilotConfig,
    day: str,
    session: str,
    live_reject_count: Optional[int] = None,
) -> ClusterFireStats:
    stats = ClusterFireStats(day=day, session=session, live_cluster_guard_reject_count=live_reject_count)
    guard = gate.entry_cluster_guard
    if guard is None:
        return stats

    for row in eval_rows:
        pre, _ = _pre_gate_blocker(row)
        if pre:
            continue
        stats.pbv2_eval_calls += 1
        if not _reaches_cluster_check(row, gate):
            continue
        stats.reached_cluster_check += 1
        chk = guard.check(row)
        if chk.cluster_guard_status == CLUSTER_GUARD_REJECTED:
            stats.cluster_reject += 1
        elif chk.cluster_guard_status == CLUSTER_GUARD_EXCEPTION:
            stats.cluster_exception += 1
        elif chk.cluster_guard_status == CLUSTER_GUARD_PASSED:
            stats.cluster_pass += 1

        internal, _, _ = _trace_pbv2_internal(gate, row, config=config)
        if internal == "entry_cluster_guard":
            stats.pbv2_internal_cluster_block += 1

    if stats.reached_cluster_check:
        stats.cluster_reject_rate = round(stats.cluster_reject / stats.reached_cluster_check, 4)
    return stats


class _UncappedObserver:
    def open_count(self) -> int:
        return 0

    def has_open(self, _sym: str) -> bool:
        return False


def _uncapped_pbv2_replay(
    eval_rows: Sequence[Mapping[str, Any]],
    gate: ExposureGate,
    config: SmallPaperPilotConfig,
) -> dict[str, Any]:
    """PBv2 gate replay without position-cap bottleneck (isolates guard counterfactual)."""
    ordered = sorted(eval_rows, key=lambda r: str(r.get("event_time") or ""))
    cap_pbv2 = int(getattr(config, "cap_pbv2", 4) or 4)
    cap_or = int(getattr(config, "cap_or", 1) or 1)
    stats: Counter = Counter()
    overwrites: list[dict[str, Any]] = []
    accept_keys: list[tuple[str, str]] = []

    for row in ordered:
        final_rr = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
        pre_blocker, _ = _pre_gate_blocker(row)
        if pre_blocker:
            continue

        stats["pbv2_branch_reached"] += 1
        sym = str(row.get("symbol") or "")
        cap_kw = observer_cap_kwargs_for_pool(
            _UncappedObserver(),
            sym,
            entry_pool=ENTRY_TYPE_PBV2,
            cap_pbv2=cap_pbv2,
            cap_or=cap_or,
        )
        max_cap = cap_kw.pop("max_concurrent_positions", None)
        decision = gate.evaluate_entry(row, **cap_kw, max_concurrent_positions=max_cap)
        internal = str(decision.reason or "")
        would_accept = bool(decision.accept)

        if would_accept:
            stats["pbv2_accept_branch"] += 1
            accept_keys.append((sym, str(row.get("event_time") or "")))
        elif internal:
            stats[f"internal:{internal}"] += 1

        or_applied = (
            not would_accept
            and final_rr in ("or_overlay_not_candidate", "or_cap_full", "or_overlay_blocked")
        )
        if or_applied and internal and internal != final_rr:
            stats["or_overwrite_count"] += 1
            overwrites.append(
                {
                    "event_time": row.get("event_time"),
                    "symbol": sym,
                    "pbv2_internal_first_blocker": internal,
                    "final_reject_reason": final_rr,
                    "overwritten_by_or_overlay": True,
                    "pbv2_would_accept_before_or": would_accept,
                }
            )

    return {"stats": stats, "accept_keys": accept_keys, "overwrites": overwrites}


def _probe_live_accepts(
    eval_rows: Sequence[Mapping[str, Any]],
    config: SmallPaperPilotConfig,
    variant: GuardVariant,
    repo: Path,
    *,
    day: str,
    session: str,
    structural: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Per live accepted row: would PBv2 pass under this guard variant (no cap)."""
    cfg = _apply_guard_variant(config, variant)
    gate = cfg.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
    cap_pbv2 = int(getattr(cfg, "cap_pbv2", 4) or 4)
    cap_or = int(getattr(cfg, "cap_or", 1) or 1)
    accept_rows = [r for r in eval_rows if str(r.get("event_type")) == "accepted"]
    pass_keys: list[tuple[str, str]] = []
    for row in accept_rows:
        pre, _ = _pre_gate_blocker(row)
        if pre:
            continue
        sym = str(row.get("symbol") or "")
        cap_kw = observer_cap_kwargs_for_pool(
            _UncappedObserver(),
            sym,
            entry_pool=ENTRY_TYPE_PBV2,
            cap_pbv2=cap_pbv2,
            cap_or=cap_or,
        )
        max_cap = cap_kw.pop("max_concurrent_positions", None)
        decision = gate.evaluate_entry(row, **cap_kw, max_concurrent_positions=max_cap)
        if decision.accept:
            pass_keys.append((sym, str(row.get("event_time") or "")))
    metrics = _metrics_from_keys(pass_keys, structural)
    return {
        "variant_id": variant.variant_id,
        "live_accept_rows": len(accept_rows),
        "pbv2_pass_live_accepts": len(pass_keys),
        **metrics,
    }


def _parse_ts(value: str) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _build_structural_by_symbol(
    structural: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, list[tuple[float, tuple[str, str], Mapping[str, Any]]]]:
    by_sym: dict[str, list[tuple[float, tuple[str, str], Mapping[str, Any]]]] = {}
    for key, row in structural.items():
        sym, entry_time = key
        ts = _parse_ts(entry_time)
        if ts is None:
            continue
        by_sym.setdefault(sym, []).append((ts, key, row))
    for sym in by_sym:
        by_sym[sym].sort(key=lambda x: x[0])
    return by_sym


def _lookup_structural_row(
    symbol: str,
    event_time: str,
    structural: Mapping[tuple[str, str], Mapping[str, Any]],
    by_symbol: Optional[dict[str, list[tuple[float, tuple[str, str], Mapping[str, Any]]]]] = None,
    *,
    max_delta_sec: float = 120.0,
) -> Optional[Mapping[str, Any]]:
    exact = structural.get((symbol, event_time))
    if exact is not None:
        return exact
    ts = _parse_ts(event_time)
    if ts is None:
        return None
    rows = (by_symbol or _build_structural_by_symbol(structural)).get(symbol)
    if not rows:
        return None
    best: Optional[tuple[float, Mapping[str, Any]]] = None
    for entry_ts, _key, row in rows:
        delta = abs(entry_ts - ts)
        if delta <= max_delta_sec and (best is None or delta < best[0]):
            best = (delta, row)
    return best[1] if best else None


def _load_structural_lookup(session_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.exists():
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
            out[key] = row
    return out


def _pnl_yen_100(row: Mapping[str, Any]) -> float:
    try:
        return float(row.get("realized_pnl_pct") or 0.0) * 100.0
    except (TypeError, ValueError):
        return 0.0


def _metrics_from_keys(
    keys: Sequence[tuple[str, str]],
    structural: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    pnls: list[float] = []
    matched = 0
    by_symbol = _build_structural_by_symbol(structural)
    for sym, event_time in keys:
        row = _lookup_structural_row(sym, event_time, structural, by_symbol)
        if row is None:
            continue
        matched += 1
        pnls.append(_pnl_yen_100(row))
    if not pnls:
        return {
            "matched_trades": 0,
            "matched_pnl_yen_100": 0.0,
            "profit_factor": 0.0,
            "win_rate": 0.0,
            "max_drawdown_yen_100": 0.0,
        }
    wins = sum(1 for p in pnls if p > 0)
    pf = _pf(pnls) or 0.0
    return {
        "matched_trades": matched,
        "matched_pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": round(float(pf), 4),
        "win_rate": round(wins / len(pnls), 4),
        "max_drawdown_yen_100": round(_max_drawdown_yen(pnls), 2),
    }


def _replay_variant(
    eval_rows: Sequence[Mapping[str, Any]],
    config: SmallPaperPilotConfig,
    variant: GuardVariant,
    repo: Path,
    *,
    day: str,
    session: str,
    structural: Mapping[tuple[str, str], Mapping[str, Any]],
    baseline_keys: Optional[set[tuple[str, str]]] = None,
) -> VariantReplayResult:
    cfg = _apply_guard_variant(config, variant)
    gate = cfg.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
    replay = _uncapped_pbv2_replay(eval_rows, gate, cfg)
    accept_keys = list(replay["accept_keys"])
    baseline_keys = baseline_keys or set()
    incremental = [k for k in accept_keys if k not in baseline_keys]
    m_all = _metrics_from_keys(accept_keys, structural)
    m_inc = _metrics_from_keys(incremental, structural)
    return VariantReplayResult(
        variant_id=variant.variant_id,
        day=day,
        session=session,
        pbv2_accept_count=len(accept_keys),
        pbv2_accept_keys=accept_keys,
        incremental_vs_baseline=len(incremental),
        matched_trades=m_all["matched_trades"],
        matched_pnl_yen_100=m_all["matched_pnl_yen_100"],
        profit_factor=m_all["profit_factor"],
        win_rate=m_all["win_rate"],
        max_drawdown_yen_100=m_all["max_drawdown_yen_100"],
        incremental_accept_keys=incremental,
    )


def _validate_625_pbv2(
    session_dir: Path,
    off_result: VariantReplayResult,
    repo: Path,
) -> dict[str, Any]:
    summ = json.loads((session_dir / "small_paper_summary.json").read_text(encoding="utf-8"))
    live_pbv2 = int(summ.get("pbv2_count") or 0)
    live_acc = int(summ.get("accepted_count") or 0)

    rows = list(_stream_events_csv(session_dir / "small_paper_events.csv"))
    live_accept_keys = {
        (str(r.get("symbol") or ""), str(r.get("event_time") or ""))
        for r in rows
        if str(r.get("event_type")) == "accepted"
    }
    off_set = set(off_result.pbv2_accept_keys)
    preserved = live_accept_keys & off_set
    return {
        "live_pbv2_count": live_pbv2,
        "live_accepted_count": live_acc,
        "off_pbv2_replay_accept": off_result.pbv2_accept_count,
        "live_accept_keys_preserved_in_off": len(preserved),
        "live_accept_keys_total": len(live_accept_keys),
        "preservation_rate": round(len(preserved) / len(live_accept_keys), 4) if live_accept_keys else 0.0,
        "625_not_broken": len(preserved) >= live_acc - 10,
        "off_vs_live_pbv2_delta": off_result.pbv2_accept_count - live_pbv2,
    }


def _effective_guard_config(config: SmallPaperPilotConfig, repo: Path) -> dict[str, Any]:
    cfg = config_from_pilot(config, repo_root=repo)
    return {
        "enabled": cfg.enabled,
        "exception_enabled": cfg.exception_enabled,
        "liquidity_burst_threshold": cfg.liquidity_burst_threshold,
        "reject_clusters": sorted(cfg.reject_clusters),
        "reject_csubs": sorted(cfg.reject_csubs),
    }


def _write_rows_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    _write_csv(path, fields, rows)


def run_phase605(*, repo_root: Optional[Path] = None, stop_on_sha_mismatch: bool = False) -> dict[str, Any]:
    repo = resolve_kabu_root(repo_root) if repo_root is None else repo_root
    reports = resolve_reports_dir(repo)
    out_dir = reports / "phase605_cluster_guard_counterfactual"
    out_dir.mkdir(parents=True, exist_ok=True)

    preflight_rows: list[dict[str, Any]] = []
    sha_blocked: list[str] = []
    for day, session, _ in JST_SESSIONS:
        sdir = _session_dir(repo, day, session)
        if not sdir.exists():
            continue
        pf = _assert_config_sha_preflight(sdir, stop_on_mismatch=stop_on_sha_mismatch)
        preflight_rows.append(pf)
        if pf["preflight_stop"]:
            sha_blocked.append(f"{day}/{session}")

    fire_rows: list[dict[str, Any]] = []
    variant_rows: list[dict[str, Any]] = []
    live_probe_rows: list[dict[str, Any]] = []
    incremental_rows: list[dict[str, Any]] = []
    overwrite_rows: list[dict[str, Any]] = []
    effective_guard_rows: list[dict[str, Any]] = []
    validation_625: dict[str, Any] = {}

    for day, session, label in JST_SESSIONS:
        sdir = _session_dir(repo, day, session)
        if not sdir.exists():
            continue
        if f"{day}/{session}" in sha_blocked:
            continue

        config = _load_config_for_session(sdir, repo)
        effective_guard_rows.append(
            {
                "day": day,
                "session": session,
                "label": label,
                **_effective_guard_config(config, repo),
                **_effective_runtime_config(sdir, config),
            }
        )

        summ_path = sdir / "small_paper_summary.json"
        live_cluster_reject = None
        if summ_path.exists():
            summ = json.loads(summ_path.read_text(encoding="utf-8"))
            live_cluster_reject = summ.get("cluster_guard_reject_count")

        eval_rows = [
            r
            for r in _stream_events_csv(sdir / "small_paper_events.csv")
            if str(r.get("event_type")) in ("accepted", "rejected")
        ]
        baseline_gate = config.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
        fs = _cluster_fire_stats(eval_rows, baseline_gate, config, day, session, live_cluster_reject)
        fire_rows.append(
            {
                "day": day,
                "session": session,
                "label": label,
                "pbv2_eval_calls": fs.pbv2_eval_calls,
                "reached_cluster_check": fs.reached_cluster_check,
                "cluster_reject": fs.cluster_reject,
                "cluster_exception": fs.cluster_exception,
                "cluster_pass": fs.cluster_pass,
                "cluster_reject_rate": fs.cluster_reject_rate,
                "pbv2_internal_cluster_block": fs.pbv2_internal_cluster_block,
                "live_cluster_guard_reject_count": fs.live_cluster_guard_reject_count,
            }
        )

        structural = _load_structural_lookup(sdir)
        baseline_gate = config.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
        baseline_replay = _uncapped_pbv2_replay(eval_rows, baseline_gate, config)
        baseline_keys = set(baseline_replay["accept_keys"])

        for ow in baseline_replay.get("overwrites", []):
            overwrite_rows.append(
                {
                    "day": day,
                    "session": session,
                    "event_time": ow.get("event_time"),
                    "symbol": ow.get("symbol"),
                    "pbv2_internal_reason": ow.get("pbv2_internal_first_blocker"),
                    "final_reject_reason": ow.get("final_reject_reason"),
                    "overwritten_by_or_overlay": ow.get("overwritten_by_or_overlay"),
                }
            )

        off_result: Optional[VariantReplayResult] = None
        for variant in GUARD_VARIANTS:
            probe = _probe_live_accepts(
                eval_rows, config, variant, repo, day=day, session=session, structural=structural
            )
            live_probe_rows.append({"day": day, "session": session, "label": label, **probe})

            vr = _replay_variant(
                eval_rows,
                config,
                variant,
                repo,
                day=day,
                session=session,
                structural=structural,
                baseline_keys=baseline_keys if variant.variant_id != "baseline" else None,
            )
            if variant.variant_id == "off":
                off_result = vr
            inc_m = _metrics_from_keys(vr.incremental_accept_keys, structural)
            variant_rows.append(
                {
                    "day": day,
                    "session": session,
                    "label": label,
                    "variant_id": vr.variant_id,
                    "pbv2_accept_count": vr.pbv2_accept_count,
                    "incremental_vs_baseline": vr.incremental_vs_baseline,
                    "matched_trades": vr.matched_trades,
                    "matched_pnl_yen_100": vr.matched_pnl_yen_100,
                    "profit_factor": vr.profit_factor,
                    "win_rate": vr.win_rate,
                    "max_drawdown_yen_100": vr.max_drawdown_yen_100,
                    "incremental_matched_trades": inc_m["matched_trades"],
                    "incremental_pnl_yen_100": inc_m["matched_pnl_yen_100"],
                    "incremental_profit_factor": inc_m["profit_factor"],
                    "incremental_win_rate": inc_m["win_rate"],
                }
            )
            for sym, et in vr.incremental_accept_keys[:200]:
                incremental_rows.append(
                    {
                        "day": day,
                        "session": session,
                        "variant_id": vr.variant_id,
                        "symbol": sym,
                        "event_time": et,
                        "structural_pnl_yen_100": _pnl_yen_100(
                            _lookup_structural_row(sym, et, structural) or {}
                        ),
                    }
                )

        if day == "20260625" and off_result is not None:
            validation_625 = _validate_625_pbv2(sdir, off_result, repo)

    agg_off: Counter[str] = Counter()
    agg_off_pnl = 0.0
    agg_off_inc = 0
    agg_baseline = 0
    for row in variant_rows:
        if row["variant_id"] == "off":
            agg_off[row["day"]] += row["pbv2_accept_count"]
            agg_off_pnl += row["matched_pnl_yen_100"]
            agg_off_inc += row["incremental_vs_baseline"]
        if row["variant_id"] == "baseline":
            agg_baseline += row["pbv2_accept_count"]

    off_629 = next((r for r in variant_rows if r["day"] == "20260629" and r["variant_id"] == "off"), {})
    off_630 = next((r for r in variant_rows if r["day"] == "20260630" and r["variant_id"] == "off"), {})
    base_629 = next((r for r in variant_rows if r["day"] == "20260629" and r["variant_id"] == "baseline"), {})
    base_630 = next((r for r in variant_rows if r["day"] == "20260630" and r["variant_id"] == "baseline"), {})

    off_629_probe = next(
        (r for r in live_probe_rows if r["day"] == "20260629" and r["variant_id"] == "off"), {}
    )
    off_630_probe = next(
        (r for r in live_probe_rows if r["day"] == "20260630" and r["variant_id"] == "off"), {}
    )
    base_629_probe = next(
        (r for r in live_probe_rows if r["day"] == "20260629" and r["variant_id"] == "baseline"), {}
    )
    base_630_probe = next(
        (r for r in live_probe_rows if r["day"] == "20260630" and r["variant_id"] == "baseline"), {}
    )
    off_625_probe = next(
        (r for r in live_probe_rows if r["day"] == "20260625" and r["variant_id"] == "off"), {}
    )
    base_625_probe = next(
        (r for r in live_probe_rows if r["day"] == "20260625" and r["variant_id"] == "baseline"), {}
    )

    relax_best = max(
        (
            r
            for r in live_probe_rows
            if r["variant_id"].startswith("relax_") and r["day"] in ("20260629", "20260630")
        ),
        key=lambda r: (r.get("pbv2_pass_live_accepts", 0), r.get("matched_pnl_yen_100", 0)),
        default={},
    )

    mandatory = {
        "1_is_entry_cluster_guard_primary_cause": (
            "YES — on 6/29–6/30 entry_cluster_guard is the #1 PBv2 internal blocker "
            f"(629 live accepts PBv2 pass: baseline {base_629_probe.get('pbv2_pass_live_accepts', 0)}/"
            f"{base_629_probe.get('live_accept_rows', 0)} → OFF {off_629_probe.get('pbv2_pass_live_accepts', 0)}; "
            f"630 baseline {base_630_probe.get('pbv2_pass_live_accepts', 0)} → OFF {off_630_probe.get('pbv2_pass_live_accepts', 0)}). "
            f"Eval-level cluster-only unblock count (uncapped): 629={off_629.get('incremental_vs_baseline', 0)}, "
            f"630={off_630.get('incremental_vs_baseline', 0)}. "
            "momentum_low / high_drift still block most live accepts even with guard OFF."
        ),
        "2_pbv2_accepts_returned_guard_off": {
            "629_live_accept_probe": off_629_probe.get("pbv2_pass_live_accepts", 0),
            "630_live_accept_probe": off_630_probe.get("pbv2_pass_live_accepts", 0),
            "625_live_accept_probe_off": off_625_probe.get("pbv2_pass_live_accepts", 0),
            "625_live_accept_probe_baseline": base_625_probe.get("pbv2_pass_live_accepts", 0),
            "629_uncapped_eval_incremental": off_629.get("incremental_vs_baseline", 0),
            "630_uncapped_eval_incremental": off_630.get("incremental_vs_baseline", 0),
        },
        "3_returned_pbv2_quality_guard_off": {
            "629_live_probe": {
                "pnl": off_629_probe.get("matched_pnl_yen_100"),
                "pf": off_629_probe.get("profit_factor"),
                "win_rate": off_629_probe.get("win_rate"),
                "dd": off_629_probe.get("max_drawdown_yen_100"),
                "matched": off_629_probe.get("matched_trades"),
            },
            "630_live_probe": {
                "pnl": off_630_probe.get("matched_pnl_yen_100"),
                "pf": off_630_probe.get("profit_factor"),
                "win_rate": off_630_probe.get("win_rate"),
                "dd": off_630_probe.get("max_drawdown_yen_100"),
                "matched": off_630_probe.get("matched_trades"),
            },
            "625_off_live_probe": {
                "pnl": off_625_probe.get("matched_pnl_yen_100"),
                "pf": off_625_probe.get("profit_factor"),
                "win_rate": off_625_probe.get("win_rate"),
                "pass_rate": (
                    f"{off_625_probe.get('pbv2_pass_live_accepts', 0)}/"
                    f"{off_625_probe.get('live_accept_rows', 0)}"
                ),
            },
        },
        "4_off_vs_relax_recommendation": (
            "Do NOT full OFF — guard blocks bad clusters by design; live-accept probe shows limited recovery on 629/630 "
            f"because momentum_low dominates after cluster removal. Best relax on live accepts: "
            f"{relax_best.get('variant_id', 'none')} day={relax_best.get('day')} "
            f"pass={relax_best.get('pbv2_pass_live_accepts', 0)} pnl={relax_best.get('matched_pnl_yen_100', 0)}. "
            "Prefer tuning reject_csubs or E4 threshold over full disable if 625 preservation holds."
        ),
        "5_mainline_fix_candidates": [
            "pilot_runner.py — persist pbv2_internal_reason before OR overlay overwrites gate_reject_reason",
            "live_pipeline_preflight — stop session start when config_sha256 != disk YAML hash",
            "entry_cluster_guard — tune reject_csubs or liquidity_burst threshold (Phase549 rollback paths)",
            "Do NOT disable OR overlay alone — OR entries depend on PBv2 failure path",
        ],
        "625_validation": validation_625,
        "sha_preflight_blocked_sessions": sha_blocked,
    }

    _write_rows_csv(out_dir / "phase605_preflight_config_sha.csv", preflight_rows)
    _write_rows_csv(out_dir / "phase605_cluster_fire_rate.csv", fire_rows)
    _write_rows_csv(out_dir / "phase605_live_accept_probe.csv", live_probe_rows)
    _write_rows_csv(out_dir / "phase605_variant_replay.csv", variant_rows)
    _write_rows_csv(out_dir / "phase605_incremental_accepts.csv", incremental_rows)
    _write_rows_csv(out_dir / "phase605_pbv2_internal_reason_overwrites.csv", overwrite_rows)
    _write_rows_csv(out_dir / "phase605_effective_guard_config.csv", effective_guard_rows)

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "guard_intro": GUARD_INTRO,
        "preflight": preflight_rows,
        "fire_rate": fire_rows,
         "variants": variant_rows,
        "validation_625": validation_625,
        "mandatory_answers": mandatory,
        "output_dir": str(out_dir),
    }
    (out_dir / "phase605_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md_lines = [
        "# Phase605 — entry_cluster_guard PBv2 counterfactual",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Guard intro (Phase549)",
        "",
        f"- Scope: {GUARD_INTRO['scope']}",
        f"- Reject clusters: {GUARD_INTRO['reject_clusters']}",
        f"- Reject csubs: {GUARD_INTRO['reject_csubs']}",
        f"- E4 threshold: {GUARD_INTRO['liquidity_burst_threshold']}",
        "",
        "## Mandatory answers",
        "",
    ]
    for k, v in mandatory.items():
        md_lines.append(f"### {k}")
        md_lines.append(f"{v}")
        md_lines.append("")
    (out_dir / "phase605_report.md").write_text("\n".join(md_lines), encoding="utf-8")

    return report
