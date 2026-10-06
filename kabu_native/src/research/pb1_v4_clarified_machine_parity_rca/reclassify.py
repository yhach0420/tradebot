"""Reclassify the seven prior MACHINE_SPEC_VIOLATION rows after time-aligned RCA."""
from __future__ import annotations

from typing import Any

SEVEN = (
    ("8058", "20250812"),
    ("6963", "20241002"),
    ("3382", "20241115"),
    ("6273", "20250120"),
    ("5802", "20250613"),
    ("3110", "20250805"),
    ("7182", "20251112"),
)


def _pick(rows: list[dict[str, Any]], symbol: str, date: str) -> dict[str, Any]:
    return next((x for x in rows if str(x.get("symbol")) == symbol and str(x.get("date")) == date), {}) or {}


def _known(row: dict[str, Any]) -> str:
    human = str(row.get("human_opening_state") or "")
    late = bool(row.get("human_late")) or human == "LATE_RANGE_RESOLUTION"
    if late:
        return "KNOWN_AFTER_0915"
    if human in ("TWO_SIDED_OPEN", "FLAT_OR_CRAWL", "MICRO_OR_LEAK", "TRUE_OPENING_DRIVE", "FAILED_OPEN_THEN_REAL_DRIVE"):
        return "KNOWN_AT_0915"
    return "AMBIGUOUS"


def reclassify_seven(
    *,
    rows: list[dict[str, Any]],
    late: dict[str, Any],
    fo: dict[str, Any],
    flat: dict[str, Any],
    two: dict[str, Any],
    logs: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    _ = two
    fo_neg = {(x.get("symbol"), x.get("date")): x for x in list(fo.get("negatives") or [])}
    fo_pos = {(x.get("symbol"), x.get("date")): x for x in list(fo.get("positives") or [])}
    flat_map = {(x.get("symbol"), x.get("date")): x for x in list(flat.get("rows") or [])}
    c3110 = dict(fo.get("3110") or {})
    late_leaks = {(x.get("symbol"), x.get("date")) for x in list(late.get("actual_active_leaks") or [])}
    out = []
    for sym, date in SEVEN:
        row = _pick(rows, sym, date)
        log = logs.get((sym, date)) or {}
        known = _known(row)
        cls = "IMPLEMENTATION_ASSUMPTION"
        reason = ""
        if (sym, date) == ("6963", "20241002"):
            cls = "CONFIRMED_MACHINE_ENCODING_BUG"
            reason = (
                "Clean ACTIVE-staleness case: location after seed evolution (09:39). "
                "Tiny leftover extremes reset stall. V2 requires renewed directional auction, not any new high/low."
            )
        elif (sym, date) == ("3110", "20250805"):
            abc = c3110.get("abc")
            cls = "HUMAN_LABEL_AMBIGUITY" if abc == "C" else ("CONFIRMED_MACHINE_ENCODING_BUG" if abc == "A" else "TIMING_MISMATCH")
            reason = str(c3110.get("abc_why") or "")
        elif (sym, date) == ("8058", "20250812"):
            v = str((flat.get("8058_20250812") or {}).get("verdict") or "")
            if "machine_bug" in v:
                cls = "CONFIRMED_MACHINE_ENCODING_BUG"
            elif "label_ambiguity" in v:
                cls = "HUMAN_LABEL_AMBIGUITY"
            else:
                cls = "IMPLEMENTATION_ASSUMPTION"
            reason = v
        elif (sym, date) in fo_neg:
            neg = fo_neg[(sym, date)]
            if neg.get("wide_range_or_doji_only") or not neg.get("visible_directional_attempt"):
                cls = "CONFIRMED_MACHINE_ENCODING_BUG"
                reason = "Machine minted FAILED_OPEN_SEED from a wide/ambiguous range without a visible failed directional attempt, then kept a live thesis."
            else:
                cls = "SPEC_AMBIGUITY"
                reason = "Visible-attempt semantics are close; V2 MAY-language is soft but a failed attempt is arguable."
        elif (sym, date) in late_leaks:
            cls = "CONFIRMED_MACHINE_ENCODING_BUG"
            reason = "ACTIVE remained after post-seed evolution on a human LATE leftover."
        else:
            cls = "TIMING_MISMATCH"
            reason = "Time-aligned review does not show a later ACTIVE leak independent of 09:14 location mint."
        out.append(
            {
                "symbol": sym,
                "date": date,
                "human_opening_state": row.get("human_opening_state"),
                "human_known": known,
                "machine_SEED": row.get("machine_SEED"),
                "location_t": row.get("location_t"),
                "replay_death": log.get("replay_death"),
                "final_rca_class": cls,
                "reason": reason,
                "old_audit_class": "MACHINE_SPEC_VIOLATION",
            }
        )
    confirmed = [x for x in out if x.get("final_rca_class") == "CONFIRMED_MACHINE_ENCODING_BUG"]
    return {
        "n": len(out),
        "rows": out,
        "confirmed_machine_bug_n": len(confirmed),
        "by_class": {
            k: sum(1 for x in out if x.get("final_rca_class") == k)
            for k in (
                "CONFIRMED_MACHINE_ENCODING_BUG",
                "SPEC_AMBIGUITY",
                "HUMAN_LABEL_AMBIGUITY",
                "TIMING_MISMATCH",
                "IMPLEMENTATION_ASSUMPTION",
            )
        },
        "do_not_preserve_old_class": True,
        "positives_checked": list(fo_pos.keys()),
        "flat_checked": list(flat_map.keys()),
    }
