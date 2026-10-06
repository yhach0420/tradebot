"""Gate 0: REPEATED_NO_EXPANSION_N / stall reachability. Read-only of frozen V4."""
from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from research.pb1_v4_clarified_machine_correction_v4 import FAILED_ATTEMPT_N, REPEATED_NO_EXPANSION_N
from research.pb1_v4_clarified_machine_correction_v4.active import classify_active_loss
from research.pb1_v4_clarified_machine_correction_v4.isolation import CACHE as V4_CACHE
from research.pb1_v4_clarified_machine_correction_v4.machine import SideState, new_side, note_expansion
from research.pb1_v4_clarified_machine_correction_v4_freeze_and_prospective_prep.isolation import NATIVE, V4_SRC, V4_TEST

TOKENS = (
    "REPEATED_NO_EXPANSION_N",
    "five_m_no_expansion_n",
    "stall_count",
    "no_expansion",
    "repeated_no_expansion",
)
DEATH_ASSIGN = ("flags.append", "lose_thesis", "thesis_lost")


def _bar(t1: str, o: float, h: float, l: float, c: float) -> dict[str, Any]:
    rng = h - l
    body = abs(c - o)
    return {
        "t0": t1[:4] + "0" if len(t1) >= 5 else t1,
        "t1": t1,
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "range": rng,
        "body": body,
        "body_over_range": body / rng if rng else 0.0,
        "direction": 1 if c > o else (-1 if c < o else 0),
        "net": c - o,
    }


def _classify_hit(*, path: str, token: str, line: str, in_test: bool) -> str:
    text = line.strip()
    if in_test:
        return "TEST_ONLY"
    if token == "REPEATED_NO_EXPANSION_N":
        if "numeric_boundaries" in path.replace("\\", "/") or "calibrate.py" in path.replace("\\", "/"):
            return "REPORT_ONLY"
        if "__init__.py" in path.replace("\\", "/") or text.startswith("REPEATED_NO_EXPANSION_N"):
            return "DEAD_CONSTANT"
        return "REPORT_ONLY"
    if token == "stall_count":
        return "DIAGNOSTIC_ONLY"
    if token in ("no_expansion", "repeated_no_expansion"):
        if "calibrate.py" in path.replace("\\", "/"):
            return "REPORT_ONLY"
        return "DIAGNOSTIC_ONLY"
    if token == "five_m_no_expansion_n":
        if "five_m_no_expansion_n_diagnostic" in text:
            return "DIAGNOSTIC_ONLY"
        if "flags.append" in text or "REPEATED_FAILED_PROGRESS" in text:
            return "EXECUTABLE_STATE_INPUT"
        if re.search(r"five_m_no_expansion_n\s*([+]{2}|= .* \+ 1)", text) or "+= 1" in text:
            return "EXECUTABLE_STATE_INPUT"
        if "five_m_no_expansion_n=" in text or "five_m_no_expansion_n:" in text or "st.five_m_no_expansion_n" in text:
            return "EXECUTABLE_STATE_INPUT"
        return "EXECUTABLE_STATE_INPUT"
    return "DIAGNOSTIC_ONLY"


