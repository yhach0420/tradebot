#!/usr/bin/env python3
"""Phase687W43E — Pullback-to-Reversal Transition and Data Quality Root Cause.

Research-only. Outputs only:
  w43e_report.md / w43e_report.json / w43e_audit.xlsx
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import warnings
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

warnings.filterwarnings("ignore", category=UserWarning)

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE / "scripts"))

import phase687w43c_watch50_future30m_opportunity as w43c  # noqa: E402
import phase687w43d_5day_winner_state_validation as w43d  # noqa: E402

JST = __import__("zoneinfo").ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
REPORTS = NATIVE / "results" / "reports"
PAPER = NATIVE / "results" / "small_paper"
PUSH_ROOT = NATIVE / "data" / "push_jsonl"
MAX_WORKERS = 4
RANDOM_ITERS = 50
STOP_MAE = -1.2
NO_PROGRESS_MFE = 0.3
NO_PROGRESS_RET = 0.2

DQ_CLASSES = [
    "CURRENT_PRICE_MISSING",
    "CURRENT_PRICE_STALE",
    "BOARD_MISSING",
    "BOARD_STALE",
    "FEATURE_HISTORY_INSUFFICIENT",
    "OPEN_WARMUP",
    "REFRESH_WARMUP",
    "TIMESTAMP_MISMATCH",
    "SUBSCRIPTION_GAP",
    "FEATURE_COMPUTE_FAILURE",
    "PIPELINE_ORDERING_FAILURE",
    "GENUINE_MARKET_STALE",
    "UNKNOWN_DATA_QUALITY",
]
IMPL_DQ = {
    "OPEN_WARMUP",
    "REFRESH_WARMUP",
    "FEATURE_HISTORY_INSUFFICIENT",
    "FEATURE_COMPUTE_FAILURE",
    "PIPELINE_ORDERING_FAILURE",
    "SUBSCRIPTION_GAP",
    "TIMESTAMP_MISMATCH",
}
MARKET_DQ = {"GENUINE_MARKET_STALE", "CURRENT_PRICE_STALE", "BOARD_STALE", "CURRENT_PRICE_MISSING", "BOARD_MISSING"}


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _hhmm(epoch: float) -> tuple[int, int]:
    dt = datetime.fromtimestamp(epoch, tz=JST)
    return dt.hour, dt.minute


def pick_sessions_relaxed(day: str) -> dict[str, Optional[Path]]:
    """Prefer W43D picker; fall back to largest event files by session clock when summary JSON missing."""
    sess = w43d.pick_sessions(day)
    if sess.get("am") or sess.get("pm"):
        return sess
    root = PAPER / day
    if not root.is_dir():
        return {"am": None, "pm": None}
    am = pm = None
    am_sz = pm_sz = -1
    for s in root.glob("live_session_*"):
        ev = s / "small_paper_events.jsonl"
        sz = ev.stat().st_size if ev.is_file() else 0
        if sz < 1_000_000:
            continue
        # live_session_HHMMSS
        try:
            hh = int(s.name.split("_")[-1][:2])
        except ValueError:
            hh = 12
        if hh < 12 and sz > am_sz:
            am, am_sz = s, sz
        elif hh >= 12 and sz > pm_sz:
            pm, pm_sz = s, sz
    # single large morning-only day
    if am is None and pm is None:
        best = None
        best_sz = 0
        for s in root.glob("live_session_*"):
            ev = s / "small_paper_events.jsonl"
            sz = ev.stat().st_size if ev.is_file() else 0
            if sz > best_sz:
                best, best_sz = s, sz
        if best and best_sz >= 1_000_000:
            am = best
    return {"am": am, "pm": pm}


def detect_days(n_market: int = 10) -> pd.DataFrame:
    push_days = sorted(
        [p.name for p in PUSH_ROOT.iterdir() if p.is_dir() and p.name.startswith("2026-")],
        reverse=True,
    )
    rows = []
    market = []
    for dash in push_days:
        day = dash.replace("-", "")
        n_push = len(list((PUSH_ROOT / dash).glob("*.jsonl")))
        uni = all(
            (REPORTS / f"universe_core10_dynamic40_price_risk_{s}_{day}.csv").is_file()
            for s in ("am", "am_refresh1000", "pm", "pm_refresh1430")
        )
        sess = pick_sessions_relaxed(day)
        has_sess = sess["am"] is not None or sess["pm"] is not None
        ev = 0
        for sk in ("am", "pm"):
            sd = sess.get(sk)
            if not sd:
                continue
            p = Path(sd) / "small_paper_events.jsonl"
            if p.is_file() and p.stat().st_size > 1_000_000:
                ev += 1
        market_ok = n_push >= 45 and uni
        runtime = bool(market_ok and has_sess and ev > 0 and day != "20260714")
        if day == "20260714":
            runtime = False
        row = {
            "trading_date": day,
            "date_iso": dash,
            "push_files": n_push,
            "universe_ok": uni,
            "MARKET_DATA_DAY": market_ok,
            "RUNTIME_ACTIVE_DAY": runtime,
            "MARKET_ONLY_DAY": bool(market_ok and not runtime),
            "EXCLUDED_DAY": not market_ok,
            "paper_sessions": int(has_sess),
            "event_files": ev,
            "am_session": str(sess["am"]) if sess["am"] else "",
            "pm_session": str(sess["pm"]) if sess["pm"] else "",
        }
        rows.append(row)
        if market_ok and len(market) < n_market:
            market.append(day)
        if len(market) >= n_market and dash < "2026-06-20":
            break
    df = pd.DataFrame(rows)
    df["in_analysis_window"] = df["trading_date"].isin(market)
    return df


def day_meta(day: str) -> dict[str, Any]:
    dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return {
        "day": day,
        "date": dash,
        "push_dir": PUSH_ROOT / dash,
        "sessions": pick_sessions_relaxed(day),
        "universe": {
            "am": REPORTS / f"universe_core10_dynamic40_price_risk_am_{day}.csv",
            "am_refresh": REPORTS / f"universe_core10_dynamic40_price_risk_am_refresh1000_{day}.csv",
            "pm": REPORTS / f"universe_core10_dynamic40_price_risk_pm_{day}.csv",
            "pm_refresh": REPORTS / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day}.csv",
        },
    }


def build_or_load_snaps(day: str, tmp: Path) -> pd.DataFrame:
    cache = tmp / f"snaps_{day}.parquet"
    if cache.is_file():
        print(f"  cache hit {day}", flush=True)
        return pd.read_parquet(cache)
    if day == "20260717":
        pq = OUT / "w43c_20260717_watch50_snapshot.parquet"
        if pq.is_file():
            df = w43d.enrich_features(pd.read_parquet(pq))
            df.to_parquet(cache, index=False)
            return df
    print(f"  building snapshots {day}", flush=True)
    df = w43d.run_day_snapshots(day_meta(day))
    df.to_parquet(cache, index=False)
    return df


def load_w43d_moves() -> pd.DataFrame:
    p = OUT / "w43d_5d_independent_moves.csv"
    if p.is_file():
        m = pd.read_csv(p)
        m["trading_date"] = m["trading_date"].astype(str)
        return m
    return pd.DataFrame()


def build_moves_for_day(snaps: pd.DataFrame, day: str) -> pd.DataFrame:
    meta = day_meta(day)
    entries = w43d.load_official_for_day(meta)
    ep = w43d.build_raw_episodes(snaps)
    if ep.empty:
        return ep
    ep = w43d.attach_capture(ep, entries)
    ep, _ = w43d.classify_causal_funnel(ep, [meta])
    moves = w43d.build_independent_moves(ep)
    return moves


def iter_session_events(day: str):
    sess = w43d.pick_sessions(day)
    for sk in ("am", "pm"):
        sd = sess.get(sk)
        if not sd:
            continue
        p = Path(sd) / "small_paper_events.jsonl"
        if not p.is_file():
            continue
        with p.open(encoding="utf-8") as f:
            for line in f:
                if '"event_type"' not in line:
                    continue
                if not any(x in line for x in ("rejected", "candidate", "accepted")):
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("event_type") not in ("rejected", "candidate", "accepted"):
                    continue
                et = w43c.parse_ts(o.get("entry_time") or o.get("event_time"))
                if et is None:
                    continue
                yield sk, o, w43c.to_epoch(et)


def classify_dq_reason(
    reason: str,
    *,
    price_age: Optional[float],
    board_age: Optional[float],
    current_price: Any,
    push_ups: Optional[float],
    hour: int,
    minute: int,
    ret60_nan: bool,
    ret300_nan: bool,
    segment: str,
) -> str:
    r = (reason or "").lower()
    # warmup first by clock / segment
    if (hour == 9 and minute < 10) or (segment == "am_open" and hour == 9 and minute < 15):
        if ret60_nan or ret300_nan or (price_age is not None and price_age > 5) or "stale" in r:
            return "OPEN_WARMUP"
    if segment in ("am_refresh1000",) and hour == 10 and minute < 10:
        if ret60_nan or ret300_nan or "stale" in r:
            return "REFRESH_WARMUP"
    if segment in ("pm_refresh1430",) and hour == 14 and minute < 40:
        if ret60_nan or ret300_nan or "stale" in r:
            return "REFRESH_WARMUP"

    if current_price is None or current_price == "" or (
        isinstance(current_price, float) and not math.isfinite(current_price)
    ):
        if "stale" in r or "missing" in r or "liquidity" in r:
            return "CURRENT_PRICE_MISSING"

    if "stale_board" in r or "data_stale_board" in r or "board_stale" in r:
        if push_ups is not None and push_ups < 0.05 and (board_age or 0) > 30:
            return "GENUINE_MARKET_STALE"
        if (board_age or 0) > 15 and (push_ups or 0) >= 0.2:
            return "PIPELINE_ORDERING_FAILURE"
        return "BOARD_STALE"

    if "stale_price" in r or "price_stale" in r or "liquidity_stale" in r or "data_stale" in r:
        if push_ups is not None and push_ups < 0.05 and (price_age or 0) > 30:
            return "GENUINE_MARKET_STALE"
        if (price_age or 0) > 15 and (push_ups or 0) >= 0.2:
            return "PIPELINE_ORDERING_FAILURE"
        return "CURRENT_PRICE_STALE"

    if "feature" in r and ("incomplete" in r or "missing" in r):
        return "FEATURE_COMPUTE_FAILURE"
    if ret300_nan and ret60_nan:
        return "FEATURE_HISTORY_INSUFFICIENT"
    if board_age is None and ("board" in r or "stale" in r):
        return "BOARD_MISSING"
    if "stale" in r:
        return "GENUINE_MARKET_STALE" if (push_ups or 0) < 0.05 else "UNKNOWN_DATA_QUALITY"
    return "UNKNOWN_DATA_QUALITY"


def audit_dq_moves(moves: pd.DataFrame, snaps: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dq = moves[moves["funnel_class"] == "DATA_QUALITY_BLOCKED"].copy()
    rows = []
    recovery_rows = []
    if dq.empty:
        return pd.DataFrame(), pd.DataFrame()

    for day, g in dq.groupby("trading_date"):
        # collect events once
        ev_by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for _sk, o, epoch in iter_session_events(str(day)):
            sym = str(o.get("symbol") or "")
            reason = str(
                o.get("final_reject_reason")
                or o.get("gate_reject_reason")
                or o.get("or_overlay_reason")
                or o.get("pbv2_internal_reason")
                or ""
            )
            if not any(k in reason.lower() for k in ("stale", "missing", "feature", "liquidity", "data_")):
                # still keep if ages extreme
                pa = _f(o.get("price_age_sec"))
                ba = _f(o.get("board_age_sec"))
                if not ((pa is not None and pa > 10) or (ba is not None and ba > 10)):
                    continue
            ev_by_sym[sym].append(
                {
                    "epoch": epoch,
                    "reason": reason,
                    "price_age": _f(o.get("price_age_sec")),
                    "board_age": _f(o.get("board_age_sec")),
                    "current_price": o.get("current_price"),
                    "event_type": o.get("event_type"),
                    "score_v2": _f(o.get("entry_expectancy_score_v2")),
                    "pbv2": o.get("pbv2_internal_reason"),
                }
            )

        for _, m in g.iterrows():
            sym = str(m["symbol"])
            a0 = float(m["anchor_epoch"])
            lo, hi = a0 - 30, a0 + 300
            evs = [e for e in ev_by_sym.get(sym, []) if lo <= e["epoch"] <= hi]
            # snapshot context at/after anchor
            try:
                sg = snaps[(snaps["trading_date"].astype(str) == str(day)) & (snaps["symbol"] == sym)].sort_values(
                    "t0_epoch"
                )
            except Exception:
                sg = pd.DataFrame()
            near = sg[(sg["t0_epoch"] >= a0 - 30) & (sg["t0_epoch"] <= a0 + 300)] if len(sg) else sg
            row0 = None
            if len(near):
                row0 = near.iloc[(near["t0_epoch"] - a0).abs().argmin()]
            elif len(sg):
                row0 = sg.iloc[(sg["t0_epoch"] - a0).abs().argmin()]

            price_age = evs[0]["price_age"] if evs else (_f(row0.get("price_age_sec")) if row0 is not None else None)
            board_age = evs[0]["board_age"] if evs else (_f(row0.get("board_age_sec")) if row0 is not None else None)
            cur_px = evs[0]["current_price"] if evs else None
            push_ups = _f(row0.get("push_updates_per_sec_60s")) if row0 is not None else None
            ret60_nan = row0 is None or not np.isfinite(_f(row0.get("ret_60s")) or np.nan)
            ret300_nan = row0 is None or not np.isfinite(_f(row0.get("ret_300s")) or np.nan)
            hh, mm = _hhmm(a0)
            segment = str(m.get("universe_segment") or "")
            reason = evs[0]["reason"] if evs else str(m.get("funnel_detail") or "")
            # prefer first DQ-looking reason
            for e in evs:
                if any(k in e["reason"].lower() for k in ("stale", "missing", "feature", "liquidity")):
                    reason = e["reason"]
                    price_age, board_age, cur_px = e["price_age"], e["board_age"], e["current_price"]
                    break

            klass = classify_dq_reason(
                reason,
                price_age=price_age,
                board_age=board_age,
                current_price=cur_px,
                push_ups=push_ups,
                hour=hh,
                minute=mm,
                ret60_nan=ret60_nan,
                ret300_nan=ret300_nan,
                segment=segment,
            )
            # recovery: first snapshot after block with finite price ages and ret_30s
            first_block = min((e["epoch"] for e in evs), default=a0)
            last_block = max((e["epoch"] for e in evs), default=a0)
            first_valid = None
            fut = {}
            if len(sg):
                after = sg[sg["t0_epoch"] >= first_block]
                for _, r in after.iterrows():
                    pa = _f(r.get("price_age_sec"))
                    ba = _f(r.get("board_age_sec"))
                    if pa is not None and pa <= 5 and ba is not None and ba <= 5 and _f(r.get("ret_30s")) is not None:
                        first_valid = float(r["t0_epoch"])
                        fut = {
                            "future_return_from_first_valid": _f(r.get("future_30m_return")),
                            "future_mfe_from_first_valid": _f(r.get("future_30m_mfe")),
                            "future_mae_from_first_valid": _f(r.get("future_30m_mae")),
                            "primary_label_at_valid": r.get("primary_label"),
                        }
                        break
            cand_after = any(e["event_type"] == "candidate" and e["epoch"] >= (first_valid or 1e18) for e in evs)
            # official entry after recovery from moves capture
            official_after = bool(
                m.get("capture_class") in ("CAPTURED_5M", "LATE_CAPTURED_15M")
                and first_valid is not None
                and _f(m.get("secs_to_entry")) is not None
            )
            already_up = False
            if first_valid is not None and len(sg):
                arow = sg.iloc[(sg["t0_epoch"] - a0).abs().argmin()]
                vrow = sg.iloc[(sg["t0_epoch"] - first_valid).abs().argmin()]
                pa = _f(arow.get("ret_30s"))
                # price change from anchor to valid using future path proxy: ret between snapshots via day
                # use difference in ret_300s as rough; better: compare prices if present - not always
                r_anchor = _f(arow.get("future_30m_return"))
                # "already risen" if from anchor to valid the short ret flipped positive strongly
                already_up = bool((_f(vrow.get("ret_30s")) or 0) > 0.3 and (_f(arow.get("ret_30s")) or 0) < 0)

            rows.append(
                {
                    "trading_date": day,
                    "symbol": sym,
                    "independent_move_id": m.get("independent_move_id"),
                    "anchor_time": m.get("anchor_time"),
                    "anchor_epoch": a0,
                    "dq_class": klass,
                    "origin": "implementation" if klass in IMPL_DQ else ("market" if klass in MARKET_DQ else "unknown"),
                    "reason_sample": reason[:200],
                    "price_age_sec": price_age,
                    "board_age_sec": board_age,
                    "push_updates_per_sec_60s": push_ups,
                    "universe_segment": segment,
                    "first_block_time": datetime.fromtimestamp(first_block, tz=JST).isoformat(),
                    "last_block_time": datetime.fromtimestamp(last_block, tz=JST).isoformat(),
                    "blocked_duration_sec": max(0.0, last_block - first_block),
                    "first_valid_time_after_block": (
                        datetime.fromtimestamp(first_valid, tz=JST).isoformat() if first_valid else None
                    ),
                    "validity_recovered": first_valid is not None,
                    "recovery_delay_sec": (first_valid - first_block) if first_valid else None,
                    "candidate_after_recovery": cand_after,
                    "official_entry_after_recovery": official_after,
                    "already_risen_at_recovery": already_up,
                    **fut,
                    "max_future_mfe_at_anchor": _f(m.get("max_future_mfe")),
                }
            )
            recovery_rows.append(rows[-1])

    return pd.DataFrame(rows), pd.DataFrame(recovery_rows)


def audit_pbv2_moves(moves: pd.DataFrame) -> pd.DataFrame:
    pb = moves[moves["funnel_class"] == "PBV2_BASE_NOT_CANDIDATE"].copy()
    rows = []
    if pb.empty:
        return pd.DataFrame()
    for day, g in pb.groupby("trading_date"):
        ev_by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for _sk, o, epoch in iter_session_events(str(day)):
            sym = str(o.get("symbol") or "")
            ev_by_sym[sym].append(
                {
                    "epoch": epoch,
                    "event_type": o.get("event_type"),
                    "reason": str(
                        o.get("final_reject_reason")
                        or o.get("gate_reject_reason")
                        or o.get("or_overlay_reason")
                        or ""
                    ),
                    "pbv2": str(o.get("pbv2_internal_reason") or ""),
                    "pbv2_gate": str(o.get("pbv2_internal_gate") or ""),
                    "score_v2": _f(o.get("entry_expectancy_score_v2")),
                    "score_gate_pass": o.get("entry_score_v2_gate_pass"),
                    "mom": _f(o.get("entry_momentum_score") if o.get("entry_momentum_score") is not None else o.get("momentum_continuation_score")),
                    "cont": _f(o.get("continuation_quality_score")),
                    "board_mid": o.get("entry_board_mid_token_active"),
                    "imbalance": _f(o.get("entry_order_book_imbalance")),
                    "flat": o.get("pbv2_flat_band_shadow_block") or o.get("pbv2_flat_band_variant"),
                }
            )
        for _, m in g.iterrows():
            sym = str(m["symbol"])
            a0 = float(m["anchor_epoch"])
            evs = [e for e in ev_by_sym.get(sym, []) if a0 - 30 <= e["epoch"] <= a0 + 300]
            if not evs:
                rows.append(
                    {
                        "trading_date": day,
                        "symbol": sym,
                        "independent_move_id": m.get("independent_move_id"),
                        "pbv2_fail_class": "candidate_evaluation_not_run",
                        "candidate_seen": False,
                        "first_candidate_time": None,
                        "score_v2": None,
                        "momentum_state": None,
                        "board_state": None,
                    }
                )
                continue
            mom_vals = [e["mom"] for e in evs if e["mom"] is not None]
            board_flags = [e["board_mid"] for e in evs if e["board_mid"] is not None]
            scores = [e["score_v2"] for e in evs if e["score_v2"] is not None]
            pbv2_rs = [e["pbv2"] for e in evs if e["pbv2"]]
            reasons = [e["reason"] for e in evs]
            cand = any(e["event_type"] == "candidate" for e in evs)
            first_cand = min((e["epoch"] for e in evs if e["event_type"] == "candidate"), default=None)

            # PBv2 Momentum:low ≈ momentum_continuation_score <= ~p33 (research proxy 0.0 / low)
            MOM_CUT = 0.2546  # TERTILE_CUTOFFS Momentum p33
            mom_low = False
            if mom_vals:
                mom_low = max(mom_vals) <= MOM_CUT
            mom_low = mom_low or any("momentum" in (p or "").lower() for p in pbv2_rs)
            board_true = [bool(x) for x in board_flags]
            board_ok = any(board_true) if board_true else None
            board_low = (board_ok is False) or (
                board_true and not any(board_true)
            ) or any("board" in (p or "").lower() and "mid" not in (p or "").lower() for p in pbv2_rs)
            if board_flags and not any(bool(x) for x in board_flags):
                board_low = True
            score_avail = len(scores) > 0
            score_low = score_avail and max(scores) < 3
            score_before = not score_avail

            together = False
            for e in evs:
                if e["mom"] is not None and e["board_mid"] is not None:
                    if e["mom"] > MOM_CUT and bool(e["board_mid"]):
                        together = True
                        break
            sync_fail = (not together) and (len(mom_vals) > 0 and len(board_flags) > 0)

            if score_before and not mom_vals and not board_flags:
                fail = "score_not_computed_yet"
            elif sync_fail and mom_low and board_low:
                fail = "momentum_and_board_not_simultaneous"
            elif sync_fail and (mom_low or board_low):
                fail = "momentum_and_board_not_simultaneous"
            elif mom_low and board_low:
                fail = "momentum_and_board_both_weak"
            elif mom_low:
                fail = "momentum_insufficient"
            elif board_low:
                fail = "board_insufficient"
            elif score_low:
                fail = "score_insufficient"
            elif not cand and all(
                ("or_overlay_not_candidate" in (r or "")) or (not r) for r in reasons
            ):
                fail = "or_overlay_internal_other"
            elif not cand:
                fail = "candidate_evaluation_not_run"
            else:
                fail = "or_overlay_internal_other"

            rows.append(
                {
                    "trading_date": day,
                    "symbol": sym,
                    "independent_move_id": m.get("independent_move_id"),
                    "anchor_time": m.get("anchor_time"),
                    "pbv2_fail_class": fail,
                    "candidate_seen": cand,
                    "first_candidate_time": (
                        datetime.fromtimestamp(first_cand, tz=JST).isoformat() if first_cand else None
                    ),
                    "score_v2": max(scores) if scores else None,
                    "score_v2_available": score_avail,
                    "momentum_state": ("low" if mom_low else ("ok" if mom_vals else "unknown")),
                    "board_state": (
                        "mid_or_high" if board_ok else ("below_mid" if board_ok is False else "unknown")
                    ),
                    "continuation_quality": max([e["cont"] for e in evs if e["cont"] is not None], default=None),
                    "pbv2_internal_sample": (pbv2_rs[0] if pbv2_rs else None),
                    "reason_sample": (reasons[0] if reasons else None),
                    "n_traces": len(evs),
                }
            )
    return pd.DataFrame(rows)


def assign_states(df: pd.DataFrame) -> pd.DataFrame:
    """Assign exclusive realtime state per snapshot row (no future peeking)."""
    df = df.sort_values(["trading_date", "symbol", "t0_epoch"]).copy()
    ret10 = pd.to_numeric(df.get("ret_10s"), errors="coerce")
    ret30 = pd.to_numeric(df.get("ret_30s"), errors="coerce")
    ret120 = pd.to_numeric(df.get("ret_120s"), errors="coerce")
    slope60 = pd.to_numeric(df.get("slope_60s"), errors="coerce")
    a30 = pd.to_numeric(df.get("accel_30s"), errors="coerce")
    a60 = pd.to_numeric(df.get("accel_60s"), errors="coerce")
    imb = pd.to_numeric(df.get("imbalance_chg_60s"), errors="coerce")
    askp = pd.to_numeric(df.get("net_ask_pressure_60s"), errors="coerce")
    volr = pd.to_numeric(df.get("vol_recovery_flag"), errors="coerce")
    vwap = pd.to_numeric(df.get("vwap_reclaim_flag"), errors="coerce")
    nh = pd.to_numeric(df.get("new_high_restart_count"), errors="coerce")
    bounce = pd.to_numeric(df.get("bounce_from_low_300s"), errors="coerce")
    fall = pd.to_numeric(df.get("fall_from_high_300s"), errors="coerce")

    # previous ret_30s for improvement
    df["_ret30_prev"] = ret30.groupby([df["trading_date"], df["symbol"]]).shift(1)
    ret30_imp = ret30 > df["_ret30_prev"]

    rising = (ret30.fillna(-1) >= 0) & (ret120.fillna(-1) >= 0) & (slope60.fillna(-1) >= 0)
    pullback = (ret30.fillna(0) < 0) | (ret120.fillna(0) < 0)

    rev_signals = (
        (ret10.fillna(0) > 0).astype(int)
        + ret30_imp.fillna(False).astype(int)
        + (a30.fillna(0) > 0).astype(int)
        + (a60.fillna(0) > 0).astype(int)
        + ((bounce.fillna(0) > 0) & (fall.fillna(0) > -1)).astype(int)  # local low bounce proxy
        + (imb.fillna(0) > 0).astype(int)
        + (askp.fillna(0) < 0).astype(int)
        + (volr.fillna(0) > 0).astype(int)
        + (vwap.fillna(0) > 0).astype(int)
        + (nh.fillna(0) > 0).astype(int)
    )
    df["_rev_signals"] = rev_signals
    started = pullback & (rev_signals >= 2)
    decel = pullback & (~started) & ((a30.fillna(0) > 0) | (a60.fillna(0) > 0))
    falling = pullback & (~started) & (~decel)

    # sequential refinement for persisting / failed / breakout
    state = np.array(["RISING_CONTINUATION"] * len(df), dtype=object)
    state = np.where(rising, "RISING_CONTINUATION", state)
    state = np.where(falling, "PULLBACK_FALLING", state)
    state = np.where(decel, "DECELERATING_DECLINE", state)
    state = np.where(started, "REVERSAL_STARTED", state)
    # default non-pullback non-rising
    other = (~rising) & (~pullback)
    state = np.where(other, "RISING_CONTINUATION", state)  # mild up / flat → continuation bucket

    df["realtime_state"] = state
    # persisting / failed / breakout via group scan
    out_states = []
    flap_counts = []
    for (day, sym), g in df.groupby(["trading_date", "symbol"], sort=False):
        prev = None
        start_epoch = None
        start_low = None
        persist_since = None
        flap = 0
        last_non = None
        local_states = []
        epochs = g["t0_epoch"].to_numpy()
        prices_proxy = pd.to_numeric(g.get("ret_30s"), errors="coerce").to_numpy()  # relative
        bounce_a = pd.to_numeric(g.get("bounce_from_low_300s"), errors="coerce").to_numpy()
        nh_a = pd.to_numeric(g.get("new_high_restart_count"), errors="coerce").to_numpy()
        vwap_a = pd.to_numeric(g.get("vwap_reclaim_flag"), errors="coerce").to_numpy()
        fall_a = pd.to_numeric(g.get("fall_from_high_300s"), errors="coerce").to_numpy()
        base = g["realtime_state"].to_numpy()
        for i in range(len(g)):
            st = base[i]
            if prev == "REVERSAL_STARTED" or prev == "REVERSAL_PERSISTING":
                # fail if ret worsens a lot within 60s of start
                if start_epoch is not None and epochs[i] - start_epoch <= 60:
                    if prices_proxy[i] is not None and start_low is not None:
                        if np.isfinite(prices_proxy[i]) and prices_proxy[i] < start_low - 0.15:
                            st = "REVERSAL_FAILED"
                if prev in ("REVERSAL_STARTED", "REVERSAL_PERSISTING") and st == "REVERSAL_STARTED":
                    if persist_since is None:
                        persist_since = epochs[i]
                    if epochs[i] - (start_epoch or epochs[i]) >= 30:
                        # no new low: bounce not collapsing
                        if bounce_a[i] is not None and np.isfinite(bounce_a[i]) and bounce_a[i] >= 0:
                            st = "REVERSAL_PERSISTING"
                if st in ("REVERSAL_STARTED", "REVERSAL_PERSISTING"):
                    if (nh_a[i] or 0) > 0 or (vwap_a[i] or 0) > 0:
                        st = "BREAKOUT_CONFIRMED"
            if st == "REVERSAL_STARTED" and prev != "REVERSAL_STARTED":
                start_epoch = epochs[i]
                start_low = prices_proxy[i]
                persist_since = epochs[i]
            if prev is not None and st != prev:
                if last_non == st:
                    flap += 1
                last_non = prev
            local_states.append(st)
            prev = st
        out_states.extend(local_states)
        flap_counts.extend([flap] * len(g))
    df["realtime_state"] = out_states
    df["state_flap_count"] = flap_counts
    df.drop(columns=["_ret30_prev"], errors="ignore", inplace=True)
    return df


def state_transitions(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (day, sym), g in df.groupby(["trading_date", "symbol"], sort=False):
        g = g.sort_values("t0_epoch")
        prev_st = None
        prev_t = None
        for _, r in g.iterrows():
            st = r["realtime_state"]
            t = float(r["t0_epoch"])
            if prev_st is not None and st != prev_st:
                rows.append(
                    {
                        "trading_date": day,
                        "symbol": sym,
                        "session": r.get("session"),
                        "state_from": prev_st,
                        "state_to": st,
                        "transition_time": r.get("t0_time"),
                        "transition_epoch": t,
                        "time_in_previous_state": t - prev_t if prev_t is not None else None,
                        "ret_30s": _f(r.get("ret_30s")),
                        "ret_120s": _f(r.get("ret_120s")),
                        "accel_30s": _f(r.get("accel_30s")),
                        "accel_60s": _f(r.get("accel_60s")),
                        "spread_bps": _f(r.get("spread_bps")),
                        "seconds_since_last_new_high": _f(r.get("seconds_since_last_new_high")),
                        "volume_recovery": _f(r.get("vol_recovery_flag")),
                        "board_change": _f(r.get("imbalance_chg_60s")),
                        "vwap_state": _f(r.get("vwap_reclaim_flag")),
                        "state_flap_count": int(r.get("state_flap_count") or 0),
                        "future_5m_return": _f(r.get("future_5m_return")),
                        "future_10m_return": _f(r.get("future_10m_return")),
                        "future_15m_return": _f(r.get("future_15m_return")),
                        "future_30m_return": _f(r.get("future_30m_return")),
                        "future_30m_mfe": _f(r.get("future_30m_mfe")),
                        "future_30m_mae": _f(r.get("future_30m_mae")),
                        "primary_label": r.get("primary_label"),
                    }
                )
            prev_st = st
            prev_t = t
    return pd.DataFrame(rows)


def outcome_metrics(sub: pd.DataFrame) -> dict[str, Any]:
    if sub is None or len(sub) == 0:
        return {
            "n": 0,
            "valid_label_count": 0,
            "large_rise_precision": None,
            "mean_future_30m_return": None,
            "mean_future_30m_mfe": None,
            "mean_future_30m_mae": None,
            "stop_proxy_rate": None,
            "no_progress_proxy_rate": None,
        }
    lab = sub["primary_label"] if "primary_label" in sub.columns else pd.Series([None] * len(sub))
    valid = sub[lab.notna() & (lab != "UNAVAILABLE")] if lab is not None else sub
    n_valid = len(valid)
    mfe = pd.to_numeric(valid.get("future_30m_mfe"), errors="coerce")
    mae = pd.to_numeric(valid.get("future_30m_mae"), errors="coerce")
    ret = pd.to_numeric(valid.get("future_30m_return"), errors="coerce")
    ret15 = pd.to_numeric(valid.get("future_15m_return"), errors="coerce")
    # NO_PROGRESS needs 15m mfe - approximate with min(mfe path) unavailable → use 15m return band + mfe<0.3
    stop = (mae <= STOP_MAE) if mae is not None else pd.Series([False] * n_valid)
    nop = (mfe < NO_PROGRESS_MFE) & (ret15.abs() <= NO_PROGRESS_RET) if n_valid else pd.Series(dtype=bool)
    # if future_15m missing, fallback
    if n_valid and nop.isna().all():
        nop = (mfe < NO_PROGRESS_MFE) & (ret.abs() <= NO_PROGRESS_RET)
    return {
        "n": int(len(sub)),
        "valid_label_count": int(n_valid),
        "large_rise_precision": float((valid["primary_label"] == "LARGE_RISE").mean()) if n_valid else None,
        "mean_future_30m_return": _f(ret.mean()),
        "mean_future_30m_mfe": _f(mfe.mean()),
        "mean_future_30m_mae": _f(mae.mean()),
        "stop_proxy_rate": float(stop.mean()) if n_valid else None,
        "no_progress_proxy_rate": float(nop.mean()) if n_valid else None,
        "sideways_rate": float((valid["primary_label"] == "SIDEWAYS").mean()) if n_valid else None,
        "decline_rate": float((valid["primary_label"] == "DECLINE").mean()) if n_valid else None,
    }


def confirmation_delay_analysis(snaps: pd.DataFrame) -> pd.DataFrame:
    """Evaluate entry delays from REVERSAL_STARTED transitions."""
    rows = []
    delays = [0, 30, 60, 90, 120]  # seconds; 15s not on 30s grid → skip or nearest
    # also special states
    for day, sdf in snaps.groupby("trading_date"):
        for sym, g in sdf.groupby("symbol"):
            g = g.sort_values("t0_epoch")
            states = g["realtime_state"].to_numpy()
            epochs = g["t0_epoch"].to_numpy()
            # find first STARTED in each streak
            i = 0
            while i < len(g):
                if states[i] != "REVERSAL_STARTED":
                    i += 1
                    continue
                t0 = float(epochs[i])
                # streak end
                j = i
                while j < len(g) and states[j] in ("REVERSAL_STARTED", "REVERSAL_PERSISTING", "BREAKOUT_CONFIRMED"):
                    j += 1
                # delay entries
                for dly in delays:
                    target = t0 + dly
                    # nearest snapshot at or after target within 15s
                    after = g[g["t0_epoch"] >= target - 1e-6]
                    if after.empty:
                        continue
                    r = after.iloc[0]
                    if float(r["t0_epoch"]) - target > 20:
                        continue
                    failed = bool(
                        ((g["t0_epoch"] >= t0) & (g["t0_epoch"] <= t0 + 60) & (g["realtime_state"] == "REVERSAL_FAILED")).any()
                    )
                    rows.append(
                        {
                            "trading_date": day,
                            "symbol": sym,
                            "delay_label": f"{dly}s",
                            "delay_sec": dly,
                            "entry_epoch": float(r["t0_epoch"]),
                            "primary_label": r.get("primary_label"),
                            "future_5m_return": _f(r.get("future_5m_return")),
                            "future_15m_return": _f(r.get("future_15m_return")),
                            "future_30m_return": _f(r.get("future_30m_return")),
                            "future_30m_mfe": _f(r.get("future_30m_mfe")),
                            "future_30m_mae": _f(r.get("future_30m_mae")),
                            "confirmation_failure": failed,
                            "price_worse": bool((_f(r.get("ret_30s")) or 0) > 0.2),  # already bounced a lot
                        }
                    )
                # persisting / breakout first times after start
                for lab, stname in (("REVERSAL_PERSISTING", "REVERSAL_PERSISTING"), ("BREAKOUT_CONFIRMED", "BREAKOUT_CONFIRMED")):
                    hit = g[(g["t0_epoch"] >= t0) & (g["realtime_state"] == stname)]
                    if hit.empty:
                        continue
                    r = hit.iloc[0]
                    rows.append(
                        {
                            "trading_date": day,
                            "symbol": sym,
                            "delay_label": lab,
                            "delay_sec": float(r["t0_epoch"] - t0),
                            "entry_epoch": float(r["t0_epoch"]),
                            "primary_label": r.get("primary_label"),
                            "future_5m_return": _f(r.get("future_5m_return")),
                            "future_15m_return": _f(r.get("future_15m_return")),
                            "future_30m_return": _f(r.get("future_30m_return")),
                            "future_30m_mfe": _f(r.get("future_30m_mfe")),
                            "future_30m_mae": _f(r.get("future_30m_mae")),
                            "confirmation_failure": False,
                            "price_worse": bool((_f(r.get("ret_30s")) or 0) > 0.2),
                        }
                    )
                i = max(i + 1, j)
    detail = pd.DataFrame(rows)
    summary = []
    if detail.empty:
        return pd.DataFrame()
    for lab, g in detail.groupby("delay_label"):
        m = outcome_metrics(g)
        m.update(
            {
                "delay_label": lab,
                "mean_delay_sec": _f(pd.to_numeric(g["delay_sec"], errors="coerce").mean()),
                "confirmation_failure_rate": float(g["confirmation_failure"].mean()),
                "entry_price_worse_rate": float(g["price_worse"].mean()),
            }
        )
        summary.append(m)
    return pd.DataFrame(summary).sort_values("delay_label")


def scan_queue_audit(moves: pd.DataFrame) -> pd.DataFrame:
    sq = moves[moves["funnel_class"] == "SCAN_OR_QUEUE_LIMITED"]
    rows = []
    for day, g in sq.groupby("trading_date"):
        for _, m in g.iterrows():
            a0 = float(m["anchor_epoch"])
            ranks = []
            later_entry = False
            for _sk, o, epoch in iter_session_events(str(day)):
                if str(o.get("symbol") or "") != str(m["symbol"]):
                    continue
                if abs(epoch - a0) > 900:
                    continue
                if o.get("same_scan_rank") is not None:
                    ranks.append(_f(o.get("same_scan_rank")))
                if o.get("event_type") == "accepted" and epoch >= a0:
                    later_entry = True
            rows.append(
                {
                    "trading_date": day,
                    "symbol": m["symbol"],
                    "anchor_time": m.get("anchor_time"),
                    "min_same_scan_rank": min([r for r in ranks if r is not None], default=None),
                    "n_rank_traces": len(ranks),
                    "later_official_entry": later_entry,
                    "max_future_mfe": _f(m.get("max_future_mfe")),
                    "detail": str(m.get("funnel_detail") or "")[:180],
                }
            )
    return pd.DataFrame(rows)


def lod_reversal(snaps: pd.DataFrame, entries: pd.DataFrame) -> pd.DataFrame:
    """LOD: train directions on ret_120s/accel; compare state entries vs PBv2."""
    rows = []
    days = sorted(snaps["trading_date"].astype(str).unique())
    for hold in days:
        train = snaps[snaps["trading_date"].astype(str) != hold]
        test = snaps[snaps["trading_date"].astype(str) == hold]
        # train medians for scaling
        feats = ["ret_120s", "ret_30s", "accel_60s"]
        params = {}
        for f in feats:
            x = pd.to_numeric(train[f], errors="coerce").dropna()
            if len(x) < 100:
                continue
            iqr = float(x.quantile(0.75) - x.quantile(0.25))
            if iqr <= 1e-12:
                continue
            # direction: lower ret_120 associated with LARGE_RISE in W43D → negative direction for rise score? 
            # For reversal entry we want started state; score = -z(ret_120)+z(accel)
            params[f] = {"med": float(x.median()), "iqr": iqr}
        if len(params) < 2:
            continue

        def score(df):
            s = np.zeros(len(df))
            if "ret_120s" in params:
                z = (pd.to_numeric(df["ret_120s"], errors="coerce") - params["ret_120s"]["med"]) / params["ret_120s"]["iqr"]
                s += -z.fillna(0).to_numpy()  # pullback favored
            if "accel_60s" in params:
                z = (pd.to_numeric(df["accel_60s"], errors="coerce") - params["accel_60s"]["med"]) / params["accel_60s"]["iqr"]
                s += z.fillna(0).to_numpy()
            return s

        for method, mask_fn in (
            ("reversal_started", lambda d: d["realtime_state"] == "REVERSAL_STARTED"),
            ("reversal_persisting", lambda d: d["realtime_state"] == "REVERSAL_PERSISTING"),
            ("breakout_confirmed", lambda d: d["realtime_state"] == "BREAKOUT_CONFIRMED"),
        ):
            sub = test[mask_fn(test)]
            if sub.empty:
                continue
            # take top-score per timestamp top3
            sub = sub.copy()
            sub["_sc"] = score(sub)
            picked = []
            for t0, slot in sub.groupby("t0_epoch"):
                picked.append(slot.sort_values("_sc", ascending=False).head(3))
            if not picked:
                continue
            sel = pd.concat(picked)
            m = outcome_metrics(sel)
            # random
            rnd = []
            times = sel["t0_epoch"].unique()
            for t0 in times[:200]:
                slot = test[test["t0_epoch"] == t0]
                slot = slot[slot["primary_label"].notna() & (slot["primary_label"] != "UNAVAILABLE")]
                if len(slot) < 3:
                    continue
                for it in range(min(10, RANDOM_ITERS)):
                    rng = np.random.default_rng(43 + int(t0) % 10000 + it)
                    idx = rng.choice(slot.index.to_numpy(), size=3, replace=False)
                    rnd.append(float((slot.loc[idx, "primary_label"] == "LARGE_RISE").mean()))
            # pbv2 official near times
            eday = entries[entries["trading_date"].astype(str) == str(hold)] if not entries.empty else entries
            off = []
            if not eday.empty:
                for _, e in eday.iterrows():
                    slot = test[test["symbol"] == e["symbol"]]
                    if slot.empty:
                        continue
                    j = (slot["t0_epoch"] - e["entry_epoch"]).abs().idxmin()
                    off.append(slot.loc[j])
            om = outcome_metrics(pd.DataFrame(off)) if off else outcome_metrics(pd.DataFrame())
            rows.append(
                {
                    "holdout_day": hold,
                    "method": method,
                    **{f"sel_{k}": v for k, v in m.items()},
                    "random_precision_mean": float(np.mean(rnd)) if rnd else None,
                    "delta_vs_random": (m["large_rise_precision"] - float(np.mean(rnd)))
                    if (m["large_rise_precision"] is not None and rnd)
                    else None,
                    "pbv2_precision": om["large_rise_precision"],
                    "delta_vs_pbv2": (
                        m["large_rise_precision"] - om["large_rise_precision"]
                        if m["large_rise_precision"] is not None and om["large_rise_precision"] is not None
                        else None
                    ),
                    "pbv2_mean_ret": om["mean_future_30m_return"],
                    "sel_minus_pbv2_ret": (
                        (m["mean_future_30m_return"] - om["mean_future_30m_return"])
                        if m["mean_future_30m_return"] is not None and om["mean_future_30m_return"] is not None
                        else None
                    ),
                }
            )
    return pd.DataFrame(rows)


def write_xlsx(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for line in [
        ["W43E audit"],
        ["generated", datetime.now(JST).isoformat()],
        ["outputs", "w43e_report.md / w43e_report.json / w43e_audit.xlsx"],
        ["runtime_unchanged", True],
    ]:
        ws.append(line)
    for name, df in sheets.items():
        w = wb.create_sheet(name[:31])
        if df is None or df.empty:
            w.append(["empty"])
            continue
        out = df.copy()
        # cap huge sheets
        if len(out) > 100000:
            out = out.head(100000)
        for row in dataframe_to_rows(out, index=False, header=True):
            w.append(row)
        w.auto_filter.ref = w.dimensions
        w.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    print("W43E starting...", flush=True)
    day_df = detect_days(10)
    market_days = day_df.loc[day_df["in_analysis_window"] & day_df["MARKET_DATA_DAY"], "trading_date"].tolist()
    runtime_days = day_df.loc[
        day_df["trading_date"].isin(market_days) & day_df["RUNTIME_ACTIVE_DAY"], "trading_date"
    ].tolist()
    print(f" market_days={market_days}", flush=True)
    print(f" runtime_days={runtime_days} (n={len(runtime_days)})", flush=True)
    if len(runtime_days) < 6:
        print(" WARNING: runtime active days < 6; using all available", flush=True)

    prior = load_w43d_moves()
    # cache across resume attempts; deleted in finally after success
    tmp = OUT / "_w43e_snap_cache"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        snap_parts = []
        move_parts = []
        for day in market_days:
            print(f"Day {day}...", flush=True)
            sn = build_or_load_snaps(day, tmp)
            sn["trading_date"] = sn["trading_date"].astype(str)
            snap_parts.append(sn)
            if not prior.empty and day in set(prior["trading_date"].astype(str)):
                move_parts.append(prior[prior["trading_date"].astype(str) == day])
                print(f"  reused W43D moves n={len(move_parts[-1])}", flush=True)
            elif day in runtime_days or day_df.loc[day_df.trading_date == day, "MARKET_DATA_DAY"].iloc[0]:
                # build moves for market days (funnel only meaningful for runtime)
                mv = build_moves_for_day(sn, day)
                if not mv.empty:
                    if day not in runtime_days:
                        mv = mv.copy()
                        mv["funnel_class"] = "MARKET_ONLY_NO_RUNTIME_EVALUATION"
                    move_parts.append(mv)
                    print(f"  built moves n={len(mv)}", flush=True)

        snaps = pd.concat(snap_parts, ignore_index=True)
        moves = pd.concat(move_parts, ignore_index=True) if move_parts else pd.DataFrame()
        print(f" snaps={len(snaps)} moves={len(moves)}", flush=True)

        # enrich features if needed
        if "accel_30s" not in snaps.columns:
            snaps = w43d.enrich_features(snaps)

        runtime_moves = moves[moves["trading_date"].astype(str).isin(runtime_days)].copy() if not moves.empty else moves

        print("Track A DQ...", flush=True)
        dq_df, dq_rec = audit_dq_moves(runtime_moves, snaps[snaps["trading_date"].astype(str).isin(runtime_days)])
        dq_counts = Counter(dq_df["dq_class"]) if len(dq_df) else Counter()
        dq_daily = (
            dq_df.groupby(["trading_date", "dq_class"]).size().reset_index(name="n") if len(dq_df) else pd.DataFrame()
        )

        print("Track B PBv2...", flush=True)
        pb_df = audit_pbv2_moves(runtime_moves)
        pb_counts = Counter(pb_df["pbv2_fail_class"]) if len(pb_df) else Counter()

        print("Track C states...", flush=True)
        # state on market days; for speed use every 30s already — optionally thin to AM/PM open hours
        snaps_s = snaps.copy()
        snaps_s = assign_states(snaps_s)
        # winner state distribution at independent move anchors
        winner_states = []
        if not moves.empty:
            for _, m in moves.iterrows():
                day = str(m["trading_date"])
                sym = str(m["symbol"])
                a0 = float(m["anchor_epoch"])
                g = snaps_s[(snaps_s["trading_date"].astype(str) == day) & (snaps_s["symbol"] == sym)]
                if g.empty:
                    continue
                j = (g["t0_epoch"] - a0).abs().idxmin()
                winner_states.append(
                    {
                        "trading_date": day,
                        "symbol": sym,
                        "realtime_state": g.loc[j, "realtime_state"],
                        "primary_label": "LARGE_RISE",
                        "capture_class": m.get("capture_class"),
                        "funnel_class": m.get("funnel_class"),
                    }
                )
        win_state_df = pd.DataFrame(winner_states)
        state_dist = (
            snaps_s["realtime_state"].value_counts(normalize=True).rename_axis("state").reset_index(name="rate")
        )
        state_dist["n"] = snaps_s["realtime_state"].value_counts().values
        win_dist = (
            win_state_df["realtime_state"].value_counts(normalize=True).rename_axis("state").reset_index(name="rate")
            if len(win_state_df)
            else pd.DataFrame()
        )
        if len(win_dist):
            win_dist["n"] = win_state_df["realtime_state"].value_counts().values

        print("Transitions...", flush=True)
        # sample transitions to keep excel sane: only state changes into reversal family or from it
        trans = state_transitions(snaps_s)
        if len(trans) > 200000:
            trans_x = trans.sample(200000, random_state=0)
        else:
            trans_x = trans

        print("Confirmation delay...", flush=True)
        delay_sum = confirmation_delay_analysis(snaps_s)

        print("Scan/queue...", flush=True)
        scan_df = scan_queue_audit(runtime_moves)

        print("Entries + LOD...", flush=True)
        ent_parts = []
        for day in runtime_days:
            e = w43d.load_official_for_day(day_meta(day))
            if not e.empty:
                ent_parts.append(e)
        entries = pd.concat(ent_parts, ignore_index=True) if ent_parts else pd.DataFrame()
        lod_df = lod_reversal(snaps_s[snaps_s["trading_date"].astype(str).isin(runtime_days)], entries)

        # comparisons A-E summaries
        comp_rows = []
        started = snaps_s[snaps_s["realtime_state"] == "REVERSAL_STARTED"]
        pers = snaps_s[snaps_s["realtime_state"] == "REVERSAL_PERSISTING"]
        brk = snaps_s[snaps_s["realtime_state"] == "BREAKOUT_CONFIRMED"]
        for name, base in (("A_started", started), ("B_persisting", pers), ("C_breakout", brk)):
            lr = base[base["primary_label"] == "LARGE_RISE"]
            dec = base[base["primary_label"] == "DECLINE"]
            nop = base  # proxy filter later
            comp_rows.append({"comparison": f"{name}_LARGE_RISE", **outcome_metrics(lr)})
            comp_rows.append({"comparison": f"{name}_DECLINE", **outcome_metrics(dec)})
        # D missed vs stop
        if not runtime_moves.empty and not entries.empty:
            missed = runtime_moves[runtime_moves["capture_class"] == "MISSED"].copy()
            if len(missed):
                missed_o = pd.DataFrame(
                    {
                        "primary_label": ["LARGE_RISE"] * len(missed),
                        "future_30m_mfe": pd.to_numeric(missed["max_future_mfe"], errors="coerce"),
                        "future_30m_return": pd.to_numeric(missed["max_future_return"], errors="coerce"),
                        "future_30m_mae": np.nan,
                        "future_15m_return": np.nan,
                    }
                )
                comp_rows.append({"comparison": "D_MISSED_winner", **outcome_metrics(missed_o)})
            stop_rows = []
            for _, e in entries[entries["exit_reason"] == "stop_hit"].iterrows():
                g = snaps_s[
                    (snaps_s["trading_date"].astype(str) == str(e["trading_date"]))
                    & (snaps_s["symbol"] == e["symbol"])
                ]
                if g.empty:
                    continue
                j = (g["t0_epoch"] - e["entry_epoch"]).abs().idxmin()
                stop_rows.append(g.loc[j])
            if stop_rows:
                comp_rows.append({"comparison": "D_STOP_entry", **outcome_metrics(pd.DataFrame(stop_rows))})
        # E pbv2 vs reversal transitions
        if not entries.empty:
            off_rows = []
            for _, e in entries.iterrows():
                g = snaps_s[
                    (snaps_s["trading_date"].astype(str) == str(e["trading_date"]))
                    & (snaps_s["symbol"] == e["symbol"])
                ]
                if g.empty:
                    continue
                j = (g["t0_epoch"] - e["entry_epoch"]).abs().idxmin()
                off_rows.append(g.loc[j])
            comp_rows.append({"comparison": "E_PBv2_official", **outcome_metrics(pd.DataFrame(off_rows))})
        if len(delay_sum):
            best_delay_row = delay_sum.sort_values(
                ["large_rise_precision", "mean_future_30m_return"], ascending=False
            ).iloc[0]
        else:
            best_delay_row = {}
        # prefer delay with n>=100 and best precision*return tradeoff
        if len(delay_sum):
            cand = delay_sum[delay_sum["valid_label_count"] >= 50].copy()
            if cand.empty:
                cand = delay_sum.copy()
            cand["_score"] = cand["large_rise_precision"].fillna(0) * 0.6 + (
                cand["mean_future_30m_return"].fillna(0) / 2.0
            ).clip(-1, 1) * 0.4 - cand["stop_proxy_rate"].fillna(0) * 0.3
            best_delay_row = cand.sort_values("_score", ascending=False).iloc[0].to_dict()

        # feature audit cliffs on started LR vs Decline
        feat_rows = []
        feats = [
            "ret_120s",
            "ret_30s",
            "accel_30s",
            "accel_60s",
            "spread_bps",
            "seconds_since_last_new_high",
            "day_high_distance_pct",
            "bounce_from_low_300s",
            "vol_recovery_flag",
            "imbalance_chg_60s",
        ]
        for day in market_days:
            a = started[(started["trading_date"].astype(str) == day) & (started["primary_label"] == "LARGE_RISE")]
            b = started[(started["trading_date"].astype(str) == day) & (started["primary_label"] == "DECLINE")]
            if len(a) < 10 or len(b) < 10:
                continue
            for f in feats:
                aa = pd.to_numeric(a[f], errors="coerce").dropna().to_numpy()
                bb = pd.to_numeric(b[f], errors="coerce").dropna().to_numpy()
                if len(aa) < 5 or len(bb) < 5:
                    continue
                # cheap cliffs
                if len(aa) > 800:
                    aa = np.random.default_rng(0).choice(aa, 800, replace=False)
                if len(bb) > 800:
                    bb = np.random.default_rng(0).choice(bb, 800, replace=False)
                gt = sum(np.sum(x > bb) for x in aa)
                lt = sum(np.sum(x < bb) for x in aa)
                delta = (gt - lt) / (len(aa) * len(bb))
                feat_rows.append(
                    {"trading_date": day, "feature": f, "cliffs_delta": delta, "n_a": len(aa), "n_b": len(bb)}
                )
        feat_df = pd.DataFrame(feat_rows)

        # --- answers & verdicts ---
        n_dq = int(len(dq_df))
        n_pb = int(len(pb_df))
        top_dq = dq_counts.most_common(1)[0][0] if dq_counts else None
        top_pb = pb_counts.most_common(1)[0][0] if pb_counts else None
        impl_n = int((dq_df["origin"] == "implementation").sum()) if len(dq_df) else 0
        mkt_n = int((dq_df["origin"] == "market").sum()) if len(dq_df) else 0
        open_n = int(dq_counts.get("OPEN_WARMUP", 0))
        ref_n = int(dq_counts.get("REFRESH_WARMUP", 0))
        recovered = dq_df[dq_df["validity_recovered"] == True] if len(dq_df) else dq_df
        edge_after = None
        if len(recovered):
            edge_after = float(
                (
                    pd.to_numeric(recovered["future_mfe_from_first_valid"], errors="coerce") >= 1.0
                ).mean()
            )

        def win_rate(state: str) -> Optional[float]:
            if win_dist.empty:
                return None
            r = win_dist[win_dist["state"] == state]
            return float(r["rate"].iloc[0]) if len(r) else 0.0

        # LOD stability
        lod_stable = False
        if len(lod_df):
            g = lod_df[lod_df["method"] == "reversal_started"]
            if len(g):
                lod_stable = bool((g["delta_vs_random"] > 0).mean() >= 0.6)

        beats_pbv2 = False
        beats_random = False
        if best_delay_row:
            # compare delay metrics vs official
            off_comp = [c for c in comp_rows if c["comparison"] == "E_PBv2_official"]
            if off_comp and best_delay_row.get("large_rise_precision") is not None:
                beats_pbv2 = bool(
                    best_delay_row["large_rise_precision"] > (off_comp[0]["large_rise_precision"] or 0)
                    and (best_delay_row.get("mean_future_30m_return") or -9)
                    >= (off_comp[0].get("mean_future_30m_return") or -9)
                )
        if len(lod_df):
            beats_random = bool((lod_df["delta_vs_random"] > 0).mean() >= 0.5)

        dq_fix_required = impl_n >= max(10, 0.25 * n_dq) if n_dq else False
        shadow_ready = bool(
            best_delay_row
            and best_delay_row.get("valid_label_count", 0) >= 100
            and (best_delay_row.get("large_rise_precision") or 0) >= 0.25
            and lod_stable
            and beats_random
            and (best_delay_row.get("mean_future_30m_return") or -9) > 0
        )

        verdicts = []
        if impl_n > mkt_n and impl_n > 0:
            verdicts.append("FOUND_DATA_QUALITY_PIPELINE_BUG")
        if dq_counts.get("GENUINE_MARKET_STALE", 0) >= 5:
            verdicts.append("FOUND_GENUINE_MARKET_DATA_GAP")
        if open_n >= 5:
            verdicts.append("FOUND_OPEN_WARMUP_GAP")
        if ref_n >= 5:
            verdicts.append("FOUND_REFRESH_WARMUP_GAP")
        if pb_counts.get("momentum_insufficient", 0) >= pb_counts.get("board_insufficient", 0):
            if pb_counts.get("momentum_insufficient", 0) > 0:
                verdicts.append("FOUND_PBV2_MOMENTUM_LIMIT")
        if pb_counts.get("board_insufficient", 0) > 0:
            verdicts.append("FOUND_PBV2_BOARD_LIMIT")
        if pb_counts.get("momentum_and_board_not_simultaneous", 0) > 0:
            verdicts.append("FOUND_PBV2_SYNCHRONIZATION_LIMIT")
        if shadow_ready or (
            best_delay_row
            and (best_delay_row.get("large_rise_precision") or 0) >= 0.22
            and (best_delay_row.get("mean_future_30m_return") or -9) > -0.05
        ):
            if best_delay_row and (best_delay_row.get("large_rise_precision") or 0) >= 0.22:
                verdicts.append("FOUND_REVERSAL_ENTRY_WINDOW")
            else:
                verdicts.append("FOUND_NO_REVERSAL_ENTRY_WINDOW")
        else:
            verdicts.append("FOUND_NO_REVERSAL_ENTRY_WINDOW")
        if best_delay_row and (best_delay_row.get("large_rise_precision") or 0) >= 0.22:
            verdicts.append("FOUND_CONFIRMATION_DELAY_EDGE")
        if not beats_pbv2:
            verdicts.append("FOUND_NO_EDGE_VS_PBV2")
        if dq_fix_required:
            verdicts.append("DATA_QUALITY_FIX_REQUIRED")
        verdicts.append("SHADOW_CANDIDATE_READY" if shadow_ready else "SHADOW_CANDIDATE_NOT_READY")

        answers = {
            "1_dq_top_cause": top_dq,
            "2_dq_counts": dict(dq_counts),
            "3_dq_implementation_n": impl_n,
            "4_dq_market_n": mkt_n,
            "5_open_warmup_n": open_n,
            "6_refresh_warmup_n": ref_n,
            "7_edge_after_dq_recovery_rate": edge_after,
            "8_dq_runtime_fix_needed": dq_fix_required,
            "9_pbv2_top_cause": top_pb,
            "10_momentum_insufficient_n": int(pb_counts.get("momentum_insufficient", 0)),
            "11_board_insufficient_n": int(pb_counts.get("board_insufficient", 0)),
            "12_mom_board_sync_fail_n": int(pb_counts.get("momentum_and_board_not_simultaneous", 0))
            + int(pb_counts.get("momentum_and_board_both_weak", 0)),
            "13_candidate_eval_not_run_n": int(pb_counts.get("candidate_evaluation_not_run", 0)),
            "14_winner_state_distribution": win_dist.to_dict(orient="records") if len(win_dist) else [],
            "15_pullback_falling_rate": win_rate("PULLBACK_FALLING"),
            "16_decelerating_decline_rate": win_rate("DECELERATING_DECLINE"),
            "17_reversal_started_rate": win_rate("REVERSAL_STARTED"),
            "18_reversal_persisting_rate": win_rate("REVERSAL_PERSISTING"),
            "19_breakout_confirmed_rate": win_rate("BREAKOUT_CONFIRMED"),
            "20_reversal_failed_rate": win_rate("REVERSAL_FAILED"),
            "21_best_confirmation_delay": best_delay_row.get("delay_label"),
            "22_best_delay_precision": best_delay_row.get("large_rise_precision"),
            "23_best_delay_ret_mfe_mae": {
                "return": best_delay_row.get("mean_future_30m_return"),
                "mfe": best_delay_row.get("mean_future_30m_mfe"),
                "mae": best_delay_row.get("mean_future_30m_mae"),
            },
            "24_best_delay_stop_proxy": best_delay_row.get("stop_proxy_rate"),
            "25_improves_vs_pbv2": beats_pbv2,
            "26_improves_vs_random": beats_random,
            "27_lod_direction_stable": lod_stable,
            "28_shadow_candidate_ready": shadow_ready,
            "29_dq_fix_first": bool(dq_fix_required and impl_n >= (n_pb * 0.3 if n_pb else 0)),
            "30_runtime_yaml_shadow_unchanged": True,
        }

        report = {
            "metadata": {
                "phase": "Phase687W43E",
                "generated_at": datetime.now(JST).isoformat(),
                "market_days": market_days,
                "runtime_active_days": runtime_days,
                "runtime_active_count": len(runtime_days),
                "runtime_active_lt_6": len(runtime_days) < 6,
            },
            "verdicts": verdicts,
            "day_classification": day_df[day_df["in_analysis_window"] | day_df["EXCLUDED_DAY"]].head(20).to_dict(
                orient="records"
            ),
            "data_quality": {
                "n": n_dq,
                "counts": dict(dq_counts),
                "implementation_n": impl_n,
                "market_n": mkt_n,
                "recovery_edge_rate": edge_after,
            },
            "pbv2_not_candidate": {"n": n_pb, "counts": dict(pb_counts)},
            "state_distribution": {
                "all_snapshots": state_dist.to_dict(orient="records"),
                "winner_anchors": win_dist.to_dict(orient="records") if len(win_dist) else [],
            },
            "confirmation_delay": delay_sum.to_dict(orient="records") if len(delay_sum) else [],
            "comparisons": comp_rows,
            "leave_one_day_out": lod_df.to_dict(orient="records") if len(lod_df) else [],
            "scan_queue": {
                "n": int(len(scan_df)),
                "later_entry_rate": float(scan_df["later_official_entry"].mean()) if len(scan_df) else None,
            },
            "required_answers": answers,
            "runtime_change_audit": {
                "runtime_changed": False,
                "yaml_changed": False,
                "shadow_added": False,
                "orders_changed": False,
                "past_artifacts_overwritten": False,
            },
            "data_integrity": {
                "snaps": int(len(snaps)),
                "moves": int(len(moves)),
                "dq_audited": n_dq,
                "pbv2_audited": n_pb,
                "transitions": int(len(trans)),
            },
        }

        md = f"""# Phase687W43E — Pullback-to-Reversal & Data Quality Root Cause

