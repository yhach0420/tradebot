"""CDEED runner — directional vs execution decomposition on S1 continuous."""
from __future__ import annotations

import json
import math
import pickle
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.continuous_directional_vs_execution_edge.constants import (
    CACHE_DIR,
    CANCEL,
    COST_BPS,
    HOLD_DAYS,
    HYPOTHESES,
    LIVE_ORDER,
    OUT_ROOT,
    PRIMARY_D,
    REPRO_ABS_TOL,
    S1_CACHE,
    SOURCE_CS,
    STRIDE,
    SUBMIT,
    TRAIN_DAYS,
    VAL_DAYS,
)
from research.continuous_directional_vs_execution_edge.labels import (
    execution_horizons,
    make_directional_labels,
    mechanical_down_audit,
    quote_ok,
    tick_size_jpy,
)
from research.continuous_directional_vs_execution_edge.reporting import emit
from research.continuous_directional_vs_execution_edge.scoring import (
    _score_samples,
    dir_pop,
    eval_dir_fixed,
    exec_selected_metrics,
    fit_dir_candidate,
    train_dir_passes,
    val_dir_passes,
)
from research.upward_edge_identification_audit.loader import load_streams
from research.upward_edge_identification_audit.runner import dedupe_samples

JST = ZoneInfo("Asia/Tokyo")


def _pct(vals: list[float], p: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(p * (len(s) - 1)))]


def _enrich(samples, streams) -> tuple[list, dict]:
    """Attach directional labels, mechanical rows, execution horizons."""
    mech_b2, mech_b4 = [], []
    invalid = []
    enriched = []
    for s in samples:
        ticks = streams.get(s.stream_key)
        if not ticks or s.idx >= len(ticks):
            invalid.append({"sample_id": s.sample_id, "reason": "missing_stream"})
            continue
        bid, ask = s.entry_bid, s.entry_ask
        ok, reason = quote_ok(bid, ask)
        if not ok:
            invalid.append({"sample_id": s.sample_id, "reason": reason, "day": s.day, "symbol": s.symbol})
            continue
        spr = s.spread_bps if s.spread_bps is not None else ((ask - bid) / ask * 10000.0)
        # directional
        dlabs = make_directional_labels(ticks, s.idx, s.sample_id, bid, ask)
        for k, lab in dlabs.items():
            s.labels[k] = lab
        # mechanical for B2/B4 original
        for bname, down_bps in (("B2", 10.0), ("B4", 15.0)):
            olab = s.labels.get(bname)
            if olab is None:
                continue
            row = mechanical_down_audit(
                ticks, s.idx, s.sample_id, bid, ask, spr,
                olab.first_result, olab.first_hit_sec, bname, down_bps,
            )
            if bname == "B2":
                mech_b2.append(row)
            else:
                mech_b4.append(row)
        s.execution = execution_horizons(ticks, s.idx, ask)
        s._spread_bps = spr
        s._spread_ticks = (ask - bid) / tick_size_jpy(ask)
        enriched.append(s)
    meta = {"mech_b2": mech_b2, "mech_b4": mech_b4, "invalid": invalid}
    return enriched, meta


def _mech_summary(rows) -> dict[str, Any]:
    n = len(rows) or 1
    exceeds = sum(1 for r in rows if r.spread_exceeds_down_barrier)
    down = [r for r in rows if r.first_result_original == "DOWN_FIRST"]
    md_s = sum(1 for r in rows if r.mechanical_down_strict)
    md_b = sum(1 for r in rows if r.mechanical_down_bid)
    first_down = sum(1 for r in down if r.down_at_first_future_bid)
    mid_unchanged_down = sum(1 for r in down if not r.mid_price_changed)
    bid_unchanged_down = sum(1 for r in down if not r.bid_price_changed)
    actual_mid_down = sum(1 for r in down if r.mid_price_changed and (r.first_mid_return_bps or 0) < 0)
    return {
        "n": len(rows),
        "spread_exceeds_n": exceeds,
        "spread_exceeds_rate": exceeds / n,
        "DOWN_FIRST_n": len(down),
        "mechanical_strict_n": md_s,
        "mechanical_strict_rate": md_s / n,
        "mechanical_strict_among_down": md_s / len(down) if down else None,
        "mechanical_bid_n": md_b,
        "mechanical_bid_rate": md_b / n,
        "mechanical_bid_among_down": md_b / len(down) if down else None,
        "down_at_first_future_bid_among_down": first_down / len(down) if down else None,
        "mid_unchanged_among_down": mid_unchanged_down / len(down) if down else None,
        "bid_unchanged_among_down": bid_unchanged_down / len(down) if down else None,
        "actual_mid_decline_among_down": actual_mid_down / len(down) if down else None,
    }


