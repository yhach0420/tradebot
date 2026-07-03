"""
Assemble final deliverable CSVs from per-session aggregates.

Outputs into results/reports/fresh_pbv2_reanalysis/.
"""
import csv
import glob
import gzip
import json
import os

AGG = "temp/fresh_pbv2_reanalysis/agg"
OUT = "results/reports/fresh_pbv2_reanalysis"
os.makedirs(OUT, exist_ok=True)

SESSIONS = [
    ("20260624", "081514", "AM"), ("20260624", "122521", "PM"),
    ("20260625", "080340", "AM"), ("20260625", "122535", "PM"),
    ("20260629", "080236", "AM"), ("20260629", "122526", "PM"),
    ("20260630", "091118", "AM"),
    ("20260701", "080616", "AM"),
]

aggs = {}
blockers = {}
summaries = {}
for d, s, ap in SESSIONS:
    aggs[(d, ap)] = json.load(open(f"{AGG}/{d}_{s}.json", encoding="utf-8"))
    bp = f"{AGG}/{d}_{s}_blockers.json"
    if os.path.exists(bp):
        blockers[(d, ap)] = json.load(open(bp, encoding="utf-8"))
    summaries[(d, ap)] = json.load(
        open(f"results/small_paper/{d}/live_session_{s}/small_paper_summary.json", encoding="utf-8"))

# ------------------------------------------------------------------ A: funnel
rows = []
for d, s, ap in SESSIONS:
    a = aggs[(d, ap)]
    sm = summaries[(d, ap)]
    n = a["counts"]
    fr = a["final_reasons"]
    cf = a["freshness_cf"]
    stale_price = fr.get("data_stale_price", 0)
    stale_board = fr.get("data_stale_board", 0)
    event_stale = fr.get("event_stale_price", 0)
    top20 = "; ".join(f"{k}={v}" for k, v in list(a["final_reasons"].items())[:20])
    rows.append({
        "day": d, "ampm": ap, "session": s,
        "push_messages": sm.get("push_messages"),
        "gate_evaluations": sm.get("gate_evaluations"),
        "candidates": n["candidates"],
        "pre_gate_reject(am_pm/universe)": n["pre_gate"],
        "freshness_reject_total": n["stale"],
        "reject_data_stale_price": stale_price,
        "reject_data_stale_board": stale_board,
        "reject_event_stale_price": event_stale,
        "trade_stale_tag_count": sm.get("trade_stale_tag_count", ""),
        "freshness_pass(=PBv2_reached)": n["fresh"],
        "pbv2_or_gate_pass(candidate_stage)": n["accepted"],
        "final_accepted(entries)": sm.get("accepted_count"),
        "accepted_pbv2_pool": sm.get("pbv2_count", ""),
        "accepted_or_pool": sm.get("or_count", ""),
        "score_v2_hist": json.dumps(a["v2_hist"]),
        "score3_candidates": n["score3"],
        "score3_fresh": n["score3_fresh"],
        "score3_fresh_rejected": n["score3_fresh_rejected"],
        "cluster_guard_reject_count(runtime)": sm.get("cluster_guard_reject_count", ""),
        "or_blocked_count": sm.get("or_blocked_count", ""),
        "or_cap_full_count": sm.get("or_cap_full_count", ""),
        "reject_reason_top20": top20,
    })
