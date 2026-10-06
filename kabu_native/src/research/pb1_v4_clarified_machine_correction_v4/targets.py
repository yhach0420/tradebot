"""Required V4 target checks. Not accuracy. First candidate, no retune."""
from __future__ import annotations

from typing import Any


def _clock_hhmm(value: Any) -> str:
    text = str(value or "")
    if "T" in text:
        text = text.split("T", 1)[1]
    if " " in text:
        text = text.split(" ", 1)[1]
    return text[:5]


def _row(audit: dict[str, Any], key: str) -> dict[str, Any]:
    hit = dict(audit.get(key) or {})
    if hit:
        return hit
    if "_" not in str(key):
        return {}
    sym, date = str(key).split("_", 1)
    for r in list(audit.get("rows") or []):
        if str(r.get("symbol")) == sym and str(r.get("date")) == date:
            return dict(r)
    return {}


def _ok(row: dict[str, Any], pred: bool, detail: str) -> dict[str, Any]:
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "PASS": bool(pred),
        "detail": detail,
        "SEED": row.get("machine_SEED"),
        "opening": row.get("machine_opening_state"),
        "post_dominant_class": row.get("post_dominant_class") or row.get("POST_DOMINANT_PATH_STATE"),
        "sandwich_semantic_class": row.get("sandwich_semantic_class"),
        "THESIS_LIVE": row.get("machine_THESIS_LIVE"),
        "THESIS_REACHED": row.get("machine_THESIS_REACHED"),
        "ACTIVE_LIVE": row.get("machine_ACTIVE_LIVE"),
        "THESIS_LOST_AT": row.get("machine_THESIS_LOST_AT"),
        "THESIS_LOST_REASON": row.get("machine_THESIS_LOST_REASON"),
        "E1": row.get("e1_entry_t") or row.get("machine_E1"),
        "auction_end_family": row.get("auction_end_family"),
    }


def target_checks(audit: dict[str, Any]) -> dict[str, Any]:
    r = lambda a, b: _row(audit, f"{a}_{b}")
    a7011m = r("7011", "20241205")
    post = str(a7011m.get("post_dominant_class") or a7011m.get("POST_DOMINANT_PATH_STATE") or "")
    t_one = [
        _ok(
            a7011m,
            post == "INITIAL_DISPLACEMENT_WITH_ABSORPTION"
            and str(a7011m.get("machine_SEED") or "") == "NO_VALID_DRIVE_SEED"
            and str(a7011m.get("machine_opening_state") or "") in ("MICRO_OR_LEAK", "NO_VALID_DRIVE_SEED")
            and not a7011m.get("machine_ACTIVE")
            and not a7011m.get("machine_THESIS_LIVE"),
            "7011/20241205 NO_VALID/MICRO remains; post-dominant persisted",
        ),
    ]
    protect_one = []
    for sym, date in (("8002", "20241002"), ("6857", "20250930"), ("8630", "20250828"), ("6501", "20251010")):
        row = r(sym, date)
        protect_one.append(
            _ok(
                row,
                str(row.get("machine_SEED") or "") == "TRUE_OPENING_DRIVE_SEED"
                or str(row.get("post_dominant_class") or "") == "INITIAL_DISPLACEMENT_WITH_REAL_FOLLOWTHROUGH",
                f"{sym}/{date} real post-dominant continuation protected",
            )
        )
    pull = []
    for sym, date in (("6963", "20250613"), ("5803", "20250212"), ("8031", "20250225")):
        row = r(sym, date)
        if (sym, date) == ("8031", "20250225"):
            pull.append(
                _ok(
                    row,
                    str(row.get("sandwich_semantic_class") or "") != "TWO_SIDED_COMMITTED_FIGHT"
                    and not (
                        str(row.get("machine_opening_state") or "") == "TWO_SIDED_OPEN"
                        and str(row.get("sandwich_semantic_class") or "") == "PULLBACK_ORIGINAL_AUCTION_INTACT"
                    ),
                    "8031 pullback must not be killed as TWO_SIDED",
                )
            )
        else:
            pull.append(
                _ok(
                    row,
                    str(row.get("machine_SEED") or "") == "TRUE_OPENING_DRIVE_SEED"
                    or str(row.get("sandwich_semantic_class") or "") == "PULLBACK_ORIGINAL_AUCTION_INTACT",
                    f"{sym}/{date} intact pullback must not be body-only TWO_SIDED",
                )
            )
    fight = []
    for sym, date in (("7741", "20250314"), ("5803", "20250709"), ("5706", "20250725")):
        row = r(sym, date)
        cls = str(row.get("sandwich_semantic_class") or "")
        fight.append(
            _ok(
                row,
                cls in ("MEANINGFUL_COUNTER_AUCTION", "TWO_SIDED_COMMITTED_FIGHT")
                or str(row.get("machine_opening_state") or "") == "TWO_SIDED_OPEN",
                f"{sym}/{date} meaningful counter must not be rescued as pullback TRUE",
            )
        )
    s3382 = r("3382", "20241004")
    t3382 = _ok(
        s3382,
        str(s3382.get("machine_SEED") or "") == "FAILED_OPEN_SEED"
        and bool(s3382.get("machine_ACTIVE_REACHED") or s3382.get("opening_drive_id"))
        and bool(s3382.get("e1_entry_t") or s3382.get("machine_E1"))
        and _clock_hhmm(s3382.get("e1_entry_t")).startswith("09:50")
        and _clock_hhmm(s3382.get("machine_THESIS_LOST_AT")).startswith("10:04"),
        "3382 E1 09:50 survives; death remains after E1 at 10:04",
    )
    s7011 = r("7011", "20250523")
    t7011 = _ok(
        s7011,
        str(s7011.get("machine_SEED") or "") == "FAILED_OPEN_SEED"
        and bool(s7011.get("machine_THESIS_REACHED") or s7011.get("thesis_id"))
        and (not s7011.get("machine_THESIS_LIVE"))
        and (not s7011.get("machine_ACTIVE_LIVE"))
        and _clock_hhmm(s7011.get("machine_THESIS_LOST_AT")).startswith("09:44")
        and str(s7011.get("machine_THESIS_LOST_REASON") or "") in ("FAILED_BREAK_REACCEPTED", "STALE_RANGE_RESOLUTION"),
        "7011/20250523 FAILED_BREAK_REACCEPTED / RANGE_ACCEPTED at confirmation 09:44",
    )
    s4063 = r("4063", "20251118")
    t4063 = _ok(
        s4063,
        str(s4063.get("machine_SEED") or "") == "FAILED_OPEN_SEED"
        and bool(s4063.get("machine_THESIS_REACHED") or s4063.get("thesis_id"))
        and (not s4063.get("machine_THESIS_LIVE"))
        and (not s4063.get("machine_ACTIVE_LIVE"))
        and _clock_hhmm(s4063.get("machine_THESIS_LOST_AT")).startswith("10:19")
        and str(s4063.get("machine_THESIS_LOST_REASON") or "") == "STALE_RANGE_RESOLUTION",
        "4063 existing COMMITTED_OPPOSITE_NON_CONTRACTING STALE route preserved",
    )
    items = t_one + protect_one + pull + fight + [t3382, t7011, t4063]
    return {
        "checks": items,
        "n": len(items),
        "pass_n": sum(1 for x in items if x.get("PASS")),
        "fail_n": sum(1 for x in items if not x.get("PASS")),
        "all_pass": all(bool(x.get("PASS")) for x in items),
    }
