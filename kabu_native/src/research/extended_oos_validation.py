"""
Phase 38: Extended OOS validation — multi-window drift (no new EXIT logic).
"""

from __future__ import annotations

import statistics
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.entry_v2 import MOMENTUM_V13_COMBINED_REFERENCE
from research.phase37_validation import (
    VALIDATION_PROFILES,
    _latest_trading_date,
    _profile_summary_metrics,
    _trading_days_between,
)
from research.research_exit_criteria import (
    _as_float,
    _load_csv,
    _market_structure_consistency,
    _symbol_concentration_pct,
    _trade_metrics_from_rows,
)

EXTENDED_OOS_WINDOWS: tuple[dict[str, Any], ...] = (
    {"id": "oos_march", "start": "2026-03-01", "end": "2026-03-31"},
    {"id": "oos_april", "start": "2026-04-01", "end": "2026-04-30"},
    {"id": "oos_may_late", "start": "2026-05-16", "end": None},
    {"id": "oos_latest", "start": None, "end": None, "latest_days": 10},
)

DRIFT_REFERENCE_ID = "in_sample"


def _window_metrics(
    run_dir: Path,
    profile: str,
    *,
    universe_symbol_count: Optional[int] = None,
) -> dict[str, Any]:
    base = _profile_summary_metrics(
        run_dir, profile, universe_symbol_count=universe_symbol_count
    )
    trades = _load_csv(run_dir / "trades_by_profile.csv")
    sym_rows = _load_csv(run_dir / "symbol_summary.csv")
    msc = _market_structure_consistency(trades, profile)
    tm = _trade_metrics_from_rows(trades, profile)
    conc = base.get("concentration_top_symbol_pct")
    if conc is None and sym_rows:
        conc = _symbol_concentration_pct(sym_rows, profile)
    days = {str(t.get("trade_date", ""))[:10] for t in trades if str(t.get("profile")) == profile}
    days.discard("")
    return {
        **base,
        "profit_factor": base.get("profit_factor"),
        "avg_pnl_pct": base.get("avg_pnl_pct"),
        "trade_count": tm.get("trade_count") or base.get("entry_count"),
        "trades_per_day": (
            (tm.get("trade_count") or 0) / len(days) if days else None
        ),
        "continuation_consistency": msc.get("momentum_continuation_consistency"),
        "persistence_consistency": msc.get("continuation_persistence_consistency"),
        "false_hold_rate": msc.get("continuation_false_hold_rate"),
        "concentration_top_symbol_pct": conc,
    }


def _drift_pct(ref: Optional[float], cur: Optional[float]) -> Optional[float]:
    if ref is None or cur is None:
        return None
    if abs(ref) < 1e-9:
        return None
    return ((cur - ref) / abs(ref)) * 100.0


