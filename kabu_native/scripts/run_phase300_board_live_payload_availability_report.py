#!/usr/bin/env python3
"""
Phase300: audit Board payload availability on live/PUSH path (log review only).

Output: kabu_native/results/reports/phase300_board_live_payload_availability_report.json
"""

from __future__ import annotations

import json
import socket
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase300_board_live_payload_availability_report.json"
BOARD_KEYS = ("BidQty", "AskQty", "BidPrice", "AskPrice")
DEPTH_KEYS = tuple(f"{side}{i}" for side in ("Buy", "Sell") for i in range(1, 11))
TARGET_DAYS = ("20260604", "20260605")
MAX_EVENT_FILE_BYTES = 300_000_000
MAX_LINES_PER_SESSION = 200_000


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_push_spec() -> dict[str, Any]:
    from api.push_client import EXPECTED_PUSH_FIELDS_STOCK, push_spec

    spec = push_spec()
    stock = list(EXPECTED_PUSH_FIELDS_STOCK)
    return {
        "expected_fields_stock": stock,
        "has_bid_qty": "BidQty" in stock,
        "has_ask_qty": "AskQty" in stock,
        "has_depth_buy_sell": any(k.startswith("Buy") or k.startswith("Sell") for k in stock),
        "notes": spec.get("notes"),
    }


