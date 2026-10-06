"""Offline re-entry clock interaction audit. No Runtime / Paper / OPVAL / new policy."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.executable_target_v2_b_threshold.analyze import pack_metrics
from research.reentry_architecture_v1.analyze import build_episode_ledger
from research.reentry_clock_interaction_v2 import (
    ANALYSIS_ID,
    A2R2_STATUS,
    B2_FORMAL,
    C14_ID,
    C2_STATUS_MAINTAINED,
    C3_STARTED,
    ELIGIBLE_DAYS,
    EXPECTED_A_RE2_N,
    EXPECTED_A_RE2_PNL,
    NEW_FORWARD_N,
    NEW_POLICY,
    R2_RUNTIME_CANDIDATE,
    RUNTIME_CHANGED,
    TRUE_OOS,
    V1_FORMAL,
)
from research.reentry_clock_interaction_v2.analyze import (
    a0_parity,
    b0_parity,
    clock_inventory,
    common_anchor_diagnostic,
    decide,
    elapsed_block,
    elapsed_rows,
    enrich_ledger,
    flatten_crosstab,
    lineage_and_shift,
    overlap_block,
    re2_intersections,
    setup_reuse_block,
    slice_stats,
)
from research.reentry_clock_interaction_v2.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
A_CACHE = NATIVE / "results" / "research" / "current_entry_nonexec_mechanism" / "_work_cache"
B_CACHE = NATIVE / "results" / "research" / "uniform10_b_followup" / "_work_cache"

RE2_COLS = (
    "date",
    "session",
    "symbol",
    "anchor_time",
    "clock_class",
    "entry_kind",
    "fill_time",
    "current_trade_pnl",
    "prior_outcome",
    "prior_exit_family",
    "score_improved",
    "rank_improved",
    "price_above_prior_exit",
    "anchor_distance",
    "seconds_since_prior_exit",
    "elapsed_sec_from_first_fill",
    "elapsed_bin_prior_exit",
    "current_entry_score",
    "prior_entry_score",
    "current_rank",
    "prior_rank",
    "current_mid",
    "prior_exit_price",
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_stage(cache: Path, day: str, stage: str) -> dict:
    fp = cache / f"{day}_{stage}.json"
    if not fp.is_file():
        return {}
    return json.loads(fp.read_text(encoding="utf-8"))


def _slim_re2(rows: list[dict]) -> list[dict]:
    return [{k: r.get(k) for k in RE2_COLS} for r in rows]


def _st_n(st: dict | None) -> int:
    return int((st or {}).get("N") or 0)


def _st_pnl(st: dict | None):
    return (st or {}).get("PnL")


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE REENTRY CLOCK INTERACTION V2", flush=True)
    print("No new policy. R2 not a Runtime candidate. C3 not started.", flush=True)

    if list(FEATURE_ORDER) != [
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ]:
        print("STOP FEATURE_ORDER drift", flush=True)
        return 2
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        print("STOP rank_pass_gate drift", flush=True)
        return 2
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        print("STOP: C14 identity mismatch", flush=True)
        return 2

    days = list(ELIGIBLE_DAYS)
    a0_trades: list[dict] = []
    b0_trades: list[dict] = []
    a0_rank: list[dict] = []
    b0_rank: list[dict] = []
    miss = []
    for d in days:
        a0b = _load_stage(A_CACHE, d, "A0")
        b0b = _load_stage(B_CACHE, d, "B0")
        if not a0b.get("ok") or not b0b.get("ok"):
            miss.append(d)
            continue
        a0_trades.extend(a0b.get("trades") or [])
        b0_trades.extend(b0b.get("trades") or [])
        a0_rank.extend(a0b.get("rank_rows") or [])
        b0_rank.extend(b0b.get("rank_rows") or [])
    if miss or len(days) != 18:
        print("STOP missing Exact A0/B0 cache", miss, flush=True)
        return 2

    a_pack = pack_metrics(a0_trades, days)
    b_pack = pack_metrics(b0_trades, days)
    a_par = a0_parity(a_pack)
    b_par = b0_parity(b_pack)
    print("A0 Exact", a_par.get("observed"), "ok", a_par.get("ok"), flush=True)
    print("B0 Exact", b_par.get("observed"), "ok", b_par.get("ok"), flush=True)

    clock_inv = clock_inventory()
    empty_req = {
        "A_REENTRY2_N": None,
        "A_REENTRY2_PNL": None,
        "CLOCK_EXPLAINS_ORDINAL_REVERSAL": None,
        "ORDINAL_EFFECT_INDEPENDENT": None,
        "PRIMARY_MECHANISM": None,
        "RECOMMENDED_NEXT_RESEARCH": "STOP. Parity failed.",
        "VERDICT": "REENTRY_CLOCK_INTERACTION_AUDIT_FAILED",
    }
    if not a_par.get("ok") or not b_par.get("ok"):
        report = {
            "ANALYSIS_ID": ANALYSIS_ID,
            "required": empty_req,
            "parity": {"A0": a_par, "B0": b_par},
            "answers": {},
        }
        report["_markdown"] = build_markdown(report)
        write_artifacts(
            report,
            {
                "Summary": kv_rows(empty_req),
                "Parity": kv_rows({"A0": a_par, "B0": b_par}),
                "Safety": kv_rows({"submit": 0, "cancel": 0, "live": 0}),
            },
        )
        print("VERDICT: REENTRY_CLOCK_INTERACTION_AUDIT_FAILED", flush=True)
        return 0

    a_led = enrich_ledger(build_episode_ledger(a0_trades, clock="A", universe="A0", rank_rows=a0_rank))
    b_led = enrich_ledger(build_episode_ledger(b0_trades, clock="B", universe="B0", rank_rows=b0_rank))
    a_re2 = [r for r in a_led if r.get("entry_kind") == "REENTRY_2"]
    re2_st = slice_stats(a_re2)
    if int(re2_st.get("N") or 0) != int(EXPECTED_A_RE2_N) or abs(float(re2_st.get("PnL") or 0) - float(EXPECTED_A_RE2_PNL)) > 1.0:
        print("STOP A RE2 ledger mismatch", re2_st, flush=True)
        empty_req["VERDICT"] = "REENTRY_CLOCK_INTERACTION_AUDIT_FAILED"
        empty_req["RECOMMENDED_NEXT_RESEARCH"] = "STOP. A REENTRY_2 ledger did not match V1 N/PnL."
        empty_req["A_REENTRY2_N"] = re2_st.get("N")
        empty_req["A_REENTRY2_PNL"] = re2_st.get("PnL")
        report = {"ANALYSIS_ID": ANALYSIS_ID, "required": empty_req, "answers": {}}
        report["_markdown"] = build_markdown(report)
        write_artifacts(report, {"Summary": kv_rows(empty_req), "Safety": kv_rows({"submit": 0})})
        return 0

    inter = re2_intersections(a_re2)
    overlap = overlap_block(a_re2)
    shift = lineage_and_shift(a_led, b_led, a0_rank, b0_rank)
    elapsed_a = elapsed_block(a_led, clock="A")
    elapsed_b = elapsed_block(b_led, clock="B")
    common = common_anchor_diagnostic(a_led, b_led, a0_rank, b0_rank)
    setup_a = setup_reuse_block(a_led, clock="A")
    setup_b = setup_reuse_block(b_led, clock="B")
    gates = decide(
        a_par_ok=True,
        b_par_ok=True,
        re2_n=int(re2_st["N"]),
        re2_pnl=float(re2_st["PnL"]),
        overlap=overlap,
        shift=shift,
        elapsed_a=elapsed_a,
        elapsed_b=elapsed_b,
        common=common,
        inter=inter,
    )
    qs = gates.get("answers") or {}

    def compact_elapsed(block: dict) -> dict:
        out = {}
        for k, v in block.items():
            if k in ("clock", "reentry_all"):
                continue
            if isinstance(v, dict):
                out[k] = {"N": v.get("N"), "PnL": v.get("PnL"), "PF": v.get("PF"), "avg_PnL": v.get("avg_PnL")}
        return out

    req = {
        "A_REENTRY2_N": re2_st.get("N"),
        "A_REENTRY2_PNL": re2_st.get("PnL"),
        "RE2_AFTER_WIN_N": _st_n(overlap.get("RE2_AFTER_WIN")),
        "RE2_AFTER_WIN_PNL": _st_pnl(overlap.get("RE2_AFTER_WIN")),
        "RE2_SCORE_NOT_IMPROVED_N": _st_n(overlap.get("RE2_SCORE_NOT_IMPROVED")),
        "RE2_SCORE_NOT_IMPROVED_PNL": _st_pnl(overlap.get("RE2_SCORE_NOT_IMPROVED")),
        "RE2_PRICE_ABOVE_N": _st_n(overlap.get("RE2_PRICE_ABOVE")),
        "RE2_PRICE_ABOVE_PNL": _st_pnl(overlap.get("RE2_PRICE_ABOVE")),
        "RE2_TRIPLE_OVERLAP_N": _st_n(overlap.get("RE2_TRIPLE_OVERLAP")),
        "RE2_TRIPLE_OVERLAP_PNL": _st_pnl(overlap.get("RE2_TRIPLE_OVERLAP")),
        "RE2_UNEXPLAINED_REMAINDER_N": _st_n(overlap.get("RE2_UNEXPLAINED_REMAINDER")),
        "RE2_UNEXPLAINED_REMAINDER_PNL": _st_pnl(overlap.get("RE2_UNEXPLAINED_REMAINDER")),
        "COMMON_ANCHOR_N": clock_inv.get("COMMON_ANCHOR_N"),
        "A_ONLY_ANCHOR_N": clock_inv.get("A_ONLY_ANCHOR_N"),
        "B_ONLY_ANCHOR_N": clock_inv.get("B_ONLY_ANCHOR_N"),
        "ORDINAL_SHIFT_EPISODES_N": shift.get("ORDINAL_SHIFT_EPISODES_N"),
        "CLOCK_INSERTED_FILL_N": shift.get("CLOCK_INSERTED_FILL_N"),
        "CLOCK_REMOVED_FILL_N": shift.get("CLOCK_REMOVED_FILL_N"),
        "OCCUPANCY_CASCADE_EPISODES_N": shift.get("OCCUPANCY_CASCADE_EPISODES_N"),
        "A_RE2_BECOMES_B_RE1_N": shift.get("A_RE2_BECOMES_B_RE1_N"),
        "A_RE2_BECOMES_B_RE2_N": shift.get("A_RE2_BECOMES_B_RE2_N"),
        "A_RE2_BECOMES_B_RE3PLUS_N": shift.get("A_RE2_BECOMES_B_RE3PLUS_N"),
        "A_RE2_NO_DIRECT_MATCH_N": shift.get("A_RE2_NO_DIRECT_MATCH_N"),
        "A_ELAPSED_TIME": compact_elapsed(elapsed_a),
        "B_ELAPSED_TIME": compact_elapsed(elapsed_b),
        "CLOCK_EXPLAINS_ORDINAL_REVERSAL": gates.get("CLOCK_EXPLAINS_ORDINAL_REVERSAL"),
        "ORDINAL_EFFECT_INDEPENDENT": gates.get("ORDINAL_EFFECT_INDEPENDENT"),
        "PRIMARY_MECHANISM": gates.get("PRIMARY_MECHANISM"),
        "RECOMMENDED_NEXT_RESEARCH": gates.get("RECOMMENDED_NEXT_RESEARCH"),
        "VERDICT": gates.get("VERDICT"),
    }
    manifest = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "C14_ID": C14_ID,
        "C14_SHA": c14.get("sha256"),
        "episode_scope": "date x session x symbol",
        "nearest_anchor_matching": False,
        "new_policy": NEW_POLICY,
        "R2_RUNTIME_CANDIDATE": R2_RUNTIME_CANDIDATE,
        "V1_FORMAL_UNCHANGED": V1_FORMAL,
        "C2_STATUS_MAINTAINED": C2_STATUS_MAINTAINED,
        "B2_FORMAL_FROZEN": B2_FORMAL,
        "A2R2_STATUS": A2R2_STATUS,
        "C3_STARTED": C3_STARTED,
        "occupancy_approximation": False,
        "portfolio_rebuilt_on_common_anchors": False,
        "A0_B0_SOURCE": "frozen Exact Dual-Lane caches",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
    }
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": req,
        "parity": {"A0": a_par, "B0": b_par},
        "clock_inventory": clock_inv,
        "re2_intersections": inter,
        "overlap": {k: v for k, v in overlap.items() if k != "rows"},
        "lineage": {
            k: v
            for k, v in shift.items()
            if k not in ("episode_rows", "re2_match_rows")
        },
        "common_anchor": {k: v for k, v in common.items() if k != "sample_state_diff_rows"},
        "setup_reuse_A": setup_a,
        "setup_reuse_B": setup_b,
        "gates": {k: v for k, v in gates.items() if k != "answers"},
        "answers": qs,
        "manifest": manifest,
        "SAFETY": "submit/cancel/live=0/0/0",
    }
    report["_markdown"] = build_markdown(report)

    setup_rows = []
    for clock, block in (("A", setup_a), ("B", setup_b)):
        for k, st in (block.get("by_kind") or {}).items():
            rec = {"clock": clock, "kind": k}
            rec.update(st or {})
            setup_rows.append(rec)

    sheets = {
        "Summary": kv_rows(req),
        "Manifest": kv_rows(manifest),
        "Parity": kv_rows({"A0": a_par, "B0": b_par}),
        "Clock_Class": kv_rows(clock_inv),
        "RE2_Cross": (
            flatten_crosstab(inter.get("prior_outcome"), "prior_outcome")
            + flatten_crosstab(inter.get("prior_exit"), "prior_exit")
            + flatten_crosstab(inter.get("score"), "score")
            + flatten_crosstab(inter.get("rank"), "rank")
            + flatten_crosstab(inter.get("price"), "price")
            + flatten_crosstab(inter.get("anchor_distance"), "anchor_distance")
            + flatten_crosstab(inter.get("clock_class"), "clock_class")
        ),
        "Overlap": kv_rows({k: v for k, v in overlap.items() if not str(k).startswith("PAIR")} | {
            "PAIR_WIN_AND_SCORE_DN": overlap.get("PAIR_WIN_AND_SCORE_DN"),
            "PAIR_WIN_AND_PRICE_UP": overlap.get("PAIR_WIN_AND_PRICE_UP"),
            "PAIR_SCORE_DN_AND_PRICE_UP": overlap.get("PAIR_SCORE_DN_AND_PRICE_UP"),
        }),
        "Lineage": shift.get("episode_rows") or [{"empty": True}],
        "Ordinal_Shift": (shift.get("re2_match_rows") or []) + [kv_rows({
            "ORDINAL_SHIFT_EPISODES_N": shift.get("ORDINAL_SHIFT_EPISODES_N"),
            "CLOCK_INSERTED_FILL_N": shift.get("CLOCK_INSERTED_FILL_N"),
            "CLOCK_REMOVED_FILL_N": shift.get("CLOCK_REMOVED_FILL_N"),
            "OCCUPANCY_CASCADE_EPISODES_N": shift.get("OCCUPANCY_CASCADE_EPISODES_N"),
        })[0]],
        "Elapsed": elapsed_rows(elapsed_a) + elapsed_rows(elapsed_b),
        "Common_Anchor": kv_rows({k: v for k, v in common.items() if k != "sample_state_diff_rows"})
        + (common.get("sample_state_diff_rows") or []),
        "Setup_Reuse": setup_rows,
        "RE2_Ledger": _slim_re2(a_re2),
        "Mechanism": kv_rows({k: v for k, v in gates.items() if k != "answers"}) + kv_rows(qs),
        "Safety": kv_rows(
            {
                "OFFLINE_ONLY": True,
                "C2_changed": False,
                "C2_STATUS_MAINTAINED": C2_STATUS_MAINTAINED,
                "V1_FORMAL_UNCHANGED": V1_FORMAL,
                "B2_VERDICT_REWRITTEN": False,
                "A2R2_STATUS": A2R2_STATUS,
                "R2_RUNTIME_CANDIDATE": R2_RUNTIME_CANDIDATE,
                "C3_STARTED": C3_STARTED,
                "Runtime_changed": RUNTIME_CHANGED,
                "new_policy": NEW_POLICY,
                "cooldown_search": False,
                "threshold_search": False,
                "nearest_anchor_matching": False,
                "occupancy_approximation": False,
                "Paper_started": False,
                "OPVAL_started": False,
                "submit": 0,
                "cancel": 0,
                "live": 0,
            }
        ),
    }
    write_artifacts(report, sheets)
    print("OUT", OUT, flush=True)
    for k, v in req.items():
        if k in ("A_ELAPSED_TIME", "B_ELAPSED_TIME"):
            print(f"{k}: {json.dumps(v, ensure_ascii=False, default=str)}", flush=True)
        else:
            print(f"{k}: {v}", flush=True)
    print("V1_FORMAL_UNCHANGED:", V1_FORMAL, flush=True)
    print("STOP. Runtime/R2/C3/new-policy unchanged. Paper/OPVAL not operated.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
