"""Run the frozen complete-strategy economic feasibility and stop."""
from __future__ import annotations

import json

from research.causal_driver_pb1.sector_state_alpha_complete_economic.publish import build, publish
from research.causal_driver_pb1.sector_state_alpha_complete_economic.replay import run_replay
from research.causal_driver_pb1.sector_state_alpha_complete_economic.verify import identity_check


def main() -> int:
    before = identity_check()
    if not before.get("ok"):
        report = build({"ok": False, "prospective_rows_read": 0}, before, before)
        publish(report)
        print(json.dumps({"VERDICT": report["verdict"], "NEXT": report["next"]}), flush=True)
        return 2
    result = run_replay(list(before.get("symbols") or []))
    after = identity_check()
    report = build(result, before, after)
    publish(report)
    print(json.dumps({"VERDICT": report["verdict"], "NEXT": report["next"], "filled": (report.get("funnel") or {}).get("filled_trade_n")}), flush=True)
    return 0 if report["verdict"].endswith("PASS_V1") or report["verdict"].endswith("FAIL_V1") else 2


if __name__ == "__main__":
    raise SystemExit(main())
