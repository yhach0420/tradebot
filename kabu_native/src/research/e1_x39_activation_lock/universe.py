"""Historical Universe provenance + anchor mapping audit (no PnL mapping)."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from research.e1_x31_population_direction.identity import reproduce_population
from research.e1_x32_upstream_attribution.eval_stages import clock_epochs_for_day
from research.e1_x32_upstream_attribution.membership import load_captured_symbols
from research.e1_x33b_neutral_anchor.neutral import candidate_symbols_by_day

from . import CLOCK_POINTS_HM, FORBIDDEN_FROM, HISTORICAL_DAYS

NATIVE = Path(__file__).resolve().parents[3]
UNIVERSE_GENS = ("am", "am_refresh1000", "pm", "pm_refresh1430")

# Documented scheduled times (filename convention) — not automatic effective_time proof
GEN_SCHEDULED_HM = {
    "am": (9, 0),
    "am_refresh1000": (10, 0),
    "pm": (12, 30),
    "pm_refresh1430": (14, 30),
}


def _norm(s: str) -> str:
    s = str(s).strip()
    return s[:-2] if s.endswith(".T") else s


def load_universe_generation(day: str, gen: str) -> tuple[set[str], Path | None]:
    ddir = NATIVE / "results" / "daily" / day / "runtime"
    fp = ddir / f"universe_core10_dynamic40_price_risk_{gen}_{day}.csv"
    if not fp.exists():
        return set(), None
    out: set[str] = set()
    with fp.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm(row.get("symbol") or row.get("symbol_key") or "")
            if not sym:
                continue
            passed = row.get("passed")
            if passed is not None and str(passed).lower() in ("false", "0", "no"):
                continue
            out.add(sym)
    return out, fp


def provenance_conclusion() -> dict[str, Any]:
    """
    Source/code/artifact provenance for CANDIDATE_SYMBOL_POOL.
    Does not invent prospective mapping from PnL.
    """
    return {
        "candidate_symbol_pool_definition": (
            "Day-level unique symbols from X30/X33 population "
            "(load_population → X28E combined → X19/X14 cluster-first episodes)."
        ),
        "source_files": [
            "src/research/e1_x30_absolute_rise_entry_v2/population.py",
            "src/research/e1_x33b_neutral_anchor/neutral.py:candidate_symbols_by_day",
            "src/research/e1_x33b_neutral_anchor/neutral.py:planned_neutral_anchors",
            "src/research/e1_x32_upstream_attribution/funnel.py:STAGE_7_X30_X31_POPULATION",
            "src/research/e1_x19_outcome_pre_path/population.py:_build_day",
        ],
        "runtime_universe_artifacts": (
            "results/daily/{day}/runtime/universe_core10_dynamic40_price_risk_"
            "{am|am_refresh1000|pm|pm_refresh1430}_{day}.csv"
        ),
        "historical_v1r_membership_semantic": (
            "DAY_FIXED CANDIDATE_SYMBOL_POOL × CLOCK_POINTS_HM. "
            "Same symbol set for all anchors on a day (including 10:00 and 14:40). "
            "NOT as-of 'latest universe generation with effective_time <= t'."
        ),
        "cluster_first_uses_forward_labels": True,
        "prospective_cannot_rebuild_pool_via_cluster_first": True,
        "no_previous_day_universe_fallback": True,
        "prebuild_policy": "fail_closed_if_same_day_AM_missing",
        "performance_used_to_choose_mapping": False,
    }


def audit_historical_universe_parity() -> dict[str, Any]:
    rows, _, _ = reproduce_population()
    pool = candidate_symbols_by_day(rows)
    day_rows: list[dict[str, Any]] = []
    anchor_rows: list[dict[str, Any]] = []
    refresh_deltas = []

    for day in HISTORICAL_DAYS:
        assert day < FORBIDDEN_FROM
        gens: dict[str, set[str]] = {}
        paths: dict[str, str | None] = {}
        for g in UNIVERSE_GENS:
            syms, fp = load_universe_generation(day, g)
            gens[g] = syms
            paths[g] = str(fp) if fp else None
        cand = pool[day]
        cap = load_captured_symbols(day)
        union = set().union(*gens.values()) if any(gens.values()) else set()
        am = gens.get("am", set())
        ar = gens.get("am_refresh1000", set())
        pm = gens.get("pm", set())
        pr = gens.get("pm_refresh1430", set())

        am_ar_delta = (len(am - ar), len(ar - am))
        pm_pr_delta = (len(pm - pr), len(pr - pm))
        refresh_deltas.append({"date": day, "am_vs_refresh1000": am_ar_delta, "pm_vs_refresh1430": pm_pr_delta})

        missing_vs_am = sorted(am - cand)
        gap_class: list[dict[str, Any]] = []
        for sym in missing_vs_am:
            in_cap = sym in cap
            day_dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            board = NATIVE / "data" / "push_jsonl" / day_dash / f"{sym}.T.jsonl"
            gap_class.append({
                "symbol": sym,
                "in_am_universe": True,
                "in_capture": in_cap,
                "board_exists": board.exists(),
                "classification": (
                    "CAPTURE_MISS" if not in_cap
                    else "CLUSTER_EPISODE_ABSENT_RESEARCH_FILTER"
                ),
            })

        day_rows.append({
            "date": day,
            "candidate_pool_count": len(cand),
            "am_count": len(am),
            "am_refresh1000_count": len(ar),
            "pm_count": len(pm),
            "pm_refresh1430_count": len(pr),
            "captured_count": len(cap),
            "cand_eq_am": cand == am,
            "cand_subset_am": cand <= am,
            "gens_identical": am == ar == pm == pr,
            "am_vs_refresh1000_delta": am_ar_delta,
            "pm_vs_refresh1430_delta": pm_pr_delta,
            "missing_vs_am_n": len(missing_vs_am),
            "gap_classifications": gap_class,
            "universe_paths": paths,
            "difference_kind": (
                "NONE" if cand == am
                else "BOARD_OR_CLUSTER_AVAILABILITY_NOT_UNIVERSE_SEMANTIC"
            ),
        })

        # Per-anchor mapping record (Historical semantic = day-fixed pool)
        for epoch, sess in clock_epochs_for_day(day):
            from datetime import datetime
            from zoneinfo import ZoneInfo
            JST = ZoneInfo("Asia/Tokyo")
            dt = datetime.fromtimestamp(epoch, tz=JST)
            hm = (dt.hour, dt.minute)
            # Historical: active generation for V1R membership is NOT refresh-switched
            active = "DAY_FIXED_CANDIDATE_SYMBOL_POOL"
            # Diagnostic: what as-of would pick if using scheduled HM as effective_time
            asof_candidates = []
            for g, (hh, mm) in GEN_SCHEDULED_HM.items():
                # scheduled wall clock that day
                from datetime import date as date_cls
                sched = datetime(
                    int(day[:4]), int(day[4:6]), int(day[6:8]), hh, mm, tzinfo=JST
                ).timestamp()
                if sched <= epoch + 1e-9:
                    asof_candidates.append((sched, g))
            asof_gen = max(asof_candidates)[1] if asof_candidates else None
            asof_set = gens.get(asof_gen, set()) if asof_gen else set()
            anchor_rows.append({
                "date": day,
                "session": sess,
                "anchor_hm": f"{hm[0]:02d}:{hm[1]:02d}",
                "anchor_time": epoch,
                "active_universe_generation": active,
                "universe_artifact_path": None,  # pool is research population, not a CSV
                "effective_time_rule": "DAY_FIXED_POOL_NOT_ASOF_GENERATION",
                "symbol_count_day_pool": len(cand),
                "candidate_pool_count": len(cand),
                "asof_diagnostic_generation": asof_gen,
                "asof_diagnostic_count": len(asof_set),
                "intersection_pool_asof": len(cand & asof_set) if asof_set else len(cand),
                "missing_vs_asof": sorted(asof_set - cand)[:8] if asof_set else [],
                "extra_vs_asof": sorted(cand - asof_set)[:8] if asof_set else [],
            })

    all_gens_identical = all(r["gens_identical"] for r in day_rows)
    all_subset = all(r["cand_subset_am"] for r in day_rows)
    any_refresh_delta = any(
        r["am_vs_refresh1000_delta"] != (0, 0) or r["pm_vs_refresh1430_delta"] != (0, 0)
        for r in day_rows
    )

    # Prospective binding decision
    unresolved_reasons = [
        (
            "Historical V1R membership is DAY_FIXED CANDIDATE_SYMBOL_POOL from X30 "
            "cluster-first population, not as-of universe generation switching."
        ),
        (
            "Cluster-first construction uses forward labels; prospective cannot rebuild "
            "exact Historical pool identity from universe CSVs alone."
        ),
        (
            "On Historical14, AM/refresh1000/PM/refresh1430 membership were identical "
            f"(any_refresh_delta={any_refresh_delta}); therefore refresh-asof cannot be "
            "validated as the Historical V1R semantic."
        ),
        (
            "Candidate vs AM gaps are BOARD_OR_CLUSTER_AVAILABILITY "
            f"(cand_eq_am days={sum(1 for r in day_rows if r['cand_eq_am'])}/14); "
            "not Universe generation semantic differences."
        ),
        (
            "Freezing prospective pool as AM/capture/asof-refresh would invent a mapping "
            "not proven by Historical V1R provenance; forbidden by E1_X39 §3/§5."
        ),
    ]

    return {
        "provenance": provenance_conclusion(),
        "day_parity": day_rows,
        "anchor_mapping_sample": [a for a in anchor_rows if a["anchor_hm"] in (
            "09:05", "10:00", "10:20", "12:40", "14:40"
        )],
        "anchor_mapping_n": len(anchor_rows),
        "refresh_deltas": refresh_deltas,
        "all_gens_identical_historical14": all_gens_identical,
        "any_refresh_membership_delta": any_refresh_delta,
        "all_candidate_subset_am": all_subset,
        "rule_1000": {
            "v1r_anchor_1000_exists": True,
            "runtime_am_refresh1000_exists": True,
            "historical_v1r_rule": (
                "10:00 uses same DAY_FIXED CANDIDATE_SYMBOL_POOL as 09:05; "
                "refresh1000 does not change Historical V1R membership "
                "(gens identical on H14; day-fixed semantic)."
            ),
            "forbidden": "Choosing universe by post-hoc 10:00 performance",
            "deterministic_ordering": (
                "If a prospective as-of rule were ever proven, refresh effective only after "
                "generation fully written; until then 10:00 remains pre-refresh/day-fixed. "
                "Historical SoT remains day-fixed pool (not as-of)."
            ),
        },
        "rule_1430_1440": {
            "v1r_anchor_1430": False,
            "v1r_anchor_1440": True,
            "historical_v1r_rule": (
                "14:40 uses same DAY_FIXED CANDIDATE_SYMBOL_POOL; "
                "PM refresh1430 did not alter H14 membership vs AM/PM."
            ),
            "no_retroactive_rewrite": True,
        },
        "prospective_mapping_frozen": False,
        "prospective_mapping": None,
        "unresolved_reasons": unresolved_reasons,
        "pass": False,
        "verdict_hint": "E1_X39_UNIVERSE_BINDING_UNRESOLVED",
    }
