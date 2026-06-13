"""
Phase371: High-MFE stop_hit EXIT recovery review (B/C bands forensic + shadow).
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

from research.phase366_stophit_reclassification import MIN_DAY
from small_paper.high_mfe_stophit_exit_recovery_shadow import (
    CANDIDATE_LABELS,
    EXIT_CANDIDATES,
    _float,
    _pf,
    is_high_mfe_stop,
)

JST = ZoneInfo("Asia/Tokyo")

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "session_kind",
    "universe_group",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "mfe_band",
    "mfe_band_label",
    "peak_mfe_pct",
    "mfe_left_pct",
    "pnl_pct",
    "pnl_yen_100",
    "entry_to_mfe_sec",
    "mfe_to_stop_sec",
    "board_dynamic_trailing_tier",
    "board_dynamic_trailing_activate_pct",
    "board_dynamic_trailing_giveback_frac",
    "trailing_mfe_activated",
    "trailing_activation_missed",
    "giveback_too_wide",
    "overlap_exit",
    "tick_count",
    "best_candidate",
    "best_candidate_delta_yen",
]


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@dataclass
class Phase371HighMfeStopHitExitRecovery:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase371_high_mfe_stophit_exit_recovery_summary.json",
            "by_candidate": self.reports_dir / "phase371_high_mfe_stophit_by_candidate.csv",
            "trades": self.reports_dir / "phase371_high_mfe_stophit_trades.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.session_results.append(dict(result))

    def _aggregate(self) -> dict[str, Any]:
        all_production: list[dict[str, Any]] = []
        high_mfe_stops: list[dict[str, Any]] = []

        for sr in self.session_results:
            for t in sr.get("production_trades") or []:
                row = dict(t)
                row["session_id"] = row.get("session_id") or sr.get("session_meta", {}).get(
                    "session_id"
                )
                all_production.append(row)
            high_mfe_stops.extend(sr.get("high_mfe_stops") or [])

        by_band: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in high_mfe_stops:
            by_band[str(t.get("mfe_band") or "")].append(t)

        candidate_metrics: dict[str, dict[str, Any]] = {}
        session_deltas: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

        for cid in EXIT_CANDIDATES:
            actual_yens: list[float] = []
            shadow_yens: list[float] = []
            bc_actual_yens: list[float] = []
            bc_shadow_yens: list[float] = []
            improved = worsened = 0
            stop_reduced = profit_take_miss = shadow_applied_count = 0
            dyn_delta = core_delta = 0.0
            am_delta = pm_delta = 0.0

            for sr in self.session_results:
                sm = sr["session_meta"]
                sid = str(sm.get("session_id") or "")
                sess_actual = 0.0
                sess_shadow = 0.0
                for t in sr.get("production_trades") or []:
                    actual = float(_float(t.get("pnl_yen_100")) or 0.0)
                    sim = (t.get("candidate_shadow") or {}).get(cid) or {}
                    sim_yen = _float(sim.get("shadow_pnl_yen_100"))
                    shadow = float(sim_yen if sim_yen is not None else actual)
                    actual_yens.append(actual)
                    shadow_yens.append(shadow)
                    sess_actual += actual
                    sess_shadow += shadow

                    if is_high_mfe_stop(t):
                        bc_actual_yens.append(actual)
                        bc_shadow_yens.append(shadow)

                    if sim.get("shadow_applied"):
                        shadow_applied_count += 1
                        if actual > 0 and shadow < actual:
                            profit_take_miss += 1
                    if (
                        t.get("exit_reason_canonical") == "stop_hit"
                        and sim.get("shadow_exit_reason") != "stop_hit"
                        and sim.get("shadow_applied")
                    ):
                        stop_reduced += 1

                    delta = shadow - actual
                    ug = str(t.get("universe_group") or "")
                    sk = str(t.get("session_kind") or "")
                    if ug == "dynamic40":
                        dyn_delta += delta
                    elif ug == "core10":
                        core_delta += delta
                    if sk == "am":
                        am_delta += delta
                    elif sk == "pm":
                        pm_delta += delta

                sess_delta = round(sess_shadow - sess_actual, 2)
                session_deltas[cid][sid] = sess_delta
                if sess_delta > 0:
                    improved += 1
                elif sess_delta < 0:
                    worsened += 1

            actual_total = round(sum(actual_yens), 2)
            shadow_total = round(sum(shadow_yens), 2)
            bc_actual = round(sum(bc_actual_yens), 2)
            bc_shadow = round(sum(bc_shadow_yens), 2)
            candidate_metrics[cid] = {
                "candidate_id": cid,
                "label": CANDIDATE_LABELS.get(cid, cid),
                "actual_total_pnl_yen_100": actual_total,
                "shadow_total_pnl_yen_100": shadow_total,
                "delta_yen": round(shadow_total - actual_total, 2),
                "bc_actual_total_pnl_yen_100": bc_actual,
                "bc_shadow_total_pnl_yen_100": bc_shadow,
                "bc_delta_yen": round(bc_shadow - bc_actual, 2),
                "actual_pf": _pf(actual_yens),
                "shadow_pf": _pf(shadow_yens),
                "delta_pf": (
                    round((_pf(shadow_yens) or 0) - (_pf(actual_yens) or 0), 4)
                    if _pf(shadow_yens) is not None and _pf(actual_yens) is not None
                    else None
                ),
                "stop_hit_reduction_count": stop_reduced,
                "profit_take_miss_count": profit_take_miss,
                "shadow_applied_trade_count": shadow_applied_count,
                "improved_session_count": improved,
                "worsened_session_count": worsened,
                "dynamic40_delta_yen": round(dyn_delta, 2),
                "core10_delta_yen": round(core_delta, 2),
                "am_delta_yen": round(am_delta, 2),
                "pm_delta_yen": round(pm_delta, 2),
            }

        best = max(EXIT_CANDIDATES, key=lambda c: candidate_metrics[c]["bc_delta_yen"])
        best_row = candidate_metrics[best]

        forensic_summary = _forensic_summary(high_mfe_stops, by_band)

        export_trades = []
        for t in high_mfe_stops:
            best_c = max(
                EXIT_CANDIDATES,
                key=lambda c: float(
                    ((t.get("candidate_shadow") or {}).get(c) or {}).get("shadow_delta_yen") or 0.0
                ),
            )
            best_sim = (t.get("candidate_shadow") or {}).get(best_c) or {}
            export_trades.append(
                {
                    **{k: t.get(k) for k in TRADE_FIELDS if k not in ("best_candidate", "best_candidate_delta_yen")},
                    "best_candidate": best_c,
                    "best_candidate_delta_yen": best_sim.get("shadow_delta_yen"),
                }
            )

        return {
            "all_production_count": len(all_production),
            "high_mfe_stop_count": len(high_mfe_stops),
            "by_band_counts": {b: len(rows) for b, rows in by_band.items()},
            "forensic_summary": forensic_summary,
            "candidate_metrics": candidate_metrics,
            "best_candidate": best,
            "export_trades": export_trades,
        }

    def finalize_outputs(
        self, *, wall_runtime_sec: float, sessions_discovered: int
    ) -> dict[str, Path]:
        agg = self._aggregate()
        paths = self.paths()
        best = agg["best_candidate"]
        best_row = agg["candidate_metrics"][best]

        candidate_rows = [agg["candidate_metrics"][c] for c in EXIT_CANDIDATES]

        summary = {
            "phase": 371,
            "title": "high_mfe_stophit_exit_recovery_review",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "phase355_plus_phase364_production_kept_trades",
            "focus_population": "stop_hit AND peak_mfe>=0.3% (MFE bands B+C)",
            "date_range": {"min_day": MIN_DAY, "max_day": "latest_available"},
            "sessions_evaluated": len(self.session_results),
            "sessions_discovered": sessions_discovered,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "production_trade_count": agg["all_production_count"],
            "high_mfe_stop_count": agg["high_mfe_stop_count"],
            "high_mfe_stop_loss_yen_100": agg["forensic_summary"].get("total_loss_yen_100"),
            "by_mfe_band": agg["forensic_summary"].get("by_band"),
            "candidates": CANDIDATE_LABELS,
            "by_candidate": agg["candidate_metrics"],
            "forensic_axes": agg["forensic_summary"],
            "best_candidate": best,
            "conclusion": {
                "best_candidate": best,
                "best_label": CANDIDATE_LABELS.get(best, best),
                "bc_delta_yen": best_row["bc_delta_yen"],
                "full_stack_delta_yen": best_row["delta_yen"],
                "delta_pf": best_row.get("delta_pf"),
                "stop_hit_reduction_count": best_row["stop_hit_reduction_count"],
                "profit_take_miss_count": best_row["profit_take_miss_count"],
                "improved_session_count": best_row["improved_session_count"],
                "worsened_session_count": best_row["worsened_session_count"],
                "dynamic40_delta_yen": best_row["dynamic40_delta_yen"],
                "core10_delta_yen": best_row["core10_delta_yen"],
                "am_delta_yen": best_row["am_delta_yen"],
                "pm_delta_yen": best_row["pm_delta_yen"],
                "production_adopt_candidate": (
                    best_row["bc_delta_yen"] > 0
                    and (best_row.get("shadow_pf") or 0) >= (best_row.get("actual_pf") or 0)
                    and best_row["profit_take_miss_count"] < best_row["stop_hit_reduction_count"]
                ),
                "recommendation": _recommendation(best_row),
            },
        }

        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if candidate_rows:
            _write_csv(
                paths["by_candidate"],
                candidate_rows,
                sorted({k for r in candidate_rows for k in r}),
            )
        if agg["export_trades"]:
            _write_csv(paths["trades"], agg["export_trades"], TRADE_FIELDS)
        return paths


def _forensic_summary(
    high_mfe_stops: Sequence[Mapping[str, Any]],
    by_band: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    total_loss = round(
        sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in high_mfe_stops), 2
    )

    def _axis(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
        acc: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "total_pnl_yen_100": 0.0}
        )
        for t in rows:
            val = str(t.get(key) or "unknown")
            acc[val]["count"] += 1
            acc[val]["total_pnl_yen_100"] += float(_float(t.get("pnl_yen_100")) or 0.0)
        return {
            k: {
                "count": v["count"],
                "total_pnl_yen_100": round(v["total_pnl_yen_100"], 2),
            }
            for k, v in sorted(acc.items())
        }

    by_band_summary = {}
    for band_id in ("B", "C"):
        rows = list(by_band.get(band_id, []))
        yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in rows]
        mfe_left = [float(_float(t.get("mfe_left_pct")) or 0.0) for t in rows if rows]
        by_band_summary[band_id] = {
            "count": len(rows),
            "total_pnl_yen_100": round(sum(yens), 2) if yens else 0.0,
            "avg_mfe_left_pct": round(sum(mfe_left) / len(mfe_left), 4) if mfe_left else None,
            "trailing_activation_missed_count": sum(
                1 for t in rows if t.get("trailing_activation_missed")
            ),
            "giveback_too_wide_count": sum(1 for t in rows if t.get("giveback_too_wide")),
            "avg_entry_to_mfe_sec": _avg_field(rows, "entry_to_mfe_sec"),
            "avg_mfe_to_stop_sec": _avg_field(rows, "mfe_to_stop_sec"),
        }

    return {
        "total_loss_yen_100": total_loss,
        "by_band": by_band_summary,
        "trailing_activation_missed_count": sum(
            1 for t in high_mfe_stops if t.get("trailing_activation_missed")
        ),
        "giveback_too_wide_count": sum(1 for t in high_mfe_stops if t.get("giveback_too_wide")),
        "overlap_exit_count": sum(1 for t in high_mfe_stops if t.get("overlap_exit")),
        "avg_entry_to_mfe_sec": _avg_field(high_mfe_stops, "entry_to_mfe_sec"),
        "avg_mfe_to_stop_sec": _avg_field(high_mfe_stops, "mfe_to_stop_sec"),
        "universe_group": _axis(high_mfe_stops, "universe_group"),
        "session_kind": _axis(high_mfe_stops, "session_kind"),
        "board_dynamic_trailing_tier": _axis(high_mfe_stops, "board_dynamic_trailing_tier"),
        "entry_time_bucket": _axis(high_mfe_stops, "entry_time_bucket"),
    }


def _avg_field(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
    vals = [float(_float(t.get(key))) for t in rows if _float(t.get(key)) is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _recommendation(best_row: Mapping[str, Any]) -> str:
    if best_row.get("bc_delta_yen", 0) > 0 and best_row.get("profit_take_miss_count", 0) == 0:
        return f"Shadow-validate {best_row.get('candidate_id')} EXIT policy on B/C stop hits."
    if best_row.get("bc_delta_yen", 0) > 0:
        return (
            f"{best_row.get('candidate_id')} improves B/C losses but cuts "
            f"{best_row.get('profit_take_miss_count')} winners; continue shadow only."
        )
    return "No EXIT candidate improves high-MFE stop_hit recovery; do not adopt."
