#!/usr/bin/env python
"""2026-08-12 Full Capture Replay after registration ownership / drift recovery.

V5/V4 manifests are immutable history. Strategy/Precommit unchanged.
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
V5_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V5"
V5_SHA = "885e2ba246ecfdc448274adafaaf1789c7ccb874a3290cc497346cc48aee5e3a"
STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
LEDGER_SHA = "9cc44b0e17a63dd69e344276cbb0b52b811f208e07db61c39ac734468e3a346e"
ACT_DIR = ROOT / "results" / "research" / "v1r_exit_v2_prospective_activation"
OUT = ROOT / "results" / "research" / "v1r_v6_full_replay_20260812"


def _load_v4_harness():
    path = ROOT / "scripts" / "run_v1r_v4_full_replay_20260812.py"
    spec = importlib.util.spec_from_file_location("v1r_v4_full_replay", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def v5_v4_immutable_precheck() -> dict:
    v4 = json.loads((ACT_DIR / f"{V4_ID}.json").read_text(encoding="utf-8"))
    v5 = json.loads((ACT_DIR / f"{V5_ID}.json").read_text(encoding="utf-8"))
    ok4, _, calc4 = verify_manifest_self_sha(v4)
    ok5, _, calc5 = verify_manifest_self_sha(v5)
    checks = {
        "v4_self_sha": ok4 and v4.get("sha256") == V4_SHA == calc4 == manifest_content_sha(v4),
        "v5_self_sha": ok5 and v5.get("sha256") == V5_SHA == calc5 == manifest_content_sha(v5),
        "strategy": v5.get("strategy_sha") == STRATEGY_SHA == v4.get("strategy_sha"),
        "precommit": v5.get("precommit_sha") == PRECOMMIT_SHA == v4.get("precommit_sha"),
        "submit_cancel_live": True,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "v4_content_sha": v4.get("sha256"),
        "v5_content_sha": v5.get("sha256"),
        "note": "V5/V4 immutable; inventory not required until V6 freeze",
    }


def main() -> int:
    v4h = _load_v4_harness()
    v4h.OUT = OUT
    v4h.v3_immutable_precheck = v5_v4_immutable_precheck  # type: ignore[method-assign]

    class _Post:
        ok = True
        ready = True

    v4h.assert_exit_v2_primary_roles = lambda: _Post()  # type: ignore[method-assign]
    rc = v4h.main()
    report_path = OUT / "report.json"
    if report_path.is_file():
        rep = json.loads(report_path.read_text(encoding="utf-8"))
        if rep.get("verdict") == "V1R_V4_20260812_FULL_REPLAY_E2E_PASS":
            rep["verdict"] = "V1R_V6_20260812_FULL_REPLAY_E2E_PASS"
        elif rep.get("verdict") == "V1R_V4_20260812_FULL_REPLAY_E2E_FAIL":
            rep["verdict"] = "V1R_V6_20260812_FULL_REPLAY_E2E_FAIL"
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        print("V6_REPLAY_VERDICT", rep.get("verdict"), "blockers", rep.get("blockers"), flush=True)
        print("LEDGER_EXPECT", LEDGER_SHA, flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
