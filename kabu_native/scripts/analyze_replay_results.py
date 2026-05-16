#!/usr/bin/env python3
"""
Structural analysis of kabu_native replay trades (multi-symbol, multi-day).

例::
    python kabu_native/scripts/analyze_replay_results.py
    python kabu_native/scripts/analyze_replay_results.py --replay-root kabu_native/results/replay
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else None

TIME_BANDS: list[tuple[str, int, int]] = [
    ("09:00-09:30", 9 * 60, 9 * 60 + 30),
    ("09:30-10:30", 9 * 60 + 30, 10 * 60 + 30),
    ("10:30-11:30", 10 * 60 + 30, 11 * 60 + 30),
    ("12:30-13:30", 12 * 60 + 30, 13 * 60 + 30),
    ("13:30-14:30", 13 * 60 + 30, 14 * 60 + 30),
    ("14:30-15:30", 14 * 60 + 30, 15 * 60 + 30),
]

EXIT_REASON_GROUPS = {
    "breakout_failure": "breakout_failure",
    "hard_stop": "hard_stop",
    "time_stop": "time_stop",
    "vwap_reclaim_failure": "vwap",
    "vwap": "vwap",
    "eod_close": "eod_close",
    "board_imbalance_deterioration": "other",
    "high_update_stall": "other",
    "spread_widening": "other",
    "push_density_drop": "other",
}


@dataclass
class TradeRow:
    symbol: str
    trade_date: str
    entry_time: str
    exit_time: str
    pnl_pct: float
    exit_reason: str
    exit_reason_group: str
    time_band_jst: str
    signal_score_at_entry: int
    elapsed_min: float
    max_loss_pct: float
    source_run: str

    @classmethod
    def from_csv(cls, row: dict[str, str], source_run: str) -> TradeRow:
        reason = str(row.get("exit_reason") or "unknown")
        group = EXIT_REASON_GROUPS.get(reason, "other")
        entry = str(row.get("entry_time") or "")
        return cls(
            symbol=str(row.get("symbol") or ""),
            trade_date=str(row.get("trade_date") or entry[:10]),
            entry_time=entry,
            exit_time=str(row.get("exit_time") or ""),
            pnl_pct=float(row.get("pnl_pct") or 0),
            exit_reason=reason,
            exit_reason_group=group,
            time_band_jst=classify_time_band_jst(entry),
            signal_score_at_entry=int(float(row.get("signal_score_at_entry") or 0)),
            elapsed_min=float(row.get("elapsed_min") or 0),
            max_loss_pct=float(row.get("max_adverse_excursion_pct") or row.get("pnl_pct") or 0),
            source_run=source_run,
        )


def classify_time_band_jst(entry_time_iso: str) -> str:
    if not entry_time_iso:
        return "unknown"
    try:
        dt = datetime.fromisoformat(entry_time_iso.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if JST is not None:
        dt = dt.astimezone(JST)
    minutes = dt.hour * 60 + dt.minute
    for label, start, end in TIME_BANDS:
        if start <= minutes < end:
            return label
    if 11 * 60 + 30 <= minutes < 12 * 60 + 30:
        return "lunch_break"
    return "outside_bands"


def find_latest_run_dir(replay_root: Path) -> Path | None:
    candidates = [p.parent for p in replay_root.rglob("trades.csv")]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_trades_from_run(run_dir: Path) -> list[TradeRow]:
    path = run_dir / "trades.csv"
    if not path.is_file():
        return []
    run_id = run_dir.name
    trades: list[TradeRow] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            trades.append(TradeRow.from_csv(row, run_id))
    return trades


def load_all_trades(
    replay_root: Path,
    *,
    latest_run_only: bool = False,
) -> tuple[list[TradeRow], Path | None]:
    if latest_run_only:
        run_dir = find_latest_run_dir(replay_root)
        if run_dir is None:
            return [], None
        return load_trades_from_run(run_dir), run_dir
    trades: list[TradeRow] = []
    for path in sorted(replay_root.rglob("trades.csv")):
        trades.extend(load_trades_from_run(path.parent))
    return trades, None


def load_universe_meta(path: Path) -> dict[str, dict[str, float | None]]:
    meta: dict[str, dict[str, float | None]] = {}
    if not path.is_file():
        return meta
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol", "")).strip()
            if not sym:
                continue
            if not sym.endswith(".T"):
                sym = f"{sym}.T"
            tv, sp = row.get("trading_value"), row.get("spread_bps")
            meta[sym] = {
                "trading_value": float(tv) if tv not in (None, "") else None,
                "spread_bps": float(sp) if sp not in (None, "") else None,
            }
    return meta


def infer_score_tier(score: int) -> str:
    if score >= 80:
        return "tier_proxy_A"
    if score >= 60:
        return "tier_proxy_B"
    return "tier_proxy_below"


def _tertile_labels(values: dict[str, float]) -> dict[str, str]:
    items = [(k, v) for k, v in values.items() if v is not None and v == v]
    if len(items) < 3:
        return {k: "mid" for k in values}
    items.sort(key=lambda x: x[1])
    n = len(items)
    out: dict[str, str] = {}
    for i, (k, _) in enumerate(items):
        q = i / max(1, n - 1)
        if q < 1 / 3:
            out[k] = "low"
        elif q < 2 / 3:
            out[k] = "mid"
        else:
            out[k] = "high"
    return out


def by_tier_proxy(rows: list[TradeRow]) -> list[dict[str, Any]]:
    by: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        by[infer_score_tier(r.signal_score_at_entry)].append(r)
    return [{"tier_proxy": k, **summarize_block(v)} for k, v in sorted(by.items())]


def by_bucket(
    rows: list[TradeRow],
    symbol_bucket: dict[str, str],
    bucket_key: str,
) -> list[dict[str, Any]]:
    by: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        by[symbol_bucket.get(r.symbol, "unknown")].append(r)
    return [{bucket_key: k, **summarize_block(v)} for k, v in sorted(by.items())]


def pf_distribution_by_symbol(rows: list[TradeRow]) -> list[dict[str, Any]]:
    by_sym: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        by_sym[r.symbol].append(r)
    out: list[dict[str, Any]] = []
    for sym, tr in sorted(by_sym.items()):
        block = summarize_block(tr)
        bf = sum(1 for t in tr if t.exit_reason == "breakout_failure")
        opening = [t for t in tr if t.time_band_jst == "09:00-09:30"]
        out.append(
            {
                "symbol": sym,
                "trades": block["trades"],
                "total_pnl_pct": block["total_pnl_pct"],
                "profit_factor": block.get("profit_factor"),
                "win_rate": block["win_rate"],
                "breakout_failure_share": round(bf / len(tr), 4) if tr else None,
                "opening_band_trades": len(opening),
                "opening_band_pnl": round(sum(t.pnl_pct for t in opening), 4) if opening else 0.0,
            }
        )
    return out


def trades_insufficiency(
    run_dir: Path | None,
    trades: list[TradeRow],
    *,
    expected_symbols: list[str] | None = None,
) -> dict[str, Any]:
    sym_trades = Counter(r.symbol for r in trades)
    expected = expected_symbols or sorted(sym_trades.keys())
    skipped: list[dict[str, str]] = []
    if run_dir and (run_dir / "skipped_inputs.csv").is_file():
        with (run_dir / "skipped_inputs.csv").open(encoding="utf-8", newline="") as f:
            skipped = list(csv.DictReader(f))
    return {
        "expected_symbol_count": len(expected),
        "symbols_with_trades": len(sym_trades),
        "symbols_zero_trades": [s for s in expected if sym_trades.get(s, 0) == 0],
        "symbols_low_trades_lt3": [s for s in expected if 0 < sym_trades.get(s, 0) < 3],
        "skipped_input_rows": len(skipped),
        "skipped_by_reason": dict(Counter(r.get("skip_reason", "") for r in skipped)),
        "median_trades_per_symbol": statistics.median(list(sym_trades.values())) if sym_trades else 0,
    }


def opening_band_generalization(rows: list[TradeRow]) -> dict[str, Any]:
    opening = "09:00-09:30"
    by_sym: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        if r.time_band_jst == opening:
            by_sym[r.symbol].append(r)
    sym_pnl = {sym: sum(t.pnl_pct for t in tr) for sym, tr in by_sym.items()}
    negative_syms = [s for s, p in sym_pnl.items() if p < 0]
    n = len(by_sym)
    overall_opening = [r for r in rows if r.time_band_jst == opening]
    return {
        "opening_band": opening,
        "symbols_with_opening_trades": n,
        "symbols_negative_opening_pnl": negative_syms,
        "share_symbols_negative": round(len(negative_syms) / n, 4) if n else None,
        "overall_opening": summarize_block(overall_opening),
        "per_symbol_opening_pnl": [
            {"symbol": s, "trades": len(by_sym[s]), "total_pnl_pct": round(sym_pnl[s], 4)}
            for s in sorted(sym_pnl, key=lambda x: sym_pnl[x])
        ],
        "is_universal_problem": len(negative_syms) >= max(1, int(0.6 * n)) if n else None,
    }


def breakout_failure_generalization(rows: list[TradeRow]) -> dict[str, Any]:
    by_sym: dict[str, float] = {}
    for sym in {r.symbol for r in rows}:
        tr = [r for r in rows if r.symbol == sym]
        bf = sum(1 for t in tr if t.exit_reason == "breakout_failure")
        by_sym[sym] = bf / len(tr) if tr else 0.0
    overall_bf = sum(1 for r in rows if r.exit_reason == "breakout_failure") / len(rows) if rows else 0
    high_bf = [s for s, sh in by_sym.items() if sh >= 0.7]
    return {
        "overall_breakout_failure_share": round(overall_bf, 4),
        "symbols_bf_share_ge_70pct": high_bf,
        "share_symbols_high_bf": round(len(high_bf) / len(by_sym), 4) if by_sym else None,
        "is_universal": len(high_bf) >= max(1, int(0.7 * len(by_sym))) if by_sym else None,
        "per_symbol_bf_share": [
            {"symbol": s, "breakout_failure_share": round(by_sym[s], 4)} for s in sorted(by_sym)
        ],
    }


def symbol_concentration(rows: list[TradeRow]) -> dict[str, Any]:
    sym_block = by_symbol(rows)
    return {
        "overfitting": overfitting_checks(rows),
        "worst_pnl_symbols": sorted(sym_block, key=lambda x: x["total_pnl_pct"])[:8],
        "most_active_symbols": sorted(sym_block, key=lambda x: -x["trades"])[:8],
    }


def summarize_block(rows: list[TradeRow]) -> dict[str, Any]:
    if not rows:
        return {
            "trades": 0,
            "win_rate": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "median_pnl_pct": None,
            "max_loss_pct": None,
            "avg_loss_pct": None,
            "profit_factor": None,
        }
    pnls = [r.pnl_pct for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        pf: float | None = gross_profit / gross_loss
    elif gross_profit > 0:
        pf = None
    else:
        pf = None
    return {
        "trades": len(rows),
        "win_rate": len(wins) / len(rows),
        "total_pnl_pct": sum(pnls),
        "avg_pnl_pct": statistics.mean(pnls),
        "median_pnl_pct": statistics.median(pnls),
        "max_loss_pct": min(pnls),
        "avg_loss_pct": statistics.mean(losses) if losses else None,
        "profit_factor": pf,
    }


def exit_reason_detail(rows: list[TradeRow]) -> list[dict[str, Any]]:
    by: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        by[r.exit_reason].append(r)
    out: list[dict[str, Any]] = []
    for reason in sorted(by, key=lambda k: -len(by[k])):
        block = summarize_block(by[reason])
        out.append({"exit_reason": reason, "exit_reason_group": EXIT_REASON_GROUPS.get(reason, "other"), **block})
    return out


def exit_reason_group_detail(rows: list[TradeRow]) -> list[dict[str, Any]]:
    by: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        by[r.exit_reason_group].append(r)
    out: list[dict[str, Any]] = []
    for grp in sorted(by, key=lambda k: -len(by[k])):
        block = summarize_block(by[grp])
        out.append({"exit_reason_group": grp, **block})
    return out


def by_symbol(rows: list[TradeRow]) -> list[dict[str, Any]]:
    by: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        by[r.symbol].append(r)
    out: list[dict[str, Any]] = []
    for sym in sorted(by, key=lambda s: sum(t.pnl_pct for t in by[s])):
        tr = by[sym]
        block = summarize_block(tr)
        reasons = Counter(t.exit_reason for t in tr)
        out.append(
            {
                "symbol": sym,
                **block,
                "exit_reason_counts": dict(reasons),
            }
        )
    return out


def by_day(rows: list[TradeRow]) -> list[dict[str, Any]]:
    by: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        by[r.trade_date].append(r)
    out: list[dict[str, Any]] = []
    for day in sorted(by):
        tr = by[day]
        block = summarize_block(tr)
        out.append(
            {
                "trade_date": day,
                **block,
                "winning_day": block["total_pnl_pct"] > 0,
            }
        )
    return out


def by_time_band(rows: list[TradeRow]) -> list[dict[str, Any]]:
    by: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        by[r.time_band_jst].append(r)
    order = [b[0] for b in TIME_BANDS] + ["lunch_break", "outside_bands", "unknown"]
    out: list[dict[str, Any]] = []
    for band in order:
        if band not in by:
            continue
        out.append({"time_band_jst": band, **summarize_block(by[band])})
    for band in sorted(by):
        if band not in {x["time_band_jst"] for x in out}:
            out.append({"time_band_jst": band, **summarize_block(by[band])})
    return out


def overfitting_checks(rows: list[TradeRow]) -> dict[str, Any]:
    if not rows:
        return {"status": "no_trades"}

    total_pnl = sum(r.pnl_pct for r in rows)
    by_sym: dict[str, float] = defaultdict(float)
    by_day: dict[str, float] = defaultdict(float)
    for r in rows:
        by_sym[r.symbol] += r.pnl_pct
        by_day[r.trade_date] += r.pnl_pct

    def _concentration(pnl_map: dict[str, float], label: str) -> dict[str, Any]:
        if not pnl_map:
            return {}
        abs_total = sum(abs(v) for v in pnl_map.values()) or 1e-9
        best_key = max(pnl_map, key=lambda k: abs(pnl_map[k]))
        share = abs(pnl_map[best_key]) / abs_total
        dominant_positive = max(pnl_map.items(), key=lambda x: x[1])
        dominant_negative = min(pnl_map.items(), key=lambda x: x[1])
        return {
            f"{label}_count": len(pnl_map),
            f"{label}_largest_abs_share": round(share, 4),
            f"{label}_largest_abs_key": best_key,
            f"{label}_best_total_pnl": {"key": dominant_positive[0], "pnl": round(dominant_positive[1], 6)},
            f"{label}_worst_total_pnl": {"key": dominant_negative[0], "pnl": round(dominant_negative[1], 6)},
            f"{label}_single_dominates": share >= 0.5,
        }

    reasons = Counter(r.exit_reason for r in rows)
    top_reason, top_count = reasons.most_common(1)[0]
    reason_share = top_count / len(rows)

    sym_pos = [s for s, p in by_sym.items() if p > 0]
    day_pos = [d for d, p in by_day.items() if p > 0]

    flags: list[str] = []
    sym_c = _concentration(by_sym, "symbol")
    day_c = _concentration(by_day, "day")
    if sym_c.get("symbol_single_dominates"):
        flags.append("pnl_concentrated_in_one_symbol")
    if day_c.get("day_single_dominates"):
        flags.append("pnl_concentrated_in_one_day")
    if reason_share >= 0.7:
        flags.append("exit_reason_heavily_skewed")
    if len(sym_pos) == 1 and total_pnl > 0:
        flags.append("all_profit_from_single_symbol")
    if len(day_pos) == 1 and total_pnl > 0:
        flags.append("all_profit_from_single_day")
    if total_pnl <= 0 and sym_c.get("symbol_largest_abs_share", 0) >= 0.6:
        flags.append("losses_driven_by_few_symbols")

    return {
        "total_pnl_pct": round(total_pnl, 6),
        "symbol": sym_c,
        "day": day_c,
        "exit_reason_top": {"reason": top_reason, "count": top_count, "share": round(reason_share, 4)},
        "exit_reason_counts": dict(reasons),
        "risk_flags": flags,
        "interpretation": _interpret_flags(flags, total_pnl, reason_share),
    }


def _interpret_flags(flags: list[str], total_pnl: float, reason_share: float) -> list[str]:
    notes: list[str] = []
    if not flags:
        notes.append("集中リスクは限定的だが、サンプル数・銘柄数を増やして再確認すること。")
    if "exit_reason_heavily_skewed" in flags:
        notes.append(
            f"EXIT が1理由に偏っている（最大 {reason_share:.0%}）。"
            "kabu_exit_v1 の breakout_failure / hard_stop 閾値を構造単位で見直す候補。"
        )
    if "pnl_concentrated_in_one_symbol" in flags or "all_profit_from_single_symbol" in flags:
        notes.append("銘柄依存が高い。9984 等への個別パッチではなく、流動性クラスタ単位の調整を優先。")
    if "pnl_concentrated_in_one_day" in flags or "all_profit_from_single_day" in flags:
        notes.append("特定日のレジーム依存。日付ホールドアウトまたはレジームフィルタを検討。")
    if "losses_driven_by_few_symbols" in flags:
        notes.append("損失が少数銘柄に集中。universe / morning_screen のゲート強化を検討。")
    if total_pnl < 0 and "exit_reason_heavily_skewed" in flags:
        notes.append("全体マイナスかつ breakout_failure 偏重 → エントリー直後の失敗判定が厳しすぎる可能性。")
    return notes


def flatten_for_csv(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    overall = payload["overall"]
    rows.append({"section": "overall", "key": "all", **{k: overall.get(k) for k in _METRIC_KEYS}})

    for item in payload.get("by_symbol", []):
        rows.append(
            {
                "section": "by_symbol",
                "key": item["symbol"],
                **{k: item.get(k) for k in _METRIC_KEYS},
                "extra": json.dumps(item.get("exit_reason_counts"), ensure_ascii=False),
            }
        )
    for item in payload.get("by_day", []):
        rows.append(
            {
                "section": "by_day",
                "key": item["trade_date"],
                **{k: item.get(k) for k in _METRIC_KEYS},
                "extra": json.dumps({"winning_day": item.get("winning_day")}, ensure_ascii=False),
            }
        )
    for item in payload.get("by_exit_reason", []):
        rows.append(
            {
                "section": "by_exit_reason",
                "key": item["exit_reason"],
                **{k: item.get(k) for k in _METRIC_KEYS},
                "extra": item.get("exit_reason_group"),
            }
        )
    for item in payload.get("by_exit_reason_group", []):
        rows.append(
            {
                "section": "by_exit_reason_group",
                "key": item["exit_reason_group"],
                **{k: item.get(k) for k in _METRIC_KEYS},
            }
        )
    for item in payload.get("by_time_band", []):
        rows.append(
            {
                "section": "by_time_band",
                "key": item["time_band_jst"],
                **{k: item.get(k) for k in _METRIC_KEYS},
            }
        )
    ov = payload.get("overfitting", {})
    rows.append(
        {
            "section": "overfitting",
            "key": "summary",
            "trades": payload["overall"].get("trades"),
            "extra": json.dumps(ov, ensure_ascii=False),
        }
    )
    return rows


_METRIC_KEYS = (
    "trades",
    "win_rate",
    "total_pnl_pct",
    "avg_pnl_pct",
    "median_pnl_pct",
    "max_loss_pct",
    "avg_loss_pct",
    "profit_factor",
)


def main() -> int:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]

    parser = argparse.ArgumentParser(description="kabu_native リプレイ構造分析")
    parser.add_argument(
        "--replay-root",
        type=Path,
        default=native_root / "results" / "replay",
    )
    parser.add_argument("--output-stamp", default=None)
    parser.add_argument(
        "--all-runs",
        action="store_true",
        help="全 replay ランを合算（既定: 最新ランのみ）",
    )
    parser.add_argument(
        "--universe-meta",
        type=Path,
        default=native_root / "data" / "universe" / "universe_20260516.csv",
    )
    parser.add_argument(
        "--expected-symbols",
        type=Path,
        default=native_root / "data" / "universe" / "universe_intraday_full.csv",
    )
    args = parser.parse_args()

    replay_root = args.replay_root if args.replay_root.is_absolute() else (repo_root / args.replay_root)
    stamp = args.output_stamp or datetime.now().strftime("%Y%m%d")
    latest_only = not args.all_runs

    trades, run_dir = load_all_trades(replay_root, latest_run_only=latest_only)

    if not trades:
        print(f"trades.csv が見つかりません: {replay_root}", file=sys.stderr)
        return 2

    umeta_path = args.universe_meta if args.universe_meta.is_absolute() else (repo_root / args.universe_meta)
    umeta = load_universe_meta(umeta_path)
    spread_vals = {s: m["spread_bps"] for s, m in umeta.items() if m.get("spread_bps") is not None}
    liq_vals = {s: m["trading_value"] for s, m in umeta.items() if m.get("trading_value") is not None}
    spread_bucket = _tertile_labels({k: float(v) for k, v in spread_vals.items()})
    liq_bucket = _tertile_labels({k: float(v) for k, v in liq_vals.items()})

    exp_path = args.expected_symbols if args.expected_symbols.is_absolute() else (repo_root / args.expected_symbols)
    expected_syms: list[str] = []
    if exp_path.is_file():
        with exp_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                sym = str(row.get("symbol", "")).strip()
                if sym:
                    expected_syms.append(sym if sym.endswith(".T") else f"{sym}.T")

    payload: dict[str, Any] = {
        "meta": {
            "component": "kabu_native.analyze_replay_results",
            "generated_at_local": datetime.now().isoformat(timespec="seconds"),
            "replay_root": str(replay_root.relative_to(repo_root)),
            "latest_run_only": latest_only,
            "run_dir": str(run_dir.relative_to(repo_root)) if run_dir else None,
            "trade_count": len(trades),
            "unique_symbols": sorted({t.symbol for t in trades}),
            "unique_dates": sorted({t.trade_date for t in trades}),
            "source_runs": sorted({t.source_run for t in trades}),
        },
        "overall": summarize_block(trades),
        "symbol_concentration": symbol_concentration(trades),
        "by_symbol": by_symbol(trades),
        "by_day": by_day(trades),
        "by_exit_reason": exit_reason_detail(trades),
        "by_exit_reason_group": exit_reason_group_detail(trades),
        "by_time_band": by_time_band(trades),
        "by_tier_proxy": by_tier_proxy(trades),
        "by_spread_bucket": by_bucket(trades, spread_bucket, "spread_bucket"),
        "by_liquidity_bucket": by_bucket(trades, liq_bucket, "liquidity_bucket"),
        "pf_by_symbol": pf_distribution_by_symbol(trades),
        "trades_insufficiency": trades_insufficiency(run_dir, trades, expected_symbols=expected_syms or None),
        "opening_band_analysis": opening_band_generalization(trades),
        "breakout_failure_analysis": breakout_failure_generalization(trades),
        "overfitting": overfitting_checks(trades),
        "next_adjustment_hints": _next_hints(trades),
    }

    out_dir = native_root / "results" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"structure_analysis_{stamp}.csv"
    json_path = out_dir / f"structure_analysis_{stamp}.json"

    flat = flatten_for_csv(payload)
    fields = ["section", "key", *_METRIC_KEYS, "extra"]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    o = payload["overall"]
    print(f"trades={o['trades']} total_pnl={o['total_pnl_pct']:.4f} win_rate={o['win_rate']}")
    print(f"flags={payload['overfitting'].get('risk_flags')}")
    print(f"CSV: {csv_path.relative_to(repo_root)}")
    print(f"JSON: {json_path.relative_to(repo_root)}")
    return 0


def _next_hints(rows: list[TradeRow]) -> list[str]:
    """Actionable next steps from structure (not per-symbol patches)."""
    hints: list[str] = []
    bands = by_time_band(rows)
    worst_band = min(bands, key=lambda b: b.get("total_pnl_pct", 0)) if bands else None
    if worst_band and worst_band.get("trades", 0) >= 3:
        hints.append(
            f"時間帯 {worst_band['time_band_jst']} が最悪（total_pnl={worst_band['total_pnl_pct']:.4f}）。"
            "エントリー時間帯ゲートまたは tier 別閾値の検討。"
        )
    sym = by_symbol(rows)
    if sym:
        worst_sym = min(sym, key=lambda s: s["total_pnl_pct"])
        if worst_sym["trades"] >= 5:
            hints.append(
                f"銘柄 {worst_sym['symbol']} で損失集中（{worst_sym['trades']} trades）。"
                "個別パラメータではなく universe 流動性帯で見る。"
            )
    grp = exit_reason_group_detail(rows)
    bf = next((g for g in grp if g["exit_reason_group"] == "breakout_failure"), None)
    if bf and bf.get("trades", 0) >= 5 and (bf.get("avg_pnl_pct") or 0) < 0:
        hints.append("breakout_failure 系 EXIT が主因 → fail_window / fail_buffer のスイープ（全銘柄共通）。")
    if len({r.symbol for r in rows}) < 5:
        hints.append(
            "分析銘柄数が少ない。data_inventory の 27 銘柄で run_replay を再実行してから本分析を繰り返す。"
        )
    return hints


if __name__ == "__main__":
    raise SystemExit(main())
