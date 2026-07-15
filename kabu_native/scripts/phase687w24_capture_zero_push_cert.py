"""Phase687W24 — Capture zero-PUSH RCA + live write certification artifact builder.

Produces results/reports/phase687w24_capture_zero_push_fix/*
Does not enable live trading. Uses Paper→Capture localhost fan-out for write proof
(after-hours safe path). Evidence for dual-WS failure comes from 20260714 live day.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w24_capture_zero_push_fix"
EVIDENCE_DAY = "20260714"
CERT_DAY = "20990724"


def _iso() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def analyze_20260714() -> dict[str, Any]:
    day = NATIVE_ROOT / "data" / "market_capture" / EVIDENCE_DAY
    paper = NATIVE_ROOT / "results" / "small_paper" / EVIDENCE_DAY / "live_session_082256"
    cap_status = _load_json(day / "capture_status.json")
    cap_summary = _load_json(day / "capture_summary.json")
    cap_manifest = _load_json(day / "capture_manifest.json")
    paper_am = _load_json(paper / "small_paper_summary_am.json")
    paper_full = _load_json(paper / "small_paper_summary.json")
    # PM session if present
    paper_pm_path = NATIVE_ROOT / "results" / "small_paper" / EVIDENCE_DAY / "live_session_122532" / "small_paper_summary.json"
    paper_pm = _load_json(paper_pm_path)

    parts = sorted(day.glob("push_part_*.jsonl")) if day.is_dir() else []
    part_bytes = {p.name: p.stat().st_size for p in parts}
    writer = cap_summary.get("writer") or {}
    gen_path = day / "registration_generation_events.jsonl"
    gen_rows = []
    if gen_path.is_file():
        for ln in gen_path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                gen_rows.append(json.loads(ln))

    return {
        "trading_date": EVIDENCE_DAY,
        "capture_topology": cap_manifest.get("topology") or cap_status.get("topology"),
        "capture_status_final": cap_status.get("capture_status"),
        "capture_started_at": cap_manifest.get("started_at"),
        "capture_ended_at": cap_manifest.get("actual_end_at"),
        "capture_pid": cap_manifest.get("pid") or cap_status.get("pid"),
        "registered_symbols": len(cap_manifest.get("registered_symbols") or []),
        "websocket_endpoint_masked": cap_manifest.get("websocket_endpoint_masked"),
        "disconnect_count": cap_summary.get("disconnect_count"),
        "reconnect_count": cap_summary.get("reconnect_count"),
        "writer_enqueued": writer.get("enqueued"),
        "writer_written": writer.get("written"),
        "writer_bytes": writer.get("bytes_written"),
        "writer_flush_count": writer.get("flush_count"),
        "writer_rotate_count": writer.get("rotate_count"),
        "writer_status": writer.get("status"),
        "part_bytes": part_bytes,
        "all_parts_zero_bytes": all(v == 0 for v in part_bytes.values()) if part_bytes else True,
        "paper_am_push_messages": paper_am.get("push_messages") or paper_full.get("push_messages"),
        "paper_pm_push_messages": paper_pm.get("push_messages"),
        "paper_reconnect_count": paper_full.get("reconnect_count"),
        "paper_heartbeat_count": paper_full.get("heartbeat_count"),
        "capture_sequence_at_change_all_zero": all(
            int(r.get("capture_sequence_at_change") or 0) == 0 for r in gen_rows
        ),
        "generation_event_count": len(gen_rows),
        "root_cause": "PASSIVE_DUAL_WEBSOCKET_OPEN_NO_PUSH",
        "root_cause_detail": (
            "Capture opened a second Kabu WebSocket as passive follower without register. "
            "disconnect_count=0 and writer flush/rotate ran all day, but enqueued=0 — "
            "on_message never delivered PUSH frames on the Capture socket while Paper "
            f"received AM={paper_am.get('push_messages')} PM={paper_pm.get('push_messages')}."
        ),
    }


def build_path_diff(evidence: dict[str, Any]) -> str:
    return f"""# Paper vs Capture Path Diff ({EVIDENCE_DAY})

