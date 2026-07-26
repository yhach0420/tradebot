"""File-based control channel: Paper Universe Manager → Ingress registration.

Also carries DEMO inject / control commands for cross-process E2E
(never used for live Kabu PUSH).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from small_paper.market_ingress_protocol import now_iso


def desired_universe_path(native_root: Path) -> Path:
    return Path(native_root) / "runtime" / "ingress_desired_universe.json"


def demo_inject_path(native_root: Path) -> Path:
    return Path(native_root) / "runtime" / "ingress_demo_inject.jsonl"


def demo_control_path(native_root: Path) -> Path:
    return Path(native_root) / "runtime" / "ingress_demo_control.jsonl"


def write_desired_universe(
    native_root: Path,
    *,
    symbols: list[str],
    position_symbols: Optional[list[str]] = None,
    generation: Optional[int] = None,
    trading_date: str = "",
) -> dict[str, Any]:
    path = desired_universe_path(native_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    gen = int(generation if generation is not None else time.time())
    payload = {
        "generation": gen,
        "symbols": [str(s).split(".")[0] for s in symbols],
        "position_symbols": [str(s).split(".")[0] for s in (position_symbols or [])],
        "trading_date": trading_date,
        "updated_at": now_iso(),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return payload


def read_desired_universe(native_root: Path) -> Optional[dict[str, Any]]:
    path = desired_universe_path(native_root)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def append_demo_inject(native_root: Path, payloads: list[dict[str, Any]]) -> int:
    """Append DEMO_KABU_PUSH payloads for synthetic Ingress to drain."""
    path = demo_inject_path(native_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as fh:
        for p in payloads:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
            n += 1
    return n


def drain_demo_inject(native_root: Path, *, max_rows: int = 500) -> list[dict[str, Any]]:
    """Drain pending demo inject rows (file rename handshake)."""
    import os

    path = demo_inject_path(native_root)
    if not path.is_file() or path.stat().st_size == 0:
        return []
    work = path.with_suffix(".drain")
    try:
        os.replace(str(path), str(work))
    except Exception:
        return []
    try:
        lines = work.read_text(encoding="utf-8").splitlines()
        take = lines[:max_rows]
        rest = lines[max_rows:]
        if rest:
            with path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(rest) + "\n")
        out: list[dict[str, Any]] = []
        for ln in take:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out
    finally:
        try:
            work.unlink(missing_ok=True)
        except Exception:
            pass


def append_demo_control(native_root: Path, cmd: str, **extra: Any) -> None:
    path = demo_control_path(native_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"cmd": str(cmd), "at": now_iso(), **extra}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def drain_demo_control(native_root: Path) -> list[dict[str, Any]]:
    import os

    path = demo_control_path(native_root)
    if not path.is_file() or path.stat().st_size == 0:
        return []
    work = path.with_suffix(".drain")
    try:
        os.replace(str(path), str(work))
    except Exception:
        return []
    try:
        out: list[dict[str, Any]] = []
        for ln in work.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out
    finally:
        try:
            work.unlink(missing_ok=True)
        except Exception:
            pass
