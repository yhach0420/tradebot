#!/usr/bin/env python
"""Run PBv2 zero-base candidate revalidation → report.md / report.json / audit.xlsx."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.pbv2_zero_base_revalidation.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    payload = run_pipeline(native=ROOT)
    v = payload.get("verdict") or {}
    print("verdict:", v.get("final"))
    print("codes:", ",".join(v.get("codes") or []))
    print("n_panel:", payload.get("n_panel"))
    print("n_pbv2:", payload.get("n_pbv2_candidates"))
    print("n_non_pbv2:", payload.get("n_non_pbv2"))
    print("large_rise:", (payload.get("large_rise_summary") or {}).get("large_rise_episode_total"))
    print("best:", (payload.get("best_candidate") or {}).get("rule_id"))
    print("out:", payload.get("out_dir"))
    print("submit/cancel/live_order:", payload.get("submit"), payload.get("cancel"), payload.get("live_order"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