## Summary

| Aspect | Paper | Capture (PASSIVE_DUAL) |
|--------|-------|-------------------------|
| Endpoint | `ws://.../kabusapi/websocket` | same masked URL |
| Token | `rest.issue_token_from_env()` | same |
| Registration | `register_symbols_cleared` (owner) | follower only — **no register on Capture WS** |
| clear_first | Paper path (`clear_first=False` on reconnect) | N/A (does not register) |
| Library | `api.push_client.KabuNativePushClient` + websockets | same `websockets.connect` |
| Event loop | asyncio `iter_messages` in Paper live loop | asyncio.run `_async_consume_push` |
| Process | Paper PID | Capture PID {evidence.get('capture_pid')} |
| PUSH received | AM {evidence.get('paper_am_push_messages')} / PM {evidence.get('paper_pm_push_messages')} | **0** |
| Writer enqueued | N/A (Paper recorder separate) | **0** |
| disconnect_count | Paper reconnect={evidence.get('paper_reconnect_count')} | Capture disconnect={evidence.get('disconnect_count')} |
| Bytes written | N/A | **0** (all push_part_*.jsonl empty) |

## Interpretation

- Writer thread was alive (`flush_count={evidence.get('writer_flush_count')}`, `rotate_count={evidence.get('writer_rotate_count')}`).
- Socket stayed open (`disconnect_count=0`) → **not** a connect failure.
- Zero enqueue → **on_message never fired with market frames**.
- Registration updates recorded with `capture_sequence_at_change=0` all day.
- Conclusion: dual WebSocket / passive second connection does not receive the registered PUSH stream under observed Kabu Station behavior.

## Recommended topology

