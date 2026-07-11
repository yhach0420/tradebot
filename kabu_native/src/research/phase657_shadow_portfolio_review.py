"""
Phase657: Shadow Portfolio Final Review (research only).

Cross-cuts Runtime, Forward, and Research shadows across all available session data
to produce ADOPT / KEEP / MERGE / REMOVE decisions. No ENTRY/EXIT/YAML/runtime changes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase634_pbv2_only_rise5_full_period import PRE625_CUTOFF
from research.phase652_shadow_registry import (
    PRODUCTION_YAML,
    ShadowDef,
    _build_dashboard,
    _build_registry_rows,
    _discover_session_summaries,
    _extract_dashboard_row,
    _load_yaml_config,
    _parse_ops_docs,
    _registry_definitions,
)
from research.structural_trade_normalize import resolve_kabu_root

PHASE657_VERDICT = "phase657_shadow_portfolio_review_done"
REPORT_DIR_NAME = "phase657_shadow_portfolio_review"
NATIVE_ROOT = Path(__file__).resolve().parents[2]

MERGE_INTO: dict[str, str] = {
    "loss_acceleration_exit": "realtime_board_exit_shadow",
    "board_collapse_profit_exit": "realtime_board_exit_shadow",
    "profit_protect_exit": "realtime_board_exit_shadow",
    "phase634_pbv2_rise5_full_period": "pbv2_rise5_shadow",
    "phase649_flat_band_guard": "pbv2_flat_band_shadow",
    "phase648_rise5_rise10_analysis": "pbv2_flat_band_shadow",
}

RESEARCH_ONLY_IDS = frozenset(
    {
        "phase632_pbv2_profit_filter",
        "phase633_combo_soft_robustness",
        "phase634_pbv2_rise5_full_period",
        "phase647_momentum_low_trend",
        "phase648_rise5_rise10_analysis",
        "phase649_flat_band_guard",
        "phase643_position_sizing_shadow",
        "phase651_scan_ranking_audit",
        "phase655_no_progress_early_exit",
        "phase656_winner_favor_hybrid",
        "phase654_loss_attribution",
    }
)

HIGH_CPU_SHADOWS = frozenset({"realtime_board_exit_shadow"})
LOW_DISCORD_VALUE = frozenset(
    {
        "loss_acceleration_exit",
        "board_collapse_profit_exit",
        "profit_protect_exit",
        "volume_gate_relaxation_shadow",
        "low_liquidity_shadow",
    }
)

SCORECARD_COLUMNS = [
    "shadow_id",
    "name",
    "category",
    "layer",
    "entry_or_exit",
    "final_decision",
    "total_score",
    "score_expected_value",
    "score_stability",
    "score_reproducibility",
    "score_runtime_load",
    "score_maintainability",
    "score_side_effects",
    "score_adopt_ease",
    "session_count",
    "day_count",
    "target_entries",
    "block_count",
    "net_effect_yen",
    "blocked_winners",
    "blocked_losers",
    "pre625_net_yen",
    "post625_net_yen",
    "notes",
]


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]], columns: Optional[Sequence[str]] = None) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    fields = list(columns) if columns else []
    if not fields:
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fields.append(key)
    _write_csv(path, fields, rows)


def _day_iso(day_key: str) -> str:
    d = str(day_key)
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


def _load_summary(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _discover_summaries_extended() -> list[tuple[str, str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = []
    seen: set[str] = set()
    for day, session, sp in _discover_session_summaries():
        key = f"{day}/{session}"
        if key in seen:
            continue
        sm = _load_summary(sp)
        if sm:
            seen.add(key)
            out.append((day, session, sm))
            continue
        sess_dir = sp.parent
        for alt in (
            "small_paper_summary_am.json",
            "small_paper_am_summary.json",
            "small_paper_summary_pm.json",
            "small_paper_pm_summary.json",
        ):
            ap = sess_dir / alt
            if ap.is_file():
                sm2 = _load_summary(ap)
                if sm2:
                    seen.add(key)
                    out.append((day, session, sm2))
                    break
    return out


def _research_shadow_defs() -> list[ShadowDef]:
    return [
        ShadowDef(
            shadow_id="phase651_scan_ranking_audit",
            phase="651",
            name="Scan accept ranking audit (max_entries_per_scan)",
            category="research_counterfactual",
            runtime_or_research="research",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            implementation_files=["src/research/phase651_scan_accept_ranking_audit.py"],
            status="research_only",
            decision="observe",
            recommended_next_action="ranking shadow only",
        ),
        ShadowDef(
            shadow_id="phase655_no_progress_early_exit",
            phase="655",
            name="No-progress early-exit counterfactual",
            category="research_counterfactual",
            runtime_or_research="research",
            entry_or_exit="exit",
            target_pool="ALL",
            implementation_files=["src/research/phase655_no_progress_entry_quality.py"],
            status="research_only",
            decision="hold",
            recommended_next_action="shadow-only; do not mainline EXIT",
        ),
        ShadowDef(
            shadow_id="phase656_winner_favor_hybrid",
            phase="656",
            name="Big-winner favor + flat-band hybrid filter",
            category="research_counterfactual",
            runtime_or_research="research",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            implementation_files=["src/research/phase656_winner_attribution.py"],
            status="research_only",
            decision="hold",
            recommended_next_action="ENTRY block shadow forward test",
        ),
        ShadowDef(
            shadow_id="phase654_loss_attribution",
            phase="654",
            name="7/7 loss attribution (flat-band vs rise5)",
            category="research_counterfactual",
            runtime_or_research="research",
            entry_or_exit="entry",
            target_pool="PBV2_ONLY",
            implementation_files=["src/research/phase654_20260707_loss_attribution.py"],
            status="research_only",
            decision="observe",
            recommended_next_action="inform flat-band adoption timing",
        ),
    ]


def _load_external_research() -> dict[str, Any]:
    reports: dict[str, Any] = {}
    for name, rel in (
        ("phase654", "phase654_20260707_loss_attribution/phase654_report.json"),
        ("phase655", "phase655_no_progress_analysis/phase655_report.json"),
        ("phase656", "phase656_winner_attribution/phase656_report.json"),
        ("phase652", "phase652_shadow_registry/phase652_report.json"),
    ):
        path = NATIVE_ROOT / "results" / "reports" / rel
        if path.is_file():
            try:
                reports[name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
    return reports


def _per_session_metrics(
    defs: Sequence[ShadowDef],
    summaries: Sequence[tuple[str, str, Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    by_shadow: dict[str, dict[str, Any]] = {}

    for sd in defs:
        by_shadow[sd.shadow_id] = {
            "shadow_id": sd.shadow_id,
            "name": sd.name,
            "category": sd.category,
            "layer": sd.runtime_or_research,
            "entry_or_exit": sd.entry_or_exit,
            "sessions": set(),
            "days": set(),
            "block_count": 0.0,
            "target_count": 0.0,
            "net_effect_yen": 0.0,
            "blocked_winners": 0.0,
            "blocked_losers": 0.0,
            "daily_net": defaultdict(float),
            "pre625_net": 0.0,
            "post625_net": 0.0,
            "am_net": 0.0,
            "pm_net": 0.0,
            "per_day_effects": [],
        }

    for day, session, sm in summaries:
        day_iso = _day_iso(day)
        period = "post625" if day_iso >= PRE625_CUTOFF else "pre625"
        accepted = int(sm.get("accepted_count") or 0)
        am_pm = sm.get("am_pm_session") if isinstance(sm.get("am_pm_session"), Mapping) else {}
        sess_kind = str(am_pm.get("kind") or "").lower()

        for sd in defs:
            if sd.runtime_or_research == "research" and sd.shadow_id not in RESEARCH_ONLY_IDS:
                continue
            ex = _extract_dashboard_row(sd, sm)
            has_data = any(
                ex.get(k) is not None for k in ("block_count", "target_count", "net_effect", "delta_yen")
            ) or ex.get("enabled_in_summary")
            if not has_data and sd.runtime_or_research != "research":
                continue

            net = float(ex.get("net_effect") or ex.get("delta_yen") or 0.0)
            blocks = float(ex.get("block_count") or 0.0)
            targets = float(ex.get("target_count") or 0.0)
            if targets == 0 and accepted > 0 and sd.entry_or_exit == "entry":
                targets = float(accepted)

            agg = by_shadow[sd.shadow_id]
            agg["sessions"].add(session)
            agg["days"].add(day)
            agg["block_count"] += blocks
            agg["target_count"] += targets
            agg["net_effect_yen"] += net
            agg["blocked_winners"] += float(ex.get("blocked_winners") or 0.0)
            agg["blocked_losers"] += float(ex.get("blocked_losers") or 0.0)
            agg["daily_net"][day] += net
            if period == "pre625":
                agg["pre625_net"] += net
            else:
                agg["post625_net"] += net
            if sess_kind == "am":
                agg["am_net"] += net
            elif sess_kind == "pm":
                agg["pm_net"] += net

            rows.append(
                {
                    "day": day,
                    "day_iso": day_iso,
                    "period": period,
                    "session": session,
                    "session_kind": sess_kind,
                    "shadow_id": sd.shadow_id,
                    "accepted_count": accepted,
                    "block_count": blocks,
                    "target_count": targets,
                    "net_effect_yen": net,
                    "blocked_winners": ex.get("blocked_winners"),
                    "blocked_losers": ex.get("blocked_losers"),
                    "delta_yen": ex.get("delta_yen"),
                }
            )

    for agg in by_shadow.values():
        agg["per_day_effects"] = list(agg["daily_net"].values())
        agg["session_count"] = len(agg["sessions"])
        agg["day_count"] = len(agg["days"])
        del agg["sessions"]
        del agg["days"]
        del agg["daily_net"]

    return rows, by_shadow


def _inject_research_metrics(by_shadow: dict[str, dict[str, Any]], external: Mapping[str, Any]) -> None:
    p654 = external.get("phase654", {}).get("mandatory_answers", {})
    p655 = external.get("phase655", {}).get("mandatory_answers", {})
    p656 = external.get("phase656", {}).get("mandatory_answers", {})

    if p654:
        fb = p654.get("1_flat_band_prevented_how_much", {})
        agg = by_shadow.setdefault("pbv2_flat_band_shadow", {})
        agg["research_note"] = str(fb.get("interpretation", ""))
        hint = float(fb.get("combined_delta_yen_100") or 0.0)
        agg["research_net_effect_hint"] = hint
        if int(agg.get("session_count") or 0) == 0 and hint:
            agg["net_effect_yen"] = hint
    if p655:
        cf = p655.get("6_counterfactual_improves", {})
        by_shadow.setdefault("phase655_no_progress_early_exit", {})["research_net_effect_hint"] = float(
            cf.get("delta_pnl_yen_100") or 0.0
        )
        by_shadow.setdefault("phase655_no_progress_early_exit", {})["research_verdict"] = p655.get(
            "10_final_verdict"
        )
    if p656:
        cf6 = p656.get("6_promising_counterfactual_variant", {})
        by_shadow.setdefault("phase656_winner_favor_hybrid", {})["research_net_effect_hint"] = float(
            cf6.get("delta_pnl_yen_100") or 0.0
        )
        by_shadow.setdefault("phase656_winner_favor_hybrid", {})["research_verdict"] = p656.get("9_final_verdict")


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _score_shadow(sd: ShadowDef, agg: Mapping[str, Any]) -> dict[str, float]:
    net = float(agg.get("net_effect_yen") or agg.get("research_net_effect_hint") or 0.0)
    sessions = int(agg.get("session_count") or 0)
    bw = float(agg.get("blocked_winners") or 0.0)
    bl = float(agg.get("blocked_losers") or 0.0)
    day_effects = list(agg.get("per_day_effects") or [])
    pre = float(agg.get("pre625_net") or 0.0)
    post = float(agg.get("post625_net") or 0.0)

    # Expected value (25)
    ev = _clamp(12.5 + net / 20000.0, 0, 25) if net > 0 else _clamp(12.5 + net / 40000.0, 0, 25)

    # Stability (20): share of positive session-days
    if day_effects:
        pos_days = sum(1 for x in day_effects if x > 0)
        stability = _clamp(20.0 * pos_days / len(day_effects))
    elif sessions > 0:
        stability = 10.0
    else:
        stability = 0.0

    # Reproducibility (15): pre/post same sign or research-only
    if sd.runtime_or_research == "research":
        repro = 12.0
    elif pre == 0 and post == 0:
        repro = 5.0
    elif pre > 0 and post > 0:
        repro = 15.0
    elif pre < 0 and post < 0:
        repro = 3.0
    else:
        repro = 7.0

    # Runtime load (10) — higher score = lighter
    if sd.shadow_id in HIGH_CPU_SHADOWS:
        load = 3.0
    elif sd.category == "forward_shadow":
        load = 8.0
    elif sd.category == "extension_shadow":
        load = 6.0
    elif sd.entry_or_exit == "exit":
        load = 7.0
    else:
        load = 9.0

    # Maintainability (10)
    maint = 8.0
    if sd.discord_section and "none" not in str(sd.discord_section).lower():
        maint += 1.0
    if sd.shadow_id in LOW_DISCORD_VALUE:
        maint -= 3.0
    if sd.runtime_or_research == "research":
        maint = 9.0
    maint = _clamp(maint, 0, 10)

    # Side effects (10) — fewer blocked winners is better
    if bw + bl > 0:
        side = _clamp(10.0 * (bl / (bw + bl)), 0, 10)
    else:
        side = 8.0 if sd.runtime_or_research == "research" else 6.0

    # Adopt ease (10)
    adopt = 5.0
    if sd.adopted_mainline:
        adopt = 10.0
    elif sd.mainline_effect and "logging_only" in sd.mainline_effect:
        adopt += 2.0
    if sd.shadow_id == "pbv2_flat_band_shadow" and net > 0:
        adopt += 2.0
    if sd.shadow_id == "pbv2_rise5_shadow" and net <= 0:
        adopt -= 2.0
    if sd.shadow_id == "boundary_forward_shadow":
        adopt = 1.0
    adopt = _clamp(adopt, 0, 10)

    total = round(ev + stability + repro + load + maint + side + adopt, 2)
    return {
        "score_expected_value": round(ev, 2),
        "score_stability": round(stability, 2),
        "score_reproducibility": round(repro, 2),
        "score_runtime_load": round(load, 2),
        "score_maintainability": round(maint, 2),
        "score_side_effects": round(side, 2),
        "score_adopt_ease": round(adopt, 2),
        "total_score": total,
    }


def _decide(sd: ShadowDef, agg: Mapping[str, Any], scores: Mapping[str, float]) -> tuple[str, str]:
    net = float(agg.get("net_effect_yen") or agg.get("research_net_effect_hint") or 0.0)
    sessions = int(agg.get("session_count") or 0)
    bw = float(agg.get("blocked_winners") or 0.0)
    total = float(scores.get("total_score") or 0.0)

    if sd.adopted_mainline:
        return "ADOPT", "Already production mainline (board-dynamic trailing policy)"
    if sd.shadow_id in MERGE_INTO:
        return "MERGE", f"Consolidate into {MERGE_INTO[sd.shadow_id]}"
    if sd.status == "disabled" or sd.deprecated_candidate:
        return "REMOVE", "Disabled or deprecated in registry"
    if sd.shadow_id == "boundary_forward_shadow":
        return "REMOVE", "Phase409 adoption_review_allowed=False; logging only"
    if sd.runtime_or_research == "research":
        rv = str(agg.get("research_verdict") or "HOLD")
        if rv == "REJECT":
            return "REMOVE", "Research verdict REJECT"
        return "KEEP", "Research-only counterfactual; no runtime hook"

    if sd.shadow_id == "pbv2_flat_band_shadow":
        hint = float(agg.get("research_net_effect_hint") or 0.0)
        effective_net = net if sessions > 0 else hint
        if effective_net > 0 and (sessions >= 5 or hint >= 20000):
            return "ADOPT", "Promotion candidate: positive net_effect (runtime + Phase654); ENTRY guard shadow trial next"
        return "KEEP", "Mixed AM/PM; continue forward sessions before mainline"

    if sd.shadow_id == "pbv2_rise5_shadow":
        if net > 50000 and sessions >= 10:
            return "ADOPT", "Sustained positive net_effect"
        return "KEEP", "Low or zero net_effect on recent days; keep logging"

    if sd.shadow_id == "exit_shadow_monitor_t2_t3":
        if net > 0:
            return "KEEP", "Positive EXIT delta; validate T3 before ADOPT"
        return "KEEP", "Observe T2/T3 EXIT overlays"

    if sd.shadow_id == "realtime_board_exit_shadow":
        return "KEEP", "High CPU; push-live validation required"

    if sd.category == "forward_shadow":
        if sessions >= 10 and total >= 55:
            return "KEEP", "Sufficient forward days; not adoption-ready"
        return "KEEP", "Continue forward collection"

    if sd.category == "extension_shadow":
        if sessions == 0:
            return "REMOVE", "No session data; extension noise"
        return "KEEP", "Extension research; low risk log-only"

    if total >= 70 and net > 0 and bw < 30:
        return "ADOPT", "High portfolio score with positive net_effect"
    if total < 35 and sessions == 0:
        return "REMOVE", "No measurable sessions"
    return "KEEP", "Default observe"


def _architecture_md(decisions: Sequence[Mapping[str, Any]]) -> str:
    adopt = [d["shadow_id"] for d in decisions if d.get("final_decision") == "ADOPT"]
    keep = [d["shadow_id"] for d in decisions if d.get("final_decision") == "KEEP"]
    merge = [d["shadow_id"] for d in decisions if d.get("final_decision") == "MERGE"]
    remove = [d["shadow_id"] for d in decisions if d.get("final_decision") == "REMOVE"]

    return f"""# Phase657 Shadow Architecture (post-review)

