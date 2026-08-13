"""DAY_FIXED_AM_RUNTIME_UNIVERSE_V1 — same-day AM CSV as Ingress registration SoT.

V1R Primary and Market Ingress registration share the same canonical 50.
Ingress must not generate an independent universe. Prior-day desired files
are fail-closed (STALE_DESIRED_UNIVERSE); mtime is never the SoT.

V8: after startup prebuild, membership is frozen as SAME_DAY_AM_FROZEN_UNIVERSE.
Runtime SoT is the freeze artifact (canonical set + SHA), not later CSV bytes.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.market_capture_registration import (
    load_symbols_from_universe_csv,
)
from small_paper.v1r_live_dual_lane import canonical_symbol_key
from small_paper.v1r_primary_runtime import UNIVERSE_CONTRACT

JST = ZoneInfo("Asia/Tokyo")

STALE_DESIRED_UNIVERSE = "STALE_DESIRED_UNIVERSE"
EXPECTED_SYMBOLS = 50
AM_CSV_NAME = "universe_core10_dynamic40_price_risk_am_{day}.csv"
FROZEN_CSV_NAME = "same_day_am_frozen_universe_{day}.csv"
FROZEN_JSON_NAME = "same_day_am_frozen_universe_{day}.json"

SAME_DAY_AM_FROZEN_AUTHORITY = "SAME_DAY_AM_FROZEN_UNIVERSE"
POST_BIND_UNIVERSE_MUTATION = "POST_BIND_UNIVERSE_MUTATION"
FROZEN_AM_UNIVERSE_MISMATCH = "FROZEN_AM_UNIVERSE_MISMATCH"
FROZEN_AM_UNIVERSE_SOURCE_DRIFT = "FROZEN_AM_UNIVERSE_SOURCE_DRIFT"
AM_UNIVERSE_REUSED_FROZEN = "AM_UNIVERSE_REUSED_FROZEN"
AM_UNIVERSE_FROZEN = "AM_UNIVERSE_FROZEN"


def am_csv_path(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "results" / "reports" / AM_CSV_NAME.format(day=str(trading_date))


def frozen_csv_path(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "results" / "reports" / FROZEN_CSV_NAME.format(day=str(trading_date))


def frozen_universe_path(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "runtime" / FROZEN_JSON_NAME.format(day=str(trading_date))


def frozen_audit_path(native_root: Path, trading_date: str) -> Path:
    return Path(native_root) / "data" / "market_capture" / str(trading_date) / "frozen_am_universe_audit.jsonl"


def frozen_summary_path(native_root: Path) -> Path:
    return Path(native_root) / "runtime" / "frozen_am_universe_summary.json"


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


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    fd, tmp = tempfile.mkstemp(prefix="am_frozen_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def load_frozen_summary(native_root: Path) -> dict[str, Any]:
    body = _read_json(frozen_summary_path(native_root))
    return {
        "post_bind_universe_rebuild_count": int(body.get("post_bind_universe_rebuild_count") or 0),
        "post_bind_universe_mutation_count": int(body.get("post_bind_universe_mutation_count") or 0),
        "last_event": str(body.get("last_event") or ""),
        "trading_date": str(body.get("trading_date") or ""),
    }


def _write_frozen_summary(native_root: Path, payload: dict[str, Any]) -> None:
    _atomic_write_json(frozen_summary_path(native_root), payload)


def append_frozen_universe_audit(
    native_root: Path,
    trading_date: str,
    event: str,
    **fields: Any,
) -> None:
    day = str(trading_date)
    path = frozen_audit_path(native_root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(JST).isoformat(timespec="seconds"),
        "event": event,
        "trading_date": day,
        **fields,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    summary = load_frozen_summary(native_root)
    if event == POST_BIND_UNIVERSE_MUTATION:
        summary["post_bind_universe_mutation_count"] = int(summary["post_bind_universe_mutation_count"]) + 1
    summary["last_event"] = event
    summary["trading_date"] = day
    summary["updated_at"] = rec["ts"]
    _write_frozen_summary(native_root, summary)


def note_post_bind_universe_mutation_attempt(native_root: Path, trading_date: str) -> dict[str, Any]:
    append_frozen_universe_audit(
        native_root,
        trading_date,
        POST_BIND_UNIVERSE_MUTATION,
        post_bind_universe_rebuild_count=0,
    )
    summary = load_frozen_summary(native_root)
    return {
        "ok": False,
        "reason": POST_BIND_UNIVERSE_MUTATION,
        "error": POST_BIND_UNIVERSE_MUTATION,
        "post_bind_universe_rebuild_count": 0,
        "post_bind_universe_mutation_count": int(summary.get("post_bind_universe_mutation_count") or 0),
    }


def _write_frozen_csv_from_symbols(path: Path, symbols: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, raw in enumerate(symbols):
        bare = str(raw).split(".")[0].split("@")[0]
        slot = "core" if i < 10 else "dynamic"
        rows.append(
            {
                "symbol": f"{bare}.T",
                "symbol_key": f"{bare}@1",
                "exchange": "1",
                "passed": "True",
                "source_bucket": "core10_discord" if slot == "core" else "vol_liq_dynamic40",
                "selected_reason": slot,
                "universe_slot": slot,
                "rank": str(i + 1),
                "am_pm_session": "am",
            }
        )
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def load_am_csv_from_disk(native_root: Path, trading_date: str) -> dict[str, Any]:
    """Read live AM CSV bytes. Not the V1R runtime membership SoT after freeze."""
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
        "authority": "",
        "source_drift": False,
        "present": path.is_file(),
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


def load_frozen_am_universe(native_root: Path, trading_date: str) -> dict[str, Any]:
    day = str(trading_date)
    path = frozen_universe_path(native_root, day)
    base: dict[str, Any] = {
        "ok": False,
        "present": path.is_file(),
        "authority": SAME_DAY_AM_FROZEN_AUTHORITY,
        "trading_date": day,
        "canonical_symbols": [],
        "canonical_membership_sha": "",
        "source_csv_path": "",
        "source_csv_sha": "",
        "frozen_csv_path": str(frozen_csv_path(native_root, day)),
        "built_at": "",
        "generation": 0,
        "id": "",
        "reason": "",
    }
    if not path.is_file():
        base["reason"] = "frozen_am_universe_missing"
        return base
    body = _read_json(path)
    if not body:
        base["reason"] = FROZEN_AM_UNIVERSE_MISMATCH
        return base
    body_day = str(body.get("trading_date") or "")
    symbols = canonical_symbols(list(body.get("canonical_symbols") or []))
    stored_sha = str(body.get("canonical_membership_sha") or "")
    recomputed = canonical_membership_sha(symbols) if symbols else ""
    base.update(
        {
            "canonical_symbols": symbols,
            "canonical_membership_sha": stored_sha,
            "source_csv_path": str(body.get("source_csv_path") or ""),
            "source_csv_sha": str(body.get("source_csv_sha") or ""),
            "frozen_csv_path": str(body.get("frozen_csv_path") or frozen_csv_path(native_root, day)),
            "built_at": str(body.get("built_at") or ""),
            "generation": int(body.get("generation") or 0),
            "id": str(body.get("id") or ""),
        }
    )
    if body_day != day:
        base["reason"] = FROZEN_AM_UNIVERSE_MISMATCH
        return base
    if len(symbols) != EXPECTED_SYMBOLS:
        base["reason"] = FROZEN_AM_UNIVERSE_MISMATCH
        return base
    if not stored_sha or stored_sha != recomputed:
        base["reason"] = FROZEN_AM_UNIVERSE_MISMATCH
        return base
    if str(body.get("authority") or "") not in ("", SAME_DAY_AM_FROZEN_AUTHORITY):
        base["reason"] = FROZEN_AM_UNIVERSE_MISMATCH
        return base
    base["ok"] = True
    base["reason"] = ""
    return base


def detect_frozen_source_csv_drift(native_root: Path, trading_date: str) -> dict[str, Any]:
    frozen = load_frozen_am_universe(native_root, trading_date)
    if not frozen.get("ok"):
        return {
            "ok": False,
            "drift": False,
            "reason": str(frozen.get("reason") or "frozen_am_universe_missing"),
        }
    src = Path(str(frozen.get("source_csv_path") or am_csv_path(native_root, trading_date)))
    expected = str(frozen.get("source_csv_sha") or "")
    if not src.is_file():
        return {
            "ok": False,
            "drift": True,
            "reason": FROZEN_AM_UNIVERSE_SOURCE_DRIFT,
            "expected_sha": expected,
            "actual_sha": "",
        }
    actual = file_sha256(src)
    if expected and actual != expected:
        return {
            "ok": False,
            "drift": True,
            "reason": FROZEN_AM_UNIVERSE_SOURCE_DRIFT,
            "expected_sha": expected,
            "actual_sha": actual,
        }
    return {
        "ok": True,
        "drift": False,
        "reason": "",
        "expected_sha": expected,
        "actual_sha": actual,
    }


def freeze_same_day_am_universe(
    native_root: Path,
    trading_date: str,
    *,
    symbols: Optional[Sequence[Any]] = None,
    source_path: str = "",
    source_sha256: str = "",
    generation: int = 1,
) -> dict[str, Any]:
    """Freeze same-day AM canonical50 once. Later rebuilds must not rewrite membership."""
    day = str(trading_date)
    existing = load_frozen_am_universe(native_root, day)
    if existing.get("present") and existing.get("ok"):
        incoming = canonical_symbols(symbols) if symbols is not None else list(existing.get("canonical_symbols") or [])
        incoming_sha = canonical_membership_sha(incoming) if incoming else str(existing.get("canonical_membership_sha") or "")
        if incoming and incoming_sha != str(existing.get("canonical_membership_sha") or ""):
            append_frozen_universe_audit(
                native_root,
                day,
                POST_BIND_UNIVERSE_MUTATION,
                existing_sha=existing.get("canonical_membership_sha"),
                attempted_sha=incoming_sha,
            )
            return {
                "ok": False,
                "reason": POST_BIND_UNIVERSE_MUTATION,
                "authority": SAME_DAY_AM_FROZEN_AUTHORITY,
                "trading_date": day,
                "canonical_symbols": list(existing.get("canonical_symbols") or []),
                "canonical_membership_sha": str(existing.get("canonical_membership_sha") or ""),
            }
        append_frozen_universe_audit(
            native_root,
            day,
            AM_UNIVERSE_REUSED_FROZEN,
            canonical_membership_sha=existing.get("canonical_membership_sha"),
            post_bind_universe_rebuild_count=0,
            post_bind_universe_mutation_count=0,
        )
        return {
            **existing,
            "ok": True,
            "reused": True,
            "reason": AM_UNIVERSE_REUSED_FROZEN,
        }
    if existing.get("present") and not existing.get("ok"):
        return {
            "ok": False,
            "reason": str(existing.get("reason") or FROZEN_AM_UNIVERSE_MISMATCH),
            "authority": SAME_DAY_AM_FROZEN_AUTHORITY,
            "trading_date": day,
        }

    if symbols is None:
        loaded = load_am_csv_from_disk(native_root, day)
        if not loaded.get("ok"):
            return loaded
        symbols = list(loaded["symbols"])
        source_path = str(loaded.get("universe_path") or source_path)
        source_sha256 = str(loaded.get("universe_sha256") or source_sha256)
    symbols = canonical_symbols(symbols)
    membership = canonical_membership_sha(symbols)
    if len(symbols) != EXPECTED_SYMBOLS:
        return {
            "ok": False,
            "reason": FROZEN_AM_UNIVERSE_MISMATCH,
            "trading_date": day,
            "canonical_symbols": symbols,
            "symbol_count": len(symbols),
        }
    src = Path(source_path) if source_path else am_csv_path(native_root, day)
    if not source_sha256 and src.is_file():
        source_sha256 = file_sha256(src)
        source_path = str(src)
    frozen_csv = frozen_csv_path(native_root, day)
    if src.is_file():
        frozen_csv.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, frozen_csv)
    else:
        _write_frozen_csv_from_symbols(frozen_csv, symbols)
    built_at = datetime.now(JST).isoformat(timespec="seconds")
    ident = f"am_frozen_{day}_{int(generation)}"
    payload = {
        "authority": SAME_DAY_AM_FROZEN_AUTHORITY,
        "trading_date": day,
        "canonical_symbols": list(symbols),
        "canonical_membership_sha": membership,
        "source_csv_path": str(source_path or src),
        "source_csv_sha": str(source_sha256),
        "frozen_csv_path": str(frozen_csv),
        "built_at": built_at,
        "generation": int(generation),
        "id": ident,
    }
    _atomic_write_json(frozen_universe_path(native_root, day), payload)
    _write_frozen_summary(
        native_root,
        {
            "post_bind_universe_rebuild_count": 0,
            "post_bind_universe_mutation_count": 0,
            "last_event": AM_UNIVERSE_FROZEN,
            "trading_date": day,
            "canonical_membership_sha": membership,
            "updated_at": built_at,
        },
    )
    append_frozen_universe_audit(
        native_root,
        day,
        AM_UNIVERSE_FROZEN,
        canonical_membership_sha=membership,
        source_csv_sha=source_sha256,
        generation=int(generation),
        id=ident,
        post_bind_universe_rebuild_count=0,
        post_bind_universe_mutation_count=0,
    )
    return {
        **payload,
        "ok": True,
        "present": True,
        "reused": False,
        "reason": "",
        "symbols": list(symbols),
        "symbol_count": len(symbols),
    }


def reuse_frozen_am_universe(
    *,
    native_root: Path,
    trading_date: str,
    repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Daily/pilot AM preparation: reuse frozen exact50. Never rebuild membership."""
    day = str(trading_date)
    frozen = load_frozen_am_universe(native_root, day)
    if not frozen.get("present"):
        return {"attempted": False, "reused": False, "ok": False, "reason": "frozen_am_universe_missing"}
    if not frozen.get("ok"):
        append_frozen_universe_audit(
            native_root,
            day,
            FROZEN_AM_UNIVERSE_MISMATCH,
            detail=frozen.get("reason"),
        )
        return {
            "attempted": True,
            "reused": False,
            "ok": False,
            "reason": str(frozen.get("reason") or FROZEN_AM_UNIVERSE_MISMATCH),
            "authority": SAME_DAY_AM_FROZEN_AUTHORITY,
            "post_bind_universe_rebuild_count": 0,
            "post_bind_universe_mutation_count": int(
                load_frozen_summary(native_root).get("post_bind_universe_mutation_count") or 0
            ),
        }
    drift = detect_frozen_source_csv_drift(native_root, day)
    if drift.get("drift"):
        append_frozen_universe_audit(
            native_root,
            day,
            FROZEN_AM_UNIVERSE_SOURCE_DRIFT,
            expected_sha=drift.get("expected_sha"),
            actual_sha=drift.get("actual_sha"),
            canonical_membership_sha=frozen.get("canonical_membership_sha"),
        )
        return {
            "attempted": True,
            "reused": True,
            "ok": False,
            "reason": FROZEN_AM_UNIVERSE_SOURCE_DRIFT,
            "authority": SAME_DAY_AM_FROZEN_AUTHORITY,
            "symbols": list(frozen.get("canonical_symbols") or []),
            "canonical_membership_sha": str(frozen.get("canonical_membership_sha") or ""),
            "post_bind_universe_rebuild_count": 0,
            "post_bind_universe_mutation_count": 0,
            "allow_put_new50": False,
        }
    frozen_csv = Path(str(frozen.get("frozen_csv_path") or frozen_csv_path(native_root, day)))
    root = Path(repo_root or native_root)
    try:
        am_rel = str(frozen_csv.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        am_rel = str(frozen_csv)
    append_frozen_universe_audit(
        native_root,
        day,
        AM_UNIVERSE_REUSED_FROZEN,
        canonical_membership_sha=frozen.get("canonical_membership_sha"),
        post_bind_universe_rebuild_count=0,
        post_bind_universe_mutation_count=0,
    )
    return {
        "attempted": True,
        "reused": True,
        "ok": True,
        "reason": AM_UNIVERSE_REUSED_FROZEN,
        "audit": AM_UNIVERSE_REUSED_FROZEN,
        "authority": SAME_DAY_AM_FROZEN_AUTHORITY,
        "am_csv": am_rel,
        "am_row_count": EXPECTED_SYMBOLS,
        "symbols": list(frozen.get("canonical_symbols") or []),
        "symbol_count": EXPECTED_SYMBOLS,
        "canonical_membership_sha": str(frozen.get("canonical_membership_sha") or ""),
        "universe_path": str(frozen_csv),
        "post_bind_universe_rebuild_count": 0,
        "post_bind_universe_mutation_count": 0,
        "price_risk_filter_enabled": True,
        "dynamic_price_risk_excluded_count": 0,
        "dynamic_price_risk_replacement_count": 0,
        "allow_put_new50": False,
    }


def load_am_canonical_50(native_root: Path, trading_date: str) -> dict[str, Any]:
    """Runtime AM membership SoT: frozen canonical50 when present, else live CSV.

    After freeze, later CSV overwrites do not change membership. SOURCE_DRIFT is
    reported separately; callers must not PUT the new CSV set.
    """
    day = str(trading_date)
    frozen = load_frozen_am_universe(native_root, day)
    if frozen.get("present"):
        symbols = list(frozen.get("canonical_symbols") or [])
        drift = detect_frozen_source_csv_drift(native_root, day) if frozen.get("ok") else {"drift": False}
        universe_path = str(frozen.get("frozen_csv_path") or frozen_csv_path(native_root, day))
        base: dict[str, Any] = {
            "ok": bool(frozen.get("ok")),
            "contract": UNIVERSE_CONTRACT,
            "trading_date": day,
            "symbols": symbols,
            "symbol_count": len(symbols),
            "universe_path": universe_path,
            "universe_sha256": str(frozen.get("source_csv_sha") or ""),
            "canonical_membership_sha": str(frozen.get("canonical_membership_sha") or ""),
            "authority": SAME_DAY_AM_FROZEN_AUTHORITY,
            "source_drift": bool(drift.get("drift")),
            "generation": int(frozen.get("generation") or 0),
            "id": str(frozen.get("id") or ""),
            "built_at": str(frozen.get("built_at") or ""),
            "source_csv_path": str(frozen.get("source_csv_path") or ""),
            "reason": "",
        }
        if not frozen.get("ok"):
            base["reason"] = str(frozen.get("reason") or FROZEN_AM_UNIVERSE_MISMATCH)
            return base
        if drift.get("drift"):
            base["reason"] = FROZEN_AM_UNIVERSE_SOURCE_DRIFT
            # Membership remains frozen; ok stays True so exact50 vs frozen still works.
            base["ok"] = True
            return base
        base["ok"] = True
        base["reason"] = ""
        return base
    loaded = load_am_csv_from_disk(native_root, day)
    loaded["authority"] = ""
    loaded["source_drift"] = False
    return loaded


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
    After freeze, desired is always the frozen canonical50.
    """
    from small_paper.ingress_control_channel import write_desired_universe

    day = str(trading_date)
    frozen = load_frozen_am_universe(native_root, day)
    if frozen.get("present"):
        if not frozen.get("ok"):
            return {
                "ok": False,
                "reason": str(frozen.get("reason") or FROZEN_AM_UNIVERSE_MISMATCH),
                "trading_date": day,
                "allow_put": False,
            }
        frozen_syms = list(frozen.get("canonical_symbols") or [])
        frozen_sha = str(frozen.get("canonical_membership_sha") or "")
        if symbols is not None:
            incoming = canonical_symbols(symbols)
            if canonical_membership_sha(incoming) != frozen_sha:
                append_frozen_universe_audit(
                    native_root,
                    day,
                    FROZEN_AM_UNIVERSE_MISMATCH,
                    attempted_sha=canonical_membership_sha(incoming),
                    frozen_sha=frozen_sha,
                )
                return {
                    "ok": False,
                    "reason": FROZEN_AM_UNIVERSE_MISMATCH,
                    "trading_date": day,
                    "symbols": frozen_syms,
                    "symbol_count": len(frozen_syms),
                    "canonical_membership_sha": frozen_sha,
                    "allow_put": False,
                    "allow_put_new50": False,
                }
        symbols = frozen_syms
        source_path = str(frozen.get("frozen_csv_path") or frozen.get("source_csv_path") or source_path)
        source_sha256 = str(frozen.get("source_csv_sha") or source_sha256)
        membership = frozen_sha
    elif symbols is None:
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
        "authority": SAME_DAY_AM_FROZEN_AUTHORITY if frozen.get("present") else "",
        "allow_put_new50": False if frozen.get("present") else True,
    }
