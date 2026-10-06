"""Offline raw-event incremental prediction probe. No Runtime write. No Paper. No Exact."""
from __future__ import annotations

import json
import math
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

from research.canonical_entry_performance_rebase.analyze import _f
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.raw_event_information_audit import FAMILY_KEYS
from research.raw_event_prediction_probe import (
    ANALYSIS_ID,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    DESCRIPTOR_ADDED,
    ECONOMIC_STORY_FILTER,
    ELIGIBLE_DAYS,
    EVENT_MODEL_STARTED,
    EXACT_RAN,
    FEATURE_SEARCH,
    HYPERPARAMETER_TUNING,
    LABEL_CHANGED,
    MAX_WORKERS,
    MODEL_CHANGED,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    PNL_USED,
    PRIOR_VERDICT_REQUIRED,
    RAW_DESCRIPTOR_N,
    RAW_DESCRIPTORS,
    RF_PARAMS,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    STRATEGY_CREATED,
    SUBSET_SEARCH,
    THRESHOLD_SEARCH,
    TRUE_OOS,
    WINDOW_CHANGED,
)
from research.raw_event_prediction_probe.analyze import (
    arch_summary,
    base_parity,
    decide,
    integrity_ok,
    paired_joint_days,
)
from research.raw_event_prediction_probe.oof import e0_job, e1_job, process_architecture
from research.raw_event_prediction_probe.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "raw_event_information_audit" / "report.json"
S1_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_sequence_representation" / "arch_S1.json"
ENRICHED = NATIVE / "results" / "research" / "_work_cache" / "entry_sequence_representation" / "enriched_rows.json"
RAW_CACHE = NATIVE / "results" / "research" / "_work_cache" / "raw_event_information_audit"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "raw_event_prediction_probe"
JOINED = CACHE / "joined_rows.json"
PKG = Path(__file__).resolve().parent


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
                f"done {label} {body.get(key) or body.get('architecture_id') or ident} "
                f"ok={body.get('ok')} blocker={body.get('blocker')}",
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
                "MODEL": "RandomForestClassifier P(JOINT_IMPROVEMENT_LABEL=1)",
                "RF_PARAMS": RF_PARAMS,
                "ARCHITECTURES": ["E0", "E1"],
                "RAW_DESCRIPTOR_N": RAW_DESCRIPTOR_N,
                "RAW_DESCRIPTORS": list(RAW_DESCRIPTORS),
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "SUBSET_SEARCH": SUBSET_SEARCH,
                "ECONOMIC_STORY_FILTER": ECONOMIC_STORY_FILTER,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "MODEL_CHANGED": MODEL_CHANGED,
                "LABEL_CHANGED": LABEL_CHANGED,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "WINDOW_CHANGED": WINDOW_CHANGED,
                "DESCRIPTOR_ADDED": DESCRIPTOR_ADDED,
                "EXACT_RAN": EXACT_RAN,
                "EVENT_MODEL_STARTED": EVENT_MODEL_STARTED,
            }
        ),
        "E0": [{"empty": True}],
        "E1": [{"empty": True}],
        "Gates": [{"empty": True}],
        "Daily": [{"empty": True}],
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
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "MODEL_CHANGED": MODEL_CHANGED,
                "FEATURE_SEARCH": FEATURE_SEARCH,
                "SUBSET_SEARCH": SUBSET_SEARCH,
                "ECONOMIC_STORY_FILTER": ECONOMIC_STORY_FILTER,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "WINDOW_CHANGED": WINDOW_CHANGED,
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
        "STOP. No Exact. No runtime candidate. No event-level deep model. Runtime unchanged. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "RAW_EVENT_PREDICTION_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "VERDICT": "RAW_EVENT_PREDICTION_INTEGRITY_FAILED",
        "RAW_DESCRIPTOR_PREDICTION_PASS": False,
        "RAW_DESCRIPTOR_INCREMENTAL": False,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "PRIMARY_FINDING": note,
        "BASE_PARITY": False,
        "RAW_DESCRIPTOR_N": RAW_DESCRIPTOR_N,
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
            "VERDICT": "RAW_EVENT_PREDICTION_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": note,
            "note": "STOP. Integrity failed.",
        },
    )


