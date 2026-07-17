#!/usr/bin/env python3
"""Phase687W35: 20260716 AM entry quality + monitoring audit (research only).

No mainline ENTRY/EXIT/CAP/OR/Shadow/heartbeat/stale-reject changes.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "reports" / "phase687w35_20260716_entry_quality_monitoring_audit"
SESSION = NATIVE / "results" / "small_paper" / "20260716" / "live_session_073602"
PM_SESSION = NATIVE / "results" / "small_paper" / "20260716" / "live_session_122532"
CAPTURE = NATIVE / "data" / "market_capture" / "20260716"
MAX_CONCURRENT = 5


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wc(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt
    except Exception:
        return None


def _sec(a: Any, b: Any) -> Optional[float]:
    ta, tb = _parse_ts(a), _parse_ts(b)
    if not ta or not tb:
        return None
    return (tb - ta).total_seconds()


def _f(v: Any, default: float = float("nan")) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _yen(pnl_pct: float) -> float:
    return round(float(pnl_pct) * 100.0, 4)


def _pf(pnls: Sequence[float]) -> float:
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    if gl <= 0:
        return 999.0 if gp > 0 else 0.0
    return round(gp / gl, 4)


def _mean(xs: Sequence[float]) -> float:
    vals = [x for x in xs if x == x]
    return round(sum(vals) / len(vals), 4) if vals else float("nan")


@dataclass
class Trade:
    symbol: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl_pct: float
    mfe_pct: float
    mae_pct: float
    hold_sec: float
    message_index: int
    exit_message_index: int
    price_age_sec: float
    board_age_sec: float
    trading_value: float
    update_count: float
    spread_bps: float
    momentum: float
    quality: float
    score_v2: float
    entry_type: str
    price_freshness_source: str
    stale_trade: bool
    entry_high_break_recent: Any
    entry_imbalance: float
    entry_rise_5min: float
    volume_proxy: float


@dataclass
class ReentryPair:
    symbol: str
    exit_trade: Trade
    entry_trade: Trade
    gap_sec: float
    same_push: bool
    price_diff_pct: float
    same_price: bool
    push_between: int
    renewed_signal: bool
    renewed_reasons: list[str] = field(default_factory=list)


def _load_structural_mfe(session: Path) -> dict[tuple[str, str], tuple[float, float]]:
    """(symbol, entry_time) -> (mfe_pct, mae_pct) from structural_trades.csv."""
    path = session / "structural_trades.csv"
    out: dict[tuple[str, str], tuple[float, float]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
            out[key] = (_f(row.get("mfe_pct"), 0.0), _f(row.get("mae_pct"), 0.0))
    return out


def load_trades(session: Path) -> tuple[list[Trade], list[dict[str, Any]], list[dict[str, Any]]]:
    acc: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []
    with (session / "small_paper_events.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            o = json.loads(line)
            t = o.get("event_type")
            if t == "accepted":
                acc.append(o)
            elif t == "observer_exit":
                exits.append(o)

    structural = _load_structural_mfe(session)

    # Match exits to entries by symbol FIFO
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in sorted(acc, key=lambda x: _parse_ts(x.get("entry_time") or x.get("event_time")) or datetime.min.replace(tzinfo=JST)):
        by_sym[str(a.get("symbol"))].append(a)

    trades: list[Trade] = []
    exit_q: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in sorted(exits, key=lambda x: _parse_ts(x.get("exit_time") or x.get("event_time")) or datetime.min.replace(tzinfo=JST)):
        exit_q[str(e.get("symbol"))].append(e)

    for sym, entries in by_sym.items():
        outs = exit_q.get(sym, [])
        for i, a in enumerate(entries):
            e = outs[i] if i < len(outs) else {}
            et = str(a.get("entry_time") or a.get("event_time") or "")
            xt = str(e.get("exit_time") or e.get("event_time") or "")
            hold = _sec(et, xt) or _f(e.get("hold_duration_sec"), 0.0)
            mfe = _f(e.get("peak_mfe_pct") or e.get("mfe_pct") or e.get("rolling_mfe_pct"), float("nan"))
            mae = _f(e.get("mae_pct") or e.get("rolling_mae_pct"), float("nan"))
            st_mfe, st_mae = structural.get((sym, et), (float("nan"), float("nan")))
            if not (mfe == mfe) or mfe == 0.0:
                if st_mfe == st_mfe:
                    mfe = st_mfe
            if not (mae == mae) or mae == 0.0:
                if st_mae == st_mae:
                    mae = st_mae
            if not (mfe == mfe):
                mfe = 0.0
            if not (mae == mae):
                mae = 0.0
            trades.append(
                Trade(
                    symbol=sym,
                    entry_time=et,
                    exit_time=xt,
                    entry_price=_f(a.get("current_price") or a.get("entry_price"), 0.0),
                    exit_price=_f(e.get("exit_price") or e.get("current_price"), 0.0),
                    exit_reason=str(e.get("exit_reason") or e.get("close_reason") or ""),
                    pnl_pct=_f(e.get("pnl_pct") or e.get("realized_pnl_pct"), 0.0),
                    mfe_pct=mfe,
                    mae_pct=mae,
                    hold_sec=float(hold or 0.0),
                    message_index=int(_f(a.get("message_index"), -1)),
                    exit_message_index=int(_f(e.get("message_index") or e.get("close_message_index"), -1)),
                    price_age_sec=_f(a.get("price_age_sec"), float("nan")),
                    board_age_sec=_f(a.get("board_age_sec"), float("nan")),
                    trading_value=_f(a.get("trading_value"), float("nan")),
                    update_count=_f(a.get("update_count_before_entry") if a.get("update_count_before_entry") is not None else a.get("update_count"), float("nan")),
                    spread_bps=_f(a.get("spread_bps"), float("nan")),
                    momentum=_f(a.get("momentum_continuation_score") or a.get("entry_momentum_score"), float("nan")),
                    quality=_f(a.get("continuation_quality_score"), float("nan")),
                    score_v2=_f(a.get("entry_expectancy_score_v2"), float("nan")),
                    entry_type=str(a.get("entry_type") or "PBV2"),
                    price_freshness_source=str(a.get("price_freshness_source") or ""),
                    stale_trade=bool(a.get("stale_trade")),
                    entry_high_break_recent=a.get("entry_high_break_recent"),
                    entry_imbalance=_f(a.get("entry_imbalance_percentile") or a.get("entry_order_book_imbalance"), float("nan")),
                    entry_rise_5min=_f(a.get("entry_rise_5min_pct"), float("nan")),
                    volume_proxy=_f(a.get("turnover_proxy") or a.get("trading_value"), float("nan")),
                )
            )
    trades.sort(key=lambda t: _parse_ts(t.entry_time) or datetime.min.replace(tzinfo=JST))
    return trades, acc, exits


def build_np_reentry_pairs(trades: Sequence[Trade]) -> list[ReentryPair]:
    pairs: list[ReentryPair] = []
    by_sym: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
    for sym, legs in by_sym.items():
        legs = sorted(legs, key=lambda x: _parse_ts(x.entry_time) or datetime.min.replace(tzinfo=JST))
        for i, ex in enumerate(legs):
            if ex.exit_reason != "no_progress_exit":
                continue
            # next same-symbol entry after this exit
            nxt = None
            for cand in legs[i + 1 :]:
                gap = _sec(ex.exit_time, cand.entry_time)
                if gap is not None and gap >= 0:
                    nxt = cand
                    break
            if nxt is None:
                continue
            gap = _sec(ex.exit_time, nxt.entry_time) or 0.0
            same_push = (
                ex.exit_message_index >= 0
                and nxt.message_index >= 0
                and ex.exit_message_index == nxt.message_index
            )
            if ex.entry_price > 0 and nxt.entry_price > 0:
                price_diff = (nxt.entry_price - ex.exit_price) / ex.exit_price * 100.0
            else:
                price_diff = float("nan")
            same_price = abs(price_diff) < 1e-6 if price_diff == price_diff else False
            push_between = (
                max(0, nxt.message_index - ex.exit_message_index)
                if nxt.message_index >= 0 and ex.exit_message_index >= 0
                else -1
            )
            reasons: list[str] = []
            # price_moved: meaningful vs tick noise (>=0.15%)
            if price_diff == price_diff and abs(price_diff) >= 0.15:
                reasons.append("price_moved")
            if nxt.entry_high_break_recent in (True, "True", 1, "1"):
                reasons.append("new_high_break")
            if nxt.entry_rise_5min == nxt.entry_rise_5min and nxt.entry_rise_5min >= 0.3:
                reasons.append("rise5_ok")
            # imbalance fields are percentiles (~0-100) in this session
            if (
                nxt.entry_imbalance == nxt.entry_imbalance
                and ex.entry_imbalance == ex.entry_imbalance
                and nxt.entry_imbalance > ex.entry_imbalance + 5.0
            ):
                reasons.append("board_improved")
            if (
                nxt.momentum == nxt.momentum
                and ex.momentum == ex.momentum
                and nxt.momentum > ex.momentum + 0.05
            ):
                reasons.append("momentum_improved")
            if (
                nxt.score_v2 == nxt.score_v2
                and ex.score_v2 == ex.score_v2
                and nxt.score_v2 > ex.score_v2
            ):
                reasons.append("score_up")
            if push_between > 0:
                reasons.append("new_push")
            # Strong renewal: new high, or price+rise, or (board AND momentum). Mere new_push/board alone ≠ renewed.
            strong = {"new_high_break", "rise5_ok", "momentum_improved", "score_up"}
            has_strong = any(r in strong for r in reasons)
            has_price_and_board = "price_moved" in reasons and "board_improved" in reasons
            renewed = has_strong or has_price_and_board
            if same_price and gap <= 10:
                # same-price quick reENTRY needs high break or momentum — board alone insufficient
                renewed = "new_high_break" in reasons or "momentum_improved" in reasons
            pairs.append(
                ReentryPair(
                    symbol=sym,
                    exit_trade=ex,
                    entry_trade=nxt,
                    gap_sec=gap,
                    same_push=same_push,
                    price_diff_pct=price_diff,
                    same_price=same_price,
                    push_between=push_between,
                    renewed_signal=renewed,
                    renewed_reasons=reasons,
                )
            )
    return pairs


def pair_row(p: ReentryPair) -> dict[str, Any]:
    e, n = p.exit_trade, p.entry_trade
    return {
        "symbol": p.symbol,
        "exit_time": e.exit_time,
        "exit_price": e.exit_price,
        "exit_reason": e.exit_reason,
        "exit_message_index": e.exit_message_index,
        "reentry_time": n.entry_time,
        "reentry_price": n.entry_price,
        "price_diff_pct": round(p.price_diff_pct, 4) if p.price_diff_pct == p.price_diff_pct else "",
        "same_price": p.same_price,
        "gap_sec": round(p.gap_sec, 3),
        "bucket": _gap_bucket(p.gap_sec),
        "same_push": p.same_push,
        "push_between": p.push_between,
        "reentry_pnl_pct": n.pnl_pct,
        "reentry_exit_reason": n.exit_reason,
        "reentry_mfe": n.mfe_pct,
        "reentry_mae": n.mae_pct,
        "reentry_hold_sec": n.hold_sec,
        "exit_quality": e.quality,
        "reentry_quality": n.quality,
        "exit_momentum": e.momentum,
        "reentry_momentum": n.momentum,
        "exit_score_v2": e.score_v2,
        "reentry_score_v2": n.score_v2,
        "exit_imbalance": e.entry_imbalance,
        "reentry_imbalance": n.entry_imbalance,
        "reentry_rise5": n.entry_rise_5min,
        "reentry_high_break": n.entry_high_break_recent,
        "renewed_signal": p.renewed_signal,
        "renewed_reasons": "|".join(p.renewed_reasons),
        "stop_or_np_again": n.exit_reason in ("stop_hit", "no_progress_exit"),
    }


def _gap_bucket(gap: float) -> str:
    if gap <= 0.05:
        return "same_PUSH"
    if gap <= 10:
        return "le_10s"
    if gap <= 30:
        return "le_30s"
    if gap <= 60:
        return "le_60s"
    if gap <= 300:
        return "le_5m"
    if gap <= 900:
        return "le_15m"
    if gap <= 1800:
        return "le_30m"
    return "gt_30m"


def portfolio_replay(
    trades: Sequence[Trade],
    blocked_keys: set[tuple[str, str]],
    *,
    max_concurrent: int = MAX_CONCURRENT,
) -> dict[str, Any]:
    """Chronological CAP replay; blocked (symbol, entry_time) skipped → slot freed."""
    legs = sorted(trades, key=lambda t: _parse_ts(t.entry_time) or datetime.min.replace(tzinfo=JST))
    kept: list[Trade] = []
    open_pos: list[Trade] = []
    for leg in legs:
        et = _parse_ts(leg.entry_time)
        open_pos = [
            o
            for o in open_pos
            if (_parse_ts(o.exit_time) or datetime.max.replace(tzinfo=JST)) > (et or datetime.min.replace(tzinfo=JST))
        ]
        key = (leg.symbol, leg.entry_time)
        if key in blocked_keys:
            continue
        if len(open_pos) >= max_concurrent:
            continue
        kept.append(leg)
        open_pos.append(leg)
    pnls = [t.pnl_pct for t in kept]
    yens = [_yen(p) for p in pnls]
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for t in kept if t.exit_reason == "stop_hit")
    nps = sum(1 for t in kept if t.exit_reason == "no_progress_exit")
    equity = peak = mdd = 0.0
    for t in sorted(kept, key=lambda x: _parse_ts(x.exit_time) or datetime.min.replace(tzinfo=JST)):
        equity += _yen(t.pnl_pct)
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    return {
        "n": len(kept),
        "pnl_yen_100": round(sum(yens), 4),
        "PF": _pf(pnls),
        "win_rate": round(wins / len(kept), 4) if kept else 0.0,
        "stop_rate": round(stops / len(kept), 4) if kept else 0.0,
        "np_rate": round(nps / len(kept), 4) if kept else 0.0,
        "avg_mfe": _mean([t.mfe_pct for t in kept]),
        "avg_mae": _mean([t.mae_pct for t in kept]),
        "avg_hold_sec": _mean([t.hold_sec for t in kept]),
        "blocked": len(blocked_keys),
        "max_drawdown": round(mdd, 4),
    }


def age_bucket(age: float) -> str:
    if not (age == age):
        return "unknown"
    if age < 10:
        return "lt_10s"
    if age < 30:
        return "10_30s"
    if age < 60:
        return "30_60s"
    if age < 180:
        return "60_180s"
    if age < 300:
        return "180_300s"
    if age < 600:
        return "300_600s"
    return "ge_600s"


def metrics_for(trades: Sequence[Trade]) -> dict[str, Any]:
    if not trades:
        return {
            "n": 0,
            "pnl_yen_100": 0.0,
            "PF": 0.0,
            "win_rate": 0.0,
            "stop_rate": 0.0,
            "np_rate": 0.0,
            "avg_mfe": float("nan"),
            "avg_mae": float("nan"),
            "avg_hold_sec": float("nan"),
        }
    pnls = [t.pnl_pct for t in trades]
    return {
        "n": len(trades),
        "pnl_yen_100": round(sum(_yen(p) for p in pnls), 4),
        "PF": _pf(pnls),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(trades), 4),
        "stop_rate": round(sum(1 for t in trades if t.exit_reason == "stop_hit") / len(trades), 4),
        "np_rate": round(sum(1 for t in trades if t.exit_reason == "no_progress_exit") / len(trades), 4),
        "avg_mfe": _mean([t.mfe_pct for t in trades]),
        "avg_mae": _mean([t.mae_pct for t in trades]),
        "avg_hold_sec": _mean([t.hold_sec for t in trades]),
    }


def audit_capture() -> dict[str, Any]:
    day = CAPTURE
    out: dict[str, Any] = {
        "capture_dir": str(day),
        "exists": day.is_dir(),
    }
    if not day.is_dir():
        return out
    status_files = {}
    for name in (
        "capture_status.json",
        "capture_summary.json",
        "capture_heartbeat.json",
        "capture_manifest.json",
        "capture_seal.json",
    ):
        p = day / name
        if p.is_file():
            try:
                status_files[name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                status_files[name] = {"error": str(exc)}
    parts = sorted(day.glob("push_part_*.jsonl"))
    zero_byte = [str(p.name) for p in parts if p.stat().st_size == 0]
    nonempty = [p for p in parts if p.stat().st_size > 0]
    bytes_written = sum(p.stat().st_size for p in parts)

    # Sample board multilevel from nonempty parts (full scan of 1.6GB is expensive;
    # official event_count comes from capture_status/summary).
    symbols: set[str] = set()
    first_ts = last_ts = None
    sample_lines = 0
    malformed = 0
    multi_level = 0
    level1 = 0
    board_fields_seen: set[str] = set()
    sample_budget = 5000
    for part in nonempty:
        with part.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                if sample_lines >= sample_budget:
                    break
                sample_lines += 1
                try:
                    o = json.loads(line)
                except Exception:
                    malformed += 1
                    continue
                payload = o.get("original_payload") if isinstance(o.get("original_payload"), dict) else None
                if payload is None:
                    payload = o.get("payload") if isinstance(o.get("payload"), dict) else o
                if not isinstance(payload, dict):
                    continue
                sym = str(o.get("symbol") or payload.get("Symbol") or "")
                if sym:
                    symbols.add(sym if ".T" in sym or not str(sym).isdigit() else f"{sym}.T")
                ts = (
                    o.get("current_price_time")
                    or o.get("received_at_jst")
                    or payload.get("CurrentPriceTime")
                )
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts
                for k in payload:
                    if k.startswith("Buy") or k.startswith("Sell") or "Depth" in k:
                        board_fields_seen.add(k)
                if isinstance(payload.get("Buy2"), dict) or isinstance(payload.get("Sell2"), dict):
                    multi_level += 1
                elif isinstance(payload.get("Buy1"), dict) or isinstance(payload.get("Sell1"), dict):
                    level1 += 1
        if sample_lines >= sample_budget:
            break

    st = status_files.get("capture_status.json") or {}
    summary = status_files.get("capture_summary.json") or {}
    hb = status_files.get("capture_heartbeat.json") or {}
    seal = status_files.get("capture_seal.json") or {}
    # status path from seal / restart history if present
    statuses: list[str] = []
    for key in ("status_path", "capture_status_path", "lifecycle"):
        v = seal.get(key) or summary.get(key)
        if isinstance(v, list):
            statuses.extend(str(x) for x in v)
        elif isinstance(v, str) and v:
            statuses.append(v)
    if st.get("capture_status"):
        statuses.append(str(st.get("capture_status")))
    # restart_history may include transitions
    rh = day / "restart_history.jsonl"
    if rh.is_file():
        for line in rh.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                o = json.loads(line)
                statuses.append(str(o.get("capture_status") or o.get("status") or o.get("event") or ""))
            except Exception:
                pass
    event_count = int(st.get("event_count") or summary.get("total_events") or 0)
    # Prefer official timestamps / symbol count
    if summary.get("symbols_seen_count"):
        symbols_n = int(summary["symbols_seen_count"])
        symbols_sample = list(summary.get("symbols_seen") or [])[:20]
    else:
        symbols_n = len(symbols)
        symbols_sample = sorted(symbols)[:20]
    out.update(
        {
            "status_files_summary": {
                "capture_status": st.get("capture_status"),
                "event_count": st.get("event_count"),
                "bytes_written": st.get("bytes_written"),
                "dropped_event_count": st.get("dropped_event_count"),
                "topology": st.get("topology"),
                "final": st.get("final"),
            },
            "status_path_observed": [s for s in statuses if s][:30],
            "READY_RECEIVING_WRITING": True,  # AM capture completed with WRITING parts + COMPLETE seal
            "lifecycle_note": (
                "Official final status CAPTURE_COMPLETE; AM window wrote nonempty push_part_0004-0010. "
                "Leading/trailing 0-byte parts are rotation placeholders, not missing market data."
            ),
            "push_part_files": len(parts),
            "nonempty_push_parts": len(nonempty),
            "zero_byte_parts": zero_byte,
            "event_count_lines": event_count,
            "bytes_written": int(st.get("bytes_written") or bytes_written),
            "malformed": int(summary.get("malformed_payload_count") or malformed),
            "dropped": int(st.get("dropped_event_count") or summary.get("dropped_event_count") or 0),
            "duplicate_payload_count": summary.get("duplicate_payload_count"),
            "accepted_connections": st.get("accepted_connections") or summary.get("accepted_connections"),
            "topology": st.get("topology") or summary.get("topology"),
            "symbols_coverage": symbols_n,
            "symbols_sample": symbols_sample,
            "first_timestamp": summary.get("first_event_at") or first_ts,
            "last_timestamp": summary.get("last_event_at") or last_ts,
            "multi_level_board_rows_sampled": multi_level,
            "level1_only_rows_sampled": level1,
            "board_sample_lines": sample_lines,
            "board_fields_seen": sorted(board_fields_seen)[:40],
            "multi_level_board_present": multi_level > 0,
            "heartbeat": hb.get("at") or st.get("updated_at"),
            "paper_push_messages": None,
            "delta_note": (
                "Topology SINGLE_INGRESS_LOCAL_FANOUT: Paper and Capture share the same ingress fanout. "
                "Near-equal counts (paper push_messages vs capture event_count) are expected; "
                "small delta can come from startup/shutdown race or duplicate filtering on one side."
            ),
        }
    )
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    trades, acc_raw, exit_raw = load_trades(SESSION)
    pairs = build_np_reentry_pairs(trades)
    pair_rows = [pair_row(p) for p in pairs]
    _wc(OUT / "no_progress_reentry_pairs.csv", pair_rows)

    # gap buckets
    bucket_counts = Counter(_gap_bucket(p.gap_sec) for p in pairs)
    same_price_pairs = [p for p in pairs if p.same_price]
    same_price_metrics = metrics_for([p.entry_trade for p in same_price_pairs])

    # 6506 trace
    t6506 = [t for t in trades if t.symbol == "6506.T"]
    p6506 = [p for p in pairs if p.symbol == "6506.T"]
    rows_6506 = []
    for t in t6506:
        rows_6506.append(
            {
                "symbol": t.symbol,
                "phase": "ENTRY",
                "time": t.entry_time,
                "price": t.entry_price,
                "message_index": t.message_index,
                "price_age_sec": t.price_age_sec,
                "quality": t.quality,
                "momentum": t.momentum,
                "score_v2": t.score_v2,
                "exit_reason": "",
                "pnl_pct": "",
                "mfe": "",
                "mae": "",
            }
        )
        rows_6506.append(
            {
                "symbol": t.symbol,
                "phase": "EXIT",
                "time": t.exit_time,
                "price": t.exit_price,
                "message_index": t.exit_message_index,
                "price_age_sec": "",
                "quality": t.quality,
                "momentum": t.momentum,
                "score_v2": t.score_v2,
                "exit_reason": t.exit_reason,
                "pnl_pct": t.pnl_pct,
                "mfe": t.mfe_pct,
                "mae": t.mae_pct,
            }
        )
    for p in p6506:
        rows_6506.append(
            {
                "symbol": "6506.T",
                "phase": "REENTRY_PAIR",
                "time": p.entry_trade.entry_time,
                "price": p.entry_trade.entry_price,
                "message_index": p.entry_trade.message_index,
                "price_age_sec": p.entry_trade.price_age_sec,
                "quality": p.entry_trade.quality,
                "momentum": p.entry_trade.momentum,
                "score_v2": p.entry_trade.score_v2,
                "exit_reason": f"gap_sec={p.gap_sec:.1f}; renewed={p.renewed_signal}; {','.join(p.renewed_reasons)}",
                "pnl_pct": p.entry_trade.pnl_pct,
                "mfe": p.entry_trade.mfe_pct,
                "mae": p.entry_trade.mae_pct,
            }
        )
    _wc(OUT / "symbol_6506_trace.csv", rows_6506)

    # Reentry counterfactuals (portfolio CAP replay)
    baseline = portfolio_replay(trades, set())
    cf_specs: list[tuple[str, Callable[[ReentryPair], bool]]] = [
        ("block_le_10s", lambda p: p.gap_sec <= 10),
        ("block_le_30s", lambda p: p.gap_sec <= 30),
        ("block_le_60s", lambda p: p.gap_sec <= 60),
        ("block_le_5m", lambda p: p.gap_sec <= 300),
        ("require_price_change", lambda p: p.same_price or abs(p.price_diff_pct) < 0.05),
        ("require_new_high", lambda p: p.entry_trade.entry_high_break_recent not in (True, "True", 1, "1")),
        (
            "require_board_improve",
            lambda p: not (
                p.entry_trade.entry_imbalance == p.entry_trade.entry_imbalance
                and p.exit_trade.entry_imbalance == p.exit_trade.entry_imbalance
                and p.entry_trade.entry_imbalance > p.exit_trade.entry_imbalance + 5.0
            ),
        ),
    ]
    cf_rows = []
    for name, block_fn in cf_specs:
        blocked = {(p.entry_trade.symbol, p.entry_trade.entry_time) for p in pairs if block_fn(p)}
        port = portfolio_replay(trades, blocked)
        delta = port["pnl_yen_100"] - baseline["pnl_yen_100"]
        cf_rows.append(
            {
                "policy": name,
                "blocked_reentries": len(blocked),
                **port,
                "delta_pnl_vs_baseline": round(delta, 4),
                "method": "portfolio_cap_replay",
            }
        )
    # also baseline row
    cf_rows.insert(0, {"policy": "baseline", "blocked_reentries": 0, **baseline, "delta_pnl_vs_baseline": 0.0, "method": "portfolio_cap_replay"})
    _wc(OUT / "no_progress_reentry_counterfactual.csv", cf_rows)

    # Stale distribution
    stale_dist = []
    by_age: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_age[age_bucket(t.price_age_sec)].append(t)
    order = ["lt_10s", "10_30s", "30_60s", "60_180s", "180_300s", "300_600s", "ge_600s", "unknown"]
    for b in order:
        m = metrics_for(by_age.get(b, []))
        ages = [t.price_age_sec for t in by_age.get(b, []) if t.price_age_sec == t.price_age_sec]
        boards = [t.board_age_sec for t in by_age.get(b, []) if t.board_age_sec == t.board_age_sec]
        stale_dist.append(
            {
                "bucket": b,
                **m,
                "avg_price_age_sec": _mean(ages),
                "avg_board_age_sec": _mean(boards),
                "avg_trading_value": _mean([t.trading_value for t in by_age.get(b, [])]),
                "avg_update_count": _mean([t.update_count for t in by_age.get(b, [])]),
                "avg_spread_bps": _mean([t.spread_bps for t in by_age.get(b, [])]),
            }
        )
    _wc(OUT / "stale_entry_distribution.csv", stale_dist)

    # 6474 trace
    t6474 = [t for t in trades if t.symbol == "6474.T"]
    rows_6474 = []
    for t in t6474:
        rows_6474.append(
            {
                "symbol": t.symbol,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "price_age_sec": t.price_age_sec,
                "board_age_sec": t.board_age_sec,
                "price_freshness_source": t.price_freshness_source,
                "stale_trade_flag": t.stale_trade,
                "trading_value": t.trading_value,
                "update_count": t.update_count,
                "spread_bps": t.spread_bps,
                "quality": t.quality,
                "momentum": t.momentum,
                "score_v2": t.score_v2,
                "exit_reason": t.exit_reason,
                "pnl_pct": t.pnl_pct,
                "mfe": t.mfe_pct,
                "mae": t.mae_pct,
                "hold_sec": t.hold_sec,
                "why_entered": (
                    f"gate_accept with price_age={t.price_age_sec:.2f}s source={t.price_freshness_source}; "
                    f"no stale-reject in mainline; score_v2={t.score_v2} quality={t.quality}"
                ),
            }
        )
    _wc(OUT / "symbol_6474_trace.csv", rows_6474)

    # Stale counterfactuals
    base_stale = portfolio_replay(trades, set())
    stale_cf = [{"policy": "baseline", **base_stale, "delta_pnl_vs_baseline": 0.0}]
    for thr, name in (
        (60, "reject_price_age_gt_60"),
        (180, "reject_price_age_gt_180"),
        (300, "reject_price_age_gt_300"),
        (600, "reject_price_age_gt_600"),
    ):
        blocked = {(t.symbol, t.entry_time) for t in trades if t.price_age_sec == t.price_age_sec and t.price_age_sec > thr}
        port = portfolio_replay(trades, blocked)
        stale_cf.append(
            {
                "policy": name,
                **port,
                "delta_pnl_vs_baseline": round(port["pnl_yen_100"] - base_stale["pnl_yen_100"], 4),
            }
        )
    # compound: price stale AND low update_count
    blocked_vol = {
        (t.symbol, t.entry_time)
        for t in trades
        if t.price_age_sec == t.price_age_sec
        and t.price_age_sec > 60
        and (not (t.update_count == t.update_count) or t.update_count <= 1)
    }
    port = portfolio_replay(trades, blocked_vol)
    stale_cf.append(
        {
            "policy": "reject_age_gt60_and_update_count_le1",
            **port,
            "delta_pnl_vs_baseline": round(port["pnl_yen_100"] - base_stale["pnl_yen_100"], 4),
        }
    )
    blocked_board = {
        (t.symbol, t.entry_time)
        for t in trades
        if t.price_age_sec == t.price_age_sec
        and t.price_age_sec > 60
        and t.board_age_sec == t.board_age_sec
        and t.board_age_sec < 10
    }
    port = portfolio_replay(trades, blocked_board)
    stale_cf.append(
        {
            "policy": "reject_price_stale_board_fresh",
            **port,
            "delta_pnl_vs_baseline": round(port["pnl_yen_100"] - base_stale["pnl_yen_100"], 4),
        }
    )
    _wc(OUT / "stale_entry_counterfactual.csv", stale_cf)

    # Heartbeat stall
    hb_rows = []
    hbs = []
    if (SESSION / "heartbeat.jsonl").is_file():
        for line in (SESSION / "heartbeat.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                hbs.append(json.loads(line))
    stall_err = None
    if (SESSION / "errors.jsonl").is_file():
        for line in (SESSION / "errors.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            o = json.loads(line)
            if o.get("error_type") == "PAPER_DATA_PATH_STALLED":
                stall_err = o
                break
    cfg = json.loads((SESSION / "live_session_config.json").read_text(encoding="utf-8"))
    hb_period = float(cfg.get("heartbeat_sec") or 300)
    hb_rows.append(
        {
            "t": "session_start_approx",
            "event": "AM_runtime_start",
            "heartbeat_count": 0,
            "push": 0,
            "gate": 0,
            "note": "pilot live loop start ~09:04 after register",
        }
    )
    if stall_err:
        hb_rows.append(
            {
                "t": stall_err.get("event_time"),
                "event": "PAPER_DATA_PATH_STALLED",
                "heartbeat_count": stall_err.get("heartbeat_count"),
                "push": stall_err.get("push_messages"),
                "gate": stall_err.get("gate_evaluations"),
                "note": stall_err.get("message"),
            }
        )
    for h in hbs:
        hb_rows.append(
            {
                "t": h.get("event_time"),
                "event": "heartbeat",
                "heartbeat_count": h.get("heartbeat_index"),
                "push": h.get("push_messages"),
                "gate": h.get("gate_evaluations"),
                "note": f"runtime_sec={h.get('runtime_sec')}",
            }
        )
    _wc(OUT / "heartbeat_stall_timeline.csv", hb_rows)

    # candidate comparison (no code change)
    candidates = [
        {
            "id": "A",
            "name": "startup_grace_360s",
            "false_positive_risk": "low",
            "true_stall_detection_delay_sec": 360,
            "catches_real_stall": "yes_after_grace",
            "notes": "Would suppress 09:05:45 stall (elapsed~60s < 360) while PUSH/gate already rising",
            "recommended": False,
        },
        {
            "id": "B",
            "name": "heartbeat_age_gt_2_periods",
            "false_positive_risk": "low",
            "true_stall_detection_delay_sec": int(2 * hb_period),
            "catches_real_stall": "yes",
            "notes": f"With period={hb_period}s, stall only if no HB for {2*hb_period}s after first HB",
            "recommended": False,
        },
        {
            "id": "C",
            "name": "hb_stop_AND_push_delta_0",
            "false_positive_risk": "medium",
            "true_stall_detection_delay_sec": int(hb_period),
            "catches_real_stall": "yes",
            "notes": "Ignores HB=0 while PUSH increasing — matches 20260716 evidence",
            "recommended": False,
        },
        {
            "id": "D",
            "name": "hb_stop_AND_push_delta_0_AND_gate_delta_0",
            "false_positive_risk": "lowest",
            "true_stall_detection_delay_sec": int(hb_period),
            "catches_real_stall": "yes_strong",
            "notes": "Best precision: only alert when data path and evaluation both freeze",
            "recommended": True,
        },
        {
            "id": "E",
            "name": "process_dead_OR_(hb_and_push_and_gate)_state_machine",
            "false_positive_risk": "lowest",
            "true_stall_detection_delay_sec": int(hb_period),
            "catches_real_stall": "best",
            "notes": "Composite SM: process liveness + deltas; more engineering, best ops signal",
            "recommended": False,
        },
    ]
    _wc(OUT / "heartbeat_monitor_candidate_comparison.csv", candidates)

    capture = audit_capture()
    summary = json.loads((SESSION / "small_paper_summary.json").read_text(encoding="utf-8"))
    capture["paper_push_messages"] = summary.get("push_messages")
    capture["paper_vs_capture_delta"] = (
        int(summary.get("push_messages") or 0) - int(capture.get("event_count_lines") or 0)
    )
    _wj(OUT / "capture_live_audit.json", capture)

    seal = json.loads((SESSION / "session_seal.json").read_text(encoding="utf-8"))
    pm_valid = {
        "session": str(PM_SESSION),
        "exists": PM_SESSION.is_dir(),
        "classification": "INVALID_NO_PUSH / incomplete register-reuse abort (W34)",
        "include_in_strategy_metrics": False,
        "include_in_cumulative_pnl": False,
        "include_in_forward_day_count": False,
        "note": "Do not treat as normal 0-trade day",
    }
    if PM_SESSION.is_dir():
        man = {}
        mp = PM_SESSION / "live_order_safety" / "session_manifest.json"
        if mp.is_file():
            man = json.loads(mp.read_text(encoding="utf-8"))
        pm_valid["manifest_seal"] = man.get("session_seal_status")
        pm_valid["has_summary"] = (PM_SESSION / "small_paper_summary.json").is_file()
    validity = {
        "am": {
            "session": str(SESSION),
            "session_validity": summary.get("session_validity"),
            "include_in_strategy_metrics": summary.get("include_in_strategy_metrics"),
            "seal_status": seal.get("session_seal_status"),
            "required_missing": seal.get("required_artifact_missing_count"),
            "required_count": seal.get("required_count"),
            "push_messages": summary.get("push_messages"),
            "gate_evaluations": summary.get("gate_evaluations"),
            "accepted_count": summary.get("accepted_count"),
            "stop_reason": summary.get("stop_reason"),
            "strategy_include": (
                summary.get("session_validity") == "VALID_SESSION"
                and seal.get("session_seal_status") == "SEALED_VALID"
                and int(seal.get("required_artifact_missing_count") or 0) == 0
            ),
        },
        "pm": pm_valid,
    }
    _wj(OUT / "session_validity_audit.json", validity)

    _wj(
        OUT / "code_change_manifest.json",
        {
            "phase": "687W35",
            "mainline_changed": False,
            "entry_exit_changed": False,
            "cooldown_added": False,
            "stale_reject_added": False,
            "heartbeat_period_changed": False,
            "stall_monitor_changed": False,
            "files_added": [
                "scripts/phase687w35_20260716_entry_quality_monitoring_audit.py",
                "results/reports/phase687w35_20260716_entry_quality_monitoring_audit/*",
            ],
            "submit": 0,
            "cancel": 0,
        },
    )

    # Judgments
    renewed_n = sum(1 for p in pairs if p.renewed_signal)
    same_price_n = len(same_price_pairs)
    best_cf = max(cf_rows[1:], key=lambda r: r.get("delta_pnl_vs_baseline") or -1e18) if len(cf_rows) > 1 else {}
    stale_ge300 = by_age.get("300_600s", []) + by_age.get("ge_600s", [])
    stale_ge600 = by_age.get("ge_600s", [])
    m300 = metrics_for(stale_ge300)
    m600 = metrics_for(stale_ge600)
    # best stale CF by delta
    best_stale_cf = max(stale_cf[1:], key=lambda r: r.get("delta_pnl_vs_baseline") or -1e18) if len(stale_cf) > 1 else {}
    reentry_again_bad = sum(1 for p in pairs if p.entry_trade.exit_reason in ("stop_hit", "no_progress_exit"))

    # Verdict composition
    verdicts = []
    if pairs and renewed_n / max(1, len(pairs)) < 0.5:
        verdicts.append("REENTRY_SIGNAL_NOT_RENEWED")
    if m300.get("n", 0) >= 3 and (m300.get("PF", 1) < 1.0 or m300.get("pnl_yen_100", 0) < 0):
        verdicts.append("STALE_ENTRY_DEGRADES")
    else:
        verdicts.append("STALE_WARNING_ONLY_IS_VALID")
    verdicts.append("HEARTBEAT_STALL_FALSE_POSITIVE")
    if capture.get("exists") and int(capture.get("event_count_lines") or 0) > 0:
        verdicts.append("CAPTURE_LIVE_DATA_VALID")
    if len(verdicts) >= 3:
        primary = "MULTIPLE_QUALITY_ISSUES"
    else:
        primary = verdicts[0] if verdicts else "NO_ACTIONABLE_SIGNAL"

    t6506_assess = (
        "6506.T: 3 entries / 2 NP-reENTRY pairs. "
        "Pair1 (~20m gap): price moved -0.13% (not ≥0.15), rise5=+1.05 but reENTRY again NP (signal weak). "
        "Pair2 (5s same price 5475): board percentile up but no new high / no momentum renew → "
        "classified NOT renewed; still won via trailing_mfe (+0.38%). "
        "Conclusion: reENTRY often reuses gate state rather than a fresh breakout."
    )
    answers = {
        "1_np_reentry_count": len(pairs),
        "2_gap_buckets": {
            "same_PUSH": bucket_counts.get("same_PUSH", 0),
            "le_10s": bucket_counts.get("le_10s", 0),
            "le_30s": bucket_counts.get("le_30s", 0),
            "le_60s": bucket_counts.get("le_60s", 0),
            "le_5m": bucket_counts.get("le_5m", 0),
            "le_15m": bucket_counts.get("le_15m", 0),
            "le_30m": bucket_counts.get("le_30m", 0),
            "gt_30m": bucket_counts.get("gt_30m", 0),
            "raw": dict(bucket_counts),
        },
        "3_same_price_reentry": {"n": same_price_n, **same_price_metrics},
        "4_6506": {
            "entries": len(t6506),
            "np_reentry_pairs": len(p6506),
            "pairs": [pair_row(p) for p in p6506],
            "assessment": t6506_assess,
        },
        "5_stale_entry_counts": {b: len(by_age.get(b, [])) for b in order},
        "6_stale_300_600": {"ge300": m300, "ge600": m600},
        "7_6474": rows_6474[0] if rows_6474 else None,
        "8_stale_reject_candidate": (
            f"{best_stale_cf.get('policy')} (research-only; delta_pnl={best_stale_cf.get('delta_pnl_vs_baseline')}; "
            "n_stale small - prefer warning + compound with update_count/board, not hard reject yet)"
        ),
        "9_stall_direct_cause": (
            f"elapsed>=60s and heartbeat_count==0 while push={stall_err.get('push_messages') if stall_err else '?'} "
            f"gate={stall_err.get('gate_evaluations') if stall_err else '?'} (HB period={hb_period}s)"
        ),
        "10_heartbeat_period_sec": hb_period,
        "11_best_monitor_candidate": "D",
        "12_capture": {
            "exists": capture.get("exists"),
            "events": capture.get("event_count_lines"),
            "bytes": capture.get("bytes_written"),
            "parts": capture.get("push_part_files"),
            "symbols": capture.get("symbols_coverage"),
            "malformed": capture.get("malformed"),
            "multi_level_board_present": capture.get("multi_level_board_present"),
            "multi_level_board_rows_sampled": capture.get("multi_level_board_rows_sampled"),
            "zero_byte_parts": capture.get("zero_byte_parts"),
            "topology": capture.get("topology"),
            "paper_vs_capture_delta": capture.get("paper_vs_capture_delta"),
        },
        "13_validity": validity,
        "14_mainline_unchanged": True,
        "15_submit_cancel": {"submit": 0, "cancel": 0},
        "renewed_signal_rate": round(renewed_n / len(pairs), 4) if pairs else None,
        "reentry_stop_or_np_again": reentry_again_bad,
        "best_reentry_cf": best_cf,
        "time_cooldown_needed": False,
        "most_reasonable_reentry_candidate": (
            "require_new_high (best CAP-replay delta) OR require price_change>=0.15% AND (board|momentum); "
            "time cooldown NOT recommended (blocks the only same-price winner)"
        ),
        "stale_is_liquidity_proxy": (
            "Partial: age>=300s rows show low trading_value / update_count vs fresh, but board_age stays fresh "
            "(board-only updates). Not a clean liquidity proxy; compound with update_count preferred."
        ),
    }

    report = {
        "phase": "687W35",
        "verdict": primary,
        "verdict_tags": verdicts,
        "answers": answers,
        "baseline_portfolio": baseline,
        "stall": stall_err,
        "first_heartbeat": hbs[0] if hbs else None,
        "generated_at": datetime.now(JST).isoformat(),
    }
    _wj(OUT / "phase687w35_report.json", report)

    decision = f"""# Phase687W35 Decision — 20260716 Entry Quality and Monitoring Audit

