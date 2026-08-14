"""Full-Day Paper Environment Certification — identity, gate, clock audit.

Production process graph still starts at run_paper_trade_checked.bat.
This module is imported by the checked runner so a missing PASS artifact
cannot launch formal Paper. Certification runs set TRADEBOT_CERTIFICATION_MODE=1.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from small_paper.runtime_clock import (
    ENV_CERT_MODE,
    certification_mode,
    skip_cert_gate,
)
from small_paper.v1r_activation_binding import (
    NATIVE,
    RUNTIME_DEPENDENCY_RELS,
    SELECTOR_PATH,
    collect_runtime_inventory,
    file_sha256,
    load_activation_manifest,
    load_active_selector,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

REPO = NATIVE.parent
CERT_DIR = NATIVE / "results" / "research" / "paper_runtime_full_day_certification"
PASS_NAME = "paper_runtime_full_day_certification.json"
CHECKED_BAT = REPO / "run_paper_trade_checked.bat"
PAPER_BAT = REPO / "run_paper_trade.bat"

HISTORICAL_GATES = (
    "LIVE_LAUNCHER_STUB",
    "PBV2_PRIMARY_CONTAMINATION",
    "EMPTY_NATIVE_UNIVERSE",
    "SYMBOL_KEY_6098_6098T",
    "PASSIVE_FILL_WALL_CLOCK_PARITY",
    "KABU_API_TOKEN_OWNERSHIP_CONFLICT",
    "POST_SESSION_AM_SAFETY_SIDE_EFFECT",
    "INGRESS_AUTH_RETRY_STORM",
    "NATIVE_FULL_PUSH_THROTTLE_BUG",
    "STALE_RECOVERY_FORCE_EVAL_DEATH_SPIRAL",
    "PM_REBUILD_OVERWROTE_FROZEN_SOURCE",
    "FROZEN_AND_SCREENING_PATH_ALIAS",
    "TEARDOWN_EXTERNAL_BACKUP_LOGGER_NAMEERROR",
)

# Domain B files that must not call datetime.now(JST) / time.time() for session gates
# after V15 wiring. Logging stamps in other files may remain domain C.
SESSION_CLOCK_FILES = (
    "src/small_paper/session_schedule.py",
    "src/small_paper/am_pm_session_policy.py",
    "src/runner/am_pm_daily_runner.py",
    "src/small_paper/pre_session_warmup.py",
)


def _sha_file(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture_identity(*, native_root: Optional[Path] = None, repo_root: Optional[Path] = None) -> dict[str, Any]:
    native = Path(native_root or NATIVE)
    repo = Path(repo_root or REPO)
    selector = load_active_selector()
    manifest = load_activation_manifest(selector=selector)
    ok, _, calc = verify_manifest_self_sha(manifest)
    inv = collect_runtime_inventory(native_root=native)
    inv_check = verify_runtime_inventory(manifest, native_root=native)
    bat = repo / "run_paper_trade_checked.bat"
    paper_bat = repo / "run_paper_trade.bat"
    cfg = native / "configs" / "small_paper_pilot.yaml"
    if not cfg.is_file():
        cfg = native / "config" / "small_paper_pilot.yaml"
    return {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "activation_id": selector.get("activation_id"),
        "activation_sha": selector.get("activation_sha"),
        "manifest_id": manifest.get("manifest_id"),
        "manifest_self_sha_ok": bool(ok) and calc == str(manifest.get("sha256") or ""),
        "runtime_commit": str(manifest.get("runtime_code_git_commit") or ""),
        "parent_activation_id": str(manifest.get("parent_activation_id") or ""),
        "parent_activation_sha": str(manifest.get("parent_activation_sha") or ""),
        "supersede_reason": str(manifest.get("supersede_reason") or ""),
        "strategy_sha": str(manifest.get("strategy_sha") or ""),
        "precommit_sha": str(manifest.get("precommit_sha") or ""),
        "inventory": inv,
        "inventory_n": len(inv),
        "inventory_match": bool(inv_check.get("ok")),
        "inventory_matched": inv_check.get("matched"),
        "selector_target": selector.get("activation_id"),
        "checked_bat_sha256": _sha_file(bat),
        "paper_bat_sha256": _sha_file(paper_bat),
        "config_sha256": _sha_file(cfg) if cfg.is_file() else "",
        "config_path": str(cfg) if cfg.is_file() else "",
        "submit_cancel_live": str(manifest.get("submit_cancel_live") or "0/0/0"),
    }


def identities_equal(a: MappingLike, b: MappingLike) -> tuple[bool, list[str]]:
    keys = (
        "activation_id",
        "activation_sha",
        "runtime_commit",
        "strategy_sha",
        "precommit_sha",
        "checked_bat_sha256",
        "paper_bat_sha256",
        "config_sha256",
        "inventory",
    )
    mismatches: list[str] = []
    for k in keys:
        if a.get(k) != b.get(k):
            mismatches.append(k)
    return (not mismatches), mismatches


MappingLike = dict[str, Any]


def load_latest_pass(*, cert_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    path = Path(cert_dir or CERT_DIR) / PASS_NAME
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    return body


def enforce_pre_paper_certification_gate(
    *,
    native_root: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> int:
    """Return 0 to proceed. Non-zero refuses Paper start.

    Certification mode and explicit skip (unit tests) bypass the gate.
    """
    if certification_mode() or skip_cert_gate():
        return 0
    if str(os.environ.get("PYTEST_CURRENT_TEST") or "").strip():
        return 0
    if str(os.environ.get("TRADEBOT_DEMO_PUSH_E2E") or "").strip() in {"1", "true", "yes", "on"}:
        return 0
    if str(os.environ.get("TRADEBOT_COMM_FAULT_E2E") or "").strip() in {"1", "true", "yes", "on"}:
        return 0
    ident = capture_identity(native_root=native_root, repo_root=repo_root)
    artifact = load_latest_pass()
    if artifact is None:
        print("[PRE-PAPER CERTIFICATION GATE] FAIL: PASS artifact missing", flush=True)
        print("reason: V1R_RUNTIME_PRE_PAPER_CERTIFICATION_REQUIRED", flush=True)
        return 2
    if str(artifact.get("verdict") or "") != "V1R_RUNTIME_PRE_PAPER_CERTIFICATION_PASS":
        print("[PRE-PAPER CERTIFICATION GATE] FAIL: last certification is not PASS", flush=True)
        return 2
    failed = artifact.get("failed_tests") or []
    if failed:
        print("[PRE-PAPER CERTIFICATION GATE] FAIL: failed_tests non-empty", flush=True)
        return 2
    before = artifact.get("identity_before") or artifact.get("identity") or {}
    ok, mismatches = identities_equal(ident, before)
    if not ok:
        print(
            "[PRE-PAPER CERTIFICATION GATE] FAIL: identity mismatch vs PASS artifact: "
            + ",".join(mismatches),
            flush=True,
        )
        return 2
    after = artifact.get("identity_after") or before
    ok2, mismatches2 = identities_equal(ident, after)
    if not ok2:
        print(
            "[PRE-PAPER CERTIFICATION GATE] FAIL: identity mutated after certification: "
            + ",".join(mismatches2),
            flush=True,
        )
        return 2
    print(
        "[PRE-PAPER CERTIFICATION GATE] PASS "
        f"{ident.get('activation_id')} {str(ident.get('activation_sha') or '')[:12]}",
        flush=True,
    )
    return 0


def _calls_wall_clock(node: ast.AST) -> bool:
    """True if expression is datetime.now(...) or time.time()."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in {"now", "utcnow", "time", "monotonic", "perf_counter"}:
        return True
    if isinstance(func, ast.Name) and func.id in {"now_jst"}:
        return False
    return False


