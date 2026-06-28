"""
Phase551A — Runtime daily attribution from live-window Current Runtime (B).

Builds day-level PnL / MFE0 / NoProgress / PBv2 / OR breakdown from full-path
guard evaluation (cross-day reentry state preserved). Also reads phase551 daily CSV
for cross-check.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    _build_bar_cache_for_days,
    _latest_live_day,
)
from research.phase533_or_profit_source_audit import _num
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _is_no_progress
from research.phase541_guard_v2_full_period_validation import _enrich_trades_phase541
from research.phase546_entry_cluster_shadow_replay import _merge_dataset, _trade_key
from research.phase547_reject_cluster_winner_rescue import _period_thresholds
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase551_current_runtime_full_period_replay import (
    E4_THRESHOLD,
    PERIOD_MIN,
    _evaluate_live_trades,
    _is_or_trade,
    _load_canonical_trades_for_day,
)
from research.phase518_day_high_winner_loser_separation import _build_micro_lookup
from research.phase451_entry_shape_tournament import _build_price_index_to
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE551A_VERDICT = "phase551a_runtime_daily_attribution_done"
VARIANT_ID = "B_current_runtime"
INITIAL_EQUITY_LEVELS = (1_000_000, 3_000_000, 5_000_000)

ATTRIBUTION_FIELDS = [
    "day",
    "pnl_rank",
    "daily_pnl_yen_100",
    "daily_pf",
    "daily_trades",
    "daily_mfe0",
    "daily_no_progress",
    "daily_pbv2_pnl",
    "daily_or_pnl",
    "daily_maxDD_yen_100",
    "cumulative_pnl_yen_100",
    "cumulative_equity_1M_yen",
]

RANKING_FIELDS = ["pnl_rank", "day", "daily_pnl_yen_100", "daily_trades", "daily_mfe0", "daily_no_progress"]

TOP5_FIELDS = ["rank_type", "rank", "day", "daily_pnl_yen_100", "daily_trades", "daily_mfe0", "daily_no_progress"]

EQUITY_CURVE_FIELDS = [
    "day",
    "daily_pnl_yen_100",
    "cumulative_pnl_yen_100",
    "equity_1M_yen",
    "equity_3M_yen",
    "equity_5M_yen",
]

CAUSE_FIELDS = [
    "loss_rank",
    "day",
    "daily_pnl_yen_100",
    "share_of_total_loss_pct",
    "cumulative_loss_yen_100",
    "daily_trades",
    "daily_mfe0",
    "daily_no_progress",
    "daily_pbv2_pnl",
    "daily_or_pnl",
]


def _iter_calendar_days(start: str, end: str) -> list[str]:
    d0 = datetime.strptime(start, "%Y%m%d")
    d1 = datetime.strptime(end, "%Y%m%d")
    out: list[str] = []
    cur = d0
    while cur <= d1:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def _day_metrics(accepted: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    pbv2 = [t for t in accepted if not _is_or_trade(t)]
    or_t = [t for t in accepted if _is_or_trade(t)]
    pbv2_pnls = [_num(t.get("pnl_yen_100")) for t in pbv2]
    or_pnls = [_num(t.get("pnl_yen_100")) for t in or_t]
    return {
        "daily_pnl_yen_100": round(sum(pnls), 2),
        "daily_pf": _pf(pnls),
        "daily_trades": len(pnls),
        "daily_mfe0": sum(1 for t in accepted if _is_mfe0(t)),
        "daily_no_progress": sum(1 for t in accepted if _is_no_progress(t)),
        "daily_pbv2_pnl": round(sum(pbv2_pnls), 2),
        "daily_or_pnl": round(sum(or_pnls), 2),
        "daily_maxDD_yen_100": round(_max_drawdown_yen(pnls), 2) if pnls else 0.0,
    }


def _load_csv_daily(reports: Path, variant_id: str) -> dict[str, dict[str, Any]]:
    path = reports / "phase551_runtime_daily.csv"
    if not path.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("variant_id") != variant_id:
                continue
            day = str(row.get("day") or "")[:8]
            out[day] = row
    return out


def _load_live_enriched(repo_root: Path, *, live_start: str, end: str) -> tuple[list[dict[str, Any]], Mapping]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(kabu)
    cluster_rows = _merge_dataset(reports)
    cluster_by_key = {_trade_key(r): dict(r) for r in cluster_rows}
    thresholds = _period_thresholds(cluster_rows)
    thresholds.setdefault("liquidity_burst_p75", E4_THRESHOLD)

    days = [d for d in _iter_calendar_days(live_start, end)]
    live_trades: list[dict[str, Any]] = []
    for day in days:
        for t in _load_canonical_trades_for_day(repo_root, day, all_sessions=True):
            key = _trade_key(t)
            merged = {**dict(t), **cluster_by_key.get(key, {})}
            merged["day"] = day
            if merged.get("liquidity_burst") in (None, "") and cluster_by_key.get(key):
                merged["liquidity_burst"] = cluster_by_key[key].get("liquidity_burst")
            live_trades.append(merged)

    symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in live_trades})
    price_idx = _build_price_index_to(kabu, period_end=end)
    bar_cache = _build_bar_cache_for_days(repo_root, days=days, symbols=symbols, price_idx=price_idx)
    micro = _build_micro_lookup(live_trades)
    enriched = _enrich_trades_phase541(live_trades, bar_cache=bar_cache, micro_lookup=micro)
    return enriched, thresholds


def _daily_from_accepted(accepted: Sequence[Mapping[str, Any]], *, calendar_days: Sequence[str]) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in accepted:
        by_day[str(t.get("day") or "")[:8]].append(dict(t))

    rows: list[dict[str, Any]] = []
    for day in calendar_days:
        acc = by_day.get(day) or []
        if not acc:
            rows.append(
                {
                    "day": day,
                    "daily_pnl_yen_100": 0.0,
                    "daily_pf": None,
                    "daily_trades": 0,
                    "daily_mfe0": 0,
                    "daily_no_progress": 0,
                    "daily_pbv2_pnl": 0.0,
                    "daily_or_pnl": 0.0,
                    "daily_maxDD_yen_100": 0.0,
                }
            )
        else:
            rows.append({"day": day, **_day_metrics(acc)})
    return rows


@dataclass
class Phase551AJob:
    repo_root: Path
    period_start: str = PERIOD_MIN
    period_end: str = "20260625"
    variant_id: str = VARIANT_ID

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        kabu = resolve_kabu_root(repo)
        reports = resolve_reports_dir(kabu)
        end = min(self.period_end, _latest_live_day(repo))
        live_start = max(self.period_start, PERIOD_MIN)
        calendar_days = _iter_calendar_days(live_start, end)

        enriched, thresholds = _load_live_enriched(repo, live_start=live_start, end=end)
        days = calendar_days
        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in enriched})
        price_idx = _build_price_index_to(kabu, period_end=end)
        bar_cache = _build_bar_cache_for_days(repo, days=days, symbols=symbols, price_idx=price_idx)
        live_ev = _evaluate_live_trades(
            enriched,
            include_or=True,
            reentry_rsi=True,
            entry_quality=True,
            cluster_guard=True,
            cluster_exception=True,
            bar_cache=bar_cache,
            thresholds=thresholds,
        )

        accepted = live_ev.get("_accepted") or []
        daily_rows = _daily_from_accepted(accepted, calendar_days=calendar_days)
        total_pnl = round(sum(r["daily_pnl_yen_100"] for r in daily_rows), 2)

        ranked = sorted(
            [r for r in daily_rows if r["daily_trades"] > 0],
            key=lambda r: _num(r["daily_pnl_yen_100"]),
            reverse=True,
        )
        good_days = [r for r in ranked if _num(r["daily_pnl_yen_100"]) > 0]
        bad_days = [r for r in reversed(ranked) if _num(r["daily_pnl_yen_100"]) < 0]
        for i, row in enumerate(ranked, start=1):
            row["pnl_rank"] = i

        rank_by_day = {r["day"]: r.get("pnl_rank") for r in ranked}
        for row in daily_rows:
            row["pnl_rank"] = rank_by_day.get(row["day"])

        cum = 0.0
        for row in daily_rows:
            cum += _num(row["daily_pnl_yen_100"])
            row["cumulative_pnl_yen_100"] = round(cum, 2)
            row["cumulative_equity_1M_yen"] = round(1_000_000 + cum, 2)

        top5_good = [
            {**r, "rank_type": "good", "rank": i}
            for i, r in enumerate(good_days[:5], start=1)
        ]
        top5_bad = [
            {**r, "rank_type": "bad", "rank": i}
            for i, r in enumerate(bad_days[:5], start=1)
        ]

        equity_curve: list[dict[str, Any]] = []
        cum_pnl = 0.0
        for row in daily_rows:
            cum_pnl += _num(row["daily_pnl_yen_100"])
            equity_curve.append(
                {
                    "day": row["day"],
                    "daily_pnl_yen_100": row["daily_pnl_yen_100"],
                    "cumulative_pnl_yen_100": round(cum_pnl, 2),
                    "equity_1M_yen": round(1_000_000 + cum_pnl, 2),
                    "equity_3M_yen": round(3_000_000 + cum_pnl, 2),
                    "equity_5M_yen": round(5_000_000 + cum_pnl, 2),
                }
            )

        loss_days = sorted(
            [r for r in daily_rows if _num(r["daily_pnl_yen_100"]) < 0],
            key=lambda r: _num(r["daily_pnl_yen_100"]),
        )
        total_loss = sum(_num(r["daily_pnl_yen_100"]) for r in loss_days) or -1.0
        cum_loss = 0.0
        cause_rows: list[dict[str, Any]] = []
        for i, row in enumerate(loss_days, start=1):
            pnl = _num(row["daily_pnl_yen_100"])
            cum_loss += pnl
            share = round(abs(pnl) / abs(total_loss) * 100.0, 2) if total_loss < 0 else 0.0
            cause_rows.append(
                {
                    "loss_rank": i,
                    "day": row["day"],
                    "daily_pnl_yen_100": pnl,
                    "share_of_total_loss_pct": share,
                    "cumulative_loss_yen_100": round(cum_loss, 2),
                    "daily_trades": row["daily_trades"],
                    "daily_mfe0": row["daily_mfe0"],
                    "daily_no_progress": row["daily_no_progress"],
                    "daily_pbv2_pnl": row["daily_pbv2_pnl"],
                    "daily_or_pnl": row["daily_or_pnl"],
                }
            )

        csv_daily = _load_csv_daily(reports, self.variant_id)

        return {
            "verdict": PHASE551A_VERDICT,
            "generated_at": _now_iso(),
            "variant_id": self.variant_id,
            "period_live": f"{live_start}-{end}",
            "total_pnl_yen_100": total_pnl,
            "live_eval_pnl_yen_100": live_ev.get("pnl_yen_100"),
            "daily_attribution": daily_rows,
            "pnl_ranking": [
                {
                    "pnl_rank": r.get("pnl_rank"),
                    "day": r["day"],
                    "daily_pnl_yen_100": r["daily_pnl_yen_100"],
                    "daily_trades": r["daily_trades"],
                    "daily_mfe0": r["daily_mfe0"],
                    "daily_no_progress": r["daily_no_progress"],
                }
                for r in sorted(ranked, key=lambda x: x.get("pnl_rank") or 999)
            ],
            "top5_good": top5_good,
            "top5_bad": top5_bad,
            "equity_curve": equity_curve,
            "loss_cause_ranking": cause_rows,
            "csv_crosscheck": {
                day: {
                    "csv_daily_pnl": _num((csv_daily.get(day) or {}).get("daily_pnl_yen_100")),
                    "eval_daily_pnl": next((r["daily_pnl_yen_100"] for r in daily_rows if r["day"] == day), 0),
                }
                for day in calendar_days
                if day in csv_daily or any(r["day"] == day and r["daily_trades"] for r in daily_rows)
            },
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        docs = kabu / "docs" / "operations" / "phase551a_runtime_daily_attribution.md"
        paths = {
            "attribution": reports / "phase551a_runtime_daily_attribution.csv",
            "ranking": reports / "phase551a_runtime_daily_ranking.csv",
            "top5": reports / "phase551a_runtime_daily_top5.csv",
            "equity_curve": reports / "phase551a_runtime_daily_equity_curve.csv",
            "cause": reports / "phase551a_runtime_daily_loss_cause_ranking.csv",
            "report": reports / "phase551a_report.json",
            "docs": docs,
        }

        daily = result.get("daily_attribution") or []
        _write_csv(paths["attribution"], ATTRIBUTION_FIELDS, daily)
        _write_csv(paths["ranking"], RANKING_FIELDS, result.get("pnl_ranking") or [])
        top5 = (result.get("top5_good") or []) + (result.get("top5_bad") or [])
        _write_csv(paths["top5"], TOP5_FIELDS, top5)
        _write_csv(paths["equity_curve"], EQUITY_CURVE_FIELDS, result.get("equity_curve") or [])
        _write_csv(paths["cause"], CAUSE_FIELDS, result.get("loss_cause_ranking") or [])
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        cause = result.get("loss_cause_ranking") or []
        good = result.get("top5_good") or []
        bad = result.get("top5_bad") or []
        lines = [
            "# Phase551A — Runtime Daily Attribution",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Variant:** {result.get('variant_id')}",
            f"**Period:** {result.get('period_live')}",
            f"**Total PnL (full-path eval):** {result.get('total_pnl_yen_100')} yen",
            "",
            "Note: `phase551_runtime_daily.csv` uses per-day guard replay (cross-day reentry state resets).",
            "Phase551A regroups accepted trades from full-path eval so daily PnL sums to the live total.",
            "",
            "## Daily PnL ranking (trading days only, best → worst)",
            "",
            "| Rank | Day | PnL | Trades | MFE0 | NoProgress |",
            "| ---: | --- | ---: | ---: | ---: | ---: |",
        ]
        for r in result.get("pnl_ranking") or []:
            lines.append(
                f"| {r.get('pnl_rank')} | {r.get('day')} | {r.get('daily_pnl_yen_100')} | "
                f"{r.get('daily_trades')} | {r.get('daily_mfe0')} | {r.get('daily_no_progress')} |"
            )
        lines.extend(["", "## Top 5 good days", ""])
        for r in good:
            lines.append(
                f"- #{r.get('rank')} **{r.get('day')}**: {r.get('daily_pnl_yen_100')} yen "
                f"({r.get('daily_trades')} trades, MFE0={r.get('daily_mfe0')}, NoProgress={r.get('daily_no_progress')})"
            )
        lines.extend(["", "## Top 5 bad days", ""])
        for r in bad:
            lines.append(
                f"- #{r.get('rank')} **{r.get('day')}**: {r.get('daily_pnl_yen_100')} yen "
                f"({r.get('daily_trades')} trades, MFE0={r.get('daily_mfe0')}, NoProgress={r.get('daily_no_progress')})"
            )
        lines.extend(["", "## -30,800 yen の主因営業日（損失寄与順）", ""])
        for r in cause:
            lines.append(
                f"{r.get('loss_rank')}. **{r.get('day')}** — {r.get('daily_pnl_yen_100')} yen "
                f"({r.get('share_of_total_loss_pct')}% of loss days, cumulative {r.get('cumulative_loss_yen_100')} yen)"
            )
        lines.extend(
            [
                "",
                "## Output files",
                "",
                "- `results/reports/phase551a_runtime_daily_attribution.csv`",
                "- `results/reports/phase551a_runtime_daily_ranking.csv`",
                "- `results/reports/phase551a_runtime_daily_top5.csv`",
                "- `results/reports/phase551a_runtime_daily_equity_curve.csv`",
                "- `results/reports/phase551a_runtime_daily_loss_cause_ranking.csv`",
                "- `results/reports/phase551a_report.json`",
            ]
        )
        paths["docs"].write_text("\n".join(lines) + "\n", encoding="utf-8")
        return paths
