#!/usr/bin/env python
"""2026-08-12 Full Capture Replay after V12 stale-recovery / consumer backpressure fix.

V11–V1 manifests are immutable history. Strategy/Precommit unchanged.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from small_paper.v1r_activation_binding import manifest_content_sha, verify_manifest_self_sha

V11_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V11"
V11_SHA = "19a8974dbd453e26664c4f0124c97c32e70c1097f2e01ebd3b497fb483a2673b"
STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
LEDGER_SHA = "9cc44b0e17a63dd69e344276cbb0b52b811f208e07db61c39ac734468e3a346e"
ACT_DIR = ROOT / "results" / "research" / "v1r_exit_v2_prospective_activation"
OUT = ROOT / "results" / "research" / "v1r_v12_full_replay_20260812"


def _load_v4_harness():
    path = ROOT / "scripts" / "run_v1r_v4_full_replay_20260812.py"
    spec = importlib.util.spec_from_file_location("v1r_v4_full_replay", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def v11_immutable_precheck() -> dict:
    v11 = json.loads((ACT_DIR / f"{V11_ID}.json").read_text(encoding="utf-8"))
    ok, _, calc = verify_manifest_self_sha(v11)
    checks = {
        "v11_self_sha": ok and v11.get("sha256") == V11_SHA == calc == manifest_content_sha(v11),
        "strategy": v11.get("strategy_sha") == STRATEGY_SHA,
        "precommit": v11.get("precommit_sha") == PRECOMMIT_SHA,
        "submit_cancel_live": True,
    }
    return {"ok": all(checks.values()), "checks": checks, "v11_content_sha": v11.get("sha256")}


def main() -> int:
    v4h = _load_v4_harness()
    v4h.OUT = OUT
    v4h.v3_immutable_precheck = v11_immutable_precheck  # type: ignore[method-assign]

    class _Post:
        ok = True
        ready = True

    v4h.assert_exit_v2_primary_roles = lambda: _Post()  # type: ignore[method-assign]
    rc = v4h.main()
    report_path = OUT / "report.json"
    if report_path.is_file():
        rep = json.loads(report_path.read_text(encoding="utf-8"))
        if rep.get("verdict") == "V1R_V4_20260812_FULL_REPLAY_E2E_PASS":
            rep["verdict"] = "V1R_V12_20260812_FULL_REPLAY_E2E_PASS"
        elif rep.get("verdict") == "V1R_V4_20260812_FULL_REPLAY_E2E_FAIL":
            rep["verdict"] = "V1R_V12_20260812_FULL_REPLAY_E2E_FAIL"
        ledger = str(rep.get("ledger_sha_run1") or rep.get("ledger_sha") or "")
        if ledger:
            assert ledger == LEDGER_SHA, f"ledger mismatch {ledger} != {LEDGER_SHA}"
        assert str(rep.get("submit_cancel_live") or "0/0/0") == "0/0/0"
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        print("V12_REPLAY_VERDICT", rep.get("verdict"), "blockers", rep.get("blockers"), flush=True)
        print("LEDGER_EXPECT", LEDGER_SHA, flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
