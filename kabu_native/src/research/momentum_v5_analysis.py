"""
Phase 27: Recovery-based EXIT v5 comparison and recovery analysis outputs.
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.entry_v2 import ENTRY_V2_PHASE27_PROFILES, MOMENTUM_V2_REFERENCE


def _as_float(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _profile_metrics(profile_row: dict[str, Any], trade_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pname = str(profile_row.get("profile", ""))
    rows = [r for r in trade_rows if str(r.get("profile")) == pname]
    n = len(rows)
    hs = sum(1 for r in rows if str(r.get("exit_reason")) == "hard_stop")
    imb = sum(1 for r in rows if str(r.get("exit_reason")) == "board_imbalance_deterioration")
    early_cut = sum(
        1
        for r in rows
        if str(r.get("exit_reason")) in ("recovery_early_cut", "recovery_or_cut_fail", "early_adverse_guard")
    )
    recovery_holds = sum(int(r.get("recovery_hold_count") or 0) for r in rows)
    recovered = [r for r in rows if r.get("recovered_after_adverse")]
    recovery_success = sum(1 for r in recovered if float(r.get("pnl_pct", 0)) > 0)
    return {
        **profile_row,
        "trades": profile_row.get("entry_count"),
        "hard_stop_rate": (hs / n) if n else None,
        "board_imbalance_exit_rate": profile_row.get("board_imbalance_exit_rate"),
        "early_cut_count": early_cut,
        "recovery_hold_count": recovery_holds,
        "recovery_success_rate": (recovery_success / len(recovered)) if recovered else None,
    }


def _adoption_vs_v2(row: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    flags: list[str] = []

    def _f(key: str) -> float | None:
        return _as_float(row.get(key))

    def _rf(key: str) -> float | None:
        return _as_float(ref.get(key))

    pf, rpf = _f("profit_factor"), _rf("profit_factor")
    ap, rap = _f("avg_pnl_pct"), _rf("avg_pnl_pct")
    ml, rml = _f("max_loss_pct"), _rf("max_loss_pct")
    mfe, rmfe = _f("mfe_ge_0_3_pct_rate"), _rf("mfe_ge_0_3_pct_rate")
    hs, rhs = _f("hard_stop_rate"), _rf("hard_stop_rate")
    imb, rimb = _f("board_imbalance_exit_rate"), _rf("board_imbalance_exit_rate")
    ec, rec = row.get("entry_count"), ref.get("entry_count")
    swt, rswt = row.get("symbols_with_trades"), ref.get("symbols_with_trades")

    if pf is not None and rpf is not None and pf <= rpf:
        flags.append("pf_not_improved")
    if ap is not None and rap is not None and ap <= rap:
        flags.append("avg_pnl_not_improved")
    if ml is not None and rml is not None and ml < rml:
        flags.append("max_loss_worse")
    if mfe is not None and rmfe is not None and mfe < rmfe - 0.01:
        flags.append("mfe_0_3_degraded")
    if hs is not None and rhs is not None and hs > rhs + 0.03:
        flags.append("hard_stop_rate_worse")
    if imb is not None and rimb is not None and imb > rimb + 0.02:
        flags.append("imbalance_exit_rate_worse")
    if isinstance(swt, int) and isinstance(rswt, int) and rswt > 0 and swt < max(1, int(rswt * 0.5)):
        flags.append("symbols_with_trades_dropped")
    if (ec or 0) < (rec or 0) * 0.6 and pf is not None and rpf is not None and pf > rpf:
        flags.append("trade_count_only_reduction_improvement")
    if (ec or 0) > (rec or 0) and pf is not None and rpf is not None and pf < rpf:
        flags.append("trade_count_up_pf_down")
    if (row.get("concentration_top_symbol_pct") or 0) > 0.5:
        flags.append("symbol_concentration_high")

    return {
        "reference_profile": MOMENTUM_V2_REFERENCE,
        "adoption_flags": flags,
        "recommended": len(flags) == 0 and pf is not None and rpf is not None and pf >= rpf,
    }


def build_recovery_analysis(v2_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(v2_rows)
    winners = [r for r in rows if float(r.get("pnl_pct", 0)) > 0]
    losers = [r for r in rows if float(r.get("pnl_pct", 0)) < 0]

    def _rate(grp: Sequence[Mapping[str, Any]], key: str) -> float | None:
        if not grp:
            return None
        return sum(1 for r in grp if r.get(key)) / len(grp)

    def _mean(grp: Sequence[Mapping[str, Any]], key: str) -> float | None:
        vals = [_as_float(r.get(key)) for r in grp]
        vals = [v for v in vals if v is not None]
        return statistics.mean(vals) if vals else None

    notes: list[str] = []
    if _rate(losers, "recovered_after_adverse") and _rate(losers, "recovered_after_adverse") > 0.2:
        notes.append("losers_also_recover_sometimes_exit_timing_issue")
    w60 = _mean(winners, "early_60s_max_favorable_pct")
    l60 = _mean(losers, "early_60s_max_adverse_pct")
    if w60 is not None and l60 is not None and w60 > l60 + 0.05:
        notes.append("60s_favorable_separates_winners")
    notes.append("v5_strategy: delay_imb_before_60s_cut_only_non_recoverable_adverse")

    return {
        "phase": 27,
        "reference_profile": MOMENTUM_V2_REFERENCE,
        "trade_count": len(rows),
        "winners": len(winners),
        "losers": len(losers),
        "winner_recovered_rate": _rate(winners, "recovered_after_adverse"),
        "loser_recovered_rate": _rate(losers, "recovered_after_adverse"),
        "winner_adverse_first_rate": _rate(winners, "adverse_first_sec"),
        "loser_adverse_first_rate": _rate(losers, "adverse_first_sec"),
        "winner_60s_avg_favorable": w60,
        "loser_60s_avg_adverse": l60,
        "imbalance_exit_share": (
            sum(1 for r in rows if str(r.get("exit_reason")) == "board_imbalance_deterioration")
            / len(rows)
            if rows
            else None
        ),
        "diagnosis_notes": notes,
        "hypothesis": (
            "初動逆行をすぐ切らず、回復不能な逆行だけを切る。"
            "board_imbalance は60秒以降・連続悪化・逆行同時のみ。"
        ),
    }


def write_momentum_phase27_outputs(
    out: Path,
    *,
    by_profile: dict[str, list[Any]],
    profile_summaries: list[dict[str, Any]],
) -> None:
    all_rows: list[dict[str, Any]] = []
    v2_rows: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE27_PROFILES:
        if pname not in by_profile:
            continue
        for r in by_profile[pname]:
            all_rows.extend(getattr(r, "enriched_trade_rows", []))
        if pname == MOMENTUM_V2_REFERENCE:
            for r in by_profile[pname]:
                v2_rows.extend(getattr(r, "enriched_trade_rows", []))

    if v2_rows:
        (out / "momentum_v5_recovery_analysis.json").write_text(
            json.dumps(build_recovery_analysis(v2_rows), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    summary_by = {str(r["profile"]): r for r in profile_summaries}
    ref = summary_by.get(MOMENTUM_V2_REFERENCE)
    if not ref:
        return

    comp_rows: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE27_PROFILES:
        if pname not in summary_by:
            continue
        prow = _profile_metrics(summary_by[pname], all_rows)
        comp_rows.append({**prow, "vs_reference": _adoption_vs_v2(prow, ref)})

    flat: list[dict[str, Any]] = []
    for r in comp_rows:
        flat_row = {k: v for k, v in r.items() if k != "vs_reference"}
        vs = r.get("vs_reference") or {}
        if isinstance(vs, dict):
            for vk, vv in vs.items():
                flat_row[f"vs_{vk}"] = vv
        flat.append(flat_row)

    keys = (
        "profile",
        "trades",
        "symbols_with_trades",
        "total_pnl_pct",
        "avg_pnl_pct",
        "profit_factor",
        "win_rate",
        "max_loss_pct",
        "avg_loss_pct",
        "mfe_ge_0_3_pct_rate",
        "mfe_ge_0_5_pct_rate",
        "mae_p50",
        "mae_p90",
        "hard_stop_rate",
        "board_imbalance_exit_rate",
        "recovery_hold_count",
        "early_cut_count",
        "recovery_success_rate",
        "median_hold_min",
        "worst_day_pnl",
        "concentration_top_symbol_pct",
        "vs_reference_profile",
        "vs_adoption_flags",
        "vs_recommended",
    )
    fields = [k for k in keys if flat and k in flat[0]]
    for fr in flat:
        for k in fr:
            if k not in fields:
                fields.append(k)

    with (out / "momentum_v5_comparison.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)

    (out / "momentum_v5_comparison.json").write_text(
        json.dumps(
            {
                "phase": 27,
                "reference_profile": MOMENTUM_V2_REFERENCE,
                "profiles": list(ENTRY_V2_PHASE27_PROFILES),
                "rows": comp_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
