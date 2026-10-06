"""Offline UNIFORM10 ENTRY REBUILD V2. No Runtime write. No Paper. Nested OOF then one-shot exact portfolio."""
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
from research.uniform10_entry_rebuild_v2 import (
    ANALYSIS_ID,
    B0_STATUS,
    B1_STATUS,
    C14_ID,
    C_V1_STATUS,
    C_V1_SUPERSEDED,
    MANIFEST,
    MAX_WORKERS,
    NEW_FORWARD_N,
    PER_FEATURE_THRESHOLD,
    TRUE_OOS,
    WAIT_SEC,
)
from research.uniform10_entry_rebuild_v2.analyze import b0_match, portfolio_gate, ranking_gate, verdict
from research.uniform10_entry_rebuild_v2.eligibility import rebase
from research.uniform10_entry_rebuild_v2.extract import process_day
from research.uniform10_entry_rebuild_v2.features import CATALOG
from research.uniform10_entry_rebuild_v2.oof import (
    eligible,
    fit_model,
    nested_oof,
    nested_oof_from_winners,
    robustness,
    score_rows,
    tod_split,
)
from research.uniform10_entry_rebuild_v2.portfolio import pack_trades, process_c2_day, tail_pack
from research.uniform10_entry_rebuild_v2.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from small_paper.v1r_native_entry_live import FEATURE_ORDER

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
CACHE = OUT / "_work_cache"
REBASE = NATIVE / "results" / "research" / "passive_fill_corrected_rebase"
B_PANEL = NATIVE / "results" / "research" / "uniform10_entry_rebuild" / "_work_cache"


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
    _cache_fp(day, stage).write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str), encoding="utf-8")


def _pool(fn, jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, job): job["date"] for job in jobs}
        for fut in as_completed(futs):
            day = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "date": day, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {day} stage={body.get('stage')} ok={body.get('ok')} "
                f"sec={body.get('elapsed_sec')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _abc_line(p: dict[str, Any]) -> str:
    return f"{p.get('trades')} / {p.get('PnL') if p.get('PnL') is not None else p.get('pnl')} / {p.get('PF')} / {p.get('maxDD')}"


