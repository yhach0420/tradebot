"""V18 fixed-180 policy PnL, latency, vs V14 HOLD180. No horizon search. No dynamic EXIT."""
from __future__ import annotations

from typing import Any

import numpy as np

from replay.pnl_yen import summarize_pnl_yen_100
from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.anchor_timing_robustness.metrics import maxdd
from research.simple_tech_entry_family.v3_analyze import concentration, day_rows, day_sign_counts
from research.simple_tech_entry_family.v4_analyze import _mean, _median
from research.simple_tech_entry_family.v6_pullback_analyze import drop_symbol_mean, symbol_pack
from research.simple_tech_entry_family.v9_analyze import drop_top3_mean
from research.simple_tech_exit_family.v14_analyze import _finite, _gt0, _pctl, hold_benchmark
from research.simple_tech_exit_family.v18_spec import (
    E4_FILLED_N_EXPECTED,
    EXIT_POLICY_NAME,
    HOLD180_DROP_TOP_SYMBOL_EXPECTED,
    HOLD180_EX_BEST_EXPECTED,
    HOLD180_EX_TOP3_EXPECTED,
    HOLD180_MEAN_EXPECTED,
    HOLD180_MEDIAN_EXPECTED,
    HOLD180_NEG_DAY_EXPECTED,
    HOLD180_POS_DAY_EXPECTED,
    POLICY_ID,
)


def _close(a: Any, b: Any, tol: float = 1e-8) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= float(tol)


def hold180_ref_ok(v14_rows: list[dict[str, Any]]) -> dict[str, Any]:
    bm = hold_benchmark(v14_rows)
    h = dict(bm.get(180) or {})
    ok = bool(
        _close(h.get("mean"), HOLD180_MEAN_EXPECTED)
        and _close(h.get("median"), HOLD180_MEDIAN_EXPECTED)
        and int(h.get("POSITIVE_DAY_N") or -1) == int(HOLD180_POS_DAY_EXPECTED)
        and int(h.get("NEGATIVE_DAY_N") or -1) == int(HOLD180_NEG_DAY_EXPECTED)
        and _close(h.get("EX_BEST"), HOLD180_EX_BEST_EXPECTED)
        and _close(h.get("EX_TOP3"), HOLD180_EX_TOP3_EXPECTED)
        and _close(h.get("DROP_TOP_SYMBOL"), HOLD180_DROP_TOP_SYMBOL_EXPECTED)
    )
    return {"ok": ok, "HOLD_180": h}


def attach_v14_hold(v18_rows: list[dict[str, Any]], v14_by_key: dict[tuple[Any, ...], dict[str, Any]]) -> int:
    n = 0
    for r in v18_rows:
        key = (str(r.get("date") or ""), str(r.get("symbol") or ""), float(r.get("fill_t") or 0.0))
        old = v14_by_key.get(key)
        if not old:
            n += 1
            r["V14_HOLD180"] = None
            continue
        r["V14_HOLD180"] = old.get("hold_180")
        a = r.get("HOLD180_RECOMPUTE_BPS")
        b = old.get("hold_180")
        if not _finite(a) or not _finite(b) or abs(float(a) - float(b)) > 1e-6:
            n += 1
    return n


def _done(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if (not r.get("EXIT_MISS")) and _finite(r.get("exit_bps")) and _finite(r.get("exit_bid"))]


def latency_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    xs = [r.get("exit_latency_sec") for r in _done(rows)]
    return {
        "N": len([v for v in xs if _finite(v)]),
        "mean": _mean(xs),
        "median": _median(xs),
        "p75": _pctl(xs, 75.0),
        "max": (max(float(v) for v in xs if _finite(v)) if any(_finite(v) for v in xs) else None),
        "SESSION_CLAMP_N": sum(1 for r in rows if r.get("session_clamped")),
    }


def _sign_n(xs: list[float]) -> dict[str, int]:
    win = sum(1 for v in xs if v > 1e-12)
    loss = sum(1 for v in xs if v < -1e-12)
    flat = len(xs) - win - loss
    return {"WIN_N": win, "LOSS_N": loss, "FLAT_N": flat}