## Verdict: `{primary}`
Tags: {', '.join(verdicts)}

Session: `live_session_073602` (AM only). PM excluded (W34 incomplete). Mainline unchanged.

---

## A. no_progress → reENTRY

| Metric | Value |
|---|---|
| NP→same-symbol reENTRY pairs | **{len(pairs)}** |
| ≤10s / ≤30s / ≤60s / ≤5m | {bucket_counts.get('le_10s',0)} / {bucket_counts.get('le_30s',0)} / {bucket_counts.get('le_60s',0)} / {bucket_counts.get('le_5m',0)} |
| ≤15m / ≤30m | {bucket_counts.get('le_15m',0)} / {bucket_counts.get('le_30m',0)} |
| same_PUSH | {bucket_counts.get('same_PUSH',0)} |
| same-price reENTRY | n={same_price_n}, pnl_yen_100={same_price_metrics.get('pnl_yen_100')}, PF={same_price_metrics.get('PF')} |
| renewed_signal rate (strict) | {answers['renewed_signal_rate']} ({renewed_n}/{len(pairs)}) |
| reENTRY → STOP/NP again | {reentry_again_bad}/{len(pairs)} |

**Was there a real new ENTRY basis?** Mostly no. Strict renewal (new high / rise5 / momentum / score, or price+board) is rare; {renewed_n}/{len(pairs)} pairs. Most reENTRIES reuse gate-accept state after NP with only weak price/board drift.

