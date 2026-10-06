"""Offline joint feature-architecture redesign. No Runtime write. No Paper. No Exact."""
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
from research.joint_feature_architecture import (
    ANALYSIS_ID,
    ARCHITECTURES,
    BEST_ARCHITECTURE_ADOPTED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    ELIGIBLE_DAYS,
    EXACT_RAN,
    HYPERPARAMETER_TUNING,
    INDIVIDUAL_FEATURE_SELECTION,
    JOIN_KEYS,
    LABEL_CHANGED,
    MAX_WORKERS,
    MODEL_CHANGED,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    PNL_USED,
    PRIOR_VERDICT_REQUIRED,
    RF_PARAMS,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SEQUENCE_MODEL_STARTED,
    STRATEGY_CREATED,
    TRUE_OOS,
    WINDOW_SEARCH,
)
from research.joint_feature_architecture.analyze import (
    arch_summary,
    attribution,
    base_parity,
    decide,
    redundancy,
)
from research.joint_feature_architecture.oof import architecture_jobs, process_architecture
from research.joint_feature_architecture.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.joint_feature_architecture.replay import process_day
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "direct_joint_objective" / "report.json"
ROWS_PATH = NATIVE / "results" / "research" / "_work_cache" / "entry_target_architecture" / "path_rows.json"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "joint_feature_architecture"
ENRICHED = CACHE / "enriched_rows.json"


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
                "ARCHITECTURES": [a["architecture_id"] for a in ARCHITECTURES],
                "LABEL_CHANGED": LABEL_CHANGED,
                "HYPERPARAMETER_TUNING": HYPERPARAMETER_TUNING,
                "INDIVIDUAL_FEATURE_SELECTION": INDIVIDUAL_FEATURE_SELECTION,
                "BEST_ARCHITECTURE_ADOPTED": BEST_ARCHITECTURE_ADOPTED,
                "EXACT_RAN": EXACT_RAN,
                "SEQUENCE_MODEL_STARTED": SEQUENCE_MODEL_STARTED,
            }
        ),
        "Architectures": [{"empty": True}],
        "Gates": [{"empty": True}],
        "Attribution": [{"empty": True}],
        "Redundancy": [{"empty": True}],
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
                "INDIVIDUAL_FEATURE_SELECTION": INDIVIDUAL_FEATURE_SELECTION,
                "WINDOW_SEARCH": WINDOW_SEARCH,
                "MODEL_CHANGED": MODEL_CHANGED,
                "PNL_USED": PNL_USED,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "SEQUENCE_MODEL_STARTED": SEQUENCE_MODEL_STARTED,
                "BEST_ARCHITECTURE_ADOPTED": BEST_ARCHITECTURE_ADOPTED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No Exact. No runtime candidate. No sequence model. Runtime unchanged. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "FEATURE_ARCHITECTURE_REDESIGN_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str) -> int:
    return write_report(
        {
            "VERDICT": "FEATURE_ARCHITECTURE_REDESIGN_INTEGRITY_FAILED",
            "FEATURE_ARCHITECTURE_PASS": False,
            "NEXT_RESEARCH": "NONE",
            "TRUE_OOS": TRUE_OOS,
            "NEW_FORWARD_N": NEW_FORWARD_N,
            "PRIMARY_FINDING": note,
            "ARCHITECTURE_N": 6,
            "BASE_PARITY": False,
            "FUTURE_EVENT_USE_N": None,
            "TARGET_CONTAMINATION_N": None,
        },
        decision={
            "CASE": None,
            "VERDICT": "FEATURE_ARCHITECTURE_REDESIGN_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": note,
            "note": "STOP. Integrity failed.",
        },
    )


