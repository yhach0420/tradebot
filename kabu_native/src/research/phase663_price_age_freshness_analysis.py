"""Phase663 — Price age freshness analysis across full-period actual ENTRY trades."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase631_profit_source_attribution import _num
from research.phase632_pbv2_profit_filter_counterfactual import _metrics, _profit_factor
from research.phase634_pbv2_only_rise5_full_period import (
    _is_push_replay_session,
    _session_bucket,
    load_trades_for_session,
)

PHASE663_VERDICT = "phase663_price_age_freshness_analysis_done"
REPORT_DIR_NAME = "phase663_price_age_freshness"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"

# Canonical 22 trading days (Phase656/658 universe).
CANONICAL_DAYS: tuple[str, ...] = (
    "2026-05-29",
    "2026-06-01",
    "2026-06-02",
    "2026-06-03",
    "2026-06-08",
    "2026-06-09",
    "2026-06-10",
    "2026-06-11",
    "2026-06-12",
    "2026-06-15",
    "2026-06-16",
    "2026-06-17",
    "2026-06-18",
    "2026-06-19",
    "2026-06-22",
    "2026-06-24",
    "2026-06-25",
    "2026-06-29",
    "2026-06-30",
    "2026-07-01",
    "2026-07-06",
    "2026-07-07",
)

PRODUCTION_MAX_PRICE_AGE_SEC = 3.0

BUCKET_ORDER = ("lt_300", "300_599", "600_899", "gte_900", "missing")
BUCKET_LABELS = {
    "lt_300": "<300s",
    "300_599": "300-599s",
    "600_899": "600-899s",
    "gte_900": ">=900s",
    "missing": "missing_price_age_sec",
}


def price_age_bucket(price_age_sec: Any) -> str:
    pa = _num(price_age_sec)
    if pa is None:
        return "missing"
    if pa < 300:
        return "lt_300"
    if pa < 600:
        return "300_599"
    if pa < 900:
        return "600_899"
    return "gte_900"


def _is_stop_hit(trade: Mapping[str, Any]) -> bool:
    reason = str(trade.get("exit_reason") or "")
    return reason == "stop_hit" or bool(trade.get("stop_hit"))


def _is_no_progress(trade: Mapping[str, Any]) -> bool:
    reason = str(trade.get("exit_reason") or "")
    return reason == "no_progress_exit" or bool(trade.get("no_progress_exit"))


def _bucket_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "entry_count": 0,
            "win_rate": None,
            "profit_factor": None,
            "total_pnl_yen_100": 0.0,
            "avg_pnl_yen_100": None,
            "stop_hit_rate": None,
            "no_progress_exit_rate": None,
        }
    base = _metrics(list(trades))
    n = len(trades)
    return {
        "entry_count": n,
        "win_rate": base.get("win_rate"),
        "profit_factor": base.get("profit_factor"),
        "total_pnl_yen_100": base.get("pnl_yen_100"),
        "avg_pnl_yen_100": base.get("avg_pnl_yen_100"),
        "stop_hit_rate": round(sum(1 for t in trades if _is_stop_hit(t)) / n, 4),
        "no_progress_exit_rate": round(sum(1 for t in trades if _is_no_progress(t)) / n, 4),
    }


def load_canonical_trades(root: Path = SMALL_PAPER_ROOT) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    if not root.is_dir():
        return trades
    for day in CANONICAL_DAYS:
        day_dir = root / day.replace("-", "")
        if not day_dir.is_dir():
            continue
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            for t in load_trades_for_session(sess_dir, day):
                key = (day, str(t.get("session") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
                if key in seen:
                    continue
                seen.add(key)
                row = dict(t)
                row["day"] = day
                row["price_age_bucket"] = price_age_bucket(row.get("price_age_sec"))
                row["session_bucket"] = _session_bucket(row)
                row["stop_hit_flag"] = _is_stop_hit(row)
                row["no_progress_exit_flag"] = _is_no_progress(row)
                trades.append(row)
    trades.sort(key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    return trades


def build_bucket_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in BUCKET_ORDER:
        sub = [t for t in trades if t.get("price_age_bucket") == bucket]
        m = _bucket_metrics(sub)
        rows.append(
            {
                "bucket_id": bucket,
                "bucket_label": BUCKET_LABELS[bucket],
                **m,
                "share_of_entries": round(len(sub) / len(trades), 4) if trades else 0.0,
            }
        )
    return rows


def build_am_pm_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sess in ("AM", "PM", "lunch", "unknown"):
        sess_trades = [t for t in trades if t.get("session_bucket") == sess]
        if not sess_trades:
            continue
        for bucket in BUCKET_ORDER:
            sub = [t for t in sess_trades if t.get("price_age_bucket") == bucket]
            if not sub:
                continue
            m = _bucket_metrics(sub)
            rows.append({"session_bucket": sess, "bucket_id": bucket, "bucket_label": BUCKET_LABELS[bucket], **m})
    return rows


def build_symbol_rows(trades: Sequence[Mapping[str, Any]], *, min_entries: int = 1) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol") or "")].append(dict(t))
    rows: list[dict[str, Any]] = []
    for sym, seq in sorted(by_sym.items()):
        if len(seq) < min_entries:
            continue
        ages = [float(v) for v in (_num(t.get("price_age_sec")) for t in seq) if v is not None]
        stale = [t for t in seq if t.get("price_age_bucket") not in ("lt_300", "missing")]
        rows.append(
            {
                "symbol": sym,
                "entry_count": len(seq),
                "price_age_sec_median": round(statistics.median(ages), 3) if ages else None,
                "price_age_sec_max": round(max(ages), 3) if ages else None,
                "stale_entry_count_ge_300": len(stale),
                "stale_entry_share": round(len(stale) / len(seq), 4) if seq else 0.0,
                **_bucket_metrics(seq),
            }
        )
    rows.sort(key=lambda r: (-int(r.get("stale_entry_count_ge_300") or 0), -float(r.get("total_pnl_yen_100") or 0.0)))
    return rows


def build_daily_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in CANONICAL_DAYS:
        day_trades = [t for t in trades if t.get("day") == day]
        if not day_trades:
            continue
        for bucket in BUCKET_ORDER:
            sub = [t for t in day_trades if t.get("price_age_bucket") == bucket]
            if not sub:
                continue
            m = _bucket_metrics(sub)
            rows.append({"day": day, "bucket_id": bucket, "bucket_label": BUCKET_LABELS[bucket], **m})
    return rows


def _compare_stale_vs_fresh(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fresh = [t for t in trades if t.get("price_age_bucket") == "lt_300" and _num(t.get("price_age_sec")) is not None]
    stale_ge_300 = [t for t in trades if t.get("price_age_bucket") in ("300_599", "600_899", "gte_900")]
    aged_3_299 = [
        t
        for t in trades
        if t.get("price_age_bucket") == "lt_300"
        and (pa := _num(t.get("price_age_sec"))) is not None
        and pa >= PRODUCTION_MAX_PRICE_AGE_SEC
    ]
    fresh_lt_3 = [
        t
        for t in trades
        if t.get("price_age_bucket") == "lt_300"
        and (pa := _num(t.get("price_age_sec"))) is not None
        and pa < PRODUCTION_MAX_PRICE_AGE_SEC
    ]
    return {
        "fresh_lt_300": _bucket_metrics(fresh),
        "stale_ge_300": _bucket_metrics(stale_ge_300),
        "aged_3_to_299_sec": _bucket_metrics(aged_3_299),
        "fresh_lt_3_sec": _bucket_metrics(fresh_lt_3),
        "delta_avg_pnl_stale_minus_fresh_lt_300": round(
            float(_bucket_metrics(stale_ge_300).get("avg_pnl_yen_100") or 0.0)
            - float(_bucket_metrics(fresh).get("avg_pnl_yen_100") or 0.0),
            2,
        ),
        "delta_stop_hit_stale_minus_fresh_lt_300": round(
            float(_bucket_metrics(stale_ge_300).get("stop_hit_rate") or 0.0)
            - float(_bucket_metrics(fresh).get("stop_hit_rate") or 0.0),
            4,
        ),
        "delta_no_progress_stale_minus_fresh_lt_300": round(
            float(_bucket_metrics(stale_ge_300).get("no_progress_exit_rate") or 0.0)
            - float(_bucket_metrics(fresh).get("no_progress_exit_rate") or 0.0),
            4,
        ),
    }


def _stale_symbol_concentration(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    stale = [t for t in trades if t.get("price_age_bucket") in ("300_599", "600_899", "gte_900")]
    if not stale:
        return {"stale_entry_count": 0, "unique_symbols": 0, "unique_days": 0, "top_symbols": []}
    sym_counts: dict[str, int] = defaultdict(int)
    day_counts: dict[str, int] = defaultdict(int)
    for t in stale:
        sym_counts[str(t.get("symbol") or "")] += 1
        day_counts[str(t.get("day") or "")] += 1
    top = sorted(sym_counts.items(), key=lambda x: -x[1])[:10]
    return {
        "stale_entry_count": len(stale),
        "unique_symbols": len(sym_counts),
        "unique_days": len(day_counts),
        "top_symbols": [{"symbol": s, "count": c} for s, c in top],
        "top_days": [{"day": d, "count": c} for d, c in sorted(day_counts.items(), key=lambda x: -x[1])],
        "symbol_concentrated": len(sym_counts) <= 3 and len(stale) >= 3,
        "day_concentrated": len(day_counts) <= 2 and len(stale) >= 3,
    }


def decide_guard_action(
    trades: Sequence[Mapping[str, Any]],
    *,
    comparison: Mapping[str, Any],
    concentration: Mapping[str, Any],
) -> tuple[str, str]:
    stale_n = int((comparison.get("stale_ge_300") or {}).get("entry_count") or 0)
    total_n = len(trades)
    stale_share = stale_n / total_n if total_n else 0.0

    if stale_n == 0:
        return "REJECT", "No actual ENTRY with price_age_sec>=300 in the 22-day universe."

    if stale_share < 0.005 and concentration.get("day_concentrated"):
        return (
            "REJECT",
            "Stale-price ENTRY is extremely rare and concentrated on a single day/symbol cluster; "
            "not a full-period systemic issue. Existing production guard entry_max_price_age_sec=3.0 "
            "already targets freshness at accept time.",
        )

    stale = comparison.get("stale_ge_300") or {}
    fresh = comparison.get("fresh_lt_300") or {}
    worse_pnl = float(stale.get("avg_pnl_yen_100") or 0.0) < float(fresh.get("avg_pnl_yen_100") or 0.0)
    worse_np = float(stale.get("no_progress_exit_rate") or 0.0) > float(fresh.get("no_progress_exit_rate") or 0.0)
    worse_stop = float(stale.get("stop_hit_rate") or 0.0) > float(fresh.get("stop_hit_rate") or 0.0)

    if stale_n >= 20 and (worse_pnl or worse_np or worse_stop) and not concentration.get("symbol_concentrated"):
        return (
            "ADOPT",
            "Stale-price ENTRY is frequent enough and broadly distributed with worse outcomes; "
            "add/strengthen a price freshness guard.",
        )

    if stale_n < 20 and (worse_pnl or worse_np or worse_stop):
        return (
            "HOLD",
            "Stale-price ENTRY shows worse exit-quality signals but sample size is too small for adoption; "
            "monitor and keep existing entry_max_price_age_sec=3.0 guard.",
        )

    return (
        "REJECT",
        "Insufficient evidence that stale-price ENTRY materially degrades full-period performance.",
    )


def _mandatory_answers(
    trades: Sequence[Mapping[str, Any]],
    *,
    bucket_rows: Sequence[Mapping[str, Any]],
    comparison: Mapping[str, Any],
    concentration: Mapping[str, Any],
    decision: str,
) -> dict[str, Any]:
    with_age = [t for t in trades if _num(t.get("price_age_sec")) is not None]
    ages = [float(_num(t.get("price_age_sec"))) for t in with_age]
    stale = [t for t in trades if t.get("price_age_bucket") in ("300_599", "600_899", "gte_900")]
    return {
        "1_how_many_large_price_age_entries": {
            "entries_with_logged_price_age_sec": len(with_age),
            "entries_missing_price_age_sec": sum(1 for t in trades if t.get("price_age_bucket") == "missing"),
            "ge_300": sum(1 for a in ages if a >= 300),
            "ge_600": sum(1 for a in ages if a >= 600),
            "ge_900": sum(1 for a in ages if a >= 900),
            "bucket_counts": {r["bucket_id"]: r["entry_count"] for r in bucket_rows},
            "stale_entries_ge_300_detail": [
                {
                    "day": t.get("day"),
                    "symbol": t.get("symbol"),
                    "entry_time": t.get("entry_time"),
                    "price_age_sec": t.get("price_age_sec"),
                    "exit_reason": t.get("exit_reason"),
                    "pnl_yen_100": t.get("pnl_yen_100"),
                }
                for t in stale
            ],
        },
        "2_do_stale_entries_worsen_performance": comparison,
        "3_stop_hit_no_progress_increase": {
            "stale_ge_300": (comparison.get("stale_ge_300") or {}),
            "fresh_lt_300": (comparison.get("fresh_lt_300") or {}),
            "aged_3_to_299_sec": (comparison.get("aged_3_to_299_sec") or {}),
            "fresh_lt_3_sec": (comparison.get("fresh_lt_3_sec") or {}),
        },
        "4_symbol_specific_or_broad": concentration,
        "5_should_add_price_freshness_guard": {
            "decision": decision,
            "production_existing_guard_sec": PRODUCTION_MAX_PRICE_AGE_SEC,
            "note": "Analysis is observational only; no counterfactual blocking applied.",
        },
    }


def _write_decision_md(
    *,
    report: Mapping[str, Any],
    answers: Mapping[str, Any],
    decision: str,
    rationale: str,
) -> None:
    b = answers.get("1_how_many_large_price_age_entries") or {}
    comp = answers.get("2_do_stale_entries_worsen_performance") or {}
    conc = answers.get("4_symbol_specific_or_broad") or {}
    lines = [
        "# Phase663 — Price Age Freshness Decision",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        f"**Decision:** **{decision}**",
        "",
        "## Rationale",
        "",
        rationale,
        "",
        "## Mandatory answers",
        "",
        "### 1. How many large `price_age_sec` ENTRY?",
        "",
        f"- Logged `price_age_sec`: {b.get('entries_with_logged_price_age_sec')} / {report.get('entry_count')}",
        f"- Missing `price_age_sec`: {b.get('entries_missing_price_age_sec')}",
        f"- `>=300s`: {b.get('ge_300')} | `>=600s`: {b.get('ge_600')} | `>=900s`: {b.get('ge_900')}",
        "",
        "### 2. Do stale-price ENTRY worsen performance?",
        "",
        f"- Fresh `<300s` avg PnL: {(comp.get('fresh_lt_300') or {}).get('avg_pnl_yen_100')}",
        f"- Stale `>=300s` avg PnL: {(comp.get('stale_ge_300') or {}).get('avg_pnl_yen_100')}",
        "",
        "### 3. `stop_hit` / `no_progress_exit` increase?",
        "",
        f"- Fresh `<300s` stop_hit: {(comp.get('fresh_lt_300') or {}).get('stop_hit_rate')}, no_progress: {(comp.get('fresh_lt_300') or {}).get('no_progress_exit_rate')}",
        f"- Stale `>=300s` stop_hit: {(comp.get('stale_ge_300') or {}).get('stop_hit_rate')}, no_progress: {(comp.get('stale_ge_300') or {}).get('no_progress_exit_rate')}",
        f"- Within `<300s`, ages `3-299s` no_progress: {(comp.get('aged_3_to_299_sec') or {}).get('no_progress_exit_rate')} vs `<3s`: {(comp.get('fresh_lt_3_sec') or {}).get('no_progress_exit_rate')}",
        "",
        "### 4. Symbol-specific or broad?",
        "",
        f"- Stale `>=300s` entries: {conc.get('stale_entry_count')} across {conc.get('unique_symbols')} symbols / {conc.get('unique_days')} days",
        f"- Top symbols: {conc.get('top_symbols')}",
        "",
        "### 5. Price freshness guard?",
        "",
        f"- **{decision}** — production already enforces `entry_max_price_age_sec={PRODUCTION_MAX_PRICE_AGE_SEC}` at accept.",
        "",
        "## Constraints",
        "",
        "- Runtime unchanged",
        "- No shadow added",
        "- Analysis only (no counterfactual adoption test)",
        "",
    ]
    (REPORT_ROOT / "phase663_price_age_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit() -> dict[str, Any]:
    trades = load_canonical_trades()
    bucket_rows = build_bucket_rows(trades)
    am_pm_rows = build_am_pm_rows(trades)
    symbol_rows = build_symbol_rows(trades)
    daily_rows = build_daily_rows(trades)
    comparison = _compare_stale_vs_fresh(trades)
    concentration = _stale_symbol_concentration(trades)
    decision, rationale = decide_guard_action(trades, comparison=comparison, concentration=concentration)

    ages = [float(v) for v in (_num(t.get("price_age_sec")) for t in trades) if v is not None]
    report: dict[str, Any] = {
        "verdict": PHASE663_VERDICT,
        "entry_count": len(trades),
        "trading_day_count": len({t.get("day") for t in trades}),
        "session_count": len({(t.get("day"), t.get("session")) for t in trades}),
        "price_age_sec_coverage": round(len(ages) / len(trades), 4) if trades else 0.0,
        "price_age_sec_median": round(statistics.median(ages), 3) if ages else None,
        "price_age_sec_p95": round(sorted(ages)[int(len(ages) * 0.95)], 3) if ages else None,
        "production_entry_max_price_age_sec": PRODUCTION_MAX_PRICE_AGE_SEC,
        "decision": decision,
        "decision_rationale": rationale,
        "bucket_summary": bucket_rows,
        "comparison": comparison,
        "stale_concentration": concentration,
    }
    answers = _mandatory_answers(
        trades,
        bucket_rows=bucket_rows,
        comparison=comparison,
        concentration=concentration,
        decision=decision,
    )
    report["mandatory_answers"] = answers

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    _write_csv(
        REPORT_ROOT / "phase663_price_age_buckets.csv",
        [
            "bucket_id",
            "bucket_label",
            "entry_count",
            "share_of_entries",
            "win_rate",
            "profit_factor",
            "total_pnl_yen_100",
            "avg_pnl_yen_100",
            "stop_hit_rate",
            "no_progress_exit_rate",
        ],
        bucket_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase663_price_age_am_pm_summary.csv",
        [
            "session_bucket",
            "bucket_id",
            "bucket_label",
            "entry_count",
            "win_rate",
            "profit_factor",
            "total_pnl_yen_100",
            "avg_pnl_yen_100",
            "stop_hit_rate",
            "no_progress_exit_rate",
        ],
        am_pm_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase663_price_age_symbol_summary.csv",
        [
            "symbol",
            "entry_count",
            "price_age_sec_median",
            "price_age_sec_max",
            "stale_entry_count_ge_300",
            "stale_entry_share",
            "win_rate",
            "profit_factor",
            "total_pnl_yen_100",
            "avg_pnl_yen_100",
            "stop_hit_rate",
            "no_progress_exit_rate",
        ],
        symbol_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase663_price_age_daily_breakdown.csv",
        ["day", "bucket_id", "bucket_label", "entry_count", "win_rate", "profit_factor", "total_pnl_yen_100", "avg_pnl_yen_100", "stop_hit_rate", "no_progress_exit_rate"],
        daily_rows,
    )
    (REPORT_ROOT / "phase663_price_age_freshness_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_decision_md(report=report, answers=answers, decision=decision, rationale=rationale)
    return report


def main() -> int:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict"), "decision": report.get("decision"), "entry_count": report.get("entry_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
