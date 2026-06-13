"""
Phase370: K10 stop-chain A1 ENTRY guard shadow validation (post Phase355+364).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from research.phase366_stophit_reclassification import MIN_DAY
from small_paper.k10_stop_chain_a1_entry_guard_shadow import (
    SPLIT_VARIANTS,
    VARIANT_LABELS,
    _float,
    _variant_metrics,
    annotate_day_variants,
)

JST = ZoneInfo("Asia/Tokyo")
CONCENTRATION_MAX_SHARE = 0.5

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "session_kind",
    "universe_group",
    "universe_slot",
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "pnl_pct",
    "peak_mfe_pct",
    "exit_reason_canonical",
    "entry_momentum_score",
    "entry_imbalance_percentile",
    "board_dynamic_tier",
    "k10_guard_shadow_blocked",
    "k10_shadow_pnl_yen_100",
    "k10_shadow_delta_yen",
    "k10_guard_variant",
    "k10_matches_a1",
    "k10_prior_low_mfe_stop",
    "is_low_mfe_stop",
]


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _concentration(
    day_deltas: dict[str, float],
    symbol_skipped: dict[str, float],
    *,
    total_delta: float,
    total_skipped_pnl: float,
) -> dict[str, Any]:
    day_share = sym_share = None
    top_day = top_symbol = ""
    if abs(total_delta) > 1e-6 and day_deltas:
        top_day = max(day_deltas, key=lambda d: abs(day_deltas[d]))
        day_share = round(abs(day_deltas[top_day]) / abs(total_delta), 4)
    if abs(total_skipped_pnl) > 1e-6 and symbol_skipped:
        top_symbol = min(symbol_skipped, key=symbol_skipped.get)
        sym_share = round(abs(symbol_skipped[top_symbol]) / abs(total_skipped_pnl), 4)
    return {
        "top_day": top_day,
        "top_day_delta_share": day_share,
        "top_symbol": top_symbol,
        "top_symbol_skipped_pnl_share": sym_share,
        "not_single_day_dependent": day_share is None or day_share < CONCENTRATION_MAX_SHARE,
        "not_single_symbol_dependent": sym_share is None or sym_share < CONCENTRATION_MAX_SHARE,
    }


@dataclass
class Phase370K10StopChainA1GuardValidation:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase370_k10_stop_chain_a1_guard_summary.json",
            "by_variant": self.reports_dir / "phase370_k10_stop_chain_a1_guard_by_variant.csv",
            "by_day": self.reports_dir / "phase370_k10_stop_chain_a1_guard_by_day.csv",
            "by_symbol": self.reports_dir / "phase370_k10_stop_chain_a1_guard_by_symbol.csv",
            "trades": self.reports_dir / "phase370_k10_stop_chain_a1_guard_trades.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error") or int(result.get("trade_count_actual") or 0) <= 0:
            return
        self.session_results.append(dict(result))

    def _all_production_trades(self) -> list[dict[str, Any]]:
        trades: list[dict[str, Any]] = []
        for sr in self.session_results:
            for t in sr.get("production_trades") or []:
                row = dict(t)
                row["session_id"] = row.get("session_id") or sr.get("session_meta", {}).get(
                    "session_id"
                )
                row["day_key"] = row.get("day_key") or sr.get("session_meta", {}).get("day_key")
                trades.append(row)
        return trades

    def _aggregate(self) -> dict[str, Any]:
        all_trades = self._all_production_trades()
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in all_trades:
            by_day[str(t.get("day_key") or "")].append(t)

        variant_trades: dict[str, list[dict[str, Any]]] = {v: [] for v in SPLIT_VARIANTS}
        for _day, day_rows in by_day.items():
            annotated = annotate_day_variants(day_rows)
            for variant in SPLIT_VARIANTS:
                variant_trades[variant].extend(annotated[variant])

        by_variant: dict[str, dict[str, Any]] = {}
        by_day_variant: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {
                "actual_pnl": 0.0,
                "shadow_pnl": 0.0,
                "delta_yen": 0.0,
                "skipped_trade_count": 0,
                "skipped_trade_pnl_actual": 0.0,
                "stop_hit_reduction_count": 0,
            }
        )
        by_am_pm_variant: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {
                "delta_yen": 0.0,
                "dynamic40_delta_yen": 0.0,
                "core10_delta_yen": 0.0,
                "actual_pnl": 0.0,
                "shadow_pnl": 0.0,
            }
        )
        symbol_skipped: dict[tuple[str, str], float] = defaultdict(float)

        for variant in SPLIT_VARIANTS:
            trades = variant_trades[variant]
            metrics = _variant_metrics(trades, variant=variant)
            day_deltas: dict[str, float] = defaultdict(float)
            improved = worsened = 0

            for sr in self.session_results:
                sm = sr["session_meta"]
                day = str(sm.get("day_key") or sm.get("day") or "")
                sess_trades = [
                    t
                    for t in trades
                    if str(t.get("session_id") or "") == str(sm.get("session_id") or "")
                ]
                if not sess_trades:
                    continue
                sess_actual = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in sess_trades)
                sess_shadow = sum(
                    float(_float(t.get("k10_shadow_pnl_yen_100")) or 0.0) for t in sess_trades
                )
                delta = round(sess_shadow - sess_actual, 2)
                if delta > 0:
                    improved += 1
                elif delta < 0:
                    worsened += 1

                day_deltas[day] += delta
                kind = str(sr.get("session_kind") or "")
                by_am_pm_variant[(kind, variant)]["delta_yen"] += delta
                by_am_pm_variant[(kind, variant)]["actual_pnl"] += sess_actual
                by_am_pm_variant[(kind, variant)]["shadow_pnl"] += sess_shadow
                dyn_a = sum(
                    float(_float(t.get("pnl_yen_100")) or 0.0)
                    for t in sess_trades
                    if str(t.get("universe_group") or "") == "dynamic40"
                )
                dyn_s = sum(
                    float(_float(t.get("k10_shadow_pnl_yen_100")) or 0.0)
                    for t in sess_trades
                    if str(t.get("universe_group") or "") == "dynamic40"
                )
                core_a = sum(
                    float(_float(t.get("pnl_yen_100")) or 0.0)
                    for t in sess_trades
                    if str(t.get("universe_group") or "") == "core10"
                )
                core_s = sum(
                    float(_float(t.get("k10_shadow_pnl_yen_100")) or 0.0)
                    for t in sess_trades
                    if str(t.get("universe_group") or "") == "core10"
                )
                by_am_pm_variant[(kind, variant)]["dynamic40_delta_yen"] += round(dyn_s - dyn_a, 2)
                by_am_pm_variant[(kind, variant)]["core10_delta_yen"] += round(core_s - core_a, 2)

            for day, day_rows in by_day.items():
                day_variant_rows = [t for t in trades if str(t.get("day_key") or "") == day]
                if not day_variant_rows:
                    continue
                d_actual = sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in day_variant_rows)
                d_shadow = sum(
                    float(_float(t.get("k10_shadow_pnl_yen_100")) or 0.0) for t in day_variant_rows
                )
                d_delta = round(d_shadow - d_actual, 2)
                blocked_day = [t for t in day_variant_rows if t.get("k10_guard_shadow_blocked")]
                dk = (day, variant)
                by_day_variant[dk]["actual_pnl"] += d_actual
                by_day_variant[dk]["shadow_pnl"] += d_shadow
                by_day_variant[dk]["delta_yen"] += d_delta
                by_day_variant[dk]["skipped_trade_count"] += len(blocked_day)
                by_day_variant[dk]["skipped_trade_pnl_actual"] += sum(
                    float(_float(t.get("pnl_yen_100")) or 0.0) for t in blocked_day
                )
                by_day_variant[dk]["stop_hit_reduction_count"] += sum(
                    1 for t in blocked_day if t.get("exit_reason_canonical") == "stop_hit"
                )

            for t in trades:
                if t.get("k10_guard_shadow_blocked"):
                    symbol_skipped[(variant, str(t.get("symbol") or ""))] += float(
                        _float(t.get("pnl_yen_100")) or 0.0
                    )

            sym_map = {sym: pnl for (v, sym), pnl in symbol_skipped.items() if v == variant}
            conc = _concentration(
                dict(day_deltas),
                sym_map,
                total_delta=metrics["delta_yen"],
                total_skipped_pnl=metrics["skipped_trade_pnl_actual"],
            )
            by_variant[variant] = {
                **metrics,
                "improved_session_count": improved,
                "worsened_session_count": worsened,
                **conc,
            }

        best = max(SPLIT_VARIANTS, key=lambda v: by_variant[v]["delta_yen"])
        best_row = by_variant[best]
        pass_checks = {
            "total_pnl_improved": best_row["delta_yen"] > 0,
            "pf_improved": (best_row.get("shadow_pf") or 0) > (best_row.get("actual_pf") or 0),
            "skipped_pnl_negative": best_row["skipped_trade_pnl_actual"] < 0,
            "stop_hit_reduction": best_row["stop_hit_reduction_count"] > 0,
            "improved_ge_worsened": best_row["improved_session_count"]
            >= best_row["worsened_session_count"],
            "not_single_day_dependent": best_row.get("not_single_day_dependent", False),
            "not_single_symbol_dependent": best_row.get("not_single_symbol_dependent", False),
        }
        production_adopt = all(pass_checks.values())

        export_trades = variant_trades.get(best, [])
        return {
            "by_variant": by_variant,
            "best_variant": best,
            "by_day_variant": by_day_variant,
            "by_am_pm_variant": by_am_pm_variant,
            "symbol_skipped": symbol_skipped,
            "pass_checks_best": pass_checks,
            "production_adopt_candidate": production_adopt,
            "export_trades": export_trades,
            "all_trades_count": len(all_trades),
        }

    def finalize_outputs(
        self, *, wall_runtime_sec: float, sessions_discovered: int
    ) -> dict[str, Path]:
        agg = self._aggregate()
        paths = self.paths()
        best = agg["best_variant"]
        best_row = agg["by_variant"][best]

        variant_rows = [dict(agg["by_variant"][v]) for v in SPLIT_VARIANTS]
        by_day_rows = [
            {
                "day": day,
                "variant": variant,
                "actual_total_pnl_yen_100": round(vals["actual_pnl"], 2),
                "shadow_total_pnl_yen_100": round(vals["shadow_pnl"], 2),
                "delta_yen": round(vals["delta_yen"], 2),
                "skipped_trade_count": int(vals["skipped_trade_count"]),
                "skipped_trade_pnl_actual": round(vals["skipped_trade_pnl_actual"], 2),
                "stop_hit_reduction_count": int(vals["stop_hit_reduction_count"]),
            }
            for (day, variant), vals in sorted(agg["by_day_variant"].items())
        ]
        by_symbol_rows = [
            {
                "variant": variant,
                "symbol": sym,
                "skipped_trade_pnl_actual": round(pnl, 2),
            }
            for (variant, sym), pnl in sorted(agg["symbol_skipped"].items())
        ]

        phase368_contrast = {
            "phase368_scope": "Blocks ALL subsequent entries after prior low-MFE stop (any profile)",
            "phase370_scope": (
                "Blocks only Dynamic40 entries matching A1 (board_low/imb + weak momentum) "
                "after prior low-MFE stop"
            ),
            "phase368_best_variant_delta_yen": None,
            "phase368_all_variants_failed": True,
            "key_difference": (
                "Phase368 skipped winners because reentry block was too broad; "
                "K10 adds A1 entry-profile filter so only low-momentum board-low "
                "re-entries are shadow-blocked"
            ),
        }
        phase368_summary = self.reports_dir / "phase368_symbol_reentry_cluster_guard_summary.json"
        if phase368_summary.exists():
            p368 = json.loads(phase368_summary.read_text(encoding="utf-8"))
            conc368 = p368.get("conclusion") or {}
            phase368_contrast["phase368_best_variant_delta_yen"] = conc368.get("delta_yen")
            phase368_contrast["phase368_all_variants_failed"] = not conc368.get(
                "production_adopt_candidate"
            )

        summary = {
            "phase": 370,
            "title": "k10_stop_chain_a1_guard_shadow_validation",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "phase355_plus_phase364_production_kept_trades",
            "guard_rule": (
                "Shadow-block Dynamic40 ENTRY when same day/symbol had prior low-MFE stop_hit "
                f"(peak_mfe<{0.3}%) AND current entry matches A1 profile per variant"
            ),
            "date_range": {"min_day": MIN_DAY, "max_day": "latest_available"},
            "sessions_evaluated": len(self.session_results),
            "sessions_discovered": sessions_discovered,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "production_trade_count": agg["all_trades_count"],
            "variants": VARIANT_LABELS,
            "by_variant": agg["by_variant"],
            "by_am_pm": {
                f"{kind}/{variant}": vals
                for (kind, variant), vals in sorted(agg["by_am_pm_variant"].items())
            },
            "best_variant": best,
            "pass_checks_best": agg["pass_checks_best"],
            "phase368_contrast": phase368_contrast,
            "conclusion": {
                "best_variant": best,
                "best_label": VARIANT_LABELS.get(best, best),
                "delta_yen": best_row["delta_yen"],
                "actual_pf": best_row["actual_pf"],
                "shadow_pf": best_row["shadow_pf"],
                "delta_pf": best_row.get("delta_pf"),
                "skipped_trade_count": best_row["skipped_trade_count"],
                "skipped_trade_pnl_actual": best_row["skipped_trade_pnl_actual"],
                "stop_hit_reduction_count": best_row["stop_hit_reduction_count"],
                "low_mfe_stop_hit_reduction_count": best_row.get(
                    "low_mfe_stop_hit_reduction_count"
                ),
                "dynamic40_delta_yen": best_row["dynamic40_delta_yen"],
                "core10_delta_yen": best_row["core10_delta_yen"],
                "improved_session_count": best_row["improved_session_count"],
                "worsened_session_count": best_row["worsened_session_count"],
                "top_day_delta_share": best_row.get("top_day_delta_share"),
                "top_symbol_skipped_pnl_share": best_row.get("top_symbol_skipped_pnl_share"),
                "production_adopt_candidate": agg["production_adopt_candidate"],
                "recommendation": (
                    f"Adopt {best} K10 stop-chain A1 guard on production stack."
                    if agg["production_adopt_candidate"]
                    else "Reject K10 guard; shadow validation failed pass checks."
                ),
            },
        }

        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if variant_rows:
            _write_csv(
                paths["by_variant"],
                variant_rows,
                sorted({k for r in variant_rows for k in r}),
            )
        if by_day_rows:
            _write_csv(paths["by_day"], by_day_rows, sorted({k for r in by_day_rows for k in r}))
        if by_symbol_rows:
            _write_csv(
                paths["by_symbol"],
                by_symbol_rows,
                ["variant", "symbol", "skipped_trade_pnl_actual"],
            )
        export = agg["export_trades"]
        if export:
            _write_csv(paths["trades"], export, TRADE_FIELDS)
        return paths
