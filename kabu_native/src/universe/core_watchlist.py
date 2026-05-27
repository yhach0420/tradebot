"""
Phase 117/119: Discord-managed Core10 daily rotation watchlist (shadow only).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

CORE_LIMIT = 10
WATCHLIST_VERSION = 2
REJECT_CORE_LIMIT_EXCEEDED = "core_limit_exceeded"
REJECT_DUPLICATE = "duplicate"
REJECT_INVALID_SYMBOL = "invalid_symbol"

# TSE listed symbols: 4-digit numeric (7203.T) or growth/alpha (186A.T = 3 digits + 1 letter)
_TSE_NUMERIC_RE = re.compile(r"^\d{4}\.T$", re.IGNORECASE)
_TSE_ALPHA_RE = re.compile(r"^\d{3}[A-Z]\.T$", re.IGNORECASE)

CORE_LIMIT_MESSAGE = (
    "Core watchlist limit reached (10/10).\n"
    "Remove or replace symbols before adding more."
)

CORE_LIMIT_MESSAGE_LEGACY = (
    "Core watchlist limit reached (10/10).\n"
    "Remove an existing symbol before adding a new one."
)


@dataclass
class CoreWatchlistState:
    symbols: list[str]
    core_last_updated_date: Optional[str]
    updated_at_jst: Optional[str]
    write_path: Path
    raw_version: int


def normalize_watch_symbol(raw: str) -> str:
    s = str(raw or "").strip().upper().split("@")[0]
    if not s:
        return ""
    if s.endswith(".T"):
        return s
    if s.isdigit() and len(s) == 4:
        return f"{s}.T"
    if re.match(r"^\d{3}[A-Z]$", s):
        return f"{s}.T"
    return s


def is_valid_tse_watch_symbol(symbol: str) -> bool:
    """True for normalized TSE watch symbols (numeric or alpha suffix)."""
    sym = normalize_watch_symbol(symbol)
    if not sym:
        return False
    return bool(_TSE_NUMERIC_RE.match(sym) or _TSE_ALPHA_RE.match(sym))


def validate_watch_symbol(symbol: str) -> tuple[bool, Optional[str]]:
    sym = normalize_watch_symbol(symbol)
    if not sym:
        return False, REJECT_INVALID_SYMBOL
    if not is_valid_tse_watch_symbol(sym):
        return False, REJECT_INVALID_SYMBOL
    return True, None


def resolve_watchlist_paths(repo_root: Path) -> list[Path]:
    return [
        repo_root / "discord_issue_bot" / "watchlist.json",
        repo_root / "watchlist.json",
    ]


def resolve_core_symbol_source_path(repo_root: Path) -> dict[str, Any]:
    paths = resolve_watchlist_paths(repo_root)
    readable: Optional[Path] = None
    for p in paths:
        if p.is_file():
            readable = p
            break
    primary = paths[0]
    return {
        "core_symbol_source_path": str(primary),
        "discord_bot_write_path": str(primary),
        "yahoo_watch_fallback_path": str(paths[1]),
        "paths_checked": [str(p) for p in paths],
        "readable_path": str(readable) if readable else None,
        "readable_exists": readable is not None,
        "note": (
            "Discord !core commands use discord_issue_bot/watchlist.json; "
            "!watch is an alias of !core (Phase119). "
            "market.yahoo.watch may read repo-root watchlist.json separately."
        ),
    }


def _today_jst() -> date:
    return datetime.now(JST).date()


def _today_jst_iso() -> str:
    return _today_jst().isoformat()


def _parse_watchlist_raw(raw: Any) -> tuple[list[str], Optional[str], Optional[str], int]:
    if isinstance(raw, list):
        syms = [normalize_watch_symbol(s) for s in raw if normalize_watch_symbol(s)]
        return syms, None, None, 1
    if isinstance(raw, dict):
        maybe = raw.get("symbols") or raw.get("watchlist") or []
        if isinstance(maybe, list):
            syms = [normalize_watch_symbol(s) for s in maybe if normalize_watch_symbol(s)]
            last = str(raw.get("core_last_updated_date") or "").strip() or None
            at = str(raw.get("updated_at_jst") or "").strip() or None
            ver = int(raw.get("version") or WATCHLIST_VERSION)
            return syms, last, at, ver
    return [], None, None, 1


def load_core_state(repo_root: Path) -> CoreWatchlistState:
    paths = resolve_watchlist_paths(repo_root)
    write_path = paths[0]
    for p in paths:
        if not p.is_file():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        symbols, last_d, at_jst, ver = _parse_watchlist_raw(raw)
        if not last_d:
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=JST).date()
                last_d = mtime.isoformat()
            except OSError:
                last_d = None
        return CoreWatchlistState(
            symbols=symbols,
            core_last_updated_date=last_d,
            updated_at_jst=at_jst,
            write_path=write_path,
            raw_version=ver,
        )
    return CoreWatchlistState(
        symbols=[],
        core_last_updated_date=None,
        updated_at_jst=None,
        write_path=write_path,
        raw_version=WATCHLIST_VERSION,
    )


def load_core_watchlist(repo_root: Path) -> tuple[list[str], Path]:
    state = load_core_state(repo_root)
    return state.symbols, state.write_path


def save_core_state(
    repo_root: Path,
    symbols: Sequence[str],
    *,
    updated_date: Optional[date] = None,
) -> Path:
    write_path = resolve_watchlist_paths(repo_root)[0]
    uniq: list[str] = []
    seen: set[str] = set()
    for s in symbols:
        sym = normalize_watch_symbol(s)
        if sym and sym not in seen:
            seen.add(sym)
            uniq.append(sym)
    if len(uniq) > CORE_LIMIT:
        raise ValueError(f"core symbols exceed limit: {len(uniq)} > {CORE_LIMIT}")
    d = updated_date or _today_jst()
    now = datetime.now(JST).isoformat(timespec="seconds")
    payload = {
        "version": WATCHLIST_VERSION,
        "symbols": uniq,
        "core_last_updated_date": d.isoformat(),
        "updated_at_jst": now,
    }
    write_path.parent.mkdir(parents=True, exist_ok=True)
    write_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return write_path


def save_core_watchlist(repo_root: Path, symbols: Sequence[str]) -> Path:
    return save_core_state(repo_root, symbols, updated_date=_today_jst())


def parse_replace_symbols(raw: str) -> tuple[list[str], list[str], list[str]]:
    """Return (valid_unique_ordered, invalid, duplicate_in_input)."""
    parts = re.split(r"[\s,;]+", (raw or "").strip())
    ordered: list[str] = []
    invalid: list[str] = []
    dup_in_input: list[str] = []
    seen: set[str] = set()
    for p in parts:
        if not p.strip():
            continue
        sym = normalize_watch_symbol(p)
        if not sym:
            continue
        ok, _ = validate_watch_symbol(sym)
        if not ok:
            invalid.append(p.strip())
            continue
        if sym in seen:
            dup_in_input.append(sym)
            continue
        seen.add(sym)
        ordered.append(sym)
    return ordered, invalid, dup_in_input


def can_add_to_core(symbols: list[str], new_symbol: str) -> tuple[bool, Optional[str], str]:
    sym = normalize_watch_symbol(new_symbol)
    ok, reason = validate_watch_symbol(sym)
    if not ok:
        return (
            False,
            reason,
            f"Invalid symbol: {new_symbol!r}. Use TSE code (e.g. 7203.T or 186A.T).",
        )
    normed = [normalize_watch_symbol(s) for s in symbols]
    if sym in normed:
        return False, REJECT_DUPLICATE, f"Already on Core10: {sym}"
    if len(normed) >= CORE_LIMIT:
        return False, REJECT_CORE_LIMIT_EXCEEDED, CORE_LIMIT_MESSAGE
    return True, None, ""


def can_replace_core(raw: str) -> tuple[bool, list[str], Optional[str], str]:
    ordered, invalid, dup_in = parse_replace_symbols(raw)
    if invalid:
        return False, ordered, REJECT_INVALID_SYMBOL, (
            f"Invalid symbols: {', '.join(invalid)}. Use TSE code (e.g. 7203.T or 186A.T)."
        )
    if dup_in:
        return False, ordered, REJECT_DUPLICATE, f"Duplicate in input: {', '.join(sorted(set(dup_in)))}"
    if len(ordered) > CORE_LIMIT:
        return (
            False,
            ordered,
            REJECT_CORE_LIMIT_EXCEEDED,
            f"Core replace rejected: {len(ordered)} symbols (max {CORE_LIMIT}).",
        )
    return True, ordered, None, ""


def assess_core_freshness(
    state: CoreWatchlistState,
    *,
    trade_date: Optional[date] = None,
) -> dict[str, Any]:
    td = trade_date or _today_jst()
    trade_iso = td.isoformat()
    last = state.core_last_updated_date
    is_today = last == trade_iso if last else False
    stale = not is_today
    warning = ""
    if stale:
        if not last:
            warning = "Core10 has no core_last_updated_date; update via !core replace before morning run."
        else:
            warning = (
                f"Core10 last updated {last}, not today ({trade_iso}). "
                "Review with !core list / !core replace (caution only, not blocked)."
            )
    return {
        "core_last_updated_date": last,
        "core_is_today": is_today,
        "core_stale_warning": warning if stale else "",
        "trade_date_checked": trade_iso,
        "morning_check_caution": stale,
        "blocked": False,
    }


def core_status_report(
    repo_root: Path,
    *,
    trade_date: Optional[date] = None,
) -> dict[str, Any]:
    state = load_core_state(repo_root)
    source = resolve_core_symbol_source_path(repo_root)
    freshness = assess_core_freshness(state, trade_date=trade_date)
    normed = [normalize_watch_symbol(s) for s in state.symbols]
    invalid: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for sym in normed:
        if not sym:
            continue
        ok, _ = validate_watch_symbol(sym)
        if not ok:
            invalid.append(sym)
        if sym in seen:
            duplicates.append(sym)
        seen.add(sym)
    dynamic_slots = max(0, 50 - min(len(normed), CORE_LIMIT))
    return {
        **source,
        **freshness,
        "core_count": len(normed),
        "core_symbols": list(normed),
        "invalid_core_symbols": invalid,
        "duplicate_core_symbols": duplicates,
        "core_limit": CORE_LIMIT,
        "dynamic_slots_if_core10_full": 40,
        "dynamic_slots_current": dynamic_slots,
        "updated_at_jst": state.updated_at_jst,
        "core_freshness_check_present": True,
    }


def format_core_list_reply(
    symbols: list[str],
    *,
    trade_date: Optional[date] = None,
    core_last_updated_date: Optional[str] = None,
) -> str:
    td = trade_date or _today_jst()
    trade_iso = td.isoformat()
    is_today = core_last_updated_date == trade_iso if core_last_updated_date else False
    label = "today" if is_today else "stale"
    n = len(symbols)
    stale_msg = ""
    if not is_today:
        if not core_last_updated_date:
            stale_msg = (
                "Core10 has no core_last_updated_date; update via !core replace before morning run."
            )
        else:
            stale_msg = (
                f"Core10 last updated {core_last_updated_date}, not today ({trade_iso}). "
                "Review with !core list / !core replace."
            )
    if not symbols:
        lines = [f"Core10 {label} (0/{CORE_LIMIT}):", "(empty — use !core replace or !core add)"]
        if stale_msg:
            lines.append(f"\n⚠ {stale_msg}")
        return "\n".join(lines)
    body = "\n".join(symbols)
    lines = [f"Core10 {label} ({n}/{CORE_LIMIT}):", body]
    if stale_msg:
        lines.append(f"\n⚠ {stale_msg}")
    return "\n".join(lines)


def discord_core_commands_present(repo_root: Path) -> bool:
    bot = repo_root / "discord_issue_bot" / "discord_issue_bot.py"
    if not bot.is_file():
        return False
    text = bot.read_text(encoding="utf-8")
    required = (
        '@bot.group(name="core"',
        '@core_group.command(name="list")',
        '@core_group.command(name="add")',
        '@core_group.command(name="remove")',
        '@core_group.command(name="clear")',
        '@core_group.command(name="replace")',
        "can_replace_core",
        "save_core_state",
    )
    return all(r in text for r in required)


def discord_enforcement_ok(repo_root: Path) -> bool:
    bot = repo_root / "discord_issue_bot" / "discord_issue_bot.py"
    if not bot.is_file():
        return False
    text = bot.read_text(encoding="utf-8")
    return (
        "can_add_to_core" in text
        and "can_replace_core" in text
        and "REJECT_CORE_LIMIT_EXCEEDED" in text
    )
