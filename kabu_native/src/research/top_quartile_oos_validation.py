"""
Phase 40: Top-quartile exposure gate — OOS / extended validation (no new EXIT logic).

Applies Phase39 gate per window, pools IS+OOS for sample-size and generalization gates.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.continuation_quality_ranking import (
    MIN_QUALITY_SMALL_PAPER,
    continuation_quality_score,
)
from research.exposure_gate import (
    REJECT_DAILY_LOSS,
    REJECT_LOW_QUALITY,
    REJECT_MAX_CONCURRENT,
    REJECT_RISK_CLUSTER,
    ExposureGateConfig,
    run_exposure_gate_simulation,
)
from research.research_exit_criteria import _as_float, _load_csv, _symbol_concentration_pct
from research.risk_layer_validation import build_risk_layer_report
from research.small_scale_paper_validation import (
    _peak_concurrent,
    _reject_reason_counts,
    _trade_metrics,
    evaluate_move_to_small_paper_candidate,
    load_small_paper_config,
)

MIN_TOP_QUARTILE_QUALITY = 0.55

_TRADE_CSV_FIELDS = (
    "window_id",
    "symbol",
    "trade_date",
    "profile",
    "entry_time",
    "exit_time",
    "pnl_pct",
    "exit_reason",
    "continuation_quality_score",
    "quality_tier",
    "gate_accept",
    "gate_reject_reason",
    "momentum_continuation_score",
    "bearish_accumulation_score",
)

_SUMMARY_CSV_FIELDS = (
    "window_id",
    "window_status",
    "no_data_reason",
    "run_dir",
    "full_book_trade_count",
    "full_book_pf",
    "full_book_avg_pnl_pct",
    "quality_055_pf",
    "quality_055_trade_count",
    "quality_042_pf",
    "quality_042_trade_count",
    "gate_accepted_count",
    "gate_pf",
    "gate_avg_pnl_pct",
    "rejected_low_quality",
    "rejected_risk_cluster",
    "rejected_max_concurrent",
    "rejected_daily_loss_guard",
    "symbols_with_trades",
    "symbols_coverage_ratio",
    "concentration_top_symbol_pct",
    "worst_day_pnl_pct",
    "max_consecutive_losers",
    "peak_concurrent_observed",
    "risk_clustering_acceptable",
)


def _trades_for_profile(
    trades: Sequence[Mapping[str, Any]], profile: str
) -> list[dict[str, Any]]:
    return [dict(t) for t in trades if str(t.get("profile")) == profile]


def _filter_min_quality(
    trades: Sequence[Mapping[str, Any]], *, min_quality: float
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        q = continuation_quality_score(t)
        if q >= min_quality:
            row = dict(t)
            row["continuation_quality_score"] = round(q, 4)
            out.append(row)
    return out


def _worst_day_pnl(trades: Sequence[Mapping[str, Any]]) -> Optional[float]:
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        d = str(t.get("trade_date", ""))[:10]
        if d:
            by_day[d] += _as_float(t.get("pnl_pct")) or 0.0
    return min(by_day.values()) if by_day else None


def _max_consecutive_losers(trades: Sequence[Mapping[str, Any]]) -> int:
    ordered = sorted(
        trades,
        key=lambda t: (str(t.get("trade_date", "")), str(t.get("entry_time", ""))),
    )
    streak = 0
    peak = 0
    for t in ordered:
        if (_as_float(t.get("pnl_pct")) or 0.0) < 0:
            streak += 1
            peak = max(peak, streak)
        else:
            streak = 0
    return peak


def _tag_rows(rows: Sequence[Mapping[str, Any]], window_id: str) -> list[dict[str, Any]]:
    return [{**dict(r), "window_id": window_id} for r in rows]


def _dedupe_trades(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate overlapping windows by symbol + entry/exit times."""
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for t in trades:
        key = (
            str(t.get("symbol")),
            str(t.get("entry_time")),
            str(t.get("exit_time")),
            str(t.get("trade_date")),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(t))
    return out


