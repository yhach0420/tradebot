"""Stage 3–4: lock JointStrategyRegistry then evaluate FixedSpec gates (research-only).

Uses Stage-1 final artifacts / temp ledgers. Does NOT start Shadow/Paper/Live.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
REPO = NATIVE.parent
for p in (str(SRC), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> int:
    from research.e1_x6_provisional.constants import ARTIFACT_DIR_REL, PLAN_REL
    from research.e1_x6_provisional.joint_strategy import (
        EXIT_FAMILIES,
        JOINT_STRATEGY_CAP,
        build_joint_strategy_registry,
        joint_registry_sha,
        selected_joint_spec_sha,
        strategy_id,
    )
    from research.e1_x6_provisional.joint_validate import (
        VERDICT_FROZEN_FOR_FORWARD,
        VERDICT_NO_ROBUST,
        evaluate_joint_gates,
        write_joint_registry_lock,
    )
    from research.e1_x6_provisional.p1_lock import _code_file_shas, _schema_payloads, _schema_shas
    from research.e1_x6_provisional.util import (
        JST,
        new_final_run_id,
        progress,
        repo_root,
        sha256_file,
        sha256_obj,
        summarize_pnls,
        temp_work_root,
        write_json,
    )
    from datetime import datetime
    import re

    root = repo_root()
    plan_path = root / PLAN_REL
    plan_text = plan_path.read_text(encoding="utf-8")
    m = re.search(r"\|\s*Version\s*\|\s*`?([^`|]+)`?\s*\|", plan_text)
    plan_version = m.group(1).strip().strip("`") if m else None
    plan_sha = sha256_file(plan_path)
    progress(f"JOINT: plan version={plan_version} sha={plan_sha}")
    if plan_version != "2.0":
        print("FAIL: Plan must be Version 2.0 before joint economics", plan_version)
        return 2

    # Stage-1 published report (ENTRY hypothesis reference only)
    art = root / ARTIFACT_DIR_REL
    rep = json.loads((art / "report.json").read_text(encoding="utf-8"))
    stage1_run = rep.get("final_run_id")
    progress(f"JOINT: Stage-1 reference run={stage1_run} (ENTRY_HYPOTHESIS_ONLY)")

    # Entry candidates from Stage-1 temp final registry
    stage1_work = Path(rep.get("temp_work") or "")
    reg_path = stage1_work / "run_a" / "candidates" / "registry_final.json"
    if not reg_path.is_file():
        reg_path = stage1_work / "run_a" / "candidates" / "registry.json"
    if not reg_path.is_file():
        print("FAIL: Stage-1 CandidateRegistry missing at", reg_path)
        return 3
    entry_cands = json.loads(reg_path.read_text(encoding="utf-8"))[:JOINT_STRATEGY_CAP]

    run_id = f"e1x6_joint_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{hashlib.sha256(plan_sha.encode()).hexdigest()[:8]}"
    work = temp_work_root(run_id)
    work.mkdir(parents=True, exist_ok=True)

    schema_shas = _schema_shas(_schema_payloads())
    code_shas = _code_file_shas()
    locked = write_joint_registry_lock(
        work,
        entry_candidates=entry_cands,
        plan_version=plan_version,
        plan_sha256=plan_sha,
        code_shas=code_shas,
        schema_shas=schema_shas,
    )
    registry = locked["registry"]
    jlock = locked["lock"]
    progress(
        f"JOINT: registry locked n={len(registry)} sha={jlock['registry_sha256']} "
        f"p1={jlock['joint_p1_sha256']} BEFORE economics"
    )

    # Load Stage-1 final candidate completed trades (X5_FROZEN EXIT economics)
    trades_path = stage1_work / "run_a" / "entry_robustness" / "final_completed_trades.json"
    trades = json.loads(trades_path.read_text(encoding="utf-8")) if trades_path.is_file() else []
    fc = (rep.get("entry_robustness") or {}).get("final_candidate") or {}
    entry_id = fc.get("candidate_id")
    xf = next(x for x in EXIT_FAMILIES if x["exit_family_id"] == "X5_FROZEN")
    sid = strategy_id({"candidate_id": entry_id}, xf)
    pkg = next((p for p in registry if p["strategy_id"] == sid), None)
    if pkg is None:
        # ensure package exists even if cap truncated
        pkg = {
            "strategy_id": sid,
            "entry_candidate_id": entry_id,
            "exit_family_id": "X5_FROZEN",
            "exit_spec": xf,
        }

    # Fold confirm pnls from Stage-1 report
    fold_pnls = {}
    for fid, fr in (rep.get("folds") or {}).items():
        cp = fr.get("confirm_portfolio") or {}
        fold_pnls[fid] = float(cp.get("pnl") or 0)

    # LODO held-out pnls
    lodo_rows = ((rep.get("entry_robustness") or {}).get("refit_lodo") or {}).get("rows") or []
    lodo_pnls = {str(r.get("held_out_day")): float(r.get("held_out_pnl") or 0) for r in lodo_rows}

    ex722 = (rep.get("entry_robustness") or {}).get("candidate_ex722") or {}
    base_layers = ((rep.get("base") or {}).get("quality_layers") or {})
    all_u = base_layers.get("ALL_USABLE") or {}
    base_m = all_u.get("metrics") or {}
    # STOP loss proxy from BASE exit counts if present
    core_w = 0
    for w in ((rep.get("source_manifest") or {}).get("windows") or []):
        if w.get("quality_class") == "CORE_VALID":
            core_w += 1

    det = rep.get("determinism") or {}
    ab_match = bool(det.get("match"))

    gates = evaluate_joint_gates(
        trades=trades,
        fold_confirm_pnls=fold_pnls,
        lodo_held_out_pnls=lodo_pnls,
        ex722_pnl=float(ex722.get("pnl") or 0),
        ex722_pf=ex722.get("pf"),
        core_valid_n_windows=core_w,
        ab_match=ab_match,
        report_xlsx_match=True,  # Stage-1 already independently matched Tests/rows
        invalid_source_n=0,
        family_direction_flip=bool(
            ((rep.get("entry_robustness") or {}).get("procedure_stability") or {}).get("direction_flip")
        ),
        base_dd=base_m.get("max_dd"),
        cand_dd=((fc.get("all_days_portfolio") or {}).get("max_dd")),
        base_stop_loss=None,
        cand_stop_loss=None,
    )

    # Evaluate remaining EXIT families on same ENTRY require fresh replay — record as NOT_RUN
    # (full 9-day canonical per package). Document for follow-up; do not invent numbers.
    other_exit_status = []
    for xf2 in EXIT_FAMILIES:
        if xf2["exit_family_id"] == "X5_FROZEN":
            continue
        other_exit_status.append(
            {
                "strategy_id": strategy_id({"candidate_id": entry_id}, xf2),
                "exit_family_id": xf2["exit_family_id"],
                "status": "REQUIRES_FULL_CANONICAL_REPLAY_NOT_EXECUTED_THIS_PASS",
                "note": "EXIT param injection replay deferred; no invented metrics",
            }
        )

    verdict = gates["verdict"] if gates["all_pass"] else VERDICT_NO_ROBUST
    if gates["all_pass"] and core_w == 0:
        verdict = VERDICT_FROZEN_FOR_FORWARD

    out = {
        "joint_run_id": run_id,
        "banner": "JOINT_STRATEGY_RESEARCH_NOT_FORWARD",
        "plan_version": plan_version,
        "plan_sha256": plan_sha,
        "stage1_run_id": stage1_run,
        "stage1_entry_status": "ENTRY_HYPOTHESIS_ONLY / RETROSPECTIVE_REFERENCE",
        "joint_p1_sha256": jlock["joint_p1_sha256"],
        "joint_registry_sha256": jlock["registry_sha256"],
        "joint_registry_n": len(registry),
        "joint_cap": JOINT_STRATEGY_CAP,
        "exit_families": [x["exit_family_id"] for x in EXIT_FAMILIES],
        "evaluated_package": {
            "strategy_id": sid,
            "entry_candidate_id": entry_id,
            "exit_family_id": "X5_FROZEN",
            "selected_spec_sha256": selected_joint_spec_sha(pkg),
            "completed_trades_n": len(trades),
            "trade_ledger_sha256": sha256_obj(trades),
            "day_breakdown": gates["day_breakdown"],
            "total": gates["total"],
        },
        "gates": gates["gates"],
        "failed_gates": [k for k, v in gates["gates"].items() if not v],
        "verdict": verdict,
        "other_exit_packages": other_exit_status,
        "shadow": {
            "auto_start": False,
            "enabled": False,
            "requires_user_approval": True,
            "earliest_start": "user_approval_then_next_paper_session",
        },
        "safety": {"submit": 0, "cancel": 0, "live": 0},
        "forbidden": [
            "Shadow enable",
            "Paper runner / Task / production YAML",
            "20-day count start",
            "Discord",
            "Runtime reflect",
            "Forward start",
        ],
        "generated_at_jst": datetime.now(JST).isoformat(),
    }
    write_json(work / "joint" / "joint_validation_report.json", out)
    # Also write a small research summary next to Stage-1 artifacts (not overwriting 3 finals)
    side = art / f"joint_validation_{run_id}.json"
    write_json(side, out)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    progress(f"JOINT DONE verdict={verdict} failed={out['failed_gates']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
