"""
Phase431 — Entry priority & immediate reentry audit (20260617 runtime).

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_scan_controller import candidate_rank_score as _rank_score

JST = ZoneInfo("Asia/Tokyo")

TARGET_DAY = "20260617"
REENTRY_WINDOWS = (30, 60, 180, 300)
INTERACTION_WINDOW_SEC = 15.0

SESSION_DIRS = (
    "live_session_071605",
    "live_session_122538",
)


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _parse_ts(ts: str) -> Optional[datetime]:
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _pnl_yen_100(entry_price: float, pnl_pct: float) -> float:
    return round(entry_price * 100.0 * pnl_pct / 100.0, 2)


def _metrics_from_pnls(pnls: Sequence[float], holds: Sequence[float]) -> dict[str, Any]:
    if not pnls:
        return {
            "count": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_pnl_yen": 0.0,
            "avg_pnl_yen": 0.0,
            "median_pnl_yen": 0.0,
            "avg_hold_sec": 0.0,
            "median_hold_sec": 0.0,
        }
    return {
        "count": len(pnls),
        "win_rate": _win_rate(pnls),
        "profit_factor": _pf(pnls),
        "total_pnl_yen": round(sum(pnls), 2),
        "avg_pnl_yen": round(statistics.mean(pnls), 2),
        "median_pnl_yen": round(statistics.median(pnls), 2),
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else 0.0,
        "median_hold_sec": round(statistics.median(holds), 2) if holds else 0.0,
    }


def _load_structural_trades(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            ep = _float(row.get("entry_price"))
            pct = _float(row.get("realized_pnl_pct"))
            rows.append(
                {
                    "session": session_dir.name,
                    "symbol": str(row.get("symbol") or ""),
                    "entry_time": str(row.get("entry_time") or ""),
                    "close_time": str(row.get("close_time") or ""),
                    "entry_price": ep,
                    "realized_pnl_pct": pct,
                    "pnl_yen_100": _pnl_yen_100(ep, pct),
                    "hold_sec": _float(row.get("hold_duration_sec")),
                    "close_reason": str(row.get("close_reason") or ""),
                }
            )
    return rows


def _analyze_reentry(trades: Sequence[Mapping[str, Any]]) -> tuple[list[dict], dict[str, Any]]:
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_sym[str(t["symbol"])].append(dict(t))
    for sym in by_sym:
        by_sym[sym].sort(key=lambda r: (_parse_ts(str(r["entry_time"])) or datetime.min.replace(tzinfo=JST)))

    reentry_rows: list[dict[str, Any]] = []
    for sym, seq in by_sym.items():
        for i in range(1, len(seq)):
            prev = seq[i - 1]
            cur = seq[i]
            prev_close = _parse_ts(str(prev["close_time"]))
            cur_entry = _parse_ts(str(cur["entry_time"]))
            if prev_close is None or cur_entry is None:
                continue
            gap = (cur_entry - prev_close).total_seconds()
            if gap < 0:
                continue
            reentry_rows.append(
                {
                    "symbol": sym,
                    "prev_close_time": prev["close_time"],
                    "reentry_time": cur["entry_time"],
                    "gap_sec": round(gap, 2),
                    "prev_pnl_yen": prev["pnl_yen_100"],
                    "reentry_pnl_yen": cur["pnl_yen_100"],
                    "reentry_hold_sec": cur["hold_sec"],
                    "reentry_close_reason": cur["close_reason"],
                    "session": cur.get("session"),
                }
            )

    non_reentry_pnls = []
    non_reentry_holds = []
    reentry_flag_ids: set[tuple[str, str]] = set()
    for r in reentry_rows:
        reentry_flag_ids.add((r["symbol"], r["reentry_time"]))

    for t in trades:
        key = (str(t["symbol"]), str(t["entry_time"]))
        if key in reentry_flag_ids:
            continue
        # first trade per symbol chain is non-reentry; also non-first without reentry flag
        non_reentry_pnls.append(_float(t["pnl_yen_100"]))
        non_reentry_holds.append(_float(t["hold_sec"]))

    window_stats: dict[str, Any] = {}
    for w in REENTRY_WINDOWS:
        subset = [r for r in reentry_rows if r["gap_sec"] <= w]
        pnls = [_float(r["reentry_pnl_yen"]) for r in subset]
        holds = [_float(r["reentry_hold_sec"]) for r in subset]
        window_stats[f"within_{w}s"] = _metrics_from_pnls(pnls, holds)

    non_re = _metrics_from_pnls(non_reentry_pnls, non_reentry_holds)
    summary = {
        "total_trades": len(trades),
        "reentry_pair_count": len(reentry_rows),
        "non_reentry_trade_count": non_re["count"],
        "windows": window_stats,
        "non_reentry": non_re,
    }
    return reentry_rows, summary


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _load_rejects(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "small_paper_rejects.csv"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(dict(row))
    return rows


def _analyze_entry_priority(session_dirs: Sequence[Path]) -> tuple[list[dict], list[dict], dict[str, Any]]:
    priority_rows: list[dict[str, Any]] = []
    cap_reject_rows: list[dict[str, Any]] = []
    scan_multi = 0
    scan_total = 0
    max_scan_rejects = 0
    cap_reject_count = 0
    high_score_cap_rejects = 0

    for session_dir in session_dirs:
        audit_path = session_dir / "entry_scan_audit.jsonl"
        eval_by_scan: dict[str, list[dict]] = defaultdict(list)
        for row in _load_jsonl(audit_path):
            if row.get("audit_type") == "entry_symbol_eval":
                eval_by_scan[str(row.get("scan_id") or "")].append(row)
            elif row.get("audit_type") == "entry_scan_summary":
                scan_total += 1
                if bool(row.get("same_scan_batch_entry")):
                    scan_multi += 1
            elif row.get("audit_type") == "entry_notify":
                scan_id = str(row.get("scan_id") or "")
                sym = str(row.get("symbol") or "")
                accepted = bool(row.get("entry_decision"))
                reject = str(row.get("reject_reason") or "")
                evals = {str(e.get("symbol")): e for e in eval_by_scan.get(scan_id, [])}
                ev = evals.get(sym, {})
                v2 = int(_float(ev.get("entry_score_v2")))
                rank_note = str(row.get("same_scan_rank") or "")
                priority_rows.append(
                    {
                        "session": session_dir.name,
                        "scan_id": scan_id,
                        "symbol": sym,
                        "entry_signal_ts": row.get("entry_signal_ts"),
                        "entry_decision": accepted,
                        "reject_reason": reject,
                        "entry_score_v2": v2,
                        "same_scan_rank": rank_note,
                        "same_scan_candidates": row.get("same_scan_candidates"),
                        "message_index": ev.get("message_index"),
                        "eval_start_ts": ev.get("eval_start_ts"),
                        "price_age_sec": ev.get("price_age_sec"),
                    }
                )
                if reject == "max_entries_per_scan":
                    max_scan_rejects += 1

        for row in _load_rejects(session_dir):
            reason = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
            if reason != "max_concurrent":
                continue
            cap_reject_count += 1
            v2 = int(_float(row.get("entry_expectancy_score_v2") or row.get("entry_score_v2")))
            cq = _float(row.get("continuation_quality_score"))
            if v2 >= 5:
                high_score_cap_rejects += 1
            cap_reject_rows.append(
                {
                    "session": session_dir.name,
                    "event_time": row.get("event_time"),
                    "symbol": row.get("symbol"),
                    "entry_score_v2": v2,
                    "continuation_quality_score": round(cq, 4),
                    "entry_expectancy_score_v2": v2,
                    "message_index": row.get("message_index"),
                    "gate_reject_reason": reason,
                }
            )

    rank_replay_rows: list[dict[str, Any]] = []
    for session_dir in session_dirs:
        audit_rows = _load_jsonl(session_dir / "entry_scan_audit.jsonl")
        by_scan: dict[str, list[dict]] = defaultdict(list)
        for row in audit_rows:
            if row.get("audit_type") == "entry_symbol_eval" and row.get("entry_decision"):
                by_scan[str(row.get("scan_id"))].append(row)
        for scan_id, cands in by_scan.items():
            if len(cands) < 2:
                continue
            scored = []
            for c in cands:
                v2 = int(_float(c.get("entry_score_v2")))
                cq = _float(c.get("continuation_quality_score"))
                scored.append(
                    {
                        "scan_id": scan_id,
                        "symbol": c.get("symbol"),
                        "entry_score_v2": v2,
                        "continuation_quality_score": cq,
                        "eval_start_ts": c.get("eval_start_ts"),
                        "message_index": c.get("message_index"),
                    }
                )
            scored.sort(
                key=lambda x: (
                    -int(x["entry_score_v2"]),
                    -float(x["continuation_quality_score"]),
                    int(_float(x.get("message_index"))),
                )
            )
            for i, s in enumerate(scored, start=1):
                rank_replay_rows.append({**s, "session": session_dir.name, "rank_by_score": i})

    summary = {
        "scan_window_sec": 2.0,
        "max_entries_per_scan": 1,
        "entry_scan_batch_enabled": True,
        "rank_formula": "candidate_rank_score (v2*1000 + cq*100 + tv + imb + vwap + mom - price_age*100)",
        "cap_max_concurrent": 5,
        "scan_total": scan_total,
        "scan_multi_candidate": scan_multi,
        "max_entries_per_scan_rejects": max_scan_rejects,
        "max_concurrent_reject_count": cap_reject_count,
        "high_score_v2_ge5_cap_rejects": high_score_cap_rejects,
        "adoption_order_within_scan": "rank_score descending (not raw PUSH order)",
        "adoption_order_across_pushes_cap_full": "PUSH processing order (first gate-pass wins)",
        "candidate_queue_exists": True,
        "queue_type": "EntryScanController 2s scan batch + rank_score",
        "rank_replay_multi_scan_count": len({r["scan_id"] for r in rank_replay_rows}),
    }
    priority_rows.extend(rank_replay_rows)
    return priority_rows, cap_reject_rows, summary


def _analyze_interaction(
    reentry_rows: Sequence[Mapping[str, Any]],
    session_dirs: Sequence[Path],
    *,
    window_sec: float = INTERACTION_WINDOW_SEC,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cap_events: list[tuple[datetime, dict]] = []
    for session_dir in session_dirs:
        for row in _load_rejects(session_dir):
            if str(row.get("gate_reject_reason") or "") != "max_concurrent":
                continue
            ts = _parse_ts(str(row.get("event_time") or ""))
            if ts is None:
                continue
            cap_events.append(
                (
                    ts,
                    {
                        "symbol": str(row.get("symbol") or ""),
                        "entry_score_v2": int(_float(row.get("entry_expectancy_score_v2"))),
                        "continuation_quality_score": _float(row.get("continuation_quality_score")),
                        "session": session_dir.name,
                    },
                )
            )
    cap_events.sort(key=lambda x: x[0])

    interaction_rows: list[dict[str, Any]] = []
    for r in reentry_rows:
        if _float(r.get("gap_sec")) > 300:
            continue
        rt = _parse_ts(str(r.get("reentry_time") or ""))
        if rt is None:
            continue
        blocked: list[dict] = []
        for ts, ev in cap_events:
            if abs((ts - rt).total_seconds()) > window_sec:
                continue
            if ev["symbol"] == r["symbol"]:
                continue
            blocked.append({**ev, "cap_reject_time": ts.isoformat()})
        if not blocked:
            continue
        best_blocked = max(blocked, key=lambda x: (x["entry_score_v2"], x["continuation_quality_score"]))
        interaction_rows.append(
            {
                "reentry_symbol": r["symbol"],
                "reentry_time": r["reentry_time"],
                "gap_sec": r["gap_sec"],
                "reentry_pnl_yen": r["reentry_pnl_yen"],
                "blocked_symbol_count": len(blocked),
                "best_blocked_symbol": best_blocked["symbol"],
                "best_blocked_score_v2": best_blocked["entry_score_v2"],
                "best_blocked_cq": round(best_blocked["continuation_quality_score"], 4),
                "reentry_score_v2_proxy": "",
            }
        )

    re_pnl = sum(_float(r["reentry_pnl_yen"]) for r in interaction_rows)
    summary = {
        "interaction_window_sec": window_sec,
        "reentry_caused_cap_block_cases": len(interaction_rows),
        "reentry_trades_pnl_in_cases": round(re_pnl, 2),
        "note": "Temporal correlation: max_concurrent reject for other symbols within ±window of reentry",
    }
    return interaction_rows, summary


def _verdict(
    reentry_summary: Mapping[str, Any],
    priority_summary: Mapping[str, Any],
    interaction_summary: Mapping[str, Any],
) -> str:
    w300 = (reentry_summary.get("windows") or {}).get("within_300s") or {}
    re_pnl = _float(w300.get("total_pnl_yen"))
    non_pnl = _float((reentry_summary.get("non_reentry") or {}).get("total_pnl_yen"))
    re_avg = _float(w300.get("avg_pnl_yen"))
    non_avg = _float((reentry_summary.get("non_reentry") or {}).get("avg_pnl_yen"))
    high_cap = int(priority_summary.get("high_score_v2_ge5_cap_rejects") or 0)
    if high_cap > 0:
        return "priority_issue_found"
    if re_pnl < 0 and re_avg < non_avg:
        return "reentry_negative"
    if re_pnl > 0 and re_avg >= non_avg:
        return "reentry_positive"
    if int(interaction_summary.get("reentry_caused_cap_block_cases") or 0) > 5:
        return "priority_issue_found"
    return "no_issue"


def run_phase431_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    base = kabu / "results" / "small_paper" / TARGET_DAY
    session_dirs = [base / d for d in SESSION_DIRS if (base / d).is_dir()]

    all_trades: list[dict[str, Any]] = []
    for sd in session_dirs:
        all_trades.extend(_load_structural_trades(sd))

    reentry_rows, reentry_summary = _analyze_reentry(all_trades)
    priority_rows, cap_reject_rows, priority_summary = _analyze_entry_priority(session_dirs)
    interaction_rows, interaction_summary = _analyze_interaction(reentry_rows, session_dirs)

    w = reentry_summary.get("windows") or {}
    verdict = _verdict(reentry_summary, priority_summary, interaction_summary)

    mandatory = {
        "1_reentry_favorable_or_not": (
            "unfavorable"
            if _float((w.get("within_300s") or {}).get("avg_pnl_yen")) < _float((reentry_summary.get("non_reentry") or {}).get("avg_pnl_yen"))
            else "favorable"
        ),
        "2_ban_candidate": _float((w.get("within_300s") or {}).get("total_pnl_yen")) < 0,
        "3_priority_logic": "scan batch: rank_score; CAP full: PUSH arrival order",
        "4_score_or_fifo": "within-scan: score rank; CAP: FIFO (PUSH order)",
        "5_high_score_miss_examples": int(priority_summary.get("high_score_v2_ge5_cap_rejects") or 0),
        "6_cap5_impact": {
            "max_concurrent_rejects": priority_summary.get("max_concurrent_reject_count"),
            "reentry_cap_interactions": interaction_summary.get("reentry_caused_cap_block_cases"),
        },
        "7_improvement_room": (
            "Consider reentry cooloff after EXIT; rank_score tie-break audit; "
            "CAP queue by score when slots free"
        ),
    }

    part_a_mandatory = {
        f"count_within_{ws}s": int((w.get(f"within_{ws}s") or {}).get("count") or 0)
        for ws in REENTRY_WINDOWS
    }
    part_a_mandatory.update(
        {
            f"pf_within_{ws}s": (w.get(f"within_{ws}s") or {}).get("profit_factor")
            for ws in REENTRY_WINDOWS
        }
    )
    part_a_mandatory.update(
        {
            f"pnl_within_{ws}s": (w.get(f"within_{ws}s") or {}).get("total_pnl_yen")
            for ws in REENTRY_WINDOWS
        }
    )

    summary = {
        "phase": "431-Entry-Priority-Reentry-Audit",
        "generated_at": _now_iso(),
        "target_date": TARGET_DAY,
        "verdict": verdict,
        "sessions": [sd.name for sd in session_dirs],
        "part_a_immediate_reentry": reentry_summary,
        "part_a_mandatory": part_a_mandatory,
        "part_b_entry_priority": priority_summary,
        "part_c_interaction": interaction_summary,
        "mandatory_answers": mandatory,
        "runtime_logic_reference": {
            "evaluate_entry": "research/exposure_gate.py ExposureGate.evaluate_entry",
            "scan_batch": "small_paper/entry_scan_controller.py EntryScanController._flush_locked",
            "rank_score": "small_paper/entry_scan_controller.py candidate_rank_score",
            "max_entries_per_scan": 1,
            "entry_scan_window_sec": 2.0,
        },
    }

    return {
        "summary": summary,
        "_reentry_rows": reentry_rows,
        "_priority_rows": priority_rows,
        "_cap_reject_rows": cap_reject_rows,
        "_interaction_rows": interaction_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    pa = s.get("part_a_immediate_reentry") or {}
    pb = s.get("part_b_entry_priority") or {}
    pc = s.get("part_c_interaction") or {}
    m = s.get("mandatory_answers") or {}
    pm = s.get("part_a_mandatory") or {}
    lines = [
        "# Phase431 — Entry Priority & Immediate Reentry Audit",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Target: {s.get('target_date')}",
        f"Verdict: **{s.get('verdict')}**",
        "",
        "## Part A — Immediate Reentry",
        "",
        "| window | count | PF | total PnL | avg PnL | win_rate |",
        "|--------|-------|-----|-----------|---------|----------|",
    ]
    for ws in REENTRY_WINDOWS:
        wk = (pa.get("windows") or {}).get(f"within_{ws}s") or {}
        lines.append(
            f"| ≤{ws}s | {wk.get('count')} | {wk.get('profit_factor')} | "
            f"{wk.get('total_pnl_yen')} | {wk.get('avg_pnl_yen')} | {wk.get('win_rate')} |"
        )
    nr = pa.get("non_reentry") or {}
    lines.extend(
        [
            "",
            f"Non-reentry trades: count={nr.get('count')} PF={nr.get('profit_factor')} "
            f"total={nr.get('total_pnl_yen')} avg={nr.get('avg_pnl_yen')}",
            "",
            "## Part B — Entry Priority",
            "",
            f"- within-scan order: **{pb.get('adoption_order_within_scan')}**",
            f"- CAP-full order: **{pb.get('adoption_order_across_pushes_cap_full')}**",
            f"- candidate queue: **{pb.get('candidate_queue_exists')}** ({pb.get('queue_type')})",
            f"- max_concurrent rejects: **{pb.get('max_concurrent_reject_count')}**",
            f"- high score (v2≥5) CAP rejects: **{pb.get('high_score_v2_ge5_cap_rejects')}**",
            f"- max_entries_per_scan rejects: **{pb.get('max_entries_per_scan_rejects')}**",
            "",
            "## Part C — Reentry × Priority Interaction",
            "",
            f"- correlated cases: **{pc.get('reentry_caused_cap_block_cases')}**",
            f"- reentry PnL in those cases: **{pc.get('reentry_trades_pnl_in_cases')}** yen",
            "",
            "## 必須回答",
            "",
        ]
    )
    for k, v in m.items():
        lines.append(f"- {k}: {v}")
    lines.extend(["", "### Part A counts", ""])
    for ws in REENTRY_WINDOWS:
        lines.append(f"- ≤{ws}s: count={pm.get(f'count_within_{ws}s')} PF={pm.get(f'pf_within_{ws}s')} PnL={pm.get(f'pnl_within_{ws}s')}")
    return "\n".join(lines)


REENTRY_FIELDS = [
    "symbol",
    "prev_close_time",
    "reentry_time",
    "gap_sec",
    "prev_pnl_yen",
    "reentry_pnl_yen",
    "reentry_hold_sec",
    "reentry_close_reason",
    "session",
]

PRIORITY_FIELDS = [
    "session",
    "scan_id",
    "symbol",
    "entry_signal_ts",
    "entry_decision",
    "reject_reason",
    "entry_score_v2",
    "same_scan_rank",
    "same_scan_candidates",
    "message_index",
    "eval_start_ts",
    "rank_by_score",
]

INTERACTION_FIELDS = [
    "reentry_symbol",
    "reentry_time",
    "gap_sec",
    "reentry_pnl_yen",
    "blocked_symbol_count",
    "best_blocked_symbol",
    "best_blocked_score_v2",
    "best_blocked_cq",
]


@dataclass
class Phase431Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase431_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "summary": reports / "phase431_entry_priority_summary.json",
            "reentry": reports / "phase431_immediate_reentry_audit.csv",
            "priority": reports / "phase431_entry_priority_audit.csv",
            "interaction": reports / "phase431_reentry_priority_interaction.csv",
            "report": kabu / "docs" / "operations" / "phase431_entry_priority_reentry_report.md",
        }
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["reentry"], REENTRY_FIELDS, result.get("_reentry_rows") or [])
        prio = list(result.get("_priority_rows") or []) + [
            {**r, "scan_id": "", "entry_signal_ts": r.get("event_time"), "entry_decision": False, "reject_reason": "max_concurrent"}
            for r in (result.get("_cap_reject_rows") or [])
        ]
        _write_csv(paths["priority"], PRIORITY_FIELDS, prio)
        _write_csv(paths["interaction"], INTERACTION_FIELDS, result.get("_interaction_rows") or [])
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
