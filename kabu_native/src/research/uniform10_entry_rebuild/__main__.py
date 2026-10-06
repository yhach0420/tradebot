"""Offline UNIFORM10 ENTRY rebuild. Starts only after corrected rebase PASS. No Runtime write."""
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
from research.uniform10_entry_rebuild import MAX_WORKERS, NEW_FORWARD_N, PRIMARY_TARGET, SEARCH_SPACE, UNIFORM10
from research.uniform10_entry_rebuild.analyze import (
    abc_pack,
    attach_rebuild_scores,
    ranking_block,
    robustness,
    stability,
)
from research.uniform10_entry_rebuild.features import CATALOG
from research.uniform10_entry_rebuild.model import coverage_and_corr, search_models
from research.uniform10_entry_rebuild.publish import OUT, build_markdown, write_artifacts
from research.uniform10_entry_rebuild.replay import process_day
from small_paper.v1r_native_entry_live import FEATURE_ORDER

REBASE = NATIVE / "results" / "research" / "passive_fill_corrected_rebase"
CACHE = OUT / "_work_cache"
C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache(day: str, stage: str) -> Path:
    return CACHE / f"{day}_{stage}.json"


def _load_cache(day: str, stage: str) -> dict | None:
    fp = _cache(day, stage)
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
    slim = dict(body)
    _cache(day, stage).write_text(json.dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")


def _pool(jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_day, job): job["date"] for job in jobs}
        for fut in as_completed(futs):
            day = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "date": day, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(f"done {day} stage={body.get('stage')} ok={body.get('ok')} blocker={body.get('blocker')}", flush=True)
    return out


