"""Recover the 28 unknown EXIT uids from the PM sessions that already contain them.

Does not rerun the 29-day morning sessions or the eight capital books.
"""
from __future__ import annotations

import json
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import _capture_dir, _paper_sessions, _symbol
from research.current50_v16_historical_economic_expansion_v2.expand import _attach_text
from research.simple_tech_entry_family.harvest import _parse_iso
from research.v16_execution_realism_unfilled_risk_v1.audit import QtyE, _frozen_ok, _install_patch
from research.v16_one_tick_exit_capital_scaling_v2.run import _busy
from small_paper.fixed_support_x1_session import _EPS, _bid_ok
from small_paper.paper_session_executor import bind_outer_registration

JST = timezone(timedelta(hours=9))
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "v16_exit_execution_evidence_gap_rca_v1"
PRIOR = NATIVE / "results" / "research" / "v16_execution_realism_unfilled_risk_v1" / "report.json"
G = 1_000_000.0
WORKERS = 2


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(float(stamp), JST).isoformat(timespec="milliseconds")


def _clock(payload: dict[str, Any], ev: dict[str, Any]) -> dict[str, Any]:
    buy = payload.get("Buy1") if isinstance(payload.get("Buy1"), dict) else None
    sell = payload.get("Sell1") if isinstance(payload.get("Sell1"), dict) else None
    return {
        "buy1_present": buy is not None,
        "buy1_price": None if buy is None else buy.get("Price"),
        "buy1_qty": None if buy is None else buy.get("Qty"),
        "buy1_time": None if buy is None else buy.get("Time"),
        "buy1_sign": None if buy is None else buy.get("Sign"),
        "sell1_price": None if sell is None else sell.get("Price"),
        "sell1_qty": None if sell is None else sell.get("Qty"),
        "bid_price_field": payload.get("BidPrice"),
        "bid_qty_field": payload.get("BidQty"),
        "ask_price_field": payload.get("AskPrice"),
        "ask_qty_field": payload.get("AskQty"),
        "cpt": payload.get("CurrentPriceTime"),
        "model_bid": ev.get("bid"),
        "model_bid_qty": ev.get("bid_qty"),
        "model_fresh": ev.get("fresh_sec"),
        "state": ev.get("state"),
    }


class Probe(QtyE):
    def __init__(self) -> None:
        super().__init__()
        self.pre_flat: list[dict[str, Any]] = []
        self.fires: list[dict[str, Any]] = []

    def _normalize(self, event: dict[str, Any]):
        ev = super()._normalize(event)
        if ev and not ev.get("buy1"):
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            ev["gap_clock"] = _clock(payload, ev)
        return ev

    def _on_path(self, sym: str, index: int, event_t: float, heap: list[tuple], flush_t: float) -> None:
        super()._on_path(sym, index, event_t, heap, flush_t)
        pos = self.open_pos.get(sym)
        if pos is None or pos.get("gap_logged"):
            return
        reason = str(pos.get("pending_reason") or "")
        if reason in {"BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED"}:
            pos["gap_logged"] = True
            self.fires.append(
                {
                    "uid": pos.get("signal_uid"),
                    "reason": reason,
                    "t": float(event_t),
                    "t_jst": _iso(event_t),
                    "support": pos.get("support"),
                }
            )

    def _ensure_flatten(self, now: float) -> None:
        if self.flatten_start is not None and float(now) + _EPS >= float(self.flatten_start) and not self._flatten_armed:
            for sym, pos in self.open_pos.items():
                self.pre_flat.append(
                    {
                        "uid": pos.get("signal_uid"),
                        "symbol": sym,
                        "pending_reason": pos.get("pending_reason"),
                        "exiting": bool(pos.get("exiting")),
                        "exit_trigger_t": pos.get("exit_trigger_t"),
                        "support": pos.get("support"),
                        "entry_px": pos.get("entry_px"),
                        "entry_t": pos.get("entry_t"),
                    }
                )
        super()._ensure_flatten(now)


def _unknown_rows(book: Probe) -> list[dict[str, Any]]:
    rows = []
    for trade in book.trades:
        symbol = str(trade["symbol"])
        exit_t = float(trade["exit_t"])
        exit_px = float(trade["exit_px"])
        found = None
        for item in book.bid_log.get(symbol, []):
            if abs(float(item["t"]) - exit_t) <= 1e-6 and abs(float(item["px"]) - exit_px) <= 1e-6:
                found = item
                break
        if found is not None and found.get("buy1"):
            continue
        clock = None if found is None else found.get("clock")
        cause = "NO_BID_LOG_MATCH" if found is None else "BUY1_QTY_ABSENT"
        rows.append(
            {
                "uid": trade["signal_uid"],
                "symbol": symbol,
                "reason": trade["reason"],
                "pnl_yen": trade["pnl_yen"],
                "entry_px": trade["entry_px"],
                "exit_px": exit_px,
                "exit_t_jst": _iso(exit_t),
                "trigger_jst": _iso(float(trade.get("exit_trigger_t") or exit_t)),
                "cause": cause,
                "model_qty": None if found is None else found.get("qty"),
                "model_fresh": None if found is None else found.get("fresh"),
                "clock": clock,
            }
        )
    return rows


