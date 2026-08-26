"""Research DynamicEngine: Fixed CLOCK_GRID off; Current _run_anchor/fill/EXIT on."""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))

from run_p0_4_exact_vs_fast_parity import CollectorEngine  # noqa: E402
from research.e1_x34b_entry_execution.features import preentry_from_board  # noqa: E402
from research.dynamic_anchor_p2_0b import CONFIRMED  # noqa: E402
from small_paper.v1r_live_dual_lane import canonical_symbol_key  # noqa: E402
from small_paper.v1r_native_entry_live import FEATURE_ORDER  # noqa: E402
from small_paper.v1r_primary_runtime import POSITION_CAP  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


class DynamicEngine(CollectorEngine):
    """P1 CollectorEngine + Dynamic T1/C1. Production CLOCK_GRID never fires."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.t1_out: Optional[dict[str, Any]] = None
        self.pending_confirmed: list[dict[str, Any]] = []
        self.dynamic_meta: dict[tuple[str, float], dict[str, Any]] = {}
        self.funnel = defaultdict(int)
        self.snapshot_future_leak = 0
        self.clock_grid_blocked = 0
        self.occ_integral = 0.0
        self.occ_span = 0.0
        self.max_concurrent = 0
        self._last_occ_t: Optional[float] = None
        self.dynamic_anchor_fires = 0
        self.eval_rows: list[dict[str, Any]] = []

    def bind_p2_1(self, t1_out: dict[str, Any]) -> None:
        """Frozen P2-1 T1/C1 ledger. ENTRY wakes on first event_t > t1 in this stream."""
        self.t1_out = t1_out
        self.trading_date = str(t1_out.get("date") or self.trading_date)
        self.pending_confirmed = [
            dict(c) for c in (t1_out.get("confirms") or []) if c.get("status") == CONFIRMED
        ]

    def maybe_fire_anchor(self, *, now_t: Optional[float] = None) -> list[dict[str, Any]]:
        self.clock_grid_blocked += 1
        return []

    def _note_occupancy(self, t: float) -> None:
        exp = int(self.exposure())
        self.max_concurrent = max(self.max_concurrent, exp)
        if self._last_occ_t is not None:
            dt = float(t) - float(self._last_occ_t)
            if dt > 0:
                self.occ_integral += exp * dt
                self.occ_span += dt
        self._last_occ_t = float(t)

    def process_market_push(
        self,
        *,
        symbol: str,
        payload: dict[str, Any],
        event_t: Optional[float] = None,
    ) -> dict[str, Any]:
        t = float(event_t if event_t is not None else 0.0)
        t_ing = time.perf_counter()
        ing = self.ingest_push(symbol=symbol, payload=payload, event_t=t)
        self.last_native_ingest_us = (time.perf_counter() - t_ing) * 1_000_000.0
        if not ing.get("ingested") and ing.get("reason") == "duplicate_sequence":
            ing["fill_checked"] = False
            ing["anchor_fired"] = False
            self.last_fill_check_us = 0.0
            return ing
        fired = self.maybe_fire_dynamic(now_t=t)
        t_fill = time.perf_counter()
        fills = self.on_tick_fill_check(event_t=t, payload=payload, symbol=symbol)
        self.last_fill_check_us = (time.perf_counter() - t_fill) * 1_000_000.0
        self._note_occupancy(t)
        ing["fill_checked"] = True
        ing["anchor_fired"] = bool(fired)
        ing["fill_n"] = len(fills or [])
        return ing

    def maybe_fire_dynamic(self, *, now_t: float) -> list[dict[str, Any]]:
        due: list[dict[str, Any]] = []
        still: list[dict[str, Any]] = []
        for c in self.pending_confirmed:
            if c.get("t1") is not None and float(c["t1"]) + 1e-12 < float(now_t):
                c["decision_fire_time"] = float(now_t)
                due.append(c)
            else:
                still.append(c)
        self.pending_confirmed = still
        if not due:
            return []
        by_t1: dict[float, list[dict[str, Any]]] = {}
        for c in due:
            by_t1.setdefault(round(float(c["t1"]), 6), []).append(c)
        out: list[dict[str, Any]] = []
        for t1k in sorted(by_t1):
            cohort = by_t1[t1k]
            t1 = float(cohort[0]["t1"])
            hour = datetime.fromtimestamp(t1, JST).hour
            session = str(cohort[0].get("session") or ("AM" if hour < 12 else "PM"))
            out.extend(
                self._fire_confirmed_cohort(
                    t1=t1, session=session, cohort=cohort, decision_time=float(now_t)
                )
            )
        return out

    def _universe_snapshot_ranks(self, t1: float) -> dict[str, dict[str, Any]]:
        scored: list[tuple[float, str]] = []
        info: dict[str, dict[str, Any]] = {}
        for sym in list(self.universe):
            s = canonical_symbol_key(sym)
            board = self._board_arrays(s)
            feats = preentry_from_board(board, t1) if board["t"].size else {}
            finite = bool(feats) and not any(
                feats.get(f) is None or not np.isfinite(feats.get(f)) for f in FEATURE_ORDER
            )
            score = None
            if finite:
                try:
                    sc = float(self.score_fn(feats))
                    if np.isfinite(sc):
                        score = sc
                except Exception:
                    score = None
            if board["t"].size:
                i = int(np.searchsorted(board["t"], t1, side="right") - 1)
                if i >= 0 and float(board["t"][i]) > float(t1) + 1e-12:
                    self.snapshot_future_leak += 1
            rec = {
                "symbol": s,
                "score": score,
                "feature_evaluable": finite,
                "score_evaluable": score is not None,
            }
            info[s] = rec
            if score is not None:
                scored.append((-score, s))
        scored.sort()
        for rank, (_neg, s) in enumerate(scored):
            info[s]["universe_rank"] = rank
        return info

    def _fire_confirmed_cohort(
        self,
        *,
        t1: float,
        session: str,
        cohort: list[dict[str, Any]],
        decision_time: float,
    ) -> list[dict[str, Any]]:
        day = self.trading_date
        confirmed_set = {canonical_symbol_key(c["symbol"]) for c in cohort}
        by_sym = {canonical_symbol_key(c["symbol"]): c for c in cohort}
        uni_info = self._universe_snapshot_ranks(t1)
        elig = []
        seen = set()
        for raw in list(self.universe):
            s = canonical_symbol_key(raw)
            if s in confirmed_set and s not in seen:
                elig.append(raw)
                seen.add(s)
        for s in confirmed_set:
            self.funnel["current_entry_evaluated"] += 1
            inf = uni_info.get(s) or {}
            if inf.get("feature_evaluable"):
                self.funnel["feature_evaluable"] += 1
            if inf.get("score_evaluable"):
                self.funnel["score_evaluable"] += 1
            self.eval_rows.append({
                "date": day,
                "t1": t1,
                "symbol": s,
                "decision_time": decision_time,
                "feature_evaluable": inf.get("feature_evaluable"),
                "score_evaluable": inf.get("score_evaluable"),
                "universe_rank": inf.get("universe_rank"),
                "score": inf.get("score"),
            })
        if not elig:
            self.funnel["other_reject"] += len(confirmed_set)
            return []
        saved = list(self.universe)
        anchor = f"D{t1:.6f}"
        pending_before = set(self.pending)
        open_before = set(self.open_symbols)
        admits_before = len(self.a_admits)
        try:
            self.universe = elig
            out = CollectorEngine._run_anchor(self, anchor=anchor, t0=t1, day=day, session=session)
        finally:
            self.universe = saved
        self.dynamic_anchor_fires += 1
        admitted_syms = {
            canonical_symbol_key(a.get("symbol")) for a in self.a_admits[admits_before:]
        }
        for e in self.a_candidates:
            if e.get("anchor") == anchor and e.get("admitted"):
                self.funnel["candidate_selected"] += 1
        for s in confirmed_set:
            inf = uni_info.get(s) or {}
            if s in admitted_syms:
                self.dynamic_meta[(s, float(t1))] = {
                    **by_sym[s],
                    "decision_time": decision_time,
                    "snapshot_cutoff": t1,
                    "score": inf.get("score"),
                    "universe_rank": inf.get("universe_rank"),
                    "anchor": anchor,
                }
                self.funnel["admitted"] += 1
            elif s in pending_before:
                self.funnel["blocked_pending"] += 1
            elif s in open_before:
                self.funnel["blocked_open"] += 1
            elif not inf.get("feature_evaluable") or not inf.get("score_evaluable"):
                self.funnel["other_reject"] += 1
            else:
                self.funnel["blocked_cap"] += 1
        return out

    def decision_snapshot_leaks(self) -> int:
        n = 0
        for m in self.dynamic_meta.values():
            fire = m.get("decision_time")
            cut = m.get("snapshot_cutoff")
            if fire is not None and cut is not None and float(fire) <= float(cut) + 1e-12:
                n += 1
        return n
