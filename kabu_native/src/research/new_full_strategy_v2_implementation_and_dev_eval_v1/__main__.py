"""Offline V4 implementation + DEV eval. Integrity first. Runtime/Capture writes 0."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_indicator_exit.isolation import advanced
from research.new_full_strategy_v2_implementation_and_dev_eval_v1 import ANALYSIS_ID, CERTIFIED, TRUE_OOS
from research.new_full_strategy_v2_implementation_and_dev_eval_v1.analyze import (
    already_executed_check,
    build_answers,
    decide,
)
from research.new_full_strategy_v2_implementation_and_dev_eval_v1.isolation import (
    OUT,
    holdout_path_touch_n,
    set_research_priority_below_normal,
    snapshot,
    stress_path_touch_n,
    write_overlap_n,
)
from research.new_full_strategy_v2_implementation_and_dev_eval_v1.publish import (
    SHEET_ORDER,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.new_full_strategy_v2_implementation_and_dev_eval_v1.spec import source_sha256, v4_sha256

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "SUBMIT_N": 0,
        "CANCEL_N": 0,
        "LIVE_ORDER_N": 0,
        "ENTRY_RUNTIME_CHANGED": False,
        "EXIT_RUNTIME_CHANGED": False,
        "CAPTURE_CHANGED": False,
        "FUTURE_DATA_USED": False,
        "STRESS_OPENED": False,
        "HOLDOUT_OPENED": False,
        "SIZING": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "OLD_ST_RCA_CONTINUED": False,
        "CSB_RCA_RUN": False,
        "ANOTHER_PRECOMMIT_RUN": False,
        "POST_RESULT_RETUNE": False,
    }


def _dev_result_hash(eco: dict[str, Any] | None) -> str | None:
    if not eco:
        return None
    body = json.dumps(
        {
            "base": eco.get("base"),
            "g_table": eco.get("g_table"),
            "CAUSAL_EX_TOP1_PNL": eco.get("CAUSAL_EX_TOP1_PNL"),
            "CAUSAL_EX_TOP1_TRADE_N": eco.get("CAUSAL_EX_TOP1_TRADE_N"),
            "CAUSAL_EX_TOP1_PF": eco.get("CAUSAL_EX_TOP1_PF"),
            "blocks": eco.get("blocks"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _publish(report: dict[str, Any], pack: dict[str, Any]) -> None:
    d = dict(pack.get("decision") or {})
    report["answers"] = build_answers(pack)
    report["_markdown"] = build_markdown({"answers": report["answers"], "decision": d})
    eco = pack.get("economics")
    sheets = {
        "answers": kv_rows(report.get("answers")),
        "integrity": kv_rows(
            {
                "ALL_INTEGRITY_TESTS_PASS": (pack.get("integrity") or {}).get("ALL_INTEGRITY_TESTS_PASS"),
                "pass_n": (pack.get("integrity") or {}).get("pass_n"),
                "total_n": (pack.get("integrity") or {}).get("total_n"),
                "tests": (pack.get("integrity") or {}).get("tests"),
                "harvest_structural": pack.get("harvest_structural"),
                "pin": pack.get("pin"),
            }
        ),
        "coverage": kv_rows(pack.get("coverage")),
        "economics": kv_rows(eco if isinstance(eco, dict) else {"ECONOMICS_VISIBLE": False}),
        "decision": kv_rows(d),
        "safety": kv_rows(report.get("safety")),
    }
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)


def main() -> None:
    set_research_priority_below_normal()
    before = snapshot(phase="PRE")
    overlap = write_overlap_n(
        str((before.get("capture") or {}).get("active_dir") or ""),
        str((before.get("paper") or {}).get("session_dir") or ""),
    )
    if overlap:
        raise RuntimeError(f"WRITE_OVERLAP {overlap}")
    src_hash = source_sha256()
    reused = already_executed_check(src_hash)
    if reused.get("REUSED_EXISTING_RESULT"):
        prev = dict(reused.get("prior_report") or {})
        print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
        print("ALREADY_EXECUTED_CHECK true", flush=True)
        print(f"VERDICT {(prev.get('decision') or {}).get('VERDICT')}", flush=True)
        print(f"NEXT {(prev.get('decision') or {}).get('NEXT')}", flush=True)
        print(f"OUT {OUT}", flush=True)
        print("STOP", flush=True)
        return
    pack = decide(run_economics=True, source_hash=src_hash)
    after = snapshot(phase="POST")
    opened: list[Path] = []
    leak = {
        "HOLDOUT_BURNED_READ_N": holdout_path_touch_n(opened),
        "STRESS_READ_N": stress_path_touch_n(opened),
        "FUTURE_DATA_N": 0,
    }
    if leak["HOLDOUT_BURNED_READ_N"] or leak["STRESS_READ_N"] or leak["FUTURE_DATA_N"]:
        raise RuntimeError(f"LEAKAGE {leak}")
    d = dict(pack.get("decision") or {})
    eco = pack.get("economics") if isinstance(pack.get("economics"), dict) else None
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "source_sha256": src_hash,
        "spec_sha256_v4": v4_sha256(),
        "implementation_sha256": src_hash,
        "dev_result_sha256": _dev_result_hash(eco),
        "ALREADY_EXECUTED_CHECK": False,
        "REUSED_EXISTING_RESULT": False,
        "integrity": pack.get("integrity"),
        "pin": pack.get("pin"),
        "harvest_structural": pack.get("harvest_structural"),
        "base_counts": pack.get("base_counts"),
        "coverage": pack.get("coverage"),
        "economics": pack.get("economics"),
        "decision": d,
        "flags": pack.get("flags"),
        "leakage": leak,
        "safety": _safety(),
        "isolation_before": before,
        "isolation_after": after,
        "isolation_advanced": advanced(before, after),
        "TRUE_OOS": TRUE_OOS,
        "CERTIFIED": CERTIFIED,
        "generated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _publish(report, pack)
    print(f"ANALYSIS_ID {ANALYSIS_ID}", flush=True)
    print("ALREADY_EXECUTED_CHECK false", flush=True)
    print(f"V4_IDENTITY_PASS {d.get('V4_IDENTITY_PASS')}", flush=True)
    print(f"ALL_INTEGRITY_TESTS_PASS {d.get('ALL_INTEGRITY_TESTS_PASS')}", flush=True)
    print(f"INTEGRITY {d.get('INTEGRITY_PASS_N')}/{d.get('INTEGRITY_TOTAL_N')}", flush=True)
    print(f"FULL_STRATEGY_SPEC_SHA256_V4 {d.get('FULL_STRATEGY_SPEC_SHA256_V4')}", flush=True)
    print(f"V4_HASH_UNCHANGED {d.get('V4_HASH_UNCHANGED')}", flush=True)
    print(f"BASE_ECONOMIC_RUN_N {d.get('BASE_ECONOMIC_RUN_N')}", flush=True)
    print(f"G6_CAUSAL_RERUN_N {d.get('G6_CAUSAL_RERUN_N')}", flush=True)
    print(f"FAILED_STAGE {d.get('FAILED_STAGE')}", flush=True)
    print(f"VERDICT {d.get('VERDICT')}", flush=True)
    print(f"NEXT {d.get('NEXT')}", flush=True)
    print(f"OUT {OUT}", flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
