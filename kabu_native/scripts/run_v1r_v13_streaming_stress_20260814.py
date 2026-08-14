#!/usr/bin/env python
"""V13 consumer-stack streaming + stress on 2026-08-14 Capture 09:14–09:16:30.

Reuses the V12 harness; V12 consumer performance must be preserved.
Does not start live Paper. Strategy results are not scored.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "results" / "research" / "v1r_v13_streaming_stress_20260814"


def main() -> int:
    path = ROOT / "scripts" / "run_v1r_v12_streaming_stress_20260814.py"
    spec = importlib.util.spec_from_file_location("v1r_v12_streaming_stress", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.OUT = OUT
    rc = mod.main()
    report_path = OUT / "report.json"
    if report_path.is_file():
        rep = json.loads(report_path.read_text(encoding="utf-8"))
        v = str(rep.get("verdict") or "")
        if "V12" in v:
            rep["verdict"] = v.replace("V12", "V13")
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        print("V13_STREAMING_STRESS", rep.get("verdict"), flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
