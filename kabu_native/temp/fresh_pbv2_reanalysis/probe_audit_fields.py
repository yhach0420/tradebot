"""Probe: audit jsonl schema differences per day."""
import json

SESSIONS = [
    ("20260624", "081514"), ("20260625", "080340"),
    ("20260629", "080236"), ("20260630", "091118"), ("20260701", "080616"),
]
for d, s in SESSIONS:
    p = f"results/small_paper/{d}/live_session_{s}/entry_scan_audit.jsonl"
    keys = set()
    types = set()
    n = 0
    with open(p, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= 3000:
                break
            j = json.loads(line)
            types.add(j.get("audit_type"))
            if j.get("audit_type") == "entry_symbol_eval":
                keys |= set(j.keys())
                n += 1
    print(d, s, "types:", sorted(types), "eval_keys:", sorted(keys))
    print()