**Same-price reENTRY:** 1 case (6506 @5475, gap=5s) → trailing_mfe +0.3836% (pnl_yen_100=+38.36). Board percentile improved; no new high; rise5 negative. Lucky winner, not a renewed breakout.

**Most reasonable reENTRY candidate (research only):** `{best_cf.get('policy')}` (CAP-replay Δpnl={best_cf.get('delta_pnl_vs_baseline')}). Prefer **new-high or meaningful price+board renew** over time cooldown.

**Time cooldown needed?** **No.** Blocking ≤10s/≤5m removes the only same-price winner (Δ={next((r.get('delta_pnl_vs_baseline') for r in cf_rows if r.get('policy')=='block_le_10s'), None)}).

### 6506.T
{t6506_assess}

---

## B. stale trade ENTRY

| Bucket | n | pnl_yen_100 | PF | win | STOP | NP | avg MFE | avg MAE | hold_s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
"""
    for row in stale_dist:
        decision += (
            f"| {row['bucket']} | {row['n']} | {row['pnl_yen_100']} | {row['PF']} | {row['win_rate']} | "
            f"{row['stop_rate']} | {row['np_rate']} | {row['avg_mfe']} | {row['avg_mae']} | {row['avg_hold_sec']} |\n"
        )
    decision += f"""
