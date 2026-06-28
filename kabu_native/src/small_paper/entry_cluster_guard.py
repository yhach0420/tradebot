"""
Phase549: Production ENTRY cluster guard — V6 Balanced Reject + E4 Liquidity Burst exception.

PBv2 only. OR overlay unaffected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from small_paper.entry_cluster_classifier import (
    EntryClusterModel,
    compute_entry_cluster_feature_fields,
    load_default_model,
    resolve_entry_cluster_guard_model_path,
)

REJECT_ENTRY_CLUSTER_GUARD = "entry_cluster_guard"
REJECT_ENTRY_CLUSTER_GUARD_DEBUG = "entry_cluster_guard_debug"
LOG_EVENT_KIND = "entry_cluster_guard_triggered"
CLUSTER_GUARD_PASSED = "PASSED"
CLUSTER_GUARD_EXCEPTION = "EXCEPTION"
CLUSTER_GUARD_REJECTED = "REJECTED"

DEFAULT_LIQUIDITY_BURST_THRESHOLD = 0.052267
BIG_WINNER_MFE_PCT = 1.0


def _float(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class EntryClusterGuardConfig:
    enabled: bool = False
    exception_enabled: bool = True
    liquidity_burst_threshold: float = DEFAULT_LIQUIDITY_BURST_THRESHOLD
    reject_clusters: frozenset[int] = frozenset({5})
    reject_csubs: frozenset[int] = frozenset({0, 2, 3, 5})
    model_path: Optional[Path] = None


@dataclass
class EntryClusterGuardCheck:
    blocked: bool
    cluster_guard_status: str = CLUSTER_GUARD_PASSED
    cluster_id: int = -1
    subcluster_id: int = -1
    new_subcluster_id: int = -1
    liquidity_burst: Optional[float] = None
    liquidity_burst_threshold: Optional[float] = None
    reject_reason: str = ""
    via_exception: bool = False

    def log_fields(self, *, symbol: str) -> dict[str, Any]:
        return {
            "event_kind": LOG_EVENT_KIND,
            "symbol": symbol,
            "cluster_id": self.cluster_id,
            "subcluster_id": self.subcluster_id,
            "new_subcluster_id": self.new_subcluster_id,
            "liquidity_burst": self.liquidity_burst,
            "liquidity_burst_threshold": self.liquidity_burst_threshold,
            "cluster_guard_status": self.cluster_guard_status,
            "via_exception": self.via_exception,
            "reject_reason": self.reject_reason or REJECT_ENTRY_CLUSTER_GUARD,
        }


@dataclass
class EntryClusterGuardState:
    config: EntryClusterGuardConfig
    model: EntryClusterModel
    reject_count: int = 0
    exception_count: int = 0
    rejected_symbols: set[str] = field(default_factory=set)
    exception_symbols: set[str] = field(default_factory=set)
    blocked_cluster_counts: dict[str, int] = field(default_factory=dict)
    exception_pnls: list[float] = field(default_factory=list)
    exception_wins: int = 0
    exception_mfe0: int = 0
    exception_big_winner: int = 0

    def summary_fields(self) -> dict[str, Any]:
        exc_n = len(self.exception_pnls)
        exc_pnl = round(sum(self.exception_pnls), 2) if self.exception_pnls else 0.0
        wins = self.exception_wins
        pf = 0.0
        if exc_n:
            gp = sum(p for p in self.exception_pnls if p > 0)
            gl = abs(sum(p for p in self.exception_pnls if p < 0))
            pf = round(gp / gl, 4) if gl > 0 else (999.0 if gp > 0 else 0.0)
        return {
            "entry_cluster_guard_enabled": self.config.enabled,
            "entry_cluster_guard_exception_enabled": self.config.exception_enabled,
            "entry_cluster_guard_liquidity_burst_threshold": self.config.liquidity_burst_threshold,
            "cluster_guard_reject_count": self.reject_count,
            "cluster_guard_exception_count": self.exception_count,
            "cluster_guard_rejected_pnl": 0.0,
            "cluster_guard_exception_pnl": exc_pnl,
            "cluster_guard_exception_win_rate": round(wins / exc_n, 4) if exc_n else 0.0,
            "cluster_guard_exception_pf": pf,
            "cluster_guard_exception_big_winner": self.exception_big_winner,
            "cluster_guard_exception_mfe0": self.exception_mfe0,
            "cluster_guard_blocked_cluster_counts": dict(self.blocked_cluster_counts),
            "cluster_guard_reject_symbols": sorted(self.rejected_symbols),
            "cluster_guard_exception_symbols": sorted(self.exception_symbols),
        }

    def _is_reject(self, cls: Mapping[str, Any]) -> bool:
        cid = int(cls.get("cluster_id") or -1)
        csub = int(cls.get("new_subcluster_id") or -1)
        if cid in self.config.reject_clusters:
            return True
        if csub in self.config.reject_csubs:
            return True
        return False

    def check(self, trade: Mapping[str, Any]) -> EntryClusterGuardCheck:
        if not self.config.enabled:
            return EntryClusterGuardCheck(blocked=False, cluster_guard_status=CLUSTER_GUARD_PASSED)

        merged = {**trade, **compute_entry_cluster_feature_fields(trade)}
        cls = self.model.classify(merged)
        lb = _float(cls.get("liquidity_burst"))
        thr = float(self.config.liquidity_burst_threshold)
        cid = int(cls.get("cluster_id") or -1)
        sid = int(cls.get("subcluster_id") or -1)
        csub = int(cls.get("new_subcluster_id") or -1)

        if not self._is_reject(cls):
            return EntryClusterGuardCheck(
                blocked=False,
                cluster_guard_status=CLUSTER_GUARD_PASSED,
                cluster_id=cid,
                subcluster_id=sid,
                new_subcluster_id=csub,
                liquidity_burst=lb,
                liquidity_burst_threshold=thr,
            )

        if self.config.exception_enabled and lb is not None and lb >= thr:
            return EntryClusterGuardCheck(
                blocked=False,
                cluster_guard_status=CLUSTER_GUARD_EXCEPTION,
                cluster_id=cid,
                subcluster_id=sid,
                new_subcluster_id=csub,
                liquidity_burst=lb,
                liquidity_burst_threshold=thr,
                via_exception=True,
            )

        return EntryClusterGuardCheck(
            blocked=True,
            cluster_guard_status=CLUSTER_GUARD_REJECTED,
            cluster_id=cid,
            subcluster_id=sid,
            new_subcluster_id=csub,
            liquidity_burst=lb,
            liquidity_burst_threshold=thr,
            reject_reason=REJECT_ENTRY_CLUSTER_GUARD,
        )

    def record_accept(self, trade: Mapping[str, Any], chk: EntryClusterGuardCheck) -> None:
        if not self.config.enabled:
            return
        sym = str(trade.get("symbol") or "")
        if chk.via_exception:
            self.exception_count += 1
            if sym:
                self.exception_symbols.add(sym)
        trade["cluster_guard_status"] = chk.cluster_guard_status
        trade["cluster_id"] = chk.cluster_id
        trade["new_subcluster_id"] = chk.new_subcluster_id
        trade["liquidity_burst"] = chk.liquidity_burst

    def record_reject(self, trade: Mapping[str, Any], chk: EntryClusterGuardCheck) -> None:
        if not self.config.enabled or not chk.blocked:
            return
        self.reject_count += 1
        sym = str(trade.get("symbol") or "")
        if sym:
            self.rejected_symbols.add(sym)
        key = f"c{chk.cluster_id}_s{chk.new_subcluster_id}"
        self.blocked_cluster_counts[key] = self.blocked_cluster_counts.get(key, 0) + 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        if not self.config.enabled:
            return
        if str(row.get("cluster_guard_status") or "") != CLUSTER_GUARD_EXCEPTION:
            return
        pnl = _float(row.get("pnl_yen_100"))
        if pnl is None:
            pct = _float(row.get("realized_pnl_pct") or row.get("pnl_pct"))
            pnl = (pct or 0.0) * 100.0
        self.exception_pnls.append(float(pnl))
        if pnl > 0:
            self.exception_wins += 1
        mfe = _float(row.get("peak_mfe_pct") or row.get("mfe_pct") or row.get("rolling_mfe_pct"))
        mfe = mfe if mfe is not None else 0.0
        if mfe <= 0.0:
            self.exception_mfe0 += 1
        if pnl > 0 and mfe > BIG_WINNER_MFE_PCT:
            self.exception_big_winner += 1


def config_from_pilot(pilot_config: Any, *, repo_root: Optional[Path] = None) -> EntryClusterGuardConfig:
    reject_clusters = pilot_config.raw.get("entry_cluster_guard_reject_clusters", [5])
    reject_csubs = pilot_config.raw.get("entry_cluster_guard_reject_csubs", [0, 2, 3, 5])
    model_path = pilot_config.raw.get("entry_cluster_guard_model_path")
    mp = Path(model_path) if model_path else None
    return EntryClusterGuardConfig(
        enabled=bool(getattr(pilot_config, "entry_cluster_guard_enabled", False)),
        exception_enabled=bool(getattr(pilot_config, "entry_cluster_guard_exception_enabled", True)),
        liquidity_burst_threshold=float(
            getattr(
                pilot_config,
                "entry_cluster_guard_liquidity_burst_threshold",
                DEFAULT_LIQUIDITY_BURST_THRESHOLD,
            )
        ),
        reject_clusters=frozenset(int(x) for x in reject_clusters),
        reject_csubs=frozenset(int(x) for x in reject_csubs),
        model_path=mp,
    )


def validate_entry_cluster_guard_model(
    pilot_config: Any,
    *,
    repo_root: Path,
) -> tuple[Optional[EntryClusterGuardState], list[str]]:
    """Preflight: model exists, JSON parses, classifier loads, guard state builds."""
    cfg = config_from_pilot(pilot_config, repo_root=repo_root)
    if not cfg.enabled:
        return None, []
    errors: list[str] = []
    try:
        path = resolve_entry_cluster_guard_model_path(
            repo_root=repo_root,
            yaml_path=cfg.model_path,
        )
    except FileNotFoundError as exc:
        return None, [str(exc)]
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"entry_cluster_guard model JSON parse error ({path}): {exc}"]
    try:
        model = EntryClusterModel.load(path)
    except Exception as exc:  # noqa: BLE001 — surface load failures in preflight
        return None, [f"entry_cluster_guard classifier load failed ({path}): {exc}"]
    try:
        state = build_entry_cluster_guard_state(pilot_config, repo_root=repo_root)
    except Exception as exc:  # noqa: BLE001
        return None, [f"build_entry_cluster_guard_state failed: {exc}"]
    if state is None:
        errors.append("build_entry_cluster_guard_state returned None while guard enabled")
        return None, errors
    if state.model.cluster_features != model.cluster_features:
        errors.append("classifier model mismatch after build_entry_cluster_guard_state")
    return state, errors


def build_entry_cluster_guard_state(
    pilot_config: Any,
    *,
    repo_root: Path,
) -> Optional[EntryClusterGuardState]:
    cfg = config_from_pilot(pilot_config, repo_root=repo_root)
    if not cfg.enabled:
        return None
    path = resolve_entry_cluster_guard_model_path(
        repo_root=repo_root,
        yaml_path=cfg.model_path,
    )
    model = EntryClusterModel.load(path)
    return EntryClusterGuardState(config=cfg, model=model)


def compute_entry_cluster_guard_fields(
    trade: Mapping[str, Any],
    *,
    model: Optional[EntryClusterModel] = None,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    merged = {**trade, **compute_entry_cluster_feature_fields(trade)}
    if model is None and repo_root is not None:
        model = load_default_model(repo_root=repo_root)
    if model is None:
        return {"liquidity_burst": merged.get("liquidity_burst")}
    cls = model.classify(merged)
    return {**cls, **compute_entry_cluster_feature_fields(trade)}


PHASE549_RUNTIME_VERDICT = "phase549_runtime_v6_e4_adopted"
PHASE552_MODEL_PATH_VERDICT = "phase552_entry_cluster_guard_model_path_fix_done"
PHASE552_SMOKE_VERDICT = "phase552_production_startup_smoke_test_and_model_path_fix_done"
