"""
Phase343: board_failure_exit MFE filter + confirm_ticks tuning evaluation.
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

from research.phase337_exit_candidate_evaluation import (
    _float,
    _is_concentrated,
    _memory_mb,
    _top_share,
)
from research.phase340_vwap_dev_finetune_evaluation import (
    PROFIT_MISS_SMALL_THRESHOLD,
    _VariantAccum,
    adoption_assessment,
    tradeoff_score,
)
from small_paper.board_failure_exit_tuning import (
    BOARD_FAILURE_EXIT_ID,
    BoardFailureTuningVariant,
    default_phase343_variants,
)

JST = ZoneInfo("Asia/Tokyo")

MFE_COHORTS: tuple[str, ...] = ("all", "mfe_lt_0p2", "mfe_lt_0p3", "mfe_lt_0p4", "mfe_lt_0p5")

MFE_COHORT_CEILINGS: dict[str, float] = {
    "mfe_lt_0p2": 0.2,
    "mfe_lt_0p3": 0.3,
    "mfe_lt_0p4": 0.4,
    "mfe_lt_0p5": 0.5,
}


def trade_in_mfe_cohort(row: Mapping[str, Any], cohort: str) -> bool:
    if cohort == "all":
        return True
    ceiling = MFE_COHORT_CEILINGS.get(cohort)
    if ceiling is None:
        return False
    return float(row.get("peak_mfe_pct") or 0.0) < ceiling

PHASE342_BASELINE: dict[str, Any] = {
    "label": "phase342_no_mfe_filter",
    "delta_yen": 21280.0,
    "profit_take_miss_yen_100": -9520.0,
    "shadow_pf": 0.0633,
    "actual_pf": 0.2135,
    "stop_hit_reduction_count": 4,
    "trigger_count": 11,
}

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "symbol",
    "position_id",
    "variant_id",
    "max_mfe_pct",
    "confirm_ticks",
    "peak_mfe_pct",
    "mfe_bucket",
    "shadow_pnl_yen_100",
    "actual_exit_reason",
    "actual_pnl_yen_100",
    "candidate_vs_actual_delta_yen",
    "no_candidate_trigger",
]


def _load_phase342_baseline(reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / "phase342_board_failure_exit_summary.json"
    if not path.is_file():
        return dict(PHASE342_BASELINE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        all_m = (data.get("mfe_cohort_metrics") or {}).get("all") or {}
        return {
            "label": "phase342_no_mfe_filter",
            "delta_yen": float(all_m.get("delta_yen") or PHASE342_BASELINE["delta_yen"]),
            "profit_take_miss_yen_100": float(
                all_m.get("profit_take_miss_yen_100")
                or PHASE342_BASELINE["profit_take_miss_yen_100"]
            ),
            "shadow_pf": all_m.get("shadow_pf") or PHASE342_BASELINE["shadow_pf"],
            "actual_pf": all_m.get("actual_pf") or PHASE342_BASELINE["actual_pf"],
            "stop_hit_reduction_count": int(
                all_m.get("stop_hit_reduction_count")
                or PHASE342_BASELINE["stop_hit_reduction_count"]
            ),
            "trigger_count": int(all_m.get("trigger_count") or PHASE342_BASELINE["trigger_count"]),
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return dict(PHASE342_BASELINE)


def phase343_adoption_assessment(
    metrics: Mapping[str, Any],
    *,
    actual_total: float,
    actual_pf: Optional[float],
    phase342_baseline: Mapping[str, Any],
) -> dict[str, Any]:
    base = adoption_assessment(metrics, actual_total=actual_total, actual_pf=actual_pf)
    checks = dict(base.get("checks") or {})
    pf = metrics.get("profit_factor")
    profit_miss = float(metrics.get("profit_take_miss_yen_100") or 0)
    phase342_miss = float(phase342_baseline.get("profit_take_miss_yen_100") or 0)

    checks["pf_not_worse_than_actual"] = (
        pf is not None
        and actual_pf is not None
        and pf != float("inf")
        and actual_pf != float("inf")
        and float(pf) >= float(actual_pf)
    )
    checks["profit_take_miss_vs_phase342"] = profit_miss > phase342_miss
    checks["profit_take_miss_improved"] = profit_miss > phase342_miss

    adopt_ready = all(
        [
            checks.get("total_pnl_improved"),
            checks.get("pf_not_worse_than_actual"),
            checks.get("stop_hit_reduction"),
            checks.get("not_symbol_concentrated"),
            checks.get("profit_take_miss_vs_phase342"),
        ]
    )
    return {"adopt_ready": adopt_ready, "checks": checks}


@dataclass
class Phase343BoardFailureMfeAggregator:
    reports_dir: Path
    variants: tuple[BoardFailureTuningVariant, ...] = field(default_factory=default_phase343_variants)
    phase342_baseline: dict[str, Any] = field(default_factory=dict)
    sessions_evaluated: int = 0
    sessions_failed: int = 0
    positions_evaluated: int = 0
    actual_total_pnl_yen_100: float = 0.0
    actual_gross_profit_yen_100: float = 0.0
    actual_gross_loss_yen_100: float = 0.0
    actual_stop_hit_count: int = 0
    variant_acc: dict[str, _VariantAccum] = field(default_factory=dict)
    variant_meta: dict[str, BoardFailureTuningVariant] = field(default_factory=dict)
    cohort_acc: dict[str, dict[str, _VariantAccum]] = field(default_factory=dict)
    failed_sessions: list[dict[str, Any]] = field(default_factory=list)
    peak_memory_mb: float = 0.0

    def __post_init__(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        if not self.phase342_baseline:
            self.phase342_baseline = _load_phase342_baseline(self.reports_dir)
        for v in self.variants:
            self.variant_acc.setdefault(v.variant_id, _VariantAccum())
            self.variant_meta[v.variant_id] = v
            self.cohort_acc.setdefault(v.variant_id, {})
            for cohort in MFE_COHORTS:
                self.cohort_acc[v.variant_id].setdefault(cohort, _VariantAccum())

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase343_board_failure_mfe_tuning_summary.json",
            "sessions": self.reports_dir / "phase343_board_failure_mfe_tuning_sessions.csv",
            "trades": self.reports_dir / "phase343_board_failure_mfe_tuning_trades.csv",
            "by_variant": self.reports_dir / "phase343_board_failure_mfe_tuning_by_variant.csv",
            "by_mfe_cohort": self.reports_dir
            / "phase343_board_failure_mfe_tuning_by_mfe_cohort.csv",
            "tradeoff": self.reports_dir / "phase343_board_failure_mfe_tuning_tradeoff.csv",
        }

    def note_memory(self) -> None:
        self.peak_memory_mb = max(self.peak_memory_mb, _memory_mb())

    def _append_csv(
        self,
        path: Path,
        fieldnames: Sequence[str],
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        if not rows:
            return
        write_header = not path.is_file()
        with path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
            if write_header:
                w.writeheader()
            for row in rows:
                w.writerow(row)

    def actual_pf(self) -> Optional[float]:
        if self.actual_gross_loss_yen_100 <= 0:
            return None if self.actual_gross_profit_yen_100 <= 0 else float("inf")
        return round(self.actual_gross_profit_yen_100 / self.actual_gross_loss_yen_100, 4)

    def ingest_session(
        self,
        *,
        session_meta: Mapping[str, Any],
        trade_rows: Sequence[Mapping[str, Any]],
        push_rows: int,
        runtime_sec: float,
        error: str = "",
    ) -> None:
        self.note_memory()
        if error:
            self.sessions_failed += 1
            self.failed_sessions.append({**dict(session_meta), "error": error})
            return

        sid = str(session_meta.get("session_id") or "")
        day_key = str(session_meta.get("day_key") or "")
        seen_positions: dict[str, float] = {}
        for row in trade_rows:
            pid = str(row.get("position_id") or "")
            if not pid or pid in seen_positions:
                continue
            actual = float(_float(row.get("actual_pnl_yen_100")) or 0.0)
            seen_positions[pid] = actual
            self.positions_evaluated += 1
            self.actual_total_pnl_yen_100 += actual
            if actual > 0:
                self.actual_gross_profit_yen_100 += actual
            elif actual < 0:
                self.actual_gross_loss_yen_100 += abs(actual)
            if str(row.get("actual_exit_reason") or "") == "stop_hit":
                self.actual_stop_hit_count += 1

        enriched: list[dict[str, Any]] = []
        for row in trade_rows:
            vid = str(row.get("variant_id") or "")
            if vid not in self.variant_acc:
                continue
            enriched.append({**dict(row), "session_id": sid, "day_key": day_key})
            self.variant_acc[vid].ingest_trade(row)
            for cohort in MFE_COHORTS:
                if trade_in_mfe_cohort(row, cohort):
                    self.cohort_acc[vid][cohort].ingest_trade(row)

        session_actual = round(sum(seen_positions.values()), 2)
        session_shadow_totals = {
            vid: round(
                sum(
                    float(_float(r.get("shadow_pnl_yen_100")) or 0.0)
                    for r in enriched
                    if str(r.get("variant_id") or "") == vid
                ),
                2,
            )
            for vid in self.variant_acc
        }
        for vid in self.variant_acc:
            self.variant_acc[vid].ingest_session_delta(
                round(session_shadow_totals.get(vid, 0.0) - session_actual, 2)
            )

        session_row: dict[str, Any] = {
            **dict(session_meta),
            "positions": len(seen_positions),
            "push_rows": push_rows,
            "runtime_sec": round(runtime_sec, 1),
            "actual_total_pnl_yen_100": session_actual,
        }
        for vid in self.variant_acc:
            sh = session_shadow_totals.get(vid, 0.0)
            session_row[f"{vid}_shadow_pnl_yen_100"] = sh
            session_row[f"{vid}_session_delta_yen"] = round(sh - session_actual, 2)
        self._append_csv(self.paths()["sessions"], sorted(session_row.keys()), [session_row])
        self._append_csv(self.paths()["trades"], TRADE_FIELDS, enriched)
        self.sessions_evaluated += 1

    def _variant_metrics(self, vid: str) -> dict[str, Any]:
        acc = self.variant_acc[vid]
        meta = self.variant_meta[vid]
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        delta = round(acc.shadow_total_pnl_yen_100 - actual_total, 2)
        profit_miss = round(acc.profit_take_miss_yen_100, 2)
        sym_abs = {s: abs(v) for s, v in acc.symbol_delta_yen.items() if s}
        concentrated = _is_concentrated(sym_abs, delta)
        cohort_out: dict[str, Any] = {}
        for cohort in MFE_COHORTS:
            cacc = self.cohort_acc[vid][cohort]
            pos_n = cacc.trigger_count + cacc.no_trigger_count
            if pos_n <= 0:
                continue
            cohort_out[cohort] = {
                "positions": pos_n,
                "shadow_total_pnl_yen_100": round(cacc.shadow_total_pnl_yen_100, 2),
                "shadow_pf": cacc.pf(),
                "trigger_count": cacc.trigger_count,
                "no_trigger_count": cacc.no_trigger_count,
                "stop_hit_reduction_count": cacc.stop_hit_reduction_count,
                "profit_take_miss_yen_100": round(cacc.profit_take_miss_yen_100, 2),
                "improved_trade_count": cacc.improved_trade_count,
                "worsened_trade_count": cacc.worsened_trade_count,
            }
        return {
            **meta.to_dict(),
            "candidate_id": BOARD_FAILURE_EXIT_ID,
            "actual_total_pnl_yen_100": actual_total,
            "shadow_total_pnl_yen_100": round(acc.shadow_total_pnl_yen_100, 2),
            "delta_yen": delta,
            "profit_factor": acc.pf(),
            "trigger_count": acc.trigger_count,
            "no_trigger_count": acc.no_trigger_count,
            "improved_trade_count": acc.improved_trade_count,
            "worsened_trade_count": acc.worsened_trade_count,
            "stop_hit_reduction_count": acc.stop_hit_reduction_count,
            "profit_take_miss_yen_100": profit_miss,
            "stop_hit_avoidance_rate": acc.stop_hit_avoidance_rate(),
            "tradeoff_score": tradeoff_score(profit_miss, acc.stop_hit_reduction_count),
            "improved_session_count": acc.improved_session_count,
            "worsened_session_count": acc.worsened_session_count,
            "unchanged_session_count": acc.unchanged_session_count,
            "symbol_concentration": concentrated,
            "top_symbol_delta_share": _top_share(sym_abs, delta),
            "mfe_cohort_metrics": cohort_out,
        }

    def build_summary(self) -> dict[str, Any]:
        self.note_memory()
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        actual_pf = self.actual_pf()
        variants = {vid: self._variant_metrics(vid) for vid in self.variant_acc}
        adoption = {
            vid: phase343_adoption_assessment(
                met,
                actual_total=actual_total,
                actual_pf=actual_pf,
                phase342_baseline=self.phase342_baseline,
            )
            for vid, met in variants.items()
        }
        adopt_ready = [vid for vid, a in adoption.items() if a.get("adopt_ready")]

        tradeoff_rows = []
        for vid, met in sorted(variants.items()):
            tradeoff_rows.append(
                {
                    "variant_id": vid,
                    "max_mfe_pct": met.get("max_mfe_pct"),
                    "confirm_ticks": met.get("confirm_ticks"),
                    "profit_take_miss_yen_100": met.get("profit_take_miss_yen_100"),
                    "profit_take_miss_vs_phase342": round(
                        float(met.get("profit_take_miss_yen_100") or 0)
                        - float(self.phase342_baseline.get("profit_take_miss_yen_100") or 0),
                        2,
                    ),
                    "stop_hit_reduction_count": met.get("stop_hit_reduction_count"),
                    "delta_yen": met.get("delta_yen"),
                    "profit_factor": met.get("profit_factor"),
                    "tradeoff_score": met.get("tradeoff_score"),
                    "trigger_count": met.get("trigger_count"),
                }
            )
        tradeoff_rows.sort(key=lambda r: float(r.get("tradeoff_score") or 0), reverse=True)

        return {
            "phase": 343,
            "title": "board_failure_exit_small_mfe_filter_tuning",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "target_candidate": BOARD_FAILURE_EXIT_ID,
            "phase342_baseline": self.phase342_baseline,
            "sessions_evaluated": self.sessions_evaluated,
            "sessions_failed": self.sessions_failed,
            "positions_evaluated": self.positions_evaluated,
            "actual_total_pnl_yen_100": actual_total,
            "actual_pf": actual_pf,
            "actual_stop_hit_count": self.actual_stop_hit_count,
            "variants": variants,
            "adoption_assessment": adoption,
            "adopt_ready_variants": adopt_ready,
            "tradeoff_comparison": tradeoff_rows,
            "best_variant_by_tradeoff": tradeoff_rows[0]["variant_id"] if tradeoff_rows else None,
            "best_variant_by_delta_yen": (
                max(variants, key=lambda v: variants[v]["delta_yen"]) if variants else None
            ),
            "peak_memory_mb": self.peak_memory_mb,
            "failed_sessions": self.failed_sessions,
            "note": "Shadow only — MFE filter tuning; production EXIT unchanged",
        }

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        summary = self.build_summary()

        by_variant = []
        for vid, met in summary["variants"].items():
            row = {
                **met,
                **summary["adoption_assessment"][vid],
            }
            checks = row.pop("checks", {})
            if isinstance(checks, dict):
                for ck, cv in checks.items():
                    row[f"check_{ck}"] = cv
            row["mfe_cohort_metrics_json"] = json.dumps(
                met.get("mfe_cohort_metrics") or {}, ensure_ascii=False
            )
            by_variant.append(row)

        if by_variant:
            fields = sorted({k for r in by_variant for k in r})
            with paths["by_variant"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for row in by_variant:
                    w.writerow(row)

        cohort_rows: list[dict[str, Any]] = []
        for vid, met in summary["variants"].items():
            for cohort, cm in (met.get("mfe_cohort_metrics") or {}).items():
                cohort_rows.append({"variant_id": vid, "cohort": cohort, **cm})
        if cohort_rows:
            fields = sorted({k for r in cohort_rows for k in r})
            with paths["by_mfe_cohort"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for row in cohort_rows:
                    w.writerow(row)

        tradeoff = summary.get("tradeoff_comparison") or []
        if tradeoff:
            with paths["tradeoff"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(tradeoff[0].keys()), extrasaction="ignore")
                w.writeheader()
                for row in tradeoff:
                    w.writerow(row)

        sizes = {k: paths[k].stat().st_size if paths[k].is_file() else 0 for k in paths}
        summary["output_file_sizes_bytes"] = sizes
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {k: str(v) for k, v in paths.items()}