## Verdict
`{' | '.join(verdicts)}`

## Days
- Market ({len(market_days)}): `{market_days}`
- Runtime active ({len(runtime_days)}): `{runtime_days}`
- Runtime active < 6: **{len(runtime_days) < 6}**

## Track A — Data Quality
- Audited DQ moves: **{n_dq}**
- Top cause: **{top_dq}**
- Implementation: **{impl_n}** / Market: **{mkt_n}**
- OPEN_WARMUP: **{open_n}** / REFRESH_WARMUP: **{ref_n}**
- Edge after recovery (MFE≥1%): **{edge_after}**
- Runtime DQ fix needed: **{dq_fix_required}**

Counts: `{dict(dq_counts)}`

## Track B — PBv2 not candidate
- Audited: **{n_pb}**
- Top cause: **{top_pb}**
- momentum: **{answers['10_momentum_insufficient_n']}** / board: **{answers['11_board_insufficient_n']}** / sync: **{answers['12_mom_board_sync_fail_n']}** / eval-not-run: **{answers['13_candidate_eval_not_run_n']}**

Counts: `{dict(pb_counts)}`

## Track C — Reversal states (winner anchors)
{win_dist.to_string(index=False) if len(win_dist) else 'n/a'}

## Confirmation delay
Best: **{answers['21_best_confirmation_delay']}**  
precision={answers['22_best_delay_precision']} ret/mfe/mae={answers['23_best_delay_ret_mfe_mae']} stop_proxy={answers['24_best_delay_stop_proxy']}

## vs baselines
- vs PBv2: **{beats_pbv2}**
- vs random: **{beats_random}**
- LOD stable: **{lod_stable}**
- Shadow ready: **{shadow_ready}**
- DQ fix first: **{answers['29_dq_fix_first']}**

## Required answers
1. {answers['1_dq_top_cause']}
2. {answers['2_dq_counts']}
3. {answers['3_dq_implementation_n']}
4. {answers['4_dq_market_n']}
5. {answers['5_open_warmup_n']}
6. {answers['6_refresh_warmup_n']}
7. {answers['7_edge_after_dq_recovery_rate']}
8. {answers['8_dq_runtime_fix_needed']}
9. {answers['9_pbv2_top_cause']}
10. {answers['10_momentum_insufficient_n']}
11. {answers['11_board_insufficient_n']}
12. {answers['12_mom_board_sync_fail_n']}
13. {answers['13_candidate_eval_not_run_n']}
14. see winner state distribution
15-20. falling={answers['15_pullback_falling_rate']} decel={answers['16_decelerating_decline_rate']} started={answers['17_reversal_started_rate']} persist={answers['18_reversal_persisting_rate']} breakout={answers['19_breakout_confirmed_rate']} failed={answers['20_reversal_failed_rate']}
21-24. delay={answers['21_best_confirmation_delay']} prec={answers['22_best_delay_precision']} metrics={answers['23_best_delay_ret_mfe_mae']} stop={answers['24_best_delay_stop_proxy']}
25. {answers['25_improves_vs_pbv2']}
26. {answers['26_improves_vs_random']}
27. {answers['27_lod_direction_stable']}
28. {answers['28_shadow_candidate_ready']}
29. {answers['29_dq_fix_first']}
30. Runtime/YAML/Shadow unchanged=**True**
"""
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "w43e_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        (OUT / "w43e_report.md").write_text(md, encoding="utf-8")
        write_xlsx(
            {
                "day_classification": day_df[
                    day_df["in_analysis_window"] | (day_df["date_iso"] >= "2026-06-20")
                ].head(30),
                "dq_summary": pd.DataFrame([{"dq_class": k, "n": v} for k, v in dq_counts.items()]),
                "dq_daily": dq_daily if len(dq_daily) else pd.DataFrame(),
                "dq_recovery": dq_rec.head(5000) if len(dq_rec) else pd.DataFrame(),
                "pbv2_not_candidate": pb_df.head(5000) if len(pb_df) else pd.DataFrame(),
                "state_distribution": pd.concat(
                    [
                        state_dist.assign(scope="all_snapshots"),
                        win_dist.assign(scope="winner_anchors") if len(win_dist) else pd.DataFrame(),
                    ],
                    ignore_index=True,
                ),
                "state_transitions": trans_x.head(80000) if len(trans_x) else pd.DataFrame(),
                "confirmation_delay": delay_sum if len(delay_sum) else pd.DataFrame(),
                "comparison_summary": pd.DataFrame(comp_rows),
                "leave_one_day_out": lod_df if len(lod_df) else pd.DataFrame(),
                "scan_queue": scan_df if len(scan_df) else pd.DataFrame(),
                "feature_audit": feat_df if len(feat_df) else pd.DataFrame(),
                "data_integrity": pd.DataFrame(
                    [{"key": k, "value": str(v)} for k, v in report["data_integrity"].items()]
                ),
            },
            OUT / "w43e_audit.xlsx",
        )
        print(
            json.dumps(
                {
                    "verdicts": verdicts,
                    "dq_top": top_dq,
                    "pbv2_top": top_pb,
                    "best_delay": answers["21_best_confirmation_delay"],
                    "shadow_ready": shadow_ready,
                    "runtime_days": len(runtime_days),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        # keep snap cache for resume
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
