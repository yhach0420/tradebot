"""Run E1_X6_FCRR Phase A (SELLING_EXHAUSTED reachability audit)."""
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

from research.e1_x6_fcrr.phase_a_audit import run_phase_a  # noqa: E402
from research.e1_x6_fcrr.phase_a_publish import publish_phase_a  # noqa: E402


def main() -> None:
    print("=== Phase A: SE reachability audit (no economics) ===", flush=True)
    print("reference=e1x6_fcrr_20260803_075026_e53466 status=FCRR_V1_FIXED_THRESHOLD_UNREACHABLE_REFERENCE", flush=True)
    report = run_phase_a()
    store = Path.home() / "e1x6_research_store" / "fcrr" / report["phase_a_run_id"]
    shas = publish_phase_a(report, store)
    print("=== PHASE_A_PUBLISHED ===", flush=True)
    print("phase_a_run_id", report["phase_a_run_id"], flush=True)
    for k, v in shas.items():
        print(k, v, flush=True)
    print("answers_summary", json.dumps({
        "q1": report["answers"]["q1_pullback_active_25644_meaning"]["answer"],
        "q2_unique_pb_episodes": report["answers"]["q2_unique_pullback_episodes"],
        "q5_top": (report["answers"]["q5_dominant_reject"] or [])[:3],
        "q6": report["answers"]["q6_no_new_low_30s_pass_events"],
        "q11": report["answers"]["q11_context_to_pullback_transitions"],
        "se_pass_events": report["se_pass_events"],
    }, ensure_ascii=False), flush=True)
    print("STOP_PHASE_A", flush=True)


if __name__ == "__main__":
    main()
