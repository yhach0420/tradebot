"""
Phase632: PBv2 Profit Filter Counterfactual (research only).

Applies post-accept filters inspired by Phase631 profit sources.
Does NOT change ENTRY / PBv2 score formulas — counterfactual keep/drop only.
"""

from __future__ import annotations

import csv
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from research.phase631_profit_source_attribution import (
    DAYS,
    REPLAY_ROOT,
    load_all_trades,
)

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase632_pbv2_profit_filter_counterfactual"
PHASE632_VERDICT = "phase632_pbv2_profit_filter_counterfactual_done"
PHASE632_FAIL = "phase632_pbv2_profit_filter_counterfactual_failed"
MAX_WORKERS = 4
DISK_USAGE_MAX_PCT = 76.0
# Cap blocked-trade analysis rows (no full candidate dump).
BLOCKED_ANALYSIS_MAX_ROWS = 500


FilterFn = Callable[[dict[str, Any]], bool]


def _disk_usage_pct(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return 100.0 * (1.0 - usage.free / usage.total)


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pass_missing_ok(pred: bool, val: Optional[float], *, fail_closed: bool = False) -> bool:
    """If feature missing: fail_closed=False keeps trade (baseline-compatible)."""
    if val is None:
        return not fail_closed
    return pred


@dataclass(frozen=True)
class Variant:
    variant_id: str
    label: str
    kind: str  # baseline | single | combo
    keep: FilterFn


def _build_variants() -> list[Variant]:
    def tv_ge_1e8(t: dict[str, Any]) -> bool:
        v = _num(t.get("trading_value"))
        return _pass_missing_ok(v is not None and v >= 1e8, v)

    def price_age_le(max_age: float) -> FilterFn:
        def _f(t: dict[str, Any]) -> bool:
            v = _num(t.get("price_age_sec"))
            return _pass_missing_ok(v is not None and v <= max_age, v)

        return _f

    def update_count_ge(min_u: float) -> FilterFn:
        def _f(t: dict[str, Any]) -> bool:
            v = _num(t.get("update_count_before_entry"))
            return _pass_missing_ok(v is not None and v >= min_u, v)

        return _f

    def turnover_ge(min_t: float) -> FilterFn:
        def _f(t: dict[str, Any]) -> bool:
            v = _num(t.get("turnover_proxy"))
            return _pass_missing_ok(v is not None and v >= min_t, v)

        return _f

    def rise10_ge(min_r: float) -> FilterFn:
        def _f(t: dict[str, Any]) -> bool:
            v = _num(t.get("entry_rise_10min_pct"))
            return _pass_missing_ok(v is not None and v >= min_r, v)

        return _f

    def rise5_le(max_r: float) -> FilterFn:
        def _f(t: dict[str, Any]) -> bool:
            v = _num(t.get("entry_rise_5min_pct"))
            return _pass_missing_ok(v is not None and v <= max_r, v)

        return _f

    def mom_le(max_m: float) -> FilterFn:
        def _f(t: dict[str, Any]) -> bool:
            v = _num(t.get("momentum_score"))
            return _pass_missing_ok(v is not None and v <= max_m, v)

        return _f

    def day_high_dist_ge(min_d: float) -> FilterFn:
        """Winners farther from day high (Phase631)."""

        def _f(t: dict[str, Any]) -> bool:
            v = _num(t.get("day_high_distance_pct"))
            return _pass_missing_ok(v is not None and v >= min_d, v)

        return _f

    def no_or(t: dict[str, Any]) -> bool:
        return str(t.get("entry_pool") or "") != "OR"

    def no_low_liq(t: dict[str, Any]) -> bool:
        band = str(t.get("trading_value_band") or "")
        if band == "lt_1e8":
            return False
        v = _num(t.get("trading_value"))
        if v is not None and v < 1e8:
            return False
        return True

    def all_of(*fns: FilterFn) -> FilterFn:
        def _f(t: dict[str, Any]) -> bool:
            return all(fn(t) for fn in fns)

        return _f

    singles: list[Variant] = [
        Variant("baseline", "baseline (no filter)", "baseline", lambda t: True),
        Variant("tv_ge_1e8", "trading_value >= 1e8", "single", tv_ge_1e8),
        Variant("price_age_le_3", "price_age_sec <= 3", "single", price_age_le(3.0)),
        Variant("price_age_le_5", "price_age_sec <= 5", "single", price_age_le(5.0)),
        Variant("update_ge_1", "update_count_before_entry >= 1", "single", update_count_ge(1.0)),
        Variant("update_ge_2", "update_count_before_entry >= 2", "single", update_count_ge(2.0)),
        Variant("turnover_ge_p25", "turnover_proxy >= 0.0102", "single", turnover_ge(0.0102)),
        Variant("turnover_ge_p50", "turnover_proxy >= 0.0212", "single", turnover_ge(0.0212)),
        Variant("rise10_ge_0", "entry_rise_10min_pct >= 0", "single", rise10_ge(0.0)),
        Variant("rise10_ge_p50", "entry_rise_10min_pct >= 0.065", "single", rise10_ge(0.065)),
        Variant("rise5_le_p75", "entry_rise_5min_pct <= 0.30", "single", rise5_le(0.30)),
        Variant("rise5_le_p90", "entry_rise_5min_pct <= 0.66", "single", rise5_le(0.66)),
        Variant("mom_le_p75", "momentum_score <= 0.124", "single", mom_le(0.124)),
        Variant("mom_le_p50", "momentum_score <= 0.015", "single", mom_le(0.015)),
        Variant("dh_dist_ge_p25", "day_high_distance_pct >= 3.88", "single", day_high_dist_ge(3.88)),
        Variant("dh_dist_ge_p50", "day_high_distance_pct >= 5.83", "single", day_high_dist_ge(5.83)),
        Variant("no_or_overlay", "block OR_OVERLAY", "single", no_or),
        Variant("no_low_liq", "exclude trading_value_band=lt_1e8", "single", no_low_liq),
    ]

    # Combos: liquidity + freshness + anti-chase (Phase631 themes)
    combos = [
        Variant(
            "combo_liq_fresh",
            "tv>=1e8 & price_age<=3",
            "combo",
            all_of(tv_ge_1e8, price_age_le(3.0)),
        ),
        Variant(
            "combo_liq_fresh_update",
            "tv>=1e8 & price_age<=3 & update>=1",
            "combo",
            all_of(tv_ge_1e8, price_age_le(3.0), update_count_ge(1.0)),
        ),
        Variant(
            "combo_liq_fresh_antichase",
            "tv>=1e8 & price_age<=3 & rise5<=0.30 & mom<=0.124",
            "combo",
            all_of(tv_ge_1e8, price_age_le(3.0), rise5_le(0.30), mom_le(0.124)),
        ),
        Variant(
            "combo_full_phase631",
            "tv>=1e8 & age<=3 & update>=1 & turnover>=p25 & rise10>=0 & rise5<=0.30 & mom<=0.124 & dh>=p25 & no_low_liq",
            "combo",
            all_of(
                tv_ge_1e8,
                price_age_le(3.0),
                update_count_ge(1.0),
                turnover_ge(0.0102),
                rise10_ge(0.0),
                rise5_le(0.30),
                mom_le(0.124),
                day_high_dist_ge(3.88),
                no_low_liq,
            ),
        ),
        Variant(
            "combo_pbv2_only_liq_fresh",
            "no OR & tv>=1e8 & price_age<=3",
            "combo",
            all_of(no_or, tv_ge_1e8, price_age_le(3.0)),
        ),
        Variant(
            "combo_soft",
            "no_low_liq & price_age<=5 & rise5<=0.66",
            "combo",
            all_of(no_low_liq, price_age_le(5.0), rise5_le(0.66)),
        ),
    ]
    return singles + combos


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    gp = sum(p for p in pnls if p > 0)
    gl = sum(p for p in pnls if p < 0)
    if gl == 0:
        return None if gp == 0 else float("inf")
    return gp / abs(gl)


def _max_drawdown(pnls_chrono: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls_chrono:
        equity += float(p)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(max_dd, 2)


def _metrics(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(t["pnl_yen_100"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    chrono = sorted(trades, key=lambda t: (str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    chrono_pnls = [float(t["pnl_yen_100"]) for t in chrono]
    pf = _profit_factor(pnls)
    return {
        "entry_count": len(trades),
        "pbv2_accepted": sum(1 for t in trades if t.get("entry_pool") == "PBV2"),
        "or_accepted": sum(1 for t in trades if t.get("entry_pool") == "OR"),
        "pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": (
            None
            if pf is None
            else (999.0 if pf == float("inf") else round(float(pf), 4))
        ),
        "profit_factor_raw": (
            None if pf is None else (999.0 if pf == float("inf") else float(pf))
        ),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "avg_pnl_yen_100": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "max_dd_yen_100": _max_drawdown(chrono_pnls) if chrono_pnls else 0.0,
        "win_count": len(wins),
        "loss_count": len(losses),
        "gross_profit_yen_100": round(sum(wins), 2),
        "gross_loss_yen_100": round(sum(losses), 2),
    }


def _daily_pnl(trades: Sequence[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in trades:
        d = str(t.get("day") or "")
        out[d] = out.get(d, 0.0) + float(t["pnl_yen_100"])
    return {k: round(v, 2) for k, v in sorted(out.items())}


def _symbol_pnl(trades: Sequence[dict[str, Any]], *, top_n: int = 30) -> list[dict[str, Any]]:
    agg: dict[str, float] = {}
    for t in trades:
        s = str(t.get("symbol") or "")
        agg[s] = agg.get(s, 0.0) + float(t["pnl_yen_100"])
    rows = [{"symbol": s, "pnl_yen_100": round(v, 2)} for s, v in agg.items()]
    rows.sort(key=lambda r: r["pnl_yen_100"], reverse=True)
    # keep best and worst tails only (compact)
    if len(rows) <= top_n * 2:
        return rows
    return rows[:top_n] + rows[-top_n:]


def evaluate_variant(
    variant: Variant,
    trades: Sequence[dict[str, Any]],
    baseline_metrics: dict[str, Any],
) -> dict[str, Any]:
    kept: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for t in trades:
        if variant.keep(t):
            kept.append(t)
        else:
            blocked.append(t)

    m = _metrics(kept)
    # Wrongly blocked winners / rescued losers
    wrong_block_winners = [t for t in blocked if float(t["pnl_yen_100"]) > 0]
    rescued_losers = [t for t in blocked if float(t["pnl_yen_100"]) < 0]
    wrong_block_pnl = sum(float(t["pnl_yen_100"]) for t in wrong_block_winners)
    rescued_pnl = sum(float(t["pnl_yen_100"]) for t in rescued_losers)

    delta_pnl = m["pnl_yen_100"] - float(baseline_metrics["pnl_yen_100"])
    base_pf = baseline_metrics.get("profit_factor_raw")
    cur_pf = m.get("profit_factor_raw")
    delta_pf = None
    if isinstance(base_pf, (int, float)) and isinstance(cur_pf, (int, float)):
        if base_pf != float("inf") and cur_pf != float("inf"):
            delta_pf = round(float(cur_pf) - float(base_pf), 4)

    return {
        "variant_id": variant.variant_id,
        "label": variant.label,
        "kind": variant.kind,
        **{k: v for k, v in m.items() if k != "profit_factor_raw"},
        "profit_factor_raw": m.get("profit_factor_raw"),
        "blocked_count": len(blocked),
        "wrongly_blocked_winners": len(wrong_block_winners),
        "wrongly_blocked_winners_pnl_yen_100": round(wrong_block_pnl, 2),
        "rescued_losers": len(rescued_losers),
        "rescued_losers_pnl_yen_100": round(rescued_pnl, 2),
        "net_block_effect_yen_100": round(-wrong_block_pnl - rescued_pnl, 2),
        # removing a loser (rescued_pnl negative) improves by -rescued_pnl;
        # removing a winner costs wrong_block_pnl. net improvement = -rescued_pnl - wrong_block_pnl
        # = -(wrong_block_pnl + rescued_pnl). Since rescued_pnl < 0, -rescued_pnl > 0.
        "delta_pnl_yen_100": round(delta_pnl, 2),
        "delta_pf": delta_pf,
        "delta_max_dd_yen_100": round(
            float(m["max_dd_yen_100"]) - float(baseline_metrics["max_dd_yen_100"]), 2
        ),
        "daily_pnl": _daily_pnl(kept),
        "symbol_pnl_tails": _symbol_pnl(kept),
        "_blocked_trades": blocked,
        "_kept_trades": kept,
    }


def _compact_trade_row(t: dict[str, Any], *, variant_id: str, role: str) -> dict[str, Any]:
    return {
        "variant_id": variant_id,
        "role": role,
        "day": t.get("day"),
        "symbol": t.get("symbol"),
        "entry_time": t.get("entry_time"),
        "entry_pool": t.get("entry_pool"),
        "exit_reason": t.get("exit_reason"),
        "pnl_yen_100": t.get("pnl_yen_100"),
        "trading_value": t.get("trading_value"),
        "price_age_sec": t.get("price_age_sec"),
        "update_count_before_entry": t.get("update_count_before_entry"),
        "turnover_proxy": t.get("turnover_proxy"),
        "entry_rise_5min_pct": t.get("entry_rise_5min_pct"),
        "entry_rise_10min_pct": t.get("entry_rise_10min_pct"),
        "momentum_score": t.get("momentum_score"),
        "day_high_distance_pct": t.get("day_high_distance_pct"),
        "trading_value_band": t.get("trading_value_band"),
    }


def _write_csv(fp: Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def run(replay_root: Path = REPLAY_ROOT) -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    disk_pct = _disk_usage_pct(NATIVE_ROOT)
    # Constraint: do not write large artifacts. Compact CSVs/json only (~MB).
    # If disk is already above the budget, still run in-memory counterfactual
    # (reuses existing Phase630 replay; no new payloads / full-candidate dumps).
    disk_warning = disk_pct > DISK_USAGE_MAX_PCT
    if disk_warning:
        print(
            f"[phase632] WARN disk_usage={disk_pct:.1f}% > {DISK_USAGE_MAX_PCT}% "
            f"(compact outputs only)",
            flush=True,
        )

    trades = load_all_trades(replay_root)
    if len(trades) < 50:
        report = {
            "phase": "phase632_pbv2_profit_filter_counterfactual",
            "verdict": PHASE632_FAIL,
            "error": f"insufficient trades: {len(trades)}",
        }
        (REPORT_DIR / "phase632_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    variants = _build_variants()
    baseline_metrics = _metrics(trades)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {
            ex.submit(evaluate_variant, v, trades, baseline_metrics): v for v in variants
        }
        for fut in as_completed(futs):
            results.append(fut.result())

    # stable order: baseline, singles, combos
    order = {v.variant_id: i for i, v in enumerate(variants)}
    results.sort(key=lambda r: order.get(r["variant_id"], 999))

    # Rank by delta_pnl then PF then shallower DD
    ranked = sorted(
        [r for r in results if r["variant_id"] != "baseline"],
        key=lambda r: (
            float(r["delta_pnl_yen_100"]),
            float(r["profit_factor"] or 0.0),
            -abs(float(r["max_dd_yen_100"])),
        ),
        reverse=True,
    )

    # Variant comparison CSV (aggregates only)
    cmp_fields = [
        "variant_id",
        "label",
        "kind",
        "entry_count",
        "pbv2_accepted",
        "or_accepted",
        "pnl_yen_100",
        "profit_factor",
        "win_rate",
        "avg_pnl_yen_100",
        "max_dd_yen_100",
        "blocked_count",
        "wrongly_blocked_winners",
        "wrongly_blocked_winners_pnl_yen_100",
        "rescued_losers",
        "rescued_losers_pnl_yen_100",
        "delta_pnl_yen_100",
        "delta_pf",
        "delta_max_dd_yen_100",
    ]
    _write_csv(
        REPORT_DIR / "phase632_variant_comparison.csv",
        [{k: r.get(k) for k in cmp_fields} for r in results],
        cmp_fields,
    )

    # Daily comparison (long format)
    daily_rows: list[dict[str, Any]] = []
    for r in results:
        for day, pnl in (r.get("daily_pnl") or {}).items():
            daily_rows.append(
                {
                    "variant_id": r["variant_id"],
                    "day": day,
                    "pnl_yen_100": pnl,
                    "entry_count": sum(
                        1 for t in r.get("_kept_trades") or [] if t.get("day") == day
                    ),
                }
            )
    _write_csv(
        REPORT_DIR / "phase632_daily_comparison.csv",
        daily_rows,
        ["variant_id", "day", "pnl_yen_100", "entry_count"],
    )

    # Blocked trade analysis: top variants only, capped rows
    top_for_block = ["baseline"] + [r["variant_id"] for r in ranked[:5]]
    block_rows: list[dict[str, Any]] = []
    for r in results:
        if r["variant_id"] not in top_for_block or r["variant_id"] == "baseline":
            continue
        blocked = r.get("_blocked_trades") or []
        winners = sorted(
            [t for t in blocked if float(t["pnl_yen_100"]) > 0],
            key=lambda t: float(t["pnl_yen_100"]),
            reverse=True,
        )
        losers = sorted(
            [t for t in blocked if float(t["pnl_yen_100"]) < 0],
            key=lambda t: float(t["pnl_yen_100"]),
        )
        half = BLOCKED_ANALYSIS_MAX_ROWS // (2 * max(1, len(top_for_block) - 1))
        for t in winners[:half]:
            block_rows.append(
                _compact_trade_row(t, variant_id=r["variant_id"], role="wrongly_blocked_winner")
            )
        for t in losers[:half]:
            block_rows.append(
                _compact_trade_row(t, variant_id=r["variant_id"], role="rescued_loser")
            )
    block_fields = [
        "variant_id",
        "role",
        "day",
        "symbol",
        "entry_time",
        "entry_pool",
        "exit_reason",
        "pnl_yen_100",
        "trading_value",
        "price_age_sec",
        "update_count_before_entry",
        "turnover_proxy",
        "entry_rise_5min_pct",
        "entry_rise_10min_pct",
        "momentum_score",
        "day_high_distance_pct",
        "trading_value_band",
    ]
    _write_csv(REPORT_DIR / "phase632_blocked_trade_analysis.csv", block_rows, block_fields)

    # Strip heavy trade lists from JSON report
    public_results = []
    for r in results:
        pub = {k: v for k, v in r.items() if not k.startswith("_")}
        public_results.append(pub)

    best = ranked[0] if ranked else None
    improves = [
        r
        for r in ranked
        if float(r["delta_pnl_yen_100"]) > 0
        and (r["profit_factor"] is None or float(r["profit_factor"] or 0) >= float(baseline_metrics.get("profit_factor") or 0))
    ]

    report = {
        "phase": "phase632_pbv2_profit_filter_counterfactual",
        "verdict": PHASE632_VERDICT,
        "replay_root": str(replay_root),
        "days": list(DAYS),
        "trade_count_baseline": len(trades),
        "disk_usage_pct": round(disk_pct, 2),
        "disk_budget_pct": DISK_USAGE_MAX_PCT,
        "disk_warning": disk_warning,
        "max_workers": MAX_WORKERS,
        "baseline": {k: baseline_metrics[k] for k in baseline_metrics if k != "profit_factor_raw"},
        "variants": public_results,
        "ranking_by_delta_pnl": [
            {
                "rank": i,
                "variant_id": r["variant_id"],
                "label": r["label"],
                "delta_pnl_yen_100": r["delta_pnl_yen_100"],
                "pnl_yen_100": r["pnl_yen_100"],
                "profit_factor": r["profit_factor"],
                "max_dd_yen_100": r["max_dd_yen_100"],
                "entry_count": r["entry_count"],
                "wrongly_blocked_winners": r["wrongly_blocked_winners"],
                "rescued_losers": r["rescued_losers"],
            }
            for i, r in enumerate(ranked, start=1)
        ],
        "best_variant": (
            {
                "variant_id": best["variant_id"],
                "label": best["label"],
                "delta_pnl_yen_100": best["delta_pnl_yen_100"],
                "pnl_yen_100": best["pnl_yen_100"],
                "profit_factor": best["profit_factor"],
                "max_dd_yen_100": best["max_dd_yen_100"],
                "entry_count": best["entry_count"],
            }
            if best
            else None
        ),
        "improving_variants": [r["variant_id"] for r in improves],
        "artifacts": {
            "variant_comparison": str(REPORT_DIR / "phase632_variant_comparison.csv"),
            "daily_comparison": str(REPORT_DIR / "phase632_daily_comparison.csv"),
            "blocked_trade_analysis": str(REPORT_DIR / "phase632_blocked_trade_analysis.csv"),
            "report": str(REPORT_DIR / "phase632_report.json"),
        },
        "notes": [
            "Counterfactual only: filters applied post-accept on Phase630 replay trades.",
            "ENTRY/PBv2 score formulas unchanged.",
            "Missing feature values keep the trade (fail-open) unless band/OR rules apply.",
        ],
    }
    (REPORT_DIR / "phase632_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    report = run()
    print(f"verdict={report.get('verdict')}", flush=True)
    print(f"baseline_trades={report.get('trade_count_baseline')}", flush=True)
    best = report.get("best_variant") or {}
    print(
        f"best={best.get('variant_id')} delta_pnl={best.get('delta_pnl_yen_100')} "
        f"pnl={best.get('pnl_yen_100')} pf={best.get('profit_factor')}",
        flush=True,
    )
    for row in (report.get("ranking_by_delta_pnl") or [])[:8]:
        print(
            f"  #{row['rank']} {row['variant_id']}: delta={row['delta_pnl_yen_100']} "
            f"pnl={row['pnl_yen_100']} pf={row['profit_factor']} n={row['entry_count']}",
            flush=True,
        )
    print(f"report={REPORT_DIR / 'phase632_report.json'}", flush=True)
    return 0 if report.get("verdict") == PHASE632_VERDICT else 1


if __name__ == "__main__":
    raise SystemExit(main())
