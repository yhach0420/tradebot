"""Incremental lineage, additive PnL decomp, precommitted verdicts."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from research.anchor_10min_opportunity import (
    DAY_POS_TOL,
    MISSED_FILL_MIN,
    OFFSET_SHARE,
    PHASE_A_PNL_EPS,
    PNL_PER_FILL_RATIO_OK,
    SUPPORT_DAY_POS_DROP,
    VERDICT_A_MIXED,
    VERDICT_A_NONE,
    VERDICT_A_OFFSET,
    VERDICT_A_VALUE,
    VERDICT_GAP_ONLY,
    VERDICT_NOT,
    VERDICT_SUPPORTED,
)
from research.anchor_10min_opportunity.grids import phase_a_added_labels, tod_family
from research.anchor_economic_sensitivity.decompose import daily_pnl, portfolio_reentry
from research.anchor_timing_robustness.metrics import maxdd, mean_finite, trade_stats


def _pnl(row: dict[str, Any]) -> float:
    return float(row.get("pnl_yen_100") or 0.0)


def _kind_rows(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tagged = []
    for t in trades:
        rec = dict(t)
        rec.setdefault("shift_key", rec.get("variant") or "X")
        tagged.append(rec)
    return tagged


def first_reentry(trades: list[dict[str, Any]], label: str) -> dict[str, Any]:
    rows = []
    for t in trades:
        rec = dict(t)
        rec["shift_key"] = label
        rec["pnl_yen_100"] = _pnl(t)
        rows.append(rec)
    st = portfolio_reentry(rows, label)
    st.pop("lineage", None)
    return st


def _flatten(days: list[dict[str, Any]], variant: str) -> list[dict[str, Any]]:
    out = []
    for d in days:
        pack = (d.get("portfolios") or {}).get(variant) or {}
        for t in pack.get("trades") or []:
            rec = dict(t)
            rec["variant"] = variant
            rec["period"] = d.get("period")
            rec["tod_family"] = tod_family(str(rec.get("anchor_time") or ""))
            out.append(rec)
    return out


def _iso_flat(days: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    out = []
    for d in days:
        for t in d.get(key) or []:
            rec = dict(t)
            rec["period"] = d.get("period")
            out.append(rec)
    return out


def _day_pos_rate(trades: list[dict[str, Any]]) -> Optional[float]:
    daily = daily_pnl(trades)
    if not daily:
        return None
    pos = sum(1 for r in daily if float(r["pnl"]) > 0)
    return pos / float(len(daily))


def _sess_split(trades: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for sess in ("AM", "PM"):
        sub = [t for t in trades if str(t.get("session")) == sess]
        st = trade_stats(sub)
        st["maxDD"] = maxdd(sub)
        out[sess] = st
    return out


def _tod_split(trades: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for fam in ("OPEN_EARLY", "NORMAL_SESSION", "SESSION_TAIL"):
        sub = [t for t in trades if t.get("tod_family") == fam]
        st = trade_stats(sub)
        st["maxDD"] = maxdd(sub)
        out[fam] = st
    return out


def incremental_lineage(
    ref: list[dict[str, Any]],
    aug: list[dict[str, Any]],
    *,
    midpoints: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_r: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_a: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in ref:
        by_r[(str(t.get("date")), str(t.get("symbol")))].append(t)
    for t in aug:
        by_a[(str(t.get("date")), str(t.get("symbol")))].append(t)
    keys = sorted(set(by_r) | set(by_a))
    rows = []
    direct = displaced = capacity = state = reentry = 0.0
    n_new_fill = n_displaced = n_blocked = n_same_earlier = n_cap = n_re = n_none = 0

    def _sort(xs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(xs, key=lambda x: float(x.get("fill_time") or 0.0))

    for key in keys:
        rseq = _sort(by_r.get(key, []))
        aseq = _sort(by_a.get(key, []))
        n = min(len(rseq), len(aseq))
        date, sym = key
        for i in range(n):
            r, a = rseq[i], aseq[i]
            dlt = _pnl(a) - _pnl(r)
            a_mid = str(a.get("anchor_time") or "") in midpoints
            r_mid = str(r.get("anchor_time") or "") in midpoints
            if a_mid and not r_mid:
                cls = "SAME_SYMBOL_EARLIER_ENTRY"
                displaced += dlt
                n_same_earlier += 1
                n_displaced += 1
            elif (not a_mid) and (not r_mid):
                cls = "SAME_SYMBOL_STATE_EFFECT"
                state += dlt
            else:
                cls = "SAME_SYMBOL_STATE_EFFECT"
                state += dlt
            rows.append(
                {
                    "date": date,
                    "symbol": sym,
                    "class": cls,
                    "entry_index": i + 1,
                    "anchor": a.get("anchor_time"),
                    "anchor_ref": r.get("anchor_time"),
                    "rank": a.get("candidate_rank"),
                    "score": a.get("score"),
                    "fill_time": a.get("fill_time"),
                    "fill_price": a.get("fill_price"),
                    "exit_time": a.get("exit_time"),
                    "exit_reason": a.get("exit_reason"),
                    "pnl": _pnl(a),
                    "pnl_ref": _pnl(r),
                    "pnl_delta": dlt,
                    "mfe": a.get("mfe_yen_100"),
                    "mae": a.get("mae_yen_100"),
                    "later_canonical_in_reference": str(r.get("anchor_time") or ""),
                }
            )
        for j, a in enumerate(aseq[n:]):
            a_mid = str(a.get("anchor_time") or "") in midpoints
            entry_index = n + j + 1
            is_reentry = entry_index >= 2
            if a_mid and not is_reentry:
                cls = "NEW_OPPORTUNITY_FILL"
                direct += _pnl(a)
                n_new_fill += 1
            elif is_reentry:
                cls = "REENTRY_EFFECT"
                reentry += _pnl(a)
                n_re += 1
            else:
                cls = "CAPACITY_INTERACTION"
                capacity += _pnl(a)
                n_cap += 1
            rows.append(
                {
                    "date": date,
                    "symbol": sym,
                    "class": cls,
                    "entry_index": entry_index,
                    "anchor": a.get("anchor_time"),
                    "anchor_ref": None,
                    "rank": a.get("candidate_rank"),
                    "score": a.get("score"),
                    "fill_time": a.get("fill_time"),
                    "fill_price": a.get("fill_price"),
                    "exit_time": a.get("exit_time"),
                    "exit_reason": a.get("exit_reason"),
                    "pnl": _pnl(a),
                    "pnl_ref": 0.0,
                    "pnl_delta": _pnl(a),
                    "mfe": a.get("mfe_yen_100"),
                    "mae": a.get("mae_yen_100"),
                    "later_canonical_in_reference": None,
                }
            )
        for j, r in enumerate(rseq[n:]):
            entry_index = n + j + 1
            if entry_index >= 2:
                cls = "REENTRY_EFFECT"
                reentry -= _pnl(r)
                n_re += 1
            else:
                cls = "BLOCKED_LATER_ENTRY"
                displaced -= _pnl(r)
                n_blocked += 1
            rows.append(
                {
                    "date": date,
                    "symbol": sym,
                    "class": cls,
                    "entry_index": entry_index,
                    "anchor": None,
                    "anchor_ref": r.get("anchor_time"),
                    "rank": r.get("candidate_rank"),
                    "score": r.get("score"),
                    "fill_time": r.get("fill_time"),
                    "fill_price": r.get("fill_price"),
                    "exit_time": r.get("exit_time"),
                    "exit_reason": r.get("exit_reason"),
                    "pnl": 0.0,
                    "pnl_ref": _pnl(r),
                    "pnl_delta": -_pnl(r),
                    "mfe": r.get("mfe_yen_100"),
                    "mae": r.get("mae_yen_100"),
                    "later_canonical_in_reference": r.get("anchor_time"),
                }
            )

    observed = round(sum(_pnl(t) for t in aug) - sum(_pnl(t) for t in ref), 2)
    recon = round(direct + displaced + capacity + state + reentry, 2)
    residual = round(observed - recon, 2)
    decomp = {
        "DIRECT_NEW_ENTRY_VALUE": round(direct, 2),
        "DISPLACED_ENTRY_EFFECT": round(displaced, 2),
        "CAPACITY_EFFECT": round(capacity, 2),
        "SAME_SYMBOL_STATE_EFFECT": round(state, 2),
        "REENTRY_EFFECT": round(reentry, 2),
        "residual": residual,
        "recon": recon,
        "observed": observed,
        "NEW_OPPORTUNITY_FILL_N": n_new_fill,
        "DISPLACED_LATER_ENTRY_N": n_displaced,
        "BLOCKED_LATER_ENTRY_N": n_blocked,
        "SAME_SYMBOL_EARLIER_N": n_same_earlier,
        "CAPACITY_INTERACTION_N": n_cap,
        "REENTRY_EFFECT_N": n_re,
        "NO_PORTFOLIO_EFFECT_N": n_none,
    }
    return rows, decomp


def _iso_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = rows
    filled = [r for r in selected if r.get("independent_filled") in (True, "True", "true", 1)]
    st = trade_stats(filled)
    st["selected"] = len(selected)
    st["fills"] = len(filled)
    st["fill_rate"] = (len(filled) / len(selected)) if selected else None
    st["direct_pnl"] = st.get("pnl")
    return st


def _port_stats(trades: list[dict[str, Any]], *, anchors_n: int, selected_n: int) -> dict[str, Any]:
    st = trade_stats(trades)
    st["maxDD"] = maxdd(trades)
    nfill = int(st.get("trades") or 0)
    uniq = len({str(t.get("symbol")) for t in trades})
    uniq_sel = None
    st["unique_fill_symbols"] = uniq
    st["selected_n"] = selected_n
    st["anchor_n"] = anchors_n
    pnl = float(st.get("pnl") or 0.0)
    gp = float(st.get("gross_profit") or 0.0)
    gl = float(st.get("gross_loss") or 0.0)
    st["pnl_per_anchor"] = (pnl / anchors_n) if anchors_n else None
    st["pnl_per_selected"] = (pnl / selected_n) if selected_n else None
    st["pnl_per_fill"] = (pnl / nfill) if nfill else None
    st["pnl_per_unique_symbol"] = (pnl / uniq) if uniq else None
    st["gross_profit_per_anchor"] = (gp / anchors_n) if anchors_n else None
    st["gross_loss_per_anchor"] = (gl / anchors_n) if anchors_n else None
    st["maxDD_per_fill"] = (float(st["maxDD"]) / nfill) if nfill else None
    st["day_positive_rate"] = _day_pos_rate(trades)
    daily = daily_pnl(trades)
    st["day_pnl_median"] = mean_finite([d["pnl"] for d in daily])
    fr = first_reentry(trades, "X")
    st["FIRST_ENTRY_PNL"] = fr.get("FIRST_ENTRY_PNL")
    st["REENTRY_PNL"] = fr.get("REENTRY_PNL")
    st["FIRST_ENTRY_N"] = fr.get("FIRST_ENTRY_N")
    st["REENTRY_COUNT"] = fr.get("REENTRY_COUNT")
    st["AM"] = _sess_split(trades).get("AM")
    st["PM"] = _sess_split(trades).get("PM")
    st["tod"] = _tod_split(trades)
    return st


def _selected_n(days: list[dict[str, Any]], variant: str) -> int:
    n = 0
    for d in days:
        n += int(((d.get("portfolios") or {}).get(variant) or {}).get("selected_n") or 0)
    return n


def missed_vs_later(
    iso_added: list[dict[str, Any]],
    ref: list[dict[str, Any]],
    aug: list[dict[str, Any]],
    midpoints: set[str],
) -> dict[str, Any]:
    """Split isolated midpoint fills into missed vs later-canonical-also-taken."""
    ref_by = defaultdict(list)
    for t in ref:
        ref_by[(str(t.get("date")), str(t.get("symbol")))].append(t)
    filled = [r for r in iso_added if r.get("independent_filled") in (True, "True", "true", 1)]
    missed, later = [], []
    for r in filled:
        key = (str(r.get("date")), str(r.get("symbol")))
        t0 = float(r.get("t0") or 0.0)
        later_hits = [
            t
            for t in ref_by.get(key, [])
            if float(t.get("fill_time") or 0.0) > t0 + 1e-6
            and str(t.get("anchor_time") or "") not in midpoints
        ]
        bucket = later if later_hits else missed
        rec = dict(r)
        rec["later_ref_anchors"] = [x.get("anchor_time") for x in later_hits]
        rec["later_ref_pnl"] = round(sum(_pnl(x) for x in later_hits), 2)
        bucket.append(rec)
    return {
        "MISSED_OPPORTUNITY_FILLS": len(missed),
        "LATER_CANONICAL_ALSO_TAKEN": len(later),
        "missed_pnl": round(sum(float(x.get("pnl_yen_100") or 0.0) for x in missed), 2),
        "later_also_taken_iso_pnl": round(sum(float(x.get("pnl_yen_100") or 0.0) for x in later), 2),
        "missed_rows": missed,
        "later_rows": later,
    }


def phase_a_verdict(
    *,
    iso: dict[str, Any],
    ref: dict[str, Any],
    aug: dict[str, Any],
    decomp: dict[str, Any],
    day_pos_ref: Optional[float],
    day_pos_aug: Optional[float],
) -> str:
    iso_pnl = float(iso.get("pnl") or 0.0)
    iso_fills = int(iso.get("fills") or 0)
    d_pnl = float(aug.get("pnl") or 0.0) - float(ref.get("pnl") or 0.0)
    isolated_pos = iso_pnl > PHASE_A_PNL_EPS and iso_fills > 0
    port_pos = d_pnl > PHASE_A_PNL_EPS
    offset_abs = abs(float(decomp.get("DISPLACED_ENTRY_EFFECT") or 0.0)) + abs(
        float(decomp.get("CAPACITY_EFFECT") or 0.0)
    ) + abs(float(decomp.get("SAME_SYMBOL_STATE_EFFECT") or 0.0))
    offset = isolated_pos and (not port_pos) and (
        offset_abs >= OFFSET_SHARE * max(abs(iso_pnl), 1.0) or d_pnl <= PHASE_A_PNL_EPS
    )
    day_ok = True
    if day_pos_ref is not None and day_pos_aug is not None:
        day_ok = day_pos_aug + 1e-12 >= day_pos_ref - DAY_POS_TOL
    if isolated_pos and port_pos and float(decomp.get("DIRECT_NEW_ENTRY_VALUE") or 0.0) > PHASE_A_PNL_EPS and day_ok:
        if float(decomp.get("DISPLACED_ENTRY_EFFECT") or 0.0) < 0 and abs(
            float(decomp.get("DISPLACED_ENTRY_EFFECT") or 0.0)
        ) >= OFFSET_SHARE * abs(float(decomp.get("DIRECT_NEW_ENTRY_VALUE") or 0.0)):
            return VERDICT_A_MIXED
        return VERDICT_A_VALUE
    if offset:
        return VERDICT_A_OFFSET
    if (not isolated_pos) and (not port_pos):
        return VERDICT_A_NONE
    return VERDICT_A_MIXED


def historical_support(*, ref: dict[str, Any], uni: dict[str, Any]) -> str:
    rp = float(ref.get("pnl") or 0.0)
    up = float(uni.get("pnl") or 0.0)
    rpf = ref.get("pnl_per_fill")
    upf = uni.get("pnl_per_fill")
    rd = ref.get("day_positive_rate")
    ud = uni.get("day_positive_rate")
    better = up > rp + PHASE_A_PNL_EPS
    fill_ok = True
    if rpf and upf is not None and abs(float(rpf)) > 1e-9:
        fill_ok = float(upf) >= PNL_PER_FILL_RATIO_OK * float(rpf)
    day_ok = True
    if rd is not None and ud is not None:
        day_ok = float(ud) + 1e-12 >= float(rd) - SUPPORT_DAY_POS_DROP
    if better and fill_ok and day_ok:
        return "STRONG"
    if better:
        return "MODERATE"
    if up > PHASE_A_PNL_EPS and abs(up - rp) <= abs(rp) * 0.25:
        return "WEAK"
    if up + PHASE_A_PNL_EPS < rp:
        return "NEGATIVE"
    return "WEAK"


def missed_evidence(*, missed: dict[str, Any], iso: dict[str, Any], a_verdict: str) -> str:
    n = int(missed.get("MISSED_OPPORTUNITY_FILLS") or 0)
    pnl = float(missed.get("missed_pnl") or 0.0)
    pf = iso.get("PF")
    pf_ok = False
    try:
        pf_ok = pf is not None and pf != "Infinity" and float(pf) >= 1.0
    except (TypeError, ValueError):
        pf_ok = pf == "Infinity"
    if n >= MISSED_FILL_MIN and pnl > PHASE_A_PNL_EPS and pf_ok and a_verdict == VERDICT_A_VALUE:
        return "STRONG"
    if n >= MISSED_FILL_MIN and pnl > PHASE_A_PNL_EPS:
        return "MODERATE"
    if n > 0 and pnl > PHASE_A_PNL_EPS:
        return "WEAK"
    return "NONE"


def final_verdict(*, a_verdict: str, support: str) -> str:
    gap_yes = a_verdict in {VERDICT_A_VALUE, VERDICT_A_MIXED, VERDICT_A_OFFSET}
    uni_yes = support in {"STRONG", "MODERATE"}
    if gap_yes and uni_yes:
        return VERDICT_SUPPORTED
    if gap_yes and not uni_yes:
        return VERDICT_GAP_ONLY
    return VERDICT_NOT


def run_analysis(days: list[dict[str, Any]]) -> dict[str, Any]:
    midpoints = set(phase_a_added_labels())
    ref = _flatten(days, "REFERENCE")
    aug = _flatten(days, "AUGMENTED")
    uni = _flatten(days, "UNIFORM10")
    iso_added = _iso_flat(days, "isolated_added")
    iso_tail = _iso_flat(days, "isolated_tail")
    iso_u10 = _iso_flat(days, "isolated_uniform_new")
    lin, decomp = incremental_lineage(ref, aug, midpoints=midpoints)
    iso_st = _iso_stats(iso_added)
    tail_st = _iso_stats(iso_tail)
    u10new_st = _iso_stats(iso_u10)
    ref_sel = _selected_n(days, "REFERENCE")
    aug_sel = _selected_n(days, "AUGMENTED")
    uni_sel = _selected_n(days, "UNIFORM10")
    from research.anchor_10min_opportunity.grids import augmented_tuples, reference_tuples, uniform10_tuples

    n_days = len(days) or 1
    ref_st = _port_stats(ref, anchors_n=len(reference_tuples()) * n_days, selected_n=ref_sel)
    aug_st = _port_stats(aug, anchors_n=len(augmented_tuples()) * n_days, selected_n=aug_sel)
    uni_st = _port_stats(uni, anchors_n=len(uniform10_tuples()) * n_days, selected_n=uni_sel)
    missed = missed_vs_later(iso_added, ref, aug, midpoints)
    a_verdict = phase_a_verdict(
        iso=iso_st,
        ref=ref_st,
        aug=aug_st,
        decomp=decomp,
        day_pos_ref=ref_st.get("day_positive_rate"),
        day_pos_aug=aug_st.get("day_positive_rate"),
    )
    support = historical_support(ref=ref_st, uni=uni_st)
    missed_ev = missed_evidence(missed=missed, iso=iso_st, a_verdict=a_verdict)
    verdict = final_verdict(a_verdict=a_verdict, support=support)

    daily = {
        "REFERENCE": daily_pnl(ref),
        "AUGMENTED": daily_pnl(aug),
        "UNIFORM10": daily_pnl(uni),
    }
    no_fill = [r for r in iso_added if r.get("independent_filled") not in (True, "True", "true", 1)]
    for r in no_fill:
        lin.append(
            {
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "class": "NEW_OPPORTUNITY_NO_FILL",
                "entry_index": None,
                "anchor": r.get("anchor"),
                "rank": r.get("rank"),
                "score": r.get("alloc_score") if r.get("alloc_score") is not None else r.get("score"),
                "fill_time": None,
                "pnl": 0.0,
                "later_canonical_in_reference": None,
            }
        )
    decomp["NEW_OPPORTUNITY_NO_FILL_N"] = len(no_fill)

    # NO_PORTFOLIO_EFFECT: isolated fill that never appears in augmented portfolio
    aug_keys = {(str(t.get("date")), str(t.get("symbol")), str(t.get("anchor_time"))) for t in aug}
    n_none = 0
    for r in iso_added:
        if r.get("independent_filled") not in (True, "True", "true", 1):
            continue
        key = (str(r.get("date")), str(r.get("symbol")), str(r.get("anchor")))
        if key not in aug_keys:
            n_none += 1
    decomp["NO_PORTFOLIO_EFFECT_N"] = n_none

    return {
        "ref_trades": ref,
        "aug_trades": aug,
        "uni_trades": uni,
        "iso_added": iso_added,
        "iso_tail": iso_tail,
        "iso_u10": iso_u10,
        "lineage": lin,
        "decomp": decomp,
        "iso_added_stats": iso_st,
        "iso_tail_stats": tail_st,
        "iso_u10new_stats": u10new_st,
        "ref_stats": ref_st,
        "aug_stats": aug_st,
        "uni_stats": uni_st,
        "missed": {k: v for k, v in missed.items() if k not in {"missed_rows", "later_rows"}},
        "missed_rows": missed.get("missed_rows") or [],
        "later_rows": missed.get("later_rows") or [],
        "daily": daily,
        "PHASE_A_VERDICT": a_verdict,
        "HISTORICAL_10MIN_SUPPORT": support,
        "MISSED_OPPORTUNITY_EVIDENCE": missed_ev,
        "verdict": verdict,
        "first_reentry": {
            "REFERENCE": first_reentry(ref, "REFERENCE"),
            "AUGMENTED": first_reentry(aug, "AUGMENTED"),
            "UNIFORM10": first_reentry(uni, "UNIFORM10"),
        },
    }
