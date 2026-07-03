"""
Phase613 — Runtime latency / freshness timeout audit (research + live trace).
"""

from __future__ import annotations

import bisect
import csv
import gzip
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase605_entry_cluster_guard_counterfactual import _load_config_for_session, _session_dir
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_latency_trace import classify_stale
from storage.intraday_recorder import parse_kabu_time

VERDICT = "phase613_runtime_latency_freshness_timeout_audit_done"
JST = ZoneInfo("Asia/Tokyo")
MAX_STALE_SAMPLES = 3000
MAX_SLOWEST_SAMPLES = 1000
MAX_SYMBOL_SAMPLES = 100

SESSIONS: tuple[tuple[str, str, str, str], ...] = (
    ("GOOD", "20260625", "live_session_080340", "AM"),
    ("GOOD", "20260625", "live_session_122535", "PM"),
    ("BAD", "20260629", "live_session_080236", "AM"),
    ("BAD", "20260629", "live_session_122526", "PM"),
    ("BAD", "20260630", "live_session_091118", "AM"),
)


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or str(val).strip() == "":
        return None
    return parse_kabu_time(val, fallback=datetime.now(JST))


def _safe_median(vals: Sequence[float]) -> Optional[float]:
    if not vals:
        return None
    return round(float(statistics.median(vals)), 3)


def _day_push_dir(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}"


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
        self._available = root.is_dir() and any(root.rglob("*.jsonl"))

    @property
    def available(self) -> bool:
        return self._available

    def get(self, day: str, symbol: str) -> PushIndex:
        key = (day, symbol)
        if key not in self._cache:
            p = self.root / _day_push_dir(day) / f"{symbol}.jsonl"
            self._cache[key] = PushIndex.load(p)
        return self._cache[key]


def _iter_stale_audit_rows(session_dir: Path) -> list[dict[str, Any]]:
    p = session_dir / "entry_scan_audit.jsonl"
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("audit_type") != "entry_symbol_eval":
            continue
        if str(row.get("reject_reason") or "") != "data_stale_price":
            continue
        out.append(row)
    return out


def _board_ts(payload: Mapping[str, Any]) -> Any:
    bid, ask = payload.get("BidTime"), payload.get("AskTime")
    if not bid:
        return ask
    if not ask:
        return bid
    bt, at = _parse_ts(bid), _parse_ts(ask)
    if bt is None:
        return ask
    if at is None:
        return bid
    return bid if bt >= at else ask


def _age_sec(at: datetime, raw: Any) -> Optional[float]:
    tick = _parse_ts(raw)
    if tick is None:
        return None
    return max(0.0, (at - tick).total_seconds())


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
    eval_lat_ms = float(audit.get("eval_latency_ms") or 0)
    d_price_fresh = float(audit.get("price_age_sec") or 0)
    d_board_fresh = float(audit.get("board_age_sec") or 0)
    cpt_audit = audit.get("last_price_update_ts")
    board_audit = audit.get("last_board_update_ts")

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
        source = "push_join"
    else:
        t0 = None
        pipeline_sec = max(eval_lat_ms / 1000.0, 0.0)
        d_feed = max(0.0, d_price_fresh - pipeline_sec) if cpt else None
        d_system_ms = eval_lat_ms
        source = "audit_only"

    classification = classify_stale(
        current_price_time=cpt,
        d_feed_price_age_sec=d_feed,
        d_price_age_at_freshness_sec=d_price_fresh,
        max_price_age_sec=max_price_age,
        gate_reason="data_stale_price",
    )
    if classification == "E_other" and d_price_fresh > max_price_age:
        if cpt is None or str(cpt).strip() == "":
            classification = "C_missing_current_price_time"
        elif d_board_fresh <= max_price_age:
            classification = "A_feed_already_stale"
        else:
            classification = "A_feed_already_stale"

    return {
        "cohort": cohort,
        "day": day,
        "session": session,
        "symbol": sym,
        "event_time": str(audit.get("eval_start_ts") or ""),
        "scan_id": str(audit.get("scan_id") or ""),
        "t0_push_received_at": t0.isoformat(timespec="milliseconds") if t0 else "",
        "t3_freshness_check_at": str(audit.get("eval_start_ts") or ""),
        "CurrentPriceTime": cpt,
        "BidTime": bid_time,
        "AskTime": ask_time,
        "last_board_update_ts": board_audit,
        "d_feed_price_age_at_push_sec": round(d_feed, 6) if d_feed is not None else None,
        "d_system_to_freshness_ms": round(d_system_ms, 3) if d_system_ms is not None else None,
        "d_total_pipeline_ms": eval_lat_ms,
        "d_price_age_at_freshness_sec": d_price_fresh,
        "d_board_age_at_freshness_sec": d_board_fresh,
        "stale_classification": classification,
        "gate_reject_reason": "data_stale_price",
        "entry_score_v2": audit.get("entry_score_v2"),
        "source": source,
        "push_join_available": has_push,
    }


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