def analyze_window_top_quartile(
    *,
    window_id: str,
    run_dir: Path,
    gate_cfg: ExposureGateConfig,
    universe_symbol_count: int,
) -> dict[str, Any]:
    trades_path = run_dir / "trades_by_profile.csv"
    if not trades_path.is_file():
        return {
            "window_id": window_id,
            "run_dir": str(run_dir),
            "window_status": "no_data",
            "no_data_reason": "trades_by_profile.csv missing",
            "skipped": True,
        }

    trades = _load_csv(trades_path)
    focus = _trades_for_profile(trades, gate_cfg.profile)
    if not focus:
        return {
            "window_id": window_id,
            "run_dir": str(run_dir),
            "window_status": "no_data",
            "no_data_reason": "zero_trades_for_focus_profile",
            "skipped": True,
            "full_book": {"trade_count": 0},
        }

    full_m = _trade_metrics(focus)
    q055 = _filter_min_quality(focus, min_quality=MIN_TOP_QUARTILE_QUALITY)
    q042 = _filter_min_quality(focus, min_quality=MIN_QUALITY_SMALL_PAPER)
    m055 = _trade_metrics(q055)
    m042 = _trade_metrics(q042)

    accepted, rejects = run_exposure_gate_simulation(trades, gate_cfg)
    acc_m = _trade_metrics(accepted)
    reject_counts = _reject_reason_counts(rejects)

    sym_rows = [
        {"profile": gate_cfg.profile, "symbol": sym, "entry_count": cnt}
        for sym, cnt in Counter(str(t.get("symbol")) for t in accepted).items()
    ]
    conc = _symbol_concentration_pct(sym_rows, gate_cfg.profile)
    symbols_traded = len({str(t.get("symbol")) for t in accepted})
    cov = symbols_traded / universe_symbol_count if universe_symbol_count > 0 else None
    risk = build_risk_layer_report(accepted, focus_profile=gate_cfg.profile)

    return {
        "window_id": window_id,
        "run_dir": str(run_dir),
        "window_status": "valid_window",
        "skipped": False,
        "full_book": full_m,
        "quality_ge_055": m055,
        "quality_ge_042": m042,
        "gate_accepted": acc_m,
        "gate_summary": {
            "eligible_attempts": len(focus),
            "accepted_count": len(accepted),
            "rejected_count": len(rejects),
            "rejected_low_quality": reject_counts.get(REJECT_LOW_QUALITY, 0),
            "rejected_risk_cluster_block": reject_counts.get(REJECT_RISK_CLUSTER, 0),
            "rejected_max_concurrent": reject_counts.get(REJECT_MAX_CONCURRENT, 0),
            "rejected_daily_loss_guard": reject_counts.get(REJECT_DAILY_LOSS, 0),
            "reject_reason_counts": reject_counts,
            "peak_concurrent_observed": _peak_concurrent(accepted),
        },
        "symbols_with_trades": symbols_traded,
        "symbols_coverage_ratio": cov,
        "concentration_top_symbol_pct": conc,
        "worst_day_pnl_pct": _worst_day_pnl(accepted),
        "max_consecutive_losers": _max_consecutive_losers(accepted),
        "risk_layer": {
            "risk_clustering_acceptable": risk.get("risk_clustering_acceptable"),
            "max_consecutive_losers": risk.get("max_consecutive_losers"),
            "worst_day_pnl_pct": risk.get("worst_day_pnl_pct"),
            "diagnosis": risk.get("diagnosis"),
        },
        "_accepted_rows": accepted,
        "_reject_rows": rejects,
    }


def _oos_deterioration_pct(
    is_pf: Optional[float], oos_pf: Optional[float]
) -> Optional[float]:
    if is_pf is None or oos_pf is None:
        return None
    if is_pf <= 1e-9:
        return None
    return ((is_pf - oos_pf) / abs(is_pf)) * 100.0


