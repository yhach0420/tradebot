"""V16 G2 incidence, P2/P3 decomposition, counterfactual vs HOLD. No EXIT rule. No threshold search."""
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
    entry_stack_ok,
    hold_benchmark,
    taxonomy,
)
from research.simple_tech_exit_family.v15_analyze import v14_taxonomy_ok
from research.simple_tech_exit_family.v16_spec import (
    G1_EVENT_N_180_V15,
    G1_EVENT_N_300_V15,
    MECH_MIN_DISTINCT_DAYS,
    MECH_MIN_EVENT_N,
    MECHANISM_ID,
    P2_CAPTURE_MIN,
    P3_180_EXPECTED,
    P3_300_EXPECTED,
    P3_G1_EVENT_N_180_V15,
    P3_G1_EVENT_N_300_V15,
)


def _armed(row: dict[str, Any], h: int) -> bool:
    if not _finite(row.get("G2_ARM_T")) or not _finite(row.get("fill_t")):
        return False
    return float(row["G2_ARM_T"]) <= float(row["fill_t"]) + float(h) + 1e-12


def _g2(row: dict[str, Any], h: int) -> bool:
    if not _finite(row.get("G2_EVENT_T")) or not _finite(row.get("fill_t")):
        return False
    return float(row["G2_EVENT_T"]) <= float(row["fill_t"]) + float(h) + 1e-12


def _delta_rows(rows: list[dict[str, Any]], h: int) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not _g2(r, h):
            continue
        if r.get("G2_EVENT_MISS") or not _finite(r.get("G2_EXIT_PNL")) or not _finite(r.get(f"hold_{h}")):
            continue
        rec = dict(r)
        rec["delta"] = float(r["G2_EXIT_PNL"]) - float(r[f"hold_{h}"])
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
    finite_xs = [float(v) for v in xs if _finite(v)]
    s = float(np.sum(finite_xs)) if finite_xs else 0.0
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
        "SUM": s if drows else 0.0,
    }


def _class_rate(rows: list[dict[str, Any]], h: int, label: str) -> dict[str, Any]:
    grp = [r for r in rows if r.get(f"class_{h}") == label]
    n = len(grp)
    hit = sum(1 for r in grp if _g2(r, h))
    return {
        "N": n,
        "EVENT_N": hit,
        "EVENT_RATE": (float(hit) / float(n)) if n else None,
        "NO_EVENT_N": n - hit,
    }


def _recovery(rows: list[dict[str, Any]], h: int) -> dict[str, Any]:
    hit = [r for r in rows if _g2(r, h)]
    key = f"MAX_AFTER_{h}"
    xs = [r.get(key) for r in hit if _finite(r.get(key))]
    p2 = [r for r in hit if r.get(f"class_{h}") == "P2_PROFIT_TO_LOSS"]
    p3 = [r for r in hit if r.get(f"class_{h}") == "P3_LOSS_TO_RECOVERY"]
    p2x = [r.get(key) for r in p2 if _finite(r.get(key))]
    p3x = [r.get(key) for r in p3 if _finite(r.get(key))]
    return {
        "ALL": {"N": len(xs), "mean": _mean(xs), "median": _median(xs)},
        "P2": {"N": len(p2x), "mean": _mean(p2x), "median": _median(p2x)},
        "P3": {"N": len(p3x), "mean": _mean(p3x), "median": _median(p3x)},
    }


def _timing(rows: list[dict[str, Any]], h: int) -> dict[str, Any]:
    hit = [r for r in rows if _g2(r, h)]
    fill_to = []
    arm_to = []
    for r in hit:
        if _finite(r.get("G2_EVENT_T")) and _finite(r.get("fill_t")):
            fill_to.append(float(r["G2_EVENT_T"]) - float(r["fill_t"]))
        if _finite(r.get("G2_EVENT_T")) and _finite(r.get("G2_ARM_T")):
            arm_to.append(float(r["G2_EVENT_T"]) - float(r["G2_ARM_T"]))

    def _pack(xs: list[float]) -> dict[str, Any]:
        return {
            "mean": _mean(xs),
            "median": _median(xs),
            "p25": _pctl(xs, 25.0) if xs else None,
            "p75": _pctl(xs, 75.0) if xs else None,
            "N": len(xs),
        }

    return {"TIME_FILL_TO_EVENT": _pack(fill_to), "TIME_ARM_TO_EVENT": _pack(arm_to)}


