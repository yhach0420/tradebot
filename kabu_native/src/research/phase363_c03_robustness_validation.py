"""
Phase363: C03 near_day_high_low_mom guard robustness (6/12 dependency check).
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

from research.phase362_stack_validation import (
    enrich_stack_trade,
    load_session_stack_trades,
    stack_blocked,
)

JST = ZoneInfo("Asia/Tokyo")
MIN_DAY = "20260518"
EXCLUDE_DAY = "20260612"

C03_SCOPES = (
    ("c03_all_symbols", "B_phase355_plus_c03_all"),
    ("c03_dynamic40_only", "C_phase355_plus_c03_dynamic40"),
)


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


def _session_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    stack: str,
    session_kind: str,
) -> dict[str, Any]:
    yens: list[float] = []
    stops = 0
    for t in trades:
        yen = _float(t.get("pnl_yen_100"))
        if yen is None:
            continue
        if stack_blocked(stack, t, session_kind=session_kind):
            continue
        yens.append(float(yen))
        if t.get("is_stop_hit"):
            stops += 1
    return {
        "pnl_yen_100": round(sum(yens), 2),
        "profit_factor": _pf(yens),
        "trade_count": len(yens),
        "stop_hit_count": stops,
        "yens": yens,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@dataclass
class Phase363C03Robustness:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase363_c03_robustness_summary.json",
            "by_day": self.reports_dir / "phase363_c03_robustness_by_day.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error") or int(result.get("trade_count_actual") or 0) <= 0:
            return
        self.session_results.append(dict(result))

    def _analyze_scope(self, scope_key: str, stack: str) -> dict[str, Any]:
        by_day: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "baseline_pnl": 0.0,
                "c03_pnl": 0.0,
                "baseline_yens": [],
                "c03_yens": [],
                "baseline_stops": 0,
                "c03_stops": 0,
                "session_count": 0,
            }
        )

        for sr in self.session_results:
            day = str(sr["session_meta"].get("day_key") or sr["session_meta"].get("day") or "")
            session_kind = str(sr.get("session_kind") or "")
            trades = sr.get("trades") or []
            base = _session_metrics(trades, stack="A_phase355_only", session_kind=session_kind)
            c03 = _session_metrics(trades, stack=stack, session_kind=session_kind)
            d = by_day[day]
            d["baseline_pnl"] += base["pnl_yen_100"]
            d["c03_pnl"] += c03["pnl_yen_100"]
            d["baseline_yens"].extend(base["yens"])
            d["c03_yens"].extend(c03["yens"])
            d["baseline_stops"] += base["stop_hit_count"]
            d["c03_stops"] += c03["stop_hit_count"]
            d["session_count"] += 1

        day_rows: list[dict[str, Any]] = []
        for day in sorted(by_day.keys()):
            d = by_day[day]
            delta = round(d["c03_pnl"] - d["baseline_pnl"], 2)
            base_pf = _pf(d["baseline_yens"])
            c03_pf = _pf(d["c03_yens"])
            day_rows.append(
                {
                    "day": day,
                    "scope": scope_key,
                    "baseline_pnl_yen_100": round(d["baseline_pnl"], 2),
                    "c03_pnl_yen_100": round(d["c03_pnl"], 2),
                    "delta_yen": delta,
                    "baseline_pf": base_pf,
                    "c03_pf": c03_pf,
                    "delta_pf": (
                        round((c03_pf or 0) - (base_pf or 0), 4)
                        if c03_pf is not None and base_pf is not None
                        else None
                    ),
                    "baseline_stop_hit_count": d["baseline_stops"],
                    "c03_stop_hit_count": d["c03_stops"],
                    "stop_hit_reduction": d["baseline_stops"] - d["c03_stops"],
                    "session_count": d["session_count"],
                }
            )

        def _totals(days: Sequence[str]) -> dict[str, Any]:
            rows = [r for r in day_rows if r["day"] in days]
            base_yens: list[float] = []
            c03_yens: list[float] = []
            for day in days:
                base_yens.extend(by_day[day]["baseline_yens"])
                c03_yens.extend(by_day[day]["c03_yens"])
            base_pnl = round(sum(r["baseline_pnl_yen_100"] for r in rows), 2)
            c03_pnl = round(sum(r["c03_pnl_yen_100"] for r in rows), 2)
            delta = round(c03_pnl - base_pnl, 2)
            base_pf = _pf(base_yens)
            c03_pf = _pf(c03_yens)
            base_stops = sum(r["baseline_stop_hit_count"] for r in rows)
            c03_stops = sum(r["c03_stop_hit_count"] for r in rows)
            improved = sum(1 for r in rows if r["delta_yen"] > 0)
            worsened = sum(1 for r in rows if r["delta_yen"] < 0)
            flat = sum(1 for r in rows if r["delta_yen"] == 0)
            return {
                "day_count": len(rows),
                "baseline_pnl_yen_100": base_pnl,
                "c03_pnl_yen_100": c03_pnl,
                "delta_yen": delta,
                "baseline_pf": base_pf,
                "c03_pf": c03_pf,
                "delta_pf": (
                    round((c03_pf or 0) - (base_pf or 0), 4)
                    if c03_pf is not None and base_pf is not None
                    else None
                ),
                "stop_hit_reduction": base_stops - c03_stops,
                "improved_day_count": improved,
                "worsened_day_count": worsened,
                "flat_day_count": flat,
            }

        all_days = sorted(by_day.keys())
        full = _totals(all_days)
        excl_days = [d for d in all_days if d != EXCLUDE_DAY]
        excl = _totals(excl_days)

        top_improve = sorted(day_rows, key=lambda r: r["delta_yen"], reverse=True)[:5]
        top_worsen = sorted(day_rows, key=lambda r: r["delta_yen"])[:5]

        excl_share = (
            round(
                abs(next((r["delta_yen"] for r in day_rows if r["day"] == EXCLUDE_DAY), 0.0))
                / abs(full["delta_yen"]),
                4,
            )
            if full["delta_yen"] != 0
            else None
        )

        production_candidate = (
            excl["delta_yen"] > 0
            and (excl.get("delta_pf") or 0) > 0
            and excl["improved_day_count"] >= excl["worsened_day_count"]
        )

        return {
            "scope": scope_key,
            "stack": stack,
            "by_day": day_rows,
            "delta_yen_by_day": {r["day"]: r["delta_yen"] for r in day_rows},
            "delta_pf_by_day": {r["day"]: r["delta_pf"] for r in day_rows},
            "stop_hit_reduction_by_day": {r["day"]: r["stop_hit_reduction"] for r in day_rows},
            "full_period": full,
            "exclude_20260612": excl,
            "exclude_day_delta_yen": next(
                (r["delta_yen"] for r in day_rows if r["day"] == EXCLUDE_DAY), 0.0
            ),
            "exclude_day_delta_share_of_full": excl_share,
            "top5_improve_days": top_improve,
            "top5_worsen_days": top_worsen,
            "production_candidate": production_candidate,
            "verdict": (
                "production_candidate"
                if production_candidate
                else "shadow_continuation"
            ),
        }

    def build_summary(self) -> dict[str, Any]:
        scopes = {key: self._analyze_scope(key, stack) for key, stack in C03_SCOPES}
        primary = scopes["c03_all_symbols"]
        dyn = scopes["c03_dynamic40_only"]

        return {
            "phase": 363,
            "title": "c03_robustness_validation",
            "guard": "C03_near_day_high_low_mom",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "phase355_baseline_vs_c03_incremental",
            "date_range": {"min_day": MIN_DAY, "exclude_focus_day": EXCLUDE_DAY},
            "guard_conditions": {
                "day_high_distance_pct": "<=1.5",
                "entry_momentum_score": "<0.30",
            },
            "sessions_evaluated": len(self.session_results),
            "c03_all_symbols": scopes["c03_all_symbols"],
            "c03_dynamic40_only": scopes["c03_dynamic40_only"],
            "answers": {
                "q1_top5_improve_days": primary["top5_improve_days"],
                "q2_top5_worsen_days": primary["top5_worsen_days"],
                "q3_exclude_612_delta_yen": primary["exclude_20260612"]["delta_yen"],
                "q3_exclude_612_pf": {
                    "baseline_pf": primary["exclude_20260612"]["baseline_pf"],
                    "c03_pf": primary["exclude_20260612"]["c03_pf"],
                    "delta_pf": primary["exclude_20260612"]["delta_pf"],
                },
                "q4_exclude_612_delta_positive": primary["exclude_20260612"]["delta_yen"] > 0,
                "q5_improved_vs_worsened_days": {
                    "improved": primary["full_period"]["improved_day_count"],
                    "worsened": primary["full_period"]["worsened_day_count"],
                    "exclude_612_improved": primary["exclude_20260612"]["improved_day_count"],
                    "exclude_612_worsened": primary["exclude_20260612"]["worsened_day_count"],
                },
                "q6_dynamic40_comparison": {
                    "all_symbols_full_delta": primary["full_period"]["delta_yen"],
                    "dynamic40_full_delta": dyn["full_period"]["delta_yen"],
                    "all_symbols_exclude_612_delta": primary["exclude_20260612"]["delta_yen"],
                    "dynamic40_exclude_612_delta": dyn["exclude_20260612"]["delta_yen"],
                },
            },
            "verdict": {
                "c03_all_symbols": primary["verdict"],
                "c03_dynamic40_only": dyn["verdict"],
                "recommendation": (
                    "Production candidate: C03 improvement survives 6/12 exclusion."
                    if primary["production_candidate"]
                    else "Shadow continuation: C03 remains 6/12-dependent or weak ex-6/12."
                ),
                "612_dependency": (
                    primary["exclude_day_delta_share_of_full"] is not None
                    and primary["exclude_day_delta_share_of_full"] > 0.5
                ),
            },
        }

    def finalize_outputs(self, *, wall_runtime_sec: float, sessions_discovered: int) -> dict[str, Path]:
        paths = self.paths()
        summary = self.build_summary()
        paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        by_day_rows: list[dict[str, Any]] = []
        for scope_key, _stack in C03_SCOPES:
            scope = summary[scope_key] if scope_key in summary else summary["c03_all_symbols"]
            if scope_key in ("c03_all_symbols", "c03_dynamic40_only"):
                scope = summary[scope_key]
                by_day_rows.extend(scope["by_day"])

        _write_csv(paths["by_day"], by_day_rows, sorted({k for r in by_day_rows for k in r}))
        summary["sessions_discovered"] = sessions_discovered
        summary["wall_runtime_sec"] = round(wall_runtime_sec, 2)
        paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return paths
