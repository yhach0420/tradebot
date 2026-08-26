"""V1R Activation binding — selector / inventory / hash SoT (no Strategy semantics).

Breaks the freeze cycle:
  hardcoded gate ACTIVATION_SHA ↔ manifest.runtime_file_sha256[gate]

SoT:
  A) active selector (default file, or TRADEBOT_ACTIVATION_SELECTOR)
     → expected activation_id / activation_sha
  B) activation manifest         → self sha256
  C) manifest.runtime_file_sha256 → working-tree Path.read_bytes() SHA256
  D) Strategy / Precommit / roles from manifest (verified by gate)

Same resolver is used for Formal Paper and Certification. Certification does
not skip inventory. UNCERTIFIED candidates are fail-closed outside certification.

Hash policy (single SoT): actual working-tree bytes via Path.read_bytes().
No LF/CRLF normalization on either freeze or startup path.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results/research/v1r_exit_v2_prospective_activation"
SELECTOR_NAME = "active_v1r_activation.json"
SELECTOR_PATH = OUT / SELECTOR_NAME
SELECTOR_SCHEMA = "V1R_ACTIVE_ACTIVATION_SELECTOR_V1"
ENV_ACTIVATION_SELECTOR = "TRADEBOT_ACTIVATION_SELECTOR"
V25_ACTIVATION_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V25"
CANDIDATE_STATUS_UNCERTIFIED = "UNCERTIFIED"
CANDIDATE_STATUS_FORMAL = "FORMAL"
CANDIDATE_STATUS_OPVAL = "OPERATIONAL_VALIDATION_ONLY"
UNCERTIFIED_NOT_ALLOWED = "UNCERTIFIED_CANDIDATE_NOT_ALLOWED_FOR_FORMAL_PAPER"

# Strategy Runtime dependency inventory (binding selector is NOT included).
# Gate is included: after binding refactor it must not hard-code activation SHA,
# so selector bind does not mutate inventory bytes.
RUNTIME_DEPENDENCY_RELS: tuple[str, ...] = (
    "src/small_paper/v1r_activation_binding.py",
    "src/small_paper/v1r_paper_primary_launcher.py",
    "src/small_paper/v1r_exit_v2_activation_gate.py",
    "src/small_paper/v1r_native_entry_live.py",
    "src/small_paper/v1r_live_dual_lane.py",
    "src/small_paper/v1r_pbv2_shadow_discord_digest.py",
    "src/small_paper/v1r_pbv2_notification_routing.py",
    "src/small_paper/v1r_pbv2_duplicate_runtime.py",
    "src/small_paper/v1r_prospective_day_gate.py",
    "src/small_paper/runtime_clock.py",
    "src/small_paper/paper_full_day_certification.py",
    "src/small_paper/pilot_runner.py",
    "src/small_paper/paper_market_bus_consumer.py",
    "src/small_paper/local_market_bus.py",
    "src/small_paper/capture_sequence_reader.py",
    "src/small_paper/paper_trade_checked_runner.py",
    "src/small_paper/market_ingress_spawn.py",
    "src/small_paper/ingress_run_identity.py",
    "src/small_paper/session_runtime_identity.py",
    "src/small_paper/certification_input_coverage.py",
    "src/small_paper/derived_artifact_contract.py",
    "src/small_paper/discord_notifier.py",
    "src/small_paper/canonical_summary.py",
    "src/small_paper/v1r_primary_runtime.py",
    "src/small_paper/v1r_exit_v2_contract.py",
    "src/small_paper/v1r_primary_activation_gate.py",
    "src/notify/v1r_discord_routing.py",
    "src/notify/v1r_discord_embeds.py",
    "src/research/e1_x34a_execution_policy/arms.py",
    "src/small_paper/day_fixed_am_registration.py",
    "src/small_paper/kabu_registration_authority.py",
    "src/small_paper/kabu_token_authority.py",
    "src/small_paper/auth_lifecycle.py",
    "src/small_paper/kabu_readonly_readiness.py",
    "src/api/rest_client.py",
    "src/small_paper/ingress_control_channel.py",
    "src/small_paper/market_capture_registration.py",
    "src/small_paper/market_ingress_service.py",
    "src/small_paper/consumer_ack_state.py",
    "src/small_paper/evaluation_reachability.py",
    "src/small_paper/live_writer.py",
    "src/small_paper/consumer_push_telemetry.py",
    "src/api/kabu_register.py",
    "src/small_paper/safety.py",
    "src/small_paper/registration_lifetime.py",
    "src/runner/am_pm_daily_runner.py",
    "src/universe/core10_dynamic40_price_risk.py",
    # V26 runtime-critical modules (AUTH/lifecycle/ownership/ingress state).
    # Generated from the same inventory collector; not a rehash of the V25 44-set.
    "src/small_paper/auth_issue_trace.py",
    "src/small_paper/ownership_classifier.py",
    "src/small_paper/runtime_lifecycle.py",
    "src/small_paper/runtime_ownership.py",
    "src/small_paper/bounded_side_task.py",
    "src/small_paper/capture_child_cleanup.py",
    "src/small_paper/market_ingress_state.py",
    "src/small_paper/operational_validation.py",
)

# Must be ⊆ RUNTIME_DEPENDENCY_RELS. Uncovered ⇒ inventory coverage FAIL.
RUNTIME_CRITICAL_MUST_COVER: tuple[str, ...] = (
    "src/small_paper/auth_issue_trace.py",
    "src/small_paper/ownership_classifier.py",
    "src/small_paper/runtime_lifecycle.py",
    "src/small_paper/runtime_ownership.py",
    "src/small_paper/bounded_side_task.py",
    "src/small_paper/capture_child_cleanup.py",
    "src/small_paper/market_ingress_state.py",
    "src/small_paper/canonical_summary.py",
    "src/small_paper/operational_validation.py",
)

# Disk scan for unexpected runtime-critical modules not yet in the generator.
RUNTIME_CRITICAL_SCAN_PREFIXES: tuple[str, ...] = (
    "auth_issue_trace",
    "ownership_classifier",
    "runtime_lifecycle",
    "runtime_ownership",
    "operational_validation",
)

# Forbidden in runtime inventory (would reintroduce selector↔manifest cycles).
FORBIDDEN_INVENTORY_NAMES: frozenset[str] = frozenset(
    {
        SELECTOR_NAME,
        "active_v1r_activation.json",
        "runtime_pins.json",
    }
)


def file_sha256(path: Path) -> str:
    """SoT: SHA256 of actual working-tree bytes (Path.read_bytes). No newline normalize."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory_digest(inv: Mapping[str, str]) -> str:
    payload = json.dumps(dict(sorted((str(k).replace("\\", "/"), str(v)) for k, v in inv.items())), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def candidate_source_digest(inv: Mapping[str, str], *, native_root: Optional[Path] = None) -> str:
    """SHA256 of concatenated working-tree bytes for every inventoried path (sorted)."""
    root = Path(native_root or NATIVE)
    h = hashlib.sha256()
    for rel in sorted(str(k).replace("\\", "/") for k in inv):
        p = root / rel
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def v25_frozen_inventory_rels(*, native_root: Optional[Path] = None) -> tuple[str, ...]:
    root = Path(native_root or NATIVE)
    p = root / "results/research/v1r_exit_v2_prospective_activation" / f"{V25_ACTIVATION_ID}.json"
    if not p.is_file():
        return tuple()
    body = json.loads(p.read_text(encoding="utf-8"))
    inv = body.get("runtime_file_sha256") or {}
    return tuple(sorted(str(k).replace("\\", "/") for k in inv))


def resolve_selector_path(
    *,
    path: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    """Generic activation selector: explicit path, else env, else default V25 selector file."""
    if path is not None:
        return Path(path)
    env = environ if environ is not None else os.environ
    raw = str(env.get(ENV_ACTIVATION_SELECTOR) or "").strip()
    if not raw:
        return SELECTOR_PATH
    p = Path(raw)
    if not p.is_absolute():
        p = NATIVE / p
    return p


def scan_runtime_critical_files(*, native_root: Optional[Path] = None) -> list[str]:
    root = Path(native_root or NATIVE)
    sp = root / "src" / "small_paper"
    found: list[str] = []
    if sp.is_dir():
        for p in sorted(sp.glob("*.py")):
            stem = p.stem
            if any(stem == pref or stem.startswith(pref) for pref in RUNTIME_CRITICAL_SCAN_PREFIXES):
                found.append(f"src/small_paper/{p.name}")
    for rel in RUNTIME_CRITICAL_MUST_COVER:
        if (root / rel).is_file() and rel not in found:
            found.append(rel)
    return found


def audit_runtime_inventory_coverage(*, native_root: Optional[Path] = None) -> dict[str, Any]:
    """Generator coverage vs V25 frozen set + V26 runtime-critical must-cover + disk scan."""
    root = Path(native_root or NATIVE)
    listed = {str(r).replace("\\", "/") for r in RUNTIME_DEPENDENCY_RELS}
    v25 = set(v25_frozen_inventory_rels(native_root=root))
    must = [str(r).replace("\\", "/") for r in RUNTIME_CRITICAL_MUST_COVER]
    uncovered = [r for r in must if r not in listed]
    missing_on_disk = [r for r in must if not (root / r).is_file()]
    scanned = scan_runtime_critical_files(native_root=root)
    unexpected = [r for r in scanned if r not in listed]
    new_added = sorted((listed - v25) | set(must))
    new_covered = [r for r in new_added if r in listed]
    ok = not uncovered and not missing_on_disk and not unexpected
    reason = ""
    if uncovered:
        reason = "runtime_critical_uncovered"
    elif missing_on_disk:
        reason = "runtime_critical_missing_on_disk"
    elif unexpected:
        reason = "unexpected_runtime_critical_module"
    return {
        "ok": ok,
        "reason": reason,
        "v25_inventory_count": len(v25),
        "v26_candidate_inventory_count": len(listed),
        "new_runtime_files_added": new_added,
        "new_runtime_files_covered": new_covered,
        "runtime_critical_uncovered_files": uncovered,
        "runtime_critical_missing_on_disk": missing_on_disk,
        "unexpected_runtime_critical_files": unexpected,
        "generator_rels": sorted(listed),
    }


def verify_generator_inventory_coverage(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Manifest inventory keys must equal the current generator set (no missing / extra)."""
    inv = manifest.get("runtime_file_sha256") or {}
    if not isinstance(inv, dict):
        inv = {}
    got = {str(k).replace("\\", "/") for k in inv}
    expected = {str(r).replace("\\", "/") for r in RUNTIME_DEPENDENCY_RELS}
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    ok = not missing and not extra and bool(got)
    return {
        "ok": ok,
        "reason": "" if ok else "inventory_generator_set_mismatch",
        "missing_from_manifest": missing,
        "extra_in_manifest": extra,
        "generator_n": len(expected),
        "manifest_n": len(got),
    }


def activation_paper_policy(manifest: Mapping[str, Any]) -> dict[str, Any]:
    status = str(manifest.get("candidate_status") or manifest.get("activation_status") or "").strip()
    if not status:
        status = CANDIDATE_STATUS_FORMAL
    formal_raw = manifest.get("formal_paper_allowed")
    if formal_raw is None:
        formal_allowed = status == CANDIDATE_STATUS_FORMAL
    else:
        formal_allowed = bool(formal_raw)
    return {
        "candidate_status": status,
        "formal_paper_allowed": formal_allowed,
        "immutable": bool(manifest.get("immutable", status == CANDIDATE_STATUS_FORMAL)),
    }


def uncertified_paper_blocked_reason(
    manifest: Mapping[str, Any],
    *,
    certification: Optional[bool] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """UNCERTIFIED candidate may only start under certification_mode. Same code for Paper/Cert."""
    if certification is None:
        from small_paper.runtime_clock import certification_mode

        certification = certification_mode(environ=dict(environ) if environ is not None else None)
    pol = activation_paper_policy(manifest)
    if (
        pol["candidate_status"] in {CANDIDATE_STATUS_UNCERTIFIED, CANDIDATE_STATUS_OPVAL}
        and not certification
    ):
        return UNCERTIFIED_NOT_ALLOWED
    return ""


def manifest_content_sha(obj: Mapping[str, Any]) -> str:
    """Canonical activation/strategy body SHA (excludes sha256 field)."""
    payload = {k: v for k, v in obj.items() if k != "sha256"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def collect_runtime_inventory(
    *,
    native_root: Optional[Path] = None,
    rels: Optional[Sequence[str]] = None,
) -> dict[str, str]:
    root = Path(native_root or NATIVE)
    out: dict[str, str] = {}
    for rel in rels or RUNTIME_DEPENDENCY_RELS:
        rel_n = rel.replace("\\", "/")
        name = Path(rel_n).name
        if name in FORBIDDEN_INVENTORY_NAMES or rel_n.endswith(SELECTOR_NAME):
            raise RuntimeError(f"selector/pins must not enter runtime inventory: {rel_n}")
        p = root / rel_n
        if not p.is_file():
            raise FileNotFoundError(rel_n)
        out[rel_n] = file_sha256(p)
    return out


def load_active_selector(
    *,
    path: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    p = resolve_selector_path(path=path, environ=environ)
    if not p.is_file():
        raise FileNotFoundError(f"active activation selector missing: {p}")
    body = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError("selector must be a JSON object")
    aid = str(body.get("activation_id") or "").strip()
    ash = str(body.get("activation_sha") or "").strip()
    if not aid or not ash:
        raise ValueError("selector requires activation_id and activation_sha")
    # Selector is identity-only — reject trading-condition keys if smuggled in.
    forbidden_econ = {
        "strategy_sha",
        "precommit_sha",
        "guard",
        "continuation",
        "wait_sec",
        "cap",
        "qty",
        "runtime_file_sha256",
    }
    bad = sorted(k for k in forbidden_econ if k in body)
    if bad:
        raise ValueError(f"selector must not carry economic/runtime fields: {bad}")
    return body


def resolve_manifest_path(
    selector: Mapping[str, Any],
    *,
    out_dir: Optional[Path] = None,
) -> Path:
    base = Path(out_dir or OUT)
    rel = str(selector.get("manifest_relpath") or "").strip()
    if rel:
        p = Path(rel)
        if not p.is_absolute():
            p = base / rel
    else:
        p = base / f"{selector['activation_id']}.json"
    return p


def load_activation_manifest(
    *,
    selector: Optional[Mapping[str, Any]] = None,
    path: Optional[Path] = None,
    out_dir: Optional[Path] = None,
) -> dict[str, Any]:
    if path is not None:
        p = Path(path)
    else:
        sel = selector if selector is not None else load_active_selector()
        p = resolve_manifest_path(sel, out_dir=out_dir)
    if not p.is_file():
        raise FileNotFoundError(f"activation manifest missing: {p}")
    body = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError("activation manifest must be a JSON object")
    return body


def verify_manifest_self_sha(manifest: Mapping[str, Any]) -> tuple[bool, str, str]:
    got = str(manifest.get("sha256") or "")
    calc = manifest_content_sha(manifest)
    return got == calc and bool(got), got, calc


def verify_runtime_inventory(
    manifest: Mapping[str, Any],
    *,
    native_root: Optional[Path] = None,
) -> dict[str, Any]:
    inv = manifest.get("runtime_file_sha256") or {}
    if not isinstance(inv, dict) or not inv:
        return {
            "ok": False,
            "reason": "runtime_file_sha256_missing",
            "expected_n": 0,
            "matched": 0,
            "mismatches": [],
        }
    # Reject selector/pins self-reference inside inventory
    for rel in inv:
        name = Path(str(rel)).name
        if name in FORBIDDEN_INVENTORY_NAMES:
            return {
                "ok": False,
                "reason": f"forbidden_inventory_entry:{rel}",
                "expected_n": len(inv),
                "matched": 0,
                "mismatches": [str(rel)],
            }
    root = Path(native_root or NATIVE)
    mismatches: list[dict[str, str]] = []
    matched = 0
    for rel, exp in inv.items():
        rel_n = str(rel).replace("\\", "/")
        p = root / rel_n
        if not p.is_file():
            mismatches.append({"path": rel_n, "reason": "missing", "expected": str(exp), "got": ""})
            continue
        got = file_sha256(p)
        if got != str(exp):
            mismatches.append(
                {"path": rel_n, "reason": "hash_mismatch", "expected": str(exp), "got": got}
            )
        else:
            matched += 1
    return {
        "ok": len(mismatches) == 0 and matched == len(inv),
        "reason": "" if not mismatches else "runtime_inventory_mismatch",
        "expected_n": len(inv),
        "matched": matched,
        "mismatches": mismatches,
    }


def verify_selector_binding(
    selector: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, bool]:
    mid = str(manifest.get("manifest_id") or manifest.get("activation_id") or "")
    return {
        "activation_id_match": str(selector.get("activation_id") or "") == mid,
        "activation_sha_match": str(selector.get("activation_sha") or "")
        == str(manifest.get("sha256") or ""),
    }
