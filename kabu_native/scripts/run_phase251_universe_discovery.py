#!/usr/bin/env python3
"""
Phase251: Universe改善の事前分析（review-only）

対象:
  - 全 push_replay
  - 全 replay
  - 全 live

入力（読み取り専用）:
  - kabu_native/results/replay/**/trades.csv
  - kabu_native/results/small_paper/**/structural_trades.csv

出力:
  - kabu_native/results/reports/phase251_universe_discovery.json

禁止:
  - ENTRY変更 / YAML変更 / 本番変更（本スクリプトは集計のみ）
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]


JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None


@dataclass(frozen=True)
class TradeLike:
    symbol: str
    entry_price: float | None
    pnl_pct: float
    source_kind: str  # push_replay | replay | live | unknown
    source_path: str


def _now_jst_iso() -> str:
    dt = datetime.now().astimezone(JST) if JST else datetime.now()
    return dt.isoformat(timespec="seconds")


def _pf(rows: Iterable[TradeLike]) -> float | None:
    gross_profit = 0.0
    gross_loss = 0.0
    any_row = False
    for r in rows:
        any_row = True
        if r.pnl_pct > 0:
            gross_profit += r.pnl_pct
        elif r.pnl_pct < 0:
            gross_loss += abs(r.pnl_pct)
    if not any_row:
        return None
    if gross_loss > 0:
        return gross_profit / gross_loss
    if gross_profit > 0:
        return None
    return None


def _summarize(rows: list[TradeLike]) -> dict[str, Any]:
    if not rows:
        return {"trade_count": 0, "pnl_pct_sum": 0.0, "profit_factor": None}
    return {
        "trade_count": len(rows),
        "pnl_pct_sum": sum(r.pnl_pct for r in rows),
        "profit_factor": _pf(rows),
    }


def _infer_kind(path: Path) -> str:
    s = str(path).replace("\\", "/").lower()
    if "/results/replay/" in s:
        return "replay"
    if "/results/" in s and "/push_replay_" in s:
        return "push_replay"
    if "/results/" in s and "/live_" in s:
        return "live"
    return "unknown"


def _norm_symbol(sym: str) -> str:
    sym = str(sym or "").strip()
    if not sym:
        return ""
    return sym if sym.endswith(".T") else f"{sym}.T"


def _iter_replay_trades_csv(path: Path) -> Iterable[TradeLike]:
    kind = _infer_kind(path)
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            symbol = _norm_symbol(row.get("symbol") or "")
            if not symbol:
                continue
            entry_price = row.get("entry_price")
            try:
                ep = float(entry_price) if entry_price not in (None, "") else None
            except ValueError:
                ep = None
            try:
                pnl = float(row.get("pnl_pct") or 0.0)
            except ValueError:
                pnl = 0.0
            yield TradeLike(
                symbol=symbol,
                entry_price=ep,
                pnl_pct=pnl,
                source_kind=kind,
                source_path=str(path),
            )


def _iter_structural_trades_csv(path: Path) -> Iterable[TradeLike]:
    kind = _infer_kind(path)
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            symbol = _norm_symbol(row.get("symbol") or "")
            if not symbol:
                continue
            entry_price = row.get("entry_price")
            try:
                ep = float(entry_price) if entry_price not in (None, "") else None
            except ValueError:
                ep = None
            try:
                pnl = float(row.get("realized_pnl_pct") or 0.0)
            except ValueError:
                pnl = 0.0
            yield TradeLike(
                symbol=symbol,
                entry_price=ep,
                pnl_pct=pnl,
                source_kind=kind,
                source_path=str(path),
            )


def _price_band(entry_price: float | None) -> str:
    if entry_price is None:
        return "unknown"
    p = float(entry_price)
    if p < 300:
        return "<300"
    if p < 1000:
        return "300-1000"
    if p < 3000:
        return "1000-3000"
    if p < 10000:
        return "3000-10000"
    return "10000+"


TV_BANDS: list[tuple[str, float, float | None]] = [
    ("<1e9", 0.0, 1e9),
    ("1e9-5e9", 1e9, 5e9),
    ("5e9-2e10", 5e9, 2e10),
    ("2e10-5e10", 2e10, 5e10),
    ("5e10-1e11", 5e10, 1e11),
    ("1e11+", 1e11, None),
]


def _tv_band(tv: float | None) -> str:
    if tv is None:
        return "unknown"
    v = float(tv)
    for label, lo, hi in TV_BANDS:
        if v >= lo and (hi is None or v < hi):
            return label
    return "unknown"


def _read_latest_universe_meta(native_root: Path) -> dict[str, dict[str, Any]]:
    """
    Prefer the latest kabu_native/data/universe/universe_YYYYMMDD.csv for trading_value/volume/current_price.
    """
    universe_dir = native_root / "data" / "universe"
    candidates = sorted(universe_dir.glob("universe_*.csv"))
    if not candidates:
        return {}

    def _has_trading_value(p: Path) -> bool:
        try:
            with p.open(encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, [])
            return "trading_value" in header
        except OSError:
            return False

    rich = [p for p in candidates if _has_trading_value(p)]
    pool = rich or candidates
    latest = max(pool, key=lambda p: p.stat().st_mtime)
    meta: dict[str, dict[str, Any]] = {}
    with latest.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm_symbol(row.get("symbol") or "")
            if not sym:
                continue
            def _f(k: str) -> float | None:
                v = row.get(k)
                if v in (None, ""):
                    return None
                try:
                    return float(v)
                except ValueError:
                    return None
            meta[sym] = {
                "universe_meta_source": str(latest),
                "current_price": _f("current_price"),
                "trading_value": _f("trading_value"),
                "trading_volume": _f("trading_volume"),
                "spread_bps": _f("spread_bps"),
                "exchange_name": row.get("exchange_name") or None,
                "passed": (str(row.get("passed") or "").lower() == "true"),
                "exclude_reasons": row.get("exclude_reasons") or None,
            }
    return meta


def _read_jpx_master(repo_root: Path) -> dict[str, dict[str, Any]]:
    """
    data/jpx/tradable_symbols.csv provides sector_33_name and scale_category (size proxy).
    """
    path = repo_root / "data" / "jpx" / "tradable_symbols.csv"
    if not path.is_file():
        return {}
    meta: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm_symbol(row.get("symbol") or "")
            if not sym:
                continue
            meta[sym] = {
                "market": row.get("market") or None,
                "name": row.get("name") or None,
                "sector_33_code": row.get("sector_33_code") or None,
                "sector_33_name": row.get("sector_33_name") or None,
                "scale_category": row.get("scale_category") or None,
                "is_active": (str(row.get("is_active") or "").lower() == "true"),
            }
    return meta


def _group(rows: list[TradeLike], key_fn) -> list[dict[str, Any]]:
    by: dict[str, list[TradeLike]] = {}
    for r in rows:
        k = str(key_fn(r) or "unknown")
        by.setdefault(k, []).append(r)
    out: list[dict[str, Any]] = []
    for k in sorted(by):
        out.append({"key": k, **_summarize(by[k])})
    return out


def _group2(rows: list[TradeLike], key1_fn, key2_fn) -> list[dict[str, Any]]:
    by: dict[tuple[str, str], list[TradeLike]] = {}
    for r in rows:
        k1 = str(key1_fn(r) or "unknown")
        k2 = str(key2_fn(r) or "unknown")
        by.setdefault((k1, k2), []).append(r)
    out: list[dict[str, Any]] = []
    for (k1, k2) in sorted(by):
        out.append({"key1": k1, "key2": k2, **_summarize(by[(k1, k2)])})
    return out


def main() -> int:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]

    parser = argparse.ArgumentParser(description="Phase251 universe discovery (review-only)")
    parser.add_argument(
        "--results-root",
        type=Path,
        default=native_root / "results",
        help="kabu_native/results",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=native_root / "results" / "reports" / "phase251_universe_discovery.json",
    )
    args = parser.parse_args()

    results_root = args.results_root if args.results_root.is_absolute() else (repo_root / args.results_root)
    out_path = args.out if args.out.is_absolute() else (repo_root / args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    replay_paths = sorted((results_root / "replay").rglob("trades.csv"))
    small_paths = sorted((results_root / "small_paper").rglob("structural_trades.csv"))

    trades: list[TradeLike] = []
    for p in replay_paths:
        trades.extend(list(_iter_replay_trades_csv(p)))
    for p in small_paths:
        trades.extend(list(_iter_structural_trades_csv(p)))

    if not trades:
        print(f"No trades found under: {results_root}", file=sys.stderr)
        return 2

    u_meta = _read_latest_universe_meta(native_root)
    jpx = _read_jpx_master(repo_root)

    def tv_of(sym: str) -> float | None:
        m = u_meta.get(sym)
        if not m:
            return None
        v = m.get("trading_value")
        return float(v) if v is not None else None

    def sector_of(sym: str) -> str:
        return str((jpx.get(sym) or {}).get("sector_33_name") or "unknown")

    def scale_of(sym: str) -> str:
        return str((jpx.get(sym) or {}).get("scale_category") or "unknown")

    payload: dict[str, Any] = {
        "phase": 251,
        "purpose": "Universe改善の事前分析（何を採用するかではなく、何が勝っているか）",
        "generated_at_jst": _now_jst_iso(),
        "inputs": {
            "results_root": str(results_root),
            "replay_trades_csv_count": len(replay_paths),
            "small_paper_structural_trades_csv_count": len(small_paths),
            "universe_meta_loaded": bool(u_meta),
            "jpx_master_loaded": bool(jpx),
            "universe_meta_source": next(iter(u_meta.values())).get("universe_meta_source") if u_meta else None,
        },
        "constraints": {
            "review_only": True,
            "no_entry_change": True,
            "no_yaml_change": True,
            "no_production_change": True,
        },
        "overall": _summarize(trades),
        "overall_by_source_kind": [
            {"source_kind": k, **_summarize([t for t in trades if t.source_kind == k])}
            for k in sorted({t.source_kind for t in trades})
        ],
        "by_symbol": [
            {"symbol": sym, **_summarize([t for t in trades if t.symbol == sym])}
            for sym in sorted({t.symbol for t in trades})
        ],
        "by_symbol_by_source_kind": _group2(
            trades,
            key1_fn=lambda t: t.source_kind,
            key2_fn=lambda t: t.symbol,
        ),
        "by_price_band": _group(trades, key_fn=lambda t: _price_band(t.entry_price)),
        "by_price_band_by_source_kind": _group2(
            trades,
            key1_fn=lambda t: t.source_kind,
            key2_fn=lambda t: _price_band(t.entry_price),
        ),
        "by_trading_value_band": _group(trades, key_fn=lambda t: _tv_band(tv_of(t.symbol))),
        "by_trading_value_band_by_source_kind": _group2(
            trades,
            key1_fn=lambda t: t.source_kind,
            key2_fn=lambda t: _tv_band(tv_of(t.symbol)),
        ),
        "by_industry_33": _group(trades, key_fn=lambda t: sector_of(t.symbol)),
        "by_industry_33_by_source_kind": _group2(
            trades,
            key1_fn=lambda t: t.source_kind,
            key2_fn=lambda t: sector_of(t.symbol),
        ),
        "by_scale_category_proxy": _group(trades, key_fn=lambda t: scale_of(t.symbol)),
        "by_scale_category_proxy_by_source_kind": _group2(
            trades,
            key1_fn=lambda t: t.source_kind,
            key2_fn=lambda t: scale_of(t.symbol),
        ),
        "notes": [
            "PF は pnl_pct の gross_profit / gross_loss（loss=abs）で計算（loss=0 の場合は null）。",
            "出来高代金別は universe_*.csv の trading_value を使用（銘柄静的メタ）。",
            "時価総額は直接取得していないため、JPX の scale_category（TOPIX Core30/Large70/Mid400/Small 等）を proxy として出力。",
        ],
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote: {out_path}")
    print(f"trades={payload['overall']['trade_count']} pnl_sum={payload['overall']['pnl_pct_sum']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

