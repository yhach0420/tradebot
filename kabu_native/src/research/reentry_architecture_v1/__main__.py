"""Offline re-entry architecture causal audit. No Runtime / Paper / OPVAL."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.inventory import build_inventory
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.reentry_architecture_v1 import (
    ANALYSIS_ID,
    B2_FORMAL,
    B2_VERDICT_REWRITTEN,
    C14_CHANGED,
    C14_ID,
    C2_CHANGED,
    C2_STATUS_MAINTAINED,
    C3_STARTED,
    ELIGIBLE_DAYS,
    MAX_WORKERS,
    NEW_FORWARD_N,
    NONEXEC_FORMAL,
    POLICY_NAME,
    RUNTIME_CHANGED,
    TRUE_OOS,
    WAIT_SEC,
)
from research.reentry_architecture_v1.analyze import (
    a0_parity,
    am_pm_reentry,
    answers_q,
    b0_parity,
    build_episode_ledger,
    decide,
    economic_row,
    first_reentry_split,
    grouped_slice,
    historical_candidate_vs_a0,
    primary_failure_mode,
    same_direction,
    score_rank_change_block,
    sequence_block,
    variant_headline,
)
from research.reentry_architecture_v1.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.reentry_architecture_v1.replay import process_day
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import CLOCK_GRID

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
CACHE = OUT / "_work_cache"
A_CACHE = NATIVE / "results" / "research" / "current_entry_nonexec_mechanism" / "_work_cache"
B_CACHE = NATIVE / "results" / "research" / "uniform10_b_followup" / "_work_cache"

LEDGER_COLS = (
    "clock",
    "universe",
    "date",
    "session",
    "symbol",
    "anchor_time",
    "entry_ordinal",
    "entry_kind",
    "fill_time",
    "exit_time",
    "prior_entry_time",
    "prior_exit_time",
    "prior_exit_reason",
    "prior_exit_family",
    "prior_outcome",
    "prior_trade_pnl",
    "prior_trade_MFE",
    "prior_trade_MAE",
    "prior_hold_sec",
    "seconds_since_prior_exit",
    "anchors_since_prior_exit",
    "anchor_distance",
    "current_entry_score",
    "prior_entry_score",
    "score_delta",
    "score_improved",
    "current_rank",
    "prior_rank",
    "rank_delta",
    "rank_improved",
    "current_mid",
    "prior_exit_price",
    "current_mid_vs_prior_exit",
    "price_above_prior_exit",
    "current_executable",
    "current_board_state",
    "feat_spread_bps",
    "feat_imbalance",
    "feat_mid_ret_60s",
    "feat_mid_ret_180s",
    "feat_event_rate_60s",
    "feat_log_bid_qty",
    "current_trade_pnl",
    "current_MFE",
    "current_MAE",
    "current_exit_reason",
    "current_hold_sec",
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_fp(day: str, stage: str) -> Path:
    return CACHE / f"{day}_{stage}.json"


def _load_cache(day: str, stage: str) -> dict | None:
    fp = _cache_fp(day, stage)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("stage") == stage:
        return body
    return None


def _save_cache(day: str, stage: str, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    slim = {
        k: body.get(k)
        for k in (
            "ok",
            "date",
            "stage",
            "reentry_policy",
            "executable_t0_only",
            "clock",
            "fire_mode",
            "trades",
            "lockout_skips",
            "admitted",
            "fills",
            "expired",
            "elapsed_sec",
            "blocker",
        )
    }
    _cache_fp(day, stage).write_text(json.dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def _pool(jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_day, job): (job["date"], job.get("stage")) for job in jobs}
        for fut in as_completed(futs):
            day, stage = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "date": day, "stage": stage, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"extract {day} {body.get('stage')} ok={body.get('ok')} "
                f"trades={len(body.get('trades') or [])} sec={body.get('elapsed_sec')} "
                f"blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _run_jobs(elig: list[dict], specs: list[dict], stage: str) -> list[dict]:
    jobs = []
    got = []
    by_date = {r["date"]: r for r in elig}
    for spec in specs:
        day = spec["date"]
        cached = _load_cache(day, stage)
        if cached:
            got.append(cached)
            print(f"extract {day} {stage} cache-hit trades={len(cached.get('trades') or [])}", flush=True)
        else:
            r = by_date[day]
            jobs.append({"date": day, "capture_path": r["capture_path"], "universe": r["universe_symbols"], **spec})
    for body in _pool(jobs):
        got.append(body)
        if body.get("ok"):
            _save_cache(str(body.get("date")), str(body.get("stage")), body)
    got.sort(key=lambda x: str(x.get("date") or ""))
    return got


def _collect_trades(rows: list[dict]) -> list[dict]:
    xs: list[dict] = []
    for r in rows:
        xs.extend(r.get("trades") or [])
    return xs


def _load_frozen_stage(cache: Path, day: str, stage: str) -> dict:
    fp = cache / f"{day}_{stage}.json"
    if not fp.is_file():
        return {}
    return json.loads(fp.read_text(encoding="utf-8"))


def _econ(trades: list[dict], days: list[str], *, clock: str, universe: str, rank_rows: list | None = None) -> tuple[dict, list]:
    led = build_episode_ledger(trades, clock=clock, universe=universe, rank_rows=rank_rows)
    return economic_row(trades, days, led), led


def _seq_rows(clock: str, seq: dict) -> list[dict]:
    out = []
    for k, v in seq.items():
        rec = {"clock": clock, "slice": k}
        rec.update(v or {})
        out.append(rec)
    return out


def _group_rows(clock: str, name: str, block: dict) -> list[dict]:
    out = []
    for k, v in block.items():
        rec = {"clock": clock, "family": name, "slice": k}
        rec.update(v or {})
        out.append(rec)
    return out


def _slim_ledger(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({k: r.get(k) for k in LEDGER_COLS})
    return out


def _daily_rows(label: str, econ: dict) -> list[dict]:
    days = ((econ.get("daily") or {}).get("days") or []) if isinstance(econ.get("daily"), dict) else []
    out = []
    for d in days:
        rec = dict(d)
        rec["variant"] = label
        out.append(rec)
    return out


def _tail_rows(label: str, econ: dict) -> list[dict]:
    ex = econ.get("exclude") or {}
    out = []
    for key in (
        "ex_top1_trade",
        "ex_top3_trades",
        "ex_best_day",
        "ex_top3_days",
        "ex_top_symbol",
        "ex_top3_symbols",
        "ex_285A",
    ):
        rec = dict(ex.get(key) or {})
        rec["variant"] = label
        rec["tail"] = key
        out.append(rec)
    return out


def _variant_sheet_row(label: str, econ: dict, bar: dict | None = None) -> dict:
    rec = {"variant": label, **variant_headline(econ)}
    if bar:
        rec.update({f"bar_{k}": v for k, v in bar.items()})
    return rec


def _integrity_report(a_par: dict, b_par: dict) -> dict:
    req = {
        "A0_PARITY": bool(a_par.get("ok")),
        "B0_PARITY": bool(b_par.get("ok")),
        "A_FIRST_PNL": None,
        "A_REENTRY_PNL": None,
        "B_FIRST_PNL": None,
        "B_REENTRY_PNL": None,
        "A_REENTRY1_PNL": None,
        "A_REENTRY2_PNL": None,
        "A_REENTRY3PLUS_PNL": None,
        "AFTER_WIN_REENTRY_PNL": None,
        "AFTER_LOSS_REENTRY_PNL": None,
        "PRIMARY_REENTRY_FAILURE_MODE": None,
        "R1_A_PNL": None,
        "R1_A_PF": None,
        "R1_A_DD": None,
        "R2_A_PNL": None,
        "R2_A_PF": None,
        "R2_A_DD": None,
        "R3_A_PNL": None,
        "R3_A_PF": None,
        "R3_A_DD": None,
        "R1_B_PNL": None,
        "R1_B_PF": None,
        "R1_B_DD": None,
        "R2_B_PNL": None,
        "R2_B_PF": None,
        "R2_B_DD": None,
        "R3_B_PNL": None,
        "R3_B_PF": None,
        "R3_B_DD": None,
        "BEST_MECHANISM_SUPPORTED_VARIANT": "NONE",
        "CROSS_CLOCK_SUPPORTED": False,
        "EXECUTABILITY_PLUS_REENTRY_PROMISING": False,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "RECOMMENDED_NEXT_STEP": "STOP. Parity failed. Do not interpret re-entry policies.",
        "VERDICT": "REENTRY_AUDIT_INTEGRITY_FAILED",
    }
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": req,
        "parity": {"A0": a_par, "B0": b_par},
        "answers": {},
    }
    report["_markdown"] = build_markdown(report)
    return report, req


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE REENTRY ARCHITECTURE V1", flush=True)
    print("C2/B2/A2 formal verdicts frozen. C14/Runtime not modified.", flush=True)

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

    inv = build_inventory()
    want = set(ELIGIBLE_DAYS)
    elig = [
        r
        for r in inv
        if r.get("date") in want and r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path")
    ]
    days = [r["date"] for r in elig]
    missing = [d for d in ELIGIBLE_DAYS if d not in set(days)]
    if missing or len(elig) != 18:
        print("STOP eligible days", missing, len(elig), flush=True)
        return 2
    print(f"eligible days={len(elig)} CLOCK_GRID_N={len(CLOCK_GRID)} ANALYSIS_ID={ANALYSIS_ID}", flush=True)

    a0_trades: list[dict] = []
    a2_trades: list[dict] = []
    b0_trades: list[dict] = []
    b2_trades: list[dict] = []
    a0_rank: list[dict] = []
    b0_rank: list[dict] = []
    frozen_miss = []
    for d in days:
        a0b = _load_frozen_stage(A_CACHE, d, "A0")
        a2b = _load_frozen_stage(A_CACHE, d, "A2")
        b0b = _load_frozen_stage(B_CACHE, d, "B0")
        b2b = _load_frozen_stage(B_CACHE, d, "B2")
        if not a0b.get("ok") or not a2b.get("ok") or not b0b.get("ok") or not b2b.get("ok"):
            frozen_miss.append(d)
            continue
        a0_trades.extend(a0b.get("trades") or [])
        a2_trades.extend(a2b.get("trades") or [])
        b0_trades.extend(b0b.get("trades") or [])
        b2_trades.extend(b2b.get("trades") or [])
        a0_rank.extend(a0b.get("rank_rows") or [])
        b0_rank.extend(b0b.get("rank_rows") or [])
    if frozen_miss:
        print("STOP missing frozen A0/A2/B0/B2 cache", frozen_miss, flush=True)
        return 2

    a0_econ, a0_led = _econ(a0_trades, days, clock="A", universe="A0", rank_rows=a0_rank)
    b0_econ, b0_led = _econ(b0_trades, days, clock="B", universe="B0", rank_rows=b0_rank)
    a2_econ, a2_led = _econ(a2_trades, days, clock="A", universe="A2")
    b2_econ, b2_led = _econ(b2_trades, days, clock="B", universe="B2")
    a_par = a0_parity(a0_econ.get("pack") or a0_econ)
    b_par = b0_parity(b0_econ.get("pack") or b0_econ)
    print("A0 Exact", a_par.get("observed"), "ok", a_par.get("ok"), flush=True)
    print("B0 Exact", b_par.get("observed"), "ok", b_par.get("ok"), flush=True)
    if not a_par.get("ok") or not b_par.get("ok"):
        report, req = _integrity_report(a_par, b_par)
        write_artifacts(
            report,
            {
                "Summary": kv_rows(req),
                "Manifest": kv_rows({"ANALYSIS_ID": ANALYSIS_ID}),
                "Parity": kv_rows({"A0": a_par, "B0": b_par}),
                "Safety": kv_rows({"submit": 0, "cancel": 0, "live": 0}),
            },
        )
        print("VERDICT: REENTRY_AUDIT_INTEGRITY_FAILED", flush=True)
        return 0

    a_seq = sequence_block(a0_led)
    b_seq = sequence_block(b0_led)
    a_prior = grouped_slice(a0_led, "prior_outcome", ("WIN", "DRAW", "LOSS"))
    b_prior = grouped_slice(b0_led, "prior_outcome", ("WIN", "DRAW", "LOSS"))
    a_exit = grouped_slice(a0_led, "prior_exit_family", ("EARLY_GUARD", "CONT_EXIT_600", "CONT_EXTEND_750", "SESSION_CLOSE", "OTHER"))
    b_exit = grouped_slice(b0_led, "prior_exit_family", ("EARLY_GUARD", "CONT_EXIT_600", "CONT_EXTEND_750", "SESSION_CLOSE", "OTHER"))
    a_dist = grouped_slice(a0_led, "anchor_distance", ("NEXT_ANCHOR", "TWO_LATER", "THREE_PLUS"))
    b_dist = grouped_slice(b0_led, "anchor_distance", ("NEXT_ANCHOR", "TWO_LATER", "THREE_PLUS"))
    a_chg = score_rank_change_block(a0_led)
    b_chg = score_rank_change_block(b0_led)
    a_tod = am_pm_reentry(a0_led)

    stages = []
    for pol in ("R1", "R2", "R3"):
        stages.append(("A", pol, False, f"A_{pol}"))
    for pol in ("R1", "R2", "R3"):
        stages.append(("B", pol, False, f"B_{pol}"))
    for pol in ("R1", "R2", "R3"):
        stages.append(("A", pol, True, f"A2_{pol}"))
    for pol in ("R1", "R2", "R3"):
        stages.append(("B", pol, True, f"B2_{pol}"))

    collected: dict[str, list[dict]] = {}
    for clock, pol, exe, stage in stages:
        specs = [
            {
                "date": d,
                "stage": stage,
                "reentry_policy": pol,
                "executable_t0_only": exe,
                "clock": clock,
            }
            for d in days
        ]
        rows = _run_jobs(elig, specs, stage)
        bad = [r for r in rows if not r.get("ok")]
        if bad:
            print("STOP extract failed", stage, [(r.get("date"), r.get("blocker")) for r in bad], flush=True)
            return 2
        collected[stage] = _collect_trades(rows)
        print(f"{stage} trades={len(collected[stage])}", flush=True)

    a_vars = {"R0": a0_econ}
    b_vars = {"R0": b0_econ}
    a2_vars = {"R0": a2_econ}
    b2_vars = {"R0": b2_econ}
    a_ledgers = {"R0": a0_led}
    b_ledgers = {"R0": b0_led}
    for pol in ("R1", "R2", "R3"):
        ae, al = _econ(collected[f"A_{pol}"], days, clock="A", universe=f"A_{pol}")
        be, bl = _econ(collected[f"B_{pol}"], days, clock="B", universe=f"B_{pol}")
        a2e, _ = _econ(collected[f"A2_{pol}"], days, clock="A", universe=f"A2_{pol}")
        b2e, _ = _econ(collected[f"B2_{pol}"], days, clock="B", universe=f"B2_{pol}")
        a_vars[pol] = ae
        b_vars[pol] = be
        a2_vars[pol] = a2e
        b2_vars[pol] = b2e
        a_ledgers[pol] = al
        b_ledgers[pol] = bl
        print(f"A_{pol}", variant_headline(ae), flush=True)
        print(f"B_{pol}", variant_headline(be), flush=True)
        print(f"A2_{pol}", variant_headline(a2e), flush=True)
        print(f"B2_{pol}", variant_headline(b2e), flush=True)

    a_bars = {p: historical_candidate_vs_a0(a_vars[p], a0_econ) for p in ("R1", "R2", "R3")}
    b_dir = {}
    for p in ("R1", "R2", "R3"):
        da = (a_vars[p].get("PnL") or 0) - (a0_econ.get("PnL") or 0)
        db = (b_vars[p].get("PnL") or 0) - (b0_econ.get("PnL") or 0)
        b_dir[p] = same_direction(da, db)

    mode = primary_failure_mode(
        seq=a_seq,
        after=a_seq,
        dist=a_dist,
        chg=a_chg,
        tod=a_tod,
        r1=a_vars.get("R1"),
        a0=a0_econ,
    )
    qs = answers_q(seq=a_seq, chg=a_chg, dist=a_dist, mode=mode)
    gates = decide(
        a0_ok=True,
        b0_ok=True,
        a0=a0_econ,
        b0=b0_econ,
        a_vars=a_vars,
        b_vars=b_vars,
        a2_vars=a2_vars,
        b2_vars=b2_vars,
        a_bars=a_bars,
        b_dir=b_dir,
        mode=mode,
        a_seq=a_seq,
        b_seq=b_seq,
    )

    a_split = first_reentry_split(a0_led)
    b_split = first_reentry_split(b0_led)
    req = {
        "A0_PARITY": True,
        "B0_PARITY": True,
        "A_FIRST_PNL": (a_split.get("first_entry") or {}).get("PnL"),
        "A_REENTRY_PNL": (a_split.get("re_entry") or {}).get("PnL"),
        "B_FIRST_PNL": (b_split.get("first_entry") or {}).get("PnL"),
        "B_REENTRY_PNL": (b_split.get("re_entry") or {}).get("PnL"),
        "A_REENTRY1_PNL": (a_seq.get("REENTRY_1") or {}).get("PnL"),
        "A_REENTRY2_PNL": (a_seq.get("REENTRY_2") or {}).get("PnL"),
        "A_REENTRY3PLUS_PNL": (a_seq.get("REENTRY_3_PLUS") or {}).get("PnL"),
        "AFTER_WIN_REENTRY_PNL": (a_seq.get("AFTER_WIN") or {}).get("PnL"),
        "AFTER_LOSS_REENTRY_PNL": (a_seq.get("AFTER_LOSS") or {}).get("PnL"),
        "PRIMARY_REENTRY_FAILURE_MODE": gates.get("PRIMARY_REENTRY_FAILURE_MODE"),
        "R1_A_PNL": a_vars["R1"].get("PnL"),
        "R1_A_PF": a_vars["R1"].get("PF"),
        "R1_A_DD": a_vars["R1"].get("maxDD"),
        "R2_A_PNL": a_vars["R2"].get("PnL"),
        "R2_A_PF": a_vars["R2"].get("PF"),
        "R2_A_DD": a_vars["R2"].get("maxDD"),
        "R3_A_PNL": a_vars["R3"].get("PnL"),
        "R3_A_PF": a_vars["R3"].get("PF"),
        "R3_A_DD": a_vars["R3"].get("maxDD"),
        "R1_B_PNL": b_vars["R1"].get("PnL"),
        "R1_B_PF": b_vars["R1"].get("PF"),
        "R1_B_DD": b_vars["R1"].get("maxDD"),
        "R2_B_PNL": b_vars["R2"].get("PnL"),
        "R2_B_PF": b_vars["R2"].get("PF"),
        "R2_B_DD": b_vars["R2"].get("maxDD"),
        "R3_B_PNL": b_vars["R3"].get("PnL"),
        "R3_B_PF": b_vars["R3"].get("PF"),
        "R3_B_DD": b_vars["R3"].get("maxDD"),
        "BEST_MECHANISM_SUPPORTED_VARIANT": gates.get("BEST_MECHANISM_SUPPORTED_VARIANT"),
        "CROSS_CLOCK_SUPPORTED": gates.get("CROSS_CLOCK_SUPPORTED"),
        "EXECUTABILITY_PLUS_REENTRY_PROMISING": gates.get("EXECUTABILITY_PLUS_REENTRY_PROMISING"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "RECOMMENDED_NEXT_STEP": gates.get("RECOMMENDED_NEXT_STEP"),
        "VERDICT": gates.get("VERDICT"),
    }
    manifest = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "C14_ID": C14_ID,
        "C14_SHA": c14.get("sha256"),
        "WAIT_SEC": WAIT_SEC,
        "FILL_SOT": "is_executable_continuous_board",
        "CLOCK_A": "CURRENT IRREGULAR CLOCK_GRID production",
        "CLOCK_B": "UNIFORM10 shifted_grid research-only",
        "CLOCK_GRID": [f"{h:02d}:{m:02d}" for h, m in CLOCK_GRID],
        "development_days": days,
        "policies": POLICY_NAME,
        "C2_STATUS_MAINTAINED": C2_STATUS_MAINTAINED,
        "B2_FORMAL_FROZEN": B2_FORMAL,
        "NONEXEC_FORMAL_UNCHANGED": NONEXEC_FORMAL,
        "A2_STANDALONE_VERDICT_UNCHANGED": True,
        "occupancy_used": False,
        "trade_deletion": False,
        "cooldown_search": False,
        "score_threshold_used": False,
        "new_model": False,
        "episode_scope": "date x session x symbol",
        "R0_SOURCE": "frozen Exact Dual-Lane caches A0/B0/A2/B2",
    }
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": req,
        "parity": {"A0": a_par, "B0": b_par},
        "A0": variant_headline(a0_econ),
        "B0": variant_headline(b0_econ),
        "A2_R0": variant_headline(a2_econ),
        "B2_R0": variant_headline(b2_econ),
        "A_sequence": a_seq,
        "B_sequence": b_seq,
        "A_prior_outcome": a_prior,
        "B_prior_outcome": b_prior,
        "A_prior_exit": a_exit,
        "B_prior_exit": b_exit,
        "A_anchor_distance": a_dist,
        "B_anchor_distance": b_dist,
        "A_score_rank_change": a_chg,
        "B_score_rank_change": b_chg,
        "A_variants": {p: variant_headline(a_vars[p]) for p in ("R0", "R1", "R2", "R3")},
        "B_variants": {p: variant_headline(b_vars[p]) for p in ("R0", "R1", "R2", "R3")},
        "A2_variants": {p: variant_headline(a2_vars[p]) for p in ("R0", "R1", "R2", "R3")},
        "B2_variants": {p: variant_headline(b2_vars[p]) for p in ("R0", "R1", "R2", "R3")},
        "A_candidate_bar": a_bars,
        "B_direction": b_dir,
        "gates": gates,
        "answers": qs,
        "manifest": manifest,
        "B2_FORMAL_UNCHANGED": B2_FORMAL,
        "C2_STATUS_MAINTAINED": C2_STATUS_MAINTAINED,
        "SAFETY": "submit/cancel/live=0/0/0",
    }
    report["_markdown"] = build_markdown(report)

    a_var_rows = [_variant_sheet_row(f"A_{p}", a_vars[p], a_bars.get(p)) for p in ("R0", "R1", "R2", "R3")]
    b_var_rows = [_variant_sheet_row(f"B_{p}", b_vars[p]) for p in ("R0", "R1", "R2", "R3")]
    inter_rows = []
    for p in ("R0", "R1", "R2", "R3"):
        inter_rows.append(_variant_sheet_row(f"A2_{p}", a2_vars[p]))
        inter_rows.append(_variant_sheet_row(f"B2_{p}", b2_vars[p]))

    daily = []
    tail = []
    for p in ("R0", "R1", "R2", "R3"):
        daily.extend(_daily_rows(f"A_{p}", a_vars[p]))
        daily.extend(_daily_rows(f"B_{p}", b_vars[p]))
        daily.extend(_daily_rows(f"A2_{p}", a2_vars[p]))
        daily.extend(_daily_rows(f"B2_{p}", b2_vars[p]))
        tail.extend(_tail_rows(f"A_{p}", a_vars[p]))
        tail.extend(_tail_rows(f"B_{p}", b_vars[p]))

    sheets = {
        "Summary": kv_rows(req),
        "Manifest": kv_rows(manifest),
        "Parity": kv_rows({"A0": a_par, "B0": b_par}),
        "Episode_Ledger": _slim_ledger(a0_led + b0_led),
        "Sequence": _seq_rows("A", a_seq) + _seq_rows("B", b_seq),
        "Prior_Outcome": _group_rows("A", "prior_outcome", a_prior) + _group_rows("B", "prior_outcome", b_prior),
        "Prior_Exit": _group_rows("A", "prior_exit", a_exit) + _group_rows("B", "prior_exit", b_exit),
        "Anchor_Distance": _group_rows("A", "anchor_distance", a_dist) + _group_rows("B", "anchor_distance", b_dist),
        "Score_Rank_Change": _group_rows("A", "score_rank", a_chg) + _group_rows("B", "score_rank", b_chg),
        "A_Variants": a_var_rows,
        "B_Variants": b_var_rows,
        "Executability_Interaction": inter_rows,
        "Daily": daily,
        "Tail_Robustness": tail,
        "Mechanism": kv_rows(gates) + kv_rows(qs),
        "Safety": kv_rows(
            {
                "OFFLINE_ONLY": True,
                "C2_changed": C2_CHANGED,
                "C2_STATUS_MAINTAINED": C2_STATUS_MAINTAINED,
                "C3_STARTED": C3_STARTED,
                "C14_changed": C14_CHANGED,
                "Runtime_changed": RUNTIME_CHANGED,
                "CLOCK_changed": False,
                "ENTRY_changed": False,
                "EXIT_changed": False,
                "Fill_changed": False,
                "B2_VERDICT_REWRITTEN": B2_VERDICT_REWRITTEN,
                "A2_STANDALONE_VERDICT_UNCHANGED": True,
                "NONEXEC_FORMAL_UNCHANGED": NONEXEC_FORMAL,
                "occupancy_used": False,
                "trade_deletion": False,
                "cooldown_search": False,
                "score_threshold": False,
                "new_model": False,
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
        print(f"{k}: {v}", flush=True)
    print("B2_FORMAL_UNCHANGED:", B2_FORMAL, flush=True)
    print("C2_STATUS_MAINTAINED:", C2_STATUS_MAINTAINED, flush=True)
    print("STOP. Runtime/C14/C2/B2-verdict/A2-verdict unchanged. Paper/OPVAL not operated.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
