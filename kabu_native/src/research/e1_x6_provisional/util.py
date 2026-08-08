"""Shared helpers for E1_X6 provisional pipeline."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def repo_root() -> Path:
    # This file: kabu_native/src/research/e1_x6_provisional/util.py
    return Path(__file__).resolve().parents[4]


def native_root() -> Path:
    return repo_root() / "kabu_native"


def temp_work_root(run_id: str) -> Path:
    p = Path(tempfile.gettempdir()) / "e1x6_prov_work" / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def norm_cache_dir(pass_name: Optional[str] = None) -> Path:
    """Normalization cache root.

    Pipeline A/B isolation: pass pass_name='run_a'|'run_B' for separate caches.
    Sequential A then B is preferred on ~16GB machines to avoid OOM.
    """
    base = Path(tempfile.gettempdir()) / "e1x6_norm_cache"
    if pass_name:
        p = base / str(pass_name)
    else:
        p = base
    p.mkdir(parents=True, exist_ok=True)
    return p


_PROGRESS_MODE = "provisional"


def set_progress_mode(mode: str) -> None:
    """mode: 'provisional' | 'final' — selects progress log filename."""
    global _PROGRESS_MODE
    _PROGRESS_MODE = "final" if mode == "final" else "provisional"


def progress_log_path(*, final: Optional[bool] = None) -> Path:
    use_final = (_PROGRESS_MODE == "final") if final is None else bool(final)
    name = "e1x6_final_progress.log" if use_final else "e1x6_prov_progress.log"
    return Path(tempfile.gettempdir()) / name


def progress(msg: str) -> None:
    # Avoid UnicodeEncodeError on Windows cp932 consoles
    safe = str(msg).encode("ascii", "replace").decode("ascii")
    line = f"{datetime.now(JST).isoformat()} | {safe}"
    print(line, flush=True)
    try:
        with open(progress_log_path(), "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(JST).isoformat()} | {msg}\n")
    except Exception:
        pass


def new_run_id() -> str:
    now = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    suffix = hashlib.sha256(f"{now}|{os.getpid()}".encode()).hexdigest()[:8]
    return f"e1x6_prov_{now}_{suffix}"


def new_final_run_id() -> str:
    now = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    suffix = hashlib.sha256(f"final|{now}|{os.getpid()}".encode()).hexdigest()[:8]
    return f"e1x6_final_{now}_{suffix}"


def stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(f"not JSON serializable: {type(o)}")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_text(stable_json(obj))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_replace_dir_files(staging: Path, dest: Path, names: tuple[str, ...]) -> None:
    """Move only the named artifacts into dest, replacing prior copies."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        src = staging / name
        if not src.is_file():
            raise FileNotFoundError(f"missing staged artifact: {src}")
        target = dest / name
        tmp = dest / f".{name}.tmp"
        data = src.read_bytes()
        tmp.write_bytes(data)
        os.replace(tmp, target)


def parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=JST)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return dt.replace(tzinfo=JST) if dt.tzinfo is None else dt.astimezone(JST)
    except Exception:
        return None


def am_pm_of(ts: datetime) -> str:
    hm = ts.hour * 60 + ts.minute
    am_lo = 9 * 60 + 3
    am_hi = 11 * 60 + 25
    pm_lo = 12 * 60 + 33
    pm_hi = 15 * 60 + 23
    if am_lo <= hm <= am_hi:
        return "AM"
    if pm_lo <= hm <= pm_hi:
        return "PM"
    if hm < pm_lo:
        return "AM" if hm < 12 * 60 else "LUNCH"
    return "AFTER"


def expected_window_iso(day: str, am_pm: str) -> dict[str, str]:
    from research.e1_x6_provisional.constants import AM_EXPECTED, PM_EXPECTED

    y, m, d = int(day[:4]), int(day[4:6]), int(day[6:8])
    start_hm, end_hm = AM_EXPECTED if am_pm == "AM" else PM_EXPECTED
    sh, sm = map(int, start_hm.split(":"))
    eh, em = map(int, end_hm.split(":"))
    start = datetime(y, m, d, sh, sm, tzinfo=JST)
    end = datetime(y, m, d, eh, em, tzinfo=JST)
    return {"start": start.isoformat(), "end": end.isoformat()}


def coverage_ratio(actual_start: Optional[datetime], actual_end: Optional[datetime], expected: Mapping[str, str]) -> Optional[float]:
    e0 = parse_ts(expected["start"])
    e1 = parse_ts(expected["end"])
    if not e0 or not e1 or not actual_start or not actual_end:
        return None
    exp_sec = max((e1 - e0).total_seconds(), 1.0)
    overlap0 = max(actual_start, e0)
    overlap1 = min(actual_end, e1)
    if overlap1 <= overlap0:
        return 0.0
    return float((overlap1 - overlap0).total_seconds() / exp_sec)


def norm_sym(s: str) -> str:
    s = str(s or "").strip()
    return s if not s or s.endswith(".T") else f"{s}.T"


def summarize_pnls(pnls: list[float]) -> dict[str, Any]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    draws = [p for p in pnls if p == 0]
    total = float(sum(pnls)) if pnls else 0.0
    if losses:
        pf: Optional[float] = sum(wins) / abs(sum(losses))
        pf_status = "OK"
    elif wins:
        pf = None
        pf_status = "NO_LOSS"
    else:
        pf = None
        pf_status = "EMPTY"
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return {
        "n": len(pnls),
        "pnl": total,
        "pf": pf,
        "pf_status": pf_status,
        "wins": len(wins),
        "losses": len(losses),
        "draws": len(draws),
        "max_dd": max_dd,
        "pnl_ex_top1_trade": (total - max(pnls)) if pnls else 0.0,
    }