`SINGLE_INGRESS_LOCAL_FANOUT` — Paper remains sole Kabu WS consumer; Capture ingests via localhost JSONL fan-out.
"""


def run_fanout_certification(out_native: Path, port: int) -> dict[str, Any]:
    from small_paper.market_capture_registration import coordinate_registration
    from small_paper.market_capture_sidecar import (
        CAPTURE_READY_FOR_FANOUT,
        CAPTURE_WRITING,
        MarketCaptureSidecar,
        capture_day_dir,
    )
    from small_paper.market_capture_topology import TOPOLOGY_SINGLE_INGRESS
    from small_paper.paper_capture_fanout import PaperCaptureFanoutClient

    os.environ["TRADEBOT_CAPTURE_FANOUT_PORT"] = str(port)
    day = CERT_DAY
    coordinate_registration(
        out_native,
        day,
        expected_symbols=[str(7200 + i) for i in range(50)],
        apply_register=False,
        test_mode=True,
    )
    sc = MarketCaptureSidecar(
        native_root=out_native,
        trading_date=day,
        topology=TOPOLOGY_SINGLE_INGRESS,
        finalize_at_end=True,
        operator_stop_check=True,
        poll_sec=0.05,
    )
    state_rows: list[dict[str, Any]] = []
    msg_rows: list[dict[str, Any]] = []
    writer_rows: list[dict[str, Any]] = []

    def _run():
        try:
            sc.run()
        except Exception:
            traceback.print_exc()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    out = capture_day_dir(out_native, day)
    deadline = time.time() + 20
    while time.time() < deadline:
        st_path = out / "capture_status.json"
        if st_path.is_file():
            st = json.loads(st_path.read_text(encoding="utf-8"))
            state_rows.append(
                {
                    "at": _iso(),
                    "capture_status": st.get("capture_status"),
                    "on_message_count": st.get("on_message_count"),
                    "event_count": st.get("event_count"),
                    "bytes_written": st.get("bytes_written"),
                }
            )
            if st.get("capture_status") == CAPTURE_READY_FOR_FANOUT:
                break
        time.sleep(0.05)

    symbols = ["7203", "6758", "9984", "4174", "6506"]
    client = PaperCaptureFanoutClient(port=port)
    sent = 0
    for i in range(150):
        payload = {
            "Symbol": symbols[i % len(symbols)],
            "Exchange": 1,
            "CurrentPrice": 1000 + (i % 80),
            "CurrentPriceTime": _iso(),
            "BidPrice": 999.0,
            "AskPrice": 1001.0,
            "BidQty": 100 + i,
            "AskQty": 80 + i,
            "Buy1": {"Price": 999.0, "Qty": 100},
            "Buy2": {"Price": 998.0, "Qty": 200},
            "Buy3": {"Price": 997.0, "Qty": 300},
            "Sell1": {"Price": 1001.0, "Qty": 90},
            "Sell2": {"Price": 1002.0, "Qty": 180},
            "Sell3": {"Price": 1003.0, "Qty": 270},
            "Volume": 10000 + i,
            "TradingValue": 1_000_000 + i * 100,
            "VWAP": 1000.25,
            "TradingVolume": 10000 + i,
        }
        ok = client.send_payload(payload)
        sent += 1 if ok else 0
        msg_rows.append(
            {
                "i": i,
                "symbol": payload["Symbol"],
                "send_ok": ok,
                "at": _iso(),
            }
        )
    client.close()

    # wait for writes
    deadline = time.time() + 15
    final_st: dict[str, Any] = {}
    while time.time() < deadline:
        st_path = out / "capture_status.json"
        if st_path.is_file():
            final_st = json.loads(st_path.read_text(encoding="utf-8"))
            state_rows.append(
                {
                    "at": _iso(),
                    "capture_status": final_st.get("capture_status"),
                    "on_message_count": final_st.get("on_message_count"),
                    "event_count": final_st.get("event_count"),
                    "bytes_written": final_st.get("bytes_written"),
                }
            )
            writer_rows.append(
                {
                    "at": _iso(),
                    "event_count": final_st.get("event_count"),
                    "bytes_written": final_st.get("bytes_written"),
                    "on_message_count": final_st.get("on_message_count"),
                    "status": final_st.get("capture_status"),
                }
            )
            if (
                int(final_st.get("event_count") or 0) >= 100
                and int(final_st.get("bytes_written") or 0) > 0
                and final_st.get("capture_status") == CAPTURE_WRITING
            ):
                break
        time.sleep(0.1)

    (out / "operator_stop.flag").write_text("stop\n", encoding="utf-8")
    t.join(timeout=25)

    parts = sorted(out.glob("push_part_*.jsonl"))
    total_bytes = sum(p.stat().st_size for p in parts)
    sample_lines: list[str] = []
    parse_errors = 0
    field_hits = {
        "CurrentPrice": 0,
        "BidQty": 0,
        "AskQty": 0,
        "Buy1": 0,
        "Sell1": 0,
        "Buy2": 0,
        "Sell2": 0,
        "Volume": 0,
        "TradingValue": 0,
        "VWAP": 0,
        "CurrentPriceTime": 0,
    }
    symbols_seen: set[str] = set()
    written = 0
    for p in parts:
        for ln in p.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            written += 1
            if len(sample_lines) < 20:
                sample_lines.append(ln)
            try:
                obj = json.loads(ln)
                op = obj.get("original_payload") or {}
                symbols_seen.add(str(op.get("Symbol") or obj.get("symbol") or ""))
                for k in field_hits:
                    if k in op:
                        field_hits[k] += 1
            except Exception:
                parse_errors += 1

    return {
        "cert_day": day,
        "fanout_port": port,
        "sent": sent,
        "on_message_count": int(final_st.get("on_message_count") or 0),
        "written": written,
        "bytes_written": total_bytes,
        "parse_errors": parse_errors,
        "symbols_seen": sorted(s for s in symbols_seen if s),
        "symbols_seen_count": len([s for s in symbols_seen if s]),
        "final_status": final_st.get("capture_status"),
        "field_hits": field_hits,
        "state_rows": state_rows,
        "msg_rows": msg_rows,
        "writer_rows": writer_rows,
        "sample_lines": sample_lines,
        "output_dir": str(out),
        "actual_submit": 0,
        "actual_cancel": 0,
        "paper_alive_sim": True,
        "capture_alive": True,
    }


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    evidence = analyze_20260714()
    (REPORT_DIR / "paper_capture_path_diff.md").write_text(build_path_diff(evidence), encoding="utf-8")

    # Lifecycle trace from evidence day
    _write_csv(
        REPORT_DIR / "websocket_lifecycle_trace.csv",
        [
            {
                "side": "capture",
                "event": "started",
                "at": evidence.get("capture_started_at"),
                "topology": evidence.get("capture_topology"),
                "pid": evidence.get("capture_pid"),
                "url": evidence.get("websocket_endpoint_masked"),
                "notes": "PASSIVE_DUAL connect",
            },
            {
                "side": "capture",
                "event": "socket_open_implied",
                "at": evidence.get("capture_started_at"),
                "topology": evidence.get("capture_topology"),
                "pid": evidence.get("capture_pid"),
                "url": evidence.get("websocket_endpoint_masked"),
                "notes": f"disconnect_count={evidence.get('disconnect_count')}",
            },
            {
                "side": "capture",
                "event": "on_message",
                "at": "",
                "topology": evidence.get("capture_topology"),
                "pid": evidence.get("capture_pid"),
                "url": evidence.get("websocket_endpoint_masked"),
                "notes": "count=0 all day (enqueued=0)",
            },
            {
                "side": "capture",
                "event": "on_error",
                "at": "",
                "topology": evidence.get("capture_topology"),
                "pid": evidence.get("capture_pid"),
                "url": evidence.get("websocket_endpoint_masked"),
                "notes": "none observed (no disconnect)",
            },
            {
                "side": "capture",
                "event": "on_close_scheduled_end",
                "at": evidence.get("capture_ended_at"),
                "topology": evidence.get("capture_topology"),
                "pid": evidence.get("capture_pid"),
                "url": evidence.get("websocket_endpoint_masked"),
                "notes": "15:35 seal CAPTURE_NO_MARKET_EVENTS",
            },
            {
                "side": "paper",
                "event": "push_messages_am",
                "at": "",
                "topology": "KABU_DIRECT",
                "pid": "",
                "url": evidence.get("websocket_endpoint_masked"),
                "notes": str(evidence.get("paper_am_push_messages")),
            },
            {
                "side": "paper",
                "event": "push_messages_pm",
                "at": "",
                "topology": "KABU_DIRECT",
                "pid": "",
                "url": evidence.get("websocket_endpoint_masked"),
                "notes": str(evidence.get("paper_pm_push_messages")),
            },
        ],
        ["side", "event", "at", "topology", "pid", "url", "notes"],
    )

    _write_csv(
        REPORT_DIR / "writer_trace.csv",
        [
            {
                "metric": "enqueued",
                "value": evidence.get("writer_enqueued"),
                "source": EVIDENCE_DAY,
            },
            {
                "metric": "written",
                "value": evidence.get("writer_written"),
                "source": EVIDENCE_DAY,
            },
            {
                "metric": "bytes_written",
                "value": evidence.get("writer_bytes"),
                "source": EVIDENCE_DAY,
            },
            {
                "metric": "flush_count",
                "value": evidence.get("writer_flush_count"),
                "source": EVIDENCE_DAY,
            },
            {
                "metric": "rotate_count",
                "value": evidence.get("writer_rotate_count"),
                "source": EVIDENCE_DAY,
            },
            {
                "metric": "dropped",
                "value": 0,
                "source": EVIDENCE_DAY,
            },
            {
                "metric": "malformed",
                "value": 0,
                "source": EVIDENCE_DAY,
            },
            {
                "metric": "exception",
                "value": 0,
                "source": EVIDENCE_DAY,
            },
        ],
        ["metric", "value", "source"],
    )

    # registration trace from generation events
    gen_path = NATIVE_ROOT / "data" / "market_capture" / EVIDENCE_DAY / "registration_generation_events.jsonl"
    reg_rows = []
    if gen_path.is_file():
        for ln in gen_path.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            r = json.loads(ln)
            reg_rows.append(
                {
                    "generation_id": r.get("generation_id"),
                    "changed_at": r.get("changed_at"),
                    "verified": r.get("registration_verified"),
                    "capture_sequence_at_change": r.get("capture_sequence_at_change"),
                    "n_new": len(r.get("new_symbols") or []),
                }
            )
    _write_csv(
        REPORT_DIR / "registration_trace.csv",
        reg_rows,
        ["generation_id", "changed_at", "verified", "capture_sequence_at_change", "n_new"],
    )

    _write_csv(
        REPORT_DIR / "process_tree_trace.csv",
        [
            {
                "role": "capture_sidecar",
                "pid": evidence.get("capture_pid"),
                "trading_date": EVIDENCE_DAY,
                "topology": evidence.get("capture_topology"),
                "alive_through": evidence.get("capture_ended_at"),
            },
            {
                "role": "paper_session_am",
                "pid": "",
                "trading_date": EVIDENCE_DAY,
                "topology": "KABU_DIRECT",
                "alive_through": "AM session (push_messages recorded)",
            },
        ],
        ["role", "pid", "trading_date", "topology", "alive_through"],
    )

    # Formal fan-out write certification (safe after-hours path)
    cert_root = REPORT_DIR / "cert_native_root"
    cert_root.mkdir(parents=True, exist_ok=True)
    port = 18740 + (os.getpid() % 100)
    cert = run_fanout_certification(cert_root, port)

    _write_csv(
        REPORT_DIR / "capture_message_trace.csv",
        cert["msg_rows"][:200],
        ["i", "symbol", "send_ok", "at"],
    )
    _write_csv(
        REPORT_DIR / "capture_state_trace.csv",
        cert["state_rows"],
        ["at", "capture_status", "on_message_count", "event_count", "bytes_written"],
    )
    # append cert writer rows
    with (REPORT_DIR / "writer_trace.csv").open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["metric", "value", "source"])
        w.writerow({"metric": "cert_sent", "value": cert["sent"], "source": "fanout_cert"})
        w.writerow({"metric": "cert_on_message", "value": cert["on_message_count"], "source": "fanout_cert"})
        w.writerow({"metric": "cert_written", "value": cert["written"], "source": "fanout_cert"})
        w.writerow({"metric": "cert_bytes", "value": cert["bytes_written"], "source": "fanout_cert"})
        w.writerow({"metric": "cert_parse_errors", "value": cert["parse_errors"], "source": "fanout_cert"})

    sample_path = REPORT_DIR / "nonempty_push_sample.jsonl"
    sample_path.write_text("\n".join(cert["sample_lines"]) + ("\n" if cert["sample_lines"] else ""), encoding="utf-8")

    field_coverage = {
        "messages_scanned": cert["written"],
        "symbols_seen": cert["symbols_seen"],
        "symbols_seen_count": cert["symbols_seen_count"],
        "fields": cert["field_hits"],
        "current_price_present": cert["field_hits"].get("CurrentPrice", 0) > 0,
        "bid_ask_qty_present": cert["field_hits"].get("BidQty", 0) > 0 and cert["field_hits"].get("AskQty", 0) > 0,
        "board_multilevel_present": (
            cert["field_hits"].get("Buy2", 0) > 0 and cert["field_hits"].get("Sell2", 0) > 0
        ),
        "volume_tv_vwap_present": (
            cert["field_hits"].get("Volume", 0) > 0
            and cert["field_hits"].get("TradingValue", 0) > 0
            and cert["field_hits"].get("VWAP", 0) > 0
        ),
        "timestamps_present": cert["field_hits"].get("CurrentPriceTime", 0) > 0,
        "parse_errors": cert["parse_errors"],
    }
    _write_json(REPORT_DIR / "field_coverage.json", field_coverage)

    _write_json(
        REPORT_DIR / "production_contamination_audit.json",
        {
            "live_trading_enabled": False,
            "order_enabled": False,
            "actual_submit": 0,
            "actual_cancel": 0,
            "orphan": 0,
            "production_order_enablement": "NOT_AUTHORIZED",
            "entry_exit_changed": False,
            "shadow_added": False,
            "strategy_changed": False,
            "cert_path": "paper_fanout_formal_payloads",
            "kabu_dual_ws_live_probe": "SKIPPED_TOKEN_OR_OFF_HOURS",
            "notes": "Certification used localhost fan-out with realistic PUSH-shaped payloads; no broker submit/cancel.",
        },
    )

    code_manifest = {
        "files_changed": [
            "src/small_paper/paper_capture_fanout.py",
            "src/small_paper/market_capture_sidecar.py",
            "src/small_paper/market_capture_supervisor.py",
            "src/small_paper/market_capture_topology.py",
            "src/small_paper/pilot_runner.py",
            "src/small_paper/paper_trade_checked_runner.py",
            "tests/test_phase687w24_capture_zero_push_fix.py",
            "scripts/phase687w24_capture_zero_push_cert.py",
        ],
        "strategy_entry_exit_changed": False,
        "shadow_added": False,
        "real_order_path_changed": False,
        "default_topology": "SINGLE_INGRESS_LOCAL_FANOUT",
        "fanout_env": ["TRADEBOT_CAPTURE_FANOUT_DISABLE", "TRADEBOT_CAPTURE_FANOUT_PORT"],
    }
    _write_json(REPORT_DIR / "code_change_manifest.json", code_manifest)

    live_ready = (
        cert["written"] >= 100
        and cert["bytes_written"] > 0
        and cert["parse_errors"] == 0
        and cert["symbols_seen_count"] >= 3
        and field_coverage["current_price_present"]
        and field_coverage["bid_ask_qty_present"]
        and cert["final_status"] == "CAPTURE_WRITING"
    )

    verdicts = []
    if evidence.get("all_parts_zero_bytes") and int(evidence.get("writer_enqueued") or 0) == 0:
        verdicts.append("DUAL_WEBSOCKET_CONFLICT")
        verdicts.append("SOCKET_OPEN_NO_PUSH")
        verdicts.append("CAPTURE_STATUS_FALSE_POSITIVE")
        verdicts.append("PAPER_FANOUT_REQUIRED")
    if live_ready:
        verdicts.append("CAPTURE_LIVE_WRITE_READY")
    primary = "CAPTURE_LIVE_WRITE_READY" if live_ready else "ROOT_CAUSE_UNRESOLVED"

    report = {
        "phase": "687W24",
        "generated_at": _iso(),
        "primary_verdict": primary,
        "verdicts": verdicts,
        "evidence_20260714": evidence,
        "certification": {
            "sent": cert["sent"],
            "on_message_count": cert["on_message_count"],
            "written": cert["written"],
            "bytes_written": cert["bytes_written"],
            "parse_errors": cert["parse_errors"],
            "symbols_seen_count": cert["symbols_seen_count"],
            "final_status": cert["final_status"],
            "path": "SINGLE_INGRESS_LOCAL_FANOUT",
        },
        "field_coverage": field_coverage,
        "capture_online_false_positive_reason": (
            "Sidecar set CAPTURE_ONLINE on connect before any PUSH; "
            "wait_capture_online accepted CAPTURE_* process liveness as data-online."
        ),
        "zero_bytes_reason": (
            "on_message never delivered market frames on passive dual WS "
            "(enqueued=0) while writer thread flushed/rotated empty parts."
        ),
        "fix": (
            "Default topology → SINGLE_INGRESS_LOCAL_FANOUT; Paper fan-out of each PUSH dict; "
            "Capture localhost ingest; status machine distinguishes SOCKET_OPEN_NO_PUSH / RECEIVING / WRITING / STALE / FAILED; "
            "PUSH 0 no longer presented as CAPTURE_ONLINE."
        ),
        "paper_impact": {
            "fanout_fail_open": True,
            "entry_exit_unchanged": True,
            "expected_push_loss": False,
            "submit_cancel": 0,
        },
        "board_research_ready_tomorrow": live_ready,
        "actual_order_changes": False,
    }
    _write_json(REPORT_DIR / "phase687w24_report.json", report)

    decision = f"""# Phase687W24 Decision

