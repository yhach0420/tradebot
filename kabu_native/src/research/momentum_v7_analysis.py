"""
Phase 29: momentum v7 comparison vs v2/v5/v6 and recovery-path effectiveness.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.entry_v2 import (
    ENTRY_V2_PHASE29_PROFILES,
    MOMENTUM_V2_REFERENCE,
    MOMENTUM_V5_COMBINED_REFERENCE,
    MOMENTUM_V6_COMBINED_REFERENCE,
)

MICRO_HORIZONS = (15, 30, 60)


def _as_float(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _dist(vals: Sequence[float]) -> dict[str, Any]:
    if not vals:
        return {"count": 0, "p50": None, "mean": None}
    s = sorted(vals)
    return {"count": len(s), "p50": statistics.median(s), "mean": statistics.mean(s)}


def _profile_exit_metrics(row: dict[str, Any], trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pname = str(row.get("profile", ""))
    grp = [t for t in trades if str(t.get("profile")) == pname]
    n = len(grp)
    if not n:
        return {**row, "trades": row.get("entry_count")}

    imb = sum(1 for t in grp if str(t.get("exit_reason")) == "board_imbalance_deterioration")
    struct = sum(1 for t in grp if str(t.get("exit_reason")) in ("structure_break_v7", "structure_break_exit"))
    adv_cut = sum(int(t.get("adverse_cut_count") or 0) for t in grp)
    rec_hold = sum(int(t.get("recovery_hold_count") or 0) for t in grp)
    recovered = [t for t in grp if t.get("recovered_after_adverse")]
    rec_ok = sum(1 for t in recovered if float(t.get("pnl_pct", 0)) > 0)
    reentry = sum(int(t.get("reentry_blocked") or 0) for t in grp)

    return {
        **row,
        "trades": row.get("entry_count"),
        "imbalance_exit_rate": (imb / n) if n else None,
        "structure_break_exit_rate": (struct / n) if n else None,
        "recovery_hold_count": rec_hold,
        "recovery_success_rate": (rec_ok / len(recovered)) if recovered else None,
        "adverse_cut_count": adv_cut,
        "reentry_loop_rate": (reentry / n) if n else None,
    }


def _adoption_v2(row: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    flags: list[str] = []

    def _f(k: str) -> float | None:
        return _as_float(row.get(k))

    def _r(k: str) -> float | None:
        return _as_float(ref.get(k))

    pf, rpf = _f("profit_factor"), _r("profit_factor")
    ap, rap = _f("avg_pnl_pct"), _r("avg_pnl_pct")
    ml, rml = _f("max_loss_pct"), _r("max_loss_pct")
    mfe3, rmfe3 = _f("mfe_ge_0_3_pct_rate"), _r("mfe_ge_0_3_pct_rate")
    mfe5, rmfe5 = _f("mfe_ge_0_5_pct_rate"), _r("mfe_ge_0_5_pct_rate")
    hs, rhs = _f("hard_stop_rate"), _r("hard_stop_rate")
    imb, rimb = _f("board_imbalance_exit_rate"), _r("board_imbalance_exit_rate")
    ec, rec = row.get("entry_count"), ref.get("entry_count")
    swt, rswt = row.get("symbols_with_trades"), ref.get("symbols_with_trades")
    rel, rrel = _f("reentry_loop_rate"), _r("reentry_loop_rate")

    if pf is not None and rpf is not None and pf <= rpf:
        flags.append("pf_not_improved_vs_v2")
    if ml is not None and rml is not None and ml < rml:
        flags.append("max_loss_worse")
    if mfe3 is not None and rmfe3 is not None and mfe3 < rmfe3 - 0.01:
        flags.append("mfe_0_3_degraded")
    if mfe5 is not None and rmfe5 is not None and mfe5 < rmfe5 - 0.01:
        flags.append("mfe_0_5_degraded")
    if hs is not None and rhs is not None and hs > rhs + 0.03:
        flags.append("hard_stop_rate_worse")
    if imb is not None and rimb is not None and imb > rimb + 0.02:
        flags.append("imbalance_exit_rate_worse")
    if isinstance(swt, int) and isinstance(rswt, int) and rswt > 0 and swt < max(1, int(rswt * 0.5)):
        flags.append("symbols_with_trades_dropped")
    if (ec or 0) < (rec or 0) * 0.6 and pf is not None and rpf is not None and pf > rpf:
        flags.append("trade_count_only_reduction_improvement")
    if rel is not None and rrel is not None and rel > rrel + 0.05:
        flags.append("reentry_loop_rate_worse")
    if (row.get("concentration_top_symbol_pct") or 0) > 0.5:
        flags.append("symbol_concentration_high")

    return {
        "vs_v2_reference": MOMENTUM_V2_REFERENCE,
        "vs_v2_adoption_flags": flags,
        "vs_v2_recommended": len(flags) == 0 and pf is not None and rpf is not None and pf >= rpf,
    }


def _adoption_v6(row: dict[str, Any], v6: dict[str, Any]) -> dict[str, Any]:
    flags: list[str] = []
    ap, vap = _as_float(row.get("avg_pnl_pct")), _as_float(v6.get("avg_pnl_pct"))
    if ap is not None and vap is not None and ap <= vap:
        flags.append("avg_pnl_not_improved_vs_v6")
    return {
        "vs_v6_reference": MOMENTUM_V6_COMBINED_REFERENCE,
        "vs_v6_adoption_flags": flags,
        "vs_v6_avg_pnl_improved": ap is not None and vap is not None and ap > vap,
    }


def _horizon_winner_loser(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    winners = [r for r in rows if float(r.get("pnl_pct", 0)) > 0]
    losers = [r for r in rows if float(r.get("pnl_pct", 0)) < 0]

    def _vals(grp: Sequence[Mapping[str, Any]]) -> list[float]:
        out: list[float] = []
        for r in grp:
            v = _as_float(r.get(key))
            if v is not None:
                out.append(v)
        return out

    return {
        "winners": _dist(_vals(winners)),
        "losers": _dist(_vals(losers)),
        "winner_minus_loser_mean": (
            (statistics.mean(_vals(winners)) - statistics.mean(_vals(losers)))
            if _vals(winners) and _vals(losers)
            else None
        ),
    }


def build_recovery_path_analysis(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    v7_trades = [t for t in trades if str(t.get("profile", "")).startswith("momentum_volume_v7_")]
    by_judgment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in v7_trades:
        j = str(t.get("v7_judgment") or "none")
        if t.get("v7_recovery_hold_at_60"):
            j = "recovery_hold"
        if int(t.get("adverse_cut_count") or 0) > 0 or str(t.get("exit_reason")) == "v7_adverse_cut":
            j = "adverse_cut"
        if t.get("v7_delayed_imb_suppressed") and str(t.get("exit_reason")) == "board_imbalance_deterioration":
            j = "delayed_imb_exit"
        by_judgment[j].append(dict(t))

    def _effectiveness(grp: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not grp:
            return {"count": 0}
        pnls = [float(t.get("pnl_pct", 0)) for t in grp]
        wins = sum(1 for p in pnls if p > 0)
        return {
            "count": len(grp),
            "win_rate": wins / len(grp),
            "avg_pnl_pct": statistics.mean(pnls),
            "total_pnl_pct": sum(pnls),
        }

    horizons_block: dict[str, Any] = {}
    for h in MICRO_HORIZONS:
        horizons_block[f"{h}s"] = {
            "momentum": _horizon_winner_loser(
                v7_trades, f"early_{h}s_momentum_pct_from_entry"
            ),
            "vwap_change": _horizon_winner_loser(v7_trades, f"early_{h}s_vwap_distance_change"),
            "imbalance_change": _horizon_winner_loser(
                v7_trades, f"early_{h}s_board_imbalance_change"
            ),
        }

    judgment_detail: dict[str, Any] = {}
    for label, grp in sorted(by_judgment.items()):
        eff = _effectiveness(grp)
        judgment_detail[label] = {
            **eff,
            "horizons": {
                str(h): {
                    "momentum": _horizon_winner_loser(grp, f"early_{h}s_momentum_pct_from_entry"),
                    "vwap_change": _horizon_winner_loser(grp, f"early_{h}s_vwap_distance_change"),
                }
                for h in MICRO_HORIZONS
            },
        }

    hold_grp = by_judgment.get("recovery_hold", [])
    cut_grp = by_judgment.get("adverse_cut", [])
    delayed_grp = [
        t
        for t in v7_trades
        if t.get("v7_delayed_imb_suppressed") and float(t.get("hold_min", 0) or 0) > 1.0
    ]

    return {
        "phase": 29,
        "v7_trade_count": len(v7_trades),
        "horizons_all_v7": horizons_block,
        "by_v7_judgment": judgment_detail,
        "effectiveness_notes": {
            "recovery_hold": {
                **_effectiveness(hold_grp),
                "hypothesis": "recovery_hold_at_60 should improve avg_pnl vs cutting at 60s",
                "positive_pnl_rate": (
                    sum(1 for t in hold_grp if float(t.get("pnl_pct", 0)) > 0) / len(hold_grp)
                    if hold_grp
                    else None
                ),
            },
            "adverse_cut": {
                **_effectiveness(cut_grp),
                "hypothesis": "adverse_cut should reduce tail losses",
                "avg_mae_pct": (
                    statistics.mean(float(t.get("mae_pct", 0)) for t in cut_grp) if cut_grp else None
                ),
            },
            "delayed_imb": {
                **_effectiveness(delayed_grp),
                "hypothesis": "delayed_imb should not only defer losses (check avg_pnl vs v2 imb exits)",
                "imb_exit_rate": (
                    sum(
                        1
                        for t in delayed_grp
                        if str(t.get("exit_reason")) == "board_imbalance_deterioration"
                    )
                    / len(delayed_grp)
                    if delayed_grp
                    else None
                ),
            },
        },
    }


def write_momentum_phase29_outputs(
    out: Path,
    *,
    by_profile: dict[str, list[Any]],
    profile_summaries: list[dict[str, Any]],
) -> None:
    all_trades: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE29_PROFILES:
        if pname not in by_profile:
            continue
        for r in by_profile[pname]:
            all_trades.extend(getattr(r, "enriched_trade_rows", []))

    (out / "momentum_v7_recovery_path_analysis.json").write_text(
        json.dumps(build_recovery_path_analysis(all_trades), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {str(r["profile"]): r for r in profile_summaries}
    ref_v2 = summary.get(MOMENTUM_V2_REFERENCE)
    ref_v6 = summary.get(MOMENTUM_V6_COMBINED_REFERENCE)
    if not ref_v2:
        return

    comp: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE29_PROFILES:
        if pname not in summary:
            continue
        prow = _profile_exit_metrics(summary[pname], all_trades)
        adoption: dict[str, Any] = {"vs_v2": _adoption_v2(prow, ref_v2)}
        if ref_v6 and pname.startswith("momentum_volume_v7_"):
            adoption["vs_v6"] = _adoption_v6(prow, ref_v6)
        comp.append({**prow, **adoption})

    flat: list[dict[str, Any]] = []
    for r in comp:
        fr = dict(r)
        vs2 = r.get("vs_v2") or {}
        if isinstance(vs2, dict):
            for k, v in vs2.items():
                fr[k] = v
        vs6 = r.get("vs_v6") or {}
        if isinstance(vs6, dict):
            for k, v in vs6.items():
                fr[f"v6_{k}"] = v
        fr.pop("vs_v2", None)
        fr.pop("vs_v6", None)
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
        "avg_loss_pct",
        "mae_p50",
        "mae_p90",
        "mfe_ge_0_3_pct_rate",
        "mfe_ge_0_5_pct_rate",
        "board_imbalance_exit_rate",
        "hard_stop_rate",
        "structure_break_exit_rate",
        "recovery_hold_count",
        "recovery_success_rate",
        "adverse_cut_count",
        "reentry_loop_rate",
        "median_hold_min",
        "worst_day_pnl",
        "concentration_top_symbol_pct",
        "trades_per_day",
        "vs_v2_adoption_flags",
        "vs_v2_recommended",
        "v6_vs_v6_avg_pnl_improved",
        "v6_vs_v6_adoption_flags",
    )
    fields = [k for k in keys if flat and k in flat[0]]
    for fr in flat:
        for k in fr:
            if k not in fields:
                fields.append(k)

    with (out / "momentum_v7_comparison.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)

    (out / "momentum_v7_comparison.json").write_text(
        json.dumps(
            {
                "phase": 29,
                "reference_profiles": {
                    "v2": MOMENTUM_V2_REFERENCE,
                    "v5": MOMENTUM_V5_COMBINED_REFERENCE,
                    "v6": MOMENTUM_V6_COMBINED_REFERENCE,
                },
                "profiles": list(ENTRY_V2_PHASE29_PROFILES),
                "rows": comp,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
