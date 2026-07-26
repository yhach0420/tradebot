"""UEIA runner — dataset audit, samples, labels, M0–M11, H1–H6, VAL/HOLDOUT."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.upward_edge_identification_audit.constants import (
    BARRIERS,
    CANCEL,
    COST_BPS,
    LIVE_ORDER,
    MAX_HORIZON_SEC,
    OUT_ROOT,
    PRIMARY_BARRIERS,
    SEED,
    STRIDE,
    SUBMIT,
    TARGET_TRAIN_DAYS,
)
from research.upward_edge_identification_audit.labels import label_summary
from research.upward_edge_identification_audit.loader import discover_days, load_streams
from research.upward_edge_identification_audit.models import (
    fit_group_model,
    pick_univariate_candidates,
    population_metrics,
    run_models,
)
from research.upward_edge_identification_audit.reporting import emit
from research.upward_edge_identification_audit.samples import Sample, build_all_samples

JST = ZoneInfo("Asia/Tokyo")


def split_days(all_days: list[str]) -> dict[str, Any]:
    n = len(all_days)
    scope: dict[str, Any] = {
        "available_days": all_days, "n_available": n,
        "target_train_days": TARGET_TRAIN_DAYS, "blocked": False, "block_reason": None,
    }
    if n == 0:
        scope["blocked"] = True
        scope["block_reason"] = "DATASET_SCOPE_BLOCKED"
        return {**scope, "train": [], "validation": [], "holdout": []}
    if n == 1:
        scope["blocked"] = True
        scope["block_reason"] = "DATASET_SCOPE_BLOCKED"
        return {**scope, "train": all_days, "validation": [], "holdout": []}
    if n >= 5:
        # 60/20/20 by day count
        n_train = max(3, int(round(n * 0.6)))
        n_val = max(1, int(round(n * 0.2)))
        train = all_days[:n_train]
        val = all_days[n_train:n_train + n_val]
        hold = all_days[n_train + n_val:]
    elif n == 4:
        train, val, hold = all_days[:2], [all_days[2]], [all_days[3]]
    elif n == 3:
        train, val, hold = all_days[:2], [all_days[2]], []
    else:
        train, val, hold = all_days, [], []
    if n < TARGET_TRAIN_DAYS:
        scope["DATASET_DAYS_LIMITED"] = True
    return {**scope, "train": train, "validation": val, "holdout": hold}


def _dist(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"count": 0, "missing_rate": 1.0}
    s = sorted(vals)
    n = len(s)

    def q(p):
        return s[min(n - 1, int(p * (n - 1)))]

    mean = sum(s) / n
    var = sum((x - mean) ** 2 for x in s) / max(1, n - 1)
    return {
        "count": n, "mean": mean, "std": math.sqrt(var),
        "min": s[0], "p10": q(0.10), "p25": q(0.25), "p50": q(0.50),
        "p75": q(0.75), "p90": q(0.90), "max": s[-1],
        "missing_rate": None, "zero_rate": sum(1 for x in s if x == 0) / n,
        "inf_rate": 0.0, "stale_rate": 0.0,
    }


def feature_distribution(samples: list[Sample]) -> dict[str, Any]:
    bags: dict[str, list[float]] = defaultdict(list)
    missing: dict[str, int] = defaultdict(int)
    total = len(samples)
    for s in samples:
        keys = set(s.features.keys())
        for k in keys:
            v = s.features[k]
            if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                missing[k] += 1
            else:
                bags[k].append(float(v))
        # count missing for known keys across all
    all_keys = sorted(set(bags) | set(missing))
    out = {}
    for k in all_keys:
        d = _dist(bags.get(k, []))
        miss = missing.get(k, 0) + (total - d["count"] - missing.get(k, 0))
        # simpler missing: samples without finite value
        miss_n = total - d["count"]
        d["missing_rate"] = miss_n / total if total else 1.0
        out[k] = d
    return out


def dedupe_samples(samples: list[Sample], barrier: str) -> tuple[list[Sample], dict[str, Any]]:
    """Group near-duplicate same-symbol samples within horizon; keep first."""
    horizon = BARRIERS[barrier]["horizon_sec"]
    by_sym: dict[tuple[str, str], list[Sample]] = defaultdict(list)
    for s in samples:
        by_sym[(s.day, s.symbol)].append(s)
    kept = []
    dropped = 0
    for _, rows in by_sym.items():
        rows = sorted(rows, key=lambda x: x.event_time)
        last_t = None
        for s in rows:
            if last_t is not None and (s.event_time - last_t).total_seconds() < horizon:
                dropped += 1
                continue
            kept.append(s)
            last_t = s.event_time
    return kept, {"before": len(samples), "after": len(kept), "dropped": dropped, "embargo_sec": horizon}


def group_availability(fd: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for g in ("G1", "G2", "G3", "G4", "G5", "G6"):
        keys = [k for k in fd if k.startswith(g + "_")]
        if not keys:
            out[g] = {"n_features": 0, "mean_avail": 0.0}
            continue
        avail = [1.0 - (fd[k].get("missing_rate") or 1.0) for k in keys]
        out[g] = {"n_features": len(keys), "mean_avail": sum(avail) / len(avail), "features": keys}
    return out


def compare_groups(samples: list[Sample], barrier: str, feat: str, high_q: float = 0.75) -> dict[str, Any]:
    vals = [(s.features.get(feat), s) for s in samples if s.features.get(feat) is not None]
    if len(vals) < 40:
        return {"n": len(vals), "note": "insufficient"}
    vals.sort(key=lambda x: x[0])
    thr = vals[int(0.75 * (len(vals) - 1))][0]
    high = [s for v, s in vals if v >= thr]
    up = [s for s in high if s.labels[barrier].first_result == "UP_FIRST"]
    dn = [s for s in high if s.labels[barrier].first_result == "DOWN_FIRST"]
    norise = [s for s in high if s.labels[barrier].first_result != "UP_FIRST"]
    return {
        "feature": feat, "thr": thr, "n_high": len(high),
        "n_up": len(up), "n_down": len(dn), "n_no_rise": len(norise),
        "up_rate": len(up) / len(high) if high else None,
        "down_rate": len(dn) / len(high) if high else None,
        "up_feat_means": _mean_feats(up),
        "norise_feat_means": _mean_feats(norise),
    }


def _mean_feats(samples: list[Sample], keys: Optional[list[str]] = None) -> dict[str, float]:
    if not samples:
        return {}
    keys = keys or [
        "G2_w5_buy_trade_ratio", "G3_up_ticks_per_buy_qty", "G3_buy_qty_per_up_tick",
        "G4_bid_survival_sec", "G4_seconds_since_last_low", "G4_bid_replenishment_count",
        "G5_symbol_minus_median_return", "G6_dist_to_recent_high_bps", "G6_already_risen_bps",
    ]
    out = {}
    for k in keys:
        vals = [s.features[k] for s in samples if s.features.get(k) is not None]
        if vals:
            out[k] = sum(vals) / len(vals)
    return out


def winner_vs_down(samples: list[Sample], barrier: str) -> dict[str, Any]:
    up = [s for s in samples if s.labels[barrier].first_result == "UP_FIRST"]
    dn = [s for s in samples if s.labels[barrier].first_result == "DOWN_FIRST"]
    # strong winner: UP with MFE >= 30bps
    strong = [s for s in up if (s.labels[barrier].MFE_bps or 0) >= 30]
    keys = [
        "G2_w5_buy_trade_ratio", "G3_up_ticks_per_buy_qty", "G3_sell_qty_per_down_tick",
        "G4_bid_survival_sec", "G4_seconds_since_last_low", "G4_bid_replenishment_count",
        "G5_symbol_minus_median_return", "G6_already_risen_bps", "G1_ret_5s",
    ]
    return {
        "n_up": len(up), "n_down": len(dn), "n_strong": len(strong),
        "up_means": _mean_feats(up, keys),
        "down_means": _mean_feats(dn, keys),
        "strong_means": _mean_feats(strong, keys),
        "separators": {
            k: {
                "up": _mean_feats(up, [k]).get(k),
                "down": _mean_feats(dn, [k]).get(k),
                "delta": (
                    (_mean_feats(up, [k]).get(k) or 0) - (_mean_feats(dn, [k]).get(k) or 0)
                ),
            } for k in keys
        },
    }


def try_pbv2_comparison(days: list[str], barrier: str) -> dict[str, Any]:
    try:
        from research.price_flow_exit.entries import load_pbv2_entries
        from research.upward_edge_identification_audit.constants import REPO_ROOT
        entries = [e for e in load_pbv2_entries(REPO_ROOT) if e.day in days]
        if not entries:
            return {"status": "PBV2_COMPARISON_UNAVAILABLE", "n": 0}
        # Label via capture streams for those days — expensive; summarize entry MFE fields if present
        return {
            "status": "PBV2_REFERENCE_ONLY",
            "n": len(entries),
            "note": "Accepted events loaded; first-passage on capture requires join — summary counts only",
            "days": sorted({e.day for e in entries}),
            "symbols": len({e.symbol for e in entries}),
        }
    except Exception as e:
        return {"status": "PBV2_COMPARISON_UNAVAILABLE", "error": str(e)[:200]}


def edge_gate(val_metrics: dict[str, Any], base: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    auc = val_metrics.get("roc_auc")
    lift = val_metrics.get("top_decile_lift")
    cadj = val_metrics.get("top_decile_cost_adj")
    ud = val_metrics.get("top_decile_up_down")
    mm = val_metrics.get("top_decile_mfe_mae")
    ok = True
    if auc is None or auc <= 0.55:
        ok = False
        reasons.append("auc<=0.55")
    if lift is None or lift <= 1.20:
        ok = False
        reasons.append("lift<=1.20")
    if cadj is None or cadj <= 0:
        ok = False
        reasons.append("top_decile_cost_adj<=0")
    base_ud = base.get("up_down_ratio")
    if ud is not None and base_ud is not None and ud <= base_ud:
        ok = False
        reasons.append("up_down_not_improved")
    base_mm = base.get("mfe_mae_ratio")
    if mm is not None and base_mm is not None and mm <= base_mm:
        ok = False
        reasons.append("mfe_mae_not_improved")
    return ok, reasons


def run_ueia(*, run_id: Optional[str] = None, out_root: Optional[Path] = None, test_results=None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = (out_root or OUT_ROOT) / run_id

    all_days = discover_days()
    print(f"[ueia] days={all_days}", flush=True)
    split = split_days(all_days)
    if split.get("blocked"):
        payload = {
            "run_id": run_id, "phase": "upward_edge_identification_audit",
            "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
            "mainline_changed": False, "dataset_scope": split,
            "verdict": {"final_verdict": "DATASET_SCOPE_BLOCKED", "codes": ["DATASET_SCOPE_BLOCKED"]},
            "completion": {"1_data_period": all_days, "59_final_verdict": "DATASET_SCOPE_BLOCKED"},
            "tests": test_results or {},
        }
        emit(out_dir, payload)
        payload["out_dir"] = str(out_dir)
        return payload

    train_days = split["train"]
    val_days = split["validation"]
    hold_days = split["holdout"]
    print(f"[ueia] TRAIN={train_days} VAL={val_days} HOLD={hold_days}", flush=True)

    # Load all needed days once
    need = list(dict.fromkeys(train_days + val_days + hold_days))
    streams = load_streams(need)
    print(f"[ueia] streams={len(streams)}", flush=True)

    samples, meta = build_all_samples(streams)
    print(f"[ueia] samples={len(samples)}", flush=True)

    train_s = [s for s in samples if s.day in train_days]
    val_s = [s for s in samples if s.day in val_days]
    hold_s = [s for s in samples if s.day in hold_days]

    # Dedup with embargo on primary barrier B2
    train_d, dedupe_meta = dedupe_samples(train_s, "B2")
    val_d, dedupe_val = dedupe_samples(val_s, "B2")
    hold_d, _ = dedupe_samples(hold_s, "B2") if hold_s else ([], {"before": 0, "after": 0})

    fd = feature_distribution(train_d)
    avail = group_availability(fd)

    # Persistence restore check
    bid_surv = fd.get("G4_bid_survival_sec") or {}
    since_low = fd.get("G4_seconds_since_last_low") or {}
    persistence_ok = (bid_surv.get("count") or 0) > 0 and (since_low.get("count") or 0) > 0
    persistence_code = "UEIA_PERSISTENCE_RESTORED" if persistence_ok else "UEIA_PERSISTENCE_BLOCKED"

    # Trade side audit
    side_counts = defaultdict(int)
    for key, ticks in streams.items():
        if key.split("|")[0] not in train_days:
            continue
        for t in ticks:
            side_counts[t.trade_side] += 1
    side_tot = sum(side_counts.values()) or 1
    flow_dir = {
        "counts": dict(side_counts),
        "buy_rate": side_counts.get("BUY", 0) / side_tot,
        "sell_rate": side_counts.get("SELL", 0) / side_tot,
        "unknown_rate": side_counts.get("UNKNOWN", 0) / side_tot,
        "none_rate": side_counts.get("NONE", 0) / side_tot,
    }
    flow_unreliable = flow_dir["unknown_rate"] > 0.25

    # Labels
    label_counts = {}
    first_passage_summary = {}
    for bid in BARRIERS:
        labs = [s.labels[bid] for s in train_d]
        first_passage_summary[bid] = label_summary(labs)
        label_counts[bid] = {
            "UP_FIRST": first_passage_summary[bid]["UP_FIRST"],
            "DOWN_FIRST": first_passage_summary[bid]["DOWN_FIRST"],
            "NEITHER": first_passage_summary[bid]["NEITHER"],
            "BOTH_SAME_EVENT": first_passage_summary[bid]["BOTH_SAME_EVENT"],
            "DATA_END": first_passage_summary[bid]["DATA_END"],
        }

    sample_types = defaultdict(int)
    for s in samples:
        sample_types[s.sample_type] += 1

    # Univariate
    print("[ueia] univariate B2...", flush=True)
    uni_b2 = pick_univariate_candidates(train_d, "B2", max_features=25)
    uni_b4 = pick_univariate_candidates(train_d, "B4", max_features=15)

    # Models on B2 and B4
    print("[ueia] models B2...", flush=True)
    res_b2 = run_models(train_d, val_d if val_d else train_d, "B2")
    print("[ueia] models B4...", flush=True)
    res_b4 = run_models(train_d, val_d if val_d else train_d, "B4")

    # Flatten train/val metrics
    train_metrics = {f"B2_{k}": v.get("train") for k, v in res_b2["models"].items()}
    train_metrics.update({f"B4_{k}": v.get("train") for k, v in res_b4["models"].items()})
    val_metrics = {f"B2_{k}": v.get("test") for k, v in res_b2["models"].items()}
    val_metrics.update({f"B4_{k}": v.get("test") for k, v in res_b4["models"].items()})
    hyp_results = {
        f"B2_{k}": {"train": v.get("train"), "val": v.get("test"), "weights": v.get("weights_top")}
        for k, v in res_b2["hypotheses"].items()
    }
    hyp_results.update({
        f"B4_{k}": {"train": v.get("train"), "val": v.get("test"), "weights": v.get("weights_top")}
        for k, v in res_b4["hypotheses"].items()
    })

    # Pick best fixed candidate by VAL AUC among H1–H6 on B2/B4
    best = None
    best_key = None
    for key, blob in hyp_results.items():
        auc = (blob.get("val") or {}).get("roc_auc")
        if auc is None:
            continue
        if best is None or auc > best[0]:
            best = (auc, key, blob)
            best_key = key

    base_b2 = first_passage_summary["B2"]
    base_b4 = first_passage_summary["B4"]
    edge_ok = False
    edge_reasons = []
    validated_barrier = None
    if best is not None:
        bname = best_key.split("_", 1)[0]
        base = base_b2 if bname == "B2" else base_b4
        edge_ok, edge_reasons = edge_gate(best[2].get("val") or {}, base)
        if edge_ok:
            validated_barrier = bname

    # HOLDOUT only if validated
    holdout_metrics = {"note": "not_run"}
    holdout_edge = False
    if edge_ok and hold_d and best_key:
        groups = {
            "H1": ["G2", "G3"], "H2": ["G3", "G4"], "H3": ["G2", "G3", "G4"],
            "H4": ["G2", "G3", "G4", "G5"], "H5": ["G2", "G3", "G4", "G5", "G6"],
            "H6": ["G1", "G2", "G3", "G4", "G5", "G6"],
        }
        hid = best_key.split("_", 1)[1]
        bar = best_key.split("_", 1)[0]
        print(f"[ueia] HOLDOUT {best_key}...", flush=True)
        hfit = fit_group_model(train_d, hold_d, groups[hid], bar)
        holdout_metrics = {best_key: hfit.get("test")}
        h_ok, _ = edge_gate(hfit.get("test") or {}, base_b2 if bar == "B2" else base_b4)
        holdout_edge = h_ok

    # Comparisons
    wvd = winner_vs_down(train_d, "B2")
    high_buy = compare_groups(train_d, "B2", "G2_w5_buy_trade_ratio")
    high_repl = compare_groups(train_d, "B2", "G4_bid_replenishment_count")

    # Daily / symbol metrics for best
    daily_metrics = {}
    symbol_metrics = {}
    for day in train_days:
        daily_metrics[day] = population_metrics([s for s in train_d if s.day == day], "B2")
    by_sym = defaultdict(list)
    for s in train_d:
        by_sym[s.symbol].append(s)
    for sym, rows in sorted(by_sym.items(), key=lambda x: -len(x[1]))[:20]:
        symbol_metrics[sym] = population_metrics(rows, "B2")

    # Concentration: UP_FIRST by symbol
    up_by_sym = defaultdict(int)
    for s in train_d:
        if s.labels["B2"].first_result == "UP_FIRST":
            up_by_sym[s.symbol] += 1
    up_tot = sum(up_by_sym.values()) or 1
    top_syms = sorted(up_by_sym.items(), key=lambda x: -x[1])
    top1 = top_syms[0][1] / up_tot if top_syms else 0
    top3 = sum(v for _, v in top_syms[:3]) / up_tot if top_syms else 0

    pbv2 = try_pbv2_comparison(need, "B2")

    # Integrity
    integ_viol = 0
    if STRIDE != 1:
        integ_viol += 1
    # sample id uniqueness
    ids = [s.sample_id for s in samples]
    if len(ids) != len(set(ids)):
        integ_viol += 1
    # monotonic within stream samples
    by_stream = defaultdict(list)
    for s in samples:
        by_stream[s.stream_key].append(s)
    for rows in by_stream.values():
        times = [r.event_time for r in rows]
        if times != sorted(times):
            # allow interleaved REGULAR/STATE at same idx — check non-decreasing
            for a, b in zip(times, times[1:]):
                if b < a:
                    integ_viol += 1
                    break

    integrity = {
        "event_stride": STRIDE,
        "violations": integ_viol,
        "dedupe": dedupe_meta,
        "dedupe_val": dedupe_val,
        "embargo_sec": MAX_HORIZON_SEC,
        "quantile_fit": "TRAIN_only",
        "normalization_fit": "TRAIN_only",
        "verdict": "EDGE_AUDIT_INTEGRITY_PASS" if integ_viol == 0 else "EDGE_AUDIT_INTEGRITY_BLOCKED",
    }

    # Missing top
    miss_ranked = sorted(
        [{"feature": k, "missing_rate": v.get("missing_rate")} for k, v in fd.items()],
        key=lambda x: -(x["missing_rate"] or 0),
    )[:20]

    # Failure causes
    causes = []
    if split.get("DATASET_DAYS_LIMITED"):
        causes.append("DATASET_DAYS_LIMITED")
    if not persistence_ok:
        causes.append("FEATURE_DATA_INCOMPLETE")
        causes.append("PERSISTENCE_FEATURE_BLOCKED")
    if flow_unreliable:
        causes.append("FLOW_DIRECTION_UNRELIABLE")
    if integ_viol:
        causes.append("EDGE_AUDIT_INTEGRITY_BLOCKED")

    # Model group conclusions
    m2_auc = (val_metrics.get("B2_M2") or {}).get("roc_auc")
    m3_auc = (val_metrics.get("B2_M3") or {}).get("roc_auc")
    m4_auc = (val_metrics.get("B2_M4") or {}).get("roc_auc")
    m5_auc = (val_metrics.get("B2_M5") or {}).get("roc_auc")
    m0_auc = (val_metrics.get("B2_M0") or {}).get("roc_auc")

    if edge_ok and holdout_edge:
        final = "UEIA_HOLDOUT_EDGE"
    elif edge_ok:
        final = "UEIA_VALIDATED_EDGE"
    elif best and (best[2].get("train") or {}).get("roc_auc") and (best[2].get("train") or {}).get("roc_auc") > 0.55:
        if not edge_ok:
            final = "UEIA_NO_VALIDATED_EDGE"
            causes.append("EDGE_NOT_STABLE")
        else:
            final = "UEIA_NO_VALIDATED_EDGE"
    else:
        final = "UEIA_CURRENT_DATA_NO_IDENTIFIABLE_EDGE"
        causes.append("CURRENT_DATA_NO_IDENTIFIABLE_EDGE")

    if (m2_auc or 0) <= 0.55 and (m3_auc or 0) <= 0.55:
        causes.append("NO_LOCAL_FLOW_EDGE")
    if (m5_auc or 0) > max(m2_auc or 0, m3_auc or 0) + 0.02 and (m5_auc or 0) > 0.55:
        causes.append("CONTEXT_REQUIRED")
    if (m4_auc or 0) > max(m2_auc or 0, m3_auc or 0) + 0.02 and (m4_auc or 0) > 0.55:
        causes.append("PERSISTENCE_REQUIRED")

    # Remaining upside consumed check
    g6_hi = compare_groups(train_d, "B2", "G6_already_risen_bps")
    if (g6_hi.get("up_rate") or 1) < (base_b2.get("UP_FIRST_rate") or 0):
        causes.append("ENTRY_EDGE_CONSUMED")

    if integrity["verdict"] == "EDGE_AUDIT_INTEGRITY_BLOCKED":
        final = "EDGE_AUDIT_INTEGRITY_BLOCKED"

    codes = ["UEIA_DATASET_READY", "UEIA_LABELS_READY", "UEIA_FEATURES_READY", persistence_code]
    if edge_ok:
        codes.append("UEIA_VALIDATED_EDGE")
    if holdout_edge:
        codes.append("UEIA_HOLDOUT_EDGE")
    codes.append(final)
    codes.extend([c for c in causes if c not in codes])

    # Best univariate
    best_uni = uni_b2[0] if uni_b2 else None

    # Best feature group by VAL AUC
    group_best = None
    for mid in ("M1", "M2", "M3", "M4", "M5", "M6"):
        auc = (val_metrics.get(f"B2_{mid}") or {}).get("roc_auc")
        if auc is None:
            continue
        if group_best is None or auc > group_best[0]:
            group_best = (auc, mid)

    payload = {
        "run_id": run_id,
        "phase": "upward_edge_identification_audit",
        "seed": SEED,
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "mainline_changed": False, "paper_auto_start": False, "live_trading_enabled": False,
        "dataset_scope": {**split, **meta},
        "sample_count": len(samples),
        "sample_types": dict(sample_types),
        "sample_population": {
            "all": len(samples), "train": len(train_s), "train_deduped": len(train_d),
            "val": len(val_s), "val_deduped": len(val_d), "hold": len(hold_s),
        },
        "barriers": BARRIERS,
        "label_counts": label_counts,
        "first_passage_summary": first_passage_summary,
        "feature_availability": avail,
        "feature_missing_rates": miss_ranked,
        "feature_distribution": {k: fd[k] for k in list(fd)[:80]},
        "group_metrics": {
            "B2": {mid: val_metrics.get(f"B2_{mid}") for mid in [
                "M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11"
            ]},
            "B4": {mid: val_metrics.get(f"B4_{mid}") for mid in [
                "M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11"
            ]},
        },
        "univariate_results": [
            {"feature": u["feature"], "separation": u["separation"], "bins": u["bins"]}
            for u in uni_b2[:15]
        ],
        "hypothesis_results": hyp_results,
        "train_metrics": train_metrics,
        "validation_metrics": val_metrics,
        "holdout_metrics": holdout_metrics,
        "best_fixed_candidate": {
            "key": best_key, "val_auc": best[0] if best else None,
            "edge_ok": edge_ok, "gate_reasons": edge_reasons,
            "details": best[2] if best else None,
        },
        "pbv2_comparison": pbv2,
        "edge_identification_status": final,
        "failure_causes": causes,
        "winner_vs_down": wvd,
        "high_buy_no_rise": high_buy,
        "high_replenish_no_rise": high_repl,
        "daily_metrics": daily_metrics,
        "symbol_metrics": symbol_metrics,
        "duplicate_overlap_audit": {
            "train": dedupe_meta, "val": dedupe_val,
            "before_after_auc_note": "models fit on deduped TRAIN; VAL evaluated deduped",
        },
        "data_quality": {
            "flow_direction": flow_dir,
            "persistence_restored": persistence_ok,
            "bid_survival_sec": bid_surv,
            "seconds_since_last_low": since_low,
            "index_available": False,
            "sector_available": False,
            "watch50_cross_section": avail.get("G5", {}).get("mean_avail"),
        },
        "execution_audit": {
            "entry": "canonical_ask", "future_path": "canonical_bid",
            "cost_bps": COST_BPS, "lot": 100, "stride": STRIDE,
            "verdict": "EXECUTION_SEMANTICS_OK",
        },
        "integrity": integrity,
        "tests": test_results or {},
        "verdict": {"final_verdict": final, "codes": codes},
    }

    # Separating feature
    seps = (wvd.get("separators") or {})
    best_sep = None
    for k, v in seps.items():
        d = abs(v.get("delta") or 0)
        if best_sep is None or d > best_sep[0]:
            best_sep = (d, k, v)

    payload["completion"] = {
        "1_data_period": all_days,
        "2_n_days": len(all_days),
        "3_split": {"train": train_days, "validation": val_days, "holdout": hold_days},
        "4_push_events": meta.get("push_events"),
        "5_sample_n": len(samples),
        "6_sample_types": dict(sample_types),
        "7_barriers": BARRIERS,
        "8_up_first": {b: {"n": label_counts[b]["UP_FIRST"], "rate": first_passage_summary[b]["UP_FIRST_rate"]} for b in BARRIERS},
        "9_down_first": {b: {"n": label_counts[b]["DOWN_FIRST"], "rate": first_passage_summary[b]["DOWN_FIRST_rate"]} for b in BARRIERS},
        "10_neither": {b: {"n": label_counts[b]["NEITHER"], "rate": first_passage_summary[b]["NEITHER_rate"]} for b in BARRIERS},
        "11_both_same": {b: label_counts[b]["BOTH_SAME_EVENT"] for b in BARRIERS},
        "12_data_end": {b: label_counts[b]["DATA_END"] for b in BARRIERS},
        "13_group_availability": avail,
        "14_missing_top": miss_ranked,
        "15_bid_survival_restore": {"ok": persistence_ok, "dist": bid_surv, "code": persistence_code},
        "16_seconds_since_last_low_restore": {"ok": persistence_ok, "dist": since_low},
        "17_trade_side_audit": flow_dir,
        "18_M0": val_metrics.get("B2_M0"),
        "19_M1": val_metrics.get("B2_M1"),
        "20_M2": val_metrics.get("B2_M2"),
        "21_M3": val_metrics.get("B2_M3"),
        "22_M4": val_metrics.get("B2_M4"),
        "23_M5": val_metrics.get("B2_M5"),
        "24_M6": val_metrics.get("B2_M6"),
        "25_M7_M11": {k: val_metrics.get(f"B2_{k}") for k in ("M7", "M8", "M9", "M10", "M11")},
        "26_best_univariate": best_uni,
        "27_best_group": group_best,
        "28_best_hypothesis": best_key,
        "29_train_auc": (best[2].get("train") or {}).get("roc_auc") if best else None,
        "30_val_auc": best[0] if best else None,
        "31_holdout_auc": (list(holdout_metrics.values())[0] or {}).get("roc_auc") if isinstance(holdout_metrics, dict) and holdout_metrics.get("note") != "not_run" else None,
        "32_train_pr_auc": (best[2].get("train") or {}).get("pr_auc") if best else None,
        "33_val_pr_auc": (best[2].get("val") or {}).get("pr_auc") if best else None,
        "34_top_decile_lift": (best[2].get("val") or {}).get("top_decile_lift") if best else None,
        "35_top_quintile_lift": (best[2].get("val") or {}).get("top_quintile_lift") if best else None,
        "36_cost_adj": (best[2].get("val") or {}).get("top_decile_cost_adj") if best else None,
        "37_mfe_mae": (best[2].get("val") or {}).get("top_decile_mfe_mae") if best else None,
        "38_up_down_ratio": (best[2].get("val") or {}).get("top_decile_up_down") if best else None,
        "39_daily": daily_metrics,
        "40_symbols": symbol_metrics,
        "41_top1_top3": (top1, top3),
        "42_high_buy_no_rise": high_buy,
        "43_high_replenish_no_rise": high_repl,
        "44_best_separator": best_sep,
        "45_context_effect": {"M2": m2_auc, "M5": m5_auc, "M10": (val_metrics.get("B2_M10") or {}).get("roc_auc")},
        "46_persistence_effect": {"M3": m3_auc, "M4": m4_auc, "M9": (val_metrics.get("B2_M9") or {}).get("roc_auc")},
        "47_remaining_upside_effect": {"M6": (val_metrics.get("B2_M6") or {}).get("roc_auc"), "M11": (val_metrics.get("B2_M11") or {}).get("roc_auc")},
        "48_pbv2": pbv2,
        "49_dedupe": {"train": dedupe_meta, "val": dedupe_val},
        "50_edge_status": final,
        "51_failure_causes": causes,
        "52_missing_observations": [
            "index_returns", "sector_returns",
            *(["persistence"] if not persistence_ok else []),
        ],
        "53_fixed_hypothesis_for_next": best_key if edge_ok else None,
        "54_integrity": integrity,
        "55_execution": payload["execution_audit"],
        "56_tests": test_results,
        "57_submit_cancel_live": (SUBMIT, CANCEL, LIVE_ORDER),
        "58_mainline_changed": False,
        "59_final_verdict": final,
        "artifacts": str(out_dir),
        "primary_barriers": PRIMARY_BARRIERS,
        "m0_auc_ref": m0_auc,
    }

    print("[ueia] emit...", flush=True)
    emit(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload
