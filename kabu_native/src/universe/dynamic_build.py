"""
Hybrid static + dynamic universe builder (shadow / dry-run only).
"""

from __future__ import annotations

import csv
import json
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

HTTP_STATUS_RE = re.compile(r"HTTP\s+(\d{3})")
RATE_LIMIT_RE = re.compile(r"429|rate|too many|頻度|4001006", re.I)
KABU_CODE_BODY_RE = re.compile(r'"Code"\s*:\s*(\d+)')
KABU_BOARD_REGISTER_LIMIT = 50
BOARD_MODES = ("none", "validate", "score")

from universe.filters import calc_spread_bps, is_etf_board
from universe.symbols import ParsedSymbol, parse_symbol

SUPERVISORY_NAME_RE = re.compile(r"監理|整理|注意|停止|上場廃止")
PUSH_LIMIT_DEFAULT = 50
STATIC_MAX_DEFAULT = 27
DYNAMIC_MAX_DEFAULT = 23
TRADABLE_MARKETS = ("prime", "standard", "growth")
FOCUS_DIAGNOSTIC_SYMBOLS = ("6613.T", "3905.T")

CSV_FIELDS = (
    "symbol",
    "exchange",
    "symbol_key",
    "symbol_name",
    "passed",
    "selection_reason",
    "dynamic_score",
    "trading_value_proxy",
    "change_previous_close_pct",
    "current_price",
    "board_liquidity_proxy",
    "spread_proxy",
    "exclude_reasons",
)

TRIAL_CSV_FIELDS = (
    "symbol",
    "symbol_key",
    "exchange",
    "passed",
    "source_bucket",
    "selected_reason",
    "sampling_method",
    "market",
    "dynamic_score",
    "board_validated",
    "board_error_class",
    "symbol_name",
    "market_position",
    "market_position_pct",
    "sampling_bucket",
)


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
    if kabu == 4002005:
        return "unsupported_code"
    m = HTTP_STATUS_RE.search(msg)
    code = m.group(1) if m else None
    if code == "429":
        return "http_429_rate_limit"
    if code in ("401", "403"):
        return "http_auth_or_token"
    if code == "404":
        return "http_404_or_symbol"
    if code in ("502", "503", "504"):
        return "http_5xx_or_retryable"
    if "ネットワーク" in msg or "timeout" in msg.lower():
        return "network_or_timeout"
    if code == "400" and kabu is not None:
        return f"http_400_kabu_{kabu}"
    if code:
        return f"http_{code}_other"
    return "other_error"


@dataclass
class SymbolMasterEntry:
    parsed: ParsedSymbol
    market: str = "unknown"
    master_index: int = -1


@dataclass
class BoardCandidatePick:
    entry: SymbolMasterEntry
    candidate_rank: int
    selection_source: str
    sample_seed: str
    market: str
    market_position: int = 0
    market_size: int = 0
    market_position_pct: float = 0.0
    sampling_method: str = ""
    sampling_bucket: int = 0
    selected_by_rule: str = ""


@dataclass
class BoardFetchStats:
    success: int = 0
    errors: int = 0
    rate_limit_count: int = 0
    backoff_count: int = 0
    aborted_early: bool = False
    abort_reason: Optional[str] = None
    error_class_counts: Counter[str] = field(default_factory=Counter)
    fetched_count: int = 0


@dataclass
class DynamicUniverseConfig:
    static_universe_path: str = "kabu_native/data/universe/universe_intraday_full.csv"
    symbol_master_path: str = "data/jpx/tradable_symbols.csv"
    symbol_master_paths: list[str] = field(
        default_factory=lambda: ["data/jpx/tradable_symbols.csv"]
    )
    static_max: int = STATIC_MAX_DEFAULT
    dynamic_max: int = DYNAMIC_MAX_DEFAULT
    push_limit: int = PUSH_LIMIT_DEFAULT
    default_exchange: int = 1
    min_current_price: float = 100.0
    max_spread_bps: float = 80.0
    min_board_liquidity_qty: float = 1.0
    exclude_etf: bool = True
    # tradable = Prime+Standard+Growth ordinary (master pre-filtered); no market score bias
    market_filter: str = "tradable"
    score_log_trading_value_weight: float = 1.0
    score_change_pct_weight: float = 0.5
    score_liquidity_bonus_weight: float = 0.3
    score_spread_penalty_weight: float = 0.05
    # Phase105: board-free dynamic23 + register-limit-aware board (max 50)
    board_mode: str = "none"
    candidate_sampling_mode: str = "hybrid_static_plus_dynamic"
    dynamic_prime_quota: int = 8
    dynamic_standard_quota: int = 8
    dynamic_growth_quota: int = 7
    # Phase102/103 bulk candidate budget (legacy diagnostics only)
    candidate_prime_quota: int = 145
    candidate_standard_quota: int = 145
    candidate_growth_quota: int = 110
    candidate_total_max: int = 400
    sample_seed: Optional[str] = None
    board_fetch_delay_sec: float = 0.25
    rate_limit_backoff_sec: float = 2.0
    max_board_fetch_per_run: int = KABU_BOARD_REGISTER_LIMIT
    max_consecutive_rate_limits: int = 15
    kabu_board_register_limit: int = KABU_BOARD_REGISTER_LIMIT
    # Deprecated alias — ignored when stratified sampling enabled
    board_fetch_max_candidates: int = KABU_BOARD_REGISTER_LIMIT


@dataclass
class BoardMetrics:
    symbol: str
    exchange: int
    symbol_key: str
    symbol_name: Optional[str]
    current_price: Optional[float]
    trading_value_proxy: Optional[float]
    change_previous_close_pct: Optional[float]
    board_liquidity_proxy: Optional[float]
    spread_proxy: Optional[float]
    dynamic_score: Optional[float]
    passed_filter: bool
    reject_reasons: list[str]
    board_error: Optional[str] = None


def load_dynamic_config(path: Path) -> DynamicUniverseConfig:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"config must be a mapping: {path}")

    master_path = str(
        raw.get("symbol_master_path")
        or raw.get("symbol_master_default")
        or "data/jpx/tradable_symbols.csv"
    )
    master_paths = raw.get("symbol_master_paths")
    if master_paths:
        paths = [str(p) for p in master_paths]
    else:
        paths = [master_path]
    return DynamicUniverseConfig(
        static_universe_path=str(raw.get("static_universe_path", DynamicUniverseConfig.static_universe_path)),
        symbol_master_path=master_path,
        symbol_master_paths=paths,
        static_max=int(raw.get("static_max", STATIC_MAX_DEFAULT)),
        dynamic_max=int(raw.get("dynamic_max", DYNAMIC_MAX_DEFAULT)),
        push_limit=int(raw.get("push_limit", PUSH_LIMIT_DEFAULT)),
        default_exchange=int(raw.get("default_exchange", 1)),
        min_current_price=float(raw.get("min_current_price", 100)),
        max_spread_bps=float(raw.get("max_spread_bps", 80)),
        min_board_liquidity_qty=float(raw.get("min_board_liquidity_qty", 1)),
        exclude_etf=bool(raw.get("exclude_etf", True)),
        market_filter=str(raw.get("market_filter", raw.get("market", "tradable"))),
        score_log_trading_value_weight=float(raw.get("score_log_trading_value_weight", 1.0)),
        score_change_pct_weight=float(raw.get("score_change_pct_weight", 0.5)),
        score_liquidity_bonus_weight=float(raw.get("score_liquidity_bonus_weight", 0.3)),
        score_spread_penalty_weight=float(raw.get("score_spread_penalty_weight", 0.05)),
        board_mode=str(raw.get("board_mode", "none")),
        candidate_sampling_mode=str(
            raw.get("candidate_sampling_mode", "hybrid_static_plus_dynamic")
        ),
        dynamic_prime_quota=int(raw.get("dynamic_prime_quota", 8)),
        dynamic_standard_quota=int(raw.get("dynamic_standard_quota", 8)),
        dynamic_growth_quota=int(raw.get("dynamic_growth_quota", 7)),
        candidate_prime_quota=int(raw.get("candidate_prime_quota", 145)),
        candidate_standard_quota=int(raw.get("candidate_standard_quota", 145)),
        candidate_growth_quota=int(raw.get("candidate_growth_quota", 110)),
        candidate_total_max=int(
            raw.get("candidate_total_max", raw.get("board_fetch_max_candidates", 400))
        ),
        sample_seed=str(raw["sample_seed"]) if raw.get("sample_seed") else None,
        board_fetch_delay_sec=float(raw.get("board_fetch_delay_sec", 0.25)),
        rate_limit_backoff_sec=float(raw.get("rate_limit_backoff_sec", 2.0)),
        max_board_fetch_per_run=int(
            raw.get(
                "max_board_fetch_per_run",
                raw.get("board_fetch_max_candidates", KABU_BOARD_REGISTER_LIMIT),
            )
        ),
        max_consecutive_rate_limits=int(raw.get("max_consecutive_rate_limits", 15)),
        kabu_board_register_limit=int(raw.get("kabu_board_register_limit", KABU_BOARD_REGISTER_LIMIT)),
        board_fetch_max_candidates=int(
            raw.get("board_fetch_max_candidates", KABU_BOARD_REGISTER_LIMIT)
        ),
    )


def _norm_symbol(code: str) -> str:
    c = code.strip().upper().split("@")[0].replace(".T", "")
    return f"{c}.T" if c else ""


def load_static_universe(path: Path, *, static_max: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "").strip()
            if not sym:
                continue
            code = sym.split("@")[0].replace(".T", "")
            ex = int(row.get("exchange") or 1)
            rows.append(
                {
                    "symbol": _norm_symbol(code),
                    "exchange": ex,
                    "symbol_key": str(row.get("symbol_key") or f"{code}@{ex}"),
                    "symbol_name": str(row.get("symbol_name") or ""),
                    "passed": "True",
                    "selection_reason": "static_intraday_full",
                    "dynamic_score": "",
                    "trading_value_proxy": "",
                    "change_previous_close_pct": "",
                    "current_price": "",
                    "board_liquidity_proxy": "",
                    "spread_proxy": "",
                    "exclude_reasons": "",
                }
            )
    return rows[:static_max]


