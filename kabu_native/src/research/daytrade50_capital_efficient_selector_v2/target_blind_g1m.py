"""Target-blind DAYTRADE50 V2 walk at the stored capital model.

Reads the Discord SoT. Does not search leverage, rewrite the score, or touch V16.
"""
from __future__ import annotations

import csv
import json
import statistics
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from openpyxl import Workbook

from research.auto50_research_capture.mode import MAX_TICK_RATIO, MIN_CLOSE
from research.daytrade50_capital_efficient_selector_v2.baselines import (
    auto50_v1_score,
    load_current50_symbols,
)
from research.daytrade50_capital_efficient_selector_v2.features import (
    EXCLUDED_SELECTION_DATES,
    MARKETS,
    bare,
    execution_suitability,
    selection_calendar,
    snapshot_days,
)
from research.daytrade50_capital_efficient_selector_v2.gross_budget_control import load_budget
from research.daytrade50_capital_efficient_selector_v2.lineage import (
    WITNESS_SYMBOLS,
    download_daily_bars,
    jpx_sessions,
    lag_sessions,
    previous_jpx_session,
    universe_symbols,
)
from research.daytrade50_capital_efficient_selector_v2.policy import (
    QTY,
    canonical_leverage,
    derived_requested_gross_yen,
    paper_effective_g,
    policy_instance_sha,
    policy_spec_sha,
)
from research.daytrade50_capital_efficient_selector_v2.selector import FAIL_CLOSED, select_top50
from research.daytrade50_capital_efficient_selector_v2.validate import _quantile
from small_paper.v1r_activation_binding import manifest_content_sha

ROOT = Path(__file__).resolve().parents[3]
REPO = ROOT.parent
OUT = ROOT / "results" / "research" / "daytrade50_capital_efficient_selector_v2" / "target_blind_validation_cap500k_lev2x_g1m"
REPORTS = ROOT / "results" / "reports"
RUNTIME = ROOT / "runtime"
V16 = ROOT / "results" / "research" / "v1r_exit_v2_prospective_activation" / "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V16.json"
PINNED_V16_SHA = "49fb47c67a1da39a99edfba8b88d66c2e8009a07d6db94c269d6e8cdd05a2240"
EXPECTED_SPEC = "5a395155b26970b37bbb5030987db49ea9af035d09549b73922e589ef6c53ebe"
EXPECTED_INSTANCE = "8ff7da05e5e5991050048dce24c14882c7ff14e1814429efb07a0e246589c598"
STORE = RUNTIME / "system_gross_exposure_budget.json"

VALIDATED = "DAYTRADE50_V2_TARGET_BLIND_SELECTOR_VALIDATED"
MIXED = "DAYTRADE50_V2_TARGET_BLIND_SELECTOR_MIXED"
NOT_SUPPORTED = "DAYTRADE50_V2_TARGET_BLIND_SELECTOR_NOT_SUPPORTED"
MISMATCH = "CAPITAL_MODEL_IDENTITY_MISMATCH"


def read_identity() -> dict[str, Any]:
    view = load_budget(STORE)
    base = view.base_capital_yen
    leverage = view.max_leverage_multiplier
    derived = None
    instance = None
    if base is not None and leverage is not None:
        derived = derived_requested_gross_yen(base, leverage)
        instance = policy_instance_sha(base, leverage)
    spec = policy_spec_sha()
    match = (
        view.status == "STAGED"
        and base == 500_000
        and leverage == canonical_leverage("2.0")
        and view.g_yen == 1_000_000
        and derived == 1_000_000
        and paper_effective_g(derived) == 1_000_000
        and instance == view.policy_instance_sha == EXPECTED_INSTANCE
        and spec == EXPECTED_SPEC
        and view.policy_spec_sha == EXPECTED_SPEC
    )
    return {
        "match": match,
        "BASE_CAPITAL_YEN": base,
        "MAX_LEVERAGE_MULTIPLIER": 2.0 if leverage == canonical_leverage("2.0") else leverage,
        "stored_leverage": leverage,
        "SYSTEM_GROSS_EXPOSURE_BUDGET_YEN": view.g_yen,
        "derived_product": derived,
        "POLICY_SPEC_SHA": spec,
        "POLICY_INSTANCE_SHA": instance,
        "reader_status": view.status,
    }


