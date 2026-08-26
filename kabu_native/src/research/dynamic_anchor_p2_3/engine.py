"""Research DecompEngine: same Dynamic fire path, exclusive terminal ledger."""
from __future__ import annotations

from typing import Any

from run_p0_4_exact_vs_fast_parity import CollectorEngine
from research.dynamic_anchor_p2_2.engine import DynamicEngine
from research.dynamic_anchor_p2_3.metrics import exclusive_entry_terminal
from small_paper.v1r_live_dual_lane import canonical_symbol_key


class DecompEngine(DynamicEngine):
    """P2-2 DynamicEngine plus per-confirmed exclusive ENTRY terminal."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.terminal_rows: list[dict[str, Any]] = []

    def _record_row(self, row: dict[str, Any]) -> None:
        self.terminal_rows.append(row)

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
        seen: set[str] = set()
        for raw in list(self.universe):
            s = canonical_symbol_key(raw)
            if s in confirmed_set and s not in seen:
                elig.append(raw)
                seen.add(s)
        elig_set = {canonical_symbol_key(x) for x in elig}
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
        anchor = f"D{t1:.6f}"
        if not elig:
            self.funnel["other_reject"] += len(confirmed_set)
            for s in confirmed_set:
                src = by_sym.get(s) or {}
                inf = uni_info.get(s) or {}
                self._record_row({
                    "date": day,
                    "session": session,
                    "symbol": s,
                    "t0": src.get("t0"),
                    "t1": t1,
                    "anchor": anchor,
                    "decision_time": decision_time,
                    "c1_status": src.get("status"),
                    "trend_slope": src.get("trend_slope"),
                    "endpoint_return": src.get("endpoint_return"),
                    "p0": src.get("p0"),
                    "p10": src.get("p10"),
                    "in_elig": False,
                    "feature_evaluable": inf.get("feature_evaluable"),
                    "score_evaluable": inf.get("score_evaluable"),
                    "joint_admitted": False,
                    "universe_rank": inf.get("universe_rank"),
                    "score": inf.get("score"),
                    "limit": None,
                    "entry_terminal": "OTHER_REJECT",
                    "fill_terminal": None,
                    "canonical_terminal_outcome": "OTHER_REJECT",
                })
            return []
        saved = list(self.universe)
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
        admit_by_sym = {
            canonical_symbol_key(a.get("symbol")): a for a in self.a_admits[admits_before:]
        }
        for e in self.a_candidates:
            if e.get("anchor") == anchor and e.get("admitted"):
                self.funnel["candidate_selected"] += 1
        for s in confirmed_set:
            inf = uni_info.get(s) or {}
            src = by_sym.get(s) or {}
            snap = self.snapshots.get((anchor, s)) or {}
            joint_admitted = bool(snap.get("admitted"))
            live_admitted = s in admitted_syms
            entry = exclusive_entry_terminal(
                live_admitted=live_admitted,
                pending_before=s in pending_before,
                open_before=s in open_before,
                in_elig=s in elig_set,
                feature_evaluable=bool(inf.get("feature_evaluable")),
                score_evaluable=bool(inf.get("score_evaluable")),
                joint_admitted=joint_admitted,
            )
            if live_admitted:
                self.dynamic_meta[(s, float(t1))] = {
                    **src,
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
            limit = None
            if s in admit_by_sym:
                limit = admit_by_sym[s].get("limit")
            if limit is None:
                limit = snap.get("bid") or snap.get("limit")
            self._record_row({
                "date": day,
                "session": session,
                "symbol": s,
                "t0": src.get("t0"),
                "t1": t1,
                "anchor": anchor,
                "decision_time": decision_time,
                "c1_status": src.get("status"),
                "trend_slope": src.get("trend_slope"),
                "endpoint_return": src.get("endpoint_return"),
                "p0": src.get("p0"),
                "p10": src.get("p10"),
                "in_elig": s in elig_set,
                "feature_evaluable": inf.get("feature_evaluable"),
                "score_evaluable": inf.get("score_evaluable"),
                "joint_admitted": joint_admitted,
                "universe_rank": inf.get("universe_rank"),
                "score": inf.get("score"),
                "limit": limit,
                "entry_terminal": entry,
                "fill_terminal": None,
                "canonical_terminal_outcome": entry,
            })
        return out


def attach_fill_terminals(eng: DecompEngine, *, wait_sec: float | None = None) -> list[str]:
    """ADMITTED → FILLED | EXPIRED. Pending leftover is unresolved."""
    from research.dynamic_anchor_p2_3.fill_stage import resolve_admitted_fill_stage
    from small_paper.v1r_primary_runtime import WAIT_SEC as _WAIT

    w = float(wait_sec if wait_sec is not None else _WAIT)
    notes = resolve_admitted_fill_stage(eng.terminal_rows, list(eng.a_fills), wait_sec=w)
    pending_left = []
    try:
        pending_left = [str(s) for s in (eng.pending or {})]
    except Exception:
        pending_left = []
    if pending_left:
        notes.extend([f"PENDING_LEFT:{s}" for s in pending_left])
    return notes
