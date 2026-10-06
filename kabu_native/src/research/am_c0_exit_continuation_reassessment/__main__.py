"""Offline AM C0 EXIT continuation reassessment. Frozen C0 ENTRY. CURRENT keeps C14. No Paper."""
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

from research.am_c0_exit_continuation_reassessment import (
    ANALYSIS_ID,
    ARM_IDS,
    C0_AUGMENT_LOCKED,
    C0_OVERLAY_LOCKED,
    CONTINUATION_ARM_N,
    CONTINUATION_THRESHOLD_SEARCH_N,
    CURRENT_EXIT_CHANGE_N,
    CURRENT_LOCKED,
    E0,
    E1,
    E2,
    ENTRY_PARENT_SHA256,
    ENTRY_POLICY_CHANGE_N,
    EXIT_HORIZON_SEARCH_N,
    FUTURE_EXIT_SIGNAL_USE_N,
    ORACLE_MFE_EXIT_USE_N,
    PAPER_OPERATION_N,
    PTL_REUSE_N,
    RUNTIME_CHANGE_N,
    WAIT_CHANGE_N,
)
from research.am_c0_exit_continuation_reassessment.analyze import (
    decide_case,
    e_full_gate,
    fixed_arm,
    join_class_keys,
    pick_best,
    preservation_ok,
    vs_c0,
)
from research.am_c0_exit_continuation_reassessment.continue_exit import apply_arm_exits
from research.am_c0_exit_continuation_reassessment.harvest import process_cont_day
from research.am_c0_exit_continuation_reassessment.precommit import precommit_spec, print_precommit, spec_sha256
from research.am_c0_exit_continuation_reassessment.publish import OUT, build_markdown, kv_rows, write_artifacts
from research.am_c0_exit_ptl_guard.analyze import current_trade_counts, identity_audit
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
from research.am_entry_profit_improvement.metrics import economic_pack, paired_delta
from research.am_entry_research_final_decision.spec import canonical_c0_spec
from research.am_entry_research_final_decision.spec import spec_sha256 as c0_spec_sha256
from research.am_exit_contribution_rca.analyze import L2, L3
from research.am_exit_contribution_rca.contract import c14_horizons
from research.am_expanded_entry_risk_integration.analyze import profit_concentration
from research.anchor_timing_robustness.inventory import build_inventory
from research.canonical_entry_performance_rebase.analyze import row_key, session_of
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
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_c0_exit_continuation_reassessment"
RCA_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_exit_contribution_rca"

INTEGRITY_ZERO_KEYS = (
    "FUTURE_EXIT_SIGNAL_USE_N",
    "ORACLE_MFE_EXIT_USE_N",
    "ENTRY_POLICY_CHANGE_N",
    "B0_SCORE_MISMATCH_N",
    "B1_SCORE_MISMATCH_N",
    "C0_CONFIRMATION_MISMATCH_N",
    "C0_CANDIDATE_DECISION_MISMATCH_N",
    "CONTINUATION_THRESHOLD_SEARCH_N",
    "EXIT_HORIZON_SEARCH_N",
    "PTL_REUSE_N",
    "WAIT_CHANGE_N",
    "CURRENT_EXIT_CHANGE_N",
    "RUNTIME_CHANGE_N",
    "PAPER_OPERATION_N",
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


def _slim(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k not in ("trades", "daily")}


def _empty_required(*, verdict: str, nxt: str, sha: str | None = None) -> dict[str, Any]:
    keys = (
        "BASE_PARITY",
        "CONTINUATION_ARM_N",
        "E0_NET",
        "E0_PF",
        "E0_DD",
        "E1_FORCED_EXTEND_N",
        "E1_FIXED_DELTA_NET",
        "E1_OVERLAY_NET",
        "E1_OVERLAY_PF",
        "E1_OVERLAY_DD",
        "E1_PAIRED_MEDIAN",
        "E1_POS_DAYS",
        "E1_NEG_DAYS",
        "E1_EX_BEST",
        "E1_EX_TOP3",
        "E2_FORCED_EXTEND_N",
        "E2_FIXED_DELTA_NET",
        "E2_OVERLAY_NET",
        "E2_OVERLAY_PF",
        "E2_OVERLAY_DD",
        "E2_PAIRED_MEDIAN",
        "E2_POS_DAYS",
        "E2_NEG_DAYS",
        "E2_EX_BEST",
        "E2_EX_TOP3",
        "PASS_ARM_N",
        "BEST_PASS_ARM",
        "CURRENT_PRESERVATION_PASS_ALL",
        "TRUE_OOS",
        "NEW_FORWARD_N",
        "VERDICT",
        "NEXT",
    )
    req = {k: None for k in keys}
    req["CONTINUATION_ARM_N"] = CONTINUATION_ARM_N
    req["TRUE_OOS"] = TRUE_OOS
    req["NEW_FORWARD_N"] = NEW_FORWARD_N
    req["VERDICT"] = verdict
    req["NEXT"] = nxt
    req["BASE_PARITY"] = False
    req["CURRENT_PRESERVATION_PASS_ALL"] = False
    req["PASS_ARM_N"] = 0
    req["PRECOMMIT_SPEC_SHA256"] = sha
    return req


def _integrity(msg: str, extra: dict | None = None, *, sha: str | None = None) -> int:
    decision = decide_case(integrity_ok=False, preservation_all=False, arms=[])
    required = _empty_required(verdict=str(decision.get("VERDICT")), nxt=str(decision.get("NEXT")), sha=sha)
    required["STOP_REASON"] = msg
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **(extra or {})}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, {"summary": kv_rows(required), "integrity": kv_rows({"STOP_REASON": msg})})
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


