"""Phase 1 USDJPY historical adapter runner. No alpha, no stock returns, no PB1 bind."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research.causal_driver_pb1 import (
    C1_FIRST,
    C1_LAST,
    DEV_FIRST,
    DEV_LAST,
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_V4_MACHINE_SHA256,
)
from research.causal_driver_pb1.contracts.enums import AccessKind, DatasetRole, QualityStatus
from research.causal_driver_pb1.contracts.source import SourceIdentity
from research.causal_driver_pb1.datasets.firewall import ECONOMIC_FIELDS, AccessLedger, request_dataset
from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.identity.pin import bind_identities, contract_schema_sha256, frozen_source_hashes
from research.causal_driver_pb1.phase1 import (
    ALLOWED_JST_FIRST,
    ALLOWED_JST_LAST,
    CASE_BLOCKED,
    CASE_READY,
    DRIVER_FAMILY_ID,
    EXPECTED_PHASE0_CONTRACT_SCHEMA_SHA256,
    EXPECTED_PHASE0_DATASET_ROLE_CONFIG_SHA256,
    EXPECTED_PHASE0_FIREWALL_CONFIG_SHA256,
    EXPECTED_PHASE0_VERDICT,
    INSTRUMENT,
    MID_IS_RAW_SOURCE,
    NATIVE_TIMEZONE,
    NEXT_PRECOMMIT_PHASE2,
    NEXT_RESOLVE,
    PHASE1_RUN_MODE_LABEL,
    RESOLUTION,
    SOURCE_ID,
    SOURCE_PROVIDER,
    TIMESTAMP_SEMANTICS,
    UTC_FILE_FIRST,
    UTC_FILE_LAST,
    VALUE_SEMANTICS,
)
from research.causal_driver_pb1.phase1.bars import load_and_pair
from research.causal_driver_pb1.phase1.checks import bidask_checks, gap_checks, restricted_date_checks, timestamp_checks
from research.causal_driver_pb1.phase1.contamination import contamination_ledger
from research.causal_driver_pb1.phase1.coverage import japan_session_coverage
from research.causal_driver_pb1.phase1.dates import yyyymmdd
from research.causal_driver_pb1.phase1.errors import UsdJpySourceUnavailable
from research.causal_driver_pb1.phase1.firewall import (
    phase1_firewall_overlay_sha256,
    phase1_run_mode,
    prove_restricted_dates,
    request_usdjpy_ingest,
)
from research.causal_driver_pb1.phase1.gaps import classify_gaps
from research.causal_driver_pb1.phase1.isolation import CACHE, LEGACY_JETTA, PHASE0_OUT
from research.causal_driver_pb1.phase1.jetta import (
    JETTA,
    JETTA_INSTRUMENT,
    ensure_utc_files,
    inventory_rows,
    inventory_sha256,
    payload_schema_keys,
)
from research.causal_driver_pb1.phase1.map_obs import map_bar, map_bars, observation_stream_hash
from research.causal_driver_pb1.phase1.transport import UsdJpyHistoricalDriverTransport, assert_transport_protocol
from research.causal_driver_pb1.transport.synthetic import HistoricalDriverTransport as Phase0HistoricalDriverTransport


FORBIDDEN_SOURCE_TOKENS = (
    "future_return",
    "future_return_bps",
    "net_pnl",
    "gross_pnl",
    "profit_factor",
    "mfe_bps",
    "mae_bps",
    "complete_strategy_economics",
    "trade_outcome",
    "USDJPY_RET_1M",
    "USDJPY_RET_3M",
    "USDJPY_RET_5M",
    "USDJPY_RET_180S",
)


def _ok(name: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "pass": True, **extra}


def _fail(name: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "pass": False, "reason": reason, **extra}


def _phase1_source_scan() -> dict[str, Any]:
    root = Path(__file__).resolve().parent
    hits: list[dict[str, str]] = []
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for tok in FORBIDDEN_SOURCE_TOKENS:
            if tok in text and path.name != "evaluate.py":
                hits.append({"file": path.name, "token": tok})
            if tok in text and path.name == "evaluate.py" and tok in FORBIDDEN_SOURCE_TOKENS:
                # evaluate.py lists the tokens as the forbidden scan set; that mention is the scanner.
                if "FORBIDDEN_SOURCE_TOKENS" not in text:
                    hits.append({"file": path.name, "token": tok})
    # evaluate.py contains the token list by design; ignore those.
    hits = [h for h in hits if h["file"] != "evaluate.py"]
    return {"pass": not hits, "hits": hits, "economic_data_read_n": 0 if not hits else len(hits)}


def _economic_ledger_reads(ledger: AccessLedger) -> int:
    n = 0
    for ev in ledger.events:
        fields = [str(x).lower() for x in (ev.get("requested_fields") or [])]
        if any(f in ECONOMIC_FIELDS for f in fields) and ev.get("allowed"):
            n += 1
        if ev.get("access_kind") == AccessKind.ECONOMIC_PAYLOAD.value and ev.get("allowed"):
            n += 1
    return n


def _quality_counts(bars) -> dict[str, int]:
    out = {s.value: 0 for s in QualityStatus}
    for b in bars:
        out[b.quality_status.value] = out.get(b.quality_status.value, 0) + 1
    return out


def evaluate() -> dict[str, Any]:
    blockers: list[str] = []
    identity = bind_identities()
    pre_hashes = frozen_source_hashes()
    contract_sha = contract_schema_sha256()
    if contract_sha != EXPECTED_PHASE0_CONTRACT_SCHEMA_SHA256:
        blockers.append("PHASE0_CONTRACT_SCHEMA_CHANGED")
    if identity.get("firewall_config_sha256") != EXPECTED_PHASE0_FIREWALL_CONFIG_SHA256:
        blockers.append("PHASE0_FIREWALL_CONFIG_CHANGED")
    if identity.get("dataset_role_config_sha256") != EXPECTED_PHASE0_DATASET_ROLE_CONFIG_SHA256:
        blockers.append("PHASE0_DATASET_ROLE_CONFIG_CHANGED")
    if identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256:
        blockers.append("V4_CHANGED")
    if identity.get("COMPLETE_STRATEGY_SHA256") != EXPECTED_COMPLETE_STRATEGY_SHA256:
        blockers.append("COMPLETE_STRATEGY_CHANGED")
    phase0_report = PHASE0_OUT / "report.json"
    phase0_verdict = ""
    if phase0_report.is_file():
        import json

        phase0_verdict = str((json.loads(phase0_report.read_text(encoding="utf-8")).get("answers") or {}).get("VERDICT") or "")
    if phase0_verdict != EXPECTED_PHASE0_VERDICT:
        blockers.append("PHASE0_NOT_READY")

    uni = load_research_observation_universe()
    ledger = AccessLedger(run_id="PHASE1_USDJPY_DRIVER_INGEST", run_mode=phase1_run_mode())
    request_dataset(
        ledger=ledger,
        run_mode=phase1_run_mode(),
        dataset_id="RESEARCH_OBSERVATION_UNIVERSE_105",
        dataset_role=DatasetRole.DEVELOPMENT,
        requested_fields=("symbol",),
        access_kind=AccessKind.METADATA,
    )
    request_usdjpy_ingest(ledger=ledger, session_yyyymmdd=DEV_FIRST)
    request_usdjpy_ingest(ledger=ledger, session_yyyymmdd=C1_FIRST)
    fw_restricted = prove_restricted_dates(ledger)
    if not fw_restricted.get("pass"):
        blockers.append("FIREWALL_RESTRICTED_DATE")

    ts_checks = timestamp_checks()
    ba_checks = bidask_checks()
    gp_checks = gap_checks()
    rd_checks = restricted_date_checks()
    if not ts_checks["pass"]:
        blockers.append("TIMESTAMP_SEMANTICS")
    if not ba_checks["pass"]:
        blockers.append("BIDASK")
    if not gp_checks["pass"]:
        blockers.append("GAP_POLICY")
    if not rd_checks["pass"]:
        blockers.append("RESTRICTED_DATE")

    source_unavailable = False
    ingest_meta: dict[str, Any] = {}
    try:
        ingest_meta = ensure_utc_files(start=UTC_FILE_FIRST, end=UTC_FILE_LAST)
    except UsdJpySourceUnavailable as exc:
        source_unavailable = True
        ingest_meta = {"ok": False, "reason": str(exc), "source_substituted": False}
        blockers.append("USDJPY_SOURCE_UNAVAILABLE")

    inv = inventory_rows(start=UTC_FILE_FIRST, end=UTC_FILE_LAST) if not source_unavailable else []
    inv_sha = inventory_sha256(inv) if inv else "0" * 64
    missing_files = [r for r in inv if not r.get("exists")]
    if missing_files:
        blockers.append("RAW_FILE_MISSING")
        source_unavailable = True

    from datetime import date as date_cls

    schema_keys = payload_schema_keys(date_cls(2024, 9, 17), "BID") if not source_unavailable else []
    packed: dict[str, Any] = {
        "bars": [],
        "raw_bid_n": 0,
        "raw_ask_n": 0,
        "paired_bar_n": 0,
        "duplicate_n": 0,
        "identical_duplicate_n": 0,
        "conflicting_duplicate_n": 0,
    }
    if not source_unavailable:
        packed = load_and_pair(utc_first=UTC_FILE_FIRST, utc_last=UTC_FILE_LAST, source_id=SOURCE_ID)
    bars = packed.get("bars") or []
    if int(packed.get("conflicting_duplicate_n") or 0) > 0:
        blockers.append("CONFLICTING_DUPLICATE")

    source = SourceIdentity(
        source_id=SOURCE_ID,
        provider=SOURCE_PROVIDER,
        instrument=INSTRUMENT,
        resolution=RESOLUTION,
        timezone=NATIVE_TIMEZONE,
        timestamp_semantics=TIMESTAMP_SEMANTICS,
        source_path_or_endpoint_identity=f"{JETTA}/candles/minute/{JETTA_INSTRUMENT}/{{BID|ASK}}/Y/M/D",
        inventory_sha256=inv_sha,
    )
    obs = map_bars(bars, source) if bars else []
    hash1 = observation_stream_hash(obs)
    hash2 = observation_stream_hash(map_bar(b, source) for b in bars) if bars else hash1
    determinism_pass = hash1 == hash2
    if not determinism_pass:
        blockers.append("DETERMINISM")

    transport = UsdJpyHistoricalDriverTransport(obs)
    stream_n = 0
    order_ok = True
    naive = False
    prev_key = None
    from research.causal_driver_pb1.transport.order import sort_key as _sort_key

    for o in transport.stream():
        if stream_n == 0 and (o.event_time.tzinfo is None or o.available_at.tzinfo is None):
            naive = True
        key = _sort_key(o)
        if prev_key is not None and key < prev_key:
            order_ok = False
        prev_key = key
        stream_n += 1
    interface_ok = assert_transport_protocol(transport) and assert_transport_protocol(Phase0HistoricalDriverTransport([]))
    if stream_n != len(obs):
        blockers.append("TRANSPORT_COUNT")
    if not order_ok:
        blockers.append("TRANSPORT_ORDER")
    if naive:
        blockers.append("NAIVE_DATETIME")

    gaps = classify_gaps(bars) if bars else {"unexpected_gap_n": 0, "expected_closed_gap_n": 0, "unexpected_examples": []}
    cov = japan_session_coverage(bars) if bars else {"coverage_days": 0, "coverage_min": None, "coverage_median": None, "coverage_p05": None, "days_below_99": 0, "rows": []}

    jst_dates = sorted({b.jst_date for b in bars})
    source_start = jst_dates[0] if jst_dates else ""
    source_end = jst_dates[-1] if jst_dates else ""
    dev_n = sum(1 for b in bars if DEV_FIRST <= b.jst_date <= DEV_LAST and b.quality_status == QualityStatus.VALID)
    c1_n = sum(1 for b in bars if C1_FIRST <= b.jst_date <= C1_LAST and b.quality_status == QualityStatus.VALID)
    fv_opened = any(b.jst_date >= "20260422" for b in bars)
    prospective_opened = any(b.jst_date >= "20260924" for b in bars)
    if fv_opened:
        blockers.append("FROZEN_VALIDATION_OPENED")
    if prospective_opened:
        blockers.append("PROSPECTIVE_DATA_OPENED")
    if not source_unavailable:
        if source_start != ALLOWED_JST_FIRST:
            # first kept JST date should be Development start; extra pre-window must already be dropped
            if not source_start or source_start < ALLOWED_JST_FIRST:
                blockers.append("SOURCE_START_BEFORE_ALLOWED")
        if source_end and source_end > ALLOWED_JST_LAST:
            blockers.append("SOURCE_END_AFTER_ALLOWED")
        if dev_n < 300_000:
            blockers.append("DEVELOPMENT_COVERAGE_INSUFFICIENT")
        if c1_n < 100_000:
            blockers.append("C1_COVERAGE_INSUFFICIENT")
        if source_end and source_end < C1_LAST:
            blockers.append("C1_RANGE_INCOMPLETE")

    scan = _phase1_source_scan()
    econ_n = _economic_ledger_reads(ledger)
    if not scan["pass"]:
        blockers.append("ECONOMIC_TOKEN_IN_ADAPTER")
    if econ_n != 0:
        blockers.append("ECONOMIC_DATA_READ")

    post_hashes = frozen_source_hashes()
    runtime_rows = [
        _ok("v4_sha", sha=identity.get("V4_MACHINE_SHA256"))
        if identity.get("V4_MACHINE_SHA256") == EXPECTED_V4_MACHINE_SHA256
        else _fail("v4_sha", "mismatch"),
        _ok("cs_sha", sha=identity.get("COMPLETE_STRATEGY_SHA256"))
        if identity.get("COMPLETE_STRATEGY_SHA256") == EXPECTED_COMPLETE_STRATEGY_SHA256
        else _fail("cs_sha", "mismatch"),
        _ok("paper_runner_unchanged")
        if pre_hashes.get("paper_trade_checked_runner") == post_hashes.get("paper_trade_checked_runner")
        else _fail("paper_runner_unchanged", "changed"),
        _ok("dynamic40_unchanged")
        if pre_hashes.get("core10_dynamic40") == post_hashes.get("core10_dynamic40")
        else _fail("dynamic40_unchanged", "changed"),
        _ok("v4_tree_unchanged") if pre_hashes.get("v4") == post_hashes.get("v4") else _fail("v4_tree_unchanged", "changed"),
        _ok("cs_tree_unchanged")
        if pre_hashes.get("complete_strategy") == post_hashes.get("complete_strategy")
        else _fail("cs_tree_unchanged", "changed"),
        _ok("phase0_contracts_unchanged")
        if contract_sha == EXPECTED_PHASE0_CONTRACT_SCHEMA_SHA256
        else _fail("phase0_contracts_unchanged", contract_sha),
    ]
    runtime_pass = all(r.get("pass") for r in runtime_rows)
    if not runtime_pass:
        blockers.append("RUNTIME_NONIMPACT")

    quality = _quality_counts(bars)
    contam = contamination_ledger()
    normalized_schema = {
        "UsdJpyNormalizedBar": [
            "bar_start",
            "available_at",
            "bid_open",
            "bid_high",
            "bid_low",
            "bid_close",
            "ask_open",
            "ask_high",
            "ask_low",
            "ask_close",
            "source_id",
            "quality_status",
            "mid_close_derived",
            "spread_close",
        ],
        "DriverObservation.value": VALUE_SEMANTICS,
        "MID_IS_RAW_SOURCE": MID_IS_RAW_SOURCE,
        "direction": "NEUTRAL",
        "native_json_keys": schema_keys,
    }
    normalized_schema_sha = sha256_obj(normalized_schema)

    sample = None
    if bars:
        sample_bar = next((b for b in bars if b.bar_start.hour == 9 and b.bar_start.minute == 30 and b.quality_status == QualityStatus.VALID), bars[0])
        sample = {
            "bar_start": sample_bar.bar_start.isoformat(),
            "available_at": sample_bar.available_at.isoformat(),
            "source_timestamp": sample_bar.source_timestamp.isoformat(),
            "bid_close": sample_bar.bid_close,
            "ask_close": sample_bar.ask_close,
            "mid_close": sample_bar.mid_close,
            "spread": sample_bar.spread_close,
            "quality": sample_bar.quality_status.value,
        }

    firewall_pass = bool(fw_restricted.get("pass")) and econ_n == 0 and not fv_opened and not prospective_opened
    timestamp_pass = bool(ts_checks.get("pass"))
    bidask_pass = bool(ba_checks.get("pass"))
    if not interface_ok:
        blockers.append("TRANSPORT_INTERFACE")

    unique_blockers = list(dict.fromkeys(blockers))
    ok = not unique_blockers
    verdict = CASE_READY if ok else CASE_BLOCKED
    nxt = NEXT_PRECOMMIT_PHASE2 if ok else NEXT_RESOLVE

    return {
        "ok": ok,
        "VERDICT": verdict,
        "NEXT": nxt,
        "blockers": unique_blockers,
        "run_mode": PHASE1_RUN_MODE_LABEL,
        "phase0_firewall_run_mode": phase1_run_mode().value,
        "source_provider": SOURCE_PROVIDER,
        "source_id": SOURCE_ID,
        "instrument": INSTRUMENT,
        "resolution": RESOLUTION,
        "source_start": source_start,
        "source_end": source_end,
        "native_timezone": NATIVE_TIMEZONE,
        "canonical_timezone": "Asia/Tokyo",
        "timestamp_semantics": TIMESTAMP_SEMANTICS,
        "raw_bid_n": int(packed.get("raw_bid_n") or 0),
        "raw_ask_n": int(packed.get("raw_ask_n") or 0),
        "paired_bar_n": int(packed.get("paired_bar_n") or 0),
        "kept_bar_n": int(packed.get("kept_bar_n") or len(bars)),
        "duplicate_n": int(packed.get("duplicate_n") or 0),
        "identical_duplicate_n": int(packed.get("identical_duplicate_n") or 0),
        "conflicting_duplicate_n": int(packed.get("conflicting_duplicate_n") or 0),
        "unexpected_gap_n": int(gaps.get("unexpected_gap_n") or 0),
        "expected_closed_gap_n": int(gaps.get("expected_closed_gap_n") or 0),
        "coverage_days": cov.get("coverage_days"),
        "coverage_p05": cov.get("coverage_p05"),
        "coverage_median": cov.get("coverage_median"),
        "coverage_min": cov.get("coverage_min"),
        "days_below_99": cov.get("days_below_99"),
        "driver_observation_n": len(obs),
        "determinism_pass": determinism_pass,
        "timestamp_pass": timestamp_pass,
        "bidask_pass": bidask_pass,
        "firewall_pass": firewall_pass,
        "runtime_nonimpact_pass": runtime_pass,
        "economic_data_read_n": econ_n,
        "FROZEN_VALIDATION_OPENED": fv_opened,
        "PROSPECTIVE_DATA_OPENED": prospective_opened,
        "V4_CHANGED": identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "identity": identity,
        "universe105_count": uni.symbol_count,
        "ingest": ingest_meta,
        "inventory_sha256": inv_sha,
        "inventory_file_n": len(inv),
        "inventory_bytes": int(sum(int(r.get("bytes") or 0) for r in inv)),
        "legacy_jetta_root": str(LEGACY_JETTA),
        "phase1_cache": str(CACHE / "jetta" / "candles_minute"),
        "raw_inventory": inv,
        "schema": normalized_schema,
        "normalized_schema_sha256": normalized_schema_sha,
        "sample_bar": sample,
        "quality_counts": quality,
        "dev_paired_n": dev_n,
        "c1_paired_n": c1_n,
        "gaps": {k: v for k, v in gaps.items() if k != "unexpected_examples" or True},
        "coverage": cov,
        "timestamp": ts_checks,
        "bidask": ba_checks,
        "gap_tests": gp_checks,
        "restricted_dates": rd_checks,
        "firewall": fw_restricted,
        "ledger_events": ledger.events,
        "transport": {
            "pass": interface_ok and stream_n == len(obs),
            "interface": "DriverTransport",
            "phase0_historical_connected": True,
            "stream_n": stream_n,
            "order": ["available_at", "event_time", "source_id", "driver_observation_id"],
        },
        "determinism": {
            "pass": determinism_pass,
            "output_hash": hash1,
            "replay_hash": hash2,
            "observation_n": len(obs),
        },
        "runtime_nonimpact": {"pass": runtime_pass, "rows": runtime_rows},
        "source_scan": scan,
        "contamination": contam,
        "phase1_firewall_overlay_sha256": phase1_firewall_overlay_sha256(),
        "source_fingerprint": source.fingerprint(),
        "driver_family_id": DRIVER_FAMILY_ID,
        "MID_IS_RAW_SOURCE": MID_IS_RAW_SOURCE,
        "value_semantics": VALUE_SEMANTICS,
        "USDJPY_ALPHA_TESTED": False,
        "SECTOR_RESPONSE_TESTED": False,
        "SYMBOL_RESPONSE_TESTED": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "research_only": True,
        "yyyymmdd_helper": yyyymmdd(DEV_FIRST),
    }
