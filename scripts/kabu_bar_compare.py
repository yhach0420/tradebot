#!/usr/bin/env python3
"""
Yahoo 1 分足 CSV と kabu PUSH 由来 1 分足の品質・シグナル差分を定量化する。

入力:
  - Yahoo: data/intraday_1m/<day>/<symbol>.csv
  - kabu: PUSH JSONL（kabu_push_probe 出力）または kabu 1 分足 CSV
  - 検証用: Yahoo から合成 PUSH（--synthetic-push-keep）で疎密をシミュレート

paper_trade / 監視プロセスは不要。

例::
    python scripts/kabu_bar_compare.py \\
        --yahoo-csv data/intraday_1m/2026-05-15/9984.T.csv \\
        --synthetic-push-keep 0.35

    python scripts/kabu_bar_compare.py --batch-day 2026-05-15 \\
        --symbols 1321.T,9984.T,5803.T \\
        --synthetic-push-keep-low 0.25 --synthetic-push-keep-high 0.75
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_yahoo_csv(path: Path) -> pd.DataFrame:
    sys.path.insert(0, str(_project_root()))
    from src.signal_engine import normalize_ohlcv_dataframe

    return normalize_ohlcv_dataframe(pd.read_csv(path))


def _push_messages_from_yahoo(
    yahoo: pd.DataFrame,
    *,
    keep_fraction: float = 1.0,
    seed: int = 0,
    emit_high_low: bool = True,
) -> list[dict[str, Any]]:
    """Yahoo 1 分足各行を board 相当 PUSH に変換（検証用。実 PUSH ではない）。"""
    rng = random.Random(seed)
    msgs: list[dict[str, Any]] = []
    cum = 0.0
    num = 0.0
    den = 0.0

    for _, row in yahoo.iterrows():
        if keep_fraction < 1.0 and rng.random() > keep_fraction:
            continue
        vol = float(row["volume"]) if pd.notna(row["volume"]) else 0.0
        cum += max(0.0, vol)
        tp = (float(row["high"]) + float(row["low"]) + float(row["close"])) / 3.0
        if vol > 0:
            num += tp * vol
            den += vol
        vwap = (num / den) if den > 0 else None

        ts = row["timestamp"]
        if hasattr(ts, "isoformat"):
            tstr = ts.isoformat()
        else:
            tstr = str(ts)

        def _one(price: float) -> dict[str, Any]:
            m: dict[str, Any] = {
                "CurrentPrice": price,
                "CurrentPriceTime": tstr,
                "TradingVolume": cum,
            }
            if vwap is not None:
                m["VWAP"] = vwap
            return m

        msgs.append(_one(float(row["close"])))
        if emit_high_low and float(row["high"]) != float(row["low"]):
            if float(row["high"]) != float(row["close"]):
                msgs.append(_one(float(row["high"])))
            if float(row["low"]) != float(row["close"]):
                msgs.append(_one(float(row["low"])))
    return msgs


def _ohlcv_from_push_messages(msgs: list[dict[str, Any]]) -> pd.DataFrame:
    from src.kabu_bar_builder import (
        MinuteBarBuilderFromPush,
        floor_minute_utc,
        parse_push_time_to_utc,
        vwap_from_push_field,
    )

    builder = MinuteBarBuilderFromPush()
    completed: list[Any] = []
    vwap_by_minute: dict[datetime, float] = {}
    sample_count: dict[datetime, int] = {}

    for msg in msgs:
        ts = parse_push_time_to_utc(msg)
        if ts is not None:
            m = floor_minute_utc(ts)
            sample_count[m] = sample_count.get(m, 0) + 1
            v = vwap_from_push_field(msg)
            if v is not None:
                vwap_by_minute[m] = v
        completed.extend(builder.feed(msg))
    last = builder.flush()
    if last is not None:
        completed.append(last)

    out_rows = []
    for b in completed:
        ts = b.minute_start_utc
        out_rows.append(
            {
                "timestamp": ts,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume_delta,
                "vwap": vwap_by_minute.get(ts),
                "push_samples": sample_count.get(ts, 0),
            }
        )
    return pd.DataFrame(out_rows)


def _load_kabu_jsonl(path: Path) -> pd.DataFrame:
    msgs: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            msgs.append(json.loads(line))
    return _ohlcv_from_push_messages(msgs)


def _diff_stats(a: pd.Series, b: pd.Series, *, name: str) -> dict[str, Any]:
    d = pd.to_numeric(a, errors="coerce") - pd.to_numeric(b, errors="coerce")
    d = d.dropna()
    if d.empty:
        return {"field": name, "count": 0}
    abs_d = d.abs()
    return {
        "field": name,
        "count": int(len(d)),
        "mean_diff": float(d.mean()),
        "mean_abs_diff": float(abs_d.mean()),
        "max_abs_diff": float(abs_d.max()),
        "median_abs_diff": float(abs_d.median()),
        "p95_abs_diff": float(abs_d.quantile(0.95)),
    }


@dataclass
class CompareReport:
    symbol: str
    yahoo_csv: str
    kabu_source: str
    aligned_rows: int
    yahoo_rows: int
    kabu_rows: int
    ohlcv_stats: list[dict[str, Any]] = field(default_factory=list)
    signal_summary: dict[str, Any] = field(default_factory=dict)
    push_density: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


def compare_yahoo_vs_kabu(
    yahoo: pd.DataFrame,
    kabu: pd.DataFrame,
    *,
    symbol: str,
    yahoo_path: str,
    kabu_source: str,
    vwap_mode_yahoo: str = "session_typical",
    vwap_mode_kabu: str = "session_typical",
) -> tuple[CompareReport, pd.DataFrame]:
    from src.signal_engine import compare_signal_eval_runs, eval_signals_on_ohlcv_dataframe

    merged = pd.merge(
        yahoo,
        kabu,
        on="timestamp",
        how="inner",
        suffixes=("_yahoo", "_kabu"),
    )

    ohlcv_stats = []
    for col in ("open", "high", "low", "close", "volume"):
        ohlcv_stats.append(
            _diff_stats(merged[f"{col}_yahoo"], merged[f"{col}_kabu"], name=col)
        )
    if "vwap_yahoo" in merged.columns and "vwap_kabu" in merged.columns:
        ohlcv_stats.append(_diff_stats(merged["vwap_yahoo"], merged["vwap_kabu"], name="vwap"))
    elif "vwap_kabu" in merged.columns:
        ohlcv_stats.append(_diff_stats(merged["close_yahoo"], merged["vwap_kabu"], name="close_vs_kabu_vwap"))

    y_eval, _ = eval_signals_on_ohlcv_dataframe(yahoo, vwap_mode=vwap_mode_yahoo)
    k_eval, _ = eval_signals_on_ohlcv_dataframe(kabu, vwap_mode=vwap_mode_kabu)
    sig = compare_signal_eval_runs(y_eval, k_eval, suffixes=("_yahoo", "_kabu"))

    if "breakout_cross_now_yahoo" in sig.columns and "breakout_cross_now_kabu" in sig.columns:
        breakout_mismatch = int(
            (
                sig["breakout_cross_now_yahoo"].fillna(False).astype(bool)
                ^ sig["breakout_cross_now_kabu"].fillna(False).astype(bool)
            ).sum()
        )
    else:
        breakout_mismatch = 0

    entry_tol = 0.01  # yen
    entry_mismatch = 0
    if "entry_candidate_yahoo" in sig.columns and "entry_candidate_kabu" in sig.columns:
        ey = pd.to_numeric(sig["entry_candidate_yahoo"], errors="coerce")
        ek = pd.to_numeric(sig["entry_candidate_kabu"], errors="coerce")
        both = ey.notna() & ek.notna()
        entry_mismatch = int((both & (ey - ek).abs() > entry_tol).sum())

    if "all_timing_gates_pass_yahoo" in sig.columns and "all_timing_gates_pass_kabu" in sig.columns:
        timing_mismatch = int(
            (
                sig["all_timing_gates_pass_yahoo"].fillna(False).astype(bool)
                ^ sig["all_timing_gates_pass_kabu"].fillna(False).astype(bool)
            ).sum()
        )
    else:
        timing_mismatch = 0

    if "signal_score_yahoo" in sig.columns and "signal_score_kabu" in sig.columns:
        score_mismatch = int(
            (
                sig["signal_score_yahoo"].fillna(0).astype(int)
                != sig["signal_score_kabu"].fillna(0).astype(int)
            ).sum()
        )
    else:
        score_mismatch = 0

    r5_diff = None
    if "recent_5m_high_yahoo" in sig.columns and "recent_5m_high_kabu" in sig.columns:
        d = pd.to_numeric(sig["recent_5m_high_yahoo"], errors="coerce") - pd.to_numeric(
            sig["recent_5m_high_kabu"], errors="coerce"
        )
        d = d.dropna()
        if len(d):
            r5_diff = {
                "mean_abs_diff": float(d.abs().mean()),
                "max_abs_diff": float(d.abs().max()),
            }

    push_density: dict[str, Any] = {}
    if "push_samples_kabu" in merged.columns:
        ps = pd.to_numeric(merged["push_samples_kabu"], errors="coerce").fillna(0)
        push_density = {
            "mean_push_samples_per_minute": float(ps.mean()),
            "minutes_with_zero_push": int((ps <= 0).sum()),
            "minutes_with_one_push": int((ps == 1).sum()),
        }
    elif "push_samples" in kabu.columns:
        ps = pd.to_numeric(kabu["push_samples"], errors="coerce").fillna(0)
        push_density = {
            "mean_push_samples_per_minute": float(ps.mean()),
            "minutes_with_zero_push": int((ps <= 0).sum()),
        }

    y_breakouts = int(y_eval["breakout_cross_now"].fillna(False).sum())
    k_breakouts = int(k_eval["breakout_cross_now"].fillna(False).sum())

    signal_summary = {
        "eval_rows": int(len(sig)),
        "breakout_cross_yahoo": y_breakouts,
        "breakout_cross_kabu": k_breakouts,
        "breakout_timing_mismatch_rows": breakout_mismatch,
        "entry_candidate_mismatch_rows_gt_1yen": entry_mismatch,
        "all_timing_gates_mismatch_rows": timing_mismatch,
        "signal_score_mismatch_rows": score_mismatch,
        "recent_5m_high_diff": r5_diff,
        "vwap_distance_pct_mean_abs_diff": None,
    }
    if "vwap_distance_pct_yahoo" in sig.columns and "vwap_distance_pct_kabu" in sig.columns:
        vd = pd.to_numeric(sig["vwap_distance_pct_yahoo"], errors="coerce") - pd.to_numeric(
            sig["vwap_distance_pct_kabu"], errors="coerce"
        )
        vd = vd.dropna()
        if len(vd):
            signal_summary["vwap_distance_pct_mean_abs_diff"] = float(vd.abs().mean())

    detail = merged.copy()
    detail = pd.merge(detail, sig, on="timestamp", how="left")

    report = CompareReport(
        symbol=symbol,
        yahoo_csv=yahoo_path,
        kabu_source=kabu_source,
        aligned_rows=int(len(merged)),
        yahoo_rows=int(len(yahoo)),
        kabu_rows=int(len(kabu)),
        ohlcv_stats=ohlcv_stats,
        signal_summary=signal_summary,
        push_density=push_density,
    )
    return report, detail


def _symbol_total_volume(path: Path) -> float:
    df = pd.read_csv(path, usecols=["volume"])
    return float(pd.to_numeric(df["volume"], errors="coerce").fillna(0).sum())


def _report_to_dict(r: CompareReport) -> dict[str, Any]:
    return {
        "symbol": r.symbol,
        "yahoo_csv": r.yahoo_csv,
        "kabu_source": r.kabu_source,
        "aligned_rows": r.aligned_rows,
        "yahoo_rows": r.yahoo_rows,
        "kabu_rows": r.kabu_rows,
        "ohlcv_stats": r.ohlcv_stats,
        "signal_summary": r.signal_summary,
        "push_density": r.push_density,
        "meta": r.meta,
    }


def main() -> int:
    root = _project_root()
    sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser(description="Yahoo vs kabu 1分足品質比較")
    parser.add_argument("--yahoo-csv", type=Path, help="Yahoo 1 分足 CSV")
    parser.add_argument("--kabu-csv", type=Path, help="kabu 合成 1 分足 CSV")
    parser.add_argument("--kabu-jsonl", type=Path, help="kabu PUSH JSONL")
    parser.add_argument(
        "--synthetic-push-keep",
        type=float,
        default=None,
        metavar="FRAC",
        help="Yahoo 行から合成 PUSH を生成するときの保持率 (0-1)",
    )
    parser.add_argument("--synthetic-seed", type=int, default=42)

    parser.add_argument("--batch-day", type=str, help="data/intraday_1m/<day> 一括")
    parser.add_argument("--symbols", type=str, default="", help="カンマ区切り。空なら batch 内全銘柄")
    parser.add_argument(
        "--synthetic-push-keep-low",
        type=float,
        default=0.25,
        help="出来高少銘柄 tier 用の合成 PUSH 保持率",
    )
    parser.add_argument(
        "--synthetic-push-keep-high",
        type=float,
        default=0.75,
        help="出来高多銘柄 tier 用の合成 PUSH 保持率",
    )
    parser.add_argument("--volume-split", type=int, default=2, help="出来高 tier 分割数（2=高低）")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--write-detail-csv", action="store_true", help="銘柄ごと詳細 CSV")

    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    day_out = datetime.now().strftime("%Y%m%d")
    out_dir = args.out_dir or (root / "results" / "kabu_bar_compare" / day_out)
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: list[CompareReport] = []

    def run_one(
        yahoo_path: Path,
        *,
        symbol: str,
        kabu_csv: Path | None,
        kabu_jsonl: Path | None,
        keep: float | None,
        tier_label: str = "",
    ) -> None:
        yahoo = _load_yahoo_csv(yahoo_path)
        if kabu_jsonl is not None:
            kabu = _load_kabu_jsonl(kabu_jsonl)
            src = f"jsonl:{kabu_jsonl}"
        elif kabu_csv is not None:
            kabu = _load_yahoo_csv(kabu_csv)
            src = f"csv:{kabu_csv}"
        elif keep is not None:
            msgs = _push_messages_from_yahoo(
                yahoo, keep_fraction=keep, seed=args.synthetic_seed + hash(symbol) % 10000
            )
            kabu = _ohlcv_from_push_messages(msgs)
            src = f"synthetic_push_keep={keep}"
        else:
            raise ValueError("kabu 入力がありません（--kabu-csv / --kabu-jsonl / --synthetic-push-keep）")

        rep, detail = compare_yahoo_vs_kabu(
            yahoo,
            kabu,
            symbol=symbol,
            yahoo_path=str(yahoo_path),
            kabu_source=src,
        )
        rep.meta["volume_tier"] = tier_label
        rep.meta["synthetic_keep"] = keep
        reports.append(rep)

        if args.write_detail_csv:
            tag = symbol.replace(".", "_")
            detail.to_csv(out_dir / f"detail_{tag}_{stamp}.csv", index=False)

    if args.batch_day:
        day_dir = root / "data" / "intraday_1m" / args.batch_day
        if not day_dir.is_dir():
            print(f"batch day not found: {day_dir}", file=sys.stderr)
            return 2
        sym_list = [s.strip() for s in args.symbols.split(",") if s.strip()]
        paths = [day_dir / f"{s}.csv" for s in sym_list] if sym_list else sorted(day_dir.glob("*.csv"))
        vols = [(p, _symbol_total_volume(p)) for p in paths]
        vols.sort(key=lambda x: x[1])
        n = len(vols)
        if n == 0:
            print("no symbols", file=sys.stderr)
            return 2
        split = max(1, args.volume_split)
        chunk = max(1, n // split)
        low_paths = [p for p, _ in vols[:chunk]]
        high_paths = [p for p, _ in vols[-chunk:]]

        for p in low_paths:
            run_one(
                p,
                symbol=p.stem,
                kabu_csv=args.kabu_csv,
                kabu_jsonl=args.kabu_jsonl,
                keep=args.synthetic_push_keep if args.synthetic_push_keep is not None else args.synthetic_push_keep_low,
                tier_label="low_volume",
            )
        for p in high_paths:
            run_one(
                p,
                symbol=p.stem,
                kabu_csv=args.kabu_csv,
                kabu_jsonl=args.kabu_jsonl,
                keep=args.synthetic_push_keep if args.synthetic_push_keep is not None else args.synthetic_push_keep_high,
                tier_label="high_volume",
            )
    elif args.yahoo_csv:
        sym = args.yahoo_csv.stem
        run_one(
            args.yahoo_csv.resolve(),
            symbol=sym,
            kabu_csv=args.kabu_csv.resolve() if args.kabu_csv else None,
            kabu_jsonl=args.kabu_jsonl.resolve() if args.kabu_jsonl else None,
            keep=args.synthetic_push_keep,
            tier_label="single",
        )
    else:
        parser.error("--yahoo-csv または --batch-day が必要です")
        return 2

    summary_path = out_dir / f"kabu_bar_compare_{stamp}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "synthetic_push は Yahoo 1分足からの検証用擬似PUSH。"
            "実機 JSONL がある場合は --kabu-jsonl を指定してください。"
        ),
        "reports": [_report_to_dict(r) for r in reports],
    }
    low = [r for r in reports if r.meta.get("volume_tier") == "low_volume"]
    high = [r for r in reports if r.meta.get("volume_tier") == "high_volume"]

    def _agg(rs: list[CompareReport], key: str) -> Optional[float]:
        vals = []
        for r in rs:
            for s in r.ohlcv_stats:
                if s.get("field") == key and s.get("count", 0) > 0:
                    vals.append(s["mean_abs_diff"])
        return float(np.mean(vals)) if vals else None

    tier_summary = {
        "low_volume_symbols": [r.symbol for r in low],
        "high_volume_symbols": [r.symbol for r in high],
        "low_mean_abs_close_diff": _agg(low, "close"),
        "high_mean_abs_close_diff": _agg(high, "close"),
        "low_breakout_mismatch_total": sum(
            r.signal_summary.get("breakout_timing_mismatch_rows", 0) for r in low
        ),
        "high_breakout_mismatch_total": sum(
            r.signal_summary.get("breakout_timing_mismatch_rows", 0) for r in high
        ),
    }
    payload["tier_summary"] = tier_summary
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(summary_path.relative_to(root))
    for r in reports:
        close_stat = next((s for s in r.ohlcv_stats if s.get("field") == "close"), {})
        print(
            f"{r.symbol} tier={r.meta.get('volume_tier')} aligned={r.aligned_rows} "
            f"close_mad={close_stat.get('mean_abs_diff')} "
            f"breakout_mismatch={r.signal_summary.get('breakout_timing_mismatch_rows')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
