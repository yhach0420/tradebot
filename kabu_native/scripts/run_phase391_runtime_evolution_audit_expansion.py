#!/usr/bin/env python3
"""Phase391: Runtime Evolution Audit Expansion — audit CSV → SoT MD + satellite docs."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_full_system_development_history import (  # noqa: E402
    OUT_ADOPTION_FUNNEL,
    OUT_DEPENDENCY_GRAPH,
    OUT_RUNTIME_LOG,
    generate,
)


def main() -> int:
    out, old_lines, new_lines = generate(refresh_audit=False)
    print(f"phase391 complete: {out}")
    print(f"satellite: {OUT_RUNTIME_LOG}")
    print(f"satellite: {OUT_DEPENDENCY_GRAPH}")
    print(f"satellite: {OUT_ADOPTION_FUNNEL}")
    print(f"line delta: {new_lines - old_lines:+d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