def evaluate_phase40_small_paper_candidate(
    *,
    combined_accepted_metrics: Mapping[str, Any],
    combined_symbols: int,
    universe_symbol_count: int,
    concentration_top_symbol_pct: Optional[float],
    peak_concurrent: int,
    combined_risk: Mapping[str, Any],
    candidate_gates: Mapping[str, Any],
    max_concurrent_limit: int,
    oos_deterioration_pct: Optional[float],
) -> dict[str, Any]:
    combined_min = int(candidate_gates.get("combined_min_trades", 100))
    base = evaluate_move_to_small_paper_candidate(
        accepted_metrics=combined_accepted_metrics,
        symbols_with_trades=combined_symbols,
        universe_symbol_count=universe_symbol_count,
        concentration_top_symbol_pct=concentration_top_symbol_pct,
        peak_concurrent=peak_concurrent,
        risk_layer=combined_risk,
        candidate_gates={**candidate_gates, "min_trades": combined_min},
        max_concurrent_limit=max_concurrent_limit,
    )
    max_det = float(candidate_gates.get("oos_deterioration_max_pct", 20.0))
    oos_ok = oos_deterioration_pct is not None and oos_deterioration_pct <= max_det
    gates = dict(base.get("gates") or {})
    gates["oos_deterioration_ok"] = oos_ok
    gates["combined_is_oos_min_trades"] = gates.get("min_trades", False)
    passed = all(gates.values())
    return {
        **base,
        "move_to_small_paper_candidate": passed,
        "gates": gates,
        "oos_deterioration_pct": oos_deterioration_pct,
        "oos_deterioration_max_pct": max_det,
        "sample_size_note": (
            "Phase40 uses combined IS+OOS gate-accepted trades for min_trades; "
            "per-window IS-only sample may remain small."
        ),
    }


def _window_summary_row(w: Mapping[str, Any]) -> dict[str, Any]:
    if w.get("skipped") or w.get("window_status") == "no_data":
        return {
            "window_id": w.get("window_id"),
            "run_dir": w.get("run_dir", ""),
            "window_status": w.get("window_status", "no_data"),
            "no_data_reason": w.get("no_data_reason") or w.get("reason"),
        }
    full = w.get("full_book") or {}
    g055 = w.get("quality_ge_055") or {}
    g042 = w.get("quality_ge_042") or {}
    gate = w.get("gate_accepted") or {}
    gs = w.get("gate_summary") or {}
    risk = w.get("risk_layer") or {}
    return {
        "window_id": w.get("window_id"),
        "run_dir": w.get("run_dir"),
        "full_book_trade_count": full.get("trade_count"),
        "full_book_pf": full.get("profit_factor"),
        "full_book_avg_pnl_pct": full.get("avg_pnl_pct"),
        "quality_055_pf": g055.get("profit_factor"),
        "quality_055_trade_count": g055.get("trade_count"),
        "quality_042_pf": g042.get("profit_factor"),
        "quality_042_trade_count": g042.get("trade_count"),
        "gate_accepted_count": gate.get("trade_count"),
        "gate_pf": gate.get("profit_factor"),
        "gate_avg_pnl_pct": gate.get("avg_pnl_pct"),
        "rejected_low_quality": gs.get("rejected_low_quality"),
        "rejected_risk_cluster": gs.get("rejected_risk_cluster_block"),
        "rejected_max_concurrent": gs.get("rejected_max_concurrent"),
        "rejected_daily_loss_guard": gs.get("rejected_daily_loss_guard"),
        "symbols_with_trades": w.get("symbols_with_trades"),
        "symbols_coverage_ratio": w.get("symbols_coverage_ratio"),
        "concentration_top_symbol_pct": w.get("concentration_top_symbol_pct"),
        "worst_day_pnl_pct": w.get("worst_day_pnl_pct"),
        "max_consecutive_losers": w.get("max_consecutive_losers"),
        "peak_concurrent_observed": gs.get("peak_concurrent_observed"),
        "risk_clustering_acceptable": risk.get("risk_clustering_acceptable"),
    }


