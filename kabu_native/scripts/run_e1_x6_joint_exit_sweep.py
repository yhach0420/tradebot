"""Full-canonical Joint EXIT-variant sweep on locked Stage-1 ENTRY (research-only)."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
REPO = NATIVE.parent
for p in (str(SRC), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> int:
    from research.e1_x6_provisional.analysis_mask import build_mask_index
    from research.e1_x6_provisional.constants import ARTIFACT_DIR_REL, DAYS, PLAN_REL
    from research.e1_x6_provisional.exit_param_override import exit_param_override
    from research.e1_x6_provisional.joint_strategy import EXIT_FAMILIES, strategy_id
    from research.e1_x6_provisional.joint_validate import evaluate_joint_gates
    from research.e1_x6_provisional.p0_manifest import build_source_manifest
    from research.e1_x6_provisional.p2_execute import replay_candidate_day_partitions
    from research.e1_x6_provisional.util import (
        JST,
        norm_cache_dir,
        progress,
        repo_root,
        sha256_file,
        sha256_obj,
        temp_work_root,
        write_json,
    )
    import re

    root = repo_root()
    plan_path = root / PLAN_REL
    plan_sha = sha256_file(plan_path)
    text = plan_path.read_text(encoding="utf-8")
    ver = re.search(r"\|\s*Version\s*\|\s*`?([^`|]+)`?\s*\|", text)
    plan_version = ver.group(1).strip().strip("`") if ver else None
    if plan_version != "2.0":
        print("FAIL plan", plan_version)
        return 2

    art = root / ARTIFACT_DIR_REL
    rep = json.loads((art / "report.json").read_text(encoding="utf-8"))
    fc = (rep.get("entry_robustness") or {}).get("final_candidate") or {}
    entry = {
        "candidate_id": fc.get("candidate_id"),
        "family": fc.get("family"),
        "features": fc.get("features"),
        "direction": fc.get("direction"),
        "thresholds": fc.get("thresholds"),
        "threshold_code": fc.get("threshold_code"),
        "selection_basis": fc.get("selection_basis"),
    }
    stage1_work = Path(rep.get("temp_work") or "")
    run_id = f"e1x6_joint_exit_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}"
    work = temp_work_root(run_id)
    work.mkdir(parents=True, exist_ok=True)
    progress(f"JOINT EXIT sweep start run={run_id} entry={entry['candidate_id']}")

    m = build_source_manifest(final=True)
    mi = build_mask_index(m)
    cdir = norm_cache_dir()

    fold_pnls = {
        fid: float((fr.get("confirm_portfolio") or {}).get("pnl") or 0)
        for fid, fr in (rep.get("folds") or {}).items()
    }
    lodo_rows = ((rep.get("entry_robustness") or {}).get("refit_lodo") or {}).get("rows") or []
    lodo_pnls = {str(r.get("held_out_day")): float(r.get("held_out_pnl") or 0) for r in lodo_rows}
    # For EXIT variants, fold/LODO with X5_FROZEN are NOT valid evidence — mark fail unless re-run
    # Day gates can still be evaluated from fresh 9-day ledger.

    results = []
    for xf in EXIT_FAMILIES:
        xid = xf["exit_family_id"]
        sid = strategy_id(entry, xf)
        progress(f"JOINT EXIT replay strategy={sid}")
        with exit_param_override(xf):
            merged = replay_candidate_day_partitions(
                list(DAYS),
                entry,
                mi,
                cache_dir=cdir,
                banner="JOINT_STRATEGY_RESEARCH_NOT_FORWARD",
            )
        trades = merged["completed_trades"]
        write_json(work / f"trades_{xid}.json", trades)
        # Fold/LODO not re-run for variant → force those gates False via empty maps
        gates = evaluate_joint_gates(
            trades=trades,
            fold_confirm_pnls={} if xid != "X5_FROZEN" else fold_pnls,
            lodo_held_out_pnls={} if xid != "X5_FROZEN" else lodo_pnls,
            ex722_pnl=float(
                sum(float(t.get("net_pnl_yen_100") or 0) for t in trades if str(t.get("day")) != "20260722")
            ),
            ex722_pf=None,
            core_valid_n_windows=0,
            ab_match=False,  # single pass only this sweep
            report_xlsx_match=False,
            invalid_source_n=0,
            family_direction_flip=False,
            base_dd=None,
            cand_dd=merged["metrics"].get("max_dd"),
            base_stop_loss=None,
            cand_stop_loss=None,
        )
        # recompute ex722 pf
        from research.e1_x6_provisional.util import summarize_pnls

        ex_trades = [t for t in trades if str(t.get("day")) != "20260722"]
        ex_m = summarize_pnls([float(t["net_pnl_yen_100"]) for t in ex_trades]) if ex_trades else {}
        gates2 = evaluate_joint_gates(
            trades=trades,
            fold_confirm_pnls={} if xid != "X5_FROZEN" else fold_pnls,
            lodo_held_out_pnls={} if xid != "X5_FROZEN" else lodo_pnls,
            ex722_pnl=float(ex_m.get("pnl") or 0),
            ex722_pf=ex_m.get("pf"),
            core_valid_n_windows=0,
            ab_match=False,
            report_xlsx_match=False,
            invalid_source_n=0,
            family_direction_flip=False,
            base_dd=None,
            cand_dd=merged["metrics"].get("max_dd"),
            base_stop_loss=None,
            cand_stop_loss=None,
        )
        row = {
            "strategy_id": sid,
            "exit_family_id": xid,
            "n": merged["metrics"]["n"],
            "pnl": merged["metrics"]["pnl"],
            "pf": merged["metrics"]["pf"],
            "day_breakdown": gates2["day_breakdown"],
            "gates": gates2["gates"],
            "failed_gates": [k for k, v in gates2["gates"].items() if not v],
            "trade_ledger_sha256": sha256_obj(trades),
            "evaluation_mode": "FULL_CANONICAL_EVENT_REPLAY",
        }
        results.append(row)
        progress(f"JOINT EXIT done {xid} n={row['n']} pnl={row['pnl']} failed={row['failed_gates'][:5]}")

    any_pass = any(not r["failed_gates"] for r in results)
    out = {
        "joint_exit_sweep_id": run_id,
        "plan_version": plan_version,
        "plan_sha256": plan_sha,
        "entry_candidate_id": entry["candidate_id"],
        "results": results,
        "verdict": "E1_X6_JOINT_RESEARCH_SPEC_FROZEN_FOR_FORWARD_TEST"
        if any_pass
        else "E1_X6_NO_ROBUST_JOINT_STRATEGY",
        "note": "EXIT variants: fold/LODO/A/B not re-executed in this sweep → those gates fail by contract",
        "safety": {"submit": 0, "cancel": 0, "live": 0},
        "shadow_enabled": False,
        "generated_at_jst": datetime.now(JST).isoformat(),
    }
    write_json(work / "joint_exit_sweep.json", out)
    write_json(art / f"joint_exit_sweep_{run_id}.json", out)
    print(json.dumps({k: out[k] for k in out if k != "results"}, ensure_ascii=False, indent=2))
    for r in results:
        print(r["exit_family_id"], "failed", r["failed_gates"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
