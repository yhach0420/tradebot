import json
from pathlib import Path
from collections import Counter
from research.e1_x28_executable_joint.board import load_board_events
from research.e1_x22_actual_exit_factory.paths import _load_price_events

days = ["2026-08-05", "2026-08-06", "2026-08-07"]
root = Path("data/push_jsonl")
for day in days:
    d = root / day
    files = sorted(d.glob("*.jsonl"))
    print(f"=== {day} n_files={len(files)} ===")
    keys_payload = Counter()
    has_cp = has_s1 = has_b1 = lines = 0
    cp_ok = s1_ok = b1_ok = 0
    sample_files = files[:3] + files[len(files)//2:len(files)//2+1] + files[-2:]
    for fp in sample_files:
        n = 0
        with fp.open("rb") as f:
            for line in f:
                if not line.strip():
                    continue
                n += 1
                if n > 150:
                    break
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                lines += 1
                pay = obj.get("payload") or {}
                for k in pay.keys():
                    keys_payload[k] += 1
                if pay.get("CurrentPrice") not in (None, ""):
                    has_cp += 1
                    try:
                        if float(pay["CurrentPrice"]) > 0:
                            cp_ok += 1
                    except Exception:
                        pass
                s1 = pay.get("Sell1") or {}
                b1 = pay.get("Buy1") or {}
                if isinstance(s1, dict) and s1.get("Price") not in (None, ""):
                    has_s1 += 1
                    try:
                        if float(s1["Price"]) > 0:
                            s1_ok += 1
                    except Exception:
                        pass
                if isinstance(b1, dict) and b1.get("Price") not in (None, ""):
                    has_b1 += 1
                    try:
                        if float(b1["Price"]) > 0:
                            b1_ok += 1
                    except Exception:
                        pass
    print("sampled_lines", lines, "CurrentPrice", has_cp, "Sell1", has_s1, "Buy1", has_b1)
    print("ok_cp", cp_ok, "ok_s1", s1_ok, "ok_b1", b1_ok)
    print("top_payload_keys", keys_payload.most_common(20))
    for fp in files[:5]:
        sym = fp.name.replace(".T.jsonl", "").replace(".jsonl", "")
        day_nd = day.replace("-", "")
        b = load_board_events(day_nd, sym)
        t, p = _load_price_events(day_nd, sym)
        print(f"  {sym}: board_events={b['t'].size} price_events={t.size}")
