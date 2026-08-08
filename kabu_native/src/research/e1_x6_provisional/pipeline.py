"""Orchestrate P0 → P1 lock → P2 economics → determinism → publish."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from research.e1_x6_provisional.constants import (
    ARTIFACT_DIR_REL,
    FAILED_PLAN13_MASKCLIP_RUN,
    FINAL_BANNER,
    PLAN_REL,
    PROVISIONAL_BANNER,
    RUN_HISTORY_NOTES,
    SUPERSEDED_ANALYSIS_MASK_RUN,
    SUPERSEDED_FINAL_AUDIT_RUN,
    SUPERSEDED_PRIOR_RUN,
    SUPERSEDED_REPLAY_BOUNDARY_RUN,
    SUPERSEDED_SEMANTIC_CONTRACT_RUN,
)
from research.e1_x6_provisional.cost_contract import CostContractMismatch, verify_frozen_e1_x5_cost_contract
from research.e1_x6_provisional.p0_manifest import build_source_manifest
from research.e1_x6_provisional.p1_lock import build_p1_lock
from research.e1_x6_provisional.p2_execute import run_p2_pass
from research.e1_x6_provisional.publish import publish_artifacts
from research.e1_x6_provisional.util import (
    JST,
    new_final_run_id,
    new_run_id,
    progress,
    progress_log_path,
    repo_root,
    set_progress_mode,
    sha256_file,
    temp_work_root,
    write_json,
)

REQUIRED_PLAN_VERSION = "1.3"


def _confirm_plan_unique() -> dict[str, Any]:
    import re

    root = repo_root()
    plan = root / PLAN_REL
    found = list(root.rglob("e1_x6_validation_plan.md"))
    text = plan.read_text(encoding="utf-8") if plan.is_file() else ""
    version = None
    status = None
    m = re.search(r"\|\s*Version\s*\|\s*`?([^`|]+)`?\s*\|", text)
    if m:
        version = m.group(1).strip()
    m2 = re.search(r"\|\s*状態\s*\|\s*`?([^`|]+)`?\s*\|", text)
    if m2:
        status = m2.group(1).strip()
    return {
        "unique": len(found) == 1 and plan.is_file(),
        "paths": [str(p.relative_to(root)).replace("\\", "/") for p in found],
        "reference": str(PLAN_REL).replace("\\", "/"),
        "version_on_disk": version,
        "status_on_disk": status,
        "sha256": sha256_file(plan) if plan.is_file() else None,
        "version_required": REQUIRED_PLAN_VERSION,
        "version_ok": version == REQUIRED_PLAN_VERSION,
    }


SUPERSEDED_RUNS = [
    SUPERSEDED_PRIOR_RUN,
    SUPERSEDED_SEMANTIC_CONTRACT_RUN,
    SUPERSEDED_ANALYSIS_MASK_RUN,
    SUPERSEDED_REPLAY_BOUNDARY_RUN,
    SUPERSEDED_FINAL_AUDIT_RUN,
]

RUN_HISTORY = [
    FAILED_PLAN13_MASKCLIP_RUN,
    *RUN_HISTORY_NOTES,
    SUPERSEDED_FINAL_AUDIT_RUN,
]


def _safety_block() -> dict[str, int]:
    return {"submit": 0, "cancel": 0, "live": 0}


def _entry_rob_shas(pass_out: dict[str, Any]) -> dict[str, Any]:
    er = pass_out.get("entry_robustness") or {}
    fc = er.get("final_candidate") or {}
    folds = pass_out.get("folds") or {}
    fold_reg = {
        fid: fr.get("fold_registry_sha256")
        for fid, fr in folds.items()
        if isinstance(fr, dict) and fr.get("fold_registry_sha256")
    }
    return {
        "final_candidate_decision_ledger_sha256": fc.get("decision_ledger_sha256")
        or er.get("final_candidate_decision_ledger_sha256"),
        "final_candidate_trade_ledger_sha256": fc.get("completed_trade_ledger_sha256")
        or er.get("final_candidate_trade_ledger_sha256"),
        "lodo_sha256": (er.get("refit_lodo") or {}).get("sha256") or er.get("lodo_sha256"),
        "fixed_spec_sha256": (er.get("fixed_spec_day_deletion") or {}).get("sha256")
        or er.get("fixed_spec_sha256"),
        "fold_registry_shas": fold_reg,
        "final_censored_ledger_sha256": fc.get("censored_ledger_sha256"),
    }


def run_provisional_pipeline(
    *,
    run_id: Optional[str] = None,
    skip_pass_b_full: bool = False,
    resume_run_id: Optional[str] = None,
    allow_full_replay: bool = False,
) -> dict[str, Any]:
    """Full P0→P2. Blocked by default until after 7/31 Capture (allow_full_replay=True)."""
    from research.e1_x6_provisional.util import read_json

    set_progress_mode("provisional")
    if not allow_full_replay:
        raise RuntimeError(
            "FULL_REPLAY_BLOCKED_DURING_CAPTURE: refuse heavy provisional replay while 7/31 "
            "Capture/Paper must keep CPU/I/O. Pass allow_full_replay=True only after 7/31 PM seal "
            "for a new final run_id. Fixture tests remain available without this flag."
        )

    run_id = resume_run_id or run_id or new_run_id()
    work = temp_work_root(run_id)
    resume = bool(resume_run_id)
    if not resume:
        try:
            progress_log_path().write_text("", encoding="utf-8")
        except Exception:
            pass
    progress(f"START provisional_run_id={run_id} work={work} resume={resume}")

    try:
        cost_ok = verify_frozen_e1_x5_cost_contract()
    except CostContractMismatch as e:
        progress(f"BLOCKED COST_CONTRACT_MISMATCH: {e}")
        return {
            "provisional_run_id": run_id,
            "banner": PROVISIONAL_BANNER,
            "status": {"P0": "NOT_STARTED", "P1": "COST_CONTRACT_MISMATCH", "P2": "NOT_EXECUTED"},
            "blockers": ["COST_CONTRACT_MISMATCH"],
            "error": str(e),
            "superseded_runs": list(SUPERSEDED_RUNS),
            "safety": _safety_block(),
        }

    plan_info = _confirm_plan_unique()
    if not plan_info["unique"] or not plan_info.get("version_ok"):
        progress(f"WARN plan gate: {plan_info}")
        return {
            "provisional_run_id": run_id,
            "banner": PROVISIONAL_BANNER,
            "status": {"P0": "BLOCKED_PLAN_SOT", "P1": "NOT_LOCKED", "P2": "NOT_EXECUTED"},
            "blockers": ["PLAN_SOT_NOT_UNIQUE_OR_VERSION"]
            if not plan_info["unique"]
            else ["PLAN_VERSION_NOT_1_3"],
            "plan": plan_info,
            "superseded_runs": list(SUPERSEDED_RUNS),
            "safety": _safety_block(),
        }

    # --- P0 ---
    p0_path = work / "p0_source_manifest.json"
    if resume and p0_path.is_file():
        source_manifest = read_json(p0_path)
        progress("P0: resumed from disk")
    else:
        source_manifest = build_source_manifest(final=False)
        write_json(p0_path, source_manifest)
    p0_status = source_manifest.get("status") or "P0_COMPLETE_PROVISIONAL"

    # --- P1 MUST lock before economics ---
    p1_path = work / "p1_lock.json"
    if resume and p1_path.is_file():
        p1 = read_json(p1_path)
        progress(f"P1: resumed lock sha={p1.get('p1_lock_sha256')}")
    else:
        p1 = build_p1_lock(
            run_id=run_id,
            source_manifest_sha256=source_manifest["source_manifest_sha256"],
            analysis_mask_sha256=source_manifest.get("source_manifest_sha256"),
            plan_version=plan_info.get("version_on_disk"),
            plan_sha256=plan_info.get("sha256"),
        )
        write_json(p1_path, p1)
    p1_lock_sha = p1["p1_lock_sha256"]
    progress(f"GATE: p1_lock_sha256 saved BEFORE economics: {p1_lock_sha}")

    # --- P2 pass A ---
    pass_a = run_p2_pass(
        work,
        pass_name="run_a",
        resume=resume,
        source_manifest=source_manifest,
        banner=PROVISIONAL_BANNER,
        run_entry_robustness=False,
    )

    # --- P2 pass B (determinism) ---
    if skip_pass_b_full:
        progress("P2: skip_pass_b_full ignored; dual replay required for final")
    pass_b = run_p2_pass(
        work,
        pass_name="run_b",
        resume=False,
        source_manifest=source_manifest,
        banner=PROVISIONAL_BANNER,
        run_entry_robustness=False,
    )

    det = {
        "banner": PROVISIONAL_BANNER,
        "dataset_sha_a": pass_a["dataset"]["sha256"],
        "dataset_sha_b": pass_b["dataset"]["sha256"],
        "label_sha_a": pass_a["labels"]["sha256"],
        "label_sha_b": pass_b["labels"]["sha256"],
        "base_ledger_sha_a": pass_a["base"]["ALL_USABLE_ledger_sha256"],
        "base_ledger_sha_b": pass_b["base"]["ALL_USABLE_ledger_sha256"],
        "candidate_registry_sha_a": pass_a["candidates"]["registry_sha256"],
        "candidate_registry_sha_b": pass_b["candidates"]["registry_sha256"],
        "fold_sha_a": pass_a["fold_ledger_sha256"],
        "fold_sha_b": pass_b["fold_ledger_sha256"],
        "counters_a": pass_a["counters"],
        "counters_b": pass_b["counters"],
        "cost_contract": cost_ok,
    }
    det["match"] = all(
        [
            det["dataset_sha_a"] == det["dataset_sha_b"],
            det["label_sha_a"] == det["label_sha_b"],
            det["base_ledger_sha_a"] == det["base_ledger_sha_b"],
            det["candidate_registry_sha_a"] == det["candidate_registry_sha_b"],
            det["fold_sha_a"] == det["fold_sha_b"],
            det["counters_a"] == det["counters_b"],
        ]
    )
    det["status"] = "DETERMINISM_PASS" if det["match"] else "DETERMINISM_FAIL"

    blockers: list[str] = []
    if not plan_info["unique"]:
        blockers.append("PLAN_SOT_NOT_UNIQUE")
    if not det["match"]:
        blockers.append("DETERMINISM_MISMATCH")

    core = (pass_a["base"].get("CORE_VALID") or {})
    if core.get("status") == "NOT_EVALUABLE":
        evidence = "E1_X6_INSUFFICIENT_EVIDENCE"
    else:
        evidence = "EVIDENCE_PRESENT"

    p2_status = "P2_EXECUTED_PROVISIONAL" if det["match"] else "BLOCKED_DETERMINISM"

    report: dict[str, Any] = {
        "provisional_run_id": run_id,
        "banner": PROVISIONAL_BANNER,
        "PROVISIONAL_NOT_FOR_SELECTION": True,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "superseded_runs": list(SUPERSEDED_RUNS),
        "status": {
            "P0": p0_status,
            "P1": "P1_REVISED_LOCKED_PRE_ECONOMICS",
            "P2": p2_status,
            "evidence": evidence,
        },
        "plan": plan_info,
        "source_manifest": source_manifest,
        "p1": p1,
        "base": pass_a["base"],
        "dataset": pass_a["dataset"],
        "labels": pass_a["labels"],
        "candidates": pass_a["candidates"],
        "folds": pass_a["folds"],
        "determinism": det,
        "blockers": blockers,
        "safety": _safety_block(),
        "next_single_step": (
            "After 7/31 Capture seal: new final run_id — rebuild Source Manifest 7/21–31, "
            "new P1 lock (5bps contract verify), BASE, dataset/labels, candidate registry, "
            "F1–F5 portfolio confirm, dual replay, atomic publish. "
            "Do not append F5 onto superseded provisional runs. "
            "If CORE_VALID/support insufficient => E1_X6_INSUFFICIENT_EVIDENCE."
        ),
        "artifact_dir": str(ARTIFACT_DIR_REL).replace("\\", "/"),
        "temp_work": str(work),
        "progress_log": str(progress_log_path()),
    }
    write_json(work / "report_prepublish.json", report)
    if det["match"]:
        pub = publish_artifacts(report, run_id=run_id)
        report["published_paths"] = pub.get("paths")
        report["audit_sheet_counts"] = pub.get("audit_sheet_counts")
    else:
        report["published_paths"] = None
        report["publish_skipped"] = "DETERMINISM_MISMATCH"
    progress(f"DONE provisional_run_id={run_id} P2={p2_status} determinism={det['match']}")
    return report


def run_final_9day_pipeline(
    *,
    run_id: Optional[str] = None,
    resume_run_id: Optional[str] = None,
    allow_full_replay: bool = False,
    mode: str = "final",
) -> dict[str, Any]:
    """FINAL 9-day (20260721–20260731) research pipeline with dual A/B + robustness.

    Requires allow_full_replay=True. On A/B mismatch: do NOT publish;
    status E1_X6_SOURCE_BLOCKED. Does not start EXIT / Forward / Runtime.
    """
    from research.e1_x6_provisional.util import read_json

    if mode not in ("final", "FINAL"):
        raise ValueError("run_final_9day_pipeline requires mode='final'")

    set_progress_mode("final")
    if not allow_full_replay:
        raise RuntimeError(
            "FULL_REPLAY_BLOCKED: final 9-day pipeline requires allow_full_replay=True"
        )

    run_id = resume_run_id or run_id or new_final_run_id()
    work = temp_work_root(run_id)
    resume = bool(resume_run_id)
    if not resume:
        try:
            progress_log_path(final=True).write_text("", encoding="utf-8")
        except Exception:
            pass
    progress(f"START final_run_id={run_id} banner={FINAL_BANNER} work={work}")

    try:
        cost_ok = verify_frozen_e1_x5_cost_contract()
    except CostContractMismatch as e:
        progress(f"BLOCKED COST_CONTRACT_MISMATCH: {e}")
        return {
            "final_run_id": run_id,
            "run_id": run_id,
            "banner": FINAL_BANNER,
            "FINAL_9DAY_INTERNAL_RESEARCH_NOT_FORWARD": True,
            "status": {"P0": "NOT_STARTED", "P1": "COST_CONTRACT_MISMATCH", "P2": "NOT_EXECUTED"},
            "blockers": ["COST_CONTRACT_MISMATCH"],
            "error": str(e),
            "superseded_runs": list(SUPERSEDED_RUNS),
            "safety": _safety_block(),
            "verdict": None,
        }

    plan_info = _confirm_plan_unique()
    if not plan_info["unique"] or not plan_info.get("version_ok"):
        progress(f"BLOCKED plan Version 1.3 required before economics: {plan_info}")
        return {
            "final_run_id": run_id,
            "run_id": run_id,
            "banner": FINAL_BANNER,
            "FINAL_9DAY_INTERNAL_RESEARCH_NOT_FORWARD": True,
            "status": {"P0": "BLOCKED_PLAN_SOT", "P1": "NOT_LOCKED", "P2": "NOT_EXECUTED"},
            "blockers": ["PLAN_VERSION_1_3_REQUIRED"],
            "plan": plan_info,
            "superseded_runs": list(SUPERSEDED_RUNS),
            "safety": _safety_block(),
            "verdict": None,
        }

    # --- P0 FINAL ---
    p0_path = work / "p0_source_manifest.json"
    if resume and p0_path.is_file():
        source_manifest = read_json(p0_path)
        progress("P0: resumed from disk")
    else:
        source_manifest = build_source_manifest(
            banner=FINAL_BANNER, status="P0_FINAL_COMPLETE", final=True
        )
        write_json(p0_path, source_manifest)
    p0_status = source_manifest.get("status") or "P0_FINAL_COMPLETE"

    # --- P1 lock BEFORE economics ---
    p1_path = work / "p1_lock.json"
    if resume and p1_path.is_file():
        p1 = read_json(p1_path)
        progress(f"P1: resumed lock sha={p1.get('p1_lock_sha256')}")
    else:
        p1 = build_p1_lock(
            run_id=run_id,
            source_manifest_sha256=source_manifest["source_manifest_sha256"],
            analysis_mask_sha256=source_manifest.get("source_manifest_sha256"),
            plan_version=plan_info.get("version_on_disk"),
            plan_sha256=plan_info.get("sha256"),
        )
        p1["banner"] = FINAL_BANNER
        p1["final_run_id"] = run_id
        # re-hash after banner stamp
        from research.e1_x6_provisional.util import sha256_obj

        p1["p1_lock_sha256"] = sha256_obj({k: v for k, v in p1.items() if k != "p1_lock_sha256"})
        write_json(p1_path, p1)
    if p1.get("p1_precommit_status") == "P1_PRECOMMIT_INCOMPLETE":
        progress(f"BLOCKED P1_PRECOMMIT_INCOMPLETE: {p1.get('p1_precommit_missing')}")
        return {
            "final_run_id": run_id,
            "run_id": run_id,
            "banner": FINAL_BANNER,
            "FINAL_9DAY_INTERNAL_RESEARCH_NOT_FORWARD": True,
            "status": {"P0": p0_status, "P1": "P1_PRECOMMIT_INCOMPLETE", "P2": "NOT_EXECUTED"},
            "blockers": ["P1_PRECOMMIT_INCOMPLETE"],
            "p1": p1,
            "plan": plan_info,
            "superseded_runs": list(SUPERSEDED_RUNS),
            "safety": _safety_block(),
            "verdict": None,
        }
    progress(f"GATE: p1_lock_sha256 saved BEFORE economics: {p1['p1_lock_sha256']}")

    # --- Dual pass A/B (SEQUENTIAL: A then B; separate caches to avoid OOM on ~16GB) ---
    progress("P2: sequential pass A then B (separate norm caches under run_a / run_b)")
    pass_a = run_p2_pass(
        work,
        pass_name="run_a",
        resume=resume,
        source_manifest=source_manifest,
        banner=FINAL_BANNER,
        run_entry_robustness=True,
    )
    pass_b = run_p2_pass(
        work,
        pass_name="run_b",
        resume=False,
        source_manifest=source_manifest,
        banner=FINAL_BANNER,
        run_entry_robustness=True,
    )

    det = {
        "banner": FINAL_BANNER,
        "dataset_sha_a": pass_a["dataset"]["sha256"],
        "dataset_sha_b": pass_b["dataset"]["sha256"],
        "label_sha_a": pass_a["labels"]["sha256"],
        "label_sha_b": pass_b["labels"]["sha256"],
        "base_ledger_sha_a": pass_a["base"]["ALL_USABLE_ledger_sha256"],
        "base_ledger_sha_b": pass_b["base"]["ALL_USABLE_ledger_sha256"],
        "candidate_registry_sha_a": pass_a["candidates"]["registry_sha256"],
        "candidate_registry_sha_b": pass_b["candidates"]["registry_sha256"],
        "fold_sha_a": pass_a["fold_ledger_sha256"],
        "fold_sha_b": pass_b["fold_ledger_sha256"],
        "counters_a": pass_a["counters"],
        "counters_b": pass_b["counters"],
        "cost_contract": cost_ok,
    }
    er_a = _entry_rob_shas(pass_a)
    er_b = _entry_rob_shas(pass_b)
    det["final_candidate_decision_ledger_sha_a"] = er_a["final_candidate_decision_ledger_sha256"]
    det["final_candidate_decision_ledger_sha_b"] = er_b["final_candidate_decision_ledger_sha256"]
    det["final_candidate_trade_ledger_sha_a"] = er_a["final_candidate_trade_ledger_sha256"]
    det["final_candidate_trade_ledger_sha_b"] = er_b["final_candidate_trade_ledger_sha256"]
    det["lodo_sha_a"] = er_a["lodo_sha256"]
    det["lodo_sha_b"] = er_b["lodo_sha256"]
    det["fixed_spec_sha_a"] = er_a["fixed_spec_sha256"]
    det["fixed_spec_sha_b"] = er_b["fixed_spec_sha256"]
    det["fold_registry_shas_a"] = er_a["fold_registry_shas"]
    det["fold_registry_shas_b"] = er_b["fold_registry_shas"]
    det["final_censored_ledger_sha_a"] = er_a.get("final_censored_ledger_sha256")
    det["final_censored_ledger_sha_b"] = er_b.get("final_censored_ledger_sha256")
    det["ab_isolation"] = {
        "mode": "sequential_A_then_B",
        "separate_norm_caches": True,
        "work_roots": {"run_a": "temp/.../run_a", "run_b": "temp/.../run_b"},
        "note": "Prefer sequential full passes to avoid OOM on ~16GB machines (~12GB peak observed).",
    }
    det["match"] = all(
        [
            det["dataset_sha_a"] == det["dataset_sha_b"],
            det["label_sha_a"] == det["label_sha_b"],
            det["base_ledger_sha_a"] == det["base_ledger_sha_b"],
            det["candidate_registry_sha_a"] == det["candidate_registry_sha_b"],
            det["fold_sha_a"] == det["fold_sha_b"],
            det["counters_a"] == det["counters_b"],
            det["final_candidate_decision_ledger_sha_a"]
            == det["final_candidate_decision_ledger_sha_b"],
            det["final_candidate_trade_ledger_sha_a"] == det["final_candidate_trade_ledger_sha_b"],
            det["lodo_sha_a"] == det["lodo_sha_b"],
            det["fixed_spec_sha_a"] == det["fixed_spec_sha_b"],
            det["fold_registry_shas_a"] == det["fold_registry_shas_b"],
            det["final_censored_ledger_sha_a"] == det["final_censored_ledger_sha_b"],
        ]
    )
    det["status"] = "DETERMINISM_PASS" if det["match"] else "DETERMINISM_FAIL"

    entry_rob = pass_a.get("entry_robustness") or {}
    verdict = entry_rob.get("verdict")
    next_phase = entry_rob.get("NEXT_PHASE")

    blockers: list[str] = []
    if not det["match"]:
        blockers.append("DETERMINISM_MISMATCH")
        p2_status = "E1_X6_SOURCE_BLOCKED"
        overall = "E1_X6_SOURCE_BLOCKED"
    else:
        p2_status = "P2_FINAL_EXECUTED"
        overall = verdict or "P2_FINAL_EXECUTED"

    from research.e1_x6_provisional.fixture_suite import fixture_contract_suite

    fixture_tests = fixture_contract_suite()
    # Strip approximated / PROVISIONAL banners from final report surfaces
    if isinstance(entry_rob, dict):
        entry_rob = dict(entry_rob)
        entry_rob.pop(PROVISIONAL_BANNER, None)
        if entry_rob.get("banner") != FINAL_BANNER:
            entry_rob["banner"] = FINAL_BANNER

    report: dict[str, Any] = {
        "final_run_id": run_id,
        "run_id": run_id,
        "banner": FINAL_BANNER,
        "FINAL_9DAY_INTERNAL_RESEARCH_NOT_FORWARD": True,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "superseded_runs": list(SUPERSEDED_RUNS),
        "run_history": list(RUN_HISTORY),
        "status": {
            "P0": p0_status,
            "P1": "P1_REVISED_LOCKED_PRE_ECONOMICS",
            "P2": p2_status,
            "overall": overall,
            "verdict": verdict,
            "NEXT_PHASE": next_phase,
        },
        "plan": plan_info,
        "source_manifest": source_manifest,
        "p1": p1,
        "base": pass_a["base"],
        "dataset": pass_a["dataset"],
        "labels": pass_a["labels"],
        "candidates": pass_a["candidates"],
        "folds": pass_a["folds"],
        "entry_robustness": entry_rob,
        "verdict": verdict,
        "NEXT_PHASE": next_phase,
        "determinism": det,
        "blockers": blockers,
        "tests": fixture_tests,
        "safety": _safety_block(),
        "EXIT_REDESIGN_STARTED": False,
        "FORWARD_STARTED": False,
        "RUNTIME_STARTED": False,
        "next_single_step": (
            f"Verdict={verdict}. "
            + (
                "NEXT_PHASE=PHASE2_EXIT_REDESIGN (not started in this run)."
                if next_phase == "PHASE2_EXIT_REDESIGN"
                else "Do not start EXIT redesign / Forward / Runtime until ENTRY gates pass."
            )
        ),
        "artifact_dir": str(ARTIFACT_DIR_REL).replace("\\", "/"),
        "temp_work": str(work),
        "progress_log": str(progress_log_path(final=True)),
    }
    write_json(work / "report_prepublish.json", report)

    if not det["match"]:
        progress(f"BLOCKED publish: A/B mismatch => E1_X6_SOURCE_BLOCKED run_id={run_id}")
        report["published_paths"] = None
        report["publish_skipped"] = "E1_X6_SOURCE_BLOCKED"
        return report

    pub = publish_artifacts(report, run_id=run_id, banner=FINAL_BANNER)
    report["published_paths"] = pub.get("paths")
    report["audit_sheet_counts"] = pub.get("audit_sheet_counts")
    progress(f"DONE final_run_id={run_id} verdict={verdict} published=yes")
    return report


def run_pipeline(
    *,
    mode: str = "provisional",
    allow_full_replay: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Dispatch provisional vs final."""
    if mode == "final":
        return run_final_9day_pipeline(allow_full_replay=allow_full_replay, **kwargs)
    return run_provisional_pipeline(allow_full_replay=allow_full_replay, **kwargs)


if __name__ == "__main__":
    run_provisional_pipeline(allow_full_replay=False)
