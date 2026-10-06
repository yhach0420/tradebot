"""Offline causal entry sequence representation probe. No Runtime write. No Paper. No Exact."""
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
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_sequence_representation import (
    ANALYSIS_ID,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    CHANNEL_CHANGED,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    GRID_CHANGED,
    HYPERPARAMETER_TUNING,
    INDIVIDUAL_LAG_SELECTION,
    LABEL_CHANGED,
    MAX_WORKERS,
    MODEL_CHANGED,
    N_MARKS,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    PNL_USED,
    PRIOR_VERDICT_REQUIRED,
    RF_PARAMS,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    S1_FEATURES,
    SEQ_FEATURES,
    SEQUENCE_FEATURE_N,
    STRATEGY_CREATED,
    TEMPORAL_DEEP_STARTED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRUE_OOS,
    WINDOW_CHANGED,
)
from research.entry_sequence_representation.analyze import (
    arch_summary,
    base_parity,
    decide,
    integrity_ok,
)
from research.entry_sequence_representation.oof import process_architecture, s0_job, s1_job
from research.entry_sequence_representation.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.entry_sequence_representation.replay import process_day
from research.entry_sequence_representation.sequence import seq_to_row
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "joint_feature_architecture" / "report.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "entry_sequence_representation"
ENRICHED = CACHE / "enriched_rows.json"
SEQ_SRC = Path(__file__).resolve().parent / "sequence.py"
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
                "ARCHITECTURES": ["S0", "S1"],
                "SEQUENCE_FEATURE_N": SEQUENCE_FEATURE_N,
                "GRID_MARKS_PER_ROW": N_MARKS,
                "LABEL_CHANGED": LABEL_CHANGED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "GRID_CHANGED": GRID_CHANGED,
                "WINDOW_CHANGED": WINDOW_CHANGED,
                "CHANNEL_CHANGED": CHANNEL_CHANGED,
                "INDIVIDUAL_LAG_SELECTION": INDIVIDUAL_LAG_SELECTION,
                "EXACT_RAN": EXACT_RAN,
                "TEMPORAL_DEEP_STARTED": TEMPORAL_DEEP_STARTED,
            }
        ),
        "S0": [{"empty": True}],
        "S1": [{"empty": True}],
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
                "PNL_USED": PNL_USED,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "TEMPORAL_DEEP_STARTED": TEMPORAL_DEEP_STARTED,
                "GRID_CHANGED": GRID_CHANGED,
                "WINDOW_CHANGED": WINDOW_CHANGED,
                "CHANNEL_CHANGED": CHANNEL_CHANGED,
                "INDIVIDUAL_LAG_SELECTION": INDIVIDUAL_LAG_SELECTION,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No Exact. No runtime candidate. No temporal deep model. Runtime unchanged. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "SEQUENCE_REPRESENTATION_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "VERDICT": "SEQUENCE_REPRESENTATION_INTEGRITY_FAILED",
        "SEQUENCE_REPRESENTATION_PASS": False,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "PRIMARY_FINDING": note,
        "BASE_PARITY": False,
        "SEQUENCE_FEATURE_N": SEQUENCE_FEATURE_N,
        "FUTURE_EVENT_USE_N": None,
        "SESSION_CARRY_N": None,
        "ITAYOSE_STATE_USE_N": None,
        "SPECIAL_STATE_USE_N": None,
        "TARGET_CONTAMINATION_N": None,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": None,
            "VERDICT": "SEQUENCE_REPRESENTATION_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": note,
            "note": "STOP. Integrity failed.",
        },
    )


def _target_contamination_n() -> int:
    n = 0
    for path in (SEQ_SRC, REPLAY_SRC):
        text = path.read_text(encoding="utf-8")
        for token in ("T1", "T2", "MFE_600", "DOWNSIDE_AVOID"):
            if token in text:
                n += text.count(token)
    for name in SEQ_FEATURES:
        if name in {"T1", "T2"} or "MFE" in name or "DOWNSIDE" in name:
            n += 1
    return int(n)


