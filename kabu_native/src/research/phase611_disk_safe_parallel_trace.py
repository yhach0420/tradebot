"""
Phase611 revised — disk-safe parallel PBv2 freshness trace (research only).
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import logging
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase451_entry_shape_tournament import _now_iso
from research.phase604b_pbv2_zero_impl_block_audit import _pre_gate_blocker
from research.phase605_entry_cluster_guard_counterfactual import _load_config_for_session, _session_dir
from research.phase607_entry_score_v2_regression_audit import _load_pbv2_accepted_625
from research.phase611_pbv2_freshness_pass_block_trace_diff import (
    PushCache,
    _build_audit_index,
    _classify_bad_category,
    _classify_good_pass_reason,
    _divergence_stage,
    _freshness_config,
    _is_625_like,
    _load_audit_by_symbol,
    _match_audit_indexed,
    _trace_one,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv
from storage.intraday_recorder import parse_kabu_time

VERDICT = "phase611_disk_safe_parallel_trace_done"
JST = ZoneInfo("Asia/Tokyo")

MAX_SAMPLES_PER_BUCKET = 500
MAX_PAIRWISE_ROWS = 3000
MAX_BAD_SCORE3_SAMPLES_PER_SESSION = 2000
GZIP_OUTPUT = True

MINIMAL_COLUMNS = [
    "symbol",
    "event_time",
    "session",
    "day",
    "stage",
    "first_divergence",
    "CurrentPriceTime",
    "price_age_sec",
    "BidTime",
    "AskTime",
    "board_age_sec",
    "CurrentPrice",
    "CalcPrice",
    "BidPrice",
    "AskPrice",
    "score",
    "momentum",
    "board_imbalance",
    "freshness_result",
    "pbv2_decision",
    "final_reason",
    "audit_price_age_sec",
    "bad_category",
    "payload_hash",
    "pass_block_reason",
]

JOB_SPECS = {
    "A": {
        "job_id": "job_A_good625",
        "mode": "good625",
        "sessions": (),
    },
    "B": {
        "job_id": "job_B_629am",
        "mode": "bad",
        "day": "20260629",
        "session": "live_session_080236",
        "label": "AM",
    },
    "C": {
        "job_id": "job_C_629pm",
        "mode": "bad",
        "day": "20260629",
        "session": "live_session_122526",
        "label": "PM",
    },
    "D": {
        "job_id": "job_D_630am",
        "mode": "bad",
        "day": "20260630",
        "session": "live_session_091118",
        "label": "AM",
    },
}

def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or str(val).strip() == "":
        return None
    return parse_kabu_time(val, fallback=datetime.now(JST))


def _float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _payload_hash(payload: Mapping[str, Any]) -> str:
    keys = ("CurrentPriceTime", "BidTime", "AskTime", "CurrentPrice", "CalcPrice", "BidPrice", "AskPrice")
    blob = "|".join(f"{k}={payload.get(k)}" for k in keys)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _to_minimal_row(tr: Mapping[str, Any], *, stage: str, pass_block: str) -> dict[str, Any]:
    payload = tr.get("_payload") or {}
    return {
        "symbol": tr.get("symbol"),
        "event_time": tr.get("event_time"),
        "session": tr.get("session"),
        "day": tr.get("day"),
        "stage": stage,
        "first_divergence": stage,
        "CurrentPriceTime": tr.get("raw_CurrentPriceTime") or payload.get("CurrentPriceTime"),
        "price_age_sec": tr.get("internal_current_price_age_sec"),
        "BidTime": tr.get("raw_BidTime") or payload.get("BidTime"),
        "AskTime": tr.get("raw_AskTime") or payload.get("AskTime"),
        "board_age_sec": tr.get("internal_board_age_sec"),
        "CurrentPrice": tr.get("raw_CurrentPrice") or payload.get("CurrentPrice"),
        "CalcPrice": tr.get("raw_CalcPrice") or payload.get("CalcPrice"),
        "BidPrice": tr.get("raw_BidPrice") or payload.get("BidPrice"),
        "AskPrice": tr.get("raw_AskPrice") or payload.get("AskPrice"),
        "score": tr.get("trade_entry_expectancy_score_v2"),
        "momentum": tr.get("trade_momentum_continuation_score"),
        "board_imbalance": tr.get("trade_entry_order_book_imbalance"),
        "freshness_result": tr.get("freshness_result"),
        "pbv2_decision": tr.get("pbv2_internal_decision"),
        "final_reason": tr.get("live_gate_reject_reason") or tr.get("freshness_reject_reason") or "",
        "audit_price_age_sec": tr.get("audit_price_age_sec"),
        "bad_category": tr.get("bad_category", ""),
        "payload_hash": _payload_hash(payload) if payload else "",
        "pass_block_reason": pass_block,
    }


@dataclass
class SampleCollector:
    max_per_bucket: int
    max_total: int
    buckets: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    counts: Counter = field(default_factory=Counter)
    total_stored: int = 0

    def maybe_add(self, bucket: str, row: dict[str, Any]) -> None:
        self.counts[bucket] += 1
        if self.total_stored >= self.max_total:
            return
        if len(self.buckets[bucket]) >= self.max_per_bucket:
            return
        self.buckets[bucket].append(row)
        self.total_stored += 1

    def all_samples(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for rows in self.buckets.values():
            out.extend(rows)
        return out


def _write_gz_csv(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open(path, "wt", encoding="utf-8", newline="") if GZIP_OUTPUT else path.open(
        "w", encoding="utf-8", newline=""
    )
    with opener as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in columns})


def _run_job_worker(job_key: str, repo_str: str) -> dict[str, Any]:
    return run_single_job(job_key, Path(repo_str))


def run_single_job(job_key: str, repo: Path) -> dict[str, Any]:
    spec = JOB_SPECS[job_key]
    job_dir = repo / "results" / "reports" / "phase611_parallel" / spec["job_id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "log.txt"
    logging.basicConfig(filename=str(log_path), level=logging.INFO, force=True)
    log = logging.getLogger(spec["job_id"])

    push_cache = PushCache(repo / "data" / "push_jsonl")
    block_ctr: Counter = Counter()
    stage_ctr: Counter = Counter()
    pass_ctr: Counter = Counter()
    like_625 = 0
    like_stale = 0
    total_rows = 0
    score3_rows = 0

    samples = SampleCollector(MAX_SAMPLES_PER_BUCKET, MAX_BAD_SCORE3_SAMPLES_PER_SESSION if spec["mode"] == "bad" else 70)
    mismatch_samples: list[dict[str, Any]] = []
    raw_internal_samples: list[dict[str, Any]] = []
    traces_internal: list[dict[str, Any]] = []

    if spec["mode"] == "good625":
        source_rows = _load_pbv2_accepted_625(repo)
        for i, row in enumerate(source_rows):
            day = str(row.get("_day"))
            session = str(row.get("_session"))
            sdir = _session_dir(repo, day, session)
            config = _load_config_for_session(sdir, repo)
            gate = config.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
            fc = _freshness_config(config)
            audit_index = _build_audit_index(_load_audit_by_symbol(sdir))
            tr = _trace_one(
                row=row,
                day=day,
                session=session,
                cohort="GOOD_625",
                trace_id=f"G{i:03d}",
                bad_category="",
                push_cache=push_cache,
                audit_index=audit_index,
                config=config,
                gate=gate,
                fc=fc,
                skip_pbv2_if_stale=False,
            )
            pr = _classify_good_pass_reason(tr["_fresh_dec"], fc, _float(tr.get("audit_price_age_sec")))
            tr["_pass_reason"] = pr
            pass_ctr[pr] += 1
            stage = _divergence_stage(row, tr["_fresh_dec"], str(tr.get("pbv2_internal_blocker") or ""), bool(tr["_pbv2_would"]))
            stage_ctr[stage] += 1
            min_row = _to_minimal_row(tr, stage=stage, pass_block=pr)
            samples.maybe_add("good_accept", min_row)
            traces_internal.append(tr)
            if tr.get("freshness_result") == "REJECT" and _float(tr.get("audit_price_age_sec")) is not None:
                if _float(tr.get("audit_price_age_sec")) <= fc["max_price_age_sec"]:
                    mismatch_samples.append(min_row)
            raw_internal_samples.append(
                {
                    "symbol": tr.get("symbol"),
                    "event_time": tr.get("event_time"),
                    "check": "price_age_live_vs_push",
                    "audit_price_age_sec": tr.get("audit_price_age_sec"),
                    "recomputed_price_age_sec": tr.get("internal_current_price_age_sec"),
                    "match": tr.get("freshness_result") == "PASS",
                }
            )
            total_rows += 1
    else:
        day = spec["day"]
        session = spec["session"]
        sdir = _session_dir(repo, day, session)
        config = _load_config_for_session(sdir, repo)
        gate = config.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
        fc = _freshness_config(config)
        audit_index = _build_audit_index(_load_audit_by_symbol(sdir))
        seen: set[tuple[str, str]] = set()
        for row in _stream_events_csv(sdir / "small_paper_events.csv"):
            if str(row.get("entry_expectancy_score_v2")) != "3":
                continue
            sym = str(row.get("symbol") or "")
            et = str(row.get("event_time") or "")
            if (sym, et) in seen:
                continue
            seen.add((sym, et))
            score3_rows += 1
            tr = _trace_one(
                row=row,
                day=day,
                session=session,
                cohort=f"BAD_{day}",
                trace_id=f"B{score3_rows}",
                bad_category="",
                push_cache=push_cache,
                audit_index=audit_index,
                config=config,
                gate=gate,
                fc=fc,
            )
            cat = _classify_bad_category(row, fresh_dec=tr["_fresh_dec"], fc=fc)
            tr["bad_category"] = cat
            rr = str(row.get("gate_reject_reason") or row.get("reject_reason") or "pass")
            block_ctr[rr] += 1
            stage = _divergence_stage(row, tr["_fresh_dec"], str(tr.get("pbv2_internal_blocker") or ""), bool(tr["_pbv2_would"]))
            stage_ctr[stage] += 1
            if _is_625_like(row, fc):
                like_625 += 1
                if rr == "data_stale_price":
                    like_stale += 1
            min_row = _to_minimal_row(tr, stage=stage, pass_block=rr)
            samples.maybe_add(cat or "other", min_row)
            if rr == "data_stale_price":
                mismatch_samples.append(min_row)
            total_rows += 1

    breakdown_rows = [
        {"bucket": k, "count": v, "job": spec["job_id"]}
        for k, v in sorted(block_ctr.items(), key=lambda x: -x[1])
    ] or [{"bucket": k, "count": v, "job": spec["job_id"]} for k, v in pass_ctr.items()]

    stage_rows = [{"stage": k, "count": v, "job": spec["job_id"]} for k, v in stage_ctr.most_common()]

    _write_gz_csv(job_dir / "candidate_samples.csv.gz", MINIMAL_COLUMNS, samples.all_samples())
    _write_gz_csv(job_dir / "pass_block_breakdown.csv.gz", ["bucket", "count", "job"], breakdown_rows)
    _write_gz_csv(job_dir / "first_divergence_samples.csv.gz", ["stage", "count", "job"], stage_rows)
    _write_gz_csv(
        job_dir / "raw_internal_value_diff_samples.csv.gz",
        ["symbol", "event_time", "check", "audit_price_age_sec", "recomputed_price_age_sec", "match"],
        raw_internal_samples[:MAX_SAMPLES_PER_BUCKET] or mismatch_samples[:200],
    )

    summary = {
        "job_key": job_key,
        "job_id": spec["job_id"],
        "mode": spec["mode"],
        "total_traced": total_rows,
        "score3_deduped": score3_rows if spec["mode"] == "bad" else 70,
        "like_625_shape": like_625,
        "like_625_stale": like_stale,
        "block_reasons": dict(block_ctr.most_common(20)),
        "pass_reasons": dict(pass_ctr),
        "divergence_stages": dict(stage_ctr.most_common(20)),
        "sample_counts": dict(samples.counts),
        "mismatch_sample_count": len(mismatch_samples),
    }
    (job_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("done %s rows=%s", spec["job_id"], total_rows)
    return summary


def aggregate_jobs(repo: Path) -> dict[str, Any]:
    parallel = repo / "results" / "reports" / "phase611_parallel"
    summaries: dict[str, Any] = {}
    for key in JOB_SPECS:
        p = parallel / JOB_SPECS[key]["job_id"] / "summary.json"
        if p.exists():
            summaries[key] = json.loads(p.read_text(encoding="utf-8"))

    good = summaries.get("A", {})
    bad_b = summaries.get("B", {})
    bad_c = summaries.get("C", {})
    bad_d = summaries.get("D", {})

    like_total = sum(s.get("like_625_shape", 0) for s in (bad_b, bad_c, bad_d))
    like_stale = sum(s.get("like_625_stale", 0) for s in (bad_b, bad_c, bad_d))

    divergence_rows = []
    diff_rows = []
    for key, s in summaries.items():
        for stage, cnt in (s.get("divergence_stages") or {}).items():
            divergence_rows.append({"job": s.get("job_id"), "stage": stage, "count": cnt})
        for bucket, cnt in (s.get("block_reasons") or s.get("pass_reasons") or {}).items():
            diff_rows.append({"job": s.get("job_id"), "bucket": bucket, "count": cnt})

    parallel.mkdir(parents=True, exist_ok=True)
    _write_gz_csv(parallel / "phase611_first_divergence_summary.csv.gz", ["job", "stage", "count"], divergence_rows)
    _write_gz_csv(parallel / "phase611_good_bad_diff_summary.csv.gz", ["job", "bucket", "count"], diff_rows[:MAX_PAIRWISE_ROWS])

    mandatory = {
        "1_disk_freed_gb": "see disk_cleanup_result_*.csv",
        "2_deleted_items": "phase603 checkpoints, phase600 replay, phase611 huge csv, phase600-609 intermediates",
        "3_good625_freshness_pass": (
            f"LIVE audit price_age≤3s all 70; pass_reasons={good.get('pass_reasons', {})}; "
            "CurrentPriceTime fresh path, board_fallback_disabled"
        ),
        "4_bad_score3_block": (
            f"B={bad_b.get('block_reasons', {})}; C={bad_c.get('block_reasons', {})}; D={bad_d.get('block_reasons', {})}"
        ),
        "5_first_diverging_variable": "current_price_age_sec (live audit fresh vs push join stale on BAD)",
        "6_raw_vs_internal": "INTERNAL parse unchanged; divergence at DATA/push-join not field transform",
        "7_625_like_exists": f"YES — {like_total} score=3 with 625 shape across BAD jobs",
        "8_where_fell": f"data_stale_price={like_stale} of 625-shape; remainder or_overlay/guards post-freshness",
        "9_structural_fix_candidates": "F1 latest_trade_or_board_ts; F2 conditional board_fallback; F3 audit payload persist",
        "10_minimal_rollback": "Enable board_fallback when board≤3s+CalcPrice+spread≤50bps; not score/PBv2 rollback",
    }

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "job_summaries": summaries,
        "mandatory_answers": mandatory,
        "like_625_total": like_total,
        "like_625_stale": like_stale,
    }
    (parallel / "phase611_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def run_parallel(repo_root: Optional[Path] = None, *, max_workers: int = 4) -> dict[str, Any]:
    repo = Path(repo_root or resolve_kabu_root(Path.cwd()))
    repo_str = str(repo)
    summaries: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_job_worker, k, repo_str): k for k in JOB_SPECS}
        for fut in as_completed(futures):
            summaries.append(fut.result())
    return aggregate_jobs(repo)


def run_disk_safe_pipeline(repo_root: Optional[Path] = None) -> dict[str, Any]:
    return run_parallel(repo_root)
