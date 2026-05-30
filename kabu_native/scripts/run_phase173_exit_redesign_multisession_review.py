#!/usr/bin/env python3
"""
Phase173: Multi-session validation of Phase172 fixed exit scenarios (A-I).

Constraints:
- No parameter search; fixed scenarios only (calls Phase172 evaluator).
- Review/replay only; reads existing session dirs under kabu_native/results/small_paper/.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


BASE = Path("kabu_native/results/small_paper")

REQUIRED_SESSIONS = [
    BASE / "20260521" / "live_full_session_081418",
    BASE / "20260525" / "live_session_075733",
    BASE / "20260527" / "live_session_082953",
    BASE / "20260527" / "live_session_122531",
]


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_session_dirs() -> list[Path]:
    out: list[Path] = []
    if not BASE.is_dir():
        return out
    for root, _dirs, files in os.walk(BASE):
        if "structural_trades.csv" in files and "small_paper_summary.json" in files:
            out.append(Path(root))
    # stable sort
    out.sort(key=lambda p: str(p))
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("note\nno_rows\n", encoding="utf-8")
        return
    keys: set[str] = set()
    for r in rows:
        keys |= set(r.keys())
    fields = sorted(keys)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _pf_from_wl(win: float, loss: float):
    gl = abs(loss)
    if gl <= 0:
        return None if win <= 0 else float("inf")
    return win / gl


def main() -> int:
    repo_root, native_root = _bootstrap()
    reports = native_root / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    from research.phase172_exit_metric_redesign_review import evaluate_exit_policies

    discovered = _walk_session_dirs()
    # ensure required sessions are included (even if empty) for reporting
    candidates: list[Path] = []
    seen = set()
    for p in REQUIRED_SESSIONS + discovered:
        if str(p) in seen:
            continue
        seen.add(str(p))
        candidates.append(p)

    per_session_rows: list[dict[str, Any]] = []
    mfe_rows: list[dict[str, Any]] = []
    reaccel_rows: list[dict[str, Any]] = []

    excluded: list[dict[str, Any]] = []
    included_sessions: list[dict[str, Any]] = []

    for sdir in candidates:
        trades = sdir / "structural_trades.csv"
        events_csv = sdir / "small_paper_events.csv"
        events_jsonl = sdir / "small_paper_events.jsonl"
        summary_path = sdir / "small_paper_summary.json"
        if not (trades.is_file() and summary_path.is_file()):
            excluded.append({"session_dir": str(sdir), "reason": "missing_structural_trades_or_summary"})
            continue
        summary = _read_json(summary_path)
        accepted = int(summary.get("accepted_count") or 0)
        if accepted <= 0:
            excluded.append(
                {
                    "session_dir": str(sdir),
                    "reason": "accepted_count_zero_exit_not_evaluable",
                    "accepted_count": accepted,
                    "note": "e.g. 20260527 guard bug day",
                }
            )
            continue
        if not (events_csv.is_file() or events_jsonl.is_file()):
            excluded.append({"session_dir": str(sdir), "reason": "missing_small_paper_events_for_price_path"})
            continue

        out = evaluate_exit_policies(session_dir=sdir)
        if not out.get("ok"):
            excluded.append({"session_dir": str(sdir), "reason": "evaluation_failed", "error": out.get("error")})
            continue

        session_id = str(sdir.relative_to(BASE)) if BASE in sdir.parents else str(sdir)
        included_sessions.append(
            {
                "session_id": session_id,
                "session_dir": str(sdir),
                "mode": out.get("mode"),
                "coverage_rate": out.get("price_path_coverage_rate"),
                "trade_count": accepted,
            }
        )

        for r in out.get("scenario_metrics") or []:
            per_session_rows.append({"session_id": session_id, **r})
        for r in out.get("mfe_capture_analysis") or []:
            mfe_rows.append({"session_id": session_id, **r})
        # keep only 120s reaccel for required metric
        for r in out.get("after_exit_reacceleration") or []:
            if int(r.get("horizon_sec") or 0) == 120:
                reaccel_rows.append({"session_id": session_id, **r})

    # Aggregate across sessions by summing wins/losses
    agg: dict[str, dict[str, Any]] = {}
    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in per_session_rows:
        by_scenario[str(r.get("scenario") or "")].append(r)

    aggregate_rows: list[dict[str, Any]] = []
    session_win_counts: list[dict[str, Any]] = []

    for scen, rows in sorted(by_scenario.items()):
        # skip not supported
        valid = [x for x in rows if x.get("trade_count")]
        if not valid:
            continue
        tw = sum(float(x.get("total_win_pnl") or 0) for x in valid)
        tl = sum(float(x.get("total_loss_pnl") or 0) for x in valid)
        total = sum(float(x.get("total_pnl") or 0) for x in valid)
        tc = sum(int(x.get("trade_count") or 0) for x in valid)
        pf = _pf_from_wl(tw, tl)
        aggregate_rows.append(
            {
                "scenario": scen,
                "session_count": len(valid),
                "trade_count": tc,
                "total_pnl": round(total, 4),
                "total_win_pnl": round(tw, 4),
                "total_loss_pnl": round(tl, 4),
                "aggregate_pf": round(pf, 4) if pf is not None and pf != float("inf") else pf,
                "avg_pnl": round(total / max(1, tc), 4) if tc else None,
            }
        )

    # Session win/loss vs baseline A
    baseline = "A_current_combined_structural_exit_v1"
    by_session: dict[str, dict[str, float]] = defaultdict(dict)
    for r in per_session_rows:
        sid = str(r.get("session_id") or "")
        scen = str(r.get("scenario") or "")
        by_session[sid][scen] = float(r.get("pf") or 0) if r.get("pf") is not None else 0.0

    improved_days = 0
    total_days = 0
    for sid, mp in sorted(by_session.items()):
        if baseline not in mp:
            continue
        base_pf = mp.get(baseline) or 0.0
        best_scen = None
        best_pf = -1e9
        for scen, pf in mp.items():
            if scen == baseline:
                continue
            if pf > best_pf:
                best_pf = pf
                best_scen = scen
        total_days += 1
        improved = best_pf > base_pf + 1e-9
        if improved:
            improved_days += 1
        session_win_counts.append(
            {
                "session_id": sid,
                "baseline_pf": base_pf,
                "best_pf": best_pf,
                "best_scenario": best_scen,
                "improved_vs_baseline": improved,
            }
        )

    # Verdict
    # Decide based on aggregate_pf ranking among key candidates.
    agg_pf = {r["scenario"]: r.get("aggregate_pf") for r in aggregate_rows}
    def _pfv(x):
        v = agg_pf.get(x)
        if v is None:
            return -1e9
        if v == float("inf"):
            return 1e9
        return float(v)

    verdict = "F_insufficient_data"
    if total_days >= 2:
        if _pfv("D_time_stop") > _pfv(baseline):
            verdict = "A_time_stop_promising"
        elif _pfv("C_trailing_mfe") > _pfv(baseline):
            verdict = "B_trailing_mfe_promising"
        elif _pfv("I_hold_10min_reference") > _pfv(baseline) or _pfv("H_hold_5min_reference") > _pfv(baseline):
            verdict = "C_hold_time_reference_promising"
        elif _pfv(baseline) >= max(_pfv("C_trailing_mfe"), _pfv("D_time_stop"), _pfv("I_hold_10min_reference")):
            verdict = "D_current_exit_still_best"
        else:
            verdict = "E_mixed_need_live_shadow"

    out_json = reports / "phase173_exit_redesign_multisession_review.json"
    _write_csv(reports / "phase173_exit_policy_scenarios_by_session.csv", per_session_rows)
    _write_csv(reports / "phase173_exit_policy_scenarios_aggregate.csv", aggregate_rows)
    _write_csv(reports / "phase173_mfe_capture_multisession.csv", mfe_rows)
    _write_csv(reports / "phase173_after_exit_reacceleration_multisession.csv", reaccel_rows)

    out = {
        "phase": 173,
        "verdict": verdict,
        "verdict_options": {
            "A": "time_stop_promising",
            "B": "trailing_mfe_promising",
            "C": "hold_time_reference_promising",
            "D": "current_exit_still_best",
            "E": "mixed_need_live_shadow",
            "F": "insufficient_data",
        },
        "included_sessions": included_sessions,
        "excluded_sessions": excluded,
        "session_count": len(included_sessions),
        "improved_days_vs_baseline_A": improved_days,
        "evaluated_days": total_days,
        "session_best_vs_baseline": session_win_counts,
        "outputs": {
            "json": str(out_json),
            "by_session_csv": str(reports / "phase173_exit_policy_scenarios_by_session.csv"),
            "aggregate_csv": str(reports / "phase173_exit_policy_scenarios_aggregate.csv"),
            "mfe_capture_csv": str(reports / "phase173_mfe_capture_multisession.csv"),
            "reaccel_csv": str(reports / "phase173_after_exit_reacceleration_multisession.csv"),
        },
        "notes": [
            "Fixed scenarios only; no parameter tuning.",
            "Sessions with accepted_count=0 are excluded from exit evaluation (explicitly recorded).",
        ],
    }
    out_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    md = (
        "## Phase173 recommendation\n\n"
        f"- verdict: `{verdict}`\n"
        f"- sessions evaluated: {len(included_sessions)} (excluded: {len(excluded)})\n"
        f"- improved days vs baseline A: {improved_days}/{total_days}\n\n"
        "See aggregate CSV for PF and total_pnl comparisons.\n"
    )
    (reports / "phase173_recommendation.md").write_text(md, encoding="utf-8")

    print(json.dumps({"verdict": verdict, "sessions": len(included_sessions), "outputs": out["outputs"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

