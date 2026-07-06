"""
Phase644: Live order latency readiness audit (research / reporting).
"""

from __future__ import annotations

import csv
import gzip
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import JST, _now_iso
from small_paper.order_latency_dryrun_trace import (
    TRACE_FILENAME,
    OrderLatencyDryRunSession,
    aggregate_samples,
    order_latency_dryrun_summary_fields,
)

PHASE644_VERDICT = "phase644_live_order_latency_readiness_done"
PHASE644_FAIL = "phase644_live_order_latency_readiness_fail"

REPORT_DIR_NAME = "phase644_live_order_latency"

SUMMARY_FIELDS = [
    "metric",
    "value",
    "unit",
    "notes",
]

BY_POOL_FIELDS = [
    "sample_kind",
    "count",
    "reached_dryrun_count",
    "push_to_order_p50_sec",
    "push_to_order_p95_sec",
    "push_to_order_max_sec",
    "price_to_order_p50_sec",
    "price_to_order_p95_sec",
    "decision_latency_p50_ms",
    "queue_latency_p50_ms",
    "order_build_latency_p50_ms",
]

BY_SYMBOL_FIELDS = [
    "symbol",
    "count",
    "push_to_order_p50_sec",
    "push_to_order_p95_sec",
    "price_to_order_p50_sec",
    "sample_kinds",
]


def _iter_trace_files(small_paper_root: Path) -> Iterable[Path]:
    if not small_paper_root.is_dir():
        return
    for day_dir in sorted(small_paper_root.iterdir()):
        if not day_dir.is_dir():
            continue
        for sess in sorted(day_dir.glob("live_session_*")):
            fp = sess / TRACE_FILENAME
            if fp.is_file():
                yield fp


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