def g2_pack(rows: list[dict[str, Any]], *, integrity_ok: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"MECHANISM_ID": MECHANISM_ID}
    for h in (180, 300):
        armed = [r for r in rows if _armed(r, h)]
        hit = [r for r in rows if _g2(r, h)]
        days = {str(r.get("date") or "") for r in hit}
        p1 = _class_rate(rows, h, "P1_NEVER_POSITIVE")
        p2 = _class_rate(rows, h, "P2_PROFIT_TO_LOSS")
        p3 = _class_rate(rows, h, "P3_LOSS_TO_RECOVERY")
        p4 = _class_rate(rows, h, "P4_FINAL_POSITIVE")
        drows = _delta_rows(rows, h)
        rob = _robust_delta(drows, h)
        p2_rows = _delta_rows([r for r in rows if r.get(f"class_{h}") == "P2_PROFIT_TO_LOSS"], h)
        p3_rows = _delta_rows([r for r in rows if r.get(f"class_{h}") == "P3_LOSS_TO_RECOVERY"], h)
        p2d = _robust_delta(p2_rows, h)
        p3d = _robust_delta(p3_rows, h)
        p2_saved = float(p2d.get("SUM") or 0.0)
        p3_destroyed = -float(p3d.get("SUM") or 0.0)
        net = p2_saved - p3_destroyed
        p2_rate = p2.get("EVENT_RATE")
        p3_rate = p3.get("EVENT_RATE")
        selective = p2_rate is not None and p3_rate is not None and float(p2_rate) > float(p3_rate) + 1e-12
        capture = p2.get("EVENT_RATE")
        supported = bool(
            len(hit) >= int(MECH_MIN_EVENT_N)
            and len(days) >= int(MECH_MIN_DISTINCT_DAYS)
            and selective
            and capture is not None
            and float(capture) >= float(P2_CAPTURE_MIN) - 1e-12
            and _gt0(rob.get("mean"))
            and _ge0(rob.get("median"))
            and bool(rob.get("MULTI_DAY_POSITIVE"))
            and bool(rob.get("EX_BEST_OK"))
            and bool(rob.get("DROP_TOP_OK"))
            and float(net) > 1e-12
            and bool(integrity_ok)
        )
        out[h] = {
            "ARMED_N": len(armed),
            "EVENT_N": len(hit),
            "DISTINCT_DAY_N": len(days),
            "EVENT_RATE": (float(len(hit)) / float(len(rows))) if rows else None,
            "P1": p1,
            "P2": p2,
            "P3": p3,
            "P4": p4,
            "P2_G2_EVENT_N": p2.get("EVENT_N"),
            "P2_EVENT_RATE": p2_rate,
            "P3_G2_EVENT_N": p3.get("EVENT_N"),
            "P3_EVENT_RATE": p3_rate,
            "P2_CAPTURE_N": p2.get("EVENT_N"),
            "P2_CAPTURE_RATE": capture,
            "P2_GT_P3_RATE": bool(selective),
            "DELTA": rob,
            "P2_DELTA": p2d,
            "P3_DELTA": p3d,
            "TOTAL_P2_SAVED_BPS": p2_saved,
            "TOTAL_P3_DESTROYED_BPS": p3_destroyed,
            "NET_G2_CONTRIBUTION_BPS": net,
            "POST_EVENT_RECOVERY": _recovery(rows, h),
            "TIMING": _timing(rows, h),
            "MECHANISM_SUPPORTED": supported,
        }
    out["G2_SUPPORTED_180"] = bool(out[180]["MECHANISM_SUPPORTED"])
    out["G2_SUPPORTED_300"] = bool(out[300]["MECHANISM_SUPPORTED"])
    out["G2_CROSS_HORIZON_SUPPORTED"] = bool(out[180]["MECHANISM_SUPPORTED"] and out[300]["MECHANISM_SUPPORTED"])
    out["HORIZON_SPECIFIC"] = bool(out[180]["MECHANISM_SUPPORTED"] ^ out[300]["MECHANISM_SUPPORTED"])
    return out


