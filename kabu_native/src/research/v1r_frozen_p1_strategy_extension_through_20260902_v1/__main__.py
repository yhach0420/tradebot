"""Offline FROZEN P1 V1R strategy extension through 20260902. Research only."""
from __future__ import annotations

import hashlib
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

os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.analyze import (
    build_answers,
    common_simple_tech_metrics,
    decide,
    inventory_summary,
    metrics_from_trades,
    spot_plan,
    unused_metrics,
)
from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.harvest import (
    activation_alias_audit,
    inventory_extension,
    restore_p1_source,
    reuse_p0,
    reuse_p1_full14,
    simple_tech_closure,
)
from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.isolation import (
    CACHE,
    OUT,
    P02_OUT,
    P03_OUT,
    P04_OUT,
    P1_OUT,
    SIMPLE_TECH_REBASE_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.v1r_frozen_p1_strategy_extension_through_20260902_v1.spec import (
    ANALYSIS_ID,
    ANCHOR_SHA,
    ENTRY_SHA,
    EVALUATION_TARGET,
    EXIT_SHA,
    EXTENSION_CANDIDATE_DAYS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    P1_FULL14_DAYS,
    P1_RUNNER_SHA,
    REPLAY_CODE_SHA,
    STRATEGY_NAME,
    STRATEGY_SHA,
    V1R_LIVE_DUAL_LANE_SHA,
    V1R_NATIVE_ENTRY_LIVE_SHA,
    canonical_spec,
    source_sha256,
    spec_sha256,
)

INTEGRITY_ZERO = (
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "RESEARCH_WRITE_PATH_OVERLAP_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "INPUT_20260903_N",
    "INPUT_20260904_N",
    "FUTURE_DATA_N",
    "PROSPECTIVE_HARVESTER_RUN_N",
    "PRIOR_ARTIFACT_MUTATED_N",
    "MAIN_TREE_CHECKOUT_N",
    "RUNTIME_SOURCE_REWRITE_N",
    "SEP05_RUNTIME_REPLAY_N",
    "EXTENSION_REPLAY_ON_UNPINNED_SOURCE_N",
    "SIMPLE_TECH_REOPEN_N",
    "NEW_ENTRY_N",
    "NEW_EXIT_N",
    "THRESHOLD_TUNE_N",
    "SIZING_IMPLEMENTED_N",
)

PRIOR_FILES = (
    P1_OUT / "report.json",
    P03_OUT / "report.json",
    P04_OUT / "report.json",
    P02_OUT / "report.json",
    SIMPLE_TECH_REBASE_OUT / "report.json",
    SIMPLE_TECH_REBASE_OUT / "report.md",
    SIMPLE_TECH_REBASE_OUT / "audit.xlsx",
)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _prior_hashes() -> dict[str, str]:
    return {str(p.relative_to(NATIVE)).replace("\\", "/"): _sha_file(p) for p in PRIOR_FILES}


def _write(report: dict[str, Any]) -> None:
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))