def _market_map(reports_dir: Path) -> dict[str, tuple[str, str]]:
    meta: dict[str, tuple[str, str]] = {}
    for day in snapshot_days(reports_dir):
        path = reports_dir / f"features_{day}.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                symbol = bare(raw.get("symbol"))
                if not symbol:
                    continue
                meta[symbol] = (
                    str(raw.get("exchange") or "1"),
                    str(raw.get("market") or "prime").strip().lower(),
                )
    return meta


def _series(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    dates = [str(row["source_bar_date"]) for row in rows]
    return {
        "dates": dates,
        "index": {day: pos for pos, day in enumerate(dates)},
        "close": [float(row["close"]) for row in rows],
        "high": [float(row["high"]) for row in rows],
        "low": [float(row["low"]) for row in rows],
        "volume": [float(row["volume"]) for row in rows],
    }


def _feature(symbol: str, series: Mapping[str, Any], required: str, exchange: str, market: str) -> Optional[dict[str, Any]]:
    index = series["index"].get(required)
    if index is None or index < 19:
        return None
    if exchange != "1" or market not in MARKETS:
        return None
    start = index - 19
    close = series["close"]
    high = series["high"]
    low = series["low"]
    volume = series["volume"]
    ranges: list[float] = []
    trading: list[float] = []
    for pos in range(start, index + 1):
        price = close[pos]
        if price <= 0:
            return None
        span = (high[pos] - low[pos]) / price * 100.0
        if span < 0:
            return None
        ranges.append(span)
        trading.append(volume[pos] * price)
    latest_close = close[index]
    suitability = execution_suitability(latest_close)
    tv5 = float(statistics.median(trading[-5:]))
    tv20 = float(statistics.median(trading))
    range5 = float(statistics.median(ranges[-5:]))
    range20 = float(statistics.median(ranges))
    if min(tv5, tv20) <= 0 or suitability is None:
        return None
    return {
        "symbol": symbol,
        "eligible": True,
        "prior_close": latest_close,
        "liquidity_feature": min(tv5, tv20),
        "range_pct_5d_median": range5,
        "range_pct_20d_median": range20,
        "suitability_feature": suitability,
        "tick_ratio": -suitability,
        "tv_5d_median": tv5,
        "tv_20d_median": tv20,
        "SOURCE_BAR_DATE": required,
        "WINDOW_END_SESSION": required,
        "WINDOW_START_SESSION": series["dates"][start],
        "OBSERVATION_N": 20,
    }


def _target_metrics(series: Mapping[str, Any], day: str) -> Optional[dict[str, float]]:
    index = series["index"].get(day)
    if index is None:
        return None
    price = series["close"][index]
    if price <= 0:
        return None
    volume = series["volume"][index]
    return {
        "range_pct": (series["high"][index] - series["low"][index]) / price * 100.0,
        "trading_value": volume * price,
    }


def _book_metrics(
    symbols: Sequence[str],
    notionals: Mapping[str, float],
    suitability: Mapping[str, float],
    targets: Mapping[str, Mapping[str, Any]],
    gross: float,
) -> dict[str, Any]:
    ranges: list[float] = []
    trading: list[float] = []
    ticks: list[float] = []
    missing = 0
    dead = 0
    eligible = 0
    for symbol in symbols:
        target = targets.get(symbol)
        if target is None or float(target["trading_value"]) <= 0:
            dead += 1
            if target is None:
                missing += 1
        else:
            trading.append(float(target["trading_value"]))
            ranges.append(float(target["range_pct"]))
        if symbol in suitability:
            ticks.append(float(suitability[symbol]))
        if symbol in notionals and float(notionals[symbol]) <= gross:
            eligible += 1
    count = len(list(symbols))
    return {
        "selected_n": count,
        "coverage_50": count == 50,
        "range_median": _quantile(ranges, 0.5),
        "range_p25": _quantile(ranges, 0.25),
        "range_p75": _quantile(ranges, 0.75),
        "trading_value_median": _quantile(trading, 0.5),
        "active_rate": ((count - dead) / count) if count else None,
        "dead_rate": (dead / count) if count else None,
        "target_missing_n": missing,
        "tick_suitability_median": _quantile(ticks, 0.5),
        "one_lot_eligibility_rate": (eligible / count) if count else None,
    }


def _auto50(prior_rows: Sequence[Mapping[str, Any]]) -> list[str]:
    ranked: list[tuple[float, str]] = []
    for row in prior_rows:
        close = float(row["close"])
        trading_value = float(row["trading_value"])
        if close < MIN_CLOSE or trading_value <= 0:
            continue
        ratio = (float(row["tick_yen"]) / close) * 100.0
        if ratio > MAX_TICK_RATIO:
            continue
        score = auto50_v1_score(float(row["range_pct"]), trading_value)
        if score is None:
            continue
        ranked.append((float(score), str(row["symbol"])))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [symbol for _score, symbol in ranked[:50]]


def _median(values: Sequence[Optional[float]]) -> Optional[float]:
    usable = [float(value) for value in values if value is not None]
    if not usable:
        return None
    return float(statistics.median(usable))


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    usable = [float(value) for value in values if value is not None]
    if not usable:
        return None
    return sum(usable) / len(usable)


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "days": len(rows),
        "range_median": _median([row.get("range_median") for row in rows]),
        "range_p25": _median([row.get("range_p25") for row in rows]),
        "range_p75": _median([row.get("range_p75") for row in rows]),
        "trading_value_median": _median([row.get("trading_value_median") for row in rows]),
        "active_rate": _mean([row.get("active_rate") for row in rows]),
        "dead_rate": _mean([row.get("dead_rate") for row in rows]),
        "tick_suitability_median": _median([row.get("tick_suitability_median") for row in rows]),
        "one_lot_eligibility_rate": _mean([row.get("one_lot_eligibility_rate") for row in rows]),
    }


