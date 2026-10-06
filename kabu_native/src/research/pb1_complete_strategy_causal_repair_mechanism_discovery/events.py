"""Causal post-entry deterioration clocks. Triggers are completed-bar market state only."""
from __future__ import annotations

from typing import Any

from research.pb1_complete_strategy_causal_repair_mechanism_discovery.bars import (
    build_nm,
    ema_on_closes,
    enrich_1m,
    open_0900,
    or_levels,
)
from research.pb1_v4_clarified_machine_correction_v4 import OR_RECROSS_CLOSES, UNWIND_FRAC
from research.pb1_v4_clarified_machine_correction_v4.active import classify_progress, classify_stale_range_resolution
from research.pb1_v4_clarified_machine_correction_v4.encoding import RCA_COMPARABLE_OPPOSITE_BODY
from research.pb1_v4_clarified_machine_correction_v4.location import classify_interaction, five_m_left
from research.pb1_v4_complete_strategy_economic_failure_decomposition.path import dir_bps


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _sign(side: str) -> int:
    return 1 if str(side).lower() in {"bull", "long", "1"} else -1


def _after(t: str, after: str) -> bool:
    return str(t)[:5] > str(after)[:5]


def _adverse_close(*, sign: int, close: float, ref: float) -> bool:
    if not (_finite(close) and _finite(ref)):
        return False
    if int(sign) > 0:
        return float(close) < float(ref)
    return float(close) > float(ref)


def _recross(*, sign: int, close: float, or_high: float, or_low: float) -> bool:
    if int(sign) > 0:
        return float(close) < float(or_high)
    return float(close) > float(or_low)


