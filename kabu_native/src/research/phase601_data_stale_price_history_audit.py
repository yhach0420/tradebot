"""
Phase601: data_stale_price introduction history audit (read-only).
"""

from __future__ import annotations

import csv
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

VERDICT = "phase601_data_stale_price_history_audit_done"
INTRO_COMMIT = "14ad1a9"
INTRO_DATE = "2026-06-13"
INTRO_PHASE = "NP-entry-scan (kabutrade0612); observability Phase490"

SESSIONS = [
    ("20260624", "live_session_081514", "AM"),
    ("20260624", "live_session_122521", "PM"),
    ("20260625", "live_session_080340", "AM"),
    ("20260625", "live_session_122535", "PM"),
    ("20260629", "live_session_080236", "AM"),
    ("20260629", "live_session_122526", "PM"),
]

HISTORY_FIELDS = [
    "item",
    "value",
    "source",
    "notes",
]
THRESHOLD_FIELDS = [
    "commit_or_date",
    "entry_max_price_age_sec",
    "entry_max_board_age_sec",
    "live_stale_tick_sec",
    "entry_freshness_guard_enabled",
    "changed",
    "notes",
]
DAILY_RATE_FIELDS = [
    "day",
    "session",
    "period",
    "candidate_count",
    "data_stale_price",
    "data_stale_board",
    "stale_price_pct",
    "stale_board_pct",
    "stale_combined_pct",
    "accepted_count",
    "pbv2_accept",
    "or_accept",
    "notes",
]
SYMBOL_FIELDS = [
    "day",
    "session",
    "symbol",
    "stale_price_reject_count",
    "stale_board_reject_count",
    "total_eval_count",
    "median_price_age_sec",
    "max_price_age_sec",
    "median_board_age_sec",
    "sample_last_price_ts",
    "sample_last_board_ts",
    "universe_bucket",
    "notes",
]
TIMESTAMP_FIELDS = [
    "topic",
    "field_or_function",
    "source",
    "compared_to",
    "used_for_reject",
    "notes",
]


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout or proc.stderr or ""


def _load_summary(sp: Path, day: str, sess: str) -> dict[str, Any]:
    p = sp / day / sess / "small_paper_summary.json"
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _count_accepts(sp: Path, day: str, sess: str) -> tuple[int, int]:
    pb = or_c = 0
    p = sp / day / sess / "small_paper_events.jsonl"
    if not p.is_file():
        return 0, 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("event_type") != "accepted":
            continue
        et = str(ev.get("entry_type") or "PBV2").upper()
        if et == "OR_OVERLAY":
            or_c += 1
        else:
            pb += 1
    return pb, or_c


def _audit_rows(sp: Path, day: str, sess: str) -> list[dict[str, Any]]:
    p = sp / day / sess / "entry_scan_audit.jsonl"
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("audit_type") != "entry_symbol_eval":
            continue
        out.append(row)
    return out


