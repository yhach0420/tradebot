"""V2 vs V1 execution-cliff, recovery of good nonfills, mechanism verdict, next deficiency."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.simple_tech_entry_family.analyze import robustness
from research.simple_tech_entry_family.stages import bad_entry, good_upmove
from research.simple_tech_entry_family.v2_spec import V1_LOCKED


def _mean(xs: list[Any]) -> Optional[float]:
    vs = [float(x) for x in xs if x is not None and isinstance(x, (int, float)) and x == x]
    if not vs:
        return None
    return float(np.mean(vs))


def _key(r: dict[str, Any]) -> tuple[str, str, float]:
    return (str(r.get("date") or ""), str(r.get("symbol") or "").replace(".T", ""), float(r.get("bar_minute") or 0.0))


def nested_mismatch(v1_opps: list[dict[str, Any]], v2_nested: list[dict[str, Any]]) -> dict[str, int]:
    flags = ("s0", "s1", "s2", "s3", "s5", "s6")
    a: dict[tuple, dict[str, bool]] = {}
    b: dict[tuple, dict[str, bool]] = {}
    for r in v1_opps:
        a[_key(r)] = {f: bool(r.get(f)) for f in flags}
    for r in v2_nested:
        b[_key(r)] = {f: bool(r.get(f)) for f in flags}
    keys = set(a) | set(b)
    out = {
        "ROW_MISMATCH_N": sum(1 for k in keys if k not in a or k not in b),
        "RAW_PASS_MISMATCH_N": 0,
        "TREND_PASS_MISMATCH_N": 0,
        "PULLBACK_PASS_MISMATCH_N": 0,
        "RCI_PASS_MISMATCH_N": 0,
        "VOLUME_PASS_MISMATCH_N": 0,
        "BOARD_NESTED_MISMATCH_N": 0,
    }
    fmap = {
        "s0": "RAW_PASS_MISMATCH_N",
        "s1": "TREND_PASS_MISMATCH_N",
        "s2": "PULLBACK_PASS_MISMATCH_N",
        "s3": "RCI_PASS_MISMATCH_N",
        "s5": "VOLUME_PASS_MISMATCH_N",
        "s6": "BOARD_NESTED_MISMATCH_N",
    }
    for k in keys:
        ra = a.get(k)
        rb = b.get(k)
        if ra is None or rb is None:
            for dest in fmap.values():
                out[dest] += 1
            continue
        for f, dest in fmap.items():
            if bool(ra.get(f)) != bool(rb.get(f)):
                out[dest] += 1
    return out


def fill_cliff(signals: list[dict[str, Any]], port: dict[str, Any]) -> dict[str, Any]:
    admitted = [r for r in (port.get("candidates") or signals) if r.get("s8_pending") or r.get("s8")]
    filled = [r for r in admitted if r.get("WOULD_FILL") and (r.get("s9_fill") or r.get("s9"))]
    nonfill = [r for r in admitted if not r.get("WOULD_FILL")]
    filled_fwd = _mean([r.get("fwd_3m") for r in filled])
    non_fwd = _mean([r.get("fwd_3m") for r in nonfill])
    pend_n = int(port.get("admitted_n") or len(admitted))
    fill_n = int(port.get("fill_n") or len(filled))
    rate = (float(fill_n) / float(pend_n)) if pend_n else None
    gap = None
    if filled_fwd is not None and non_fwd is not None:
        gap = float(filled_fwd) - float(non_fwd)
    return {
        "SIGNAL_N": len(signals),
        "PENDING_N": pend_n,
        "FILL_N": fill_n,
        "FILL_RATE": rate,
        "FILLED_FWD3": filled_fwd,
        "NONFILLED_FWD3": non_fwd,
        "FILL_QUALITY_GAP": gap,
        "FILLED_N_FWD": len(filled),
        "NONFILL_N_FWD": len(nonfill),
        "FILLED_GOOD_N": sum(1 for r in filled if good_upmove(r)),
        "NONFILL_GOOD_N": sum(1 for r in nonfill if good_upmove(r)),
        "FILLED_BAD_N": sum(1 for r in filled if bad_entry(r)),
        "admitted": admitted,
        "filled": filled,
        "nonfill": nonfill,
    }


def _best_setup(v1: dict[str, Any], setups: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    d = str(v1.get("date") or "")
    s = str(v1.get("symbol") or "").replace(".T", "")
    bm = float(v1.get("bar_minute") or 0.0)
    cands = [u for u in setups if str(u.get("date") or "") == d and str(u.get("symbol") or "").replace(".T", "") == s]
    if not cands:
        return None
    exact = [u for u in cands if abs(float(u.get("bar_minute") or 0.0) - bm) <= 1e-6]
    if exact:
        return exact[0]
    minus = [u for u in cands if abs(float(u.get("bar_minute") or 0.0) - (bm - 60.0)) <= 1e-6]
    if minus:
        return minus[0]
    plus = [u for u in cands if abs(float(u.get("bar_minute") or 0.0) - (bm + 60.0)) <= 1e-6]
    if plus:
        return plus[0]
    return min(cands, key=lambda u: abs(float(u.get("bar_minute") or 0.0) - bm))


def v1_v2_counterfactual(
    v1_signals: list[dict[str, Any]],
    setups: list[dict[str, Any]],
    v2_signals: list[dict[str, Any]],
    v2_port_cands: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_setup = {(str(r.get("date")), str(r.get("symbol") or "").replace(".T", ""), float(r.get("bar_minute") or 0.0)): r for r in v2_signals}
    by_t0 = {(str(r.get("date")), str(r.get("symbol") or "").replace(".T", ""), float(r.get("t0") or 0.0)): r for r in v2_port_cands}
    rows = []
    for v1 in v1_signals:
        st = _best_setup(v1, setups)
        v2s = None
        if st is not None:
            v2s = by_setup.get((str(st.get("date")), str(st.get("symbol") or "").replace(".T", ""), float(st.get("bar_minute") or 0.0)))
        v2p = None
        if v2s is not None:
            v2p = by_t0.get((str(v2s.get("date")), str(v2s.get("symbol") or "").replace(".T", ""), float(v2s.get("t0") or 0.0)))
        v1_t0 = v1.get("t0")
        v2_t0 = (v2s or {}).get("t0") or (st or {}).get("trigger_t")
        earlier = None
        if v1_t0 is not None and v2_t0 is not None:
            earlier = float(v2_t0) < float(v1_t0) - 1e-9
        v1_fill = bool(v1.get("WOULD_FILL"))
        v2_fill = bool((v2s or {}).get("WOULD_FILL")) if v2s else False
        rows.append(
            {
                "date": v1.get("date"),
                "symbol": str(v1.get("symbol") or "").replace(".T", ""),
                "v1_bar_minute": v1.get("bar_minute"),
                "v1_signal_t": v1_t0,
                "v2_setup_t": (st or {}).get("setup_t"),
                "v2_event_trigger_t": v2_t0,
                "v2_status": (st or {}).get("status") or "NO_SETUP",
                "v2_bar_minute": (st or {}).get("bar_minute"),
                "v1_limit": v1.get("limit") or v1.get("bid"),
                "v2_limit": (v2s or {}).get("limit"),
                "v1_filled": v1_fill,
                "v2_filled": v2_fill,
                "v1_expired": (not v1_fill),
                "v2_expired": bool(v2s is not None and not v2_fill),
                "v1_fwd_1m": v1.get("fwd_1m"),
                "v1_fwd_3m": v1.get("fwd_3m"),
                "v1_fwd_5m": v1.get("fwd_5m"),
                "v2_fwd_1m": (v2s or st or {}).get("fwd_1m"),
                "v2_fwd_3m": (v2s or st or {}).get("fwd_3m"),
                "v2_fwd_5m": (v2s or st or {}).get("fwd_5m"),
                "v2_earlier": earlier,
                "v1_good": good_upmove(v1),
                "v2_good": good_upmove(v2s) if v2s else False,
                "v2_pending": bool(v2p.get("s8_pending")) if v2p else False,
                "lead_sec": (float(v1_t0) - float(v2_t0)) if (v1_t0 is not None and v2_t0 is not None) else None,
            }
        )
    return rows


def recovery_pack(v1_signals: list[dict[str, Any]], cf: list[dict[str, Any]], v2_filled: list[dict[str, Any]]) -> dict[str, Any]:
    v1_good_non = [r for r in v1_signals if good_upmove(r) and not r.get("WOULD_FILL")]
    v1_fills = {(str(r.get("date")), str(r.get("symbol") or "").replace(".T", "")) for r in v1_signals if r.get("WOULD_FILL")}
    recovered = []
    still = []
    for g in v1_good_non:
        d = str(g.get("date") or "")
        s = str(g.get("symbol") or "").replace(".T", "")
        hit = next((c for c in cf if str(c.get("date")) == d and str(c.get("symbol")) == s and c.get("v1_good")), None)
        if hit and hit.get("v2_filled"):
            recovered.append(hit)
        else:
            still.append({"date": d, "symbol": s, "v2_status": (hit or {}).get("v2_status")})
    new_bad = []
    for r in v2_filled:
        key = (str(r.get("date")), str(r.get("symbol") or "").replace(".T", ""))
        if key in v1_fills:
            continue
        if bad_entry(r):
            new_bad.append(r)
    rec_days = {str(r.get("date")) for r in recovered}
    return {
        "V1_GOOD_UPMOVE_LOST_AT_FILL": len(v1_good_non),
        "GOOD_UPMOVE_RECOVERED_N": len(recovered),
        "GOOD_UPMOVE_STILL_NONFILL_N": len(still),
        "NEW_BAD_FILL_N": len(new_bad),
        "RECOVERED_DAYS": sorted(rec_days),
        "recovered": recovered,
        "still": still,
        "new_bad": [
            {"date": r.get("date"), "symbol": str(r.get("symbol") or "").replace(".T", ""), "fwd_3m": r.get("fwd_3m")}
            for r in new_bad
        ],
    }


def mechanism_supported(
    *,
    v1_cliff: dict[str, Any],
    v2_cliff: dict[str, Any],
    rec: dict[str, Any],
    cf: list[dict[str, Any]],
    mismatch: dict[str, int],
    ni_ok: bool,
    integrity_ok: bool,
) -> dict[str, Any]:
    v1_rate = v1_cliff.get("FILL_RATE")
    v2_rate = v2_cliff.get("FILL_RATE")
    v1_ff = v1_cliff.get("FILLED_FWD3")
    v2_ff = v2_cliff.get("FILLED_FWD3")
    v1_gap = v1_cliff.get("FILL_QUALITY_GAP")
    v2_gap = v2_cliff.get("FILL_QUALITY_GAP")
    a = v1_rate is not None and v2_rate is not None and float(v2_rate) > float(v1_rate)
    b = v1_ff is not None and v2_ff is not None and float(v2_ff) > float(v1_ff)
    c = v1_gap is not None and v2_gap is not None and float(v2_gap) > float(v1_gap)
    d = int(rec.get("GOOD_UPMOVE_RECOVERED_N") or 0) > 0
    e = int(rec.get("NEW_BAD_FILL_N") or 0) <= int(rec.get("GOOD_UPMOVE_RECOVERED_N") or 0)
    rec_days = list(rec.get("RECOVERED_DAYS") or [])
    earlier_days = {str(r.get("date")) for r in cf if r.get("v2_earlier")}
    fill_days = {str(r.get("date")) for r in v2_cliff.get("filled") or []}
    f = len(rec_days) >= 2 or (len(earlier_days) >= 2 and len(fill_days) >= 2) or len(fill_days) >= 2 and d
    g = bool(ni_ok and integrity_ok and all(int(mismatch.get(k) or 0) == 0 for k in (
        "TREND_PASS_MISMATCH_N",
        "PULLBACK_PASS_MISMATCH_N",
        "RCI_PASS_MISMATCH_N",
        "VOLUME_PASS_MISMATCH_N",
        "BOARD_NESTED_MISMATCH_N",
        "ROW_MISMATCH_N",
    )))
    ok = bool(a and b and c and d and e and f and g)
    return {
        "A_FILL_RATE": a,
        "B_FILLED_FWD3": b,
        "C_FILL_QUALITY_GAP": c,
        "D_GOOD_RECOVERED": d,
        "E_NEW_BAD_NOT_DOMINATE": e,
        "F_MULTI_DAY": f,
        "G_INTEGRITY": g,
        "TRIGGER_MECHANISM_SUPPORTED": ok,
        "earlier_n": sum(1 for r in cf if r.get("v2_earlier")),
        "matched_n": sum(1 for r in cf if r.get("v2_status") != "NO_SETUP"),
        "effect_days": sorted(set(rec_days) | (fill_days if d else set())),
    }


def next_deficiency(
    *,
    supported: bool,
    v2_cliff: dict[str, Any],
    rec: dict[str, Any],
    cf: list[dict[str, Any]],
    setups: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    filled_fwd = v2_cliff.get("FILLED_FWD3")
    non_fwd = v2_cliff.get("NONFILLED_FWD3")
    cliff_left = (
        filled_fwd is not None
        and non_fwd is not None
        and float(filled_fwd) < float(non_fwd)
        and int(v2_cliff.get("NONFILL_GOOD_N") or 0) > 0
    )
    earlier_n = sum(1 for r in cf if r.get("v2_earlier"))
    n_cf = len(cf) or 1
    expired = sum(1 for u in setups if u.get("status") == "SETUP_EXPIRED")
    ema_fail = sum(1 for u in setups if u.get("status") == "CROSS_EMA_FAIL")
    bb_fail = sum(1 for u in setups if u.get("status") == "CROSS_BB_FAIL")
    board_fail = sum(1 for u in setups if u.get("status") == "BOARD_VETO")
    pending = sum(1 for u in setups if u.get("status") == "PENDING")
    setup_n = len(setups)
    trig_n = sum(1 for u in setups if u.get("triggered"))
    good_sig_fwd = _mean([r.get("fwd_3m") for r in (v2_cliff.get("admitted") or [])])
    status_counts = defaultdict(int)
    for u in setups:
        status_counts[str(u.get("status") or "")] += 1

    if cliff_left and (not supported or int(rec.get("GOOD_UPMOVE_STILL_NONFILL_N") or 0) > 0):
        name = "PASSIVE_FILL_INCOMPATIBLE_WITH_MOMENTUM_ENTRY"
        why = "Forward quality of signals remains positive for nonfills while Passive Fill still misses the up-moves."
    elif earlier_n == 0 and setup_n > 0:
        name = "SETUP_TOO_LATE"
        why = "V2 SETUP_READY is last completed RCI/volume bar; HIGH[k] cross did not precede V1 completed-bar t0 on the 29 BOARD_PASS rows."
    elif setup_n > 0 and trig_n == 0:
        name = "TREND_STATE_INSUFFICIENT"
        why = "SETUP_READY bars did not produce a from-below HIGH[k] cross before the next completed bar."
    elif ema_fail > pending and ema_fail >= max(bb_fail, board_fail, expired):
        name = "TREND_STATE_INSUFFICIENT"
        why = "First HIGH[k] cross often occurred at or below last completed EMA9[k]."
    elif expired > pending and expired >= ema_fail:
        name = "SETUP_TOO_LATE"
        why = "Most SETUP_READY bars expired at the next 1-minute boundary without a causal HIGH[k] cross."
    elif not supported and good_sig_fwd is not None and float(good_sig_fwd) > 0 and float(v2_cliff.get("FILL_RATE") or 0) <= float(V1_LOCKED["FILL_RATE"]):
        name = "PASSIVE_FILL_INCOMPATIBLE"
        why = "Earlier-or-replaced trigger still does not convert positive-forward signals into Passive Fills."
    elif int(rec.get("GOOD_UPMOVE_RECOVERED_N") or 0) == 0 and trig_n > 0:
        name = "REVERSAL_TOO_LATE"
        why = "Event trigger did not recover V1 good-upmove nonfills; reversal bar may already be after the usable bounce."
    else:
        name = "PASSIVE_FILL_INCOMPATIBLE_WITH_MOMENTUM_ENTRY" if cliff_left else "SETUP_TOO_LATE"
        why = "V2 did not satisfy the trigger-mechanism gate; do not search 2-tick/5s/10s delay confirms."

    if supported and cliff_left:
        name = "PASSIVE_FILL_INCOMPATIBLE_WITH_MOMENTUM_ENTRY"
        why = "Trigger advance helped some fills, but a fill-quality cliff remains: good movers still avoid Passive Fill."
    elif supported:
        losses = [t for t in trades if float(t.get("pnl_yen_100") or 0.0) < -1e-9]
        f8 = sum(1 for t in losses if (t.get("failure") or "") == "F8_EXIT_GIVEBACK")
        f7 = sum(1 for t in losses if (t.get("failure") or "") == "F7_EXECUTION_COST")
        if f8 > f7 and f8:
            name = "EXIT_GIVEBACK_AFTER_EARLIER_TRIGGER"
            why = "Trigger mechanism supported. Remaining losses are C14 giveback; EXIT stays frozen."
        elif f7:
            name = "EXECUTION_COST_AFTER_EARLIER_TRIGGER"
            why = "Trigger mechanism supported. Remaining losses are spread/1m cost; fill rule stays frozen."
        else:
            name = "EDGE_AFTER_TRIGGER_STILL_WEAK"
            why = "Trigger mechanism supported. Next RCA is full V2 portfolio edge, not a later confirm delay."

    return {
        "PRIMARY_DEFICIENCY_AFTER_V2": name,
        "reason": why,
        "do_not_search_delay_confirm": True,
        "setup_n": setup_n,
        "trigger_n": trig_n,
        "expired_n": expired,
        "ema_fail_n": ema_fail,
        "bb_fail_n": bb_fail,
        "board_fail_n": board_fail,
        "pending_n": pending,
        "earlier_n": earlier_n,
        "earlier_frac_v1_29": float(earlier_n) / float(n_cf),
        "status_counts": dict(status_counts),
        "v2_signal_fwd3": good_sig_fwd,
        "cliff_remaining": bool(cliff_left),
        "supported": bool(supported),
    }


def v1_cliff_locked() -> dict[str, Any]:
    filled = float(V1_LOCKED["FILLED_FWD3"])
    non = float(V1_LOCKED["NONFILLED_FWD3"])
    return {
        "SIGNAL_N": int(V1_LOCKED["SIGNAL_N"]),
        "FILL_N": int(V1_LOCKED["FILL_N"]),
        "FILL_RATE": float(V1_LOCKED["FILL_RATE"]),
        "FILLED_FWD3": filled,
        "NONFILLED_FWD3": non,
        "FILL_QUALITY_GAP": filled - non,
        "GOOD_UPMOVE_LOST_AT_FILL": int(V1_LOCKED["GOOD_UPMOVE_LOST_AT_FILL"]),
        "NET": float(V1_LOCKED["NET"]),
        "PF": float(V1_LOCKED["PF"]),
        "MAX_DD": float(V1_LOCKED["MAX_DD"]),
    }


__all__ = [
    "nested_mismatch",
    "fill_cliff",
    "v1_v2_counterfactual",
    "recovery_pack",
    "mechanism_supported",
    "next_deficiency",
    "v1_cliff_locked",
    "robustness",
]
