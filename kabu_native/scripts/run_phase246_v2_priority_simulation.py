#!/usr/bin/env python3
"""
Phase246 (review only)

Goal:
For the same 38 sessions used in Phase245, simulate whether prioritizing
v2_score_ge5 candidates (without increasing max_concurrent_positions) helps.

Key constraint:
- max_concurrent_positions = 3 (fixed)
- no ENTRY/Score/YAML/prod change (review-only simulation)

We simulate *entry admission order only* at each decision timestamp:
- A (baseline): current order (as logged)
- B (v2 priority): within the same event_time group, process v2_ge5 first
- C (v2-only): within the same event_time group, keep only v2_ge5

Decision points:
We only consider rows with event_type in {"accepted","rejected"} and where
either event_type=="accepted" OR gate_reject_reason=="max_concurrent".
Other rejects remain rejected in all scenarios (not part of this comparison).

Exit:
Use Phase71 simulate_combined_split driven by subsequent candidate ticks,
same as Phase245 counterfactual replay engine.

Output:
kabu_native/results/reports/phase246_v2_priority_simulation.json
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase246_v2_priority_simulation.json"

MAX_POS = 3
TARGET_REASON = "max_concurrent"

V1_MODE = "legacy"
V1_RATIO = 0.85


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_module(name: str, rel_path: str) -> Any:
    path = REPO / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _metrics(pnls: list[float], stop_hits: int) -> dict[str, Any]:
    n = len(pnls)
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "win_rate": None,
            "stop_rate": None,
            "avg_pnl_pct": None,
        }
    wins = sum(1 for p in pnls if p > 0)
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stop_hits / n, 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
    }


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    csv_path = session_dir / "small_paper_events.csv"
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        out: list[dict[str, Any]] = []
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
    return []


def _read_summary(session_dir: Path) -> dict[str, Any]:
    p = session_dir / "small_paper_summary.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _discover_sessions(base: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for summary_path in sorted(base.rglob("small_paper_summary.json")):
        sdir = summary_path.parent
        summ = _read_summary(sdir)
        out.append(
            {
                "session_id": sdir.relative_to(base).as_posix(),
                "session_dir": str(sdir),
                "mode": summ.get("mode"),
                "source": summ.get("source"),
            }
        )
    return out


def _session_end(events: list[dict[str, Any]]) -> str:
    best = ""
    best_ts = 0.0
    for ev in events:
        t = str(ev.get("entry_time") or ev.get("event_time") or "")
        ts = _parse_ts(t)
        if ts >= best_ts:
            best_ts = ts
            best = t
    return best


@dataclass
class CompletedTrade:
    pnl_pct: float
    stop_hit: bool
    v2_ge5: bool


def _v2_ge5(ev: dict[str, Any]) -> bool:
    # Prefer already-logged flag, else compute v2 score (for older sessions).
    flag = ev.get("entry_expectancy_score_v2_ge5_flag")
    if flag in (True, "True", "true", "1", 1):
        return True
    if flag in (False, "False", "false", "0", 0):
        return False

    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    fields = compute_entry_expectancy_score_fields(trade=ev)
    return int(fields.get("entry_expectancy_score_v2") or 0) >= 5


class ScenarioSim:
    def __init__(self, name: str, p71: Any, policy: str):
        self.name = name
        self.p71 = p71
        self.policy = policy  # "A"|"B"|"C"

        self.sym_states: dict[str, Any] = {}
        self.active: dict[str, Any] = {}  # symbol -> ActiveTrade
        self.completed: list[CompletedTrade] = []

        self.max_concurrent_reject_count = 0
        self.v2_missed_count = 0
        self.v2_taken_count = 0

        self._pending_time: Optional[str] = None
        self._pending_decisions: list[dict[str, Any]] = []

    def _sym_state(self, sym: str) -> Any:
        return self.sym_states.setdefault(sym, self.p71.SymState())

    def _close(self, act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        tr = act.trade
        pnl = float(self.p71._pnl_pct(tr.entry_price, close_price))
        stop = str(reason) == "stop_hit"
        v2 = bool(getattr(tr, "v2_ge5", False))
        self.completed.append(CompletedTrade(pnl_pct=pnl, stop_hit=stop, v2_ge5=v2))

    def _open_from_event(self, ev: dict[str, Any]) -> bool:
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = _float(ev.get("current_price")) or _float(ev.get("entry_price")) or 0.0
        if not sym or not ent or px <= 0:
            return False
        if sym in self.active:
            return False
        if len(self.active) >= MAX_POS:
            self.max_concurrent_reject_count += 1
            if _v2_ge5(ev):
                self.v2_missed_count += 1
            return False

        ts = self.p71._parse_ts(ent) if hasattr(self.p71, "_parse_ts") else _parse_ts(ent)
        st = self._sym_state(sym)
        comps = self.p71._components(st, ts=ts, price=float(px), ev=ev)
        q = _float(ev.get("continuation_quality_score")) or 0.0

        tr = self.p71.StructuralTrade(sym, ent, float(px), float(q))
        setattr(tr, "v2_ge5", _v2_ge5(ev))
        if getattr(tr, "v2_ge5"):
            self.v2_taken_count += 1

        self.active[sym] = self.p71.ActiveTrade(
            trade=tr,
            entry_ts=ts,
            rich_ticks=[
                {
                    "price": float(px),
                    "pnl_pct": 0.0,
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            ],
        )
        return True

    def _eligible_decision(self, ev: dict[str, Any]) -> bool:
        et = str(ev.get("event_type") or "")
        if et not in ("accepted", "rejected"):
            return False
        reason = str(ev.get("gate_reject_reason") or "")
        # "baseline entry intention" population = accepted + max_concurrent rejects
        return et == "accepted" or reason == TARGET_REASON

    def _flush_pending(self) -> None:
        if not self._pending_decisions:
            return

        # Apply per-policy ordering/filtering within same event_time group.
        items = list(self._pending_decisions)
        if self.policy == "A":
            ordered = items  # as-is
        elif self.policy == "B":
            ordered = sorted(
                items,
                key=lambda ev: (0 if _v2_ge5(ev) else 1, int(ev.get("message_index") or 0)),
            )
        elif self.policy == "C":
            ordered = [ev for ev in items if _v2_ge5(ev)]
            ordered = sorted(ordered, key=lambda ev: int(ev.get("message_index") or 0))
        else:
            ordered = items

        for ev in ordered:
            self._open_from_event(ev)

        self._pending_decisions = []

    def on_row(self, ev: dict[str, Any]) -> None:
        # Candidate ticks drive exits for active positions.
        et = str(ev.get("event_type") or "")
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = _float(ev.get("current_price")) or 0.0

        # group decision rows by event_time (not entry_time).
        ev_time = str(ev.get("event_time") or "")
        if self._pending_time is None:
            self._pending_time = ev_time
        if ev_time != self._pending_time:
            self._flush_pending()
            self._pending_time = ev_time

        if et == "candidate" and sym in self.active and px > 0 and ent:
            ts = self.p71._parse_ts(ent) if hasattr(self.p71, "_parse_ts") else _parse_ts(ent)
            st = self._sym_state(sym)
            act = self.active[sym]
            comps = self.p71._components(st, ts=ts, price=float(px), ev=ev)
            act.rich_ticks.append(
                {
                    "price": float(px),
                    "pnl_pct": self.p71._pnl_pct(act.trade.entry_price, float(px)),
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            )
            sig = self.p71.simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode=V1_MODE,
                ratio=V1_RATIO,
                allow_session_end=False,
            )
            if sig:
                _, reason, _ = sig
                self._close(act, close_time=ent, close_price=float(px), reason=str(reason))
                self.active.pop(sym, None)

        # collect eligible decision rows for this time slice.
        if self._eligible_decision(ev):
            self._pending_decisions.append(ev)

    def finalize(self, session_end: str) -> None:
        self._flush_pending()
        # close remaining at session end at last seen price.
        for act in list(self.active.values()):
            last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
            self._close(act, close_time=session_end, close_price=float(last_px), reason="session_end")
        self.active.clear()

    def summary(self) -> dict[str, Any]:
        pnls = [t.pnl_pct for t in self.completed]
        stops = sum(1 for t in self.completed if t.stop_hit)
        return {
            "metrics": _metrics(pnls, stops),
            "max_concurrent_reject_count": self.max_concurrent_reject_count,
            "v2_missed_count": self.v2_missed_count,
            "v2_taken_count": self.v2_taken_count,
        }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    p71 = _load_module("phase71_engine_p246", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")
    sessions = _discover_sessions(SMALL_PAPER)

    sims_all = {
        "A": ScenarioSim("A_current_order", p71, "A"),
        "B": ScenarioSim("B_v2_priority", p71, "B"),
        "C": ScenarioSim("C_v2_only", p71, "C"),
    }

    by_session: list[dict[str, Any]] = []

    for idx, sess in enumerate(sessions, 1):
        sdir = Path(sess["session_dir"])
        events = _load_events(sdir)
        if not events:
            continue
        # ensure stable order for streaming.
        def _k(ev: dict[str, Any]) -> tuple[float, int]:
            return (_parse_ts(str(ev.get("event_time") or ev.get("entry_time") or "")), int(ev.get("message_index") or 0))

        events_sorted = sorted(events, key=_k)
        end = _session_end(events_sorted)

        sims = {
            "A": ScenarioSim("A_current_order", p71, "A"),
            "B": ScenarioSim("B_v2_priority", p71, "B"),
            "C": ScenarioSim("C_v2_only", p71, "C"),
        }

        for ev in events_sorted:
            for sim in sims.values():
                sim.on_row(ev)
        for sim in sims.values():
            sim.finalize(end)

        by_session.append(
            {
                "session_id": sess["session_id"],
                "mode": sess.get("mode"),
                "source": sess.get("source"),
                "A": sims["A"].summary(),
                "B": sims["B"].summary(),
                "C": sims["C"].summary(),
            }
        )

        # merge into all-sessions aggregate
        for key in ("A", "B", "C"):
            sims_all[key].completed.extend(sims[key].completed)
            sims_all[key].max_concurrent_reject_count += sims[key].max_concurrent_reject_count
            sims_all[key].v2_missed_count += sims[key].v2_missed_count
            sims_all[key].v2_taken_count += sims[key].v2_taken_count

        if idx % 10 == 0:
            print(f"  [{idx}/{len(sessions)}] simulated...", flush=True)

    report = {
        "phase": 246,
        "mode": "v2_priority_simulation",
        "constraints": {
            "review_only": True,
            "max_concurrent_positions_fixed": MAX_POS,
            "entry_change_forbidden": True,
            "score_change_forbidden": True,
            "yaml_change_forbidden": True,
            "production_change_forbidden": True,
        },
        "population": {
            "sessions_scanned": len(sessions),
            "note": "decision rows = accepted + rejected(max_concurrent); other rejects excluded from reordering",
        },
        "comparison": {
            "A_current_order": sims_all["A"].summary(),
            "B_v2_priority": sims_all["B"].summary(),
            "C_v2_only": sims_all["C"].summary(),
        },
        "by_session": by_session,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

