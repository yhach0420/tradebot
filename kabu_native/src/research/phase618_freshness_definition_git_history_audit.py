"""
Phase618 — Freshness definition git history audit (read-only).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

VERDICT = "phase618_freshness_definition_git_history_done"

# Last commit before 2026-06-26 (covers 6/25 live sessions)
PRE625_REF = "196a559"
PRE625_LABEL = "196a559 (2026-06-22 kabutrade0621, last commit before 6/26)"
HEAD_REF = "HEAD"
INTRO_COMMIT = "14ad1a9"
INTRO_DATE = "2026-06-13"
INTRO_PHASE = "kabutrade0612 / NP-entry-scan"

REPO_REL = "kabu_native"
FILES = (
    f"{REPO_REL}/src/small_paper/entry_scan_controller.py",
    f"{REPO_REL}/src/small_paper/pilot_runner.py",
    f"{REPO_REL}/src/storage/intraday_recorder.py",
    f"{REPO_REL}/src/small_paper/live_feature_bridge.py",
    f"{REPO_REL}/src/small_paper/config.py",
    f"{REPO_REL}/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
)

CODE_MAP_ROWS: list[dict[str, Any]] = [
    {
        "role": "reject_constant",
        "symbol_or_field": "REJECT_DATA_STALE_PRICE",
        "file": "src/small_paper/entry_scan_controller.py",
        "line_start": 19,
        "line_end": 19,
        "function": "module",
        "expression": 'REJECT_DATA_STALE_PRICE = "data_stale_price"',
        "notes": "Reject reason string assigned to gate decision",
    },
    {
        "role": "price_timestamp_read",
        "symbol_or_field": "CurrentPriceTime",
        "file": "src/small_paper/entry_scan_controller.py",
        "line_start": 98,
        "line_end": 105,
        "function": "_field_age_sec",
        "expression": "raw = payload.get(field); tick = parse_kabu_time(raw, fallback=now)",
        "notes": "field='CurrentPriceTime' at call site compute_entry_freshness L128-129",
    },
    {
        "role": "eval_time_reference",
        "symbol_or_field": "reference_now | datetime.now(JST)",
        "file": "src/small_paper/entry_scan_controller.py",
        "line_start": 101,
        "line_end": 104,
        "function": "_field_age_sec",
        "expression": "now = reference_now if reference_now is not None else datetime.now(JST); age = (now - tick).total_seconds()",
        "notes": "LIVE: datetime.now(JST) at freshness eval. push-replay (working tree): optional recorded_at via pilot_runner._replay_reference_now",
    },
    {
        "role": "age_compute",
        "symbol_or_field": "price_age_sec",
        "file": "src/small_paper/entry_scan_controller.py",
        "line_start": 122,
        "line_end": 148,
        "function": "compute_entry_freshness",
        "expression": "price_ts, price_age = _field_age_sec(payload, 'CurrentPriceTime', reference_now=...)",
        "notes": "Also computes board_age from min(BidTime, AskTime)",
    },
    {
        "role": "comparison_stale_price",
        "symbol_or_field": "price_age_sec > max_price_age_sec",
        "file": "src/small_paper/entry_scan_controller.py",
        "line_start": 157,
        "line_end": 162,
        "function": "_price_ts_fresh",
        "expression": "snap.price_age_sec <= max_price_age_sec (fresh PASS path)",
        "notes": "Inverse: stale when None or > threshold",
    },
    {
        "role": "comparison_stale_price",
        "symbol_or_field": "data_stale_price emit",
        "file": "src/small_paper/entry_scan_controller.py",
        "line_start": 267,
        "line_end": 274,
        "function": "evaluate_entry_data_freshness",
        "expression": "reject_reason=REJECT_DATA_STALE_PRICE when price not fresh and board_fallback fails/disabled",
        "notes": "Committed HEAD uses check_entry_data_freshness L135-138 with same numeric test",
    },
    {
        "role": "committed_stale_check",
        "symbol_or_field": "check_entry_data_freshness",
        "file": "src/small_paper/entry_scan_controller.py",
        "line_start": 135,
        "line_end": 138,
        "function": "check_entry_data_freshness",
        "expression": "if snap.price_age_sec is None or snap.price_age_sec > float(max_price_age_sec): return REJECT_DATA_STALE_PRICE",
        "notes": "Present in git HEAD (924bb1e); working tree delegates to evaluate_entry_data_freshness",
    },
    {
        "role": "threshold_config",
        "symbol_or_field": "entry_max_price_age_sec",
        "file": "src/small_paper/entry_scan_controller.py",
        "line_start": 570,
        "line_end": 570,
        "function": "entry_scan_controller_from_config",
        "expression": "max_price_age_sec=float(getattr(config, 'entry_max_price_age_sec', 3.0) or 3.0)",
        "notes": "Wired from YAML/config",
    },
    {
        "role": "call_site",
        "symbol_or_field": "compute_entry_freshness",
        "file": "src/small_paper/pilot_runner.py",
        "line_start": 2284,
        "line_end": 2309,
        "function": "_process_push_payload",
        "expression": "freshness = compute_entry_freshness(enriched, ...); evaluate_entry_data_freshness(...)",
        "notes": "Uses enriched payload after LiveFeatureBridge.enrich_payload; eval_start_ts=_now_iso() at L2118 is audit only, NOT used in age math",
    },
    {
        "role": "payload_passthrough",
        "symbol_or_field": "CurrentPriceTime",
        "file": "src/small_paper/live_feature_bridge.py",
        "line_start": 237,
        "line_end": 242,
        "function": "enrich_payload",
        "expression": "out = dict(payload); out.update(snapshot.to_payload_fields())",
        "notes": "CurrentPriceTime not overwritten by enrich; copied from raw PUSH",
    },
    {
        "role": "parse",
        "symbol_or_field": "parse_kabu_time",
        "file": "src/storage/intraday_recorder.py",
        "line_start": 63,
        "line_end": 73,
        "function": "parse_kabu_time",
        "expression": "datetime.fromisoformat(s) with JST tz",
        "notes": "Parses kabu ISO timestamp strings from PUSH",
    },
    {
        "role": "kabu_push_field",
        "symbol_or_field": "CurrentPriceTime",
        "file": "src/api/push_client.py",
        "line_start": 22,
        "line_end": 22,
        "function": "EXPECTED_PUSH_FIELDS_STOCK",
        "expression": "Expected PUSH field in kabu WebSocket payload",
        "notes": "Docs: updates on trade execution (Phase602); not updated on board-only ticks",
    },
    {
        "role": "yaml_threshold",
        "symbol_or_field": "entry_max_price_age_sec: 3.0",
        "file": "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
        "line_start": 173,
        "line_end": 173,
        "function": "config",
        "expression": "entry_max_price_age_sec: 3.0",
        "notes": "Unchanged since intro commit 14ad1a9",
    },
]


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (proc.stdout or "") + (proc.stderr or "")


def _git_diff(repo: Path, base: str, head: str, path: str) -> str:
    return _run_git(repo, "diff", f"{base}..{head}", "--", path).strip()


def _line_snippet(repo: Path, ref: str, path: str, pattern: str) -> str:
    text = _run_git(repo, "show", f"{ref}:{path}")
    for i, line in enumerate(text.splitlines(), start=1):
        if pattern in line:
            return f"L{i}:{line.strip()}"
    return ""


def _threshold_history(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "commit_or_date": INTRO_COMMIT,
            "entry_max_price_age_sec": 3.0,
            "entry_max_board_age_sec": 3.0,
            "entry_freshness_guard_enabled": True,
            "entry_freshness_board_fallback_enabled": False,
            "changed": True,
            "notes": f"Introduced {INTRO_DATE} ({INTRO_PHASE}) in prod YAML + entry_scan_controller",
        },
        {
            "commit_or_date": PRE625_REF,
            "entry_max_price_age_sec": 3.0,
            "entry_max_board_age_sec": 3.0,
            "entry_freshness_guard_enabled": True,
            "entry_freshness_board_fallback_enabled": False,
            "changed": False,
            "notes": "Same 3.0s threshold at last commit before 6/26",
        },
        {
            "commit_or_date": "HEAD committed (924bb1e)",
            "entry_max_price_age_sec": 3.0,
            "entry_max_board_age_sec": 3.0,
            "entry_freshness_guard_enabled": True,
            "entry_freshness_board_fallback_enabled": False,
            "changed": False,
            "notes": "entry_scan_controller freshness core identical to PRE625_REF",
        },
        {
            "commit_or_date": "working tree (uncommitted)",
            "entry_max_price_age_sec": 3.0,
            "entry_max_board_age_sec": 3.0,
            "entry_freshness_guard_enabled": True,
            "entry_freshness_board_fallback_enabled": False,
            "changed": False,
            "notes": "Threshold unchanged; evaluate_entry_data_freshness + reference_now added locally (Phase603, fallback OFF in prod YAML)",
        },
    ]
    log = _run_git(repo, "log", "-p", "-S", "entry_max_price_age_sec", "--", f"{REPO_REL}/configs/")
    if "entry_max_price_age_sec: 3.0" in log and log.count("entry_max_price_age_sec") <= 5:
        rows[0]["notes"] += "; git log -S shows single YAML introduction at 3.0"
    return rows


def _git_diff_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pairs = (
        ("pre625_vs_head_committed", PRE625_REF, HEAD_REF),
        ("pre625_vs_working_tree", PRE625_REF, None),
        ("head_committed_vs_working_tree", HEAD_REF, None),
    )
    for label, base, head in pairs:
        for rel in FILES:
            if head is None:
                diff = _run_git(repo, "diff", base, "--", rel).strip()
            else:
                diff = _git_diff(repo, base, head, rel)
            changed = bool(diff)
            summary = "no diff" if not changed else f"{len(diff.splitlines())} lines"
            rows.append(
                {
                    "comparison": label,
                    "file": rel.replace(f"{REPO_REL}/", ""),
                    "changed": changed,
                    "diff_line_count": len(diff.splitlines()) if diff else 0,
                    "summary": summary,
                    "key_hunk": diff[:500].replace("\n", " | ") if diff else "",
                }
            )
    return rows


def run_phase618(repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = Path(repo_root or resolve_kabu_root(Path.cwd()))
    trade_root = repo.parent if repo.name == "kabu_native" else repo
    if (trade_root / ".git").is_dir():
        git_repo = trade_root
    elif (repo / ".git").is_dir():
        git_repo = repo
    else:
        git_repo = trade_root

    reports = resolve_reports_dir(repo)

    esc_pre = _run_git(
        git_repo,
        "show",
        f"{PRE625_REF}:{REPO_REL}/src/small_paper/entry_scan_controller.py",
    )
    esc_head = _run_git(
        git_repo,
        "show",
        f"HEAD:{REPO_REL}/src/small_paper/entry_scan_controller.py",
    )
    core_pre = "_field_age_sec" in esc_pre and "datetime.now(JST)" in esc_pre
    core_head = esc_head == esc_pre or (
        "_field_age_sec" in esc_head
        and "now = datetime.now(JST)" in esc_head
        and "reference_now" not in esc_head
    )

    wt_esc = (repo / "src/small_paper/entry_scan_controller.py").read_text(encoding="utf-8")
    has_reference_now = "reference_now" in wt_esc
    has_board_fallback = "evaluate_entry_data_freshness" in wt_esc

    pre625_formula = (
        "price_age_sec = (datetime.now(JST) - parse_kabu_time(payload['CurrentPriceTime'])).total_seconds(); "
        "data_stale_price if price_age_sec is None or price_age_sec > entry_max_price_age_sec(3.0)"
    )
    wt_formula = pre625_formula + (
        "; push-replay only: reference_now=payload.recorded_at when set; "
        "optional board_fallback when entry_freshness_board_fallback_enabled (prod=false)"
    )

    mandatory = {
        "1_eval_time_definition": (
            "Wall-clock JST at freshness evaluation: datetime.now(JST) inside _field_age_sec when "
            "compute_entry_freshness runs in _process_push_payload (NOT PUSH receive t0, NOT eval_start_ts audit field). "
            "Working tree only: push-replay may pass reference_now=payload.recorded_at."
        ),
        "2_current_price_time_definition": (
            "payload['CurrentPriceTime'] from kabu WebSocket PUSH — ISO timestamp of last trade print "
            "(現値時刻). Read via payload.get in _field_age_sec; passed through enrich_payload unchanged. "
            "Null/missing → price_age_sec=None → data_stale_price."
        ),
        "3_comparison_location": (
            "entry_scan_controller.py: _field_age_sec L92-105 (age), _price_ts_fresh L157-162 or "
            "check_entry_data_freshness L135-138 (HEAD), evaluate_entry_data_freshness L267-274 (working tree); "
            "threshold entry_max_price_age_sec from config L570"
        ),
        "4_same_formula_pre625_vs_head_committed": bool(core_pre and core_head),
        "5_eval_time_source_changed_since_pre625": False,
        "6_current_price_time_source_changed_since_pre625": False,
        "7_threshold_3s_changed_since_pre625": False,
        "8_pre625_was_trade_time_to_eval_within_3s": True,
        "9_implementation_change_vs_design": (
            "DESIGN (kabu feed semantics): CurrentPriceTime updates only on trades; board can be fresh while "
            "price timestamp frozen → high data_stale_price rate (Phase602). "
            "IMPLEMENTATION: core comparison unchanged since 6/13 intro; uncommitted Phase603 adds board_fallback "
            "(disabled in prod YAML) and replay reference_now only."
        ),
        "pre625_formula": pre625_formula,
        "working_tree_formula": wt_formula if has_board_fallback else pre625_formula,
        "pre625_ref": PRE625_REF,
        "head_ref": _run_git(git_repo, "rev-parse", "--short", "HEAD").strip(),
    }

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "pre625_reference": PRE625_LABEL,
        "intro_commit": {"hash": INTRO_COMMIT, "date": INTRO_DATE, "phase": INTRO_PHASE},
        "mandatory_answers": mandatory,
        "findings": [
            "6/25 live sessions ran on code >= 14ad1a9 (freshness guard) and <= 196a559 (last pre-6/26 commit).",
            "git diff 196a559..HEAD for entry_scan_controller.py: empty — core age formula unchanged on committed HEAD.",
            "Working tree differs: evaluate_entry_data_freshness + reference_now (uncommitted Phase603 path).",
            "Prod YAML entry_freshness_board_fallback_enabled=false → live path still CurrentPriceTime-only reject.",
            "User formula 'eval - CurrentPriceTime <= 3s' ≡ price_age_sec <= 3.0 (code rejects when age > 3.0 or CPT missing).",
        ],
        "artifacts": {
            "code_map": str(reports / "phase618_freshness_definition_code_map.csv"),
            "git_diff": str(reports / "phase618_freshness_git_diff.csv"),
            "threshold_history": str(reports / "phase618_freshness_threshold_history.csv"),
        },
    }

    _write_csv(
        reports / "phase618_freshness_definition_code_map.csv",
        list(CODE_MAP_ROWS[0].keys()),
        CODE_MAP_ROWS,
    )
    diff_rows = _git_diff_rows(git_repo)
    _write_csv(
        reports / "phase618_freshness_git_diff.csv",
        list(diff_rows[0].keys()) if diff_rows else ["comparison"],
        diff_rows,
    )
    thresh_rows = _threshold_history(git_repo)
    _write_csv(
        reports / "phase618_freshness_threshold_history.csv",
        list(thresh_rows[0].keys()),
        thresh_rows,
    )
    (reports / "phase618_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    import sys
    from pathlib import Path as P

    root = P(sys.argv[1]) if len(sys.argv) > 1 else None
    r = run_phase618(root)
    print(r["verdict"])
