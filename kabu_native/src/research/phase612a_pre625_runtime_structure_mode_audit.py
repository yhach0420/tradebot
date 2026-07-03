"""
Phase612A: Compare HEAD normal vs pre625 runtime structure sessions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

VERDICT = "phase612a_pre625_runtime_structure_mode_audit_done"

COMPARE_METRICS = (
    "pbv2_count",
    "or_entry_count",
    "accepted_count",
    "data_stale_price",
    "gate_evaluations",
    "push_messages",
    "quality_fallback_rate_pct",
    "live_feature_complete_rate_pct",
)


def _load_summary(session_dir: Path) -> dict[str, Any]:
    p = session_dir / "small_paper_summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _load_meta(session_dir: Path) -> dict[str, Any]:
    p = session_dir / "live_session_config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _first_pbv2_eval_time(session_dir: Path) -> str:
    audit = session_dir / "entry_scan_audit.jsonl"
    if not audit.is_file():
        return ""
    for line in audit.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("audit_type") != "entry_symbol_eval":
            continue
        if row.get("entry_score_v2") is not None:
            return str(row.get("eval_end_ts") or row.get("eval_start_ts") or "")
    return ""


def _freshness_rates(session_dir: Path) -> dict[str, Any]:
    audit = session_dir / "entry_scan_audit.jsonl"
    n = 0
    price_fresh = 0
    board_fresh = 0
    if not audit.is_file():
        return {"eval_count": 0, "price_fresh_rate": 0.0, "board_fresh_rate": 0.0}
    for line in audit.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("audit_type") != "entry_symbol_eval":
            continue
        n += 1
        pa = row.get("price_age_sec")
        ba = row.get("board_age_sec")
        if pa is not None and float(pa) <= 3.0:
            price_fresh += 1
        if ba is not None and float(ba) <= 3.0:
            board_fresh += 1
    return {
        "eval_count": n,
        "price_fresh_rate": round(price_fresh / n, 4) if n else 0.0,
        "board_fresh_rate": round(board_fresh / n, 4) if n else 0.0,
    }


def compare_sessions(
    head_dir: Path,
    pre625_dir: Path,
    *,
    head_label: str = "HEAD",
    pre625_label: str = "PRE625_STRUCTURE",
) -> dict[str, Any]:
    head_s = _load_summary(head_dir)
    pre_s = _load_summary(pre625_dir)
    head_m = _load_meta(head_dir)
    pre_m = _load_meta(pre625_dir)
    head_f = _freshness_rates(head_dir)
    pre_f = _freshness_rates(pre625_dir)

    rows: list[dict[str, Any]] = []
    for metric in COMPARE_METRICS:
        hv = head_s.get(metric)
        pv = pre_s.get(metric)
        rows.append(
            {
                "metric": metric,
                "head_value": hv,
                "pre625_value": pv,
                "delta": (pv - hv) if isinstance(hv, (int, float)) and isinstance(pv, (int, float)) else "",
            }
        )
    rows.append(
        {
            "metric": "first_pbv2_eval_time",
            "head_value": _first_pbv2_eval_time(head_dir),
            "pre625_value": _first_pbv2_eval_time(pre625_dir),
            "delta": "",
        }
    )
    for k in ("price_fresh_rate", "board_fresh_rate", "eval_count"):
        rows.append(
            {
                "metric": k,
                "head_value": head_f.get(k),
                "pre625_value": pre_f.get(k),
                "delta": (
                    (pre_f.get(k) or 0) - (head_f.get(k) or 0)
                    if isinstance(head_f.get(k), (int, float)) and isinstance(pre_f.get(k), (int, float))
                    else ""
                ),
            }
        )

    return {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "head_session": str(head_dir),
        "pre625_session": str(pre625_dir),
        "head_pre625_mode": head_m.get("pre625_runtime_structure_mode"),
        "pre625_pre625_mode": pre_m.get("pre625_runtime_structure_mode"),
        "comparison_rows": rows,
        "head_summary": {k: head_s.get(k) for k in COMPARE_METRICS},
        "pre625_summary": {k: pre_s.get(k) for k in COMPARE_METRICS},
    }


def run_phase612a(
    repo_root: Optional[Path] = None,
    *,
    head_session: Optional[Path] = None,
    pre625_session: Optional[Path] = None,
) -> dict[str, Any]:
    repo = Path(repo_root or resolve_kabu_root(Path.cwd()))
    reports = resolve_reports_dir(repo)
    sp = repo / "results" / "small_paper"

    if head_session is None:
        head_session = sp / "20260625" / "live_session_080340"
    if pre625_session is None:
        pre625_session = head_session

    report = compare_sessions(head_session, pre625_session)
    _write_csv(
        reports / "phase612a_head_vs_pre625_structure_compare.csv",
        ["metric", "head_value", "pre625_value", "delta"],
        report["comparison_rows"],
    )
    (reports / "phase612a_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
