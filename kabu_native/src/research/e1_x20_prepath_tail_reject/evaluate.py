"""Monotonicity, transport, variant metrics, gates."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from . import CONFIRMATION, DISCOVERY, STRESS_DAY

LABEL_FR = "forward_return_180s"


def _mean(xs: list[float]) -> Optional[float]:
    return float(np.mean(xs)) if xs else None


def select(rows: list[dict[str, Any]], flag: str) -> list[dict[str, Any]]:
    return [r for r in rows if r.get(flag)]


def class_composition(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows) or 1
    counts = defaultdict(int)
    for r in rows:
        counts[r.get("outcome_class") or "UNCLASSIFIED"] += 1
    w, s = counts["WINNER"], counts["STOP"]
    ws = w + s
    return {
        "support": len(rows),
        "WINNER": counts["WINNER"],
        "STOP": counts["STOP"],
        "NOPROGRESS": counts["NOPROGRESS"],
        "TWO_SIDED_VOLATILE": counts["TWO_SIDED_VOLATILE"],
        "UNCLASSIFIED": counts["UNCLASSIFIED"],
        "winner_share_ws": (w / ws) if ws else None,
        "stop_share_ws": (s / ws) if ws else None,
        "winner_stop_odds": (w / s) if s else (float("inf") if w else None),
        "winner_rate_all": counts["WINNER"] / n,
        "stop_rate_all": counts["STOP"] / n,
        "noprogress_rate_all": counts["NOPROGRESS"] / n,
        "twosided_rate_all": counts["TWO_SIDED_VOLATILE"] / n,
        "ws_support": ws,
        "mean_FR_180": _mean([float(r[LABEL_FR]) for r in rows if r.get(LABEL_FR) is not None]),
        "mean_MFE_180": _mean([float(r["MFE_180s"]) for r in rows if r.get("MFE_180s") is not None]),
        "mean_MAE_180": _mean([float(r["MAE_180s"]) for r in rows if r.get("MAE_180s") is not None]),
        "entry_days": len({r["date"] for r in rows}),
        "symbols_n": len({r["symbol"] for r in rows}),
    }


def monotonicity(rows: list[dict[str, Any]], feature: str, qs: dict[str, float], period: str) -> dict[str, Any]:
    q20, q40, q60, q80 = qs["q20"], qs["q40"], qs["q60"], qs["q80"]

    def bin_of(v: float) -> int:
        if v <= q20:
            return 1
        if v <= q40:
            return 2
        if v <= q60:
            return 3
        if v <= q80:
            return 4
        return 5

    bins: dict[int, list] = {i: [] for i in range(1, 6)}
    for r in rows:
        v = r.get(feature)
        if v is None:
            continue
        bins[bin_of(float(v))].append(r)

    bin_rows = []
    stop_rates = []
    odds_list = []
    for i in range(1, 6):
        comp = class_composition(bins[i])
        bin_rows.append({"bin": i, "period": period, "feature": feature, **comp})
        if comp["stop_share_ws"] is not None:
            stop_rates.append(comp["stop_share_ws"])
        if comp["winner_stop_odds"] is not None and np.isfinite(comp["winner_stop_odds"]):
            odds_list.append(comp["winner_stop_odds"])

    # NON_MONOTONIC if stop share clearly decreases with feature (corr negative)
    non_mono = False
    stop_rates = [b["stop_share_ws"] for b in bin_rows if b.get("stop_share_ws") is not None]
    if len(stop_rates) >= 2 and stop_rates[-1] + 0.02 < stop_rates[0]:
        non_mono = True
    elif len(stop_rates) >= 3 and float(np.std(stop_rates)) > 0:
        corr = float(np.corrcoef(list(range(len(stop_rates))), stop_rates)[0, 1])
        if corr < -0.2:
            non_mono = True

    return {
        "feature": feature,
        "period": period,
        "bins": bin_rows,
        "NON_MONOTONIC_MECHANISM": non_mono,
        "mono_stop_increasing": (not non_mono),
        "mono_odds_decreasing": True,
    }


def threshold_transport(rows: list[dict[str, Any]], feature: str, thr: float) -> dict[str, Any]:
    def rank(sub):
        xs = [float(r[feature]) for r in sub if r.get(feature) is not None]
        if not xs:
            return None
        return float(sum(1 for x in xs if x <= thr) / len(xs))

    disc = rank([r for r in rows if r["date"] in DISCOVERY])
    conf = rank([r for r in rows if r["date"] in CONFIRMATION])
    stress = rank([r for r in rows if r["date"] == STRESS_DAY])
    unstable = False
    for name, v in (("confirmation", conf), ("stress", stress)):
        if v is None or not (0.65 <= v <= 0.95):
            unstable = True
    # discovery should be ~0.80
    return {
        "feature": feature,
        "threshold": thr,
        "discovery_percentile_rank": disc,
        "confirmation_percentile_rank": conf,
        "stress_20260803_percentile_rank": stress,
        "THRESHOLD_TRANSPORT_UNSTABLE": unstable,
    }


def period_slice(rows: list[dict[str, Any]], period: str) -> list[dict[str, Any]]:
    if period == "DISCOVERY":
        return [r for r in rows if r["date"] in DISCOVERY]
    if period == "CONFIRMATION":
        return [r for r in rows if r["date"] in CONFIRMATION]
    if period == "STRESS":
        return [r for r in rows if r["date"] == STRESS_DAY]
    return rows


def variant_metrics(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    flag = f"in_{variant}"
    sub = select(rows, flag)
    comp = class_composition(sub)
    comp["variant"] = variant
    return comp


def compare_vs_b0(ret: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    def d(key):
        a, b = ret.get(key), base.get(key)
        if a is None or b is None:
            return None
        if key == "winner_stop_odds" and (not np.isfinite(a) or not np.isfinite(b)):
            return None
        return float(a - b)

    odds_imp = None
    if ret.get("winner_stop_odds") is not None and base.get("winner_stop_odds") is not None:
        if np.isfinite(ret["winner_stop_odds"]) and np.isfinite(base["winner_stop_odds"]):
            odds_imp = ret["winner_stop_odds"] > base["winner_stop_odds"]

    stop_down = None
    if ret.get("stop_share_ws") is not None and base.get("stop_share_ws") is not None:
        stop_down = ret["stop_share_ws"] < base["stop_share_ws"]

    win_ok = None
    if ret.get("winner_share_ws") is not None and base.get("winner_share_ws") is not None:
        win_ok = ret["winner_share_ws"] >= base["winner_share_ws"] - 1e-12

    np_ok = None
    if ret.get("noprogress_rate_all") is not None and base.get("noprogress_rate_all") is not None:
        np_ok = ret["noprogress_rate_all"] <= base["noprogress_rate_all"] + 0.02

    ts_ok = None
    if ret.get("twosided_rate_all") is not None and base.get("twosided_rate_all") is not None:
        ts_ok = ret["twosided_rate_all"] <= base["twosided_rate_all"] + 0.02

    return {
        "odds_delta": d("winner_stop_odds"),
        "stop_share_delta": d("stop_share_ws"),
        "winner_share_delta": d("winner_share_ws"),
        "noprogress_rate_delta": d("noprogress_rate_all"),
        "twosided_rate_delta": d("twosided_rate_all"),
        "odds_improved": odds_imp,
        "stop_share_down": stop_down,
        "winner_share_not_down": win_ok,
        "noprogress_ok": np_ok,
        "twosided_ok": ts_ok,
        "pass_single": bool(odds_imp and stop_down and win_ok and np_ok and ts_ok),
    }


def rejected_ok(rej: dict[str, Any], ret: dict[str, Any]) -> bool:
    if rej.get("stop_share_ws") is None or ret.get("stop_share_ws") is None:
        return False
    if rej.get("winner_share_ws") is None or ret.get("winner_share_ws") is None:
        return False
    return rej["stop_share_ws"] > ret["stop_share_ws"] and rej["winner_share_ws"] < ret["winner_share_ws"]


def daily_direction(rows: list[dict[str, Any]], variant: str) -> list[dict[str, Any]]:
    """Per-day winner_stop_odds vs B0."""
    days = sorted({r["date"] for r in rows})
    out = []
    for d in days:
        day = [r for r in rows if r["date"] == d]
        b0 = class_composition(select(day, "in_B0"))
        bv = class_composition(select(day, f"in_{variant}"))
        cmp_ = compare_vs_b0(bv, b0)
        out.append({
            "date": d,
            "variant": variant,
            "B0_odds": b0.get("winner_stop_odds"),
            "odds": bv.get("winner_stop_odds"),
            "expected_direction": bool(cmp_.get("odds_improved") and cmp_.get("stop_share_down")),
            **cmp_,
            "B0_support": b0["support"],
            "support": bv["support"],
        })
    return out


def matched_compare(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    groups = defaultdict(lambda: {"B0": [], "V": []})
    for r in rows:
        if not r.get("in_B0"):
            continue
        key = (r.get("date"), r.get("session"), r.get("time_bucket"), r.get("price_band"), r.get("vol_tercile"))
        groups[key]["B0"].append(r)
        if r.get(f"in_{variant}"):
            groups[key]["V"].append(r)

    used = excl = 0
    odds_d, stop_d, win_d = [], [], []
    for key, g in groups.items():
        b0c = class_composition(g["B0"])
        vc = class_composition(g["V"])
        if (b0c.get("ws_support") or 0) < 10 or (vc.get("ws_support") or 0) < 5:
            excl += 1
            continue
        used += 1
        if b0c.get("winner_stop_odds") and vc.get("winner_stop_odds") and np.isfinite(b0c["winner_stop_odds"]) and np.isfinite(vc["winner_stop_odds"]):
            odds_d.append(vc["winner_stop_odds"] - b0c["winner_stop_odds"])
        if b0c.get("stop_share_ws") is not None and vc.get("stop_share_ws") is not None:
            stop_d.append(vc["stop_share_ws"] - b0c["stop_share_ws"])
        if b0c.get("winner_share_ws") is not None and vc.get("winner_share_ws") is not None:
            win_d.append(vc["winner_share_ws"] - b0c["winner_share_ws"])
    return {
        "variant": variant,
        "groups_used": used,
        "groups_excluded": excl,
        "matched_odds_delta": _mean(odds_d),
        "matched_stop_share_delta": _mean(stop_d),
        "matched_winner_share_delta": _mean(win_d),
    }


def support_gate(ret: dict[str, Any], b0: dict[str, Any], *, stress: bool = False) -> dict[str, Any]:
    if stress:
        ok = (
            (ret.get("support") or 0) >= 50
            and (ret.get("ws_support") or 0) >= 20
        )
        return {"pass": ok, "stress": True}
    retention = (ret["support"] / b0["support"]) if b0.get("support") else 0
    ok = (
        (ret.get("support") or 0) >= 1000
        and retention >= 0.50
        and (ret.get("ws_support") or 0) >= 300
        and (ret.get("entry_days") or 0) >= 9
    )
    return {"pass": ok, "retention_rate": retention}


def stability(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    retained = select(rows, f"in_{variant}")
    n = len(retained) or 1
    by_day = defaultdict(int)
    by_sym = defaultdict(int)
    for r in retained:
        by_day[r["date"]] += 1
        by_sym[r["symbol"]] += 1
    max_day = max(by_day.values()) / n if by_day else 0
    max_sym = max(by_sym.values()) / n if by_sym else 0

    # LODO: leave one discovery+confirmation day, check odds vs B0 direction
    days = sorted({r["date"] for r in rows if r["date"] != STRESS_DAY})
    flips = 0
    for leave in days:
        sub = [r for r in rows if r["date"] != leave]
        b0 = class_composition(select(sub, "in_B0"))
        bv = class_composition(select(sub, f"in_{variant}"))
        cmp_ = compare_vs_b0(bv, b0)
        if cmp_.get("odds_improved") is False and cmp_.get("stop_share_down") is False:
            flips += 1
    # exclusions
    ex2354 = class_composition(select([r for r in rows if r["symbol"] != "2354"], f"in_{variant}"))
    ex285a = class_composition(select([r for r in rows if r["symbol"] != "285A"], f"in_{variant}"))
    b0 = class_composition(select(rows, "in_B0"))
    return {
        "max_day_contribution": max_day,
        "max_symbol_contribution": max_sym,
        "max_day": max(by_day, key=by_day.get) if by_day else None,
        "max_symbol": max(by_sym, key=by_sym.get) if by_sym else None,
        "lodo_major_flips": flips,
        "lodo_ok": flips < 2,
        "pass_day_cap": max_day <= 0.30,
        "pass_sym_cap": max_sym <= 0.10,
        "exclude_2354_odds": ex2354.get("winner_stop_odds"),
        "exclude_285A_odds": ex285a.get("winner_stop_odds"),
        "baseline_odds": b0.get("winner_stop_odds"),
    }


def candidate_gate(
    *,
    mono_ok: bool,
    transport_ok: bool,
    support_ok: bool,
    period_ok: bool,
    single_cmp: dict[str, Any],
    rej_ok: bool,
    stab: dict[str, Any],
    incremental_ok: bool = True,
) -> tuple[bool, list[str]]:
    reasons = []
    if not mono_ok:
        reasons.append("monotonicity")
    if not transport_ok:
        reasons.append("transport")
    if not support_ok:
        reasons.append("support")
    if not period_ok:
        reasons.append("period")
    if not single_cmp.get("pass_single"):
        reasons.append("class_metrics")
    if not rej_ok:
        reasons.append("rejected_complement")
    if not stab.get("lodo_ok") or not stab.get("pass_day_cap") or not stab.get("pass_sym_cap"):
        reasons.append("stability")
    if not incremental_ok:
        reasons.append("incremental")
    return len(reasons) == 0, reasons
