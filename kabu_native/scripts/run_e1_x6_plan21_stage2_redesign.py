"""Plan 2.1 Stage-2: ENTRY structural redesign sweep (after stage-1 passers=0).

Reuses the stage-1 oracle bundles (parity already proven against session replay,
all 17 partitions exact-match) — bundles are entry-agnostic BASE captures.

Order: P1 lock (stage-2, pre-economics) -> pooled feature quantiles -> fixed
enumeration -> registry lock into P1 -> A/B sweep -> Plan 2.1 gates -> verdict.

No Shadow / Runtime / Forward / Paper / Task / production YAML / Discord changes.
submit/cancel/live = 0/0/0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
for p in (str(NATIVE / "src"), str(NATIVE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402

STAGE1_RUN_ID_DEFAULT = "e1x6_p21_20260802_204337_49eabae8"
STAGE1_REGISTRY_SHA = "44ad006fe9928c9077cd12c0b69b8253e938b4101d55ac60e6a0bc85b8e2bb34"
STAGE1_VERDICT = "E1_X6_NO_ROBUST_JOINT_STRATEGY"


def work_root(run_id: str) -> Path:
    from research.e1_x6_provisional.oracle_capture import durable_store_root

    p = durable_store_root() / "plan21_work" / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def exit_params_from_family(xf: dict):
    from research.e1_x6_provisional.joint_oracle_replay import ExitParams

    tr = xf.get("trailing") or {}
    return ExitParams(
        stop_bps=float(xf["initial_stop_bps"]),
        target_bps=float(xf["target_bps"]),
        trail_arm_bps=float(tr.get("arm_bps")),
        giveback=float(tr.get("giveback")),
        max_hold_sec=float(xf["max_hold_sec"]),
        invalidation_score_drop=xf.get("_exec_invalidation_score_drop"),
        no_progress_sec=xf.get("_exec_no_progress_sec"),
        no_progress_mfe_bps=float(xf.get("_exec_no_progress_mfe_bps") or 0.0),
    )


def entry_mask_for(arrays: dict, cand: dict) -> np.ndarray:
    n = arrays["score"].shape[0]
    mask = np.ones(n, dtype=bool)
    for feat in cand["features"]:
        v = arrays[feat]
        thr = float(cand["thresholds"][feat])
        part = [p for p in str(cand["direction"]).split("&") if p.startswith(feat + ":")]
        direction = part[0].split(":", 1)[1] if part else "higher_better"
        with np.errstate(invalid="ignore"):
            mask &= (v >= thr) if direction == "higher_better" else (v <= thr)
    return mask


def bundle_files(stage1_run_id: str) -> list[Path]:
    from research.e1_x6_provisional.oracle_capture import durable_bundle_root

    files = sorted(durable_bundle_root(stage1_run_id).glob("*.pkl.gz"))
    if len(files) != 17:
        raise SystemExit(f"FAIL: expected 17 stage-1 bundles, found {len(files)}")
    return files


def phase_p1(run_id: str, work: Path, stage1_run_id: str) -> dict:
    from research.e1_x6_provisional.constants import DAYS, PLAN_REL
    from research.e1_x6_provisional.cost_contract import verify_frozen_e1_x5_cost_contract
    from research.e1_x6_provisional.day_robust_gates import (
        PLAN21_GATE_IDS,
        ROLLING_CONFIRM_DAYS,
        SELECTION_PRIORITY,
    )
    from research.e1_x6_provisional.p1_lock import _code_file_shas, _dependency_versions
    from research.e1_x6_provisional.redesign_features import (
        FEATURE_INVENTORY,
        FEATURES_UNAVAILABLE,
        STAGE2_ENTRY_CAP,
        STAGE2_FEATURE_GRIDS,
        STAGE2_GROUP_TEMPLATES,
        STAGE2_JOINT_CAP,
        stage2_exit_families,
    )
    from research.e1_x6_provisional.util import (
        progress,
        repo_root,
        sha256_file,
        sha256_obj,
        write_json,
    )

    existing = work / "p1_lock.json"
    plan_path = repo_root() / PLAN_REL
    plan_sha = sha256_file(plan_path)
    m = re.search(r"\|\s*Version\s*\|\s*`?([^`|]+)`?\s*\|", plan_path.read_text(encoding="utf-8"))
    if (m.group(1).strip().strip("`") if m else None) != "2.1":
        raise SystemExit("FAIL: Plan must be Version 2.1")
    if existing.is_file():
        p1_prev = json.loads(existing.read_text(encoding="utf-8"))
        if p1_prev.get("plan_sha256") != plan_sha:
            raise SystemExit("FAIL: plan SHA changed since stage-2 P1 lock; resume forbidden")
        progress(f"P21S2: P1 lock reused (resume) sha={p1_prev.get('p1_sha256')}")
        return p1_prev

    cost = verify_frozen_e1_x5_cost_contract()

    stage1_work = work_root(stage1_run_id)
    s1_sweep = json.loads((stage1_work / "sweep_results.json").read_text(encoding="utf-8"))
    if s1_sweep["verdict"] != STAGE1_VERDICT or s1_sweep["passers_n"] != 0:
        raise SystemExit("FAIL: stage-2 requires stage-1 passers=0 verdict")
    s1_parity = json.loads((stage1_work / "parity.json").read_text(encoding="utf-8"))
    if not s1_parity["all_match"]:
        raise SystemExit("FAIL: stage-1 parity not all_match")
    s1_p1 = json.loads((stage1_work / "p1_lock.json").read_text(encoding="utf-8"))
    if s1_p1["registry_lock"]["joint_registry_sha256"] != STAGE1_REGISTRY_SHA:
        raise SystemExit("FAIL: stage-1 registry SHA mismatch")

    bshas = {fp.name: sha256_file(fp) for fp in bundle_files(stage1_run_id)}

    code_shas = _code_file_shas()
    for rel in (
        "kabu_native/src/research/e1_x6_provisional/joint_oracle_replay.py",
        "kabu_native/src/research/e1_x6_provisional/day_robust_gates.py",
        "kabu_native/src/research/e1_x6_provisional/redesign_features.py",
        "kabu_native/src/research/e1_x6_provisional/joint_strategy.py",
        "kabu_native/scripts/run_e1_x6_plan21_stage2_redesign.py",
    ):
        fp = repo_root() / rel
        code_shas[rel] = hashlib.sha256(fp.read_bytes()).hexdigest() if fp.is_file() else None

    p1 = {
        "run_id": run_id,
        "stage": "PLAN21_STAGE2_ENTRY_REDESIGN",
        "plan_version": "2.1",
        "plan_sha256": plan_sha,
        "cost_contract": cost,
        "days": list(DAYS),
        "stage1": {
            "run_id": stage1_run_id,
            "verdict": s1_sweep["verdict"],
            "passers_n": s1_sweep["passers_n"],
            "results_sha256": s1_sweep["results_sha256"],
            "registry_sha256": STAGE1_REGISTRY_SHA,
            "parity_all_match": True,
            "bundle_shas": bshas,
            "p1_sha256": s1_p1["p1_sha256"],
        },
        "gates": {
            "plan21_gate_ids": PLAN21_GATE_IDS,
            "rolling_confirm_days": list(ROLLING_CONFIRM_DAYS),
            "best_day_rule": "day pnl desc, tie-break day asc; mechanical, never date-fitted",
            "date_specific_gates_forbidden": True,
        },
        "selection_priority": list(SELECTION_PRIORITY),
        "redesign": {
            "feature_inventory": FEATURE_INVENTORY,
            "features_unavailable": FEATURES_UNAVAILABLE,
            "feature_grids": {k: list(v) for k, v in STAGE2_FEATURE_GRIDS.items()},
            "group_templates": [
                {"group_id": gid, "spec": [[f, d, list(qs)] for f, d, qs in spec]}
                for gid, spec in STAGE2_GROUP_TEMPLATES
            ],
            "entry_cap": STAGE2_ENTRY_CAP,
            "joint_cap": STAGE2_JOINT_CAP,
            "enumeration_order": "lex sort of candidate_id; joint lex sort of strategy_id",
            "tie_break": "candidate_id / strategy_id lex asc",
            "seed": "NONE_DETERMINISTIC",
            "threshold_source": "quantiles over pooled eligible signals all 9 days, NaN excluded",
            "nan_policy": "insufficient history => NaN => predicate fails (no entry)",
            "forbidden": [
                "future values", "MFE/MAE features", "dates", "symbol-specific",
                "post-hoc losing-time-of-day exclusion",
            ],
            "exit_families": stage2_exit_families(),
        },
        "engine": {
            "evaluation_mode": "FULL_CANONICAL_EVENT_REPLAY_ORACLE_PARITY_PROVEN",
            "parity_scope": "stage-1 proof (identical bundles + engine code SHAs)",
        },
        "code_file_shas": code_shas,
        "dependency_versions": _dependency_versions(),
        "safety": {"submit": 0, "cancel": 0, "live": 0},
        "created_at_jst": datetime.now().astimezone().isoformat(),
    }
    p1["p1_sha256"] = sha256_obj({k: v for k, v in p1.items() if k != "p1_sha256"})
    write_json(work / "p1_lock.json", p1)
    progress(f"P21S2: P1 locked sha={p1['p1_sha256']} (pre-economics)")
    return p1


def phase_registry(run_id: str, work: Path, stage1_run_id: str) -> list[dict]:
    from research.e1_x6_provisional.joint_oracle_replay import (
        PartitionBundle,
        entry_signals_for_bundle,
    )
    from research.e1_x6_provisional.joint_strategy import (
        build_joint_strategy_registry,
        joint_registry_sha,
    )
    from research.e1_x6_provisional.redesign_features import (
        FEATURE_INVENTORY,
        STAGE2_JOINT_CAP,
        compute_bundle_features,
        enumerate_stage2_entries,
        resolve_quantile_values,
        stage2_exit_families,
    )
    from research.e1_x6_provisional.util import progress, read_json, sha256_obj, write_json

    pooled: dict[str, list[np.ndarray]] = {k: [] for k in FEATURE_INVENTORY}
    for fp in bundle_files(stage1_run_id):
        t0 = time.time()
        b = PartitionBundle.load(fp)
        signals = entry_signals_for_bundle(b)
        feats = compute_bundle_features(b, signals)
        for k, v in feats.items():
            pooled[k].append(v)
        progress(
            f"P21S2: features {b.day} {b.am_pm} signals={len(signals)} dt={time.time() - t0:.1f}s"
        )
        del b, signals, feats
    pooled_arr = {k: np.concatenate(v) for k, v in pooled.items()}
    qvals = resolve_quantile_values(pooled_arr)
    coverage = {
        k: {
            "n": int(pooled_arr[k].shape[0]),
            "finite": int(np.sum(~np.isnan(pooled_arr[k]))),
        }
        for k in pooled_arr
    }

    entries = enumerate_stage2_entries(qvals)
    registry = build_joint_strategy_registry(
        entries, exit_families=stage2_exit_families(), cap=STAGE2_JOINT_CAP
    )
    reg_sha = joint_registry_sha(registry)
    write_json(work / "entry_candidates.json", entries)
    write_json(work / "joint_registry.json", registry)
    lock = {
        "run_id": run_id,
        "quantile_values": {k: {str(q): v for q, v in d.items()} for k, d in qvals.items()},
        "feature_coverage": coverage,
        "entry_candidates_n": len(entries),
        "entry_candidates_sha256": sha256_obj(entries),
        "joint_registry_n": len(registry),
        "joint_registry_sha256": reg_sha,
        "locked_before_candidate_economics": True,
        "locked_at_jst": datetime.now().astimezone().isoformat(),
    }
    p1 = read_json(work / "p1_lock.json")
    p1["registry_lock"] = lock
    write_json(work / "p1_lock.json", p1)
    progress(f"P21S2: registry locked n={len(registry)} sha={reg_sha}")
    return registry


def run_sweep_pass(stage1_run_id: str, registry: list[dict], pass_name: str) -> dict:
    from research.e1_x6_provisional.joint_oracle_replay import (
        PackageAccumulator,
        PartitionBundle,
        entry_signals_for_bundle,
        replay_package_on_bundle,
    )
    from research.e1_x6_provisional.redesign_features import compute_bundle_features
    from research.e1_x6_provisional.util import progress

    accs = {p["strategy_id"]: PackageAccumulator(strategy_id=p["strategy_id"]) for p in registry}
    xps = {p["strategy_id"]: exit_params_from_family(p["exit_spec"]) for p in registry}

    for fp in bundle_files(stage1_run_id):
        t0 = time.time()
        b = PartitionBundle.load(fp)
        signals = entry_signals_for_bundle(b)
        arrays = compute_bundle_features(b, signals)
        exit_cache: dict = {}
        entry_mask_cache: dict = {}
        for p in registry:
            sid = p["strategy_id"]
            ekey = p["entry_candidate_id"]
            if ekey in entry_mask_cache:
                mask = entry_mask_cache[ekey]
            else:
                mask = entry_mask_for(
                    arrays,
                    {
                        "features": p["entry_features"],
                        "direction": p["entry_direction"],
                        "thresholds": p["entry_thresholds"],
                    },
                )
                entry_mask_cache[ekey] = mask
            replay_package_on_bundle(
                b,
                signals=signals,
                signal_mask=mask,
                xp=xps[sid],
                exit_cache=exit_cache,
                collect_trades=False,
                acc=accs[sid],
            )
        progress(
            f"P21S2: sweep[{pass_name}] {b.day} {b.am_pm} signals={len(signals)} "
            f"cache={len(exit_cache)} dt={time.time() - t0:.1f}s"
        )
        del b, signals, arrays, exit_cache, entry_mask_cache
    return accs


def phase_sweep_and_gates(run_id: str, work: Path, stage1_run_id: str) -> dict:
    from research.e1_x6_provisional.constants import DAYS
    from research.e1_x6_provisional.day_robust_gates import (
        evaluate_plan21_gates,
        realized_sequence_max_dd,
        refit_lodo_selection,
        selection_rank_key,
        simplicity_score,
    )
    from research.e1_x6_provisional.day_robust_gates import stop_loss_total as stop_loss_of
    from research.e1_x6_provisional.joint_oracle_replay import (
        PartitionBundle,
        metrics_from_accumulator,
    )
    from research.e1_x6_provisional.util import progress, read_json, sha256_obj, write_json

    registry = read_json(work / "joint_registry.json")

    base_trades: list[dict] = []
    invalid_source_trades = 0
    for fp in bundle_files(stage1_run_id):
        b = PartitionBundle.load(fp)
        base_trades.extend(b.x5_trades)
        if str(b.mask_meta.get("quality_class") or "") == "INVALID_SOURCE":
            invalid_source_trades += len(b.x5_trades)
        del b
    base = {
        "n": len(base_trades),
        "pnl": float(sum(float(t.get("net_pnl_yen_100") or 0) for t in base_trades)),
        "max_dd": realized_sequence_max_dd(base_trades),
        "stop_loss_total": stop_loss_of(base_trades),
    }
    progress(f"P21S2: BASE n={base['n']} pnl={base['pnl']:.2f} dd={base['max_dd']:.2f}")

    accs_a = run_sweep_pass(stage1_run_id, registry, "A")
    accs_b = run_sweep_pass(stage1_run_id, registry, "B")
    det_rows = []
    ab_all = True
    for sid in sorted(accs_a.keys()):
        sa = accs_a[sid].ledger_sha()
        sb = accs_b[sid].ledger_sha()
        ok = sa == sb
        ab_all = ab_all and ok
        det_rows.append({"strategy_id": sid, "sha_a": sa, "sha_b": sb, "match": ok})
    progress(f"P21S2: A/B determinism all={ab_all}")

    metrics = {sid: metrics_from_accumulator(acc, DAYS) for sid, acc in accs_a.items()}
    day_pnls = {sid: m["day_pnl"] for sid, m in metrics.items()}
    pkgs_by_id = {p["strategy_id"]: p for p in registry}
    lodo_rows = [
        refit_lodo_selection(day_pnls, pkgs_by_id, held_out_day=d, days=DAYS) for d in DAYS
    ]
    lodo_pnls = {r["held_out_day"]: r["held_out_pnl"] for r in lodo_rows}

    results = []
    for p in registry:
        sid = p["strategy_id"]
        m = metrics[sid]
        g = evaluate_plan21_gates(
            m,
            base_max_dd=base["max_dd"],
            base_stop_loss_total=base["stop_loss_total"],
            ab_match=ab_all,
            invalid_source_n=invalid_source_trades,
            lodo_held_out_pnls=lodo_pnls,
            days=DAYS,
        )
        results.append(
            {
                "strategy_id": sid,
                "entry_candidate_id": p["entry_candidate_id"],
                "exit_family_id": p["exit_family_id"],
                "metrics": m,
                "gates": g["gates"],
                "all_pass": g["all_pass"],
                "failed": g["failed"],
                "simplicity": simplicity_score(p),
            }
        )

    passers = [r for r in results if r["all_pass"]]
    ranked = sorted(
        passers, key=lambda r: selection_rank_key(r["metrics"], package=pkgs_by_id[r["strategy_id"]])
    )
    verdict = (
        "E1_X6_JOINT_RESEARCH_SPEC_FROZEN_FOR_FORWARD_TEST"
        if ranked
        else "E1_X6_NO_ROBUST_JOINT_STRATEGY"
    )
    out = {
        "run_id": run_id,
        "stage": "PLAN21_STAGE2_ENTRY_REDESIGN",
        "stage1_run_id": stage1_run_id,
        "base": base,
        "invalid_source_trades": invalid_source_trades,
        "ab_determinism": {"all_match": ab_all, "rows": det_rows},
        "lodo": {"rows": lodo_rows, "held_out_pnls": lodo_pnls},
        "results": results,
        "passers_n": len(ranked),
        "selected": ranked[0]["strategy_id"] if ranked else None,
        "verdict": verdict,
        "results_sha256": sha256_obj(results),
    }
    write_json(work / "sweep_results.json", out)
    progress(f"P21S2: sweep done passers={len(ranked)} verdict={verdict}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--stage1-run-id", default=STAGE1_RUN_ID_DEFAULT)
    ap.add_argument("--phase", default="all", choices=["all", "p1", "registry", "sweep"])
    args = ap.parse_args()

    from research.e1_x6_provisional.util import progress, set_progress_mode

    set_progress_mode("final")
    run_id = args.run_id or (
        f"e1x6_p21s2_{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
        f"{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:8]}"
    )
    work = work_root(run_id)
    progress(f"P21S2: run_id={run_id} phase={args.phase} stage1={args.stage1_run_id}")
    print(f"RUN_ID={run_id}", flush=True)

    if args.phase in ("all", "p1"):
        phase_p1(run_id, work, args.stage1_run_id)
    if args.phase in ("all", "registry"):
        phase_registry(run_id, work, args.stage1_run_id)
    if args.phase in ("all", "sweep"):
        out = phase_sweep_and_gates(run_id, work, args.stage1_run_id)
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "verdict": out["verdict"],
                    "passers_n": out["passers_n"],
                    "selected": out["selected"],
                    "ab": out["ab_determinism"]["all_match"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    progress("P21S2: script phase complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
