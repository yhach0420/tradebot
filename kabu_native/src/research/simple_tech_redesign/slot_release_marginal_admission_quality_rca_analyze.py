"""Analyze CAP-only blocked quality vs failed-treatment incremental cascade."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any, Optional

from replay.pnl_yen import summarize_pnl_yen_100
from research.anchor_vs_event_driven.run_comparison import _bare
from research.simple_tech_redesign.branch_u_causal_analyze import attribution
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import (
    arm_econ,
    class_of,
    identity_check,
    short_class,
)
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_spec import (
    CANDIDATE_EXIT_REASON,
    DEV_ADDED_N,
    DEV_CORE_N,
    DEV_FILL_N,
    DEV_PNL,
    FWD_ADDED_N,
    FWD_CORE_N,
    FWD_FILL_N,
    FWD_PNL,
    YEN_PARITY_TOL,
)
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_harvest import (
    _f,
    _tid,
    _tid_s,
    session_clock,
)
from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_spec import (
    DECOMP_TOL,
    DEV_CONC_DAY,
    DEV_DIRECT,
    DEV_INCR_N,
    DEV_SLOT,
    DEV_TOTAL,
    DEV_TREAT_FILL_N,
    EARLIEST_POSSIBLE_IF_CASE_A,
    FWD_DIRECT,
    FWD_INCR_N,
    FWD_OUTLIER_DAY,
    FWD_OUTLIER_SYMBOL,
    FWD_SLOT,
    FWD_TOTAL,
    FWD_TREAT_FILL_N,
)

EPS = 1e-9


def _iqr(xs: list[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    s = sorted(xs)
    n = len(s)
    return float(s[(3 * n) // 4] - s[n // 4])


def quality_pack(pnls: list[float], *, n_denom: Optional[int] = None) -> dict[str, Any]:
    n = len(pnls)
    den = int(n_denom) if n_denom is not None else n
    if n == 0:
        return {
            "n": 0,
            "total_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "PF": None,
            "win_rate": None,
            "median_pnl": None,
            "mean_pnl": None,
            "max_loss": None,
            "max_gain": None,
            "pnl_per_fill": None,
            "gross_loss_per_fill": None,
        }
    trades = [{"pnl_yen_100": float(p)} for p in pnls]
    summ = summarize_pnl_yen_100(trades)
    wins = sum(1 for p in pnls if p > EPS)
    total = float(summ.get("total_pnl_yen_100") or 0.0)
    gl = float(summ.get("gross_loss_yen_100") or 0.0)
    return {
        "n": n,
        "total_pnl": total,
        "gross_profit": float(summ.get("gross_profit_yen_100") or 0.0),
        "gross_loss": gl,
        "PF": summ.get("profit_factor_yen_100"),
        "win_rate": wins / n if n else None,
        "median_pnl": float(median(pnls)),
        "mean_pnl": total / n if n else None,
        "max_loss": float(min(pnls)),
        "max_gain": float(max(pnls)),
        "pnl_per_fill": total / den if den else None,
        "gross_loss_per_fill": gl / den if den else None,
    }


def _pf_num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, str) and str(v).lower() == "inf":
        return float("inf")
    x = _f(v)
    return float(x) if x is not None else None


def weaker(blocked: dict[str, Any], admitted: dict[str, Any]) -> dict[str, Any]:
    bm, am = blocked.get("mean_pnl"), admitted.get("mean_pnl")
    bp, ap = _pf_num(blocked.get("PF")), _pf_num(admitted.get("PF"))
    wr_b, wr_a = blocked.get("win_rate"), admitted.get("win_rate")
    gl_b, gl_a = blocked.get("gross_loss_per_fill"), admitted.get("gross_loss_per_fill")
    mean_worse = bm is not None and am is not None and float(bm) < float(am) - EPS
    pf_worse = bp is not None and ap is not None and float(bp) < float(ap) - EPS
    wr_worse = wr_b is not None and wr_a is not None and float(wr_b) < float(wr_a) - EPS
    gl_worse = gl_b is not None and gl_a is not None and float(gl_b) > float(gl_a) + EPS
    return {
        "mean_worse": bool(mean_worse),
        "pf_worse": bool(pf_worse),
        "win_rate_worse": bool(wr_worse),
        "gross_loss_per_fill_worse": bool(gl_worse),
        "clearly_weaker": bool(mean_worse and (pf_worse or wr_worse or gl_worse)),
    }


def clock_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    opens = [float(r["seconds_from_session_open"]) for r in rows if _f(r.get("seconds_from_session_open")) is not None]
    closes = [float(r["seconds_to_session_close"]) for r in rows if _f(r.get("seconds_to_session_close")) is not None]
    return {
        "n": len(rows),
        "from_open_median": float(median(opens)) if opens else None,
        "from_open_iqr": _iqr(opens),
        "to_close_median": float(median(closes)) if closes else None,
        "to_close_iqr": _iqr(closes),
    }


def conc_share(rows: list[dict[str, Any]], *, key: str, pnl_key: str = "pnl_yen_100") -> dict[str, Any]:
    by: dict[str, float] = defaultdict(float)
    for r in rows:
        by[str(r.get(key) or "")] += float(_f(r.get(pnl_key)) or 0.0)
    if not by:
        return {
            "top": None,
            "top_pnl": None,
            "share": None,
            "share_of_net": None,
            "share_of_abs": None,
            "warning_gt_50pct": False,
            "by": {},
        }
    top_k = max(by, key=lambda k: abs(by[k]))
    total = float(sum(by.values()))
    abs_den = float(sum(abs(v) for v in by.values()))
    share_net = (abs(by[top_k]) / abs(total)) if abs(total) > EPS else None
    share_abs = (abs(by[top_k]) / abs_den) if abs_den > EPS else None
    return {
        "top": top_k,
        "top_pnl": by[top_k],
        "share": share_abs,
        "share_of_net": share_net,
        "share_of_abs": share_abs,
        "warning_gt_50pct": bool(share_abs is not None and share_abs > 0.5),
        "by": dict(by),
    }


def identity_failed_treatment(ctrl_tr: list[dict[str, Any]], treat_tr: list[dict[str, Any]], *, cohort: str) -> dict[str, Any]:
    if cohort == "DEVELOPMENT":
        ident = identity_check(ctrl_tr, fill_n=DEV_FILL_N, core_n=DEV_CORE_N, added_n=DEV_ADDED_N, pnl=DEV_PNL)
        exp = {"treat_fill_n": DEV_TREAT_FILL_N, "incr_n": DEV_INCR_N, "direct": DEV_DIRECT, "slot": DEV_SLOT, "total": DEV_TOTAL}
    else:
        ident = identity_check(ctrl_tr, fill_n=FWD_FILL_N, core_n=FWD_CORE_N, added_n=FWD_ADDED_N, pnl=FWD_PNL)
        exp = {"treat_fill_n": FWD_TREAT_FILL_N, "incr_n": FWD_INCR_N, "direct": FWD_DIRECT, "slot": FWD_SLOT, "total": FWD_TOTAL}
    attr = attribution(ctrl_tr, treat_tr)
    direct = float(attr.get("DIRECT_EXIT_DELTA") or 0.0)
    slot = float(attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA") or 0.0) + float(attr.get("DISPLACED_TRADE_DELTA") or 0.0)
    total = float(attr.get("TOTAL_CAUSAL_DELTA") or 0.0)
    incr_n = int(attr.get("INCREMENTAL_TREATMENT_N") or 0)
    treat_n = len(treat_tr)
    ok = (
        bool(ident.get("ok"))
        and treat_n == int(exp["treat_fill_n"])
        and incr_n == int(exp["incr_n"])
        and abs(direct - float(exp["direct"])) <= YEN_PARITY_TOL
        and abs(slot - float(exp["slot"])) <= YEN_PARITY_TOL
        and abs(total - float(exp["total"])) <= DECOMP_TOL
        and abs((direct + slot) - total) <= DECOMP_TOL
    )
    ident.update(
        {
            "treatment_fill_n": treat_n,
            "incremental_fill_n": incr_n,
            "DIRECT_EXIT_DELTA": direct,
            "SLOT_RELEASE_DOWNSTREAM_DELTA": slot,
            "TOTAL_CAUSAL_DELTA": total,
            "expected_treatment_fill_n": exp["treat_fill_n"],
            "expected_incremental_fill_n": exp["incr_n"],
            "failed_treatment_ok": bool(ok),
        }
    )
    ident["ok"] = bool(ok)
    return ident


def role_quality(rows: list[dict[str, Any]], pnl_key: str) -> dict[str, Any]:
    out = {}
    for role in ("CORE", "ADDED"):
        xs = [float(_f(r.get(pnl_key)) or 0.0) for r in rows if str(r.get("fill_role") or "") == role and _f(r.get(pnl_key)) is not None]
        out[role] = quality_pack(xs)
    return out


def _parent_sym(trade_id: Any) -> str:
    parts = str(trade_id or "").split("|")
    return parts[1] if len(parts) == 3 else ""


def evaluate_cohort(
    *,
    cohort: str,
    bodies: list[dict[str, Any]],
    residual: dict[tuple[str, str, float], dict[str, Any]],
) -> dict[str, Any]:
    ctrl_tr: list[dict[str, Any]] = []
    treat_tr: list[dict[str, Any]] = []
    og_tr: list[dict[str, Any]] = []
    cap_only: list[dict[str, Any]] = []
    multi: list[dict[str, Any]] = []
    same_sym: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []
    leftover_ok = True
    sot_ok = True
    replay_match = True
    for body in bodies:
        leftover_ok = leftover_ok and bool(body.get("leftover_ok"))
        sot_ok = sot_ok and bool(body.get("control_sot_ok")) and bool(body.get("treatment_sot_ok"))
        replay_match = replay_match and bool(body.get("control_replay_match"))
        ctrl_tr.extend(list((body.get("control") or {}).get("trades") or []))
        treat_tr.extend(list((body.get("treatment") or {}).get("trades") or []))
        og_tr.extend(list((body.get("one_generation") or {}).get("trades") or []))
        inv = dict(body.get("cap_inventory") or {})
        cap_only.extend(list(inv.get("cap_only") or []))
        multi.extend(list(inv.get("multi_reason") or []))
        same_sym.extend(list(inv.get("same_symbol") or []))
        chains.extend(list(body.get("chains") or []))
    ident = identity_failed_treatment(ctrl_tr, treat_tr, cohort=cohort)
    ident["leftover_ok"] = leftover_ok
    ident["control_sot_ok"] = sot_ok
    ident["control_replay_match"] = replay_match
    hyp = [r for r in cap_only if bool(r.get("actual_filled")) and _f(r.get("session_close_pnl")) is not None]
    ctrl_clock_rows = []
    for t in ctrl_tr:
        key = _tid(t)
        if key is None:
            continue
        ctrl_clock_rows.append(session_clock(key[0], key[2]))
    blocked_q = quality_pack([float(r["session_close_pnl"]) for r in hyp])
    admitted_q = quality_pack([float(_f(t.get("pnl_yen_100")) or 0.0) for t in ctrl_tr])
    incr = [c for c in chains if bool(c.get("is_incremental"))]
    gen0 = [c for c in chains if bool(c.get("is_control_shared"))]
    gen_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for c in incr:
        gen_rows[int(c.get("generation") or 1)].append(c)
    if gen0:
        gen_rows[0] = gen0
    gen_econ = {}
    for g in sorted(gen_rows):
        xs = gen_rows[g]
        pack = quality_pack([float(_f(r.get("pnl_yen_100")) or 0.0) for r in xs])
        pack["fill_n"] = len(xs)
        pack["candidate_exit_n"] = sum(1 for r in xs if str(r.get("exit_reason") or "") == CANDIDATE_EXIT_REASON)
        gen_econ[str(g)] = pack
    gen1 = list(gen_rows.get(1) or [])
    gen2p = [r for g, rs in gen_rows.items() if g >= 2 for r in rs]
    gen1_q = quality_pack([float(_f(r.get("pnl_yen_100")) or 0.0) for r in gen1])
    gen2_q = quality_pack([float(_f(r.get("pnl_yen_100")) or 0.0) for r in gen2p])
    ctrl_ids = {k for k in (_tid(t) for t in ctrl_tr) if k is not None}
    og_incr = [t for t in og_tr if _tid(t) not in ctrl_ids]
    full_attr = attribution(ctrl_tr, treat_tr)
    og_attr = attribution(ctrl_tr, og_tr)
    full_total = float(full_attr.get("TOTAL_CAUSAL_DELTA") or 0.0)
    og_total = float(og_attr.get("TOTAL_CAUSAL_DELTA") or 0.0)
    one_gen = {
        "treatment_fill_n": len(og_tr),
        "incremental_fill_n": int(og_attr.get("INCREMENTAL_TREATMENT_N") or 0),
        "incremental_pnl": float(og_attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA") or 0.0) + float(og_attr.get("DISPLACED_TRADE_DELTA") or 0.0),
        "TOTAL_CAUSAL_DELTA": og_total,
        "vs_full_cascade_delta": og_total - full_total,
        "gen2plus_suppressed": True,
        "incremental_econ": arm_econ(og_incr),
    }
    hyp_ids = {_tid(r) for r in hyp}
    incr_ids = {_tid(r) for r in incr}
    same_ids = {_tid(r) for r in same_sym}
    overlap = sorted(k for k in (incr_ids & hyp_ids) if k is not None)
    incr_not_cap = sorted(k for k in (incr_ids - hyp_ids) if k is not None)
    cap_not_incr = sorted(k for k in (hyp_ids - incr_ids) if k is not None)
    incr_session = []
    join_rows = []
    for r in incr:
        key = _tid(r)
        hit = next((h for h in cap_only if _tid(h) == key), None)
        hyp_hit = next((h for h in hyp if _tid(h) == key), None)
        if hyp_hit is not None:
            incr_session.append(float(hyp_hit["session_close_pnl"]))
        join_rows.append(
            {
                "incremental_trade_id": r.get("trade_id"),
                "cohort": cohort,
                "blocked_candidate_identity": (hit or {}).get("trade_id"),
                "originally_cap_only_blocked": hit is not None,
                "original_block_time": (hit or {}).get("block_t"),
                "admission_time": r.get("admit_t") or r.get("fill_time") or r.get("t0"),
                "release_root": r.get("root_control_exit_trade_id"),
                "parent_fill_trade_id": r.get("parent_fill_trade_id"),
                "generation": r.get("generation"),
                "control_same_symbol_blocked": key in same_ids,
                "parity_ok": bool(
                    hit is not None
                    and key is not None
                    and abs(float(hit.get("t0") or 0.0) - float(r.get("t0") or 0.0)) <= EPS
                ),
            }
        )
    same_root = [r for r in incr if str(r.get("symbol") or "") == _parent_sym(r.get("root_control_exit_trade_id"))]
    same_parent = [r for r in incr if str(r.get("symbol") or "") == _parent_sym(r.get("parent_fill_trade_id"))]
    prev_exited = []
    treat_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in treat_tr:
        treat_by_day[str(t.get("date") or "")].append(t)
    for r in incr:
        day = str(r.get("date") or "")
        admit = float(_f(r.get("admit_t") or r.get("t0")) or 0.0)
        prior = False
        for t in treat_by_day.get(day, []):
            if _bare(t.get("symbol")) != str(r.get("symbol") or ""):
                continue
            et = _f(t.get("exit_time"))
            t0 = _f(t.get("t0"))
            if et is None or t0 is None:
                continue
            if abs(float(t0) - float(r.get("t0") or 0.0)) <= EPS:
                continue
            if float(et) + EPS < admit:
                prior = True
                break
        if prior:
            prev_exited.append(r)

    def _sym_pack(xs: list[dict[str, Any]]) -> dict[str, Any]:
        q = quality_pack([float(_f(r.get("pnl_yen_100")) or 0.0) for r in xs])
        q["n"] = len(xs)
        return q

    tm = {k: t for t in treat_tr if (k := _tid(t)) is not None}
    incr_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in incr:
        incr_by_root[str(r.get("root_control_exit_trade_id") or "")].append(r)
    class_acc: dict[str, dict[str, Any]] = defaultdict(lambda: {"root_n": 0, "direct_delta": 0.0, "downstream_n": 0, "downstream_pnl": 0.0})
    for t in ctrl_tr:
        key = _tid(t)
        if key is None:
            continue
        cls = short_class(class_of(key, residual))
        treat = tm.get(key) or {}
        dlt = float(_f(treat.get("pnl_yen_100")) or 0.0) - float(_f(t.get("pnl_yen_100")) or 0.0)
        downs = incr_by_root.get(_tid_s(key), [])
        acc = class_acc[cls]
        acc["root_n"] += 1
        acc["direct_delta"] += dlt
        acc["downstream_n"] += len(downs)
        acc["downstream_pnl"] += float(sum(float(_f(x.get("pnl_yen_100")) or 0.0) for x in downs))
        acc["combined_causal_contribution"] = float(acc["direct_delta"]) + float(acc["downstream_pnl"])
    blocked_day = conc_share(hyp, key="date", pnl_key="session_close_pnl")
    incr_day = conc_share(incr, key="date")
    blocked_sym = conc_share(hyp, key="symbol", pnl_key="session_close_pnl")
    incr_sym = conc_share(incr, key="symbol")
    outlier = None
    if cohort != "DEVELOPMENT":
        hits = [r for r in incr if str(r.get("date") or "") == FWD_OUTLIER_DAY and str(r.get("symbol") or "") == FWD_OUTLIER_SYMBOL]
        hit = min(hits, key=lambda r: float(_f(r.get("pnl_yen_100")) or 0.0)) if hits else None
        if hit is not None:
            key = _tid(hit)
            cap_hit = next((h for h in cap_only if _tid(h) == key), None)
            incr_pnl = float(_f(hit.get("pnl_yen_100")) or 0.0)
            slot = float(ident.get("SLOT_RELEASE_DOWNSTREAM_DELTA") or 0.0)
            blocked_total = float(blocked_q.get("total_pnl") or 0.0)
            hyp_ex = [
                r
                for r in hyp
                if not (str(r.get("date") or "") == FWD_OUTLIER_DAY and str(r.get("symbol") or "") == FWD_OUTLIER_SYMBOL)
            ]
            incr_ex = [
                r
                for r in incr
                if not (str(r.get("date") or "") == FWD_OUTLIER_DAY and str(r.get("symbol") or "") == FWD_OUTLIER_SYMBOL)
            ]
            ex_q = quality_pack([float(r["session_close_pnl"]) for r in hyp_ex])
            outlier = {
                "trade_id": hit.get("trade_id"),
                "originally_cap_only_blocked": cap_hit is not None,
                "cap_record": cap_hit,
                "generation": hit.get("generation"),
                "root_control_exit_trade_id": hit.get("root_control_exit_trade_id"),
                "parent_fill_trade_id": hit.get("parent_fill_trade_id"),
                "parent_exit_reason": hit.get("parent_exit_reason"),
                "same_symbol_as_root": str(hit.get("symbol") or "") == _parent_sym(hit.get("root_control_exit_trade_id")),
                "same_symbol_as_parent": str(hit.get("symbol") or "") == _parent_sym(hit.get("parent_fill_trade_id")),
                "pnl_yen_100": incr_pnl,
                "share_of_incremental_pnl": (abs(incr_pnl) / abs(slot)) if abs(slot) > EPS else None,
                "share_of_blocked_pool_pnl": (abs(incr_pnl) / abs(blocked_total)) if abs(blocked_total) > EPS else None,
                "exclusion_forbidden": True,
                "fwd_blocked_pool_excluding_285A_diagnostic_only": ex_q,
                "fwd_incremental_excluding_285A_diagnostic_only": quality_pack(
                    [float(_f(r.get("pnl_yen_100")) or 0.0) for r in incr_ex]
                ),
                "fwd_blocked_ex_285A_vs_admitted": weaker(ex_q, admitted_q),
            }
    return {
        "cohort": cohort,
        "identity": ident,
        "cap_only_blocked_n": len(cap_only),
        "multi_reason_n": len(multi),
        "same_symbol_blocked_n": len(same_sym),
        "execution_evaluable_n": sum(1 for r in cap_only if bool(r.get("executable_signal"))),
        "hypothetical_fill_n": len(hyp),
        "blocked_pool": blocked_q,
        "admitted_control": admitted_q,
        "comparison": weaker(blocked_q, admitted_q),
        "blocked_by_role": role_quality(hyp, "session_close_pnl"),
        "admitted_by_role": role_quality([{"fill_role": t.get("fill_role"), "pnl_yen_100": t.get("pnl_yen_100")} for t in ctrl_tr], "pnl_yen_100"),
        "time": {"control_admitted": clock_pack(ctrl_clock_rows), "cap_only_blocked": clock_pack(cap_only), "hypothetical_fills": clock_pack(hyp)},
        "incremental": incr,
        "generation_econ": gen_econ,
        "GEN1": gen1_q,
        "GEN2PLUS": gen2_q,
        "GEN1_TOTAL_PNL": gen1_q.get("total_pnl"),
        "GEN2PLUS_TOTAL_PNL": gen2_q.get("total_pnl"),
        "one_generation_only": one_gen,
        "observed_vs_blocked": {
            "incremental_n": len(incr),
            "overlap_n": len(overlap),
            "incremental_not_in_cap_only_n": len(incr_not_cap),
            "cap_only_not_observed_n": len(cap_not_incr),
            "incremental_not_in_cap_only_ids": [_tid_s(k) for k in incr_not_cap],
            "overlap_ids": [_tid_s(k) for k in overlap],
            "join": join_rows,
            "parity_ok_n": sum(1 for j in join_rows if j.get("parity_ok")),
            "all_blocked_pool": blocked_q,
            "observed_incremental_treatment_pnl": quality_pack([float(_f(r.get("pnl_yen_100")) or 0.0) for r in incr]),
            "observed_incremental_subset_session_close": quality_pack(incr_session),
        },
        "same_symbol": {
            "same_symbol_as_root": _sym_pack(same_root),
            "same_symbol_as_parent": _sym_pack(same_parent),
            "same_symbol_previously_exited_in_session": _sym_pack(prev_exited),
        },
        "root_exit_class": {k: dict(v) for k, v in class_acc.items()},
        "day_concentration": {
            "blocked_top": blocked_day,
            "incremental_top": incr_day,
            "dev_20260806_blocked_pnl": float(sum(float(r.get("session_close_pnl") or 0.0) for r in hyp if str(r.get("date") or "") == DEV_CONC_DAY)),
            "dev_20260806_incremental_pnl": float(sum(float(_f(r.get("pnl_yen_100")) or 0.0) for r in incr if str(r.get("date") or "") == DEV_CONC_DAY)),
            "fwd_20260828_blocked_pnl": float(sum(float(r.get("session_close_pnl") or 0.0) for r in hyp if str(r.get("date") or "") == FWD_OUTLIER_DAY)),
            "fwd_20260828_incremental_pnl": float(sum(float(_f(r.get("pnl_yen_100")) or 0.0) for r in incr if str(r.get("date") or "") == FWD_OUTLIER_DAY)),
        },
        "symbol_concentration": {"blocked": blocked_sym, "incremental": incr_sym},
        "outlier_285A": outlier,
        "cap_only_rows": cap_only,
        "hypothetical_rows": hyp,
        "multi_reason_rows": multi,
    }


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, leak_ok: bool, harvested_ok: bool) -> dict[str, Any]:
    ident_ok = bool(dev.get("identity", {}).get("ok")) and bool(fwd.get("identity", {}).get("ok"))
    leftover = bool(dev.get("identity", {}).get("leftover_ok")) and bool(fwd.get("identity", {}).get("leftover_ok"))
    replay = bool(dev.get("identity", {}).get("control_replay_match")) and bool(fwd.get("identity", {}).get("control_replay_match"))
    integrity = bool(leak_ok and harvested_ok and ident_ok and leftover and replay)
    dev_hyp_n = int(dev.get("hypothetical_fill_n") or 0)
    fwd_hyp_n = int(fwd.get("hypothetical_fill_n") or 0)
    insufficient = dev_hyp_n < 5 or fwd_hyp_n < 3
    dev_weak = bool((dev.get("comparison") or {}).get("clearly_weaker"))
    fwd_weak = bool((fwd.get("comparison") or {}).get("clearly_weaker"))
    d_mean_w = bool((dev.get("comparison") or {}).get("mean_worse"))
    f_mean_w = bool((fwd.get("comparison") or {}).get("mean_worse"))
    quality_reverse = bool(d_mean_w) != bool(f_mean_w)
    gen2_fwd = float(fwd.get("GEN2PLUS_TOTAL_PNL") or 0.0)
    gen1_fwd = float(fwd.get("GEN1_TOTAL_PNL") or 0.0)
    slot_fwd = float(fwd.get("identity", {}).get("SLOT_RELEASE_DOWNSTREAM_DELTA") or 0.0)
    cascade_amplifies = gen2_fwd < -EPS and abs(slot_fwd) > EPS and abs(gen2_fwd) >= 0.3 * abs(slot_fwd)
    gen1_already = gen1_fwd < -EPS
    incr_n = int((fwd.get("observed_vs_blocked") or {}).get("incremental_n") or 0)
    same_n = int(((fwd.get("same_symbol") or {}).get("same_symbol_as_parent") or {}).get("n") or 0) + int(
        ((fwd.get("same_symbol") or {}).get("same_symbol_as_root") or {}).get("n") or 0
    )
    same_major = bool(incr_n) and same_n >= incr_n / 2
    out_share = float((fwd.get("outlier_285A") or {}).get("share_of_incremental_pnl") or 0.0)
    concentrated = out_share >= 0.5
    fwd_ex_weak = bool(((fwd.get("outlier_285A") or {}).get("fwd_blocked_ex_285A_vs_admitted") or {}).get("clearly_weaker"))
    obs_pnl = float(((fwd.get("observed_vs_blocked") or {}).get("observed_incremental_treatment_pnl") or {}).get("total_pnl") or 0.0)
    timing_sel = (not fwd_weak) and obs_pnl < -EPS
    secondary: list[str] = []
    if concentrated:
        secondary.append("CONCENTRATED_OUTLIER")
    if cascade_amplifies:
        secondary.append("CASCADE_AMPLIFICATION")
    if same_major:
        secondary.append("SAME_SYMBOL_REENTRY")
    if quality_reverse:
        secondary.append("REGIME_DEPENDENT_MARGINAL_QUALITY")
    if fwd_weak:
        secondary.append("MARGINAL_ENTRY_QUALITY")
    if timing_sel:
        secondary.append("EXIT_RELEASE_TIMING_SELECTION")
    if not integrity:
        case, verdict, nxt, bottleneck = (
            "G",
            "SIMPLE_TECH_SLOT_RELEASE_MARGINAL_RCA_INTEGRITY_FAILED",
            "FAIL_CLOSED. Identity / blocked-reason / occupancy mismatch.",
            "MIXED_OR_INSUFFICIENT",
        )
    elif insufficient:
        case, verdict, nxt, bottleneck = (
            "F",
            "SIMPLE_TECH_SLOT_RELEASE_MARGINAL_RCA_INSUFFICIENT",
            "STOP. Sample / execution evaluability insufficient.",
            "MIXED_OR_INSUFFICIENT",
        )
    elif quality_reverse and not (dev_weak and fwd_weak):
        case, verdict, nxt, bottleneck = (
            "D",
            "SIMPLE_TECH_MARGINAL_QUALITY_REGIME_UNSTABLE",
            "No new ENTRY filter. No new EXIT. Additional future evidence / regime RCA first.",
            "REGIME_DEPENDENT_MARGINAL_QUALITY",
        )
    elif concentrated and not (dev_weak and fwd_weak):
        case, verdict, nxt, bottleneck = (
            "E",
            "SIMPLE_TECH_SLOT_RELEASE_DAMAGE_CONCENTRATED_OUTLIER",
            "Rule addition forbidden. Future evidence first.",
            "CONCENTRATED_OUTLIER",
        )
    elif cascade_amplifies and not (dev_weak and fwd_weak):
        case, verdict, nxt, bottleneck = (
            "B",
            "SIMPLE_TECH_SLOT_RELEASE_CASCADE_AMPLIFICATION_SUPPORTED",
            "Do not expand EXIT candidates. Investigate causal occupancy / re-admission in a separate RCA. Do not change CAP yet.",
            "CASCADE_AMPLIFICATION",
        )
    elif (not fwd_weak) and (not dev_weak) and timing_sel:
        case, verdict, nxt, bottleneck = (
            "C",
            "SIMPLE_TECH_EXIT_RELEASE_TIMING_SELECTION_SUPPORTED",
            "Floor-break family stays CLOSED. Not a generic ENTRY bottleneck. Organize exit-release timing architecture before any new EXIT.",
            "EXIT_RELEASE_TIMING_SELECTION",
        )
    elif (dev_weak or d_mean_w) and (fwd_weak or f_mean_w):
        case, verdict, nxt, bottleneck = (
            "A",
            "SIMPLE_TECH_MARGINAL_ENTRY_QUALITY_BOTTLENECK_SUPPORTED",
            "Stop adding EXIT. Return to PRE-CAP ENTRY QUALITY mechanism discovery. Board family stays CLOSED. Do not use CAP as a quality filter. Do not create a new filter yet.",
            "MARGINAL_ENTRY_QUALITY",
        )
    else:
        case, verdict, nxt, bottleneck = (
            "E" if concentrated else "F",
            "SIMPLE_TECH_SLOT_RELEASE_DAMAGE_CONCENTRATED_OUTLIER" if concentrated else "SIMPLE_TECH_SLOT_RELEASE_MARGINAL_RCA_INSUFFICIENT",
            "Rule addition forbidden. Future evidence first." if concentrated else "STOP.",
            "CONCENTRATED_OUTLIER" if concentrated else "MIXED_OR_INSUFFICIENT",
        )
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "PRIMARY_CAUSAL_BOTTLENECK": bottleneck,
        "SECONDARY_DRIVERS": secondary,
        "CANDIDATE_FROZEN": False,
        "FAMILY_CLOSED": True,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NEW_RULE_CREATED": False,
        "ENTRY_CHANGED": False,
        "CAP_CHANGED": False,
        "first_eligible_prospective_date": None,
        "earliest_possible_if_case_A": EARLIEST_POSSIBLE_IF_CASE_A,
        "prospective_armed": False,
        "PRIMARY_NEXT_EXIT_TARGET": None,
        "questions": {
            "Q1_blocked_pool_worse_than_admitted_DEV": bool(dev_weak),
            "Q2_DEV_FWD_same_direction": bool(d_mean_w) == bool(f_mean_w),
            "Q3_FWD_blocked_pool_itself_bad": bool(fwd_weak),
            "Q4_floor_break_admitted_worse_subset": (not fwd_weak) and obs_pnl < -EPS,
            "Q5_FWD_damage_already_in_GEN1": gen1_already,
            "Q6_GEN2plus_worsened": cascade_amplifies,
            "Q7_same_symbol_reentry_primary": same_major,
            "Q8_explained_by_20260828_285A_alone": concentrated,
        },
        "gates": {
            "integrity": integrity,
            "insufficient": insufficient,
            "dev_clearly_weaker": dev_weak,
            "fwd_clearly_weaker": fwd_weak,
            "fwd_blocked_ex_285A_clearly_weaker_diagnostic": fwd_ex_weak,
            "quality_reverse": quality_reverse,
            "cascade_amplifies": cascade_amplifies,
            "concentrated_outlier": concentrated,
        },
    }



