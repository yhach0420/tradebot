"""V14 path taxonomy, event incidence, counterfactual event-exit vs HOLD. No EXIT rule. No threshold search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_entry_family.v3_analyze import concentration, day_rows, day_sign_counts, horizon_pack
from research.simple_tech_entry_family.v4_analyze import _mean, _median
from research.simple_tech_entry_family.v6_pullback_analyze import drop_symbol_mean, symbol_pack
from research.simple_tech_entry_family.v9_analyze import drop_top3_mean
from research.simple_tech_exit_family.v14_spec import (
    DEVELOPMENT_ENTRY_STACK,
    MECH_MIN_DISTINCT_DAYS,
    MECH_MIN_EVENT_N,
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _gt0(v: Any) -> bool:
    return v is not None and float(v) > 1e-12


def _ge0(v: Any) -> bool:
    return v is not None and float(v) >= -1e-12


def _pctl(xs: list[Any], q: float) -> Optional[float]:
    vs = [float(x) for x in xs if _finite(x)]
    if not vs:
        return None
    return float(np.percentile(vs, q))


def classify_horizon(row: dict[str, Any], h: int) -> str:
    ep = row.get(f"hold_{h}")
    mx = row.get(f"MAX_BEFORE_{h}")
    mn = row.get(f"MIN_BEFORE_{h}")
    if not _finite(ep):
        return "MISSING"
    epf = float(ep)
    if abs(epf) <= 1e-12:
        return "ZERO"
    max_pos = _finite(mx) and float(mx) > 1e-12
    min_neg = _finite(mn) and float(mn) < -1e-12
    if epf < 0.0:
        if not max_pos:
            return "P1_NEVER_POSITIVE"
        return "P2_PROFIT_TO_LOSS"
    if min_neg:
        return "P3_LOSS_TO_RECOVERY"
    return "P4_FINAL_POSITIVE"


def taxonomy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h in (180, 300):
        counts: dict[str, int] = defaultdict(int)
        for r in rows:
            lab = classify_horizon(r, h)
            r[f"class_{h}"] = lab
            counts[lab] += 1
        p1 = int(counts.get("P1_NEVER_POSITIVE") or 0)
        p2 = int(counts.get("P2_PROFIT_TO_LOSS") or 0)
        if p2 > p1:
            prim = "PROFIT_GIVEBACK"
        elif p1 > p2:
            prim = "EARLY_FAILURE"
        else:
            prim = "MIXED"
        out[h] = {
            "P1_NEVER_POSITIVE": p1,
            "P2_PROFIT_TO_LOSS": p2,
            "P3_LOSS_TO_RECOVERY": int(counts.get("P3_LOSS_TO_RECOVERY") or 0),
            "P4_FINAL_POSITIVE": int(counts.get("P4_FINAL_POSITIVE") or 0),
            "ZERO": int(counts.get("ZERO") or 0),
            "MISSING": int(counts.get("MISSING") or 0),
            "NEVER_POSITIVE_LOSER_N": p1,
            "PROFIT_TO_LOSS_N": p2,
            "PRIMARY_EXIT_PROBLEM": prim,
        }
    a = out[180]["PRIMARY_EXIT_PROBLEM"]
    b = out[300]["PRIMARY_EXIT_PROBLEM"]
    if a == b == "PROFIT_GIVEBACK":
        overall = "PROFIT_GIVEBACK"
    elif a == b == "EARLY_FAILURE":
        overall = "EARLY_FAILURE"
    elif a != b:
        overall = "HORIZON_DEPENDENT_MIXED"
    else:
        overall = "MIXED"
    out["PRIMARY_EXIT_PROBLEM"] = overall
    return out


def hold_benchmark(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pack = horizon_pack(rows)
    daily = day_rows(rows, list(ELIGIBLE_DAYS))
    out: dict[str, Any] = {"N": len(rows)}
    for h in (60, 180, 300):
        signs = day_sign_counts(daily, f"MARKOUT{h}_MEAN")
        conc = concentration(rows, f"markout_{h}")
        syms = symbol_pack(rows, f"markout_{h}")
        out[h] = {
            "mean": pack.get(f"MARKOUT_{h}_MEAN"),
            "median": pack.get(f"MARKOUT_{h}_MEDIAN"),
            "positive_rate": pack.get(f"MARKOUT_{h}_POS_RATE"),
            "POSITIVE_DAY_N": signs.get("POSITIVE_DAY_N"),
            "NEGATIVE_DAY_N": signs.get("NEGATIVE_DAY_N"),
            "EX_BEST": conc.get("EX_BEST_DAY_MARKOUT"),
            "EX_TOP3": conc.get("EX_TOP3_DAY_MARKOUT"),
            "DROP_TOP_SYMBOL": syms.get("DROP_TOP_SYMBOL_MARKOUT"),
            "DROP_TOP3_SYMBOL": drop_top3_mean(rows, f"markout_{h}"),
        }
    return out


def _event(row: dict[str, Any], eid: str) -> Optional[dict[str, Any]]:
    ev = (row.get("events") or {}).get(eid)
    return ev if isinstance(ev, dict) else None


def _in_horizon(row: dict[str, Any], eid: str, h: int) -> bool:
    ev = _event(row, eid)
    if not ev or not _finite(ev.get("event_t")) or not _finite(row.get("fill_t")):
        return False
    return float(ev["event_t"]) <= float(row["fill_t"]) + float(h) + 1e-12


def _winner(row: dict[str, Any], h: int) -> bool:
    v = row.get(f"hold_{h}")
    return _finite(v) and float(v) > 1e-12


def _loser(row: dict[str, Any], h: int) -> bool:
    v = row.get(f"hold_{h}")
    return _finite(v) and float(v) < -1e-12


def _delta_rows(rows: list[dict[str, Any]], eid: str, h: int) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not _in_horizon(r, eid, h):
            continue
        ev = _event(r, eid) or {}
        if ev.get("miss") or not _finite(ev.get("exit_pnl")) or not _finite(r.get(f"hold_{h}")):
            continue
        rec = dict(r)
        rec["delta"] = float(ev["exit_pnl"]) - float(r[f"hold_{h}"])
        rec[f"markout_{h}"] = rec["delta"]
        rec["markout_180"] = rec["delta"] if h == 180 else r.get("markout_180")
        rec["markout_300"] = rec["delta"] if h == 300 else r.get("markout_300")
        rec["markout_60"] = rec["delta"] if h == 60 else r.get("markout_60")
        out.append(rec)
    return out


def _paired_days(drows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[float]] = defaultdict(list)
    for r in drows:
        if _finite(r.get("delta")):
            by[str(r.get("date") or "")].append(float(r["delta"]))
    pos = neg = zero = 0
    for vs in by.values():
        m = float(np.mean(vs))
        if m > 1e-12:
            pos += 1
        elif m < -1e-12:
            neg += 1
        else:
            zero += 1
    return {
        "POSITIVE_PAIRED_DAY_N": pos,
        "NEGATIVE_PAIRED_DAY_N": neg,
        "ZERO_PAIRED_DAY_N": zero,
        "MULTI_DAY_POSITIVE": bool(pos > neg),
    }


def _robust_delta(drows: list[dict[str, Any]], h: int) -> dict[str, Any]:
    key = f"markout_{h}"
    conc = concentration(drows, key) if drows else {}
    top = symbol_pack(drows, key).get("TOP_SYMBOL") if drows else None
    paired = _paired_days(drows)
    xs = [r.get("delta") for r in drows]
    drop = drop_symbol_mean(drows, str(top or ""), key) if drows else None
    return {
        "N": len(drows),
        "mean": _mean(xs),
        "median": _median(xs),
        **paired,
        "EX_BEST": conc.get("EX_BEST_DAY_MARKOUT"),
        "DROP_TOP_SYMBOL": drop,
        "EX_BEST_OK": _gt0(conc.get("EX_BEST_DAY_MARKOUT")),
        "DROP_TOP_OK": _gt0(drop),
    }


def event_pack(rows: list[dict[str, Any]], eid: str, *, integrity_ok: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"EVENT_ID": eid}
    for h in (180, 300):
        hit = [r for r in rows if _in_horizon(r, eid, h)]
        days = {str(r.get("date") or "") for r in hit}
        tte = []
        for r in hit:
            ev = _event(r, eid) or {}
            if _finite(ev.get("event_t")) and _finite(r.get("fill_t")):
                tte.append(float(ev["event_t"]) - float(r["fill_t"]))
        winners = [r for r in rows if _winner(r, h)]
        losers = [r for r in rows if _loser(r, h)]
        w_hit = sum(1 for r in winners if _in_horizon(r, eid, h))
        l_hit = sum(1 for r in losers if _in_horizon(r, eid, h))
        w_rate = (float(w_hit) / float(len(winners))) if winners else None
        l_rate = (float(l_hit) / float(len(losers))) if losers else None
        drows = _delta_rows(rows, eid, h)
        rob = _robust_delta(drows, h)
        p2 = [r for r in rows if r.get(f"class_{h}") == "P2_PROFIT_TO_LOSS"]
        lead = lag = none = 0
        leads: list[float] = []
        for r in p2:
            ev = _event(r, eid)
            be = r.get("BE_LOSS_TIME")
            if not ev or not _in_horizon(r, eid, h) or not _finite((ev or {}).get("event_t")):
                none += 1
                continue
            if not _finite(be):
                none += 1
                continue
            dt = float(be) - float(ev["event_t"])
            if float(ev["event_t"]) + 1e-12 < float(be):
                lead += 1
                leads.append(dt)
            else:
                lag += 1
        p1 = [r for r in rows if r.get(f"class_{h}") == "P1_NEVER_POSITIVE"]
        p1_hit = [r for r in p1 if _in_horizon(r, eid, h)]
        p1_tte = []
        for r in p1_hit:
            ev = _event(r, eid) or {}
            if _finite(ev.get("event_t")) and _finite(r.get("fill_t")):
                p1_tte.append(float(ev["event_t"]) - float(r["fill_t"]))
        p1_delta = _delta_rows(p1, eid, h)
        win_harm_rows = _delta_rows(winners, eid, h)
        harm = [r.get("delta") for r in win_harm_rows]
        loser_gt_winner = l_rate is not None and w_rate is not None and float(l_rate) > float(w_rate)
        supported = bool(
            len(hit) >= int(MECH_MIN_EVENT_N)
            and len(days) >= int(MECH_MIN_DISTINCT_DAYS)
            and loser_gt_winner
            and _gt0(rob.get("mean"))
            and _ge0(rob.get("median"))
            and bool(rob.get("MULTI_DAY_POSITIVE"))
            and bool(rob.get("EX_BEST_OK"))
            and bool(rob.get("DROP_TOP_OK"))
            and bool(integrity_ok)
        )
        out[h] = {
            "EVENT_N": len(hit),
            "DISTINCT_DAY_N": len(days),
            "TIME_TO_EVENT_MEAN": _mean(tte),
            "TIME_TO_EVENT_MEDIAN": _median(tte),
            "TIME_TO_EVENT_P25": _pctl(tte, 25.0) if tte else None,
            "TIME_TO_EVENT_P75": _pctl(tte, 75.0) if tte else None,
            "EVENT_RATE_WINNER": w_rate,
            "EVENT_RATE_LOSER": l_rate,
            "DELTA": rob,
            "LEAD_N": lead,
            "LAG_N": lag,
            "NO_EVENT_N": none,
            "MEDIAN_LEAD_SEC": _median(leads),
            "P1_EVENT_N": len(p1_hit),
            "P1_EVENT_RATE": (float(len(p1_hit)) / float(len(p1))) if p1 else None,
            "P1_TIME_TO_EVENT_MEDIAN": _median(p1_tte),
            "P1_LOSS_REDUCTION_MEAN": _mean([r.get("delta") for r in p1_delta]),
            "WINNER_HARM_N": len(win_harm_rows),
            "WINNER_HARM_MEAN": _mean(harm),
            "WINNER_HARM_MEDIAN": _median(harm),
            "MECHANISM_SUPPORTED": supported,
        }
    out["CROSS_HORIZON_SUPPORTED"] = bool(out[180]["MECHANISM_SUPPORTED"] and out[300]["MECHANISM_SUPPORTED"])
    out["HORIZON_SPECIFIC_SIGNAL"] = bool(out[180]["MECHANISM_SUPPORTED"] ^ out[300]["MECHANISM_SUPPORTED"])
    return out


def decision_case(
    *,
    identity_ok: bool,
    integrity_ok: bool,
    tax: dict[str, Any],
    packs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not identity_ok or not integrity_ok:
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_V14_EXIT_RCA_INVALID",
            "NEXT": "STOP. Identity/causal/quote integrity failed. Do not adopt an EXIT rule.",
            "EXIT_MECHANISM_SUPPORTED_LIST": [],
        }
    supported = [eid for eid, p in packs.items() if p.get("CROSS_HORIZON_SUPPORTED")]
    prim = str(tax.get("PRIMARY_EXIT_PROBLEM") or "")
    if prim == "PROFIT_GIVEBACK" and supported:
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V14_PROFIT_GIVEBACK_MECHANISM_FOUND",
            "NEXT": "STOP. Mechanism is diagnostic only. EXIT_SPEC_FROZEN=false. Do not adopt an EXIT rule in this run.",
            "EXIT_MECHANISM_SUPPORTED_LIST": supported,
        }
    if prim == "EARLY_FAILURE" and supported:
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V14_EARLY_FAILURE_MECHANISM_FOUND",
            "NEXT": "STOP. Mechanism is diagnostic only. EXIT_SPEC_FROZEN=false. Do not adopt an EXIT rule in this run.",
            "EXIT_MECHANISM_SUPPORTED_LIST": supported,
        }
    if prim in {"PROFIT_GIVEBACK", "EARLY_FAILURE"} and not supported:
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_V14_EXIT_PROBLEM_FOUND_MECHANISM_UNRESOLVED",
            "NEXT": "STOP. Failure mode is clear but D1-D3 are not cross-horizon supported. Do not rule-ize.",
            "EXIT_MECHANISM_SUPPORTED_LIST": [],
        }
    return {
        "CASE": "D",
        "VERDICT": "SIMPLE_TECH_V14_EXIT_PATH_MIXED",
        "NEXT": "STOP. 180/300 failure modes differ or are mixed. Do not adopt an EXIT rule.",
        "EXIT_MECHANISM_SUPPORTED_LIST": supported,
    }


def event_summary_row(pack: dict[str, Any], h: int) -> dict[str, Any]:
    block = dict(pack.get(h) or {})
    delta = dict(block.get("DELTA") or {})
    return {
        "EVENT_ID": pack.get("EVENT_ID"),
        "H": h,
        "EVENT_N": block.get("EVENT_N"),
        "DISTINCT_DAY_N": block.get("DISTINCT_DAY_N"),
        "WINNER_EVENT_RATE": block.get("EVENT_RATE_WINNER"),
        "LOSER_EVENT_RATE": block.get("EVENT_RATE_LOSER"),
        "DELTA_MEAN": delta.get("mean"),
        "DELTA_MEDIAN": delta.get("median"),
        "DELTA_N": delta.get("N"),
        "POS_PAIRED_DAY": delta.get("POSITIVE_PAIRED_DAY_N"),
        "NEG_PAIRED_DAY": delta.get("NEGATIVE_PAIRED_DAY_N"),
        "EX_BEST": delta.get("EX_BEST"),
        "DROP_TOP_SYMBOL": delta.get("DROP_TOP_SYMBOL"),
        "LEAD_N": block.get("LEAD_N"),
        "LAG_N": block.get("LAG_N"),
        "NO_EVENT_N": block.get("NO_EVENT_N"),
        "MEDIAN_LEAD_SEC": block.get("MEDIAN_LEAD_SEC"),
        "WINNER_HARM_N": block.get("WINNER_HARM_N"),
        "WINNER_HARM_MEAN": block.get("WINNER_HARM_MEAN"),
        "WINNER_HARM_MEDIAN": block.get("WINNER_HARM_MEDIAN"),
        "MECHANISM_SUPPORTED": block.get("MECHANISM_SUPPORTED"),
    }


def required_event_block(pack: dict[str, Any]) -> dict[str, Any]:
    a = pack.get(180) or {}
    b = pack.get(300) or {}
    da = dict(a.get("DELTA") or {})
    db = dict(b.get("DELTA") or {})
    return {
        "EVENT_N_180": a.get("EVENT_N"),
        "EVENT_N_300": b.get("EVENT_N"),
        "DISTINCT_DAY_N_180": a.get("DISTINCT_DAY_N"),
        "DISTINCT_DAY_N_300": b.get("DISTINCT_DAY_N"),
        "WINNER_EVENT_RATE_180": a.get("EVENT_RATE_WINNER"),
        "LOSER_EVENT_RATE_180": a.get("EVENT_RATE_LOSER"),
        "WINNER_EVENT_RATE_300": b.get("EVENT_RATE_WINNER"),
        "LOSER_EVENT_RATE_300": b.get("EVENT_RATE_LOSER"),
        "DELTA_VS_HOLD180_MEAN": da.get("mean"),
        "DELTA_VS_HOLD180_MEDIAN": da.get("median"),
        "DELTA_VS_HOLD300_MEAN": db.get("mean"),
        "DELTA_VS_HOLD300_MEDIAN": db.get("median"),
        "LEAD_BEFORE_BE_LOSS_180": a.get("LEAD_N"),
        "LEAD_BEFORE_BE_LOSS_300": b.get("LEAD_N"),
        "WINNER_HARM_MEAN_180": a.get("WINNER_HARM_MEAN"),
        "WINNER_HARM_MEAN_300": b.get("WINNER_HARM_MEAN"),
        "MECHANISM_SUPPORTED_180": a.get("MECHANISM_SUPPORTED"),
        "MECHANISM_SUPPORTED_300": b.get("MECHANISM_SUPPORTED"),
        "CROSS_HORIZON_SUPPORTED": pack.get("CROSS_HORIZON_SUPPORTED"),
    }


def entry_stack_ok(v13_req: dict[str, Any]) -> bool:
    return bool(v13_req.get("ENTRY_SIGNAL_SPEC_FROZEN_DEVELOPMENT")) and bool(
        v13_req.get("ENTRY_EXECUTION_SPEC_FROZEN_DEVELOPMENT")
    ) and str(v13_req.get("DEVELOPMENT_ENTRY_STACK") or "") == DEVELOPMENT_ENTRY_STACK