def resolve_oos_windows(
    *,
    reference_run_dir: Path,
    extended_oos_json: Optional[Path] = None,
    latest_oos_json: Optional[Path] = None,
    window_runs: Optional[Sequence[Mapping[str, Any]]] = None,
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = [
        {
            "window_id": "in_sample",
            "run_dir": reference_run_dir.resolve(),
            "window_status": "valid_window",
        }
    ]

    def _merge_spec(w: Mapping[str, Any]) -> None:
        wid = str(w.get("window_id") or w.get("id") or "")
        if not wid:
            return
        status = str(w.get("status") or w.get("window_status") or "valid_window")
        rd = w.get("run_dir")
        entry: dict[str, Any] = {
            "window_id": wid,
            "window_status": status,
            "start": w.get("start"),
            "end": w.get("end"),
        }
        if status == "no_data":
            entry["no_data_reason"] = w.get("reason") or w.get("no_data_reason")
        if rd:
            entry["run_dir"] = Path(str(rd))
        existing = {x["window_id"] for x in windows}
        if wid in existing:
            for i, x in enumerate(windows):
                if x["window_id"] == wid:
                    windows[i] = {**x, **entry}
                    break
        else:
            windows.append(entry)

    if latest_oos_json and latest_oos_json.is_file():
        data = json.loads(latest_oos_json.read_text(encoding="utf-8"))
        for w in data.get("windows") or []:
            _merge_spec(w)
    elif extended_oos_json and extended_oos_json.is_file():
        data = json.loads(extended_oos_json.read_text(encoding="utf-8"))
        for w in data.get("windows") or []:
            _merge_spec(
                {
                    "window_id": w.get("window_id"),
                    "run_dir": w.get("run_dir"),
                    "status": "valid_window" if w.get("run_dir") else "no_data",
                    "reason": "no_run_dir" if not w.get("run_dir") else None,
                }
            )

    if window_runs:
        for spec in window_runs:
            _merge_spec(
                {
                    "window_id": spec.get("id") or spec.get("window_id"),
                    "run_dir": spec.get("run_dir"),
                    "status": spec.get("status", "valid_window"),
                    "reason": spec.get("reason"),
                    "start": spec.get("start"),
                    "end": spec.get("end"),
                }
            )
    return windows


def build_top_quartile_oos_validation(
    *,
    windows: Sequence[Mapping[str, Any]],
    gate_cfg: ExposureGateConfig,
    candidate_gates: Mapping[str, Any],
    universe_symbol_count: int,
) -> dict[str, Any]:
    per_window: list[dict[str, Any]] = []
    all_accepted: list[dict[str, Any]] = []
    all_rejects: list[dict[str, Any]] = []
    oos_accepted: list[dict[str, Any]] = []

    for spec in windows:
        wid = str(spec["window_id"])
        status = str(spec.get("window_status") or "valid_window")
        if status == "no_data" or not spec.get("run_dir"):
            per_window.append(
                {
                    "window_id": wid,
                    "window_status": "no_data",
                    "no_data_reason": spec.get("no_data_reason")
                    or spec.get("reason")
                    or "no_run_dir",
                    "start": spec.get("start"),
                    "end": spec.get("end"),
                    "skipped": True,
                }
            )
            continue
        run_dir = Path(spec["run_dir"])
        analysis = analyze_window_top_quartile(
            window_id=wid,
            run_dir=run_dir,
            gate_cfg=gate_cfg,
            universe_symbol_count=universe_symbol_count,
        )
        acc = analysis.pop("_accepted_rows", [])
        rej = analysis.pop("_reject_rows", [])
        per_window.append(analysis)
        all_accepted.extend(_tag_rows(acc, wid))
        all_rejects.extend(_tag_rows(rej, wid))
        if wid != "in_sample":
            oos_accepted.extend(acc)

    all_accepted_deduped = _dedupe_trades(all_accepted)
    oos_accepted_deduped = _dedupe_trades(oos_accepted)
    combined_m = _trade_metrics(all_accepted_deduped)
    oos_only_m = _trade_metrics(oos_accepted_deduped)
    is_row = next((w for w in per_window if w.get("window_id") == "in_sample"), {})
    is_gate_pf = _as_float((is_row.get("gate_accepted") or {}).get("profit_factor"))
    oos_gate_pf = oos_only_m.get("profit_factor")
    det_pct = _oos_deterioration_pct(
        _as_float(is_gate_pf) if is_gate_pf is not None else None,
        _as_float(oos_gate_pf) if oos_gate_pf is not None else None,
    )

    sym_rows = [
        {"profile": gate_cfg.profile, "symbol": sym, "entry_count": cnt}
        for sym, cnt in Counter(str(t.get("symbol")) for t in all_accepted_deduped).items()
    ]
    conc = _symbol_concentration_pct(sym_rows, gate_cfg.profile)
    symbols_traded = len({str(t.get("symbol")) for t in all_accepted_deduped})
    peak = _peak_concurrent(all_accepted_deduped)
    combined_risk = build_risk_layer_report(
        all_accepted_deduped, focus_profile=gate_cfg.profile
    )

    candidate = evaluate_phase40_small_paper_candidate(
        combined_accepted_metrics=combined_m,
        combined_symbols=symbols_traded,
        universe_symbol_count=universe_symbol_count,
        concentration_top_symbol_pct=conc,
        peak_concurrent=peak,
        combined_risk=combined_risk,
        candidate_gates=candidate_gates,
        max_concurrent_limit=gate_cfg.max_concurrent_positions,
        oos_deterioration_pct=det_pct,
    )

    summary_rows = [_window_summary_row(w) for w in per_window]

    return {
        "phase": 40,
        "mode": "top_quartile_oos_validation_not_live",
        "profile": gate_cfg.profile,
        "universe_symbol_count": universe_symbol_count,
        "windows_analyzed": len(per_window),
        "per_window": per_window,
        "combined_is_oos": {
            "gate_accepted": combined_m,
            "gate_accepted_raw_count": len(all_accepted),
            "gate_accepted_deduped_count": len(all_accepted_deduped),
            "symbols_with_trades": symbols_traded,
            "symbols_coverage_ratio": (
                symbols_traded / universe_symbol_count
                if universe_symbol_count > 0
                else None
            ),
            "concentration_top_symbol_pct": conc,
            "peak_concurrent_observed": peak,
            "risk_layer": combined_risk,
        },
        "oos_only_gate_accepted": oos_only_m,
        "oos_deterioration_vs_in_sample_gate_pf_pct": det_pct,
        "candidate_evaluation": candidate,
        "summary_table": summary_rows,
        "rationale": (
            "Phase39 IS gate PF improved but n=28; Phase40 pools OOS windows to test "
            "generalization and combined sample size without new EXIT logic."
        ),
    }


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def run_phase40_top_quartile_oos_validation(
    *,
    reference_run_dir: Path,
    output_dir: Path,
    config_path: Path,
    universe_symbol_count: int,
    extended_oos_json: Optional[Path] = None,
    latest_oos_json: Optional[Path] = None,
    window_runs: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Path:
    gate_cfg, candidate_gates = load_small_paper_config(config_path)
    windows = resolve_oos_windows(
        reference_run_dir=reference_run_dir,
        extended_oos_json=extended_oos_json,
        latest_oos_json=latest_oos_json,
        window_runs=window_runs,
    )
    report = build_top_quartile_oos_validation(
        windows=windows,
        gate_cfg=gate_cfg,
        candidate_gates=candidate_gates,
        universe_symbol_count=universe_symbol_count,
    )

    accepted_rows: list[dict[str, Any]] = []
    reject_rows: list[dict[str, Any]] = []
    for spec in windows:
        wid = str(spec["window_id"])
        if spec.get("window_status") == "no_data" or not spec.get("run_dir"):
            continue
        run_dir = Path(spec["run_dir"])
        trades = _load_csv(run_dir / "trades_by_profile.csv")
        acc, rej = run_exposure_gate_simulation(trades, gate_cfg)
        accepted_rows.extend(_tag_rows(acc, wid))
        reject_rows.extend(_tag_rows(rej, wid))

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "top_quartile_oos_validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(output_dir / "top_quartile_oos_trades.csv", _TRADE_CSV_FIELDS, accepted_rows)
    _write_csv(output_dir / "top_quartile_oos_rejects.csv", _TRADE_CSV_FIELDS, reject_rows)
    _write_csv(
        output_dir / "top_quartile_oos_summary.csv",
        _SUMMARY_CSV_FIELDS,
        report.get("summary_table") or [],
    )
    return output_dir