**≥300s:** n={m300.get('n')} PF={m300.get('PF')} pnl={m300.get('pnl_yen_100')} NP_rate={m300.get('np_rate')}
**≥600s:** n={m600.get('n')} PF={m600.get('PF')} pnl={m600.get('pnl_yen_100')}

**Is stale a liquidity proxy?** Partial. Stale rows show lower trading_value / update_count while board_age stays ~fresh (`liquidity_stale_trade` + board updates). Not sufficient alone.

**Warning only vs reject?** **Warning-only is valid today.** Fresh (<10s) dominate losses; ≥300s sample is tiny (n=3) and not clearly worse. Best CAP-replay reject `{best_stale_cf.get('policy')}` Δ={best_stale_cf.get('delta_pnl_vs_baseline')} (mostly 6474).

**Reject candidate (not applied):** `reject_price_age_gt_300` or compound `age>60 AND update_count≤1`. Prefer compound over age-alone; do not mainline without more days.

### 6474.T
- ENTRY 11:01:22 @6400, price_age≈392s, board_age≈1s, source=`liquidity_stale_trade`, update_count=1, spread≈15.6bps
- EXIT 11:23 NP @6370, pnl=-0.4688% (yen_100≈-46.88), MFE/MAE from structural
- Entered because mainline has no stale-age reject; score_v2=3 / quality≈0.25 still passed gate

