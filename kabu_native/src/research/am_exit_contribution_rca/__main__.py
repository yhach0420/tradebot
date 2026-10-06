"""Offline AM EXIT contribution RCA. Frozen C0/C14. No Runtime write. No Paper. No policy."""
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
from research.am_entry_profit_improvement.metrics import economic_pack
from research.am_entry_research_final_decision.spec import canonical_c0_spec, spec_sha256
from research.am_exit_contribution_rca import (
    ANALYSIS_ID,
    C0_AUGMENT_LOCKED,
    CURRENT_LOCKED,
    ENTRY_POLICY_CHANGE_N,
    EXIT_POLICY_CHANGE_N,
    FROZEN_C0_SPEC_SHA256,
    ORACLE_EXIT_SELECTION_USE_N,
    PAPER_OPERATION_N,
    PROSPECTIVE_CHALLENGER_ID,
    RUNTIME_CHANGE_N,
    WAIT_CHANGE_N,
)
from research.am_exit_contribution_rca.analyze import (
    L1,
    L2,
    L3,
    L4,
    attach_classes,
    decide_case,
    exit_reason_rows,
    is_loss,
    population_pack,
)
from research.am_exit_contribution_rca.contract import c14_horizons
from research.am_exit_contribution_rca.harvest import process_path_day
from research.am_exit_contribution_rca.publish import OUT, build_markdown, kv_rows, write_artifacts
from research.am_exit_contribution_rca.replay import replay_c0
from research.anchor_timing_robustness.inventory import build_inventory
from research.canonical_entry_performance_rebase.analyze import _f, session_of
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.passive_wait_policy_reassessment.analyze import _close
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

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
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_exit_contribution_rca"

INTEGRITY_ZERO_KEYS = (
    "FUTURE_FEATURE_USE_N",
    "FUTURE_ENTRY_SELECTION_USE_N",
    "ENTRY_POLICY_CHANGE_N",
    "EXIT_POLICY_CHANGE_N",
    "WAIT_CHANGE_N",
    "RUNTIME_CHANGE_N",
    "PAPER_OPERATION_N",
    "ORACLE_EXIT_SELECTION_USE_N",
    "ITAYOSE_PATH_USE_N",
    "SPECIAL_BOARD_PATH_USE_N",
    "SESSION_CARRY_N",
    "INVALID_QUOTE_USE_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
)

TRADE_DROP = ()


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, default=str), encoding="utf-8")


def _slim_trade(t: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "date",
        "session",
        "symbol",
        "anchor",
        "t0",
        "fill_time",
        "fill_price",
        "exit_time",
        "exit_price",
        "exit_reason",
        "pnl_yen_100",
        "arm",
        "row_key",
        "HOLDING_SEC",
        "CANONICAL_EXIT_EVAL_END",
        "MFE_PRE_EXIT_YEN_100",
        "MAE_PRE_EXIT_YEN_100",
        "MFE_PRE_EXIT_BPS",
        "MAE_PRE_EXIT_BPS",
        "TIME_TO_MFE_SEC",
        "TIME_TO_MAE_SEC",
        "PNL_PEAK_TIME",
        "GIVEBACK_FROM_MFE_TO_EXIT",
        "MFE_TO_EVAL_END_YEN_100",
        "MAE_TO_EVAL_END_YEN_100",
        "PNL_AT_EVAL_END_YEN_100",
        "POST_EXIT_MAX_PNL_YEN_100",
        "POST_EXIT_MIN_PNL_YEN_100",
        "POST_EXIT_RECOVERY_VS_EXIT",
        "POST_EXIT_AVAILABLE",
        "LOSS_CLASS",
        "REALIZED_CAPTURE_RATIO",
        "quote_n_pre_exit",
        "quote_n_to_eval_end",
        "quote_n_post_exit",
        "realized_pnl_yen_100",
        "population",
    )
    return {k: t.get(k) for k in keep}