def _join(path_rows: list[dict], harvest: list[dict]) -> tuple[list[dict], dict[str, int], int]:
    idx = {}
    for h in harvest:
        idx[f"{h.get('date')}|{h.get('anchor')}|{h.get('symbol')}"] = h
    tot = {
        "future_event_use_n": 0,
        "session_carry_n": 0,
        "itayose_state_use_n": 0,
        "special_state_use_n": 0,
        "sequence_after_t0_n": 0,
        "grid_bad_n": 0,
    }
    miss = 0
    out = []
    zeros = {n: 0.0 for n in SEQ_FEATURES}
    for r in path_rows:
        rec = dict(r)
        k = f"{rec.get('date')}|{rec.get('anchor')}|{rec.get('symbol')}"
        h = idx.get(k)
        rec.update(zeros)
        if h is None:
            miss += 1
        else:
            rec.update(seq_to_row(list(h.get("seq") or [])))
            tot["future_event_use_n"] += int(h.get("future_event_use_n") or 0)
            tot["session_carry_n"] += int(h.get("session_carry_n") or 0)
            tot["itayose_state_use_n"] += int(h.get("itayose_state_use_n") or 0)
            tot["special_state_use_n"] += int(h.get("special_state_use_n") or 0)
            tot["sequence_after_t0_n"] += int(h.get("sequence_after_t0_n") or 0)
            if int(h.get("grid_marks") or 0) != int(N_MARKS):
                tot["grid_bad_n"] += 1
        out.append(rec)
    return out, tot, miss


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE CAUSAL ENTRY SEQUENCE REPRESENTATION PROBE V1", flush=True)
    print("S0=F2_UNION|none. S1=A0+37x7 sequence. Frozen RF. No Exact. No deep model.", flush=True)

    if len(SEQ_FEATURES) != int(SEQUENCE_FEATURE_N) or int(SEQUENCE_FEATURE_N) != 259:
        print("STOP SEQUENCE_FEATURE_N drift", flush=True)
        return 2
    if len(S1_FEATURES) != 11 + 259:
        print("STOP S1 width drift", len(S1_FEATURES), flush=True)
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
        print("STOP prior feature-architecture verdict mismatch", preg.get("VERDICT"), flush=True)
        return _integrity("STOP. Prior ENGINEERED_FEATURE_ARCHITECTURE_INSUFFICIENT required.")
    if not ROWS_PATH.is_file():
        print("STOP missing path_rows.json", flush=True)
        return _integrity("STOP. Common-cohort path_rows missing.")

    contamination_n = _target_contamination_n()
    if contamination_n != 0:
        print("STOP TARGET_CONTAMINATION", contamination_n, flush=True)
        return _integrity("STOP. Sequence harvest must not read T1/T2.", extra={"TARGET_CONTAMINATION_N": contamination_n})

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
    s0_path = CACHE / "arch_S0.json"
    s0_job_body = s0_job(str(ROWS_PATH), list(ELIGIBLE_DAYS))
    s0_saved = _load(s0_path)
    if s0_saved.get("ok") and s0_saved.get("architecture_id") == "S0" and s0_saved.get("daily_mfe"):
        s0 = s0_saved
        print("S0 cache-hit", flush=True)
    else:
        print("S0 LODO start", flush=True)
        s0 = process_architecture(s0_job_body)
        if not s0.get("ok"):
            print("STOP S0 failed", s0.get("blocker"), flush=True)
            return _integrity("STOP. S0 F2_UNION|none LODO failed.")
        _save_json(s0_path, s0)

    parity = base_parity(s0)
    if not parity.get("BASE_PARITY"):
        print("STOP BASE_PARITY", parity, flush=True)
        return _integrity(
            "STOP. S0 did not reproduce previous F2_UNION|none Direct Joint control.",
            extra={
                "BASE_PARITY": False,
                "S0_MFE_DELTA": s0.get("TOP3_MFE_DELTA"),
                "S0_DOWNSIDE_DELTA": s0.get("TOP3_DOWNSIDE_DELTA"),
                "S0_JOINT_RATE": s0.get("JOINT_COHORT_SUCCESS_RATE"),
                "parity": parity,
            },
        )
    print("BASE_PARITY true", flush=True)

    feat_got = []
    feat_jobs = []
    for day in ELIGIBLE_DAYS:
        fp = CACHE / f"{day}_SEQ.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("date") == day and saved.get("rows"):
            feat_got.append(saved)
            print(f"SEQ cache-hit {day} rows={len(saved.get('rows') or [])}", flush=True)
            continue
        r = by_date[day]
        feat_jobs.append({"date": day, "capture_path": r["capture_path"], "universe": r["universe_symbols"]})
    print(f"seq harvest jobs={len(feat_jobs)}", flush=True)
    for body in _pool(process_day, feat_jobs, "SEQ", "date"):
        if body.get("ok"):
            _save_json(CACHE / f"{body.get('date')}_SEQ.json", body)
        feat_got.append(body)
    fail = [b for b in feat_got if not b.get("ok")]
    if fail or len(feat_got) != len(ELIGIBLE_DAYS):
        print("STOP sequence harvest failed", [(b.get("date"), b.get("blocker")) for b in fail], flush=True)
        return _integrity("STOP. Sequence harvest failed.")

    harvest = []
    for b in feat_got:
        harvest.extend(list(b.get("rows") or []))
    path_body = json.loads(ROWS_PATH.read_text(encoding="utf-8"))
    path_rows = list(path_body.get("rows") or [])
    enriched, tot, miss = _join(path_rows, harvest)
    print(
        f"join miss={miss} future={tot['future_event_use_n']} carry={tot['session_carry_n']} "
        f"itay={tot['itayose_state_use_n']} spec={tot['special_state_use_n']} "
        f"after_t0={tot['sequence_after_t0_n']} grid_bad={tot['grid_bad_n']} seq_rows={len(harvest)}",
        flush=True,
    )
    ENRICHED.write_text(
        json.dumps({"rows": json_sanitize(enriched)}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    ok_int, note_int = integrity_ok(
        future_n=tot["future_event_use_n"],
        session_carry_n=tot["session_carry_n"],
        itayose_n=tot["itayose_state_use_n"],
        special_n=tot["special_state_use_n"],
        after_t0_n=tot["sequence_after_t0_n"],
        grid_marks=N_MARKS,
        sequence_feature_n=SEQUENCE_FEATURE_N,
        contamination_n=contamination_n,
        grid_bad_n=tot["grid_bad_n"],
        join_miss_n=miss,
    )
    if not ok_int:
        print("STOP integrity", note_int, flush=True)
        return _integrity(
            f"STOP. {note_int}",
            extra={
                "BASE_PARITY": True,
                "S0_MFE_DELTA": s0.get("TOP3_MFE_DELTA"),
                "S0_DOWNSIDE_DELTA": s0.get("TOP3_DOWNSIDE_DELTA"),
                "S0_JOINT_RATE": s0.get("JOINT_COHORT_SUCCESS_RATE"),
                "SEQUENCE_ROWS": len(harvest),
                "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
                "SESSION_CARRY_N": tot["session_carry_n"],
                "ITAYOSE_STATE_USE_N": tot["itayose_state_use_n"],
                "SPECIAL_STATE_USE_N": tot["special_state_use_n"],
                "SEQUENCE_AFTER_T0_N": tot["sequence_after_t0_n"],
                "TARGET_CONTAMINATION_N": contamination_n,
            },
        )

    s1_path = CACHE / "arch_S1.json"
    s1_saved = _load(s1_path)
    if s1_saved.get("ok") and s1_saved.get("architecture_id") == "S1" and s1_saved.get("daily_mfe"):
        s1 = s1_saved
        print("S1 cache-hit", flush=True)
    else:
        print("S1 LODO start", flush=True)
        s1 = process_architecture(s1_job(str(ENRICHED), list(ELIGIBLE_DAYS)))
        if not s1.get("ok"):
            print("STOP S1 failed", s1.get("blocker"), flush=True)
            return _integrity("STOP. S1 sequence LODO failed.")
        _save_json(s1_path, s1)

    s0s = arch_summary(s0)
    s1s = arch_summary(s1)
    decision = decide(s1, s0=s0, parity_ok=True, integ_ok=True, integ_note="ok")
    required = {
        "BASE_PARITY": True,
        "S0_MFE_DELTA": s0.get("TOP3_MFE_DELTA"),
        "S0_DOWNSIDE_DELTA": s0.get("TOP3_DOWNSIDE_DELTA"),
        "S0_JOINT_RATE": s0.get("JOINT_COHORT_SUCCESS_RATE"),
        "SEQUENCE_ROWS": len(harvest),
        "SEQUENCE_FEATURE_N": SEQUENCE_FEATURE_N,
        "S1_MFE_DELTA": s1.get("TOP3_MFE_DELTA"),
        "S1_DOWNSIDE_DELTA": s1.get("TOP3_DOWNSIDE_DELTA"),
        "S1_JOINT_RATE": s1.get("JOINT_COHORT_SUCCESS_RATE"),
        "DELTA_MFE_VS_S0": decision.get("DELTA_MFE_VS_S0"),
        "DELTA_DOWNSIDE_VS_S0": decision.get("DELTA_DOWNSIDE_VS_S0"),
        "DELTA_JOINT_RATE_VS_S0": decision.get("DELTA_JOINT_RATE_VS_S0"),
        "S1_MFE_POS_DAYS": s1.get("MFE_POSITIVE_DAYS"),
        "S1_MFE_NEG_DAYS": s1.get("MFE_NEGATIVE_DAYS"),
        "S1_DOWNSIDE_POS_DAYS": s1.get("DOWNSIDE_POSITIVE_DAYS"),
        "S1_DOWNSIDE_NEG_DAYS": s1.get("DOWNSIDE_NEGATIVE_DAYS"),
        "S1_MFE_EX_BEST_DAY": s1.get("MFE_EX_BEST_DAY"),
        "S1_MFE_EX_TOP3_DAYS": s1.get("MFE_EX_TOP3_DAYS"),
        "S1_DOWNSIDE_EX_BEST_DAY": s1.get("DOWNSIDE_EX_BEST_DAY"),
        "S1_DOWNSIDE_EX_TOP3_DAYS": s1.get("DOWNSIDE_EX_TOP3_DAYS"),
        "S1_ROC_AUC": s1.get("ROC_AUC"),
        "S1_AVERAGE_PRECISION": s1.get("AVERAGE_PRECISION"),
        "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
        "SESSION_CARRY_N": tot["session_carry_n"],
        "ITAYOSE_STATE_USE_N": tot["itayose_state_use_n"],
        "SPECIAL_STATE_USE_N": tot["special_state_use_n"],
        "SEQUENCE_AFTER_T0_N": tot["sequence_after_t0_n"],
        "GRID_MARKS_PER_ROW": N_MARKS,
        "TARGET_CONTAMINATION_N": contamination_n,
        "JOIN_MISS_N": miss,
        "SEQUENCE_REPRESENTATION_PASS": decision.get("SEQUENCE_REPRESENTATION_PASS"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "parity": parity,
        "s1_gates": s1s.get("gates"),
        "s1_gate_fail": s1s.get("gate_fail"),
    }
    daily = []
    by_dn = {str(r.get("date")): r.get("value") for r in (s1.get("daily_downside") or [])}
    for rec in s1.get("daily_mfe") or []:
        d = str(rec.get("date"))
        daily.append({"date": d, "S1_MFE_DELTA": rec.get("value"), "S1_DOWNSIDE_DELTA": by_dn.get(d)})
    print(
        f"pass={decision.get('SEQUENCE_REPRESENTATION_PASS')} CASE={decision.get('CASE')} "
        f"S1_MFE={s1.get('TOP3_MFE_DELTA')} S1_DN={s1.get('TOP3_DOWNSIDE_DELTA')} "
        f"S1_JOINT={s1.get('JOINT_COHORT_SUCCESS_RATE')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={"s0": s0s, "s1": s1s, "aggregate": {"parity": parity}},
        sheets_extra={
            "S0": [ {k: v for k, v in s0s.items() if k != "gates"} ],
            "S1": [ {k: v for k, v in s1s.items() if k != "gates"} ],
            "Gates": [
                {"architecture_id": "S0", "PASS": s0s.get("PASS"), **(s0s.get("gates") or {}), "gate_fail": s0s.get("gate_fail")},
                {"architecture_id": "S1", "PASS": s1s.get("PASS"), **(s1s.get("gates") or {}), "gate_fail": s1s.get("gate_fail")},
            ],
            "Daily": daily or [{"empty": True}],
            "Integrity": kv_rows(
                {
                    "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
                    "SESSION_CARRY_N": tot["session_carry_n"],
                    "ITAYOSE_STATE_USE_N": tot["itayose_state_use_n"],
                    "SPECIAL_STATE_USE_N": tot["special_state_use_n"],
                    "SEQUENCE_AFTER_T0_N": tot["sequence_after_t0_n"],
                    "GRID_MARKS_PER_ROW": N_MARKS,
                    "TARGET_CONTAMINATION_N": contamination_n,
                    "JOIN_MISS_N": miss,
                    "SEQUENCE_ROWS": len(harvest),
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
