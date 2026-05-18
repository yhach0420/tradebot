"""
Phase 30: momentum v8 comparison vs v2/v5/v6/v7 and persistence metrics.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.entry_v2 import (
    ENTRY_V2_PHASE30_PROFILES,
    MOMENTUM_V2_REFERENCE,
    MOMENTUM_V5_COMBINED_REFERENCE,
    MOMENTUM_V6_COMBINED_REFERENCE,
    MOMENTUM_V7_COMBINED_REFERENCE,
)
from research.recovery_persistence_analysis import build_recovery_persistence_analysis


def _as_float(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _profile_metrics(row: dict[str, Any], trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pname = str(row.get("profile", ""))
    grp = [t for t in trades if str(t.get("profile")) == pname]
    n = len(grp)
    if not n:
        return {**row, "trades": row.get("entry_count")}

    imb = sum(1 for t in grp if str(t.get("exit_reason")) == "board_imbalance_deterioration")
    struct = sum(
        1
        for t in grp
        if str(t.get("exit_reason")) in ("structure_break_v8", "structure_break_v7", "structure_break_exit")
    )
    reentry = sum(int(t.get("reentry_blocked") or 0) for t in grp)

    def _rate(key: str) -> float | None:
        return sum(1 for t in grp if t.get(key)) / n

    return {
        **row,
        "trades": row.get("entry_count"),
        "imbalance_exit_rate": (imb / n) if n else row.get("board_imbalance_exit_rate"),
        "structure_break_exit_rate": (struct / n) if n else None,
        "reclaim_persistence_rate": _rate("reclaim_persistent"),
        "favorable_persistence_rate": _rate("favorable_persistent"),
        "recovery_then_trend_rate": _rate("recovery_then_trend"),
        "recovery_then_fail_rate": _rate("recovery_then_fail"),
        "adverse_persistence_rate": (
            sum(1 for t in grp if (_as_float(t.get("adverse_persistence_count")) or 0) >= 4) / n
        ),
        "reentry_loop_rate": (reentry / n) if n else None,
    }


def _adoption(
    row: dict[str, Any],
    refs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    flags: list[str] = []

    def _f(k: str) -> float | None:
        return _as_float(row.get(k))

    pf = _f("profit_factor")
    ap = _f("avg_pnl_pct")
    ml = _f("max_loss_pct")
    mfe3 = _f("mfe_ge_0_3_pct_rate")
    hs = _f("hard_stop_rate")
    imb = _f("imbalance_exit_rate")
    ec = row.get("entry_count")
    swt = row.get("symbols_with_trades")
    rel = _f("reentry_loop_rate")
    rtt = _f("recovery_then_trend_rate")
    rtf = _f("recovery_then_fail_rate")
    rpr = _f("reclaim_persistence_rate")

    for label, ref in refs.items():
        rpf = _as_float(ref.get("profit_factor"))
        rap = _as_float(ref.get("avg_pnl_pct"))
        rml = _as_float(ref.get("max_loss_pct"))
        rmfe3 = _as_float(ref.get("mfe_ge_0_3_pct_rate"))
        rhs = _as_float(ref.get("hard_stop_rate"))
        rimb = _as_float(ref.get("imbalance_exit_rate"))
        rec = ref.get("entry_count")
        rswt = ref.get("symbols_with_trades")
        rrel = _as_float(ref.get("reentry_loop_rate"))
        rrtt = _as_float(ref.get("recovery_then_trend_rate"))
        rrtf = _as_float(ref.get("recovery_then_fail_rate"))
        rrpr = _as_float(ref.get("reclaim_persistence_rate"))

        if pf is not None and rpf is not None and pf <= rpf:
            flags.append(f"pf_not_improved_vs_{label}")
        if ap is not None and rap is not None and ap <= rap:
            flags.append(f"avg_pnl_not_improved_vs_{label}")
        if ml is not None and rml is not None and ml < rml:
            flags.append(f"max_loss_worse_vs_{label}")
        if mfe3 is not None and rmfe3 is not None and mfe3 < rmfe3 - 0.01:
            flags.append(f"mfe_0_3_degraded_vs_{label}")
        if hs is not None and rhs is not None and hs > rhs + 0.03:
            flags.append(f"hard_stop_worse_vs_{label}")
        if imb is not None and rimb is not None and imb > rimb + 0.02:
            flags.append(f"imbalance_exit_worse_vs_{label}")
        if isinstance(swt, int) and isinstance(rswt, int) and rswt > 0 and swt < max(1, int(rswt * 0.5)):
            flags.append(f"symbols_dropped_vs_{label}")
        if (ec or 0) < (rec or 0) * 0.6 and pf is not None and rpf is not None and pf > rpf:
            flags.append("trade_count_only_reduction_improvement")
        if rel is not None and rrel is not None and rel > rrel + 0.05:
            flags.append("reentry_loop_rate_worse")
        if rtt is not None and rrtt is not None and rtt < rrtt - 0.03:
            flags.append("recovery_then_trend_decreased")
        if rtf is not None and rrtf is not None and rtf > rrtf + 0.05:
            flags.append("recovery_then_fail_increased")
        if rpr is not None and rrpr is not None and rpr < rrpr - 0.03:
            flags.append("reclaim_persistence_decreased")

    if (row.get("concentration_top_symbol_pct") or 0) > 0.5:
        flags.append("symbol_concentration_high")

    v2 = refs.get("v2", {})
    v2pf = _as_float(v2.get("profit_factor"))
    recommended = (
        len(flags) == 0
        and pf is not None
        and v2pf is not None
        and pf >= v2pf
        and ap is not None
        and _as_float(v2.get("avg_pnl_pct")) is not None
        and ap > _as_float(v2.get("avg_pnl_pct"))
    )

    return {"adoption_flags": flags, "recommended": recommended}


def write_momentum_phase30_outputs(
    out: Path,
    *,
    by_profile: dict[str, list[Any]],
    profile_summaries: list[dict[str, Any]],
) -> None:
    all_trades: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE30_PROFILES:
        if pname not in by_profile:
            continue
        for r in by_profile[pname]:
            all_trades.extend(getattr(r, "enriched_trade_rows", []))

    (out / "recovery_persistence_analysis.json").write_text(
        json.dumps(build_recovery_persistence_analysis(all_trades), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {str(r["profile"]): r for r in profile_summaries}
    ref_names = {
        "v2": MOMENTUM_V2_REFERENCE,
        "v5": MOMENTUM_V5_COMBINED_REFERENCE,
        "v6": MOMENTUM_V6_COMBINED_REFERENCE,
        "v7": MOMENTUM_V7_COMBINED_REFERENCE,
    }
    refs: dict[str, dict[str, Any]] = {}
    for label, pname in ref_names.items():
        if pname in summary:
            refs[label] = _profile_metrics(summary[pname], all_trades)
    if "v2" not in refs:
        return

    comp: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE30_PROFILES:
        if pname not in summary:
            continue
        prow = _profile_metrics(summary[pname], all_trades)
        if pname.startswith("momentum_volume_v8_"):
            adopt = _adoption(prow, refs)
            comp.append({**prow, **adopt})
        else:
            comp.append(prow)

    flat = [dict(r) for r in comp]
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
        "mae_p50",
        "mae_p90",
        "mfe_ge_0_3_pct_rate",
        "mfe_ge_0_5_pct_rate",
        "imbalance_exit_rate",
        "hard_stop_rate",
        "structure_break_exit_rate",
        "reclaim_persistence_rate",
        "favorable_persistence_rate",
        "recovery_then_trend_rate",
        "recovery_then_fail_rate",
        "adverse_persistence_rate",
        "reentry_loop_rate",
        "median_hold_min",
        "worst_day_pnl",
        "concentration_top_symbol_pct",
        "trades_per_day",
        "adoption_flags",
        "recommended",
    )
    fields = [k for k in keys if flat and k in flat[0]]
    for fr in flat:
        for k in fr:
            if k not in fields:
                fields.append(k)

    with (out / "momentum_v8_comparison.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)

    (out / "momentum_v8_comparison.json").write_text(
        json.dumps(
            {
                "phase": 30,
                "reference_profiles": {
                    "v2": MOMENTUM_V2_REFERENCE,
                    "v5": MOMENTUM_V5_COMBINED_REFERENCE,
                    "v6": MOMENTUM_V6_COMBINED_REFERENCE,
                    "v7": MOMENTUM_V7_COMBINED_REFERENCE,
                },
                "profiles": list(ENTRY_V2_PHASE30_PROFILES),
                "rows": comp,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
