"""PTF post-BE transition comparison and verdict."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Optional

from research.simple_tech_redesign.exit_composition_shift_rca_analyze import load_residual_rows
from research.simple_tech_redesign.exit_residual_rca_spec import YEN_PARITY_TOL
from research.simple_tech_redesign.ptf_post_be_rca_spec import (
    COMPARE_PROVEN,
    COMPARE_PROTECTED,
    DEV_BE_N_EXPECTED,
    FWD_BE_N_EXPECTED,
    PRIMARY_DIAGNOSTIC,
    PTF_TOP_DAY,
    PTF_TOP_SYMBOL,
    SOURCE_COMPOSITION_VERDICT,
)

EPS = 1e-9
MIN_SEP = 0.15
FWD_PTF_MIN = 3


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def be_rows_from_residual() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dev, fwd, _ = load_residual_rows()
    dev_be = [r for r in dev if r.get("break_even_reached")]
    fwd_be = [r for r in fwd if r.get("break_even_reached")]
    return dev_be, fwd_be


def assert_be_identity(dev_be: list[dict[str, Any]], fwd_be: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(dev_be) == int(DEV_BE_N_EXPECTED), f"DEV BE n={len(dev_be)} expected {DEV_BE_N_EXPECTED}"
    assert len(fwd_be) == int(FWD_BE_N_EXPECTED), f"FWD BE n={len(fwd_be)} expected {FWD_BE_N_EXPECTED}"
    for rows in (dev_be, fwd_be):
        for r in rows:
            assert r.get("break_even_reached"), "non-BE in BE population"
            assert _f(r.get("first_break_even_time")) is not None, "missing first_break_even_time"
            assert str(r.get("residual_class") or "") != "U_EARLY_NEVER_BE"
    return {"dev_be_n": len(dev_be), "fwd_be_n": len(fwd_be), "ok": True}


def merge_harvest(be_rows: list[dict[str, Any]], harvested: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(r.get("trade_id") or ""): r for r in harvested}
    out = []
    for r in be_rows:
        tid = str(r.get("trade_id") or "")
        h = dict(by_id.get(tid) or {})
        rec = dict(r)
        rec.update({k: v for k, v in h.items() if k not in rec or k.startswith("post_be")})
        out.append(rec)
    return out


def _rate(rows: list[dict[str, Any]], pred) -> float:
    if not rows:
        return 0.0
    return sum(1 for r in rows if pred(r)) / len(rows)


def _class_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    c = Counter(str(r.get("residual_class") or "") for r in rows)
    return dict(c)


def _post(r: dict[str, Any]) -> dict[str, Any]:
    return dict(r.get("post_be") or {})


def _seq_a(r: dict[str, Any]) -> dict[str, Any]:
    return dict(_post(r).get("sequence_a") or {})


def _seq_b(r: dict[str, Any]) -> dict[str, Any]:
    return dict(_post(r).get("sequence_b") or {})


def _seq_c(r: dict[str, Any]) -> dict[str, Any]:
    return dict(_post(r).get("sequence_c") or {})


def compare_groups(rows: list[dict[str, Any]], *, label_a: str, label_b: str) -> dict[str, Any]:
    ga = [r for r in rows if str(r.get("residual_class") or "") == label_a]
    gb = [r for r in rows if str(r.get("residual_class") or "") == label_b]
    metrics = {}
    for key, fn in (
        ("had_below_be", lambda r: bool(_seq_a(r).get("had_below_be"))),
        ("no_reclaim_after_below", lambda r: bool(_seq_a(r).get("no_reclaim_after_below"))),
        ("had_structure_loss", lambda r: bool(_seq_b(r).get("had_structure_loss"))),
        ("no_recover_after_loss", lambda r: bool(_seq_b(r).get("no_recover_after_loss"))),
        ("reclaim_then_reloss", lambda r: bool(_seq_c(r).get("reclaim_then_reloss"))),
        ("k6_reached", lambda r: bool(dict(_post(r).get("k6_post_be") or {}).get("k6_reached"))),
        ("v29_A_RECLAIM_THEN_RELOSS", lambda r: str(_post(r).get("v29_family_a") or "") == "A_RECLAIM_THEN_RELOSS"),
        ("first_order_BELOW_BE_FIRST", lambda r: str(_post(r).get("sequence_d_first_order") or "") == "BELOW_BE_FIRST"),
        ("first_order_STRUCTURE_FIRST", lambda r: str(_post(r).get("sequence_d_first_order") or "") == "STRUCTURE_LOSS_FIRST"),
    ):
        ra = _rate(ga, fn)
        rb = _rate(gb, fn)
        metrics[key] = {"a_n": len(ga), "b_n": len(gb), "a_rate": ra, "b_rate": rb, "delta": ra - rb}
    return {"group_a": label_a, "group_b": label_b, "metrics": metrics}


def protected_pool(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("residual_class") or "") in COMPARE_PROTECTED]


def cohort_pack(rows: list[dict[str, Any]], *, cohort: str) -> dict[str, Any]:
    ptf = [r for r in rows if str(r.get("residual_class") or "") == PRIMARY_DIAGNOSTIC]
    p_early = [r for r in rows if str(r.get("residual_class") or "") == COMPARE_PROVEN]
    prot = protected_pool(rows)
    good = [r for r in rows if str(r.get("residual_class") or "") == "PROTECTED_GOOD"]
    dip = [r for r in rows if str(r.get("residual_class") or "") == "PROTECTED_DIP"]
    return {
        "cohort": cohort,
        "be_n": len(rows),
        "class_counts": _class_counts(rows),
        "ptf_n": len(ptf),
        "p_early_n": len(p_early),
        "protected_n": len(prot),
        "good_n": len(good),
        "dip_n": len(dip),
        "ptf_vs_protected": compare_groups(rows, label_a=PRIMARY_DIAGNOSTIC, label_b="PROTECTED_GOOD")
        if good
        else compare_groups(rows, label_a=PRIMARY_DIAGNOSTIC, label_b="PROTECTED_DIP"),
        "ptf_vs_good": compare_groups(rows, label_a=PRIMARY_DIAGNOSTIC, label_b="PROTECTED_GOOD"),
        "ptf_vs_dip": compare_groups(rows, label_a=PRIMARY_DIAGNOSTIC, label_b="PROTECTED_DIP"),
        "ptf_vs_p_early": compare_groups(rows, label_a=PRIMARY_DIAGNOSTIC, label_b=COMPARE_PROVEN),
        "role": {
            role: {
                "be_n": len([r for r in rows if str(r.get("fill_role") or "") == role]),
                "ptf_n": len([r for r in ptf if str(r.get("fill_role") or "") == role]),
                "ptf_vs_protected": compare_groups(
                    [r for r in rows if str(r.get("fill_role") or "") == role],
                    label_a=PRIMARY_DIAGNOSTIC,
                    label_b="PROTECTED_GOOD",
                ),
            }
            for role in ("CORE", "ADDED")
        },
        "event_inventory": _event_inventory(rows),
        "ptf_concentration": _ptf_concentration(ptf, cohort=cohort),
        "loo": _loo(ptf, cohort=cohort),
    }


def _event_inventory(rows: list[dict[str, Any]]) -> dict[str, Any]:
    inv: dict[str, float] = defaultdict(float)
    n_ok = 0
    for r in rows:
        if not r.get("post_be_ok"):
            continue
        n_ok += 1
        pb = _post(r)
        for kind, rec in dict(pb.get("structure_inventory") or {}).items():
            if kind.startswith("A_") or kind.startswith("D_") or kind.startswith("E_") or kind == "TREND_LOST_1M":
                if isinstance(rec, dict) and rec.get("hit"):
                    inv[str(kind)] += 1.0
    return {k: (v / n_ok if n_ok else 0.0) for k, v in inv.items()}


def _ptf_concentration(ptf: list[dict[str, Any]], *, cohort: str) -> dict[str, Any]:
    loss_by_day: dict[str, float] = defaultdict(float)
    loss_by_sym: dict[str, float] = defaultdict(float)
    gross = 0.0
    for r in ptf:
        g = float(_f(r.get("below_be_terminal_loss")) or 0.0)
        gross += g
        loss_by_day[str(r.get("date") or "")] += g
        loss_by_sym[str(r.get("symbol") or "")] += g
    top_day = PTF_TOP_DAY.get(cohort, "")
    top_sym = PTF_TOP_SYMBOL.get(cohort, "")
    return {
        "gross_loss": gross,
        "top_day": top_day,
        "top_day_share": (loss_by_day.get(top_day, 0.0) / gross) if gross > EPS else None,
        "top_symbol": top_sym,
        "top_symbol_share": (loss_by_sym.get(top_sym, 0.0) / gross) if gross > EPS else None,
    }


def _loo(ptf: list[dict[str, Any]], *, cohort: str) -> dict[str, Any]:
    top_day = PTF_TOP_DAY.get(cohort, "")
    top_sym = PTF_TOP_SYMBOL.get(cohort, "")
    full = _ptf_metric_rates(ptf)
    day_ex = _ptf_metric_rates([r for r in ptf if str(r.get("date") or "") != top_day])
    sym_ex = _ptf_metric_rates([r for r in ptf if str(r.get("symbol") or "") != top_sym])
    return {"full": full, "leave_one_top_day": day_ex, "leave_one_top_symbol": sym_ex}


def _ptf_metric_rates(ptf: list[dict[str, Any]]) -> dict[str, float]:
    if not ptf:
        return {}
    return {
        "n": float(len(ptf)),
        "had_below_be": _rate(ptf, lambda r: bool(_seq_a(r).get("had_below_be"))),
        "first_order_BELOW_BE_FIRST": _rate(
            ptf, lambda r: str(_post(r).get("sequence_d_first_order") or "") == "BELOW_BE_FIRST"
        ),
        "no_reclaim_after_below": _rate(ptf, lambda r: bool(_seq_a(r).get("no_reclaim_after_below"))),
        "no_recover_after_loss": _rate(ptf, lambda r: bool(_seq_b(r).get("no_recover_after_loss"))),
        "reclaim_then_reloss": _rate(ptf, lambda r: bool(_seq_c(r).get("reclaim_then_reloss"))),
    }


def _direction_ok(dev_delta: float, fwd_delta: float) -> bool:
    if abs(dev_delta) < MIN_SEP and abs(fwd_delta) < MIN_SEP:
        return True
    return (dev_delta > 0 and fwd_delta > 0) or (dev_delta < 0 and fwd_delta < 0)


def _closed_overlap(dev: dict[str, Any], fwd: dict[str, Any]) -> list[str]:
    hits = []
    for pack in (dev, fwd):
        ptf_n = int(pack.get("ptf_n") or 0)
        if ptf_n <= 0:
            continue
        ptf_rows_rate = float(dict(pack.get("ptf_vs_p_early", {}).get("metrics", {}).get("v29_A_RECLAIM_THEN_RELOSS") or {}).get("a_rate") or 0.0)
        k6_rate = float(dict(pack.get("ptf_vs_p_early", {}).get("metrics", {}).get("k6_reached") or {}).get("a_rate") or 0.0)
        if ptf_rows_rate >= 0.40:
            hits.append("V29_TERMINAL_SEQUENCE")
        if k6_rate >= 0.65:
            hits.append("V28_K6_PERSISTENCE_EXIT")
    if "V28_K6_PERSISTENCE_EXIT" in hits or "V29_TERMINAL_SEQUENCE" in hits:
        hits.append("V27_PERSISTENCE_3M_EMA_K6")
    return sorted(set(hits))


def _ptf_similar_to(rows_cmp: dict[str, Any], *, keys: tuple[str, ...]) -> bool:
    metrics = dict(rows_cmp.get("metrics") or {})
    return all(abs(float(dict(metrics.get(k) or {}).get("delta") or 0.0)) < MIN_SEP for k in keys)


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, harvested_ok: bool) -> dict[str, Any]:
    dev_ptf_n = int(dev.get("ptf_n") or 0)
    fwd_ptf_n = int(fwd.get("ptf_n") or 0)
    if not harvested_ok or dev_ptf_n == 0:
        return _decision("E", "SIMPLE_TECH_PTF_POST_BE_INSUFFICIENT", None, "Harvest incomplete or zero PTF.")

    if fwd_ptf_n < FWD_PTF_MIN:
        return _decision("E", "SIMPLE_TECH_PTF_POST_BE_INSUFFICIENT", None, "FWD PTF n below formal direction threshold.")

    good_metrics = dev.get("ptf_vs_good", {}).get("metrics", {})
    dip_metrics = dev.get("ptf_vs_dip", {}).get("metrics", {})
    pearly_metrics = dev.get("ptf_vs_p_early", {}).get("metrics", {})
    fwd_good = fwd.get("ptf_vs_good", {}).get("metrics", {})
    fwd_pearly = fwd.get("ptf_vs_p_early", {}).get("metrics", {})

    closed = _closed_overlap(dev, fwd)
    if closed and "V29_TERMINAL_SEQUENCE" in closed:
        return _decision(
            "C",
            "SIMPLE_TECH_PTF_MECHANISM_ALREADY_CLOSED",
            "V29_EMA_RECLAIM_RELOSS",
            "ALREADY_TESTED_AND_CLOSED: V29 EMA reclaim-then-reloss family.",
            closed=closed,
        )
    if closed and "V28_K6_PERSISTENCE_EXIT" in closed and float(dict(good_metrics.get("k6_reached") or {}).get("delta") or 0.0) < MIN_SEP:
        return _decision(
            "C",
            "SIMPLE_TECH_PTF_MECHANISM_ALREADY_CLOSED",
            "V28_K6_PERSISTENCE_EXIT",
            "ALREADY_TESTED_AND_CLOSED: V28 K6 persistence exit family.",
            closed=closed,
        )

    shared_keys = (
        "had_below_be",
        "no_reclaim_after_below",
        "no_recover_after_loss",
        "first_order_BELOW_BE_FIRST",
        "reclaim_then_reloss",
    )
    shared_with_p_early = _ptf_similar_to(dev.get("ptf_vs_p_early", {}), keys=shared_keys)

    below_good_dev = float(dict(good_metrics.get("had_below_be") or {}).get("delta") or 0.0)
    below_good_fwd = float(dict(fwd_good.get("had_below_be") or {}).get("delta") or 0.0)
    order_good_dev = float(dict(good_metrics.get("first_order_BELOW_BE_FIRST") or {}).get("delta") or 0.0)
    order_good_fwd = float(dict(fwd_good.get("first_order_BELOW_BE_FIRST") or {}).get("delta") or 0.0)
    norecover_good_dev = float(dict(good_metrics.get("no_recover_after_loss") or {}).get("delta") or 0.0)
    norecover_dip_dev = float(dict(dip_metrics.get("no_recover_after_loss") or {}).get("delta") or 0.0)

    loo = dev.get("loo", {})
    loo_day = dict(loo.get("leave_one_top_day") or {})
    loo_sym = dict(loo.get("leave_one_top_symbol") or {})
    loo_holds = True
    for key in ("had_below_be", "first_order_BELOW_BE_FIRST"):
        full_v = float(dict(loo.get("full") or {}).get(key) or 0.0)
        if full_v >= MIN_SEP:
            if float(loo_day.get(key) or 0.0) < MIN_SEP and float(loo_sym.get(key) or 0.0) < MIN_SEP:
                loo_holds = False

    economic_distinct_from_protected = (
        abs(below_good_dev) >= MIN_SEP
        and abs(order_good_dev) >= MIN_SEP
        and _direction_ok(below_good_dev, below_good_fwd)
        and _direction_ok(order_good_dev, order_good_fwd)
        and loo_holds
    )
    technical_distinct = abs(norecover_good_dev) >= MIN_SEP or abs(norecover_dip_dev) >= MIN_SEP

    if shared_with_p_early:
        return _decision(
            "B",
            "SIMPLE_TECH_PROVEN_FAILURE_SHARED_MECHANISM_FOUND",
            "PROVEN_FAILURE_POST_BE_BELOW_BE_THEN_SESSION_GIVEBACK",
            "PTF post-BE transition matches P_EARLY; not separable as independent mechanism.",
        )

    if economic_distinct_from_protected and technical_distinct:
        return _decision(
            "A",
            "SIMPLE_TECH_PTF_DISTINCT_POST_BE_MECHANISM_FOUND",
            "PTF_POST_BE_BELOW_BE_BEFORE_STRUCTURE",
            "PTF-specific post-BE economic ordering separates from protected winners.",
        )

    return _decision(
        "D",
        "SIMPLE_TECH_PTF_POST_BE_MECHANISM_NOT_FOUND",
        None,
        "PTF damage stable but causal technical transitions do not separate from protected winners; not independent from P_EARLY either.",
    )


def _decision(
    case: str,
    verdict: str,
    mechanism: Optional[str],
    nxt: str,
    *,
    closed: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "PRIMARY_NEXT_MECHANISM": mechanism,
        "PRIMARY_NEXT_EXIT_TARGET": None,
        "NEW_EXIT_RULE": False,
        "CANDIDATE_FROZEN": False,
        "ALREADY_CLOSED_OVERLAP": closed or [],
        "NARRATIVE_CORRECTION": (
            "Prior composition RCA: role-standardization residual does not prove or disprove "
            "genuine lifecycle shift; concentration was primary driver for rank reversal."
        ),
    }


def sequence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        pb = _post(r)
        out.append(
            {
                "trade_id": r.get("trade_id"),
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "fill_role": r.get("fill_role"),
                "residual_class": r.get("residual_class"),
                "path_type": r.get("path_type"),
                "first_BE_time": r.get("first_break_even_time"),
                "first_positive_executable_time": pb.get("first_positive_executable_time"),
                "first_below_BE_time": pb.get("first_below_be_time"),
                "first_BE_reclaim_time": pb.get("first_be_reclaim_time"),
                "first_structure_loss_type": pb.get("primary_structure_kind"),
                "first_structure_loss_time": pb.get("first_structure_loss_time"),
                "first_structure_recover_time": pb.get("first_structure_recover_time"),
                "second_structure_loss_time": pb.get("second_structure_loss_time"),
                "sequence_d_first_order": pb.get("sequence_d_first_order"),
                "v29_family_a": pb.get("v29_family_a"),
                "session_close_pnl": r.get("session_close_pnl"),
                "peak_to_close_giveback": r.get("peak_to_close_giveback"),
                "below_be_terminal_loss": r.get("below_be_terminal_loss"),
            }
        )
    return out