def _not_worse(left: Optional[float], right: Optional[float], *, higher: bool) -> Optional[bool]:
    if left is None or right is None:
        return None
    if higher:
        return left + 1e-9 >= right
    return left <= right + 1e-9


def _score_pair(v2: Mapping[str, Any], other: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        ("range_median", True),
        ("trading_value_median", True),
        ("active_rate", True),
        ("dead_rate", False),
        ("tick_suitability_median", True),
        ("one_lot_eligibility_rate", True),
    )
    losses = 0
    comparable = 0
    detail = {}
    for name, higher in fields:
        flag = _not_worse(v2.get(name), other.get(name), higher=higher)
        detail[name] = flag
        if flag is None:
            continue
        comparable += 1
        if not flag:
            losses += 1
    return {"comparable": comparable, "losses": losses, "detail": detail}


def classify(summary: Mapping[str, Any]) -> str:
    if not summary["identity_match"]:
        return MISMATCH
    if summary["lag_not_zero"] or summary["future_used"] or summary["future_conditioned_selection"]:
        return NOT_SUPPORTED
    if summary["fail_closed_days"]:
        return NOT_SUPPORTED
    auto = summary["versus_auto50"]
    current = summary["versus_current50"]
    if auto["comparable"] and auto["losses"] >= 4:
        return NOT_SUPPORTED
    if current["days"] >= 10 and current["losses"] >= 4:
        return NOT_SUPPORTED
    if auto["losses"] or (current["days"] >= 10 and current["losses"]):
        return MIXED
    return VALIDATED


def _bucket(shares: Sequence[float]) -> dict[str, int]:
    counts = {"le_25": 0, "25_50": 0, "50_75": 0, "75_100": 0}
    for share in shares:
        if share <= 0.25:
            counts["le_25"] += 1
        elif share <= 0.50:
            counts["25_50"] += 1
        elif share <= 0.75:
            counts["50_75"] += 1
        else:
            counts["75_100"] += 1
    return counts


