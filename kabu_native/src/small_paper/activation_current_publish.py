"""Publish a paper activation as current only after a matching certification.

The checked-runner gate stays in place. This module is the activation update
step that must run first. It does not generate a certification, and it does
not rewrite an existing activation manifest.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from small_paper.paper_full_day_certification import (
    CURRENT_CERT_PASS,
    activation_cert_path,
)

REASON_MISSING = "MISSING"
REASON_FAIL = "FAIL"
REASON_STALE = "STALE"
REASON_ACTIVATION_ID = "ACTIVATION_ID_MISMATCH"
REASON_ACTIVATION_SHA = "ACTIVATION_SHA_MISMATCH"
REASON_CANDIDATE = "CANDIDATE_MISMATCH"
REASON_EXECUTION_FAMILY = "EXECUTION_FAMILY_MISMATCH"
REASON_SESSION_EXECUTOR = "SESSION_EXECUTOR_MISMATCH"
REASON_INVENTORY = "INVENTORY_MISMATCH"
REASON_ORDER_CONTRACT = "ORDER_CONTRACT"

SELECTOR_SCHEMA = "V1R_ACTIVE_ACTIVATION_SELECTOR_V1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def certification_identity(cert: Mapping[str, Any]) -> dict[str, str]:
    nested = cert.get("identity") or cert.get("identity_before") or {}
    if not isinstance(nested, dict):
        nested = {}
    return {
        "activation_id": _text(cert.get("activation_id") or nested.get("activation_id")),
        "activation_sha": _text(cert.get("activation_sha") or nested.get("activation_sha")),
        "candidate_name": _text(
            cert.get("candidate_name")
            or nested.get("candidate_name")
            or nested.get("primary_strategy")
        ),
        "execution_family": _text(cert.get("execution_family") or nested.get("execution_family")),
        "session_executor": _text(cert.get("session_executor") or nested.get("session_executor")),
        "inventory_digest": _text(cert.get("inventory_digest") or nested.get("inventory_digest")),
    }


def manifest_identity(manifest: Mapping[str, Any]) -> dict[str, str]:
    return {
        "activation_id": _text(manifest.get("activation_id") or manifest.get("manifest_id")),
        "activation_sha": _text(manifest.get("sha256") or manifest.get("activation_sha")),
        "candidate_name": _text(manifest.get("candidate_name") or manifest.get("primary_strategy")),
        "execution_family": _text(manifest.get("execution_family")),
        "session_executor": _text(manifest.get("session_executor")),
        "inventory_digest": _text(
            manifest.get("runtime_inventory_digest") or manifest.get("inventory_digest")
        ),
        "parent_activation_sha": _text(manifest.get("parent_activation_sha")),
    }


def assess_pre_paper_certification(
    manifest: Mapping[str, Any],
    cert: Optional[Mapping[str, Any]],
) -> str:
    """Return a fail-closed reason, or "" when the cert matches this manifest."""
    if not cert:
        return REASON_MISSING
    verdict = _text(cert.get("verdict"))
    failed = list(cert.get("failed_tests") or [])
    if verdict != CURRENT_CERT_PASS or failed:
        return REASON_FAIL
    got = certification_identity(cert)
    want = manifest_identity(manifest)
    if got["activation_id"] != want["activation_id"]:
        return REASON_ACTIVATION_ID
    if got["activation_sha"] != want["activation_sha"]:
        parent = want["parent_activation_sha"]
        if parent and got["activation_sha"] == parent:
            return REASON_STALE
        return REASON_ACTIVATION_SHA
    for key, reason in (
        ("candidate_name", REASON_CANDIDATE),
        ("execution_family", REASON_EXECUTION_FAMILY),
        ("session_executor", REASON_SESSION_EXECUTOR),
    ):
        if got[key] != want[key]:
            return reason
    if got["inventory_digest"] != want["inventory_digest"]:
        return REASON_INVENTORY
    if not _order_contract_clean(manifest):
        return REASON_ORDER_CONTRACT
    return ""


def _order_contract_clean(manifest: Mapping[str, Any]) -> bool:
    if int(manifest.get("submit") or 0) != 0:
        return False
    if int(manifest.get("cancel") or 0) != 0:
        return False
    live = manifest.get("live")
    if live is True or int(live or 0) != 0:
        return False
    if _text(manifest.get("submit_cancel_live") or "0/0/0") != "0/0/0":
        return False
    if manifest.get("order_enabled") is True or manifest.get("live_trading_enabled") is True:
        return False
    return True


def _load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"expected JSON object: {path}")
    return body


def publish_current_activation(
    *,
    manifest_path: Path,
    selector_path: Path,
    cert_dir: Optional[Path] = None,
    cert_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Point the current selector at manifest_path only when certification matches.

    On any failure the selector file is left untouched. The manifest is never
    rewritten. This function does not create a certification artifact.
    """
    manifest_path = Path(manifest_path)
    selector_path = Path(selector_path)
    before = selector_path.read_bytes() if selector_path.is_file() else None
    manifest = _load_json(manifest_path)
    if manifest is None:
        return _refused("MANIFEST_MISSING", before=before, selector_path=selector_path)
    ident = manifest_identity(manifest)
    if cert_path is not None:
        loaded = _load_json(Path(cert_path))
    else:
        loaded = _load_json(activation_cert_path(ident["activation_id"], cert_dir=cert_dir))
    reason = assess_pre_paper_certification(manifest, loaded)
    if reason:
        return _refused(reason, before=before, selector_path=selector_path)
    if manifest_path.parent.resolve() == selector_path.parent.resolve():
        rel = manifest_path.name
    else:
        rel = str(manifest_path.resolve())
    selector = {
        "schema": SELECTOR_SCHEMA,
        "activation_id": ident["activation_id"],
        "activation_sha": ident["activation_sha"],
        "manifest_relpath": rel,
        "note": "Identity-only selector; no Strategy/Precommit/trading fields.",
    }
    selector_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = selector_path.with_suffix(selector_path.suffix + ".publish_tmp")
    tmp.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, selector_path)
    return {
        "ok": True,
        "published": True,
        "reason": "",
        "activation_id": ident["activation_id"],
        "activation_sha": ident["activation_sha"],
        "selector_path": str(selector_path),
        "submit_cancel_live": "0/0/0",
    }


def _refused(reason: str, *, before: Optional[bytes], selector_path: Path) -> dict[str, Any]:
    after = selector_path.read_bytes() if selector_path.is_file() else None
    if after != before:
        raise RuntimeError("publish refusal changed the current selector")
    return {
        "ok": False,
        "published": False,
        "reason": reason,
        "selector_unchanged": True,
        "selector_path": str(selector_path),
    }
