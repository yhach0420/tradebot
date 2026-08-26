"""P2-4A runner. Research contract + synthetic only. No Capture. No production changes."""
from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.trailing10_dynamic_anchor_p2_4a.publish import build_report, write_artifacts


def main() -> int:
    rep = build_report()
    paths = write_artifacts(rep)
    print(rep["verdict"], paths["report_json"], flush=True)
    print(
        f"BINDING={rep['CURRENT_ENTRY_TIME_BINDING']} "
        f"SYNTHETIC={rep['SYNTHETIC_TESTS']['passed']}/{rep['SYNTHETIC_TESTS']['n']} "
        f"SPEC_FROZEN={rep['SPEC_FROZEN']}",
        flush=True,
    )
    return 0 if rep["verdict"] == "P2_4A_TRAILING10_EDGE_PRECOMMITTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