def g1_compare(pack: dict[str, Any]) -> dict[str, Any]:
    a = pack.get(180) or {}
    b = pack.get(300) or {}
    n180 = int(a.get("EVENT_N") or 0)
    n300 = int(b.get("EVENT_N") or 0)
    p3_180 = int(a.get("P3_G2_EVENT_N") or 0)
    p3_300 = int(b.get("P3_G2_EVENT_N") or 0)
    return {
        "G1_EVENT_N_180_V15": G1_EVENT_N_180_V15,
        "G1_EVENT_N_300_V15": G1_EVENT_N_300_V15,
        "G2_EVENT_N_180": n180,
        "G2_EVENT_N_300": n300,
        "G2_LT_G1_EVENT_N_180": n180 < int(G1_EVENT_N_180_V15),
        "G2_LT_G1_EVENT_N_300": n300 < int(G1_EVENT_N_300_V15),
        "P3_G1_EVENT_N_180_V15": P3_G1_EVENT_N_180_V15,
        "P3_G1_EVENT_N_300_V15": P3_G1_EVENT_N_300_V15,
        "P3_G2_EVENT_N_180": p3_180,
        "P3_G2_EVENT_N_300": p3_300,
        "P3_FALSE_EXIT_REDUCED_180": p3_180 < int(P3_G1_EVENT_N_180_V15),
        "P3_FALSE_EXIT_REDUCED_300": p3_300 < int(P3_G1_EVENT_N_300_V15),
    }


def _p2_captured_p3_destroyed(pack: dict[str, Any]) -> bool:
    for h in (180, 300):
        block = pack.get(h) or {}
        cap = block.get("P2_CAPTURE_RATE")
        p3m = (block.get("P3_DELTA") or {}).get("mean")
        p3n = int(block.get("P3_G2_EVENT_N") or 0)
        recov = (block.get("POST_EVENT_RECOVERY") or {}).get("P3") or {}
        if (
            cap is not None
            and float(cap) >= float(P2_CAPTURE_MIN) - 1e-12
            and p3n >= 1
            and p3m is not None
            and float(p3m) < -1e-12
            and (
                float(block.get("NET_G2_CONTRIBUTION_BPS") or 0.0) <= 1e-12
                or (recov.get("median") is not None and float(recov["median"]) > 1e-12)
            )
        ):
            return True
    return False


def _too_rare(pack: dict[str, Any]) -> bool:
    a = pack.get(180) or {}
    b = pack.get(300) or {}
    return int(a.get("EVENT_N") or 0) < int(MECH_MIN_EVENT_N) and int(b.get("EVENT_N") or 0) < int(MECH_MIN_EVENT_N)


