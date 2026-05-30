"""
Phase180: Symbol-level trade diagnostics from small_paper_events (backward-tolerant).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _boolish(val: Any) -> bool:
    if val is True:
        return True
    if val is False or val is None or val == "":
        return False
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "y")


def _iter_event_rows(session_dir: Path) -> list[dict[str, Any]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    csv_path = session_dir / "small_paper_events.csv"
    rows: list[dict[str, Any]] = []
    if jsonl.is_file():
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                rows.append(dict(row))
    return rows


def _classify_verdict(
    *,
    score: Optional[float],
    threshold: Optional[float],
    price: Optional[float],
    trading_value: Optional[float],
    turnover: Optional[float],
) -> str:
    low_price = price is not None and price < 100
    very_low_price = price is not None and price < 50
    thin_value = trading_value is not None and trading_value < 1e8
    very_thin_value = trading_value is not None and trading_value < 5e7
    low_turnover = turnover is not None and turnover < 0.002

    if threshold is not None and score is not None:
        if score < threshold:
            return "exclude_candidate"
        if very_low_price or very_thin_value:
            return "watch"
        if low_price or thin_value or low_turnover:
            return "watch"
        return "keep"

    if very_low_price or very_thin_value:
        return "exclude_candidate"
    if thin_value or low_turnover or low_price:
        return "watch"
    if trading_value is not None and trading_value >= 1e9:
        return "keep"
    return "watch"


@dataclass
class _SymAcc:
    symbol: str
    accepted_count: int = 0
    observer_exit_count: int = 0
    trailing_mfe_exit_count: int = 0
    stop_hit_count: int = 0
    overlap_replaced_review_count: int = 0
    pnl_pcts: list[float] = field(default_factory=list)
    mfe_pcts: list[float] = field(default_factory=list)
    mae_pcts: list[float] = field(default_factory=list)
    hold_secs: list[float] = field(default_factory=list)
    trading_values: list[float] = field(default_factory=list)
    turnover_proxies: list[float] = field(default_factory=list)
    low_liquidity_shadow_reject_count: int = 0
    suitability_score_last: Optional[float] = None
    suitability_threshold_last: Optional[float] = None
    current_price_last: Optional[float] = None

    def to_row(self) -> dict[str, Any]:
        total_pnl = sum(self.pnl_pcts) if self.pnl_pcts else None
        avg_pnl = (total_pnl / len(self.pnl_pcts)) if self.pnl_pcts else None
        avg_mfe = (
            sum(self.mfe_pcts) / len(self.mfe_pcts) if self.mfe_pcts else None
        )
        avg_mae = (
            sum(self.mae_pcts) / len(self.mae_pcts) if self.mae_pcts else None
        )
        avg_hold = (
            sum(self.hold_secs) / len(self.hold_secs) if self.hold_secs else None
        )
        avg_tv = (
            sum(self.trading_values) / len(self.trading_values)
            if self.trading_values
            else None
        )
        avg_to = (
            sum(self.turnover_proxies) / len(self.turnover_proxies)
            if self.turnover_proxies
            else None
        )
        verdict = _classify_verdict(
            score=self.suitability_score_last,
            threshold=self.suitability_threshold_last,
            price=self.current_price_last,
            trading_value=avg_tv,
            turnover=avg_to,
        )
        return {
            "symbol": self.symbol,
            "accepted_count": self.accepted_count,
            "observer_exit_count": self.observer_exit_count,
            "trailing_mfe_exit_count": self.trailing_mfe_exit_count,
            "stop_hit_count": self.stop_hit_count,
            "overlap_replaced_review_count": self.overlap_replaced_review_count,
            "avg_pnl_pct": round(avg_pnl, 4) if avg_pnl is not None else None,
            "total_pnl_pct": round(total_pnl, 4) if total_pnl is not None else None,
            "avg_mfe_pct": round(avg_mfe, 4) if avg_mfe is not None else None,
            "avg_mae_pct": round(avg_mae, 4) if avg_mae is not None else None,
            "avg_hold_sec": round(avg_hold, 1) if avg_hold is not None else None,
            "avg_trading_value": round(avg_tv, 2) if avg_tv is not None else None,
            "avg_turnover_proxy": round(avg_to, 6) if avg_to is not None else None,
            "low_liquidity_shadow_reject_count": self.low_liquidity_shadow_reject_count,
            "verdict": verdict,
        }


def aggregate_symbol_diagnostics(
    session_dirs: Sequence[Path],
) -> dict[str, Any]:
    acc: dict[str, _SymAcc] = {}
    sessions_meta: list[dict[str, Any]] = []

    for session_dir in session_dirs:
        if not session_dir.is_dir():
            continue
        rows = _iter_event_rows(session_dir)
        sessions_meta.append(
            {
                "session_dir": str(session_dir).replace("\\", "/"),
                "event_rows": len(rows),
                "has_observer_exit": any(
                    str(r.get("event_type") or "") == "observer_exit" for r in rows
                ),
            }
        )
        for r in rows:
            sym = str(r.get("symbol") or "").strip()
            if not sym:
                continue
            et = str(r.get("event_type") or "")
            if sym not in acc:
                acc[sym] = _SymAcc(symbol=sym)
            a = acc[sym]

            if et == "accepted":
                a.accepted_count += 1
                sc = _float(r.get("daytrade_suitability_score"))
                if sc is not None:
                    a.suitability_score_last = sc
                th = _float(r.get("daytrade_suitability_threshold"))
                if th is not None:
                    a.suitability_threshold_last = th
                px = _float(r.get("current_price"))
                if px is not None:
                    a.current_price_last = px
                tv = _float(r.get("trading_value"))
                if tv is not None:
                    a.trading_values.append(tv)
                to = _float(r.get("turnover_proxy"))
                if to is not None:
                    a.turnover_proxies.append(to)
                if _boolish(r.get("low_liquidity_shadow_rejected")):
                    a.low_liquidity_shadow_reject_count += 1
                continue

            if et != "observer_exit":
                continue

            a.observer_exit_count += 1
            reason = str(r.get("exit_reason") or r.get("structural_exit_reason") or "")
            if reason == "trailing_mfe_exit":
                a.trailing_mfe_exit_count += 1
            if _boolish(r.get("stop_hit")) or reason == "stop_hit":
                a.stop_hit_count += 1
            if _boolish(r.get("overlap_replaced_review")) or reason == "overlap_replaced_review":
                a.overlap_replaced_review_count += 1

            pnl = _float(r.get("pnl_pct"))
            if pnl is not None:
                a.pnl_pcts.append(pnl)
            mfe = _float(r.get("peak_mfe_pct")) or _float(r.get("rolling_mfe_pct"))
            if mfe is not None:
                a.mfe_pcts.append(mfe)
            mae = _float(r.get("rolling_mae_pct"))
            if mae is not None:
                a.mae_pcts.append(mae)
            hs = _float(r.get("hold_sec"))
            if hs is not None:
                a.hold_secs.append(hs)

    symbols = [a.to_row() for a in sorted(acc.values(), key=lambda x: x.symbol)]
    return {
        "phase": 180,
        "symbols": symbols,
        "sessions": sessions_meta,
        "symbol_count": len(symbols),
    }


def discover_session_dirs_for_day(repo_root: Path, day_stamp: str) -> list[Path]:
    base = repo_root / "kabu_native" / "results" / "small_paper" / day_stamp
    if not base.is_dir():
        return []
    return sorted(
        [p for p in base.iterdir() if p.is_dir() and p.name.startswith("live_session_")],
        key=lambda p: p.name,
    )
