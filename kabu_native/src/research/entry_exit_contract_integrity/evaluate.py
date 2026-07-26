"""Re-evaluate matched EXIT with integrity metrics (ENTRY/EXIT rules unchanged)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from research.entry_exit_contract.contract import EntryContract
from research.entry_exit_contract.exits import path_for_contract, simulate_current_exit, simulate_generic_x6, simulate_matched_exit
from research.entry_exit_contract_integrity.execution import execution_ladder, summarize_reality
from research.entry_exit_contract_integrity.metrics import economic_success_block, mfe_capture_block
from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block
from research.price_flow_exit.exit_rules import ExitParams
from research.price_flow_exit_integrity.dd import summarize_dd
from research.price_flow_exit_integrity.dependency import dependency_audit
from research.price_flow_exit_integrity.trades import SimTrade
from research.volume_confirmed_impulse_entry.push_loader import PushTick


def evaluate_matched(
    contracts: Sequence[EntryContract],
    push_by_day: dict[str, dict[str, list[PushTick]]],
    *,
    oos_days: Sequence[str],
    params: ExitParams,
    also_baselines: bool = True,
    light: bool = False,
) -> dict[str, Any]:
    rows = []
    trades: list[SimTrade] = []
    for c in contracts:
        if c.day not in oos_days:
            continue
        ticks = (push_by_day.get(c.day) or {}).get(c.symbol) or []
        if not ticks:
            continue
        path = path_for_contract(c, ticks)
        ex = simulate_matched_exit(c, path)
        if light:
            row = {
                "strategy_id": c.strategy_id,
                "day": c.day,
                "symbol": c.symbol,
                "session": c.session,
                "entry_time": c.entry_time.isoformat(),
                "exit_time": ex.exit_time.isoformat(),
                "entry_price": c.entry_price,
                "exit_price": ex.exit_price,
                "exit_reason": ex.exit_reason,
                "hold_sec": ex.hold_sec,
                "setup_id": c.setup_id,
                "episode_id": c.episode_id,
                "pnl_5bps": ex.pnl_5bps,
                "expected_achieved": ex.expected_achieved,
                "structural_success": bool(ex.expected_achieved),
                "economic_success": False,
                "captured_success": False,
                "under_captured_success": False,
                "execution": {},
            }
        else:
            mfe_blk = mfe_capture_block(c, path, ex)
            ladder = execution_ladder(c, path, exit_time=ex.exit_time, exit_price=ex.exit_price)
            econ = economic_success_block(c, path, ex, mfe_blk, bid_qty_at_exit=ladder.get("bid_qty"))
            row = {
                "strategy_id": c.strategy_id,
                "day": c.day,
                "symbol": c.symbol,
                "session": c.session,
                "entry_time": c.entry_time.isoformat(),
                "exit_time": ex.exit_time.isoformat(),
                "entry_price": c.entry_price,
                "exit_price": ex.exit_price,
                "exit_reason": ex.exit_reason,
                "hold_sec": ex.hold_sec,
                "setup_id": c.setup_id,
                "episode_id": c.episode_id,
                "pnl_5bps": ex.pnl_5bps,
                "expected_achieved": ex.expected_achieved,
                **mfe_blk,
                **econ,
                "execution": ladder,
            }
        rows.append(row)
        trades.append(
            SimTrade(
                day=c.day,
                symbol=c.symbol,
                entry_time=c.entry_time,
                exit_time=ex.exit_time,
                entry_price=c.entry_price,
                exit_price=ex.exit_price,
                exit_reason=ex.exit_reason,
                pnl_5bps=float(ex.pnl_5bps),
                hold_sec=float(ex.hold_sec),
                entry_method=c.strategy_id,
                cohort=c.strategy_id,
                setup_id=c.setup_id,
                impulse_episode_id=c.episode_id,
                breakout_episode_id=c.episode_id,
                pbv2=False,
                vcie=True,
                mode="M2",
                session=c.session,
            )
        )

    baselines = {}
    if also_baselines and not light:
        for mode, fn in (("M0", simulate_current_exit), ("M1", lambda c, p: simulate_generic_x6(c, p, params))):
            pnls = []
            for c in contracts:
                if c.day not in oos_days:
                    continue
                ticks = (push_by_day.get(c.day) or {}).get(c.symbol) or []
                if not ticks:
                    continue
                path = path_for_contract(c, ticks)
                ex = fn(c, path)
                pnls.append(float(ex.pnl_5bps))
            baselines[mode] = pnl_metric_block(pnls, pnls) if pnls else {"n": 0, "total_pnl_5bps": 0.0, "PF_5bps": None}

    return _summarize(rows, trades, baselines)


def _summarize(rows: list[dict[str, Any]], trades: list[SimTrade], baselines: dict[str, Any]) -> dict[str, Any]:
    pnls = [float(r["pnl_5bps"]) for r in rows]
    block = pnl_metric_block(pnls, pnls) if pnls else {"n": 0, "total_pnl_5bps": 0.0, "PF_5bps": None}
    dd = summarize_dd(trades)
    n = max(1, len(rows))
    struct = sum(1 for r in rows if r.get("structural_success"))
    econ = sum(1 for r in rows if r.get("economic_success"))
    capd = sum(1 for r in rows if r.get("captured_success"))
    under = sum(1 for r in rows if r.get("under_captured_success"))
    caps = [float(r["capture_ratio_positive_mfe_only"]) for r in rows if r.get("capture_ratio_positive_mfe_only") is not None]
    reality = {
        "R0": summarize_reality(rows, "R0_pnl_5bps"),
        "R1": summarize_reality(rows, "R1_pnl_5bps"),
        "R2": summarize_reality(rows, "R2_pnl_5bps"),
        "R3": summarize_reality(rows, "R3_pnl_5bps"),
        "R4": summarize_reality(rows, "R4_pnl_5bps"),
        "R5": summarize_reality(rows, "R5_pnl_5bps"),
    }
    dep = dependency_audit(trades, label="matched") if trades else {}
    by_day = defaultdict(float)
    for r in rows:
        by_day[r["day"]] += float(r["pnl_5bps"])
    return {
        "n": len(rows),
        "structural_success_rate": round(struct / n, 4),
        "economic_success_rate": round(econ / n, 4),
        "captured_success_rate": round(capd / n, 4),
        "under_captured_success_rate": round(under / n, 4),
        "structural_n": struct,
        "economic_n": econ,
        "captured_n": capd,
        "under_captured_n": under,
        "mean_capture_ratio_positive_mfe_only": round(sum(caps) / len(caps), 4) if caps else None,
        "median_capture_ratio_positive_mfe_only": round(sorted(caps)[len(caps) // 2], 4) if caps else None,
        "pos_days": sum(1 for v in by_day.values() if v > 0),
        "neg_days": sum(1 for v in by_day.values() if v < 0),
        "reality": reality,
        "dependency": {
            k: dep.get(k)
            for k in (
                "top1_symbol_pnl_share",
                "top1_day_pnl_share",
                "pf_after_exclude_max_symbol",
                "pf_after_exclude_max_day",
                "dependency_blocked",
                "verdict",
            )
        },
        "baselines": baselines,
        "sample_rows": rows[:60],
        "trades": trades,
        **block,
        "dd_trade_sequence_max_dd": dd.get("trade_sequence_max_dd"),
        "dd_intraday_max_dd": dd.get("intraday_max_dd"),
        "dd_daily_close_max_dd": dd.get("daily_close_max_dd"),
        "avg_hold_sec": round(sum(t.hold_sec for t in trades) / max(1, len(trades)), 2) if trades else None,
        "median_hold_sec": round(sorted(t.hold_sec for t in trades)[len(trades) // 2], 2) if trades else None,
    }


def pairing_verdict_v2(
    m2: dict[str, Any],
    *,
    cap5_pnl: float | None,
    oos_n: int,
) -> str:
    b0 = (m2.get("baselines") or {}).get("M0") or {}
    b1 = (m2.get("baselines") or {}).get("M1") or {}
    pnl = float(m2.get("total_pnl_5bps") or 0)
    pf = float(m2.get("PF_5bps") or 0)
    pnl0 = float(b0.get("total_pnl_5bps") or 0)
    pf0 = float(b0.get("PF_5bps") or 0)
    pnl1 = float(b1.get("total_pnl_5bps") or 0)
    pf1 = float(b1.get("PF_5bps") or 0)
    r1 = (m2.get("reality") or {}).get("R1") or {}
    r3 = (m2.get("reality") or {}).get("R3") or {}
    dep_blocked = bool((m2.get("dependency") or {}).get("dependency_blocked"))
    better = pnl > pnl0 and pf > pf0 and pnl >= pnl1 and pf >= pf1
    if not better:
        return "ENTRY_EXIT_PAIRING_NO_EDGE"
    edge = (
        pnl > 0
        and pf > 1
        and (cap5_pnl is not None and cap5_pnl > 0)
        and float(r1.get("PF_5bps") or 0) > 1
        and float(r3.get("PF_5bps") or 0) > 1
        and int(m2.get("pos_days") or 0) > int(m2.get("neg_days") or 0)
        and not dep_blocked
        and oos_n >= 10
    )
    if edge:
        return "ENTRY_EXIT_PAIRING_EDGE"
    if pf <= 1 or pnl <= 0:
        return "ENTRY_EXIT_PAIRING_RELATIVE_UPLIFT"
    return "ENTRY_EXIT_PAIRING_RELATIVE_UPLIFT"


def turnover_stats(trades: Sequence[SimTrade], *, oos_days: Sequence[str], episodes_n: int) -> dict[str, Any]:
    n_days = max(1, len(oos_days))
    holds = [t.hold_sec for t in trades]
    by_sym_day: dict[tuple, int] = defaultdict(int)
    for t in trades:
        by_sym_day[(t.day, t.symbol)] += 1
    # approximate hours: 5h AM+PM session per day
    hours = n_days * 5.0
    cost = sum(abs(t.entry_price) * 100 * 0.0005 for t in trades)  # 5bps yen proxy
    gross = sum(t.pnl_5bps + abs(t.entry_price) * 100 * 0.0005 for t in trades)
    return {
        "trades": len(trades),
        "trades_per_day": round(len(trades) / n_days, 2),
        "trades_per_hour": round(len(trades) / hours, 2),
        "roundtrip_turnover": len(trades),
        "avg_hold_sec": round(sum(holds) / len(holds), 2) if holds else None,
        "median_hold_sec": round(sorted(holds)[len(holds) // 2], 2) if holds else None,
        "same_symbol_entries_per_day_max": max(by_sym_day.values()) if by_sym_day else 0,
        "episodes_per_day": round(episodes_n / n_days, 2),
        "cost_total_yen_proxy": round(cost, 2),
        "gross_edge_before_cost": round(gross, 2),
        "net_edge_after_cost": round(sum(t.pnl_5bps for t in trades), 2),
    }
