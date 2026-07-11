# ADR-687W7A2: W4S Session Seal Propagation Integrity

- **Status:** Accepted (propagation / finalize order — strategy & restore unchanged)
- **Date:** 2026-07-11
- **Evidence:** `results/reports/phase687w7a2_w4s_seal_propagation/`

## Context

W7A wrote soak snapshot **before** full session seal, so snapshot could show
`session_seal_entry_count=0` while `session_seal.json` had `entry_count=14`.
Field presence alone could look like success.

## Decision

### Source of Truth

`session_seal.json` (full seal build) is the only SoT for:

- session_seal_status, entry_count, required_count, missing count
- verified, generated_at, schema_version, manifest_sha256
- post_seal_mutation_detected

Snapshot must **copy real values** after seal verify.

### Finalize order (no circular invalidation)

1. Canonical / NP / Safety artifacts finalize
2. Pre-seal soak snapshot (seal fields = NOT_GENERATED / zeros)
3. Session manifest update (including disk_usage_end) **before** seal
4. Full session seal at session root
5. Seal verify
6. Propagate seal SoT into soak snapshot; re-save
7. Record `final_snapshot_sha256` on **session_seal only** (not rewrite sealed manifest)

Mutation detection accepts pre-seal **or** final snapshot hash for
`soak_session_snapshot.json` when `seal_metadata_overlay_applied=true`.

### W4S success

Reject when entry/required are 0, unequal, status ≠ SEALED_VALID, unverified,
missing files, post-seal mutation, schema/hash mismatch, or snapshot≠seal.

Classifications: SEAL_PROPAGATION_OK / SEAL_NOT_GENERATED / SEAL_INCOMPLETE /
SEAL_SNAPSHOT_MISMATCH / SEAL_HASH_MISMATCH / SEAL_MUTATED_AFTER_FINALIZE.

## Consequences

- Module: `src/small_paper/w4s_seal_propagation.py`
- Pilot uses `finalize_session_seal_propagation`
- PRODUCTION ORDER ENABLEMENT remains NOT AUTHORIZED / NOT IMPLEMENTED
