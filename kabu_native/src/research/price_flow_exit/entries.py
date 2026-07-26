"""Fixed ENTRY cohorts E0–E4 (ENTRY time/price immutable)."""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from research.pbv2_zero_base_revalidation.labels import attach_labels
from research.pbv2_zero_base_revalidation.panel import CandidateRow, build_price_paths_and_panel
from research.volume_confirmed_impulse_entry.features import ThresholdSet, detect_triggers_for_symbol, aggregate_to_seconds
from research.volume_confirmed_impulse_entry.push_loader import PushTick
from research.price_flow_exit.constants import CAPTURE_DAYS, NATIVE, PUSH_CACHE, SOT_VCIE

JST = ZoneInfo("Asia/Tokyo")


@dataclass
class FixedEntry:
    day: str
    symbol: str
    entry_time: datetime
    entry_price: float
    entry_method: str  # PBv2 | VCIE_V4 | BOTH
    cohort: str  # E0..E4
    breakout_level: Optional[float] = None
    volume_impulse_10s: Optional[float] = None
    volume_impulse_30s: Optional[float] = None
    spread_bps: Optional[float] = None
    pbv2: bool = False
    vcie: bool = False
    entry_imbalance_percentile: Optional[float] = None
    setup_id: str = ""
    impulse_episode_id: str = ""
    breakout_episode_id: str = ""
    accept: bool = False
    actual_exit_time: Optional[datetime] = None
    actual_exit_reason: str = ""
    actual_pnl_5bps: Optional[float] = None
    actual_exit_price: Optional[float] = None
    features: dict[str, Optional[float]] = field(default_factory=dict)


def load_push_day(day: str, native: Path = NATIVE) -> dict[str, list[PushTick]]:
    path = PUSH_CACHE / f"{day}_push.pkl"
    with path.open("rb") as fh:
        by, _st = pickle.load(fh)
    return by


def _vcie_thresholds_for_day(day: str) -> ThresholdSet:
    # From SoT VCIE run threshold_history
    if day <= "20260721":
        return ThresholdSet(vol_impulse_10s=1.5, vol_impulse_30s=1.3, uptick_ratio=0.55, hold_n=5.0, context_age_sec=180.0)
    return ThresholdSet(vol_impulse_10s=2.0, vol_impulse_30s=1.5, uptick_ratio=0.7, hold_n=5.0, context_age_sec=180.0)


def reconstruct_vcie_v4(native: Path = NATIVE) -> list[FixedEntry]:
    out: list[FixedEntry] = []
    for day in CAPTURE_DAYS:
        by = load_push_day(day, native)
        thr = _vcie_thresholds_for_day(day)
        bars_cache = {sym: aggregate_to_seconds(ticks) for sym, ticks in by.items() if len(ticks) >= 40}
        for sym, bars in bars_cache.items():
            trigs = detect_triggers_for_symbol(bars, method="V4_FULL_VCIE", thr=thr, step=2)
            for t in trigs:
                eid = uuid4().hex[:10]
                out.append(
                    FixedEntry(
                        day=t.day,
                        symbol=t.symbol,
                        entry_time=t.event_time,
                        entry_price=float(t.entry_price),
                        entry_method="VCIE_V4",
                        cohort="E1",
                        breakout_level=t.breakout_level,
                        volume_impulse_10s=t.features.get("volume_impulse_10s"),
                        volume_impulse_30s=t.features.get("volume_impulse_30s"),
                        spread_bps=t.features.get("spread_change_30s"),
                        pbv2=False,
                        vcie=True,
                        setup_id=eid,
                        impulse_episode_id=eid,
                        breakout_episode_id=f"{t.symbol}:{t.breakout_level:.4f}" if t.breakout_level else eid,
                        features=dict(t.features),
                    )
                )
    return out


def load_pbv2_entries(native: Path = NATIVE) -> list[FixedEntry]:
    panel, paths, _meta = build_price_paths_and_panel(native)
    attach_labels(panel, paths)
    out: list[FixedEntry] = []
    for r in panel:
        if r.day not in CAPTURE_DAYS:
            continue
        if not (r.pbv2_decision or r.accept):
            continue
        if r.current_price <= 0:
            continue
        eid = uuid4().hex[:10]
        out.append(
            FixedEntry(
                day=r.day,
                symbol=r.symbol,
                entry_time=r.evaluation_time,
                entry_price=float(r.current_price),
                entry_method="PBv2",
                cohort="E0",
                pbv2=True,
                vcie=False,
                entry_imbalance_percentile=_f(r.features.get("f_imb_pct") or r.features.get("entry_imbalance_percentile")),
                setup_id=eid,
                impulse_episode_id=eid,
                breakout_episode_id=eid,
                accept=bool(r.accept),
                actual_exit_reason=r.actual_exit_reason or "",
                actual_pnl_5bps=r.actual_pnl_5bps,
                actual_exit_price=None,
                spread_bps=_f(r.features.get("f_spread")),
                features={k: _f(v) for k, v in r.features.items()},
            )
        )
    return out


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def build_cohorts(native: Path = NATIVE) -> dict[str, list[FixedEntry]]:
    print("[pfe] load PBv2 ENTRYs…", flush=True)
    e0 = load_pbv2_entries(native)
    print(f"[pfe] E0 n={len(e0)}", flush=True)
    print("[pfe] reconstruct VCIE V4 ENTRYs…", flush=True)
    e1 = reconstruct_vcie_v4(native)
    print(f"[pfe] E1 n={len(e1)}", flush=True)

    # mark overlap
    pbv2_keys = {(e.day, e.symbol, e.entry_time.replace(microsecond=0)) for e in e0}
    # loose match: same day/symbol within 120s
    def near_pbv2(e: FixedEntry) -> bool:
        for p in e0:
            if p.day != e.day or p.symbol != e.symbol:
                continue
            if abs((p.entry_time - e.entry_time).total_seconds()) <= 120:
                return True
        return False

    for e in e1:
        if near_pbv2(e):
            e.pbv2 = True
            e.entry_method = "BOTH"

    e2 = list(e0) + [e for e in e1 if not e.pbv2]
    for e in e2:
        e.cohort = "E2"
    e3 = [e for e in e1 if e.pbv2]
    for e in e3:
        e.cohort = "E3"
        e.entry_method = "BOTH"
    e4 = [e for e in e1 if not e.pbv2]
    for e in e4:
        e.cohort = "E4"

    # restore cohort labels on e0/e1
    for e in e0:
        e.cohort = "E0"
    for e in e1:
        e.cohort = "E1"

    return {"E0": e0, "E1": e1, "E2": e2, "E3": e3, "E4": e4}
