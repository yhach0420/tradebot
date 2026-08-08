"""Structural decision-quote coverage vs spread tradeability (Phase A-R3 §3).

R2's decision_quote_coverage mixed quote quality with spread_bps<=50. R3
splits them:

  A. STRUCTURAL_DECISION_QUOTE_COVERAGE (gate >=0.90):
     numerator = finite bid/ask, both >0, bid<=ask, snapshot freshness<=30s,
     no source conflict, no missing state. Spread is NOT in the numerator.

  B. SPREAD_TRADEABILITY (strategy filter, not a coverage gate):
     among structurally-valid opportunities, share with spread_bps<=50.
     Unhealthy spread => NOT_EVALUABLE_UNHEALTHY_SPREAD at ENTRY (unchanged).

R2 mixed values are preserved from the R2 published report as failure evidence
and are never overwritten.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .decision_coverage import (
    LOOKBACK_STEPS,
    MKT_LOO_MIN,
    SNAPSHOT_FRESH_SEC,
    SPREAD_MAX_BPS,
    _f,
    _pcts,
)

DENOMINATOR_DEFINITION_STRUCTURAL = (
    "due_symbol_grid_n identical to R2 (PUSH in grid + warmup + 600s horizon). "
    "structural numerator: finite bid/ask, both >0, bid<=ask, snapshot age<=30s, "
    "no source conflict, no missing state. spread_bps<=50 is NOT in the numerator."
)

# Audit expectations (recomputed from raw; hard-code forbidden for READY gate —
# used only as a cross-check tolerance band in the orchestrator).
AUDIT_EXPECT = {
    "20260724_AM": {"structural_n": 22572, "due_n": 22572, "coverage": 1.0},
    "20260729_AM": {"structural_n": 50705, "due_n": 51121, "coverage": 0.991862},
    "min_included_approx": 0.991862,
    "weighted_included_approx": 0.999129,
}


def _spread_bps(bid: np.ndarray, ask: np.ndarray) -> np.ndarray:
    mid = (bid + ask) / 2.0
    with np.errstate(invalid="ignore", divide="ignore"):
        return (ask - bid) / mid * 10000.0


def scan_day_r3(native_root, day: str, universe: list[str]) -> dict[str, Any]:
    """One streaming pass: structural coverage + spread tradeability (R3)."""
    return _scan_day_r3_impl(native_root, day, universe)


def _scan_day_r3_impl(native_root, day, universe) -> dict[str, Any]:
    """Full R3 scan: identical due definition to R2, split structural vs spread."""
    import json
    from datetime import datetime

    from .asof_coverage import _TICK_BINS, _tick_bin
    from .features import WARMUP_SEC, session_grid_epochs
    from .raw_inventory import _parse_iso, _session_of
    from .source_manifest import raw_day_dir
    from .windows import EXIT_HORIZON_SEC

    uset = set(universe)
    rd = raw_day_dir(native_root, day)
    S: dict[tuple[str, str], dict[str, list]] = {}
    first_last: dict[str, list[float]] = {}
    tick_ev: dict[str, dict[str, list]] = {}
    sem = {k: {"unchanged_n": 0, "advanced_while_unchanged": 0,
               "changed_n": 0, "advanced_on_change": 0, "present_n": 0}
           for k in ("BidTime", "AskTime", "CurrentPriceTime", "TradingVolumeTime")}
    prev_vals = {k: None for k in sem}
    prev_field = {k: None for k in sem}

    for fp in sorted(rd.glob("*.jsonl")):
        sym = fp.stem
        if sym.endswith(".T"):
            sym = sym[:-2]
        if sym not in uset:
            continue
        tev = tick_ev.setdefault(sym, {})
        st_bid = st_ask = float("nan")
        st_q_ing = float("nan")
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
                bb = _f((p.get("Buy1") or {}).get("Price"))
                sa = _f((p.get("Sell1") or {}).get("Price"))
                fields = {"BidTime": bb, "AskTime": sa,
                          "CurrentPriceTime": _f(p.get("CurrentPrice")),
                          "TradingVolumeTime": _f(p.get("TradingVolume"))}
                for key, val in fields.items():
                    raw_t = p.get(key)
                    if raw_t is None or val is None:
                        continue
                    tse = _parse_iso(raw_t)
                    if tse is None:
                        continue
                    tse = tse.timestamp()
                    c = sem[key]
                    c["present_n"] += 1
                    pv, pt = prev_field[key], prev_vals[key]
                    if pv is not None and pt is not None:
                        if val == pv:
                            c["unchanged_n"] += 1
                            if tse > pt + 1e-9:
                                c["advanced_while_unchanged"] += 1
                        else:
                            c["changed_n"] += 1
                            if tse > pt + 1e-9:
                                c["advanced_on_change"] += 1
                    prev_field[key], prev_vals[key] = val, tse
                if bb is not None and sa is not None:
                    if bb > 0 and sa > 0:
                        cands = []
                        if np.isfinite(st_bid) and st_bid != bb:
                            cands.append((min(st_bid, bb), abs(bb - st_bid)))
                        if np.isfinite(st_ask) and st_ask != sa:
                            cands.append((min(st_ask, sa), abs(sa - st_ask)))
                        b2 = _f((p.get("Buy2") or {}).get("Price"))
                        s2 = _f((p.get("Sell2") or {}).get("Price"))
                        if b2 is not None and b2 > 0 and b2 != bb:
                            cands.append((min(bb, b2), abs(bb - b2)))
                        if s2 is not None and s2 > 0 and s2 != sa:
                            cands.append((min(sa, s2), abs(sa - s2)))
                        for pref, inc in cands:
                            if inc <= 0:
                                continue
                            lo = _tick_bin(pref)
                            hi_i = _TICK_BINS.index(lo) + 1
                            hi = _TICK_BINS[hi_i] if hi_i < len(_TICK_BINS) else float("inf")
                            if max(pref, pref + inc) > hi:
                                continue
                            rep = str(lo + 1.0)
                            cur = tev.get(rep)
                            if cur is None:
                                tev[rep] = [inc, 1]
                            else:
                                cur[0] = min(cur[0], inc)
                                cur[1] += 1
                    st_bid, st_ask = bb, sa
                    st_q_ing = ing
                row = S.setdefault((sym, sk), {
                    "ing": [], "bid": [], "ask": [], "q_ing": [], "source": [],
                })
                row["ing"].append(ing)
                row["bid"].append(st_bid)
                row["ask"].append(st_ask)
                row["q_ing"].append(st_q_ing)
                row["source"].append(str(d.get("source") or ""))

    windows: dict[str, Any] = {}
    grids: dict[str, np.ndarray] = {}
    for sk in ("AM", "PM"):
        full_grid = session_grid_epochs(day, sk)
        exp_s, exp_e = float(full_grid[0]), float(full_grid[-1])
        fl = first_last.get(sk)
        if fl is None:
            windows[sk] = {"expected_start_epoch": exp_s, "expected_end_epoch": exp_e,
                           "valid_start_epoch": None, "valid_end_epoch": None,
                           "valid_sec": 0.0, "coverage_rate": 0.0,
                           "eligible_grids_n": 0, "quality_class": "NO_DATA"}
            grids[sk] = full_grid[0:0]
            continue
        vs, ve = max(exp_s, fl[0]), min(exp_e, fl[1])
        mask = (full_grid >= vs - 1e-9) & (full_grid <= ve + 1e-9)
        cov = (ve - vs) / (exp_e - exp_s) if ve > vs else 0.0
        windows[sk] = {"expected_start_epoch": exp_s, "expected_end_epoch": exp_e,
                       "valid_start_epoch": vs, "valid_end_epoch": ve,
                       "valid_sec": round(max(0.0, ve - vs), 3),
                       "coverage_rate": round(cov, 6),
                       "eligible_grids_n": int(mask.sum()),
                       "quality_class": "FULL" if cov >= 0.99 else
                                        ("TRUNCATED" if cov > 0 else "NO_DATA")}
        grids[sk] = full_grid[mask]

    sessions: dict[str, Any] = {}
    for sk in ("AM", "PM"):
        g = grids[sk]
        ng = g.shape[0]
        w = windows[sk]
        n_uni = len(universe)
        if ng == 0:
            sessions[sk] = {"universe_n": n_uni, "full_grid_n": 0}
            continue
        vs, ve = w["valid_start_epoch"], w["valid_end_epoch"]
        warm_anchor = max(w["expected_start_epoch"], vs) + WARMUP_SEC
        entry_until = (w["expected_end_epoch"]
                       if ve >= w["expected_end_epoch"] - 1e-9
                       else ve - EXIT_HORIZON_SEC)

        eval_mat = np.zeros((n_uni, ng), dtype=bool)
        push_mat = np.zeros((n_uni, ng), dtype=bool)
        due_mat = np.zeros((n_uni, ng), dtype=bool)
        structural_mat = np.zeros((n_uni, ng), dtype=bool)
        spread_ok_mat = np.zeros((n_uni, ng), dtype=bool)
        spread_vals: list[float] = []
        rejects = {"invalid_value_n": 0, "crossed_n": 0, "spread_unhealthy_n": 0,
                   "source_conflict_n": 0, "missing_state_n": 0,
                   "stale_snapshot_reject_n": 0}
        stale_snapshot_n = 0
        full_ok_total = 0
        push_stats: dict[str, Any] = {}
        lb_fail = 0
        sym_rows: dict[str, Any] = {}

        for si, sym in enumerate(universe):
            row = S.get((sym, sk))
            if not row:
                push_stats[sym] = {"push_n": 0}
                sym_rows[sym] = {"due_n": 0, "structural_n": 0,
                                 "spread_healthy_n": 0, "spread_unhealthy_n": 0}
                continue
            ing = np.asarray(row["ing"])
            order = np.argsort(ing, kind="stable")
            ing = ing[order]
            bid = np.asarray(row["bid"])[order]
            ask = np.asarray(row["ask"])[order]
            srcs = [row["source"][i] for i in order]
            push_stats[sym] = {"push_n": int(ing.size),
                               "push_interval_sec": _pcts(
                                   np.diff(ing) if ing.size > 1 else np.asarray([]))}
            idx = np.searchsorted(ing, g, side="right") - 1
            has = idx >= 0
            age = np.full(ng, np.inf)
            age[has] = g[has] - ing[idx[has]]
            qb = np.full(ng, np.nan)
            qa = np.full(ng, np.nan)
            qb[has] = bid[idx[has]]
            qa[has] = ask[idx[has]]
            finite = np.isfinite(qb) & np.isfinite(qa)
            pos = finite & (qb > 0) & (qa > 0)
            ordered = pos & (qa >= qb)
            crossed = pos & (qa < qb)
            fresh = has & (age <= SNAPSHOT_FRESH_SEC + 1e-9)
            sp = _spread_bps(qb, qa)
            spread_ok = ordered & (sp <= SPREAD_MAX_BPS + 1e-12)

            eval_mat[si] = fresh & ordered
            full_ok_total += int(np.sum(fresh & ordered))
            stale_snapshot_n += int(np.sum(has & ~fresh))

            push = has & (age < 5.0 - 1e-9)
            push_mat[si] = push
            in_scope = (g >= warm_anchor - 1e-9) & (g <= entry_until + 1e-9)
            due_mat[si] = push & in_scope

            conflict = np.zeros(ng, dtype=bool)
            if len(set(srcs)) > 1:
                lo_idx = np.searchsorted(ing, g - 5.0, side="right")
                for gi in np.nonzero(due_mat[si])[0]:
                    seen = {srcs[k] for k in range(lo_idx[gi], idx[gi] + 1)}
                    if len(seen) > 1:
                        conflict[gi] = True

            # STRUCTURAL (no spread filter)
            structural = due_mat[si] & ordered & fresh & ~conflict
            structural_mat[si] = structural
            spread_ok_mat[si] = structural & spread_ok

            dd = due_mat[si]
            rejects["missing_state_n"] += int(np.sum(dd & ~finite))
            rejects["invalid_value_n"] += int(np.sum(dd & finite & ((qb <= 0) | (qa <= 0))))
            rejects["crossed_n"] += int(np.sum(dd & crossed))
            rejects["source_conflict_n"] += int(np.sum(dd & conflict))
            rejects["stale_snapshot_reject_n"] += int(np.sum(dd & ~fresh))
            rejects["spread_unhealthy_n"] += int(np.sum(structural & ~spread_ok))

            for gi in np.nonzero(structural)[0]:
                if np.isfinite(sp[gi]):
                    spread_vals.append(float(sp[gi]))

            covered = fresh
            run = 0
            for gi in range(ng):
                run = run + 1 if covered[gi] else 0
                if dd[gi] and run < LOOKBACK_STEPS + 1:
                    lb_fail += 1

            dn = int(due_mat[si].sum())
            sn = int(structural.sum())
            sh = int((structural & spread_ok).sum())
            sym_rows[sym] = {
                "due_n": dn,
                "structural_n": sn,
                "structural_coverage": round(sn / dn, 6) if dn else None,
                "spread_healthy_n": sh,
                "spread_unhealthy_n": sn - sh,
                "spread_healthy_rate": round(sh / sn, 6) if sn else None,
                "push_n": push_stats[sym]["push_n"],
                **({"push_interval_sec": push_stats[sym].get("push_interval_sec")}
                   if push_stats[sym].get("push_n") else {}),
            }

        due_n = int(due_mat.sum())
        structural_n = int(structural_mat.sum())
        spread_healthy_n = int(spread_ok_mat.sum())
        loo_counts = []
        ctx_ok = 0
        for si in range(n_uni):
            gi_idx = np.nonzero(due_mat[si])[0]
            if gi_idx.size == 0:
                continue
            loo = eval_mat.sum(axis=0)[gi_idx] - eval_mat[si, gi_idx].astype(int)
            loo_counts.append(loo)
            ctx_ok += int(np.sum(loo >= MKT_LOO_MIN))
        loo_all = np.concatenate(loo_counts) if loo_counts else np.asarray([])
        sv = np.asarray(spread_vals) if spread_vals else np.asarray([])

        # R2-compatible mixed metric (for audit diff only; not a gate)
        mixed_ok = int(spread_ok_mat.sum())  # structural AND spread healthy
        sessions[sk] = {
            "universe_n": n_uni,
            "full_grid_n": ng,
            "full_grid_state_coverage": round(full_ok_total / (n_uni * ng), 6),
            "due_symbol_grid_n": due_n,
            "NOT_DUE_NO_SYMBOL_UPDATE_n": int((~push_mat).sum()),
            # R3 A — structural gate
            "structural_decision_quote_available_n": structural_n,
            "structural_decision_quote_coverage": (
                round(structural_n / due_n, 6) if due_n else None
            ),
            # R3 B — spread tradeability (not a coverage gate)
            "spread_healthy_n": spread_healthy_n,
            "spread_unhealthy_n": structural_n - spread_healthy_n,
            "spread_healthy_rate": (
                round(spread_healthy_n / structural_n, 6) if structural_n else None
            ),
            "spread_bps_stats": {
                "p50": round(float(np.quantile(sv, 0.50)), 3) if sv.size else None,
                "p90": round(float(np.quantile(sv, 0.90)), 3) if sv.size else None,
                "p95": round(float(np.quantile(sv, 0.95)), 3) if sv.size else None,
                "p99": round(float(np.quantile(sv, 0.99)), 3) if sv.size else None,
                "max": round(float(np.max(sv)), 3) if sv.size else None,
            },
            # retained R2 mixed metric for comparison (NOT a gate)
            "decision_quote_coverage_r2_mixed": (
                round(mixed_ok / due_n, 6) if due_n else None
            ),
            "decision_quote_available_n_r2_mixed": mixed_ok,
            "mkt_evaluable_stats": {
                "min": int(loo_all.min()) if loo_all.size else None,
                "p05": float(np.quantile(loo_all, 0.05)) if loo_all.size else None,
                "median": float(np.median(loo_all)) if loo_all.size else None,
                "p95": float(np.quantile(loo_all, 0.95)) if loo_all.size else None,
                "max": int(loo_all.max()) if loo_all.size else None,
            },
            "market_context_coverage": round(ctx_ok / due_n, 6) if due_n else None,
            "incomplete_lookback_n": lb_fail,
            "stale_snapshot_n": stale_snapshot_n,
            "rejects": rejects,
            "warmup_anchor_epoch": warm_anchor,
            "entry_evaluable_until_epoch": entry_until,
            "denominator_definition": DENOMINATOR_DEFINITION_STRUCTURAL,
            "symbol_decision_coverage": sym_rows,
        }

    semantics = {}
    for key, c in sem.items():
        if c["unchanged_n"] >= 100:
            rate = c["advanced_while_unchanged"] / c["unchanged_n"]
            verdict = ("LAST_CHANGE_TIME" if rate < 0.01
                       else ("OBSERVATION_TIME" if rate > 0.99 else "UNKNOWN"))
        else:
            verdict, rate = "UNKNOWN", None
        semantics[key] = {**c, "advance_rate_when_unchanged":
                          (round(rate, 6) if rate is not None else None),
                          "semantics": verdict}
    unknown_n = sum(1 for k in ("BidTime", "AskTime", "CurrentPriceTime",
                                "TradingVolumeTime")
                    if semantics[k]["semantics"] == "UNKNOWN")
    for sk in sessions:
        if "due_symbol_grid_n" in sessions[sk]:
            sessions[sk]["source_semantics_unknown_n"] = unknown_n
    semantics["policy"] = (
        "availability_ts = ingress only; usable_ts=max(ingress,source) ABOLISHED; "
        "structural coverage excludes spread; spread is a strategy filter"
    )

    return {
        "day": day,
        "universe_n": len(universe),
        "universe": universe,
        "windows": windows,
        "sessions": sessions,
        "tick_evidence": tick_ev,
        "source_semantics": semantics,
        "computed_at": datetime.now().astimezone().isoformat(),
    }
