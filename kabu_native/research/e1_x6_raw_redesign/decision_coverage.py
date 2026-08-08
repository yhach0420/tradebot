"""Decision-opportunity coverage (Phase A-R2 §3-§6, §10).

Separates three coverages:
  A. FULL_GRID_STATE_COVERAGE  — universe x all 5s grids (diagnostic only)
  B. DECISION_QUOTE_COVERAGE   — denominator = due symbol-grids (a raw PUSH of
     the symbol arrived inside the grid, availability order) that also satisfy
     warmup + analysis mask + 600s horizon; numerator = healthy quote state
  C. MARKET_CONTEXT_COVERAGE   — share of decision opportunities where the
     leave-one-out market aggregate has >= 30 evaluable other symbols

Timestamps: availability_ts (ingress) is the ONLY causal/availability clock.
usable_ts = max(ingress, source) is ABOLISHED. Source timestamps are stored as
diagnostics (snapshot_age vs field_source_age separated) and their semantics
(observation vs last-change) are PROVEN from data, never assumed.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .asof_coverage import _TICK_BINS, _tick_bin
from .features import SESSION_TIMES, WARMUP_SEC, session_grid_epochs
from .raw_inventory import _parse_iso, _session_of
from .source_manifest import raw_day_dir
from .windows import EXIT_HORIZON_SEC

SNAPSHOT_FRESH_SEC = 30.0
MKT_LOO_MIN = 30
SPREAD_MAX_BPS = 50.0
LOOKBACK_STEPS = 60  # 300s / 5s

DENOMINATOR_DEFINITION_B = (
    "due_symbol_grid_n = symbol-grids with >=1 raw PUSH of that symbol arriving "
    "inside the 5s grid interval (availability order; last state in grid used), "
    "AND grid >= max(session_start, valid_start)+300s warmup, AND grid <= "
    "entry_evaluable_until (600s horizon / analysis mask). Grids without a "
    "symbol PUSH are NOT_DUE_NO_SYMBOL_UPDATE and excluded from the denominator."
)


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _pcts(arr: np.ndarray, qs=(0.50, 0.90, 0.95, 0.99)) -> dict[str, float]:
    if arr.size == 0:
        return {f"p{int(q*100)}": None for q in qs} | {"max": None}
    out = {f"p{int(q*100)}": round(float(np.quantile(arr, q)), 3) for q in qs}
    out["max"] = round(float(np.max(arr)), 3)
    return out


def scan_day_r2(native_root: Path, day: str, universe: list[str]) -> dict[str, Any]:
    """One streaming pass per day. Ingress-only availability; no source merge."""
    uset = set(universe)
    rd = raw_day_dir(native_root, day)

    # per (sym, session): event-parallel lists
    S: dict[tuple[str, str], dict[str, list]] = {}
    first_last: dict[str, list[float]] = {}
    tick_ev: dict[str, dict[str, list]] = {}
    sem = {k: {"unchanged_n": 0, "advanced_while_unchanged": 0,
               "changed_n": 0, "advanced_on_change": 0, "present_n": 0}
           for k in ("BidTime", "AskTime", "CurrentPriceTime", "TradingVolumeTime")}
    src_delta: list[float] = []

    for fp in sorted(rd.glob("*.jsonl")):
        sym = fp.stem
        if sym.endswith(".T"):
            sym = sym[:-2]
        if sym not in uset:
            continue
        tev = tick_ev.setdefault(sym, {})
        st_bid = st_ask = float("nan")
        st_q_ing = float("nan")
        prev_vals = {"BidTime": None, "AskTime": None,
                     "CurrentPriceTime": None, "TradingVolumeTime": None}
        prev_field = {"BidTime": None, "AskTime": None,
                      "CurrentPriceTime": None, "TradingVolumeTime": None}
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

                # --- source-timestamp semantics proof (data-driven) ---
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
                    if key == "BidTime":
                        src_delta.append(ing - tse)

                # --- quote state carry (ingress order only) ---
                if bb is not None and sa is not None:
                    # tick evidence (same binning as R1)
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

    # ---- windows / valid spans (identical formula to R1 windows) ----
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

    # ---- per-session aggregation ----
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
        push_mat = np.zeros((n_uni, ng), dtype=bool)  # >=1 PUSH in (t-5, t]
        due_mat = np.zeros((n_uni, ng), dtype=bool)   # push & in decision scope
        ok_mat = np.zeros((n_uni, ng), dtype=bool)
        lb_fail_mat = np.zeros((n_uni, ng), dtype=bool)
        rejects = {"invalid_value_n": 0, "crossed_n": 0, "spread_reject_n": 0,
                   "source_conflict_n": 0, "missing_state_n": 0,
                   "stale_snapshot_reject_n": 0}
        stale_snapshot_n = 0
        push_stats: dict[str, Any] = {}
        full_ok_total = 0

        for si, sym in enumerate(universe):
            row = S.get((sym, sk))
            if not row:
                push_stats[sym] = {"push_n": 0}
                continue
            ing = np.asarray(row["ing"])
            order = np.argsort(ing, kind="stable")  # availability order
            ing = ing[order]
            bid = np.asarray(row["bid"])[order]
            ask = np.asarray(row["ask"])[order]
            srcs = [row["source"][i] for i in order]
            push_stats[sym] = {"push_n": int(ing.size),
                               "push_interval_sec": _pcts(np.diff(ing) if ing.size > 1
                                                          else np.asarray([]))}
            idx = np.searchsorted(ing, g, side="right") - 1
            has = idx >= 0
            age = np.full(ng, np.inf)
            age[has] = g[has] - ing[idx[has]]
            qb = np.full(ng, np.nan)
            qa = np.full(ng, np.nan)
            qb[has] = bid[idx[has]]
            qa[has] = ask[idx[has]]
            quote_ok = (np.isfinite(qb) & np.isfinite(qa) & (qb > 0) & (qa > 0)
                        & (qa >= qb))
            spread_ok = np.zeros(ng, dtype=bool)
            nz = quote_ok.copy()
            with np.errstate(invalid="ignore", divide="ignore"):
                sp = (qa - qb) / ((qa + qb) / 2.0) * 10000.0
            spread_ok[nz] = sp[nz] <= SPREAD_MAX_BPS

            fresh = has & (age <= SNAPSHOT_FRESH_SEC + 1e-9)
            eval_mat[si] = fresh & quote_ok
            full_ok_total += int(np.sum(fresh & quote_ok))
            stale_snapshot_n += int(np.sum(has & ~fresh))

            # PUSH arrived inside this grid interval (availability = ingress)
            push = has & (age < 5.0 - 1e-9)
            push_mat[si] = push
            in_scope = (g >= warm_anchor - 1e-9) & (g <= entry_until + 1e-9)
            due_mat[si] = push & in_scope

            # multi-source within the same grid = source conflict
            conflict = np.zeros(ng, dtype=bool)
            if len(set(srcs)) > 1:
                lo_idx = np.searchsorted(ing, g - 5.0, side="right")
                for gi in np.nonzero(due_mat[si])[0]:
                    seen = {srcs[k] for k in range(lo_idx[gi], idx[gi] + 1)}
                    if len(seen) > 1:
                        conflict[gi] = True
            # numerator (§4B): healthy quote + fresh snapshot + no conflict
            ok = due_mat[si] & quote_ok & spread_ok & fresh & ~conflict
            ok_mat[si] = ok

            dd = due_mat[si]
            rejects["missing_state_n"] += int(np.sum(dd & ~np.isfinite(qb)))
            rejects["invalid_value_n"] += int(np.sum(dd & np.isfinite(qb)
                                                     & np.isfinite(qa)
                                                     & ((qb <= 0) | (qa <= 0))))
            rejects["crossed_n"] += int(np.sum(dd & np.isfinite(qb) & np.isfinite(qa)
                                               & (qb > 0) & (qa > 0) & (qa < qb)))
            rejects["spread_reject_n"] += int(np.sum(dd & quote_ok & ~spread_ok))
            rejects["source_conflict_n"] += int(np.sum(dd & conflict))
            rejects["stale_snapshot_reject_n"] += int(np.sum(dd & ~fresh))

            # 300s continuous lookback (per-opportunity, NOT a usability kill)
            covered = fresh
            run = 0
            for gi in range(ng):
                run = run + 1 if covered[gi] else 0
                if dd[gi] and run < LOOKBACK_STEPS + 1:
                    lb_fail_mat[si, gi] = True

        total_eval = eval_mat.sum(axis=0)
        due_n = int(due_mat.sum())
        ok_n = int(ok_mat.sum())
        loo_counts = []
        ctx_ok = 0
        for si in range(n_uni):
            gi_idx = np.nonzero(due_mat[si])[0]
            if gi_idx.size == 0:
                continue
            loo = total_eval[gi_idx] - eval_mat[si, gi_idx].astype(int)
            loo_counts.append(loo)
            ctx_ok += int(np.sum(loo >= MKT_LOO_MIN))
        loo_all = np.concatenate(loo_counts) if loo_counts else np.asarray([])

        # NOT_DUE = grids with no symbol PUSH (out-of-scope WITH a push is not NOT_DUE)
        not_due_n = int((~push_mat).sum())
        sym_cov = {}
        for si, sym in enumerate(universe):
            dn = int(due_mat[si].sum())
            sym_cov[sym] = {
                "due_n": dn,
                "ok_n": int(ok_mat[si].sum()),
                "decision_coverage": round(ok_mat[si].sum() / dn, 6) if dn else None,
                **({"push_interval_sec": push_stats[sym].get("push_interval_sec")}
                   if push_stats.get(sym, {}).get("push_n") else {}),
                "push_n": push_stats.get(sym, {}).get("push_n", 0),
            }

        sessions[sk] = {
            "universe_n": n_uni,
            "full_grid_n": ng,
            "full_grid_state_coverage": round(full_ok_total / (n_uni * ng), 6),
            "due_symbol_grid_n": due_n,
            "NOT_DUE_NO_SYMBOL_UPDATE_n": not_due_n,
            "decision_quote_available_n": ok_n,
            "decision_quote_coverage": round(ok_n / due_n, 6) if due_n else None,
            "mkt_evaluable_stats": {
                "min": int(loo_all.min()) if loo_all.size else None,
                "p05": float(np.quantile(loo_all, 0.05)) if loo_all.size else None,
                "median": float(np.median(loo_all)) if loo_all.size else None,
                "p95": float(np.quantile(loo_all, 0.95)) if loo_all.size else None,
                "max": int(loo_all.max()) if loo_all.size else None,
            },
            "market_context_coverage": round(ctx_ok / due_n, 6) if due_n else None,
            "incomplete_lookback_n": int(lb_fail_mat.sum()),
            "stale_snapshot_n": stale_snapshot_n,
            "rejects": rejects,
            "warmup_anchor_epoch": warm_anchor,
            "entry_evaluable_until_epoch": entry_until,
            "denominator_definition": DENOMINATOR_DEFINITION_B,
            "symbol_decision_coverage": sym_cov,
        }

    # ---- source semantics verdicts ----
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
    sd = np.asarray(src_delta) if src_delta else np.asarray([])
    semantics["source_to_ingress_delta_sec"] = {
        "p50": round(float(np.median(sd)), 3) if sd.size else None,
        "p90": round(float(np.quantile(sd, 0.90)), 3) if sd.size else None,
        "negative_n": int(np.sum(sd < 0)) if sd.size else 0,
        "n": int(sd.size),
    }
    semantics["policy"] = (
        "availability_ts = ingress only; usable_ts=max(ingress,source) ABOLISHED; "
        "snapshot freshness uses ingress-based snapshot_age_sec; field source "
        "times are diagnostics; LAST_CHANGE_TIME fields never stale a snapshot "
        "merely because the value did not change; UNKNOWN-semantics source "
        "timestamps are never used as availability"
    )
    unknown_n = sum(1 for k in ("BidTime", "AskTime", "CurrentPriceTime",
                                "TradingVolumeTime")
                    if semantics[k]["semantics"] == "UNKNOWN")
    for sk in sessions:
        if "due_symbol_grid_n" in sessions[sk]:
            sessions[sk]["source_semantics_unknown_n"] = unknown_n

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
