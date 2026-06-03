#!/usr/bin/env python3
"""
Phase242b: intraday refresh failed fix report (review + implementation).

Writes:
- kabu_native/results/reports/phase242b_intraday_refresh_fix_report.json

This report is based on code-level behavior + unit tests. It does not run live.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native" / "results" / "reports" / "phase242b_intraday_refresh_fix_report.json"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "phase": "242b",
        "mode": "intraday_refresh_fix_report",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "entry_change_forbidden": True,
            "score_change_forbidden": True,
            "yaml_change_forbidden": True,
            "forced_unhold_forbidden": True,
            "open_symbols_drop_forbidden": True,
            "hard_reject_add_forbidden": True,
        },
        "root_cause": "open_symbols_exceed_cap aborted refresh before register_symbols()",
        "fix": {
            "location": "universe.intraday_refresh.merge_universe_with_open_symbols",
            "behavior": [
                "Keep open symbols first (no drops) if open_symbols_count <= 50",
                "Remove duplicates from refresh universe",
                "Fill remaining slots from refresh universe up to 50-N",
                "Call register_symbols on exactly 50 symbols (or N if N==50)",
                "If open_symbols_count > 50, keep failing with open_symbols_exceed_cap",
            ],
        },
        "added_logs": [
            "open_symbols_count",
            "refresh_csv_rows",
            "carried_open_symbols_count",
            "refresh_symbols_added_count",
            "final_register_count",
            "register_called",
            "register_success",
            "fallback_reason",
        ],
        "unit_tests": {
            "file": "kabu_native/tests/test_phase242b_intraday_refresh_merge_cap50.py",
            "cases": [0, 3, 8, 50, 51],
            "expected": {
                "0_50": "success (final_register_count==50, register_count_ok)",
                "51": "fail (open_symbols_exceed_cap)",
            },
        },
        "notes": [
            "This change only affects intraday refresh universe registration; it does not alter entry conditions or scoring.",
            "Open symbols are preserved by design; refresh universe is trimmed to fit cap.",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

