"""
Phase400: Holding time audit on Phase399 Position-CAP backfill trades.

Quantifies hold duration for position_cap_accepted trades (CAP slot occupancy).
Research / report only — no Runtime changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase382_capital_constrained_backtest import _parse_ts, _write_csv

JST = ZoneInfo("Asia/Tokyo")

PERIOD_START = "20260529"
PERIOD_END = "20260615"
PHASE399_TRADES = "phase399_historical_position_cap_backfill_trades.csv"

EXIT_REASON_FIELDS = [
    "exit_reason_bucket",
    "trade_count",
    "avg_hold_sec",
    "median_hold_sec",
    "p90_hold_sec",
    "total_pnl_yen_100",
    "win_count",
    "loss_count",
    "win_rate",
]

SYMBOL_FIELDS = [
    "symbol",
    "trade_count",
    "total_cap_seconds",
    "avg_hold_sec",
    "median_hold_sec",
    "max_hold_sec",
    "total_pnl_yen_100",
    "win_rate",
    "rank_by_total_cap_seconds",
]

CAP_OCCUPATION_FIELDS = [
    "rank",
    "day",
    "session",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "pnl_yen_100",
    "exit_reason_bucket",
    "is_winner",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _bool(val: Any) -> bool:
    return str(val or "").strip().lower() in ("true", "1", "yes")


def _percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return round(s[f] + (s[c] - s[f]) * (k - f), 2)


def normalize_exit_reason(reason: str) -> str:
    r = str(reason or "").strip().lower()
    if "stop_hit" in r or r == "stop":
        return "stop_hit"
    if "trailing_mfe" in r:
        return "trailing_mfe"
    if "overlap" in r:
        return "overlap_replaced"
    if "session" in r or "afternoon" in r or "end_of" in r:
        return "session_close"
    return "other"


def hold_seconds(entry_time: str, exit_time: str) -> float:
    ent = _parse_ts(entry_time)
    ex = _parse_ts(exit_time)
    if ent is None or ex is None:
        return 0.0
    return max(0.0, (ex - ent).total_seconds())


def load_phase399_trades(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(dict(row))
    return rows


def enrich_trade(row: Mapping[str, Any]) -> dict[str, Any]:
    hold = hold_seconds(str(row.get("entry_time") or ""), str(row.get("exit_time") or ""))
    pnl = _float(row.get("pnl_yen_100"))
    bucket = normalize_exit_reason(str(row.get("exit_reason") or ""))
    is_winner = pnl is not None and pnl > 0
    is_loser = pnl is not None and pnl < 0
    return {
        **dict(row),
        "hold_sec": round(hold, 2),
        "exit_reason_bucket": bucket,
        "pnl_yen_100_float": pnl if pnl is not None else 0.0,
        "is_winner": is_winner,
        "is_loser": is_loser,
        "position_cap_accepted_bool": _bool(row.get("position_cap_accepted")),
    }


def _hold_stats(holds: Sequence[float]) -> dict[str, float]:
    if not holds:
        return {
            "avg_hold_sec": 0.0,
            "median_hold_sec": 0.0,
            "p90_hold_sec": 0.0,
            "p95_hold_sec": 0.0,
            "max_hold_sec": 0.0,
        }
    return {
        "avg_hold_sec": round(statistics.mean(holds), 2),
        "median_hold_sec": round(statistics.median(holds), 2),
        "p90_hold_sec": _percentile(holds, 90),
        "p95_hold_sec": _percentile(holds, 95),
        "max_hold_sec": round(max(holds), 2),
    }


def _aggregate_exit_reason(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        buckets.setdefault(str(t.get("exit_reason_bucket") or "other"), []).append(dict(t))
    rows: list[dict[str, Any]] = []
    for bucket in sorted(buckets):
        group = buckets[bucket]
        holds = [float(t.get("hold_sec") or 0) for t in group]
        pnls = [float(t.get("pnl_yen_100_float") or 0) for t in group]
        wins = sum(1 for t in group if t.get("is_winner"))
        losses = sum(1 for t in group if t.get("is_loser"))
        stats = _hold_stats(holds)
        rows.append(
            {
                "exit_reason_bucket": bucket,
                "trade_count": len(group),
                "avg_hold_sec": stats["avg_hold_sec"],
                "median_hold_sec": stats["median_hold_sec"],
                "p90_hold_sec": stats["p90_hold_sec"],
                "total_pnl_yen_100": round(sum(pnls), 2),
                "win_count": wins,
                "loss_count": losses,
                "win_rate": round(wins / len(group), 4) if group else 0.0,
            }
        )
    return rows


def _aggregate_symbols(trades: Sequence[Mapping[str, Any]], *, top_n: int = 20) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        sym = str(t.get("symbol") or "")
        by_sym.setdefault(sym, []).append(dict(t))
    rows: list[dict[str, Any]] = []
    for sym, group in by_sym.items():
        holds = [float(t.get("hold_sec") or 0) for t in group]
        pnls = [float(t.get("pnl_yen_100_float") or 0) for t in group]
        wins = sum(1 for t in group if t.get("is_winner"))
        rows.append(
            {
                "symbol": sym,
                "trade_count": len(group),
                "total_cap_seconds": round(sum(holds), 2),
                "avg_hold_sec": round(statistics.mean(holds), 2) if holds else 0.0,
                "median_hold_sec": round(statistics.median(holds), 2) if holds else 0.0,
                "max_hold_sec": round(max(holds), 2) if holds else 0.0,
                "total_pnl_yen_100": round(sum(pnls), 2),
                "win_rate": round(wins / len(group), 4) if group else 0.0,
            }
        )
    rows.sort(key=lambda r: (-float(r["total_cap_seconds"]), str(r["symbol"])))
    for i, row in enumerate(rows[:top_n], start=1):
        row["rank_by_total_cap_seconds"] = i
    return rows[:top_n]


def _cap_occupation_top(trades: Sequence[Mapping[str, Any]], *, top_n: int = 20) -> list[dict[str, Any]]:
    ranked = sorted(trades, key=lambda t: (-float(t.get("hold_sec") or 0), str(t.get("entry_time") or "")))
    rows: list[dict[str, Any]] = []
    for i, t in enumerate(ranked[:top_n], start=1):
        rows.append(
            {
                "rank": i,
                "day": t.get("day"),
                "session": t.get("session"),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "hold_sec": t.get("hold_sec"),
                "pnl_yen_100": t.get("pnl_yen_100_float"),
                "exit_reason_bucket": t.get("exit_reason_bucket"),
                "is_winner": t.get("is_winner"),
            }
        )
    return rows


def _win_loss_hold_stats(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    winners = [t for t in trades if t.get("is_winner")]
    losers = [t for t in trades if t.get("is_loser")]
    flat = [t for t in trades if not t.get("is_winner") and not t.get("is_loser")]
    return {
        "winners": {
            "count": len(winners),
            **_hold_stats([float(t.get("hold_sec") or 0) for t in winners]),
            "total_pnl_yen_100": round(sum(float(t.get("pnl_yen_100_float") or 0) for t in winners), 2),
        },
        "losers": {
            "count": len(losers),
            **_hold_stats([float(t.get("hold_sec") or 0) for t in losers]),
            "total_pnl_yen_100": round(sum(float(t.get("pnl_yen_100_float") or 0) for t in losers), 2),
        },
        "flat": {"count": len(flat)},
    }


def estimate_opportunity_cost(
    *,
    accepted: Sequence[Mapping[str, Any]],
    all_rows: Sequence[Mapping[str, Any]],
    median_hold: float,
    p90_hold: float,
) -> dict[str, Any]:
    rejects = [r for r in all_rows if str(r.get("position_cap_reject_reason") or "") == "reject_position_cap_backfill"]
    reject_count = len(rejects)
    accepted_pnls = [float(t.get("pnl_yen_100_float") or 0) for t in accepted]
    avg_pnl = round(statistics.mean(accepted_pnls), 2) if accepted_pnls else 0.0
    median_pnl = round(statistics.median(accepted_pnls), 2) if accepted_pnls else 0.0

    long_losers = [
        t
        for t in accepted
        if t.get("is_loser") and float(t.get("hold_sec") or 0) >= p90_hold
    ]
    long_loser_cap_sec = round(sum(float(t.get("hold_sec") or 0) for t in long_losers), 2)
    long_loser_pnl = round(sum(float(t.get("pnl_yen_100_float") or 0) for t in long_losers), 2)

    short_winners = [
        t
        for t in accepted
        if t.get("is_winner") and float(t.get("hold_sec") or 0) <= median_hold
    ]
    short_winner_pnl = round(sum(float(t.get("pnl_yen_100_float") or 0) for t in short_winners), 2)

    upper_bound_yen = round(reject_count * avg_pnl, 2)
    conservative_yen = round(reject_count * median_pnl, 2)

    total_cap_sec = round(sum(float(t.get("hold_sec") or 0) for t in accepted), 2)
    losing_cap_sec = round(
        sum(float(t.get("hold_sec") or 0) for t in accepted if t.get("is_loser")),
        2,
    )

    return {
        "position_cap_reject_count": reject_count,
        "avg_pnl_per_accepted_yen_100": avg_pnl,
        "median_pnl_per_accepted_yen_100": median_pnl,
        "upper_bound_opportunity_cost_yen_100": upper_bound_yen,
        "conservative_opportunity_cost_yen_100": conservative_yen,
        "long_hold_loser_count_p90_plus": len(long_losers),
        "long_hold_loser_cap_seconds": long_loser_cap_sec,
        "long_hold_loser_pnl_yen_100": long_loser_pnl,
        "short_hold_winner_pnl_yen_100": short_winner_pnl,
        "total_cap_seconds_accepted": total_cap_sec,
        "losing_cap_seconds_share": round(losing_cap_sec / total_cap_sec, 4) if total_cap_sec else 0.0,
        "pnl_per_cap_minute": round(
            sum(accepted_pnls) / (total_cap_sec / 60.0), 4
        )
        if total_cap_sec > 0
        else 0.0,
    }


def assess_cap_efficiency(
    *,
    hold_stats: Mapping[str, float],
    win_loss: Mapping[str, Any],
    opportunity: Mapping[str, Any],
    exit_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    winners = win_loss.get("winners") or {}
    losers = win_loss.get("losers") or {}
    avg_win_hold = float(winners.get("avg_hold_sec") or 0)
    avg_loss_hold = float(losers.get("avg_hold_sec") or 0)
    session_close = next((r for r in exit_rows if r.get("exit_reason_bucket") == "session_close"), {})
    long_hold_profitable = avg_win_hold > avg_loss_hold
    improvement_room = (
        float(opportunity.get("losing_cap_seconds_share") or 0) > 0.35
        or int(opportunity.get("position_cap_reject_count") or 0) > 50
    )
    time_exit_research = (
        float(session_close.get("avg_hold_sec") or 0) > float(hold_stats.get("median_hold_sec") or 0) * 1.5
        or int(opportunity.get("long_hold_loser_count_p90_plus") or 0) >= 5
    )
    return {
        "long_hold_profitable": long_hold_profitable,
        "avg_winner_hold_sec": avg_win_hold,
        "avg_loser_hold_sec": avg_loss_hold,
        "cap_efficiency_improvement_room": improvement_room,
        "time_based_exit_research_recommended": time_exit_research,
        "rationale": {
            "losing_cap_seconds_share": opportunity.get("losing_cap_seconds_share"),
            "position_cap_reject_count": opportunity.get("position_cap_reject_count"),
            "session_close_avg_hold_sec": session_close.get("avg_hold_sec"),
            "overall_median_hold_sec": hold_stats.get("median_hold_sec"),
        },
    }


def run_holding_time_audit(
    *,
    repo_root: Path,
    trades_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    period_start: str = PERIOD_START,
    period_end: str = PERIOD_END,
) -> dict[str, Any]:
    reports = output_dir or (repo_root / "results" / "reports")
    path = trades_path or (reports / PHASE399_TRADES)
    if not path.is_file():
        raise FileNotFoundError(f"Phase399 trades not found: {path}")

    raw = load_phase399_trades(path)
    enriched = [enrich_trade(r) for r in raw if period_start <= str(r.get("day") or "") <= period_end]
    accepted = [t for t in enriched if t.get("position_cap_accepted_bool")]
    holds = [float(t.get("hold_sec") or 0) for t in accepted]
    hold_stats = _hold_stats(holds)
    win_loss = _win_loss_hold_stats(accepted)
    exit_rows = _aggregate_exit_reason(accepted)
    symbol_rows = _aggregate_symbols(accepted, top_n=20)
    cap_top = _cap_occupation_top(accepted, top_n=20)
    opportunity = estimate_opportunity_cost(
        accepted=accepted,
        all_rows=enriched,
        median_hold=float(hold_stats.get("median_hold_sec") or 0),
        p90_hold=float(hold_stats.get("p90_hold_sec") or 0),
    )
    efficiency = assess_cap_efficiency(
        hold_stats=hold_stats,
        win_loss=win_loss,
        opportunity=opportunity,
        exit_rows=exit_rows,
    )

    mandatory_answers = {
        "1_position_cap_avg_hold_sec": hold_stats["avg_hold_sec"],
        "2_median_hold_sec": hold_stats["median_hold_sec"],
        "3_long_occupation_symbol_top20": [r["symbol"] for r in symbol_rows],
        "4_long_hold_profitable": efficiency["long_hold_profitable"],
        "5_cap_efficiency_improvement_room": efficiency["cap_efficiency_improvement_room"],
        "6_time_based_exit_research_needed": efficiency["time_based_exit_research_recommended"],
    }

    summary = {
        "phase": 400,
        "generated_at": _now_iso(),
        "source": str(path),
        "period_start": period_start,
        "period_end": period_end,
        "position_cap_accepted_trade_count": len(accepted),
        "structural_candidate_count": len(enriched),
        "hold_duration_sec": hold_stats,
        "win_loss_hold": win_loss,
        "opportunity_cost": opportunity,
        "cap_efficiency": efficiency,
        "mandatory_answers": mandatory_answers,
        "cap_occupation_top20": cap_top,
        "symbol_top20": symbol_rows,
    }

    reports.mkdir(parents=True, exist_ok=True)
    (reports / "phase400_holding_time_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_csv(reports / "phase400_holding_time_by_exit_reason.csv", exit_rows, EXIT_REASON_FIELDS)
    _write_csv(reports / "phase400_symbol_holding_time.csv", symbol_rows, SYMBOL_FIELDS)
    _write_csv(reports / "phase400_cap_occupation_top20.csv", cap_top, CAP_OCCUPATION_FIELDS)

    docs = repo_root / "docs" / "operations"
    docs.mkdir(parents=True, exist_ok=True)
    report_path = docs / "phase400_holding_time_report.md"
    report_path.write_text(
        _build_report(summary=summary, exit_rows=exit_rows, symbol_rows=symbol_rows, cap_top=cap_top),
        encoding="utf-8",
    )

    return {
        "summary": summary,
        "report_path": str(report_path),
        "exit_rows": exit_rows,
        "symbol_rows": symbol_rows,
        "cap_top": cap_top,
    }


def _build_report(
    *,
    summary: Mapping[str, Any],
    exit_rows: Sequence[Mapping[str, Any]],
    symbol_rows: Sequence[Mapping[str, Any]],
    cap_top: Sequence[Mapping[str, Any]],
) -> str:
    hs = summary.get("hold_duration_sec") or {}
    ans = summary.get("mandatory_answers") or {}
    opp = summary.get("opportunity_cost") or {}
    eff = summary.get("cap_efficiency") or {}
    wl = summary.get("win_loss_hold") or {}

    def _fmt_sec(sec: float) -> str:
        if sec >= 3600:
            return f"{sec:.0f}s ({sec/3600:.2f}h)"
        if sec >= 60:
            return f"{sec:.0f}s ({sec/60:.1f}min)"
        return f"{sec:.0f}s"

    lines = [
        "# Phase400 — Holding Time Audit (Position-CAP Mode)",
        "",
        f"Generated: {summary.get('generated_at')}",
        "",
        f"Period: `{summary.get('period_start')}` – `{summary.get('period_end')}`",
        f"Source: Phase399 `{PHASE399_TRADES}` (position_cap_accepted trades only)",
        f"Accepted trades analyzed: **{summary.get('position_cap_accepted_trade_count')}**",
        "",
        "## 必須回答",
        "",
        f"### 1. Position-CAP の平均保有時間",
        "",
        f"**{_fmt_sec(float(ans.get('1_position_cap_avg_hold_sec') or 0))}** (`avg_hold_sec={hs.get('avg_hold_sec')}`)",
        "",
        f"### 2. 中央値",
        "",
        f"**{_fmt_sec(float(ans.get('2_median_hold_sec') or 0))}** (`median_hold_sec={hs.get('median_hold_sec')}`)",
        "",
        f"補足: p90={_fmt_sec(float(hs.get('p90_hold_sec') or 0))}, p95={_fmt_sec(float(hs.get('p95_hold_sec') or 0))}, max={_fmt_sec(float(hs.get('max_hold_sec') or 0))}",
        "",
        "### 3. 長時間占有銘柄 Top20（total_cap_seconds 順）",
        "",
        "| rank | symbol | trades | total_cap_sec | avg_hold | total_pnl | win_rate |",
        "|------|--------|--------|---------------|----------|-----------|----------|",
    ]
    for row in symbol_rows:
        lines.append(
            f"| {row.get('rank_by_total_cap_seconds')} | {row.get('symbol')} | {row.get('trade_count')} | "
            f"{row.get('total_cap_seconds')} | {row.get('avg_hold_sec')} | ¥{row.get('total_pnl_yen_100')} | "
            f"{row.get('win_rate')} |"
        )

    lines.extend(
        [
            "",
            "### 4. 長時間保有は利益に繋がっているか",
            "",
            f"**{'はい（勝ちトレードの平均保有 > 負け）' if ans.get('4_long_hold_profitable') else 'いいえ（長時間保有は損失寄り）'}**",
            "",
            f"- 勝ち平均保有: {_fmt_sec(float((wl.get('winners') or {}).get('avg_hold_sec') or 0))}",
            f"- 負け平均保有: {_fmt_sec(float((wl.get('losers') or {}).get('avg_hold_sec') or 0))}",
            f"- 勝ち合計PnL: ¥{(wl.get('winners') or {}).get('total_pnl_yen_100')}",
            f"- 負け合計PnL: ¥{(wl.get('losers') or {}).get('total_pnl_yen_100')}",
            "",
            "### 5. CAP効率改善余地はあるか",
            "",
            f"**{'あり' if ans.get('5_cap_efficiency_improvement_room') else '限定的'}**",
            "",
            f"- position_cap reject 件数: {opp.get('position_cap_reject_count')}",
            f"- 損失トレードの CAP 秒数シェア: {float(opp.get('losing_cap_seconds_share') or 0)*100:.1f}%",
            f"- Opportunity Cost 上限推定: ¥{opp.get('upper_bound_opportunity_cost_yen_100')} "
            f"(保守: ¥{opp.get('conservative_opportunity_cost_yen_100')})",
            f"- pnl / cap-minute: ¥{opp.get('pnl_per_cap_minute')}",
            "",
            "### 6. 将来的に時間切れ EXIT 研究が必要か",
            "",
            f"**{'推奨' if ans.get('6_time_based_exit_research_needed') else '現時点では優先度低'}**",
            "",
            f"- session_close 平均保有: "
            f"{next((r.get('avg_hold_sec') for r in exit_rows if r.get('exit_reason_bucket')=='session_close'), 'n/a')}",
            f"- p90+ 長時間負けトレード数: {opp.get('long_hold_loser_count_p90_plus')}",
            "",
            "## EXIT 理由別",
            "",
            "| bucket | trades | avg_hold | median | p90 | total_pnl | win_rate |",
            "|--------|--------|----------|--------|-----|-----------|----------|",
        ]
    )
    for row in exit_rows:
        lines.append(
            f"| {row.get('exit_reason_bucket')} | {row.get('trade_count')} | {row.get('avg_hold_sec')} | "
            f"{row.get('median_hold_sec')} | {row.get('p90_hold_sec')} | ¥{row.get('total_pnl_yen_100')} | "
            f"{row.get('win_rate')} |"
        )

    lines.extend(
        [
            "",
            "## CAP 占有時間 Top20（単一トレード hold_sec 順）",
            "",
            "| rank | symbol | hold_sec | pnl | exit | winner |",
            "|------|--------|----------|-----|------|--------|",
        ]
    )
    for row in cap_top:
        lines.append(
            f"| {row.get('rank')} | {row.get('symbol')} | {row.get('hold_sec')} | "
            f"¥{row.get('pnl_yen_100')} | {row.get('exit_reason_bucket')} | {row.get('is_winner')} |"
        )

    lines.extend(
        [
            "",
            "## Opportunity Cost 推定",
            "",
            f"- reject 件数 × 平均 accepted PnL（上限）: ¥{opp.get('upper_bound_opportunity_cost_yen_100')}",
            f"- reject 件数 × 中央値 accepted PnL（保守）: ¥{opp.get('conservative_opportunity_cost_yen_100')}",
            f"- p90+ 長時間負けの CAP 秒数: {opp.get('long_hold_loser_cap_seconds')}",
            f"- 短時間勝ち（≤median）PnL: ¥{opp.get('short_hold_winner_pnl_yen_100')}",
            "",
            "## 成果物",
            "",
            "- `results/reports/phase400_holding_time_summary.json`",
            "- `results/reports/phase400_holding_time_by_exit_reason.csv`",
            "- `results/reports/phase400_symbol_holding_time.csv`",
            "- `results/reports/phase400_cap_occupation_top20.csv`",
            "",
        ]
    )
    return "\n".join(lines)
