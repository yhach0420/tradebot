"""
Phase 25: momentum_volume_v2 loss analysis and momentum v3 comparison outputs.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.entry_v2 import ENTRY_V2_PHASE25_PROFILES, MOMENTUM_V2_REFERENCE

KNOWN_ETF_SYMBOLS = frozenset({"1306.T", "1321.T", "1306", "1321"})

LIQUIDITY_BUCKETS: tuple[tuple[str, float, float | None], ...] = (
    ("low", 0.0, 50_000_000.0),
    ("mid", 50_000_000.0, 200_000_000.0),
    ("high", 200_000_000.0, None),
)


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _liquidity_bucket(mtv: Optional[float]) -> str:
    if mtv is None:
        return "unknown"
    for name, lo, hi in LIQUIDITY_BUCKETS:
        if mtv >= lo and (hi is None or mtv < hi):
            return name
    return "unknown"


def _is_etf_symbol(symbol: str) -> bool:
    base = symbol.split(".")[0].split("@")[0]
    return symbol in KNOWN_ETF_SYMBOLS or base in KNOWN_ETF_SYMBOLS


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "p50": None, "p90": None, "mean": None}
    s = sorted(values)
    n = len(s)

    def _pct(p: float) -> float:
        return s[min(n - 1, max(0, int(p * (n - 1))))]

    return {
        "count": n,
        "p50": statistics.median(s),
        "p90": _pct(0.90),
        "mean": statistics.mean(s),
    }


def _trade_group_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    pnls = [float(r["pnl_pct"]) for r in rows]
    mfes = [float(r["mfe_pct"]) for r in rows]
    maes = [float(r["mae_pct"]) for r in rows]
    holds = [float(r["hold_min"]) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "count": len(rows),
        "win_rate": wins / len(rows),
        "avg_pnl_pct": statistics.mean(pnls),
        "median_pnl_pct": statistics.median(pnls),
        "avg_mfe_pct": statistics.mean(mfes),
        "avg_mae_pct": statistics.mean(maes),
        "median_hold_min": statistics.median(holds),
        "mfe_ge_0_3_rate": sum(1 for m in mfes if m >= 0.3) / len(rows),
        "mfe_ge_0_5_rate": sum(1 for m in mfes if m >= 0.5) / len(rows),
    }


def _exit_reason_analysis(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_reason: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_reason[str(r.get("exit_reason", ""))].append(r)

    out: dict[str, Any] = {}
    for reason, grp in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        entry_imbs = [
            float(r["board_imbalance_entry"])
            for r in grp
            if r.get("board_imbalance_entry") is not None
        ]
        exit_imbs = [
            float(r["board_imbalance_exit"])
            for r in grp
            if r.get("board_imbalance_exit") is not None
        ]
        speeds = [
            float(r["imbalance_deterioration_per_min"])
            for r in grp
            if r.get("imbalance_deterioration_per_min") is not None
        ]
        block = {
            **_trade_group_stats(grp),
            "entry_board_imbalance": _distribution(entry_imbs),
            "exit_board_imbalance": _distribution(exit_imbs),
            "deterioration_speed_per_min": _distribution(speeds),
        }
        out[reason] = block
    return out


def _losers_winners_analysis(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    losers = [r for r in rows if float(r["pnl_pct"]) < 0]
    winners = [r for r in rows if float(r["pnl_pct"]) > 0]
    mfe_winners = [r for r in rows if float(r.get("mfe_pct", 0)) >= 0.3]
    mfe_big = [r for r in rows if float(r.get("mfe_pct", 0)) >= 0.5]

    def _feat(grp: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not grp:
            return {"count": 0}
        return {
            "count": len(grp),
            **_trade_group_stats(grp),
            "max_down_1m": _distribution(
                [float(r["max_down_1m_pct"]) for r in grp if r.get("max_down_1m_pct") is not None]
            ),
            "max_down_3m": _distribution(
                [float(r["max_down_3m_pct"]) for r in grp if r.get("max_down_3m_pct") is not None]
            ),
            "spread_bps": _distribution(
                [float(r["entry_spread_bps"]) for r in grp if r.get("entry_spread_bps") is not None]
            ),
            "minute_tv": _distribution(
                [
                    float(r["entry_minute_trading_value"])
                    for r in grp
                    if r.get("entry_minute_trading_value") is not None
                ]
            ),
            "momentum": _distribution(
                [
                    float(r["entry_price_momentum_pct"])
                    for r in grp
                    if r.get("entry_price_momentum_pct") is not None
                ]
            ),
            "vwap_dist": _distribution(
                [
                    float(r["entry_vwap_distance_pct"])
                    for r in grp
                    if r.get("entry_vwap_distance_pct") is not None
                ]
            ),
            "entry_imbalance": _distribution(
                [
                    float(r["board_imbalance_entry"])
                    for r in grp
                    if r.get("board_imbalance_entry") is not None
                ]
            ),
        }

    mae_before_mfe = sum(
        1
        for r in losers
        if float(r.get("mae_pct", 0)) < -0.05
        and float(r.get("mfe_pct", 0)) < 0.1
    )

    return {
        "losers": _feat(losers),
        "winners": _feat(winners),
        "mfe_ge_0_3": _feat(mfe_winners),
        "mfe_ge_0_5": _feat(mfe_big),
        "mae_before_mfe_count": mae_before_mfe,
        "mae_before_mfe_rate": (mae_before_mfe / len(losers)) if losers else None,
    }


def _generalization_check(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_day: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_sym: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_liq: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_etf: dict[str, list[Mapping[str, Any]]] = defaultdict(list)

    for r in rows:
        by_day[str(r["trade_date"])].append(r)
        sym = str(r["symbol"])
        by_sym[sym].append(r)
        mtv = _as_float(r.get("entry_minute_trading_value"))
        by_liq[_liquidity_bucket(mtv)].append(r)
        by_etf["etf" if _is_etf_symbol(sym) else "equity"].append(r)

    def _summ(groups: dict[str, list]) -> list[dict[str, Any]]:
        out_list = []
        for key, grp in sorted(groups.items()):
            pnls = [float(x["pnl_pct"]) for x in grp]
            out_list.append(
                {
                    "key": key,
                    "trades": len(grp),
                    "total_pnl_pct": sum(pnls),
                    "avg_pnl_pct": statistics.mean(pnls) if pnls else None,
                    "win_rate": sum(1 for p in pnls if p > 0) / len(pnls) if pnls else None,
                }
            )
        return out_list

    sym_counts = Counter(str(r["symbol"]) for r in rows)
    top_sym, top_n = sym_counts.most_common(1)[0] if sym_counts else ("", 0)
    conc = top_n / len(rows) if rows else None

    return {
        "by_day": _summ(by_day),
        "by_symbol": _summ(by_sym),
        "by_liquidity_bucket": _summ(by_liq),
        "by_etf_equity": _summ(by_etf),
        "concentration_top_symbol": top_sym,
        "concentration_top_symbol_pct": conc,
        "symbols_with_trades": len(by_sym),
    }


def build_momentum_v2_loss_analysis(
    trade_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = list(trade_rows)
    imb_rows = [r for r in rows if str(r.get("exit_reason")) == "board_imbalance_deterioration"]
    return {
        "profile": MOMENTUM_V2_REFERENCE,
        "trade_count": len(rows),
        "summary": _trade_group_stats(rows),
        "exit_reason_analysis": _exit_reason_analysis(rows),
        "board_imbalance_deterioration_focus": _trade_group_stats(imb_rows),
        "losers_winners": _losers_winners_analysis(rows),
        "generalization": _generalization_check(rows),
        "diagnosis_notes": _diagnosis_notes(rows, imb_rows),
    }


def _diagnosis_notes(
    all_rows: Sequence[Mapping[str, Any]],
    imb_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    notes: list[str] = []
    if not all_rows:
        notes.append("no_trades_for_analysis")
        return notes
    imb_rate = len(imb_rows) / len(all_rows)
    notes.append(f"board_imbalance_exit_share={imb_rate:.2%}")
    if imb_rows:
        avg_pnl = statistics.mean(float(r["pnl_pct"]) for r in imb_rows)
        avg_mfe = statistics.mean(float(r["mfe_pct"]) for r in imb_rows)
        notes.append(f"imb_exit_avg_pnl={avg_pnl:.4f}")
        notes.append(f"imb_exit_avg_mfe={avg_mfe:.4f}")
        if avg_mfe >= 0.15 and avg_pnl < 0:
            notes.append("imb_exit_likely_exit_timing_not_entry_quality")
        elif avg_mfe < 0.1:
            notes.append("imb_exit_may_include_entry_quality_issues")
    losers = [r for r in all_rows if float(r["pnl_pct"]) < 0]
    early_drop = sum(
        1
        for r in losers
        if _as_float(r.get("max_down_1m_pct")) is not None and float(r["max_down_1m_pct"]) < -0.15
    )
    if losers and early_drop / len(losers) > 0.4:
        notes.append("losers_often_adverse_1m_suggests_entry_guard")
    return notes


def _profile_comparison_row(profile_row: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "profile",
        "entry_count",
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
        "breakout_failure_rate",
        "board_imbalance_exit_rate",
        "median_hold_min",
        "losing_day_count",
        "worst_day_pnl",
        "concentration_top_symbol_pct",
    )
    row = {k: profile_row.get(k) for k in keys}
    row["trades"] = profile_row.get("entry_count")
    flags: list[str] = []

    def _f(key: str) -> Optional[float]:
        v = profile_row.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    def _rf(key: str) -> Optional[float]:
        v = ref.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    pf, rpf = _f("profit_factor"), _rf("profit_factor")
    ap, rap = _f("avg_pnl_pct"), _rf("avg_pnl_pct")
    ml, rml = _f("max_loss_pct"), _rf("max_loss_pct")
    mfe, rmfe = _f("mfe_ge_0_3_pct_rate"), _rf("mfe_ge_0_3_pct_rate")
    bf, rbf = _f("board_imbalance_exit_rate"), _rf("board_imbalance_exit_rate")
    ec, rec = profile_row.get("entry_count"), ref.get("entry_count")
    swt, rswt = profile_row.get("symbols_with_trades"), ref.get("symbols_with_trades")

    if pf is not None and rpf is not None and pf <= rpf:
        flags.append("pf_not_improved")
    if ap is not None and rap is not None and ap <= rap:
        flags.append("avg_pnl_not_improved")
    if ml is not None and rml is not None and ml < rml:
        flags.append("max_loss_worse")
    if mfe is not None and rmfe is not None and mfe < rmfe - 0.01:
        flags.append("mfe_0_3_degraded")
    if bf is not None and rbf is not None and bf > rbf + 0.02:
        flags.append("imb_exit_rate_worse")
    if isinstance(swt, int) and isinstance(rswt, int) and rswt > 0 and swt < max(1, int(rswt * 0.5)):
        flags.append("symbols_with_trades_dropped")
    if (ec or 0) < (rec or 0) * 0.6 and pf is not None and rpf is not None and pf > rpf:
        flags.append("trade_count_only_reduction_improvement")
    if (ec or 0) > (rec or 0) and pf is not None and rpf is not None and pf < rpf:
        flags.append("trade_count_up_pf_down")
    if (profile_row.get("concentration_top_symbol_pct") or 0) > 0.5:
        flags.append("symbol_concentration_high")

    row["vs_reference"] = {
        "reference_profile": MOMENTUM_V2_REFERENCE,
        "adoption_flags": flags,
        "recommended": len(flags) == 0 and pf is not None and rpf is not None and pf >= rpf,
    }
    return row


def _extended_profile_metrics(
    profile_row: dict[str, Any],
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    num_days: int,
) -> dict[str, Any]:
    rows = [r for r in trade_rows if str(r.get("profile")) == profile_row.get("profile")]
    maes = [float(r["mae_pct"]) for r in rows]
    daily = _generalization_check(rows)["by_day"] if rows else []
    losing_days = sum(1 for d in daily if (d.get("total_pnl_pct") or 0) < 0)
    worst = min(daily, key=lambda x: x.get("total_pnl_pct", 0)) if daily else {}
    imb_n = sum(1 for r in rows if str(r.get("exit_reason")) == "board_imbalance_deterioration")
    s = sorted(maes) if maes else []
    mae_p90 = s[min(len(s) - 1, int(0.9 * (len(s) - 1)))] if len(s) > 1 else (s[0] if s else None)
    return {
        **profile_row,
        "mae_p50": statistics.median(maes) if maes else None,
        "mae_p90": mae_p90,
        "board_imbalance_exit_rate": (imb_n / len(rows)) if rows else None,
        "losing_day_count": losing_days,
        "worst_day_pnl": worst.get("total_pnl_pct"),
    }


def write_momentum_phase25_outputs(
    out: Path,
    *,
    by_profile: dict[str, list[Any]],
    profile_summaries: list[dict[str, Any]],
) -> None:
    v2_rows: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE25_PROFILES:
        if pname not in by_profile:
            continue
        for r in by_profile[pname]:
            all_rows.extend(getattr(r, "enriched_trade_rows", []))
        if pname == MOMENTUM_V2_REFERENCE:
            for r in by_profile[pname]:
                v2_rows.extend(getattr(r, "enriched_trade_rows", []))

    if v2_rows:
        analysis = build_momentum_v2_loss_analysis(v2_rows)
        (out / "momentum_v2_loss_analysis.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        csv_row = {
            "profile": MOMENTUM_V2_REFERENCE,
            "trades": analysis["trade_count"],
            "imb_exit_count": analysis["exit_reason_analysis"]
            .get("board_imbalance_deterioration", {})
            .get("count", 0),
            "imb_exit_avg_pnl": analysis["board_imbalance_deterioration_focus"].get("avg_pnl_pct"),
            "imb_exit_median_pnl": analysis["board_imbalance_deterioration_focus"].get(
                "median_pnl_pct"
            ),
            "imb_exit_avg_mfe": analysis["board_imbalance_deterioration_focus"].get("avg_mfe_pct"),
            "losers_count": analysis["losers_winners"]["losers"].get("count", 0),
            "winners_count": analysis["losers_winners"]["winners"].get("count", 0),
            "mfe_0_3_winners": analysis["losers_winners"]["mfe_ge_0_3"].get("count", 0),
            "concentration_top_symbol_pct": analysis["generalization"].get(
                "concentration_top_symbol_pct"
            ),
            "diagnosis": ";".join(analysis.get("diagnosis_notes", [])),
        }
        with (out / "momentum_v2_loss_analysis.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_row.keys()))
            w.writeheader()
            w.writerow(csv_row)

    summary_by = {str(r["profile"]): r for r in profile_summaries}
    ref = summary_by.get(MOMENTUM_V2_REFERENCE)
    if not ref:
        return

    num_days = max(
        len({r.get("trade_date") for r in v2_rows}),
        1,
    )
    comp_rows: list[dict[str, Any]] = []
    for pname in ENTRY_V2_PHASE25_PROFILES:
        if pname not in summary_by:
            continue
        prow = _extended_profile_metrics(summary_by[pname], all_rows, num_days=num_days)
        comp_rows.append(_profile_comparison_row(prow, ref))

    if not comp_rows:
        return

    flat: list[dict[str, Any]] = []
    for r in comp_rows:
        flat_row = {k: v for k, v in r.items() if k != "vs_reference"}
        vs = r.get("vs_reference") or {}
        if isinstance(vs, dict):
            for vk, vv in vs.items():
                flat_row[f"vs_{vk}"] = vv
        flat.append(flat_row)

    fields = list(flat[0].keys())
    with (out / "momentum_v3_comparison.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)

    (out / "momentum_v3_comparison.json").write_text(
        json.dumps(
            {
                "phase": 25,
                "reference_profile": MOMENTUM_V2_REFERENCE,
                "profiles": ENTRY_V2_PHASE25_PROFILES,
                "rows": comp_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