def _symbol_breakdown(
    sp: Path,
    day: str,
    sess: str,
    period: str,
    *,
    focus_symbols: Optional[Sequence[str]] = None,
) -> list[dict[str, Any]]:
    rows = _audit_rows(sp, day, sess)
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_sym[str(r.get("symbol") or "")].append(r)

    stale_counts: Counter[str] = Counter()
    board_counts: Counter[str] = Counter()
    for r in rows:
        sym = str(r.get("symbol") or "")
        reason = str(r.get("reject_reason") or "")
        if reason == "data_stale_price":
            stale_counts[sym] += 1
        elif reason == "data_stale_board":
            board_counts[sym] += 1

    symbols = set(stale_counts.keys()) | set(board_counts.keys())
    if focus_symbols:
        symbols |= set(focus_symbols)

    out: list[dict[str, Any]] = []
    for sym in sorted(symbols):
        evals = by_sym.get(sym, [])
        ages = [float(r["price_age_sec"]) for r in evals if r.get("price_age_sec") is not None]
        bages = [float(r["board_age_sec"]) for r in evals if r.get("board_age_sec") is not None]
        sample = next((r for r in evals if r.get("reject_reason") == "data_stale_price"), evals[0] if evals else {})
        note = ""
        if ages and max(ages) > 300 and bages and min(bages) < 5:
            note = "price_ts_stale_board_fresh"
        out.append(
            {
                "day": day,
                "session": period,
                "symbol": sym,
                "stale_price_reject_count": stale_counts.get(sym, 0),
                "stale_board_reject_count": board_counts.get(sym, 0),
                "total_eval_count": len(evals),
                "median_price_age_sec": round(sorted(ages)[len(ages) // 2], 2) if ages else "",
                "max_price_age_sec": round(max(ages), 2) if ages else "",
                "median_board_age_sec": round(sorted(bages)[len(bages) // 2], 2) if bages else "",
                "sample_last_price_ts": sample.get("last_price_update_ts", ""),
                "sample_last_board_ts": sample.get("last_board_update_ts", ""),
                "universe_bucket": "",
                "notes": note,
            }
        )
    out.sort(key=lambda r: (-int(r["stale_price_reject_count"]), str(r["symbol"])))
    return out


class Phase601AuditJob:
    def __init__(self, repo_root: Path) -> None:
        self.repo = repo_root
        self.kabu = resolve_kabu_root(repo_root)
        self.reports = resolve_reports_dir(self.kabu)
        self.sp = self.kabu / "results" / "small_paper"

    def run(self) -> dict[str, Any]:
        history = self._history_rows()
        threshold = self._threshold_history()
        daily = self._daily_rates()
        sym_629 = self._symbol_breakdown_629()
        ts_audit = self._timestamp_audit()
        mandatory = self._mandatory(daily, sym_629)

        return {
            "verdict": VERDICT,
            "generated_at": _now_iso(),
            "history": history,
            "threshold_history": threshold,
            "daily_rates": daily,
            "symbol_breakdown_629": sym_629,
            "timestamp_audit": ts_audit,
            "mandatory_answers": mandatory,
        }

    def _history_rows(self) -> list[dict[str, Any]]:
        return [
            {"item": "first_commit", "value": INTRO_COMMIT, "source": "git log -S data_stale_price", "notes": "kabutrade0612"},
            {"item": "first_date", "value": INTRO_DATE, "source": "git show 14ad1a9", "notes": ""},
            {"item": "phase", "value": INTRO_PHASE, "source": "docs/audits/full_phase_history_audit.csv", "notes": "Phase490 counts rejects; guard from NP-entry-scan"},
            {"item": "implementation_file", "value": "kabu_native/src/small_paper/entry_scan_controller.py", "source": "code", "notes": ""},
            {"item": "reject_function", "value": "check_entry_data_freshness()", "source": "entry_scan_controller.py", "notes": "Before ExposureGate"},
            {"item": "freshness_compute", "value": "compute_entry_freshness()", "source": "entry_scan_controller.py", "notes": "CurrentPriceTime, BidTime, AskTime"},
            {"item": "wired_in", "value": "pilot_runner._process_push_payload()", "source": "pilot_runner.py", "notes": "Before _evaluate_gate_entry PBv2"},
            {"item": "reject_constant", "value": "REJECT_DATA_STALE_PRICE = data_stale_price", "source": "entry_scan_controller.py:19", "notes": ""},
            {"item": "yaml_control", "value": "entry_max_price_age_sec, entry_max_board_age_sec, entry_freshness_guard_enabled", "source": "production YAML", "notes": "Rollback: entry_freshness_guard_enabled=false"},
            {"item": "separate_stat_only", "value": "live.stale_tick_sec=120", "source": "pilot_runner _tick_age_sec", "notes": "Increments stale_tick_count; NOT data_stale_price reject"},
        ]

    def _threshold_history(self) -> list[dict[str, Any]]:
        rows = [
            {
                "commit_or_date": INTRO_DATE,
                "entry_max_price_age_sec": 3.0,
                "entry_max_board_age_sec": 3.0,
                "live_stale_tick_sec": 120,
                "entry_freshness_guard_enabled": True,
                "changed": False,
                "notes": "Initial NP-entry-scan introduction",
            },
            {
                "commit_or_date": "2026-06-29 HEAD",
                "entry_max_price_age_sec": 3.0,
                "entry_max_board_age_sec": 3.0,
                "live_stale_tick_sec": 120,
                "entry_freshness_guard_enabled": True,
                "changed": False,
                "notes": "No threshold change in git history since 14ad1a9",
            },
        ]
        diff = _run_git(self.repo, "log", "-p", "-S", "entry_max_price_age_sec", "--", "kabu_native/configs/")
        if "entry_max_price_age_sec: 3.0" in diff and diff.count("entry_max_price_age_sec") <= 3:
            rows.append(
                {
                    "commit_or_date": "git_audit",
                    "entry_max_price_age_sec": 3.0,
                    "entry_max_board_age_sec": 3.0,
                    "live_stale_tick_sec": 120,
                    "entry_freshness_guard_enabled": True,
                    "changed": False,
                    "notes": "git log -S entry_max_price_age_sec shows single introduction at 3.0",
                }
            )
        return rows

    def _daily_rates(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for day, sess, period in SESSIONS:
            summ = _load_summary(self.sp, day, sess)
            rc = summ.get("reject_reason_counts") or {}
            cand = int(summ.get("candidate_count") or summ.get("gate_evaluations") or 0)
            sp_rej = int(rc.get("data_stale_price") or 0)
            sb_rej = int(rc.get("data_stale_board") or 0)
            acc = int(summ.get("accepted_count") or 0)
            pb, or_c = _count_accepts(self.sp, day, sess)
            rows.append(
                {
                    "day": day,
                    "session": sess,
                    "period": period,
                    "candidate_count": cand,
                    "data_stale_price": sp_rej,
                    "data_stale_board": sb_rej,
                    "stale_price_pct": round(100 * sp_rej / cand, 2) if cand else 0,
                    "stale_board_pct": round(100 * sb_rej / cand, 2) if cand else 0,
                    "stale_combined_pct": round(100 * (sp_rej + sb_rej) / cand, 2) if cand else 0,
                    "accepted_count": acc,
                    "pbv2_accept": pb,
                    "or_accept": or_c,
                    "notes": "",
                }
            )
        return rows

    def _symbol_breakdown_629(self) -> list[dict[str, Any]]:
        focus = ["4265.T"]
        am = _symbol_breakdown(self.sp, "20260629", "live_session_080236", "AM", focus_symbols=focus)
        pm = _symbol_breakdown(self.sp, "20260629", "live_session_122526", "PM", focus_symbols=focus)
        combined = am + pm
        return combined[:200]

    def _timestamp_audit(self) -> list[dict[str, Any]]:
        return [
            {
                "topic": "price_timestamp_field",
                "field_or_function": "payload.CurrentPriceTime",
                "source": "kabu WebSocket PUSH",
                "compared_to": "datetime.now(JST) at eval",
                "used_for_reject": "data_stale_price if age>entry_max_price_age_sec(3)",
                "notes": "_field_age_sec in entry_scan_controller.py",
            },
            {
                "topic": "board_timestamp_field",
                "field_or_function": "payload.BidTime / AskTime (min age)",
                "source": "kabu WebSocket PUSH board",
                "compared_to": "datetime.now(JST) at eval",
                "used_for_reject": "data_stale_board if age>entry_max_board_age_sec(3)",
                "notes": "Separate from price; both must pass",
            },
            {
                "topic": "price_vs_board_independent",
                "field_or_function": "check_entry_data_freshness order",
                "source": "entry_scan_controller.py",
                "compared_to": "N/A",
                "used_for_reject": "price checked first, then board",
                "notes": "4265.T 6/29 PM: board_age~0.3s price_age~9937s",
            },
            {
                "topic": "push_replay_clock",
                "field_or_function": "compute_entry_freshness uses now()",
                "source": "push-replay dry run",
                "compared_to": "recorded push CurrentPriceTime from past session",
                "used_for_reject": "100% data_stale_price in Phase600 full replay",
                "notes": "Replay artifact; not live runtime regression",
            },
            {
                "topic": "rest_board_fallback",
                "field_or_function": "resolve_data_source poll->kabu_board",
                "source": "entry_scan_controller.resolve_data_source",
                "compared_to": "live/push-replay uses kabu_push",
                "used_for_reject": "No on live PUSH path",
                "notes": "REST board fallback not active on live WebSocket path",
            },
            {
                "topic": "phase590_600_changes",
                "field_or_function": "entry_scan_controller.py",
                "source": "git log f50c5a7..HEAD",
                "compared_to": "6/25 baseline",
                "used_for_reject": "none",
                "notes": "No edits to stale guard since 6/26; Phase591-594 post-accept only",
            },
        ]

    def _mandatory(
        self,
        daily: Sequence[Mapping[str, Any]],
        sym_629: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        rates = [float(r["stale_price_pct"]) for r in daily]
        avg_rate = sum(rates) / len(rates) if rates else 0
        r629 = [r for r in daily if r["day"] == "20260629"]
        r629_avg = sum(float(r["stale_price_pct"]) for r in r629) / max(len(r629), 1)
        r4265 = next((r for r in sym_629 if r.get("symbol") == "4265.T" and r.get("session") == "PM"), {})

        return {
            "1_introduced_when": INTRO_DATE,
            "2_phase": INTRO_PHASE,
            "3_current_trigger": "CurrentPriceTime missing OR age>entry_max_price_age_sec(3.0)",
            "4_current_threshold_sec": 3.0,
            "5_threshold_changed_since_intro": False,
            "6_active_before_0625": True,
            "7_0629_anomalously_high": abs(r629_avg - avg_rate) >= 8.0,
            "8_4265_stale_reason": (
                "CurrentPriceTime frozen ~10:11 while board Bid/Ask updates at eval time (price_age~9900s)"
                if r4265.get("notes") == "price_ts_stale_board_fresh"
                else str(r4265.get("notes") or "see symbol breakdown")
            ),
            "9_price_timestamp_source": "payload.CurrentPriceTime from kabu PUSH",
            "10_implementation_bug_likelihood": "low_for_guard_logic; feed_or_timestamp_staleness_for_some_symbols",
            "11_runtime_fix_needed": False,
            "12_next_phase": "phase602_push_replay_clock_parity_and_price_ts_fallback_study",
            "stale_price_pct_avg_all_days": round(avg_rate, 2),
            "stale_price_pct_avg_0629": round(r629_avg, 2),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        rep = self.reports
        rep.mkdir(parents=True, exist_ok=True)
        paths = {
            "history": rep / "phase601_data_stale_price_history.csv",
            "threshold": rep / "phase601_data_stale_price_threshold_history.csv",
            "daily": rep / "phase601_data_stale_price_daily_rate.csv",
            "symbol": rep / "phase601_data_stale_price_symbol_breakdown_20260629.csv",
            "timestamp": rep / "phase601_price_timestamp_source_audit.csv",
            "json": rep / "phase601_report.json",
        }
        _write_csv(paths["history"], HISTORY_FIELDS, result.get("history") or [])
        _write_csv(paths["threshold"], THRESHOLD_FIELDS, result.get("threshold_history") or [])
        _write_csv(paths["daily"], DAILY_RATE_FIELDS, result.get("daily_rates") or [])
        _write_csv(paths["symbol"], SYMBOL_FIELDS, result.get("symbol_breakdown_629") or [])
        _write_csv(paths["timestamp"], TIMESTAMP_FIELDS, result.get("timestamp_audit") or [])
        paths["json"].write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        doc = self.kabu / "docs" / "operations" / "phase601_data_stale_price_history_audit.md"
        ma = result.get("mandatory_answers") or {}
        doc.write_text(
            "\n".join(
                [
                    "# Phase601 data_stale_price Introduction History Audit",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    "",
                    "## Mandatory answers",
                    "",
                ]
                + [f"{i}. {ma.get(k)}" for i, k in enumerate(
                    [
                        "1_introduced_when",
                        "2_phase",
                        "3_current_trigger",
                        "4_current_threshold_sec",
                        "5_threshold_changed_since_intro",
                        "6_active_before_0625",
                        "7_0629_anomalously_high",
                        "8_4265_stale_reason",
                        "9_price_timestamp_source",
                        "10_implementation_bug_likelihood",
                        "11_runtime_fix_needed",
                        "12_next_phase",
                    ],
                    start=1,
                )]
                + ["", "## Outputs", ""]
                + [f"- `{p.name}`" for p in paths.values()]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths


def run_phase601(repo_root: Optional[Path] = None) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[3]
    job = Phase601AuditJob(root)
    result = job.run()
    paths = job.write_outputs(result)
    result["output_paths"] = {k: str(v) for k, v in paths.items()}
    return result