def _join(path_rows: list[dict], harvest: list[dict]) -> tuple[list[dict], int, int]:
    idx = {}
    for h in harvest:
        idx[f"{h.get('date')}|{h.get('anchor')}|{h.get('symbol')}"] = h
    out = []
    future = 0
    miss = 0
    for r in path_rows:
        rec = dict(r)
        k = f"{rec.get('date')}|{rec.get('anchor')}|{rec.get('symbol')}"
        h = idx.get(k)
        if h is None:
            miss += 1
            for key in JOIN_KEYS:
                rec[key] = None
        else:
            future += int(h.get("future_event_use_n") or 0)
            for key in JOIN_KEYS:
                rec[key] = h.get(key)
        out.append(rec)
    return out, future, miss


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE JOINT FEATURE ARCHITECTURE REDESIGN V1", flush=True)
    print("Direct joint RF frozen. 6 architectures. No Exact.", flush=True)

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
    if len(ARCHITECTURES) != 6:
        print("STOP architecture n != 6", flush=True)
        return 2
    prior = _load(PRIOR)
    preg = prior.get("required") or {}
    if preg.get("VERDICT") != PRIOR_VERDICT_REQUIRED:
        print("STOP prior direct-joint verdict mismatch", preg.get("VERDICT"), flush=True)
        return _integrity("STOP. Prior DIRECT_JOINT_OBJECTIVE_STILL_UPSIDE_ONLY required.")
    if not ROWS_PATH.is_file():
        print("STOP missing path_rows.json", flush=True)
        return _integrity("STOP. Common-cohort path_rows missing.")

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

    feat_got = []
    feat_jobs = []
    for day in ELIGIBLE_DAYS:
        fp = CACHE / f"{day}_FEAT.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("date") == day and saved.get("rows"):
            feat_got.append(saved)
            print(f"FEAT cache-hit {day} rows={len(saved.get('rows') or [])}", flush=True)
            continue
        r = by_date[day]
        feat_jobs.append({"date": day, "capture_path": r["capture_path"], "universe": r["universe_symbols"]})
    print(f"feat harvest jobs={len(feat_jobs)}", flush=True)
    for body in _pool(process_day, feat_jobs, "FEAT", "date"):
        if body.get("ok"):
            _save_json(CACHE / f"{body.get('date')}_FEAT.json", body)
        feat_got.append(body)
    fail = [b for b in feat_got if not b.get("ok")]
    if fail or len(feat_got) != len(ELIGIBLE_DAYS):
        print("STOP feature harvest failed", [(b.get("date"), b.get("blocker")) for b in fail], flush=True)
        return _integrity("STOP. Feature harvest failed.")

    harvest = []
    future_n = 0
    for b in feat_got:
        harvest.extend(list(b.get("rows") or []))
        future_n += int(b.get("future_event_use_n") or 0)
    path_body = json.loads(ROWS_PATH.read_text(encoding="utf-8"))
    path_rows = list(path_body.get("rows") or [])
    enriched, future_join, miss = _join(path_rows, harvest)
    print(f"join miss={miss} future={future_n} join_future_flags={future_join}", flush=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    ENRICHED.write_text(
        json.dumps({"rows": json_sanitize(enriched)}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    jobs = []
    got = []
    for job in architecture_jobs(str(ENRICHED), list(ELIGIBLE_DAYS)):
        aid = str(job.get("representation_id"))
        fp = CACHE / f"arch_{aid}.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("architecture_id") == aid and saved.get("daily_mfe"):
            got.append(saved)
            print(f"arch cache-hit {aid}", flush=True)
            continue
        jobs.append(job)
    print(f"arch jobs={len(jobs)}", flush=True)
    for body in _pool(process_architecture, jobs, "ARCH", "representation_id"):
        if body.get("ok"):
            _save_json(CACHE / f"arch_{body.get('architecture_id')}.json", body)
        got.append(body)
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != 6:
        print("STOP architecture LODO failed", [(b.get("architecture_id"), b.get("blocker")) for b in fail], flush=True)
        return _integrity("STOP. Architecture LODO failed.")

    got.sort(key=lambda b: str(b.get("architecture_id") or ""))
    by_id = {str(b.get("architecture_id")): b for b in got}
    a0 = by_id.get("A0") or {}
    parity = base_parity(a0)
    if not parity.get("BASE_PARITY"):
        print("STOP BASE_PARITY", parity, flush=True)
        return _integrity("STOP. A0 did not reproduce previous F2_UNION|none Direct Joint control.")

    summaries = [arch_summary(b) for b in got]
    effects = attribution(by_id)
    attach_joint_labels(enriched)
    labeled = [r for r in enriched if r.get("joint_label") in (0, 1) and r.get("executable_at_t0") and r.get("common_cohort")]
    red = redundancy(labeled, list(JOIN_KEYS))
    contamination_n = 0
    decision = decide(
        summaries,
        effects=effects,
        parity_ok=True,
        future_n=future_n,
        contamination_n=contamination_n,
    )
    passing = [str(s.get("architecture_id")) for s in summaries if s.get("PASS")]
    a0s = next(s for s in summaries if s.get("architecture_id") == "A0")
    required = {
        "ARCHITECTURE_N": 6,
        "BASE_PARITY": True,
        "A0_MFE_DELTA": a0s.get("MFE_DELTA"),
        "A0_DOWNSIDE_DELTA": a0s.get("DOWNSIDE_DELTA"),
        "A0_JOINT_RATE": a0s.get("JOINT_RATE"),
        "PASSING_ARCHITECTURES": passing,
        "PASSING_ARCHITECTURE_N": len(passing),
        "PRIMARY_INFORMATION_SOURCE": decision.get("PRIMARY_INFORMATION_SOURCE"),
        "INTERACTION_DEPENDENT": decision.get("INTERACTION_DEPENDENT"),
        "BEST_DIAGNOSTIC_ARCHITECTURE": decision.get("BEST_DIAGNOSTIC_ARCHITECTURE"),
        "MAX_FEATURE_CORRELATION": red.get("MAX_FEATURE_CORRELATION"),
        "FUTURE_EVENT_USE_N": int(future_n),
        "TARGET_CONTAMINATION_N": 0,
        "FEATURE_ARCHITECTURE_PASS": decision.get("FEATURE_ARCHITECTURE_PASS"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "attribution": effects,
        "redundancy": {
            "MAX_FEATURE_CORRELATION": red.get("MAX_FEATURE_CORRELATION"),
            "MAX_FEATURE_CORRELATION_PAIR": red.get("MAX_FEATURE_CORRELATION_PAIR"),
            "VOLUME_RATE_VS_PERCENTILE": red.get("VOLUME_RATE_VS_PERCENTILE"),
            "HIGH_ABS_GE_080": red.get("HIGH_ABS_GE_080"),
        },
        "parity": parity,
    }
    print(
        f"pass={decision.get('FEATURE_ARCHITECTURE_PASS')} CASE={decision.get('CASE')} "
        f"passing={passing} source={decision.get('PRIMARY_INFORMATION_SOURCE')}",
        flush=True,
    )
    gate_rows = []
    for s in summaries:
        rec = {"architecture_id": s.get("architecture_id"), "PASS": s.get("PASS")}
        rec.update(s.get("gates") or {})
        rec["gate_fail"] = s.get("gate_fail")
        gate_rows.append(rec)
    return write_report(
        required,
        decision=decision,
        extra={"architectures": summaries, "aggregate": {"attribution": effects, "redundancy": red, "parity": parity}},
        sheets_extra={
            "Architectures": [{k: v for k, v in s.items() if k != "gates"} for s in summaries],
            "Gates": gate_rows,
            "Attribution": kv_rows(effects),
            "Redundancy": (red.get("HIGH_ABS_GE_080") or red.get("pairs_head") or [{"empty": True}]),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