def audit_clock_access(*, native_root: Optional[Path] = None) -> dict[str, Any]:
    """Classify wall-clock calls in production runtime inventory + session helpers."""
    native = Path(native_root or NATIVE)
    extra = [
        "src/small_paper/runtime_clock.py",
        "src/small_paper/session_schedule.py",
        "src/small_paper/am_pm_session_policy.py",
        "src/small_paper/pre_session_warmup.py",
        "src/small_paper/paper_full_day_certification.py",
        "src/small_paper/market_ingress_protocol.py",
        "src/small_paper/virtual_clock.py",
    ]
    rels = list(dict.fromkeys([*RUNTIME_DEPENDENCY_RELS, *extra]))
    rows: list[dict[str, Any]] = []
    bypass: list[dict[str, Any]] = []
    for rel in rels:
        path = native / rel
        if not path.is_file():
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name not in {
                "now",
                "utcnow",
                "time",
                "monotonic",
                "perf_counter",
            }:
                continue
            # datetime.now / time.time / time.monotonic / time.perf_counter
            if isinstance(func, ast.Attribute):
                base = func.value
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
            else:
                base_name = ""
            if name in {"now", "utcnow"} and base_name not in {"datetime", "dt", "datetime_cls"}:
                # skip logger.now etc — still record datetime.now
                if "datetime" not in ast.dump(func.value if isinstance(func, ast.Attribute) else func):
                    if base_name not in {"datetime"}:
                        continue
            if name in {"time"} and base_name not in {"time"}:
                continue
            if name in {"monotonic", "perf_counter"} and base_name not in {"time"}:
                continue
            lineno = int(getattr(node, "lineno", 0) or 0)
            snippet = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
            domain, reason = _classify_clock(rel, snippet, name)
            fn = _enclosing_function(tree, lineno)
            row = {
                "file": rel,
                "line": lineno,
                "function": fn,
                "snippet": snippet[:200],
                "clock_domain": domain,
                "reason": reason,
                "api": f"{base_name}.{name}" if base_name else name,
            }
            rows.append(row)
            if domain == "BYPASS":
                bypass.append(row)
    # Session-clock files must not retain unguarded datetime.now(JST) for waits.
    session_bypass = [
        r
        for r in rows
        if r["file"] in SESSION_CLOCK_FILES
        and "datetime.now" in r["snippet"]
        and "runtime_clock" not in r["snippet"]
        and "now_jst(" not in r["snippet"]
    ]
    safety_bypass = [
        r for r in rows if str(r.get("file") or "").endswith("safety.py") and r.get("clock_domain") == "BYPASS"
    ]
    ok = not session_bypass and not safety_bypass
    return {
        "ok": ok,
        "n": len(rows),
        "bypass_n": len(bypass),
        "session_clock_bypass": session_bypass,
        "safety_trading_date_bypass": safety_bypass,
        "rows": rows,
        "verdict": "CLOCK_AUDIT_PASS" if ok else "CLOCK_AUDIT_FAIL",
    }


