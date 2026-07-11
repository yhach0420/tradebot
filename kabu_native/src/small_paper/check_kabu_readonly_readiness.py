"""CLI: python -m small_paper.check_kabu_readonly_readiness

Exit codes:
  0 READONLY_READY
  2 STATION_OR_TOKEN_NOT_READY
  3 AUTH_OR_CONFIG_ERROR
  4 RESPONSE_INVALID
  5 SAFETY_INVARIANT_FAILED
"""

from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    from small_paper.kabu_readonly_readiness import (
        probe_summary_for_cli,
        readiness_exit_code,
        run_readonly_readiness_probe,
    )

    diag = run_readonly_readiness_probe(load_env=True, allow_live=True)
    summary = probe_summary_for_cli(diag)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return readiness_exit_code(diag)


if __name__ == "__main__":
    raise SystemExit(main())
