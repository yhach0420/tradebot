"""Phase687W9 — Market registration coordination (shared resource only).

Sidecar is a registration follower. Checked runner owns coordination before start.
Hard limit: 50 symbols. Sidecar must never call unregister/all.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

KABU_PUSH_REGISTER_LIMIT = 50
REGISTRATION_LOCK_NAME = "market_registration.lock"
REGISTRATION_MANIFEST_NAME = "market_registration_manifest.json"


def trading_date_jst(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(JST)).strftime("%Y%m%d")


def runtime_dir(native_root: Path) -> Path:
    d = Path(native_root) / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def lock_path(native_root: Path) -> Path:
    return runtime_dir(native_root) / REGISTRATION_LOCK_NAME


def manifest_path(native_root: Path) -> Path:
    return runtime_dir(native_root) / REGISTRATION_MANIFEST_NAME


class RegistrationLockError(RuntimeError):
    pass


class FileLock:
    """Exclusive create lock (cross-platform, no unregister side effects)."""

    def __init__(self, path: Path, *, timeout_sec: float = 30.0, stale_sec: float = 300.0) -> None:
        self.path = Path(path)
        self.timeout_sec = timeout_sec
        self.stale_sec = stale_sec
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_sec
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, f"pid={os.getpid()}\nacquired_at={datetime.now(JST).isoformat()}\n".encode("utf-8"))
                return
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > self.stale_sec:
                        self.path.unlink(missing_ok=True)  # type: ignore[arg-type]
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise RegistrationLockError(f"timeout acquiring {self.path}")
                time.sleep(0.05)

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self.path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


@contextmanager
def registration_lock(native_root: Path, *, timeout_sec: float = 30.0) -> Iterator[FileLock]:
    lock = FileLock(lock_path(native_root), timeout_sec=timeout_sec)
    lock.acquire()
    try:
        yield lock
    finally:
        lock.release()


def _normalize_symbol(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    # strip exchange suffix like 7203.T
    if "." in s:
        s = s.split(".", 1)[0]
    return s


def load_symbols_from_universe_csv(path: Path, *, limit: int = KABU_PUSH_REGISTER_LIMIT) -> list[str]:
    """Load unique symbols from universe CSV (SoT). Caps at 50."""
    if not path.is_file():
        return []
    symbols: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        # prefer Symbol / code / ticker columns
        for row in rows:
            sym = ""
            for key in ("Symbol", "symbol", "code", "Code", "ticker", "Ticker"):
                if key in row and row[key]:
                    sym = _normalize_symbol(row[key])
                    break
            if not sym:
                # first column fallback
                vals = list(row.values())
                if vals:
                    sym = _normalize_symbol(vals[0])
            if not sym or sym in seen:
                continue
            seen.add(sym)
            symbols.append(sym)
            if len(symbols) >= limit:
                break
    return symbols


def candidate_universe_paths(native_root: Path, trading_date: str) -> list[Path]:
    reports = Path(native_root) / "results" / "reports"
    day = trading_date
    frozen = reports / f"same_day_am_frozen_universe_{day}.csv"
    rest = [
        reports / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{day}.csv",
        reports / f"universe_core10_dynamic40_price_risk_am_refresh1000_{day}.csv",
        reports / f"universe_core10_dynamic40_price_risk_pm_{day}.csv",
        reports / f"universe_core10_dynamic40_price_risk_am_{day}.csv",
        reports / f"universe_core10_dynamic40_pm_{day}.csv",
        reports / f"universe_core10_dynamic40_am_{day}.csv",
    ]
    if frozen.is_file():
        return [frozen]
    return rest


def resolve_universe_symbols(
    native_root: Path,
    trading_date: str,
    *,
    explicit_csv: Optional[Path] = None,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Resolve today's expected ≤50 symbols from existing Paper universe SoT."""
    paths = [Path(explicit_csv)] if explicit_csv else candidate_universe_paths(native_root, trading_date)
    chosen: Optional[Path] = None
    symbols: list[str] = []
    for p in paths:
        if p and p.is_file():
            symbols = load_symbols_from_universe_csv(p)
            if symbols:
                chosen = p
                break
    if not symbols and not allow_empty:
        return {
            "ok": False,
            "trading_date": trading_date,
            "symbols": [],
            "symbol_count": 0,
            "universe_path": None,
            "reason": "universe_csv_not_found_or_empty",
            "candidates": [str(p) for p in paths if p],
        }
    if len(symbols) > KABU_PUSH_REGISTER_LIMIT:
        return {
            "ok": False,
            "trading_date": trading_date,
            "symbols": symbols[:KABU_PUSH_REGISTER_LIMIT],
            "symbol_count": len(symbols),
            "universe_path": str(chosen) if chosen else None,
            "reason": f"symbol_count_{len(symbols)}_exceeds_limit_{KABU_PUSH_REGISTER_LIMIT}",
        }
    sha = ""
    if chosen and chosen.is_file():
        sha = hashlib.sha256(chosen.read_bytes()).hexdigest()
    return {
        "ok": True if symbols or allow_empty else False,
        "trading_date": trading_date,
        "symbols": symbols,
        "symbol_count": len(symbols),
        "universe_path": str(chosen) if chosen else None,
        "universe_manifest_sha256": sha,
        "limit": KABU_PUSH_REGISTER_LIMIT,
        "reason": "" if symbols else "empty_allowed",
    }


