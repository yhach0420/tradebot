"""Offline temporal joint TCN probe. No Runtime write. No Paper. No Exact."""
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

from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.temporal_model_probe import (
    ANALYSIS_ID,
    ARCHITECTURE_CHANGED,
    ATTENTION_USED,
    C14_CHANGED,
    C14_ID,
    C4_STARTED,
    CHANNEL_CHANGED,
    CLASS_WEIGHT_USED,
    DILATION_CHANGED,
    ELIGIBLE_DAYS,
    EPOCH_CHANGED,
    EXACT_RAN,
    GRID_CHANGED,
    GRU_USED,
    HIDDEN_CHANGED,
    HYPERPARAMETER_TUNING,
    KERNEL_CHANGED,
    LABEL_CHANGED,
    LAYER_CHANGED,
    LR_CHANGED,
    LSTM_USED,
    MAX_WORKERS,
    NEW_FORWARD_N,
    OTHER_TEMPORAL_STARTED,
    PAPER_OPERATED,
    PNL_USED,
    PRIOR_VERDICT_REQUIRED,
    RUNTIME_CANDIDATE_CREATED,
    RUNTIME_CHANGED,
    SEED_N,
    SEED_SELECTION,
    SEEDS,
    SEQUENCE_CHANGED,
    STRATEGY_CREATED,
    THRESHOLD_SEARCH,
    TOPK_SEARCH,
    TRANSFORMER_USED,
    TRUE_OOS,
    WINDOW_CHANGED,
    XGBOOST_USED,
)
from research.temporal_model_probe.analyze import aggregate, control_parity, decide, seed_row
from research.temporal_model_probe.oof import process_seed
from research.temporal_model_probe.publish import (
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
PRIOR = NATIVE / "results" / "research" / "entry_sequence_representation" / "report.json"
SEQ_CACHE = NATIVE / "results" / "research" / "_work_cache" / "entry_sequence_representation"
S0_PATH = SEQ_CACHE / "arch_S0.json"
S1_PATH = SEQ_CACHE / "arch_S1.json"
ENRICHED = SEQ_CACHE / "enriched_rows.json"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "temporal_model_probe"
MODEL_SRC = Path(__file__).resolve().parent / "model.py"
TRAIN_SRC = Path(__file__).resolve().parent / "train.py"


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
                "MODEL": "SmallCausalTCN BCEWithLogits P(JOINT_IMPROVEMENT_LABEL=1)",
                "SEEDS": list(SEEDS),
                "EPOCHS": 30,
                "LR": 0.001,
                "WEIGHT_DECAY": 0.0001,
                "BATCH_SIZE": 256,
                "DROPOUT": 0.10,
                "LSTM_USED": LSTM_USED,
                "GRU_USED": GRU_USED,
                "TRANSFORMER_USED": TRANSFORMER_USED,
                "ATTENTION_USED": ATTENTION_USED,
                "XGBOOST_USED": XGBOOST_USED,
                "SEED_SELECTION": SEED_SELECTION,
                "EXACT_RAN": EXACT_RAN,
                "OTHER_TEMPORAL_STARTED": OTHER_TEMPORAL_STARTED,
            }
        ),
        "Seeds": [{"empty": True}],
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
                "ARCHITECTURE_CHANGED": ARCHITECTURE_CHANGED,
                "KERNEL_CHANGED": KERNEL_CHANGED,
                "DILATION_CHANGED": DILATION_CHANGED,
                "LAYER_CHANGED": LAYER_CHANGED,
                "HIDDEN_CHANGED": HIDDEN_CHANGED,
                "EPOCH_CHANGED": EPOCH_CHANGED,
                "LR_CHANGED": LR_CHANGED,
                "CLASS_WEIGHT_USED": CLASS_WEIGHT_USED,
                "GRID_CHANGED": GRID_CHANGED,
                "WINDOW_CHANGED": WINDOW_CHANGED,
                "CHANNEL_CHANGED": CHANNEL_CHANGED,
                "SEQUENCE_CHANGED": SEQUENCE_CHANGED,
                "LABEL_CHANGED": LABEL_CHANGED,
                "SEED_SELECTION": SEED_SELECTION,
                "TOPK_SEARCH": TOPK_SEARCH,
                "THRESHOLD_SEARCH": THRESHOLD_SEARCH,
                "PNL_USED": PNL_USED,
                "RUNTIME_CANDIDATE_CREATED": RUNTIME_CANDIDATE_CREATED,
                "STRATEGY_CREATED": STRATEGY_CREATED,
                "OTHER_TEMPORAL_STARTED": OTHER_TEMPORAL_STARTED,
                "LSTM_USED": LSTM_USED,
                "GRU_USED": GRU_USED,
                "TRANSFORMER_USED": TRANSFORMER_USED,
                "ATTENTION_USED": ATTENTION_USED,
                "XGBOOST_USED": XGBOOST_USED,
            }
        ),
    }
    if sheets_extra:
        sheets.update(sheets_extra)
    write_artifacts(report, sheets)
    print(f"VERDICT={required.get('VERDICT')}", flush=True)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(
        "STOP. No Exact. No runtime candidate. No other temporal model. Runtime unchanged. submit/cancel/live=0/0/0.",
        flush=True,
    )
    fail = required.get("VERDICT") == "TEMPORAL_MODEL_INTEGRITY_FAILED"
    return 2 if fail else 0


