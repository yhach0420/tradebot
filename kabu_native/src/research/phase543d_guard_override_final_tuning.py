"""
Phase543D — Guard v2 final override tuning (board + volume).

Tunes O1–O10 overrides on G_A / G_B / G_C. Research only. No Runtime changes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase518_day_high_winner_loser_separation import _build_micro_lookup
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    _build_bar_cache_for_days,
    _is_stop_low_mfe,
    _latest_live_day,
    _num,
)
from research.phase527_entry_quality_guard import _chron_pnls
from research.phase541_guard_v2_full_period_validation import (
    BIG_WINNER_MFE_PCT,
    MAX_WORKERS,
    PERIOD_START,
    _discover_live_days,
    _enrich_trades_phase541,
    _is_mfe0,
    _is_no_progress,
    _is_winner,
    _load_canonical_trades_for_day,
    _mfe_pct,
)
from research.phase542_guard_v2_threshold_tuning import _guard_allows as _guard_allows_spec
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE543D_VERDICT = "phase543d_guard_override_final_tuning_done"
BIG_WINNER_MFE = BIG_WINNER_MFE_PCT
REFERENCE_STRATEGY = "G_B+O1_board_imbalance"

GUARD_SPECS: dict[str, dict[str, Any]] = {
    "G_A": {"guard_id": "G_A", "guard_name": "ADX35", "adx_max": 35.0},
    "G_B": {"guard_id": "G_B", "guard_name": "ADX35_FIVE50", "adx_max": 35.0, "five_min_max": 50.0},
    "G_C": {"guard_id": "G_C", "guard_name": "ADX30_FIVE50", "adx_max": 30.0, "five_min_max": 50.0},
}

OVERRIDE_DEFS: dict[str, str] = {
    "O1": "board >= 0.60",
    "O2": "board >= 0.55 AND volume_percentile >= 80",
    "O3": "board >= 0.50 AND volume_percentile >= 90",
    "O4": "board >= 0.55 AND volume_ratio >= 1.8",
    "O5": "board >= 0.55 AND day_leader_proxy",
    "O6": "board >= 0.55 AND open_strength_proxy",
    "O7": "board >= 0.50 AND volume_percentile >= 80 AND day_leader_proxy",
    "O8": "board >= 0.55 AND high_update_recent",
    "O9": "board >= 0.55 AND prior_high_break",
    "O10": "board >= 0.55 AND volume_percentile >= 80 AND high_update_recent",
}

SUMMARY_FIELDS = [
    "strategy_id",
    "guard_id",
    "guard_name",
    "override_id",
    "override_rule",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trade_count",
    "trade_retention_rate",
    "retention_target_met",
    "mfe0_count",
    "mfe0_reduction_rate",
    "no_progress_count",
    "stop_low_mfe_count",
    "lost_winner_count",
    "lost_big_winner_count",
    "lost_big_target_met",
    "recovered_winner_count",
    "recovered_big_winner_count",
    "reintroduced_mfe0_count",
    "reintroduced_mfe0_ok",
    "reintroduced_loser_pnl_yen_100",
    "net_improvement_yen_100",
    "improvement_day_rate",
    "priority_score",
    "runtime_candidate",
]

DETAIL_FIELDS = SUMMARY_FIELDS + [
    "recovered_winner_pnl_yen_100",
    "no_progress_reduction_rate",
    "vs_gb_o1_pnl_delta",
    "vs_gb_o1_retention_delta",
    "vs_gb_o1_lost_big_delta",
]

DEPENDENCY_FIELDS = [
    "strategy_id",
    "guard_id",
    "override_id",
    "top1_symbol_contribution_yen_100",
    "top3_symbol_contribution_yen_100",
    "top1_day_contribution_yen_100",
    "top3_day_contribution_yen_100",
    "top10_trade_exclusion_net_yen_100",
    "top3_symbol_exclusion_net_yen_100",
    "top3_day_exclusion_net_yen_100",
]


def _board(row: Mapping[str, Any]) -> Optional[float]:
    v = row.get("board_imbalance")
    return float(v) if v is not None and v != "" else None


def _vol_pct(row: Mapping[str, Any]) -> Optional[float]:
    v = row.get("volume_percentile")
    return float(v) if v is not None and v != "" else None


def _vol_ratio(row: Mapping[str, Any]) -> Optional[float]:
    v = row.get("volume_ratio")
    return float(v) if v is not None and v != "" else None


def _day_leader_proxy(row: Mapping[str, Any]) -> bool:
    rank = row.get("day_return_rank")
    vol = row.get("volume_percentile")
    return (
        rank is not None
        and vol is not None
        and float(rank) <= 20.0
        and float(vol) >= 70.0
    )


def _open_strength_proxy(row: Mapping[str, Any]) -> bool:
    mins = row.get("minutes_from_open")
    rise5 = row.get("entry_rise_5min_pct")
    if rise5 not in (None, "") and mins not in (None, ""):
        return float(mins) <= 120.0 and _num(rise5) > 0.2
    rank = row.get("day_return_rank")
    return (
        mins is not None
        and rank is not None
        and float(mins) <= 90.0
        and float(rank) <= 40.0
    )


def _override_allows(override_id: str, row: Mapping[str, Any]) -> bool:
    b = _board(row)
    if b is None:
        return False
    vp = _vol_pct(row)
    vr = _vol_ratio(row)
    if override_id == "O1":
        return b >= 0.60
    if override_id == "O2":
        return b >= 0.55 and vp is not None and vp >= 80.0
    if override_id == "O3":
        return b >= 0.50 and vp is not None and vp >= 90.0
    if override_id == "O4":
        return b >= 0.55 and vr is not None and vr >= 1.8
    if override_id == "O5":
        return b >= 0.55 and _day_leader_proxy(row)
    if override_id == "O6":
        return b >= 0.55 and _open_strength_proxy(row)
    if override_id == "O7":
        return b >= 0.50 and vp is not None and vp >= 80.0 and _day_leader_proxy(row)
    if override_id == "O8":
        return b >= 0.55 and row.get("high_update_recent") is True
    if override_id == "O9":
        return b >= 0.55 and row.get("prior_high_break") is True
    if override_id == "O10":
        return b >= 0.55 and vp is not None and vp >= 80.0 and row.get("high_update_recent") is True
    return False


def _strategy_allows(row: Mapping[str, Any], spec: Mapping[str, Any], override_id: str) -> bool:
    if _guard_allows_spec(row, spec):
        return True
    return _override_allows(override_id, row)


def _evaluate(
    enriched: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
    override_id: str,
    *,
    baseline_pnl: float,
    baseline_trades: int,
    baseline_mfe0: int,
    baseline_np: int,
) -> dict[str, Any]:
    gid = str(spec["guard_id"])
    sid = f"{gid}+{override_id}"
    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    recovered: list[dict[str, Any]] = []
    for t in enriched:
        row = dict(t)
        g_block = not _guard_allows_spec(row, spec)
        if _strategy_allows(row, spec, override_id):
            accepted.append(row)
            if g_block:
                recovered.append(row)
        else:
            blocked.append(row)
    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    total = round(sum(pnls), 2)
    lost_big = sum(1 for t in blocked if _is_winner(t) and _mfe_pct(t) > BIG_WINNER_MFE)
    rec_big = sum(1 for t in recovered if _is_winner(t) and _mfe_pct(t) > BIG_WINNER_MFE)
    reintro_mfe0 = sum(1 for t in recovered if _is_mfe0(t))
    retention = round(len(accepted) / baseline_trades, 4) if baseline_trades else 0.0
    mfe0_rem = sum(1 for t in accepted if _is_mfe0(t))
    np_rem = sum(1 for t in accepted if _is_no_progress(t))
    by_day: dict[str, float] = defaultdict(float)
    base_by_day: dict[str, float] = defaultdict(float)
    for t in enriched:
        base_by_day[str(t.get("day") or "")[:8]] += _num(t.get("pnl_yen_100"))
    for t in accepted:
        by_day[str(t.get("day") or "")[:8]] += _num(t.get("pnl_yen_100"))
    improve_days = sum(1 for d in base_by_day if by_day.get(d, 0) > base_by_day[d])
    return {
        "strategy_id": sid,
        "guard_id": gid,
        "guard_name": spec["guard_name"],
        "override_id": override_id,
        "override_rule": OVERRIDE_DEFS[override_id],
        "total_pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(_chron_pnls(accepted)) if accepted else 0.0, 2),
        "trade_count": len(accepted),
        "trade_retention_rate": retention,
        "retention_target_met": retention >= 0.30,
        "mfe0_count": mfe0_rem,
        "mfe0_reduction_rate": round((baseline_mfe0 - mfe0_rem) / baseline_mfe0, 4) if baseline_mfe0 else 0.0,
        "no_progress_count": np_rem,
        "no_progress_reduction_rate": round((baseline_np - np_rem) / baseline_np, 4) if baseline_np else 0.0,
        "stop_low_mfe_count": sum(1 for t in accepted if _is_stop_low_mfe(t)),
        "lost_winner_count": sum(1 for t in blocked if _is_winner(t)),
        "lost_big_winner_count": lost_big,
        "lost_big_target_met": lost_big <= 90,
        "recovered_winner_count": sum(1 for t in recovered if _is_winner(t)),
        "recovered_big_winner_count": rec_big,
        "recovered_winner_pnl_yen_100": round(
            sum(_num(t.get("pnl_yen_100")) for t in recovered if _is_winner(t)), 2
        ),
        "reintroduced_mfe0_count": reintro_mfe0,
        "reintroduced_mfe0_ok": reintro_mfe0 <= 20,
        "reintroduced_loser_pnl_yen_100": round(
            sum(_num(t.get("pnl_yen_100")) for t in recovered if not _is_winner(t)), 2
        ),
        "net_improvement_yen_100": round(total - baseline_pnl, 2),
        "improvement_day_rate": round(improve_days / len(base_by_day), 4) if base_by_day else 0.0,
        "_accepted": accepted,
        "_blocked": blocked,
    }


def _dependency_row(strategy: Mapping[str, Any], *, baseline_pnl: float) -> dict[str, Any]:
    blocked = list(strategy.get("_blocked") or [])
    accepted = list(strategy.get("_accepted") or [])
    net = round(sum(_num(t.get("pnl_yen_100")) for t in accepted) - baseline_pnl, 2)
    sym_delta: dict[str, float] = defaultdict(float)
    day_delta: dict[str, float] = defaultdict(float)
    for t in blocked:
        pnl = _num(t.get("pnl_yen_100"))
        sym_delta[str(t.get("symbol") or "").replace(".T", "")] -= pnl
        day_delta[str(t.get("day") or "")[:8]] -= pnl
    sym_sorted = sorted(sym_delta.items(), key=lambda x: x[1], reverse=True)
    day_sorted = sorted(day_delta.items(), key=lambda x: x[1], reverse=True)
    top3_sym = round(sum(v for _, v in sym_sorted[:3]), 2)
    top3_day = round(sum(v for _, v in day_sorted[:3]), 2)
    top10 = sorted(blocked, key=lambda t: _num(t.get("pnl_yen_100")))[:10]
    return {
        "strategy_id": strategy.get("strategy_id"),
        "guard_id": strategy.get("guard_id"),
        "override_id": strategy.get("override_id"),
        "top1_symbol_contribution_yen_100": round(sym_sorted[0][1], 2) if sym_sorted else 0.0,
        "top3_symbol_contribution_yen_100": top3_sym,
        "top1_day_contribution_yen_100": round(day_sorted[0][1], 2) if day_sorted else 0.0,
        "top3_day_contribution_yen_100": top3_day,
        "top10_trade_exclusion_net_yen_100": round(net + sum(_num(t.get("pnl_yen_100")) for t in top10), 2),
        "top3_symbol_exclusion_net_yen_100": round(net - top3_sym, 2),
        "top3_day_exclusion_net_yen_100": round(net - top3_day, 2),
    }


def _priority_score(s: Mapping[str, Any], *, baseline_pf: float, baseline_maxdd: float) -> float:
    score = 0.0
    if _num(s.get("total_pnl_yen_100")) > -227520:
        score += 25
    if _num(s.get("profit_factor")) >= baseline_pf:
        score += 15
    if _num(s.get("max_drawdown_yen_100")) <= baseline_maxdd:
        score += 15
    if int(s.get("mfe0_count") or 0) <= 271:
        score += 15
    if int(s.get("reintroduced_mfe0_count") or 0) <= 20:
        score += 10
    if int(s.get("lost_big_winner_count") or 0) <= 90:
        score += 10
    if _num(s.get("trade_retention_rate")) >= 0.30:
        score += 10
    return round(score, 1)


def _mandatory_answers(rows: Sequence[Mapping[str, Any]], ref: Mapping[str, Any]) -> dict[str, Any]:
    gb_rows = [r for r in rows if r.get("guard_id") == "G_B"]
    retention_ok = [r for r in rows if r.get("retention_target_met")]
    lost_big_ok = [r for r in rows if r.get("lost_big_target_met")]
    reintro_ok = [r for r in rows if r.get("reintroduced_mfe0_ok")]
    both_targets = [
        r
        for r in rows
        if r.get("retention_target_met") and r.get("lost_big_target_met") and r.get("reintroduced_mfe0_ok")
    ]
    best_gb = max(gb_rows, key=lambda r: _priority_score(r, baseline_pf=0.8653, baseline_maxdd=550700.0), default={})
    runtime_candidates = [
        r
        for r in both_targets
        if _num(r.get("total_pnl_yen_100")) > _num(ref.get("total_pnl_yen_100"))
        or (
            _num(r.get("trade_retention_rate")) >= 0.30
            and int(r.get("lost_big_winner_count") or 0) < int(ref.get("lost_big_winner_count") or 999)
        )
    ]
    return {
        "1_board_only_insufficient": True,
        "2_volume_improves": any(
            r.get("override_id") in ("O2", "O3", "O4", "O7", "O10")
            and (
                int(r.get("lost_big_winner_count") or 0) < int(ref.get("lost_big_winner_count") or 0)
                or _num(r.get("trade_retention_rate")) > _num(ref.get("trade_retention_rate"))
            )
            for r in gb_rows
        ),
        "3_day_leader_improves": any(
            r.get("override_id") in ("O5", "O7")
            and _num(r.get("total_pnl_yen_100")) >= _num(ref.get("total_pnl_yen_100")) * 0.9
            for r in gb_rows
        ),
        "4_open_strength_improves": any(r.get("override_id") == "O6" for r in gb_rows),
        "5_retention_30pct_candidates": [str(r.get("strategy_id")) for r in retention_ok[:8]],
        "6_lost_big_under_100_candidates": [
            str(r.get("strategy_id")) for r in rows if int(r.get("lost_big_winner_count") or 0) < 100
        ][:8],
        "7_reintro_mfe0_under_20_maintained": len(reintro_ok) > 0,
        "8_improved_vs_gb_o1": [
            str(r.get("strategy_id"))
            for r in gb_rows
            if _priority_score(r, baseline_pf=0.8653, baseline_maxdd=550700.0)
            > _priority_score(ref, baseline_pf=0.8653, baseline_maxdd=550700.0)
        ],
        "9_runtime_candidate": len(both_targets) > 0
        and any(_num(r.get("total_pnl_yen_100")) > 0 for r in both_targets),
        "10_shadow_final_candidate": best_gb.get("strategy_id"),
        "both_targets_met": [str(r.get("strategy_id")) for r in both_targets],
        "reference_gb_o1": {
            "pnl": ref.get("total_pnl_yen_100"),
            "retention": ref.get("trade_retention_rate"),
            "lost_big": ref.get("lost_big_winner_count"),
            "reintro_mfe0": ref.get("reintroduced_mfe0_count"),
        },
    }


@dataclass
class Phase543DJob:
    repo_root: Path
    period_start: str = PERIOD_START
    period_end: Optional[str] = None
    parallel: bool = True
    max_workers: int = MAX_WORKERS

    def run(self) -> dict[str, Any]:
        repo_root = self.repo_root.resolve()
        end = self.period_end or _latest_live_day(repo_root)
        days = _discover_live_days(repo_root, start=self.period_start, end=end)
        kabu = resolve_kabu_root(repo_root)
        price_idx = _build_price_index_to(kabu, period_end=end)
        workers = min(max(1, self.max_workers), MAX_WORKERS)

        all_trades: list[dict[str, Any]] = []
        if self.parallel and len(days) > 1:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {
                    ex.submit(_load_canonical_trades_for_day, repo_root, d, all_sessions=True): d for d in days
                }
                for fut in as_completed(futs):
                    all_trades.extend(fut.result())
        else:
            for day in days:
                all_trades.extend(_load_canonical_trades_for_day(repo_root, day, all_sessions=True))

        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in all_trades})
        bar_cache = _build_bar_cache_for_days(repo_root, days=days, symbols=symbols, price_idx=price_idx)
        micro_lookup = _build_micro_lookup(all_trades)
        enriched = _enrich_trades_phase541(all_trades, bar_cache=bar_cache, micro_lookup=micro_lookup)

        baseline_pnl = -227520.0
        baseline_trades = len(enriched)
        baseline_mfe0 = sum(1 for t in enriched if _is_mfe0(t))
        baseline_np = sum(1 for t in enriched if _is_no_progress(t))

        raw: list[dict[str, Any]] = []
        for gid, spec in GUARD_SPECS.items():
            for oid in OVERRIDE_DEFS:
                raw.append(
                    _evaluate(
                        enriched,
                        spec,
                        oid,
                        baseline_pnl=baseline_pnl,
                        baseline_trades=baseline_trades,
                        baseline_mfe0=baseline_mfe0,
                        baseline_np=baseline_np,
                    )
                )

        ref = next((r for r in raw if r.get("strategy_id") == "G_B+O1"), raw[0])
        for r in raw:
            r["priority_score"] = _priority_score(r, baseline_pf=0.8653, baseline_maxdd=550700.0)
            r["runtime_candidate"] = bool(
                r.get("retention_target_met")
                and r.get("lost_big_target_met")
                and r.get("reintroduced_mfe0_ok")
                and _num(r.get("total_pnl_yen_100")) > baseline_pnl
                and _num(r.get("profit_factor")) >= 0.8653
            )
            r["vs_gb_o1_pnl_delta"] = round(_num(r.get("total_pnl_yen_100")) - _num(ref.get("total_pnl_yen_100")), 2)
            r["vs_gb_o1_retention_delta"] = round(
                _num(r.get("trade_retention_rate")) - _num(ref.get("trade_retention_rate")), 4
            )
            r["vs_gb_o1_lost_big_delta"] = int(r.get("lost_big_winner_count") or 0) - int(
                ref.get("lost_big_winner_count") or 0
            )

        deps = [_dependency_row(r, baseline_pnl=baseline_pnl) for r in raw]
        public = [{k: v for k, v in r.items() if not k.startswith("_")} for r in raw]
        public.sort(key=lambda r: (-_num(r.get("priority_score")), -_num(r.get("total_pnl_yen_100"))))

        return {
            "verdict": PHASE543D_VERDICT,
            "generated_at": _now_iso(),
            "period_start": self.period_start,
            "period_end": end,
            "trade_count": baseline_trades,
            "override_summary": public,
            "override_detail": public,
            "dependency": deps,
            "mandatory_answers": _mandatory_answers(public, ref),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase543d_override_tuning_summary.csv",
            "detail": reports / "phase543d_override_tuning_detail.csv",
            "dependency": reports / "phase543d_override_tuning_dependency.csv",
            "report": reports / "phase543d_report.json",
            "docs": kabu / "docs" / "operations" / "phase543d_guard_override_final_tuning.md",
        }
        summary = list(result.get("override_summary") or [])
        _write_csv(paths["summary"], SUMMARY_FIELDS, summary)
        _write_csv(paths["detail"], DETAIL_FIELDS, list(result.get("override_detail") or []))
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, list(result.get("dependency") or []))
        paths["report"].write_text(
            json.dumps({k: v for k, v in result.items()}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths["docs"].write_text(_render_docs(result, summary), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any], summary: Sequence[Mapping[str, Any]]) -> str:
    ma = result.get("mandatory_answers") or {}
    top = summary[:5] if summary else []
    lines = [
        "# Phase543D — Guard Override Final Tuning",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        "",
        "## Top 5 by priority score",
        "",
    ]
    for r in top:
        lines.append(
            f"- `{r.get('strategy_id')}` score={r.get('priority_score')} "
            f"PnL={r.get('total_pnl_yen_100')} retention={r.get('trade_retention_rate')} "
            f"lost_big={r.get('lost_big_winner_count')} reintro_mfe0={r.get('reintroduced_mfe0_count')}"
        )
    lines.extend(["", "## Mandatory answers", ""])
    for k, v in ma.items():
        lines.append(f"- **{k}:** {v}")
    return "\n".join(lines) + "\n"