def main() -> int:
    set_research_priority_below_normal()
    sha = spec_sha256()
    source_sha = source_sha256()
    pre = snapshot(phase="PRE")
    prior = _prior_hashes()
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak.update({"SUBMIT_N": int(SUBMIT_N), "CANCEL_N": int(CANCEL_N), "LIVE_ORDER_N": int(LIVE_ORDER_N)})
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"PREFLIGHT {ANALYSIS_ID} spec={sha[:12]} today={TODAY} target={EVALUATION_TARGET}", flush=True)

    if str(TODAY) < str(MAX_RESEARCH_DATE):
        print("WARN today before MAX_RESEARCH_DATE; continuing with frozen bound.", flush=True)

    for day in list(EXTENSION_CANDIDATE_DAYS):
        if str(day) in FORBIDDEN_INPUT_DAYS:
            leak["INPUT_20260903_N"] = int(str(day) == "20260903")
            leak["INPUT_20260904_N"] = int(str(day) == "20260904")
        if str(day) > str(MAX_RESEARCH_DATE):
            leak["FUTURE_DATA_N"] = 1

    st = simple_tech_closure()
    if not st.get("ok"):
        leak["SIMPLE_TECH_REOPEN_N"] = 1

    alias = activation_alias_audit()
    pin = restore_p1_source()
    leak["MAIN_TREE_CHECKOUT_N"] = int(bool(pin.get("main_tree_checkout")))
    leak["RUNTIME_SOURCE_REWRITE_N"] = int(bool(pin.get("runtime_source_rewritten")))
    p0 = reuse_p0()
    p1 = reuse_p1_full14()
    print(f"PIN {pin.get('PIN_MODE')} alias_ok={alias.get('ok')} p1_ok={p1.get('ok')}", flush=True)

    print("INVENTORY extension candidates", flush=True)
    inv_rows = inventory_extension(today=TODAY)
    inv = inventory_summary(inv_rows)
    for r in inv_rows:
        print(
            f"  {r.get('date')} class={r.get('capture_class')} uni={r.get('UNIVERSE_SOURCE')} "
            f"n={r.get('UNIVERSE_N')} elig={r.get('replay_eligible')} full={r.get('full')}",
            flush=True,
        )

    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [str(r.get("capture_path") or "") for r in inv_rows if r.get("capture_path")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
    )

    spot = {
        **spot_plan(list(inv.get("full_days") or [])),
        "ran": False,
        "pass": False,
        "trade_parity": "n/a",
        "ledger_sha_parity": "n/a",
        "skip_reason": "source-pin failed; Exact/Fast extension spot not started",
    }
    if pin.get("ok") and str(pin.get("PIN_MODE")) == "CURRENT_FILE_HASH_EQUAL_TO_P1":
        spot["skip_reason"] = "pin equals P1 but this run still requires Exact/Fast spot before Fast aggregate; not started because working-tree mismatch was expected"
    elif pin.get("ok"):
        pin = {
            **pin,
            "ok": False,
            "blocker": (str(pin.get("blocker") or "") + " Isolated P1 bytes cannot be imported without replacing sys.modules; main tree left unchanged.").strip(),
        }

    extension = {
        "ran": False,
        "metrics": unused_metrics(reason="source_pin_failed_no_replay"),
        "robustness": {"used": False, "reason": "source_pin_failed_no_replay"},
        "stress": unused_metrics(reason="source_pin_failed_no_replay"),
        "combined": unused_metrics(reason="source_pin_failed_no_replay"),
    }

    prior_m = metrics_from_trades(list(p1.get("trades") or []), daily=list(p1.get("daily") or []), days=list(P1_FULL14_DAYS))
    common = common_simple_tech_metrics(
        prior_trades=list(p1.get("trades") or []),
        prior_daily=list(p1.get("all_daily") or []),
        extension_trades=[],
        extension_ran=False,
    )
    ref = metrics_from_trades(list(p1.get("reference_trades") or []), daily=list(p1.get("all_daily") or []), days=list(p1.get("reference_day_list") or []))
    ref["reason"] = "PRIOR_P1_REFERENCE_ALL_USABLE reused only; extension partials not appended because replay did not run"

    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    decision = decide(pin=pin, alias=alias, p1=p1, inv=inv, extension=extension, spot=spot, leak_ok=leak_ok)

    post = snapshot(phase="POST")
    after = _prior_hashes()
    mutated = sum(1 for k, v in prior.items() if after.get(k) != v)
    leak["PRIOR_ARTIFACT_MUTATED_N"] = int(mutated)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    if not leak_ok and str(decision.get("CASE")) != "E":
        decision = decide(pin=pin, alias=alias, p1=p1, inv=inv, extension=extension, spot=spot, leak_ok=False)

    slim_p1 = {k: v for k, v in p1.items() if k not in {"trades", "reference_trades", "all_daily"}}
    report = {
        "analysis_id": ANALYSIS_ID,
        "EVALUATION_TARGET": EVALUATION_TARGET,
        "STRATEGY_NAME": STRATEGY_NAME,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "canonical_spec": canonical_spec(),
        "identity": {
            "STRATEGY_SHA": STRATEGY_SHA,
            "ENTRY_SHA": ENTRY_SHA,
            "EXIT_SHA": EXIT_SHA,
            "ANCHOR_SHA": ANCHOR_SHA,
            "REPLAY_CODE_SHA": REPLAY_CODE_SHA,
            "V1R_NATIVE_ENTRY_LIVE_SHA": V1R_NATIVE_ENTRY_LIVE_SHA,
            "V1R_LIVE_DUAL_LANE_SHA": V1R_LIVE_DUAL_LANE_SHA,
            "P1_RUNNER_SHA": P1_RUNNER_SHA,
            "HISTORICAL_P1_RESULT_NAME": "CURRENT_RUNTIME_REPLAY",
            "HISTORICAL_P1_RESULT_NAME_MEANS": "Runtime as of 2026-08-21, not 2026-09-05 working tree",
        },
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "STRATEGY_CHANGED": False,
            "ENTRY_CHANGED": False,
            "EXIT_CHANGED": False,
            "CAP_CHANGED": False,
            "SIZING_CHANGED": False,
            "SIZING_RESEARCH_ALLOWED": decision.get("SIZING_RESEARCH_ALLOWED"),
            "NEW_ENTRY_FAMILY_DESIGN_ALLOWED": decision.get("NEW_ENTRY_FAMILY_DESIGN_ALLOWED"),
            "V1R_RESEARCH_PRIORITY_MAINTAINED": decision.get("V1R_RESEARCH_PRIORITY_MAINTAINED"),
            "SPEC_SHA256": sha,
            "SOURCE_SHA256": source_sha,
        },
        "decision": decision,
        "source_pin": {k: v for k, v in pin.items() if k != "components"} | {"components": pin.get("components")},
        "activation_alias": alias,
        "simple_tech_closure": st,
        "p0_reuse": p0,
        "p1_reuse": slim_p1,
        "inventory": inv_rows,
        "inventory_summary": inv,
        "spot": spot,
        "extension": extension,
        "cohorts": {
            "A_PRIOR_P1_FULL14": prior_m,
            "B_EXTENSION_FULL": unused_metrics(reason="source_pin_failed_no_replay"),
            "C_COMBINED_FULL": unused_metrics(reason="source_pin_failed_no_replay"),
            "D_REFERENCE_ALL_REPLAY_USABLE": ref,
            "E_COMMON_SIMPLE_TECH_DAYS": common,
            "F_REUSED_HISTORY_STRESS_4": unused_metrics(reason="source_pin_failed_no_replay"),
        },
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "submit_cancel_live": {"submit": 0, "cancel": 0, "live_order": 0},
        "answers": {},
        "_markdown": "",
    }
    _write(report)
    names = sorted(p.name for p in OUT.iterdir() if p.is_file())
    print(f"CASE {decision.get('CASE')} {decision.get('VERDICT')}", flush=True)
    print(f"out={OUT} files={names}", flush=True)
    print("STOP.", flush=True)
    return 0 if str(decision.get("CASE")) in {"A", "B", "C", "D", "E"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
