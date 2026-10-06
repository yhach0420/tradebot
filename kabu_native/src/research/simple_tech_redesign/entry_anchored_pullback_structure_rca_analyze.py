"""Entry-anchored pullback structure RCA rates, LOO, overlap, verdict."""
from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any, Callable, Optional

from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_spec import (
    DEV_ADDED_N,
    DEV_CORE_N,
    DEV_FILL_N,
    DEV_PNL,
    DEV_TOP_LOSS_DAY,
    DEV_TOP_LOSS_SYMBOL,
    FAILURE_CLASSES,
    FWD_ADDED_N,
    FWD_CORE_N,
    FWD_FILL_N,
    FWD_PNL,
    FWD_TOP_LOSS_DAY,
    FWD_TOP_LOSS_SYMBOL,
    MIN_FAILURE_N,
    MIN_PROTECTED_N,
    MIN_RATE_SEP,
    PROTECTED_CLASSES,
    REPACKAGING_SAME_BAR_RATE,
)
from research.simple_tech_redesign.exit_residual_rca_spec import YEN_PARITY_TOL

EPS = 1e-9
TOP_LOSS = {
    "DEVELOPMENT": {"day": DEV_TOP_LOSS_DAY, "symbol": DEV_TOP_LOSS_SYMBOL},
    "FORWARD_BURNED": {"day": FWD_TOP_LOSS_DAY, "symbol": FWD_TOP_LOSS_SYMBOL},
}


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
    return dict(r.get("structure_path") or {})


def _ref(r: dict[str, Any]) -> dict[str, Any]:
    return dict(_path(r).get("reference") or {})


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


