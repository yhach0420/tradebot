"""NQ/ES sector/symbol causal response. No Kabu 50. Frozen Validation closed. Runtime 0/0/0."""
from __future__ import annotations

import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))


def main() -> int:
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")
    from research.nq_es_sector_symbol_response_v1.__main__ import main as run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
