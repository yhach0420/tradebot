"""Freeze decision from official 60d daily. No PnL. No 20260914 live. No threshold search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED
from research.fixed_daytrade_universe_v1 import (
    BAND_MAX,
    BAND_MIN,
    CASE_CALENDAR_MISMATCH,
    CASE_FETCH,
    CASE_FROZEN,
    CASE_INSUFFICIENT,
    CASE_KEY_REQUIRED,
    CASE_NO_VA,
    CASE_SCHEMA,
    CASE_WAITING,
    EMPTY_UNFROZEN_SHA,
    EXPECTED_WINDOW_FIRST,
    EXPECTED_WINDOW_LAST,
    EXPECTED_WINDOW_N,
    FORBIDDEN_DATA_FROM,
    FREEZE_ASOF_JST,
    LAST_COMPLETE_TSE_SESSION,
    LIVE_20260914_CHANGED,
    MIN_ACTIVE_SESSION_N,
    MIN_MEDIAN_VA_JPY,
    MIN_P20_VA_JPY,
    MIN_SESSION_COVERAGE,
    NEXT_PANEL,
    NEXT_REDESIGN,
    NEXT_RETRY,
    NEXT_SET_KEY,
    OUTCOME_USED,
    PANEL_BUILT,
    PANEL_CONDITIONED_AS_OF,
    PAPER_CHANGED,
    PAPER_LOT_QTY,
    PAPER_RESULT_USED,
    REVIEW_CADENCE,
    ROBUSTNESS_HALF_RATIO_FLOOR,
    RUNTIME_CHANGED,
    SELECTION_METHODOLOGY_VERSION,
    STRATEGY_SEARCH_STARTED,
    TARGET_N,
    TRAIL_SESSIONS,
    UNIVERSE_ID,
)
from research.fixed_daytrade_universe_v1.acquire import acquire_official_daily
from research.fixed_daytrade_universe_v1.liquidity import liquidity_pass, metrics_aligned_to_sessions
from research.fixed_daytrade_universe_v1.operability import (
    PAPER_ELIGIBILITY_UNKNOWN,
    STATUS_RESEARCH_MEMBER,
    STATUS_STRUCT,
    evaluate_operability,
    membership_status,
    paper_eligibility_status,
    paper_rules,
    phase0_blocker_notes,
)
from research.fixed_universe_historical_foundation_v1.universe import (
    CANDIDATES,
    REQUIRED_SECTOR_BUCKETS,
    candidate_rows,
)
from research.run_20260914_day2_futures_plus_first_live_breadth_v1 import TRADING_DATE as LIVE_TRADING_DATE

THRESHOLD_RATIONALE = (
    "Floors are precommitted day-trade tradability and continuity requirements. "
    "They are not fitted to the candidate 45 and are not optimized on PnL, future return, "
    "strategy score, or Paper results. A name must trade on most official TSE sessions "
    f"(finite-Va days >= {MIN_ACTIVE_SESSION_N} of {TRAIL_SESSIONS}; coverage >= {MIN_SESSION_COVERAGE:.4f}) "
    "so a later minute panel is usable. "
    f"median daily trading value >= {MIN_MEDIAN_VA_JPY:.0f} JPY and p20 >= {MIN_P20_VA_JPY:.0f} JPY "
    "are absolute liquidity floors, not a ranking cutoff. "
    f"first30 vs last30 median Va ratio floor {ROBUSTNESS_HALF_RATIO_FLOOR} flags surge-only or collapse names. "
    "If all 45 clear the floors, keep all 45. Do not drop names to hit 45. "
    "High 100-share notional does not exclude research membership; Paper capital gate is a separate eligibility flag."
)


def universe_identity_sha(symbols_sorted: list[str]) -> str:
    payload = {
        "universe_id": UNIVERSE_ID,
        "symbols_sorted": sorted({str(s) for s in symbols_sorted}),
        "selection_methodology_version": SELECTION_METHODOLOGY_VERSION,
        "data_cutoff": LAST_COMPLETE_TSE_SESSION,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _va_distribution(values: list[float | None]) -> dict[str, Any] | None:
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return None
    xs.sort()

    def _pct(q: float) -> float:
        if len(xs) == 1:
            return xs[0]
        pos = (len(xs) - 1) * q / 100.0
        lo = int(pos)
        hi = min(lo + 1, len(xs) - 1)
        frac = pos - lo
        return xs[lo] * (1.0 - frac) + xs[hi] * frac

    return {
        "n": len(xs),
        "min": xs[0],
        "p20": _pct(20.0),
        "median": _pct(50.0),
        "p80": _pct(80.0),
        "max": xs[-1],
    }


def evaluate_candidates(acquired: dict[str, Any]) -> list[dict[str, Any]]:
    window = dict(acquired.get("window") or {})
    days = list(window.get("days") or [])
    daily_map = dict(acquired.get("daily_by_symbol") or {})
    master_end = dict(acquired.get("master_asof_cutoff") or {})
    master_start = dict(acquired.get("master_asof_window_start") or {})
    out: list[dict[str, Any]] = []
    for base in candidate_rows():
        code = str(base["symbol"])
        rows = list(daily_map.get(code) or [])
        liq = metrics_aligned_to_sessions(session_days=days, rows=rows)
        liq_ok, liq_reasons = liquidity_pass(liq)
        m_end = dict(master_end.get(code) or {})
        m_start = dict(master_start.get(code) or {})
        listed_end = bool(m_end)
        listed_start = bool(m_start)
        common = bool(m_end.get("common_stock_domestic")) if m_end else False
        listing_ok = listed_end and listed_start and common and (not m_end.get("etf"))
        close_asof = None
        by_date = {str(r.get("date")): r for r in rows}
        last_row = by_date.get(str(window.get("last") or LAST_COMPLETE_TSE_SESSION))
        if last_row is not None:
            try:
                close_asof = float(last_row.get("close"))
            except (TypeError, ValueError):
                close_asof = None
        op = evaluate_operability(trading_unit=m_end.get("trading_unit"), price=close_asof)
        data_usable = bool(rows)
        status = membership_status(
            listing_ok=listing_ok,
            common_stock=common,
            liquidity_ok=liq_ok,
            operability=op,
            data_usable=data_usable,
        )
        paper_el = paper_eligibility_status(op) if status == STATUS_RESEARCH_MEMBER else (
            PAPER_ELIGIBILITY_UNKNOWN if status == STATUS_STRUCT else None
        )
        freeze_member = status == STATUS_RESEARCH_MEMBER
        out.append(
            {
                **base,
                "quantitative_liquidity_available": True,
                "liquidity_pass": liq_ok,
                "liquidity_fail_reasons": liq_reasons,
                "listing_continuity_pass": listing_ok,
                "listed_asof_cutoff": listed_end,
                "listed_asof_window_start": listed_start,
                "common_stock_domestic": common,
                "data_usable": data_usable,
                "prod_cat": m_end.get("prod_cat"),
                "market": m_end.get("market"),
                "market_name": m_end.get("market_name"),
                "sector33_name_official": m_end.get("sector33_name"),
                "trading_unit": m_end.get("trading_unit"),
                "trading_unit_field_in_master": False,
                "close_asof_20260911": close_asof,
                "share_100_notional": op.get("share_100_notional"),
                "research_member": freeze_member,
                "operability_pass": None,
                "membership_status": status,
                "paper_eligibility": paper_el,
                "structurally_ineligible": status == STATUS_STRUCT,
                "temporarily_not_trade_eligible": paper_el == "FIXED_MEMBER_TEMPORARILY_NOT_TRADE_ELIGIBLE",
                "retained_only_for_sector": False,
                "outcome_used": False,
                "historical_pnl_used": False,
                "future_return_used": False,
                "paper_result_used": False,
                "strategy_score_used": False,
                "market_result_20260914_used": False,
                "qualitative_core_liquid_not_sufficient_to_freeze": True,
                "same_operability_rule": True,
                **{k: liq.get(k) for k in (
                    "session_n",
                    "session_coverage",
                    "median_daily_trading_value_60d",
                    "p20_daily_trading_value_60d",
                    "median_daily_volume_60d",
                    "median_trading_value_first30",
                    "median_trading_value_last30",
                    "median_volume_first30",
                    "median_volume_last30",
                    "active_session_n",
                    "expected_session_n",
                    "missing_session_n",
                    "null_trade_day_n",
                    "zero_or_missing_day_n",
                    "recent_surge_only_flag",
                    "recent_collapse_flag",
                    "did_not_fill_missing_as_zero_volume",
                )},
            }
        )
    return out


def _fail(case: str, verdict: str, nxt: str, *, reason: str, interpretation: str) -> dict[str, Any]:
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "universe_frozen": False,
        "final_n": None,
        "final_symbols": [],
        "reason": reason,
        "did_not_guess": True,
        "qualitative_core_liquid_used_to_freeze": False,
        "INTERPRETATION": interpretation,
        "historical_pnl_used": False,
        "future_return_used": False,
        "paper_result_used": False,
        "strategy_score_used": False,
        "market_result_20260914_used": False,
    }


def decide(*, acquired: dict[str, Any], audits: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    hint = acquired.get("CASE_HINT")
    if hint == CASE_KEY_REQUIRED or (not acquired.get("available") and not hint):
        return _fail(
            "KEY_REQUIRED",
            CASE_KEY_REQUIRED,
            NEXT_SET_KEY,
            reason=str(acquired.get("reason") or "jquants_api_key_absent"),
            interpretation=(
                "J-Quants API V2 key is not in the environment. "
                "No yfinance, scraping, or kabu /board fallback. Freeze not guessed."
            ),
        )
    if hint == CASE_CALENDAR_MISMATCH:
        return _fail(
            "CALENDAR_MISMATCH",
            CASE_CALENDAR_MISMATCH,
            NEXT_RETRY,
            reason=str(acquired.get("reason") or "calendar_window_mismatch"),
            interpretation="Official J-Quants TSE calendar window does not match expected 20260618-20260911 n=60.",
        )
    if hint == CASE_NO_VA:
        return _fail(
            "NO_VA",
            CASE_NO_VA,
            NEXT_RETRY,
            reason=str(acquired.get("reason") or "Va_missing"),
            interpretation="Official trading value field Va is not in the daily response. Close*Volume was not substituted.",
        )
    if hint == CASE_SCHEMA:
        return _fail(
            "SCHEMA",
            CASE_SCHEMA,
            NEXT_RETRY,
            reason=str(acquired.get("reason") or "schema_mismatch"),
            interpretation="J-Quants daily/master schema mismatch. Field guessing is forbidden.",
        )
    if hint == CASE_FETCH or not acquired.get("available"):
        return _fail(
            "FETCH",
            CASE_FETCH if hint == CASE_FETCH else CASE_WAITING,
            NEXT_RETRY,
            reason=str(acquired.get("reason") or "fetch_failed"),
            interpretation="Official daily fetch failed closed. No alternate source.",
        )
    rows = audits if audits is not None else evaluate_candidates(acquired)
    members = [r for r in rows if r.get("membership_status") == STATUS_RESEARCH_MEMBER]
    symbols = sorted({str(r["symbol"]) for r in members})
    n = len(symbols)
    frozen = BAND_MIN <= n <= BAND_MAX
    if frozen:
        sha = universe_identity_sha(symbols)
        assert sha != EMPTY_UNFROZEN_SHA
        assert n > 0
        return {
            "CASE": "FROZEN",
            "VERDICT": CASE_FROZEN,
            "NEXT": NEXT_PANEL,
            "universe_frozen": True,
            "final_n": n,
            "final_symbols": symbols,
            "identity_sha256": sha,
            "reason": "quantitative_gates_passed_without_pnl",
            "did_not_guess": True,
            "qualitative_core_liquid_used_to_freeze": False,
            "low_liquidity_retained_only_for_sector": False,
            "INTERPRETATION": (
                f"{n} names passed research membership gates (60d Va/Vo, coverage, listing, "
                "common-stock, data usability). Trading unit / 100-share Paper eligibility "
                "is separate and not a freeze blocker."
            ),
            "historical_pnl_used": False,
            "future_return_used": False,
            "paper_result_used": False,
            "strategy_score_used": False,
            "market_result_20260914_used": False,
            "candidate_n": len(CANDIDATES),
        }
    return {
        "CASE": "INSUFFICIENT",
        "VERDICT": CASE_INSUFFICIENT,
        "NEXT": NEXT_REDESIGN,
        "universe_frozen": False,
        "final_n": n,
        "final_symbols": symbols,
        "reason": "operational_liquid_names_below_40" if n < BAND_MIN else "outside_40_50_band",
        "did_not_guess": True,
        "qualitative_core_liquid_used_to_freeze": False,
        "INTERPRETATION": f"{n} names passed gates; required band is {BAND_MIN}-{BAND_MAX}.",
        "historical_pnl_used": False,
        "future_return_used": False,
        "paper_result_used": False,
        "strategy_score_used": False,
        "market_result_20260914_used": False,
        "candidate_n": len(CANDIDATES),
    }


def build_manifest(*, decision: dict[str, Any], acquired: dict[str, Any], phase0_fix: dict[str, Any]) -> dict[str, Any]:
    frozen = bool(decision.get("universe_frozen"))
    symbols = [str(s) for s in (decision.get("final_symbols") or [])] if frozen else []
    symbols_sorted = sorted(symbols)
    sha = universe_identity_sha(symbols_sorted) if frozen else None
    if frozen:
        assert sha != EMPTY_UNFROZEN_SHA
        assert symbols_sorted
    window = dict(acquired.get("window") or {})
    return {
        "universe_id": UNIVERSE_ID,
        "frozen": frozen,
        "freeze_asof_jst": FREEZE_ASOF_JST,
        "data_cutoff": LAST_COMPLETE_TSE_SESSION,
        "last_complete_tse_session": LAST_COMPLETE_TSE_SESSION,
        "forbidden_data_from": FORBIDDEN_DATA_FROM,
        "symbol_n": len(symbols_sorted) if frozen else 0,
        "symbols_sorted": symbols_sorted,
        "selection_methodology_version": SELECTION_METHODOLOGY_VERSION,
        "selection_methodology": (
            "official_jquants_v2_trailing_60_tse_daily_Va_Vo_coverage_listing_master_"
            "trading_unit_if_present_paper_operability; no PnL; no future return; "
            "no qualitative-only freeze; 9983/6861 not revived"
        ),
        "source_period": {
            "first": window.get("first"),
            "last": window.get("last"),
            "n": window.get("n"),
            "observed": bool(acquired.get("available")),
        },
        "identity_sha256": sha,
        "identity_inputs": (
            "canonical_sorted_symbol_list + selection_methodology_version + data_cutoff"
        ),
        "empty_unfrozen_sha_not_reused": sha != EMPTY_UNFROZEN_SHA,
        "sector_map_status": "frozen" if frozen else "candidates_only_until_freeze",
        "panel_conditioned_as_of": PANEL_CONDITIONED_AS_OF,
        "claim_selectable_in_2025": False,
        "review_cadence": list(REVIEW_CADENCE),
        "phase0_autosplice_fix": {
            "from": phase0_fix.get("from"),
            "to": phase0_fix.get("to"),
            "ok": phase0_fix.get("ok"),
        },
        "verdict": decision.get("VERDICT"),
    }


def _sector_rows(audits: list[dict[str, Any]], members: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for bucket in REQUIRED_SECTOR_BUCKETS:
        names = [r["symbol"] for r in audits if r.get("sector_bucket") == bucket and r["symbol"] in members]
        all_cand = [r["symbol"] for r in audits if r.get("sector_bucket") == bucket]
        out.append(
            {
                "sector_bucket": bucket,
                "candidate_n": len(all_cand),
                "final_n": len(names),
                "symbols": ",".join(names),
                "required": True,
                "equal_weight_required": False,
                "liquidity_broken_for_balance": False,
                "covered": len(names) > 0,
            }
        )
    return out


def _operability_rules(audits: list[dict[str, Any]], *, data_ok: bool) -> dict[str, Any]:
    rules = paper_rules()
    rules["operability_rule_defined"] = True
    rules["applied_this_run"] = False
    if not data_ok or not audits:
        rules["operability_audit_complete"] = False
        rules["notional_audit_complete"] = False
        rules["trading_unit_audit_complete"] = False
        rules["reason_not_applied"] = (
            "close_asof_and_or_trading_unit_unavailable; paper_eligibility_audit_deferred"
        )
        return rules
    closes_ok = all(r.get("close_asof_20260911") is not None for r in audits)
    units_ok = all(r.get("trading_unit") is not None for r in audits)
    rules["notional_audit_complete"] = bool(closes_ok)
    rules["trading_unit_audit_complete"] = bool(units_ok)
    rules["operability_audit_complete"] = bool(closes_ok and units_ok)
    if not rules["operability_audit_complete"]:
        rules["reason_not_applied"] = (
            "paper_eligibility_audit_incomplete; trading_unit_and_or_close_missing; "
            "required_before_paper_not_a_research_freeze_blocker"
        )
    else:
        rules["reason_not_applied"] = None
    return rules


def build_report_body(*, phase0_fix: dict[str, Any], acquired: dict[str, Any] | None = None) -> dict[str, Any]:
    got = acquired if acquired is not None else acquire_official_daily()
    audits = evaluate_candidates(got) if got.get("available") else [
        {
            **r,
            "quantitative_liquidity_available": False,
            "liquidity_pass": None,
            "listing_continuity_pass": None,
            "operability_pass": None,
            "retained_only_for_sector": False,
            "membership_status": None,
            "paper_eligibility": PAPER_ELIGIBILITY_UNKNOWN,
            "research_member": False,
            "close_asof_20260911": None,
            "trading_unit": None,
            "share_100_notional": None,
            "qualitative_core_liquid_not_sufficient_to_freeze": True,
            "historical_pnl_used": False,
            "future_return_used": False,
            "paper_result_used": False,
            "strategy_score_used": False,
            "market_result_20260914_used": False,
        }
        for r in candidate_rows()
    ]
    decision = decide(acquired=got, audits=audits if got.get("available") else None)
    members = set(decision.get("final_symbols") or []) if decision.get("universe_frozen") else set()
    manifest = build_manifest(decision=decision, acquired=got, phase0_fix=phase0_fix)
    liq_rows = []
    liq_3030 = []
    listing_rows = []
    oper_rows = []
    for r in audits:
        liq_rows.append(
            {
                "symbol": r["symbol"],
                "session_n": r.get("session_n"),
                "session_coverage": r.get("session_coverage"),
                "median_daily_trading_value_60d": r.get("median_daily_trading_value_60d"),
                "p20_daily_trading_value_60d": r.get("p20_daily_trading_value_60d"),
                "median_daily_volume_60d": r.get("median_daily_volume_60d"),
                "active_session_n": r.get("active_session_n"),
                "expected_session_n": r.get("expected_session_n"),
                "missing_session_n": r.get("missing_session_n"),
                "null_trade_day_n": r.get("null_trade_day_n"),
                "liquidity_pass": r.get("liquidity_pass"),
                "did_not_fill_missing_as_zero_volume": r.get("did_not_fill_missing_as_zero_volume"),
            }
        )
        liq_3030.append(
            {
                "symbol": r["symbol"],
                "median_trading_value_first30": r.get("median_trading_value_first30"),
                "median_trading_value_last30": r.get("median_trading_value_last30"),
                "median_volume_first30": r.get("median_volume_first30"),
                "median_volume_last30": r.get("median_volume_last30"),
                "recent_surge_only_flag": r.get("recent_surge_only_flag"),
                "recent_collapse_flag": r.get("recent_collapse_flag"),
            }
        )
        listing_rows.append(
            {
                "symbol": r["symbol"],
                "listed_asof_cutoff": r.get("listed_asof_cutoff"),
                "listed_asof_window_start": r.get("listed_asof_window_start"),
                "prod_cat": r.get("prod_cat"),
                "common_stock_domestic": r.get("common_stock_domestic"),
                "market": r.get("market"),
                "market_name": r.get("market_name"),
                "sector33_name_official": r.get("sector33_name_official"),
                "trading_unit": r.get("trading_unit"),
                "trading_unit_field_in_master": False,
                "listing_continuity_pass": r.get("listing_continuity_pass"),
            }
        )
        oper_rows.append(
            {
                "symbol": r["symbol"],
                "close_asof_20260911": r.get("close_asof_20260911"),
                "trading_unit": r.get("trading_unit"),
                "share_100_notional": r.get("share_100_notional"),
                "membership_status": r.get("membership_status"),
                "paper_eligibility": r.get("paper_eligibility"),
                "research_member": r.get("research_member"),
                "structurally_ineligible": r.get("structurally_ineligible"),
                "temporarily_not_trade_eligible": r.get("temporarily_not_trade_eligible"),
                "same_rule": True,
            }
        )
    final_rows = [
        {
            "universe_id": UNIVERSE_ID,
            "frozen": bool(decision.get("universe_frozen")),
            "symbol": s,
            "symbol_n": decision.get("final_n"),
            "membership_status": next(
                (r.get("membership_status") for r in audits if r["symbol"] == s),
                None,
            ),
            "paper_eligibility": next(
                (r.get("paper_eligibility") for r in audits if r["symbol"] == s),
                None,
            ),
            "verdict": decision.get("VERDICT"),
        }
        for s in (decision.get("final_symbols") or [])
    ]
    if not final_rows:
        final_rows = [
            {
                "universe_id": UNIVERSE_ID,
                "frozen": False,
                "symbol": "",
                "symbol_n": decision.get("final_n"),
                "symbols": "",
                "verdict": decision.get("VERDICT"),
            }
        ]
    cred = dict(got.get("credentials") or {})
    cred.pop("value", None)
    daily_public = {
        k: v
        for k, v in got.items()
        if k
        not in {
            "daily_by_symbol",
            "master_asof_cutoff",
            "master_asof_window_start",
        }
    }
    return {
        "phase0_fix": phase0_fix,
        "daily_source": daily_public,
        "expected_window": {
            "first": (got.get("window") or {}).get("first"),
            "last": (got.get("window") or {}).get("last"),
            "n": (got.get("window") or {}).get("n"),
            "expected_first": EXPECTED_WINDOW_FIRST,
            "expected_last": EXPECTED_WINDOW_LAST,
            "expected_n": int(EXPECTED_WINDOW_N),
            "mismatch": (got.get("window") or {}).get("mismatch"),
        },
        "universe_candidates": audits,
        "sector_coverage": _sector_rows(audits, members),
        "operability_rules": _operability_rules(audits, data_ok=bool(got.get("available"))),
        "operability": oper_rows,
        "listing_master": listing_rows,
        "exclusions": phase0_blocker_notes()
        + [
            {
                "symbol": r["symbol"],
                "reason": ",".join(r.get("liquidity_fail_reasons") or []) or r.get("membership_status"),
                "membership_status": r.get("membership_status"),
                "retained_only_for_sector": False,
            }
            for r in audits
            if r.get("membership_status") != STATUS_RESEARCH_MEMBER
        ],
        "liquidity_60d": liq_rows,
        "liquidity_30_30": liq_3030,
        "source_audit": list(got.get("source_audit") or []),
        "threshold_rationale": THRESHOLD_RATIONALE,
        "manifest": manifest,
        "final_universe": final_rows,
        "decision": decision,
        "live_20260914": {
            "trading_date": LIVE_TRADING_DATE,
            "plan_changed": bool(LIVE_20260914_CHANGED),
            "registration_changed": False,
            "feature_mining_closed": bool(FEATURE_MINING_CLOSED),
            "market_result_used": False,
        },
        "outcome_flags": {
            "historical_pnl_used": False,
            "future_return_used": False,
            "paper_result_used": False,
            "strategy_score_used": False,
            "market_result_20260914_used": False,
        },
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    daily = dict(report.get("daily_source") or {})
    man = dict(report.get("manifest") or {})
    win = dict(report.get("expected_window") or {})
    cov = list(report.get("sector_coverage") or [])
    lost = [r["sector_bucket"] for r in cov if not r.get("covered")] if d.get("universe_frozen") else []
    audit = list(report.get("universe_candidates") or [])
    ops = dict(report.get("operability_rules") or {})
    fix = dict(report.get("phase0_fix") or {})
    live = dict(report.get("live_20260914") or {})
    members = [r for r in audit if r.get("membership_status") == STATUS_RESEARCH_MEMBER]
    listing_pass_n = sum(1 for r in audit if r.get("listing_continuity_pass"))
    dist = _va_distribution([r.get("median_daily_trading_value_60d") for r in members])
    p20s = [r.get("p20_daily_trading_value_60d") for r in members if r.get("p20_daily_trading_value_60d") is not None]
    covs = [r.get("session_coverage") for r in members if r.get("session_coverage") is not None]
    data_ok = bool(daily.get("available"))
    notional_complete = bool(ops.get("notional_audit_complete"))
    unit_complete = bool(ops.get("trading_unit_audit_complete"))
    audit_complete = bool(ops.get("operability_audit_complete"))
    paper_unknown_n = sum(1 for r in members if r.get("paper_eligibility") == PAPER_ELIGIBILITY_UNKNOWN)
    return {
        "1_source_period": (
            f"{win.get('first')}-{win.get('last')} n={win.get('n')}; "
            f"expected {win.get('expected_first')}-{win.get('expected_last')} n={win.get('expected_n')}; "
            f"observed={daily.get('available')}"
        ),
        "2_candidate_N": len(CANDIDATES),
        "3_quantitative_liquidity_data_available": data_ok,
        "4_final_N": d.get("final_n"),
        "5_final_symbols": d.get("final_symbols") or [],
        "6_median_trading_value_distribution": dist,
        "7_minimum_p20_trading_value": min(p20s) if p20s else None,
        "8_session_coverage_minimum": min(covs) if covs else None,
        "9_listing_continuity_pass_N": listing_pass_n if data_ok else None,
        "10_operational_compatibility_pass_N": None if not audit_complete else sum(
            1 for r in members if r.get("paper_eligibility") == "FIXED_MEMBER_TRADE_ELIGIBLE"
        ),
        "11_100_share_notional_audit_complete": bool(notional_complete),
        "12_trading_unit_audit_complete": bool(unit_complete),
        "13_9983_exact_exclusion_reason": (
            "9983 not in Phase0 candidate 45; this retry does not revive it; "
            "Phase0 EXCLUDED_LOT_SIZE_BLOCKERS mixed trading unit with 100-share notional"
        ),
        "14_6861_exact_exclusion_reason": (
            "6861 not in Phase0 candidate 45; this retry does not revive it; "
            "Phase0 EXCLUDED_LOT_SIZE_BLOCKERS mixed trading unit with 100-share notional"
        ),
        "15_same_rule_applied_to_all_candidates": bool(ops.get("same_rule_all_candidates")),
        "16_sector_bucket_N": len(REQUIRED_SECTOR_BUCKETS),
        "17_any_required_sector_lost": bool(lost),
        "18_any_low_liquidity_name_retained_only_for_sector_coverage": False,
        "19_outcome_PnL_used": bool(OUTCOME_USED),
        "20_future_return_used": False,
        "21_Paper_result_used": bool(PAPER_RESULT_USED),
        "22_PANEL_CONDITIONED": True,
        "23_universe_frozen": bool(d.get("universe_frozen")),
        "24_universe_SHA": man.get("identity_sha256"),
        "25_review_cadence": list(REVIEW_CADENCE),
        "26_Phase0_autosplice_inconsistency_corrected": bool(fix.get("ok")) and fix.get("to") is False,
        "27_strategy_search_started": bool(STRATEGY_SEARCH_STARTED),
        "28_panel_built": bool(PANEL_BUILT),
        "29_20260914_live_changed": bool(live.get("plan_changed")),
        "30_Runtime_changed": bool(RUNTIME_CHANGED),
        "31_Paper_changed": bool(PAPER_CHANGED),
        "32_submit_cancel_live": "0/0/0",
        "33_VERDICT": d.get("VERDICT"),
        "34_NEXT": d.get("NEXT"),
        "target_n": int(TARGET_N),
        "band": [int(BAND_MIN), int(BAND_MAX)],
        "panel_conditioned_as_of": PANEL_CONDITIONED_AS_OF,
        "did_not_guess": bool(d.get("did_not_guess")),
        "FEATURE_MINING_CLOSED": bool(FEATURE_MINING_CLOSED),
        "historical_pnl_used": False,
        "future_return_used": False,
        "paper_result_used": False,
        "strategy_score_used": False,
        "market_result_20260914_used": False,
        "empty_unfrozen_sha_not_reused": man.get("identity_sha256") != EMPTY_UNFROZEN_SHA,
        "trading_unit_field_in_official_master": False,
        "paper_lot_qty_is_not_exchange_unit": int(PAPER_LOT_QTY) == 100,
        "operability_rule_defined": bool(ops.get("operability_rule_defined")),
        "operability_audit_complete": bool(audit_complete),
        "applied_this_run": bool(ops.get("applied_this_run")),
        "paper_eligibility_unknown_n": paper_unknown_n if data_ok else None,
        "paper_preflight_required": True,
        "trading_unit_missing_is_not_research_blocker": True,
    }


assert FEATURE_MINING_CLOSED is True
assert LIVE_TRADING_DATE == "20260914"
assert CASE_FROZEN.startswith("FIXED_DAYTRADE")
assert CASE_KEY_REQUIRED.startswith("JQUANTS_API_KEY_REQUIRED")
assert NEXT_PANEL == "BUILD_ALIGNED_HISTORICAL_PANEL_V1"
assert universe_identity_sha([]) != EMPTY_UNFROZEN_SHA
assert universe_identity_sha(["7203", "9984"]) != EMPTY_UNFROZEN_SHA