def _percentile(values: Sequence[float], pct: float) -> Optional[float]:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return round(xs[0], 6)
    k = (len(xs) - 1) * pct
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return round(xs[f], 6)
    return round(xs[f] + (xs[c] - xs[f]) * (k - f), 6)


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run_synthetic_probe(output_dir: Path) -> list[dict[str, Any]]:
    """Generate reference latency samples via sendorder dry-run wiring (no HTTP)."""
    from small_paper.live_order_api_wiring import LiveOrderWiringSession, process_entry_wiring

    class _Writer:
        def append_live_order_latency(self, row: Mapping[str, Any]) -> None:
            pass

        def append_live_order_would_send(self, row: Mapping[str, Any]) -> None:
            pass

    class _Cfg:
        live_trading_enabled = False
        order_enabled = False
        live_order_dry_run_enabled = True
        live_order_api_wiring_enabled = True
        order_latency_dryrun_trace_enabled = True
        live_order_entry_timeout_sec = 4.0

    now = datetime.now(JST)
    cpt = (now - timedelta(milliseconds=50)).isoformat(timespec="milliseconds")
    recv = now.isoformat(timespec="milliseconds")
    payload = {
        "CurrentPriceTime": cpt,
        "recorded_at": recv,
        "CurrentPrice": 1500.0,
        "AskPrice": 1501.0,
        "Symbol": "1234",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    trace = OrderLatencyDryRunSession(output_dir)
    import time

    trace.begin_push(
        symbol="1234.T",
        payload=payload,
        message_index=1,
        t1_push_received_at=recv,
        t2_mono=time.monotonic(),
    )
    trace.mark_enrich_end()
    trace.mark_freshness_end()
    trace.mark_decision_end(accepted=True, entry_route="pbv2", gate_reason="")
    trace.mark_direct_execute(entry_signal_mono=time.monotonic())
    wiring = LiveOrderWiringSession()
    trade = {"entry_time": recv, "entry_type": "PBV2", "current_price": 1500.0}
    process_entry_wiring(
        wiring,
        symbol="1234.T",
        trade=trade,
        payload=payload,
        writer=_Writer(),
        config=_Cfg(),
        entry_signal_ts=recv,
        latency_session=trace,
    )
    return trace.samples


def _by_pool_rows(samples: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    kinds = sorted({str(r.get("sample_kind") or "unknown") for r in samples})
    rows: list[dict[str, Any]] = []
    for kind in kinds:
        subset = [r for r in samples if str(r.get("sample_kind") or "") == kind]
        push = [_num(r.get("push_to_order_sec")) for r in subset if r.get("reached_dryrun")]
        push = [x for x in push if x is not None]
        price = [_num(r.get("price_to_order_sec")) for r in subset if r.get("reached_dryrun")]
        price = [x for x in price if x is not None]
        dec = [_num(r.get("decision_latency_ms")) for r in subset]
        dec = [x for x in dec if x is not None]
        q = [_num(r.get("queue_latency_ms")) for r in subset]
        q = [x for x in q if x is not None]
        ob = [_num(r.get("order_build_latency_ms")) for r in subset]
        ob = [x for x in ob if x is not None]
        rows.append(
            {
                "sample_kind": kind,
                "count": len(subset),
                "reached_dryrun_count": sum(1 for r in subset if r.get("reached_dryrun")),
                "push_to_order_p50_sec": _percentile(push, 0.5),
                "push_to_order_p95_sec": _percentile(push, 0.95),
                "push_to_order_max_sec": round(max(push), 6) if push else None,
                "price_to_order_p50_sec": _percentile(price, 0.5),
                "price_to_order_p95_sec": _percentile(price, 0.95),
                "decision_latency_p50_ms": _percentile(dec, 0.5),
                "queue_latency_p50_ms": _percentile(q, 0.5),
                "order_build_latency_p50_ms": _percentile(ob, 0.5),
            }
        )
    return rows


def _by_symbol_rows(samples: Sequence[Mapping[str, Any]], *, top_n: int = 50) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = {}
    for r in samples:
        by_sym.setdefault(str(r.get("symbol") or ""), []).append(dict(r))
    rows: list[dict[str, Any]] = []
    for sym in sorted(by_sym.keys(), key=lambda s: len(by_sym[s]), reverse=True)[:top_n]:
        subset = by_sym[sym]
        push = [_num(r.get("push_to_order_sec")) for r in subset if r.get("reached_dryrun")]
        push = [x for x in push if x is not None]
        price = [_num(r.get("price_to_order_sec")) for r in subset if r.get("reached_dryrun")]
        price = [x for x in price if x is not None]
        kinds = sorted({str(r.get("sample_kind") or "") for r in subset})
        rows.append(
            {
                "symbol": sym,
                "count": len(subset),
                "push_to_order_p50_sec": _percentile(push, 0.5),
                "push_to_order_p95_sec": _percentile(push, 0.95),
                "price_to_order_p50_sec": _percentile(price, 0.5),
                "sample_kinds": "|".join(kinds),
            }
        )
    return rows


def _summary_csv_rows(answers: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {"metric": "push_to_order_p50_sec", "value": answers.get("1_push_to_order_p50_sec"), "unit": "sec", "notes": ""},
        {"metric": "push_to_order_p95_sec", "value": answers.get("1_push_to_order_p95_sec"), "unit": "sec", "notes": ""},
        {"metric": "push_to_order_max_sec", "value": answers.get("1_push_to_order_max_sec"), "unit": "sec", "notes": ""},
        {"metric": "price_to_order_p50_sec", "value": answers.get("2_price_to_order_p50_sec"), "unit": "sec", "notes": ""},
        {"metric": "price_to_order_p95_sec", "value": answers.get("2_price_to_order_p95_sec"), "unit": "sec", "notes": ""},
        {"metric": "price_to_order_max_sec", "value": answers.get("2_price_to_order_max_sec"), "unit": "sec", "notes": ""},
        {"metric": "dominant_delay_stage", "value": answers.get("3_dominant_delay_stage"), "unit": "", "notes": ""},
        {"metric": "acceptable_for_live_orders", "value": answers.get("6_acceptable_for_live_orders"), "unit": "", "notes": ""},
    ]
    return rows


@dataclass
class Phase644Job:
    native_root: Path
    report_dir: Optional[Path] = None
    include_synthetic: bool = True

    def run(self) -> dict[str, Any]:
        native = self.native_root.resolve()
        small_paper = native / "results" / "small_paper"
        samples: list[dict[str, Any]] = []
        sources: list[str] = []
        for fp in _iter_trace_files(small_paper):
            rows = _load_jsonl(fp)
            if rows:
                samples.extend(rows)
                sources.append(str(fp))

        synthetic: list[dict[str, Any]] = []
        if self.include_synthetic and not samples:
            probe_dir = (self.report_dir or native / "results" / "reports" / REPORT_DIR_NAME) / "_synthetic_probe"
            synthetic = run_synthetic_probe(probe_dir)
            samples.extend(synthetic)
            sources.append(str(probe_dir / TRACE_FILENAME))

        answers = aggregate_samples(samples)
        agg = OrderLatencyDryRunSession(
            self.report_dir or native / "results" / "reports" / REPORT_DIR_NAME
        )
        agg.samples = list(samples)
        summary_fields = order_latency_dryrun_summary_fields(agg if samples else None)

        return {
            "verdict": PHASE644_VERDICT if samples else PHASE644_FAIL,
            "generated_at": _now_iso(),
            "mandatory_answers": answers,
            "summary_fields": summary_fields,
            "samples": samples,
            "sources": sources,
            "by_pool": _by_pool_rows(samples),
            "by_symbol": _by_symbol_rows(samples),
            "summary_csv": _summary_csv_rows(answers),
            "synthetic_included": bool(synthetic),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        out = self.report_dir or (self.native_root / "results" / "reports" / REPORT_DIR_NAME)
        out.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}

        _write_csv(out / "phase644_latency_summary.csv", SUMMARY_FIELDS, list(result.get("summary_csv") or []))
        paths["summary"] = out / "phase644_latency_summary.csv"

        samples_fp = out / "phase644_latency_samples.csv.gz"
        sample_rows = list(result.get("samples") or [])
        if sample_rows:
            fieldnames = sorted({k for r in sample_rows for k in r.keys()})
            with gzip.open(samples_fp, "wt", encoding="utf-8", newline="") as gz:
                w = csv.DictWriter(gz, fieldnames=fieldnames, extrasaction="ignore")
                w.writeheader()
                for row in sample_rows:
                    w.writerow(row)
        else:
            samples_fp.write_bytes(b"")
        paths["samples"] = samples_fp

        _write_csv(out / "phase644_by_pool.csv", BY_POOL_FIELDS, list(result.get("by_pool") or []))
        paths["by_pool"] = out / "phase644_by_pool.csv"
        _write_csv(out / "phase644_by_symbol.csv", BY_SYMBOL_FIELDS, list(result.get("by_symbol") or []))
        paths["by_symbol"] = out / "phase644_by_symbol.csv"

        report = {
            "phase": "644",
            "verdict": result.get("verdict"),
            "generated_at": result.get("generated_at"),
            "mandatory_answers": result.get("mandatory_answers"),
            "summary_fields": result.get("summary_fields"),
            "sources": result.get("sources"),
            "synthetic_included": result.get("synthetic_included"),
            "artifacts": {k: str(v) for k, v in paths.items()},
        }
        report_fp = out / "phase644_report.json"
        report_fp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["report"] = report_fp
        return paths


def main() -> int:
    here = Path(__file__).resolve()
    native = here.parents[2]
    job = Phase644Job(native_root=native)
    result = job.run()
    paths = job.write_outputs(result)
    print(json.dumps({"verdict": result.get("verdict"), "paths": {k: str(v) for k, v in paths.items()}}, indent=2))
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=False, indent=2))
    return 0 if result.get("verdict") == PHASE644_VERDICT else 1


if __name__ == "__main__":
    raise SystemExit(main())
