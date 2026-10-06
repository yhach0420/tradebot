"""V2 foundation decision. No strategy. V1 immutable. No paid external data required."""
from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd

from research.aligned_historical_panel_v1.time_semantics import SEMANTICS_BAR_END, SEMANTICS_BAR_START, SEMANTICS_UNKNOWN
from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.daytrade_historical_research_foundation_v2 import (
    ANALYSIS_ID,
    CASE_DAILY,
    CASE_ENTITLEMENT,
    CASE_PARTIAL,
    CASE_READY,
    CASE_TIME,
    FUTURE_RETURN_USED,
    LIVE_20260914_CHANGED,
    MINUTE_FROM_PREFERRED,
    MINUTE_TO,
    NEXT_ENTITLEMENT,
    NEXT_RUNTIME,
    NEXT_TIME,
    PNL_USED,
    RUNTIME_STOCK_MAX,
    STRATEGY_SEARCH_STARTED,
    V1_1_ADOPTED,
    V1_MODIFIED,
    WINDOW_FIRST,
)
from research.daytrade_historical_research_foundation_v2.eligibility import LABEL_TRADE, classify
from research.daytrade_historical_research_foundation_v2.isolation import REF
from research.daytrade_historical_research_foundation_v2.market_state import ARCHITECTURE, compression_agreement
from research.daytrade_historical_research_foundation_v2.metrics import minute_metrics
from research.daytrade_historical_research_foundation_v2.minute_panel import ingest_pool_minutes, probe_entitlement_and_ticks
from research.daytrade_historical_research_foundation_v2.pool import build_pool, fetch_daily_by_date, load_session_days
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE as LIVE_TRADING_DATE


def decide(*, entitlement: dict[str, Any], semantics: str, pool_ok: bool, panel: dict[str, Any] | None, state: dict[str, Any] | None) -> dict[str, Any]:
    minute = dict(entitlement.get("minute") or {})
    if minute.get("http_status") in {401, 402, 403} or minute.get("addon_required"):
        return {
            "CASE": "ENTITLEMENT",
            "VERDICT": CASE_ENTITLEMENT,
            "NEXT": NEXT_ENTITLEMENT,
            "reason": str(minute.get("reason") or "minute_not_entitled"),
            "INTERPRETATION": "J-Quants minute endpoint is not active on this key. Add-on purchase is not requested. Daily is not used as intraday.",
        }
    if not minute.get("available"):
        return {
            "CASE": "ENTITLEMENT",
            "VERDICT": CASE_ENTITLEMENT,
            "NEXT": NEXT_ENTITLEMENT,
            "reason": str(minute.get("reason") or "minute_unavailable"),
            "INTERPRETATION": "Minute probe did not return HTTP 200.",
        }
    if semantics not in {SEMANTICS_BAR_START, SEMANTICS_BAR_END}:
        return {
            "CASE": "TIME",
            "VERDICT": CASE_TIME,
            "NEXT": NEXT_TIME,
            "reason": "time_semantics_unknown",
            "INTERPRETATION": "Minute Time is not proven BAR_START or BAR_END. Mechanism research does not start.",
        }
    if not pool_ok:
        return {
            "CASE": "DAILY",
            "VERDICT": CASE_DAILY,
            "NEXT": NEXT_RUNTIME,
            "reason": "daily_pool_failed",
            "INTERPRETATION": "Prime 60d daily pool could not be built.",
        }
    if not panel or not panel.get("ok"):
        return {
            "CASE": "PARTIAL",
            "VERDICT": CASE_PARTIAL,
            "NEXT": NEXT_RUNTIME,
            "reason": "minute_panel_incomplete",
            "INTERPRETATION": "Research pool exists and time semantics are proven, but the historical minute panel is incomplete.",
        }
    return {
        "CASE": "READY",
        "VERDICT": CASE_READY,
        "NEXT": NEXT_RUNTIME,
        "reason": "pool_panel_eligibility_context_architecture_ready",
        "INTERPRETATION": "V2 historical research foundation is ready. Runtime 50-slot design is next. Strategy search has not started.",
        "reference_state_buildable": bool((state or {}).get("buildable", True)),
    }


