"""
Investigation G: representative case traces (raw PUSH -> enrich -> freshness ->
PBv2 -> OR -> cap -> final) built from recorded events + audit + volume shadow +
frozen cluster model + push_jsonl day-high computation.

Cases:
  A) 10 accepted entries on healthy day 6/25
  B) 20 score>=3 fresh rejected candidates on 6/29 (incl. cluster-stage kills)
  C) 20 candidates rescued by freshness-v2 on 7/1 (price stale >3s but evaluated/tagged)
  D) day movers on 6/29: symbols with biggest push-observed day gain vs entry outcome
"""
import csv
import glob
import gzip
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "src")
from small_paper.entry_cluster_classifier import EntryClusterModel  # noqa: E402
from pathlib import Path  # noqa: E402

MODEL = EntryClusterModel.load(Path("configs/entry_cluster_guard_model.json"))
OUT = "results/reports/fresh_pbv2_reanalysis"
STALE = {"data_stale_price", "data_stale_board", "event_stale_price"}
PRE = {"am_pm_entry_stop", "outside_refresh_universe", "outside_allowed_trading_window"}

COLS = ["case_group", "day", "symbol", "event_time",
        "raw_push_current_price", "raw_push_day_gain_pct_at_eval",
        "price_age_sec", "board_age_sec", "freshness_stage",
        "momentum_continuation_score", "entry_order_book_imbalance", "entry_score_v2",
        "vol_liq_score(shadow)", "suitability_pass",
        "day_high_distance_pct", "entry_rise_5min_pct", "entry_rise_10min_pct",
        "cluster_assignment", "liquidity_burst",
        "pbv2_stage_outcome", "or_stage_outcome", "final_reason", "final_outcome",
        "push_day_max_gain_pct"]

rows_out = []


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
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
            m[(r.get("symbol"), str(r.get("timestamp") or "")[:19])] = r
    return m


def load_audit(sdir):
    m = {}
    with open(f"{sdir}/entry_scan_audit.jsonl", encoding="utf-8") as f:
        for line in f:
            if '"entry_symbol_eval"' not in line:
                continue
            try:
                a = json.loads(line)
            except Exception:
                continue
            m[(a.get("symbol"), str(a.get("eval_end_ts") or "")[:19])] = a
    return m


def push_day_stats(day_iso, symbols):
    """prev-close-relative max gain from push jsonl (bounded read, selected symbols)."""
    out = {}
    for sym in symbols:
        p = f"data/push_jsonl/{day_iso}/{sym}.jsonl"
        if not os.path.exists(p):
            continue
        prev_close = None
        max_px = None
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                px = _f(r.get("CurrentPrice"))
                pc = _f(r.get("PreviousClose"))
                if pc:
                    prev_close = pc
                if px:
                    max_px = px if max_px is None else max(max_px, px)
        if prev_close and max_px:
            out[sym] = round((max_px - prev_close) / prev_close * 100.0, 2)
    return out


