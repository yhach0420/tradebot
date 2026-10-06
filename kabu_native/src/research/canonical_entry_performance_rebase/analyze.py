"""Canonical rebase analytics. No refit. Rank then join TARGET. No target backfill."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.anchor_vs_event_driven.run_comparison import _bare
from research.c3_execution_coupling_audit.analyze import admit_clock
from research.canonical_entry_performance_rebase import (
    DD_TOL,
    MIN_COHORT_N,
    PF_TOL_PACK,
    PNL_TOL,
    TRADE_TOL,
)
from research.edge_decay_rca.analyze import tag_entry_kind
from research.entry_decision_population_contract.analyze import join_origin
from research.entry_objective_redesign_c3.analyze import paired_daily
from research.entry_objective_redesign_c3.oof import TARGET, spearman
from research.executable_target_v2_b_threshold.analyze import pack_metrics
from research.uniform10_entry_rebuild_v2.eligibility import modeling_bucket, rebase, target_v4


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def row_key(r: dict[str, Any]) -> str:
    return f"{r.get('date')}|{r.get('anchor')}|{_bare(r.get('symbol'))}"


def _yen(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(x - round(x)) < 1e-9:
        i = int(round(x))
        return f"+{i}" if i > 0 else str(i)
    return f"+{x}" if x > 0 else str(x)


def pack_line(pack: dict[str, Any] | None) -> str:
    if not pack:
        return "n/a"
    pnl = pack.get("PnL") if pack.get("PnL") is not None else pack.get("pnl")
    return f"{pack.get('trades')} / {_yen(pnl)} / {pack.get('PF')} / {_yen(pack.get('maxDD'))}"


def session_of(r: dict[str, Any]) -> str:
    sess = str(r.get("session") or "").upper()
    if sess in {"AM", "PM"}:
        return sess
    an = str(r.get("anchor") or "00:00")
    return "AM" if an < "12:00" else "PM"


def slim_rank(d: dict[str, Any]) -> dict[str, Any]:
    skip = {"daily_top1", "daily_top3", "daily_top5", "daily_top10", "daily_spearman", "daily_spearman"}
    return {k: v for k, v in d.items() if not str(k).startswith("daily_")}


def wf_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        an = r.get("anchor") or r.get("anchor_time") or ""
        k = f"{r.get('date')}|{an}|{_bare(r.get('symbol'))}"
        out[k] = r
    return out


def parity_against(pack: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    trades = int(pack.get("trades") or 0)
    pnl = float(pack.get("PnL") if pack.get("PnL") is not None else pack.get("pnl") or 0.0)
    pf_raw = pack.get("PF")
    try:
        pf = float("inf") if pf_raw in ("Infinity", float("inf")) else float(pf_raw)
    except (TypeError, ValueError):
        pf = 0.0
    dd = float(pack.get("maxDD") or 0.0)
    ok = (
        abs(trades - int(expected["trades"])) <= TRADE_TOL
        and abs(pnl - float(expected["PnL"])) <= PNL_TOL
        and abs(pf - float(expected["PF"])) <= PF_TOL_PACK
        and abs(dd - float(expected["maxDD"])) <= DD_TOL
    )
    return {
        "ok": ok,
        "observed": {"trades": trades, "PnL": pnl, "PF": pack.get("PF"), "maxDD": dd},
        "expected": dict(expected),
    }


def join_target(snap: dict[str, Any], panel: dict[str, Any] | None) -> dict[str, Any]:
    rec = dict(snap)
    rec["symbol"] = _bare(rec.get("symbol"))
    p = panel or {}
    for k in (
        "m4_t0_px",
        "m4_t1_px",
        "m4_t0_age",
        "m4_t1_age",
        "m4_t_lo0_reason",
        "m4_t_lo1_reason",
        "tse_t0_cont",
        "tse_t1_cont",
        "t0_phase",
        "t1_phase",
        "last_state",
        "last_ask_sign",
        "last_bid_sign",
        "last_special",
        "last_lag",
        "last_bid",
        "last_ask",
        "empty",
        "universe_n",
        "modeling_bucket",
        TARGET,
    ):
        if k not in rec or rec.get(k) is None:
            rec[k] = p.get(k)
    if rec.get("modeling_bucket") is None:
        rec["modeling_bucket"] = modeling_bucket(rec)
    if rec.get(TARGET) is None:
        rec[TARGET] = target_v4(rec)
    rec["target_valid"] = _f(rec.get(TARGET)) is not None
    return rec


def target_integrity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reb = rebase(rows)
    unexp = int(reb.get("UNEXPECTED_TARGET_MISSING") or 0)
    contam = (
        int(reb.get("ITAYOSE_TARGET_CONTAMINATION") or 0)
        + int(reb.get("SPECIAL_QUOTE_CONTAMINATION") or 0)
        + int(reb.get("CLOSING_AUCTION_ENDPOINT_CONTAMINATION") or 0)
        + int(reb.get("FUTURE_EVENT_USE") or 0)
    )
    return {
        **reb,
        "TARGET_V4_VALID_N": int(reb.get("TARGET_V4_ROWS") or 0),
        "TARGET_V4_MISSING_N": len(rows) - int(reb.get("TARGET_V4_ROWS") or 0),
        "TARGET_UNEXPECTED_MISSING_N": unexp,
        "TARGET_CONTAMINATION_N": contam,
        "ok": unexp == 0 and contam == 0,
    }


def populations(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    current_actual = []
    c3_oof_actual = []
    c3_final_actual = []
    matched = []
    for r in rows:
        cur = _f(r.get("current_score")) is not None
        oof = _f(r.get("c3_oof_score")) is not None
        fin = _f(r.get("c3_live_score")) is not None
        exe = bool(r.get("exact_executable") or r.get("canonical_executable"))
        if cur:
            current_actual.append(r)
        if exe and oof:
            c3_oof_actual.append(r)
        if exe and fin:
            c3_final_actual.append(r)
        if exe and cur and oof:
            matched.append(r)
    return {
        "MATCHED_RANKING_POPULATION": matched,
        "CURRENT_ACTUAL_SCORABLE": current_actual,
        "C3_OOF_ACTUAL_SCORABLE": c3_oof_actual,
        "C3_FINAL_ACTUAL_SCORABLE": c3_final_actual,
    }


def _cohorts(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[(str(r.get("date")), str(r.get("anchor")))].append(r)
    return by


def rank_group(grp: list[dict[str, Any]], score_key: str) -> list[dict[str, Any]]:
    xs = [r for r in grp if _f(r.get(score_key)) is not None]
    xs.sort(key=lambda r: (-float(r[score_key]), str(r.get("symbol") or "")))
    return xs


def topk_fixed(ranked: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """Fixed rank positions. Do not skip target-missing names."""
    return ranked[:k]


def _mean(xs: list[Optional[float]]) -> Optional[float]:
    vs = [float(v) for v in xs if v is not None]
    return float(np.mean(vs)) if vs else None


def _rate(xs: list[bool]) -> Optional[float]:
    if not xs:
        return None
    return float(sum(1 for v in xs if v) / len(xs))


def cohort_rank_block(grp: list[dict[str, Any]], score_key: str) -> Optional[dict[str, Any]]:
    ranked = rank_group(grp, score_key)
    n = len(ranked)
    if n < int(MIN_COHORT_N):
        return None
    all_t = [_f(r.get(TARGET)) for r in ranked]
    all_valid = [v for v in all_t if v is not None]
    all_m = float(np.mean(all_valid)) if all_valid else None
    miss = {
        1: 0,
        3: 0,
        5: 0,
        10: 0,
    }
    out: dict[str, Any] = {
        "n": n,
        "n_target_valid": len(all_valid),
        "ALL_TARGET": all_m,
        "TARGET_V4_MISSING_N": n - len(all_valid),
    }
    pairs_s = []
    pairs_y = []
    for r in ranked:
        sc = _f(r.get(score_key))
        yt = _f(r.get(TARGET))
        if sc is not None and yt is not None:
            pairs_s.append(sc)
            pairs_y.append(yt)
    out["spearman"] = spearman(pairs_s, pairs_y) if len(pairs_s) >= int(MIN_COHORT_N) else None

    def top_stats(k: int) -> tuple[Optional[float], Optional[float], int]:
        pos = topk_fixed(ranked, k)
        tgs = [_f(r.get(TARGET)) for r in pos]
        n_miss = sum(1 for v in tgs if v is None)
        tm = _mean(tgs)
        upl = (tm - all_m) if tm is not None and all_m is not None else None
        return tm, upl, n_miss

    for k in (1, 3, 5, 10):
        tm, upl, n_miss = top_stats(k)
        miss[k] = n_miss
        out[f"TOP{k}_TARGET"] = tm
        out[f"TOP{k}_UPLIFT"] = upl
        out[f"TOP{k}_POSITION_TARGET_MISSING_N"] = n_miss
    return out


def ranking_no_backfill(rows: list[dict[str, Any]], score_key: str) -> dict[str, Any]:
    by = _cohorts(rows)
    per = []
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    n_excl = 0
    miss1 = miss3 = miss5 = 0
    for (day, an), grp in sorted(by.items()):
        blk = cohort_rank_block(grp, score_key)
        if blk is None:
            n_excl += 1
            continue
        rec = {"date": day, "anchor": an, **blk}
        per.append(rec)
        by_day[day].append(rec)
        miss1 += int(blk.get("TOP1_POSITION_TARGET_MISSING_N") or 0)
        miss3 += int(blk.get("TOP3_POSITION_TARGET_MISSING_N") or 0)
        miss5 += int(blk.get("TOP5_POSITION_TARGET_MISSING_N") or 0)

    def daily_mean(field: str) -> tuple[Optional[float], list[dict[str, Any]]]:
        days = []
        for d, xs in sorted(by_day.items()):
            vs = [x[field] for x in xs if x.get(field) is not None]
            if not vs:
                continue
            days.append({"date": d, field: float(np.mean(vs)), "n_cohorts": len(vs)})
        vals = [x[field] for x in days]
        return (float(np.mean(vals)) if vals else None, days)

    fields = (
        "ALL_TARGET",
        "TOP1_TARGET",
        "TOP3_TARGET",
        "TOP5_TARGET",
        "TOP10_TARGET",
        "TOP1_UPLIFT",
        "TOP3_UPLIFT",
        "TOP5_UPLIFT",
        "TOP10_UPLIFT",
        "spearman",
    )
    out: dict[str, Any] = {
        "score_key": score_key,
        "n_cohorts_total": len(by),
        "n_cohorts_used": len(per),
        "n_cohorts_excluded_lt10": n_excl,
        "TOP1_TARGET_MISSING_N": miss1,
        "TOP3_POSITION_TARGET_MISSING_N": miss3,
        "TOP5_POSITION_TARGET_MISSING_N": miss5,
    }
    for f in fields:
        m, days = daily_mean(f)
        out["MEAN_DAILY_SPEARMAN" if f == "spearman" else f] = m
        if f == "TOP1_UPLIFT":
            out["daily_top1"] = days
        elif f == "TOP3_UPLIFT":
            out["daily_top3"] = days
        elif f == "TOP5_UPLIFT":
            out["daily_top5"] = days
        elif f == "TOP10_UPLIFT":
            out["daily_top10"] = days
        elif f == "spearman":
            out["daily_spearman"] = days
    out["n_days"] = len(out.get("daily_top3") or [])
    return out


def attach_would_fill(rows: list[dict[str, Any]], wf_by: dict[str, dict[str, Any]]) -> None:
    for r in rows:
        wf = wf_by.get(row_key(r)) or {}
        r["WOULD_FILL_1S"] = bool(wf.get("WOULD_FILL_1S")) if wf else False
        fill_px = _f(wf.get("fill_price"))
        mark = _f(r.get("m4_t1_px"))
        if r["WOULD_FILL_1S"] and fill_px is not None and fill_px > 0 and mark is not None and mark > 0:
            r["FILL_TO_600S_RETURN"] = float(mark) / float(fill_px) - 1.0
        else:
            r["FILL_TO_600S_RETURN"] = None


def topk_exec_block(rows: list[dict[str, Any]], score_key: str, k: int) -> dict[str, Any]:
    picked: list[dict[str, Any]] = []
    miss_t = 0
    for grp in _cohorts(rows).values():
        ranked = rank_group(grp, score_key)
        pos = topk_fixed(ranked, k)
        for r in pos:
            if _f(r.get(TARGET)) is None:
                miss_t += 1
        picked.extend(pos)
    tgs = [_f(r.get(TARGET)) for r in picked]
    wfs = [bool(r.get("WOULD_FILL_1S")) for r in picked]
    f6 = [_f(r.get("FILL_TO_600S_RETURN")) for r in picked if r.get("WOULD_FILL_1S")]
    return {
        "K": k,
        "score_key": score_key,
        "N": len(picked),
        "TARGET_V4": _mean(tgs),
        "TARGET_POSITION_MISSING_N": miss_t,
        "WOULD_FILL_1S_RATE": _rate(wfs),
        "WOULD_FILL_1S_N": int(sum(1 for v in wfs if v)),
        "FILL_TO_600S_RETURN_MEAN": _mean(f6),
        "FILL_TO_600S_N": len([v for v in f6 if v is not None]),
    }


def coverage_loss(
    current_actual: list[dict[str, Any]],
    c3_actual: list[dict[str, Any]],
) -> dict[str, Any]:
    c3_keys = {row_key(r) for r in c3_actual}
    lost = [r for r in current_actual if row_key(r) not in c3_keys]
    by_anchor: Counter[str] = Counter()
    by_day: Counter[str] = Counter()
    by_sess: Counter[str] = Counter()
    for r in lost:
        by_anchor[str(r.get("anchor") or "")] += 1
        by_day[str(r.get("date") or "")] += 1
        by_sess[session_of(r)] += 1
    disp = 0
    for grp in _cohorts(current_actual).values():
        ranked = rank_group(grp, "current_score")
        for r in topk_fixed(ranked, 5):
            if row_key(r) not in c3_keys:
                disp += 1
    return {
        "C3_SCORE_UNAVAILABLE_N": len(lost),
        "C3_COVERAGE_LOSS_ROWS": len(lost),
        "by_anchor": dict(by_anchor),
        "by_day": dict(by_day),
        "by_session": dict(by_sess),
        "C3_COVERAGE_SELECTION_DISPLACEMENT_N": disp,
        "displacement_def": "CURRENT Top5 (score, symbol ASC) names not in C3 actual scorable set",
    }


def trade_fill_to_600_origin(
    t: dict[str, Any],
    admits: list[dict[str, Any]],
    panel_by: dict[str, dict[str, Any]],
) -> tuple[Optional[float], str]:
    origin = join_origin(t, admits)
    day = str(t.get("date") or "")
    if origin is not None:
        an = str(origin.get("anchor") or "")
        how = "PENDING.anchor"
    else:
        an = admit_clock(day, t.get("fill_time")) or str(t.get("anchor_time") or t.get("anchor") or "")
        how = "admit_clock_fallback"
    k = f"{day}|{an}|{_bare(t.get('symbol'))}"
    p = panel_by.get(k) or {}
    mark = _f(p.get("m4_t1_px"))
    fp = _f(t.get("fill_price"))
    if mark is None or fp is None or fp <= 0:
        return None, how
    return float(mark) / float(fp) - 1.0, how


def exit_classes_canonical(
    trades: list[dict[str, Any]],
    admits: list[dict[str, Any]],
    panel_by: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    a = b = c = d = miss = 0
    n_origin = n_fallback = 0
    rows = []
    for t in trades:
        f6, how = trade_fill_to_600_origin(t, admits, panel_by)
        if how == "PENDING.anchor":
            n_origin += 1
        else:
            n_fallback += 1
        pnl = _f(t.get("pnl_yen_100"))
        if f6 is None or pnl is None:
            miss += 1
            klass = "MISSING"
        elif f6 > 0 and pnl > 0:
            a += 1
            klass = "A"
        elif f6 > 0 and pnl <= 0:
            b += 1
            klass = "B"
        elif f6 <= 0 and pnl > 0:
            c += 1
            klass = "C"
        else:
            d += 1
            klass = "D"
        rows.append(
            {
                "trade_id": t.get("trade_id"),
                "date": t.get("date"),
                "symbol": _bare(t.get("symbol")),
                "FILL_TO_600": f6,
                "EXIT_PNL": pnl,
                "class": klass,
                "join": how,
            }
        )
    total = a + b + c + d
    return {
        "C3_EXIT_CLASS_A_N": a,
        "C3_EXIT_CLASS_B_N": b,
        "C3_EXIT_CLASS_C_N": c,
        "C3_EXIT_CLASS_D_N": d,
        "MISSING_N": miss,
        "SUM_ABCD": total,
        "assert_n": len(trades),
        "ok": total == len(trades) and miss == 0,
        "n_pending_origin": n_origin,
        "n_admit_clock_fallback": n_fallback,
        "rows": rows,
    }


def first_entry_block(
    trades: list[dict[str, Any]],
    admits: list[dict[str, Any]],
    panel_by: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tagged = tag_entry_kind(trades)
    first = [t for t in tagged if t.get("entry_kind") == "FIRST_ENTRY"]
    f6s = [trade_fill_to_600_origin(t, admits, panel_by)[0] for t in first]
    pack = pack_metrics(first, None)
    vs = [v for v in f6s if v is not None]
    return {
        "FIRST_N": len(first),
        "FIRST_FILL_TO_600": float(np.mean(vs)) if vs else None,
        "FIRST_PNL": pack.get("PnL"),
        "FIRST_PF": pack.get("PF"),
    }


def ranking_edge_present(current: dict[str, Any], c3: dict[str, Any], paired: dict[str, Any]) -> bool:
    c3_t3 = _f(c3.get("TOP3_UPLIFT"))
    cur_t3 = _f(current.get("TOP3_UPLIFT"))
    dmean = _f(paired.get("TOP3_DELTA_MEAN"))
    return bool(
        c3_t3 is not None
        and c3_t3 > 0
        and cur_t3 is not None
        and c3_t3 > cur_t3
        and dmean is not None
        and dmean > 0
    )


def decide(
    *,
    parity_ok: bool,
    target_ok: bool,
    exit_ok: bool,
    rank_edge: bool,
    exact_fail: bool,
    adverse: bool,
) -> dict[str, Any]:
    note_train = (
        "Frozen C3 was trained on old Panel, including vwap_dist_bps reconstruction. "
        "CONFIRMED does not mean a Canonical training pipeline would reproduce the edge. "
        "NEXT is same C3 protocol retrain on Canonical data. Not C4. Not execution-aware."
    )
    if not parity_ok:
        return {
            "VERDICT": "CANONICAL_ENTRY_REBASE_FAILED",
            "CANONICAL_ENTRY_BASELINE_ESTABLISHED": False,
            "OLD_C3_INTERPRETATION": "INVALIDATED",
            "NEXT_RESEARCH": "NONE",
            "CASE": None,
            "note": "Exact parity anchor missed. Observed values not adopted as new SoT.",
        }
    if not target_ok:
        return {
            "VERDICT": "CANONICAL_ENTRY_REBASE_FAILED",
            "CANONICAL_ENTRY_BASELINE_ESTABLISHED": False,
            "OLD_C3_INTERPRETATION": "PARTIALLY_CONFIRMED",
            "NEXT_RESEARCH": "NONE",
            "CASE": None,
            "note": "TARGET V4 integrity failed (UNEXPECTED_MISSING or CONTAMINATION).",
        }
    if not exit_ok:
        return {
            "VERDICT": "CANONICAL_ENTRY_REBASE_FAILED",
            "CANONICAL_ENTRY_BASELINE_ESTABLISHED": False,
            "OLD_C3_INTERPRETATION": "PARTIALLY_CONFIRMED",
            "NEXT_RESEARCH": "NONE",
            "CASE": None,
            "note": "C3 FINAL 53 EXIT class partition failed (A+B+C+D != 53).",
        }
    if rank_edge and exact_fail and adverse:
        return {
            "VERDICT": "CANONICAL_C3_EXECUTION_MISMATCH_CONFIRMED",
            "CANONICAL_ENTRY_BASELINE_ESTABLISHED": True,
            "OLD_C3_INTERPRETATION": "CONFIRMED",
            "NEXT_RESEARCH": "C3_CANONICAL_RETRAIN_SAME_PROTOCOL",
            "CASE": "B",
            "note": note_train,
        }
    if not rank_edge:
        return {
            "VERDICT": "CANONICAL_C3_RANK_EDGE_INVALIDATED",
            "CANONICAL_ENTRY_BASELINE_ESTABLISHED": True,
            "OLD_C3_INTERPRETATION": "INVALIDATED",
            "NEXT_RESEARCH": "C3_CANONICAL_RETRAIN_SAME_PROTOCOL",
            "CASE": "A",
            "note": note_train,
        }
    if rank_edge or exact_fail or adverse:
        return {
            "VERDICT": "CANONICAL_C3_INTERPRETATION_PARTIALLY_CHANGED",
            "CANONICAL_ENTRY_BASELINE_ESTABLISHED": True,
            "OLD_C3_INTERPRETATION": "PARTIALLY_CONFIRMED",
            "NEXT_RESEARCH": "C3_CANONICAL_RETRAIN_SAME_PROTOCOL",
            "CASE": "B" if rank_edge else "A",
            "note": note_train,
        }
    return {
        "VERDICT": "CANONICAL_ENTRY_REBASE_INCONCLUSIVE",
        "CANONICAL_ENTRY_BASELINE_ESTABLISHED": True,
        "OLD_C3_INTERPRETATION": "PARTIALLY_CONFIRMED",
        "NEXT_RESEARCH": "C3_CANONICAL_RETRAIN_SAME_PROTOCOL",
        "CASE": None,
        "note": note_train,
    }