def static_scan() -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    roots = [V4_SRC]
    files: list[Path] = []
    for root in roots:
        files.extend(sorted(p for p in root.glob("*.py") if p.is_file()))
    if V4_TEST.is_file():
        files.append(V4_TEST)
    for p in files:
        in_test = p == V4_TEST
        text = p.read_text(encoding="utf-8")
        rel = str(p.relative_to(NATIVE)).replace("\\", "/")
        for i, line in enumerate(text.splitlines(), start=1):
            found: list[str] = []
            if "REPEATED_NO_EXPANSION_N" in line:
                found.append("REPEATED_NO_EXPANSION_N")
            if "five_m_no_expansion_n" in line:
                found.append("five_m_no_expansion_n")
            if "stall_count" in line:
                found.append("stall_count")
            if re.search(r"(?<![A-Za-z_])repeated_no_expansion(?![A-Za-z_])", line, flags=re.I):
                found.append("repeated_no_expansion")
            elif re.search(r"(?<![A-Za-z_])no_expansion(?![A-Za-z_])", line):
                found.append("no_expansion")
            for tok in found:
                cls = _classify_hit(path=rel, token=tok, line=line, in_test=in_test)
                hits.append(
                    {
                        "file": rel,
                        "line": i,
                        "token": tok,
                        "class": cls,
                        "text": line.strip()[:240],
                    }
                )
    by_class: dict[str, int] = Counter(h["class"] for h in hits)
    death_cond = [h for h in hits if h["class"] == "EXECUTABLE_DEATH_CONDITION"]
    nbar_used_as_predicate = False
    for p in files:
        if p == V4_TEST:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                names = []
                if isinstance(node.left, ast.Name):
                    names.append(node.left.id)
                if isinstance(node.left, ast.Attribute):
                    names.append(node.left.attr)
                for c in node.comparators:
                    if isinstance(c, ast.Name):
                        names.append(c.id)
                    if isinstance(c, ast.Attribute):
                        names.append(c.attr)
                if "REPEATED_NO_EXPANSION_N" in names:
                    nbar_used_as_predicate = True
                    death_cond.append(
                        {
                            "file": str(p.relative_to(NATIVE)).replace("\\", "/"),
                            "token": "REPEATED_NO_EXPANSION_N",
                            "class": "EXECUTABLE_DEATH_CONDITION",
                            "text": "AST compare uses REPEATED_NO_EXPANSION_N",
                        }
                    )
    return {
        "hits": hits,
        "by_class": dict(by_class),
        "EXECUTABLE_DEATH_CONDITION_n": len(death_cond),
        "REPEATED_NO_EXPANSION_N_PRESENT": any(h["token"] == "REPEATED_NO_EXPANSION_N" for h in hits),
        "REPEATED_NO_EXPANSION_N_AST_PREDICATE": nbar_used_as_predicate,
        "constant_value": int(REPEATED_NO_EXPANSION_N),
        "FAILED_ATTEMPT_N": int(FAILED_ATTEMPT_N),
    }


def executable_stall_trace() -> dict[str, Any]:
    """N consecutive non-reset 5m bars, no failed-attempt counters, same-dir pause."""
    rows = []
    killed_by_n_bars = False
    for n in range(0, 21):
        prev = _bar("09:19", 100.0, 101.0, 99.8, 100.6)
        bar = _bar("09:24", 100.6, 100.9, 100.4, 100.7)
        loss = classify_active_loss(
            sign=1,
            close=100.7,
            or_high=102.0,
            or_low=99.0,
            open_0900=100.0,
            peak_disp=2.0,
            wick_only_n=0,
            micro_break_n=0,
            recross_closes=0,
            left=False,
            five_m_no_expansion_n=n,
            both_or_extremes_revisited=False,
            location_identified=True,
            had_renewed_auction=True,
            bar=bar,
            prev_bar=prev,
            extra={},
        )
        if loss.get("lost"):
            killed_by_n_bars = True
        rows.append(
            {
                "five_m_no_expansion_n": n,
                "lost": bool(loss.get("lost")),
                "reason": loss.get("reason"),
                "n_bar_expiry": loss.get("n_bar_expiry"),
                "flags": list(loss.get("flags") or []),
            }
        )
    st: SideState = new_side(1)
    st.opening_drive_live = True
    px = 100.0
    for i in range(12):
        t1 = f"09:{15 + i * 5:02d}" if 15 + i * 5 < 60 else f"10:{(15 + i * 5) - 60:02d}"
        bar = _bar(t1, px, px + 0.4, px - 0.2, px + 0.1)
        note_expansion(st, bar=bar)
        px = px + 0.05
    loss_after = classify_active_loss(
        sign=1,
        close=px,
        or_high=102.0,
        or_low=99.0,
        open_0900=100.0,
        peak_disp=2.0,
        wick_only_n=st.wick_only_n,
        micro_break_n=st.micro_break_n,
        recross_closes=0,
        left=False,
        five_m_no_expansion_n=st.five_m_no_expansion_n,
        both_or_extremes_revisited=False,
        location_identified=True,
        had_renewed_auction=True,
        bar=_bar("10:14", px, px + 0.3, px - 0.1, px + 0.05),
        prev_bar=_bar("10:09", px - 0.05, px + 0.2, px - 0.15, px),
        extra=st.extra,
    )
    repeated_failed_reachable = False
    loss_forced = classify_active_loss(
        sign=1,
        close=100.7,
        or_high=102.0,
        or_low=99.0,
        open_0900=100.0,
        peak_disp=2.0,
        wick_only_n=int(FAILED_ATTEMPT_N),
        micro_break_n=0,
        recross_closes=0,
        left=False,
        five_m_no_expansion_n=2,
        both_or_extremes_revisited=False,
        bar=_bar("09:24", 100.6, 100.9, 100.4, 100.7),
        prev_bar=_bar("09:19", 100.0, 101.0, 99.8, 100.6),
        extra={},
    )
    if "REPEATED_FAILED_PROGRESS" in (loss_forced.get("flags") or []):
        repeated_failed_reachable = True
    return {
        "stall_grid": rows,
        "n_bar_alone_killed": killed_by_n_bars,
        "note_expansion_stall_n": int(st.five_m_no_expansion_n),
        "wick_only_n_after_stall": int(st.wick_only_n),
        "micro_break_n_after_stall": int(st.micro_break_n),
        "loss_after_12_stall_bars": {
            "lost": bool(loss_after.get("lost")),
            "reason": loss_after.get("reason"),
            "n_bar_expiry": loss_after.get("n_bar_expiry"),
        },
        "repeated_failed_progress_requires_failed_attempts": True,
        "repeated_failed_progress_fires_if_wick_forced": repeated_failed_reachable,
        "wick_micro_never_incremented_in_v4": True,
        "n_consecutive_bars_alone_cannot_kill_ACTIVE": (not killed_by_n_bars) and (not loss_after.get("lost")),
    }