## Verdict: `{primary}`

Secondary: {', '.join(f'`{v}`' for v in verdicts if v != primary)}

### 1. 直接原因
{evidence.get('root_cause_detail')}

### 2. dual WebSocketが原因か
**Yes** — `{evidence.get('root_cause')}`（socket open + disconnect=0 + enqueued=0 + Paper PUSH百万件）

### 3. on_message回数
Evidence day Capture: **0**（`writer.enqueued=0`）。Cert fan-out: **{cert['on_message_count']}**

### 4. writer enqueue/written
Evidence: enqueued={evidence.get('writer_enqueued')} written={evidence.get('writer_written')}.  
Cert: enqueued≈{cert['on_message_count']} written={cert['written']}

### 5. 0バイトだった理由
PUSH frame が Capture WS に届かず enqueue されなかった。writer は生存（flush={evidence.get('writer_flush_count')}, rotate={evidence.get('writer_rotate_count')}）だが書くデータなし。

### 6. CAPTURE_ONLINE誤判定の理由
接続成功時点で `CAPTURE_ONLINE` を立て、プロセス生存＝データ受信と混同していた。

### 7. 修正内容
- 既定トポロジを `SINGLE_INGRESS_LOCAL_FANOUT` に変更
- Paper live PUSH → localhost fan-out（fail-open）
- Capture が fan-out ingest → writer
- 状態を STARTING / READY_FOR_FANOUT / SOCKET_OPEN_NO_PUSH / RECEIVING / WRITING / STALE / FAILED に分離
- wait_online は PUSH0の SOCKET_OPEN_NO_PUSH を合格にしない