def resolve_symbol_master(
    repo_root: Path,
    paths: Sequence[str],
) -> tuple[Optional[Path], list[SymbolMasterEntry]]:
    for rel in paths:
        p = repo_root / rel if not Path(rel).is_absolute() else Path(rel)
        if not p.is_file():
            continue
        parsed = _load_master_csv(p)
        if parsed:
            return p, parsed
    return None, []


def _load_master_csv(path: Path) -> list[SymbolMasterEntry]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []
        fields = {c.strip().lower() for c in reader.fieldnames}
        sym_col = next(
            (c for c in reader.fieldnames if c.strip().lower() in ("symbol", "code", "銘柄コード")),
            "symbol" if "symbol" in fields else None,
        )
        if sym_col is None:
            sym_col = reader.fieldnames[0]
        ex_col = next(
            (c for c in reader.fieldnames if c.strip().lower() in ("exchange", "市場コード")),
            None,
        )
        market_col = next(
            (c for c in reader.fieldnames if c.strip().lower() == "market"),
            None,
        )
        active_col = next(
            (c for c in reader.fieldnames if c.strip().lower() == "is_active"),
            None,
        )
        out: list[SymbolMasterEntry] = []
        seen: set[tuple[str, int]] = set()
        for master_index, row in enumerate(reader):
            if active_col and str(row.get(active_col) or "").strip().lower() == "false":
                continue
            raw = str(row.get(sym_col) or "").strip()
            if not raw:
                continue
            try:
                ex = int(row.get(ex_col) or 1) if ex_col else 1
                parsed = parse_symbol(raw if "@" in raw else f"{raw}@{ex}", default_exchange=ex)
            except ValueError:
                continue
            key = (parsed.code, parsed.exchange)
            if key in seen:
                continue
            seen.add(key)
            market = str(row.get(market_col) or "unknown").strip().lower() if market_col else "unknown"
            out.append(
                SymbolMasterEntry(parsed=parsed, market=market, master_index=master_index)
            )
        return out


def market_quotas(cfg: DynamicUniverseConfig) -> dict[str, int]:
    return {
        "prime": cfg.candidate_prime_quota,
        "standard": cfg.candidate_standard_quota,
        "growth": cfg.candidate_growth_quota,
    }


