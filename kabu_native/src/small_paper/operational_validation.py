"""Operational Validation Paper startup contract (fail-closed).

Separate from Formal Paper and from Certification.
Not a Candidate. Not a freeze. Not a bypass of activation/inventory.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
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
ENV_OPVAL_DEGRADED_UNIVERSE = "TRADEBOT_OPVAL_DEGRADED_UNIVERSE_ONLY"
DEGRADED_OPVAL_READY = "DEGRADED_OPVAL_READY"
OPVAL_DEGRADED_MODE = "OPVAL_DEGRADED_UNIVERSE_ONLY"
OPVAL_TERMINAL_INVALID_TODAY = "4449"
OPVAL_KABU_SYMBOL_NOT_FOUND = "4002001"
EXPECTED_FROZEN_N = 50
EXPECTED_DEGRADED_ACTIVE_N = 49
_PUSH_SYMBOL_RE = re.compile(r'"symbol"\s*:\s*"([^"]+)"')
OPVAL_ACTIVATION_ID = "V1R_EXIT_V2_PAPER_PRIMARY_OPVAL_CURRENT_TRADING_DAY"
OPVAL_LEGACY_ACTIVATION_ID = "V1R_EXIT_V2_PAPER_PRIMARY_OPVAL_20260817"
OPVAL_LEGACY_PINNED_DATE = "20260817"
C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
C7_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G7_7"
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


def opval_degraded_universe_mode(*, environ: Optional[Mapping[str, str]] = None) -> bool:
    """Today-only attach flag. Does not change the normal 50/50 production gate."""
    return operational_validation_mode(environ=environ) and _flag(
        ENV_OPVAL_DEGRADED_UNIVERSE, environ=environ
    )


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
    if aid == C7_ID:
        return "OPVAL_CANDIDATE7_DIRECT_SELECTOR_FORBIDDEN"
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
    if str(manifest.get("candidate_id") or "") == C7_ID:
        return "OPVAL_CANDIDATE7_DIRECT_SELECTOR_FORBIDDEN"

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


def _canon_set(raw: Sequence[Any] | None) -> set[str]:
    from small_paper.v1r_live_dual_lane import canonical_symbol_key

    out: set[str] = set()
    for item in raw or []:
        key = canonical_symbol_key(item)
        if key:
            out.add(key)
    return out


def resolve_opval_degraded_probe_symbol(
    native_root: Path,
    trading_date: str,
    *,
    frozen_symbols: Optional[Sequence[Any]] = None,
    active_symbols: Optional[Sequence[Any]] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Readonly board probe for today's degraded OPVAL only.

    Does not PUT /register. Does not require Kabu GET /register exact50.
    Does not treat 4001019 as registered=0. Picks a remaining frozen member
    excluding terminal-invalid 4449 (PARTIAL_UNCONFIRMED vs exact50 SoT).
    """
    from small_paper.day_fixed_am_registration import load_frozen_am_universe
    from small_paper.kabu_registration_authority import (
        PREFERRED_FROZEN_PROBE_BARE,
        board_symbol_key,
    )

    env = dict(environ) if environ is not None else dict(os.environ)
    out: dict[str, Any] = {
        "ok": False,
        "reason": "",
        "symbol_key": "",
        "kabu_probe_symbol": "",
        "kabu_probe_symbol_registered": False,
        "kabu_probe_symbol_frozen_member": False,
        "probe_source": "OPVAL_DEGRADED_REMAINING_49",
        "owned": True,
        "exact50": False,
        "partial_unconfirmed": True,
        "registration_mutation": 0,
        "submit_cancel_live": "0/0/0",
        "allow_9984_fallback": False,
    }
    if not opval_degraded_universe_mode(environ=env):
        out["reason"] = "OPVAL_DEGRADED_UNIVERSE_FLAG_REQUIRED"
        return out
    if frozen_symbols is None:
        frozen = load_frozen_am_universe(Path(native_root), str(trading_date))
        frozen_set = _canon_set(frozen.get("canonical_symbols"))
        if not frozen.get("ok"):
            out["reason"] = str(frozen.get("reason") or "frozen_not_ok")
            return out
    else:
        frozen_set = _canon_set(frozen_symbols)
    if len(frozen_set) != EXPECTED_FROZEN_N or OPVAL_TERMINAL_INVALID_TODAY not in frozen_set:
        out["reason"] = f"frozen_not_degraded_contract:n={len(frozen_set)}"
        return out
    remaining = frozen_set - {OPVAL_TERMINAL_INVALID_TODAY}
    if active_symbols is not None:
        active = _canon_set(active_symbols) & remaining
    else:
        desired = _read_json_file(Path(native_root) / "runtime" / "ingress_desired_universe.json")
        desired_set = _canon_set(desired.get("symbols"))
        if (
            str(desired.get("trading_date") or "") == str(trading_date)
            and desired_set
            and OPVAL_TERMINAL_INVALID_TODAY not in desired_set
        ):
            active = desired_set & remaining
        else:
            active = set(remaining)
    if len(active) != EXPECTED_DEGRADED_ACTIVE_N and active_symbols is None:
        # Desired file may already be the remaining 49; if intersection is empty, fail.
        if not active:
            out["reason"] = "no_remaining_probe_symbol"
            return out
    if not active:
        out["reason"] = "no_remaining_probe_symbol"
        return out
    pick = (
        PREFERRED_FROZEN_PROBE_BARE
        if PREFERRED_FROZEN_PROBE_BARE in active
        else sorted(active)[0]
    )
    if pick == OPVAL_TERMINAL_INVALID_TODAY:
        out["reason"] = "probe_picked_terminal_invalid"
        return out
    key = board_symbol_key(pick)
    out.update(
        {
            "ok": True,
            "reason": "",
            "symbol_key": key,
            "kabu_probe_symbol": key,
            "kabu_probe_symbol_frozen_member": True,
            "pick": pick,
        }
    )
    return out


