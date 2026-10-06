"""Complete playbooks from stable native sequences. Thesis-linked EXIT. No HM1 retune."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

import numpy as np

from research.one_minute_native_playbook_discovery_v1 import (
    ENTRY_CUTOFF,
    FAVOR_BPS,
    MAX_PLAYBOOKS,
    MIN_PLAYBOOK_DAY_N,
    MIN_PLAYBOOK_SYMBOL_N,
    MIN_PLAYBOOK_TRADE_N,
    MIN_SEQ_DAY_N,
    MIN_SEQ_N,
    OCCUPANCY,
    SESSION_FLAT,
    TIME_STOP_MIN,
    X1_TAX_BPS,
)

SPECS = (
    {
        "candidate_id": "PB_NATIVE_PULLBACK_RECLAIM",
        "sequence": "PULLBACK_THEN_RECLAIM",
        "thesis": "Positive 15m structure, 5m pullback, then VWAP reclaim resumes demand.",
        "entry": "last causal event VWAP_RECLAIM; fill next bar open",
        "exit_kind": "reclaim",
    },
    {
        "candidate_id": "PB_NATIVE_COMPRESSION_BREAKOUT",
        "sequence": "COMPRESSION_THEN_BREAKOUT",
        "thesis": "Range compression then 20-bar high breakout; stay long while acceptance holds.",
        "entry": "BREAKOUT20 after COMPRESSION in the same episode",
        "exit_kind": "breakout",
    },
    {
        "candidate_id": "PB_NATIVE_VOL_IMPULSE",
        "sequence": "VOL_EXPAND_THEN_IMPULSE_UP",
        "thesis": "Activity expansion then upward impulse; continuation of the impulse origin.",
        "entry": "IMPULSE_UP after VOL/VA expand",
        "exit_kind": "impulse",
    },
    {
        "candidate_id": "PB_NATIVE_IMPULSE_PAUSE",
        "sequence": "IMPULSE_THEN_PAUSE",
        "thesis": "Impulse then pause; second-leg continuation after digestion.",
        "entry": "PAUSE_AFTER_IMPULSE",
        "exit_kind": "impulse",
    },
    {
        "candidate_id": "PB_NATIVE_LAG_CATCHUP",
        "sequence": "LAG_THEN_CATCHUP",
        "thesis": "Sector leader already impulsed; lagging stock starts catching up.",
        "entry": "LAG_CATCHUP onset",
        "exit_kind": "impulse",
    },
    {
        "candidate_id": "PB_NATIVE_OPENING_GAP_HOLD",
        "sequence": "OPENING_GAP_HOLD",
        "thesis": "Opening gap still held at 09:14; continuation of the gap side.",
        "entry": "09:15 open after 09:14 hold",
        "exit_kind": "gap",
    },
    {
        "candidate_id": "PB_NATIVE_BREAKOUT",
        "sequence": "BREAKOUT20",
        "thesis": "20-bar high breakout; invalidation is loss of that level.",
        "entry": "BREAKOUT20 onset",
        "exit_kind": "breakout",
    },
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _spec_sha(spec: dict[str, Any]) -> str:
    raw = json.dumps({k: v for k, v in spec.items() if k != "spec_sha256"}, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _exit_from_fwd(e: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    fwd = list(e.get("fwd_bars") or [])
    px = e.get("x0_entry_open")
    if not fwd or not _finite(px) or float(px) <= 0:
        return {"ok": False}
    px = float(px)
    kind = spec.get("exit_kind")
    hi20 = e.get("hi20")
    reason = "session_flat"
    exit_i = min(len(fwd) - 1, TIME_STOP_MIN)
    below_run = 0
    for i, row in enumerate(fwd):
        hh, _o, _h, _l, cl, vw, above_vw, brk = row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]
        if str(hh) >= SESSION_FLAT:
            exit_i, reason = i, "session_flat"
            break
        if kind == "reclaim" and above_vw is False:
            exit_i, reason = i, "vwap_loss"
            break
        if kind == "breakout" and _finite(brk) and _finite(cl) and float(cl) < float(brk):
            exit_i, reason = i, "breakout_fail"
            break
        if kind == "impulse":
            if _finite(cl) and float(cl) < px * (1.0 - FAVOR_BPS / 10_000.0):
                below_run += 1
            else:
                below_run = 0
            if below_run >= 2:
                exit_i, reason = i, "impulse_origin_fail"
                break
        if kind == "gap":
            # gap fill: close crosses back through entry open toward prior close; use VWAP loss as deterioration proxy if gap-up long
            if above_vw is False:
                exit_i, reason = i, "gap_hold_fail"
                break
        if i >= TIME_STOP_MIN:
            exit_i, reason = i, "time_stop"
            break
        _ = hi20
    if exit_i + 1 < len(fwd) and _finite(fwd[exit_i + 1][1]):
        exit_px = float(fwd[exit_i + 1][1])
        fill = "next_open_after_invalidation_bar"
    else:
        exit_px = float(fwd[exit_i][4]) if _finite(fwd[exit_i][4]) else None
        fill = "invalidation_close_last_bar"
    if not _finite(exit_px):
        return {"ok": False}
    x0 = float((exit_px / px - 1.0) * 10_000.0)
    return {
        "ok": True,
        "exit_reason": reason,
        "exit_fill": fill,
        "x0_bps": x0,
        "x1_bps": x0 - float(X1_TAX_BPS),
        "hold_min": int(exit_i),
    }


def replay(episodes: list[dict[str, Any]], spec: dict[str, Any], date_to_block: dict[str, str]) -> dict[str, Any]:
    seq = spec["sequence"]
    rows = [e for e in episodes if e.get("sequence") == seq and e.get("x0_entry_open")]
    rows.sort(key=lambda e: (str(e["date"]), str(e["event_time"]), str(e["symbol"])))
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades = []
    skipped = {"cutoff": 0, "occupancy": 0, "same_symbol": 0, "no_exit": 0}
    for e in rows:
        day = str(e["date"])
        hh = str(e["event_time"])
        if hh > ENTRY_CUTOFF:
            skipped["cutoff"] += 1
            continue
        occ[day] = [p for p in occ[day] if str(p["exit_hh"]) > hh]
        if e["symbol"] in traded_today[day]:
            skipped["same_symbol"] += 1
            continue
        if len(occ[day]) >= OCCUPANCY:
            skipped["occupancy"] += 1
            continue
        got = _exit_from_fwd(e, spec)
        if not got.get("ok"):
            skipped["no_exit"] += 1
            continue
        fwd = list(e.get("fwd_bars") or [])
        hold = int(got["hold_min"])
        exit_hh = str(fwd[min(hold + 1, len(fwd) - 1)][0]) if fwd else SESSION_FLAT
        trade = {
            "date": day,
            "symbol": e["symbol"],
            "sector": e.get("sector"),
            "event_time": hh,
            "block": e.get("block") or date_to_block.get(day),
            "sequence": seq,
            **got,
            "exit_hh": exit_hh,
            "path_class": e.get("path_class"),
        }
        trades.append(trade)
        occ[day].append(trade)
        traded_today[day].add(str(e["symbol"]))
    if not trades:
        return {"ok": False, "trade_n": 0, "skipped": skipped, "sequence": seq, "candidate_id": spec.get("candidate_id")}
    x0 = np.asarray([float(t["x0_bps"]) for t in trades], dtype=float)
    x1 = np.asarray([float(t["x1_bps"]) for t in trades], dtype=float)
    by_day: dict[str, list[float]] = defaultdict(list)
    by_block: dict[str, list[float]] = defaultdict(list)
    by_sym: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t["date"]].append(float(t["x0_bps"]))
        if t.get("block"):
            by_block[str(t["block"])].append(float(t["x0_bps"]))
        if float(t["x0_bps"]) > 0:
            by_sym[str(t["symbol"])] += float(t["x0_bps"])
    day_means = np.asarray([float(np.mean(vs)) for vs in by_day.values()], dtype=float)
    eq = np.cumsum(day_means)
    dd = float(np.min(eq - np.maximum.accumulate(eq))) if eq.size else 0.0
    pos = float(np.sum(x0[x0 > 0]))
    neg = float(-np.sum(x0[x0 < 0]))
    pf = (pos / neg) if neg > 0 else None
    block_mean = {k: float(np.mean(vs)) for k, vs in by_block.items()}
    return {
        "ok": True,
        "candidate_id": spec.get("candidate_id"),
        "sequence": seq,
        "trade_n": int(x0.size),
        "day_n": len(by_day),
        "symbol_n": len({t["symbol"] for t in trades}),
        "sector_n": len({t.get("sector") for t in trades}),
        "mean_x0_bps": float(np.mean(x0)),
        "mean_x1_bps": float(np.mean(x1)),
        "median_x0_bps": float(np.median(x0)),
        "hit_rate": float(np.mean(x0 > 0)),
        "profit_factor": pf,
        "max_dd_daily_mean_bps": dd,
        "top_symbol_share_of_positive_bps": (max(by_sym.values()) / pos) if pos > 0 and by_sym else None,
        "block_mean_x0": block_mean,
        "block_positive_n": sum(1 for v in block_mean.values() if v > 0),
        "block_n": len(block_mean),
        "exit_reasons": {k: int(sum(1 for t in trades if t.get("exit_reason") == k)) for k in {t.get("exit_reason") for t in trades}},
        "skipped": skipped,
        "occupancy": OCCUPANCY,
        "same_symbol": "one_live_no_same_day_reentry",
        "session_close": SESSION_FLAT,
        "x1_tax_bps": X1_TAX_BPS,
        "not_bid_ask": True,
    }


def promote(econ: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    if int(econ.get("trade_n") or 0) < MIN_PLAYBOOK_TRADE_N:
        reasons.append("trade_n")
    if int(econ.get("day_n") or 0) < MIN_PLAYBOOK_DAY_N:
        reasons.append("day_n")
    if int(econ.get("symbol_n") or 0) < MIN_PLAYBOOK_SYMBOL_N:
        reasons.append("symbol_n")
    if float(econ.get("mean_x1_bps") or -999) <= 0:
        reasons.append("mean_x1_not_positive")
    pf = econ.get("profit_factor")
    if pf is None or float(pf) < 1.10:
        reasons.append("profit_factor")
    if float(econ.get("top_symbol_share_of_positive_bps") or 0) >= 0.40:
        reasons.append("symbol_concentration")
    if int(econ.get("block_positive_n") or 0) < 3:
        reasons.append("block_consistency")
    return {"promoted": not reasons, "fail_reasons": reasons}


def build_playbooks(*, episodes: list[dict[str, Any]], seq_rows: list[dict[str, Any]], date_to_block: dict[str, str]) -> dict[str, Any]:
    seq_ok = {
        str(r["sequence"])
        for r in seq_rows
        if int(r.get("n") or 0) >= MIN_SEQ_N and int(r.get("day_n") or 0) >= MIN_SEQ_DAY_N
    }
    out = []
    for raw in SPECS:
        spec = dict(raw)
        spec["occupancy"] = OCCUPANCY
        spec["CAP"] = "skip_if_occupancy_full"
        spec["slot_release"] = "on_exit"
        spec["reentry"] = "none_same_day"
        spec["execution"] = "X0=next_bar_open_after_available_at; X1=X0-8bps_tax_not_BidAsk"
        spec["hm1_retuned"] = False
        spec["uses_frozen_validation"] = False
        spec["spec_sha256"] = _spec_sha(spec)
        econ = replay(episodes, spec, date_to_block)
        gate = promote(econ)
        out.append(
            {
                "spec": spec,
                "sequence_sample_ok": spec["sequence"] in seq_ok,
                "economics": econ,
                "promotion": gate,
            }
        )
    promoted = [c for c in out if c["promotion"]["promoted"]][:MAX_PLAYBOOKS]
    return {
        "proposals": out,
        "promoted": promoted,
        "promoted_n": len(promoted),
        "designed_on_discovery_only": True,
        "old_confirmation_used_to_design": False,
        "hm1_tuned": False,
        "entry_only_success": False,
    }
