"""RANK1_ONLY Exact Dual-Lane engine. Research-only. Runtime CAP/Fill/EXIT unchanged."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x36_joint_allocator.replay import simulate_joint
from research.entry_objective_redesign_c3.engine import C3LookupEngine, _xs_imbalance
from research.entry_objective_redesign_c3.oof import _predict_row, apply_norm
from research.uniform10_b_followup.classify import classify_t0_row
from research.uniform10_entry_rebuild_v2.features import causal_features
from small_paper.v1r_live_dual_lane import get_dual_lane, live_primary_enabled
from small_paper.v1r_native_entry_live import PendingOrder
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC


class Top1RankEngine(C3LookupEngine):
    """Score executable names, rank score DESC / symbol ASC, admit RANK==1 only. No threshold."""

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
        snapshots: list[dict[str, Any]] = []
        collected: list[dict[str, Any]] = []
        fit = self.fit or {}
        feats = list(fit.get("features") or [])
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
                "executable_at_t0": None,
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
            klass = classify_t0_row(src, event_t=float(board["t"][i]))
            snap["executable_at_t0"] = klass["executable_at_t0"]
            snap["nonexec_bucket"] = klass["nonexec_bucket"]
            if not klass["executable_at_t0"]:
                snapshots.append(snap)
                continue
            limit = float(board["bid"][i])
            if not np.isfinite(limit) or limit <= 0:
                snapshots.append(snap)
                continue
            raw_feats = causal_features(board, t0, rows=None, series=self._series(s))
            rec = {
                "date": day,
                "symbol": s,
                "anchor": anchor,
                **{
                    k: raw_feats.get(k)
                    for k in (
                        "imbalance",
                        "drawdown_180s",
                        "mid_range_180s_bps",
                        "vwap_dist_bps",
                        "mid_abs_ret_60s",
                        "mid_ret_180s",
                        "spread_bps",
                        "mid_ret_60s",
                        "event_rate_60s",
                        "log_bid_qty",
                    )
                },
            }
            collected.append({"snap": snap, "limit": limit, "rec": rec, "s": s})
            snapshots.append(snap)
        if collected:
            feat_rows = [c["rec"] for c in collected]
            _xs_imbalance(feat_rows)
            normed = apply_norm(feat_rows, feats, str(fit.get("normalization") or "none"))
            for c, r in zip(collected, normed):
                sc = _predict_row(fit, r)
                c["score"] = sc
                c["snap"]["model_score"] = sc
        events: list[dict[str, Any]] = []
        snap_by = {str(s["symbol"]): s for s in snapshots}
        for c in collected:
            sc = c.get("score")
            if sc is None or not np.isfinite(float(sc)):
                continue
            events.append(
                {
                    "date": day,
                    "symbol": c["s"],
                    "session": session,
                    "signal_time": float(t0),
                    "filled": False,
                    "limit_price": c["limit"],
                    "bid0": c["limit"],
                    "executable_at_t0": True,
                    "score_preview": float(sc),
                }
            )
        self.anchor_candidate_n += len(events)
        if not events:
            for srow in snapshots:
                self._emit(srow)
            self._emit({"kind": "ANCHOR_NO_CANDIDATE", "anchor": anchor, "t0": t0, "candidate_n": 0})
            self._harvest(self.events, default_anchor=anchor)
            self.events.clear()
            return out

        def _sfn(e: dict) -> float:
            v = e.get("score_preview")
            try:
                x = float(v)
            except (TypeError, ValueError):
                return float("-inf")
            return x if np.isfinite(x) else float("-inf")

        sim = simulate_joint([dict(e) for e in events], score_fn=_sfn)
        ranked = sorted(
            [e for e in sim["events"] if e.get("alloc_score") is not None],
            key=lambda e: (-float(e.get("alloc_score") or 0.0), str(e.get("symbol") or "")),
        )
        rank_by_sym = {str(e["symbol"]): i + 1 for i, e in enumerate(ranked)}
        for e in sim["events"]:
            ss = snap_by.get(str(e["symbol"]))
            rnk = rank_by_sym.get(str(e["symbol"]))
            if ss is not None:
                ss["model_score"] = e.get("alloc_score")
                ss["rank"] = rnk
                ss["admitted"] = rnk == 1
        for srow in snapshots:
            self._emit(srow)
        for e in sim["events"]:
            rnk = rank_by_sym.get(str(e["symbol"]))
            if rnk != 1:
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
                rank=1,
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
                "rank": 1,
                "limit": po.limit_price,
                "wait_sec": WAIT_SEC,
                "open": self.open_n,
                "pending": self.pending_n,
                "cap": POSITION_CAP,
                "entry_mode": "TOP1_RANK1_ONLY_RESEARCH_ENTRY",
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
