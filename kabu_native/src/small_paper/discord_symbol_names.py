"""
Phase279: Symbol display names for Discord UX (read-only master lookup).
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Optional


def _norm_symbol(sym: str) -> str:
    s = str(sym or "").strip().upper()
    if not s:
        return ""
    if "." not in s and s.isdigit():
        return f"{s}.T"
    return s


def _repo_root_guess() -> Path:
    here = Path(__file__).resolve()
    # kabu_native/src/small_paper -> repo root
    return here.parents[3]


def load_symbol_name_map(
    *,
    master_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> dict[str, str]:
    """Load symbol -> name from data/jpx/tradable_symbols.csv (code-only if missing)."""
    root = repo_root or _repo_root_guess()
    path = master_path or (root / "data" / "jpx" / "tradable_symbols.csv")
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = _norm_symbol(row.get("symbol") or "")
            if not sym:
                continue
            name = str(row.get("name") or row.get("symbol_name") or "").strip()
            if name:
                out[sym] = name
    return out


@lru_cache(maxsize=1)
def get_cached_symbol_name_map() -> dict[str, str]:
    return load_symbol_name_map()


def format_symbol_label(symbol: str, name_map: Optional[Mapping[str, str]] = None) -> str:
    sym = _norm_symbol(symbol)
    code = sym.replace(".T", "") if sym else "—"
    names = name_map if name_map is not None else get_cached_symbol_name_map()
    name = (names.get(sym) or "").strip()
    if name:
        return f"{code} {name}"
    return code


def format_symbol_display(
    symbol: str,
    name: Optional[str] = None,
    *,
    name_map: Optional[Mapping[str, str]] = None,
) -> str:
    """Discord ENTRY/EXIT header: code with .T suffix and optional Japanese name."""
    sym = _norm_symbol(symbol)
    if not sym:
        return "—"
    resolved = (name or "").strip()
    if not resolved:
        names = name_map if name_map is not None else get_cached_symbol_name_map()
        resolved = (names.get(sym) or "").strip()
    if resolved:
        return f"{sym} {resolved}"
    return sym


def _name_keys(symbol: str) -> list[str]:
    raw = str(symbol or "").strip()
    sym = _norm_symbol(raw)
    keys: list[str] = []
    for item in (raw, sym, sym.replace(".T", "") if sym else ""):
        if item and item not in keys:
            keys.append(item)
    return keys


def _lookup_name(symbol: str, mapping: Optional[Mapping[str, str]]) -> str:
    if not mapping:
        return ""
    for key in _name_keys(symbol):
        name = str(mapping.get(key) or "").strip()
        if name:
            return name
    return ""


def _log_name_lookup_failure(symbol: str) -> None:
    """Local log only. Never raises and never blocks ENTRY or EXIT."""
    try:
        import json
        from datetime import datetime
        from zoneinfo import ZoneInfo

        native = Path(__file__).resolve().parents[2]
        path = native / "results" / "operations" / "x1_discord_routing" / "name_lookup_failures.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "symbol": _norm_symbol(symbol) or str(symbol or ""),
            "result": "UNKNOWN",
            "logged_at": datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds"),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        return


def resolve_x1_symbol_name(
    symbol: str,
    *,
    board_name: str = "",
    universe_names: Optional[Mapping[str, str]] = None,
    master_names: Optional[Mapping[str, str]] = None,
) -> tuple[str, str]:
    """Board payload, then today's universe map, then the local symbol master.

    No network lookup. Failure returns an empty name and source ``unknown``.
    """
    try:
        board = str(board_name or "").strip()
        if board:
            return board, "board"
        universe = _lookup_name(symbol, universe_names)
        if universe:
            return universe, "universe"
        master = master_names if master_names is not None else get_cached_symbol_name_map()
        found = _lookup_name(symbol, master)
        if found:
            return found, "symbol_master"
    except Exception:
        found = ""
    _log_name_lookup_failure(symbol)
    return "", "unknown"


def format_x1_symbol_line(symbol: str, name: str) -> tuple[str, bool]:
    """Known names render as ``8362 名称``. Unknown keeps the code and a separate label."""
    sym = _norm_symbol(symbol)
    code = sym.replace(".T", "") if sym else str(symbol or "—")
    if str(name or "").strip():
        return f"{code} {str(name).strip()}", True
    shown = sym or code
    return shown, False


def format_yen_price(price: object) -> str:
    try:
        value = float(price)
    except (TypeError, ValueError):
        return "—"
    nearest = round(value)
    if abs(value - nearest) < 1e-6:
        return f"{int(nearest):,}円"
    return f"{value:,.2f}円"


def format_yen_pnl(pnl: object) -> str:
    try:
        value = float(pnl)
    except (TypeError, ValueError):
        return "—"
    nearest = round(value)
    if abs(value - nearest) < 1e-6:
        value = float(nearest)
        shown = int(nearest)
    else:
        shown = value
    if shown > 0:
        text = f"+{shown:,}" if isinstance(shown, int) else f"+{shown:,.2f}"
    elif shown < 0:
        text = f"-{abs(shown):,}" if isinstance(shown, int) else f"-{abs(shown):,.2f}"
    else:
        return "0円"
    return f"{text}円"


def format_hold_time(hold_sec: object) -> str:
    try:
        seconds = int(round(float(hold_sec)))
    except (TypeError, ValueError):
        return "—"
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分{seconds % 60}秒"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remain = seconds % 60
    return f"{hours}時間{minutes:02d}分{remain:02d}秒"


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def format_cap_usage(after: object, maximum: object) -> str:
    return f"{_as_int(after)} / {_as_int(maximum)}"


def format_cap_release(before: object, after: object, maximum: object) -> str:
    return f"{_as_int(before)} / {_as_int(maximum)} → {_as_int(after)} / {_as_int(maximum)}"


_PAPER_ONLY = "PAPER ONLY / 実注文なし"


def x1_symbol_fields(symbol: str, name: str) -> list[dict[str, str]]:
    line, known = format_x1_symbol_line(symbol, name)
    fields = [{"name": "銘柄", "value": line[:256], "inline": False}]
    if not known:
        fields.append({"name": "銘柄名", "value": "UNKNOWN", "inline": True})
    return fields


def x1_entry_discord_fields(event: Mapping[str, object]) -> list[dict[str, str]]:
    symbol = str(event.get("symbol") or "")
    name, _source = resolve_x1_symbol_name(
        symbol,
        board_name=str(event.get("symbol_name_board") or event.get("symbol_name") or ""),
        universe_names=event.get("symbol_name_universe_map") if isinstance(event.get("symbol_name_universe_map"), Mapping) else {symbol: str(event.get("symbol_name_universe") or "")},
    )
    qty = event.get("qty")
    price = format_yen_price(event.get("entry_price"))
    entry_line = price if qty in (None, "") else f"{price} × {_as_int(qty)}株"
    fields = x1_symbol_fields(symbol, name)
    fields.extend(
        [
            {"name": "ENTRY", "value": entry_line, "inline": False},
            {
                "name": "CAP",
                "value": format_cap_usage(event.get("cap_after"), event.get("cap_max")),
                "inline": True,
            },
            {"name": "SUPPORT", "value": format_yen_price(event.get("support")), "inline": True},
            {"name": "execution", "value": str(event.get("execution_family") or "")[:80], "inline": True},
            {"name": "activation_id", "value": str(event.get("activation_id") or "")[:256], "inline": False},
            {"name": "source", "value": str(event.get("source") or "")[:80], "inline": False},
            {"name": "notice", "value": _PAPER_ONLY, "inline": False},
        ]
    )
    return fields


def x1_exit_discord_fields(context: Mapping[str, object]) -> list[dict[str, str]]:
    symbol = str(context.get("symbol") or "")
    name, _source = resolve_x1_symbol_name(
        symbol,
        board_name=str(context.get("symbol_name_board") or context.get("symbol_name") or ""),
        universe_names={symbol: str(context.get("symbol_name_universe") or "")},
    )
    fields = x1_symbol_fields(symbol, name)
    fields.extend(
        [
            {"name": "ENTRY", "value": format_yen_price(context.get("entry_price")), "inline": True},
            {"name": "EXIT", "value": format_yen_price(context.get("exit_price") if context.get("exit_price") is not None else context.get("current_price")), "inline": True},
            {"name": "数量", "value": f"{_as_int(context.get('qty'))}株", "inline": True},
            {"name": "損益", "value": format_yen_pnl(context.get("realized_pnl_yen")), "inline": True},
            {"name": "保有時間", "value": format_hold_time(context.get("hold_sec")), "inline": True},
            {"name": "EXIT理由", "value": str(context.get("exit_reason") or "")[:80], "inline": False},
            {
                "name": "CAP",
                "value": format_cap_release(
                    context.get("cap_before_release"),
                    context.get("cap_after_release"),
                    context.get("cap_max"),
                ),
                "inline": False,
            },
            {"name": "activation_id", "value": str(context.get("activation_id") or "")[:256], "inline": False},
            {"name": "execution_family", "value": str(context.get("execution_family") or "")[:80], "inline": True},
            {"name": "notice", "value": _PAPER_ONLY, "inline": False},
        ]
    )
    return fields
