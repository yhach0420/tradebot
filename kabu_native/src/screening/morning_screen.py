"""
Morning screening: score universe-passed symbols from kabu /board snapshots.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from universe.filters import SECURITY_TYPE_EQUITY, calc_spread_bps

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]


JST = ZoneInfo("Asia/Tokyo") if ZoneInfo else timezone.utc


@dataclass
class MorningScreenGates:
    require_security_type_equity: bool = True
    min_trading_value: float | None = None
    min_trading_volume: float | None = None
    min_change_pct: float | None = None
    max_change_pct: float | None = None
    max_spread_bps: float | None = None
    max_freshness_sec: float | None = None
    min_price: float | None = None
    max_price: float | None = None


@dataclass
class MorningScreenConfig:
    session_mode: str = "any"
    weights: dict[str, float] = field(default_factory=dict)
    gates: MorningScreenGates = field(default_factory=MorningScreenGates)
    max_symbols: int | None = 10
    output_all_rows: bool = True


@dataclass
class UniverseEntry:
    symbol: str
    exchange: int
    symbol_key: str
    symbol_name: str


@dataclass
class MorningScreenResult:
    rank: int | None
    symbol: str
    symbol_name: str | None
    current_price: float | None
    change_pct: float | None
    trading_value: float | None
    trading_volume: float | None
    vwap: float | None
    vwap_distance_pct: float | None
    high_proximity_ratio: float | None
    spread_bps: float | None
    board_imbalance: float | None
    freshness_sec: float | None
    score: float
    pass_screen: bool
    reject_reasons: list[str]
    symbol_key: str = ""
    security_type: int | None = None
    subscores: dict[str, float | None] = field(default_factory=dict)

    def to_csv_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank if self.rank is not None else "",
            "symbol": self.symbol,
            "symbol_name": self.symbol_name or "",
            "current_price": self.current_price,
            "change_pct": self.change_pct,
            "trading_value": self.trading_value,
            "trading_volume": self.trading_volume,
            "vwap": self.vwap,
            "vwap_distance_pct": self.vwap_distance_pct,
            "high_proximity_ratio": self.high_proximity_ratio,
            "spread_bps": self.spread_bps,
            "board_imbalance": self.board_imbalance,
            "freshness_sec": self.freshness_sec,
            "score": round(self.score, 4) if self.score is not None else "",
            "pass_screen": self.pass_screen,
            "reject_reasons": "|".join(self.reject_reasons),
        }

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "symbol": self.symbol,
            "symbol_key": self.symbol_key,
            "symbol_name": self.symbol_name,
            "current_price": self.current_price,
            "change_pct": self.change_pct,
            "trading_value": self.trading_value,
            "trading_volume": self.trading_volume,
            "vwap": self.vwap,
            "vwap_distance_pct": self.vwap_distance_pct,
            "high_proximity_ratio": self.high_proximity_ratio,
            "spread_bps": self.spread_bps,
            "board_imbalance": self.board_imbalance,
            "freshness_sec": self.freshness_sec,
            "security_type": self.security_type,
            "score": round(self.score, 4),
            "pass_screen": self.pass_screen,
            "reject_reasons": self.reject_reasons,
            "subscores": self.subscores,
        }


def load_morning_screen_config(path: Path) -> MorningScreenConfig:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"morning_screen config must be a mapping: {path}")

    gates_raw = raw.get("gates") or {}
    if not isinstance(gates_raw, Mapping):
        gates_raw = {}

    def _gfloat(key: str) -> float | None:
        v = gates_raw.get(key)
        return None if v is None else float(v)

    gates = MorningScreenGates(
        require_security_type_equity=bool(gates_raw.get("require_security_type_equity", True)),
        min_trading_value=_gfloat("min_trading_value"),
        min_trading_volume=_gfloat("min_trading_volume"),
        min_change_pct=_gfloat("min_change_pct"),
        max_change_pct=_gfloat("max_change_pct"),
        max_spread_bps=_gfloat("max_spread_bps"),
        max_freshness_sec=_gfloat("max_freshness_sec"),
        min_price=_gfloat("min_price"),
        max_price=_gfloat("max_price"),
    )

    weights_raw = raw.get("weights") or {}
    weights = {str(k): float(v) for k, v in weights_raw.items()} if isinstance(weights_raw, Mapping) else {}

    max_sym = raw.get("max_symbols")
    return MorningScreenConfig(
        session_mode=str(raw.get("session_mode", "any")).strip().lower(),
        weights=weights,
        gates=gates,
        max_symbols=int(max_sym) if max_sym is not None else None,
        output_all_rows=bool(raw.get("output_all_rows", True)),
    )


def load_universe_passed(path: Path) -> list[UniverseEntry]:
    entries: list[UniverseEntry] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            passed = str(row.get("passed", "")).strip().lower()
            if passed not in ("true", "1", "yes"):
                continue
            symbol = str(row.get("symbol", "")).strip()
            if not symbol:
                continue
            exchange = int(row.get("exchange") or 1)
            symbol_key = str(row.get("symbol_key") or f"{symbol}@{exchange}").strip()
            symbol_name = str(row.get("symbol_name") or "").strip() or symbol
            entries.append(
                UniverseEntry(
                    symbol=symbol,
                    exchange=exchange,
                    symbol_key=symbol_key,
                    symbol_name=symbol_name,
                )
            )
    return entries


def extract_board_metrics(board: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(JST)
    price = _as_float(board.get("CurrentPrice")) or _as_float(board.get("CalcPrice"))
    vwap = _as_float(board.get("VWAP"))
    high = _as_float(board.get("HighPrice"))
    change_pct = _as_float(board.get("ChangePreviousClosePer"))
    trading_value = _as_float(board.get("TradingValue"))
    trading_volume = _as_float(board.get("TradingVolume"))
    spread_bps = calc_spread_bps(board)
    security_type = _as_int(board.get("SecurityType"))
    symbol_name = _as_str(board.get("SymbolName"))

    vwap_distance_pct: float | None = None
    if price is not None and vwap is not None and vwap > 0:
        vwap_distance_pct = (price - vwap) / vwap * 100.0

    high_proximity_ratio: float | None = None
    if price is not None and high is not None and high > 0:
        high_proximity_ratio = price / high

    freshness_sec = calc_freshness_sec(board.get("CurrentPriceTime"), now=now)
    board_imbalance = calc_board_imbalance(board)

    return {
        "symbol_name": symbol_name,
        "current_price": price,
        "change_pct": change_pct,
        "trading_value": trading_value,
        "trading_volume": trading_volume,
        "vwap": vwap,
        "vwap_distance_pct": vwap_distance_pct,
        "high_proximity_ratio": high_proximity_ratio,
        "spread_bps": spread_bps,
        "board_imbalance": board_imbalance,
        "freshness_sec": freshness_sec,
        "security_type": security_type,
    }


def score_symbol(
    entry: UniverseEntry,
    board: Mapping[str, Any] | None,
    config: MorningScreenConfig,
    *,
    batch_stats: Mapping[str, tuple[float, float]] | None = None,
    board_error: str | None = None,
    now: datetime | None = None,
) -> MorningScreenResult:
    reject: list[str] = []
    subscores: dict[str, float | None] = {}

    if board_error:
        reject.append("board_fetch_error")
    if board is None and not board_error:
        reject.append("board_missing")

    metrics: dict[str, Any] = {}
    if board is not None:
        metrics = extract_board_metrics(board, now=now)
        if metrics.get("symbol_name"):
            entry = UniverseEntry(
                symbol=entry.symbol,
                exchange=entry.exchange,
                symbol_key=entry.symbol_key,
                symbol_name=str(metrics["symbol_name"]),
            )

    def _metric(key: str) -> Any:
        return metrics.get(key)

    # --- record missing (do not silently drop) ---
    for key in (
        "trading_value",
        "trading_volume",
        "current_price",
        "change_pct",
        "vwap",
        "vwap_distance_pct",
        "high_proximity_ratio",
        "spread_bps",
        "board_imbalance",
        "freshness_sec",
        "security_type",
    ):
        if board is not None and _metric(key) is None and key != "freshness_sec":
            reject.append(f"missing_{key}")

    if board is not None and _metric("freshness_sec") is None:
        reject.append("missing_freshness_sec")

    # --- gates ---
    g = config.gates
    tv = _metric("trading_value")
    tvol = _metric("trading_volume")
    price = _metric("current_price")
    chg = _metric("change_pct")
    spread = _metric("spread_bps")
    fresh = _metric("freshness_sec")
    st = _metric("security_type")

    if g.require_security_type_equity:
        if st is None:
            pass  # already missing_security_type
        elif int(st) != SECURITY_TYPE_EQUITY:
            reject.append("security_type_not_equity")

    if g.min_trading_value is not None:
        if tv is None:
            pass
        elif tv < g.min_trading_value:
            reject.append("trading_value_below_min")

    if g.min_trading_volume is not None:
        if tvol is None:
            pass
        elif tvol < g.min_trading_volume:
            reject.append("trading_volume_below_min")

    if g.min_change_pct is not None:
        if chg is None:
            pass
        elif chg < g.min_change_pct:
            reject.append("change_pct_below_min")

    if g.max_change_pct is not None:
        if chg is None:
            pass
        elif chg > g.max_change_pct:
            reject.append("change_pct_above_max")

    if g.max_spread_bps is not None:
        if spread is None:
            pass
        elif spread > g.max_spread_bps:
            reject.append("spread_bps_above_max")

    if g.max_freshness_sec is not None and config.session_mode != "any":
        if fresh is None:
            pass
        elif fresh > g.max_freshness_sec:
            reject.append("freshness_above_max")

    if g.min_price is not None:
        if price is None:
            pass
        elif price < g.min_price:
            reject.append("price_below_min")

    if g.max_price is not None:
        if price is None:
            pass
        elif price > g.max_price:
            reject.append("price_above_max")

    # --- subscores 0..1 ---
    stats = batch_stats or {}
    subscores["trading_value"] = _norm_minmax(tv, stats.get("trading_value"))
    subscores["trading_volume"] = _norm_minmax(tvol, stats.get("trading_volume"))
    subscores["current_price"] = _score_price_band(price, g.min_price, g.max_price)
    subscores["change_pct"] = _score_change_pct(chg)
    subscores["vwap_distance"] = _score_vwap_distance(_metric("vwap_distance_pct"))
    subscores["high_proximity"] = _score_high_proximity(_metric("high_proximity_ratio"))
    subscores["spread_bps"] = _score_spread(spread, g.max_spread_bps)
    subscores["board_imbalance"] = _score_imbalance(_metric("board_imbalance"))
    subscores["freshness"] = _score_freshness(fresh)
    subscores["security_type"] = 1.0 if st == SECURITY_TYPE_EQUITY else (0.0 if st is not None else None)

    w = config.weights or _default_weights()
    total_w = sum(w.get(k, 0.0) for k in subscores)
    if total_w <= 0:
        total_w = 1.0

    weighted = 0.0
    used_w = 0.0
    for key, sub in subscores.items():
        wt = w.get(key, 0.0)
        if wt <= 0 or sub is None:
            continue
        weighted += sub * wt
        used_w += wt

    score = (weighted / used_w * 100.0) if used_w > 0 else 0.0

    # Deduplicate reject reasons preserving order
    seen: set[str] = set()
    unique_reject: list[str] = []
    for r in reject:
        if r not in seen:
            seen.add(r)
            unique_reject.append(r)

    pass_screen = len(unique_reject) == 0

    return MorningScreenResult(
        rank=None,
        symbol=entry.symbol,
        symbol_name=entry.symbol_name,
        current_price=price,
        change_pct=chg,
        trading_value=tv,
        trading_volume=tvol,
        vwap=_metric("vwap"),
        vwap_distance_pct=_metric("vwap_distance_pct"),
        high_proximity_ratio=_metric("high_proximity_ratio"),
        spread_bps=spread,
        board_imbalance=_metric("board_imbalance"),
        freshness_sec=fresh,
        score=score,
        pass_screen=pass_screen,
        reject_reasons=unique_reject,
        symbol_key=entry.symbol_key,
        security_type=st,
        subscores=subscores,
    )


def rank_results(
    results: list[MorningScreenResult],
    *,
    max_symbols: int | None,
    output_all_rows: bool,
) -> list[MorningScreenResult]:
    passed = [r for r in results if r.pass_screen]
    passed_sorted = sorted(passed, key=lambda r: r.score, reverse=True)

    if max_symbols is not None and max_symbols > 0:
        top_keys = {r.symbol_key for r in passed_sorted[:max_symbols]}
    else:
        top_keys = {r.symbol_key for r in passed_sorted}

    ranked_top: list[MorningScreenResult] = []
    for i, r in enumerate(passed_sorted[: max_symbols or len(passed_sorted)], start=1):
        ranked_top.append(
            MorningScreenResult(
                rank=i,
                symbol=r.symbol,
                symbol_name=r.symbol_name,
                current_price=r.current_price,
                change_pct=r.change_pct,
                trading_value=r.trading_value,
                trading_volume=r.trading_volume,
                vwap=r.vwap,
                vwap_distance_pct=r.vwap_distance_pct,
                high_proximity_ratio=r.high_proximity_ratio,
                spread_bps=r.spread_bps,
                board_imbalance=r.board_imbalance,
                freshness_sec=r.freshness_sec,
                score=r.score,
                pass_screen=r.pass_screen,
                reject_reasons=r.reject_reasons,
                symbol_key=r.symbol_key,
                security_type=r.security_type,
                subscores=r.subscores,
            )
        )

    if output_all_rows:
        top_set = top_keys
        out: list[MorningScreenResult] = []
        rank_by_key = {r.symbol_key: r.rank for r in ranked_top}
        for r in results:
            out.append(
                MorningScreenResult(
                    rank=rank_by_key.get(r.symbol_key),
                    symbol=r.symbol,
                    symbol_name=r.symbol_name,
                    current_price=r.current_price,
                    change_pct=r.change_pct,
                    trading_value=r.trading_value,
                    trading_volume=r.trading_volume,
                    vwap=r.vwap,
                    vwap_distance_pct=r.vwap_distance_pct,
                    high_proximity_ratio=r.high_proximity_ratio,
                    spread_bps=r.spread_bps,
                    board_imbalance=r.board_imbalance,
                    freshness_sec=r.freshness_sec,
                    score=r.score,
                    pass_screen=r.pass_screen,
                    reject_reasons=r.reject_reasons,
                    symbol_key=r.symbol_key,
                    security_type=r.security_type,
                    subscores=r.subscores,
                )
            )
        return out

    return ranked_top


def compute_batch_stats(boards: Mapping[str, Mapping[str, Any]]) -> dict[str, tuple[float, float]]:
    tvs: list[float] = []
    vols: list[float] = []
    for board in boards.values():
        m = extract_board_metrics(board)
        if m["trading_value"] is not None:
            tvs.append(float(m["trading_value"]))
        if m["trading_volume"] is not None:
            vols.append(float(m["trading_volume"]))
    stats: dict[str, tuple[float, float]] = {}
    if tvs:
        stats["trading_value"] = (min(tvs), max(tvs))
    if vols:
        stats["trading_volume"] = (min(vols), max(vols))
    return stats


def calc_board_imbalance(board: Mapping[str, Any]) -> float | None:
    bid = _as_float(board.get("BidQty")) or 0.0
    ask = _as_float(board.get("AskQty")) or 0.0
    for i in range(1, 11):
        buy = board.get(f"Buy{i}")
        if isinstance(buy, Mapping):
            bid += _as_float(buy.get("Qty")) or 0.0
        sell = board.get(f"Sell{i}")
        if isinstance(sell, Mapping):
            ask += _as_float(sell.get("Qty")) or 0.0
    total = bid + ask
    if total <= 0:
        return None
    return bid / total


def calc_freshness_sec(price_time: Any, *, now: datetime) -> float | None:
    if price_time is None:
        return None
    if isinstance(price_time, datetime):
        ts = price_time
    else:
        s = str(price_time).strip()
        if not s:
            return None
        try:
            ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=JST)
    now_aware = now if now.tzinfo else now.replace(tzinfo=JST)
    return max(0.0, (now_aware - ts.astimezone(now_aware.tzinfo)).total_seconds())


def _default_weights() -> dict[str, float]:
    return {
        "trading_value": 0.15,
        "trading_volume": 0.10,
        "current_price": 0.05,
        "change_pct": 0.15,
        "vwap_distance": 0.15,
        "high_proximity": 0.15,
        "spread_bps": 0.10,
        "board_imbalance": 0.10,
        "freshness": 0.05,
        "security_type": 0.0,
    }


def _norm_minmax(value: float | None, bounds: tuple[float, float] | None) -> float | None:
    if value is None or bounds is None:
        return None
    lo, hi = bounds
    if hi <= lo:
        return 1.0 if value >= lo else 0.0
    return max(0.0, min(1.0, (float(value) - lo) / (hi - lo)))


def _score_price_band(price: float | None, lo: float | None, hi: float | None) -> float | None:
    if price is None:
        return None
    if lo is not None and price < lo:
        return max(0.0, 1.0 - (lo - price) / lo) if lo > 0 else 0.0
    if hi is not None and price > hi:
        return max(0.0, 1.0 - (price - hi) / hi) if hi > 0 else 0.0
    return 1.0


def _score_change_pct(chg: float | None) -> float | None:
    if chg is None:
        return None
    c = float(chg)
    if 1.0 <= c < 5.0:
        return 1.0
    if 5.0 <= c < 8.0:
        return 0.75
    if 0.0 <= c < 1.0:
        return 0.55
    if 8.0 <= c <= 12.0:
        return 0.35
    if c < 0:
        return max(0.0, 0.25 + c / 20.0)
    return 0.2


def _score_vwap_distance(dist_pct: float | None) -> float | None:
    if dist_pct is None:
        return None
    d = float(dist_pct)
    if d >= 0.5:
        return min(1.0, 0.7 + d / 10.0)
    if d >= 0:
        return 0.5 + d
    return max(0.0, 0.5 + d / 2.0)


def _score_high_proximity(ratio: float | None) -> float | None:
    if ratio is None:
        return None
    r = float(ratio)
    if r >= 0.98:
        return 1.0
    if r >= 0.95:
        return 0.8
    if r >= 0.90:
        return 0.5
    return max(0.0, r - 0.5)


def _score_spread(spread_bps: float | None, max_bps: float | None) -> float | None:
    if spread_bps is None:
        return None
    cap = max_bps if max_bps and max_bps > 0 else 40.0
    return max(0.0, min(1.0, 1.0 - float(spread_bps) / cap))


def _score_imbalance(imb: float | None) -> float | None:
    if imb is None:
        return None
    # ロングバイアス: 買い厚み 0.55 以上を高評価
    if imb >= 0.55:
        return min(1.0, 0.5 + (imb - 0.5) * 2.0)
    if imb >= 0.45:
        return 0.5
    return max(0.0, imb)


def _score_freshness(sec: float | None) -> float | None:
    if sec is None:
        return None
    if sec <= 30:
        return 1.0
    if sec <= 120:
        return 0.85
    if sec <= 900:
        return 0.6
    if sec <= 3600:
        return 0.4
    # 引け後も実行可能: 古いデータは低得点だが 0 にはしない
    return max(0.15, 1.0 - math.log10(sec + 1) / 6.0)


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None
