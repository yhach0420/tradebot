"""
Phase620 parallel disk-safe runner (4 workers, focus days 624-701).
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import shutil
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase603_full_period_backtest import (
    PROD_YAML,
    _accept_meta_from_rows,
    _metrics_from_trade_rows,
    _trade_rows_from_structural,
)
from research.phase620_freshness_backtest_v2 import (
    PRE_PBV2_STALE,
    _gate_counts,
    _stream_audit_samples,
)
from research.phase620_freshness_semantics_variant import (
    SIM_EVENT_LAG_SEC,
    V2_VARIANT_ALIASES,
    V2_VARIANT_IDS,
    VARIANTS,
    apply_variant,
    restore_variant,
    tag_counts,
)
from research.structural_observer_review import (
    _session_end_time,
    replay_combined_structural_exit_v1,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

VERDICT = "phase620_parallel_disk_safe_done"
REPORT_DIR = "phase620_parallel"
TEMP_ROOT_NAME = "phase620"
SAMPLE100 = 100
POLL_INTERVAL = 5.0

DISK_WARN_PCT = 70.0
DISK_BLOCK_NEW_PCT = 74.0
DISK_STOP_ALL_PCT = 76.0
DISK_MONITOR_SEC = 300

WORKER_PARTITIONS: dict[int, tuple[str, ...]] = {
    1: ("2026-06-24", "2026-06-25"),
    2: ("2026-06-26", "2026-06-27"),
    3: ("2026-06-29",),
    4: ("2026-06-30", "2026-07-01"),
}

PAPER_TRADE_DIR_RE = None  # set at runtime: YYYYMMDD live dirs only


def _num(v: Any) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def disk_used_pct(path: Path) -> float:
    try:
        u = shutil.disk_usage(path)
        if u.total <= 0:
            return 0.0
        return round(100.0 * (u.total - u.free) / u.total, 2)
    except OSError:
        return 0.0


def _temp_root(kabu: Path) -> Path:
    return kabu / "temp" / TEMP_ROOT_NAME


def _replay_root(kabu: Path) -> Path:
    """Replay API requires path under results/small_paper; use research-only prefix."""
    return kabu / "results" / "small_paper" / "_phase620_parallel_replay"


def _job_ckpt_dir(temp: Path, worker_id: int, variant: str, day: str) -> Path:
    return temp / "checkpoints" / f"worker{worker_id}" / variant / day


@dataclass
class DiskMonitor:
    kabu: Path
    reports: Path
    force: bool = False
    stop_event: threading.Event = field(default_factory=threading.Event)
    block_new: threading.Event = field(default_factory=threading.Event)
    log_rows: list[dict[str, Any]] = field(default_factory=list)
    _thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            pct = disk_used_pct(self.kabu)
            row = {"ts": _now_iso(), "disk_used_pct": pct, "action": "ok"}
            if pct >= DISK_STOP_ALL_PCT and not self.force:
                row["action"] = "stop_all"
                self.block_new.set()
                self.stop_event.set()
                self._cleanup_phase620_artifacts()
            elif pct >= DISK_STOP_ALL_PCT and self.force:
                row["action"] = "warning_above_76_forced_run"
            elif pct >= DISK_BLOCK_NEW_PCT and not self.force:
                row["action"] = "block_new_jobs"
                self.block_new.set()
            elif pct >= DISK_WARN_PCT:
                row["action"] = "warning"
            else:
                self.block_new.clear()
            self.log_rows.append(row)
            self._append_status(row)
            if self.stop_event.wait(DISK_MONITOR_SEC):
                break

    def _cleanup_phase620_artifacts(self) -> None:
        kabu = self.kabu
        targets = [
            _temp_root(kabu),
            _replay_root(kabu),
            kabu / "results" / "small_paper" / "_phase620_v2_temp",
            kabu / "results" / "small_paper" / "_phase620_freshness_checkpoints",
            kabu / "results" / "reports" / "phase620_freshness_backtest_v2" / "jobs",
        ]
        for t in targets:
            if not t.exists():
                continue
            try:
                if t.is_dir():
                    shutil.rmtree(t, ignore_errors=True)
                else:
                    t.unlink(missing_ok=True)
                self.log_rows.append({"ts": _now_iso(), "cleanup_path": str(t), "action": "deleted"})
            except OSError as exc:
                self.log_rows.append({"ts": _now_iso(), "cleanup_path": str(t), "action": f"error:{exc}"})

    def _append_status(self, row: Mapping[str, Any]) -> None:
        p = self.reports / "phase620_parallel_status.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        write_header = not p.is_file()
        with p.open("a", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["ts", "disk_used_pct", "action"])
            if write_header:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in ("ts", "disk_used_pct", "action")})

    def write_audit(self) -> None:
        self.reports.mkdir(parents=True, exist_ok=True)
        _write_csv(
            self.reports / "phase620_disk_usage.csv",
            ["ts", "disk_used_pct", "action", "cleanup_path"],
            self.log_rows,
        )
        cleanup = [r for r in self.log_rows if r.get("cleanup_path")]
        _write_csv(
            self.reports / "phase620_cleanup_log.csv",
            ["ts", "cleanup_path", "action"],
            cleanup,
        )


def preflight_cleanup(kabu: Path, reports: Path, *, force: bool = False) -> dict[str, Any]:
    pct_before = disk_used_pct(kabu)
    monitor = DiskMonitor(kabu, reports)
    if pct_before >= DISK_BLOCK_NEW_PCT:
        monitor._cleanup_phase620_artifacts()
        phase234 = kabu / "results" / "small_paper" / "phase234"
        if phase234.is_dir():
            shutil.rmtree(phase234, ignore_errors=True)
            monitor.log_rows.append({"ts": _now_iso(), "cleanup_path": str(phase234), "action": "deleted"})
    pct_after = disk_used_pct(kabu)
    can_start = pct_after < DISK_BLOCK_NEW_PCT or force
    return {
        "pct_before": pct_before,
        "pct_after": pct_after,
        "can_start": can_start,
        "forced": force and pct_after >= DISK_BLOCK_NEW_PCT,
    }


def _run_single_job(task: Mapping[str, Any]) -> dict[str, Any]:
    repo_root = Path(str(task["repo_root"]))
    kabu = resolve_kabu_root(repo_root)
    for p in (kabu / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    worker_id = int(task["worker_id"])
    day_iso = str(task["day"])
    variant_id = str(task["variant"])
    poll = float(task.get("poll_interval_sec", POLL_INTERVAL))
    temp = _temp_root(kabu)
    ckpt = _job_ckpt_dir(temp, worker_id, variant_id, day_iso)
    ckpt_file = ckpt / "summary.json.gz"

    if ckpt_file.is_file() and bool(task.get("resume", True)):
        with gzip.open(ckpt_file, "rt", encoding="utf-8") as fh:
            return json.load(fh)

    if not bool(task.get("force_disk")) and disk_used_pct(kabu) >= DISK_BLOCK_NEW_PCT:
        raise RuntimeError(f"disk {disk_used_pct(kabu)}% >= {DISK_BLOCK_NEW_PCT}% block")

    is_p603 = variant_id == "P603_ref"
    if not is_p603:
        apply_variant(variant_id)
    else:
        restore_variant()

    replay_tmp = _replay_root(kabu) / f"w{worker_id}_{variant_id}_{day_iso.replace('-', '')}"
    try:
        from small_paper.config import load_pilot_config
        from small_paper.pilot_runner import run_push_replay_dry_run

        cfg_path = (
            repo_root / PROD_YAML
            if (repo_root / PROD_YAML).is_file()
            else kabu / "configs" / Path(PROD_YAML).name
        )
        base = load_pilot_config(cfg_path)
        cfg = replace(
            base,
            discord_enabled=False,
            discord_observer_only=True,
            entry_freshness_board_fallback_enabled=is_p603,
        )
        push_dir = kabu / "data" / "push_jsonl" / day_iso
        if not push_dir.is_dir():
            return {"skipped": True, "day": day_iso, "variant": variant_id, "reason": "no_push_data"}

        if replay_tmp.is_dir():
            shutil.rmtree(replay_tmp, ignore_errors=True)
        t0 = time.monotonic()
        result = run_push_replay_dry_run(
            cfg,
            push_dir=push_dir,
            output_dir=replay_tmp,
            repo_root=repo_root,
            poll_interval_sec=poll,
            streaming_push_replay=True,
            enable_discord=False,
            write_board_shadow_reports=False,
        )
        runtime = time.monotonic() - t0
        events = list(result.events or [])
        accepted = list(result.accepted or [])
        summary = dict(result.summary or {})
        rc = summary.get("reject_reason_counts") or {}
        audit_stale, _ = _stream_audit_samples(replay_tmp / "entry_scan_audit.jsonl", max_samples=50)
        tags = tag_counts() if not is_p603 else {}
        gate = _gate_counts(events, accepted)
        accept_meta = _accept_meta_from_rows(accepted)
        session_end = _session_end_time(events)
        struct_trades, _ = replay_combined_structural_exit_v1(
            events,
            pilot_config=cfg,
            poll_interval_sec=poll,
            session_end=session_end,
        )
        trade_rows = _trade_rows_from_structural(struct_trades, accept_meta=accept_meta)
        perf = _metrics_from_trade_rows(trade_rows)
        exit_count = len(struct_trades)

        alias = V2_VARIANT_ALIASES.get(variant_id, variant_id)
        label = VARIANTS[alias].label if alias in VARIANTS else "P603 board_fallback"

        payload = {
            "worker_id": worker_id,
            "day": day_iso,
            "variant": variant_id,
            "label": label,
            "runtime_sec": round(runtime, 1),
            "pbv2_reach": gate["pbv2_reach"],
            "pbv2_accepted": gate["pbv2_accepted"],
            "or_accepted": gate["or_accepted"],
            "entry_count": gate["entry_count"],
            "exit_count": exit_count,
            "event_stale": int(rc.get("event_stale_price") or audit_stale.get("event_stale_price", 0)),
            "board_stale": int(rc.get("data_stale_board") or audit_stale.get("data_stale_board", 0)),
            "trade_stale": int(tags.get("liquidity_stale_trade", 0) or audit_stale.get("liquidity_stale_trade", 0)),
            "data_stale_price": int(rc.get("data_stale_price") or 0),
            "liquidity_guard_pass": int(tags.get("liquidity_guard_pass", 0)),
            "total_pnl_yen_100": perf["total_pnl_yen_100"],
            "profit_factor": perf.get("profit_factor"),
            "win_rate": perf.get("win_rate"),
            "avg_pnl_yen_100": perf.get("avg_pnl_yen_100"),
            "max_drawdown_yen_100": perf.get("max_drawdown_yen_100"),
            "trade_count": perf["trade_count"],
            "sample100": trade_rows[:SAMPLE100],
        }
        ckpt.mkdir(parents=True, exist_ok=True)
        with gzip.open(ckpt_file, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, default=str)
        return payload
    finally:
        restore_variant()
        if replay_tmp.is_dir():
            shutil.rmtree(replay_tmp, ignore_errors=True)


def _worker_task(task: Mapping[str, Any]) -> dict[str, Any]:
    """Run all variant×day jobs for one worker sequentially (memory-safe)."""
    repo_root = Path(str(task["repo_root"]))
    kabu = resolve_kabu_root(repo_root)
    worker_id = int(task["worker_id"])
    days = list(task["days"])
    variants = list(task.get("variants") or V2_VARIANT_IDS)
    poll = float(task.get("poll_interval_sec", POLL_INTERVAL))
    force_disk = bool(task.get("force_disk"))
    results: list[dict[str, Any]] = []

    for day_iso in days:
        push_dir = kabu / "data" / "push_jsonl" / day_iso
        if not push_dir.is_dir():
            continue
        for variant_id in variants:
            pct = disk_used_pct(kabu)
            if not force_disk and pct >= DISK_STOP_ALL_PCT:
                raise RuntimeError(f"worker{worker_id} disk stop at {pct}%")
            if not force_disk and pct >= DISK_BLOCK_NEW_PCT:
                raise RuntimeError(f"worker{worker_id} disk block at {pct}%")
            row = _run_single_job(
                {
                    "repo_root": str(repo_root),
                    "worker_id": worker_id,
                    "day": day_iso,
                    "variant": variant_id,
                    "poll_interval_sec": poll,
                    "resume": task.get("resume", True),
                    "force_disk": force_disk,
                }
            )
            if not row.get("skipped"):
                results.append(row)
            print(f"[w{worker_id}] {variant_id} {day_iso} pnl={row.get('total_pnl_yen_100')}", flush=True)

    return _aggregate_worker(worker_id, results, kabu)


def _aggregate_worker(worker_id: int, rows: Sequence[Mapping[str, Any]], kabu: Path) -> dict[str, Any]:
    reports = resolve_reports_dir(kabu)
    out = reports / REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)

    by_var: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    samples: list[dict] = []
    for r in rows:
        by_var[str(r["variant"])].append(r)
        samples.extend(list(r.get("sample100") or [])[:20])

    variant_rows: list[dict[str, Any]] = []
    for vid in V2_VARIANT_IDS:
        vrows = by_var.get(vid, [])
        if not vrows:
            continue
        all_samples: list[dict] = []
        for d in vrows:
            all_samples.extend(list(d.get("sample100") or []))
        perf = _metrics_from_trade_rows(all_samples) if all_samples else {}
        total_pnl = round(sum(_num(x.get("total_pnl_yen_100")) for x in vrows), 2)
        variant_rows.append(
            {
                "variant": vid,
                "label": vrows[0].get("label"),
                "days": len(vrows),
                "event_stale": sum(int(x.get("event_stale") or 0) for x in vrows),
                "board_stale": sum(int(x.get("board_stale") or 0) for x in vrows),
                "trade_stale": sum(int(x.get("trade_stale") or 0) for x in vrows),
                "pbv2_accepted": sum(int(x.get("pbv2_accepted") or 0) for x in vrows),
                "entry_count": sum(int(x.get("entry_count") or 0) for x in vrows),
                "exit_count": sum(int(x.get("exit_count") or 0) for x in vrows),
                "total_pnl_yen_100": total_pnl,
                "profit_factor": perf.get("profit_factor"),
                "win_rate": perf.get("win_rate"),
                "max_drawdown_yen_100": round(
                    max(_num(x.get("max_drawdown_yen_100")) for x in vrows) if vrows else 0, 2
                ),
                "trade_count": sum(int(x.get("trade_count") or 0) for x in vrows),
            }
        )

    summary = {
        "worker_id": worker_id,
        "verdict": "worker_done",
        "job_count": len(rows),
        "variant_summary": variant_rows,
        "disk_used_pct_end": disk_used_pct(kabu),
    }
    summary_path = out / f"worker{worker_id}_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    var_path = out / f"worker{worker_id}_variant.csv.gz"
    fields = [
        "variant", "label", "days", "event_stale", "board_stale", "trade_stale",
        "pbv2_accepted", "entry_count", "exit_count", "total_pnl_yen_100",
        "profit_factor", "win_rate", "max_drawdown_yen_100", "trade_count",
    ]
    with gzip.open(var_path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in variant_rows:
            w.writerow({k: r.get(k, "") for k in fields})

    sample_path = out / f"worker{worker_id}_sample100.csv.gz"
    sf = ["symbol", "entry_time", "exit_time", "pnl_yen_100", "exit_reason", "variant"]
    with gzip.open(sample_path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=sf, extrasaction="ignore")
        w.writeheader()
        for r in samples[:SAMPLE100]:
            w.writerow({k: r.get(k, "") for k in sf if k != "variant"})

    return summary


def aggregate_final(repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(kabu)
    out = reports / REPORT_DIR

    all_variant: dict[str, list[dict]] = defaultdict(list)
    day_pnls: dict[str, dict[str, float]] = defaultdict(dict)
    for wid in WORKER_PARTITIONS:
        sp = out / f"worker{wid}_summary.json"
        if not sp.is_file():
            continue
        ws = json.loads(sp.read_text(encoding="utf-8"))
        for vr in ws.get("variant_summary") or []:
            all_variant[str(vr["variant"])].append(vr)

    compare_rows: list[dict[str, Any]] = []
    all_samples_by_var: dict[str, list[dict]] = defaultdict(list)
    for wid in WORKER_PARTITIONS:
        sp = out / f"worker{wid}_sample100.csv.gz"
        if sp.is_file():
            with gzip.open(sp, "rt", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    pass  # samples lack variant column in file

    for vid in V2_VARIANT_IDS:
        parts = all_variant.get(vid, [])
        if not parts:
            continue
        pnl = round(sum(_num(p.get("total_pnl_yen_100")) for p in parts), 2)
        samples: list[dict] = []
        temp = _temp_root(kabu)
        for wid in WORKER_PARTITIONS:
            for day in WORKER_PARTITIONS[wid]:
                ckpt = _job_ckpt_dir(temp, wid, vid, day) / "summary.json.gz"
                if ckpt.is_file():
                    with gzip.open(ckpt, "rt", encoding="utf-8") as fh:
                        data = json.load(fh)
                    samples.extend(data.get("sample100") or [])
        perf = _metrics_from_trade_rows(samples) if samples else {}
        compare_rows.append(
            {
                "variant": vid,
                "event_stale": sum(int(p.get("event_stale") or 0) for p in parts),
                "board_stale": sum(int(p.get("board_stale") or 0) for p in parts),
                "trade_stale": sum(int(p.get("trade_stale") or 0) for p in parts),
                "pbv2_accepted": sum(int(p.get("pbv2_accepted") or 0) for p in parts),
                "entry_count": sum(int(p.get("entry_count") or 0) for p in parts),
                "exit_count": sum(int(p.get("exit_count") or 0) for p in parts),
                "total_pnl_yen_100": pnl,
                "profit_factor": perf.get("profit_factor"),
                "win_rate": perf.get("win_rate"),
                "max_drawdown_yen_100": max(_num(p.get("max_drawdown_yen_100")) for p in parts),
                "trade_count": sum(int(p.get("trade_count") or 0) for p in parts),
            }
        )

    ranked = sorted(compare_rows, key=lambda r: (_num(r["total_pnl_yen_100"]), _num(r.get("profit_factor") or 0)), reverse=True)
    candidates = [r for r in ranked if r["variant"] not in ("baseline", "P603_ref")]
    best = ranked[0] if ranked else {}
    second = ranked[1] if len(ranked) > 1 else {}
    baseline = next((r for r in compare_rows if r["variant"] == "baseline"), {})
    adopt = "baseline"
    adopt_reason = "no candidate beats baseline on PnL"
    reject_reason = ""
    if candidates:
        top = candidates[0]
        if _num(top.get("total_pnl_yen_100")) > _num(baseline.get("total_pnl_yen_100")):
            adopt = str(top["variant"])
            adopt_reason = f"candidate {adopt} PnL>{baseline.get('total_pnl_yen_100')}"
        else:
            reject_reason = "no candidate PnL exceeds baseline"

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "sim_event_lag_sec": SIM_EVENT_LAG_SEC,
        "worker_partitions": {str(k): list(v) for k, v in WORKER_PARTITIONS.items()},
        "variant_compare": compare_rows,
        "best_variant": best.get("variant"),
        "second_best": second.get("variant"),
        "adopt_recommendation": adopt,
        "adopt_reason": adopt_reason,
        "reject_reason": reject_reason,
        "disk_used_pct_end": disk_used_pct(kabu),
        "mandatory_answers": {
            "variants": compare_rows,
            "best": best.get("variant"),
            "second_best": second.get("variant"),
            "adopt": adopt,
            "reject_reason": reject_reason,
        },
    }
    (out / "phase620_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _write_csv(out / "phase620_variant_compare.csv", list(compare_rows[0].keys()) if compare_rows else ["variant"], compare_rows)
    best_rows = ranked[:3]
    _write_csv(out / "phase620_best_variant.csv", list(best_rows[0].keys()) if best_rows else ["variant"], best_rows)

    temp = _temp_root(kabu)
    if temp.is_dir():
        shutil.rmtree(temp, ignore_errors=True)
    replay = _replay_root(kabu)
    if replay.is_dir():
        shutil.rmtree(replay, ignore_errors=True)
    return report


def run_parallel(repo_root: Path, *, resume: bool = True, force_disk: bool = False) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(kabu)
    pre = preflight_cleanup(kabu, reports, force=force_disk)
    if not pre["can_start"]:
        raise RuntimeError(
            f"disk {pre['pct_after']}% >= {DISK_BLOCK_NEW_PCT}% — use --force-disk after manual cleanup"
        )

    monitor = DiskMonitor(kabu, reports, force=force_disk)
    monitor.start()

    tasks = []
    for wid, days in WORKER_PARTITIONS.items():
        tasks.append(
            {
                "repo_root": str(repo_root),
                "worker_id": wid,
                "days": list(days),
                "variants": list(V2_VARIANT_IDS),
                "poll_interval_sec": POLL_INTERVAL,
                "resume": resume,
                "force_disk": force_disk,
            }
        )

    print(f"phase620_parallel: 4 workers pre_disk={pre['pct_after']}%", flush=True)
    failed: list[str] = []
    try:
        with ProcessPoolExecutor(max_workers=4) as pool:
            futs = {pool.submit(_worker_task, t): int(t["worker_id"]) for t in tasks}
            for fut in as_completed(futs):
                wid = futs[fut]
                try:
                    fut.result()
                    print(f"worker{wid} complete", flush=True)
                except Exception as exc:
                    print(f"worker{wid} FAILED: {exc}", flush=True)
                    failed.append(f"worker{wid}:{exc}")
    finally:
        monitor.stop_event.set()
        if monitor._thread:
            monitor._thread.join(timeout=5)
        monitor.write_audit()

    if failed:
        raise RuntimeError(f"workers failed: {failed}")

    report = aggregate_final(repo_root)
    report["preflight_disk"] = pre
    report["disk_used_pct_final"] = disk_used_pct(kabu)
    if disk_used_pct(kabu) > DISK_STOP_ALL_PCT and not force_disk:
        report["verdict"] = "phase620_parallel_disk_over_limit"
    elif pre.get("forced"):
        report["disk_note"] = "baseline above 76%; forced run; paper trade dirs untouched"
    return report
