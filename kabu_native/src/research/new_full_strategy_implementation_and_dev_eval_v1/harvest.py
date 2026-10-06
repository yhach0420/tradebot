"""DEV-only Full Causal harvest. Fills only. No PnL. Holdout/Stress sealed."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from _p1_inventory import resolve_universe
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir, iter_push
from research.new_full_strategy_implementation_and_dev_eval_v1 import (
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    BURNED_HOLDOUT_DAYS,
    STRESS_DAYS,
)
from research.new_full_strategy_implementation_and_dev_eval_v1.engine import SessionEngine
from research.new_full_strategy_implementation_and_dev_eval_v1.isolation import CACHE, TODAY

try:
    import orjson

    def _loads(raw: str) -> dict[str, Any]:
        got = orjson.loads(raw)
        return got if isinstance(got, dict) else {}

except Exception:
    def _loads(raw: str) -> dict[str, Any]:
        got = json.loads(raw)
        return got if isinstance(got, dict) else {}


AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "STRESS_FILE_OPEN_N": 0,
    "FUTURE_DATA_N": 0,
    "CAPTURE_DISCOVERED_UNIVERSE_N": 0,
}


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def assert_dev_day(day: str) -> None:
    d = str(day)
    if d != d[:8] or d > str(MAX_RESEARCH_DATE):
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FUTURE_OR_BEYOND_MAX {d}")
    if d in BURNED_HOLDOUT_DAYS:
        AUDIT["HOLDOUT_BURNED_READ_N"] += 1
        raise RuntimeError(f"HOLDOUT_READ {d}")
    if d in STRESS_DAYS:
        AUDIT["STRESS_READ_N"] += 1
        raise RuntimeError(f"STRESS_READ {d}")
    if d in FORBIDDEN_INPUT_DAYS or d >= "20260903" or d == str(TODAY):
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FORBIDDEN_DAY {d}")
    if d not in DEVELOPMENT_DAYS:
        raise RuntimeError(f"NOT_DEV_DAY {d}")


def cache_path(day: str, *, exclude: str = "") -> Path:
    tag = f"ex_{exclude}" if exclude else "base"
    return CACHE / f"day_{day}_{tag}.json"


def harvest_day(day: str, *, exclude_symbol: str = "", use_cache: bool = True) -> dict[str, Any]:
    assert_dev_day(day)
    path = cache_path(day, exclude=str(exclude_symbol or ""))
    if use_cache:
        prev = _load(path)
        if prev.get("date") == str(day) and prev.get("trades") is not None:
            return prev
    cap = find_capture_dir(day)
    if cap is None:
        raise RuntimeError(f"NO_CAPTURE {day}")
    uni = resolve_universe(day, cap)
    if not uni.get("resolved"):
        raise RuntimeError(f"UNIVERSE_UNRESOLVED {day} {uni.get('reason')}")
    symbols = [_bare(s) for s in list(uni.get("symbols") or []) if _bare(s)]
    src = str(uni.get("source") or "")
    if src.startswith("capture") or "symbols_seen" in src:
        AUDIT["CAPTURE_DISCOVERED_UNIVERSE_N"] += 1
        raise RuntimeError(f"CAPTURE_DISCOVERED_UNIVERSE {day}")
    ex = _bare(exclude_symbol)
    if ex:
        symbols = [s for s in symbols if s != ex]
    eng = SessionEngine(day, symbols, debug=False)
    n_rec = 0
    t0 = time.time()
    print(f"HARVEST_DAY_START {day} universe_n={len(symbols)} exclude={ex or '-'}", flush=True)
    for rec in iter_push(cap):
        n_rec += 1
        eng.ingest(rec)
        if n_rec % 200000 == 0:
            print(f"HARVEST_DAY_PROG {day} n={n_rec} sec={time.time()-t0:.1f}", flush=True)
    eng.finish()
    out = eng.result()
    out["universe"] = symbols
    out["universe_source"] = src
    out["exclude_symbol"] = ex
    out["record_n"] = n_rec
    out["elapsed_sec"] = float(time.time() - t0)
    out["capture_path"] = str(cap)
    _dump(path, out)
    return out


def harvest_days(days: tuple[str, ...] | list[str], *, exclude_symbol: str = "", use_cache: bool = True) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    unfilled: list[dict[str, Any]] = []
    flags_sum: dict[str, int] = {}
    for day in days:
        one = harvest_day(str(day), exclude_symbol=exclude_symbol, use_cache=use_cache)
        rows.append(
            {
                "date": one.get("date"),
                "trade_n": len(one.get("trades") or []),
                "unfilled_n": len(one.get("unfilled_session_exits") or []),
                "universe_n": one.get("universe_n"),
                "universe_source": one.get("universe_source"),
                "record_n": one.get("record_n"),
                "elapsed_sec": one.get("elapsed_sec"),
                "flags": one.get("flags"),
            }
        )
        trades.extend(list(one.get("trades") or []))
        unfilled.extend(list(one.get("unfilled_session_exits") or []))
        for k, v in dict(one.get("flags") or {}).items():
            flags_sum[str(k)] = int(flags_sum.get(str(k), 0) or 0) + int(v or 0)
    return {
        "days": list(days),
        "exclude_symbol": _bare(exclude_symbol),
        "day_rows": rows,
        "trades": trades,
        "unfilled_session_exits": unfilled,
        "flags": flags_sum,
        "AUDIT": dict(AUDIT),
    }


def structural_integrity(pack: dict[str, Any]) -> dict[str, Any]:
    flags = dict(pack.get("flags") or {})
    walk = int(flags.get("WALKBACK_SESSION_EXIT_N") or 0)
    after = int(flags.get("ENTRY_FILL_AFTER_FLATTEN_N") or 0)
    dup = int(flags.get("DUPLICATE_EXIT_FILL_N") or 0)
    cpt = int(flags.get("CURRENT_PRICE_TIME_AS_ARRIVAL_N") or 0)
    disc = int((pack.get("AUDIT") or {}).get("CAPTURE_DISCOVERED_UNIVERSE_N") or 0)
    leak = int((pack.get("AUDIT") or {}).get("HOLDOUT_BURNED_READ_N") or 0) + int(
        (pack.get("AUDIT") or {}).get("STRESS_READ_N") or 0
    ) + int((pack.get("AUDIT") or {}).get("FUTURE_DATA_N") or 0)
    ok = walk == 0 and after == 0 and dup == 0 and cpt == 0 and disc == 0 and leak == 0
    return {
        "WALKBACK_SESSION_EXIT_N": walk,
        "ENTRY_FILL_AFTER_FLATTEN_N": after,
        "DUPLICATE_EXIT_FILL_N": dup,
        "CURRENT_PRICE_TIME_AS_ARRIVAL_N": cpt,
        "CAPTURE_DISCOVERED_UNIVERSE_N": disc,
        "LEAKAGE_N": leak,
        "HARVEST_STRUCTURAL_PASS": bool(ok),
    }
