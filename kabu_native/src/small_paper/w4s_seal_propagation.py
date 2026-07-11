"""Phase687W7A2 — W4S session seal → soak snapshot propagation integrity.

session_seal.json (full seal build result) is the Source of Truth for seal fields.
Snapshot must copy real values; field presence alone must not PASS.
Does not change strategy, restore, or order submit logic.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.stateful_journal_recovery import (
    REQUIRED_SEAL_ARTIFACTS,
    SCHEMA_VERSION,
    _sha256_file,
    detect_post_seal_mutation,
    write_full_session_seal,
)

JST = ZoneInfo("Asia/Tokyo")
SEAL_PROPAGATION_VERSION = "687W7A2.1"

SEAL_PROPAGATION_OK = "SEAL_PROPAGATION_OK"
SEAL_NOT_GENERATED = "SEAL_NOT_GENERATED"
SEAL_INCOMPLETE = "SEAL_INCOMPLETE"
SEAL_SNAPSHOT_MISMATCH = "SEAL_SNAPSHOT_MISMATCH"
SEAL_HASH_MISMATCH = "SEAL_HASH_MISMATCH"
SEAL_MUTATED_AFTER_FINALIZE = "SEAL_MUTATED_AFTER_FINALIZE"

SNAPSHOT_SEAL_FIELDS = (
    "session_seal_status",
    "session_seal_entry_count",
    "session_seal_required_count",
    "required_artifact_missing_count",
    "session_seal_verified",
    "session_seal_generated_at",
    "session_seal_schema_version",
    "session_seal_manifest_sha256",
    "post_seal_mutation_detected",
)


def _now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def compute_seal_manifest_sha256(seal: Mapping[str, Any]) -> str:
    """Stable hash over required entry digests (SoT integrity fingerprint)."""
    parts: list[str] = []
    for ent in seal.get("entries") or []:
        name = str(ent.get("canonical_name") or ent.get("relative_path") or "")
        digest = str(ent.get("sha256") or "")
        exists = "1" if ent.get("exists") else "0"
        parts.append(f"{name}|{exists}|{digest}")
    parts.sort()
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def enrich_seal_sot_fields(seal: MutableMapping[str, Any]) -> dict[str, Any]:
    """Ensure seal dict carries required_count / missing list / manifest hash."""
    required = list(REQUIRED_SEAL_ARTIFACTS)
    missing_from_entries = [
        str(e.get("canonical_name") or e.get("relative_path") or "")
        for e in (seal.get("entries") or [])
        if e.get("required", True) and not e.get("exists")
    ]
    seal["required_count"] = int(seal.get("required_count") or len(required))
    seal["missing_required"] = list(seal.get("missing_required") or missing_from_entries)
    if seal.get("required_artifact_missing_count") is None:
        seal["required_artifact_missing_count"] = len(seal["missing_required"])
    else:
        seal["required_artifact_missing_count"] = int(seal["required_artifact_missing_count"])
    if not seal.get("session_seal_manifest_sha256"):
        seal["session_seal_manifest_sha256"] = compute_seal_manifest_sha256(seal)
    seal.setdefault("manifest_sha256", seal["session_seal_manifest_sha256"])
    return dict(seal)


def extract_seal_sot(seal: Mapping[str, Any], *, verified: bool, post_mutation: bool) -> dict[str, Any]:
    """Copy seal SoT values for snapshot propagation (real values only)."""
    s = enrich_seal_sot_fields(dict(seal))
    status = str(s.get("session_seal_status") or "UNKNOWN")
    entry_count = int(s.get("entry_count") or 0)
    required_count = int(s.get("required_count") or len(REQUIRED_SEAL_ARTIFACTS))
    missing_n = int(s.get("required_artifact_missing_count") or len(s.get("missing_required") or []))
    return {
        "session_seal_status": status,
        "session_seal_entry_count": entry_count,
        "session_seal_required_count": required_count,
        "required_artifact_missing_count": missing_n,
        "session_seal_verified": bool(verified) and status == "SEALED_VALID" and not post_mutation,
        "session_seal_generated_at": str(s.get("generated_at") or ""),
        "session_seal_schema_version": str(s.get("schema_version") or ""),
        "session_seal_manifest_sha256": str(
            s.get("session_seal_manifest_sha256") or s.get("manifest_sha256") or ""
        ),
        "post_seal_mutation_detected": bool(post_mutation),
    }


def compare_seal_snapshot(
    snap: Mapping[str, Any],
    seal: Mapping[str, Any],
    *,
    verified: bool = True,
    post_mutation: bool = False,
) -> dict[str, Any]:
    """Cross-artifact validation: snapshot fields must equal seal SoT."""
    sot = extract_seal_sot(seal, verified=verified, post_mutation=post_mutation)
    checks: list[dict[str, Any]] = []

    def eq(name: str, actual: Any, expected: Any) -> None:
        checks.append({"field": name, "actual": actual, "expected": expected, "match": actual == expected})

    eq("session_seal_status", snap.get("session_seal_status"), sot["session_seal_status"])
    eq("session_seal_entry_count", int(snap.get("session_seal_entry_count") or 0), sot["session_seal_entry_count"])
    eq(
        "session_seal_required_count",
        int(snap.get("session_seal_required_count") or 0),
        sot["session_seal_required_count"],
    )
    eq(
        "required_artifact_missing_count",
        int(snap.get("required_artifact_missing_count") or 0),
        sot["required_artifact_missing_count"],
    )
    eq(
        "session_seal_schema_version",
        str(snap.get("session_seal_schema_version") or ""),
        sot["session_seal_schema_version"],
    )
    eq("session_seal_verified", bool(snap.get("session_seal_verified")), sot["session_seal_verified"])
    eq(
        "post_seal_mutation_detected",
        bool(snap.get("post_seal_mutation_detected")),
        sot["post_seal_mutation_detected"],
    )
    eq(
        "session_seal_manifest_sha256",
        str(snap.get("session_seal_manifest_sha256") or ""),
        sot["session_seal_manifest_sha256"],
    )
    eq(
        "session_seal_generated_at",
        str(snap.get("session_seal_generated_at") or ""),
        sot["session_seal_generated_at"],
    )

    mismatch = [c for c in checks if not c["match"]]
    return {
        "seal_propagation_version": SEAL_PROPAGATION_VERSION,
        "checks": checks,
        "mismatch_count": len(mismatch),
        "mismatches": mismatch,
        "seal_sot": sot,
        "pass": len(mismatch) == 0,
    }


def classify_seal_propagation(
    snap: Mapping[str, Any],
    seal: Optional[Mapping[str, Any]],
    *,
    verified: bool = False,
    post_mutation: bool = False,
) -> str:
    if seal is None or not seal:
        return SEAL_NOT_GENERATED
    if post_mutation:
        return SEAL_MUTATED_AFTER_FINALIZE
    status = str(seal.get("session_seal_status") or "")
    if status != "SEALED_VALID":
        return SEAL_INCOMPLETE
    if not verified:
        return SEAL_INCOMPLETE
    s = enrich_seal_sot_fields(dict(seal))
    snap_hash = str(snap.get("session_seal_manifest_sha256") or "")
    seal_hash = str(s.get("session_seal_manifest_sha256") or "")
    if snap_hash and seal_hash and snap_hash != seal_hash:
        return SEAL_HASH_MISMATCH
    cmp = compare_seal_snapshot(snap, seal, verified=verified, post_mutation=post_mutation)
    if cmp["mismatch_count"] > 0:
        return SEAL_SNAPSHOT_MISMATCH
    return SEAL_PROPAGATION_OK


def propagate_seal_fields_into_snapshot(
    snap: MutableMapping[str, Any],
    seal: Mapping[str, Any],
    *,
    verified: bool,
    post_mutation: bool,
) -> dict[str, Any]:
    """Copy SoT seal fields into snapshot (in-place)."""
    sot = extract_seal_sot(seal, verified=verified, post_mutation=post_mutation)
    for k, v in sot.items():
        snap[k] = v
    w7a = snap.get("w7a_recovery")
    if isinstance(w7a, dict):
        for k, v in sot.items():
            w7a[k] = v
    status = classify_seal_propagation(snap, seal, verified=verified, post_mutation=post_mutation)
    snap["seal_propagation_status"] = status
    snap["seal_propagation_version"] = SEAL_PROPAGATION_VERSION
    if isinstance(w7a, dict):
        w7a["seal_propagation_status"] = status
        w7a["seal_propagation_version"] = SEAL_PROPAGATION_VERSION
    return {
        "seal_propagation_status": status,
        "fields": sot,
        "comparison": compare_seal_snapshot(snap, seal, verified=verified, post_mutation=post_mutation),
    }


def w4s_seal_success_ok(snap: Mapping[str, Any], seal: Optional[Mapping[str, Any]] = None) -> bool:
    """W4S must not count session as success when seal/snapshot disagree or incomplete."""
    entry = int(snap.get("session_seal_entry_count") or 0)
    required = int(snap.get("session_seal_required_count") or 0)
    if entry == 0 or required == 0:
        return False
    if entry != required:
        return False
    if snap.get("session_seal_status") != "SEALED_VALID":
        return False
    if snap.get("session_seal_verified") is not True:
        return False
    if int(snap.get("required_artifact_missing_count") or 0) != 0:
        return False
    if snap.get("post_seal_mutation_detected") is not False:
        return False
    if not str(snap.get("session_seal_schema_version") or ""):
        return False
    if not str(snap.get("session_seal_manifest_sha256") or ""):
        return False
    if snap.get("seal_propagation_status") != SEAL_PROPAGATION_OK:
        return False
    if seal is not None:
        cmp = compare_seal_snapshot(
            snap,
            seal,
            verified=bool(snap.get("session_seal_verified")),
            post_mutation=bool(snap.get("post_seal_mutation_detected")),
        )
        if not cmp["pass"]:
            return False
    return True


def resolve_seal_path(session_root: Path, safety_dir: Optional[Path] = None) -> Optional[Path]:
    """Prefer session-root full seal (14 artifacts) over safety-subdir incomplete seal."""
    candidates: list[Path] = [session_root / "session_seal.json"]
    if safety_dir is not None:
        candidates.append(safety_dir / "session_seal.json")
    best: Optional[Path] = None
    best_n = -1
    for p in candidates:
        if not p.is_file():
            continue
        try:
            seal = json.loads(p.read_text(encoding="utf-8"))
            n = int(seal.get("entry_count") or 0)
            if n > best_n:
                best = p
                best_n = n
        except Exception:
            if best is None:
                best = p
    return best


def _snapshot_entry_rel(seal: Mapping[str, Any]) -> Optional[str]:
    for ent in seal.get("entries") or []:
        rel = str(ent.get("relative_path") or "")
        canon = str(ent.get("canonical_name") or "")
        if "soak_session_snapshot.json" in rel or canon == "soak_session_snapshot.json":
            return rel
    return None


def mark_pre_seal_snapshot_hash(seal: MutableMapping[str, Any], snapshot_path: Path) -> None:
    if snapshot_path.is_file():
        seal["pre_seal_snapshot_sha256"] = _sha256_file(snapshot_path)
    rel = _snapshot_entry_rel(seal)
    if rel:
        for ent in seal.get("entries") or []:
            if ent.get("relative_path") == rel or ent.get("canonical_name") == "soak_session_snapshot.json":
                seal["pre_seal_snapshot_sha256"] = ent.get("sha256") or seal.get("pre_seal_snapshot_sha256")
                break


def apply_final_snapshot_overlay(
    seal_path: Path,
    snapshot_path: Path,
    *,
    session_root: Path,
) -> dict[str, Any]:
    """Record final snapshot hash on seal only — do not rewrite sealed manifest bytes.

    Circular dependency avoidance:
    - Primary seal entry hashes remain pre-seal snapshot digests.
    - final_snapshot_sha256 lives on session_seal.json (not a sealed artifact rewrite).
    - Mutation detection accepts pre OR final hash for soak_session_snapshot only.
    - session_manifest.json must not be mutated after seal (disk/seal metadata applied pre-seal).
    """
    del session_root  # reserved for future separate finalization seal path
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    enrich_seal_sot_fields(seal)
    if not seal.get("pre_seal_snapshot_sha256"):
        mark_pre_seal_snapshot_hash(seal, snapshot_path)
    final_hash = _sha256_file(snapshot_path) if snapshot_path.is_file() else ""
    seal["final_snapshot_sha256"] = final_hash
    seal["seal_metadata_overlay_applied"] = True
    seal["seal_propagation_version"] = SEAL_PROPAGATION_VERSION
    seal["final_snapshot_hash_owner"] = "session_seal.final_snapshot_sha256"
    seal_path.write_text(json.dumps(seal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "pre_seal_snapshot_sha256": seal.get("pre_seal_snapshot_sha256"),
        "final_snapshot_sha256": final_hash,
        "seal_metadata_overlay_applied": True,
    }


def verify_seal_for_propagation(seal_path: Path, root: Path) -> dict[str, Any]:
    mut = detect_post_seal_mutation(seal_path, root)
    post = bool(mut.get("post_seal_mutation_detected"))
    seal = json.loads(seal_path.read_text(encoding="utf-8")) if seal_path.is_file() else {}
    status = str(seal.get("session_seal_status") or "")
    verified = (not post) and status == "SEALED_VALID" and int(seal.get("required_artifact_missing_count") or 0) == 0
    return {
        "verified": verified,
        "post_seal_mutation_detected": post,
        "mutation": mut,
        "seal": seal,
    }


def apply_seal_propagation_to_snapshot_file(
    snapshot_path: Path,
    seal_path: Path,
    *,
    session_root: Path,
) -> dict[str, Any]:
    """Load snapshot, copy seal SoT fields, re-save, record final overlay hashes."""
    if not seal_path.is_file():
        return {"seal_propagation_status": SEAL_NOT_GENERATED, "pass": False, "error": "seal_missing"}
    if not snapshot_path.is_file():
        return {"seal_propagation_status": SEAL_NOT_GENERATED, "pass": False, "error": "snapshot_missing"}

    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    enrich_seal_sot_fields(seal)
    if not seal.get("pre_seal_snapshot_sha256"):
        mark_pre_seal_snapshot_hash(seal, snapshot_path)
        seal_path.write_text(json.dumps(seal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    v = verify_seal_for_propagation(seal_path, session_root)
    snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    prop = propagate_seal_fields_into_snapshot(
        snap,
        v["seal"] if v.get("seal") else seal,
        verified=bool(v["verified"]),
        post_mutation=bool(v["post_seal_mutation_detected"]),
    )
    snapshot_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    apply_final_snapshot_overlay(seal_path, snapshot_path, session_root=session_root)

    v2 = verify_seal_for_propagation(seal_path, session_root)
    seal2 = json.loads(seal_path.read_text(encoding="utf-8"))
    snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    prop2 = propagate_seal_fields_into_snapshot(
        snap,
        seal2,
        verified=bool(v2["verified"]),
        post_mutation=bool(v2["post_seal_mutation_detected"]),
    )
    snapshot_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    apply_final_snapshot_overlay(seal_path, snapshot_path, session_root=session_root)

    snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    seal2 = json.loads(seal_path.read_text(encoding="utf-8"))
    cmp = compare_seal_snapshot(
        snap,
        seal2,
        verified=bool(snap.get("session_seal_verified")),
        post_mutation=bool(snap.get("post_seal_mutation_detected")),
    )
    status = classify_seal_propagation(
        snap,
        seal2,
        verified=bool(snap.get("session_seal_verified")),
        post_mutation=bool(snap.get("post_seal_mutation_detected")),
    )
    snap["seal_propagation_status"] = status
    snapshot_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    apply_final_snapshot_overlay(seal_path, snapshot_path, session_root=session_root)

    return {
        "seal_propagation_status": status,
        "comparison": cmp,
        "verified": bool(snap.get("session_seal_verified")),
        "w4s_seal_success_ok": w4s_seal_success_ok(snap, seal2),
        "snapshot": {k: snap.get(k) for k in SNAPSHOT_SEAL_FIELDS},
        "pass": status == SEAL_PROPAGATION_OK and cmp["pass"] and w4s_seal_success_ok(snap, seal2),
        "propagation": prop2 or prop,
    }


def finalize_session_seal_propagation(
    session_root: Path,
    *,
    safety_dir: Optional[Path] = None,
    session_id: str = "",
    skip_if_locked: bool = True,
) -> dict[str, Any]:
    """Canonical finalize after pre-seal snapshot + manifest update.

    Order: full seal → verify → propagate to snapshot → resave → record final hash.
    Duplicate finalize returns prior SEALED_VALID result unchanged.
    """
    safety = safety_dir or (session_root / "live_order_safety")
    snapshot_path = safety / "soak_session_snapshot.json"
    if not snapshot_path.is_file():
        snapshot_path = session_root / "soak_session_snapshot.json"

    seal_path = session_root / "session_seal.json"
    if skip_if_locked and seal_path.is_file():
        try:
            prev = json.loads(seal_path.read_text(encoding="utf-8"))
            if (
                prev.get("session_seal_status") == "SEALED_VALID"
                and prev.get("finalize_locked")
                and prev.get("seal_metadata_overlay_applied")
                and snapshot_path.is_file()
            ):
                snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
                cmp = compare_seal_snapshot(
                    snap,
                    prev,
                    verified=bool(snap.get("session_seal_verified")),
                    post_mutation=bool(snap.get("post_seal_mutation_detected")),
                )
                return {
                    "duplicate_finalize": True,
                    "seal_path": str(seal_path),
                    "seal_propagation_status": snap.get("seal_propagation_status") or SEAL_PROPAGATION_OK,
                    "comparison": cmp,
                    "pass": cmp["pass"] and w4s_seal_success_ok(snap, prev),
                    "snapshot": {k: snap.get(k) for k in SNAPSHOT_SEAL_FIELDS},
                }
        except Exception:
            pass

    write_full_session_seal(session_root, session_id=session_id, output_path=seal_path)
    if safety.is_dir() and safety.resolve() != session_root.resolve():
        try:
            write_full_session_seal(safety, session_id=session_id)
        except Exception:
            pass

    result = apply_seal_propagation_to_snapshot_file(
        snapshot_path,
        seal_path,
        session_root=session_root,
    )
    result["duplicate_finalize"] = False
    result["seal_path"] = str(seal_path)
    result["finalize_order"] = [
        "pre_seal_snapshot",
        "session_manifest_update",
        "full_session_seal",
        "seal_verify",
        "propagate_seal_to_snapshot",
        "resave_snapshot",
        "record_final_snapshot_hash_on_seal_and_manifest",
    ]
    return result


def run_negative_seal_mismatch_tests(
    *,
    good_snap: Mapping[str, Any],
    good_seal: Mapping[str, Any],
) -> dict[str, Any]:
    """Intentionally break snapshot/seal agreement; each case must FAIL success gate."""
    cases: list[dict[str, Any]] = []

    def one(label: str, mutate_snap=None) -> None:
        snap = dict(good_snap)
        seal = dict(good_seal)
        if mutate_snap:
            mutate_snap(snap)
        ok = w4s_seal_success_ok(snap, seal)
        cmp = compare_seal_snapshot(
            snap,
            seal,
            verified=bool(snap.get("session_seal_verified")),
            post_mutation=bool(snap.get("post_seal_mutation_detected")),
        )
        status = classify_seal_propagation(
            snap,
            seal,
            verified=bool(snap.get("session_seal_verified")),
            post_mutation=bool(snap.get("post_seal_mutation_detected")),
        )
        detected = ok is False
        cases.append(
            {
                "case": label,
                "w4s_seal_success_ok": ok,
                "comparison_pass": cmp["pass"],
                "seal_propagation_status": status,
                "detected_fail": detected,
                "mismatch_count": cmp["mismatch_count"],
            }
        )

    one("seal14_snapshot0", mutate_snap=lambda s: s.update(session_seal_entry_count=0, seal_propagation_status=SEAL_SNAPSHOT_MISMATCH))
    one("seal14_snapshot13", mutate_snap=lambda s: s.update(session_seal_entry_count=13, seal_propagation_status=SEAL_SNAPSHOT_MISMATCH))
    one("required_count_mismatch", mutate_snap=lambda s: s.update(session_seal_required_count=13, seal_propagation_status=SEAL_SNAPSHOT_MISMATCH))
    one("status_mismatch", mutate_snap=lambda s: s.update(session_seal_status="INCOMPLETE", seal_propagation_status=SEAL_SNAPSHOT_MISMATCH))
    one("missing_count_mismatch", mutate_snap=lambda s: s.update(required_artifact_missing_count=1, seal_propagation_status=SEAL_SNAPSHOT_MISMATCH))
    one("schema_mismatch", mutate_snap=lambda s: s.update(session_seal_schema_version="WRONG", seal_propagation_status=SEAL_SNAPSHOT_MISMATCH))
    one("hash_mismatch", mutate_snap=lambda s: s.update(session_seal_manifest_sha256="deadbeef" * 8, seal_propagation_status=SEAL_HASH_MISMATCH))
    one(
        "post_seal_mutation",
        mutate_snap=lambda s: s.update(
            post_seal_mutation_detected=True,
            session_seal_verified=False,
            seal_propagation_status=SEAL_MUTATED_AFTER_FINALIZE,
        ),
    )
    one("gate_entry_count_zero", mutate_snap=lambda s: s.update(session_seal_entry_count=0, seal_propagation_status=SEAL_INCOMPLETE))
    one("gate_required_zero", mutate_snap=lambda s: s.update(session_seal_required_count=0, seal_propagation_status=SEAL_INCOMPLETE))

    all_detected = all(c["detected_fail"] for c in cases)
    return {
        "seal_propagation_version": SEAL_PROPAGATION_VERSION,
        "cases": cases,
        "all_detected_fail": all_detected,
        "pass": all_detected,
    }


def build_synthetic_full_seal_session(root: Path, *, session_id: str = "W7A2") -> dict[str, Any]:
    """Create 14 required artifacts + pre-seal snapshot, then run finalize propagation."""
    root.mkdir(parents=True, exist_ok=True)
    safety = root / "live_order_safety"
    safety.mkdir(parents=True, exist_ok=True)

    for name in REQUIRED_SEAL_ARTIFACTS:
        if name == "soak_session_snapshot.json":
            continue
        if name == "session_manifest.json" or name.startswith(
            ("order_", "capital_", "broker_", "kill_")
        ):
            target = safety / name
        elif name.startswith("np_"):
            target = root / name
        else:
            target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".json"):
            target.write_text("{}\n", encoding="utf-8")
        else:
            target.write_text('{"row":1}\n', encoding="utf-8")

    pre = {
        "schema_version": SCHEMA_VERSION,
        "phase": "687W4S",
        "session_id": session_id,
        "session_seal_status": "MISSING",
        "session_seal_entry_count": 0,
        "session_seal_required_count": 0,
        "required_artifact_missing_count": 0,
        "session_seal_verified": False,
        "session_seal_generated_at": "",
        "session_seal_schema_version": "",
        "session_seal_manifest_sha256": "",
        "post_seal_mutation_detected": False,
        "seal_propagation_status": SEAL_NOT_GENERATED,
        "session_manifest_status": "COMPLETE",
        "journal_restore_status": "JOURNAL_OK",
        "recovery_assertion_failure_count": 0,
        "recovery_unexpected_object_count": 0,
        "recovery_expected_actual_match": True,
    }
    snap_path = safety / "soak_session_snapshot.json"
    snap_path.write_text(json.dumps(pre, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (safety / "session_manifest.json").write_text(
        json.dumps(
            {
                "sealed": True,
                "git_commit": "synthetic",
                "config_sha256": "abc",
                "session_seal_status": "SEALED",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = finalize_session_seal_propagation(root, safety_dir=safety, session_id=session_id)
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    seal = json.loads((root / "session_seal.json").read_text(encoding="utf-8"))
    return {
        "finalize": result,
        "snapshot": snap,
        "seal": seal,
        "entry_count": int(seal.get("entry_count") or 0),
        "required_count": int(seal.get("required_count") or len(REQUIRED_SEAL_ARTIFACTS)),
        "snapshot_entry_count": int(snap.get("session_seal_entry_count") or 0),
        "pass": bool(result.get("pass"))
        and int(snap.get("session_seal_entry_count") or 0) == 14
        and int(snap.get("session_seal_required_count") or 0) == 14,
    }
