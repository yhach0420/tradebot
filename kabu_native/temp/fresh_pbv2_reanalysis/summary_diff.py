"""Investigation A/B: diff all scalar summary fields across sessions."""
import json, csv

SESSIONS = [
    ("20260624", "081514", "AM"), ("20260624", "122521", "PM"),
    ("20260625", "080340", "AM"), ("20260625", "122535", "PM"),
    ("20260629", "080236", "AM"), ("20260629", "122526", "PM"),
    ("20260630", "091118", "AM"),
    ("20260701", "080616", "AM"),
]

data = {}
allkeys = []
seen = set()
for d, s, ap in SESSIONS:
    p = f"results/small_paper/{d}/live_session_{s}/small_paper_summary.json"
    j = json.load(open(p, encoding="utf-8"))
    flat = {}
    for k, v in j.items():
        if isinstance(v, (int, float, bool, str)) or v is None:
            flat[k] = v
        elif isinstance(v, dict) and k == "reject_reason_counts":
            for rk, rv in v.items():
                flat[f"reject.{rk}"] = rv
    data[(d, ap)] = flat
    for k in flat:
        if k not in seen:
            seen.add(k)
            allkeys.append(k)

cols = [f"{d}_{ap}" for d, s, ap in SESSIONS]
rows = []
for k in allkeys:
    vals = [data[(d, ap)].get(k, "") for d, s, ap in SESSIONS]
    sv = set(str(v) for v in vals)
    differs = len(sv) > 1
    rows.append([k, differs] + vals)

with open("temp/fresh_pbv2_reanalysis/summary_all_fields.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["key", "differs"] + cols)
    w.writerows(rows)

# print numeric keys with interesting divergence between 0625 and 0629
print(f"{'key':60s} " + " ".join(f"{c:>12s}" for c in cols))
for r in rows:
    k = r[0]
    vals = r[2:]
    def num(v):
        try: return float(v)
        except Exception: return None
    n25 = num(vals[2]); n29 = num(vals[4])
    if n25 is None or n29 is None:
        continue
    if abs(n25 - n29) > max(5, 0.3 * max(abs(n25), abs(n29))):
        print(f"{k:60s} " + " ".join(f"{str(v)[:12]:>12s}" for v in vals))