def select_runtime_board_probe_symbol(
    native_root: Path,
    trading_date: str,
    *,
    actual_symbols: Optional[Sequence[Any]] = None,
    proposed_symbol: Optional[str] = None,
    push: Any = None,
    environ: Optional[Mapping[str, str]] = None,
    write_audit: bool = True,
) -> dict[str, Any]:
    """OPVAL degraded remaining-49 probe; otherwise Formal exact50 Kabu probe."""
    env = dict(environ) if environ is not None else dict(os.environ)
    if opval_degraded_universe_mode(environ=env):
        return resolve_opval_degraded_probe_symbol(
            Path(native_root),
            str(trading_date),
            environ=env,
        )
    from small_paper.kabu_registration_authority import resolve_registered_probe_symbol

    return resolve_registered_probe_symbol(
        Path(native_root),
        str(trading_date),
        actual_symbols=actual_symbols,
        proposed_symbol=proposed_symbol,
        push=push,
        environ=environ,
        write_audit=write_audit,
    )


def collect_live_push_symbols(native_root: Path, trading_date: str, *, expected_pid: int = 0) -> set[str]:
    """Unique bare symbols from current Capture session PUSH files."""
    day = Path(native_root) / "data" / "market_capture" / str(trading_date)
    found: set[str] = set()
    sessions = sorted(day.glob("session_ing_*"))
    if expected_pid > 0:
        sessions = [p for p in sessions if f"_{int(expected_pid)}_" in p.name] or sessions
    for session in sessions:
        if not session.is_dir():
            continue
        for part in reversed(sorted(session.glob("push_part_*.jsonl"))):
            try:
                text = part.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in _PUSH_SYMBOL_RE.finditer(text):
                found |= _canon_set([match.group(1)])
            if (
                OPVAL_TERMINAL_INVALID_TODAY not in found
                and len(found) >= EXPECTED_DEGRADED_ACTIVE_N
            ):
                return found
            if len(found) >= EXPECTED_FROZEN_N:
                return found
    return found


