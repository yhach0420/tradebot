"""Phase687W23 — board liquidity research (fail-closed on empty capture)."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[2]
CAPTURE = NATIVE / "data" / "market_capture" / "20260714"
PAPER_AM = NATIVE / "results" / "small_paper" / "20260714" / "live_session_082256"
REPORT = NATIVE / "results" / "reports" / "phase687w23_board_liquidity_predictive_research"

# Intended feature dictionary (research plan; not computed without raw PUSH)
FEATURE_DICTIONARY = {
    "static": [
        "best_bid_qty",
        "best_ask_qty",
        "top1_imbalance",
        "top3_imbalance",
        "top5_imbalance",
        "top10_imbalance",
        "bid_depth_total",
        "ask_depth_total",
        "depth_ratio",
        "spread_yen",
        "spread_bps",
        "mid_price",
        "microprice",
        "microprice_minus_mid_bps",
        "depth_entropy",
        "top1_depth_share",
        "top3_depth_share",
    ],
    "temporal_windows_sec": [5, 15, 30, 60, 120, 300],
    "temporal": [
        "bid_depth_chg_rate",
        "ask_depth_chg_rate",
        "imbalance_delta",
        "spread_shrink_rate",
        "microprice_slope",
        "mid_slope",
        "bid_uptick_count",
        "ask_uptick_count",
        "board_update_count",
        "price_update_count",
        "volume_accel",
        "trading_value_accel",
    ],
    "flow": [
        "ofi_proxy",
        "bid_add",
        "bid_cancel",
        "ask_add",
        "ask_cancel",
        "best_bid_hold_sec",
        "best_ask_hold_sec",
        "imbalance_persistence_sec",
    ],
    "board_price_divergence": [
        "board_update_count_60s",
        "price_update_count_300s",
        "current_price_time_age_sec",
        "volume_delta_300s",
        "board_fresh_price_stale_flag",
    ],
}


def _write_csv(path: Path, rows: list[dict[str, Any]], cols: Optional[list[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = cols or list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def audit_capture() -> dict[str, Any]:
    summary = json.loads((CAPTURE / "capture_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((CAPTURE / "capture_manifest.json").read_text(encoding="utf-8"))
    status = json.loads((CAPTURE / "capture_status.json").read_text(encoding="utf-8"))
    reg = json.loads((CAPTURE / "registration_manifest.json").read_text(encoding="utf-8"))
    symbols = list(manifest.get("registered_symbols") or reg.get("registered_symbols") or [])
    parts = sorted(CAPTURE.glob("push_part_*.jsonl"))
    part_bytes = {p.name: p.stat().st_size for p in parts}
    total_push_bytes = sum(part_bytes.values())
    # Attempt to count events if any
    event_count = 0
    field_presence: dict[str, int] = {}
    symbols_seen: set[str] = set()
    for p in parts:
        if p.stat().st_size <= 0:
            continue
        with p.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                event_count += 1
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                payload = o.get("payload") if isinstance(o.get("payload"), dict) else o
                sym = str(payload.get("Symbol") or o.get("symbol") or "")
                if sym:
                    symbols_seen.add(sym)
                for k in (
                    "CurrentPrice",
                    "CurrentPriceTime",
                    "BidPrice",
                    "AskPrice",
                    "BidQty",
                    "AskQty",
                    "TradingVolume",
                    "TradingValue",
                    "VWAP",
                    "Buy1",
                    "Sell1",
                    "BidTime",
                    "AskTime",
                ):
                    if payload.get(k) is not None and payload.get(k) != "":
                        field_presence[k] = field_presence.get(k, 0) + 1
                # depth ladders
                for i in range(1, 11):
                    for side in ("Buy", "Sell"):
                        key = f"{side}{i}"
                        if payload.get(key) is not None:
                            field_presence[key] = field_presence.get(key, 0) + 1

    missing = []
    if event_count == 0 or total_push_bytes == 0:
        missing.extend(
            [
                "PUSH events (all push_part_*.jsonl empty)",
                "Board depth Buy1-10 / Sell1-10",
                "BidPrice/AskPrice/BidQty/AskQty time series",
                "CurrentPrice / CurrentPriceTime time series",
                "Volume / TradingValue / VWAP time series",
                "Board timestamps for AM/PM coverage",
            ]
        )

    quality_row = {
        "trading_date": "20260714",
        "capture_status": status.get("capture_status") or summary.get("capture_status"),
        "registered_symbol_count": len(symbols),
        "registered_has_4174": "4174" in symbols or "4174.T" in symbols,
        "push_part_files": len(parts),
        "push_part_nonzero_files": sum(1 for s in part_bytes.values() if s > 0),
        "total_push_bytes": total_push_bytes,
        "total_events": int(summary.get("total_events") or event_count or 0),
        "symbols_seen_count": int(summary.get("symbols_seen_count") or len(symbols_seen)),
        "board_push_count": field_presence.get("BidPrice", 0),
        "current_price_count": field_presence.get("CurrentPrice", 0),
        "current_price_time_count": field_presence.get("CurrentPriceTime", 0),
        "bid_qty_count": field_presence.get("BidQty", 0),
        "ask_qty_count": field_presence.get("AskQty", 0),
        "buy1_count": field_presence.get("Buy1", 0),
        "sell1_count": field_presence.get("Sell1", 0),
        "buy10_count": field_presence.get("Buy10", 0),
        "sell10_count": field_presence.get("Sell10", 0),
        "volume_count": field_presence.get("TradingVolume", 0),
        "trading_value_count": field_presence.get("TradingValue", 0),
        "vwap_count": field_presence.get("VWAP", 0),
        "bid_time_count": field_presence.get("BidTime", 0),
        "ask_time_count": field_presence.get("AskTime", 0),
        "missing_rate_events": 1.0 if event_count == 0 else 0.0,
        "stale_rate": None,
        "am_coverage": 0.0,
        "pm_coverage": 0.0,
        "data_sufficient_for_board_research": False,
        "blocker": "CAPTURE_NO_MARKET_EVENTS",
    }
    return {
        "quality_row": quality_row,
        "registered_symbols": symbols,
        "part_bytes": part_bytes,
        "field_presence": field_presence,
        "missing_items": missing,
        "summary": summary,
        "manifest": manifest,
        "status": status,
    }


def paper_fallback_note() -> dict[str, Any]:
    """Document that Paper has price/imbalance summaries but NOT multi-level board capture."""
    ev = PAPER_AM / "small_paper_events.jsonl"
    if not ev.is_file():
        return {"paper_am_events": False}
    n_acc = 0
    n_cand = 0
    n_4174 = 0
    sample_keys: list[str] = []
    with ev.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"4174.T"' in line:
                n_4174 += 1
            if '"event_type": "accepted"' in line:
                n_acc += 1
                if n_acc == 1:
                    o = json.loads(line)
                    sample_keys = sorted(
                        k
                        for k in o.keys()
                        if any(
                            x in k.lower()
                            for x in ("bid", "ask", "board", "imb", "spread", "price_age", "fresh")
                        )
                    )
            elif '"event_type": "candidate"' in line:
                n_cand += 1
    return {
        "paper_am_events": True,
        "accepted_count": n_acc,
        "candidate_count_approx_stream": n_cand,
        "4174_event_lines": n_4174,
        "boardish_keys_on_accepted_sample": sample_keys,
        "multi_level_board_in_paper": False,
        "note": "Paper events have price_age/board_age/imbalance-related gate fields but not Buy1-10 depth ladders from Capture.",
    }


def case_4174_from_paper() -> list[dict[str, Any]]:
    """W22A-linked divergence signals from Paper accepted/exit — NOT capture board depth."""
    ev = PAPER_AM / "small_paper_events.jsonl"
    rows: list[dict[str, Any]] = []
    if not ev.is_file():
        return rows
    with ev.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"4174.T"' not in line:
                continue
            if '"accepted"' not in line and '"observer_exit"' not in line:
                continue
            o = json.loads(line)
            if o.get("symbol") != "4174.T":
                continue
            if o.get("event_type") not in ("accepted", "observer_exit"):
                continue
            rows.append(
                {
                    "source": "paper_events_not_capture",
                    "event_type": o.get("event_type"),
                    "event_time": o.get("event_time"),
                    "message_index": o.get("message_index"),
                    "current_price": o.get("current_price") or o.get("exit_price"),
                    "price_age_sec": o.get("price_age_sec"),
                    "board_age_sec": o.get("board_age_sec"),
                    "price_freshness_source": o.get("price_freshness_source"),
                    "exit_reason": o.get("exit_reason"),
                    "board_fresh_price_stale": (
                        (o.get("board_age_sec") is not None and float(o.get("board_age_sec") or 0) < 3)
                        and (o.get("price_age_sec") is not None and float(o.get("price_age_sec") or 0) > 60)
                    ),
                    "hypothesis_h5_flag": o.get("price_freshness_source") == "liquidity_stale_trade",
                }
            )
    return rows


def current_board_mid_high_definition() -> dict[str, Any]:
    return {
        "board_imbalance_definition": "BidQty / (BidQty + AskQty) top-of-book only (realtime_board_exit_shadow.calc_bid_ask_imbalance)",
        "board_mid_high": "ExposureGate entry_score_v2 requires board_mid_or_high token; demo docs map mid≈0.48 BidQty/(Bid+Ask) band",
        "depth_levels_used_in_mainline": 1,
        "buy1_sell10_used_in_mainline_board_token": False,
        "correlation_with_new_features": "NOT_COMPUTABLE_WITHOUT_CAPTURE",
    }


def run() -> dict[str, Any]:
    REPORT.mkdir(parents=True, exist_ok=True)
    audit = audit_capture()
    paper = paper_fallback_note()
    case4174 = case_4174_from_paper()
    board_def = current_board_mid_high_definition()

    _write_csv(REPORT / "raw_data_quality.csv", [audit["quality_row"]])
    cov = [
        {
            "symbol": s if "." in s else f"{s}.T",
            "registered": True,
            "push_count": 0,
            "board_push_count": 0,
            "am_push_count": 0,
            "pm_push_count": 0,
            "coverage_ok": False,
        }
        for s in audit["registered_symbols"]
    ]
    _write_csv(REPORT / "symbol_board_coverage.csv", cov)

    # Empty / blocked analytical outputs
    empty_note = [{"status": "SKIPPED", "reason": "CAPTURE_NO_MARKET_EVENTS", "rows": 0}]
    for name in (
        "label_distribution.csv",
        "univariate_results.csv",
        "multivariate_results.csv",
        "pbv2_winner_loser_comparison.csv",
        "stop_vs_winner.csv",
        "no_progress_vs_winner.csv",
        "feature_correlation.csv",
        "feature_importance.csv",
        "threshold_stability.csv",
        "daily_breakdown.csv",
        "am_pm_breakdown.csv",
        "symbol_concentration.csv",
        "portfolio_counterfactual.csv",
    ):
        _write_csv(REPORT / name, empty_note)

    _write_csv(REPORT / "case_4174_board_price_divergence.csv", case4174 or empty_note)

    # Empty parquet substitute: write JSONL marker (pyarrow may be absent)
    feat_path = REPORT / "board_features.parquet"
    # Use a sidecar JSON to avoid hard dependency; also write empty parquet-compatible CSV marker
    (REPORT / "board_features.parquet.json").write_text(
        json.dumps(
            {
                "rows": 0,
                "reason": "CAPTURE_NO_MARKET_EVENTS",
                "schema": FEATURE_DICTIONARY,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.table({"status": ["CAPTURE_NO_MARKET_EVENTS"], "rows": [0]})
        pq.write_table(table, feat_path)
    except Exception:
        # Minimal parquet-less placeholder file
        feat_path.write_bytes(b"PARQUET_UNAVAILABLE_CAPTURE_EMPTY\n")

    (REPORT / "board_feature_dictionary.md").write_text(
        """# Board Feature Dictionary (Phase687W23)

