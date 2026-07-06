"""
Phase633: combo_soft robustness decomposition (research only).

Breaks Phase632 combo_soft (no_low_liq & price_age<=5 & rise5<=0.66) by
day / symbol / AM-PM / OR-PBv2, and checks adoption criteria.

No ENTRY/PBv2 score changes — counterfactual keep/drop only.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, Sequence

from research.phase631_profit_source_attribution import (
    DAYS,
    REPLAY_ROOT,
    load_all_trades,
)
from research.phase632_pbv2_profit_filter_counterfactual import (
    _daily_pnl,
    _max_drawdown,
    _metrics,
    _num,
)

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase633_combo_soft_robustness"
PHASE633_VERDICT = "phase633_combo_soft_robustness_done"
PHASE633_FAIL = "phase633_combo_soft_robustness_failed"

# combo_soft definition (must match Phase632)
PRICE_AGE_MAX = 5.0
RISE5_MAX = 0.66
BIG_WINNER_YEN = 5000.0  # "大勝ち" threshold for blocked-trade audit
ENTRY_REDUCTION_MAX = 0.40  # reject if entries drop more than 40%
DAY_WORSEN_YEN = -20000.0  # "大きく悪化" day threshold
TOP_SYMBOL_SHARE_MAX = 0.35  # single-symbol share of delta_pnl


def combo_soft_keep(t: dict[str, Any]) -> bool:
    band = str(t.get("trading_value_band") or "")
    if band == "lt_1e8":
        return False
    tv = _num(t.get("trading_value"))
    if tv is not None and tv < 1e8:
        return False
    age = _num(t.get("price_age_sec"))
    if age is not None and age > PRICE_AGE_MAX:
        return False
    rise5 = _num(t.get("entry_rise_5min_pct"))
    if rise5 is not None and rise5 > RISE5_MAX:
        return False
    return True


def _session_bucket(t: dict[str, Any]) -> str:
    mins = _num(t.get("minutes_from_open"))
    if mins is None:
        return "unknown"
    # JST cash: AM 09:00-11:30 (0-150m), PM 12:30-15:30 (210-390m)
    if mins < 150:
        return "AM"
    if mins >= 210:
        return "PM"
    return "lunch"


def _metrics_slice(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "entry_count": 0,
            "pbv2_accepted": 0,
            "or_accepted": 0,
            "pnl_yen_100": 0.0,
            "profit_factor": None,
            "win_rate": None,
            "avg_pnl_yen_100": None,
            "max_dd_yen_100": 0.0,
        }
    m = _metrics(list(trades))
    return {k: v for k, v in m.items() if k != "profit_factor_raw"}


def _split(trades: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept, blocked = [], []
    for t in trades:
        (kept if combo_soft_keep(t) else blocked).append(t)
    return kept, blocked


def _by_key(trades: Sequence[dict[str, Any]], key_fn) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        out[str(key_fn(t))].append(t)
    return dict(out)


def _delta_row(label: str, base: Sequence[dict[str, Any]], kept: Sequence[dict[str, Any]]) -> dict[str, Any]:
    mb = _metrics_slice(base)
    mk = _metrics_slice(kept)
    return {
        "slice": label,
        "baseline_n": mb["entry_count"],
        "kept_n": mk["entry_count"],
        "blocked_n": mb["entry_count"] - mk["entry_count"],
        "baseline_pnl": mb["pnl_yen_100"],
        "kept_pnl": mk["pnl_yen_100"],
        "delta_pnl": round(float(mk["pnl_yen_100"]) - float(mb["pnl_yen_100"]), 2),
        "baseline_pf": mb["profit_factor"],
        "kept_pf": mk["profit_factor"],
        "baseline_dd": mb["max_dd_yen_100"],
        "kept_dd": mk["max_dd_yen_100"],
        "delta_dd": round(float(mk["max_dd_yen_100"]) - float(mb["max_dd_yen_100"]), 2),
        "entry_reduction_pct": (
            round(1.0 - mk["entry_count"] / mb["entry_count"], 4)
            if mb["entry_count"]
            else None
        ),
    }


def _write_csv(fp: Path, rows: Sequence[dict[str, Any]]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        fp.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fields.append(k)
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def run(replay_root: Path = REPLAY_ROOT) -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    trades = load_all_trades(replay_root)
    if len(trades) < 50:
        report = {
            "phase": "phase633_combo_soft_robustness",
            "verdict": PHASE633_FAIL,
            "error": f"insufficient trades: {len(trades)}",
        }
        (REPORT_DIR / "phase633_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    kept, blocked = _split(trades)
    base_m = _metrics_slice(trades)
    kept_m = _metrics_slice(kept)

    # --- 1) Daily ---
    by_day_base = _by_key(trades, lambda t: t.get("day"))
    by_day_kept = _by_key(kept, lambda t: t.get("day"))
    daily_rows = []
    for day in DAYS:
        daily_rows.append(
            _delta_row(day, by_day_base.get(day, []), by_day_kept.get(day, []))
        )
    worsen_days = [r for r in daily_rows if float(r["delta_pnl"]) < DAY_WORSEN_YEN]
    any_day_worse = [r for r in daily_rows if float(r["delta_pnl"]) < 0]

    # --- 2) Symbol concentration ---
    by_sym_base = _by_key(trades, lambda t: t.get("symbol"))
    by_sym_kept = _by_key(kept, lambda t: t.get("symbol"))
    symbol_rows = []
    for sym in sorted(by_sym_base):
        symbol_rows.append(
            _delta_row(sym, by_sym_base[sym], by_sym_kept.get(sym, []))
        )
    symbol_rows.sort(key=lambda r: float(r["delta_pnl"]), reverse=True)
    total_delta = float(kept_m["pnl_yen_100"]) - float(base_m["pnl_yen_100"])
    # contribution of each symbol to delta = kept_pnl_sym - base_pnl_sym
    top_pos = [r for r in symbol_rows if float(r["delta_pnl"]) > 0][:15]
    top_neg = [r for r in symbol_rows if float(r["delta_pnl"]) < 0]
    top_neg.sort(key=lambda r: float(r["delta_pnl"]))
    top_neg = top_neg[:15]
    max_sym_share = 0.0
    max_sym = None
    if abs(total_delta) > 1e-6:
        for r in symbol_rows:
            share = abs(float(r["delta_pnl"])) / abs(total_delta)
            if share > max_sym_share:
                max_sym_share = share
                max_sym = r["slice"]
    # 584A specific
    row_584a = next((r for r in symbol_rows if "584A" in str(r["slice"])), None)

    # Leave-one-symbol-out: delta without that symbol's contribution
    # If removing top contributor still leaves positive delta => not single-symbol dependent
    leave_one_out = []
    for r in top_pos[:10]:
        sym = r["slice"]
        base_wo = [t for t in trades if t.get("symbol") != sym]
        kept_wo = [t for t in kept if t.get("symbol") != sym]
        d = float(_metrics_slice(kept_wo)["pnl_yen_100"]) - float(
            _metrics_slice(base_wo)["pnl_yen_100"]
        )
        leave_one_out.append(
            {
                "excluded_symbol": sym,
                "delta_pnl_without_symbol": round(d, 2),
                "still_positive": d > 0,
            }
        )

    # --- 3) OR / PBv2 ---
    pool_rows = []
    for pool in ("PBV2", "OR"):
        b = [t for t in trades if t.get("entry_pool") == pool]
        k = [t for t in kept if t.get("entry_pool") == pool]
        pool_rows.append(_delta_row(pool, b, k))

    # --- AM / PM ---
    session_rows = []
    for sess in ("AM", "PM", "lunch", "unknown"):
        b = [t for t in trades if _session_bucket(t) == sess]
        k = [t for t in kept if _session_bucket(t) == sess]
        if not b:
            continue
        session_rows.append(_delta_row(sess, b, k))

    # --- 4) Blocked big winners ---
    blocked_winners = [t for t in blocked if float(t["pnl_yen_100"]) > 0]
    blocked_losers = [t for t in blocked if float(t["pnl_yen_100"]) < 0]
    big_winners_blocked = [
        t for t in blocked_winners if float(t["pnl_yen_100"]) >= BIG_WINNER_YEN
    ]
    big_winners_blocked.sort(key=lambda t: float(t["pnl_yen_100"]), reverse=True)
    big_winner_pnl = sum(float(t["pnl_yen_100"]) for t in big_winners_blocked)
    rescued_loser_pnl = sum(float(t["pnl_yen_100"]) for t in blocked_losers)

    # block reason attribution (which clause fired)
    def _block_reasons(t: dict[str, Any]) -> list[str]:
        reasons = []
        band = str(t.get("trading_value_band") or "")
        tv = _num(t.get("trading_value"))
        if band == "lt_1e8" or (tv is not None and tv < 1e8):
            reasons.append("low_liq")
        age = _num(t.get("price_age_sec"))
        if age is not None and age > PRICE_AGE_MAX:
            reasons.append("price_age")
        rise5 = _num(t.get("entry_rise_5min_pct"))
        if rise5 is not None and rise5 > RISE5_MAX:
            reasons.append("rise5_cap")
        return reasons or ["unknown"]

    reason_counts: dict[str, int] = defaultdict(int)
    reason_big_win: dict[str, int] = defaultdict(int)
    for t in blocked:
        for r in _block_reasons(t):
            reason_counts[r] += 1
    for t in big_winners_blocked:
        for r in _block_reasons(t):
            reason_big_win[r] += 1

    # --- 5) 6/29 & 6/30 PBv2 restoration ---
    restore_days = ("2026-06-29", "2026-06-30")
    restore_rows = []
    for day in restore_days:
        b_all = by_day_base.get(day, [])
        k_all = by_day_kept.get(day, [])
        b_pbv2 = [t for t in b_all if t.get("entry_pool") == "PBV2"]
        k_pbv2 = [t for t in k_all if t.get("entry_pool") == "PBV2"]
        restore_rows.append(
            {
                **_delta_row(f"{day}_all", b_all, k_all),
                "pool": "ALL",
            }
        )
        restore_rows.append(
            {
                **_delta_row(f"{day}_PBv2", b_pbv2, k_pbv2),
                "pool": "PBv2",
            }
        )
    # "邪魔しない" = PBv2 pnl on those days does not worsen materially
    restore_ok = all(
        float(r["delta_pnl"]) >= DAY_WORSEN_YEN
        for r in restore_rows
        if r.get("pool") == "PBv2"
    )

    # --- 6) Phase621 / 627 conflict ---
    # Phase621: freshness_semantics_v2 (event/board/trade stale) — pre-gate.
    # combo_soft price_age<=5 is a *post-accept* overlay; does not change v2 thresholds.
    # Phase627: cluster guard internal reason preservation — combo_soft never touches
    # cluster_guard_status / pbv2_internal_reason.
    conflict = {
        "phase621_freshness_v2": {
            "conflicts": False,
            "note": (
                "Phase621 owns pre-gate event/board/trade stale semantics. "
                "combo_soft applies price_age_sec<=5 only on already-accepted trades "
                "(counterfactual post-filter). No YAML / freshness_semantics_v2 change."
            ),
        },
        "phase627_cluster_guard": {
            "conflicts": False,
            "note": (
                "Phase627 preserves pbv2_internal_reason / cluster_guard fields. "
                "combo_soft does not read or rewrite gate reasons; OR/PBv2 labels unchanged."
            ),
        },
    }

    # --- Adoption criteria ---
    entry_reduction = 1.0 - kept_m["entry_count"] / base_m["entry_count"]
    delta_pnl = float(kept_m["pnl_yen_100"]) - float(base_m["pnl_yen_100"])
    base_pf = base_m["profit_factor"] or 0.0
    kept_pf = kept_m["profit_factor"] or 0.0
    delta_dd = float(kept_m["max_dd_yen_100"]) - float(base_m["max_dd_yen_100"])  # less negative = better

    criteria = {
        "pnl_improved": delta_pnl > 0,
        "pf_improved": kept_pf >= base_pf,
        "dd_improved": delta_dd >= 0,  # max_dd is negative; improvement => closer to 0
        "daily_worsening_limited": len(worsen_days) == 0,
        "no_single_symbol_dependency": (
            max_sym_share <= TOP_SYMBOL_SHARE_MAX
            and all(x["still_positive"] for x in leave_one_out[:5])
        ),
        "entry_reduction_not_excessive": entry_reduction <= ENTRY_REDUCTION_MAX,
        "restore_days_pbv2_ok": restore_ok,
        "no_phase621_627_conflict": True,
    }
    adopt = all(criteria.values())

    # Compact blocked big-winner rows
    big_win_rows = [
        {
            "day": t.get("day"),
            "symbol": t.get("symbol"),
            "entry_time": t.get("entry_time"),
            "entry_pool": t.get("entry_pool"),
            "pnl_yen_100": t.get("pnl_yen_100"),
            "block_reasons": "|".join(_block_reasons(t)),
            "price_age_sec": t.get("price_age_sec"),
            "entry_rise_5min_pct": t.get("entry_rise_5min_pct"),
            "trading_value": t.get("trading_value"),
            "trading_value_band": t.get("trading_value_band"),
        }
        for t in big_winners_blocked[:50]
    ]

    _write_csv(REPORT_DIR / "phase633_daily_breakdown.csv", daily_rows)
    _write_csv(REPORT_DIR / "phase633_symbol_breakdown.csv", symbol_rows)
    _write_csv(REPORT_DIR / "phase633_pool_session_breakdown.csv", pool_rows + session_rows)
    _write_csv(REPORT_DIR / "phase633_restore_days_pbv2.csv", restore_rows)
    _write_csv(REPORT_DIR / "phase633_blocked_big_winners.csv", big_win_rows)
    _write_csv(REPORT_DIR / "phase633_leave_one_symbol_out.csv", leave_one_out)

    answers = {
        "1_daily_large_worsening": {
            "worsen_days_below_threshold": [
                {"day": r["slice"], "delta_pnl": r["delta_pnl"]} for r in worsen_days
            ],
            "any_negative_days": [
                {"day": r["slice"], "delta_pnl": r["delta_pnl"]} for r in any_day_worse
            ],
            "pass": len(worsen_days) == 0,
        },
        "2_single_symbol_dependency": {
            "max_contributor": max_sym,
            "max_share_of_delta": round(max_sym_share, 4),
            "symbol_584A": row_584a,
            "leave_one_out_top5_still_positive": all(
                x["still_positive"] for x in leave_one_out[:5]
            ),
            "pass": criteria["no_single_symbol_dependency"],
        },
        "3_or_vs_pbv2": pool_rows,
        "4_blocked_big_winners": {
            "big_winner_threshold_yen": BIG_WINNER_YEN,
            "count": len(big_winners_blocked),
            "pnl_sum_yen_100": round(big_winner_pnl, 2),
            "rescued_losers_pnl_yen_100": round(rescued_loser_pnl, 2),
            "net_block_effect_yen_100": round(-big_winner_pnl - rescued_loser_pnl, 2),
            "block_reason_counts": dict(reason_counts),
            "big_win_block_reason_counts": dict(reason_big_win),
            "top_blocked_big_winners": big_win_rows[:10],
            "pass": big_winner_pnl < abs(rescued_loser_pnl),  # rescued losses outweigh big wins cut
        },
        "5_restore_629_630_pbv2": {
            "rows": restore_rows,
            "pass": restore_ok,
        },
        "6_phase621_627_conflict": conflict,
    }

    report = {
        "phase": "phase633_combo_soft_robustness",
        "verdict": PHASE633_VERDICT,
        "filter": {
            "name": "combo_soft",
            "rules": [
                "trading_value_band != lt_1e8 and trading_value >= 1e8",
                f"price_age_sec <= {PRICE_AGE_MAX}",
                f"entry_rise_5min_pct <= {RISE5_MAX}",
            ],
        },
        "baseline": base_m,
        "combo_soft": kept_m,
        "delta": {
            "pnl_yen_100": round(delta_pnl, 2),
            "pf": round(kept_pf - base_pf, 4),
            "max_dd_yen_100": round(delta_dd, 2),
            "entry_reduction_pct": round(entry_reduction, 4),
            "blocked_count": len(blocked),
        },
        "adoption_criteria": criteria,
        "adopt_recommendation": "ADOPT" if adopt else "HOLD",
        "mandatory_answers": answers,
        "daily": daily_rows,
        "pool": pool_rows,
        "session": session_rows,
        "top_symbol_positive_delta": top_pos,
        "top_symbol_negative_delta": top_neg,
        "artifacts": {
            "daily": str(REPORT_DIR / "phase633_daily_breakdown.csv"),
            "symbol": str(REPORT_DIR / "phase633_symbol_breakdown.csv"),
            "pool_session": str(REPORT_DIR / "phase633_pool_session_breakdown.csv"),
            "restore_days": str(REPORT_DIR / "phase633_restore_days_pbv2.csv"),
            "blocked_big_winners": str(REPORT_DIR / "phase633_blocked_big_winners.csv"),
            "leave_one_out": str(REPORT_DIR / "phase633_leave_one_symbol_out.csv"),
            "report": str(REPORT_DIR / "phase633_report.json"),
        },
    }
    (REPORT_DIR / "phase633_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    report = run()
    print(f"verdict={report.get('verdict')}", flush=True)
    print(f"adopt={report.get('adopt_recommendation')}", flush=True)
    print(f"criteria={json.dumps(report.get('adoption_criteria'), ensure_ascii=False)}", flush=True)
    d = report.get("delta") or {}
    print(
        f"delta_pnl={d.get('pnl_yen_100')} pf={d.get('pf')} dd={d.get('max_dd_yen_100')} "
        f"entry_reduction={d.get('entry_reduction_pct')}",
        flush=True,
    )
    for row in report.get("daily") or []:
        print(
            f"  day {row['slice']}: delta_pnl={row['delta_pnl']} "
            f"n {row['baseline_n']}->{row['kept_n']}",
            flush=True,
        )
    for row in report.get("pool") or []:
        print(
            f"  pool {row['slice']}: delta_pnl={row['delta_pnl']} "
            f"n {row['baseline_n']}->{row['kept_n']}",
            flush=True,
        )
    ans = report.get("mandatory_answers") or {}
    print(f"big_winners_blocked={ans.get('4_blocked_big_winners', {}).get('count')}", flush=True)
    print(f"report={REPORT_DIR / 'phase633_report.json'}", flush=True)
    return 0 if report.get("verdict") == PHASE633_VERDICT else 1


if __name__ == "__main__":
    raise SystemExit(main())
