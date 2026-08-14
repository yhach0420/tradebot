"""Current-run derived-artifact contract (V22).

Derived verdicts (design consistency, preflight, safety) are not SoT.
Pinned activations / Frozen Universe / Precommit remain exact-SHA pins.
Ingress status remains the V21 current-run identity contract.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from small_paper.ingress_run_identity import (
    ENV_ACTIVATION_ID,
    ENV_ACTIVATION_SHA,
    ENV_CERTIFICATION_RUN_ID,
    ENV_STAGE_RUN_ID,
    activation_identity,
    atomic_write_json,
)

ARTIFACT_SCHEMA_VERSION = "DERIVED_ARTIFACT_CURRENT_RUN_V1"
STALE_DERIVED_ARTIFACT_REJECTED = "STALE_DERIVED_ARTIFACT_REJECTED"
CURRENT_DESIGN_CONSISTENCY_NOT_PROVEN = "CURRENT_DESIGN_CONSISTENCY_NOT_PROVEN"
ENV_RUNTIME_RUN_ID = "TRADEBOT_RUNTIME_RUN_ID"

REQUIRED_DERIVED_FIELDS = (
    "artifact_schema_version",
    "artifact_type",
    "activation_id",
    "activation_sha",
    "runtime_commit",
    "config_sha",
    "trading_date",
    "generated_at",
    "input_manifest_sha",
)

DESIGN_PATH_REL = Path("results/reports/phase687w3_e2e_readonly_reconciliation/phase687w3_design_consistency.json")
SCHEMA_REL = Path("docs/live_trading/schema/live_order_design_schema.json")

# Production startup / recovery / safety decision inventory (V22 audit).
# class A = pinned exact SHA; B = current-run identity; C = recompute/identity;
# D = historical/audit only — never a runtime PASS/FAIL SoT.
RUNTIME_ARTIFACT_DECISION_INVENTORY: tuple[dict[str, str], ...] = (
    {"path": "results/research/v1r_exit_v2_prospective_activation/V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_*.json", "class": "A", "use": "activation_pin", "decision": "startup_bind"},
    {"path": "results/research/v1r_exit_v2_prospective_activation/PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY.json", "class": "A", "use": "strategy_pin", "decision": "startup_bind"},
    {"path": "results/research/v1r_exit_v2_prospective_activation/PROSPECTIVE_PRECOMMIT_V1R_EXIT_V2_U1.json", "class": "A", "use": "precommit_pin", "decision": "startup_bind"},
    {"path": "data/universe/*frozen* / Frozen AM CSV", "class": "A", "use": "frozen_am50", "decision": "universe_bind"},
    {"path": "configs/production_config_sha256.pin", "class": "A", "use": "config_pin", "decision": "recovery_config_sha"},
    {"path": "data/market_capture/{day}/ingress_status.json", "class": "B", "use": "ingress_online", "decision": "wait_ingress_online"},
    {"path": "data/market_capture/{day}/ingress_spawn.json", "class": "B", "use": "spawn_identity", "decision": "wait/reuse"},
    {"path": "kabu_station_token_bundle.json", "class": "B", "use": "station_token", "decision": "issue_station_token/acquire_readonly"},
    {"path": "results/reports/phase687w3_e2e_readonly_reconciliation/phase687w3_design_consistency.json", "class": "C", "use": "design_consistency", "decision": "recovery/enablement"},
    {"path": "results/reports/small_paper_safety_{day}.json", "class": "C", "use": "safety_verdict", "decision": "daily_runner_preflight"},
    {"path": "results/reports/phase148_am_pm_daily_runner_{day}.json", "class": "C", "use": "daily_lifecycle", "decision": "certification_evaluate"},
    {"path": "results/reports/daily_runner_summary_{day}.json", "class": "C", "use": "daily_summary", "decision": "certification_evaluate"},
    {"path": "results/small_paper/{day}/live_session_*/small_paper_summary.json", "class": "C", "use": "session_summary", "decision": "cert_metrics"},
    {"path": "results/small_paper/{day}/*/session_seal.json", "class": "B", "use": "session_seal", "decision": "recovery_prior_session"},
    {"path": "results/small_paper/{day}/live_session_*/session_manifest.json", "class": "B", "use": "prior_session_manifest", "decision": "recovery_prior_session"},
    {"path": "results/research/paper_runtime_full_day_certification/runs/{certification_run_id}/{stage_run_id}/**", "class": "C", "use": "cert_current_stage_dest", "decision": "cert_evaluate_copied_only"},
    {"path": "results/research/paper_runtime_full_day_certification/run_snapshots/**", "class": "D", "use": "legacy_cert_dest", "decision": "forbidden_current_sot"},
    {"path": "results/reports/phase687w4_*/phase687w4_design_consistency.json", "class": "D", "use": "w4_copy", "decision": "audit_only"},
    {"path": "results/reports/phase687w5_*/phase687w5_design_consistency.json", "class": "D", "use": "w5_copy", "decision": "audit_only"},
)


def ensure_runtime_run_id(*, environ: Optional[dict[str, str]] = None) -> str:
    env = environ if environ is not None else os.environ
    rid = str(env.get(ENV_RUNTIME_RUN_ID) or "").strip()
    if rid:
        return rid
    rid = "rtrun_" + hashlib.sha256(f"{os.getpid()}:{time.time_ns()}".encode()).hexdigest()[:24]
    env[ENV_RUNTIME_RUN_ID] = rid
    return rid


def _git_head(repo: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), text=True, timeout=8
        ).strip()
    except Exception:
        return ""


def current_derived_scope(
    *,
    native_root: Path,
    trading_date: str = "",
    config_path: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    env = dict(environ if environ is not None else os.environ)
    native = Path(native_root)
    aid, ash = activation_identity(environ=env)
    runtime_commit = ""
    try:
        from small_paper.v1r_activation_binding import load_activation_manifest, load_active_selector

        sel = load_active_selector()
        man = load_activation_manifest(selector=sel)
        runtime_commit = str(man.get("runtime_code_git_commit") or "")
        if not aid:
            aid = str(sel.get("activation_id") or "")
        if not ash:
            ash = str(sel.get("activation_sha") or "")
    except Exception:
        pass
    if not runtime_commit:
        runtime_commit = _git_head(native.parent) or "UNSET"
    if not aid:
        aid = "UNSET"
    if not ash:
        ash = "UNSET"
    cfg = Path(config_path) if config_path else native / "configs" / "small_paper_pilot.yaml"
    cfg_sha = ""
    if cfg.is_file():
        from small_paper.v1r_activation_binding import file_sha256

        cfg_sha = file_sha256(cfg)
    else:
        cfg_sha = "MISSING"
    schema = native / SCHEMA_REL
    schema_sha = ""
    if schema.is_file():
        from small_paper.v1r_activation_binding import file_sha256

        schema_sha = file_sha256(schema)
    token_statuses = ""
    try:
        from small_paper.kabu_readonly_readiness import TokenProbeStatus

        token_statuses = ",".join(s.value for s in TokenProbeStatus)
    except Exception:
        pass
    manifest_body = {
        "schema_sha256": schema_sha,
        "token_probe_statuses": token_statuses,
        "activation_sha": ash,
        "runtime_commit": runtime_commit,
        "config_sha": cfg_sha,
    }
    input_manifest_sha = hashlib.sha256(
        json.dumps(manifest_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    day = str(trading_date or env.get("TRADEBOT_TRADING_DATE") or "").strip()
    if not day:
        try:
            from small_paper.runtime_clock import trading_date_jst, session_now

            day = trading_date_jst(session_now())
        except Exception:
            day = ""
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "certification_run_id": str(env.get(ENV_CERTIFICATION_RUN_ID) or "").strip(),
        "stage_run_id": str(env.get(ENV_STAGE_RUN_ID) or "").strip(),
        "runtime_run_id": ensure_runtime_run_id(environ=env),
        "activation_id": aid,
        "activation_sha": ash,
        "runtime_commit": runtime_commit,
        "config_sha": cfg_sha,
        "trading_date": day,
        "input_manifest_sha": input_manifest_sha,
        "schema_sha256": schema_sha,
    }


def stamp_derived_artifact(
    doc: dict[str, Any],
    *,
    artifact_type: str,
    native_root: Path,
    trading_date: str = "",
    config_path: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    producer: str = "",
) -> dict[str, Any]:
    scope = current_derived_scope(
        native_root=native_root,
        trading_date=trading_date,
        config_path=config_path,
        environ=environ,
    )
    doc["artifact_schema_version"] = ARTIFACT_SCHEMA_VERSION
    doc["artifact_type"] = artifact_type
    doc["generated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    if producer:
        doc["producer"] = producer
    for key in (
        "certification_run_id",
        "stage_run_id",
        "runtime_run_id",
        "activation_id",
        "activation_sha",
        "runtime_commit",
        "config_sha",
        "trading_date",
        "input_manifest_sha",
    ):
        val = scope.get(key) or ""
        if val:
            doc[key] = val
    return doc


def validate_derived_artifact(
    doc: Optional[Mapping[str, Any]],
    expected: Mapping[str, Any],
    *,
    require_cert_ids: bool = False,
) -> dict[str, Any]:
    out = {"ok": False, "reason": STALE_DERIVED_ARTIFACT_REJECTED, "reject_code": ""}
    if not isinstance(doc, Mapping) or not doc:
        out["reject_code"] = "missing_artifact"
        return out
    for field in REQUIRED_DERIVED_FIELDS:
        if not str(doc.get(field) or "").strip():
            out["reject_code"] = f"missing_field:{field}"
            return out
    if str(doc.get("artifact_schema_version") or "") != ARTIFACT_SCHEMA_VERSION:
        out["reject_code"] = "schema_mismatch"
        return out
    for key in ("activation_id", "activation_sha", "runtime_commit", "config_sha", "input_manifest_sha"):
        want = str(expected.get(key) or "").strip()
        got = str(doc.get(key) or "").strip()
        if not want:
            out["reject_code"] = f"expected_missing:{key}"
            return out
        if got != want:
            out["reject_code"] = f"{key}_mismatch"
            return out
    exp_day = str(expected.get("trading_date") or "").strip()
    if exp_day and str(doc.get("trading_date") or "").strip() != exp_day:
        out["reject_code"] = "trading_date_mismatch"
        return out
    cert = str(expected.get("certification_run_id") or "").strip()
    stage = str(expected.get("stage_run_id") or "").strip()
    if require_cert_ids or cert or stage:
        if cert and str(doc.get("certification_run_id") or "").strip() != cert:
            out["reject_code"] = "certification_run_id_mismatch"
            return out
        if stage and str(doc.get("stage_run_id") or "").strip() != stage:
            out["reject_code"] = "stage_run_id_mismatch"
            return out
    out["ok"] = True
    out["reason"] = "CURRENT_RUN_DERIVED_OK"
    out["reject_code"] = ""
    return out


def _runtime_native_root() -> Path:
    return Path(__file__).resolve().parents[2]


def dump_runtime_artifact_inventory(path: Path) -> dict[str, Any]:
    body = {
        "schema": "RUNTIME_ARTIFACT_DECISION_INVENTORY_V1",
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "classes": {
            "A": "IMMUTABLE_PINNED_ARTIFACT",
            "B": "CURRENT_RUN_SOURCE_STATE",
            "C": "DERIVED_VERDICT_MUST_RECOMPUTE_OR_BIND",
            "D": "HISTORICAL_AUDIT_ONLY_FORBIDDEN_CURRENT_SOT",
        },
        "items": [dict(row) for row in RUNTIME_ARTIFACT_DECISION_INVENTORY],
        "note": (
            "Runtime PASS/FAIL/READY/BLOCK/RECOVER must not use class D. "
            "Class C leftover JSON is never SoT; re-evaluate from current inputs."
        ),
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return body


def _load_design_check(native_root: Path) -> Any:
    cand = Path(native_root) / "scripts" / "check_live_order_design_consistency.py"
    if not cand.is_file():
        cand = _runtime_native_root() / "scripts" / "check_live_order_design_consistency.py"
    spec = importlib.util.spec_from_file_location("check_live_order_design_consistency_mod", cand)
    if spec is None or spec.loader is None:
        raise RuntimeError("design_consistency_script_unreadable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def recompute_design_consistency(*, native_root: Path) -> dict[str, Any]:
    mod = _load_design_check(native_root)
    return dict(mod.check())


def evaluate_or_recompute_design_consistency(
    native_root: Path,
    *,
    trading_date: str = "",
    config_path: Optional[Path] = None,
    recompute_fn: Optional[Callable[[], Mapping[str, Any]]] = None,
    write: bool = True,
    path: Optional[Path] = None,
) -> dict[str, Any]:
    """Current-run design consistency. Stale leftover JSON is never a FAIL SoT."""
    root = Path(native_root)
    path = Path(path) if path is not None else root / DESIGN_PATH_REL
    expected = current_derived_scope(
        native_root=root, trading_date=trading_date, config_path=config_path
    )
    out: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "pass": False,
        "status": "missing",
        "recomputed": False,
        "stale_derived_artifact_rejected": False,
        "reject_code": "",
        "input_manifest_sha": expected.get("input_manifest_sha"),
        "expected_scope": {k: expected.get(k) for k in ("activation_sha", "runtime_commit", "config_sha")},
    }
    payload: Optional[dict[str, Any]] = None
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            payload = loaded if isinstance(loaded, dict) else None
        except Exception as exc:
            out["status"] = f"corrupt:{exc}"
            payload = None
    require_cert = bool(expected.get("certification_run_id") or expected.get("stage_run_id"))
    valid = validate_derived_artifact(payload, expected, require_cert_ids=require_cert)
    if valid.get("ok") and payload is not None:
        out["pass"] = bool(payload.get("pass"))
        out["status"] = "ok" if out["pass"] else "fail"
        out["mismatch_count"] = payload.get("mismatch_count")
        out["recomputed"] = False
        return out

    out["stale_derived_artifact_rejected"] = True
    out["reject_code"] = str(valid.get("reject_code") or STALE_DERIVED_ARTIFACT_REJECTED)
    out["status"] = STALE_DERIVED_ARTIFACT_REJECTED
    try:
        fresh = dict(recompute_fn() if recompute_fn is not None else recompute_design_consistency(native_root=root))
    except Exception as exc:
        out["status"] = CURRENT_DESIGN_CONSISTENCY_NOT_PROVEN
        out["pass"] = False
        out["error"] = f"{type(exc).__name__}:{exc}"
        return out
    stamp_derived_artifact(
        fresh,
        artifact_type="design_consistency",
        native_root=root,
        trading_date=trading_date or expected.get("trading_date") or "",
        config_path=config_path,
        producer="evaluate_or_recompute_design_consistency",
    )
    out["recomputed"] = True
    out["pass"] = bool(fresh.get("pass"))
    out["mismatch_count"] = fresh.get("mismatch_count")
    out["mismatches"] = fresh.get("mismatches") if not out["pass"] else []
    out["status"] = "ok" if out["pass"] else "fail"
    out["input_manifest_sha"] = fresh.get("input_manifest_sha")
    if write:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, fresh)
        except Exception as exc:
            out["status"] = CURRENT_DESIGN_CONSISTENCY_NOT_PROVEN
            out["pass"] = False
            out["error"] = f"write_failed:{type(exc).__name__}:{exc}"
            return out
    return out


def cert_run_root(cert_dir: Path, certification_run_id: str) -> Path:
    rid = str(certification_run_id or "").strip() or "_missing_certification_run_id"
    return Path(cert_dir) / "runs" / rid


def cert_stage_dest(cert_dir: Path, certification_run_id: str, stage_run_id: str) -> Path:
    sid = str(stage_run_id or "").strip() or "_missing_stage_run_id"
    return cert_run_root(cert_dir, certification_run_id) / sid
