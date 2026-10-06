"""Offline SIMPLE_TECH V21 sizing attribution RCA. Frozen V20 trades. No sizing search. No Capture restream."""
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

os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.am_entry_profit_improvement import (
    CANCEL_N,
    ELIGIBLE_DAYS,
    LIVE_ORDER_N,
    PAPER_OPERATED,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
)
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.isolation import V13_OUT
from research.simple_tech_entry_family.publish import kv_rows
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_exit_family.isolation import V18_OUT, V19_OUT
from research.simple_tech_exit_family.v19_analyze import identity_hashes, load_v18_official_trades
from research.simple_tech_strategy.isolation import (
    TODAY,
    V20_OUT,
    V21_OUT,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_strategy.v20_analyze import concentration_yen, pnl_pack, v19_identity_parity
from research.simple_tech_strategy.v20_harvest import V19_CACHE, load_v19_day_trades
from research.simple_tech_strategy.v21_analyze import (
    _day_decomp,
    _sym_decomp,
    bps_robustness,
    corr_pack,
    day_attribution,
    decision_case,
    fixed100_lock,
    norm_improves,
    normalized_pack,
    notional_distribution,
    notional_explains,
    notional_quartiles,
    symbol_attribution,
)
from research.simple_tech_strategy.v21_harvest import enrich_trades
from research.simple_tech_strategy.v21_publish import REQUIRED_KEYS, build_markdown, flatten_v21_trade, write_artifacts
from research.simple_tech_strategy.v21_spec import (
    ACTUAL_EXIT_SET_HASH_EXPECTED,
    ADOPT_1M_POLICY,
    ANALYSIS_ID,
    CAPITAL_OPTIMIZATION,
    C14_USED,
    DEVELOPMENT_ENTRY_STACK,
    DEVELOPMENT_STRATEGY_STACK,
    DYNAMIC_SIZING,
    E4_CHANGED,
    E4_FILL_SET_HASH_EXPECTED,
    E4_FILLED_N_EXPECTED,
    EXIT_INPUT_FILL_SET_HASH_EXPECTED,
    EMA_CHANGED,
    ENTRY_CHANGED,
    EXIT_ADDED,
    EXIT_CHANGED,
    GRID_SEARCH,
    HOLD_CHANGED,
    HOLD_SEC,
    HYPOTHETICAL_NOTIONAL_IS_POLICY,
    HYPOTHETICAL_NOTIONAL_YEN,
    KELLY,
    ML_USED,
    PARENT_SPEC_SHA256_EXPECTED,
    PRICE_BUCKET_SIZING,
    PULLBACK_CHANGED,
    QUARTILE_GATE,
    RCI_CHANGED,
    RESEARCH_PARALLELISM,
    RISK_SIZING,
    RUNTIME_ADOPTION_ALLOWED,
    RUNTIME_CANDIDATE,
    SCHEDULED_EXIT_SET_HASH_EXPECTED,
    SIZING_SEARCH,
    STRATEGY_CERTIFIED,
    SYMBOL_SIZING,
    TRUE_OOS,
    V13_SPEC_SHA256_EXPECTED,
    V18_SPEC_SHA256_EXPECTED,
    V19_SPEC_SHA256_EXPECTED,
    V19_VERDICT_EXPECTED,
    V20_EX_BEST_DAY_EXPECTED,
    V20_EX_TOP3_DAY_EXPECTED,
    V20_PF_EXPECTED,
    V20_SPEC_SHA256_EXPECTED,
    V20_TOTAL_PNL_YEN_100_EXPECTED,
    V20_VERDICT_EXPECTED,
    V20_VERDICT_MUTATION,
    VOL_SIZING,
    WAIT_CHANGED,
    canonical_v21_spec,
    spec_sha256_v21,
)

INTEGRITY_ZERO = (
    "LIVE_PROCESS_CONTROL_CALL_N",
    "RUNTIME_WRITE_N",
    "CAPTURE_WRITE_N",
    "ADDITIONAL_WEBSOCKET_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "KABUS_RESTART_N",
    "CAPTURE_RESTART_N",
    "RUNTIME_RESTART_N",
    "C14_REPLAY_N",
    "ENTRY_CHANGE_N",
    "EXIT_CHANGE_N",
    "SIZING_SEARCH_N",
    "ADOPT_1M_POLICY_N",
    "QUARTILE_GATE_N",
    "V20_WRITE_N",
    "V19_WRITE_N",
    "V13_WRITE_N",
    "KELLY_N",
    "VOL_SIZING_N",
    "RISK_SIZING_N",
    "SYMBOL_SIZING_N",
    "DYNAMIC_SIZING_N",
    "CAPITAL_OPTIMIZATION_N",
    "ML_USE_N",
    "GRID_SEARCH_N",
)


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], parent_sha: str, v21_sha: str) -> int:
    req = {k: None for k in REQUIRED_KEYS}
    req.update(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": "SIMPLE_TECH_V21_INVALID",
            "NEXT": msg,
            "TRUE_OOS": False,
            "FORWARD_OOS_ELIGIBLE": False,
            "PRIMARY_FRAGILITY_SOURCE": None,
            "PARENT_SPEC_SHA256": parent_sha,
            "V21_SPEC_SHA256": v21_sha,
        }
    )
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": req,
        "preflight": pre,
        "leak": leak,
        "_markdown": build_markdown({"required": req}),
    }
    write_artifacts(report, {"Precommit": kv_rows({"blocker": msg}), "Integrity": kv_rows(leak), "Non_Interference": kv_rows(pre)})
    print(msg, flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    parent_sha = v1_spec_sha256()
    spec = canonical_v21_spec()
    v21_sha = spec_sha256_v21(spec)
    chk = self_check()
    pre = snapshot(phase="PRE")
    leak: dict[str, Any] = {k: 0 for k in INTEGRITY_ZERO}
    leak["SUBMIT_N"] = int(SUBMIT_N)
    leak["CANCEL_N"] = int(CANCEL_N)
    leak["LIVE_ORDER_N"] = int(LIVE_ORDER_N)
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} v21={v21_sha[:12]}", flush=True)
    if parent_sha != PARENT_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V1 parent spec SHA drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if not chk.get("ok"):
        return _stop("STOP. Indicator self-check failed.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if int(RESEARCH_PARALLELISM) != 1 or SESSION != "AM":
        return _stop("STOP. Parallelism/session drifted.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if (
        RUNTIME_ADOPTION_ALLOWED
        or RUNTIME_CANDIDATE
        or STRATEGY_CERTIFIED
        or ENTRY_CHANGED
        or EXIT_CHANGED
        or EMA_CHANGED
        or RCI_CHANGED
        or PULLBACK_CHANGED
        or E4_CHANGED
        or WAIT_CHANGED
        or HOLD_CHANGED
        or EXIT_ADDED
        or SIZING_SEARCH
        or VOL_SIZING
        or RISK_SIZING
        or KELLY
        or PRICE_BUCKET_SIZING
        or SYMBOL_SIZING
        or DYNAMIC_SIZING
        or CAPITAL_OPTIMIZATION
        or ADOPT_1M_POLICY
        or HYPOTHETICAL_NOTIONAL_IS_POLICY
        or QUARTILE_GATE
        or V20_VERDICT_MUTATION
        or C14_USED
        or ML_USED
        or GRID_SEARCH
        or float(HOLD_SEC) != 180.0
        or float(HYPOTHETICAL_NOTIONAL_YEN) != 1000000.0
    ):
        return _stop("STOP. Forbidden flags set.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if TODAY in set(ELIGIBLE_DAYS):
        leak["ACTIVE_CAPTURE_INPUT_N"] = 1
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)

    v13_req = dict((_load(V13_OUT / "report.json").get("required") or {}))
    if str(v13_req.get("V13_SPEC_SHA256") or "") != V13_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V13 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if str(v13_req.get("DEVELOPMENT_ENTRY_STACK") or "") != DEVELOPMENT_ENTRY_STACK:
        return _stop("STOP. V13 stack mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    v18_req = dict((_load(V18_OUT / "report.json").get("required") or {}))
    if str(v18_req.get("V18_SPEC_SHA256") or "") != V18_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V18 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    v19_req = dict((_load(V19_OUT / "report.json").get("required") or {}))
    if str(v19_req.get("V19_SPEC_SHA256") or "") != V19_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V19 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if str(v19_req.get("VERDICT") or "") != V19_VERDICT_EXPECTED:
        return _stop("STOP. V19 official verdict mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    v20_req = dict((_load(V20_OUT / "report.json").get("required") or {}))
    if str(v20_req.get("V20_SPEC_SHA256") or "") != V20_SPEC_SHA256_EXPECTED:
        return _stop("STOP. V20 spec SHA mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if str(v20_req.get("VERDICT") or "") != V20_VERDICT_EXPECTED:
        return _stop("STOP. V20 official verdict mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if bool(v20_req.get("FORWARD_OOS_ELIGIBLE")):
        return _stop("STOP. V20 FORWARD_OOS_ELIGIBLE mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if abs(float(v20_req.get("TOTAL_PNL_YEN_100") or 0) - float(V20_TOTAL_PNL_YEN_100_EXPECTED)) > 1e-2:
        return _stop("STOP. V20 TOTAL_PNL mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if abs(float(v20_req.get("PF") or 0) - float(V20_PF_EXPECTED)) > 1e-4:
        return _stop("STOP. V20 PF mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if abs(float(v20_req.get("EX_BEST_DAY_TOTAL_PNL") or 0) - float(V20_EX_BEST_DAY_EXPECTED)) > 1e-2:
        return _stop("STOP. V20 EX_BEST mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
    if abs(float(v20_req.get("EX_TOP3_DAY_TOTAL_PNL") or 0) - float(V20_EX_TOP3_DAY_EXPECTED)) > 1e-2:
        return _stop("STOP. V20 EX_TOP3 mutated.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)

    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        [
            str(V19_OUT / "report.json"),
            str(V19_OUT / "audit.xlsx"),
            str(V20_OUT / "report.json"),
            str(V20_OUT / "audit.xlsx"),
            str(V19_CACHE),
        ],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"])
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active Capture input referenced.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)

    v19_official = load_v18_official_trades(V19_OUT / "audit.xlsx")
    v20_official = load_v18_official_trades(V20_OUT / "audit.xlsx")
    if len(v20_official) != int(E4_FILLED_N_EXPECTED) or len(v19_official) != int(E4_FILLED_N_EXPECTED):
        return _stop("STOP. Official trade n mismatch.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)

    cache_rows: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        body = load_v19_day_trades(str(day))
        if not body:
            return _stop(f"STOP. V19 independent cache missing {day}.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)
        cache_rows.extend(list(body.get("rows") or []))
    if len(cache_rows) != int(E4_FILLED_N_EXPECTED):
        return _stop(f"STOP. V19 cache trade n={len(cache_rows)}.", pre=pre, leak=leak, parent_sha=parent_sha, v21_sha=v21_sha)

    rows = enrich_trades(cache_rows)
    ident = v19_identity_parity(rows, v19_official)
    hashes = identity_hashes(rows)
    hash_ok = bool(
        str(hashes.get("ACTUAL_EXIT_SET_HASH") or "") == ACTUAL_EXIT_SET_HASH_EXPECTED
        and str(hashes.get("EXIT_INPUT_FILL_SET_HASH") or "") == EXIT_INPUT_FILL_SET_HASH_EXPECTED
        and str(hashes.get("SCHEDULED_EXIT_SET_HASH") or "") == SCHEDULED_EXIT_SET_HASH_EXPECTED
        and str(v19_req.get("ACTUAL_EXIT_SET_HASH") or "") == ACTUAL_EXIT_SET_HASH_EXPECTED
        and str(v19_req.get("EXIT_INPUT_FILL_SET_HASH") or "") == EXIT_INPUT_FILL_SET_HASH_EXPECTED
        and str(v19_req.get("SCHEDULED_EXIT_SET_HASH") or "") == SCHEDULED_EXIT_SET_HASH_EXPECTED
        and str(v20_req.get("ACTUAL_EXIT_SET_HASH") or "") == ACTUAL_EXIT_SET_HASH_EXPECTED
        and str(v20_req.get("E4_FILL_SET_HASH") or "") == E4_FILL_SET_HASH_EXPECTED
    )
    stack_parity = bool(
        str(v19_req.get("DEVELOPMENT_STRATEGY_STACK") or "") == DEVELOPMENT_STRATEGY_STACK
        and str(v20_req.get("DEVELOPMENT_STRATEGY_STACK") or "") == DEVELOPMENT_STRATEGY_STACK
        and hash_ok
        and len(rows) == int(E4_FILLED_N_EXPECTED)
    )
    identity_ok = bool(stack_parity)

    dist = notional_distribution(rows)
    corrs = corr_pack(rows)
    quarts = notional_quartiles(rows)
    bps = bps_robustness(rows)
    days = day_attribution(rows, list(ELIGIBLE_DAYS))
    pnl = pnl_pack(rows, list(ELIGIBLE_DAYS))
    conc = concentration_yen(rows, list(ELIGIBLE_DAYS))
    lock = fixed100_lock(pnl, conc)
    med_n = dist.get("median")
    mean_bps = bps.get("MEAN_BPS")
    best_date = str(conc.get("BEST_DAY") or "")
    worst_yen = min(days, key=lambda r: float(r.get("net_pnl_yen_100") or 0.0)) if days else {}
    best_day_row = next((r for r in days if str(r.get("date")) == best_date), {})
    best_attr = _day_decomp(best_day_row, med_n, mean_bps)
    worst_attr = _day_decomp(worst_yen, med_n, mean_bps)
    syms = symbol_attribution(rows)
    top_sym = _sym_decomp(syms[0], med_n, mean_bps) if syms else {}
    norm = normalized_pack(rows, list(ELIGIBLE_DAYS))
    explain = notional_explains(dist, corrs)
    improve = norm_improves(norm)

    post = snapshot(phase="POST")
    reporting = reporting_semantics(pre, post)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO) and int(leak.get("RESEARCH_WRITE_PATH_OVERLAP_N") or 0) == 0
    ni_ok = bool(leak_ok and reporting.get("RUNTIME_PID_UNCHANGED") and reporting.get("CAPTURE_PID_UNCHANGED"))
    integ_ok = bool(ni_ok and reporting.get("REPORTING_SEMANTICS_PASS") and identity_ok and lock.get("V20_FIXED100_LOCK"))
    dec = decision_case(integrity_ok=integ_ok, bps=bps, explain=explain, improve=improve)

    fixed100 = {
        "TOTAL": pnl.get("TOTAL_PNL_YEN_100"),
        "PF": pnl.get("PROFIT_FACTOR"),
        "EX_BEST": conc.get("EX_BEST_DAY_TOTAL_PNL"),
        "EX_TOP3": conc.get("EX_TOP3_DAY_TOTAL_PNL"),
        "DD": pnl.get("REALIZED_CLOSE_EQUITY_MAX_DD_YEN"),
        "POSITIVE_DAY_N": conc.get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N": conc.get("NEGATIVE_DAY_N"),
        "DROP_TOP_SYMBOL": conc.get("DROP_TOP_SYMBOL_TOTAL_PNL"),
        "DROP_TOP3_SYMBOL": conc.get("DROP_TOP3_SYMBOL_TOTAL_PNL"),
        "IS_OFFICIAL_STRATEGY_RESULT": True,
    }
    norm_out = {
        "TOTAL": norm.get("TOTAL"),
        "PF": norm.get("PF"),
        "EX_BEST": norm.get("EX_BEST_DAY_TOTAL_PNL"),
        "EX_TOP3": norm.get("EX_TOP3_DAY_TOTAL_PNL"),
        "DD": norm.get("MAX_DD"),
        "DROP_TOP_SYMBOL": norm.get("DROP_TOP_SYMBOL_TOTAL_PNL"),
        "DROP_TOP3_SYMBOL": norm.get("DROP_TOP3_SYMBOL_TOTAL_PNL"),
        "POSITIVE_DAY_N": norm.get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N": norm.get("NEGATIVE_DAY_N"),
        "IS_STRATEGY_RESULT": False,
        "UNIT_NOTIONAL_YEN": float(HYPOTHETICAL_NOTIONAL_YEN),
    }
    req = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PARENT_SPEC_SHA256": parent_sha,
        "V19_SPEC_SHA256": V19_SPEC_SHA256_EXPECTED,
        "V20_SPEC_SHA256": V20_SPEC_SHA256_EXPECTED,
        "V21_SPEC_SHA256": v21_sha,
        "STRATEGY_STACK_PARITY": bool(stack_parity),
        "TRADE_N": len(rows),
        "NOTIONAL_DISTRIBUTION": dist,
        "CORR_NOTIONAL_PNL_YEN": corrs.get("CORR_NOTIONAL_PNL_YEN"),
        "CORR_NOTIONAL_ABS_PNL": corrs.get("CORR_NOTIONAL_ABS_PNL"),
        "CORR_NOTIONAL_BPS": corrs.get("CORR_NOTIONAL_BPS"),
        "NOTIONAL_QUARTILES": quarts,
        "BEST_DAY_ATTRIBUTION": best_attr,
        "WORST_DAY_ATTRIBUTION": worst_attr,
        "TOP_SYMBOL_ATTRIBUTION": top_sym,
        "BPS_ROBUSTNESS": bps,
        "FIXED100": fixed100,
        "NORMALIZED_1M_DIAGNOSTIC": norm_out,
        "PRIMARY_FRAGILITY_SOURCE": dec.get("PRIMARY_FRAGILITY_SOURCE"),
        "CASE": dec.get("CASE"),
        "TRUE_OOS": bool(TRUE_OOS),
        "FORWARD_OOS_ELIGIBLE": False,
        "STRATEGY_CERTIFIED": False,
        "RUNTIME_CANDIDATE": False,
        "V20_OFFICIAL_UNCHANGED": True,
        "V20_VERDICT": V20_VERDICT_EXPECTED,
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "VERDICT": dec.get("VERDICT"),
        "NEXT": dec.get("NEXT"),
        "ACTUAL_EXIT_SET_HASH": hashes.get("ACTUAL_EXIT_SET_HASH"),
        "EXIT_INPUT_FILL_SET_HASH": hashes.get("EXIT_INPUT_FILL_SET_HASH"),
        "SCHEDULED_EXIT_SET_HASH": hashes.get("SCHEDULED_EXIT_SET_HASH"),
        "E4_FILL_SET_HASH": E4_FILL_SET_HASH_EXPECTED,
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "required": req,
        "spec": spec,
        "self_check": chk,
        "identity": ident,
        "notional": dist,
        "correlations": corrs,
        "quartiles": quarts,
        "bps": bps,
        "explain": explain,
        "improve": improve,
        "lock": lock,
        "decision": dec,
        "reporting": reporting,
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH", "LIVE_PIDS")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH", "LIVE_PIDS")},
        "leak": leak,
        "runtime_changed": bool(RUNTIME_CHANGED),
        "paper_operated": bool(PAPER_OPERATED),
        "true_oos": False,
        "_markdown": "",
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Precommit": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "V21_SPEC_SHA256": v21_sha,
                "V20_SPEC_SHA256": V20_SPEC_SHA256_EXPECTED,
                "HYPOTHETICAL_NOTIONAL_YEN": HYPOTHETICAL_NOTIONAL_YEN,
                "HYPOTHETICAL_NOTIONAL_IS_POLICY": False,
                "SIZING_SEARCH": False,
                "TRUE_OOS": False,
            }
        ),
        "Identity": kv_rows(
            {
                "STRATEGY_STACK_PARITY": stack_parity,
                "TRADE_N": len(rows),
                "TRADE_BY_TRADE_V19_PARITY": ident.get("TRADE_BY_TRADE_V19_PARITY"),
                "ACTUAL_EXIT_SET_HASH": hashes.get("ACTUAL_EXIT_SET_HASH"),
                "EXIT_INPUT_FILL_SET_HASH": hashes.get("EXIT_INPUT_FILL_SET_HASH"),
                "SCHEDULED_EXIT_SET_HASH": hashes.get("SCHEDULED_EXIT_SET_HASH"),
                "V20_FIXED100_LOCK": lock.get("V20_FIXED100_LOCK"),
            }
        ),
        "Notional": kv_rows(dist),
        "Corr": kv_rows(
            {
                "YEN_PEARSON": (corrs.get("CORR_NOTIONAL_PNL_YEN") or {}).get("pearson"),
                "YEN_SPEARMAN": (corrs.get("CORR_NOTIONAL_PNL_YEN") or {}).get("spearman"),
                "ABS_PEARSON": (corrs.get("CORR_NOTIONAL_ABS_PNL") or {}).get("pearson"),
                "ABS_SPEARMAN": (corrs.get("CORR_NOTIONAL_ABS_PNL") or {}).get("spearman"),
                "BPS_PEARSON": (corrs.get("CORR_NOTIONAL_BPS") or {}).get("pearson"),
                "BPS_SPEARMAN": (corrs.get("CORR_NOTIONAL_BPS") or {}).get("spearman"),
            }
        ),
        "Quartiles": quarts or [{"empty": True}],
        "Days": days or [{"empty": True}],
        "BestWorstDays": [best_attr, worst_attr],
        "Symbols": [_sym_decomp(s, med_n, mean_bps) for s in syms] or [{"empty": True}],
        "TopSymbols": [_sym_decomp(s, med_n, mean_bps) for s in syms[:5]] or [{"empty": True}],
        "BpsRobustness": kv_rows(bps),
        "Fixed100": kv_rows(fixed100),
        "Normalized1M": kv_rows({k: v for k, v in norm_out.items()}),
        "Attribution": kv_rows({**explain, **improve, **{k: dec.get(k) for k in ("CASE", "VERDICT", "PRIMARY_FRAGILITY_SOURCE")}}),
        "Trades": [flatten_v21_trade(r) for r in rows],
        "Reporting": kv_rows(reporting),
        "Integrity": kv_rows(leak),
        "Non_Interference": kv_rows({**reporting, "NON_INTERFERENCE_PASS": ni_ok}),
    }
    write_artifacts(report, sheets)
    v20_after = _load(V20_OUT / "report.json")
    if str((v20_after.get("required") or {}).get("VERDICT") or "") != V20_VERDICT_EXPECTED:
        print("STOP. V20 official verdict mutated.", flush=True)
        return 2
    if str((v20_after.get("required") or {}).get("V20_SPEC_SHA256") or "") != V20_SPEC_SHA256_EXPECTED:
        print("STOP. V20 official SHA mutated.", flush=True)
        return 2
    print(
        f"DONE verdict={req.get('VERDICT')} case={req.get('CASE')} source={req.get('PRIMARY_FRAGILITY_SOURCE')} "
        f"ni={ni_ok} out={V21_OUT}",
        flush=True,
    )
    return 0 if ni_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
