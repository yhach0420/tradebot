"""Offline raw-event information-loss audit. No Runtime write. No Paper. No Exact. No model."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.inventory import build_inventory
from research.direct_joint_objective.oof import attach_joint_labels
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.raw_event_information_audit import (
    ANALYSIS_ID,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    DESCRIPTOR_ADDED,
    ELIGIBLE_DAYS,
    EVENT_MODEL_STARTED,
    EXACT_RAN,
    FAMILIES,
    FAMILY_KEYS,
    FEATURE_SEARCH,
    GRID_RERUN,
    MAX_WORKERS,
    NEW_FORWARD_N,
    NEW_MODEL,
    PAPER_OPERATED,
    PNL_USED,
    PRIOR_VERDICT_REQUIRED,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    WINDOW_CHANGED,
)
from research.raw_event_information_audit.analyze import (
    control_parity,
    coverage,
    decide,
    descriptor_separation,
    family_decision,
    incremental_gate,
    _lost_rate,
)
from research.raw_event_information_audit.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.raw_event_information_audit.replay import process_day
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "temporal_model_probe" / "report.json"
SEQ_CACHE = NATIVE / "results" / "research" / "_work_cache" / "entry_sequence_representation"
S0_PATH = SEQ_CACHE / "arch_S0.json"
S1_PATH = SEQ_CACHE / "arch_S1.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "raw_event_information_audit"
EVT_SRC = Path(__file__).resolve().parent / "events.py"
REPLAY_SRC = Path(__file__).resolve().parent / "replay.py"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str), encoding="utf-8")


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


def write_report(
    required: dict,
    *,
    decision: dict,
    extra: dict | None = None,
    sheets_extra: dict | None = None,
) -> int:
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "decision": decision,
        **(extra or {}),
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "WINDOW_SEC": 180,
                "STEP_SEC": 5,
                "NEW_MODEL": NEW_MODEL,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "EVENT_MODEL_STARTED": EVENT_MODEL_STARTED,
                "EXACT_RAN": EXACT_RAN,
            }
        ),
        "Coverage": [{"empty": True}],
        "Descriptors": [{"empty": True}],
        "Families": [{"empty": True}],
        "Aliasing": [{"empty": True}],
        "Integrity": [{"empty": True}],
        "Decision": kv_rows(decision),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C4_STARTED": C4_STARTED,
                "EXACT_RAN": EXACT_RAN,
                "NEW_MODEL": NEW_MODEL,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "WINDOW_CHANGED": WINDOW_CHANGED,
                "GRID_RERUN": GRID_RERUN,
                "DESCRIPTOR_ADDED": DESCRIPTOR_ADDED,
                "PNL_USED": PNL_USED,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "EVENT_MODEL_STARTED": EVENT_MODEL_STARTED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No event-level model. No Exact. No runtime candidate. Runtime unchanged. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "RAW_EVENT_AUDIT_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "VERDICT": "RAW_EVENT_AUDIT_INTEGRITY_FAILED",
        "RAW_EVENT_INCREMENTAL_INFORMATION": False,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "PRIMARY_FINDING": note,
        "BASE_PARITY": False,
        "FUTURE_EVENT_USE_N": None,
        "SESSION_CARRY_N": None,
        "TARGET_CONTAMINATION_N": None,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": None,
            "VERDICT": "RAW_EVENT_AUDIT_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": note,
            "note": "STOP. Integrity failed.",
        },
    )


def _target_contamination_n() -> int:
    n = 0
    for path in (EVT_SRC, REPLAY_SRC):
        text = path.read_text(encoding="utf-8")
        for token in ("T1", "T2", "MFE_600", "DOWNSIDE_AVOID"):
            if token in text:
                n += text.count(token)
    return int(n)


def _join(path_rows: list[dict], harvest: list[dict]) -> tuple[list[dict], int]:
    idx = {}
    for h in harvest:
        idx[f"{h.get('date')}|{h.get('anchor')}|{h.get('symbol')}"] = h
    miss = 0
    out = []
    skip = {"date", "session", "anchor", "symbol", "t0"}
    for r in path_rows:
        rec = dict(r)
        k = f"{rec.get('date')}|{rec.get('anchor')}|{rec.get('symbol')}"
        h = idx.get(k)
        if h is None:
            miss += 1
            continue
        for key, val in h.items():
            if key in skip:
                continue
            rec[key] = val
        out.append(rec)
    return out, miss


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE RAW EVENT INFORMATION LOSS AUDIT V1", flush=True)
    print("Diagnostic only. No model. Frozen Direct Joint label. No Exact.", flush=True)

    if list(FEATURE_ORDER) != [
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ]:
        print("STOP FEATURE_ORDER drift", flush=True)
        return 2
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        print("STOP rank_pass_gate drift", flush=True)
        return 2
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        print("STOP: C14 identity mismatch", flush=True)
        return 2
    prior = _load(PRIOR)
    preg = prior.get("required") or {}
    if preg.get("VERDICT") != PRIOR_VERDICT_REQUIRED:
        print("STOP prior temporal verdict mismatch", preg.get("VERDICT"), flush=True)
        return _integrity("STOP. Prior TEMPORAL_MODEL_STILL_SINGLE_OBJECTIVE required.")
    if not S0_PATH.is_file() or not S1_PATH.is_file() or not ROWS_PATH.is_file():
        print("STOP missing control/path caches", flush=True)
        return _integrity("STOP. S0/S1/path_rows caches missing.")

    s0 = _load(S0_PATH)
    s1 = _load(S1_PATH)
    parity = control_parity(s0, s1)
    if not parity.get("BASE_PARITY"):
        print("STOP BASE_PARITY", parity, flush=True)
        return _integrity("STOP. S0/S1 did not reproduce frozen controls.", extra={"parity": parity})
    print("BASE_PARITY true", flush=True)

    contamination_n = _target_contamination_n()
    if contamination_n != 0:
        return _integrity("STOP. Raw-event harvest must not read T1/T2.", extra={"TARGET_CONTAMINATION_N": contamination_n})

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
        print("STOP eligible day mismatch", len(elig), flush=True)
        return _integrity("STOP. Eligible Capture days mismatch.")
    by_date = {r["date"]: r for r in elig}

    CACHE.mkdir(parents=True, exist_ok=True)
    feat_got = []
    feat_jobs = []
    for day in ELIGIBLE_DAYS:
        fp = CACHE / f"{day}_RAW.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("date") == day and saved.get("rows"):
            feat_got.append(saved)
            print(f"RAW cache-hit {day} rows={len(saved.get('rows') or [])}", flush=True)
            continue
        r = by_date[day]
        feat_jobs.append({"date": day, "capture_path": r["capture_path"], "universe": r["universe_symbols"]})
    print(f"raw harvest jobs={len(feat_jobs)}", flush=True)
    for body in _pool(process_day, feat_jobs, "RAW", "date"):
        if body.get("ok"):
            _save_json(CACHE / f"{body.get('date')}_RAW.json", body)
        feat_got.append(body)
    fail = [b for b in feat_got if not b.get("ok")]
    if fail or len(feat_got) != len(ELIGIBLE_DAYS):
        print("STOP raw harvest failed", [(b.get("date"), b.get("blocker")) for b in fail], flush=True)
        return _integrity("STOP. Raw-event harvest failed.")

    harvest = []
    tot = {
        "event_time_after_t0_n": 0,
        "session_carry_n": 0,
        "itayose_event_use_n": 0,
        "special_event_use_n": 0,
        "future_event_use_n": 0,
    }
    for b in feat_got:
        harvest.extend(list(b.get("rows") or []))
        for k in tot:
            tot[k] += int(b.get(k) or 0)
    if (
        tot["event_time_after_t0_n"]
        or tot["session_carry_n"]
        or tot["itayose_event_use_n"]
        or tot["special_event_use_n"]
        or tot["future_event_use_n"]
    ):
        note = (
            f"after_t0={tot['event_time_after_t0_n']} carry={tot['session_carry_n']} "
            f"itay={tot['itayose_event_use_n']} spec={tot['special_event_use_n']} "
            f"future={tot['future_event_use_n']}"
        )
        print("STOP integrity", note, flush=True)
        return _integrity(
            f"STOP. {note}",
            extra={
                "BASE_PARITY": True,
                "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
                "SESSION_CARRY_N": tot["session_carry_n"],
                "ITAYOSE_EVENT_USE_N": tot["itayose_event_use_n"],
                "SPECIAL_EVENT_USE_N": tot["special_event_use_n"],
                "EVENT_TIME_AFTER_T0_N": tot["event_time_after_t0_n"],
            },
        )

    path_rows = list((_load(ROWS_PATH).get("rows") or []))
    joined, miss = _join(path_rows, harvest)
    print(f"join miss={miss} harvest={len(harvest)} joined={len(joined)}", flush=True)
    if miss != 0:
        return _integrity("STOP. JOIN_MISS_N != 0.", extra={"JOIN_MISS_N": miss, "BASE_PARITY": True})

    attach_joint_labels(joined)
    labeled = [
        r
        for r in joined
        if r.get("executable_at_t0")
        and r.get("common_cohort")
        and r.get("joint_label") in (0, 1)
    ]
    cov = coverage(labeled)
    lost = {
        "imbalance": _lost_rate(cov["RAW_IMBALANCE_FLIPS_N"], cov["GRID_VISIBLE_IMBALANCE_FLIPS_N"]),
        "spread": _lost_rate(cov["RAW_SPREAD_TRANSITIONS_N"], cov["GRID_VISIBLE_SPREAD_TRANSITIONS_N"]),
        "depth": _lost_rate(cov["RAW_DEPTH_DIRECTION_CHANGES_N"], cov["GRID_VISIBLE_DEPTH_DIRECTION_CHANGES_N"]),
    }
    seps = []
    for fam in FAMILIES:
        for key in FAMILY_KEYS[fam]:
            seps.append(descriptor_separation(labeled, key))
    fam_out = {fam: family_decision(seps, fam) for fam in FAMILIES}
    gate = incremental_gate(families=fam_out, lost=lost)
    decision = decide(parity_ok=True, integ_ok=True, integ_note="ok", families=fam_out, gate=gate)
    primary_src = None
    if gate.get("not_recoverable_supported_families"):
        primary_src = str(gate["not_recoverable_supported_families"][0])
    elif any(fam_out[f].get("status") == "SUPPORTED" for f in FAMILIES):
        primary_src = next(f for f in FAMILIES if fam_out[f].get("status") == "SUPPORTED")
    else:
        primary_src = "NONE"
    required = {
        "BASE_PARITY": True,
        "RAW_EVENT_ROW_N": cov["RAW_EVENT_ROW_N"],
        "EVENTS_PER_ROW_MEAN": cov["EVENTS_PER_ROW_MEAN"],
        "EVENTS_PER_ROW_MEDIAN": cov["EVENTS_PER_ROW_MEDIAN"],
        "EVENTS_PER_ROW_P10": cov["EVENTS_PER_ROW_P10"],
        "EVENTS_PER_ROW_P90": cov["EVENTS_PER_ROW_P90"],
        "EVENTS_PER_ROW_P99": cov["EVENTS_PER_ROW_P99"],
        "ZERO_EVENT_ROW_N": cov["ZERO_EVENT_ROW_N"],
        "LOST_IMBALANCE_FLIP_RATE": lost["imbalance"],
        "LOST_SPREAD_TRANSITION_RATE": lost["spread"],
        "LOST_DEPTH_CHANGE_RATE": lost["depth"],
        "EVENT_TIMING_INFORMATION": fam_out["EVENT_TIMING"]["status"],
        "IMBALANCE_TRANSITION_INFORMATION": fam_out["IMBALANCE_TRANSITION"]["status"],
        "SPREAD_TRANSITION_INFORMATION": fam_out["SPREAD_TRANSITION"]["status"],
        "DEPTH_TRANSITION_INFORMATION": fam_out["DEPTH_TRANSITION"]["status"],
        "STATE_PERSISTENCE_INFORMATION": fam_out["STATE_PERSISTENCE"]["status"],
        "SUPPORTED_NOT_RECOVERABLE_FAMILIES": gate.get("not_recoverable_supported_families"),
        "RAW_EVENT_INCREMENTAL_INFORMATION": gate.get("RAW_EVENT_INCREMENTAL_INFORMATION"),
        "PRIMARY_RAW_INFORMATION_SOURCE": primary_src,
        "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
        "SESSION_CARRY_N": tot["session_carry_n"],
        "ITAYOSE_EVENT_USE_N": tot["itayose_event_use_n"],
        "SPECIAL_EVENT_USE_N": tot["special_event_use_n"],
        "EVENT_TIME_AFTER_T0_N": tot["event_time_after_t0_n"],
        "TARGET_CONTAMINATION_N": contamination_n,
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "parity": parity,
        "gate": {k: v for k, v in gate.items() if k != "lost"},
        "aliasing": {
            **{k: cov[k] for k in cov if "FLIP" in k or "TRANSITION" in k or "DEPTH" in k},
            "LOST_IMBALANCE_FLIP_RATE": lost["imbalance"],
            "LOST_SPREAD_TRANSITION_RATE": lost["spread"],
            "LOST_DEPTH_CHANGE_RATE": lost["depth"],
        },
    }
    desc_rows = [{k: v for k, v in s.items() if k != "reason"} | {"reason": s.get("reason")} for s in seps]
    fam_rows = [
        {
            "family": f,
            "status": fam_out[f]["status"],
            "robust_keys": fam_out[f]["robust_keys"],
            "not_recoverable_robust_keys": fam_out[f]["not_recoverable_robust_keys"],
            "economic_story": fam_out[f]["economic_story"],
        }
        for f in FAMILIES
    ]
    print(
        f"CASE={decision.get('CASE')} incremental={gate.get('RAW_EVENT_INCREMENTAL_INFORMATION')} "
        f"lost_imb={lost['imbalance']} lost_spr={lost['spread']} lost_dep={lost['depth']}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={"descriptors": seps, "families": fam_rows, "coverage": cov},
        sheets_extra={
            "Coverage": kv_rows(cov),
            "Descriptors": desc_rows,
            "Families": fam_rows,
            "Aliasing": kv_rows(required["aliasing"]),
            "Integrity": kv_rows(
                {
                    "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
                    "SESSION_CARRY_N": tot["session_carry_n"],
                    "ITAYOSE_EVENT_USE_N": tot["itayose_event_use_n"],
                    "SPECIAL_EVENT_USE_N": tot["special_event_use_n"],
                    "EVENT_TIME_AFTER_T0_N": tot["event_time_after_t0_n"],
                    "TARGET_CONTAMINATION_N": contamination_n,
                    "JOIN_MISS_N": miss,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
