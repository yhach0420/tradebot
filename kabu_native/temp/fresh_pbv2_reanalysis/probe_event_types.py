import json
from collections import Counter

p = "results/small_paper/20260625/live_session_080340/small_paper_events.jsonl"
c = Counter()
acc = None
with open(p, encoding="utf-8") as f:
    for line in f:
        try:
            j = json.loads(line)
        except Exception:
            c["_parse_err"] += 1
            continue
        c[j.get("event_type")] += 1
        if j.get("event_type") == "accepted" and acc is None:
            acc = j
print(c.most_common())
if acc:
    ks = ["symbol", "entry_time", "entry_type", "or_reason", "gate_accept", "entry_expectancy_score_v2",
          "momentum_continuation_score", "entry_order_book_imbalance", "atr_pct", "trading_value",
          "continuation_quality_score", "universe_bucket"]
    print({k: acc.get(k, "(none)") for k in ks})
