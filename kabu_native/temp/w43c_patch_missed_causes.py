import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

OUT = Path(r"c:\Users\yhach\Documents\tradebotfile\kabu_native\results\research\pre_entry_market_state")
ep = pd.read_csv(OUT / "w43c_20260717_opportunity_episodes.csv")
missed = ep[ep["capture_class"] == "MISSED"]
c = dict(Counter(missed["opportunity_class"]))
print("missed causes", c)

r = json.loads((OUT / "w43c_20260717_report.json").read_text(encoding="utf-8"))
r["required_answers"]["4_missed_main_causes"] = c
# also fix capture summary cap/rule counts already from day_cap
(OUT / "w43c_20260717_report.json").write_text(
    json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
md = (OUT / "w43c_20260717_report.md").read_text(encoding="utf-8")
md2 = re.sub(r"4\. Missed causes: `.*?`", "4. Missed causes: `" + str(c) + "`", md)
(OUT / "w43c_20260717_report.md").write_text(md2, encoding="utf-8")
print("patched")
