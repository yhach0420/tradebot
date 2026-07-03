"""
Phase622 — Stagnation Exit Re-entry Guard + Liquidity Stale Attribution.

Research only. No Runtime / ENTRY / PBv2 / EXIT / order changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase409_boundary_forward_shadow import load_structural_trades_for_day
from research.phase431_entry_priority_reentry_audit import _metrics_from_pnls, _pnl_yen_100
from research.phase451_entry_shape_tournament import _now_iso
from research.phase523_reentry_definition_overlay_edge_reality_audit import _resolved_exit_reason
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

VERDICT = "phase622_stagnation_reentry_guard_done"
JST = ZoneInfo("Asia/Tokyo")
TARGET_AM_DAY = "20260701"
TARGET_AM_SESSION = "live_session_080616"
LIQUIDITY_BURST_THRESHOLD = 0.025
SCORE_BOOST_DELTA = 0.10
GUARD_MAX_GAP_SEC = 20 * 60
GUARD_MAX_PREV_MFE_PCT = 0.6
WEAK_PNL_ABS_PCT = 0.3

WEAK_EXIT_BUCKETS = frozenset(
    {
        "stagnation",
        "time_decay",
        "weak_mfe",
        "low_mfe",
        "flat_exit",
        "profit_protect_weak",
    }
)

GUARD_REASON_MAP = {
    "no_progress_exit": "stagnation",
    "time_decay_exit": "time_decay",
    "time_decay": "time_decay",
}


@dataclass(frozen=True)
class GuardSignal:
    new_high_after_exit: bool
    momentum_improved: bool
    board_improved: bool
    liquidity_burst: bool
    score_boost: bool

    @property
    def any_pass(self) -> bool:
        return (
            self.new_high_after_exit
            or self.momentum_improved
            or self.board_improved
            or self.liquidity_burst
            or self.score_boost
        )


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _write_rows_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    _write_csv(path, fields, rows)


def _load_session_symbol_events(session_dir: Path) -> dict[str, list[tuple[datetime, float]]]:
    path = session_dir / "small_paper_events.csv"
    out: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            sym = str(row.get("symbol") or "")
            if not sym:
                continue
            ts = _parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
            px = _num(row.get("current_price") or row.get("entry_price") or row.get("close_price"))
            if ts is None or px <= 0:
                continue
            out[sym].append((ts, px))
    for sym in out:
        out[sym].sort(key=lambda x: x[0])
    return out


def _peak_between_from_index(
    series: Sequence[tuple[datetime, float]],
    *,
    prev_close: datetime,
    cur_entry: datetime,
) -> float:
    peak = 0.0
    for ts, px in series:
        if ts < prev_close:
            continue
        if ts > cur_entry:
            break
        if px > peak:
            peak = px
    return peak


def _peak_between_exit_and_entry(
    session_dir: Path,
    symbol: str,
    *,
    prev_close: datetime,
    cur_entry: datetime,
    enabled: bool,
    event_index: Optional[Mapping[str, Sequence[tuple[datetime, float]]]] = None,
) -> float:
    if not enabled:
        return 0.0
    if event_index is not None:
        return _peak_between_from_index(
            event_index.get(symbol) or [],
            prev_close=prev_close,
            cur_entry=cur_entry,
        )
    path = session_dir / "small_paper_events.csv"
    if not path.is_file():
        return 0.0
    peak = 0.0
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if str(row.get("symbol") or "") != symbol:
                continue
            ts = _parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
            if ts is None or ts < prev_close or ts > cur_entry:
                continue
            px = _num(row.get("current_price") or row.get("entry_price") or row.get("close_price"))
            if px > peak:
                peak = px
    return peak


def _discover_am_session(kabu: Path, day: str) -> Optional[Path]:
    explicit = kabu / "results" / "small_paper" / day / TARGET_AM_SESSION
    if explicit.is_dir():
        return explicit
    day_dir = kabu / "results" / "small_paper" / day
    if not day_dir.is_dir():
        return None
    candidates: list[tuple[str, Path]] = []
    for p in day_dir.iterdir():
        if not p.is_dir() or not p.name.startswith("live_session_"):
            continue
        summary_path = p / "small_paper_summary.json"
        kind = ""
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                kind = str((summary.get("am_pm_session") or {}).get("kind") or "")
            except (OSError, json.JSONDecodeError):
                kind = ""
        if kind == "am" or p.name.startswith("live_session_08"):
            candidates.append((p.name, p))
    if not candidates:
        return None
    return sorted(candidates)[0][1]


def _load_structural_trades(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            ep = _num(row.get("entry_price"))
            pct = _num(row.get("realized_pnl_pct"))
            rows.append(
                {
                    "session": session_dir.name,
                    "day": session_dir.parent.name,
                    "symbol": str(row.get("symbol") or ""),
                    "entry_time": str(row.get("entry_time") or ""),
                    "close_time": str(row.get("close_time") or ""),
                    "entry_price": ep,
                    "close_price": _num(row.get("close_price")),
                    "realized_pnl_pct": pct,
                    "pnl_yen_100": _pnl_yen_100(ep, pct),
                    "hold_sec": _num(row.get("hold_duration_sec")),
                    "close_reason": str(row.get("close_reason") or ""),
                    "exit_reason_resolved": _resolved_exit_reason(
                        {
                            "exit_reason": row.get("close_reason"),
                            "structural_exit_reason": row.get("close_reason"),
                        }
                    ),
                    "mfe_pct": _num(row.get("mfe_pct")),
                    "mae_pct": _num(row.get("mae_pct")),
                    "continuation_quality_score": _num(row.get("continuation_quality_score")),
                }
            )
    rows.sort(key=lambda r: (_parse_ts(str(r["entry_time"])) or datetime.min.replace(tzinfo=JST)))
    return rows


def _load_accepted_lookup(session_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = session_dir / "small_paper_events.csv"
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if str(row.get("event_type") or "") != "accepted":
                continue
            sym = str(row.get("symbol") or "")
            entry_time = str(row.get("entry_time") or row.get("event_time") or "")
            if not sym or not entry_time:
                continue
            out[(sym, entry_time)] = dict(row)
    return out


def _prev_trade_high(prev: Mapping[str, Any]) -> float:
    ep = _num(prev.get("entry_price"))
    xp = _num(prev.get("close_price"))
    mfe = _num(prev.get("mfe_pct"))
    hi = max(ep, xp)
    if ep > 0 and mfe > 0:
        hi = max(hi, ep * (1.0 + mfe / 100.0))
    return hi


def _weak_exit_bucket(prev: Mapping[str, Any]) -> Optional[str]:
    reason = normalize_exit_reason(str(prev.get("exit_reason_resolved") or prev.get("close_reason") or ""))
    mapped = GUARD_REASON_MAP.get(reason)
    if mapped:
        return mapped
    mfe = _num(prev.get("mfe_pct"))
    pnl = _num(prev.get("realized_pnl_pct"))
    if reason == "trailing_mfe_exit" and mfe < GUARD_MAX_PREV_MFE_PCT:
        return "weak_mfe"
    if mfe < GUARD_MAX_PREV_MFE_PCT:
        return "low_mfe"
    if abs(pnl) < WEAK_PNL_ABS_PCT:
        return "flat_exit"
    if reason == "trailing_mfe_exit" and pnl < 0.5:
        return "profit_protect_weak"
    return None


def _accepted_features(
    accepted_lookup: Mapping[tuple[str, str], Mapping[str, Any]],
    trade: Mapping[str, Any],
) -> dict[str, Optional[float]]:
    key = (str(trade.get("symbol") or ""), str(trade.get("entry_time") or ""))
    row = accepted_lookup.get(key) or {}
    return {
        "momentum_continuation_score": _num(
            row.get("entry_momentum_continuation_score") or row.get("momentum_continuation_score"),
        ),
        "board_imbalance": _num(
            row.get("entry_order_book_imbalance") or row.get("board_imbalance_score"),
        ),
        "liquidity_burst": _num(row.get("liquidity_burst")),
        "continuation_quality_score": _num(
            row.get("continuation_quality_score") or trade.get("continuation_quality_score"),
        ),
        "price_freshness_source": str(row.get("price_freshness_source") or ""),
    }


def _evaluate_guard_signals(
    prev: Mapping[str, Any],
    cur: Mapping[str, Any],
    *,
    prev_features: Mapping[str, Any],
    cur_features: Mapping[str, Any],
    session_dir: Optional[Path] = None,
    use_event_peak: bool = False,
    event_index: Optional[Mapping[str, Sequence[tuple[datetime, float]]]] = None,
) -> GuardSignal:
    prev_close = _parse_ts(str(prev.get("close_time") or ""))
    cur_entry = _parse_ts(str(cur.get("entry_time") or ""))
    prev_high = _prev_trade_high(prev)
    entry_px = _num(cur.get("entry_price"))
    new_high = entry_px > prev_high
    if use_event_peak and session_dir is not None and prev_close and cur_entry:
        peak = _peak_between_exit_and_entry(
            session_dir,
            str(cur.get("symbol") or ""),
            prev_close=prev_close,
            cur_entry=cur_entry,
            enabled=True,
            event_index=event_index,
        )
        if peak > prev_high:
            new_high = True
    prev_mom = _num(prev_features.get("momentum_continuation_score"))
    cur_mom = _num(cur_features.get("momentum_continuation_score"))
    prev_board = _num(prev_features.get("board_imbalance"))
    cur_board = _num(cur_features.get("board_imbalance"))
    prev_score = _num(prev_features.get("continuation_quality_score"))
    cur_score = _num(cur_features.get("continuation_quality_score"))
    burst = _num(cur_features.get("liquidity_burst")) >= LIQUIDITY_BURST_THRESHOLD
    return GuardSignal(
        new_high_after_exit=new_high,
        momentum_improved=cur_mom > prev_mom,
        board_improved=cur_board > prev_board,
        liquidity_burst=burst,
        score_boost=cur_score >= prev_score + SCORE_BOOST_DELTA,
    )


def _guard_blocks_reentry(
    prev: Mapping[str, Any],
    cur: Mapping[str, Any],
    *,
    gap_sec: float,
    accepted_lookup: Mapping[tuple[str, str], Mapping[str, Any]],
    session_dir: Optional[Path] = None,
    use_event_peak: bool = False,
    event_index: Optional[Mapping[str, Sequence[tuple[datetime, float]]]] = None,
) -> tuple[bool, Optional[str], GuardSignal]:
    bucket = _weak_exit_bucket(prev)
    if bucket is None or bucket not in WEAK_EXIT_BUCKETS:
        return False, bucket, GuardSignal(False, False, False, False, False)
    if gap_sec >= GUARD_MAX_GAP_SEC:
        return False, bucket, GuardSignal(False, False, False, False, False)
    if _num(prev.get("mfe_pct")) >= GUARD_MAX_PREV_MFE_PCT:
        return False, bucket, GuardSignal(False, False, False, False, False)
    prev_features = _accepted_features(accepted_lookup, prev)
    cur_features = _accepted_features(accepted_lookup, cur)
    signals = _evaluate_guard_signals(
        prev,
        cur,
        prev_features=prev_features,
        cur_features=cur_features,
        session_dir=session_dir,
        use_event_peak=use_event_peak,
        event_index=event_index,
    )
    return (not signals.any_pass), bucket, signals


def _liquidity_stale_attribution(
    session_dir: Path,
    trades: Sequence[Mapping[str, Any]],
    accepted_lookup: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    stale_accepted = [
        row
        for row in accepted_lookup.values()
        if str(row.get("price_freshness_source") or "") == "liquidity_stale_trade"
    ]
    trade_by_key = {(str(t["symbol"]), str(t["entry_time"])): t for t in trades}
    rows: list[dict[str, Any]] = []
    for acc in stale_accepted:
        sym = str(acc.get("symbol") or "")
        entry_time = str(acc.get("entry_time") or acc.get("event_time") or "")
        trade = trade_by_key.get((sym, entry_time))
        if trade is None:
            for t in trades:
                if t["symbol"] == sym and str(t["entry_time"]).startswith(entry_time[:16]):
                    trade = t
                    break
        if trade is None:
            continue
        rows.append(
            {
                "symbol": sym,
                "entry_time": trade["entry_time"],
                "close_time": trade["close_time"],
                "entry_price": trade["entry_price"],
                "close_price": trade["close_price"],
                "pnl_yen_100": trade["pnl_yen_100"],
                "realized_pnl_pct": trade["realized_pnl_pct"],
                "profit_factor_component": "win" if trade["pnl_yen_100"] > 0 else "loss",
                "mfe_pct": trade["mfe_pct"],
                "mae_pct": trade["mae_pct"],
                "exit_reason": trade["exit_reason_resolved"],
                "continuation_quality_score": trade["continuation_quality_score"],
                "momentum_continuation_score": _accepted_features(accepted_lookup, trade)[
                    "momentum_continuation_score"
                ],
                "price_freshness_source": "liquidity_stale_trade",
            }
        )
    rows.sort(key=lambda r: str(r["entry_time"]))
    return rows


def _same_symbol_reentry_trace(
    session_dir: Path,
    trades: Sequence[Mapping[str, Any]],
    accepted_lookup: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    event_index: Mapping[str, Sequence[tuple[datetime, float]]],
) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t["symbol"])].append(dict(t))
    rows: list[dict[str, Any]] = []
    for sym, seq in by_sym.items():
        if len(seq) < 2:
            continue
        seq.sort(key=lambda r: (_parse_ts(str(r["entry_time"])) or datetime.min.replace(tzinfo=JST)))
        for i in range(1, len(seq)):
            prev = seq[i - 1]
            cur = seq[i]
            prev_close = _parse_ts(str(prev["close_time"]))
            cur_entry = _parse_ts(str(cur["entry_time"]))
            if prev_close is None or cur_entry is None:
                continue
            gap_sec = (cur_entry - prev_close).total_seconds()
            if gap_sec < 0:
                continue
            bucket = _weak_exit_bucket(prev)
            prev_features = _accepted_features(accepted_lookup, prev)
            cur_features = _accepted_features(accepted_lookup, cur)
            signals = _evaluate_guard_signals(
                prev,
                cur,
                prev_features=prev_features,
                cur_features=cur_features,
                session_dir=session_dir,
                use_event_peak=True,
                event_index=event_index,
            )
            blocked, _, _ = _guard_blocks_reentry(
                prev,
                cur,
                gap_sec=gap_sec,
                accepted_lookup=accepted_lookup,
                session_dir=session_dir,
                use_event_peak=True,
                event_index=event_index,
            )
            prev_high = _prev_trade_high(prev)
            peak_between = _peak_between_exit_and_entry(
                session_dir,
                sym,
                prev_close=prev_close,
                cur_entry=cur_entry,
                enabled=True,
                event_index=event_index,
            )
            rows.append(
                {
                    "symbol": sym,
                    "prev_entry_time": prev["entry_time"],
                    "prev_close_time": prev["close_time"],
                    "prev_exit_reason": prev["exit_reason_resolved"],
                    "prev_exit_bucket": bucket or "",
                    "prev_mfe_pct": prev["mfe_pct"],
                    "prev_pnl_yen_100": prev["pnl_yen_100"],
                    "reentry_time": cur["entry_time"],
                    "gap_sec": round(gap_sec, 1),
                    "gap_min": round(gap_sec / 60.0, 2),
                    "reentry_pnl_yen_100": cur["pnl_yen_100"],
                    "reentry_exit_reason": cur["exit_reason_resolved"],
                    "reentry_mfe_pct": cur["mfe_pct"],
                    "reentry_mae_pct": cur["mae_pct"],
                    "prev_was_stagnation": bucket == "stagnation",
                    "prev_was_weak_or_flat": bucket in WEAK_EXIT_BUCKETS,
                    "no_new_high_before_reentry": peak_between <= prev_high,
                    "no_momentum_improvement": not signals.momentum_improved,
                    "no_board_improvement": not signals.board_improved,
                    "no_liquidity_burst": not signals.liquidity_burst,
                    "no_score_boost": not signals.score_boost,
                    "quick_reentry_loss": gap_sec <= GUARD_MAX_GAP_SEC and cur["pnl_yen_100"] < 0,
                    "guard_would_block": blocked,
                    "new_high_after_exit": signals.new_high_after_exit,
                    "momentum_improved": signals.momentum_improved,
                    "board_improved": signals.board_improved,
                    "liquidity_burst": signals.liquidity_burst,
                    "score_boost": signals.score_boost,
                }
            )
    rows.sort(key=lambda r: (str(r["symbol"]), str(r["reentry_time"])))
    return rows


def _case_study_4062(
    session_dir: Path,
    trades: Sequence[Mapping[str, Any]],
    accepted_lookup: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    event_index: Mapping[str, Sequence[tuple[datetime, float]]],
) -> list[dict[str, Any]]:
    sym = "4062.T"
    seq = [dict(t) for t in trades if t["symbol"] == sym]
    seq.sort(key=lambda r: (_parse_ts(str(r["entry_time"])) or datetime.min.replace(tzinfo=JST)))
    if len(seq) < 2:
        return []
    prev, cur = seq[0], seq[1]
    prev_close = _parse_ts(str(prev["close_time"]))
    cur_entry = _parse_ts(str(cur["entry_time"]))
    gap_sec = (cur_entry - prev_close).total_seconds() if prev_close and cur_entry else 0.0
    prev_features = _accepted_features(accepted_lookup, prev)
    cur_features = _accepted_features(accepted_lookup, cur)
    signals = _evaluate_guard_signals(
        prev,
        cur,
        prev_features=prev_features,
        cur_features=cur_features,
        session_dir=session_dir,
        use_event_peak=True,
        event_index=event_index,
    )
    prev_high = _prev_trade_high(prev)
    peak_between = (
        _peak_between_exit_and_entry(
            session_dir,
            sym,
            prev_close=prev_close,
            cur_entry=cur_entry,
            enabled=True,
            event_index=event_index,
        )
        if prev_close and cur_entry
        else 0.0
    )
    rows = [
        {
            "leg": "1_entry",
            "timestamp": prev["entry_time"],
            "price": prev["entry_price"],
            "momentum": prev_features["momentum_continuation_score"],
            "board_imbalance": prev_features["board_imbalance"],
            "quality_score": prev_features["continuation_quality_score"],
            "note": "first_entry",
        },
        {
            "leg": "1_exit",
            "timestamp": prev["close_time"],
            "price": prev["close_price"],
            "momentum": "",
            "board_imbalance": "",
            "quality_score": "",
            "note": f"exit={prev['exit_reason_resolved']} pnl={prev['realized_pnl_pct']}% mfe={prev['mfe_pct']}%",
        },
        {
            "leg": "between_peak",
            "timestamp": prev["close_time"],
            "price": round(peak_between, 1),
            "momentum": "",
            "board_imbalance": "",
            "quality_score": "",
            "note": f"peak_between prev_high={round(prev_high,1)} new_high={peak_between > prev_high}",
        },
        {
            "leg": "2_entry",
            "timestamp": cur["entry_time"],
            "price": cur["entry_price"],
            "momentum": cur_features["momentum_continuation_score"],
            "board_imbalance": cur_features["board_imbalance"],
            "quality_score": cur_features["continuation_quality_score"],
            "note": "second_entry",
        },
        {
            "leg": "2_exit",
            "timestamp": cur["close_time"],
            "price": cur["close_price"],
            "momentum": "",
            "board_imbalance": "",
            "quality_score": "",
            "note": f"exit={cur['exit_reason_resolved']} pnl={cur['realized_pnl_pct']}% mfe={cur['mfe_pct']}%",
        },
        {
            "leg": "signal_check",
            "timestamp": cur["entry_time"],
            "price": "",
            "momentum": signals.momentum_improved,
            "board_imbalance": signals.board_improved,
            "quality_score": signals.score_boost,
            "note": (
                f"gap_min={round(gap_sec/60,1)} new_high={signals.new_high_after_exit} "
                f"liquidity_burst={signals.liquidity_burst} any_pass={signals.any_pass}"
            ),
            "any_restart_signal": signals.any_pass,
            "new_high_after_exit": signals.new_high_after_exit,
            "momentum_improved": signals.momentum_improved,
            "board_improved": signals.board_improved,
            "liquidity_burst": signals.liquidity_burst,
            "score_boost": signals.score_boost,
        },
    ]
    return rows


def _iter_live_session_dirs(kabu: Path) -> list[Path]:
    root = kabu / "results" / "small_paper"
    if not root.is_dir():
        return []
    out: list[Path] = []
    for day_dir in sorted(root.iterdir()):
        if not day_dir.is_dir() or not day_dir.name.isdigit():
            continue
        for sess in sorted(day_dir.iterdir()):
            if not sess.is_dir() or not sess.name.startswith("live_session_"):
                continue
            summary_path = sess / "small_paper_summary.json"
            if summary_path.is_file():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    if str(summary.get("source") or "") == "push-replay":
                        continue
                except (OSError, json.JSONDecodeError):
                    pass
            if (sess / "structural_trades.csv").is_file():
                out.append(sess)
    return out


def _counterfactual_rows(
    kabu: Path,
    *,
    session_filter: Optional[str] = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sess in _iter_live_session_dirs(kabu):
        day = sess.parent.name
        is_am = sess.name.startswith("live_session_08") or sess.name == TARGET_AM_SESSION
        scope = "am_today" if day == TARGET_AM_DAY and is_am else "all_period"
        if session_filter == "am_today" and scope != "am_today":
            continue
        trades = _load_structural_trades(sess)
        if not trades:
            continue
        accepted_lookup = _load_accepted_lookup(sess)
        by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in trades:
            by_sym[str(t["symbol"])].append(dict(t))
        for sym, seq in by_sym.items():
            seq.sort(key=lambda r: (_parse_ts(str(r["entry_time"])) or datetime.min.replace(tzinfo=JST)))
            for i in range(1, len(seq)):
                prev, cur = seq[i - 1], seq[i]
                prev_close = _parse_ts(str(prev["close_time"]))
                cur_entry = _parse_ts(str(cur["entry_time"]))
                if prev_close is None or cur_entry is None:
                    continue
                gap_sec = (cur_entry - prev_close).total_seconds()
                if gap_sec < 0:
                    continue
                blocked, bucket, signals = _guard_blocks_reentry(
                    prev,
                    cur,
                    gap_sec=gap_sec,
                    accepted_lookup=accepted_lookup,
                    session_dir=None,
                    use_event_peak=False,
                )
                rows.append(
                    {
                        "scope": scope,
                        "day": day,
                        "session": sess.name,
                        "symbol": sym,
                        "reentry_time": cur["entry_time"],
                        "gap_sec": round(gap_sec, 1),
                        "prev_exit_bucket": bucket or "",
                        "prev_exit_reason": prev["exit_reason_resolved"],
                        "prev_mfe_pct": prev["mfe_pct"],
                        "baseline_pnl_yen_100": cur["pnl_yen_100"],
                        "guard_blocked": blocked,
                        "guard_pnl_yen_100": 0.0 if blocked else cur["pnl_yen_100"],
                        "new_high_after_exit": signals.new_high_after_exit,
                        "momentum_improved": signals.momentum_improved,
                        "board_improved": signals.board_improved,
                        "liquidity_burst": signals.liquidity_burst,
                        "score_boost": signals.score_boost,
                    }
                )
    return rows


def _aggregate_counterfactual(rows: Sequence[Mapping[str, Any]], *, scope: str) -> dict[str, Any]:
    if scope == "all":
        subset = list(rows)
    else:
        subset = [r for r in rows if str(r.get("scope")) == scope]
    baseline_pnls = [_num(r.get("baseline_pnl_yen_100")) for r in subset]
    guard_pnls = [_num(r.get("guard_pnl_yen_100")) for r in subset]
    blocked = [r for r in subset if r.get("guard_blocked")]
    blocked_pnls = [_num(r.get("baseline_pnl_yen_100")) for r in blocked]
    return {
        "scope": scope,
        "reentry_pair_count": len(subset),
        "blocked_reentry_count": len(blocked),
        "blocked_reentry_pnl_yen_100": round(sum(blocked_pnls), 2),
        "blocked_win_count": sum(1 for p in blocked_pnls if p > 0),
        "blocked_loss_count": sum(1 for p in blocked_pnls if p < 0),
        "baseline_reentry_pnl_yen_100": round(sum(baseline_pnls), 2),
        "guard_reentry_pnl_yen_100": round(sum(guard_pnls), 2),
        "reentry_pnl_delta_yen_100": round(sum(guard_pnls) - sum(baseline_pnls), 2),
        "baseline_reentry_pf": _pf(baseline_pnls),
        "guard_reentry_pf": _pf(guard_pnls),
        "baseline_reentry_win_rate": _win_rate(baseline_pnls),
        "guard_reentry_win_rate": _win_rate(guard_pnls),
        "baseline_reentry_max_dd_yen_100": round(_max_drawdown_yen(baseline_pnls), 2),
        "guard_reentry_max_dd_yen_100": round(_max_drawdown_yen(guard_pnls), 2),
    }


def _portfolio_metrics_from_sessions(kabu: Path, *, blocked_keys: set[tuple[str, str, str]]) -> dict[str, Any]:
    pnls: list[float] = []
    for sess in _iter_live_session_dirs(kabu):
        day = sess.parent.name
        for t in _load_structural_trades(sess):
            key = (day, str(t["symbol"]), str(t["entry_time"]))
            if key in blocked_keys:
                continue
            pnls.append(_num(t["pnl_yen_100"]))
    return {
        "trade_count": len(pnls),
        "total_pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "win_rate": _win_rate(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(pnls), 2),
    }


def run_phase622(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root or Path(__file__).resolve().parents[2])
    reports = resolve_reports_dir(kabu)
    session_dir = _discover_am_session(kabu, TARGET_AM_DAY)
    if session_dir is None:
        raise FileNotFoundError(f"AM session not found for {TARGET_AM_DAY}")

    trades = _load_structural_trades(session_dir)
    accepted_lookup = _load_accepted_lookup(session_dir)
    event_index = _load_session_symbol_events(session_dir)

    stale_rows = _liquidity_stale_attribution(session_dir, trades, accepted_lookup)
    reentry_rows = _same_symbol_reentry_trace(
        session_dir, trades, accepted_lookup, event_index=event_index
    )
    case_rows = _case_study_4062(session_dir, trades, accepted_lookup, event_index=event_index)
    cf_rows = _counterfactual_rows(kabu)

    blocked_keys = {
        (str(r["day"]), str(r["symbol"]), str(r["reentry_time"]))
        for r in cf_rows
        if r.get("guard_blocked")
    }
    baseline_portfolio = _portfolio_metrics_from_sessions(kabu, blocked_keys=set())
    guard_portfolio = _portfolio_metrics_from_sessions(kabu, blocked_keys=blocked_keys)

    stale_pnls = [_num(r["pnl_yen_100"]) for r in stale_rows]
    stale_metrics = _metrics_from_pnls(stale_pnls, [0.0] * len(stale_pnls))
    stale_metrics.update(
        {
            "avg_mfe_pct": round(statistics.mean([_num(r["mfe_pct"]) for r in stale_rows]), 4)
            if stale_rows
            else 0.0,
            "avg_mae_pct": round(statistics.mean([_num(r["mae_pct"]) for r in stale_rows]), 4)
            if stale_rows
            else 0.0,
        }
    )

    stagnation_quick_losses = [
        r
        for r in reentry_rows
        if r.get("prev_was_stagnation") or (
            r.get("prev_was_weak_or_flat") and r.get("quick_reentry_loss")
        )
    ]
    stagnation_reentry_pnls = [_num(r["reentry_pnl_yen_100"]) for r in stagnation_quick_losses]

    cf_am = _aggregate_counterfactual(cf_rows, scope="am_today")
    cf_all = _aggregate_counterfactual(cf_rows, scope="all_period")

    case_signal = next((r for r in case_rows if r.get("leg") == "signal_check"), {})
    implement_guard = (
        cf_all["reentry_pnl_delta_yen_100"] > 0
        and cf_all["blocked_loss_count"] > cf_all["blocked_win_count"]
        and guard_portfolio["total_pnl_yen_100"] >= baseline_portfolio["total_pnl_yen_100"]
    )

    report: dict[str, Any] = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "target_am_session": str(session_dir.relative_to(kabu)).replace("\\", "/"),
        "investigation_1_liquidity_stale_trade": {
            "accepted_count": len(stale_rows),
            "metrics": stale_metrics,
            "verdict_profit_or_loss": "profit"
            if stale_metrics["total_pnl_yen"] > 0
            else ("loss" if stale_metrics["total_pnl_yen"] < 0 else "flat"),
        },
        "investigation_2_same_symbol_reentry": {
            "pair_count": len(reentry_rows),
            "stagnation_or_quick_weak_reentry_count": len(stagnation_quick_losses),
            "stagnation_or_quick_weak_reentry_pnl_yen_100": round(sum(stagnation_reentry_pnls), 2),
            "guard_would_block_count_am": sum(1 for r in reentry_rows if r.get("guard_would_block")),
        },
        "investigation_3_4062_case_study": {
            "second_entry_any_restart_signal": bool(case_signal.get("any_restart_signal")),
            "signal_note": case_signal.get("note", ""),
            "new_high_after_exit": case_signal.get("new_high_after_exit"),
            "momentum_improved": case_signal.get("momentum_improved"),
            "board_improved": case_signal.get("board_improved"),
            "liquidity_burst": case_signal.get("liquidity_burst"),
            "score_boost": case_signal.get("score_boost"),
        },
        "counterfactual": {
            "am_today": cf_am,
            "all_period": cf_all,
            "portfolio_baseline": baseline_portfolio,
            "portfolio_with_guard": guard_portfolio,
            "portfolio_delta_yen_100": round(
                guard_portfolio["total_pnl_yen_100"] - baseline_portfolio["total_pnl_yen_100"],
                2,
            ),
        },
        "mandatory_answers": {
            "1_liquidity_stale_7_trades_profit_or_loss": (
                "profit"
                if stale_metrics["total_pnl_yen"] > 0
                else ("loss" if stale_metrics["total_pnl_yen"] < 0 else "flat")
            ),
            "2_4062_second_entry_restart_signal": _parse_4062_answer(case_rows),
            "3_stagnation_quick_reentry_harmful": (
                sum(stagnation_reentry_pnls) < 0 or cf_all["blocked_reentry_pnl_yen_100"] < 0
            ),
            "4_guard_improves_metrics": (
                cf_all["reentry_pnl_delta_yen_100"] > 0
                and guard_portfolio["total_pnl_yen_100"] >= baseline_portfolio["total_pnl_yen_100"]
            ),
            "5_implement_on_mainline": implement_guard,
        },
        "guard_spec": {
            "name": "reentry_after_stagnation_guard",
            "max_gap_sec": GUARD_MAX_GAP_SEC,
            "max_prev_mfe_pct": GUARD_MAX_PREV_MFE_PCT,
            "weak_exit_buckets": sorted(WEAK_EXIT_BUCKETS),
            "required_any_of": [
                "new_high_after_exit",
                "momentum_improved",
                "board_improved",
                "liquidity_burst",
                "score_boost",
            ],
        },
    }

    _write_rows_csv(reports / "phase622_liquidity_stale_trade_attribution.csv", stale_rows)
    _write_rows_csv(reports / "phase622_same_symbol_reentry_trace.csv", reentry_rows)
    _write_rows_csv(reports / "phase622_4062_reentry_case_study.csv", case_rows)
    _write_rows_csv(reports / "phase622_reentry_guard_counterfactual.csv", cf_rows)
    out = reports / "phase622_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_paths"] = {
        "report": str(out),
        "liquidity_stale": str(reports / "phase622_liquidity_stale_trade_attribution.csv"),
        "reentry_trace": str(reports / "phase622_same_symbol_reentry_trace.csv"),
        "case_4062": str(reports / "phase622_4062_reentry_case_study.csv"),
        "counterfactual": str(reports / "phase622_reentry_guard_counterfactual.csv"),
    }
    return report


def _parse_4062_answer(case_rows: Sequence[Mapping[str, Any]]) -> str:
    sig = next((r for r in case_rows if r.get("leg") == "signal_check"), None)
    if not sig:
        return "unknown"
    if bool(sig.get("any_restart_signal")):
        return "yes_restart_signal_present"
    return "no_restart_signal"


if __name__ == "__main__":
    rep = run_phase622()
    print(rep["verdict"])
    print(json.dumps(rep["mandatory_answers"], ensure_ascii=False, indent=2))