def decision_case(
    *,
    identity_ok: bool,
    integrity_ok: bool,
    pack: dict[str, Any],
) -> dict[str, Any]:
    if not identity_ok or not integrity_ok:
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_V16_INVALID",
            "NEXT": "STOP. Identity/causal/quote integrity failed. Do not freeze G2.",
        }
    if pack.get("G2_CROSS_HORIZON_SUPPORTED"):
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V16_BAR_CONFIRMED_GIVEBACK_SUPPORTED",
            "NEXT": "G2 freeze-candidate as bar-confirmed giveback component only. EXIT_SPEC_FROZEN=false. Not a full EXIT policy.",
        }
    if pack.get("HORIZON_SPECIFIC"):
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V16_BAR_CONFIRMED_GIVEBACK_HORIZON_SPECIFIC",
            "NEXT": "STOP. G2 is horizon-specific. Do not freeze. EXIT_SPEC_FROZEN=false.",
        }
    if (not _too_rare(pack)) and _p2_captured_p3_destroyed(pack):
        return {
            "CASE": "C",
            "VERDICT": "SIMPLE_TECH_V16_BAR_CONFIRMED_WINNER_HARM",
            "NEXT": "STOP. G2 captures P2 but still destroys P3 recovery. Do not freeze G2. EXIT_SPEC_FROZEN=false.",
        }
    return {
        "CASE": "D",
        "VERDICT": "SIMPLE_TECH_V16_BAR_CONFIRMED_GIVEBACK_NOT_SUPPORTED",
        "NEXT": "STOP. G2 is too rare or has no robust benefit. Do not freeze. EXIT_SPEC_FROZEN=false.",
    }


def v14_p3_ok(tax: dict[str, Any]) -> bool:
    a = tax.get(180) or {}
    b = tax.get(300) or {}
    return int(a.get("P3_LOSS_TO_RECOVERY") or -1) == int(P3_180_EXPECTED) and int(
        b.get("P3_LOSS_TO_RECOVERY") or -1
    ) == int(P3_300_EXPECTED)


def arm_parity_n(v16_rows: list[dict[str, Any]], v15_by_key: dict[tuple[Any, ...], dict[str, Any]]) -> int:
    n = 0
    for r in v16_rows:
        key = (str(r.get("date") or ""), str(r.get("symbol") or ""), float(r.get("fill_t") or 0.0))
        old = v15_by_key.get(key)
        if not old:
            n += 1
            continue
        a = r.get("G2_ARM_T")
        b = old.get("G1_ARM_T")
        if (a is None) != (b is None):
            n += 1
            continue
        if a is None:
            continue
        if abs(float(a) - float(b)) > 1e-6:
            n += 1
    return n


