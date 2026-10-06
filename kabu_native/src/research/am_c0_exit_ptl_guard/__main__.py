"""Offline AM C0 EXIT PTL guard. Frozen C0 ENTRY. CURRENT keeps C14. No Runtime write. No Paper."""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_exit_ptl_guard import (
    ANALYSIS_ID,
    ARCHITECTURE_ID,
    C0_AUGMENT_LOCKED,
    C0_OVERLAY_LOCKED,
    CURRENT_EXIT_CHANGE_N,
    CURRENT_LOCKED,
    EARLIEST_TRIGGER_SEC,
    ENTRY_PARENT_SHA256,
    ENTRY_POLICY_CHANGE_N,
    EXIT_THRESHOLD_SEARCH_N,
    FUTURE_EXIT_SIGNAL_USE_N,
    LATEST_TRIGGER_SEC,
    ORACLE_MFE_EXIT_USE_N,
    PAPER_OPERATION_N,
    RUNTIME_CHANGE_N,
    TRAILING_THRESHOLD_SEARCH_N,
    WAIT_CHANGE_N,
)
from research.am_c0_exit_ptl_guard.analyze import (
    current_trade_counts,
    decide_case,
    e1_augment_exit_metrics,
    e1_full_gate,
    fixed_trade_effect,
    identity_audit,
    join_l2_keys,
    preservation_ok,
    vs_c0,
)
from research.am_c0_exit_ptl_guard.harvest import process_e1_day
from research.am_c0_exit_ptl_guard.precommit import precommit_spec, print_precommit, spec_sha256
from research.am_c0_exit_ptl_guard.ptl import apply_e1_exits
from research.am_c0_exit_ptl_guard.publish import OUT, build_markdown, kv_rows, write_artifacts
from research.am_c0_exit_ptl_guard.replay import prepare_c0
from research.am_current_utility_augment.analyze import preservation_audit
from research.am_current_utility_augment.overlay import overlay_replay
from research.am_entry_fixed_spec_oof import V2_CURRENT_MAX_DD, V2_CURRENT_NET_PNL, V2_CURRENT_PF, V2_CURRENT_TRADE_N
from research.am_entry_information_expansion import MAX_WORKERS
from research.am_entry_profit_improvement import (
    C14_ID,
    CANCEL_N,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    LIVE_ORDER_N,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
)
from research.am_entry_profit_improvement.analyze import freeze_parity, independent_top3
from research.am_entry_profit_improvement.metrics import _pf_num, economic_pack, paired_delta
from research.am_entry_research_final_decision.spec import canonical_c0_spec
from research.am_entry_research_final_decision.spec import spec_sha256 as c0_spec_sha256
from research.am_exit_contribution_rca.contract import c14_horizons
from research.am_expanded_entry_risk_integration.analyze import profit_concentration
from research.anchor_timing_robustness.inventory import build_inventory
from research.canonical_entry_performance_rebase.analyze import _f, row_key, session_of
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.passive_wait_policy_reassessment.analyze import _close
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
REGIME_LABELED = (
    NATIVE / "results" / "research" / "_work_cache" / "am_entry_temporal_regime_information" / "labeled_am_regime.json"
)
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_c0_exit_ptl_guard"
RCA_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_exit_contribution_rca"