def _count_live_ingress(*, native_root: Path, trading_date: str) -> int:
    try:
        from small_paper.v1r_pbv2_duplicate_runtime import list_live_ingress

        live = list_live_ingress(trading_date=str(trading_date), native_root=Path(native_root))
        return len(list(live or []))
    except Exception:
        return -1


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def prior_4449_symbol_not_found_evidence(native_root: Path, trading_date: str) -> dict[str, Any]:
    path = (
        Path(native_root)
        / "results"
        / "research"
        / "v26g6_opval_launcher"
        / f"opval_{trading_date}_step07_rca.json"
    )
    body = _read_json_file(path)
    probe = ((body.get("first_causal_error") or {}).get("board_probe") or {}).get("4449@1") or {}
    code_raw = probe.get("Code", probe.get("code"))
    code = str(code_raw).strip() if code_raw is not None else ""
    message = str(probe.get("Message") or probe.get("message") or "")
    day_ok = str(body.get("trading_date") or trading_date) == str(trading_date)
    if (not code or not body) and path.is_file():
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            raw = ""
        if OPVAL_KABU_SYMBOL_NOT_FOUND in raw and "4449" in raw and str(trading_date) in raw:
            code = OPVAL_KABU_SYMBOL_NOT_FOUND
            if "銘柄が見つからない" in raw or "SYMBOL NOT FOUND" in raw.upper():
                message = "銘柄が見つからない"
            day_ok = True
    ok = code == OPVAL_KABU_SYMBOL_NOT_FOUND and day_ok
    return {
        "ok": ok,
        "path": str(path),
        "code": code,
        "message": message,
        "source": "session_rca_board_probe",
    }


def probe_terminal_invalid_4002001(
    *,
    symbol: str = OPVAL_TERMINAL_INVALID_TODAY,
    native_root: Path,
    trading_date: str,
    allow_live_board: bool = False,
) -> dict[str, Any]:
    """Prove SYMBOL NOT FOUND without issuing a token.

    Skip live /board when Capture is already RATE_LIMIT circuit-open.
    """
    prior = prior_4449_symbol_not_found_evidence(native_root, trading_date)
    out: dict[str, Any] = {
        "ok": False,
        "symbol": str(symbol),
        "code": "",
        "message": "",
        "live_board": False,
        "issued_token": False,
    }
    if not allow_live_board:
        out.update(prior)
        out["ok"] = bool(prior.get("ok"))
        out["live_board"] = False
        return out
    try:
        from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url
        from small_paper.kabu_token_authority import read_shared_token

        token = read_shared_token(native_root, trading_date)
        if not token:
            out["reason"] = "shared_token_missing"
            if prior.get("ok"):
                out.update(prior)
                out["ok"] = True
            return out
        client = KabuNativeRestClient(default_base_url(), timeout=5.0, max_retries=1)
        try:
            client.get_board(f"{symbol}@1", token=token)
            out["reason"] = "symbol_unexpectedly_found"
            return out
        except KabuNativeApiError as exc:
            code = str(getattr(exc, "kabu_code", "") or "")
            out["code"] = code
            out["message"] = str(exc)
            out["live_board"] = True
            if code == OPVAL_KABU_SYMBOL_NOT_FOUND or OPVAL_KABU_SYMBOL_NOT_FOUND in str(exc):
                out["ok"] = True
                out["code"] = OPVAL_KABU_SYMBOL_NOT_FOUND
                return out
            if prior.get("ok") and code in {"4001006", "4001007"}:
                out.update({**prior, "ok": True, "live_board": True, "fallback": code})
                return out
            out["reason"] = f"board_error:{code or type(exc).__name__}"
            return out
    except Exception as exc:
        out["reason"] = f"probe_exception:{type(exc).__name__}"
        if prior.get("ok"):
            out.update({**prior, "ok": True, "probe_exception": type(exc).__name__})
        return out


