"""Decide CASE A/B/C/E for P1 source forensic recovery."""
from __future__ import annotations

from typing import Any

from research.v1r_p1_source_forensic_recovery_v1.spec import EXPECTED_DUAL_SHA, EXPECTED_NATIVE_SHA


def decide(
    *,
    exact_native: bool,
    exact_dual: bool,
    material_n: int,
    calib: dict[str, Any],
    leak_ok: bool,
) -> dict[str, Any]:
    if not leak_ok:
        return _pack(
            "E",
            "FORENSIC_RECOVERY_INTEGRITY_FAILED",
            "STOP. Isolation leak. Do not interpret recovery.",
            mode="FORENSIC_RECOVERY_INTEGRITY_FAILED",
            ext=False,
            new_family=False,
            sizing=False,
        )
    if exact_native and exact_dual:
        return _pack(
            "A",
            "V1R_P1_BYTE_EXACT_SOURCE_RECOVERED",
            "NEXT: resume frozen P1 V1R extension replay on isolated recovered_source. Do not run extension economics in this run.",
            mode="BYTE_EXACT",
            ext=True,
            new_family=False,
            sizing=False,
        )
    equiv = bool(calib.get("ran") and calib.get("BEHAVIORAL_EQUIVALENCE"))
    if equiv and int(material_n) == 0:
        return _pack(
            "B",
            "V1R_P1_BEHAVIORAL_RECONSTRUCTION_ACCEPTED",
            "NEXT: freeze reconstructed implementation identity, then run 20260824-20260902 extension in a later run. Not this run.",
            mode="P1_BEHAVIORAL_RECONSTRUCTION_ACCEPTED",
            ext=True,
            new_family=False,
            sizing=False,
            byte_identical=False,
            behaviorally_identical=True,
        )
    return _pack(
        "C",
        "V1R_P1_IMPLEMENTATION_RECOVERY_EXHAUSTED",
        "NEXT: V1R 8/24-9/2 extension cannot run as canonical P1. Reconsider NEW ENTRY FAMILY DESIGN in a later phase. Do not start it in this run. Do not size.",
        mode="P1_IMPLEMENTATION_RECOVERY_EXHAUSTED",
        ext=False,
        new_family=True,
        sizing=False,
        byte_identical=False,
        behaviorally_identical=False,
    )


def _pack(
    case: str,
    verdict: str,
    nxt: str,
    *,
    mode: str,
    ext: bool,
    new_family: bool,
    sizing: bool,
    byte_identical: bool | None = None,
    behaviorally_identical: bool | None = None,
) -> dict[str, Any]:
    if byte_identical is None:
        byte_identical = case == "A"
    if behaviorally_identical is None:
        behaviorally_identical = case in {"A", "B"}
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "RECOVERY_MODE": mode,
        "BYTE_IDENTICAL": bool(byte_identical),
        "BEHAVIORALLY_IDENTICAL_ON_P1_FULL14": bool(behaviorally_identical) if case != "C" else False,
        "extension_replay_allowed": bool(ext),
        "NEW_ENTRY_FAMILY_DESIGN_ALLOWED": bool(new_family) and case == "C",
        "SIZING_RESEARCH_ALLOWED": bool(sizing),
        "EXPECTED_NATIVE_SHA": EXPECTED_NATIVE_SHA,
        "EXPECTED_DUAL_SHA": EXPECTED_DUAL_SHA,
        "MAIN_TREE_CHANGED": False,
        "RUNTIME_CHANGED": False,
        "FUTURE_DATA_USED": False,
        "MAX_RESEARCH_DATE": "20260902",
        "PROSPECTIVE_HARVEST_SUSPENDED": True,
        "TRUE_OOS": False,
        "CERTIFIED": False,
    }


def build_answers(report: dict[str, Any]) -> dict[str, str]:
    dec = dict(report.get("decision") or {})
    searches = dict(report.get("searches") or {})
    calib = dict(report.get("calibration") or {})
    sem = dict(report.get("semantic_diff") or {})
    rec = dict(report.get("recovery") or {})
    proof = dict(report.get("existence_proof") or {})

    def s(key: str, field: str = "searched") -> str:
        body = dict(searches.get(key) or {})
        return (
            f"searched={body.get(field, body.get('searched'))} "
            f"candidate_n={body.get('candidate_n')} hash_match_n={body.get('hash_match_n')} "
            f"evidence={body.get('evidence')}"
        )

    return {
        "1": "Prior CASE E V1R_FROZEN_REPLAY_INTEGRITY_FAILED is correct. P1_SOURCE_UNRECOVERABLE was not declared permanently lost before this local forensic search.",
        "2": (
            "Source is not declared permanently lost until git reachable, reflog/stash, unreachable/dangling blobs, "
            "Cursor history/workspace/transcripts, project backups/content-ids/archives, temp/pyc, and recycle/version-copy are searched. "
            f"EXPECTED_BYTES_EXISTED={proof.get('EXPECTED_BYTES_EXISTED')} p1_match={proof.get('p1_match')} v26g7_match={proof.get('v26g7_match')}."
        ),
        "3": EXPECTED_NATIVE_SHA,
        "4": EXPECTED_DUAL_SHA,
        "5": s("git_reachable"),
        "6": s("git_reflog_stash"),
        "7": f"stash searched; {(searches.get('git_reflog_stash') or {}).get('stash_list') or 'empty'}",
        "8": s("git_fsck") + " | " + s("git_blobs"),
        "9": s("cursor_history") + " | " + s("cursor_workspace") + " | " + s("agent_transcripts"),
        "10": s("project_named") + " | " + s("content_ids") + " | " + s("archives"),
        "11": s("temp_recycle"),
        "12": s("pyc"),
        "13": s("temp_recycle"),
        "14": str((report.get("search_totals") or {}).get("candidate_file_n")),
        "15": str((report.get("search_totals") or {}).get("exact_sha_hit_n")),
        "16": str(bool(rec.get("BYTE_EXACT_RECOVERED"))).lower(),
        "17": str(rec.get("paths") or "none"),
        "18": str(rec.get("rehash") or "n/a"),
        "19": str(bool(sem.get("performed"))).lower(),
        "20": str(sem.get("material_n")),
        "21": str(bool(calib.get("ran"))).lower(),
        "22": f"{calib.get('trade_n_match_n')}/14",
        "23": f"{calib.get('ledger_sha_match_n')}/14",
        "24": f"{calib.get('pnl_match_n')}/14",
        "25": f"{calib.get('anchor_admission_match_n')}/14 identity={calib.get('identity_match_n')}/14 fill_exit={calib.get('fill_exit_match_n')}/14",
        "26": str(bool(calib.get("BEHAVIORAL_EQUIVALENCE"))).lower(),
        "27": str(dec.get("RECOVERY_MODE")),
        "28": f"{dec.get('VERDICT')} CASE {dec.get('CASE')}",
        "29": str(bool(dec.get("extension_replay_allowed"))).lower(),
        "30": str(bool(dec.get("NEW_ENTRY_FAMILY_DESIGN_ALLOWED"))).lower(),
        "31": str(bool(dec.get("SIZING_RESEARCH_ALLOWED"))).lower(),
        "32": "false",
        "33": "false",
        "34": "false",
        "35": "20260902",
        "36": "true",
        "37": "0/0/0",
        "38": str(dec.get("NEXT") or ""),
    }
