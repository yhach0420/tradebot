"""DAY_FIXED_AM_RUNTIME_UNIVERSE_V1 — same-day AM CSV as Ingress registration SoT.

V1R Primary and Market Ingress registration share the same canonical 50.
Ingress must not generate an independent universe. Prior-day desired files
are fail-closed (STALE_DESIRED_UNIVERSE); mtime is never the SoT.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional, Sequence

from small_paper.market_capture_registration import (
    load_symbols_from_universe_csv,
)
from small_paper.v1r_live_dual_lane import canonical_symbol_key
from small_paper.v1r_primary_runtime import UNIVERSE_CONTRACT

STALE_DESIRED_UNIVERSE = "STALE_DESIRED_UNIVERSE"
EXPECTED_SYMBOLS = 50
AM_CSV_NAME = "universe_core10_dynamic40_price_risk_am_{day}.csv"


def am_csv_path(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "results" / "reports" / AM_CSV_NAME.format(day=str(trading_date))


def canonical_symbols(raw: Sequence[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = canonical_symbol_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def canonical_membership_sha(symbols: Sequence[str]) -> str:
    norm = ",".join(sorted({canonical_symbol_key(s) for s in symbols if canonical_symbol_key(s)}))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_am_canonical_50(native_root: Path, trading_date: str) -> dict[str, Any]:
    """Load same-day AM Core10+Dynamic40 CSV. Fail-closed unless exactly 50 unique canonical."""
    day = str(trading_date)
    path = am_csv_path(native_root, day)
    base: dict[str, Any] = {
        "ok": False,
        "contract": UNIVERSE_CONTRACT,
        "trading_date": day,
        "symbols": [],
        "symbol_count": 0,
        "universe_path": str(path) if path.is_file() else None,
        "universe_sha256": "",
        "canonical_membership_sha": "",
        "reason": "",
    }
    if not path.is_file():
        base["reason"] = "am_csv_missing"
        return base
    raw = load_symbols_from_universe_csv(path)
    symbols = canonical_symbols(raw)
    sha = file_sha256(path)
    base.update(
        {
            "symbols": symbols,
            "symbol_count": len(symbols),
            "universe_path": str(path),
            "universe_sha256": sha,
            "canonical_membership_sha": canonical_membership_sha(symbols),
        }
    )
    if len(symbols) != EXPECTED_SYMBOLS:
        base["reason"] = f"symbol_count_{len(symbols)}_expected_{EXPECTED_SYMBOLS}"
        return base
    base["ok"] = True
    base["reason"] = ""
    return base


def validate_desired_payload(
    payload: Optional[dict[str, Any]],
    requested_trading_date: str,
) -> dict[str, Any]:
    """Fail-closed unless payload.trading_date == requested trading_date.

    trading_date is the SoT. mtime is ignored.
    """
    requested = str(requested_trading_date or "")
    if not payload:
        return {
            "ok": False,
            "rejected": True,
            "reason": "desired_universe_missing",
            "requested_trading_date": requested,
            "payload_trading_date": "",
            "allow_put": False,
            "symbols": [],
        }
    payload_day = str(payload.get("trading_date") or "")
    if not requested or payload_day != requested:
        return {
            "ok": False,
            "rejected": True,
            "reason": STALE_DESIRED_UNIVERSE,
            "requested_trading_date": requested,
            "payload_trading_date": payload_day,
            "allow_put": False,
            "symbols": [],
        }
    symbols = canonical_symbols(list(payload.get("symbols") or []))
    return {
        "ok": True,
        "rejected": False,
        "reason": "",
        "requested_trading_date": requested,
        "payload_trading_date": payload_day,
        "allow_put": True,
        "symbols": symbols,
        "generation": int(payload.get("generation") or 0),
        "position_symbols": canonical_symbols(list(payload.get("position_symbols") or [])),
        "source_path": str(payload.get("source_path") or ""),
        "source_sha256": str(payload.get("source_sha256") or ""),
        "source_trading_date": str(payload.get("source_trading_date") or payload_day),
    }


def bind_same_day_am_desired_universe(
    native_root: Path,
    trading_date: str,
    *,
    generation: Optional[int] = None,
    symbols: Optional[Sequence[str]] = None,
    source_path: str = "",
    source_sha256: str = "",
) -> dict[str, Any]:
    """Overwrite control-channel desired universe with same-day AM (or explicit) 50.

    Stale prior-day files are replaced. Never writes a mismatched trading_date.
    """
    from small_paper.ingress_control_channel import write_desired_universe

    day = str(trading_date)
    if symbols is None:
        loaded = load_am_canonical_50(native_root, day)
        if not loaded.get("ok"):
            return loaded
        symbols = list(loaded["symbols"])
        source_path = str(loaded.get("universe_path") or source_path)
        source_sha256 = str(loaded.get("universe_sha256") or source_sha256)
        membership = str(loaded.get("canonical_membership_sha") or "")
    else:
        symbols = canonical_symbols(symbols)
        membership = canonical_membership_sha(symbols)
        if len(symbols) != EXPECTED_SYMBOLS:
            return {
                "ok": False,
                "reason": f"symbol_count_{len(symbols)}_expected_{EXPECTED_SYMBOLS}",
                "trading_date": day,
                "symbols": symbols,
                "symbol_count": len(symbols),
            }
    written = write_desired_universe(
        native_root,
        symbols=list(symbols),
        generation=generation,
        trading_date=day,
        source_path=source_path,
        source_sha256=source_sha256,
        source_trading_date=day,
    )
    if written.get("rejected"):
        return written
    return {
        "ok": True,
        "reason": "",
        "trading_date": day,
        "symbols": list(symbols),
        "symbol_count": len(symbols),
        "universe_path": source_path,
        "universe_sha256": source_sha256,
        "canonical_membership_sha": membership,
        "desired": written,
        "source_trading_date": day,
        "source_path": source_path,
        "source_sha256": source_sha256,
    }