def _eval_overlay(
    tagged: list[dict[str, Any]],
    baseline: dict[str, Any],
    base_pack: dict[str, Any],
    arm_id: str,
    *,
    integrity_ok: bool,
    c0_keys: set[str],
    tagged_e0: list[dict[str, Any]],
    e0_overlay: dict[str, Any],
) -> dict[str, Any]:
    overlay = overlay_replay(tagged, include_augment=True, augment_rank="utility")
    ident = identity_audit(tagged_e0, tagged, e0_overlay, overlay, c0_keys)
    ov_trades = list(overlay.get("trades") or [])
    aug_trades = [t for t in ov_trades if str(t.get("arm") or "") == "AUGMENT"]
    ov_pack = economic_pack(ov_trades, list(ELIGIBLE_DAYS))
    aug_pack = economic_pack(aug_trades, list(ELIGIBLE_DAYS))
    paired = paired_delta(list(ov_pack.get("daily") or []), list(base_pack.get("daily") or []))
    pres = preservation_audit(baseline, overlay)
    pres.update(current_trade_counts(baseline, overlay))
    pres["CURRENT_PRESERVATION_PASS"] = preservation_ok(pres)
    gate = e_full_gate(
        ov_pack,
        base_pack,
        paired,
        preservation_ok_flag=bool(pres.get("CURRENT_PRESERVATION_PASS")),
        integrity_ok=integrity_ok,
    )
    conc = profit_concentration(list(aug_pack.get("daily") or []))
    aug_adm = [a for a in (overlay.get("admissions") or []) if str(a.get("arm") or "") == "AUGMENT"]
    aug_fill = [f for f in (overlay.get("fills") or []) if str(f.get("arm") or "") == "AUGMENT"]
    rec = {
        "architecture_id": arm_id,
        "AUGMENT_CANDIDATE_N": len(list(overlay.get("augment_candidates") or [])),
        "AUGMENT_ADMITTED_N": len(aug_adm),
        "AUGMENT_FILL_N": len(aug_fill),
        "AUGMENT_FILL_RATE": _arm_fill_rate(len(aug_adm), len(aug_fill)),
        "AUGMENT_TRADE_N": aug_pack.get("trade_count"),
        "AUGMENT_NET_PNL": aug_pack.get("net_pnl_yen_100"),
        "AUGMENT_GROSS_PROFIT": aug_pack.get("gross_profit"),
        "AUGMENT_GROSS_LOSS": aug_pack.get("gross_loss"),
        "AUGMENT_PF": aug_pack.get("profit_factor"),
        "AUGMENT_MAX_DD": aug_pack.get("max_drawdown_yen_100"),
        "AUGMENT_WIN_N": aug_pack.get("win_n"),
        "AUGMENT_LOSS_N": aug_pack.get("loss_n"),
        "AUGMENT_FLAT_N": aug_pack.get("flat_n"),
        "OVERLAY_TRADE_N": ov_pack.get("trade_count"),
        "OVERLAY_NET_PNL": ov_pack.get("net_pnl_yen_100"),
        "OVERLAY_PF": ov_pack.get("profit_factor"),
        "OVERLAY_MAX_DD": ov_pack.get("max_drawdown_yen_100"),
        "PAIRED_POS_DAYS": paired.get("PAIRED_POS_DAYS"),
        "PAIRED_NEG_DAYS": paired.get("PAIRED_NEG_DAYS"),
        "PAIRED_ZERO_DAYS": paired.get("PAIRED_ZERO_DAYS"),
        "PAIRED_MEDIAN_DAILY_DELTA": paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
        "EX_BEST_DAY_PNL_DELTA": paired.get("EX_BEST_DAY_PNL_DELTA"),
        "EX_TOP3_DAYS_PNL_DELTA": paired.get("EX_TOP3_DAYS_PNL_DELTA"),
        "CURRENT_PRESERVATION_PASS": pres.get("CURRENT_PRESERVATION_PASS"),
        "ARM_PASS": gate.get("ARM_PASS"),
        "gates": gate.get("gates"),
        "preservation": pres,
        "identity": ident,
        "overlay_daily": list(ov_pack.get("daily") or []),
        "augment_daily": list(aug_pack.get("daily") or []),
        "overlay": overlay,
    }
    rec.update(conc)
    rec.update(vs_c0(rec))
    return rec