def _contamination_n() -> int:
    n = 0
    text = (PKG / "oof.py").read_text(encoding="utf-8")
    for token in ("T1", "T2", "MFE_600", "DOWNSIDE_AVOID"):
        n += text.count(token)
    for name in RAW_DESCRIPTORS:
        if name in {"T1", "T2"} or "MFE" in name or "DOWNSIDE" in name:
            n += 1
    return int(n)


def _finite_or_zero(v: Any) -> float:
    x = _f(v)
    if x is None or not math.isfinite(float(x)):
        return 0.0
    return float(x)


def _join(enriched: list[dict], harvest: list[dict]) -> tuple[list[dict], int]:
    idx = {}
    for h in harvest:
        idx[f"{h.get('date')}|{h.get('anchor')}|{h.get('symbol')}"] = h
    miss = 0
    out = []
    zeros = {k: 0.0 for k in RAW_DESCRIPTORS}
    for r in enriched:
        rec = dict(r)
        k = f"{rec.get('date')}|{rec.get('anchor')}|{rec.get('symbol')}"
        h = idx.get(k)
        rec.update(zeros)
        if h is None:
            miss += 1
        else:
            for key in RAW_DESCRIPTORS:
                rec[key] = _finite_or_zero(h.get(key))
        out.append(rec)
    return out, miss


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE RAW EVENT INCREMENTAL PREDICTION PROBE V1", flush=True)
    print("E0=S1 flattened 5s. E1=E0+23 precommitted raw descriptors. Frozen RF. No Exact.", flush=True)

    if list(RAW_DESCRIPTORS) != list(
        FAMILY_KEYS["EVENT_TIMING"]
        + FAMILY_KEYS["IMBALANCE_TRANSITION"]
        + FAMILY_KEYS["SPREAD_TRANSITION"]
        + FAMILY_KEYS["DEPTH_TRANSITION"]
    ):
        print("STOP RAW_DESCRIPTORS drift", flush=True)
        return 2
    if len(RAW_DESCRIPTORS) != int(RAW_DESCRIPTOR_N):
        print("STOP RAW_DESCRIPTOR_N drift", flush=True)
        return 2
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
        print("STOP prior raw-event audit verdict mismatch", preg.get("VERDICT"), flush=True)
        return _integrity("STOP. Prior RAW_EVENT_SIGNAL_NOT_ROBUST required.")
    if not S1_PATH.is_file() or not ENRICHED.is_file():
        print("STOP missing S1/enriched caches", flush=True)
        return _integrity("STOP. S1 / enriched_rows caches missing.")

    s1 = _load(S1_PATH)
    pre = base_parity(s1)
    if not pre.get("BASE_PARITY"):
        print("STOP S1 cache BASE_PARITY", pre, flush=True)
        return _integrity("STOP. Cached S1 did not match the frozen flattened 5s control.", extra={"parity": pre})
    print("S1 cache parity true", flush=True)

    contamination_n = _contamination_n()
    if contamination_n != 0:
        print("STOP TARGET_CONTAMINATION", contamination_n, flush=True)
        return _integrity(
            "STOP. Raw-descriptor join must not read outcome fields.",
            extra={"TARGET_CONTAMINATION_N": contamination_n},
        )

    feat_got = []
    for day in ELIGIBLE_DAYS:
        fp = RAW_CACHE / f"{day}_RAW.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("date") == day and saved.get("rows"):
            feat_got.append(saved)
            print(f"RAW cache-hit {day} rows={len(saved.get('rows') or [])}", flush=True)
            continue
        print("STOP missing RAW harvest cache", day, flush=True)
        return _integrity(f"STOP. Missing raw-event harvest cache for {day}.")
    if len(feat_got) != len(ELIGIBLE_DAYS):
        return _integrity("STOP. Raw-event harvest day count mismatch.")

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

    enriched_body = json.loads(ENRICHED.read_text(encoding="utf-8"))
    enriched = list(enriched_body.get("rows") or [])
    joined, miss = _join(enriched, harvest)
    print(
        f"join miss={miss} harvest={len(harvest)} enriched={len(enriched)} joined={len(joined)} "
        f"future={tot['future_event_use_n']} carry={tot['session_carry_n']} "
        f"itay={tot['itayose_event_use_n']} spec={tot['special_event_use_n']} "
        f"after_t0={tot['event_time_after_t0_n']}",
        flush=True,
    )
    ok_int, note_int = integrity_ok(
        future_n=tot["future_event_use_n"],
        session_carry_n=tot["session_carry_n"],
        itayose_n=tot["itayose_event_use_n"],
        special_n=tot["special_event_use_n"],
        after_t0_n=tot["event_time_after_t0_n"],
        contamination_n=contamination_n,
        join_miss_n=miss,
        raw_n=len(RAW_DESCRIPTORS),
    )
    if not ok_int:
        print("STOP integrity", note_int, flush=True)
        return _integrity(
            f"STOP. {note_int}",
            extra={
                "BASE_PARITY": True,
                "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
                "SESSION_CARRY_N": tot["session_carry_n"],
                "TARGET_CONTAMINATION_N": contamination_n,
                "JOIN_MISS_N": miss,
            },
        )

    CACHE.mkdir(parents=True, exist_ok=True)
    JOINED.write_text(
        json.dumps({"rows": json_sanitize(joined)}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    e0_path = CACHE / "arch_E0.json"
    e1_path = CACHE / "arch_E1.json"
    e0_saved = _load(e0_path)
    e1_saved = _load(e1_path)
    jobs = []
    e0 = None
    e1 = None
    if e0_saved.get("ok") and e0_saved.get("architecture_id") == "E0" and e0_saved.get("daily_joint"):
        e0 = e0_saved
        print("E0 cache-hit", flush=True)
    else:
        jobs.append(e0_job(str(ENRICHED), list(ELIGIBLE_DAYS)))
    if e1_saved.get("ok") and e1_saved.get("architecture_id") == "E1" and e1_saved.get("daily_joint"):
        e1 = e1_saved
        print("E1 cache-hit", flush=True)
    else:
        jobs.append(e1_job(str(JOINED), list(ELIGIBLE_DAYS)))
    print(f"LODO jobs={len(jobs)}", flush=True)
    for body in _pool(process_architecture, jobs, "RF", "representation_id"):
        aid = body.get("architecture_id") or body.get("representation_id")
        if aid == "E0":
            e0 = body
            if body.get("ok"):
                _save_json(e0_path, body)
        elif aid == "E1":
            e1 = body
            if body.get("ok"):
                _save_json(e1_path, body)
    if not e0 or not e0.get("ok"):
        print("STOP E0 failed", (e0 or {}).get("blocker"), flush=True)
        return _integrity("STOP. E0 S1-control LODO failed.")
    if not e1 or not e1.get("ok"):
        print("STOP E1 failed", (e1 or {}).get("blocker"), flush=True)
        return _integrity("STOP. E1 raw-descriptor LODO failed.")

    parity = base_parity(e0)
    if not parity.get("BASE_PARITY"):
        print("STOP E0 BASE_PARITY", parity, flush=True)
        return _integrity(
            "STOP. E0 did not reproduce the frozen S1 flattened 5s sequence control.",
            extra={
                "BASE_PARITY": False,
                "E0_MFE_DELTA": e0.get("TOP3_MFE_DELTA"),
                "E0_DOWNSIDE_DELTA": e0.get("TOP3_DOWNSIDE_DELTA"),
                "E0_JOINT_RATE": e0.get("JOINT_COHORT_SUCCESS_RATE"),
                "parity": parity,
            },
        )
    print("BASE_PARITY true", flush=True)

    paired = paired_joint_days(e0, e1)
    decision = decide(e1, e0=e0, parity_ok=True, integ_ok=True, integ_note="ok", paired=paired)
    e0s = arch_summary(e0)
    e1s = arch_summary(e1, d_joint=decision.get("DELTA_JOINT_RATE_VS_S1"))
    required = {
        "BASE_PARITY": True,
        "RAW_DESCRIPTOR_N": RAW_DESCRIPTOR_N,
        "E0_MFE_DELTA": e0.get("TOP3_MFE_DELTA"),
        "E0_DOWNSIDE_DELTA": e0.get("TOP3_DOWNSIDE_DELTA"),
        "E0_JOINT_RATE": e0.get("JOINT_COHORT_SUCCESS_RATE"),
        "E1_MFE_DELTA": e1.get("TOP3_MFE_DELTA"),
        "E1_DOWNSIDE_DELTA": e1.get("TOP3_DOWNSIDE_DELTA"),
        "E1_JOINT_RATE": e1.get("JOINT_COHORT_SUCCESS_RATE"),
        "DELTA_MFE_VS_S1": decision.get("DELTA_MFE_VS_S1"),
        "DELTA_DOWNSIDE_VS_S1": decision.get("DELTA_DOWNSIDE_VS_S1"),
        "DELTA_JOINT_RATE_VS_S1": decision.get("DELTA_JOINT_RATE_VS_S1"),
        "JOINT_RATE_IMPROVED_DAYS": paired.get("JOINT_RATE_IMPROVED_DAYS"),
        "JOINT_RATE_WORSENED_DAYS": paired.get("JOINT_RATE_WORSENED_DAYS"),
        "E1_MFE_POS_DAYS": e1.get("MFE_POSITIVE_DAYS"),
        "E1_MFE_NEG_DAYS": e1.get("MFE_NEGATIVE_DAYS"),
        "E1_DOWNSIDE_POS_DAYS": e1.get("DOWNSIDE_POSITIVE_DAYS"),
        "E1_DOWNSIDE_NEG_DAYS": e1.get("DOWNSIDE_NEGATIVE_DAYS"),
        "E1_MFE_EX_BEST_DAY": e1.get("MFE_EX_BEST_DAY"),
        "E1_MFE_EX_TOP3_DAYS": e1.get("MFE_EX_TOP3_DAYS"),
        "E1_DOWNSIDE_EX_BEST_DAY": e1.get("DOWNSIDE_EX_BEST_DAY"),
        "E1_DOWNSIDE_EX_TOP3_DAYS": e1.get("DOWNSIDE_EX_TOP3_DAYS"),
        "E1_ROC_AUC": e1.get("ROC_AUC"),
        "E1_AVERAGE_PRECISION": e1.get("AVERAGE_PRECISION"),
        "RAW_DESCRIPTOR_INCREMENTAL": decision.get("RAW_DESCRIPTOR_INCREMENTAL"),
        "RAW_DESCRIPTOR_PREDICTION_PASS": decision.get("RAW_DESCRIPTOR_PREDICTION_PASS"),
        "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
        "SESSION_CARRY_N": tot["session_carry_n"],
        "TARGET_CONTAMINATION_N": contamination_n,
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "parity": parity,
        "e1_gates": e1s.get("gates"),
        "e1_gate_fail": e1s.get("gate_fail"),
    }
    daily = []
    by_e0 = {str(r.get("date")): r for r in (paired.get("daily") or [])}
    by_dn = {str(r.get("date")): r.get("value") for r in (e1.get("daily_downside") or [])}
    by_mfe = {str(r.get("date")): r.get("value") for r in (e1.get("daily_mfe") or [])}
    for d in ELIGIBLE_DAYS:
        rec = dict(by_e0.get(d) or {"date": d})
        rec["E1_MFE_DELTA"] = by_mfe.get(d)
        rec["E1_DOWNSIDE_DELTA"] = by_dn.get(d)
        daily.append(rec)
    print(
        f"pass={decision.get('RAW_DESCRIPTOR_PREDICTION_PASS')} incremental={decision.get('RAW_DESCRIPTOR_INCREMENTAL')} "
        f"CASE={decision.get('CASE')} E1_MFE={e1.get('TOP3_MFE_DELTA')} E1_DN={e1.get('TOP3_DOWNSIDE_DELTA')} "
        f"E1_JOINT={e1.get('JOINT_COHORT_SUCCESS_RATE')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={"e0": e0s, "e1": e1s, "paired": paired, "aggregate": {"parity": parity}},
        sheets_extra={
            "E0": [{k: v for k, v in e0s.items() if k != "gates"}],
            "E1": [{k: v for k, v in e1s.items() if k != "gates"}],
            "Gates": [
                {"architecture_id": "E0", "PASS": e0s.get("PASS"), **(e0s.get("gates") or {}), "gate_fail": e0s.get("gate_fail")},
                {"architecture_id": "E1", "PASS": e1s.get("PASS"), **(e1s.get("gates") or {}), "gate_fail": e1s.get("gate_fail")},
            ],
            "Daily": daily or [{"empty": True}],
            "Integrity": kv_rows(
                {
                    "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
                    "SESSION_CARRY_N": tot["session_carry_n"],
                    "ITAYOSE_EVENT_USE_N": tot["itayose_event_use_n"],
                    "SPECIAL_EVENT_USE_N": tot["special_event_use_n"],
                    "EVENT_TIME_AFTER_T0_N": tot["event_time_after_t0_n"],
                    "TARGET_CONTAMINATION_N": contamination_n,
                    "JOIN_MISS_N": miss,
                    "RAW_DESCRIPTOR_N": RAW_DESCRIPTOR_N,
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
