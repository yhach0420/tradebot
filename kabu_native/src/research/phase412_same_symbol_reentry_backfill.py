"""
Phase412: Same-symbol open re-entry reject — Runtime adoption review (research backfill).

Backfills 20260529-20260616 using Phase399 Position-CAP baseline history and compares:
baseline (position-cap accepted trades) vs same_symbol_open_reentry_reject counterfactual.

Research / report only. Does not modify Runtime / YAML / Entry / Exit / Orders.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf
from research.phase382_capital_constrained_backtest import _parse_ts, _write_csv
from research.phase400_holding_time_audit import hold_seconds, normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase409_boundary_forward_shadow import load_structural_trades_for_day
from research.phase410_duplicate_reentry_audit import apply_counterfactual_policy

JST = ZoneInfo("Asia/Tokyo")

POLICY = "same_symbol_open_reentry_reject"
PERIOD_START = "20260529"
PERIOD_END = "20260616"
INITIAL_EQUITY_YEN = 1_500_000.0

PHASE399_TRADES_CSV = "phase399_historical_position_cap_backfill_trades.csv"

TRADES_FIELDS = [
    "logged_at",
    "day",
    "session",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "exit_reason",
    "baseline_included",
    "shadow_included",
    "reject_reason",
    "pnl_yen_100",
    "baseline_pnl_yen_100",
    "shadow_pnl_yen_100",
]

DAILY_FIELDS = [
    "day",
    "session_count",
    "trade_count",
    "shadow_trade_count",
    "rejected_same_symbol_open_count",
    "total_pnl_yen_100",
    "shadow_total_pnl_yen_100",
    "delta_pnl_yen_100",
    "pf",
    "shadow_pf",
    "maxdd",
    "shadow_maxdd",
    "final_equity",
    "shadow_final_equity",
    "win_rate",
    "shadow_win_rate",
    "avg_hold_sec",
    "shadow_avg_hold_sec",
    "median_hold_sec",
    "shadow_median_hold_sec",
    "overlap_replaced_review_count",
    "shadow_overlap_replaced_review_count",
    "stop_hit_count",
    "shadow_stop_hit_count",
    "trailing_mfe_count",
    "shadow_trailing_mfe_count",
    "session_close_count",
    "shadow_session_close_count",
    "affected_symbols",
    "status",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _bool(val: Any) -> bool:
    return str(val or "").strip().lower() in ("true", "1", "yes")


def _float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _replace_all_rows(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(path, list(rows), list(fields))


def _chronological_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    sort_keys = [
        (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST), i)
        for i, t in enumerate(trades)
    ]
    order = [i for _, i in sorted(sort_keys, key=lambda x: (x[0], x[1]))]
    return [_float(trades[i].get("pnl_yen_100_float") or trades[i].get("pnl_yen_100") or 0) for i in order]


def _win_rate(pnls: Sequence[float]) -> float:
    if not pnls:
        return 0.0
    wins = sum(1 for p in pnls if p > 0)
    return round(100.0 * wins / len(pnls), 2)


def _counts_by_reason(trades: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {"overlap_replaced_review": 0, "stop_hit": 0, "trailing_mfe": 0, "session_close": 0}
    for t in trades:
        bucket = normalize_exit_reason(str(t.get("exit_reason") or t.get("close_reason") or ""))
        if bucket == "overlap_replaced":
            out["overlap_replaced_review"] += 1
        elif bucket == "stop_hit":
            out["stop_hit"] += 1
        elif bucket == "trailing_mfe":
            out["trailing_mfe"] += 1
        elif bucket == "session_close":
            out["session_close"] += 1
    return out


def _ensure_hold_sec(trade: Mapping[str, Any]) -> float:
    hs = trade.get("hold_sec")
    if hs not in (None, ""):
        return _float(hs)
    return hold_seconds(str(trade.get("entry_time") or ""), str(trade.get("exit_time") or ""))


def _load_phase399_position_cap_baseline(repo_root: Path) -> list[dict[str, Any]]:
    path = repo_root / "results" / "reports" / PHASE399_TRADES_CSV
    rows = _read_csv_rows(path)
    out: list[dict[str, Any]] = []
    for r in rows:
        if not (PERIOD_START <= str(r.get("day") or "") <= "20260615"):
            continue
        if not _bool(r.get("position_cap_accepted")):
            continue
        t = dict(r)
        t["pnl_yen_100_float"] = _float(t.get("pnl_yen_100"))
        t["exit_reason"] = t.get("exit_reason") or t.get("close_reason") or ""
        t["hold_sec"] = _ensure_hold_sec(t)
        out.append(t)
    return out


def _load_baseline_trades_for_period(repo_root: Path) -> list[dict[str, Any]]:
    baseline = _load_phase399_position_cap_baseline(repo_root)
    # Phase399 ends at 6/15; append 6/16 live paper structural trades as baseline.
    day_616 = "20260616"
    if PERIOD_START <= day_616 <= PERIOD_END:
        baseline.extend(load_structural_trades_for_day(repo_root, day_616))
    # Ensure minimal fields
    for t in baseline:
        t["pnl_yen_100_float"] = _float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100"))
        t["exit_reason"] = t.get("exit_reason") or t.get("close_reason") or ""
        t["hold_sec"] = _ensure_hold_sec(t)
    baseline.sort(
        key=lambda r: (
            str(r.get("day") or ""),
            _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        )
    )
    return baseline


def _group_by_day(trades: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        day = str(t.get("day") or "")
        if not day:
            continue
        out.setdefault(day, []).append(dict(t))
    return out


def _shadow_keys(trades: Sequence[Mapping[str, Any]]) -> set[tuple[str, str, str, str]]:
    return {
        (
            str(t.get("day") or ""),
            str(t.get("session") or ""),
            str(t.get("symbol") or ""),
            str(t.get("entry_time") or ""),
        )
        for t in trades
    }


def build_trade_rows_for_day(
    baseline: Sequence[Mapping[str, Any]],
    shadow: Sequence[Mapping[str, Any]],
    *,
    day: str,
    logged_at: str,
) -> list[dict[str, Any]]:
    kept = _shadow_keys(shadow)
    rows: list[dict[str, Any]] = []
    for t in baseline:
        key = (
            str(t.get("day") or ""),
            str(t.get("session") or ""),
            str(t.get("symbol") or ""),
            str(t.get("entry_time") or ""),
        )
        included = key in kept
        pnl = _float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0)
        rows.append(
            {
                "logged_at": logged_at,
                "day": day,
                "session": t.get("session"),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "hold_sec": _ensure_hold_sec(t),
                "exit_reason": t.get("exit_reason"),
                "baseline_included": True,
                "shadow_included": included,
                "reject_reason": "" if included else POLICY,
                "pnl_yen_100": round(pnl, 2),
                "baseline_pnl_yen_100": round(pnl, 2),
                "shadow_pnl_yen_100": round(pnl, 2) if included else 0.0,
            }
        )
    return rows


def aggregate_daily(
    baseline: Sequence[Mapping[str, Any]],
    shadow: Sequence[Mapping[str, Any]],
    *,
    day: str,
) -> dict[str, Any]:
    sessions = {str(t.get("session") or "") for t in baseline if t.get("session")}
    base_pnls = [_float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0) for t in baseline]
    sh_pnls = [_float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0) for t in shadow]
    base_holds = [float(_ensure_hold_sec(t)) for t in baseline]
    sh_holds = [float(_ensure_hold_sec(t)) for t in shadow]
    base_counts = _counts_by_reason(baseline)
    sh_counts = _counts_by_reason(shadow)

    base_total = round(sum(base_pnls), 2)
    sh_total = round(sum(sh_pnls), 2)

    affected_syms = sorted(
        {
            str(t.get("symbol") or "")
            for t in baseline
            if (
                (str(t.get("day") or ""), str(t.get("session") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
                not in _shadow_keys(shadow)
            )
        }
    )

    return {
        "day": day,
        "session_count": len(sessions),
        "trade_count": len(baseline),
        "shadow_trade_count": len(shadow),
        "rejected_same_symbol_open_count": len(baseline) - len(shadow),
        "total_pnl_yen_100": base_total,
        "shadow_total_pnl_yen_100": sh_total,
        "delta_pnl_yen_100": round(sh_total - base_total, 2),
        "pf": _pf(_chronological_pnls(baseline)),
        "shadow_pf": _pf(_chronological_pnls(shadow)),
        "maxdd": _max_drawdown_yen(_chronological_pnls(baseline)),
        "shadow_maxdd": _max_drawdown_yen(_chronological_pnls(shadow)),
        "final_equity": round(INITIAL_EQUITY_YEN + base_total, 2),
        "shadow_final_equity": round(INITIAL_EQUITY_YEN + sh_total, 2),
        "win_rate": _win_rate(base_pnls),
        "shadow_win_rate": _win_rate(sh_pnls),
        "avg_hold_sec": round(sum(base_holds) / len(base_holds), 2) if base_holds else 0.0,
        "shadow_avg_hold_sec": round(sum(sh_holds) / len(sh_holds), 2) if sh_holds else 0.0,
        "median_hold_sec": round(median(base_holds), 2) if base_holds else 0.0,
        "shadow_median_hold_sec": round(median(sh_holds), 2) if sh_holds else 0.0,
        "overlap_replaced_review_count": int(base_counts["overlap_replaced_review"]),
        "shadow_overlap_replaced_review_count": int(sh_counts["overlap_replaced_review"]),
        "stop_hit_count": int(base_counts["stop_hit"]),
        "shadow_stop_hit_count": int(sh_counts["stop_hit"]),
        "trailing_mfe_count": int(base_counts["trailing_mfe"]),
        "shadow_trailing_mfe_count": int(sh_counts["trailing_mfe"]),
        "session_close_count": int(base_counts["session_close"]),
        "shadow_session_close_count": int(sh_counts["session_close"]),
        "affected_symbols": ",".join([s for s in affected_syms if s]),
        "status": "ok",
    }


def aggregate_cumulative(daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    raise RuntimeError("aggregate_cumulative requires trade lists; use aggregate_cumulative_from_trades()")


def aggregate_cumulative_from_trades(
    baseline_trades: Sequence[Mapping[str, Any]],
    shadow_trades: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [r for r in daily_rows if PERIOD_START <= str(r.get("day") or "") <= PERIOD_END]

    base_pnls = [_float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0) for t in baseline_trades]
    sh_pnls = [_float(t.get("pnl_yen_100_float") or t.get("pnl_yen_100") or 0) for t in shadow_trades]
    base_total = round(sum(base_pnls), 2)
    sh_total = round(sum(sh_pnls), 2)

    base_chron = _chronological_pnls(baseline_trades)
    sh_chron = _chronological_pnls(shadow_trades)
    base_pf = _pf(base_chron)
    sh_pf = _pf(sh_chron)
    base_dd = _max_drawdown_yen(base_chron)
    sh_dd = _max_drawdown_yen(sh_chron)

    base_trades = len(baseline_trades)
    sh_trades = len(shadow_trades)

    base_counts = _counts_by_reason(baseline_trades)
    sh_counts = _counts_by_reason(shadow_trades)

    affected_days = sorted(
        [
            str(r.get("day") or "")
            for r in rows
            if int(_float(r.get("rejected_same_symbol_open_count"))) > 0
        ]
    )
    affected_symbols = sorted(
        {s for r in rows for s in str(r.get("affected_symbols") or "").split(",") if s.strip()}
    )

    verdict = "reject_runtime_adoption"
    adopt_allowed = (
        sh_total >= base_total
        and (sh_pf or 0) >= (base_pf or 0)
        and sh_dd <= base_dd + 1e-6
        and (base_trades - sh_trades) >= 1
        and (base_counts["overlap_replaced_review"] - sh_counts["overlap_replaced_review"]) >= 1
    )
    if adopt_allowed:
        verdict = "runtime_adoption_candidate"

    return {
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "initial_equity_yen": INITIAL_EQUITY_YEN,
        "baseline_trade_count": int(base_trades),
        "shadow_trade_count": int(sh_trades),
        "trade_reduction_count": base_trades - sh_trades,
        "rejected_same_symbol_open_count": base_trades - sh_trades,
        "baseline_total_pnl_yen_100": float(base_total),
        "shadow_total_pnl_yen_100": float(sh_total),
        "delta_pnl_yen_100": round(sh_total - base_total, 2),
        "baseline_pf": base_pf,
        "shadow_pf": sh_pf,
        "baseline_maxdd": float(base_dd),
        "shadow_maxdd": float(sh_dd),
        "baseline_final_equity": round(INITIAL_EQUITY_YEN + base_total, 2),
        "shadow_final_equity": round(INITIAL_EQUITY_YEN + sh_total, 2),
        "baseline_win_rate": _win_rate(base_pnls),
        "shadow_win_rate": _win_rate(sh_pnls),
        "baseline_avg_hold_sec": round(
            sum(float(_ensure_hold_sec(t)) for t in baseline_trades) / max(1, len(baseline_trades)), 2
        ),
        "shadow_avg_hold_sec": round(
            sum(float(_ensure_hold_sec(t)) for t in shadow_trades) / max(1, len(shadow_trades)), 2
        ),
        "baseline_median_hold_sec": round(
            median([float(_ensure_hold_sec(t)) for t in baseline_trades]), 2
        )
        if baseline_trades
        else 0.0,
        "shadow_median_hold_sec": round(
            median([float(_ensure_hold_sec(t)) for t in shadow_trades]), 2
        )
        if shadow_trades
        else 0.0,
        "baseline_overlap_replaced_review_count": int(base_counts["overlap_replaced_review"]),
        "shadow_overlap_replaced_review_count": int(sh_counts["overlap_replaced_review"]),
        "overlap_replaced_review_reduction_count": int(
            base_counts["overlap_replaced_review"] - sh_counts["overlap_replaced_review"]
        ),
        "baseline_stop_hit_count": int(base_counts["stop_hit"]),
        "shadow_stop_hit_count": int(sh_counts["stop_hit"]),
        "baseline_trailing_mfe_count": int(base_counts["trailing_mfe"]),
        "shadow_trailing_mfe_count": int(sh_counts["trailing_mfe"]),
        "baseline_session_close_count": int(base_counts["session_close"]),
        "shadow_session_close_count": int(sh_counts["session_close"]),
        "affected_days": affected_days,
        "affected_symbols": affected_symbols,
        "adoption_gate": {
            "pnl_ge_baseline": sh_total >= base_total,
            "pf_ge_baseline": (sh_pf or 0) >= (base_pf or 0),
            "maxdd_le_baseline": sh_dd <= base_dd + 1e-6,
            "trade_count_reduced": (base_trades - sh_trades) > 0,
            "overlap_reduced": (base_counts["overlap_replaced_review"] - sh_counts["overlap_replaced_review"]) > 0,
        },
        "verdict": verdict,
        "runtime_yaml_change_forbidden": True,
        "policy": POLICY,
    }


def render_report_md(summary: Mapping[str, Any], *, day_616_check: Mapping[str, Any]) -> str:
    verdict = str(summary.get("verdict") or "")
    allow = verdict == "runtime_adoption_candidate"
    lines: list[str] = []
    lines.append("# Phase412 — Same-Symbol Reentry Reject Runtime Adoption Review")
    lines.append("")
    lines.append("## Conclusion")
    lines.append("")
    lines.append(f"- **Runtime反映してよいか**: {'YES (candidate)' if allow else 'NO (do not adopt)'}")
    lines.append(f"- **理由**: verdict=`{verdict}` (baseline vs shadow backfill gate)")
    lines.append("- **悪化リスク**: 同一銘柄の重複ENTRYを抑止するため、短期の再ENTRYで取りに行く局面があれば機会損失になり得る。")
    lines.append("- **rollback方法**: `same_symbol_open_reentry_policy: replace`（既存互換）")
    lines.append("")
    lines.append("## Backfill summary (20260529–20260616)")
    lines.append("")
    lines.append(f"- baseline_trade_count: {summary.get('baseline_trade_count')}")
    lines.append(f"- shadow_trade_count: {summary.get('shadow_trade_count')}")
    lines.append(f"- trade_reduction_count: {summary.get('trade_reduction_count')}")
    lines.append(f"- baseline_total_pnl_yen_100: {summary.get('baseline_total_pnl_yen_100')}")
    lines.append(f"- shadow_total_pnl_yen_100: {summary.get('shadow_total_pnl_yen_100')}")
    lines.append(f"- delta_pnl_yen_100: {summary.get('delta_pnl_yen_100')}")
    lines.append(f"- baseline_pf: {summary.get('baseline_pf')}")
    lines.append(f"- shadow_pf: {summary.get('shadow_pf')}")
    lines.append(f"- baseline_maxdd: {summary.get('baseline_maxdd')}")
    lines.append(f"- shadow_maxdd: {summary.get('shadow_maxdd')}")
    lines.append(f"- baseline_overlap_replaced_review_count: {summary.get('baseline_overlap_replaced_review_count')}")
    lines.append(f"- shadow_overlap_replaced_review_count: {summary.get('shadow_overlap_replaced_review_count')}")
    lines.append(f"- overlap_replaced_review_reduction_count: {summary.get('overlap_replaced_review_reduction_count')}")
    lines.append("")
    lines.append("## Mandatory check — 20260616 matches Phase411")
    lines.append("")
    lines.append(f"- 20260616 baseline trades: {day_616_check.get('baseline_trade_count')}")
    lines.append(f"- 20260616 shadow trades: {day_616_check.get('shadow_trade_count')}")
    lines.append(f"- 20260616 shadow PnL: {day_616_check.get('shadow_total_pnl_yen_100')}")
    lines.append(f"- 20260616 shadow PF: {day_616_check.get('shadow_pf')}")
    lines.append(f"- 20260616 shadow maxDD: {day_616_check.get('shadow_maxdd')}")
    lines.append("")
    return "\n".join(lines)


def run_phase412_backfill(*, repo_root: Path, reports_dir: Path) -> dict[str, Any]:
    logged_at = _now_iso()
    baseline = _load_baseline_trades_for_period(repo_root)
    by_day = _group_by_day(baseline)

    trade_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    baseline_all: list[dict[str, Any]] = []
    shadow_all: list[dict[str, Any]] = []

    for day in sorted(d for d in by_day.keys() if PERIOD_START <= d <= PERIOD_END):
        base_day = sorted(
            by_day.get(day, []),
            key=lambda r: (_parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)),
        )
        shadow_day = apply_counterfactual_policy(base_day, policy=POLICY)
        baseline_all.extend(dict(t) for t in base_day)
        shadow_all.extend(dict(t) for t in shadow_day)
        trade_rows.extend(build_trade_rows_for_day(base_day, shadow_day, day=day, logged_at=logged_at))
        daily_rows.append(aggregate_daily(base_day, shadow_day, day=day))

    summary = aggregate_cumulative_from_trades(baseline_all, shadow_all, daily_rows)
    day_616 = next((r for r in daily_rows if str(r.get("day") or "") == "20260616"), {})

    paths = Phase412BackfillJob(repo_root=repo_root, reports_dir=reports_dir).paths()
    _replace_all_rows(paths["trades"], trade_rows, TRADES_FIELDS)
    _replace_all_rows(paths["daily"], daily_rows, DAILY_FIELDS)
    payload = {
        "phase": 412,
        "generated_at": logged_at,
        "policy": POLICY,
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "inputs": {
            "phase399_trades_csv": str(repo_root / "results" / "reports" / PHASE399_TRADES_CSV),
            "day_616_source": "results/small_paper/20260616/*/structural_trades.csv",
        },
        "summary": summary,
        "day_616_check": day_616,
        "output_paths": {k: str(v) for k, v in paths.items()},
        "constraints": {
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
            "exit_change_forbidden": True,
            "order_change_forbidden": True,
            "yaml_change_forbidden": True,
            "discord_change_forbidden": True,
        },
    }
    paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_md = render_report_md(summary, day_616_check=day_616)
    report_path = repo_root / "docs" / "operations" / "phase412_same_symbol_reentry_adoption_review.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_md, encoding="utf-8")

    return payload


@dataclass
class Phase412BackfillJob:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "trades": self.reports_dir / "phase412_same_symbol_reentry_backfill_trades.csv",
            "daily": self.reports_dir / "phase412_same_symbol_reentry_backfill_daily.csv",
            "summary": self.reports_dir / "phase412_same_symbol_reentry_backfill_summary.json",
        }

    def run(self) -> dict[str, Any]:
        return run_phase412_backfill(repo_root=self.repo_root, reports_dir=self.reports_dir)

