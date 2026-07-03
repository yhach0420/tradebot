"""
Investigation E: decision parity across runtime structures, computed as a
per-candidate counterfactual on recorded data (no runtime re-execution).

Structures compared on identical recorded candidates:
  S1 = 6/29-6/30 runtime  (cluster guard csubs {0,2,3,5} + stop_low_mfe, freshness v1+fallback state as recorded)
  S2 = CORE_ONLY          (extensions off; gate chain identical to S1 -> parity vs S1 at gate level)
  S3 = 6/25-equivalent    (no cluster guard, no stop_low_mfe)
  S4 = current HEAD       (cluster guard csubs=[], reject cluster_id==5 only)

The shared prefix (high_drift, near_day, suitability(volume-shadow join),
momentum, board, reentry, quality) is identical in all structures; divergence
can only occur at the cluster/stop_low_mfe stage. For rows reaching that stage
we classify with the frozen production model to get cluster_id / csub.
"""
import csv
import gzip
import json
import sys
from collections import Counter

sys.path.insert(0, "src")
from small_paper.entry_cluster_classifier import EntryClusterModel, compute_entry_cluster_feature_fields  # noqa: E402

BASE = "results/small_paper"
MODEL = EntryClusterModel.load(__import__("pathlib").Path("configs/entry_cluster_guard_model.json"))
LB_THRESHOLD = 0.052267

SUIT_THRESHOLD = 54.695739
MOMENTUM_CUTOFF = 0.2546
BOARD_P33 = 0.437286
STALE = {"data_stale_price", "data_stale_board", "event_stale_price"}
PRE = {"am_pm_entry_stop", "outside_refresh_universe", "outside_allowed_trading_window"}

SESSIONS = [
    ("20260629", "080236", "AM"),
    ("20260629", "122526", "PM"),
    ("20260630", "091118", "AM"),
    ("20260701", "080616", "AM"),
]


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _dyn40(j):
    return any("dynamic" in str(j.get(k) or "") for k in ("universe_bucket", "universe_slot", "source_bucket"))


def prefix_blocker(j, shadow):
    """Shared gate prefix identical across S1-S4. Returns None if row reaches cluster stage."""
    if _dyn40(j):
        dist = abs(_f(j.get("day_high_distance_pct")) or _f(j.get("entry_near_day_high_pct")) or 0.0)
        r5, r10, r15 = (_f(j.get(k)) for k in ("entry_rise_5min_pct", "entry_rise_10min_pct", "entry_rise_15min_pct"))
        if dist >= 1.2:
            if r10 is not None and r10 < -0.15 and (r5 is None or (r5 > r10 and r5 <= 1.0)):
                return "high_drift_pullback"
            if dist >= 1.5:
                if r15 is not None and r15 < -0.5 and (r5 is None or r5 < 0.2):
                    return "high_drift_pullback"
                if r5 is not None and r5 < -0.5 and (r10 is None or r10 < -0.2):
                    return "high_drift_pullback"
        d2 = _f(j.get("day_high_distance_pct")) or _f(j.get("entry_near_day_high_pct"))
        mom = (_f(j.get("entry_momentum_score")) or _f(j.get("entry_momentum_continuation_score"))
               or _f(j.get("momentum_continuation_score")))
        if d2 is not None and d2 <= 1.5 and (mom or 0.0) < 0.30:
            return "near_day_high_low_momentum_dynamic40_guard"
    key = (j.get("symbol"), str(j.get("event_time") or "")[:19])
    hit = shadow.get(key)
    if hit is not None:
        score, p100 = hit
        if score is None or not p100:
            return "daytrade_suitability"
    m = _f(j.get("momentum_continuation_score"))
    if m is None or m > MOMENTUM_CUTOFF:
        return "momentum_low_required"
    b = _f(j.get("entry_order_book_imbalance"))
    if b is None or b <= BOARD_P33:
        return "entry_score_v2_below_threshold"
    if str(j.get("reentry_rsi_guard_blocked")).lower() == "true":
        return "reentry_rsi_guard_below60"
    if str(j.get("entry_quality_guard_blocked")).lower() == "true":
        return "entry_quality_guard"
    return None


def load_shadow(sdir):
    m = {}
    try:
        f = open(f"{sdir}/volume_gate_shadow_eval.jsonl", encoding="utf-8")
    except FileNotFoundError:
        return m
    with f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            m[(r.get("symbol"), str(r.get("timestamp") or "")[:19])] = (r.get("vol_liq_score"), bool(r.get("pass_v100")))
    return m


def run():
    parity_rows = []
    div_rows = []
    for day, sess, ampm in SESSIONS:
        sdir = f"{BASE}/{day}/live_session_{sess}"
        shadow = load_shadow(sdir)
        c = Counter()
        cluster_dist = Counter()
        with open(f"{sdir}/small_paper_events.jsonl", encoding="utf-8") as f:
            for line in f:
                if '"candidate"' not in line:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                if j.get("event_type") != "candidate":
                    continue
                r = j.get("gate_reject_reason")
                if r in STALE or r in PRE:
                    continue
                c["fresh_candidates"] += 1
                pb = prefix_blocker(j, shadow)
                if pb is not None:
                    c["prefix_reject_all_structures"] += 1
                    continue
                c["reached_cluster_stage"] += 1
                cls = MODEL.classify(j)
                cid = cls["cluster_id"]
                csub = cls["new_subcluster_id"]
                lb = _f(cls.get("liquidity_burst")) or 0.0
                cluster_dist[f"c{cid}_s{csub}"] += 1
                # S1: 6/29 runtime — reject cid==5 or csub in {0,2,3,5} unless lb exception
                s1_reject = (cid == 5 or csub in (0, 2, 3, 5)) and lb < LB_THRESHOLD
                # S3: 6/25 equivalent — no cluster guard
                s3_reject = False
                # S4: HEAD — csubs=[] → only cid==5
                s4_reject = (cid == 5) and lb < LB_THRESHOLD
                c["S1_pass"] += 0 if s1_reject else 1
                c["S3_pass"] += 0 if s3_reject else 1
                c["S4_pass"] += 0 if s4_reject else 1
                if s1_reject != s3_reject or s1_reject != s4_reject:
                    c["divergent"] += 1
                    if len(div_rows) < 3000:
                        div_rows.append([
                            day, ampm, j.get("event_time"), j.get("symbol"),
                            "entry_cluster_guard", f"c{cid}_s{csub}", round(lb, 6),
                            "REJECT" if s1_reject else "PASS",
                            "REJECT" if s1_reject else "PASS",  # S2 == S1 at gate level
                            "REJECT" if s3_reject else "PASS",
                            "REJECT" if s4_reject else "PASS",
                            j.get("momentum_continuation_score"),
                            j.get("entry_order_book_imbalance"),
                            j.get("update_count_before_entry"),
                        ])
        rec = {
            "day": day, "ampm": ampm,
            "fresh_candidates": c["fresh_candidates"],
            "prefix_reject_all_structures": c["prefix_reject_all_structures"],
            "reached_cluster_stage": c["reached_cluster_stage"],
            "S1_runtime_0629_pass": c["S1_pass"],
            "S2_core_only_pass": c["S1_pass"],
            "S3_pre625_equiv_pass": c["S3_pass"],
            "S4_head_pass": c["S4_pass"],
            "divergent_decisions": c["divergent"],
            "cluster_dist_top": json.dumps(dict(cluster_dist.most_common(6))),
        }
        parity_rows.append(rec)
        print(day, ampm, dict(c), dict(cluster_dist.most_common(6)))

    with open("results/reports/fresh_pbv2_reanalysis/fresh_pbv2_reanalysis_structure_parity.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(parity_rows[0].keys()))
        w.writeheader()
        w.writerows(parity_rows)
    with gzip.open("results/reports/fresh_pbv2_reanalysis/fresh_pbv2_reanalysis_first_divergence.csv.gz", "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day", "ampm", "event_time", "symbol", "divergence_stage", "cluster_assignment",
                    "liquidity_burst", "S1_runtime0629", "S2_core_only", "S3_pre625_equiv", "S4_head",
                    "momentum_continuation_score", "entry_order_book_imbalance", "update_count_before_entry"])
        w.writerows(div_rows)
    print("written parity + divergence")


if __name__ == "__main__":
    run()