## Status
**NOT COMPUTED** — `data/market_capture/20260714` has `CAPTURE_NO_MARKET_EVENTS` (0 PUSH bytes).

## Planned static features
"""
        + "\n".join(f"- `{x}`" for x in FEATURE_DICTIONARY["static"])
        + "\n\n## Temporal windows (sec)\n"
        + ", ".join(str(x) for x in FEATURE_DICTIONARY["temporal_windows_sec"])
        + "\n\n## Temporal / flow / divergence\n"
        + "\n".join(
            f"- `{x}`"
            for x in (
                FEATURE_DICTIONARY["temporal"]
                + FEATURE_DICTIONARY["flow"]
                + FEATURE_DICTIONARY["board_price_divergence"]
            )
        )
        + """

## Current mainline Board mid/high (code)
- Top-of-book imbalance: `BidQty/(BidQty+AskQty)` only
- Multi-level Buy1–Buy10 / Sell1–Sell10 **not** used for Board mid/high token
- Therefore new depth/OFI features would not be duplicate of mid/high — but cannot be validated without Capture
""",
        encoding="utf-8",
    )

    (REPORT / "candidate_rules.md").write_text(
        """# Candidate Rules — Phase687W23

## Verdict gate
No candidate rules are promoted. Capture PUSH is empty.