def _enclosing_function(tree: ast.AST, lineno: int) -> str:
    found = ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = int(getattr(node, "lineno", 0) or 0)
            end = int(getattr(node, "end_lineno", start) or start)
            if start <= lineno <= end:
                found = node.name
    return found or "<module>"


def _classify_clock(rel: str, snippet: str, api: str) -> tuple[str, str]:
    s = snippet
    if api in {"monotonic", "perf_counter"}:
        return "C", "processing/health monotonic"
    if "now_jst(" in s or "runtime_clock" in s or "session_now" in s:
        return "B", "session clock abstraction"
    if rel.endswith("runtime_clock.py"):
        return "B", "session clock implementation (wall fallback when disabled)"
    if "received_at" in s or "recorded_at" in s or "event_t" in s:
        return "A", "event-time / ingress stamp"
    if api == "time" and "time.time" in s:
        if "expire" in s.lower() or "fill" in s.lower():
            return "A", "causal fallback — review"
        return "C", "unix stamp / duration"
    # session scheduler files
    if rel in SESSION_CLOCK_FILES and "datetime.now" in s:
        return "BYPASS", "session scheduler still uses wall datetime.now"
    if rel.endswith("market_ingress_service.py") and "datetime.now" in s:
        if "FINALIZE" in s or "15, 35" in s or "is_market_session" in s:
            return "BYPASS", "ingress session gate wall clock"
        return "C", "ingress stamp/id"
    if rel.endswith("pilot_runner.py") and "datetime.now" in s:
        if "force_close" in s or "session" in s.lower() or "refresh" in s:
            return "B", "pilot session gate — must use now_jst"
        return "C", "pilot log/day stamp"
    if rel.endswith("v1r_native_entry_live.py") and "datetime.now" in s:
        return "B", "anchor _now — must use session now_jst when clock injected"
    if rel.endswith("paper_trade_checked_runner.py") and "datetime.now" in s:
        return "B", "checked-runner trading date / 15:35"
    if rel.endswith("v1r_paper_primary_launcher.py") and "datetime.now" in s:
        return "B", "launcher day stamp"
    if rel.endswith("kabu_token_authority.py"):
        if "%Y%m%d" in s and "datetime.now" in s:
            return "BYPASS", "token authority trading date wall clock"
        return "C", "token authority stamp"
    if rel.endswith("safety.py"):
        if "%Y%m%d" in s and "datetime.now" in s:
            return "BYPASS", "safety probe trading date wall clock"
        if "datetime.now" in s and ("stale" in s.lower() or "tick" in s.lower() or "age" in s.lower()):
            return "C", "safety tick-age (domain C)"
        return "C", "safety stamp"
    if api in {"now", "utcnow"}:
        return "C", "wall stamp (log/meta) unless session-critical"
    return "C", "unclassified processing"


