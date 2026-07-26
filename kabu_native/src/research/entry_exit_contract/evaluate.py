"""M0/M1/M2 evaluation, contract audit, coverage, dependency."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional, Sequence

from research.entry_exit_contract.contract import EntryContract, classify_contract
from research.entry_exit_contract.execution import execution_realism
from research.entry_exit_contract.exits import (
    ExitSim,
    path_for_contract,
    simulate_current_exit,
    simulate_generic_x6,
    simulate_matched_exit,
)
from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block
from research.price_flow_exit.exit_rules import ExitParams
from research.price_flow_exit.path_mfe import PathBar, compute_executable_mfe
from research.price_flow_exit.entries import FixedEntry
from research.price_flow_exit_integrity.dd import summarize_dd
from research.price_flow_exit_integrity.dependency import dependency_audit
from research.price_flow_exit_integrity.trades import SimTrade
from research.volume_confirmed_impulse_entry.push_loader import PushTick


def _mfe_capture(c: EntryContract, path: Sequence[PathBar], ex: ExitSim) -> tuple[Optional[float], Optional[float], Optional[float]]:
    fe = FixedEntry(
        day=c.day,
        symbol=c.symbol,
        entry_time=c.entry_time,
        entry_price=c.entry_price,
        entry_method=c.strategy_id,
        cohort=c.strategy_id,
        setup_id=c.setup_id,
    )
    mfe = compute_executable_mfe(fe, path)
    cap = None
    if mfe.mfe_5bps is not None and mfe.mfe_5bps > 0:
        cap = ex.pnl_5bps / mfe.mfe_5bps
    return mfe.mfe_5bps, mfe.mae_5bps, cap


def _same_episode_regret(path: Sequence[PathBar], ex: ExitSim, entry_price: float) -> Optional[float]:
    peak = None
    for b in path:
        if b.t <= ex.exit_time:
            continue
        if b.bid is not None:
            peak = b.bid if peak is None else max(peak, b.bid)
    if peak is None:
        return None
    return (peak - ex.exit_price) / entry_price * 100.0


def evaluate_mode(
    contracts: Sequence[EntryContract],
    push_by_day: dict[str, dict[str, list[PushTick]]],
    *,
    mode: str,
    params: ExitParams,
    oos_days: Sequence[str],
) -> dict[str, Any]:
    """mode: M0 current | M1 generic_x6 | M2 matched"""
    rows = []
    trades: list[SimTrade] = []
    for c in contracts:
        if c.day not in oos_days:
            continue
        ticks = (push_by_day.get(c.day) or {}).get(c.symbol) or []
        if not ticks:
            continue
        path = path_for_contract(c, ticks)
        if mode == "M0":
            ex = simulate_current_exit(c, path)
        elif mode == "M1":
            ex = simulate_generic_x6(c, path, params)
        else:
            ex = simulate_matched_exit(c, path)
        mfe5, mae5, cap = _mfe_capture(c, path, ex)
        # false invalidation: invalidated but later new high in same episode window
        false_inv = False
        if ex.invalidated_at_sec is not None and path:
            for b in path:
                hold = (b.t - c.entry_time).total_seconds()
                if hold <= (ex.invalidated_at_sec or 0):
                    continue
                if b.t > ex.exit_time and b.px > c.entry_price * 1.003:
                    # rise after exit — check if still same episode (before next structure)
                    if hold - ex.hold_sec <= 120:
                        false_inv = True
                        break
        evaluable = c.volume_quality == "OK" and not ex.quote_not_evaluable
        cls = classify_contract(
            expected_achieved=ex.expected_achieved,
            invalidated=ex.invalidated_at_sec is not None or "invalid" in ex.exit_reason.lower() or "fail" in ex.exit_reason.lower() or "reentry" in ex.exit_reason.lower(),
            invalidated_at_sec=ex.invalidated_at_sec,
            exit_hold_sec=ex.hold_sec,
            pnl_5bps=ex.pnl_5bps,
            capture_ratio=cap,
            evaluable=evaluable,
            false_invalidation=false_inv,
        )
        # contract consistency: matched exit reasons should start with strategy or emergency
        consistent = True
        violation = ""
        if mode == "M2":
            ok_prefix = (c.strategy_id + "-", "hard_stop", "session_close", "path_end", "fallback_")
            if not any(ex.exit_reason.startswith(p) or ex.exit_reason == p.rstrip("-") for p in ok_prefix):
                # allow exact emergency names
                if ex.exit_reason not in ("hard_stop", "session_close", "path_end") and not ex.exit_reason.startswith(c.strategy_id):
                    consistent = False
                    violation = f"unrelated_primary_exit:{ex.exit_reason}"
            # invalidation level must exist at entry
            if c.invalidation_level is None:
                consistent = False
                violation = "missing_invalidation_level"
        regret = _same_episode_regret(path, ex, c.entry_price)
        execr = execution_realism(c, path, exit_time=ex.exit_time, exit_price=ex.exit_price)
        row = {
            **c.to_row(),
            "mode": mode,
            "exit_time": ex.exit_time.isoformat(),
            "exit_price": ex.exit_price,
            "exit_reason": ex.exit_reason,
            "pnl_5bps": ex.pnl_5bps,
            "hold_sec": ex.hold_sec,
            "matched_exit_used": ex.matched_exit_used,
            "fallback_exit_used": ex.fallback_exit_used,
            "invalidated_at_sec": ex.invalidated_at_sec,
            "invalidation_to_exit_sec": (ex.hold_sec - ex.invalidated_at_sec) if ex.invalidated_at_sec is not None else None,
            "expected_achieved": ex.expected_achieved,
            "executable_mfe_5bps": mfe5,
            "executable_mae_5bps": mae5,
            "mfe_capture_ratio": cap,
            "classification": cls,
            "exit_contract_consistent": consistent,
            "contract_violation_reason": violation,
            "same_episode_regret_pct": regret,
            "lost_winner": bool(mfe5 is not None and mfe5 > 0.2 and ex.pnl_5bps < 0),
            "execution": execr,
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
                pnl_5bps=ex.pnl_5bps,
                hold_sec=ex.hold_sec,
                entry_method=c.strategy_id,
                cohort=c.strategy_id,
                setup_id=c.setup_id,
                impulse_episode_id=c.episode_id,
                breakout_episode_id=c.episode_id,
                pbv2=False,
                vcie=True,
                mode=mode,
                session=c.session,
            )
        )
    return _summarize(rows, trades, mode)


def _summarize(rows: list[dict[str, Any]], trades: list[SimTrade], mode: str) -> dict[str, Any]:
    pnls = [float(r["pnl_5bps"]) for r in rows]
    block = pnl_metric_block(pnls, pnls) if pnls else {"n": 0, "total_pnl_5bps": 0.0, "PF_5bps": None}
    dd = summarize_dd(trades)
    by_cls: dict[str, int] = defaultdict(int)
    for r in rows:
        by_cls[r["classification"]] += 1
    n = max(1, len(rows))
    success = by_cls.get("CONTRACT_SUCCESS_CAPTURED", 0) + by_cls.get("CONTRACT_SUCCESS_UNDER_CAPTURED", 0)
    fail = by_cls.get("CONTRACT_FAILED_EXITED_FAST", 0) + by_cls.get("CONTRACT_FAILED_EXITED_LATE", 0)
    lat = [float(r["invalidation_to_exit_sec"]) for r in rows if r.get("invalidation_to_exit_sec") is not None]
    caps = [float(r["mfe_capture_ratio"]) for r in rows if r.get("mfe_capture_ratio") is not None]
    mfes = [float(r["executable_mfe_5bps"]) for r in rows if r.get("executable_mfe_5bps") is not None]
    slip1 = [float(r["execution"]["pnl_1tick_slip"]) for r in rows if (r.get("execution") or {}).get("pnl_1tick_slip") is not None]
    slip2 = [float(r["execution"]["pnl_2tick_slip"]) for r in rows if (r.get("execution") or {}).get("pnl_2tick_slip") is not None]
    d500 = [float(r["execution"]["pnl_500ms_delay"]) for r in rows if (r.get("execution") or {}).get("pnl_500ms_delay") is not None]
    by_day = defaultdict(float)
    for r in rows:
        by_day[r["day"]] += float(r["pnl_5bps"])
    pos = sum(1 for v in by_day.values() if v > 0)
    neg = sum(1 for v in by_day.values() if v < 0)
    dep = dependency_audit(trades, label=mode) if trades else {}
    return {
        "mode": mode,
        "n": len(rows),
        "complete_contract": sum(1 for r in rows if r["classification"] != "CONTRACT_NOT_EVALUABLE"),
        "contract_success_rate": round(success / n, 4),
        "contract_failure_rate": round(fail / n, 4),
        "expected_horizon_achieved_rate": round(sum(1 for r in rows if r.get("expected_achieved")) / n, 4),
        "invalidation_rate": round(sum(1 for r in rows if r.get("invalidated_at_sec") is not None) / n, 4),
        "mean_invalidation_to_exit_sec": round(sum(lat) / len(lat), 2) if lat else None,
        "matched_exit_rate": round(sum(1 for r in rows if r.get("matched_exit_used")) / n, 4),
        "fallback_exit_rate": round(sum(1 for r in rows if r.get("fallback_exit_used")) / n, 4),
        "hard_stop_rate": round(sum(1 for r in rows if "hard_stop" in str(r.get("exit_reason")) or r.get("exit_reason") == "stop_hit") / n, 4),
        "early_stop_rate": round(
            sum(1 for r in rows if ("stop" in str(r.get("exit_reason")) and float(r.get("hold_sec") or 999) <= 300)) / n, 4
        ),
        "np_rate": round(sum(1 for r in rows if "no_progress" in str(r.get("exit_reason"))) / n, 4),
        "winner_rate": round(sum(1 for r in rows if float(r["pnl_5bps"]) > 0) / n, 4),
        "mean_mfe_5bps": round(sum(mfes) / len(mfes), 4) if mfes else None,
        "mean_mfe_capture": round(sum(caps) / len(caps), 4) if caps else None,
        "false_invalidation_n": sum(1 for r in rows if r["classification"] == "CONTRACT_FALSE_INVALIDATION"),
        "lost_winner_n": sum(1 for r in rows if r.get("lost_winner")),
        "mean_same_episode_regret": round(
            sum(float(r["same_episode_regret_pct"]) for r in rows if r.get("same_episode_regret_pct") is not None)
            / max(1, sum(1 for r in rows if r.get("same_episode_regret_pct") is not None)),
            4,
        )
        if any(r.get("same_episode_regret_pct") is not None for r in rows)
        else None,
        "pnl_1tick_slip_total": round(sum(slip1), 2) if slip1 else None,
        "pnl_2tick_slip_total": round(sum(slip2), 2) if slip2 else None,
        "pnl_500ms_delay_total": round(sum(d500), 2) if d500 else None,
        "classification_counts": dict(by_cls),
        "pos_days": pos,
        "neg_days": neg,
        "dependency": {k: dep.get(k) for k in ("top1_symbol_pnl_share", "top1_day_pnl_share", "pf_after_exclude_max_symbol", "pf_after_exclude_max_day", "verdict", "dependency_blocked")},
        "sample_rows": rows[:80],
        "trades": trades,
        **block,
        **{f"dd_{k}": v for k, v in dd.items() if k != "note"},
    }


def pairing_verdict(m0: dict[str, Any], m1: dict[str, Any], m2: dict[str, Any]) -> str:
    def pf(m):
        return float(m.get("PF_5bps") or 0)

    def pnl(m):
        return float(m.get("total_pnl_5bps") or 0)

    def dd(m):
        return float(m.get("dd_trade_sequence_max_dd") or 0)

    better_than_current = pnl(m2) > pnl(m0) and pf(m2) > pf(m0) and dd(m2) >= dd(m0)  # less negative dd is greater
    vs_x6 = pnl(m2) >= pnl(m1) and pf(m2) >= pf(m1)
    if better_than_current and vs_x6:
        return "ENTRY_EXIT_PAIRING_EDGE"
    if better_than_current and not vs_x6:
        return "GENERIC_EARLY_EXIT_EFFECT_ONLY"
    return "ENTRY_EXIT_PAIRING_NO_EDGE"


def coverage_gate(contracts: dict[str, list[EntryContract]], evals: dict[str, Any], oos_days: Sequence[str]) -> dict[str, Any]:
    total_complete = 0
    per = {}
    for sid in ("EC1", "EC2", "EC3"):
        m2 = ((evals.get(sid) or {}).get("M2") or {})
        n_c = int(m2.get("complete_contract") or 0)
        total_complete += n_c
        # trigger concentration
        xs = [c for c in contracts.get(sid) or [] if c.day in oos_days]
        by_day = defaultdict(int)
        by_sym = defaultdict(int)
        for c in xs:
            by_day[c.day] += 1
            by_sym[c.symbol] += 1
        n = max(1, len(xs))
        per[sid] = {
            "entry_n": len(xs),
            "complete_contract": n_c,
            "max_day_trigger_ratio": round(max(by_day.values()) / n, 4) if by_day else None,
            "max_symbol_trigger_ratio": round(max(by_sym.values()) / n, 4) if by_sym else None,
        }
    oos_n = len(oos_days)
    ok = (
        oos_n >= 10
        and total_complete >= 300
        and all(per[s]["complete_contract"] >= 100 for s in per)
        and all((per[s]["max_day_trigger_ratio"] or 1) < 0.40 for s in per)
        and all((per[s]["max_symbol_trigger_ratio"] or 1) < 0.25 for s in per)
    )
    return {
        "oos_days": oos_n,
        "total_complete_contract": total_complete,
        "per_strategy": per,
        "gate_ok": ok,
        "verdict": "ENTRY_EXIT_CONTRACT_FRAMEWORK_READY" if True else "ENTRY_EXIT_CONTRACT_FRAMEWORK_BLOCKED",
        # framework ready if code ran; coverage separate
        "coverage_verdict": "ENTRY_EXIT_CONTRACT_INSUFFICIENT_OOS" if oos_n < 10 else ("COVERAGE_PASS" if ok else "COVERAGE_PARTIAL"),
    }
