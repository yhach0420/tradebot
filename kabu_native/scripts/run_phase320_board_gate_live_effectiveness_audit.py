#!/usr/bin/env python3
"""
Phase320: Live audit — is Board:mid effective as an entry filter?

Read-only analysis of 20260608 AM/PM small_paper_events/rejects CSVs.
Output: phase320_board_gate_live_effectiveness_audit.json
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase320_board_gate_live_effectiveness_audit.json"
DAY = "20260608"
JST = ZoneInfo("Asia/Tokyo")

SESSIONS = {
    "am": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_080642",
    "pm": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_122548",
}

MOMENTUM_LOW_P33 = 0.2546  # Phase229 tertile cutoff for Momentum:low
SCORE_MIN = 3
MOMENTUM_BOARD_FALSE_MIN_REJECTS = 10  # "sufficient" threshold for verdict


def _bootstrap() -> None:
    src = REPO / "kabu_native" / "src"
    for p in (src, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _parse_bool(val: Any) -> Optional[bool]:
    if val is None or val == "":
        return None
    return str(val).lower() in ("true", "1", "yes")


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> Optional[int]:
    try:
        if val is None or val == "":
            return None
        return int(float(val))
    except (TypeError, ValueError):
        return None


def momentum_low_active(row: dict[str, str]) -> Optional[bool]:
    v = _float(row.get("momentum_continuation_score"))
    if v is None:
        v = _float(row.get("entry_momentum_continuation_score"))
    if v is None:
        return None
    return v <= MOMENTUM_LOW_P33


def board_tri_state(row: dict[str, str]) -> str:
    b = _parse_bool(row.get("entry_board_mid_token_active"))
    if b is True:
        return "true"
    if b is False:
        return "false"
    return "null"


def _load_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _board_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    c = Counter(board_tri_state(r) for r in rows)
    return {
        "board_true_count": c.get("true", 0),
        "board_false_count": c.get("false", 0),
        "board_null_count": c.get("null", 0),
    }


def _analyze_accepted(rows: list[dict[str, str]]) -> dict[str, Any]:
    board = _board_counts(rows)
    mom_true = sum(1 for r in rows if momentum_low_active(r) is True)
    mom_false = sum(1 for r in rows if momentum_low_active(r) is False)
    mom_null = sum(1 for r in rows if momentum_low_active(r) is None)
    score_dist = dict(Counter(_int(r.get("entry_expectancy_score_v2")) for r in rows))
    score_dist = {str(k): v for k, v in sorted(score_dist.items(), key=lambda x: (x[0] is None, x[0]))}
    n = len(rows)
    board_true_rate = round(board["board_true_count"] / n, 4) if n else None
    return {
        "accepted_count": n,
        "accepted_board_true_count": board["board_true_count"],
        "accepted_board_false_count": board["board_false_count"],
        "accepted_board_null_count": board["board_null_count"],
        "accepted_momentum_true_count": mom_true,
        "accepted_momentum_false_count": mom_false,
        "accepted_momentum_null_count": mom_null,
        "accepted_board_true_rate": board_true_rate,
        "accepted_score_distribution": score_dist,
    }


def _analyze_rejected(rows: list[dict[str, str]]) -> dict[str, Any]:
    board = _board_counts(rows)
    mom_board_false_score2: list[dict[str, str]] = []
    below_board_deficit = 0

    for r in rows:
        mom = momentum_low_active(r)
        board_false = board_tri_state(r) == "false"
        score = _int(r.get("entry_expectancy_score_v2"))
        reason = str(r.get("gate_reject_reason") or "")

        if mom is True and board_false and score == 2:
            mom_board_false_score2.append(r)

        if (
            reason == "entry_score_v2_below_threshold"
            and mom is True
            and board_false
            and score == 2
        ):
            below_board_deficit += 1

    mom_bf_s2_reasons = Counter(
        str(r.get("gate_reject_reason") or "") for r in mom_board_false_score2
    )

    return {
        "rejected_count": len(rows),
        "rejected_board_true_count": board["board_true_count"],
        "rejected_board_false_count": board["board_false_count"],
        "rejected_board_null_count": board["board_null_count"],
        "momentum_true_board_false_score2_count": len(mom_board_false_score2),
        "momentum_true_board_false_score2_reject_reason_counts": dict(
            mom_bf_s2_reasons.most_common()
        ),
        "entry_score_v2_below_threshold_board_deficit_count": below_board_deficit,
        "entry_score_v2_below_threshold_total": sum(
            1 for r in rows if str(r.get("gate_reject_reason") or "") == "entry_score_v2_below_threshold"
        ),
        "reject_reason_top10": dict(
            Counter(str(r.get("gate_reject_reason") or "") for r in rows).most_common(10)
        ),
    }


def _merge_accepted(am: dict[str, Any], pm: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "accepted_count",
        "accepted_board_true_count",
        "accepted_board_false_count",
        "accepted_board_null_count",
        "accepted_momentum_true_count",
        "accepted_momentum_false_count",
        "accepted_momentum_null_count",
    ]
    out: dict[str, Any] = {k: int(am.get(k, 0) or 0) + int(pm.get(k, 0) or 0) for k in keys}
    n = out["accepted_count"]
    out["accepted_board_true_rate"] = round(out["accepted_board_true_count"] / n, 4) if n else None
    merged_scores: Counter[str] = Counter()
    for block in (am, pm):
        for k, v in (block.get("accepted_score_distribution") or {}).items():
            merged_scores[str(k)] += int(v)
    out["accepted_score_distribution"] = dict(sorted(merged_scores.items()))
    return out


def _merge_rejected(am: dict[str, Any], pm: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "rejected_count",
        "rejected_board_true_count",
        "rejected_board_false_count",
        "rejected_board_null_count",
        "momentum_true_board_false_score2_count",
        "entry_score_v2_below_threshold_board_deficit_count",
        "entry_score_v2_below_threshold_total",
    ]
    out: dict[str, Any] = {k: int(am.get(k, 0) or 0) + int(pm.get(k, 0) or 0) for k in keys}
    reason_merge: Counter[str] = Counter()
    for block in (am, pm):
        for k, v in (block.get("momentum_true_board_false_score2_reject_reason_counts") or {}).items():
            reason_merge[k] += int(v)
    out["momentum_true_board_false_score2_reject_reason_counts"] = dict(
        reason_merge.most_common()
    )
    top_merge: Counter[str] = Counter()
    for block in (am, pm):
        for k, v in (block.get("reject_reason_top10") or {}).items():
            top_merge[k] += int(v)
    out["reject_reason_top10"] = dict(top_merge.most_common(10))
    return out


def _verdict(accepted: dict[str, Any], rejected: dict[str, Any]) -> dict[str, Any]:
    n_acc = int(accepted.get("accepted_count") or 0)
    board_true_rate = float(accepted.get("accepted_board_true_rate") or 0)
    acc_board_bad = int(accepted.get("accepted_board_false_count") or 0) + int(
        accepted.get("accepted_board_null_count") or 0
    )
    mom_bf_s2 = int(rejected.get("momentum_true_board_false_score2_count") or 0)
    below_deficit = int(rejected.get("entry_score_v2_below_threshold_board_deficit_count") or 0)

    accepted_board_near_100 = n_acc > 0 and board_true_rate >= 0.99
    rejected_sufficient = mom_bf_s2 >= MOMENTUM_BOARD_FALSE_MIN_REJECTS

    effective = accepted_board_near_100 and rejected_sufficient and acc_board_bad == 0

    rationale: list[str] = []
    if acc_board_bad > 0:
        rationale.append(f"accepted has board_false={accepted.get('accepted_board_false_count')} board_null={accepted.get('accepted_board_null_count')}")
    if not accepted_board_near_100:
        rationale.append(f"accepted board_true_rate={board_true_rate} (<0.99)")
    if not rejected_sufficient:
        rationale.append(f"momentum_true_board_false_score2_count={mom_bf_s2} (<{MOMENTUM_BOARD_FALSE_MIN_REJECTS})")
    if effective:
        rationale.append(
            f"accepted board_true_rate={board_true_rate:.2%} (n={n_acc}); "
            f"rejected Momentum+no-Board score=2: {mom_bf_s2}; "
            f"entry_score_v2_below_threshold board-deficit: {below_deficit}"
        )

    return {
        "Board_effective_as_entry_filter": effective,
        "accepted_board_true_rate": board_true_rate,
        "accepted_board_false_or_null": acc_board_bad,
        "momentum_true_board_false_score2_count": mom_bf_s2,
        "entry_score_v2_below_threshold_board_deficit_count": below_deficit,
        "rationale": rationale,
    }


def _session_block(label: str, session_dir: Path) -> dict[str, Any]:
    events_path = session_dir / "small_paper_events.csv"
    rejects_path = session_dir / "small_paper_rejects.csv"
    events = _load_rows(events_path)
    accepted_rows = [r for r in events if r.get("event_type") == "accepted"]
    reject_rows = _load_rows(rejects_path)
    if not reject_rows:
        reject_rows = [r for r in events if r.get("event_type") == "rejected"]
    return {
        "session_label": label,
        "session_dir": str(session_dir.relative_to(REPO)).replace("\\", "/"),
        "events_csv": str(events_path.relative_to(REPO)).replace("\\", "/"),
        "rejects_csv": str(rejects_path.relative_to(REPO)).replace("\\", "/"),
        "events_csv_exists": events_path.is_file(),
        "rejects_csv_exists": rejects_path.is_file(),
        "accepted": _analyze_accepted(accepted_rows),
        "rejected": _analyze_rejected(reject_rows),
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    blocks = {k: _session_block(k, p) for k, p in SESSIONS.items()}
    am_acc, pm_acc = blocks["am"]["accepted"], blocks["pm"]["accepted"]
    am_rej, pm_rej = blocks["am"]["rejected"], blocks["pm"]["rejected"]
    combined_accepted = _merge_accepted(am_acc, pm_acc)
    combined_rejected = _merge_rejected(am_rej, pm_rej)
    verdict = _verdict(combined_accepted, combined_rejected)

    report = {
        "phase": 320,
        "title": "board_gate_live_effectiveness_audit",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": "analysis only; no logic changes",
        "target_date": DAY,
        "production_entry_logic": {
            "SCORE_POINTS_V2": {"Momentum:low": 2, "Board:mid": 1},
            "entry_score_v2_min": SCORE_MIN,
            "momentum_low_required": True,
            "momentum_low_p33_cutoff": MOMENTUM_LOW_P33,
        },
        "sessions": blocks,
        "combined": {
            "accepted": combined_accepted,
            "rejected": combined_rejected,
        },
        "verdict": verdict,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"Board_effective={verdict['Board_effective_as_entry_filter']} "
        f"acc_board_true_rate={verdict['accepted_board_true_rate']} "
        f"mom_board_false_s2={verdict['momentum_true_board_false_score2_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
