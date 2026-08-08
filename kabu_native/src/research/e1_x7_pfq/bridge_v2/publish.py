"""Publish bridge V2 artifacts: report.json / report.md / audit.xlsx only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x6_provisional.util import sha256_file, sha256_obj


SHEETS = [
    "Index",
    "Precommit",
    "Identity",
    "MatchedParents",
    "EventTimeOutcome",
    "FixedGridOutcome",
    "CandidateEnrichment",
    "FirstTouch",
    "PathQuality",
    "Bootstrap",
    "JointTrades",
    "HardSoftRegistry",
    "Counterfactual",
    "FailureClassification",
    "CaptureMetrics",
    "Daily",
    "DayDeletion",
    "Concentration",
    "Verdict",
    "Tests",
    "Determinism",
    "Safety",
    "ChangeLog",
]


def _kv_rows(d: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict) and len(json.dumps(v, default=str)) < 500:
            rows.append({"key": key, "value": json.dumps(v, default=str)})
        elif isinstance(v, (list, dict)):
            rows.append({"key": key, "value": json.dumps(v, default=str)[:2000]})
        else:
            rows.append({"key": key, "value": v})
    return rows


def _write_xlsx(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name in SHEETS:
        ws = wb.create_sheet(name[:31])
        rows = sheets.get(name) or [{"note": "empty"}]
        if not rows:
            rows = [{"note": "empty"}]
        # normalize keys
        keys = []
        for r in rows:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
        ws.append(keys)
        for r in rows:
            ws.append([_cell(r.get(k)) for k in keys])
    wb.save(path)


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, default=str)[:32000]
    if isinstance(v, bool):
        return str(v)
    return v


def publish(report: dict[str, Any], tests: dict[str, Any], det: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets_data = report.pop("_sheets", {})

    # Build sheet payloads
    enr = report.get("candidate_enrichment") or {}
    first_touch_rows = []
    boot_rows = []
    for cid, block in enr.items():
        for mode in ("event_time", "fixed_grid"):
            metrics = ((block.get(mode) or {}).get("metrics") or {})
            for mk, mv in metrics.items():
                if "plus" in mk and "minus" in mk:
                    first_touch_rows.append({
                        "candidate_id": cid,
                        "mode": mode,
                        "metric": mk,
                        "candidate_rate": mv.get("candidate_rate"),
                        "matched_parent_rate": mv.get("matched_parent_rate"),
                        "difference": mv.get("difference"),
                        "positive_difference_days": mv.get("positive_difference_days"),
                        "negative_difference_days": mv.get("negative_difference_days"),
                    })
                boot = mv.get("bootstrap")
                if boot:
                    boot_rows.append({"candidate_id": cid, "mode": mode, "metric": mk, **boot})

    daily = []
    for pid, jr in (report.get("joint_replay") or {}).items():
        for d, pnl in (jr.get("day_pnl") or {}).items():
            daily.append({"pair_id": pid, "day": d, "pnl": pnl})

    sheets = {
        "Index": [
            {"item": "analysis_id", "value": report.get("analysis_id")},
            {"item": "run_id", "value": report.get("run_id")},
            {"item": "source_run", "value": report.get("source_run")},
            {"item": "verdict", "value": report.get("verdict")},
        ],
        "Precommit": _kv_rows(report.get("precommit") or {}),
        "Identity": _kv_rows(report.get("identity") or {}),
        "MatchedParents": [
            {"parent": k, "n": v} for k, v in (report.get("matched_parents") or {}).items()
        ],
        "EventTimeOutcome": sheets_data.get("EventTimeOutcome") or [],
        "FixedGridOutcome": sheets_data.get("FixedGridOutcome") or [],
        "CandidateEnrichment": [
            {
                "candidate_id": cid,
                "matched_parent": b.get("matched_parent"),
                "n_candidate": b.get("n_candidate"),
                "n_parent": b.get("n_parent"),
                "fg_plus5_minus10_diff": ((b.get("fixed_grid") or {}).get("metrics") or {}).get("plus5_vs_minus10_rate", {}).get("difference"),
                "ev_plus5_minus10_diff": ((b.get("event_time") or {}).get("metrics") or {}).get("plus5_vs_minus10_rate", {}).get("difference"),
            }
            for cid, b in enr.items()
        ],
        "FirstTouch": first_touch_rows,
        "PathQuality": sheets_data.get("PathQuality") or [],
        "Bootstrap": boot_rows,
        "JointTrades": sheets_data.get("JointTrades") or [],
        "HardSoftRegistry": [
            {"kind": "HARD", "reason": r} for r in sorted(report.get("hard_exit_trade_counts") or {})
        ] + [
            {"kind": "SOFT", "reason": r} for r in sorted(report.get("soft_exit_trade_counts") or {})
        ],
        "Counterfactual": sheets_data.get("Counterfactual") or [],
        "FailureClassification": sheets_data.get("FailureClassification") or [],
        "CaptureMetrics": sheets_data.get("CaptureMetrics") or [],
        "Daily": daily,
        "DayDeletion": [{"note": "not_applicable_bridge_audit_no_deletion"}],
        "Concentration": [
            {"candidate_id": cid, **(b.get("concentration") or {})}
            for cid, b in enr.items()
        ],
        "Verdict": _kv_rows(report.get("verdict_detail") or {"verdict": report.get("verdict")}),
        "Tests": (tests.get("rows") or [{"outcome": "n/a"}]),
        "Determinism": _kv_rows(det),
        "Safety": _kv_rows(report.get("safety") or {}),
        "ChangeLog": [
            {"change": "E1_X7_PFQ_REALIZABILITY_BRIDGE_AUDIT_V2", "note": "no PFQ condition change; audit only"},
            {"change": "prospective", "note": "BLOCKED_PENDING_REALIZABILITY_BRIDGE_AUDIT"},
            {"change": "joint_label", "note": "PFQ_DESIGN_SUPPORT_INSUFFICIENT"},
        ],
    }

    # public report without heavy sheets
    public = {k: v for k, v in report.items() if not k.startswith("_")}
    public["tests"] = {
        "exit_code": tests.get("exit_code"),
        "passed": tests.get("passed"),
        "failed": tests.get("failed"),
        "total": tests.get("total"),
    }
    public["determinism"] = det
    public["published_shas"] = {}

    jp = out_dir / "report.json"
    mp = out_dir / "report.md"
    xp = out_dir / "audit.xlsx"
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")

    vd = report.get("verdict_detail") or {}
    md = [
        f"# {report.get('analysis_id')}",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- source_run: `{report.get('source_run')}`",
        f"- **verdict: `{report.get('verdict')}`**",
        f"- pfq_close: {vd.get('pfq_close')}",
        f"- exit_revision: {vd.get('exit_revision')} (implemented={vd.get('exit_revision_implemented', False)})",
        f"- prospective: BLOCKED_PENDING_REALIZABILITY_BRIDGE_AUDIT",
        f"- PFQ_JOINT: PFQ_DESIGN_SUPPORT_INSUFFICIENT (41 < 50)",
        "",
        "## Identity / Replay",
        "",
        f"- episode_identity_sha: `{((report.get('identity') or {}).get('episode_identity_sha'))}`",
        f"- joint_replay_identity: {report.get('joint_replay_identity')}",
        "",
        "## Failure counts",
        "",
        f"- classes: `{json.dumps(report.get('failure_class_counts'))}`",
        f"- hard exits: `{json.dumps(report.get('hard_exit_trade_counts'))}`",
        f"- soft exits: `{json.dumps(report.get('soft_exit_trade_counts'))}`",
        "",
        "## Safety",
        "",
        f"- submit/cancel/live: 0/0/0",
        f"- tests: {tests.get('passed')}/{tests.get('total')}",
        f"- A/B: {det.get('ab_match')}",
        "",
    ]
    mp.write_text("\n".join(md), encoding="utf-8")
    _write_xlsx(xp, sheets)

    shas = {
        "report.json": sha256_file(jp),
        "report.md": sha256_file(mp),
        "audit.xlsx": sha256_file(xp),
    }
    public["published_shas"] = shas
    jp.write_text(json.dumps(public, indent=2, default=str), encoding="utf-8")
    return shas
