"""Exact Dual-Lane CURRENT ENTRY with session re-entry lockout. Research-only."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x34b_entry_execution.features import preentry_from_board
from research.e1_x36_joint_allocator.replay import simulate_joint
from research.uniform10_b_followup.classify import classify_t0_row
from research.uniform10_b_followup.engine import BFollowEngine, _pct_keep
from small_paper.v1r_live_dual_lane import canonical_symbol_key, get_dual_lane, live_primary_enabled
from small_paper.v1r_native_entry_live import FEATURE_ORDER, PendingOrder
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

JST = ZoneInfo("Asia/Tokyo")


def _session_of_epoch(ts: Any) -> str:
    try:
        dt = datetime.fromtimestamp(float(ts), JST)
        return "AM" if dt.hour < 12 else "PM"
    except (TypeError, ValueError, OSError):
        return ""


class ReentryEngine(BFollowEngine):
    """CURRENT ENTRY Dual Lane plus a precommitted same-session re-entry policy.

    R0 uses the canonical BFollowEngine / CollectorEngine path (no extra gate).
    R1/R2/R3 exclude locked symbols from simulate_joint so CAP can go to other names.
    Lockout is not a score/threshold/cooldown search.
    """

    def __init__(self, *a: Any, reentry_policy: str = "R0", **k: Any) -> None:
        super().__init__(*a, **k)
        self.reentry_policy = str(reentry_policy or "R0")
        self._session_rt: dict[tuple[str, str], dict[str, Any]] = {}
        self._fill_session: dict[str, str] = {}
        self.lockout_skips = 0

    def _rt(self, session: str, symbol: str) -> dict[str, Any]:
        key = (str(session or ""), canonical_symbol_key(symbol))
        rec = self._session_rt.get(key)
        if rec is None:
            rec = {"n": 0, "last_pnl": None}
            self._session_rt[key] = rec
        return rec

    def _reentry_blocked(self, symbol: str, session: str) -> bool:
        pol = str(self.reentry_policy or "R0")
        if pol in ("R0", "", "None"):
            return False
        rec = self._rt(session, symbol)
        n = int(rec.get("n") or 0)
        last = rec.get("last_pnl")
        if pol == "R1":
            return n >= 1
        if pol == "R2":
            return n >= 2
        if pol == "R3":
            if n < 1:
                return False
            try:
                return float(last) <= 0.0
            except (TypeError, ValueError):
                return True
        return False

    def _promote_fill(self, po: PendingOrder, fill: dict[str, Any]) -> dict[str, Any]:
        ev = super()._promote_fill(po, fill)
        key = canonical_symbol_key(po.symbol)
        self._fill_session[key] = str(po.session or "")
        return ev

    def _exit_economics(self, symbol: str) -> tuple[float, Optional[float], Optional[float]]:
        dual = get_dual_lane(trace_dir=self.trace_dir) if live_primary_enabled() else None
        key = canonical_symbol_key(symbol)
        if dual is None:
            return 0.0, None, None
        for row in reversed(list(getattr(dual, "traces", None) or [])):
            extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
            merged = {**row, **extra}
            if str(merged.get("event") or "") != "EXIT_EXECUTED":
                continue
            if canonical_symbol_key(merged.get("symbol")) != key:
                continue
            if str(merged.get("lane") or "primary") != "primary":
                continue
            try:
                fill_px = float(merged.get("fill_price") or 0.0)
            except (TypeError, ValueError):
                fill_px = 0.0
            try:
                exit_px = float(merged.get("exit_price") or 0.0)
            except (TypeError, ValueError):
                exit_px = 0.0
            pnl = (exit_px - fill_px) * 100.0 if fill_px and exit_px else 0.0
            return float(pnl), fill_px or None, exit_px or None
        return 0.0, None, None

    def note_primary_exit(
        self,
        symbol: str,
        *,
        exit_time: Optional[float] = None,
        reason: str = "",
    ) -> dict[str, Any]:
        rec = super().note_primary_exit(symbol, exit_time=exit_time, reason=reason)
        key = canonical_symbol_key(symbol)
        if rec.get("duplicate") and key not in self._fill_session:
            return rec
        sess = str(self._fill_session.pop(key, "") or "")
        if not sess:
            sess = _session_of_epoch(exit_time)
        pnl, _fill_px, _exit_px = self._exit_economics(key)
        st = self._rt(sess, key)
        st["n"] = int(st.get("n") or 0) + 1
        st["last_pnl"] = float(pnl)
        st["last_reason"] = str(reason or "")
        st["last_exit_time"] = exit_time
        return rec

    def _run_anchor(self, *, anchor: str, t0: float, day: str, session: str) -> list[dict[str, Any]]:
        if str(self.reentry_policy or "R0") in ("R0", "", "None"):
            return super()._run_anchor(anchor=anchor, t0=t0, day=day, session=session)
        return self._run_anchor_lockout(anchor=anchor, t0=t0, day=day, session=session)

    def _run_anchor_lockout(self, *, anchor: str, t0: float, day: str, session: str) -> list[dict[str, Any]]:
        """BFollowEngine filtered admit path plus same-session re-entry lockout.

        Locked symbols are dropped before simulate_joint so CAP/same-symbol
        occupancy can admit other names. No occupancy approximation.
        """
        self.anchor_occ.append(
            {
                "date": day,
                "session": session,
                "anchor": anchor,
                "t0": float(t0),
                "open": sorted(str(s) for s in self.open_symbols),
                "pending": sorted(str(s) for s in self.pending),
                "exposure": int(self.exposure()),
                "open_n": int(self.open_n),
                "pending_n": int(self.pending_n),
                "position_cap": int(POSITION_CAP),
            }
        )
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
                "snapshot_sequence": None,
                "snapshot_received_at": None,
                "snapshot_age_ms": None,
                "Buy1": None,
                "Sell1": None,
                "model_score": None,
                "rank": None,
                "admitted": False,
                "executable_at_t0": None,
                "nonexec_bucket": None,
            }
            if board["t"].size == 0:
                snapshots.append(snap)
                continue
            i = int(np.searchsorted(board["t"], t0, side="right") - 1)
            if i < 0:
                snapshots.append(snap)
                continue
            src = rows[i] if i < len(rows) else {}
            snap_t = float(board["t"][i])
            snap["snapshot_sequence"] = src.get("sequence")
            snap["snapshot_received_at"] = src.get("received_at")
            snap["snapshot_age_ms"] = round((float(t0) - snap_t) * 1000.0, 3)
            snap["Buy1"] = {"Price": float(board["bid"][i]), "Qty": float(board["bid_qty"][i])}
            snap["Sell1"] = {"Price": float(board["ask"][i]), "Qty": float(board["ask_qty"][i])}
            klass = classify_t0_row(src, event_t=float(board["t"][i]))
            snap["executable_at_t0"] = klass["executable_at_t0"]
            snap["nonexec_bucket"] = klass["nonexec_bucket"]
            snap["board_state"] = klass["state"]
            feats = preentry_from_board(board, t0)
            if any(feats.get(f) is None or not np.isfinite(feats.get(f)) for f in FEATURE_ORDER):
                snapshots.append(snap)
                continue
            score = float(self.score_fn(feats))
            if not np.isfinite(score):
                snapshots.append(snap)
                continue
            limit = float(board["bid"][i])
            if not np.isfinite(limit) or limit <= 0:
                snapshots.append(snap)
                continue
            snap["model_score"] = score
            snap["features"] = {f: feats.get(f) for f in FEATURE_ORDER}
            if self._reentry_blocked(s, session):
                snap["reentry_lockout"] = True
                self.lockout_skips += 1
                snapshots.append(snap)
                continue
            rec = {
                "date": day,
                "symbol": s,
                "session": session,
                "signal_time": float(t0),
                "filled": False,
                "limit_price": limit,
                "bid0": limit,
                "executable_at_t0": klass["executable_at_t0"],
                "nonexec_bucket": klass["nonexec_bucket"],
                **{f: feats.get(f) for f in FEATURE_ORDER},
                "score_preview": score,
            }
            events.append(rec)
            snapshots.append(snap)
        scores = [float(e["score_preview"]) for e in events]
        admit_events = []
        for e in events:
            if self.executable_t0_only and not e.get("executable_at_t0"):
                continue
            if not _pct_keep(scores, self.score_pct, float(e["score_preview"])):
                continue
            admit_events.append(e)
        snap_by = {str(s["symbol"]): s for s in snapshots}
        if not admit_events:
            for srow in snapshots:
                self._emit(srow)
            self._emit(
                {
                    "kind": "ANCHOR_NO_CANDIDATE",
                    "anchor": anchor,
                    "t0": t0,
                    "universe_n": len(self.universe),
                    "candidate_n": 0,
                }
            )
            self._harvest(self.events, default_anchor=anchor)
            self.events.clear()
            return out

        sim = simulate_joint([dict(e) for e in admit_events], score_fn=self.score_fn)
        ranked = sorted(
            [e for e in sim["events"] if e.get("alloc_score") is not None],
            key=lambda e: (-float(e.get("alloc_score") or 0.0), str(e.get("symbol") or "")),
        )
        rank_by_sym = {str(e["symbol"]): i for i, e in enumerate(ranked)}
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
                        {
                            "symbol": e["symbol"],
                            "reason": "CAPACITY_BLOCKED",
                            "anchor": anchor,
                            "score": e.get("alloc_score"),
                        },
                    )
                continue
            if self.exposure() >= POSITION_CAP:
                self._notify(
                    "CAP_BLOCKED",
                    {"symbol": e["symbol"], "reason": "CAPACITY_BLOCKED_LIVE", "anchor": anchor},
                )
                continue
            if e["symbol"] in self.pending or e["symbol"] in self.open_symbols:
                continue
            if self._reentry_blocked(str(e["symbol"]), session):
                self.lockout_skips += 1
                continue
            po = PendingOrder(
                symbol=str(e["symbol"]),
                signal_time=float(t0),
                limit_price=float(e["limit_price"]),
                score=float(e.get("alloc_score") if e.get("alloc_score") is not None else e.get("score_preview") or 0.0),
                rank=int(e.get("cohort_rank") if e.get("cohort_rank") is not None else rank_by_sym.get(str(e["symbol"]), 0)),
                anchor=anchor,
                session=session,
                date=day,
                features={f: e.get(f) for f in FEATURE_ORDER},
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
                "entry_mode": "V1R / PASSIVE BID",
                "strategy": "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY",
            }
            for f in FEATURE_ORDER:
                payload[f] = e.get(f)
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