Generated: {_now_iso()}

## Layer diagram

```mermaid
flowchart TB
  subgraph production [Production Mainline]
    PBv2[PBv2 ENTRY]
    OR[OR Overlay]
    EXIT[Structural EXIT]
    BDT[Board-Dynamic Trailing]
  end

  subgraph adopt_lane [ADOPT Candidates]
    FB[Flat-band Guard]
    BDT
  end

  subgraph runtime_entry [Runtime ENTRY Shadows - KEEP]
    R5[Rise5 Shadow]
    PM[Pullback Misread Shadow]
    VG[Volume Gate Relaxation]
    EXT[Extension ENTRY Shadows]
  end

  subgraph runtime_exit [Runtime EXIT Shadows - KEEP]
    T23[EXIT T2/T3 Monitor]
    RB[Realtime Board EXIT Bundle]
  end

  subgraph forward [Forward Shadows - KEEP]
    FWD[Sector Heat / Risk Sizing / Equity Stop / Post Entry / ...]
  end

  subgraph research [Research Only]
    RES[655 No Progress / 656 Winner / 651 Ranking / 649 Counterfactuals]
  end

  PBv2 --> R5
  PBv2 --> FB
  PBv2 --> PM
  EXIT --> BDT
  EXIT --> T23
  EXIT --> RB
  adopt_lane --> PBv2
  runtime_entry --> PBv2
  runtime_exit --> EXIT
  forward -.-> production
  research -.-> adopt_lane
```