def trace_session(day, sess, want):
    """want: list of (case_group, predicate, cap)"""
    sdir = f"results/small_paper/{day}/live_session_{sess}"
    shadow = load_shadow(sdir)
    audit = load_audit(sdir)
    taken = Counter()
    picked = []
    with open(f"{sdir}/small_paper_events.jsonl", encoding="utf-8") as f:
        for line in f:
            try:
                j = json.loads(line)
            except Exception:
                continue
            et = j.get("event_type")
            if et not in ("candidate", "accepted"):
                continue
            for name, pred, cap in want:
                if taken[name] >= cap:
                    continue
                if pred(j, et):
                    taken[name] += 1
                    picked.append((name, j, et))
                    break
    day_iso = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    syms = sorted({j.get("symbol") for _, j, _ in picked})
    gains = push_day_stats(day_iso, syms)
    for name, j, et in picked:
        sym = j.get("symbol")
        ts = str(j.get("event_time") or "")[:19]
        a = audit.get((sym, ts), {})
        sh = shadow.get((sym, ts), {})
        r = j.get("gate_reject_reason") or ""
        internal = j.get("pbv2_internal_reason") or ""
        if r in STALE:
            fstage = f"REJECT({r})"
            pbv2 = "not_reached"
            orr = "not_reached"
        else:
            fstage = "PASS"
            pbv2 = "ACCEPT" if (not r or et == "accepted") else f"REJECT({internal or 'masked'})"
            orr = f"REJECT({r})" if r == "or_overlay_not_candidate" else ("n/a" if not r else r)
        cls = MODEL.classify(j)
        pc = _f(j.get("current_price"))
        prev_gain = ""
        rows_out.append(dict(zip(COLS, [
            name, day, sym, j.get("event_time"),
            pc, prev_gain,
            a.get("price_age_sec"), a.get("board_age_sec"), fstage,
            j.get("momentum_continuation_score"), j.get("entry_order_book_imbalance"),
            j.get("entry_expectancy_score_v2"),
            sh.get("vol_liq_score"), sh.get("pass_v100"),
            j.get("day_high_distance_pct"), j.get("entry_rise_5min_pct"), j.get("entry_rise_10min_pct"),
            f"c{cls['cluster_id']}_s{cls['new_subcluster_id']}", cls.get("liquidity_burst"),
            pbv2, orr, r or ("ACCEPTED" if et == "accepted" else ""),
            "ENTRY" if et == "accepted" else "NO_ENTRY",
            gains.get(sym, ""),
        ])))


def is_score3_fresh_reject(j, et):
    if et != "candidate":
        return False
    r = j.get("gate_reject_reason")
    if not r or r in STALE or r in PRE:
        return False
    try:
        return int(j.get("entry_expectancy_score_v2")) >= 3
    except (TypeError, ValueError):
        return False


trace_session("20260625", "080340", [("A_healthy_accepted_0625", lambda j, et: et == "accepted", 10)])
trace_session("20260629", "080236", [("B_score3_fresh_rejected_0629AM", is_score3_fresh_reject, 12)])
trace_session("20260629", "122526", [("B_score3_fresh_rejected_0629PM", is_score3_fresh_reject, 8)])


def is_v2_rescued(j, et):
    # trade-stale tagged rows: evaluated under v2 although price age exceeded 3s
    if et != "candidate":
        return False
    src = str(j.get("price_freshness_source") or "")
    return src == "liquidity_stale_trade"


trace_session("20260701", "080616", [("C_v2_rescued_0701", is_v2_rescued, 20)])

# D) big day movers on 6/29 vs outcome
day_iso = "2026-06-29"
all_syms = [os.path.basename(p)[:-6] for p in glob.glob(f"data/push_jsonl/{day_iso}/*.jsonl")]
gains = push_day_stats(day_iso, all_syms)
top = sorted(gains.items(), key=lambda x: -x[1])[:10]
acc_syms = set()
p = "results/small_paper/20260629/live_session_080236/small_paper_events.jsonl"
with open(p, encoding="utf-8") as f:
    for line in f:
        if '"accepted"' not in line:
            continue
        j = json.loads(line)
        if j.get("event_type") == "accepted":
            acc_syms.add(j.get("symbol"))
for sym, g in top:
    rows_out.append(dict(zip(COLS, [
        "D_day_mover_0629", "20260629", sym, "", "", "",
        "", "", "", "", "", "", "", "", "", "", "", "", "",
        "", "", "", "ENTRY" if sym in acc_syms else "NO_ENTRY", g,
    ])))

with gzip.open(f"{OUT}/fresh_pbv2_reanalysis_case_trace.csv.gz", "wt", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader()
    w.writerows(rows_out)
print("case trace rows:", len(rows_out))
for r in rows_out[:8]:
    print(r["case_group"], r["symbol"], r["final_reason"], r["cluster_assignment"], r["push_day_max_gain_pct"])