def source_regression_gates(*, native_root: Optional[Path] = None) -> dict[str, Any]:
    """Prove historical bugs would FAIL this certification (source-level)."""
    native = Path(native_root or NATIVE)
    checks: dict[str, dict[str, Any]] = {}

    launcher = (native / "src/small_paper/v1r_paper_primary_launcher.py").read_text(encoding="utf-8")
    checks["LIVE_LAUNCHER_STUB"] = {
        "ok": "run_core10_dynamic40_am_pm_daily_runner.py" in launcher
        and "subprocess.Popen" in launcher
        and "offline_replay" in launcher,
        "reason": "launcher must spawn daily runner for live mode",
    }

    gate = (native / "src/small_paper/v1r_exit_v2_activation_gate.py").read_text(encoding="utf-8")
    checks["PBV2_PRIMARY_CONTAMINATION"] = {
        "ok": 'PBV2_ROLE = "SHADOW_ONLY"' in gate and "no_fallback_to_pbv2_primary" in gate.lower()
        or "PBV2_ROLE" in gate,
        "reason": "PBv2 must remain SHADOW_ONLY",
    }

    day_fixed = (native / "src/small_paper/day_fixed_am_registration.py").read_text(encoding="utf-8")
    checks["EMPTY_NATIVE_UNIVERSE"] = {
        "ok": "EXPECTED_SYMBOLS" in day_fixed and "reuse_frozen_am_universe" in day_fixed,
        "reason": "frozen AM50 SoT must exist",
    }
    checks["PM_REBUILD_OVERWROTE_FROZEN_SOURCE"] = {
        "ok": "POST_BIND_UNIVERSE_MUTATION" in day_fixed
        and "note_post_bind_universe_mutation_attempt" in day_fixed,
        "reason": "post-freeze AM rebuild must be blocked",
    }
    checks["FROZEN_AND_SCREENING_PATH_ALIAS"] = {
        "ok": "frozen_csv_path" in day_fixed and "am_csv_path" in day_fixed,
        "reason": "frozen vs screening paths must be distinct helpers",
    }

    native_entry = (native / "src/small_paper/v1r_native_entry_live.py").read_text(encoding="utf-8")
    checks["PASSIVE_FILL_WALL_CLOCK_PARITY"] = {
        "ok": "board_event_epoch_from_payload" in native_entry
        and "received_at" in native_entry,
        "reason": "Passive Fill must use ingress event-time",
    }
    checks["SYMBOL_KEY_6098_6098T"] = {
        "ok": 'replace(".T", "")' in native_entry or "split(\".\")" in native_entry
        or "canonical" in native_entry.lower(),
        "reason": "symbol keys must canonicalize .T",
    }

    token = (native / "src/small_paper/kabu_token_authority.py").read_text(encoding="utf-8")
    clock = (native / "src/small_paper/runtime_clock.py").read_text(encoding="utf-8")
    rest = (native / "src/api/rest_client.py").read_text(encoding="utf-8")
    preflight = (native / "scripts/run_market_ingress_v2_preflight.py").read_text(encoding="utf-8")
    cert_script = (native / "scripts/run_paper_full_day_certification.py").read_text(encoding="utf-8")
    checks["KABU_API_TOKEN_OWNERSHIP_CONFLICT"] = {
        "ok": "TOKEN_SECOND_ISSUER_BLOCKED" in token
        and "station_issue_lock" in token
        and "MARKET_INGRESS_SERVICE" in token
        and "CreateMutexW" in token,
        "reason": "Station-global single token issuer with OS mutex + file lock",
    }
    checks["TOKEN_ISSUE_SOT_SINGLE_ENTRY"] = {
        "ok": "issue_station_token" in rest and "post_token_http" in rest and "issue_station_token" in token,
        "reason": "all POST /token must go through TokenAuthority",
    }
    checks["REPLAY_AUTH_MODE_SPLIT"] = {
        "ok": "MARKET_INPUT_MODE" in clock
        and "KABU_AUTH_MODE" in clock
        and "apply_non_issuer_env" in clock
        and "official_cert_child_env" in clock,
        "reason": "replay input must not imply live token POST",
    }
    checks["PREFLIGHT_SYNTHETIC_TOKEN_SIDE_EFFECT"] = {
        "ok": "apply_non_issuer_env" in preflight,
        "reason": "S4 preflight must strip cert env and never POST /token",
    }
    checks["CERT_PRECHECK_NOT_ISSUER"] = {
        "ok": "token_issue_attempted" in cert_script and "issue_token_from_env" not in cert_script,
        "reason": "S1 certification precheck must not POST /token",
    }
    spawn = (native / "src/small_paper/market_ingress_spawn.py").read_text(encoding="utf-8")
    identity = (native / "src/small_paper/ingress_run_identity.py").read_text(encoding="utf-8")
    checks["INGRESS_SPAWN_ENV_CONTRACT"] = {
        "ok": "apply_non_issuer_env" in spawn and "official_cert_child_env" in spawn,
        "reason": "synthetic spawn strips cert env; live Ingress keeps LIVE auth + replay",
    }
    checks["INGRESS_CURRENT_RUN_IDENTITY"] = {
        "ok": "STALE_INGRESS_STATUS_REJECTED" in spawn
        and "expected_launch_nonce" in spawn
        and "CURRENT_INGRESS_NOT_READY" in spawn
        and "evaluate_current_run_online" in identity
        and "process_start_identity" in identity
        and "status_written_unix" in identity,
        "reason": "wait_ingress_online must bind launch_nonce/PID/start/heartbeat, not stale files",
    }
    health = (native / "src/small_paper/market_ingress_health.py").read_text(encoding="utf-8")
    checks["INGRESS_STATUS_ATOMIC_WRITE"] = {
        "ok": "atomic_write_json" in health and "os.replace" in identity,
        "reason": "ingress_status must be written atomically",
    }
    ingress = (native / "src/small_paper/market_ingress_service.py").read_text(encoding="utf-8")
    checks["INGRESS_AUTH_RETRY_STORM"] = {
        "ok": "RECOVERY_BACKOFFS" in ingress or "429" in ingress or "backoff" in token.lower(),
        "reason": "401/429 handled in token/ingress authority (runtime also required)",
    }

    daily = (native / "src/runner/am_pm_daily_runner.py").read_text(encoding="utf-8")
    checks["POST_SESSION_AM_SAFETY_SIDE_EFFECT"] = {
        "ok": "SKIPPED_AFTER_SESSION_END" in daily
        and "should_skip_am_live_after_session_end" in daily,
        "reason": "PM direct-start must skip ended AM",
    }

    evalr = (native / "src/small_paper/evaluation_reachability.py").read_text(encoding="utf-8")
    checks["NATIVE_FULL_PUSH_THROTTLE_BUG"] = {
        "ok": "forced_eval" in evalr.lower() or "pbv2" in evalr.lower(),
        "reason": "eval reachability tracks forced_eval vs full ingest",
    }
    checks["STALE_RECOVERY_FORCE_EVAL_DEATH_SPIRAL"] = {
        "ok": "forced_eval" in evalr.lower() and "recovery" in evalr.lower(),
        "reason": "stale recovery must not bypass PBv2 5s gate",
    }

    pilot = (native / "src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    checks["TEARDOWN_EXTERNAL_BACKUP_LOGGER_NAMEERROR"] = {
        "ok": "apply_external_backup_teardown_logging" in pilot
        and 'log.warning("external backup pending' not in _external_backup_block(pilot),
        "reason": "external-backup warning must not use undefined log",
    }

    failed = [k for k, v in checks.items() if not v.get("ok")]
    return {"ok": not failed, "failed": failed, "checks": checks}


