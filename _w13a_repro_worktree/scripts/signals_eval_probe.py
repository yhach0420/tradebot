#!/usr/bin/env python3
"""
1 分足 OHLCV（CSV）だけで yahoo_kabu_watch の「エントリータイミング」ゲートを再現し、
Yahoo 足 vs kabu/PUSH 合成足などを比較する。

paper_trade や yahoo_kabu_watch の起動は不要。

例::
    # Yahoo 寄りのキャッシュ 1 本だけ
    python scripts/signals_eval_probe.py --csv data/intraday_1m/2026-05-15/9984.T.csv --label yahoo

    # 2 本比較（出力に _yahoo / _kabu サフィックス列 + 差分列）
    python scripts/signals_eval_probe.py \\
        --compare \\
        --yahoo-csv data/intraday_1m/2026-05-15/9984.T.csv \\
        --kabu-csv path/to/kabu_9984_1m.csv \\
        --yahoo-vwap session_typical --kabu-vwap session_typical
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_ohlcv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return df


def _enriched_compare(comp: pd.DataFrame) -> pd.DataFrame:
    """両系統が揃う行だけ数値差分・ゲート不一致列を追加。"""
    out = comp.copy()
    yh = "recent_5m_high_yahoo"
    kh = "recent_5m_high_kabu"
    if yh in out.columns and kh in out.columns:
        out["recent_5m_high_diff"] = pd.to_numeric(out[yh], errors="coerce") - pd.to_numeric(
            out[kh], errors="coerce"
        )
    yvw = "vwap_distance_pct_yahoo"
    kvw = "vwap_distance_pct_kabu"
    if yvw in out.columns and kvw in out.columns:
        out["vwap_distance_pct_diff"] = pd.to_numeric(out[yvw], errors="coerce") - pd.to_numeric(
            out[kvw], errors="coerce"
        )

    gy = "all_timing_gates_pass_yahoo"
    gk = "all_timing_gates_pass_kabu"
    if gy in out.columns and gk in out.columns:
        y_ok = out[gy].fillna(False).astype(bool)
        k_ok = out[gk].fillna(False).astype(bool)
        out["timing_gate_mismatch"] = y_ok ^ k_ok

    by = "breakout_cross_now_yahoo"
    bk = "breakout_cross_now_kabu"
    if by in out.columns and bk in out.columns:
        out["breakout_cross_mismatch"] = out[by].fillna(False).astype(bool) ^ out[bk].fillna(
            False
        ).astype(bool)
    return out


def _summary_from_compare(comp: pd.DataFrame) -> dict[str, Any]:
    n = int(len(comp))
    summ: dict[str, Any] = {"rows": n}
    if "timing_gate_mismatch" in comp.columns:
        summ["timing_gate_mismatch_count"] = int(comp["timing_gate_mismatch"].fillna(False).sum())
    if "breakout_cross_mismatch" in comp.columns:
        summ["breakout_cross_mismatch_count"] = int(comp["breakout_cross_mismatch"].fillna(False).sum())
    if "recent_5m_high_diff" in comp.columns:
        s = comp["recent_5m_high_diff"].dropna()
        if len(s):
            summ["recent_5m_high_diff_abs_median"] = float(s.abs().median())
            summ["recent_5m_high_diff_abs_p95"] = float(s.abs().quantile(0.95))
    return summ


def main() -> int:
    root = _project_root()
    sys.path.insert(0, str(root))
    from src.signal_engine import compare_signal_eval_runs, eval_signals_on_ohlcv_dataframe

    parser = argparse.ArgumentParser(description="signals_eval 単体検証（1分足 DataFrame）")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", type=Path, help="単一の 1 分足 CSV（timestamp_utc or index + OHLCV）")
    src.add_argument("--compare", action="store_true", help="--yahoo-csv と --kabu-csv を比較")

    parser.add_argument("--yahoo-csv", type=Path, help="比較左（Yahoo 系）")
    parser.add_argument("--kabu-csv", type=Path, help="比較右（kabu/PUSH 合成など）")
    parser.add_argument(
        "--label",
        type=str,
        default="run",
        help="単一 --csv モードの出力ファイル名ラベル",
    )
    parser.add_argument("--out-dir", type=Path, default=None, help="出力ディレクトリ")

    parser.add_argument(
        "--vwap-mode",
        choices=("session_typical", "column"),
        default="session_typical",
        help="--csv 単体実行時の VWAP 供給",
    )
    parser.add_argument(
        "--yahoo-vwap",
        choices=("session_typical", "column"),
        default="session_typical",
        help="左系列の VWAP 供給",
    )
    parser.add_argument(
        "--kabu-vwap",
        choices=("session_typical", "column"),
        default="session_typical",
        help="右系列の VWAP 供給（kabu 列があるなら column も可）",
    )
    parser.add_argument("--vwap-column", type=str, default="vwap", metavar="NAME", help="column モードの列名")

    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    day = datetime.now().strftime("%Y%m%d")
    out_dir = args.out_dir or (root / "results" / "signals_eval_probe" / day)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.csv:
        df = _load_ohlcv(args.csv.resolve())
        res, _ = eval_signals_on_ohlcv_dataframe(
            df,
            vwap_mode=args.vwap_mode,
            vwap_column=args.vwap_column,
        )
        tag = args.label.replace(" ", "_")
        csv_path = out_dir / f"signals_eval_{tag}_{stamp}.csv"
        js_path = out_dir / f"signals_eval_{tag}_{stamp}_meta.json"
        res.to_csv(csv_path, index=False)
        summary_rows = res["all_timing_gates_pass"].fillna(False).sum()
        meta = {
            "source_csv": str(args.csv.resolve()),
            "vwap_mode": args.vwap_mode,
            "row_count": int(len(res)),
            "timing_gate_pass_rows": int(summary_rows),
            "breakout_cross_count": int(res["breakout_cross_now"].fillna(False).sum()),
            "stamp": stamp,
        }
        js_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(csv_path.relative_to(root))
        return 0

    if not args.compare or args.yahoo_csv is None or args.kabu_csv is None:
        parser.error("--compare には --yahoo-csv と --kabu-csv が必要です")
        return 2

    left_raw = _load_ohlcv(args.yahoo_csv.resolve())
    right_raw = _load_ohlcv(args.kabu_csv.resolve())

    left_df, _ = eval_signals_on_ohlcv_dataframe(
        left_raw,
        vwap_mode=args.yahoo_vwap,
        vwap_column=args.vwap_column,
    )
    right_df, _ = eval_signals_on_ohlcv_dataframe(
        right_raw,
        vwap_mode=args.kabu_vwap,
        vwap_column=args.vwap_column,
    )

    comp = compare_signal_eval_runs(left_df, right_df, suffixes=("_yahoo", "_kabu"))
    comp = _enriched_compare(comp)
    csv_path = out_dir / f"signals_eval_compare_{stamp}.csv"
    json_path = out_dir / f"signals_eval_compare_{stamp}.json"
    comp.to_csv(csv_path, index=False)

    summary = {
        "yahoo_csv": str(args.yahoo_csv.resolve()),
        "kabu_csv": str(args.kabu_csv.resolve()),
        "yahoo_vwap_mode": args.yahoo_vwap,
        "kabu_vwap_mode": args.kabu_vwap,
        "stamp": stamp,
        "divergence": _summary_from_compare(comp),
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(csv_path.relative_to(root))
    print(json_path.relative_to(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
