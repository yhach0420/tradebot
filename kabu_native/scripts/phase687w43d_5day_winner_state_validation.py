#!/usr/bin/env python3
"""Phase687W43D: 5-Day Winner-State Validation + Causal Funnel Repair.

Research-only. No Runtime/YAML/Shadow/order changes.
"""

from __future__ import annotations

import json
import math
import sys
import warnings
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore", category=UserWarning)

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(NATIVE / "src"))

from small_paper.canonical_summary import collect_canonical_trades  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
REPORTS = NATIVE / "results" / "reports"
PAPER = NATIVE / "results" / "small_paper"
PUSH_ROOT = NATIVE / "data" / "push_jsonl"
PRIORITY_DAYS = ["2026-07-10", "2026-07-14", "2026-07-15", "2026-07-16", "2026-07-17"]
OUTLIER = "7581.T"
MAX_WORKERS = 4
W43C_OLD_RULE_REJECTED = 204

# Load W43C helpers under a stable importable name (required for ProcessPool pickling)
sys.path.insert(0, str(NATIVE / "scripts"))
import phase687w43c_watch50_future30m_opportunity as w43c  # noqa: E402

FEATURE_COLS = list(w43c.FEATURE_COLS) + [
    "accel_30s",
    "new_high_restart_count",
    "vol_recovery_flag",
    "vwap_reclaim_flag",
    "seconds_since_vwap_reclaim",
]

COMBOS_2 = [
    ("accel_60s", "pre_300s_new_high_count"),
    ("accel_60s", "ret_30s"),
    ("spread_bps", "seconds_since_last_new_high"),
    ("max_dd_300s", "accel_60s"),
    ("vwap_dev_pct", "accel_60s"),
]
COMBOS_3 = [
    ("accel_60s", "ret_30s", "seconds_since_last_new_high"),
    ("accel_60s", "spread_bps", "ret_30s"),
    ("max_dd_300s", "accel_60s", "vol_recovery_flag"),
    ("ret_30s", "accel_60s", "new_high_restart_count"),
    ("vwap_dev_pct", "accel_60s", "imbalance_chg_60s"),
]

