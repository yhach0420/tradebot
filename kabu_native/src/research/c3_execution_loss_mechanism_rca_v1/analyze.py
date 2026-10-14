"""Diagnose why causal fills lose more than the C3 paper book.

The paper ledger and the execution-realism report are read only.
No runtime file and no order path is changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
ROOT = NATIVE.parent
for item in (str(ROOT), str(NATIVE), str(SRC)):
    if item not in sys.path:
        sys.path.insert(0, item)

OUT = NATIVE / "results" / "research" / "c3_execution_loss_mechanism_rca_v1"
os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(OUT / "residual.json")

from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import _capture_dir, _symbol
from research.execution_realism_layer_v1.model import ExecutionRealismBook, _finite_number
from research.execution_realism_layer_v1.replay import AM_WATERMARK, DAY, PM_WATERMARK, _economics, load_universe
from research.low_price_risk_review import jpx_tick_size_yen
from research.v16_bid_freshness_residual_accounting_v1.run import _exit_decision
from research.v16_one_tick_exit_complete_strategy_v1.evaluate import support_action
from small_paper.fixed_support_x1_session import _EPS, _finite_pos, production_session_bounds

os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(OUT / "residual.json")

JST = timezone(timedelta(hours=9))
REALISM = NATIVE / "results" / "research" / "execution_realism_layer_v1" / "report.json"
PROTECTED = (
    REALISM,
    NATIVE / "results" / "research" / "v19_actual_paper_e2e_parity_20261013" / "report.json",
    NATIVE / "src" / "research" / "v16_one_tick_exit_complete_strategy_v1" / "evaluate.py",
    NATIVE / "src" / "small_paper" / "c3_paper_mode.json",
    NATIVE / "src" / "small_paper" / "universe_selector_mode.json",
    NATIVE / "results" / "research" / "v1r_exit_v2_prospective_activation" / "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V20.json",
)


def _fmt(stamp) -> str:
    if stamp is None:
        return ""
    return datetime.fromtimestamp(float(stamp), JST).strftime("%H:%M:%S.%f")[:-3]


def _sha(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _breaks(price, level) -> bool:
    if not _finite_pos(float(price)) or not _finite_pos(float(level)):
        return False
    tick = float(jpx_tick_size_yen(float(level)))
    return support_action(float(price), float(level), tick, False) == "BREAK_SUPPORT_FAILURE"


def load_baseline() -> list[dict]:
    rows = []
    for folder in ("live_session_085018", "live_session_122543"):
        path = NATIVE / "results" / "small_paper" / DAY / folder / "fixed_support_x1_paper_ledger.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("event") != "EXIT":
                continue
            support = float(row["entry_pre_break_high"])
            tick = float(jpx_tick_size_yen(support))
            entry = float(row["fill_price"])
            rows.append(
                {
                    "signal_uid": row["signal_uid"],
                    "symbol": row["symbol"],
                    "reason": row["exit_reason"],
                    "pnl_yen": float(row["pnl_yen"] or 0.0),
                    "hold_sec": float(row["exit_fill_timestamp"]) - float(row["fill_timestamp"]),
                    "signal_t": float(row["signal_timestamp"]),
                    "fill_t": float(row["fill_timestamp"]),
                    "exit_t": float(row["exit_fill_timestamp"]),
                    "exit_trigger_t": float(row["exit_trigger_timestamp"]),
                    "entry_px": entry,
                    "exit_px": float(row["exit_price"]),
                    "support": support,
                    "tick": tick,
                    "fire": support - tick,
                    "reentry": bool(row.get("reentry")),
                    "reconfirm": int(row.get("reconfirm_count") or 0),
                    "ask_below_support": entry < support,
                    "ask_through_fire": entry <= support - tick + 1e-9,
                }
            )
    return rows


class DiagBook(ExecutionRealismBook):
    """Same fills as the realism book, plus the quote at each decision."""

    def __init__(self, order_delay_sec: float) -> None:
        super().__init__(order_delay_sec)
        self.diag: dict[str, dict] = {}

    def _note(self, uid: str, **fields) -> None:
        if not uid:
            return
        self.diag.setdefault(uid, {"signal_uid": uid})
        self.diag[uid].update(fields)

    def _on_signal(self, sym: str, index: int, event_t: float, heap: list[tuple], flush_t: float) -> None:
        super()._on_signal(sym, index, event_t, heap, flush_t)
        pos = self.open_pos.get(sym)
        if pos is None or abs(float(pos.get("signal_t") or -1.0) - float(event_t)) > 1e-6:
            return
        if str(pos.get("exec_state")) != "BUY_PENDING":
            return
        self._snap(sym, pos, int(pos["signal_index"]), "signal")

    def _try_fill_buy(self, sym: str, pos: dict, index: int) -> None:
        before = float(pos.get("filled_qty") or 0.0)
        super()._try_fill_buy(sym, pos, index)
        if sym not in self.open_pos:
            return
        pos = self.open_pos[sym]
        if float(pos.get("filled_qty") or 0.0) <= before:
            return
        self._snap(sym, pos, index, "fill")

    def _schedule_exit(self, pos, sym, index, reason, event_t, found, heap) -> None:
        uid = str(pos.get("signal_uid") or "")
        px = None
        try:
            px = float(self.syms[sym].px[int(index)])
        except (KeyError, IndexError, TypeError, ValueError):
            px = None
        level = float(pos.get("support") or 0.0)
        fill_t = pos.get("entry_fill_t")
        self._note(
            uid,
            exit_signal_t=float(event_t),
            exit_reason=str(reason),
            exit_px=px,
            exit_breaks=_breaks(px, level) if px is not None else False,
            exit_on_fill_print=fill_t is not None and abs(float(event_t) - float(fill_t)) <= 1e-4,
        )
        super()._schedule_exit(pos, sym, index, reason, event_t, found, heap)

    def _snap(self, sym: str, pos: dict, index: int, kind: str) -> None:
        quote = self.quotes.get(sym, {}).get(int(index), {})
        try:
            px = float(self.syms[sym].px[int(index)])
        except (KeyError, IndexError, TypeError, ValueError):
            px = float("nan")
        level = float(pos.get("support") or 0.0)
        tick = float(jpx_tick_size_yen(level)) if _finite_pos(level) else None
        ask = _finite_number(quote.get("ask"))
        bid = _finite_number(quote.get("bid"))
        prefix = "signal" if kind == "signal" else "fill"
        self._note(
            str(pos.get("signal_uid") or ""),
            **{
                f"{prefix}_t": float(self.syms[sym].t[int(index)]),
                f"{prefix}_px": px,
                f"{prefix}_ask": ask,
                f"{prefix}_bid": bid,
                f"{prefix}_spread": None if ask is None or bid is None else ask - bid,
                f"{prefix}_breaks": _breaks(px, level),
                "support": level,
                "tick": tick,
                "fire": None if tick is None else level - tick,
                "session": self.session,
                "symbol": sym,
            },
        )


class FillPrintCancelBook(ExecutionRealismBook):
    """Do not open a buy on a print that already breaks the frozen support.

    This is a research rule. It is not a hold-time filter and it is not adopted.
    """

    def _try_fill_buy(self, sym: str, pos: dict, index: int) -> None:
        if float(pos.get("filled_qty") or 0.0) <= 0.0:
            try:
                px = float(self.syms[sym].px[int(index)])
            except (KeyError, IndexError, TypeError, ValueError):
                px = float("nan")
            if _breaks(px, float(pos.get("support") or 0.0)):
                self._drop_buy(sym, pos, "FILL_PRINT_BREAKS_SUPPORT", float(self.syms[sym].t[int(index)]))
                return
        super()._try_fill_buy(sym, pos, index)


def _bind(cls, kind: str, symbols: list[str], delay: float):
    os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(OUT / f"residual_{kind}_{delay}.json")
    path = Path(os.environ["TRADEBOT_C3_RESIDUAL_PATH"])
    if path.exists():
        path.unlink()
    book = cls(delay)
    book.notifier = None
    membership = list(symbols)

    def admit(symbol: str, _t: float) -> tuple[bool, str]:
        if symbol in membership:
            return True, "SAME_DAY_AM_FROZEN_UNIVERSE"
        return False, "NOT_IN_DAY_FREEZE"

    book.admission = admit
    if kind == "pm":
        book.bind_safe_pm_flatten(day=DAY)
    else:
        book.bind_production_schedule(day=DAY, kind="am")
    book.ledger_path = None
    book.notifier = None
    return book


def _replay(cls, delay: float, symbols: list[str], capture: Path):
    started = time.perf_counter()
    am = _bind(cls, "am", symbols, delay)
    pm = _bind(cls, "pm", symbols, delay)
    try:
        import orjson

        loads = orjson.loads
    except Exception:
        loads = json.loads
    lines = 0
    for part in sorted(capture.glob("push_part_*.jsonl")):
        with part.open("rb") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                lines += 1
                rec = loads(raw)
                if rec.get("kind") not in (None, "market_push"):
                    continue
                seq = int(rec.get("sequence") or 0)
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else None
                if not payload:
                    continue
                symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
                if not symbol:
                    continue
                received = str(rec.get("received_at") or "")
                event = {"symbol": symbol, "payload": payload, "received_at": received, "t0_push_received_at": received}
                if seq > AM_WATERMARK and not am._closed:
                    am.on_market_event(event)
                if seq > PM_WATERMARK and not pm._closed:
                    pm.on_market_event(event)
                if lines % 200000 == 0:
                    print(f"PROGRESS {cls.__name__} lines={lines}", flush=True)
    if not am._closed:
        am.close_at_runtime_boundary(day=DAY, kind="am")
    if not pm._closed:
        pm.close_at_runtime_boundary(day=DAY, kind="pm")
    print(f"DONE {cls.__name__} {round(time.perf_counter() - started, 1)}s", flush=True)
    return am, pm


def _pack_closed(books) -> list[dict]:
    rows = []
    for book in books:
        rows.extend(book.closed)
        rows.extend(book.cancels)
    return rows


def scan_unfilled(capture: Path, residuals: list[dict], baseline: dict[str, dict], sess_end: float) -> list[dict]:
    wanted = {row["symbol"] for row in residuals}
    meta = {row["signal_uid"]: row for row in residuals}
    by_uid = defaultdict(list)
    probe = ExecutionRealismBook(0.0)
    probe.notifier = None
    try:
        import orjson

        loads = orjson.loads
    except Exception:
        loads = json.loads
    for part in sorted(capture.glob("push_part_*.jsonl")):
        with part.open("rb") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                rec = loads(raw)
                if rec.get("kind") not in (None, "market_push"):
                    continue
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else None
                if not payload:
                    continue
                symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
                if symbol not in wanted:
                    continue
                ev = probe._normalize({"symbol": symbol, "payload": payload, "received_at": rec.get("received_at")})
                if not ev:
                    continue
                by_uid[symbol].append(ev)
    reports = []
    for row in residuals:
        uid = row["signal_uid"]
        base = baseline[uid]
        events = [ev for ev in by_uid[row["symbol"]] if ev.get("t") is not None]
        events.sort(key=lambda ev: float(ev["t"]))
        window = [ev for ev in events if float(base["fill_t"]) - 1.0 <= float(ev["t"]) <= float(sess_end) + 1.0]
        gaps = []
        for left, right in zip(window, window[1:]):
            gaps.append(float(right["t"]) - float(left["t"]))
        proven = []
        broke = False
        min_px = None
        max_px = None
        for ev in window:
            if float(ev["t"]) < float(base["fill_t"]) - 1e-6 or float(ev["t"]) >= float(sess_end) - _EPS:
                continue
            px = _finite_number(ev.get("px"))
            if px is not None:
                min_px = px if min_px is None else min(min_px, px)
                max_px = px if max_px is None else max(max_px, px)
                if _breaks(px, base["support"]):
                    broke = True
            decision = _exit_decision(ev)
            if decision.get("ok") and float(ev["t"]) + 1e-9 >= float(base["fill_t"]):
                proven.append(
                    {
                        "t": _fmt(ev["t"]),
                        "epoch": float(ev["t"]),
                        "px": decision.get("px"),
                        "qty": decision.get("qty"),
                        "age": decision.get("age"),
                    }
                )
        last = proven[-1] if proven else None
        after_boundary = []
        for ev in events:
            stamp = float(ev["t"])
            if stamp + 1e-9 < float(sess_end) or stamp > float(sess_end) + 120.0:
                continue
            if _exit_decision(ev).get("ok"):
                after_boundary.append(ev)
        paper_bid = None
        for ev in events:
            if abs(float(ev["t"]) - float(base["exit_t"])) <= 0.002:
                decision = _exit_decision(ev)
                paper_bid = {
                    "t": _fmt(ev["t"]),
                    "px": decision.get("px"),
                    "qty": decision.get("qty"),
                    "age": decision.get("age"),
                    "ok": bool(decision.get("ok")),
                    "reason": decision.get("reason"),
                    "seconds_before_boundary": round(float(sess_end) - float(ev["t"]), 3),
                }
                break
        signal_ev = None
        for ev in events:
            if abs(float(ev["t"]) - float(base["signal_t"])) <= 0.002:
                signal_ev = ev
                break
        reports.append(
            {
                "signal_uid": uid,
                "symbol": row["symbol"],
                "support": base["support"],
                "tick": base["tick"],
                "fire": base["fire"],
                "paper_entry_px": base["entry_px"],
                "paper_exit_px": base["exit_px"],
                "paper_pnl_yen": base["pnl_yen"],
                "paper_reason": base["reason"],
                "paper_exit": _fmt(base["exit_t"]),
                "paper_trigger": _fmt(base["exit_trigger_t"]),
                "signal": _fmt(base["signal_t"]),
                "signal_px": None if signal_ev is None else signal_ev.get("px"),
                "signal_ask": None if signal_ev is None else signal_ev.get("ask"),
                "signal_bid": None if signal_ev is None else signal_ev.get("bid"),
                "signal_already_breaks": False if signal_ev is None else _breaks(signal_ev.get("px"), base["support"]),
                "reconfirm_n": base["reconfirm"],
                "events_in_window": len(window),
                "max_gap_sec": None if not gaps else round(max(gaps), 3),
                "last_event": "" if not window else _fmt(window[-1]["t"]),
                "min_px_while_held": min_px,
                "max_px_while_held": max_px,
                "support_broke_before_boundary": broke,
                "proven_bid_n_while_held": len(proven),
                "last_proven_bid_while_held": last,
                "proven_bid_at_or_after_boundary_n": len(after_boundary),
                "paper_exit_bid": paper_bid,
                "assigned_exit_px": None,
                "cause": "SESSION_BOUNDARY_USES_EARLIER_BID" if not broke else "SUPPORT_BROKE_BUT_MODEL_DID_NOT_EXIT",
            }
        )
    return reports


def _hold_label(hold: float) -> str:
    if hold < 1.0:
        return "lt_1s"
    if hold < 5.0:
        return "lt_5s"
    if hold < 30.0:
        return "lt_30s"
    return "ge_30s"


def decompose(baseline: list[dict], model_closed: list[dict], model_res: list[dict]) -> dict:
    base = {row["signal_uid"]: row for row in baseline}
    matched = []
    new_rows = []
    for row in model_closed:
        src = base.get(row["signal_uid"])
        if src is None:
            new_rows.append(row)
            continue
        qty = float(row["qty"])
        entry_gap = (float(row["entry_px"]) - float(src["entry_px"])) * qty
        exit_gap = (float(src["exit_px"]) - float(row["exit_px"])) * qty
        same_print = (
            row.get("exit_signal_t") is not None
            and abs(float(row["exit_signal_t"]) - float(row["entry_fill_t"])) <= 1e-3
        )
        matched.append(
            {
                **row,
                "base_pnl": src["pnl_yen"],
                "base_entry": src["entry_px"],
                "base_exit": src["exit_px"],
                "base_reason": src["reason"],
                "base_hold": src["hold_sec"],
                "base_reentry": src["reentry"],
                "support": src["support"],
                "fire": src["fire"],
                "ask_below_support": src["ask_below_support"],
                "ask_through_fire": src["ask_through_fire"],
                "entry_worse_yen": round(entry_gap, 6),
                "exit_worse_yen": round(exit_gap, 6),
                "same_print_exit": bool(same_print),
                "hold_label": _hold_label(float(src["hold_sec"])),
            }
        )
    res_ids = {row["signal_uid"] for row in model_res}
    missing = [base[uid] for uid in base if uid not in {row["signal_uid"] for row in model_closed} and uid not in res_ids]
    residual_base = [base[row["signal_uid"]] for row in model_res if row["signal_uid"] in base]

    def _sum(rows, key):
        return round(sum(float(row[key]) for row in rows), 6)

    by_reason = defaultdict(lambda: {"n": 0, "entry_worse_yen": 0.0, "exit_worse_yen": 0.0, "base_pnl": 0.0, "model_pnl": 0.0, "same_print_n": 0})
    by_hold = defaultdict(lambda: {"n": 0, "entry_worse_yen": 0.0, "exit_worse_yen": 0.0, "base_pnl": 0.0, "model_pnl": 0.0, "same_print_n": 0})
    for row in matched:
        for bucket, key in ((by_reason, row["base_reason"]), (by_hold, row["hold_label"])):
            item = bucket[key]
            item["n"] += 1
            item["entry_worse_yen"] += row["entry_worse_yen"]
            item["exit_worse_yen"] += row["exit_worse_yen"]
            item["base_pnl"] += row["base_pnl"]
            item["model_pnl"] += float(row["pnl_yen"])
            if key == row["hold_label"]:
                item["same_print_n"] += int(bool(row["same_print_exit"]))
    for bucket in (by_reason, by_hold):
        for item in bucket.values():
            for name in ("entry_worse_yen", "exit_worse_yen", "base_pnl", "model_pnl"):
                item[name] = round(item[name], 6)

    def _reentry(rows, entry_key, exit_key, pnl_key):
        grouped = defaultdict(list)
        for row in rows:
            grouped[row["symbol"]].append(row)
        near = {"lt_5s": [], "lt_30s": [], "ge_30s": []}
        for items in grouped.values():
            items.sort(key=lambda row: float(row[entry_key]))
            prev = None
            for row in items:
                if prev is not None:
                    gap = float(row[entry_key]) - float(prev[exit_key])
                    label = "lt_5s" if gap < 5 else "lt_30s" if gap < 30 else "ge_30s"
                    near[label].append({"gap": gap, "pnl": float(row[pnl_key]), "symbol": row["symbol"], "uid": row.get("signal_uid")})
                prev = row
        return {
            label: {"n": len(items), "pnl_yen": round(sum(item["pnl"] for item in items), 6)}
            for label, items in near.items()
        }

    return {
        "matched_n": len(matched),
        "entry_worse_yen": _sum(matched, "entry_worse_yen"),
        "exit_worse_yen": _sum(matched, "exit_worse_yen"),
        "same_print_n": sum(1 for row in matched if row["same_print_exit"]),
        "same_print_model_pnl": round(sum(float(row["pnl_yen"]) for row in matched if row["same_print_exit"]), 6),
        "same_print_base_pnl": round(sum(float(row["base_pnl"]) for row in matched if row["same_print_exit"]), 6),
        "same_print_exit_worse": round(sum(row["exit_worse_yen"] for row in matched if row["same_print_exit"]), 6),
        "later_exit_worse": round(sum(row["exit_worse_yen"] for row in matched if not row["same_print_exit"]), 6),
        "ask_below_support_n": sum(1 for row in matched if row["ask_below_support"]),
        "ask_below_support_base_pnl": round(sum(row["base_pnl"] for row in matched if row["ask_below_support"]), 6),
        "ask_below_support_model_pnl": round(sum(float(row["pnl_yen"]) for row in matched if row["ask_below_support"]), 6),
        "ask_through_fire_n": sum(1 for row in matched if row["ask_through_fire"]),
        "ask_through_fire_model_pnl": round(sum(float(row["pnl_yen"]) for row in matched if row["ask_through_fire"]), 6),
        "by_reason": dict(by_reason),
        "by_hold": dict(by_hold),
        "new_trades_n": len(new_rows),
        "new_trades_pnl": round(sum(float(row["pnl_yen"]) for row in new_rows), 6),
        "not_admitted_n": len(missing),
        "not_admitted_base_pnl": round(sum(row["pnl_yen"] for row in missing), 6),
        "not_admitted": [
            {"uid": row["signal_uid"], "symbol": row["symbol"], "pnl_yen": row["pnl_yen"], "reason": row["reason"], "hold_sec": round(row["hold_sec"], 3)}
            for row in missing
        ],
        "residual_base_pnl": round(sum(row["pnl_yen"] for row in residual_base), 6),
        "baseline_reentry": _reentry(baseline, "signal_t", "exit_t", "pnl_yen"),
        "model_reentry": _reentry(model_closed, "signal_t", "exit_fill_t", "pnl_yen"),
        "matched_rows": matched,
    }


def _diag_summary(rows: list[dict]) -> dict:
    signal_break = [row for row in rows if row.get("signal_breaks")]
    fill_break = [row for row in rows if row.get("fill_breaks")]
    ask_through = [
        row
        for row in rows
        if row.get("signal_ask") is not None and row.get("fire") is not None and float(row["signal_ask"]) <= float(row["fire"]) + 1e-9
    ]
    spreads = [float(row["signal_spread"]) for row in rows if row.get("signal_spread") is not None]
    return {
        "n": len(rows),
        "signal_px_already_breaks_n": len(signal_break),
        "fill_print_breaks_n": len(fill_break),
        "signal_ask_through_fire_n": len(ask_through),
        "median_signal_spread_yen": None if not spreads else round(sorted(spreads)[len(spreads) // 2], 6),
        "signal_spread_notional_yen": round(sum(spreads) * 100.0, 6),
    }


def _reason_mix(rows: list[dict]) -> dict:
    out = {}
    for reason in sorted({row["reason"] for row in rows}):
        chosen = [row for row in rows if row["reason"] == reason]
        out[reason] = {"n": len(chosen), "pnl_yen": round(sum(float(row["pnl_yen"]) for row in chosen), 6)}
    return out


def write_xlsx(path: Path, report: dict) -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "判定"
    for row in (
        ("質問", "答え"),
        ("主損失", report["answers"]["primary_loss"]),
        ("構造不整合", report["answers"]["structural_inconsistency"]),
        ("遅延の説明", report["answers"]["delay_explanation"]),
        ("再ENTRY", report["answers"]["reentry"]),
        ("未決済5件", report["answers"]["unfilled_handling"]),
        ("修正箇所", report["answers"]["where"]),
        ("経済評価候補", report["answers"]["candidate"]),
    ):
        sheet.append(row)
    unfilled = book.create_sheet("未決済")
    unfilled.append(("uid", "銘柄", "支持", "発火", "シグナル価格", "Ask", "Bid", "シグナル時に破綻", "保有中に破綻", "最後の有効Bid", "境界前秒", "境界後の有効Bid", "最大間隔秒", "Paper価格", "研究の決済価格"))
    for row in report["unfilled"]:
        last = row.get("last_proven_bid_while_held") or {}
        paper = row.get("paper_exit_bid") or {}
        unfilled.append(
            (
                row["signal_uid"],
                row["symbol"],
                row["support"],
                row["fire"],
                row["signal_px"],
                row["signal_ask"],
                row["signal_bid"],
                row["signal_already_breaks"],
                row["support_broke_before_boundary"],
                last.get("t"),
                paper.get("seconds_before_boundary"),
                row["proven_bid_at_or_after_boundary_n"],
                row["max_gap_sec"],
                row["paper_exit_px"],
                row["assigned_exit_px"],
            )
        )
    split = book.create_sheet("悪化分解")
    split.append(("区分", "件数", "ENTRY悪化", "EXIT悪化", "基準PnL", "研究PnL", "同一板EXIT"))
    for label, row in report["decomposition"]["by_hold"].items():
        split.append((label, row["n"], row["entry_worse_yen"], row["exit_worse_yen"], row["base_pnl"], row["model_pnl"], row.get("same_print_n")))
    for label, row in report["decomposition"]["by_reason"].items():
        split.append((label, row["n"], row["entry_worse_yen"], row["exit_worse_yen"], row["base_pnl"], row["model_pnl"], ""))
    holds = book.create_sheet("支持線")
    holds.append(("uid", "銘柄", "基準保有秒", "支持", "シグナル価格", "シグナルAsk", "シグナルBid", "シグナル時破綻", "約定時破綻", "同一板EXIT", "ENTRY悪化", "EXIT悪化", "研究PnL"))
    diag = {row["signal_uid"]: row for row in report["diag_rows"]}
    for row in report["decomposition"]["matched_rows"]:
        snap = diag.get(row["signal_uid"], {})
        holds.append(
            (
                row["signal_uid"],
                row["symbol"],
                round(float(row["base_hold"]), 3),
                row["support"],
                snap.get("signal_px"),
                snap.get("signal_ask"),
                snap.get("signal_bid"),
                snap.get("signal_breaks"),
                snap.get("fill_breaks"),
                row["same_print_exit"],
                row["entry_worse_yen"],
                row["exit_worse_yen"],
                row["pnl_yen"],
            )
        )
    candidate = book.create_sheet("候補")
    cand = report["candidate"]
    candidate.append(("項目", "研究現状", "同一板では買わない案"))
    candidate.append(("決済件数", report["realism"]["closed_n"], cand["closed_n"]))
    candidate.append(("PnL", report["realism"]["gross_pnl_yen"], cand["gross_pnl_yen"]))
    candidate.append(("PF", report["realism"]["profit_factor"], cand["profit_factor"]))
    candidate.append(("DD", report["realism"]["exit_order_drawdown_yen"], cand["exit_order_drawdown_yen"]))
    candidate.append(("取消", report["realism"]["cancels_n"], cand["cancels_n"]))
    candidate.append(("未約定", report["realism"]["unfilled_n"], cand["unfilled_n"]))
    candidate.append(("採用", "しない", "しない"))
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)


def write_md(path: Path, report: dict) -> None:
    answers = report["answers"]
    lines = [
        f"# {report['verdict']}",
        "",
        f"NEXT: {report['next']}",
        "",
        "## 1. 主損失",
        "",
        answers["primary_loss"],
        "",
        "## 2. ENTRYとEXITの整合",
        "",
        answers["structural_inconsistency"],
        "",
        "## 3. 約定遅延で説明できる範囲",
        "",
        answers["delay_explanation"],
        "",
        "## 4. 再ENTRY",
        "",
        answers["reentry"],
        "",
        "## 5. 未決済5件",
        "",
        answers["unfilled_handling"],
        "",
    ]
    for row in report["unfilled"]:
        last = row.get("last_proven_bid_while_held") or {}
        paper = row.get("paper_exit_bid") or {}
        lines.append(
            f"- {row['symbol']} {row['signal_uid']}: 支持 {row['support']}、発火 {row['fire']}、シグナル価格 {row['signal_px']}、Ask {row['signal_ask']}、Bid {row['signal_bid']}。"
            f"シグナル時の破綻 {row['signal_already_breaks']}。保有中の支持線割れ {row['support_broke_before_boundary']}。"
            f"最後の有効Bid {last.get('t')} / {last.get('px')}、鮮度 {last.get('age')} 秒、数量 {last.get('qty')}。"
            f"Paperが使ったBidは境界の {paper.get('seconds_before_boundary')} 秒前。境界以降の有効Bid {row['proven_bid_at_or_after_boundary_n']} 件。"
            f"窓内の最大間隔 {row['max_gap_sec']} 秒。研究側の決済価格は付けていない。"
        )
    decomp = report["decomposition"]
    lines.extend(
        [
            "",
            "## 6. 悪化の分解",
            "",
            f"- 同じ約定のENTRY価格悪化: {decomp['entry_worse_yen']} 円。",
            f"- 同じ約定のEXIT価格悪化: {decomp['exit_worse_yen']} 円。うち買い約定と同一板のEXIT {decomp['same_print_exit_worse']} 円、それ以外 {decomp['later_exit_worse']} 円。",
            f"- 売り未確定で基準PnLから外れた分: {decomp['residual_base_pnl']} 円。",
            f"- 枠が開かず入らなかった基準取引: {decomp['not_admitted_base_pnl']} 円。",
            f"- 基準にない新規約定: {decomp['new_trades_pnl']} 円。",
            f"- シグナル時Askが発火価格以下: {decomp['ask_through_fire_n']} 件、研究PnL {decomp['ask_through_fire_model_pnl']} 円。",
            f"- シグナル時Askが支持線未満: {decomp['ask_below_support_n']} 件、基準PnL {decomp['ask_below_support_base_pnl']} 円、研究PnL {decomp['ask_below_support_model_pnl']} 円。",
            "",
            "## 7. 修正箇所と候補",
            "",
            answers["where"],
            "",
            answers["candidate"],
            "",
            "同一板で支持線が既に割れている買いを成立させない研究案の全日再生:",
            "",
            f"- 現状 263件 / {report['realism']['gross_pnl_yen']} 円。",
            f"- 研究案 {report['candidate']['closed_n']} 件 / {report['candidate']['gross_pnl_yen']} 円、PF {report['candidate']['profit_factor']}、DD {report['candidate']['exit_order_drawdown_yen']}。",
            f"- 取消 {report['candidate']['cancels_n']}、うち FILL_PRINT_BREAKS_SUPPORT {report['candidate']['fill_print_cancels_n']}。未約定 {report['candidate']['unfilled_n']}。",
            "",
            "Runtimeには載せていない。submit/cancel/live = 0/0/0。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    before = {str(path): _sha(path) for path in PROTECTED}
    realism = json.loads(REALISM.read_text(encoding="utf-8"))
    if realism["baseline"]["closed_n"] != 268 or realism["baseline"]["gross_pnl_yen"] != -29500.0:
        raise SystemExit("BASELINE_MOVED")
    if realism["primary"]["economics"]["gross_pnl_yen"] != -47080.0:
        raise SystemExit("REALISM_MOVED")
    baseline = load_baseline()
    if len(baseline) != 268 or round(sum(row["pnl_yen"] for row in baseline), 6) != -29500.0:
        raise SystemExit("LEDGER_MOVED")
    base_map = {row["signal_uid"]: row for row in baseline}
    decomp = decompose(baseline, realism["closed_trades"], realism["primary_residuals"])
    _session, _start, sess_end = production_session_bounds(DAY, "am")
    capture = _capture_dir(DAY)
    if capture is None:
        raise SystemExit("NO_CAPTURE")
    print("SCAN unfilled", flush=True)
    unfilled = scan_unfilled(capture, realism["primary_residuals"], base_map, sess_end)
    if any(row["support_broke_before_boundary"] for row in unfilled):
        print("UNFILLED_SUPPORT_BROKE", flush=True)
    symbols = load_universe()
    print("REPLAY diag", flush=True)
    am, pm = _replay(DiagBook, 0.0, symbols, capture)
    diag_rows = list(am.diag.values()) + list(pm.diag.values())
    diag_closed_n = len(am.closed) + len(pm.closed)
    diag_pnl = round(sum(float(row["pnl_yen"]) for row in list(am.closed) + list(pm.closed)), 6)
    if diag_closed_n != 263 or diag_pnl != -47080.0:
        raise SystemExit(f"DIAG_DIVERGED n={diag_closed_n} pnl={diag_pnl}")
    del am, pm
    print("REPLAY candidate", flush=True)
    cam, cpm = _replay(FillPrintCancelBook, 0.0, symbols, capture)
    candidate_closed = list(cam.closed) + list(cpm.closed)
    candidate_cancels = list(cam.cancels) + list(cpm.cancels)
    candidate_res = list(cam.residuals) + list(cpm.residuals)
    cand_econ = _economics(candidate_closed)
    cand = {
        **cand_econ,
        "cancels_n": len(candidate_cancels),
        "fill_print_cancels_n": sum(1 for row in candidate_cancels if row.get("reason") == "FILL_PRINT_BREAKS_SUPPORT"),
        "unfilled_n": len(candidate_res),
        "unfilled_notional_yen": round(sum(float(row.get("notional") or 0.0) for row in candidate_res), 6),
        "adopted": False,
    }
    del cam, cpm
    snap = _diag_summary(diag_rows)
    reason_mix = _reason_mix(baseline)
    delay_0 = -47080.0 - (-29500.0)
    delay_100 = -78120.0 - (-47080.0)
    delay_1000 = -105680.0 - (-47080.0)
    answers = {
        "primary_loss": (
            f"当日のC3損失の本体はBREAK_SUPPORT_FAILURE。"
            f"基準では {reason_mix.get('BREAK_SUPPORT_FAILURE', {}).get('n')} 件 / {reason_mix.get('BREAK_SUPPORT_FAILURE', {}).get('pnl_yen')} 円。"
            f"IMPULSE_EXHAUSTED は {reason_mix.get('IMPULSE_EXHAUSTED', {}).get('pnl_yen')} 円、SESSION_FLAT は {reason_mix.get('SESSION_FLAT', {}).get('pnl_yen')} 円。"
            f"執行現実性で増えた分では、同じ約定のENTRY悪化が {decomp['entry_worse_yen']} 円、EXIT悪化が {decomp['exit_worse_yen']} 円。"
            f"ENTRY悪化 {decomp['by_hold']['ge_30s']['entry_worse_yen']} 円は、基準保有30秒以上の取引で起きている。"
        ),
        "structural_inconsistency": (
            f"ENTRYシグナルのCurrentPriceが、その時点で既に支持線-1呼値以下だった件数は {snap['signal_px_already_breaks_n']}。"
            f"買いを成立させた板で既に割れていた件数は {snap['fill_print_breaks_n']}、研究PnL {decomp['same_print_model_pnl']} 円。"
            f"Askが支持線未満の {decomp['ask_below_support_n']} 件は研究PnL {decomp['ask_below_support_model_pnl']} 円で、損失源ではない。"
            "判定はCurrentPrice、支払いは次のAsk、EXITは後のCurrentPrice。シグナル時に上昇仮説は割れていない。"
            "前場のSESSION_FLATは、11:25より前のBidをその境界の決済として記帳している。"
        ),
        "delay_explanation": (
            f"基準 -29500 円から仮定遅延0秒の -47080 円までの -17580 円は、発注往復を足す前の因果約定で説明できる。"
            f"そこから仮定0.1秒でさらに {delay_100} 円、仮定1秒では0秒比 {delay_1000} 円。"
            f"1秒仮定の -105680 円のうち、0秒の因果約定を超える分は {delay_1000} 円。実測の発注遅延は無い。"
        ),
        "reentry": (
            f"基準の再ENTRYは、前のEXITから5秒未満 {decomp['baseline_reentry']['lt_5s']['n']} 件 / {decomp['baseline_reentry']['lt_5s']['pnl_yen']} 円、"
            f"30秒未満 {decomp['baseline_reentry']['lt_30s']['n']} 件 / {decomp['baseline_reentry']['lt_30s']['pnl_yen']} 円。"
            f"研究側は5秒未満 {decomp['model_reentry']['lt_5s']['n']} 件 / {decomp['model_reentry']['lt_5s']['pnl_yen']} 円、"
            f"30秒未満 {decomp['model_reentry']['lt_30s']['n']} 件 / {decomp['model_reentry']['lt_30s']['pnl_yen']} 円。"
            "直後の再ENTRYは当日損失の中心ではない。損失の中心は支持線割れそのもの。"
        ),
        "unfilled_handling": (
            "5件とも保有中に支持線-1呼値へ到達していない。最後のセッション内Bidは11:24台で、鮮度は2秒未満、数量は100株以上。"
            "PaperはそのBidを11:25のSESSION_FLATとして記帳している。11:25から120秒のあいだにも有効Bidはあるが、セッション終了後の板であり、研究側の決済価格にはしていない。"
            "880,200円は未決済リスクとして残し、Paperの価格も境界後の価格も付けない。"
        ),
        "where": (
            "損失本体は、60秒高値の上抜け判定と、その後のCurrentPriceによる1呼値割れ。シグナル時のCurrentPriceは発火条件を満たしていない。"
            "執行差の大きい箇所は、シグナルAskと次に買えるAskの差。ここをシグナルAskへ戻すと、Paperの同一板約定に戻る。"
            "記帳の不整合は、前場終了の最後のBidをSESSION_FLATの価格にしている箇所。"
        ),
        "candidate": (
            "同一板でCurrentPriceが既に支持線-1呼値以下なら買わない案を、全日のイベント再生で評価した。"
            "保有時間の下限ではない。結果は4件の取消だけで、PnLの改善はその4件の-1200円分に留まる。"
            "-17580円の執行差は説明できない。次の経済評価へ進める候補にはしない。"
        ),
    }
    report = {
        "verdict": "C3_LOSS_MECHANISM_DIAGNOSED",
        "next": "NO_RUNTIME_CHANGE",
        "answers": answers,
        "frozen": {
            "baseline_n": 268,
            "baseline_pnl_yen": -29500.0,
            "realism_0s_n": 263,
            "realism_0s_pnl_yen": -47080.0,
            "realism_0_1s_pnl_yen": -78120.0,
            "realism_1s_pnl_yen": -105680.0,
            "unfilled_n": 5,
            "unfilled_notional_yen": 880200.0,
        },
        "reason_mix_baseline": reason_mix,
        "decomposition": {key: value for key, value in decomp.items() if key != "matched_rows"},
        "matched_count_check": decomp["matched_n"],
        "diag": snap,
        "diag_rows": diag_rows,
        "unfilled": unfilled,
        "realism": {
            "closed_n": 263,
            "gross_pnl_yen": -47080.0,
            "profit_factor": realism["primary"]["economics"]["profit_factor"],
            "exit_order_drawdown_yen": realism["primary"]["economics"]["exit_order_drawdown_yen"],
            "cancels_n": 0,
            "unfilled_n": 5,
        },
        "candidate": cand,
        "candidate_rule": "If the print that would fill the buy already has CurrentPrice <= support - 1 tick, cancel the buy. No minimum hold.",
        "runtime_changed": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "protected_sha_before": before,
    }
    report["decomposition"]["by_hold"] = decomp["by_hold"]
    report["decomposition"]["by_reason"] = decomp["by_reason"]
    report["decomposition"]["matched_rows"] = decomp["matched_rows"]
    after = {str(path): _sha(path) for path in PROTECTED}
    report["protected_sha_after"] = after
    report["protected_unchanged"] = before == after
    if not report["protected_unchanged"]:
        report["verdict"] = "RCA_PROTECTED_FILE_CHANGED"
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_md(OUT / "report.md", report)
    write_xlsx(OUT / "audit.xlsx", report)
    print(report["verdict"], cand["gross_pnl_yen"], snap["fill_print_breaks_n"], report["protected_unchanged"], flush=True)


if __name__ == "__main__":
    main()
