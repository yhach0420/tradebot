"""
Phase 28: momentum v6 comparison and microstructure export.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.entry_v2 import ENTRY_V2_PHASE28_PROFILES, MOMENTUM_V2_REFERENCE
from research.microstructure_analysis import build_microstructure_analysis


def _as_float(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _profile_metrics(row: dict[str, Any], trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pname = str(row.get("profile", ""))
    grp = [t for t in trades if str(t.get("profile")) == pname]
    n = len(grp)
    imb = sum(1 for t in grp if str(t.get("exit_reason")) == "board_imbalance_deterioration")
    fake = sum(1 for t in grp if str(t.get("exit_reason")) == "fake_breakout_exit")
    struct = sum(1 for t in grp if str(t.get("exit_reason")) in ("structure_break_exit", "microstructure_noise_exit"))
    recovered = [t for t in grp if t.get("recovered_after_adverse")]
    rec_ok = sum(1 for t in recovered if float(t.get("pnl_pct", 0)) > 0)
    vwap_ok = sum(1 for t in grp if t.get("vwap_reclaim_achieved"))
    adv_persist = sum(1 for t in grp if (_as_float(t.get("adverse_persistence_count")) or 0) >= 3)
    fav_persist = sum(1 for t in grp if (_as_float(t.get("favorable_persistence_count")) or 0) >= 2)
    reentry_loops = sum(int(t.get("reentry_blocked") or 0) for t in grp)

    return {
        **row,
        "trades": row.get("entry_count"),
        "hard_stop_rate": row.get("hard_stop_rate"),
        "board_imbalance_exit_rate": row.get("board_imbalance_exit_rate"),
        "fake_breakout_rate": (fake / n) if n else None,
        "fake_breakout_exit_rate": (fake / n) if n else None,
        "structure_break_exit_rate": (struct / n) if n else None,
        "recovery_success_rate": (rec_ok / len(recovered)) if recovered else None,
        "vwap_reclaim_success_rate": (vwap_ok / n) if n else None,
        "adverse_persistence_rate": (adv_persist / n) if n else None,
        "favorable_persistence_rate": (fav_persist / n) if n else None,
        "reentry_loop_rate": (reentry_loops / max(n, 1)) if n else None,
    }


def _adoption(row: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    flags: list[str] = []

    def _f(k: str) -> float | None:
        return _as_float(row.get(k))

    def _r(k: str) -> float | None:
        return _as_float(ref.get(k))

    pf, rpf = _f("profit_factor"), _r("profit_factor")
    ap, rap = _f("avg_pnl_pct"), _r("avg_pnl_pct")
    ml, rml = _f("max_loss_pct"), _r("max_loss_pct")
    mfe, rmfe = _f("mfe_ge_0_3_pct_rate"), _r("mfe_ge_0_3_pct_rate")
    hs, rhs = _f("hard_stop_rate"), _r("hard_stop_rate")
    imb, rimb = _f("board_imbalance_exit_rate"), _r("board_imbalance_exit_rate")
    fake, rfake = _f("fake_breakout_rate"), _r("fake_breakout_rate")
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
    if fake is not None and rfake is not None and fake > rfake + 0.05:
        flags.append("fake_breakout_rate_worse")
    if isinstance(swt, int) and isinstance(rswt, int) and rswt > 0 and swt < max(1, int(rswt * 0.5)):
        flags.append("symbols_with_trades_dropped")
    if (ec or 0) < (rec or 0) * 0.6 and pf is not None and rpf is not None and pf > rpf:
        flags.append("trade_count_only_reduction_improvement")
    if (ec or 0) > (rec or 0) * 1.8:
        flags.append("reentry_loop_risk")
    if (row.get("concentration_top_symbol_pct") or 0) > 0.5:
        flags.append("symbol_concentration_high")

    return {
        "reference_profile": MOMENTUM_V2_REFERENCE,
        "adoption_flags": flags,
        "recommended": len(flags) == 0 and pf is not None and rpf is not None and pf >= rpf,
    }


def write_momentum_phase28_outputs(
    out: Path,
    *,
    by_profile: dict[str, list[Any]],
    profile_summaries: list[dict[str, Any]],
) -> None:
    all_trades: list[dict[str, Any]] = []
    v2_trades: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE28_PROFILES:
        if pname not in by_profile:
            continue
        for r in by_profile[pname]:
            all_trades.extend(getattr(r, "enriched_trade_rows", []))
        if pname == MOMENTUM_V2_REFERENCE:
            for r in by_profile[pname]:
                v2_trades.extend(getattr(r, "enriched_trade_rows", []))

    if v2_trades:
        (out / "microstructure_analysis.json").write_text(
            json.dumps(build_microstructure_analysis(v2_trades), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    summary = {str(r["profile"]): r for r in profile_summaries}
    ref = summary.get(MOMENTUM_V2_REFERENCE)
    if not ref:
        return

    comp: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE28_PROFILES:
        if pname not in summary:
            continue
        prow = _profile_metrics(summary[pname], all_trades)
        comp.append({**prow, "vs_reference": _adoption(prow, ref)})

    flat: list[dict[str, Any]] = []
    for r in comp:
        fr = {k: v for k, v in r.items() if k != "vs_reference"}
        vs = r.get("vs_reference") or {}
        if isinstance(vs, dict):
            for k, v in vs.items():
                fr[f"vs_{k}"] = v
        flat.append(fr)

    keys = (
        "profile",
        "trades",
        "symbols_with_trades",
        "total_pnl_pct",
        "avg_pnl_pct",
        "profit_factor",
        "win_rate",
        "max_loss_pct",
        "mae_p50",
        "mae_p90",
        "mfe_ge_0_3_pct_rate",
        "hard_stop_rate",
        "board_imbalance_exit_rate",
        "fake_breakout_rate",
        "recovery_success_rate",
        "vwap_reclaim_success_rate",
        "adverse_persistence_rate",
        "favorable_persistence_rate",
        "reentry_loop_rate",
        "median_hold_min",
        "worst_day_pnl",
        "concentration_top_symbol_pct",
        "trades_per_day",
        "vs_reference_profile",
        "vs_adoption_flags",
        "vs_recommended",
    )
    fields = [k for k in keys if flat and k in flat[0]]
    for fr in flat:
        for k in fr:
            if k not in fields:
                fields.append(k)

    with (out / "momentum_v6_comparison.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)

    (out / "momentum_v6_comparison.json").write_text(
        json.dumps(
            {"phase": 28, "reference_profile": MOMENTUM_V2_REFERENCE, "profiles": list(ENTRY_V2_PHASE28_PROFILES), "rows": comp},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
