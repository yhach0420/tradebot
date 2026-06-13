"""
Phase373: Production monitoring pack for Phase355+364 live stack.

JSON/CSV only — does not touch Discord summary or canonical PnL aggregation.
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

from research.phase366_stophit_reclassification import MIN_DAY, production_kept_trades
from research.phase365_production_stack_validation import load_session_production_stack_trades
from research.phase372_low_mfe_immediate_death_forensic import annotate_immediate_death
from small_paper.high_mfe_stophit_exit_recovery_shadow import _build_tick_paths
from small_paper.near_day_high_low_momentum_dynamic40_entry_guard import (
    REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
)
from small_paper.pullback_misread_dynamic40_entry_guard import (
    REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
)
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

JST = ZoneInfo("Asia/Tokyo")
LOW_MFE_THRESHOLD_PCT = 0.3

GUARD_REJECT_REASONS = frozenset(
    {
        REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD,
        REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
    }
)

REJECT_FIELDS = [
    "session_id",
    "day_key",
    "session_kind",
    "symbol",
    "event_time",
    "entry_time",
    "reject_reason",
    "universe_slot",
    "universe_bucket",
    "entry_rise_5min_pct",
    "entry_vwap_dev_pct",
    "day_high_distance_pct",
    "entry_momentum_score",
    "core10_guard_anomaly",
]

STOPHIT_FIELDS = [
    "session_id",
    "day_key",
    "session_kind",
    "universe_group",
    "universe_slot",
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "peak_mfe_pct",
    "exit_reason_canonical",
    "is_low_mfe_stop",
    "loss_60s_0p3",
    "loss_120s_0p5",
    "min_pnl_first_60s",
    "min_pnl_first_120s",
]

BY_SYMBOL_FIELDS = [
    "symbol",
    "accepted_trade_count",
    "stop_hit_count",
    "low_mfe_stop_hit_count",
    "immediate_death_60s_count",
    "immediate_death_120s_count",
    "dynamic40_stop_hit_count",
    "core10_stop_hit_count",
    "pullback_misread_dynamic40_reject_count",
    "near_day_high_low_momentum_dynamic40_reject_count",
    "total_guard_reject_count",
    "core10_guard_reject_count",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val in (None, ""):
        return False
    return str(val).lower() in {"1", "true", "yes", "y"}


def _event_reject_reason(row: Mapping[str, Any]) -> str:
    return str(row.get("gate_reject_reason") or row.get("reject_reason") or "").strip()


def _is_low_mfe_stop(row: Mapping[str, Any]) -> bool:
    if str(row.get("exit_reason_canonical") or "") != "stop_hit":
        return False
    peak = _float(row.get("peak_mfe_pct"))
    return (peak if peak is not None else 0.0) < LOW_MFE_THRESHOLD_PCT


def _is_dynamic40_group(row: Mapping[str, Any]) -> bool:
    return str(row.get("universe_group") or "") == "dynamic40"


def _is_core10_group(row: Mapping[str, Any]) -> bool:
    return str(row.get("universe_group") or "") == "core10"


def _is_core10_slot(row: Mapping[str, Any]) -> bool:
    return str(row.get("universe_slot") or "").lower() == "core"


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _empty_metrics() -> dict[str, int]:
    return {
        "pullback_misread_dynamic40_reject_count": 0,
        "near_day_high_low_momentum_dynamic40_reject_count": 0,
        "total_guard_reject_count": 0,
        "accepted_trade_count": 0,
        "stop_hit_count": 0,
        "low_mfe_stop_hit_count": 0,
        "immediate_death_60s_count": 0,
        "immediate_death_120s_count": 0,
        "dynamic40_stop_hit_count": 0,
        "core10_stop_hit_count": 0,
        "dynamic40_low_mfe_stop_hit_count": 0,
        "core10_low_mfe_stop_hit_count": 0,
        "core10_guard_reject_count": 0,
        "raw_accepted_event_count": 0,
        "raw_rejected_event_count": 0,
    }


def _merge_metrics(dst: dict[str, int], src: Mapping[str, int]) -> None:
    for key in _empty_metrics():
        dst[key] = int(dst.get(key, 0)) + int(src.get(key, 0))


def summarize_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    out = _empty_metrics()
    for row in rows:
        _merge_metrics(out, row)
    return out


def classify_guard_reject(row: Mapping[str, Any]) -> Optional[str]:
    reason = _event_reject_reason(row)
    if reason in GUARD_REJECT_REASONS:
        return reason
    return None


def build_reject_row(
    row: Mapping[str, Any],
    *,
    session_meta: Mapping[str, Any],
    session_kind: str,
) -> dict[str, Any]:
    reason = classify_guard_reject(row) or _event_reject_reason(row)
    slot = str(row.get("universe_slot") or "")
    return {
        "session_id": session_meta.get("session_id") or "",
        "day_key": session_meta.get("day_key") or session_meta.get("day") or "",
        "session_kind": session_kind,
        "symbol": row.get("symbol") or "",
        "event_time": row.get("event_time") or "",
        "entry_time": row.get("entry_time") or "",
        "reject_reason": reason,
        "universe_slot": slot,
        "universe_bucket": row.get("universe_bucket") or row.get("source_bucket") or "",
        "entry_rise_5min_pct": _float(row.get("entry_rise_5min_pct")),
        "entry_vwap_dev_pct": _float(row.get("entry_vwap_dev_pct")),
        "day_high_distance_pct": _float(
            row.get("day_high_distance_pct") or row.get("entry_near_day_high_pct")
        ),
        "entry_momentum_score": _float(
            row.get("entry_momentum_score")
            or row.get("entry_momentum_continuation_score")
        ),
        "core10_guard_anomaly": _is_core10_slot(row),
    }


def build_stophit_row(
    trade: Mapping[str, Any],
    *,
    death: Mapping[str, Any],
) -> dict[str, Any]:
    low_mfe = _is_low_mfe_stop(trade)
    dyn = _is_dynamic40_group(trade)
    core = _is_core10_group(trade)
    return {
        "session_id": trade.get("session_id") or "",
        "day_key": trade.get("day_key") or "",
        "session_kind": trade.get("session_kind") or "",
        "universe_group": trade.get("universe_group") or "",
        "universe_slot": trade.get("universe_slot") or "",
        "symbol": trade.get("symbol") or "",
        "entry_time": trade.get("entry_time") or "",
        "exit_time": trade.get("exit_time") or "",
        "pnl_yen_100": _float(trade.get("pnl_yen_100")),
        "peak_mfe_pct": _float(trade.get("peak_mfe_pct")),
        "exit_reason_canonical": trade.get("exit_reason_canonical") or "",
        "is_low_mfe_stop": low_mfe,
        "loss_60s_0p3": _bool(death.get("loss_60s_0p3")),
        "loss_120s_0p5": _bool(death.get("loss_120s_0p5")),
        "min_pnl_first_60s": death.get("min_pnl_first_60s"),
        "min_pnl_first_120s": death.get("min_pnl_first_120s"),
        "is_dynamic40": dyn,
        "is_core10": core,
        "is_dynamic40_low_mfe": low_mfe and dyn,
        "is_core10_low_mfe": low_mfe and core,
    }


def session_metrics_from_parts(
    *,
    reject_rows: Sequence[Mapping[str, Any]],
    production_trades: Sequence[Mapping[str, Any]],
    stophit_rows: Sequence[Mapping[str, Any]],
    raw_accepted: int,
    raw_rejected: int,
) -> dict[str, int]:
    metrics = _empty_metrics()
    metrics["raw_accepted_event_count"] = raw_accepted
    metrics["raw_rejected_event_count"] = raw_rejected
    metrics["accepted_trade_count"] = len(production_trades)

    for rej in reject_rows:
        reason = str(rej.get("reject_reason") or "")
        if reason == REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD:
            metrics["pullback_misread_dynamic40_reject_count"] += 1
        elif reason == REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD:
            metrics["near_day_high_low_momentum_dynamic40_reject_count"] += 1
        if reason in GUARD_REJECT_REASONS:
            metrics["total_guard_reject_count"] += 1
        if _bool(rej.get("core10_guard_anomaly")):
            metrics["core10_guard_reject_count"] += 1

    for trade in production_trades:
        if _bool(trade.get("loss_60s_0p3")):
            metrics["immediate_death_60s_count"] += 1
        if _bool(trade.get("loss_120s_0p5")):
            metrics["immediate_death_120s_count"] += 1

    for row in stophit_rows:
        if str(row.get("exit_reason_canonical") or "") != "stop_hit":
            continue
        metrics["stop_hit_count"] += 1
        if _bool(row.get("is_low_mfe_stop")):
            metrics["low_mfe_stop_hit_count"] += 1
        if _bool(row.get("is_dynamic40")):
            metrics["dynamic40_stop_hit_count"] += 1
        if _bool(row.get("is_core10")):
            metrics["core10_stop_hit_count"] += 1
        if _bool(row.get("is_dynamic40_low_mfe")):
            metrics["dynamic40_low_mfe_stop_hit_count"] += 1
        if _bool(row.get("is_core10_low_mfe")):
            metrics["core10_low_mfe_stop_hit_count"] += 1

    return metrics


def load_session_production_monitoring(
    session_meta: Mapping[str, Any], *, reports_dir: Path
) -> dict[str, Any]:
    base = load_session_production_stack_trades(session_meta, reports_dir=reports_dir)
    if base.get("error"):
        return {
            **base,
            "session_meta": dict(session_meta),
            "metrics": _empty_metrics(),
            "reject_rows": [],
            "stophit_rows": [],
            "production_trades": [],
        }

    sess_dir = Path(str(session_meta["session_dir"]))
    session_kind = str(base.get("session_kind") or session_meta.get("session_kind") or "")
    events_path = sess_dir / "small_paper_events.csv"

    reject_rows: list[dict[str, Any]] = []
    raw_accepted = 0
    raw_rejected = 0
    if events_path.is_file():
        for row in _stream_events_csv(events_path):
            et = str(row.get("event_type") or "")
            if et == "accepted":
                raw_accepted += 1
            elif et == "rejected":
                raw_rejected += 1
                reason = classify_guard_reject(row)
                if reason:
                    reject_rows.append(
                        build_reject_row(row, session_meta=session_meta, session_kind=session_kind)
                    )

    production = production_kept_trades(base)
    trade_keys = {(t.get("symbol", ""), t.get("entry_time", "")) for t in production}
    tick_paths = _build_tick_paths(events_path, trade_keys) if events_path.is_file() else {}

    production_enriched: list[dict[str, Any]] = []
    stophit_rows: list[dict[str, Any]] = []
    for trade in production:
        key = (trade.get("symbol", ""), trade.get("entry_time", ""))
        death = annotate_immediate_death(tick_paths.get(key, []))
        row = dict(trade)
        row.update(death)
        row["session_id"] = session_meta.get("session_id") or ""
        row["day_key"] = session_meta.get("day_key") or session_meta.get("day") or ""
        row["session_kind"] = session_kind
        production_enriched.append(row)
        if str(row.get("exit_reason_canonical") or "") == "stop_hit":
            stophit_rows.append(build_stophit_row(row, death=death))

    metrics = session_metrics_from_parts(
        reject_rows=reject_rows,
        production_trades=production_enriched,
        stophit_rows=stophit_rows,
        raw_accepted=raw_accepted,
        raw_rejected=raw_rejected,
    )

    return {
        **base,
        "session_meta": dict(session_meta),
        "metrics": metrics,
        "reject_rows": reject_rows,
        "stophit_rows": stophit_rows,
        "production_trades": production_enriched,
        "error": "",
    }


@dataclass
class Phase373ProductionMonitoring:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase373_production_monitoring_summary.json",
            "by_symbol": self.reports_dir / "phase373_production_monitoring_by_symbol.csv",
            "rejects": self.reports_dir / "phase373_production_monitoring_rejects.csv",
            "stophit": self.reports_dir / "phase373_production_monitoring_stophit.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.session_results.append(dict(result))

    def _aggregate(self) -> dict[str, Any]:
        all_rejects: list[dict[str, Any]] = []
        all_stops: list[dict[str, Any]] = []
        session_rows: list[dict[str, Any]] = []
        by_day: dict[str, dict[str, int]] = defaultdict(_empty_metrics)
        by_symbol: dict[str, dict[str, int]] = defaultdict(_empty_metrics)

        for sr in self.session_results:
            meta = sr.get("session_meta") or {}
            day_key = str(meta.get("day_key") or meta.get("day") or "")
            metrics = dict(sr.get("metrics") or _empty_metrics())
            session_rows.append(
                {
                    "session_id": meta.get("session_id") or "",
                    "day_key": day_key,
                    "session_kind": meta.get("session_kind") or sr.get("session_kind") or "",
                    **metrics,
                }
            )
            _merge_metrics(by_day[day_key], metrics)

            for rej in sr.get("reject_rows") or []:
                all_rejects.append(dict(rej))
                sym = str(rej.get("symbol") or "")
                reason = str(rej.get("reject_reason") or "")
                if reason == REJECT_PULLBACK_MISREAD_DYNAMIC40_GUARD:
                    by_symbol[sym]["pullback_misread_dynamic40_reject_count"] += 1
                elif reason == REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD:
                    by_symbol[sym]["near_day_high_low_momentum_dynamic40_reject_count"] += 1
                if reason in GUARD_REJECT_REASONS:
                    by_symbol[sym]["total_guard_reject_count"] += 1
                if _bool(rej.get("core10_guard_anomaly")):
                    by_symbol[sym]["core10_guard_reject_count"] += 1

            for trade in sr.get("production_trades") or []:
                sym = str(trade.get("symbol") or "")
                by_symbol[sym]["accepted_trade_count"] += 1
                if _bool(trade.get("loss_60s_0p3")):
                    by_symbol[sym]["immediate_death_60s_count"] += 1
                if _bool(trade.get("loss_120s_0p5")):
                    by_symbol[sym]["immediate_death_120s_count"] += 1

            for stop in sr.get("stophit_rows") or []:
                all_stops.append(dict(stop))
                sym = str(stop.get("symbol") or "")
                by_symbol[sym]["stop_hit_count"] += 1
                if _bool(stop.get("is_low_mfe_stop")):
                    by_symbol[sym]["low_mfe_stop_hit_count"] += 1
                if _bool(stop.get("is_dynamic40")):
                    by_symbol[sym]["dynamic40_stop_hit_count"] += 1
                if _bool(stop.get("is_core10")):
                    by_symbol[sym]["core10_stop_hit_count"] += 1

        overall = summarize_metrics(session_rows)
        day_rows = [
            {"day_key": day, **metrics}
            for day, metrics in sorted(by_day.items())
        ]
        symbol_rows = [
            {"symbol": sym, **metrics}
            for sym, metrics in sorted(by_symbol.items())
        ]

        return {
            "overall": overall,
            "by_day": day_rows,
            "by_session": session_rows,
            "symbol_rows": symbol_rows,
            "all_rejects": all_rejects,
            "all_stops": all_stops,
        }

    def _daily_checks(self, agg: Mapping[str, Any]) -> dict[str, Any]:
        overall = agg.get("overall") or {}
        by_day = list(agg.get("by_day") or [])
        symbol_rows = list(agg.get("symbol_rows") or [])
        all_stops = list(agg.get("all_stops") or [])

        guards_firing = int(overall.get("total_guard_reject_count") or 0) > 0
        core10_guard_clean = int(overall.get("core10_guard_reject_count") or 0) == 0

        stop_trend = [
            {
                "day_key": row.get("day_key"),
                "stop_hit_count": row.get("stop_hit_count"),
                "low_mfe_stop_hit_count": row.get("low_mfe_stop_hit_count"),
            }
            for row in by_day
        ]

        low_mfe_symbols = sorted(
            [
                {
                    "symbol": r.get("symbol"),
                    "low_mfe_stop_hit_count": r.get("low_mfe_stop_hit_count"),
                    "stop_hit_count": r.get("stop_hit_count"),
                }
                for r in symbol_rows
                if int(r.get("low_mfe_stop_hit_count") or 0) > 0
            ],
            key=lambda x: (-int(x.get("low_mfe_stop_hit_count") or 0), str(x.get("symbol"))),
        )

        immediate_death_days = sorted(
            [
                {
                    "day_key": row.get("day_key"),
                    "immediate_death_60s_count": row.get("immediate_death_60s_count"),
                    "immediate_death_120s_count": row.get("immediate_death_120s_count"),
                    "accepted_trade_count": row.get("accepted_trade_count"),
                }
                for row in by_day
            ],
            key=lambda x: (
                -int(x.get("immediate_death_60s_count") or 0),
                -int(x.get("immediate_death_120s_count") or 0),
            ),
        )

        return {
            "guards_firing": guards_firing,
            "guards_firing_detail": {
                "pullback_misread_dynamic40_reject_count": overall.get(
                    "pullback_misread_dynamic40_reject_count"
                ),
                "near_day_high_low_momentum_dynamic40_reject_count": overall.get(
                    "near_day_high_low_momentum_dynamic40_reject_count"
                ),
                "total_guard_reject_count": overall.get("total_guard_reject_count"),
            },
            "core10_not_caught_by_guards": core10_guard_clean,
            "core10_guard_reject_count": overall.get("core10_guard_reject_count"),
            "stop_hit_trend_by_day": stop_trend,
            "low_mfe_stop_symbols": low_mfe_symbols[:30],
            "immediate_death_heavy_days": immediate_death_days[:10],
            "low_mfe_stop_hit_total": overall.get("low_mfe_stop_hit_count"),
            "stop_hit_total": overall.get("stop_hit_count"),
            "immediate_death_60s_total": overall.get("immediate_death_60s_count"),
            "immediate_death_120s_total": overall.get("immediate_death_120s_count"),
            "low_mfe_stop_hit_share": round(
                int(overall.get("low_mfe_stop_hit_count") or 0)
                / int(overall.get("stop_hit_count") or 1),
                4,
            )
            if int(overall.get("stop_hit_count") or 0) > 0
            else 0.0,
            "stophit_rows_with_immediate_death_60s": sum(
                1 for s in all_stops if _bool(s.get("loss_60s_0p3"))
            ),
        }

    def finalize_outputs(
        self,
        *,
        wall_runtime_sec: float,
        sessions_discovered: int,
        sessions_evaluated: int,
    ) -> dict[str, Path]:
        paths = self.paths()
        agg = self._aggregate()
        overall = agg["overall"]
        daily_checks = self._daily_checks(agg)

        summary = {
            "phase": 373,
            "title": "Production monitoring pack (Phase355+364)",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": {
                "min_day": MIN_DAY,
                "stack": "C_phase355_plus_phase364",
                "sessions_discovered": sessions_discovered,
                "sessions_evaluated": sessions_evaluated,
            },
            "metrics": {
                "pullback_misread_dynamic40_reject_count": overall.get(
                    "pullback_misread_dynamic40_reject_count"
                ),
                "near_day_high_low_momentum_dynamic40_reject_count": overall.get(
                    "near_day_high_low_momentum_dynamic40_reject_count"
                ),
                "total_guard_reject_count": overall.get("total_guard_reject_count"),
                "accepted_trade_count": overall.get("accepted_trade_count"),
                "stop_hit_count": overall.get("stop_hit_count"),
                "low_mfe_stop_hit_count": overall.get("low_mfe_stop_hit_count"),
                "immediate_death_60s_count": overall.get("immediate_death_60s_count"),
                "immediate_death_120s_count": overall.get("immediate_death_120s_count"),
                "dynamic40_stop_hit_count": overall.get("dynamic40_stop_hit_count"),
                "core10_stop_hit_count": overall.get("core10_stop_hit_count"),
                "dynamic40_low_mfe_stop_hit_count": overall.get(
                    "dynamic40_low_mfe_stop_hit_count"
                ),
                "core10_low_mfe_stop_hit_count": overall.get("core10_low_mfe_stop_hit_count"),
                "core10_guard_reject_count": overall.get("core10_guard_reject_count"),
            },
            "by_day": agg["by_day"],
            "by_session": agg["by_session"],
            "daily_checks": daily_checks,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "output_note": (
                "JSON/CSV monitoring only; Discord summary and canonical PnL unchanged."
            ),
        }

        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_csv(paths["by_symbol"], agg["symbol_rows"], BY_SYMBOL_FIELDS)
        _write_csv(paths["rejects"], agg["all_rejects"], REJECT_FIELDS)
        _write_csv(paths["stophit"], agg["all_stops"], STOPHIT_FIELDS)
        return paths
