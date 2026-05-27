"""
Phase 111: Decompose opening_dynamic50 vs static27 failure (review only).
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from universe.hero_backtest import (
    FOCUS_SYMBOLS,
    build_hero_definition,
    load_session_activity,
    load_static27,
    load_symbol_set_from_csv,
    top_n_by_metric,
)
from universe.opening_screen import (
    compute_opening_daytrade_scores,
    fetch_previous_day_yfinance,
    load_push_window_first_last,
    opening_features_from_push,
    rank_normalize,
    select_top50,
)

FAILURE_TAGS = (
    "previous_day_data_missing",
    "early_intraday_data_missing",
    "score_formula_not_sensitive",
    "yfinance_cap_sampling_bias",
    "hero_definition_mismatch",
    "static27_session_bias",
)

TARGET_DAYS = ("2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22")
FOCUS_DAY = "2026-05-21"
YFINANCE_CAP_DEFAULT = 600


def _norm(code: str) -> str:
    c = str(code).strip().upper().split("@")[0]
    return c if c.endswith(".T") else f"{c}.T"


def load_opening_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_opening_symbols(path: Path) -> set[str]:
    return {_norm(r.get("symbol") or "") for r in load_opening_csv_rows(path) if r.get("symbol")}


def master_index_map(all_symbols: Sequence[str]) -> dict[str, int]:
    return {s: i for i, s in enumerate(all_symbols)}


def in_yfinance_cap(symbol: str, cap: int, index_map: Mapping[str, int]) -> bool:
    idx = index_map.get(symbol)
    return idx is not None and idx < cap


def has_push_file(push_day_dir: Path, symbol: str) -> bool:
    stem = symbol.replace(".T", "")
    return (push_day_dir / f"{stem}.T.jsonl").is_file() or (push_day_dir / f"{symbol}.jsonl").is_file()


def hero_reasons_for_symbol(hero_def: Any, symbol: str) -> list[str]:
    reasons: list[str] = []
    for key, syms in (hero_def.hero_sources or {}).items():
        if symbol in syms:
            reasons.append(key)
    if symbol in hero_def.session_candidate_top20:
        reasons.append("session_candidate_top20")
    if symbol in hero_def.session_accepted_symbols:
        reasons.append("session_accepted")
    return reasons


@dataclass
class SymbolScoreDetail:
    symbol: str
    trade_date: str
    in_yfinance_cap: bool
    master_index: int
    yfinance_prev_fetched: bool
    has_push_jsonl: bool
    has_opening_push_0905: bool
    previous_day_vol_liq_score: Optional[float] = None
    early_momentum_score: Optional[float] = None
    early_trading_value_score: Optional[float] = None
    early_range_score: Optional[float] = None
    opening_daytrade_score: Optional[float] = None
    rank_among_scored: Optional[int] = None
    rank_total_pool: Optional[int] = None
    gap_to_top50_score: Optional[float] = None
    in_opening_dynamic50: bool = False
    in_static27: bool = False
    in_hero_top20: bool = False
    hero_reasons: list[str] = field(default_factory=list)
    failure_tags: list[str] = field(default_factory=list)


def score_components_for_symbol(
    symbol: str,
    *,
    prev_by_sym: Mapping[str, Any],
    opening_by_sym: Mapping[str, Any],
    all_symbols: Sequence[str],
) -> dict[str, Optional[float]]:
    from universe.opening_screen import PreviousDayFeatures, OpeningWindowFeatures

    prev_vl = {
        s: (prev_by_sym[s].volatility_liquidity_score if s in prev_by_sym else None) for s in all_symbols
    }
    mom = {
        s: (opening_by_sym[s].early_momentum_score if s in opening_by_sym else None) for s in all_symbols
    }
    tv = {s: (opening_by_sym[s].trading_value_proxy if s in opening_by_sym else None) for s in all_symbols}
    rng = {s: (opening_by_sym[s].range_pct if s in opening_by_sym else None) for s in all_symbols}
    n_prev = rank_normalize(prev_vl)
    n_mom = rank_normalize(mom)
    n_tv = rank_normalize(tv)
    n_rng = rank_normalize(rng)
    op = opening_by_sym.get(symbol)
    prev = prev_by_sym.get(symbol)
    return {
        "previous_day_vol_liq_score": prev.volatility_liquidity_score if prev else None,
        "rank_prev_vol_liq": n_prev.get(symbol),
        "early_momentum_score": op.early_momentum_score if op else None,
        "rank_early_momentum": n_mom.get(symbol),
        "early_trading_value_score": op.trading_value_proxy if op else None,
        "rank_early_tv": n_tv.get(symbol),
        "early_range_score": op.range_pct if op else None,
        "rank_early_range": n_rng.get(symbol),
    }


def analyze_focus_symbol(
    symbol: str,
    *,
    trade_date: date,
    all_symbols: Sequence[str],
    index_map: Mapping[str, int],
    yfinance_cap: int,
    push_day_dir: Path,
    opening_path: Path,
    static27: set[str],
    hero_def: Any,
    yfinance_extra: bool = True,
) -> SymbolScoreDetail:
    td = trade_date.isoformat()
    idx = index_map.get(symbol, -1)
    in_cap = in_yfinance_cap(symbol, yfinance_cap, index_map)
    has_push = has_push_file(push_day_dir, symbol)

    fetch_syms = list(all_symbols[:yfinance_cap])
    if yfinance_extra and symbol not in fetch_syms:
        fetch_syms.append(symbol)
    prev_by_sym = fetch_previous_day_yfinance(fetch_syms, trade_date)
    first, last = load_push_window_first_last(push_day_dir, cutoff_hhmm="09:05")
    opening_by_sym = {}
    for sym, payload in last.items():
        opening_by_sym[sym] = opening_features_from_push(
            sym, payload, checkpoint="09:05", first_payload=first.get(sym)
        )
    has_0905 = symbol in opening_by_sym

    scores = compute_opening_daytrade_scores(all_symbols, prev_by_sym, opening_by_sym)
    ranked = sorted(scores.keys(), key=lambda s: scores.get(s, 0.0), reverse=True)
    rank_pos = ranked.index(symbol) + 1 if symbol in ranked else None
    top50 = ranked[:50]
    top50_min = scores.get(top50[-1], 0.0) if len(top50) >= 50 else None
    sym_score = scores.get(symbol)

    opening50 = load_opening_symbols(opening_path)
    comps = score_components_for_symbol(
        symbol, prev_by_sym=prev_by_sym, opening_by_sym=opening_by_sym, all_symbols=all_symbols
    )

    tags: list[str] = []
    if not in_cap and symbol not in prev_by_sym:
        tags.append("yfinance_cap_sampling_bias")
        tags.append("previous_day_data_missing")
    elif symbol not in prev_by_sym:
        tags.append("previous_day_data_missing")
    if not has_push:
        tags.append("early_intraday_data_missing")
    if symbol in hero_def.hero_top20 and symbol not in opening50:
        tags.append("hero_definition_mismatch")
    if sym_score is not None and top50_min is not None and sym_score < top50_min:
        tags.append("score_formula_not_sensitive")
    if symbol in static27 and symbol not in opening50:
        tags.append("static27_session_bias")

    return SymbolScoreDetail(
        symbol=symbol,
        trade_date=td,
        in_yfinance_cap=in_cap,
        master_index=idx,
        yfinance_prev_fetched=symbol in prev_by_sym,
        has_push_jsonl=has_push,
        has_opening_push_0905=has_0905,
        previous_day_vol_liq_score=comps["previous_day_vol_liq_score"],
        early_momentum_score=comps["early_momentum_score"],
        early_trading_value_score=comps["early_trading_value_score"],
        early_range_score=comps["early_range_score"],
        opening_daytrade_score=sym_score,
        rank_among_scored=rank_pos,
        rank_total_pool=len(all_symbols),
        gap_to_top50_score=round((top50_min or 0) - (sym_score or 0), 6) if top50_min and sym_score is not None else None,
        in_opening_dynamic50=symbol in opening50,
        in_static27=symbol in static27,
        in_hero_top20=symbol in hero_def.hero_top20,
        hero_reasons=hero_reasons_for_symbol(hero_def, symbol),
        failure_tags=sorted(set(tags)),
    )


def build_20260521_session_rows(
    *,
    opening_path: Path,
    static27: set[str],
    session_dir: Path,
    hero_def: Any,
) -> list[dict[str, Any]]:
    opening50 = load_opening_symbols(opening_path)
    act = load_session_activity(session_dir)
    cand_top = set(act.get("candidate_top20") or [])
    acc = set(act.get("accepted_symbols") or [])
    hero20 = hero_def.hero_top20

    rows: list[dict[str, Any]] = []

    def base(sym: str, category: str) -> dict[str, Any]:
        return {
            "trade_date": FOCUS_DAY,
            "symbol": sym,
            "category": category,
            "in_opening50": sym in opening50,
            "in_static27": sym in static27,
            "in_candidate_top20": sym in cand_top,
            "in_accepted": sym in acc,
            "in_hero_top20": sym in hero20,
        }

    for sym in sorted(opening50 - cand_top - acc):
        if sym not in hero20:
            rows.append(base(sym, "opening_only_not_session"))
    for sym in sorted((cand_top | acc) - opening50):
        cat = "accepted_missing_opening" if sym in acc else "candidate_missing_opening"
        rows.append(base(sym, cat))
    for sym in sorted((static27 & hero20) - opening50):
        rows.append(base(sym, "static27_hero_active_not_opening"))
    for sym in sorted(static27 - opening50):
        if sym in acc or sym in cand_top:
            rows.append(base(sym, "static27_session_active_not_opening"))

    seen = {r["symbol"] + r["category"] for r in rows}
    for sym in sorted(opening50 & (cand_top | acc)):
        key = sym + "opening_and_session"
        if key not in seen:
            rows.append(base(sym, "opening_and_session"))

    return rows


def classify_day_failure(
    *,
    trade_date: str,
    opening_rows: list[dict[str, str]],
    static27: set[str],
    hero_def: Any,
    phase108_json: Optional[dict[str, Any]],
    yfinance_cap: int,
    index_map: Mapping[str, int],
    session_found: bool,
) -> dict[str, Any]:
    opening50 = {_norm(r["symbol"]) for r in opening_rows}
    overlap = len(static27 & opening50)
    push_cov_0905 = 0
    prev_cov = 0.0
    if phase108_json:
        dc = phase108_json.get("data_coverage") or {}
        push_cov_0905 = (dc.get("opening_push_coverage_by_checkpoint") or {}).get("09:05", 0)
        prev_cov = float(dc.get("previous_day_coverage_pct") or 0)

    tags: Counter[str] = Counter()
    if prev_cov < 0.5:
        tags["yfinance_cap_sampling_bias"] += 2
        tags["previous_day_data_missing"] += 1
    if push_cov_0905 < 10:
        tags["early_intraday_data_missing"] += 2
    if overlap <= 3 and session_found:
        tags["static27_session_bias"] += 2
    if overlap == 0:
        tags["yfinance_cap_sampling_bias"] += 1
        tags["static27_session_bias"] += 1

    accepted_in_static = sum(
        1 for s in hero_def.session_accepted_symbols if s in static27
    )
    accepted_in_open = sum(
        1 for s in hero_def.session_accepted_symbols if s in opening50
    )
    if session_found and accepted_in_open < accepted_in_static // 2:
        tags["static27_session_bias"] += 1

    tags["hero_definition_mismatch"] += 1
    tags["score_formula_not_sensitive"] += 1

    return {
        "trade_date": trade_date,
        "static27_opening_overlap": overlap,
        "push_0905_coverage": push_cov_0905,
        "previous_day_coverage_pct": prev_cov,
        "accepted_in_static27": accepted_in_static,
        "accepted_in_opening50": accepted_in_open,
        "failure_tag_counts": dict(tags),
        "primary_tags": [t for t, _ in tags.most_common(3)],
    }


def determine_verdict(
    breakdown_rows: list[dict[str, Any]],
    focus_3905: list[SymbolScoreDetail],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    tag_totals: Counter[str] = Counter()
    for row in breakdown_rows:
        for t, c in (row.get("failure_tag_counts") or {}).items():
            tag_totals[t] += int(c)

    data_tags = {
        "previous_day_data_missing",
        "early_intraday_data_missing",
        "yfinance_cap_sampling_bias",
    }
    formula_tags = {"score_formula_not_sensitive", "hero_definition_mismatch"}
    bias_tags = {"static27_session_bias"}

    data_score = sum(tag_totals[t] for t in data_tags)
    formula_score = sum(tag_totals[t] for t in formula_tags)
    bias_score = sum(tag_totals[t] for t in bias_tags)

    f3905 = [d for d in focus_3905 if d.symbol == "3905.T"]
    if f3905 and all("yfinance_cap_sampling_bias" in d.failure_tags for d in f3905):
        notes.append("3905.T outside yfinance cap (master index 897) all 4 days")

    focus_day = next((r for r in breakdown_rows if r["trade_date"] == FOCUS_DAY), {})
    if focus_day.get("static27_opening_overlap") == 0:
        notes.append("2026-05-21: zero overlap static27 vs opening50")

    if data_score >= formula_score + bias_score and data_score >= 4:
        return "failure_due_to_data_coverage", notes
    if bias_score > data_score and bias_score >= 3:
        return "failure_due_to_static_session_bias", notes
    if formula_score > data_score:
        return "failure_due_to_score_formula", notes
    return "mixed_failure", notes
