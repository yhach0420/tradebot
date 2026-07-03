"""Dump 2 full candidate events (score>=3, or_overlay_not_candidate) from 6/29 PM and 6/25 AM."""
import json

def show(path, want_reason, n=1):
    print("=" * 70)
    print(path, "reason:", want_reason)
    shown = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if shown >= n:
                break
            if '"candidate"' not in line:
                continue
            j = json.loads(line)
            if j.get("event_type") != "candidate":
                continue
            if j.get("gate_reject_reason") != want_reason:
                continue
            v2 = j.get("entry_expectancy_score_v2")
            try:
                v2 = int(v2)
            except Exception:
                continue
            if v2 < 3:
                continue
            shown += 1
            for k in sorted(j.keys()):
                print(f"  {k} = {json.dumps(j[k], ensure_ascii=False)[:150]}")

show("results/small_paper/20260629/live_session_122526/small_paper_events.jsonl", "or_overlay_not_candidate")
show("results/small_paper/20260625/live_session_080340/small_paper_events.jsonl", "momentum_low_required")