def main() -> int:
    os.environ["PYTHONPATH"] = f"{SRC};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE.parent}"
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE UNIFORM10 ENTRY REBUILD V2", flush=True)
    print("C14/Runtime/CLOCK/ENTRY/EXIT forbidden. C V1 SUPERSEDED. No V1 weights.", flush=True)
    print("MANIFEST families", MANIFEST["families"], "max_f", MANIFEST["max_selected_features"], flush=True)

    if list(FEATURE_ORDER) != [
        "spread_bps", "imbalance", "mid_ret_60s", "mid_ret_180s", "event_rate_60s", "log_bid_qty"
    ]:
        print("STOP FEATURE_ORDER drift", flush=True)
        return 2
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        print("STOP: C14 identity mismatch", flush=True)
        return 2

    inv = build_inventory()
    elig_days = [r for r in inv if r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path")]
    days = [r["date"] for r in elig_days]
    print(f"eligible days={len(elig_days)} WAIT_SEC={WAIT_SEC} ANALYSIS_ID={ANALYSIS_ID}", flush=True)

    jobs = []
    extracted = []
    for r in elig_days:
        cached = _load_cache(r["date"], "C2_PANEL")
        if cached:
            extracted.append(cached)
            print(f"extract {r['date']} cache-hit rows={len(cached.get('rows') or [])}", flush=True)
        else:
            jobs.append({"date": r["date"], "capture_path": r["capture_path"], "universe": r["universe_symbols"]})
    for body in _pool(process_day, jobs):
        extracted.append(body)
        if body.get("ok"):
            _save_cache(str(body.get("date")), "C2_PANEL", body)
    extracted.sort(key=lambda x: str(x.get("date") or ""))
    failed = [r for r in extracted if not r.get("ok")]
    if failed:
        print("STOP extract failed", [(r.get("date"), r.get("blocker")) for r in failed], flush=True)
        return 2

    rows: list[dict] = []
    for body in extracted:
        rows.extend(body.get("rows") or [])
    print(f"audit_rows={len(rows)}", flush=True)
    reb = rebase(rows)
    print(
        f"ELIGIBLE={reb.get('PRIMARY_MODELING_ELIGIBLE')} STRUCT={reb.get('STRUCTURAL_INELIGIBLE_ROWS')} "
        f"UNEXPECTED={reb.get('UNEXPECTED_TARGET_MISSING')}",
        flush=True,
    )
    if reb.get("STOP_UNEXPECTED_MISSING"):
        gates = {
            **reb,
            "TRUE_OOS": TRUE_OOS,
            "NEW_FORWARD_N": NEW_FORWARD_N,
            "VERDICT": "C2_REBUILD_INTEGRITY_FAILED",
            "RECOMMENDED_NEXT_STEP": "STOP. Unexpected TARGET missing is material.",
        }
        report = {"ANALYSIS_ID": ANALYSIS_ID, "gates": gates, "eligibility": reb, "C_REBUILD_THIS_RUN": False}
        report["_markdown"] = build_markdown(report)
        write_artifacts(report, {"Summary": kv_rows(gates), "Eligibility": kv_rows(reb), "Safety": kv_rows({"submit": 0})})
        print("VERDICT: C2_REBUILD_INTEGRITY_FAILED", flush=True)
        return 0

    print("nested OOF start", flush=True)
    winners_fp = CACHE / "inner_winners.json"
    if winners_fp.is_file():
        winners = json.loads(winners_fp.read_text(encoding="utf-8"))
        print(f"reconstruct nested OOF from {len(winners)} inner winners (no inner re-search)", flush=True)
        nest = nested_oof_from_winners(rows, winners)
    else:
        nest = nested_oof(rows)
    new_m = nest.get("new_eval") or {}
    cur_m = nest.get("current_eval") or {}
    rank_new = nest.get("ranking_new") or {}
    rank_cur = nest.get("ranking_current") or {}
    oof_rows = nest.pop("oof_rows", None) or []
    nest.pop("fits_by_day", None)
    rob = robustness(oof_rows)
    tod_pack = tod_split(oof_rows)
    del oof_rows
    rgate = ranking_gate(new_m=new_m, cur_m=cur_m, rank=rank_new, rob=rob)
    print(
        f"OOF spearman new={new_m.get('MEAN_DAILY_SPEARMAN')} current={cur_m.get('MEAN_DAILY_SPEARMAN')} "
        f"gate={rgate.get('RANKING_GATE_PASS')}",
        flush=True,
    )

    # A / B Dual-Lane caches (not occupancy)
    a_trades: list[dict] = []
    for r in elig_days:
        body = _load(REBASE / "_work_cache" / f"{r['date']}.json")
        a_trades.extend(body.get("trades") or [])
    b_trades: list[dict] = []
    for r in elig_days:
        body = _load(B_PANEL / f"{r['date']}_B_PANEL.json")
        b_trades.extend(body.get("trades") or [])
    A = pack_trades(a_trades)
    B = pack_trades(b_trades)
    if not b0_match(B):
        print("STOP B Dual-Lane headline mismatch vs 278/441350/1.315622/-357050", B, flush=True)
        gates = {
            **reb,
            **rgate,
            "VERDICT": "C2_REBUILD_INTEGRITY_FAILED",
            "RECOMMENDED_NEXT_STEP": "STOP. B Dual-Lane cache does not match expected B0 headline. Portfolio engine mismatch.",
            "TRUE_OOS": TRUE_OOS,
            "NEW_FORWARD_N": NEW_FORWARD_N,
        }
        report = {"ANALYSIS_ID": ANALYSIS_ID, "gates": gates, "B": B, "A": A}
        report["_markdown"] = build_markdown(report)
        write_artifacts(report, {"Summary": kv_rows(gates), "Safety": kv_rows({"submit": 0})})
        return 2

    c2_trades: list[dict] = []
    c2_pack: dict = {}
    c2_tail: dict = {}
    pgate: dict = {}
    portfolio_ran = False
    locked = nest.get("locked_spec") or {}
    if rgate.get("RANKING_GATE_PASS"):
        print("ranking gate PASS - building locked-spec OOF lookup then one-shot Dual Lane C2", flush=True)
        lookup: dict[str, float] = {}
        day_set = sorted({str(r.get("date")) for r in rows})
        for hold in day_set:
            train = eligible([r for r in rows if str(r.get("date")) != hold])
            test = [r for r in rows if str(r.get("date")) == hold]
            fit = fit_model(train, locked)
            scored = score_rows(test, fit)
            for rec in scored:
                s = rec.get("c2_score")
                if s is None:
                    continue
                lookup[f"{rec.get('date')}|{rec.get('anchor')}|{rec.get('symbol')}"] = float(s)
        jobs_c = []
        got = []
        for r in elig_days:
            jobs_c.append(
                {
                    "date": r["date"],
                    "capture_path": r["capture_path"],
                    "universe": r["universe_symbols"],
                    "score_lookup": lookup,
                }
            )
        for body in _pool(process_c2_day, jobs_c):
            got.append(body)
            if body.get("ok"):
                slim = {"ok": True, "date": body.get("date"), "stage": "C2_EXACT", "trades": body.get("trades"), "elapsed_sec": body.get("elapsed_sec")}
                _save_cache(str(body.get("date")), "C2_EXACT", slim)
        got.sort(key=lambda x: str(x.get("date") or ""))
        bad = [x for x in got if not x.get("ok")]
        if bad:
            print("STOP C2 exact failed", [(x.get("date"), x.get("blocker")) for x in bad], flush=True)
            portfolio_ran = False
        else:
            for x in got:
                c2_trades.extend(x.get("trades") or [])
            c2_pack = pack_trades(c2_trades)
            c2_tail = tail_pack(c2_trades)
            pgate = portfolio_gate(c2=c2_pack, tail=c2_tail)
            portfolio_ran = True
    else:
        print("ranking gate FAIL - skip exact portfolio (PnL not inspected for selection)", flush=True)

    vname, nxt = verdict(rebase_stop=False, ranking=rgate, portfolio_ran=portfolio_ran and bool(c2_pack), port=pgate)
    feat_inv = []
    for name, family, formula, lookback, avail in CATALOG:
        hit = next((x for x in (nest.get("feat_inventory_all_train_diagnostic") or []) if x.get("feature") == name), {})
        feat_inv.append(
            {
                "name": name,
                "family": family,
                "formula": formula,
                "lookback": lookback,
                "source": "Capture board t<=t0",
                "causal_availability": avail,
                "missing_rate": hit.get("missing_rate"),
                "t0_availability": avail,
                "coverage": hit.get("coverage"),
            }
        )

    gates = {
        "TARGET_V4_ROWS": reb.get("TARGET_V4_ROWS"),
        "STRUCTURAL_INELIGIBLE_ROWS": reb.get("STRUCTURAL_INELIGIBLE_ROWS"),
        "UNEXPECTED_TARGET_MISSING": reb.get("UNEXPECTED_TARGET_MISSING"),
        "PRIMARY_MODELING_ELIGIBLE": reb.get("PRIMARY_MODELING_ELIGIBLE"),
        "OOF_MEAN_DAILY_SPEARMAN": new_m.get("MEAN_DAILY_SPEARMAN"),
        "OOF_MEDIAN_DAILY_SPEARMAN": new_m.get("MEDIAN_DAILY_SPEARMAN"),
        "OOF_POSITIVE_DAY_COUNT": new_m.get("positive_day_count"),
        "CURRENT_SCORE_OOF_SPEARMAN": cur_m.get("MEAN_DAILY_SPEARMAN"),
        "NEW_SCORE_OOF_SPEARMAN": new_m.get("MEAN_DAILY_SPEARMAN"),
        "ALL_TARGET": rank_new.get("ALL_TARGET"),
        "TOP10_TARGET": rank_new.get("TOP10_TARGET"),
        "TOP5_TARGET": rank_new.get("TOP5_TARGET"),
        "TOP3_TARGET": rank_new.get("TOP3_TARGET"),
        "TOP1_TARGET": rank_new.get("TOP1_TARGET"),
        "SELECTED_FEATURES": locked.get("features"),
        "SELECTED_MODEL": locked.get("family"),
        "C2_EXACT": _abc_line(c2_pack) if c2_pack else None,
        "C2_POSITIVE_DAY_RATE": c2_pack.get("positive_day_rate") if c2_pack else None,
        "C2_MEDIAN_DAILY_PNL": c2_pack.get("median_daily_pnl") if c2_pack else None,
        "C2_PNL_EX_TOP3_TRADES": (c2_tail.get("ex_top3_trades") or {}).get("pnl") if c2_tail else None,
        "C2_PNL_EX_TOP3_DAYS": (c2_tail.get("ex_top3_days") or {}).get("pnl") if c2_tail else None,
        "C2_PNL_EX_TOP_SYMBOL": (c2_tail.get("ex_top_symbol") or {}).get("pnl") if c2_tail else None,
        "A": _abc_line(A),
        "B": _abc_line(B),
        "C2": _abc_line(c2_pack) if c2_pack else None,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": vname,
        "RECOMMENDED_NEXT_STEP": nxt,
        "RANKING_GATE_PASS": rgate.get("RANKING_GATE_PASS"),
        "PORTFOLIO_GATE_PASS": pgate.get("PORTFOLIO_GATE_PASS") if pgate else False,
        "CLASSIFICATION": "HISTORICAL_DEVELOPMENT_CANDIDATE_ONLY",
        "C_V1_STATUS": C_V1_STATUS,
        "C_V1_SUPERSEDED": C_V1_SUPERSEDED,
    }

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "C14_ID": C14_ID,
        "C14_SHA": c14.get("sha256"),
        "WAIT_SEC": WAIT_SEC,
        "fill_price": "limit_price",
        "FILL_SOT": "is_executable_continuous_board",
        "development_days": days,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "HISTORICAL_DEVELOPMENT_ONLY": True,
        "MANIFEST": MANIFEST,
        "C_V1": {"status": C_V1_STATUS, "SUPERSEDED": C_V1_SUPERSEDED, "weights_inherited": False},
        "eligibility": reb,
        "feature_inventory": feat_inv,
        "oof_folds": nest.get("folds"),
        "locked_spec": {k: v for k, v in locked.items() if k not in {"inner_leaderboard"}},
        "oof_new": {k: v for k, v in new_m.items() if k != "days"},
        "oof_current": {k: v for k, v in cur_m.items() if k != "days"},
        "oof_days": new_m.get("days"),
        "ranking_new": rank_new,
        "ranking_current": rank_cur,
        "tod": tod_pack,
        "robustness": rob,
        "ranking_gate": rgate,
        "A": A,
        "B": B,
        "C2": c2_pack or None,
        "C2_tail": c2_tail or None,
        "portfolio_gate": pgate or None,
        "portfolio_ran": portfolio_ran,
        "gates": gates,
        "B_STATUS": {"B0": B0_STATUS, "B1": B1_STATUS, "per_feature_threshold": PER_FEATURE_THRESHOLD},
        "C_REBUILD_THIS_RUN": False,
        "RUNTIME_IMPLEMENTED": False,
        "SAFETY": "submit/cancel/live=0/0/0",
        "selector_used_pnl": False,
        "score_admission_searched": False,
        "topk_architecture_searched": False,
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "Summary": kv_rows({k: gates.get(k) for k in (
            "TARGET_V4_ROWS", "STRUCTURAL_INELIGIBLE_ROWS", "UNEXPECTED_TARGET_MISSING",
            "OOF_MEAN_DAILY_SPEARMAN", "OOF_MEDIAN_DAILY_SPEARMAN", "OOF_POSITIVE_DAY_COUNT",
            "CURRENT_SCORE_OOF_SPEARMAN", "NEW_SCORE_OOF_SPEARMAN",
            "ALL_TARGET", "TOP10_TARGET", "TOP5_TARGET", "TOP3_TARGET", "TOP1_TARGET",
            "SELECTED_FEATURES", "SELECTED_MODEL",
            "C2_EXACT", "C2_POSITIVE_DAY_RATE", "C2_MEDIAN_DAILY_PNL",
            "C2_PNL_EX_TOP3_TRADES", "C2_PNL_EX_TOP3_DAYS", "C2_PNL_EX_TOP_SYMBOL",
            "A", "B", "C2", "TRUE_OOS", "NEW_FORWARD_N", "VERDICT", "RECOMMENDED_NEXT_STEP",
        )}),
        "Manifest": kv_rows(MANIFEST),
        "Eligibility": kv_rows(reb),
        "Feature_Inventory": feat_inv,
        "OOF_Folds": nest.get("folds") or [{"empty": True}],
        "OOF_Ranking": [rank_new, {"side": "current", **rank_cur}],
        "OOF_Days": new_m.get("days") or [{"empty": True}],
        "TOD": tod_pack,
        "Robustness": kv_rows(rob),
        "Comparison_ABC": [ {"variant": "A", **A}, {"variant": "B", **B}, {"variant": "C2", **(c2_pack or {"empty": True})} ],
        "C2_Portfolio": kv_rows(c2_pack) if c2_pack else [{"skipped": True}],
        "C2_Tail": [v for v in (c2_tail or {}).values() if isinstance(v, dict)] or [{"skipped": True}],
        "Decision": kv_rows(gates),
        "Safety": kv_rows(
            {
                "OFFLINE_RESEARCH_ONLY": True,
                "C14_changed": False,
                "Runtime_changed": False,
                "CLOCK_changed": False,
                "ENTRY_changed": False,
                "EXIT_changed": False,
                "Execution_freshness_changed": False,
                "Paper_started": False,
                "OPVAL_started": False,
                "submit": 0,
                "cancel": 0,
                "live": 0,
                "C_V1_SUPERSEDED": True,
                "new_runtime_candidate": False,
            }
        ),
    }
    write_artifacts(report, sheets)
    print("OUT", OUT, flush=True)
    for k in (
        "TARGET_V4_ROWS", "STRUCTURAL_INELIGIBLE_ROWS", "UNEXPECTED_TARGET_MISSING",
        "OOF_MEAN_DAILY_SPEARMAN", "OOF_MEDIAN_DAILY_SPEARMAN", "OOF_POSITIVE_DAY_COUNT",
        "CURRENT_SCORE_OOF_SPEARMAN", "NEW_SCORE_OOF_SPEARMAN",
        "ALL_TARGET", "TOP10_TARGET", "TOP5_TARGET", "TOP3_TARGET", "TOP1_TARGET",
        "SELECTED_FEATURES", "SELECTED_MODEL",
        "C2_EXACT", "VERDICT",
    ):
        print(f"{k}: {gates.get(k)}", flush=True)
    print("TRUE_OOS: false", flush=True)
    print("NEW_FORWARD_N: 0", flush=True)
    print("STOP. C V2 research only. Runtime not changed. No new candidate activated.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