def vs_hold180(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ds = []
    for r in _done(rows):
        if not _finite(r.get("V14_HOLD180")):
            continue
        ds.append(float(r["exit_bps"]) - float(r["V14_HOLD180"]))
        r["delta_vs_v14_hold180"] = ds[-1]
    return {
        "N": len(ds),
        "mean": _mean(ds),
        "median": _median(ds),
        "worst": (min(ds) if ds else None),
        "best": (max(ds) if ds else None),
    }


def policy_pack(rows: list[dict[str, Any]], *, integrity_ok: bool) -> dict[str, Any]:
    done = _done(rows)
    for r in done:
        r["markout_180"] = r.get("exit_bps")
        r["pnl_yen_100"] = r.get("pnl_yen_100")
    bps = [float(r["exit_bps"]) for r in done]
    yen = [float(r["pnl_yen_100"]) for r in done if _finite(r.get("pnl_yen_100"))]
    signs = _sign_n(bps)
    yen_sum = summarize_pnl_yen_100(done)
    daily = day_rows(done, list(ELIGIBLE_DAYS))
    day_signs = day_sign_counts(daily, "MARKOUT180_MEAN")
    conc = concentration(done, "markout_180") if done else {}
    top = symbol_pack(done, "markout_180").get("TOP_SYMBOL") if done else None
    drop = drop_symbol_mean(done, str(top or ""), "markout_180") if done else None
    drop3 = drop_top3_mean(done, "markout_180") if done else None
    mean_bps = _mean(bps)
    median_bps = _median(bps)
    pos = int(day_signs.get("POSITIVE_DAY_N") or 0)
    neg = int(day_signs.get("NEGATIVE_DAY_N") or 0)
    ex_best = conc.get("EX_BEST_DAY_MARKOUT")
    ex_top3 = conc.get("EX_TOP3_DAY_MARKOUT")
    n = len(done)
    supported = bool(
        n == int(E4_FILLED_N_EXPECTED)
        and _gt0(mean_bps)
        and _gt0(median_bps)
        and pos > neg
        and _gt0(ex_best)
        and _gt0(ex_top3)
        and _gt0(drop)
        and bool(integrity_ok)
    )
    edge_pos = bool(_gt0(mean_bps) and _gt0(median_bps))
    return {
        "POLICY_ID": POLICY_ID,
        "EXIT_POLICY": EXIT_POLICY_NAME,
        "TRADE_N": n,
        "MISS_N": sum(1 for r in rows if r.get("EXIT_MISS")),
        **signs,
        "WIN_RATE": (float(signs["WIN_N"]) / float(n)) if n else None,
        "MEAN_BPS": mean_bps,
        "MEDIAN_BPS": median_bps,
        "TOTAL_PNL_YEN_100": yen_sum.get("total_pnl_yen_100"),
        "AVG_PNL_YEN_100": yen_sum.get("avg_pnl_yen_100"),
        "PROFIT_FACTOR_YEN_100": yen_sum.get("profit_factor_yen_100"),
        "GROSS_PROFIT_YEN_100": yen_sum.get("gross_profit_yen_100"),
        "GROSS_LOSS_YEN_100": yen_sum.get("gross_loss_yen_100"),
        "MAX_DRAWDOWN_YEN_100": maxdd(done, time_key="actual_exit_quote_time", pnl_key="pnl_yen_100") if yen else None,
        "POSITIVE_DAY_N": pos,
        "NEGATIVE_DAY_N": neg,
        "FLAT_DAY_N": day_signs.get("ZERO_DAY_N"),
        "EX_BEST_DAY": ex_best,
        "EX_TOP3_DAY": ex_top3,
        "DROP_TOP_SYMBOL": drop,
        "DROP_TOP3_SYMBOL": drop3,
        "TOP_SYMBOL": top,
        "BEST_DAY": conc.get("BEST_DAY"),
        "LATENCY": latency_pack(rows),
        "DELTA_VS_V14_HOLD180": vs_hold180(rows),
        "EDGE_POSITIVE": edge_pos,
        "FIXED180_DEVELOPMENT_SUPPORTED": supported,
    }


def decision_case(
    *,
    identity_ok: bool,
    integrity_ok: bool,
    pack: dict[str, Any],
) -> dict[str, Any]:
    if not identity_ok or not integrity_ok:
        return {
            "CASE": "D",
            "VERDICT": "SIMPLE_TECH_V18_INVALID",
            "NEXT": "STOP. Identity/causal/quote integrity failed. Do not freeze FIXED180.",
            "FIXED180_DEVELOPMENT_SUPPORTED": False,
            "EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT": False,
            "EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT": False,
            "DEVELOPMENT_EXIT_POLICY": None,
        }
    if pack.get("FIXED180_DEVELOPMENT_SUPPORTED"):
        return {
            "CASE": "A",
            "VERDICT": "SIMPLE_TECH_V18_FIXED180_EXIT_DEVELOPMENT_SUPPORTED",
            "NEXT": (
                "EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT=true for FIXED180_FIRST_CAUSAL_BID. "
                "EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT=false. "
                "Next run: structure verification — freeze current Bid execution or study EXIT execution separately. "
                "TRUE_OOS=false. EXIT_CERTIFIED=false."
            ),
            "FIXED180_DEVELOPMENT_SUPPORTED": True,
            "EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT": True,
            "EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT": False,
            "DEVELOPMENT_EXIT_POLICY": EXIT_POLICY_NAME,
        }
    if pack.get("EDGE_POSITIVE"):
        return {
            "CASE": "B",
            "VERDICT": "SIMPLE_TECH_V18_FIXED180_EXIT_FRAGILE",
            "NEXT": "STOP. FIXED180 is positive but robustness failed. Do not freeze. EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT=false.",
            "FIXED180_DEVELOPMENT_SUPPORTED": False,
            "EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT": False,
            "EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT": False,
            "DEVELOPMENT_EXIT_POLICY": None,
        }
    return {
        "CASE": "C",
        "VERDICT": "SIMPLE_TECH_V18_FIXED180_EXIT_NOT_SUPPORTED",
        "NEXT": "STOP. FIXED180 economic edge <= 0. Do not freeze. EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT=false.",
        "FIXED180_DEVELOPMENT_SUPPORTED": False,
        "EXIT_SIGNAL_SPEC_FROZEN_DEVELOPMENT": False,
        "EXIT_EXECUTION_SPEC_FROZEN_DEVELOPMENT": False,
        "DEVELOPMENT_EXIT_POLICY": None,
    }


def policy_summary_row(pack: dict[str, Any]) -> dict[str, Any]:
    lat = dict(pack.get("LATENCY") or {})
    dlt = dict(pack.get("DELTA_VS_V14_HOLD180") or {})
    return {
        "POLICY": pack.get("EXIT_POLICY"),
        "TRADE_N": pack.get("TRADE_N"),
        "WIN_N": pack.get("WIN_N"),
        "LOSS_N": pack.get("LOSS_N"),
        "FLAT_N": pack.get("FLAT_N"),
        "WIN_RATE": pack.get("WIN_RATE"),
        "MEAN_BPS": pack.get("MEAN_BPS"),
        "MEDIAN_BPS": pack.get("MEDIAN_BPS"),
        "TOTAL_PNL_YEN_100": pack.get("TOTAL_PNL_YEN_100"),
        "PROFIT_FACTOR_YEN_100": pack.get("PROFIT_FACTOR_YEN_100"),
        "MAX_DRAWDOWN_YEN_100": pack.get("MAX_DRAWDOWN_YEN_100"),
        "POS_DAY": pack.get("POSITIVE_DAY_N"),
        "NEG_DAY": pack.get("NEGATIVE_DAY_N"),
        "EX_BEST_DAY": pack.get("EX_BEST_DAY"),
        "EX_TOP3_DAY": pack.get("EX_TOP3_DAY"),
        "DROP_TOP_SYMBOL": pack.get("DROP_TOP_SYMBOL"),
        "LAT_MEAN": lat.get("mean"),
        "LAT_MEDIAN": lat.get("median"),
        "LAT_P75": lat.get("p75"),
        "LAT_MAX": lat.get("max"),
        "DELTA_MEAN": dlt.get("mean"),
        "DELTA_MEDIAN": dlt.get("median"),
        "DELTA_WORST": dlt.get("worst"),
        "SUPPORTED": pack.get("FIXED180_DEVELOPMENT_SUPPORTED"),
    }
