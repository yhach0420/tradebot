"""Causal decomposition of isolated + portfolio economic sensitivity."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

from research.anchor_economic_sensitivity import (
    ALL_SHIFTS,
    BOUNDARY_HIGH,
    BOUNDARY_MED,
    COMPONENT_DOMINANT,
    COMPONENT_MATERIAL,
    DEVELOPMENT_DAYS,
    EARLY_GUARD,
    EXIT600,
    EXTEND750,
    HOLDOUT_DAYS,
    LOO_STABLE_MIN,
    PRIMARY_SHIFTS,
    REENTRY_SHRINK_PRIMARY,
    RESIDUAL_FAIL,
    RESIDUAL_OK,
    SAME_PRICE_BPS,
    TAIL_DAY_SHARE_HIGH,
    VERDICT_FAIL,
    VERDICT_OK,
)
from research.anchor_timing_robustness.metrics import mean_finite, pf_of, pf_out, trade_stats


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def _pnl(row: dict[str, Any]) -> float:
    return float(_f(row.get("pnl_yen_100")) or 0.0)


def _bool(v: Any) -> bool:
    return v in (True, "True", "true", 1)


def exit_family(reason: Any) -> str:
    r = str(reason or "").strip()
    if not r:
        return "NONE"
    if r in EARLY_GUARD or r.upper().startswith("IMB"):
        return "EARLY_GUARD"
    if r in EXIT600:
        return "EXIT600"
    if r in EXTEND750:
        return "EXTEND750"
    if r == "SESSION_CLOSE":
        return "SESSION_CLOSE"
    return "OTHER"


def _bps(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or a <= 0:
        return None
    return (float(b) - float(a)) / float(a) * 10000.0


def _in_days(row: dict[str, Any], days: Optional[Iterable[str]]) -> bool:
    if days is None:
        return True
    return str(row.get("date")) in set(days)


def iso_index(iso: list[dict[str, Any]], *, days: Optional[Iterable[str]] = None) -> dict[tuple, dict[str, Any]]:
    out: dict[tuple, dict[str, Any]] = {}
    for r in iso:
        if not _in_days(r, days):
            continue
        key = (str(r.get("date")), str(r.get("anchor")), str(r.get("shift_key")), str(r.get("symbol")))
        out[key] = r
    return out


def selected_sets(comparisons: list[dict[str, Any]], shift: str, *, days: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
    rows = []
    for c in comparisons:
        if not c.get("valid"):
            continue
        if str(c.get("shift_key")) != shift:
            continue
        if days is not None and str(c.get("date")) not in set(days):
            continue
        sref = [str(x) for x in (c.get("selected_ref") or [])]
        ssh = [str(x) for x in (c.get("selected_shift") or [])]
        rows.append(
            {
                "date": str(c.get("date")),
                "anchor": str(c.get("anchor")),
                "tod_bucket": c.get("tod_bucket"),
                "shift_key": shift,
                "selected_ref": sref,
                "selected_shift": ssh,
                "common": sorted(set(sref) & set(ssh)),
                "ref_only": sorted(set(sref) - set(ssh)),
                "shift_only": sorted(set(ssh) - set(sref)),
                "top10_ref": [str(x) for x in (c.get("top10_ref") or [])],
                "top10_shift": [str(x) for x in (c.get("top10_shift") or [])],
                "ref_entry_rank_at_shift": c.get("ref_entry_rank_at_shift") or [],
                "shift_entry_rank_at_ref": c.get("shift_entry_rank_at_ref") or [],
            }
        )
    return rows


def lineage_rows(
    sets: list[dict[str, Any]],
    idx: dict[tuple, dict[str, Any]],
    shift: str,
) -> list[dict[str, Any]]:
    out = []
    for s in sets:
        day, an = s["date"], s["anchor"]
        for cls, symbols in (
            ("COMMON_SELECTED", s["common"]),
            ("REFERENCE_ONLY", s["ref_only"]),
            ("SHIFT_ONLY", s["shift_only"]),
        ):
            for sym in symbols:
                ref = idx.get((day, an, "REFERENCE", sym), {})
                sh = idx.get((day, an, shift, sym), {})
                fill_r = _bool(ref.get("independent_filled")) if ref else False
                fill_s = _bool(sh.get("independent_filled")) if sh else False
                px_r = _f(ref.get("fill_price")) if fill_r else None
                px_s = _f(sh.get("fill_price")) if fill_s else None
                t0_r = _f(ref.get("t0"))
                t0_s = _f(sh.get("t0"))
                ft_r = _f(ref.get("fill_time"))
                ft_s = _f(sh.get("fill_time"))
                row = {
                    "date": day,
                    "period": ref.get("period") or sh.get("period"),
                    "anchor": an,
                    "shifted_family": shift,
                    "symbol": sym,
                    "class": cls,
                    "rank_ref": ref.get("rank"),
                    "rank_shift": sh.get("rank"),
                    "score_ref": _f(ref.get("alloc_score") if ref.get("alloc_score") is not None else ref.get("score")),
                    "score_shift": _f(sh.get("alloc_score") if sh.get("alloc_score") is not None else sh.get("score")),
                    "limit_price_ref": _f(ref.get("limit")),
                    "limit_price_shift": _f(sh.get("limit")),
                    "bid_ref": _f(ref.get("bid_at_anchor")),
                    "ask_ref": _f(ref.get("ask_at_anchor")),
                    "bid_shift": _f(sh.get("bid_at_anchor")),
                    "ask_shift": _f(sh.get("ask_at_anchor")),
                    "spread_bps_ref": _f(ref.get("spread_bps")),
                    "spread_bps_shift": _f(sh.get("spread_bps")),
                    "imbalance_ref": _f(ref.get("imbalance")),
                    "imbalance_shift": _f(sh.get("imbalance")),
                    "ret_1m_bps_ref": _f(ref.get("ret_1m_bps")),
                    "ret_1m_bps_shift": _f(sh.get("ret_1m_bps")),
                    "ret_5m_bps_ref": _f(ref.get("ret_5m_bps")),
                    "ret_5m_bps_shift": _f(sh.get("ret_5m_bps")),
                    "fill_ref": fill_r,
                    "fill_shift": fill_s,
                    "no_fill_reference": (cls != "SHIFT_ONLY") and (not fill_r) and bool(ref),
                    "no_fill_shift": (cls != "REFERENCE_ONLY") and (not fill_s) and bool(sh),
                    "fill_time_ref": ft_r,
                    "fill_time_shift": ft_s,
                    "fill_latency_ref": (ft_r - t0_r) if (ft_r is not None and t0_r is not None) else None,
                    "fill_latency_shift": (ft_s - t0_s) if (ft_s is not None and t0_s is not None) else None,
                    "fill_price_ref": px_r,
                    "fill_price_shift": px_s,
                    "entry_price_delta_bps": _bps(px_r, px_s),
                    "limit_delta_bps": _bps(_f(ref.get("limit")), _f(sh.get("limit"))),
                    "exit_time_ref": _f(ref.get("exit_time")),
                    "exit_time_shift": _f(sh.get("exit_time")),
                    "exit_reason_ref": ref.get("exit_reason"),
                    "exit_reason_shift": sh.get("exit_reason"),
                    "exit_family_ref": exit_family(ref.get("exit_reason")),
                    "exit_family_shift": exit_family(sh.get("exit_reason")),
                    "pnl_ref": _pnl(ref) if fill_r else 0.0,
                    "pnl_shift": _pnl(sh) if fill_s else 0.0,
                    "MFE_ref": _f(ref.get("mfe_yen_100")),
                    "MFE_shift": _f(sh.get("mfe_yen_100")),
                    "MAE_ref": _f(ref.get("mae_yen_100")),
                    "MAE_shift": _f(sh.get("mae_yen_100")),
                    "fill_class": None,
                }
                if fill_r and fill_s:
                    dlt = row["entry_price_delta_bps"]
                    if dlt is None or abs(dlt) <= SAME_PRICE_BPS:
                        row["fill_class"] = "BOTH_FILL_SAME_PRICE"
                    else:
                        row["fill_class"] = "BOTH_FILL_DIFFERENT_PRICE"
                elif fill_r and not fill_s:
                    row["fill_class"] = "FILL_LOST"
                elif fill_s and not fill_r:
                    row["fill_class"] = "FILL_GAINED"
                else:
                    row["fill_class"] = "NEITHER_FILL"
                if cls != "SHIFT_ONLY" and not fill_r:
                    if cls != "REFERENCE_ONLY" and not fill_s:
                        row["no_fill_class"] = "NO_FILL_BOTH"
                    else:
                        row["no_fill_class"] = "NO_FILL_REFERENCE"
                elif cls != "REFERENCE_ONLY" and not fill_s:
                    row["no_fill_class"] = "NO_FILL_SHIFT"
                else:
                    row["no_fill_class"] = None
                row["pnl_delta"] = row["pnl_shift"] - row["pnl_ref"]
                out.append(row)
    return out


def _sum_pnl(rows: Iterable[dict[str, Any]], key: str) -> float:
    return round(sum(float(r.get(key) or 0.0) for r in rows), 2)


def isolated_decomp(lin: list[dict[str, Any]]) -> dict[str, Any]:
    common = [r for r in lin if r["class"] == "COMMON_SELECTED"]
    ref_only = [r for r in lin if r["class"] == "REFERENCE_ONLY"]
    sh_only = [r for r in lin if r["class"] == "SHIFT_ONLY"]
    both_fill = [r for r in common if r["fill_ref"] and r["fill_shift"]]
    fill_xor = [r for r in common if bool(r["fill_ref"]) != bool(r["fill_shift"])]
    a = _sum_pnl(both_fill, "pnl_delta")
    c = _sum_pnl(fill_xor, "pnl_delta")
    b = _sum_pnl(sh_only, "pnl_shift") - _sum_pnl(ref_only, "pnl_ref")
    pnl_ref = _sum_pnl(common, "pnl_ref") + _sum_pnl(ref_only, "pnl_ref")
    pnl_sh = _sum_pnl(common, "pnl_shift") + _sum_pnl(sh_only, "pnl_shift")
    observed = round(pnl_sh - pnl_ref, 2)
    recon = round(a + b + c, 2)
    residual = round(observed - recon, 2)
    n_union = len(common) + len(ref_only) + len(sh_only)
    n_ref = len(common) + len(ref_only)
    n_sh = len(common) + len(sh_only)
    fill_ref = sum(1 for r in lin if r["class"] != "SHIFT_ONLY" and r["fill_ref"])
    fill_sh = sum(1 for r in lin if r["class"] != "REFERENCE_ONLY" and r["fill_shift"])
    both_reason_match = [r for r in both_fill if str(r.get("exit_reason_ref") or "") == str(r.get("exit_reason_shift") or "")]
    fam_switch = [r for r in both_fill if r.get("exit_family_ref") != r.get("exit_family_shift")]
    trans: Counter[tuple[str, str]] = Counter(
        (str(r.get("exit_family_ref")), str(r.get("exit_family_shift"))) for r in both_fill
    )
    px_deltas = [r["entry_price_delta_bps"] for r in both_fill if r.get("entry_price_delta_bps") is not None]
    fill_cls = Counter(str(r.get("fill_class")) for r in common)
    lat_r = [r["fill_latency_ref"] for r in both_fill if r.get("fill_latency_ref") is not None]
    lat_s = [r["fill_latency_shift"] for r in both_fill if r.get("fill_latency_shift") is not None]
    spr_r = [r["spread_bps_ref"] for r in common if r.get("spread_bps_ref") is not None]
    spr_s = [r["spread_bps_shift"] for r in common if r.get("spread_bps_shift") is not None]
    imb_r = [r["imbalance_ref"] for r in common if r.get("imbalance_ref") is not None]
    imb_s = [r["imbalance_shift"] for r in common if r.get("imbalance_shift") is not None]
    r1_r = [r["ret_1m_bps_ref"] for r in common if r.get("ret_1m_bps_ref") is not None]
    r1_s = [r["ret_1m_bps_shift"] for r in common if r.get("ret_1m_bps_shift") is not None]
    r5_r = [r["ret_5m_bps_ref"] for r in common if r.get("ret_5m_bps_ref") is not None]
    r5_s = [r["ret_5m_bps_shift"] for r in common if r.get("ret_5m_bps_shift") is not None]
    abs_obs = abs(observed) if abs(observed) > 1e-9 else None
    shares = {
        "COMMON_SYMBOL_TIMING_EFFECT": a,
        "SELECTION_REPLACEMENT_EFFECT": b,
        "FILL_CONVERSION_EFFECT": c,
        "residual": residual,
        "share_timing": (abs(a) / abs_obs) if abs_obs else None,
        "share_replacement": (abs(b) / abs_obs) if abs_obs else None,
        "share_fill": (abs(c) / abs_obs) if abs_obs else None,
        "share_residual": (abs(residual) / abs_obs) if abs_obs else None,
    }
    eg_ref = sum(1 for r in both_fill if r.get("exit_family_ref") == "EARLY_GUARD")
    eg_sh = sum(1 for r in both_fill if r.get("exit_family_shift") == "EARLY_GUARD")
    e6_ref = sum(1 for r in both_fill if r.get("exit_family_ref") == "EXIT600")
    e6_sh = sum(1 for r in both_fill if r.get("exit_family_shift") == "EXIT600")
    e7_ref = sum(1 for r in both_fill if r.get("exit_family_ref") == "EXTEND750")
    e7_sh = sum(1 for r in both_fill if r.get("exit_family_shift") == "EXTEND750")
    n_bf = len(both_fill) or None
    return {
        "COMMON_SELECTED_N": len(common),
        "REF_ONLY_N": len(ref_only),
        "SHIFT_ONLY_N": len(sh_only),
        "UNION_N": n_union,
        "COMMON_SELECTED_RATE": (len(common) / n_union) if n_union else None,
        "COMMON_REF_PNL": _sum_pnl(common, "pnl_ref"),
        "COMMON_SHIFT_PNL": _sum_pnl(common, "pnl_shift"),
        "COMMON_PNL_DELTA": round(_sum_pnl(common, "pnl_shift") - _sum_pnl(common, "pnl_ref"), 2),
        "COMMON_TIMING_PNL_DELTA": a,
        "COMMON_FILL_RATE_REF": (sum(1 for r in common if r["fill_ref"]) / len(common)) if common else None,
        "COMMON_FILL_RATE_SHIFT": (sum(1 for r in common if r["fill_shift"]) / len(common)) if common else None,
        "COMMON_FILL_PRICE_DELTA_BPS": mean_finite(px_deltas),
        "COMMON_EXIT_REASON_MATCH_RATE": (len(both_reason_match) / n_bf) if n_bf else None,
        "COMMON_EARLY_GUARD_RATE_REF": (eg_ref / n_bf) if n_bf else None,
        "COMMON_EARLY_GUARD_RATE_SHIFT": (eg_sh / n_bf) if n_bf else None,
        "COMMON_EXIT600_RATE_REF": (e6_ref / n_bf) if n_bf else None,
        "COMMON_EXIT600_RATE_SHIFT": (e6_sh / n_bf) if n_bf else None,
        "COMMON_EXTEND750_RATE_REF": (e7_ref / n_bf) if n_bf else None,
        "COMMON_EXTEND750_RATE_SHIFT": (e7_sh / n_bf) if n_bf else None,
        "REF_ONLY_PNL": _sum_pnl(ref_only, "pnl_ref"),
        "SHIFT_ONLY_PNL": _sum_pnl(sh_only, "pnl_shift"),
        "REPLACEMENT_PNL_DELTA": b,
        "FILL_RATE_REF": (fill_ref / n_ref) if n_ref else None,
        "FILL_RATE_SHIFT": (fill_sh / n_sh) if n_sh else None,
        "FILL_PRICE_DELTA_BPS": mean_finite(px_deltas),
        "EXIT_REASON_SWITCH_RATE": (len(fam_switch) / n_bf) if n_bf else None,
        "EXIT_TIMING_PNL_DELTA": _sum_pnl(fam_switch, "pnl_delta"),
        "BOTH_FILL_N": len(both_fill),
        "fill_class_counts": dict(fill_cls),
        "FILL_LATENCY_REF_SEC": mean_finite(lat_r),
        "FILL_LATENCY_SHIFT_SEC": mean_finite(lat_s),
        "SPREAD_BPS_REF": mean_finite(spr_r),
        "SPREAD_BPS_SHIFT": mean_finite(spr_s),
        "IMBALANCE_REF": mean_finite(imb_r),
        "IMBALANCE_SHIFT": mean_finite(imb_s),
        "RET_1M_BPS_REF": mean_finite(r1_r),
        "RET_1M_BPS_SHIFT": mean_finite(r1_s),
        "RET_5M_BPS_REF": mean_finite(r5_r),
        "RET_5M_BPS_SHIFT": mean_finite(r5_s),
        "microstructure_available": bool(spr_r or spr_s),
        "exit_transition": [{"ref": a, "shift": b, "n": n} for (a, b), n in sorted(trans.items())],
        "TOTAL_SHIFT_MINUS_REFERENCE_PNL": observed,
        "COMMON_SYMBOL_TIMING_EFFECT": a,
        "SELECTION_REPLACEMENT_EFFECT": b,
        "FILL_CONVERSION_EFFECT": c,
        "residual": residual,
        "recon": recon,
        "pnl_ref_selected": pnl_ref,
        "pnl_shift_selected": pnl_sh,
        **shares,
        "n_ref_selected": n_ref,
        "n_shift_selected": n_sh,
    }


def _rank_bucket_0idx(rk: Any) -> Optional[str]:
    if rk is None:
        return None
    try:
        i = int(rk)
    except (TypeError, ValueError):
        return None
    if 0 <= i <= 2:
        return "RANK_1_3"
    if 3 <= i <= 6:
        return "RANK_4_7"
    if 7 <= i <= 9:
        return "RANK_8_10"
    return "RANK_11_PLUS"


def _score_margins(rank_rows: list[dict[str, Any]], shift_key: str, *, days: Optional[Iterable[str]] = None) -> dict[str, Any]:
    by: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    dayset = set(days) if days is not None else None
    for r in rank_rows:
        if str(r.get("shift_key")) != shift_key:
            continue
        day = str(r.get("date") or "")
        if dayset is not None and day not in dayset:
            continue
        rk = r.get("rank")
        sc = _f(r.get("score"))
        if rk is None or sc is None:
            continue
        by[(day, str(r.get("anchor")))][int(rk)] = sc
    m45, m56, m67, cutoff = [], [], [], []
    for scores in by.values():
        if 3 in scores and 4 in scores:
            m45.append(scores[3] - scores[4])
        if 4 in scores and 5 in scores:
            m56.append(scores[4] - scores[5])
            cutoff.append(scores[4] - scores[5])
        if 5 in scores and 6 in scores:
            m67.append(scores[5] - scores[6])
    note = None
    if not cutoff:
        note = "Rank-6 scores unavailable; cutoff margin not computed."
    return {
        "n_anchors_with_scores": len(by),
        "SCORE_MARGIN_RANK45": mean_finite(m45),
        "SCORE_MARGIN_RANK56": mean_finite(m56),
        "SCORE_MARGIN_RANK67": mean_finite(m67),
        "TOP5_CUTOFF_SCORE_MARGIN": mean_finite(cutoff),
        "score_margin_note": note,
    }


def rank_boundary(
    sets: list[dict[str, Any]],
    *,
    rank_rows: Optional[list[dict[str, Any]]] = None,
    shift_key: str = "M5",
    days: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    def _pos(lst: list[str], sym: str) -> Optional[int]:
        try:
            return lst.index(sym)
        except ValueError:
            return None

    swap45 = swap56 = swap67 = 0
    n_pair = 0
    repl_from_47 = 0
    repl_n = 0
    repl_rank_other: list[float] = []
    bucket_counts: Counter[str] = Counter()
    for s in sets:
        t10r, t10s = s["top10_ref"], s["top10_shift"]
        if len(t10r) >= 7 and len(t10s) >= 1:
            n_pair += 1

            def swapped(i: int, j: int) -> bool:
                a, b = t10r[i], t10r[j]
                pa, pb = _pos(t10s, a), _pos(t10s, b)
                if pa is None or pb is None:
                    return True
                return pa > pb

            if swapped(3, 4):
                swap45 += 1
            if len(t10r) >= 6 and swapped(4, 5):
                swap56 += 1
            if swapped(5, 6):
                swap67 += 1
        by_ref = {str(x.get("symbol")): x for x in s["ref_entry_rank_at_shift"]}
        by_sh = {str(x.get("symbol")): x for x in s["shift_entry_rank_at_ref"]}
        for sym in s["ref_only"]:
            repl_n += 1
            rk = by_ref.get(sym, {}).get("rank_shift")
            if rk is not None:
                repl_rank_other.append(float(rk))
                b = _rank_bucket_0idx(rk)
                if b:
                    bucket_counts[b] += 1
                if 3 <= int(rk) <= 6:
                    repl_from_47 += 1
        for sym in s["shift_only"]:
            repl_n += 1
            rk = by_sh.get(sym, {}).get("rank_reference")
            if rk is not None:
                repl_rank_other.append(float(rk))
                b = _rank_bucket_0idx(rk)
                if b:
                    bucket_counts[b] += 1
                if 3 <= int(rk) <= 6:
                    repl_from_47 += 1
    frac = (repl_from_47 / repl_n) if repl_n else None
    if frac is None:
        level = "INSUFFICIENT"
    elif frac >= BOUNDARY_HIGH:
        level = "HIGH"
    elif frac >= BOUNDARY_MED:
        level = "MEDIUM"
    else:
        level = "LOW"
    margins_ref = _score_margins(rank_rows or [], "REFERENCE", days=days)
    margins_sh = _score_margins(rank_rows or [], shift_key, days=days)
    note = margins_ref.get("score_margin_note") or margins_sh.get("score_margin_note")
    if not note:
        note = "Cutoff = 0-index rank4 minus rank5 (1-based rank5 vs rank6). Ranks in prior study are 0-indexed."
    return {
        "RANK45_SWAP_RATE": (swap45 / n_pair) if n_pair else None,
        "RANK56_SWAP_RATE": (swap56 / n_pair) if n_pair else None,
        "RANK67_SWAP_RATE": (swap67 / n_pair) if n_pair else None,
        "REPLACEMENT_FROM_RANK47_RATE": frac,
        "REPLACEMENT_OTHER_SIDE_MEAN_RANK": mean_finite(repl_rank_other),
        "REPLACEMENT_OTHER_SIDE_BUCKETS": dict(bucket_counts),
        "n_anchor_pairs": n_pair,
        "replacement_n": repl_n,
        "TOP5_BOUNDARY_SENSITIVITY": level,
        "TOP5_CUTOFF_SCORE_MARGIN_REF": margins_ref.get("TOP5_CUTOFF_SCORE_MARGIN"),
        "TOP5_CUTOFF_SCORE_MARGIN_SHIFT": margins_sh.get("TOP5_CUTOFF_SCORE_MARGIN"),
        "SCORE_MARGIN_RANK45_REF": margins_ref.get("SCORE_MARGIN_RANK45"),
        "SCORE_MARGIN_RANK56_REF": margins_ref.get("SCORE_MARGIN_RANK56"),
        "SCORE_MARGIN_RANK67_REF": margins_ref.get("SCORE_MARGIN_RANK67"),
        "SCORE_MARGIN_RANK45_SHIFT": margins_sh.get("SCORE_MARGIN_RANK45"),
        "SCORE_MARGIN_RANK56_SHIFT": margins_sh.get("SCORE_MARGIN_RANK56"),
        "SCORE_MARGIN_RANK67_SHIFT": margins_sh.get("SCORE_MARGIN_RANK67"),
        "score_margin_note": note,
        "GLOBAL_RANK_ROBUST_BUT_SELECTION_BOUNDARY_SENSITIVE": bool(level in {"HIGH", "MEDIUM"}),
    }


def portfolio_reentry(port: list[dict[str, Any]], shift: str, *, days: Optional[Iterable[str]] = None) -> dict[str, Any]:
    rows = [r for r in port if str(r.get("shift_key")) == shift and _in_days(r, days)]
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[(str(r.get("date")), str(r.get("symbol")))].append(r)
    lineage = []
    first, re1, re2 = [], [], []
    for (day, sym), grp in sorted(by.items()):
        grp = sorted(grp, key=lambda x: float(_f(x.get("fill_time")) or 0.0))
        prev_exit = None
        prev_reason = None
        for i, t in enumerate(grp):
            kind = "FIRST_ENTRY" if i == 0 else ("REENTRY_1" if i == 1 else "REENTRY_2_PLUS")
            rec = {
                "date": day,
                "period": t.get("period"),
                "symbol": sym,
                "shift_key": shift,
                "entry_index": i + 1,
                "kind": kind,
                "anchor": t.get("anchor") or t.get("anchor_time"),
                "fill_time": _f(t.get("fill_time")),
                "fill_price": _f(t.get("fill_price")),
                "prior_exit_time": prev_exit,
                "prior_exit_reason": prev_reason,
                "time_since_previous_exit": (
                    float(_f(t.get("fill_time")) or 0.0) - float(prev_exit)
                    if prev_exit is not None and _f(t.get("fill_time")) is not None
                    else None
                ),
                "exit_time": _f(t.get("exit_time")),
                "exit_reason": t.get("exit_reason"),
                "pnl": _pnl(t),
            }
            lineage.append(rec)
            if i == 0:
                first.append(t)
            elif i == 1:
                re1.append(t)
            else:
                re2.append(t)
            prev_exit = _f(t.get("exit_time"))
            prev_reason = t.get("exit_reason")
    re_all = re1 + re2
    st_all = trade_stats(rows)
    st_first = trade_stats(first)
    st_re = trade_stats(re_all)
    return {
        "shift_key": shift,
        "all": st_all,
        "FIRST_ENTRY_N": len(first),
        "FIRST_ENTRY_PNL": st_first.get("pnl"),
        "FIRST_ENTRY_PF": st_first.get("PF"),
        "FIRST_ENTRY_WIN_RATE": st_first.get("win_rate"),
        "REENTRY_COUNT": len(re_all),
        "REENTRY_PNL": st_re.get("pnl"),
        "REENTRY_PF": st_re.get("PF"),
        "REENTRY_WIN_RATE": st_re.get("win_rate"),
        "REENTRY_1_N": len(re1),
        "REENTRY_2_PLUS_N": len(re2),
        "FIRST_ENTRY_ONLY_PNL": st_first.get("pnl"),
        "lineage": lineage,
    }


def daily_pnl(rows: list[dict[str, Any]], *, pnl_key: str = "pnl_yen_100") -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[str(r.get("date"))].append(r)
    out = []
    for day in sorted(by):
        tr = by[day]
        pnls = sorted((_pnl(t) if pnl_key == "pnl_yen_100" else float(t.get(pnl_key) or 0.0)) for t in tr)
        # per-trade contributions
        ordered = sorted(tr, key=lambda t: _pnl(t), reverse=True)
        top1 = _pnl(ordered[0]) if ordered else 0.0
        top3 = sum(_pnl(t) for t in ordered[:3])
        worst1 = _pnl(ordered[-1]) if ordered else 0.0
        worst3 = sum(_pnl(t) for t in ordered[-3:])
        tot = round(sum(_pnl(t) for t in tr), 2)
        out.append(
            {
                "date": day,
                "period": tr[0].get("period"),
                "n": len(tr),
                "pnl": tot,
                "top1": round(top1, 2),
                "top3": round(top3, 2),
                "worst1": round(worst1, 2),
                "worst3": round(worst3, 2),
                "top1_share": (top1 / tot) if abs(tot) > 1e-9 else None,
                "top3_share": (top3 / tot) if abs(tot) > 1e-9 else None,
            }
        )
    return out


def loo_and_tail(daily: list[dict[str, Any]], *, trades: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    if not daily:
        return {"n": 0}
    tot = sum(float(d["pnl"]) for d in daily)
    best = max(daily, key=lambda d: float(d["pnl"]))
    worst = min(daily, key=lambda d: float(d["pnl"]))
    loo = []
    signs = []
    base_sign = 1 if tot > 0 else (-1 if tot < 0 else 0)
    for leave in daily:
        sub = [d for d in daily if d["date"] != leave["date"]]
        p = round(sum(float(d["pnl"]) for d in sub), 2)
        sg = 1 if p > 0 else (-1 if p < 0 else 0)
        loo.append({"left_out": leave["date"], "pnl": p, "sign": sg})
        signs.append(sg == base_sign)
    stable_n = sum(1 for x in signs if x)
    abs_tot = abs(tot) if abs(tot) > 1e-9 else None
    best_share = (abs(float(best["pnl"])) / abs_tot) if abs_tot else None
    level = "LOW"
    if (best_share is not None and best_share >= TAIL_DAY_SHARE_HIGH) or stable_n < LOO_STABLE_MIN:
        level = "HIGH"
    elif best_share is not None and best_share >= 0.30:
        level = "MEDIUM"
    def _pf_ex(day: str) -> Any:
        if not trades:
            return None
        sub = [t for t in trades if str(t.get("date")) != day]
        return trade_stats(sub).get("PF")

    return {
        "n_days": len(daily),
        "total_pnl": round(tot, 2),
        "best_day": best["date"],
        "best_day_pnl": best["pnl"],
        "worst_day": worst["date"],
        "worst_day_pnl": worst["pnl"],
        "REMOVE_BEST_DAY_PNL": round(tot - float(best["pnl"]), 2),
        "REMOVE_WORST_DAY_PNL": round(tot - float(worst["pnl"]), 2),
        "REMOVE_BEST_DAY_PF": _pf_ex(str(best["date"])),
        "REMOVE_WORST_DAY_PF": _pf_ex(str(worst["date"])),
        "loo": loo,
        "loo_sign_match_n": stable_n,
        "HOLDOUT_LOO_SIGN_STABILITY": f"{stable_n}/{len(daily)}" if daily else "0/0",
        "TAIL_CONCENTRATION": level,
        "best_day_share_of_abs_total": best_share,
    }


def loo_delta(daily_ref: list[dict[str, Any]], daily_shift: list[dict[str, Any]]) -> dict[str, Any]:
    ref = {str(d["date"]): float(d["pnl"]) for d in daily_ref}
    sh = {str(d["date"]): float(d["pnl"]) for d in daily_shift}
    days = sorted(set(ref) | set(sh))
    if not days:
        return {"n": 0}
    deltas = [{"date": d, "pnl": round(sh.get(d, 0.0) - ref.get(d, 0.0), 2)} for d in days]
    body = loo_and_tail(deltas)
    body["series"] = "SHIFT_MINUS_REFERENCE"
    return body


def classify(
    *,
    iso_all: dict[str, dict[str, Any]],
    iso_h: dict[str, dict[str, Any]],
    iso_d: dict[str, dict[str, Any]],
    port_all: dict[str, dict[str, Any]],
    port_h: dict[str, dict[str, Any]],
    boundary_m5: dict[str, Any],
    tail_iso_h: dict[str, dict[str, Any]],
    tail_port_h: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    def gap(block: dict[str, dict[str, Any]], a: str, b: str, key: str) -> Optional[float]:
        va, vb = block.get(a, {}).get(key), block.get(b, {}).get(key)
        if va is None or vb is None:
            return None
        return float(vb) - float(va)

    # Portfolio first-entry-only vs full (all days): M5 is the dramatic family.
    full_gap_m5 = gap(port_all, "REFERENCE", "M5", "all_pnl")
    fo_gap_m5 = gap(port_all, "REFERENCE", "M5", "first_only_pnl")
    shrink_m5 = None
    if full_gap_m5 is not None and abs(full_gap_m5) > 1e-9 and fo_gap_m5 is not None:
        shrink_m5 = 1.0 - (abs(fo_gap_m5) / abs(full_gap_m5))

    h_iso = iso_h.get("M5") or {}
    shares = [
        ("EXECUTION_TIMING_PRIMARY", float(h_iso.get("share_timing") or 0.0), "COMMON_SYMBOL_TIMING_EFFECT"),
        ("SELECTION_BOUNDARY_PRIMARY", float(h_iso.get("share_replacement") or 0.0), "SELECTION_REPLACEMENT_EFFECT"),
        ("FILL_CONVERSION_PRIMARY", float(h_iso.get("share_fill") or 0.0), "FILL_CONVERSION_EFFECT"),
    ]
    # EXIT is a subset of timing; if switch rate high and timing dominates, EXIT_PATH_TIMING
    exit_switch = float(h_iso.get("EXIT_REASON_SWITCH_RATE") or 0.0)
    both_n = int(h_iso.get("BOTH_FILL_N") or 0)
    timing_is_exit = bool(exit_switch >= 0.40 and both_n >= 8)

    ranked = sorted(shares, key=lambda x: x[1], reverse=True)
    iso_primary = ranked[0][0] if ranked[0][1] >= COMPONENT_DOMINANT else None
    iso_multi = sum(1 for s in shares if s[1] >= COMPONENT_MATERIAL) >= 2
    if timing_is_exit and (ranked[0][0] == "EXECUTION_TIMING_PRIMARY"):
        iso_primary_label = "EXIT_PATH_TIMING_PRIMARY"
    else:
        iso_primary_label = iso_primary or ("MULTIPLE_ECONOMIC_SENSITIVITY_DRIVERS" if iso_multi else None)

    tail_high = (tail_iso_h.get("M5_vs_REF") or tail_iso_h.get("M5") or {}).get("TAIL_CONCENTRATION") == "HIGH" or (
        tail_port_h.get("M5_vs_REF") or tail_port_h.get("M5") or {}
    ).get("TAIL_CONCENTRATION") == "HIGH"
    loo = str((tail_iso_h.get("M5_vs_REF") or tail_iso_h.get("M5") or {}).get("HOLDOUT_LOO_SIGN_STABILITY") or "")
    loo_n = 0
    if "/" in loo:
        try:
            loo_n = int(loo.split("/")[0])
        except ValueError:
            loo_n = 0
    tail_primary = bool(tail_high and loo_n < LOO_STABLE_MIN)

    # Holdout portfolio reentry shrink (interpretation), all-days shrink (user §11 example).
    full_gap_m5_h = gap(port_h, "REFERENCE", "M5", "all_pnl")
    fo_gap_m5_h = gap(port_h, "REFERENCE", "M5", "first_only_pnl")
    shrink_m5_h = None
    if full_gap_m5_h is not None and abs(full_gap_m5_h) > 1e-9 and fo_gap_m5_h is not None:
        shrink_m5_h = 1.0 - (abs(fo_gap_m5_h) / abs(full_gap_m5_h))

    boundary_high = str(boundary_m5.get("TOP5_BOUNDARY_SENSITIVITY")) == "HIGH"
    reentry_primary = bool(shrink_m5 is not None and shrink_m5 >= REENTRY_SHRINK_PRIMARY)

    drivers = []
    if reentry_primary:
        drivers.append("REENTRY_STATE_INTERACTION_PRIMARY")
    if iso_primary_label:
        drivers.append(iso_primary_label)
    if tail_primary:
        drivers.append("TAIL_CONCENTRATION_PRIMARY")
    if boundary_high and "SELECTION_BOUNDARY_PRIMARY" not in drivers:
        drivers.append("SELECTION_BOUNDARY_PRIMARY")
    # unique preserve order
    seen = []
    for d in drivers:
        if d not in seen:
            seen.append(d)
    drivers = seen

    if not drivers:
        primary = "NO_CLEAR_PRIMARY_DRIVER"
        secondary = None
        family = "NO_CLEAR_PRIMARY_DRIVER"
    elif len(drivers) == 1:
        primary = drivers[0]
        secondary = ranked[1][0] if ranked[1][1] >= COMPONENT_MATERIAL else None
        family = primary
    else:
        primary = drivers[0]
        secondary = drivers[1]
        family = "MULTIPLE_ECONOMIC_SENSITIVITY_DRIVERS"

    residual_ok = float(h_iso.get("share_residual") or 0.0) <= RESIDUAL_FAIL
    # Isolated holdout recon quality
    decomp_ok = residual_ok and primary != "NO_CLEAR_PRIMARY_DRIVER"
    verdict = VERDICT_OK if decomp_ok else VERDICT_FAIL

    # Rank robustness restated from prior study (not recomputed as a new search).
    rank_rob = "STRONG"
    econ_rob = "WEAK"
    overfit = "LOW"
    if reentry_primary and not (iso_primary_label in {"EXECUTION_TIMING_PRIMARY", "EXIT_PATH_TIMING_PRIMARY"} and float(h_iso.get("share_timing") or 0) >= COMPONENT_DOMINANT):
        overfit = "LOW"
    elif iso_primary_label in {"EXECUTION_TIMING_PRIMARY", "EXIT_PATH_TIMING_PRIMARY"} and float(h_iso.get("share_timing") or 0) >= COMPONENT_DOMINANT:
        overfit = "MEDIUM"
    if tail_primary:
        overfit = "MEDIUM"

    return {
        "PRIMARY_ROOT_CAUSE": primary,
        "SECONDARY_ROOT_CAUSE": secondary,
        "driver_family": family,
        "CROSS_SECTIONAL_RANK_ROBUSTNESS": rank_rob,
        "ECONOMIC_TIMING_ROBUSTNESS": econ_rob,
        "EXACT_CLOCK_OVERFIT_EVIDENCE": overfit,
        "verdict": verdict,
        "reentry_shrink_m5": shrink_m5,
        "reentry_shrink_m5_holdout": shrink_m5_h,
        "full_gap_m5": full_gap_m5,
        "first_only_gap_m5": fo_gap_m5,
        "full_gap_m5_holdout": full_gap_m5_h,
        "first_only_gap_m5_holdout": fo_gap_m5_h,
        "holdout_iso_shares_m5": {
            "timing": h_iso.get("share_timing"),
            "replacement": h_iso.get("share_replacement"),
            "fill": h_iso.get("share_fill"),
            "residual": h_iso.get("share_residual"),
        },
        "iso_primary_label": iso_primary_label,
        "tail_primary": tail_primary,
        "reentry_primary": reentry_primary,
        "drivers": drivers,
        "decomp_ok": decomp_ok,
        "thresholds": {
            "REENTRY_SHRINK_PRIMARY": REENTRY_SHRINK_PRIMARY,
            "COMPONENT_DOMINANT": COMPONENT_DOMINANT,
            "COMPONENT_MATERIAL": COMPONENT_MATERIAL,
            "RESIDUAL_OK": RESIDUAL_OK,
            "RESIDUAL_FAIL": RESIDUAL_FAIL,
            "BOUNDARY_HIGH": BOUNDARY_HIGH,
            "LOO_STABLE_MIN": LOO_STABLE_MIN,
        },
    }


def run_all(
    *,
    comparisons: list[dict[str, Any]],
    iso: list[dict[str, Any]],
    port: list[dict[str, Any]],
    rank_rows: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    idx = iso_index(iso)
    rank_rows = rank_rows or []
    periods = {
        "ALL": None,
        "DEVELOPMENT": DEVELOPMENT_DAYS,
        "POST_FREEZE_HOLDOUT": HOLDOUT_DAYS,
    }
    lineage_by: dict[str, dict[str, list[dict[str, Any]]]] = {}
    iso_decomp: dict[str, dict[str, dict[str, Any]]] = {}
    boundary: dict[str, dict[str, Any]] = {}
    for pname, days in periods.items():
        lineage_by[pname] = {}
        iso_decomp[pname] = {}
        for sh in ALL_SHIFTS:
            sets = selected_sets(comparisons, sh, days=days)
            lin = lineage_rows(sets, idx, sh)
            lineage_by[pname][sh] = lin
            iso_decomp[pname][sh] = isolated_decomp(lin)
        boundary[pname] = {
            sh: rank_boundary(
                selected_sets(comparisons, sh, days=days),
                rank_rows=rank_rows,
                shift_key=sh,
                days=days,
            )
            for sh in PRIMARY_SHIFTS
        }

    port_stats: dict[str, dict[str, dict[str, Any]]] = {}
    port_lineage: dict[str, dict[str, list]] = {}
    for pname, days in periods.items():
        port_stats[pname] = {}
        port_lineage[pname] = {}
        for sh in ("REFERENCE",) + ALL_SHIFTS:
            pr = portfolio_reentry(port, sh, days=days)
            port_lineage[pname][sh] = pr.pop("lineage")
            pr["all_pnl"] = (pr.get("all") or {}).get("pnl")
            pr["first_only_pnl"] = pr.get("FIRST_ENTRY_ONLY_PNL")
            port_stats[pname][sh] = pr

    def _iso_filled(days, shift):
        return [
            r
            for r in iso
            if str(r.get("shift_key")) == shift and _bool(r.get("independent_filled")) and _in_days(r, days)
        ]

    tail_iso = {}
    tail_port = {}
    daily_iso = {}
    daily_port = {}
    iso_filled_h = {}
    port_h_rows = {}
    for sh in ("REFERENCE", "M5", "P5"):
        iso_filled_h[sh] = _iso_filled(HOLDOUT_DAYS, sh)
        port_h_rows[sh] = [r for r in port if str(r.get("shift_key")) == sh and _in_days(r, HOLDOUT_DAYS)]
        d_iso = daily_pnl(iso_filled_h[sh])
        d_port = daily_pnl(port_h_rows[sh])
        daily_iso[sh] = d_iso
        daily_port[sh] = d_port
        tail_iso[sh] = loo_and_tail(d_iso, trades=iso_filled_h[sh])
        tail_port[sh] = loo_and_tail(d_port, trades=port_h_rows[sh])
    tail_iso["M5_vs_REF"] = loo_delta(daily_iso["REFERENCE"], daily_iso["M5"])
    tail_iso["P5_vs_REF"] = loo_delta(daily_iso["REFERENCE"], daily_iso["P5"])
    tail_port["M5_vs_REF"] = loo_delta(daily_port["REFERENCE"], daily_port["M5"])
    tail_port["P5_vs_REF"] = loo_delta(daily_port["REFERENCE"], daily_port["P5"])

    clf = classify(
        iso_all=iso_decomp["ALL"],
        iso_h=iso_decomp["POST_FREEZE_HOLDOUT"],
        iso_d=iso_decomp["DEVELOPMENT"],
        port_all=port_stats["ALL"],
        port_h=port_stats["POST_FREEZE_HOLDOUT"],
        boundary_m5=boundary["ALL"]["M5"],
        tail_iso_h=tail_iso,
        tail_port_h=tail_port,
    )

    reentry_contrib = None
    if clf.get("full_gap_m5") is not None and clf.get("first_only_gap_m5") is not None:
        reentry_contrib = round(float(clf["full_gap_m5"]) - float(clf["first_only_gap_m5"]), 2)

    return {
        "lineage": lineage_by,
        "iso_decomp": iso_decomp,
        "boundary": boundary,
        "port_stats": port_stats,
        "port_lineage": port_lineage,
        "daily_iso_holdout": daily_iso,
        "daily_port_holdout": daily_port,
        "tail_iso_holdout": tail_iso,
        "tail_port_holdout": tail_port,
        "classify": clf,
        "REENTRY_INTERACTION_CONTRIBUTION": reentry_contrib,
    }
