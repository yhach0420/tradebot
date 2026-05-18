"""
Phase 24: ENTRY v2 deep-dive diagnostics for pullback / momentum focus profiles.

Outputs per-profile distributions, daily/symbol breakdowns, and enriched trade rows.
No per-symbol/day/time optimization — aggregation only.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.entry_v2 import (
    ENTRY_V2_DEEP_DIVE_PROFILES,
    ENTRY_V2_PHASE24_PROFILES,
    ENTRY_V2_REFERENCE_PROFILE,
)

HORIZONS_SEC: tuple[tuple[str, float], ...] = (
    ("1m", 60.0),
    ("3m", 180.0),
    ("5m", 300.0),
)


def _pct_change(current: float, base: float) -> float:
    if base <= 0:
        return 0.0
    return ((float(current) - float(base)) / float(base)) * 100.0


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def post_entry_horizons(
    cache: Any,
    entry_idx: int,
    entry_px: float,
) -> dict[str, Optional[float]]:
    """Max up/down % within 1m / 3m / 5m after entry."""
    out: dict[str, Optional[float]] = {}
    if cache is None or entry_idx < 0 or entry_idx >= len(cache.ts_sec):
        for label, _ in HORIZONS_SEC:
            out[f"max_up_{label}_pct"] = None
            out[f"max_down_{label}_pct"] = None
        return out

    t0 = cache.ts_sec[entry_idx]
    for label, horizon in HORIZONS_SEC:
        t_limit = t0 + horizon
        max_px = entry_px
        min_px = entry_px
        found = False
        for j in range(entry_idx + 1, len(cache.events)):
            if cache.ts_sec[j] > t_limit:
                break
            px = cache.prices[j]
            if px is None:
                continue
            found = True
            max_px = max(max_px, px)
            min_px = min(min_px, px)
        if found:
            out[f"max_up_{label}_pct"] = _pct_change(max_px, entry_px)
            out[f"max_down_{label}_pct"] = _pct_change(min_px, entry_px)
        else:
            out[f"max_up_{label}_pct"] = None
            out[f"max_down_{label}_pct"] = None
    return out


def build_enriched_trade_row(
    *,
    profile: str,
    trade_date: str,
    symbol: str,
    trade: Any,
    entry_snap: Mapping[str, Any],
    entry_idx: int,
    forward_cache: Any,
    exit_snap: Optional[Mapping[str, Any]] = None,
    early_snap: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    horizons = post_entry_horizons(forward_cache, entry_idx, float(trade.entry_price))
    entry_imb = entry_snap.get("board_imbalance_entry")
    exit_imb = (exit_snap or {}).get("board_imbalance_exit")
    hold = float(trade.elapsed_min)
    imb_drop = None
    imb_speed = None
    if entry_imb is not None and exit_imb is not None:
        imb_drop = float(entry_imb) - float(exit_imb)
        if hold > 0:
            imb_speed = imb_drop / hold
    return {
        "profile": profile,
        "trade_date": trade_date,
        "symbol": symbol,
        "entry_time": trade.entry_time.isoformat(),
        "exit_time": trade.exit_time.isoformat(),
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "pnl_pct": round(float(trade.pnl_pct), 6),
        "exit_reason": trade.exit_reason,
        "mfe_pct": round(float(trade.max_favorable_excursion_pct), 6),
        "mae_pct": round(float(trade.max_adverse_excursion_pct), 6),
        "hold_min": round(hold, 4),
        "entry_vwap_distance_pct": entry_snap.get("vwap_distance_pct"),
        "entry_minute_trading_value": entry_snap.get("minute_trading_value"),
        "entry_price_momentum_pct": entry_snap.get("price_momentum_pct"),
        "entry_volume_increase_ratio": entry_snap.get("volume_increase_ratio"),
        "entry_v2_score": entry_snap.get("entry_v2_score"),
        "entry_spread_bps": entry_snap.get("spread_bps"),
        "pullback_depth_pct": entry_snap.get("pullback_depth_pct"),
        "board_imbalance_entry": entry_imb,
        "board_imbalance_exit": exit_imb,
        "board_imbalance_min_since_entry": (exit_snap or {}).get("board_imbalance_min_since_entry"),
        "imbalance_deterioration": imb_drop,
        "imbalance_deterioration_per_min": imb_speed,
        "exit_imbalance_low_streak": (exit_snap or {}).get("imbalance_low_streak"),
        **(early_snap or {}),
        **horizons,
    }


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "p50": None, "p75": None, "p90": None, "mean": None}
    s = sorted(values)
    n = len(s)

    def _pct(p: float) -> float:
        idx = min(n - 1, max(0, int(p * (n - 1))))
        return s[idx]

    return {
        "count": n,
        "p50": statistics.median(s),
        "p75": _pct(0.75),
        "p90": _pct(0.90),
        "mean": statistics.mean(s),
    }


def _daily_pnl(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    by_day: dict[str, float] = defaultdict(float)
    for r in rows:
        by_day[str(r["trade_date"])] += float(r.get("pnl_pct") or 0)
    return dict(by_day)


@dataclass
class ProfileDeepDive:
    profile: str
    trade_rows: list[dict[str, Any]]
    profile_summary: dict[str, Any]

    def build(self) -> dict[str, Any]:
        rows = self.trade_rows
        n = len(rows)
        daily = _daily_pnl(rows)
        losing_days = sum(1 for v in daily.values() if v < 0)
        worst_day = min(daily.items(), key=lambda x: x[1]) if daily else ("", 0.0)

        by_sym: dict[str, list[float]] = defaultdict(list)
        exit_reasons: Counter[str] = Counter()
        mfes: list[float] = []
        maes: list[float] = []
        holds: list[float] = []
        vwap_dists: list[float] = []
        mtvs: list[float] = []
        moms: list[float] = []

        for r in rows:
            exit_reasons[str(r.get("exit_reason", ""))] += 1
            mfes.append(float(r["mfe_pct"]))
            maes.append(float(r["mae_pct"]))
            holds.append(float(r["hold_min"]))
            vd = _as_float(r.get("entry_vwap_distance_pct"))
            if vd is not None:
                vwap_dists.append(vd)
            mtv = _as_float(r.get("entry_minute_trading_value"))
            if mtv is not None:
                mtvs.append(mtv)
            mom = _as_float(r.get("entry_price_momentum_pct"))
            if mom is not None:
                moms.append(mom)
            by_sym[str(r["symbol"])].append(float(r["pnl_pct"]))

        sym_summary = []
        for sym, pnls in sorted(by_sym.items()):
            sym_summary.append(
                {
                    "symbol": sym,
                    "trades": len(pnls),
                    "total_pnl_pct": sum(pnls),
                    "win_rate": sum(1 for p in pnls if p > 0) / len(pnls) if pnls else None,
                    "avg_pnl_pct": statistics.mean(pnls) if pnls else None,
                }
            )

        day_summary = []
        for day, pnl in sorted(daily.items()):
            day_rows = [r for r in rows if r["trade_date"] == day]
            day_summary.append(
                {
                    "trade_date": day,
                    "trades": len(day_rows),
                    "total_pnl_pct": pnl,
                    "win_rate": sum(1 for r in day_rows if float(r["pnl_pct"]) > 0) / len(day_rows)
                    if day_rows
                    else None,
                }
            )

        ps = self.profile_summary
        return {
            "profile": self.profile,
            "trades": n,
            "symbols_with_trades": ps.get("symbols_with_trades"),
            "concentration_top_symbol": ps.get("concentration_top_symbol"),
            "concentration_top_symbol_pct": ps.get("concentration_top_symbol_pct"),
            "losing_day_count": losing_days,
            "worst_day_pnl": worst_day[1] if daily else None,
            "worst_day": worst_day[0] if daily else "",
            "exit_reason_distribution": dict(exit_reasons),
            "mfe_distribution": _distribution(mfes),
            "mae_distribution": _distribution(maes),
            "hold_min_distribution": _distribution(holds),
            "entry_vwap_distance_distribution": _distribution(vwap_dists),
            "entry_minute_tv_distribution": _distribution(mtvs),
            "entry_momentum_distribution": _distribution(moms),
            "post_entry_max_up": {
                label: _distribution(
                    [float(r[f"max_up_{label}_pct"]) for r in rows if r.get(f"max_up_{label}_pct") is not None]
                )
                for label, _ in HORIZONS_SEC
            },
            "post_entry_max_down": {
                label: _distribution(
                    [
                        float(r[f"max_down_{label}_pct"])
                        for r in rows
                        if r.get(f"max_down_{label}_pct") is not None
                    ]
                )
                for label, _ in HORIZONS_SEC
            },
            "daily_summary": day_summary,
            "symbol_summary": sym_summary,
            "profit_factor": ps.get("profit_factor"),
            "avg_pnl_pct": ps.get("avg_pnl_pct"),
            "max_loss_pct": ps.get("max_loss_pct"),
            "mfe_ge_0_3_pct_rate": ps.get("mfe_ge_0_3_pct_rate"),
            "breakout_failure_rate": ps.get("breakout_failure_rate"),
        }


def _adoption_vs_reference(
    profile_row: dict[str, Any],
    ref_row: dict[str, Any],
) -> dict[str, Any]:
    flags: list[str] = []

    def _f(key: str) -> Optional[float]:
        v = profile_row.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    def _rf(key: str) -> Optional[float]:
        v = ref_row.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    pf, rpf = _f("profit_factor"), _rf("profit_factor")
    ap, rap = _f("avg_pnl_pct"), _rf("avg_pnl_pct")
    ml, rml = _f("max_loss_pct"), _rf("max_loss_pct")
    mfe, rmfe = _f("mfe_ge_0_3_pct_rate"), _rf("mfe_ge_0_3_pct_rate")
    bf, rbf = _f("breakout_failure_rate"), _rf("breakout_failure_rate")
    swt, rswt = profile_row.get("symbols_with_trades"), ref_row.get("symbols_with_trades")
    ec, rec = profile_row.get("entry_count"), ref_row.get("entry_count")

    if pf is not None and rpf is not None and pf <= rpf:
        flags.append("pf_not_improved")
    if ap is not None and rap is not None and ap <= rap:
        flags.append("avg_pnl_not_improved")
    if ml is not None and rml is not None and ml < rml:
        flags.append("max_loss_worse")
    if mfe is not None and rmfe is not None and mfe < rmfe - 0.001:
        flags.append("mfe_0_3_not_improved")
    if bf is not None and rbf is not None and bf > rbf + 0.02:
        flags.append("bf_rate_worse")
    if isinstance(swt, int) and isinstance(rswt, int) and rswt > 0 and swt < max(1, int(rswt * 0.5)):
        flags.append("symbols_with_trades_dropped")
    if (ec or 0) > (rec or 0) and pf is not None and rpf is not None and pf < rpf:
        flags.append("trade_count_up_pf_down")
    if (profile_row.get("concentration_top_symbol_pct") or 0) > 0.5:
        flags.append("symbol_concentration_high")

    return {
        "reference_profile": ref_row.get("profile"),
        "adoption_flags": flags,
        "recommended": len(flags) == 0 and pf is not None and rpf is not None and pf >= rpf,
    }


def _comparison_row_vs_reference(
    profile_row: dict[str, Any],
    ref_row: dict[str, Any] | None,
) -> dict[str, Any]:
    keys = (
        "profile",
        "eval_count",
        "candidate_count",
        "entry_count",
        "trades_per_day",
        "symbols_with_trades",
        "win_rate",
        "total_pnl_pct",
        "avg_pnl_pct",
        "median_pnl_pct",
        "profit_factor",
        "max_loss_pct",
        "avg_loss_pct",
        "mfe_ge_0_3_pct_rate",
        "mfe_ge_0_5_pct_rate",
        "avg_mfe_pct",
        "avg_mae_pct",
        "breakout_failure_rate",
        "median_hold_min",
        "concentration_top_symbol",
        "concentration_top_symbol_pct",
    )
    row = {k: profile_row.get(k) for k in keys}
    ref_name = ENTRY_V2_REFERENCE_PROFILE.get(str(profile_row.get("profile", "")))
    row["reference_profile"] = ref_name
    if ref_row is None:
        row["vs_reference"] = {}
        return row
    row["vs_reference"] = _adoption_vs_reference(profile_row, ref_row)
    return row


def write_entry_v2_phase24_outputs(
    out: Path,
    *,
    by_profile: dict[str, list[Any]],
    profile_summaries: list[dict[str, Any]],
) -> None:
    summary_by_name = {str(r["profile"]): r for r in profile_summaries}
    all_enriched: list[dict[str, Any]] = []
    deep_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    symbol_rows: list[dict[str, Any]] = []

    for pname in ENTRY_V2_PHASE24_PROFILES:
        if pname not in by_profile:
            continue
        trade_rows: list[dict[str, Any]] = []
        for r in by_profile[pname]:
            trade_rows.extend(getattr(r, "enriched_trade_rows", []))
        all_enriched.extend(trade_rows)
        ps = summary_by_name.get(pname, {"profile": pname})
        dive = ProfileDeepDive(profile=pname, trade_rows=trade_rows, profile_summary=ps).build()
        deep_rows.append(
            {
                "profile": pname,
                "trades": dive["trades"],
                "symbols_with_trades": dive["symbols_with_trades"],
                "losing_day_count": dive["losing_day_count"],
                "worst_day": dive["worst_day"],
                "worst_day_pnl": dive["worst_day_pnl"],
                "concentration_top_symbol": dive["concentration_top_symbol"],
                "concentration_top_symbol_pct": dive["concentration_top_symbol_pct"],
                "profit_factor": dive["profit_factor"],
                "avg_pnl_pct": dive["avg_pnl_pct"],
                "max_loss_pct": dive["max_loss_pct"],
                "mfe_ge_0_3_pct_rate": dive["mfe_ge_0_3_pct_rate"],
                "breakout_failure_rate": dive["breakout_failure_rate"],
                "exit_reason_top": max(dive["exit_reason_distribution"], key=dive["exit_reason_distribution"].get)
                if dive["exit_reason_distribution"]
                else "",
                "mfe_p50": dive["mfe_distribution"].get("p50"),
                "mae_p50": dive["mae_distribution"].get("p50"),
                "hold_min_p50": dive["hold_min_distribution"].get("p50"),
                "entry_vwap_dist_p50": dive["entry_vwap_distance_distribution"].get("p50"),
                "entry_momentum_p50": dive["entry_momentum_distribution"].get("p50"),
            }
        )
        for d in dive["daily_summary"]:
            daily_rows.append({"profile": pname, **d})
        for s in dive["symbol_summary"]:
            symbol_rows.append({"profile": pname, **s})

    if not deep_rows:
        return

    # entry_v2_deep_dive.csv
    df_fields = list(deep_rows[0].keys())
    with (out / "entry_v2_deep_dive.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=df_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(deep_rows)

    # full json with distributions for deep-dive profiles only
    profiles_json: dict[str, Any] = {}
    for pname in ENTRY_V2_DEEP_DIVE_PROFILES:
        if pname not in by_profile:
            continue
        trade_rows = []
        for r in by_profile[pname]:
            trade_rows.extend(getattr(r, "enriched_trade_rows", []))
        ps = summary_by_name.get(pname, {"profile": pname})
        profiles_json[pname] = ProfileDeepDive(
            profile=pname, trade_rows=trade_rows, profile_summary=ps
        ).build()

    (out / "entry_v2_deep_dive.json").write_text(
        json.dumps(
            {
                "phase": 24,
                "deep_dive_profiles": list(ENTRY_V2_DEEP_DIVE_PROFILES),
                "phase24_profiles": list(ENTRY_V2_PHASE24_PROFILES),
                "profiles": profiles_json,
                "summary_table": deep_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if all_enriched:
        tf = list(all_enriched[0].keys())
        with (out / "entry_v2_candidate_trades.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=tf, extrasaction="ignore")
            w.writeheader()
            w.writerows(all_enriched)

    if daily_rows:
        with (out / "entry_v2_daily_summary.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(daily_rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(daily_rows)

    if symbol_rows:
        with (out / "entry_v2_symbol_summary.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(symbol_rows[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(symbol_rows)

    # Phase 24 comparison (vs v1 references, not baseline)
    ref_rows = {str(r["profile"]): r for r in profile_summaries}
    comp_rows: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE24_PROFILES:
        if pname not in ref_rows:
            continue
        ref_name = ENTRY_V2_REFERENCE_PROFILE.get(pname, pname)
        comp_row = _comparison_row_vs_reference(ref_rows[pname], ref_rows.get(ref_name))
        if pname == "hybrid_vwap_momentum_v1":
            mom_ref = ref_rows.get("momentum_volume_v1")
            if mom_ref:
                comp_row["vs_momentum_volume_v1"] = _adoption_vs_reference(
                    ref_rows[pname], mom_ref
                )
        comp_rows.append(comp_row)

    if comp_rows:
        flat: list[dict[str, Any]] = []
        for r in comp_rows:
            flat_row = {
                k: v
                for k, v in r.items()
                if k not in ("vs_reference", "vs_momentum_volume_v1")
            }
            vs = r.get("vs_reference") or {}
            if isinstance(vs, dict):
                for vk, vv in vs.items():
                    flat_row[f"vs_{vk}"] = vv
            vs_mom = r.get("vs_momentum_volume_v1")
            if isinstance(vs_mom, dict):
                for vk, vv in vs_mom.items():
                    flat_row[f"vs_mom_{vk}"] = vv
            flat.append(flat_row)
        fields = list(flat[0].keys())
        with (out / "entry_v2_comparison.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(flat)
        (out / "entry_v2_comparison.json").write_text(
            json.dumps(
                {
                    "phase": 24,
                    "description": "ENTRY v2 Phase24 comparison vs pullback_vwap_v1 / momentum_volume_v1",
                    "reference_map": ENTRY_V2_REFERENCE_PROFILE,
                    "rows": comp_rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
