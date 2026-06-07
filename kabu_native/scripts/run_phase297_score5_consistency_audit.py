#!/usr/bin/env python3
"""
Phase297: audit whether Phase272/273/274 score5 matches current production score5.

Output: kabu_native/results/reports/phase297_score5_consistency_audit.json
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase297_score5_consistency_audit.json"
P272 = REPO / "kabu_native/results/reports/phase272_v2_threshold_profit_max_review.json"
P273 = REPO / "kabu_native/results/reports/phase273_entry_score_v2_min5_implementation_report.json"
P274 = REPO / "kabu_native/results/reports/phase274_post_implementation_validation.json"
P292 = REPO / "kabu_native/results/reports/phase292_score_generation_integrity_audit.json"
P295 = REPO / "kabu_native/results/reports/phase295_hbrecent_pregate_fix_report.json"

DATE_START = 20260518
DATE_END = 20260605
P272_END = 20260603
REJECT_REPLAY_MAX_EVENTS = 500_000
JST = ZoneInfo("Asia/Tokyo")
DURATION_P66 = 406.0

FOCUS_TOKENS = (
    "HBRecent:no",
    "Duration:high",
    "Momentum:low",
    "Price:high",
    "TV:mid",
)


def _bootstrap() -> Any:
    for p in (REPO / "kabu_native" / "src", REPO / "kabu_native" / "scripts", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    import run_phase270_fast_paper_integration_comparison as p270

    return p270


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _git_show_at(ref: str, rel: str) -> str:
    try:
        r = subprocess.run(
            ["git", "show", f"{ref}:{rel}"],
            cwd=REPO,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if r.returncode != 0:
            return ""
        return r.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def _day_from_sid(sid: str) -> Optional[str]:
    parts = sid.replace("\\", "/").split("/")
    if parts and len(parts[0]) == 8 and parts[0].isdigit():
        return parts[0]
    return None


def _day_in_range(day: str, *, end: int = DATE_END) -> bool:
    try:
        d = int(day)
        return DATE_START <= d <= end
    except ValueError:
        return False


def _skip_session(sid: str, event_count: int) -> Optional[str]:
    low = sid.lower()
    if "phase282_discord_flow" in low:
        return "phase282_test_harness"
    if "phase284_resim" in low or "phase285_resim" in low:
        return "phase284_285_resim_harness"
    if event_count > REJECT_REPLAY_MAX_EVENTS:
        return f"event_count>{REJECT_REPLAY_MAX_EVENTS}"
    return None


def _discover_sessions(p270: Any, *, end: int = DATE_END) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for summary_path in sorted(SMALL_PAPER.rglob("small_paper_summary.json")):
        sid = summary_path.parent.relative_to(SMALL_PAPER).as_posix()
        day = _day_from_sid(sid)
        if not day or not _day_in_range(day, end=end):
            continue
        events = p270._load_events(summary_path.parent)
        if not events:
            continue
        if _skip_session(sid, len(events)):
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        found.append(
            {
                "session_id": sid,
                "day": day,
                "stream": p270._session_stream(sid, summary),
                "event_count": len(events),
                "session_dir": summary_path.parent,
            }
        )
    return found


class PriceRingTracker:
    def __init__(self) -> None:
        self.rings: dict[str, list[tuple[float, float]]] = {}

    def observe(self, ev: dict[str, Any], p270: Any) -> None:
        from small_paper.extended_entry_shadow import append_price_tick
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        px = p270._float(ev.get("current_price")) or 0.0
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        if not sym or px <= 0 or not ent:
            return
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        append_price_tick(self.rings.setdefault(sym, []), ts=ts, px=px)

    def hbrecent(self, ev: dict[str, Any], p270: Any) -> bool:
        from small_paper.extended_entry_shadow import compute_entry_high_break_recent_field
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = p270._float(ev.get("current_price")) or 0.0
        if not sym or not ent:
            return False
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        return bool(
            compute_entry_high_break_recent_field(
                trade=ev,
                payload={"CurrentPrice": px},
                price_ring=self.rings.get(sym, []),
                entry_ts=ts,
            )["entry_high_break_recent"]
        )


def _active_tokens(
    ev: dict[str, Any],
    *,
    score_points: dict[str, int],
    duration_p66: float,
    hbrecent_override: Optional[bool] = None,
) -> dict[str, bool]:
    from small_paper.entry_expectancy_score_shadow import TERTILE_CUTOFFS, _bin_tertile, _float, _feature_token

    work = dict(ev)
    if hbrecent_override is not None:
        work["entry_high_break_recent"] = hbrecent_override

    active: dict[str, bool] = {}
    for token, pts in score_points.items():
        if pts <= 0:
            continue
        lbl = token.split(":", 1)[0]
        if lbl == "HBRecent":
            hb = work.get("entry_high_break_recent")
            if hb is None:
                active[token] = False
                continue
            tok = f"HBRecent:{'yes' if str(hb).lower() in ('true', '1', 'yes') else 'no'}"
        elif lbl == "Duration":
            v = _float(work.get("max_continuation_duration"))
            if v is None:
                active[token] = False
                continue
            cuts = TERTILE_CUTOFFS["Duration"]
            level = _bin_tertile(v, cuts["p33"], duration_p66)
            tok = f"Duration:{level}"
        else:
            tok = _feature_token(lbl, work)
        active[token] = tok == token
    return active


def _score_from_tokens(active: dict[str, bool], score_points: dict[str, int]) -> int:
    return sum(score_points[t] for t, on in active.items() if on)


def _phase272_score(ev: dict[str, Any], p270: Any) -> tuple[int, dict[str, bool]]:
    """Phase270/272 replay: logged fields + compute_entry_expectancy_score_fields."""
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2, compute_entry_expectancy_score_fields

    sf = compute_entry_expectancy_score_fields(trade=ev)
    score = int(sf.get("entry_expectancy_score_v2") or 0)
    active = _active_tokens(ev, score_points=SCORE_POINTS_V2, duration_p66=DURATION_P66)
    return score, active


def _current_production_score(
    ev: dict[str, Any],
    ring: PriceRingTracker,
    p270: Any,
) -> tuple[int, dict[str, bool]]:
    """Post-Phase295: HBRecent pre-gate + standard Duration cutoff."""
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    work = dict(ev)
    work["entry_high_break_recent"] = ring.hbrecent(work, p270)
    active = _active_tokens(work, score_points=SCORE_POINTS_V2, duration_p66=DURATION_P66)
    score = _score_from_tokens(active, SCORE_POINTS_V2)
    return score, active


def _token_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "score5_candidate_count": 0,
            "token_hit_counts": {t: 0 for t in FOCUS_TOKENS},
            "token_hit_rates": {t: 0.0 for t in FOCUS_TOKENS},
            "pattern_counts": {},
            "focus_pattern_exact_match": 0,
        }
    token_hits = Counter()
    patterns = Counter()
    exact = 0
    for r in rows:
        active = r["active_tokens"]
        pat = "+".join(sorted(t for t in FOCUS_TOKENS if active.get(t)))
        patterns[pat] += 1
        if set(t for t in FOCUS_TOKENS if active.get(t)) == set(FOCUS_TOKENS):
            exact += 1
        for t in FOCUS_TOKENS:
            if active.get(t):
                token_hits[t] += 1
    return {
        "score5_candidate_count": n,
        "token_hit_counts": {t: token_hits[t] for t in FOCUS_TOKENS},
        "token_hit_rates": {t: round(token_hits[t] / n, 4) for t in FOCUS_TOKENS},
        "pattern_counts": dict(patterns.most_common(20)),
        "focus_pattern_exact_match": exact,
        "focus_pattern_exact_match_rate": round(exact / n, 4),
    }


def _compare_breakdown(p272: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    n272 = p272.get("score5_candidate_count", 0) or 0
    ncur = current.get("score5_candidate_count", 0) or 0
    delta_rates = {}
    for t in FOCUS_TOKENS:
        r272 = p272.get("token_hit_rates", {}).get(t, 0.0)
        rcur = current.get("token_hit_rates", {}).get(t, 0.0)
        delta_rates[t] = round(rcur - r272, 4)
    return {
        "phase272_score5_count": n272,
        "current_score5_count": ncur,
        "count_delta": ncur - n272,
        "count_ratio_current_over_phase272": round(ncur / n272, 4) if n272 else None,
        "token_hit_rate_delta_current_minus_phase272": delta_rates,
        "pattern_overlap_top5": _pattern_overlap(
            p272.get("pattern_counts", {}),
            current.get("pattern_counts", {}),
        ),
    }


def _pattern_overlap(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(set(a) | set(b), key=lambda k: -(int(a.get(k, 0)) + int(b.get(k, 0))))[:5]
    return {
        k: {"phase272": int(a.get(k, 0)), "current": int(b.get(k, 0))}
        for k in keys
    }


def _score_code_audit() -> dict[str, Any]:
    shadow_path = "kabu_native/src/small_paper/entry_expectancy_score_shadow.py"
    pilot_path = "kabu_native/src/small_paper/pilot_runner.py"
    p270_path = "kabu_native/scripts/run_phase270_fast_paper_integration_comparison.py"
    p273_path = "kabu_native/scripts/run_phase273_entry_score_v2_min5_implementation_report.py"

    pilot_now = (REPO / pilot_path).read_text(encoding="utf-8") if (REPO / pilot_path).is_file() else ""
    pilot_pre295 = _git_show_at("59e3d72", pilot_path)
    shadow_now = (REPO / shadow_path).read_text(encoding="utf-8") if (REPO / shadow_path).is_file() else ""
    shadow_pre295 = _git_show_at("59e3d72", shadow_path)

    return {
        "shared_formula": {
            "module": shadow_path,
            "function": "compute_entry_expectancy_score_fields",
            "score_points_key": "SCORE_POINTS_V2",
            "duration_p66_cutoff": DURATION_P66,
            "formula_unchanged_since_phase273_commit": shadow_now == shadow_pre295,
        },
        "phase272": {
            "script": "ephemeral _phase272_run_once.py (deleted; report retained)",
            "report": str(P272.relative_to(REPO)).replace("\\", "/"),
            "engine": "Phase270/71 fast-paper replay",
            "score_path": f"{p270_path}::_enrich -> compute_entry_expectancy_score_fields(trade=ev)",
            "input_fields": "logged event columns as-is (no HBRecent pre-gate recompute)",
            "gate_threshold": "score_v2_min variable (4/5/6 comparison)",
            "reference_metrics_B_v2_ge5": _load_json(P272).get("2_threshold_comparison_overall", {}).get("B_v2_ge5"),
        },
        "phase273": {
            "script": str(p273_path).replace("\\", "/"),
            "change": "entry_score_v2_min 4→5 only",
            "score_path": "compute_entry_expectancy_score_fields(trade=trade) in dry-run gate",
            "formula_changed": False,
            "yaml_gate_only": True,
        },
        "phase274": {
            "script": "ephemeral _phase274_run_once.py (deleted; report retained)",
            "report": str(P274.relative_to(REPO)).replace("\\", "/"),
            "engine": "same as Phase272 B_v2_ge5",
            "implementation_matches_phase272": _load_json(P274)
            .get("verdict", {})
            .get("implementation_matches_phase272"),
        },
        "live_production_pre_phase295": {
            "pilot_commit": "59e3d72 (kabutrade0603)",
            "hbrecent_before_gate": "compute_entry_high_break_recent_field" in pilot_pre295,
            "score_before_gate": "compute_entry_expectancy_score_fields(trade=trade)" in pilot_pre295,
            "shadow_only_on_accept": (
                "if decision.accept:" in pilot_pre295
                and pilot_pre295.find("compute_entry_shadow_fields")
                > pilot_pre295.find("compute_entry_expectancy_score_fields(trade=trade)")
            ),
        },
        "live_production_post_phase295": {
            "hbrecent_before_gate": "compute_entry_high_break_recent_field" in pilot_now,
            "score_before_gate": "compute_entry_expectancy_score_fields(trade=trade)" in pilot_now,
            "duration_cutoff_changed": False,
        },
    }


def _scan_sessions(
    sessions: list[dict[str, Any]],
    p270: Any,
    *,
    collect_breakdown: bool = True,
) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2, _float as sf_float

    phase272_score5: list[dict[str, Any]] = []
    current_score5: list[dict[str, Any]] = []
    logged_score5 = 0

    counters = {
        "decision_pool_events": 0,
        "phase272": Counter(),
        "current": Counter(),
        "logged": Counter(),
        "phase272_duration_high": 0,
        "current_duration_high": 0,
        "phase272_hbrecent_no": 0,
        "current_hbrecent_no": 0,
        "logged_hbrecent_null": 0,
        "phase272_hbrecent_null": 0,
        "score_changed_events": 0,
    }

    duration_max = 0.0
    duration_above_cutoff = 0
    duration_count = 0
    duration_p95_val: Optional[float] = None
    duration_samples: list[float] = []

    for sess in sessions:
        events = p270._load_events(sess["session_dir"])
        ring = PriceRingTracker()
        for ev in sorted(
            events,
            key=lambda e: (
                p270._parse_ts(str(e.get("event_time") or "")),
                int(p270._float(e.get("message_index")) or 0),
            ),
        ):
            ring.observe(ev, p270)
            if not p270._in_decision_pool(ev):
                continue
            counters["decision_pool_events"] += 1

            s272, a272 = _phase272_score(ev, p270)
            scur, acur = _current_production_score(ev, ring, p270)
            counters["phase272"][s272] += 1
            counters["current"][scur] += 1
            if s272 != scur:
                counters["score_changed_events"] += 1

            if ev.get("entry_high_break_recent") is None:
                counters["phase272_hbrecent_null"] += 1
                counters["logged_hbrecent_null"] += 1
            if a272.get("HBRecent:no"):
                counters["phase272_hbrecent_no"] += 1
            if acur.get("HBRecent:no"):
                counters["current_hbrecent_no"] += 1
            if a272.get("Duration:high"):
                counters["phase272_duration_high"] += 1
            if acur.get("Duration:high"):
                counters["current_duration_high"] += 1

            dv = sf_float(ev.get("max_continuation_duration"))
            if dv is not None:
                fv = float(dv)
                duration_count += 1
                duration_max = max(duration_max, fv)
                if fv > DURATION_P66:
                    duration_above_cutoff += 1
                if collect_breakdown and len(duration_samples) < 50_000:
                    duration_samples.append(fv)

            if collect_breakdown:
                if s272 >= 5:
                    phase272_score5.append({"active_tokens": a272, "score": s272, "day": sess["day"]})
                if scur >= 5:
                    current_score5.append({"active_tokens": acur, "score": scur, "day": sess["day"]})

            logged_v2 = p270._int(ev.get("entry_expectancy_score_v2"))
            if logged_v2 is not None:
                counters["logged"][logged_v2] += 1
                if logged_v2 >= 5:
                    logged_score5 += 1

    def _dist(c: Counter) -> dict[str, int]:
        return {str(k): v for k, v in sorted(c.items())}

    def _pct(vals: list[float], p: float) -> Optional[float]:
        if not vals:
            return None
        s = sorted(vals)
        idx = min(len(s) - 1, int(len(s) * p / 100.0))
        return round(float(s[idx]), 4)

    if duration_samples:
        duration_p95_val = _pct(duration_samples, 95)

    p272_bd = _token_breakdown(phase272_score5) if collect_breakdown else {}
    cur_bd = _token_breakdown(current_score5) if collect_breakdown else {}

    return {
        "sessions_scanned": len(sessions),
        "decision_pool_events": counters["decision_pool_events"],
        "score_distribution": {
            "phase272_replay": _dist(counters["phase272"]),
            "current_production_logic": _dist(counters["current"]),
            "logged_live_gate": _dist(counters["logged"]),
        },
        "score5_counts": {
            "phase272_replay": (
                len(phase272_score5)
                if collect_breakdown
                else sum(v for k, v in counters["phase272"].items() if k >= 5)
            ),
            "current_production_logic": (
                len(current_score5)
                if collect_breakdown
                else sum(v for k, v in counters["current"].items() if k >= 5)
            ),
            "logged_live_gate": logged_score5,
        },
        "score_ge5_rate": {
            "phase272_replay": round(
                (
                    len(phase272_score5)
                    if collect_breakdown
                    else sum(v for k, v in counters["phase272"].items() if k >= 5)
                )
                / max(1, counters["decision_pool_events"]),
                6,
            ),
            "current_production_logic": round(
                (
                    len(current_score5)
                    if collect_breakdown
                    else sum(v for k, v in counters["current"].items() if k >= 5)
                )
                / max(1, counters["decision_pool_events"]),
                6,
            ),
        },
        "hbrecent_no_presence": {
            "phase272_replay_token_hits": counters["phase272_hbrecent_no"],
            "current_production_token_hits": counters["current_hbrecent_no"],
            "phase272_events_with_hbrecent_null": counters["phase272_hbrecent_null"],
            "logged_events_with_hbrecent_null": counters["logged_hbrecent_null"],
            "phase272_hbrecent_no_rate": round(
                counters["phase272_hbrecent_no"] / max(1, counters["decision_pool_events"]), 4
            ),
            "current_hbrecent_no_rate": round(
                counters["current_hbrecent_no"] / max(1, counters["decision_pool_events"]), 4
            ),
        },
        "duration_high_presence": {
            "phase272_replay_token_hits": counters["phase272_duration_high"],
            "current_production_token_hits": counters["current_duration_high"],
            "max_continuation_duration": {
                "count": duration_count,
                "max": round(duration_max, 4) if duration_count else None,
                "p95_sampled": duration_p95_val,
                "above_cutoff_406": duration_above_cutoff,
            },
        },
        "score_changed_phase272_vs_current_events": counters["score_changed_events"],
        "phase272_score5_breakdown": p272_bd,
        "current_score5_breakdown": cur_bd,
        "composition_comparison": _compare_breakdown(p272_bd, cur_bd) if collect_breakdown else {},
        "score_points_v2": dict(SCORE_POINTS_V2),
    }


def _verdict(scan_p272_range: dict[str, Any], scan_full: dict[str, Any], code: dict[str, Any]) -> dict[str, Any]:
    p272_n = scan_p272_range["score5_counts"]["phase272_replay"]
    cur_n = scan_p272_range["score5_counts"]["current_production_logic"]
    logged_n = scan_p272_range["score5_counts"]["logged_live_gate"]

    same_formula = code["shared_formula"]["formula_unchanged_since_phase273_commit"]
    hbrecent_pre_gate_then = code["live_production_pre_phase295"]["hbrecent_before_gate"]
    hbrecent_pre_gate_now = code["live_production_post_phase295"]["hbrecent_before_gate"]

    dur272 = scan_p272_range["duration_high_presence"]["phase272_replay_token_hits"]
    dur_cur = scan_p272_range["duration_high_presence"]["current_production_token_hits"]

    adopted_same_as_current = (
        same_formula
        and hbrecent_pre_gate_then == hbrecent_pre_gate_now
        and p272_n == cur_n
        and scan_p272_range["score_changed_phase272_vs_current_events"] == 0
    )

    reasons: list[str] = []
    if not same_formula:
        reasons.append("SCORE_POINTS_V2 or cutoff constants changed since Phase273 commit")
    if not hbrecent_pre_gate_then and hbrecent_pre_gate_now:
        reasons.append(
            "Live gate at adoption time lacked HBRecent pre-gate; current production computes it before score (Phase295)"
        )
    if p272_n != cur_n:
        reasons.append(
            f"score5 candidate count differs on Phase272 window: replay={p272_n} current_logic={cur_n}"
        )
    if scan_p272_range["score_changed_phase272_vs_current_events"] > 0:
        reasons.append(
            f"{scan_p272_range['score_changed_phase272_vs_current_events']} decision-pool events change score under current logic"
        )
    if dur272 > 0:
        reasons.append(
            "Duration:high fired in Phase272 replay via logged max_continuation_duration "
            f"({dur272} hits) but not on live 6/4-6/5 gate rejects (Phase292)"
        )
    if logged_n != p272_n:
        reasons.append(
            f"logged live gate score5 ({logged_n}) != Phase272 replay score5 ({p272_n}); replay uses event fields not live gate path"
        )

    return {
        "adopted_score5_same_as_current_production": adopted_same_as_current,
        "formula_same": same_formula,
        "hbrecent_pre_gate_at_adoption": hbrecent_pre_gate_then,
        "hbrecent_pre_gate_current": hbrecent_pre_gate_now,
        "duration_high_fired_in_phase272_window": dur272 > 0,
        "duration_high_fired_current_logic_same_window": dur_cur > 0,
        "phase272_replay_equals_current_on_same_events": scan_p272_range["score_changed_phase272_vs_current_events"] == 0,
        "phase274_matches_phase272_b": code["phase274"]["implementation_matches_phase272"],
        "summary": (
            "Adopted score5 (Phase272/274 replay) is NOT identical to current production score5."
            if not adopted_same_as_current
            else "Adopted score5 matches current production score5 on formula and replay window."
        ),
        "reasons_not_identical": reasons,
        "notes": [
            "Phase272/274 evaluated score5 via fast-paper replay on logged event features (Phase270 _enrich).",
            "Live production at Phase273 gate time computed score before accept-only shadow fields; reject path missed HBRecent (+2).",
            "Phase295 fixed HBRecent pre-gate only; Duration:high cutoff (406) unchanged.",
            f"Full-range (through {DATE_END}) current_logic score5={scan_full['score5_counts']['current_production_logic']}.",
        ],
    }


def main() -> int:
    p270 = _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    sessions_p272 = _discover_sessions(p270, end=P272_END)
    sessions_full = _discover_sessions(p270, end=DATE_END)

    code = _score_code_audit()
    scan_p272 = _scan_sessions(sessions_p272, p270, collect_breakdown=True)
    scan_full = _scan_sessions(sessions_full, p270, collect_breakdown=False)

    report = {
        "phase": 297,
        "title": "score5_consistency_audit",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "objective": "Audit whether Phase272/273/274 score5 equals current production score5",
        "constraint": "investigation only",
        "references": {
            "phase272": str(P272.relative_to(REPO)).replace("\\", "/"),
            "phase273": str(P273.relative_to(REPO)).replace("\\", "/"),
            "phase274": str(P274.relative_to(REPO)).replace("\\", "/"),
            "phase292": str(P292.relative_to(REPO)).replace("\\", "/"),
            "phase295": str(P295.relative_to(REPO)).replace("\\", "/"),
        },
        "1_phase272_score_code": code["phase272"],
        "2_phase273_score_code": code["phase273"],
        "3_phase274_score_code": code["phase274"],
        "4_hbrecent_pre_gate_at_adoption": {
            "live_production_pre_phase295": code["live_production_pre_phase295"],
            "phase272_274_replay": "uses logged event fields; no HBRecent pre-gate recompute",
            "phase292_finding": _load_json(P292).get("check_6_phase273_timing", {}).get("verdict"),
            "verdict": (
                "HBRecent:no was NOT available before live gate at adoption time on reject path. "
                "Replay could still score HBRecent when logged field was present on accepted/historical rows."
            ),
        },
        "5_duration_high_fired_at_adoption": {
            "cutoff_p66": DURATION_P66,
            "phase272_window": scan_p272["duration_high_presence"],
            "live_6_4_6_5_note": (
                "Phase292: live 20260604-20260605 reject path had max_continuation_duration "
                "well below cutoff 406 (Duration:high=0 on gate rejects)."
            ),
            "verdict": (
                f"Duration:high DID fire in Phase272 replay ({scan_p272['duration_high_presence']['phase272_replay_token_hits']} token hits) "
                "because logged max_continuation_duration in replay events reaches far above 406 "
                f"(max={scan_p272['duration_high_presence']['max_continuation_duration']['max']}). "
                "This differs from live gate rejects on 6/4-6/5 where duration scale stayed low."
            ),
        },
        "6_phase272_score5_candidate_breakdown": scan_p272["phase272_score5_breakdown"],
        "7_composition_comparison_phase272_vs_current": scan_p272["composition_comparison"],
        "supporting_scans": {
            "phase272_date_window": {
                "date_range": [DATE_START, P272_END],
                **{k: v for k, v in scan_p272.items() if k != "composition_comparison"},
            },
            "full_date_window": {
                "date_range": [DATE_START, DATE_END],
                "score5_counts": scan_full["score5_counts"],
                "score_distribution_tail": {
                    k: {kk: vv for kk, vv in v.items() if int(kk) >= 3}
                    for k, v in scan_full["score_distribution"].items()
                },
                "hbrecent_no_presence": scan_full["hbrecent_no_presence"],
                "duration_high_presence": scan_full["duration_high_presence"],
            },
        },
        "shared_score_formula": code["shared_formula"],
        "verdict": _verdict(scan_p272, scan_full, code),
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    v = report["verdict"]
    print(
        f"same={v['adopted_score5_same_as_current_production']} "
        f"p272_score5={scan_p272['score5_counts']['phase272_replay']} "
        f"current_score5={scan_p272['score5_counts']['current_production_logic']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
