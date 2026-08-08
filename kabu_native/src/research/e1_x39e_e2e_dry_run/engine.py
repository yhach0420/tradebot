"""V1R Paper Primary dry-run engine — demo PUSH → feature → admit → fill/exit."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.e1_x35_passive_exit.exits import simulate_exit
from research.e1_x35_passive_exit.paths import build_path
from research.e1_x35r_exit_contract.contracts import canonical_fixed_exit
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized
from research.e1_x38_operational_wiring.notify_queue import NonBlockingNotifyQueue
from research.e1_x39_activation_lock.recovery import (
    PositionState,
    load_state,
    persist_state,
    recover,
)
from research.e1_x39_activation_lock.warmup import RollingFeatureState

from . import (
    BOARD_FRESHNESS_SEC,
    DEMO_DAY,
    DEMO_MARKER,
    DEMO_UNIVERSE,
    EXIT_HOLD_SEC,
    FEATURE_ORDER,
    LOT_QTY,
    MIN_QTY,
    MODEL_ARTIFACT_SHA,
    NOTIFY_PREFIXES,
    POSITION_CAP,
    PRECOMMIT_U1_SHA,
    V1R_SHA,
    WAIT_SEC,
)
from .push_board import DemoPush, SymbolBoardBuilder, demo_day_epoch

_1M = 1_000_000.0


@dataclass
class SafetyCounters:
    submit: int = 0
    cancel: int = 0
    live: int = 0
    live_api_calls: list[str] = field(default_factory=list)

    def assert_zero(self) -> None:
        assert self.submit == 0 and self.cancel == 0 and self.live == 0
        assert len(self.live_api_calls) == 0


@dataclass
class DryRunEngine:
    ser: dict
    notify: NonBlockingNotifyQueue
    safety: SafetyCounters = field(default_factory=SafetyCounters)
    boards: dict[str, SymbolBoardBuilder] = field(default_factory=dict)
    ledgers: dict[str, list[dict]] = field(default_factory=dict)
    open_plus_pending_max: int = 0
    cap_violations: int = 0
    heartbeat_snapshots: list[dict] = field(default_factory=list)
    shadow_pbv2_positions: int = 0
    shadow_1m_positions: int = 0
    cash_1m: float = _1M
    primary_open: int = 0
    primary_pending: int = 0

    def __post_init__(self) -> None:
        for sym in DEMO_UNIVERSE:
            self.boards[sym] = SymbolBoardBuilder(symbol=sym)
        for name in (
            "V1R_RESEARCH_PROSPECTIVE_TEST",
            "V1R_OPERATIONAL_REALIZABLE_TEST",
            "PBV2_SHADOW_TEST",
            "V1R_1M_SHADOW_TEST",
        ):
            self.ledgers[name] = [{"marker": DEMO_MARKER, "demo_day": DEMO_DAY}]
        self.score_fn = score_fn_from_serialized(self.ser)

    def ingest_push(self, push: DemoPush) -> None:
        """Runtime PUSH ingestion path (demo transport)."""
        if push.symbol not in self.boards:
            self.boards[push.symbol] = SymbolBoardBuilder(symbol=push.symbol)
        self.boards[push.symbol].ingest(push)

    def board(self, symbol: str) -> dict[str, np.ndarray]:
        return self.boards[symbol].to_board()

    def features_from_push(self, symbol: str, t0: float) -> dict[str, Any]:
        """raw PUSH → board → preentry features (not injected)."""
        b = self.board(symbol)
        batch = preentry_from_board(b, t0)
        roll = RollingFeatureState()
        roll.update_from_board_prefix(b, t0)
        snap = roll.snapshot(t0)
        # prefer batch (canonical); verify rolling identity
        for f in FEATURE_ORDER:
            bv, rv = batch.get(f), snap.get(f)
            if bv is not None and rv is not None and np.isfinite(bv) and np.isfinite(rv):
                assert abs(float(bv) - float(rv)) < 1e-6, (symbol, f, bv, rv)
        return {f: batch.get(f) for f in FEATURE_ORDER}

    def emit_hb(self, *, last_anchor: Optional[str], next_anchor: Optional[str]) -> dict:
        hb = {
            "PRIMARY": "V1R",
            "PBV2": "SHADOW",
            "ONE_M": "SHADOW",
            "v1r_sha": V1R_SHA,
            "model_sha": MODEL_ARTIFACT_SHA,
            "universe_binding_sha": "45b2fb20d02abbe7d557a55fecc87da3e7c19126eb7415ce9bdc4579aca39fee",
            "precommit_sha": PRECOMMIT_U1_SHA,
            "universe_contract": "DAY_FIXED_AM_RUNTIME_UNIVERSE_V1",
            "OPEN": self.primary_open,
            "PENDING": self.primary_pending,
            "last_anchor": last_anchor,
            "next_anchor": next_anchor,
            "decision_latency_ms": 0.0,
            "notify_backlog": self.notify.stats()["backlog"],
            "event_drops": 0,
            "submit_cancel_live": f"{self.safety.submit}/{self.safety.cancel}/{self.safety.live}",
            "cash_1m": self.cash_1m,
            "demo_marker": DEMO_MARKER,
        }
        self.heartbeat_snapshots.append(hb)
        return hb

    def track_cap(self, open_n: int, pending_n: int) -> None:
        total = open_n + pending_n
        self.open_plus_pending_max = max(self.open_plus_pending_max, total)
        if total > POSITION_CAP:
            self.cap_violations += 1

    def append_ledger(self, name: str, row: dict) -> None:
        row = dict(row)
        row["marker"] = DEMO_MARKER
        self.ledgers[name].append(row)
