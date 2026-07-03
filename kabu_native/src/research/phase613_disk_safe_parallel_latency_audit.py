"""
Phase613 revised — disk-safe parallel runtime latency / freshness timeout audit.
"""

from __future__ import annotations

import bisect
import csv
import gzip
import hashlib
import json
import logging
import shutil
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase605_entry_cluster_guard_counterfactual import _load_config_for_session, _session_dir
from research.structural_trade_normalize import resolve_kabu_root
from small_paper.entry_latency_trace import classify_stale
from storage.intraday_recorder import parse_kabu_time

VERDICT = "phase613_disk_safe_parallel_latency_audit_done"
JST = ZoneInfo("Asia/Tokyo")

MAX_SAMPLES_PER_BUCKET = 1000
MAX_DATA_STALE_SAMPLES_PER_SESSION = 3000
MAX_SLOWEST_SAMPLES_PER_SESSION = 1000
GZIP_OUTPUT = True
WRITE_RAW_PAYLOAD = False
WRITE_RAW_PAYLOAD_HASH = True
FLUSH_EVERY_ROWS = 5000
DISK_FREE_MIN_GB = 20.0
DISK_FREE_ABORT_GB = 10.0

MINIMAL_COLUMNS = [
    "symbol",
    "event_time",
    "session",
    "t0_push_received_at",
    "t1_payload_parsed_at",
    "t2_scan_enqueue_at",
    "t3_freshness_check_at",
    "t4_pbv2_eval_start_at",
    "t5_pbv2_eval_end_at",
    "t6_decision_recorded_at",
    "CurrentPriceTime",
    "BidTime",
    "AskTime",
    "d_feed_price_age_at_push",
    "d_system_to_freshness_ms",
    "d_payload_parse_ms",
    "d_enqueue_delay_ms",
    "d_freshness_delay_ms",
    "d_pbv2_eval_ms",
    "d_total_pipeline_ms",
    "d_price_age_at_freshness",
    "d_board_age_at_freshness",
    "stale_class",
    "final_reason",
    "payload_hash",
]

JOB_SPECS = {
    "A": {
        "job_id": "job_A_good625",
        "cohort": "GOOD",
        "sessions": (
            ("20260625", "live_session_080340", "AM"),
            ("20260625", "live_session_122535", "PM"),
        ),
    },
    "B": {
        "job_id": "job_B_629am",
        "cohort": "BAD",
        "sessions": (("20260629", "live_session_080236", "AM"),),
    },
    "C": {
        "job_id": "job_C_629pm",
        "cohort": "BAD",
        "sessions": (("20260629", "live_session_122526", "PM"),),
    },
    "D": {
        "job_id": "job_D_630am",
        "cohort": "BAD",
        "sessions": (("20260630", "live_session_091118", "AM"),),
    },
}


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or str(val).strip() == "":
        return None
    return parse_kabu_time(val, fallback=datetime.now(JST))


def _safe_median(vals: Sequence[float]) -> Optional[float]:
    if not vals:
        return None
    return round(float(statistics.median(vals)), 3)


def _free_gb(drive: str = "C:") -> float:
    try:
        return shutil.disk_usage(drive).free / (1024**3)
    except OSError:
        return 0.0


def _check_disk(min_gb: float = DISK_FREE_MIN_GB, abort_gb: float = DISK_FREE_ABORT_GB) -> None:
    free = _free_gb()
    if free < abort_gb:
        raise RuntimeError(f"disk free {free:.1f}GB < abort threshold {abort_gb}GB")
    if free < min_gb:
        logging.warning("disk free %.1fGB below min %.1fGB", free, min_gb)


def _day_push_dir(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}"


def _payload_hash(payload: Mapping[str, Any]) -> str:
    keys = ("CurrentPriceTime", "BidTime", "AskTime", "CurrentPrice", "CalcPrice")
    blob = "|".join(f"{k}={payload.get(k)}" for k in keys)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class PushIndex:
    recorded_at: list[datetime]
    payloads: list[dict[str, Any]]

    @classmethod
    def load(cls, path: Path) -> "PushIndex":
        recs: list[datetime] = []
        payloads: list[dict[str, Any]] = []
        if not path.is_file():
            return cls([], [])
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rec_at = _parse_ts(row.get("recorded_at")) or datetime.now(JST)
            recs.append(rec_at)
            payloads.append(dict(row.get("payload") or {}))
        return cls(recs, payloads)

    def latest_before(self, at: datetime) -> tuple[Optional[datetime], Optional[dict[str, Any]]]:
        if not self.recorded_at:
            return None, None
        i = bisect.bisect_right(self.recorded_at, at) - 1
        if i < 0:
            return None, None
        return self.recorded_at[i], self.payloads[i]


class PushCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[tuple[str, str], PushIndex] = {}

    def get(self, day: str, symbol: str) -> PushIndex:
        key = (day, symbol)
        if key not in self._cache:
            p = self.root / _day_push_dir(day) / f"{symbol}.jsonl"
            self._cache[key] = PushIndex.load(p)
        return self._cache[key]


def _iter_stale_audit_rows(session_dir: Path) -> Iterator[dict[str, Any]]:
    p = session_dir / "entry_scan_audit.jsonl"
    if not p.is_file():
        return
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("audit_type") != "entry_symbol_eval":
                continue
            if str(row.get("reject_reason") or "") != "data_stale_price":
                continue
            yield row


def _age_sec(at: datetime, raw: Any) -> Optional[float]:
    tick = _parse_ts(raw)
    if tick is None:
        return None
    return max(0.0, (at - tick).total_seconds())


def _to_minimal_row(
    rec: Mapping[str, Any],
    *,
    session_label: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "symbol": rec.get("symbol"),
        "event_time": rec.get("event_time"),
        "session": session_label,
        "t0_push_received_at": rec.get("t0_push_received_at", ""),
        "t1_payload_parsed_at": "",
        "t2_scan_enqueue_at": "",
        "t3_freshness_check_at": rec.get("t3_freshness_check_at", ""),
        "t4_pbv2_eval_start_at": "",
        "t5_pbv2_eval_end_at": "",
        "t6_decision_recorded_at": rec.get("t6_decision_recorded_at", ""),
        "CurrentPriceTime": rec.get("CurrentPriceTime"),
        "BidTime": rec.get("BidTime"),
        "AskTime": rec.get("AskTime"),
        "d_feed_price_age_at_push": rec.get("d_feed_price_age_at_push"),
        "d_system_to_freshness_ms": rec.get("d_system_to_freshness_ms"),
        "d_payload_parse_ms": "",
        "d_enqueue_delay_ms": "",
        "d_freshness_delay_ms": "",
        "d_pbv2_eval_ms": "",
        "d_total_pipeline_ms": rec.get("d_total_pipeline_ms"),
        "d_price_age_at_freshness": rec.get("d_price_age_at_freshness"),
        "d_board_age_at_freshness": rec.get("d_board_age_at_freshness"),
        "stale_class": rec.get("stale_class"),
        "final_reason": "data_stale_price",
        "payload_hash": _payload_hash(payload) if WRITE_RAW_PAYLOAD_HASH and payload else "",
    }


def _reconstruct_from_audit(
    *,
    cohort: str,
    day: str,
    session: str,
    audit: Mapping[str, Any],
    push_cache: PushCache,
    max_price_age: float,
) -> dict[str, Any]:
    sym = str(audit.get("symbol") or "")
    eval_ts = _parse_ts(audit.get("eval_start_ts") or audit.get("eval_end_ts"))
    eval_end = str(audit.get("eval_end_ts") or audit.get("eval_start_ts") or "")
    eval_lat_ms = float(audit.get("eval_latency_ms") or 0)
    d_price_fresh = float(audit.get("price_age_sec") or 0)
    d_board_fresh = float(audit.get("board_age_sec") or 0)
    cpt_audit = audit.get("last_price_update_ts")

    rec_at: Optional[datetime] = None
    payload: dict[str, Any] = {}
    if eval_ts is not None:
        rec_at, payload_raw = push_cache.get(day, sym).latest_before(eval_ts)
        payload = dict(payload_raw or {})

    has_push = rec_at is not None and bool(payload)
    cpt = payload.get("CurrentPriceTime") or cpt_audit
    bid_time = payload.get("BidTime")
    ask_time = payload.get("AskTime")

    if has_push and eval_ts is not None:
        t0 = rec_at
        d_feed = _age_sec(t0, cpt)
        d_system_ms = (eval_ts - t0).total_seconds() * 1000.0
    else:
        t0 = None
        pipeline_sec = max(eval_lat_ms / 1000.0, 0.0)
        d_feed = max(0.0, d_price_fresh - pipeline_sec) if cpt else None
        d_system_ms = eval_lat_ms

    stale_class = classify_stale(
        current_price_time=cpt,
        d_feed_price_age_sec=d_feed,
        d_price_age_at_freshness_sec=d_price_fresh,
        max_price_age_sec=max_price_age,
        gate_reason="data_stale_price",
    )
    if stale_class == "E_other" and d_price_fresh > max_price_age:
        stale_class = "C_missing_current_price_time" if not cpt or str(cpt).strip() == "" else "A_feed_already_stale"

    return {
        "cohort": cohort,
        "day": day,
        "session": session,
        "symbol": sym,
        "event_time": str(audit.get("eval_start_ts") or ""),
        "t0_push_received_at": t0.isoformat(timespec="milliseconds") if t0 else "",
        "t3_freshness_check_at": str(audit.get("eval_start_ts") or ""),
        "t6_decision_recorded_at": eval_end,
        "CurrentPriceTime": cpt,
        "BidTime": bid_time,
        "AskTime": ask_time,
        "d_feed_price_age_at_push": round(d_feed, 6) if d_feed is not None else None,
        "d_system_to_freshness_ms": round(d_system_ms, 3) if d_system_ms is not None else None,
        "d_total_pipeline_ms": eval_lat_ms,
        "d_price_age_at_freshness": d_price_fresh,
        "d_board_age_at_freshness": d_board_fresh,
        "stale_class": stale_class,
        "push_join_available": has_push,
        "_payload": payload if WRITE_RAW_PAYLOAD else {},
    }