INTEGRITY_ZERO_KEYS = (
    "FUTURE_EXIT_SIGNAL_USE_N",
    "ORACLE_MFE_EXIT_USE_N",
    "ENTRY_POLICY_CHANGE_N",
    "B0_SCORE_MISMATCH_N",
    "B1_SCORE_MISMATCH_N",
    "C0_CONFIRMATION_MISMATCH_N",
    "C0_CANDIDATE_DECISION_MISMATCH_N",
    "EXIT_THRESHOLD_SEARCH_N",
    "TRAILING_THRESHOLD_SEARCH_N",
    "WAIT_CHANGE_N",
    "CURRENT_EXIT_CHANGE_N",
    "RUNTIME_CHANGE_N",
    "PAPER_OPERATION_N",
    "ITAYOSE_EXIT_USE_N",
    "SPECIAL_BOARD_EXIT_USE_N",
    "INVALID_QUOTE_EXIT_USE_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, default=str), encoding="utf-8")


def _empty_required(*, verdict: str, nxt: str, sha: str | None = None) -> dict[str, Any]:
    keys = (
        "BASE_PARITY",
        "EXIT_ARCHITECTURE",
        "EXIT_PRECOMMIT_SPEC_SHA256",
        "FIXED_TRADE_N",
        "FIXED_DELTA_NET",
        "L2_BASE_N",
        "PTL_TRIGGER_N",
        "L2_PTL_TRIGGER_N",
        "L2_CONVERTED_TO_NONLOSS_N",
        "WINNER_EARLY_CUT_N",
        "LOSER_PNL_SAVED_BY_GUARD",
        "WINNER_PNL_LOST_BY_GUARD",
        "E1_AUGMENT_TRADE_N",
        "E1_AUGMENT_NET",
        "E1_AUGMENT_PF",
        "E1_OVERLAY_NET",
        "E1_OVERLAY_PF",
        "E1_OVERLAY_DD",
        "PAIRED_POS_DAYS",
        "PAIRED_NEG_DAYS",
        "PAIRED_ZERO_DAYS",
        "PAIRED_MEDIAN",
        "EX_BEST",
        "EX_TOP3",
        "DELTA_NET_VS_C0",
        "DELTA_PF_VS_C0",
        "DELTA_DD_VS_C0",
        "DELTA_PAIRED_MEDIAN_VS_C0",
        "CURRENT_PRESERVATION_PASS",
        "E1_FULL_PASS",
        "TRUE_OOS",
        "NEW_FORWARD_N",
        "VERDICT",
        "NEXT",
    )
    req = {k: None for k in keys}
    req["EXIT_ARCHITECTURE"] = ARCHITECTURE_ID
    req["EXIT_PRECOMMIT_SPEC_SHA256"] = sha
    req["TRUE_OOS"] = TRUE_OOS
    req["NEW_FORWARD_N"] = NEW_FORWARD_N
    req["VERDICT"] = verdict
    req["NEXT"] = nxt
    req["BASE_PARITY"] = False
    req["E1_FULL_PASS"] = False
    req["CURRENT_PRESERVATION_PASS"] = False
    return req


def _integrity(msg: str, extra: dict | None = None, *, sha: str | None = None) -> int:
    decision = decide_case(
        integrity_ok=False,
        preservation_ok=False,
        full_pass=False,
        overlay_net=0.0,
        overlay_pf=0.0,
        overlay_dd=0.0,
        paired_median=None,
        paired_pos=0,
        paired_neg=0,
        ex_best=None,
        ex_top3=None,
        winner_early_cut_n=0,
        l2_converted_n=0,
        l2_ptl_n=0,
        l2_reduced_n=0,
    )
    required = _empty_required(verdict=str(decision.get("VERDICT")), nxt=str(decision.get("NEXT")), sha=sha)
    required["STOP_REASON"] = msg
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **(extra or {})}
    report["_markdown"] = build_markdown(report)
    write_artifacts(
        report,
        {
            "summary": kv_rows(required),
            "integrity": kv_rows({"STOP_REASON": msg}),
        },
    )
    print(msg, flush=True)
    return 2


