"""Audit + write V1R_PBV2_DUPLICATE_RUNTIME_CONTAMINATION artifacts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))

from small_paper.v1r_pbv2_duplicate_runtime import (  # noqa: E402
    audit_duplicate_runtime,
    write_verdict_artifacts,
)


def main() -> int:
    day = "20260812"
    if len(sys.argv) > 1:
        day = str(sys.argv[1])
    audit = audit_duplicate_runtime(native_root=NATIVE, trading_date=day)
    paths = write_verdict_artifacts(audit, native_root=NATIVE)
    print(json.dumps({"paths": paths, "summary": {
        "verdict": audit.get("verdict"),
        "contaminated": audit.get("contaminated"),
        "counts": audit.get("counts"),
        "classes": audit.get("classes"),
    }}, indent=2, ensure_ascii=False))
    return 0 if not audit.get("contaminated") else 2


if __name__ == "__main__":
    raise SystemExit(main())
