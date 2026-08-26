"""Resolve the current immutable OPVAL runtime candidate.

Not Formal V25. Not a freeze rewrite. Candidate number is not hardcoded:
the identity-only selector `active_v1r_opval_runtime_candidate.json` (or
TRADEBOT_OPVAL_RUNTIME_CANDIDATE_SELECTOR) points at whichever immutable
candidate is currently selected.

This module is intentionally outside RUNTIME_DEPENDENCY_RELS / runtime-critical
scan prefixes so Candidate-9 inventory bytes stay unchanged.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from small_paper.v1r_activation_binding import (
    NATIVE,
    SELECTOR_PATH,
    V25_ACTIVATION_ID,
    inventory_digest,
    verify_manifest_self_sha,
    verify_runtime_inventory,
    verify_selector_binding,
)

C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
OPVAL_ACTIVATION_ID = "V1R_EXIT_V2_PAPER_PRIMARY_OPVAL_CURRENT_TRADING_DAY"

ENV_OPVAL_RUNTIME_CANDIDATE_SELECTOR = "TRADEBOT_OPVAL_RUNTIME_CANDIDATE_SELECTOR"
OPVAL_RUNTIME_CANDIDATE_SELECTOR_NAME = "active_v1r_opval_runtime_candidate.json"
CANDIDATE_ID_PREFIX = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_"
STRATEGY_IDENTITY_KEYS = (
    "entry_sha",
    "anchor_sha",
    "exit_v2_candidate_sha",
    "strategy_sha",
)


def opval_runtime_candidate_out(*, native_root: Optional[Path] = None) -> Path:
    root = Path(native_root or NATIVE)
    return root / "results" / "research" / "v1r_exit_v2_prospective_activation"


def opval_runtime_candidate_selector_path(
    *,
    native_root: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    path: Optional[Path] = None,
) -> Path:
    if path is not None:
        return Path(path)
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_OPVAL_RUNTIME_CANDIDATE_SELECTOR) or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = Path(native_root or NATIVE) / p
        return p
    return opval_runtime_candidate_out(native_root=native_root) / OPVAL_RUNTIME_CANDIDATE_SELECTOR_NAME


def _load_json(path: Path) -> dict[str, Any]:
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError(f"JSON object required: {path}")
    return body


def _manifest_path(selector: Mapping[str, Any], *, native_root: Optional[Path] = None) -> Path:
    rel = str(selector.get("manifest_relpath") or "").strip()
    if not rel:
        return Path()
    p = Path(rel)
    if p.is_file():
        return p
    cand = opval_runtime_candidate_out(native_root=native_root) / rel
    if cand.is_file():
        return cand
    root = Path(native_root or NATIVE)
    alt = root / rel
    return alt if alt.is_file() else p


def _empty_resolve(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": False,
        "reason": "",
        "id": "",
        "sha256": "",
        "present": False,
        "self_sha_ok": False,
        "immutable": False,
        "working_tree_matches": False,
        "manifest": {},
        "selector": {},
        "selector_path": "",
        "manifest_path": "",
        "inventory_digest": "",
        "entry_sha": "",
        "anchor_sha": "",
        "exit_sha": "",
        "strategy_sha": "",
    }
    base.update(extra)
    return base


def resolve_current_opval_runtime_candidate(
    *,
    native_root: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    selector_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Load the current immutable OPVAL runtime candidate from the official pointer."""
    root = Path(native_root or NATIVE)
    env = dict(environ) if environ is not None else dict(os.environ)
    sel_path = opval_runtime_candidate_selector_path(
        native_root=root, environ=env, path=selector_path
    )
    try:
        if sel_path.resolve() == Path(SELECTOR_PATH).resolve():
            return _empty_resolve(
                reason="OPVAL_FORMAL_SELECTOR_SUBSTITUTION",
                selector_path=str(sel_path),
            )
    except OSError:
        return _empty_resolve(
            reason="OPVAL_FORMAL_SELECTOR_SUBSTITUTION",
            selector_path=str(sel_path),
        )
    if not sel_path.is_file():
        return _empty_resolve(
            reason="OPVAL_RUNTIME_CANDIDATE_SELECTOR_MISSING",
            selector_path=str(sel_path),
        )
    try:
        selector = _load_json(sel_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_resolve(
            reason="OPVAL_RUNTIME_CANDIDATE_SELECTOR_INVALID",
            selector_path=str(sel_path),
        )
    aid = str(selector.get("activation_id") or "").strip()
    if aid in {V25_ACTIVATION_ID, OPVAL_ACTIVATION_ID}:
        return _empty_resolve(
            reason="OPVAL_FORMAL_SELECTOR_SUBSTITUTION",
            id=aid,
            selector=selector,
            selector_path=str(sel_path),
        )
    if aid == C6_ID:
        return _empty_resolve(
            reason="OPVAL_CANDIDATE6_FORBIDDEN",
            id=aid,
            selector=selector,
            selector_path=str(sel_path),
        )
    if not aid.startswith(CANDIDATE_ID_PREFIX):
        return _empty_resolve(
            reason="OPVAL_RUNTIME_CANDIDATE_ID_INVALID",
            id=aid,
            selector=selector,
            selector_path=str(sel_path),
        )
    man_path = _manifest_path(selector, native_root=root)
    if not man_path.is_file():
        return _empty_resolve(
            reason="OPVAL_RUNTIME_CANDIDATE_MANIFEST_MISSING",
            id=aid,
            selector=selector,
            selector_path=str(sel_path),
            manifest_path=str(man_path),
        )
    try:
        manifest = _load_json(man_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return _empty_resolve(
            reason="OPVAL_BOUND_CANDIDATE_MANIFEST_INVALID",
            id=aid,
            selector=selector,
            selector_path=str(sel_path),
            manifest_path=str(man_path),
        )
    mid = str(manifest.get("manifest_id") or manifest.get("candidate_id") or "").strip()
    if mid != aid:
        return _empty_resolve(
            reason="OPVAL_BOUND_CANDIDATE_MANIFEST_INVALID",
            id=aid,
            sha256=str(manifest.get("sha256") or ""),
            present=True,
            selector=selector,
            selector_path=str(sel_path),
            manifest_path=str(man_path),
            manifest=manifest,
        )
    bind = verify_selector_binding(selector, manifest)
    ok_sha, got, calc = verify_manifest_self_sha(manifest)
    sha = str(manifest.get("sha256") or "")
    self_sha_ok = bool(ok_sha and got == calc == sha and sha)
    if not bind.get("activation_id_match") or not bind.get("activation_sha_match") or not self_sha_ok:
        return _empty_resolve(
            reason="OPVAL_BOUND_CANDIDATE_MANIFEST_INVALID",
            id=aid,
            sha256=sha,
            present=True,
            self_sha_ok=self_sha_ok,
            selector=selector,
            selector_path=str(sel_path),
            manifest_path=str(man_path),
            manifest=manifest,
        )
    if not bool(manifest.get("immutable")):
        return _empty_resolve(
            reason="OPVAL_BOUND_CANDIDATE_NOT_IMMUTABLE",
            id=aid,
            sha256=sha,
            present=True,
            self_sha_ok=True,
            immutable=False,
            selector=selector,
            selector_path=str(sel_path),
            manifest_path=str(man_path),
            manifest=manifest,
        )
    strategy = {k: str(manifest.get(k) or "").strip() for k in STRATEGY_IDENTITY_KEYS}
    if not all(strategy.values()):
        return _empty_resolve(
            reason="OPVAL_BOUND_STRATEGY_IDENTITY_INVALID",
            id=aid,
            sha256=sha,
            present=True,
            self_sha_ok=True,
            immutable=True,
            selector=selector,
            selector_path=str(sel_path),
            manifest_path=str(man_path),
            manifest=manifest,
            **{
                "entry_sha": strategy["entry_sha"],
                "anchor_sha": strategy["anchor_sha"],
                "exit_sha": strategy["exit_v2_candidate_sha"],
                "strategy_sha": strategy["strategy_sha"],
            },
        )
    inv = manifest.get("runtime_file_sha256") or {}
    inv_check = verify_runtime_inventory(manifest, native_root=root)
    digest = inventory_digest(inv) if isinstance(inv, dict) else ""
    return {
        "ok": True,
        "reason": "",
        "id": aid,
        "sha256": sha,
        "present": True,
        "self_sha_ok": True,
        "immutable": True,
        "working_tree_matches": bool(inv_check.get("ok")),
        "inventory_check": inv_check,
        "manifest": manifest,
        "selector": selector,
        "selector_path": str(sel_path),
        "manifest_path": str(man_path),
        "inventory_digest": digest,
        "entry_sha": strategy["entry_sha"],
        "anchor_sha": strategy["anchor_sha"],
        "exit_sha": strategy["exit_v2_candidate_sha"],
        "strategy_sha": strategy["strategy_sha"],
        "candidate_status": str(manifest.get("candidate_status") or ""),
        "formal_paper_allowed": bool(manifest.get("formal_paper_allowed")),
    }


def opval_bound_runtime_candidate_blocked_reason(
    manifest: Mapping[str, Any],
    *,
    native_root: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Fail-closed: OPVAL day identity must bind the currently selected immutable candidate."""
    bound_id = str(
        manifest.get("bound_current_runtime_candidate_id")
        or manifest.get("bound_current_runtime_candidate")
        or ""
    ).strip()
    bound_sha = str(manifest.get("bound_current_runtime_candidate_sha") or "").strip()
    if not bound_id or not bound_sha:
        return "OPVAL_BOUND_CANDIDATE_MISSING"
    current = resolve_current_opval_runtime_candidate(native_root=native_root, environ=environ)
    if not current.get("ok"):
        return str(current.get("reason") or "OPVAL_RUNTIME_CANDIDATE_SELECTOR_MISSING")
    if bound_id != str(current.get("id") or "") or bound_sha != str(current.get("sha256") or ""):
        return "OPVAL_BOUND_CANDIDATE_SELECTOR_MISMATCH"
    if not current.get("immutable"):
        return "OPVAL_BOUND_CANDIDATE_NOT_IMMUTABLE"
    if not current.get("self_sha_ok"):
        return "OPVAL_BOUND_CANDIDATE_MANIFEST_INVALID"
    cand = current.get("manifest") or {}
    if not isinstance(cand, dict):
        return "OPVAL_BOUND_CANDIDATE_MANIFEST_INVALID"
    opval_inv = manifest.get("runtime_file_sha256") or {}
    cand_inv = cand.get("runtime_file_sha256") or {}
    if not isinstance(opval_inv, dict) or not isinstance(cand_inv, dict):
        return "OPVAL_BOUND_CANDIDATE_INVENTORY_MISMATCH"
    if opval_inv != cand_inv or inventory_digest(opval_inv) != inventory_digest(cand_inv):
        return "OPVAL_BOUND_CANDIDATE_INVENTORY_MISMATCH"
    want_digest = str(manifest.get("runtime_inventory_digest") or "").strip()
    cand_digest = str(cand.get("runtime_inventory_digest") or current.get("inventory_digest") or "").strip()
    if want_digest and cand_digest and want_digest != cand_digest:
        return "OPVAL_BOUND_CANDIDATE_INVENTORY_MISMATCH"
    if not current.get("working_tree_matches"):
        return "OPVAL_INVENTORY_MISMATCH"
    for key in STRATEGY_IDENTITY_KEYS:
        if str(manifest.get(key) or "").strip() != str(cand.get(key) or "").strip():
            return "OPVAL_BOUND_STRATEGY_IDENTITY_MISMATCH"
    return ""
