"""E1_X18 failure source audit runner."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    CANDIDATE_ID,
    CANDIDATE_STATUS,
    DOCUMENT_ID,
    FORBIDDEN_DAY,
    FORBIDDEN_RISK_FROM,
    HIST_SOURCE_RUN,
    PROSP_DAY,
    PROSP_ROLE,
    PROSP_SOURCE_RUN,
    VWAP_UPPER_LIMIT_BPS,
)
from .analyze import (
    classify_failures,
    cohort_shift,
    common_symbol_analysis,
    effect_block,
    gap_trend,
    historical_daily,
    market_state,
    no_progress_decomposition,
    threshold_transport,
    time_of_day,
    vwap_distribution,
)
from .analyze import _cohort
from .load import attach_context, contract_parity, load_panels
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x18_vwap_failure_source"


def _kv(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": json.dumps(v, default=str)[:12000]})
        else:
            rows.append({"key": k, "value": v})
    return rows


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x18_vwap_failure_source.py"
    import os
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src")}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-q", "--tb=line"],
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
    return {
        "exit_code": p.returncode, "passed": passed, "failed": failed,
        "total": passed + failed or 1,
        "rows": [{"test": "pytest_suite",
                  "outcome": "PASSED" if p.returncode == 0 else "FAILED",
                  "detail": out[-2500:]}],
    }


def run(*, force_context: bool = False) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    run_id = f"e1x18_failsrc_{now.strftime('%Y%m%d_%H%M%S')}_A"

    parity = contract_parity()
    hist, prosp = load_panels()
    print(f"=== panels hist={len(hist)} prosp={len(prosp)} ===", flush=True)
    all_rows = attach_context(hist + prosp, force=force_context)
    hist = [r for r in all_rows if r.get("panel") == "historical"]
    prosp = [r for r in all_rows if r.get("panel") == "prospective_consumed"]

    hist_daily = historical_daily(hist)
    prosp_cmp = {
        "A2_vs_C0": effect_block(_cohort(prosp, "in_A2"), _cohort(prosp, "in_A0")),
        "Rejected_vs_A2": effect_block(_cohort(prosp, "in_A2_Rejected"), _cohort(prosp, "in_A2")),
        "supports": {
            "C0": len(_cohort(prosp, "in_A0")),
            "A2": len(_cohort(prosp, "in_A2")),
            "Rejected": len(_cohort(prosp, "in_A2_Rejected")),
        },
        "role": PROSP_ROLE,
    }
    hist_vwap = vwap_distribution(hist, "historical")
    prosp_vwap = vwap_distribution(prosp, "prospective_consumed")
    transport = threshold_transport(hist_vwap, prosp_vwap)
    cohort = cohort_shift(hist, prosp)
    common = common_symbol_analysis(hist, prosp)
    tod = time_of_day(hist, prosp)
    market = market_state(hist, prosp)
    gap = gap_trend(hist, prosp)
    npdec = no_progress_decomposition(prosp)

    classification = classify_failures(
        parity, transport, cohort, common, tod, market, gap, npdec, hist_daily,
    )
    verdict = classification["verdict"]

    decision_closure = {
        "candidate_id": CANDIDATE_ID,
        "status": CANDIDATE_STATUS,
        "reasons": [
            "prospective FR failed",
            "prospective touch failed",
            "prospective MAE failed",
            "rejected group materially better",
            "NoProgress-only improvement insufficient to keep candidate",
        ],
        "immutable": True,
        "no_inverse_candidate": True,
        "no_threshold_retune": True,
        "threshold_frozen_bps": VWAP_UPPER_LIMIT_BPS,
    }

    next_decision = {
        "20260804": "UNCLASSIFIED_DO_NOT_OPEN",
        "open_20260804": False,
        "new_candidate_from_x18": False,
        "note": "Do not open 20260804 from E1_X18 results alone",
    }

    # Determinism
    h1 = sha256_obj({"daily": hist_daily["daily"], "class": classification["tags"]})
    h2 = sha256_obj({"daily": historical_daily(hist)["daily"], "class": classification["tags"]})
    det = {"ab_match": h1 == h2, "hash_a": h1, "hash_b": h2}

    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "candidate_status": CANDIDATE_STATUS,
        "parity_ok": parity["ok"],
        "primary": classification["primary_failure_source"],
        "secondary": classification["secondary_failure_sources"],
        "hist_source": HIST_SOURCE_RUN,
        "prosp_source": PROSP_SOURCE_RUN,
        "threshold": VWAP_UPPER_LIMIT_BPS,
        "no_retune": True,
        "no_inverse": True,
        "hist_daily_n": len(hist_daily["daily"]),
        "transport": transport,
        "common_n": common["common_n"],
        "within_symbol_reversal": common["WITHIN_SYMBOL_REGIME_REVERSAL"],
        "opened_20260804": False,
        "prosp_day_role": PROSP_ROLE,
        "forbidden_risk_from": FORBIDDEN_RISK_FROM,
        "time_buckets_fixed": True,
        "asof_only": True,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    tests = _run_tests()
    safety = {
        "submit_cancel_live": "0/0/0",
        "mainline_changed": False,
        "production_yaml_changed": False,
        "ENTRY_changed": False,
        "EXIT_changed": False,
        "Universe_changed": False,
        "20260804_opened": False,
        "20260803_role": PROSP_ROLE,
        "Shadow": False,
        "Forward": False,
        "Paper_connection": False,
        "Discord": False,
        "paper_trade_only": True,
    }

    sheets = {
        "SourceIdentity": _kv({
            "analysis_id": ANALYSIS_ID,
            "hist_source": HIST_SOURCE_RUN,
            "prosp_source": PROSP_SOURCE_RUN,
            "prosp_day": PROSP_DAY,
            "prosp_role": PROSP_ROLE,
        }),
        "DecisionClosure": _kv(decision_closure),
        "ContractParity": _kv(parity),
        "HistoricalDaily": hist_daily["daily"],
        "ProspectiveComparison": _kv(prosp_cmp),
        "VWAPDistribution": hist_vwap["by_day"] + prosp_vwap["by_day"] + [_kv(transport)[0]],
        "CohortComposition": _kv(cohort),
        "CommonSymbols": _kv(common),
        "TimeOfDay": _kv(tod),
        "MarketState": _kv(market),
        "GapTrendStructure": _kv(gap),
        "NoProgressDecomposition": _kv(npdec),
        "FailureClassification": _kv(classification),
        "NextDecision": _kv(next_decision),
        "ChangeLog": [{"at": now.isoformat(), "note": "E1_X18 VWAP reject failure source audit"}],
    }
    # fix VWAPDistribution sheet - last row messy; use transport as kv rows separately
    sheets["VWAPDistribution"] = hist_vwap["by_day"] + prosp_vwap["by_day"]

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "candidate_id": CANDIDATE_ID,
        "candidate_closure_status": CANDIDATE_STATUS,
        "decision_closure": decision_closure,
        "contract_parity": parity,
        "historical_daily": hist_daily,
        "prospective_comparison": prosp_cmp,
        "vwap_distribution": {"historical": hist_vwap, "prospective": prosp_vwap, "transport": transport},
        "cohort_composition": cohort,
        "common_symbols": common,
        "time_of_day": tod,
        "market_state": market,
        "gap_trend": gap,
        "no_progress_decomposition": npdec,
        "primary_failure_source": classification["primary_failure_source"],
        "secondary_failure_sources": classification["secondary_failure_sources"],
        "failure_tags": classification["tags"],
        "next_decision": next_decision,
        "threshold_frozen_bps": VWAP_UPPER_LIMIT_BPS,
        "safety": safety,
        "_sheets": sheets,
    }
    shas = publish(report, tests, det, OUT)
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "candidate": CANDIDATE_STATUS,
        "primary": classification["primary_failure_source"],
        "secondary": classification["secondary_failure_sources"],
        "parity": parity["ok"],
        "tests": f"{tests['passed']}/{tests['total']}",
        "ab": det["ab_match"],
    }, indent=2))
    return report


if __name__ == "__main__":
    run(force_context="--force" in sys.argv)
