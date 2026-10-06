"""Entry thesis invalidation RCA analysis, LOO, closed overlap, verdict."""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any, Callable, Optional

from research.simple_tech_redesign.entry_thesis_invalidation_rca_spec import (
    DEV_ADDED_N,
    DEV_CORE_N,
    DEV_FILL_N,
    DEV_PNL,
    FAILURE_CLASSES,
    FWD_ADDED_N,
    FWD_CORE_N,
    FWD_FILL_N,
    FWD_PNL,
    MIN_FAILURE_N,
    MIN_FIRED_N,
    MIN_PROTECTED_N,
    MIN_RATE_SEP,
    PREDICATE_INVENTORY,
    PROTECTED_CLASSES,
)
from research.simple_tech_redesign.exit_composition_shift_rca_analyze import load_residual_rows
from research.simple_tech_redesign.exit_residual_rca_spec import YEN_PARITY_TOL

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


def _path(r: dict[str, Any]) -> dict[str, Any]:
    return dict(r.get("thesis_path") or {})


def _thesis(r: dict[str, Any]) -> dict[str, Any]:
    return dict(_path(r).get("thesis") or {})


def assert_population_identity(dev: list[dict[str, Any]], fwd: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(dev) == DEV_FILL_N, f"DEV fill {len(dev)} != {DEV_FILL_N}"
    assert len(fwd) == FWD_FILL_N, f"FWD fill {len(fwd)} != {FWD_FILL_N}"
    dev_core = sum(1 for r in dev if str(r.get("fill_role") or "") == "CORE")
    dev_added = sum(1 for r in dev if str(r.get("fill_role") or "") == "ADDED")
    fwd_core = sum(1 for r in fwd if str(r.get("fill_role") or "") == "CORE")
    fwd_added = sum(1 for r in fwd if str(r.get("fill_role") or "") == "ADDED")
    assert dev_core == DEV_CORE_N and dev_added == DEV_ADDED_N
    assert fwd_core == FWD_CORE_N and fwd_added == FWD_ADDED_N
    dev_pnl = sum(float(_f(r.get("session_close_pnl")) or 0.0) for r in dev)
    fwd_pnl = sum(float(_f(r.get("session_close_pnl")) or 0.0) for r in fwd)
    assert abs(dev_pnl - DEV_PNL) <= YEN_PARITY_TOL, f"DEV pnl {dev_pnl}"
    assert abs(fwd_pnl - FWD_PNL) <= YEN_PARITY_TOL, f"FWD pnl {fwd_pnl}"
    return {
        "dev_fill_n": len(dev),
        "fwd_fill_n": len(fwd),
        "dev_core_n": dev_core,
        "dev_added_n": dev_added,
        "fwd_core_n": fwd_core,
        "fwd_added_n": fwd_added,
        "dev_pnl": dev_pnl,
        "fwd_pnl": fwd_pnl,
    }


def assert_entry_parity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    parity_fail = [r for r in rows if not bool(_path(r).get("entry_parity_ok"))]
    assert not parity_fail, f"entry predicate parity mismatch n={len(parity_fail)}"
    return {"entry_parity_ok_n": len(rows), "entry_parity_fail_n": 0}


def assert_identity(dev: list[dict[str, Any]], fwd: list[dict[str, Any]]) -> dict[str, Any]:
    out = assert_population_identity(dev, fwd)
    if any(_path(r) for r in dev + fwd if r.get("thesis_path") is not None):
        parity = assert_entry_parity(dev + fwd)
        out.update(parity)
    return out


def merge_harvest(rows: list[dict[str, Any]], harvested: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(r.get("trade_id") or ""): r for r in harvested}
    out = []
    for r in rows:
        h = dict(by_id.get(str(r.get("trade_id") or "")) or {})
        rec = dict(r)
        if h.get("thesis_path") is not None:
            rec["thesis_path"] = h["thesis_path"]
            rec["thesis_ok"] = h.get("thesis_ok")
        out.append(rec)
    return out


def _pool(rows: list[dict[str, Any]], pool: str) -> list[dict[str, Any]]:
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
    th = [_thesis(r) for r in rows]
    any_first = _rate(rows, lambda r: bool(_thesis(r).get("any_first_invalidation")))
    multi = _rate(rows, lambda r: bool(_thesis(r).get("ever_multi_invalid")))
    full = _rate(rows, lambda r: bool(_thesis(r).get("ever_fully_invalid")))
    recover = _rate(rows, lambda r: bool(_thesis(r).get("recovered_to_all_valid")))
    multi_before_rec = _rate(rows, lambda r: bool(_thesis(r).get("multi_before_full_recovery")))
    partial_recover = _rate(
        rows,
        lambda r: bool(_thesis(r).get("ever_partially_invalid"))
        and bool(_thesis(r).get("recovered_to_all_valid"))
        and not bool(_thesis(r).get("ever_multi_invalid")),
    )
    partial_to_multi = _rate(
        rows,
        lambda r: bool(_thesis(r).get("ever_partially_invalid")) and bool(_thesis(r).get("ever_multi_invalid")),
    )
    fired_multi = [r for r in rows if _thesis(r).get("ever_multi_invalid")]
    churn_v2i = [float(_thesis(r).get("valid_to_invalid_n") or 0) for r in rows]
    churn_i2v = [float(_thesis(r).get("invalid_to_valid_n") or 0) for r in rows]
    pred_churn: dict[str, dict[str, Any]] = {}
    for pid, _ in [("P1_TREND_UP", None), ("P2_PULLBACK_SETUP", None), ("P3_REVERSAL_RCI", None)]:
        v2i = []
        i2v = []
        for r in rows:
            pe = dict(_path(r).get("predicates") or {}).get(pid) or {}
            v2i.append(float(pe.get("valid_to_invalid_n") or 0))
            i2v.append(float(pe.get("invalid_to_valid_n") or 0))
        pred_churn[pid] = {
            "median_valid_to_invalid_n": median(v2i) if v2i else None,
            "iqr_valid_to_invalid_n": _iqr(v2i),
            "median_invalid_to_valid_n": median(i2v) if i2v else None,
            "iqr_invalid_to_valid_n": _iqr(i2v),
        }
    first_pred = Counter(str(_path(r).get("first_invalidated_predicate") or "NONE") for r in rows if _thesis(r).get("any_first_invalidation"))
    seq = Counter(str(_path(r).get("sequence_order") or "NONE") for r in rows)
    return {
        "n": n,
        "any_first_invalidation_rate": any_first,
        "multi_invalid_rate": multi,
        "full_invalid_rate": full,
        "recovery_after_first_invalid_rate": recover,
        "multi_invalid_before_recovery_rate": multi_before_rec,
        "recovered_to_all_valid_rate": recover,
        "partial_then_recover_rate": partial_recover,
        "partial_to_multi_rate": partial_to_multi,
        "multi_fired_n": len(fired_multi),
        "median_thesis_valid_to_invalid_n": median(churn_v2i) if churn_v2i else None,
        "iqr_thesis_valid_to_invalid_n": _iqr(churn_v2i),
        "median_thesis_invalid_to_valid_n": median(churn_i2v) if churn_i2v else None,
        "iqr_thesis_invalid_to_valid_n": _iqr(churn_i2v),
        "predicate_churn": pred_churn,
        "first_invalidated_predicate": dict(first_pred),
        "sequence_order": dict(seq),
        "overlay": _overlay(rows),
        "overlay_multi_fired": _overlay(fired_multi),
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


def _top_loss_day_symbol(rows: list[dict[str, Any]]) -> tuple[str, str]:
    by_day: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    for r in rows:
        loss = max(0.0, -float(_f(r.get("session_close_pnl")) or 0.0))
        by_day[str(r.get("date") or "")] += loss
        by_sym[str(r.get("symbol") or "")] += loss
    top_day = max(by_day.items(), key=lambda x: x[1])[0] if by_day else ""
    top_sym = max(by_sym.items(), key=lambda x: x[1])[0] if by_sym else ""
    return top_day, top_sym


def _loo_multi_rate(rows: list[dict[str, Any]], *, exclude_day: str | None, exclude_sym: str | None) -> float:
    filt = rows
    if exclude_day:
        filt = [r for r in filt if str(r.get("date") or "") != exclude_day]
    if exclude_sym:
        filt = [r for r in filt if str(r.get("symbol") or "") != exclude_sym]
    return _rate(filt, lambda r: bool(_thesis(r).get("ever_multi_invalid")))


def closed_mechanism_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    multi_rows = [r for r in rows if _thesis(r).get("ever_multi_invalid")]
    n = len(multi_rows)
    if not n:
        return {"multi_n": 0, "verdict_hint": "NO_MULTI_EVENTS"}
    v27_hit = sum(1 for r in multi_rows if bool(dict(_path(r).get("closed_overlap") or {}).get("v27_ema_structure_loss_hit")))
    same_bar = sum(1 for r in multi_rows if bool(dict(_path(r).get("closed_overlap") or {}).get("same_bar")))
    p3_first = sum(
        1 for r in multi_rows if str(_path(r).get("first_invalidated_predicate") or "") == "P3_REVERSAL_RCI"
    )
    return {
        "multi_n": n,
        "v27_ema_structure_loss_co_hit_rate": v27_hit / n,
        "v27_same_bar_rate": same_bar / n,
        "p3_first_invalidation_share": p3_first / n,
        "closed_paths": {
            "V27_EMA_PERSISTENCE": "CLOSED",
            "V28_K6": "CLOSED",
            "V29_TERMINAL_SEQUENCE": "CLOSED",
            "BRANCH_U_BB": "CLOSED",
            "BRANCH_P_SECOND_BE_LOSS": "CLOSED",
            "PRE_CAP_BOARD": "CLOSED",
        },
        "repackaging_risk": bool(same_bar / n >= 0.80 if n else False),
        "note": "MULTI_INVALID with P3_REVERSAL_RCI one-bar cross is expected churn, not independent thesis collapse.",
    }


def cohort_pack(rows: list[dict[str, Any]], *, cohort: str) -> dict[str, Any]:
    pools = (
        "FAILURE",
        "PROTECTED",
        "U_EARLY_NEVER_BE",
        "P_EARLY_AFTER_BE",
        "P_PROFIT_THEN_FAILURE",
        "PROTECTED_DIP",
        "PROTECTED_GOOD",
        "OTHER",
    )
    by_pool = {p: _event_pack(_pool(rows, p)) for p in pools}
    failure = by_pool["FAILURE"]
    protected = by_pool["PROTECTED"]
    fail_rows = _pool(rows, "FAILURE")
    top_day, top_sym = _top_loss_day_symbol(fail_rows)
    return {
        "cohort": cohort,
        "fill_n": len(rows),
        "by_pool": by_pool,
        "failure_vs_protected_delta": {
            "multi_invalid_rate": float(failure["multi_invalid_rate"]) - float(protected["multi_invalid_rate"]),
            "any_first_invalidation_rate": float(failure["any_first_invalidation_rate"])
            - float(protected["any_first_invalidation_rate"]),
            "partial_then_recover_rate": float(failure["partial_then_recover_rate"])
            - float(protected["partial_then_recover_rate"]),
            "partial_to_multi_rate": float(failure["partial_to_multi_rate"]) - float(protected["partial_to_multi_rate"]),
        },
        "role": {
            role: {
                "FAILURE": _event_pack([r for r in _pool(rows, "FAILURE") if str(r.get("fill_role") or "") == role]),
                "PROTECTED": _event_pack([r for r in _pool(rows, "PROTECTED") if str(r.get("fill_role") or "") == role]),
            }
            for role in ("CORE", "ADDED")
        },
        "loo_multi_invalid": {
            "full": _loo_multi_rate(fail_rows, exclude_day=None, exclude_sym=None),
            "leave_one_top_loss_day": _loo_multi_rate(fail_rows, exclude_day=top_day, exclude_sym=None),
            "leave_one_top_loss_symbol": _loo_multi_rate(fail_rows, exclude_day=None, exclude_sym=top_sym),
            "top_loss_day": top_day,
            "top_loss_symbol": top_sym,
        },
        "closed_mechanism": closed_mechanism_audit(rows),
        "winner_harm": {
            "dip_multi_rate": float(by_pool["PROTECTED_DIP"]["multi_invalid_rate"]),
            "good_multi_rate": float(by_pool["PROTECTED_GOOD"]["multi_invalid_rate"]),
            "failure_multi_rate": float(failure["multi_invalid_rate"]),
        },
    }


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, harvested_ok: bool) -> dict[str, Any]:
    if not harvested_ok:
        return _verdict("D", "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_INSUFFICIENT", None, "Harvest incomplete.")

    dev_fail = dev["by_pool"]["FAILURE"]
    dev_prot = dev["by_pool"]["PROTECTED"]
    if int(dev_fail["n"]) < MIN_FAILURE_N or int(dev_prot["n"]) < MIN_PROTECTED_N:
        return _verdict("D", "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_INSUFFICIENT", None, "DEV FAILURE/PROTECTED below minimum.")

    fail_multi_n = int(dev_fail["multi_fired_n"])
    prot_multi_n = int(dev_prot["multi_fired_n"])
    if fail_multi_n < MIN_FIRED_N or prot_multi_n < MIN_FIRED_N:
        return _verdict("D", "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_INSUFFICIENT", None, "Multi-invalid fired sample below minimum.")

    delta_multi = float(dev["failure_vs_protected_delta"]["multi_invalid_rate"])
    fwd_delta = float(fwd["failure_vs_protected_delta"]["multi_invalid_rate"])
    dip_multi = float(dev["winner_harm"]["dip_multi_rate"])
    good_multi = float(dev["winner_harm"]["good_multi_rate"])
    fail_multi = float(dev_fail["multi_invalid_rate"])

    closed = dict(dev.get("closed_mechanism") or {})
    if closed.get("repackaging_risk"):
        return _verdict(
            "E",
            "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_ALREADY_CLOSED",
            None,
            "MULTI_INVALID largely coincides with closed V27 EMA structure loss timing.",
        )

    if dip_multi >= fail_multi - MIN_RATE_SEP / 2 or good_multi >= fail_multi - MIN_RATE_SEP / 2:
        return _verdict(
            "B",
            "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_WINNER_HARM",
            None,
            "Thesis invalidation fires frequently on PROTECTED winners.",
        )

    added_delta = float(dev["role"]["ADDED"]["FAILURE"]["multi_invalid_rate"]) - float(
        dev["role"]["ADDED"]["PROTECTED"]["multi_invalid_rate"]
    )
    if added_delta < 0:
        return _verdict(
            "C",
            "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_CONTRADICTED",
            None,
            "ADDED primary direction reversed.",
        )

    loo = dict(dev.get("loo_multi_invalid") or {})
    loo_ok = True
    if fail_multi >= MIN_RATE_SEP:
        if float(loo.get("full") or 0) >= MIN_RATE_SEP and float(loo.get("leave_one_top_loss_day") or 0) < MIN_RATE_SEP / 2:
            loo_ok = False
        if float(loo.get("full") or 0) >= MIN_RATE_SEP and float(loo.get("leave_one_top_loss_symbol") or 0) < MIN_RATE_SEP / 2:
            loo_ok = False

    partial_to_multi_delta = float(dev["failure_vs_protected_delta"]["partial_to_multi_rate"])
    if (
        delta_multi >= MIN_RATE_SEP
        and partial_to_multi_delta >= MIN_RATE_SEP / 2
        and fwd_delta >= -MIN_RATE_SEP / 2
        and loo_ok
    ):
        return _verdict(
            "A",
            "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_MECHANISM_FOUND",
            "MULTI_INVALID",
            "DEV FAILURE shows higher multi-predicate thesis collapse; FWD not reversed; LOO holds.",
        )

    if abs(delta_multi) < MIN_RATE_SEP:
        return _verdict(
            "C",
            "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_CONTRADICTED",
            None,
            "FAILURE vs PROTECTED multi-invalid separation too weak.",
        )

    if delta_multi >= MIN_RATE_SEP and fwd_delta < -MIN_RATE_SEP:
        return _verdict(
            "C",
            "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_CONTRADICTED",
            None,
            "FWD direction contradicts DEV.",
        )

    return _verdict(
        "C",
        "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_CONTRADICTED",
        None,
        "Primary hypothesis not supported.",
    )


def _verdict(case: str, verdict: str, mechanism: Optional[str], nxt: str) -> dict[str, Any]:
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "PRIMARY_NEXT_MECHANISM": mechanism,
        "PRIMARY_NEXT_EXIT_TARGET": None,
        "NEW_EXIT_RULE": False,
        "CANDIDATE_FROZEN": bool(case == "A"),
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "RUNTIME_ACTIONABLE": ["FIRST_INVALID", "MULTI_INVALID", "FULL_INVALID", "RECOVERY"],
        "RUNTIME_FORBIDDEN": ["SESSION_CLOSE_WITHOUT_FULL_RECOVERY", "GOOD", "DIP", "PTF", "SESSION_CLOSE_LABEL"],
    }


def predicate_inventory_report() -> list[dict[str, Any]]:
    return [dict(p) for p in PREDICATE_INVENTORY]


def trade_audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        th = _thesis(r)
        out.append(
            {
                "trade_id": r.get("trade_id"),
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "fill_role": r.get("fill_role"),
                "residual_class": r.get("residual_class"),
                "entry_parity_ok": _path(r).get("entry_parity_ok"),
                "entry_states": _path(r).get("entry_states"),
                "any_first_invalidation": th.get("any_first_invalidation"),
                "ever_multi_invalid": th.get("ever_multi_invalid"),
                "ever_fully_invalid": th.get("ever_fully_invalid"),
                "recovered_to_all_valid": th.get("recovered_to_all_valid"),
                "multi_before_full_recovery": th.get("multi_before_full_recovery"),
                "first_invalidated_predicate": _path(r).get("first_invalidated_predicate"),
                "sequence_order": _path(r).get("sequence_order"),
                "session_close_pnl": r.get("session_close_pnl"),
                "peak_to_close_giveback": r.get("peak_to_close_giveback"),
                "below_be_terminal_loss": r.get("below_be_terminal_loss"),
            }
        )
    return out