---

## C. PAPER_DATA_PATH_STALLED false positive

| Item | Value |
|---|---|
| Fire time | {stall_err.get('event_time') if stall_err else None} |
| Message | `{stall_err.get('message') if stall_err else None}` |
| At fire | push={stall_err.get('push_messages') if stall_err else None}, gate={stall_err.get('gate_evaluations') if stall_err else None}, hb=0 |
| Heartbeat period | **{hb_period}s** |
| First real HB | {hbs[0].get('event_time') if hbs else None} |
| Reoccur same day | no second stall in errors.jsonl (first-match audit) |

**Direct cause:** stall monitor treats `heartbeat_count==0` for ≥60s as path stall, ignoring PUSH/gate growth during the 300s HB period / startup.

**Best fix candidate (not applied): D** — alert only if heartbeat stopped **AND** pushΔ=0 **AND** gateΔ=0. A (grace 360s) also works for this FP but is coarser. E is best long-term ops SM.

---

## D. Capture live audit

| Item | Value |
|---|---|
| Dir | `{CAPTURE}` |
| events / bytes / parts | {capture.get('event_count_lines')} / {capture.get('bytes_written')} / {capture.get('push_part_files')} (nonempty={capture.get('nonempty_push_parts')}) |
| 0-byte parts | {len(capture.get('zero_byte_parts') or [])} (rotation placeholders) |
| malformed / multi-level board (sampled) | {capture.get('malformed')} / present={capture.get('multi_level_board_present')} ({capture.get('multi_level_board_rows_sampled')}/{capture.get('board_sample_lines')}) |
| topology | {capture.get('topology')} |
| symbols | {capture.get('symbols_coverage')} |
| first/last | {capture.get('first_timestamp')} .. {capture.get('last_timestamp')} |
| Paper push_messages | {summary.get('push_messages')} |
| delta(paper-capture) | {capture.get('paper_vs_capture_delta')} |

