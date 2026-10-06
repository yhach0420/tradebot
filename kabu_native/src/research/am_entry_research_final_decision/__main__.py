"""Offline AM ENTRY development close + C0 prospective freeze. No Runtime write. No Paper. No new fit."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_entry_profit_improvement import (
    C14_CHANGED,
    C14_ID,
    CANCEL_N,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    LIVE_ORDER_N,
    NEW_FEATURE_N,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
    WAIT_SEARCH_N,
)
from research.am_entry_research_final_decision import (
    ANALYSIS_ID,
    C0_LOCKED,
    CLOSED_LINES,
    CURRENT_LOCKED,
    FORMAL_CANDIDATE,
    NEXT,
    PAPER_OPERATION_N,
    PAPER_STRATEGY_ADOPTION_ALLOWED,
    PROSPECTIVE_CHALLENGER_ID,
    PROSPECTIVE_CHALLENGER_NAME,
    PROSPECTIVE_STATUS,
    RUNTIME_ADOPTION_ALLOWED,
    RUNTIME_CHANGE_N,
    VERDICT,
)
from research.am_entry_research_final_decision.analyze import evidence_ok
from research.am_entry_research_final_decision.publish import OUT, build_markdown, kv_rows, write_artifacts
from research.am_entry_research_final_decision.spec import canonical_c0_spec, spec_sha256
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PRIOR = NATIVE / "results" / "research" / "am_entry_architecture_final_reassessment" / "report.json"

INTEGRITY_ZERO_KEYS = (
    "NEW_MODEL_N",
    "NEW_FEATURE_N",
    "NEW_TARGET_N",
    "MODEL_SEARCH_N",
    "HYPERPARAMETER_SEARCH_N",
    "SCORE_BLEND_N",
    "SCORE_THRESHOLD_SEARCH_N",
    "PROBABILITY_THRESHOLD_SEARCH_N",
    "WAIT_SEARCH_N",
    "EXIT_CHANGE_N",
    "CURRENT_POLICY_CHANGE_N",
    "RUNTIME_CHANGE_N",
    "PAPER_OPERATION_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "STRATEGY_REPLAY_N",
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _empty_required(*, verdict: str, nxt: str, sha: str | None = None) -> dict[str, Any]:
    return {
        "AM_ENTRY_FULL_GATE_POLICY_FOUND": False,
        "AM_ENTRY_DEVELOPMENT_ECONOMIC_EDGE_FOUND": False,
        "BEST_DEVELOPMENT_ARCHITECTURE": None,
        "C0_NET": None,
        "C0_PF": None,
        "C0_MAX_DD": None,
        "C0_PAIRED_MEDIAN": None,
        "C0_POS_DAYS": None,
        "C0_NEG_DAYS": None,
        "C0_EX_BEST": None,
        "C0_EX_TOP3": None,
        "C0_PROSPECTIVE_SPEC_SHA256": sha,
        "AM_EXISTING_18D_ARCHITECTURE_SEARCH_CLOSED": False,
        "RUNTIME_ADOPTION_ALLOWED": False,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": verdict,
        "NEXT": nxt,
    }


def _integrity(msg: str, extra: dict | None = None, *, sha: str | None = None) -> int:
    required = _empty_required(
        verdict="AM_ENTRY_FINAL_DECISION_INTEGRITY_FAILED",
        nxt="STOP",
        sha=sha,
    )
    required["STOP_REASON"] = msg
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "closed_lines": list(CLOSED_LINES),
        **(extra or {}),
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(
        report,
        {
            "required": kv_rows(required),
            "integrity": kv_rows({"STOP_REASON": msg}),
            "closed_lines": [{"line": x} for x in CLOSED_LINES],
        },
    )
    print(msg, flush=True)
    return 2


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get(
        "PYTHONPATH", ""
    )
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM ENTRY RESEARCH FINAL DECISION V1", flush=True)
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
    if RUNTIME_ADOPTION_ALLOWED is not False or FORMAL_CANDIDATE is not False:
        return _integrity("STOP. Runtime/formal adoption drifted.")
    if int(RUNTIME_CHANGE_N) != 0 or int(PAPER_OPERATION_N) != 0:
        return _integrity("STOP. Runtime/paper operation counters drifted.")
    if W5_RUNTIME_ADOPTED is not False or RUNTIME_CHANGED is not False or C14_CHANGED is not False:
        return _integrity("STOP. Runtime/C14/W5 freeze drifted.")

    spec = canonical_c0_spec()
    sha = spec_sha256(spec)
    print(f"C0_PROSPECTIVE_SPEC_SHA256 {sha}", flush=True)

    if not PRIOR.is_file():
        return _integrity("STOP. Prior dual-confirmation report missing.", sha=sha)
    prior = _load(PRIOR)
    ok, fails = evidence_ok(prior, abs_tol=PARITY_ABS_TOL)
    print(f"PRIOR_EVIDENCE_OK={ok}", flush=True)
    if not ok:
        return _integrity(
            "STOP. Locked C0/CURRENT evidence did not match prior official report.",
            extra={"evidence_fails": fails},
            sha=sha,
        )

    leak = {
        "NEW_MODEL_N": 0,
        "NEW_FEATURE_N": NEW_FEATURE_N,
        "NEW_TARGET_N": 0,
        "MODEL_SEARCH_N": 0,
        "HYPERPARAMETER_SEARCH_N": 0,
        "SCORE_BLEND_N": 0,
        "SCORE_THRESHOLD_SEARCH_N": 0,
        "PROBABILITY_THRESHOLD_SEARCH_N": 0,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "EXIT_CHANGE_N": 0,
        "CURRENT_POLICY_CHANGE_N": 0,
        "RUNTIME_CHANGE_N": RUNTIME_CHANGE_N,
        "PAPER_OPERATION_N": PAPER_OPERATION_N,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "STRATEGY_REPLAY_N": 0,
        "C14_CHANGED": C14_CHANGED,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "PAPER_OPERATED": PAPER_OPERATED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "PRIOR_EVIDENCE_OK": True,
    }
    if any(int(leak.get(k) or 0) != 0 for k in INTEGRITY_ZERO_KEYS):
        return _integrity("STOP. Integrity counters non-zero.", extra={"integrity": leak}, sha=sha)

    required = {
        "AM_ENTRY_FULL_GATE_POLICY_FOUND": False,
        "AM_ENTRY_DEVELOPMENT_ECONOMIC_EDGE_FOUND": True,
        "BEST_DEVELOPMENT_ARCHITECTURE": PROSPECTIVE_CHALLENGER_ID,
        "C0_NET": C0_LOCKED["NET"],
        "C0_PF": C0_LOCKED["PF"],
        "C0_MAX_DD": C0_LOCKED["MAX_DD"],
        "C0_PAIRED_MEDIAN": C0_LOCKED["PAIRED_MEDIAN"],
        "C0_POS_DAYS": C0_LOCKED["PAIRED_POS_DAYS"],
        "C0_NEG_DAYS": C0_LOCKED["PAIRED_NEG_DAYS"],
        "C0_EX_BEST": C0_LOCKED["EX_BEST"],
        "C0_EX_TOP3": C0_LOCKED["EX_TOP3"],
        "C0_PROSPECTIVE_SPEC_SHA256": sha,
        "AM_EXISTING_18D_ARCHITECTURE_SEARCH_CLOSED": True,
        "RUNTIME_ADOPTION_ALLOWED": False,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": VERDICT,
        "NEXT": NEXT,
        "PROSPECTIVE_CHALLENGER_NAME": PROSPECTIVE_CHALLENGER_NAME,
        "PROSPECTIVE_STATUS": PROSPECTIVE_STATUS,
        "PAPER_STRATEGY_ADOPTION_ALLOWED": PAPER_STRATEGY_ADOPTION_ALLOWED,
        "FORMAL_CANDIDATE": FORMAL_CANDIDATE,
        "FULL_GATE_PASS": False,
        "CURRENT_PRESERVATION_PASS": True,
        "C0_PAIRED_ZERO_DAYS": C0_LOCKED["PAIRED_ZERO_DAYS"],
        "C0_DELTA_NET": C0_LOCKED["DELTA_NET"],
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "W5_RUNTIME_ADOPTED": False,
    }
    extra = {
        "c0_spec": spec,
        "closed_lines": list(CLOSED_LINES),
        "current_locked": CURRENT_LOCKED,
        "c0_locked": C0_LOCKED,
        "integrity": leak,
        "prior_source": str(PRIOR),
        "same_data_search_forbidden": [
            "C0 threshold tuning",
            "B0/B1 score blending",
            "confirmation threshold",
            "fallback rule",
            "Top2 / Top3 augment",
            "X14 subset",
            "RF tuning",
            "new feature",
            "new target",
            "new regime feature",
            "RAW23",
            "GRU",
            "LSTM",
            "TCN",
            "Transformer",
            "event window search",
            "time filter",
            "symbol filter",
            "price filter",
            "day filter",
            "WAIT tuning",
            "EXIT tuning",
            "risk gate tuning",
        ],
        "future_oos_rule": {
            "require_sha256_match": True,
            "sha256_key": "C0_PROSPECTIVE_SPEC_SHA256",
            "do_not_require_development_net_pf_reproduction": True,
            "metrics": [
                "NET",
                "PF",
                "DD",
                "paired positive / negative / zero days",
                "paired median daily delta",
                "ex-best",
                "ex-top3",
                "CURRENT preservation",
            ],
        },
    }
    sheets = {
        "required": kv_rows(required),
        "c0_spec": kv_rows(spec) + [{"key": "C0_PROSPECTIVE_SPEC_SHA256", "value": sha}],
        "closed_lines": [{"line": x} for x in CLOSED_LINES],
        "evidence": kv_rows(
            {
                **{f"CURRENT_{k}": v for k, v in CURRENT_LOCKED.items()},
                **{f"C0_{k}": v for k, v in C0_LOCKED.items()},
                "PRIOR_SOURCE": str(PRIOR),
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
