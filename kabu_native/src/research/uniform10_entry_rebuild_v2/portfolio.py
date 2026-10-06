"""C2 Dual Lane day replay + portfolio metrics. Occupancy/hypo Arch E forbidden."""
from __future__ import annotations

import gc
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_10min_opportunity.grids import tod_family
from research.anchor_timing_robustness.metrics import maxdd
from research.anchor_timing_robustness.replay import _portfolio_pack, _pop_webhooks
from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day
from research.edge_decay_rca.analyze import tag_entry_kind
from research.passive_fill_corrected_rebase.analyze import headline
from research.uniform10_entry_rebuild import UNIFORM10
from research.uniform10_entry_rebuild_v2.engine import C2LookupEngine
from research.uniform10_entry_rebuild_v2.oof import tod_bucket
from run_p0_4_exact_vs_fast_parity import _Discard


def process_c2_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    lookup = dict(payload.get("score_lookup") or {})
    t0w = time.perf_counter()
    try:
        eng, dual = _boot(universe, C2LookupEngine)
        if dual is None or eng is None or not getattr(eng, "ready", False):
            return {
                "ok": False,
                "date": day,
                "stage": "C2_EXACT",
                "blocker": getattr(eng, "fail_reason", "dual_unavailable") if eng is not None else "boot_failed",
            }
        eng.score_lookup = lookup
        eng.require_executable_continuous_fill = True
        eng.offset_sec = 0
        eng.allowed_hm = UNIFORM10
        eng.fire_mode = "shifted_grid"
        eng.notify_enabled = False
        eng.ingest_audit = _Discard()  # type: ignore[assignment]
        events_n, last_et = _stream_day(day, capture, eng, dual)
        eng._harvest(eng.events)
        packed = _portfolio_pack(eng, dual, day=day, variant="C2_UNIFORM10_V4_ENTRY", offset_sec=0, events_n=events_n, last_et=last_et)
        trades = packed.get("trades") or []
        print(f"{day} C2 trades={packed.get('trade_n')} pnl={packed.get('pnl')}", flush=True)
        del eng, dual, packed
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "stage": "C2_EXACT",
            "trades": trades,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": "C2_EXACT",
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }


def _pnl(t: dict[str, Any]) -> float:
    return float(t.get("pnl_yen_100") or 0.0)


def pack_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    h = headline(trades)
    tagged = tag_entry_kind(trades)
    first = [t for t in tagged if t.get("entry_kind") == "FIRST_ENTRY"]
    reent = [t for t in tagged if t.get("entry_kind") == "REENTRY"]
    am = [t for t in trades if str(t.get("session")) == "AM"]
    pm = [t for t in trades if str(t.get("session")) == "PM"]
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[str(t.get("date"))] += _pnl(t)
    daily = list(by_day.values())
    pos = sum(1 for v in daily if v > 0)
    tod = {}
    for fam in ("OPEN_EARLY", "NORMAL_SESSION", "SESSION_TAIL"):
        xs = [t for t in trades if tod_family(str(t.get("anchor") or t.get("anchor_time") or "")) == fam]
        tod[fam] = headline(xs)
    c2tod = {}
    for fam in ("OPEN_EARLY", "AM_NORMAL", "PM_NORMAL", "SESSION_TAIL"):
        xs = [t for t in trades if tod_bucket(str(t.get("anchor") or t.get("anchor_time") or "")) == fam]
        c2tod[fam] = headline(xs)
    return {
        **h,
        "PnL": h.get("pnl"),
        "maxDD": maxdd(trades),
        "wins": h.get("win"),
        "losses": h.get("loss"),
        "draws": h.get("draw"),
        "positive_day_rate": (pos / len(daily)) if daily else None,
        "median_daily_pnl": float(np.median(daily)) if daily else None,
        "n_days": len(daily),
        "AM": headline(am),
        "PM": headline(pm),
        "tod_legacy": tod,
        "tod_c2": c2tod,
        "first_entry": headline(first),
        "re_entry": headline(reent),
    }


def tail_pack(trades: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda t: -_pnl(t))
    by_day: dict[str, list] = defaultdict(list)
    by_sym: dict[str, list] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("date"))].append(t)
        by_sym[str(t.get("symbol"))].append(t)
    day_pnl = sorted(((sum(_pnl(x) for x in xs), d) for d, xs in by_day.items()), reverse=True)
    sym_pnl = sorted(((sum(_pnl(x) for x in xs), s) for s, xs in by_sym.items()), reverse=True)

    def drop_trades(n: int) -> list:
        drop = set(id(t) for t in ordered[:n])
        return [t for t in trades if id(t) not in drop]

    def drop_days(n: int) -> list:
        drop = {d for _p, d in day_pnl[:n]}
        return [t for t in trades if str(t.get("date")) not in drop]

    def drop_syms(n: int) -> list:
        drop = {s for _p, s in sym_pnl[:n]}
        return [t for t in trades if str(t.get("symbol")) not in drop]

    def pack(xs: list, name: str) -> dict[str, Any]:
        h = headline(xs)
        return {"name": name, "trades": h.get("trades"), "PnL": h.get("pnl"), "pnl": h.get("pnl"), "PF": h.get("PF"), "maxDD": maxdd(xs)}

    return {
        "ex_top1_trade": pack(drop_trades(1), "ex_top1_trade"),
        "ex_top3_trades": pack(drop_trades(3), "ex_top3_trades"),
        "ex_top10_trades": pack(drop_trades(10), "ex_top10_trades"),
        "ex_best_day": pack(drop_days(1), "ex_best_day"),
        "ex_top3_days": pack(drop_days(3), "ex_top3_days"),
        "ex_top_symbol": pack(drop_syms(1), "ex_top_symbol"),
        "ex_top3_symbols": pack(drop_syms(3), "ex_top3_symbols"),
        "ex_285A": pack([t for t in trades if str(t.get("symbol")) != "285A"], "ex_285A"),
        "top_day": day_pnl[0][1] if day_pnl else None,
        "top_symbol": sym_pnl[0][1] if sym_pnl else None,
    }