def required_g2_block(pack: dict[str, Any], cmp: dict[str, Any]) -> dict[str, Any]:
    a = pack.get(180) or {}
    b = pack.get(300) or {}
    da = dict(a.get("DELTA") or {})
    db = dict(b.get("DELTA") or {})
    reca = dict(a.get("POST_EVENT_RECOVERY") or {})
    recb = dict(b.get("POST_EVENT_RECOVERY") or {})
    return {
        "G2_EVENT_N_180": a.get("EVENT_N"),
        "G2_EVENT_N_300": b.get("EVENT_N"),
        "DISTINCT_DAY_N_180": a.get("DISTINCT_DAY_N"),
        "DISTINCT_DAY_N_300": b.get("DISTINCT_DAY_N"),
        "P2_EVENT_RATE_180": a.get("P2_EVENT_RATE"),
        "P2_EVENT_RATE_300": b.get("P2_EVENT_RATE"),
        "P3_EVENT_RATE_180": a.get("P3_EVENT_RATE"),
        "P3_EVENT_RATE_300": b.get("P3_EVENT_RATE"),
        "P2_CAPTURE_RATE_180": a.get("P2_CAPTURE_RATE"),
        "P2_CAPTURE_RATE_300": b.get("P2_CAPTURE_RATE"),
        "P2_G2_EVENT_N_180": a.get("P2_G2_EVENT_N"),
        "P2_G2_EVENT_N_300": b.get("P2_G2_EVENT_N"),
        "P3_G2_EVENT_N_180": a.get("P3_G2_EVENT_N"),
        "P3_G2_EVENT_N_300": b.get("P3_G2_EVENT_N"),
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
        "P2_DELTA_180": a.get("P2_DELTA"),
        "P2_DELTA_300": b.get("P2_DELTA"),
        "P3_DELTA_180": a.get("P3_DELTA"),
        "P3_DELTA_300": b.get("P3_DELTA"),
        "NET_G2_CONTRIBUTION_BPS_180": a.get("NET_G2_CONTRIBUTION_BPS"),
        "NET_G2_CONTRIBUTION_BPS_300": b.get("NET_G2_CONTRIBUTION_BPS"),
        "TOTAL_P2_SAVED_BPS_180": a.get("TOTAL_P2_SAVED_BPS"),
        "TOTAL_P2_SAVED_BPS_300": b.get("TOTAL_P2_SAVED_BPS"),
        "TOTAL_P3_DESTROYED_BPS_180": a.get("TOTAL_P3_DESTROYED_BPS"),
        "TOTAL_P3_DESTROYED_BPS_300": b.get("TOTAL_P3_DESTROYED_BPS"),
        "POST_EVENT_RECOVERY_P2_180": (reca.get("P2") or {}),
        "POST_EVENT_RECOVERY_P3_180": (reca.get("P3") or {}),
        "POST_EVENT_RECOVERY_P2_300": (recb.get("P2") or {}),
        "POST_EVENT_RECOVERY_P3_300": (recb.get("P3") or {}),
        "POST_EVENT_RECOVERY_ALL_180": (reca.get("ALL") or {}),
        "POST_EVENT_RECOVERY_ALL_300": (recb.get("ALL") or {}),
        "G2_SUPPORTED_180": pack.get("G2_SUPPORTED_180"),
        "G2_SUPPORTED_300": pack.get("G2_SUPPORTED_300"),
        "G2_CROSS_HORIZON_SUPPORTED": pack.get("G2_CROSS_HORIZON_SUPPORTED"),
        "G1_COMPARE": cmp,
        "TIMING_180": a.get("TIMING"),
        "TIMING_300": b.get("TIMING"),
    }


def g2_summary_row(pack: dict[str, Any], h: int) -> dict[str, Any]:
    block = dict(pack.get(h) or {})
    delta = dict(block.get("DELTA") or {})
    rec = dict(block.get("POST_EVENT_RECOVERY") or {})
    return {
        "MECHANISM_ID": pack.get("MECHANISM_ID"),
        "H": h,
        "ARMED_N": block.get("ARMED_N"),
        "EVENT_N": block.get("EVENT_N"),
        "DISTINCT_DAY_N": block.get("DISTINCT_DAY_N"),
        "P2_EVENT_RATE": block.get("P2_EVENT_RATE"),
        "P3_EVENT_RATE": block.get("P3_EVENT_RATE"),
        "P2_CAPTURE_RATE": block.get("P2_CAPTURE_RATE"),
        "P2_G2_EVENT_N": block.get("P2_G2_EVENT_N"),
        "P3_G2_EVENT_N": block.get("P3_G2_EVENT_N"),
        "DELTA_MEAN": delta.get("mean"),
        "DELTA_MEDIAN": delta.get("median"),
        "POS_PAIRED_DAY": delta.get("POSITIVE_PAIRED_DAY_N"),
        "NEG_PAIRED_DAY": delta.get("NEGATIVE_PAIRED_DAY_N"),
        "EX_BEST": delta.get("EX_BEST"),
        "DROP_TOP_SYMBOL": delta.get("DROP_TOP_SYMBOL"),
        "P2_SAVED": block.get("TOTAL_P2_SAVED_BPS"),
        "P3_DESTROYED": block.get("TOTAL_P3_DESTROYED_BPS"),
        "NET": block.get("NET_G2_CONTRIBUTION_BPS"),
        "RECOVERY_P2_MEDIAN": (rec.get("P2") or {}).get("median"),
        "RECOVERY_P3_MEDIAN": (rec.get("P3") or {}).get("median"),
        "MECHANISM_SUPPORTED": block.get("MECHANISM_SUPPORTED"),
    }
