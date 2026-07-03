"""Probe: sample candidate events for field availability per day."""
import json, sys

SESSIONS = [
    ("20260624", "081514"), ("20260624", "122521"),
    ("20260625", "080340"), ("20260625", "122535"),
    ("20260629", "080236"), ("20260629", "122526"),
    ("20260630", "091118"),
    ("20260701", "080616"),
]

for d, s in SESSIONS:
    p = f"results/small_paper/{d}/live_session_{s}/small_paper_events.jsonl"
    tot = 0
    with_pbv2 = 0
    sample = None
    keys = set()
    with open(p, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= 5000:
                break
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("event_type") != "candidate":
                continue
            tot += 1
            keys |= set(j.keys())
            if "pbv2_internal_reason" in j:
                with_pbv2 += 1
                if sample is None and j.get("gate_reject_reason") == "or_overlay_not_candidate":
                    sample = j
    print(d, s, "candidates:", tot, "pbv2_internal_reason:", with_pbv2, "n_keys:", len(keys))
    if sample:
        interesting = {k: sample.get(k) for k in [
            "gate_reject_reason", "pbv2_internal_reason", "entry_expectancy_score_v2",
            "entry_score_v2_gate_pass", "price_age_sec", "board_age_sec",
            "price_freshness_source", "fallback_used"]}
        print("   sample:", json.dumps(interesting, ensure_ascii=False))
