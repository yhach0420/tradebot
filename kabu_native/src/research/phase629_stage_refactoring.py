"""
Phase629: ENTRY pipeline stage refactoring — regression tooling.

Subcommands:
    stagetest            run stage unit tests (S1..S8)
    compare              compare baseline vs after replay outputs for the 4 days
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

HERE = Path(__file__).resolve()
NATIVE_ROOT = HERE.parents[2]          # kabu_native
REPO_ROOT = NATIVE_ROOT.parent

for p in (NATIVE_ROOT / "src", REPO_ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

DAYS = ("2026-06-25", "2026-06-29", "2026-06-30", "2026-07-01")
PHASE_DIR = NATIVE_ROOT / "results" / "small_paper" / "_phase629"
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase629_stage_refactoring"

# Wall-clock / run-dependent fields (identical-code reruns differ on these).
# entry_time/exit_time in candidate/reject trade rows are the run's own wall
# clock (virtual hold projection); payload-derived entry/exit times are
# verified via small_paper_positions.csv where they are kept.
VOLATILE_EVENT_KEYS = frozenset(
    {
        "event_time",
        "eval_start_ts",
        "eval_end_ts",
        "eval_latency_ms",
        "entry_signal_ts",
        "entry_signal_mono",
        "entry_latency_ms",
        "decision_latency_ms",
        "entry_to_first_tick_ms",
        "entry_time",
        "exit_time",
        "scan_start_ts",
        "scan_end_ts",
        "scan_duration_sec",
        # observer exit / shadow fields derived from datetime.now(JST) wall clock
        "hold_sec",
        "shadow_exit_time",
        "no_progress_hold_sec",
        "timestamp",
    }
)
# positions csv columns (symbol, entry_time, exit_time, open_slots_after) are
# all deterministic market-time values -> compared strictly.
VOLATILE_POSITION_KEYS: frozenset[str] = frozenset()
VOLATILE_SUMMARY_KEYS = frozenset(
    {
        "generated_at",
        "runtime_sec",
        "started_at",
        "ended_at",
        "session_started_at",
        "session_ended_at",
        "push_replay_runtime_sec",
        "eval_latency_ms_p50",
        "eval_latency_ms_p95",
        "eval_latency_ms_max",
        "output_dir",
        # embeds the run's output-dir name (baseline/after tag) -> always differs
        "daytrade_suitability_run_session_key",
        # vol_liq cache build timing / hit metadata (wall clock; same thresholds)
        "vol_liq_cache_elapsed_sec",
        "vol_liq_cache_seconds_saved",
        "vol_liq_cache_baseline_elapsed_sec",
        "vol_liq_cache_path",
        "vol_liq_cache_hit",
        "vol_liq_cache_status",
        "vol_liq_cache_fallback",
        "vol_liq_cache_fallback_reason",
    }
)
_SCAN_ID_RE = re.compile(r"^\d{8}_\d{6}_(\d+)$")


def _norm_scan_id(v: Any) -> Any:
    m = _SCAN_ID_RE.match(str(v or ""))
    return f"scan_{m.group(1)}" if m else v


def _canon_row(row: Mapping[str, Any], volatile: frozenset[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k in volatile:
            continue
        if k == "scan_id":
            v = _norm_scan_id(v)
        if isinstance(v, float):
            v = round(v, 9)
        out[k] = v
    return out


def _iter_jsonl(fp: Path) -> Iterable[dict[str, Any]]:
    if not fp.is_file():
        return
    with fp.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _read_csv_rows(fp: Path) -> list[dict[str, Any]]:
    if not fp.is_file():
        return []
    with fp.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _diff_row_lists(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
    *,
    volatile: frozenset[str],
    label: str,
) -> dict[str, Any]:
    field_diffs: Counter = Counter()
    samples: list[dict[str, Any]] = []
    n = min(len(a), len(b))
    mismatch_rows = 0
    for i in range(n):
        ra = _canon_row(a[i], volatile)
        rb = _canon_row(b[i], volatile)
        if ra == rb:
            continue
        mismatch_rows += 1
        keys = set(ra) | set(rb)
        for k in keys:
            if ra.get(k) != rb.get(k):
                field_diffs[k] += 1
                if len(samples) < 20:
                    samples.append(
                        {"row": i, "field": k, "baseline": ra.get(k), "after": rb.get(k)}
                    )
    return {
        "label": label,
        "rows_baseline": len(a),
        "rows_after": len(b),
        "row_count_match": len(a) == len(b),
        "mismatch_rows": mismatch_rows,
        "field_diffs": dict(field_diffs),
        "samples": samples,
        "match": len(a) == len(b) and mismatch_rows == 0,
    }


def _summary_diff(sa: Mapping[str, Any], sb: Mapping[str, Any]) -> dict[str, Any]:
    diffs: dict[str, Any] = {}
    for k in sorted(set(sa) | set(sb)):
        if k in VOLATILE_SUMMARY_KEYS:
            continue
        va, vb = sa.get(k), sb.get(k)
        if isinstance(va, float) and isinstance(vb, float):
            if round(va, 9) == round(vb, 9):
                continue
        if va != vb:
            diffs[k] = {"baseline": va, "after": vb}
    return diffs


def _discord_lines_from_summary(summary: Mapping[str, Any]) -> list[str]:
    """Deterministic Discord content derived from summary (builder unchanged)."""
    from small_paper.discord_message_builder import (
        format_freshness_semantics_v2_lines,
        format_gate_dominance_alert_lines,
        format_pbv2_internal_breakdown_lines,
        format_runtime_health_lines,
        format_summary_yen_display_lines,
    )

    s = {k: v for k, v in summary.items() if k not in VOLATILE_SUMMARY_KEYS}
    lines: list[str] = []
    lines += format_summary_yen_display_lines(s)
    lines += format_freshness_semantics_v2_lines(s)
    lines += format_pbv2_internal_breakdown_lines(s)
    lines += format_gate_dominance_alert_lines(s)
    # runtime-health lines contain latency numbers -> skip volatile entries
    lines += [
        ln
        for ln in format_runtime_health_lines(s)
        if "latency" not in ln.lower() and "runtime" not in ln.lower()
    ]
    return lines


def _key_metrics(events: list[dict[str, Any]], summary: Mapping[str, Any]) -> dict[str, Any]:
    accepted = [e for e in events if e.get("event_type") == "accepted"]
    or_accepted = [e for e in accepted if str(e.get("entry_type") or "").upper() == "OR"]
    reject_reasons = Counter(
        str(e.get("gate_reject_reason") or "")
        for e in events
        if e.get("event_type") == "rejected"
    )
    exit_count = sum(
        1 for e in events if str(e.get("event_type") or "").startswith("observer_")
        and e.get("exit_reason")
    )
    if exit_count == 0:
        exit_count = sum(1 for e in events if e.get("event_type") == "exit")
    return {
        "candidates": sum(1 for e in events if e.get("event_type") == "candidate"),
        "entry_count": len(accepted),
        "pbv2_accepted": len(accepted) - len(or_accepted),
        "or_accepted": len(or_accepted),
        "exit_count": exit_count,
        "rejected_count": sum(1 for e in events if e.get("event_type") == "rejected"),
        "reject_reason_top": dict(reject_reasons.most_common(10)),
        "gate_evaluations": summary.get("gate_evaluations"),
        "stale_reason_counts": summary.get("stale_reason_counts"),
        "pbv2_internal_reason_counts": summary.get("pbv2_internal_reason_counts"),
    }


def compare_day(day: str, tag_a: str, tag_b: str) -> dict[str, Any]:
    da = PHASE_DIR / tag_a / day.replace("-", "")
    db = PHASE_DIR / tag_b / day.replace("-", "")
    ev_a = list(_iter_jsonl(da / "small_paper_events.jsonl"))
    ev_b = list(_iter_jsonl(db / "small_paper_events.jsonl"))
    rej_a = _read_csv_rows(da / "small_paper_rejects.csv")
    rej_b = _read_csv_rows(db / "small_paper_rejects.csv")
    pos_a = _read_csv_rows(da / "small_paper_positions.csv")
    pos_b = _read_csv_rows(db / "small_paper_positions.csv")
    sum_a = json.loads((da / "small_paper_summary.json").read_text(encoding="utf-8"))
    sum_b = json.loads((db / "small_paper_summary.json").read_text(encoding="utf-8"))
    aud_a = list(_iter_jsonl(da / "entry_scan_audit.jsonl"))
    aud_b = list(_iter_jsonl(db / "entry_scan_audit.jsonl"))

    events_cmp = _diff_row_lists(ev_a, ev_b, volatile=VOLATILE_EVENT_KEYS, label="events_jsonl")
    rejects_cmp = _diff_row_lists(rej_a, rej_b, volatile=VOLATILE_EVENT_KEYS, label="rejects_csv")
    pos_cmp = _diff_row_lists(pos_a, pos_b, volatile=VOLATILE_POSITION_KEYS, label="positions_csv")
    audit_cmp = _diff_row_lists(aud_a, aud_b, volatile=VOLATILE_EVENT_KEYS, label="entry_scan_audit")
    summary_diffs = _summary_diff(sum_a, sum_b)
    disc_a = _discord_lines_from_summary(sum_a)
    disc_b = _discord_lines_from_summary(sum_b)

    met_a = _key_metrics(ev_a, sum_a)
    met_b = _key_metrics(ev_b, sum_b)
    return {
        "day": day,
        "events": events_cmp,
        "rejects": rejects_cmp,
        "positions": pos_cmp,
        "audit": audit_cmp,
        "summary_diff_keys": summary_diffs,
        "summary_match": not summary_diffs,
        "discord_lines_match": disc_a == disc_b,
        "discord_lines_baseline": disc_a,
        "discord_lines_after": disc_b,
        "key_metrics_baseline": met_a,
        "key_metrics_after": met_b,
        "key_metrics_match": met_a == met_b,
        "match": (
            events_cmp["match"]
            and rejects_cmp["match"]
            and pos_cmp["match"]
            and audit_cmp["match"]
            and not summary_diffs
            and disc_a == disc_b
            and met_a == met_b
        ),
    }


def cmd_compare(tag_a: str = "baseline", tag_b: str = "after") -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    all_match = True
    for day in DAYS:
        r = compare_day(day, tag_a, tag_b)
        results.append(r)
        all_match = all_match and r["match"]
        print(
            f"{day}: match={r['match']} events={r['events']['match']} "
            f"rejects={r['rejects']['match']} positions={r['positions']['match']} "
            f"audit={r['audit']['match']} summary={r['summary_match']} "
            f"discord={r['discord_lines_match']} metrics={r['key_metrics_match']}",
            flush=True,
        )
        if not r["match"]:
            for section in ("events", "rejects", "positions", "audit"):
                sec = r[section]
                if not sec["match"]:
                    print(f"  {section}: rows {sec['rows_baseline']}/{sec['rows_after']} "
                          f"mismatch_rows={sec['mismatch_rows']} fields={sec['field_diffs']}")
                    for smp in sec["samples"][:5]:
                        print(f"    sample: {smp}")
            if r["summary_diff_keys"]:
                print(f"  summary diffs: {list(r['summary_diff_keys'])[:20]}")
    out = REPORT_DIR / f"phase629_compare_{tag_a}_vs_{tag_b}.json"
    out.write_text(
        json.dumps(
            {
                "tag_a": tag_a,
                "tag_b": tag_b,
                "all_match": all_match,
                "days": [
                    {k: v for k, v in r.items() if k not in ("discord_lines_baseline", "discord_lines_after")}
                    for r in results
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"ALL_MATCH={all_match} -> {out}", flush=True)
    return 0 if all_match else 1


# ---------------------------------------------------------------------------
# Stage unit tests
# ---------------------------------------------------------------------------


def _result(test_id: str, name: str, passed: bool, detail: str) -> dict[str, Any]:
    print(f"[{test_id}] {'PASS' if passed else 'FAIL'} {name} :: {detail}", flush=True)
    return {"test_id": test_id, "name": name, "passed": bool(passed), "detail": detail}


def _mk_ctx(tmp_dir: Path):
    """Minimal live-like _PushPipelineContext (same recipe as live_pipeline_preflight)."""
    import time

    from small_paper.config import load_pilot_config
    from small_paper.live_feature_bridge import LiveFeatureBridge
    from small_paper.live_writer import LiveSessionWriter
    from small_paper.pilot_runner import (
        EVENT_FIELDS,
        _LiveRunState,
        _PushPipelineContext,
        _make_entry_scan_controller,
    )

    cfg_path = NATIVE_ROOT / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    from dataclasses import replace

    config = replace(load_pilot_config(cfg_path), discord_enabled=False)
    gate = config.make_exposure_gate(repo_root=REPO_ROOT)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    writer = LiveSessionWriter(tmp_dir, incremental=False, event_fields=EVENT_FIELDS)
    state = _LiveRunState(started_mono=time.monotonic())
    ctx = _PushPipelineContext(
        config=config,
        gate=gate,
        feature_bridge=LiveFeatureBridge(config.feature_bridge_config()),
        state=state,
        writer=writer,
        code_to_symbol={"6976": "6976.T"},
        source="push-replay",
        pos_fields=(),
        entry_scan=_make_entry_scan_controller(config, source="push-replay", writer=writer),
    )
    return ctx


def _sample_payload(now_iso: str) -> dict[str, Any]:
    return {
        "Symbol": "6976",
        "SymbolName": "TAIYO YUDEN",
        "CurrentPrice": 1500.0,
        "CurrentPriceTime": now_iso,
        "CalcPrice": 1500.0,
        "PreviousClose": 1450.0,
        "BidPrice": 1499.0,
        "AskPrice": 1501.0,
        "BidTime": now_iso,
        "AskTime": now_iso,
        "TradingVolume": 500000,
        "TradingValue": 750000000,
        "HighPrice": 1520.0,
        "LowPrice": 1440.0,
        "OpeningPrice": 1460.0,
        "recorded_at": now_iso,
    }


def cmd_stagetest() -> int:
    import shutil
    import time
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from small_paper.entry_pipeline_stages import (
        CLUSTER_STAGE_NOT_EVALUATED,
        Stage0NormalizedPayload,
        Stage1FreshnessResult,
        Stage2PBv2Result,
        Stage3ClusterDecision,
        Stage4FinalEntryDecision,
        StageTraceLogger,
        classify_cluster_stage,
        stage_trace_enabled,
    )
    from small_paper.pilot_runner import (
        _process_push_payload,
        _stage0_normalize_payload,
        _stage1_evaluate_freshness,
        _stage2_evaluate_pbv2,
        _stage3_cluster_decision,
        _stage4_finalize_decision,
        _stage6_record_candidate,
    )

    JST = ZoneInfo("Asia/Tokyo")
    now_iso = datetime.now(JST).isoformat(timespec="seconds")
    tmp = NATIVE_ROOT / "temp" / "_phase629_stagetest"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    results: list[dict[str, Any]] = []

    # S1: Stage0 returns dataclass with normalized payload/trade
    ctx = _mk_ctx(tmp)
    payload = _sample_payload(now_iso)
    norm = _stage0_normalize_payload(ctx, payload, 1, t0_push_received_at=now_iso)
    ok = (
        isinstance(norm, Stage0NormalizedPayload)
        and norm.symbol == "6976.T"
        and isinstance(norm.trade, dict)
        and norm.trade.get("symbol") == "6976.T"
        and "recorded_at" in norm.enriched
    )
    results.append(_result("S1", "Stage0 normalize -> Stage0NormalizedPayload", ok,
                           f"symbol={getattr(norm, 'symbol', None)}"))

    # S2: Stage0 returns None when symbol cannot be resolved
    bad = _stage0_normalize_payload(ctx, {"CurrentPrice": 1.0}, 2)
    results.append(_result("S2", "Stage0 unresolvable symbol -> None", bad is None, f"got={bad}"))

    # S3: Stage1 freshness dataclass, fresh input passes (no short circuit)
    fresh = _stage1_evaluate_freshness(ctx, norm)
    ok = (
        isinstance(fresh, Stage1FreshnessResult)
        and fresh.short_circuit_decision is None
        and fresh.stale_reason in (None, "")
        and fresh.ref_now is not None
        and not fresh.ref_now_unbound
    )
    results.append(_result("S3", "Stage1 fresh payload -> no short-circuit", ok,
                           f"stale={fresh.stale_reason} pre_gate={fresh.pre_gate_reason!r}"))

    # S4: Stage1 stale input -> short-circuit GateDecision with stale reason
    ctx4 = _mk_ctx(tmp / "s4")
    old_iso = "2026-07-01T09:00:00+09:00"
    payload_old = _sample_payload(old_iso)
    # recorded_at stays "now" so the reference clock is current while the
    # price/board timestamps are old -> event_stale/data_stale reject.
    payload_old["recorded_at"] = now_iso
    norm4 = _stage0_normalize_payload(ctx4, payload_old, 1)
    fresh4 = _stage1_evaluate_freshness(ctx4, norm4)
    ok = (
        fresh4.short_circuit_decision is not None
        and not fresh4.short_circuit_decision.accept
        and bool(fresh4.stale_reason)
        and ctx4.state.stale_reason_counts.get(fresh4.stale_reason, 0) == 1
    )
    results.append(_result("S4", "Stage1 stale payload -> short-circuit stale GateDecision", ok,
                           f"reason={fresh4.stale_reason}"))

    # S5: Stage2 PBv2 decision + internal reason persisted; decision not mutated afterwards
    pbv2 = _stage2_evaluate_pbv2(ctx, norm)
    dec_snapshot = (pbv2.decision.accept, str(pbv2.decision.reason or ""))
    cluster = _stage3_cluster_decision(norm, pbv2)
    final = _stage4_finalize_decision(ctx, norm, fresh, pbv2)
    dec_after = (pbv2.decision.accept, str(pbv2.decision.reason or ""))
    ok = (
        isinstance(pbv2, Stage2PBv2Result)
        and dec_snapshot == dec_after
        and (pbv2.decision.accept or pbv2.internal_reason == str(pbv2.decision.reason or ""))
        and (pbv2.decision.accept or norm.trade.get("pbv2_internal_reason") == pbv2.internal_reason)
    )
    results.append(_result("S5", "Stage2 PBv2 GateDecision immutable + internal reason persisted", ok,
                           f"decision={dec_after} internal={pbv2.internal_reason!r}"))

    # S6: Stage3 classification is read-only and returns frozen dataclass
    ok = isinstance(cluster, Stage3ClusterDecision) and cluster.status in (
        "PASS", "REJECT", "FEATURE_INCOMPLETE", "EXCEPTION", CLUSTER_STAGE_NOT_EVALUATED,
    )
    try:
        object.__setattr__  # noqa: B018
        mutable = False
        try:
            cluster.status = "X"  # type: ignore[misc]
            mutable = True
        except Exception:
            mutable = False
    except Exception:
        mutable = False
    results.append(_result("S6", "Stage3 ClusterDecision frozen dataclass", ok and not mutable,
                           f"status={cluster.status} mutable={mutable}"))

    # S7: Stage4 never rewrites pbv2 internal reason; final reject reason recorded
    ok = (
        isinstance(final, Stage4FinalEntryDecision)
        and norm.trade.get("pbv2_internal_reason", "") == pbv2.internal_reason
        and (final.decision.accept or norm.trade.get("final_reject_reason") == str(final.decision.reason or ""))
    )
    results.append(_result("S7", "Stage4 preserves pbv2_internal_reason + sets final_reject_reason", ok,
                           f"route={final.entry_route} final={final.final_reject_reason!r}"))

    # S8: full orchestrator end-to-end writes exactly one candidate event; trace logger off by default
    ctx8 = _mk_ctx(tmp / "s8")
    n_before = len(ctx8.state.events)
    _process_push_payload(ctx8, _sample_payload(now_iso), 1, t0_push_received_at=now_iso)
    cands = [e for e in ctx8.state.events if e.get("event_type") == "candidate"]
    trace_off = not stage_trace_enabled()
    tl = StageTraceLogger(symbol="6976.T", msg_i=1)
    tl.start("stage0_payload_normalize")
    tl.end("stage0_payload_normalize")
    ok = len(cands) == 1 and len(ctx8.state.events) > n_before and trace_off and len(tl.records) == 0
    results.append(_result("S8", "Orchestrator e2e (1 candidate event) + trace no-op by default", ok,
                           f"candidates={len(cands)} events={len(ctx8.state.events)} trace_off={trace_off}"))

    # S9: trace logger records start/end when enabled via env
    import os

    os.environ["ENTRY_PIPELINE_STAGE_TRACE"] = "1"
    try:
        tl2 = StageTraceLogger(symbol="6976.T", msg_i=2)
        tl2.start("stage1_freshness")
        tl2.end("stage1_freshness")
        ok = len(tl2.records) == 2 and tl2.records[0]["phase"] == "start" and tl2.records[1]["phase"] == "end"
    finally:
        os.environ.pop("ENTRY_PIPELINE_STAGE_TRACE", None)
    results.append(_result("S9", "StageTraceLogger records start/end under DEBUG flag", ok,
                           f"records={len(tl2.records)}"))

    shutil.rmtree(tmp, ignore_errors=True)
    n_pass = sum(1 for r in results if r["passed"])
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "phase629_stage_tests.json").write_text(
        json.dumps({"passed": n_pass, "total": len(results), "results": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"STAGE_TESTS {n_pass}/{len(results)} PASS", flush=True)
    return 0 if n_pass == len(results) else 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if cmd == "stagetest":
        return cmd_stagetest()
    if cmd == "compare":
        tag_a = sys.argv[2] if len(sys.argv) > 2 else "baseline"
        tag_b = sys.argv[3] if len(sys.argv) > 3 else "after"
        return cmd_compare(tag_a, tag_b)
    print(f"unknown cmd: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
