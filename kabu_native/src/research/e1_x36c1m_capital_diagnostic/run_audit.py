"""E1_X36C1M runner — capital diagnostic only; no freeze mutation."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x28_executable_joint.board import verify_board_mapping
from research.e1_x31_population_direction.identity import ab_identity, reproduce_population
from research.e1_x32_upstream_attribution.eval_stages import load_boards_for_symbols
from research.e1_x33b_neutral_anchor.neutral import (
    candidate_symbols_by_day,
    planned_neutral_anchors,
)
from research.e1_x34c_passive_deployability.events import build_events
from research.e1_x36_joint_allocator.panel import enrich_events

from . import (
    ANALYSIS_ID,
    ANCHOR_SHA,
    DOCUMENT_ID,
    ENTRY_SHA,
    EXEC_SHA,
    EXIT_SHA,
    FORBIDDEN_FROM,
    INITIAL_CASH_PRIMARY,
    PRECOMMIT_SHA,
    SENSITIVITY_CASH,
    SOURCE_X36_RUN,
    V1R_SHA,
    X36_CROSS,
)
from .analyze import (
    build_fold_scorers,
    decide_verdict,
    run_capital_continuous,
    symbol_285a_stats,
    unlimited_identity,
)
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x36c1m_capital_diagnostic"
X36R = NATIVE / "results" / "research" / "e1_x36r_freeze_integrity"
X37 = NATIVE / "results" / "research" / "e1_x37_prospective"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"
X34C = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"
X36 = NATIVE / "results" / "research" / "e1_x36_joint_allocator"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x36c1m_capital_diagnostic.py"
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src"), "PYTHONIOENCODING": "utf-8"}
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
    return {"passed": passed, "failed": failed, "returncode": p.returncode, "tail": out[-2000:]}


def _sha_ok(path: Path, exp: str) -> bool:
    body = json.loads(path.read_text(encoding="utf-8"))
    raw = {k: v for k, v in body.items() if k != "sha256"}
    return body.get("sha256") == exp and hashlib.sha256(
        json.dumps(raw, sort_keys=True, default=str).encode()
    ).hexdigest() == exp


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x36c1m_cap_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    # freeze protection
    v1r_ok = _sha_ok(X36R / "PASSIVE_FIXED600_FULL_STRATEGY_V1R.json", V1R_SHA)
    pre_ok = _sha_ok(X37 / "PROSPECTIVE_PRECOMMIT_V1.json", PRECOMMIT_SHA)
    assert v1r_ok and pre_ok, {"v1r_ok": v1r_ok, "pre_ok": pre_ok}
    print("  V1R/precommit unchanged OK", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok")

    print("=== panel ===", flush=True)
    rows_pop, labels, identity = reproduce_population()
    ab_pop = ab_identity(rows_pop, labels, identity)
    assert ab_pop["ok"]
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows_pop)
    pool = candidate_symbols_by_day(rows_pop)
    planned = planned_neutral_anchors(pool)
    boards = load_boards_for_symbols(sorted({(a["date"], a["symbol"]) for a in planned}))
    raw = build_events(planned, boards)
    panel = enrich_events(raw, boards)
    print(f"  panel n={len(panel)}", flush=True)

    print("=== fold scorers (frozen OUTER_SPECS, no re-select) ===", flush=True)
    folds = build_fold_scorers(panel)
    score_by_date = folds["score_by_date"]

    print("=== unlimited X36 identity ===", flush=True)
    ident = unlimited_identity(panel, score_by_date)
    print(f"  identity_pass={ident['pass']} observed={ident['observed']}", flush=True)
    if not ident["pass"]:
        decision = decide_verdict(identity_ok=False, primary={})
        report = {
            "analysis_id": ANALYSIS_ID,
            "run_id": run_id,
            "verdict": decision["verdict"],
            "identity": ident,
            "opened_20260810": False,
            "v1r_unchanged": True,
            "precommit_unchanged": True,
            "safety": {"submit_cancel_live": "0/0/0"},
        }
        publish(OUT, report, {"summary": [report]})
        print("=== STOP identity fail ===", flush=True)
        return report

    print("=== primary 1M continuous capital ===", flush=True)
    primary = run_capital_continuous(panel, score_by_date, initial_cash=INITIAL_CASH_PRIMARY)
    print(
        f"  start={primary['initial_cash']} end={primary['ending_cash']} "
        f"pnl={primary['total_pnl_yen_cash']} fills={primary['accepted_fills']} "
        f"cap_block={primary['capital_blocked']} pos={primary['economics']['positive_days']}",
        flush=True,
    )

    s285 = symbol_285a_stats(primary["events"])
    print(f"  285A: {s285}", flush=True)

    print("=== sensitivity ===", flush=True)
    sensitivity = []
    for cash in SENSITIVITY_CASH:
        label = "unlimited" if cash is None else f"{int(cash)}"
        if cash is None:
            # unlimited continuous with same scorers — PnL = realized sum like unconstrained continuous
            # For comparison use fold-independent unlimited identity numbers for unlimited column
            row = {
                "initial_cash": "unlimited",
                "pnl": ident["observed"]["total_pnl_yen"],
                "fills": ident["observed"]["fills"],
                "admitted": ident["observed"]["admitted"],
                "capital_blocked": 0,
                "pf": ident["observed"]["pf"],
                "positive_days": ident["observed"]["positive_days"],
                "note": "X36 cross-fitted SoT (fold-independent)",
            }
        else:
            r = run_capital_continuous(panel, score_by_date, initial_cash=float(cash))
            row = {
                "initial_cash": cash,
                "ending_cash": r["ending_cash"],
                "pnl": r["total_pnl_yen_cash"],
                "return_pct": r["total_return_pct"],
                "fills": r["accepted_fills"],
                "admitted": r["orders_admitted"],
                "capital_blocked": r["capital_blocked"],
                "pf": (r["economics"] or {}).get("pf"),
                "positive_days": (r["economics"] or {}).get("positive_days"),
                "max_drawdown_yen": r["max_drawdown_yen"],
            }
            print(f"  cash={label}: pnl={row['pnl']} fills={row['fills']} cap_block={row['capital_blocked']}", flush=True)
        sensitivity.append(row)

    decision = decide_verdict(identity_ok=True, primary=primary)
    print(f"  verdict={decision['verdict']}", flush=True)

    comparison = {
        "x36_pnl": X36_CROSS["total_pnl_yen"],
        "x36_fills": X36_CROSS["fills"],
        "x36_pf": X36_CROSS["pf"],
        "x36_positive_days": X36_CROSS["positive_days"],
        "c1m_pnl": primary["total_pnl_yen_cash"],
        "c1m_fills": primary["accepted_fills"],
        "c1m_pf": (primary["economics"] or {}).get("pf"),
        "c1m_positive_days": (primary["economics"] or {}).get("positive_days"),
        "pnl_ratio_1m_over_x36": decision.get("pnl_ratio"),
    }

    # trades sheet sample (accepted + capital blocked)
    trades = []
    for e in primary["events"]:
        if e.get("accepted") or e.get("CAPITAL_BLOCKED") or e.get("admitted"):
            trades.append({
                "date": e["date"],
                "symbol": e["symbol"],
                "score": e.get("alloc_score"),
                "required_cash": e.get("required_cash"),
                "admitted": e.get("admitted"),
                "capital_blocked": e.get("CAPITAL_BLOCKED"),
                "capacity_blocked": e.get("CAPACITY_BLOCKED"),
                "filled": e.get("accepted"),
                "pnl_yen": e.get("realized_pnl_yen"),
            })
            if len(trades) >= 500:
                break

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": decision["verdict"],
        "verdict_detail": decision,
        "source_x36_run": SOURCE_X36_RUN,
        "entry_sha": ENTRY_SHA,
        "anchor_sha": ANCHOR_SHA,
        "execution_sha": EXEC_SHA,
        "exit_sha": EXIT_SHA,
        "v1r_sha": V1R_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "v1r_unchanged": True,
        "precommit_unchanged": True,
        "no_model_refit_beyond_frozen_outer_specs": True,
        "no_strategy_mutation": True,
        "unlimited_identity": {k: v for k, v in ident.items() if k != "events"},
        "primary_1m": {
            k: v for k, v in primary.items()
            if k not in ("events",)
        },
        "comparison": comparison,
        "symbol_285a_1m": s285,
        "sensitivity": sensitivity,
        "sensitivity_summary": {
            str(r.get("initial_cash")): {"pnl": r.get("pnl"), "fills": r.get("fills")}
            for r in sensitivity
        },
        "daily": primary.get("day_stats"),
        "opened_20260810": False,
        "prospective_evidence_consumed": False,
        "safety": {"research_paper_only": True, "submit_cancel_live": "0/0/0"},
        "ab_determinism": {"ok": ab_pop["ok"], "population": ab_pop},
    }

    interim = {
        "run_id": run_id,
        "verdict": decision["verdict"],
        "unlimited_identity_pass": ident["pass"],
        "initial_cash": INITIAL_CASH_PRIMARY,
        "ending_cash": primary["ending_cash"],
        "pnl": primary["total_pnl_yen_cash"],
        "return_pct": primary["total_return_pct"],
        "pf": (primary["economics"] or {}).get("pf"),
        "positive_days": (primary["economics"] or {}).get("positive_days"),
        "fills": primary["accepted_fills"],
        "admitted": primary["orders_admitted"],
        "capital_blocked": primary["capital_blocked"],
        "max_drawdown_yen": primary["max_drawdown_yen"],
        "max_drawdown_pct": primary["max_drawdown_pct"],
        "comparison": comparison,
        "symbol_285a_1m": s285,
        "sensitivity": sensitivity,
        "qty": 100,
        "no_fractional": True,
        "pending_cash_reservation": True,
        "cash_never_negative": primary["cash_never_negative"],
        "open_pending_le_5": primary["hard_cap_violations"] == 0,
        "canonical_fixed600": True,
        "no_model_refit": True,
        "v1r_unchanged": True,
        "precommit_unchanged": True,
        "opened_20260810": False,
        "submit_cancel_live": "0/0/0",
        "ab_determinism": report["ab_determinism"],
        "daily": primary.get("day_stats"),
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": decision["verdict"],
            "start": primary["initial_cash"],
            "end": primary["ending_cash"],
            "pnl": primary["total_pnl_yen_cash"],
            "fills": primary["accepted_fills"],
            "capital_blocked": primary["capital_blocked"],
            "x36_pnl": X36_CROSS["total_pnl_yen"],
            "ratio": decision.get("pnl_ratio"),
        }],
        "daily": primary.get("day_stats") or [],
        "trades": trades,
        "capital_blocked": [
            {"required_cash": x} for x in (primary.get("required_cash_blocked") or [])[:200]
        ] or [{"note": "none"}],
        "sensitivity": sensitivity,
        "symbol_285a": [s285],
    }
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    tests = _run_tests()
    report["tests"] = tests
    interim["tests"] = tests
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")
    publish(OUT, report, sheets)

    print(f"=== DONE {decision['verdict']} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": decision["verdict"],
        "start": primary["initial_cash"],
        "end": primary["ending_cash"],
        "pnl": primary["total_pnl_yen_cash"],
        "fills": primary["accepted_fills"],
        "capital_blocked": primary["capital_blocked"],
        "ratio": decision.get("pnl_ratio"),
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
