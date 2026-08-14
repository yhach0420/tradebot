#!/usr/bin/env python
"""2026-08-13 Capture streaming parity after V13 frozen-universe SoT fix.

Reuses the V10 streaming harness (native process_market_push) with a V13 output dir.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "results" / "research" / "v1r_v13_streaming_parity_20260813"


def main() -> int:
    path = ROOT / "scripts" / "run_v1r_v10_streaming_parity_20260813.py"
    spec = importlib.util.spec_from_file_location("v1r_v10_streaming_parity", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.OUT = OUT
    rc = mod.main()
    report_path = OUT / "report.json"
    if report_path.is_file():
        rep = json.loads(report_path.read_text(encoding="utf-8"))
        v = str(rep.get("verdict") or "")
        if "V10" in v:
            rep["verdict"] = v.replace("V10", "V13")
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        print("V13_STREAMING_PARITY", rep.get("verdict"), flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
