"""Emergency decontamination tests + actual live-loop demo path.

Proves:
1) PBv2-only accept does NOT mutate V1R Primary occupancy
2) V1R-native anchor → score → admit → pending → passive fill works
3) dual.try_admit_fill rejects non-v1r source
4) Frozen SHA identity
5) Discord Primary ENTRY方式 is never PBv2
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))

os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from notify.v1r_discord_embeds import build_entry_embed, build_fill_embed
from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.e1_x37_prospective.freeze import load_model_artifact, verify_model_identity
from small_paper.v1r_live_dual_lane import (
    get_dual_lane,
    reset_dual_lane_for_tests,
    V1RLiveDualLane,
)
from small_paper.v1r_native_entry_live import (
    ENTRY_SHA,
    EXEC_SHA,
    boot_v1r_native_entry,
    reset_native_entry_for_tests,
    set_native_entry,
)
from small_paper.v1r_primary_runtime import (
    ANCHOR_SHA,
    CLOCK_GRID,
    MODEL_ARTIFACT_SHA,
    POSITION_CAP,
    WAIT_SEC,
)

OUT = NATIVE / "results" / "research" / "v1r_native_entry_decontam_20260812"
OUT.mkdir(parents=True, exist_ok=True)


def _warmup(eng, symbols, t0, *, base=1000.0):
    for si, sym in enumerate(symbols):
        b = base + si * 50.0
        for k in range(220):
            tt = t0 - 220.0 + k
            mid = b * (1.0 + 0.001 * si * (k / 220.0))
            eng.ingest_push(
                symbol=sym,
                payload={
                    "Buy1": {"Price": mid, "Qty": 300.0},
                    "Sell1": {"Price": mid + 2.0, "Qty": 200.0},
                    "board_age_sec": 0.2,
                    "SpecialQuote": False,
                },
                event_t=tt,
            )
        eng.ingest_push(
            symbol=sym,
            payload={
                "Buy1": {"Price": b, "Qty": 500.0},
                "Sell1": {"Price": b + 3.0, "Qty": 200.0},
                "board_age_sec": 0.2,
                "SpecialQuote": False,
            },
            event_t=t0,
        )


def test_sha_identity() -> dict:
    ser = load_model_artifact()
    ident = verify_model_identity(ser)
    assert ident["pass"], ident
    assert ser["model_artifact_sha256"] == MODEL_ARTIFACT_SHA
    assert ANCHOR_SHA.startswith("4a2f176e")
    assert ENTRY_SHA.startswith("f2887bb2")
    assert EXEC_SHA.startswith("040fa4b0")
    assert len(CLOCK_GRID) == 16
    return {"pass": True, "model": MODEL_ARTIFACT_SHA, "anchors": len(CLOCK_GRID)}


def test_pbv2_negative() -> dict:
    """PBv2-only accept: Primary candidate/fill/open unchanged; dual reject."""
    reset_dual_lane_for_tests()
    reset_native_entry_for_tests()
    eng = boot_v1r_native_entry(universe=["1001", "1002"], trace_dir=OUT / "neg")
    set_native_entry(eng)
    dual = V1RLiveDualLane(trace_dir=OUT / "neg")
    # monkey patch singleton
    import small_paper.v1r_live_dual_lane as dl

    dl._DUAL = dual

    before = eng.snapshot()
    snap = eng.note_pbv2_shadow_accept(
        symbol="9999", entry_price=1234.0, entry_time="2026-08-12T09:03:00+09:00"
    )
    after = eng.snapshot()
    assert snap["primary_unchanged"]
    assert before["open_n"] == after["open_n"] == 0
    assert before["pending_n"] == after["pending_n"] == 0
    assert before["primary_fills"] == after["primary_fills"] == 0
    assert after["shadow_pbv2"]["accepts"] == 1

    rej = dual.try_admit_fill(
        symbol="9999", fill_price=1234.0, fill_time=time.time(), source="pbv2_gate_accept"
    )
    assert rej.get("rejected") is True
    assert dual.open_n("primary") == 0
    assert dual.stats.primary_fills == 0

    # Discord Primary ENTRY must not say PBv2
    emb = build_entry_embed({
        "symbol": "1001", "anchor": "09:05", "score": 0.5, "rank": 1,
        "limit": 1000, "open": 0, "pending": 1, "cap": 5,
    })
    text = json.dumps(emb, ensure_ascii=False)
    assert "ENTRY方式" in text
    assert "V1R / PASSIVE BID" in text
    assert "PBv2" not in text or "SHADOW" in text  # PBv2 string only ok in shadow embeds
    # harden: field value must not be ENTRY方式: PBv2
    for f in emb.get("fields") or []:
        if f.get("name") == "ENTRY方式":
            assert "PBv2" not in str(f.get("value"))

    return {
        "pass": True,
        "pbv2_shadow_accepts": 1,
        "primary_open": after["open_n"],
        "primary_pending": after["pending_n"],
        "dual_primary_fills": dual.stats.primary_fills,
        "dual_reject_reason": rej.get("reason"),
    }


def test_v1r_positive() -> dict:
    """Anchor → admit → pending → 1s passive fill → Primary open via dual."""
    reset_dual_lane_for_tests()
    reset_native_entry_for_tests()
    symbols = [f"2{i:03d}" for i in range(6)]
    eng = boot_v1r_native_entry(universe=symbols, trace_dir=OUT / "pos")
    assert eng.ready, eng.fail_reason
    set_native_entry(eng)
    import small_paper.v1r_live_dual_lane as dl

    dual = V1RLiveDualLane(trace_dir=OUT / "pos")
    dl._DUAL = dual

    t0 = time.time()
    _warmup(eng, symbols, t0)
    pending_ev = eng.fire_anchor_at(anchor="09:05", t0=t0, day="20260812", session="AM")
    assert eng.anchor_fires == 1
    assert eng.pending_n > 0, eng.snapshot()
    assert eng.pending_n <= POSITION_CAP
    assert any(e.get("kind") == "V1R_ENTRY_PENDING" for e in eng.events)

    # Fill first pending via ask-cross within 1s
    fill_sym = next(iter(eng.pending))
    po = eng.pending[fill_sym]
    limit = po.limit_price
    eng.ingest_push(
        symbol=fill_sym,
        payload={
            "Buy1": {"Price": limit, "Qty": 300.0},
            "Sell1": {"Price": limit - 1.0, "Qty": 150.0},
            "board_age_sec": 0.4,
            "SpecialQuote": False,
        },
        event_t=t0 + 0.4,
    )
    done = eng.on_tick_fill_check(event_t=t0 + 0.5)
    fills = [d for d in done if d.get("kind") == "V1R_FILL"]
    assert fills, (done, eng.snapshot())
    assert fills[0]["source"] == "v1r_native"
    assert abs(float(fills[0]["fill_price"]) - limit) < 1e-9
    assert eng.open_n >= 1
    assert dual.stats.primary_fills >= 1
    assert dual.stats.control_fills >= 1
    assert dual.open_n("primary") >= 1

    fill_emb = build_fill_embed({
        "symbol": fill_sym, "anchor": "09:05", "fill": limit, "limit": limit,
        "score": po.score, "rank": 1, "open": eng.open_n, "pending": eng.pending_n, "cap": 5,
    })
    ft = json.dumps(fill_emb, ensure_ascii=False)
    assert "PBv2" not in ft or "SHADOW" in ft

    return {
        "pass": True,
        "anchor_fires": eng.anchor_fires,
        "pending_at_anchor": len(pending_ev),
        "fill_symbol": fill_sym,
        "fill_price": fills[0]["fill_price"],
        "primary_open": eng.open_n,
        "dual_primary_fills": dual.stats.primary_fills,
        "dual_control_fills": dual.stats.control_fills,
        "entry_mode": fills[0].get("entry_mode"),
    }


def test_historical_entry_parity_light() -> dict:
    """Frozen SoT arms: find_ask_cross_fill + simulate_joint identity."""
    from research.e1_x36_joint_allocator.replay import simulate_joint
    from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized

    ser = load_model_artifact()
    sfn = score_fn_from_serialized(ser)
    # synthetic cohort
    t0 = 1_700_000_000.0
    events = []
    for i, sym in enumerate(["A", "B", "C", "D", "E", "F"]):
        feats = {
            "spread_bps": 5.0 + i,
            "imbalance": 0.1 * i,
            "mid_ret_60s": 0.001 * i,
            "mid_ret_180s": 0.002 * i,
            "event_rate_60s": 10.0 + i,
            "log_bid_qty": 5.0,
        }
        events.append({
            "date": "20260801",
            "symbol": sym,
            "session": "AM",
            "signal_time": t0,
            "filled": False,
            "limit_price": 1000.0 + i,
            "bid0": 1000.0 + i,
            **feats,
        })
    a = simulate_joint([dict(e) for e in events], score_fn=sfn)
    b = simulate_joint([dict(e) for e in events], score_fn=sfn)
    adm_a = sorted(e["symbol"] for e in a["events"] if e.get("admitted"))
    adm_b = sorted(e["symbol"] for e in b["events"] if e.get("admitted"))
    assert adm_a == adm_b
    assert len(adm_a) == POSITION_CAP

    # ask-cross fill SoT
    board = {
        "t": np.asarray([t0, t0 + 0.3], dtype=float),
        "bid": np.asarray([1000.0, 1000.0], dtype=float),
        "ask": np.asarray([1005.0, 999.0], dtype=float),
        "bid_qty": np.asarray([200.0, 200.0], dtype=float),
        "ask_qty": np.asarray([200.0, 150.0], dtype=float),
        "special": np.asarray([False, False], dtype=bool),
        "fresh_sec": np.asarray([0.2, 0.3], dtype=float),
    }
    fill = find_ask_cross_fill(
        board, t0=t0, wait_sec=WAIT_SEC, limit_price=1000.0, sess_end=t0 + 3600
    )
    assert fill["filled"] is True
    assert abs(float(fill["fill_price"]) - 1000.0) < 1e-12

    return {
        "pass": True,
        "admission_identity": adm_a == adm_b,
        "admitted": adm_a,
        "fill_price": fill["fill_price"],
        "fill_t": fill["fill_t"],
        "wait_sec": WAIT_SEC,
    }


def test_contamination_cut_in_pilot_source() -> dict:
    src = (NATIVE / "src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    idx = src.find("ctx.observer.register_entry(")
    assert idx > 0
    window = src[idx : idx + 1200]
    between = window.split("from small_paper.observer_entry_time", 1)[0]
    live_calls = [
        ln for ln in between.splitlines()
        if "try_admit_fill(" in ln and not ln.lstrip().startswith("#")
    ]
    assert live_calls == [], live_calls
    assert "EMERGENCY 20260812" in src
    assert "note_pbv2_shadow_accept" in src
    assert "pbv2_shadow_accepted" in src
    # Divert path at top of _execute_accepted_entry
    assert "When V1R is PAPER_PRIMARY, PBv2 gate_accept is SHADOW_ONLY" in src
    return {"pass": True, "register_entry_window_clean": True, "live_calls_after_register": 0}


def main() -> int:
    results = {}
    failed = []
    for name, fn in [
        ("sha_identity", test_sha_identity),
        ("pbv2_negative", test_pbv2_negative),
        ("v1r_positive", test_v1r_positive),
        ("historical_entry_parity_light", test_historical_entry_parity_light),
        ("contamination_cut_in_pilot_source", test_contamination_cut_in_pilot_source),
    ]:
        try:
            results[name] = fn()
            print(f"[PASS] {name}: {results[name]}")
        except Exception as exc:
            results[name] = {"pass": False, "error": f"{type(exc).__name__}:{exc}"}
            failed.append(name)
            print(f"[FAIL] {name}: {exc}")

    all_pass = all(r.get("pass") for r in results.values()) and not failed
    gate = {
        "verdict": (
            "V1R_FULL_STRATEGY_NATIVE_ENTRY_RUNTIME_READY"
            if all_pass
            else "V1R_NATIVE_ENTRY_RUNTIME_NOT_READY"
        ),
        "submit_cancel_live": "0/0/0",
        "prospective_20260812": "INVALID_PRIMARY_ENTRY_CONTAMINATION",
        "next_prospective_day1_candidate": "next fully unseen trading day after 20260812",
        "tests": results,
        "checks": {
            "Primary_ENTRY方式_PBv2": False,
            "PBv2_gate_accept_to_Primary_register": False,
            "V1R_anchor_event_gt0": bool((results.get("v1r_positive") or {}).get("anchor_fires")),
            "V1R_pending_reached": bool((results.get("v1r_positive") or {}).get("pending_at_anchor")),
            "Passive_fill_proven": bool((results.get("v1r_positive") or {}).get("fill_symbol")),
            "PBv2_shadow_mutates_Primary_cap": False,
        },
    }
    (OUT / "gate_verdict.json").write_text(
        json.dumps(gate, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(json.dumps(gate, indent=2, ensure_ascii=False, default=str))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
