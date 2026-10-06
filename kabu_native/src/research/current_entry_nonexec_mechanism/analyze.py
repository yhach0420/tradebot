"""A0 parity, rank bias, A2 counterfactual, B2 post-hoc decomp. Does not rewrite B2 verdict."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

import numpy as np

from research.current_entry_nonexec_mechanism import (
    A0_DD_TOL,
    A0_PF_TOL,
    A0_PNL_TOL,
    A0_TRADE_TOL,
    B2_FORMAL,
    BIAS_ABS_GAP_MIN,
    BIAS_ENRICHMENT_MIN,
    EXPECTED_A0_MAXDD,
    EXPECTED_A0_PF,
    EXPECTED_A0_PNL,
    EXPECTED_A0_TRADES,
)
from research.edge_decay_rca.analyze import tag_entry_kind
from research.executable_target_v2_b_threshold.analyze import pack_metrics
from research.uniform10_b_followup.analyze import (
    flow_join,
    opportunity_cost_b2,
    rank_audit,
)


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


def a0_parity(pack: dict[str, Any]) -> dict[str, Any]:
    trades = int(pack.get("trades") or 0)
    pnl = float(pack.get("PnL") if pack.get("PnL") is not None else pack.get("pnl") or 0.0)
    pf = pack.get("PF")
    try:
        pf_n = float("inf") if pf in ("Infinity", float("inf")) else float(pf)
    except (TypeError, ValueError):
        pf_n = 0.0
    dd = float(pack.get("maxDD") or 0.0)
    ok = (
        abs(trades - int(EXPECTED_A0_TRADES)) <= int(A0_TRADE_TOL)
        and abs(pnl - float(EXPECTED_A0_PNL)) <= float(A0_PNL_TOL)
        and abs(pf_n - float(EXPECTED_A0_PF)) <= float(A0_PF_TOL)
        and abs(dd - float(EXPECTED_A0_MAXDD)) <= float(A0_DD_TOL)
    )
    return {
        "ok": ok,
        "observed": {"trades": trades, "PnL": pnl, "PF": pack.get("PF"), "maxDD": dd},
        "expected": {
            "trades": EXPECTED_A0_TRADES,
            "PnL": EXPECTED_A0_PNL,
            "PF": EXPECTED_A0_PF,
            "maxDD": EXPECTED_A0_MAXDD,
        },
    }


def prefix_rank(rank: dict[str, Any]) -> dict[str, Any]:
    return {
        "A_NONEXEC_ALL_RATE": rank.get("NONEXEC_ALL_RATE"),
        "A_NONEXEC_TOP1_RATE": rank.get("NONEXEC_TOP1_RATE"),
        "A_NONEXEC_TOP3_RATE": rank.get("NONEXEC_TOP3_RATE"),
        "A_NONEXEC_TOP5_RATE": rank.get("NONEXEC_TOP5_RATE"),
        "A_NONEXEC_TOP10_RATE": rank.get("NONEXEC_TOP10_RATE"),
        "A_NONEXEC_TOP1_ENRICHMENT": rank.get("NONEXEC_TOP1_ENRICHMENT"),
        "A_NONEXEC_TOP3_ENRICHMENT": rank.get("NONEXEC_TOP3_ENRICHMENT"),
        "mean_rank_executable": rank.get("mean_rank_executable"),
        "mean_rank_nonexec": rank.get("mean_rank_nonexec"),
        "mean_score_executable": (rank.get("score_executable") or {}).get("mean"),
        "mean_score_nonexec": (rank.get("score_nonexec") or {}).get("mean"),
        "A_CURRENT_ENTRY_NONEXEC_RANK_BIAS": rank.get("CURRENT_ENTRY_NONEXEC_RANK_BIAS"),
        "n_scored": rank.get("n_scored"),
        "n_executable": rank.get("n_executable"),
        "n_nonexec": rank.get("n_nonexec"),
        "nonexec_buckets": rank.get("nonexec_buckets"),
        "bias_rule": rank.get("bias_rule"),
        "NONEXEC_TOP3_ABS_GAP": rank.get("NONEXEC_TOP3_ABS_GAP"),
        "score_executable": rank.get("score_executable"),
        "score_nonexec": rank.get("score_nonexec"),
    }


def slim_pack(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: pack.get(k) for k in (
        "trades", "PnL", "PF", "maxDD", "avg_trade", "median_trade",
        "positive_day_rate", "median_daily_pnl", "AM", "PM", "first_entry", "re_entry",
    )}


def compare_a0_a2(a0: dict[str, Any], a2: dict[str, Any], *, flow0: dict, flow2: dict) -> dict[str, Any]:
    ex0 = a0.get("exclude") or {}
    ex2 = a2.get("exclude") or {}
    keys = (
        "trades", "PnL", "PF", "maxDD", "avg_trade", "median_trade",
        "positive_day_rate", "median_daily_pnl",
    )
    out: dict[str, Any] = {"tag": "A0_vs_A2"}
    for k in keys:
        out[f"A0_{k}"] = a0.get(k)
        out[f"A2_{k}"] = a2.get(k)
    out["A0_AM_pnl"] = (a0.get("AM") or {}).get("pnl")
    out["A2_AM_pnl"] = (a2.get("AM") or {}).get("pnl")
    out["A0_PM_pnl"] = (a0.get("PM") or {}).get("pnl")
    out["A2_PM_pnl"] = (a2.get("PM") or {}).get("pnl")
    out["A0_first_pnl"] = (a0.get("first_entry") or {}).get("pnl")
    out["A2_first_pnl"] = (a2.get("first_entry") or {}).get("pnl")
    out["A0_reentry_pnl"] = (a0.get("re_entry") or {}).get("pnl")
    out["A2_reentry_pnl"] = (a2.get("re_entry") or {}).get("pnl")
    for name in (
        "ex_top1_trade", "ex_top3_trades", "ex_best_day", "ex_top3_days",
        "ex_top_symbol", "ex_top3_symbols", "ex_285A",
    ):
        out[f"A0_{name}_PnL"] = (ex0.get(name) or {}).get("PnL")
        out[f"A2_{name}_PnL"] = (ex2.get(name) or {}).get("PnL")
    out["A0_PENDING_N"] = flow0.get("PENDING_N")
    out["A2_PENDING_N"] = flow2.get("PENDING_N")
    out["A0_EXPIRED_N"] = flow0.get("EXPIRED_N")
    out["A2_EXPIRED_N"] = flow2.get("EXPIRED_N")
    out["A0_PENDING_NONEXEC_N"] = flow0.get("PENDING_NONEXEC_AT_T0_N")
    out["A2_PENDING_NONEXEC_N"] = flow2.get("PENDING_NONEXEC_AT_T0_N")
    return out


def _slot(t: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(t.get("date") or ""),
        str(t.get("symbol") or "").replace(".T", ""),
        str(t.get("anchor_time") or t.get("anchor") or ""),
    )


def _hm_min(anchor: str) -> Optional[int]:
    s = str(anchor or "")
    if len(s) >= 5 and s[2] == ":":
        try:
            return int(s[:2]) * 60 + int(s[3:5])
        except ValueError:
            return None
    return None


def b2_trade_decomp(
    *,
    b0_trades: list[dict],
    b2_trades: list[dict],
    b0_admits: list[dict],
    b0_expired: list[dict],
    b0_rank: list[dict],
) -> dict[str, Any]:
    b0_t = tag_entry_kind(b0_trades)
    b2_t = tag_entry_kind(b2_trades)
    b0_map = {_slot(t): t for t in b0_t}
    b2_map = {_slot(t): t for t in b2_t}
    added_keys = [k for k in b2_map if k not in b0_map]
    removed_keys = [k for k in b0_map if k not in b2_map]
    added = [b2_map[k] for k in added_keys]
    removed = [b0_map[k] for k in removed_keys]

    def _kind_pack(xs: list[dict], kind: str) -> dict[str, Any]:
        sub = [t for t in xs if t.get("entry_kind") == kind]
        return {"n": len(sub), "PnL": float(sum(_pnl(t) for t in sub))}

    rank_idx = {(str(r.get("date")), str(r.get("anchor")), str(r.get("symbol"))): r for r in b0_rank}

    def _nonexec_admit(a: dict) -> bool:
        hit = rank_idx.get((str(a.get("date")), str(a.get("anchor")), str(a.get("symbol") or "").replace(".T", "")))
        return bool(hit is not None and not hit.get("executable_at_t0"))

    ne_anchors = {(str(a.get("date")), str(a.get("anchor"))) for a in b0_admits if _nonexec_admit(a)}
    b0_sym_days = {(str(t.get("date")), str(t.get("symbol") or "").replace(".T", "")) for t in b0_t}
    exp_by_day: dict[str, list[str]] = {}
    for e in b0_expired:
        exp_by_day.setdefault(str(e.get("date")), []).append(str(e.get("anchor") or ""))

    mech = Counter()
    for t in added:
        d, s, an = _slot(t)
        if (d, an) in ne_anchors:
            label = "CAP_FREEING"
        elif (d, s) in b0_sym_days:
            label = "SAME_SYMBOL_SEQUENCING"
        else:
            earlier = False
            hm = _hm_min(an)
            for ea in exp_by_day.get(d) or []:
                ehm = _hm_min(ea)
                if hm is not None and ehm is not None and ehm < hm:
                    earlier = True
                    break
            label = "SLOT_RELEASE" if earlier else "OTHER"
        mech[label] += 1
        t["b2_add_mechanism"] = label

    rem_mech = Counter()
    for t in removed:
        d, s, an = _slot(t)
        hit = rank_idx.get((d, an, s))
        if hit is not None and not hit.get("executable_at_t0"):
            lab = "DROPPED_NONEXEC_T0"
        else:
            lab = "DISPLACED_OCCUPANCY_CASCADE"
        rem_mech[lab] += 1
        t["b2_remove_mechanism"] = lab

    return {
        "B2_ADDED_TRADES_N": len(added),
        "B2_REMOVED_TRADES_N": len(removed),
        "added_first_entry": _kind_pack(added, "FIRST_ENTRY"),
        "added_re_entry": _kind_pack(added, "REENTRY"),
        "removed_first_entry": _kind_pack(removed, "FIRST_ENTRY"),
        "removed_re_entry": _kind_pack(removed, "REENTRY"),
        "B2_ADDED_FIRST_PNL": _kind_pack(added, "FIRST_ENTRY").get("PnL"),
        "B2_ADDED_REENTRY_PNL": _kind_pack(added, "REENTRY").get("PnL"),
        "added_mechanism": dict(mech),
        "removed_mechanism": dict(rem_mech),
        "POST_HOC_DIAGNOSTIC_ONLY": True,
        "B2_formal_unchanged": dict(B2_FORMAL),
    }


def paired_day_block(*, b0_trades: list[dict], b2_trades: list[dict], days: list[str]) -> dict[str, Any]:
    b0_t = tag_entry_kind(b0_trades)
    b2_t = tag_entry_kind(b2_trades)

    def by_day(xs: list[dict]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {d: [] for d in days}
        for t in xs:
            d = str(t.get("date"))
            if d in out:
                out[d].append(t)
        return out

    b0d = by_day(b0_t)
    b2d = by_day(b2_t)
    rows = []
    deltas = []
    for d in days:
        a = b0d[d]
        b = b2d[d]
        a_first = [t for t in a if t.get("entry_kind") == "FIRST_ENTRY"]
        b_first = [t for t in b if t.get("entry_kind") == "FIRST_ENTRY"]
        a_re = [t for t in a if t.get("entry_kind") == "REENTRY"]
        b_re = [t for t in b if t.get("entry_kind") == "REENTRY"]
        d_pnl = sum(_pnl(t) for t in b) - sum(_pnl(t) for t in a)
        deltas.append(d_pnl)
        rows.append(
            {
                "date": d,
                "B0_trades": len(a),
                "B0_PnL": sum(_pnl(t) for t in a),
                "B2_trades": len(b),
                "B2_PnL": sum(_pnl(t) for t in b),
                "delta_PnL": d_pnl,
                "B0_first_PnL": sum(_pnl(t) for t in a_first),
                "B2_first_PnL": sum(_pnl(t) for t in b_first),
                "delta_first": sum(_pnl(t) for t in b_first) - sum(_pnl(t) for t in a_first),
                "B0_reentry_PnL": sum(_pnl(t) for t in a_re),
                "B2_reentry_PnL": sum(_pnl(t) for t in b_re),
                "delta_reentry": sum(_pnl(t) for t in b_re) - sum(_pnl(t) for t in a_re),
            }
        )
    arr = np.asarray(deltas, dtype=float)
    pos = int(sum(1 for x in deltas if x > 1e-9))
    neg = int(sum(1 for x in deltas if x < -1e-9))
    zero = len(deltas) - pos - neg
    ordered = sorted(rows, key=lambda r: float(r["delta_PnL"]), reverse=True)

    def drop_days(n: int, *, worst: bool = False) -> Optional[float]:
        if not ordered:
            return None
        pick = ordered[-n:] if worst else ordered[:n]
        bad = {r["date"] for r in pick}
        return float(sum(r["delta_PnL"] for r in rows if r["date"] not in bad))

    rng = np.random.default_rng(0)
    boots = [float(np.mean(rng.choice(arr, size=arr.size, replace=True))) for _ in range(10000)] if arr.size else []
    ci = (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))) if boots else (None, None)
    return {
        "days": rows,
        "positive_delta_days": pos,
        "negative_delta_days": neg,
        "zero_delta_days": zero,
        "mean_delta": float(np.mean(arr)) if arr.size else None,
        "median_delta": float(np.median(arr)) if arr.size else None,
        "delta_ex_best_day": drop_days(1, worst=False),
        "delta_ex_top3_days": drop_days(3, worst=False),
        "delta_ex_worst_day": drop_days(1, worst=True),
        "bootstrap_mean_delta_ci95": {"lo": ci[0], "hi": ci[1], "n_boot": 10000, "seed": 0},
        "sign_diagnostic": {"n_pos": pos, "n_neg": neg, "n_zero": zero, "n": len(deltas)},
        "POST_HOC_DIAGNOSTIC_ONLY": True,
        "B2_PAIRED_ANALYSIS_CLASSIFICATION": "POST_HOC_DIAGNOSTIC_ONLY",
        "B2_formal_unchanged": dict(B2_FORMAL),
    }


def opportunity_a2(rank_rows: list[dict], trades: list[dict]) -> dict[str, Any]:
    raw = opportunity_cost_b2(rank_rows, trades)
    return {
        "A2_WOULD_DROP_FILL_N": raw.get("B2_WOULD_DROP_FILL_N"),
        "A2_WOULD_DROP_FILL_PNL": raw.get("B2_WOULD_DROP_FILL_PNL"),
        "A2_WOULD_DROP_OPENED_WITHIN_1S_N": raw.get("B2_WOULD_DROP_OPENED_WITHIN_1S_N"),
        "note": raw.get("note"),
    }


def decide(
    *,
    parity_ok: bool,
    bias: bool,
    a2_tested: bool,
    a0: dict,
    a2: dict,
    flow0: dict,
    flow2: dict,
    opp: dict,
    decomp: dict,
) -> dict[str, Any]:
    if not parity_ok:
        return {
            "VERDICT": "CURRENT_ENTRY_NONEXEC_AUDIT_INTEGRITY_FAILED",
            "NONEXEC_BIAS_SCOPE": "INCONCLUSIVE",
            "PRIMARY_MECHANISM": None,
            "RECOMMENDED_NEXT_RESEARCH": "STOP. Repair A0 Exact Dual-Lane parity.",
            "CASE": None,
        }
    if not bias:
        return {
            "VERDICT": "CURRENT_ENTRY_NONEXEC_BIAS_NOT_REPRODUCED",
            "NONEXEC_BIAS_SCOPE": "B_ONLY",
            "PRIMARY_MECHANISM": "nonexec rank bias not reproduced on CURRENT IRREGULAR CLOCK",
            "RECOMMENDED_NEXT_RESEARCH": "D. no further work on CURRENT ENTRY executability eligibility",
            "CASE": 1,
        }

    drop_n = int(opp.get("A2_WOULD_DROP_FILL_N") or 0)
    drop_pnl = float(opp.get("A2_WOULD_DROP_FILL_PNL") or 0.0)
    a2_ne_pending = flow2.get("PENDING_NONEXEC_AT_T0_N")
    wasted_cleared = a2_tested and a2_ne_pending is not None and int(a2_ne_pending) == 0
    material_fill_loss = drop_n >= 5 or abs(drop_pnl) >= 50000.0

    a0_first = float((a0.get("first_entry") or {}).get("pnl") or 0.0)
    a2_first = float((a2.get("first_entry") or {}).get("pnl") or 0.0) if a2_tested else a0_first
    a0_re = float((a0.get("re_entry") or {}).get("pnl") or 0.0)
    a2_re = float((a2.get("re_entry") or {}).get("pnl") or 0.0) if a2_tested else a0_re
    first_up = a2_tested and (a2_first - a0_first) > 10000.0
    re_down = a2_tested and (a2_re - a0_re) < -10000.0

    added_first = ((decomp.get("added_first_entry") or {}).get("PnL") or 0.0)
    added_re = ((decomp.get("added_re_entry") or {}).get("PnL") or 0.0)

    if a2_tested and first_up and re_down:
        verdict = "CURRENT_ENTRY_NONEXEC_BIAS_CONFIRMED_REENTRY_IS_NEXT_PROBLEM"
        nxt = "B. re-entry architecture"
        case = 4
        mech = (
            "executability eligibility frees wasted nonexec PENDING/CAP; "
            "first-entry improves; additional re-entry chain consumes the gain"
        )
    elif a2_tested and wasted_cleared and not material_fill_loss:
        verdict = "CURRENT_ENTRY_NONEXEC_BIAS_CONFIRMED_GATE_PROMISING"
        nxt = "A. executability eligibility"
        case = 2
        mech = "nonexec names are wasted ranking/PENDING slots; t0 executable eligibility removes them with small Fill opportunity cost"
    elif a2_tested:
        verdict = "CURRENT_ENTRY_NONEXEC_BIAS_CONFIRMED_GATE_NOT_SUFFICIENT"
        nxt = "D. no further work" if material_fill_loss else "A. executability eligibility"
        case = 3
        mech = "bias exists on CURRENT IRREGULAR; standalone t0 executable gate is not a sufficient structural fix"
    else:
        verdict = "CURRENT_ENTRY_NONEXEC_BIAS_CONFIRMED_GATE_NOT_SUFFICIENT"
        nxt = "A. executability eligibility"
        case = 3
        mech = "bias confirmed; A2 not tested"
        wasted_cleared = False

    # Q3 primary among A2/B2
    cap_n = int((decomp.get("added_mechanism") or {}).get("CAP_FREEING") or 0)
    if cap_n >= max(1, int((decomp.get("B2_ADDED_TRADES_N") or 0) * 0.4)):
        q3 = "CAP liberation"
    elif added_first > added_re:
        q3 = "better first selection"
    elif int((decomp.get("added_re_entry") or {}).get("n") or 0) > int((decomp.get("added_first_entry") or {}).get("n") or 0):
        q3 = "changed re-entry chain"
    else:
        q3 = "reduced expiry / occupancy cascade"

    fill_ne = int(flow0.get("FILL_FROM_NONEXEC_AT_T0_N") or 0)
    pending_ne = int(flow0.get("PENDING_NONEXEC_AT_T0_N") or 0)
    q2 = "almost wasted ranking/PENDING slot" if pending_ne > 0 and fill_ne <= 1 else "mix of Fill opportunity and waste"

    return {
        "VERDICT": verdict,
        "CASE": case,
        "NONEXEC_BIAS_SCOPE": "CURRENT_ENTRY_GENERAL",
        "PRIMARY_MECHANISM": mech,
        "Q1": "CURRENT ENTRY itself (reproduced on CURRENT IRREGULAR CLOCK, not UNIFORM10-only)",
        "Q2": q2,
        "Q3": q3,
        "Q4": (
            f"B2 added first-entry PnL={decomp.get('B2_ADDED_FIRST_PNL')} "
            f"added re-entry PnL={decomp.get('B2_ADDED_REENTRY_PNL')}; "
            "nonexec pending no longer reserve CAP so later first-entries fill, "
            "and the extra occupancy then seeds additional re-entries whose net is worse"
        ),
        "Q5": nxt,
        "RECOMMENDED_NEXT_RESEARCH": nxt,
        "A2_wasted_slots_cleared": wasted_cleared,
        "A2_material_fill_loss": material_fill_loss,
        "A2_first_pnl_delta": (a2_first - a0_first) if a2_tested else None,
        "A2_reentry_pnl_delta": (a2_re - a0_re) if a2_tested else None,
        "B2_VERDICT_UNCHANGED": dict(B2_FORMAL),
    }