def path_extrema(trade: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    side = str(trade.get("side") or "")
    entry = float(trade.get("entry_px") or 0)
    entry_t = str(trade.get("entry_t") or "")[:5]
    exit_t = str(trade.get("exit_t") or "")[:5]
    times = [str(t)[:5] for t in list(rec.get("t") or [])]
    mfe = mae = None
    t_mfe = t_mae = None
    first_fav = None
    peak_px = None
    for i, t in enumerate(times):
        if t <= entry_t or t > exit_t:
            continue
        h = rec["h"][i]
        l = rec["l"][i]
        if not (_finite(h) and _finite(l)):
            continue
        hi = dir_bps(side=side, entry=entry, px=float(h))
        lo = dir_bps(side=side, entry=entry, px=float(l))
        fav = hi if _sign(side) > 0 else lo
        unfav = lo if _sign(side) > 0 else hi
        if fav is not None and (mfe is None or fav > mfe):
            mfe = float(fav)
            t_mfe = t
            peak_px = float(h) if _sign(side) > 0 else float(l)
        if unfav is not None and (mae is None or unfav < mae):
            mae = float(unfav)
            t_mae = t
        if first_fav is None and fav is not None and fav > 0:
            first_fav = t
    realized = dir_bps(side=side, entry=entry, px=float(trade.get("exit_px") or 0))
    capture = None
    if mfe is not None and mfe > 0 and realized is not None:
        capture = float(realized) / float(mfe)
    return {
        "MFE_bps": mfe,
        "MAE_bps": mae,
        "time_to_MFE": t_mfe,
        "time_to_MAE": t_mae,
        "first_favorable_expansion_t": first_fav,
        "peak_favorable_px": peak_px,
        "realized_gross_bps": realized,
        "MFE_capture_ratio": capture,
    }


def _persist_loss(times: list[str], closes: list[float], refs: list[float], *, sign: int, after_t: str) -> str | None:
    prev = False
    for t, c, r in zip(times, closes, refs):
        if not _after(t, after_t):
            prev = False
            continue
        bad = _adverse_close(sign=sign, close=c, ref=r)
        if bad and prev:
            return t
        prev = bool(bad)
    return None


def detect_events(trade: dict[str, Any], rec: dict[str, Any], *, level: float | None) -> dict[str, Any]:
    side = str(trade.get("side") or "")
    sign = _sign(side)
    entry_t = str(trade.get("entry_t") or "")[:5]
    lost_t = str(trade.get("THESIS_LOST_AT") or "")[:5] or None
    one = enrich_1m(rec)
    five = build_nm(rec, width=5)
    three = build_nm(rec, width=3)
    orl = or_levels(rec)
    or_h = float(orl["or_high"]) if orl and orl.get("ok") else None
    or_l = float(orl["or_low"]) if orl and orl.get("ok") else None
    opn = open_0900(rec)
    out: dict[str, Any] = {k: None for k in (
        "FIRST_OR_RECROSS",
        "SECOND_OR_RECROSS",
        "OPPOSITE_COMMITTED_5M",
        "STALE_RANGE_RESOLUTION",
        "FAILED_EXTENSION_TWO_5M",
        "DISPLACEMENT_UNWIND",
        "VWAP_ADVERSE_1M",
        "EMA9_1M_LOSS_PERSIST_2",
        "EMA9_3M_LOSS_PERSIST_2",
        "EMA9_5M_LOSS_PERSIST_2",
        "SMA5_1M_LOSS_PERSIST_2",
        "TWO_BAR_WEAKNESS",
        "ASF_FIRST_STRUCTURAL_KILL",
    )}
    out["or_ok"] = bool(orl and orl.get("ok"))
    out["level"] = float(level) if _finite(level) else None
    out["THESIS_LOST_AT"] = lost_t

    left = False
    recross_run = 0
    first_recross = None
    second_recross = None
    no_ext_run = 0
    failed_ext = None
    prev_bar = None
    prev_ext = None
    peak_disp = None
    through_closes = 0
    prev_ix = "NO_INTERACTION_YET"
    asf_t = None
    for bar in five:
        t1 = str(bar.get("t1") or "")[:5]
        if or_h is not None and or_l is not None:
            if five_m_left(sign=sign, bar=bar, or_high=or_h, or_low=or_l):
                left = True
            recross = left and _recross(sign=sign, close=float(bar["c"]), or_high=or_h, or_low=or_l)
            if recross:
                recross_run += 1
                if first_recross is None and _after(t1, entry_t):
                    first_recross = t1
                if recross_run >= int(OR_RECROSS_CLOSES) and second_recross is None and _after(t1, entry_t):
                    second_recross = t1
            else:
                recross_run = 0
        if _after(t1, entry_t):
            prog = classify_progress(sign=sign, bar=bar, prev_bar=prev_bar, prev_ext=prev_ext)
            if str(prog.get("class") or "") == "NO_DIRECTIONAL_PROGRESS":
                no_ext_run += 1
                if no_ext_run >= 2 and failed_ext is None:
                    failed_ext = t1
            else:
                no_ext_run = 0
            body = bar.get("body_over_range")
            if int(bar.get("direction") or 0) == -int(sign) and _finite(body) and float(body) >= float(RCA_COMPARABLE_OPPOSITE_BODY):
                if out["OPPOSITE_COMMITTED_5M"] is None:
                    out["OPPOSITE_COMMITTED_5M"] = t1
            stale = classify_stale_range_resolution(sign=sign, bar=bar, prev_bar=prev_bar)
            if stale.get("stale") and out["STALE_RANGE_RESOLUTION"] is None:
                out["STALE_RANGE_RESOLUTION"] = t1
            if _finite(level) and asf_t is None:
                ix = classify_interaction(
                    sign=sign,
                    high=float(bar["h"]),
                    low=float(bar["l"]),
                    close=float(bar["c"]),
                    level=float(level),
                    prev=prev_ix,
                )
                prev_ix = str(ix.get("state") or prev_ix)
                if str(ix.get("state") or "") == "TEMPORARY_PENETRATION":
                    through_closes += 1
                elif str(ix.get("state") or "") in {"HOLD", "REJECTION", "TOUCH"}:
                    through_closes = 0
                if bool(ix.get("kills_thesis")) or through_closes >= 2:
                    asf_t = t1
        ext = bar.get("h") if sign > 0 else bar.get("l")
        if _finite(ext):
            if prev_ext is None:
                prev_ext = float(ext)
            elif sign > 0:
                prev_ext = max(float(prev_ext), float(ext))
            else:
                prev_ext = min(float(prev_ext), float(ext))
        prev_bar = bar
    out["FIRST_OR_RECROSS"] = first_recross
    out["SECOND_OR_RECROSS"] = second_recross
    out["FAILED_EXTENSION_TWO_5M"] = failed_ext
    out["ASF_FIRST_STRUCTURAL_KILL"] = asf_t

    times = one["t"]
    for i, t in enumerate(times):
        if not _after(t, entry_t):
            continue
        c = float(one["c"][i]) if i < len(one["c"]) else None
        if _finite(opn) and _finite(c):
            disp = (float(c) - float(opn)) * float(sign)
            peak_disp = disp if peak_disp is None else max(float(peak_disp), float(disp))
            if (
                peak_disp is not None
                and float(peak_disp) > 0
                and (float(peak_disp) - float(disp)) >= float(UNWIND_FRAC) * float(peak_disp)
                and float(disp) < 0.25 * float(peak_disp)
                and out["DISPLACEMENT_UNWIND"] is None
            ):
                out["DISPLACEMENT_UNWIND"] = t
        vw = float(one["vwap"][i]) if i < len(one["vwap"]) else None
        if out["VWAP_ADVERSE_1M"] is None and _adverse_close(sign=sign, close=float(c or 0), ref=float(vw or 0)):
            out["VWAP_ADVERSE_1M"] = t
        if i >= 1:
            c0 = float(one["c"][i - 1]) if i - 1 < len(one["c"]) else None
            o = float(one["o"][i]) if i < len(one["o"]) else None
            weak = False
            if _finite(c) and _finite(o) and _finite(c0):
                if sign > 0:
                    weak = float(c) < float(o) and float(c) < float(c0)
                else:
                    weak = float(c) > float(o) and float(c) > float(c0)
            prev_weak = False
            if i >= 2:
                c1 = float(one["c"][i - 1])
                o1 = float(one["o"][i - 1])
                c2 = float(one["c"][i - 2])
                if sign > 0:
                    prev_weak = float(c1) < float(o1) and float(c1) < float(c2)
                else:
                    prev_weak = float(c1) > float(o1) and float(c1) > float(c2)
            if weak and prev_weak and out["TWO_BAR_WEAKNESS"] is None:
                out["TWO_BAR_WEAKNESS"] = t

    closes_1 = [float(x) for x in one["c"]]
    ema9_1 = [float(x) for x in one["ema9"]]
    sma5_1 = [float(x) for x in one["sma5"]]
    out["EMA9_1M_LOSS_PERSIST_2"] = _persist_loss(times, closes_1, ema9_1, sign=sign, after_t=entry_t)
    out["SMA5_1M_LOSS_PERSIST_2"] = _persist_loss(times, closes_1, sma5_1, sign=sign, after_t=entry_t)
    if three:
        t3 = [str(b["t1"])[:5] for b in three]
        c3 = [float(b["c"]) for b in three]
        e3 = ema_on_closes(c3, 9)
        out["EMA9_3M_LOSS_PERSIST_2"] = _persist_loss(t3, c3, e3, sign=sign, after_t=entry_t)
    if five:
        t5 = [str(b["t1"])[:5] for b in five]
        c5 = [float(b["c"]) for b in five]
        e5 = ema_on_closes(c5, 9)
        out["EMA9_5M_LOSS_PERSIST_2"] = _persist_loss(t5, c5, e5, sign=sign, after_t=entry_t)
    return out
