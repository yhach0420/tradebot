#!/usr/bin/env python
"""V26-G5: Candidate-5 short proofs then Full Runtime Preflight. No Formal freeze."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
G5 = NATIVE / "results" / "research" / "v26g5_candidate5_preflight"
SELECTOR = NATIVE / "results/research/v1r_exit_v2_prospective_activation/active_v1r_candidate_v26g4_5.json"
SHORT = NATIVE / "scripts" / "run_v26g5_candidate5_short_proof.py"
IDENT = NATIVE / "scripts" / "assert_v26g5_candidate5_identity.py"
FULL = NATIVE / "scripts" / "run_paper_full_day_certification.py"
PY = sys.executable
STAGES = ("window_a", "am_pm", "pm_direct", "window_c", "fill_am", "restart")


def _run(args: list[str], *, timeout: int, env: dict[str, str] | None = None) -> int:
    print("RUN", " ".join(args), flush=True)
    proc = subprocess.run(args, cwd=str(NATIVE), env=env, timeout=timeout)
    return int(proc.returncode)


def main() -> int:
    G5.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = "src;.."
    env["TRADEBOT_ACTIVATION_SELECTOR"] = str(SELECTOR)
    rc = _run([PY, str(IDENT)], timeout=120, env=env)
    if rc != 0:
        (G5 / "verdict.json").write_text(
            json.dumps({"verdict": "V1R_V26G5_CANDIDATE5_IDENTITY_DRIFT"}, indent=2) + "\n",
            encoding="utf-8",
        )
        return 2
    short: dict = {}
    for stage in STAGES:
        timeout = 4200 if stage == "fill_am" else 2700
        rc = _run([PY, str(SHORT), "--stage", stage], timeout=timeout, env=env)
        path = G5 / f"short_{stage}.json"
        body = {}
        if path.is_file():
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                body = {}
        short[stage] = {"rc": rc, "proof_ok": body.get("proof_ok"), "fail": body.get("proof_fail_reasons")}
        (G5 / "short_sequence.json").write_text(json.dumps(short, indent=2) + "\n", encoding="utf-8")
        if rc != 0 or body.get("proof_ok") is False:
            verdict = {
                "verdict": "V1R_V26G5_CANDIDATE5_SHORT_PROOF_FAIL",
                "CANDIDATE5_SHORT_PROOF_PASS": False,
                "failed_stage": stage,
                "short": short,
            }
            (G5 / "verdict.json").write_text(json.dumps(verdict, indent=2, default=str) + "\n", encoding="utf-8")
            print(json.dumps(verdict, default=str), flush=True)
            return 2
    (G5 / "verdict.json").write_text(
        json.dumps(
            {
                "verdict": "CANDIDATE5_SHORT_PROOF_PASS",
                "CANDIDATE5_SHORT_PROOF_PASS": True,
                "short": short,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("CANDIDATE5_SHORT_PROOF_PASS=true; starting Full Runtime Preflight", flush=True)
    full_env = dict(env)
    full_env["TRADEBOT_ACTIVATION_SELECTOR"] = str(SELECTOR)
    rc = _run([PY, str(FULL)], timeout=86400, env=full_env)
    (G5 / "full_preflight_exit.json").write_text(
        json.dumps({"exit_code": rc}, indent=2) + "\n", encoding="utf-8"
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