def _pool(fn, jobs: list[dict], label: str, key: str) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, job): job.get(key) for job in jobs}
        for fut in as_completed(futs):
            ident = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, key: ident, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {label} {body.get(key) or ident} ok={body.get('ok')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _arm_fill_rate(admitted: int, fill_n: int) -> float | None:
    return (float(fill_n) / float(admitted)) if admitted else None


def _slim(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k not in ("trades", "daily")}


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get(
        "PYTHONPATH", ""
    )
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM C0 EXIT PTL GUARD V1", flush=True)
    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        return _integrity("STOP. Runtime WAIT_SEC drifted from 1.0.")
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        return _integrity("STOP. DEV_WAIT_SEC drifted from 5.0.")
    if abs(float(EARLIEST_TRIGGER_SEC) - 120.0) > 1e-12 or abs(float(LATEST_TRIGGER_SEC) - 600.0) > 1e-12:
        return _integrity("STOP. PTL window drifted.")
    if list(FEATURE_ORDER) != [
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ]:
        return _integrity("STOP. FEATURE_ORDER drift.")
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        return _integrity("STOP. rank_pass_gate drift.")
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        return _integrity("STOP. C14 identity mismatch.")
    if TRUE_OOS is not False or int(NEW_FORWARD_N) != 0:
        return _integrity("STOP. TRUE_OOS / NEW_FORWARD_N drifted.")
    if int(SUBMIT_N) != 0 or int(CANCEL_N) != 0 or int(LIVE_ORDER_N) != 0 or PAPER_OPERATED is not False:
        return _integrity("STOP. submit/cancel/live/paper drifted.")
    if W5_RUNTIME_ADOPTED is not False or RUNTIME_CHANGED is not False:
        return _integrity("STOP. Runtime freeze drifted.")

    parent_sha = c0_spec_sha256(canonical_c0_spec())
    if parent_sha != ENTRY_PARENT_SHA256:
        return _integrity("STOP. C0 prospective spec SHA256 mismatch.", extra={"got_sha": parent_sha})

    spec = precommit_spec()
    sha = spec_sha256(spec)
    print_precommit(spec, sha)

    try:
        horizons = c14_horizons(c14)
    except Exception as exc:
        return _integrity(f"STOP. C14 horizon resolve failed: {exc}", sha=sha)
    print(f"C14 horizons {horizons}", flush=True)

    if not REGIME_LABELED.is_file():
        return _integrity("STOP. labeled AM rows missing.", sha=sha)
    rows = list(_load(REGIME_LABELED).get("rows") or [])
    pm_n = sum(1 for r in rows if session_of(r) != "AM")
    if pm_n:
        return _integrity("STOP. PM rows in labeled AM.", extra={"PM_ROWS_USED_N": pm_n}, sha=sha)
    top3 = independent_top3(rows)
    obs = {
        "AM_LABELED_N": len(rows),
        "AM_Y_FILL5_POS_N": sum(1 for r in rows if int(r.get("Y_FILL5") or 0) == 1),
        "AM_CURRENT_TOP3_FILL5_RATE": top3.get("AM_CURRENT_TOP3_FILL5_RATE"),
    }
    parity_pop = freeze_parity(obs)
    if not parity_pop.get("ok"):
        return _integrity("STOP. Frozen AM labeled population did not reproduce.", extra={"parity": parity_pop}, sha=sha)

    print("prepare frozen C0", flush=True)
    got = prepare_c0(rows, REGIME_LABELED)
    tagged = list(got.get("tagged") or [])
    baseline = got.get("baseline") or {}
    e0_overlay = got.get("overlay") or {}
    c0_keys = set(got.get("c0_keys") or set())
    cur_trades = [t for t in (baseline.get("trades") or []) if str(t.get("arm") or "CURRENT") == "CURRENT"]
    e0_aug = [t for t in (e0_overlay.get("trades") or []) if str(t.get("arm") or "") == "AUGMENT"]
    e0_all = list(e0_overlay.get("trades") or [])
    cur_pack = economic_pack(cur_trades, list(ELIGIBLE_DAYS))
    e0_aug_pack = economic_pack(e0_aug, list(ELIGIBLE_DAYS))
    e0_ov_pack = economic_pack(e0_all, list(ELIGIBLE_DAYS))
    e0_paired = paired_delta(list(e0_ov_pack.get("daily") or []), list(cur_pack.get("daily") or []))
    replay_ok = (
        int(cur_pack.get("trade_count") or -1) == int(CURRENT_LOCKED["TRADE_N"])
        and _close(cur_pack.get("net_pnl_yen_100"), CURRENT_LOCKED["NET"], PARITY_ABS_TOL)
        and _close(cur_pack.get("profit_factor"), CURRENT_LOCKED["PF"], 1e-12)
        and _close(cur_pack.get("max_drawdown_yen_100"), CURRENT_LOCKED["DD"], PARITY_ABS_TOL)
        and int(cur_pack.get("trade_count") or -1) == int(V2_CURRENT_TRADE_N)
        and _close(cur_pack.get("net_pnl_yen_100"), V2_CURRENT_NET_PNL, PARITY_ABS_TOL)
        and _close(cur_pack.get("profit_factor"), V2_CURRENT_PF, 1e-12)
        and _close(cur_pack.get("max_drawdown_yen_100"), V2_CURRENT_MAX_DD, PARITY_ABS_TOL)
        and _close(e0_ov_pack.get("net_pnl_yen_100"), C0_OVERLAY_LOCKED["NET"], PARITY_ABS_TOL)
        and _close(e0_ov_pack.get("profit_factor"), C0_OVERLAY_LOCKED["PF"], 1e-12)
        and _close(e0_ov_pack.get("max_drawdown_yen_100"), C0_OVERLAY_LOCKED["DD"], PARITY_ABS_TOL)
        and int(e0_paired.get("PAIRED_POS_DAYS") or -1) == int(C0_OVERLAY_LOCKED["PAIRED_POS_DAYS"])
        and int(e0_paired.get("PAIRED_NEG_DAYS") or -1) == int(C0_OVERLAY_LOCKED["PAIRED_NEG_DAYS"])
        and int(e0_paired.get("PAIRED_ZERO_DAYS") or -1) == int(C0_OVERLAY_LOCKED["PAIRED_ZERO_DAYS"])
        and _close(e0_paired.get("PAIRED_MEDIAN_DAILY_DELTA"), C0_OVERLAY_LOCKED["PAIRED_MEDIAN"], PARITY_ABS_TOL)
        and _close(e0_paired.get("EX_BEST_DAY_PNL_DELTA"), C0_OVERLAY_LOCKED["EX_BEST"], PARITY_ABS_TOL)
        and _close(e0_paired.get("EX_TOP3_DAYS_PNL_DELTA"), C0_OVERLAY_LOCKED["EX_TOP3"], PARITY_ABS_TOL)
        and int(e0_aug_pack.get("trade_count") or -1) == int(C0_AUGMENT_LOCKED["TRADE_N"])
        and int(e0_aug_pack.get("win_n") or -1) == int(C0_AUGMENT_LOCKED["WIN_N"])
        and int(e0_aug_pack.get("loss_n") or -1) == int(C0_AUGMENT_LOCKED["LOSS_N"])
        and int(e0_aug_pack.get("flat_n") or -1) == int(C0_AUGMENT_LOCKED["FLAT_N"])
        and _close(e0_aug_pack.get("net_pnl_yen_100"), C0_AUGMENT_LOCKED["NET"], PARITY_ABS_TOL)
        and _close(e0_aug_pack.get("profit_factor"), C0_AUGMENT_LOCKED["PF"], 1e-12)
    )
    print(
        f"E0 CURRENT n={cur_pack.get('trade_count')} net={cur_pack.get('net_pnl_yen_100')} "
        f"overlay_net={e0_ov_pack.get('net_pnl_yen_100')} aug_n={e0_aug_pack.get('trade_count')} "
        f"aug_net={e0_aug_pack.get('net_pnl_yen_100')} replay_ok={replay_ok}",
        flush=True,
    )
    if not replay_ok:
        return _integrity(
            "STOP. Frozen CURRENT / C0 overlay economics did not reproduce.",
            extra={
                "current": _slim(cur_pack),
                "e0_overlay": _slim(e0_ov_pack),
                "e0_augment": _slim(e0_aug_pack),
                "e0_paired": {k: e0_paired.get(k) for k in ("PAIRED_POS_DAYS", "PAIRED_NEG_DAYS", "PAIRED_ZERO_DAYS", "PAIRED_MEDIAN_DAILY_DELTA", "EX_BEST_DAY_PNL_DELTA", "EX_TOP3_DAYS_PNL_DELTA")},
            },
            sha=sha,
        )

    inv = build_inventory()
    elig = [
        r
        for r in inv
        if r.get("date") in set(ELIGIBLE_DAYS)
        and r.get("replay_eligible")
        and r.get("universe_symbols")
        and r.get("capture_path")
    ]
    if len(elig) != len(ELIGIBLE_DAYS):
        return _integrity("STOP. Eligible Capture days mismatch.", sha=sha)
    inv_by = {r["date"]: r for r in elig}

    fills_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for r in tagged:
        if not r.get("_aug_eligible") or int(r.get("Y_FILL5") or 0) != 1:
            continue
        key = str(r.get("_row_key") or row_key(r))
        if key in seen:
            continue
        seen.add(key)
        fills_by[str(r.get("date") or "")].append(
            {
                "date": r.get("date"),
                "anchor": r.get("anchor"),
                "symbol": r.get("symbol"),
                "session": r.get("session") or "AM",
                "fill_t": r.get("fill_t"),
                "fill_price": r.get("fill_price"),
                "exit_t": r.get("exit_t"),
                "exit_price": r.get("exit_price"),
                "exit_reason": r.get("exit_reason"),
                "pnl_yen_100": r.get("pnl_yen_100"),
                "arm": "AUGMENT",
                "row_key": key,
            }
        )
    print(f"C0-eligible fills n={sum(len(v) for v in fills_by.values())}", flush=True)

    CACHE.mkdir(parents=True, exist_ok=True)
    e1_got = []
    e1_jobs = []
    for day in ELIGIBLE_DAYS:
        fp = CACHE / f"{day}_E1.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("date") == day and saved.get("architecture_id") == ARCHITECTURE_ID and saved.get("rows") is not None:
            e1_got.append(saved)
            print(f"E1 cache-hit {day} n={len(saved.get('rows') or [])}", flush=True)
            continue
        if not (fills_by.get(day) or []):
            empty = {"ok": True, "date": day, "rows": [], "leak": {}, "architecture_id": ARCHITECTURE_ID}
            _save_json(fp, empty)
            e1_got.append(empty)
            print(f"E1 empty {day}", flush=True)
            continue
        r = inv_by[day]
        e1_jobs.append(
            {
                "date": day,
                "capture_path": r["capture_path"],
                "universe": r["universe_symbols"],
                "fills": fills_by.get(day) or [],
                "horizons": horizons,
            }
        )
    print(f"e1 jobs={len(e1_jobs)}", flush=True)
    for body in _pool(process_e1_day, e1_jobs, "E1", "date"):
        if body.get("ok"):
            body["architecture_id"] = ARCHITECTURE_ID
            _save_json(CACHE / f"{body.get('date')}_E1.json", body)
        e1_got.append(body)
    fail = [b for b in e1_got if not b.get("ok")]
    if fail or len(e1_got) != len(ELIGIBLE_DAYS):
        return _integrity(
            "STOP. E1 path harvest failed.",
            extra={"fail": [(b.get("date"), b.get("blocker")) for b in fail]},
            sha=sha,
        )

    leak = {
        "FUTURE_EXIT_SIGNAL_USE_N": FUTURE_EXIT_SIGNAL_USE_N,
        "ORACLE_MFE_EXIT_USE_N": ORACLE_MFE_EXIT_USE_N,
        "ENTRY_POLICY_CHANGE_N": ENTRY_POLICY_CHANGE_N,
        "B0_SCORE_MISMATCH_N": 0,
        "B1_SCORE_MISMATCH_N": 0,
        "C0_CONFIRMATION_MISMATCH_N": 0,
        "C0_CANDIDATE_DECISION_MISMATCH_N": 0,
        "EXIT_THRESHOLD_SEARCH_N": EXIT_THRESHOLD_SEARCH_N,
        "TRAILING_THRESHOLD_SEARCH_N": TRAILING_THRESHOLD_SEARCH_N,
        "WAIT_CHANGE_N": WAIT_CHANGE_N,
        "CURRENT_EXIT_CHANGE_N": CURRENT_EXIT_CHANGE_N,
        "RUNTIME_CHANGE_N": RUNTIME_CHANGE_N,
        "PAPER_OPERATION_N": PAPER_OPERATION_N,
        "ITAYOSE_EXIT_USE_N": 0,
        "SPECIAL_BOARD_EXIT_USE_N": 0,
        "INVALID_QUOTE_EXIT_USE_N": 0,
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "C14_REPLAY_MISMATCH_N": 0,
        "E1_JOIN_MISS_N": 0,
        "PATH_MISS_N": 0,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "PAPER_OPERATED": PAPER_OPERATED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "C0_SPEC_SHA256_MATCH": True,
        "BASE_PARITY": True,
    }
    e1_by: dict[str, dict[str, Any]] = {}
    path_for_l2: list[dict[str, Any]] = []
    for body in e1_got:
        lg = body.get("leak") or {}
        for k in (
            "ITAYOSE_EXIT_USE_N",
            "SPECIAL_BOARD_EXIT_USE_N",
            "INVALID_QUOTE_EXIT_USE_N",
            "ORACLE_MFE_EXIT_USE_N",
            "FUTURE_EXIT_SIGNAL_USE_N",
            "ITAYOSE_SKIP_N",
            "SPECIAL_SKIP_N",
            "INVALID_SKIP_N",
        ):
            leak[k] = int(leak.get(k) or 0) + int(lg.get(k) or 0)
        for r in list(body.get("rows") or []):
            if r.get("c14_replay_mismatch"):
                leak["C14_REPLAY_MISMATCH_N"] = int(leak.get("C14_REPLAY_MISMATCH_N") or 0) + 1
            key = str(r.get("row_key") or "")
            if key:
                e1_by[key] = r
            path_for_l2.append(r)
    if int(leak.get("C14_REPLAY_MISMATCH_N") or 0):
        return _integrity("STOP. Harvest C14 did not match labeled C14.", extra={"integrity": leak}, sha=sha)

    e0_aug_keys = {str(t.get("row_key") or row_key(t)) for t in e0_aug}
    rca_l2_src = []
    for day in ELIGIBLE_DAYS:
        for r in list(_load(RCA_CACHE / f"{day}_PATH.json").get("rows") or []):
            if str(r.get("arm") or "") != "AUGMENT":
                continue
            rk = str(r.get("row_key") or "")
            if rk and rk in e0_aug_keys:
                rca_l2_src.append(r)
    l2_src = rca_l2_src
    if len(join_l2_keys(l2_src)) != int(C0_AUGMENT_LOCKED["L2_BASE_N"]):
        l2_src = [r for r in path_for_l2 if str(r.get("row_key") or "") in e0_aug_keys]
        for r in l2_src:
            if r.get("realized_pnl_yen_100") is None:
                r["realized_pnl_yen_100"] = _f(r.get("c14_pnl_yen_100"))
    l2_keys = join_l2_keys(l2_src)
    if len(l2_keys) != int(C0_AUGMENT_LOCKED["L2_BASE_N"]):
        return _integrity(
            f"STOP. L2_BASE_N reproduced {len(l2_keys)} != {C0_AUGMENT_LOCKED['L2_BASE_N']}.",
            extra={"l2_keys": sorted(l2_keys)},
            sha=sha,
        )

    fixed = fixed_trade_effect(e0_aug, e1_by, l2_keys)
    if int(fixed.get("FIXED_TRADE_N") or -1) != int(C0_AUGMENT_LOCKED["TRADE_N"]):
        return _integrity("STOP. FIXED_TRADE_N != 26.", extra={"fixed": {k: v for k, v in fixed.items() if k != "rows"}}, sha=sha)
    print(
        f"FIXED n={fixed.get('FIXED_TRADE_N')} delta_net={fixed.get('DELTA_FIXED_NET')} "
        f"ptl={fixed.get('PTL_TRIGGER_N')} l2_ptl={fixed.get('L2_PTL_TRIGGER_N')} "
        f"l2_conv={fixed.get('L2_CONVERTED_TO_NONLOSS_N')} win_cut={fixed.get('WINNER_EARLY_CUT_N')}",
        flush=True,
    )

    tagged_e1, e1_miss = apply_e1_exits(tagged, e1_by)
    leak["E1_JOIN_MISS_N"] = int(e1_miss)
    if e1_miss:
        return _integrity("STOP. E1 join miss on C0-eligible fills.", extra={"integrity": leak}, sha=sha)

    e1_overlay = overlay_replay(tagged_e1, include_augment=True, augment_rank="utility")
    ident = identity_audit(tagged, tagged_e1, e0_overlay, e1_overlay, c0_keys)
    for k in (
        "C0_CANDIDATE_DECISION_MISMATCH_N",
        "B0_SCORE_MISMATCH_N",
        "B1_SCORE_MISMATCH_N",
        "C0_CONFIRMATION_MISMATCH_N",
        "ENTRY_POLICY_CHANGE_N",
    ):
        leak[k] = int(ident.get(k) or 0)
    if int(ident.get("TAGGED_LEN_MISMATCH_N") or 0):
        leak["C0_CONFIRMATION_MISMATCH_N"] = int(leak.get("C0_CONFIRMATION_MISMATCH_N") or 0) + int(
            ident.get("TAGGED_LEN_MISMATCH_N") or 0
        )

    e1_trades = list(e1_overlay.get("trades") or [])
    e1_aug = [t for t in e1_trades if str(t.get("arm") or "") == "AUGMENT"]
    e1_ov_pack = economic_pack(e1_trades, list(ELIGIBLE_DAYS))
    e1_aug_pack = economic_pack(e1_aug, list(ELIGIBLE_DAYS))
    e1_paired = paired_delta(list(e1_ov_pack.get("daily") or []), list(cur_pack.get("daily") or []))
    pres = preservation_audit(baseline, e1_overlay)
    cur_counts = current_trade_counts(baseline, e1_overlay)
    pres.update(cur_counts)
    pres["CURRENT_PRESERVATION_PASS"] = preservation_ok(pres)
    leak["CURRENT_EXIT_CHANGE_N"] = int(pres.get("CURRENT_EXIT_MISMATCH_N") or 0) + int(CURRENT_EXIT_CHANGE_N)
    conc = profit_concentration(list(e1_aug_pack.get("daily") or []))
    fx_metrics = e1_augment_exit_metrics(e1_aug, fixed)
    vs = vs_c0(
        {
            "OVERLAY_NET_PNL": e1_ov_pack.get("net_pnl_yen_100"),
            "OVERLAY_PF": e1_ov_pack.get("profit_factor"),
            "OVERLAY_MAX_DD": e1_ov_pack.get("max_drawdown_yen_100"),
            "PAIRED_POS_DAYS": e1_paired.get("PAIRED_POS_DAYS"),
            "PAIRED_NEG_DAYS": e1_paired.get("PAIRED_NEG_DAYS"),
            "PAIRED_MEDIAN_DAILY_DELTA": e1_paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
            "EX_BEST_DAY_PNL_DELTA": e1_paired.get("EX_BEST_DAY_PNL_DELTA"),
            "EX_TOP3_DAYS_PNL_DELTA": e1_paired.get("EX_TOP3_DAYS_PNL_DELTA"),
        }
    )

    integrity_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO_KEYS)
    gate = e1_full_gate(
        e1_ov_pack,
        cur_pack,
        e1_paired,
        preservation_ok=bool(pres.get("CURRENT_PRESERVATION_PASS")),
        integrity_ok=integrity_ok,
    )
    decision = decide_case(
        integrity_ok=integrity_ok,
        preservation_ok=bool(pres.get("CURRENT_PRESERVATION_PASS")),
        full_pass=bool(gate.get("E1_FULL_PASS")),
        overlay_net=float(e1_ov_pack.get("net_pnl_yen_100") or 0.0),
        overlay_pf=_pf_num(e1_ov_pack.get("profit_factor")),
        overlay_dd=float(e1_ov_pack.get("max_drawdown_yen_100") or 0.0),
        paired_median=e1_paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
        paired_pos=int(e1_paired.get("PAIRED_POS_DAYS") or 0),
        paired_neg=int(e1_paired.get("PAIRED_NEG_DAYS") or 0),
        ex_best=e1_paired.get("EX_BEST_DAY_PNL_DELTA"),
        ex_top3=e1_paired.get("EX_TOP3_DAYS_PNL_DELTA"),
        winner_early_cut_n=int(fixed.get("WINNER_EARLY_CUT_N") or 0),
        l2_converted_n=int(fixed.get("L2_CONVERTED_TO_NONLOSS_N") or 0),
        l2_ptl_n=int(fixed.get("L2_PTL_TRIGGER_N") or 0),
        l2_reduced_n=int(fixed.get("BASELINE_L2_REDUCED_LOSS_N") or 0),
    )
    if not integrity_ok:
        return _integrity("STOP. Integrity counters non-zero.", extra={"integrity": leak, "identity": ident}, sha=sha)

    aug_adm = [a for a in (e1_overlay.get("admissions") or []) if str(a.get("arm") or "") == "AUGMENT"]
    aug_fill = [f for f in (e1_overlay.get("fills") or []) if str(f.get("arm") or "") == "AUGMENT"]
    candidates = list(e1_overlay.get("augment_candidates") or [])
    required = {
        "BASE_PARITY": True,
        "EXIT_ARCHITECTURE": ARCHITECTURE_ID,
        "EXIT_PRECOMMIT_SPEC_SHA256": sha,
        "FIXED_TRADE_N": fixed.get("FIXED_TRADE_N"),
        "FIXED_DELTA_NET": fixed.get("DELTA_FIXED_NET"),
        "L2_BASE_N": fixed.get("L2_BASE_N"),
        "PTL_TRIGGER_N": fx_metrics.get("PTL_TRIGGER_N"),
        "L2_PTL_TRIGGER_N": fixed.get("L2_PTL_TRIGGER_N"),
        "L2_CONVERTED_TO_NONLOSS_N": fixed.get("L2_CONVERTED_TO_NONLOSS_N"),
        "WINNER_EARLY_CUT_N": fixed.get("WINNER_EARLY_CUT_N"),
        "LOSER_PNL_SAVED_BY_GUARD": fixed.get("LOSER_PNL_SAVED_BY_GUARD"),
        "WINNER_PNL_LOST_BY_GUARD": fixed.get("WINNER_PNL_LOST_BY_GUARD"),
        "E1_AUGMENT_TRADE_N": e1_aug_pack.get("trade_count"),
        "E1_AUGMENT_NET": e1_aug_pack.get("net_pnl_yen_100"),
        "E1_AUGMENT_PF": e1_aug_pack.get("profit_factor"),
        "E1_OVERLAY_NET": e1_ov_pack.get("net_pnl_yen_100"),
        "E1_OVERLAY_PF": e1_ov_pack.get("profit_factor"),
        "E1_OVERLAY_DD": e1_ov_pack.get("max_drawdown_yen_100"),
        "PAIRED_POS_DAYS": e1_paired.get("PAIRED_POS_DAYS"),
        "PAIRED_NEG_DAYS": e1_paired.get("PAIRED_NEG_DAYS"),
        "PAIRED_ZERO_DAYS": e1_paired.get("PAIRED_ZERO_DAYS"),
        "PAIRED_MEDIAN": e1_paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
        "EX_BEST": e1_paired.get("EX_BEST_DAY_PNL_DELTA"),
        "EX_TOP3": e1_paired.get("EX_TOP3_DAYS_PNL_DELTA"),
        "DELTA_NET_VS_C0": vs.get("DELTA_NET_VS_C0"),
        "DELTA_PF_VS_C0": vs.get("DELTA_PF_VS_C0"),
        "DELTA_DD_VS_C0": vs.get("DELTA_DD_VS_C0"),
        "DELTA_PAIRED_MEDIAN_VS_C0": vs.get("DELTA_PAIRED_MEDIAN_VS_C0"),
        "CURRENT_PRESERVATION_PASS": bool(pres.get("CURRENT_PRESERVATION_PASS")),
        "E1_FULL_PASS": bool(gate.get("E1_FULL_PASS")),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "ENTRY_PARENT_SHA256": ENTRY_PARENT_SHA256,
    }
    extra = {
        "precommit": spec,
        "decision": decision,
        "gates": gate.get("gates"),
        "fixed": {k: v for k, v in fixed.items() if k != "rows"},
        "e1_exit_metrics": fx_metrics,
        "vs_c0": vs,
        "e0": {
            "overlay": _slim(e0_ov_pack),
            "augment": _slim(e0_aug_pack),
            "paired": {k: e0_paired.get(k) for k in ("PAIRED_POS_DAYS", "PAIRED_NEG_DAYS", "PAIRED_ZERO_DAYS", "PAIRED_MEDIAN_DAILY_DELTA", "EX_BEST_DAY_PNL_DELTA", "EX_TOP3_DAYS_PNL_DELTA")},
        },
        "e1": {
            "AUGMENT_CANDIDATE_N": len(candidates),
            "AUGMENT_ADMITTED_N": len(aug_adm),
            "AUGMENT_FILL_N": len(aug_fill),
            "AUGMENT_FILL_RATE": _arm_fill_rate(len(aug_adm), len(aug_fill)),
            "AUGMENT_TRADE_N": e1_aug_pack.get("trade_count"),
            "AUGMENT_NET_PNL": e1_aug_pack.get("net_pnl_yen_100"),
            "AUGMENT_GROSS_PROFIT": e1_aug_pack.get("gross_profit"),
            "AUGMENT_GROSS_LOSS": e1_aug_pack.get("gross_loss"),
            "AUGMENT_PF": e1_aug_pack.get("profit_factor"),
            "AUGMENT_MAX_DD": e1_aug_pack.get("max_drawdown_yen_100"),
            "AUGMENT_WIN_N": e1_aug_pack.get("win_n"),
            "AUGMENT_LOSS_N": e1_aug_pack.get("loss_n"),
            "AUGMENT_FLAT_N": e1_aug_pack.get("flat_n"),
            "OVERLAY_TRADE_N": e1_ov_pack.get("trade_count"),
            "OVERLAY_NET_PNL": e1_ov_pack.get("net_pnl_yen_100"),
            "OVERLAY_PF": e1_ov_pack.get("profit_factor"),
            "OVERLAY_MAX_DD": e1_ov_pack.get("max_drawdown_yen_100"),
            **conc,
        },
        "preservation": pres,
        "identity": ident,
        "horizons": horizons,
        "integrity": leak,
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "POSITION_CAP": POSITION_CAP,
        "note": "Original C0 remains FROZEN_FOR_FUTURE_OOS_ONLY. This is C0_EXIT_PTL_GUARD_120_600_V1 only.",
    }
    daily_rows = []
    for d in ELIGIBLE_DAYS:
        cday = next((x for x in (cur_pack.get("daily") or []) if x.get("date") == d), {})
        e0d = next((x for x in (e0_ov_pack.get("daily") or []) if x.get("date") == d), {})
        e1d = next((x for x in (e1_ov_pack.get("daily") or []) if x.get("date") == d), {})
        a0 = next((x for x in (e0_aug_pack.get("daily") or []) if x.get("date") == d), {})
        a1 = next((x for x in (e1_aug_pack.get("daily") or []) if x.get("date") == d), {})
        daily_rows.append(
            {
                "date": d,
                "CURRENT": cday.get("pnl_yen_100"),
                "E0_OVERLAY": e0d.get("pnl_yen_100"),
                "E1_OVERLAY": e1d.get("pnl_yen_100"),
                "E0_AUGMENT": a0.get("pnl_yen_100"),
                "E1_AUGMENT": a1.get("pnl_yen_100"),
            }
        )
    sheets = {
        "summary": kv_rows(required),
        "precommit": kv_rows({**spec, "EXIT_PRECOMMIT_SPEC_SHA256": sha, "PRECOMMIT_LOCKED": True}),
        "fixed_trades": list(fixed.get("rows") or []),
        "e1_augment": [
            {
                "date": t.get("date"),
                "symbol": t.get("symbol"),
                "anchor": t.get("anchor"),
                "t0": t.get("t0"),
                "fill_time": t.get("fill_time"),
                "exit_time": t.get("exit_time"),
                "exit_reason": t.get("exit_reason"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "row_key": t.get("row_key"),
            }
            for t in e1_aug
        ],
        "daily_pnl": daily_rows,
        "current_preservation": kv_rows(pres),
        "integrity": kv_rows(leak),
    }
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, **extra}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(f"VERDICT {required.get('VERDICT')}", flush=True)
    print(f"NEXT {required.get('NEXT')}", flush=True)
    print(
        f"E1 overlay_net={required.get('E1_OVERLAY_NET')} pf={required.get('E1_OVERLAY_PF')} "
        f"dd={required.get('E1_OVERLAY_DD')} pass={required.get('E1_FULL_PASS')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