def dynamic_market_quotas(cfg: DynamicUniverseConfig) -> dict[str, int]:
    """Per-market quotas for dynamic23 sampling (Phase105)."""
    quotas = {
        "prime": cfg.dynamic_prime_quota,
        "standard": cfg.dynamic_standard_quota,
        "growth": cfg.dynamic_growth_quota,
    }
    total = sum(quotas.values())
    if total == cfg.dynamic_max:
        return quotas
    if total <= 0:
        per = max(1, cfg.dynamic_max // len(TRADABLE_MARKETS))
        return {m: per for m in TRADABLE_MARKETS}
    scale = cfg.dynamic_max / total
    scaled = {m: max(0, int(round(quotas[m] * scale))) for m in TRADABLE_MARKETS}
    while sum(scaled.values()) < cfg.dynamic_max:
        for m in TRADABLE_MARKETS:
            if sum(scaled.values()) >= cfg.dynamic_max:
                break
            scaled[m] += 1
    while sum(scaled.values()) > cfg.dynamic_max:
        for m in reversed(TRADABLE_MARKETS):
            if sum(scaled.values()) <= cfg.dynamic_max:
                break
            if scaled[m] > 0:
                scaled[m] -= 1
    return scaled


def _phase105_sampling_mode(cfg: DynamicUniverseConfig) -> bool:
    mode = cfg.candidate_sampling_mode
    return mode in (
        "hybrid_static_plus_dynamic",
        "market_stratified_stride",
    ) or cfg.board_mode in BOARD_MODES


def _market_pools(
    master_entries: Sequence[SymbolMasterEntry],
    static_codes: set[str],
) -> dict[str, list[SymbolMasterEntry]]:
    static_upper = {c.upper() for c in static_codes}
    by_market: dict[str, list[SymbolMasterEntry]] = {m: [] for m in TRADABLE_MARKETS}
    for e in master_entries:
        if e.parsed.code.upper() in static_upper:
            continue
        m = e.market if e.market in TRADABLE_MARKETS else "unknown"
        if m in by_market:
            by_market[m].append(e)
    return by_market


def _seed_int(seed: str) -> int:
    return int(seed) if str(seed).isdigit() else hash(seed) % (10**9)


def stride_sample_positions(pool_size: int, quota: int, start: int) -> list[int]:
    """Evenly spaced indices with rotation — covers market list, not a single contiguous window."""
    if quota <= 0 or pool_size <= 0:
        return []
    if quota >= pool_size:
        return [(start + i) % pool_size for i in range(pool_size)]
    positions: list[int] = []
    seen: set[int] = set()
    for j in range(quota):
        # Midpoint of each equal arc segment (better than floor-only indexing)
        pos = (start + (j * pool_size + pool_size // (2 * quota)) // quota) % pool_size
        if pos in seen:
            step = max(1, pool_size // quota)
            pos = (pos + step) % pool_size
            while pos in seen and len(seen) < pool_size:
                pos = (pos + 1) % pool_size
        seen.add(pos)
        positions.append(pos)
    return positions


def hybrid_stride_positions(pool_size: int, quota: int, start: int) -> list[int]:
    """
    Percentile anchors along market-local master order + multi-pass rotated stride.
    Avoids single-window bias and spreads across the full segment list.
    """
    if quota <= 0 or pool_size <= 0:
        return []
    anchor_pcts = [i * 0.05 for i in range(21)]
    n_anchors = min(len(anchor_pcts), max(4, quota // 3))
    stride_quota = max(0, quota - n_anchors)
    merged: list[int] = []
    seen: set[int] = set()

    for pct in anchor_pcts[:n_anchors]:
        anchor_pos = int(pct * (pool_size - 1))
        if anchor_pos not in seen:
            seen.add(anchor_pos)
            merged.append(anchor_pos)

    n_passes = min(4, max(1, stride_quota))
    base = stride_quota // n_passes if n_passes else 0
    rem = stride_quota % n_passes if n_passes else 0
    for p in range(n_passes):
        sub_q = base + (1 if p < rem else 0)
        if sub_q <= 0:
            continue
        offset = (start + p * pool_size // n_passes) % pool_size
        for pos in stride_sample_positions(pool_size, sub_q, offset):
            if pos not in seen:
                seen.add(pos)
                merged.append(pos)

    if len(merged) < quota:
        for pos in stride_sample_positions(pool_size, quota - len(merged), start):
            if pos not in seen:
                seen.add(pos)
                merged.append(pos)
            if len(merged) >= quota:
                break
    return merged[:quota]


def _sample_continuous_window(
    pool: list[SymbolMasterEntry],
    quota: int,
    start: int,
    *,
    market: str,
    seed: str,
    source: str,
    rule: str,
) -> list[BoardCandidatePick]:
    n = len(pool)
    picks: list[BoardCandidatePick] = []
    for j in range(min(quota, n)):
        pos = (start + j) % n
        entry = pool[pos]
        picks.append(
            _pick_from_pool_entry(
                entry=entry,
                market=market,
                market_position=pos,
                market_size=n,
                sample_seed=seed,
                selection_source=source,
                sampling_method=source,
                sampling_bucket=j,
                selected_by_rule=rule,
            )
        )
    return picks


def _pick_from_pool_entry(
    *,
    entry: SymbolMasterEntry,
    market: str,
    market_position: int,
    market_size: int,
    sample_seed: str,
    selection_source: str,
    sampling_method: str,
    sampling_bucket: int,
    selected_by_rule: str,
) -> BoardCandidatePick:
    pct = (market_position / (market_size - 1)) if market_size > 1 else 0.0
    return BoardCandidatePick(
        entry=entry,
        candidate_rank=0,
        selection_source=selection_source,
        sample_seed=sample_seed,
        market=market,
        market_position=market_position,
        market_size=market_size,
        market_position_pct=round(pct, 6),
        sampling_method=sampling_method,
        sampling_bucket=sampling_bucket,
        selected_by_rule=selected_by_rule,
    )


def _sample_hybrid_stride_plus_rotation(
    pool: list[SymbolMasterEntry],
    quota: int,
    start: int,
    *,
    market: str,
    seed: str,
) -> list[BoardCandidatePick]:
    n = len(pool)
    positions = hybrid_stride_positions(n, quota, start)
    picks: list[BoardCandidatePick] = []
    for bucket, pos in enumerate(positions):
        picks.append(
            _pick_from_pool_entry(
                entry=pool[pos],
                market=market,
                market_position=pos,
                market_size=n,
                sample_seed=seed,
                selection_source="hybrid_stride_plus_rotation",
                sampling_method="hybrid_stride_plus_rotation",
                sampling_bucket=bucket,
                selected_by_rule="percentile_anchor_plus_multi_stride_rotation",
            )
        )
    return picks


def select_board_candidates(
    master_entries: Sequence[SymbolMasterEntry],
    static_codes: set[str],
    *,
    cfg: DynamicUniverseConfig,
    day_stamp: str,
) -> list[BoardCandidatePick]:
    """Market-stratified sampling. Head-N from master CSV is not used."""
    quotas = market_quotas(cfg)
    seed = cfg.sample_seed or day_stamp
    by_market = _market_pools(master_entries, static_codes)
    mode = cfg.candidate_sampling_mode
    seed_int = _seed_int(seed)

    picks: list[BoardCandidatePick] = []
    for market in TRADABLE_MARKETS:
        pool = by_market[market]
        quota = quotas.get(market, 0)
        if not pool or quota <= 0:
            continue
        n = len(pool)
        start = seed_int % n if n else 0

        if mode == "hybrid_stride_plus_rotation" or "stride" in mode:
            picks.extend(
                _sample_hybrid_stride_plus_rotation(
                    pool, quota, start, market=market, seed=seed
                )
            )
        elif mode == "multi_window_sampling":
            windows = 5
            per_window = max(1, quota // windows)
            for w in range(windows):
                win_start = (start + w * n // windows) % n
                source = "multi_window_sampling"
                picks.extend(
                    _sample_continuous_window(
                        pool,
                        per_window,
                        win_start,
                        market=market,
                        seed=seed,
                        source=source,
                        rule=f"multi_window_{w}",
                    )
                )
        else:
            source = (
                "rotating_market_stratified_sample"
                if "rotating" in mode
                else "market_stratified_sample"
            )
            picks.extend(
                _sample_continuous_window(
                    pool,
                    quota,
                    start,
                    market=market,
                    seed=seed,
                    source=source,
                    rule="single_continuous_window",
                )
            )

    cap = cfg.candidate_total_max
    if len(picks) > cap:
        picks = picks[:cap]
    max_fetch = cfg.max_board_fetch_per_run
    if len(picks) > max_fetch:
        picks = picks[:max_fetch]
    for i, p in enumerate(picks, start=1):
        p.candidate_rank = i
    return picks


def select_dynamic_sample_candidates(
    master_entries: Sequence[SymbolMasterEntry],
    static_codes: set[str],
    *,
    cfg: DynamicUniverseConfig,
    day_stamp: str,
) -> list[BoardCandidatePick]:
    """Board-free dynamic23: market-stratified stride (no bulk /board scoring)."""
    quotas = dynamic_market_quotas(cfg)
    seed = cfg.sample_seed or day_stamp
    by_market = _market_pools(master_entries, static_codes)
    mode = cfg.candidate_sampling_mode
    seed_int = _seed_int(seed)
    picks: list[BoardCandidatePick] = []

    for market in TRADABLE_MARKETS:
        pool = by_market[market]
        quota = quotas.get(market, 0)
        if not pool or quota <= 0:
            continue
        n = len(pool)
        start = seed_int % n if n else 0

        if mode == "hybrid_stride_plus_rotation":
            picks.extend(
                _sample_hybrid_stride_plus_rotation(
                    pool, quota, start, market=market, seed=seed
                )
            )
        else:
            positions = stride_sample_positions(n, quota, start)
            method = (
                "market_stratified_stride"
                if "stride" in mode or mode == "hybrid_static_plus_dynamic"
                else mode
            )
            for bucket, pos in enumerate(positions):
                picks.append(
                    _pick_from_pool_entry(
                        entry=pool[pos],
                        market=market,
                        market_position=pos,
                        market_size=n,
                        sample_seed=seed,
                        selection_source="hybrid_static_plus_dynamic",
                        sampling_method=method,
                        sampling_bucket=bucket,
                        selected_by_rule="market_stratified_stride",
                    )
                )

    if len(picks) > cfg.dynamic_max:
        picks = picks[: cfg.dynamic_max]
    for i, p in enumerate(picks, start=1):
        p.candidate_rank = i
    return picks


def candidate_dispersion_diagnostics(
    picks: Sequence[BoardCandidatePick],
) -> dict[str, Any]:
    by_market: dict[str, list[BoardCandidatePick]] = {m: [] for m in TRADABLE_MARKETS}
    for p in picks:
        if p.market in by_market:
            by_market[p.market].append(p)

    per_market: dict[str, Any] = {}
    for market in TRADABLE_MARKETS:
        mp = by_market[market]
        if not mp:
            continue
        pcts = [p.market_position_pct for p in mp]
        positions = [p.market_position for p in mp]
        size = mp[0].market_size
        span = (max(positions) - min(positions)) if positions else 0
        per_market[market] = {
            "count": len(mp),
            "market_size": size,
            "market_position_min": min(positions),
            "market_position_max": max(positions),
            "market_position_median": sorted(positions)[len(positions) // 2],
            "market_position_pct_min": min(pcts),
            "market_position_pct_max": max(pcts),
            "market_position_pct_median": sorted(pcts)[len(pcts) // 2],
            "candidate_market_position_coverage_pct": round(span / max(size - 1, 1), 4),
            "unique_positions": len(set(positions)),
        }

    all_pcts = [p.market_position_pct for p in picks if p.market in TRADABLE_MARKETS]
    return {
        "per_market": per_market,
        "candidate_market_position_min": min(all_pcts) if all_pcts else None,
        "candidate_market_position_max": max(all_pcts) if all_pcts else None,
        "candidate_market_position_median": sorted(all_pcts)[len(all_pcts) // 2] if all_pcts else None,
        "candidate_market_position_coverage_pct": {
            m: per_market[m]["candidate_market_position_coverage_pct"]
            for m in per_market
        },
    }


def focus_dynamic23_diagnostics(
    picks: Sequence[BoardCandidatePick],
    *,
    metrics: Optional[Sequence[BoardMetrics]] = None,
    board_validated: Optional[Mapping[str, bool]] = None,
) -> dict[str, Any]:
    """3905.T / 6613.T — diagnostic only, no hardcoded selection."""
    selected_syms = {_norm_symbol(p.entry.parsed.code) for p in picks}
    metric_by_sym = {m.symbol: m for m in metrics} if metrics else {}
    out: dict[str, Any] = {}
    for sym in FOCUS_DIAGNOSTIC_SYMBOLS:
        p = next((x for x in picks if _norm_symbol(x.entry.parsed.code) == sym), None)
        if p is None:
            out[sym] = {
                "in_dynamic_candidate_pool": False,
                "in_dynamic23": False,
                "not_selected_reason": "not_in_dynamic23_sample",
                "market_position_pct": None,
                "sampling_bucket": None,
                "selected_by_rule": None,
                "sampling_method": None,
                "market": None,
                "note": "diagnostic only; not hardcoded into universe",
            }
            continue
        m = metric_by_sym.get(sym)
        validated = (board_validated or {}).get(sym)
        out[sym] = {
            "in_dynamic_candidate_pool": True,
            "in_dynamic23": True,
            "not_selected_reason": None,
            "market": p.market,
            "market_position": p.market_position,
            "market_size": p.market_size,
            "market_position_pct": p.market_position_pct,
            "sampling_bucket": p.sampling_bucket,
            "selected_by_rule": p.selected_by_rule,
            "sampling_method": p.sampling_method,
            "candidate_rank": p.candidate_rank,
            "board_validated": validated,
            "board_error_class": classify_board_fetch_error(m.board_error) if m and m.board_error else None,
            "dynamic_score": m.dynamic_score if m else None,
            "note": "diagnostic only; not hardcoded into universe",
        }
    return out


def focus_sampling_diagnostics(picks: Sequence[BoardCandidatePick]) -> dict[str, Any]:
    by_sym = {_norm_symbol(p.entry.parsed.code): p for p in picks}
    out: dict[str, Any] = {}
    for sym in FOCUS_DIAGNOSTIC_SYMBOLS:
        p = by_sym.get(sym)
        if p is None:
            out[sym] = {
                "in_candidate_list": False,
                "market_position_pct": None,
                "sampling_bucket": None,
                "selected_by_rule": None,
                "sampling_method": None,
                "market_position": None,
                "market_size": None,
                "note": "diagnostic only; not used for selection",
            }
            continue
        out[sym] = {
            "in_candidate_list": True,
            "market": p.market,
            "market_position": p.market_position,
            "market_size": p.market_size,
            "market_position_pct": p.market_position_pct,
            "sampling_bucket": p.sampling_bucket,
            "selected_by_rule": p.selected_by_rule,
            "sampling_method": p.sampling_method,
            "candidate_rank": p.candidate_rank,
            "note": "diagnostic only; not used for selection",
        }
    return out


def determine_phase103_verdict(
    *,
    picks: Sequence[BoardCandidatePick],
    dispersion: Mapping[str, Any],
    focus_diag: Mapping[str, Any],
    cfg: DynamicUniverseConfig,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    focus_in = [s for s in FOCUS_DIAGNOSTIC_SYMBOLS if focus_diag.get(s, {}).get("in_candidate_list")]
    focus_all = len(focus_in) == len(FOCUS_DIAGNOSTIC_SYMBOLS)

    per_market = dispersion.get("per_market") or {}
    coverages = [
        float(v.get("candidate_market_position_coverage_pct") or 0)
        for v in per_market.values()
    ]
    min_coverage = min(coverages) if coverages else 0.0
    avg_coverage = sum(coverages) / len(coverages) if coverages else 0.0

    total_quota = cfg.candidate_total_max
    pool_sizes = [int(v.get("market_size") or 0) for v in per_market.values()]
    max_pool = max(pool_sizes) if pool_sizes else 0
    budget_ratio = total_quota / max(max_pool, 1)

    if focus_all and min_coverage >= 0.35:
        notes.append(
            f"focus symbols in candidates; min per-market position span coverage={min_coverage:.1%}"
        )
        return "sampling_revision_ready", notes

    if len(focus_in) >= 1 and min_coverage >= 0.25:
        notes.append(f"partial focus capture ({focus_in}); coverage={min_coverage:.1%}")
        return "sampling_ok_but_focus_missed", notes

    if not focus_all and min_coverage >= 0.35:
        notes.append("dispersion OK but focus symbols outside stride window for this seed")
        return "sampling_ok_but_focus_missed", notes

    if budget_ratio < 0.25 and min_coverage < 0.2:
        notes.append(
            f"candidate budget {total_quota} vs largest market {max_pool} — need wider sampling budget"
        )
        return "need_larger_candidate_budget", notes

    if min_coverage < 0.15:
        notes.append(f"low market position coverage ({min_coverage:.1%})")
        return "need_prefilter_data_source", notes

    notes.append(f"avg_coverage={avg_coverage:.1%} focus_in={focus_in}")
    return "sampling_ok_but_focus_missed", notes


def run_phase103_sampling_revision(
    *,
    repo_root: Path,
    cfg: DynamicUniverseConfig,
    day_stamp: str,
    reports_dir: Path,
    symbol_master_override: Optional[Path] = None,
) -> dict[str, Any]:
    """Sampling-only Phase103 — no kabu /board, no PF."""
    master_paths = cfg.symbol_master_paths
    if symbol_master_override is not None:
        master_paths = [str(symbol_master_override)]
    master_path, master_entries = resolve_symbol_master(repo_root, master_paths)
    static_path = repo_root / cfg.static_universe_path
    if not static_path.is_file():
        static_path = repo_root / "kabu_native" / "data" / "universe" / "universe_intraday_full.csv"
    static_rows = load_static_universe(static_path, static_max=cfg.static_max)
    static_codes = {r["symbol"].replace(".T", "").upper() for r in static_rows}

    picks: list[BoardCandidatePick] = []
    if master_entries:
        picks = select_board_candidates(
            master_entries, static_codes, cfg=cfg, day_stamp=day_stamp
        )

    dispersion = candidate_dispersion_diagnostics(picks)
    focus_diag = focus_sampling_diagnostics(picks)
    verdict, verdict_notes = determine_phase103_verdict(
        picks=picks,
        dispersion=dispersion,
        focus_diag=focus_diag,
        cfg=cfg,
    )

    candidates_csv = reports_dir / f"phase103_board_fetch_candidates_{day_stamp}.csv"
    json_path = reports_dir / f"phase103_sampling_revision_{day_stamp}.json"
    universe_csv = reports_dir / f"universe_dynamic_trial_{day_stamp}.csv"

    write_board_fetch_candidates_csv(candidates_csv, picks)
    write_universe_csv(universe_csv, static_rows[: cfg.push_limit])

    payload: dict[str, Any] = {
        "phase": 103,
        "day_stamp": day_stamp,
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "sampling_only": True,
        "board_fetch_skipped": True,
        "candidate_sampling_mode": cfg.candidate_sampling_mode,
        "candidate_quotas": market_quotas(cfg),
        "sample_seed": cfg.sample_seed or day_stamp,
        "candidate_count": len(picks),
        "candidate_market_distribution": dict(Counter(p.market for p in picks)),
        "dispersion_diagnostics": dispersion,
        "focus_diagnostics": focus_diag,
        "symbol_master_path": str(master_path.relative_to(repo_root)) if master_path else None,
        "symbol_master_count": len(master_entries),
        "static_count": len(static_rows),
        "output_candidates_csv": str(candidates_csv.relative_to(repo_root)),
        "output_json": str(json_path.relative_to(repo_root)),
        "output_universe_csv": str(universe_csv.relative_to(repo_root)),
        "constraints_confirmed": _constraints()
        + ["phase103_sampling_only_no_board_fetch"],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def optional_focus_diagnostics(
    picks: Sequence[BoardCandidatePick],
    metrics: Sequence[BoardMetrics],
    selected_dynamic: Sequence[str],
) -> dict[str, Any]:
    base = focus_sampling_diagnostics(picks)
    metric_by_sym = {m.symbol: m for m in metrics}
    pool_scored = sorted(
        [m for m in metrics if m.dynamic_score is not None],
        key=lambda x: x.dynamic_score or 0.0,
        reverse=True,
    )
    pool_rank = {m.symbol: i + 1 for i, m in enumerate(pool_scored)}
    selected_set = set(selected_dynamic)
    for sym in FOCUS_DIAGNOSTIC_SYMBOLS:
        m = metric_by_sym.get(sym)
        base[sym] = {
            **base.get(sym, {}),
            "board_fetched_ok": m is not None
            and "board_fetch_error" not in (m.reject_reasons if m else []),
            "dynamic_pool_rank": pool_rank.get(sym),
            "dynamic_score": m.dynamic_score if m else None,
            "selected_dynamic23": sym in selected_set,
            "reject_reasons": m.reject_reasons if m else [],
        }
    return base


def market_distribution(entries: Sequence[SymbolMasterEntry]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for e in entries:
        c[e.market or "unknown"] += 1
    return dict(c)


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def board_liquidity_proxy(board: Mapping[str, Any]) -> Optional[float]:
    bid = _as_float(board.get("BidQty")) or 0.0
    ask = _as_float(board.get("AskQty")) or 0.0
    total = bid + ask
    return total if total > 0 else None


def is_supervisory_board(board: Mapping[str, Any]) -> bool:
    name = str(board.get("SymbolName") or "")
    if SUPERVISORY_NAME_RE.search(name):
        return True
    status = str(board.get("CurrentPriceStatus") or "")
    if "監理" in status or "整理" in status or "停止" in status:
        return True
    return False


def compute_dynamic_score(
    *,
    trading_value: float,
    change_pct: float,
    liquidity: float,
    spread_bps: Optional[float],
    cfg: DynamicUniverseConfig,
) -> float:
    log_tv = math.log10(max(trading_value, 1.0))
    change_term = abs(change_pct) * cfg.score_change_pct_weight
    liq_bonus = min(2.0, math.log10(max(liquidity, 1.0))) * cfg.score_liquidity_bonus_weight
    spread_pen = (spread_bps or 50.0) * cfg.score_spread_penalty_weight
    return log_tv * cfg.score_log_trading_value_weight + change_term + liq_bonus - spread_pen


def evaluate_board_for_dynamic(
    parsed: ParsedSymbol,
    board: Optional[Mapping[str, Any]],
    *,
    cfg: DynamicUniverseConfig,
    board_error: Optional[str] = None,
) -> BoardMetrics:
    sym = _norm_symbol(parsed.code)
    reasons: list[str] = []

    if board_error:
        return BoardMetrics(
            symbol=sym,
            exchange=parsed.exchange,
            symbol_key=parsed.symbol_key,
            symbol_name=None,
            current_price=None,
            trading_value_proxy=None,
            change_previous_close_pct=None,
            board_liquidity_proxy=None,
            spread_proxy=None,
            dynamic_score=None,
            passed_filter=False,
            reject_reasons=["board_fetch_error"],
            board_error=board_error,
        )

    if board is None:
        return BoardMetrics(
            symbol=sym,
            exchange=parsed.exchange,
            symbol_key=parsed.symbol_key,
            symbol_name=None,
            current_price=None,
            trading_value_proxy=None,
            change_previous_close_pct=None,
            board_liquidity_proxy=None,
            spread_proxy=None,
            dynamic_score=None,
            passed_filter=False,
            reject_reasons=["board_missing"],
        )

    name = str(board.get("SymbolName") or "") or None
    price = _as_float(board.get("CurrentPrice")) or _as_float(board.get("CalcPrice"))
    tv = _as_float(board.get("TradingValue"))
    chg = _as_float(board.get("ChangePreviousClosePer"))
    liq = board_liquidity_proxy(board)
    spread = calc_spread_bps(board)

    # No Prime/Standard/Growth score bias — master is pre-filtered; board only catches ETF/supervisory
    if cfg.exclude_etf and is_etf_board(board):
        reasons.append("etf")
    if is_supervisory_board(board):
        reasons.append("supervisory_or_halted_name")
    if price is None:
        reasons.append("missing_current_price")
    elif price < cfg.min_current_price:
        reasons.append("price_below_min")
    if tv is None or tv <= 0:
        reasons.append("trading_value_invalid")
    if liq is None or liq < cfg.min_board_liquidity_qty:
        reasons.append("board_liquidity_low")
    if spread is None:
        reasons.append("missing_spread_bps")
    elif spread > cfg.max_spread_bps:
        reasons.append("spread_bps_above_max")

    score = None
    if not reasons and tv is not None and chg is not None and liq is not None:
        score = compute_dynamic_score(
            trading_value=tv,
            change_pct=chg,
            liquidity=liq,
            spread_bps=spread,
            cfg=cfg,
        )

    return BoardMetrics(
        symbol=sym,
        exchange=parsed.exchange,
        symbol_key=parsed.symbol_key,
        symbol_name=name,
        current_price=price,
        trading_value_proxy=tv,
        change_previous_close_pct=chg,
        board_liquidity_proxy=liq,
        spread_proxy=spread,
        dynamic_score=score,
        passed_filter=len(reasons) == 0 and score is not None,
        reject_reasons=reasons,
    )


def fetch_board_metrics_batch(
    candidates: Sequence[BoardCandidatePick | SymbolMasterEntry],
    *,
    client: Any,
    token: str,
    cfg: DynamicUniverseConfig,
    log: Optional[logging.Logger] = None,
) -> tuple[list[BoardMetrics], BoardFetchStats]:
    stats = BoardFetchStats()
    results: list[BoardMetrics] = []
    reg_cap = min(cfg.kabu_board_register_limit, KABU_BOARD_REGISTER_LIMIT)
    limit = min(len(candidates), cfg.max_board_fetch_per_run, reg_cap)
    consecutive_429 = 0

    for i in range(limit):
        item = candidates[i]
        entry = item.entry if isinstance(item, BoardCandidatePick) else item
        parsed = entry.parsed
        err: Optional[str] = None
        board: Optional[dict[str, Any]] = None
        try:
            board = client.get_board(parsed.symbol_key, token=token)
            stats.success += 1
            consecutive_429 = 0
        except Exception as e:
            err = str(e)
            stats.errors += 1
            err_cls = classify_board_fetch_error(err)
            stats.error_class_counts[err_cls] += 1
            if err_cls == "http_429_rate_limit":
                stats.rate_limit_count += 1
                consecutive_429 += 1
                stats.backoff_count += 1
                if cfg.rate_limit_backoff_sec > 0:
                    time.sleep(cfg.rate_limit_backoff_sec)
                if consecutive_429 >= cfg.max_consecutive_rate_limits:
                    stats.aborted_early = True
                    stats.abort_reason = "max_consecutive_rate_limits"
                    results.append(
                        evaluate_board_for_dynamic(parsed, board, cfg=cfg, board_error=err)
                    )
                    stats.fetched_count = len(results)
                    if log:
                        log.warning(
                            "aborting board batch after %s consecutive 429s at %s/%s",
                            consecutive_429,
                            i + 1,
                            limit,
                        )
                    break
            else:
                consecutive_429 = 0

        results.append(evaluate_board_for_dynamic(parsed, board, cfg=cfg, board_error=err))
        stats.fetched_count = len(results)

        if cfg.board_fetch_delay_sec > 0 and i + 1 < limit and not stats.aborted_early:
            time.sleep(cfg.board_fetch_delay_sec)
        if log and (i + 1) % 50 == 0:
            log.info(
                "board fetch %s/%s ok=%s err=%s 429=%s backoff=%s",
                i + 1,
                limit,
                stats.success,
                stats.errors,
                stats.rate_limit_count,
                stats.backoff_count,
            )

    return results, stats


def write_board_fetch_candidates_csv(
    path: Path,
    picks: Sequence[BoardCandidatePick],
) -> None:
    fields = (
        "symbol",
        "market",
        "candidate_rank",
        "selection_source",
        "sample_seed",
        "master_index",
        "market_position",
        "market_size",
        "market_position_pct",
        "sampling_method",
        "sampling_bucket",
        "selected_by_rule",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for p in picks:
            row = {
                "symbol": _norm_symbol(p.entry.parsed.code),
                "market": p.market,
                "candidate_rank": p.candidate_rank,
                "selection_source": p.selection_source,
                "sample_seed": p.sample_seed,
                "master_index": p.entry.master_index,
                "market_position": p.market_position,
                "market_size": p.market_size,
                "market_position_pct": p.market_position_pct,
                "sampling_method": p.sampling_method,
                "sampling_bucket": p.sampling_bucket,
                "selected_by_rule": p.selected_by_rule,
            }
            w.writerow(row)


def write_dynamic_scored_candidates_csv(
    path: Path,
    picks: Sequence[BoardCandidatePick],
    metrics: Sequence[BoardMetrics],
    selected_dynamic: set[str],
) -> None:
    metric_by_sym = {m.symbol: m for m in metrics}
    fields = (
        "symbol",
        "market",
        "board_fetch_ok",
        "current_price",
        "change_previous_close_pct",
        "trading_value_proxy",
        "board_liquidity_proxy",
        "spread_proxy",
        "dynamic_score",
        "filter_pass",
        "reject_reason",
        "selected_dynamic",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for p in picks:
            sym = _norm_symbol(p.entry.parsed.code)
            m = metric_by_sym.get(sym)
            if m is None:
                w.writerow(
                    {
                        "symbol": sym,
                        "market": p.market,
                        "board_fetch_ok": False,
                        "filter_pass": False,
                        "reject_reason": "not_fetched",
                        "selected_dynamic": sym in selected_dynamic,
                    }
                )
                continue
            ok = "board_fetch_error" not in m.reject_reasons and m.board_error is None
            w.writerow(
                {
                    "symbol": sym,
                    "market": p.market,
                    "board_fetch_ok": ok,
                    "current_price": m.current_price if m.current_price is not None else "",
                    "change_previous_close_pct": m.change_previous_close_pct
                    if m.change_previous_close_pct is not None
                    else "",
                    "trading_value_proxy": m.trading_value_proxy
                    if m.trading_value_proxy is not None
                    else "",
                    "board_liquidity_proxy": m.board_liquidity_proxy
                    if m.board_liquidity_proxy is not None
                    else "",
                    "spread_proxy": m.spread_proxy if m.spread_proxy is not None else "",
                    "dynamic_score": round(m.dynamic_score, 6) if m.dynamic_score is not None else "",
                    "filter_pass": m.passed_filter,
                    "reject_reason": "|".join(m.reject_reasons),
                    "selected_dynamic": sym in selected_dynamic,
                }
            )


def determine_phase102_verdict(
    *,
    stats: BoardFetchStats,
    picks: Sequence[BoardCandidatePick],
    selected_dynamic: Sequence[str],
    focus_diag: Mapping[str, Any],
    candidate_market_dist: Mapping[str, int],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    n_fetch = max(stats.fetched_count, 1)
    err_rate = stats.errors / n_fetch
    rl_rate = stats.rate_limit_count / n_fetch

    focus_in_candidates = all(
        focus_diag.get(s, {}).get("in_candidate_list") for s in FOCUS_DIAGNOSTIC_SYMBOLS
    )
    focus_fetched = all(
        focus_diag.get(s, {}).get("board_fetched_ok") for s in FOCUS_DIAGNOSTIC_SYMBOLS
    )

    markets_present = {m for m in candidate_market_dist if candidate_market_dist[m] > 0}
    balanced = markets_present >= set(TRADABLE_MARKETS)

    if rl_rate > 0.5 or (stats.aborted_early and stats.success < 50):
        notes.append(f"rate_limit_share={rl_rate:.1%} backoff={stats.backoff_count}")
        return "still_rate_limited", notes

    if balanced and not focus_in_candidates:
        notes.append("market mix OK but focus symbols absent from stratified sample window")
        return "candidate_sampling_ok_but_focus_missed", notes

    if balanced and focus_in_candidates and not focus_fetched and stats.success > 100:
        notes.append("focus in candidate list but board not retrieved — check API session")

    if balanced and rl_rate < 0.15 and stats.success >= 100:
        notes.append(
            f"board_ok={stats.success} rate_limits={stats.rate_limit_count} "
            f"candidate_dist={dict(candidate_market_dist)}"
        )
        return "dynamic_fetch_revision_ready", notes

    if stats.success < 80 and rl_rate < 0.5:
        notes.append("insufficient board coverage — consider prefilter or lower fetch volume")
        return "need_prefilter_data_source", notes

    if focus_in_candidates and stats.success >= 50:
        return "dynamic_fetch_revision_ready", notes

    return "still_rate_limited", notes


def merge_hybrid_universe(
    static_rows: list[dict[str, Any]],
    dynamic_metrics: Sequence[BoardMetrics],
    *,
    cfg: DynamicUniverseConfig,
) -> tuple[list[dict[str, Any]], int, list[str], Counter[str]]:
    static_syms = {str(r["symbol"]) for r in static_rows}
    reject_counter: Counter[str] = Counter()

    eligible = [m for m in dynamic_metrics if m.symbol not in static_syms]
    for m in dynamic_metrics:
        if m.symbol in static_syms and m.dynamic_score is not None:
            pass  # duplicate — not counted as reject
        for r in m.reject_reasons:
            reject_counter[r] += 1

    pool = [m for m in eligible if m.passed_filter]
    pool.sort(key=lambda m: m.dynamic_score or 0.0, reverse=True)
    dynamic_pick = pool[: cfg.dynamic_max]

    duplicate_removed = sum(1 for m in dynamic_metrics if m.symbol in static_syms)

    out = list(static_rows)
    for m in dynamic_pick:
        out.append(
            {
                "symbol": m.symbol,
                "exchange": m.exchange,
                "symbol_key": m.symbol_key,
                "symbol_name": m.symbol_name or "",
                "passed": "True",
                "selection_reason": "dynamic_turnover_gap_score",
                "dynamic_score": round(m.dynamic_score or 0.0, 6),
                "trading_value_proxy": m.trading_value_proxy,
                "change_previous_close_pct": m.change_previous_close_pct,
                "current_price": m.current_price,
                "board_liquidity_proxy": m.board_liquidity_proxy,
                "spread_proxy": m.spread_proxy,
                "exclude_reasons": "",
            }
        )

    if len(out) > cfg.push_limit:
        out = out[: cfg.push_limit]

    selected_dynamic = [m.symbol for m in dynamic_pick]
    return out, duplicate_removed, selected_dynamic, reject_counter


def write_universe_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_FIELDS), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in CSV_FIELDS})


def write_universe_trial_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(TRIAL_CSV_FIELDS), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in TRIAL_CSV_FIELDS})


def write_phase105_dynamic_pool_csv(
    path: Path,
    picks: Sequence[BoardCandidatePick],
    *,
    metrics: Optional[Sequence[BoardMetrics]] = None,
) -> None:
    metric_by_sym = {m.symbol: m for m in metrics} if metrics else {}
    fields = (
        "symbol",
        "symbol_key",
        "exchange",
        "market",
        "candidate_rank",
        "sampling_method",
        "sampling_bucket",
        "selected_by_rule",
        "market_position",
        "market_position_pct",
        "selection_source",
        "dynamic_score",
        "board_validated",
        "board_error_class",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for p in picks:
            sym = _norm_symbol(p.entry.parsed.code)
            m = metric_by_sym.get(sym)
            err_cls = classify_board_fetch_error(m.board_error) if m and m.board_error else ""
            validated = ""
            if m is not None:
                validated = m.board_error is None and "board_fetch_error" not in m.reject_reasons
            w.writerow(
                {
                    "symbol": sym,
                    "symbol_key": p.entry.parsed.symbol_key,
                    "exchange": p.entry.parsed.exchange,
                    "market": p.market,
                    "candidate_rank": p.candidate_rank,
                    "sampling_method": p.sampling_method,
                    "sampling_bucket": p.sampling_bucket,
                    "selected_by_rule": p.selected_by_rule,
                    "market_position": p.market_position,
                    "market_position_pct": p.market_position_pct,
                    "selection_source": p.selection_source,
                    "dynamic_score": round(m.dynamic_score, 6) if m and m.dynamic_score is not None else "",
                    "board_validated": validated,
                    "board_error_class": err_cls,
                }
            )


def build_trial_universe_rows(
    static_rows: list[dict[str, Any]],
    dynamic_picks: Sequence[BoardCandidatePick],
    *,
    metrics: Optional[Sequence[BoardMetrics]] = None,
    board_validated: Optional[Mapping[str, bool]] = None,
) -> list[dict[str, Any]]:
    metric_by_sym = {m.symbol: m for m in metrics} if metrics else {}
    rows: list[dict[str, Any]] = []
    for s in static_rows:
        sym = str(s["symbol"])
        rows.append(
            {
                "symbol": sym,
                "symbol_key": str(s.get("symbol_key") or ""),
                "exchange": int(s.get("exchange") or 1),
                "passed": str(s.get("passed") or "True"),
                "source_bucket": "static_legacy",
                "selected_reason": str(s.get("selection_reason") or "static_intraday_full"),
                "sampling_method": "",
                "market": "static",
                "dynamic_score": "",
                "board_validated": "",
                "board_error_class": "",
                "symbol_name": str(s.get("symbol_name") or ""),
                "market_position": "",
                "market_position_pct": "",
                "sampling_bucket": "",
            }
        )
    for p in dynamic_picks:
        sym = _norm_symbol(p.entry.parsed.code)
        m = metric_by_sym.get(sym)
        err_cls = classify_board_fetch_error(m.board_error) if m and m.board_error else ""
        validated = (board_validated or {}).get(sym, "")
        if validated == "" and m is not None:
            validated = m.board_error is None and "board_fetch_error" not in m.reject_reasons
        rows.append(
            {
                "symbol": sym,
                "symbol_key": p.entry.parsed.symbol_key,
                "exchange": p.entry.parsed.exchange,
                "passed": "True",
                "source_bucket": "dynamic_sampled",
                "selected_reason": p.selected_by_rule or p.selection_source,
                "sampling_method": p.sampling_method,
                "market": p.market,
                "dynamic_score": round(m.dynamic_score, 6) if m and m.dynamic_score is not None else "",
                "board_validated": validated,
                "board_error_class": err_cls,
                "symbol_name": (m.symbol_name if m else "") or "",
                "market_position": p.market_position,
                "market_position_pct": p.market_position_pct,
                "sampling_bucket": p.sampling_bucket,
            }
        )
    return rows


def evaluate_phase105_success_criteria(
    *,
    board_mode: str,
    static_count: int,
    dynamic_count: int,
    total_count: int,
    push_limit: int,
    static_max: int,
    dynamic_max: int,
    fetch_stats: BoardFetchStats,
    validate_register_hits: int,
    board_validate_target_count: int = 0,
) -> dict[str, Any]:
    """Explicit Phase105 pass/fail checks (no PF evaluation)."""
    register_limit_exceeded = int(
        fetch_stats.error_class_counts.get("register_limit_exceeded", 0)
    ) + int(validate_register_hits)

    if board_mode == "none":
        met = (
            total_count == push_limit == 50
            and static_count == static_max == 27
            and dynamic_count == dynamic_max == 23
            and register_limit_exceeded == 0
        )
        return {
            "board_mode": "none",
            "met": met,
            "total_count": total_count,
            "static_count": static_count,
            "dynamic_count": dynamic_count,
            "register_limit_exceeded": register_limit_exceeded,
            "board_queries": 0,
            "requirements": {
                "total_count": 50,
                "static_count": 27,
                "dynamic_count": 23,
                "register_limit_exceeded": 0,
            },
        }

    if board_mode == "validate":
        met = (
            board_validate_target_count <= KABU_BOARD_REGISTER_LIMIT
            and register_limit_exceeded == 0
            and total_count == push_limit
            and static_count == static_max
            and dynamic_count == dynamic_max
        )
        return {
            "board_mode": "validate",
            "met": met,
            "total_count": total_count,
            "static_count": static_count,
            "dynamic_count": dynamic_count,
            "register_limit_exceeded": register_limit_exceeded,
            "board_queries": board_validate_target_count,
            "board_fetch_success_count": fetch_stats.success,
            "board_fetch_error_count": fetch_stats.errors,
            "requirements": {
                "board_queries_max": 50,
                "register_limit_exceeded": 0,
            },
        }

    register_limit_exceeded = int(fetch_stats.error_class_counts.get("register_limit_exceeded", 0))
    met = (
        register_limit_exceeded == 0
        and total_count == push_limit
        and dynamic_count == dynamic_max
        and fetch_stats.fetched_count <= KABU_BOARD_REGISTER_LIMIT
    )
    return {
        "board_mode": board_mode,
        "met": met,
        "total_count": total_count,
        "dynamic_count": dynamic_count,
        "register_limit_exceeded": register_limit_exceeded,
        "board_queries": fetch_stats.fetched_count,
        "requirements": {"board_queries_max": 50, "register_limit_exceeded": 0},
    }


def determine_phase105_verdict(
    *,
    board_mode: str,
    total_count: int,
    push_limit: int,
    dynamic_count: int,
    dynamic_max: int,
    static_count: int,
    static_max: int,
    fetch_stats: BoardFetchStats,
    need_symbol_master: bool,
    validate_register_hits: int,
    board_validate_target_count: int,
    success_criteria: Mapping[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if need_symbol_master:
        return "need_boardless_prefilter_data", ["symbol master missing"]

    if success_criteria.get("met"):
        if board_mode == "none":
            notes.append("board-free 50-symbol universe; register_limit_exceeded=0")
        elif board_mode == "validate":
            notes.append(
                f"validate: board_queries={board_validate_target_count} register_limit_exceeded=0"
            )
        else:
            notes.append(f"score/validate criteria met (mode={board_mode})")
        return "register_limit_aware_universe_ready", notes

    reg = success_criteria.get("register_limit_exceeded", 0)
    if board_mode == "validate" and reg > 0:
        notes.append(f"register_limit_exceeded={reg} on final validate (max 50 board calls)")
        return "validate_mode_register_issue", notes

    if total_count < push_limit or dynamic_count < dynamic_max or static_count < static_max:
        notes.append(
            f"counts static={static_count}/{static_max} dynamic={dynamic_count}/{dynamic_max} "
            f"total={total_count}/{push_limit}"
        )
        return "need_boardless_prefilter_data", notes

    if board_mode == "validate" and board_validate_target_count > KABU_BOARD_REGISTER_LIMIT:
        notes.append(f"board_validate_target_count={board_validate_target_count} > 50")
        return "validate_mode_register_issue", notes

    if board_mode in ("validate", "score") and fetch_stats.errors > 0:
        notes.append(f"board errors={fetch_stats.errors} class={dict(fetch_stats.error_class_counts)}")
        return "runner_ready_but_selection_weak", notes

    notes.append("50-symbol CSV ok; success_criteria not fully met — selection quality unverified")
    return "runner_ready_but_selection_weak", notes


def build_register_limit_aware_universe(
    *,
    repo_root: Path,
    cfg: DynamicUniverseConfig,
    day_stamp: str,
    reports_dir: Path,
    board_mode: str,
    skip_kabu: bool = False,
    symbol_master_override: Optional[Path] = None,
    log: Optional[logging.Logger] = None,
) -> dict[str, Any]:
    """Phase105: static27 + dynamic23 without bulk board; optional validate/score (<=50 board calls)."""
    mode = board_mode if board_mode in BOARD_MODES else cfg.board_mode
    if skip_kabu:
        mode = "none"

    static_path = repo_root / cfg.static_universe_path
    if not static_path.is_file():
        static_path = repo_root / "kabu_native" / "data" / "universe" / "universe_intraday_full.csv"

    static_rows = load_static_universe(static_path, static_max=cfg.static_max)
    master_paths = cfg.symbol_master_paths
    if symbol_master_override is not None:
        master_paths = [str(symbol_master_override)]
    master_path, master_entries = resolve_symbol_master(repo_root, master_paths)
    need_symbol_master = master_path is None or not master_entries
    market_dist_input = market_distribution(master_entries)

    output_csv = reports_dir / f"universe_dynamic_trial_{day_stamp}.csv"
    pool_csv = reports_dir / f"phase105_dynamic_candidate_pool_{day_stamp}.csv"
    phase105_json = reports_dir / f"phase105_register_limit_aware_universe_{day_stamp}.json"

    static_codes = {r["symbol"].replace(".T", "").upper() for r in static_rows}
    dynamic_picks: list[BoardCandidatePick] = []
    if master_entries:
        dynamic_picks = select_dynamic_sample_candidates(
            master_entries, static_codes, cfg=cfg, day_stamp=day_stamp
        )

    metrics: list[BoardMetrics] = []
    fetch_stats = BoardFetchStats()
    board_validated: dict[str, bool] = {}
    validate_register_hits = 0

    effective_dynamic = list(dynamic_picks)
    if mode == "score" and master_entries and not skip_kabu:
        from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, require_kabu_password

        try:
            password = require_kabu_password()
            client = KabuNativeRestClient(base_url=default_base_url())
            token = client.issue_token(password)
            metrics, fetch_stats = fetch_board_metrics_batch(
                dynamic_picks,
                client=client,
                token=token,
                cfg=cfg,
                log=log,
            )
            scored = sorted(
                [m for m in metrics if m.passed_filter and m.dynamic_score is not None],
                key=lambda m: m.dynamic_score or 0.0,
                reverse=True,
            )
            pick_syms = {m.symbol for m in scored[: cfg.dynamic_max]}
            if len(pick_syms) < cfg.dynamic_max:
                for p in dynamic_picks:
                    sym = _norm_symbol(p.entry.parsed.code)
                    if sym not in pick_syms:
                        pick_syms.add(sym)
                    if len(pick_syms) >= cfg.dynamic_max:
                        break
            effective_dynamic = [
                p for p in dynamic_picks if _norm_symbol(p.entry.parsed.code) in pick_syms
            ][: cfg.dynamic_max]
            if len(effective_dynamic) < cfg.dynamic_max:
                effective_dynamic = dynamic_picks[: cfg.dynamic_max]
        except KabuNativeApiError as e:
            if log:
                log.warning("score mode board unavailable: %s", e)
            effective_dynamic = dynamic_picks[: cfg.dynamic_max]

    elif mode == "validate" and not skip_kabu and (static_rows or effective_dynamic):
        from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, require_kabu_password

        validate_entries: list[SymbolMasterEntry] = []
        for r in static_rows:
            code = str(r["symbol"]).replace(".T", "")
            ex = int(r.get("exchange") or 1)
            try:
                parsed = parse_symbol(f"{code}@{ex}", default_exchange=ex)
            except ValueError:
                continue
            validate_entries.append(SymbolMasterEntry(parsed=parsed, market="static"))
        for p in effective_dynamic:
            validate_entries.append(p.entry)

        reg_cap = min(cfg.kabu_board_register_limit, cfg.push_limit)
        validate_entries = validate_entries[:reg_cap]
        try:
            password = require_kabu_password()
            client = KabuNativeRestClient(base_url=default_base_url())
            token = client.issue_token(password)
            metrics, fetch_stats = fetch_board_metrics_batch(
                validate_entries,
                client=client,
                token=token,
                cfg=cfg,
                log=log,
            )
            for m in metrics:
                sym = m.symbol
                ok = m.board_error is None and "board_fetch_error" not in m.reject_reasons
                board_validated[sym] = ok
                if m.board_error and classify_board_fetch_error(m.board_error) == "register_limit_exceeded":
                    validate_register_hits += 1
        except KabuNativeApiError as e:
            if log:
                log.warning("validate mode board unavailable: %s", e)

    trial_rows = build_trial_universe_rows(
        static_rows,
        effective_dynamic,
        metrics=metrics if metrics else None,
        board_validated=board_validated if board_validated else None,
    )
    if len(trial_rows) > cfg.push_limit:
        trial_rows = trial_rows[: cfg.push_limit]

    write_universe_trial_csv(output_csv, trial_rows)
    write_phase105_dynamic_pool_csv(pool_csv, effective_dynamic, metrics=metrics or None)

    dynamic_syms = [
        _norm_symbol(p.entry.parsed.code)
        for p in effective_dynamic
        if _norm_symbol(p.entry.parsed.code) in {r["symbol"] for r in trial_rows}
    ]
    focus_diag = focus_dynamic23_diagnostics(
        effective_dynamic,
        metrics=metrics or None,
        board_validated=board_validated or None,
    )
    dispersion = candidate_dispersion_diagnostics(effective_dynamic)

    board_validate_target_count = 0
    if mode == "validate" and not skip_kabu:
        board_validate_target_count = min(
            len(static_rows) + len(effective_dynamic),
            cfg.push_limit,
            cfg.kabu_board_register_limit,
        )

    success_criteria = evaluate_phase105_success_criteria(
        board_mode=mode,
        static_count=len(static_rows),
        dynamic_count=len(dynamic_syms),
        total_count=len(trial_rows),
        push_limit=cfg.push_limit,
        static_max=cfg.static_max,
        dynamic_max=cfg.dynamic_max,
        fetch_stats=fetch_stats,
        validate_register_hits=validate_register_hits,
        board_validate_target_count=board_validate_target_count,
    )

    verdict, verdict_notes = determine_phase105_verdict(
        board_mode=mode,
        total_count=len(trial_rows),
        push_limit=cfg.push_limit,
        dynamic_count=len(dynamic_syms),
        dynamic_max=cfg.dynamic_max,
        static_count=len(static_rows),
        static_max=cfg.static_max,
        fetch_stats=fetch_stats,
        need_symbol_master=need_symbol_master,
        validate_register_hits=validate_register_hits,
        board_validate_target_count=board_validate_target_count,
        success_criteria=success_criteria,
    )

    payload: dict[str, Any] = {
        "phase": 105,
        "day_stamp": day_stamp,
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "success_criteria": success_criteria,
        "design_note": (
            "No bulk /board on 400 candidates. Board-free dynamic23; "
            "optional validate on final 50 only."
        ),
        "board_mode": mode,
        "candidate_sampling_mode": cfg.candidate_sampling_mode,
        "dynamic_market_quotas": dynamic_market_quotas(cfg),
        "kabu_board_register_limit": cfg.kabu_board_register_limit,
        "max_board_fetch_per_run": min(cfg.max_board_fetch_per_run, cfg.kabu_board_register_limit),
        "static_count": len(static_rows),
        "dynamic_count": len(dynamic_syms),
        "total_count": len(trial_rows),
        "push_limit": cfg.push_limit,
        "need_symbol_master": need_symbol_master,
        "symbol_master_path": str(master_path.relative_to(repo_root)) if master_path else None,
        "symbol_master_count": len(master_entries),
        "market_distribution_input": market_dist_input,
        "dynamic_candidate_pool_count": len(effective_dynamic),
        "dynamic_market_distribution": dict(Counter(p.market for p in effective_dynamic)),
        "board_fetch_success_count": fetch_stats.success,
        "board_fetch_error_count": fetch_stats.errors,
        "board_error_reason_counts": dict(fetch_stats.error_class_counts),
        "board_fetch_skipped": mode == "none" or skip_kabu,
        "board_validate_target_count": board_validate_target_count,
        "register_limit_exceeded_count": success_criteria.get("register_limit_exceeded", 0),
        "bulk_board_fetch_disabled": True,
        "output_universe_csv": str(output_csv.relative_to(repo_root)),
        "phase105_dynamic_candidate_pool_csv": str(pool_csv.relative_to(repo_root)),
        "phase105_json_path": str(phase105_json.relative_to(repo_root)),
        "selected_dynamic_symbols": dynamic_syms,
        "dispersion_diagnostics": dispersion,
        "focus_diagnostics": focus_diag,
        "constraints_confirmed": _constraints()
        + [
            "phase105_register_limit_aware_no_bulk_board",
            f"board_mode_{mode}",
        ],
    }
    phase105_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def build_dynamic_universe(
    *,
    repo_root: Path,
    cfg: DynamicUniverseConfig,
    day_stamp: str,
    reports_dir: Path,
    skip_kabu: bool = False,
    symbol_master_override: Optional[Path] = None,
    log: Optional[logging.Logger] = None,
    board_mode: Optional[str] = None,
    legacy_bulk_board_fetch: bool = False,
) -> dict[str, Any]:
    effective_mode = board_mode or cfg.board_mode
    if not legacy_bulk_board_fetch and _phase105_sampling_mode(cfg):
        payload = build_register_limit_aware_universe(
            repo_root=repo_root,
            cfg=cfg,
            day_stamp=day_stamp,
            reports_dir=reports_dir,
            board_mode=effective_mode,
            skip_kabu=skip_kabu,
            symbol_master_override=symbol_master_override,
            log=log,
        )
        payload["legacy_verdict"] = payload.get("verdict")
        return payload

    static_path = repo_root / cfg.static_universe_path
    if not static_path.is_file():
        alt = repo_root / "kabu_native" / "configs" / "universe_intraday_full.csv"
        if alt.is_file():
            static_path = alt
        else:
            static_path = repo_root / "kabu_native" / "data" / "universe" / "universe_intraday_full.csv"

    static_rows = load_static_universe(static_path, static_max=cfg.static_max)
    master_paths = cfg.symbol_master_paths
    if symbol_master_override is not None:
        master_paths = [str(symbol_master_override)]
    master_path, master_entries = resolve_symbol_master(repo_root, master_paths)
    need_symbol_master = master_path is None or not master_entries
    market_dist_input = market_distribution(master_entries)

    output_csv = reports_dir / f"universe_dynamic_trial_{day_stamp}.csv"
    reject_counter: Counter[str] = Counter()
    board_ok = 0
    board_err = 0
    dynamic_metrics: list[BoardMetrics] = []
    selected_dynamic: list[str] = []
    duplicate_removed = 0

    candidates_path = reports_dir / f"phase103_board_fetch_candidates_{day_stamp}.csv"
    scored_path = reports_dir / f"phase102_dynamic_scored_candidates_{day_stamp}.csv"
    phase102_json_path = reports_dir / f"phase102_dynamic_universe_fetch_revision_{day_stamp}.json"

    if need_symbol_master or skip_kabu:
        rows = static_rows[: cfg.push_limit]
        write_universe_csv(output_csv, rows)
        static_codes = {r["symbol"].replace(".T", "").upper() for r in static_rows}
        candidate_picks = (
            select_board_candidates(master_entries, static_codes, cfg=cfg, day_stamp=day_stamp)
            if master_entries
            else []
        )
        write_board_fetch_candidates_csv(candidates_path, candidate_picks)
        write_dynamic_scored_candidates_csv(scored_path, candidate_picks, [], set())
        candidate_market_dist = Counter(p.market for p in candidate_picks)
        focus_diag = optional_focus_diagnostics(candidate_picks, [], [])
        empty_stats = BoardFetchStats()
        if need_symbol_master:
            verdict = "need_symbol_master"
        else:
            verdict = "candidate_sampling_ok_but_focus_missed"
            if all(focus_diag.get(s, {}).get("in_candidate_list") for s in FOCUS_DIAGNOSTIC_SYMBOLS):
                verdict = "dynamic_fetch_revision_ready"
        payload = _phase102_payload_base(
            repo_root=repo_root,
            cfg=cfg,
            day_stamp=day_stamp,
            static_rows=static_rows,
            master_path=master_path,
            master_entries=master_entries,
            market_dist_input=market_dist_input,
            static_path=static_path,
            output_csv=output_csv,
            candidates_path=candidates_path,
            scored_path=scored_path,
            phase102_json_path=phase102_json_path,
            candidate_picks=candidate_picks,
            fetch_stats=empty_stats,
            candidate_market_dist=candidate_market_dist,
            board_success_dist=Counter(),
            selected_dynamic=[],
            reject_counter=reject_counter,
            focus_diag=focus_diag,
        )
        payload["verdict"] = verdict
        payload["skip_kabu"] = skip_kabu
        payload["board_skipped"] = skip_kabu
        payload["total_count"] = len(rows)
        return payload

    from api.rest_client import KabuNativeApiError, KabuNativeRestClient, default_base_url, require_kabu_password

    try:
        password = require_kabu_password()
    except KabuNativeApiError as e:
        rows = static_rows[: cfg.push_limit]
        write_universe_csv(output_csv, rows)
        return {
            "verdict": "need_board_api_fix",
            "error": str(e),
            "static_count": len(static_rows),
            "dynamic_count": 0,
            "total_count": len(rows),
            "push_limit": cfg.push_limit,
            "duplicate_removed_count": 0,
            "need_symbol_master": False,
            "board_fetch_success_count": 0,
            "board_fetch_error_count": 0,
            "output_universe_csv": str(output_csv.relative_to(repo_root)),
            "selected_dynamic_symbols": [],
            "rejected_reason_counts": {},
            "constraints_confirmed": _constraints(),
        }

    client = KabuNativeRestClient(base_url=default_base_url())
    try:
        token = client.issue_token(password)
    except KabuNativeApiError as e:
        rows = static_rows[: cfg.push_limit]
        write_universe_csv(output_csv, rows)
        return {
            "verdict": "need_board_api_fix",
            "error": str(e),
            "static_count": len(static_rows),
            "dynamic_count": 0,
            "total_count": len(rows),
            "push_limit": cfg.push_limit,
            "duplicate_removed_count": 0,
            "need_symbol_master": False,
            "board_fetch_success_count": 0,
            "board_fetch_error_count": 0,
            "output_universe_csv": str(output_csv.relative_to(repo_root)),
            "selected_dynamic_symbols": [],
            "rejected_reason_counts": {},
            "constraints_confirmed": _constraints(),
        }

    static_codes = {r["symbol"].replace(".T", "").upper() for r in static_rows}
    candidate_picks = select_board_candidates(
        master_entries, static_codes, cfg=cfg, day_stamp=day_stamp
    )
    candidates_path = reports_dir / f"phase103_board_fetch_candidates_{day_stamp}.csv"
    scored_path = reports_dir / f"phase102_dynamic_scored_candidates_{day_stamp}.csv"
    phase102_json_path = reports_dir / f"phase102_dynamic_universe_fetch_revision_{day_stamp}.json"
    write_board_fetch_candidates_csv(candidates_path, candidate_picks)

    candidate_market_dist = Counter(p.market for p in candidate_picks)

    dynamic_metrics, fetch_stats = fetch_board_metrics_batch(
        candidate_picks,
        client=client,
        token=token,
        cfg=cfg,
        log=log,
    )
    board_ok = fetch_stats.success
    board_err = fetch_stats.errors

    board_success_dist: Counter[str] = Counter()
    for p in candidate_picks:
        sym = _norm_symbol(p.entry.parsed.code)
        m = next((x for x in dynamic_metrics if x.symbol == sym), None)
        if m and m.board_error is None and "board_fetch_error" not in m.reject_reasons:
            board_success_dist[p.market] += 1

    if board_ok == 0 and board_err > 0:
        rows = static_rows[: cfg.push_limit]
        write_universe_csv(output_csv, rows)
        write_dynamic_scored_candidates_csv(scored_path, candidate_picks, dynamic_metrics, set())
        focus_diag = optional_focus_diagnostics(candidate_picks, dynamic_metrics, [])
        phase102_verdict, phase102_notes = determine_phase102_verdict(
            stats=fetch_stats,
            picks=candidate_picks,
            selected_dynamic=[],
            focus_diag=focus_diag,
            candidate_market_dist=dict(candidate_market_dist),
        )
        payload = _phase102_payload_base(
            repo_root=repo_root,
            cfg=cfg,
            day_stamp=day_stamp,
            static_rows=static_rows,
            master_path=master_path,
            master_entries=master_entries,
            market_dist_input=market_dist_input,
            static_path=static_path,
            output_csv=output_csv,
            candidates_path=candidates_path,
            scored_path=scored_path,
            phase102_json_path=phase102_json_path,
            candidate_picks=candidate_picks,
            fetch_stats=fetch_stats,
            candidate_market_dist=candidate_market_dist,
            board_success_dist=board_success_dist,
            selected_dynamic=[],
            reject_counter=Counter(r for m in dynamic_metrics for r in m.reject_reasons),
            focus_diag=focus_diag,
        )
        payload["verdict"] = phase102_verdict
        payload["legacy_verdict"] = "need_board_api_fix"
        payload["verdict_notes"] = phase102_notes
        payload["total_count"] = len(rows)
        if fetch_stats.error_class_counts.get("http_400_other", 0) == board_err:
            payload["verdict_notes"] = list(phase102_notes) + [
                "all board errors classified http_400_other — kabu may be closed or API not ready",
            ]
        return payload

    rows, duplicate_removed, selected_dynamic, reject_counter = merge_hybrid_universe(
        static_rows, dynamic_metrics, cfg=cfg
    )
    write_universe_csv(output_csv, rows)
    selected_set = set(selected_dynamic)
    write_dynamic_scored_candidates_csv(scored_path, candidate_picks, dynamic_metrics, selected_set)

    market_dist_selected: Counter[str] = Counter()
    for sym in selected_dynamic:
        for p in candidate_picks:
            if _norm_symbol(p.entry.parsed.code) == sym:
                market_dist_selected[p.market] += 1
                break
    for _r in static_rows:
        market_dist_selected["static_legacy"] += 1

    focus_diag = optional_focus_diagnostics(candidate_picks, dynamic_metrics, selected_dynamic)
    phase102_verdict, phase102_notes = determine_phase102_verdict(
        stats=fetch_stats,
        picks=candidate_picks,
        selected_dynamic=selected_dynamic,
        focus_diag=focus_diag,
        candidate_market_dist=dict(candidate_market_dist),
    )

    legacy_verdict = "dynamic_universe_build_ready"
    if len(selected_dynamic) == 0 and board_ok == 0:
        legacy_verdict = "need_board_api_fix"
    elif len(selected_dynamic) == 0:
        legacy_verdict = "build_ready_with_tradable_master"

    payload = _phase102_payload_base(
        repo_root=repo_root,
        cfg=cfg,
        day_stamp=day_stamp,
        static_rows=static_rows,
        master_path=master_path,
        master_entries=master_entries,
        market_dist_input=market_dist_input,
        static_path=static_path,
        output_csv=output_csv,
        candidates_path=candidates_path,
        scored_path=scored_path,
        phase102_json_path=phase102_json_path,
        candidate_picks=candidate_picks,
        fetch_stats=fetch_stats,
        candidate_market_dist=candidate_market_dist,
        board_success_dist=board_success_dist,
        selected_dynamic=selected_dynamic,
        reject_counter=reject_counter,
        focus_diag=focus_diag,
    )
    payload["verdict"] = phase102_verdict
    payload["legacy_verdict"] = legacy_verdict
    payload["verdict_notes"] = phase102_notes
    payload["total_count"] = len(rows)
    payload["dynamic_count"] = len(selected_dynamic)
    payload["duplicate_removed_count"] = duplicate_removed
    payload["market_distribution_selected"] = dict(market_dist_selected)
    payload["selected_market_distribution"] = dict(market_dist_selected)
    return payload


def _phase102_payload_base(
    *,
    repo_root: Path,
    cfg: DynamicUniverseConfig,
    day_stamp: str,
    static_rows: list[dict[str, Any]],
    master_path: Optional[Path],
    master_entries: Sequence[SymbolMasterEntry],
    market_dist_input: dict[str, int],
    static_path: Path,
    output_csv: Path,
    candidates_path: Path,
    scored_path: Path,
    phase102_json_path: Path,
    candidate_picks: Sequence[BoardCandidatePick],
    fetch_stats: BoardFetchStats,
    candidate_market_dist: Counter[str],
    board_success_dist: Counter[str],
    selected_dynamic: Sequence[str],
    reject_counter: Counter[str],
    focus_diag: dict[str, Any],
) -> dict[str, Any]:
    selected_market: Counter[str] = Counter()
    for sym in selected_dynamic:
        for p in candidate_picks:
            if _norm_symbol(p.entry.parsed.code) == sym:
                selected_market[p.market] += 1
                break

    return {
        "phase": 102,
        "day_stamp": day_stamp,
        "static_count": len(static_rows),
        "dynamic_count": len(selected_dynamic),
        "push_limit": cfg.push_limit,
        "need_symbol_master": False,
        "symbol_master_path": str(master_path.relative_to(repo_root)) if master_path else None,
        "symbol_master_count": len(master_entries),
        "tradable_symbol_count": len(master_entries),
        "market_distribution_input": market_dist_input,
        "candidate_sampling_mode": cfg.candidate_sampling_mode,
        "candidate_quotas": market_quotas(cfg),
        "sample_seed": cfg.sample_seed or day_stamp,
        "candidate_count": len(candidate_picks),
        "candidate_market_distribution": dict(candidate_market_dist),
        "board_success_market_distribution": dict(board_success_dist),
        "selected_market_distribution": dict(selected_market),
        "board_fetch_success_count": fetch_stats.success,
        "board_fetch_error_count": fetch_stats.errors,
        "board_candidates_scanned": fetch_stats.fetched_count,
        "rate_limit_count": fetch_stats.rate_limit_count,
        "backoff_count": fetch_stats.backoff_count,
        "board_fetch_aborted_early": fetch_stats.aborted_early,
        "board_fetch_abort_reason": fetch_stats.abort_reason,
        "board_error_reason_counts": dict(fetch_stats.error_class_counts),
        "board_fetch_delay_sec": cfg.board_fetch_delay_sec,
        "rate_limit_backoff_sec": cfg.rate_limit_backoff_sec,
        "max_board_fetch_per_run": cfg.max_board_fetch_per_run,
        "head_n_sampling_disabled": True,
        "output_universe_csv": str(output_csv.relative_to(repo_root)),
        "phase103_board_fetch_candidates_csv": str(candidates_path.relative_to(repo_root)),
        "phase102_dynamic_scored_candidates_csv": str(scored_path.relative_to(repo_root)),
        "phase102_json_path": str(phase102_json_path.relative_to(repo_root)),
        "selected_dynamic_symbols": list(selected_dynamic),
        "rejected_reason_counts": dict(reject_counter),
        "static_universe_path": str(static_path.relative_to(repo_root)),
        "constraints_confirmed": _constraints(),
        "universe_scope": "tradable_prime_standard_growth",
        "no_market_segment_score_bias": True,
        "optional_focus_diagnostics": focus_diag,
    }


def _constraints() -> list[str]:
    return [
        "no_symbol_hardcode_add_or_exclude",
        "no_market_segment_score_bias",
        "no_time_of_day_filter",
        "no_entry_exit_quality_vol_liq_change",
        "no_production_pilot_yaml_change",
        "no_overwrite_universe_intraday_full",
        "shadow_dry_run_only",
        "tradable_master_prime_standard_growth",
    ]
