"""
Phase644b: Live paper order latency measurement from order_latency_dryrun_trace.jsonl.

Collects traces from live sessions only (no synthetic / push-replay).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from small_paper.order_latency_dryrun_trace import (
    TRACE_FILENAME,
    _session_kind_from_ts,
    _time_bucket_from_ts,
    compute_latency_stats,
    detect_bottleneck,
    evaluate_latency_thresholds,
)

PHASE644B_VERDICT = "phase644b_live_order_latency_measurement_done"
PHASE644B_FAIL = "phase644b_live_order_latency_measurement_fail"
REPORT_DIR_NAME = "phase644b_live_order_latency"

METRIC_KEYS = (
    ("price_to_order_sec", "sec"),
    ("push_to_order_sec", "sec"),
    ("push_to_decision_ms", "ms"),
    ("decision_to_order_ms", "ms"),
    ("queue_latency_ms", "ms"),
    ("order_build_ms", "ms"),
    ("dryrun_ms", "ms"),
    ("decision_latency_ms", "ms"),
)

SUMMARY_CSV_FIELDS = [
    "metric",
    "unit",
    "count",
    "p50",
    "p90",
    "p95",
    "p99",
    "max",
    "mean",
]

BY_POOL_FIELDS = [
    "pool",
    "sample_kind",
    "count",
    "reached_dryrun_count",
    "push_to_order_p50_sec",
    "push_to_order_p95_sec",
    "push_to_order_p99_sec",
    "push_to_order_max_sec",
    "price_to_order_p50_sec",
    "price_to_order_p95_sec",
    "decision_latency_p95_ms",
    "queue_latency_p95_ms",
    "order_build_p95_ms",
]

BY_SYMBOL_FIELDS = [
    "symbol",
    "count",
    "session_kinds",
    "push_to_order_p50_sec",
    "push_to_order_p95_sec",
    "push_to_order_max_sec",
    "price_to_order_p95_sec",
    "top_sample_kind",
]

BY_TIMEBUCKET_FIELDS = [
    "time_bucket",
    "session_kind",
    "count",
    "push_to_order_p50_sec",
    "push_to_order_p95_sec",
    "push_to_order_max_sec",
    "price_to_order_p95_sec",
]

TOP20_FIELDS = [
    "rank",
    "symbol",
    "sample_kind",
    "session_kind",
    "time_bucket",
    "push_to_order_sec",
    "price_to_order_sec",
    "decision_latency_ms",
    "queue_latency_ms",
    "order_build_ms",
    "source_session",
    "source_day",
]


def _load_jsonl(fp: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with fp.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _is_live_session_trace(fp: Path) -> bool:
    parts = {p.lower() for p in fp.parts}
    if "_synthetic_probe" in parts or "reports" in parts or "_phase630" in parts:
        return False
    if "live_session_" not in fp.parent.name:
        return False
    summary = fp.parent / "small_paper_summary.json"
    if summary.is_file():
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
            if str(data.get("source") or "") == "push-replay":
                return False
        except json.JSONDecodeError:
            pass
    return True


def iter_live_trace_files(small_paper_root: Path) -> Iterable[tuple[Path, str, str]]:
    """Yield (trace_path, day_key, session_name)."""
    if not small_paper_root.is_dir():
        return
    for day_dir in sorted(small_paper_root.iterdir()):
        if not day_dir.is_dir() or not (len(day_dir.name) == 8 and day_dir.name.isdigit()):
            continue
        for sess in sorted(day_dir.glob("live_session_*")):
            fp = sess / TRACE_FILENAME
            if fp.is_file() and _is_live_session_trace(fp):
                yield fp, day_dir.name, sess.name


def enrich_trace_row(row: dict[str, Any], *, day_key: str, session_name: str) -> dict[str, Any]:
    out = dict(row)
    out.setdefault("session_kind", _session_kind_from_ts(out.get("t1_push_received_at")))
    out.setdefault("time_bucket", _time_bucket_from_ts(out.get("t1_push_received_at")))
    if out.get("order_build_ms") is None and out.get("order_build_latency_ms") is not None:
        out["order_build_ms"] = out.get("order_build_latency_ms")
    if out.get("dryrun_ms") is None and out.get("dryrun_latency_ms") is not None:
        out["dryrun_ms"] = out.get("dryrun_latency_ms")
    out["source_day"] = day_key
    out["source_session"] = session_name
    return out


def load_live_traces(small_paper_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    samples: list[dict[str, Any]] = []
    sources: list[str] = []
    for fp, day_key, session_name in iter_live_trace_files(small_paper_root):
        rows = _load_jsonl(fp)
        if not rows:
            continue
        for row in rows:
            samples.append(enrich_trace_row(row, day_key=day_key, session_name=session_name))
        sources.append(str(fp))
    return samples, sources


def _vals(samples: Sequence[Mapping[str, Any]], key: str, *, dryrun_only: bool = False) -> list[float]:
    out: list[float] = []
    for r in samples:
        if dryrun_only and not r.get("reached_dryrun"):
            continue
        v = r.get(key)
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _filter_kind(samples: Sequence[Mapping[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [dict(r) for r in samples if str(r.get("sample_kind") or "") == kind]


def _metric_summary_rows(samples: Sequence[Mapping[str, Any]], *, dryrun_only: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, unit in METRIC_KEYS:
        stats = compute_latency_stats(_vals(samples, key, dryrun_only=dryrun_only))
        rows.append({"metric": key, "unit": unit, **stats})
    return rows


def _by_pool_rows(samples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pools = [
        ("PBv2", "pbv2_accepted"),
        ("OR", "or_accepted"),
        ("cap_blocked", "cap_blocked"),
        ("max_scan_blocked", "max_scan_blocked"),
        ("all", ""),
    ]
    rows: list[dict[str, Any]] = []
    for pool, kind in pools:
        subset = list(samples) if not kind else _filter_kind(samples, kind)
        if not subset:
            continue
        push = compute_latency_stats(_vals(subset, "push_to_order_sec", dryrun_only=True))
        price = compute_latency_stats(_vals(subset, "price_to_order_sec", dryrun_only=True))
        dec = compute_latency_stats(_vals(subset, "decision_latency_ms"))
        queue = compute_latency_stats(_vals(subset, "queue_latency_ms"))
        ob = compute_latency_stats(_vals(subset, "order_build_ms"))
        rows.append(
            {
                "pool": pool,
                "sample_kind": kind or "all",
                "count": len(subset),
                "reached_dryrun_count": sum(1 for r in subset if r.get("reached_dryrun")),
                "push_to_order_p50_sec": push.get("p50"),
                "push_to_order_p95_sec": push.get("p95"),
                "push_to_order_p99_sec": push.get("p99"),
                "push_to_order_max_sec": push.get("max"),
                "price_to_order_p50_sec": price.get("p50"),
                "price_to_order_p95_sec": price.get("p95"),
                "decision_latency_p95_ms": dec.get("p95"),
                "queue_latency_p95_ms": queue.get("p95"),
                "order_build_p95_ms": ob.get("p95"),
            }
        )
    return rows


def _by_symbol_rows(samples: Sequence[Mapping[str, Any]], *, top_n: int = 100) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = {}
    for r in samples:
        by_sym.setdefault(str(r.get("symbol") or ""), []).append(dict(r))
    rows: list[dict[str, Any]] = []
    for sym in sorted(by_sym.keys(), key=lambda s: len(by_sym[s]), reverse=True)[:top_n]:
        subset = by_sym[sym]
        push = compute_latency_stats(_vals(subset, "push_to_order_sec", dryrun_only=True))
        price = compute_latency_stats(_vals(subset, "price_to_order_sec", dryrun_only=True))
        kinds = sorted({str(r.get("sample_kind") or "") for r in subset})
        rows.append(
            {
                "symbol": sym,
                "count": len(subset),
                "session_kinds": "|".join(sorted({str(r.get("session_kind") or "") for r in subset})),
                "push_to_order_p50_sec": push.get("p50"),
                "push_to_order_p95_sec": push.get("p95"),
                "push_to_order_max_sec": push.get("max"),
                "price_to_order_p95_sec": price.get("p95"),
                "top_sample_kind": kinds[0] if len(kinds) == 1 else "mixed",
            }
        )
    return rows


def _by_timebucket_rows(samples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in samples:
        key = (str(r.get("time_bucket") or "unknown"), str(r.get("session_kind") or "UNKNOWN"))
        buckets.setdefault(key, []).append(dict(r))
    rows: list[dict[str, Any]] = []
    for (tb, sk) in sorted(buckets.keys()):
        subset = buckets[(tb, sk)]
        push = compute_latency_stats(_vals(subset, "push_to_order_sec", dryrun_only=True))
        price = compute_latency_stats(_vals(subset, "price_to_order_sec", dryrun_only=True))
        rows.append(
            {
                "time_bucket": tb,
                "session_kind": sk,
                "count": len(subset),
                "push_to_order_p50_sec": push.get("p50"),
                "push_to_order_p95_sec": push.get("p95"),
                "push_to_order_max_sec": push.get("max"),
                "price_to_order_p95_sec": price.get("p95"),
            }
        )
    return rows


def _top20_rows(samples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    dryrun = [dict(r) for r in samples if r.get("reached_dryrun") and r.get("push_to_order_sec") is not None]
    dryrun.sort(key=lambda r: float(r["push_to_order_sec"]), reverse=True)
    rows: list[dict[str, Any]] = []
    for i, r in enumerate(dryrun[:20], start=1):
        rows.append(
            {
                "rank": i,
                "symbol": r.get("symbol"),
                "sample_kind": r.get("sample_kind"),
                "session_kind": r.get("session_kind"),
                "time_bucket": r.get("time_bucket"),
                "push_to_order_sec": r.get("push_to_order_sec"),
                "price_to_order_sec": r.get("price_to_order_sec"),
                "decision_latency_ms": r.get("decision_latency_ms"),
                "queue_latency_ms": r.get("queue_latency_ms"),
                "order_build_ms": r.get("order_build_ms"),
                "source_session": r.get("source_session"),
                "source_day": r.get("source_day"),
            }
        )
    return rows


def _improvement_target(bottleneck: str, thresholds: Mapping[str, Any]) -> str:
    if thresholds.get("queue_latency_p95_warning"):
        return "queue"
    if thresholds.get("decision_latency_p95_warning"):
        return "PBv2/decision"
    if bottleneck in ("order_build_ms", "payload_to_enrich_ms"):
        return "order build"
    if bottleneck == "pbv2_or_latency_ms":
        return "PBv2"
    if bottleneck == "queue_latency_ms":
        return "queue"
    return "continue monitoring (no dominant bottleneck yet)"


def build_mandatory_answers(samples: Sequence[Mapping[str, Any]], *, sources: Sequence[str]) -> dict[str, Any]:
    push = compute_latency_stats(_vals(samples, "push_to_order_sec", dryrun_only=True))
    price = compute_latency_stats(_vals(samples, "price_to_order_sec", dryrun_only=True))
    stage_stats = {
        "decision_latency_ms": compute_latency_stats(_vals(samples, "decision_latency_ms")),
        "queue_latency_ms": compute_latency_stats(_vals(samples, "queue_latency_ms")),
        "order_build_ms": compute_latency_stats(_vals(samples, "order_build_ms")),
        "pbv2_or_latency_ms": compute_latency_stats(_vals(samples, "pbv2_or_latency_ms")),
        "payload_to_enrich_ms": compute_latency_stats(_vals(samples, "payload_to_enrich_ms")),
    }
    bundle = {
        "push_to_order_sec": push,
        "price_to_order_sec": price,
        "queue_latency_ms": stage_stats["queue_latency_ms"],
        "decision_latency_ms": stage_stats["decision_latency_ms"],
    }
    thresholds = evaluate_latency_thresholds(bundle)
    bottleneck = detect_bottleneck(stage_stats)

    slow_symbols = [
        r for r in _by_symbol_rows(samples)
        if r.get("push_to_order_max_sec") is not None and float(r["push_to_order_max_sec"]) > 1.0
    ]
    slow_buckets = [
        r for r in _by_timebucket_rows(samples)
        if r.get("push_to_order_p95_sec") is not None and float(r["push_to_order_p95_sec"]) > 1.0
    ]

    return {
        "1_live_trace_count": len(samples),
        "1_live_trace_sources": len(sources),
        "2_pbv2_sample_count": len(_filter_kind(samples, "pbv2_accepted")),
        "2_or_sample_count": len(_filter_kind(samples, "or_accepted")),
        "2_cap_blocked_sample_count": len(_filter_kind(samples, "cap_blocked")),
        "2_max_scan_blocked_sample_count": len(_filter_kind(samples, "max_scan_blocked")),
        "3_push_to_order_p50_sec": push.get("p50"),
        "3_push_to_order_p95_sec": push.get("p95"),
        "3_push_to_order_p99_sec": push.get("p99"),
        "3_push_to_order_max_sec": push.get("max"),
        "4_price_to_order_p50_sec": price.get("p50"),
        "4_price_to_order_p95_sec": price.get("p95"),
        "4_price_to_order_p99_sec": price.get("p99"),
        "4_price_to_order_max_sec": price.get("max"),
        "5_top_bottleneck": bottleneck,
        "5_stage_p95_ms": {k: v.get("p95") for k, v in stage_stats.items()},
        "6_slow_symbols": slow_symbols[:10],
        "6_slow_time_buckets": slow_buckets[:10],
        "7_ready_for_real_orders": thresholds.get("acceptable_for_live_orders") if samples else None,
        "7_threshold_alerts": thresholds.get("alerts"),
        "8_improvement_target": _improvement_target(bottleneck, thresholds) if samples else "await live traces",
        "9_continue_monitoring": [
            "push_to_order p50/p95/p99/max",
            "price_to_order p50/p95/p99/max",
            "queue_latency_ms p95",
            "decision_latency_ms p95",
            "PBv2 vs OR pool split",
            "latency_top20 tail outliers",
        ],
        "no_live_traces_yet": len(samples) == 0,
    }


@dataclass
class Phase644bJob:
    native_root: Path
    report_dir: Optional[Path] = None

    def run(self) -> dict[str, Any]:
        native = self.native_root.resolve()
        small_paper = native / "results" / "small_paper"
        samples, sources = load_live_traces(small_paper)
        answers = build_mandatory_answers(samples, sources=sources)
        dryrun = [r for r in samples if r.get("reached_dryrun")]
        push = compute_latency_stats(_vals(samples, "push_to_order_sec", dryrun_only=True))
        price = compute_latency_stats(_vals(samples, "price_to_order_sec", dryrun_only=True))
        stage_stats = {
            "decision_latency_ms": compute_latency_stats(_vals(samples, "decision_latency_ms")),
            "queue_latency_ms": compute_latency_stats(_vals(samples, "queue_latency_ms")),
            "order_build_ms": compute_latency_stats(_vals(samples, "order_build_ms")),
            "pbv2_or_latency_ms": compute_latency_stats(_vals(samples, "pbv2_or_latency_ms")),
        }
        thresholds = evaluate_latency_thresholds(
            {
                "push_to_order_sec": push,
                "price_to_order_sec": price,
                "queue_latency_ms": stage_stats["queue_latency_ms"],
                "decision_latency_ms": stage_stats["decision_latency_ms"],
            }
        )
        return {
            "verdict": PHASE644B_VERDICT,
            "generated_at": _now_iso(),
            "mandatory_answers": answers,
            "counts": {
                "sample_count": len(samples),
                "dryrun_reached_count": len(dryrun),
                "pbv2_accepted": answers["2_pbv2_sample_count"],
                "or_accepted": answers["2_or_sample_count"],
                "cap_blocked": answers["2_cap_blocked_sample_count"],
                "max_scan_blocked": answers["2_max_scan_blocked_sample_count"],
            },
            "thresholds": thresholds,
            "top_bottleneck": detect_bottleneck(stage_stats) if samples else "none",
            "samples": samples,
            "sources": sources,
            "summary_csv": _metric_summary_rows(samples, dryrun_only=True),
            "by_pool": _by_pool_rows(samples),
            "by_symbol": _by_symbol_rows(samples),
            "by_timebucket": _by_timebucket_rows(samples),
            "top20": _top20_rows(samples),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        out = self.report_dir or (self.native_root / "results" / "reports" / REPORT_DIR_NAME)
        out.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}
        _write_csv(out / "phase644b_latency_summary.csv", SUMMARY_CSV_FIELDS, list(result.get("summary_csv") or []))
        paths["summary"] = out / "phase644b_latency_summary.csv"
        _write_csv(out / "phase644b_by_pool.csv", BY_POOL_FIELDS, list(result.get("by_pool") or []))
        paths["by_pool"] = out / "phase644b_by_pool.csv"
        _write_csv(out / "phase644b_by_symbol.csv", BY_SYMBOL_FIELDS, list(result.get("by_symbol") or []))
        paths["by_symbol"] = out / "phase644b_by_symbol.csv"
        _write_csv(out / "phase644b_by_timebucket.csv", BY_TIMEBUCKET_FIELDS, list(result.get("by_timebucket") or []))
        paths["by_timebucket"] = out / "phase644b_by_timebucket.csv"
        _write_csv(out / "phase644b_latency_top20.csv", TOP20_FIELDS, list(result.get("top20") or []))
        paths["top20"] = out / "phase644b_latency_top20.csv"
        report = {
            "phase": "644b",
            "verdict": result.get("verdict"),
            "generated_at": result.get("generated_at"),
            "mandatory_answers": result.get("mandatory_answers"),
            "counts": result.get("counts"),
            "thresholds": result.get("thresholds"),
            "top_bottleneck": result.get("top_bottleneck"),
            "sources": result.get("sources"),
            "artifacts": {k: str(v) for k, v in paths.items()},
        }
        report_fp = out / "phase644b_report.json"
        report_fp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["report"] = report_fp
        return paths


def main() -> int:
    here = Path(__file__).resolve()
    native = here.parents[2]
    job = Phase644bJob(native_root=native)
    result = job.run()
    paths = job.write_outputs(result)
    print(json.dumps({"verdict": result.get("verdict"), "paths": {k: str(v) for k, v in paths.items()}}, indent=2))
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=False, indent=2))
    return 0 if result.get("verdict") == PHASE644B_VERDICT else 1


if __name__ == "__main__":
    raise SystemExit(main())
