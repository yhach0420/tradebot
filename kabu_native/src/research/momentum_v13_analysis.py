"""
Phase 35: momentum v13 continuation-momentum comparison vs v12.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.continuation_momentum_analysis import build_continuation_momentum_analysis
from research.entry_v2 import (
    ENTRY_V2_PHASE35_PROFILES,
    MOMENTUM_V12_COMBINED_REFERENCE,
    MOMENTUM_V2_REFERENCE,
)


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
    mexit = sum(
        1
        for t in grp
        if str(t.get("exit_reason", "")).startswith(
            ("momentum_", "bearish_accumulation", "continuation_weakness")
        )
    )
    reentry = sum(int(t.get("reentry_blocked") or 0) for t in grp)

    def _mean(key: str) -> float | None:
        vals = [_as_float(t.get(key)) for t in grp]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    held = [t for t in grp if int(t.get("momentum_hold_events") or 0) > 0]
    held_ok = sum(1 for t in held if float(t.get("pnl_pct", 0)) > 0)

    def _dur_mean() -> float | None:
        vals = [_as_float(t.get("max_momentum_continuation_duration")) for t in grp]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    return {
        **row,
        "trades": row.get("entry_count"),
        "imbalance_exit_rate": (imb / n) if n else row.get("board_imbalance_exit_rate"),
        "momentum_continuation_score_mean": _mean("momentum_continuation_score"),
        "continuation_duration_mean": _dur_mean(),
        "continuation_decay_rate": sum(1 for t in grp if t.get("momentum_decay_detected")) / n,
        "continuation_weakness_rate": sum(
            1 for t in grp if int(t.get("continuation_weakness_ticks") or 0) >= 4
        )
        / n,
        "bearish_accumulation_rate": sum(
            1 for t in grp if int(t.get("bearish_accumulation_ticks") or 0) >= 4
        )
        / n,
        "continuation_hold_success_rate": (held_ok / len(held)) if held else None,
        "continuation_false_hold_rate": (
            (len(held) - held_ok) / len(held) if held else None
        ),
        "fixed_time_proxy_rate": sum(1 for t in grp if t.get("fixed_time_proxy_fired")) / n,
        "transition_exit_rate": (mexit / n) if n else None,
        "reentry_loop_rate": (reentry / n) if n else None,
    }


def _adoption_v12(row: dict[str, Any], refs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    flags: list[str] = []

    def _f(k: str) -> float | None:
        return _as_float(row.get(k))

    pf = _f("profit_factor")
    ap = _f("avg_pnl_pct")
    ml = _f("max_loss_pct")
    mfe3 = _f("mfe_ge_0_3_pct_rate")
    hs = _f("hard_stop_rate")
    ec = row.get("entry_count")
    swt = row.get("symbols_with_trades")
    rel = _f("reentry_loop_rate")
    ftp = _f("fixed_time_proxy_rate")
    false_hold = _f("continuation_false_hold_rate")
    mom_score = _f("momentum_continuation_score_mean")

    for label, ref in refs.items():
        rpf = _as_float(ref.get("profit_factor"))
        rap = _as_float(ref.get("avg_pnl_pct"))
        rml = _as_float(ref.get("max_loss_pct"))
        rmfe3 = _as_float(ref.get("mfe_ge_0_3_pct_rate"))
        rhs = _as_float(ref.get("hard_stop_rate"))
        rec = ref.get("entry_count")
        rswt = ref.get("symbols_with_trades")
        rrel = _as_float(ref.get("reentry_loop_rate"))
        rftp = _as_float(ref.get("fixed_time_proxy_rate"))
        rfalse = _as_float(ref.get("continuation_false_hold_rate"))
        rmom = _as_float(ref.get("momentum_continuation_score_mean"))

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
        if isinstance(swt, int) and isinstance(rswt, int) and rswt > 0 and swt < max(1, int(rswt * 0.5)):
            flags.append(f"symbols_dropped_vs_{label}")
        if (ec or 0) < (rec or 0) * 0.6 and pf is not None and rpf is not None and pf > rpf:
            flags.append("trade_count_only_reduction_improvement")
        if rel is not None and rrel is not None and rel > rrel + 0.05:
            flags.append("reentry_loop_rate_worse")
        if label == "v12" and ftp is not None and rftp is not None and ftp > rftp + 0.03:
            flags.append("fixed_time_dependency_increased")
        if label == "v12" and false_hold is not None and rfalse is not None and false_hold > rfalse + 0.05:
            flags.append("false_hold_increased")
        if label == "v12" and mom_score is not None and rmom is not None and mom_score < rmom - 0.03:
            flags.append("momentum_continuation_degraded")

    v12 = refs.get("v12", {})
    v12pf = _as_float(v12.get("profit_factor"))
    recommended = (
        len(flags) == 0
        and pf is not None
        and v12pf is not None
        and pf >= v12pf
        and ap is not None
        and _as_float(v12.get("avg_pnl_pct")) is not None
        and ap > _as_float(v12.get("avg_pnl_pct"))
    )

    return {"adoption_flags": flags, "recommended": recommended}


def write_momentum_phase35_outputs(
    out: Path,
    *,
    by_profile: dict[str, list[Any]],
    profile_summaries: list[dict[str, Any]],
) -> None:
    all_trades: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE35_PROFILES:
        if pname not in by_profile:
            continue
        for r in by_profile[pname]:
            all_trades.extend(getattr(r, "enriched_trade_rows", []))

    (out / "continuation_momentum_analysis.json").write_text(
        json.dumps(build_continuation_momentum_analysis(all_trades), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary = {str(r["profile"]): r for r in profile_summaries}
    refs: dict[str, dict[str, Any]] = {}
    for label, pname in (("v2", MOMENTUM_V2_REFERENCE), ("v12", MOMENTUM_V12_COMBINED_REFERENCE)):
        if pname in summary:
            refs[label] = _profile_metrics(summary[pname], all_trades)
    if "v12" not in refs:
        return

    comp: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE35_PROFILES:
        if pname not in summary:
            continue
        prow = _profile_metrics(summary[pname], all_trades)
        if pname.startswith("momentum_volume_v13_"):
            comp.append({**prow, **_adoption_v12(prow, refs)})
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
        "momentum_continuation_score_mean",
        "continuation_duration_mean",
        "continuation_decay_rate",
        "continuation_weakness_rate",
        "bearish_accumulation_rate",
        "continuation_hold_success_rate",
        "continuation_false_hold_rate",
        "fixed_time_proxy_rate",
        "reentry_loop_rate",
        "median_hold_min",
        "worst_day_pnl",
        "concentration_top_symbol_pct",
        "adoption_flags",
        "recommended",
    )
    fields = [k for k in keys if flat and k in flat[0]]
    for fr in flat:
        for k in fr:
            if k not in fields:
                fields.append(k)

    with (out / "momentum_v13_comparison.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)

    (out / "momentum_v13_comparison.json").write_text(
        json.dumps(
            {
                "phase": 35,
                "reference_profiles": {
                    "v2": MOMENTUM_V2_REFERENCE,
                    "v12": MOMENTUM_V12_COMBINED_REFERENCE,
                },
                "profiles": list(ENTRY_V2_PHASE35_PROFILES),
                "rows": comp,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