def reuse_matching_frozen_desired_universe(native_root: Path, trading_date: str) -> dict[str, Any]:
    """OPVAL degraded: do not rewrite desired/generation when frozen 50 already bound."""
    from small_paper.day_fixed_am_registration import (
        canonical_membership_sha,
        load_frozen_am_universe,
    )
    from small_paper.ingress_control_channel import read_desired_universe

    day = str(trading_date)
    frozen = load_frozen_am_universe(Path(native_root), day)
    frozen_syms = list(frozen.get("canonical_symbols") or [])
    if not (frozen.get("ok") and len(frozen_syms) == EXPECTED_FROZEN_N):
        return {"ok": False, "reused": False, "reason": "frozen_not_exact50"}
    req = read_desired_universe(Path(native_root), requested_trading_date=day) or {}
    if req.get("rejected"):
        return {"ok": False, "reused": False, "reason": str(req.get("reason") or "desired_rejected")}
    des = list(req.get("symbols") or [])
    if not des:
        return {"ok": False, "reused": False, "reason": "desired_missing"}
    frozen_sha = str(frozen.get("canonical_membership_sha") or canonical_membership_sha(frozen_syms))
    if canonical_membership_sha(des) != frozen_sha:
        return {"ok": False, "reused": False, "reason": "desired_not_frozen"}
    return {
        "ok": True,
        "reused": True,
        "rejected": False,
        "reason": "OPVAL_REUSE_EXISTING_FROZEN_DESIRED",
        "trading_date": day,
        "symbols": frozen_syms,
        "symbol_count": len(frozen_syms),
        "generation": int(req.get("generation") or 0),
        "allow_put": False,
        "paper_register": "DISABLED",
        "owner": "MARKET_INGRESS_SERVICE",
        "mode": OPVAL_DEGRADED_MODE,
    }


