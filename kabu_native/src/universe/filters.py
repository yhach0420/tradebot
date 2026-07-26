"""
Board-based universe filters for kabu_native.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from universe.symbols import ParsedSymbol, parse_symbol_list

# 市場構造用 ETF（旧系と同様の代表例）
KNOWN_ETF_CODES: frozenset[str] = frozenset({"1306", "1321"})

# kabu BoardSuccess: 株式は 1 が一般的（ETF/REIT 等は別コード）
SECURITY_TYPE_EQUITY = 1


@dataclass
class UniverseConfig:
    market: str = "prime"
    include_symbols: list[str] = field(default_factory=list)
    exclude_symbols: list[str] = field(default_factory=list)
    exclude_etf: bool = True
    min_trading_value: float | None = None
    min_trading_volume: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    max_spread_bps: float | None = None
    max_symbols: int | None = None
    default_exchange: int = 1

    def parsed_include(self) -> list[ParsedSymbol]:
        return parse_symbol_list(self.include_symbols, default_exchange=self.default_exchange)

    def parsed_exclude(self) -> set[tuple[str, int]]:
        return {(p.code, p.exchange) for p in parse_symbol_list(self.exclude_symbols, default_exchange=self.default_exchange)}


@dataclass
class UniverseRow:
    symbol: str
    exchange: int
    symbol_key: str
    symbol_name: str | None
    passed: bool
    exclude_reasons: list[str]
    current_price: float | None
    trading_value: float | None
    trading_volume: float | None
    spread_bps: float | None
    security_type: int | None
    exchange_name: str | None
    board_error: str | None = None

    def to_csv_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "symbol_key": self.symbol_key,
            "symbol_name": self.symbol_name or "",
            "passed": self.passed,
            "exclude_reasons": "|".join(self.exclude_reasons),
            "current_price": self.current_price,
            "trading_value": self.trading_value,
            "trading_volume": self.trading_volume,
            "spread_bps": self.spread_bps,
            "security_type": self.security_type,
            "exchange_name": self.exchange_name or "",
            "board_error": self.board_error or "",
        }

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "symbol_key": self.symbol_key,
            "symbol_name": self.symbol_name,
            "passed": self.passed,
            "exclude_reasons": self.exclude_reasons,
            "metrics": {
                "current_price": self.current_price,
                "trading_value": self.trading_value,
                "trading_volume": self.trading_volume,
                "spread_bps": self.spread_bps,
                "security_type": self.security_type,
                "exchange_name": self.exchange_name,
            },
            "board_error": self.board_error,
        }


def load_universe_config(path: Path) -> UniverseConfig:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"universe config must be a mapping: {path}")

    def _float(key: str) -> float | None:
        v = raw.get(key)
        if v is None:
            return None
        return float(v)

    def _int(key: str) -> int | None:
        v = raw.get(key)
        if v is None:
            return None
        return int(v)

    return UniverseConfig(
        market=str(raw.get("market", "prime")).strip().lower(),
        include_symbols=[str(s) for s in (raw.get("include_symbols") or [])],
        exclude_symbols=[str(s) for s in (raw.get("exclude_symbols") or [])],
        exclude_etf=bool(raw.get("exclude_etf", True)),
        min_trading_value=_float("min_trading_value"),
        min_trading_volume=_float("min_trading_volume"),
        min_price=_float("min_price"),
        max_price=_float("max_price"),
        max_spread_bps=_float("max_spread_bps"),
        max_symbols=_int("max_symbols"),
        default_exchange=int(raw.get("default_exchange", 1)),
    )


def calc_spread_bps(board: Mapping[str, Any]) -> float | None:
    # Prefer canonical English book when attached (Stage0 / research normalize).
    c_spread = _as_float(board.get("canonical_spread_bps"))
    if c_spread is not None:
        return c_spread
    c_bid = _as_float(board.get("canonical_best_bid"))
    c_ask = _as_float(board.get("canonical_best_ask"))
    if c_bid is not None and c_ask is not None and c_bid > 0 and c_ask > 0:
        mid = (c_bid + c_ask) / 2.0
        if mid > 0:
            return abs(c_ask - c_bid) / mid * 10000.0
    # Last resort: reconstruct from Buy1/Sell1 (true book) rather than kabu Bid/Ask labels
    buy1 = board.get("Buy1") if isinstance(board.get("Buy1"), Mapping) else None
    sell1 = board.get("Sell1") if isinstance(board.get("Sell1"), Mapping) else None
    bid = _as_float(buy1.get("Price")) if buy1 else None
    ask = _as_float(sell1.get("Price")) if sell1 else None
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return abs(ask - bid) / mid * 10000.0


def is_etf_board(board: Mapping[str, Any]) -> bool:
    code = str(board.get("Symbol") or "").strip()
    if code in KNOWN_ETF_CODES:
        return True
    st = board.get("SecurityType")
    if st is not None:
        try:
            if int(st) != SECURITY_TYPE_EQUITY:
                return True
        except (TypeError, ValueError):
            pass
    name = str(board.get("SymbolName") or "")
    for kw in ("ETF", "ＥＴＦ", "上場投信", "インデックス", "REIT", "リート"):
        if kw in name.upper() or kw in name:
            return True
    return False


def is_prime_market(board: Mapping[str, Any]) -> bool:
    ex_name = str(board.get("ExchangeName") or "")
    if "プ" in ex_name or "PRIME" in ex_name.upper():
        return True
    if "スタンダード" in ex_name or "グロース" in ex_name:
        return False
    # 名称欠損時は東証(1)をプライム相当とみなす
    if not ex_name.strip() and board.get("Exchange") == 1:
        return True
    return False


def evaluate_board(
    parsed: ParsedSymbol,
    board: Mapping[str, Any] | None,
    config: UniverseConfig,
    *,
    board_error: str | None = None,
) -> UniverseRow:
    reasons: list[str] = []

    if (parsed.code, parsed.exchange) in config.parsed_exclude():
        reasons.append("config_exclude_symbols")

    if board_error:
        return UniverseRow(
            symbol=parsed.code,
            exchange=parsed.exchange,
            symbol_key=parsed.symbol_key,
            symbol_name=None,
            passed=False,
            exclude_reasons=reasons + ["board_fetch_error"],
            current_price=None,
            trading_value=None,
            trading_volume=None,
            spread_bps=None,
            security_type=None,
            exchange_name=None,
            board_error=board_error,
        )

    if board is None:
        return UniverseRow(
            symbol=parsed.code,
            exchange=parsed.exchange,
            symbol_key=parsed.symbol_key,
            symbol_name=None,
            passed=False,
            exclude_reasons=reasons + ["board_missing"],
            current_price=None,
            trading_value=None,
            trading_volume=None,
            spread_bps=None,
            security_type=None,
            exchange_name=None,
        )

    symbol_name = _as_str(board.get("SymbolName"))
    exchange_name = _as_str(board.get("ExchangeName"))
    current_price = _as_float(board.get("CurrentPrice")) or _as_float(board.get("CalcPrice"))
    trading_value = _as_float(board.get("TradingValue"))
    trading_volume = _as_float(board.get("TradingVolume"))
    spread_bps = calc_spread_bps(board)
    security_type = _as_int(board.get("SecurityType"))
    exchange_code = _as_int(board.get("Exchange"))

    if config.market == "prime" and not is_prime_market(board):
        reasons.append("market_not_prime")

    if exchange_code is not None and exchange_code != parsed.exchange:
        reasons.append("exchange_mismatch")

    if config.exclude_etf and is_etf_board(board):
        reasons.append("etf")

    if config.min_trading_value is not None:
        if trading_value is None:
            reasons.append("missing_trading_value")
        elif trading_value < config.min_trading_value:
            reasons.append("trading_value_below_min")

    if config.min_trading_volume is not None:
        if trading_volume is None:
            reasons.append("missing_trading_volume")
        elif trading_volume < config.min_trading_volume:
            reasons.append("trading_volume_below_min")

    if config.min_price is not None:
        if current_price is None:
            reasons.append("missing_current_price")
        elif current_price < config.min_price:
            reasons.append("price_below_min")

    if config.max_price is not None:
        if current_price is None:
            reasons.append("missing_current_price")
        elif current_price > config.max_price:
            reasons.append("price_above_max")

    if config.max_spread_bps is not None:
        if spread_bps is None:
            reasons.append("missing_spread_bps")
        elif spread_bps > config.max_spread_bps:
            reasons.append("spread_bps_above_max")

    if security_type is not None and security_type != SECURITY_TYPE_EQUITY and config.exclude_etf:
        if "etf" not in reasons:
            reasons.append("security_type_not_equity")

    passed = len(reasons) == 0
    return UniverseRow(
        symbol=parsed.code,
        exchange=parsed.exchange,
        symbol_key=parsed.symbol_key,
        symbol_name=symbol_name,
        passed=passed,
        exclude_reasons=reasons,
        current_price=current_price,
        trading_value=trading_value,
        trading_volume=trading_volume,
        spread_bps=round(spread_bps, 4) if spread_bps is not None else None,
        security_type=security_type,
        exchange_name=exchange_name,
        board_error=board_error,
    )


def apply_max_symbols(rows: list[UniverseRow], max_symbols: int | None) -> list[UniverseRow]:
    """Cap passed symbols by descending trading_value; demote overflow with reason."""
    if max_symbols is None or max_symbols <= 0:
        return rows

    passed = [r for r in rows if r.passed]
    if len(passed) <= max_symbols:
        return rows

    passed_sorted = sorted(
        passed,
        key=lambda r: (r.trading_value is not None, r.trading_value or 0.0),
        reverse=True,
    )
    keep_keys = {r.symbol_key for r in passed_sorted[:max_symbols]}

    out: list[UniverseRow] = []
    for row in rows:
        if row.passed and row.symbol_key not in keep_keys:
            out.append(
                UniverseRow(
                    symbol=row.symbol,
                    exchange=row.exchange,
                    symbol_key=row.symbol_key,
                    symbol_name=row.symbol_name,
                    passed=False,
                    exclude_reasons=row.exclude_reasons + ["max_symbols_cap"],
                    current_price=row.current_price,
                    trading_value=row.trading_value,
                    trading_volume=row.trading_volume,
                    spread_bps=row.spread_bps,
                    security_type=row.security_type,
                    exchange_name=row.exchange_name,
                    board_error=row.board_error,
                )
            )
        else:
            out.append(row)
    return out


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