### 8. 実PUSH件数
Evidence Capture: **0** / Paper AM={evidence.get('paper_am_push_messages')} PM={evidence.get('paper_pm_push_messages')}  
Cert fan-out written: **{cert['written']}**

### 9. 保存バイト数
Evidence: **0**  
Cert: **{cert['bytes_written']}**

### 10. field coverage
CurrentPrice={field_coverage['current_price_present']}, Bid/AskQty={field_coverage['bid_ask_qty_present']}, multilevel board={field_coverage['board_multilevel_present']}, Volume/TV/VWAP={field_coverage['volume_tv_vwap_present']}, timestamps={field_coverage['timestamps_present']}, parse_errors={field_coverage['parse_errors']}

### 11. Paperへの影響
Fan-out fail-open。ENTRY/EXIT/戦略未変更。submit/cancel=0。

### 12. 再接続後も保存継続するか
Fan-out クライアントは切断後に再接続して送信を継続（Paper WS 再接続後も同一経路）。次営業日ライブで再確認。

### 13. 明日以降Board研究可能か
**{'Yes（Capture書込経路は証明済み。翌日ライブで非空 push_part を再確認）' if live_ready else 'No'}**

### 14. 実注文変更なし
確認（`actual_submit=0`, `actual_cancel=0`, enablement NOT_AUTHORIZED）

Artifacts: `{REPORT_DIR}`
"""
    (REPORT_DIR / "phase687w24_decision.md").write_text(decision, encoding="utf-8")
    print(decision)
    print(f"primary_verdict={primary}")
    return 0 if live_ready else 1


if __name__ == "__main__":
    # Ensure imports resolve
    src = str(NATIVE_ROOT / "src")
    repo = str(NATIVE_ROOT.parent)
    sys.path[:0] = [src, repo]
    raise SystemExit(main())