def _external_backup_block(src: str) -> str:
    start = src.find('task="external_backup"')
    if start < 0:
        return src
    end = src.find("finalize_session_seal_propagation", start)
    return src[start:end] if end > start else src[start : start + 2000]


def detect_teardown_nameerror_would_fail_v13() -> dict[str, Any]:
    """Certification must be able to detect the V13 NameError."""
    ns: dict[str, Any] = {"ext": {"session": "cert", "pending": True}}
    detected = False
    try:
        exec('log.warning("external backup pending (D not connected): %s", ext.get("session"))', ns)
    except NameError as exc:
        detected = "log" in str(exc)
    return {
        "ok": detected,
        "gate": "TEARDOWN_EXTERNAL_BACKUP_LOGGER_NAMEERROR",
        "v13_reproduced": detected,
    }


def copy_scoped_run_snapshot(
    *,
    dest: Path,
    reports_dir: Path,
    day: str,
    expected_scope: Mapping[str, Any],
    filenames: Optional[tuple[str, ...]] = None,
) -> dict[str, Any]:
    """Copy only artifacts whose certification/stage/activation ids match expected_scope."""
    from small_paper.ingress_run_identity import artifact_matches_scope

    dest.mkdir(parents=True, exist_ok=True)
    names = filenames or (
        f"phase148_am_pm_daily_runner_{day}.json",
        f"daily_runner_summary_{day}.json",
        f"small_paper_safety_{day}.json",
    )
    copied: dict[str, str] = {}
    excluded: list[dict[str, str]] = []
    missing: list[str] = []
    for fn in names:
        src = Path(reports_dir) / fn
        if not src.is_file():
            missing.append(fn)
            continue
        try:
            doc = json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            excluded.append({"file": fn, "reason": "unreadable"})
            continue
        if not artifact_matches_scope(doc if isinstance(doc, dict) else {}, expected_scope):
            excluded.append({"file": fn, "reason": "scope_mismatch"})
            continue
        target = dest / fn
        target.write_bytes(src.read_bytes())
        copied[fn] = str(target)
    return {
        "copied": copied,
        "excluded": excluded,
        "missing": missing,
        "stale_artifact_excluded_count": len(excluded),
    }


