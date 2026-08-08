"""E1_X10 orchestrator — risk diagnostic only; no ENTRY/EXIT/runtime change."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_fcrr.replay import _universe_from_manifest, load_source_manifest
from research.e1_x6_provisional.util import sha256_file, sha256_obj
from research.e1_x7_pfq.config import DAYS
from research.e1_x10_risk_universe.config_discovery import discover_risk_config
from research.e1_x10_risk_universe.metrics import aggregate_symbol_day, summarize_symbol
from research.e1_x10_risk_universe.quotes import (
    iter_symbol_day_rows,
    qty_unit_contract,
    reference_price_from_rows,
)

from . import (
    ANALYSIS_ID,
    DOCUMENT_ID,
    LOT,
    NOTIONAL_BANDS,
    SOURCE_CLOSURE,
    TARGET_SYMBOL,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
CLOSURE_DIR = NATIVE / "results" / "research" / "e1_x7_x9_closure"
PUBLISH = NATIVE / "results" / "research" / "e1_x10_risk_universe"
PKG = Path(__file__).resolve().parent


def _safety() -> dict[str, Any]:
    return {
        "submit_cancel_live": "0/0/0",
        "mainline_changed": False,
        "production_yaml_changed": False,
        "entry_changed": False,
        "exit_changed": False,
        "universe_runtime_changed": False,
        "unused_data_used": False,
        "prospective": False,
        "shadow": False,
        "forward": False,
        "paper": False,
        "discord": False,
        "pfq_revived": False,
        "new_candidate": False,
        "diagnostic_only": True,
    }


def _pnl_independence_audit() -> dict[str, Any]:
    """Static scan: package must not import/use trade PnL or ENTRY alpha labels."""
    # tokens split so this file itself is not a false positive for tests
    forbidden_imports = (
        "first" + "_touch",
        "profit" + "_factor",
        "net_" + "pnl",
        "pnl_" + "yen",
        "passes_" + "candidate",
        "PFQ_" + "UPDATE",
        "evaluate_" + "update_signal",
        "load_" + "episodes",
    )
    hits = []
    for fp in sorted(PKG.glob("*.py")):
        if fp.name == "__init__.py":
            continue  # holds forbidden-token definitions only
        if fp.name == "run_audit.py":
            # skip the audit function body; scan other functions by excluding this helper
            text = fp.read_text(encoding="utf-8")
            start = text.find("def _pnl_independence_audit")
            end = text.find("\ndef _band_for")
            if start >= 0 and end > start:
                text = text[:start] + text[end:]
        else:
            text = fp.read_text(encoding="utf-8")
        for tok in forbidden_imports:
            if tok in text:
                for i, line in enumerate(text.splitlines(), 1):
                    if tok in line and not line.strip().startswith("#"):
                        hits.append({"file": fp.name, "line": i, "token": tok, "text": line.strip()[:160]})
    unused_ok = "20260803" not in DAYS and all(d < "20260803" for d in DAYS)
    return {
        "status": "PASS" if not hits and unused_ok else "FAIL",
        "forbidden_hits": hits[:50],
        "n_hits": len(hits),
        "unused_data_ok": unused_ok,
        "risk_feature_lineage": "push_jsonl bid/ask/qty/ages/PreviousClose + jpx_tick_size_yen only",
        "risk_decision_lineage": "static eligibility requires configured risk budget; none invented",
    }


def _band_for(notional: float) -> str:
    if notional <= 300_000:
        return "LE_300K"
    if notional <= 500_000:
        return "300K_500K"
    if notional <= 1_000_000:
        return "500K_1M"
    return "GT_1M"


def run_once(*, label: str = "A") -> dict[str, Any]:
    run_id = f"e1x10_risk_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{label}"

    # --- identity ---
    closure_path = CLOSURE_DIR / "report.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    if SOURCE_CLOSURE not in str(closure.get("run_id")):
        return {
            "analysis_id": ANALYSIS_ID,
            "document_id": DOCUMENT_ID,
            "run_id": run_id,
            "verdict": "E1_X10_RISK_UNIVERSE_IDENTITY_MISMATCH",
            "mismatches": [{"expected": SOURCE_CLOSURE, "got": closure.get("run_id")}],
            "determinism_shas": {"verdict": "E1_X10_RISK_UNIVERSE_IDENTITY_MISMATCH"},
            "safety": _safety(),
            "stop": True,
            "_sheets": {},
        }

    risk_cfg = discover_risk_config()
    sm = load_source_manifest()
    symbols_by_day = {day: _universe_from_manifest(sm, day) for day in DAYS}
    all_symbols = sorted({s for ss in symbols_by_day.values() for s in ss})

    print(f"=== [{label}] Risk config: {risk_cfg['status']} ===", flush=True)
    print(f"=== [{label}] Symbols {len(all_symbols)} days {len(DAYS)} ===", flush=True)

    day_metrics: list[dict[str, Any]] = []
    ref_rows: list[dict[str, Any]] = []
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for day in DAYS:
        universe = set(symbols_by_day[day])
        print(f"=== [{label}] Day {day} n={len(universe)} ===", flush=True)
        for sym in sorted(universe):
            rows = list(iter_symbol_day_rows(day, sym))
            ref = reference_price_from_rows(day, rows)
            ref_row = {"symbol": sym, **ref}
            ref_rows.append(ref_row)
            if not rows:
                continue
            m = aggregate_symbol_day(
                day, sym, rows, ref,
                max_age=float(risk_cfg["freshness_max_price_age_sec"]),
            )
            day_metrics.append(m)
            by_sym[sym].append(m)

    symbol_summaries = [summarize_symbol(rs) for sym, rs in sorted(by_sym.items())]

    # coverage
    n_sym = len(all_symbols)
    n_sym_with = len(by_sym)
    n_ref_ok = sum(1 for r in ref_rows if r.get("asof_valid") and r.get("reference_price") is not None)
    n_spread_ok = sum(1 for s in symbol_summaries if s.get("spread_days_ok"))
    n_exec = sum(1 for s in symbol_summaries if (s.get("n_exec_anchors_total") or 0) > 0)
    coverage = {
        "n_universe_symbols": n_sym,
        "n_symbols_with_quotes": n_sym_with,
        "symbol_quote_coverage": n_sym_with / n_sym if n_sym else 0.0,
        "reference_price_symbol_day_coverage": n_ref_ok / len(ref_rows) if ref_rows else 0.0,
        "spread_ok_symbol_coverage": n_spread_ok / n_sym_with if n_sym_with else 0.0,
        "exec_anchor_symbol_coverage": n_exec / n_sym_with if n_sym_with else 0.0,
        "n_symbol_days": len(day_metrics),
        "n_exec_anchors_total": sum(s.get("n_exec_anchors_total") or 0 for s in symbol_summaries),
    }

    # notional distribution
    notionals = [s["one_lot_notional_median"] for s in symbol_summaries if s.get("one_lot_notional_median") is not None]
    import numpy as np

    def q(xs, p):
        return float(np.quantile(xs, p)) if xs else None

    notional_dist = {
        "min": min(notionals) if notionals else None,
        "p25": q(notionals, 0.25),
        "median": q(notionals, 0.50),
        "p75": q(notionals, 0.75),
        "p90": q(notionals, 0.90),
        "max": max(notionals) if notionals else None,
        "n": len(notionals),
    }
    tick_risks = [s["one_tick_risk_yen_100_median"] for s in symbol_summaries if s.get("one_tick_risk_yen_100_median") is not None]
    tick_dist = {
        "min": min(tick_risks) if tick_risks else None,
        "median": q(tick_risks, 0.50),
        "p90": q(tick_risks, 0.90),
        "max": max(tick_risks) if tick_risks else None,
    }

    # capital concentration (no invented capital)
    pos_cap = risk_cfg.get("max_concurrent_positions")
    ranked = sorted(
        [s for s in symbol_summaries if s.get("one_lot_notional_median") is not None],
        key=lambda x: -float(x["one_lot_notional_median"]),
    )
    top3 = ranked[:3]
    top3_sum = sum(float(s["one_lot_notional_median"]) for s in top3)
    capital_block = {
        "max_concurrent_positions": pos_cap,
        "available_trading_capital_yen": None,
        "capital_status": "NOT_CONFIGURED",
        "largest_one_lot_notional": float(ranked[0]["one_lot_notional_median"]) if ranked else None,
        "largest_symbol": ranked[0]["symbol"] if ranked else None,
        "top3_one_lot_notional_sum": top3_sum if top3 else None,
        "top3_symbols": [s["symbol"] for s in top3],
        "required_capital_for_top3": top3_sum if top3 else None,
        "required_capital_for_position_cap": (
            sum(float(s["one_lot_notional_median"]) for s in ranked[: int(pos_cap)])
            if ranked and pos_cap else None
        ),
        "capital_reserve_after_top3": "NOT_CONFIGURED",
        "note": "cannot pass/fail capital concentration without available_trading_capital_yen",
    }

    # required risk budget table (not a decision)
    required_budgets = []
    for s in symbol_summaries:
        required_budgets.append({
            "symbol": s["symbol"],
            "estimated_execution_risk_yen": s.get("estimated_execution_risk_yen"),
            "one_lot_notional_median": s.get("one_lot_notional_median"),
            "spread_cost_p95": s.get("spread_cost_p95"),
            "down_jump_yen_100_p95": s.get("down_jump_yen_100_p95"),
            "exec_loss_5s_p95": s.get("exec_loss_5s_p95"),
        })

    # static eligibility — only if budget+capital configured
    eligibility = []
    if risk_cfg["status"] == "CONFIGURED":
        limit = float(risk_cfg["per_trade_risk_limit_yen"])
        for s in symbol_summaries:
            est = s.get("estimated_execution_risk_yen")
            reasons = []
            if est is None:
                status = "RISK_NOT_EVALUABLE"
                reasons.append("missing_execution_risk")
            elif est > limit:
                status = "RISK_INELIGIBLE"
                reasons.append("execution_risk_above_limit")
            else:
                status = "ELIGIBLE"
            eligibility.append({"symbol": s["symbol"], "status": status, "reasons": reasons, "estimated_execution_risk_yen": est})
    else:
        for s in symbol_summaries:
            eligibility.append({
                "symbol": s["symbol"],
                "status": "RISK_NOT_EVALUABLE",
                "reasons": ["RISK_BUDGET_NOT_CONFIGURED"],
                "estimated_execution_risk_yen": s.get("estimated_execution_risk_yen"),
            })

    # notional bands (descriptive)
    bands = {name: {"band": name, "n_symbols": 0, "symbols": [],
                    "spread_p95": [], "down_jump_p95": [], "exec5_p95": [],
                    "depth_cov": [], "fresh": []}
             for name, _, _ in NOTIONAL_BANDS}
    for s in symbol_summaries:
        n = s.get("one_lot_notional_median")
        if n is None:
            continue
        b = _band_for(float(n))
        bands[b]["n_symbols"] += 1
        bands[b]["symbols"].append(s["symbol"])
        if s.get("spread_cost_p95") is not None:
            bands[b]["spread_p95"].append(float(s["spread_cost_p95"]))
        if s.get("down_jump_yen_100_p95") is not None:
            bands[b]["down_jump_p95"].append(float(s["down_jump_yen_100_p95"]))
        if s.get("exec_loss_5s_p95") is not None:
            bands[b]["exec5_p95"].append(float(s["exec_loss_5s_p95"]))
        if s.get("bid_depth_100_coverage") is not None:
            bands[b]["depth_cov"].append(float(s["bid_depth_100_coverage"]))
        if s.get("both_fresh_rate") is not None:
            bands[b]["fresh"].append(float(s["both_fresh_rate"]))
    band_rows = []
    for name, _, _ in NOTIONAL_BANDS:
        b = bands[name]
        band_rows.append({
            "band": name,
            "n_symbols": b["n_symbols"],
            "spread_p95_median": q(b["spread_p95"], 0.50),
            "down_jump_p95_median": q(b["down_jump_p95"], 0.50),
            "exec_loss_5s_p95_median": q(b["exec5_p95"], 0.50),
            "depth_coverage_median": q(b["depth_cov"], 0.50),
            "freshness_rate_median": q(b["fresh"], 0.50),
            "descriptive_only": True,
        })

    # 285A profile
    kiox = next((s for s in symbol_summaries if s["symbol"] == TARGET_SYMBOL), None)

    # dynamic gate feasibility (design only)
    dynamic_gate = [
        {"field": "current_one_lot_notional", "runtime_source": "best_ask_or_mid × 100",
         "availability": "YES", "missing_behavior": "FAIL_CLOSED_RECOMMENDED", "implemented_now": False},
        {"field": "current_spread_yen_bps", "runtime_source": "canonical_board Buy1/Sell1",
         "availability": "YES", "missing_behavior": "FAIL_CLOSED_RECOMMENDED", "implemented_now": False},
        {"field": "current_best_bid_qty", "runtime_source": "Buy1.Qty",
         "availability": "YES", "missing_behavior": "FAIL_CLOSED_RECOMMENDED", "implemented_now": False},
        {"field": "current_best_ask_qty", "runtime_source": "Sell1.Qty",
         "availability": "YES", "missing_behavior": "FAIL_CLOSED_RECOMMENDED", "implemented_now": False},
        {"field": "price_freshness", "runtime_source": "compute_entry_freshness / CurrentPriceTime",
         "availability": "PARTIAL_CurrentPriceTime_often_null_in_capture", "missing_behavior": "FAIL_CLOSED_RECOMMENDED", "implemented_now": False},
        {"field": "board_freshness", "runtime_source": "BidTime/AskTime vs now",
         "availability": "YES", "missing_behavior": "FAIL_CLOSED_RECOMMENDED", "implemented_now": False},
        {"field": "no_bid", "runtime_source": "missing Buy1.Price",
         "availability": "YES", "missing_behavior": "FAIL_CLOSED_RECOMMENDED", "implemented_now": False},
        {"field": "special_quote", "runtime_source": "dedicated field",
         "availability": "NOT_AVAILABLE_IN_CAPTURE", "missing_behavior": "FAIL_CLOSED_RECOMMENDED", "implemented_now": False},
    ]

    pnl_audit = _pnl_independence_audit()

    # verdict
    major_cov = (
        coverage["symbol_quote_coverage"] >= 0.90
        and coverage["reference_price_symbol_day_coverage"] >= 0.90
        and coverage["spread_ok_symbol_coverage"] >= 0.90
    )
    if risk_cfg["status"] == "RISK_BUDGET_NOT_CONFIGURED":
        if not major_cov and coverage["symbol_quote_coverage"] < 0.5:
            verdict = "E1_X10_RISK_UNIVERSE_DATA_INSUFFICIENT"
            next_step = "enumerate missing capture fields; do not auto-change capture"
        else:
            verdict = "E1_X10_RISK_BUDGET_NOT_CONFIGURED"
            next_step = (
                "report required risk budgets and capital needs; "
                "do not auto-select budget; optional later: Risk Universe Policy freeze plan after budget is set"
            )
    elif not major_cov:
        verdict = "E1_X10_RISK_UNIVERSE_DATA_INSUFFICIENT"
        next_step = "enumerate missing capture fields"
    else:
        # budget configured path (not expected currently)
        verdict = "E1_X10_FIXED_100SHARE_RISK_UNIVERSE_DESIGN_READY"
        next_step = "Risk Universe Policy freeze plan only; runtime implementation needs separate approval"

    vd = {
        "verdict": verdict,
        "diagnostic_status": "RISK_DESIGN_DIAGNOSTIC_ONLY",
        "pfq_closed": True,
        "robust_strategy": False,
        "next": next_step,
        "eligibility_counts": {
            "ELIGIBLE": sum(1 for e in eligibility if e["status"] == "ELIGIBLE"),
            "RISK_INELIGIBLE": sum(1 for e in eligibility if e["status"] == "RISK_INELIGIBLE"),
            "RISK_NOT_EVALUABLE": sum(1 for e in eligibility if e["status"] == "RISK_NOT_EVALUABLE"),
        },
    }

    det = {
        "source_identity_sha": sha256_obj({"closure": SOURCE_CLOSURE, "closure_sha": sha256_file(closure_path)}),
        "reference_price_sha": sha256_obj([(r["symbol"], r["day"], r.get("reference_price"), r.get("asof_valid")) for r in ref_rows]),
        "notional_sha": sha256_obj([(s["symbol"], s.get("one_lot_notional_median"), s.get("one_lot_notional_max")) for s in symbol_summaries]),
        "tick_risk_sha": sha256_obj([(s["symbol"], s.get("one_tick_risk_yen_100_median")) for s in symbol_summaries]),
        "spread_sha": sha256_obj([(s["symbol"], s.get("spread_cost_p50"), s.get("spread_cost_p95")) for s in symbol_summaries]),
        "depth_sha": sha256_obj([(s["symbol"], s.get("bid_depth_100_coverage"), s.get("ask_depth_100_coverage"), s.get("best_bid_qty_p10")) for s in symbol_summaries]),
        "freshness_sha": sha256_obj([(s["symbol"], s.get("both_fresh_rate"), s.get("board_fresh_rate")) for s in symbol_summaries]),
        "gap_risk_sha": sha256_obj([(s["symbol"], s.get("down_jump_yen_100_p95"), s.get("down_jump_yen_100_max")) for s in symbol_summaries]),
        "executable_loss_sha": sha256_obj([(s["symbol"], s.get("exec_loss_5s_p50"), s.get("exec_loss_5s_p95"), s.get("n_exec_anchors_total")) for s in symbol_summaries]),
        "symbol_risk_summary_sha": sha256_obj([(s["symbol"], s.get("estimated_execution_risk_yen"), s.get("n_days")) for s in symbol_summaries]),
        "eligibility_sha": sha256_obj([(e["symbol"], e["status"]) for e in eligibility]),
        "verdict": verdict,
    }

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "label": label,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "source_closure": SOURCE_CLOSURE,
        "period": {"days": list(DAYS), "status": "RISK_DESIGN_DIAGNOSTIC_ONLY"},
        "purpose": "AUTOMATION_RISK_ELIGIBILITY",
        "not_purpose": "ALPHA_SELECTION",
        "lot": LOT,
        "qty_unit_contract": qty_unit_contract(),
        "current_risk_config": risk_cfg,
        "coverage": coverage,
        "notional_distribution": notional_dist,
        "tick_risk_distribution": tick_dist,
        "capital_concentration": capital_block,
        "required_risk_budgets": required_budgets,
        "eligibility": eligibility,
        "eligibility_counts": vd["eligibility_counts"],
        "notional_bands": band_rows,
        "kioxia_profile": kiox,
        "dynamic_gate_feasibility": dynamic_gate,
        "pnl_independence": pnl_audit,
        "symbol_risk_summary": symbol_summaries,
        "verdict": verdict,
        "verdict_detail": vd,
        "determinism_shas": det,
        "safety": _safety(),
        "stop": True,
        "_sheets": {
            "CurrentRiskConfig": risk_cfg.get("rows") or [],
            "ReferencePrices": ref_rows,
            "OneLotNotional": [
                {"symbol": s["symbol"], "day": s["day"], "reference_price": s.get("reference_price"),
                 "one_lot_notional_yen": s.get("one_lot_notional_yen"), "status": s.get("status_notional")}
                for s in day_metrics
            ],
            "TickRisk": [
                {"symbol": s["symbol"], "day": s["day"], "reference_price": s.get("reference_price"),
                 "tick_size_yen": s.get("tick_size_yen"), "one_tick_risk_yen_100": s.get("one_tick_risk_yen_100"),
                 "source": s.get("tick_size_source")}
                for s in day_metrics
            ],
            "SpreadRisk": [
                {k: s.get(k) for k in (
                    "symbol", "day", "spread_status", "n_spread_obs", "median_spread_yen", "p90_spread_yen",
                    "p95_spread_yen", "max_spread_yen", "median_spread_bps", "p90_spread_bps",
                    "median_spread_cost_yen_100", "p95_spread_cost_yen_100",
                )}
                for s in day_metrics
            ],
            "DepthRisk": [
                {k: s.get(k) for k in (
                    "symbol", "day", "n_depth_obs", "p10_best_bid_qty", "p10_best_ask_qty",
                    "p_best_bid_qty_ge_100", "p_best_ask_qty_ge_100",
                    "p_best_bid_qty_ge_300", "p_best_ask_qty_ge_300",
                )}
                for s in day_metrics
            ],
            "Freshness": [
                {k: s.get(k) for k in (
                    "symbol", "day", "price_fresh_rate", "board_fresh_rate", "both_fresh_rate",
                    "p50_price_age", "p90_price_age", "p50_board_age", "p90_board_age",
                    "freshness_max_sec_reused",
                )}
                for s in day_metrics
            ],
            "BidJumps": [
                {k: s.get(k) for k in (
                    "symbol", "day", "n_jump_obs", "p90_down_bid_jump_yen", "p95_down_bid_jump_yen",
                    "max_down_bid_jump_yen", "p90_down_bid_jump_yen_100", "p95_down_bid_jump_yen_100",
                    "max_down_bid_jump_yen_100", "special_quote_status",
                )}
                for s in day_metrics
            ],
            "ExecutableLoss": [
                {k: s.get(k) for k in (
                    "symbol", "day", "n_exec_anchors", "exec_grid_sec",
                    "exec_loss_yen_100_1s_p50", "exec_loss_yen_100_1s_p95",
                    "exec_loss_yen_100_5s_p50", "exec_loss_yen_100_5s_p90",
                    "exec_loss_yen_100_5s_p95", "exec_loss_yen_100_5s_max",
                    "exec_loss_yen_100_10s_p95", "exec_loss_yen_100_30s_p95",
                )}
                for s in day_metrics
            ],
            "SymbolRiskSummary": symbol_summaries,
            "CapitalConcentration": [capital_block],
            "StaticEligibility": eligibility,
            "DynamicGateFeasibility": dynamic_gate,
            "NotionalBands": band_rows,
            "KioxiaProfile": [kiox] if kiox else [{"symbol": TARGET_SYMBOL, "status": "NOT_IN_UNIVERSE"}],
            "PnLIndependence": [pnl_audit],
        },
    }
    return report