def persist_opval_degraded_evidence(
    native_root: Path,
    trading_date: str,
    payload: Mapping[str, Any],
) -> Path:
    dest_dir = Path(native_root) / "results" / "research" / "v26g6_opval_launcher"
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"opval_degraded_universe_{trading_date}.json"
    body = {
        "schema": "OPVAL_DEGRADED_UNIVERSE_EVIDENCE_V1",
        "trading_date": str(trading_date),
        "mode": OPVAL_DEGRADED_MODE,
        "terminal_invalid": list(payload.get("terminal_invalid") or [OPVAL_TERMINAL_INVALID_TODAY]),
        "active_universe_count": int(payload.get("active_universe_count") or EXPECTED_DEGRADED_ACTIVE_N),
        "frozen_universe_count": EXPECTED_FROZEN_N,
        "frozen_rewritten": False,
        "permanent_universe_policy": False,
        "INVALID_FOR_STRATEGY_EVALUATION": True,
        "NOT_PROSPECTIVE_DAY1": True,
        "classification": DEGRADED_OPVAL_READY if payload.get("ready") else str(payload.get("reason") or ""),
        **dict(payload),
        "written_at": datetime.now(JST).isoformat(timespec="milliseconds"),
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def evaluate_opval_degraded_universe_ready(
    *,
    native_root: Path,
    trading_date: str,
    expected_capture_pid: int,
    frozen_symbols: Optional[Sequence[Any]] = None,
    live_symbols: Optional[Sequence[Any]] = None,
    status: Optional[Mapping[str, Any]] = None,
    token_audit: Optional[Mapping[str, Any]] = None,
    terminal_probe: Optional[Mapping[str, Any]] = None,
    retry_before: Optional[int] = None,
    retry_after: Optional[int] = None,
    retry_sample_sec: float = 8.0,
    ingress_process_count: Optional[int] = None,
    capture_seq_before: Optional[int] = None,
    capture_seq_after: Optional[int] = None,
    environ: Optional[Mapping[str, str]] = None,
    allow_live_board: Optional[bool] = None,
    pid_alive: Optional[bool] = None,
) -> dict[str, Any]:
    """Today-only degraded GO. Never returns PAPER_READY. Never mutates frozen 50."""
    from small_paper.capture_child_cleanup import query_process
    from small_paper.day_fixed_am_registration import load_frozen_am_universe
    from small_paper.kabu_token_authority import audit_snapshot

    env = dict(environ) if environ is not None else dict(os.environ)
    day = str(trading_date)
    out: dict[str, Any] = {
        "ok": False,
        "ready": False,
        "classification": "",
        "reason": "",
        "mode": OPVAL_DEGRADED_MODE,
        "trading_date": day,
        "terminal_invalid": [],
        "active_symbols": [],
        "active_universe_count": 0,
        "frozen_count": 0,
        "existing_ingress_reused": True,
        "second_ingress_spawned": False,
        "second_token_issuer": False,
        "retry_storm_active": False,
        "paper_ready_forbidden": True,
        "submit_cancel_live": "0/0/0",
        "INVALID_FOR_STRATEGY_EVALUATION": True,
        "NOT_PROSPECTIVE_DAY1": True,
        "OPERATIONAL_VALIDATION_ONLY": True,
        "DEGRADED_UNIVERSE_49_OF_50": True,
    }
    if not opval_degraded_universe_mode(environ=env):
        out["reason"] = "OPVAL_DEGRADED_UNIVERSE_FLAG_REQUIRED"
        return out
    if day != opval_clock_day(environ=env) and env.get("TRADEBOT_ALLOW_DEGRADED_CLOCK_MISMATCH") != "1":
        clock = opval_clock_day(environ=env)
        if day != clock:
            out["reason"] = f"OPVAL_TRADING_DATE_MISMATCH:resolved={day} clock={clock}"
            return out
    if expected_capture_pid <= 0:
        out["reason"] = "OPVAL_EXPECTED_CAPTURE_PID_REQUIRED"
        return out
    alive = bool(pid_alive) if pid_alive is not None else bool(
        query_process(int(expected_capture_pid)).get("exists")
    )
    if not alive:
        out["reason"] = f"CAPTURE_PID_NOT_ALIVE:{expected_capture_pid}"
        return out

    st = dict(status) if status is not None else _read_json_file(
        Path(native_root) / "data" / "market_capture" / day / "ingress_status.json"
    )
    status_pid = int(st.get("pid") or 0)
    if status_pid != int(expected_capture_pid):
        out["reason"] = f"status_pid_mismatch:status={status_pid} expected={expected_capture_pid}"
        return out

    frozen = load_frozen_am_universe(Path(native_root), day) if frozen_symbols is None else {
        "ok": True,
        "canonical_symbols": list(frozen_symbols),
        "canonical_membership_sha": "",
    }
    frozen_set = _canon_set(frozen_symbols if frozen_symbols is not None else frozen.get("canonical_symbols"))
    out["frozen_count"] = len(frozen_set)
    out["frozen_membership_sha"] = str(frozen.get("canonical_membership_sha") or "")
    if not frozen.get("ok") or len(frozen_set) != EXPECTED_FROZEN_N:
        out["reason"] = f"frozen_not_exact50:n={len(frozen_set)}"
        return out
    if OPVAL_TERMINAL_INVALID_TODAY not in frozen_set:
        out["reason"] = "frozen_missing_expected_terminal_invalid_4449"
        return out

    if live_symbols is None:
        live_set = collect_live_push_symbols(Path(native_root), day, expected_pid=int(expected_capture_pid))
    else:
        live_set = _canon_set(live_symbols)
    missing = sorted(frozen_set - live_set)
    extra = sorted(live_set - frozen_set)
    out["missing"] = missing
    out["extra"] = extra
    out["active_symbols"] = sorted(live_set)
    out["active_universe_count"] = len(live_set)
    if extra:
        out["reason"] = f"unexpected_live_symbols:{extra[:8]}"
        return out
    if missing != [OPVAL_TERMINAL_INVALID_TODAY]:
        out["reason"] = f"terminal_invalid_not_exactly_4449:missing={missing}"
        return out
    if len(live_set) != EXPECTED_DEGRADED_ACTIVE_N:
        out["reason"] = f"active_not_49:n={len(live_set)}"
        return out
    out["terminal_invalid"] = [OPVAL_TERMINAL_INVALID_TODAY]

    circuit = str(st.get("circuit_reason") or "")
    if allow_live_board is None:
        allow_live_board = False
    probe = dict(terminal_probe) if terminal_probe is not None else probe_terminal_invalid_4002001(
        symbol=OPVAL_TERMINAL_INVALID_TODAY,
        native_root=Path(native_root),
        trading_date=day,
        allow_live_board=bool(allow_live_board),
    )
    out["terminal_probe"] = {
        "ok": bool(probe.get("ok")),
        "code": str(probe.get("code") or ""),
        "message": str(probe.get("message") or "")[:200],
        "live_board": bool(probe.get("live_board")),
        "source": str(probe.get("source") or ""),
        "reason": str(probe.get("reason") or ""),
    }
    if not probe.get("ok"):
        out["reason"] = f"4449_not_proven_4002001:{probe.get('reason') or probe.get('code')}"
        return out

    auth_n = int(st.get("auth_failure_count") or 0)
    if auth_n > 0:
        out["reason"] = f"persistent_auth_failure:count={auth_n}"
        return out
    last_err = str(st.get("last_error") or st.get("auth_failure_code") or "")
    if "4001007" in last_err:
        out["reason"] = "persistent_4001007"
        return out

    rb = int(retry_before if retry_before is not None else st.get("registration_retry_count") or 0)
    if retry_after is None and retry_before is None:
        time.sleep(max(0.0, float(retry_sample_sec)))
        st2 = _read_json_file(Path(native_root) / "data" / "market_capture" / day / "ingress_status.json")
        ra = int(st2.get("registration_retry_count") or 0)
        seq_b = int(st.get("raw_last_sequence") or 0)
        seq_a = int(st2.get("raw_last_sequence") or 0)
        st = st2
    else:
        ra = int(retry_after if retry_after is not None else rb)
        seq_b = int(capture_seq_before if capture_seq_before is not None else st.get("raw_last_sequence") or 0)
        seq_a = int(capture_seq_after if capture_seq_after is not None else seq_b)
    retry_delta = ra - rb
    out["registration_retry_before"] = rb
    out["registration_retry_after"] = ra
    out["registration_retry_delta"] = retry_delta
    out["capture_seq_before"] = seq_b
    out["capture_seq_after"] = seq_a
    storm = retry_delta > 1
    out["retry_storm_active"] = storm
    if storm:
        out["reason"] = f"OPVAL_RETRY_STORM_ACTIVE:delta={retry_delta} over {retry_sample_sec}s"
        return out
    if seq_a <= seq_b:
        out["reason"] = "CAPTURE_PUSH_NOT_INCREASING"
        return out

    n_ing = int(ingress_process_count) if ingress_process_count is not None else _count_live_ingress(
        native_root=Path(native_root), trading_date=day
    )
    out["ingress_process_count"] = n_ing
    if n_ing != 1:
        out["second_ingress_spawned"] = n_ing > 1
        out["reason"] = f"ingress_process_count_not_1:n={n_ing}"
        return out

    audit = dict(token_audit) if token_audit is not None else audit_snapshot(
        native_root=Path(native_root), trading_date=day
    )
    day_auth = _read_json_file(
        Path(native_root) / "data" / "market_capture" / day / "kabu_token_authority.json"
    )
    if token_audit is not None:
        issue_n = int(audit.get("token_issue_count") or 0)
        unexpected = int(audit.get("unexpected_token_issue_count") or 0)
        blocked_second = int(audit.get("blocked_second_issuer_count") or 0)
        owner = str(audit.get("token_issue_owner") or audit.get("owner") or "")
    else:
        # Day-session authority is SoT. Station lifetime counters are not a second issuer.
        issue_n = int(day_auth.get("token_issue_count") or 0)
        unexpected = int(day_auth.get("unexpected_token_issue_count") or 0)
        blocked_second = int(day_auth.get("blocked_second_issuer_count") or 0)
        owner = str(day_auth.get("token_issue_owner") or day_auth.get("owner") or "")
    out["token_issue_count"] = issue_n
    out["token_issue_owner"] = owner
    out["unexpected_token_issue_count"] = unexpected
    out["blocked_second_issuer_count"] = blocked_second
    second = unexpected > 0 or blocked_second > 0 or (
        owner not in ("", "MARKET_INGRESS_SERVICE")
    ) or (token_audit is not None and issue_n != 1)
    if token_audit is None:
        second = unexpected > 0 or blocked_second > 0 or issue_n != 1 or owner not in (
            "",
            "MARKET_INGRESS_SERVICE",
        )
    out["second_token_issuer"] = bool(second)
    if second:
        out["reason"] = f"token_issuer_not_exactly_one:issue={issue_n} unexpected={unexpected} owner={owner}"
        return out

    out["ok"] = True
    out["ready"] = True
    out["classification"] = DEGRADED_OPVAL_READY
    out["reason"] = DEGRADED_OPVAL_READY
    out["capture_pid"] = int(expected_capture_pid)
    out["circuit_reason"] = str(st.get("circuit_reason") or "")
    out["entry_block_reason"] = str(st.get("entry_block_reason") or "")
    out["state"] = str(st.get("state") or "")
    return out

