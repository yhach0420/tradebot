"""Frozen V4 source inventory. Does not write V4 files."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from research.pb1_v4_clarified_machine_correction_v4.definitions import MACHINE_FILES, STATE_MACHINE_TEXT, machine_sha256
from research.pb1_v4_clarified_machine_correction_v4.spec import SOURCE_FILES, source_sha256
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep import (
    DETECTOR_SHA256,
    EXPECTED_SPEC_SHA,
    EXPECTED_V2_SHA,
    EXPECTED_V3_SHA,
    EXPECTED_V4_MACHINE_SHA256,
    EXPECTED_V4_SOURCE_SHA256,
    FROZEN_IDENTITY,
    SR_STATE_MACHINE_SHA256,
)
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.isolation import (
    NATIVE,
    V4_RUNNER,
    V4_SRC,
    V4_TEST,
)
from research.pb1_v4_semantic_spec_clarification.spec import spec_sha256


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    if not path.is_file():
        return ""
    return _sha_bytes(path.read_bytes())


def _git(args: list[str]) -> str:
    try:
        out = subprocess.check_output(["git", *args], cwd=str(NATIVE), stderr=subprocess.DEVNULL)
        return out.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _role(rel: str) -> str:
    norm = rel.replace("\\", "/")
    if "/pb1_v4_clarified_machine_correction_v4/" in f"/{norm}" or norm.endswith("pb1_v4_clarified_machine_correction_v4.py"):
        if "freeze_and_prospective_prep" in norm:
            return "FREEZE_PREP_ONLY"
        if "test_pb1_v4_clarified_machine_correction_v4.py" in norm:
            return "V4_TEST_CONSTITUENT"
        if "run_pb1_v4_clarified_machine_correction_v4.py" in norm:
            return "V4_RUNNER_CONSTITUENT"
        return "V4_MACHINE_CONSTITUENT"
    if "freeze_and_prospective_prep" in norm:
        return "FREEZE_PREP_ONLY"
    return "UNRELATED"


def file_inventory() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in SOURCE_FILES:
        p = V4_SRC / name
        rows.append(
            {
                "path": str(p.relative_to(NATIVE)).replace("\\", "/"),
                "role": "V4_MACHINE_CONSTITUENT",
                "in_source_sha": True,
                "in_machine_sha": name in MACHINE_FILES or name == "definitions.py",
                "sha256": _sha_file(p),
            }
        )
    extra = [("tests/research/test_pb1_v4_clarified_machine_correction_v4.py", V4_TEST, "V4_TEST_CONSTITUENT"), ("scripts/run_pb1_v4_clarified_machine_correction_v4.py", V4_RUNNER, "V4_RUNNER_CONSTITUENT")]
    for rel, p, role in extra:
        rows.append({"path": rel, "role": role, "in_source_sha": False, "in_machine_sha": False, "sha256": _sha_file(p)})
    return rows


def config_sha256() -> str:
    from research import pb1_v4_clarified_machine_correction_v4 as m

    keys = sorted(k for k in dir(m) if k.isupper() and not k.startswith("_"))
    payload = {k: getattr(m, k) for k in keys}
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return _sha_bytes(raw)


def source_inventory_sha(rows: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(str(r.get("path")).encode("utf-8"))
        h.update(str(r.get("sha256")).encode("utf-8"))
    return h.hexdigest()


def test_sha256() -> str:
    return _sha_file(V4_TEST)


def git_snapshot() -> dict[str, Any]:
    status = _git(["status", "--porcelain"])
    commit = _git(["rev-parse", "HEAD"])
    dirty_rows = []
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().replace("\\", "/")
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        dirty_rows.append({"status": line[:2].strip(), "path": path, "role": _role(path)})
    v4_dirty = [r for r in dirty_rows if str(r.get("role") or "").startswith("V4_")]
    return {
        "git_commit": commit,
        "working_tree_clean": status == "",
        "dirty_n": len(dirty_rows),
        "dirty": dirty_rows,
        "v4_constituent_dirty_n": len(v4_dirty),
        "v4_constituent_dirty": v4_dirty,
        "unrelated_or_prep_dirty_n": len(dirty_rows) - len(v4_dirty),
    }


def environment_identity() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "cwd": str(NATIVE),
    }


def freeze_identity(*, frozen_at: str) -> dict[str, Any]:
    rows = file_inventory()
    live_machine = machine_sha256()
    live_source = source_sha256()
    live_spec = spec_sha256()
    git = git_snapshot()
    state_text_sha = _sha_bytes(STATE_MACHINE_TEXT.encode("utf-8"))
    cfg = config_sha256()
    tests = test_sha256()
    inv_sha = source_inventory_sha(rows)
    identity_ok = (
        live_machine == EXPECTED_V4_MACHINE_SHA256
        and live_source == EXPECTED_V4_SOURCE_SHA256
        and live_spec == EXPECTED_SPEC_SHA
    )
    return {
        "frozen_identity": FROZEN_IDENTITY,
        "ok": identity_ok,
        "frozen_at": frozen_at,
        "machine_sha": live_machine,
        "EXPECTED_V4_MACHINE_SHA256": EXPECTED_V4_MACHINE_SHA256,
        "source_inventory_sha": inv_sha,
        "v4_source_sha": live_source,
        "EXPECTED_V4_SOURCE_SHA256": EXPECTED_V4_SOURCE_SHA256,
        "config_sha": cfg,
        "test_sha": tests,
        "spec_sha": live_spec,
        "parent_v2_sha": EXPECTED_V2_SHA,
        "parent_v3_sha": EXPECTED_V3_SHA,
        "detector_sha": DETECTOR_SHA256,
        "sr_state_machine_sha": SR_STATE_MACHINE_SHA256,
        "v4_state_machine_text_sha": state_text_sha,
        "files": rows,
        "git": git,
        "environment": environment_identity(),
        "V4_CHANGED": False,
        "identity_mismatch": not identity_ok,
    }