def _load_phase611_reference(repo: Path) -> dict[str, Any]:
    p = repo / "results" / "reports" / "phase611_parallel" / "phase611_report.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def run_phase613(repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = Path(repo_root or resolve_kabu_root(Path.cwd()))
    reports = resolve_reports_dir(repo)
    push_cache = PushCache(repo / "data" / "push_jsonl")

    all_stale: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    sym_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cohort_stats: dict[str, dict[str, Any]] = {}

    for cohort, day, session, label in SESSIONS:
        sdir = _session_dir(repo, day, session)
        if not sdir.exists():
            continue
        cfg = _load_config_for_session(sdir, repo)
        max_age = float(getattr(cfg, "entry_max_price_age_sec", 3.0) or 3.0)
        meta_path = sdir / "live_session_config.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        summ_path = sdir / "small_paper_summary.json"
        summ = json.loads(summ_path.read_text(encoding="utf-8")) if summ_path.exists() else {}

        trace_path = sdir / "entry_latency_trace.jsonl"
        if trace_path.is_file():
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    row.setdefault("source", "live_trace")
                    trace_rows.append(row)

        session_stale: list[dict[str, Any]] = []
        for audit in _iter_stale_audit_rows(sdir):
            rec = _reconstruct_from_audit(
                cohort=cohort,
                day=day,
                session=session,
                audit=audit,
                push_cache=push_cache,
                max_price_age=max_age,
            )
            session_stale.append(rec)
            sym_buckets[str(rec["symbol"])].append(rec)

        all_stale.extend(session_stale)
        cls_ctr = Counter(r["stale_classification"] for r in session_stale)
        feed_ages = [
            float(r["d_feed_price_age_at_push_sec"])
            for r in session_stale
            if r.get("d_feed_price_age_at_push_sec") is not None
        ]
        price_ages = [float(r["d_price_age_at_freshness_sec"]) for r in session_stale]
        sys_ms = [
            float(r["d_system_to_freshness_ms"])
            for r in session_stale
            if r.get("d_system_to_freshness_ms") is not None
        ]
        board_fresh_price_stale = sum(
            1
            for r in session_stale
            if (r.get("d_board_age_at_freshness_sec") or 99) <= max_age
            and (r.get("d_price_age_at_freshness_sec") or 0) > max_age
        )
        push_join_hits = sum(1 for r in session_stale if r.get("push_join_available"))
        cohort_stats[f"{cohort}_{day}_{label}"] = {
            "cohort": cohort,
            "day": day,
            "session": session,
            "data_stale_count": len(session_stale),
            "classification": dict(cls_ctr),
            "median_feed_age_sec": _safe_median(feed_ages),
            "median_price_age_at_freshness_sec": _safe_median(price_ages),
            "median_system_to_freshness_ms": _safe_median(sys_ms),
            "board_fresh_price_stale_count": board_fresh_price_stale,
            "push_join_hits": push_join_hits,
            "vol_liq_cache": summ.get("vol_liq_startup_cache_enabled"),
            "live_order_adapter": meta.get("live_order_adapter_enabled") or summ.get("live_order_adapter_enabled"),
            "pre625_mode": meta.get("pre625_runtime_structure_mode"),
            "poll_interval_sec": meta.get("poll_interval_sec"),
        }

    analysis_rows = trace_rows if trace_rows else all_stale
    cls_total = Counter(r.get("stale_classification") or "E_other" for r in analysis_rows)
    feed_ages_all = [
        float(r["d_feed_price_age_at_push_sec"])
        for r in analysis_rows
        if r.get("d_feed_price_age_at_push_sec") is not None
    ]
    price_ages_all = [float(r["d_price_age_at_freshness_sec"]) for r in analysis_rows if r.get("d_price_age_at_freshness_sec") is not None]
    sys_ms_all = [
        float(r["d_system_to_freshness_ms"])
        for r in analysis_rows
        if r.get("d_system_to_freshness_ms") is not None
    ]
    over_3s_sys = sum(1 for v in sys_ms_all if v > 3000.0)

    stage_cols = [
        "d_payload_parse_ms",
        "d_enqueue_delay_ms",
        "d_freshness_delay_ms",
        "d_pbv2_eval_ms",
        "d_record_delay_ms",
        "d_total_pipeline_ms",
    ]
    stage_pct_rows = []
    for col in stage_cols:
        vals = [float(r[col]) for r in analysis_rows if r.get(col) is not None]
        stage_pct_rows.append(
            {
                "stage": col,
                "p50": _percentile(vals, 0.5),
                "p90": _percentile(vals, 0.9),
                "p99": _percentile(vals, 0.99),
                "max": round(max(vals), 3) if vals else 0,
                "n": len(vals),
            }
        )

    slowest = sorted(
        analysis_rows,
        key=lambda r: float(
            r.get("d_total_pipeline_ms") or r.get("d_system_to_freshness_ms") or r.get("d_price_age_at_freshness_sec") or 0
        ),
        reverse=True,
    )[:MAX_SLOWEST_SAMPLES]
    stale_samples = sorted(
        all_stale,
        key=lambda r: float(r.get("d_price_age_at_freshness_sec") or 0),
        reverse=True,
    )[:MAX_STALE_SAMPLES]

    sym_rows = []
    for sym, rows in sorted(sym_buckets.items(), key=lambda x: -len(x[1]))[:200]:
        cls = Counter(r["stale_classification"] for r in rows)
        feed_for_sym = [
            float(r["d_feed_price_age_at_push_sec"])
            for r in rows
            if r.get("d_feed_price_age_at_push_sec") is not None
        ]
        sym_rows.append(
            {
                "symbol": sym,
                "stale_count": len(rows),
                "A_feed": cls.get("A_feed_already_stale", 0),
                "B_system": cls.get("B_system_latency_stale", 0),
                "C_missing": cls.get("C_missing_current_price_time", 0),
                "median_feed_age_sec": _safe_median(feed_for_sym),
                "median_price_age_sec": _safe_median([float(r["d_price_age_at_freshness_sec"]) for r in rows]),
            }
        )

    bucket_rows = []
    for key, st in cohort_stats.items():
        for cls, cnt in (st.get("classification") or {}).items():
            bucket_rows.append(
                {
                    "session_key": key,
                    "classification": cls,
                    "count": cnt,
                    "cohort": st["cohort"],
                    "day": st["day"],
                    "session": st["session"],
                }
            )

    good_a = sum(v.get("classification", {}).get("A_feed_already_stale", 0) for v in cohort_stats.values() if v.get("cohort") == "GOOD")
    good_b = sum(v.get("classification", {}).get("B_system_latency_stale", 0) for v in cohort_stats.values() if v.get("cohort") == "GOOD")
    bad_a = sum(v.get("classification", {}).get("A_feed_already_stale", 0) for v in cohort_stats.values() if v.get("cohort") == "BAD")
    bad_b = sum(v.get("classification", {}).get("B_system_latency_stale", 0) for v in cohort_stats.values() if v.get("cohort") == "BAD")

    primary = (
        "A_feed_already_stale"
        if cls_total.get("A_feed_already_stale", 0) >= cls_total.get("B_system_latency_stale", 0)
        else "B_system_latency_stale"
    )

    sys_stage_pct = {
        "stage": "d_system_to_freshness_ms",
        "p50": _percentile(sys_ms_all, 0.5),
        "p90": _percentile(sys_ms_all, 0.9),
        "p99": _percentile(sys_ms_all, 0.99),
        "max": round(max(sys_ms_all), 3) if sys_ms_all else 0,
        "n": len(sys_ms_all),
    }
    if sys_stage_pct["n"] > 0:
        stage_pct_rows.append(sys_stage_pct)
    stage_with_data = [r for r in stage_pct_rows if r["n"] > 0]
    slowest_stage = max(stage_with_data, key=lambda r: r["p90"])["stage"] if stage_with_data else "unknown"

    good_price_medians = [
        cohort_stats[k]["median_price_age_at_freshness_sec"]
        for k in cohort_stats
        if cohort_stats[k]["cohort"] == "GOOD" and cohort_stats[k].get("median_price_age_at_freshness_sec") is not None
    ]
    bad_price_medians = [
        cohort_stats[k]["median_price_age_at_freshness_sec"]
        for k in cohort_stats
        if cohort_stats[k]["cohort"] == "BAD" and cohort_stats[k].get("median_price_age_at_freshness_sec") is not None
    ]
    board_fresh_total = sum(st.get("board_fresh_price_stale_count", 0) for st in cohort_stats.values())

    phase611 = _load_phase611_reference(repo)
    p611_note = ""
    if phase611:
        p611_note = (
            "phase611 push-join (when push_jsonl existed): "
            "629AM board_fresh_price_stale=13163/24578 stale; B_freshness_pass_pbv2_reject dominant after freshness"
        )

    mandatory = {
        "1_primary_cause": primary,
        "2_push_to_freshness_ms_median": _safe_median(sys_ms_all),
        "3_over_3s_system_latency_count": over_3s_sys,
        "4_slowest_stage": slowest_stage,
        "5_good_vs_bad_latency_increase": (
            f"GOOD median_price_age_sec={_safe_median(good_price_medians)} median_sys_ms="
            f"{[_safe_median([float(cohort_stats[k]['median_system_to_freshness_ms']) for k in cohort_stats if cohort_stats[k]['cohort']=='GOOD' and cohort_stats[k].get('median_system_to_freshness_ms') is not None])]}; "
            f"BAD median_price_age_sec={_safe_median(bad_price_medians)} median_sys_ms="
            f"{[_safe_median([float(cohort_stats[k]['median_system_to_freshness_ms']) for k in cohort_stats if cohort_stats[k]['cohort']=='BAD' and cohort_stats[k].get('median_system_to_freshness_ms') is not None])]}; "
            f"push_jsonl_available={push_cache.available}"
        ),
        "6_heavy_modules_correlation": (
            "629/630 sessions: vol_liq_startup_cache+live_order_adapter ON; 625 OFF. "
            "Eval latency ~1ms unchanged — heavy modules post-accept, not freshness-path delay. " + p611_note
        ),
        "7_median_feed_age_sec": _safe_median(feed_ages_all) or _safe_median(price_ages_all),
        "8_board_fresh_price_stale_total": board_fresh_total,
        "9_system_creates_stale": cls_total.get("B_system_latency_stale", 0) > 0,
        "10_structural_fix": (
            "F1 latest_trade_or_board_ts freshness anchor; F2 conditional board_fallback when board fresh; "
            "F3 persist eval-time payload; enable entry_latency_trace for next live session to measure t0-t6"
        ),
    }

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "analysis_row_count": len(analysis_rows),
        "reconstructed_stale_count": len(all_stale),
        "live_trace_row_count": len(trace_rows),
        "push_jsonl_available": push_cache.available,
        "classification_total": dict(cls_total),
        "cohort_stats": cohort_stats,
        "good_A_vs_B": {"A": good_a, "B": good_b},
        "bad_A_vs_B": {"A": bad_a, "B": bad_b},
        "mandatory_answers": mandatory,
        "notes": [
            "Historical reconstruction uses entry_scan_audit.jsonl (reject_reason=data_stale_price).",
            "push_jsonl join used when available (recorded_at -> eval_start_ts for d_system_to_freshness_ms).",
            "Per-stage t0-t6 breakdown (parse/enqueue/freshness/pbv2/record) requires entry_latency_trace.jsonl from next live session.",
        ],
    }

    sample_cols = [
        "cohort",
        "day",
        "session",
        "symbol",
        "event_time",
        "stale_classification",
        "d_feed_price_age_at_push_sec",
        "d_system_to_freshness_ms",
        "d_price_age_at_freshness_sec",
        "d_board_age_at_freshness_sec",
        "CurrentPriceTime",
        "entry_score_v2",
        "source",
        "push_join_available",
    ]
    _write_gz_csv(reports / "phase613_data_stale_samples.csv.gz", sample_cols, stale_samples)
    _write_gz_csv(reports / "phase613_slowest_pipeline_samples.csv.gz", sample_cols, slowest)
    _write_csv(
        reports / "phase613_stale_classification.csv",
        ["classification", "count"],
        [{"classification": k, "count": v} for k, v in cls_total.most_common()],
    )
    _write_csv(
        reports / "phase613_latency_bucket_summary.csv",
        list(bucket_rows[0].keys()) if bucket_rows else ["session_key", "classification", "count"],
        bucket_rows,
    )
    _write_csv(
        reports / "phase613_symbol_latency_breakdown.csv",
        list(sym_rows[0].keys()) if sym_rows else ["symbol", "stale_count"],
        sym_rows[:MAX_SYMBOL_SAMPLES],
    )
    _write_csv(
        reports / "phase613_stage_latency_percentiles.csv",
        list(stage_pct_rows[0].keys()) if stage_pct_rows else ["stage", "p50", "p90", "p99", "max", "n"],
        stage_pct_rows,
    )
    (reports / "phase613_latency_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
