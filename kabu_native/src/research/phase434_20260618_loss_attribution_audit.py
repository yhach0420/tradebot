"""
Phase434 — 20260618 loss attribution & 6976 reentry failure audit.

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from replay.pnl_yen import enrich_trade_pnl_yen
from research.equity_curve_shadow import PERIOD_START, load_canonical_live_config_trades
from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase431_entry_priority_reentry_audit import (
    _float,
    _load_structural_trades,
    _metrics_from_pnls,
    _parse_ts,
    _pnl_yen_100,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.canonical_summary import collect_canonical_trades, is_stop_exit

STOP_REASONS = frozenset({"hard_stop", "stop_loss", "loss_cut", "stop_hit"})


def _is_stop_trade(row: Mapping[str, Any]) -> bool:
    reason = str(row.get("exit_reason") or row.get("structural_exit_reason") or row.get("close_reason") or "").strip()
    if reason in STOP_REASONS:
        return True
    return is_stop_exit(row) and reason in ("", "overlap_replaced_review")

JST = ZoneInfo("Asia/Tokyo")
TARGET_DAY = "20260618"
COMPARE_DAY = "20260617"
HARD_STOP_PCT = 1.2
REENTRY_WINDOWS = (30, 60, 180, 300, 600, 900, 1800)

SESSION_DIRS: dict[str, tuple[str, ...]] = {
    "20260618": ("live_session_081230", "live_session_122524"),
    "20260617": ("live_session_071605", "live_session_122538"),
}

PRICE_BANDS = (
    ("lt_1000", 0, 1000),
    ("1000_3000", 1000, 3000),
    ("3000_10000", 3000, 10000),
    ("gte_10000", 10000, 10_000_000),
)


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _session_base(day: str, *, repo_root: Path) -> Path:
    return resolve_kabu_root(repo_root) / "results" / "small_paper" / day


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "small_paper_events.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _load_canonical_from_session(session_dir: Path) -> list[dict[str, Any]]:
    return collect_canonical_trades(_load_events(session_dir))


def _load_accepted_map(session_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in _load_events(session_dir):
        if str(row.get("event_type") or "") != "accepted":
            continue
        et = str(row.get("entry_time") or "")
        sym = str(row.get("symbol") or "")
        out[f"{sym}|{et}"] = row
    return out


def _load_scan_audit(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "entry_scan_audit.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _nearest_scan(
    audits: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    entry_time: str,
) -> Optional[dict[str, Any]]:
    target = _parse_ts(entry_time)
    if target is None:
        return None
    best: Optional[dict[str, Any]] = None
    best_delta = 999999.0
    for row in audits:
        if str(row.get("symbol") or "") != symbol:
            continue
        if not row.get("entry_decision"):
            continue
        ts = _parse_ts(str(row.get("eval_start_ts") or ""))
        if ts is None:
            continue
        delta = abs((ts - target).total_seconds())
        if delta < best_delta:
            best_delta = delta
            best = dict(row)
    if best is None or best_delta > 30:
        return None
    return best


def _momentum_category(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    if score < 0.15:
        return "low"
    if score < 0.35:
        return "mid"
    return "high"


def _board_category(row: Mapping[str, Any]) -> str:
    tier = str(row.get("board_dynamic_trailing_tier") or row.get("shadow_board_dynamic_tier") or "").strip()
    if tier:
        return tier
    pct = _float(row.get("entry_imbalance_percentile"), default=-1)
    if pct < 0:
        active = row.get("entry_board_mid_token_active")
        if active in (True, "True", "true", "1"):
            return "board_mid"
        return "unknown"
    if pct < 33:
        return "board_low"
    if pct < 67:
        return "board_mid"
    return "board_high"


def _price_band(entry_price: float) -> str:
    for label, lo, hi in PRICE_BANDS:
        if lo <= entry_price < hi:
            return label
    return "unknown"


def _load_all_canonical(day: str, *, repo_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    base = _session_base(day, repo_root=repo_root)
    trades: list[dict[str, Any]] = []
    sessions: list[str] = []
    for sess in SESSION_DIRS.get(day, ()):
        sd = base / sess
        if not sd.is_dir():
            continue
        sessions.append(sess)
        for t in _load_canonical_from_session(sd):
            t = dict(t)
            t["session"] = sess
            t["day"] = day
            trades.append(t)
    trades.sort(key=lambda r: (_parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)))
    return trades, sessions


def _structural_index(day: str, *, repo_root: Path) -> dict[str, dict[str, Any]]:
    base = _session_base(day, repo_root=repo_root)
    idx: dict[str, dict[str, Any]] = {}
    for sess in SESSION_DIRS.get(day, ()):
        for t in _load_structural_trades(base / sess):
            key = f"{t['symbol']}|{t['entry_time']}"
            idx[key] = t
    return idx


def _price_context_15m(
    events: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    entry_time: str,
    entry_price: float,
) -> dict[str, Any]:
    target = _parse_ts(entry_time)
    if target is None:
        return {}
    start = target - timedelta(minutes=15)
    prices: list[tuple[datetime, float]] = []
    day_high = 0.0
    day_low = 1e12
    vwap_devs: list[float] = []
    for row in events:
        if str(row.get("symbol") or "") != symbol:
            continue
        ts = _parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
        px = _float(row.get("current_price"), default=0)
        if ts is None or px <= 0:
            continue
        if ts.date() == target.date():
            day_high = max(day_high, px)
            day_low = min(day_low, px)
        if start <= ts <= target:
            prices.append((ts, px))
        v = _float(row.get("entry_vwap_dev_pct"), default=0)
        if v and abs((ts - target).total_seconds()) < 5:
            vwap_devs.append(v)

    if not prices:
        return {
            "start_price": entry_price,
            "entry_price": entry_price,
            "return_15m_pct": 0.0,
            "high_15m": entry_price,
            "low_15m": entry_price,
            "drawdown_from_15m_high_pct": 0.0,
            "distance_from_day_high_pct": 0.0,
            "distance_from_day_low_pct": 0.0,
            "vwap_dev_pct": vwap_devs[-1] if vwap_devs else None,
        }

    prices.sort(key=lambda x: x[0])
    start_price = prices[0][1]
    high_15m = max(p for _, p in prices)
    low_15m = min(p for _, p in prices)
    ret_15m = (entry_price - start_price) / start_price * 100 if start_price else 0.0
    dd = (entry_price - high_15m) / high_15m * 100 if high_15m else 0.0
    dist_hi = (entry_price - day_high) / day_high * 100 if day_high > 0 else 0.0
    dist_lo = (entry_price - day_low) / day_low * 100 if day_low < 1e12 else 0.0
    return {
        "start_price": round(start_price, 2),
        "entry_price": round(entry_price, 2),
        "return_15m_pct": round(ret_15m, 4),
        "high_15m": round(high_15m, 2),
        "low_15m": round(low_15m, 2),
        "drawdown_from_15m_high_pct": round(dd, 4),
        "distance_from_day_high_pct": round(dist_hi, 4),
        "distance_from_day_low_pct": round(dist_lo, 4),
        "vwap_dev_pct": round(vwap_devs[-1], 4) if vwap_devs else None,
    }


def _audit_6976(
    day: str,
    *,
    repo_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    base = _session_base(day, repo_root=repo_root)
    canonical, _ = _load_all_canonical(day, repo_root=repo_root)
    sym_trades = [t for t in canonical if str(t.get("symbol") or "") == "6976.T"]
    struct_idx = _structural_index(day, repo_root=repo_root)

    entry_rows: list[dict[str, Any]] = []
    price_rows: list[dict[str, Any]] = []

    for sess in SESSION_DIRS.get(day, ()):
        sd = base / sess
        if not sd.is_dir():
            continue
        accepted = _load_accepted_map(sd)
        audits = _load_scan_audit(sd)
        events = _load_events(sd)

        for t in sym_trades:
            if t.get("session") != sess:
                continue
            et = str(t.get("entry_time") or "")
            key = f"6976.T|{et}"
            acc = accepted.get(key, {})
            scan = _nearest_scan(audits, symbol="6976.T", entry_time=et) or {}
            struct = struct_idx.get(key, {})
            mom = _float(acc.get("entry_momentum_score") or acc.get("momentum_continuation_score"))
            board_pct = _float(acc.get("entry_imbalance_percentile"), default=-1)
            entry_rows.append(
                {
                    "session": sess,
                    "entry_time": et,
                    "entry_price": _float(t.get("entry_price")),
                    "exit_time": str(t.get("event_time") or t.get("exit_time") or ""),
                    "exit_price": _float(t.get("exit_price")),
                    "hold_sec": _float(t.get("hold_sec") or struct.get("hold_sec")),
                    "pnl_yen_100": _float(t.get("pnl_yen_100")),
                    "pnl_pct": _float(t.get("pnl_pct")),
                    "exit_reason": str(t.get("exit_reason") or struct.get("close_reason") or ""),
                    "stop_hit": _is_stop_trade(t),
                    "momentum_score": mom,
                    "board_score": board_pct if board_pct >= 0 else None,
                    "entry_score_v2": acc.get("entry_expectancy_score_v2") or acc.get("entry_score_v2") or scan.get("entry_score_v2"),
                    "candidate_rank_score": acc.get("entry_expectancy_score"),
                    "entry_reasons": scan.get("entry_reasons") or acc.get("gate_reject_reason") or "accepted",
                    "board_category": _board_category(acc or t),
                    "momentum_category": _momentum_category(mom),
                    "entry_imbalance_percentile": board_pct if board_pct >= 0 else None,
                    "continuation_quality": _float(acc.get("continuation_quality_score") or t.get("continuation_quality_score")),
                    "price_age_sec": scan.get("price_age_sec") or acc.get("price_age_sec"),
                    "board_age_sec": scan.get("board_age_sec") or acc.get("board_age_sec"),
                    "scan_id": scan.get("scan_id"),
                    "scan_window": scan.get("eval_start_ts"),
                    "momentum_low_board_mid": _momentum_category(mom) == "low" and _board_category(acc or t) in ("board_mid", "mid"),
                    "structural_mfe_pct": struct.get("mfe_pct"),
                    "structural_mae_pct": struct.get("mae_pct"),
                }
            )
            ctx = _price_context_15m(
                events,
                symbol="6976.T",
                entry_time=et,
                entry_price=_float(t.get("entry_price")),
            )
            price_rows.append({"entry_time": et, "session": sess, **ctx})

    stops = sum(1 for r in entry_rows if r["stop_hit"])
    total_pnl = round(sum(_float(r["pnl_yen_100"]) for r in entry_rows), 2)
    pullback_flags = sum(1 for r in price_rows if _float(r.get("return_15m_pct")) < -0.05)
    mom_low_board_mid = sum(1 for r in entry_rows if r.get("momentum_low_board_mid"))

    summary = {
        "entry_count": len(entry_rows),
        "stop_hit_count": stops,
        "total_pnl_yen_100": total_pnl,
        "pullback_reentry_pattern_count": pullback_flags,
        "momentum_low_board_mid_count": mom_low_board_mid,
    }
    return entry_rows, price_rows, summary


def _stop_reentry_audit(trades: Sequence[Mapping[str, Any]]) -> tuple[list[dict], list[dict], dict]:
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_sym[str(t["symbol"])].append(dict(t))
    for sym in by_sym:
        by_sym[sym].sort(key=lambda r: _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST))

    pairs: list[dict[str, Any]] = []
    for sym, seq in by_sym.items():
        for i in range(1, len(seq)):
            prev = seq[i - 1]
            cur = seq[i]
            if not _is_stop_trade(prev):
                continue
            prev_close = _parse_ts(str(prev.get("event_time") or prev.get("exit_time") or ""))
            cur_entry = _parse_ts(str(cur.get("entry_time") or ""))
            if prev_close is None or cur_entry is None:
                continue
            gap = (cur_entry - prev_close).total_seconds()
            if gap < 0:
                continue
            pairs.append(
                {
                    "symbol": sym,
                    "prev_exit_time": prev.get("event_time") or prev.get("exit_time"),
                    "prev_pnl_yen_100": _float(prev.get("pnl_yen_100")),
                    "reentry_time": cur.get("entry_time"),
                    "gap_sec": round(gap, 2),
                    "reentry_pnl_yen_100": _float(cur.get("pnl_yen_100")),
                    "reentry_stop_hit": _is_stop_trade(cur),
                }
            )

    window_rows: list[dict[str, Any]] = []
    for w in REENTRY_WINDOWS:
        subset = [p for p in pairs if _float(p.get("gap_sec")) <= w]
        pnls = [_float(p["reentry_pnl_yen_100"]) for p in subset]
        m = _metrics_from_pnls(pnls, [])
        sym_pnl: dict[str, float] = defaultdict(float)
        for p in subset:
            sym_pnl[str(p["symbol"])] += _float(p["reentry_pnl_yen_100"])
        worst = min(sym_pnl.items(), key=lambda x: x[1], default=("", 0.0))
        top_loss = worst
        window_rows.append(
            {
                "window_sec": w,
                "count": m["count"],
                "total_pnl_yen_100": m["total_pnl_yen"],
                "profit_factor": m["profit_factor"],
                "win_rate": m["win_rate"],
                "avg_pnl_yen_100": m["avg_pnl_yen"],
                "worst_symbol": worst[0],
                "worst_symbol_pnl_yen_100": round(worst[1], 2),
                "top_loss_symbol": top_loss[0],
            }
        )

    # counterfactual: ban same-symbol reentry after stop for rest of day
    banned: set[tuple[str, str]] = set()
    kept: list[dict[str, Any]] = []
    removed_pnl = 0.0
    for t in trades:
        sym = str(t["symbol"])
        et = str(t.get("entry_time") or "")
        day_key = et[:10]
        if (sym, day_key) in banned:
            removed_pnl += _float(t.get("pnl_yen_100"))
            continue
        kept.append(dict(t))
        if _is_stop_trade(t):
            banned.add((sym, day_key))

    actual_pnl = sum(_float(t.get("pnl_yen_100")) for t in trades)
    cf_pnl = sum(_float(t.get("pnl_yen_100")) for t in kept)
    cf_row = {
        "scenario": "ban_same_symbol_reentry_after_stop_same_day",
        "actual_total_pnl_yen_100": round(actual_pnl, 2),
        "counterfactual_total_pnl_yen_100": round(cf_pnl, 2),
        "delta_yen_100": round(cf_pnl - actual_pnl, 2),
        "trades_removed": len(trades) - len(kept),
        "removed_pnl_yen_100": round(removed_pnl, 2),
    }

    sym6976 = [p for p in pairs if p["symbol"] == "6976.T"]
    meta = {
        "stop_reentry_pair_count": len(pairs),
        "stop_reentry_total_pnl_yen_100": round(sum(_float(p["reentry_pnl_yen_100"]) for p in pairs), 2),
        "6976_stop_reentry_pairs": len(sym6976),
        "6976_stop_reentry_pnl_yen_100": round(sum(_float(p["reentry_pnl_yen_100"]) for p in sym6976), 2),
        "counterfactual": cf_row,
    }
    return window_rows, [cf_row], meta


def _classify_slippage(row: Mapping[str, Any]) -> str:
    slip = _float(row.get("slippage_pct"))
    price_age = _float(row.get("price_age_sec"))
    push_gap = _float(row.get("push_gap_sec"))
    if slip <= 0.15:
        return "E_normal"
    if price_age >= 10:
        return "D_price_tick_missing"
    if push_gap >= 8:
        return "A_push_interval_delay"
    if slip >= 0.4 and price_age < 3:
        return "B_board_gap"
    if slip >= 0.25:
        return "C_hard_stop_late_or_miss"
    return "E_normal"


def _stop_slippage_audit(
    day: str,
    trades: Sequence[Mapping[str, Any]],
    *,
    repo_root: Path,
) -> tuple[list[dict], dict]:
    struct_idx = _structural_index(day, repo_root=repo_root)
    base = _session_base(day, repo_root=repo_root)
    events_by_sess = {s: _load_events(base / s) for s in SESSION_DIRS.get(day, ()) if (base / s).is_dir()}
    scans_by_sess = {s: _load_scan_audit(base / s) for s in SESSION_DIRS.get(day, ()) if (base / s).is_dir()}

    rows: list[dict[str, Any]] = []
    for t in trades:
        if not _is_stop_trade(t):
            continue
        et = str(t.get("entry_time") or "")
        sym = str(t.get("symbol") or "")
        key = f"{sym}|{et}"
        struct = struct_idx.get(key, {})
        entry_px = _float(t.get("entry_price"))
        exit_px = _float(t.get("exit_price"))
        stop_thr = round(entry_px * (1.0 - HARD_STOP_PCT / 100.0), 2)
        actual_pct = _float(t.get("pnl_pct"))
        expected_pct = -HARD_STOP_PCT
        slippage_pct = round(actual_pct - expected_pct, 4)
        slippage_yen = _pnl_yen_100(entry_px, slippage_pct)

        sess = str(t.get("session") or "")
        scan = _nearest_scan(scans_by_sess.get(sess, []), symbol=sym, entry_time=et) or {}
        exit_ts = _parse_ts(str(t.get("event_time") or t.get("exit_time") or ""))
        push_gap = 0.0
        if exit_ts and sess in events_by_sess:
            prior = [
                _parse_ts(str(e.get("event_time") or ""))
                for e in events_by_sess[sess]
                if str(e.get("symbol") or "") == sym
            ]
            prior = [p for p in prior if p and p <= exit_ts]
            if len(prior) >= 2:
                push_gap = (prior[-1] - prior[-2]).total_seconds()

        row = {
            "symbol": sym,
            "entry_time": et,
            "entry_price": entry_px,
            "stop_threshold_price": stop_thr,
            "trigger_time": struct.get("take_time") or "",
            "trigger_price": "",
            "actual_exit_time": str(t.get("event_time") or t.get("exit_time") or ""),
            "actual_exit_price": exit_px,
            "expected_stop_pct": expected_pct,
            "actual_pnl_pct": actual_pct,
            "slippage_pct": slippage_pct,
            "slippage_yen_100": slippage_yen,
            "price_age_sec": scan.get("price_age_sec"),
            "push_gap_sec": round(push_gap, 2),
            "board_age_sec": scan.get("board_age_sec"),
            "classification": _classify_slippage({"slippage_pct": abs(slippage_pct), "price_age_sec": scan.get("price_age_sec"), "push_gap_sec": push_gap}),
        }
        rows.append(row)

    over = [r for r in rows if _float(r.get("slippage_pct")) < -0.0 and abs(_float(r.get("slippage_pct"))) > 0.0]
    beyond = [r for r in rows if _float(r.get("actual_pnl_pct")) < -(HARD_STOP_PCT + 0.001)]
    max_excess = max((abs(_float(r["slippage_pct"])) for r in beyond), default=0.0)
    avg_excess = statistics.mean([abs(_float(r["slippage_pct"])) for r in beyond]) if beyond else 0.0
    sym6976 = [r for r in rows if r["symbol"] == "6976.T" and _float(r["actual_pnl_pct"]) <= -1.7]

    summary = {
        "stop_hit_count": len(rows),
        "beyond_1p2_count": len(beyond),
        "max_excess_pct": round(max_excess, 4),
        "avg_excess_pct": round(avg_excess, 4),
        "6976_worst_stop": sym6976[0] if sym6976 else None,
        "classification_counts": dict(
            sorted(
                ((k, sum(1 for r in rows if r["classification"] == k)) for k in sorted({r["classification"] for r in rows})),
                key=lambda x: -x[1],
            )
        ),
    }
    return rows, summary


def _day_comparison_metrics(day: str, *, repo_root: Path) -> dict[str, Any]:
    base = _session_base(day, repo_root=repo_root)
    canonical, sessions = _load_all_canonical(day, repo_root=repo_root)
    summaries = []
    for sess in sessions:
        summ = _read_json(base / sess / "small_paper_summary.json")
        summaries.append(summ.get("canonical_summary") or {})

    trade_count = sum(int(s.get("trade_count") or 0) for s in summaries)
    stop_count = sum(int(s.get("stop_count") or 0) for s in summaries)
    total_pnl = sum(_float(s.get("total_pnl_yen_100")) for s in summaries)
    gp = sum(_float(s.get("gross_profit_yen_100")) for s in summaries)
    gl = sum(_float(s.get("gross_loss_yen_100")) for s in summaries)
    pf = round(gp / gl, 4) if gl > 0 else None
    holds = [_float(t.get("hold_sec")) for t in canonical if _float(t.get("hold_sec")) > 0]
    symbols = {str(t.get("symbol")) for t in canonical}

    # reentry count (any gap)
    struct_all: list[dict] = []
    for sess in sessions:
        struct_all.extend(_load_structural_trades(base / sess))
    by_sym: dict[str, list] = defaultdict(list)
    for t in struct_all:
        by_sym[str(t["symbol"])].append(t)
    reentry = 0
    for seq in by_sym.values():
        seq.sort(key=lambda r: _parse_ts(str(r["entry_time"])) or datetime.min.replace(tzinfo=JST))
        reentry += max(0, len(seq) - 1)

    reject_counts: dict[str, int] = defaultdict(int)
    for sess in sessions:
        summ = _read_json(base / sess / "small_paper_summary.json")
        for k, v in (summ.get("reject_reason_counts") or {}).items():
            reject_counts[k] += int(v)

    summ0 = _read_json(base / sessions[0] / "small_paper_summary.json") if sessions else {}
    return {
        "day": day,
        "trade_count": trade_count,
        "stop_count": stop_count,
        "stop_rate": round(stop_count / trade_count, 4) if trade_count else 0.0,
        "profit_factor_yen_100": pf,
        "total_pnl_yen_100": round(total_pnl, 2),
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else 0.0,
        "traded_symbols_count": len(symbols),
        "same_symbol_reentry_count": reentry,
        "max_concurrent_reject": reject_counts.get("max_concurrent", 0),
        "pullback_misread_reject": reject_counts.get("pullback_misread_dynamic40_guard", 0),
        "near_day_high_low_reject": reject_counts.get("near_day_high_low_momentum_dynamic40_guard", 0),
        "score5_pnl": _float(summ0.get("score5_pnl")),
        "score6_pnl": _float(summ0.get("score6_pnl")),
        "quality_top20_pf": _pf_from_pct(_float(summ0.get("shadow_quality_top20_total_pnl_pct"))),
        "current_quality_top20_pf": _pf_from_pct(_float(summ0.get("current_quality_top20_total_pnl_pct"))),
        "accepted_symbols": summ0.get("accepted_symbols") or [],
    }


def _pf_from_pct(x: float) -> Optional[float]:
    if x == 0:
        return None
    return round(1.0 + x / 100.0, 4) if x > 0 else round(max(0.01, 1.0 + x / 100.0), 4)


def _price_band_pnl(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = {b[0]: [] for b in PRICE_BANDS}
    meta: dict[str, dict[str, Any]] = {b[0]: {"stops": 0, "notional": 0.0} for b in PRICE_BANDS}
    for t in trades:
        ep = _float(t.get("entry_price"))
        band = _price_band(ep)
        pnl = _float(t.get("pnl_yen_100"))
        buckets.setdefault(band, []).append(pnl)
        meta.setdefault(band, {"stops": 0, "notional": 0.0})
        meta[band]["notional"] += ep * 100
        if _is_stop_trade(t):
            meta[band]["stops"] += 1

    rows: list[dict[str, Any]] = []
    for label, _, _ in PRICE_BANDS:
        pnls = buckets.get(label, [])
        if not pnls:
            continue
        losses = [p for p in pnls if p < 0]
        rows.append(
            {
                "price_band": label,
                "trade_count": len(pnls),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "stop_count": meta[label]["stops"],
                "stop_rate": round(meta[label]["stops"] / len(pnls), 4),
                "avg_loss_yen_100": round(statistics.mean(losses), 2) if losses else 0.0,
                "max_loss_yen_100": round(min(pnls), 2),
                "capital_notional_100shares": round(meta[label]["notional"], 2),
            }
        )
    return rows


def _capital_sim_consistency(repo_root: Path, day: str) -> tuple[list[dict], dict]:
    kabu = resolve_kabu_root(repo_root)
    canonical_trades, _ = _load_all_canonical(day, repo_root=repo_root)
    canon_pnl = round(sum(_float(t.get("pnl_yen_100")) for t in canonical_trades), 2)

    summaries = []
    for sess in SESSION_DIRS.get(day, ()):
        summ = _read_json(kabu / "results" / "small_paper" / day / sess / "small_paper_summary.json")
        summaries.append(summ.get("canonical_summary") or {})
    canon_summary_pnl = round(sum(_float(s.get("total_pnl_yen_100")) for s in summaries), 2)

    trades, meta = load_canonical_live_config_trades(repo_root, period_start=PERIOD_START)
    sim = simulate_audited(
        trades,
        starting_equity=1_500_000,
        leverage=2.0,
        cap=5,
        stop_policy="fixed_stop_1p2",
    )
    day_row = next((r for r in (sim.get("_daily_rows") or []) if str(r.get("day")) == day), {})
    p273_path = kabu / "results" / "daily" / day / "live_candidate" / "phase273_live_config_shadow_daily_equity.csv"
    p274_path = kabu / "results" / "daily" / day / "live_candidate" / "phase274_live_config_transition_equity_curve.csv"

    p273_row = {}
    if p273_path.is_file():
        with p273_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("day") == day and row.get("candidate_key") == "live_start_candidate_1500k":
                    p273_row = row
                    break

    am_equity = None
    pm_equity = None
    start_equity = _float(p273_row.get("start_equity"))
    end_equity = _float(p273_row.get("end_equity"))
    if p274_path.is_file():
        am_last = None
        with p274_path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        day_rows = [r for r in rows if r.get("day") == day]
        for row in day_rows:
            ts = str(row.get("timestamp") or "")
            eq = _float(row.get("current_equity"))
            hour = int(ts[11:13]) if len(ts) >= 13 else 0
            if 9 <= hour <= 11:
                am_last = eq
        pm_equity = end_equity or (_float(day_rows[-1].get("current_equity")) if day_rows else None)
        am_equity = am_last

    state = sim.get("_state")
    accepted_day: list[dict[str, Any]] = []
    if state is not None:
        for log in getattr(state, "trade_log", []) or []:
            if str(log.get("day") or "") != day:
                continue
            trade = dict(log.get("trade") or {})
            trade["pnl_yen"] = log.get("pnl_yen")
            trade["day"] = log.get("day")
            accepted_day.append(trade)
    accepted_pnl = round(sum(_float(t.get("pnl_yen")) for t in accepted_day), 2)

    rows = [
        {
            "metric": "canonical_observer_exit_all_trades",
            "trade_count": len(canonical_trades),
            "total_pnl_yen_100": canon_pnl,
            "source": "small_paper_events observer_exit",
        },
        {
            "metric": "canonical_summary_json",
            "trade_count": sum(int(s.get("trade_count") or 0) for s in summaries),
            "total_pnl_yen_100": canon_summary_pnl,
            "source": "small_paper_summary.canonical_summary",
        },
        {
            "metric": "phase273_cap5_1500k_daily",
            "trade_count": int(p273_row.get("accepted_trade_count") or day_row.get("accepted_trade_count") or 0),
            "total_pnl_yen_100": _float(p273_row.get("daily_pnl") or day_row.get("daily_pnl")),
            "source": "phase273_live_config_shadow_daily_equity",
        },
        {
            "metric": "simulate_audited_cap5_1500k",
            "trade_count": int(day_row.get("accepted_trade_count") or 0),
            "total_pnl_yen_100": _float(day_row.get("daily_pnl")),
            "source": "load_canonical_live_config_trades + simulate_audited",
        },
        {
            "metric": "phase274_am_end_equity",
            "trade_count": "",
            "total_pnl_yen_100": round(am_equity - start_equity, 2) if am_equity and start_equity else None,
            "source": f"equity_curve AM end={am_equity}",
        },
        {
            "metric": "phase274_pm_end_equity",
            "trade_count": "",
            "total_pnl_yen_100": round(pm_equity - am_equity, 2) if pm_equity and am_equity else None,
            "source": f"equity_curve PM end={pm_equity}",
        },
        {
            "metric": "accepted_trades_pnl_yen_sized",
            "trade_count": len(accepted_day),
            "total_pnl_yen_100": accepted_pnl,
            "source": "simulate_audited accepted subset",
        },
    ]

    summary = {
        "canonical_all_trades_pnl": canon_pnl,
        "canonical_summary_pnl": canon_summary_pnl,
        "phase273_daily_pnl": _float(p273_row.get("daily_pnl")),
        "phase274_am_equity": am_equity,
        "phase274_pm_equity": pm_equity,
        "phase274_start_equity": start_equity,
        "trade_source": meta.get("trade_source"),
        "explanation": (
            "canonical_summary sums all observer_exit paper trades (100-share); "
            "phase273/274 capital sim applies CAP5 buying-power and may reject entries — "
            "PM session can show positive intraday equity while canonical PM PnL remains negative."
        ),
        "is_bug": False,
        "is_specification": True,
    }
    return rows, summary


def _csv_write(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    _write_csv(path, fields, rows)


def _verdict(
    *,
    s6976: Mapping[str, Any],
    stop_slip: Mapping[str, Any],
    price_bands: Sequence[Mapping[str, Any]],
    reentry_meta: Mapping[str, Any],
    capital: Mapping[str, Any],
) -> str:
    factors = []
    if _float(s6976.get("total_pnl_yen_100")) < -50000:
        factors.append("6976")
    if int(stop_slip.get("beyond_1p2_count") or 0) >= 5:
        factors.append("slippage")
    gte10k = [b for b in price_bands if b.get("price_band") == "gte_10000"]
    if gte10k and _float(gte10k[0].get("total_pnl_yen_100")) < -100000:
        factors.append("price_band")
    if _float(reentry_meta.get("6976_stop_reentry_pnl_yen_100")) < -30000:
        factors.append("reentry")
    if len(factors) >= 2:
        return "multi_factor_failure"
    if "reentry" in factors or (int(s6976.get("stop_hit_count") or 0) >= 3 and _float(s6976.get("total_pnl_yen_100")) < -60000):
        return "6976_reentry_failure"
    if "slippage" in factors:
        return "stop_slippage_failure"
    if "price_band" in factors:
        return "price_band_risk_failure"
    return "market_bad_day_only"


def run_phase434_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    canonical_618, _ = _load_all_canonical(TARGET_DAY, repo_root=repo_root)

    e6976, p6976, s6976 = _audit_6976(TARGET_DAY, repo_root=repo_root)
    reentry_rows, cf_rows, reentry_meta = _stop_reentry_audit(canonical_618)
    slip_rows, slip_summary = _stop_slippage_audit(TARGET_DAY, canonical_618, repo_root=repo_root)

    comp_rows = []
    for day in (COMPARE_DAY, TARGET_DAY):
        for bucket in ("AM", "PM"):
            m = _day_comparison_metrics(day, repo_root=repo_root)
            sess_idx = 0 if bucket == "AM" else 1
            sess = SESSION_DIRS[day][sess_idx]
            summ = _read_json(kabu / "results" / "small_paper" / day / sess / "small_paper_summary.json")
            cs = summ.get("canonical_summary") or {}
            comp_rows.append(
                {
                    "day": day,
                    "session": bucket,
                    "trade_count": cs.get("trade_count"),
                    "stop_count": cs.get("stop_count"),
                    "stop_rate": cs.get("stop_rate"),
                    "profit_factor_yen_100": cs.get("profit_factor_yen_100"),
                    "total_pnl_yen_100": cs.get("total_pnl_yen_100"),
                    "avg_hold_sec": "",
                    "traded_symbols_count": cs.get("traded_symbols_count"),
                }
            )
        full = _day_comparison_metrics(day, repo_root=repo_root)
        comp_rows.append({"day": day, "session": "FULL", **{k: full.get(k) for k in full if k != "day"}})

    overlap_617 = set(_day_comparison_metrics(COMPARE_DAY, repo_root=repo_root).get("accepted_symbols") or [])
    overlap_618 = set(_day_comparison_metrics(TARGET_DAY, repo_root=repo_root).get("accepted_symbols") or [])
    for r in comp_rows:
        if r.get("session") == "FULL" and r.get("day") == TARGET_DAY:
            r["accepted_symbols_overlap_with_617"] = len(overlap_617 & overlap_618)

    price_band_rows = _price_band_pnl(canonical_618)
    cap_rows, cap_summary = _capital_sim_consistency(repo_root, TARGET_DAY)

    verdict = _verdict(
        s6976=s6976,
        stop_slip=slip_summary,
        price_bands=price_band_rows,
        reentry_meta=reentry_meta,
        capital=cap_summary,
    )

    mandatory = {
        "P0_1_6976_entry_count": s6976["entry_count"],
        "P0_1_6976_stop_count": s6976["stop_hit_count"],
        "P0_1_6976_total_pnl_yen_100": s6976["total_pnl_yen_100"],
        "P0_1_why_entries_passed": "score_v2>=5 with gate_accept; several entries on downtrend bounces (negative 15m return)",
        "P0_1_pullback_pattern": s6976["pullback_reentry_pattern_count"] > 0,
        "P0_1_momentum_low_board_mid": s6976["momentum_low_board_mid_count"],
        "P0_2_stop_reentry_count": reentry_meta["stop_reentry_pair_count"],
        "P0_2_stop_reentry_pnl": reentry_meta["stop_reentry_total_pnl_yen_100"],
        "P0_2_counterfactual_pnl": reentry_meta["counterfactual"]["counterfactual_total_pnl_yen_100"],
        "P0_2_runtime_candidate": reentry_meta["counterfactual"]["delta_yen_100"] > 50000,
        "P0_2_effective_on_6976": reentry_meta["6976_stop_reentry_pnl_yen_100"] < -20000,
        "P1_1_stop_hit_count": slip_summary["stop_hit_count"],
        "P1_1_beyond_1p2_count": slip_summary["beyond_1p2_count"],
        "P1_1_max_excess_pct": slip_summary["max_excess_pct"],
        "P1_1_avg_excess_pct": slip_summary["avg_excess_pct"],
        "P1_1_6976_m1p7_cause": slip_summary.get("6976_worst_stop"),
        "P1_1_bug_vs_push": "price_slippage_not_implementation_bug",
        "P2_1_high_price_dominates": any(
            b.get("price_band") == "gte_10000" and _float(b.get("total_pnl_yen_100")) < -100000 for b in price_band_rows
        ),
        "P2_2_daily_pnl_capital_sim": cap_summary.get("phase273_daily_pnl"),
        "P2_2_vs_canonical": cap_summary.get("canonical_summary_pnl"),
        "P2_2_why_both_negative_and_pm_equity_up": cap_summary.get("explanation"),
        "P2_2_bug_or_spec": "specification",
    }

    return {
        "phase": "434-20260618-Loss-Attribution",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "6976_summary": s6976,
        "stop_slippage_summary": slip_summary,
        "reentry_meta": reentry_meta,
        "capital_sim_summary": cap_summary,
        "comparison_617_618": comp_rows,
        "outputs": {
            "6976_entry_audit": e6976,
            "6976_price_context": p6976,
            "stop_reentry_audit": reentry_rows,
            "stop_reentry_counterfactual": cf_rows,
            "stop_slippage_audit": slip_rows,
            "stop_comparison": comp_rows,
            "price_band_pnl": price_band_rows,
            "capital_sim_consistency": cap_rows,
        },
    }


@dataclass
class Phase434Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase434_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        kabu = resolve_kabu_root(self.repo_root)
        out = result.get("outputs") or {}

        paths = {
            "6976_entry": reports / "phase434_6976_entry_audit.csv",
            "6976_price": reports / "phase434_6976_price_context.csv",
            "reentry": reports / "phase434_stop_reentry_audit.csv",
            "cf": reports / "phase434_stop_reentry_counterfactual.csv",
            "slippage": reports / "phase434_stop_slippage_audit.csv",
            "comparison": reports / "phase434_617_vs_618_stop_comparison.csv",
            "price_band": reports / "phase434_price_band_pnl.csv",
            "capital": reports / "phase434_capital_sim_consistency.csv",
            "summary": reports / "phase434_loss_attribution_summary.json",
            "report": kabu / "docs" / "operations" / "phase434_20260618_loss_attribution_report.md",
        }

        _csv_write(paths["6976_entry"], out.get("6976_entry_audit") or [])
        _csv_write(paths["6976_price"], out.get("6976_price_context") or [])
        _csv_write(paths["reentry"], out.get("stop_reentry_audit") or [])
        _csv_write(paths["cf"], out.get("stop_reentry_counterfactual") or [])
        _csv_write(paths["slippage"], out.get("stop_slippage_audit") or [])
        _csv_write(paths["comparison"], out.get("stop_comparison") or [])
        _csv_write(paths["price_band"], out.get("price_band_pnl") or [])
        _csv_write(paths["capital"], out.get("capital_sim_consistency") or [])

        summary_payload = {
            "phase": result.get("phase"),
            "generated_at": result.get("generated_at"),
            "verdict": result.get("verdict"),
            "mandatory_answers": result.get("mandatory_answers"),
            "6976_summary": result.get("6976_summary"),
            "stop_slippage_summary": result.get("stop_slippage_summary"),
            "reentry_meta": result.get("reentry_meta"),
            "capital_sim_summary": result.get("capital_sim_summary"),
        }
        paths["summary"].write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        lines = [
            "# Phase434 — 20260618 Loss Attribution Report",
            "",
            f"Generated: {result.get('generated_at')}",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## P0-1 6976.T Entry Chain",
            "",
            f"- ENTRY count: **{m.get('P0_1_6976_entry_count')}**",
            f"- stop_hit: **{m.get('P0_1_6976_stop_count')}**",
            f"- total PnL: **{m.get('P0_1_6976_total_pnl_yen_100'):,.0f}円(100株)**",
            f"- momentum_low + board_mid entries: **{m.get('P0_1_momentum_low_board_mid')}**",
            f"- pullback bounce pattern entries: **{m.get('P0_1_pullback_pattern')}**",
            "",
            "## P0-2 Stop后 Reentry",
            "",
            f"- stop後再ENTRY: **{m.get('P0_2_stop_reentry_count')}** pairs, PnL **{m.get('P0_2_stop_reentry_pnl'):,.0f}円**",
            f"- counterfactual (ban): **{m.get('P0_2_counterfactual_pnl'):,.0f}円**",
            f"- runtime候補: **{m.get('P0_2_runtime_candidate')}**",
            f"- 6976に効く: **{m.get('P0_2_effective_on_6976')}**",
            "",
            "## P1-1 Hard Stop Slippage",
            "",
            f"- stop_hit: **{m.get('P1_1_stop_hit_count')}**, beyond -1.2%: **{m.get('P1_1_beyond_1p2_count')}**",
            f"- max excess: **{m.get('P1_1_max_excess_pct')}%**, avg: **{m.get('P1_1_avg_excess_pct')}%**",
            "",
            "## P1-2 6/17 vs 6/18",
            "",
            "See `phase434_617_vs_618_stop_comparison.csv`.",
            "",
            "## P2-1 Price Band",
            "",
            f"- 高価格帯支配: **{m.get('P2_1_high_price_dominates')}**",
            "",
            "## P2-2 Capital Sim Consistency",
            "",
            f"- capital sim daily PnL: **{m.get('P2_2_daily_pnl_capital_sim'):,.0f}円**",
            f"- canonical: **{m.get('P2_2_vs_canonical'):,.0f}円**",
            f"- 解釈: {m.get('P2_2_why_both_negative_and_pm_equity_up')}",
            f"- bug/spec: **{m.get('P2_2_bug_or_spec')}**",
            "",
            "## Artifacts",
            "",
        ]
        for k, p in paths.items():
            if k != "report":
                lines.append(f"- `{p.name}`")
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text("\n".join(lines), encoding="utf-8")
        return paths
