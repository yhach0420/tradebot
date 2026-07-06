"""Phase634 post625 PBv2 breakdown (ad-hoc query)."""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

KABU = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KABU / "src"))

from research.phase634_pbv2_only_rise5_full_period import (
    PRE625_CUTOFF,
    _pbv2_rise5_keep,
    _pbv2_combo_soft_keep,
    load_all_full_period_trades,
)

TH = 1.8444  # p95
TARGET = ("2026-06-25", "2026-06-29", "2026-06-30", "2026-07-01")


def main() -> None:
    trades, sessions = load_all_full_period_trades()
    post625 = [t for t in trades if str(t.get("day") or "") >= PRE625_CUTOFF]
    pre625 = [t for t in trades if str(t.get("day") or "") < PRE625_CUTOFF]

    print("=== 1. PBv2 trade count by day (post625) ===")
    for day in TARGET:
        all_d = [t for t in post625 if t.get("day") == day]
        pbv2 = [t for t in all_d if t.get("entry_pool") == "PBV2"]
        or_t = [t for t in all_d if t.get("entry_pool") == "OR"]
        pnl_all = sum(float(t["pnl_yen_100"]) for t in all_d)
        pnl_pbv2 = sum(float(t["pnl_yen_100"]) for t in pbv2)
        print(
            f"{day}: total={len(all_d)} PBv2={len(pbv2)} OR={len(or_t)} "
            f"PnL_all={pnl_all:.0f} PnL_pbv2={pnl_pbv2:.0f}"
        )

    print("\n=== All post625 days in dataset ===")
    by_day: dict[str, dict] = defaultdict(lambda: {"all": 0, "pbv2": 0, "or": 0, "pnl": 0.0, "pnl_pbv2": 0.0})
    for t in post625:
        d = str(t.get("day"))
        by_day[d]["all"] += 1
        by_day[d]["pnl"] += float(t["pnl_yen_100"])
        if t.get("entry_pool") == "PBV2":
            by_day[d]["pbv2"] += 1
            by_day[d]["pnl_pbv2"] += float(t["pnl_yen_100"])
        else:
            by_day[d]["or"] += 1
    for d in sorted(by_day):
        x = by_day[d]
        print(
            f"{d}: total={x['all']} PBv2={x['pbv2']} OR={x['or']} "
            f"pnl={x['pnl']:.0f} pnl_pbv2={x['pnl_pbv2']:.0f}"
        )

    print("\n=== 2. Sessions on target days ===")
    for day in TARGET:
        ss = [s for s in sessions if s["day"] == day]
        print(f"{day}: {len(ss)} session(s) -> {[(s['session'], s['trade_count']) for s in ss]}")

    print("\n=== 3. post625 delta by day (p95 rise5 cap) ===")
    total_b = total_k = 0.0
    for day in sorted({str(t.get("day")) for t in post625}):
        sub = [t for t in post625 if t.get("day") == day]
        kept = [t for t in sub if _pbv2_rise5_keep(t, TH)]
        b = sum(float(t["pnl_yen_100"]) for t in sub)
        k = sum(float(t["pnl_yen_100"]) for t in kept)
        pbv2_sub = [t for t in sub if t.get("entry_pool") == "PBV2"]
        pbv2_kept = [t for t in kept if t.get("entry_pool") == "PBV2"]
        bp = sum(float(t["pnl_yen_100"]) for t in pbv2_sub)
        kp = sum(float(t["pnl_yen_100"]) for t in pbv2_kept)
        blocked = [t for t in pbv2_sub if not _pbv2_rise5_keep(t, TH)]
        block_pnl = sum(float(t["pnl_yen_100"]) for t in blocked)
        print(
            f"{day}: delta_all={k - b:.0f} | PBv2 delta={kp - bp:.0f} "
            f"(blocked {len(blocked)} pbv2, blocked_pnl={block_pnl:.0f})"
        )
        total_b += b
        total_k += k
    print(f"TOTAL post625 delta={total_k - total_b:.0f}")

    print("\n=== 5. Recalc on PBv2-only days (days with PBv2>0) ===")
    pbv2_days_pre = sorted({str(t.get("day")) for t in pre625 if t.get("entry_pool") == "PBV2"})
    pbv2_days_post = sorted({str(t.get("day")) for t in post625 if t.get("entry_pool") == "PBV2"})
    print(f"pre625 days with PBv2: {len(pbv2_days_pre)}")
    print(f"post625 days with PBv2: {len(pbv2_days_post)} -> {pbv2_days_post}")

    for period_name, subset, days in (
        ("full", trades, None),
        ("pbv2_days_only", trades, pbv2_days_pre + pbv2_days_post),
        ("post625_pbv2_days", post625, pbv2_days_post),
    ):
        if days is not None:
            day_set = set(days)
            sub = [t for t in subset if str(t.get("day")) in day_set]
        else:
            sub = subset
        kept = [t for t in sub if _pbv2_rise5_keep(t, TH)]
        b = sum(float(t["pnl_yen_100"]) for t in sub)
        k = sum(float(t["pnl_yen_100"]) for t in kept)
        print(f"{period_name}: n={len(sub)} delta={k - b:.0f} baseline_pnl={b:.0f} kept_pnl={k:.0f}")

    # post625 only on days with pbv2
    sub = [t for t in post625 if str(t.get("day")) in set(pbv2_days_post)]
    kept = [t for t in sub if _pbv2_rise5_keep(t, TH)]
    b = sum(float(t["pnl_yen_100"]) for t in sub)
    k = sum(float(t["pnl_yen_100"]) for t in kept)
    print(f"\npost625 restricted to PBv2 days: n={len(sub)} delta={k - b:.0f}")


if __name__ == "__main__":
    main()
