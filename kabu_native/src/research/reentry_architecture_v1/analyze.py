"""Episode ledger, mechanism slices, Exact variant economics, candidate bar."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.anchor_economic_sensitivity import EARLY_GUARD, EXIT600, EXTEND750
from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_timing_robustness.metrics import trade_stats
from research.current_entry_nonexec_mechanism.analyze import a0_parity
from research.executable_target_v2_b_threshold.analyze import pack_metrics
from research.reentry_architecture_v1 import (
    EXPECTED_A0_MAXDD,
    EXPECTED_A0_PF,
    EXPECTED_A0_PNL,
    EXPECTED_A0_TRADES,
    EXPECTED_B0_MAXDD,
    EXPECTED_B0_PF,
    EXPECTED_B0_PNL,
    EXPECTED_B0_TRADES,
    MIN_TRADE_RETENTION,
    NEW_FORWARD_N,
    POLICY_NAME,
    TRUE_OOS,
)
from research.uniform10_b_followup.analyze import b0_parity, slim_pack
from research.uniform10_entry_rebuild import UNIFORM10
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import CLOCK_GRID

ORDINALS = ("FIRST", "REENTRY_1", "REENTRY_2", "REENTRY_3_PLUS")


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _pnl(t: dict[str, Any]) -> float:
    return float(t.get("pnl_yen_100") or 0.0)


def _pf_num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if v in ("Infinity", float("inf")):
        return float("inf")
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def slice_stats(xs: list[dict[str, Any]]) -> dict[str, Any]:
    st = trade_stats(xs)
    return {
        "N": int(len(xs)),
        "PnL": st.get("pnl"),
        "PF": st.get("PF"),
        "avg_PnL": st.get("avg_trade"),
        "median_PnL": st.get("median_trade"),
        "win_rate": st.get("win_rate"),
    }


def prior_outcome_label(pnl: Optional[float]) -> str:
    if pnl is None:
        return ""
    if pnl > 1e-9:
        return "WIN"
    if pnl < -1e-9:
        return "LOSS"
    return "DRAW"


def exit_family(reason: Any) -> str:
    r = str(reason or "").strip()
    if not r:
        return "OTHER"
    if r in EARLY_GUARD or r.upper().startswith("IMB"):
        return "EARLY_GUARD"
    if r in EXIT600 or r == "CONT_EXIT_600":
        return "CONT_EXIT_600"
    if r in EXTEND750 or r == "CONT_EXTEND_750":
        return "CONT_EXTEND_750"
    if r == "SESSION_CLOSE":
        return "SESSION_CLOSE"
    return "OTHER"


def ordinal_label(i: int) -> str:
    if i <= 0:
        return "FIRST"
    if i == 1:
        return "REENTRY_1"
    if i == 2:
        return "REENTRY_2"
    return "REENTRY_3_PLUS"


def clock_hm(clock: str) -> tuple[tuple[int, int], ...]:
    if clock == "B":
        return tuple((int(h), int(m)) for h, m in UNIFORM10)
    return tuple((int(h), int(m)) for h, m in CLOCK_GRID)


def anchors_since_exit(*, day: str, clock: str, exit_t: Optional[float], entry_t0: Optional[float]) -> Optional[int]:
    if exit_t is None or entry_t0 is None:
        return None
    n = 0
    for h, m in clock_hm(clock):
        t0 = hm_epoch(day, h, m)
        if t0 > float(exit_t) + 1e-9 and t0 <= float(entry_t0) + 1e-9:
            n += 1
    return n


def anchor_bucket(n: Optional[int]) -> str:
    if n is None:
        return ""
    if n <= 1:
        return "NEXT_ANCHOR"
    if n == 2:
        return "TWO_LATER"
    return "THREE_PLUS"


def _anchor_t0(row: dict[str, Any], clock: str) -> Optional[float]:
    day = str(row.get("date") or "")
    lab = str(row.get("anchor_time") or row.get("anchor") or "")
    if not day or ":" not in lab:
        t = _f(row.get("fill_time"))
        return t
    try:
        h, m = (int(x) for x in lab.split(":"))
        return hm_epoch(day, h, m)
    except (TypeError, ValueError):
        return _f(row.get("fill_time"))


def _rank_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in rows:
        key = (str(r.get("date") or ""), str(r.get("anchor") or ""), str(r.get("symbol") or ""))
        out[key] = r
    return out


def build_episode_ledger(
    trades: list[dict[str, Any]],
    *,
    clock: str,
    universe: str,
    rank_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    idx = _rank_index(rank_rows or [])
    by: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by[(str(t.get("date") or ""), str(t.get("session") or ""), str(t.get("symbol") or ""))].append(t)
    out: list[dict[str, Any]] = []
    for (date, session, symbol), grp in sorted(by.items()):
        grp = sorted(grp, key=lambda x: float(_f(x.get("fill_time")) or 0.0))
        for i, t in enumerate(grp):
            rec = dict(t)
            rec["clock"] = clock
            rec["universe"] = universe
            rec["entry_ordinal"] = i + 1
            rec["entry_kind"] = ordinal_label(i)
            rec["is_reentry"] = i >= 1
            rk = idx.get((date, str(t.get("anchor_time") or ""), symbol), {})
            score = _f(t.get("score"))
            if score is None:
                score = _f(rk.get("score"))
            rank = _f(t.get("candidate_rank"))
            if rank is None:
                rank = _f(rk.get("rank"))
            rec["current_entry_score"] = score
            rec["current_rank"] = rank
            rec["current_executable"] = rk.get("executable_at_t0")
            rec["current_board_state"] = rk.get("board_state")
            rec["current_nonexec_bucket"] = rk.get("nonexec_bucket")
            rec["current_mid"] = _f(t.get("fill_price"))
            rec["current_trade_pnl"] = _pnl(t)
            rec["current_MFE"] = _f(t.get("mfe_yen_100"))
            rec["current_MAE"] = _f(t.get("mae_yen_100"))
            rec["current_exit_reason"] = t.get("exit_reason") or t.get("reason")
            rec["current_hold_sec"] = _f(t.get("holding_sec"))
            for f in FEATURE_ORDER:
                rec[f"feat_{f}"] = _f(t.get(f))
                if rec[f"feat_{f}"] is None:
                    rec[f"feat_{f}"] = _f(rk.get(f))
            if i == 0:
                rec["prior_entry_time"] = None
                rec["prior_exit_time"] = None
                rec["prior_exit_reason"] = None
                rec["prior_trade_pnl"] = None
                rec["prior_trade_MFE"] = None
                rec["prior_trade_MAE"] = None
                rec["prior_hold_sec"] = None
                rec["seconds_since_prior_exit"] = None
                rec["anchors_since_prior_exit"] = None
                rec["anchor_distance"] = ""
                rec["prior_entry_score"] = None
                rec["prior_rank"] = None
                rec["score_delta"] = None
                rec["rank_delta"] = None
                rec["score_improved"] = None
                rec["rank_improved"] = None
                rec["prior_exit_price"] = None
                rec["current_mid_vs_prior_exit"] = None
                rec["price_above_prior_exit"] = None
                rec["prior_outcome"] = ""
            else:
                p = grp[i - 1]
                rec["prior_entry_time"] = _f(p.get("fill_time"))
                rec["prior_exit_time"] = _f(p.get("exit_time"))
                rec["prior_exit_reason"] = p.get("exit_reason") or p.get("reason")
                rec["prior_trade_pnl"] = _pnl(p)
                rec["prior_trade_MFE"] = _f(p.get("mfe_yen_100"))
                rec["prior_trade_MAE"] = _f(p.get("mae_yen_100"))
                rec["prior_hold_sec"] = _f(p.get("holding_sec"))
                cur_fill = _f(t.get("fill_time"))
                prior_exit = _f(p.get("exit_time"))
                rec["seconds_since_prior_exit"] = (
                    round(float(cur_fill) - float(prior_exit), 3) if cur_fill is not None and prior_exit is not None else None
                )
                n_anc = anchors_since_exit(
                    day=date,
                    clock=clock,
                    exit_t=prior_exit,
                    entry_t0=_anchor_t0(t, clock),
                )
                rec["anchors_since_prior_exit"] = n_anc
                rec["anchor_distance"] = anchor_bucket(n_anc)
                p_score = _f(p.get("score"))
                p_rk = idx.get((date, str(p.get("anchor_time") or ""), symbol), {})
                if p_score is None:
                    p_score = _f(p_rk.get("score"))
                p_rank = _f(p.get("candidate_rank"))
                if p_rank is None:
                    p_rank = _f(p_rk.get("rank"))
                rec["prior_entry_score"] = p_score
                rec["prior_rank"] = p_rank
                rec["score_delta"] = (score - p_score) if score is not None and p_score is not None else None
                rec["rank_delta"] = (rank - p_rank) if rank is not None and p_rank is not None else None
                rec["score_improved"] = bool(rec["score_delta"] > 0) if rec["score_delta"] is not None else None
                rec["rank_improved"] = bool(rec["rank_delta"] < 0) if rec["rank_delta"] is not None else None
                rec["prior_exit_price"] = _f(p.get("exit_price"))
                cur_mid = rec["current_mid"]
                prior_px = rec["prior_exit_price"]
                rec["current_mid_vs_prior_exit"] = (
                    (cur_mid - prior_px) if cur_mid is not None and prior_px is not None else None
                )
                rec["price_above_prior_exit"] = (
                    bool(cur_mid > prior_px) if cur_mid is not None and prior_px is not None else None
                )
                rec["prior_outcome"] = prior_outcome_label(rec["prior_trade_pnl"])
            rec["prior_exit_family"] = exit_family(rec.get("prior_exit_reason")) if i else ""
            out.append(rec)
    return out


def first_reentry_split(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    first = [r for r in ledger if r.get("entry_kind") == "FIRST"]
    reent = [r for r in ledger if r.get("is_reentry")]
    return {"first_entry": slice_stats(first), "re_entry": slice_stats(reent)}


def sequence_block(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for lab in ORDINALS:
        body[lab] = slice_stats([r for r in ledger if r.get("entry_kind") == lab])
    reent = [r for r in ledger if r.get("is_reentry")]
    body["AFTER_WIN"] = slice_stats([r for r in reent if r.get("prior_outcome") == "WIN"])
    body["AFTER_DRAW"] = slice_stats([r for r in reent if r.get("prior_outcome") == "DRAW"])
    body["AFTER_LOSS"] = slice_stats([r for r in reent if r.get("prior_outcome") == "LOSS"])
    return body


def grouped_slice(ledger: list[dict[str, Any]], key: str, labels: tuple[str, ...]) -> dict[str, Any]:
    reent = [r for r in ledger if r.get("is_reentry")]
    out: dict[str, Any] = {}
    for lab in labels:
        out[lab] = slice_stats([r for r in reent if str(r.get(key) or "") == lab])
    leftover = [r for r in reent if str(r.get(key) or "") not in labels]
    if leftover:
        out["UNLABELED"] = slice_stats(leftover)
    return out


def score_rank_change_block(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    reent = [r for r in ledger if r.get("is_reentry")]

    def yn(field: str, true: bool) -> list[dict[str, Any]]:
        return [r for r in reent if r.get(field) is true]

    return {
        "score_improved": slice_stats(yn("score_improved", True)),
        "score_not_improved": slice_stats(yn("score_improved", False)),
        "rank_improved": slice_stats(yn("rank_improved", True)),
        "rank_not_improved": slice_stats(yn("rank_improved", False)),
        "price_above_prior_exit": slice_stats(yn("price_above_prior_exit", True)),
        "price_not_above_prior_exit": slice_stats(yn("price_above_prior_exit", False)),
    }


def am_pm_reentry(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    reent = [r for r in ledger if r.get("is_reentry")]
    return {
        "AM": slice_stats([r for r in reent if str(r.get("session") or "") == "AM"]),
        "PM": slice_stats([r for r in reent if str(r.get("session") or "") == "PM"]),
    }


def economic_row(trades: list[dict[str, Any]], days: list[str], ledger: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    pack = pack_metrics(trades, days)
    led = ledger if ledger is not None else build_episode_ledger(trades, clock="A", universe="X")
    split = first_reentry_split(led)
    ex = pack.get("exclude") or {}
    am = (pack.get("AM") or {}) if isinstance(pack.get("AM"), dict) else {}
    pm = (pack.get("PM") or {}) if isinstance(pack.get("PM"), dict) else {}
    return {
        "trades": pack.get("trades"),
        "PnL": pack.get("PnL"),
        "PF": pack.get("PF"),
        "maxDD": pack.get("maxDD"),
        "positive_day_rate": pack.get("positive_day_rate"),
        "median_daily_pnl": pack.get("median_daily_pnl"),
        "first_entry": split["first_entry"],
        "re_entry": split["re_entry"],
        "AM": {"n": am.get("trades"), "PnL": am.get("pnl"), "PF": am.get("PF")},
        "PM": {"n": pm.get("trades"), "PnL": pm.get("pnl"), "PF": pm.get("PF")},
        "ex_top1_trade": ex.get("ex_top1_trade"),
        "ex_top3_trades": ex.get("ex_top3_trades"),
        "ex_best_day": ex.get("ex_best_day"),
        "ex_top3_days": ex.get("ex_top3_days"),
        "ex_top_symbol": ex.get("ex_top_symbol"),
        "ex_top3_symbols": ex.get("ex_top3_symbols"),
        "ex_285A": ex.get("ex_285A"),
        "pack": slim_pack(pack),
        "daily": pack.get("daily"),
        "exclude": ex,
    }


def _ex_pnl(row: dict[str, Any] | None) -> Optional[float]:
    if not row:
        return None
    v = row.get("PnL")
    if v is None:
        v = row.get("pnl")
    return _f(v)


def historical_candidate_vs_a0(cand: dict[str, Any], a0: dict[str, Any]) -> dict[str, Any]:
    pnl = _f(cand.get("PnL"))
    a_pnl = _f(a0.get("PnL"))
    pf = _pf_num(cand.get("PF"))
    a_pf = _pf_num(a0.get("PF"))
    dd = _f(cand.get("maxDD"))
    a_dd = _f(a0.get("maxDD"))
    med = _f(cand.get("median_daily_pnl"))
    a_med = _f(a0.get("median_daily_pnl"))
    pos = _f(cand.get("positive_day_rate"))
    a_pos = _f(a0.get("positive_day_rate"))
    n = _f(cand.get("trades"))
    a_n = _f(a0.get("trades"))
    re = _f((cand.get("re_entry") or {}).get("PnL"))
    a_re = _f((a0.get("re_entry") or {}).get("PnL"))
    ex3t = _ex_pnl(cand.get("ex_top3_trades"))
    a_ex3t = _ex_pnl(a0.get("ex_top3_trades"))
    ex3d = _ex_pnl(cand.get("ex_top3_days"))
    a_ex3d = _ex_pnl(a0.get("ex_top3_days"))
    pnl_ok = pnl is not None and a_pnl is not None and pnl > a_pnl
    pf_ok = pf is not None and a_pf is not None and pf >= a_pf - 1e-12
    dd_ok = dd is not None and a_dd is not None and dd >= a_dd - 1e-9
    daily_ok = (
        (med is not None and a_med is not None and med >= a_med - 1e-9)
        or (pos is not None and a_pos is not None and pos >= a_pos - 1e-12)
    )
    re_ok = re is not None and a_re is not None and re > a_re
    n_ok = n is not None and a_n is not None and n >= float(MIN_TRADE_RETENTION) * a_n - 1e-9
    tail_ok = (
        ex3t is not None
        and a_ex3t is not None
        and ex3t >= a_ex3t - 1e-9
        and ex3d is not None
        and a_ex3d is not None
        and ex3d >= a_ex3d - 1e-9
    )
    ok = bool(pnl_ok and pf_ok and dd_ok and daily_ok and re_ok and n_ok and tail_ok)
    return {
        "ok": ok,
        "pnl_ok": pnl_ok,
        "pf_ok": pf_ok,
        "dd_ok": dd_ok,
        "daily_ok": daily_ok,
        "reentry_loss_improved": re_ok,
        "trade_count_ok": n_ok,
        "ex_top3_ok": tail_ok,
    }


def same_direction(a_delta_pnl: Optional[float], b_delta_pnl: Optional[float]) -> Optional[bool]:
    if a_delta_pnl is None or b_delta_pnl is None:
        return None
    if abs(a_delta_pnl) < 1e-9 and abs(b_delta_pnl) < 1e-9:
        return True
    return (a_delta_pnl > 0 and b_delta_pnl > 0) or (a_delta_pnl < 0 and b_delta_pnl < 0)


def primary_failure_mode(
    *,
    seq: dict[str, Any],
    after: dict[str, Any],
    dist: dict[str, Any],
    chg: dict[str, Any],
    tod: dict[str, Any],
    r1: dict[str, Any] | None,
    a0: dict[str, Any],
) -> str:
    """Precommitted classification. Natural-boundary slices only. No threshold search."""
    r1_pnl = slice_stats([]).get("PnL")
    r1_re = (seq.get("REENTRY_1") or {}).get("PnL")
    r2_re = (seq.get("REENTRY_2") or {}).get("PnL")
    r3_re = (seq.get("REENTRY_3_PLUS") or {}).get("PnL")
    after_win = (after.get("AFTER_WIN") or seq.get("AFTER_WIN") or {}).get("PnL")
    after_loss = (after.get("AFTER_LOSS") or seq.get("AFTER_LOSS") or {}).get("PnL")
    next_a = (dist.get("NEXT_ANCHOR") or {}).get("PnL")
    later = (dist.get("THREE_PLUS") or {}).get("PnL")
    score_up = (chg.get("score_improved") or {}).get("PnL")
    score_dn = (chg.get("score_not_improved") or {}).get("PnL")
    am = (tod.get("AM") or {}).get("PnL")
    pm = (tod.get("PM") or {}).get("PnL")

    def neg(v: Any) -> bool:
        x = _f(v)
        return x is not None and x < 0

    def mag(v: Any) -> float:
        x = _f(v)
        return abs(x) if x is not None else 0.0

    r1_first = _f(((r1 or {}).get("first_entry") or {}).get("PnL")) if r1 else None
    a0_first = _f((a0.get("first_entry") or {}).get("PnL"))
    cap_shift = r1_first is not None and a0_first is not None and (r1_first - a0_first) > mag(a0_first) * 0.15

    if cap_shift:
        return "CAP_OCCUPANCY"
    if neg(after_loss) and mag(after_loss) > mag(after_win) * 1.5 and (after_win is None or _f(after_win) >= 0 or mag(after_loss) > mag(after_win)):
        if neg(after_loss) and (after_win is None or not neg(after_win) or mag(after_loss) >= mag(after_win)):
            return "LOSS_CHASING"
    if (not neg(r1_re) or mag(r1_re) < mag(r2_re) + mag(r3_re)) and (neg(r2_re) or neg(r3_re)) and mag(r2_re) + mag(r3_re) > mag(r1_re):
        return "REPEATED_ENTRY"
    if neg(score_up) and mag(score_up) >= mag(score_dn) * 0.5:
        if neg(later) and (next_a is None or mag(later) >= mag(next_a) * 0.5):
            return "STALE_SETUP"
        return "STALE_SETUP"
    if neg(am) and neg(pm) and abs(mag(am) - mag(pm)) < min(mag(am), mag(pm)) * 0.35:
        pass
    elif mag(am) > 0 and mag(pm) > 0 and max(mag(am), mag(pm)) > 2.0 * min(mag(am), mag(pm)):
        return "TIME_OF_DAY"
    if neg(r1_re) and (neg(r2_re) or _f(r2_re) is None) and (neg(r3_re) or _f((seq.get("REENTRY_3_PLUS") or {}).get("N")) in (None, 0)):
        return "REPEATED_ENTRY" if mag(r2_re) + mag(r3_re) > mag(r1_re) else "STALE_SETUP"
    return "OTHER"


def answers_q(
    *,
    seq: dict[str, Any],
    chg: dict[str, Any],
    dist: dict[str, Any],
    mode: str,
) -> dict[str, str]:
    r1 = seq.get("REENTRY_1") or {}
    r2 = seq.get("REENTRY_2") or {}
    r3 = seq.get("REENTRY_3_PLUS") or {}
    aw = seq.get("AFTER_WIN") or {}
    al = seq.get("AFTER_LOSS") or {}
    nxt = dist.get("NEXT_ANCHOR") or {}
    two = dist.get("TWO_LATER") or {}
    late = dist.get("THREE_PLUS") or {}
    su = chg.get("score_improved") or {}
    sd = chg.get("score_not_improved") or {}
    ru = chg.get("rank_improved") or {}
    rd = chg.get("rank_not_improved") or {}
    pu = chg.get("price_above_prior_exit") or {}
    pd = chg.get("price_not_above_prior_exit") or {}

    def neg(block: dict[str, Any]) -> bool:
        x = _f(block.get("PnL"))
        return x is not None and x < 0

    q1 = (
        "Broad: REENTRY_1 and later slices are both loss-making."
        if neg(r1) and (neg(r2) or int(r2.get("N") or 0) == 0)
        else (
            "Concentrated in REENTRY_2 / REENTRY_3_PLUS; REENTRY_1 is not the whole loss."
            if (not neg(r1) or mag_pnl(r2) + mag_pnl(r3) > mag_pnl(r1))
            else "Loss is present on re-entry but not isolated to one ordinal."
        )
    )
    q2 = (
        "Worse after prior LOSS."
        if mag_pnl(al) > mag_pnl(aw) and neg(al)
        else ("Worse after prior WIN." if mag_pnl(aw) > mag_pnl(al) and neg(aw) else "After-WIN and after-LOSS are similar or mixed.")
    )
    later_still = neg(late) or (int(late.get("N") or 0) == 0 and neg(two))
    q3 = (
        "No. Waiting 3+ anchors does not restore a first-entry-like edge."
        if later_still
        else "Waiting appears less damaging on this clock; not treated as a cooldown search."
    )
    q4 = (
        "Yes. Re-entry remains economically bad even when score/rank improved vs the prior entry."
        if neg(su) or neg(ru)
        else "Improved score/rank re-entries are not the main loss pocket on this clock."
    )
    q5 = (
        "Yes. Recovered vs not-recovered vs prior exit differ."
        if abs(mag_pnl(pu) - mag_pnl(pd)) > min(max(mag_pnl(pu), mag_pnl(pd)), 1.0) * 0.25
        else "No material split on price recovered vs not recovered vs prior exit."
    )
    q6 = mode
    return {"Q1": q1, "Q2": q2, "Q3": q3, "Q4": q4, "Q5": q5, "Q6": q6}


def mag_pnl(block: dict[str, Any] | None) -> float:
    x = _f((block or {}).get("PnL"))
    return abs(x) if x is not None else 0.0


def first_intact(cand: dict[str, Any], a0: dict[str, Any]) -> bool:
    c = _f((cand.get("first_entry") or {}).get("PnL"))
    a = _f((a0.get("first_entry") or {}).get("PnL"))
    if c is None or a is None:
        return False
    return c >= a - abs(a) * 0.05 - 1.0


def decide(
    *,
    a0_ok: bool,
    b0_ok: bool,
    a0: dict[str, Any],
    b0: dict[str, Any],
    a_vars: dict[str, dict[str, Any]],
    b_vars: dict[str, dict[str, Any]],
    a2_vars: dict[str, dict[str, Any]],
    b2_vars: dict[str, dict[str, Any]],
    a_bars: dict[str, dict[str, Any]],
    b_dir: dict[str, Optional[bool]],
    mode: str,
    a_seq: dict[str, Any],
    b_seq: dict[str, Any],
) -> dict[str, Any]:
    if not a0_ok or not b0_ok:
        return {
            "VERDICT": "REENTRY_AUDIT_INTEGRITY_FAILED",
            "BEST_MECHANISM_SUPPORTED_VARIANT": "NONE",
            "CROSS_CLOCK_SUPPORTED": False,
            "EXECUTABILITY_PLUS_REENTRY_PROMISING": False,
            "TRUE_OOS": TRUE_OOS,
            "NEW_FORWARD_N": NEW_FORWARD_N,
            "RECOMMENDED_NEXT_STEP": "STOP. A0/B0 Exact Dual-Lane did not match the frozen headline. Do not interpret R1/R2/R3.",
            "PRIMARY_REENTRY_FAILURE_MODE": None,
        }

    a_re = _f((a0.get("re_entry") or {}).get("PnL"))
    b_re = _f((b0.get("re_entry") or {}).get("PnL"))
    mechanism_a = a_re is not None and a_re < 0
    mechanism_b = b_re is not None and b_re < 0

    # Prefer the policy that targets the diagnosed mode, not max historical PnL.
    preferred_order = {
        "LOSS_CHASING": ("R3", "R1", "R2"),
        "REPEATED_ENTRY": ("R2", "R1", "R3"),
        "STALE_SETUP": ("R1", "R3", "R2"),
        "CAP_OCCUPANCY": ("R1", "R2", "R3"),
        "TIME_OF_DAY": ("R1", "R2", "R3"),
        "OTHER": ("R1", "R2", "R3"),
    }.get(mode, ("R1", "R2", "R3"))

    a0_econ = a_vars.get("R0") or a0
    hist = [p for p in preferred_order if (a_bars.get(p) or {}).get("ok") and first_intact(a_vars.get(p) or {}, a0_econ)]
    if not hist:
        hist = [p for p in preferred_order if (a_bars.get(p) or {}).get("ok")]

    best = "NONE"
    cross = False
    clock_dep = False
    if hist:
        best = hist[0]
        same = b_dir.get(best)
        if same is True:
            cross = True
        elif same is False:
            clock_dep = True

    a2_r0 = a2_vars.get("R0") or {}
    promising = False
    combo_ok = []
    a0_first = _f((a0_econ.get("first_entry") or {}).get("PnL"))
    a2_first = _f((a2_r0.get("first_entry") or {}).get("PnL"))
    a2_re = _f((a2_r0.get("re_entry") or {}).get("PnL"))
    for p in ("R1", "R2", "R3"):
        row = a2_vars.get(p) or {}
        if not row:
            continue
        f = _f((row.get("first_entry") or {}).get("PnL"))
        r = _f((row.get("re_entry") or {}).get("PnL"))
        keep_first = f is not None and a0_first is not None and f >= a0_first
        cut_re = r is not None and a2_re is not None and r > a2_re
        if keep_first and cut_re:
            promising = True
            bar = historical_candidate_vs_a0(row, a0_econ)
            if bar.get("ok") and first_intact(row, a0_econ):
                combo_ok.append(p)

    if not mechanism_a:
        verdict = "REENTRY_MECHANISM_NOT_SUPPORTED"
        rec = "Re-entry is not a loss pocket on CURRENT IRREGULAR under session-scoped episodes. Do not change Runtime."
        best = "NONE"
        cross = False
    elif best != "NONE" and not clock_dep:
        verdict = "REENTRY_POLICY_HISTORICAL_CANDIDATE"
        rec = (
            f"{POLICY_NAME[best]} met the historical candidate bar on A and is the mechanism-aligned "
            "precommitted policy. Runtime implementation is forbidden. TRUE_OOS=false."
        )
    elif best != "NONE" and clock_dep:
        verdict = "REENTRY_POLICY_CLOCK_DEPENDENT"
        rec = (
            f"{POLICY_NAME[best]} met the A bar but B moved the opposite way. "
            "Treat as clock-dependent. Do not write Runtime."
        )
    elif combo_ok:
        verdict = "EXECUTABILITY_PLUS_REENTRY_HISTORICAL_CANDIDATE"
        best = "NONE"
        rec = (
            f"A0+R1/R2/R3 did not meet the bar. A2 combined with {combo_ok[0]} did as a new combination "
            "diagnostic. Frozen A2/B2 verdicts are unchanged. Runtime forbidden."
        )
    elif mechanism_a:
        verdict = "REENTRY_LOSS_CONFIRMED_NO_SIMPLE_POLICY"
        rec = (
            "Re-entry destroys first-entry edge on CURRENT IRREGULAR. R1/R2/R3 did not pass the "
            "historical candidate bar without breaking first-entry or robustness. "
            "Do not adopt NO_REENTRY from historical re-entry PnL alone. Runtime forbidden."
        )
    else:
        verdict = "REENTRY_MECHANISM_NOT_SUPPORTED"
        rec = "No supported re-entry architecture change."

    if best == "NONE":
        cross = False

    return {
        "VERDICT": verdict,
        "BEST_MECHANISM_SUPPORTED_VARIANT": best,
        "CROSS_CLOCK_SUPPORTED": bool(cross),
        "CLOCK_DEPENDENT": bool(clock_dep),
        "EXECUTABILITY_PLUS_REENTRY_PROMISING": bool(promising),
        "COMBO_HISTORICAL_OK": combo_ok,
        "MECHANISM_A": mechanism_a,
        "MECHANISM_B": mechanism_b,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "RECOMMENDED_NEXT_STEP": rec,
        "PRIMARY_REENTRY_FAILURE_MODE": mode,
        "PREFERRED_POLICY_ORDER": list(preferred_order),
        "HISTORICAL_PASS_A": hist,
    }


def variant_headline(econ: dict[str, Any]) -> dict[str, Any]:
    return {
        "trades": econ.get("trades"),
        "PnL": econ.get("PnL"),
        "PF": econ.get("PF"),
        "maxDD": econ.get("maxDD"),
        "positive_day_rate": econ.get("positive_day_rate"),
        "median_daily_pnl": econ.get("median_daily_pnl"),
        "first_n": (econ.get("first_entry") or {}).get("N"),
        "first_PnL": (econ.get("first_entry") or {}).get("PnL"),
        "first_PF": (econ.get("first_entry") or {}).get("PF"),
        "re_n": (econ.get("re_entry") or {}).get("N"),
        "re_PnL": (econ.get("re_entry") or {}).get("PnL"),
        "re_PF": (econ.get("re_entry") or {}).get("PF"),
    }


__all__ = [
    "a0_parity",
    "answers_q",
    "am_pm_reentry",
    "b0_parity",
    "build_episode_ledger",
    "decide",
    "economic_row",
    "exit_family",
    "first_intact",
    "first_reentry_split",
    "grouped_slice",
    "historical_candidate_vs_a0",
    "primary_failure_mode",
    "same_direction",
    "score_rank_change_block",
    "sequence_block",
    "slice_stats",
    "slim_pack",
    "variant_headline",
    "EXPECTED_A0_TRADES",
    "EXPECTED_A0_PNL",
    "EXPECTED_A0_PF",
    "EXPECTED_A0_MAXDD",
    "EXPECTED_B0_TRADES",
    "EXPECTED_B0_PNL",
    "EXPECTED_B0_PF",
    "EXPECTED_B0_MAXDD",
]
