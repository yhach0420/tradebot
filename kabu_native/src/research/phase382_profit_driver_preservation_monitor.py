"""
Phase382: Profit driver preservation monitor for Stack C (Phase355+Phase364).

Daily monitoring only — no ENTRY/EXIT/Universe changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase377_daily_regime_breakdown import PERIOD_B_START, PRIMARY_STACK
from research.phase379_380_period_b_eval import evaluate_variant_shadow, is_low_mfe_stop
from research.phase381_winner_profile_review import (
    _board_tier,
    enrich_trade_with_rank,
    is_winning,
    top_set_composition,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_BASELINE_JSON = "phase381_winner_profile_summary.json"
LOW_MFE_SPIKE_MULTIPLIER = 2.5
MIN_DRIVER_PNL_YEN = 1.0

BY_DAY_FIELDS = [
    "day",
    "trade_count",
    "winning_count",
    "total_pnl_yen_100",
    "trailing_mfe_exit_pnl",
    "trailing_mfe_exit_count",
    "overlap_replaced_pnl",
    "overlap_replaced_count",
    "rank_21_30_winning_pnl",
    "rank_31_40_winning_pnl",
    "rank_21_40_winning_pnl",
    "board_low_pnl",
    "board_low_winner_count",
    "am_winning_pnl",
    "pm_winning_pnl",
    "low_mfe_stop_hit_count",
    "top10_overlap_share",
    "top10_trailing_share",
    "top10_exit_reason_json",
    "preservation_status",
    "preservation_flags_json",
]

CHECKS_FIELDS = [
    "day",
    "preservation_status",
    "trailing_mfe_exit_pnl_ok",
    "overlap_replaced_pnl_ok",
    "rank_21_40_winning_pnl_ok",
    "board_low_winners_ok",
    "low_mfe_stop_spike",
    "top10_overlap_share_ok",
    "top10_trailing_share_ok",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _day_in_monitor_range(day: str, *, min_day: str, max_day: Optional[str]) -> bool:
    if day < min_day:
        return False
    if max_day and day > max_day:
        return False
    return True


def _is_rank_21_40(trade: Mapping[str, Any]) -> bool:
    bucket = str(trade.get("dynamic40_rank_bucket") or "")
    return bucket in ("rank_21_30", "rank_31_40")


def load_baseline_reference(reports_dir: Path, *, baseline_name: str = DEFAULT_BASELINE_JSON) -> dict[str, Any]:
    path = reports_dir / baseline_name
    if not path.is_file():
        return {"loaded": False, "source": str(path)}
    data = json.loads(path.read_text(encoding="utf-8"))
    fj = data.get("final_judgment") or {}
    top100 = (data.get("top_profit_compositions") or {}).get("100") or {}
    exit_counts = top100.get("exit_reason_counts") or {}
    top_n = max(int(top100.get("trade_count") or 1), 1)
    return {
        "loaded": True,
        "source": str(path),
        "period": data.get("period"),
        "trade_count": data.get("trade_count"),
        "winning_count": data.get("winning_count"),
        "total_pnl_yen_100": data.get("total_pnl_yen_100"),
        "trailing_mfe_pnl": fj.get("trailing_mfe_pnl"),
        "overlap_pnl": fj.get("overlap_pnl"),
        "board_low_winner_count": fj.get("board_low_winner_count"),
        "am_winning_pnl": fj.get("am_winning_pnl"),
        "pm_winning_pnl": fj.get("pm_winning_pnl"),
        "preserve_profile": fj.get("preserve_profile") or [],
        "top100_overlap_share": round(int(exit_counts.get("overlap_replaced") or 0) / top_n, 4),
        "top100_trailing_share": round(int(exit_counts.get("trailing_mfe_exit") or 0) / top_n, 4),
        "low_mfe_stop_hit_count": sum(
            1
            for row in (data.get("winning_vs_low_mfe_compare") or [])
            if row.get("feature") == "peak_mfe_pct"
        ),
    }


def load_session_profit_driver_trades(
    session_meta: Mapping[str, Any],
    *,
    reports_dir: Path,
    min_day: str = PERIOD_B_START,
    max_day: Optional[str] = None,
) -> dict[str, Any]:
    from research.phase365_production_stack_validation import load_session_production_stack_trades
    from research.phase366_stophit_reclassification import production_kept_trades
    from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

    day = str(session_meta.get("day_key") or session_meta.get("day") or "")
    if not _day_in_monitor_range(day, min_day=min_day, max_day=max_day):
        return {"error": "outside_monitor_range", "trades": [], "all_trades": []}

    base = load_session_production_stack_trades(session_meta, reports_dir=reports_dir)
    if base.get("error"):
        return {**base, "trades": [], "all_trades": []}

    sess_dir = Path(str(session_meta["session_dir"]))
    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    trades: list[dict[str, Any]] = []
    for t in production_kept_trades(base):
        key = (t.get("symbol", ""), t.get("entry_time", ""))
        acc = accepted.get(key, {})
        row = enrich_trade_with_rank(t, acc, session_meta=session_meta, reports_dir=reports_dir)
        row["day_key"] = day
        trades.append(row)

    return {
        **base,
        "day_key": day,
        "all_trades": trades,
        "trade_count": len(trades),
        "error": "",
    }


def _sum_pnl(trades: Sequence[Mapping[str, Any]]) -> float:
    return round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades), 2)


def _top10_exit_shares(winning: Sequence[Mapping[str, Any]]) -> tuple[float, float, dict[str, int]]:
    if not winning:
        return 0.0, 0.0, {}
    comp = top_set_composition(winning, min(10, len(winning)))
    counts = comp.get("exit_reason_counts") or {}
    n = max(int(comp.get("trade_count") or 0), 1)
    overlap_share = round(int(counts.get("overlap_replaced") or 0) / n, 4)
    trailing_share = round(int(counts.get("trailing_mfe_exit") or 0) / n, 4)
    return overlap_share, trailing_share, {k: int(v) for k, v in counts.items()}


def compute_daily_driver_metrics(day: str, trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    winning = [t for t in trades if is_winning(t)]
    trailing = [
        t for t in trades if str(t.get("exit_reason_canonical") or "") == "trailing_mfe_exit"
    ]
    overlap = [
        t for t in trades if str(t.get("exit_reason_canonical") or "") == "overlap_replaced"
    ]
    low_mfe = [t for t in trades if is_low_mfe_stop(t)]
    rank_21_30_win = [
        t
        for t in winning
        if str(t.get("dynamic40_rank_bucket") or "") == "rank_21_30"
    ]
    rank_31_40_win = [
        t
        for t in winning
        if str(t.get("dynamic40_rank_bucket") or "") == "rank_31_40"
    ]
    rank_21_40_win = [t for t in winning if _is_rank_21_40(t)]
    board_low_win = [t for t in winning if _board_tier(t) == "board_low"]
    am_win = [t for t in winning if str(t.get("session_kind") or "").lower() == "am"]
    pm_win = [t for t in winning if str(t.get("session_kind") or "").lower() == "pm"]

    overlap_share, trailing_share, top10_exit = _top10_exit_shares(winning)
    rank_21_40_pnl = _sum_pnl(rank_21_40_win)

    flags = {
        "trailing_mfe_exit_pnl_ok": _sum_pnl(trailing) > MIN_DRIVER_PNL_YEN,
        "overlap_replaced_pnl_ok": _sum_pnl(overlap) > MIN_DRIVER_PNL_YEN,
        "rank_21_40_winning_pnl_ok": rank_21_40_pnl > MIN_DRIVER_PNL_YEN,
        "board_low_winners_ok": len(board_low_win) > 0,
        "low_mfe_stop_spike": False,
        "top10_overlap_share_ok": overlap_share >= 0.2 if winning else True,
        "top10_trailing_share_ok": trailing_share >= 0.15 if winning else True,
    }

    status = _day_preservation_status(flags, trade_count=len(trades), winning_count=len(winning))

    return {
        "day": day,
        "trade_count": len(trades),
        "winning_count": len(winning),
        "total_pnl_yen_100": _sum_pnl(trades),
        "trailing_mfe_exit_pnl": _sum_pnl(trailing),
        "trailing_mfe_exit_count": len(trailing),
        "overlap_replaced_pnl": _sum_pnl(overlap),
        "overlap_replaced_count": len(overlap),
        "rank_21_30_winning_pnl": _sum_pnl(rank_21_30_win),
        "rank_31_40_winning_pnl": _sum_pnl(rank_31_40_win),
        "rank_21_40_winning_pnl": rank_21_40_pnl,
        "board_low_pnl": _sum_pnl(board_low_win),
        "board_low_winner_count": len(board_low_win),
        "am_winning_pnl": _sum_pnl(am_win),
        "pm_winning_pnl": _sum_pnl(pm_win),
        "low_mfe_stop_hit_count": len(low_mfe),
        "top10_overlap_share": overlap_share,
        "top10_trailing_share": trailing_share,
        "top10_exit_reason_json": json.dumps(top10_exit, ensure_ascii=False),
        "preservation_status": status,
        "preservation_flags_json": json.dumps(flags, ensure_ascii=False),
        "_flags": flags,
    }


def _day_preservation_status(
    flags: Mapping[str, bool], *, trade_count: int, winning_count: int
) -> str:
    if trade_count <= 0:
        return "no_trades"
    critical = [
        flags.get("trailing_mfe_exit_pnl_ok"),
        flags.get("overlap_replaced_pnl_ok"),
        flags.get("rank_21_40_winning_pnl_ok"),
        flags.get("board_low_winners_ok"),
    ]
    if winning_count > 0 and not any(critical):
        return "fail"
    warn_keys = (
        "trailing_mfe_exit_pnl_ok",
        "overlap_replaced_pnl_ok",
        "rank_21_40_winning_pnl_ok",
        "board_low_winners_ok",
        "top10_overlap_share_ok",
        "top10_trailing_share_ok",
    )
    if winning_count > 0 and any(flags.get(k) is False for k in warn_keys):
        return "warn"
    if flags.get("low_mfe_stop_spike"):
        return "warn"
    return "ok"


def apply_low_mfe_spike_flags(by_day_rows: list[dict[str, Any]], *, multiplier: float) -> None:
    counts = [int(r.get("low_mfe_stop_hit_count") or 0) for r in by_day_rows if r.get("day")]
    if not counts:
        return
    baseline = statistics.median(counts)
    threshold = max(baseline * multiplier, baseline + 5.0, 15.0)
    for row in by_day_rows:
        flags = dict(row.get("_flags") or json.loads(row.get("preservation_flags_json") or "{}"))
        spike = int(row.get("low_mfe_stop_hit_count") or 0) > threshold
        flags["low_mfe_stop_spike"] = spike
        row["_flags"] = flags
        row["preservation_flags_json"] = json.dumps(flags, ensure_ascii=False)
        if spike and row.get("preservation_status") == "ok":
            row["preservation_status"] = "warn"


def evaluate_preservation_shadow(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    specs = [
        (
            "E_confirm_overlap_cut",
            lambda t: str(t.get("exit_reason_canonical") or "") == "overlap_replaced",
        ),
        (
            "F_confirm_trailing_cut",
            lambda t: str(t.get("exit_reason_canonical") or "") == "trailing_mfe_exit",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for variant_id, block in specs:
        metrics = evaluate_variant_shadow(trades, variant_id=variant_id, would_block=block)
        rows.append(
            {
                "variant_id": variant_id,
                "delta_yen": metrics.get("delta_yen"),
                "delta_pf": metrics.get("delta_pf"),
                "would_hurt": float(metrics.get("delta_yen") or 0.0) < 0.0,
            }
        )
    return rows


def evaluate_window_preservation_checks(
    *,
    by_day_rows: Sequence[Mapping[str, Any]],
    window_metrics: Mapping[str, Any],
    baseline: Mapping[str, Any],
    shadow_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    overlap_row = next((r for r in shadow_rows if r.get("variant_id") == "E_confirm_overlap_cut"), {})
    trailing_row = next((r for r in shadow_rows if r.get("variant_id") == "F_confirm_trailing_cut"), {})

    winning_pnl = float(window_metrics.get("winning_pnl_yen_100") or 0.0)
    trailing_pnl = float(window_metrics.get("trailing_mfe_exit_pnl") or 0.0)
    overlap_pnl = float(window_metrics.get("overlap_replaced_pnl") or 0.0)
    driver_pnl = trailing_pnl + overlap_pnl
    trailing_share = round(trailing_pnl / driver_pnl, 4) if driver_pnl > 0 else 0.0
    overlap_share = round(overlap_pnl / driver_pnl, 4) if driver_pnl > 0 else 0.0

    low_mfe_counts = [int(r.get("low_mfe_stop_hit_count") or 0) for r in by_day_rows]
    low_mfe_median = round(statistics.median(low_mfe_counts), 2) if low_mfe_counts else 0.0
    low_mfe_max = max(low_mfe_counts) if low_mfe_counts else 0
    spike_threshold = max(low_mfe_median * LOW_MFE_SPIKE_MULTIPLIER, low_mfe_median + 5.0, 15.0)

    status_days = Counter(str(r.get("preservation_status") or "") for r in by_day_rows)
    checks = {
        "overlap_cut_would_hurt": bool(overlap_row.get("would_hurt")),
        "trailing_cut_would_hurt": bool(trailing_row.get("would_hurt")),
        "overlap_pnl_present": overlap_pnl > MIN_DRIVER_PNL_YEN,
        "trailing_pnl_present": trailing_pnl > MIN_DRIVER_PNL_YEN,
        "rank_21_40_active": float(window_metrics.get("rank_21_40_winning_pnl") or 0.0) > MIN_DRIVER_PNL_YEN,
        "board_low_winners_present": int(window_metrics.get("board_low_winner_count") or 0) > 0,
        "low_mfe_stop_not_spiking": low_mfe_max <= spike_threshold,
        "top10_overlap_share_ok": float(window_metrics.get("top10_overlap_share") or 0.0) >= float(
            baseline.get("top100_overlap_share") or 0.0
        )
        * 0.5,
        "top10_trailing_share_ok": float(window_metrics.get("top10_trailing_share") or 0.0) >= float(
            baseline.get("top100_trailing_share") or 0.0
        )
        * 0.5,
        "driver_pnl_share_trailing": trailing_share,
        "driver_pnl_share_overlap": overlap_share,
        "low_mfe_stop_median_daily": low_mfe_median,
        "low_mfe_stop_max_daily": low_mfe_max,
        "low_mfe_spike_threshold": round(spike_threshold, 2),
        "days_ok": int(status_days.get("ok", 0)),
        "days_warn": int(status_days.get("warn", 0)),
        "days_fail": int(status_days.get("fail", 0)),
        "days_no_trades": int(status_days.get("no_trades", 0)),
    }

    fail_count = int(checks["days_fail"])
    warn_count = int(checks["days_warn"])
    if fail_count > 0 or not checks["overlap_cut_would_hurt"] or not checks["trailing_cut_would_hurt"]:
        overall = "fail"
    elif warn_count > 0 or not checks["low_mfe_stop_not_spiking"]:
        overall = "warn"
    elif winning_pnl > 0 and (
        not checks["overlap_pnl_present"] or not checks["trailing_pnl_present"]
    ):
        overall = "warn"
    else:
        overall = "ok"

    recommendation = _preservation_recommendation(overall, checks, baseline)
    return {
        "checks": checks,
        "preservation_status": overall,
        "recommendation": recommendation,
    }


def _preservation_recommendation(
    status: str, checks: Mapping[str, Any], baseline: Mapping[str, Any]
) -> str:
    preserve = baseline.get("preserve_profile") or [
        "trailing_mfe_exit",
        "overlap_replaced",
        "board_low",
        "rank_21_40_dynamic",
    ]
    if status == "ok":
        return "利益源維持 - EXIT/Universe変更禁止、現行stack継続"
    if not checks.get("trailing_cut_would_hurt") or not checks.get("overlap_cut_would_hurt"):
        return "CRITICAL: trailing/overlap利益源が弱体化 - EXIT維持を最優先"
    missing = []
    if not checks.get("overlap_pnl_present"):
        missing.append("overlap_replaced")
    if not checks.get("trailing_pnl_present"):
        missing.append("trailing_mfe_exit")
    if not checks.get("rank_21_40_active"):
        missing.append("rank_21_40")
    if not checks.get("board_low_winners_present"):
        missing.append("board_low")
    if not checks.get("low_mfe_stop_not_spiking"):
        missing.append("low_mfe_stop_spike")
    if missing:
        return f"監視警告: {', '.join(missing)} を確認 - preserve {preserve}"
    return f"監視注意 - preserve {preserve}"


def aggregate_window_metrics(by_day_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    totals = defaultdict(float)
    counts = defaultdict(int)
    overlap_shares: list[float] = []
    trailing_shares: list[float] = []
    for row in by_day_rows:
        for key in (
            "trade_count",
            "winning_count",
            "total_pnl_yen_100",
            "trailing_mfe_exit_pnl",
            "overlap_replaced_pnl",
            "rank_21_40_winning_pnl",
            "board_low_pnl",
            "am_winning_pnl",
            "pm_winning_pnl",
            "low_mfe_stop_hit_count",
            "trailing_mfe_exit_count",
            "overlap_replaced_count",
            "board_low_winner_count",
        ):
            val = row.get(key)
            if val is None:
                continue
            if key.endswith("_count") or key in ("trade_count", "winning_count"):
                counts[key] += int(val)
            else:
                totals[key] += float(val)
        if int(row.get("winning_count") or 0) > 0:
            overlap_shares.append(float(row.get("top10_overlap_share") or 0.0))
            trailing_shares.append(float(row.get("top10_trailing_share") or 0.0))

    return {
        "trade_count": counts["trade_count"],
        "winning_count": counts["winning_count"],
        "winning_pnl_yen_100": round(
            totals["am_winning_pnl"] + totals["pm_winning_pnl"], 2
        ),
        "total_pnl_yen_100": round(totals["total_pnl_yen_100"], 2),
        "trailing_mfe_exit_pnl": round(totals["trailing_mfe_exit_pnl"], 2),
        "trailing_mfe_exit_count": counts["trailing_mfe_exit_count"],
        "overlap_replaced_pnl": round(totals["overlap_replaced_pnl"], 2),
        "overlap_replaced_count": counts["overlap_replaced_count"],
        "rank_21_40_winning_pnl": round(totals["rank_21_40_winning_pnl"], 2),
        "board_low_pnl": round(totals["board_low_pnl"], 2),
        "board_low_winner_count": counts["board_low_winner_count"],
        "am_winning_pnl": round(totals["am_winning_pnl"], 2),
        "pm_winning_pnl": round(totals["pm_winning_pnl"], 2),
        "low_mfe_stop_hit_count": counts["low_mfe_stop_hit_count"],
        "top10_overlap_share": round(statistics.mean(overlap_shares), 4) if overlap_shares else 0.0,
        "top10_trailing_share": round(statistics.mean(trailing_shares), 4) if trailing_shares else 0.0,
    }


def build_checks_csv_rows(by_day_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in by_day_rows:
        flags = row.get("_flags") or json.loads(row.get("preservation_flags_json") or "{}")
        rows.append(
            {
                "day": row.get("day"),
                "preservation_status": row.get("preservation_status"),
                "trailing_mfe_exit_pnl_ok": flags.get("trailing_mfe_exit_pnl_ok"),
                "overlap_replaced_pnl_ok": flags.get("overlap_replaced_pnl_ok"),
                "rank_21_40_winning_pnl_ok": flags.get("rank_21_40_winning_pnl_ok"),
                "board_low_winners_ok": flags.get("board_low_winners_ok"),
                "low_mfe_stop_spike": flags.get("low_mfe_stop_spike"),
                "top10_overlap_share_ok": flags.get("top10_overlap_share_ok"),
                "top10_trailing_share_ok": flags.get("top10_trailing_share_ok"),
            }
        )
    return rows


def build_report(summary: Mapping[str, Any]) -> str:
    wm = summary.get("window_metrics") or {}
    checks = (summary.get("preservation_checks") or {}).get("checks") or {}
    baseline = summary.get("baseline_reference") or {}
    verdict = summary.get("final_verdict") or {}
    lines = [
        "# Phase382 Profit Driver Preservation Monitor",
        "",
        f"**期間:** {summary.get('population', {}).get('min_day')}–{summary.get('population', {}).get('max_day') or 'open'}",
        f"**Stack:** {summary.get('stack_id')}",
        "",
        "## 結論",
        "",
        f"- **preservation_status:** {verdict.get('preservation_status')}",
        f"- **recommendation:** {verdict.get('recommendation')}",
        "",
        "## Window metrics",
        "",
        f"- trailing_mfe_exit_pnl: {wm.get('trailing_mfe_exit_pnl')}",
        f"- overlap_replaced_pnl: {wm.get('overlap_replaced_pnl')}",
        f"- rank_21_40_winning_pnl: {wm.get('rank_21_40_winning_pnl')}",
        f"- board_low_winner_count: {wm.get('board_low_winner_count')}",
        f"- am_winning_pnl: {wm.get('am_winning_pnl')}",
        f"- pm_winning_pnl: {wm.get('pm_winning_pnl')}",
        f"- low_mfe_stop_hit_count: {wm.get('low_mfe_stop_hit_count')}",
        "",
        "## Baseline (Phase381)",
        "",
        f"- trailing_mfe_pnl: {baseline.get('trailing_mfe_pnl')}",
        f"- overlap_pnl: {baseline.get('overlap_pnl')}",
        f"- board_low_winner_count: {baseline.get('board_low_winner_count')}",
        "",
        "## Preservation checks",
        "",
    ]
    for key, val in sorted(checks.items()):
        lines.append(f"- {key}: {val}")
    lines.append("")
    lines.append("## Daily status")
    lines.append("")
    for row in summary.get("by_day") or []:
        lines.append(
            f"- {row.get('day')}: {row.get('preservation_status')} "
            f"trailing={row.get('trailing_mfe_exit_pnl')} overlap={row.get('overlap_replaced_pnl')} "
            f"low_mfe_stop={row.get('low_mfe_stop_hit_count')}"
        )
    lines.append("")
    lines.append("## 禁止事項")
    lines.append("")
    lines.append("- 新規ガード追加禁止")
    lines.append("- EXIT変更禁止")
    lines.append("- Universe縮小禁止")
    return "\n".join(lines) + "\n"


@dataclass
class Phase382ProfitDriverPreservationMonitor:
    reports_dir: Path
    min_day: str = PERIOD_B_START
    max_day: Optional[str] = None
    baseline_name: str = DEFAULT_BASELINE_JSON
    all_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase382_profit_driver_preservation_summary.json",
            "by_day": self.reports_dir / "phase382_profit_driver_preservation_by_day.csv",
            "checks": self.reports_dir / "phase382_profit_driver_preservation_checks.csv",
            "report": self.reports_dir / "phase382_profit_driver_preservation_report.md",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.all_trades.extend(result.get("all_trades") or [])

    def analyze(
        self,
        *,
        sessions_discovered: int = 0,
        sessions_evaluated: int = 0,
        wall_runtime_sec: float = 0.0,
    ) -> dict[str, Any]:
        baseline = load_baseline_reference(self.reports_dir, baseline_name=self.baseline_name)
        by_day_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade in self.all_trades:
            day = str(trade.get("day_key") or "")
            if day:
                by_day_map[day].append(trade)

        by_day_rows = [
            compute_daily_driver_metrics(day, by_day_map[day])
            for day in sorted(by_day_map)
        ]
        apply_low_mfe_spike_flags(by_day_rows, multiplier=LOW_MFE_SPIKE_MULTIPLIER)

        for row in by_day_rows:
            flags = row.pop("_flags", {})
            row["preservation_flags_json"] = json.dumps(flags, ensure_ascii=False)

        window_metrics = aggregate_window_metrics(by_day_rows)
        shadow_rows = evaluate_preservation_shadow(self.all_trades)
        preservation = evaluate_window_preservation_checks(
            by_day_rows=by_day_rows,
            window_metrics=window_metrics,
            baseline=baseline,
            shadow_rows=shadow_rows,
        )

        consistency: dict[str, Any] = {"phase381_baseline_loaded": bool(baseline.get("loaded"))}
        p376 = self.reports_dir / "phase376_production_daily_pnl.csv"
        if p376.is_file():
            consistency["phase376_csv_present"] = True

        return {
            "phase": 382,
            "title": "Profit driver preservation monitor",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "stack_id": PRIMARY_STACK,
            "population": {
                "min_day": self.min_day,
                "max_day": self.max_day,
                "sessions_discovered": sessions_discovered,
                "sessions_evaluated": sessions_evaluated,
                "trade_count": len(self.all_trades),
            },
            "baseline_reference": baseline,
            "window_metrics": window_metrics,
            "preservation_checks": preservation,
            "shadow_variants_ef": shadow_rows,
            "by_day": [
                {k: v for k, v in row.items() if not k.startswith("_")}
                for row in by_day_rows
            ],
            "final_verdict": {
                "preservation_status": preservation.get("preservation_status"),
                "recommendation": preservation.get("recommendation"),
            },
            "consistency_checks": consistency,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "output_note": "Monitoring only; no ENTRY/EXIT/Universe changes.",
            "_checks_rows": build_checks_csv_rows(by_day_rows),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        by_day = [{k: v for k, v in row.items() if not k.startswith("_")} for row in result.get("by_day") or []]
        _write_csv(paths["by_day"], by_day, BY_DAY_FIELDS)
        _write_csv(paths["checks"], list(result.get("_checks_rows") or []), CHECKS_FIELDS)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["report"].write_text(build_report(payload), encoding="utf-8")
        return paths
