#!/usr/bin/env python3
"""
Daily live observer review for q070_cap3_mfe_fav_vol_liq_trial (read-only).

Usage:
  python kabu_native/scripts/run_daily_live_observer_review.py \\
    --session-dir kabu_native/results/small_paper/20260521/live_full_session_081418
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "kabu_native" / "results" / "reports"
EXPECTED_POLICY_LABEL = "q070_cap3_mfe_fav_vol_liq_trial"
EXPECTED_EXIT_POLICY = "combined_structural_exit_v1"
OFFICIAL_EXIT_REASONS = frozenset(
    {
        "stop_hit",
        "session_end",
        "overlap_replaced_review",
        "quality_decay_exit",
        "momentum_fade_exit",
        "price_momentum_fade_exit",
        "favorable_fade_exit",
        "vwap_break_exit",
        "mfe_giveback_exit",
    }
)
PF_CONTINUE_MIN = 1.2
PF_CAUTION_MIN = 1.0
MIN_TRADE_COUNT_CONTINUE = 50
SMALL_AVG_PNL_THRESHOLD = 0.005


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return sum(wins) / gl


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_trades(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _load_exit_reasons(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    out: list[dict[str, Any]] = []
    for r in rows:
        reason = r.get("close_reason") or r.get("exit_reason") or ""
        out.append(
            {
                "exit_reason": reason,
                "trade_count": int(_float(r.get("trade_count")) or 0),
                "pct_of_trades": _float(r.get("pct_of_trades")),
                "avg_pnl_pct": _float(r.get("avg_pnl_pct")),
                "profit_factor": _float(r.get("profit_factor")),
                "total_pnl_pct": round(
                    (_float(r.get("avg_pnl_pct")) or 0) * int(_float(r.get("trade_count")) or 0),
                    4,
                ),
            }
        )
    return out


def _top_symbols(trades: Sequence[Mapping[str, Any]], *, key: str, n: int = 5) -> list[dict[str, Any]]:
    if key == "trade_count":
        c: Counter[str] = Counter()
        for t in trades:
            c[str(t.get("symbol") or "")] += 1
        ranked = c.most_common(n)
        return [{"symbol": s, "trade_count": cnt} for s, cnt in ranked]
    by_sym: dict[str, float] = {}
    for t in trades:
        sym = str(t.get("symbol") or "")
        by_sym[sym] = by_sym.get(sym, 0.0) + (_float(t.get("realized_pnl_pct")) or 0.0)
    ranked = sorted(by_sym.items(), key=lambda x: -x[1])[:n]
    return [{"symbol": s, "total_pnl": round(v, 4)} for s, v in ranked]


def _warning_flags(metrics: Mapping[str, Any]) -> list[str]:
    flags: list[str] = []
    pf = _float(metrics.get("structural_pf"))
    if pf is not None and pf < PF_CONTINUE_MIN:
        flags.append("pf_below_1.2")
    avg = _float(metrics.get("structural_avg_pnl"))
    if avg is not None and avg <= 0:
        flags.append("avg_pnl_non_positive")
    tc = int(metrics.get("structural_trade_count") or 0)
    if tc < MIN_TRADE_COUNT_CONTINUE:
        flags.append("trade_count_below_50")
    if metrics.get("rejected_by_daytrade_suitability") is None:
        flags.append("rejected_by_daytrade_suitability_missing")
    if metrics.get("policy_label") != EXPECTED_POLICY_LABEL:
        flags.append("policy_label_mismatch")
    if metrics.get("order_enabled") is True:
        flags.append("order_enabled_true")
    if metrics.get("paper_only") is False:
        flags.append("paper_only_false")
    if metrics.get("unknown_exit_reason_exists"):
        flags.append("unknown_exit_reason_exists")
    if avg is not None and 0 < avg <= SMALL_AVG_PNL_THRESHOLD:
        flags.append("avg_pnl_very_small")
    return flags


def _verdict(metrics: Mapping[str, Any], warnings: Sequence[str]) -> tuple[str, str, bool]:
    """Return daily_verdict, rationale, continue_main_config."""
    pf = _float(metrics.get("structural_pf")) or 0.0
    avg = _float(metrics.get("structural_avg_pnl"))
    tc = int(metrics.get("structural_trade_count") or 0)
    policy_ok = metrics.get("policy_label") == EXPECTED_POLICY_LABEL
    order_ok = metrics.get("order_enabled") is False
    paper_ok = metrics.get("paper_only") is True

    stop_reasons: list[str] = []
    if pf < PF_CAUTION_MIN:
        stop_reasons.append(f"PF {pf} < {PF_CAUTION_MIN}")
    if avg is not None and avg <= 0:
        stop_reasons.append(f"avg_pnl {avg} <= 0")
    if not policy_ok:
        stop_reasons.append(f"policy_label {metrics.get('policy_label')!r}")
    if not order_ok:
        stop_reasons.append("order_enabled is true")
    if not paper_ok:
        stop_reasons.append("paper_only is false")

    if stop_reasons:
        return (
            "stop_and_review",
            "; ".join(stop_reasons),
            False,
        )

    continue_ok = (
        pf >= PF_CONTINUE_MIN
        and avg is not None
        and avg > 0
        and tc >= MIN_TRADE_COUNT_CONTINUE
        and policy_ok
        and order_ok
        and paper_ok
    )
    if continue_ok:
        return (
            "continue",
            f"PF {pf}, avg_pnl {avg}, trades {tc}; safety flags OK",
            True,
        )

    caution_bits: list[str] = []
    if PF_CAUTION_MIN <= pf < PF_CONTINUE_MIN:
        caution_bits.append(f"PF {pf} in [{PF_CAUTION_MIN},{PF_CONTINUE_MIN})")
    if tc < MIN_TRADE_COUNT_CONTINUE:
        caution_bits.append(f"trade_count {tc} < {MIN_TRADE_COUNT_CONTINUE}")
    if avg is not None and 0 < avg <= SMALL_AVG_PNL_THRESHOLD:
        caution_bits.append(f"avg_pnl {avg} is small")
    if warnings:
        caution_bits.append(f"warnings: {','.join(warnings)}")

    return (
        "caution",
        "; ".join(caution_bits) or "metrics below continue thresholds",
        True,
    )


def _session_key(session_dir: Path) -> str:
    parts = session_dir.parts
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return session_dir.name


def _upsert_summary_csv(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    fields: list[str] = []
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or [])
            rows = [dict(r) for r in reader]
    for k in row:
        if k not in fields:
            fields.append(k)
    key = str(row.get("session_id") or "")
    rows = [r for r in rows if str(r.get("session_id") or "") != key]
    rows.append({k: row.get(k) for k in fields})
    rows.sort(key=lambda r: str(r.get("session_id") or ""))
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def review_session(session_dir: Path) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    summary_path = session_dir / "small_paper_summary.json"
    observer_path = session_dir / "structural_observer_review.json"
    trades_path = session_dir / "structural_trades.csv"
    exit_path = session_dir / "structural_exit_reasons.csv"

    missing = [
        p.name
        for p in (summary_path, observer_path, trades_path, exit_path)
        if not p.is_file()
    ]

    summary = _load_json(summary_path)
    observer = _load_json(observer_path)
    trades = _load_trades(trades_path)
    exit_rows = _load_exit_reasons(exit_path)

    pnls = [_float(t.get("realized_pnl_pct")) for t in trades]
    pnls_ok = [p for p in pnls if p is not None]
    pf_csv = _profit_factor(pnls_ok) if pnls_ok else None

    obs_metrics = observer.get("structural_metrics") or observer
    structural_pf = _float(observer.get("structural_pf")) or _float(obs_metrics.get("structural_pf")) or pf_csv
    structural_avg = _float(observer.get("structural_avg_pnl")) or _float(
        obs_metrics.get("structural_avg_pnl")
    )
    if structural_avg is None and pnls_ok:
        structural_avg = sum(pnls_ok) / len(pnls_ok)
    structural_tc = int(
        observer.get("structural_trade_count")
        or obs_metrics.get("structural_trade_count")
        or len(trades)
    )
    structural_wr = _float(observer.get("structural_win_rate")) or _float(
        obs_metrics.get("structural_win_rate")
    )
    total_pnl = round(sum(pnls_ok), 4) if pnls_ok else None

    reject_counts = summary.get("reject_reason_counts") or {}
    rejected_vol = summary.get("rejected_by_daytrade_suitability")
    if rejected_vol is None and isinstance(reject_counts, dict):
        rejected_vol = reject_counts.get("daytrade_suitability")

    exit_dist = observer.get("exit_reason_distribution") or obs_metrics.get(
        "exit_reason_distribution"
    ) or {}
    if not exit_rows and exit_dist:
        exit_rows = [
            {
                "exit_reason": k,
                "trade_count": int(v),
                "profit_contribution_rate": None,
            }
            for k, v in exit_dist.items()
        ]

    unknown_exits = sorted(
        {
            str(t.get("close_reason") or "")
            for t in trades
            if str(t.get("close_reason") or "") not in OFFICIAL_EXIT_REASONS
        }
        - {""}
    )

    metrics = {
        "session_id": _session_key(session_dir),
        "session_dir": str(session_dir),
        "policy_label": summary.get("policy_label") or observer.get("policy_context", {}).get(
            "policy_label"
        ),
        "expected_policy_label": EXPECTED_POLICY_LABEL,
        "structural_exit_policy": summary.get("structural_exit_policy")
        or observer.get("structural_exit_policy"),
        "accepted_count": summary.get("accepted_count"),
        "rejected_by_daytrade_suitability": rejected_vol,
        "candidate_count": summary.get("candidate_count"),
        "structural_pf": round(structural_pf, 4) if structural_pf not in (None, float("inf")) else structural_pf,
        "structural_avg_pnl": round(structural_avg, 4) if structural_avg is not None else None,
        "structural_trade_count": structural_tc,
        "structural_win_rate": round(structural_wr, 4) if structural_wr is not None else None,
        "total_pnl": total_pnl,
        "order_enabled": summary.get("order_enabled"),
        "paper_only": summary.get("paper_only"),
        "unknown_exit_reason_exists": bool(unknown_exits),
        "unknown_exit_reasons": unknown_exits,
    }

    warnings = _warning_flags(metrics)
    daily_verdict, verdict_rationale, continue_main = _verdict(metrics, warnings)

    return {
        "phase": 91,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "purpose": "Daily continue/caution/stop gate for vol_liq trial live observer; no logic changes",
        "main_config": EXPECTED_POLICY_LABEL,
        "phase90_stability_reference": "acceptable",
        "session_id": metrics["session_id"],
        "session_dir": str(session_dir),
        "input_files": {
            "small_paper_summary.json": summary_path.is_file(),
            "structural_observer_review.json": observer_path.is_file(),
            "structural_trades.csv": trades_path.is_file(),
            "structural_exit_reasons.csv": exit_path.is_file(),
        },
        "missing_files": missing,
        "metrics": metrics,
        "exit_reason_breakdown": exit_rows,
        "top_symbols_by_trade_count": _top_symbols(trades, key="trade_count"),
        "top_symbols_by_total_pnl": _top_symbols(trades, key="total_pnl"),
        "warning_flags": warnings,
        "daily_verdict": daily_verdict,
        "verdict_rationale": verdict_rationale,
        "continue_main_config": continue_main,
        "conclusion": (
            f"Continue {EXPECTED_POLICY_LABEL} as main live observer config."
            if continue_main
            else f"Do not continue without review; address {daily_verdict}."
        ),
        "note": "Diagnostic review only; production pilot YAML unchanged.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily live observer session review")
    parser.add_argument(
        "--session-dir",
        type=Path,
        required=True,
        help="Live session output directory (e.g. .../20260521/live_full_session_081418)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for JSON (default: session-dir)",
    )
    parser.add_argument(
        "--summary-csv",
        type=Path,
        default=REPORTS / "daily_live_observer_summary.csv",
        help="Cumulative summary CSV path",
    )
    args = parser.parse_args()

    session_dir = args.session_dir if args.session_dir.is_absolute() else ROOT / args.session_dir
    if not session_dir.is_dir():
        print(f"Session dir not found: {session_dir}", file=sys.stderr)
        return 1

    review = review_session(session_dir)
    out_dir = args.output_dir or session_dir
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "daily_live_observer_review.json"
    json_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    m = review["metrics"]
    summary_row = {
        "session_id": m["session_id"],
        "reviewed_at": review["generated_at"],
        "daily_verdict": review["daily_verdict"],
        "continue_main_config": review["continue_main_config"],
        "policy_label": m.get("policy_label"),
        "accepted_count": m.get("accepted_count"),
        "rejected_by_daytrade_suitability": m.get("rejected_by_daytrade_suitability"),
        "candidate_count": m.get("candidate_count"),
        "structural_pf": m.get("structural_pf"),
        "structural_avg_pnl": m.get("structural_avg_pnl"),
        "structural_trade_count": m.get("structural_trade_count"),
        "structural_win_rate": m.get("structural_win_rate"),
        "total_pnl": m.get("total_pnl"),
        "warning_flags": "|".join(review.get("warning_flags") or []),
        "order_enabled": m.get("order_enabled"),
        "paper_only": m.get("paper_only"),
    }
    csv_path = args.summary_csv if args.summary_csv.is_absolute() else ROOT / args.summary_csv
    _upsert_summary_csv(csv_path, summary_row)

    print(
        json.dumps(
            {
                "daily_verdict": review["daily_verdict"],
                "continue_main_config": review["continue_main_config"],
                "conclusion": review["conclusion"],
                "json": str(json_path),
                "summary_csv": str(csv_path),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