def _empty_required(*, verdict: str, nxt: str) -> dict[str, Any]:
    keys = (
        "BASE_PARITY",
        "CURRENT_TRADE_N",
        "C0_AUGMENT_TRADE_N",
        "C0_AUGMENT_WIN_N",
        "C0_AUGMENT_LOSS_N",
        "C0_AUGMENT_FLAT_N",
        "C0_AUGMENT_NET",
        "C0_LOSS_ENTRY_NEVER_PROFITABLE_N",
        "C0_LOSS_PROFIT_TO_LOSS_N",
        "C0_LOSS_RECOVERED_AFTER_EXIT_N",
        "C0_LOSS_PARTIAL_RECOVERY_N",
        "C0_LOSS_GROSS_LOSS",
        "L1_GROSS_LOSS_SHARE",
        "L2_GROSS_LOSS_SHARE",
        "L3_GROSS_LOSS_SHARE",
        "L4_GROSS_LOSS_SHARE",
        "C0_MEDIAN_MFE",
        "C0_MEDIAN_MAE",
        "C0_MEDIAN_GIVEBACK",
        "C0_WIN_MEDIAN_CAPTURE_RATIO",
        "CURRENT_PROFIT_TO_LOSS_RATE",
        "C0_PROFIT_TO_LOSS_RATE",
        "PRIMARY_LOSS_MECHANISM",
        "EXIT_CONTRIBUTION_SUPPORTED",
        "C0_SPEC_SHA256_MATCH",
        "TRUE_OOS",
        "NEW_FORWARD_N",
        "VERDICT",
        "NEXT",
    )
    req = {k: None for k in keys}
    req["TRUE_OOS"] = TRUE_OOS
    req["NEW_FORWARD_N"] = NEW_FORWARD_N
    req["VERDICT"] = verdict
    req["NEXT"] = nxt
    req["BASE_PARITY"] = False
    req["EXIT_CONTRIBUTION_SUPPORTED"] = False
    return req


def _integrity(msg: str, extra: dict | None = None) -> int:
    decision = decide_case(integrity_ok=False, c0_loss={})
    required = _empty_required(verdict=str(decision.get("VERDICT")), nxt=str(decision.get("NEXT")))
    required["STOP_REASON"] = msg
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "decision": decision,
        "questions": {f"Q{i}": None for i in range(1, 9)},
        **(extra or {}),
    }
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


