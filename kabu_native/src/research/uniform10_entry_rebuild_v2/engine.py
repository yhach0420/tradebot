"""Research Dual Lane engine: UNIFORM10 + C2 lookup scores. Fill/EXIT/CAP unchanged."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x36_joint_allocator.replay import simulate_joint
from research.fixed_anchor_mechanism_audit_p3_0.engine import P3Engine
from small_paper.v1r_live_dual_lane import get_dual_lane, live_primary_enabled
from small_paper.v1r_native_entry_live import PendingOrder
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC


class C2LookupEngine(P3Engine):
    """Admit by precomputed C2 score. No TopK gate. No score threshold. CAP/Fill live."""

    def __init__(self, *a: Any, score_lookup: Optional[dict[str, float]] = None, **k: Any) -> None:
        super().__init__(*a, **k)
        self.score_lookup = score_lookup or {}

    def _run_anchor(self, *, anchor: str, t0: float, day: str, session: str) -> list[dict[str, Any]]:
        pending_before = set(self.pending)
        open_before = set(self.open_symbols)
        self._harvest(self.events)
        self.events.clear()
        dual_pre = get_dual_lane(trace_dir=self.trace_dir) if live_primary_enabled() else None
        if dual_pre is not None:
            dual_pre.maybe_session_close(event_t=float(t0))
        self.on_tick_fill_check(event_t=float(t0))
        out: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        for sym in list(self.universe):
            s = str(sym).replace(".T", "")
            rows = self.boards.get(s) or []
            board = self._board_arrays(s)
            snap: dict[str, Any] = {
                "kind": "ANCHOR_SYMBOL_SNAPSHOT",
                "symbol": s,
                "anchor": anchor,
                "anchor_t0": float(t0),
                "model_score": None,
                "rank": None,
                "admitted": False,
            }
            if board["t"].size == 0:
                snapshots.append(snap)
                continue
            i = int(np.searchsorted(board["t"], t0, side="right") - 1)
            if i < 0:
                snapshots.append(snap)
                continue
            src = rows[i] if i < len(rows) else {}
            snap["snapshot_sequence"] = src.get("sequence")
            snap["Buy1"] = {"Price": float(board["bid"][i]), "Qty": float(board["bid_qty"][i])}
            snap["Sell1"] = {"Price": float(board["ask"][i]), "Qty": float(board["ask_qty"][i])}
            key = f"{day}|{anchor}|{s}"
            score = self.score_lookup.get(key)
            if score is None or not np.isfinite(float(score)):
                snapshots.append(snap)
                continue
            limit = float(board["bid"][i])
            if not np.isfinite(limit) or limit <= 0:
                snapshots.append(snap)
                continue
            snap["model_score"] = float(score)
            events.append(
                {
                    "date": day,
                    "symbol": s,
                    "session": session,
                    "signal_time": float(t0),
                    "filled": False,
                    "limit_price": limit,
                    "bid0": limit,
                    "score_preview": float(score),
                }
            )
            snapshots.append(snap)
        if not events:
            for srow in snapshots:
                self._emit(srow)
            self._emit({"kind": "ANCHOR_NO_CANDIDATE", "anchor": anchor, "t0": t0, "candidate_n": 0})
            self._harvest(self.events, default_anchor=anchor)
            self.events.clear()
            return out

        def _sfn(e: dict) -> float:
            return float(e.get("score_preview") or float("-inf"))

        sim = simulate_joint([dict(e) for e in events], score_fn=_sfn)
        ranked = sorted(
            [e for e in sim["events"] if e.get("alloc_score") is not None],
            key=lambda e: (-float(e.get("alloc_score") or 0.0), str(e.get("symbol") or "")),
        )
        rank_by_sym = {str(e["symbol"]): i for i, e in enumerate(ranked)}
        snap_by = {str(s["symbol"]): s for s in snapshots}
        for e in sim["events"]:
            ss = snap_by.get(str(e["symbol"]))
            if ss is not None:
                ss["model_score"] = e.get("alloc_score")
                ss["rank"] = rank_by_sym.get(str(e["symbol"]))
                ss["admitted"] = bool(e.get("admitted"))
        for srow in snapshots:
            self._emit(srow)
        for e in sim["events"]:
            if not e.get("admitted"):
                if e.get("CAPACITY_BLOCKED"):
                    self._notify(
                        "CAP_BLOCKED",
                        {"symbol": e["symbol"], "reason": "CAPACITY_BLOCKED", "anchor": anchor, "score": e.get("alloc_score")},
                    )
                continue
            if self.exposure() >= POSITION_CAP:
                self._notify("CAP_BLOCKED", {"symbol": e["symbol"], "reason": "CAPACITY_BLOCKED_LIVE", "anchor": anchor})
                continue
            if e["symbol"] in self.pending or e["symbol"] in self.open_symbols:
                continue
            po = PendingOrder(
                symbol=str(e["symbol"]),
                signal_time=float(t0),
                limit_price=float(e["limit_price"]),
                score=float(e.get("alloc_score") if e.get("alloc_score") is not None else e.get("score_preview") or 0.0),
                rank=int(rank_by_sym.get(str(e["symbol"]), 0)),
                anchor=anchor,
                session=session,
                date=day,
                features={},
            )
            self.pending[po.symbol] = po
            self.primary_admitted += 1
            payload = {
                "kind": "V1R_ENTRY_PENDING",
                "symbol": po.symbol,
                "anchor": anchor,
                "score": po.score,
                "rank": po.rank,
                "limit": po.limit_price,
                "wait_sec": WAIT_SEC,
                "open": self.open_n,
                "pending": self.pending_n,
                "cap": POSITION_CAP,
                "entry_mode": "C2_REBUILT_ENTRY_RESEARCH",
                "strategy": "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY",
            }
            self._emit(payload)
            self._notify("ENTRY", payload)
            out.append(payload)
        scored_admitted = [c for c in self.a_candidates if c.get("anchor") == anchor and c.get("admitted")]
        pending_after = set(self.pending)
        for c in scored_admitted:
            sym = str(c.get("symbol") or "")
            if sym in pending_before or (sym in open_before and sym not in pending_after):
                if not any(a.get("symbol") == sym and a.get("anchor") == anchor for a in self.a_admits):
                    self.same_symbol_blocked += 1
        self._harvest(self.events, default_anchor=anchor)
        self.events.clear()
        return out
