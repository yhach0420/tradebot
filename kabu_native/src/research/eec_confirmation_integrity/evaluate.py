"""Delay buckets, Ask ENTRY scenarios, C0–C4, dependency detail."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Optional, Sequence

from research.eec_confirmation_integrity.causal import (
    ask_plus_ticks,
    audit_candidate,
    next_ask_after,
    simulate_ask_trade,
)
from research.eec_confirmation_integrity.constants import CAP, DELAY_BUCKETS, FROZEN_NOISE, MAX_CONFIRM_SEC
from research.eec_noise_hysteresis.path_util import path_with_lookback
from research.entry_exit_contract.contract import EntryContract
from research.entry_exit_contract.exits import path_for_contract, simulate_matched_exit
from research.entry_exit_contract_integrity.execution import execution_ladder, summarize_reality
from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block
from research.price_flow_exit_integrity.dd import summarize_dd
from research.price_flow_exit_integrity.dependency import dependency_audit
from research.price_flow_exit_integrity.portfolio import filter_no_overlap, replay_cap5
from research.price_flow_exit_integrity.trades import SimTrade
from research.volume_confirmed_impulse_entry.push_loader import PushTick


def _bucket(delay: Optional[float], cross_session: bool) -> str:
    if cross_session:
        return "session_cross"
    if delay is None:
        return "no_confirm"
    d = float(delay)
    if 0 <= d <= 5:
        return "0_5"
    if d <= 15:
        return "6_15"
    if d <= 30:
        return "16_30"
    if d <= 60:
        return "31_60"
    if d <= 120:
        return "61_120"
    if d <= 180:
        return "121_180"
    if d <= 300:
        return "181_300"
    return "301_plus"


def _to_trades(rows: Sequence[dict[str, Any]], *, mode: str) -> list[SimTrade]:
    out = []
    for r in rows:
        if r.get("pnl_5bps") is None or r.get("skip"):
            continue
        out.append(
            SimTrade(
                day=r["day"],
                symbol=r["symbol"],
                entry_time=datetime.fromisoformat(r["entry_time"]),
                exit_time=datetime.fromisoformat(r["exit_time"]),
                entry_price=float(r["entry_price"]),
                exit_price=float(r["exit_price"]),
                exit_reason=str(r.get("exit_reason") or ""),
                pnl_5bps=float(r["pnl_5bps"]),
                hold_sec=float(r.get("hold_sec") or 0),
                entry_method="EC2",
                cohort="EC2",
                setup_id=str(r.get("setup_id") or ""),
                impulse_episode_id=str(r.get("episode_id") or ""),
                breakout_episode_id=str(r.get("episode_id") or ""),
                pbv2=False,
                vcie=True,
                mode=mode,
                session=str(r.get("session") or "AM"),
            )
        )
    return out


def summarize_trades(rows: Sequence[dict[str, Any]], *, oos_days: Sequence[str], mode: str) -> dict[str, Any]:
    traded = [r for r in rows if not r.get("skip") and r.get("pnl_5bps") is not None]
    pnls = [float(r["pnl_5bps"]) for r in traded]
    block = pnl_metric_block(pnls, pnls) if pnls else {"n": 0, "total_pnl_5bps": 0.0, "PF_5bps": None}
    trades = _to_trades(traded, mode=mode)
    # attach execution ladder reality when present
    reality = {}
    if any("execution" in r for r in traded):
        reality = {
            "R0": summarize_reality(traded, "R0_pnl_5bps"),
            "R1": summarize_reality(traded, "R1_pnl_5bps"),
            "R3": summarize_reality(traded, "R3_pnl_5bps"),
        }
    dep = dependency_audit(trades, label=mode) if trades else {"dependency_blocked": False}
    dd = summarize_dd(trades) if trades else {}
    kept, _ = filter_no_overlap(sorted(trades, key=lambda t: (t.entry_time, t.setup_id)))
    cap = replay_cap5(kept, portfolio_id=mode, cap=CAP)
    by_day = defaultdict(float)
    for t in trades:
        by_day[t.day] += t.pnl_5bps
    return {
        "mode": mode,
        "n_traded": len(traded),
        "n_skipped": sum(1 for r in rows if r.get("skip")),
        **block,
        "trades_per_day": round(len(traded) / max(1, len(oos_days)), 2),
        "pos_days": sum(1 for v in by_day.values() if v > 0),
        "neg_days": sum(1 for v in by_day.values() if v < 0),
        "reality": reality,
        "dependency": dep,
        "dependency_blocked": bool(dep.get("dependency_blocked")),
        "dd_trade_sequence_max_dd": dd.get("trade_sequence_max_dd"),
        "cap5": cap.summary(),
        "sample_rows": traded[:40],
    }


def delay_bucket_report(audit_rows: Sequence[dict], trade_by_setup: dict[str, dict]) -> dict[str, Any]:
    buckets: dict[str, list] = defaultdict(list)
    for a in audit_rows:
        b = _bucket(a.get("confirmation_delay_sec"), bool(a.get("confirmation_after_session_break")))
        tr = trade_by_setup.get(a["setup_id"])
        if tr:
            buckets[b].append({**a, **tr})
        else:
            buckets[b].append(a)
    out = {}
    for name in [x[0] for x in DELAY_BUCKETS] + ["session_cross", "no_confirm"]:
        xs = buckets.get(name) or []
        pnls = [float(x["pnl_5bps"]) for x in xs if x.get("pnl_5bps") is not None and not x.get("skip")]
        block = pnl_metric_block(pnls, pnls) if pnls else {"n": 0, "total_pnl_5bps": 0.0, "PF_5bps": None}
        # reality if execution present
        traded = [x for x in xs if x.get("pnl_5bps") is not None and not x.get("skip")]
        r1 = summarize_reality(traded, "R1_pnl_5bps") if traded and any(x.get("execution") for x in traded) else {}
        r3 = summarize_reality(traded, "R3_pnl_5bps") if traded and any(x.get("execution") for x in traded) else {}
        trades = _to_trades(traded, mode=f"B_{name}")
        kept, _ = filter_no_overlap(sorted(trades, key=lambda t: (t.entry_time, t.setup_id)))
        cap = replay_cap5(kept, portfolio_id=f"B_{name}", cap=CAP).summary() if kept else {}
        by_sym = defaultdict(float)
        by_day = defaultdict(float)
        for t in trades:
            by_sym[t.symbol] += t.pnl_5bps
            by_day[t.day] += t.pnl_5bps
        out[name] = {
            "n": len(xs),
            "n_traded": len(traded),
            **block,
            "R1_PF": r1.get("PF_5bps"),
            "R3_PF": r3.get("PF_5bps"),
            "cap5_pnl": cap.get("pnl_5bps"),
            "top_symbol": max(by_sym.items(), key=lambda kv: kv[1])[0] if by_sym else None,
            "day_pnl": {k: round(v, 2) for k, v in by_day.items()},
            "primary_eval": name not in ("181_300", "301_plus", "session_cross", "no_confirm"),
        }
    return out


def build_population_audits(
    contracts: Sequence[EntryContract],
    push: dict,
    *,
    oos_days: Sequence[str],
) -> list[dict[str, Any]]:
    oos = [c for c in contracts if c.day in oos_days]
    peers = oos
    rows = []
    for c in oos:
        ticks = (push.get(c.day) or {}).get(c.symbol) or []
        if not ticks:
            continue
        rows.append(audit_candidate(c, ticks, peers=peers, noise=FROZEN_NOISE))
    return rows


def run_cohorts(
    contracts: Sequence[EntryContract],
    push: dict,
    audit_rows: Sequence[dict[str, Any]],
    *,
    oos_days: Sequence[str],
) -> dict[str, Any]:
    by_setup = {c.setup_id: c for c in contracts if c.day in oos_days}
    audit_by = {a["setup_id"]: a for a in audit_rows}

    def sim_a0(c: EntryContract) -> Optional[dict]:
        ticks = (push.get(c.day) or {}).get(c.symbol) or []
        path = path_for_contract(c, ticks)
        if not path:
            return None
        ex = simulate_matched_exit(c, path)
        ladder = execution_ladder(c, path, exit_time=ex.exit_time, exit_price=ex.exit_price)
        return {
            "setup_id": c.setup_id,
            "episode_id": c.episode_id,
            "day": c.day,
            "symbol": c.symbol,
            "session": c.session,
            "entry_time": c.entry_time.isoformat(),
            "exit_time": ex.exit_time.isoformat(),
            "entry_price": c.entry_price,
            "exit_price": ex.exit_price,
            "exit_reason": ex.exit_reason,
            "pnl_5bps": float(ex.pnl_5bps),
            "hold_sec": float(ex.hold_sec),
            "skip": False,
            "execution": ladder,
        }

    def sim_raw_a1(c: EntryContract, a: dict) -> Optional[dict]:
        """C1 reference: unrestricted N1 (may use px fallback as v3)."""
        if not a.get("confirmed_raw") or a.get("confirmation_time") is None:
            return {"setup_id": c.setup_id, "day": c.day, "symbol": c.symbol, "session": c.session, "skip": True, "pnl_5bps": None}
        ticks = (push.get(c.day) or {}).get(c.symbol) or []
        path, _ = path_with_lookback(ticks, c.entry_time)
        ct = datetime.fromisoformat(a["confirmation_time"])
        # use ask if ok else px at confirm bar
        entry_px = None
        for b in path:
            if b.t == ct or (b.t >= ct):
                if b.ask is not None and b.ask > 0:
                    entry_px = float(b.ask)
                else:
                    entry_px = float(b.px)
                ct = b.t
                break
        if entry_px is None:
            return {"setup_id": c.setup_id, "day": c.day, "symbol": c.symbol, "session": c.session, "skip": True, "pnl_5bps": None}
        tr = simulate_ask_trade(c, ticks, confirm_t=ct, entry_ask=entry_px, path=path)
        if not tr:
            return None
        c2 = c
        from dataclasses import replace

        c2 = replace(c, entry_time=ct, entry_price=entry_px)
        path_post = [b for b in path if b.t >= ct]
        ladder = execution_ladder(c2, path_post, exit_time=datetime.fromisoformat(tr["exit_time"]), exit_price=tr["exit_price"])
        return {**tr, "setup_id": c.setup_id, "episode_id": c.episode_id, "day": c.day, "symbol": c.symbol, "session": c.session, "skip": False, "execution": ladder}

    def sim_strict(c: EntryContract, a: dict, *, entry_mode: str, require_ask_ok: bool = True) -> Optional[dict]:
        if not a.get("causal_ok"):
            return {
                "setup_id": c.setup_id,
                "episode_id": c.episode_id,
                "day": c.day,
                "symbol": c.symbol,
                "session": c.session,
                "skip": True,
                "pnl_5bps": None,
                "reject_reason": a.get("reject_reason"),
            }
        if require_ask_ok and not a.get("execution_ok"):
            return {
                "setup_id": c.setup_id,
                "episode_id": c.episode_id,
                "day": c.day,
                "symbol": c.symbol,
                "session": c.session,
                "skip": True,
                "pnl_5bps": None,
                "reject_reason": a.get("ask_status_at_confirm") or "ASK_NOT_EVALUABLE",
            }
        ticks = (push.get(c.day) or {}).get(c.symbol) or []
        path, _ = path_with_lookback(ticks, c.entry_time)
        ct = datetime.fromisoformat(a["confirmation_time"])
        ask0 = a.get("strict_ask")
        if ask0 is None:
            return {
                "setup_id": c.setup_id,
                "day": c.day,
                "symbol": c.symbol,
                "session": c.session,
                "skip": True,
                "pnl_5bps": None,
                "reject_reason": "ASK_MISSING",
            }
        ask0 = float(ask0)
        if entry_mode == "E0":
            entry_px = ask0
        elif entry_mode == "E1":
            if require_ask_ok:
                entry_px = ask_plus_ticks(ask0, 1)
            else:
                entry_px = ask_plus_ticks(ask0, 1)
        elif entry_mode == "E2":
            nxt = next_ask_after(path, ct, within_sec=None)
            if nxt is None:
                # diagnostic mode may fall back to stored ask field
                if require_ask_ok:
                    return {"setup_id": c.setup_id, "day": c.day, "symbol": c.symbol, "session": c.session, "skip": True, "pnl_5bps": None, "reject_reason": "NO_NEXT_ASK"}
                entry_px = ask0
            else:
                entry_px = nxt
        elif entry_mode == "E3":
            nxt = next_ask_after(path, ct, within_sec=0.5)
            if nxt is None:
                return {"setup_id": c.setup_id, "day": c.day, "symbol": c.symbol, "session": c.session, "skip": True, "pnl_5bps": None, "reject_reason": "NO_ASK_500MS"}
            entry_px = nxt
        elif entry_mode == "E4":
            nxt = next_ask_after(path, ct, within_sec=1.0)
            if nxt is None:
                return {"setup_id": c.setup_id, "day": c.day, "symbol": c.symbol, "session": c.session, "skip": True, "pnl_5bps": None, "reject_reason": "NO_ASK_1S"}
            entry_px = nxt
        else:
            entry_px = ask0
        tr = simulate_ask_trade(c, ticks, confirm_t=ct, entry_ask=entry_px, path=path)
        if not tr:
            return None
        from dataclasses import replace

        c2 = replace(c, entry_time=ct, entry_price=entry_px)
        path_post = [b for b in path if b.t >= ct]
        ladder = execution_ladder(c2, path_post, exit_time=datetime.fromisoformat(tr["exit_time"]), exit_price=tr["exit_price"])
        delay = a.get("confirmation_delay_sec")
        return {
            **tr,
            "setup_id": c.setup_id,
            "episode_id": c.episode_id,
            "day": c.day,
            "symbol": c.symbol,
            "session": c.session,
            "skip": False,
            "execution": ladder,
            "confirmation_delay_sec": delay,
            "entry_mode": entry_mode,
            "ask_status": a.get("ask_status_at_confirm"),
        }

    c0_rows, c1_rows = [], []
    e_rows = {k: [] for k in ("E0", "E1", "E2", "E3", "E4")}
    diag_crossed = []  # causal-timed but using stored ask field even if crossed (v3-like; NOT valid ENTRY)

    for sid, c in by_setup.items():
        a = audit_by.get(sid)
        if not a:
            continue
        r0 = sim_a0(c)
        if r0:
            c0_rows.append(r0)
        r1 = sim_raw_a1(c, a)
        if r1:
            c1_rows.append(r1)
        for em in ("E0", "E1", "E2", "E3", "E4"):
            er = sim_strict(c, a, entry_mode=em, require_ask_ok=True)
            if er:
                e_rows[em].append(er)
        # diagnostic: causal timing + stored ask field (may be crossed) — explains v3 A1 illusion
        dr = sim_strict(c, a, entry_mode="E0", require_ask_ok=False)
        if dr:
            dr["diagnostic"] = "CROSSED_OR_INVALID_ASK_FIELD"
            diag_crossed.append(dr)

    c2_rows = list(e_rows["E0"])
    c3_rows = list(e_rows["E1"])
    c4_rows = list(e_rows["E2"])

    trade_by_setup = {r["setup_id"]: r for r in c1_rows if not r.get("skip")}
    buckets = delay_bucket_report(audit_rows, trade_by_setup)

    exec_scenarios = {k: summarize_trades(v, oos_days=oos_days, mode=k) for k, v in e_rows.items()}
    cohorts = {
        "C0": summarize_trades(c0_rows, oos_days=oos_days, mode="C0"),
        "C1": summarize_trades(c1_rows, oos_days=oos_days, mode="C1"),
        "C2": summarize_trades(c2_rows, oos_days=oos_days, mode="C2"),
        "C3": summarize_trades(c3_rows, oos_days=oos_days, mode="C3"),
        "C4": summarize_trades(c4_rows, oos_days=oos_days, mode="C4"),
        "DIAG_CROSSED_ASK": summarize_trades(diag_crossed, oos_days=oos_days, mode="DIAG_CROSSED_ASK"),
    }
    return {
        "cohorts": cohorts,
        "execution_scenarios": exec_scenarios,
        "delay_buckets": buckets,
        "c2_rows": c2_rows,
        "c1_rows": c1_rows,
        "ask_quote_audit": {
            "causal_ok_n": sum(1 for a in audit_rows if a.get("causal_ok")),
            "execution_ok_n": sum(1 for a in audit_rows if a.get("execution_ok")),
            "v3_raw_crossed_ask_n": sum(1 for a in audit_rows if a.get("v3_raw_used_crossed_ask")),
            "ask_status_counts": _count([a.get("ask_status_at_confirm") for a in audit_rows if a.get("causal_ok")]),
        },
    }


def expiry_counts(audit_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(audit_rows)
    late = sum(1 for a in audit_rows if a.get("reject_reason") == "late_confirmation_gt_180")
    cross = sum(
        1
        for a in audit_rows
        if a.get("reject_reason") in ("cross_session", "across_session_close") or a.get("confirmation_after_session_break")
    )
    new_ep = sum(
        1 for a in audit_rows if a.get("reject_reason") == "new_reclaim_setup" or a.get("confirmation_after_new_reclaim_setup")
    )
    after_inv = sum(1 for a in audit_rows if str(a.get("reject_reason") or "").startswith("after_expiry"))
    expired = sum(1 for a in audit_rows if a.get("episode_end_reason") and a.get("episode_end_reason") != "path_end")
    strict = sum(1 for a in audit_rows if a.get("causal_ok"))
    exec_ok = sum(1 for a in audit_rows if a.get("execution_ok"))
    raw = sum(1 for a in audit_rows if a.get("confirmed_raw"))
    return {
        "n_candidates": n,
        "expired_candidate_n": expired,
        "raw_confirmation_n": raw,
        "late_confirmation_excluded_n": late,
        "cross_session_excluded_n": cross,
        "new_episode_misattr_n": new_ep,
        "confirmation_after_invalidation_n": after_inv,
        "strict_causal_A1_n": strict,
        "strict_causal_ask_executable_n": exec_ok,
        "v3_raw_crossed_ask_n": sum(1 for a in audit_rows if a.get("v3_raw_used_crossed_ask")),
        "reject_reasons": _count([a.get("reject_reason") for a in audit_rows]),
        "expiry_reasons": _count([a.get("episode_end_reason") for a in audit_rows]),
        "primary_delay_excluded_gt_180": sum(
            1
            for a in audit_rows
            if a.get("confirmation_delay_sec") is not None and float(a["confirmation_delay_sec"]) > MAX_CONFIRM_SEC
        ),
    }


def _count(xs):
    d: dict[str, int] = {}
    for x in xs:
        k = str(x or "None")
        d[k] = d.get(k, 0) + 1
    return d


def dependency_detail(c2_summary: dict[str, Any]) -> dict[str, Any]:
    dep = c2_summary.get("dependency") or {}
    # enrich with per-symbol PF
    sym_pnl = dep.get("symbol_pnl") or {}
    sym_n = dep.get("symbol_trades") or {}
    rows = []
    for s, pnl in sorted(sym_pnl.items(), key=lambda kv: -kv[1]):
        rows.append({"symbol": s, "n": sym_n.get(s), "pnl": pnl})
    day_rows = [{"day": d, "pnl": p} for d, p in sorted((dep.get("day_pnl") or {}).items())]
    return {
        **dep,
        "symbol_table": rows[:40],
        "day_table": day_rows,
        "pnl_after_exclude_max_symbol": next(
            (r["pnl_5bps"] for r in (dep.get("leave_one_symbol_out") or []) if r.get("exclude") == dep.get("top1_symbol")),
            None,
        ),
        "pnl_after_exclude_max_day": next(
            (r["pnl_5bps"] for r in (dep.get("leave_one_day_out") or []) if r.get("exclude") == dep.get("top1_day")),
            None,
        ),
    }
