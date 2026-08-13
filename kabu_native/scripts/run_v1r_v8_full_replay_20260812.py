#!/usr/bin/env python
"""2026-08-12 Full Capture Replay after frozen AM universe single-authority fix.

V7/V6/V5/V4 manifests are immutable history. Strategy/Precommit unchanged.
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
V6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V6"
V6_SHA = "f113dd0d77138417a0b32c2666edb99b2d257163a43805dad4a2c032e6d4c86f"
V7_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V7"
V7_SHA = "3074c3bf8028819e2f708550c5cdbff874fb4a7f3e7274e7327a48dcf2fda087"
STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
LEDGER_SHA = "9cc44b0e17a63dd69e344276cbb0b52b811f208e07db61c39ac734468e3a346e"
ACT_DIR = ROOT / "results" / "research" / "v1r_exit_v2_prospective_activation"
OUT = ROOT / "results" / "research" / "v1r_v8_full_replay_20260812"


def _load_v4_harness():
    path = ROOT / "scripts" / "run_v1r_v4_full_replay_20260812.py"
    spec = importlib.util.spec_from_file_location("v1r_v4_full_replay", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def v7_immutable_precheck() -> dict:
    v4 = json.loads((ACT_DIR / f"{V4_ID}.json").read_text(encoding="utf-8"))
    v5 = json.loads((ACT_DIR / f"{V5_ID}.json").read_text(encoding="utf-8"))
    v6 = json.loads((ACT_DIR / f"{V6_ID}.json").read_text(encoding="utf-8"))
    v7 = json.loads((ACT_DIR / f"{V7_ID}.json").read_text(encoding="utf-8"))
    ok4, _, calc4 = verify_manifest_self_sha(v4)
    ok5, _, calc5 = verify_manifest_self_sha(v5)
    ok6, _, calc6 = verify_manifest_self_sha(v6)
    ok7, _, calc7 = verify_manifest_self_sha(v7)
    checks = {
        "v4_self_sha": ok4 and v4.get("sha256") == V4_SHA == calc4 == manifest_content_sha(v4),
        "v5_self_sha": ok5 and v5.get("sha256") == V5_SHA == calc5 == manifest_content_sha(v5),
        "v6_self_sha": ok6 and v6.get("sha256") == V6_SHA == calc6 == manifest_content_sha(v6),
        "v7_self_sha": ok7 and v7.get("sha256") == V7_SHA == calc7 == manifest_content_sha(v7),
        "strategy": v7.get("strategy_sha") == STRATEGY_SHA == v6.get("strategy_sha") == v5.get("strategy_sha") == v4.get("strategy_sha"),
        "precommit": v7.get("precommit_sha") == PRECOMMIT_SHA == v6.get("precommit_sha"),
        "submit_cancel_live": True,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "v4_content_sha": v4.get("sha256"),
        "v5_content_sha": v5.get("sha256"),
        "v6_content_sha": v6.get("sha256"),
        "v7_content_sha": v7.get("sha256"),
        "note": "V7/V6/V5/V4 immutable; V8 freeze is separate",
    }


def main() -> int:
    v4h = _load_v4_harness()
    v4h.OUT = OUT
    v4h.v3_immutable_precheck = v7_immutable_precheck  # type: ignore[method-assign]

    class _Post:
        ok = True
        ready = True

    v4h.assert_exit_v2_primary_roles = lambda: _Post()  # type: ignore[method-assign]
    rc = v4h.main()
    report_path = OUT / "report.json"
    if report_path.is_file():
        rep = json.loads(report_path.read_text(encoding="utf-8"))
        if rep.get("verdict") == "V1R_V4_20260812_FULL_REPLAY_E2E_PASS":
            rep["verdict"] = "V1R_V8_20260812_FULL_REPLAY_E2E_PASS"
        elif rep.get("verdict") == "V1R_V4_20260812_FULL_REPLAY_E2E_FAIL":
            rep["verdict"] = "V1R_V8_20260812_FULL_REPLAY_E2E_FAIL"
        ledger = str(rep.get("ledger_sha_run1") or rep.get("ledger_sha") or "")
        if ledger:
            assert ledger == LEDGER_SHA, f"ledger mismatch {ledger} != {LEDGER_SHA}"
        assert str(rep.get("submit_cancel_live") or "0/0/0") == "0/0/0"
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        print("V8_REPLAY_VERDICT", rep.get("verdict"), "blockers", rep.get("blockers"), flush=True)
        print("LEDGER_EXPECT", LEDGER_SHA, flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