def _context_complements(*, pool_rows: list[dict[str, Any]], trade_syms: set[str], need: bool) -> list[dict[str, Any]]:
    if not need:
        return []
    trade_sec = {r.get("tse33_name") for r in pool_rows if r["symbol"] in trade_syms}
    rest = [r for r in pool_rows if r["symbol"] not in trade_syms]
    out = []
    seen_sec: set[str] = set()
    for r in sorted(rest, key=lambda x: (-(x.get("median_va") or 0.0), x["symbol"])):
        sec = str(r.get("tse33_name") or "")
        if sec in trade_sec or sec in seen_sec:
            continue
        seen_sec.add(sec)
        out.append(
            {
                "symbol": r["symbol"],
                "tse33_name": sec,
                "entry_eligible": False,
                "role": "CONTEXT_ONLY",
                "why": "missing_sector_in_trade_eligible_for_reference_state",
            }
        )
        if len(out) >= 8:
            break
    return out


def build_report_body() -> dict[str, Any]:
    probe = probe_entitlement_and_ticks()
    minute = dict(probe.get("minute") or {})
    semantics = str(probe.get("EQUITY_MINUTE_TIME_SEMANTICS") or SEMANTICS_UNKNOWN)
    print(
        f"MINUTE_PROBE status={minute.get('http_status')} available={minute.get('available')} "
        f"semantics={semantics} tick_performed={bool((probe.get('ticks') or {}).get('performed'))}",
        flush=True,
    )
    session_days = load_session_days()
    daily = {"ok": False, "by_symbol": {}}
    pool = {"union": [], "eligible_before_ranking_n": None}
    panel = None
    labels: list[dict[str, Any]] = []
    state = {"buildable": False}
    compression = {"buildable": False}
    context_only: list[dict[str, Any]] = []

    if minute.get("available"):
        print(f"SESSION_DAYS {len(session_days)}", flush=True)
        if not session_days:
            daily = {"ok": False, "reason": "session_calendar_missing"}
        else:
            daily = fetch_daily_by_date(session_days)
            if daily.get("ok"):
                pool = build_pool(session_days=session_days, daily=daily)
                print(f"POOL eligible={pool.get('eligible_before_ranking_n')} union={pool.get('union_n')}", flush=True)
        if semantics in {SEMANTICS_BAR_START, SEMANTICS_BAR_END} and pool.get("union"):
            symbols = [r["symbol"] for r in pool["union"]]
            panel = ingest_pool_minutes(symbols=symbols, semantics=semantics)
            frames_60: list[pd.DataFrame] = []
            for row in pool["union"]:
                path = REF / "minute" / f"minute_{row['symbol']}_{MINUTE_FROM_PREFERRED}_{MINUTE_TO}.parquet"
                mm = None
                if path.is_file():
                    try:
                        df = pd.read_parquet(
                            path,
                            columns=["symbol", "date", "time_label", "open", "high", "low", "close", "volume", "trading_value"],
                        )
                    except Exception:
                        df = pd.read_parquet(path)
                    df60 = df[df["date"].astype(str) >= WINDOW_FIRST].copy()
                    if not df60.empty:
                        df60["symbol"] = str(row["symbol"])
                        frames_60.append(df60)
                    mm = minute_metrics(df60, session_n_expected=len(session_days))
                labels.append(classify(pool_row=row, minute_row=mm))
            trade_syms = {r["symbol"] for r in labels if r.get("label") == LABEL_TRADE}
            if frames_60:
                loaded = pd.concat(frames_60, ignore_index=True)
                compression = compression_agreement(pool_df=loaded, trade_symbols=sorted(trade_syms))
                state = {"buildable": True, "architecture": ARCHITECTURE, "compression": compression, "used_as_entry_signal": False}
                need = not bool(compression.get("trade_eligible_reproduces_pool_state"))
                context_only = _context_complements(pool_rows=pool["union"], trade_syms=trade_syms, need=need)
            else:
                state = {"buildable": False, "architecture": ARCHITECTURE, "used_as_entry_signal": False}
        elif pool.get("union"):
            for row in pool["union"]:
                labels.append(classify(pool_row=row, minute_row=None))

    decision = decide(
        entitlement=probe,
        semantics=semantics,
        pool_ok=bool(daily.get("ok")) and bool(pool.get("union")),
        panel=panel,
        state=state,
    )
    label_counts = dict(Counter(r.get("label") for r in labels))
    trade_n = int(label_counts.get(LABEL_TRADE) or 0)
    runtime_stock_n = trade_n + len(context_only)
    return {
        "probe": probe,
        "daily": {k: daily.get(k) for k in ("ok", "reason", "status", "symbol_n", "from_cache_days", "network_days", "sample_keys") if k in daily or True},
        "pool": {k: v for k, v in pool.items() if k not in {"eligible", "liquidity_set", "activity_set"}},
        "pool_union_rows": pool.get("union") or [],
        "panel": panel,
        "eligibility": labels,
        "eligibility_counts": label_counts,
        "reference_market_state": state,
        "context_only_proposal": context_only,
        "runtime_draft_not_frozen": {
            "trade_eligible_n": trade_n,
            "context_only_n": len(context_only),
            "runtime_stock_n": runtime_stock_n,
            "runtime_stock_max": RUNTIME_STOCK_MAX,
            "nk225mini": 1,
            "topix_futures": 1,
            "kabu_total_if_drafted": runtime_stock_n + 2,
            "within_50": runtime_stock_n + 2 <= 50,
            "exceeds_runtime_stock_max": runtime_stock_n > RUNTIME_STOCK_MAX,
            "frozen": False,
            "note": "Draft input for DESIGN_KABU_50_SLOT_RUNTIME_UNIVERSE_V1. Not a registration set. Filling 50 slots is not an objective.",
        },
        "decision": {
            **decision,
            "strategy_search_started": False,
            "pnl_used": False,
            "future_return_used": False,
            "v1_modified": False,
            "v1_1_adopted": bool(V1_1_ADOPTED),
        },
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    probe = dict(report.get("probe") or {})
    minute = dict(probe.get("minute") or {})
    ticks = dict(probe.get("ticks") or {})
    pool = dict(report.get("pool") or {})
    panel = dict(report.get("panel") or {})
    state = dict(report.get("reference_market_state") or {})
    d = dict(report.get("decision") or {})
    elig = list(report.get("eligibility") or [])
    metrics_complete = bool(elig) and all(bool(r.get("minute_metrics_complete")) for r in elig)
    bid_ask = bool(ticks.get("bid_ask_fields_in_header")) or bool(probe.get("bid_ask_in_official_trades"))
    return {
        "minute_entitlement_active": bool(minute.get("available")) and minute.get("http_status") == 200,
        "historical_candidate_n_before_ranking": pool.get("eligible_before_ranking_n"),
        "liquidity_top80_n": pool.get("liquidity_top80_n"),
        "activity_top40_n": pool.get("activity_top40_n"),
        "union_research_pool_n": pool.get("union_n"),
        "actual_history_first_last": [panel.get("history_first"), panel.get("history_last")] if panel else None,
        "minute_time_semantics": probe.get("EQUITY_MINUTE_TIME_SEMANTICS"),
        "tick_crosscheck_pass": str(probe.get("EQUITY_MINUTE_TIME_SEMANTICS")) in {SEMANTICS_BAR_START, SEMANTICS_BAR_END},
        "per_symbol_minute_coverage": None if not panel else {"ok_n": panel.get("ok_n"), "fail_n": len(panel.get("fail_symbols") or [])},
        "trade_eligibility_metrics_complete": metrics_complete,
        "bid_ask_historical_available": bool(bid_ask),
        "reference_market_state_buildable": bool(state.get("buildable")),
        "strategy_search_started": bool(STRATEGY_SEARCH_STARTED),
        "pnl_used": bool(PNL_USED),
        "future_return_used": bool(FUTURE_RETURN_USED),
        "old_v1_modified": bool(V1_MODIFIED),
        "runtime_modified": False,
        "paper_modified": False,
        "live_20260914_modified": bool(LIVE_20260914_CHANGED),
        "submit_cancel_live": "0/0/0",
        "FEATURE_MINING_CLOSED": bool(FEATURE_MINING_CLOSED),
        "v1_1_adopted": False,
        "panel_conditioned_as_of_202609": True,
        "claim_selectable_in_2025": False,
        "http_status_minute": minute.get("http_status"),
        "tick_performed": bool(ticks.get("performed")),
        "paid_external_required": False,
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }


assert ANALYSIS_ID == "DAYTRADE_HISTORICAL_RESEARCH_FOUNDATION_V2"
assert LIVE_TRADING_DATE == "20260914"