with open(f"{OUT}/fresh_pbv2_reanalysis_daily_funnel.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

# --------------------------------------------------------- C: input distribution
rows = []
for d, s, ap in SESSIONS:
    a = aggs[(d, ap)]
    for k, byb in a["dist"].items():
        for b, st in byb.items():
            miss = a["missing"].get(f"{k}:{b}", 0)
            tot = st["n"] + miss
            rows.append({
                "day": d, "ampm": ap, "field": k, "candidate_bucket": b,
                "n_present": st["n"], "n_missing": miss,
                "missing_rate_pct": round(100.0 * miss / tot, 2) if tot else "",
                "p10": st["p10"], "p50": st["p50"], "p90": st["p90"],
            })
    for tok, v in a["board_token_fresh"].items():
        rows.append({"day": d, "ampm": ap, "field": "board_token(fresh)", "candidate_bucket": tok,
                     "n_present": v, "n_missing": "", "missing_rate_pct": "", "p10": "", "p50": "", "p90": ""})
    for k, st in a["ages"].items():
        rows.append({"day": d, "ampm": ap, "field": f"{k}_age_sec(audit)", "candidate_bucket": "all_evals",
                     "n_present": st["n"], "n_missing": "", "missing_rate_pct": "",
                     "p10": "", "p50": st["p50"], "p90": st["p90"]})
    for sym, cnt in a["top_symbols"]:
        rows.append({"day": d, "ampm": ap, "field": "top_symbol_share", "candidate_bucket": sym,
                     "n_present": cnt, "n_missing": "", "missing_rate_pct": "", "p10": "", "p50": "", "p90": ""})
with open(f"{OUT}/fresh_pbv2_reanalysis_input_distribution.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

# ------------------------------------------------ D: internal blockers per day
rows = []
for d, s, ap in SESSIONS:
    key = (d, ap)
    if key in blockers:
        b = blockers[key]
        for reason, v in b["blockers"].items():
            rows.append({"day": d, "ampm": ap, "method": "shadow_joined_reconstruction",
                         "first_blocker": reason, "count": v})
        for a_, b_, v in b["confusion"]:
            rows.append({"day": d, "ampm": ap, "method": "validation_internal_vs_recon",
                         "first_blocker": f"{a_} <- {b_}", "count": v})
    else:
        a = aggs[key]
        for reason, v in a["final_reasons"].items():
            if reason in ("data_stale_price", "data_stale_board", "am_pm_entry_stop", "ACCEPT"):
                continue
            rows.append({"day": d, "ampm": ap, "method": "recorded_final_reason(unmasked_era)",
                         "first_blocker": reason, "count": v})
        for reason, v in a["recon_reasons"].items():
            rows.append({"day": d, "ampm": ap, "method": "event_field_reconstruction",
                         "first_blocker": reason, "count": v})
with open(f"{OUT}/fresh_pbv2_reanalysis_pbv2_internal_blockers.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["day", "ampm", "method", "first_blocker", "count"])
    w.writeheader()
    w.writerows(rows)

# ------------------------------------------------ score3 fresh trace merge
with gzip.open(f"{OUT}/fresh_pbv2_reanalysis_score3_fresh_trace.csv.gz", "wt", newline="", encoding="utf-8") as fo:
    w = csv.writer(fo)
    hdr_written = False
    for p in sorted(glob.glob(f"{AGG}/*_score3_trace.csv.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as fi:
            r = csv.reader(fi)
            hdr = next(r)
            if not hdr_written:
                w.writerow(hdr)
                hdr_written = True
            for i, row in enumerate(r):
                if i >= 1500:  # cap per session to keep artifact small
                    break
                w.writerow(row)

# ------------------------------------------------ F: freshness counterfactual
rows = []
for d, s, ap in SESSIONS:
    a = aggs[(d, ap)]
    cf = a["freshness_cf"]
    c3 = a["freshness_cf_score3"]
    sm = summaries[(d, ap)]
    ev = cf.get("evals", 0)
    rows.append({
        "day": d, "ampm": ap,
        "evals": ev,
        "v1_pass(price<=3s AND board<=3s)": cf.get("v1_pass", 0),
        "v1_pass_rate_pct": round(100.0 * cf.get("v1_pass", 0) / ev, 1) if ev else "",
        "v2_pass(board<=3s; event/trade approx)": cf.get("v2_pass", 0),
        "v2_pass_rate_pct": round(100.0 * cf.get("v2_pass", 0) / ev, 1) if ev else "",
        "rescued_v1_to_v2": cf.get("rescued_v1_to_v2", 0),
        "board_fallback_used(runtime,6/30+)": cf.get("fallback_used_recorded", ""),
        "score3_evals": c3.get("score3", 0),
        "score3_v1_pass": c3.get("score3_v1_pass", 0),
        "score3_v2_pass": c3.get("score3_v2_pass", 0),
        "score3_rescued_by_v2": c3.get("score3_rescued", 0),
        "actual_accepted": sm.get("accepted_count"),
        "actual_total_pnl_yen_100": sm.get("total_pnl_yen_100"),
        "note": "PnL/PF/DD of counterfactuals not computable without execution replay; "
                "6/30 shows freshness relief alone (fallback active, stale->5%) did NOT restore accepts (cluster guard binding)",
    })
with open(f"{OUT}/fresh_pbv2_reanalysis_freshness_counterfactual.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

print("built funnel / input_distribution / internal_blockers / score3_trace / freshness_counterfactual")
