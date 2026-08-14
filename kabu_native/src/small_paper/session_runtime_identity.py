"""Current-run session / trading-date identity (V23).

Runtime trading date is never datetime.now(JST) as a silent last fallback.
Post-session and collectors join artifacts by stamped current-run identity.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.derived_artifact_contract import ENV_RUNTIME_RUN_ID, ensure_runtime_run_id
from small_paper.ingress_run_identity import (
    ENV_ACTIVATION_ID,
    ENV_ACTIVATION_SHA,
    ENV_BUS_IDENTITY,
    ENV_CERTIFICATION_RUN_ID,
    ENV_INGRESS_RUN_ID,
    ENV_LAUNCH_NONCE,
    ENV_STAGE_RUN_ID,
    activation_identity,
)
from small_paper.runtime_clock import (
    ENV_V0,
    certification_mode,
    market_input_mode,
    now_jst,
    session_clock_enabled,
)

JST = ZoneInfo("Asia/Tokyo")

ENV_TRADING_DATE = "TRADEBOT_TRADING_DATE"
ENV_SESSION_TRADING_DATE = "TRADEBOT_SESSION_TRADING_DATE"
ENV_DAILY_RUN_ID = "TRADEBOT_DAILY_RUN_ID"
ENV_SESSION_ID = "TRADEBOT_SESSION_ID"
ENV_SESSION_KIND = "TRADEBOT_SESSION_KIND"

RUNTIME_TRADING_DATE_NOT_PROVEN = "RUNTIME_TRADING_DATE_NOT_PROVEN"

SESSION_IDENTITY_KEYS: tuple[str, ...] = (
    "certification_run_id",
    "stage_run_id",
    "activation_id",
    "activation_sha",
    "runtime_commit",
    "runtime_run_id",
    "daily_run_id",
    "session_id",
    "session_kind",
    "trading_date",
    "market_input_mode",
    "ingress_run_id",
    "launch_nonce",
)


class RuntimeTradingDateNotProven(RuntimeError):
    """Fail-closed: no proven runtime trading date (no wall-clock silent fallback)."""

    def __init__(self, reason: str = RUNTIME_TRADING_DATE_NOT_PROVEN) -> None:
        super().__init__(reason)
        self.reason = reason


def _valid_yyyymmdd(raw: str) -> bool:
    s = str(raw or "").strip().replace("-", "")
    if len(s) != 8 or not s.isdigit():
        return False
    try:
        datetime.strptime(s, "%Y%m%d")
        return True
    except ValueError:
        return False


def _norm_day(raw: str) -> str:
    return str(raw or "").strip().replace("-", "")[:8]


def resolve_runtime_trading_date(
    explicit: Optional[str] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve Runtime trading date. Never falls back to datetime.now(JST).

    Priority:
      1. explicit runtime trading_date
      2. daily/session context env (TRADEBOT_TRADING_DATE)
      3. RuntimeClock / session-clock V0 day_stamp (now_jst when clock enabled)
      4. production (non-cert): RuntimeClock now_jst()
      5. else fail-closed RUNTIME_TRADING_DATE_NOT_PROVEN
    """
    env = environ if environ is not None else os.environ
    cand = _norm_day(str(explicit or ""))
    if _valid_yyyymmdd(cand):
        return cand
    # Session clock (virtual session day) outranks leftover wall TRADEBOT_TRADING_DATE.
    if session_clock_enabled(environ=dict(env) if not isinstance(env, dict) else env):
        v0 = str(env.get(ENV_V0) or "").strip()
        if v0:
            try:
                dt = datetime.fromisoformat(v0)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=JST)
                return dt.astimezone(JST).strftime("%Y%m%d")
            except Exception:
                pass
        return now_jst(environ=dict(env) if not isinstance(env, dict) else env).strftime("%Y%m%d")
    env_day = _norm_day(str(env.get(ENV_TRADING_DATE) or env.get(ENV_SESSION_TRADING_DATE) or ""))
    if _valid_yyyymmdd(env_day):
        return env_day
    if not certification_mode(environ=dict(env) if not isinstance(env, dict) else env):
        return now_jst(environ=dict(env) if not isinstance(env, dict) else env).strftime("%Y%m%d")
    raise RuntimeTradingDateNotProven(RUNTIME_TRADING_DATE_NOT_PROVEN)


def _runtime_commit() -> str:
    try:
        from small_paper.v1r_activation_binding import load_activation_manifest, load_active_selector

        sel = load_active_selector()
        man = load_activation_manifest(selector=sel)
        return str(man.get("runtime_code_git_commit") or "").strip()
    except Exception:
        return ""