PBV2_BASE_KEYS = (
    "or_overlay_not_candidate",
    "momentum_low_required",
    "momentum_low",
    "board_requirement",
    "entry_score_v2_gate",  # often paired with not reaching candidate — still base if no candidate_seen
)
# concrete rules require candidate evidence
RULE_KEYS = (
    "flat_band_mainline",
    "high_drift_pullback",
    "late_chase",
    "entry_score_v2_below",
    "entry_quality_guard",
    "near_day_high",
    "pullback_misread",
    "weak_shape",
    "reentry_rsi",
    "daytrade_suitability",
    "entry_cluster",
    "classic_late_chase",
)
DATA_KEYS = (
    "data_stale",
    "stale_board",
    "stale_price",
    "liquidity_stale",
    "price_stale",
    "board_stale",
    "feature_incomplete",
)
SAME_KEYS = (
    "reject_same_symbol",
    "same_symbol",
    "cooloff",
    "reentry_block",
    "open_overlap",
    "same_push_reentry",
)
SCAN_KEYS = ("max_entries_per_scan", "queue_full", "scan_cap", "score5_rank")
CAP_KEYS = ("max_concurrent", "position_cap", "or_cap_full", "cap_full")


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wc(path: Path, rows: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(rows, pd.DataFrame):
        rows.to_csv(path, index=False)
        return
    pd.DataFrame(rows).to_csv(path, index=False)


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def day_compact(d: str) -> str:
    return d.replace("-", "")


def pick_sessions(day: str) -> dict[str, Optional[Path]]:
    root = PAPER / day
    am = pm = None
    am_sz = pm_sz = -1
    if not root.is_dir():
        return {"am": None, "pm": None}
    for s in root.glob("live_session_*"):
        ev = s / "small_paper_events.jsonl"
        sz = ev.stat().st_size if ev.is_file() else 0
        if (s / "small_paper_summary_am.json").is_file() and sz > am_sz:
            am, am_sz = s, sz
        if (s / "small_paper_summary_pm.json").is_file() and sz > pm_sz:
            pm, pm_sz = s, sz
    # fallback: largest events file as am if marked somehow
    if am is None and pm is None:
        best = None
        best_sz = 0
        for s in root.glob("live_session_*"):
            ev = s / "small_paper_events.jsonl"
            sz = ev.stat().st_size if ev.is_file() else 0
            if sz > best_sz:
                best, best_sz = s, sz
        if best and best_sz > 1_000_000:
            am = best
    return {"am": am if am and am_sz > 1_000_000 else None, "pm": pm if pm and pm_sz > 1_000_000 else None}


def detect_days() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (usable_days meta, integrity rows for all inspected)."""
    inspected: list[dict[str, Any]] = []
    usable: list[dict[str, Any]] = []
    push_days = sorted(
        [p.name for p in PUSH_ROOT.iterdir() if p.is_dir() and p.name.startswith("2026-")],
        reverse=True,
    )
    ordered: list[str] = []
    for d in PRIORITY_DAYS:
        ordered.append(d)
    for d in push_days:
        if d not in ordered:
            ordered.append(d)

    for d in ordered:
        day = day_compact(d)
        push = PUSH_ROOT / d
        n_push = len(list(push.glob("*.jsonl"))) if push.is_dir() else 0
        uni = {
            "am": REPORTS / f"universe_core10_dynamic40_price_risk_am_{day}.csv",
            "am_refresh": REPORTS / f"universe_core10_dynamic40_price_risk_am_refresh1000_{day}.csv",
            "pm": REPORTS / f"universe_core10_dynamic40_price_risk_pm_{day}.csv",
            "pm_refresh": REPORTS / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day}.csv",
        }
        uni_ok = all(p.is_file() for p in uni.values())
        sess = pick_sessions(day)
        reasons: list[str] = []
        if n_push < 45:
            reasons.append(f"push_coverage_low:{n_push}")
        if not uni_ok:
            reasons.append("universe_csv_incomplete")
        if sess["am"] is None and sess["pm"] is None:
            reasons.append("no_usable_paper_session_events")
        ok = n_push >= 45 and uni_ok
        row = {
            "date": d,
            "day": day,
            "push_files": n_push,
            "universe_ok": uni_ok,
            "am_session": str(sess["am"]) if sess["am"] else "",
            "pm_session": str(sess["pm"]) if sess["pm"] else "",
            "usable": ok,
            "exclude_reasons": reasons,
            "in_priority_list": d in PRIORITY_DAYS,
        }
        inspected.append(row)
        if ok and len(usable) < 5:
            usable.append({**row, "sessions": sess, "universe": uni, "push_dir": push})
        # After priority list fully inspected and we have 5 usable, stop backfill
        priority_done = all(any(i["date"] == p for i in inspected) for p in PRIORITY_DAYS)
        if priority_done and len(usable) >= 5 and d not in PRIORITY_DAYS:
            break
    return usable, inspected


def build_segments(day_meta: dict[str, Any]) -> list[dict[str, Any]]:
    d = day_meta["date"]
    uni = day_meta["universe"]
    segs_def = [
        ("am", "am_open", "before", uni["am"], f"{d}T09:03:00+09:00", f"{d}T10:00:00+09:00", f"{d}T11:00:00+09:00"),
        ("am", "am_refresh1000", "after", uni["am_refresh"], f"{d}T10:00:00+09:00", f"{d}T11:00:00+09:00", f"{d}T11:00:00+09:00"),
        ("pm", "pm_open", "before", uni["pm"], f"{d}T12:33:00+09:00", f"{d}T14:30:00+09:00", f"{d}T15:00:00+09:00"),
        ("pm", "pm_refresh1430", "after", uni["pm_refresh"], f"{d}T14:30:00+09:00", f"{d}T15:00:00+09:00", f"{d}T15:00:00+09:00"),
    ]
    out = []
    for session, segment, refresh, csvp, start, end, label_end in segs_def:
        syms = set(w43c.load_universe(csvp))
        st = w43c.parse_ts(start)
        en = w43c.parse_ts(end)
        le = w43c.parse_ts(label_end)
        out.append(
            {
                "session": session,
                "segment": segment,
                "refresh": refresh,
                "symbols": syms,
                "start_epoch": w43c.to_epoch(st),
                "end_epoch": w43c.to_epoch(en),
                "label_end_epoch": w43c.to_epoch(le),
            }
        )
    return out


def load_official_for_day(day_meta: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for sk in ("am", "pm"):
        sd = day_meta["sessions"].get(sk)
        if sd is None:
            continue
        events = []
        p = Path(sd) / "small_paper_events.jsonl"
        if not p.is_file():
            continue
        for line in p.open(encoding="utf-8"):
            if line.strip():
                events.append(json.loads(line))
        can = collect_canonical_trades(events)
        for t in can:
            et = w43c.parse_ts(t.get("entry_time"))
            if et is None:
                continue
            rows.append(
                {
                    "trading_date": day_meta["day"],
                    "session": sk,
                    "symbol": t.get("symbol"),
                    "entry_time": et.isoformat(),
                    "entry_epoch": w43c.to_epoch(et),
                    "entry_price": w43c.finite(t.get("entry_price")),
                    "exit_reason": t.get("exit_reason"),
                    "pnl_yen_100": w43c.finite(t.get("pnl_yen_100")),
                    "pnl_pct": w43c.finite(t.get("pnl_pct")),
                }
            )
    return pd.DataFrame(rows)


def enrich_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    s30 = pd.to_numeric(df.get("slope_30s"), errors="coerce")
    s60 = pd.to_numeric(df.get("slope_60s"), errors="coerce")
    s120 = pd.to_numeric(df.get("slope_120s"), errors="coerce")
    df["accel_30s"] = s30 - s60
    # restart proxy: new highs with recent seconds_since small after drawdown
    nh = pd.to_numeric(df.get("pre_300s_new_high_count"), errors="coerce")
    sec = pd.to_numeric(df.get("seconds_since_last_new_high"), errors="coerce")
    dd = pd.to_numeric(df.get("max_dd_300s"), errors="coerce")
    df["new_high_restart_count"] = np.where((nh >= 1) & (sec <= 60) & (dd <= -0.3), 1.0, 0.0)
    volr = pd.to_numeric(df.get("vol_ratio_60_300"), errors="coerce")
    ret30 = pd.to_numeric(df.get("ret_30s"), errors="coerce")
    df["vol_recovery_flag"] = np.where((ret30 < 0) & (volr > 0.3), 1.0, 0.0)
    vdev = pd.to_numeric(df.get("vwap_dev_pct"), errors="coerce")
    vslope = pd.to_numeric(df.get("vwap_slope_300s"), errors="coerce")
    df["vwap_reclaim_flag"] = np.where((vdev >= -0.05) & (vdev <= 0.3) & (vslope >= 0), 1.0, 0.0)
    df["seconds_since_vwap_reclaim"] = np.where(df["vwap_reclaim_flag"] == 1.0, sec, np.nan)
    return df


def build_raw_episodes(df: pd.DataFrame) -> pd.DataFrame:
    """Build LARGE_RISE episodes with anchor = first LARGE_RISE snapshot (no future peek)."""
    lr = df[df["primary_label"] == "LARGE_RISE"].sort_values(["trading_date", "symbol", "t0_epoch"])
    eps = []
    if lr.empty:
        return pd.DataFrame()
    for (day, sym), g in lr.groupby(["trading_date", "symbol"]):
        g = g.sort_values("t0_epoch")
        cur = []
        prev = None
        for _, row in g.iterrows():
            t = float(row["t0_epoch"])
            if prev is None or t - prev <= 90:
                cur.append(row)
            else:
                if cur:
                    eps.append(_episode_from_rows(cur))
                cur = [row]
            prev = t
        if cur:
            eps.append(_episode_from_rows(cur))
    return pd.DataFrame(eps)


def _episode_from_rows(rows: list[pd.Series]) -> dict[str, Any]:
    r0 = rows[0]
    start = float(r0["t0_epoch"])
    end = float(rows[-1]["t0_epoch"])
    mfes = [float(r["future_30m_mfe"]) for r in rows if pd.notna(r.get("future_30m_mfe"))]
    rets = [float(r["future_30m_return"]) for r in rows if pd.notna(r.get("future_30m_return"))]
    # 15m labels for refresh1430
    mfe15 = [float(r["future_15m_return"]) for r in rows if "future_15m_return" in r and pd.notna(r.get("future_15m_return"))]
    feat = {c: r0.get(c) for c in FEATURE_COLS if c in r0.index}
    return {
        "trading_date": r0["trading_date"],
        "symbol": r0["symbol"],
        "session": r0["session"],
        "universe_segment": r0["universe_segment"],
        "refresh_flag": r0["refresh_flag"],
        "anchor_time": r0["t0_time"],
        "anchor_epoch": start,
        "episode_end_epoch": end,
        "episode_end_time": rows[-1]["t0_time"],
        "snapshot_count": len(rows),
        "max_future_mfe": max(mfes) if mfes else None,
        "max_future_return": max(rets) if rets else None,
        "future_15m_return_at_anchor": r0.get("future_15m_return"),
        "label_horizon": "15m" if r0["universe_segment"] == "pm_refresh1430" else "30m",
        **feat,
    }


def attach_capture(ep: pd.DataFrame, entries: pd.DataFrame) -> pd.DataFrame:
    ep = ep.copy()
    ep["capture_class"] = "MISSED"
    ep["earliest_entry_epoch"] = np.nan
    ep["secs_to_entry"] = np.nan
    if ep.empty or entries.empty:
        return ep
    for i, r in ep.iterrows():
        eg = entries[
            (entries["trading_date"] == r["trading_date"])
            & (entries["symbol"] == r["symbol"])
            & (entries["entry_epoch"] >= r["anchor_epoch"])
            & (entries["entry_epoch"] <= r["anchor_epoch"] + 900)
        ]
        if eg.empty:
            continue
        ee = float(eg["entry_epoch"].min())
        dt = ee - float(r["anchor_epoch"])
        ep.at[i, "earliest_entry_epoch"] = ee
        ep.at[i, "secs_to_entry"] = dt
        if dt <= 300:
            ep.at[i, "capture_class"] = "CAPTURED_5M"
        elif dt <= 900:
            ep.at[i, "capture_class"] = "LATE_CAPTURED_15M"
    return ep


def _match_any(text: str, keys: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in keys)


def classify_causal_funnel(ep: pd.DataFrame, day_metas: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Causal classification using only [anchor-30s, anchor+5m]."""
    ep = ep.copy()
    ep["funnel_class"] = "NO_DECISION_TRACE"
    ep["funnel_detail"] = ""
    ep["candidate_seen"] = False
    ep["open_slots_at_decision"] = np.nan
    audit_rows = []

    # index missed / all needing class
    need = ep[ep["capture_class"].isin(["MISSED", "CAPTURED_5M", "LATE_CAPTURED_15M"])]
    # prefill capture classes
    for i, r in ep.iterrows():
        if r["capture_class"] == "CAPTURED_5M":
            ep.at[i, "funnel_class"] = "CAPTURED_5M"
        elif r["capture_class"] == "LATE_CAPTURED_15M":
            ep.at[i, "funnel_class"] = "LATE_CAPTURED_15M"

    windows = []
    for i, r in ep.iterrows():
        if r["capture_class"] != "MISSED":
            continue
        windows.append(
            (
                int(i),
                str(r["trading_date"]),
                str(r["symbol"]),
                float(r["anchor_epoch"]) - 30.0,
                float(r["anchor_epoch"]) + 300.0,
            )
        )

    # gather decision traces per day
    by_day_sessions: dict[str, list[Path]] = defaultdict(list)
    for m in day_metas:
        for sk in ("am", "pm"):
            sd = m["sessions"].get(sk)
            if sd:
                by_day_sessions[m["day"]].append(Path(sd))

    hits: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for day, sdirs in by_day_sessions.items():
        day_windows = [w for w in windows if w[1] == day]
        if not day_windows:
            continue
        for sd in sdirs:
            p = sd / "small_paper_events.jsonl"
            if not p.is_file():
                continue
            for line in p.open(encoding="utf-8"):
                if '"event_type"' not in line:
                    continue
                if not any(x in line for x in ("rejected", "candidate", "accepted")):
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = o.get("event_type")
                if etype not in ("rejected", "candidate", "accepted"):
                    continue
                sym = str(o.get("symbol") or "")
                et = w43c.parse_ts(o.get("entry_time") or o.get("event_time"))
                if et is None:
                    continue
                epoch = w43c.to_epoch(et)
                reason = str(
                    o.get("final_reject_reason")
                    or o.get("gate_reject_reason")
                    or o.get("or_overlay_reason")
                    or o.get("pbv2_internal_reason")
                    or ""
                )
                slot = o.get("position_slot_before")
                if slot is None:
                    slot = o.get("open_slots")
                cap = o.get("max_concurrent_positions")
                for idx, dday, wsym, lo, hi in day_windows:
                    if wsym == sym and lo <= epoch <= hi:
                        hits[idx].append(
                            {
                                "event_type": etype,
                                "reason": reason,
                                "epoch": epoch,
                                "slot": slot,
                                "cap": cap,
                                "pbv2": o.get("pbv2_internal_reason"),
                            }
                        )

    for idx, events in hits.items():
        events = sorted(events, key=lambda x: x["epoch"])
        reasons = [e["reason"] for e in events]
        types = [e["event_type"] for e in events]
        joined = "|".join(f"{e['event_type']}:{e['reason']}" for e in events[:40])
        candidate_seen = any(t == "candidate" for t in types) or any(
            t == "accepted" for t in types
        ) or any(
            r and (not _match_any(r, ("or_overlay_not_candidate",))) and t == "rejected"
            and _match_any(r, RULE_KEYS)
            for t, r in zip(types, reasons)
        )
        # stage progression
        stage = "NO_DECISION_TRACE"
        detail = joined
        # execution aborted
        if any("accept_aborted" in (r or "") or "execution_payload" in (r or "") for r in reasons):
            stage = "EXECUTION_ABORTED"
        else:
            # CAP confirmed
            cap_hit = False
            for e in events:
                r = e["reason"] or ""
                if _match_any(r, CAP_KEYS):
                    slot = e.get("slot")
                    cap = e.get("cap")
                    try:
                        if slot is not None and cap is not None and float(slot) >= float(cap):
                            cap_hit = True
                        elif slot is not None and float(slot) >= 5:
                            cap_hit = True
                    except (TypeError, ValueError):
                        pass
            if cap_hit:
                stage = "CAP_BLOCKED_CONFIRMED"
            elif any(_match_any(r or "", SCAN_KEYS) for r in reasons):
                stage = "SCAN_OR_QUEUE_LIMITED"
            elif any(_match_any(r or "", SAME_KEYS) for r in reasons):
                stage = "SAME_SYMBOL_POSITION_BLOCKED"
            elif candidate_seen and any(_match_any(r or "", RULE_KEYS) for r in reasons):
                stage = "ENTRY_RULE_REJECTED"
            elif any(_match_any(r or "", DATA_KEYS) for r in reasons):
                stage = "DATA_QUALITY_BLOCKED"
            elif any(_match_any(r or "", ("or_overlay_not_candidate",)) for r in reasons) or (
                any(_match_any(r or "", PBV2_BASE_KEYS) for r in reasons) and not candidate_seen
            ):
                stage = "PBV2_BASE_NOT_CANDIDATE"
            elif events:
                # has traces but unclassified → if only overlay not candidate
                if all(_match_any(r or "", ("or_overlay_not_candidate",)) or not r for r in reasons):
                    stage = "PBV2_BASE_NOT_CANDIDATE"
                elif candidate_seen:
                    stage = "ENTRY_RULE_REJECTED"
                else:
                    stage = "PBV2_BASE_NOT_CANDIDATE"
            else:
                stage = "NO_DECISION_TRACE"

        ep.at[idx, "funnel_class"] = stage
        ep.at[idx, "funnel_detail"] = detail
        ep.at[idx, "candidate_seen"] = bool(candidate_seen)
        audit_rows.append(
            {
                "episode_index": idx,
                "trading_date": ep.at[idx, "trading_date"],
                "symbol": ep.at[idx, "symbol"],
                "anchor_time": ep.at[idx, "anchor_time"],
                "n_traces": len(events),
                "candidate_seen": candidate_seen,
                "funnel_class": stage,
                "reasons_sample": detail[:500],
                "or_overlay_not_candidate_present": any(
                    _match_any(r or "", ("or_overlay_not_candidate",)) for r in reasons
                ),
            }
        )

    # remaining missed without hits
    for i, r in ep.iterrows():
        if r["capture_class"] == "MISSED" and r["funnel_class"] in ("NO_DECISION_TRACE",) and i not in hits:
            ep.at[i, "funnel_class"] = "NO_DECISION_TRACE"

    return ep, pd.DataFrame(audit_rows)


def build_independent_moves(ep: pd.DataFrame) -> pd.DataFrame:
    if ep.empty:
        return ep.copy()
    rows = []
    for (day, sym), g in ep.sort_values("anchor_epoch").groupby(["trading_date", "symbol"]):
        g = g.sort_values("anchor_epoch")
        cur = []
        cur_end = None
        for _, r in g.iterrows():
            a = float(r["anchor_epoch"])
            # overlap if anchor within previous move's 30m window
            if cur and a <= cur_end:
                cur.append(r)
                cur_end = max(cur_end, a + 1800.0)
            else:
                if cur:
                    rows.append(_collapse_move(cur))
                cur = [r]
                cur_end = a + 1800.0
        if cur:
            rows.append(_collapse_move(cur))
    return pd.DataFrame(rows)


def _collapse_move(rows: list[pd.Series]) -> dict[str, Any]:
    # earliest anchor; funnel = furthest progressed among members
    order = [
        "CAPTURED_5M",
        "LATE_CAPTURED_15M",
        "EXECUTION_ABORTED",
        "CAP_BLOCKED_CONFIRMED",
        "SCAN_OR_QUEUE_LIMITED",
        "SAME_SYMBOL_POSITION_BLOCKED",
        "ENTRY_RULE_REJECTED",
        "DATA_QUALITY_BLOCKED",
        "PBV2_BASE_NOT_CANDIDATE",
        "NO_DECISION_TRACE",
    ]
    rank = {k: i for i, k in enumerate(order)}
    best = min(rows, key=lambda r: rank.get(str(r.get("funnel_class")), 99))
    r0 = rows[0]
    return {
        **{k: r0[k] for k in r0.index if k in (
            "trading_date", "symbol", "session", "universe_segment", "refresh_flag",
            "label_horizon",
        ) or k in FEATURE_COLS},
        "independent_move_id": f"{r0['trading_date']}_{r0['symbol']}_{int(r0['anchor_epoch'])}",
        "anchor_time": r0["anchor_time"],
        "anchor_epoch": float(r0["anchor_epoch"]),
        "raw_episode_count": len(rows),
        "max_future_mfe": max((r.get("max_future_mfe") or -1e9) for r in rows),
        "max_future_return": max((r.get("max_future_return") or -1e9) for r in rows),
        "capture_class": best["capture_class"],
        "funnel_class": best["funnel_class"],
        "candidate_seen": bool(best.get("candidate_seen")),
        "funnel_detail": best.get("funnel_detail"),
        "secs_to_entry": best.get("secs_to_entry"),
    }


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if len(a) < 3 or len(b) < 3:
        return None
    rng = np.random.default_rng(42)
    aa = rng.choice(a, size=min(400, len(a)), replace=False)
    bb = rng.choice(b, size=min(400, len(b)), replace=False)
    gt = sum(np.sum(x > bb) for x in aa)
    lt = sum(np.sum(x < bb) for x in aa)
    n = len(aa) * len(bb)
    return float((gt - lt) / n) if n else None


def auc_safe(y: np.ndarray, s: np.ndarray) -> Optional[float]:
    m = np.isfinite(y) & np.isfinite(s)
    if m.sum() < 8 or len(np.unique(y[m])) < 2:
        return None
    try:
        return float(roc_auc_score(y[m], s[m]))
    except Exception:
        return None


def run_day_snapshots(day_meta: dict[str, Any]) -> pd.DataFrame:
    day = day_meta["day"]
    # reuse W43C parquet for 20260717
    if day == "20260717":
        pq = OUT / "w43c_20260717_watch50_snapshot.parquet"
        if pq.is_file():
            print(f"  reusing {pq.name}", flush=True)
            return enrich_features(pd.read_parquet(pq))

    segments = build_segments(day_meta)
    syms = sorted(set().union(*[s["symbols"] for s in segments]))
    # segments contain sets — convert for pickle
    seg_pickled = []
    for s in segments:
        seg_pickled.append({**s, "symbols": set(s["symbols"])})
    tasks = [
        (sym, seg_pickled, str(day_meta["push_dir"]), day_meta["date"], day)
        for sym in syms
    ]
    rows = []
    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(w43c._worker, t): t[0] for t in tasks}
        done = 0
        for fut in as_completed(futs):
            rows.extend(fut.result())
            done += 1
            if done % 10 == 0 or done == len(tasks):
                print(f"  {day} {done}/{len(tasks)} symbols rows={len(rows)}", flush=True)
    df = pd.DataFrame(rows)
    return enrich_features(df)


