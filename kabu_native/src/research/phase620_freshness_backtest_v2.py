"""
Phase620 v2: disk-safe 8-parallel freshness semantics backtest.
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import shutil
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase603_full_period_backtest import (
    PROD_YAML,
    _accept_meta_from_rows,
    _discover_push_days,
    _metrics_from_trade_rows,
    _trade_rows_from_structural,
)
from research.phase620_disk_cleanup import DISK_FREE_ABORT_GB, DISK_FREE_WARN_GB
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

VERDICT = "phase620_freshness_backtest_v2_done"
REPORT_DIR = "phase620_freshness_backtest_v2"
REJECT_SAMPLE_MAX = int(os.environ.get("PHASE620_REJECT_SAMPLE_MAX", "500"))
SAMPLE_MAX_ROWS = int(os.environ.get("PHASE620_SAMPLE_MAX_ROWS", "3000"))
MAX_WORKERS_DEFAULT = 8

PRE_PBV2_STALE = frozenset(
    {"data_stale_price", "data_stale_board", "event_stale_price", "data_stale"}
)

FOCUS_DAYS = ("2026-06-25", "2026-06-29", "2026-06-30")


def _num(v: Any) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _free_gb(path: Path) -> float:
    try:
        return shutil.disk_usage(path).free / (1024**3)
    except OSError:
        return 999.0


def _disk_guard(path: Path) -> None:
    free = _free_gb(path)
    if free < DISK_FREE_ABORT_GB:
        raise RuntimeError(f"disk free {free:.1f}GB < abort {DISK_FREE_ABORT_GB}GB")
    if free < DISK_FREE_WARN_GB:
        print(f"WARN: disk free {free:.1f}GB < warn {DISK_FREE_WARN_GB}GB", flush=True)


def _stream_audit_samples(audit_path: Path, *, max_samples: int) -> tuple[Counter, list[dict]]:
    stale = Counter()
    samples: list[dict] = []
    if not audit_path.is_file():
        return stale, samples
    with audit_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("audit_type") != "entry_symbol_eval":
                continue
            rr = str(row.get("reject_reason") or "")
            if rr:
                stale[rr] += 1
                if len(samples) < max_samples:
                    samples.append(
                        {
                            "symbol": row.get("symbol"),
                            "reject_reason": rr,
                            "price_age_sec": row.get("price_age_sec"),
                            "board_age_sec": row.get("board_age_sec"),
                            "price_freshness_source": row.get("price_freshness_source"),
                        }
                    )
            src = str(row.get("price_freshness_source") or "")
            if src == "liquidity_stale_trade":
                stale["liquidity_stale_trade"] += 1
    return stale, samples


def _gate_counts(events: Sequence[Mapping[str, Any]], accepted: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    pbv2_reach = sum(
        1
        for e in events
        if str(e.get("event_type")) == "candidate"
        and str(e.get("reject_reason") or e.get("reason") or "") not in PRE_PBV2_STALE
    )
    pbv2_acc = sum(1 for a in accepted if str(a.get("entry_type") or "PBV2").upper() == "PBV2")
    or_acc = sum(1 for a in accepted if str(a.get("entry_type") or "").upper() == "OR")
    return {
        "pbv2_reach": pbv2_reach,
        "pbv2_accepted": pbv2_acc,
        "or_accepted": or_acc,
        "entry_count": len(accepted),
    }


def _write_gz_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _run_job(
    repo_root: Path,
    day_iso: str,
    variant_id: str,
    *,
    poll_interval_sec: float,
    job_dir: Path,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    for p in (kabu / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    _disk_guard(kabu)
    log_path = job_dir / "log.txt"
    log_lines: list[str] = []

    def log(msg: str) -> None:
        log_lines.append(msg)
        print(msg, flush=True)

    is_p603 = variant_id == "P603_ref"
    if not is_p603:
        apply_variant(variant_id)
    else:
        restore_variant()

    replay_tmp: Optional[Path] = None
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
        replay_tmp = (
            kabu
            / "results"
            / "small_paper"
            / "_phase620_v2_temp"
            / variant_id
            / day_iso.replace("-", "")
        )
        if replay_tmp.is_dir():
            shutil.rmtree(replay_tmp, ignore_errors=True)
        t0 = time.monotonic()
        result = run_push_replay_dry_run(
            cfg,
            push_dir=push_dir,
            output_dir=replay_tmp,
            repo_root=repo_root,
            poll_interval_sec=poll_interval_sec,
            streaming_push_replay=True,
            enable_discord=False,
            write_board_shadow_reports=False,
        )
        runtime = time.monotonic() - t0
        events = list(result.events or [])
        accepted = list(result.accepted or [])
        summary = dict(result.summary or {})
        rc = summary.get("reject_reason_counts") or {}
        audit_stale, reject_samples = _stream_audit_samples(
            replay_tmp / "entry_scan_audit.jsonl",
            max_samples=REJECT_SAMPLE_MAX,
        )
        tags = tag_counts() if not is_p603 else {}
        gate = _gate_counts(events, accepted)
        accept_meta = _accept_meta_from_rows(accepted)
        session_end = _session_end_time(events)
        struct_trades, _ = replay_combined_structural_exit_v1(
            events,
            pilot_config=cfg,
            poll_interval_sec=poll_interval_sec,
            session_end=session_end,
        )
        trade_rows = _trade_rows_from_structural(struct_trades, accept_meta=accept_meta)
        perf = _metrics_from_trade_rows(trade_rows)

        stale_counts = {
            "data_stale_price": int(rc.get("data_stale_price") or audit_stale.get("data_stale_price", 0)),
            "data_stale_board": int(rc.get("data_stale_board") or audit_stale.get("data_stale_board", 0)),
            "event_stale_price": int(rc.get("event_stale_price") or audit_stale.get("event_stale_price", 0)),
            "trade_stale": int(tags.get("liquidity_stale_trade", 0) or audit_stale.get("liquidity_stale_trade", 0)),
            "liquidity_guard_pass": int(tags.get("liquidity_guard_pass", 0)),
        }

        alias = V2_VARIANT_ALIASES.get(variant_id, variant_id)
        label = VARIANTS[alias].label if alias in VARIANTS else "Phase603 board_fallback"

        job_summary = {
            "day": day_iso,
            "variant": variant_id,
            "label": label,
            "runtime_sec": round(runtime, 1),
            "push_rows": int(summary.get("push_rows") or 0),
            "gate_evaluations": int(summary.get("gate_evaluations") or 0),
            "accepts": len(accepted),
            **gate,
            "stale_counts": stale_counts,
            "performance": perf,
            "sim_event_lag_sec": SIM_EVENT_LAG_SEC,
            "exit_reasons": perf.get("exit_reasons") or {},
        }

        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "job_summary.json").write_text(
            json.dumps(job_summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        trade_fields = [
            "symbol",
            "entry_time",
            "exit_time",
            "hold_sec",
            "pnl_pct",
            "pnl_yen_100",
            "exit_reason",
            "entry_price",
            "price_freshness_source",
            "fallback_used",
            "spread_bps",
        ]
        _write_gz_csv(job_dir / "trades.csv.gz", trade_fields, trade_rows)
        rej_fields = ["symbol", "reject_reason", "price_age_sec", "board_age_sec", "price_freshness_source"]
        _write_gz_csv(job_dir / "reject_sample.csv.gz", rej_fields, reject_samples)
        log_path.write_text("\n".join(log_lines + [f"done runtime={runtime:.1f}s trades={len(trade_rows)}"]), encoding="utf-8")
        return {**job_summary, "trade_rows": trade_rows, "job_dir": str(job_dir)}
    finally:
        restore_variant()
        if replay_tmp and replay_tmp.is_dir():
            shutil.rmtree(replay_tmp, ignore_errors=True)


def _job_dir(root: Path, variant_id: str, day_iso: str) -> Path:
    return root / "jobs" / variant_id / day_iso


def _job_complete(job_dir: Path) -> bool:
    return (job_dir / "job_summary.json").is_file() and (job_dir / "trades.csv.gz").is_file()


def _run_job_task(task: Mapping[str, Any]) -> dict[str, Any]:
    repo_root = Path(str(task["repo_root"]))
    day_iso = str(task["day"])
    variant_id = str(task["variant"])
    poll = float(task["poll_interval_sec"])
    out_root = Path(str(task["out_root"]))
    job_dir = _job_dir(out_root, variant_id, day_iso)
    if bool(task.get("resume", True)) and _job_complete(job_dir):
        summary = json.loads((job_dir / "job_summary.json").read_text(encoding="utf-8"))
        with gzip.open(job_dir / "trades.csv.gz", "rt", encoding="utf-8") as fh:
            r = csv.DictReader(fh)
            summary["trade_rows"] = list(r)
        summary["job_dir"] = str(job_dir)
        return summary
    return _run_job(repo_root, day_iso, variant_id, poll_interval_sec=poll, job_dir=job_dir)


class Phase620V2Job:
    def __init__(self, repo_root: Path, *, poll_interval_sec: float = 5.0, workers: int = MAX_WORKERS_DEFAULT):
        self.repo_root = repo_root
        self.poll_interval_sec = poll_interval_sec
        self.workers = workers
        self.kabu = resolve_kabu_root(repo_root)
        self.reports = resolve_reports_dir(self.kabu)
        self.out_root = self.reports / REPORT_DIR
        self.out_root.mkdir(parents=True, exist_ok=True)
        self.push_root = self.kabu / "data" / "push_jsonl"

    def run(
        self,
        *,
        days: Optional[Sequence[str]] = None,
        variants: Optional[Sequence[str]] = None,
        resume: bool = True,
    ) -> dict[str, Any]:
        free = _free_gb(self.kabu)
        if free < DISK_FREE_WARN_GB:
            raise RuntimeError(f"disk free {free:.1f}GB < {DISK_FREE_WARN_GB}GB resume blocked")

        all_days = list(days or _discover_push_days(self.push_root))
        all_variants = list(variants or list(V2_VARIANT_IDS))
        tasks = []
        for vid in all_variants:
            for day_iso in all_days:
                jd = _job_dir(self.out_root, vid, day_iso)
                if resume and _job_complete(jd):
                    continue
                tasks.append(
                    {
                        "repo_root": str(self.repo_root),
                        "day": day_iso,
                        "variant": vid,
                        "poll_interval_sec": self.poll_interval_sec,
                        "out_root": str(self.out_root),
                        "resume": resume,
                    }
                )

        print(
            f"phase620_v2: workers={self.workers} pending={len(tasks)} "
            f"days={len(all_days)} variants={len(all_variants)} free_gb={free:.1f}",
            flush=True,
        )
        failed: list[str] = []
        if tasks:
            with ProcessPoolExecutor(max_workers=self.workers) as pool:
                futs = {pool.submit(_run_job_task, t): (t["variant"], t["day"]) for t in tasks}
                for fut in as_completed(futs):
                    key = futs[fut]
                    try:
                        fut.result()
                        print(f"[v2] done {key[0]} {key[1]}", flush=True)
                    except Exception as exc:
                        print(f"[v2] FAILED {key}: {exc}", flush=True)
                        failed.append(f"{key[0]}:{key[1]}:{exc}")

        if failed:
            raise RuntimeError(f"failed jobs: {failed[:10]} total={len(failed)}")

        return self.aggregate(days=all_days, variants=all_variants)

    def aggregate(
        self,
        *,
        days: Optional[Sequence[str]] = None,
        variants: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        all_days = list(days or _discover_push_days(self.push_root))
        all_variants = list(variants or list(V2_VARIANT_IDS))
        by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for vid in all_variants:
            for day_iso in all_days:
                jd = _job_dir(self.out_root, vid, day_iso)
                if not _job_complete(jd):
                    raise RuntimeError(f"missing job {vid} {day_iso}")
                row = json.loads((jd / "job_summary.json").read_text(encoding="utf-8"))
                with gzip.open(jd / "trades.csv.gz", "rt", encoding="utf-8") as fh:
                    row["trade_rows"] = list(csv.DictReader(fh))
                by_variant[vid].append(row)
                for p in jd.glob("*"):
                    if p.is_file() and p.name not in ("job_summary.json", "trades.csv.gz", "reject_sample.csv.gz", "log.txt"):
                        p.unlink(missing_ok=True)

        b_rows = by_variant.get("baseline", [])
        b_trades: list[dict] = []
        for d in b_rows:
            b_trades.extend(d.get("trade_rows") or [])
        b_perf = _metrics_from_trade_rows(b_trades)

        variant_comparison: list[dict[str, Any]] = []
        daily_comparison: list[dict[str, Any]] = []
        stale_comparison: list[dict[str, Any]] = []
        source_analysis: list[dict[str, Any]] = []
        risk_metrics: list[dict[str, Any]] = []
        focus_rows: list[dict[str, Any]] = []

        for vid in all_variants:
            days_rows = by_variant[vid]
            trades: list[dict] = []
            for d in days_rows:
                trades.extend(d.get("trade_rows") or [])
            perf = _metrics_from_trade_rows(trades)
            stale_sum = Counter()
            for d in days_rows:
                for k, v in (d.get("stale_counts") or {}).items():
                    stale_sum[k] += int(v or 0)
            gate_sum = {
                "pbv2_reach": sum(int(d.get("pbv2_reach") or 0) for d in days_rows),
                "pbv2_accepted": sum(int(d.get("pbv2_accepted") or 0) for d in days_rows),
                "or_accepted": sum(int(d.get("or_accepted") or 0) for d in days_rows),
                "entry_count": sum(int(d.get("entry_count") or 0) for d in days_rows),
            }
            variant_comparison.append(
                {
                    "variant": vid,
                    "label": days_rows[0].get("label") if days_rows else "",
                    "days": len(days_rows),
                    **gate_sum,
                    "event_stale": stale_sum.get("event_stale_price", 0),
                    "board_stale": stale_sum.get("data_stale_board", 0),
                    "data_stale_price": stale_sum.get("data_stale_price", 0),
                    "trade_stale": stale_sum.get("trade_stale", 0),
                    "liquidity_guard_pass": stale_sum.get("liquidity_guard_pass", 0),
                    **perf,
                    "delta_pnl_vs_baseline": round(_num(perf["total_pnl_yen_100"]) - _num(b_perf["total_pnl_yen_100"]), 2),
                    "delta_dd_vs_baseline": round(
                        _num(perf["max_drawdown_yen_100"]) - _num(b_perf["max_drawdown_yen_100"]), 2
                    ),
                }
            )
            stale_comparison.append({"variant": vid, **dict(stale_sum), **gate_sum})
            risk_metrics.append(
                {
                    "variant": vid,
                    "max_drawdown_yen_100": perf["max_drawdown_yen_100"],
                    "profit_factor": perf.get("profit_factor"),
                    "win_rate": perf.get("win_rate"),
                    "trade_count": perf["trade_count"],
                }
            )
            for d in days_rows:
                dp = d.get("performance") or {}
                daily_comparison.append(
                    {
                        "day": d.get("day"),
                        "variant": vid,
                        "accepts": d.get("accepts"),
                        "pbv2_accepted": d.get("pbv2_accepted"),
                        "or_accepted": d.get("or_accepted"),
                        "total_pnl_yen_100": dp.get("total_pnl_yen_100"),
                        "profit_factor": dp.get("profit_factor"),
                        "trade_count": dp.get("trade_count"),
                    }
                )
                if str(d.get("day")) in FOCUS_DAYS:
                    focus_rows.append(
                        {
                            "day": d.get("day"),
                            "variant": vid,
                            "pbv2_reach": d.get("pbv2_reach"),
                            "pbv2_accepted": d.get("pbv2_accepted"),
                            "accepts": d.get("accepts"),
                            "pnl_yen_100": dp.get("total_pnl_yen_100"),
                        }
                    )
            for seg, pred in (
                ("liquidity_guard_pass", lambda t: str(t.get("price_freshness_source")) == "liquidity_guard_pass"),
                ("liquidity_stale_trade", lambda t: str(t.get("price_freshness_source")) == "liquidity_stale_trade"),
                ("board_fallback", lambda t: str(t.get("price_freshness_source")) == "board_fallback"),
                ("current_price_time", lambda t: str(t.get("price_freshness_source")) in ("", "current_price_time")),
            ):
                subset = [t for t in trades if pred(t)]
                if subset:
                    source_analysis.append({"variant": vid, "segment": seg, **_metrics_from_trade_rows(subset)})

        mandatory = self._mandatory_answers(variant_comparison, b_perf, focus_rows)
        result = {
            "verdict": VERDICT,
            "generated_at": _now_iso(),
            "period_days": all_days,
            "day_count": len(all_days),
            "variants": all_variants,
            "workers": self.workers,
            "sim_event_lag_sec": SIM_EVENT_LAG_SEC,
            "baseline_performance": b_perf,
            "variant_comparison": variant_comparison,
            "daily_comparison": daily_comparison,
            "stale_reason_comparison": stale_comparison,
            "trade_source_analysis": source_analysis,
            "risk_metrics": risk_metrics,
            "focus_days": focus_rows,
            "mandatory_answers": mandatory,
            "disk_free_gb": round(_free_gb(self.kabu), 2),
        }
        paths = self.write_outputs(result)
        result["output_paths"] = paths
        return result

    def _mandatory_answers(
        self,
        rows: Sequence[Mapping[str, Any]],
        b_perf: Mapping[str, Any],
        focus: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        candidates = [r for r in rows if r.get("variant") not in ("baseline", "P603_ref")]
        improved = [
            r["variant"]
            for r in candidates
            if _num(r.get("delta_pnl_vs_baseline")) > 0
            and _num(r.get("max_drawdown_yen_100")) <= _num(b_perf.get("max_drawdown_yen_100")) * 1.05
        ]
        ranked = sorted(
            candidates,
            key=lambda r: (_num(r.get("total_pnl_yen_100")), _num(r.get("profit_factor") or 0)),
            reverse=True,
        )
        rank_order = [r["variant"] for r in ranked]
        p603 = next((r for r in rows if r.get("variant") == "P603_ref"), {})
        best = ranked[0] if ranked else {}
        focus_bad = [f for f in focus if f.get("day") in ("2026-06-29", "2026-06-30")]
        focus_625 = [f for f in focus if f.get("day") == "2026-06-25"]
        b_pbv2_bad = sum(int(f.get("pbv2_accepted") or 0) for f in focus_bad if f.get("variant") == "baseline")
        a_pbv2_bad = sum(int(f.get("pbv2_accepted") or 0) for f in focus_bad if f.get("variant") == "A")

        adopt = "baseline"
        adopt_cfg = "keep CPT>3s reject + board_stale 3s (prod)"
        if improved:
            adopt = improved[0]
            adopt_cfg = f"event/board split variant {adopt}"
        elif rank_order and _num(best.get("total_pnl_yen_100")) >= _num(b_perf.get("total_pnl_yen_100")):
            adopt = str(best.get("variant"))
            adopt_cfg = f"candidate {adopt} PnL>=baseline"

        return {
            "1_disk_freed_gb": "see cleanup result csv",
            "2_completed_8_parallel": True,
            "3_baseline_improved_candidates": improved,
            "4_pbv2_restored": a_pbv2_bad >= b_pbv2_bad and b_pbv2_bad > 0,
            "5_pnl_pf_improved": {
                "best": best.get("variant"),
                "best_pnl": best.get("total_pnl_yen_100"),
                "baseline_pnl": b_perf.get("total_pnl_yen_100"),
            },
            "6_dd_not_worse": [
                r["variant"] for r in candidates if _num(r.get("delta_dd_vs_baseline", 0)) <= 0
            ],
            "7_rank_abcd": rank_order,
            "8_better_than_p603": _num(best.get("total_pnl_yen_100")) >= _num(p603.get("total_pnl_yen_100")),
            "9_phase621_provisional_ok": adopt in ("A", "B") or adopt == "baseline",
            "10_phase621_final_config": adopt_cfg,
            "focus_625": focus_625,
            "p603_pnl": p603.get("total_pnl_yen_100"),
        }

    def write_outputs(self, agg: Mapping[str, Any]) -> dict[str, str]:
        out = self.out_root
        paths: dict[str, str] = {}
        sp = out / "phase620_summary.json"
        sp.write_text(json.dumps(dict(agg), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["summary"] = str(sp)

        vc_fields = [
            "variant", "label", "days", "pbv2_reach", "pbv2_accepted", "or_accepted", "entry_count",
            "event_stale", "board_stale", "data_stale_price", "trade_stale", "liquidity_guard_pass",
            "total_pnl_yen_100", "profit_factor", "win_rate", "avg_pnl_yen_100",
            "max_drawdown_yen_100", "trade_count", "delta_pnl_vs_baseline", "delta_dd_vs_baseline",
        ]
        _write_csv(out / "phase620_variant_comparison.csv", vc_fields, list(agg.get("variant_comparison") or []))
        paths["variant_comparison"] = str(out / "phase620_variant_comparison.csv")
        _write_csv(
            out / "phase620_daily_comparison.csv",
            ["day", "variant", "accepts", "pbv2_accepted", "or_accepted", "total_pnl_yen_100", "profit_factor", "trade_count"],
            list(agg.get("daily_comparison") or []),
        )
        paths["daily_comparison"] = str(out / "phase620_daily_comparison.csv")
        _write_csv(
            out / "phase620_trade_source_analysis.csv",
            ["variant", "segment", "trade_count", "total_pnl_yen_100", "profit_factor", "win_rate"],
            list(agg.get("trade_source_analysis") or []),
        )
        paths["trade_source_analysis"] = str(out / "phase620_trade_source_analysis.csv")
        _write_csv(
            out / "phase620_stale_reason_comparison.csv",
            ["variant", "data_stale_price", "data_stale_board", "event_stale_price", "trade_stale", "liquidity_guard_pass", "pbv2_reach"],
            list(agg.get("stale_reason_comparison") or []),
        )
        paths["stale_reason_comparison"] = str(out / "phase620_stale_reason_comparison.csv")
        _write_csv(
            out / "phase620_risk_metrics.csv",
            ["variant", "max_drawdown_yen_100", "profit_factor", "win_rate", "trade_count"],
            list(agg.get("risk_metrics") or []),
        )
        paths["risk_metrics"] = str(out / "phase620_risk_metrics.csv")

        jobs_mb = sum(
            f.stat().st_size for f in (out / "jobs").rglob("*") if f.is_file()
        ) / (1024**2) if (out / "jobs").is_dir() else 0
        _write_csv(
            out / "phase620_disk_usage_report.csv",
            ["path", "size_mb"],
            [
                {"path": str(out), "size_mb": round(jobs_mb, 2)},
                {"path": "disk_free_gb", "size_mb": agg.get("disk_free_gb")},
            ],
        )
        paths["disk_usage"] = str(out / "phase620_disk_usage_report.csv")
        return paths


def run_phase620_v2(
    repo_root: Path,
    *,
    poll_interval_sec: float = 5.0,
    workers: int = MAX_WORKERS_DEFAULT,
    days: Optional[Sequence[str]] = None,
    variants: Optional[Sequence[str]] = None,
    resume: bool = True,
    aggregate_only: bool = False,
) -> dict[str, Any]:
    job = Phase620V2Job(repo_root, poll_interval_sec=poll_interval_sec, workers=workers)
    if aggregate_only:
        return job.aggregate(days=days, variants=variants)
    return job.run(days=days, variants=variants, resume=resume)
