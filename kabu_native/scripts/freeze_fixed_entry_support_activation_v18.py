#!/usr/bin/env python
"""Freeze FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V18 from working-tree inventory.

Parent V17 remains immutable. ENTRY/EXIT/complete SHAs and the 15:20 PM flatten
contract are unchanged. This recertifies the C3-aware pre-paper ready seal so
startup can bind C3PaperSessionExecutor without SHA spoofing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

from small_paper.activation_current_publish import publish_current_activation
from small_paper.paper_full_day_certification import write_current_activation_certification
from small_paper.paper_primary_activation import (
    ACTIVATION_V17_ID,
    ACTIVATION_V18_ID,
    CANDIDATE_MANIFEST_SHA,
    ENTRY_SHA,
    EXIT_SHA,
    safe_pm_complete_sha,
)
from small_paper.v1r_activation_binding import (
    ENV_ACTIVATION_SELECTOR,
    OUT,
    RUNTIME_DEPENDENCY_RELS,
    SELECTOR_PATH,
    collect_runtime_inventory,
    inventory_digest,
    manifest_content_sha,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

JST = ZoneInfo("Asia/Tokyo")
PARENT_SHA = "dcc5e179a4b79d039945e6866738dffa8e6906e46e74270146ed916eb563bb45"
SUPERSEDE_REASON = "C3_SEAL_ACCEPTS_X1_OWNER_CLASSES"
REQUIRED_C3_RELS = (
    "src/small_paper/c3_paper_session.py",
    "src/research/v16_bid_freshness_residual_accounting_v1/run.py",
    "src/small_paper/pre_paper_ready_seal.py",
)


def _git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=str(REPO), text=True).strip()


def main() -> int:
    parent = json.loads((OUT / f"{ACTIVATION_V17_ID}.json").read_text(encoding="utf-8"))
    assert parent["sha256"] == PARENT_SHA == manifest_content_sha(parent)
    assert parent["entry_sha"] == ENTRY_SHA
    assert parent["exit_sha"] == EXIT_SHA
    assert parent["complete_strategy_sha"] == safe_pm_complete_sha()
    assert parent["candidate_manifest_sha"] == CANDIDATE_MANIFEST_SHA
    assert parent["pm_flatten_start"] == "15:20:00"
    assert parent["pm_continuous_execution_deadline"] == "15:25:00"
    assert parent["pm_session_flat_1530"] is False
    assert parent["session_executor"] == "FixedSupportX1SessionExecutor"
    assert parent["execution_family"] == "X1_IMMEDIATE_ASK"

    head = _git(["rev-parse", "HEAD"])
    inv = collect_runtime_inventory(native_root=NATIVE)
    assert len(inv) == len(RUNTIME_DEPENDENCY_RELS)
    for rel in REQUIRED_C3_RELS:
        assert rel in inv, rel
    digest = inventory_digest(inv)

    body = {k: v for k, v in parent.items() if k != "sha256"}
    body.update(
        {
            "manifest_id": ACTIVATION_V18_ID,
            "activation_id": ACTIVATION_V18_ID,
            "parent_activation_id": ACTIVATION_V17_ID,
            "parent_activation_sha": PARENT_SHA,
            "parent_activation_status": "SUPERSEDED_IMMUTABLE_HISTORY",
            "supersede_reason": SUPERSEDE_REASON,
            "binding_reason": SUPERSEDE_REASON,
            "runtime_file_sha256": inv,
            "runtime_inventory_digest": digest,
            "runtime_code_git_commit": head,
            "strategy_changed": False,
            "universe_membership_changed": False,
            "created_at": datetime.now(JST).isoformat(timespec="seconds"),
        }
    )
    assert body["entry_sha"] == ENTRY_SHA
    assert body["exit_sha"] == EXIT_SHA
    assert body["complete_strategy_sha"] == safe_pm_complete_sha()
    assert body["submit_cancel_live"] == "0/0/0"
    assert body["paper_only"] is True
    assert body["live"] is False
    assert body["order_enabled"] is False
    assert body["session_executor"] == "FixedSupportX1SessionExecutor"
    body["sha256"] = manifest_content_sha(body)
    path = OUT / f"{ACTIVATION_V18_ID}.json"
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    ok, got, calc = verify_manifest_self_sha(loaded)
    assert ok and got == calc == body["sha256"]
    inv_check = verify_runtime_inventory(loaded, native_root=NATIVE)
    assert inv_check.get("ok") is True, inv_check

    parent_after = json.loads((OUT / f"{ACTIVATION_V17_ID}.json").read_text(encoding="utf-8"))
    assert parent_after["sha256"] == PARENT_SHA

    tmp_sel = OUT / "_tmp_v18_selector.json"
    tmp_sel.write_text(
        json.dumps(
            {
                "schema": "V1R_ACTIVE_ACTIVATION_SELECTOR_V1",
                "activation_id": ACTIVATION_V18_ID,
                "activation_sha": body["sha256"],
                "manifest_relpath": f"{ACTIVATION_V18_ID}.json",
                "note": "Temporary selector for certification write only.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.environ[ENV_ACTIVATION_SELECTOR] = str(tmp_sel)
    try:
        cert = write_current_activation_certification(native_root=NATIVE)
    finally:
        os.environ.pop(ENV_ACTIVATION_SELECTOR, None)
        tmp_sel.unlink(missing_ok=True)

    if str(cert.get("verdict") or "") != "FIXED_ENTRY_SUPPORT_RUNTIME_PRE_PAPER_CERTIFICATION_PASS_V1":
        print(json.dumps({"ok": False, "stage": "cert", "cert": cert}, ensure_ascii=False, indent=2))
        return 2

    published = publish_current_activation(
        manifest_path=path,
        selector_path=SELECTOR_PATH,
    )
    if not published.get("ok"):
        print(json.dumps({"ok": False, "stage": "publish", **published}, ensure_ascii=False, indent=2))
        return 3

    parent_final = json.loads((OUT / f"{ACTIVATION_V17_ID}.json").read_text(encoding="utf-8"))
    assert parent_final["sha256"] == PARENT_SHA
    report = {
        "ok": True,
        "activation_id": ACTIVATION_V18_ID,
        "activation_sha": body["sha256"],
        "parent_activation_id": ACTIVATION_V17_ID,
        "parent_activation_sha": PARENT_SHA,
        "runtime_commit": head,
        "inventory_digest": digest,
        "inventory_n": len(inv),
        "inventory_match": True,
        "strategy_changed": False,
        "entry_sha": ENTRY_SHA,
        "exit_sha": EXIT_SHA,
        "complete_strategy_sha": body["complete_strategy_sha"],
        "submit_cancel_live": "0/0/0",
        "published": True,
        "cert_path": cert.get("path"),
        "selector_path": str(SELECTOR_PATH),
        "supersede_reason": SUPERSEDE_REASON,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