def selection_inversion_daily(moves: pd.DataFrame, entries: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for day, mday in moves.groupby("trading_date"):
        missed = mday[mday["capture_class"] == "MISSED"]
        eday = entries[entries["trading_date"] == day] if not entries.empty else entries
        stop = eday[eday["exit_reason"] == "stop_hit"] if not eday.empty else eday
        np_ = eday[eday["exit_reason"] == "no_progress_exit"] if not eday.empty else eday
        for name, a, b, fa, fb in (
            ("missed_vs_stop_ret30", missed, stop, "ret_30s", "ret_30s"),
            ("missed_vs_stop_accel60", missed, stop, "accel_60s", "accel_60s"),
            ("missed_vs_np_ret30", missed, np_, "ret_30s", "ret_30s"),
            ("missed_vs_np_accel60", missed, np_, "accel_60s", "accel_60s"),
        ):
            # for entries, join nearest features from moves? use entry-time from snapshot via entries only — approximate with entry pnl day features from missed vs entry symbols at entry
            if name.endswith("ret30") or name.endswith("accel60"):
                # stop/np need feature at entry: merge from moves of same symbol closest before entry — skip if no; use entries features from global snap later
                pass
            av = pd.to_numeric(a.get(fa), errors="coerce").to_numpy() if len(a) else np.array([])
            # for STOP/NP use feature from moves where symbol matched entry — fallback: empty
            if len(b) and not isinstance(b, pd.DataFrame):
                b = pd.DataFrame(b)
            bv = np.array([])
            if len(b) and not b.empty and "symbol" in b.columns:
                # take missed-style features from same-day snapshots of entry symbols via moves file — use entry rows if they have features
                if fa in b.columns:
                    bv = pd.to_numeric(b[fa], errors="coerce").to_numpy()
            rows.append(
                {
                    "trading_date": day,
                    "comparison": name,
                    "n_a": int(np.isfinite(av).sum()),
                    "n_b": int(np.isfinite(bv).sum()) if len(bv) else 0,
                    "median_a": float(np.nanmedian(av)) if np.isfinite(av).any() else None,
                    "median_b": float(np.nanmedian(bv)) if len(bv) and np.isfinite(bv).any() else None,
                    "cliffs_delta": cliffs_delta(av, bv) if len(bv) else None,
                    "missed_ret_negative": float(np.nanmedian(av)) < 0 if name.startswith("missed_vs_stop_ret") and np.isfinite(av).any() else None,
                }
            )
    return rows


def attach_entry_features(entries: pd.DataFrame, snaps: pd.DataFrame) -> pd.DataFrame:
    if entries.empty or snaps.empty:
        return entries
    parts = []
    for (day, sym), g in entries.groupby(["trading_date", "symbol"]):
        sg = snaps[(snaps["trading_date"] == day) & (snaps["symbol"] == sym)].sort_values("t0_epoch")
        if sg.empty:
            parts.append(g)
            continue
        m = pd.merge_asof(
            g.sort_values("entry_epoch"),
            sg[["t0_epoch"] + [c for c in FEATURE_COLS if c in sg.columns]],
            left_on="entry_epoch",
            right_on="t0_epoch",
            direction="backward",
        )
        parts.append(m)
    return pd.concat(parts, ignore_index=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("W43D detecting days...", flush=True)
    usable, inspected = detect_days()
    integrity = {
        "priority_days": PRIORITY_DAYS,
        "inspected": inspected,
        "usable_days": [u["date"] for u in usable],
        "usable_count": len(usable),
        "w43c_old_rule_rejected": W43C_OLD_RULE_REJECTED,
    }
    if len(usable) < 5:
        integrity["verdict_flag"] = "INSUFFICIENT_5DAY_WATCH50_DATA"
    print("usable:", [u["date"] for u in usable], flush=True)

    if not usable:
        _wj(OUT / "w43d_5d_data_integrity.json", {**integrity, "error": "no_usable_days"})
        _wj(OUT / "w43d_5d_report.json", {"verdicts": ["INSUFFICIENT_5DAY_WATCH50_DATA", "DATA_INTEGRITY_BLOCKED"]})
        return 1

    all_snaps = []
    all_entries = []
    for u in usable:
        print(f"Building snapshots {u['date']}...", flush=True)
        df = run_day_snapshots(u)
        all_snaps.append(df)
        ent = load_official_for_day(u)
        print(f"  snaps={len(df)} entries={len(ent)}", flush=True)
        all_entries.append(ent)

    snaps = pd.concat(all_snaps, ignore_index=True)
    entries = pd.concat(all_entries, ignore_index=True) if any(len(e) for e in all_entries) else pd.DataFrame()
    entries = attach_entry_features(entries, snaps)

    print("Building raw episodes...", flush=True)
    raw_ep = build_raw_episodes(snaps)
    raw_ep = attach_capture(raw_ep, entries)
    print(f"  raw episodes={len(raw_ep)} — causal funnel...", flush=True)
    raw_ep, audit = classify_causal_funnel(raw_ep, usable)
    moves = build_independent_moves(raw_ep)
    print(f"  independent moves={len(moves)}", flush=True)

    # W43C correction on 20260717
    ep17 = raw_ep[raw_ep["trading_date"] == "20260717"]
    w43c_corr = {
        "old_rule_rejected": W43C_OLD_RULE_REJECTED,
        "new_funnel_counts": dict(Counter(ep17["funnel_class"])),
        "new_entry_rule_rejected": int((ep17["funnel_class"] == "ENTRY_RULE_REJECTED").sum()),
        "new_pbv2_base_not_candidate": int((ep17["funnel_class"] == "PBV2_BASE_NOT_CANDIDATE").sum()),
        "missed_total": int((ep17["capture_class"] == "MISSED").sum()),
    }

    # Causal funnel table (independent moves primary)
    funnel_rows = []
    for scope_name, dfm in (("raw_episode", raw_ep), ("independent_move", moves)):
        for day, g in list(dfm.groupby("trading_date")) + [("ALL", dfm)]:
            vc = g["funnel_class"].value_counts().to_dict()
            funnel_rows.append(
                {
                    "scope": scope_name,
                    "trading_date": day,
                    "n": len(g),
                    **{f"n_{k}": int(v) for k, v in vc.items()},
                    "sum_check": int(sum(vc.values())),
                }
            )

    # Capture daily
    daily_cap = []
    for day, g in moves.groupby("trading_date"):
        n = len(g)
        c5 = int((g["capture_class"] == "CAPTURED_5M").sum())
        c15 = int((g["capture_class"].isin(["CAPTURED_5M", "LATE_CAPTURED_15M"])).sum())
        daily_cap.append(
            {
                "trading_date": day,
                "independent_moves": n,
                "captured_5m": c5,
                "captured_15m": c15,
                "capture_rate_5m": round(c5 / n, 4) if n else None,
                "capture_rate_15m": round(c15 / n, 4) if n else None,
                "missed": int((g["capture_class"] == "MISSED").sum()),
            }
        )
    daily_cap.append(
        {
            "trading_date": "ALL",
            "independent_moves": len(moves),
            "captured_5m": int((moves["capture_class"] == "CAPTURED_5M").sum()),
            "captured_15m": int((moves["capture_class"].isin(["CAPTURED_5M", "LATE_CAPTURED_15M"])).sum()),
            "capture_rate_5m": round(float((moves["capture_class"] == "CAPTURED_5M").mean()), 4) if len(moves) else None,
            "capture_rate_15m": round(
                float((moves["capture_class"].isin(["CAPTURED_5M", "LATE_CAPTURED_15M"])).mean()), 4
            )
            if len(moves)
            else None,
            "missed": int((moves["capture_class"] == "MISSED").sum()),
        }
    )

    # Feature effects day-by-day
    print("Feature effects...", flush=True)
    effect_rows = []
    inv_rows = []
    for day, mday in moves.groupby("trading_date"):
        missed = mday[mday["capture_class"] == "MISSED"]
        captured = mday[mday["capture_class"] == "CAPTURED_5M"]
        eday = entries[entries["trading_date"] == day] if not entries.empty else entries
        stop = eday[eday["exit_reason"] == "stop_hit"] if len(eday) else eday
        nprog = eday[eday["exit_reason"] == "no_progress_exit"] if len(eday) else eday
        # LARGE_RISE vs SIDEWAYS using independent move anchors only for LR; for sideways use snap downsample
        side = snaps[(snaps["trading_date"] == day) & (snaps["primary_label"] == "SIDEWAYS")]
        # downsample sideways to episode-like: every 10th
        side = side.iloc[::10] if len(side) > 200 else side
        dec = snaps[(snaps["trading_date"] == day) & (snaps["primary_label"] == "DECLINE")]
        dec = dec.iloc[::10] if len(dec) > 200 else dec

        comps = [
            ("MISSED_vs_NO_PROGRESS", missed, nprog),
            ("MISSED_vs_STOP", missed, stop),
            ("CAPTURED_vs_MISSED", captured, missed),
            ("LARGE_RISE_vs_SIDEWAYS", mday, side),
            ("LARGE_RISE_vs_DECLINE", mday, dec),
            ("CAPTURED_vs_STOP", captured, stop),
        ]
        for cname, a, b in comps:
            if len(a) < 3 or len(b) < 3:
                continue
            for feat in FEATURE_COLS:
                if feat not in a.columns or feat not in b.columns:
                    continue
                av = pd.to_numeric(a[feat], errors="coerce").to_numpy(dtype=float)
                bv = pd.to_numeric(b[feat], errors="coerce").to_numpy(dtype=float)
                y = np.concatenate([np.ones(len(a)), np.zeros(len(b))])
                s = np.concatenate([av, bv])
                cd = cliffs_delta(av, bv)
                effect_rows.append(
                    {
                        "trading_date": day,
                        "comparison": cname,
                        "feature": feat,
                        "n_a": int(np.isfinite(av).sum()),
                        "n_b": int(np.isfinite(bv).sum()),
                        "median_a": float(np.nanmedian(av)) if np.isfinite(av).any() else None,
                        "median_b": float(np.nanmedian(bv)) if np.isfinite(bv).any() else None,
                        "cliffs_delta": cd,
                        "auc": auc_safe(y, s),
                    }
                )
            # inversion specifics
            if cname == "MISSED_vs_STOP" and "ret_30s" in a.columns and "ret_30s" in b.columns:
                inv_rows.append(
                    {
                        "trading_date": day,
                        "missed_ret30_median": float(pd.to_numeric(a["ret_30s"], errors="coerce").median()),
                        "stop_ret30_median": float(pd.to_numeric(b["ret_30s"], errors="coerce").median()),
                        "missed_accel60_median": float(pd.to_numeric(a.get("accel_60s"), errors="coerce").median()),
                        "stop_accel60_median": float(pd.to_numeric(b.get("accel_60s"), errors="coerce").median()),
                        "inversion_ret": float(pd.to_numeric(a["ret_30s"], errors="coerce").median())
                        < float(pd.to_numeric(b["ret_30s"], errors="coerce").median()),
                        "missed_neg_ret": float(pd.to_numeric(a["ret_30s"], errors="coerce").median()) < 0,
                        "stop_pos_ret": float(pd.to_numeric(b["ret_30s"], errors="coerce").median()) > 0,
                        "n_missed": len(a),
                        "n_stop": len(b),
                    }
                )

    eff_df = pd.DataFrame(effect_rows)
    # stability summary
    stable = []
    if not eff_df.empty:
        for (comp, feat), g in eff_df.groupby(["comparison", "feature"]):
            g = g.dropna(subset=["cliffs_delta"])
            if len(g) < 1:
                continue
            signs = np.sign(g["cliffs_delta"].astype(float))
            agree = int((signs == signs.mode().iloc[0]).sum()) if len(signs) else 0
            stable.append(
                {
                    "comparison": comp,
                    "feature": feat,
                    "n_days": len(g),
                    "direction_agree_days": agree,
                    "mean_cliffs": float(g["cliffs_delta"].mean()),
                    "min_n_a": int(g["n_a"].min()),
                    "min_n_b": int(g["n_b"].min()),
                    "exploratory_only": not (
                        len(g) >= 4 and agree >= 4 and g["n_a"].min() >= 20 and g["n_b"].min() >= 20
                    ),
                }
            )
    stable_df = pd.DataFrame(stable)

    # Interactions
    print("Interactions...", flush=True)
    inter_rows = []
    for day, mday in moves.groupby("trading_date"):
        missed = mday[mday["capture_class"] == "MISSED"]
        nprog = entries[(entries["trading_date"] == day) & (entries["exit_reason"] == "no_progress_exit")]
        stop = entries[(entries["trading_date"] == day) & (entries["exit_reason"] == "stop_hit")]
        for cname, a, b in (("MISSED_vs_NO_PROGRESS", missed, nprog), ("MISSED_vs_STOP", missed, stop)):
            if len(a) < 5 or len(b) < 5:
                continue
            for feats in COMBOS_2:
                if any(f not in a.columns for f in feats):
                    continue
                aa = pd.to_numeric(a[feats[0]], errors="coerce") * pd.to_numeric(a[feats[1]], errors="coerce")
                bb = pd.to_numeric(b[feats[0]], errors="coerce") * pd.to_numeric(b[feats[1]], errors="coerce")
                inter_rows.append(
                    {
                        "trading_date": day,
                        "comparison": cname,
                        "features": "×".join(feats),
                        "order": 2,
                        "cliffs_delta": cliffs_delta(aa.to_numpy(), bb.to_numpy()),
                        "n_a": int(aa.notna().sum()),
                        "n_b": int(bb.notna().sum()),
                    }
                )
            for feats in COMBOS_3:
                if any(f not in a.columns for f in feats):
                    continue
                aa = (
                    pd.to_numeric(a[feats[0]], errors="coerce")
                    * pd.to_numeric(a[feats[1]], errors="coerce")
                    * pd.to_numeric(a[feats[2]], errors="coerce")
                )
                bb = (
                    pd.to_numeric(b[feats[0]], errors="coerce")
                    * pd.to_numeric(b[feats[1]], errors="coerce")
                    * pd.to_numeric(b[feats[2]], errors="coerce")
                )
                inter_rows.append(
                    {
                        "trading_date": day,
                        "comparison": cname,
                        "features": "×".join(feats),
                        "order": 3,
                        "cliffs_delta": cliffs_delta(aa.to_numpy(), bb.to_numpy()),
                        "n_a": int(aa.notna().sum()),
                        "n_b": int(bb.notna().sum()),
                    }
                )
    inter_df = pd.DataFrame(inter_rows)
    inter_stable = []
    if not inter_df.empty:
        for (comp, feat), g in inter_df.groupby(["comparison", "features"]):
            g = g.dropna(subset=["cliffs_delta"])
            if g.empty:
                continue
            signs = np.sign(g["cliffs_delta"])
            agree = int((signs == signs.mode().iloc[0]).sum())
            inter_stable.append(
                {
                    "comparison": comp,
                    "features": feat,
                    "order": int(g["order"].iloc[0]),
                    "n_days": len(g),
                    "direction_agree_days": agree,
                    "mean_cliffs": float(g["cliffs_delta"].mean()),
                    "exploratory_only": not (len(g) >= 4 and agree >= 4),
                }
            )

    # Leave-one-day-out on MISSED vs NP using accel_60s × ret_30s score
    print("LOD...", flush=True)
    lod_rows = []
    days = sorted(moves["trading_date"].unique())
    for hold in days:
        train = moves[moves["trading_date"] != hold]
        test = moves[moves["trading_date"] == hold]
        tr_m = train[train["capture_class"] == "MISSED"]
        tr_np = entries[(entries["trading_date"] != hold) & (entries["exit_reason"] == "no_progress_exit")]
        if len(tr_m) < 10 or len(tr_np) < 5 or test.empty:
            continue
        # score = -ret_30s + accel_60s (hypothesis: missed has neg ret + improving accel)
        def score(df):
            return -pd.to_numeric(df.get("ret_30s"), errors="coerce").fillna(0) + pd.to_numeric(
                df.get("accel_60s"), errors="coerce"
            ).fillna(0)

        # precision of top quartile on test missed vs all test moves
        sc = score(test)
        thr = score(tr_m).median()
        pred = sc >= thr
        # among predicted, fraction that are LARGE_RISE moves that were missed winners vs stop
        lod_rows.append(
            {
                "holdout_day": hold,
                "train_days": len(days) - 1,
                "threshold_median_train_missed_score": float(thr) if pd.notna(thr) else None,
                "test_moves": len(test),
                "test_pred_rate": float(pred.mean()) if len(pred) else None,
                "test_missed_score_median": float(score(test[test["capture_class"] == "MISSED"]).median())
                if (test["capture_class"] == "MISSED").any()
                else None,
            }
        )

    # Ranking every 60s
    print("Ranking...", flush=True)
    rank_rows = []
    for day, sdf in snaps.groupby("trading_date"):
        # 60s grid
        times = sorted(sdf["t0_epoch"].unique())
        times = [t for t in times if int(t) % 60 < 1 or abs((t % 60) - 0) < 1e-6 or True]
        times = times[::2]  # every 60s approx (snapshots are 30s)
        eday = entries[entries["trading_date"] == day] if not entries.empty else entries
        for t0 in times:
            slot = sdf[sdf["t0_epoch"] == t0]
            if len(slot) < 10:
                continue
            slot = slot.copy()
            slot["score"] = (
                -pd.to_numeric(slot.get("ret_30s"), errors="coerce").fillna(0)
                + 2 * pd.to_numeric(slot.get("accel_60s"), errors="coerce").fillna(0)
                + 0.5 * pd.to_numeric(slot.get("pre_300s_new_high_count"), errors="coerce").fillna(0)
            )
            slot = slot.sort_values("score", ascending=False)
            for k in (3, 5, 10):
                top = slot.head(k)
                lr = (top["primary_label"] == "LARGE_RISE").mean()
                rank_rows.append(
                    {
                        "trading_date": day,
                        "t0_epoch": t0,
                        "k": k,
                        "method": "w43d_score",
                        "large_rise_precision": float(lr),
                        "mean_future_30m_return": float(pd.to_numeric(top["future_30m_return"], errors="coerce").mean()),
                        "mean_future_30m_mfe": float(pd.to_numeric(top["future_30m_mfe"], errors="coerce").mean()),
                        "sideways_rate": float((top["primary_label"] == "SIDEWAYS").mean()),
                        "decline_rate": float((top["primary_label"] == "DECLINE").mean()),
                    }
                )
            # random
            rnd = slot.sample(n=min(5, len(slot)), random_state=int(t0) % 10000)
            rank_rows.append(
                {
                    "trading_date": day,
                    "t0_epoch": t0,
                    "k": 5,
                    "method": "random5",
                    "large_rise_precision": float((rnd["primary_label"] == "LARGE_RISE").mean()),
                    "mean_future_30m_return": float(pd.to_numeric(rnd["future_30m_return"], errors="coerce").mean()),
                    "mean_future_30m_mfe": float(pd.to_numeric(rnd["future_30m_mfe"], errors="coerce").mean()),
                    "sideways_rate": float((rnd["primary_label"] == "SIDEWAYS").mean()),
                    "decline_rate": float((rnd["primary_label"] == "DECLINE").mean()),
                }
            )
            # official entries near this time (±60s)
            if not eday.empty:
                near = eday[(eday["entry_epoch"] >= t0) & (eday["entry_epoch"] < t0 + 60)]
                if len(near):
                    lab = []
                    for _, e in near.iterrows():
                        row = slot[slot["symbol"] == e["symbol"]]
                        lab.append(row.iloc[0]["primary_label"] if len(row) else "UNAVAILABLE")
                    rank_rows.append(
                        {
                            "trading_date": day,
                            "t0_epoch": t0,
                            "k": len(near),
                            "method": "pbv2_official",
                            "large_rise_precision": float(np.mean([x == "LARGE_RISE" for x in lab])),
                            "mean_future_30m_return": None,
                            "mean_future_30m_mfe": None,
                            "sideways_rate": float(np.mean([x == "SIDEWAYS" for x in lab])),
                            "decline_rate": float(np.mean([x == "DECLINE" for x in lab])),
                        }
                    )
    rank_df = pd.DataFrame(rank_rows)
    rank_summary = (
        rank_df.groupby(["method", "k"], dropna=False)
        .agg(
            n=("large_rise_precision", "count"),
            large_rise_precision=("large_rise_precision", "mean"),
            mean_future_30m_return=("mean_future_30m_return", "mean"),
            mean_future_mfe=("mean_future_30m_mfe", "mean"),
            sideways_rate=("sideways_rate", "mean"),
            decline_rate=("decline_rate", "mean"),
        )
        .reset_index()
        if not rank_df.empty
        else pd.DataFrame()
    )

    # Refresh analysis
    refresh_rows = []
    for day, g in moves.groupby("trading_date"):
        for seg in ("am_open", "am_refresh1000", "pm_open", "pm_refresh1430"):
            gg = g[g["universe_segment"] == seg]
            n = len(gg)
            if seg == "pm_refresh1430":
                # 15m based: treat positive future_15m_return_at_anchor as proxy success if available from raw
                raw_g = raw_ep[(raw_ep["trading_date"] == day) & (raw_ep["universe_segment"] == seg)]
                refresh_rows.append(
                    {
                        "trading_date": day,
                        "segment": seg,
                        "moves": n,
                        "capture_rate_5m": round(float((gg["capture_class"] == "CAPTURED_5M").mean()), 4) if n else None,
                        "eval": "15m_labels",
                        "mean_future_15m_return": float(
                            pd.to_numeric(raw_g.get("future_15m_return_at_anchor"), errors="coerce").mean()
                        )
                        if len(raw_g)
                        else None,
                    }
                )
            else:
                refresh_rows.append(
                    {
                        "trading_date": day,
                        "segment": seg,
                        "moves": n,
                        "capture_rate_5m": round(float((gg["capture_class"] == "CAPTURED_5M").mean()), 4) if n else None,
                        "capture_rate_15m": round(
                            float((gg["capture_class"].isin(["CAPTURED_5M", "LATE_CAPTURED_15M"])).mean()), 4
                        )
                        if n
                        else None,
                        "eval": "30m_labels",
                    }
                )

    # Outlier sensitivity
    outlier_rows = []
    for name, g in (
        ("all", moves),
        ("excl_7581", moves[moves["symbol"] != OUTLIER]),
    ):
        n = len(g)
        outlier_rows.append(
            {
                "variant": name,
                "moves": n,
                "capture_rate_5m": round(float((g["capture_class"] == "CAPTURED_5M").mean()), 4) if n else None,
            }
        )
    if not moves.empty and moves["max_future_mfe"].notna().any():
        sym = moves.sort_values("max_future_mfe", ascending=False).iloc[0]["symbol"]
        g = moves[moves["symbol"] != sym]
        outlier_rows.append(
            {
                "variant": f"excl_max_mfe_{sym}",
                "moves": len(g),
                "capture_rate_5m": round(float((g["capture_class"] == "CAPTURED_5M").mean()), 4),
            }
        )

    # Guard-specific (ENTRY_RULE only)
    guard_audit = []
    for i, r in raw_ep[raw_ep["funnel_class"] == "ENTRY_RULE_REJECTED"].iterrows():
        detail = str(r.get("funnel_detail") or "")
        for gk in RULE_KEYS:
            if gk in detail.lower() or gk.replace("_", "") in detail.lower():
                guard_audit.append(
                    {
                        "trading_date": r["trading_date"],
                        "symbol": r["symbol"],
                        "guard_key": gk,
                        "capture_class": r["capture_class"],
                    }
                )
                break

    # Answers
    m_all = moves
    funnel_all = next(r for r in funnel_rows if r["scope"] == "independent_move" and r["trading_date"] == "ALL")
    # counts from independent moves
    def fc(name: str) -> int:
        return int((m_all["funnel_class"] == name).sum()) if len(m_all) else 0

    inv_df = pd.DataFrame(inv_rows)
    inv_days = int(inv_df["inversion_ret"].sum()) if not inv_df.empty and "inversion_ret" in inv_df else 0
    inv_n = len(inv_df) if not inv_df.empty else 0

    best_2 = None
    best_3 = None
    if inter_stable:
        c2 = [x for x in inter_stable if x["order"] == 2 and x["comparison"] == "MISSED_vs_NO_PROGRESS"]
        c3 = [x for x in inter_stable if x["order"] == 3 and x["comparison"] == "MISSED_vs_NO_PROGRESS"]
        if c2:
            best_2 = sorted(c2, key=lambda x: (-x["direction_agree_days"], -abs(x["mean_cliffs"])))[0]
        if c3:
            best_3 = sorted(c3, key=lambda x: (-x["direction_agree_days"], -abs(x["mean_cliffs"])))[0]

    best_miss_np = None
    best_miss_stop = None
    if not stable_df.empty:
        for comp, holder in (("MISSED_vs_NO_PROGRESS", "best_miss_np"), ("MISSED_vs_STOP", "best_miss_stop")):
            sub = stable_df[stable_df["comparison"] == comp].sort_values(
                ["direction_agree_days", "mean_cliffs"], ascending=[False, False]
            )
            if len(sub):
                if holder == "best_miss_np":
                    best_miss_np = sub.iloc[0].to_dict()
                else:
                    best_miss_stop = sub.iloc[0].to_dict()

    # refresh improvement 10:00
    ref_am = [r for r in refresh_rows if r["segment"] in ("am_open", "am_refresh1000")]
    improve_1000 = False
    if ref_am:
        by_day = defaultdict(dict)
        for r in ref_am:
            by_day[r["trading_date"]][r["segment"]] = r.get("capture_rate_5m")
        improve_1000 = sum(
            1
            for d, v in by_day.items()
            if v.get("am_refresh1000") is not None
            and v.get("am_open") is not None
            and v["am_refresh1000"] > v["am_open"]
        ) >= max(1, len(by_day) // 2)

    rank_w = rank_summary[rank_summary["method"] == "w43d_score"] if not rank_summary.empty else pd.DataFrame()
    rank_p = rank_summary[rank_summary["method"] == "pbv2_official"] if not rank_summary.empty else pd.DataFrame()
    ranking_edge = False
    if len(rank_w) and len(rank_p):
        ranking_edge = float(rank_w["large_rise_precision"].max()) > float(rank_p["large_rise_precision"].mean())

    pbv2_n = fc("PBV2_BASE_NOT_CANDIDATE")
    rule_n = fc("ENTRY_RULE_REJECTED")
    main_cause = "PBV2_BASE_NOT_CANDIDATE" if pbv2_n >= rule_n else "ENTRY_RULE_REJECTED"

    answers = {
        "1_w43c_204_rule_rejected_becomes": w43c_corr["new_entry_rule_rejected"],
        "1_w43c_correction_detail": w43c_corr,
        "2_pbv2_base_not_candidate": pbv2_n,
        "3_entry_rule_rejected": rule_n,
        "4_data_quality_blocked": fc("DATA_QUALITY_BLOCKED"),
        "5_same_symbol_position_blocked": fc("SAME_SYMBOL_POSITION_BLOCKED"),
        "6_scan_or_queue_limited": fc("SCAN_OR_QUEUE_LIMITED"),
        "7_cap_blocked_confirmed": fc("CAP_BLOCKED_CONFIRMED"),
        "8_independent_large_rise_moves_5d": int(len(moves)),
        "9_capture_rate_5m": daily_cap[-1].get("capture_rate_5m"),
        "9_capture_rate_15m": daily_cap[-1].get("capture_rate_15m"),
        "10_daily_capture": daily_cap,
        "11_selection_inversion_days": f"{inv_days}/{inv_n}" if inv_n else "0/0",
        "11_reproduced_ge_4_of_5": bool(inv_n >= 4 and inv_days >= 4),
        "12_missed_neg_ret_pos_accel": inv_df.to_dict(orient="records") if not inv_df.empty else [],
        "13_stop_pos_ret": inv_df.to_dict(orient="records") if not inv_df.empty else [],
        "14_best_vs_decline": (
            stable_df[stable_df["comparison"] == "LARGE_RISE_vs_DECLINE"]
            .sort_values("direction_agree_days", ascending=False)
            .head(1)
            .to_dict(orient="records")
            if not stable_df.empty
            else []
        ),
        "15_best_vs_noprogress": best_miss_np,
        "16_best_2feat": best_2,
        "17_best_3feat": best_3,
        "18_lod": lod_rows,
        "19_ranking_summary": rank_summary.to_dict(orient="records") if not rank_summary.empty else [],
        "19_ranking_edge_vs_pbv2": ranking_edge,
        "20_refresh_1000_improved": improve_1000,
        "21_refresh_1430": [r for r in refresh_rows if r["segment"] == "pm_refresh1430"],
        "22_main_cause": main_cause,
        "23_chase_vs_reversal": (
            "chase_bias"
            if inv_n and inv_days >= max(1, inv_n // 2)
            else "inconclusive_single_pattern"
        ),
        "24_shadow_candidates": [
            x
            for x in (best_2, best_3, best_miss_np)
            if x and (not x.get("exploratory_only", True) or x.get("direction_agree_days", 0) >= 3)
        ],
        "25_runtime_unchanged_conclusion": (
            "5-day research validation only. Causal funnel corrected vs W43C. "
            "No Runtime/YAML adoption from this phase."
        ),
    }

    verdicts = ["W43C_CAUSAL_CLASSIFICATION_CORRECTED"]
    if len(usable) < 5:
        verdicts.append("INSUFFICIENT_5DAY_WATCH50_DATA")
    if pbv2_n > rule_n:
        verdicts.append("FOUND_PBV2_BASE_CANDIDATE_LIMIT")
    if rule_n > 0:
        verdicts.append("FOUND_SPECIFIC_GUARD_CAPTURE_LIMIT")
    if answers["11_reproduced_ge_4_of_5"]:
        verdicts.append("FOUND_STABLE_SELECTION_INVERSION")
        verdicts.append("FOUND_CHASE_ENTRY_BIAS")
    if best_2 and best_2.get("direction_agree_days", 0) >= 4:
        verdicts.append("FOUND_REVERSAL_CONFIRMATION_SIGNAL")
        verdicts.append("FOUND_STABLE_MISSED_WINNER_STATE")
    elif int(len(moves)) > 0 and daily_cap[-1].get("missed", 0) > daily_cap[-1].get("captured_5m", 0):
        verdicts.append("FOUND_NO_STABLE_WINNER_STATE")
    if improve_1000:
        verdicts.append("FOUND_REFRESH_CAPTURE_IMPROVEMENT")
    if ranking_edge:
        verdicts.append("FOUND_STABLE_RANKING_EDGE")
    if fc("SCAN_OR_QUEUE_LIMITED") > 0:
        verdicts.append("FOUND_SCAN_QUEUE_CAPTURE_LIMIT")

    report = {
        "phase": "Phase687W43D",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdicts": verdicts,
        "usable_days": [u["date"] for u in usable],
        "required_answers": answers,
        "runtime_changed": False,
    }

    print("Writing outputs...", flush=True)
    raw_ep.to_parquet(OUT / "w43d_5d_raw_episodes.parquet", index=False)
    _wc(OUT / "w43d_5d_independent_moves.csv", moves)
    _wc(OUT / "w43d_5d_causal_funnel.csv", funnel_rows)
    _wc(OUT / "w43d_5d_reject_reason_audit.csv", audit if len(audit) else pd.DataFrame(guard_audit))
    _wc(OUT / "w43d_5d_daily_capture.csv", daily_cap)
    _wc(OUT / "w43d_5d_feature_effect.csv", eff_df)
    _wc(OUT / "w43d_5d_feature_interactions.csv", inter_df)
    _wc(OUT / "w43d_5d_selection_inversion.csv", inv_df if len(inv_df) else inv_rows)
    _wc(OUT / "w43d_5d_leave_one_day_out.csv", lod_rows)
    _wc(OUT / "w43d_5d_ranking_comparison.csv", rank_summary if len(rank_summary) else rank_df.head(500))
    _wc(OUT / "w43d_5d_refresh_analysis.csv", refresh_rows)
    _wc(OUT / "w43d_5d_outlier_sensitivity.csv", outlier_rows)
    integrity.update(
        {
            "snapshot_rows": int(len(snaps)),
            "raw_episodes": int(len(raw_ep)),
            "independent_moves": int(len(moves)),
            "official_entries": int(len(entries)),
            "w43c_correction": w43c_corr,
            "funnel_sum_matches": all(
                r.get("sum_check") == r.get("n") for r in funnel_rows if r["trading_date"] == "ALL"
            ),
        }
    )
    _wj(OUT / "w43d_5d_data_integrity.json", integrity)
    _wj(OUT / "w43d_5d_report.json", report)

    md = f"""# Phase687W43D — 5-Day Winner-State Validation

## Verdict
`{' | '.join(verdicts)}`

Usable days: `{[u['date'] for u in usable]}`

## W43C causal correction (20260717)
- Old RULE_REJECTED: **{W43C_OLD_RULE_REJECTED}**
- New ENTRY_RULE_REJECTED: **{w43c_corr['new_entry_rule_rejected']}**
- New PBV2_BASE_NOT_CANDIDATE: **{w43c_corr['new_pbv2_base_not_candidate']}**

## Independent moves capture
| day | moves | rate5m | rate15m |
|-----|------:|-------:|--------:|
"""
    for r in daily_cap:
        md += f"| {r['trading_date']} | {r['independent_moves']} | {r['capture_rate_5m']} | {r['capture_rate_15m']} |\n"

    md += f"""
## Funnel (independent ALL)
PBv2 base not candidate: **{pbv2_n}**  
ENTRY rule rejected: **{rule_n}**  
Main cause: **{main_cause}**

## Selection inversion
`{answers['11_selection_inversion_days']}` days with missed_ret30 < stop_ret30

## Best features
- vs NO_PROGRESS: `{best_miss_np}`
- 2-feat: `{best_2}`
- 3-feat: `{best_3}`

## Conclusion
{answers['25_runtime_unchanged_conclusion']}
"""
    _wm(OUT / "w43d_5d_report.md", md)
    print(json.dumps({"verdicts": verdicts, "days": [u["date"] for u in usable], "moves": len(moves)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