def build_extended_oos_validation(
    *,
    reference_run_dir: Path,
    window_runs: Sequence[Mapping[str, Any]],
    focus_profile: str = MOMENTUM_V13_COMBINED_REFERENCE,
    universe_symbol_count: Optional[int] = None,
    data_roots: Optional[Sequence[Path]] = None,
) -> dict[str, Any]:
    ref_m = _window_metrics(
        reference_run_dir, focus_profile, universe_symbol_count=universe_symbol_count
    )
    windows_out: list[dict[str, Any]] = []
    drift_series: dict[str, list[Optional[float]]] = {
        "pf": [],
        "avg_pnl": [],
        "continuation_consistency": [],
        "false_hold": [],
        "concentration": [],
        "trade_frequency": [],
    }

    for spec in window_runs:
        run_dir = Path(str(spec["run_dir"]))
        wm = _window_metrics(run_dir, focus_profile, universe_symbol_count=universe_symbol_count)
        drift = {
            "pf_drift_pct": _drift_pct(_as_float(ref_m.get("profit_factor")), _as_float(wm.get("profit_factor"))),
            "avg_pnl_drift_pct": _drift_pct(_as_float(ref_m.get("avg_pnl_pct")), _as_float(wm.get("avg_pnl_pct"))),
            "continuation_consistency_drift_pct": _drift_pct(
                _as_float(ref_m.get("continuation_consistency")),
                _as_float(wm.get("continuation_consistency")),
            ),
            "false_hold_drift_pct": _drift_pct(
                _as_float(ref_m.get("false_hold_rate")),
                _as_float(wm.get("false_hold_rate")),
            ),
            "concentration_drift_pct": _drift_pct(
                _as_float(ref_m.get("concentration_top_symbol_pct")),
                _as_float(wm.get("concentration_top_symbol_pct")),
            ),
            "trade_frequency_drift_pct": _drift_pct(
                _as_float(ref_m.get("trades_per_day")),
                _as_float(wm.get("trades_per_day")),
            ),
        }
        for k, v in drift.items():
            key = k.replace("_drift_pct", "").replace("_drift", "")
            if "pf" in k:
                drift_series["pf"].append(v)
            elif "avg_pnl" in k:
                drift_series["avg_pnl"].append(v)
            elif "continuation" in k:
                drift_series["continuation_consistency"].append(v)
            elif "false_hold" in k:
                drift_series["false_hold"].append(v)
            elif "concentration" in k:
                drift_series["concentration"].append(v)
            elif "trade_frequency" in k:
                drift_series["trade_frequency"].append(v)

        windows_out.append(
            {
                "window_id": spec.get("id"),
                "start": spec.get("start"),
                "end": spec.get("end"),
                "run_dir": str(run_dir),
                "metrics": wm,
                "drift_vs_reference": drift,
            }
        )

    def _stable(vals: list[Optional[float]], *, max_abs: float = 25.0) -> bool:
        present = [v for v in vals if v is not None]
        if len(present) < 2:
            return True
        return all(abs(v) <= max_abs for v in present)

    aggregate = {
        "pf_drift_stable": _stable(drift_series["pf"], max_abs=30.0),
        "avg_pnl_drift_stable": _stable(drift_series["avg_pnl"], max_abs=40.0),
        "continuation_consistency_stable": _stable(
            drift_series["continuation_consistency"], max_abs=35.0
        ),
        "false_hold_drift_stable": _stable(drift_series["false_hold"], max_abs=35.0),
        "concentration_drift_stable": _stable(drift_series["concentration"], max_abs=30.0),
        "trade_frequency_drift_stable": _stable(drift_series["trade_frequency"], max_abs=40.0),
    }

    return {
        "phase": 38,
        "reference_window": {
            "id": DRIFT_REFERENCE_ID,
            "run_dir": str(reference_run_dir),
            "metrics": ref_m,
        },
        "focus_profile": focus_profile,
        "profiles_available": list(VALIDATION_PROFILES),
        "windows": windows_out,
        "drift_aggregate": aggregate,
        "drift_series": drift_series,
    }


def resolve_extended_windows(
    data_roots: Sequence[Path],
    *,
    include_no_data: bool = False,
) -> list[dict[str, Any]]:
    """
    Resolve OOS window date ranges (Phase41: no invalid start>end).

    By default returns only valid_window entries (backward compatible for replay).
    Set include_no_data=True to include explicit no_data windows.
    """
    from research.oos_data_availability import resolve_oos_windows_with_status

    resolved = resolve_oos_windows_with_status(data_roots)
    out: list[dict[str, Any]] = []
    for spec in resolved:
        if spec["status"] != "valid_window" and not include_no_data:
            continue
        row = {
            "id": spec["window_id"],
            "start": spec.get("start"),
            "end": spec.get("end"),
            "run_dir": spec.get("run_dir"),
            "status": spec["status"],
        }
        if spec["status"] == "no_data":
            row["reason"] = spec.get("reason")
        out.append(row)
    return out