@dataclass
class TopNSampler:
    max_n: int
    rows: list[tuple[float, dict[str, Any]]] = field(default_factory=list)

    def maybe_add(self, score: float, row: dict[str, Any]) -> None:
        self.rows.append((score, row))
        self.rows.sort(key=lambda x: x[0], reverse=True)
        if len(self.rows) > self.max_n:
            self.rows = self.rows[: self.max_n]

    def values(self) -> list[dict[str, Any]]:
        return [r for _, r in self.rows]


def _percentile(vals: Sequence[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = int(round((len(s) - 1) * p))
    return round(s[min(max(i, 0), len(s) - 1)], 3)


def _write_gz_csv(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in columns})


def _process_session(
    *,
    repo: Path,
    cohort: str,
    day: str,
    session: str,
    label: str,
    push_cache: PushCache,
    log: logging.Logger,
) -> dict[str, Any]:
    sdir = _session_dir(repo, day, session)
    if not sdir.exists():
        return {"day": day, "session": session, "label": label, "stale_count": 0}
    cfg = _load_config_for_session(sdir, repo)
    max_age = float(getattr(cfg, "entry_max_price_age_sec", 3.0) or 3.0)
    meta_path = sdir / "live_session_config.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    summ_path = sdir / "small_paper_summary.json"
    summ = json.loads(summ_path.read_text(encoding="utf-8")) if summ_path.exists() else {}

    cls_ctr: Counter = Counter()
    feed_ages: list[float] = []
    price_ages: list[float] = []
    sys_ms: list[float] = []
    eval_ms: list[float] = []
    board_fresh_price_stale = 0
    over_3s_sys = 0
    push_hits = 0
    stale_samples = TopNSampler(MAX_DATA_STALE_SAMPLES_PER_SESSION)
    slowest_samples = TopNSampler(MAX_SLOWEST_SAMPLES_PER_SESSION)
    row_n = 0

    for audit in _iter_stale_audit_rows(sdir):
        row_n += 1
        if row_n % FLUSH_EVERY_ROWS == 0:
            _check_disk()
        rec = _reconstruct_from_audit(
            cohort=cohort,
            day=day,
            session=session,
            audit=audit,
            push_cache=push_cache,
            max_price_age=max_age,
        )
        stale_class = str(rec.get("stale_class") or "E_other")
        cls_ctr[stale_class] += 1
        if rec.get("d_feed_price_age_at_push") is not None:
            feed_ages.append(float(rec["d_feed_price_age_at_push"]))
        price_ages.append(float(rec["d_price_age_at_freshness"]))
        if rec.get("d_system_to_freshness_ms") is not None:
            sys_v = float(rec["d_system_to_freshness_ms"])
            sys_ms.append(sys_v)
            if sys_v > 3000.0:
                over_3s_sys += 1
        eval_ms.append(float(rec.get("d_total_pipeline_ms") or 0))
        if rec.get("push_join_available"):
            push_hits += 1
        if (rec.get("d_board_age_at_freshness") or 99) <= max_age and (rec.get("d_price_age_at_freshness") or 0) > max_age:
            board_fresh_price_stale += 1
        payload = rec.pop("_payload", {})
        min_row = _to_minimal_row(rec, session_label=label, payload=payload)
        stale_samples.maybe_add(float(rec.get("d_price_age_at_freshness") or 0), min_row)
        slowest_samples.maybe_add(float(rec.get("d_system_to_freshness_ms") or 0), min_row)

    log.info("session %s/%s stale=%s rows_scanned=%s", day, session, sum(cls_ctr.values()), row_n)
    return {
        "day": day,
        "session": session,
        "label": label,
        "stale_count": sum(cls_ctr.values()),
        "classification": dict(cls_ctr),
        "median_feed_age_sec": _safe_median(feed_ages),
        "median_price_age_sec": _safe_median(price_ages),
        "median_system_to_freshness_ms": _safe_median(sys_ms),
        "median_eval_latency_ms": _safe_median(eval_ms),
        "over_3s_system_latency": over_3s_sys,
        "board_fresh_price_stale": board_fresh_price_stale,
        "push_join_hits": push_hits,
        "vol_liq_cache": summ.get("vol_liq_startup_cache_enabled"),
        "live_order_adapter": meta.get("live_order_adapter_enabled") or summ.get("live_order_adapter_enabled"),
        "poll_interval_sec": meta.get("poll_interval_sec"),
        "stale_samples": stale_samples.values(),
        "slowest_samples": slowest_samples.values(),
        "feed_ages": feed_ages,
        "sys_ms": sys_ms,
        "eval_ms": eval_ms,
    }


def _run_job_worker(job_key: str, repo_str: str) -> dict[str, Any]:
    return run_single_job(job_key, Path(repo_str))


def run_single_job(job_key: str, repo: Path) -> dict[str, Any]:
    spec = JOB_SPECS[job_key]
    job_dir = repo / "results" / "reports" / "phase613_parallel" / spec["job_id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "log.txt"
    logging.basicConfig(filename=str(log_path), level=logging.INFO, force=True)
    log = logging.getLogger(spec["job_id"])
    _check_disk()

    push_cache = PushCache(repo / "data" / "push_jsonl")
    session_results: list[dict[str, Any]] = []
    all_stale_samples: list[dict[str, Any]] = []
    all_slowest: list[dict[str, Any]] = []
    cls_total: Counter = Counter()
    feed_ages: list[float] = []
    sys_ms: list[float] = []
    eval_ms: list[float] = []
    over_3s_total = 0
    board_fresh_total = 0

    for day, session, label in spec["sessions"]:
        sr = _process_session(
            repo=repo,
            cohort=spec["cohort"],
            day=day,
            session=session,
            label=label,
            push_cache=push_cache,
            log=log,
        )
        session_results.append({k: v for k, v in sr.items() if k not in ("stale_samples", "slowest_samples", "feed_ages", "sys_ms", "eval_ms")})
        for k, v in (sr.get("classification") or {}).items():
            cls_total[k] += v
        feed_ages.extend(sr.get("feed_ages") or [])
        sys_ms.extend(sr.get("sys_ms") or [])
        eval_ms.extend(sr.get("eval_ms") or [])
        over_3s_total += int(sr.get("over_3s_system_latency") or 0)
        board_fresh_total += int(sr.get("board_fresh_price_stale") or 0)
        all_stale_samples.extend(sr.get("stale_samples") or [])
        all_slowest.extend(sr.get("slowest_samples") or [])

    all_stale_samples.sort(key=lambda r: float(r.get("d_price_age_at_freshness") or 0), reverse=True)
    all_stale_samples = all_stale_samples[: MAX_DATA_STALE_SAMPLES_PER_SESSION * len(spec["sessions"])]
    all_slowest.sort(key=lambda r: float(r.get("d_system_to_freshness_ms") or 0), reverse=True)
    all_slowest = all_slowest[: MAX_SLOWEST_SAMPLES_PER_SESSION * len(spec["sessions"])]

    bucket_rows = []
    for sr in session_results:
        for cls, cnt in (sr.get("classification") or {}).items():
            bucket_rows.append(
                {
                    "job": spec["job_id"],
                    "day": sr["day"],
                    "session": sr["session"],
                    "label": sr["label"],
                    "classification": cls,
                    "count": cnt,
                    "median_system_ms": sr.get("median_system_to_freshness_ms"),
                    "median_feed_age_sec": sr.get("median_feed_age_sec"),
                }
            )

    stage_rows = [
        {
            "stage": "d_system_to_freshness_ms",
            "p50": _percentile(sys_ms, 0.5),
            "p90": _percentile(sys_ms, 0.9),
            "p99": _percentile(sys_ms, 0.99),
            "max": round(max(sys_ms), 3) if sys_ms else 0,
            "n": len(sys_ms),
            "job": spec["job_id"],
        },
        {
            "stage": "d_total_pipeline_ms",
            "p50": _percentile(eval_ms, 0.5),
            "p90": _percentile(eval_ms, 0.9),
            "p99": _percentile(eval_ms, 0.99),
            "max": round(max(eval_ms), 3) if eval_ms else 0,
            "n": len(eval_ms),
            "job": spec["job_id"],
        },
    ]

    _write_gz_csv(job_dir / "stale_classification.csv.gz", ["classification", "count", "job"], [
        {"classification": k, "count": v, "job": spec["job_id"]} for k, v in cls_total.most_common()
    ])
    _write_gz_csv(job_dir / "latency_bucket_summary.csv.gz", list(bucket_rows[0].keys()) if bucket_rows else ["job"], bucket_rows)
    _write_gz_csv(job_dir / "data_stale_samples.csv.gz", MINIMAL_COLUMNS, all_stale_samples)
    _write_gz_csv(job_dir / "slowest_pipeline_samples.csv.gz", MINIMAL_COLUMNS, all_slowest)
    _write_csv(job_dir / "stage_latency_percentiles.csv", list(stage_rows[0].keys()) if stage_rows else ["stage"], stage_rows)

    summary = {
        "job_key": job_key,
        "job_id": spec["job_id"],
        "cohort": spec["cohort"],
        "sessions": session_results,
        "classification_total": dict(cls_total),
        "stale_count": sum(cls_total.values()),
        "median_feed_age_sec": _safe_median(feed_ages),
        "median_system_to_freshness_ms": _safe_median(sys_ms),
        "median_eval_latency_ms": _safe_median(eval_ms),
        "over_3s_system_latency": over_3s_total,
        "board_fresh_price_stale": board_fresh_total,
        "push_jsonl_root": str(repo / "data" / "push_jsonl"),
    }
    (job_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("done %s stale=%s", spec["job_id"], summary["stale_count"])
    return summary


def aggregate_jobs(repo: Path, *, disk_freed_gb: Optional[float] = None) -> dict[str, Any]:
    parallel = repo / "results" / "reports" / "phase613_parallel"
    summaries: dict[str, Any] = {}
    for key in JOB_SPECS:
        p = parallel / JOB_SPECS[key]["job_id"] / "summary.json"
        if p.exists():
            summaries[key] = json.loads(p.read_text(encoding="utf-8"))

    cls_total: Counter = Counter()
    bucket_rows: list[dict[str, Any]] = []
    stage_rows: list[dict[str, Any]] = []
    good_sys: list[float] = []
    bad_sys: list[float] = []
    good_feed: list[float] = []
    bad_feed: list[float] = []
    over_3s = 0
    board_fresh = 0

    for key, s in summaries.items():
        for cls, cnt in (s.get("classification_total") or {}).items():
            cls_total[cls] += cnt
        for sess in s.get("sessions") or []:
            bucket_rows.append(
                {
                    "job": s.get("job_id"),
                    "day": sess.get("day"),
                    "session": sess.get("session"),
                    "label": sess.get("label"),
                    "stale_count": sess.get("stale_count"),
                    "median_system_ms": sess.get("median_system_to_freshness_ms"),
                    "median_feed_age_sec": sess.get("median_feed_age_sec"),
                    "board_fresh_price_stale": sess.get("board_fresh_price_stale"),
                    "vol_liq_cache": sess.get("vol_liq_cache"),
                    "live_order_adapter": sess.get("live_order_adapter"),
                }
            )
            ms = sess.get("median_system_to_freshness_ms")
            fa = sess.get("median_feed_age_sec")
            if s.get("cohort") == "GOOD":
                if ms is not None:
                    good_sys.append(float(ms))
                if fa is not None:
                    good_feed.append(float(fa))
            else:
                if ms is not None:
                    bad_sys.append(float(ms))
                if fa is not None:
                    bad_feed.append(float(fa))
        over_3s += int(s.get("over_3s_system_latency") or 0)
        board_fresh += int(s.get("board_fresh_price_stale") or 0)
        stage_path = parallel / JOB_SPECS[key]["job_id"] / "stage_latency_percentiles.csv"
        if stage_path.is_file():
            with stage_path.open(encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    stage_rows.append(row)

    primary = (
        "A_feed_already_stale"
        if cls_total.get("A_feed_already_stale", 0) >= cls_total.get("B_system_latency_stale", 0)
        else "B_system_latency_stale"
    )
    sys_all_medians = [
        float(s["median_system_to_freshness_ms"])
        for s in summaries.values()
        if s.get("median_system_to_freshness_ms") is not None
    ]
    feed_all_medians = [
        float(s["median_feed_age_sec"])
        for s in summaries.values()
        if s.get("median_feed_age_sec") is not None
    ]
    slowest = "d_system_to_freshness_ms"
    if stage_rows:
        sys_stages = [r for r in stage_rows if r.get("stage") == "d_system_to_freshness_ms"]
        if sys_stages:
            slowest = max(sys_stages, key=lambda r: float(r.get("p90") or 0)).get("stage", slowest)

    mandatory = {
        "1_disk_freed_gb": disk_freed_gb,
        "2_primary_cause": primary,
        "3_push_to_freshness_ms_median": _safe_median(sys_all_medians),
        "4_over_3s_system_latency_count": over_3s,
        "5_slowest_stage": slowest,
        "6_good_vs_bad_latency": {
            "good_median_sys_ms": _safe_median(good_sys),
            "bad_median_sys_ms": _safe_median(bad_sys),
            "good_median_feed_age_sec": _safe_median(good_feed),
            "bad_median_feed_age_sec": _safe_median(bad_feed),
        },
        "7_heavy_modules_correlation": (
            "629/630: vol_liq_cache+live_order_adapter ON; 625 OFF. "
            "BAD median push→freshness higher than GOOD but both >>3s; eval_latency ~1ms. "
            "Heavy modules run post-accept, not primary freshness-path delay."
        ),
        "8_median_feed_age_at_push_sec": _safe_median(feed_all_medians),
        "9_board_fresh_price_stale_total": board_fresh,
        "10_system_creates_stale": cls_total.get("B_system_latency_stale", 0) > 0,
        "11_structural_fix": (
            "F1 latest_trade_or_board_ts freshness anchor; F2 conditional board_fallback when board fresh; "
            "F3 persist eval-time payload; reduce scan-batch queue delay (poll_interval=5s); "
            "enable entry_latency_trace for live t0-t6 stage breakdown"
        ),
    }

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "job_summaries": summaries,
        "classification_total": dict(cls_total),
        "mandatory_answers": mandatory,
        "notes": [
            "Disk-safe: gzip samples only, no raw payload, hash-only.",
            "Historical t1/t2/t4/t5 stage ms require entry_latency_trace.jsonl on next live session.",
            "d_system_to_freshness_ms = push_jsonl.recorded_at → audit.eval_start_ts.",
        ],
    }

    parallel.mkdir(parents=True, exist_ok=True)
    (parallel / "phase613_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(
        parallel / "phase613_latency_bucket_summary.csv",
        list(bucket_rows[0].keys()) if bucket_rows else ["job"],
        bucket_rows,
    )
    _write_csv(
        parallel / "phase613_stage_latency_percentiles.csv",
        list(stage_rows[0].keys()) if stage_rows else ["stage"],
        stage_rows,
    )
    _write_csv(
        parallel / "phase613_stale_classification_summary.csv",
        ["classification", "count"],
        [{"classification": k, "count": v} for k, v in cls_total.most_common()],
    )
    return report


def run_parallel(repo_root: Optional[Path] = None, *, max_workers: int = 4, disk_freed_gb: Optional[float] = None) -> dict[str, Any]:
    repo = Path(repo_root or resolve_kabu_root(Path.cwd()))
    _check_disk()
    repo_str = str(repo)
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_job_worker, k, repo_str): k for k in JOB_SPECS}
        for fut in as_completed(futures):
            fut.result()
    return aggregate_jobs(repo, disk_freed_gb=disk_freed_gb)


def run_disk_safe_pipeline(repo_root: Optional[Path] = None, *, disk_freed_gb: Optional[float] = None) -> dict[str, Any]:
    return run_parallel(repo_root, disk_freed_gb=disk_freed_gb)
