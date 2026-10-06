"""V22 coverage funnel, path robustness, frozen-archetype miss structure. No horizon cherry-pick. No 180 EXIT PnL."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_entry_family.v3_analyze import _mean, _median, concentration, day_sign_counts
from research.simple_tech_entry_family.v6_pullback_analyze import symbol_pack
from research.simple_tech_entry_family.v13_analyze import identity_keys, set_hash
from research.simple_tech_redesign.v22_spec import (
    COHORT_MIN_N,
    PATH_COVERAGE_MIN_FRAC,
    PATH_HORIZONS_SEC,
    PATH_KINDS,
    SHAPE_HORIZON_MAJORITY,
    SHAPE_KIND_MAJORITY,
    UNEVAL_CLASSES,
    V7_SHAPE_HORIZON_MAJORITY,
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _gt0(v: Any) -> bool:
    return v is not None and float(v) > 1e-12


def _canon_num(v: Any) -> Any:
    if isinstance(v, bool):
        return bool(v)
    if isinstance(v, int) and not isinstance(v, bool):
        return int(v)
    if _finite(v):
        return float(v)
    if v is None:
        return None
    return str(v)


def fill_tuples_e4(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    out = []
    for r in rows:
        e4 = dict(r.get("e4") or {})
        if not e4.get("filled"):
            continue
        d, s, t0 = identity_keys(r)
        out.append(
            (
                d,
                s,
                t0,
                _canon_num(e4.get("fill_t")),
                _canon_num(e4.get("fill_price")),
                _canon_num(e4.get("limit_price")),
                bool(e4.get("collapsed_to_bid")),
            )
        )
    return sorted(out)


def path_key(kind: str, h: int) -> str:
    return f"{kind}_{int(h)}"


def coverage_frac(rows: list[dict[str, Any]], kind: str) -> float:
    if not rows:
        return 0.0
    n_ok = 0
    need = max(1, SHAPE_HORIZON_MAJORITY)
    for r in rows:
        got = sum(1 for h in PATH_HORIZONS_SEC if _finite(r.get(path_key(kind, int(h)))))
        if got >= need:
            n_ok += 1
    return float(n_ok) / float(len(rows))


def metric_pack(rows: list[dict[str, Any]], field: str, days: list[str]) -> dict[str, Any]:
    xs = [r.get(field) for r in rows]
    by: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if _finite(r.get(field)):
            by[str(r.get("date") or "")].append(float(r[field]))
    daily = []
    for d in days:
        vs = by.get(d) or []
        daily.append({"date": d, "N": len(vs), "MEAN": float(np.mean(vs)) if vs else None})
    signs = day_sign_counts(
        [{"date": x["date"], "SIGNAL_N": x["N"], "MARKOUT_MEAN": x["MEAN"]} for x in daily],
        "MARKOUT_MEAN",
    )
    # day_sign_counts uses SIGNAL_N truthy; empty days skipped. Rebuild with our MEAN key:
    nums = [x["MEAN"] for x in daily if x["N"]]
    pos = sum(1 for v in nums if v is not None and float(v) > 1e-12)
    neg = sum(1 for v in nums if v is not None and float(v) < -1e-12)
    zero = len(nums) - pos - neg
    conc = concentration(rows, field)
    syms = symbol_pack(rows, field)
    return {
        "N": len(rows),
        "USABLE_N": sum(1 for v in xs if _finite(v)),
        "MEAN": _mean(xs),
        "MEDIAN": _median(xs),
        "POS_RATE": (
            float(sum(1 for v in xs if _finite(v) and float(v) > 0.0) / sum(1 for v in xs if _finite(v)))
            if any(_finite(v) for v in xs)
            else None
        ),
        "DAY_N": sum(1 for x in daily if x["N"]),
        "POSITIVE_DAY_N": pos,
        "NEGATIVE_DAY_N": neg,
        "ZERO_DAY_N": zero,
        "EX_BEST_DAY": conc.get("EX_BEST_DAY_MARKOUT"),
        "EX_TOP3_DAY": conc.get("EX_TOP3_DAY_MARKOUT"),
        "BEST_DAY": conc.get("BEST_DAY"),
        "DROP_TOP_SYMBOL": syms.get("DROP_TOP_SYMBOL_MARKOUT"),
        "TOP_SYMBOL": syms.get("TOP_SYMBOL"),
        "signs_unused": signs,
    }


def path_shape(rows: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    kinds: dict[str, Any] = {}
    votes = 0
    robust_votes = 0
    for kind in PATH_KINDS:
        horiz: dict[str, Any] = {}
        pos_h = 0
        day_pos_h = 0
        ex_pos_h = 0
        for h in PATH_HORIZONS_SEC:
            hid = int(h)
            pack = metric_pack(rows, path_key(kind, hid), days)
            mfe = metric_pack(rows, f"mfe_{kind}_{hid}", days)
            mae = metric_pack(rows, f"mae_{kind}_{hid}", days)
            horiz[str(hid)] = {**pack, "MFE_MEAN": mfe.get("MEAN"), "MFE_MEDIAN": mfe.get("MEDIAN"), "MAE_MEAN": mae.get("MEAN"), "MAE_MEDIAN": mae.get("MEDIAN")}
            if _gt0(pack.get("MEAN")):
                pos_h += 1
            if int(pack.get("POSITIVE_DAY_N") or 0) > int(pack.get("NEGATIVE_DAY_N") or 0):
                day_pos_h += 1
            if _gt0(pack.get("EX_BEST_DAY")):
                ex_pos_h += 1
        kind_pos = pos_h >= int(SHAPE_HORIZON_MAJORITY)
        if kind_pos:
            votes += 1
        if kind_pos and ex_pos_h >= 2:
            robust_votes += 1
        kinds[kind] = {
            "horizons": horiz,
            "POSITIVE_HORIZON_N": pos_h,
            "DAY_POS_HORIZON_N": day_pos_h,
            "EX_BEST_POS_HORIZON_N": ex_pos_h,
            "KIND_POSITIVE": kind_pos,
            "COVERAGE_FRAC": coverage_frac(rows, kind),
        }
    return {
        "KIND_VOTES": votes,
        "ROBUST_KIND_VOTES": robust_votes,
        "SHAPE_POSITIVE": votes >= int(SHAPE_KIND_MAJORITY),
        "SHAPE_ROBUST": robust_votes >= 1 and votes >= int(SHAPE_KIND_MAJORITY),
        "kinds": kinds,
    }


def cohort_pack(rows: list[dict[str, Any]], cohort: str, days: list[str]) -> dict[str, Any]:
    xs = [r for r in rows if str(r.get("cohort") or "") == cohort]
    days_present = sorted({str(r.get("date") or "") for r in xs if r.get("date")})
    shape = path_shape(xs, list(days)) if cohort in ("A", "B") else None
    cov = None
    if cohort in ("A", "B"):
        cov = min(coverage_frac(xs, k) for k in PATH_KINDS)
    return {
        "COHORT": cohort,
        "N": len(xs),
        "DAY_N": len(days_present),
        "DAYS": days_present,
        "PATH_COVERAGE_MIN": cov,
        "shape": shape,
        "board_ok_n": sum(1 for r in xs if r.get("board_ok")),
        "ask_reason": dict(Counter(str(r.get("ask_reason") or "") for r in xs)),
        "funnel_reason": dict(Counter(str(r.get("funnel_reason") or "") for r in xs)),
        "nonfill_class": dict(Counter(str((r.get("e4") or {}).get("nonfill_class") or "") for r in xs)) if cohort == "B" else {},
        "uneval_class": dict(Counter(str(r.get("uneval_class") or "") for r in xs)) if cohort == "C" else {},
        "PQ1_MEAN": _mean([r.get("PQ1") for r in xs]),
        "PQ3_MEAN": _mean([r.get("PQ3") for r in xs]),
        "TQ1_MEAN": _mean([r.get("TQ1") for r in xs]),
        "TQ2_MEAN": _mean([r.get("TQ2") for r in xs]),
    }


def funnel_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    a = sum(1 for r in rows if r.get("cohort") == "A")
    b = sum(1 for r in rows if r.get("cohort") == "B")
    c = sum(1 for r in rows if r.get("cohort") == "C")
    uneval = Counter(str(r.get("uneval_class") or "") for r in rows if r.get("cohort") == "C")
    ask = Counter(str(r.get("ask_reason") or "") for r in rows)
    funnel = Counter(str(r.get("funnel_reason") or "") for r in rows)
    nonfill = Counter(str((r.get("e4") or {}).get("nonfill_class") or "") for r in rows if r.get("cohort") == "B")
    stage = {
        "TREND_N": sum(1 for r in rows if r.get("trend")),
        "PULLBACK_N": sum(1 for r in rows if r.get("pullback")),
        "RCI_N": sum(1 for r in rows if r.get("rci")),
        "BOARD_OK_N": sum(1 for r in rows if r.get("board_ok")),
        "PA_N": sum(1 for r in rows if r.get("pa")),
        "VOLUME_N": sum(1 for r in rows if r.get("volume")),
    }
    uneval_split = {k: int(uneval.get(k) or 0) for k in UNEVAL_CLASSES}
    return {
        "SIGNAL_N": len(rows),
        "EXECUTION_EVALUABLE_N": a + b,
        "EXECUTION_UNEVALUABLE_N": c,
        "E4_FILLED_N": a,
        "E4_NONFILLED_N": b,
        "UNEVAL_SPLIT": uneval_split,
        "ASK_REASON_COUNTS": dict(ask),
        "FUNNEL_REASON_COUNTS": dict(funnel),
        "E4_NONFILL_CLASS_COUNTS": dict(nonfill),
        "ENTRY_STAGE_COUNTS": stage,
    }


def _state_vals(rows: list[dict[str, Any]], tf: str, when: str, field: str) -> list[Any]:
    out = []
    for r in rows:
        if when == "t0":
            st = ((r.get("state_t0") or {}).get(tf) or {})
        else:
            st = (((r.get("state_path") or {}).get(when) or {}).get(tf) or {})
        if not st:
            continue
        out.append(st.get(field))
    return out


def _state_rate(rows: list[dict[str, Any]], tf: str, when: str, field: str) -> Optional[float]:
    xs = _state_vals(rows, tf, when, field)
    bs = [bool(v) for v in xs if v is not None]
    if not bs and not xs:
        return None
    flags = [bool(v) for v in xs]
    if not flags:
        return None
    return float(sum(1 for v in flags if v) / len(flags))


def tf_state_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    whens = ["t0"] + [str(int(h)) for h in PATH_HORIZONS_SEC]
    for tf in ("tf1", "tf3", "tf5"):
        block: dict[str, Any] = {}
        for when in whens:
            if when != "t0" and not rows:
                continue
            block[when] = {
                "RCI9_MEAN": _mean(_state_vals(rows, tf, when, "rci9")),
                "RCI9_MEDIAN": _median(_state_vals(rows, tf, when, "rci9")),
                "PCTB_MEAN": _mean(_state_vals(rows, tf, when, "pctb")),
                "EMA_GAP_BPS_MEAN": _mean(_state_vals(rows, tf, when, "ema_gap_bps")),
                "CLOSE_EMA9_BPS_MEAN": _mean(_state_vals(rows, tf, when, "close_ema9_bps")),
                "VOL_ACCEL_MEAN": _mean(_state_vals(rows, tf, when, "vol_accel")),
                "TREND_FRAC": _state_rate(rows, tf, when, "trend"),
                "PULLBACK_FRAC": _state_rate(rows, tf, when, "pullback"),
                "VOLUME_OK_FRAC": _state_rate(rows, tf, when, "volume_ok"),
                "N": sum(1 for r in rows if ((r.get("state_t0") if when == "t0" else ((r.get("state_path") or {}).get(when))) or {}).get(tf)),
            }
        out[tf] = block
    return out


def v7_archetype(row: dict[str, Any]) -> Optional[str]:
    t = bool(row.get("trend"))
    p = bool(row.get("pullback"))
    c = bool(row.get("rci"))
    pa = bool(row.get("pa"))
    if t and p and c:
        return "B1_CURRENT_T3_PULLBACK_RCI"
    if t and c and (not p):
        return "TREND_RCI_NO_PULLBACK"
    if t and p and (not c):
        return "TREND_PULLBACK_NO_RCI"
    if (not t) and p and c:
        return "PULLBACK_RCI_NO_TREND"
    if t and pa and (not p):
        return "TREND_PA_NO_PULLBACK"
    return None


def v7_shape(rows: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    horiz = {}
    pos_h = 0
    day_pos_h = 0
    ex_pos_h = 0
    for h in (60, 180, 300):
        pack = metric_pack(rows, f"markout_{h}", days)
        horiz[str(h)] = pack
        if _gt0(pack.get("MEAN")):
            pos_h += 1
        if int(pack.get("POSITIVE_DAY_N") or 0) > int(pack.get("NEGATIVE_DAY_N") or 0):
            day_pos_h += 1
        if _gt0(pack.get("EX_BEST_DAY")):
            ex_pos_h += 1
    return {
        "N": len(rows),
        "EXECUTABLE_N": sum(1 for r in rows if r.get("executable_signal")),
        "POSITIVE_HORIZON_N": pos_h,
        "DAY_POS_HORIZON_N": day_pos_h,
        "EX_BEST_POS_HORIZON_N": ex_pos_h,
        "SHAPE_POSITIVE": pos_h >= int(V7_SHAPE_HORIZON_MAJORITY),
        "SHAPE_ROBUST": pos_h >= int(V7_SHAPE_HORIZON_MAJORITY) and ex_pos_h >= 1,
        "horizons": horiz,
        "MFE_MEAN": _mean([r.get("mfe_bps") for r in rows]),
        "MAE_MEAN": _mean([r.get("mae_bps") for r in rows]),
    }


def missed_opportunity_structure(v7_tf1: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in v7_tf1:
        aid = v7_archetype(r)
        if aid:
            by[aid].append(r)
    out: dict[str, Any] = {}
    for aid in (
        "B1_CURRENT_T3_PULLBACK_RCI",
        "TREND_RCI_NO_PULLBACK",
        "TREND_PULLBACK_NO_RCI",
        "PULLBACK_RCI_NO_TREND",
        "TREND_PA_NO_PULLBACK",
    ):
        rows = by.get(aid) or []
        exe = [r for r in rows if r.get("executable_signal")]
        pack = v7_shape(exe, days)
        pack["SIGNAL_N"] = len(rows)
        pack["ARCHETYPE"] = aid
        out[aid] = pack
    near = out["TREND_RCI_NO_PULLBACK"]
    pa = out["TREND_PA_NO_PULLBACK"]
    no_trend = out["PULLBACK_RCI_NO_TREND"]
    no_rci = out["TREND_PULLBACK_NO_RCI"]
    q3 = bool(
        int(near.get("EXECUTABLE_N") or 0) >= int(COHORT_MIN_N)
        and bool(near.get("SHAPE_POSITIVE"))
        and bool(near.get("SHAPE_ROBUST"))
    )
    q4 = False
    q4_id = None
    for cand, label in ((pa, "TREND_PA_NO_PULLBACK"), (no_trend, "PULLBACK_RCI_NO_TREND")):
        if int(cand.get("EXECUTABLE_N") or 0) >= int(COHORT_MIN_N) and bool(cand.get("SHAPE_POSITIVE")) and bool(cand.get("SHAPE_ROBUST")):
            q4 = True
            q4_id = label
            break
    rci_only = bool(
        int(no_rci.get("EXECUTABLE_N") or 0) >= int(COHORT_MIN_N)
        and bool(no_rci.get("SHAPE_POSITIVE"))
        and (not q3)
    )
    return {
        "archetypes": out,
        "Q3_NEAR_MISS_PULLBACK": q3,
        "Q4_COMPLEMENTARY_ARCHETYPE": q4,
        "Q4_ARCHETYPE_ID": q4_id,
        "RCI_FAIL_LOOKS_POSITIVE_NOT_CASE_B": rci_only,
        "MARKOUT_KIND": "V7_ASK_BID_60_180_300_DIAGNOSTIC_NOT_EXIT",
    }


def classify_deficiency(
    *,
    identity_ok: bool,
    funnel: dict[str, Any],
    pack_a: dict[str, Any],
    pack_b: dict[str, Any],
    pack_c: dict[str, Any],
    miss: dict[str, Any],
    v7_loaded: bool,
) -> dict[str, Any]:
    a_n = int(pack_a.get("N") or 0)
    b_n = int(pack_b.get("N") or 0)
    c_n = int(pack_c.get("N") or 0)
    cov_a = pack_a.get("PATH_COVERAGE_MIN")
    cov_b = pack_b.get("PATH_COVERAGE_MIN")
    shape_a = dict(pack_a.get("shape") or {})
    shape_b = dict(pack_b.get("shape") or {})
    evidence = bool(identity_ok and v7_loaded)
    if evidence:
        if a_n < 1 or b_n < 1:
            evidence = False
        if cov_a is None or cov_b is None or float(cov_a) < float(PATH_COVERAGE_MIN_FRAC) or float(cov_b) < float(PATH_COVERAGE_MIN_FRAC):
            evidence = False
    q1 = bool(
        evidence
        and b_n >= int(COHORT_MIN_N)
        and bool(shape_b.get("SHAPE_POSITIVE"))
    )
    q1_robust = bool(q1 and bool(shape_b.get("SHAPE_ROBUST")))
    q2 = False
    if (not q1) and evidence:
        # Signal count is the binding issue only when current evaluable nonfills are not the missed upside.
        q2 = not bool(shape_b.get("SHAPE_POSITIVE"))
    q3 = bool(miss.get("Q3_NEAR_MISS_PULLBACK"))
    q4 = bool(miss.get("Q4_COMPLEMENTARY_ARCHETYPE")) and (not q3)
    primary = "NO_SAFE_COVERAGE_EXPANSION_FOUND"
    nxt = "Do not loosen ENTRY just to raise trade count. STOP."
    case = "D"
    if not evidence:
        primary = "DATA_EXECUTION_EVIDENCE_LIMITED"
        nxt = "Do not guess. STOP."
        case = "E"
    elif q1:
        primary = "EXECUTION_COVERAGE_DEFICIENCY"
        nxt = "Next run: execution architecture only. Do not change ENTRY signal."
        case = "A"
    elif q3:
        primary = "CURRENT_PULLBACK_TOO_NARROW"
        nxt = "Keep current pullback family. Precommit one causal pullback relaxation only."
        case = "B"
    elif q4:
        primary = "COMPLEMENTARY_ENTRY_ARCHETYPE_NEEDED"
        nxt = (
            "Keep current pullback. Design one other simple technical ENTRY archetype: "
            f"{miss.get('Q4_ARCHETYPE_ID')}."
        )
        case = "C"
    elif miss.get("RCI_FAIL_LOOKS_POSITIVE_NOT_CASE_B"):
        primary = "NO_SAFE_COVERAGE_EXPANSION_FOUND"
        nxt = "RCI-fail path is not a licensed pullback relaxation. Do not search RCI thresholds. STOP."
        case = "D"
    return {
        "PRIMARY_ENTRY_COVERAGE_DEFICIENCY": primary,
        "CASE": case,
        "NEXT": nxt,
        "Q1_E4_NONFILL_POSITIVE_PATH": q1,
        "Q1_ROBUST": q1_robust,
        "Q2_CURRENT_SIGNAL_TOO_FEW_PRIMARY": bool(q2 and (not q1) and (not q3) and (not q4)),
        "Q3_NEAR_MISS_PULLBACK": q3,
        "Q4_COMPLEMENTARY_ARCHETYPE": q4,
        "Q4_ARCHETYPE_ID": miss.get("Q4_ARCHETYPE_ID"),
        "EVIDENCE_OK": evidence,
        "COHORT_A_N": a_n,
        "COHORT_B_N": b_n,
        "COHORT_C_N": c_n,
        "FUNNEL": {
            "SIGNAL_N": funnel.get("SIGNAL_N"),
            "EXECUTION_EVALUABLE_N": funnel.get("EXECUTION_EVALUABLE_N"),
            "EXECUTION_UNEVALUABLE_N": funnel.get("EXECUTION_UNEVALUABLE_N"),
            "E4_FILLED_N": funnel.get("E4_FILLED_N"),
            "E4_NONFILLED_N": funnel.get("E4_NONFILLED_N"),
        },
    }


def eligible_mean_parity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exe = [r for r in rows if r.get("executable_signal")]
    return {
        "EXECUTABLE_N": len(exe),
        "ASK_BID_60_MEAN": _mean([r.get("ask_bid_60") for r in exe]),
        "ASK_BID_180_MEAN": _mean([r.get("ask_bid_180") for r in exe]),
        "ASK_BID_300_MEAN": _mean([r.get("ask_bid_300") for r in exe]),
    }