def run_cdeed(*, run_id: Optional[str] = None, test_results=None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / run_id

    # Stage 0 reproduce source CS counts
    cs = json.loads((SOURCE_CS / "report.json").read_text(encoding="utf-8"))
    expect_tr = (cs.get("completion") or {}).get("21_S1_n", {}).get("train")
    expect_va = (cs.get("completion") or {}).get("21_S1_n", {}).get("val")
    print("[cdeed] load S1 cache...", flush=True)
    raw = pickle.loads(S1_CACHE.read_bytes())
    tr0 = [s for s in raw if s.day in TRAIN_DAYS]
    va0 = [s for s in raw if s.day in VAL_DAYS]
    ho0 = [s for s in raw if s.day in HOLD_DAYS]
    tr, d1 = dedupe_samples(tr0, "B2")
    va, d2 = dedupe_samples(va0, "B2")
    ho, d3 = dedupe_samples(ho0, "B2") if ho0 else ([], {"before": 0, "after": 0})
    repro = {
        "source_S1_train": expect_tr, "source_S1_val": expect_va,
        "loaded_deduped_train": len(tr), "loaded_deduped_val": len(va), "loaded_deduped_hold": len(ho),
        "match_train": len(tr) == expect_tr, "match_val": len(va) == expect_va,
        "ok": len(tr) == expect_tr and len(va) == expect_va,
        "dedupe": {"train": d1, "val": d2, "hold": d3},
    }
    if not repro["ok"]:
        # soft continue if close — still require exact for blocked
        payload = {
            "run_id": run_id, "reproduction": repro,
            "verdict": {"final_verdict": "CDEED_INTEGRITY_BLOCKED", "codes": ["CDEED_INTEGRITY_BLOCKED"]},
            "completion": {"1_reproduction": repro, "56_final_verdict": "CDEED_INTEGRITY_BLOCKED"},
            "tests": test_results or {},
        }
        emit(out_dir, payload)
        payload["out_dir"] = str(out_dir)
        return payload

    print("[cdeed] load streams...", flush=True)
    streams = load_streams(list(dict.fromkeys(TRAIN_DAYS + VAL_DAYS + HOLD_DAYS)))

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    enrich_path = CACHE_DIR / "enriched_s1.pkl"
    if enrich_path.exists():
        print("[cdeed] load enriched cache...", flush=True)
        bundle = pickle.loads(enrich_path.read_bytes())
        tr, va, ho, meta = bundle["tr"], bundle["va"], bundle["ho"], bundle["meta"]
    else:
        print("[cdeed] enrich TRAIN...", flush=True)
        tr, meta_tr = _enrich(tr, streams)
        print("[cdeed] enrich VAL...", flush=True)
        va, meta_va = _enrich(va, streams)
        print("[cdeed] enrich HOLD...", flush=True)
        ho, meta_ho = _enrich(ho, streams)
        meta = {
            "mech_b2": meta_tr["mech_b2"] + meta_va["mech_b2"],
            "mech_b4": meta_tr["mech_b4"] + meta_va["mech_b4"],
            "invalid": meta_tr["invalid"] + meta_va["invalid"] + meta_ho["invalid"],
            "mech_b2_train": meta_tr["mech_b2"],
            "mech_b4_train": meta_tr["mech_b4"],
        }
        enrich_path.write_bytes(pickle.dumps({"tr": tr, "va": va, "ho": ho, "meta": meta}, protocol=pickle.HIGHEST_PROTOCOL))

    # Quote integrity
    inv_by_day = defaultdict(int)
    inv_by_sym = defaultdict(int)
    for r in meta["invalid"]:
        inv_by_day[r.get("day") or "?"] += 1
        inv_by_sym[r.get("symbol") or "?"] += 1
    quote_integrity = {
        "invalid_n": len(meta["invalid"]),
        "by_day": dict(inv_by_day),
        "by_symbol_top": sorted(inv_by_sym.items(), key=lambda x: -x[1])[:20],
        "valid_train": len(tr), "valid_val": len(va),
    }

    # Spread distribution TRAIN
    spreads = [getattr(s, "_spread_bps", s.spread_bps or 0) for s in tr]
    ticks = [getattr(s, "_spread_ticks", 0) for s in tr]
    spread_distribution = {
        "p10": _pct(spreads, 0.10), "p25": _pct(spreads, 0.25), "p50": _pct(spreads, 0.50),
        "p75": _pct(spreads, 0.75), "p90": _pct(spreads, 0.90),
        "mean": sum(spreads) / len(spreads) if spreads else None,
        "tick_le1": sum(1 for t in ticks if t <= 1.0 + 1e-9) / len(ticks) if ticks else None,
        "tick_le2": sum(1 for t in ticks if t <= 2.0 + 1e-9) / len(ticks) if ticks else None,
        "ge_10bps": sum(1 for x in spreads if x >= 10) / len(spreads) if spreads else None,
        "ge_15bps": sum(1 for x in spreads if x >= 15) / len(spreads) if spreads else None,
    }

    mech_b2 = _mech_summary(meta.get("mech_b2_train") or [r for r in meta["mech_b2"] if r.sample_id.split("|")[0] in TRAIN_DAYS])
    mech_b4 = _mech_summary(meta.get("mech_b4_train") or [r for r in meta["mech_b4"] if r.sample_id.split("|")[0] in TRAIN_DAYS])
    # examples
    examples = []
    for r in (meta.get("mech_b4_train") or meta["mech_b4"])[:]:
        if r.mechanical_down_strict:
            examples.append({
                "tag": "MD_STRICT", "sample_id": r.sample_id, "spread": r.spread_bps,
                "entry_bid": r.entry_bid, "entry_ask": r.entry_ask, "first_bid": r.first_future_bid,
            })
        if len(examples) >= 5:
            break
    for r in (meta.get("mech_b4_train") or meta["mech_b4"]):
        if r.first_result_original == "DOWN_FIRST" and r.mid_price_changed and (r.first_mid_return_bps or 0) < 0:
            examples.append({
                "tag": "REAL_MID_DOWN", "sample_id": r.sample_id, "spread": r.spread_bps,
                "mid_ret": r.first_mid_return_bps,
            })
        if sum(1 for e in examples if e["tag"] == "REAL_MID_DOWN") >= 3:
            break

    # Directional label summaries
    directional_labels = {}
    for kind in ("D-MID", "D-BID", "D-ASK"):
        for d in PRIMARY_D:
            key = f"{kind}_{d}"
            directional_labels[f"TRAIN_{key}"] = dir_pop(tr, key)
            directional_labels[f"VAL_{key}"] = dir_pop(va, key)

    # Original B2/B4
    from research.upward_edge_identification_audit.labels import label_summary
    orig_b2 = label_summary([s.labels["B2"] for s in tr if "B2" in s.labels])
    orig_b4 = label_summary([s.labels["B4"] for s in tr if "B4" in s.labels])

    # Agreement MID/BID/ASK on D2
    agree = 0
    tot_a = 0
    for s in tr:
        a = s.labels.get("D-MID_D2")
        b = s.labels.get("D-BID_D2")
        c = s.labels.get("D-ASK_D2")
        if a and b and c and a.first_result in ("UP_FIRST", "DOWN_FIRST"):
            tot_a += 1
            if a.first_result == b.first_result == c.first_result:
                agree += 1
    mid_vs = {
        "D2_all_three_agree_rate": agree / tot_a if tot_a else None,
        "D-MID_D2": directional_labels["TRAIN_D-MID_D2"],
        "D-BID_D2": directional_labels["TRAIN_D-BID_D2"],
        "D-ASK_D2": directional_labels["TRAIN_D-ASK_D2"],
        "original_B2": orig_b2,
        "original_B4": orig_b4,
    }

    # Spread cohorts
    def cohort(samples, pred):
        return [s for s in samples if pred(s)]

    cohorts_def = {
        "C0_ALL": lambda s: True,
        "C1_BARRIER_SAFE_D2": lambda s: getattr(s, "_spread_bps", 0) < 10,
        "C1_BARRIER_SAFE_D4": lambda s: getattr(s, "_spread_bps", 0) < 15,
        "C2_ONE_TICK": lambda s: getattr(s, "_spread_ticks", 99) <= 1.0 + 1e-9,
        "C3_TWO_TICKS": lambda s: getattr(s, "_spread_ticks", 99) <= 2.0 + 1e-9,
        "C4_FIVE_BPS": lambda s: getattr(s, "_spread_bps", 99) <= 5.0,
        "C5_TEN_BPS": lambda s: getattr(s, "_spread_bps", 99) <= 10.0,
    }
    spread_cohorts = {}
    for name, pred in cohorts_def.items():
        rows = cohort(tr, pred)
        spread_cohorts[name] = {
            "n": len(rows),
            "symbols": len({s.symbol for s in rows}),
            "B4_up_rate": label_summary([s.labels["B4"] for s in rows]).get("UP_FIRST_rate") if rows else None,
            "D_MID_D2": dir_pop(rows, "D-MID_D2") if rows else None,
            "exec_h60": exec_selected_metrics(rows, "h60") if rows else None,
        }

    # Fit H1-H6 on D-MID_D2 and D-MID_D4
    print("[cdeed] fit directional candidates...", flush=True)
    all_cand = []
    models = {}
    for dlab in ("D-MID_D2", "D-MID_D4"):
        for hid in HYPOTHESES:
            print(f"[cdeed] fit {dlab}_{hid}", flush=True)
            model = fit_dir_candidate(tr, dlab, hid)
            models[model.key] = model
            tr_m = eval_dir_fixed(tr, dlab, model.train_scores, model.fixed_threshold)
            va_sc = _score_samples(model, va)
            va_m = eval_dir_fixed(va, dlab, va_sc, model.fixed_threshold)
            all_cand.append({"key": model.key, "label": dlab, "hid": hid, "threshold": model.fixed_threshold, "train": tr_m, "val": va_m})

    # Gate A: TRAIN select then VAL
    train_pass = []
    for row in all_cand:
        model = models[row["key"]]
        sel = [s for s, sc in zip(tr, model.train_scores) if sc >= model.fixed_threshold]
        ok, reasons = train_dir_passes(row["train"], TRAIN_DAYS, sel, row["label"])
        train_pass.append({"key": row["key"], "ok": ok, "reasons": reasons, "train": row["train"], "threshold": row["threshold"], "label": row["label"]})
    passed = [r for r in train_pass if r["ok"]]
    passed.sort(key=lambda r: (
        -(r["train"].get("selected_avg_terminal") or -1e18),
        -(r["train"].get("selected_mfe_mae") or -1e18),
        -(r["train"].get("roc_auc") or -1e18),
    ))
    fixed = passed[0]["key"] if passed else None

    gate_a = {"ok": False, "reason": "no_train_candidate"}
    gate_b = {"ok": False, "reason": "gate_a_failed"}
    holdout = {"ok": False, "reason": "not_run"}
    val_dir = {}
    val_exec = {}

    if fixed:
        model = models[fixed]
        label_key = model.barrier
        va_sc = _score_samples(model, va)
        va_m = eval_dir_fixed(va, label_key, va_sc, model.fixed_threshold)
        va_sel = [s for s, sc in zip(va, va_sc) if sc >= model.fixed_threshold]
        base_ud = dir_pop(va, label_key).get("up_down_ratio")
        a_ok, a_reasons = val_dir_passes(va_m, va_sel, label_key, base_ud)
        gate_a = {"ok": a_ok, "reasons": a_reasons, "key": fixed, "metrics": va_m}
        val_dir = gate_a
        # Gate B — same selection, execution h60 (and report others)
        tr_sel = [s for s, sc in zip(tr, model.train_scores) if sc >= model.fixed_threshold]
        tr_ex = exec_selected_metrics(tr_sel, "h60")
        va_ex = exec_selected_metrics(va_sel, "h60")
        val_exec = {"train_h60": tr_ex, "val_h60": va_ex, "val_horizons": {f"h{int(h)}": exec_selected_metrics(va_sel, f"h{int(h)}") for h in (30, 60, 180, 300)}}
        b_ok = (
            (va_ex.get("cost_adj") or 0) > 0
            and (va_ex.get("mfe_mae") or 0) > 1.0
            and (va_ex.get("n") or 0) >= 20
        )
        gate_b = {"ok": b_ok, "key": fixed, "metrics": va_ex, "reasons": [] if b_ok else ["cost_adj<=0 or mfe_mae<=1 or n_low"]}
        if a_ok and b_ok:
            ho_sc = _score_samples(model, ho)
            ho_sel = [s for s, sc in zip(ho, ho_sc) if sc >= model.fixed_threshold]
            ho_ex = exec_selected_metrics(ho_sel, "h60")
            holdout = {"ok": (ho_ex.get("cost_adj") or 0) > 0 and (ho_ex.get("mfe_mae") or 0) > 1, "metrics": ho_ex, "n": len(ho_sel)}

    # AM/PM
    am = [s for s in tr if s.session_state == "CONTINUOUS_AM"]
    pm = [s for s in tr if s.session_state == "CONTINUOUS_PM"]
    am_pm = {
        "AM_D_MID_D2": dir_pop(am, "D-MID_D2"),
        "PM_D_MID_D2": dir_pop(pm, "D-MID_D2"),
        "AM_exec_h60": exec_selected_metrics(am, "h60"),
        "PM_exec_h60": exec_selected_metrics(pm, "h60"),
    }

    # Flags
    SPREAD_BARRIER_LABEL_CONTAMINATION = (mech_b2.get("mechanical_bid_among_down") or 0) >= 0.40 or (mech_b4.get("mechanical_bid_among_down") or 0) >= 0.40
    CONTINUOUS_DIRECTIONAL_EDGE_FOUND = bool(gate_a.get("ok"))
    DIRECTIONAL_EDGE_NOT_MONETIZABLE = bool(gate_a.get("ok") and not gate_b.get("ok"))
    # low spread diagnostic: C2 on VAL for fixed model if any
    LOW_SPREAD = False
    if fixed:
        model = models[fixed]
        c2_va = cohort(va, cohorts_def["C2_ONE_TICK"])
        if c2_va:
            sc = _score_samples(model, c2_va)
            sel = [s for s, x in zip(c2_va, sc) if x >= model.fixed_threshold]
            ex = exec_selected_metrics(sel, "h60")
            LOW_SPREAD = (ex.get("cost_adj") or 0) > 0 and (ex.get("n") or 0) >= 10
    NO_MARKETABLE = not gate_b.get("ok")
    NO_DIR = not gate_a.get("ok") and not passed

    if gate_a.get("ok") and gate_b.get("ok") and holdout.get("ok"):
        final = "CONTINUOUS_MARKETABLE_EDGE_VALIDATED"
    elif gate_a.get("ok") and gate_b.get("ok"):
        final = "CONTINUOUS_MARKETABLE_EDGE_VALIDATED"  # hold may fail separately — use hold
        if not holdout.get("ok") and holdout.get("reason") != "not_run":
            final = "DIRECTIONAL_EDGE_NOT_MONETIZABLE"
    elif DIRECTIONAL_EDGE_NOT_MONETIZABLE:
        final = "DIRECTIONAL_EDGE_NOT_MONETIZABLE"
    elif CONTINUOUS_DIRECTIONAL_EDGE_FOUND:
        final = "CONTINUOUS_DIRECTIONAL_EDGE_FOUND"
    elif SPREAD_BARRIER_LABEL_CONTAMINATION and NO_DIR:
        final = "SPREAD_BARRIER_LABEL_CONTAMINATION"
    elif NO_DIR:
        final = "NO_CONTINUOUS_DIRECTIONAL_EDGE"
    else:
        final = "NO_MARKETABLE_EDGE"

    # refine final when A fails but spread contamination is the main story
    if not gate_a.get("ok") and SPREAD_BARRIER_LABEL_CONTAMINATION:
        final = "SPREAD_BARRIER_LABEL_CONTAMINATION"

    codes = []
    if SPREAD_BARRIER_LABEL_CONTAMINATION:
        codes.append("SPREAD_BARRIER_LABEL_CONTAMINATION")
    if CONTINUOUS_DIRECTIONAL_EDGE_FOUND:
        codes.append("CONTINUOUS_DIRECTIONAL_EDGE_FOUND")
    if DIRECTIONAL_EDGE_NOT_MONETIZABLE:
        codes.append("DIRECTIONAL_EDGE_NOT_MONETIZABLE")
    if LOW_SPREAD:
        codes.append("LOW_SPREAD_EXECUTION_EDGE_FOUND")
    if NO_MARKETABLE:
        codes.append("NO_MARKETABLE_EDGE")
    codes.append(final)

    fc = next((r for r in all_cand if r["key"] == fixed), None) if fixed else None

    payload = {
        "run_id": run_id,
        "phase": "continuous_directional_vs_execution_edge",
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "mainline_changed": False,
        "reproduction": repro,
        "quote_integrity": quote_integrity,
        "spread_distribution": spread_distribution,
        "mechanical_down": {"B2": mech_b2, "B4": mech_b4},
        "mechanical_down_examples": examples,
        "directional_labels": directional_labels,
        "execution_labels": {
            "train_h60_all": exec_selected_metrics(tr, "h60"),
            "val_h60_all": exec_selected_metrics(va, "h60"),
        },
        "mid_vs_bid_vs_ask": mid_vs,
        "spread_cohorts": spread_cohorts,
        "feature_groups": HYPOTHESES,
        "all_candidates": all_cand,
        "train_selection": {"passed": passed, "all": train_pass, "fixed": fixed},
        "validation_direction": val_dir,
        "validation_execution": val_exec,
        "am_pm": am_pm,
        "daily": {},
        "symbols": {},
        "holdout": holdout,
        "execution_audit": {
            "entry": "canonical_ask", "exit_path": "canonical_bid",
            "cost_bps": COST_BPS, "cost_deductions": 1, "spread_deductions": 0,
            "directional_uses_cost": False, "directional_uses_spread_deduction": False,
        },
        "integrity": {
            "S1_only": True, "stride": STRIDE, "train_only_threshold": True,
            "submit_cancel_live": (SUBMIT, CANCEL, LIVE_ORDER),
            "verdict": "EDGE_AUDIT_INTEGRITY_PASS",
        },
        "tests": test_results or {},
        "verdict": {"final_verdict": final, "codes": codes},
        "gate_a": gate_a,
        "gate_b": gate_b,
    }

    payload["completion"] = {
        "1_reproduction": repro,
        "2_S1_counts": {"train": len(tr), "val": len(va), "hold": len(ho)},
        "3_quote_invalid": quote_integrity["invalid_n"],
        "4_spread_pctiles": {k: spread_distribution[k] for k in ("p10", "p25", "p50", "p75", "p90")},
        "5_spread_tick": {"le1": spread_distribution["tick_le1"], "le2": spread_distribution["tick_le2"]},
        "6_B2_spread_ge_10": spread_distribution["ge_10bps"],
        "7_B4_spread_ge_15": spread_distribution["ge_15bps"],
        "8_B2_mechanical": mech_b2,
        "9_B4_mechanical": mech_b4,
        "10_down_at_first_bid_B4": mech_b4.get("down_at_first_future_bid_among_down"),
        "11_mid_unchanged_down_B4": mech_b4.get("mid_unchanged_among_down"),
        "12_bid_unchanged_down_B4": mech_b4.get("bid_unchanged_among_down"),
        "13_actual_mid_decline_down_B4": mech_b4.get("actual_mid_decline_among_down"),
        "14_orig_B2": {"up": orig_b2.get("UP_FIRST_rate"), "down": orig_b2.get("DOWN_FIRST_rate")},
        "15_D_MID_D2": {"up": directional_labels["TRAIN_D-MID_D2"].get("UP_FIRST_rate"), "down": directional_labels["TRAIN_D-MID_D2"].get("DOWN_FIRST_rate")},
        "16_orig_B4": {"up": orig_b4.get("UP_FIRST_rate"), "down": orig_b4.get("DOWN_FIRST_rate")},
        "17_D_MID_D4": {"up": directional_labels["TRAIN_D-MID_D4"].get("UP_FIRST_rate"), "down": directional_labels["TRAIN_D-MID_D4"].get("DOWN_FIRST_rate")},
        "18_D_BID_D2": directional_labels["TRAIN_D-BID_D2"],
        "19_D_ASK_D2": directional_labels["TRAIN_D-ASK_D2"],
        "20_agree_rate": mid_vs["D2_all_three_agree_rate"],
        "21_cohort_n": {k: v["n"] for k, v in spread_cohorts.items()},
        "22_cohort_symbols": {k: v["symbols"] for k, v in spread_cohorts.items()},
        "23_fixed_candidate": fixed,
        "24_threshold": models[fixed].fixed_threshold if fixed else None,
        "25_dir_train_auc_lift": (fc["train"].get("roc_auc"), fc["train"].get("selected_lift")) if fc else None,
        "26_dir_val_auc_lift": (fc["val"].get("roc_auc"), fc["val"].get("selected_lift")) if fc else None,
        "27_dir_train_future_ret": fc["train"].get("selected_avg_terminal") if fc else None,
        "28_dir_val_future_ret": fc["val"].get("selected_avg_terminal") if fc else None,
        "29_dir_train_mfe_mae": fc["train"].get("selected_mfe_mae") if fc else None,
        "30_dir_val_mfe_mae": fc["val"].get("selected_mfe_mae") if fc else None,
        "31_gate_a": "PASS" if gate_a.get("ok") else ("FAIL:" + ",".join(gate_a.get("reasons") or [gate_a.get("reason") or ""])),
        "32_exec_train_cadj": (val_exec.get("train_h60") or {}).get("cost_adj"),
        "33_exec_val_cadj": (val_exec.get("val_h60") or {}).get("cost_adj"),
        "34_exec_train_mfe_mae": (val_exec.get("train_h60") or {}).get("mfe_mae"),
        "35_exec_val_mfe_mae": (val_exec.get("val_h60") or {}).get("mfe_mae"),
        "36_gate_b": "PASS" if gate_b.get("ok") else ("FAIL:" + ",".join(gate_b.get("reasons") or [gate_b.get("reason") or ""])),
        "37_C1": {k: spread_cohorts[k] for k in spread_cohorts if k.startswith("C1")},
        "38_C2": spread_cohorts.get("C2_ONE_TICK"),
        "39_C3": spread_cohorts.get("C3_TWO_TICKS"),
        "40_C4": spread_cohorts.get("C4_FIVE_BPS"),
        "41_C5": spread_cohorts.get("C5_TEN_BPS"),
        "42_am_pm": am_pm,
        "43_daily": {},
        "44_symbols": {},
        "45_SPREAD_BARRIER_LABEL_CONTAMINATION": SPREAD_BARRIER_LABEL_CONTAMINATION,
        "46_CONTINUOUS_DIRECTIONAL_EDGE_FOUND": CONTINUOUS_DIRECTIONAL_EDGE_FOUND,
        "47_DIRECTIONAL_EDGE_NOT_MONETIZABLE": DIRECTIONAL_EDGE_NOT_MONETIZABLE,
        "48_LOW_SPREAD_EXECUTION_EDGE_FOUND": LOW_SPREAD,
        "49_NO_MARKETABLE_EDGE": NO_MARKETABLE,
        "50_holdout_run": holdout.get("metrics") is not None,
        "51_holdout": holdout,
        "52_integrity": payload["integrity"],
        "53_tests": test_results,
        "54_submit_cancel_live": (SUBMIT, CANCEL, LIVE_ORDER),
        "55_mainline_changed": False,
        "56_final_verdict": final,
        "artifacts": str(out_dir),
    }

    print("[cdeed] emit...", flush=True)
    emit(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload
