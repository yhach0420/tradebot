#!/usr/bin/env python
"""Re-run 20260817 Exact+Fast after SESSION_CLOSE wall-clock leak fix."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from run_p0_4_exact_vs_fast_parity import compare_pair, run_replay  # noqa: E402

DAY = "20260817"
CAP = ROOT / "data" / "market_capture" / DAY / "session_ing_20260817_26844_1786921426_67a67ec6"
OUT = ROOT / "results" / "research" / "exact_vs_fast_replay_parity_p0_4" / "_0817_rerun.json"


def main() -> int:
    print("0817 Exact rerun", flush=True)
    exact = run_replay(DAY, CAP, mode="exact")
    print(
        f"  trades={len(exact['trades'])} pnl={exact['pnl']} sha={exact['ledger_sha']} "
        f"sec={exact['elapsed_sec']} holes={exact['sequence_holes']}",
        flush=True,
    )
    last = exact["trades"][-1]
    print(
        f"  last {last['symbol']} {last['anchor_time']} exit={last['exit_time_iso']} reason={last['exit_reason']}",
        flush=True,
    )
    print("0817 Fast rerun", flush=True)
    fast = run_replay(DAY, CAP, mode="fast")
    print(
        f"  trades={len(fast['trades'])} pnl={fast['pnl']} sha={fast['ledger_sha']} "
        f"sec={fast['elapsed_sec']} holes={fast['sequence_holes']}",
        flush=True,
    )
    lastf = fast["trades"][-1]
    print(
        f"  last {lastf['symbol']} {lastf['anchor_time']} exit={lastf['exit_time_iso']} reason={lastf['exit_reason']}",
        flush=True,
    )
    cmp_ = compare_pair(DAY, exact, fast)
    print("cmp", {k: cmp_[k] for k in cmp_ if k != "rows"}, flush=True)
    OUT.write_text(
        json.dumps({"exact": exact, "fast": fast, "cmp": cmp_}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return 0 if cmp_["sha_match"] and cmp_["trade_mismatch"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