def _integrity(note: str, extra: dict | None = None) -> int:
    body = {
        "VERDICT": "TEMPORAL_MODEL_INTEGRITY_FAILED",
        "TEMPORAL_MODEL_PASS": False,
        "NEXT_RESEARCH": "NONE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "PRIMARY_FINDING": note,
        "BASE_PARITY": False,
        "SEED_N": SEED_N,
        "FUTURE_EVENT_USE_N": None,
        "SESSION_CARRY_N": None,
        "ITAYOSE_STATE_USE_N": None,
        "SPECIAL_STATE_USE_N": None,
        "SEQUENCE_AFTER_T0_N": None,
        "TARGET_CONTAMINATION_N": None,
        "heldout_fit_leak_n": None,
    }
    if extra:
        body.update(extra)
    return write_report(
        body,
        decision={
            "CASE": None,
            "VERDICT": "TEMPORAL_MODEL_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "PRIMARY_FINDING": note,
            "note": "STOP. Integrity failed.",
        },
    )


def _seq_integrity() -> dict[str, int]:
    tot = {
        "future_event_use_n": 0,
        "session_carry_n": 0,
        "itayose_state_use_n": 0,
        "special_state_use_n": 0,
        "sequence_after_t0_n": 0,
        "grid_bad_n": 0,
        "rows": 0,
    }
    for day in ELIGIBLE_DAYS:
        body = _load(SEQ_CACHE / f"{day}_SEQ.json")
        tot["future_event_use_n"] += int(body.get("future_event_use_n") or 0)
        tot["session_carry_n"] += int(body.get("session_carry_n") or 0)
        tot["itayose_state_use_n"] += int(body.get("itayose_state_use_n") or 0)
        tot["special_state_use_n"] += int(body.get("special_state_use_n") or 0)
        tot["sequence_after_t0_n"] += int(body.get("sequence_after_t0_n") or 0)
        tot["grid_bad_n"] += int(body.get("grid_bad_n") or 0)
        tot["rows"] += len(body.get("rows") or [])
        for rec in body.get("rows") or []:
            tot["future_event_use_n"] += 0
            if int(rec.get("grid_marks") or 37) != 37:
                tot["grid_bad_n"] += 1
    return tot


