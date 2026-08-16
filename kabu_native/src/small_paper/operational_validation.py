"""Operational Validation Paper startup contract (fail-closed).

Separate from Formal Paper and from Certification.
Not a Candidate. Not a freeze. Not a bypass of activation/inventory.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.runtime_clock import (
    ENV_ARM_FILE,
    ENV_ENABLED,
    ENV_MARKET_INPUT_MODE,
    ENV_REPLAY_NOT_BEFORE,
    ENV_REPLAY_PATH,
    ENV_SPEED,
    ENV_STOP,
    ENV_T0,
    ENV_V0,
    MARKET_INPUT_REPLAY,
    MARKET_INPUT_SYNTHETIC,
    certification_mode,
    ingress_replay_path,
    session_clock_enabled,
    skip_cert_gate,
)
from small_paper.v1r_activation_binding import (
    CANDIDATE_STATUS_OPVAL,
    NATIVE,
    SELECTOR_PATH,
    V25_ACTIVATION_ID,
    candidate_source_digest,
    file_sha256,
    inventory_digest,
    resolve_selector_path,
    verify_manifest_self_sha,
    verify_runtime_inventory,
    verify_selector_binding,
)

JST = ZoneInfo("Asia/Tokyo")

ENV_OPVAL_MODE = "TRADEBOT_OPERATIONAL_VALIDATION_MODE"
OPVAL_ACTIVATION_ID = "V1R_EXIT_V2_PAPER_PRIMARY_OPVAL_20260817"
OPVAL_TRADING_DATE = "20260817"
OPVAL_ASSERTION_FAIL = "V1R_OPVAL_STARTUP_CONTRACT_FAILED"

_TRUE = {"1", "true", "yes", "on"}

OPVAL_LABELS = {
    "paper_mode": CANDIDATE_STATUS_OPVAL,
    "INVALID_FOR_STRATEGY_EVALUATION": True,
    "NOT_PROSPECTIVE_DAY1": True,
    "formal_paper_allowed": False,
    "prospective_allowed": False,
    "strategy_evaluation_allowed": False,
}

_FORBIDDEN_CLOCK_ENV = (
    ENV_ENABLED,
    ENV_V0,
    ENV_T0,
    ENV_SPEED,
    ENV_STOP,
    ENV_ARM_FILE,
)
_FORBIDDEN_REPLAY_ENV = (
    ENV_REPLAY_PATH,
    ENV_REPLAY_NOT_BEFORE,
)


def _flag(name: str, *, environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(name, "") or "").strip().lower() in _TRUE


def operational_validation_mode(*, environ: Optional[Mapping[str, str]] = None) -> bool:
    return _flag(ENV_OPVAL_MODE, environ=environ)


def current_git_head() -> str:
    import subprocess

    repo = NATIVE.parent
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(repo), text=True).strip()


def current_config_sha() -> str:
    cfg = NATIVE / "configs" / "small_paper_pilot.yaml"
    if not cfg.is_file():
        return ""
    return file_sha256(cfg)


def _nonzero(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    raw = str(value).strip().lower()
    if raw in {"", "0", "false", "no", "off"}:
        return False
    try:
        return int(raw) != 0
    except (TypeError, ValueError):
        return raw not in {"0/0/0"}


def opval_startup_blocked_reason(
    selector: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    environ: Optional[Mapping[str, str]] = None,
    native_root: Optional[Path] = None,
) -> str:
    """Fail-closed OPVAL contract. Empty string means this contract PASSed.

    Separate from Formal Paper and Certification. Inventory and identity still bind.
    """
    env = dict(environ) if environ is not None else dict(os.environ)
    if not operational_validation_mode(environ=env):
        return "OPVAL_MODE_REQUIRED"
    if certification_mode(environ=env):
        return "OPVAL_CERTIFICATION_MODE_FORBIDDEN"
    if skip_cert_gate(environ=env):
        return "OPVAL_SKIP_CERT_GATE_FORBIDDEN"

    sel_path = resolve_selector_path(environ=env)
    try:
        if sel_path.resolve() == Path(SELECTOR_PATH).resolve():
            return "OPVAL_FORMAL_SELECTOR_SUBSTITUTION"
    except OSError:
        return "OPVAL_FORMAL_SELECTOR_SUBSTITUTION"
    aid = str(selector.get("activation_id") or "").strip()
    if aid == V25_ACTIVATION_ID or aid != OPVAL_ACTIVATION_ID:
        return "OPVAL_FORMAL_SELECTOR_SUBSTITUTION"
    mid = str(manifest.get("manifest_id") or manifest.get("candidate_id") or "").strip()
    if mid != OPVAL_ACTIVATION_ID:
        return "OPVAL_IDENTITY_MISMATCH"
    status = str(manifest.get("candidate_status") or "").strip()
    if status != CANDIDATE_STATUS_OPVAL:
        return "OPVAL_STATUS_MISMATCH"
    if bool(manifest.get("formal_paper_allowed")):
        return "OPVAL_FORMAL_PAPER_FORBIDDEN"
    if bool(manifest.get("prospective_allowed")):
        return "OPVAL_PROSPECTIVE_FORBIDDEN"
    if bool(manifest.get("strategy_evaluation_allowed")):
        return "OPVAL_STRATEGY_EVALUATION_FORBIDDEN"
    if str(manifest.get("candidate_id") or "") == "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6":
        return "OPVAL_CANDIDATE6_FORBIDDEN"

    bind = verify_selector_binding(selector, manifest)
    if not bind.get("activation_id_match") or not bind.get("activation_sha_match"):
        return "OPVAL_SELECTOR_SHA_MISMATCH"
    ok_sha, got, calc = verify_manifest_self_sha(dict(manifest))
    if not (ok_sha and got and got == calc == str(selector.get("activation_sha") or "")):
        return "OPVAL_MANIFEST_SHA_MISMATCH"

    inv = verify_runtime_inventory(manifest, native_root=native_root)
    if not inv.get("ok"):
        return "OPVAL_INVENTORY_MISMATCH"
    want_digest = str(manifest.get("runtime_inventory_digest") or "").strip()
    got_inv = manifest.get("runtime_file_sha256") or {}
    if want_digest and inventory_digest(got_inv) != want_digest:
        return "OPVAL_INVENTORY_DIGEST_MISMATCH"
    want_src = str(manifest.get("candidate_source_digest") or "").strip()
    if want_src:
        got_src = candidate_source_digest(got_inv, native_root=native_root)
        if got_src != want_src:
            return "OPVAL_SOURCE_DIGEST_MISMATCH"
    want_git = str(manifest.get("runtime_code_git_commit") or "").strip()
    if want_git:
        try:
            got_git = current_git_head()
        except Exception:
            return "OPVAL_GIT_HEAD_MISMATCH"
        if got_git != want_git:
            return "OPVAL_GIT_HEAD_MISMATCH"
    want_cfg = str(manifest.get("config_sha256") or "").strip()
    if want_cfg:
        got_cfg = current_config_sha()
        if not got_cfg or got_cfg != want_cfg:
            return "OPVAL_CONFIG_SHA_MISMATCH"

    if not bool(manifest.get("paper_only", False)):
        return "OPVAL_PAPER_ONLY_REQUIRED"
    if bool(manifest.get("order_enabled")) or _nonzero(manifest.get("order_enabled")):
        return "OPVAL_ORDER_ENABLED"
    if bool(manifest.get("live_trading_enabled")) or _nonzero(manifest.get("live_trading_enabled")):
        return "OPVAL_LIVE_TRADING_ENABLED"
    scl = str(manifest.get("submit_cancel_live") or "").strip()
    if scl != "0/0/0":
        return "OPVAL_SUBMIT_CANCEL_LIVE"
    for key, code in (("submit", "OPVAL_SUBMIT"), ("cancel", "OPVAL_CANCEL"), ("live", "OPVAL_LIVE")):
        if _nonzero(manifest.get(key)):
            return code

    if session_clock_enabled(environ=env):
        return "OPVAL_SESSION_CLOCK_FORBIDDEN"
    for key in _FORBIDDEN_CLOCK_ENV:
        if str(env.get(key) or "").strip():
            return "OPVAL_SESSION_CLOCK_FORBIDDEN"
    for key in _FORBIDDEN_REPLAY_ENV:
        if str(env.get(key) or "").strip():
            return "OPVAL_REPLAY_PATH_FORBIDDEN"
    if ingress_replay_path(environ=env):
        return "OPVAL_REPLAY_PATH_FORBIDDEN"
    mode = str(env.get(ENV_MARKET_INPUT_MODE) or "").strip().upper()
    if mode in {MARKET_INPUT_REPLAY, MARKET_INPUT_SYNTHETIC}:
        return "OPVAL_REPLAY_PATH_FORBIDDEN"

    day = str(env.get("TRADEBOT_TRADING_DATE") or env.get("TRADEBOT_SESSION_TRADING_DATE") or "").strip()
    if not day:
        day = datetime.now(JST).strftime("%Y%m%d")
    if day != OPVAL_TRADING_DATE:
        return "OPVAL_HISTORICAL_DATE"

    return ""
