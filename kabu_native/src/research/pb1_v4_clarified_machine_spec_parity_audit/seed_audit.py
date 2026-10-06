"""SEED taxonomy vs 09:15-knowable human labels. LATE is not scored at seed."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_clarified_machine_implementation.seed import continued_intent_state
from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES

PREV_S1_FP = (
    (1, "7011", "20241205", "MICRO_OR_LEAK"),
    (8, "1605", "20250318", "TWO_SIDED_OPEN"),
    (9, "8031", "20250225", "TWO_SIDED_OPEN"),
    (11, "7741", "20250314", "TWO_SIDED_OPEN"),
    (13, "6701", "20250203", "TWO_SIDED_OPEN"),
    (16, "8002", "20250109", "LATE_RANGE_RESOLUTION"),
    (17, "6920", "20250404", "MICRO_OR_LEAK"),
    (19, "6367", "20250702", "TWO_SIDED_OPEN"),
    (41, "7203", "20250729", "TWO_SIDED_OPEN"),
    (43, "5803", "20250904", "LATE_RANGE_RESOLUTION"),
    (44, "6871", "20250922", "LATE_RANGE_RESOLUTION"),
    (45, "6525", "20250917", "TWO_SIDED_OPEN"),
    (46, "9501", "20251113", "MICRO_OR_LEAK"),
    (52, "4519", "20250826", "LATE_RANGE_RESOLUTION"),
    (56, "5801", "20251022", "LATE_RANGE_RESOLUTION"),
    (64, "8058", "20250801", "TWO_SIDED_OPEN"),
    (67, "6501", "20250724", "LATE_RANGE_RESOLUTION"),
    (70, "2914", "20241022", "TWO_SIDED_OPEN"),
    (78, "9501", "20250311", "LATE_RANGE_RESOLUTION"),
)

INVALID_AT_0915 = ("TWO_SIDED_OPEN", "FLAT_OR_CRAWL", "MICRO_OR_LEAK")
VALID_SEEDS = ("TRUE_OPENING_DRIVE_SEED", "FAILED_OPEN_SEED")


def _conf(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    tp = sum(1 for h, m in pairs if h and m)
    tn = sum(1 for h, m in pairs if (not h) and (not m))
    fp = sum(1 for h, m in pairs if (not h) and m)
    fn = sum(1 for h, m in pairs if h and (not m))
    n = len(pairs)
    return {"n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn, "accuracy": (tp + tn) / n if n else None}


def seed_valid(seed: Any) -> bool:
    return str(seed or "") in VALID_SEEDS


def seed_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs_0915: list[tuple[bool, bool]] = []
    late_excluded = 0
    fp_invalid_as_seed = []
    for r in rows:
        human = str(r.get("human_opening_state") or "")
        late = bool(r.get("human_late")) or human == "LATE_RANGE_RESOLUTION"
        m_valid = seed_valid(r.get("machine_SEED"))
        if late:
            late_excluded += 1
            r["seed_scored_at_0915"] = False
            r["human_known_t"] = "AFTER_0915"
            continue
        r["seed_scored_at_0915"] = True
        r["human_known_t"] = "09:15"
        h_valid = human in VALID_OPENING_STATES
        pairs_0915.append((h_valid, m_valid))
        if human in INVALID_AT_0915 and m_valid:
            fp_invalid_as_seed.append(r)
        r["seed_parity_0915_ok"] = h_valid == m_valid
    # human invalid that machine started as TRUE/FAILED
    started_wrong_or_plausible = []
    for r in rows:
        human = str(r.get("human_opening_state") or "")
        if human not in INVALID_AT_0915:
            continue
        if not seed_valid(r.get("machine_SEED")):
            continue
        later_killed = (not bool(r.get("machine_ACTIVE"))) or (not bool(r.get("machine_LOCATION")))
        started_wrong_or_plausible.append(
            {
                "rca_id": r.get("rca_id"),
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "human_opening_state": human,
                "machine_SEED": r.get("machine_SEED"),
                "machine_ACTIVE": r.get("machine_ACTIVE"),
                "machine_LOCATION": r.get("machine_LOCATION"),
                "machine_THESIS_READY": r.get("machine_THESIS_READY"),
                "machine_death": r.get("machine_death"),
                "class": "plausible_seed_later_killed" if later_killed and not r.get("machine_THESIS_READY") else "seed_itself_wrong_or_active_leak",
                "intent": (r.get("path") or {}).get("open_bars"),
            }
        )
    fp19_map = []
    for rid, sym, date, human in PREV_S1_FP:
        rec = next((x for x in rows if str(x.get("symbol")) == sym and str(x.get("date")) == date), None) or {}
        m_valid = seed_valid(rec.get("machine_SEED"))
        later_killed = m_valid and not rec.get("machine_THESIS_READY")
        still_leak = bool(rec.get("machine_THESIS_READY")) and (human == "LATE_RANGE_RESOLUTION" or rec.get("human_late"))
        if not m_valid:
            kind = "correct_seed_reject"
        elif later_killed:
            kind = "plausible_seed_but_later_killed"
        elif still_leak:
            kind = "still_material_ACTIVE_leak"
        else:
            kind = "seed_itself_wrong_or_active_leak"
        fp19_map.append(
            {
                "rca_id": rid,
                "symbol": sym,
                "date": date,
                "prev_human": human,
                "now_seed": rec.get("machine_SEED"),
                "now_active": rec.get("machine_ACTIVE"),
                "now_thesis": rec.get("machine_THESIS_READY"),
                "kind": kind,
            }
        )
    return {
        "late_not_scored_at_seed_n": late_excluded,
        "seed_parity_at_0915": _conf(pairs_0915),
        "human_invalid_started_as_seed": started_wrong_or_plausible,
        "prev_s1_fp19": fp19_map,
        "fp19_correct_seed_reject_n": sum(1 for x in fp19_map if x["kind"] == "correct_seed_reject"),
        "fp19_plausible_later_killed_n": sum(1 for x in fp19_map if x["kind"] == "plausible_seed_but_later_killed"),
        "fp19_still_material_active_leak_n": sum(1 for x in fp19_map if x["kind"] == "still_material_ACTIVE_leak"),
        "fp19_seed_wrong_n": sum(1 for x in fp19_map if x["kind"] == "seed_itself_wrong_or_active_leak"),
    }


def two_sided_internal(rows: list[dict[str, Any]], *, symbol: str, date: str) -> dict[str, Any]:
    rec = next((x for x in rows if str(x.get("symbol")) == symbol and str(x.get("date")) == date), None)
    if not rec:
        return {"missing": True, "symbol": symbol, "date": date}
    bars = list(((rec.get("path") or {}).get("open_bars") or []))
    sign = int(rec.get("machine_DIR") or 0)
    intent = continued_intent_state(bars, sign=sign) if bars and sign in (1, -1) else {}
    dirs = [int(b.get("direction") or 0) for b in bars]
    bodies = [b.get("body_over_range") for b in bars]
    n_opp = int(sum(1 for d in dirs if d == -sign and d != 0))
    substantial_opp = any(
        int(b.get("direction") or 0) == -sign and float(b.get("body_over_range") or 0) >= 0.35 for b in bars
    )
    loc = intent.get("opening_close_loc")
    committed = loc is not None and (float(loc) > 0.65 if sign > 0 else float(loc) < 0.35)
    if n_opp >= 1 and substantial_opp and committed:
        verdict = "B_two_sided_detector_too_permissive"
        cls = "MACHINE_SPEC_VIOLATION"
        why = "substantial opposite body exists but two_sided_balance also requires uncommitted close loc, so a committed finish still mints TRUE"
    elif n_opp >= 1 and not substantial_opp:
        verdict = "A_human_label_coarse_or_legitimate_pullback"
        cls = "HUMAN_LABEL_AMBIGUITY"
        why = "opposite bar is not a substantial counter-auction; last bar continues"
    else:
        verdict = "EXPECTED_REPRESENTATION_DIFFERENCE"
        cls = "EXPECTED_REPRESENTATION_DIFFERENCE"
        why = "path is mixed; machine TRUE vs human TWO_SIDED is a representation split"
    return {
        "symbol": symbol,
        "date": date,
        "human_opening_state": rec.get("human_opening_state"),
        "machine_SEED": rec.get("machine_SEED"),
        "machine_ACTIVE": rec.get("machine_ACTIVE"),
        "machine_THESIS_READY": rec.get("machine_THESIS_READY"),
        "dirs": dirs,
        "bodies": bodies,
        "intent": intent,
        "substantial_opp": substantial_opp,
        "committed_finish": committed,
        "verdict": verdict,
        "mismatch_class": cls,
        "why": why,
        "through": "09:15",
        "future_outcome_used": False,
    }


def flat_crawl_case(rows: list[dict[str, Any]], *, symbol: str, date: str) -> dict[str, Any]:
    rec = next((x for x in rows if str(x.get("symbol")) == symbol and str(x.get("date")) == date), None)
    if not rec:
        return {"missing": True, "symbol": symbol, "date": date}
    bars = list(((rec.get("path") or {}).get("open_bars") or []))
    intent = continued_intent_state(bars, sign=int(rec.get("machine_DIR") or 0)) if bars and int(rec.get("machine_DIR") or 0) in (1, -1) else {}
    failing = []
    if rec.get("machine_SEED") in VALID_SEEDS:
        if intent.get("one_bar_domination"):
            failing.append("continued_intent_one_bar")
        if intent.get("loss_of_directional_progress"):
            failing.append("continued_intent_last_crawl")
        if not failing:
            failing.append("taxonomy_precedence_or_same_clock_scale_accepted_a_crawl_as_drive")
    return {
        "symbol": symbol,
        "date": date,
        "human_opening_state": rec.get("human_opening_state"),
        "machine_SEED": rec.get("machine_SEED"),
        "machine_ACTIVE": rec.get("machine_ACTIVE"),
        "machine_THESIS_READY": rec.get("machine_THESIS_READY"),
        "intent": intent,
        "failing_family": failing,
        "bodies": [b.get("body_over_range") for b in bars],
        "dirs": [b.get("direction") for b in bars],
        "through": "09:15",
    }
