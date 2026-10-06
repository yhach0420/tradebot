"""Event study + standalone scoring. Precommit must already be hashed."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from research.pb1_complete_strategy_causal_repair_mechanism_discovery.bars import or_levels
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.decide import annotate_class, summarize_exit, summarize_sizing
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.events import detect_events, path_extrema
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.levels import restore_level
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.load import daily_history, prior_and_atr
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.precommit import EXIT_CANDIDATES, SIZING_CANDIDATES
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.score import candidate_exit
from research.pb1_complete_strategy_causal_repair_mechanism_discovery.sizing import apply_sizing


def run_event_study(*, trades: list[dict[str, Any]], recs: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    hist = daily_history(recs)
    dev_atrs: list[float] = []
    prepared: list[dict[str, Any]] = []
    missing_rec = 0
    asf_ident = 0
    for tr in trades:
        rec = recs.get((str(tr["symbol"]), str(tr["date"])))
        if rec is None:
            missing_rec += 1
            continue
        prior, atr = prior_and_atr(hist, symbol=str(tr["symbol"]), date=str(tr["date"]))
        if tr.get("sample") == "DEV" and atr is not None:
            dev_atrs.append(float(atr))
        orl = or_levels(rec)
        lvl = restore_level(
            tr,
            or_high=orl.get("or_high") if orl else None,
            or_low=orl.get("or_low") if orl else None,
            prior_dailies=prior,
            rec=rec,
        )
        if lvl.get("identifiable"):
            asf_ident += 1
        path = path_extrema(tr, rec)
        klass = annotate_class(tr, path)
        ev = detect_events(tr, rec, level=lvl.get("level"))
        prepared.append(
            {
                **tr,
                "path": path,
                "path_class": klass,
                "events": ev,
                "level_restore": lvl,
                "atr20": atr,
            }
        )
    atr_ref = float(median(dev_atrs)) if dev_atrs else float("nan")
    by_mech: dict[str, list[dict[str, Any]]] = {m: [] for m in EXIT_CANDIDATES}
    giveback_rows = []
    asf_rows = []
    recross_rows = []
    noexp_rows = []
    winner_rows = []
    for row in prepared:
        rec = recs[(str(row["symbol"]), str(row["date"]))]
        path = row["path"]
        ev = row["events"]
        for mech in EXIT_CANDIDATES:
            scored = candidate_exit(row, rec, ev.get(mech))
            packed = {
                **scored,
                "symbol": row["symbol"],
                "date": row["date"],
                "fold": row["fold"],
                "month": row["month"],
                "sample": row["sample"],
                "side": row["side"],
                "path_class": row["path_class"],
                "entry_px": row["entry_px"],
                "MFE_bps": path.get("MFE_bps"),
                "MAE_bps": path.get("MAE_bps"),
                "capture": None,
                "THESIS_LOST_AT": row.get("THESIS_LOST_AT"),
                "THESIS_LOST_REASON": row.get("THESIS_LOST_REASON"),
                "current_exit": row.get("exit_reason"),
            }
            mfe = path.get("MFE_bps")
            rz = scored.get("cand_gross_bps")
            if mfe and float(mfe) > 0 and rz is not None:
                packed["capture"] = float(rz) / float(mfe)
            by_mech[mech].append(packed)
            if row["path_class"] == "C_FAVORABLE_THEN_FULL_GIVEBACK":
                giveback_rows.append({"mechanism": mech, **packed, "first_fav": path.get("first_favorable_expansion_t"), "peak_t": path.get("time_to_MFE")})
            if row["path_class"] == "B_SMALL_EDGE_NEVER_EXPANDED":
                noexp_rows.append({"mechanism": mech, **packed})
            if float(row.get("net_pnl_yen") or 0) > 0:
                winner_rows.append({"mechanism": mech, **packed})
            if mech in {"FIRST_OR_RECROSS", "SECOND_OR_RECROSS"}:
                recross_rows.append({"mechanism": mech, **packed})
            if mech == "ASF_FIRST_STRUCTURAL_KILL":
                asf_rows.append({**packed, **row["level_restore"], "event_t": ev.get(mech)})
    exit_summ = {m: summarize_exit(rows) for m, rows in by_mech.items()}
    sizing_rows: dict[str, list[dict[str, Any]]] = {s: [] for s in SIZING_CANDIDATES}
    for row in prepared:
        for fam in SIZING_CANDIDATES:
            sized = apply_sizing(row, family=fam, atr_ref=atr_ref, atr=row.get("atr20"))
            sizing_rows[fam].append({**sized, "symbol": row["symbol"], "date": row["date"], "fold": row["fold"], "month": row["month"]})
    sizing_summ = {fam: summarize_sizing(rows, family=fam) for fam, rows in sizing_rows.items()}
    months = sorted({str(r["month"]) for r in prepared})
    month_exit = {}
    for m in EXIT_CANDIDATES:
        month_exit[m] = {}
        for mo in months:
            sub = [r for r in by_mech[m] if r.get("month") == mo]
            if not sub:
                continue
            month_exit[m][mo] = {
                "n": len(sub),
                "delta_net_bps": sum(float(x.get("delta_net_bps") or 0) for x in sub) / len(sub),
            }
    return {
        "prepared_n": len(prepared),
        "missing_rec": missing_rec,
        "atr_ref": atr_ref,
        "atr_ref_source": "median_development_ATR20",
        "asf_identifiable_n": asf_ident,
        "asf_identifiable_frac": (asf_ident / len(prepared)) if prepared else 0.0,
        "by_mech": by_mech,
        "exit_summ": exit_summ,
        "sizing_rows": sizing_rows,
        "sizing_summ": sizing_summ,
        "giveback_rows": giveback_rows,
        "noexp_rows": noexp_rows,
        "winner_rows": winner_rows,
        "recross_rows": recross_rows,
        "asf_rows": asf_rows,
        "month_exit": month_exit,
        "path_class_counts": _counts(prepared, "path_class"),
        "prepared": prepared,
    }


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    bag: dict[str, int] = defaultdict(int)
    for r in rows:
        bag[str(r.get(key) or "")] += 1
    return dict(bag)
