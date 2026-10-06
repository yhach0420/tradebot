"""V15 G1 incidence, counterfactual event-exit vs HOLD, support gates. No EXIT rule. No threshold search."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.simple_tech_entry_family.v3_analyze import concentration
from research.simple_tech_entry_family.v4_analyze import _mean, _median
from research.simple_tech_entry_family.v6_pullback_analyze import drop_symbol_mean, symbol_pack
from research.simple_tech_entry_family.v9_analyze import drop_top3_mean
from research.simple_tech_exit_family.v14_analyze import (
    _finite,
    _ge0,
    _gt0,
    _pctl,
    classify_horizon,
    entry_stack_ok,
    hold_benchmark,
    taxonomy,
)
from research.simple_tech_exit_family.v15_spec import (
    MECH_MIN_DISTINCT_DAYS,
    MECH_MIN_EVENT_N,
    MECHANISM_ID,
    P2_180_EXPECTED,
    P2_300_EXPECTED,
    P2_CAPTURE_MIN,
)


def _armed(row: dict[str, Any], h: int) -> bool:
    if not _finite(row.get("G1_ARM_T")) or not _finite(row.get("fill_t")):
        return False
    return float(row["G1_ARM_T"]) <= float(row["fill_t"]) + float(h) + 1e-12


def _g1(row: dict[str, Any], h: int) -> bool:
    if not _finite(row.get("G1_EVENT_T")) or not _finite(row.get("fill_t")):
        return False
    return float(row["G1_EVENT_T"]) <= float(row["fill_t"]) + float(h) + 1e-12


def _winner(row: dict[str, Any], h: int) -> bool:
    v = row.get(f"hold_{h}")
    return _finite(v) and float(v) > 1e-12


def _loser(row: dict[str, Any], h: int) -> bool:
    v = row.get(f"hold_{h}")
    return _finite(v) and float(v) < -1e-12


def _delta_rows(rows: list[dict[str, Any]], h: int) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not _g1(r, h):
            continue
        if not _finite(r.get("G1_EXIT_PNL")) or not _finite(r.get(f"hold_{h}")):
            continue
        rec = dict(r)
        rec["delta"] = float(r["G1_EXIT_PNL"]) - float(r[f"hold_{h}"])
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
        "EX_TOP3": conc.get("EX_TOP3_DAY_MARKOUT"),
        "DROP_TOP_SYMBOL": drop,
        "DROP_TOP3_SYMBOL": drop_top3_mean(drows, key) if drows else None,
        "EX_BEST_OK": _gt0(conc.get("EX_BEST_DAY_MARKOUT")),
        "DROP_TOP_OK": _gt0(drop),
    }


def _timing(rows: list[dict[str, Any]], h: int) -> dict[str, Any]:
    armed = [r for r in rows if _armed(r, h)]
    hit = [r for r in rows if _g1(r, h)]
    first_pos = []
    arm_to_event = []
    fill_to_event = []
    for r in armed:
        if _finite(r.get("G1_ARM_T")) and _finite(r.get("fill_t")):
            first_pos.append(float(r["G1_ARM_T"]) - float(r["fill_t"]))
    for r in hit:
        if _finite(r.get("G1_EVENT_T")) and _finite(r.get("fill_t")):
            fill_to_event.append(float(r["G1_EVENT_T"]) - float(r["fill_t"]))
        if _finite(r.get("G1_EVENT_T")) and _finite(r.get("G1_ARM_T")):
            arm_to_event.append(float(r["G1_EVENT_T"]) - float(r["G1_ARM_T"]))

    def _pack(xs: list[float]) -> dict[str, Any]:
        return {
            "mean": _mean(xs),
            "median": _median(xs),
            "p25": _pctl(xs, 25.0) if xs else None,
            "p75": _pctl(xs, 75.0) if xs else None,
            "N": len(xs),
        }

    return {
        "TIME_FIRST_POSITIVE": _pack(first_pos),
        "TIME_ARM_TO_EVENT": _pack(arm_to_event),
        "TIME_FILL_TO_EVENT": _pack(fill_to_event),
    }


def _recovery(rows: list[dict[str, Any]], h: int) -> dict[str, Any]:
    hit = [r for r in rows if _g1(r, h)]
    key = f"MAX_AFTER_{h}"
    xs = [r.get(key) for r in hit if _finite(r.get(key))]
    winners = [r for r in hit if _winner(r, h)]
    wx = [r.get(key) for r in winners if _finite(r.get(key))]
    return {
        "N": len(xs),
        "MISSING_N": len(hit) - len(xs),
        "mean": _mean(xs),
        "median": _median(xs),
        "p25": _pctl(xs, 25.0) if xs else None,
        "p75": _pctl(xs, 75.0) if xs else None,
        "WINNER_N": len(wx),
        "WINNER_MEAN": _mean(wx),
        "WINNER_MEDIAN": _median(wx),
    }


def _class_rate(rows: list[dict[str, Any]], h: int, label: str) -> dict[str, Any]:
    grp = [r for r in rows if r.get(f"class_{h}") == label]
    n = len(grp)
    hit = sum(1 for r in grp if _g1(r, h))
    return {
        "N": n,
        "EVENT_N": hit,
        "EVENT_RATE": (float(hit) / float(n)) if n else None,
        "NO_EVENT_N": n - hit,
    }


def winner_harm_ok(harm_mean: Any, overall_mean: Any, winner_n: int) -> bool:
    if int(winner_n) <= 0 or harm_mean is None:
        return True
    if float(harm_mean) >= -1e-12:
        return True
    if overall_mean is None or float(overall_mean) <= 1e-12:
        return False
    return float(harm_mean) + float(overall_mean) > 1e-12


def g1_pack(rows: list[dict[str, Any]], *, integrity_ok: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"MECHANISM_ID": MECHANISM_ID}
    for h in (180, 300):
        armed = [r for r in rows if _armed(r, h)]
        hit = [r for r in rows if _g1(r, h)]
        days = {str(r.get("date") or "") for r in hit}
        p1 = _class_rate(rows, h, "P1_NEVER_POSITIVE")
        p2 = _class_rate(rows, h, "P2_PROFIT_TO_LOSS")
        p3 = _class_rate(rows, h, "P3_LOSS_TO_RECOVERY")
        p4 = _class_rate(rows, h, "P4_FINAL_POSITIVE")
        winners = [r for r in rows if _winner(r, h)]
        losers = [r for r in rows if _loser(r, h)]
        w_hit = [r for r in winners if _g1(r, h)]
        l_hit = [r for r in losers if _g1(r, h)]
        drows = _delta_rows(rows, h)
        rob = _robust_delta(drows, h)
        win_harm_rows = _delta_rows(winners, h)
        harm = [r.get("delta") for r in win_harm_rows]
        harm_conc = concentration(win_harm_rows, f"markout_{h}") if win_harm_rows else {}
        recov = _recovery(rows, h)
        capture = p2.get("EVENT_RATE")
        wh_ok = winner_harm_ok( _mean(harm), rob.get("mean"), len(win_harm_rows))
        supported = bool(
            len(hit) >= int(MECH_MIN_EVENT_N)
            and len(days) >= int(MECH_MIN_DISTINCT_DAYS)
            and capture is not None
            and float(capture) >= float(P2_CAPTURE_MIN) - 1e-12
            and _gt0(rob.get("mean"))
            and _ge0(rob.get("median"))
            and bool(rob.get("MULTI_DAY_POSITIVE"))
            and bool(rob.get("EX_BEST_OK"))
            and bool(rob.get("DROP_TOP_OK"))
            and bool(wh_ok)
            and bool(integrity_ok)
        )
        out[h] = {
            "ARMED_N": len(armed),
            "EVENT_N": len(hit),
            "DISTINCT_DAY_N": len(days),
            "EVENT_RATE": (float(len(hit)) / float(len(rows))) if rows else None,
            "EVENT_RATE_ARMED": (float(len(hit)) / float(len(armed))) if armed else None,
            "P1": p1,
            "P2": p2,
            "P3": p3,
            "P4": p4,
            "FINAL_WINNER": {
                "N": len(winners),
                "EVENT_N": len(w_hit),
                "EVENT_RATE": (float(len(w_hit)) / float(len(winners))) if winners else None,
            },
            "FINAL_LOSER": {
                "N": len(losers),
                "EVENT_N": len(l_hit),
                "EVENT_RATE": (float(len(l_hit)) / float(len(losers))) if losers else None,
            },
            "P2_CAPTURE_N": p2.get("EVENT_N"),
            "P2_CAPTURE_RATE": capture,
            "P3_G1_EVENT_N": p3.get("EVENT_N"),
            "P3_NO_EVENT_N": p3.get("NO_EVENT_N"),
            "TIMING": _timing(rows, h),
            "DELTA": rob,
            "WINNER_EVENT_N": len(win_harm_rows),
            "WINNER_HARM_MEAN": _mean(harm),
            "WINNER_HARM_MEDIAN": _median(harm),
            "WINNER_HARM_EX_BEST": harm_conc.get("EX_BEST_DAY_MARKOUT"),
            "WINNER_HARM_OK": bool(wh_ok),
            "POST_EVENT_RECOVERY": recov,
            "MECHANISM_SUPPORTED": supported,
        }
    out["G1_SUPPORTED_180"] = bool(out[180]["MECHANISM_SUPPORTED"])
    out["G1_SUPPORTED_300"] = bool(out[300]["MECHANISM_SUPPORTED"])
    out["G1_CROSS_HORIZON_SUPPORTED"] = bool(out[180]["MECHANISM_SUPPORTED"] and out[300]["MECHANISM_SUPPORTED"])
    out["HORIZON_SPECIFIC"] = bool(out[180]["MECHANISM_SUPPORTED"] ^ out[300]["MECHANISM_SUPPORTED"])
    return out


def _large_winner_harm(pack: dict[str, Any]) -> bool:
    for h in (180, 300):
        block = pack.get(h) or {}
        recov = dict(block.get("POST_EVENT_RECOVERY") or {})
        if (
            int(block.get("WINNER_EVENT_N") or 0) >= 1
            and block.get("WINNER_HARM_MEAN") is not None
            and float(block["WINNER_HARM_MEAN"]) < -1e-12
            and recov.get("WINNER_MEDIAN") is not None
            and float(recov["WINNER_MEDIAN"]) > 1e-12
        ):
            return True
    return False


def decision_case(
    *,
    identity_ok: bool,
    integrity_ok: bool,
    pack: dict[str, Any],
) -> dict[str, Any]:
    if not identity_ok or not integrity_ok:
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_V15_INVALID",
            "NEXT": "STOP. Identity/causal/quote integrity failed. Do not freeze G1.",
        }
    if pack.get("G1_CROSS_HORIZON_SUPPORTED"):
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V15_BE_RELOSS_PROFIT_PROTECTION_SUPPORTED",
            "NEXT": "G1 freeze-candidate as profit-protection component only. EXIT_SPEC_FROZEN=false. Study NO-G1 positions in a separate run. Not a full EXIT policy.",
        }
    if pack.get("HORIZON_SPECIFIC"):
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V15_BE_RELOSS_HORIZON_SPECIFIC",
            "NEXT": "STOP. G1 is horizon-specific. Do not freeze. EXIT_SPEC_FROZEN=false.",
        }
    if _large_winner_harm(pack):
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_V15_BE_RELOSS_WINNER_HARM",
            "NEXT": "STOP. G1 winner harm / post-event recovery destruction. Do not freeze G1. EXIT_SPEC_FROZEN=false.",
        }
    return {
        "CASE": "D",
        "VERDICT": "SIMPLE_TECH_V15_BE_RELOSS_NOT_SUPPORTED",
        "NEXT": "STOP. G1 is not robustly supported. Do not freeze. EXIT_SPEC_FROZEN=false.",
    }


def v14_taxonomy_ok(tax: dict[str, Any]) -> bool:
    a = tax.get(180) or {}
    b = tax.get(300) or {}
    return int(a.get("P2_PROFIT_TO_LOSS") or -1) == int(P2_180_EXPECTED) and int(
        b.get("P2_PROFIT_TO_LOSS") or -1
    ) == int(P2_300_EXPECTED)


def be_loss_parity_n(v15_rows: list[dict[str, Any]], v14_by_key: dict[tuple[Any, ...], dict[str, Any]]) -> int:
    n = 0
    for r in v15_rows:
        key = (str(r.get("date") or ""), str(r.get("symbol") or ""), float(r.get("fill_t") or 0.0))
        old = v14_by_key.get(key)
        if not old:
            n += 1
            continue
        a = r.get("G1_EVENT_T")
        b = old.get("BE_LOSS_TIME")
        if (a is None) != (b is None):
            n += 1
            continue
        if a is None:
            continue
        if abs(float(a) - float(b)) > 1e-6:
            n += 1
    return n


def required_g1_block(pack: dict[str, Any]) -> dict[str, Any]:
    a = pack.get(180) or {}
    b = pack.get(300) or {}
    da = dict(a.get("DELTA") or {})
    db = dict(b.get("DELTA") or {})
    ra = dict(a.get("POST_EVENT_RECOVERY") or {})
    rb = dict(b.get("POST_EVENT_RECOVERY") or {})
    return {
        "ARMED_N_180": a.get("ARMED_N"),
        "ARMED_N_300": b.get("ARMED_N"),
        "G1_EVENT_N_180": a.get("EVENT_N"),
        "G1_EVENT_N_300": b.get("EVENT_N"),
        "DISTINCT_DAY_N_180": a.get("DISTINCT_DAY_N"),
        "DISTINCT_DAY_N_300": b.get("DISTINCT_DAY_N"),
        "P2_CAPTURE_RATE_180": a.get("P2_CAPTURE_RATE"),
        "P2_CAPTURE_RATE_300": b.get("P2_CAPTURE_RATE"),
        "DELTA_VS_HOLD180": {
            "N": da.get("N"),
            "mean": da.get("mean"),
            "median": da.get("median"),
            "POSITIVE_PAIRED_DAY_N": da.get("POSITIVE_PAIRED_DAY_N"),
            "NEGATIVE_PAIRED_DAY_N": da.get("NEGATIVE_PAIRED_DAY_N"),
            "EX_BEST": da.get("EX_BEST"),
            "EX_TOP3": da.get("EX_TOP3"),
            "DROP_TOP_SYMBOL": da.get("DROP_TOP_SYMBOL"),
            "DROP_TOP3_SYMBOL": da.get("DROP_TOP3_SYMBOL"),
        },
        "DELTA_VS_HOLD300": {
            "N": db.get("N"),
            "mean": db.get("mean"),
            "median": db.get("median"),
            "POSITIVE_PAIRED_DAY_N": db.get("POSITIVE_PAIRED_DAY_N"),
            "NEGATIVE_PAIRED_DAY_N": db.get("NEGATIVE_PAIRED_DAY_N"),
            "EX_BEST": db.get("EX_BEST"),
            "EX_TOP3": db.get("EX_TOP3"),
            "DROP_TOP_SYMBOL": db.get("DROP_TOP_SYMBOL"),
            "DROP_TOP3_SYMBOL": db.get("DROP_TOP3_SYMBOL"),
        },
        "P3_G1_EVENT_N_180": a.get("P3_G1_EVENT_N"),
        "P3_NO_EVENT_N_180": a.get("P3_NO_EVENT_N"),
        "P3_G1_EVENT_N_300": b.get("P3_G1_EVENT_N"),
        "P3_NO_EVENT_N_300": b.get("P3_NO_EVENT_N"),
        "WINNER_HARM_180": {
            "WINNER_EVENT_N": a.get("WINNER_EVENT_N"),
            "WINNER_HARM_MEAN": a.get("WINNER_HARM_MEAN"),
            "WINNER_HARM_MEDIAN": a.get("WINNER_HARM_MEDIAN"),
            "WINNER_HARM_EX_BEST": a.get("WINNER_HARM_EX_BEST"),
            "WINNER_HARM_OK": a.get("WINNER_HARM_OK"),
        },
        "WINNER_HARM_300": {
            "WINNER_EVENT_N": b.get("WINNER_EVENT_N"),
            "WINNER_HARM_MEAN": b.get("WINNER_HARM_MEAN"),
            "WINNER_HARM_MEDIAN": b.get("WINNER_HARM_MEDIAN"),
            "WINNER_HARM_EX_BEST": b.get("WINNER_HARM_EX_BEST"),
            "WINNER_HARM_OK": b.get("WINNER_HARM_OK"),
        },
        "POST_EVENT_RECOVERY_180": ra,
        "POST_EVENT_RECOVERY_300": rb,
        "G1_SUPPORTED_180": pack.get("G1_SUPPORTED_180"),
        "G1_SUPPORTED_300": pack.get("G1_SUPPORTED_300"),
        "G1_CROSS_HORIZON_SUPPORTED": pack.get("G1_CROSS_HORIZON_SUPPORTED"),
        "TIMING_180": a.get("TIMING"),
        "TIMING_300": b.get("TIMING"),
        "CLASS_RATES_180": {
            "P1": a.get("P1"),
            "P2": a.get("P2"),
            "P3": a.get("P3"),
            "P4": a.get("P4"),
            "FINAL_WINNER": a.get("FINAL_WINNER"),
            "FINAL_LOSER": a.get("FINAL_LOSER"),
        },
        "CLASS_RATES_300": {
            "P1": b.get("P1"),
            "P2": b.get("P2"),
            "P3": b.get("P3"),
            "P4": b.get("P4"),
            "FINAL_WINNER": b.get("FINAL_WINNER"),
            "FINAL_LOSER": b.get("FINAL_LOSER"),
        },
    }


def g1_summary_row(pack: dict[str, Any], h: int) -> dict[str, Any]:
    block = dict(pack.get(h) or {})
    delta = dict(block.get("DELTA") or {})
    recov = dict(block.get("POST_EVENT_RECOVERY") or {})
    return {
        "MECHANISM_ID": pack.get("MECHANISM_ID"),
        "H": h,
        "ARMED_N": block.get("ARMED_N"),
        "EVENT_N": block.get("EVENT_N"),
        "DISTINCT_DAY_N": block.get("DISTINCT_DAY_N"),
        "P2_CAPTURE_RATE": block.get("P2_CAPTURE_RATE"),
        "P3_G1_EVENT_N": block.get("P3_G1_EVENT_N"),
        "P3_NO_EVENT_N": block.get("P3_NO_EVENT_N"),
        "DELTA_MEAN": delta.get("mean"),
        "DELTA_MEDIAN": delta.get("median"),
        "DELTA_N": delta.get("N"),
        "POS_PAIRED_DAY": delta.get("POSITIVE_PAIRED_DAY_N"),
        "NEG_PAIRED_DAY": delta.get("NEGATIVE_PAIRED_DAY_N"),
        "EX_BEST": delta.get("EX_BEST"),
        "EX_TOP3": delta.get("EX_TOP3"),
        "DROP_TOP_SYMBOL": delta.get("DROP_TOP_SYMBOL"),
        "DROP_TOP3_SYMBOL": delta.get("DROP_TOP3_SYMBOL"),
        "WINNER_EVENT_N": block.get("WINNER_EVENT_N"),
        "WINNER_HARM_MEAN": block.get("WINNER_HARM_MEAN"),
        "WINNER_HARM_MEDIAN": block.get("WINNER_HARM_MEDIAN"),
        "WINNER_HARM_EX_BEST": block.get("WINNER_HARM_EX_BEST"),
        "RECOVERY_MEDIAN": recov.get("median"),
        "RECOVERY_WINNER_MEDIAN": recov.get("WINNER_MEDIAN"),
        "WINNER_HARM_OK": block.get("WINNER_HARM_OK"),
        "MECHANISM_SUPPORTED": block.get("MECHANISM_SUPPORTED"),
    }


__all__ = [
    "be_loss_parity_n",
    "classify_horizon",
    "decision_case",
    "entry_stack_ok",
    "g1_pack",
    "g1_summary_row",
    "hold_benchmark",
    "required_g1_block",
    "taxonomy",
    "v14_taxonomy_ok",
]
