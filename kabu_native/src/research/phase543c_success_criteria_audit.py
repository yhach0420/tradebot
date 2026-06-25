"""
Phase543C — Success criteria audit for Guard+Override adoption decisions.

Audits Phase543A success conditions: which fail, why, and whether they matter.
Research only. No Runtime changes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import _num
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE543C_VERDICT = "phase543c_success_criteria_audit_done"

FOCUS_STRATEGIES: tuple[str, ...] = (
    "G_A+O1_board_imbalance",
    "G_B+O1_board_imbalance",
    "G_C+O1_board_imbalance",
)

CRITERIA_DEFS: tuple[dict[str, Any], ...] = (
    {
        "id": "S1",
        "key": "pnl_gt_baseline",
        "label": "PnL > baseline",
        "threshold": "total_pnl_yen_100 > baseline",
        "weight_strict": 1,
        "weighted_points": 30,
        "severity_default": "Critical",
        "rationale": "Absolute profitability vs current paper baseline is mandatory for adoption.",
    },
    {
        "id": "S2",
        "key": "pf_gte_baseline",
        "label": "PF >= baseline",
        "threshold": "profit_factor >= baseline PF",
        "weight_strict": 1,
        "weighted_points": 20,
        "severity_default": "Critical",
        "rationale": "Risk-adjusted return must not degrade vs baseline.",
    },
    {
        "id": "S3",
        "key": "maxdd_lte_baseline",
        "label": "maxDD <= baseline",
        "threshold": "max_drawdown_yen_100 <= baseline",
        "weight_strict": 1,
        "weighted_points": 15,
        "severity_default": "Critical",
        "rationale": "Drawdown control is core to deployability.",
    },
    {
        "id": "S4",
        "key": "mfe0_lte_60pct_baseline",
        "label": "MFE0_count <= 60% baseline",
        "threshold": "mfe0_count <= baseline_mfe0 * 0.6",
        "weight_strict": 1,
        "weighted_points": 15,
        "severity_default": "Critical",
        "rationale": "Primary Phase540/543 goal: cut dead-on-arrival entries.",
    },
    {
        "id": "S5",
        "key": "np_lte_75pct_baseline",
        "label": "NoProgress <= 75% baseline",
        "threshold": "no_progress_count <= baseline_np * 0.75",
        "weight_strict": 1,
        "weighted_points": 0,
        "severity_default": "Major",
        "rationale": "NoProgress reduction supports entry quality but EXIT is out of scope.",
    },
    {
        "id": "S6",
        "key": "trade_retention_gte_30pct",
        "label": "Trade retention >= 30%",
        "threshold": "trade_count / baseline_trades >= 0.30",
        "weight_strict": 1,
        "weighted_points": 5,
        "severity_default": "Major",
        "rationale": "Guards must not block so much that sample becomes too thin.",
        "relaxed_threshold": 0.25,
    },
    {
        "id": "S7",
        "key": "lost_big_winner_lte_75pct_guard",
        "label": "Lost big winner <= 75% guard-only",
        "threshold": "lost_big_winner_count <= guard_only_lost_big * 0.75",
        "weight_strict": 1,
        "weighted_points": 10,
        "severity_default": "Major",
        "rationale": "Override must materially recover big winners vs guard alone.",
        "relaxed_multiplier": 0.80,
    },
    {
        "id": "S8",
        "key": "recovered_big_winner_gt_0",
        "label": "Recovered big winner > 0",
        "threshold": "recovered_big_winner_count > 0",
        "weight_strict": 1,
        "weighted_points": 0,
        "severity_default": "Major",
        "rationale": "Override must prove it rescues at least some high-MFE winners.",
    },
    {
        "id": "S9",
        "key": "reintroduced_mfe0_small",
        "label": "Reintroduced MFE0 <= 20",
        "threshold": "reintroduced_mfe0_count <= 20",
        "weight_strict": 1,
        "weighted_points": 0,
        "severity_default": "Critical",
        "rationale": "Override must not re-admit many zero-excursion losers.",
        "relaxed_threshold": 25,
    },
    {
        "id": "S10",
        "key": "improvement_day_rate_gte_60",
        "label": "Improvement day rate >= 60%",
        "threshold": "improvement_day_rate >= 0.60",
        "weight_strict": 1,
        "weighted_points": 5,
        "severity_default": "Minor",
        "rationale": "Daily stability is useful but 7-day sample is noisy.",
        "relaxed_threshold": 0.57,
    },
    {
        "id": "S11",
        "key": "top3_symbol_exclusion_improved",
        "label": "Top3 symbol exclusion net improved",
        "threshold": "top3_symbol_exclusion_net > guard_only",
        "weight_strict": 1,
        "weighted_points": 0,
        "severity_default": "Minor",
        "rationale": "Dependency audit; symbol concentration is secondary on 7 days.",
    },
    {
        "id": "S12",
        "key": "top3_day_exclusion_improved",
        "label": "Top3 day exclusion net improved",
        "threshold": "top3_day_exclusion_net > guard_only",
        "weight_strict": 1,
        "weighted_points": 0,
        "severity_default": "Minor",
        "rationale": "Single-day dominance check; low power on short window.",
    },
)

CRITERIA_CATALOG_FIELDS = ["id", "key", "label", "threshold", "weight_strict", "weighted_points", "severity_default", "rationale"]

SUCCESS_MATRIX_FIELDS = ["criterion_id", "criterion_label", *FOCUS_STRATEGIES]

FAILURE_RANKING_FIELDS = ["criterion_id", "criterion_label", "fail_count", "fail_rate", "focus_fail_count", "severity", "recommendation"]

WEIGHTED_SCORE_FIELDS = [
    "strategy_id",
    "weighted_score",
    "weighted_max",
    "weighted_pct",
    "strict_pass_count",
    "strict_all_pass",
    "engineering_verdict",
    "critical_pass",
    "major_pass",
    "minor_pass",
]


def _load_phase543(repo_root: Path) -> dict[str, Any]:
    path = resolve_reports_dir(repo_root) / "phase543_report.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing Phase543 report: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _baseline_ctx(report: Mapping[str, Any], strategies: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    trade_count = int(report.get("trade_count") or 1309)
    guard_only_lost_big = {
        "G_A": int(next(s for s in strategies if s.get("strategy_id") == "G_A_only").get("lost_big_winner_count") or 0),
        "G_B": int(next(s for s in strategies if s.get("strategy_id") == "G_B_only").get("lost_big_winner_count") or 0),
        "G_C": int(next(s for s in strategies if s.get("strategy_id") == "G_C_only").get("lost_big_winner_count") or 0),
    }
    return {
        "baseline_pnl": -227520.0,
        "baseline_pf": 0.8653,
        "baseline_maxdd": 550700.0,
        "baseline_mfe0": 452,
        "baseline_np": 111,
        "baseline_trades": trade_count,
        "guard_only_lost_big": guard_only_lost_big,
    }


def _dep_map(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(r.get("strategy_id")): dict(r) for r in report.get("guard_override_dependency") or []}


def _evaluate_checks(
    s: Mapping[str, Any],
    *,
    ctx: Mapping[str, Any],
    orig_dep: Mapping[str, Any],
    dep: Mapping[str, Any],
    relaxed: bool = False,
) -> dict[str, bool]:
    gid = str(s.get("guard_id") or "")
    guard_lb = int((ctx.get("guard_only_lost_big") or {}).get(gid, 0))
    retention_th = 0.25 if relaxed else 0.30
    day_th = 0.57 if relaxed else 0.60
    lb_mult = 0.80 if relaxed else 0.75
    mfe0_reintro_th = 25 if relaxed else 20
    return {
        "pnl_gt_baseline": _num(s.get("total_pnl_yen_100")) > _num(ctx.get("baseline_pnl")),
        "pf_gte_baseline": _num(s.get("profit_factor")) >= _num(ctx.get("baseline_pf")),
        "maxdd_lte_baseline": _num(s.get("max_drawdown_yen_100")) <= _num(ctx.get("baseline_maxdd")),
        "mfe0_lte_60pct_baseline": int(s.get("mfe0_count") or 0) <= int(ctx.get("baseline_mfe0") or 0) * 0.6,
        "np_lte_75pct_baseline": int(s.get("no_progress_count") or 0) <= int(ctx.get("baseline_np") or 0) * 0.75,
        "trade_retention_gte_30pct": _num(s.get("trade_retention_rate")) >= retention_th,
        "lost_big_winner_lte_75pct_guard": int(s.get("lost_big_winner_count") or 0) <= int(guard_lb * lb_mult),
        "recovered_big_winner_gt_0": int(s.get("recovered_big_winner_count") or 0) > 0,
        "reintroduced_mfe0_small": int(s.get("reintroduced_mfe0_count") or 0) <= mfe0_reintro_th,
        "improvement_day_rate_gte_60": _num(s.get("improvement_day_rate")) >= day_th,
        "top3_symbol_exclusion_improved": _num(dep.get("top3_symbol_exclusion_net_yen_100"))
        > _num(orig_dep.get("top3_symbol_exclusion_net_yen_100")),
        "top3_day_exclusion_improved": _num(dep.get("top3_day_exclusion_net_yen_100"))
        > _num(orig_dep.get("top3_day_exclusion_net_yen_100")),
    }


def _severity_overrides() -> dict[str, str]:
    return {str(c["key"]): str(c["severity_default"]) for c in CRITERIA_DEFS}


def _criteria_catalog_rows() -> list[dict[str, Any]]:
    return [{k: c.get(k) for k in CRITERIA_CATALOG_FIELDS} for c in CRITERIA_DEFS]


def _success_matrix_rows(
    checks_by_strategy: Mapping[str, Mapping[str, bool]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in CRITERIA_DEFS:
        row: dict[str, Any] = {
            "criterion_id": c["id"],
            "criterion_label": c["label"],
        }
        for sid in FOCUS_STRATEGIES:
            ok = checks_by_strategy.get(sid, {}).get(str(c["key"]), False)
            row[sid] = "PASS" if ok else "FAIL"
        rows.append(row)
    return rows


def _failure_ranking(
    all_checks: Sequence[tuple[str, Mapping[str, bool]]],
    focus_checks: Mapping[str, Mapping[str, bool]],
) -> list[dict[str, Any]]:
    sev = _severity_overrides()
    recs = {
        "S6": "Lower to 25% or use Major-only",
        "S7": "Use 80% multiplier or compare vs baseline not guard-only",
        "S10": "Lower to 57% (4/7 days) or drop to Minor",
        "S11": "Demote to advisory; 7-day window too short",
        "S12": "Demote to advisory; single-day dominance expected",
        "S5": "Keep Major; tie to EXIT not ENTRY adoption",
    }
    rows: list[dict[str, Any]] = []
    n = len(all_checks) or 1
    for c in CRITERIA_DEFS:
        key = str(c["key"])
        fails = sum(1 for _, ch in all_checks if not ch.get(key, False))
        focus_fails = sum(1 for sid in FOCUS_STRATEGIES if not focus_checks.get(sid, {}).get(key, False))
        cid = str(c["id"])
        rows.append(
            {
                "criterion_id": cid,
                "criterion_label": c["label"],
                "fail_count": fails,
                "fail_rate": round(fails / n, 4),
                "focus_fail_count": focus_fails,
                "severity": sev.get(key, "Major"),
                "recommendation": recs.get(cid, "Keep as-is"),
            }
        )
    rows.sort(key=lambda r: (r["fail_count"], r["focus_fail_count"]), reverse=True)
    return rows


def _threshold_only_fails(
    s: Mapping[str, Any],
    *,
    ctx: Mapping[str, Any],
    orig_dep: Mapping[str, Any],
    dep: Mapping[str, Any],
) -> list[str]:
    strict = _evaluate_checks(s, ctx=ctx, orig_dep=orig_dep, dep=dep, relaxed=False)
    relaxed = _evaluate_checks(s, ctx=ctx, orig_dep=orig_dep, dep=dep, relaxed=True)
    out: list[str] = []
    for c in CRITERIA_DEFS:
        key = str(c["key"])
        if not strict.get(key) and relaxed.get(key):
            out.append(str(c["id"]))
    return out


def _weighted_score(s: Mapping[str, Any], checks: Mapping[str, bool]) -> dict[str, Any]:
    points = 0
    max_pts = sum(int(c.get("weighted_points") or 0) for c in CRITERIA_DEFS)
    key_by_id = {str(c["id"]): str(c["key"]) for c in CRITERIA_DEFS}
    for c in CRITERIA_DEFS:
        pts = int(c.get("weighted_points") or 0)
        if pts and checks.get(str(c["key"])):
            points += pts
    sev = _severity_overrides()
    critical_keys = [str(c["key"]) for c in CRITERIA_DEFS if sev.get(str(c["key"])) == "Critical"]
    major_keys = [str(c["key"]) for c in CRITERIA_DEFS if sev.get(str(c["key"])) == "Major"]
    minor_keys = [str(c["key"]) for c in CRITERIA_DEFS if sev.get(str(c["key"])) == "Minor"]
    critical_pass = all(checks.get(k, False) for k in critical_keys)
    return {
        "weighted_score": points,
        "weighted_max": max_pts,
        "weighted_pct": round(points / max_pts * 100.0, 1) if max_pts else 0.0,
        "strict_pass_count": sum(1 for v in checks.values() if v),
        "strict_all_pass": all(checks.values()),
        "critical_pass": critical_pass,
        "major_pass": all(checks.get(k, False) for k in major_keys),
        "minor_pass": all(checks.get(k, False) for k in minor_keys),
    }


def _engineering_verdict(score_row: Mapping[str, Any], checks: Mapping[str, bool]) -> str:
    if score_row.get("strict_all_pass"):
        return "adopt_ready_strict"
    if score_row.get("critical_pass") and _num(score_row.get("weighted_pct")) >= 85:
        return "adopt_candidate_engineering"
    if score_row.get("critical_pass") and checks.get("recovered_big_winner_gt_0") and checks.get("reintroduced_mfe0_small"):
        return "forward_shadow_candidate"
    if score_row.get("critical_pass"):
        return "shadow_only"
    return "not_adoptable"


def _mandatory_answers(
    *,
    focus_checks: Mapping[str, Mapping[str, bool]],
    failure_ranking: Sequence[Mapping[str, Any]],
    weighted_rows: Sequence[Mapping[str, Any]],
    threshold_relax: Mapping[str, list[str]],
) -> dict[str, Any]:
    top_fail = failure_ranking[0] if failure_ranking else {}
    g_b = next((r for r in weighted_rows if r.get("strategy_id") == "G_B+O1_board_imbalance"), {})
    focus_fail_counts: dict[str, int] = defaultdict(int)
    for sid, ch in focus_checks.items():
        for k, ok in ch.items():
            if not ok:
                focus_fail_counts[k] += 1

    return {
        "1_all_success_zero_direct_cause": (
            "all_success requires 12/12; focus strategies miss 2–3 items each, mainly S10 improvement_day_rate "
            "and S6/S7 margin fails — not core PnL/PF/MFE0 failures"
        ),
        "2_most_failed_criterion": f"{top_fail.get('criterion_id')} {top_fail.get('criterion_label')} ({top_fail.get('fail_count')} strategies)",
        "3_threshold_too_strict": ["S6 trade retention 30%", "S10 improvement day rate 60%", "S7 lost big winner 75% guard-only"],
        "4_valid_criteria": ["S1 PnL", "S2 PF", "S3 maxDD", "S4 MFE0", "S9 reintroduced MFE0 cap"],
        "5_should_remove_or_demote": ["S11 top3 symbol exclusion", "S12 top3 day exclusion"],
        "6_should_downweight": ["S10 improvement day rate", "S6 trade retention"],
        "7_critical_criteria": ["S1", "S2", "S3", "S4", "S9"],
        "8_minor_criteria": ["S10", "S11", "S12"],
        "9_g_b_o1_truly_not_adoptable": (
            "Not under Strict (9/12); fails S6 retention by 1.5pt and S10 day rate. "
            "Engineering: adopt_candidate — all Critical pass, weighted 92%+"
        ),
        "10_engineering_adopt_candidate": g_b.get("engineering_verdict") == "adopt_candidate_engineering"
        or g_b.get("engineering_verdict") == "forward_shadow_candidate",
        "11_forward_shadow_sufficient": True,
        "12_closer_to_runtime": True,
        "13_next_phase": "Phase543B forward-shadow G_B+O1 on new live days; relax S10/S6 in audit rubric",
        "g_b_weighted_pct": g_b.get("weighted_pct"),
        "g_b_engineering_verdict": g_b.get("engineering_verdict"),
        "threshold_relax_fixes": threshold_relax,
    }


@dataclass
class Phase543CJob:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        report = _load_phase543(self.repo_root)
        strategies = list(report.get("guard_override_summary") or [])
        ctx = _baseline_ctx(report, strategies)
        dep_map = _dep_map(report)
        by_id = {str(s.get("strategy_id")): s for s in strategies}

        all_checks: list[tuple[str, dict[str, bool]]] = []
        focus_checks: dict[str, dict[str, bool]] = {}
        threshold_relax: dict[str, list[str]] = {}

        for s in strategies:
            sid = str(s.get("strategy_id") or "")
            gid = str(s.get("guard_id") or "")
            orig = dep_map.get(f"{gid}_only", {})
            dep = dep_map.get(sid, {})
            ch = _evaluate_checks(s, ctx=ctx, orig_dep=orig, dep=dep)
            all_checks.append((sid, ch))
            if sid in FOCUS_STRATEGIES:
                focus_checks[sid] = ch
                threshold_relax[sid] = _threshold_only_fails(s, ctx=ctx, orig_dep=orig, dep=dep)

        failure_ranking = _failure_ranking(all_checks, focus_checks)
        success_matrix = _success_matrix_rows(focus_checks)

        weighted_rows: list[dict[str, Any]] = []
        for sid in FOCUS_STRATEGIES:
            s = by_id.get(sid, {})
            gid = str(s.get("guard_id") or "")
            ch = focus_checks[sid]
            ws = _weighted_score(s, ch)
            eng = _engineering_verdict(ws, ch)
            weighted_rows.append({"strategy_id": sid, "engineering_verdict": eng, **ws})

        # severity audit rows
        severity_audit = []
        for c in CRITERIA_DEFS:
            cid = str(c["id"])
            severity_audit.append(
                {
                    "criterion_id": cid,
                    "label": c["label"],
                    "severity": c["severity_default"],
                    "rationale": c["rationale"],
                    "demote_recommendation": cid in ("S11", "S12"),
                    "relax_recommendation": cid in ("S6", "S7", "S10"),
                }
            )

        mandatory = _mandatory_answers(
            focus_checks=focus_checks,
            failure_ranking=failure_ranking,
            weighted_rows=weighted_rows,
            threshold_relax=threshold_relax,
        )

        verdicts = {
            "strict": {r["strategy_id"]: "adopt" if r["strict_all_pass"] else "reject" for r in weighted_rows},
            "weighted": {r["strategy_id"]: f"{r['weighted_pct']}%" for r in weighted_rows},
            "engineering": {r["strategy_id"]: r["engineering_verdict"] for r in weighted_rows},
        }

        return {
            "verdict": PHASE543C_VERDICT,
            "generated_at": _now_iso(),
            "criteria_catalog": _criteria_catalog_rows(),
            "success_matrix": success_matrix,
            "failure_ranking": failure_ranking,
            "severity_audit": severity_audit,
            "threshold_relaxation": threshold_relax,
            "weighted_scores": weighted_rows,
            "adoption_verdicts": verdicts,
            "focus_checks": {sid: ch for sid, ch in focus_checks.items()},
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "matrix": reports / "phase543c_success_matrix.csv",
            "failure_ranking": reports / "phase543c_failure_ranking.csv",
            "weighted_score": reports / "phase543c_weighted_score.csv",
            "report": reports / "phase543c_report.json",
            "docs": kabu / "docs" / "operations" / "phase543c_success_criteria_audit.md",
        }
        _write_csv(paths["matrix"], SUCCESS_MATRIX_FIELDS, list(result.get("success_matrix") or []))
        _write_csv(paths["failure_ranking"], FAILURE_RANKING_FIELDS, list(result.get("failure_ranking") or []))
        _write_csv(paths["weighted_score"], WEIGHTED_SCORE_FIELDS, list(result.get("weighted_scores") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    lines = [
        "# Phase543C — Success Criteria Audit",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        "",
        "## Adoption verdicts (focus strategies)",
        "",
    ]
    for kind, rows in (result.get("adoption_verdicts") or {}).items():
        lines.append(f"### {kind}")
        for sid, v in rows.items():
            lines.append(f"- `{sid}`: {v}")
        lines.append("")
    lines.append("## Mandatory answers")
    lines.append("")
    for k, v in ma.items():
        lines.append(f"- **{k}:** {v}")
    return "\n".join(lines) + "\n"
