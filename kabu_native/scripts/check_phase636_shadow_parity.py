#!/usr/bin/env python3
"""Phase636: Runtime parity after Phase635 PBv2 rise5 shadow guard.

Compares push-replay with pbv2_rise5_shadow_enabled=false vs true on the
Phase629A fixture days. Core ENTRY/EXIT behavior must match; only shadow
columns may differ.

Usage:
    python scripts/check_phase636_shadow_parity.py
    python scripts/check_phase636_shadow_parity.py --reuse
    python scripts/check_phase636_shadow_parity.py --days 2026-06-25

Exit codes:
    0  ALL_MATCH=True
    1  ALL_MATCH=False or setup error
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional

SCRIPT = Path(__file__).resolve()
NATIVE_ROOT = SCRIPT.parents[1]
REPO_ROOT = NATIVE_ROOT.parent

for p in (NATIVE_ROOT / "src", REPO_ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from check_runtime_parity import (  # noqa: E402
    CORE_METRIC_KEYS,
    DAYS,
    POLL_INTERVAL_SEC,
    PROD_YAML,
    VOLATILE_SUMMARY_KEYS,
    _count_csv_rows,
    _dir_size_mb,
    _extract_core_metrics,
    _load_summary,
    _purge_modules,
    _summary_behavior_diff,
    _volatile_only_diff,
)

RUN_ROOT = NATIVE_ROOT / "results" / "small_paper" / "_phase636"
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase636_shadow_parity"
PHASE636_VERDICT_DONE = "phase636_shadow_parity_done"
PHASE636_VERDICT_FAIL = "phase636_shadow_parity_failed"

PBV2_RISE5_SHADOW_EVENT_KEYS = frozenset(
    {
        "pbv2_rise5_shadow_block",
        "pbv2_rise5_shadow_reason",
        "pbv2_rise5_value",
        "pbv2_rise5_threshold",
        "pbv2_rise5_shadow_apply_pool",
        "shadow_blocked_pnl_yen_100",
        "shadow_blocked_mfe",
        "shadow_blocked_mae",
        "pbv2_rise5_shadow_pnl_yen_100",
        "pbv2_rise5_shadow_delta_yen",
    }
)

# Wall-clock / run-id fields on events (same as Phase630 summary volatile set + exit shadows)
VOLATILE_EVENT_KEYS = frozenset(
    {
        "event_time",
        "scan_id",
        "entry_signal_ts",
        "entry_signal_mono",
        "generated_at",
        "message_index",
        "hold_sec",
        "exit_time",
        "shadow_exit_time",
        "shadow_exit_price",
        "shadow_pnl_pct",
        "shadow_pnl_yen_100",
        "actual_vs_shadow_delta_yen",
        "actual_vs_shadow_delta_pct",
    }
)

BEHAVIOR_EVENT_TYPES = frozenset({"accepted", "observer_exit"})


def _is_pbv2_rise5_shadow_summary_key(key: str) -> bool:
    return key.startswith("pbv2_rise5_shadow_")


def _shadow_excluded_summary_keys() -> frozenset[str]:
    return VOLATILE_SUMMARY_KEYS


def _strip_shadow_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in row.items()
        if k not in PBV2_RISE5_SHADOW_EVENT_KEYS
        and k not in VOLATILE_EVENT_KEYS
        and not _is_pbv2_rise5_shadow_summary_key(k)
    }


def _behavior_event_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("event_type") or ""),
        str(row.get("symbol") or ""),
        str(row.get("entry_time") or ""),
    )


def _run_replay(*, day: str, label: str, shadow_enabled: bool) -> Path:
    _purge_modules()
    from small_paper.config import load_pilot_config
    from small_paper.pilot_runner import run_push_replay_dry_run
    import small_paper.pilot_runner as pr

    day_key = day.replace("-", "")
    staging_dir = Path(
        tempfile.mkdtemp(prefix=f"phase636_{day_key}_{label.lower()}_", dir=str(RUN_ROOT))
    )

    print(
        f"[phase636] REPLAY label={label} day={day} shadow={shadow_enabled} pilot={pr.__file__}",
        flush=True,
    )
    push_dir = NATIVE_ROOT / "data" / "push_jsonl" / day
    if not push_dir.is_dir():
        raise FileNotFoundError(f"push fixture not found: {push_dir}")

    cfg = replace(
        load_pilot_config(PROD_YAML),
        discord_enabled=False,
        entry_latency_trace_enabled=False,
        pbv2_rise5_shadow_enabled=shadow_enabled,
    )
    try:
        run_push_replay_dry_run(
            cfg,
            push_dir=push_dir,
            output_dir=staging_dir,
            repo_root=REPO_ROOT,
            poll_interval_sec=POLL_INTERVAL_SEC,
            streaming_push_replay=True,
            enable_discord=False,
            write_board_shadow_reports=False,
        )
        _validate_replay_dir(staging_dir)
        return staging_dir
    except Exception:
        _robust_rmtree(staging_dir)
        raise


def _robust_rmtree(path: Path, *, retries: int = 5) -> None:
    for attempt in range(retries):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except OSError:
            time.sleep(0.5 * (attempt + 1))
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _copy_out(src: Path, dst: Path) -> None:
    parent = dst.parent
    tmp = parent / f".{dst.name}.copy_tmp"
    _robust_rmtree(tmp)
    shutil.copytree(src, tmp)
    _robust_rmtree(dst)
    tmp.rename(dst)


def _validate_replay_dir(out_dir: Path) -> None:
    summary = _load_summary(out_dir)
    core = _extract_core_metrics(summary)
    accepted_events = sum(
        1
        for row in _load_events_jsonl(out_dir / "small_paper_events.jsonl")
        if row.get("event_type") == "accepted"
    )
    if accepted_events != int(core.get("accepted_total") or 0):
        raise RuntimeError(
            f"replay artifact mismatch in {out_dir}: "
            f"summary accepted={core.get('accepted_total')} events accepted={accepted_events}"
        )


def _load_events_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _compare_events(off_dir: Path, on_dir: Path) -> dict[str, Any]:
    off_rows = _load_events_jsonl(off_dir / "small_paper_events.jsonl")
    on_rows = _load_events_jsonl(on_dir / "small_paper_events.jsonl")

    def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        c: dict[str, int] = {}
        for r in rows:
            et = str(r.get("event_type") or "")
            c[et] = c.get(et, 0) + 1
        return c

    off_counts = _counts(off_rows)
    on_counts = _counts(on_rows)
    count_match = off_counts == on_counts

    off_behavior = [_strip_shadow_row(r) for r in off_rows if r.get("event_type") in BEHAVIOR_EVENT_TYPES]
    on_behavior = [_strip_shadow_row(r) for r in on_rows if r.get("event_type") in BEHAVIOR_EVENT_TYPES]

    off_by_key = {_behavior_event_key(r): r for r in off_behavior}
    on_by_key = {_behavior_event_key(r): r for r in on_behavior}
    keys_off = set(off_by_key)
    keys_on = set(on_by_key)
    missing_on = sorted(keys_off - keys_on)
    extra_on = sorted(keys_on - keys_off)

    field_diffs: list[dict[str, str]] = []
    for eid in sorted(keys_off & keys_on):
        a, b = off_by_key[eid], on_by_key[eid]
        keys = sorted(set(a) | set(b))
        for k in keys:
            if a.get(k) != b.get(k):
                field_diffs.append(
                    {
                        "event_type": eid[0],
                        "symbol": eid[1],
                        "entry_time": eid[2],
                        "field": k,
                        "shadow_off": json.dumps(a.get(k), ensure_ascii=False),
                        "shadow_on": json.dumps(b.get(k), ensure_ascii=False),
                    }
                )
                if len(field_diffs) >= 50:
                    break
        if len(field_diffs) >= 50:
            break

    behavior_match = not missing_on and not extra_on and not field_diffs
    return {
        "events_match": count_match and behavior_match,
        "event_count_off": len(off_rows),
        "event_count_on": len(on_rows),
        "event_type_counts_off": off_counts,
        "event_type_counts_on": on_counts,
        "accepted_off": off_counts.get("accepted", 0),
        "accepted_on": on_counts.get("accepted", 0),
        "behavior_event_count_off": len(off_behavior),
        "behavior_event_count_on": len(on_behavior),
        "behavior_keys_missing_on": missing_on[:20],
        "behavior_keys_extra_on": extra_on[:20],
        "behavior_field_diffs_sample": field_diffs,
    }


def _compare_positions(off_dir: Path, on_dir: Path) -> dict[str, Any]:
    off_fp = off_dir / "small_paper_positions.csv"
    on_fp = on_dir / "small_paper_positions.csv"
    off_rows = _count_csv_rows(off_fp)
    on_rows = _count_csv_rows(on_fp)
    content_match = True
    if off_fp.is_file() and on_fp.is_file():
        with off_fp.open(encoding="utf-8", newline="") as f1, on_fp.open(
            encoding="utf-8", newline=""
        ) as f2:
            content_match = list(csv.DictReader(f1)) == list(csv.DictReader(f2))
    return {
        "positions_match": off_rows == on_rows and content_match,
        "positions_rows_off": off_rows,
        "positions_rows_on": on_rows,
        "positions_content_match": content_match,
    }


def compare_day_dirs(day: str, off_dir: Path, on_dir: Path) -> dict[str, Any]:
    sum_off = _load_summary(off_dir)
    sum_on = _load_summary(on_dir)

    core_off = _extract_core_metrics(sum_off)
    core_on = _extract_core_metrics(sum_on)
    core_diffs = {
        k: {"shadow_off": core_off[k], "shadow_on": core_on[k]}
        for k in CORE_METRIC_KEYS
        if core_off.get(k) != core_on.get(k)
    }

    def _shadow_summary_diff(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
        diffs: dict[str, Any] = {}
        keys = sorted(set(a) | set(b))
        for k in keys:
            if k in _shadow_excluded_summary_keys() or _is_pbv2_rise5_shadow_summary_key(k):
                continue
            va, vb = a.get(k), b.get(k)
            if isinstance(va, float) and isinstance(vb, float):
                if round(va, 9) == round(vb, 9):
                    continue
            if va != vb:
                diffs[k] = {"shadow_off": va, "shadow_on": vb}
        return diffs

    summary_diffs = _shadow_summary_diff(sum_off, sum_on)
    events = _compare_events(off_dir, on_dir)
    positions = _compare_positions(off_dir, on_dir)

    core_match = not core_diffs
    summary_match = not summary_diffs
    events_match = bool(events["events_match"])
    positions_match = bool(positions["positions_match"])
    match = core_match and summary_match and events_match and positions_match

    shadow_only_summary = {
        k: {"shadow_off": sum_off.get(k), "shadow_on": sum_on.get(k)}
        for k in sorted(set(sum_off) | set(sum_on))
        if _is_pbv2_rise5_shadow_summary_key(k) and sum_off.get(k) != sum_on.get(k)
    }

    return {
        "day": day,
        "match": match,
        "core_metrics_match": core_match,
        "summary_match": summary_match,
        "events_match": events_match,
        "positions_match": positions_match,
        "core_metrics_off": core_off,
        "core_metrics_on": core_on,
        "core_diffs": core_diffs,
        "summary_diffs": summary_diffs,
        "events": events,
        "positions": positions,
        "shadow_only_summary_diffs": shadow_only_summary,
    }


def _write_by_day_csv(report_dir: Path, day_results: list[dict[str, Any]]) -> None:
    fp = report_dir / "phase636_parity_by_day.csv"
    rows = []
    for r in day_results:
        mo = r["core_metrics_off"]
        mn = r["core_metrics_on"]
        rows.append(
            {
                "day": r["day"],
                "match": r["match"],
                "core_metrics_match": r["core_metrics_match"],
                "summary_match": r["summary_match"],
                "events_match": r["events_match"],
                "positions_match": r["positions_match"],
                "OFF_accepted_total": mo["accepted_total"],
                "ON_accepted_total": mn["accepted_total"],
                "OFF_pbv2_accepted": mo["pbv2_accepted"],
                "ON_pbv2_accepted": mn["pbv2_accepted"],
                "OFF_or_accepted": mo["or_accepted"],
                "ON_or_accepted": mn["or_accepted"],
                "OFF_exits": mo["exits"],
                "ON_exits": mn["exits"],
                "OFF_pnl_yen_100": mo["pnl_yen_100"],
                "ON_pnl_yen_100": mn["pnl_yen_100"],
                "events_off": r["events"]["event_count_off"],
                "events_on": r["events"]["event_count_on"],
            }
        )
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["day"])
        w.writeheader()
        w.writerows(rows)


def _write_diff_gz(report_dir: Path, day_results: list[dict[str, Any]], *, all_match: bool) -> None:
    fp = report_dir / "phase636_diff_if_failed.csv.gz"
    rows: list[dict[str, str]] = []
    if not all_match:
        for r in day_results:
            if r["match"]:
                continue
            day = r["day"]
            for metric, pair in (r.get("core_diffs") or {}).items():
                rows.append(
                    {
                        "day": day,
                        "section": "core_metrics",
                        "field": metric,
                        "shadow_off": json.dumps(pair["shadow_off"], ensure_ascii=False),
                        "shadow_on": json.dumps(pair["shadow_on"], ensure_ascii=False),
                    }
                )
            for field, pair in (r.get("summary_diffs") or {}).items():
                rows.append(
                    {
                        "day": day,
                        "section": "summary",
                        "field": field,
                        "shadow_off": json.dumps(pair["shadow_off"], ensure_ascii=False),
                        "shadow_on": json.dumps(pair["shadow_on"], ensure_ascii=False),
                    }
                )
            for fd in r.get("events", {}).get("behavior_field_diffs_sample") or []:
                rows.append(
                    {
                        "day": day,
                        "section": "events",
                        "field": fd.get("field", ""),
                        "shadow_off": fd.get("shadow_off", ""),
                        "shadow_on": fd.get("shadow_on", ""),
                    }
                )
    with gzip.open(fp, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["day", "section", "field", "shadow_off", "shadow_on"])
        w.writeheader()
        w.writerows(rows)


def _write_shadow_whitelist(report_dir: Path) -> None:
    fp = report_dir / "phase636_shadow_field_whitelist.csv"
    rows = [
        {"category": "event", "field": k, "reason": "Phase635 PBv2 rise5 shadow only"}
        for k in sorted(PBV2_RISE5_SHADOW_EVENT_KEYS)
    ]
    rows.extend(
        {"category": "event_volatile", "field": k, "reason": "wall clock / run id / exit shadow timing"}
        for k in sorted(VOLATILE_EVENT_KEYS)
    )
    rows.append(
        {
            "category": "summary",
            "field": "pbv2_rise5_shadow_*",
            "reason": "Phase635 summary aggregates",
        }
    )
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["category", "field", "reason"])
        w.writeheader()
        w.writerows(rows)


def run_parity(*, days: list[str], reuse: bool) -> dict[str, Any]:
    t0 = time.monotonic()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)

    day_results: list[dict[str, Any]] = []
    all_match = True
    for day in days:
        day_key = day.replace("-", "")
        off_out = RUN_ROOT / "shadow_off" / day_key
        on_out = RUN_ROOT / "shadow_on" / day_key

        need_run = not (
            reuse
            and (off_out / "small_paper_summary.json").is_file()
            and (on_out / "small_paper_summary.json").is_file()
        )
        if reuse and not need_run:
            try:
                _validate_replay_dir(off_out)
                _validate_replay_dir(on_out)
            except RuntimeError as exc:
                print(f"[phase636] REUSE invalid {day}: {exc}; replaying", flush=True)
                need_run = True
        if need_run:
            staging = _run_replay(day=day, label="SHADOW_OFF", shadow_enabled=False)
            try:
                _copy_out(staging, off_out)
            finally:
                _robust_rmtree(staging)
            staging = _run_replay(day=day, label="SHADOW_ON", shadow_enabled=True)
            try:
                _copy_out(staging, on_out)
            finally:
                _robust_rmtree(staging)
        else:
            print(f"[phase636] REUSE day={day}", flush=True)

        r = compare_day_dirs(day, off_out, on_out)
        day_results.append(r)
        all_match = all_match and bool(r["match"])
        print(
            f"[phase636] {day}: match={r['match']} "
            f"accepted={r['core_metrics_off']['accepted_total']}/"
            f"{r['core_metrics_on']['accepted_total']} "
            f"events={r['events_match']} positions={r['positions_match']}",
            flush=True,
        )

    elapsed_sec = round(time.monotonic() - t0, 1)
    disk_mb = _dir_size_mb(RUN_ROOT)
    _write_shadow_whitelist(REPORT_DIR)
    _write_by_day_csv(REPORT_DIR, day_results)
    _write_diff_gz(REPORT_DIR, day_results, all_match=all_match)

    parity_summary = {
        "phase": "phase636_shadow_parity",
        "all_match": all_match,
        "days": [r["day"] for r in day_results],
        "day_results": [
            {
                "day": r["day"],
                "match": r["match"],
                "core_metrics_match": r["core_metrics_match"],
                "summary_match": r["summary_match"],
                "events_match": r["events_match"],
                "positions_match": r["positions_match"],
                "core_metrics_off": r["core_metrics_off"],
                "core_metrics_on": r["core_metrics_on"],
                "shadow_only_summary_keys": sorted(r["shadow_only_summary_diffs"].keys()),
            }
            for r in day_results
        ],
        "elapsed_sec": elapsed_sec,
        "disk_usage_mb": disk_mb,
        "reuse": reuse,
    }
    (REPORT_DIR / "phase636_parity_summary.json").write_text(
        json.dumps(parity_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report = {
        "phase": "phase636_shadow_parity",
        "verdict": PHASE636_VERDICT_DONE if all_match else PHASE636_VERDICT_FAIL,
        "all_match": all_match,
        "compare": "pbv2_rise5_shadow_enabled false vs true (current HEAD)",
        "shadow_field_whitelist": sorted(PBV2_RISE5_SHADOW_EVENT_KEYS),
        "answers": {
            "1_accepted_match": all(r["core_metrics_off"]["accepted_total"] == r["core_metrics_on"]["accepted_total"] for r in day_results),
            "2_pbv2_accepted_match": all(r["core_metrics_off"]["pbv2_accepted"] == r["core_metrics_on"]["pbv2_accepted"] for r in day_results),
            "3_or_accepted_match": all(r["core_metrics_off"]["or_accepted"] == r["core_metrics_on"]["or_accepted"] for r in day_results),
            "4_events_match_ex_shadow": all(r["events_match"] for r in day_results),
            "5_positions_match": all(r["positions_match"] for r in day_results),
            "6_summary_match_ex_shadow": all(r["summary_match"] for r in day_results),
            "7_elapsed_sec": elapsed_sec,
            "8_disk_mb": disk_mb,
        },
        "parity_summary": parity_summary,
    }
    (REPORT_DIR / "phase636_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[phase636] ALL_MATCH={all_match} elapsed_sec={elapsed_sec} disk_mb={disk_mb}", flush=True)
    print(f"[phase636] report -> {REPORT_DIR / 'phase636_report.json'}", flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase636 shadow parity CI")
    parser.add_argument("--days", nargs="*", default=list(DAYS))
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()
    report = run_parity(days=list(args.days), reuse=bool(args.reuse))
    return 0 if report.get("all_match") else 1


if __name__ == "__main__":
    raise SystemExit(main())
