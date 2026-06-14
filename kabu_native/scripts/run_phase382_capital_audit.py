#!/usr/bin/env python3
"""Phase382 capital-constrained backtest audit."""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "kabu_native" / "results" / "reports"


def classify_reject(reason: str) -> str:
    if reason in ("insufficient_buying_power", "invalid_size"):
        return "capital_shortage"
    if reason == "max_concurrent_positions":
        return "position_cap"
    if reason in ("maintenance_ratio_stop", "maintenance_ratio_force_exit"):
        return "maintenance_ratio"
    if reason == "equity_floor_breach":
        return "equity_floor"
    return "other"


def load_csv(name: str) -> list[dict[str, str]]:
    path = REPORTS / name
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def audit() -> dict:
    rejects = load_csv("phase382_capital_constrained_rejects.csv")
    trade_log = load_csv("phase382_capital_constrained_trade_log.csv")
    daily = load_csv("phase382_capital_constrained_daily_equity.csv")
    curve = load_csv("phase382_capital_constrained_equity_curve.csv")
    summary = json.loads((REPORTS / "phase382_capital_constrained_summary.json").read_text(encoding="utf-8"))

    out: dict = {"phase": "382_audit"}

    # 1-2: F reject breakdown
    f_rejects = [r for r in rejects if r["scenario"] == "F_no_margin_cash_only"]
    f_cat = Counter(classify_reject(r["reject_reason"]) for r in f_rejects)
    f_raw = Counter(r["reject_reason"] for r in f_rejects)
    f_bps = [float(r["buying_power"]) for r in f_rejects if r.get("buying_power")]
    f_gross = [float(r["gross_position_value"]) for r in f_rejects if r.get("gross_position_value")]
    f_prices = [float(r["entry_price"]) for r in f_rejects if r.get("entry_price")]

    out["F_reject_analysis"] = {
        "accepted": 71,
        "rejected": len(f_rejects),
        "category_counts": dict(f_cat),
        "raw_reason_top15": f_raw.most_common(15),
        "avg_buying_power_at_reject": round(sum(f_bps) / len(f_bps), 2) if f_bps else None,
        "avg_gross_at_reject": round(sum(f_gross) / len(f_gross), 2) if f_gross else None,
        "median_entry_price_rejected": sorted(f_prices)[len(f_prices) // 2] if f_prices else None,
        "pct_reject_entry_price_gt_500k": round(
            sum(1 for p in f_prices if p * 100 > 500000) / len(f_prices) * 100, 2
        )
        if f_prices
        else None,
    }

    # All scenarios reject categories
    all_cat: dict[str, dict[str, int]] = {}
    for scenario in sorted({r["scenario"] for r in rejects}):
        rows = [r for r in rejects if r["scenario"] == scenario]
        all_cat[scenario] = dict(Counter(classify_reject(r["reject_reason"]) for r in rows))
    out["all_scenario_reject_categories"] = all_cat

    # 3: F daily equity
    f_days = [r for r in daily if r["scenario"] == "F_no_margin_cash_only"]
    out["F_daily_equity"] = f_days

    # 4: reinvestment check for F
    f_entries = [
        r
        for r in trade_log
        if r["scenario"] == "F_no_margin_cash_only"
        and r["accepted_or_rejected"] == "accepted"
        and r.get("pnl_yen") in ("", None)
        and r.get("exit_time") in ("", None)
    ]
    f_exits = [
        r
        for r in trade_log
        if r["scenario"] == "F_no_margin_cash_only"
        and r["accepted_or_rejected"] == "accepted"
        and r.get("pnl_yen") not in ("", None)
    ]
    entry_eq = [(r["entry_time"], float(r["equity_before"])) for r in f_entries]
    exit_eq = [(r["exit_time"], float(r["equity_after"])) for r in f_exits]
    realized_sum = round(sum(float(r["pnl_yen"]) for r in f_exits), 2)
    final_from_log = exit_eq[-1][1] if exit_eq else None
    reinvest_ok = (
        abs(realized_sum - 40800.0) < 1.0
        and abs((final_from_log or 0) - 540800.0) < 1.0
        and entry_eq[-1][1] > entry_eq[0][1]
    )
    out["F_reinvestment_check"] = {
        "entry_accept_count": len(f_entries),
        "exit_count": len(f_exits),
        "sum_realized_pnl": realized_sum,
        "expected_total_return": 40800.0,
        "pnl_sum_matches": abs(realized_sum - 40800.0) < 1.0,
        "first_entry_equity_before": entry_eq[0][1] if entry_eq else None,
        "last_entry_equity_before": entry_eq[-1][1] if entry_eq else None,
        "equity_grows_across_entries": entry_eq[-1][1] > entry_eq[0][1] if len(entry_eq) >= 2 else None,
        "final_equity_from_last_exit": final_from_log,
        "reinvestment_correct": reinvest_ok,
        "entry_equity_timeline": entry_eq[:10] + (["..."] if len(entry_eq) > 15 else []) + entry_eq[-5:],
    }

    # 5: F position stats from trade_log
    gross_before = [float(r["gross_position_value_before"]) for r in f_entries if r.get("gross_position_value_before")]
    gross_after = [float(r["gross_position_value_after"]) for r in f_entries if r.get("gross_position_value_after")]
    bp_at_entry = [
        float(r["equity_before"]) - float(r["gross_position_value_before"])
        for r in f_entries
        if r.get("equity_before") and r.get("gross_position_value_before")
    ]
    out["F_position_stats"] = {
        "max_gross_position_value": round(max(gross_after), 2) if gross_after else None,
        "avg_gross_position_value_at_accept": round(sum(gross_after) / len(gross_after), 2) if gross_after else None,
        "avg_free_cash_at_accept": round(sum(bp_at_entry) / len(bp_at_entry), 2) if bp_at_entry else None,
        "max_position_value_single": round(
            max(float(r["position_value"]) for r in f_entries if r.get("position_value")), 2
        )
        if f_entries
        else None,
    }

    # 6: credit 3x reject rate audit (A,B,C,D)
    credit_audit = []
    for s in summary.get("scenarios", []):
        sid = s["scenario_id"]
        acc = int(s["accepted_trade_count"])
        rej = int(s["rejected_trade_count"])
        total = acc + rej
        credit_audit.append(
            {
                "scenario_id": sid,
                "leverage_limit": s.get("leverage_limit"),
                "accepted": acc,
                "rejected": rej,
                "total": total,
                "reject_rate_pct": round(rej / total * 100, 2) if total else 0,
                "reject_ge_95pct": rej / total >= 0.95 if total else False,
            }
        )
    out["credit_3x_reject_audit"] = credit_audit

    # A specific: why 95% reject - breakdown
    a_rejects = [r for r in rejects if r["scenario"] == "A_fixed_100_shares"]
    a_cat = Counter(classify_reject(r["reject_reason"]) for r in a_rejects)
    out["A_fixed_100_reject_detail"] = {
        "total_rejected": len(a_rejects),
        "category": dict(a_cat),
        "position_cap_share": round(a_cat.get("position_cap", 0) / len(a_rejects) * 100, 2) if a_rejects else 0,
        "capital_shortage_share": round(a_cat.get("capital_shortage", 0) / len(a_rejects) * 100, 2)
        if a_rejects
        else 0,
    }

    # 7: first 50 F trade_log accept/reject (chronological entry attempts)
    f_tl = [r for r in trade_log if r["scenario"] == "F_no_margin_cash_only"]
    f_tl_sorted = sorted(f_tl, key=lambda r: (r.get("entry_time") or "", r.get("exit_time") or ""))
    first50 = []
    seen_entry = set()
    for r in f_tl_sorted:
        key = (r.get("symbol"), r.get("entry_time"), r.get("accepted_or_rejected"))
        if key in seen_entry and r.get("accepted_or_rejected") == "accepted" and r.get("pnl_yen"):
            continue  # skip duplicate exit row for listing entry attempts
        if r.get("accepted_or_rejected") == "rejected" or (
            r.get("accepted_or_rejected") == "accepted" and not r.get("pnl_yen")
        ):
            first50.append(
                {
                    "entry_time": r.get("entry_time"),
                    "symbol": r.get("symbol"),
                    "accepted_or_rejected": r.get("accepted_or_rejected"),
                    "reject_reason": r.get("reject_reason"),
                    "entry_price": r.get("entry_price"),
                    "shares": r.get("shares"),
                    "equity_before": r.get("equity_before"),
                    "gross_position_value_before": r.get("gross_position_value_before"),
                    "position_value": r.get("position_value"),
                }
            )
        if len(first50) >= 50:
            break
    out["F_first_50_entry_attempts"] = first50

    # F accept explanation
    out["F_accept_71_explanation"] = {
        "mechanism": "cash_only: buying_power = equity - gross_open_positions",
        "lot_rule": "100-share lots only; reject if entry_price*100 > free cash",
        "max_concurrent": 3,
        "primary_reject": "capital_shortage (insufficient_buying_power + invalid_size)",
        "secondary_reject": "position_cap when 3 slots full",
        "why_so_few": (
            "Most signals require 100 shares; high-priced names (e.g. 3110.T ~22k) need 2.2M cash per lot. "
            "With 500k equity and up to 3 concurrent positions tying cash, only low-price / post-exit slots accept."
        ),
    }

    return out


def main() -> int:
    result = audit()
    out_path = REPORTS / "phase382_capital_audit.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== Phase382 Audit ===", flush=True)
    print(json.dumps(result["F_reject_analysis"], ensure_ascii=False, indent=2), flush=True)
    print("\nF daily equity:", flush=True)
    for d in result["F_daily_equity"]:
        print(f"  {d['day']}: {d['start_equity']} -> {d['end_equity']} pnl={d['daily_pnl']}", flush=True)
    print("\nF reinvestment:", result["F_reinvestment_check"], flush=True)
    print("\nF position stats:", result["F_position_stats"], flush=True)
    print("\nCredit 3x reject audit:", flush=True)
    for row in result["credit_3x_reject_audit"]:
        print(f"  {row}", flush=True)
    print(f"\nWritten: {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
