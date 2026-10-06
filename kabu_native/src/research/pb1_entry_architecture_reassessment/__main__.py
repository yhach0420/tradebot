"""PB1 ENTRY architecture reassessment. Research only. STOP after verdict."""
from __future__ import annotations

import json
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research.fixed_daytrade_universe_v1.secrets import assert_no_secret
from research.pb1_complete_strategy_causal_repair_mechanism_discovery import CASE_NONE as REPAIR_NONE
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.load import bind_dates, load_panel, load_v1_trades
from research.pb1_entry_architecture_reassessment import (
    CASE_FOUND,
    CASE_NONE,
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    PROGRAM_ID,
)
from research.pb1_entry_architecture_reassessment.decide import decide
from research.pb1_entry_architecture_reassessment.isolation import (
    CACHE,
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_entry_architecture_reassessment.precommit import contract, precommit_sha256
from research.pb1_entry_architecture_reassessment.publish import publish
from research.pb1_entry_architecture_reassessment.study import run_study
from research.pb1_v4_complete_strategy_build_and_economic_validation.analyze import development_metrics
from research.pb1_v4_complete_strategy_build_and_economic_validation.freeze import complete_strategy_sha256
from research.pb1_v4_complete_strategy_build_and_economic_validation.gate import identity_gate
from research.pb1_v4_complete_strategy_economic_confirmation1 import CASE_FAIL
from research.pb1_v4_complete_strategy_economic_failure_decomposition import BASELINE
from research.pb1_v4_complete_strategy_economic_failure_decomposition.load import baseline_lock, load_parent_report


def main() -> dict:
    set_research_priority_below_normal()
    pre = snapshot(phase="PRE")
    print("ISOLATION_PRE", json.dumps(pre, ensure_ascii=False), flush=True)
    g = identity_gate()
    cs_sha = complete_strategy_sha256(machine_sha=g["machine_sha"], source_inventory_sha=g["source_inventory_sha"])
    g = {
        **g,
        "complete_strategy_identity": "PB1_V4_COMPLETE_STRATEGY_FROZEN_V1",
        "COMPLETE_STRATEGY_SHA256": cs_sha,
        "expected_complete_strategy_sha256": EXPECTED_COMPLETE_STRATEGY_SHA256,
        "complete_strategy_sha_ok": cs_sha == EXPECTED_COMPLETE_STRATEGY_SHA256,
        "repair_verdict": REPAIR_NONE,
    }
    g["ok"] = bool(g.get("ok") and g["complete_strategy_sha_ok"])
    parent = load_parent_report()
    lock = baseline_lock(parent)
    v1_fail = str((parent.get("answers") or {}).get("VERDICT") or "") == CASE_FAIL
    if not (g.get("ok") and parent.get("ok") and lock.get("ok") and v1_fail):
        raise SystemExit(json.dumps({"ok": False, "reason": "identity_or_v1_fail_lock", "gate": g, "v1_fail": v1_fail}, ensure_ascii=False))
    c = contract()
    sha = precommit_sha256()
    CACHE.mkdir(parents=True, exist_ok=True)
    precommit = {"sha256": sha, "contract": c, "locked_before_feature_pnl": True}
    (CACHE / "precommit.json").write_text(json.dumps(precommit, ensure_ascii=False, indent=2), encoding="utf-8")
    assert_no_secret(precommit, where="precommit")
    print("PRECOMMIT", sha, flush=True)
    dates = bind_dates()
    if not dates.get("ok"):
        raise SystemExit("bind_dates_failed")
    trades = load_v1_trades(conf_dates=list(dates.get("confirmation_dates") or []))
    need = sorted({t["symbol"] for t in trades if t.get("symbol")})
    print(f"LOAD trades={len(trades)} symbols={len(need)} dates={len(dates.get('allowed') or [])}", flush=True)
    recs = load_panel(symbols=need, allowed=list(dates.get("allowed") or []), forbidden=list(dates.get("forbidden") or []))
    print(f"RECS {len(recs)}", flush=True)
    study = run_study(
        trades=trades,
        recs=recs,
        discovery_dates=list(dates.get("discovery_dates") or []),
        confirmation_dates=list(dates.get("confirmation_dates") or []),
    )
    decision = decide(
        summaries=list(study.get("summaries") or []),
        architectures=list(study.get("architectures") or []),
        market_context=dict(study.get("market_context") or {}),
    )
    v1_m = development_metrics(trades)
    baseline = {
        **dict(BASELINE),
        "parent_verdict": CASE_FAIL,
        "repair_verdict": REPAIR_NONE,
        "dev_plus_c1_trade_n": len(trades),
        "v1_pooled_metrics": v1_m,
        "concentration": {},
    }
    safety = {
        "V1_VERDICT_CHANGED": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "submit/cancel/live": "0/0/0",
        "orders_submit": 0,
        "orders_cancel": 0,
        "orders_live": 0,
        "research_only": True,
        "write_overlap_n": write_overlap_n(str(pre.get("ACTIVE_CAPTURE") or ""), str(pre.get("PAPER_SESSION") or pre.get("ACTIVE_PAPER_SESSION") or "")),
        "ARCH_PRECOMMIT_SHA256": sha,
    }
    if int(safety["write_overlap_n"]) != 0:
        raise SystemExit("isolation_write_overlap")
    # drop raw rows from json via publish reading study; pop after excel needs rows — publish uses study['rows']
    pub = publish(gate=g, precommit=precommit, baseline=baseline, study=study, decision=decision, safety=safety)
    assert_no_secret(safety, where="safety")
    post = snapshot(phase="POST")
    print("ISOLATION_POST", json.dumps(post, ensure_ascii=False), flush=True)
    print("OUT", OUT, flush=True)
    print("VERDICT", pub.get("verdict"), flush=True)
    print("NEXT", pub.get("next"), flush=True)
    assert pub.get("verdict") in {CASE_FOUND, CASE_NONE}
    assert safety["V5_CREATED"] is False
    return {"ok": True, **pub, "precommit_sha256": sha}


if __name__ == "__main__":
    out = main()
    print(json.dumps({"ok": out.get("ok"), "verdict": out.get("verdict"), "next": out.get("next"), "program": PROGRAM_ID, "precommit_sha256": out.get("precommit_sha256")}, ensure_ascii=False), flush=True)
