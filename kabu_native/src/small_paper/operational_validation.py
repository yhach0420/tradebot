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
ENV_CAPTURE_TRADING_DATE = "TRADEBOT_CAPTURE_TRADING_DATE"
ENV_PAPER_TRADING_DATE = "TRADEBOT_PAPER_TRADING_DATE"
ENV_OPVAL_BOUND_TRADING_DATE = "TRADEBOT_OPVAL_BOUND_TRADING_DATE"
OPVAL_ACTIVATION_ID = "V1R_EXIT_V2_PAPER_PRIMARY_OPVAL_CURRENT_TRADING_DAY"
OPVAL_LEGACY_ACTIVATION_ID = "V1R_EXIT_V2_PAPER_PRIMARY_OPVAL_20260817"
OPVAL_LEGACY_PINNED_DATE = "20260817"
C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
# Deprecated alias: Live OPVAL no longer pins this calendar day.
OPVAL_TRADING_DATE = OPVAL_LEGACY_PINNED_DATE
OPVAL_ASSERTION_FAIL = "V1R_OPVAL_STARTUP_CONTRACT_FAILED"
OPVAL_HOLIDAY_CALENDAR_YEAR = "2026"

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


def _norm_day(raw: Any) -> str:
    return str(raw or "").strip().replace("-", "")[:8]


def _valid_yyyymmdd(raw: str) -> bool:
    s = _norm_day(raw)
    if len(s) != 8 or not s.isdigit():
        return False
    try:
        datetime.strptime(s, "%Y%m%d")
        return True
    except ValueError:
        return False


def opval_clock_day(*, environ: Optional[Mapping[str, str]] = None, clock_day: str = "") -> str:
    """Production session day from RuntimeClock (not date.today / OS local)."""
    if _valid_yyyymmdd(clock_day):
        return _norm_day(clock_day)
    from small_paper.runtime_clock import now_jst

    env = dict(environ) if environ is not None else None
    return now_jst(environ=env).strftime("%Y%m%d")


def resolve_opval_canonical_trading_date(
    *,
    environ: Optional[Mapping[str, str]] = None,
    explicit: Optional[str] = None,
    clock_day: str = "",
) -> tuple[str, str]:
    """Canonical Live OPVAL trading date.

    Authority: production resolve_runtime_trading_date (RuntimeClock / session
    context). Not datetime.today(), not an arbitrary CLI date alone.
    """
    env = dict(environ) if environ is not None else dict(os.environ)
    if session_clock_enabled(environ=env):
        return "", "OPVAL_SESSION_CLOCK_FORBIDDEN"
    try:
        from small_paper.session_runtime_identity import (
            RuntimeTradingDateNotProven,
            resolve_runtime_trading_date,
        )

        day = resolve_runtime_trading_date(explicit, environ=env)
    except RuntimeTradingDateNotProven:
        return "", "OPVAL_TRADING_DATE_UNRESOLVED"
    except Exception:
        return "", "OPVAL_TRADING_DATE_UNRESOLVED"
    if not _valid_yyyymmdd(day):
        return "", "OPVAL_TRADING_DATE_UNRESOLVED"
    day = _norm_day(day)
    if day[:4] != OPVAL_HOLIDAY_CALENDAR_YEAR:
        return day, "OPVAL_TRADING_DATE_UNRESOLVED"
    try:
        from research.e1_x29_prospective.calendar import is_jpx_trading_day
    except Exception:
        return day, "OPVAL_TRADING_DATE_UNRESOLVED"
    if not is_jpx_trading_day(day):
        return day, "OPVAL_NON_TRADING_DATE"
    clock = opval_clock_day(environ=env, clock_day=clock_day)
    if _valid_yyyymmdd(clock):
        if day < clock:
            return day, "OPVAL_HISTORICAL_DATE"
        if day > clock:
            return day, "OPVAL_FUTURE_DATE"
    return day, ""


def opval_trading_date_mismatch_reason(
    *,
    resolved: str,
    capture_trading_date: str = "",
    paper_trading_date: str = "",
    bound_trading_date: str = "",
) -> str:
    cap = _norm_day(capture_trading_date) or resolved
    paper = _norm_day(paper_trading_date) or resolved
    bound = _norm_day(bound_trading_date) or resolved
    if not (_valid_yyyymmdd(resolved) and _valid_yyyymmdd(cap) and _valid_yyyymmdd(paper) and _valid_yyyymmdd(bound)):
        return "OPVAL_TRADING_DATE_UNRESOLVED"
    if cap != resolved or paper != resolved or bound != resolved:
        return "OPVAL_TRADING_DATE_MISMATCH"
    return ""


def build_opval_run_binding(
    *,
    activation_id: str,
    activation_sha: str,
    source_digest: str,
    inventory_digest_value: str,
    resolved_trading_date: str,
    capture_session_id: str = "",
    capture_run_id: str = "",
    paper_stage_run_id: str = "",
    paper_run_id: str = "",
) -> dict[str, Any]:
    return {
        "schema": "V1R_OPVAL_RUN_BINDING_V1",
        "mode": CANDIDATE_STATUS_OPVAL,
        "INVALID_FOR_STRATEGY_EVALUATION": True,
        "NOT_PROSPECTIVE_DAY1": True,
        "not_formal_activation": True,
        "working_activation_id": activation_id,
        "working_activation_sha": activation_sha,
        "source_digest": source_digest,
        "runtime_inventory_digest": inventory_digest_value,
        "resolved_trading_date": _norm_day(resolved_trading_date),
        "capture_session_id": capture_session_id or "",
        "capture_run_id": capture_run_id or "",
        "paper_stage_run_id": paper_stage_run_id or "",
        "paper_run_id": paper_run_id or "",
        "created_at": datetime.now(JST).isoformat(timespec="milliseconds"),
    }


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
    resolved_trading_date: Optional[str] = None,
    capture_trading_date: Optional[str] = None,
    paper_trading_date: Optional[str] = None,
    bound_trading_date: Optional[str] = None,
    clock_day: str = "",
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
    if aid == V25_ACTIVATION_ID:
        return "OPVAL_FORMAL_SELECTOR_SUBSTITUTION"
    if aid == C6_ID:
        return "OPVAL_CANDIDATE6_FORBIDDEN"
    if aid == OPVAL_LEGACY_ACTIVATION_ID:
        return "OPVAL_LEGACY_IDENTITY_FORBIDDEN"
    if aid != OPVAL_ACTIVATION_ID:
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
    if str(manifest.get("candidate_id") or "") == C6_ID:
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

    day, date_reason = resolve_opval_canonical_trading_date(
        environ=env,
        explicit=resolved_trading_date,
        clock_day=clock_day,
    )
    if date_reason:
        return date_reason
    cap = str(capture_trading_date or env.get(ENV_CAPTURE_TRADING_DATE) or "").strip()
    paper = str(paper_trading_date or env.get(ENV_PAPER_TRADING_DATE) or env.get("TRADEBOT_TRADING_DATE") or "").strip()
    bound = str(bound_trading_date or env.get(ENV_OPVAL_BOUND_TRADING_DATE) or "").strip()
    mismatch = opval_trading_date_mismatch_reason(
        resolved=day,
        capture_trading_date=cap,
        paper_trading_date=paper,
        bound_trading_date=bound,
    )
    if mismatch:
        return mismatch
    return ""
