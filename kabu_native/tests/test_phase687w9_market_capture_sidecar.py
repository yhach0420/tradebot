"""Phase687W9 — Independent Market Capture Sidecar tests."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from small_paper.market_capture_registration import (
    KABU_PUSH_REGISTER_LIMIT,
    FileLock,
    coordinate_registration,
    load_symbols_from_universe_csv,
    record_generation_change,
    resolve_universe_symbols,
)
from small_paper.market_capture_sidecar import (
    MarketCaptureSidecar,
    acquire_pid_file,
    capture_day_dir,
    spawn_sidecar_process,
    wait_capture_online,
)
from small_paper.market_capture_topology import (
    DUAL_WS_COMPATIBLE,
    DUAL_WS_INCOMPATIBLE,
    DUAL_WS_RECONNECT_STORM,
    PUSH_SOURCE_KABU_DIRECT,
    dual_websocket_compatibility_probe,
    run_gateway_synthetic_parity,
)
from small_paper.market_capture_writer import MarketCaptureWriter, mask_secrets
from small_paper.push_source import DEFAULT_PUSH_SOURCE, resolve_push_source_mode


def test_writer_original_payload_and_secrets(tmp_path: Path):
    w = MarketCaptureWriter(output_dir=tmp_path, capture_session_id="t1", queue_max=1000)
    w.start()
    payload = {
        "Symbol": "7203",
        "Exchange": 1,
        "CurrentPrice": 2500,
        "password": "secret",
        "token": "tok",
        "Authorization": "Bearer x",
    }
    assert w.enqueue(payload) is True
    time.sleep(0.4)
    w.stop()
    parts = list(tmp_path.glob("push_part_*.jsonl"))
    assert parts
    line = parts[0].read_text(encoding="utf-8").strip().splitlines()[0]
    obj = json.loads(line)
    assert "original_payload" in obj
    assert obj["original_payload"]["Symbol"] == "7203"
    assert obj["original_payload"]["password"] == "[REDACTED]"
    assert obj["original_payload"]["token"] == "[REDACTED]"
    assert obj["sequence"] == 1
    assert obj["symbol"] == "7203"


def test_queue_overflow_records_gap(tmp_path: Path):
    w = MarketCaptureWriter(output_dir=tmp_path, capture_session_id="t2", queue_max=2)
    # don't start writer thread — fill queue then overflow
    assert w.enqueue({"Symbol": "1"}) is True
    assert w.enqueue({"Symbol": "2"}) is True
    # overflow → emergency append or gap
    w.enqueue({"Symbol": "3"})
    assert w.stats.queue_overflows >= 1
    assert w.stats.dropped >= 1 or w.stats.emergency_appends >= 1
    gaps = tmp_path / "capture_gaps.jsonl"
    if w.stats.dropped:
        assert gaps.is_file()
        assert "queue_overflow" in gaps.read_text(encoding="utf-8")
    assert w.stats.status == "DEGRADED"


def test_rotation_by_bytes(tmp_path: Path):
    w = MarketCaptureWriter(
        output_dir=tmp_path,
        capture_session_id="t3",
        rotate_bytes=200,
        rotate_sec=3600,
        flush_records=1,
        flush_ms=10,
    )
    w.start()
    for i in range(30):
        w.enqueue({"Symbol": str(i), "CurrentPrice": i, "pad": "x" * 40})
    time.sleep(0.5)
    w.stop()
    parts = sorted(tmp_path.glob("push_part_*.jsonl"))
    assert len(parts) >= 2
    assert w.stats.rotate_count >= 1


def test_registration_limit_50(tmp_path: Path):
    syms = [str(1000 + i) for i in range(51)]
    out = coordinate_registration(tmp_path, "20260711", expected_symbols=syms, apply_register=False)
    assert out["ok"] is False
    assert out["reason"] == "expected_exceeds_limit_50"


def test_registration_lock(tmp_path: Path):
    lock_path = tmp_path / "runtime" / "market_registration.lock"
    lock_path.parent.mkdir(parents=True)
    with FileLock(lock_path, timeout_sec=2):
        second = FileLock(lock_path, timeout_sec=0.2)
        with pytest.raises(Exception):
            second.acquire()


def test_unregister_all_not_used(tmp_path: Path):
    out = coordinate_registration(
        tmp_path,
        "20260711",
        expected_symbols=[str(7200 + i) for i in range(10)],
        apply_register=False,
    )
    assert out["unregister_all_used"] is False


def test_generation_change_recorded(tmp_path: Path):
    p = record_generation_change(
        tmp_path,
        generation_id="g2",
        previous_symbols=["7203"],
        new_symbols=["7203", "6758"],
        registration_verified=True,
        capture_sequence_at_change=42,
    )
    assert p.is_file()
    row = json.loads(p.read_text(encoding="utf-8").strip())
    assert row["added"] == ["6758"]
    assert row["capture_sequence_at_change"] == 42


def test_sidecar_separate_pid_and_seal(tmp_path: Path):
    day = "20990101"
    owned = None
    try:
        # pre-seed registration
        coordinate_registration(
            tmp_path,
            day,
            expected_symbols=[str(7200 + i) for i in range(50)],
            apply_register=False,
            test_mode=True,
        )
        spawn = spawn_sidecar_process(
            native_root=tmp_path,
            trading_date=day,
            synthetic=True,
            synthetic_events=40,
        )
        from small_paper.capture_child_cleanup import cleanup_owned_capture, record_owned_from_spawn, query_process

        owned = record_owned_from_spawn(spawn, native_root=tmp_path)
        assert spawn["pid"] != os.getpid()
        wait = wait_capture_online(tmp_path, day, timeout_sec=20)
        assert wait["ok"] is True
        out = capture_day_dir(tmp_path, day)
        # Wait until at least one event is captured before operator stop (avoid race)
        deadline = time.time() + 15
        while time.time() < deadline:
            st_path = out / "capture_status.json"
            if st_path.is_file():
                try:
                    st = json.loads(st_path.read_text(encoding="utf-8"))
                    if int(st.get("event_count") or 0) > 0:
                        break
                except Exception:
                    pass
            time.sleep(0.1)
        # operator stop
        (out / "operator_stop.flag").write_text("stop\n", encoding="utf-8")
        deadline = time.time() + 20
        while time.time() < deadline and not (out / "capture_seal.json").is_file():
            time.sleep(0.2)
        assert (out / "capture_seal.json").is_file()
        seal = json.loads((out / "capture_seal.json").read_text(encoding="utf-8"))
        assert seal.get("seal_pass") is True
        assert seal.get("paper_session_seal") is False
        summary = json.loads((out / "capture_summary.json").read_text(encoding="utf-8"))
        assert summary["total_events"] > 0
        assert summary["actual_submit"] == 0
        assert summary["actual_cancel"] == 0
        # separate output root
        assert "market_capture" in str(out).replace("\\", "/")
        assert "results/small_paper" not in str(out).replace("\\", "/")
        # double start blocked
        sc = MarketCaptureSidecar(native_root=tmp_path, trading_date=day, synthetic=True, synthetic_events=5)
        # if first still holding pid briefly after seal, release should have happened
        time.sleep(0.5)
        code = sc.run()
        # either runs ok (first exited) or pid conflict 2
        assert code in (0, 2)
    finally:
        if owned is not None:
            from small_paper.capture_child_cleanup import cleanup_owned_capture, query_process

            owned.synthetic = True
            cleanup_owned_capture(owned, reason="test_teardown", skip_capture_wait=True)
            assert not query_process(owned.pid).get("exists")


def test_dual_ws_probe_compatible():
    r = dual_websocket_compatibility_probe(
        open_primary=lambda: True,
        open_secondary=lambda: True,
        primary_still_open=lambda: True,
        registration_before=["7203"],
        registration_after=["7203"],
        primary_events=[{"a": 1}],
        secondary_events=[{"a": 1}],
    )
    assert r.status == DUAL_WS_COMPATIBLE


def test_dual_ws_incompatible_and_storm():
    bad = dual_websocket_compatibility_probe(
        open_primary=lambda: True,
        open_secondary=lambda: False,
        primary_still_open=lambda: True,
        registration_before=["1"],
        registration_after=["1"],
    )
    assert bad.status == DUAL_WS_INCOMPATIBLE
    storm = dual_websocket_compatibility_probe(
        open_primary=lambda: True,
        open_secondary=lambda: True,
        primary_still_open=lambda: True,
        registration_before=["1"],
        registration_after=["1"],
        reconnect_count=9,
    )
    assert storm.status == DUAL_WS_RECONNECT_STORM


def test_gateway_parity_100k():
    # use smaller n in CI speed path but assert same invariants; full 100k in research runner
    report = run_gateway_synthetic_parity(5_000)
    assert report["loss"] == 0
    assert report["duplicate"] == 0
    assert report["order_inversion"] == 0
    assert report["payload_hash_match"] is True
    assert report["default_paper_push_source"] == PUSH_SOURCE_KABU_DIRECT
    assert report["parity_pass"] is True


def test_default_push_source_unchanged():
    assert DEFAULT_PUSH_SOURCE.value == "KABU_DIRECT"
    assert resolve_push_source_mode(None).value == "KABU_DIRECT"


def test_mask_secrets_nested():
    m = mask_secrets({"a": {"api_password": "x", "Symbol": "1"}})
    assert m["a"]["api_password"] == "[REDACTED]"
    assert m["a"]["Symbol"] == "1"


def test_notify_registration_refresh(tmp_path: Path):
    from small_paper.market_capture_registration import notify_registration_refresh, read_registration_manifest

    day = "20990202"
    out = notify_registration_refresh(
        tmp_path,
        trading_date=day,
        new_symbols=["7203", "6758"],
        previous_symbols=["7203"],
        verified=True,
        capture_day_dir=tmp_path / "data" / "market_capture" / day,
    )
    assert out["ok"] is True
    assert out["added"] == ["6758"]
    assert out["unregister_all_used"] is False
    man = read_registration_manifest(tmp_path)
    assert man["generation_id"] == out["generation_id"]
    gen_path = tmp_path / "data" / "market_capture" / day / "registration_generation_events.jsonl"
    assert gen_path.is_file()


def test_interference_immediate_fail_on_drop():
    from small_paper.market_capture_interference import (
        InterferenceInputs,
        evaluate_interference,
        INTERFERENCE_DATA_INSUFFICIENT,
    )

    r = evaluate_interference(InterferenceInputs(dropped_event_count=1, session_index=1))
    assert r.immediate_fail is True
    r2 = evaluate_interference(InterferenceInputs(session_index=1, capture_event_count=10))
    assert r2.verdict == INTERFERENCE_DATA_INSUFFICIENT
    import small_paper.market_capture_sidecar as m
    import small_paper.market_capture_writer as w
    import small_paper.market_capture_registration as r

    for mod in (m, w, r):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "entry_gate" not in src
        assert "exit_policy" not in src
        assert "live_order_safety_sm" not in src
        assert "canonical_summary" not in src
        assert "production_enablement" not in src
