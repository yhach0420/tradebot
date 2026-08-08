"""Run TAER Trigger-Anchored Entry study revision."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE / "research"))

from research.e1_x6_taer.run_study import run_taer_study  # noqa: E402


def main() -> None:
    print("=== TAER E1_X6_TRIGGER_ANCHORED_ENTRY_EXIT_JOINT_V1 ===", flush=True)
    print("prior PhaseA=FCRR_SE_REACHABILITY_AUDIT_COMPLETE PhaseB=FCRR_SEQUENTIAL_ENTRY_FAMILY_UNREACHABLE", flush=True)
    rep = run_taer_study()
    print("=== TAER_PUBLISHED ===", flush=True)
    print("run_id", rep["run_id"], flush=True)
    print("verdict", rep["verdict"], flush=True)
    print("anchors", (rep.get("anchor_audit") or {}).get("unique_anchor_episodes"), flush=True)
    print("selected_profile", rep.get("selected_profile"), flush=True)
    print("entry_observation_n", rep.get("entry_observation_n"), flush=True)
    for k, v in (rep.get("published") or {}).items():
        print(k, v, flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
