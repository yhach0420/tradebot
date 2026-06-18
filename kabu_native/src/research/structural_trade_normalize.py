"""
Shared structural_trades.csv normalization for research forward shadows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from research.market_sector_heat import _norm_symbol
from research.phase382_capital_constrained_backtest import _float
from research.phase400_holding_time_audit import enrich_trade


def normalize_structural_trade_row(
    row: Mapping[str, Any],
    *,
    day: str,
    session: str,
) -> Optional[dict[str, Any]]:
    trade = dict(row)
    sym = _norm_symbol(
        trade.get("symbol")
        or trade.get("Symbol")
        or trade.get("code")
        or ""
    )
    entry_time = (
        str(trade.get("entry_time") or "").strip()
        or str(trade.get("open_time") or "").strip()
        or str(trade.get("timestamp") or "").strip()
    )
    exit_time = (
        str(trade.get("exit_time") or "").strip()
        or str(trade.get("close_time") or "").strip()
    )
    if not sym or not entry_time:
        return None

    trade["symbol"] = sym
    trade["day"] = day
    trade["session"] = session
    trade["entry_time"] = entry_time
    trade["exit_time"] = exit_time
    trade["exit_reason"] = (
        trade.get("exit_reason")
        or trade.get("close_reason")
        or trade.get("exit_reason_bucket")
        or ""
    )
    if trade.get("pnl_yen_100") in (None, ""):
        raw = trade.get("pnl_yen_100_raw")
        if raw not in (None, ""):
            trade["pnl_yen_100"] = raw
        else:
            trade["pnl_yen_100"] = _compute_pnl_yen_100(trade)
    trade["position_cap_accepted"] = True
    return enrich_trade(trade)


def _compute_pnl_yen_100(row: Mapping[str, Any]) -> float:
    from replay.pnl_yen import compute_pnl_yen_100

    entry_px = _float(row.get("entry_price")) or 0.0
    close_px = _float(row.get("close_price")) or _float(row.get("exit_price")) or entry_px
    if entry_px > 0 and close_px > 0:
        return round(compute_pnl_yen_100(entry_px, close_px), 2)
    pct = _float(row.get("realized_pnl_pct")) or _float(row.get("pnl_pct")) or 0.0
    return round(entry_px * 100.0 * pct / 100.0, 2) if entry_px > 0 else 0.0


def resolve_kabu_root(repo_root: Path) -> Path:
    repo_root = repo_root.resolve()
    nested = repo_root / "kabu_native"
    if nested.is_dir() and (nested / "results").is_dir():
        # Monorepo layout: tradebotfile/ may also have a legacy results/ tree.
        if (nested / "results" / "reports").is_dir() or (nested / "results" / "small_paper").is_dir():
            return nested
    if (repo_root / "results").is_dir():
        return repo_root
    if nested.is_dir() and (nested / "results").is_dir():
        return nested
    return nested if nested.is_dir() else repo_root


def resolve_reports_dir(repo_root: Path) -> Path:
    kabu = resolve_kabu_root(repo_root)
    return kabu / "results" / "reports"


def copy_outputs_to_daily_research(
    repo_root: Path,
    day: str,
    paths: Mapping[str, Path],
) -> list[str]:
    """Copy research outputs into ``results/daily/YYYYMMDD/research/``."""
    import shutil

    warnings: list[str] = []
    dest_dir = resolve_kabu_root(repo_root) / "results" / "daily" / str(day) / "research"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for path in paths.values():
            src = Path(path)
            if not src.is_file():
                warnings.append(f"daily_research_copy_skip_missing:{src}")
                continue
            shutil.copy2(src, dest_dir / src.name)
    except OSError as exc:
        warnings.append(f"daily_research_copy_failed:{type(exc).__name__}:{exc}")
    return warnings
