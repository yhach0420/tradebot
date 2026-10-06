"""One-day causal replay: REFERENCE + isolated extras + AUGMENTED + UNIFORM_10 portfolios."""
from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_10min_opportunity.grids import (
    augmented_tuples,
    phase_a_added_tuples,
    tail_tuples,
    tod_family,
    uniform10_new_tuples,
    uniform10_tuples,
)
from research.anchor_timing_robustness.grid import hm_epoch, hm_label, session_of_epoch
from research.anchor_timing_robustness.replay import (
    _attach_path_stats,
    _isolated_trades,
    _pop_webhooks,
    _portfolio_pack,
)
from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day
from research.fixed_anchor_mechanism_audit_p3_0.diagnostic import score_universe_at
from research.fixed_anchor_mechanism_audit_p3_0.engine import P3Engine
from research.fixed_anchor_mechanism_audit_p3_0.replay import _Discard
from small_paper.v1r_live_dual_lane import session_end_for_position


def _session_fallback(day: str, h: int, m: int, t0: float) -> str:
    sess = session_of_epoch(day, t0)
    if sess:
        return sess
    return "AM" if h < 12 else "PM"


def _stream_grid(
    *,
    day: str,
    capture: Path,
    universe: list[str],
    variant: str,
    fire_mode: str,
    allowed_hm: Optional[tuple[tuple[int, int], ...]],
) -> tuple[Any, Any, dict[str, Any]]:
    eng, dual = _boot(universe, P3Engine)
    if dual is None or not eng.ready:
        return None, None, {
            "ok": False,
            "date": day,
            "variant": variant,
            "blocker": getattr(eng, "fail_reason", "dual_unavailable"),
        }
    eng.offset_sec = 0
    eng.allowed_hm = allowed_hm
    eng.fire_mode = fire_mode
    eng.notify_enabled = False
    eng.ingest_audit = _Discard()  # type: ignore[assignment]
    events_n, last_et = _stream_day(day, capture, eng, dual)
    eng._harvest(eng.events)
    packed = _portfolio_pack(
        eng, dual, day=day, variant=variant, offset_sec=0, events_n=events_n, last_et=last_et
    )
    admits = list(getattr(eng, "a_admits", None) or [])
    packed["selected_n"] = len(admits)
    packed["unique_selected_symbols"] = len({str(a.get("symbol") or "") for a in admits})
    packed["admits"] = [
        {
            "date": day,
            "anchor": a.get("anchor"),
            "symbol": a.get("symbol"),
            "rank": a.get("rank"),
            "score": a.get("score") if a.get("score") is not None else a.get("alloc_score"),
        }
        for a in admits
    ]
    packed["tod_family_trades"] = _tod_counts(packed.get("trades") or [])
    return eng, dual, packed


def _tod_counts(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[float]] = {"OPEN_EARLY": [], "NORMAL_SESSION": [], "SESSION_TAIL": []}
    for t in trades:
        fam = tod_family(str(t.get("anchor_time") or ""))
        by.setdefault(fam, [])
        by[fam].append(float(t.get("pnl_yen_100") or 0.0))
    out = {}
    for k, xs in by.items():
        out[k] = {"n": len(xs), "pnl": round(sum(xs), 2)}
    return out


def _score_times(eng: Any, *, day: str, times: tuple[tuple[int, int], ...], family: str) -> list[dict[str, Any]]:
    leak = False
    rows: list[dict[str, Any]] = []
    for h, m in times:
        t0 = hm_epoch(day, h, m)
        sess = _session_fallback(day, h, m, t0)
        scored = score_universe_at(eng, t0=float(t0), day=day, session=sess)
        if scored.get("snapshot_future_leak"):
            leak = True
        iso = _isolated_trades(
            eng,
            day=day,
            session=sess,
            anchor=hm_label(h, m),
            shift_min=0,
            t0=float(t0),
            rows=scored.get("rows") or [],
        )
        sess_end = float(session_end_for_position(date=day, session=sess, fill_time=t0))
        avail = sess_end - float(t0)
        for rec in iso:
            rec["family"] = family
            rec["shift_key"] = family
            rec["horizon_sec_available"] = avail
            rec["truncated_600"] = bool(avail < 600.0 - 1e-9)
            rec["truncated_750"] = bool(avail < 750.0 - 1e-9)
            rec["session_bound_ok"] = session_of_epoch(day, t0) is not None
        rows.extend(iso)
    for rec in rows:
        rec["_day_leak"] = leak
    return rows


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    if str(NATIVE / "src") not in sys.path:
        sys.path.insert(0, str(NATIVE / "src"))
        sys.path.insert(0, str(NATIVE / "scripts"))
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    t0w = time.perf_counter()
    try:
        eng, _dual, pref = _stream_grid(
            day=day,
            capture=capture,
            universe=universe,
            variant="REFERENCE",
            fire_mode="production",
            allowed_hm=None,
        )
        if eng is None or not pref.get("ok"):
            return {
                "ok": False,
                "date": day,
                "blocker": pref.get("blocker") or "REFERENCE_STREAM_FAILED",
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        print(f"{day} REFERENCE fires={pref.get('anchor_fires')} trades={pref.get('trade_n')} pnl={pref.get('pnl')}", flush=True)
        iso_added = _score_times(eng, day=day, times=phase_a_added_tuples(), family="MIDPOINT")
        iso_tail = _score_times(eng, day=day, times=tail_tuples(), family="TAIL")
        iso_u10 = _score_times(eng, day=day, times=uniform10_new_tuples(), family="UNIFORM_NEW")
        leak = any(r.get("_day_leak") for r in iso_added + iso_tail + iso_u10)
        for rec in iso_added + iso_tail + iso_u10:
            rec.pop("_day_leak", None)
        print(f"{day} ISOLATED midpoints={len(iso_added)} tail={len(iso_tail)} u10new={len(iso_u10)} leak={leak}", flush=True)
        del eng, _dual
        gc.collect()

        _e2, _d2, paug = _stream_grid(
            day=day,
            capture=capture,
            universe=universe,
            variant="AUGMENTED",
            fire_mode="shifted_grid",
            allowed_hm=augmented_tuples(),
        )
        if not paug.get("ok"):
            return {
                "ok": False,
                "date": day,
                "blocker": paug.get("blocker") or "AUGMENTED_STREAM_FAILED",
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        print(f"{day} AUGMENTED fires={paug.get('anchor_fires')} trades={paug.get('trade_n')} pnl={paug.get('pnl')}", flush=True)
        del _e2, _d2
        gc.collect()

        _e3, _d3, puni = _stream_grid(
            day=day,
            capture=capture,
            universe=universe,
            variant="UNIFORM10",
            fire_mode="shifted_grid",
            allowed_hm=uniform10_tuples(),
        )
        if not puni.get("ok"):
            return {
                "ok": False,
                "date": day,
                "blocker": puni.get("blocker") or "UNIFORM10_STREAM_FAILED",
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        print(f"{day} UNIFORM10 fires={puni.get('anchor_fires')} trades={puni.get('trade_n')} pnl={puni.get('pnl')}", flush=True)
        del _e3, _d3
        gc.collect()

        return {
            "ok": True,
            "date": day,
            "period": payload.get("period"),
            "universe_n": len(universe),
            "universe_source": payload.get("universe_source"),
            "portfolios": {"REFERENCE": pref, "AUGMENTED": paug, "UNIFORM10": puni},
            "isolated_added": iso_added,
            "isolated_tail": iso_tail,
            "isolated_uniform_new": iso_u10,
            "snapshot_future_leak": bool(leak),
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