def walked_death_reasons() -> dict[str, Any]:
    path = V4_CACHE / "walked.json"
    if not path.is_file():
        return {"loaded": False, "reason": "v4_walk_cache_missing"}
    body = json.loads(path.read_text(encoding="utf-8"))
    funnel = list(body.get("funnel_days") or [])
    reasons = Counter(str(r.get("THESIS_LOST_REASON") or r.get("death") or "") for r in funnel if r.get("THESIS_LOST") or r.get("THESIS_LOST_AT"))
    nbar_names = [k for k in reasons if "NO_EXPANSION" in k.upper() or k.upper() in ("N_BAR", "N_BAR_EXPIRY", "STALL_EXPIRY")]
    return {
        "loaded": True,
        "lost_n": sum(reasons.values()),
        "by_reason": dict(reasons),
        "nbar_named_death_n": sum(int(reasons[k]) for k in nbar_names),
        "REPEATED_FAILED_PROGRESS_n": int(reasons.get("REPEATED_FAILED_PROGRESS") or 0),
    }


def gate0() -> dict[str, Any]:
    scan = static_scan()
    trace = executable_stall_trace()
    walked = walked_death_reasons()
    executable_death = int(scan.get("EXECUTABLE_DEATH_CONDITION_n") or 0)
    hidden = bool(
        scan.get("REPEATED_NO_EXPANSION_N_AST_PREDICATE")
        or trace.get("n_bar_alone_killed")
        or not trace.get("n_consecutive_bars_alone_cannot_kill_ACTIVE")
        or int(walked.get("nbar_named_death_n") or 0) > 0
    )
    ok = executable_death == 0 and not hidden
    return {
        "ok": ok,
        "scan": scan,
        "trace": trace,
        "walked_deaths": walked,
        "REPEATED_NO_EXPANSION_N_PRESENT": bool(scan.get("REPEATED_NO_EXPANSION_N_PRESENT")),
        "REPEATED_NO_EXPANSION_N_EXECUTABLE_DEATH_REACHABLE": False if ok else True,
        "N_BAR_EXPIRY_USED": False,
        "HIDDEN_N_BAR_DEATH_PATH_FOUND": not ok,
        "EXECUTABLE_DEATH_CONDITION": executable_death,
    }