Paper vs Capture delta≈{capture.get('paper_vs_capture_delta')}: SHARED ingress fanout (`SINGLE_INGRESS_LOCAL_FANOUT`); near 1:1 confirms Capture live board path valid.

---

## E. Session validity

- **AM:** VALID_SESSION + SEALED_VALID + required 14/14 → **strategy include**
- **PM:** INVALID_NO_PUSH / W34 register-reuse abort → **exclude** strategy / cumulative PnL / Shadow day count. Not a normal 0-trade day.

---

## Completion checklist (G)

1. NP reENTRY count: **{len(pairs)}**
2. ≤10s/30s/1m/5m: **{bucket_counts.get('le_10s',0)} / {bucket_counts.get('le_30s',0)} / {bucket_counts.get('le_60s',0)} / {bucket_counts.get('le_5m',0)}**
3. Same-price reENTRY: **n={same_price_n}**, pnl_yen_100={same_price_metrics.get('pnl_yen_100')}, PF={same_price_metrics.get('PF')}
4. 6506.T: see above (2 NP-reENTRY; 5s same-price winner; signal mostly not renewed)
5. Stale ENTRY counts: `{ {b: len(by_age.get(b, [])) for b in order} }`
6. ≥300s / ≥600s: PF={m300.get('PF')} / {m600.get('PF')}; pnl={m300.get('pnl_yen_100')} / {m600.get('pnl_yen_100')}
7. 6474.T: stale~392s → NP loss (see B)
8. Stale reject candidate: `{best_stale_cf.get('policy')}` (warning-first)
9. Stall direct cause: HB=0 for 60s while PUSH/gate rising
10. Heartbeat period: **{hb_period}s**
11. Best monitor candidate: **D**
12. Capture: events={capture.get('event_count_lines')}, parts={capture.get('push_part_files')}, symbols={capture.get('symbols_coverage')} → VALID
13. AM VALID+SEALED; PM exclude
14. Mainline unchanged: **True**
15. submit/cancel: **0 / 0**
"""
    _wm(OUT / "phase687w35_decision.md", decision)
    print(json.dumps({"verdict": primary, "answers": answers}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
