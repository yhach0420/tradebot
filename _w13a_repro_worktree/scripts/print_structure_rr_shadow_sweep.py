"""Print structure_relaxed_rr_shadow_sweep from a replay JSON or paper_trade_runtime_state.json."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("path", type=str, help="Path to replay_*.json or paper_trade_runtime_state.json")
    args = p.parse_args()
    fp = Path(args.path)
    if not fp.is_file():
        print(f"not found: {fp}", file=sys.stderr)
        return 2
    with open(fp, encoding="utf-8") as f:
        data = json.load(f)
    sweep = None
    if isinstance(data, dict):
        sweep = data.get("structure_relaxed_rr_shadow_sweep")
        if sweep is None:
            ov = data.get("overall_summary")
            if isinstance(ov, dict):
                sweep = ov.get("structure_relaxed_rr_shadow_sweep")
        if sweep is None:
            ov = data.get("structure_exec_diag")
            if isinstance(ov, dict):
                sweep = ov.get("structure_relaxed_rr_shadow_sweep")
    print(json.dumps(sweep or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
