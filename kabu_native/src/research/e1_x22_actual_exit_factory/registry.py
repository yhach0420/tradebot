"""Phase A: X21 registry load, status normalize, decision-mask aliases."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from research.e1_x21_entry_factory_exit_benchmark.factory import (
    build_single_candidates,
    build_two_feature_candidates,
    decision_mask,
    discovery_thresholds,
    feature_availability,
    load_population,
)

from . import EXPECTED_CAND_N, EXPECTED_POP_N, SOURCE_X21, STATUS_X21_TO_X22

NATIVE = Path(__file__).resolve().parents[3]
X21_DIR = NATIVE / "results" / "research" / "e1_x21_entry_factory_exit_benchmark"
OUT = NATIVE / "results" / "research" / "e1_x22_actual_exit_factory"


def load_x21_report() -> dict[str, Any]:
    r = json.loads((X21_DIR / "report.json").read_text(encoding="utf-8"))
    assert r["run_id"] == SOURCE_X21, f"expected {SOURCE_X21}, got {r['run_id']}"
    return r


def load_x21_registry() -> list[dict[str, Any]]:
    lines = (X21_DIR / "_cand_registry.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines if l.strip()]


def reconcile_registry(report: dict[str, Any], registry: list[dict[str, Any]]) -> dict[str, Any]:
    total = report.get("total_implemented_logic_count")
    reg_n = len(registry)
    ids = [c["candidate_id"] for c in registry]
    unique_ids = len(set(ids))
    impls = [c.get("implementation_id") for c in registry]
    unique_impls = len(set(impls))
    delta = total - reg_n if total is not None else None
    reason = None
    ok = True
    if total != EXPECTED_CAND_N or reg_n != EXPECTED_CAND_N:
        ok = False
        reason = "count_mismatch_vs_expected_8254"
    elif delta != 0:
        ok = False
        reason = "report_vs_registry_delta"
    elif unique_ids != reg_n:
        ok = False
        reason = "duplicate_candidate_ids"
    return {
        "ok": ok,
        "report_total": total,
        "registry_rows": reg_n,
        "unique_candidate_id_count": unique_ids,
        "unique_implementation_id_count": unique_impls,
        "delta_report_minus_registry": delta,
        "delta_reason": reason or "none",
        "expected": EXPECTED_CAND_N,
    }


def normalize_status(registry: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in registry:
        orig = c.get("status")
        out.append({
            **c,
            "x21_original_status": orig,
            "x22_normalized_status": STATUS_X21_TO_X22.get(orig, f"UNMAPPED_{orig}"),
        })
    return out


def rebuild_candidates_and_masks(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, np.ndarray]]:
    """Rebuild X21 candidate specs + decision masks (same factory)."""
    avail_info = feature_availability(rows)
    available = [r["feature_name"] for r in avail_info["registered"]]
    thr = discovery_thresholds(rows, available)
    singles = build_single_candidates(available, thr)
    masks: dict[str, np.ndarray] = {
        s["candidate_id"]: decision_mask(rows, s) for s in singles
    }
    twos = build_two_feature_candidates(singles, masks, rows)
    for t in twos:
        pa, pb = t["parents"]
        masks[t["candidate_id"]] = masks[pa] & masks[pb]
    all_cands = singles + twos
    assert len(all_cands) == EXPECTED_CAND_N, f"rebuilt {len(all_cands)} != {EXPECTED_CAND_N}"
    return all_cands, masks


def mask_sha256(mask: np.ndarray) -> str:
    # pack bits for stable hash
    packed = np.packbits(mask.astype(np.uint8))
    return hashlib.sha256(packed.tobytes()).hexdigest()


def build_alias_groups(
    candidates: list[dict[str, Any]],
    masks: dict[str, np.ndarray],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, np.ndarray]]:
    """
    Returns alias rows, map candidate_id -> representative_id, unique masks by rep id.
    """
    sha_to_rep: dict[str, str] = {}
    sha_to_members: dict[str, list[str]] = {}
    rows_out = []
    unique_masks: dict[str, np.ndarray] = {}

    for c in candidates:
        cid = c["candidate_id"]
        m = masks[cid]
        sha = mask_sha256(m)
        support = int(m.sum())
        if sha not in sha_to_rep:
            sha_to_rep[sha] = cid
            sha_to_members[sha] = [cid]
            unique_masks[cid] = m
            alias_group = f"ALIAS_{sha[:16]}"
            rows_out.append({
                "candidate_id": cid,
                "decision_mask_sha256": sha,
                "mask_support": support,
                "alias_group_id": alias_group,
                "alias_representative_id": cid,
                "is_representative": True,
            })
        else:
            rep = sha_to_rep[sha]
            sha_to_members[sha].append(cid)
            rows_out.append({
                "candidate_id": cid,
                "decision_mask_sha256": sha,
                "mask_support": support,
                "alias_group_id": f"ALIAS_{sha[:16]}",
                "alias_representative_id": rep,
                "is_representative": False,
            })

    cand_to_rep = {r["candidate_id"]: r["alias_representative_id"] for r in rows_out}
    return rows_out, cand_to_rep, unique_masks


def load_population_checked() -> list[dict[str, Any]]:
    rows = load_population()
    assert len(rows) == EXPECTED_POP_N
    return rows