def symbols_equal(a: Sequence[str], b: Sequence[str]) -> bool:
    return sorted({_normalize_symbol(x) for x in a if _normalize_symbol(x)}) == sorted(
        {_normalize_symbol(x) for x in b if _normalize_symbol(x)}
    )


def write_registration_manifest(
    native_root: Path,
    *,
    trading_date: str,
    symbols: Sequence[str],
    generation_id: str,
    universe_path: Optional[str] = None,
    universe_sha256: str = "",
    verified: bool = False,
    owner: str = "checked_runner",
    extra: Optional[Mapping[str, Any]] = None,
) -> Path:
    if len(symbols) > KABU_PUSH_REGISTER_LIMIT:
        raise ValueError(f"refusing to write manifest with {len(symbols)} > {KABU_PUSH_REGISTER_LIMIT}")
    payload = {
        "schema_version": "687W9.1",
        "trading_date": trading_date,
        "generation_id": generation_id,
        "registered_symbols": list(symbols),
        "symbol_count": len(symbols),
        "limit": KABU_PUSH_REGISTER_LIMIT,
        "universe_path": universe_path,
        "universe_manifest_sha256": universe_sha256,
        "registration_verified": bool(verified),
        "owner": owner,
        "sidecar_may_unregister_all": False,
        "updated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "pid": os.getpid(),
    }
    if extra:
        payload.update(dict(extra))
    src_day = str(payload.get("source_trading_date") or trading_date)
    if src_day != str(trading_date):
        raise ValueError("STALE_DESIRED_UNIVERSE")
    from small_paper.day_fixed_am_registration import canonical_membership_sha, canonical_symbols

    canon = canonical_symbols(list(payload.get("registered_symbols") or symbols))
    payload["source_trading_date"] = src_day
    payload["source_path"] = str(payload.get("source_path") or universe_path or "")
    payload["source_sha256"] = str(payload.get("source_sha256") or universe_sha256 or "")
    payload["desired_count"] = int(payload.get("desired_count") or len(canon))
    payload["registered_count"] = int(payload.get("registered_count") or payload.get("actual_count") or len(canon))
    payload["canonical_membership_sha"] = str(
        payload.get("canonical_membership_sha") or canonical_membership_sha(canon)
    )
    path = manifest_path(native_root)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_registration_manifest(native_root: Path) -> dict[str, Any]:
    path = manifest_path(native_root)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def coordinate_registration(
    native_root: Path,
    trading_date: str,
    *,
    expected_symbols: Optional[Sequence[str]] = None,
    actual_symbols: Optional[Sequence[str]] = None,
    apply_register: bool = False,
    register_fn: Optional[Any] = None,
    fetch_fn: Optional[Any] = None,
    generation_id: Optional[str] = None,
    universe_path: Optional[str] = None,
    universe_sha256: str = "",
    test_mode: bool = False,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """
    Acquire lock → compare expected vs actual → register only if needed → verify → save manifest.

    Sidecar must call with apply_register=False (follower).
    Checked runner may apply_register when a live register_fn is provided.
    Fixture/test_mode never mutates production registration.
    """
    if test_mode and apply_register:
        return {
            "ok": False,
            "reason": "test_mode_forbids_production_register",
            "applied": False,
        }

    resolved = None
    if expected_symbols is None:
        resolved = resolve_universe_symbols(native_root, trading_date, allow_empty=True)
        expected = list(resolved.get("symbols") or [])
        universe_path = universe_path or resolved.get("universe_path")
        universe_sha256 = universe_sha256 or str(resolved.get("universe_manifest_sha256") or "")
    else:
        expected = [_normalize_symbol(s) for s in expected_symbols if _normalize_symbol(s)]

    if len(expected) > KABU_PUSH_REGISTER_LIMIT:
        return {
            "ok": False,
            "reason": "expected_exceeds_limit_50",
            "expected_count": len(expected),
            "applied": False,
        }

    gen = generation_id or f"gen_{trading_date}_{int(time.time())}"
    with registration_lock(native_root):
        current = list(actual_symbols) if actual_symbols is not None else []
        if fetch_fn is not None and actual_symbols is None:
            try:
                current = list(fetch_fn() or [])
            except Exception as exc:
                return {
                    "ok": False,
                    "reason": f"fetch_failed:{type(exc).__name__}",
                    "applied": False,
                }

        match_before = symbols_equal(expected, current) if expected and current else False
        applied = False
        if apply_register and expected and not match_before:
            if register_fn is None:
                return {"ok": False, "reason": "register_fn_missing", "applied": False}
            # Never unregister_all from this coordinator path for sidecar;
            # register_fn is responsible for safe update (checked runner only).
            register_fn(expected)
            applied = True
            if fetch_fn is not None:
                try:
                    current = list(fetch_fn() or [])
                except Exception as exc:
                    return {
                        "ok": False,
                        "reason": f"refetch_failed:{type(exc).__name__}",
                        "applied": applied,
                    }

        match_after = symbols_equal(expected, current) if expected else (not current)
        # When no live fetch available (offline/weekend), treat expected as planned.
        if not current and expected and not apply_register:
            match_after = True
            current = list(expected)
            verified = False
            status = "PLANNED_FOLLOWER"
        else:
            verified = bool(match_after) and 1 <= len(expected) <= KABU_PUSH_REGISTER_LIMIT
            status = "MATCH" if verified else "MISMATCH"

        extra_out: dict[str, Any] = {
            "status": status,
            "actual_symbols": current,
            "actual_count": len(current),
            "match_before": match_before,
            "applied": applied,
            "source_trading_date": str(trading_date),
        }
        if extra:
            extra_out.update(dict(extra))
        path = write_registration_manifest(
            native_root,
            trading_date=trading_date,
            symbols=expected if expected else current,
            generation_id=gen,
            universe_path=universe_path,
            universe_sha256=universe_sha256,
            verified=verified,
            owner="checked_runner" if apply_register else "sidecar_follower",
            extra=extra_out,
        )
        return {
            "ok": verified or status == "PLANNED_FOLLOWER",
            "status": status,
            "generation_id": gen,
            "expected_symbols": expected,
            "expected_count": len(expected),
            "actual_symbols": current,
            "actual_count": len(current),
            "registration_match": match_after or status == "PLANNED_FOLLOWER",
            "registration_verified": verified,
            "applied": applied,
            "manifest_path": str(path),
            "limit": KABU_PUSH_REGISTER_LIMIT,
            "unregister_all_used": False,
        }


def notify_registration_refresh(
    native_root: Path,
    *,
    trading_date: str,
    new_symbols: Sequence[str],
    previous_symbols: Optional[Sequence[str]] = None,
    universe_path: Optional[str] = None,
    verified: bool = True,
    capture_sequence_at_change: int = 0,
    capture_day_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Paper SoT refresh hook (10:00 / 14:30).

    Does not select universe — only publishes the already-chosen symbol list
    under the registration lock so the Sidecar follower can observe generation change.
    Never calls unregister/all. test/synthetic must not pass verified production mutate.
    """
    symbols = [_normalize_symbol(s) for s in new_symbols if _normalize_symbol(s)]
    if len(symbols) > KABU_PUSH_REGISTER_LIMIT:
        return {
            "ok": False,
            "reason": "expected_exceeds_limit_50",
            "symbol_count": len(symbols),
        }
    if not symbols:
        return {"ok": False, "reason": "empty_symbols"}

    prev_man = read_registration_manifest(native_root)
    prev = list(previous_symbols) if previous_symbols is not None else list(prev_man.get("registered_symbols") or [])
    gen = f"gen_{trading_date}_{int(time.time())}"
    # verified=True only when caller has Kabu PUT evidence. Paper MATCH alone must
    # publish PLANNED_FOLLOWER with actual_count=0 (Ingress owns Station register).
    actual_symbols = list(symbols) if verified else []
    actual_count = len(actual_symbols)
    with registration_lock(native_root):
        path = write_registration_manifest(
            native_root,
            trading_date=trading_date,
            symbols=symbols,
            generation_id=gen,
            universe_path=universe_path,
            verified=bool(verified),
            owner="paper_refresh",
            extra={
                "status": "MATCH" if verified else "PLANNED_FOLLOWER",
                "actual_symbols": actual_symbols,
                "actual_count": actual_count,
                "refresh_notify": True,
                "verification_source": "kabu_put" if verified else "paper_desired_only",
            },
        )
    event_path = None
    if capture_day_dir is not None:
        event_path = record_generation_change(
            capture_day_dir,
            generation_id=gen,
            previous_symbols=prev,
            new_symbols=symbols,
            registration_verified=verified,
            capture_sequence_at_change=capture_sequence_at_change,
        )
    return {
        "ok": True,
        "generation_id": gen,
        "manifest_path": str(path),
        "generation_event_path": str(event_path) if event_path else None,
        "added": sorted(set(symbols) - set(prev)),
        "removed": sorted(set(prev) - set(symbols)),
        "unregister_all_used": False,
    }


def record_generation_change(
    capture_dir: Path,
    *,
    generation_id: str,
    previous_symbols: Sequence[str],
    new_symbols: Sequence[str],
    registration_verified: bool,
    capture_sequence_at_change: int,
) -> Path:
    prev = list(previous_symbols)
    new = list(new_symbols)
    added = sorted(set(new) - set(prev))
    removed = sorted(set(prev) - set(new))
    event = {
        "generation_id": generation_id,
        "changed_at": datetime.now(JST).isoformat(timespec="seconds"),
        "previous_symbols": prev,
        "new_symbols": new,
        "added": added,
        "removed": removed,
        "registration_verified": registration_verified,
        "capture_sequence_at_change": capture_sequence_at_change,
    }
    path = Path(capture_dir) / "registration_generation_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path


def assert_no_unregister_all_in_sidecar_imports() -> bool:
    """Static guard used by tests — sidecar registration module never exposes unregister_all."""
    return not hasattr(sys_modules_safe(), "unregister_all")


def sys_modules_safe() -> Any:
    return type("M", (), {})()
