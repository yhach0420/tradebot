"""
Phase 163: Diagnose Phase161 (G hybrid PF ~1.77) vs Phase162 (full replay PF ~0.89) mismatch.
Review only — no exit policy or production changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.cap3_entry_replay import _profit_factor
from research.fade_exit_replay import FADE_EXIT_REASONS
from research.phase161_fade_shadow_policy_review import analyze_session, _write_csv
from research.small_paper_performance_review import _load_events
from small_paper.discord_notifier import observer_tracker_config_from_pilot
from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1

SESSION_CLOSE_REASONS = frozenset(
    {
        "session_end",
        "morning_session_close",
        "afternoon_session_close",
        "fade_hybrid_session_close",
        "fade_watch_session_close",
    }
)

LOSS_CATEGORIES = (
    "session_close",
    "overlap_replaced",
    "quality_decay",
    "stop_hit",
    "second_fade",
    "breakdown",
    "replay_baseline_gap",
    "sim_horizon",
    "other",
)


def _trade_key(session: str, symbol: str, entry_time: str) -> tuple[str, str, str]:
    return session, symbol, entry_time


def _load_phase162_details(path: Path) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    """Map (scenario, session, symbol, entry_time) -> row."""
    out: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            scen = str(row.get("scenario") or "")
            sess = str(row.get("session") or "")
            sym = str(row.get("symbol") or "")
            ent = str(row.get("entry_time") or "")
            out[(scen, sess, sym, ent)] = row
    return out


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _is_second_fade(reason: str) -> bool:
    r = str(reason or "")
    return r in ("fade_hybrid_second_fade", "fade_hybrid_delayed") or "second_fade" in r


def _is_breakdown(reason: str) -> bool:
    r = str(reason or "")
    return r in ("fade_hybrid_breakdown", "fade_watch_breakdown") or "breakdown" in r


def classify_improvement_loss(
    *,
    gain_161: float,
    gain_162: float,
    actual_reason: str,
    g161_reason: str,
    a_reason: str,
    b_reason: str,
    actual_hold: float,
    g161_hold: float,
    a_hold: float,
    b_hold: float,
    actual_pnl: float,
    a_pnl: float,
) -> str:
    """Why Phase161 improvement did not appear in Phase162 replay."""
    lost = gain_161 - gain_162
    if lost <= 0.02:
        return "retained"

    if abs(actual_pnl - a_pnl) > 0.08 and gain_162 < gain_161 * 0.4:
        return "replay_baseline_gap"

    if any("overlap_replaced" in r for r in (a_reason, b_reason, actual_reason)):
        return "overlap_replaced"

    if _is_second_fade(b_reason) or (
        _is_second_fade(g161_reason) and b_reason in FADE_EXIT_REASONS
    ):
        return "second_fade"

    if _is_breakdown(b_reason) or _is_breakdown(g161_reason):
        return "breakdown"

    # Phase161 sim runs to actual_close+300s; G often exits at session_end with reaccel path.
    if g161_reason == "session_end" and actual_reason in FADE_EXIT_REASONS:
        if _is_second_fade(b_reason) or _is_breakdown(b_reason):
            return "second_fade" if _is_second_fade(b_reason) else "breakdown"
        if b_reason in SESSION_CLOSE_REASONS or b_hold > g161_hold + 60:
            return "session_close"
        return "sim_horizon"

    if b_reason == "stop_hit" or a_reason == "stop_hit":
        return "stop_hit"

    if "quality_decay" in b_reason or "quality_decay" in a_reason:
        return "quality_decay"

    if b_reason in SESSION_CLOSE_REASONS or (
        b_hold > g161_hold + 45 and g161_reason not in SESSION_CLOSE_REASONS
    ):
        return "session_close"

    if g161_reason in SESSION_CLOSE_REASONS and b_reason not in SESSION_CLOSE_REASONS:
        return "other"

    return "other"


def _safe_pf(pnls: Sequence[float]) -> float:
    if not pnls:
        return 0.0
    p = _profit_factor(list(pnls))
    if p is None:
        return 0.0
    if p == float("inf"):
        return 99.0
    return float(p)


def analyze_phase163(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    phase162_details_csv: Optional[Path] = None,
    cap5_csv: Optional[Path] = None,
    improvement_threshold: float = 0.02,
) -> dict[str, Any]:
    from research.phase159_overlap_review import load_cap5_only_keys

    exit_cfg = observer_tracker_config_from_pilot(pilot_config)
    exit_cfg.structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1
    cap5_keys = load_cap5_only_keys(cap5_csv) if cap5_csv else set()

    p162 = _load_phase162_details(phase162_details_csv) if phase162_details_csv else {}
    scen_a = "A_combined_v1"
    scen_b = "B_fade_hybrid_shadow"

    phase161_rows: list[dict[str, Any]] = []
    for sdir in session_dirs:
        events = _load_events(sdir)
        from research.phase161_fade_shadow_policy_review import _guard_pass_keys

        guard_keys = _guard_pass_keys(events)
        phase161_rows.extend(
            analyze_session(
                sdir,
                exit_cfg=exit_cfg,
                cap5_keys=cap5_keys,
                guard_keys=guard_keys,
            )
        )

    g_rows = [
        r
        for r in phase161_rows
        if r.get("scenario") == "G_hybrid" and r.get("subset") == "all"
    ]
    a161_rows = {
        _trade_key(str(r["session"]), str(r["symbol"]), str(r["entry_time"])): r
        for r in phase161_rows
        if r.get("scenario") == "A_current" and r.get("subset") == "all"
    }

    improved_161 = [
        r
        for r in g_rows
        if r.get("improved_vs_actual") and _as_float(r.get("scenario_pnl")) - _as_float(r.get("actual_pnl")) > improvement_threshold
    ]

    trade_rows: list[dict[str, Any]] = []
    missing_p162 = 0

    for gr in improved_161:
        sess = str(gr["session"])
        sym = str(gr["symbol"])
        ent = str(gr["entry_time"])
        key3 = (sess, sym, ent)
        a161 = a161_rows.get(key3, {})

        p162_a = p162.get((scen_a, sess, sym, ent))
        p162_b = p162.get((scen_b, sess, sym, ent))
        if not p162_a or not p162_b:
            missing_p162 += 1
            continue

        actual_pnl = _as_float(gr.get("actual_pnl"))
        g161_pnl = _as_float(gr.get("scenario_pnl"))
        a162_pnl = _as_float(p162_a.get("realized_pnl_pct"))
        b162_pnl = _as_float(p162_b.get("realized_pnl_pct"))

        gain_161 = round(g161_pnl - actual_pnl, 4)
        gain_162 = round(b162_pnl - a162_pnl, 4)
        gain_lost = round(gain_161 - gain_162, 4)

        actual_reason = str(gr.get("actual_reason") or "")
        g161_reason = str(gr.get("scenario_reason") or "")
        a_reason = str(p162_a.get("close_reason") or "")
        b_reason = str(p162_b.get("close_reason") or "")

        loss_cat = classify_improvement_loss(
            gain_161=gain_161,
            gain_162=gain_162,
            actual_reason=actual_reason,
            g161_reason=g161_reason,
            a_reason=a_reason,
            b_reason=b_reason,
            actual_hold=_as_float(gr.get("actual_hold_sec")),
            g161_hold=_as_float(gr.get("scenario_hold_sec")),
            a_hold=_as_float(p162_a.get("hold_duration_sec")),
            b_hold=_as_float(p162_b.get("hold_duration_sec")),
            actual_pnl=actual_pnl,
            a_pnl=a162_pnl,
        )

        trade_rows.append(
            {
                "session": sess,
                "symbol": sym,
                "entry_time": ent,
                "actual_pnl": actual_pnl,
                "actual_reason": actual_reason,
                "actual_hold_sec": _as_float(gr.get("actual_hold_sec")),
                "g161_pnl": g161_pnl,
                "g161_reason": g161_reason,
                "g161_hold_sec": _as_float(gr.get("scenario_hold_sec")),
                "a162_pnl": a162_pnl,
                "a162_reason": a_reason,
                "a162_hold_sec": _as_float(p162_a.get("hold_duration_sec")),
                "b162_pnl": b162_pnl,
                "b162_reason": b_reason,
                "b162_hold_sec": _as_float(p162_b.get("hold_duration_sec")),
                "gain_161": gain_161,
                "gain_162": gain_162,
                "gain_lost": gain_lost,
                "improvement_retained": gain_lost <= improvement_threshold,
                "loss_category": loss_cat,
                "baseline_gap": round(actual_pnl - a162_pnl, 4),
                "a161_sim_pnl": _as_float(a161.get("scenario_pnl")),
                "post_exit_class": str(gr.get("post_exit_class_actual") or ""),
                "fade_watch_entered": p162_b.get("fade_watch_entered"),
                "fade_watch_exit_reason": p162_b.get("fade_watch_exit_reason"),
            }
        )

    lost_rows = [r for r in trade_rows if not r.get("improvement_retained")]
    total_gain_161 = round(sum(_as_float(r["gain_161"]) for r in trade_rows), 4)
    total_gain_162 = round(sum(_as_float(r["gain_162"]) for r in trade_rows), 4)
    total_gain_lost = round(sum(_as_float(r["gain_lost"]) for r in trade_rows), 4)

    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in lost_rows:
        by_cat[str(r.get("loss_category") or "other")].append(r)

    breakdown_rows: list[dict[str, Any]] = []
    abs_lost_sum = sum(abs(_as_float(r["gain_lost"])) for r in lost_rows) or 1.0
    for cat in LOSS_CATEGORIES:
        rows = by_cat.get(cat, [])
        if not rows:
            continue
        gl = sum(_as_float(r["gain_lost"]) for r in rows)
        breakdown_rows.append(
            {
                "loss_category": cat,
                "trade_count": len(rows),
                "sum_gain_161": round(sum(_as_float(r["gain_161"]) for r in rows), 4),
                "sum_gain_162": round(sum(_as_float(r["gain_162"]) for r in rows), 4),
                "sum_gain_lost": round(gl, 4),
                "avg_gain_lost": round(statistics.mean(_as_float(r["gain_lost"]) for r in rows), 4),
                "pct_of_abs_lost": round(sum(abs(_as_float(r["gain_lost"])) for r in rows) / abs_lost_sum, 4),
            }
        )
    breakdown_rows.sort(key=lambda x: -abs(float(x.get("sum_gain_lost") or 0)))

    # PF delta attribution: approximate contribution via sum of positive/negative pnl shifts
    all_g161_pnls = [_as_float(r.get("scenario_pnl")) for r in g_rows]
    all_b162_pnls = [_as_float(r.get("b162_pnl")) for r in trade_rows if r.get("b162_pnl") is not None]
    all_a162_pnls = [_as_float(r.get("a162_pnl")) for r in trade_rows if r.get("a162_pnl") is not None]

    pf_rank_rows: list[dict[str, Any]] = []
    for label, pnls in (
        ("phase161_G_hybrid_all", all_g161_pnls),
        ("phase162_B_hybrid_matched", all_b162_pnls),
        ("phase162_A_replay_matched", all_a162_pnls),
    ):
        pf_rank_rows.append(
            {
                "cohort": label,
                "trade_count": len(pnls),
                "pf": _profit_factor(pnls),
                "total_pnl": round(sum(pnls), 4),
                "avg_pnl": round(statistics.mean(pnls), 4) if pnls else None,
            }
        )

    for cat, rows in sorted(by_cat.items(), key=lambda x: -sum(abs(_as_float(r["gain_lost"])) for r in x[1])):
        pf_rank_rows.append(
            {
                "cohort": f"lost_improvement_{cat}",
                "trade_count": len(rows),
                "pf": _profit_factor([_as_float(r["b162_pnl"]) for r in rows]),
                "total_pnl": round(sum(_as_float(r["b162_pnl"]) for r in rows), 4),
                "sum_gain_lost": round(sum(_as_float(r["gain_lost"]) for r in rows), 4),
                "pf_delta_driver": round(
                    _safe_pf([_as_float(r["g161_pnl"]) for r in rows])
                    - _safe_pf([_as_float(r["b162_pnl"]) for r in rows]),
                    4,
                ),
            }
        )

    top100 = sorted(lost_rows, key=lambda r: _as_float(r["gain_lost"]), reverse=True)[:100]

    verdict, verdict_notes = _determine_verdict(
        breakdown_rows,
        trade_rows,
        missing_p162=missing_p162,
        total_gain_lost=total_gain_lost,
    )

    return {
        "verdict": verdict,
        "verdict_notes": verdict_notes,
        "session_count": len(session_dirs),
        "phase161_g_improved_count": len(improved_161),
        "matched_trade_count": len(trade_rows),
        "missing_phase162_count": missing_p162,
        "improvement_retained_count": sum(1 for r in trade_rows if r.get("improvement_retained")),
        "improvement_lost_count": len(lost_rows),
        "total_gain_161": total_gain_161,
        "total_gain_162": total_gain_162,
        "total_gain_lost": total_gain_lost,
        "improvement_loss_breakdown": breakdown_rows,
        "top100_lost_improvements": top100,
        "root_cause_ranking": pf_rank_rows,
        "trade_details": trade_rows,
    }


def _determine_verdict(
    breakdown: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    *,
    missing_p162: int,
    total_gain_lost: float,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not breakdown:
        return "replay_model_wrong", ["no improvement-loss rows to classify"]

    by_pct = sorted(breakdown, key=lambda x: -float(x.get("pct_of_abs_lost") or 0))
    top = by_pct[0]
    top_cat = str(top.get("loss_category") or "other")
    top_pct = float(top.get("pct_of_abs_lost") or 0)
    top2_pct = top_pct + (float(by_pct[1].get("pct_of_abs_lost") or 0) if len(by_pct) > 1 else 0.0)
    notes.append(f"dominant_loss_category={top_cat} pct_of_abs_lost={top_pct:.2%}")
    if top2_pct >= 0.65 and top_pct < 0.50:
        notes.append(f"top2_categories_share={top2_pct:.2%} -> mixed")
        return "mixed", notes

    baseline_rows = sum(1 for r in trades if r.get("loss_category") == "replay_baseline_gap")
    sim_horizon_rows = sum(1 for r in trades if r.get("loss_category") == "sim_horizon")
    session_close_rows = sum(1 for r in trades if r.get("loss_category") == "session_close")
    second_fade_rows = sum(1 for r in trades if r.get("loss_category") == "second_fade")

    if missing_p162 > 0:
        notes.append(f"missing_phase162_matches={missing_p162}")

    methodology_share = (baseline_rows + sim_horizon_rows + session_close_rows) / max(len(trades), 1)
    if methodology_share >= 0.35 or (sim_horizon_rows + session_close_rows) >= len(trades) * 0.2:
        notes.append(
            f"methodology_gap trades baseline={baseline_rows} sim_horizon={sim_horizon_rows} "
            f"session_close={session_close_rows}"
        )
        return "replay_model_wrong", notes

    mapping = {
        "session_close": "session_close_dominant",
        "sim_horizon": "session_close_dominant",
        "overlap_replaced": "overlap_dominant",
        "quality_decay": "quality_decay_dominant",
        "second_fade": "mixed",
        "replay_baseline_gap": "replay_model_wrong",
    }
    if top_pct >= 0.30 and top_cat in mapping:
        v = mapping[top_cat]
        if top_cat == "second_fade" and second_fade_rows >= len(trades) * 0.15:
            notes.append(f"second_fade also material n={second_fade_rows}")
        return v, notes

    if top_pct < 0.25:
        return "mixed", notes + ["no single category exceeds 30% of abs gain lost"]

    return "mixed", notes


def write_phase163_outputs(result: Mapping[str, Any], *, reports_dir: Path) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": reports_dir / "phase163_replay_mismatch_review.json",
        "breakdown": reports_dir / "phase163_improvement_loss_breakdown.csv",
        "top100": reports_dir / "phase163_top100_lost_improvements.csv",
        "ranking": reports_dir / "phase163_root_cause_ranking.csv",
    }
    design = {
        k: v
        for k, v in result.items()
        if k
        not in (
            "improvement_loss_breakdown",
            "top100_lost_improvements",
            "root_cause_ranking",
            "trade_details",
        )
    }
    paths["json"].write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(paths["breakdown"], result.get("improvement_loss_breakdown") or [])
    _write_csv(paths["top100"], result.get("top100_lost_improvements") or [])
    _write_csv(paths["ranking"], result.get("root_cause_ranking") or [])
    return {k: str(v) for k, v in paths.items()}
