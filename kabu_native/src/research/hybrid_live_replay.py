"""
Phase 138: hybrid_live_accepted_structural_exit replay engine (review only).

Entry + exit timelines are ground-truth from live structural_trades.csv
(aligned with small_paper accepted events). No simulated structural exit replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.fade_switch_policy_review import FADE_EXIT_REASONS
from research.mfe_mae_exit_review import as_float, load_structural_trades, parse_ts
from research.replay_fidelity_review import _norm_session_id
from research.small_paper_performance_review import _load_events
from research.switch_old_vs_new_review import MAX_PAIR_SEC, SWITCH_EXIT_REASONS

MAX_CONCURRENT = 3
HYBRID_MODE_ID = "hybrid_live_accepted_structural_exit"


@dataclass
class HybridPosition:
    symbol: str
    entry_time: str
    entry_ts: float
    entry_price: float
    close_time: str
    close_ts: float
    close_price: float
    close_reason: str
    realized_pnl_pct: float
    continuation_quality_score: float
    mfe_pct: float
    mae_pct: float
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "symbol": self.symbol,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "close_time": self.close_time,
            "close_price": self.close_price,
            "close_reason": self.close_reason,
            "realized_pnl_pct": self.realized_pnl_pct,
            "continuation_quality_score": self.continuation_quality_score,
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
        }


@dataclass
class HybridReplaySession:
    session_id: str
    positions: list[HybridPosition] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    fade_switches: list[dict[str, Any]] = field(default_factory=list)
    max_open_slots_observed: int = 0
    cap_violation_count: int = 0
    accepted_events_count: int = 0


def load_hybrid_positions(session_dir: Any, *, session_id: str) -> list[HybridPosition]:
    """Ground-truth positions from structural_trades.csv."""
    rows = load_structural_trades(session_dir / "structural_trades.csv")
    out: list[HybridPosition] = []
    for r in rows:
        ent = str(r.get("entry_time") or "")
        ex = str(r.get("close_time") or "")
        ent_ts = parse_ts(ent)
        ex_ts = parse_ts(ex)
        if ent_ts <= 0 or ex_ts <= 0:
            continue
        out.append(
            HybridPosition(
                session_id=session_id,
                symbol=str(r.get("symbol") or ""),
                entry_time=ent,
                entry_ts=ent_ts,
                entry_price=float(as_float(r.get("entry_price")) or 0),
                close_time=ex,
                close_ts=ex_ts,
                close_price=float(as_float(r.get("close_price")) or 0),
                close_reason=str(r.get("close_reason") or ""),
                realized_pnl_pct=float(as_float(r.get("realized_pnl_pct")) or 0),
                continuation_quality_score=float(
                    as_float(r.get("continuation_quality_score")) or 0
                ),
                mfe_pct=float(as_float(r.get("mfe_pct")) or 0),
                mae_pct=float(as_float(r.get("mae_pct")) or 0),
            )
        )
    out.sort(key=lambda p: (p.entry_ts, p.close_ts))
    return out


def build_timeline(
    positions: Sequence[HybridPosition],
) -> tuple[list[dict[str, Any]], int, int]:
    """Chronological entry/exit events with open-slot state after each event."""
    raw: list[tuple[float, str, HybridPosition]] = []
    for p in positions:
        raw.append((p.entry_ts, "entry", p))
        raw.append((p.close_ts, "exit", p))
    raw.sort(key=lambda x: (x[0], 0 if x[1] == "exit" else 1))

    open_slots: list[tuple[float, float, str]] = []
    timeline: list[dict[str, Any]] = []
    max_open = 0
    cap_violations = 0

    for ts, kind, pos in raw:
        sym = pos.symbol
        if kind == "entry":
            open_at_entry = [(a, b, s) for a, b, s in open_slots if a <= ts < b]
            open_syms = {s for _, _, s in open_at_entry}
            if len(open_at_entry) >= MAX_CONCURRENT and sym not in open_syms:
                cap_violations += 1
            open_slots.append((pos.entry_ts, pos.close_ts, sym))
        else:
            open_slots = [(a, b, s) for a, b, s in open_slots if not (s == sym and abs(a - pos.entry_ts) < 1)]

        open_now = [(a, b, s) for a, b, s in open_slots if a <= ts < b]
        open_count = len(open_now)
        max_open = max(max_open, open_count)
        if open_count > MAX_CONCURRENT:
            cap_violations += 1

        timeline.append(
            {
                "event_kind": kind,
                "session_id": pos.session_id,
                "symbol": sym,
                "event_time": pos.entry_time if kind == "entry" else pos.close_time,
                "event_ts": ts,
                "entry_time": pos.entry_time,
                "close_time": pos.close_time,
                "close_reason": pos.close_reason if kind == "exit" else "",
                "realized_pnl_pct": pos.realized_pnl_pct if kind == "exit" else None,
                "open_slots_after": open_count,
                "open_symbols_after": ",".join(sorted({s for _, _, s in open_now})),
            }
        )

    return timeline, max_open, cap_violations


def detect_fade_switches(
    positions: Sequence[HybridPosition],
    *,
    session_id: str,
    fade_only: bool = True,
) -> list[dict[str, Any]]:
    """Cross-symbol switch: exit (fade) → different symbol entry within MAX_PAIR_SEC."""
    reasons = FADE_EXIT_REASONS if fade_only else SWITCH_EXIT_REASONS
    by_entry = sorted(positions, key=lambda p: p.entry_ts)
    switches: list[dict[str, Any]] = []

    for old in positions:
        if old.close_reason not in reasons:
            continue
        best: Optional[HybridPosition] = None
        best_gap = 1e18
        for new in by_entry:
            if new.symbol == old.symbol:
                continue
            gap = new.entry_ts - old.close_ts
            if gap < 0 or gap > MAX_PAIR_SEC:
                continue
            if gap < best_gap:
                best = new
                best_gap = gap
        if not best:
            continue
        switches.append(
            {
                "session_id": session_id,
                "old_symbol": old.symbol,
                "new_symbol": best.symbol,
                "old_exit_reason": old.close_reason,
                "old_entry_time": old.entry_time,
                "old_close_time": old.close_time,
                "new_entry_time": best.entry_time,
                "switch_gap_sec": round(best_gap, 1),
                "old_pnl_at_exit": old.realized_pnl_pct,
                "new_quality": best.continuation_quality_score,
            }
        )
    return switches


def annotate_switch_whatif(
    switches: Sequence[Mapping[str, Any]],
    *,
    scenario: str,
) -> list[dict[str, Any]]:
    """Tag switches with counterfactual policy flags (review only, no PnL here)."""
    rows: list[dict[str, Any]] = []
    for sw in switches:
        row = dict(sw)
        if scenario == "A_live_timeline":
            row["policy_would_block"] = False
            row["policy_scenario"] = scenario
        elif scenario in ("B_fade_switch_block", "C_fade_switch_cooldown"):
            fade = str(sw.get("old_exit_reason") or "") in FADE_EXIT_REASONS
            row["policy_would_block"] = fade
            row["policy_scenario"] = scenario
        else:
            row["policy_would_block"] = False
            row["policy_scenario"] = scenario
        rows.append(row)
    return rows


def build_hybrid_session(session_dir: Path) -> HybridReplaySession:
    session_dir = Path(session_dir)
    session_id = (
        _norm_session_id(str(session_dir.relative_to(session_dir.parent.parent)))
        if session_dir.parent.parent
        else _norm_session_id(session_dir.name)
    )
    positions = load_hybrid_positions(session_dir, session_id=session_id)
    timeline, max_open, cap_viol = build_timeline(positions)
    fade_sw = detect_fade_switches(positions, session_id=session_id, fade_only=True)
    events = _load_events(session_dir)
    accepted_n = sum(1 for e in events if str(e.get("event_type") or "") == "accepted")

    return HybridReplaySession(
        session_id=session_id,
        positions=positions,
        timeline=timeline,
        fade_switches=fade_sw,
        max_open_slots_observed=max_open,
        cap_violation_count=cap_viol,
        accepted_events_count=accepted_n,
    )
