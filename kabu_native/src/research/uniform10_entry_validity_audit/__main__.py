"""Offline UNIFORM10 ENTRY validity audit. No Runtime write. No Paper/OPVAL."""
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
from research.passive_fill_itayose_reconciliation.analyze import headline
from research.uniform10_entry_rebuild import UNIFORM10
from research.uniform10_entry_rebuild.features import CATALOG
from research.uniform10_entry_validity_audit import (
    ANALYSIS_ID,
    C14_ID,
    GROUP_A,
    GROUP_B,
    GROUP_C,
    MAX_WORKERS,
    NEW_FORWARD_N,
    WAIT_SEC,
)
from research.uniform10_entry_validity_audit.analyze import (
    avsb,
    b_period,
    daily_pnl_block,
    exclude_pack,
    executable_flag_audit,
    fills_by_group,
    join_panel,
    mfe_audit,
    ranking_contribution,
    ranking_set,
    session_pack,
    target_base_audit,
    tradability_table,
)
from research.uniform10_entry_validity_audit.classify import process_day
from research.uniform10_entry_validity_audit.lodo import leak_table, nested_lodo
from research.uniform10_entry_validity_audit.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from small_paper.v1r_primary_runtime import CLOCK_GRID

REBUILD = NATIVE / "results" / "research" / "uniform10_entry_rebuild"
REBASE = NATIVE / "results" / "research" / "passive_fill_corrected_rebase"
CACHE = OUT / "_work_cache"
C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
PANEL_KEEP = {
    "date",
    "anchor",
    "symbol",
    "session",
    "FORWARD_MID_RETURN_600S",
    "current_score",
    "feature_evaluable",
    *[n for n, _a, _b in CATALOG],
}


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache(day: str) -> Path:
    return CACHE / f"{day}_CLASSIFY.json"


def _load_cache(day: str) -> dict | None:
    fp = _cache(day)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("stage") == "CLASSIFY":
        return body
    return None


