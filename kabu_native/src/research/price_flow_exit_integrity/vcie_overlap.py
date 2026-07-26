"""VCIE X4 overlap audit (incl. 285A.T case study)."""
from __future__ import annotations

from typing import Any, Sequence

from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block
from research.price_flow_exit_integrity.portfolio import filter_no_overlap, replay_cap5
from research.price_flow_exit_integrity.trades import SimTrade


def _block(trades: Sequence[SimTrade]) -> dict[str, Any]:
    pnls = [t.pnl_5bps for t in trades]
    b = pnl_metric_block(pnls, pnls) if pnls else {"n": 0, "total_pnl_5bps": 0.0, "PF_5bps": None}
    return {
        "n": len(trades),
        "pnl_5bps": round(float(b.get("total_pnl_5bps") or 0), 2),
        "PF_5bps": b.get("PF_5bps"),
    }


def first_entry_only(trades: Sequence[SimTrade]) -> list[SimTrade]:
    seen: set[tuple[str, str]] = set()
    out = []
    for t in sorted(trades, key=lambda x: (x.day, x.entry_time, x.symbol)):
        k = (t.day, t.symbol)
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def one_impulse_one_entry(trades: Sequence[SimTrade]) -> list[SimTrade]:
    seen: set[tuple[str, str, str]] = set()
    out = []
    for t in sorted(trades, key=lambda x: (x.day, x.entry_time, x.symbol)):
        k = (t.day, t.symbol, t.impulse_episode_id)
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def vcie_overlap_audit(trades_x4: Sequence[SimTrade], *, focus_day: str = "20260724", focus_symbol: str = "285A.T") -> dict[str, Any]:
    all_ind = list(trades_x4)
    first = first_entry_only(all_ind)
    no_ov, dropped = filter_no_overlap(all_ind)
    one_ep = one_impulse_one_entry(all_ind)
    cap = replay_cap5(no_ov, portfolio_id="VCIE_X4_NOOV_CAP5")

    focus = [t for t in all_ind if t.day == focus_day and t.symbol == focus_symbol]
    focus = sorted(focus, key=lambda t: t.entry_time)
    focus_rows = []
    # mark overlaps among focus
    for i, a in enumerate(focus):
        ov = []
        for j, b in enumerate(focus):
            if i == j:
                continue
            if b.entry_time < a.exit_time and a.entry_time < b.exit_time:
                ov.append(b.setup_id)
        adoptable = a.setup_id in {t.setup_id for t in no_ov} or (
            not any(b.entry_time < a.entry_time and b.exit_time > a.entry_time for b in focus)
        )
        # first-wins adoptable
        adoptable = False
        open_until = None
        for t in focus:
            if open_until is not None and t.entry_time < open_until:
                if t.setup_id == a.setup_id:
                    adoptable = False
                    break
                continue
            if t.setup_id == a.setup_id:
                adoptable = True
                break
            open_until = t.exit_time
        focus_rows.append(
            {
                "day": a.day,
                "symbol": a.symbol,
                "setup_id": a.setup_id,
                "entry_time": a.entry_time.isoformat(),
                "exit_time": a.exit_time.isoformat(),
                "hold_sec": a.hold_sec,
                "pnl_5bps": a.pnl_5bps,
                "exit_reason": a.exit_reason,
                "overlaps_with": ",".join(ov),
                "adoptable_no_overlap": adoptable,
            }
        )

    pnl_all = sum(t.pnl_5bps for t in focus)
    pnl_keep = sum(t.pnl_5bps for t in focus if t.setup_id in {x.setup_id for x in no_ov})

    return {
        "comparisons": [
            {"policy": "all_entry_independent", **_block(all_ind)},
            {"policy": "first_entry_only", **_block(first)},
            {"policy": "no_overlap", **_block(no_ov)},
            {"policy": "no_overlap_cap5", **_block(cap.trades)},
            {"policy": "one_impulse_episode_one_entry", **_block(one_ep)},
        ],
        "dropped_overlap_n": len(dropped),
        "focus_285A": {
            "day": focus_day,
            "symbol": focus_symbol,
            "n_entries": len(focus),
            "entries": focus_rows,
            "pnl_before_overlap_filter": round(pnl_all, 2),
            "pnl_after_no_overlap": round(pnl_keep, 2),
        },
    }
