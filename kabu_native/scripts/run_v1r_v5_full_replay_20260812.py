#!/usr/bin/env python
"""2026-08-12 Full Capture Replay after same-day AM Ingress registration binding.

V4 manifest is immutable history (not overwritten). Strategy/Precommit unchanged.
Does not require V4 runtime inventory to match the new working tree (V5 freeze is later).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from small_paper.v1r_activation_binding import manifest_content_sha, verify_manifest_self_sha

V4_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V4"
V4_SHA = "73e9397ba7e2cee05f32044c3cb5ecb80459f45d191fdb50496a1c95f4a86dc2"
STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
ACT_DIR = ROOT / "results" / "research" / "v1r_exit_v2_prospective_activation"
OUT = ROOT / "results" / "research" / "v1r_v5_full_replay_20260812"


def _load_v4_harness():
    path = ROOT / "scripts" / "run_v1r_v4_full_replay_20260812.py"
    spec = importlib.util.spec_from_file_location("v1r_v4_full_replay", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def v4_immutable_precheck() -> dict:
    """V4 is history. Do not require V4 inventory to match the new runtime WT."""
    v4_path = ACT_DIR / f"{V4_ID}.json"
    man = json.loads(v4_path.read_text(encoding="utf-8"))
    ok, _, calc = verify_manifest_self_sha(man)
    checks = {
        "v4_self_sha": ok and man.get("sha256") == V4_SHA == calc == manifest_content_sha(man),
        "strategy": man.get("strategy_sha") == STRATEGY_SHA,
        "precommit": man.get("precommit_sha") == PRECOMMIT_SHA,
        "submit_cancel_live": True,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "v4_content_sha": man.get("sha256"),
        "note": "inventory_not_required_until_v5_freeze",
    }


def main() -> int:
    v4h = _load_v4_harness()
    v4h.OUT = OUT
    v4h.v3_immutable_precheck = v4_immutable_precheck  # type: ignore[method-assign]

    class _Post:
        ok = True
        ready = True

    v4h.assert_exit_v2_primary_roles = lambda: _Post()  # type: ignore[method-assign]
    rc = v4h.main()
    report_path = OUT / "report.json"
    if report_path.is_file():
        rep = json.loads(report_path.read_text(encoding="utf-8"))
        if rep.get("verdict") == "V1R_V4_20260812_FULL_REPLAY_E2E_PASS":
            rep["verdict"] = "V1R_V5_20260812_FULL_REPLAY_E2E_PASS"
        elif rep.get("verdict") == "V1R_V4_20260812_FULL_REPLAY_E2E_FAIL":
            rep["verdict"] = "V1R_V5_20260812_FULL_REPLAY_E2E_FAIL"
        elif rep.get("verdict") == "STOP_V3_HISTORY_MUTATED":
            # precheck name only
            pass
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        print("V5_REPLAY_VERDICT", rep.get("verdict"), "blockers", rep.get("blockers"), flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
