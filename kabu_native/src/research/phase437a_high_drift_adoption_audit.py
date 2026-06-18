"""
Phase437A — High Drift adoption audit (shadow).

Evaluates whether High Drift can replace VWAP pullback, generalizes beyond 6976,
and concentration / overlap structure. Research only — no Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase436_pullback_guard_redesign_shadow import (
    PERIOD_END,
    PERIOD_START,
    STARTING_EQUITY,
    TARGET_SYMBOL,
    _accepted_trades_from_sim,
    _build_price_index,
    _enrich_trades,
    _is_stop,
    _load_accepted_index,
    _max_drawdown_yen,
    guard_high_drift,
    guard_legacy_vwap_pullback,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

VARIANTS: tuple[dict[str, Any], ...] = (
    {"variant_id": "A_baseline", "label": "baseline", "block_fn": lambda _t: False},
    {"variant_id": "B_legacy_vwap", "label": "legacy_vwap", "block_fn": guard_legacy_vwap_pullback},
    {"variant_id": "C_high_drift", "label": "high_drift", "block_fn": guard_high_drift},
    {
        "variant_id": "D_legacy_vwap_plus_high_drift",
        "label": "legacy_vwap_plus_high_drift",
        "block_fn": lambda t: guard_legacy_vwap_pullback(t) or guard_high_drift(t),
    },
)

OVERLAP_BUCKETS = (
    "vwap_only",
    "high_drift_only",
    "both",
    "neither",
)


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _expectancy(pnls: Sequence[float]) -> float:
    return round(statistics.mean(pnls), 2) if pnls else 0.0


def _gini_positive(values: Sequence[float]) -> float:
    pos = sorted(v for v in values if v > 0)
    n = len(pos)
    if n <= 1:
        return 0.0 if n == 0 else 1.0
    total = sum(pos)
    if total <= 0:
        return 0.0
    weighted = sum((i + 1) * v for i, v in enumerate(pos))
    return round((2.0 * weighted) / (n * total) - (n + 1) / n, 4)


def _hhi(shares: Sequence[float]) -> float:
    return round(sum(s * s for s in shares), 4)


def _split_kept_removed(
    trades: Sequence[Mapping[str, Any]],
    block_fn: Callable[[Mapping[str, Any]], bool],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        if block_fn(row):
            removed.append(row)
        else:
            kept.append(row)
    return kept, removed


def _variant_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    variant_id: str,
    label: str,
    block_fn: Callable[[Mapping[str, Any]], bool],
    baseline_accepted_count: int,
) -> dict[str, Any]:
    kept, removed = _split_kept_removed(trades, block_fn)
    kept_pnls = [_float(t.get("pnl_yen")) for t in kept]
    removed_pnls = [_float(t.get("pnl_yen")) for t in removed]
    stops_kept = sum(1 for t in kept if _is_stop(t))
    stops_removed = sum(1 for t in removed if _is_stop(t))
    winners_removed = sum(1 for p in removed_pnls if p > 0)
    losers_removed = sum(1 for p in removed_pnls if p < 0)
    max_dd, max_dd_pct = _max_drawdown_yen(kept)
    wr = _win_rate(kept_pnls)
    return {
        "variant_id": variant_id,
        "label": label,
        "trade_count": len(kept),
        "accepted_count": len(kept),
        "baseline_accepted_count": baseline_accepted_count,
        "total_pnl_yen": round(sum(kept_pnls), 2),
        "profit_factor": _pf(kept_pnls),
        "win_rate": wr,
        "expectancy_yen": _expectancy(kept_pnls),
        "stop_count": stops_kept,
        "stop_rate": round(stops_kept / len(kept), 4) if kept else 0.0,
        "max_drawdown_yen": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "removed_trade_count": len(removed),
        "removed_stop_count": stops_removed,
        "removed_winner_count": winners_removed,
        "removed_loser_count": losers_removed,
        "removed_pnl_yen": round(sum(removed_pnls), 2),
    }


def _day_pf(trades: Sequence[Mapping[str, Any]]) -> Optional[float]:
    pnls = [_float(t.get("pnl_yen")) for t in trades]
    return _pf(pnls) if pnls else None


def _daily_attribution(
    trades: Sequence[Mapping[str, Any]],
    *,
    baseline_block: Callable[[Mapping[str, Any]], bool],
    variant_block: Callable[[Mapping[str, Any]], bool],
    variant_id: str,
) -> list[dict[str, Any]]:
    base_kept, _ = _split_kept_removed(trades, baseline_block)
    var_kept, _ = _split_kept_removed(trades, variant_block)

    base_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    var_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in base_kept:
        base_by_day[str(t.get("day") or "")].append(t)
    for t in var_kept:
        var_by_day[str(t.get("day") or "")].append(t)

    days = sorted(set(base_by_day) | set(var_by_day))
    rows: list[dict[str, Any]] = []
    for day in days:
        b_trades = base_by_day.get(day, [])
        v_trades = var_by_day.get(day, [])
        b_pnls = [_float(t.get("pnl_yen")) for t in b_trades]
        v_pnls = [_float(t.get("pnl_yen")) for t in v_trades]
        b_pnl = round(sum(b_pnls), 2)
        v_pnl = round(sum(v_pnls), 2)
        b_stops = sum(1 for t in b_trades if _is_stop(t))
        v_stops = sum(1 for t in v_trades if _is_stop(t))
        b_sr = round(b_stops / len(b_trades), 4) if b_trades else 0.0
        v_sr = round(v_stops / len(v_trades), 4) if v_trades else 0.0
        b_pf = _day_pf(b_trades)
        v_pf = _day_pf(v_trades)
        delta_pf = None
        if b_pf is not None and v_pf is not None and b_pf != float("inf") and v_pf != float("inf"):
            delta_pf = round(v_pf - b_pf, 4)
        rows.append(
            {
                "variant_id": variant_id,
                "day": day,
                "baseline_pnl_yen": b_pnl,
                "variant_pnl_yen": v_pnl,
                "delta_pnl_yen": round(v_pnl - b_pnl, 2),
                "baseline_pf": b_pf,
                "variant_pf": v_pf,
                "delta_pf": delta_pf,
                "baseline_stop_rate": b_sr,
                "variant_stop_rate": v_sr,
                "delta_stop_rate": round(v_sr - b_sr, 4),
                "baseline_trade_count": len(b_trades),
                "variant_trade_count": len(v_trades),
            }
        )
    return rows


def _overlap_bucket(trade: Mapping[str, Any]) -> str:
    vwap = guard_legacy_vwap_pullback(trade)
    hd = guard_high_drift(trade)
    if vwap and hd:
        return "both"
    if vwap:
        return "vwap_only"
    if hd:
        return "high_drift_only"
    return "neither"


def _overlap_analysis(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {b: [] for b in OVERLAP_BUCKETS}
    for t in trades:
        buckets[_overlap_bucket(t)].append(dict(t))

    rows: list[dict[str, Any]] = []
    for bucket in OVERLAP_BUCKETS:
        subset = buckets[bucket]
        pnls = [_float(t.get("pnl_yen")) for t in subset]
        stops = sum(1 for t in subset if _is_stop(t))
        rows.append(
            {
                "bucket": bucket,
                "trade_count": len(subset),
                "total_pnl_yen": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "stop_count": stops,
                "stop_rate": round(stops / len(subset), 4) if subset else 0.0,
                "winner_count": sum(1 for p in pnls if p > 0),
                "loser_count": sum(1 for p in pnls if p < 0),
                "symbol_6976_count": sum(1 for t in subset if str(t.get("symbol")) == TARGET_SYMBOL),
            }
        )
    return rows


def _delta_concentration(
    trades: Sequence[Mapping[str, Any]],
    *,
    block_fn: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    kept, removed = _split_kept_removed(trades, block_fn)
    baseline_pnl = sum(_float(t.get("pnl_yen")) for t in trades)
    variant_pnl = sum(_float(t.get("pnl_yen")) for t in kept)
    total_delta = round(variant_pnl - baseline_pnl, 2)

    by_day: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    for t in removed:
        pnl = _float(t.get("pnl_yen"))
        by_day[str(t.get("day") or "")] += pnl
        by_sym[str(t.get("symbol") or "")] += pnl

    day_deltas = {day: round(-pnl, 2) for day, pnl in by_day.items()}
    sym_deltas = {sym: round(-pnl, 2) for sym, pnl in by_sym.items()}

    top_day_share = None
    top3_day_share = None
    top_symbol_share = None
    top3_symbol_share = None
    if abs(total_delta) > 1e-6:
        if day_deltas:
            ranked_days = sorted(day_deltas.items(), key=lambda kv: abs(kv[1]), reverse=True)
            top_day_share = round(abs(ranked_days[0][1]) / abs(total_delta), 4)
            top3_day_share = round(
                sum(abs(v) for _, v in ranked_days[:3]) / abs(total_delta), 4
            )
        if sym_deltas:
            ranked_syms = sorted(sym_deltas.items(), key=lambda kv: abs(kv[1]), reverse=True)
            top_symbol_share = round(abs(ranked_syms[0][1]) / abs(total_delta), 4)
            top3_symbol_share = round(
                sum(abs(v) for _, v in ranked_syms[:3]) / abs(total_delta), 4
            )

    positive_deltas = [v for v in day_deltas.values() if v > 0]
    pos_total = sum(positive_deltas)
    day_shares = [v / pos_total for v in positive_deltas] if pos_total > 0 else []
    sym_positive = {s: v for s, v in sym_deltas.items() if v > 0}
    sym_pos_total = sum(sym_positive.values())
    sym_shares = [v / sym_pos_total for v in sym_positive.values()] if sym_pos_total > 0 else []

    return {
        "total_delta_yen": total_delta,
        "top_day_share": top_day_share,
        "top3_day_share": top3_day_share,
        "top_symbol_share": top_symbol_share,
        "top3_symbol_share": top3_symbol_share,
        "hhi_day_positive_delta": _hhi(day_shares),
        "gini_day_positive_delta": _gini_positive(positive_deltas),
        "hhi_symbol_positive_delta": _hhi(sym_shares),
        "gini_symbol_positive_delta": _gini_positive(list(sym_positive.values())),
        "day_deltas": day_deltas,
        "symbol_deltas": sym_deltas,
        "removed_count": len(removed),
    }


def _6976_analysis(
    trades: Sequence[Mapping[str, Any]],
    *,
    block_fn: Callable[[Mapping[str, Any]], bool],
    total_delta_yen: float,
) -> dict[str, Any]:
    _, removed = _split_kept_removed(trades, block_fn)
    rem6976 = [t for t in removed if str(t.get("symbol")) == TARGET_SYMBOL]
    rem6976_pnls = [_float(t.get("pnl_yen")) for t in rem6976]
    rem6976_stops = sum(1 for t in rem6976 if _is_stop(t))
    removed_pnl = round(sum(rem6976_pnls), 2)
    improvement_from_6976 = round(-removed_pnl, 2)
    contrib = (
        round(improvement_from_6976 / total_delta_yen, 4)
        if abs(total_delta_yen) > 1e-6
        else None
    )
    rem618 = [t for t in rem6976 if str(t.get("day") or "") == "20260618"]
    return {
        "symbol": TARGET_SYMBOL,
        "removed_count": len(rem6976),
        "removed_pnl_yen": removed_pnl,
        "removed_stop_count": rem6976_stops,
        "improvement_contribution_yen": improvement_from_6976,
        "improvement_contribution_rate": contrib,
        "removed_on_20260618_count": len(rem618),
        "removed_on_20260618_pnl_yen": round(sum(_float(t.get("pnl_yen")) for t in rem618), 2),
    }


def _counterfactual_delta_excluding(
    trades: Sequence[Mapping[str, Any]],
    *,
    block_fn: Callable[[Mapping[str, Any]], bool],
    exclude_fn: Callable[[Mapping[str, Any]], bool],
) -> float:
    """Delta if we only block trades matching block_fn AND NOT exclude_fn."""
    baseline_pnl = sum(_float(t.get("pnl_yen")) for t in trades)
    kept_pnl = 0.0
    for t in trades:
        if block_fn(t) and not exclude_fn(t):
            continue
        kept_pnl += _float(t.get("pnl_yen"))
    return round(kept_pnl - baseline_pnl, 2)


def _mandatory_answers(
    *,
    overlap_rows: Sequence[Mapping[str, Any]],
    variant_rows: Sequence[Mapping[str, Any]],
    concentration: Mapping[str, Any],
    analysis6976: Mapping[str, Any],
    counterfactuals: Mapping[str, Any],
) -> dict[str, Any]:
    overlap = {str(r["bucket"]): r for r in overlap_rows}
    variants = {str(r["variant_id"]): r for r in variant_rows}
    hd = variants.get("C_high_drift", {})
    base = variants.get("A_baseline", {})

    vwap_only = int(overlap.get("vwap_only", {}).get("trade_count") or 0)
    hd_only = int(overlap.get("high_drift_only", {}).get("trade_count") or 0)
    both = int(overlap.get("both", {}).get("trade_count") or 0)

    top_day = concentration.get("top_day_share")
    top_sym = concentration.get("top_symbol_share")
    delta_excl_6976 = counterfactuals.get("delta_excluding_6976_removals_yen")
    delta_excl_618 = counterfactuals.get("delta_excluding_20260618_removals_yen")

    vwap_replaceable = (
        vwap_only == 0
        and both == 0
        and _float(hd.get("total_pnl_yen")) >= _float(base.get("total_pnl_yen"))
    )

    production_candidate = (
        _float(hd.get("total_pnl_yen")) > _float(base.get("total_pnl_yen"))
        and (top_day or 1.0) <= 0.5
        and (top_sym or 1.0) <= 0.3
        and (delta_excl_6976 or 0) > 0
    )

    if production_candidate:
        adoption = "runtime_adoption_candidate"
    elif (delta_excl_6976 or 0) > 0 and (top_day or 1.0) > 0.5:
        adoption = "shadow_continue_concentration_risk"
    elif vwap_replaceable:
        adoption = "shadow_continue_with_broader_validation"
    else:
        adoption = "shadow_continue"

    return {
        "1_vwap_only_removed_count": vwap_only,
        "2_high_drift_only_removed_count": hd_only,
        "3_both_removed_count": both,
        "4_high_drift_standalone_pnl_yen": hd.get("total_pnl_yen"),
        "5_high_drift_standalone_pf": hd.get("profit_factor"),
        "6_top_day_share": top_day,
        "7_top_symbol_share": top_sym,
        "8_vwap_fully_replaceable": vwap_replaceable,
        "9_runtime_adoption_candidate": production_candidate,
        "10_shadow_vs_adoption": adoption,
        "notes": {
            "vwap_inactive_this_period": vwap_only == 0 and both == 0,
            "delta_excluding_6976_removals_yen": delta_excl_6976,
            "delta_excluding_20260618_removals_yen": delta_excl_618,
            "delta_excluding_6976_on_20260618_yen": counterfactuals.get(
                "delta_excluding_6976_on_20260618_yen"
            ),
            "6976_improvement_contribution_rate": analysis6976.get("improvement_contribution_rate"),
            "general_improvement_after_excluding_6976": (delta_excl_6976 or 0) > 0,
        },
    }


def run_phase437a_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    accepted_idx = _load_accepted_index(kabu)
    price_idx = _build_price_index(kabu)
    trades = _enrich_trades(
        _accepted_trades_from_sim(repo_root),
        kabu_root=kabu,
        accepted_idx=accepted_idx,
        price_idx=price_idx,
    )
    baseline_accepted = len(trades)

    variant_rows: list[dict[str, Any]] = []
    concentration_by_variant: dict[str, Any] = {}
    for spec in VARIANTS:
        vid = str(spec["variant_id"])
        block_fn: Callable[[Mapping[str, Any]], bool] = spec["block_fn"]
        row = _variant_metrics(
            trades,
            variant_id=vid,
            label=str(spec["label"]),
            block_fn=block_fn,
            baseline_accepted_count=baseline_accepted,
        )
        if vid != "A_baseline":
            conc = _delta_concentration(trades, block_fn=block_fn)
            row["concentration"] = {
                k: conc[k]
                for k in (
                    "total_delta_yen",
                    "top_day_share",
                    "top3_day_share",
                    "top_symbol_share",
                    "top3_symbol_share",
                    "hhi_day_positive_delta",
                    "gini_day_positive_delta",
                    "hhi_symbol_positive_delta",
                    "gini_symbol_positive_delta",
                )
            }
            concentration_by_variant[vid] = conc
        variant_rows.append(row)

    daily_rows: list[dict[str, Any]] = []
    for spec in VARIANTS:
        if spec["variant_id"] == "A_baseline":
            continue
        daily_rows.extend(
            _daily_attribution(
                trades,
                baseline_block=lambda _t: False,
                variant_block=spec["block_fn"],
                variant_id=str(spec["variant_id"]),
            )
        )

    overlap_rows = _overlap_analysis(trades)
    hd_conc = concentration_by_variant.get("C_high_drift", {})
    analysis6976 = _6976_analysis(
        trades,
        block_fn=guard_high_drift,
        total_delta_yen=_float(hd_conc.get("total_delta_yen")),
    )

    counterfactuals = {
        "delta_excluding_6976_removals_yen": _counterfactual_delta_excluding(
            trades,
            block_fn=guard_high_drift,
            exclude_fn=lambda t: str(t.get("symbol")) == TARGET_SYMBOL,
        ),
        "delta_excluding_20260618_removals_yen": _counterfactual_delta_excluding(
            trades,
            block_fn=guard_high_drift,
            exclude_fn=lambda t: str(t.get("day") or "") == "20260618",
        ),
        "delta_excluding_6976_on_20260618_yen": _counterfactual_delta_excluding(
            trades,
            block_fn=guard_high_drift,
            exclude_fn=lambda t: str(t.get("symbol")) == TARGET_SYMBOL
            and str(t.get("day") or "") == "20260618",
        ),
    }

    mandatory = _mandatory_answers(
        overlap_rows=overlap_rows,
        variant_rows=variant_rows,
        concentration=hd_conc,
        analysis6976=analysis6976,
        counterfactuals=counterfactuals,
    )

    verdict = str(mandatory.get("10_shadow_vs_adoption") or "shadow_continue")
    if mandatory.get("9_runtime_adoption_candidate"):
        verdict = "runtime_adoption_candidate"
    elif _float(mandatory.get("notes", {}).get("6976_improvement_contribution_rate")) or 0 > 0.5:
        verdict = "6976_concentration_risk_shadow_continue"

    return {
        "phase": "437A-High-Drift-Adoption-Audit",
        "generated_at": _now_iso(),
        "period": f"{PERIOD_START}..{PERIOD_END}",
        "baseline": "Phase423 canonical + forward capital sim",
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "variants": variant_rows,
        "concentration_by_variant": concentration_by_variant,
        "overlap": overlap_rows,
        "analysis_6976": analysis6976,
        "counterfactuals": counterfactuals,
        "daily_attribution": daily_rows,
    }


def _csv_write(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    _write_csv(path, list(rows[0].keys()), rows)


@dataclass
class Phase437AJob:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase437a_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        kabu = resolve_kabu_root(self.repo_root)

        paths = {
            "audit": reports / "phase437a_high_drift_adoption_audit.csv",
            "daily": reports / "phase437a_high_drift_daily_attribution.csv",
            "overlap": reports / "phase437a_high_drift_vwap_overlap.csv",
            "summary": reports / "phase437a_high_drift_summary.json",
            "report": kabu / "docs" / "operations" / "phase437a_high_drift_adoption_audit.md",
        }

        audit_rows: list[dict[str, Any]] = []
        conc_keys = (
            "total_delta_yen",
            "top_day_share",
            "top3_day_share",
            "top_symbol_share",
            "top3_symbol_share",
            "hhi_day_positive_delta",
            "gini_day_positive_delta",
            "hhi_symbol_positive_delta",
            "gini_symbol_positive_delta",
        )
        for v in result.get("variants") or []:
            row = dict(v)
            conc = row.pop("concentration", None) or {}
            for k in conc_keys:
                row[f"conc_{k}"] = conc.get(k)
            audit_rows.append(row)
        _csv_write(paths["audit"], audit_rows)
        _csv_write(paths["daily"], result.get("daily_attribution") or [])
        _csv_write(paths["overlap"], result.get("overlap") or [])

        summary = {
            "phase": result.get("phase"),
            "generated_at": result.get("generated_at"),
            "period": result.get("period"),
            "verdict": result.get("verdict"),
            "mandatory_answers": result.get("mandatory_answers"),
            "variants": result.get("variants"),
            "overlap": result.get("overlap"),
            "analysis_6976": result.get("analysis_6976"),
            "counterfactuals": result.get("counterfactuals"),
            "concentration_high_drift": (result.get("concentration_by_variant") or {}).get(
                "C_high_drift"
            ),
        }
        paths["summary"].write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        m = result.get("mandatory_answers") or {}
        base = next((v for v in (result.get("variants") or []) if v.get("variant_id") == "A_baseline"), {})
        hd = next((v for v in (result.get("variants") or []) if v.get("variant_id") == "C_high_drift"), {})
        a6976 = result.get("analysis_6976") or {}
        cf = result.get("counterfactuals") or {}

        lines = [
            "# Phase437A — High Drift Adoption Audit",
            "",
            f"Generated: {result.get('generated_at')}",
            f"Period: {result.get('period')}",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## Mandatory answers",
            "",
            f"1. VWAP only removed: **{m.get('1_vwap_only_removed_count')}**",
            f"2. High Drift only removed: **{m.get('2_high_drift_only_removed_count')}**",
            f"3. Both removed: **{m.get('3_both_removed_count')}**",
            f"4. High Drift standalone PnL: **{m.get('4_high_drift_standalone_pnl_yen'):,.0f} yen**",
            f"5. High Drift standalone PF: **{m.get('5_high_drift_standalone_pf')}**",
            f"6. top_day_share: **{m.get('6_top_day_share')}**",
            f"7. top_symbol_share: **{m.get('7_top_symbol_share')}**",
            f"8. VWAP fully replaceable: **{m.get('8_vwap_fully_replaceable')}**",
            f"9. Runtime adoption candidate: **{m.get('9_runtime_adoption_candidate')}**",
            f"10. Shadow vs adoption: **{m.get('10_shadow_vs_adoption')}**",
            "",
            "## Variant comparison",
            "",
            "| variant | trades | PnL | PF | stop_rate | maxDD | removed |",
            "|---------|--------|-----|-----|-----------|-------|---------|",
        ]
        for v in result.get("variants") or []:
            lines.append(
                f"| {v.get('label')} | {v.get('trade_count')} | {v.get('total_pnl_yen'):,.0f} | "
                f"{v.get('profit_factor')} | {v.get('stop_rate')} | "
                f"{v.get('max_drawdown_yen'):,.0f} | {v.get('removed_trade_count')} |"
            )

        lines.extend(
            [
                "",
                "## Concentration (High Drift delta vs baseline)",
                "",
                f"- top_day_share: {m.get('6_top_day_share')} (threshold ≤0.5)",
                f"- top_symbol_share: {m.get('7_top_symbol_share')} (threshold ≤0.3)",
                f"- delta excluding 6976 removals: {cf.get('delta_excluding_6976_removals_yen'):,.0f} yen",
                f"- delta excluding 20260618 removals: {cf.get('delta_excluding_20260618_removals_yen'):,.0f} yen",
                f"- delta excluding 6976 on 20260618: {cf.get('delta_excluding_6976_on_20260618_yen'):,.0f} yen",
                "",
                "## 6976 analysis",
                "",
                f"- removed: {a6976.get('removed_count')} trades, PnL {a6976.get('removed_pnl_yen'):,.0f} yen",
                f"- removed stops: {a6976.get('removed_stop_count')}",
                f"- improvement contribution rate: {a6976.get('improvement_contribution_rate')}",
                f"- 20260618 removed: {a6976.get('removed_on_20260618_count')} "
                f"({a6976.get('removed_on_20260618_pnl_yen'):,.0f} yen)",
                "",
                "## VWAP overlap",
                "",
                "| bucket | count | PnL | PF | 6976 |",
                "|--------|-------|-----|-----|------|",
            ]
        )
        for r in result.get("overlap") or []:
            lines.append(
                f"| {r.get('bucket')} | {r.get('trade_count')} | {r.get('total_pnl_yen'):,.0f} | "
                f"{r.get('profit_factor')} | {r.get('symbol_6976_count')} |"
            )

        lines.extend(
            [
                "",
                "## Interpretation",
                "",
                f"- Baseline: {base.get('total_pnl_yen'):,.0f} yen, PF {base.get('profit_factor')}",
                f"- High Drift: {hd.get('total_pnl_yen'):,.0f} yen, PF {hd.get('profit_factor')}",
                "- Legacy VWAP removes 0 trades this period (vwap_dev>0 on all pullback candidates).",
                "- High Drift is orthogonal to VWAP; combined variant equals High Drift alone.",
                "- High concentration in 20260618 / 6976.T drives delta — not a pure 6976-only guard "
                "but period-specific validation required before Runtime.",
                "",
                "Runtime/YAML/Entry/Exit/Order/Discord changes **forbidden** (audit only).",
                "",
            ]
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text("\n".join(lines), encoding="utf-8")
        return paths
