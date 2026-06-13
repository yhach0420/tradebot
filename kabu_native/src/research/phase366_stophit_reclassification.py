"""
Phase366: Post-Phase364 stop_hit reclassification by peak MFE band.

Population: Phase355 + Phase364 production stack (kept trades only).
Period: 20260529+ observer_exit sessions.
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

from research.phase365_production_stack_validation import (
    load_session_production_stack_trades,
    stack_blocked,
)

JST = ZoneInfo("Asia/Tokyo")
MIN_DAY = "20260529"
HIGH_MFE_THRESHOLD_PCT = 0.3

MFE_BANDS = (
    ("A", "peak_mfe_lt_0.3", 0.0, 0.3),
    ("B", "peak_mfe_0.3_to_0.6", 0.3, 0.6),
    ("C", "peak_mfe_0.6_to_1.0", 0.6, 1.0),
    ("D", "peak_mfe_ge_1.0", 1.0, None),
)

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "session_kind",
    "universe_group",
    "universe_slot",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "pnl_yen_100",
    "pnl_pct",
    "peak_mfe_pct",
    "mfe_left_pct",
    "mfe_capture_ratio",
    "exit_reason_canonical",
    "mfe_band",
    "mfe_band_label",
    "board_dynamic_trailing_tier",
    "entry_imbalance_percentile",
    "entry_rise_5min_pct",
    "entry_vwap_dev_pct",
    "day_high_distance_pct",
    "entry_momentum_score",
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


def classify_mfe_band(peak_mfe_pct: Optional[float]) -> tuple[str, str]:
    peak = peak_mfe_pct if peak_mfe_pct is not None else 0.0
    for band_id, label, lo, hi in MFE_BANDS:
        if hi is None:
            if peak >= lo:
                return band_id, label
        elif lo <= peak < hi:
            return band_id, label
    return "A", "peak_mfe_lt_0.3"


def production_kept_trades(session_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    session_kind = str(session_result.get("session_kind") or "")
    kept: list[dict[str, Any]] = []
    for t in session_result.get("trades") or []:
        if stack_blocked("C_phase355_plus_phase364", t, session_kind=session_kind):
            continue
        row = dict(t)
        peak = _float(row.get("peak_mfe_pct"))
        band_id, band_label = classify_mfe_band(peak)
        row["peak_mfe_pct"] = peak
        row["mfe_band"] = band_id
        row["mfe_band_label"] = band_label
        row["universe_group"] = row.get("universe_group") or "other"
        kept.append(row)
    return kept


def load_session_stophit_trades(
    session_meta: Mapping[str, Any], *, reports_dir: Path
) -> dict[str, Any]:
    base = load_session_production_stack_trades(session_meta, reports_dir=reports_dir)
    if base.get("error"):
        return {**base, "production_trades": [], "stop_hit_trades": [], "error": base.get("error")}

    production = production_kept_trades(base)
    stop_hits = [t for t in production if t.get("exit_reason_canonical") == "stop_hit"]
    return {
        **base,
        "production_trades": production,
        "stop_hit_trades": stop_hits,
        "production_trade_count": len(production),
        "stop_hit_count": len(stop_hits),
        "error": "",
    }


def _metrics(yens: Sequence[float]) -> dict[str, Any]:
    vals = [float(y) for y in yens]
    n = len(vals)
    total = round(sum(vals), 2) if vals else 0.0
    return {
        "count": n,
        "total_pnl_yen_100": total,
        "avg_pnl_yen_100": round(total / n, 2) if n else None,
        "profit_factor": _pf(vals),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@dataclass
class Phase366StopHitReclassification:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase366_stophit_reclassification_summary.json",
            "by_mfe": self.reports_dir / "phase366_stophit_by_mfe.csv",
            "by_symbol": self.reports_dir / "phase366_stophit_by_symbol.csv",
            "trades": self.reports_dir / "phase366_stophit_trades.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.session_results.append(dict(result))

    def _aggregate(self) -> dict[str, Any]:
        all_stops: list[dict[str, Any]] = []
        production_total = 0

        for sr in self.session_results:
            production_total += int(sr.get("production_trade_count") or 0)
            all_stops.extend(sr.get("stop_hit_trades") or [])

        by_band: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_universe_band: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        by_am_pm_band: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

        for t in all_stops:
            band = str(t.get("mfe_band") or "A")
            sym = str(t.get("symbol") or "")
            ug = str(t.get("universe_group") or "other")
            sk = str(t.get("session_kind") or "")
            by_band[band].append(t)
            by_symbol[sym].append(t)
            by_universe_band[(ug, band)].append(t)
            by_am_pm_band[(sk, band)].append(t)

        band_rows: list[dict[str, Any]] = []
        band_summary: dict[str, dict[str, Any]] = {}
        for band_id, label, _, _ in MFE_BANDS:
            rows = by_band.get(band_id, [])
            yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in rows]
            mfe_left_sum = sum(float(_float(t.get("mfe_left_pct")) or 0.0) for t in rows)
            met = _metrics(yens)
            met.update(
                {
                    "mfe_band": band_id,
                    "mfe_band_label": label,
                    "mfe_left_pct_sum": round(mfe_left_sum, 4),
                    "avg_mfe_left_pct": round(mfe_left_sum / len(rows), 4) if rows else None,
                    "share_of_stop_count": round(len(rows) / len(all_stops), 4) if all_stops else 0.0,
                    "share_of_stop_loss_yen": round(
                        abs(min(sum(yens), 0.0))
                        / abs(
                            min(
                                sum(
                                    float(_float(x.get("pnl_yen_100")) or 0.0) for x in all_stops
                                ),
                                0.0,
                            )
                        ),
                        4,
                    )
                    if all_stops and sum(float(_float(x.get("pnl_yen_100")) or 0.0) for x in all_stops) < 0
                    else 0.0,
                }
            )
            band_summary[band_id] = met
            band_rows.append(met)

            for ug in ("dynamic40", "core10", "other"):
                ug_rows = by_universe_band.get((ug, band_id), [])
                ug_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in ug_rows]
                band_rows.append(
                    {
                        **_metrics(ug_yens),
                        "mfe_band": band_id,
                        "mfe_band_label": label,
                        "universe_group": ug,
                        "segment": f"{band_id}/{ug}",
                    }
                )
            for sk in ("am", "pm"):
                sk_rows = by_am_pm_band.get((sk, band_id), [])
                sk_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in sk_rows]
                band_rows.append(
                    {
                        **_metrics(sk_yens),
                        "mfe_band": band_id,
                        "mfe_band_label": label,
                        "session_kind": sk,
                        "segment": f"{band_id}/{sk}",
                    }
                )

        symbol_rows: list[dict[str, Any]] = []
        for sym, rows in sorted(by_symbol.items(), key=lambda x: sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in x[1])):
            yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in rows]
            bands = defaultdict(int)
            for t in rows:
                bands[str(t.get("mfe_band") or "A")] += 1
            symbol_rows.append(
                {
                    "symbol": sym,
                    **_metrics(yens),
                    "mfe_band_counts": dict(bands),
                    "dominant_mfe_band": max(bands, key=bands.get) if bands else "",
                }
            )

        high_mfe_stops = [
            t
            for t in all_stops
            if (_float(t.get("peak_mfe_pct")) or 0.0) >= HIGH_MFE_THRESHOLD_PCT
        ]
        low_mfe_stops = [
            t
            for t in all_stops
            if (_float(t.get("peak_mfe_pct")) or 0.0) < HIGH_MFE_THRESHOLD_PCT
        ]
        high_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in high_mfe_stops]
        low_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in low_mfe_stops]
        total_stop_pnl = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in all_stops), 2)

        high_mfe_exclude_theoretical = round(-sum(high_yens), 2)
        mfe_left_high = sum(float(_float(t.get("mfe_left_pct")) or 0.0) for t in high_mfe_stops)

        loss_by_band = {
            band_id: round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in by_band.get(band_id, [])), 2)
            for band_id, _, _, _ in MFE_BANDS
        }
        worst_band = min(loss_by_band, key=loss_by_band.get) if loss_by_band else ""

        exit_candidate_bands = [b for b in ("B", "C", "D") if band_summary.get(b, {}).get("count", 0) > 0]
        exit_candidate_count = len(high_mfe_stops)

        return {
            "all_stops": all_stops,
            "production_trade_count": production_total,
            "stop_hit_count": len(all_stops),
            "total_stop_pnl_yen_100": total_stop_pnl,
            "band_summary": band_summary,
            "band_rows": band_rows,
            "symbol_rows": symbol_rows,
            "loss_by_band": loss_by_band,
            "worst_loss_band": worst_band,
            "high_mfe_stop_count": len(high_mfe_stops),
            "high_mfe_stop_pnl_yen_100": round(sum(high_yens), 2),
            "low_mfe_stop_count": len(low_mfe_stops),
            "low_mfe_stop_pnl_yen_100": round(sum(low_yens), 2),
            "exit_improvement_candidate_count": exit_candidate_count,
            "exit_improvement_candidate_bands": exit_candidate_bands,
            "entry_residual_stop_count": len(low_mfe_stops),
            "high_mfe_exclude_theoretical_yen_100": high_mfe_exclude_theoretical,
            "high_mfe_mfe_left_pct_sum": round(mfe_left_high, 4),
            "by_universe": {
                ug: _metrics(
                    [
                        float(_float(t.get("pnl_yen_100")) or 0.0)
                        for t in all_stops
                        if str(t.get("universe_group") or "") == ug
                    ]
                )
                for ug in ("dynamic40", "core10", "other")
            },
            "by_am_pm": {
                sk: _metrics(
                    [
                        float(_float(t.get("pnl_yen_100")) or 0.0)
                        for t in all_stops
                        if str(t.get("session_kind") or "") == sk
                    ]
                )
                for sk in ("am", "pm")
            },
        }

    def finalize_outputs(
        self, *, wall_runtime_sec: float, sessions_discovered: int, sessions_evaluated: int
    ) -> dict[str, Path]:
        agg = self._aggregate()
        paths = self.paths()
        all_stops = agg["all_stops"]
        band_summary = agg["band_summary"]

        dominant_exit_band = max(
            ("B", "C", "D"),
            key=lambda b: abs(agg["loss_by_band"].get(b, 0.0)),
            default="",
        )

        summary = {
            "phase": 366,
            "title": "post_phase364_stophit_reclassification",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "phase355_plus_phase364_production_kept_trades",
            "date_range": {"min_day": MIN_DAY, "max_day": "latest_available"},
            "sessions_discovered": sessions_discovered,
            "sessions_evaluated": sessions_evaluated,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
            "production_trade_count": agg["production_trade_count"],
            "stop_hit_count": agg["stop_hit_count"],
            "stop_hit_share_of_trades": round(
                agg["stop_hit_count"] / agg["production_trade_count"], 4
            )
            if agg["production_trade_count"]
            else 0.0,
            "total_stop_pnl_yen_100": agg["total_stop_pnl_yen_100"],
            "mfe_bands": {bid: label for bid, label, _, _ in MFE_BANDS},
            "by_mfe_band": band_summary,
            "loss_by_mfe_band_yen_100": agg["loss_by_band"],
            "by_universe": agg["by_universe"],
            "by_am_pm": agg["by_am_pm"],
            "high_mfe_threshold_pct": HIGH_MFE_THRESHOLD_PCT,
            "high_mfe_stops": {
                "count": agg["high_mfe_stop_count"],
                "total_pnl_yen_100": agg["high_mfe_stop_pnl_yen_100"],
                "mfe_left_pct_sum": agg["high_mfe_mfe_left_pct_sum"],
            },
            "low_mfe_stops": {
                "count": agg["low_mfe_stop_count"],
                "total_pnl_yen_100": agg["low_mfe_stop_pnl_yen_100"],
            },
            "conclusion": {
                "stop_hit_count": agg["stop_hit_count"],
                "total_stop_loss_yen_100": agg["total_stop_pnl_yen_100"],
                "loss_breakdown_by_band": agg["loss_by_band"],
                "largest_loss_band": agg["worst_loss_band"],
                "exit_improvement_opportunity_band": dominant_exit_band,
                "exit_improvement_candidate_count": agg["exit_improvement_candidate_count"],
                "entry_residual_stop_count": agg["entry_residual_stop_count"],
                "high_mfe_exclude_theoretical_yen_100": agg["high_mfe_exclude_theoretical_yen_100"],
                "exit_improvement_worth_pursuing": agg["high_mfe_stop_count"] > 0
                and abs(agg["high_mfe_stop_pnl_yen_100"]) > abs(agg["low_mfe_stop_pnl_yen_100"]),
                "rationale": (
                    "Band A (peak_mfe<0.3%) reflects residual ENTRY-quality stops; "
                    "bands B/C/D had favorable excursion before hard stop and are EXIT-timing candidates."
                ),
            },
        }

        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if agg["band_rows"]:
            _write_csv(
                paths["by_mfe"],
                agg["band_rows"],
                sorted({k for r in agg["band_rows"] for k in r}),
            )
        if agg["symbol_rows"]:
            _write_csv(
                paths["by_symbol"],
                [
                    {
                        "symbol": r["symbol"],
                        "count": r["count"],
                        "total_pnl_yen_100": r["total_pnl_yen_100"],
                        "avg_pnl_yen_100": r["avg_pnl_yen_100"],
                        "profit_factor": r["profit_factor"],
                        "dominant_mfe_band": r["dominant_mfe_band"],
                    }
                    for r in agg["symbol_rows"]
                ],
                [
                    "symbol",
                    "count",
                    "total_pnl_yen_100",
                    "avg_pnl_yen_100",
                    "profit_factor",
                    "dominant_mfe_band",
                ],
            )
        if all_stops:
            _write_csv(paths["trades"], all_stops, TRADE_FIELDS)
        return paths
