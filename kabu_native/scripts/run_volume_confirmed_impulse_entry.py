#!/usr/bin/env python
"""Run VCIE offline research → report.md / report.json / audit.xlsx."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.volume_confirmed_impulse_entry.pipeline import run_pipeline  # noqa: E402


def main() -> int:
    payload = run_pipeline(native=ROOT)
    v = payload.get("verdict") or {}
    ev = payload.get("evaluation") or {}
    print("verdict:", v.get("final"))
    print("codes:", ",".join(v.get("codes") or []))
    print("capture_days:", payload.get("capture_days"))
    print("event_rows:", payload.get("complete_event_rows"))
    print("triggers:", ev.get("triggers_summary"))
    print("out:", payload.get("out_dir"))
    print("submit/cancel/live_order:", payload.get("submit"), payload.get("cancel"), payload.get("live_order"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
