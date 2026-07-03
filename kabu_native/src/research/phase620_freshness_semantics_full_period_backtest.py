"""
Phase620: Full-period backtest of freshness semantics variants (Baseline + A–F).
Research-only; monkey-patches evaluate_entry_data_freshness in worker processes.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
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
    _pnl_yen_100_from_pct,
    _trade_rows_from_structural,
)
from research.phase620_freshness_semantics_variant import (
    SIM_EVENT_LAG_SEC,
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

VERDICT = "phase620_freshness_semantics_full_period_backtest_done"
CHECKPOINT_DIR = "_phase620_freshness_checkpoints"
REPORT_SUBDIR = "phase620_freshness_backtest"
MAX_SAMPLE_ROWS = int(os.environ.get("PHASE620_MAX_SAMPLE_ROWS", "3000"))
DISK_FREE_ABORT_GB = float(os.environ.get("PHASE620_DISK_FREE_ABORT_GB", "20"))
GZIP_OUTPUT = True

PRE_PBV2_STALE = frozenset(
    {"data_stale_price", "data_stale_board", "event_stale_price", "data_stale"}
)

FOCUS_DAYS = {
    "2026-06-25": "pbv2_repro",
    "2026-06-29": "pbv2_restore",
    "2026-06-30": "pbv2_restore",
}


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
        raise RuntimeError(f"disk free {free:.1f}GB < abort threshold {DISK_FREE_ABORT_GB}GB")


def _parse_audit_stats(audit_path: Path, *, max_reject_samples: int = 200) -> dict[str, Any]:
    stale = Counter()
    trade_tags = Counter()
    pbv2_reach = 0
    pbv2_accept = 0
    reject_samples: list[dict[str, Any]] = []
    for line in audit_path.read_text(encoding="utf-8").splitlines() if audit_path.is_file() else []:
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
            if len(reject_samples) < max_reject_samples:
                reject_samples.append(
                    {
                        "symbol": row.get("symbol"),
                        "reject_reason": rr,
                        "price_age_sec": row.get("price_age_sec"),
                        "board_age_sec": row.get("board_age_sec"),
                        "price_freshness_source": row.get("price_freshness_source"),
                    }
                )
        else:
            pbv2_reach += 1
        src = str(row.get("price_freshness_source") or "")
        if src in ("liquidity_stale_trade", "liquidity_guard_pass"):
            trade_tags[src] += 1
        if row.get("entry_decision"):
            pbv2_accept += 1
    return {
        "data_stale_price": int(stale.get("data_stale_price", 0)),
        "data_stale_board": int(stale.get("data_stale_board", 0)),
        "event_stale_price": int(stale.get("event_stale_price", 0)),
        "trade_stale_tagged": int(trade_tags.get("liquidity_stale_trade", 0)),
        "liquidity_guard_pass": int(trade_tags.get("liquidity_guard_pass", 0)),
        "pbv2_reach_audit": pbv2_reach,
        "pbv2_accept_audit": pbv2_accept,
        "reject_samples": reject_samples,
    }


def _gate_counts_from_events(
    events: Sequence[Mapping[str, Any]], accepted: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    pbv2_reach = 0
    for e in events:
        if str(e.get("event_type")) != "candidate":
            continue
        rr = str(e.get("reject_reason") or e.get("reason") or "")
        if rr not in PRE_PBV2_STALE:
            pbv2_reach += 1
    pbv2_acc = sum(1 for a in accepted if str(a.get("entry_type") or "PBV2").upper() == "PBV2")
    or_acc = sum(1 for a in accepted if str(a.get("entry_type") or "").upper() == "OR")
    return {
        "pbv2_reach": pbv2_reach,
        "pbv2_accepted": pbv2_acc,
        "or_accepted": or_acc,
        "entry_count": len(accepted),
    }


def _run_variant_day(
    repo_root: Path,
    day_iso: str,
    variant_id: str,
    *,
    poll_interval_sec: float,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    for p in (kabu / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    _disk_guard(kabu)
    apply_variant(variant_id)
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
            entry_freshness_board_fallback_enabled=False,
        )
        push_dir = kabu / "data" / "push_jsonl" / day_iso
        out_dir = (
            kabu
            / "results"
            / "small_paper"
            / CHECKPOINT_DIR
            / f"{day_iso.replace('-', '')}_{variant_id}_replay"
        )
        t0 = time.monotonic()
        result = run_push_replay_dry_run(
            cfg,
            push_dir=push_dir,
            output_dir=out_dir,
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
        audit_path = out_dir / "entry_scan_audit.jsonl"
        audit_stats = _parse_audit_stats(audit_path)
        gate = _gate_counts_from_events(events, accepted)
        tags = tag_counts()

        payload = {
            "day": day_iso,
            "variant": variant_id,
            "variant_label": VARIANTS[variant_id].label,
            "runtime_sec": round(runtime, 1),
            "push_rows": int(summary.get("push_rows") or 0),
            "gate_evaluations": int(summary.get("gate_evaluations") or 0),
            "entry_candidates": int(
                summary.get("candidate_count")
                or len([e for e in events if e.get("event_type") == "candidate"])
            ),
            "accepts": len(accepted),
            "reject_reason_counts": dict(rc),
            "stale_counts": {
                "data_stale_price": int(rc.get("data_stale_price") or audit_stats["data_stale_price"]),
                "data_stale_board": int(rc.get("data_stale_board") or audit_stats["data_stale_board"]),
                "event_stale_price": int(rc.get("event_stale_price") or audit_stats["event_stale_price"]),
                "trade_stale_tagged": int(tags.get("liquidity_stale_trade", 0) or audit_stats["trade_stale_tagged"]),
                "liquidity_guard_pass": int(tags.get("liquidity_guard_pass", 0) or audit_stats["liquidity_guard_pass"]),
            },
            "pbv2_reach": gate["pbv2_reach"],
            "pbv2_accepted": gate["pbv2_accepted"],
            "or_accepted": gate["or_accepted"],
            "entry_count": gate["entry_count"],
            "audit": audit_stats,
            "performance": perf,
            "trade_rows": trade_rows,
            "reject_samples": audit_stats.get("reject_samples") or [],
            "sim_event_lag_sec": SIM_EVENT_LAG_SEC,
        }
    finally:
        restore_variant()
        if out_dir.is_dir():
            shutil.rmtree(out_dir, ignore_errors=True)
    return payload


def _run_job_task(task: Mapping[str, Any]) -> dict[str, Any]:
    repo_root = Path(str(task["repo_root"]))
    day_iso = str(task["day"])
    variant_id = str(task["variant"])
    poll = float(task["poll_interval_sec"])
    resume = bool(task.get("resume", True))
    job = Phase620FreshnessBacktestJob(repo_root, poll_interval_sec=poll)

    if resume:
        cached = job._load_day_with_trades(day_iso, variant_id)
        if cached is not None:
            print(f"[worker] skip cached {variant_id} {day_iso}", flush=True)
            return cached

    print(f"[worker] {variant_id} {day_iso} ...", flush=True)
    payload = _run_variant_day(repo_root, day_iso, variant_id, poll_interval_sec=poll)
    job._save_ckpt(payload)
    print(
        f"[worker] done {variant_id} {day_iso} accepts={payload.get('accepts')} "
        f"pnl={payload.get('performance', {}).get('total_pnl_yen_100')}",
        flush=True,
    )
    return payload


@dataclass
class Phase620FreshnessBacktestJob:
    repo_root: Path
    poll_interval_sec: float = 5.0

    def __post_init__(self) -> None:
        self.kabu = resolve_kabu_root(self.repo_root)
        self.reports = resolve_reports_dir(self.kabu)
        self.push_root = self.kabu / "data" / "push_jsonl"
        self.ckpt_root = self.kabu / "results" / "small_paper" / CHECKPOINT_DIR
        self.ckpt_root.mkdir(parents=True, exist_ok=True)
        self.out_dir = self.reports / REPORT_SUBDIR
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def _ckpt_path(self, day_iso: str, variant: str) -> Path:
        ext = ".json.gz" if GZIP_OUTPUT else ".json"
        return self.ckpt_root / f"{day_iso.replace('-', '')}_{variant}{ext}"

    def _load_ckpt(self, day_iso: str, variant: str) -> Optional[dict[str, Any]]:
        for ext in (".json.gz", ".json"):
            p = self.ckpt_root / f"{day_iso.replace('-', '')}_{variant}{ext}"
            if not p.is_file():
                continue
            try:
                if ext == ".json.gz":
                    with gzip.open(p, "rt", encoding="utf-8") as fh:
                        return json.load(fh)
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def _save_ckpt(self, payload: Mapping[str, Any]) -> None:
        p = self._ckpt_path(str(payload["day"]), str(payload["variant"]))
        slim = {k: v for k, v in payload.items() if k not in ("trade_rows", "reject_samples")}
        if GZIP_OUTPUT:
            with gzip.open(p, "wt", encoding="utf-8") as fh:
                json.dump(slim, fh, ensure_ascii=False, indent=2, default=str)
        else:
            p.write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        trades_path = p.with_suffix(".trades.json.gz" if GZIP_OUTPUT else ".trades.json")
        trades = list(payload.get("trade_rows") or [])[:MAX_SAMPLE_ROWS]
        if GZIP_OUTPUT:
            with gzip.open(trades_path, "wt", encoding="utf-8") as fh:
                json.dump(trades, fh, ensure_ascii=False, default=str)
        else:
            trades_path.write_text(json.dumps(trades, ensure_ascii=False, default=str), encoding="utf-8")
        rej_path = p.with_suffix(".reject.json.gz" if GZIP_OUTPUT else ".reject.json")
        rejects = list(payload.get("reject_samples") or [])[:200]
        if GZIP_OUTPUT:
            with gzip.open(rej_path, "wt", encoding="utf-8") as fh:
                json.dump(rejects, fh, ensure_ascii=False, default=str)
        else:
            rej_path.write_text(json.dumps(rejects, ensure_ascii=False, default=str), encoding="utf-8")

    def _load_day_with_trades(self, day_iso: str, variant: str) -> Optional[dict[str, Any]]:
        base = self._load_ckpt(day_iso, variant)
        if base is None:
            return None
        stem = self.ckpt_root / f"{day_iso.replace('-', '')}_{variant}"
        for trades_ext in (".trades.json.gz", ".trades.json"):
            tp = stem.with_suffix(trades_ext) if trades_ext.startswith(".trades") else Path(str(stem) + trades_ext)
            tp = self.ckpt_root / f"{day_iso.replace('-', '')}_{variant}{trades_ext}"
            if tp.is_file():
                if trades_ext.endswith(".gz"):
                    with gzip.open(tp, "rt", encoding="utf-8") as fh:
                        base["trade_rows"] = json.load(fh)
                else:
                    base["trade_rows"] = json.loads(tp.read_text(encoding="utf-8"))
                break
        else:
            base["trade_rows"] = []
        return base

    def run(
        self,
        *,
        days: Optional[Sequence[str]] = None,
        variants: Optional[Sequence[str]] = None,
        resume: bool = True,
        workers: int = 4,
    ) -> dict[str, Any]:
        all_days = list(days or _discover_push_days(self.push_root))
        all_variants = list(variants or list(VARIANTS.keys()))
        workers = max(1, int(workers))
        tasks = []
        preloaded: dict[tuple[str, str], dict[str, Any]] = {}

        for variant_id in all_variants:
            for day_iso in all_days:
                if resume:
                    cached = self._load_day_with_trades(day_iso, variant_id)
                    if cached is not None:
                        preloaded[(variant_id, day_iso)] = cached
                        continue
                tasks.append(
                    {
                        "repo_root": str(self.repo_root),
                        "day": day_iso,
                        "variant": variant_id,
                        "poll_interval_sec": self.poll_interval_sec,
                        "resume": resume,
                    }
                )

        print(
            f"phase620 parallel: workers={workers} jobs={len(tasks)} cached={len(preloaded)} "
            f"days={len(all_days)} variants={len(all_variants)}",
            flush=True,
        )
        results: dict[tuple[str, str], dict[str, Any]] = dict(preloaded)
        failed: list[str] = []
        if tasks:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_run_job_task, t): (t["variant"], t["day"]) for t in tasks}
                for fut in as_completed(futures):
                    key = futures[fut]
                    try:
                        results[key] = fut.result()
                    except Exception as exc:
                        print(f"phase620 FAILED {key}: {exc}", flush=True)
                        failed.append(f"{key[0]}:{key[1]}")

        if failed:
            raise RuntimeError(f"failed jobs: {failed}")

        by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for variant_id in all_variants:
            for day_iso in all_days:
                row = results.get((variant_id, day_iso))
                if row is None:
                    raise RuntimeError(f"missing {variant_id} {day_iso}")
                by_variant[variant_id].append(row)

        agg = self._aggregate(by_variant, all_days, all_variants)
        paths = self.write_outputs(agg)
        agg["output_paths"] = paths
        if os.environ.get("PHASE620_DELETE_CHECKPOINTS", "0") == "1":
            self._cleanup_checkpoints()
        return agg

    def _cleanup_checkpoints(self) -> None:
        for p in self.ckpt_root.glob("*"):
            if p.is_file():
                p.unlink(missing_ok=True)

    def _aggregate(
        self,
        by_variant: Mapping[str, Sequence[Mapping[str, Any]]],
        all_days: Sequence[str],
        all_variants: Sequence[str],
    ) -> dict[str, Any]:
        baseline_id = "baseline"
        b_days = list(by_variant.get(baseline_id, []))
        b_trades: list[dict[str, Any]] = []
        for d in b_days:
            b_trades.extend(list(d.get("trade_rows") or []))
        b_perf = _metrics_from_trade_rows(b_trades)

        variant_rows: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []
        stale_rows: list[dict[str, Any]] = []
        symbol_rows: list[dict[str, Any]] = []
        source_rows: list[dict[str, Any]] = []
        risk_rows: list[dict[str, Any]] = []
        focus_rows: list[dict[str, Any]] = []

        for vid in all_variants:
            days = list(by_variant.get(vid, []))
            trades: list[dict[str, Any]] = []
            for d in days:
                trades.extend(list(d.get("trade_rows") or []))
            perf = _metrics_from_trade_rows(trades)
            stale_sum = Counter()
            for d in days:
                sc = d.get("stale_counts") or {}
                for k, v in sc.items():
                    stale_sum[k] += int(v or 0)
            gate_sum = {
                "pbv2_reach": sum(int(d.get("pbv2_reach") or 0) for d in days),
                "pbv2_accepted": sum(int(d.get("pbv2_accepted") or 0) for d in days),
                "or_accepted": sum(int(d.get("or_accepted") or 0) for d in days),
                "entry_count": sum(int(d.get("entry_count") or d.get("accepts") or 0) for d in days),
            }
            variant_rows.append(
                {
                    "variant": vid,
                    "label": VARIANTS[vid].label,
                    "days": len(days),
                    **gate_sum,
                    **{f"stale_{k}": v for k, v in stale_sum.items()},
                    "total_pnl_yen_100": perf["total_pnl_yen_100"],
                    "profit_factor": perf.get("profit_factor"),
                    "win_rate": perf.get("win_rate"),
                    "avg_pnl_yen_100": perf["avg_pnl_yen_100"],
                    "max_drawdown_yen_100": perf["max_drawdown_yen_100"],
                    "trade_count": perf["trade_count"],
                    "avg_hold_sec": perf.get("avg_hold_sec"),
                    "delta_pnl_vs_baseline": round(
                        _num(perf["total_pnl_yen_100"]) - _num(b_perf["total_pnl_yen_100"]), 2
                    ),
                    "delta_pf_vs_baseline": _delta_pf(perf.get("profit_factor"), b_perf.get("profit_factor")),
                    "delta_dd_vs_baseline": round(
                        _num(perf["max_drawdown_yen_100"]) - _num(b_perf["max_drawdown_yen_100"]), 2
                    ),
                }
            )
            stale_rows.append({"variant": vid, **dict(stale_sum), **gate_sum})
            risk_rows.append(
                {
                    "variant": vid,
                    "max_drawdown_yen_100": perf["max_drawdown_yen_100"],
                    "profit_factor": perf.get("profit_factor"),
                    "win_rate": perf.get("win_rate"),
                    "trade_count": perf["trade_count"],
                    "sharpe_proxy": _sharpe_proxy(trades),
                }
            )
            for d in days:
                dp = d.get("performance") or {}
                daily_rows.append(
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
                            "focus": FOCUS_DAYS[str(d.get("day"))],
                            "variant": vid,
                            "pbv2_reach": d.get("pbv2_reach"),
                            "pbv2_accepted": d.get("pbv2_accepted"),
                            "accepts": d.get("accepts"),
                            "pnl_yen_100": dp.get("total_pnl_yen_100"),
                        }
                    )
            sym_pnl: dict[str, float] = defaultdict(float)
            for t in trades:
                sym_pnl[str(t.get("symbol"))] += _num(t.get("pnl_yen_100"))
            for sym, pnl in sorted(sym_pnl.items(), key=lambda kv: kv[1], reverse=True):
                symbol_rows.append({"variant": vid, "symbol": sym, "pnl_yen_100": round(pnl, 2)})

            for seg, pred in (
                ("liquidity_guard_pass", lambda t: str(t.get("price_freshness_source")) == "liquidity_guard_pass"),
                ("liquidity_stale_trade", lambda t: str(t.get("price_freshness_source")) == "liquidity_stale_trade"),
                ("board_fallback", lambda t: str(t.get("price_freshness_source")) == "board_fallback"),
                ("current_price_time", lambda t: str(t.get("price_freshness_source")) in ("", "current_price_time")),
            ):
                subset = [t for t in trades if pred(t)]
                if not subset:
                    continue
                m = _metrics_from_trade_rows(subset)
                source_rows.append({"variant": vid, "segment": seg, **m})

        mandatory = self._mandatory_answers(variant_rows, b_perf, focus_rows)
        p603_ref = self._load_phase603_reference()

        return {
            "verdict": VERDICT,
            "generated_at": _now_iso(),
            "period_days": list(all_days),
            "day_count": len(all_days),
            "variant_ids": list(all_variants),
            "sim_event_lag_sec": SIM_EVENT_LAG_SEC,
            "baseline_performance": b_perf,
            "variant_comparison": variant_rows,
            "daily_comparison": daily_rows,
            "stale_reason_comparison": stale_rows,
            "trade_source_analysis": source_rows,
            "symbol_pnl": symbol_rows[: MAX_SAMPLE_ROWS],
            "risk_metrics": risk_rows,
            "focus_days": focus_rows,
            "mandatory_answers": mandatory,
            "phase603_reference": p603_ref,
            "disk_free_gb_end": round(_free_gb(self.kabu), 2),
        }

    def _load_phase603_reference(self) -> dict[str, Any]:
        ckpt = self.kabu / "results" / "small_paper" / "_phase603_backtest_checkpoints"
        if not ckpt.is_dir():
            return {"available": False, "notes": "no phase603 checkpoints on disk"}
        trades: list[dict[str, Any]] = []
        for p in sorted(ckpt.glob("*_phase603.trades.json")):
            try:
                trades.extend(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        if not trades:
            return {"available": False, "notes": "phase603 dir exists but no trade files"}
        m = _metrics_from_trade_rows(trades)
        return {"available": True, "performance": m, "trade_count": len(trades)}

    def _mandatory_answers(
        self,
        variant_rows: Sequence[Mapping[str, Any]],
        b_perf: Mapping[str, Any],
        focus_rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        improved = [
            r["variant"]
            for r in variant_rows
            if r["variant"] != "baseline"
            and _num(r.get("delta_pnl_vs_baseline")) > 0
            and _num(r.get("max_drawdown_yen_100")) <= _num(b_perf.get("max_drawdown_yen_100")) * 1.05
        ]
        best_pnl = max(variant_rows, key=lambda r: _num(r.get("total_pnl_yen_100")))
        event_thresh = self._best_threshold_variant(variant_rows, ("D", "baseline", "E"), "event")
        board_thresh = self._best_threshold_variant(variant_rows, ("F", "baseline", "A"), "board")
        a_row = next((r for r in variant_rows if r["variant"] == "A"), {})
        b_row = next((r for r in variant_rows if r["variant"] == "B"), {})
        c_row = next((r for r in variant_rows if r["variant"] == "C"), {})
        focus_625 = [r for r in focus_rows if r.get("day") == "2026-06-25"]
        focus_bad = [r for r in focus_rows if r.get("day") in ("2026-06-29", "2026-06-30")]
        p603 = self._load_phase603_reference()
        p603_pnl = _num((p603.get("performance") or {}).get("total_pnl_yen_100"))
        b_pnl = _num(b_row.get("total_pnl_yen_100"))

        adopt = "baseline"
        adopt_reason = "no candidate beats baseline on PnL+DD"
        if improved:
            adopt = improved[0]
            adopt_reason = f"positive PnL delta with DD within 5%: {improved}"
        elif _num(a_row.get("total_pnl_yen_100")) >= _num(b_perf.get("total_pnl_yen_100")):
            adopt = "A"
            adopt_reason = "soft trade_stale matches/beats baseline PnL"

        return {
            "1_baseline_improved_candidates": improved,
            "2_pbv2_reproduced_625": self._focus_ok(focus_625),
            "3_pnl_pf_improved": {
                "best_variant": best_pnl.get("variant"),
                "best_pnl": best_pnl.get("total_pnl_yen_100"),
                "baseline_pnl": b_perf.get("total_pnl_yen_100"),
                "best_pf": best_pnl.get("profit_factor"),
            },
            "4_dd_not_worse": [
                r["variant"]
                for r in variant_rows
                if _num(r.get("max_drawdown_yen_100")) <= _num(b_perf.get("max_drawdown_yen_100")) * 1.05
            ],
            "5_trade_stale_soft_ok": _num(a_row.get("total_pnl_yen_100")) >= _num(b_perf.get("total_pnl_yen_100"))
            and _num(a_row.get("max_drawdown_yen_100")) <= _num(b_perf.get("max_drawdown_yen_100")) * 1.1,
            "6_optimal_event_threshold_sec": event_thresh,
            "7_optimal_board_threshold_sec": board_thresh,
            "8_better_than_phase603_fallback": p603.get("available")
            and _num(b_pnl) >= p603_pnl,
            "9_adopt_candidate": adopt,
            "10_production_ready": adopt == "baseline"
            or (adopt != "baseline" and len(improved) > 0),
            "adopt_reason": adopt_reason,
            "candidate_B_vs_A_pnl_delta": round(_num(b_row.get("total_pnl_yen_100")) - _num(a_row.get("total_pnl_yen_100")), 2),
            "candidate_C_vs_A_pnl_delta": round(_num(c_row.get("total_pnl_yen_100")) - _num(a_row.get("total_pnl_yen_100")), 2),
            "focus_bad_days_restore": self._focus_ok(focus_bad),
        }

    def _focus_ok(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"ok": False, "notes": "no focus day data"}
        by_var: dict[str, list] = defaultdict(list)
        for r in rows:
            by_var[str(r.get("variant"))].append(r)
        baseline_pbv2 = sum(int(r.get("pbv2_accepted") or 0) for r in by_var.get("baseline", []))
        out = {}
        for vid, vrows in by_var.items():
            pbv2 = sum(int(r.get("pbv2_accepted") or 0) for r in vrows)
            out[vid] = {"pbv2_accepted": pbv2, "vs_baseline": pbv2 - baseline_pbv2}
        return {"baseline_pbv2": baseline_pbv2, "by_variant": out}

    def _best_threshold_variant(
        self, rows: Sequence[Mapping[str, Any]], candidates: Sequence[str], kind: str
    ) -> dict[str, Any]:
        subset = [r for r in rows if r.get("variant") in candidates]
        if not subset:
            return {"kind": kind, "best": None}
        best = max(subset, key=lambda r: (_num(r.get("total_pnl_yen_100")), _num(r.get("profit_factor") or 0)))
        mapping = {"D": 2.0, "baseline": 3.0, "E": 5.0, "F": 2.0, "A": 3.0}
        return {"kind": kind, "best_variant": best.get("variant"), "threshold_sec": mapping.get(str(best.get("variant")), 3.0)}

    def write_outputs(self, agg: Mapping[str, Any]) -> dict[str, str]:
        out = self.out_dir
        paths: dict[str, str] = {}
        summary_path = out / "phase620_summary.json"
        summary_path.write_text(json.dumps(dict(agg), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["summary"] = str(summary_path)

        _write_csv(out / "phase620_variant_comparison.csv", _variant_csv_fields(), list(agg.get("variant_comparison") or []))
        paths["variant_comparison"] = str(out / "phase620_variant_comparison.csv")
        _write_csv(out / "phase620_daily_comparison.csv", ["day", "variant", "accepts", "pbv2_accepted", "or_accepted", "total_pnl_yen_100", "profit_factor", "trade_count"], list(agg.get("daily_comparison") or []))
        paths["daily_comparison"] = str(out / "phase620_daily_comparison.csv")
        _write_csv(out / "phase620_trade_source_analysis.csv", ["variant", "segment", "trade_count", "total_pnl_yen_100", "profit_factor", "win_rate", "avg_pnl_yen_100"], list(agg.get("trade_source_analysis") or []))
        paths["trade_source_analysis"] = str(out / "phase620_trade_source_analysis.csv")
        _write_csv(out / "phase620_stale_reason_comparison.csv", ["variant", "data_stale_price", "data_stale_board", "event_stale_price", "trade_stale_tagged", "liquidity_guard_pass", "pbv2_reach", "pbv2_accepted"], list(agg.get("stale_reason_comparison") or []))
        paths["stale_reason_comparison"] = str(out / "phase620_stale_reason_comparison.csv")
        _write_csv(out / "phase620_symbol_pnl.csv", ["variant", "symbol", "pnl_yen_100"], list(agg.get("symbol_pnl") or []))
        paths["symbol_pnl"] = str(out / "phase620_symbol_pnl.csv")
        _write_csv(out / "phase620_risk_metrics.csv", ["variant", "max_drawdown_yen_100", "profit_factor", "win_rate", "trade_count", "sharpe_proxy"], list(agg.get("risk_metrics") or []))
        paths["risk_metrics"] = str(out / "phase620_risk_metrics.csv")

        disk_rows = [{"path": str(self.ckpt_root), "size_mb": _dir_size_mb(self.ckpt_root), "files": _file_count(self.ckpt_root)}]
        disk_rows.append({"path": str(out), "size_mb": _dir_size_mb(out), "files": _file_count(out)})
        _write_csv(out / "phase620_disk_usage_report.csv", ["path", "size_mb", "files"], disk_rows)
        paths["disk_usage"] = str(out / "phase620_disk_usage_report.csv")
        return paths


def _variant_csv_fields() -> list[str]:
    return [
        "variant", "label", "days", "pbv2_reach", "pbv2_accepted", "or_accepted", "entry_count",
        "stale_data_stale_price", "stale_data_stale_board", "stale_event_stale_price",
        "stale_trade_stale_tagged", "stale_liquidity_guard_pass",
        "total_pnl_yen_100", "profit_factor", "win_rate", "avg_pnl_yen_100",
        "max_drawdown_yen_100", "trade_count", "avg_hold_sec",
        "delta_pnl_vs_baseline", "delta_pf_vs_baseline", "delta_dd_vs_baseline",
    ]


def _delta_pf(a: Any, b: Any) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(_num(a) - _num(b), 4)


def _sharpe_proxy(trades: Sequence[Mapping[str, Any]]) -> Optional[float]:
    pnls = [_num(t.get("pnl_yen_100")) for t in trades]
    if len(pnls) < 2:
        return None
    mu = statistics.mean(pnls)
    sd = statistics.stdev(pnls)
    if sd <= 0:
        return None
    return round(mu / sd, 4)


def _dir_size_mb(path: Path) -> float:
    total = 0
    if path.is_file():
        return round(path.stat().st_size / (1024**2), 2)
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return round(total / (1024**2), 2)


def _file_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file())


def run_phase620_freshness_backtest(
    repo_root: Path,
    *,
    poll_interval_sec: float = 5.0,
    resume: bool = True,
    days: Optional[Sequence[str]] = None,
    variants: Optional[Sequence[str]] = None,
    workers: int = 4,
) -> dict[str, Any]:
    job = Phase620FreshnessBacktestJob(repo_root, poll_interval_sec=poll_interval_sec)
    return job.run(days=days, variants=variants, resume=resume, workers=workers)
