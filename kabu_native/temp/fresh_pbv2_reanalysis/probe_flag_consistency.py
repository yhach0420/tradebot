"""Validate: do guard *_blocked flags survive OR-overlay masking on 7/1?"""
import json
from collections import Counter

p = "results/small_paper/20260701/live_session_080616/small_paper_events.jsonl"
stats = Counter()
n = 0
FLAGS = [
    "pullback_misread_dynamic40_guard_blocked",
    "high_drift_pullback_guard_blocked",
    "weak_shape_reject_guard_blocked",
    "near_day_high_low_momentum_dynamic40_guard_blocked",
    "late_chase_guard_blocked",
    "classic_late_chase_rsi_guard_blocked",
    "reentry_rsi_guard_blocked",
    "entry_quality_guard_blocked",
]
with open(p, encoding="utf-8") as f:
    for line in f:
        if '"candidate"' not in line:
            continue
        j = json.loads(line)
        if j.get("event_type") != "candidate":
            continue
        ir = j.get("pbv2_internal_reason")
        if not ir:
            continue
        n += 1
        for fl in FLAGS:
            v = j.get(fl)
            if v in (True, "True", "true"):
                stats[(ir, fl)] += 1
        # suitability reconstruction check
        if ir == "daytrade_suitability":
            atr = j.get("atr_pct")
            tv = j.get("trading_value")
            import math
            score = None
            try:
                if atr is not None and tv and float(tv) > 0:
                    score = float(atr) * math.log10(max(float(tv), 1.0))
            except Exception:
                pass
            if score is None:
                stats[("daytrade_suitability", "recon_score_missing")] += 1
            elif score < 54.695739:
                stats[("daytrade_suitability", "recon_below_threshold")] += 1
            else:
                stats[("daytrade_suitability", "recon_WOULD_PASS")] += 1
        if ir == "momentum_low_required":
            m = j.get("momentum_continuation_score")
            try:
                m = float(m)
                stats[("momentum_low_required", "recon_agree" if m > 0.2546 else "recon_DISAGREE")] += 1
            except Exception:
                stats[("momentum_low_required", "recon_missing")] += 1

print("rows with internal reason:", n)
for k, v in sorted(stats.items()):
    print(f"  {k[0]:45s} {k[1]:40s} {v}")