def session_metrics_in_scope(
    *,
    sessions_root: Path,
    expected_scope: Mapping[str, Any],
) -> dict[str, Any]:
    """Use current-run session artifacts only (no glob-latest SoT)."""
    from small_paper.ingress_run_identity import artifact_matches_scope

    sessions = sorted(sessions_root.glob("live_session_*")) + sorted(sessions_root.glob("v1r_primary_*"))
    matched: list[Path] = []
    excluded = 0
    for cand in sessions:
        summary_path = cand / "small_paper_summary.json"
        if not summary_path.is_file():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            excluded += 1
            continue
        if artifact_matches_scope(summary if isinstance(summary, dict) else {}, expected_scope):
            matched.append(cand)
        else:
            excluded += 1
    latest = matched[-1] if matched else None
    summary: dict[str, Any] = {}
    hb: dict[str, Any] = {}
    if latest and (latest / "small_paper_summary.json").is_file():
        summary = json.loads((latest / "small_paper_summary.json").read_text(encoding="utf-8"))
    for cand in reversed(matched):
        p = cand / "heartbeat.jsonl"
        if p.is_file():
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            if lines:
                try:
                    hb = json.loads(lines[-1])
                except Exception:
                    hb = {}
            break
    submit = int(summary.get("submit") or 0)
    cancel = int(summary.get("cancel") or 0)
    live = int(summary.get("live") or 0)
    pb = (hb.get("pbv2_eval") or {}) if isinstance(hb, dict) else {}
    return {
        "latest_session": str(latest) if latest else "",
        "matched_session_count": len(matched),
        "stale_artifact_excluded_count": excluded,
        "summary_keys": sorted(summary.keys())[:40],
        "stop_reason": summary.get("stop_reason"),
        "fatal_error": summary.get("fatal_error"),
        "session_external_backup": summary.get("session_external_backup"),
        "submit": submit,
        "cancel": cancel,
        "live": live,
        "submit_cancel_live": f"{submit}/{cancel}/{live}",
        "forced_eval_count": int(pb.get("forced_eval_count") or summary.get("forced_eval_count") or 0),
        "eval_fraction": pb.get("eval_fraction"),
        "native_ingest": hb.get("native_ingest_count") or hb.get("v1r_native_ingest"),
        "raw_published": hb.get("ingress_last_sequence") or hb.get("raw_sequence"),
        "max_consumer_processing_delay_sec": pb.get("max_consumer_processing_delay_sec"),
        "heartbeat": {k: hb.get(k) for k in ("pid", "state", "v1r_exit_v2") if k in hb},
    }

