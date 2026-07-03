#!/usr/bin/env python3
"""Phase619: event/board/trade stale semantics split (shadow)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT.parent):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def main() -> int:
    from research.phase619_freshness_stale_semantics_split import run_phase619

    report = run_phase619(repo_root=ROOT)
    print(report["verdict"])
    m = report.get("mandatory_answers", {})
    print("625_pbv2_pass_stale:", m.get("6_625_pbv2_pass_stale_combo"))
    print("score3_trade_only_rescue:", m.get("7_629_630_score3_event_ok_trade_stale_only"))
    print("liquidity_rescue:", m.get("8_liquidity_guard_pbv2_rescue_total"))
    print("p603_rescue:", m.get("9_p603_fallback_rescue_total"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