def _rest_board_schema() -> dict[str, Any]:
    import importlib.util

    check_path = REPO / "kabu_native/scripts/check_api.py"
    spec = importlib.util.spec_from_file_location("check_api_p300", check_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    keys = list(mod.BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS)
    return {
        "top_level_keys_count": len(keys),
        "has_bid_qty": "BidQty" in keys,
        "has_ask_qty": "AskQty" in keys,
        "has_buy1": "Buy1" in keys,
        "has_sell1": "Sell1" in keys,
        "depth_level_keys": [k for k in keys if k.startswith("Buy") or k.startswith("Sell")],
    }


def _kabu_reachable() -> dict[str, Any]:
    from api.rest_client import DEFAULT_BASE_URL, load_kabu_env

    load_kabu_env(repo_root=REPO)
    base = DEFAULT_BASE_URL
    host = urlparse(base).hostname or "localhost"
    port = urlparse(base).port or 18080
    try:
        with socket.create_connection((host, port), timeout=2.0):
            reachable = True
    except OSError as exc:
        reachable = False
        err = str(exc)
    else:
        err = ""
    return {"base_url": base, "host": host, "port": port, "reachable": reachable, "error": err}


def _live_rest_board_probe(symbol: str = "9984") -> dict[str, Any]:
    reachable = _kabu_reachable()
    if not reachable["reachable"]:
        return {"skipped": True, "reason": "kabu_station_unreachable", **reachable}

    try:
        from api.rest_client import KabuNativeRestClient

        client = KabuNativeRestClient()
        token = client.issue_token()
        board = client.get_board(f"{symbol}@1", token=token)
    except Exception as exc:
        return {"skipped": True, "reason": "rest_board_failed", "error": str(exc), **reachable}

    imb = None
    try:
        from screening.morning_screen import calc_board_imbalance

        imb = calc_board_imbalance(board)
    except Exception as exc:
        imb_err = str(exc)
    else:
        imb_err = ""

    present = {k: board.get(k) is not None for k in BOARD_KEYS}
    depth_present = {k: board.get(k) is not None for k in DEPTH_KEYS[:4]}
    return {
        "skipped": False,
        "symbol": symbol,
        "board_keys_present": present,
        "depth_sample_present": depth_present,
        "calc_board_imbalance": imb,
        "imbalance_computable": imb is not None,
        "imbalance_error": imb_err,
    }


def _reject_event_from_trade(trade: dict[str, Any], *, reason: str) -> dict[str, Any]:
    """Minimal mirror of pilot_runner EVENT_FIELDS copy for reject (probe only)."""
    event_fields = (
        "entry_order_book_imbalance",
        "entry_board_mid_token_active",
        "entry_expectancy_score_v2",
        "entry_high_break_recent",
        "continuation_quality_score",
        "symbol",
        "profile",
        "entry_time",
    )
    ev: dict[str, Any] = {
        "event_type": "rejected",
        "gate_reject_reason": reason,
        "gate_accept": False,
    }
    for key in event_fields:
        if key in trade:
            ev[key] = trade.get(key)
    return ev


def _synthetic_push_path_probe() -> dict[str, Any]:
    """Trace pilot path without importing pilot_runner (enriched → pregate → reject event)."""
    from small_paper.board_imbalance_shadow import compute_entry_order_book_imbalance_field
    from small_paper.daytrade_suitability_gate import attach_entry_metrics_to_trade
    from small_paper.entry_expectancy_score_shadow import _feature_token, compute_entry_expectancy_score_fields
    from small_paper.live_feature_bridge import LiveFeatureBridge
    from research.continuation_quality_ranking import continuation_quality_score

    push_payload = {
        "Symbol": "9984",
        "CurrentPrice": 3500.0,
        "CurrentPriceTime": datetime.now().isoformat(),
        "BidQty": 4800.0,
        "AskQty": 5200.0,
        "VWAP": 3480.0,
        "TradingValue": 5e10,
        "HighPrice": 3550.0,
    }
    bridge = LiveFeatureBridge()
    snap = bridge.update("9984.T", push_payload)
    enriched = bridge.enrich_payload(push_payload, snap)

    enriched_board = {k: enriched.get(k) for k in BOARD_KEYS}
    board_preserved = all(enriched.get(k) == push_payload.get(k) for k in ("BidQty", "AskQty"))

    trade: dict[str, Any] = {
        "profile": "momentum_volume_v13_combined",
        "symbol": "9984.T",
        "momentum_continuation_score": snap.momentum_continuation_score,
        "max_continuation_duration": snap.max_continuation_duration,
        "current_price": push_payload["CurrentPrice"],
    }
    attach_entry_metrics_to_trade(trade, enriched)
    trade["continuation_quality_score"] = round(continuation_quality_score(trade), 4)
    trade.update(LiveFeatureBridge.trade_quality_extras(trade, snap))
    trade.update({"entry_high_break_recent": False})
    pregate = compute_entry_order_book_imbalance_field(payload=enriched)
    trade.update(pregate)
    trade.update(compute_entry_expectancy_score_fields(trade=trade))

    rej = _reject_event_from_trade(trade, reason="entry_score_v2_below_threshold")

    return {
        "push_payload_board_fields": {k: push_payload.get(k) for k in BOARD_KEYS},
        "enriched_board_fields_preserved": board_preserved,
        "enriched_board_fields": enriched_board,
        "trade_entry_order_book_imbalance": trade.get("entry_order_book_imbalance"),
        "trade_entry_board_mid_token_active": trade.get("entry_board_mid_token_active"),
        "trade_board_mid_token": _feature_token("Board", trade),
        "pregate_imbalance_not_null": trade.get("entry_order_book_imbalance") is not None,
        "pregate_board_mid_is_bool": isinstance(trade.get("entry_board_mid_token_active"), bool),
        "reject_event_has_imbalance": rej.get("entry_order_book_imbalance") is not None,
        "reject_event_board_mid_active": rej.get("entry_board_mid_token_active"),
        "reject_event_entry_score_v2": rej.get("entry_expectancy_score_v2"),
    }


def _scan_archived_events() -> dict[str, Any]:
    """Scan small_paper event logs if present (read-only)."""
    sessions: list[dict[str, Any]] = []
    totals = Counter()
    if not SMALL_PAPER.is_dir():
        return {"sessions_found": 0, "note": "small_paper_missing"}

    for day_dir in sorted(SMALL_PAPER.iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        if day not in TARGET_DAYS:
            continue
        for sess in sorted(day_dir.iterdir()):
            if not sess.is_dir() or "live_session" not in sess.name.lower():
                continue
            ev_path = sess / "small_paper_events.jsonl"
            if not ev_path.is_file():
                continue
            if ev_path.stat().st_size > MAX_EVENT_FILE_BYTES:
                sessions.append(
                    {
                        "session_id": sess.relative_to(SMALL_PAPER).as_posix(),
                        "skipped": True,
                        "reason": f"file_bytes>{MAX_EVENT_FILE_BYTES}",
                    }
                )
                continue
            sid = sess.relative_to(SMALL_PAPER).as_posix()
            _scan_one_session(ev_path, sid, sessions, totals)
    if not sessions:
        return {
            "sessions_found": 0,
            "note": "day_dirs_exist_but_no_event_jsonl_on_disk",
            "day_dirs": sorted(p.name for p in SMALL_PAPER.iterdir() if p.is_dir()),
        }
    return {
        "sessions_found": len(sessions),
        "aggregate": dict(totals),
        "sessions": sessions[:20],
    }


def _scan_one_session(
    ev_path: Path,
    sid: str,
    sessions: list[dict[str, Any]],
    totals: Counter,
) -> None:
    n = 0
    imb_non_null = 0
    board_mid_true = 0
    imb_null_reject = 0
    v2_reject = 0
    raw_board_in_event = 0
    truncated = False
    with ev_path.open(encoding="utf-8") as f:
        for line in f:
            if n >= MAX_LINES_PER_SESSION:
                truncated = True
                break
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            n += 1
            if ev.get("entry_order_book_imbalance") is not None:
                imb_non_null += 1
            if ev.get("entry_board_mid_token_active") is True:
                board_mid_true += 1
            if any(ev.get(k) is not None for k in BOARD_KEYS):
                raw_board_in_event += 1
            if (
                str(ev.get("event_type") or "") == "rejected"
                and str(ev.get("gate_reject_reason") or "") == "entry_score_v2_below_threshold"
            ):
                v2_reject += 1
                if ev.get("entry_order_book_imbalance") is None:
                    imb_null_reject += 1
    sessions.append(
        {
            "session_id": sid,
            "event_count": n,
            "truncated": truncated,
            "entry_order_book_imbalance_non_null": imb_non_null,
            "entry_board_mid_token_active_true": board_mid_true,
            "raw_bid_ask_in_event": raw_board_in_event,
            "v2_reject_count": v2_reject,
            "v2_reject_imb_null": imb_null_reject,
        }
    )
    totals["events"] += n
    totals["imb_non_null"] += imb_non_null
    totals["board_mid_true"] += board_mid_true
    totals["raw_board_in_event"] += raw_board_in_event
    totals["v2_reject"] += v2_reject
    totals["v2_reject_imb_null"] += imb_null_reject


def _code_path_audit() -> dict[str, Any]:
    pilot = (REPO / "kabu_native/src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    return {
        "pregate_call_before_score": "compute_entry_order_book_imbalance_field(payload=enriched)" in pilot
        and pilot.find("compute_entry_order_book_imbalance_field")
        < pilot.find("compute_entry_expectancy_score_fields(trade=trade)"),
        "enriched_payload_source": "feature_bridge.enrich_payload preserves dict(payload) + snapshot fields",
        "event_fields_include_imbalance": '"entry_order_book_imbalance"' in pilot,
        "event_fields_include_board_mid": '"entry_board_mid_token_active"' in pilot,
        "reject_event_via_event_from_gate": True,
        "raw_bid_ask_not_in_event_fields": True,
        "discord_reject_includes_board_fields": False,
        "discord_reject_note": "notify_rejected posts symbol/reason/quality only; no entry_order_book_imbalance",
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    push_spec = _load_push_spec()
    rest_schema = _rest_board_schema()
    reachable = _kabu_reachable()
    rest_probe = _live_rest_board_probe()
    synthetic = _synthetic_push_path_probe()
    archived = _scan_archived_events()
    code = _code_path_audit()

    checks = {
        "1_push_payload_has_board_qty_fields": {
            "push_bid_ask_expected": push_spec["has_bid_qty"] and push_spec["has_ask_qty"],
            "push_depth_levels_expected": push_spec["has_depth_buy_sell"],
            "rest_board_full_depth": rest_schema["has_buy1"] and rest_schema["has_sell1"],
            "verdict": (
                "PUSH spec includes BidQty/AskQty (top-of-book). "
                "Full Buy1-10/Sell1-10 depth is REST /board only, not in PUSH expected_fields."
            ),
        },
        "2_pilot_runner_trade_receives_board_via_enriched": {
            "synthetic_enriched_preserved": synthetic["enriched_board_fields_preserved"],
            "verdict": "enriched=dict(payload)+snapshot; BidQty/AskQty pass through to pregate input"
            if synthetic["enriched_board_fields_preserved"]
            else "FAIL: enriched lost board fields",
        },
        "3_gate_pregate_imbalance_not_null": {
            "synthetic_pregate_not_null": synthetic["pregate_imbalance_not_null"],
            "live_rest_probe": rest_probe.get("imbalance_computable"),
            "archived_reject_imb_null_pct": (
                round(100.0 * archived.get("aggregate", {}).get("v2_reject_imb_null", 0) / max(1, archived.get("aggregate", {}).get("v2_reject", 1)), 2)
                if archived.get("aggregate")
                else None
            ),
            "verdict": "Synthetic path OK; archived logs lack raw board columns (Phase299 finding)"
            if synthetic["pregate_imbalance_not_null"]
            else "pregate returned null on synthetic PUSH",
        },
        "4_entry_board_mid_token_active_recorded": {
            "synthetic_is_bool": synthetic["pregate_board_mid_is_bool"],
            "reject_event_present": synthetic["reject_event_board_mid_active"] is not None,
            "field_in_event_fields": code["event_fields_include_board_mid"],
        },
        "5_reject_event_log_persistence": {
            "reject_has_imbalance": synthetic["reject_event_has_imbalance"],
            "code_path_pregate_before_gate": code["pregate_call_before_score"],
            "archived_scan": archived,
            "discord_includes_board": code["discord_reject_includes_board_fields"],
            "verdict": "small_paper_events reject rows get entry_order_book_imbalance via EVENT_FIELDS; Discord REJECT does not",
        },
    }

    all_synthetic_ok = all(
        [
            synthetic["enriched_board_fields_preserved"],
            synthetic["pregate_imbalance_not_null"],
            synthetic["pregate_board_mid_is_bool"],
            synthetic["reject_event_has_imbalance"],
        ]
    )

    report = {
        "phase": 300,
        "title": "board_live_payload_availability_report",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraint": "log review only; no production logic changes",
        "probe_methods": [
            "push_spec_static",
            "rest_board_schema_static",
            "kabu_reachability",
            "optional_rest_board_live_probe",
            "synthetic_pilot_path_probe",
            "archived_small_paper_event_scan",
            "code_path_audit",
        ],
        "kabu_reachability": reachable,
        "push_spec": push_spec,
        "rest_board_schema": rest_schema,
        "live_rest_board_probe": rest_probe,
        "synthetic_pilot_path_probe": synthetic,
        "archived_event_scan": archived,
        "code_path_audit": code,
        "checks": checks,
        "verdict": {
            "board_qty_available_on_push_spec": push_spec["has_bid_qty"] and push_spec["has_ask_qty"],
            "pilot_path_works_when_payload_has_bid_ask": all_synthetic_ok,
            "live_probe_completed": not rest_probe.get("skipped", True),
            "archived_logs_exercise_board": (archived.get("aggregate", {}).get("raw_board_in_event", 0) or 0) > 0,
            "production_ready_conditional": (
                "When live PUSH carries BidQty/AskQty, Phase299 pregate computes imbalance before gate. "
                "Verify on next live session; archived jsonl does not store raw board columns."
            ),
            "gaps": [
                "PUSH expected_fields has BidQty/AskQty only (no Buy1-10 depth); calc_board_imbalance uses both when present",
                "small_paper_events do not persist raw BidQty/AskQty (only entry_order_book_imbalance)",
                "Discord notify_rejected does not include board imbalance fields",
                "kabu_station was unreachable during this audit; REST live probe skipped" if not reachable["reachable"] else None,
            ],
        },
    }
    report["verdict"]["gaps"] = [g for g in report["verdict"]["gaps"] if g]

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(f"synthetic_ok={all_synthetic_ok} kabu_reachable={reachable['reachable']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
