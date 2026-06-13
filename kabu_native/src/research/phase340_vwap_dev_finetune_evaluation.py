"""
Phase340: VWAP deviation fine-tuning around 0.5% (incremental multi-session).
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
    CONCENTRATION_THRESHOLD,
    _float,
    _is_concentrated,
    _memory_mb,
    _top_share,
)
from small_paper.vwap_assisted_loss_tuning import (
    VWAP_CANDIDATE_ID,
    VwapTuningVariant,
    default_phase340_variants,
)

JST = ZoneInfo("Asia/Tokyo")
PROFIT_MISS_SMALL_THRESHOLD = -500.0

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "symbol",
    "position_id",
    "variant_id",
    "shadow_pnl_yen_100",
    "actual_exit_reason",
    "actual_pnl_yen_100",
    "candidate_vs_actual_delta_yen",
    "no_candidate_trigger",
    "min_vwap_dev_pct",
]


@dataclass
class _VariantAccum:
    shadow_total_pnl_yen_100: float = 0.0
    gross_profit_yen_100: float = 0.0
    gross_loss_yen_100: float = 0.0
    trigger_count: int = 0
    no_trigger_count: int = 0
    improved_trade_count: int = 0
    worsened_trade_count: int = 0
    stop_hit_reduction_count: int = 0
    profit_take_miss_yen_100: float = 0.0
    stop_hit_positions: int = 0
    stop_hit_avoided_count: int = 0
    improved_session_count: int = 0
    worsened_session_count: int = 0
    unchanged_session_count: int = 0
    symbol_delta_yen: dict[str, float] = field(default_factory=dict)

    def pf(self) -> Optional[float]:
        if self.gross_loss_yen_100 <= 0:
            return None if self.gross_profit_yen_100 <= 0 else float("inf")
        return round(self.gross_profit_yen_100 / self.gross_loss_yen_100, 4)

    def stop_hit_avoidance_rate(self) -> Optional[float]:
        if self.stop_hit_positions <= 0:
            return None
        return round(self.stop_hit_avoided_count / self.stop_hit_positions, 4)

    def ingest_trade(self, row: Mapping[str, Any]) -> None:
        shadow = float(_float(row.get("shadow_pnl_yen_100")) or 0.0)
        actual = float(_float(row.get("actual_pnl_yen_100")) or 0.0)
        delta = float(_float(row.get("candidate_vs_actual_delta_yen")) or 0.0)
        sym = str(row.get("symbol") or "")
        self.shadow_total_pnl_yen_100 += shadow
        if shadow > 0:
            self.gross_profit_yen_100 += shadow
        elif shadow < 0:
            self.gross_loss_yen_100 += abs(shadow)
        if row.get("no_candidate_trigger"):
            self.no_trigger_count += 1
        else:
            self.trigger_count += 1
        if delta > 0:
            self.improved_trade_count += 1
        elif delta < 0:
            self.worsened_trade_count += 1
        if row.get("actual_exit_reason") == "stop_hit":
            self.stop_hit_positions += 1
            if not row.get("no_candidate_trigger") and delta > 0:
                self.stop_hit_avoided_count += 1
                self.stop_hit_reduction_count += 1
        if actual > 0 and delta < 0:
            self.profit_take_miss_yen_100 += delta
        if sym:
            self.symbol_delta_yen[sym] = round(self.symbol_delta_yen.get(sym, 0.0) + delta, 2)

    def ingest_session_delta(self, session_delta: float) -> None:
        if session_delta > 0:
            self.improved_session_count += 1
        elif session_delta < 0:
            self.worsened_session_count += 1
        else:
            self.unchanged_session_count += 1


def tradeoff_score(profit_take_miss: float, stop_hit_reduction: int) -> float:
    """Higher is better: prioritize stop reduction, penalize profit miss."""
    return round(float(stop_hit_reduction) * 1000.0 + float(profit_take_miss), 2)


def adoption_assessment(
    metrics: Mapping[str, Any],
    *,
    actual_total: float,
    actual_pf: Optional[float],
) -> dict[str, Any]:
    delta = float(metrics.get("delta_yen") or 0)
    pf = metrics.get("profit_factor")
    profit_miss = float(metrics.get("profit_take_miss_yen_100") or 0)
    stop_red = int(metrics.get("stop_hit_reduction_count") or 0)
    improved_s = int(metrics.get("improved_session_count") or 0)
    worsened_s = int(metrics.get("worsened_session_count") or 0)
    concentrated = bool(metrics.get("symbol_concentration"))

    checks = {
        "total_pnl_improved": delta > 0,
        "pf_improved": (
            pf is not None
            and actual_pf is not None
            and pf != float("inf")
            and actual_pf != float("inf")
            and float(pf) > float(actual_pf)
        ),
        "stop_hit_reduction": stop_red > 0,
        "low_profit_take_miss": profit_miss >= PROFIT_MISS_SMALL_THRESHOLD,
        "session_stability": improved_s >= worsened_s,
        "not_symbol_concentrated": not concentrated,
    }
    return {"adopt_ready": all(checks.values()), "checks": checks}


@dataclass
class Phase340IncrementalAggregator:
    reports_dir: Path
    variants: tuple[VwapTuningVariant, ...] = field(default_factory=default_phase340_variants)
    sessions_evaluated: int = 0
    sessions_failed: int = 0
    positions_evaluated: int = 0
    actual_total_pnl_yen_100: float = 0.0
    actual_gross_profit_yen_100: float = 0.0
    actual_gross_loss_yen_100: float = 0.0
    actual_stop_hit_count: int = 0
    variant_acc: dict[str, _VariantAccum] = field(default_factory=dict)
    variant_meta: dict[str, VwapTuningVariant] = field(default_factory=dict)
    failed_sessions: list[dict[str, Any]] = field(default_factory=list)
    peak_memory_mb: float = 0.0

    def __post_init__(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        for v in self.variants:
            self.variant_acc.setdefault(v.variant_id, _VariantAccum())
            self.variant_meta[v.variant_id] = v

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase340_vwap_dev_finetune_summary.json",
            "sessions": self.reports_dir / "phase340_vwap_dev_finetune_sessions.csv",
            "trades": self.reports_dir / "phase340_vwap_dev_finetune_trades.csv",
            "by_variant": self.reports_dir / "phase340_vwap_dev_finetune_by_variant.csv",
            "tradeoff": self.reports_dir / "phase340_vwap_dev_finetune_tradeoff.csv",
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

    def ingest_session(
        self,
        *,
        session_meta: Mapping[str, Any],
        trade_rows: Sequence[Mapping[str, Any]],
        push_rows: int,
        runtime_sec: float,
        vwap_coverage_pct: Optional[float] = None,
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

        session_actual = round(sum(seen_positions.values()), 2)
        session_shadow: dict[str, float] = defaultdict(float)
        for row in enriched:
            session_shadow[str(row.get("variant_id") or "")] += float(
                _float(row.get("shadow_pnl_yen_100")) or 0.0
            )

        for vid in self.variant_acc:
            sh = round(session_shadow.get(vid, 0.0), 2)
            self.variant_acc[vid].ingest_session_delta(round(sh - session_actual, 2))

        session_row: dict[str, Any] = {
            **dict(session_meta),
            "positions": len(seen_positions),
            "push_rows": push_rows,
            "runtime_sec": round(runtime_sec, 1),
            "actual_total_pnl_yen_100": session_actual,
            "vwap_coverage_pct": vwap_coverage_pct,
        }
        for vid in self.variant_acc:
            sh = round(session_shadow.get(vid, 0.0), 2)
            session_row[f"{vid}_shadow_pnl_yen_100"] = sh
            session_row[f"{vid}_session_delta_yen"] = round(sh - session_actual, 2)
        self._append_csv(self.paths()["sessions"], sorted(session_row.keys()), [session_row])
        self._append_csv(self.paths()["trades"], TRADE_FIELDS, enriched)
        self.sessions_evaluated += 1

    def actual_pf(self) -> Optional[float]:
        if self.actual_gross_loss_yen_100 <= 0:
            return None if self.actual_gross_profit_yen_100 <= 0 else float("inf")
        return round(self.actual_gross_profit_yen_100 / self.actual_gross_loss_yen_100, 4)

    def _variant_metrics(self, vid: str) -> dict[str, Any]:
        acc = self.variant_acc[vid]
        meta = self.variant_meta[vid]
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        delta = round(acc.shadow_total_pnl_yen_100 - actual_total, 2)
        profit_miss = round(acc.profit_take_miss_yen_100, 2)
        sym_abs = {s: abs(v) for s, v in acc.symbol_delta_yen.items() if s}
        concentrated = _is_concentrated(sym_abs, delta)
        return {
            **meta.to_dict(),
            "candidate_id": VWAP_CANDIDATE_ID,
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
        }

    def build_summary(self) -> dict[str, Any]:
        self.note_memory()
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        actual_pf = self.actual_pf()
        variants = {vid: self._variant_metrics(vid) for vid in self.variant_acc}
        adoption = {
            vid: adoption_assessment(met, actual_total=actual_total, actual_pf=actual_pf)
            for vid, met in variants.items()
        }
        adopt_ready = [vid for vid, a in adoption.items() if a.get("adopt_ready")]

        tradeoff_rows = []
        for vid, met in sorted(variants.items()):
            tradeoff_rows.append(
                {
                    "variant_id": vid,
                    "min_vwap_dev_pct": met.get("min_vwap_dev_pct"),
                    "profit_take_miss_yen_100": met.get("profit_take_miss_yen_100"),
                    "stop_hit_reduction_count": met.get("stop_hit_reduction_count"),
                    "stop_hit_avoidance_rate": met.get("stop_hit_avoidance_rate"),
                    "delta_yen": met.get("delta_yen"),
                    "tradeoff_score": met.get("tradeoff_score"),
                    "improved_session_count": met.get("improved_session_count"),
                    "worsened_session_count": met.get("worsened_session_count"),
                }
            )
        tradeoff_rows.sort(key=lambda r: float(r.get("tradeoff_score") or 0), reverse=True)

        best_tradeoff = tradeoff_rows[0]["variant_id"] if tradeoff_rows else None
        best_delta = max(variants, key=lambda v: variants[v]["delta_yen"]) if variants else None

        return {
            "phase": 340,
            "title": "vwap_assisted_loss_exit_dev_finetune",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "target_candidate": VWAP_CANDIDATE_ID,
            "frozen_candidates": [
                "loss_acceleration_exit",
                "board_collapse_profit_exit",
                "profit_protect_exit",
                "high_update_failure_exit",
            ],
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
            "best_variant_by_tradeoff": best_tradeoff,
            "best_variant_by_delta_yen": best_delta,
            "primary_selection": "tradeoff_score",
            "peak_memory_mb": self.peak_memory_mb,
            "failed_sessions": self.failed_sessions,
            "note": "Fine-tune VWAP dev around 0.5% — tradeoff over raw total_pnl",
        }

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        summary = self.build_summary()
        actual_total = summary["actual_total_pnl_yen_100"]
        actual_pf = summary["actual_pf"]

        by_variant = []
        for vid, met in summary["variants"].items():
            row = {**met, **summary["adoption_assessment"][vid]}
            checks = row.pop("checks", {})
            if isinstance(checks, dict):
                for ck, cv in checks.items():
                    row[f"check_{ck}"] = cv
            by_variant.append(row)

        if by_variant:
            fields = sorted({k for r in by_variant for k in r})
            with paths["by_variant"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for row in by_variant:
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
