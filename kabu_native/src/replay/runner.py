"""
Batch replay runner for kabu_native (multi-day, multi-symbol).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from replay.intraday import load_intraday_csv, resolve_intraday_csv, yahoo_csv_filename
from replay.metrics import aggregate_summary, daily_summaries, symbol_summaries, trades_to_rows


@dataclass
class ReplayRunConfig:
    start_date: str
    end_date: str
    symbols: list[str]
    data_roots: list[Path]
    output_dir: Path
    tier: str = "B"
    entry_score_min: int = 60
    require_timing_ok: bool = True
    relaxed_signal: bool = False
    synthetic_push_keep: float = 1.0
    synthetic_spread_bps: float = 8.0
    synthetic_events_per_minute: int = 10
    eod_exit_reason: str = "eod_close"
    repo_root: Path | None = None


@dataclass
class SkippedInput:
    trade_date: str
    symbol: str
    skip_reason: str
    csv_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "symbol": self.symbol,
            "skip_reason": self.skip_reason,
            "csv_path": self.csv_path,
        }


@dataclass
class ReplayRunResult:
    trades: list[Any] = field(default_factory=list)
    skipped: list[SkippedInput] = field(default_factory=list)
    output_dir: Path | None = None


def _ensure_legacy_imports(repo_root: Path) -> None:
    root_s = str(repo_root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)


def iter_trade_dates(start: str, end: str) -> list[str]:
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    if d1 < d0:
        raise ValueError(f"end_date < start_date: {start} .. {end}")
    out: list[str] = []
    cur = d0
    while cur <= d1:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def run_replay_batch(config: ReplayRunConfig) -> ReplayRunResult:
    repo_root = config.repo_root or Path(__file__).resolve().parents[3]
    _ensure_legacy_imports(repo_root)

    from src.kabu_signal_replay import (  # noqa: WPS433
        DATA_SOURCE_YAHOO_SYNTHETIC,
        push_messages_from_yahoo_df,
        events_from_push_messages,
        replay_signal_config,
        replay_symbol_events,
        yahoo_symbol_code,
    )

    signal_cfg = replay_signal_config(relaxed=config.relaxed_signal)
    all_trades: list[Any] = []
    skipped: list[SkippedInput] = []

    for trade_date in iter_trade_dates(config.start_date, config.end_date):
        for symbol in config.symbols:
            sym_display = symbol if symbol.endswith(".T") else f"{yahoo_symbol_code(symbol)}.T"
            csv_path = resolve_intraday_csv(config.data_roots, trade_date, sym_display)
            if csv_path is None:
                skipped.append(
                    SkippedInput(
                        trade_date=trade_date,
                        symbol=sym_display,
                        skip_reason="missing_intraday_csv",
                        csv_path=str(_expected_path(config.data_roots, trade_date, sym_display)),
                    )
                )
                continue

            loaded = load_intraday_csv(csv_path)
            if not loaded.ok:
                skipped.append(
                    SkippedInput(
                        trade_date=trade_date,
                        symbol=sym_display,
                        skip_reason=loaded.skip_reason or "load_failed",
                        csv_path=str(csv_path),
                    )
                )
                continue

            msgs = push_messages_from_yahoo_df(
                loaded.df,
                symbol=sym_display,
                keep_fraction=config.synthetic_push_keep,
                seed=hash((trade_date, sym_display)) % (2**31),
                spread_bps=config.synthetic_spread_bps,
                events_per_minute=config.synthetic_events_per_minute,
            )
            events = events_from_push_messages(msgs, source=DATA_SOURCE_YAHOO_SYNTHETIC)
            result = replay_symbol_events(
                sym_display,
                events,
                tier=config.tier,
                entry_score_min=config.entry_score_min,
                require_timing_ok=config.require_timing_ok,
                data_source=DATA_SOURCE_YAHOO_SYNTHETIC,
                eod_exit_reason=config.eod_exit_reason,
                signal_cfg=signal_cfg,
            )

            for t in result.trades:
                all_trades.append(_TradeWithDate(t, trade_date))

    config.output_dir.mkdir(parents=True, exist_ok=True)
    _write_outputs(config.output_dir, all_trades, skipped, config)

    return ReplayRunResult(trades=all_trades, skipped=skipped, output_dir=config.output_dir)


@dataclass
class _TradeWithDate:
    _inner: Any
    trade_date: str

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def to_row(self) -> dict[str, Any]:
        row = self._inner.to_row()
        row["trade_date"] = self.trade_date
        return row


def _csv_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, dict):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    return out


def _expected_path(roots: list[Path], trade_date: str, symbol: str) -> Path:
    fname = yahoo_csv_filename(symbol)
    if roots:
        return roots[0] / trade_date / fname
    return Path(trade_date) / fname


def _write_outputs(
    out_dir: Path,
    trades: Sequence[Any],
    skipped: list[SkippedInput],
    config: ReplayRunConfig,
) -> None:
    import csv

    trade_rows = trades_to_rows(trades)
    if trade_rows:
        fields = list(trade_rows[0].keys())
    else:
        fields = [
            "trade_date",
            "symbol",
            "entry_time",
            "entry_price",
            "exit_time",
            "exit_price",
            "pnl_pct",
            "exit_reason",
            "max_favorable_excursion_pct",
            "max_adverse_excursion_pct",
            "elapsed_min",
            "signal_score_at_entry",
            "data_source",
        ]

    with (out_dir / "trades.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in trade_rows:
            w.writerow(row)

    daily = daily_summaries(trades)
    if daily:
        d_fields = list(daily[0].keys())
        with (out_dir / "daily_summary.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=d_fields)
            w.writeheader()
            for row in daily:
                w.writerow(_csv_row(row))

    sym = symbol_summaries(trades)
    if sym:
        s_fields = list(sym[0].keys())
        with (out_dir / "symbol_summary.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=s_fields)
            w.writeheader()
            for row in sym:
                w.writerow(_csv_row(row))

    skipped_rows = [s.to_dict() for s in skipped]
    with (out_dir / "skipped_inputs.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["trade_date", "symbol", "skip_reason", "csv_path"])
        w.writeheader()
        w.writerows(skipped_rows)

    meta = {
        "component": "kabu_native.run_replay",
        "generated_at_local": datetime.now().isoformat(timespec="seconds"),
        "start_date": config.start_date,
        "end_date": config.end_date,
        "symbols": config.symbols,
        "tier": config.tier,
        "entry_score_min": config.entry_score_min,
        "data_roots": [str(p) for p in config.data_roots],
    }
    agg = aggregate_summary(trades, meta=meta, skipped=skipped_rows)
    (out_dir / "aggregate_summary.json").write_text(
        json.dumps(agg, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_replay_config(path: Path, *, native_root: Path, repo_root: Path) -> dict[str, Any]:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"replay config must be a mapping: {path}")

    roots: list[Path] = []
    for item in raw.get("data_roots") or []:
        p = Path(str(item))
        if not p.is_absolute():
            p = (repo_root / p).resolve()
        roots.append(p)
    if not roots:
        roots = [
            (native_root / "data" / "intraday_1m").resolve(),
            (repo_root / "data" / "intraday_1m").resolve(),
        ]

    raw["data_roots"] = roots
    return raw
