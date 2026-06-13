"""
Phase341: vwap_dev_0p4pct robustness validation across additional sessions.
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
    _memory_mb,
    _top_share,
)
from research.phase340_vwap_dev_finetune_evaluation import (
    PROFIT_MISS_SMALL_THRESHOLD,
    _VariantAccum,
    adoption_assessment,
    tradeoff_score,
)
from small_paper.vwap_assisted_loss_tuning import (
    VWAP_CANDIDATE_ID,
    VwapTuningVariant,
    phase341_vwap_dev_0p4pct_variant,
)

JST = ZoneInfo("Asia/Tokyo")
VARIANT_ID = "vwap_dev_0p4pct"

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
]


@dataclass
class _SessionSnapshot:
    session_id: str
    day_key: str
    positions: int
    actual_total_pnl_yen_100: float
    shadow_total_pnl_yen_100: float
    session_delta_yen: float
    cumulative_shadow_pf: Optional[float]
    cumulative_actual_pf: Optional[float]


@dataclass
class Phase341RobustnessAggregator:
    reports_dir: Path
    variant: VwapTuningVariant = field(default_factory=phase341_vwap_dev_0p4pct_variant)
    sessions_evaluated: int = 0
    sessions_failed: int = 0
    positions_evaluated: int = 0
    actual_total_pnl_yen_100: float = 0.0
    actual_gross_profit_yen_100: float = 0.0
    actual_gross_loss_yen_100: float = 0.0
    actual_stop_hit_count: int = 0
    accum: _VariantAccum = field(default_factory=_VariantAccum)
    session_snapshots: list[_SessionSnapshot] = field(default_factory=list)
    symbol_session_delta: dict[str, dict[str, float]] = field(default_factory=dict)
    failed_sessions: list[dict[str, Any]] = field(default_factory=list)
    peak_memory_mb: float = 0.0

    def __post_init__(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase341_vwap_0p4pct_robustness_summary.json",
            "sessions": self.reports_dir / "phase341_vwap_0p4pct_robustness_sessions.csv",
            "trades": self.reports_dir / "phase341_vwap_0p4pct_robustness_trades.csv",
            "by_symbol": self.reports_dir / "phase341_vwap_0p4pct_robustness_by_symbol.csv",
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
        session_shadow = 0.0
        enriched: list[dict[str, Any]] = []

        for row in trade_rows:
            if str(row.get("variant_id") or "") != VARIANT_ID:
                continue
            pid = str(row.get("position_id") or "")
            if pid and pid not in seen_positions:
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
            enriched.append({**dict(row), "session_id": sid, "day_key": day_key})
            self.accum.ingest_trade(row)
            session_shadow += float(_float(row.get("shadow_pnl_yen_100")) or 0.0)
            sym = str(row.get("symbol") or "")
            delta = float(_float(row.get("candidate_vs_actual_delta_yen")) or 0.0)
            if sym:
                bucket = self.symbol_session_delta.setdefault(sym, {})
                bucket[sid] = round(bucket.get(sid, 0.0) + delta, 2)

        session_actual = round(sum(seen_positions.values()), 2)
        session_delta = round(session_shadow - session_actual, 2)
        self.accum.ingest_session_delta(session_delta)

        self.session_snapshots.append(
            _SessionSnapshot(
                session_id=sid,
                day_key=day_key,
                positions=len(seen_positions),
                actual_total_pnl_yen_100=session_actual,
                shadow_total_pnl_yen_100=round(session_shadow, 2),
                session_delta_yen=session_delta,
                cumulative_shadow_pf=self.accum.pf(),
                cumulative_actual_pf=self.actual_pf(),
            )
        )

        session_row = {
            **dict(session_meta),
            "positions": len(seen_positions),
            "push_rows": push_rows,
            "runtime_sec": round(runtime_sec, 1),
            "actual_total_pnl_yen_100": session_actual,
            "shadow_total_pnl_yen_100": round(session_shadow, 2),
            "session_delta_yen": session_delta,
            "vwap_coverage_pct": vwap_coverage_pct,
            "cumulative_delta_yen": round(self.accum.shadow_total_pnl_yen_100 - self.actual_total_pnl_yen_100, 2),
        }
        self._append_csv(self.paths()["sessions"], sorted(session_row.keys()), [session_row])
        self._append_csv(self.paths()["trades"], TRADE_FIELDS, enriched)
        self.sessions_evaluated += 1

    def _metrics(self) -> dict[str, Any]:
        acc = self.accum
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        delta = round(acc.shadow_total_pnl_yen_100 - actual_total, 2)
        profit_miss = round(acc.profit_take_miss_yen_100, 2)
        sym_abs = {s: abs(sum(v.values())) for s, v in self.symbol_session_delta.items() if s}
        top_share = _top_share(sym_abs, delta)
        concentrated = top_share is not None and top_share >= CONCENTRATION_THRESHOLD
        return {
            **self.variant.to_dict(),
            "candidate_id": VWAP_CANDIDATE_ID,
            "shadow_total_pnl_yen_100": round(acc.shadow_total_pnl_yen_100, 2),
            "delta_yen": delta,
            "profit_factor": acc.pf(),
            "trigger_count": acc.trigger_count,
            "improved_trade_count": acc.improved_trade_count,
            "worsened_trade_count": acc.worsened_trade_count,
            "stop_hit_reduction_count": acc.stop_hit_reduction_count,
            "profit_take_miss_yen_100": profit_miss,
            "stop_hit_avoidance_rate": acc.stop_hit_avoidance_rate(),
            "tradeoff_score": tradeoff_score(profit_miss, acc.stop_hit_reduction_count),
            "improved_session_count": acc.improved_session_count,
            "worsened_session_count": acc.worsened_session_count,
            "unchanged_session_count": acc.unchanged_session_count,
            "top_symbol_delta_share": top_share,
            "symbol_concentration": concentrated,
        }

    def _pf_trend_fail(self) -> bool:
        """Fail if cumulative shadow PF worsens in later half vs earlier half."""
        snaps = self.session_snapshots
        if len(snaps) < 4:
            return False
        mid = len(snaps) // 2
        first_pf = [s.cumulative_shadow_pf for s in snaps[:mid] if s.cumulative_shadow_pf is not None]
        second_pf = [s.cumulative_shadow_pf for s in snaps[mid:] if s.cumulative_shadow_pf is not None]
        if not first_pf or not second_pf:
            return False
        return float(second_pf[-1]) < float(first_pf[-1]) * 0.9

    def robustness_verdict(self, metrics: Mapping[str, Any]) -> dict[str, Any]:
        actual_pf = self.actual_pf()
        adoption = adoption_assessment(metrics, actual_total=self.actual_total_pnl_yen_100, actual_pf=actual_pf)
        checks = dict(adoption.get("checks") or {})
        top_share = metrics.get("top_symbol_delta_share")
        checks["top_symbol_share_below_60pct"] = (
            top_share is not None and float(top_share) < CONCENTRATION_THRESHOLD
        )
        checks["profit_take_miss_near_zero"] = (
            float(metrics.get("profit_take_miss_yen_100") or 0) >= PROFIT_MISS_SMALL_THRESHOLD
        )
        fail_reasons: list[str] = []
        if checks.get("symbol_concentration") or not checks.get("top_symbol_share_below_60pct"):
            fail_reasons.append("single_symbol_dependency")
        if self._pf_trend_fail():
            fail_reasons.append("pf_degrades_over_sessions")
            checks["pf_stable_over_sessions"] = False
        else:
            checks["pf_stable_over_sessions"] = True
        if float(metrics.get("profit_take_miss_yen_100") or 0) < PROFIT_MISS_SMALL_THRESHOLD:
            fail_reasons.append("profit_take_miss_increased")
        if int(metrics.get("stop_hit_reduction_count") or 0) <= 0:
            fail_reasons.append("stop_hit_reduction_lost")

        pass_core = all(
            [
                checks.get("total_pnl_improved"),
                checks.get("pf_improved"),
                checks.get("stop_hit_reduction"),
                checks.get("low_profit_take_miss"),
                checks.get("session_stability"),
                checks.get("top_symbol_share_below_60pct"),
                checks.get("pf_stable_over_sessions"),
            ]
        )
        return {
            "robustness_pass": pass_core and not fail_reasons,
            "adopt_ready": pass_core and not fail_reasons,
            "checks": checks,
            "fail_reasons": fail_reasons,
        }

    def by_symbol_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for sym, per_session in sorted(
            self.symbol_session_delta.items(),
            key=lambda x: abs(sum(x[1].values())),
            reverse=True,
        ):
            total_delta = round(sum(per_session.values()), 2)
            rows.append(
                {
                    "symbol": sym,
                    "total_delta_yen": total_delta,
                    "sessions_with_trades": len(per_session),
                    "per_session_delta_json": json.dumps(per_session, ensure_ascii=False),
                }
            )
        return rows

    def build_summary(self) -> dict[str, Any]:
        self.note_memory()
        metrics = self._metrics()
        verdict = self.robustness_verdict(metrics)
        return {
            "phase": 341,
            "title": "vwap_dev_0p4pct_robustness_validation",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "target_candidate": VWAP_CANDIDATE_ID,
            "variant_id": VARIANT_ID,
            "sessions_evaluated": self.sessions_evaluated,
            "sessions_failed": self.sessions_failed,
            "positions_evaluated": self.positions_evaluated,
            "actual_total_pnl_yen_100": round(self.actual_total_pnl_yen_100, 2),
            "actual_pf": self.actual_pf(),
            "actual_stop_hit_count": self.actual_stop_hit_count,
            "variant_metrics": metrics,
            "robustness_verdict": verdict,
            "session_snapshots": [
                {
                    "session_id": s.session_id,
                    "day_key": s.day_key,
                    "positions": s.positions,
                    "session_delta_yen": s.session_delta_yen,
                    "cumulative_shadow_pf": s.cumulative_shadow_pf,
                    "cumulative_actual_pf": s.cumulative_actual_pf,
                }
                for s in self.session_snapshots
            ],
            "peak_memory_mb": self.peak_memory_mb,
            "failed_sessions": self.failed_sessions,
            "note": "Pre-adoption robustness — production/Discord unchanged",
        }

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        summary = self.build_summary()
        by_symbol = self.by_symbol_rows()
        if by_symbol:
            with paths["by_symbol"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(by_symbol[0].keys()), extrasaction="ignore")
                w.writeheader()
                for row in by_symbol:
                    w.writerow(row)
        sizes = {k: paths[k].stat().st_size if paths[k].is_file() else 0 for k in paths}
        summary["output_file_sizes_bytes"] = sizes
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {k: str(v) for k, v in paths.items()}
