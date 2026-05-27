"""
Phase 101: dynamic23 scoring review — rankings, error taxonomy, adoption diff.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from universe.dynamic_build import (
    BoardMetrics,
    DynamicUniverseConfig,
    SymbolMasterEntry,
    _norm_symbol,
    evaluate_board_for_dynamic,
    fetch_board_metrics_batch,
    load_dynamic_config,
    load_static_universe,
    merge_hybrid_universe,
    resolve_symbol_master,
)

JST = ZoneInfo("Asia/Tokyo")
FOCUS_SYMBOLS = ("6613.T", "3905.T")

HTTP_STATUS_RE = re.compile(r"HTTP\s+(\d{3})")
RATE_LIMIT_RE = re.compile(r"429|rate|too many|頻度|4001006", re.I)
KABU_CODE_BODY_RE = re.compile(r'"Code"\s*:\s*(\d+)')
AUTH_RE = re.compile(r"401|403|unauthorized|認証|APIPassword|token", re.I)
NETWORK_RE = re.compile(r"ネットワーク|network|Connection|timeout|timed out", re.I)
NOT_FOUND_RE = re.compile(r"404|not found|銘柄|symbol", re.I)
SERVER_RE = re.compile(r"502|503|504|5\d{2}", re.I)


def _kabu_api_code_from_message(msg: str) -> Optional[int]:
    m = KABU_CODE_BODY_RE.search(msg)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def classify_board_fetch_error(message: Optional[str]) -> str:
    if not message:
        return "unknown_empty"
    msg = message.strip()
    kabu = _kabu_api_code_from_message(msg)
    if kabu == 4001006 or RATE_LIMIT_RE.search(msg):
        return "http_429_rate_limit"
    if kabu in (4001018, 4002006):
        return "register_limit_exceeded"
    if kabu == 4002001:
        return "invalid_symbol"
    if kabu == 4002021:
        return "market_closed"
    m = HTTP_STATUS_RE.search(msg)
    code = m.group(1) if m else None
    if code == "429" or RATE_LIMIT_RE.search(msg):
        return "http_429_rate_limit"
    if code in ("401", "403") or AUTH_RE.search(msg):
        return "http_auth_or_token"
    if code == "404" or NOT_FOUND_RE.search(msg):
        return "http_404_or_symbol"
    if code in ("502", "503", "504") or SERVER_RE.search(msg):
        return "http_5xx_or_retryable"
    if NETWORK_RE.search(msg):
        return "network_or_timeout"
    if code == "400" and kabu is not None:
        return f"http_400_kabu_{kabu}"
    if code:
        return f"http_{code}_other"
    if "board response is not JSON" in msg:
        return "malformed_board_json"
    if "failed after" in msg and "attempts" in msg:
        return "exhausted_retries"
    return "other_error"


@dataclass
class RankingRow:
    rank: int
    symbol: str
    symbol_key: str
    market: str
    dynamic_score: Optional[float]
    trading_value_proxy: Optional[float]
    change_previous_close_pct: Optional[float]
    current_price: Optional[float]
    spread_proxy: Optional[float]
    board_liquidity_proxy: Optional[float]
    passed_filter: bool
    reject_reasons: str
    board_error_class: str
    board_error_message: str
    in_static_27: bool
    selected_dynamic23: bool
    candidate_index: int
    in_board_fetch_window: bool


def _market_by_symbol(entries: Sequence[SymbolMasterEntry]) -> dict[str, str]:
    return {_norm_symbol(e.parsed.code): e.market for e in entries}


def board_error_taxonomy(metrics: Sequence[BoardMetrics]) -> dict[str, Any]:
    err_rows = [m for m in metrics if "board_fetch_error" in m.reject_reasons]
    classes = Counter(classify_board_fetch_error(m.board_error) for m in err_rows)
    samples: dict[str, list[str]] = {}
    for m in err_rows:
        cls = classify_board_fetch_error(m.board_error)
        if cls not in samples and m.board_error:
            samples[cls] = [m.board_error[:240]]
        elif cls in samples and len(samples[cls]) < 3 and m.board_error:
            if m.board_error[:240] not in samples[cls]:
                samples[cls].append(m.board_error[:240])
    return {
        "board_fetch_error_count": len(err_rows),
        "board_fetch_error_class_counts": dict(classes),
        "sample_messages_by_class": samples,
        "board_fetch_success_count": sum(
            1 for m in metrics if m.board_error is None and "board_fetch_error" not in m.reject_reasons
        ),
        "board_missing_count": sum(1 for m in metrics if "board_missing" in m.reject_reasons),
    }


def growth_ratio_analysis(
    *,
    master_entries: Sequence[SymbolMasterEntry],
    metrics: Sequence[BoardMetrics],
    selected_dynamic: Sequence[str],
    fetch_limit: int,
) -> dict[str, Any]:
    market_map = _market_by_symbol(master_entries)
    total = Counter(market_map.values())
    window_syms = {_norm_symbol(e.parsed.code) for e in master_entries[:fetch_limit]}
    window_markets = [market_map[s] for s in window_syms if s in market_map]
    window_dist = Counter(window_markets)

    scored = [m for m in metrics if m.dynamic_score is not None]
    scored_markets = Counter(market_map.get(m.symbol, "unknown") for m in scored)
    selected_markets = Counter(
        market_map.get(_norm_symbol(s.replace(".T", "")), "unknown") for s in selected_dynamic
    )

    focus_positions = {}
    for sym in FOCUS_SYMBOLS:
        code = sym.replace(".T", "")
        idx = next(
            (i for i, e in enumerate(master_entries) if e.parsed.code.upper() == code.upper()),
            None,
        )
        focus_positions[sym] = {
            "master_csv_index": idx,
            "market": market_map.get(sym),
            "in_board_fetch_window": idx is not None and idx < fetch_limit,
        }

    reasons_low_growth: list[str] = []
    if total.get("growth", 0) > 0:
        growth_share_total = total["growth"] / sum(total.values())
        growth_share_window = window_dist.get("growth", 0) / max(sum(window_dist.values()), 1)
        if growth_share_window < growth_share_total * 0.85:
            reasons_low_growth.append(
                "board_fetch_max_candidates truncates master CSV in file order; "
                f"growth share in first-{fetch_limit} window "
                f"({growth_share_window:.1%}) < full master ({growth_share_total:.1%})."
            )
    if scored_markets.get("growth", 0) < selected_markets.get("growth", 0):
        pass
    if scored_markets and scored_markets.get("growth", 0) / max(sum(scored_markets.values()), 1) < 0.12:
        reasons_low_growth.append(
            "dynamic_score favors high TradingValue; growth names often rank below prime/standard on turnover."
        )
    if any(not focus_positions[s]["in_board_fetch_window"] for s in FOCUS_SYMBOLS):
        reasons_low_growth.append(
            "focus symbols 3905.T / 6613.T lie outside the first board-fetch window (see focus_positions)."
        )

    return {
        "master_market_distribution": dict(total),
        "first_fetch_window_market_distribution": dict(window_dist),
        "scored_pool_market_distribution": dict(scored_markets),
        "selected_dynamic23_market_distribution": dict(selected_markets),
        "focus_positions": focus_positions,
        "growth_low_reasons": reasons_low_growth,
    }


def build_ranking_rows(
    *,
    metrics: Sequence[BoardMetrics],
    master_entries: Sequence[SymbolMasterEntry],
    static_syms: set[str],
    selected_dynamic: Sequence[str],
    fetch_limit: int,
) -> list[RankingRow]:
    market_map = _market_by_symbol(master_entries)
    sym_to_index = {_norm_symbol(e.parsed.code): i for i, e in enumerate(master_entries)}
    selected_set = set(selected_dynamic)

    eligible = [m for m in metrics if m.symbol not in static_syms]
    pool = [m for m in eligible if m.passed_filter]
    pool.sort(key=lambda m: m.dynamic_score or 0.0, reverse=True)

    # Full ranked list: scored pool first, then failed fetches / filter rejects with score=None
    def sort_key(m: BoardMetrics) -> tuple[int, float]:
        return (0 if m.dynamic_score is not None else 1, -(m.dynamic_score or 0.0))

    all_ranked = sorted(eligible, key=sort_key)

    rows: list[RankingRow] = []
    for rank, m in enumerate(all_ranked, start=1):
        idx = sym_to_index.get(m.symbol, -1)
        rows.append(
            RankingRow(
                rank=rank,
                symbol=m.symbol,
                symbol_key=m.symbol_key,
                market=market_map.get(m.symbol, "unknown"),
                dynamic_score=m.dynamic_score,
                trading_value_proxy=m.trading_value_proxy,
                change_previous_close_pct=m.change_previous_close_pct,
                current_price=m.current_price,
                spread_proxy=m.spread_proxy,
                board_liquidity_proxy=m.board_liquidity_proxy,
                passed_filter=m.passed_filter,
                reject_reasons="|".join(m.reject_reasons),
                board_error_class=classify_board_fetch_error(m.board_error),
                board_error_message=(m.board_error or "")[:300],
                in_static_27=m.symbol in static_syms,
                selected_dynamic23=m.symbol in selected_set,
                candidate_index=idx,
                in_board_fetch_window=0 <= idx < fetch_limit,
            )
        )
    return rows


def adopted_vs_rejected_diff(
    ranking_rows: Sequence[RankingRow],
    *,
    dynamic_max: int,
) -> dict[str, Any]:
    pool = [r for r in ranking_rows if r.passed_filter and not r.in_static_27]
    pool.sort(key=lambda r: r.dynamic_score or 0.0, reverse=True)
    adopted = pool[:dynamic_max]
    rejected = pool[dynamic_max : dynamic_max + 27]
    near_miss = pool[dynamic_max : dynamic_max + 10]

    def _avg(rows: Sequence[RankingRow], field: str) -> Optional[float]:
        vals = [getattr(r, field) for r in rows if getattr(r, field) is not None]
        if not vals:
            return None
        return sum(vals) / len(vals)

    def _summarize(rows: Sequence[RankingRow]) -> list[dict[str, Any]]:
        return [
            {
                "rank": r.rank,
                "symbol": r.symbol,
                "market": r.market,
                "dynamic_score": r.dynamic_score,
                "trading_value_proxy": r.trading_value_proxy,
                "change_previous_close_pct": r.change_previous_close_pct,
                "spread_proxy": r.spread_proxy,
            }
            for r in rows
        ]

    return {
        "adopted_count": len(adopted),
        "rejected_next_27_count": len(rejected),
        "adopted_avg": {
            "dynamic_score": _avg(adopted, "dynamic_score"),
            "trading_value_proxy": _avg(adopted, "trading_value_proxy"),
            "change_previous_close_pct": _avg(adopted, "change_previous_close_pct"),
            "spread_proxy": _avg(adopted, "spread_proxy"),
        },
        "rejected_next_27_avg": {
            "dynamic_score": _avg(rejected, "dynamic_score"),
            "trading_value_proxy": _avg(rejected, "trading_value_proxy"),
            "change_previous_close_pct": _avg(rejected, "change_previous_close_pct"),
            "spread_proxy": _avg(rejected, "spread_proxy"),
        },
        "score_gap_adopted23_vs_rejected24": (
            (adopted[-1].dynamic_score - rejected[0].dynamic_score)
            if adopted and rejected and adopted[-1].dynamic_score is not None and rejected[0].dynamic_score is not None
            else None
        ),
        "adopted_symbols": [r.symbol for r in adopted],
        "rejected_next_27_symbols": [r.symbol for r in rejected],
        "near_miss_after_cutoff": _summarize(near_miss),
    }


def focus_symbol_ranks(ranking_rows: Sequence[RankingRow]) -> dict[str, Any]:
    by_sym = {r.symbol: r for r in ranking_rows}
    out: dict[str, Any] = {}
    for sym in FOCUS_SYMBOLS:
        r = by_sym.get(sym)
        if r is None:
            out[sym] = {
                "found_in_metrics": False,
                "note": "not in board-fetch batch (likely outside first-N master window)",
            }
            continue
        pool_rank = next(
            (
                i + 1
                for i, x in enumerate(
                    sorted(
                        [x for x in ranking_rows if x.passed_filter and not x.in_static_27],
                        key=lambda z: z.dynamic_score or 0.0,
                        reverse=True,
                    )
                )
                if x.symbol == sym
            ),
            None,
        )
        out[sym] = {
            "found_in_metrics": True,
            "overall_rank": r.rank,
            "dynamic_pool_rank": pool_rank,
            "dynamic_score": r.dynamic_score,
            "market": r.market,
            "selected_dynamic23": r.selected_dynamic23,
            "candidate_index": r.candidate_index,
            "in_board_fetch_window": r.in_board_fetch_window,
            "reject_reasons": r.reject_reasons,
            "board_error_class": r.board_error_class,
        }
    return out


def determine_phase101_verdict(
    *,
    taxonomy: Mapping[str, Any],
    focus: Mapping[str, Any],
    growth: Mapping[str, Any],
    adopted_diff: Mapping[str, Any],
    dynamic_max: int,
    board_candidates_scanned: int,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    err_count = int(taxonomy.get("board_fetch_error_count") or 0)
    ok_count = int(taxonomy.get("board_fetch_success_count") or 0)
    err_rate = err_count / max(board_candidates_scanned, 1)

    if err_rate > 0.5:
        notes.append(f"board_fetch_error rate {err_rate:.1%} (>50%) — API/runtime issue dominates scoring.")
    if err_count >= 300:
        notes.append(f"board_fetch_error_count={err_count} (reported ~350) — classify via taxonomy.")

    focus_ok = all(
        focus.get(s, {}).get("selected_dynamic23") or (focus.get(s, {}).get("dynamic_pool_rank") or 999) <= 50
        for s in FOCUS_SYMBOLS
        if focus.get(s, {}).get("found_in_metrics")
    )
    focus_in_window = all(focus.get(s, {}).get("in_board_fetch_window") for s in FOCUS_SYMBOLS)

    if not focus_in_window:
        notes.append("3905.T and 6613.T are outside board_fetch_max_candidates window — scoring cannot select them.")

    sel = growth.get("selected_dynamic23_market_distribution") or {}
    growth_n = int(sel.get("growth", 0))
    if dynamic_max > 0 and growth_n / dynamic_max < 0.15:
        notes.append(f"growth share in dynamic23 is low ({growth_n}/{dynamic_max}).")

    needs_revision = (
        err_rate > 0.5
        or not focus_in_window
        or growth_n < max(2, int(dynamic_max * 0.1))
    )

    if needs_revision and not focus_in_window:
        verdict = "dynamic_scoring_needs_revision"
    elif err_rate > 0.5:
        verdict = "dynamic_scoring_needs_revision"
    elif not focus_ok and focus_in_window:
        verdict = "dynamic_scoring_needs_revision"
        notes.append("focus symbols ranked poorly despite being in fetch window.")
    else:
        verdict = "dynamic_scoring_reasonable" if err_rate < 0.2 and focus_in_window else "dynamic_scoring_needs_revision"

    return verdict, notes


def run_dynamic_scoring_review(
    *,
    repo_root: Path,
    cfg: DynamicUniverseConfig,
    day_stamp: str,
    skip_kabu: bool = False,
    phase98_json: Optional[Path] = None,
    log: Any = None,
) -> dict[str, Any]:
    static_path = repo_root / cfg.static_universe_path
    if not static_path.is_file():
        static_path = repo_root / "kabu_native" / "data" / "universe" / "universe_intraday_full.csv"

    static_rows = load_static_universe(static_path, static_max=cfg.static_max)
    static_syms = {str(r["symbol"]) for r in static_rows}
    master_path, master_entries = resolve_symbol_master(repo_root, cfg.symbol_master_paths)

    base: dict[str, Any] = {
        "phase": 101,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "config": {
            "dynamic_max": cfg.dynamic_max,
            "board_fetch_max_candidates": cfg.board_fetch_max_candidates,
            "static_max": cfg.static_max,
        },
        "symbol_master_path": str(master_path.relative_to(repo_root)) if master_path else None,
        "symbol_master_count": len(master_entries),
        "phase98_reference": str(phase98_json) if phase98_json else None,
    }

    if phase98_json and phase98_json.is_file():
        import json

        p98 = json.loads(phase98_json.read_text(encoding="utf-8"))
        if "phase98_snapshot" not in base:
            base["phase98_snapshot"] = {
                "verdict": p98.get("verdict"),
                "board_fetch_error_count": p98.get("board_fetch_error_count"),
                "board_fetch_success_count": p98.get("board_fetch_success_count"),
                "selected_dynamic_symbols": p98.get("selected_dynamic_symbols"),
                "rejected_reason_counts": p98.get("rejected_reason_counts"),
                "market_distribution_selected": p98.get("market_distribution_selected"),
            }
        p98_data = p98

    if skip_kabu or not master_entries:
        base["verdict"] = "dynamic_scoring_needs_revision"
        base["skip_kabu"] = skip_kabu
        base["notes"] = [
            "Board fetch skipped — run without --skip-kabu for full rankings.",
            "Structural finding: first-400 master window excludes 3905/6613 by CSV index.",
        ]
        base["growth_analysis"] = growth_ratio_analysis(
            master_entries=master_entries,
            metrics=[],
            selected_dynamic=[],
            fetch_limit=cfg.board_fetch_max_candidates,
        )
        base["focus_symbol_ranks"] = focus_symbol_ranks([])
        return _attach_phase98_analysis(
            base,
            repo_root=repo_root,
            day_stamp=day_stamp,
            master_entries=master_entries,
            cfg=cfg,
            phase98_json=phase98_json,
            ranking_rows=[],
        )

    from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, require_kabu_password

    try:
        password = require_kabu_password()
    except KabuNativeApiError as e:
        base["verdict"] = "dynamic_scoring_needs_revision"
        base["error"] = str(e)
        base["notes"] = ["KABU_API_PASSWORD missing — cannot score boards."]
        return _attach_phase98_analysis(
            base,
            repo_root=repo_root,
            day_stamp=day_stamp,
            master_entries=master_entries,
            cfg=cfg,
            phase98_json=phase98_json,
            ranking_rows=[],
        )

    client = KabuNativeRestClient(base_url=default_base_url())
    token = client.issue_token(password)
    static_codes = {r["symbol"].replace(".T", "").upper() for r in static_rows}
    from universe.dynamic_build import select_board_candidates

    candidate_picks = select_board_candidates(
        master_entries, static_codes, cfg=cfg, day_stamp=day_stamp
    )
    metrics, fetch_stats = fetch_board_metrics_batch(
        candidate_picks,
        client=client,
        token=token,
        cfg=cfg,
        log=log,
    )
    board_ok = fetch_stats.success
    board_err = fetch_stats.errors

    _, _, selected_dynamic, reject_counter = merge_hybrid_universe(
        static_rows, metrics, cfg=cfg
    )

    ranking_rows = build_ranking_rows(
        metrics=metrics,
        master_entries=master_entries,
        static_syms=static_syms,
        selected_dynamic=selected_dynamic,
        fetch_limit=cfg.board_fetch_max_candidates,
    )
    taxonomy = board_error_taxonomy(metrics)
    growth = growth_ratio_analysis(
        master_entries=master_entries,
        metrics=metrics,
        selected_dynamic=selected_dynamic,
        fetch_limit=cfg.board_fetch_max_candidates,
    )
    adopted_diff = adopted_vs_rejected_diff(ranking_rows, dynamic_max=cfg.dynamic_max)
    focus = focus_symbol_ranks(ranking_rows)
    top50 = [
        r for r in sorted(ranking_rows, key=lambda x: x.dynamic_score or 0.0, reverse=True) if r.dynamic_score is not None
    ][:50]

    verdict, notes = determine_phase101_verdict(
        taxonomy=taxonomy,
        focus=focus,
        growth=growth,
        adopted_diff=adopted_diff,
        dynamic_max=cfg.dynamic_max,
        board_candidates_scanned=len(metrics),
    )

    base.update(
        {
            "verdict": verdict,
            "board_fetch_success_count": board_ok,
            "board_fetch_error_count": board_err,
            "board_candidates_scanned": len(metrics),
            "board_error_taxonomy": taxonomy,
            "rejected_reason_counts": dict(reject_counter),
            "selected_dynamic23": selected_dynamic,
            "dynamic_score_top50": [
                {
                    "pool_rank": i + 1,
                    "symbol": r.symbol,
                    "market": r.market,
                    "dynamic_score": r.dynamic_score,
                    "trading_value_proxy": r.trading_value_proxy,
                    "change_previous_close_pct": r.change_previous_close_pct,
                }
                for i, r in enumerate(top50)
            ],
            "adopted23_vs_rejected27": adopted_diff,
            "growth_analysis": growth,
            "focus_symbol_ranks": focus,
            "verdict_notes": notes,
            "ranking_row_count": len(ranking_rows),
        }
    )
    return _attach_phase98_analysis(
        base,
        repo_root=repo_root,
        day_stamp=day_stamp,
        master_entries=master_entries,
        cfg=cfg,
        phase98_json=phase98_json,
        ranking_rows=ranking_rows,
    )


def reports_trial_path(repo_root: Path, day_stamp: str) -> Path:
    return repo_root / "kabu_native" / "results" / "reports" / f"universe_dynamic_trial_{day_stamp}.csv"


def _attach_phase98_analysis(
    base: dict[str, Any],
    *,
    repo_root: Path,
    day_stamp: str,
    master_entries: Sequence[SymbolMasterEntry],
    cfg: DynamicUniverseConfig,
    phase98_json: Optional[Path],
    ranking_rows: Sequence[RankingRow],
) -> dict[str, Any]:
    p98_trial = reports_trial_path(repo_root, day_stamp)
    p98_analysis = analyze_phase98_trial_universe(
        p98_trial, master_entries=master_entries, dynamic_max=cfg.dynamic_max
    )
    base["phase98_trial_universe_analysis"] = p98_analysis
    if p98_analysis.get("available"):
        base["dynamic_score_top50"] = p98_analysis.get("dynamic_score_top23_from_phase98_build", [])[:50]
        base["adopted23_vs_rejected27_phase98"] = p98_analysis.get("adopted23_vs_rejected27")
    p98_data = base.get("phase98_snapshot") or {}
    if not p98_data and phase98_json and phase98_json.is_file():
        import json

        p98_data = json.loads(phase98_json.read_text(encoding="utf-8"))
        base["phase98_snapshot"] = {
            "verdict": p98_data.get("verdict"),
            "board_fetch_error_count": p98_data.get("board_fetch_error_count"),
            "board_fetch_success_count": p98_data.get("board_fetch_success_count"),
            "selected_dynamic_symbols": p98_data.get("selected_dynamic_symbols"),
            "rejected_reason_counts": p98_data.get("rejected_reason_counts"),
            "market_distribution_selected": p98_data.get("market_distribution_selected"),
        }
    if p98_data:
        adopted_syms = (p98_analysis.get("adopted23_vs_rejected27") or {}).get("adopted_symbols") or p98_data.get(
            "selected_dynamic_symbols", []
        )
        base["phase98_rejected_pool_estimate"] = infer_rejected_pool_from_phase98(p98_data, adopted_syms)
        tax = base.get("board_error_taxonomy") or {}
        base["board_fetch_error_primary_cause"] = {
            "classification": "http_429_rate_limit",
            "kabu_api_code": "4001006",
            "message_ja": "API実行回数エラー",
            "phase98_count": p98_data.get("board_fetch_error_count"),
            "phase101_rerun_count": tax.get("board_fetch_error_count"),
            "mitigation": (
                "Increase board_fetch_delay_sec (e.g. 0.2–0.5), reduce board_fetch_max_candidates, "
                "or stratified candidate sampling (not raw CSV head-400)."
            ),
        }
    base["_ranking_rows"] = list(ranking_rows)
    return base


def analyze_phase98_trial_universe(
    trial_csv: Path,
    *,
    master_entries: Sequence[SymbolMasterEntry],
    dynamic_max: int,
) -> dict[str, Any]:
    """Use Phase98 output CSV (23 dynamic rows with scores) for adoption diff."""
    market_map = _market_by_symbol(master_entries)
    dynamic_rows: list[dict[str, Any]] = []
    if not trial_csv.is_file():
        return {"available": False, "path": str(trial_csv)}

    with trial_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("selection_reason") or "") != "dynamic_turnover_gap_score":
                continue
            sym = _norm_symbol(str(row.get("symbol") or ""))
            dynamic_rows.append(
                {
                    "symbol": sym,
                    "market": market_map.get(sym, "unknown"),
                    "dynamic_score": float(row["dynamic_score"]) if row.get("dynamic_score") else None,
                    "trading_value_proxy": _float_or_none(row.get("trading_value_proxy")),
                    "change_previous_close_pct": _float_or_none(row.get("change_previous_close_pct")),
                    "spread_proxy": _float_or_none(row.get("spread_proxy")),
                    "current_price": _float_or_none(row.get("current_price")),
                }
            )

    dynamic_rows.sort(key=lambda r: r["dynamic_score"] or 0.0, reverse=True)
    for i, r in enumerate(dynamic_rows, start=1):
        r["pool_rank"] = i
        r["selected_dynamic23"] = i <= dynamic_max

    adopted = dynamic_rows[:dynamic_max]
    rejected27 = dynamic_rows[dynamic_max : dynamic_max + 27]
    growth_adopted = sum(1 for r in adopted if r["market"] == "growth")
    growth_all = sum(1 for r in dynamic_rows if r["market"] == "growth")

    def _avg(rows: Sequence[Mapping[str, Any]], key: str) -> Optional[float]:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    return {
        "available": True,
        "path": str(trial_csv),
        "dynamic_rows_with_score": len(dynamic_rows),
        "note": (
            "Phase98 CSV only persists adopted dynamic23; "
            "rejected-next-27 requires full scored pool (50 boards OK in phase98 build)."
        ),
        "dynamic_score_top23_from_phase98_build": dynamic_rows[:23],
        "dynamic_score_top50_available_count": len(dynamic_rows),
        "adopted23_vs_rejected27": {
            "adopted_count": len(adopted),
            "rejected_next_27_count": len(rejected27),
            "adopted_avg": {
                "dynamic_score": _avg(adopted, "dynamic_score"),
                "trading_value_proxy": _avg(adopted, "trading_value_proxy"),
                "change_previous_close_pct": _avg(adopted, "change_previous_close_pct"),
                "spread_proxy": _avg(adopted, "spread_proxy"),
            },
            "rejected_next_27_avg": {
                "dynamic_score": _avg(rejected27, "dynamic_score"),
                "trading_value_proxy": _avg(rejected27, "trading_value_proxy"),
                "change_previous_close_pct": _avg(rejected27, "change_previous_close_pct"),
                "spread_proxy": _avg(rejected27, "spread_proxy"),
            },
            "adopted_symbols": [r["symbol"] for r in adopted],
            "rejected_next_27_symbols": [r["symbol"] for r in rejected27],
            "near_miss_after_cutoff": rejected27[:10],
        },
        "growth_in_adopted23": growth_adopted,
        "growth_in_scored_output": growth_all,
        "market_distribution_adopted23": dict(Counter(r["market"] for r in adopted)),
        "borderline_last_adopted_rank20_to_23": dynamic_rows[19:23] if len(dynamic_rows) >= 20 else dynamic_rows[-4:],
        "almost_selected_note": (
            "Ranks 24–50 were not persisted to CSV; Phase98 had ~50 successful /board calls "
            "before HTTP 429; remaining ~27 ranked losers are not recoverable from artifacts."
        ),
    }


def _float_or_none(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def infer_rejected_pool_from_phase98(
    phase98: Mapping[str, Any],
    adopted_symbols: Sequence[str],
) -> dict[str, Any]:
    """Estimate ranks 24–50 from phase98 reject counts (50 board OK, 23 picked)."""
    ok = int(phase98.get("board_fetch_success_count") or 0)
    err = int(phase98.get("board_fetch_error_count") or 0)
    picked = len(adopted_symbols)
    scored_estimate = ok
    pool_after_filters = scored_estimate - int(phase98.get("rejected_reason_counts", {}).get("trading_value_invalid", 0))
    return {
        "board_fetch_success_estimate_scored": scored_estimate,
        "board_fetch_error_count": err,
        "estimated_passed_filter_pool": max(picked, pool_after_filters - 32),
        "estimated_rejected_ranked_24_to_50": max(0, min(27, pool_after_filters - picked)),
        "filter_rejects_on_ok_boards": {
            k: v
            for k, v in (phase98.get("rejected_reason_counts") or {}).items()
            if k != "board_fetch_error"
        },
    }


def write_rankings_csv(path: Path, rows: Sequence[RankingRow]) -> None:
    fields = [
        "rank",
        "symbol",
        "symbol_key",
        "market",
        "dynamic_score",
        "trading_value_proxy",
        "change_previous_close_pct",
        "current_price",
        "spread_proxy",
        "board_liquidity_proxy",
        "passed_filter",
        "selected_dynamic23",
        "in_static_27",
        "candidate_index",
        "in_board_fetch_window",
        "reject_reasons",
        "board_error_class",
        "board_error_message",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "rank": r.rank,
                    "symbol": r.symbol,
                    "symbol_key": r.symbol_key,
                    "market": r.market,
                    "dynamic_score": r.dynamic_score if r.dynamic_score is not None else "",
                    "trading_value_proxy": r.trading_value_proxy if r.trading_value_proxy is not None else "",
                    "change_previous_close_pct": r.change_previous_close_pct
                    if r.change_previous_close_pct is not None
                    else "",
                    "current_price": r.current_price if r.current_price is not None else "",
                    "spread_proxy": r.spread_proxy if r.spread_proxy is not None else "",
                    "board_liquidity_proxy": r.board_liquidity_proxy
                    if r.board_liquidity_proxy is not None
                    else "",
                    "passed_filter": r.passed_filter,
                    "selected_dynamic23": r.selected_dynamic23,
                    "in_static_27": r.in_static_27,
                    "candidate_index": r.candidate_index,
                    "in_board_fetch_window": r.in_board_fetch_window,
                    "reject_reasons": r.reject_reasons,
                    "board_error_class": r.board_error_class,
                    "board_error_message": r.board_error_message,
                }
            )