## Deferred candidates (require multi-day Capture with board depth)
1. H5 detector: `board_update_count_60s>0 AND price_update_count_300s==0` (board-fresh / price-stale)
2. H1 combo: ask depth decrease + best bid upticks (30–120s)
3. H2: imbalance improvement velocity 30–120s > static top1 imbalance
4. H7: sustained microprice > mid (bps)

## Paper-only hint (not Capture-validated)
4174.T accepted rows show `price_freshness_source=liquidity_stale_trade` with rising `price_age_sec` and low `board_age_sec` — consistent with H5, but **not** a board-depth proof.
""",
        encoding="utf-8",
    )

    leakage = {
        "future_leakage_possible": False,
        "reason": "No features computed; analysis halted on empty capture",
        "train_test_split": "NOT_RUN",
        "same_symbol_adjacency_blocked": "NOT_RUN",
        "entry_post_data_in_features": False,
        "capture_events": 0,
    }
    (REPORT / "leakage_audit.json").write_text(json.dumps(leakage, indent=2), encoding="utf-8")

    manifest = {
        "mainline_changes": [],
        "research_only": True,
        "shadow_added": False,
        "entry_exit_changed": False,
        "actual_submit": 0,
        "actual_cancel": 0,
        "files_written": "results/reports/phase687w23_board_liquidity_predictive_research/**",
    }
    (REPORT / "code_change_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    verdict = "CAPTURE_DATA_INSUFFICIENT"
    secondary = ["MULTIDAY_VALIDATION_REQUIRED"]
    # If paper case shows divergence pattern, note as unvalidated signal hint only
    h5_hits = sum(1 for r in case4174 if r.get("board_fresh_price_stale") or r.get("hypothesis_h5_flag"))
    if h5_hits:
        secondary.append("BOARD_PRICE_DIVERGENCE_HINT_FROM_PAPER_ONLY")

    report = {
        "phase": "687W23",
        "verdict": verdict,
        "secondary_notes": secondary,
        "capture_day": "20260714",
        "registered_symbols": len(audit["registered_symbols"]),
        "push_events": 0,
        "data_sufficient": False,
        "missing_items": audit["missing_items"],
        "paper_fallback": paper,
        "board_mid_high_definition": board_def,
        "case_4174_paper_rows": len(case4174),
        "case_4174_h5_hint_rows": h5_hits,
        "analyses_skipped": [
            "univariate",
            "multivariate",
            "logistic/tree models",
            "portfolio_counterfactual",
            "feature_importance",
        ],
        "mainline_changes": [],
        "actual_submit": 0,
        "actual_cancel": 0,
        "completion_header": {
            "1_board_data_sufficient": False,
            "2_days_symbols_samples": {"days": 1, "registered_symbols": len(audit["registered_symbols"]), "push_samples": 0},
            "3_best_univariate": None,
            "4_best_combo": None,
            "5_winner_vs_stop": None,
            "6_winner_vs_no_progress": None,
            "7_improved_vs_board_mid_high": "NOT_EVALUABLE",
            "8_4174_divergence_detected": h5_hits > 0,
            "8_note": "Detected on Paper events only; Capture board depth absent",
            "9_portfolio_improved": "NOT_RUN",
            "10_symbol_concentration": "NOT_RUN",
            "11_overfit_risk": "N/A_NO_SIGNAL_FIT",
            "12_shadow_candidates": [],
            "13_mainline_adopted": [],
            "14_no_live_orders": True,
        },
    }
    (REPORT / "phase687w23_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    decision = f"""# Phase687W23 Decision

