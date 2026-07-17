#!/usr/bin/env python3
"""Phase687W42: Final Shadow Priority Alignment (research labels only).

MAINLINE / ENTRY / EXIT / YAML / orders / Shadow enable-disable unchanged.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPORTS = NATIVE / "results" / "reports"
W41 = REPORTS / "phase687w41_shadow_inventory_cleanup"
OUT = REPORTS / "phase687w42_shadow_priority_alignment"
JST = ZoneInfo("Asia/Tokyo")

NEXT_PHASE = "Phase687W43: Pre-Entry Market State Analysis"

PRIORITY_A = [
    "board_dynamic_trailing_shadow",
    "flat_weak_range_shadow",
    "imbalance_shadow",
    "microsequence_recovery_fail_shadow",
    "realtime_board_exit_shadow",
]

PULLBACK_REASON = (
    "Demoted A→B: 18 trading days, blocked=75, CAP replay ΔPnL=-266.55, "
    "no PF/MDD improvement; 20260716 single-day improve did not reproduce cumulatively. "
    "Reference observation only — not an adoption candidate."
)

RESEARCH_DIRECTION_NOTE = (
    "Shadow探索フェーズは完了。\n\n"
    "今後はShadow追加を目的とせず、\n"
    "価格・出来高・板・更新頻度・PBv2を統合した\n"
    "ENTRY前市場状態解析へ研究対象を移行する。"
)


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wc(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in ((k, r.get(k)) for k in cols)})


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    inv = _read_csv(W41 / "shadow_inventory_updated.csv")
    changed: list[dict[str, str]] = []

    for r in inv:
        sid = r["shadow_id"]
        prior_pri = (r.get("priority") or "").strip()
        r["prior_priority_w41"] = prior_pri
        if r.get("status") != "ACTIVE":
            r["priority"] = ""
            r["future_monitor"] = "NO"
            r["priority_role"] = ""
            continue

        if sid == "pullback_misread_guard_shadow":
            new_pri = "B"
            r["priority"] = new_pri
            r["future_monitor"] = "NO"
            r["reason"] = PULLBACK_REASON
            r["priority_role"] = "reference_observation"
            changed.append(
                {
                    "shadow_id": sid,
                    "from_priority": prior_pri or "A",
                    "to_priority": new_pri,
                    "reason": PULLBACK_REASON,
                }
            )
            continue

        if sid in PRIORITY_A:
            r["priority"] = "A"
            r["future_monitor"] = "YES"
            r["priority_role"] = "future_monitor"
        elif prior_pri == "B":
            r["priority"] = "B"
            r["future_monitor"] = "NO"
            r["priority_role"] = "reference_observation"
        else:
            r["priority"] = "C"
            r["future_monitor"] = "NO"
            r["priority_role"] = "archive_observe"

    pri = {
        "A": sorted(r["shadow_id"] for r in inv if r.get("priority") == "A"),
        "B": sorted(r["shadow_id"] for r in inv if r.get("priority") == "B"),
        "C": sorted(r["shadow_id"] for r in inv if r.get("priority") == "C"),
    }
    assert pri["A"] == sorted(PRIORITY_A), pri["A"]
    assert "pullback_misread_guard_shadow" in pri["B"]
    assert "pullback_misread_guard_shadow" not in pri["A"]

    _wc(OUT / "shadow_inventory_updated.csv", inv)
    _wc(
        OUT / "shadow_priority_matrix.csv",
        [
            {
                "shadow_id": r["shadow_id"],
                "name": r["name"],
                "status": r["status"],
                "prior_priority_w41": r.get("prior_priority_w41") or r.get("priority"),
                "priority": r["priority"],
                "future_monitor": r["future_monitor"],
                "priority_role": r.get("priority_role") or "",
                "reason": r.get("reason") or "",
            }
            for r in inv
            if r.get("status") == "ACTIVE"
        ],
    )

    required = {
        "1_priority_a": pri["A"],
        "2_priority_b": pri["B"],
        "3_priority_c": pri["C"],
        "4_changed_shadows": changed,
        "5_shadow_exploration_phase_ended": True,
        "6_next_phase": NEXT_PHASE,
        "7_submit_cancel": {"submit": 0, "cancel": 0},
        "8_mainline_unchanged": True,
    }

    report = {
        "phase": "Phase687W42",
        "title": "Final Shadow Priority Alignment",
        "verdict": ["SHADOW_PRIORITY_FINALIZED", "RESEARCH_DIRECTION_CHANGED"],
        "generated_at": datetime.now(JST).isoformat(),
        "constraints": {
            "mainline_changed": False,
            "entry_exit_changed": False,
            "yaml_changed": False,
            "real_orders_changed": False,
            "shadow_enable_disable_changed": False,
            "submit": 0,
            "cancel": 0,
        },
        "priority_a_final": PRIORITY_A,
        "changed": changed,
        "research_direction_note": RESEARCH_DIRECTION_NOTE,
        "next_phase": NEXT_PHASE,
        "required_answers": required,
        "note": "Inventory/priority labels only; no runtime or YAML mutation.",
    }
    _wj(OUT / "phase687w42_report.json", report)

    md = f"""# Phase687W42 Final Shadow Priority Alignment

## Verdict: `SHADOW_PRIORITY_FINALIZED` / `RESEARCH_DIRECTION_CHANGED`

### Constraints
- MAINLINE unchanged: **True**
- ENTRY/EXIT unchanged: **True**
- YAML unchanged: **True**
- Real orders unchanged: **True**
- Shadow enable/disable unchanged: **True**
- submit/cancel: **0/0**

### Required answers
1. Priority A: `{pri['A']}`
2. Priority B: `{pri['B']}`
3. Priority C: `{pri['C']}`
4. Changed: `{[c['shadow_id'] for c in changed]}` (pullback_misread_guard_shadow A→B)
5. Shadow探索フェーズ終了: **True**
6. Next phase: **{NEXT_PHASE}**
7. submit/cancel: **0/0**
8. MAINLINE unchanged: **True**

### Change detail
- `pullback_misread_guard_shadow`: Priority A → **Priority B**
  - {PULLBACK_REASON}

### Priority A (future monitor only)
{chr(10).join(f'- `{s}`' for s in PRIORITY_A)}

### Research direction

{RESEARCH_DIRECTION_NOTE}
"""
    _wm(OUT / "decision.md", md)
    print(json.dumps({"out": str(OUT), "priority_a": pri["A"], "changed": changed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
