"""Proven failure second-BE-loss actionability analysis and verdict."""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any, Callable, Optional

from research.simple_tech_redesign.exit_composition_shift_rca_analyze import load_residual_rows
from research.simple_tech_redesign.proven_failure_actionability_gate_spec import (
    DEV_BE_N_EXPECTED,
    DEV_CLASS_N,
    FAILURE_CLASSES,
    FWD_BE_N_EXPECTED,
    FWD_CLASS_N,
    MIN_EVENT_N,
    MIN_RATE_SEP,
    P_EARLY_TOP_DAY,
    P_EARLY_TOP_SYMBOL,
    P_FAMILY,
    PROTECTED_CLASSES,
    PTF_TOP_DAY,
    PTF_TOP_SYMBOL,
    SOURCE_PTF_VERDICT,
)

EPS = 1e-9


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


def _iqr(xs: list[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    s = sorted(xs)
    n = len(s)
    return float(s[(3 * n) // 4] - s[n // 4])


def be_rows_from_residual() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dev, fwd, _ = load_residual_rows()
    return [r for r in dev if r.get("break_even_reached")], [r for r in fwd if r.get("break_even_reached")]


def assert_identity(dev_be: list[dict[str, Any]], fwd_be: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(dev_be) == DEV_BE_N_EXPECTED
    assert len(fwd_be) == FWD_BE_N_EXPECTED
    dev_c = Counter(str(r.get("residual_class") or "") for r in dev_be)
    fwd_c = Counter(str(r.get("residual_class") or "") for r in fwd_be)
    for k, v in DEV_CLASS_N.items():
        assert int(dev_c.get(k, 0)) == int(v), f"DEV {k} got {dev_c.get(k)} exp {v}"
    for k, v in FWD_CLASS_N.items():
        assert int(fwd_c.get(k, 0)) == int(v), f"FWD {k} got {fwd_c.get(k)} exp {v}"
    return {"dev_be_n": len(dev_be), "fwd_be_n": len(fwd_be), "dev_class": dict(dev_c), "fwd_class": dict(fwd_c)}


def merge_harvest(be_rows: list[dict[str, Any]], harvested: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(r.get("trade_id") or ""): r for r in harvested}
    out = []
    for r in be_rows:
        h = dict(by_id.get(str(r.get("trade_id") or "")) or {})
        rec = dict(r)
        for k, v in h.items():
            if k in ("economic_sequence", "actionability_ok") or k.startswith("first_") or k.startswith("second_"):
                rec[k] = v
        out.append(rec)
    return out


def _seq(r: dict[str, Any]) -> dict[str, Any]:
    return dict(r.get("economic_sequence") or {})


def _label_group(residual_class: str) -> str:
    c = str(residual_class or "")
    if c in FAILURE_CLASSES:
        return c
    if c in PROTECTED_CLASSES:
        return c
    if c in FAILURE_CLASSES:
        return c
    return c


def _pool(rows: list[dict[str, Any]], pool: str) -> list[dict[str, Any]]:
    if pool == P_FAMILY:
        return [r for r in rows if str(r.get("residual_class") or "") in FAILURE_CLASSES]
    if pool == "FAILURE":
        return [r for r in rows if str(r.get("residual_class") or "") in FAILURE_CLASSES]
    if pool == "PROTECTED":
        return [r for r in rows if str(r.get("residual_class") or "") in PROTECTED_CLASSES]
    return [r for r in rows if str(r.get("residual_class") or "") == pool]


def _rate(rows: list[dict[str, Any]], pred: Callable[[dict[str, Any]], bool]) -> float:
    if not rows:
        return 0.0
    return sum(1 for r in rows if pred(r)) / len(rows)


def _event_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    first_below = [r for r in rows if _seq(r).get("had_first_below_be")]
    reclaim = [r for r in rows if _seq(r).get("had_first_reclaim")]
    second = [r for r in rows if _seq(r).get("had_second_below_be_after_reclaim")]
    be_to_below = [float(_seq(r)["be_to_first_below_sec"]) for r in rows if _f(_seq(r).get("be_to_first_below_sec")) is not None]
    below_to_rec = [
        float(_seq(r)["first_below_to_reclaim_sec"])
        for r in rows
        if _f(_seq(r).get("first_below_to_reclaim_sec")) is not None
    ]
    rec_to_second = [
        float(_seq(r)["reclaim_to_second_below_sec"])
        for r in rows
        if _f(_seq(r).get("reclaim_to_second_below_sec")) is not None
    ]
    cross_ab = [int(_seq(r).get("above_to_below_crossing_n") or 0) for r in rows]
    cross_ba = [int(_seq(r).get("below_to_above_crossing_n") or 0) for r in rows]
    jitter_n = sum(1 for r in rows if _seq(r).get("micro_jitter_warn"))
    fired = second
    not_fired = [r for r in rows if not _seq(r).get("had_second_below_be_after_reclaim")]
    return {
        "n": n,
        "first_below_n": len(first_below),
        "first_below_rate": len(first_below) / n if n else 0.0,
        "reclaim_n": len(reclaim),
        "reclaim_rate": len(reclaim) / n if n else 0.0,
        "second_below_after_reclaim_n": len(second),
        "second_below_after_reclaim_rate": len(second) / n if n else 0.0,
        "median_be_to_first_below_sec": median(be_to_below) if be_to_below else None,
        "iqr_be_to_first_below_sec": _iqr(be_to_below),
        "median_first_below_to_reclaim_sec": median(below_to_rec) if below_to_rec else None,
        "iqr_first_below_to_reclaim_sec": _iqr(below_to_rec),
        "median_reclaim_to_second_below_sec": median(rec_to_second) if rec_to_second else None,
        "iqr_reclaim_to_second_below_sec": _iqr(rec_to_second),
        "median_above_to_below_crossing_n": median(cross_ab) if cross_ab else None,
        "median_below_to_above_crossing_n": median(cross_ba) if cross_ba else None,
        "micro_jitter_warn_n": jitter_n,
        "overlay_fired": _overlay(fired),
        "overlay_not_fired": _overlay(not_fired),
    }


def _overlay(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    pnls = [float(_f(r.get("session_close_pnl")) or 0.0) for r in rows]
    gross = [float(_f(r.get("below_be_terminal_loss")) or 0.0) for r in rows]
    gb = [float(_f(r.get("peak_to_close_giveback")) or 0.0) for r in rows]
    return {
        "n": len(rows),
        "session_close_pnl_total": sum(pnls),
        "median_session_close_pnl": median(pnls),
        "gross_loss_total": sum(gross),
        "median_gross_loss": median(gross),
        "giveback_total": sum(gb),
        "median_giveback": median(gb),
    }


def _loo_second_rate(rows: list[dict[str, Any]], *, exclude_day: str | None, exclude_sym: str | None) -> float:
    filt = rows
    if exclude_day:
        filt = [r for r in filt if str(r.get("date") or "") != exclude_day]
    if exclude_sym:
        filt = [r for r in filt if str(r.get("symbol") or "") != exclude_sym]
    return _rate(filt, lambda r: bool(_seq(r).get("had_second_below_be_after_reclaim")))


def cohort_pack(rows: list[dict[str, Any]], *, cohort: str) -> dict[str, Any]:
    pools = ("P_PROFIT_THEN_FAILURE", "P_EARLY_AFTER_BE", "PROTECTED_DIP", "PROTECTED_GOOD", "FAILURE", "PROTECTED", P_FAMILY)
    by_pool = {p: _event_pack(_pool(rows, p)) for p in pools}
    failure = by_pool["FAILURE"]
    protected = by_pool["PROTECTED"]
    dip = by_pool["PROTECTED_DIP"]
    ptf = _pool(rows, "P_PROFIT_THEN_FAILURE")
    p_early = _pool(rows, "P_EARLY_AFTER_BE")
    return {
        "cohort": cohort,
        "be_n": len(rows),
        "by_pool": by_pool,
        "failure_vs_protected_delta": {
            "second_below_after_reclaim_rate": float(failure["second_below_after_reclaim_rate"])
            - float(protected["second_below_after_reclaim_rate"]),
            "first_below_rate": float(failure["first_below_rate"]) - float(protected["first_below_rate"]),
            "reclaim_rate": float(failure["reclaim_rate"]) - float(protected["reclaim_rate"]),
        },
        "role": {
            role: {
                "FAILURE": _event_pack([r for r in _pool(rows, "FAILURE") if str(r.get("fill_role") or "") == role]),
                "PROTECTED": _event_pack([r for r in _pool(rows, "PROTECTED") if str(r.get("fill_role") or "") == role]),
            }
            for role in ("CORE", "ADDED")
        },
        "loo": {
            "PTF": {
                "full": _loo_second_rate(ptf, exclude_day=None, exclude_sym=None),
                "leave_one_top_day": _loo_second_rate(ptf, exclude_day=PTF_TOP_DAY.get(cohort), exclude_sym=None),
                "leave_one_top_symbol": _loo_second_rate(ptf, exclude_day=None, exclude_sym=PTF_TOP_SYMBOL.get(cohort)),
            },
            "P_EARLY": {
                "full": _loo_second_rate(p_early, exclude_day=None, exclude_sym=None),
                "leave_one_top_day": _loo_second_rate(p_early, exclude_day=P_EARLY_TOP_DAY.get(cohort), exclude_sym=None),
                "leave_one_top_symbol": _loo_second_rate(p_early, exclude_day=None, exclude_sym=P_EARLY_TOP_SYMBOL.get(cohort)),
            },
        },
        "winner_harm": {
            "dip_second_rate": float(dip["second_below_after_reclaim_rate"]),
            "failure_second_rate": float(failure["second_below_after_reclaim_rate"]),
            "dip_exceeds_failure": float(dip["second_below_after_reclaim_rate"])
            >= float(failure["second_below_after_reclaim_rate"]) - EPS,
        },
    }


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, harvested_ok: bool) -> dict[str, Any]:
    if not harvested_ok:
        return _verdict("D", "SIMPLE_TECH_PROVEN_FAILURE_ACTIONABILITY_INSUFFICIENT", None, "Harvest incomplete.")

    dev_fail = dev["by_pool"]["FAILURE"]
    dev_prot = dev["by_pool"]["PROTECTED"]
    dev_dip = dev["by_pool"]["PROTECTED_DIP"]
    fwd_fail = fwd["by_pool"]["FAILURE"]
    fwd_prot = fwd["by_pool"]["PROTECTED"]

    fail_second_n = int(dev_fail["second_below_after_reclaim_n"])
    prot_n = int(dev_prot["n"])
    if fail_second_n < MIN_EVENT_N or prot_n < MIN_EVENT_N:
        return _verdict("D", "SIMPLE_TECH_PROVEN_FAILURE_ACTIONABILITY_INSUFFICIENT", None, "DEV FAILURE or PROTECTED sample below minimum.")

    delta = float(dev["failure_vs_protected_delta"]["second_below_after_reclaim_rate"])
    fwd_delta = float(fwd["failure_vs_protected_delta"]["second_below_after_reclaim_rate"])
    dip_rate = float(dev_dip["second_below_after_reclaim_rate"])
    fail_rate = float(dev_fail["second_below_after_reclaim_rate"])

    jitter_n = int(dev_fail.get("micro_jitter_warn_n") or 0) + int(dev_prot.get("micro_jitter_warn_n") or 0)
    if jitter_n > 0 and fail_second_n == 0:
        return _verdict(
            "C",
            "SIMPLE_TECH_PROVEN_FAILURE_SECOND_BE_LOSS_NOT_SUPPORTED",
            None,
            "Micro-jitter dominates; raw crossing not stable.",
        )

    if dev["winner_harm"]["dip_exceeds_failure"] or dip_rate >= fail_rate - MIN_RATE_SEP / 2:
        return _verdict(
            "B",
            "SIMPLE_TECH_PROVEN_FAILURE_SECOND_BE_LOSS_WINNER_HARM",
            None,
            "Second BE loss after reclaim fires frequently on PROTECTED_DIP.",
        )

    added_fail = dev["role"]["ADDED"]["FAILURE"]
    added_prot = dev["role"]["ADDED"]["PROTECTED"]
    added_delta = float(added_fail["second_below_after_reclaim_rate"]) - float(added_prot["second_below_after_reclaim_rate"])
    if added_delta < 0:
        return _verdict(
            "C",
            "SIMPLE_TECH_PROVEN_FAILURE_SECOND_BE_LOSS_NOT_SUPPORTED",
            None,
            "ADDED primary direction reversed.",
        )

    loo_ptf = dev["loo"]["PTF"]
    loo_pe = dev["loo"]["P_EARLY"]
    loo_ok = True
    if fail_rate >= MIN_RATE_SEP:
        for lo in (loo_ptf, loo_pe):
            if float(lo["full"]) >= MIN_RATE_SEP and float(lo["leave_one_top_day"]) < MIN_RATE_SEP / 2:
                loo_ok = False

    if delta >= MIN_RATE_SEP and fwd_delta >= -MIN_RATE_SEP / 2 and loo_ok:
        return _verdict(
            "A",
            "SIMPLE_TECH_PROVEN_FAILURE_SECOND_BE_LOSS_ACTIONABLE",
            "SECOND_BELOW_BE_AFTER_RECLAIM",
            "DEV separation from PROTECTED with limited DIP harm; FWD not reversed.",
        )

    if abs(delta) < MIN_RATE_SEP:
        return _verdict(
            "C",
            "SIMPLE_TECH_PROVEN_FAILURE_SECOND_BE_LOSS_NOT_SUPPORTED",
            None,
            "FAILURE vs PROTECTED second-BE-loss separation too weak.",
        )

    if delta >= MIN_RATE_SEP and fwd_delta < -MIN_RATE_SEP:
        return _verdict(
            "C",
            "SIMPLE_TECH_PROVEN_FAILURE_SECOND_BE_LOSS_NOT_SUPPORTED",
            None,
            "FWD direction contradicts DEV.",
        )

    return _verdict(
        "C",
        "SIMPLE_TECH_PROVEN_FAILURE_SECOND_BE_LOSS_NOT_SUPPORTED",
        None,
        "Actionability gate not passed.",
    )


def _verdict(case: str, verdict: str, mechanism: Optional[str], nxt: str) -> dict[str, Any]:
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "PRIMARY_NEXT_MECHANISM": mechanism,
        "PRIMARY_NEXT_EXIT_TARGET": None,
        "NEW_EXIT_RULE": False,
        "CANDIDATE_FROZEN": False,
        "SOURCE_PTF_VERDICT_KEPT": SOURCE_PTF_VERDICT,
        "CLOSED_MECHANISM_OVERLAP": [],
        "RUNTIME_TRIGGER_FORBIDDEN": ["SESSION_GIVEBACK", "SESSION_CLOSE_LABEL"],
    }


def sequence_audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        s = _seq(r)
        out.append(
            {
                "trade_id": r.get("trade_id"),
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "fill_role": r.get("fill_role"),
                "residual_class": r.get("residual_class"),
                "first_BE_time": s.get("first_BE_time"),
                "first_below_BE_time": s.get("first_below_be_time"),
                "first_BE_reclaim_time": s.get("first_reclaim_time"),
                "second_below_BE_after_reclaim_time": s.get("second_below_be_after_reclaim_time"),
                "had_second_below_be_after_reclaim": s.get("had_second_below_be_after_reclaim"),
                "above_to_below_crossing_n": s.get("above_to_below_crossing_n"),
                "below_to_above_crossing_n": s.get("below_to_above_crossing_n"),
                "session_close_pnl": r.get("session_close_pnl"),
                "peak_to_close_giveback": r.get("peak_to_close_giveback"),
                "below_be_terminal_loss": r.get("below_be_terminal_loss"),
            }
        )
    return out
