"""True as-of 5s-grid field coverage (Phase A-R1 §2) + tick evidence + windows.

Coverage denominator = universe symbols x quality-valid fixed 5s grid points
(NOT raw event rows). At each grid time t a field group is AVAILABLE only if:
  - latest update has ingress/availability timestamp <= t
  - its field-specific source timestamp <= t (usable_ts = max(ingress, source))
  - the value is finite/valid
  - field-specific age (t - ingress of that update) <= 30s (no >30s forward hold)
  - no future interpolation; state never crosses AM/PM or window boundaries
Universe(day) = canonical PM symbol set of that day (read-only cache).

Also collects, in the same streaming pass, the empirical price-increment
evidence used by the tick resolver classification (per symbol, per price band).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .features import GRID_STEP_SEC, SESSION_TIMES, session_grid_epochs
from .raw_inventory import _parse_iso, _session_of
from .source_manifest import raw_day_dir

FIELD_AGE_MAX_SEC = 30.0
FIELD_GROUPS = ("quote", "volume", "vwap", "board10")

# Field-specific source timestamps (documented; None => ingress only).
FIELD_SOURCE_TS = {
    "quote": "max(BidTime, AskTime)",
    "volume": "TradingVolumeTime",
    "vwap": "(none: ingress only; kabu PUSH has no VWAP source time)",
    "board10": "(none: ingress only)",
}

DENOMINATOR_DEFINITION = (
    "eligible_grid_n = |universe(day)| x |grids in [valid_start, valid_end] of the "
    "session window|; universe(day) = canonical PM symbol set; valid span = "
    "[max(session_start, first universe ingress), min(session_end, last universe ingress)]"
)

# merged band floors of both tick tables => constant tick per bin under each class
_TICK_BINS = (0.0, 1_000.0, 3_000.0, 5_000.0, 10_000.0, 30_000.0, 50_000.0,
              100_000.0, 300_000.0, 500_000.0, 1e6, 3e6, 5e6, 1e7, 3e7)


def _tick_bin(price: float) -> float:
    lo = 0.0
    for b in _TICK_BINS:
        if price > b:
            lo = b
        else:
            break
    return lo


def canonical_day_bundle(native_root: Path, day: str) -> dict[str, Any]:
    """ONE canonical load per day: universe (PM set) + stats + regression audit."""
    import small_paper.e1_x5_canonical_replay as cr

    from .source_manifest import canonical_cache_dir

    events, report = cr.normalize_day(native_root, day, cache_dir=canonical_cache_dir(),
                                      use_cache=True)
    pm: set[str] = set()
    per_session = {"AM": 0, "PM": 0, "OFF": 0}
    syms: dict[str, set] = {"AM": set(), "PM": set()}
    regr = 0
    prev = None
    for e in events:
        sk = _session_of(e.ts) or "OFF"
        per_session[sk] += 1
        if sk in syms:
            syms[sk].add(str(e.symbol))
        if sk == "PM":
            pm.add(str(e.symbol))
        if prev is not None and e.ts < prev:
            regr += 1
        prev = e.ts
    gaps = getattr(report, "gaps", []) or []
    out = {
        "universe": sorted(pm),
        "canonical": {
            "canonical_events": len(events),
            "canonical_by_session": per_session,
            "canonical_symbols": {k: len(v) for k, v in syms.items()},
            "gap_n": len(gaps),
            "gap_max_sec": max((float(g.get("gap_sec") or 0) for g in gaps), default=0.0),
            "canonical_duplicate_keys": int(getattr(report, "duplicate_keys", 0) or 0),
        },
        "regression_audit": {
            "day": day,
            "canonical_events": len(events),
            "canonical_ts_regressions_stored_order": regr,
            "normalizer_reported_regressions": int(
                getattr(report, "timestamp_regressions_in_file_order", 0) or 0
            ),
            "note": (
                "separate column from raw per-file ingress inversions (which were 0); "
                "'timestamp inversions = 0' in the A-report referred to RAW INGRESS order"
            ),
        },
    }
    del events
    return out


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def scan_day(native_root: Path, day: str, universe: list[str]) -> dict[str, Any]:
    """One streaming pass: per-field update series + tick evidence + spans."""
    uset = set(universe)
    rd = raw_day_dir(native_root, day)

    # per symbol/session/group: (usable_ts[], ingress_ts[], valid[])
    series: dict[tuple[str, str, str], list[tuple[float, float, bool]]] = {}
    first_last: dict[str, list[float]] = {}   # session -> [first_ingress, last_ingress]
    tick_ev: dict[str, dict[str, list]] = {}  # symbol -> bin_rep -> [min_inc, count]
    prev_quote: dict[str, tuple[float, float]] = {}

    for fp in sorted(rd.glob("*.jsonl")):
        sym = fp.stem
        if sym.endswith(".T"):
            sym = sym[:-2]  # raw files are "1407.T.jsonl"; canonical universe is "1407"
        if sym not in uset:
            continue
        tev = tick_ev.setdefault(sym, {})
        with fp.open("rb") as f:
            for lineb in f:
                try:
                    d = json.loads(lineb)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                rec = _parse_iso(d.get("recorded_at"))
                if rec is None:
                    continue
                sk = _session_of(rec)
                if sk is None:
                    continue
                ing = rec.timestamp()
                fl = first_last.setdefault(sk, [ing, ing])
                fl[0] = min(fl[0], ing)
                fl[1] = max(fl[1], ing)
                p = d.get("payload") or {}
                b1, s1 = p.get("Buy1") or {}, p.get("Sell1") or {}
                bb, sa = _f(b1.get("Price")), _f(s1.get("Price"))

                # quote group
                if bb is not None and sa is not None:
                    src = None
                    for key in ("BidTime", "AskTime"):
                        t = _parse_iso(p.get(key))
                        if t is not None:
                            ts = t.timestamp()
                            src = ts if src is None else max(src, ts)
                    usable = max(ing, src) if src is not None else ing
                    valid = bb > 0 and sa > 0 and sa >= bb
                    series.setdefault((sym, sk, "quote"), []).append((usable, ing, valid))
                    # tick evidence: consecutive best-quote increments + L1-L2 gaps
                    if valid:
                        pq = prev_quote.get(sym)
                        cands = []
                        if pq is not None:
                            if pq[0] != bb:
                                cands.append((min(pq[0], bb), abs(bb - pq[0])))
                            if pq[1] != sa:
                                cands.append((min(pq[1], sa), abs(sa - pq[1])))
                        b2 = _f((p.get("Buy2") or {}).get("Price"))
                        s2 = _f((p.get("Sell2") or {}).get("Price"))
                        if b2 is not None and b2 > 0 and b2 != bb:
                            cands.append((min(bb, b2), abs(bb - b2)))
                        if s2 is not None and s2 > 0 and s2 != sa:
                            cands.append((min(sa, s2), abs(sa - s2)))
                        prev_quote[sym] = (bb, sa)
                        for pref, inc in cands:
                            if inc <= 0:
                                continue
                            lo = _tick_bin(pref)
                            hi_idx = _TICK_BINS.index(lo) + 1
                            hi = _TICK_BINS[hi_idx] if hi_idx < len(_TICK_BINS) else float("inf")
                            if max(pref, pref + inc) > hi:
                                continue  # crosses a band boundary: skip
                            rep = str(lo + 1.0)
                            cur = tev.get(rep)
                            if cur is None:
                                tev[rep] = [inc, 1]
                            else:
                                cur[0] = min(cur[0], inc)
                                cur[1] += 1

                # volume group (cumulative TradingVolume)
                tv = _f(p.get("TradingVolume"))
                if tv is not None:
                    t = _parse_iso(p.get("TradingVolumeTime"))
                    usable = max(ing, t.timestamp()) if t is not None else ing
                    series.setdefault((sym, sk, "volume"), []).append((usable, ing, tv >= 0))

                # vwap group
                vw = _f(p.get("VWAP"))
                if vw is not None:
                    series.setdefault((sym, sk, "vwap"), []).append((ing, ing, vw > 0))

                # board10 group (all 10 levels x both sides present)
                full = True
                tot_b = tot_s = 0.0
                for side in ("Buy", "Sell"):
                    for lv in range(1, 11):
                        lvd = p.get(f"{side}{lv}") or {}
                        px, q = _f(lvd.get("Price")), _f(lvd.get("Qty"))
                        if px is None or q is None:
                            full = False
                            break
                        if side == "Buy":
                            tot_b += q
                        else:
                            tot_s += q
                    if not full:
                        break
                if full:
                    series.setdefault((sym, sk, "board10"), []).append(
                        (ing, ing, (tot_b + tot_s) > 0)
                    )

    # ---- windows (valid spans) ----
    windows: dict[str, Any] = {}
    grids: dict[str, np.ndarray] = {}
    for sk in ("AM", "PM"):
        (h0, m0), (h1, m1) = SESSION_TIMES[sk]
        full_grid = session_grid_epochs(day, sk)
        exp_start, exp_end = float(full_grid[0]), float(full_grid[-1])
        fl = first_last.get(sk)
        if fl is None:
            windows[sk] = {
                "expected_start_epoch": exp_start, "expected_end_epoch": exp_end,
                "valid_start_epoch": None, "valid_end_epoch": None,
                "valid_sec": 0.0, "coverage_rate": 0.0, "eligible_grids_n": 0,
                "quality_class": "NO_DATA",
            }
            grids[sk] = full_grid[0:0]
            continue
        vs, ve = max(exp_start, fl[0]), min(exp_end, fl[1])
        mask = (full_grid >= vs - 1e-9) & (full_grid <= ve + 1e-9)
        g = full_grid[mask]
        cov = (ve - vs) / (exp_end - exp_start) if ve > vs else 0.0
        windows[sk] = {
            "expected_start_epoch": exp_start, "expected_end_epoch": exp_end,
            "valid_start_epoch": vs, "valid_end_epoch": ve,
            "valid_sec": round(max(0.0, ve - vs), 3),
            "coverage_rate": round(cov, 6),
            "eligible_grids_n": int(g.shape[0]),
            "quality_class": "FULL" if cov >= 0.99 else ("TRUNCATED" if cov > 0 else "NO_DATA"),
        }
        grids[sk] = g

    # ---- grid as-of coverage per session/field ----
    sessions: dict[str, Any] = {}
    for sk in ("AM", "PM"):
        g = grids[sk]
        ng = g.shape[0]
        out: dict[str, Any] = {}
        for grp in FIELD_GROUPS:
            elig = ng * len(universe)
            avail = stale = missing = invalid = 0
            ages: list[float] = []
            for sym in universe:
                rows = series.get((sym, sk, grp))
                if not rows or ng == 0:
                    missing += ng
                    continue
                rows.sort(key=lambda r: r[0])
                uts = np.asarray([r[0] for r in rows])
                its = np.asarray([r[1] for r in rows])
                val = np.asarray([r[2] for r in rows], dtype=bool)
                idx = np.searchsorted(uts, g, side="right") - 1
                no = idx < 0
                missing += int(np.sum(no))
                ok = ~no
                age = np.full(ng, np.nan)
                age[ok] = g[ok] - its[idx[ok]]
                st = ok & (age > FIELD_AGE_MAX_SEC + 1e-9)
                stale += int(np.sum(st))
                fresh = ok & ~st
                inv = fresh & ~val[np.clip(idx, 0, None)]
                invalid += int(np.sum(inv))
                good = fresh & val[np.clip(idx, 0, None)]
                avail += int(np.sum(good))
                ages.extend(age[good].tolist())
            arr = np.asarray(ages) if ages else np.asarray([np.nan])
            out[grp] = {
                "eligible_grid_n": elig,
                "available_grid_n": avail,
                "coverage": round(avail / elig, 6) if elig else None,
                "stale_grid_n": stale,
                "missing_grid_n": missing,
                "invalid_value_n": invalid,
                "denominator": DENOMINATOR_DEFINITION,
                "field_source_ts": FIELD_SOURCE_TS[grp],
                "age_sec": {
                    "min": None if not ages else round(float(np.min(arr)), 3),
                    "median": None if not ages else round(float(np.median(arr)), 3),
                    "max": None if not ages else round(float(np.max(arr)), 3),
                },
            }
        sessions[sk] = out

    return {
        "day": day,
        "universe_n": len(universe),
        "universe": universe,
        "windows": windows,
        "sessions": sessions,
        "tick_evidence": tick_ev,
        "computed_at": datetime.now().astimezone().isoformat(),
    }