def main() -> int:
    os.environ["PYTHONPATH"] = f"{SRC};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE.parent}"
    print("SAFETY submit/cancel/live=0/0/0 UNIFORM10 research only", flush=True)
    print("UNIFORM10_N", len(UNIFORM10), "PRIMARY_TARGET", PRIMARY_TARGET, flush=True)
    if len(UNIFORM10) != 31:
        print("REFUSE UNIFORM10_N", len(UNIFORM10), flush=True)
        return 2
    rebase = _load(REBASE / "report.json")
    if str(rebase.get("verdict") or "") != "CORRECTED_FILL_BASELINE_READY":
        print("STOP: rebase not ready", rebase.get("verdict"), flush=True)
        return 2
    c14 = _load(C14)
    if not c14.get("sha256"):
        print("STOP: C14 missing", flush=True)
        return 2

    include_0827 = str(rebase.get("20260827_CAPTURE") or "") == "INCLUDED"
    inv = build_inventory()
    elig = [
        r
        for r in inv
        if r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path")
    ]
    if not include_0827:
        elig = [r for r in elig if r["date"] != "20260827"]
    print(f"DEVELOPMENT days={len(elig)} include_0827={include_0827}", flush=True)

    # A = corrected REFERENCE trades from rebase cache
    a_trades: list[dict] = []
    rebase_cache = REBASE / "_work_cache"
    for r in elig:
        fp = rebase_cache / f"{r['date']}.json"
        if not fp.is_file():
            print("STOP missing rebase cache", r["date"], flush=True)
            return 2
        body = json.loads(fp.read_text(encoding="utf-8"))
        a_trades.extend(body.get("trades") or [])

    jobs_b = []
    b_rows = []
    for r in elig:
        cached = _load_cache(r["date"], "B_PANEL")
        if cached:
            b_rows.append(cached)
        else:
            jobs_b.append(
                {
                    "date": r["date"],
                    "capture_path": r["capture_path"],
                    "universe": r["universe_symbols"],
                    "stage": "B_PANEL",
                }
            )
    for body in _pool(jobs_b):
        b_rows.append(body)
        if body.get("ok"):
            _save_cache(str(body.get("date")), "B_PANEL", body)
    b_rows.sort(key=lambda x: str(x.get("date") or ""))
    failed_b = [r for r in b_rows if not r.get("ok")]
    if failed_b:
        print("STOP B failed", failed_b, flush=True)
        return 2
    b_trades = []
    panel = []
    for r in b_rows:
        b_trades.extend(r.get("trades") or [])
        panel.extend(r.get("panel") or [])

    # current scores on panel (6 live features) for ranking diagnostic
    # already extracted features; current_score filled if we recompute? extract_panel doesn't.
    # Ranking for current uses mid_ret_60s as weak proxy only if current_score missing.
    feat_inv = coverage_and_corr(panel)
    model_pack = search_models(panel)
    locked = model_pack.get("locked") or {}
    if not locked:
        print("STOP: no locked ENTRY architecture", flush=True)
        return 2
    panel_scored = attach_rebuild_scores(panel, locked)
    rank_c = ranking_block(panel_scored, "rebuild_score")
    rank_cur = ranking_block(panel, "current_score")

    jobs_c = []
    c_rows = []
    for r in elig:
        cached = _load_cache(r["date"], "C")
        if cached:
            c_rows.append(cached)
        else:
            jobs_c.append(
                {
                    "date": r["date"],
                    "capture_path": r["capture_path"],
                    "universe": r["universe_symbols"],
                    "stage": "C",
                    "rebuild_model": locked,
                }
            )
    for body in _pool(jobs_c):
        c_rows.append(body)
        if body.get("ok"):
            _save_cache(str(body.get("date")), "C", body)
    c_rows.sort(key=lambda x: str(x.get("date") or ""))
    failed_c = [r for r in c_rows if not r.get("ok")]
    if failed_c:
        print("STOP C failed", failed_c, flush=True)
        return 2
    c_trades = []
    for r in c_rows:
        c_trades.extend(r.get("trades") or [])

    abc = abc_pack(a_trades, b_trades, c_trades)
    stab_c = stability(c_trades)
    up = bool(rank_c.get("UP_MOVER_RANKING_SUPPORTED"))
    hist = robustness(abc=abc, ranking_ok=up, conc=stab_c.get("concentration") or {})
    cand = {
        "candidate_id": c14.get("candidate_id"),
        "candidate_sha": c14.get("sha256"),
        "runtime_code_sha": c14.get("runtime_code_sha"),
        "runtime_code_git_commit": c14.get("runtime_code_git_commit"),
        "runtime_inventory_digest": c14.get("runtime_inventory_digest"),
        "config_sha256": c14.get("config_sha256"),
        "STRATEGY_CHANGED": False,
        "CLOCK_GRID_CHANGED": False,
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "EXECUTION_SEMANTICS_CHANGED": "CORRECTED_DEFECT",
    }
    report = {
        "ANALYSIS_ID": "UNIFORM10_ENTRY_REBUILD",
        "PRIMARY_TARGET": PRIMARY_TARGET,
        "UNIFORM10_ANCHORS": [f"{h:02d}:{m:02d}" for h, m in UNIFORM10],
        "UNIFORM10_N": len(UNIFORM10),
        "CLOCK_INTERVAL_SEARCH": False,
        "RUNTIME_ACTIVATED": False,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "SAFETY": "submit/cancel/live=0/0/0",
        "NEW_RUNTIME_CANDIDATE": cand,
        "CORRECTED_BASELINE": abc.get("A"),
        "OLD_RESULTS_STATUS": rebase.get("OLD_RESULTS_STATUS"),
        "20260827_CAPTURE": rebase.get("20260827_CAPTURE"),
        "development_days": [r["date"] for r in elig],
        "SEARCH_SPACE": SEARCH_SPACE,
        "feature_inventory": feat_inv,
        "locked_entry": locked,
        "abc": abc,
        "A": abc.get("A"),
        "B": abc.get("B"),
        "C": abc.get("C"),
        "ranking_rebuilt": rank_c,
        "ranking_current_entry": rank_cur,
        "UP_MOVER_RANKING_SUPPORTED": up,
        "HISTORICAL_ROBUSTNESS": hist,
        "C_stability": stab_c,
        "verdict": (
            "UNIFORM10_ENTRY_REBUILD_COMPLETE_RUNTIME_NOT_ACTIVATED"
        ),
        "live_feature_order_unchanged": list(FEATURE_ORDER),
        "catalog_n": len(CATALOG),
        "model_search": {
            "best_lodo": model_pack.get("best_lodo"),
            "n_candidates": len(model_pack.get("candidates") or []),
        },
    }
    report["_markdown"] = build_markdown(report)
    extra = {
        "feature_inventory": feat_inv,
        "search_candidates": model_pack.get("candidates") or [],
        "locked": [locked],
        "A_trades": [
            {k: t.get(k) for k in ("date", "symbol", "session", "anchor_time", "pnl_yen_100", "exit_reason", "fill_price")}
            for t in a_trades
        ],
        "B_trades": [
            {k: t.get(k) for k in ("date", "symbol", "session", "anchor_time", "pnl_yen_100", "exit_reason", "fill_price")}
            for t in b_trades
        ],
        "C_trades": [
            {k: t.get(k) for k in ("date", "symbol", "session", "anchor_time", "pnl_yen_100", "exit_reason", "fill_price", "score", "candidate_rank")}
            for t in c_trades
        ],
        "abc": [{"leg": k, **(abc.get(k) or {})} for k in ("A", "B", "C")],
        "ranking": [
            {"score": "rebuild", **rank_c},
        ],
        "day_B": [{"date": r.get("date"), "ok": r.get("ok"), "n_trades": len(r.get("trades") or []), "n_panel": len(r.get("panel") or [])} for r in b_rows],
        "day_C": [{"date": r.get("date"), "ok": r.get("ok"), "n_trades": len(r.get("trades") or [])} for r in c_rows],
    }
    write_artifacts(report, extra_sheets=extra)
    print("OUT", OUT, flush=True)
    print("A", abc.get("A"), flush=True)
    print("B", abc.get("B"), flush=True)
    print("C", abc.get("C"), flush=True)
    print("UP_MOVER", up, "ROBUSTNESS", hist, flush=True)
    print("STOP. Runtime not activated. NEW_FORWARD_N=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