def _join_path(trades: list[dict[str, Any]], path_by: dict[tuple, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    out = []
    miss = 0
    for t in trades:
        rec = dict(t)
        rec["realized_pnl_yen_100"] = float(_f(t.get("pnl_yen_100")) or 0.0)
        rec["HOLDING_SEC"] = (
            float(t["exit_time"]) - float(t["fill_time"])
            if t.get("exit_time") is not None and t.get("fill_time") is not None
            else None
        )
        key = (str(t.get("date") or ""), str(t.get("symbol") or ""), round(float(t.get("fill_time") or 0.0), 6))
        p = path_by.get(key)
        if not p or not p.get("ok"):
            miss += 1
            rec["ok"] = False
            out.append(rec)
            continue
        for k, v in p.items():
            if k in {"date", "symbol", "anchor", "session", "fill_t", "fill_price", "exit_t", "arm", "row_key"}:
                continue
            rec[k] = v
        rec["ok"] = True
        out.append(rec)
    return attach_classes(out), miss


def _reason_concentration(rows: list[dict[str, Any]], cls: str) -> str:
    grp = [r for r in rows if r.get("LOSS_CLASS") == cls]
    if not grp:
        return "none"
    by: dict[str, int] = defaultdict(int)
    for r in grp:
        by[str(r.get("exit_reason") or "UNKNOWN")] += 1
    top_reason, top_n = max(by.items(), key=lambda x: x[1])
    if top_n > len(grp) / 2.0:
        return f"yes: {top_reason} has {top_n}/{len(grp)} of {cls}"
    return f"no: top {top_reason} {top_n}/{len(grp)} of {cls}"


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get(
        "PYTHONPATH", ""
    )
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM EXIT CONTRIBUTION RCA V1", flush=True)
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
    if int(ENTRY_POLICY_CHANGE_N) or int(EXIT_POLICY_CHANGE_N) or int(WAIT_CHANGE_N):
        return _integrity("STOP. Policy change counters drifted.")
    if W5_RUNTIME_ADOPTED is not False or RUNTIME_CHANGED is not False:
        return _integrity("STOP. Runtime freeze drifted.")

    sha = spec_sha256(canonical_c0_spec())
    sha_ok = sha == FROZEN_C0_SPEC_SHA256
    print(f"C0_SPEC_SHA256 {sha} match={sha_ok}", flush=True)
    if not sha_ok:
        return _integrity("STOP. C0 prospective spec SHA256 mismatch.", extra={"got_sha": sha})

    try:
        horizons = c14_horizons(c14)
    except Exception as exc:
        return _integrity(f"STOP. C14 horizon resolve failed: {exc}")
    print(f"C14 horizons {horizons}", flush=True)

    if not REGIME_LABELED.is_file():
        return _integrity("STOP. labeled AM rows missing.")
    rows = list(_load(REGIME_LABELED).get("rows") or [])
    pm_n = sum(1 for r in rows if session_of(r) != "AM")
    if pm_n:
        return _integrity("STOP. PM rows in labeled AM.", extra={"PM_ROWS_USED_N": pm_n})
    top3 = independent_top3(rows)
    obs = {
        "AM_LABELED_N": len(rows),
        "AM_Y_FILL5_POS_N": sum(1 for r in rows if int(r.get("Y_FILL5") or 0) == 1),
        "AM_CURRENT_TOP3_FILL5_RATE": top3.get("AM_CURRENT_TOP3_FILL5_RATE"),
    }
    parity_pop = freeze_parity(obs)
    if not parity_pop.get("ok"):
        return _integrity("STOP. Frozen AM labeled population did not reproduce.", extra={"parity": parity_pop})

    print("replay C0 overlay", flush=True)
    got = replay_c0(rows, REGIME_LABELED)
    cur_trades = list(got.get("current_trades") or [])
    aug_trades = list(got.get("augment_trades") or [])
    ov_trades = list(got.get("overlay_trades") or [])
    cur_pack = economic_pack(cur_trades, list(ELIGIBLE_DAYS))
    aug_pack = economic_pack(aug_trades, list(ELIGIBLE_DAYS))
    replay_ok = (
        int(cur_pack.get("trade_count") or -1) == int(CURRENT_LOCKED["TRADE_N"])
        and _close(cur_pack.get("net_pnl_yen_100"), CURRENT_LOCKED["NET"], PARITY_ABS_TOL)
        and _close(cur_pack.get("profit_factor"), CURRENT_LOCKED["PF"], 1e-12)
        and _close(cur_pack.get("max_drawdown_yen_100"), CURRENT_LOCKED["DD"], PARITY_ABS_TOL)
        and int(aug_pack.get("trade_count") or -1) == int(C0_AUGMENT_LOCKED["TRADE_N"])
        and int(aug_pack.get("win_n") or -1) == int(C0_AUGMENT_LOCKED["WIN_N"])
        and int(aug_pack.get("loss_n") or -1) == int(C0_AUGMENT_LOCKED["LOSS_N"])
        and int(aug_pack.get("flat_n") or -1) == int(C0_AUGMENT_LOCKED["FLAT_N"])
        and _close(aug_pack.get("net_pnl_yen_100"), C0_AUGMENT_LOCKED["NET"], PARITY_ABS_TOL)
        and int(cur_pack.get("trade_count") or -1) == int(V2_CURRENT_TRADE_N)
        and _close(cur_pack.get("net_pnl_yen_100"), V2_CURRENT_NET_PNL, PARITY_ABS_TOL)
        and _close(cur_pack.get("profit_factor"), V2_CURRENT_PF, 1e-12)
        and _close(cur_pack.get("max_drawdown_yen_100"), V2_CURRENT_MAX_DD, PARITY_ABS_TOL)
    )
    print(
        f"CURRENT n={cur_pack.get('trade_count')} net={cur_pack.get('net_pnl_yen_100')} "
        f"C0 aug n={aug_pack.get('trade_count')} win={aug_pack.get('win_n')} "
        f"loss={aug_pack.get('loss_n')} flat={aug_pack.get('flat_n')} net={aug_pack.get('net_pnl_yen_100')} "
        f"overlay_n={len(ov_trades)} replay_ok={replay_ok}",
        flush=True,
    )
    if not replay_ok:
        return _integrity(
            "STOP. Frozen CURRENT / C0 augment economics did not reproduce.",
            extra={"current": {k: cur_pack.get(k) for k in ("trade_count", "net_pnl_yen_100", "profit_factor", "max_drawdown_yen_100")},
                   "augment": {k: aug_pack.get(k) for k in ("trade_count", "win_n", "loss_n", "flat_n", "net_pnl_yen_100")}},
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
        return _integrity("STOP. Eligible Capture days mismatch.")
    inv_by = {r["date"]: r for r in elig}

    fills_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple] = set()
    for t in cur_trades + aug_trades:
        key = (str(t.get("date")), str(t.get("symbol")), round(float(t.get("fill_time") or 0.0), 6), str(t.get("arm")))
        if key in seen:
            continue
        seen.add(key)
        fills_by[str(t.get("date") or "")].append(
            {
                "date": t.get("date"),
                "anchor": t.get("anchor"),
                "symbol": t.get("symbol"),
                "session": t.get("session") or "AM",
                "fill_t": t.get("fill_time"),
                "fill_price": t.get("fill_price"),
                "exit_t": t.get("exit_time"),
                "realized_pnl_yen_100": t.get("pnl_yen_100"),
                "arm": t.get("arm"),
                "row_key": t.get("row_key"),
            }
        )

    CACHE.mkdir(parents=True, exist_ok=True)
    path_got = []
    path_jobs = []
    for day in ELIGIBLE_DAYS:
        fp = CACHE / f"{day}_PATH.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("date") == day and saved.get("rows"):
            path_got.append(saved)
            print(f"PATH cache-hit {day} n={len(saved.get('rows') or [])}", flush=True)
            continue
        r = inv_by[day]
        path_jobs.append(
            {
                "date": day,
                "capture_path": r["capture_path"],
                "universe": r["universe_symbols"],
                "fills": fills_by.get(day) or [],
                "horizons": horizons,
            }
        )
    print(f"path jobs={len(path_jobs)}", flush=True)
    for body in _pool(process_path_day, path_jobs, "PATH", "date"):
        if body.get("ok"):
            _save_json(CACHE / f"{body.get('date')}_PATH.json", body)
        path_got.append(body)
    fail = [b for b in path_got if not b.get("ok")]
    if fail or len(path_got) != len(ELIGIBLE_DAYS):
        return _integrity(
            "STOP. Path reconstruction failed.",
            extra={"fail": [(b.get("date"), b.get("blocker")) for b in fail]},
        )

    leak = {
        "FUTURE_FEATURE_USE_N": 0,
        "FUTURE_ENTRY_SELECTION_USE_N": 0,
        "ENTRY_POLICY_CHANGE_N": ENTRY_POLICY_CHANGE_N,
        "EXIT_POLICY_CHANGE_N": EXIT_POLICY_CHANGE_N,
        "WAIT_CHANGE_N": WAIT_CHANGE_N,
        "RUNTIME_CHANGE_N": RUNTIME_CHANGE_N,
        "PAPER_OPERATION_N": PAPER_OPERATION_N,
        "ORACLE_EXIT_SELECTION_USE_N": ORACLE_EXIT_SELECTION_USE_N,
        "ITAYOSE_PATH_USE_N": 0,
        "SPECIAL_BOARD_PATH_USE_N": 0,
        "SESSION_CARRY_N": 0,
        "INVALID_QUOTE_USE_N": 0,
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "PATH_MISS_N": 0,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "PAPER_OPERATED": PAPER_OPERATED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "C0_SPEC_SHA256_MATCH": True,
        "BASE_PARITY": True,
    }
    path_by: dict[tuple, dict[str, Any]] = {}
    for body in path_got:
        lg = body.get("leak") or {}
        for k in (
            "ITAYOSE_PATH_USE_N",
            "SPECIAL_BOARD_PATH_USE_N",
            "SESSION_CARRY_N",
            "INVALID_QUOTE_USE_N",
            "ITAYOSE_SKIP_N",
            "SPECIAL_SKIP_N",
            "INVALID_SKIP_N",
        ):
            leak[k] = int(leak.get(k) or 0) + int(lg.get(k) or 0)
        for r in list(body.get("rows") or []):
            ft = _f(r.get("fill_t"))
            if ft is None:
                continue
            path_by[(str(r.get("date") or ""), str(r.get("symbol") or ""), round(float(ft), 6))] = r

    cur_joined, cur_miss = _join_path(cur_trades, path_by)
    aug_joined, aug_miss = _join_path(aug_trades, path_by)
    leak["PATH_MISS_N"] = int(cur_miss + aug_miss)
    if leak["PATH_MISS_N"]:
        return _integrity("STOP. Path join miss.", extra={"integrity": leak})
    integrity_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO_KEYS)
    if not integrity_ok:
        return _integrity("STOP. Integrity counters non-zero.", extra={"integrity": leak})

    for r in cur_joined:
        r["population"] = "P0_CURRENT"
    for r in aug_joined:
        r["population"] = "P1_C0_AUGMENT"
    ov_joined = []
    ov_by = {(str(t.get("date")), str(t.get("symbol")), round(float(t.get("fill_time") or 0.0), 6), str(t.get("arm"))): t for t in cur_joined + aug_joined}
    for t in ov_trades:
        k = (str(t.get("date")), str(t.get("symbol")), round(float(t.get("fill_time") or 0.0), 6), str(t.get("arm")))
        rec = dict(ov_by.get(k) or t)
        rec["population"] = "P2_C0_OVERLAY"
        ov_joined.append(rec)

    p0 = population_pack(cur_joined, name="P0_CURRENT")
    p1 = population_pack(aug_joined, name="P1_C0_AUGMENT")
    p2 = population_pack(ov_joined, name="P2_C0_OVERLAY")
    c0_loss_rows = [r for r in aug_joined if is_loss(r.get("realized_pnl_yen_100"))]
    cur_loss_rows = [r for r in cur_joined if is_loss(r.get("realized_pnl_yen_100"))]
    decision = decide_case(integrity_ok=True, c0_loss=p1)

    q6 = _reason_concentration(c0_loss_rows, L2)
    q7_bits = (
        f"CURRENT loss_rate={p0.get('LOSS_RATE')} L1_rate={p0.get('ENTRY_NEVER_PROFITABLE_RATE')} "
        f"L2_rate={p0.get('PROFIT_TO_LOSS_RATE')} med_mfe={p0.get('MEDIAN_MFE')} med_mae={p0.get('MEDIAN_MAE')} "
        f"med_gb={p0.get('MEDIAN_GIVEBACK')}; "
        f"C0_AUG loss_rate={p1.get('LOSS_RATE')} L1_rate={p1.get('ENTRY_NEVER_PROFITABLE_RATE')} "
        f"L2_rate={p1.get('PROFIT_TO_LOSS_RATE')} med_mfe={p1.get('MEDIAN_MFE')} med_mae={p1.get('MEDIAN_MAE')} "
        f"med_gb={p1.get('MEDIAN_GIVEBACK')}"
    )
    questions = {
        "Q1": f"{p1.get('ENTRY_NEVER_PROFITABLE_N')} of {p1.get('LOSS_N')} C0 augment losses never entered profit to eval-end (L1).",
        "Q2": f"{p1.get('PROFIT_TO_LOSS_BEFORE_EXIT_N')} of {p1.get('LOSS_N')} were profitable before actual EXIT then realized a loss (L2).",
        "Q3": f"{p1.get('RECOVERED_AFTER_EXIT_N')} of {p1.get('LOSS_N')} were unprofitable through actual EXIT then profitable after EXIT within the C14 contractual window (L3).",
        "Q4": (
            f"L1 share={p1.get('L1_GROSS_LOSS_SHARE')} L2 share={p1.get('L2_GROSS_LOSS_SHARE')} "
            f"L3 share={p1.get('L3_GROSS_LOSS_SHARE')} L4 share={p1.get('L4_GROSS_LOSS_SHARE')} "
            f"of C0 augment gross loss {p1.get('GROSS_LOSS')}."
        ),
        "Q5": (
            f"Winner median capture ratio={p1.get('WIN_MEDIAN_CAPTURE_RATIO')} "
            f"winner median giveback={p1.get('WIN_MEDIAN_GIVEBACK')} "
            f"winner median MFE={p1.get('WIN_MEDIAN_MFE')} "
            f"all-trade median giveback={p1.get('MEDIAN_GIVEBACK')}."
        ),
        "Q6": q6,
        "Q7": q7_bits,
        "Q8": decision.get("Q8"),
    }
    required = {
        "BASE_PARITY": True,
        "CURRENT_TRADE_N": p0.get("TRADE_N"),
        "C0_AUGMENT_TRADE_N": p1.get("TRADE_N"),
        "C0_AUGMENT_WIN_N": p1.get("WIN_N"),
        "C0_AUGMENT_LOSS_N": p1.get("LOSS_N"),
        "C0_AUGMENT_FLAT_N": p1.get("FLAT_N"),
        "C0_AUGMENT_NET": p1.get("NET_PNL"),
        "C0_LOSS_ENTRY_NEVER_PROFITABLE_N": p1.get("ENTRY_NEVER_PROFITABLE_N"),
        "C0_LOSS_PROFIT_TO_LOSS_N": p1.get("PROFIT_TO_LOSS_BEFORE_EXIT_N"),
        "C0_LOSS_RECOVERED_AFTER_EXIT_N": p1.get("RECOVERED_AFTER_EXIT_N"),
        "C0_LOSS_PARTIAL_RECOVERY_N": p1.get("PARTIAL_RECOVERY_N"),
        "C0_LOSS_GROSS_LOSS": p1.get("GROSS_LOSS"),
        "L1_GROSS_LOSS_SHARE": p1.get("L1_GROSS_LOSS_SHARE"),
        "L2_GROSS_LOSS_SHARE": p1.get("L2_GROSS_LOSS_SHARE"),
        "L3_GROSS_LOSS_SHARE": p1.get("L3_GROSS_LOSS_SHARE"),
        "L4_GROSS_LOSS_SHARE": p1.get("L4_GROSS_LOSS_SHARE"),
        "C0_MEDIAN_MFE": p1.get("MEDIAN_MFE"),
        "C0_MEDIAN_MAE": p1.get("MEDIAN_MAE"),
        "C0_MEDIAN_GIVEBACK": p1.get("MEDIAN_GIVEBACK"),
        "C0_WIN_MEDIAN_CAPTURE_RATIO": p1.get("WIN_MEDIAN_CAPTURE_RATIO"),
        "CURRENT_PROFIT_TO_LOSS_RATE": p0.get("PROFIT_TO_LOSS_RATE"),
        "C0_PROFIT_TO_LOSS_RATE": p1.get("PROFIT_TO_LOSS_RATE"),
        "PRIMARY_LOSS_MECHANISM": decision.get("PRIMARY_LOSS_MECHANISM"),
        "EXIT_CONTRIBUTION_SUPPORTED": decision.get("EXIT_CONTRIBUTION_SUPPORTED"),
        "C0_SPEC_SHA256_MATCH": True,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "PROSPECTIVE_CHALLENGER_ID": PROSPECTIVE_CHALLENGER_ID,
        "C0_PROSPECTIVE_SPEC_SHA256": FROZEN_C0_SPEC_SHA256,
        "Q8": decision.get("Q8"),
    }
    extra = {
        "decision": decision,
        "questions": questions,
        "horizons": horizons,
        "populations": {"P0_CURRENT": p0, "P1_C0_AUGMENT": p1, "P2_C0_OVERLAY": p2},
        "integrity": leak,
        "note": "MFE/post-exit are hindsight opportunity diagnostics. Not a strategy profit. ORACLE_EXIT_SELECTION_USE_N=0.",
        "identity": "Future EXIT research is C0_EXIT_<new_id>, not C0. C0 future OOS keeps C14.",
    }
    path_sheet = [_slim_trade(r) for r in cur_joined + aug_joined]
    sheets = {
        "summary": kv_rows(required),
        "trade_paths": path_sheet,
        "c0_losses": [_slim_trade(r) for r in c0_loss_rows],
        "current_losses": [_slim_trade(r) for r in cur_loss_rows],
        "exit_reason": exit_reason_rows(cur_joined, population="P0_CURRENT")
        + exit_reason_rows(aug_joined, population="P1_C0_AUGMENT"),
        "giveback": kv_rows(
            {
                "C0_TOTAL_REALIZED_PNL": p1.get("NET_PNL"),
                "C0_TOTAL_MFE_PRE_EXIT": p1.get("TOTAL_MFE_PRE_EXIT"),
                "C0_TOTAL_GIVEBACK_FROM_MFE": p1.get("TOTAL_GIVEBACK_FROM_MFE"),
                "C0_LOSER_TOTAL_GIVEBACK": p1.get("LOSER_TOTAL_GIVEBACK"),
                "C0_WINNER_TOTAL_GIVEBACK": p1.get("WINNER_TOTAL_GIVEBACK"),
                "C0_MEDIAN_GIVEBACK": p1.get("MEDIAN_GIVEBACK"),
                "C0_P75_GIVEBACK": p1.get("P75_GIVEBACK"),
                "C0_P90_GIVEBACK": p1.get("P90_GIVEBACK"),
                "CURRENT_MEDIAN_GIVEBACK": p0.get("MEDIAN_GIVEBACK"),
                "CURRENT_MEDIAN_MFE": p0.get("MEDIAN_MFE"),
                "CURRENT_MEDIAN_MAE": p0.get("MEDIAN_MAE"),
            }
        ),
        "integrity": kv_rows(leak),
    }
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, **extra}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(f"VERDICT {required.get('VERDICT')}", flush=True)
    print(f"NEXT {required.get('NEXT')}", flush=True)
    print(f"Q8 {questions.get('Q8')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
