"""Republish placebo semantics for NATIVE_PARTICIPATION_X_SR_CONTEXT_DISCOVERY_V1. No walk."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.fixed_daytrade_universe_v1.secrets import assert_no_secret
from research.native_participation_x_sr_context_discovery_v1.analyze import repair_placebo_semantics
from research.native_participation_x_sr_context_discovery_v1.isolation import OUT
from research.native_participation_x_sr_context_discovery_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
from research.native_participation_x_sr_context_discovery_v1.spec import source_sha256


def main() -> int:
    path = OUT / "report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    verd = str((report.get("decision") or {}).get("VERDICT") or "")
    nxt = str((report.get("decision") or {}).get("NEXT") or "")
    holm = (report.get("multiple_testing") or {}).get("holm")
    report = repair_placebo_semantics(report)
    assert str((report.get("decision") or {}).get("VERDICT") or "") == verd
    assert str((report.get("decision") or {}).get("NEXT") or "") == nxt
    assert (report.get("multiple_testing") or {}).get("holm") == holm
    hashes = dict(report.get("hashes") or {})
    hashes["SOURCE_SHA256"] = source_sha256()
    report["hashes"] = hashes
    report["_markdown"] = build_markdown(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)
    ans = dict(report.get("answers") or {}).get("Does S/R × participation show an interaction larger than placebo?")
    print(f"OUT {OUT}", flush=True)
    print(f"placebo_interaction {ans}", flush=True)
    print(f"VERDICT {verd}", flush=True)
    print(f"NEXT {nxt}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