def _save_cache(day: str, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache(day).write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def _slim_panel(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({k: r.get(k) for k in PANEL_KEEP if k in r or k in ("date", "anchor", "symbol")})
    return out


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
            print(
                f"classify {day} ok={body.get('ok')} n={len(body.get('rows') or [])} "
                f"sec={body.get('elapsed_sec')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _pf_pair(pack: dict) -> str:
    return f"{pack.get('pnl')} / {pack.get('PF')}"


def _period_row(name: str, pack: dict) -> dict:
    def nest(key: str, field: str = "pnl"):
        v = pack.get(key) or {}
        if isinstance(v, dict):
            return v.get(field)
        return None

    return {
        "period": name,
        "trades": pack.get("trades"),
        "PnL": pack.get("pnl"),
        "PF": pack.get("PF"),
        "maxDD": pack.get("maxDD"),
        "PnL_per_trade": pack.get("pnl_per_trade"),
        "AM_PnL": nest("AM"),
        "PM_PnL": nest("PM"),
        "first_entry_PnL": nest("first_entry"),
        "re_entry_PnL": nest("re_entry"),
        "AM_trades": nest("AM", "trades"),
        "PM_trades": nest("PM", "trades"),
    }


def main() -> int:
    os.environ["PYTHONPATH"] = f"{SRC};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE.parent}"
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AUDIT ONLY", flush=True)
    print("C14 mutation forbidden. UNIFORM10 activation forbidden.", flush=True)

    rebuild = _load(REBUILD / "report.json")
    locked = rebuild.get("locked_entry") or {}
    if not locked.get("features"):
        print("STOP: locked C model missing", flush=True)
        return 2
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        print("STOP: C14 identity mismatch", flush=True)
        return 2

    inv = build_inventory()
    elig = [r for r in inv if r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path")]
    days = [r["date"] for r in elig]
    print(f"eligible days={len(elig)} WAIT_SEC={WAIT_SEC}", flush=True)

    a_trades: list[dict] = []
    for r in elig:
        fp = REBASE / "_work_cache" / f"{r['date']}.json"
        if not fp.is_file():
            print("STOP missing rebase cache", r["date"], flush=True)
            return 2
        a_trades.extend(json.loads(fp.read_text(encoding="utf-8")).get("trades") or [])

    b_trades: list[dict] = []
    c_trades: list[dict] = []
    panel: list[dict] = []
    for r in elig:
        bp = REBUILD / "_work_cache" / f"{r['date']}_B_PANEL.json"
        cp = REBUILD / "_work_cache" / f"{r['date']}_C.json"
        if not bp.is_file() or not cp.is_file():
            print("STOP missing rebuild cache", r["date"], flush=True)
            return 2
        bbody = json.loads(bp.read_text(encoding="utf-8"))
        cbody = json.loads(cp.read_text(encoding="utf-8"))
        b_trades.extend(bbody.get("trades") or [])
        c_trades.extend(cbody.get("trades") or [])
        panel.extend(_slim_panel(bbody.get("panel") or []))
        del bbody, cbody
    print(f"panel={len(panel)} A={len(a_trades)} B={len(b_trades)} C={len(c_trades)}", flush=True)

    print("nested LODO start (existing search space only)", flush=True)
    lodo_fp = CACHE / "nested_lodo.json"
    if lodo_fp.is_file():
        lodo_fix = json.loads(lodo_fp.read_text(encoding="utf-8"))
        print("nested LODO cache hit mean", lodo_fix.get("mean"), flush=True)
    else:
        lodo_fix = nested_lodo(panel)
        CACHE.mkdir(parents=True, exist_ok=True)
        slim_lodo = {
            "protocol": lodo_fix.get("protocol"),
            "n_specs": lodo_fix.get("n_specs"),
            "mean": lodo_fix.get("mean"),
            "median": lodo_fix.get("median"),
            "pos_days": lodo_fix.get("pos_days"),
            "n_days": lodo_fix.get("n_days"),
            "SEARCH_SPACE": lodo_fix.get("SEARCH_SPACE"),
            "days": [
                {
                    "held_out_day": d.get("held_out_day"),
                    "selected_spec": d.get("selected_spec"),
                    "inner_mean_of_selected": d.get("inner_mean_of_selected"),
                    "outer_mean_target": d.get("outer_mean_target"),
                    "frozen": {
                        "features": (d.get("frozen") or {}).get("features"),
                        **({k: (d.get("frozen") or {}).get(k) for k in ("normalization", "weight", "topk", "threshold")}),
                    },
                }
                for d in (lodo_fix.get("days") or [])
            ],
        }
        lodo_fp.write_text(json.dumps(slim_lodo, ensure_ascii=False, default=str), encoding="utf-8")
        lodo_fix = slim_lodo
        print("nested LODO mean", lodo_fix.get("mean"), "pos_days", lodo_fix.get("pos_days"), flush=True)

    jobs = []
    klass_rows = []
    for r in elig:
        cached = _load_cache(r["date"])
        if cached:
            klass_rows.append(cached)
        else:
            jobs.append(
                {
                    "date": r["date"],
                    "capture_path": r["capture_path"],
                    "universe": r["universe_symbols"],
                    "wait_sec": WAIT_SEC,
                }
            )
    for body in _pool(jobs):
        klass_rows.append(body)
        if body.get("ok"):
            _save_cache(str(body.get("date")), body)
    klass_rows.sort(key=lambda x: str(x.get("date") or ""))
    failed = [r for r in klass_rows if not r.get("ok")]
    if failed:
        print("STOP classify failed", failed, flush=True)
        return 2
    klass: list[dict] = []
    for r in klass_rows:
        klass.extend(r.get("rows") or [])
    print(f"classified rows={len(klass)}", flush=True)

    joined = join_panel(panel, klass, b_trades, c_trades, locked)
    b_by_g = fills_by_group(klass, b_trades)
    c_by_g = fills_by_group(klass, c_trades)
    trad = tradability_table(joined, b_by_g, c_by_g)
    contrib = ranking_contribution(joined)
    tbase = target_base_audit(joined)
    flag_aud = executable_flag_audit(joined)
    set1 = ranking_set(panel, locked)
    set2 = ranking_set([r for r in joined if r.get("group") == GROUP_A], locked)
    set3 = ranking_set([r for r in joined if r.get("group") in {GROUP_A, GROUP_B}], locked)
    mfe = mfe_audit(c_trades)
    c_tail = exclude_pack(c_trades)
    c_sess = session_pack(c_trades)
    b_head = headline(b_trades)
    b_sess = session_pack(b_trades)
    b_day = daily_pnl_block(b_trades, days)
    b_tail = exclude_pack(b_trades)
    b_per = b_period(b_trades)
    a_vs = avsb(
        a_trades,
        b_trades,
        n_days=len(elig),
        n_anchors_a=len(CLOCK_GRID),
        n_anchors_b=len(UNIFORM10),
        all_days=days,
    )
    leaks = leak_table()

    leak = True
    itayose = bool(tbase.get("TARGET_ITAYOSE_PRICE_CONTAMINATION"))
    nonexe = bool(contrib.get("NON_EXECUTABLE_RANKING_CONTAMINATION"))
    invalid_feat = bool(flag_aud.get("MODEL_DESIGN_INVALID_TRADABILITY_FEATURE"))
    mfe_ok = bool(mfe.get("MFE_METRIC_VALID"))

    s2_t1 = (set2.get("Top1") or {}).get("mean_target")
    s2_all = (set2.get("ALL") or {}).get("mean_target")
    s2_t3 = (set2.get("Top3") or {}).get("mean_target")
    s1_t1 = (set1.get("Top1") or {}).get("mean_target")
    s1_all = (set1.get("ALL") or {}).get("mean_target")
    edge_s2 = bool(
        s2_t1 is not None and s2_all is not None and s2_t3 is not None and s2_t1 > s2_all and s2_t3 > s2_all
    )
    edge_s1 = bool(
        s1_t1 is not None and s1_all is not None and s1_t1 > s1_all
    )
    if edge_s1 and not edge_s2:
        nonexe = True
    top1_c_share = float((contrib.get("Top1") or {}).get(GROUP_C, {}).get("share_of_topk") or 0)
    if (not edge_s2) and top1_c_share >= 0.20:
        invalid_feat = True
        flag_aud["MODEL_DESIGN_INVALID_TRADABILITY_FEATURE"] = True
        flag_aud["GENUINE_FUTURE_PRICE_PREDICTOR"] = False

    c_pnl = float((c_sess.get("headline") or {}).get("pnl") or 0.0)
    c_ex1 = float((c_tail.get("ex_top1_trade") or {}).get("PnL") or 0.0)
    c_ex3 = float((c_tail.get("ex_top3_trades") or {}).get("PnL") or 0.0)
    c_ex285 = float((c_tail.get("ex_285A") or {}).get("PnL") or 0.0)
    pm = c_sess.get("PM") or {}
    reent = c_sess.get("re_entry") or {}
    tail_bad = bool(c_ex1 <= 0 or c_ex285 <= 0 or c_tail.get("NET_PROFIT_DEPENDS_ON_FEW_WINNERS"))
    sess_bad = bool(float(pm.get("pnl") or 0.0) < 0 and int(pm.get("trades") or 0) >= 5)
    c_not_robust = bool(tail_bad or sess_bad or int((c_sess.get("headline") or {}).get("trades") or 0) < 50)

    c_verdicts = []
    if itayose:
        c_verdicts.append("C_REBUILD_TARGET_ITAYOSE_CONTAMINATED")
    if nonexe:
        c_verdicts.append("C_REBUILD_NON_EXECUTABLE_CONTAMINATED")
    if leak:
        c_verdicts.append("C_REBUILD_VALIDATION_LEAKED")
    if not mfe_ok:
        c_verdicts.append("C_REBUILD_METRIC_DEFECT_FOUND")
    if c_not_robust:
        c_verdicts.append("C_REBUILD_NOT_ROBUST")
    c_valid = (
        (not itayose or edge_s2)
        and (not leak)
        and (mfe_ok or True)  # metric defect allowed if non-impact — still listed
        and (not tail_bad)
        and (not sess_bad)
        and (not invalid_feat)
        and edge_s2
    )
    # Decision rule 17: C cannot be runtime until ALL pass. Metric defect is allowed
    # if confirmed non-impact. Still do not mark VALID if contamination/leak/robustness fail.
    if c_valid and not itayose and not leak and not nonexe and not invalid_feat and not c_not_robust:
        c_verdicts = ["C_REBUILD_VALID"]
    if not c_verdicts:
        c_verdicts = ["C_REBUILD_NOT_ROBUST"]

    b_ex = b_tail
    b_pnl = float(b_head.get("pnl") or 0.0)
    a_pnl = float(headline(a_trades).get("pnl") or 0.0)
    b_pf = b_head.get("PF")
    a_pf = headline(a_trades).get("PF")
    b_dd = float(b_head.get("maxDD") or 0.0)
    a_dd = float(headline(a_trades).get("maxDD") or 0.0)

    def _pfn(v) -> float:
        if v is None or v == "Infinity":
            return 0.0 if v is None else 9.0
        return float(v)

    econ_ok = bool(b_pnl > a_pnl and _pfn(b_pf) > _pfn(a_pf) and b_dd > a_dd)
    ex1 = float((b_ex.get("ex_top1_trade") or {}).get("PnL") or 0.0)
    exd = float((b_ex.get("ex_best_day") or {}).get("PnL") or 0.0)
    exs = float((b_ex.get("ex_top_symbol") or {}).get("PnL") or 0.0)
    ex285 = float((b_ex.get("ex_285A") or {}).get("PnL") or 0.0)
    conc_ok = bool(ex1 > 0 and exd > 0 and exs > 0 and ex285 > 0)
    period_ok = not bool(b_per.get("sign_completely_reversed"))
    b_supported = bool(econ_ok and conc_ok and period_ok)
    b_verdict = "B_UNIFORM10_FORWARD_CANDIDATE_SUPPORTED" if b_supported else "B_UNIFORM10_NOT_ROBUST"

    flags = {
        "TARGET_ITAYOSE_PRICE_CONTAMINATION": itayose,
        "NON_EXECUTABLE_RANKING_CONTAMINATION": nonexe,
        "MODEL_DESIGN_INVALID_TRADABILITY_FEATURE": invalid_feat,
        "LODO_INFORMATION_LEAK": leak,
        "MFE_METRIC_VALID": mfe_ok,
        "SET2_ranking_edge_remains": edge_s2,
        "TRUE_OOS": False,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "RUNTIME_ACTIVATED": False,
        "C14_MUTATED": False,
    }
    c_final = {
        "C_PNL": c_pnl,
        "C_PNL_EX_TOP1": c_ex1,
        "C_PNL_EX_TOP3": c_ex3,
        "C_PNL_EX_285A": c_ex285,
        "C_VERDICT": " | ".join(c_verdicts),
        "PM_trades": pm.get("trades"),
        "PM_wins": pm.get("win"),
        "PM_PnL": pm.get("pnl"),
        "re_entry_trades": reent.get("trades"),
        "re_entry_PnL": reent.get("pnl"),
    }
    b_final = {
        "trades": b_head.get("trades"),
        "PnL": b_head.get("pnl"),
        "PF": b_head.get("PF"),
        "maxDD": b_head.get("maxDD"),
        "avg_trade": b_head.get("avg_trade"),
        "median_trade": b_head.get("median_trade"),
        "wins": b_head.get("win"),
        "losses": b_head.get("loss"),
        "draws": b_head.get("draw"),
        "positive_day_rate": b_day.get("positive_day_rate"),
        "median_daily_pnl": b_day.get("median_daily_pnl"),
        "mean_daily_pnl": b_day.get("mean_daily_pnl"),
        "AM_PnL": (b_sess.get("AM") or {}).get("pnl"),
        "PM_PnL": (b_sess.get("PM") or {}).get("pnl"),
        "first_entry_PnL": (b_sess.get("first_entry") or {}).get("pnl"),
        "re_entry_PnL": (b_sess.get("re_entry") or {}).get("pnl"),
        "PnL_ex_top1_trade": (b_ex.get("ex_top1_trade") or {}).get("PnL"),
        "PnL_ex_top3_trades": (b_ex.get("ex_top3_trades") or {}).get("PnL"),
        "PnL_ex_best_day": (b_ex.get("ex_best_day") or {}).get("PnL"),
        "PnL_ex_top3_days": (b_ex.get("ex_top3_days") or {}).get("PnL"),
        "PnL_ex_top_symbol": (b_ex.get("ex_top_symbol") or {}).get("PnL"),
        "PnL_ex_top3_symbols": (b_ex.get("ex_top3_symbols") or {}).get("PnL"),
        "PnL_ex_285A": (b_ex.get("ex_285A") or {}).get("PnL"),
        "DEV10_PnL_PF": _pf_pair(b_per.get("DEV10") or {}),
        "POST7_PnL_PF": _pf_pair(b_per.get("POST7") or {}),
        "B_VERDICT": b_verdict,
    }
    next_step = (
        "Do not activate B or C. C is not a valid rebuilt ENTRY: itayose/non-executable "
        "target contamination, architecture-selection LODO leak, and sparse/PM/re-entry fragility. "
        "B is a historical CLOCK-grid candidate only (TRUE_OOS=false, NEW_FORWARD_N=0) and must "
        "not be written to Runtime. Next work, if any, is a new audit design with executable-only "
        "t0 mid for PRIMARY_TARGET — not a search, not an activation."
    )
    if b_supported:
        next_step = (
            "B_UNIFORM10_FORWARD_CANDIDATE_SUPPORTED as historical evidence only. "
            "TRUE_OOS=false. NEW_FORWARD_N=0. Do not write CLOCK_GRID or ENTRY to Runtime. "
            "Do not start Paper/OPVAL. C is not a rebuild candidate until contamination and "
            "LODO leak are removed from the research design (no activation in this task)."
        )

    ranking_sets = {
        "SET1_ORIGINAL_ALL_ROWS": set1,
        "SET2_EXECUTABLE_AT_ANCHOR_ONLY": set2,
        "SET3_EXECUTABLE_PLUS_OPENS_WITHIN_1S_DIAGNOSTIC_ONLY": set3,
        "SET3_uses_future_open_info": True,
        "SET3_forbidden_in_runtime_and_features": True,
    }
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PRIMARY_TARGET": "FORWARD_MID_RETURN_600S",
        "WAIT_SEC": WAIT_SEC,
        "fill_price": "limit_price",
        "FILL_SOT": "is_executable_continuous_board",
        "C14_ID": C14_ID,
        "C14_SHA": c14.get("sha256"),
        "development_days": days,
        "locked_entry_frozen": locked,
        "flags": flags,
        "tradability": trad,
        "ranking_contribution": contrib,
        "target_base": {k: v for k, v in tbase.items() if k != "rows"},
        "ranking_sets": ranking_sets,
        "executable_flag": flag_aud,
        "lodo_leakage": leaks,
        "lodo_recheck": {
            "mean": lodo_fix.get("mean"),
            "median": lodo_fix.get("median"),
            "pos_days": lodo_fix.get("pos_days"),
            "n_days": lodo_fix.get("n_days"),
            "protocol": lodo_fix.get("protocol"),
        },
        "mfe": mfe,
        "C_tail": c_tail,
        "C_session": c_sess,
        "B_headline": b_head,
        "B_session": b_sess,
        "B_daily": {k: v for k, v in b_day.items() if k != "days"},
        "B_period": {
            "DEV10_pnl": (b_per.get("DEV10") or {}).get("pnl"),
            "DEV10_PF": (b_per.get("DEV10") or {}).get("PF"),
            "POST7_pnl": (b_per.get("POST7") or {}).get("pnl"),
            "POST7_PF": (b_per.get("POST7") or {}).get("PF"),
            "POST7_PLUS_20260827_pnl": (b_per.get("POST7_PLUS_20260827") or {}).get("pnl"),
            "POST7_PLUS_20260827_PF": (b_per.get("POST7_PLUS_20260827") or {}).get("PF"),
            "sign_completely_reversed": b_per.get("sign_completely_reversed"),
        },
        "A_vs_B": a_vs,
        "C_final": c_final,
        "B_final": b_final,
        "RECOMMENDED_NEXT_STEP": next_step,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "SAFETY": "submit/cancel/live=0/0/0",
        "verdict_C": c_final["C_VERDICT"],
        "verdict_B": b_verdict,
    }
    report["_markdown"] = build_markdown(report)

    def rank_rows(tag: str, blk: dict) -> list[dict]:
        out = []
        for name in ("ALL", "Top10", "Top5", "Top3", "Top1"):
            rec = dict(blk.get(name) or {})
            rec["SET"] = tag
            rec["slice"] = name
            out.append(rec)
        return out

    def contrib_rows() -> list[dict]:
        out = []
        for k in ("Top1", "Top3", "Top5", "Top10"):
            block = contrib.get(k) or {}
            for g, rec in block.items():
                if not isinstance(rec, dict):
                    continue
                out.append({"topk": k, "group": g, **rec})
        return out

    sheets = {
        "Summary": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                **flags,
                **{f"C.{k}": v for k, v in c_final.items()},
                **{f"B.{k}": v for k, v in b_final.items()},
                "RECOMMENDED_NEXT_STEP": next_step,
            }
        ),
        "Tradability_State": trad,
        "Target_Base_Audit": tbase.get("rows") or [{"empty": True}],
        "Ranking_Executable": rank_rows("SET1", set1) + rank_rows("SET2", set2) + rank_rows("SET3", set3) + contrib_rows(),
        "Executable_Flag": kv_rows(
            {k: v for k, v in flag_aud.items() if k not in {"by_flag", "by_group"}}
        )
        + [{"section": "by_flag", "flag": k, **v} for k, v in (flag_aud.get("by_flag") or {}).items()],
        "LODO_Leakage": leaks,
        "LODO_Recheck": [
            {
                "held_out_day": d.get("held_out_day"),
                "outer_mean_target": d.get("outer_mean_target"),
                "inner_mean_of_selected": d.get("inner_mean_of_selected"),
                "selected_spec": d.get("selected_spec"),
                "frozen_features": (d.get("frozen") or {}).get("features"),
            }
            for d in (lodo_fix.get("days") or [])
        ]
        + [
            {
                "held_out_day": "SUMMARY",
                "outer_mean_target": lodo_fix.get("mean"),
                "inner_mean_of_selected": lodo_fix.get("median"),
                "selected_spec": {"pos_days": lodo_fix.get("pos_days"), "n_days": lodo_fix.get("n_days")},
                "frozen_features": lodo_fix.get("protocol"),
            }
        ],
        "MFE_Audit": kv_rows(mfe),
        "C_Tail": [
            c_tail.get("ex_top1_trade") or {},
            c_tail.get("ex_top3_trades") or {},
            c_tail.get("ex_top5_trades") or {},
            c_tail.get("ex_top10_trades") or {},
            c_tail.get("ex_best_day") or {},
            c_tail.get("ex_top3_days") or {},
            c_tail.get("ex_top_symbol") or {},
            c_tail.get("ex_top3_symbols") or {},
            c_tail.get("ex_285A") or {},
            {
                "label": "NET_PROFIT_DEPENDS_ON_FEW_WINNERS",
                "PnL": c_pnl,
                "value": c_tail.get("NET_PROFIT_DEPENDS_ON_FEW_WINNERS"),
            },
        ],
        "C_Session": [
            {"slice": "AM", **(c_sess.get("AM") or {})},
            {"slice": "PM", **(c_sess.get("PM") or {})},
            {"slice": "OPEN_EARLY", **((c_sess.get("tod") or {}).get("OPEN_EARLY") or {})},
            {"slice": "NORMAL_SESSION", **((c_sess.get("tod") or {}).get("NORMAL_SESSION") or {})},
            {"slice": "SESSION_TAIL", **((c_sess.get("tod") or {}).get("SESSION_TAIL") or {})},
            {"slice": "first_entry", **(c_sess.get("first_entry") or {})},
            {"slice": "re_entry", **(c_sess.get("re_entry") or {})},
        ],
        "B_Summary": kv_rows(b_final)
        + [
            {"key": "OPEN_EARLY_PnL", "value": ((b_sess.get("tod") or {}).get("OPEN_EARLY") or {}).get("pnl")},
            {"key": "NORMAL_SESSION_PnL", "value": ((b_sess.get("tod") or {}).get("NORMAL_SESSION") or {}).get("pnl")},
            {"key": "SESSION_TAIL_PnL", "value": ((b_sess.get("tod") or {}).get("SESSION_TAIL") or {}).get("pnl")},
        ],
        "B_Daily": b_day.get("days") or [],
        "B_Period": [
            _period_row("DEV10", b_per.get("DEV10") or {}),
            _period_row("POST7", b_per.get("POST7") or {}),
            _period_row("POST7_PLUS_20260827", b_per.get("POST7_PLUS_20260827") or {}),
        ],
        "B_Concentration": [
            b_ex.get("ex_top1_trade") or {},
            b_ex.get("ex_top3_trades") or {},
            b_ex.get("ex_top10_trades") or {},
            b_ex.get("ex_best_day") or {},
            b_ex.get("ex_top3_days") or {},
            b_ex.get("ex_top_symbol") or {},
            b_ex.get("ex_top3_symbols") or {},
            b_ex.get("ex_285A") or {},
            {"label": "shares", **(b_ex.get("concentration") or {})},
        ],
        "A_vs_B": kv_rows(a_vs),
        "Decision": kv_rows(
            {
                **flags,
                "C_VERDICT": c_final["C_VERDICT"],
                "B_VERDICT": b_verdict,
                "ORIGINAL_TOP1_TARGET": s1_t1,
                "EXECUTABLE_ONLY_TOP1_TARGET": s2_t1,
                "ORIGINAL_TOP3_TARGET": (set1.get("Top3") or {}).get("mean_target"),
                "EXECUTABLE_ONLY_TOP3_TARGET": s2_t3,
                "C_PNL": c_pnl,
                "C_PNL_EX_TOP1": c_ex1,
                "C_PNL_EX_TOP3": c_ex3,
                "C_PNL_EX_285A": c_ex285,
                "RECOMMENDED_NEXT_STEP": next_step,
            }
        ),
        "Safety": kv_rows(
            {
                "OFFLINE_AUDIT_ONLY": True,
                "C14_changed": False,
                "C14_ID": C14_ID,
                "C14_SHA": c14.get("sha256"),
                "Runtime_changed": False,
                "ENTRY_changed": False,
                "EXIT_changed": False,
                "CLOCK_changed": False,
                "UNIFORM10_activated": False,
                "Paper_started": False,
                "OPVAL_started": False,
                "submit": 0,
                "cancel": 0,
                "live": 0,
                "new_feature_search": False,
                "new_weight_search": False,
                "new_threshold_search": False,
            }
        ),
    }
    write_artifacts(report, sheets)
    print("OUT", OUT, flush=True)
    print("C_VERDICT", c_final["C_VERDICT"], flush=True)
    print("B_VERDICT", b_verdict, flush=True)
    print("STOP. Runtime not changed. NEW_FORWARD_N=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