def _target_contamination_n() -> int:
    n = 0
    for path in (MODEL_SRC, TRAIN_SRC):
        text = path.read_text(encoding="utf-8")
        for token in ("T1", "T2", "MFE_600", "DOWNSIDE_AVOID"):
            if token in text:
                n += text.count(token)
    return int(n)


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE TEMPORAL JOINT MODEL ARCHITECTURE PROBE V1", flush=True)
    print("Frozen causal 1D TCN. Sequence 37x7 frozen. Direct joint label frozen. No Exact.", flush=True)

    if list(SEEDS) != [101, 202, 303] or int(SEED_N) != 3:
        print("STOP seed freeze drift", flush=True)
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
        print("STOP prior sequence verdict mismatch", preg.get("VERDICT"), flush=True)
        return _integrity("STOP. Prior SEQUENCE_SIGNAL_NOT_ROBUST required.")
    if not S0_PATH.is_file() or not S1_PATH.is_file() or not ENRICHED.is_file():
        print("STOP missing S0/S1/enriched cache", flush=True)
        return _integrity("STOP. Frozen sequence-probe caches missing.")

    s0 = _load(S0_PATH)
    s1 = _load(S1_PATH)
    parity = control_parity(s0, s1)
    if not parity.get("BASE_PARITY"):
        print("STOP BASE_PARITY", parity, flush=True)
        return _integrity("STOP. S0/S1 did not reproduce frozen sequence-probe controls.", extra={"parity": parity})
    print("BASE_PARITY true", flush=True)

    tot = _seq_integrity()
    contamination_n = _target_contamination_n()
    if (
        tot["future_event_use_n"]
        or tot["session_carry_n"]
        or tot["itayose_state_use_n"]
        or tot["special_state_use_n"]
        or tot["sequence_after_t0_n"]
        or tot["grid_bad_n"]
        or contamination_n
    ):
        note = (
            f"future={tot['future_event_use_n']} carry={tot['session_carry_n']} "
            f"itay={tot['itayose_state_use_n']} spec={tot['special_state_use_n']} "
            f"after_t0={tot['sequence_after_t0_n']} grid_bad={tot['grid_bad_n']} "
            f"contam={contamination_n}"
        )
        print("STOP sequence integrity", note, flush=True)
        return _integrity(
            f"STOP. {note}",
            extra={
                "BASE_PARITY": True,
                "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
                "SESSION_CARRY_N": tot["session_carry_n"],
                "ITAYOSE_STATE_USE_N": tot["itayose_state_use_n"],
                "SPECIAL_STATE_USE_N": tot["special_state_use_n"],
                "SEQUENCE_AFTER_T0_N": tot["sequence_after_t0_n"],
                "TARGET_CONTAMINATION_N": contamination_n,
            },
        )

    CACHE.mkdir(parents=True, exist_ok=True)
    jobs = []
    got = []
    for seed in SEEDS:
        fp = CACHE / f"seed_{seed}.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("seed") == seed and saved.get("daily_mfe"):
            got.append(saved)
            print(f"seed cache-hit {seed}", flush=True)
            continue
        jobs.append({"seed": seed, "rows_path": str(ENRICHED), "days": list(ELIGIBLE_DAYS)})
    print(f"tcn seed jobs={len(jobs)}", flush=True)
    for body in _pool(process_seed, jobs, "SEED", "seed"):
        if body.get("ok"):
            _save_json(CACHE / f"seed_{body.get('seed')}.json", body)
        got.append(body)
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != int(SEED_N):
        print("STOP TCN LODO failed", [(b.get("seed"), b.get("blocker")) for b in fail], flush=True)
        return _integrity("STOP. TCN 3-seed LODO failed.")
    got.sort(key=lambda b: int(b.get("seed") or 0))
    leak_n = sum(int(b.get("heldout_fit_leak_n") or 0) for b in got)
    if leak_n != 0:
        print("STOP heldout leak", leak_n, flush=True)
        return _integrity("STOP. heldout_fit_leak_n != 0.", extra={"heldout_fit_leak_n": leak_n, "BASE_PARITY": True})

    agg = aggregate(got)
    decision = decide(agg, parity_ok=True, integ_ok=True, integ_note="ok")
    seed_rows = [seed_row(b) for b in got]
    required = {
        "BASE_PARITY": True,
        "SEED_N": SEED_N,
        "MEDIAN_TCN_MFE_DELTA": agg.get("MEDIAN_TCN_MFE_DELTA"),
        "MEDIAN_TCN_DOWNSIDE_DELTA": agg.get("MEDIAN_TCN_DOWNSIDE_DELTA"),
        "MEDIAN_TCN_JOINT_RATE": agg.get("MEDIAN_TCN_JOINT_RATE"),
        "SEEDS_MFE_POSITIVE_N": agg.get("SEEDS_MFE_POSITIVE_N"),
        "SEEDS_DOWNSIDE_POSITIVE_N": agg.get("SEEDS_DOWNSIDE_POSITIVE_N"),
        "CONSENSUS_MFE_POS_DAYS": agg.get("CONSENSUS_MFE_POS_DAYS"),
        "CONSENSUS_MFE_NEG_DAYS": agg.get("CONSENSUS_MFE_NEG_DAYS"),
        "CONSENSUS_DOWNSIDE_POS_DAYS": agg.get("CONSENSUS_DOWNSIDE_POS_DAYS"),
        "CONSENSUS_DOWNSIDE_NEG_DAYS": agg.get("CONSENSUS_DOWNSIDE_NEG_DAYS"),
        "CONSENSUS_MFE_EX_BEST_DAY": agg.get("CONSENSUS_MFE_EX_BEST_DAY"),
        "CONSENSUS_MFE_EX_TOP3_DAYS": agg.get("CONSENSUS_MFE_EX_TOP3_DAYS"),
        "CONSENSUS_DOWNSIDE_EX_BEST_DAY": agg.get("CONSENSUS_DOWNSIDE_EX_BEST_DAY"),
        "CONSENSUS_DOWNSIDE_EX_TOP3_DAYS": agg.get("CONSENSUS_DOWNSIDE_EX_TOP3_DAYS"),
        "DELTA_MFE_VS_FLAT_RF": agg.get("DELTA_MFE_VS_FLAT_RF"),
        "DELTA_DOWNSIDE_VS_FLAT_RF": agg.get("DELTA_DOWNSIDE_VS_FLAT_RF"),
        "DELTA_JOINT_RATE_VS_FLAT_RF": agg.get("DELTA_JOINT_RATE_VS_FLAT_RF"),
        "TEMPORAL_ORDER_INCREMENTAL": agg.get("TEMPORAL_ORDER_INCREMENTAL"),
        "MEDIAN_ROC_AUC": agg.get("MEDIAN_ROC_AUC"),
        "MEDIAN_AVERAGE_PRECISION": agg.get("MEDIAN_AVERAGE_PRECISION"),
        "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
        "SESSION_CARRY_N": tot["session_carry_n"],
        "ITAYOSE_STATE_USE_N": tot["itayose_state_use_n"],
        "SPECIAL_STATE_USE_N": tot["special_state_use_n"],
        "SEQUENCE_AFTER_T0_N": tot["sequence_after_t0_n"],
        "TARGET_CONTAMINATION_N": contamination_n,
        "heldout_fit_leak_n": leak_n,
        "TEMPORAL_MODEL_PASS": decision.get("TEMPORAL_MODEL_PASS"),
        "PRIMARY_FINDING": decision.get("PRIMARY_FINDING"),
        "NEXT_RESEARCH": decision.get("NEXT_RESEARCH"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "parity": parity,
        "gates": agg.get("gates"),
        "gate_fail": agg.get("gate_fail"),
        "S0_MFE_DELTA": s0.get("TOP3_MFE_DELTA"),
        "S0_DOWNSIDE_DELTA": s0.get("TOP3_DOWNSIDE_DELTA"),
        "S0_JOINT_RATE": s0.get("JOINT_COHORT_SUCCESS_RATE"),
        "S1_MFE_DELTA": s1.get("TOP3_MFE_DELTA"),
        "S1_DOWNSIDE_DELTA": s1.get("TOP3_DOWNSIDE_DELTA"),
        "S1_JOINT_RATE": s1.get("JOINT_COHORT_SUCCESS_RATE"),
    }
    print(
        f"pass={decision.get('TEMPORAL_MODEL_PASS')} CASE={decision.get('CASE')} "
        f"med_mfe={agg.get('MEDIAN_TCN_MFE_DELTA')} med_dn={agg.get('MEDIAN_TCN_DOWNSIDE_DELTA')} "
        f"med_joint={agg.get('MEDIAN_TCN_JOINT_RATE')} incremental={agg.get('TEMPORAL_ORDER_INCREMENTAL')}",
        flush=True,
    )
    return write_report(
        required,
        decision=decision,
        extra={"seeds": seed_rows, "aggregate": {k: v for k, v in agg.items() if k != "daily"}, "daily": agg.get("daily")},
        sheets_extra={
            "Seeds": seed_rows,
            "Gates": [ {"PASS": agg.get("TEMPORAL_MODEL_PASS"), **(agg.get("gates") or {}), "gate_fail": agg.get("gate_fail")} ],
            "Daily": agg.get("daily") or [{"empty": True}],
            "Integrity": kv_rows(
                {
                    "FUTURE_EVENT_USE_N": tot["future_event_use_n"],
                    "SESSION_CARRY_N": tot["session_carry_n"],
                    "ITAYOSE_STATE_USE_N": tot["itayose_state_use_n"],
                    "SPECIAL_STATE_USE_N": tot["special_state_use_n"],
                    "SEQUENCE_AFTER_T0_N": tot["sequence_after_t0_n"],
                    "TARGET_CONTAMINATION_N": contamination_n,
                    "heldout_fit_leak_n": leak_n,
                    "SEQUENCE_ROWS": tot["rows"],
                }
            ),
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
