#!/usr/bin/env python3
"""
Phase 94: Weekly continue/stop review from daily_live_observer_summary.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "kabu_native" / "results" / "reports"
DAILY_CSV = REPORTS / "daily_live_observer_summary.csv"
EXPECTED_POLICY = "q070_cap3_mfe_fav_vol_liq_trial"
MIN_SESSIONS_CONTINUE = 3
PF_CONTINUE_MIN = 1.2
PF_CAUTION_MIN = 1.0
PF_GT1_RATE_CONTINUE = 0.70
PF_GT1_RATE_CAUTION_LO = 0.50
PF_GT1_RATE_CAUTION_HI = 0.70
CAUTION_COUNT_MANY = 2


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> int:
    try:
        return int(float(val)) if val not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


def _parse_reviewed_at(raw: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(JST)
    except (TypeError, ValueError):
        return None


def load_daily_rows(path: Path, *, lookback_days: Optional[int]) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    vol_liq = [r for r in rows if r.get("policy_label") == EXPECTED_POLICY]
    if lookback_days is None:
        return vol_liq
    cutoff = datetime.now(JST) - timedelta(days=lookback_days)
    kept: list[dict[str, str]] = []
    for r in vol_liq:
        dt = _parse_reviewed_at(str(r.get("reviewed_at") or ""))
        if dt is None or dt >= cutoff:
            kept.append(r)
    return kept


def _weighted_mean(pairs: Sequence[tuple[float, int]]) -> Optional[float]:
    w = sum(w for _, w in pairs)
    if w <= 0:
        return None
    return sum(v * w for v, w in pairs) / w


def aggregate(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    if not rows:
        return {"session_count": 0}

    verdicts = Counter(str(r.get("daily_verdict") or "") for r in rows)
    pfs: list[float] = []
    pf_pairs: list[tuple[float, int]] = []
    avg_pairs: list[tuple[float, int]] = []
    wr_pairs: list[tuple[float, int]] = []
    total_pnl = 0.0
    total_trades = 0

    for r in rows:
        n = _int(r.get("structural_trade_count"))
        pf = _float(r.get("structural_pf"))
        avg = _float(r.get("structural_avg_pnl"))
        wr = _float(r.get("structural_win_rate"))
        pnl = _float(r.get("total_pnl")) or 0.0
        total_trades += n
        total_pnl += pnl
        if pf is not None:
            pfs.append(pf)
            pf_pairs.append((pf, n))
        if avg is not None:
            avg_pairs.append((avg, n))
        if wr is not None:
            wr_pairs.append((wr, n))

    pf_gt1 = sum(1 for p in pfs if p > 1.0)
    pf_ge12 = sum(1 for p in pfs if p >= PF_CONTINUE_MIN)
    n_sess = len(rows)

    return {
        "session_count": n_sess,
        "session_ids": [r.get("session_id") for r in rows],
        "total_trade_count": total_trades,
        "weighted_pf": round(_weighted_mean(pf_pairs), 4) if pf_pairs else None,
        "weighted_avg_pnl": round(_weighted_mean(avg_pairs), 4) if avg_pairs else None,
        "total_pnl": round(total_pnl, 4),
        "weighted_win_rate": round(_weighted_mean(wr_pairs), 4) if wr_pairs else None,
        "daily_verdict_breakdown": dict(verdicts),
        "pf_gt_1_session_rate": round(pf_gt1 / n_sess, 4) if n_sess else None,
        "pf_ge_1_2_session_rate": round(pf_ge12 / n_sess, 4) if n_sess else None,
        "stop_and_review_count": int(verdicts.get("stop_and_review", 0)),
        "caution_count": int(verdicts.get("caution", 0)),
        "continue_count": int(verdicts.get("continue", 0)),
    }


def _weekly_verdict(agg: Mapping[str, Any]) -> tuple[str, str, bool]:
    n = int(agg.get("session_count") or 0)
    w_pf = _float(agg.get("weighted_pf")) or 0.0
    w_avg = _float(agg.get("weighted_avg_pnl"))
    pf_gt1_rate = _float(agg.get("pf_gt_1_session_rate")) or 0.0
    stop_n = int(agg.get("stop_and_review_count") or 0)
    caution_n = int(agg.get("caution_count") or 0)

    if n == 0:
        return "weekly_insufficient_data", "no vol_liq daily rows in summary CSV", False

    if w_pf < PF_CAUTION_MIN or (w_avg is not None and w_avg <= 0) or stop_n >= 1:
        return (
            "weekly_stop_and_review",
            f"weighted_pf={w_pf}, weighted_avg_pnl={w_avg}, stop_and_review={stop_n}",
            False,
        )

    continue_ok = (
        n >= MIN_SESSIONS_CONTINUE
        and w_pf >= PF_CONTINUE_MIN
        and w_avg is not None
        and w_avg > 0
        and pf_gt1_rate >= PF_GT1_RATE_CONTINUE
        and stop_n == 0
    )
    if continue_ok:
        return (
            "weekly_continue",
            f"sessions={n}, weighted_pf={w_pf}, weighted_avg_pnl={w_avg}, pf_gt1_rate={pf_gt1_rate:.0%}",
            True,
        )

    caution_bits: list[str] = []
    if PF_CAUTION_MIN <= w_pf < PF_CONTINUE_MIN:
        caution_bits.append(f"weighted_pf={w_pf}")
    if PF_GT1_RATE_CAUTION_LO <= pf_gt1_rate < PF_GT1_RATE_CAUTION_HI:
        caution_bits.append(f"pf_gt1_rate={pf_gt1_rate:.0%}")
    if caution_n >= CAUTION_COUNT_MANY:
        caution_bits.append(f"caution_days={caution_n}")
    if n < MIN_SESSIONS_CONTINUE:
        caution_bits.append(f"sessions={n}<{MIN_SESSIONS_CONTINUE}")

    return (
        "weekly_caution",
        "; ".join(caution_bits) or "mixed weekly metrics below continue thresholds",
        True,
    )


def write_weekly_summary_csv(path: Path, review: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    agg = review.get("aggregate") or {}
    row = {
        "generated_at": review.get("generated_at"),
        "week_key": review.get("week_key"),
        "weekly_verdict": review.get("weekly_verdict"),
        "continue_main_config": review.get("continue_main_config"),
        "session_count": agg.get("session_count"),
        "total_trade_count": agg.get("total_trade_count"),
        "weighted_pf": agg.get("weighted_pf"),
        "weighted_avg_pnl": agg.get("weighted_avg_pnl"),
        "total_pnl": agg.get("total_pnl"),
        "weighted_win_rate": agg.get("weighted_win_rate"),
        "pf_gt_1_session_rate": agg.get("pf_gt_1_session_rate"),
        "pf_ge_1_2_session_rate": agg.get("pf_ge_1_2_session_rate"),
        "stop_and_review_count": agg.get("stop_and_review_count"),
        "caution_count": agg.get("caution_count"),
        "continue_count": agg.get("continue_count"),
    }
    fields = list(row.keys())
    existing: list[dict[str, Any]] = []
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or fields)
            for k in row:
                if k not in fields:
                    fields.append(k)
            existing = [dict(r) for r in reader if r.get("week_key") != review.get("week_key")]
    existing.append(row)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in existing:
            w.writerow(r)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase94 weekly live observer review")
    parser.add_argument(
        "--daily-csv",
        type=Path,
        default=DAILY_CSV,
        help="Path to daily_live_observer_summary.csv",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help="Include vol_liq sessions reviewed within N days (0=all)",
    )
    parser.add_argument("--report-date", default=None, help="YYYYMMDD for output filename")
    args = parser.parse_args()

    day_key = args.report_date or datetime.now(JST).strftime("%Y%m%d")
    csv_path = args.daily_csv if args.daily_csv.is_absolute() else ROOT / args.daily_csv
    lookback = None if args.lookback_days <= 0 else args.lookback_days
    rows = load_daily_rows(csv_path, lookback_days=lookback)

    agg = aggregate(rows)
    verdict, rationale, continue_main = _weekly_verdict(agg)

    reviewed_dates = [
        _parse_reviewed_at(str(r.get("reviewed_at") or "")) for r in rows
    ]
    reviewed_dates_ok = [d for d in reviewed_dates if d is not None]

    review = {
        "phase": 94,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "week_key": day_key,
        "policy_id": EXPECTED_POLICY,
        "source_csv": str(csv_path.relative_to(ROOT)).replace("\\", "/"),
        "lookback_days": lookback,
        "review_period": {
            "from": min(reviewed_dates_ok).isoformat(timespec="seconds") if reviewed_dates_ok else None,
            "to": max(reviewed_dates_ok).isoformat(timespec="seconds") if reviewed_dates_ok else None,
        },
        "aggregate": agg,
        "weekly_verdict": verdict,
        "verdict_rationale": rationale,
        "continue_main_config": continue_main,
        "conclusion": (
            f"Continue {EXPECTED_POLICY} as main live observer config for the coming week."
            if continue_main and verdict == "weekly_continue"
            else (
                f"Hold {EXPECTED_POLICY}; weekly verdict={verdict} (no config change from this review)."
                if continue_main
                else f"Pause and review {EXPECTED_POLICY}; weekly verdict={verdict}."
            )
        ),
        "criteria": {
            "weekly_continue": {
                "sessions_gte": MIN_SESSIONS_CONTINUE,
                "weighted_pf_gte": PF_CONTINUE_MIN,
                "weighted_avg_pnl_gt": 0,
                "pf_gt_1_session_rate_gte": PF_GT1_RATE_CONTINUE,
                "stop_and_review_eq": 0,
            },
            "weekly_stop_and_review": {
                "weighted_pf_lt": PF_CAUTION_MIN,
                "weighted_avg_pnl_lte": 0,
                "stop_and_review_gte": 1,
            },
        },
        "note": "Diagnostic only; no logic or YAML changes.",
    }

    out_json = REPORTS / f"weekly_live_observer_review_{day_key}.json"
    out_csv = REPORTS / "weekly_live_observer_summary.csv"
    if not REPORTS.is_absolute():
        out_json = ROOT / out_json
        out_csv = ROOT / out_csv

    out_json.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    write_weekly_summary_csv(out_csv, review)

    print(
        json.dumps(
            {
                "weekly_verdict": verdict,
                "continue_main_config": continue_main,
                "session_count": agg.get("session_count"),
                "weighted_pf": agg.get("weighted_pf"),
                "output_json": str(out_json),
            },
            ensure_ascii=True,
        )
    )
    print(f"Wrote {out_json}", file=sys.stderr)
    print(f"Wrote {out_csv}", file=sys.stderr)
    return 0 if verdict != "weekly_stop_and_review" else 2


if __name__ == "__main__":
    raise SystemExit(main())
