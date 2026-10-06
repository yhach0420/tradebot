"""Stage contribution, failure taxonomy, component RCA, primary deficiency. Labels are diagnostic-only."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.am_entry_profit_improvement.metrics import economic_pack, paired_delta
from research.entry_rank_shape_audit.oof import delta_series_stats
from research.simple_tech_entry_family.stages import bad_entry, good_upmove

STAGE_ORDER = ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10")
STAGE_NAME = {
    "s0": "S0_EVALUABLE",
    "s1": "S1_TREND_UP",
    "s2": "S2_PULLBACK",
    "s3": "S3_RCI_REVERSAL",
    "s4": "S4_PRICE_ACTION",
    "s5": "S5_VOLUME_CONFIRM",
    "s6": "S6_BOARD_SUPPORT",
    "s7": "S7_EXECUTION_ELIGIBLE",
    "s8": "S8_PENDING",
    "s9": "S9_FILL",
    "s10": "S10_TRADE",
}
FAIL_ORDER = (
    "F1_TREND_FALSE",
    "F2_PULLBACK_FALSE",
    "F3_REVERSAL_FALSE",
    "F4_VOLUME_FALSE_CONFIRM",
    "F5_PRICE_TRIGGER_LATE",
    "F6_BOARD_FALSE_SUPPORT",
    "F7_EXECUTION_COST",
    "F8_EXIT_GIVEBACK",
    "F9_OTHER",
)
DEF_MAP = {
    "F1_TREND_FALSE": ("TREND_STATE_INSUFFICIENT", "trend"),
    "F2_PULLBACK_FALSE": ("PULLBACK_STATE_INSUFFICIENT", "pullback"),
    "F3_REVERSAL_FALSE": ("REVERSAL_CONFIRMATION_INSUFFICIENT", "reversal"),
    "F4_VOLUME_FALSE_CONFIRM": ("VOLUME_DIRECTION_OR_QUALITY_INSUFFICIENT", "volume"),
    "F5_PRICE_TRIGGER_LATE": ("ENTRY_TRIGGER_TOO_LATE", "trigger"),
    "F6_BOARD_FALSE_SUPPORT": ("BOARD_VETO_NOT_USEFUL", "board"),
}


def _mean(xs: list[Any]) -> Optional[float]:
    vs = [float(x) for x in xs if x is not None and x == x]
    if not vs:
        return None
    return float(np.mean(vs))


def _pack_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    f1 = [r.get("fwd_1m") for r in rows]
    f3 = [r.get("fwd_3m") for r in rows]
    f5 = [r.get("fwd_5m") for r in rows]
    mfe = [r.get("mfe_5m") for r in rows]
    mae = [r.get("mae_5m") for r in rows]
    up = sum(1 for r in rows if int(r.get("up_first") or 0) == 1)
    dn = sum(1 for r in rows if int(r.get("down_first") or 0) == 1)
    good = [r for r in rows if good_upmove(r)]
    bad = [r for r in rows if bad_entry(r)]
    return {
        "N": len(rows),
        "FORWARD_1M": _mean(f1),
        "FORWARD_3M": _mean(f3),
        "FORWARD_5M": _mean(f5),
        "MFE": _mean(mfe),
        "MAE": _mean(mae),
        "UP_FIRST_RATE": (up / len(rows)) if rows else None,
        "DOWN_FIRST_RATE": (dn / len(rows)) if rows else None,
        "GOOD_UPMOVE_N": len(good),
        "BAD_ENTRY_N": len(bad),
    }


def stage_contribution(opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    prev = list(opps)
    for i, st in enumerate(STAGE_ORDER):
        after = [r for r in prev if r.get(st)] if st != "s0" else list(prev)
        if st == "s0":
            after = [r for r in opps if r.get("s0")]
            prev_set = after
        bq = _pack_quality(prev if st != "s0" else after)
        aq = _pack_quality(after)
        b_good = {id(r) for r in prev if good_upmove(r)} if st != "s0" else {id(r) for r in after if good_upmove(r)}
        a_good = {id(r) for r in after if good_upmove(r)}
        b_bad = {id(r) for r in prev if bad_entry(r)} if st != "s0" else {id(r) for r in after if bad_entry(r)}
        a_bad = {id(r) for r in after if bad_entry(r)}
        row = {
            "stage": STAGE_NAME[st],
            "stage_id": st,
            "BEFORE_N": int(bq["N"]) if st != "s0" else int(aq["N"]),
            "AFTER_N": int(aq["N"]),
            "BEFORE_FORWARD_1M": bq["FORWARD_1M"] if st != "s0" else None,
            "AFTER_FORWARD_1M": aq["FORWARD_1M"],
            "BEFORE_FORWARD_3M": bq["FORWARD_3M"] if st != "s0" else None,
            "AFTER_FORWARD_3M": aq["FORWARD_3M"],
            "BEFORE_FORWARD_5M": bq["FORWARD_5M"] if st != "s0" else None,
            "AFTER_FORWARD_5M": aq["FORWARD_5M"],
            "BEFORE_MFE": bq["MFE"] if st != "s0" else None,
            "AFTER_MFE": aq["MFE"],
            "BEFORE_MAE": bq["MAE"] if st != "s0" else None,
            "AFTER_MAE": aq["MAE"],
            "UP_FIRST_RATE": aq["UP_FIRST_RATE"],
            "DOWN_FIRST_RATE": aq["DOWN_FIRST_RATE"],
            "GOOD_UPMOVE_RETAINED_N": len(a_good),
            "GOOD_UPMOVE_LOST_N": len(b_good - a_good) if st != "s0" else 0,
            "BAD_ENTRY_REMOVED_N": len(b_bad - a_bad) if st != "s0" else 0,
            "BAD_ENTRY_RETAINED_N": len(a_bad),
        }
        out.append(row)
        prev = after
        _ = prev_set
        _ = i
    return out


def classify_trade(tr: dict[str, Any]) -> str:
    src = tr.get("src") or tr
    pnl = float(tr.get("pnl_yen_100") or 0.0)
    if pnl > 1e-9:
        return "WIN"
    if abs(pnl) <= 1e-9:
        return "FLAT"
    mfe = src.get("mfe_path")
    mae = src.get("mae_path")
    _ = mae
    if mfe is not None and float(mfe) > 0 and pnl < -1e-9:
        return "F8_EXIT_GIVEBACK"
    spread = src.get("spread_bps")
    f1 = src.get("fwd_1m")
    if pnl < -1e-9 and spread is not None and f1 is not None and float(f1) * 10000.0 <= float(spread) + 1e-9:
        return "F7_EXECUTION_COST"
    run = src.get("fwd_1m")
    if pnl < -1e-9 and src.get("mfe_5m") is not None and float(src["mfe_5m"]) < 0.001 and run is not None and float(run) <= 0:
        # already extended; little remaining MFE
        if src.get("close") and src.get("high") and src.get("ema9"):
            if float(src["close"]) >= float(src.get("bb_upper") or 1e18) * 0.998:
                return "F5_PRICE_TRIGGER_LATE"
        if src.get("mfe_path") is not None and float(src["mfe_path"]) < 0.0015:
            return "F5_PRICE_TRIGGER_LATE"
    upv = float(src.get("up_vol") or 0.0)
    dnv = float(src.get("down_vol") or 0.0)
    askv = float(src.get("ask_vol") or 0.0)
    bidv = float(src.get("bid_vol") or 0.0)
    if pnl < -1e-9 and (dnv > upv + 1e-12 or bidv > askv + 1e-12):
        return "F4_VOLUME_FALSE_CONFIRM"
    if pnl < -1e-9 and src.get("fwd_3m") is not None and float(src["fwd_3m"]) <= 0 and int(src.get("down_first") or 0) == 1:
        if src.get("rci9") is not None and float(src["rci9"]) > -80:
            return "F3_REVERSAL_FALSE"
    if pnl < -1e-9 and src.get("low") is not None and src.get("bb_lower") is not None:
        if float(src["low"]) <= float(src["bb_lower"]) + 1e-12:
            return "F2_PULLBACK_FALSE"
    if pnl < -1e-9:
        e9 = src.get("ema9")
        e21 = src.get("ema21")
        e21l = src.get("ema21_lag3")
        slope_ok = e21 is not None and e21l is not None and float(e21) > float(e21l)
        hh = bool(src.get("hh_hl"))
        vwap_rel = src.get("vwap_rel")
        if (e9 is not None and e21 is not None and float(e9) <= float(e21)) or (not slope_ok) or (
            vwap_rel is not None and float(vwap_rel) < -0.002 and not hh
        ):
            return "F1_TREND_FALSE"
        aq = src.get("ask_qty")
        bq = src.get("bid_qty")
        if aq is not None and bq is not None and float(bq) > 0 and float(aq) >= 1.5 * float(bq):
            return "F6_BOARD_FALSE_SUPPORT"
    return "F9_OTHER"


def failure_taxonomy(trades: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows = []
    agg: dict[str, dict[str, Any]] = {
        k: {"code": k, "N": 0, "gross_loss": 0.0, "days": set(), "symbols": set()} for k in FAIL_ORDER
    }
    for tr in trades:
        code = classify_trade(tr)
        tr["failure"] = code
        src = tr.get("src") or {}
        pnl = float(tr.get("pnl_yen_100") or 0.0)
        rows.append(
            {
                "date": tr.get("date"),
                "symbol": tr.get("symbol"),
                "t0": tr.get("t0"),
                "pnl_yen_100": pnl,
                "failure": code,
                "mfe_path": src.get("mfe_path"),
                "mae_path": src.get("mae_path"),
                "fwd_1m": src.get("fwd_1m"),
                "fwd_3m": src.get("fwd_3m"),
                "up_vol": src.get("up_vol"),
                "down_vol": src.get("down_vol"),
                "ask_vol": src.get("ask_vol"),
                "bid_vol": src.get("bid_vol"),
                "exit_reason": tr.get("exit_reason"),
            }
        )
        if code in agg:
            agg[code]["N"] += 1
            if pnl < 0:
                agg[code]["gross_loss"] += -pnl
                agg[code]["days"].add(str(tr.get("date") or ""))
                agg[code]["symbols"].add(str(tr.get("symbol") or ""))
    out_agg = []
    for k in FAIL_ORDER:
        rec = agg[k]
        out_agg.append(
            {
                "code": k,
                "N": rec["N"],
                "gross_loss": rec["gross_loss"],
                "day_n": len(rec["days"]),
                "symbol_n": len(rec["symbols"]),
                "days": ",".join(sorted(rec["days"])),
            }
        )
    return rows, {r["code"]: r for r in out_agg}


def _loss_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in trades if float(t.get("pnl_yen_100") or 0.0) < -1e-9]


def trend_rca(trades: list[dict[str, Any]], opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    losses = [t for t in _loss_trades(trades) if t.get("failure") == "F1_TREND_FALSE" or True]
    rows = []
    for t in _loss_trades(trades):
        src = t.get("src") or {}
        e9 = src.get("ema9")
        e21 = src.get("ema21")
        e21l = src.get("ema21_lag3")
        slope = (float(e21) - float(e21l)) if e21 is not None and e21l is not None else None
        rows.append(
            {
                "date": t.get("date"),
                "symbol": t.get("symbol"),
                "ema9_gt_ema21": (e9 is not None and e21 is not None and float(e9) > float(e21)),
                "ema21_slope": slope,
                "hh_hl": src.get("hh_hl"),
                "vwap_rel": src.get("vwap_rel"),
                "ret_20": src.get("ret_20"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "bounce_suspect": bool(slope is not None and slope <= 0) or (src.get("vwap_rel") is not None and float(src["vwap_rel"]) < 0 and not src.get("hh_hl")),
            }
        )
    s1 = [r for r in opps if r.get("s1")]
    return [
        {
            "LOSS_N": len(_loss_trades(trades)),
            "F1_N": sum(1 for t in trades if t.get("failure") == "F1_TREND_FALSE"),
            "S1_N": len(s1),
            "S1_FWD3": _mean([r.get("fwd_3m") for r in s1]),
            "bounce_suspect_n": sum(1 for r in rows if r.get("bounce_suspect")),
            "mean_ema21_slope_loss": _mean([r.get("ema21_slope") for r in rows]),
            "mean_vwap_rel_loss": _mean([r.get("vwap_rel") for r in rows]),
        }
    ]


def rci_rca(trades: list[dict[str, Any]], opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    s3 = [r for r in opps if r.get("s3")]
    failed = 0
    for t in _loss_trades(trades):
        src = t.get("src") or {}
        if src.get("fwd_3m") is not None and float(src["fwd_3m"]) <= 0:
            failed += 1
    return [
        {
            "S3_N": len(s3),
            "S3_FWD3": _mean([r.get("fwd_3m") for r in s3]),
            "failed_bounce_loss_n": failed,
            "F3_N": sum(1 for t in trades if t.get("failure") == "F3_REVERSAL_FALSE"),
            "mean_rci_at_signal": _mean([(t.get("src") or {}).get("rci9") for t in trades]),
            "mean_rci_prev": _mean([(t.get("src") or {}).get("rci9_prev") for t in trades]),
        }
    ]


def volume_rca(trades: list[dict[str, Any]], opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    s5 = [r for r in opps if r.get("s5")]
    loss = _loss_trades(trades)
    return [
        {
            "S5_N": len(s5),
            "S5_FWD3": _mean([r.get("fwd_3m") for r in s5]),
            "loss_n": len(loss),
            "mean_up_vol_loss": _mean([(t.get("src") or {}).get("up_vol") for t in loss]),
            "mean_down_vol_loss": _mean([(t.get("src") or {}).get("down_vol") for t in loss]),
            "mean_ask_vol_loss": _mean([(t.get("src") or {}).get("ask_vol") for t in loss]),
            "mean_bid_vol_loss": _mean([(t.get("src") or {}).get("bid_vol") for t in loss]),
            "downtick_dominated_loss_n": sum(
                1
                for t in loss
                if float((t.get("src") or {}).get("down_vol") or 0) > float((t.get("src") or {}).get("up_vol") or 0)
            ),
            "bid_side_dominated_loss_n": sum(
                1
                for t in loss
                if float((t.get("src") or {}).get("bid_vol") or 0) > float((t.get("src") or {}).get("ask_vol") or 0)
            ),
            "mean_vol_accel_loss": _mean([(t.get("src") or {}).get("vol_accel") for t in loss]),
            "F4_N": sum(1 for t in trades if t.get("failure") == "F4_VOLUME_FALSE_CONFIRM"),
        }
    ]


def trigger_rca(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for t in trades:
        src = t.get("src") or {}
        t0 = t.get("t0")
        pb = src.get("pullback_low_t")
        fill = t.get("fill_time")
        lat = (float(t0) - float(pb)) if t0 is not None and pb is not None else None
        fill_lat = (float(fill) - float(t0)) if fill is not None and t0 is not None else None
        pnl = float(t.get("pnl_yen_100") or 0.0)
        rows.append(
            {
                "date": t.get("date"),
                "symbol": t.get("symbol"),
                "pullback_low_t": pb,
                "rci_reversal_t": src.get("bar_minute"),
                "price_reclaim_t": src.get("bar_minute"),
                "local_breakout_t": src.get("bar_minute"),
                "signal_t": t0,
                "entry_t": fill,
                "signal_minus_pullback_sec": lat,
                "fill_minus_signal_sec": fill_lat,
                "winner": pnl > 1e-9,
                "pnl_yen_100": pnl,
                "mfe_5m": src.get("mfe_5m"),
                "fwd_1m": src.get("fwd_1m"),
            }
        )
    win = [r for r in rows if r.get("winner")]
    loss = [r for r in rows if not r.get("winner") and float(r.get("pnl_yen_100") or 0) < -1e-9]
    return [
        {
            "trade_n": len(rows),
            "win_mean_signal_lag_sec": _mean([r.get("signal_minus_pullback_sec") for r in win]),
            "loss_mean_signal_lag_sec": _mean([r.get("signal_minus_pullback_sec") for r in loss]),
            "F5_N": sum(1 for t in trades if t.get("failure") == "F5_PRICE_TRIGGER_LATE"),
            "win_mean_mfe5": _mean([r.get("mfe_5m") for r in win]),
            "loss_mean_mfe5": _mean([r.get("mfe_5m") for r in loss]),
        }
    ] + rows[:500]


def board_rca(opps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    s5 = [r for r in opps if r.get("s5")]
    pass_r = [r for r in s5 if r.get("s6")]
    veto_r = [r for r in s5 if not r.get("s6")]
    qp = _pack_quality(pass_r)
    qv = _pack_quality(veto_r)
    return [
        {
            "S5_N": len(s5),
            "BOARD_PASS_N": len(pass_r),
            "BOARD_VETO_N": len(veto_r),
            "PASS_FWD3": qp["FORWARD_3M"],
            "VETO_FWD3": qv["FORWARD_3M"],
            "PASS_GOOD_N": qp["GOOD_UPMOVE_N"],
            "VETO_GOOD_N": qv["GOOD_UPMOVE_N"],
            "PASS_BAD_N": qp["BAD_ENTRY_N"],
            "VETO_BAD_N": qv["BAD_ENTRY_N"],
            "veto_removes_bad": int(qv["BAD_ENTRY_N"] or 0),
            "veto_removes_good": int(qv["GOOD_UPMOVE_N"] or 0),
        }
    ]


def execution_rca(signals: list[dict[str, Any]], port: dict[str, Any]) -> list[dict[str, Any]]:
    sig = list(signals)
    pend = [r for r in sig if r.get("s8_pending") or r.get("s8")]
    filled = [r for r in sig if r.get("WOULD_FILL") and r.get("s9_fill")]
    nonfill = [r for r in sig if r.get("s8_pending") and not r.get("WOULD_FILL")]
    qf = _pack_quality(filled)
    qn = _pack_quality(nonfill)
    return [
        {
            "signal_n": len(sig),
            "pending_n": int(port.get("admitted_n") or len(pend)),
            "fill_n": int(port.get("fill_n") or len(filled)),
            "fill_rate": (float(port.get("fill_n") or 0) / float(port.get("admitted_n") or 1)) if port.get("admitted_n") else None,
            "cap_blocked": port.get("cap_blocked"),
            "same_symbol_blocked": port.get("same_symbol_blocked"),
            "filled_fwd3": qf["FORWARD_3M"],
            "nonfilled_fwd3": qn["FORWARD_3M"],
            "filled_good_n": qf["GOOD_UPMOVE_N"],
            "nonfilled_good_n": qn["GOOD_UPMOVE_N"],
            "expired_n": port.get("expired_n"),
        }
    ]


def exit_attribution(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    give = [t for t in trades if t.get("failure") == "F8_EXIT_GIVEBACK"]
    gp = sum(float((t.get("src") or {}).get("mfe_path") or 0.0) * float(t.get("fill_price") or 0.0) * 100.0 for t in give)
    return [
        {
            "TRADE_N": len(trades),
            "F8_N": len(give),
            "F8_GROSS_LOSS": sum(-float(t.get("pnl_yen_100") or 0.0) for t in give if float(t.get("pnl_yen_100") or 0) < 0),
            "C14_UNCHANGED": True,
            "note": "F8 does not negate V1 ENTRY. C14 change forbidden.",
            "approx_giveback_yen": gp,
        }
    ]


def robustness(pack: dict[str, Any]) -> dict[str, Any]:
    daily = list(pack.get("daily") or [])
    pnls = [float(r.get("pnl_yen_100") or 0.0) for r in daily]
    st = delta_series_stats(pnls) if pnls else {}
    ordered = sorted(pnls, reverse=True)
    best = ordered[0] if ordered else 0.0
    top3 = sum(ordered[:3]) if ordered else 0.0
    net = float(pack.get("net_pnl_yen_100") or 0.0)
    return {
        "positive_day_n": int(pack.get("positive_day_n") or 0),
        "negative_day_n": int(pack.get("negative_day_n") or 0),
        "zero_day_n": int(pack.get("zero_day_n") or 0),
        "daily_median": pack.get("median_daily_pnl"),
        "best_day": best,
        "best_day_share": (best / net) if abs(net) > 1e-12 else None,
        "top3_day_share": (top3 / net) if abs(net) > 1e-12 else None,
        "EX_BEST": st.get("ex_best_day"),
        "EX_TOP3": st.get("ex_top3_days"),
        "EX_BEST_NET": net - best,
        "EX_TOP3_NET": net - top3,
    }


def _stage_row(contrib: list[dict[str, Any]], stage_id: str) -> dict[str, Any]:
    for row in contrib:
        if str(row.get("stage_id") or "") == stage_id:
            return row
    return {}


def select_primary(
    tax: dict[str, dict[str, Any]],
    contrib: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    board: list[dict[str, Any]],
    days_n: int,
    execution: list[dict[str, Any]] | None = None,
    signal_days: int = 0,
) -> dict[str, Any]:
    """Pick one family component. Do not use empty F-codes. Do not treat F8 as V1 ENTRY failure."""
    losses = _loss_trades(trades)
    entry_codes = [k for k in FAIL_ORDER if k[1] in "123456"]
    ranked = []
    for code in entry_codes:
        rec = tax.get(code) or {"N": 0, "gross_loss": 0.0, "day_n": 0, "symbol_n": 0}
        n = int(rec.get("N") or 0)
        if n <= 0:
            continue
        ranked.append((n, float(rec.get("gross_loss") or 0.0), int(rec.get("day_n") or 0), code))
    ranked.sort(reverse=True)

    exe = (execution or [{}])[0]
    fill_n = int(exe.get("fill_n") or 0)
    pend_n = int(exe.get("pending_n") or 0)
    fill_rate = exe.get("fill_rate")
    filled_fwd = exe.get("filled_fwd3")
    nonfill_fwd = exe.get("nonfilled_fwd3")
    fill_cliff = (
        pend_n >= 10
        and fill_rate is not None
        and float(fill_rate) < 0.35
        and filled_fwd is not None
        and nonfill_fwd is not None
        and float(filled_fwd) < float(nonfill_fwd)
    )
    b0 = board[0] if board else {}
    veto_n = int(b0.get("BOARD_VETO_N") or 0)
    pass_fwd = b0.get("PASS_FWD3")
    veto_fwd = b0.get("VETO_FWD3")
    veto_good = int(b0.get("VETO_GOOD_N") or 0)
    veto_bad = int(b0.get("VETO_BAD_N") or 0)
    board_helps = (
        veto_n > 0
        and pass_fwd is not None
        and veto_fwd is not None
        and float(pass_fwd) > float(veto_fwd)
        and veto_bad >= veto_good
    )
    s5 = _stage_row(contrib, "s5")
    s1 = _stage_row(contrib, "s1")
    s9 = _stage_row(contrib, "s9")
    vol_hurts = (
        s5.get("BEFORE_FORWARD_3M") is not None
        and s5.get("AFTER_FORWARD_3M") is not None
        and float(s5["AFTER_FORWARD_3M"]) < float(s5["BEFORE_FORWARD_3M"])
    )
    trend_hurts = (
        s1.get("BEFORE_FORWARD_3M") is not None
        and s1.get("AFTER_FORWARD_3M") is not None
        and float(s1["AFTER_FORWARD_3M"]) < float(s1["BEFORE_FORWARD_3M"])
    )
    realized_loss = sum(-float(t.get("pnl_yen_100") or 0.0) for t in losses)
    cover = float(signal_days) / float(max(days_n, 1)) if signal_days else None

    # Spec 23: if execution discards the better technical signals, do not blame MA/BB/RCI/Volume.
    # Spec 21: that pattern is the late-trigger hypothesis. V2 may change trigger only. Fill rule stays frozen.
    if fill_cliff:
        return {
            "PRIMARY_DEFICIENCY": "ENTRY_TRIGGER_TOO_LATE",
            "PRIMARY_DEFICIENCY_GROSS_LOSS": float(realized_loss),
            "PRIMARY_DEFICIENCY_DAY_COVERAGE": cover if cover is not None else (int((tax.get("F7_EXECUTION_COST") or {}).get("day_n") or 0) / float(max(days_n, 1))),
            "V2_RECOMMENDED_COMPONENT": "trigger",
            "primary_fail_code": "S9_FILL_QUALITY_CLIFF",
            "reason": "FILLED_FORWARD_WORSE_THAN_NONFILLED_AND_LOW_FILL_RATE",
            "funnel_stage": s9.get("stage") or "S9_FILL",
            "days_n": days_n,
            "fill_rate": fill_rate,
            "filled_fwd3": filled_fwd,
            "nonfilled_fwd3": nonfill_fwd,
            "good_upmove_lost_at_fill": s9.get("GOOD_UPMOVE_LOST_N"),
            "board_helps": bool(board_helps),
            "volume_hurts_fwd3": bool(vol_hurts),
            "trend_hurts_fwd3": bool(trend_hurts),
            "f8_does_not_negate_entry": True,
            "execution_fill_frozen": True,
        }

    if ranked:
        chosen = ranked[0][3]
        rec = tax.get(chosen) or {}
        if int(rec.get("day_n") or 0) <= 1 and len(losses) >= 5:
            for n, gl, dn, code in ranked[1:]:
                if dn >= 2:
                    chosen = code
                    rec = tax.get(chosen) or {}
                    break
        name, comp = DEF_MAP.get(chosen, ("REVERSAL_CONFIRMATION_INSUFFICIENT", "reversal"))
        return {
            "PRIMARY_DEFICIENCY": name,
            "PRIMARY_DEFICIENCY_GROSS_LOSS": float(rec.get("gross_loss") or 0.0),
            "PRIMARY_DEFICIENCY_DAY_COVERAGE": int(rec.get("day_n") or 0) / float(max(days_n, 1)),
            "V2_RECOMMENDED_COMPONENT": comp,
            "primary_fail_code": chosen,
            "reason": "LOSS_MECHANISM_THEN_GROSS_LOSS_THEN_DAY_COVERAGE",
            "funnel_stage": s9.get("stage"),
            "days_n": days_n,
            "board_helps": bool(board_helps),
        }

    if veto_n > 0 and not board_helps:
        return {
            "PRIMARY_DEFICIENCY": "BOARD_VETO_NOT_USEFUL",
            "PRIMARY_DEFICIENCY_GROSS_LOSS": float(realized_loss),
            "PRIMARY_DEFICIENCY_DAY_COVERAGE": cover or 0.0,
            "V2_RECOMMENDED_COMPONENT": "board",
            "primary_fail_code": "BOARD_RCA",
            "reason": "BOARD_VETO_REMOVES_GOOD_OR_FAILS_TO_LIFT_FORWARD",
            "days_n": days_n,
        }
    if vol_hurts:
        return {
            "PRIMARY_DEFICIENCY": "VOLUME_DIRECTION_OR_QUALITY_INSUFFICIENT",
            "PRIMARY_DEFICIENCY_GROSS_LOSS": float(realized_loss),
            "PRIMARY_DEFICIENCY_DAY_COVERAGE": cover or 0.0,
            "V2_RECOMMENDED_COMPONENT": "volume",
            "primary_fail_code": "S5_QUALITY_DROP",
            "reason": "VOLUME_STAGE_FORWARD_3M_DECLINED",
            "days_n": days_n,
        }
    if trend_hurts:
        return {
            "PRIMARY_DEFICIENCY": "TREND_STATE_INSUFFICIENT",
            "PRIMARY_DEFICIENCY_GROSS_LOSS": float(realized_loss),
            "PRIMARY_DEFICIENCY_DAY_COVERAGE": cover or 0.0,
            "V2_RECOMMENDED_COMPONENT": "trend",
            "primary_fail_code": "S1_QUALITY_DROP",
            "reason": "TREND_STAGE_FORWARD_3M_DECLINED",
            "days_n": days_n,
        }
    return {
        "PRIMARY_DEFICIENCY": "REVERSAL_CONFIRMATION_INSUFFICIENT",
        "PRIMARY_DEFICIENCY_GROSS_LOSS": float(realized_loss),
        "PRIMARY_DEFICIENCY_DAY_COVERAGE": cover or 0.0,
        "V2_RECOMMENDED_COMPONENT": "reversal",
        "primary_fail_code": "FALLBACK",
        "reason": "NO_F1_F6_AND_NO_FILL_CLIFF",
        "days_n": days_n,
    }


def winner_loser_paths(trades: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    win, loss = [], []
    for t in trades:
        src = t.get("src") or {}
        row = {
            "date": t.get("date"),
            "symbol": t.get("symbol"),
            "pnl_yen_100": t.get("pnl_yen_100"),
            "failure": t.get("failure"),
            "pullback_low_t": src.get("pullback_low_t"),
            "bar_minute": src.get("bar_minute"),
            "t0": t.get("t0"),
            "fill_t": t.get("fill_time"),
            "exit_t": t.get("exit_time"),
            "rci9": src.get("rci9"),
            "rci9_prev": src.get("rci9_prev"),
            "volume": src.get("volume"),
            "up_vol": src.get("up_vol"),
            "down_vol": src.get("down_vol"),
            "ask_vol": src.get("ask_vol"),
            "bid_vol": src.get("bid_vol"),
            "fwd_1m": src.get("fwd_1m"),
            "fwd_3m": src.get("fwd_3m"),
            "fwd_5m": src.get("fwd_5m"),
            "mfe_5m": src.get("mfe_5m"),
            "mae_5m": src.get("mae_5m"),
            "mfe_path": src.get("mfe_path"),
            "mae_path": src.get("mae_path"),
            "up_first": src.get("up_first"),
            "down_first": src.get("down_first"),
            "exit_reason": t.get("exit_reason"),
        }
        if float(t.get("pnl_yen_100") or 0.0) > 1e-9:
            win.append(row)
        elif float(t.get("pnl_yen_100") or 0.0) < -1e-9:
            loss.append(row)
    return win, loss


def decide_verdict(pack: dict[str, Any], rob: dict[str, Any], current: dict[str, Any], primary: dict[str, Any]) -> dict[str, Any]:
    net = float(pack.get("net_pnl_yen_100") or 0.0)
    cur_net = float(current.get("net_pnl_yen_100") or current.get("NET") or 0.0)
    pf = pack.get("profit_factor")
    med = rob.get("daily_median")
    pos = int(rob.get("positive_day_n") or 0)
    neg = int(rob.get("negative_day_n") or 0)
    strong = (
        int(pack.get("trade_count") or 0) >= 10
        and net > cur_net
        and med is not None
        and float(med) > 0
        and pos > neg
        and float(rob.get("EX_BEST_NET") or 0) > 0
        and float(rob.get("EX_TOP3_NET") or 0) >= 0
    )
    if strong:
        verdict = "SIMPLE_TECH_V1_PROMISING"
        nxt = "DO_NOT_ADOPT_RUNTIME. Keep family open. Next is V2 only after precommit of one component."
    else:
        verdict = "SIMPLE_TECH_V1_DEFICIENCY_IDENTIFIED"
        nxt = (
            f"V2 changes only {primary.get('V2_RECOMMENDED_COMPONENT')} "
            f"to address {primary.get('PRIMARY_DEFICIENCY')}. Do not implement V2 in this run."
        )
    return {
        "VERDICT": verdict,
        "NEXT": nxt,
        "strong": bool(strong),
        "pf": pf,
        "net": net,
        "current_net": cur_net,
    }


def funnel_counts(opps: list[dict[str, Any]], port: dict[str, Any], trades: list[dict[str, Any]]) -> dict[str, int]:
    def n(k: str) -> int:
        return sum(1 for r in opps if r.get(k))

    return {
        "RAW_OPPORTUNITY_N": n("s0"),
        "TREND_PASS_N": n("s1"),
        "PULLBACK_PASS_N": n("s2"),
        "RCI_PASS_N": n("s3"),
        "PRICE_ACTION_PASS_N": n("s4"),
        "VOLUME_PASS_N": n("s5"),
        "BOARD_PASS_N": n("s6"),
        "PENDING_N": int(port.get("admitted_n") or 0),
        "FILL_N": int(port.get("fill_n") or 0),
        "TRADE_N": len(trades),
    }