def session_identity_fields(
    *,
    session_id: str = "",
    session_kind: str = "",
    trading_date: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    env = environ if environ is not None else os.environ
    aid, ash = activation_identity(environ=env)
    try:
        day = resolve_runtime_trading_date(trading_date, environ=env)
    except RuntimeTradingDateNotProven:
        day = _norm_day(str(trading_date or ""))
    cert = str(env.get(ENV_CERTIFICATION_RUN_ID) or "").strip()
    stage = str(env.get(ENV_STAGE_RUN_ID) or "").strip()
    sid = str(session_id or env.get(ENV_SESSION_ID) or "").strip()
    kind = str(session_kind or env.get(ENV_SESSION_KIND) or "").strip()
    return {
        "certification_run_id": cert or None,
        "stage_run_id": stage or None,
        "activation_id": aid or None,
        "activation_sha": ash or None,
        "runtime_commit": _runtime_commit() or None,
        "runtime_run_id": (
            ensure_runtime_run_id(environ=env if isinstance(env, dict) else None)
            if isinstance(env, dict) or environ is None
            else str(env.get(ENV_RUNTIME_RUN_ID) or "").strip()
        )
        or str(env.get(ENV_RUNTIME_RUN_ID) or "").strip()
        or None,
        "daily_run_id": str(env.get(ENV_DAILY_RUN_ID) or "").strip() or None,
        "session_id": sid or None,
        "session_kind": kind or None,
        "trading_date": day or None,
        "market_input_mode": market_input_mode(environ=dict(env) if not isinstance(env, dict) else env) or None,
        "ingress_run_id": str(env.get(ENV_INGRESS_RUN_ID) or "").strip() or None,
        "launch_nonce": str(env.get(ENV_LAUNCH_NONCE) or "").strip() or None,
        "bus_identity": str(env.get(ENV_BUS_IDENTITY) or "").strip() or None,
    }


def stamp_session_identity(
    doc: dict[str, Any],
    *,
    session_id: str = "",
    session_kind: str = "",
    trading_date: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Stamp current-run identity. Does not overwrite non-empty existing values."""
    ident = session_identity_fields(
        session_id=session_id,
        session_kind=session_kind,
        trading_date=trading_date,
        environ=environ,
    )
    for key, val in ident.items():
        if val in (None, ""):
            if key not in doc:
                doc[key] = None
            continue
        existing = doc.get(key)
        if existing in (None, ""):
            doc[key] = val
    if session_id and not doc.get("session_id"):
        doc["session_id"] = session_id
    if session_kind and not doc.get("session_kind"):
        doc["session_kind"] = session_kind
    return doc


def write_session_identity_file(
    session_root: Path,
    *,
    session_id: str = "",
    session_kind: str = "",
    trading_date: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    root = Path(session_root)
    root.mkdir(parents=True, exist_ok=True)
    body = stamp_session_identity(
        {},
        session_id=session_id or root.name,
        session_kind=session_kind,
        trading_date=trading_date,
        environ=environ,
    )
    path = root / "session_identity.json"
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def expected_current_run_scope(
    *,
    trading_date: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    env = environ if environ is not None else os.environ
    ident = session_identity_fields(trading_date=trading_date, environ=env)
    out: dict[str, str] = {}
    rid = str(env.get(ENV_RUNTIME_RUN_ID) or "").strip()
    ident["runtime_run_id"] = rid or None
    for key in (
        "certification_run_id",
        "stage_run_id",
        "activation_sha",
        "runtime_run_id",
        "daily_run_id",
    ):
        val = ident.get(key)
        if val:
            out[key] = str(val)
    # trading_date is a current-run key only when session/env proved it.
    # Wall-clock orchestrator fallback must not exclude same-run fixtures
    # that were stamped without TRADEBOT_TRADING_DATE.
    env_day = _norm_day(str(env.get(ENV_TRADING_DATE) or env.get(ENV_SESSION_TRADING_DATE) or ""))
    if _valid_yyyymmdd(env_day) or session_clock_enabled(
        environ=dict(env) if not isinstance(env, dict) else env
    ):
        day = str(ident.get("trading_date") or "").strip()
        if _valid_yyyymmdd(day):
            out["trading_date"] = day
    return out


def current_run_identity_matches(doc: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    """True only when every non-empty expected identity field matches the artifact."""
    if not isinstance(doc, Mapping) or not expected:
        return False
    keys = [k for k, v in expected.items() if str(v or "").strip()]
    if not keys:
        return False
    for key in keys:
        if str(doc.get(key) or "").strip() != str(expected.get(key) or "").strip():
            return False
    return True


def load_session_identity_doc(session_root: Path) -> dict[str, Any]:
    root = Path(session_root)
    merged: dict[str, Any] = {}
    for name in (
        "session_identity.json",
        "small_paper_summary.json",
        "session_seal.json",
        "consumer_session_metrics.json",
    ):
        path = root / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            for key in SESSION_IDENTITY_KEYS:
                if merged.get(key) in (None, "") and data.get(key) not in (None, ""):
                    merged[key] = data.get(key)
    hb = root / "heartbeat.jsonl"
    if hb.is_file() and not merged.get("runtime_run_id"):
        try:
            line = hb.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
            if line:
                row = json.loads(line[0])
                if isinstance(row, dict):
                    for key in SESSION_IDENTITY_KEYS:
                        if merged.get(key) in (None, "") and row.get(key) not in (None, ""):
                            merged[key] = row.get(key)
        except Exception:
            pass
    return merged


def session_matches_current_run(session_root: Path, expected: Mapping[str, Any]) -> bool:
    return current_run_identity_matches(load_session_identity_doc(session_root), expected)


def session_root_from_snapshot(snapshot_path: Path) -> Path:
    snap = Path(snapshot_path)
    safety = snap.parent
    if safety.name == "live_order_safety":
        return safety.parent
    return safety


def iter_current_run_soak_snapshots(
    results_root: Path,
    *,
    expected: Mapping[str, Any],
) -> list[Path]:
    """Evaluate only snapshots bound to the current run. Historical rglob hits are audit-only."""
    root = Path(results_root)
    if not root.is_dir() or not expected:
        return []
    found = sorted(root.rglob("soak_session_snapshot.json"), key=lambda p: p.stat().st_mtime)
    out: list[Path] = []
    for path in found:
        if session_matches_current_run(session_root_from_snapshot(path), expected):
            out.append(path)
    return out