def _feed(book: Probe, day: str, seq_after: int, attach: str) -> int:
    capture = _capture_dir(day)
    if capture is None:
        raise RuntimeError("NO_CAPTURE")
    fed = 0
    for part in sorted(capture.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("kind") not in (None, "market_push"):
                    continue
                if int(rec.get("sequence") or 0) <= seq_after:
                    continue
                received = str(rec.get("received_at") or "")
                if not received or (attach and received < attach):
                    continue
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
                if not payload:
                    continue
                payload = dict(payload)
                payload.setdefault("received_at", received)
                payload.setdefault("recorded_at", received)
                symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
                if not symbol or book._closed:
                    continue
                book.on_market_event({"symbol": symbol, "payload": payload})
                fed += 1
        if book._closed:
            break
    if not book._closed:
        book.close_at_runtime_boundary(day=day, kind="pm")
    return fed


def run_job(spec: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        _install_patch()
        day = str(spec["day"])
        book = Probe()
        book.notifier = None
        book.ledger_path = None
        bind_outer_registration(book, native_root=NATIVE, trading_date=day, universe=[])
        book.bind_safe_pm_flatten(day=day)
        book.gross_cap = G
        if len(book.admission_membership) != 50:
            return {"day": day, "error": "ADMISSION"}
        fed = _feed(book, day, int(spec["seq_after"]), str(spec["attach"]))
        gross = round(sum(float(trade["pnl_yen"]) for trade in book.trades), 6)
        unknown = _unknown_rows(book)
        wanted = set(spec.get("dossier_uids") or [])
        pre = [row for row in book.pre_flat if row.get("uid") in wanted]
        fires = [row for row in book.fires if row.get("uid") in wanted]
        for row in pre:
            if row.get("exit_trigger_t") is not None:
                row["exit_trigger_jst"] = _iso(float(row["exit_trigger_t"]))
            if row.get("entry_t") is not None:
                row["entry_jst"] = _iso(float(row["entry_t"]))
        fallback_bids = 0
        for rows in book.bid_log.values():
            fallback_bids += sum(1 for item in rows if not item.get("buy1"))
        ok = len(book.trades) == int(spec["expect_trades"]) and abs(gross - float(spec["expect_gross"])) < 0.05
        print(
            f"GAP {day} trades {len(book.trades)} gross {gross} unknown {len(unknown)} parity {ok} sec {time.perf_counter() - started:.1f}",
            flush=True,
        )
        return {
            "day": day,
            "trades": len(book.trades),
            "gross": gross,
            "unknown_n": len(unknown),
            "unknown": unknown,
            "fallback_executable_bids": fallback_bids,
            "pre_flat": pre,
            "fires": fires,
            "parity_ok": ok,
            "fed": fed,
            "elapsed_sec": time.perf_counter() - started,
        }
    except Exception:
        return {"day": spec.get("day"), "error": traceback.format_exc()}


def _specs() -> list[dict[str, Any]]:
    prior = json.loads(PRIOR.read_text(encoding="utf-8"))
    dossiers = {
        "20260825": ["20260825|PM|2351.T|89"],
        "20260902": ["20260902|PM|7376.T|463"],
    }
    specs = []
    for day, body in prior["days"].items():
        row = body["pm"]["E"]
        if int(row.get("exit_unknown") or 0) <= 0 and day not in dossiers:
            continue
        specs.append(
            {
                "day": day,
                "seq_after": 0,
                "attach": _attach_text(day, "pm"),
                "expect_trades": int(row["trades"]),
                "expect_gross": float(row["gross"]),
                "dossier_uids": dossiers.get(day, []),
            }
        )
    if "20260825" not in {item["day"] for item in specs}:
        row = prior["days"]["20260825"]["pm"]["E"]
        specs.append(
            {
                "day": "20260825",
                "seq_after": 0,
                "attach": _attach_text("20260825", "pm"),
                "expect_trades": int(row["trades"]),
                "expect_gross": float(row["gross"]),
                "dossier_uids": dossiers["20260825"],
            }
        )
    known = prior["known_20261009_pm"]["books"]["E"]
    specs.append(
        {
            "day": "20261009",
            "seq_after": 678498,
            "attach": "",
            "expect_trades": int(known["trades"]),
            "expect_gross": float(known["gross"]),
            "dossier_uids": ["20261009|PM|5246.T|13516"],
        }
    )
    return specs


def _save(body: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "unknown_facts.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    if not _frozen_ok():
        raise SystemExit("FROZEN_SHA_MISMATCH")
    busy = _busy()
    if busy and busy != ["PROCESS_CHECK_FAILED"]:
        raise SystemExit(f"PAPER_BUSY {busy}")
    specs = _specs()
    path = OUT / "unknown_facts.json"
    done: dict[str, Any] = {}
    if path.is_file():
        previous = json.loads(path.read_text(encoding="utf-8"))
        done = {day: row for day, row in (previous.get("days") or {}).items() if row.get("parity_ok")}
    pending = [spec for spec in specs if spec["day"] not in done]
    print(f"jobs {len(specs)} pending {len(pending)} busy {busy}", flush=True)
    started = time.perf_counter()
    if pending:
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(run_job, spec): spec["day"] for spec in pending}
            for future in as_completed(futures):
                result = future.result()
                done[str(result.get("day"))] = result
                _save({"running": True, "days": done, "elapsed_sec": time.perf_counter() - started})
                if result.get("error") or not result.get("parity_ok"):
                    print("PARITY_OR_ERROR", result.get("day"), result.get("error"), flush=True)
    _save({"running": False, "days": done, "elapsed_sec": time.perf_counter() - started, "jobs": len(specs)})
    unknown = sum(int((done.get(spec["day"]) or {}).get("unknown_n") or 0) for spec in specs if spec["day"] != "20261009")
    print("UNKNOWN_29D", unknown, "sec", round(time.perf_counter() - started, 1), flush=True)


if __name__ == "__main__":
    main()
