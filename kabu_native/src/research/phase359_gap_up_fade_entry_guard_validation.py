"""
Phase359: Gap-up fade ENTRY guard shadow validation (post-Phase355 population).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase357_actual_exit_audit import MAX_DAY, MIN_DAY
from small_paper.gap_up_fade_entry_guard_shadow import SPLIT_VARIANTS, variant_blocked

JST = ZoneInfo("Asia/Tokyo")
FOCUS_DAY_AM = "20260612"
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
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_vwap_dev_pct",
    "gap_up_fade_guard_shadow_blocked",
    "gap_up_fade_shadow_pnl_yen_100",
    "gap_up_fade_shadow_delta_yen",
    "pullback_guard_would_block",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _concentration_check(
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
    not_single_day = day_share is None or day_share < CONCENTRATION_MAX_SHARE
    not_single_symbol = sym_share is None or sym_share < CONCENTRATION_MAX_SHARE
    return {
        "top_day": top_day,
        "top_day_delta_share": day_share,
        "top_symbol": top_symbol,
        "top_symbol_skipped_pnl_share": sym_share,
        "not_single_day_dependent": not_single_day,
        "not_single_symbol_dependent": not_single_symbol,
    }


@dataclass
class Phase359GapUpFadeValidation:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase359_gap_up_fade_entry_guard_summary.json",
            "by_variant": self.reports_dir / "phase359_gap_up_fade_entry_guard_by_variant.csv",
            "by_day": self.reports_dir / "phase359_gap_up_fade_entry_guard_by_day.csv",
            "by_symbol": self.reports_dir / "phase359_gap_up_fade_entry_guard_by_symbol.csv",
            "trades": self.reports_dir / "phase359_gap_up_fade_entry_guard_trades.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error") or int(result.get("trade_count_actual") or 0) <= 0:
            return
        self.session_results.append(dict(result))

    def _aggregate(self) -> dict[str, Any]:
        by_variant: dict[str, dict[str, Any]] = {}
        by_day_variant: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {
                "actual_pnl": 0.0,
                "shadow_pnl": 0.0,
                "delta_yen": 0.0,
                "skipped_trade_count": 0,
                "skipped_trade_pnl_actual": 0.0,
                "stop_hit_reduction_count": 0,
                "session_count": 0,
            }
        )
        by_am_pm_variant: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {
                "delta_yen": 0.0,
                "dynamic40_delta_yen": 0.0,
                "core10_delta_yen": 0.0,
                "session_count": 0,
            }
        )
        symbol_skipped: dict[tuple[str, str], float] = defaultdict(float)
        all_trades: list[dict[str, Any]] = []

        for variant in SPLIT_VARIANTS:
            actual_total = shadow_total = skipped_pnl = dyn_actual = dyn_shadow = 0.0
            core_actual = core_shadow = 0.0
            skipped = stops_red = improved = worsened = trade_actual = trade_shadow = 0
            all_actual_yens: list[float] = []
            all_shadow_yens: list[float] = []
            day_deltas: dict[str, float] = defaultdict(float)

            for sr in self.session_results:
                sm = sr["session_meta"]
                v = sr["variants"][variant]
                actual_total += float(v["actual_total_pnl_yen_100"])
                shadow_total += float(v["shadow_total_pnl_yen_100"])
                skipped += int(v["skipped_trade_count"])
                skipped_pnl += float(v["skipped_trade_pnl_actual"])
                stops_red += int(v["stop_hit_reduction_count"])
                trade_actual += int(v["trade_count_actual"])
                trade_shadow += int(v["trade_count_shadow"])
                dyn_actual += float(v["dynamic40_actual_pnl_yen_100"])
                dyn_shadow += float(v["dynamic40_shadow_pnl_yen_100"])
                core_actual += float(v["core10_actual_pnl_yen_100"])
                core_shadow += float(v["core10_shadow_pnl_yen_100"])
                delta = float(v["delta_yen"])
                if delta > 0:
                    improved += 1
                elif delta < 0:
                    worsened += 1

                day = str(sm.get("day_key") or sm.get("day") or "")
                day_deltas[day] += delta
                day_key = (day, variant)
                by_day_variant[day_key]["actual_pnl"] += float(v["actual_total_pnl_yen_100"])
                by_day_variant[day_key]["shadow_pnl"] += float(v["shadow_total_pnl_yen_100"])
                by_day_variant[day_key]["delta_yen"] += delta
                by_day_variant[day_key]["skipped_trade_count"] += int(v["skipped_trade_count"])
                by_day_variant[day_key]["skipped_trade_pnl_actual"] += float(
                    v["skipped_trade_pnl_actual"]
                )
                by_day_variant[day_key]["stop_hit_reduction_count"] += int(
                    v["stop_hit_reduction_count"]
                )
                by_day_variant[day_key]["session_count"] += 1

                kind_key = (str(sr.get("session_kind") or ""), variant)
                by_am_pm_variant[kind_key]["delta_yen"] += delta
                by_am_pm_variant[kind_key]["dynamic40_delta_yen"] += float(v["dynamic40_delta_yen"])
                by_am_pm_variant[kind_key]["core10_delta_yen"] += float(v["core10_delta_yen"])
                by_am_pm_variant[kind_key]["session_count"] += 1

                for t in sr.get("trades") or []:
                    ay = t.get("pnl_yen_100")
                    if ay is None:
                        continue
                    all_actual_yens.append(float(ay))
                    blocked = variant_blocked(
                        variant, t, session_kind=str(sr.get("session_kind") or "")
                    )
                    all_shadow_yens.append(0.0 if blocked else float(ay))
                    if blocked:
                        symbol_skipped[(variant, str(t["symbol"]))] += float(ay)

            delta_total = round(shadow_total - actual_total, 2)
            sym_map = {
                sym: pnl for (v, sym), pnl in symbol_skipped.items() if v == variant
            }
            conc = _concentration_check(
                dict(day_deltas),
                sym_map,
                total_delta=delta_total,
                total_skipped_pnl=skipped_pnl,
            )
            by_variant[variant] = {
                "variant": variant,
                "actual_total_pnl_yen_100": round(actual_total, 2),
                "shadow_total_pnl_yen_100": round(shadow_total, 2),
                "delta_yen": delta_total,
                "actual_pf": _pf(all_actual_yens),
                "shadow_pf": _pf(all_shadow_yens),
                "skipped_trade_count": skipped,
                "skipped_trade_pnl_actual": round(skipped_pnl, 2),
                "stop_hit_reduction_count": stops_red,
                "improved_session_count": improved,
                "worsened_session_count": worsened,
                "trade_count_actual": trade_actual,
                "trade_count_shadow": trade_shadow,
                "dynamic40_actual_pnl_yen_100": round(dyn_actual, 2),
                "dynamic40_shadow_pnl_yen_100": round(dyn_shadow, 2),
                "dynamic40_delta_yen": round(dyn_shadow - dyn_actual, 2),
                "core10_actual_pnl_yen_100": round(core_actual, 2),
                "core10_shadow_pnl_yen_100": round(core_shadow, 2),
                "core10_delta_yen": round(core_shadow - core_actual, 2),
                **conc,
            }

        am_612 = {}
        for variant in SPLIT_VARIANTS:
            for sr in self.session_results:
                sm = sr["session_meta"]
                if sm.get("day_key") == FOCUS_DAY_AM or sm.get("day") == FOCUS_DAY_AM:
                    if sm.get("session_kind") == "am":
                        am_612[variant] = sr["variants"][variant]["delta_yen"]

        best = max(SPLIT_VARIANTS, key=lambda v: by_variant[v]["delta_yen"])
        best_row = by_variant[best]
        pass_checks = {
            "total_pnl_improved": best_row["delta_yen"] > 0,
            "pf_improved": (best_row.get("shadow_pf") or 0) > (best_row.get("actual_pf") or 0),
            "skipped_pnl_negative": best_row["skipped_trade_pnl_actual"] < 0,
            "improved_ge_worsened": best_row["improved_session_count"]
            >= best_row["worsened_session_count"],
            "stop_hit_reduction": best_row["stop_hit_reduction_count"] > 0,
            "not_single_day_dependent": best_row.get("not_single_day_dependent", False),
            "not_single_symbol_dependent": best_row.get("not_single_symbol_dependent", False),
        }
        production_adopt = all(
            [
                pass_checks["total_pnl_improved"],
                pass_checks["pf_improved"],
                pass_checks["skipped_pnl_negative"],
                pass_checks["improved_ge_worsened"],
                pass_checks["stop_hit_reduction"],
                pass_checks["not_single_day_dependent"],
                pass_checks["not_single_symbol_dependent"],
            ]
        )
        production_shadow = production_adopt or (
            pass_checks["total_pnl_improved"]
            and pass_checks["skipped_pnl_negative"]
            and pass_checks["stop_hit_reduction"]
        )

        for sr in self.session_results:
            for t in sr.get("trades") or []:
                all_trades.append(t)

        return {
            "by_variant": by_variant,
            "best_variant": best,
            "am_20260612_delta_by_variant": am_612,
            "by_day_variant": by_day_variant,
            "by_am_pm_variant": by_am_pm_variant,
            "symbol_skipped": symbol_skipped,
            "pass_checks_best": pass_checks,
            "production_adopt_candidate": production_adopt,
            "production_shadow_ready": production_shadow,
            "all_trades": all_trades,
        }

    def finalize_outputs(self, *, wall_runtime_sec: float, sessions_discovered: int) -> dict[str, Path]:
        agg = self._aggregate()
        paths = self.paths()

        best = agg["best_variant"]
        best_row = agg["by_variant"][best]
        variant_rows = []
        for v in SPLIT_VARIANTS:
            row = dict(agg["by_variant"][v])
            row["universe_scope"] = "all"
            variant_rows.append(row)

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
                "session_count": int(vals["session_count"]),
            }
            for (day, variant), vals in sorted(agg["by_day_variant"].items())
        ]
        by_symbol_rows = [
            {
                "variant": variant,
                "symbol": sym,
                "skipped_trade_pnl_actual": round(pnl, 2),
            }
            for (variant, sym), pnl in sorted(
                agg["symbol_skipped"].items(), key=lambda x: (x[0][0], x[1])
            )
        ]

        summary = {
            "phase": 359,
            "title": "gap_up_fade_entry_guard_validation",
            "guard": "C_gap_up_fade_guard",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "phase355_post_excluded",
            "date_range": {"min_day": MIN_DAY, "max_day": MAX_DAY},
            "sessions_evaluated": len(self.session_results),
            "sessions_discovered": sessions_discovered,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "variants": {
                "A_all_symbols": "all symbols",
                "B_dynamic40_only": "Dynamic40 only",
                "C_core10_only": "Core10 only",
                "D_am_only": "AM session only",
                "E_am_dynamic40_only": "AM session + Dynamic40 only",
            },
            "guard_conditions": {
                "rule_1": "entry_rise_5min_pct>=0.3 AND entry_vwap_dev_pct>0",
                "rule_2": "entry_rise_10min_pct>=0.5 AND entry_rise_5min_pct<0",
            },
            "by_variant": agg["by_variant"],
            "by_am_pm": {
                f"{kind}/{variant}": vals
                for (kind, variant), vals in sorted(agg["by_am_pm_variant"].items())
            },
            "best_variant": best,
            "best_variant_metrics": best_row,
            "am_20260612_delta_by_variant": agg["am_20260612_delta_by_variant"],
            "pass_checks_best": agg["pass_checks_best"],
            "production_adopt_candidate": agg["production_adopt_candidate"],
            "production_shadow_ready": agg["production_shadow_ready"],
            "phase355_combo": {
                "note": "Population already excludes Phase355 Dynamic40 pullback blocks; "
                "gap-up fade guard is additive (orthogonal to B_pullback_misread).",
                "combo_value": best_row["delta_yen"] > 0
                and best_row["skipped_trade_pnl_actual"] < 0,
                "best_variant_delta_yen": best_row["delta_yen"],
                "best_variant_skipped_pnl": best_row["skipped_trade_pnl_actual"],
            },
            "conclusion": {
                "best_variant": best,
                "delta_yen": best_row["delta_yen"],
                "shadow_pf": best_row["shadow_pf"],
                "actual_pf": best_row["actual_pf"],
                "skipped_trade_count": best_row["skipped_trade_count"],
                "stop_hit_reduction_count": best_row["stop_hit_reduction_count"],
                "dynamic40_delta_yen": best_row["dynamic40_delta_yen"],
                "core10_delta_yen": best_row["core10_delta_yen"],
                "am_delta_yen": agg["by_am_pm_variant"].get(("am", best), {}).get("delta_yen"),
                "pm_delta_yen": agg["by_am_pm_variant"].get(("pm", best), {}).get("delta_yen"),
                "am_20260612_delta_yen": agg["am_20260612_delta_by_variant"].get(best),
                "recommendation": (
                    "Production adopt candidate (shadow pilot first)."
                    if agg["production_adopt_candidate"]
                    else (
                        "Production shadow pilot only; adoption bar not fully met."
                        if agg["production_shadow_ready"]
                        else "Continue research; no variant meets adoption bar."
                    )
                ),
            },
        }

        paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_csv(paths["by_variant"], variant_rows, sorted({k for r in variant_rows for k in r}))
        if by_day_rows:
            _write_csv(paths["by_day"], by_day_rows, sorted({k for r in by_day_rows for k in r}))
        if by_symbol_rows:
            _write_csv(
                paths["by_symbol"],
                by_symbol_rows,
                ["variant", "symbol", "skipped_trade_pnl_actual"],
            )
        _write_csv(paths["trades"], agg["all_trades"], TRADE_FIELDS)
        return paths