## Decision counts

| Decision | Count |
|----------|------:|
| ADOPT | {len(adopt)} |
| KEEP | {len(keep)} |
| MERGE | {len(merge)} |
| REMOVE | {len(remove)} |

## ADOPT
{chr(10).join(f'- {x}' for x in adopt) or '- (none new beyond production)'}

## KEEP (runtime)
{chr(10).join(f'- {x}' for x in keep[:25])}
{'- ...' if len(keep) > 25 else ''}

## MERGE
{chr(10).join(f'- {x} -> {MERGE_INTO.get(x, "?")}' for x in merge) or '- (none)'}

## REMOVE
{chr(10).join(f'- {x}' for x in remove) or '- (none)'}
"""


def run_phase657(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root or NATIVE_ROOT)
    cfg = _load_yaml_config(PRODUCTION_YAML if PRODUCTION_YAML.is_file() else kabu / "configs" / PRODUCTION_YAML.name)
    ops = _parse_ops_docs()
    defs = _registry_definitions() + _research_shadow_defs()
    summaries = _discover_summaries_extended()
    external = _load_external_research()

    registry_rows = _build_registry_rows(defs, cfg, ops, summaries)
    dashboard = _build_dashboard(defs, summaries)
    per_session, by_shadow = _per_session_metrics(defs, summaries)
    _inject_research_metrics(by_shadow, external)

    scorecards: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    for sd in defs:
        agg = by_shadow.get(sd.shadow_id, {})
        scores = _score_shadow(sd, agg)
        decision, note = _decide(sd, agg, scores)
        row = {
            "shadow_id": sd.shadow_id,
            "name": sd.name,
            "category": sd.category,
            "layer": sd.runtime_or_research,
            "entry_or_exit": sd.entry_or_exit,
            "final_decision": decision,
            "notes": note,
            "session_count": agg.get("session_count", 0),
            "day_count": agg.get("day_count", 0),
            "target_entries": round(float(agg.get("target_count") or 0.0), 1),
            "block_count": round(float(agg.get("block_count") or 0.0), 1),
            "net_effect_yen": round(float(agg.get("net_effect_yen") or 0.0), 2),
            "blocked_winners": round(float(agg.get("blocked_winners") or 0.0), 1),
            "blocked_losers": round(float(agg.get("blocked_losers") or 0.0), 1),
            "pre625_net_yen": round(float(agg.get("pre625_net") or 0.0), 2),
            "post625_net_yen": round(float(agg.get("post625_net") or 0.0), 2),
            "am_net_yen": round(float(agg.get("am_net") or 0.0), 2),
            "pm_net_yen": round(float(agg.get("pm_net") or 0.0), 2),
            **scores,
        }
        scorecards.append(row)
        decisions.append(row)

    scorecards.sort(key=lambda r: float(r.get("total_score") or 0.0), reverse=True)
    for i, row in enumerate(scorecards, start=1):
        row["rank"] = i

    adopt_list = [r for r in scorecards if r["final_decision"] == "ADOPT"]
    adopt_promotion = [
        r["shadow_id"]
        for r in adopt_list
        if r["shadow_id"] not in ("board_dynamic_trailing_shadow", "entry_price_risk_guard_shadow", "entry_expectancy_score_shadow")
    ]
    keep_list = [r for r in scorecards if r["final_decision"] == "KEEP"]
    merge_list = [r for r in scorecards if r["final_decision"] == "MERGE"]
    remove_list = [r for r in scorecards if r["final_decision"] == "REMOVE"]

    runtime_keep = [
        r["shadow_id"]
        for r in keep_list
        if r.get("layer") == "runtime" and r.get("category") in ("entry_runtime", "exit_runtime", "extension_shadow")
    ]
    forward_keep = [r["shadow_id"] for r in keep_list if r.get("category") == "forward_shadow"]
    research_end = [
        r["shadow_id"]
        for r in scorecards
        if r.get("layer") == "research" or r["shadow_id"] in RESEARCH_ONLY_IDS
    ]

    pre_live = [
        "pbv2_flat_band_shadow",
        "pbv2_rise5_shadow",
        "exit_shadow_monitor_t2_t3",
        "post_entry_forward_shadow",
        "volume_gate_relaxation_shadow",
    ]
    post_live = [
        "board_dynamic_trailing_shadow",
        "sector_heat_forward_shadow",
        "risk_sizing_forward_shadow",
        "equity_dynamic_stop_shadow",
        "pullback_misread_guard_shadow",
    ]

    maturity = "maturing"
    if len(adopt_list) >= 2 and float(dashboard.get("session_count") or 0) >= 20:
        maturity = "ready_for_selective_mainline_promotion"
    if len(remove_list) > len(adopt_list) * 3:
        maturity = "consolidation_needed"

    mandatory = {
        "1_adopt_top10": [r["shadow_id"] for r in adopt_list[:10]],
        "1b_new_mainline_promotion_candidates": adopt_promotion,
        "2_keep_shadows": [r["shadow_id"] for r in keep_list],
        "3_remove_shadows": [r["shadow_id"] for r in remove_list],
        "4_merge_shadows": [{"from": r["shadow_id"], "into": MERGE_INTO.get(r["shadow_id"], "")} for r in merge_list],
        "5_runtime_shadows_recommended_count": len(runtime_keep) + len(adopt_list),
        "6_forward_shadows_recommended_count": len(forward_keep),
        "7_research_only_terminal": research_end,
        "8_architecture_summary": {
            "production_exit": ["board_dynamic_trailing_shadow"],
            "entry_adopt_candidate": "pbv2_flat_band_shadow",
            "entry_keep_logging": ["pbv2_rise5_shadow", "pullback_misread_guard_shadow", "volume_gate_relaxation_shadow"],
            "exit_keep_logging": ["exit_shadow_monitor_t2_t3", "realtime_board_exit_shadow"],
            "forward_collect": forward_keep,
            "research_terminal": research_end,
        },
        "9_shadows_needed_before_live_orders": pre_live,
        "10_shadows_keep_after_live_orders": post_live,
        "11_portfolio_maturity": maturity,
        "12_shadow_development_can_pause": maturity in ("maturing", "ready_for_selective_mainline_promotion"),
        "12_note": "Pause new shadow creation; focus forward validation of ADOPT candidates and MERGE cleanup",
        "dataset": {
            "session_count": len(summaries),
            "trading_day_count": len({d for d, _, _ in summaries}),
            "total_accepted_entries": sum(int(sm.get("accepted_count") or 0) for _, _, sm in summaries),
            "registry_shadow_count": len(defs),
        },
    }

    runtime_overview = [
        r
        for r in scorecards
        if r.get("category") in ("entry_runtime", "exit_runtime", "extension_shadow")
        or r.get("layer") == "runtime"
    ]
    forward_overview = [r for r in scorecards if r.get("category") == "forward_shadow"]

    return {
        "phase": "657",
        "verdict": PHASE657_VERDICT,
        "generated_at": _now_iso(),
        "mandatory_answers": mandatory,
        "dashboard_session_count": dashboard.get("session_count"),
        "outputs": {
            "scorecard": scorecards,
            "ranking": scorecards,
            "adopt_keep_remove": decisions,
            "runtime_overview": runtime_overview,
            "forward_overview": forward_overview,
            "per_session_sample_count": len(per_session),
            "architecture_md": _architecture_md(decisions),
        },
    }


@dataclass
class Phase657Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase657(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        out_dir = kabu / "results" / "reports" / REPORT_DIR_NAME
        out_dir.mkdir(parents=True, exist_ok=True)
        outputs = result.get("outputs") or {}
        paths = {
            "report": out_dir / "phase657_report.json",
            "scorecard": out_dir / "phase657_shadow_scorecard.csv",
            "ranking": out_dir / "phase657_shadow_ranking.csv",
            "adopt_keep_remove": out_dir / "phase657_adopt_keep_remove.csv",
            "runtime_overview": out_dir / "phase657_runtime_overview.csv",
            "forward_overview": out_dir / "phase657_forward_overview.csv",
            "architecture": out_dir / "phase657_architecture.md",
        }
        _write_rows(paths["scorecard"], outputs.get("scorecard") or [], SCORECARD_COLUMNS)
        _write_rows(paths["ranking"], outputs.get("ranking") or [])
        _write_rows(paths["adopt_keep_remove"], outputs.get("adopt_keep_remove") or [])
        _write_rows(paths["runtime_overview"], outputs.get("runtime_overview") or [])
        _write_rows(paths["forward_overview"], outputs.get("forward_overview") or [])
        paths["architecture"].write_text(str(outputs.get("architecture_md") or ""), encoding="utf-8")
        report_payload = {
            "phase": result.get("phase"),
            "generated_at": result.get("generated_at"),
            "verdict": result.get("verdict"),
            "mandatory_answers": result.get("mandatory_answers"),
            "dashboard_session_count": result.get("dashboard_session_count"),
            "artifact_paths": {
                k: str(v.relative_to(kabu)) if v.is_relative_to(kabu) else str(v) for k, v in paths.items()
            },
        }
        paths["report"].write_text(json.dumps(report_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return paths
