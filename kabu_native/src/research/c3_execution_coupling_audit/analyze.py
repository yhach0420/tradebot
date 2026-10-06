"""Execution coupling diagnostics. No model selection. No new threshold."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare
from research.c3_execution_coupling_audit import (
    DD_TOL,
    ELIGIBLE_DAYS,
    EXPECTED_A2_MAXDD,
    EXPECTED_A2_PF,
    EXPECTED_A2_PNL,
    EXPECTED_A2_TRADES,
    EXPECTED_C3_MAXDD,
    EXPECTED_C3_PF,
    EXPECTED_C3_PNL,
    EXPECTED_C3_TRADES,
    PF_TOL,
    PNL_TOL,
    POSITION_CAP,
    TRADE_TOL,
)
from research.edge_decay_rca.analyze import tag_entry_kind
from research.entry_objective_redesign_c3.analyze import parity_pack
from research.entry_objective_redesign_c3.oof import TARGET, ranking_pop
from research.executable_target_v2_b_threshold.analyze import pack_metrics
from small_paper.v1r_primary_runtime import CLOCK_GRID


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _pf(v: Any) -> float:
    if v is None:
        return 0.0
    if v == "Infinity" or v == float("inf"):
        return 9.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def abc_line(pack: dict[str, Any] | None) -> str:
    if not pack:
        return "n/a"
    return f"{pack.get('trades')} / {pack.get('PnL') if pack.get('PnL') is not None else pack.get('pnl')} / {pack.get('PF')} / {pack.get('maxDD')}"


def slim_pack(pack: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pack:
        return None
    return {
        k: pack.get(k)
        for k in (
            "trades",
            "PnL",
            "PF",
            "maxDD",
            "avg_trade",
            "median_trade",
            "positive_day_rate",
            "median_daily_pnl",
            "AM",
            "PM",
            "first_entry",
            "re_entry",
        )
    }


def a2_parity(pack: dict[str, Any]) -> dict[str, Any]:
    return parity_pack(
        pack,
        expected={
            "trades": EXPECTED_A2_TRADES,
            "PnL": EXPECTED_A2_PNL,
            "PF": EXPECTED_A2_PF,
            "maxDD": EXPECTED_A2_MAXDD,
        },
        tols={"trades": TRADE_TOL, "PnL": PNL_TOL, "PF": PF_TOL, "maxDD": DD_TOL},
    )


def c3_final_parity(pack: dict[str, Any]) -> dict[str, Any]:
    return parity_pack(
        pack,
        expected={
            "trades": EXPECTED_C3_TRADES,
            "PnL": EXPECTED_C3_PNL,
            "PF": EXPECTED_C3_PF,
            "maxDD": EXPECTED_C3_MAXDD,
        },
        tols={"trades": TRADE_TOL, "PnL": PNL_TOL, "PF": PF_TOL, "maxDD": DD_TOL},
    )


def row_key(r: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(r.get("date") or ""),
        str(r.get("anchor") or r.get("anchor_time") or ""),
        _bare(r.get("symbol")),
    )


def admit_clock(day: str, fill_t: Any) -> str:
    """Exact admit clock: last CLOCK_GRID t0 <= fill (WAIT_SEC=1). Not nearest-anchor remap."""
    last = ""
    try:
        ft = float(fill_t)
    except (TypeError, ValueError):
        return str("")
    for h, m in CLOCK_GRID:
        t0 = hm_epoch(day, h, m)
        if t0 <= ft + 1e-9:
            last = f"{h:02d}:{m:02d}"
        else:
            break
    return last


def trade_clock(t: dict[str, Any]) -> str:
    day = str(t.get("date") or "")
    packed = str(t.get("anchor_time") or t.get("anchor") or "")
    ft = _f(t.get("fill_time"))
    if day and ft is not None:
        return admit_clock(day, ft) or packed
    return packed


def attach_scores(
    panel: list[dict[str, Any]],
    *,
    oof_by: dict[tuple[str, str, str], float],
    final_by: dict[tuple[str, str, str], float],
    wf_by: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    out = []
    for r in panel:
        rec = dict(r)
        k = row_key(rec)
        rec["symbol"] = _bare(rec.get("symbol"))
        rec["c3_oof_score"] = oof_by.get(k)
        rec["c3_final_score"] = final_by.get(k)
        wf = wf_by.get(k) or {}
        rec["WOULD_FILL_1S"] = bool(wf.get("WOULD_FILL_1S")) if wf else False
        rec["would_fill_known"] = bool(wf)
        rec["wf_fill_price"] = wf.get("fill_price")
        rec["wf_limit"] = wf.get("limit")
        fill_px = _f(wf.get("fill_price"))
        mark = _f(rec.get("m4_t1_px"))
        if rec["WOULD_FILL_1S"] and fill_px is not None and fill_px > 0 and mark is not None and mark > 0:
            rec["FILL_TO_600S_RETURN"] = float(mark) / float(fill_px) - 1.0
        else:
            rec["FILL_TO_600S_RETURN"] = None
        out.append(rec)
    return out


def score_map(rows: list[dict[str, Any]], key: str) -> dict[tuple[str, str, str], float]:
    out: dict[tuple[str, str, str], float] = {}
    for r in rows:
        v = _f(r.get(key))
        if v is None:
            continue
        out[row_key(r)] = float(v)
    return out


def wf_map(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in rows:
        out[row_key(r)] = r
    return out


def pop_scored(rows: list[dict[str, Any]], score_key: str) -> list[dict[str, Any]]:
    out = []
    for r in ranking_pop(rows):
        if _f(r.get(score_key)) is None:
            continue
        out.append(r)
    return out


def cohorts(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[(str(r.get("date")), str(r.get("anchor")))].append(r)
    return by


def topk_of(grp: list[dict[str, Any]], score_key: str, k: int) -> list[dict[str, Any]]:
    xs = [r for r in grp if _f(r.get(score_key)) is not None]
    xs.sort(key=lambda r: (-float(r[score_key]), str(r.get("symbol") or "")))
    return xs[:k]


def _mean(xs: list[Optional[float]]) -> Optional[float]:
    vs = [float(v) for v in xs if v is not None]
    return float(np.mean(vs)) if vs else None


def _median(xs: list[Optional[float]]) -> Optional[float]:
    vs = [float(v) for v in xs if v is not None]
    return float(np.median(vs)) if vs else None


def _rate(xs: list[bool]) -> Optional[float]:
    if not xs:
        return None
    return float(sum(1 for v in xs if v) / len(xs))


def _pos_rate(xs: list[Optional[float]]) -> Optional[float]:
    vs = [float(v) for v in xs if v is not None]
    if not vs:
        return None
    return float(sum(1 for v in vs if v > 0) / len(vs))


def stats_block(rows: list[dict[str, Any]], *, all_target: Optional[float]) -> dict[str, Any]:
    tgs = [_f(r.get(TARGET)) for r in rows]
    wfs = [bool(r.get("WOULD_FILL_1S")) for r in rows]
    f6 = [_f(r.get("FILL_TO_600S_RETURN")) for r in rows if r.get("WOULD_FILL_1S")]
    tm = _mean(tgs)
    return {
        "N": len(rows),
        "TARGET_V4_600S": tm,
        "TARGET_UPLIFT": (tm - all_target) if tm is not None and all_target is not None else None,
        "WOULD_FILL_1S_RATE": _rate(wfs),
        "WOULD_FILL_1S_N": int(sum(1 for v in wfs if v)),
        "FILL_TO_600S_RETURN_MEAN": _mean(f6),
        "FILL_TO_600S_RETURN_MEDIAN": _median(f6),
        "FILL_TO_600S_POSITIVE_RATE": _pos_rate(f6),
        "FILL_TO_600S_N": len([v for v in f6 if v is not None]),
    }


def all_target_mean(rows: list[dict[str, Any]]) -> Optional[float]:
    return _mean([_f(r.get(TARGET)) for r in ranking_pop(rows)])


def topk_block(rows: list[dict[str, Any]], score_key: str, k: int, *, all_target: Optional[float]) -> dict[str, Any]:
    picked: list[dict[str, Any]] = []
    for grp in cohorts(pop_scored(rows, score_key)).values():
        picked.extend(topk_of(grp, score_key, k))
    body = stats_block(picked, all_target=all_target)
    body["K"] = k
    body["score_key"] = score_key
    return body


def fillability_split(rows: list[dict[str, Any]], score_key: str, k: int, *, all_target: Optional[float]) -> dict[str, Any]:
    picked: list[dict[str, Any]] = []
    for grp in cohorts(pop_scored(rows, score_key)).values():
        picked.extend(topk_of(grp, score_key, k))
    yes = [r for r in picked if r.get("WOULD_FILL_1S")]
    no = [r for r in picked if not r.get("WOULD_FILL_1S")]
    return {
        "K": k,
        "score_key": score_key,
        "fillable": stats_block(yes, all_target=all_target),
        "unfillable": stats_block(no, all_target=all_target),
        "all_topk": stats_block(picked, all_target=all_target),
    }


def replacement_block(
    rows: list[dict[str, Any]],
    *,
    left_key: str,
    right_key: str,
    k: int,
    all_target: Optional[float],
) -> dict[str, Any]:
    both: list[dict[str, Any]] = []
    left_only: list[dict[str, Any]] = []
    right_only: list[dict[str, Any]] = []
    n_cohorts = 0
    for grp in cohorts(ranking_pop(rows)).values():
        lset = {_bare(r.get("symbol")): r for r in topk_of(grp, left_key, k)}
        rset = {_bare(r.get("symbol")): r for r in topk_of(grp, right_key, k)}
        if not lset and not rset:
            continue
        n_cohorts += 1
        for s, r in lset.items():
            if s in rset:
                both.append(r)
            else:
                left_only.append(r)
        for s, r in rset.items():
            if s not in lset:
                right_only.append(r)
    return {
        "K": k,
        "left": left_key,
        "right": right_key,
        "n_cohorts": n_cohorts,
        "BOTH_SELECTED": stats_block(both, all_target=all_target),
        "CURRENT_ONLY": stats_block(left_only, all_target=all_target),
        "C3_ONLY": stats_block(right_only, all_target=all_target),
    }


def decile_block(rows: list[dict[str, Any]], score_key: str, *, all_target: Optional[float]) -> list[dict[str, Any]]:
    xs = pop_scored(rows, score_key)
    if len(xs) < 10:
        return [{"empty": True, "score_key": score_key}]
    scores = np.asarray([float(r[score_key]) for r in xs], dtype=float)
    qs = np.quantile(scores, np.linspace(0.1, 1.0, 10))
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(10)]
    for r in xs:
        v = float(r[score_key])
        d = int(np.searchsorted(qs, v, side="left"))
        if d > 9:
            d = 9
        buckets[d].append(r)
    out = []
    for i, grp in enumerate(buckets, start=1):
        body = stats_block(grp, all_target=all_target)
        body["decile"] = i
        body["score_key"] = score_key
        body["score_min"] = min((float(r[score_key]) for r in grp), default=None)
        body["score_max"] = max((float(r[score_key]) for r in grp), default=None)
        out.append(body)
    return out


def reconstruct_a2_occupancy(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("date"))].append(t)
    for day in ELIGIBLE_DAYS:
        xs = by_day.get(day) or []
        for h, m in CLOCK_GRID:
            t0 = float(hm_epoch(day, h, m))
            open_syms = []
            for t in xs:
                ft = _f(t.get("fill_time"))
                et = _f(t.get("exit_time"))
                if ft is None or et is None:
                    continue
                if ft < t0 < et + 1e-12:
                    open_syms.append(_bare(t.get("symbol")))
            open_u = sorted(set(open_syms))
            sess = "AM" if h < 12 else "PM"
            out.append(
                {
                    "date": day,
                    "session": sess,
                    "anchor": f"{h:02d}:{m:02d}",
                    "t0": t0,
                    "open": open_u,
                    "pending": [],
                    "open_n": len(open_u),
                    "pending_n": 0,
                    "exposure": len(open_u),
                    "position_cap": POSITION_CAP,
                    "source": "A2_TRADE_RECONSTRUCT",
                }
            )
    return out


def occupancy_summary(rows: list[dict[str, Any]], *, fills: int, expired: int, admits: int, slot_release_n: int) -> dict[str, Any]:
    exps = [int(r.get("exposure") or 0) for r in rows]
    opens = [int(r.get("open_n") or 0) for r in rows]
    pends = [int(r.get("pending_n") or 0) for r in rows]
    starved = sum(1 for r in rows if int(r.get("exposure") or 0) >= POSITION_CAP)
    return {
        "n_clocks": len(rows),
        "mean_exposure": float(np.mean(exps)) if exps else None,
        "mean_open_n": float(np.mean(opens)) if opens else None,
        "mean_pending_n": float(np.mean(pends)) if pends else None,
        "clocks_at_cap": starved,
        "ADMITTED_OR_PENDING_N": admits,
        "FILL_N": fills,
        "EXPIRED_N": expired,
        "slot_release_n": slot_release_n,
    }


def funnel_from_panel(
    rows: list[dict[str, Any]],
    score_key: str,
    *,
    traces: dict[str, Any] | None,
    all_target: Optional[float],
) -> dict[str, Any]:
    pop = pop_scored(rows, score_key)
    by = cohorts(pop)
    t1, t3, t5 = [], [], []
    for grp in by.values():
        t1.extend(topk_of(grp, score_key, 1))
        t3.extend(topk_of(grp, score_key, 3))
        t5.extend(topk_of(grp, score_key, 5))
    admits = list((traces or {}).get("admits") or [])
    pending = admits
    fills = list((traces or {}).get("fills") or [])
    expired = list((traces or {}).get("expired") or [])
    cands = list((traces or {}).get("candidates") or [])
    occ = list((traces or {}).get("occupancy") or [])
    admitted_cands = [c for c in cands if c.get("admitted")]
    cap_n = int((traces or {}).get("cap_blocked") or 0)
    same_n = int((traces or {}).get("same_symbol_blocked") or 0)
    if occ:
        occ_idx = {(str(r.get("date")), str(r.get("anchor"))): r for r in occ}
        recon_cap = 0
        recon_same = 0
        for (day, an), grp in by.items():
            snap = occ_idx.get((day, an)) or {}
            occ_set = set(_bare(s) for s in (snap.get("open") or []) + (snap.get("pending") or []))
            free = max(0, POSITION_CAP - int(snap.get("exposure") or 0))
            ranked = topk_of(grp, score_key, len(grp))
            taken = 0
            for r in ranked:
                s = _bare(r.get("symbol"))
                if s in occ_set:
                    recon_same += 1
                    continue
                if taken >= free:
                    recon_cap += 1
                    continue
                taken += 1
        if cap_n <= 0:
            cap_n = recon_cap
        if same_n <= 0:
            same_n = recon_same
    top1_wf = _rate([bool(r.get("WOULD_FILL_1S")) for r in t1])
    top3_wf = _rate([bool(r.get("WOULD_FILL_1S")) for r in t3])
    top5_wf = _rate([bool(r.get("WOULD_FILL_1S")) for r in t5])
    adm_n = len(admitted_cands) if admitted_cands else len(admits)
    pend_n = len(pending)
    fill_n = len(fills)
    return {
        "score_key": score_key,
        "EXECUTABLE_ELIGIBLE_N": len(pop),
        "TOP1_N": len(t1),
        "TOP3_N": len(t3),
        "TOP5_N": len(t5),
        "ADMITTED_N": adm_n,
        "PENDING_N": pend_n,
        "WOULD_FILL_1S_N": int(sum(1 for r in pop if r.get("WOULD_FILL_1S"))),
        "ACTUAL_FILL_N": fill_n,
        "EXPIRED_N": len(expired),
        "CAP_BLOCK_N": cap_n,
        "SAME_SYMBOL_BLOCK_N": same_n,
        "TOP1_WOULD_FILL_RATE": top1_wf,
        "TOP3_WOULD_FILL_RATE": top3_wf,
        "TOP5_WOULD_FILL_RATE": top5_wf,
        "ADMITTED_TO_PENDING_RATE": (pend_n / adm_n) if adm_n else None,
        "PENDING_TO_FILL_RATE": (fill_n / pend_n) if pend_n else None,
        "TOP1": stats_block(t1, all_target=all_target),
        "TOP3": stats_block(t3, all_target=all_target),
        "TOP5": stats_block(t5, all_target=all_target),
    }


def first_entry_decomp(
    trades: list[dict[str, Any]],
    panel: list[dict[str, Any]],
    *,
    score_key: str,
) -> dict[str, Any]:
    tagged = tag_entry_kind(trades)
    first = [t for t in tagged if t.get("entry_kind") == "FIRST_ENTRY"]
    reent = [t for t in tagged if t.get("entry_kind") == "REENTRY"]
    idx = {row_key({"date": r.get("date"), "anchor": r.get("anchor"), "symbol": r.get("symbol")}): r for r in panel}
    joined = []
    for t in first:
        rec = {
            "date": t.get("date"),
            "symbol": _bare(t.get("symbol")),
            "anchor": trade_clock(t),
            "pnl_yen_100": _f(t.get("pnl_yen_100")),
            "fill_price": _f(t.get("fill_price")),
            "exit_reason": t.get("exit_reason"),
        }
        p = idx.get((str(t.get("date")), trade_clock(t), _bare(t.get("symbol")))) or {}
        rec["TARGET_V4"] = _f(p.get(TARGET))
        rec["WOULD_FILL_1S"] = bool(p.get("WOULD_FILL_1S"))
        rec["FILL_TO_600S_RETURN"] = _f(p.get("FILL_TO_600S_RETURN"))
        rec[score_key] = _f(p.get(score_key))
        mark = _f(p.get("m4_t1_px"))
        fp = rec["fill_price"]
        if rec["FILL_TO_600S_RETURN"] is None and mark is not None and fp is not None and fp > 0:
            rec["FILL_TO_600S_RETURN"] = float(mark) / float(fp) - 1.0
        joined.append(rec)
    pack = pack_metrics(first, list(ELIGIBLE_DAYS))
    re_pack = pack_metrics(reent, list(ELIGIBLE_DAYS))
    return {
        "first_n": len(first),
        "first_PnL": pack.get("PnL"),
        "first_PF": pack.get("PF"),
        "re_n": len(reent),
        "re_PnL": re_pack.get("PnL"),
        "re_PF": re_pack.get("PF"),
        "TARGET_V4_MEAN": _mean([r.get("TARGET_V4") for r in joined]),
        "WOULD_FILL_RATE": _rate([bool(r.get("WOULD_FILL_1S")) for r in joined]),
        "FILL_TO_600_MEAN": _mean([r.get("FILL_TO_600S_RETURN") for r in joined]),
        "FILL_TO_600_MEDIAN": _median([r.get("FILL_TO_600S_RETURN") for r in joined]),
        "FILL_TO_600_POS_RATE": _pos_rate([r.get("FILL_TO_600S_RETURN") for r in joined]),
        "actual_PnL": pack.get("PnL"),
        "n_joined": len(joined),
    }


def entry_vs_exit(trades: list[dict[str, Any]], panel: list[dict[str, Any]]) -> dict[str, Any]:
    idx = {row_key({"date": r.get("date"), "anchor": r.get("anchor"), "symbol": r.get("symbol")}): r for r in panel}
    exit_coupling = 0
    entry_coupling = 0
    pos_pos = 0
    other = 0
    f6s: list[float] = []
    pnls: list[float] = []
    for t in trades:
        p = idx.get((str(t.get("date")), trade_clock(t), _bare(t.get("symbol")))) or {}
        f6 = _f(p.get("FILL_TO_600S_RETURN"))
        fp = _f(t.get("fill_price"))
        mark = _f(p.get("m4_t1_px"))
        if f6 is None and mark is not None and fp is not None and fp > 0:
            f6 = float(mark) / float(fp) - 1.0
        pnl = _f(t.get("pnl_yen_100"))
        if f6 is None or pnl is None:
            other += 1
            continue
        f6s.append(f6)
        pnls.append(pnl)
        if f6 > 0 and pnl < 0:
            exit_coupling += 1
        elif f6 <= 0 and pnl < 0:
            entry_coupling += 1
        elif f6 > 0 and pnl > 0:
            pos_pos += 1
        else:
            other += 1
    if exit_coupling > 0 and entry_coupling > 0:
        klass = "BOTH"
    elif exit_coupling > 0 and entry_coupling == 0:
        klass = "EXIT"
    elif entry_coupling > 0 and exit_coupling == 0:
        klass = "ENTRY"
    else:
        klass = "INCONCLUSIVE"
    return {
        "n_fills": len(trades),
        "n_classified": exit_coupling + entry_coupling + pos_pos + other,
        "FILL_TO_600_positive_EXIT_negative": exit_coupling,
        "FILL_TO_600_nonpositive_EXIT_negative": entry_coupling,
        "both_positive": pos_pos,
        "other": other,
        "FILL_TO_600_MEAN": _mean(f6s),
        "EXIT_PNL_MEAN": _mean(pnls),
        "ENTRY_OR_EXIT_FAILURE": klass,
    }


def _occ_idx(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(str(r.get("date")), str(r.get("anchor"))): r for r in rows}


def _cand_idx(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out = {}
    for r in rows:
        out[(str(r.get("date")), str(r.get("anchor")), _bare(r.get("symbol")))] = r
    return out


def _set_idx(rows: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    return {(str(r.get("date")), str(r.get("anchor") or r.get("anchor_time") or ""), _bare(r.get("symbol"))) for r in rows}


def lineage_lost(
    a2_trades: list[dict[str, Any]],
    c3_trades: list[dict[str, Any]],
    *,
    c3_traces: dict[str, Any],
    panel: list[dict[str, Any]],
) -> dict[str, Any]:
    def _slot(t: dict[str, Any]) -> tuple[str, str, str]:
        return (str(t.get("date") or ""), trade_clock(t), _bare(t.get("symbol")))

    a2_slots = [_slot(t) for t in a2_trades]
    c3_slots = [_slot(t) for t in c3_trades]
    a2_keys = set(a2_slots)
    c3_keys = set(c3_slots)
    both = a2_keys & c3_keys
    a2_only_slots = [k for k in a2_slots if k not in c3_keys]
    a2_only = set(a2_only_slots)
    c3_only = c3_keys - a2_keys
    lost_n = sum(1 for k in a2_slots if k not in c3_keys)
    gained_n = sum(1 for k in c3_slots if k not in a2_keys)
    occ = _occ_idx(list(c3_traces.get("occupancy") or []))
    cands = _cand_idx(list(c3_traces.get("candidates") or []))
    admits = _set_idx(list(c3_traces.get("admits") or []))
    expired = _set_idx(list(c3_traces.get("expired") or []))
    fills = _set_idx(list(c3_traces.get("fills") or []))
    causes: Counter[str] = Counter()
    detail = []
    for k in a2_only_slots:
        day, an, sym = k
        snap = occ.get((day, an)) or {}
        open_set = set(_bare(s) for s in (snap.get("open") or []))
        pend_set = set(_bare(s) for s in (snap.get("pending") or []))
        exp = int(snap.get("exposure") or 0)
        cand = cands.get(k)
        if k in admits or k in expired or k in fills:
            cause = "PENDING_NO_FILL"
        elif sym in open_set or sym in pend_set:
            cause = "SAME_SYMBOL_PATH_CHANGE"
        elif exp >= POSITION_CAP:
            cause = "OCCUPANCY_CASCADE"
        elif cand is not None and cand.get("admitted") is False and exp >= POSITION_CAP:
            cause = "CAP_PATH_CHANGE"
        elif cand is not None and not cand.get("admitted"):
            if exp >= POSITION_CAP:
                cause = "CAP_PATH_CHANGE"
            else:
                cause = "RANK_REPLACEMENT_NO_FILL"
        elif cand is None:
            if exp >= POSITION_CAP:
                cause = "OCCUPANCY_CASCADE"
            else:
                cause = "RANK_REPLACEMENT_NO_FILL"
        else:
            cause = "OTHER"
        causes[cause] += 1
        if len(detail) < 80:
            detail.append({"date": day, "anchor": an, "symbol": sym, "cause": cause, "exposure": exp})
    primary = None
    if causes:
        primary = causes.most_common(1)[0][0]
    cause_sum = int(sum(causes.values()))
    return {
        "A2_N": len(a2_trades),
        "C3_N": len(c3_trades),
        "BOTH_N": sum(1 for k in a2_slots if k in c3_keys),
        "C3_LOST_VS_A2_N": lost_n,
        "C3_GAINED_VS_A2_N": gained_n,
        "NET_TRADE_DELTA": len(a2_trades) - len(c3_trades),
        "lost_minus_gained": lost_n - gained_n,
        "unique_a2_slots": len(a2_keys),
        "unique_c3_slots": len(c3_keys),
        "causes": dict(causes),
        "cause_sum": cause_sum,
        "cause_sum_matches_lost": cause_sum == lost_n,
        "PRIMARY_LOST_TRADE_CAUSE": primary,
        "detail_head": detail,
    }


def slot_occupancy_sec(trades: list[dict[str, Any]]) -> Optional[float]:
    xs = [_f(t.get("holding_sec")) for t in trades]
    vs = [v for v in xs if v is not None]
    return float(sum(vs)) if vs else None


def decide(
    *,
    a2_ok: bool,
    c3_ok: bool,
    oof_pack: dict[str, Any] | None,
    a2_pack: dict[str, Any] | None,
    c3_pack: dict[str, Any] | None,
    current_top3: dict[str, Any],
    oof_top3: dict[str, Any],
    final_top3: dict[str, Any],
    repl_oof: dict[str, Any],
    repl_final: dict[str, Any],
    fill_split_oof: dict[str, Any],
    fill_split_final: dict[str, Any],
    lineage: dict[str, Any],
    entry_exit: dict[str, Any],
    first: dict[str, Any],
) -> dict[str, Any]:
    if not a2_ok or not c3_ok:
        return {
            "VERDICT": "C3_EXECUTION_AUDIT_FAILED",
            "PRIMARY_CAUSE": "PARITY_FAIL",
            "FINAL_SPEC_INSTABILITY": "inconclusive",
            "UPWARD_TARGET_LOW_FILLABILITY": False,
            "PASSIVE_FILL_ADVERSE_SELECTION": False,
            "RECOMMENDED_NEXT_RESEARCH": "NONE",
            "reason": "A2 or C3 FINAL Exact parity failed. Audit stopped.",
        }

    oof_pnl = _f((oof_pack or {}).get("PnL"))
    oof_pf = _pf((oof_pack or {}).get("PF"))
    a2_pnl = _f((a2_pack or {}).get("PnL"))
    a2_pf = _pf((a2_pack or {}).get("PF"))
    c3_pnl = _f((c3_pack or {}).get("PnL"))
    oof_trades = int((oof_pack or {}).get("trades") or 0)
    a2_trades = int((a2_pack or {}).get("trades") or 0)

    oof_fail = bool(oof_pnl is None or oof_pnl <= 0 or oof_pf < 1.0)
    oof_strong = bool(
        oof_pnl is not None
        and a2_pnl is not None
        and oof_pnl > a2_pnl
        and oof_pf > a2_pf
        and a2_trades > 0
        and oof_trades >= 0.70 * a2_trades
    )

    cur_up = _f(current_top3.get("TARGET_UPLIFT"))
    oof_up = _f(oof_top3.get("TARGET_UPLIFT"))
    fin_up = _f(final_top3.get("TARGET_UPLIFT"))
    cur_fill = _f(current_top3.get("WOULD_FILL_1S_RATE"))
    oof_fill = _f(oof_top3.get("WOULD_FILL_1S_RATE"))
    fin_fill = _f(final_top3.get("WOULD_FILL_1S_RATE"))

    c3_up = oof_up if oof_up is not None else fin_up
    c3_fill = oof_fill if oof_fill is not None else fin_fill
    upward = bool(
        c3_up is not None
        and cur_up is not None
        and c3_up > cur_up
        and c3_fill is not None
        and cur_fill is not None
        and c3_fill < cur_fill
    )

    oof_f6 = _f(((fill_split_oof.get("fillable") or {}).get("FILL_TO_600S_RETURN_MEAN")))
    if oof_f6 is None:
        oof_f6 = _f(oof_top3.get("FILL_TO_600S_RETURN_MEAN"))
    fin_f6 = _f(((fill_split_final.get("fillable") or {}).get("FILL_TO_600S_RETURN_MEAN")))
    if fin_f6 is None:
        fin_f6 = _f(final_top3.get("FILL_TO_600S_RETURN_MEAN"))
    cur_f6 = _f(current_top3.get("FILL_TO_600S_RETURN_MEAN"))
    c3_f6 = oof_f6 if oof_f6 is not None else fin_f6
    adverse = bool(c3_f6 is not None and (c3_f6 <= 0 or (cur_f6 is not None and c3_f6 <= cur_f6)))

    exit_fail = str(entry_exit.get("ENTRY_OR_EXIT_FAILURE") or "")
    exit_coupling_flag = bool(
        exit_fail == "EXIT"
        or (
            int(entry_exit.get("FILL_TO_600_positive_EXIT_negative") or 0) > 0
            and _f(entry_exit.get("FILL_TO_600_MEAN")) is not None
            and float(entry_exit.get("FILL_TO_600_MEAN") or 0) > 0
            and c3_pnl is not None
            and c3_pnl < 0
        )
    )
    portfolio_flag = str(lineage.get("PRIMARY_LOST_TRADE_CAUSE") or "") in {
        "CAP_PATH_CHANGE",
        "OCCUPANCY_CASCADE",
        "SAME_SYMBOL_PATH_CHANGE",
    }
    lost_n = int(lineage.get("C3_LOST_VS_A2_N") or 0)
    causes = dict(lineage.get("causes") or {})
    port_n = int(causes.get("CAP_PATH_CHANGE") or 0) + int(causes.get("OCCUPANCY_CASCADE") or 0) + int(
        causes.get("SAME_SYMBOL_PATH_CHANGE") or 0
    )
    portfolio_majority = bool(lost_n > 0 and port_n / lost_n >= 0.50)

    final_instability = bool(oof_strong and (c3_pnl is None or c3_pnl <= 0))
    if oof_strong and not oof_fail:
        final_spec_flag: Any = True
    elif oof_fail:
        final_spec_flag = False
    else:
        final_spec_flag = "inconclusive"

    factors = []
    if upward:
        factors.append("FILLABILITY_MISMATCH")
    if adverse:
        factors.append("ADVERSE_SELECTION")
    if portfolio_majority:
        factors.append("PORTFOLIO")
    if exit_coupling_flag and exit_fail in {"EXIT", "BOTH"}:
        factors.append("EXIT")
    if final_instability:
        factors.append("FINAL_SPEC")

    if final_instability and oof_strong:
        verdict = "C3_FINAL_SPEC_INSTABILITY"
        primary = "FINAL_SPEC_SELECTION"
        rec = "FINAL_SPEC_SELECTION"
    elif len(factors) >= 2:
        verdict = "C3_MULTIFACTOR_EXACT_FAILURE"
        if "FILLABILITY_MISMATCH" in factors:
            primary = "PRICE_TARGET_FILLABILITY_MISMATCH"
            rec = "EXECUTION_AWARE_OBJECTIVE"
        elif "ADVERSE_SELECTION" in factors:
            primary = "PASSIVE_FILL_ADVERSE_SELECTION"
            rec = "EXECUTION_AWARE_OBJECTIVE"
        elif "PORTFOLIO" in factors:
            primary = "PORTFOLIO_COUPLING"
            rec = "PORTFOLIO_ARCHITECTURE"
        else:
            primary = "MULTIFACTOR"
            rec = "NONE"
    elif oof_fail and upward:
        verdict = "C3_PRICE_TARGET_FILLABILITY_MISMATCH"
        primary = "PRICE_TARGET_FILLABILITY_MISMATCH"
        rec = "EXECUTION_AWARE_OBJECTIVE"
    elif oof_fail and adverse:
        verdict = "C3_PASSIVE_FILL_ADVERSE_SELECTION"
        primary = "PASSIVE_FILL_ADVERSE_SELECTION"
        rec = "EXECUTION_AWARE_OBJECTIVE"
    elif portfolio_majority:
        verdict = "C3_PORTFOLIO_COUPLING"
        primary = "PORTFOLIO_COUPLING"
        rec = "PORTFOLIO_ARCHITECTURE"
    elif exit_coupling_flag and exit_fail == "EXIT":
        verdict = "C3_EXIT_COUPLING"
        primary = "EXIT_COUPLING"
        rec = "EXIT"
    else:
        verdict = "C3_EXECUTION_AUDIT_INCONCLUSIVE"
        primary = "INCONCLUSIVE"
        rec = "NONE"

    return {
        "VERDICT": verdict,
        "PRIMARY_CAUSE": primary,
        "RECOMMENDED_NEXT_RESEARCH": rec,
        "FINAL_SPEC_INSTABILITY": final_spec_flag,
        "UPWARD_TARGET_LOW_FILLABILITY": upward,
        "PASSIVE_FILL_ADVERSE_SELECTION": adverse,
        "OOF_EXACT_FAIL": oof_fail,
        "OOF_EXACT_STRONG": oof_strong,
        "EXIT_COUPLING_FLAG": exit_coupling_flag,
        "PORTFOLIO_MAJORITY": portfolio_majority,
        "factors": factors,
        "CURRENT_TOP3_UPLIFT": cur_up,
        "C3_OOF_TOP3_UPLIFT": oof_up,
        "C3_FINAL_TOP3_UPLIFT": fin_up,
        "CURRENT_TOP3_FILL": cur_fill,
        "C3_OOF_TOP3_FILL": oof_fill,
        "C3_FINAL_TOP3_FILL": fin_fill,
        "CURRENT_FILL_TO_600": cur_f6,
        "C3_OOF_FILL_TO_600": oof_f6,
        "C3_FINAL_FILL_TO_600": fin_f6,
        "first_entry_pnl": first.get("first_PnL"),
        "ENTRY_OR_EXIT_FAILURE": entry_exit.get("ENTRY_OR_EXIT_FAILURE"),
        "repl_oof_c3_only_target": ((repl_oof.get("C3_ONLY") or {}).get("TARGET_V4_600S")),
        "repl_oof_current_only_target": ((repl_oof.get("CURRENT_ONLY") or {}).get("TARGET_V4_600S")),
        "repl_final_c3_only_target": ((repl_final.get("C3_ONLY") or {}).get("TARGET_V4_600S")),
    }


def questions(dec: dict[str, Any], lineage: dict[str, Any], first: dict[str, Any], entry_exit: dict[str, Any]) -> dict[str, Any]:
    q1 = (
        "No. OOF-stitched Exact did not convert the OOF ranking edge into profit."
        if dec.get("OOF_EXACT_FAIL")
        else (
            "OOF-stitched Exact improved vs A2; FINAL Exact still failed."
            if dec.get("OOF_EXACT_STRONG")
            else "OOF-stitched Exact did not clearly convert ranking edge into a profitable Exact path."
        )
    )
    c3_t = dec.get("repl_oof_c3_only_target")
    cur_t = dec.get("repl_oof_current_only_target")
    c3_f = dec.get("C3_OOF_TOP3_FILL")
    cur_f = dec.get("CURRENT_TOP3_FILL")
    q2 = bool(
        c3_t is not None and cur_t is not None and float(c3_t) > float(cur_t) and c3_f is not None and cur_f is not None and float(c3_f) < float(cur_f)
    )
    q3 = bool(dec.get("UPWARD_TARGET_LOW_FILLABILITY"))
    q4 = bool(dec.get("PASSIVE_FILL_ADVERSE_SELECTION"))
    q5 = lineage.get("PRIMARY_LOST_TRADE_CAUSE")
    f6 = first.get("FILL_TO_600_MEAN")
    q6 = bool(f6 is not None and float(f6) <= 0) or (
        first.get("first_PnL") is not None and float(first.get("first_PnL") or 0) < 0 and (f6 is None or float(f6) <= 0)
    )
    q7 = str(entry_exit.get("ENTRY_OR_EXIT_FAILURE") or "") in {"EXIT", "BOTH"}
    q8 = dec.get("RECOMMENDED_NEXT_RESEARCH")
    return {
        "Q1": q1,
        "Q2": q2,
        "Q3": q3,
        "Q4": q4,
        "Q5": q5,
        "Q6": q6,
        "Q7": q7,
        "Q8": q8,
    }
