"""Plan 2.1 day-robust joint sweep: ALL 200 JointRegistry packages, full canonical.

Order: preconditions -> P0 manifest -> P1 lock (pre-economics) -> BASE capture
(session replay per partition, oracle bundles persisted durably) -> engine parity
proof (oracle vs session ledger, X5 package) -> registry lock -> sweep A -> sweep B
-> A/B determinism -> Plan 2.1 gates + rolling/LODO -> selection -> publish.

No Shadow / Runtime / Paper / Live / Discord changes. submit/cancel/live = 0/0/0.
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
SRC = NATIVE / "src"
REPO = NATIVE.parent
for p in (str(SRC), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402


def work_root(run_id: str) -> Path:
    # Outside the repo: kabu_native/results and OS temp were both wiped on 2026-08-02.
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


def entry_mask_for(signals: list, arrays: dict, cand: dict) -> np.ndarray:
    n = len(signals)
    mask = np.ones(n, dtype=bool)
    fam = cand.get("family")
    for feat in cand["features"]:
        v = arrays[feat]
        thr = float(cand["thresholds"][feat])
        if fam == "SINGLE_FEATURE":
            direction = cand["direction"]
        else:
            part = [p for p in str(cand["direction"]).split("&") if p.startswith(feat + ":")]
            direction = part[0].split(":", 1)[1] if part else "higher_better"
        if direction == "higher_better":
            mask &= v >= thr
        else:
            mask &= v <= thr
    return mask


def phase_p0_p1(run_id: str, work: Path) -> dict:
    from research.e1_x6_provisional.analysis_mask import build_mask_index
    from research.e1_x6_provisional.constants import (
        CANDIDATE_CAP,
        DAYS,
        PLAN_REL,
        PREDICTOR_FEATURES,
        QUANTILE_GRID,
    )
    from research.e1_x6_provisional.cost_contract import verify_frozen_e1_x5_cost_contract
    from research.e1_x6_provisional.day_robust_gates import (
        ABOLISHED_20_GATES,
        PLAN21_GATE_IDS,
        ROLLING_CONFIRM_DAYS,
        SELECTION_PRIORITY,
        SENSITIVITY_EXCLUDE_DAY,
    )
    from research.e1_x6_provisional.joint_strategy import EXIT_FAMILIES, JOINT_STRATEGY_CAP
    from research.e1_x6_provisional.p0_manifest import build_source_manifest
    from research.e1_x6_provisional.p1_lock import _code_file_shas, _dependency_versions
    from research.e1_x6_provisional.util import (
        progress,
        repo_root,
        sha256_file,
        sha256_obj,
        write_json,
    )
    from small_paper.e1_x5_forward_shadow import (
        GIVEBACK,
        MAX_HOLD_SEC,
        SPREAD_MAX_BPS,
        STOP_BPS,
        TARGET_BPS,
        THRESHOLD,
        TRAIL_ARM_BPS,
    )

    plan_path = repo_root() / PLAN_REL
    plan_text = plan_path.read_text(encoding="utf-8")
    m = re.search(r"\|\s*Version\s*\|\s*`?([^`|]+)`?\s*\|", plan_text)
    plan_version = m.group(1).strip().strip("`") if m else None
    plan_sha = sha256_file(plan_path)
    if plan_version != "2.1":
        raise SystemExit(f"FAIL: Plan must be Version 2.1, got {plan_version}")
    cost = verify_frozen_e1_x5_cost_contract()
    progress(f"P21: plan 2.1 sha={plan_sha} cost={cost['status']}")

    # Resume path: an existing P1 lock is immutable. Verify the plan is unchanged
    # and reuse it instead of rewriting (P1 must never be re-locked after economics).
    existing = work / "p1_lock.json"
    if existing.is_file():
        p1_prev = json.loads(existing.read_text(encoding="utf-8"))
        if p1_prev.get("plan_sha256") != plan_sha:
            raise SystemExit("FAIL: plan SHA changed since P1 lock; resume forbidden")
        manifest = build_source_manifest(final=True)
        if sha256_obj(manifest) != p1_prev.get("source_manifest_sha256"):
            raise SystemExit("FAIL: source manifest changed since P1 lock; resume forbidden")
        mask_index = build_mask_index(manifest)
        progress(f"P21: P1 lock reused (resume) sha={p1_prev.get('p1_sha256')}")
        return {"p1": p1_prev, "manifest": manifest, "mask_index": mask_index}

    manifest = build_source_manifest(final=True)
    manifest_sha = sha256_obj(manifest)
    mask_index = build_mask_index(manifest)
    write_json(work / "source_manifest.json", manifest)

    code_shas = _code_file_shas()
    extra = {}
    for rel in (
        "kabu_native/src/research/e1_x6_provisional/joint_oracle_replay.py",
        "kabu_native/src/research/e1_x6_provisional/oracle_capture.py",
        "kabu_native/src/research/e1_x6_provisional/day_robust_gates.py",
        "kabu_native/src/research/e1_x6_provisional/joint_strategy.py",
        "kabu_native/scripts/run_e1_x6_plan21_day_robust_sweep.py",
    ):
        fp = repo_root() / rel
        extra[rel] = hashlib.sha256(fp.read_bytes()).hexdigest() if fp.is_file() else None
    code_shas.update(extra)

    exec_exit_semantics = {
        "order_per_event": "mfe_update -> trail_arm -> STOP -> TARGET -> TRAILING -> MAX_HOLD",
        "tolerances": "1e-9 (matches frozen _update_position)",
        "prices": "entry_ask / exit_bid; bid>0 events only",
        "registered_families_executable": {
            "X5_FROZEN": {"stop": STOP_BPS, "target": TARGET_BPS, "arm": TRAIL_ARM_BPS,
                          "giveback": GIVEBACK, "max_hold": MAX_HOLD_SEC},
            "X5_TIGHTER_STOP": {"stop": STOP_BPS * 0.75},
            "X5_WIDER_TARGET": {"target": TARGET_BPS * 1.25},
            "X5_SHORTER_HOLD": {"max_hold": int(MAX_HOLD_SEC * 0.5)},
        },
        "note": "invalidation/no_progress fields in these 4 registered families are "
                "descriptive of frozen X5 (which has neither); executable exits are "
                "STOP/TARGET/TRAILING/MAX_HOLD only, exactly as the prior session sweep",
    }

    p1 = {
        "run_id": run_id,
        "plan_version": plan_version,
        "plan_sha256": plan_sha,
        "plan_v20_sha256": "72d692dfd89b98ff50b6ca3fcdcc6ab17c449216c5bf3d619cdc1eb2ccf2c82a",
        "source_manifest_sha256": manifest_sha,
        "cost_contract": cost,
        "days": list(DAYS),
        "gates": {
            "plan21_gate_ids": PLAN21_GATE_IDS,
            "abolished_from_2_0": ABOLISHED_20_GATES,
            "sensitivity_exclude_day": SENSITIVITY_EXCLUDE_DAY,
            "rolling_confirm_days": list(ROLLING_CONFIRM_DAYS),
            "rolling_definition": "fixed-package day-subset: total>0, median>0, ex-best-confirm-day>0",
            "lodo_definition": (
                "REFIT_LODO_STABILITY procedure-level: for each held-out day, rank ALL "
                "registry packages on the other 8 days by the 2.1 priority key "
                "(subset metrics; dd/pf neutral), select first, record its held-out pnl; "
                "gates on the 9 held-out pnls apply to every package's gate vector"
            ),
            "best_day_rule": "day pnl desc, tie-break day asc; mechanical, never date-fitted",
            "date_specific_gates_forbidden": True,
            "dd_definition": "realized_trade_sequence_max_dd (exit order, tie exit_time|symbol)",
            "stop_loss_definition": "sum of negative net pnl over STOP exits",
            "base_compare": "candidate max_dd >= base max_dd AND stop_loss_total >= base (not worse)",
        },
        "selection_priority": list(SELECTION_PRIORITY),
        "day_weighting": "equal per day; day metrics from per-day aggregates (not row counts)",
        "enumeration": {
            "entry_features": list(PREDICTOR_FEATURES),
            "quantile_grid": list(QUANTILE_GRID),
            "entry_cap": CANDIDATE_CAP,
            "joint_cap": JOINT_STRATEGY_CAP,
            "joint_order": "lex sort of strategy_id then cap (deterministic)",
            "tie_break": "candidate_id / strategy_id lex asc",
            "seed": "NONE_DETERMINISTIC",
            "directions": {"score": "higher_better", "spread_bps": "lower_better",
                           "score_vs_threshold_gap": "higher_better"},
            "threshold_source": "quantiles over all usable-day mask-in SCORE rows (pre-registered process)",
        },
        "exit_families": [dict(x) for x in EXIT_FAMILIES],
        "exec_exit_semantics": exec_exit_semantics,
        "entry_gate_ladder": {
            "candidate_independent_pre_gates": sorted(
                ["INVALID_LOOKBACK", "SESSION_INVALID", "INVALID_QUOTE", "DUPLICATE_EVENT"]
            ),
            "spread_max_bps": float(SPREAD_MAX_BPS),
            "cap": 5,
            "same_symbol_reentry": "blocked while holding; free at exit event (inclusive)",
            "x5_threshold_reference": float(THRESHOLD),
        },
        "engine": {
            "evaluation_mode": "FULL_CANONICAL_EVENT_REPLAY_ORACLE_PARITY_PROVEN",
            "parity_requirement": "oracle vs session replay_partition: X5 package ledger must match on ALL partitions before candidate economics are read",
            "adoption": "replicates _adopt_trade/validate_trade_window (lookback, 120s internal gap, known gap intervals)",
        },
        "code_file_shas": code_shas,
        "dependency_versions": _dependency_versions(),
        "safety": {"submit": 0, "cancel": 0, "live": 0},
        "created_at_jst": datetime.now().astimezone().isoformat(),
    }
    p1["p1_sha256"] = sha256_obj({k: v for k, v in p1.items() if k != "p1_sha256"})
    write_json(work / "p1_lock.json", p1)
    progress(f"P21: P1 locked sha={p1['p1_sha256']} (pre-economics)")
    return {"p1": p1, "manifest": manifest, "mask_index": mask_index}


def phase_capture(run_id: str, work: Path, mask_index: dict) -> list[dict]:
    from research.e1_x6_provisional.constants import DAYS
    from research.e1_x6_provisional.oracle_capture import capture_day, durable_bundle_root
    from research.e1_x6_provisional.util import progress, write_json

    out_dir = durable_bundle_root(run_id)
    metas: list[dict] = []
    for day in DAYS:
        # Day-level resume: a day's meta file is written only after ALL of its
        # window bundles were fully saved, so its presence proves completeness.
        day_meta_fp = out_dir / f"{day}_meta.json"
        if day_meta_fp.is_file():
            day_metas = json.loads(day_meta_fp.read_text(encoding="utf-8"))
            if all((out_dir / mm["file"]).is_file() for mm in day_metas):
                progress(f"P21: capture day={day} SKIP (resume; windows={len(day_metas)})")
                metas.extend(day_metas)
                write_json(work / "capture_metas.json", metas)
                continue
        t0 = time.time()
        day_metas = capture_day(day, mask_index=mask_index, out_dir=out_dir)
        write_json(day_meta_fp, day_metas)
        metas.extend(day_metas)
        progress(f"P21: capture day={day} done dt={time.time() - t0:.1f}s")
        write_json(work / "capture_metas.json", metas)
    return metas


def phase_parity(run_id: str, work: Path) -> dict:
    from research.e1_x6_provisional.joint_oracle_replay import (
        ExitParams,
        PartitionBundle,
        parity_check_bundle,
    )
    from research.e1_x6_provisional.oracle_capture import durable_bundle_root
    from research.e1_x6_provisional.util import progress, write_json
    from small_paper.e1_x5_forward_shadow import (
        GIVEBACK,
        MAX_HOLD_SEC,
        STOP_BPS,
        TARGET_BPS,
        THRESHOLD,
        TRAIL_ARM_BPS,
    )

    xp = ExitParams(
        stop_bps=float(STOP_BPS),
        target_bps=float(TARGET_BPS),
        trail_arm_bps=float(TRAIL_ARM_BPS),
        giveback=float(GIVEBACK),
        max_hold_sec=float(MAX_HOLD_SEC),
    )
    out_dir = durable_bundle_root(run_id)
    rows = []
    all_ok = True
    for fp in sorted(out_dir.glob("*.pkl.gz")):
        b = PartitionBundle.load(fp)
        r = parity_check_bundle(b, x5_threshold=float(THRESHOLD), xp=xp)
        rows.append(r)
        all_ok = all_ok and r["match"]
        progress(
            f"P21: parity {b.day} {b.am_pm} oracle_n={r['oracle_n']} session_n={r['session_n']} "
            f"match={r['match']} mm={r['mismatch_n']}"
        )
        del b
    out = {"all_match": all_ok, "partitions": rows}
    write_json(work / "parity.json", out)
    if not all_ok:
        raise SystemExit("FAIL: ORACLE_PARITY_MISMATCH — candidate economics must not be read")
    return out


def phase_registry(run_id: str, work: Path) -> dict:
    from research.e1_x6_provisional.joint_oracle_replay import PartitionBundle
    from research.e1_x6_provisional.joint_strategy import (
        EXIT_FAMILIES,
        build_joint_strategy_registry,
        joint_registry_sha,
    )
    from research.e1_x6_provisional.oracle_capture import durable_bundle_root
    from research.e1_x6_provisional.p2_execute import enumerate_candidates
    from research.e1_x6_provisional.util import progress, read_json, sha256_obj, write_json

    out_dir = durable_bundle_root(run_id)
    rows = []
    for fp in sorted(out_dir.glob("*.pkl.gz")):
        b = PartitionBundle.load(fp)
        rows.extend(
            {
                "score": r.get("score"),
                "spread_bps": r.get("spread_bps"),
                "score_vs_threshold_gap": r.get("score_vs_threshold_gap"),
            }
            for r in b.score_rows
            if r.get("score") is not None
        )
        del b
    entries = enumerate_candidates(rows)
    registry = build_joint_strategy_registry(entries, exit_families=EXIT_FAMILIES)
    reg_sha = joint_registry_sha(registry)
    write_json(work / "entry_candidates.json", entries)
    write_json(work / "joint_registry.json", registry)
    lock = {
        "run_id": run_id,
        "entry_candidates_n": len(entries),
        "entry_candidates_sha256": sha256_obj(entries),
        "joint_registry_n": len(registry),
        "joint_registry_sha256": reg_sha,
        "build_rows_n": len(rows),
        "prev_plan20_registry_sha256": "44ad006fe9928c9077cd12c0b69b8253e938b4101d55ac60e6a0bc85b8e2bb34",
        "locked_before_candidate_economics": True,
        "locked_at_jst": datetime.now().astimezone().isoformat(),
    }
    p1 = read_json(work / "p1_lock.json")
    p1["registry_lock"] = lock
    write_json(work / "p1_lock.json", p1)
    progress(f"P21: registry locked n={len(registry)} sha={reg_sha}")
    return {"registry": registry, "lock": lock}


def run_sweep_pass(run_id: str, registry: list[dict], pass_name: str) -> dict:
    """One full oracle sweep over all bundles for all packages."""
    from research.e1_x6_provisional.joint_oracle_replay import (
        PackageAccumulator,
        PartitionBundle,
        entry_signals_for_bundle,
        replay_package_on_bundle,
    )
    from research.e1_x6_provisional.oracle_capture import durable_bundle_root
    from research.e1_x6_provisional.util import progress
    from small_paper.e1_x5_forward_shadow import THRESHOLD

    out_dir = durable_bundle_root(run_id)
    accs = {p["strategy_id"]: PackageAccumulator(strategy_id=p["strategy_id"]) for p in registry}
    xps = {p["strategy_id"]: exit_params_from_family(p["exit_spec"]) for p in registry}
    entries_by_sid = {p["strategy_id"]: p for p in registry}

    files = sorted(out_dir.glob("*.pkl.gz"))
    for fp in files:
        t0 = time.time()
        b = PartitionBundle.load(fp)
        signals = entry_signals_for_bundle(b)
        arrays = {
            "score": np.asarray([float(r["score"]) for r in signals], dtype=np.float64),
            "spread_bps": np.asarray(
                [float(r["spread_bps"]) if r.get("spread_bps") is not None else np.nan for r in signals],
                dtype=np.float64,
            ),
            "score_vs_threshold_gap": np.asarray(
                [
                    float(r["score_vs_threshold_gap"])
                    if r.get("score_vs_threshold_gap") is not None
                    else float(r["score"]) - float(THRESHOLD)
                    for r in signals
                ],
                dtype=np.float64,
            ),
        }
        exit_cache: dict = {}
        entry_mask_cache: dict = {}
        for sid, pkg in entries_by_sid.items():
            ekey = pkg["entry_candidate_id"]
            if ekey in entry_mask_cache:
                mask = entry_mask_cache[ekey]
            else:
                mask = entry_mask_for(
                    signals,
                    arrays,
                    {
                        "family": pkg["entry_family"],
                        "features": pkg["entry_features"],
                        "direction": pkg["entry_direction"],
                        "thresholds": pkg["entry_thresholds"],
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
            f"P21: sweep[{pass_name}] {b.day} {b.am_pm} signals={len(signals)} "
            f"cache={len(exit_cache)} dt={time.time() - t0:.1f}s"
        )
        del b, signals, arrays, exit_cache, entry_mask_cache
    return accs


def phase_sweep_and_gates(run_id: str, work: Path) -> dict:
    from research.e1_x6_provisional.constants import DAYS
    from research.e1_x6_provisional.day_robust_gates import (
        evaluate_plan21_gates,
        refit_lodo_selection,
        selection_rank_key,
        simplicity_score,
    )
    from research.e1_x6_provisional.joint_oracle_replay import (
        PartitionBundle,
        metrics_from_accumulator,
    )
    from research.e1_x6_provisional.day_robust_gates import (
        realized_sequence_max_dd,
        stop_loss_total as stop_loss_of,
    )
    from research.e1_x6_provisional.oracle_capture import durable_bundle_root
    from research.e1_x6_provisional.util import progress, read_json, sha256_obj, write_json

    registry = read_json(work / "joint_registry.json")

    # BASE (frozen X5) comparison metrics from session-adopted trades
    base_trades: list[dict] = []
    invalid_source_trades = 0
    for fp in sorted(durable_bundle_root(run_id).glob("*.pkl.gz")):
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
    progress(f"P21: BASE n={base['n']} pnl={base['pnl']:.2f} dd={base['max_dd']:.2f}")

    accs_a = run_sweep_pass(run_id, registry, "A")
    accs_b = run_sweep_pass(run_id, registry, "B")
    det_rows = []
    ab_all = True
    for sid in sorted(accs_a.keys()):
        sa = accs_a[sid].ledger_sha()
        sb = accs_b[sid].ledger_sha()
        ok = sa == sb
        ab_all = ab_all and ok
        det_rows.append({"strategy_id": sid, "sha_a": sa, "sha_b": sb, "match": ok})
    progress(f"P21: A/B determinism all={ab_all}")

    metrics = {sid: metrics_from_accumulator(acc, DAYS) for sid, acc in accs_a.items()}

    # procedure-level REFIT_LODO_STABILITY
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
    ranked_passers = sorted(
        passers, key=lambda r: selection_rank_key(r["metrics"], package=pkgs_by_id[r["strategy_id"]])
    )
    verdict = (
        "E1_X6_JOINT_RESEARCH_SPEC_FROZEN_FOR_FORWARD_TEST"
        if ranked_passers
        else "E1_X6_NO_ROBUST_JOINT_STRATEGY"
    )
    out = {
        "run_id": run_id,
        "base": base,
        "invalid_source_trades": invalid_source_trades,
        "ab_determinism": {"all_match": ab_all, "rows": det_rows},
        "lodo": {"rows": lodo_rows, "held_out_pnls": lodo_pnls},
        "results": results,
        "passers_n": len(ranked_passers),
        "selected": ranked_passers[0]["strategy_id"] if ranked_passers else None,
        "verdict": verdict,
        "results_sha256": sha256_obj(results),
    }
    write_json(work / "sweep_results.json", out)
    progress(f"P21: sweep done passers={len(ranked_passers)} verdict={verdict}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument(
        "--phase",
        default="all",
        choices=["all", "p0p1", "capture", "parity", "registry", "sweep"],
    )
    args = ap.parse_args()

    from research.e1_x6_provisional.util import progress, set_progress_mode

    set_progress_mode("final")
    run_id = args.run_id or (
        f"e1x6_p21_{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
        f"{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:8]}"
    )
    work = work_root(run_id)
    progress(f"P21: run_id={run_id} phase={args.phase}")
    print(f"RUN_ID={run_id}", flush=True)

    ctx = None
    if args.phase in ("all", "p0p1", "capture"):
        ctx = phase_p0_p1(run_id, work)
    if args.phase in ("all", "capture"):
        phase_capture(run_id, work, ctx["mask_index"])
    if args.phase in ("all", "parity"):
        phase_parity(run_id, work)
    if args.phase in ("all", "registry"):
        phase_registry(run_id, work)
    if args.phase in ("all", "sweep"):
        out = phase_sweep_and_gates(run_id, work)
        print(json.dumps(
            {
                "run_id": run_id,
                "verdict": out["verdict"],
                "passers_n": out["passers_n"],
                "selected": out["selected"],
                "ab": out["ab_determinism"]["all_match"],
            },
            ensure_ascii=False,
        ), flush=True)
    progress("P21: script phase complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