def _req_arm(arm: dict[str, Any] | None, prefix: str) -> dict[str, Any]:
    a = arm or {}
    fx = a.get("fixed") or {}
    return {
        f"{prefix}_FORCED_EXTEND_N": fx.get("FORCED_EXTEND_N"),
        f"{prefix}_FIXED_DELTA_NET": fx.get("DELTA_NET_VS_E0"),
        f"{prefix}_OVERLAY_NET": a.get("OVERLAY_NET_PNL"),
        f"{prefix}_OVERLAY_PF": a.get("OVERLAY_PF"),
        f"{prefix}_OVERLAY_DD": a.get("OVERLAY_MAX_DD"),
        f"{prefix}_PAIRED_MEDIAN": a.get("PAIRED_MEDIAN_DAILY_DELTA"),
        f"{prefix}_POS_DAYS": a.get("PAIRED_POS_DAYS"),
        f"{prefix}_NEG_DAYS": a.get("PAIRED_NEG_DAYS"),
        f"{prefix}_EX_BEST": a.get("EX_BEST_DAY_PNL_DELTA"),
        f"{prefix}_EX_TOP3": a.get("EX_TOP3_DAYS_PNL_DELTA"),
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get(
        "PYTHONPATH", ""
    )
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM C0 EXIT CONTINUATION REASSESSMENT V1", flush=True)
    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        return _integrity("STOP. Runtime WAIT_SEC drifted from 1.0.")
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        return _integrity("STOP. DEV_WAIT_SEC drifted from 5.0.")
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
    if int(PTL_REUSE_N) != 0:
        return _integrity("STOP. PTL reuse counter drifted.")

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
        f"replay_ok={replay_ok}",
        flush=True,
    )
    if not replay_ok:
        return _integrity(
            "STOP. Frozen CURRENT / C0 overlay economics did not reproduce.",
            extra={"current": _slim(cur_pack), "e0_overlay": _slim(e0_ov_pack), "e0_augment": _slim(e0_aug_pack)},
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
    cont_got = []
    cont_jobs = []
    for day in ELIGIBLE_DAYS:
        fp = CACHE / f"{day}_CONT.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("date") == day and saved.get("rows") is not None:
            cont_got.append(saved)
            print(f"CONT cache-hit {day} n={len(saved.get('rows') or [])}", flush=True)
            continue
        if not (fills_by.get(day) or []):
            empty = {"ok": True, "date": day, "rows": [], "leak": {}}
            _save_json(fp, empty)
            cont_got.append(empty)
            print(f"CONT empty {day}", flush=True)
            continue
        r = inv_by[day]
        cont_jobs.append(
            {
                "date": day,
                "capture_path": r["capture_path"],
                "universe": r["universe_symbols"],
                "fills": fills_by.get(day) or [],
            }
        )
    print(f"cont jobs={len(cont_jobs)}", flush=True)
    for body in _pool(process_cont_day, cont_jobs, "CONT", "date"):
        if body.get("ok"):
            _save_json(CACHE / f"{body.get('date')}_CONT.json", body)
        cont_got.append(body)
    fail = [b for b in cont_got if not b.get("ok")]
    if fail or len(cont_got) != len(ELIGIBLE_DAYS):
        return _integrity(
            "STOP. Continuation harvest failed.",
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
        "CONTINUATION_THRESHOLD_SEARCH_N": CONTINUATION_THRESHOLD_SEARCH_N,
        "EXIT_HORIZON_SEARCH_N": EXIT_HORIZON_SEARCH_N,
        "PTL_REUSE_N": PTL_REUSE_N,
        "WAIT_CHANGE_N": WAIT_CHANGE_N,
        "CURRENT_EXIT_CHANGE_N": CURRENT_EXIT_CHANGE_N,
        "RUNTIME_CHANGE_N": RUNTIME_CHANGE_N,
        "PAPER_OPERATION_N": PAPER_OPERATION_N,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "C14_REPLAY_MISMATCH_N": 0,
        "E_JOIN_MISS_N": 0,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "PAPER_OPERATED": PAPER_OPERATED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "C0_SPEC_SHA256_MATCH": True,
        "BASE_PARITY": True,
    }
    by_key: dict[str, dict[str, Any]] = {}
    for body in cont_got:
        lg = body.get("leak") or {}
        for k in ("ITAYOSE_SKIP_N", "SPECIAL_SKIP_N", "INVALID_SKIP_N", "PTL_REUSE_N", "ORACLE_MFE_EXIT_USE_N", "FUTURE_EXIT_SIGNAL_USE_N"):
            leak[k] = int(leak.get(k) or 0) + int(lg.get(k) or 0)
        for r in list(body.get("rows") or []):
            if r.get("c14_replay_mismatch"):
                leak["C14_REPLAY_MISMATCH_N"] = int(leak.get("C14_REPLAY_MISMATCH_N") or 0) + 1
            key = str(r.get("row_key") or "")
            if key:
                by_key[key] = r
    if int(leak.get("C14_REPLAY_MISMATCH_N") or 0):
        return _integrity("STOP. Harvest C14 did not match labeled C14.", extra={"integrity": leak}, sha=sha)

    e0_aug_keys = {str(t.get("row_key") or row_key(t)) for t in e0_aug}
    rca_src = []
    for day in ELIGIBLE_DAYS:
        for r in list(_load(RCA_CACHE / f"{day}_PATH.json").get("rows") or []):
            if str(r.get("arm") or "") != "AUGMENT":
                continue
            rk = str(r.get("row_key") or "")
            if rk and rk in e0_aug_keys:
                rca_src.append(r)
    l2_keys = join_class_keys(rca_src, L2)
    l3_keys = join_class_keys(rca_src, L3)
    if len(l2_keys) != int(C0_AUGMENT_LOCKED["L2_N"]) or len(l3_keys) != int(C0_AUGMENT_LOCKED["L3_N"]):
        return _integrity(
            f"STOP. L2/L3 reproduced {len(l2_keys)}/{len(l3_keys)} != {C0_AUGMENT_LOCKED['L2_N']}/{C0_AUGMENT_LOCKED['L3_N']}.",
            extra={"l2": sorted(l2_keys), "l3": sorted(l3_keys)},
            sha=sha,
        )

    fx0 = fixed_arm(e0_aug, by_key, arm_id=E0, l2_keys=l2_keys, l3_keys=l3_keys)
    fx1 = fixed_arm(e0_aug, by_key, arm_id=E1, l2_keys=l2_keys, l3_keys=l3_keys)
    fx2 = fixed_arm(e0_aug, by_key, arm_id=E2, l2_keys=l2_keys, l3_keys=l3_keys)
    if int(fx0.get("FIXED_TRADE_N") or -1) != 26:
        return _integrity("STOP. FIXED_TRADE_N != 26.", sha=sha)
    print(
        f"FIXED E1 delta={fx1.get('DELTA_NET_VS_E0')} force={fx1.get('FORCED_EXTEND_N')} "
        f"E2 delta={fx2.get('DELTA_NET_VS_E0')} force={fx2.get('FORCED_EXTEND_N')}",
        flush=True,
    )

    tagged_e1, miss1 = apply_arm_exits(tagged, by_key, arm_id=E1)
    tagged_e2, miss2 = apply_arm_exits(tagged, by_key, arm_id=E2)
    leak["E_JOIN_MISS_N"] = int(miss1) + int(miss2)
    if miss1 or miss2:
        return _integrity("STOP. Arm exit join miss.", extra={"integrity": leak}, sha=sha)

    integrity_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO_KEYS)
    e0_rec = _eval_overlay(
        tagged, baseline, cur_pack, E0, integrity_ok=integrity_ok, c0_keys=c0_keys, tagged_e0=tagged, e0_overlay=e0_overlay
    )
    e1_rec = _eval_overlay(
        tagged_e1, baseline, cur_pack, E1, integrity_ok=integrity_ok, c0_keys=c0_keys, tagged_e0=tagged, e0_overlay=e0_overlay
    )
    e2_rec = _eval_overlay(
        tagged_e2, baseline, cur_pack, E2, integrity_ok=integrity_ok, c0_keys=c0_keys, tagged_e0=tagged, e0_overlay=e0_overlay
    )
    e0_rec["fixed"] = {k: v for k, v in fx0.items() if k not in ("rows", "diag_rows")}
    e1_rec["fixed"] = {k: v for k, v in fx1.items() if k not in ("rows", "diag_rows")}
    e2_rec["fixed"] = {k: v for k, v in fx2.items() if k not in ("rows", "diag_rows")}

    for rec in (e0_rec, e1_rec, e2_rec):
        ident = rec.get("identity") or {}
        for k in ("B0_SCORE_MISMATCH_N", "B1_SCORE_MISMATCH_N", "C0_CONFIRMATION_MISMATCH_N", "C0_CANDIDATE_DECISION_MISMATCH_N", "ENTRY_POLICY_CHANGE_N"):
            leak[k] = int(leak.get(k) or 0) + int(ident.get(k) or 0)
        leak["CURRENT_EXIT_CHANGE_N"] = int(leak.get("CURRENT_EXIT_CHANGE_N") or 0) + int(
            (rec.get("preservation") or {}).get("CURRENT_EXIT_MISMATCH_N") or 0
        )
    integrity_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO_KEYS)
    if not integrity_ok:
        return _integrity("STOP. Integrity counters non-zero.", extra={"integrity": leak}, sha=sha)

    # Recompute ARM_PASS with final integrity
    for rec in (e0_rec, e1_rec, e2_rec):
        gate = e_full_gate(
            {
                "net_pnl_yen_100": rec.get("OVERLAY_NET_PNL"),
                "profit_factor": rec.get("OVERLAY_PF"),
                "max_drawdown_yen_100": rec.get("OVERLAY_MAX_DD"),
            },
            cur_pack,
            {
                "PAIRED_MEDIAN_DAILY_DELTA": rec.get("PAIRED_MEDIAN_DAILY_DELTA"),
                "PAIRED_POS_DAYS": rec.get("PAIRED_POS_DAYS"),
                "PAIRED_NEG_DAYS": rec.get("PAIRED_NEG_DAYS"),
                "EX_BEST_DAY_PNL_DELTA": rec.get("EX_BEST_DAY_PNL_DELTA"),
                "EX_TOP3_DAYS_PNL_DELTA": rec.get("EX_TOP3_DAYS_PNL_DELTA"),
            },
            preservation_ok_flag=bool(rec.get("CURRENT_PRESERVATION_PASS")),
            integrity_ok=True,
        )
        rec["gates"] = gate.get("gates")
        rec["ARM_PASS"] = gate.get("ARM_PASS")

    arms = [e0_rec, e1_rec, e2_rec]
    preservation_all = all(bool(a.get("CURRENT_PRESERVATION_PASS")) for a in (e1_rec, e2_rec, e0_rec))
    decision = decide_case(integrity_ok=True, preservation_all=preservation_all, arms=arms)
    passed = [a for a in (e1_rec, e2_rec) if bool(a.get("ARM_PASS"))]
    best = pick_best(passed) if passed else None
    best_id = (best or {}).get("architecture_id")

    required = {
        "BASE_PARITY": True,
        "CONTINUATION_ARM_N": CONTINUATION_ARM_N,
        "E0_NET": e0_ov_pack.get("net_pnl_yen_100"),
        "E0_PF": e0_ov_pack.get("profit_factor"),
        "E0_DD": e0_ov_pack.get("max_drawdown_yen_100"),
        **_req_arm(e1_rec, "E1"),
        **_req_arm(e2_rec, "E2"),
        "PASS_ARM_N": len(passed),
        "BEST_PASS_ARM": best_id,
        "CURRENT_PRESERVATION_PASS_ALL": bool(preservation_all),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "ENTRY_PARENT_SHA256": ENTRY_PARENT_SHA256,
        "PRECOMMIT_SPEC_SHA256": sha,
    }

    drop = ("overlay_daily", "augment_daily", "preservation", "gates", "identity", "overlay", "fixed")
    arm_sheet = []
    for a in arms:
        row = {k: v for k, v in a.items() if k not in drop}
        row.update(a.get("fixed") or {})
        arm_sheet.append(row)

    daily_rows = []
    for d in ELIGIBLE_DAYS:
        cday = next((x for x in (cur_pack.get("daily") or []) if x.get("date") == d), {})
        rec = {"date": d, "CURRENT": cday.get("pnl_yen_100")}
        for a in arms:
            ad = next((x for x in (a.get("overlay_daily") or []) if x.get("date") == d), {})
            gd = next((x for x in (a.get("augment_daily") or []) if x.get("date") == d), {})
            rec[str(a.get("architecture_id"))] = ad.get("pnl_yen_100")
            rec[f"{a.get('architecture_id')}_AUG"] = gd.get("pnl_yen_100")
        daily_rows.append(rec)

    extra = {
        "precommit": spec,
        "decision": decision,
        "horizons": horizons,
        "e0": {"overlay": _slim(e0_ov_pack), "augment": _slim(e0_aug_pack)},
        "arms": arm_sheet,
        "fixed": {E0: e0_rec["fixed"], E1: e1_rec["fixed"], E2: e2_rec["fixed"]},
        "gates": {a.get("architecture_id"): a.get("gates") for a in arms},
        "preservation": {a.get("architecture_id"): a.get("preservation") for a in arms},
        "identity": {a.get("architecture_id"): a.get("identity") for a in arms},
        "integrity": leak,
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "POSITION_CAP": POSITION_CAP,
        "note": "Original C0 remains FROZEN_FOR_FUTURE_OOS_ONLY. Force CONT_EXIT_600 into existing CONT_EXTEND_750 only.",
    }
    sheets = {
        "summary": kv_rows(required),
        "precommit": kv_rows({**spec, "PRECOMMIT_SPEC_SHA256": sha, "PRECOMMIT_LOCKED": True}),
        "fixed_trades": list(fx0.get("rows") or []) + list(fx1.get("rows") or []) + list(fx2.get("rows") or []),
        "extend_diag": list(fx1.get("diag_rows") or []) + list(fx2.get("diag_rows") or []),
        "arms": arm_sheet,
        "daily_pnl": daily_rows,
        "current_preservation": kv_rows(e1_rec.get("preservation") or {}) + kv_rows({"arm": E2, **(e2_rec.get("preservation") or {})}),
        "integrity": kv_rows(leak),
    }
    # flatten preservation sheet
    sheets["current_preservation"] = (
        [{"arm": E0, **(e0_rec.get("preservation") or {})}, {"arm": E1, **(e1_rec.get("preservation") or {})}, {"arm": E2, **(e2_rec.get("preservation") or {})}]
    )
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, **extra}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(f"VERDICT {required.get('VERDICT')}", flush=True)
    print(f"NEXT {required.get('NEXT')}", flush=True)
    print(
        f"E1 net={required.get('E1_OVERLAY_NET')} pf={required.get('E1_OVERLAY_PF')} "
        f"E2 net={required.get('E2_OVERLAY_NET')} pf={required.get('E2_OVERLAY_PF')} "
        f"pass_n={required.get('PASS_ARM_N')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
