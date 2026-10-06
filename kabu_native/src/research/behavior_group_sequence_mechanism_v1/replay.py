"""Complete strategy replay. CAP=3, same-symbol, occupancy, thesis EXIT, session close."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

import numpy as np

from research.behavior_group_sequence_mechanism_v1 import (
    EVAL_BLOCKS,
    FAVOR_BPS,
    MIN_CONTINUATION_SYMBOL_N,
    MIN_PLAYBOOK_DAY_N,
    MIN_PLAYBOOK_SYMBOL_N,
    MIN_PLAYBOOK_TRADE_N,
    NO_PROGRESS_MIN,
    OCCUPANCY,
    SESSION_FLAT,
    TIME_STOP_MIN,
    X1_TAX_BPS,
)

SPECS = (
    {
        "candidate_id": "GM_CATCHUP_RS_TURN",
        "sequence": "CATCHUP_RS_TURN",
        "pair": "SECTOR_LAGGARD_CATCHUP x lag-to-catchup RS_TURN",
        "group_filter": None,
        "window": "D2_D4",
        "exit_kind": "catchup",
        "thesis": "Sector already moved; lag persists; 1m relative return turns positive; exit if sector/RS thesis breaks.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "BG_CATCHUP_RS_TURN",
        "sequence": "CATCHUP_RS_TURN",
        "pair": "SECTOR_LAGGARD_CATCHUP x lag-to-catchup RS_TURN",
        "group_filter": "SECTOR_LAGGARD_CATCHUP",
        "window": "D2_D4",
        "exit_kind": "catchup",
        "thesis": "Prequential catch-up names only; same causal RS_TURN trigger.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "GM_CATCHUP_VWAP",
        "sequence": "CATCHUP_VWAP",
        "pair": "SECTOR_LAGGARD_CATCHUP x VWAP_RECLAIM",
        "group_filter": None,
        "window": "D2_D4",
        "exit_kind": "catchup_reclaim",
        "thesis": "Laggard + sector strong + VWAP reclaim; not future catch-up.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "BG_CATCHUP_VWAP",
        "sequence": "CATCHUP_VWAP",
        "pair": "SECTOR_LAGGARD_CATCHUP x VWAP_RECLAIM",
        "group_filter": "SECTOR_LAGGARD_CATCHUP",
        "window": "D2_D4",
        "exit_kind": "catchup_reclaim",
        "thesis": "Prequential catch-up names + lag + VWAP reclaim.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "GM_CATCHUP_VOL",
        "sequence": "CATCHUP_VOL",
        "pair": "SECTOR_LAGGARD_CATCHUP x activity expand",
        "group_filter": None,
        "window": "D2_D4",
        "exit_kind": "catchup",
        "thesis": "Lag persists, sector strong, activity expands, 1m return non-negative.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "BG_CATCHUP_VOL",
        "sequence": "CATCHUP_VOL",
        "pair": "SECTOR_LAGGARD_CATCHUP x activity expand",
        "group_filter": "SECTOR_LAGGARD_CATCHUP",
        "window": "D2_D4",
        "exit_kind": "catchup",
        "thesis": "Prequential catch-up names + activity expand.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "GM_CATCHUP_STOP_DET",
        "sequence": "CATCHUP_STOP_DETERIORATE",
        "pair": "SECTOR_LAGGARD_CATCHUP x price stops deteriorating",
        "group_filter": None,
        "window": "D2_D4",
        "exit_kind": "catchup",
        "thesis": "Lag persists, sector strong, 1m return stops being negative.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "BG_CATCHUP_STOP_DET",
        "sequence": "CATCHUP_STOP_DETERIORATE",
        "pair": "SECTOR_LAGGARD_CATCHUP x price stops deteriorating",
        "group_filter": "SECTOR_LAGGARD_CATCHUP",
        "window": "D2_D4",
        "exit_kind": "catchup",
        "thesis": "Prequential catch-up names + stop-deteriorate.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "GM_VWAP_RECLAIM",
        "sequence": "VWAP_RECLAIM",
        "pair": "GLOBAL VWAP_RECLAIM",
        "group_filter": None,
        "window": "D2_D4",
        "exit_kind": "reclaim",
        "thesis": "Unconditional VWAP reclaim (global sequence).",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "BG_CONT_VWAP",
        "sequence": "VWAP_RECLAIM",
        "pair": "CONTINUATION x VWAP_RECLAIM",
        "group_filter": "CONTINUATION",
        "window": "D2_D4",
        "exit_kind": "reclaim",
        "thesis": "VWAP reclaim on prequential continuation names.",
        "diagnostic": False,
        "min_symbol_n": MIN_CONTINUATION_SYMBOL_N,
    },
    {
        "candidate_id": "GM_IMPULSE_PAUSE",
        "sequence": "IMPULSE_THEN_PAUSE",
        "pair": "GLOBAL IMPULSE_THEN_PAUSE",
        "group_filter": None,
        "window": "D2_D4",
        "exit_kind": "impulse",
        "thesis": "Impulse then pause; second-leg continuation.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "BG_CONT_IMPULSE_PAUSE",
        "sequence": "IMPULSE_THEN_PAUSE",
        "pair": "CONTINUATION x IMPULSE_THEN_PAUSE",
        "group_filter": "CONTINUATION",
        "window": "D2_D4",
        "exit_kind": "impulse",
        "thesis": "Impulse/pause on prequential continuation names.",
        "diagnostic": False,
        "min_symbol_n": MIN_CONTINUATION_SYMBOL_N,
    },
    {
        "candidate_id": "GM_OPENING_GAP_HOLD",
        "sequence": "OPENING_GAP_HOLD",
        "pair": "GLOBAL OPENING_GAP_HOLD",
        "group_filter": None,
        "window": "D2_D4",
        "exit_kind": "gap",
        "thesis": "09:14 gap-up hold; 09:15 entry.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "BG_OPEN_GAP_HOLD",
        "sequence": "OPENING_GAP_HOLD",
        "pair": "OPENING_MOMENTUM x OPENING_GAP_HOLD",
        "group_filter": "OPENING_MOMENTUM",
        "window": "D2_D4",
        "exit_kind": "gap",
        "thesis": "Opening gap hold on prequential opening-momentum names.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "GM_LEADER_PULLBACK_CONT",
        "sequence": "LEADER_PULLBACK_CONT",
        "pair": "GLOBAL leadership persistence / pullback continuation",
        "group_filter": None,
        "window": "D2_D4",
        "exit_kind": "leader",
        "thesis": "Intraday leader (not full-history hardcoded) persists, pullback, then resume.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "BG_LEADER_PULLBACK_CONT",
        "sequence": "LEADER_PULLBACK_CONT",
        "pair": "SECTOR_LEADER x leadership persistence / pullback continuation",
        "group_filter": "SECTOR_LEADER",
        "window": "D2_D4",
        "exit_kind": "leader",
        "thesis": "Prequential sector-leader names + leadership persistence pullback continuation.",
        "diagnostic": False,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
    },
    {
        "candidate_id": "DG_MEANREV_VWAP",
        "sequence": "VWAP_RECLAIM",
        "pair": "MEAN_REVERSION x VWAP_RECLAIM",
        "group_filter": "MEAN_REVERSION",
        "window": "D2_D4",
        "exit_kind": "reclaim",
        "thesis": "9983-class diagnostic only. Not a production family.",
        "diagnostic": True,
        "min_symbol_n": 1,
    },
    {
        "candidate_id": "GM_CATCHUP_RS_TURN_ALLDISC",
        "sequence": "CATCHUP_RS_TURN",
        "pair": "GLOBAL CATCHUP_RS_TURN including D1",
        "group_filter": None,
        "window": "ALL_DISC",
        "exit_kind": "catchup",
        "thesis": "Same RS_TURN mechanism on all Discovery blocks including D1 characterization days.",
        "diagnostic": True,
        "min_symbol_n": MIN_PLAYBOOK_SYMBOL_N,
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
    reason = "session_flat"
    exit_i = min(len(fwd) - 1, TIME_STOP_MIN)
    below_run = 0
    sec_run = 0
    rs_run = 0
    lead_run = 0
    mfe = None
    for i, row in enumerate(fwd):
        hh, _o, hi, _l, cl, _vw, above_vw, _brk, sec_r1, leader_r1, rs1, lag_dist, leader_sym = (
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
            row[11],
            row[12],
        )
        hbps = ((float(hi) / px) - 1.0) * 10_000.0 if _finite(hi) else None
        if hbps is not None and (mfe is None or hbps > mfe):
            mfe = hbps
        if str(hh) >= SESSION_FLAT:
            exit_i, reason = i, "session_flat"
            break
        if kind in {"reclaim", "gap", "catchup_reclaim"} and above_vw is False:
            exit_i, reason = i, "vwap_loss"
            break
        if kind in {"impulse", "catchup", "catchup_reclaim", "leader"}:
            if _finite(cl) and float(cl) < px * (1.0 - FAVOR_BPS / 10_000.0):
                below_run += 1
            else:
                below_run = 0
            if below_run >= 2:
                exit_i, reason = i, "structure_loss"
                break
        if kind in {"catchup", "catchup_reclaim"}:
            if _finite(sec_r1) and float(sec_r1) < 0:
                sec_run += 1
            else:
                sec_run = 0
            if sec_run >= 2:
                exit_i, reason = i, "sector_reversal"
                break
            if _finite(rs1) and float(rs1) < 0:
                rs_run += 1
            else:
                rs_run = 0
            if rs_run >= 2:
                exit_i, reason = i, "rs_failure"
                break
            if _finite(lag_dist) and float(lag_dist) >= 0:
                exit_i, reason = i, "catchup_complete"
                break
            if i >= NO_PROGRESS_MIN and (mfe is None or float(mfe) < FAVOR_BPS):
                exit_i, reason = i, "no_progress"
                break
        if kind == "leader":
            if str(leader_sym) and str(leader_sym) != str(e.get("symbol")):
                exit_i, reason = i, "leadership_lost"
                break
            if _finite(leader_r1) and float(leader_r1) < 0:
                lead_run += 1
            else:
                lead_run = 0
            if lead_run >= 2:
                exit_i, reason = i, "leader_failure"
                break
        if i >= TIME_STOP_MIN:
            exit_i, reason = i, "time_stop"
            break
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
        "mfe_bps": mfe,
    }


def _select(events: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    seq = spec["sequence"]
    gf = spec.get("group_filter")
    window = spec.get("window") or "D2_D4"
    rows = []
    for e in events:
        if e.get("sequence") != seq or not e.get("x0_entry_open"):
            continue
        blk = str(e.get("block") or "")
        if window == "D2_D4" and blk not in EVAL_BLOCKS:
            continue
        if gf and str(e.get("preq_group") or "") != str(gf):
            continue
        rows.append(e)
    rows.sort(key=lambda e: (str(e["date"]), str(e["event_time"]), str(e["symbol"])))
    return rows


def replay(events: list[dict[str, Any]], spec: dict[str, Any]) -> dict[str, Any]:
    rows = _select(events, spec)
    traded_today: dict[str, set[str]] = defaultdict(set)
    occ: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades = []
    skipped = {"cutoff": 0, "occupancy": 0, "same_symbol": 0, "no_exit": 0}
    for e in rows:
        day = str(e["date"])
        hh = str(e["event_time"])
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
            "block": e.get("block"),
            "preq_group": e.get("preq_group"),
            "sequence": spec["sequence"],
            **got,
            "exit_hh": exit_hh,
            "favor_first": e.get("favor_first"),
            "time_to_favorable": e.get("time_to_favorable"),
            "time_to_failure": e.get("time_to_failure"),
            "mfe_path_bps": e.get("mfe_bps"),
            "mae_path_bps": e.get("mae_bps"),
        }
        trades.append(trade)
        occ[day].append(trade)
        traded_today[day].add(str(e["symbol"]))
    empty = {
        "ok": False,
        "trade_n": 0,
        "day_n": 0,
        "symbol_n": 0,
        "skipped": skipped,
        "sequence": spec.get("sequence"),
        "candidate_id": spec.get("candidate_id"),
        "mean_x0_bps": None,
        "mean_x1_bps": None,
        "break_even_execution_bps": None,
        "block_mean_x0": {},
        "exit_reasons": {},
    }
    if not trades:
        return empty
    x0 = np.asarray([float(t["x0_bps"]) for t in trades], dtype=float)
    x1 = np.asarray([float(t["x1_bps"]) for t in trades], dtype=float)
    by_day: dict[str, list[float]] = defaultdict(list)
    by_block: dict[str, list[float]] = defaultdict(list)
    by_sym_pos: dict[str, float] = defaultdict(float)
    by_sym_n: dict[str, int] = defaultdict(int)
    for t in trades:
        by_day[t["date"]].append(float(t["x0_bps"]))
        if t.get("block"):
            by_block[str(t["block"])].append(float(t["x0_bps"]))
        by_sym_n[str(t["symbol"])] += 1
        if float(t["x0_bps"]) > 0:
            by_sym_pos[str(t["symbol"])] += float(t["x0_bps"])
    day_means = np.asarray([float(np.mean(vs)) for vs in by_day.values()], dtype=float)
    eq = np.cumsum(day_means)
    dd = float(np.min(eq - np.maximum.accumulate(eq))) if eq.size else 0.0
    pos = float(np.sum(x0[x0 > 0]))
    neg = float(-np.sum(x0[x0 < 0]))
    pf = (pos / neg) if neg > 0 else None
    block_mean = {k: float(np.mean(vs)) for k, vs in by_block.items()}
    mean_x0 = float(np.mean(x0))
    fav = [bool(t.get("favor_first")) for t in trades]
    ttf = [int(t["time_to_favorable"]) for t in trades if t.get("time_to_favorable") is not None]
    tfail = [int(t["time_to_failure"]) for t in trades if t.get("time_to_failure") is not None]
    mfe = [float(t["mfe_path_bps"]) for t in trades if t.get("mfe_path_bps") is not None]
    mae = [float(t["mae_path_bps"]) for t in trades if t.get("mae_path_bps") is not None]
    top_share = (max(by_sym_pos.values()) / pos) if pos > 0 and by_sym_pos else None
    return {
        "ok": True,
        "candidate_id": spec.get("candidate_id"),
        "sequence": spec.get("sequence"),
        "group_filter": spec.get("group_filter"),
        "window": spec.get("window"),
        "trade_n": int(x0.size),
        "day_n": len(by_day),
        "symbol_n": len({t["symbol"] for t in trades}),
        "sector_n": len({t.get("sector") for t in trades if t.get("sector")}),
        "mean_x0_bps": mean_x0,
        "mean_x1_bps": float(np.mean(x1)),
        "median_x0_bps": float(np.median(x0)),
        "daily_mean_bps": float(np.mean(day_means)) if day_means.size else None,
        "daily_median_bps": float(np.median(day_means)) if day_means.size else None,
        "hit_rate": float(np.mean(x0 > 0)),
        "profit_factor": pf,
        "max_dd_daily_mean_bps": dd,
        "break_even_execution_bps": mean_x0,
        "top_symbol_share_of_positive_bps": top_share,
        "symbol_n_map": dict(by_sym_n),
        "block_mean_x0": block_mean,
        "block_positive_n": sum(1 for v in block_mean.values() if v > 0),
        "block_n": len(block_mean),
        "favor_first_p": float(np.mean(fav)) if fav else None,
        "mean_time_to_favorable": float(np.mean(ttf)) if ttf else None,
        "mean_time_to_failure": float(np.mean(tfail)) if tfail else None,
        "mean_mfe_bps": float(np.mean(mfe)) if mfe else None,
        "mean_mae_bps": float(np.mean(mae)) if mae else None,
        "exit_reasons": {k: int(sum(1 for t in trades if t.get("exit_reason") == k)) for k in {t.get("exit_reason") for t in trades}},
        "skipped": skipped,
        "occupancy": OCCUPANCY,
        "same_symbol": "one_live_no_same_day_reentry",
        "session_close": SESSION_FLAT,
        "x1_tax_bps": X1_TAX_BPS,
        "not_bid_ask": True,
        "x1_tax_not_changed_to_rescue": True,
    }


def promote(econ: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    if spec.get("diagnostic"):
        return {"promoted": False, "fail_reasons": ["diagnostic_only"]}
    reasons = []
    min_sym = int(spec.get("min_symbol_n") or MIN_PLAYBOOK_SYMBOL_N)
    if int(econ.get("trade_n") or 0) < MIN_PLAYBOOK_TRADE_N:
        reasons.append("trade_n")
    if int(econ.get("day_n") or 0) < MIN_PLAYBOOK_DAY_N:
        reasons.append("day_n")
    if int(econ.get("symbol_n") or 0) < min_sym:
        reasons.append("symbol_n")
    if float(econ.get("mean_x1_bps") or -999) <= 0:
        reasons.append("mean_x1_not_positive")
    pf = econ.get("profit_factor")
    if pf is None or float(pf) < 1.10:
        reasons.append("profit_factor")
    if float(econ.get("top_symbol_share_of_positive_bps") or 0) >= 0.40:
        reasons.append("symbol_concentration")
    need_blocks = 2 if spec.get("window") == "D2_D4" else 3
    if int(econ.get("block_positive_n") or 0) < need_blocks:
        reasons.append("block_consistency")
    return {"promoted": not reasons, "fail_reasons": reasons}


def build_candidates(events: list[dict[str, Any]]) -> dict[str, Any]:
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
        spec["uses_full_discovery_group"] = False
        spec["spec_sha256"] = _spec_sha(spec)
        econ = replay(events, spec)
        out.append({"spec": spec, "economics": econ, "promotion": promote(econ, spec)})
    promoted = [c for c in out if c["promotion"]["promoted"]]
    return {
        "proposals": out,
        "promoted": promoted,
        "promoted_n": len(promoted),
        "designed_on_discovery_only": True,
        "old_confirmation_used_to_design": False,
        "hm1_tuned": False,
        "entry_only_success": False,
        "precommitted_pairs_only": True,
    }
