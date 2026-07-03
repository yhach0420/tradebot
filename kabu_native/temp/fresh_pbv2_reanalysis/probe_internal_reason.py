"""Probe: pbv2_internal_reason distribution on 7/1 (ground truth) + volume_gate_shadow structure."""
import json
from collections import Counter

p = "results/small_paper/20260701/live_session_080616/small_paper_events.jsonl"
c = Counter()
final = Counter()
pairs = Counter()
n = 0
with open(p, encoding="utf-8") as f:
    for line in f:
        if '"candidate"' not in line:
            continue
        j = json.loads(line)
        if j.get("event_type") != "candidate":
            continue
        n += 1
        fr = j.get("gate_reject_reason") or ("ACCEPT" if j.get("gate_accept") else "")
        ir = j.get("pbv2_internal_reason", "(absent)")
        final[fr] += 1
        c[ir] += 1
        pairs[(fr, ir)] += 1
print("candidates:", n)
print("--- final gate_reject_reason ---")
for k, v in final.most_common(20):
    print(f"  {k:45s} {v}")
print("--- pbv2_internal_reason ---")
for k, v in c.most_common(25):
    print(f"  {k:45s} {v}")
print("--- top (final, internal) pairs ---")
for (a, b), v in pairs.most_common(25):
    print(f"  {a:35s} <- {b:40s} {v}")

print()
print("=== volume_gate_shadow_eval sample (6/29 AM) ===")
with open("results/small_paper/20260629/live_session_080236/volume_gate_shadow_eval.jsonl", encoding="utf-8") as f:
    for i, line in enumerate(f):
        print(line.strip()[:600])
        if i >= 2:
            break
