"""
Phase500: Post-entry forward shadow review.

Aggregates ``small_paper_shadow_post_entry.csv`` sessions and Phase499 replay bootstrap.
Research only — no Runtime adoption.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.post_entry_forward_shadow import (
    POST_ENTRY_CSV_FIELDS,
    compute_early_failure_shadow_score,
    _write_csv,
)

JST = ZoneInfo("Asia/Tokyo")
MIN_EVAL_DAYS = 10
DAY_622 = "20260622"
SYMBOL_6976 = "6976"

CUMULATIVE_CSV = "phase500_post_entry_forward_shadow_trades.csv"
SUMMARY_JSON = "phase500_post_entry_shadow_summary.json"
PHASE499_BEHAVIOR = "phase499_post_entry_behavior.csv"


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).lower() in {"1", "true", "yes", "y"}


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["pnl_yen_100"] = round(_float(out.get("pnl_yen_100")) or 0.0, 2)
    out["early_failure_shadow_score"] = int(_float(out.get("early_failure_shadow_score")) or 0)
    out["mfe_60s"] = _float(out.get("mfe_60s"))
    out["mfe_120s"] = _float(out.get("mfe_120s"))
    out["reclaim_120s"] = _bool(out.get("reclaim_120s"))
    out["high_update_count_180s"] = int(_float(out.get("high_update_count_180s")) or 0)
    out["flag_e2_no_progress"] = _bool(out.get("flag_e2_no_progress"))
    out["flag_e3_stall"] = _bool(out.get("flag_e3_stall"))
    out["flag_e4_no_reclaim"] = _bool(out.get("flag_e4_no_reclaim"))
    sym = str(out.get("symbol") or "")
    out["symbol"] = sym.replace(".T", "")
    day = str(out.get("date") or out.get("day") or "")
    out["date"] = day.replace("-", "")[:8]
    return out


def _replace_day_rows(
    existing: Sequence[Mapping[str, Any]],
    new_rows: Sequence[Mapping[str, Any]],
    *,
    day: str,
) -> list[dict[str, Any]]:
    kept = [_normalize_row(r) for r in existing if str(r.get("date") or "") != day]
    kept.extend(_normalize_row(r) for r in new_rows)
    return kept


def _score_from_phase499_row(row: Mapping[str, Any]) -> int:
    high_180 = int(_float(row.get("high_update_after_entry_count_180s")) or 0)
    return compute_early_failure_shadow_score(
        flag_e2=_bool(row.get("E2_60s_mfe_lt_01")),
        flag_e3=_bool(row.get("E3_120s_stall")),
        flag_e4=_bool(row.get("E4_120s_no_reclaim")),
        high_update_count_180s=high_180,
    )


def _bootstrap_rows_from_phase499(behavior_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _read_csv(behavior_path):
        score = _score_from_phase499_row(raw)
        sym = str(raw.get("symbol") or "").replace(".T", "")
        day = str(raw.get("day") or "")[:8]
        m120 = _float(raw.get("mfe_pct_at_120s"))
        reclaim = not _bool(raw.get("E4_120s_no_reclaim"))
        rows.append(
            {
                "date": day,
                "symbol": sym,
                "entry_time": raw.get("position_key", ""),
                "exit_time": "",
                "pnl_yen_100": round(_float(raw.get("pnl_yen_100")) or 0.0, 2),
                "mfe_60s": _float(raw.get("mfe_pct_at_60s")),
                "mfe_120s": m120,
                "reclaim_120s": reclaim,
                "high_update_count_180s": int(
                    _float(raw.get("high_update_after_entry_count_180s")) or 0
                ),
                "early_failure_shadow_score": score,
                "exit_reason": str(raw.get("exit_reason") or ""),
                "flag_e2_no_progress": _bool(raw.get("E2_60s_mfe_lt_01")),
                "flag_e3_stall": _bool(raw.get("E3_120s_stall")),
                "flag_e4_no_reclaim": _bool(raw.get("E4_120s_no_reclaim")),
            }
        )
    return rows


def _agreement_rate(rows: Sequence[Mapping[str, Any]], *, reason: str, min_score: int = 3) -> float:
    subset = [r for r in rows if int(r.get("early_failure_shadow_score") or 0) >= min_score]
    if not subset:
        return 0.0
    hits = sum(1 for r in subset if str(r.get("exit_reason") or "") == reason)
    return round(hits / len(subset), 4)


def _slice_pnl(rows: Sequence[Mapping[str, Any]], *, min_score: int) -> tuple[int, float]:
    subset = [r for r in rows if int(r.get("early_failure_shadow_score") or 0) >= min_score]
    total = round(sum(_float(r.get("pnl_yen_100")) or 0.0 for r in subset), 2)
    return len(subset), total


def _impact_pnl(rows: Sequence[Mapping[str, Any]], *, min_score: int, predicate) -> float:
    subset = [
        r
        for r in rows
        if int(r.get("early_failure_shadow_score") or 0) >= min_score and predicate(r)
    ]
    return round(sum(_float(r.get("pnl_yen_100")) or 0.0 for r in subset), 2)


def compute_mandatory_answers(
    rows: Sequence[Mapping[str, Any]],
    *,
    forward_days: int,
    data_source: str,
) -> dict[str, Any]:
    ge3_n, ge3_pnl = _slice_pnl(rows, min_score=3)
    ge4_n, ge4_pnl = _slice_pnl(rows, min_score=4)
    stop_agree = _agreement_rate(rows, reason="stop_hit", min_score=3)
    np_agree = _agreement_rate(rows, reason="no_progress_exit", min_score=3)
    impact_6976 = _impact_pnl(
        rows,
        min_score=3,
        predicate=lambda r: str(r.get("symbol") or "") in ("6976", "6976.T"),
    )
    impact_622 = _impact_pnl(
        rows,
        min_score=3,
        predicate=lambda r: str(r.get("date") or "") == DAY_622,
    )

    eval_ready = forward_days >= MIN_EVAL_DAYS
    runtime_candidate = False
    if eval_ready and ge3_n >= 20:
        runtime_candidate = stop_agree >= 0.5 or np_agree >= 0.5

    next_action = (
        "Continue forward shadow collection; evaluate after 10 trading days"
        if not eval_ready
        else "Review score>=3 cohort vs stop_hit/no_progress; no exit overlay until validated"
    )

    return {
        "1_score_ge3_count": ge3_n,
        "2_score_ge3_pnl": ge3_pnl,
        "3_score_ge4_count": ge4_n,
        "4_score_ge4_pnl": ge4_pnl,
        "5_stop_hit_agreement_rate": stop_agree,
        "6_no_progress_agreement_rate": np_agree,
        "7_impact_6976": impact_6976,
        "8_impact_622": impact_622,
        "9_runtime_candidate": runtime_candidate,
        "10_next_action": next_action,
        "forward_days_collected": forward_days,
        "min_eval_days": MIN_EVAL_DAYS,
        "eval_ready": eval_ready,
        "data_source": data_source,
        "verdict": "forward_shadow_started",
    }


class PostEntryShadowReview:
    def __init__(self, *, repo_root: Path, reports_dir: Path) -> None:
        self.repo_root = repo_root
        self.reports_dir = reports_dir

    def paths(self) -> dict[str, Path]:
        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        return {
            "cumulative": self.reports_dir / CUMULATIVE_CSV,
            "summary": self.reports_dir / SUMMARY_JSON,
            "report": doc_root / "docs" / "operations" / "phase500_post_entry_shadow.md",
        }

    def run(
        self,
        *,
        day: Optional[str] = None,
        session_csv: Optional[Path] = None,
    ) -> dict[str, Any]:
        paths = self.paths()
        cumulative = [_normalize_row(r) for r in _read_csv(paths["cumulative"])]
        data_source = "forward_shadow" if cumulative else "phase499_replay_bootstrap"

        if session_csv is not None and session_csv.is_file() and day:
            session_rows = [_normalize_row(r) for r in _read_csv(session_csv)]
            cumulative = _replace_day_rows(cumulative, session_rows, day=day)
            data_source = "forward_shadow"

        analysis_rows = cumulative
        if not analysis_rows:
            behavior = self.reports_dir / PHASE499_BEHAVIOR
            if behavior.is_file():
                analysis_rows = _bootstrap_rows_from_phase499(behavior)
                data_source = "phase499_replay_bootstrap"

        forward_days = (
            len({str(r.get("date") or "") for r in cumulative if r.get("date")})
            if data_source == "forward_shadow"
            else 0
        )

        mandatory = compute_mandatory_answers(
            analysis_rows,
            forward_days=forward_days,
            data_source=data_source,
        )

        return {
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "status": "success",
            "trade_count": len(analysis_rows),
            "forward_days_collected": forward_days,
            "data_source": data_source,
            "verdict": mandatory.get("verdict"),
            "mandatory_answers": mandatory,
            "_cumulative_rows": cumulative if data_source == "forward_shadow" else [],
            "_analysis_rows": analysis_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["summary"].write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        cumulative = list(result.get("_cumulative_rows") or [])
        if cumulative:
            _write_csv(paths["cumulative"], POST_ENTRY_CSV_FIELDS, cumulative)
        self._write_report(paths["report"], result)
        return paths

    def _write_report(self, path: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        lines = [
            "# Phase500 — Post Entry Forward Shadow",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Data source:** {result.get('data_source')}",
            f"**Forward days collected:** {result.get('forward_days_collected')} / {MIN_EVAL_DAYS}",
            "",
            "## 必須回答",
            "",
            "| # | 回答 |",
            "|---|------|",
            f"| 1 score>=3件数 | **{m.get('1_score_ge3_count')}** |",
            f"| 2 score>=3 pnl | **{m.get('2_score_ge3_pnl')}** |",
            f"| 3 score>=4件数 | **{m.get('3_score_ge4_count')}** |",
            f"| 4 score>=4 pnl | **{m.get('4_score_ge4_pnl')}** |",
            f"| 5 stop_hit一致率 | **{m.get('5_stop_hit_agreement_rate')}** |",
            f"| 6 no_progress一致率 | **{m.get('6_no_progress_agreement_rate')}** |",
            f"| 7 6976影響 | **{m.get('7_impact_6976')}** |",
            f"| 8 6/22影響 | **{m.get('8_impact_622')}** |",
            f"| 9 Runtime候補 | **{m.get('9_runtime_candidate')}** |",
            f"| 10 次アクション | {m.get('10_next_action')} |",
            "",
            "## 成果物",
            "",
            f"- `results/reports/{CUMULATIVE_CSV}`",
            f"- `results/reports/{SUMMARY_JSON}`",
            "",
            "## 実行",
            "",
            "```powershell",
            "cd kabu_native",
            "$env:PYTHONPATH=\"src\"",
            "python scripts/run_phase500_post_entry_shadow.py",
            "```",
            "",
            "**注意:** 研究専用。Entry / Exit / Gate には一切使わない。",
            "",
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