def _traded_session_dates(start: date, end: date) -> set[str]:
    """JPX dates with a witness bar. Close may still be unpublished."""
    import yfinance as yf

    symbols = ["1301.T", "7203.T"]
    try:
        frame = yf.download(
            symbols,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception:
        return set()
    if frame is None or getattr(frame, "empty", True):
        return set()
    found: set[str] = set()
    for symbol in symbols:
        try:
            view = frame[symbol] if hasattr(frame.columns, "levels") else frame
            view = view.dropna(how="all")
        except Exception:
            continue
        for idx, row in view.iterrows():
            try:
                high = float(row["High"])
                low = float(row["Low"])
                volume = float(row["Volume"])
            except (TypeError, ValueError):
                continue
            if high == high and low == low and volume == volume and volume > 0:
                found.add(idx.date().strftime("%Y%m%d"))
    return found


def run() -> dict[str, Any]:
    identity = read_identity()
    body = json.loads(V16.read_text(encoding="utf-8")) if V16.is_file() else {}
    v16_sha = manifest_content_sha(body) if body else ""
    if not identity["match"]:
        return {
            "verdict": MISMATCH,
            "next": "STOP_BEFORE_VALIDATION",
            "identity": identity,
            "v16_sha": v16_sha,
            "v16_changed": v16_sha != PINNED_V16_SHA,
            "runtime_applied": False,
            "score_changed": False,
            "submit_cancel_live": "0/0/0",
        }
    gross = float(identity["SYSTEM_GROSS_EXPOSURE_BUDGET_YEN"])
    meta = _market_map(REPORTS)
    symbols = [f"{symbol}.T" for symbol in universe_symbols(REPORTS)]
    panel = download_daily_bars(symbols, date(2026, 4, 1), date(2026, 10, 8))
    witness = {symbol: panel.get(symbol) or [] for symbol in ("1301", "7203")}
    close_sessions = jpx_sessions(
        {
            symbol: [(row["source_bar_date"], float(row["close"]), float(row["volume"])) for row in rows]
            for symbol, rows in witness.items()
        }
    )
    feature_days = set(snapshot_days(REPORTS))
    traded = _traded_session_dates(date(2026, 4, 1), date(2026, 10, 9)) & feature_days
    sessions = sorted(set(close_sessions) | traded)
    close_unavailable = sorted(traded - set(close_sessions))
    series = {symbol: _series(rows) for symbol, rows in panel.items() if rows}
    from research.auto50_research_capture.mode import tick_yen

    calendar = [day for day in selection_calendar(REPORTS) if day in set(sessions)]
    freshness_days = [day for day in snapshot_days(REPORTS) if previous_jpx_session(sessions, day)]
    day_rows: list[dict[str, Any]] = []
    v2_metrics: list[dict[str, Any]] = []
    v2_by_day: dict[str, dict[str, Any]] = {}
    auto_by_day: dict[str, dict[str, Any]] = {}
    auto_metrics: list[dict[str, Any]] = []
    current_metrics: list[dict[str, Any]] = []
    eligible_pop: list[int] = []
    ineligible_pop: list[int] = []
    notionals: list[float] = []
    shares: list[float] = []
    fit_counts = {k: 0 for k in range(1, 6)}
    latest_rows: list[dict[str, Any]] = []
    latest_day = calendar[-1] if calendar else ""
    future_conditioned = 0
    for target in calendar:
        required = previous_jpx_session(sessions, target)
        if required is None:
            continue
        features: list[dict[str, Any]] = []
        prior_for_auto: list[dict[str, Any]] = []
        target_by_symbol: dict[str, dict[str, float]] = {}
        notion_by_symbol: dict[str, float] = {}
        suit_by_symbol: dict[str, float] = {}
        for symbol, item in series.items():
            exchange, market = meta.get(symbol, ("1", "prime"))
            built = _feature(symbol, item, required, exchange, market)
            if built is not None:
                if built["SOURCE_BAR_DATE"] != required or built["WINDOW_END_SESSION"] >= target:
                    future_conditioned += 1
                features.append(built)
                notion_by_symbol[symbol] = round(float(built["prior_close"]) * QTY, 2)
                suit_by_symbol[symbol] = float(built["suitability_feature"])
            index = item["index"].get(required)
            if index is not None and exchange == "1" and market in MARKETS:
                price = item["close"][index]
                span = (item["high"][index] - item["low"][index]) / price * 100.0 if price > 0 else -1
                trading_value = item["volume"][index] * price
                prior_for_auto.append(
                    {
                        "symbol": symbol,
                        "close": price,
                        "range_pct": span,
                        "trading_value": trading_value,
                        "tick_yen": tick_yen(price),
                    }
                )
                notion_by_symbol.setdefault(symbol, round(price * QTY, 2))
                suitability = execution_suitability(price)
                if suitability is not None:
                    suit_by_symbol.setdefault(symbol, suitability)
            metrics = _target_metrics(item, target)
            if metrics is not None:
                target_by_symbol[symbol] = metrics
        chosen = select_top50(features, gross_budget_yen=gross)
        history_ok = len(features)
        budget_rejected = int((chosen.get("rejected") or {}).get("budget") or 0)
        eligible_n = history_ok - budget_rejected
        eligible_pop.append(eligible_n)
        ineligible_pop.append(budget_rejected)
        status = str(chosen["status"])
        symbols_out = list(chosen["symbols"])
        if status != "TOP50":
            symbols_out = []
        for row in chosen.get("rows") or []:
            if row.get("SOURCE_BAR_DATE") != required:
                future_conditioned += 1
        book = _book_metrics(symbols_out, notion_by_symbol, suit_by_symbol, target_by_symbol, gross)
        metrics_joined = target not in set(close_unavailable)
        if status == "TOP50":
            ordered = list(chosen["rows"])
            lots = [float(row["ONE_LOT_NOTIONAL"]) for row in ordered]
            notionals.extend(lots)
            shares.extend(float(row["BUDGET_SHARE"]) for row in ordered)
            running = 0.0
            for width in range(1, 6):
                running += lots[width - 1]
                if running <= gross:
                    fit_counts[width] += 1
            if metrics_joined:
                v2_metrics.append(book)
                v2_by_day[target] = book
            if target == latest_day:
                latest_rows = ordered
        if metrics_joined:
            auto_symbols = _auto50(prior_for_auto)
            auto_book = _book_metrics(auto_symbols, notion_by_symbol, suit_by_symbol, target_by_symbol, gross)
            auto_metrics.append(auto_book)
            auto_by_day[target] = auto_book
            current_path = RUNTIME / f"same_day_am_frozen_universe_{target}.json"
            current_symbols = load_current50_symbols(current_path) if current_path.is_file() else None
            if current_symbols:
                current_metrics.append(
                    _book_metrics(current_symbols[:50], notion_by_symbol, suit_by_symbol, target_by_symbol, gross)
                )
        day_rows.append(
            {
                "target_trading_date": target,
                "required_previous_jpx_session": required,
                "actual_latest_source_bar_date": required,
                "lag_sessions": lag_sessions(sessions, target, required),
                "future_used": False,
                "status": status if status == "TOP50" else FAIL_CLOSED,
                "eligible_n": eligible_n,
                "ineligible_n": budget_rejected,
                "selected_n": len(symbols_out),
                "target_metrics_joined": metrics_joined,
            }
        )
    freshness = []
    for day in freshness_days:
        required = previous_jpx_session(sessions, day)
        freshness.append(
            {
                "target_trading_date": day,
                "required_previous_jpx_session": required,
                "lag_sessions": lag_sessions(sessions, day, required),
                "future_used": False,
                "evaluated": day in set(calendar),
                "excluded_from_fit": day in EXCLUDED_SELECTION_DATES,
            }
        )
    v2_all = _aggregate(v2_metrics)
    auto_all = _aggregate(auto_metrics)
    current_all = _aggregate(current_metrics)
    versus_auto = _score_pair(v2_all, auto_all)
    current_days = [
        row["target_trading_date"]
        for row in day_rows
        if row["status"] == "TOP50"
        and row["target_metrics_joined"]
        and (RUNTIME / f"same_day_am_frozen_universe_{row['target_trading_date']}.json").is_file()
    ]
    v2_on_current = _aggregate([v2_by_day[day] for day in current_days])
    auto_on_current = _aggregate([auto_by_day[day] for day in current_days])
    versus_current = _score_pair(v2_on_current, current_all)
    versus_current["days"] = len(current_days)
    versus_auto["days"] = len(auto_metrics)
    success_days = sum(1 for row in day_rows if row["status"] == "TOP50")
    fail_days = len(day_rows) - success_days
    summary = {
        "identity_match": True,
        "lag_not_zero": sum(1 for row in freshness if row["lag_sessions"] != 0),
        "future_used": sum(1 for row in freshness if row["future_used"]),
        "future_conditioned_selection": future_conditioned,
        "fail_closed_days": fail_days,
        "versus_auto50": versus_auto,
        "versus_current50": versus_current,
    }
    verdict = classify(summary)
    if verdict == VALIDATED:
        nxt = "FREEZE_DAYTRADE50_SELECTOR_V2_CAPITAL500K_LEVERAGE2X_G1M_AND_PREPARE_FUTURE_EXACT_BOARD_CAPTURE"
    elif verdict == MIXED:
        nxt = "SEPARATE_WEAK_DAYTRADE_MECHANISM_IN_A_LATER_PHASE"
    else:
        nxt = "DO_NOT_RETUNE_SEPARATE_THE_WEAK_MECHANISM"
    fit_rate = {str(k): (fit_counts[k] / success_days if success_days else None) for k in range(1, 6)}
    return {
        "verdict": verdict,
        "next": nxt,
        "identity": identity,
        "identity_parity": True,
        "score_changed": False,
        "future_conditioned_selection": future_conditioned,
        "runtime_applied": False,
        "paper_changed": False,
        "v16_changed": v16_sha != PINNED_V16_SHA,
        "v16_sha": v16_sha,
        "submit_cancel_live": "0/0/0",
        "qty": QTY,
        "max_concurrent": 5,
        "per_position_g_over_5": None,
        "target_date_count": len(day_rows),
        "freshness_date_count": len(freshness),
        "lag_sessions_zero": sum(1 for row in freshness if row["lag_sessions"] == 0),
        "future_used": 0,
        "top50_success_days": success_days,
        "top50_fail_closed_days": fail_days,
        "target_close_unavailable_dates": close_unavailable,
        "target_metric_days": len(v2_metrics),
        "budget_eligible_population": {
            "min": min(eligible_pop) if eligible_pop else None,
            "median": _quantile(eligible_pop, 0.5),
            "max": max(eligible_pop) if eligible_pop else None,
        },
        "budget_ineligible_population": {
            "min": min(ineligible_pop) if ineligible_pop else None,
            "median": _quantile(ineligible_pop, 0.5),
            "max": max(ineligible_pop) if ineligible_pop else None,
        },
        "one_lot_notional": {
            "min": min(notionals) if notionals else None,
            "p10": _quantile(notionals, 0.10),
            "median": _quantile(notionals, 0.50),
            "p90": _quantile(notionals, 0.90),
            "max": max(notionals) if notionals else None,
        },
        "budget_share": {
            "min": min(shares) if shares else None,
            "p10": _quantile(shares, 0.10),
            "median": _quantile(shares, 0.50),
            "p90": _quantile(shares, 0.90),
            "max": max(shares) if shares else None,
            "buckets": _bucket(shares),
        },
        "cumulative_rank_fit_rate": fit_rate,
        "comparison": {
            "DAYTRADE50_V2": v2_all,
            "AUTO50_V1": auto_all,
            "CURRENT50": current_all,
            "DAYTRADE50_V2_ON_CURRENT50_DAYS": v2_on_current,
            "AUTO50_V1_ON_CURRENT50_DAYS": auto_on_current,
            "versus_auto50": versus_auto,
            "versus_current50": versus_current,
        },
        "latest_target_trading_date": latest_day,
        "latest_top50": [
            {
                "rank": row["rank"],
                "symbol": row["symbol"],
                "PRIMARY_QUALITY_SCORE": row["PRIMARY_QUALITY_SCORE"],
                "ONE_LOT_NOTIONAL": row["ONE_LOT_NOTIONAL"],
                "BUDGET_SHARE": row["BUDGET_SHARE"],
                "tv_5d_median": row["tv_5d_median"],
                "tv_20d_median": row["tv_20d_median"],
                "LIQUIDITY_RANK": row["LIQUIDITY_RANK"],
                "range_pct_5d_median": row["range_pct_5d_median"],
                "OPPORTUNITY_5D_RANK": row["OPPORTUNITY_5D_RANK"],
                "range_pct_20d_median": row["range_pct_20d_median"],
                "OPPORTUNITY_20D_RANK": row["OPPORTUNITY_20D_RANK"],
                "suitability_feature": row["suitability_feature"],
                "EXECUTION_SUITABILITY_RANK": row["EXECUTION_SUITABILITY_RANK"],
            }
            for row in latest_rows
        ],
        "days": day_rows,
        "excluded_fit_dates": sorted(EXCLUDED_SELECTION_DATES),
    }


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _book_line(name: str, row: Mapping[str, Any]) -> str:
    return (
        f"{name}: days={row.get('days')} selected_coverage={row.get('days')} "
        f"range_median={_fmt(row.get('range_median'))} "
        f"range_p25={_fmt(row.get('range_p25'))} "
        f"range_p75={_fmt(row.get('range_p75'))} "
        f"trading_value_median={_fmt(row.get('trading_value_median'))} "
        f"active_rate={_fmt(row.get('active_rate'))} "
        f"dead_rate={_fmt(row.get('dead_rate'))} "
        f"tick_suitability_median={_fmt(row.get('tick_suitability_median'))} "
        f"one_lot_eligibility_rate={_fmt(row.get('one_lot_eligibility_rate'))}"
    )


def _markdown(report: Mapping[str, Any]) -> str:
    identity = report["identity"]
    comparison = report.get("comparison") or {}
    notion = report.get("one_lot_notional") or {}
    share = report.get("budget_share") or {}
    buckets = share.get("buckets") or {}
    eligible = report.get("budget_eligible_population") or {}
    ineligible = report.get("budget_ineligible_population") or {}
    fit = report.get("cumulative_rank_fit_rate") or {}
    lines = [
        f"VERDICT: {report['verdict']}",
        "",
        f"NEXT: {report['next']}",
        "",
        f"BASE_CAPITAL_YEN: {identity.get('BASE_CAPITAL_YEN')}",
        "",
        f"MAX_LEVERAGE_MULTIPLIER: {identity.get('MAX_LEVERAGE_MULTIPLIER')}",
        "",
        f"SYSTEM_GROSS_EXPOSURE_BUDGET_YEN: {identity.get('SYSTEM_GROSS_EXPOSURE_BUDGET_YEN')}",
        "",
        f"POLICY_SPEC_SHA: {identity.get('POLICY_SPEC_SHA')}",
        "",
        f"POLICY_INSTANCE_SHA: {identity.get('POLICY_INSTANCE_SHA')}",
        "",
        "Discord/research identity parity: true" if report.get("identity_parity") else "Discord/research identity parity: false",
        "",
        f"target date count: {report.get('target_date_count')}",
        "",
        f"freshness date count: {report.get('freshness_date_count')}",
        "",
        f"lag_sessions=0: {report.get('lag_sessions_zero')} / {report.get('freshness_date_count')}",
        "",
        f"future_used: {report.get('future_used')}",
        "",
        f"Top50 success days: {report.get('top50_success_days')}",
        "",
        f"Top50 fail-closed days: {report.get('top50_fail_closed_days')}",
        "",
        f"target metric days: {report.get('target_metric_days')}",
        "",
        f"target close unavailable dates: {report.get('target_close_unavailable_dates')}",
        "",
        f"budget eligible population min/median/max: {_fmt(eligible.get('min'))} / {_fmt(eligible.get('median'))} / {_fmt(eligible.get('max'))}",
        "",
        f"budget ineligible population min/median/max: {_fmt(ineligible.get('min'))} / {_fmt(ineligible.get('median'))} / {_fmt(ineligible.get('max'))}",
        "",
        "V2 ONE_LOT_NOTIONAL min/p10/median/p90/max: "
        f"{_fmt(notion.get('min'))} / {_fmt(notion.get('p10'))} / {_fmt(notion.get('median'))} / {_fmt(notion.get('p90'))} / {_fmt(notion.get('max'))}",
        "",
        "V2 BUDGET_SHARE min/p10/median/p90/max: "
        f"{_fmt(share.get('min'))} / {_fmt(share.get('p10'))} / {_fmt(share.get('median'))} / {_fmt(share.get('p90'))} / {_fmt(share.get('max'))}",
        "",
        f"BUDGET_SHARE buckets <=25/25-50/50-75/75-100: {buckets.get('le_25')} / {buckets.get('25_50')} / {buckets.get('50_75')} / {buckets.get('75_100')}",
        "",
        "rank-order cumulative fit 1/2/3/4/5: "
        f"{_fmt(fit.get('1'))} / {_fmt(fit.get('2'))} / {_fmt(fit.get('3'))} / {_fmt(fit.get('4'))} / {_fmt(fit.get('5'))}",
        "",
        "CURRENT50 vs AUTO50 vs V2 は同一target日のjoin後指標。CURRENT50は凍結ファイルがある日だけ。",
        "",
        _book_line("DAYTRADE50_V2", comparison.get("DAYTRADE50_V2") or {}),
        "",
        _book_line("AUTO50_V1", comparison.get("AUTO50_V1") or {}),
        "",
        _book_line("CURRENT50", comparison.get("CURRENT50") or {}),
        "",
        _book_line("DAYTRADE50_V2_ON_CURRENT50_DAYS", comparison.get("DAYTRADE50_V2_ON_CURRENT50_DAYS") or {}),
        "",
        f"versus AUTO50 losses: {(comparison.get('versus_auto50') or {}).get('losses')} detail={(comparison.get('versus_auto50') or {}).get('detail')}",
        "",
        f"versus CURRENT50 losses: {(comparison.get('versus_current50') or {}).get('losses')} days={(comparison.get('versus_current50') or {}).get('days')} detail={(comparison.get('versus_current50') or {}).get('detail')}",
        "",
        f"latest target date: {report.get('latest_target_trading_date')}",
        "",
        "latest V2 Top50:",
        "",
    ]
    for row in report.get("latest_top50") or []:
        lines.append(
            f"{row.get('rank')} {row.get('symbol')} score={_fmt(row.get('PRIMARY_QUALITY_SCORE'))} "
            f"one_lot={_fmt(row.get('ONE_LOT_NOTIONAL'))} share={_fmt(row.get('BUDGET_SHARE'))} "
            f"tv5={_fmt(row.get('tv_5d_median'))} tv20={_fmt(row.get('tv_20d_median'))} liq={_fmt(row.get('LIQUIDITY_RANK'))} "
            f"range5={_fmt(row.get('range_pct_5d_median'))} opp5={_fmt(row.get('OPPORTUNITY_5D_RANK'))} "
            f"range20={_fmt(row.get('range_pct_20d_median'))} opp20={_fmt(row.get('OPPORTUNITY_20D_RANK'))} "
            f"suit={_fmt(row.get('suitability_feature'))} suit_rank={_fmt(row.get('EXECUTION_SUITABILITY_RANK'))}"
        )
    lines.extend(
        [
            "",
            f"score changed: {report.get('score_changed')}",
            "",
            f"future-conditioned selection: {report.get('future_conditioned_selection')}",
            "",
            f"CURRENT V16 changed: {report.get('v16_changed')}",
            "",
            f"V16 sha: {report.get('v16_sha')}",
            "",
            "runtime applied: false",
            "",
            "submit/cancel/live: 0/0/0",
            "",
            "Complete Strategy PnL は評価していません。板のAsk/Bidは使っていません。",
            "",
            "weight、threshold、feature、capital、leverage は結果を見て変更していません。",
            "",
        ]
    )
    return "\n".join(lines)


def _xlsx(report: Mapping[str, Any], path: Path) -> None:
    book = Workbook()
    identity = book.active
    identity.title = "identity"
    identity.append(["field", "value"])
    for key, value in report["identity"].items():
        identity.append([key, json.dumps(value) if isinstance(value, (dict, list)) else value])
    days = book.create_sheet("days")
    day_rows = report.get("days") or []
    if day_rows:
        days.append(list(day_rows[0].keys()))
        for row in day_rows:
            days.append([row[key] for key in day_rows[0].keys()])
    compare = book.create_sheet("compare")
    comparison = report.get("comparison") or {}
    compare.append(["book", "days", "range_median", "range_p25", "range_p75", "trading_value_median", "active_rate", "dead_rate", "tick_suitability_median", "one_lot_eligibility_rate"])
    for name in ("DAYTRADE50_V2", "AUTO50_V1", "CURRENT50", "DAYTRADE50_V2_ON_CURRENT50_DAYS", "AUTO50_V1_ON_CURRENT50_DAYS"):
        row = comparison.get(name) or {}
        compare.append([name, row.get("days"), row.get("range_median"), row.get("range_p25"), row.get("range_p75"), row.get("trading_value_median"), row.get("active_rate"), row.get("dead_rate"), row.get("tick_suitability_median"), row.get("one_lot_eligibility_rate")])
    capital = book.create_sheet("capital")
    capital.append(["field", "min", "p10", "median", "p90", "max"])
    notion = report.get("one_lot_notional") or {}
    share = report.get("budget_share") or {}
    capital.append(["ONE_LOT_NOTIONAL", notion.get("min"), notion.get("p10"), notion.get("median"), notion.get("p90"), notion.get("max")])
    capital.append(["BUDGET_SHARE", share.get("min"), share.get("p10"), share.get("median"), share.get("p90"), share.get("max")])
    eligible = report.get("budget_eligible_population") or {}
    ineligible = report.get("budget_ineligible_population") or {}
    capital.append(["budget_eligible_population", eligible.get("min"), None, eligible.get("median"), None, eligible.get("max")])
    capital.append(["budget_ineligible_population", ineligible.get("min"), None, ineligible.get("median"), None, ineligible.get("max")])
    buckets = share.get("buckets") or {}
    capital.append(["bucket_le_25", buckets.get("le_25")])
    capital.append(["bucket_25_50", buckets.get("25_50")])
    capital.append(["bucket_50_75", buckets.get("50_75")])
    capital.append(["bucket_75_100", buckets.get("75_100")])
    fit_sheet = book.create_sheet("rank_fit")
    fit_sheet.append(["names", "cumulative_fit_rate"])
    for width, rate in (report.get("cumulative_rank_fit_rate") or {}).items():
        fit_sheet.append([width, rate])
    latest = book.create_sheet("latest_top50")
    rows = report.get("latest_top50") or []
    if rows:
        latest.append(list(rows[0].keys()))
        for row in rows:
            latest.append([row[key] for key in rows[0].keys()])
    book.save(path)


def main() -> None:
    report = run()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "report.md").write_text(_markdown(report), encoding="utf-8")
    _xlsx(report, OUT / "audit.xlsx")
    print(report["verdict"])
    print(report["next"])


if __name__ == "__main__":
    main()
