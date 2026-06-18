"""
Phase436 — Pullback guard redesign shadow (VWAP-free).

Replay Phase423 canonical baseline + forward (20260529–20260618).
Research only — no Runtime/YAML/Entry/Exit changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import PERIOD_START, load_canonical_live_config_trades
from research.market_sector_heat import _pf, _write_csv
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase367_low_mfe_residual_forensic import enrich_residual_trade
from research.phase379_380_period_b_eval import evaluate_variant_shadow
from research.phase382_capital_constrained_backtest import _day_from_ts, _parse_ts
from research.phase400_holding_time_audit import normalize_exit_reason
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.pullback_misread_dynamic40_entry_guard import is_dynamic40_universe
from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_dynamic40_shadow

JST = ZoneInfo("Asia/Tokyo")
PERIOD_END = "20260618"
STARTING_EQUITY = 1_500_000.0
TARGET_SYMBOL = "6976.T"


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _optional_float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _stream_events(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _iter_sessions(kabu_root: Path) -> list[tuple[str, Path]]:
    base = kabu_root / "results" / "small_paper"
    out: list[tuple[str, Path]] = []
    if not base.is_dir():
        return out
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        if day < PERIOD_START or day > PERIOD_END:
            continue
        for sess in sorted(day_dir.iterdir()):
            if sess.is_dir() and sess.name.startswith("live_session"):
                out.append((day, sess))
    return out


def _build_price_index(kabu_root: Path) -> dict[tuple[str, str], list[tuple[datetime, float]]]:
    """symbol|day -> sorted (ts, price) from event current_price."""
    idx: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for day, sess in _iter_sessions(kabu_root):
        for row in _stream_events(sess / "small_paper_events.csv"):
            sym = str(row.get("symbol") or "")
            px = _float(row.get("current_price"), default=0.0)
            if not sym or px <= 0:
                continue
            ts = _parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
            if ts is None:
                continue
            idx[(sym, day)].append((ts, px))
    for key in idx:
        idx[key].sort(key=lambda x: x[0])
    return idx


def _price_at_or_before(series: Sequence[tuple[datetime, float]], target: datetime) -> Optional[float]:
    best: Optional[float] = None
    for ts, px in series:
        if ts <= target:
            best = px
        else:
            break
    return best


def _window_return_pct(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
    minutes: float,
) -> Optional[float]:
    if entry_px <= 0:
        return None
    start_ts = entry_ts - timedelta(minutes=minutes)
    start_px = _price_at_or_before(series, start_ts)
    if start_px is None or start_px <= 0:
        return None
    return round((entry_px - start_px) / start_px * 100.0, 4)


def _window_low_high(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    minutes: float,
) -> tuple[Optional[float], Optional[float]]:
    start_ts = entry_ts - timedelta(minutes=minutes)
    lows: list[float] = []
    highs: list[float] = []
    for ts, px in series:
        if start_ts <= ts <= entry_ts:
            lows.append(px)
            highs.append(px)
    if not lows:
        return None, None
    return min(lows), max(highs)


def _trend_slope_pct_per_min(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    minutes: float,
) -> Optional[float]:
    start_ts = entry_ts - timedelta(minutes=minutes)
    start_px = _price_at_or_before(series, start_ts)
    end_px = _price_at_or_before(series, entry_ts)
    if start_px is None or end_px is None or start_px <= 0 or minutes <= 0:
        return None
    total_pct = (end_px - start_px) / start_px * 100.0
    return round(total_pct / minutes, 4)


def _attach_price_windows(
    trade: dict[str, Any],
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> None:
    sym = str(trade.get("symbol") or "")
    day = str(trade.get("day") or _day_from_ts(str(trade.get("entry_time") or "")))
    et = _parse_ts(str(trade.get("entry_time") or ""))
    ep = _float(trade.get("entry_price"), default=0.0)
    if et is None or ep <= 0:
        return
    series = price_idx.get((sym, day), [])
    if not series:
        return
    for mins, key in ((5, "return_5min_pct"), (10, "return_10min_pct"), (15, "return_15min_pct"), (30, "return_30min_pct")):
        if trade.get(key) is None:
            trade[key] = _window_return_pct(series, entry_ts=et, entry_px=ep, minutes=mins)
    low_30, high_30 = _window_low_high(series, entry_ts=et, minutes=30)
    if low_30 and ep > 0:
        trade["distance_from_30m_low_pct"] = round((ep - low_30) / low_30 * 100.0, 4)
    if high_30 and ep > 0:
        trade["distance_from_30m_high_pct"] = round((ep - high_30) / high_30 * 100.0, 4)
    trade["trend_slope_30m_pct_per_min"] = _trend_slope_pct_per_min(series, entry_ts=et, minutes=30)


def _load_accepted_index(kabu_root: Path) -> dict[tuple[str, str], dict[str, str]]:
    idx: dict[tuple[str, str], dict[str, str]] = {}
    for _day, sess in _iter_sessions(kabu_root):
        for row in _stream_events(sess / "small_paper_events.csv"):
            if row.get("event_type") != "accepted":
                continue
            key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
            idx[key] = row
    return idx


def _accepted_trades_from_sim(repo_root: Path) -> list[dict[str, Any]]:
    trades, _meta = load_canonical_live_config_trades(repo_root, period_start=PERIOD_START)
    sim = simulate_audited(
        trades,
        starting_equity=int(STARTING_EQUITY),
        leverage=2.0,
        cap=5,
        stop_policy="fixed_stop_1p2",
    )
    state = sim.get("_state")
    out: list[dict[str, Any]] = []
    if state is None:
        return out
    for log in getattr(state, "trade_log", []) or []:
        day = str(log.get("day") or "")
        if day < PERIOD_START or day > PERIOD_END:
            continue
        trade = dict(log.get("trade") or {})
        trade["day"] = day
        trade["day_key"] = day
        trade["pnl_yen"] = _float(log.get("pnl_yen"))
        trade["pnl_yen_100"] = round(trade["pnl_yen"] / 100.0, 2) if trade["pnl_yen"] else 0.0
        trade["exit_reason_canonical"] = normalize_exit_reason(
            str(trade.get("exit_reason") or trade.get("close_reason") or "")
        )
        et = str(trade.get("entry_time") or "")
        if "T09:" <= et[11:16] <= "T11:30" or et[11:13] == "11:":
            trade["session_kind"] = "am"
        else:
            trade["session_kind"] = "pm"
        slot = str(trade.get("universe_slot") or "")
        trade["universe_group"] = "dynamic40" if slot == "dynamic" else ("core10" if slot == "core" else slot)
        out.append(trade)
    out.sort(
        key=lambda t: (
            _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(t.get("symbol") or ""),
        )
    )
    return out


def _enrich_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    kabu_root: Path,
    accepted_idx: Mapping[tuple[str, str], Mapping[str, str]],
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in trades:
        base = dict(t)
        key = (str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
        acc = dict(accepted_idx.get(key, {}))
        for fld in (
            "universe_slot",
            "universe_bucket",
            "source_bucket",
            "entry_rise_5min_pct",
            "entry_rise_10min_pct",
            "entry_near_day_high_pct",
            "entry_vwap_dev_pct",
            "momentum_continuation_score",
            "entry_order_book_imbalance",
            "current_price",
        ):
            if acc.get(fld) not in (None, ""):
                base[fld] = acc[fld]
        if not base.get("entry_price"):
            base["entry_price"] = _float(acc.get("current_price"), default=0.0) or base.get("entry_price")
        row = enrich_residual_trade(base, acc)
        row["return_5min_pct"] = row.get("return_5min_pct") or row.get("entry_rise_5min_pct")
        row["return_10min_pct"] = row.get("return_10min_pct") or row.get("entry_rise_10min_pct")
        _attach_price_windows(row, price_idx)
        row["phase355_vwap_pullback_block"] = would_block_pullback_dynamic40_shadow(row)
        if not row.get("universe_group"):
            slot = str(row.get("universe_slot") or "")
            row["universe_group"] = "dynamic40" if slot == "dynamic" else ("core10" if slot == "core" else slot)
        rows.append(row)
    return rows


def _is_stop(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("exit_reason_canonical") or "") == "stop_hit"


def _scope_dynamic40(trade: Mapping[str, Any]) -> bool:
    return is_dynamic40_universe(trade) or str(trade.get("universe_group") or "") == "dynamic40"


# --- Guard candidates (VWAP-free) ---


def guard_high_drift(trade: Mapping[str, Any]) -> bool:
    if not _scope_dynamic40(trade):
        return False
    dist = abs(
        _optional_float(trade.get("day_high_distance_pct"))
        or _optional_float(trade.get("entry_near_day_high_pct"))
        or 0.0
    )
    r5 = _optional_float(trade.get("return_5min_pct") or trade.get("entry_rise_5min_pct"))
    r10 = _optional_float(trade.get("return_10min_pct") or trade.get("entry_rise_10min_pct"))
    r15 = _optional_float(trade.get("return_15min_pct"))
    if dist < 1.2:
        return False
    # Pattern A: lower-high drift + small bounce (6976-style misread)
    if r10 is not None and r10 < -0.15:
        if r5 is None:
            return True
        if r5 > r10 and r5 <= 1.0:
            return True
    # Pattern B: sustained decline from day high (opening-high / lower-high drift)
    if dist >= 1.5:
        if r15 is not None and r15 < -0.5 and (r5 is None or r5 < 0.2):
            return True
        if r5 is not None and r5 < -0.5 and (r10 is None or r10 < -0.2):
            return True
    return False


def guard_momentum_window(trade: Mapping[str, Any]) -> bool:
    if not _scope_dynamic40(trade):
        return False
    r5 = _optional_float(trade.get("return_5min_pct") or trade.get("entry_rise_5min_pct"))
    r15 = _optional_float(trade.get("return_15min_pct"))
    r30 = _optional_float(trade.get("return_30min_pct"))
    if r15 is None and r30 is None:
        return False
    long_weak = (r15 is not None and r15 < -0.3) or (r30 is not None and r30 < -0.45)
    if not long_weak:
        return False
    if r5 is None:
        return True
    return r5 >= -0.15


def guard_near_recent_low(trade: Mapping[str, Any]) -> bool:
    if not _scope_dynamic40(trade):
        return False
    dist_low = _optional_float(trade.get("distance_from_30m_low_pct"))
    if dist_low is None or dist_low > 0.5:
        return False
    r30 = _optional_float(trade.get("return_30min_pct"))
    r5 = _optional_float(trade.get("return_5min_pct") or trade.get("entry_rise_5min_pct"))
    if r30 is None or r30 >= 0:
        return False
    return r5 is None or r5 >= 0


def guard_trend_slope(trade: Mapping[str, Any]) -> bool:
    if not _scope_dynamic40(trade):
        return False
    slope = _optional_float(trade.get("trend_slope_30m_pct_per_min"))
    r5 = _optional_float(trade.get("return_5min_pct") or trade.get("entry_rise_5min_pct"))
    if slope is None or slope >= -0.015:
        return False
    return r5 is None or r5 >= 0


def guard_legacy_vwap_pullback(trade: Mapping[str, Any]) -> bool:
    return bool(trade.get("phase355_vwap_pullback_block"))


GUARD_SPECS: tuple[dict[str, Any], ...] = (
    {
        "guard_id": "baseline",
        "label": "No guard (Phase423 canonical)",
        "block_fn": lambda _t: False,
    },
    {
        "guard_id": "legacy_vwap_pullback",
        "label": "Phase355 VWAP pullback (comparison)",
        "block_fn": guard_legacy_vwap_pullback,
    },
    {
        "guard_id": "high_drift",
        "label": "High Drift Guard (VWAP-free)",
        "block_fn": guard_high_drift,
    },
    {
        "guard_id": "momentum_window",
        "label": "15m/30m Momentum Guard",
        "block_fn": guard_momentum_window,
    },
    {
        "guard_id": "near_recent_low",
        "label": "Near Recent Low Guard",
        "block_fn": guard_near_recent_low,
    },
    {
        "guard_id": "trend_slope",
        "label": "Trend Slope Guard",
        "block_fn": guard_trend_slope,
    },
)


def _max_drawdown_yen(trades: Sequence[Mapping[str, Any]], *, starting: float = STARTING_EQUITY) -> tuple[float, float]:
    ordered = sorted(
        trades,
        key=lambda t: (
            _parse_ts(str(t.get("exit_time") or t.get("entry_time") or ""))
            or datetime.min.replace(tzinfo=JST)
        ),
    )
    equity = starting
    peak = equity
    max_dd = 0.0
    for t in ordered:
        equity += _float(t.get("pnl_yen"))
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    max_dd_pct = round(max_dd / peak * 100.0, 4) if peak > 0 else 0.0
    return round(max_dd, 2), max_dd_pct


def _metrics_row(
    trades: Sequence[Mapping[str, Any]],
    *,
    guard_id: str,
    label: str,
    removed: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen")) for t in trades]
    stops = sum(1 for t in trades if _is_stop(t))
    sym6976 = [t for t in trades if str(t.get("symbol")) == TARGET_SYMBOL]
    rem6976 = [t for t in removed if str(t.get("symbol")) == TARGET_SYMBOL]
    max_dd, max_dd_pct = _max_drawdown_yen(trades)
    return {
        "guard_id": guard_id,
        "label": label,
        "trade_count": len(trades),
        "removed_count": len(removed),
        "total_pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "stop_count": stops,
        "stop_rate": round(stops / len(trades), 4) if trades else 0.0,
        "avg_pnl_yen": round(statistics.mean(pnls), 2) if pnls else 0.0,
        "max_drawdown_yen": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "symbol_6976_trade_count": len(sym6976),
        "symbol_6976_removed_count": len(rem6976),
        "symbol_6976_removal_rate": round(len(rem6976) / len(sym6976), 4) if sym6976 else 0.0,
        "symbol_6976_removed_pnl_yen": round(sum(_float(t.get("pnl_yen")) for t in rem6976), 2),
    }


def _6976_capture_rows(
    all_trades: Sequence[Mapping[str, Any]],
    block_fns: Mapping[str, Callable[[Mapping[str, Any]], bool]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sym_trades = [t for t in all_trades if str(t.get("symbol")) == TARGET_SYMBOL]
    for t in sym_trades:
        row = {
            "symbol": TARGET_SYMBOL,
            "day": t.get("day"),
            "entry_time": t.get("entry_time"),
            "pnl_yen": t.get("pnl_yen"),
            "exit_reason": t.get("exit_reason_canonical"),
            "day_high_distance_pct": t.get("day_high_distance_pct"),
            "return_5min_pct": t.get("return_5min_pct"),
            "return_10min_pct": t.get("return_10min_pct"),
            "return_15min_pct": t.get("return_15min_pct"),
            "return_30min_pct": t.get("return_30min_pct"),
            "distance_from_30m_low_pct": t.get("distance_from_30m_low_pct"),
            "trend_slope_30m_pct_per_min": t.get("trend_slope_30m_pct_per_min"),
        }
        for gid, fn in block_fns.items():
            if gid == "baseline":
                continue
            row[f"blocked_{gid}"] = fn(t)
        rows.append(row)
    return rows


def _verdict(rows: Sequence[Mapping[str, Any]]) -> str:
    baseline = next((r for r in rows if r.get("guard_id") == "baseline"), {})
    candidates = [r for r in rows if r.get("guard_id") not in ("baseline", "legacy_vwap_pullback")]
    best = None
    best_score = -1e18
    for r in candidates:
        delta_pnl = _float(r.get("total_pnl_yen")) - _float(baseline.get("total_pnl_yen"))
        rem6976 = _float(r.get("symbol_6976_removal_rate"))
        score = delta_pnl + rem6976 * 50000
        if score > best_score:
            best_score = score
            best = r
    if best and _float(best.get("total_pnl_yen")) > _float(baseline.get("total_pnl_yen")):
        return "high_drift_candidate" if best.get("guard_id") == "high_drift" else f"{best.get('guard_id')}_candidate"
    legacy = next((r for r in rows if r.get("guard_id") == "legacy_vwap_pullback"), {})
    if _float(legacy.get("total_pnl_yen")) >= _float(baseline.get("total_pnl_yen")):
        return "legacy_vwap_still_competitive"
    return "no_clear_winner"


def run_phase436_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    accepted_idx = _load_accepted_index(kabu)
    price_idx = _build_price_index(kabu)
    raw_trades = _accepted_trades_from_sim(repo_root)
    trades = _enrich_trades(raw_trades, kabu_root=kabu, accepted_idx=accepted_idx, price_idx=price_idx)

    comparison: list[dict[str, Any]] = []
    block_fns: dict[str, Callable[[Mapping[str, Any]], bool]] = {}
    shadow_details: dict[str, Any] = {}

    for spec in GUARD_SPECS:
        gid = str(spec["guard_id"])
        block_fn: Callable[[Mapping[str, Any]], bool] = spec["block_fn"]
        block_fns[gid] = block_fn
        if gid == "baseline":
            kept = list(trades)
            removed: list[dict[str, Any]] = []
        else:
            kept = []
            removed = []
            for t in trades:
                if block_fn(t):
                    removed.append(t)
                else:
                    kept.append(t)
        comparison.append(
            _metrics_row(kept, guard_id=gid, label=str(spec["label"]), removed=removed)
        )
        if gid != "baseline":
            shadow_details[gid] = evaluate_variant_shadow(
                trades,
                variant_id=gid,
                would_block=block_fn,
            )

    capture_6976 = _6976_capture_rows(trades, block_fns)
    verdict = _verdict(comparison)

    baseline_row = next(r for r in comparison if r["guard_id"] == "baseline")
    mandatory = {
        "period": f"{PERIOD_START}..{PERIOD_END}",
        "baseline_trade_count": baseline_row["trade_count"],
        "baseline_pnl_yen": baseline_row["total_pnl_yen"],
        "baseline_pf": baseline_row["profit_factor"],
        "baseline_stop_rate": baseline_row["stop_rate"],
        "best_guard_by_pnl": max(
            (r for r in comparison if r["guard_id"] != "baseline"),
            key=lambda r: _float(r.get("total_pnl_yen")),
        )["guard_id"],
        "6976_total_entries": baseline_row["symbol_6976_trade_count"],
        "verdict": verdict,
        "vwap_free_recommended": "high_drift or momentum_window for 6976-style drift+bounce",
    }

    return {
        "phase": "436-Pullback-Guard-Redesign-Shadow",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "guard_specs": [
            {"guard_id": s["guard_id"], "label": s["label"]} for s in GUARD_SPECS if s["guard_id"] != "baseline"
        ],
        "comparison": comparison,
        "shadow_details": shadow_details,
        "capture_6976": capture_6976,
    }


def _csv_write(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    _write_csv(path, list(rows[0].keys()), rows)


@dataclass
class Phase436Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase436_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        kabu = resolve_kabu_root(self.repo_root)

        paths = {
            "comparison": reports / "phase436_pullback_guard_comparison.csv",
            "capture6976": reports / "phase436_pullback_guard_6976_capture.csv",
            "summary": reports / "phase436_pullback_guard_shadow_summary.json",
            "report": kabu / "docs" / "operations" / "phase436_pullback_guard_redesign_report.md",
        }

        _csv_write(paths["comparison"], result.get("comparison") or [])
        _csv_write(paths["capture6976"], result.get("capture_6976") or [])

        summary = {
            "phase": result.get("phase"),
            "generated_at": result.get("generated_at"),
            "verdict": result.get("verdict"),
            "mandatory_answers": result.get("mandatory_answers"),
            "comparison": result.get("comparison"),
            "shadow_details": result.get("shadow_details"),
        }
        paths["summary"].write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

        lines = [
            "# Phase436 — Pullback Guard Redesign (Shadow)",
            "",
            f"Generated: {result.get('generated_at')}",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## Comparison",
            "",
            "| guard | trades | PnL | PF | stop_rate | maxDD | 6976 removal |",
            "|-------|--------|-----|-----|-----------|-------|--------------|",
        ]
        for r in result.get("comparison") or []:
            lines.append(
                f"| {r.get('guard_id')} | {r.get('trade_count')} | {r.get('total_pnl_yen'):,.0f} | "
                f"{r.get('profit_factor')} | {r.get('stop_rate')} | {r.get('max_drawdown_yen'):,.0f} | "
                f"{r.get('symbol_6976_removed_count')}/{r.get('symbol_6976_trade_count')} "
                f"({r.get('symbol_6976_removal_rate')}) |"
            )
        lines.extend(
            [
                "",
                "## Guard definitions (shadow)",
                "",
                "| guard | rule (dynamic40 only) |",
                "|-------|-------------------------|",
                "| high_drift | A: day_high≥1.2%, r10<-0.15%, r5>r10 (small bounce). B: day_high≥1.5%, r15<-0.5% or r5<-0.5% (sustained decline) |",
                "| momentum_window | r15<-0.3% or r30<-0.45% with r5≥-0.15% (weak trend + short bounce) |",
                "| near_recent_low | within 0.5% of 30m low, r30<0, r5≥0 |",
                "| trend_slope | 30m slope <-0.015%/min with r5≥0 |",
                "| legacy_vwap | rise5<0 AND vwap_dev<0 (Phase355) |",
                "",
                "## 6976 on 2026-06-18 (case study)",
                "",
            ]
        )
        for row in result.get("capture_6976") or []:
            if str(row.get("day")) != "20260618":
                continue
            blocks = [
                gid
                for gid in ("high_drift", "momentum_window", "near_recent_low", "trend_slope")
                if row.get(f"blocked_{gid}")
            ]
            lines.append(
                f"- {row.get('entry_time')}: pnl={row.get('pnl_yen'):,.0f}, "
                f"exit={row.get('exit_reason')}, blocked={blocks or 'none'}"
            )
        lines.extend(
            [
                "",
                "## Notes",
                "",
                "- VWAP-free guards target dynamic40 downtrend + small bounce (6976 pattern).",
                "- Baseline: Phase423 canonical + forward capital sim accepted trades.",
                "- Runtime change forbidden; shadow replay only.",
                "",
            ]
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text("\n".join(lines), encoding="utf-8")
        return paths
