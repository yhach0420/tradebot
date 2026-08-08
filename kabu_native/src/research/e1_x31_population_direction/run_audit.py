"""E1_X31 runner: population direction audit + conditional SHORT discovery."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x28_executable_joint.board import load_board_events, verify_board_mapping

from . import (
    ANALYSIS_ID,
    BOARD_MAPPING_SHA,
    DOCUMENT_ID,
    FORBIDDEN_FROM,
    HISTORICAL_DAYS,
    SOURCE_X30_RUN,
    VERDICT_MARKET,
    VERDICT_NEG_NOT_SHORT,
    VERDICT_NO_STABLE,
    VERDICT_SHORT_BASELINE_NO,
    VERDICT_SHORT_CV_NO,
    VERDICT_SHORT_FOUND,
)
from .analyze import (
    candidate_horizon_summary,
    day_level_audit,
    interpret_population,
    late_chase_diagnostic,
    loso_delta,
    time_of_day_audit,
)
from .controls import build_controls
from .identity import ab_identity, reproduce_population
from .publish import publish
from .short_cv import run_short_nested_cv
from .short_labels import compute_short_arrays, short_baseline_summary

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x31_population_direction"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x31_population_direction.py"
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src")}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(tp), "-q", "--tb=line"],
        cwd=str(NATIVE), capture_output=True, text=True, env=env,
    )
    out = (p.stdout or "") + (p.stderr or "")
    passed = failed = 0
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m2 = re.search(r"(\d+) failed", out)
    if m2:
        failed = int(m2.group(1))
    return {"passed": passed, "failed": failed, "returncode": p.returncode, "tail": out[-1500:]}


def _load_boards(rows, allowed):
    keys = sorted({(r["date"], r["symbol"]) for r in rows if r["date"] in allowed})
    cache = {}
    print(f"  boards {len(keys)}...", flush=True)

    def _one(k):
        return k, load_board_events(k[0], k[1])

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(_one, k) for k in keys]
        done = 0
        for fut in as_completed(futs):
            k, b = fut.result()
            cache[k] = b
            done += 1
            if done % 40 == 0 or done == len(keys):
                print(f"    {done}/{len(keys)}", flush=True)
    return cache


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x31_dir_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok"), mapping
    assert mapping.get("mapping_sha") == BOARD_MAPPING_SHA

    print("=== X30 population identity ===", flush=True)
    rows, labels, identity = reproduce_population()
    ab = ab_identity(rows, labels, identity)
    assert ab["ok"], ab
    print(f"  n={identity['population_n']} valid={identity['valid_n']} ab={ab['ok']}", flush=True)

    print("=== boards ===", flush=True)
    board_by_key = _load_boards(rows, set(HISTORICAL_DAYS))

    print("=== Phase A: candidate summary ===", flush=True)
    cand = candidate_horizon_summary(labels)

    print("=== controls (same-symbol + market/time) ===", flush=True)
    controls = build_controls(rows=rows, labels=labels, board_by_key=board_by_key)
    print(
        f"  same-sym n={controls['n_same_symbol_episodes']} "
        f"ret300={controls['same_symbol_control'][300]['mean']} "
        f"mkt300={controls['market_time_control'][300]['mean']}",
        flush=True,
    )

    day_audit = day_level_audit(rows, labels, controls)
    loso = loso_delta(rows, labels, controls)
    tod = time_of_day_audit(rows, labels, controls)
    late = late_chase_diagnostic(rows, labels)
    interp = interpret_population(cand, controls, day_audit, loso)
    print(f"  case={interp['case']} phase_b={interp['phase_b_eligible']}", flush=True)

    interim = {
        "run_id": run_id,
        "analysis_id": ANALYSIS_ID,
        "source_x30_run_id": SOURCE_X30_RUN,
        "population_n": identity["population_n"],
        "valid_n": identity["valid_n"],
        "opened_20260810": False,
        "entry_only_no_exit": True,
        "no_short_order_implementation": True,
        "no_margin_path_enable": True,
        "population_case": interp["case"],
        "short_phase_b_eligible": interp["phase_b_eligible"],
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    short_baseline = None
    short_cv = None
    short_signal_found = False
    verdict = VERDICT_NO_STABLE

    if interp["case"] == "MARKET_DOWNWARD_BACKGROUND":
        verdict = VERDICT_MARKET
    elif interp["case"] == "NO_STABLE_POPULATION_DIRECTION":
        verdict = VERDICT_NO_STABLE
    elif not interp["phase_b_eligible"]:
        # candidate-down but day/symbol gates fail
        verdict = VERDICT_NO_STABLE
    else:
        # Phase B: SHORT baseline on same candidate population
        print("=== Phase B: SHORT executable baseline ===", flush=True)
        short_cache = OUT / "_short_labels_cache.npz"
        if short_cache.exists():
            z = np.load(short_cache)
            short = {k: z[k] for k in z.files}
            if len(short.get("valid", [])) != len(rows):
                short = compute_short_arrays(rows=rows, board_by_key=board_by_key)
                np.savez_compressed(short_cache, **short)
        else:
            short = compute_short_arrays(rows=rows, board_by_key=board_by_key)
            np.savez_compressed(short_cache, **short)

        dates = np.array([r["date"] for r in rows])
        symbols = np.array([r["symbol"] for r in rows])
        short_baseline = short_baseline_summary(short, dates, symbols)
        print(f"  short baseline={short_baseline['baseline_status']}", flush=True)

        if not short_baseline["baseline_pass"]:
            # LONG shows candidate-down but SHORT exec fails
            r300 = (short_baseline.get("return_300") or {}).get("mean")
            if r300 is not None and r300 <= 0:
                verdict = VERDICT_NEG_NOT_SHORT
            else:
                verdict = VERDICT_SHORT_BASELINE_NO
        else:
            print("=== Phase C: SHORT nested CV ===", flush=True)
            short_cv = run_short_nested_cv(rows=rows, short=short)
            short_signal_found = bool(short_cv.get("short_signal_found"))
            verdict = VERDICT_SHORT_FOUND if short_signal_found else VERDICT_SHORT_CV_NO

    print("=== tests ===", flush=True)
    # refresh interim for tests
    interim.update({
        "population_case": interp["case"],
        "short_phase_b_eligible": interp["phase_b_eligible"],
        "board_mapping_sha": BOARD_MAPPING_SHA,
        "historical_days": list(HISTORICAL_DAYS),
    })
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")
    tests = _run_tests()

    report = {
        **interim,
        "document_id": DOCUMENT_ID,
        "verdict": verdict,
        "identity": identity,
        "ab_determinism": ab,
        "candidate_summary": {str(k): v for k, v in cand.items()},
        "candidate_ret300": interp["candidate_ret300"],
        "candidate_ret600": interp["candidate_ret600"],
        "same_symbol_control_ret300": interp["same_symbol_control_ret300"],
        "same_symbol_control_ret600": interp["same_symbol_control_ret600"],
        "market_time_control_ret300": interp["market_time_control_ret300"],
        "market_time_control_ret600": interp["market_time_control_ret600"],
        "candidate_minus_control_300": interp["candidate_minus_control_300"],
        "candidate_minus_control_600": interp["candidate_minus_control_600"],
        "negative_support_days": {
            "ret300": day_audit["negative_delta300_days"],
            "ret600": day_audit["negative_delta600_days"],
            "of": 14,
            "ok": day_audit["day_support_ok"],
        },
        "day_audit": day_audit,
        "loso": loso,
        "time_of_day": tod,
        "time_of_day_tag": tod.get("tag"),
        "late_chase": late,
        "late_chase_tag": late.get("tag"),
        "short_phase_b_eligible": interp["phase_b_eligible"],
        "short_baseline": short_baseline,
        "short_nested_cv": short_cv,
        "short_signal_found": short_signal_found,
        "opened_20260810": False,
        "must_be_false_20260810": True,
        "exit_research_started": False,
        "tests": tests,
        "safety": {
            "submit_cancel_live": "0/0/0",
            "paper_only": True,
            "no_runtime_entry_exit_universe_change": True,
            "no_short_order_implementation": True,
            "no_margin_path_enable": True,
            "no_discord_production": True,
        },
        "artifacts": ["report.json", "report.md", "audit.xlsx"],
        "forbidden_from": FORBIDDEN_FROM,
    }

    # strip bulky raw control rows from report
    controls_light = {
        "same_symbol_control": {str(k): v for k, v in controls["same_symbol_control"].items()},
        "market_time_control": {str(k): v for k, v in controls["market_time_control"].items()},
        "n_same_symbol_episodes": controls["n_same_symbol_episodes"],
        "control_seed": controls["control_seed"],
    }
    report["controls"] = controls_light

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": verdict,
            "case": interp["case"],
            "cand300": interp["candidate_ret300"],
            "ctrl300": interp["same_symbol_control_ret300"],
            "mkt300": interp["market_time_control_ret300"],
            "delta300": interp["candidate_minus_control_300"],
            "phase_b": interp["phase_b_eligible"],
            "short_found": short_signal_found,
            "opened_20260810": False,
        }],
        "day_audit": day_audit["days"],
        "tod": tod["buckets"],
        "loso": [loso],
        "late_chase": [late],
        "short_baseline": [short_baseline] if short_baseline else [{"status": "skipped"}],
    }
    publish(OUT, report, sheets)
    print(f"=== DONE verdict={verdict} ===", flush=True)
    return report


if __name__ == "__main__":
    main()