def assert_reference_integrity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fail = []
    for r in rows:
        p = _path(r)
        ref = _ref(r)
        if not bool(p.get("reference_ok")):
            fail.append((r.get("trade_id"), p.get("reference_blocker")))
            continue
        bars = list(ref.get("bars") or [])
        if len(bars) != 3:
            fail.append((r.get("trade_id"), "P2_BAR_N"))
            continue
        lows = [float(b["low"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        sl = float(ref["setup_low"])
        sh = float(ref["setup_high"])
        if abs(sl - min(lows)) > EPS or any(sl > x + EPS for x in lows):
            fail.append((r.get("trade_id"), "SETUP_LOW"))
        if abs(sh - max(highs)) > EPS or any(sh + EPS < x for x in highs):
            fail.append((r.get("trade_id"), "SETUP_HIGH"))
        t0 = _f(r.get("t0"))
        if t0 is None or not bool(ref.get("reference_le_signal")):
            fail.append((r.get("trade_id"), "REFERENCE_TS"))
        if not bool(ref.get("p2_pullback_setup")):
            fail.append((r.get("trade_id"), "P2_FALSE"))
    assert not fail, f"signal-reference parity FAIL n={len(fail)} sample={fail[:3]}"
    return {"signal_reference_ok_n": len(rows), "signal_reference_fail_n": 0, "p2_bars_recovered_n": len(rows)}


def merge_harvest(rows: list[dict[str, Any]], harvested: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(r.get("trade_id") or ""): r for r in harvested}
    out = []
    for r in rows:
        h = dict(by_id.get(str(r.get("trade_id") or "")) or {})
        rec = dict(r)
        if h.get("structure_path") is not None:
            rec["structure_path"] = h["structure_path"]
            rec["structure_ok"] = h.get("structure_ok")
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


def _med_iqr(xs: list[float]) -> dict[str, Any]:
    return {"n": len(xs), "median": median(xs) if xs else None, "iqr": _iqr(xs)}


def _overlay(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "session_close_pnl_total": 0.0, "gross_loss_total": 0.0, "giveback_total": 0.0, "positive_trade_rate": 0.0}
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
        "positive_trade_rate": sum(1 for p in pnls if p > EPS) / len(pnls),
    }


def _event_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    floor_n = sum(1 for r in rows if _path(r).get("floor_break"))
    up_n = sum(1 for r in rows if _path(r).get("upside_break"))
    floor_first_n = sum(1 for r in rows if _path(r).get("floor_break_first"))
    up_first_n = sum(1 for r in rows if _path(r).get("upside_break_first"))
    same_n = sum(1 for r in rows if _path(r).get("same_completed_bar"))
    neither_n = sum(1 for r in rows if _path(r).get("neither"))
    seq = Counter(str(_path(r).get("sequence") or "") for r in rows)
    tmg = [_path(r).get("timing") or {} for r in rows]
    dist = [_path(r).get("distance") or {} for r in rows]
    exe_ok = [r for r in rows if _path(r).get("floor_break")]
    exe_hit = [r for r in exe_ok if not bool(dict(_path(r).get("executability") or {}).get("miss"))]
    lat = [
        float(dict(_path(r).get("executability") or {}).get("latency_sec"))
        for r in exe_hit
        if _f(dict(_path(r).get("executability") or {}).get("latency_sec")) is not None
    ]
    return {
        "n": n,
        "floor_break_n": floor_n,
        "floor_break_rate": floor_n / n if n else 0.0,
        "upside_break_n": up_n,
        "upside_break_rate": up_n / n if n else 0.0,
        "floor_break_first_n": floor_first_n,
        "floor_break_first_rate": floor_first_n / n if n else 0.0,
        "upside_break_first_n": up_first_n,
        "upside_break_first_rate": up_first_n / n if n else 0.0,
        "same_bar_n": same_n,
        "same_bar_rate": same_n / n if n else 0.0,
        "neither_n": neither_n,
        "neither_rate": neither_n / n if n else 0.0,
        "sequence": dict(seq),
        "timing": {
            "signal_to_floor_sec": _med_iqr([float(t["signal_to_floor_sec"]) for t in tmg if _f(t.get("signal_to_floor_sec")) is not None]),
            "signal_to_upside_sec": _med_iqr([float(t["signal_to_upside_sec"]) for t in tmg if _f(t.get("signal_to_upside_sec")) is not None]),
            "fill_to_floor_sec": _med_iqr([float(t["fill_to_floor_sec"]) for t in tmg if _f(t.get("fill_to_floor_sec")) is not None]),
            "fill_to_upside_sec": _med_iqr([float(t["fill_to_upside_sec"]) for t in tmg if _f(t.get("fill_to_upside_sec")) is not None]),
        },
        "distance": {
            "setup_range_yen": _med_iqr([float(d["setup_range_yen"]) for d in dist if _f(d.get("setup_range_yen")) is not None]),
            "setup_range_bps": _med_iqr([float(d["setup_range_bps"]) for d in dist if _f(d.get("setup_range_bps")) is not None]),
            "fill_minus_setup_low_yen": _med_iqr(
                [float(d["fill_minus_setup_low_yen"]) for d in dist if _f(d.get("fill_minus_setup_low_yen")) is not None]
            ),
            "fill_minus_setup_low_bps": _med_iqr(
                [float(d["fill_minus_setup_low_bps"]) for d in dist if _f(d.get("fill_minus_setup_low_bps")) is not None]
            ),
        },
        "executability": {
            "floor_event_n": len(exe_ok),
            "first_executable_bid_n": len(exe_hit),
            "first_executable_bid_rate": len(exe_hit) / len(exe_ok) if exe_ok else 0.0,
            "median_latency_sec": median(lat) if lat else None,
            "iqr_latency_sec": _iqr(lat),
        },
        "overlay": _overlay(rows),
        "overlay_by_sequence": {
            lab: _overlay([r for r in rows if str(_path(r).get("sequence") or "") == lab])
            for lab in (
                "A_UPSIDE_BREAK_FIRST",
                "B_FLOOR_BREAK_FIRST",
                "C_SAME_COMPLETED_BAR",
                "D_NEITHER_BEFORE_SESSION_CLOSE",
            )
        },
    }


def _loo_floor_first(rows: list[dict[str, Any]], *, exclude_day: str | None, exclude_sym: str | None) -> float:
    filt = rows
    if exclude_day:
        filt = [r for r in filt if str(r.get("date") or "") != exclude_day]
    if exclude_sym:
        filt = [r for r in filt if str(r.get("symbol") or "") != exclude_sym]
    return _rate(filt, lambda r: bool(_path(r).get("floor_break_first")))


def closed_mechanism_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    floor_rows = [r for r in rows if _path(r).get("floor_break")]
    n = len(floor_rows)
    if not n:
        return {"floor_break_n": 0, "repackaging_warning": False, "conceptually_distinct": True, "case_e_same_mechanism": False}
    bb = sum(1 for r in floor_rows if bool(dict(_path(r).get("closed_overlap") or {}).get("floor_vs_bb_same_bar")))
    ema = sum(1 for r in floor_rows if bool(dict(_path(r).get("closed_overlap") or {}).get("floor_vs_ema_same_bar")))
    diffs = [
        abs(float(v))
        for r in floor_rows
        if (v := dict(_path(r).get("closed_overlap") or {}).get("setup_low_minus_bb_lower_at_signal")) is not None
        and _f(v) is not None
    ]
    bb_rate = bb / n
    ema_rate = ema / n
    level_collapse = bool(diffs) and float(median(diffs)) <= 1e-6
    return {
        "floor_break_n": n,
        "branch_u_bb_same_bar_n": bb,
        "branch_u_bb_same_bar_rate": bb_rate,
        "v27_ema_same_bar_n": ema,
        "v27_ema_same_bar_rate": ema_rate,
        "median_abs_setup_low_minus_bb_lower_at_signal": median(diffs) if diffs else None,
        "repackaging_warning": bool(bb_rate >= REPACKAGING_SAME_BAR_RATE or ema_rate >= REPACKAGING_SAME_BAR_RATE),
        "conceptually_distinct": True,
        "closed_paths": {
            "BRANCH_U_BB": "CLOSED_DYNAMIC_BB_LOWER",
            "V27_EMA": "CLOSED_DYNAMIC_TREND_STATE",
            "BRANCH_P": "CLOSED_ECONOMIC_BE",
            "ENTRY_THESIS_INVALIDATION": "CLOSED_PREDICATE_REPLAY",
        },
        "note": "Fixed signal-time SETUP_LOW vs dynamic BB/EMA. 80% same-bar is a warning, not auto-CLOSE.",
        "case_e_same_mechanism": bool(bb_rate >= REPACKAGING_SAME_BAR_RATE and level_collapse),
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
    fail_rows = _pool(rows, "FAILURE")
    top = TOP_LOSS.get(cohort) or {}
    return {
        "cohort": cohort,
        "fill_n": len(rows),
        "by_pool": by_pool,
        "failure_vs_protected_delta": {
            "floor_break_first_rate": float(by_pool["FAILURE"]["floor_break_first_rate"])
            - float(by_pool["PROTECTED"]["floor_break_first_rate"]),
            "floor_break_rate": float(by_pool["FAILURE"]["floor_break_rate"]) - float(by_pool["PROTECTED"]["floor_break_rate"]),
            "upside_break_first_rate": float(by_pool["FAILURE"]["upside_break_first_rate"])
            - float(by_pool["PROTECTED"]["upside_break_first_rate"]),
        },
        "role": {
            role: {
                "FAILURE": _event_pack([r for r in _pool(rows, "FAILURE") if str(r.get("fill_role") or "") == role]),
                "PROTECTED": _event_pack([r for r in _pool(rows, "PROTECTED") if str(r.get("fill_role") or "") == role]),
            }
            for role in ("CORE", "ADDED")
        },
        "loo_floor_break_first": {
            "full": _loo_floor_first(fail_rows, exclude_day=None, exclude_sym=None),
            "leave_one_top_loss_day": _loo_floor_first(fail_rows, exclude_day=str(top.get("day") or ""), exclude_sym=None),
            "leave_one_top_loss_symbol": _loo_floor_first(fail_rows, exclude_day=None, exclude_sym=str(top.get("symbol") or "")),
            "top_loss_day": top.get("day"),
            "top_loss_symbol": top.get("symbol"),
        },
        "closed_mechanism": closed_mechanism_audit(rows),
        "winner_harm": {
            "dip_floor_first_rate": float(by_pool["PROTECTED_DIP"]["floor_break_first_rate"]),
            "good_floor_first_rate": float(by_pool["PROTECTED_GOOD"]["floor_break_first_rate"]),
            "failure_floor_first_rate": float(by_pool["FAILURE"]["floor_break_first_rate"]),
            "protected_floor_first_rate": float(by_pool["PROTECTED"]["floor_break_first_rate"]),
        },
        "sequence_overlay_all": {
            lab: _overlay([r for r in rows if str(_path(r).get("sequence") or "") == lab])
            for lab in (
                "A_UPSIDE_BREAK_FIRST",
                "B_FLOOR_BREAK_FIRST",
                "C_SAME_COMPLETED_BAR",
                "D_NEITHER_BEFORE_SESSION_CLOSE",
            )
        },
    }


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, harvested_ok: bool, carryback_n: int) -> dict[str, Any]:
    if not harvested_ok:
        return _verdict("D", "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_INSUFFICIENT", None, "Harvest incomplete.")
    if int(carryback_n) > 0:
        return _verdict("D", "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_INSUFFICIENT", None, "Future quote carryback != 0.")

    dev_fail = dev["by_pool"]["FAILURE"]
    dev_prot = dev["by_pool"]["PROTECTED"]
    if int(dev_fail["n"]) < MIN_FAILURE_N or int(dev_prot["n"]) < MIN_PROTECTED_N:
        return _verdict("D", "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_INSUFFICIENT", None, "DEV FAILURE/PROTECTED below minimum.")

    fail_rate = float(dev_fail["floor_break_first_rate"])
    prot_rate = float(dev_prot["floor_break_first_rate"])
    dip_rate = float(dev["winner_harm"]["dip_floor_first_rate"])
    good_rate = float(dev["winner_harm"]["good_floor_first_rate"])
    delta = float(dev["failure_vs_protected_delta"]["floor_break_first_rate"])
    fwd_delta = float(fwd["failure_vs_protected_delta"]["floor_break_first_rate"])

    closed = dict(dev.get("closed_mechanism") or {})
    if closed.get("case_e_same_mechanism"):
        return _verdict(
            "E",
            "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_ALREADY_CLOSED",
            None,
            "Floor-break timestamps collapse onto a closed dynamic mechanism.",
        )

    dip_harm = dip_rate >= fail_rate - MIN_RATE_SEP / 2 or good_rate >= fail_rate - MIN_RATE_SEP / 2
    if dip_harm:
        return _verdict(
            "B",
            "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_WINNER_HARM",
            None,
            "FLOOR_BREAK_FIRST is frequent on DIP/GOOD; entry-anchored structural stop would harm winners.",
        )

    added_delta = float(dev["role"]["ADDED"]["FAILURE"]["floor_break_first_rate"]) - float(
        dev["role"]["ADDED"]["PROTECTED"]["floor_break_first_rate"]
    )
    if added_delta < 0:
        return _verdict(
            "C",
            "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_CONTRADICTED",
            None,
            "ADDED primary direction reversed.",
        )

    loo = dict(dev.get("loo_floor_break_first") or {})
    loo_ok = True
    if fail_rate >= MIN_RATE_SEP:
        if float(loo.get("full") or 0) >= MIN_RATE_SEP and float(loo.get("leave_one_top_loss_day") or 0) < MIN_RATE_SEP / 2:
            loo_ok = False
        if float(loo.get("full") or 0) >= MIN_RATE_SEP and float(loo.get("leave_one_top_loss_symbol") or 0) < MIN_RATE_SEP / 2:
            loo_ok = False

    dip_limited = dip_rate <= fail_rate - MIN_RATE_SEP
    if delta >= MIN_RATE_SEP and dip_limited and fwd_delta >= -MIN_RATE_SEP / 2 and loo_ok:
        return _verdict(
            "A",
            "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_MECHANISM_FOUND",
            "PULLBACK_FLOOR_BREAK",
            "DEV FAILURE shows higher FLOOR_BREAK_FIRST; DIP limited; FWD not reversed; LOO holds. Next run: 1 candidate precommit, no EXIT yet.",
        )

    if abs(delta) < MIN_RATE_SEP:
        return _verdict(
            "C",
            "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_CONTRADICTED",
            None,
            "FAILURE vs PROTECTED FLOOR_BREAK_FIRST separation too weak.",
        )

    if delta >= MIN_RATE_SEP and fwd_delta < -MIN_RATE_SEP:
        return _verdict(
            "C",
            "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_CONTRADICTED",
            None,
            "FWD direction contradicts DEV.",
        )

    if not loo_ok:
        return _verdict(
            "C",
            "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_CONTRADICTED",
            None,
            "Concentration LOO collapsed primary direction.",
        )

    return _verdict(
        "C",
        "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_CONTRADICTED",
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
        "CANDIDATE_FROZEN": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "EXIT_SIMULATION": False,
        "RUNTIME_ACTIONABLE": ["PULLBACK_FLOOR_BREAK", "SETUP_RANGE_BREAKOUT"],
        "RUNTIME_FORBIDDEN": ["SESSION_CLOSE_LABEL", "GOOD", "DIP", "PTF", "P3_REVERSAL_RCI_POST_ENTRY"],
    }


def trade_audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        p = _path(r)
        ref = _ref(r)
        exe = dict(p.get("executability") or {})
        tmg = dict(p.get("timing") or {})
        dist = dict(p.get("distance") or {})
        ov = dict(p.get("closed_overlap") or {})
        out.append(
            {
                "trade_id": r.get("trade_id"),
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "fill_role": r.get("fill_role"),
                "residual_class": r.get("residual_class"),
                "t0": r.get("t0"),
                "fill_time": r.get("fill_time"),
                "fill_price": r.get("fill_price"),
                "reference_ok": p.get("reference_ok"),
                "setup_low": ref.get("setup_low"),
                "setup_high": ref.get("setup_high"),
                "signal_bar_low": ref.get("signal_bar_low"),
                "signal_bar_high": ref.get("signal_bar_high"),
                "signal_bar_close": ref.get("signal_bar_close"),
                "bar_indices": ref.get("bar_indices"),
                "sequence": p.get("sequence"),
                "floor_break_t": p.get("floor_break_t"),
                "upside_break_t": p.get("upside_break_t"),
                "floor_break_first": p.get("floor_break_first"),
                "upside_break_first": p.get("upside_break_first"),
                "signal_to_floor_sec": tmg.get("signal_to_floor_sec"),
                "fill_to_floor_sec": tmg.get("fill_to_floor_sec"),
                "setup_range_yen": dist.get("setup_range_yen"),
                "setup_range_bps": dist.get("setup_range_bps"),
                "fill_minus_setup_low_yen": dist.get("fill_minus_setup_low_yen"),
                "first_executable_time": exe.get("first_executable_time"),
                "first_executable_bid": exe.get("first_executable_bid"),
                "latency_sec": exe.get("latency_sec"),
                "freshness_ok": exe.get("freshness_ok"),
                "bb_same_bar": ov.get("floor_vs_bb_same_bar"),
                "ema_same_bar": ov.get("floor_vs_ema_same_bar"),
                "session_close_pnl": r.get("session_close_pnl"),
                "peak_to_close_giveback": r.get("peak_to_close_giveback"),
                "below_be_terminal_loss": r.get("below_be_terminal_loss"),
            }
        )
    return out
