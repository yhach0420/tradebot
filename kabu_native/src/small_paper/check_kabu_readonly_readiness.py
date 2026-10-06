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
import os
import sys


def main(argv: list[str] | None = None) -> int:
    from small_paper.demo_push_firewall import demo_fully_armed, isolated_native_root
    from small_paper.kabu_readonly_readiness import (
        probe_summary_for_cli,
        readiness_exit_code,
        run_readonly_readiness_probe,
    )
    from small_paper.kabu_token_authority import ENV_AUTHORITY_DIR, ENV_STATION_AUTHORITY_DIR

    if demo_fully_armed():
        root = isolated_native_root()
        (root / "data" / "market_capture" / "demo_authority").mkdir(parents=True, exist_ok=True)
        (root / "runtime" / "kabu_station_authority").mkdir(parents=True, exist_ok=True)
        os.environ[ENV_AUTHORITY_DIR] = str(root / "data" / "market_capture" / "demo_authority")
        os.environ[ENV_STATION_AUTHORITY_DIR] = str(root / "runtime" / "kabu_station_authority")

    diag = run_readonly_readiness_probe(load_env=True, allow_live=True)
    summary = probe_summary_for_cli(diag)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return readiness_exit_code(diag)


if __name__ == "__main__":
    raise SystemExit(main())