## Verdict: `{verdict}`

Secondary: {', '.join(secondary)}

1. 板元データは十分だったか: **NO** — `CAPTURE_NO_MARKET_EVENTS`, push_part 全0バイト
2. 対象日数・銘柄数・サンプル数: **1日 / 登録50銘柄 / PUSHサンプル0**
3. 上昇判別力が最も高かった単一指標: **N/A（未計算）**
4. 最良の2〜3指標組み合わせ: **N/A**
5. WinnerとSTOPの最大差: **N/A**
6. Winnerとno_progressの最大差: **N/A**
7. 現行Board mid/highより改善したか: **評価不能**（Capture板なし）。現行mid/highは top-of-book `BidQty/(Bid+Ask)` のみ
8. 4174.T異常を検出できたか: **Paper上はYes（price_age上昇 + board_age低 + liquidity_stale_trade） / Capture板では不可**
9. CAP込みportfolioで改善したか: **未実施**
10. 特定銘柄依存か: **未評価**
11. 過学習リスク: **該当なし（フィット未実施）**
12. Shadow候補に進める指標: **なし（Capture再取得後に再判定）**
13. 本線採用した変更: **なし**
14. 実注文変更なし: **確認**

### 不足項目
""" + "\n".join(f"- {m}" for m in audit["missing_items"]) + """

### 次アクション
1. PassiveCapture Sidecarが実際にPUSHを書き込むこと**を再証明（登録50でも events=0）
2. 複数日の非空 `push_part_*.jsonl` を確保してから本フェーズを再実行
3. その後にのみ H1–H8 / univariate / portfolio を解禁
"""
    (REPORT / "phase687w23_decision.md").write_text(decision, encoding="utf-8")
    return report


if __name__ == "__main__":
    out = run()
    print(json.dumps({"verdict": out["verdict"], "header": out["completion_header"]}, indent=2, ensure_ascii=False))
