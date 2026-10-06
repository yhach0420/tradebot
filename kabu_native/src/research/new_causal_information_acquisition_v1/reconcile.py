"""Post-capture reconciliation. Read-only vs raw. No register / sendorder / unregister."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.new_causal_information_acquisition_v1 import (
    CASE_FIRST_FULL,
    CASE_INVALID,
    CASE_MODE_READY,
    CASE_PARTIAL,
    CASE_STARTED,
    NEXT_ACCUMULATE,
)
from research.new_causal_information_acquisition_v1.writer import day_layout

JST = ZoneInfo("Asia/Tokyo")
GAP_AUDIT_SEC = 60.0
EARLY = (time(7, 55, 0), time(8, 45, 0))
PREOPEN = (time(8, 45, 0), time(9, 0, 0))
AM = (time(9, 0, 0), time(11, 30, 0))
REACHED_1130 = time(11, 29, 0)
CROSSED_FAIL_RATE = 0.05
NEXT_PARTIAL = "RETAIN_RAW_DO_NOT_COUNT_AS_FULL_NEW_INFO_DAY_V1"
NEXT_INVALID = "INVESTIGATE_NEW_INFO_CAPTURE_INVALID_DAY_V1"
NEXT_STARTED = "CONTINUE_NEW_INFO_CAPTURE_TO_1130_JST_V1"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def freeze_tree(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for dirpath, _dns, fnames in os.walk(root):
        for name in fnames:
            p = Path(dirpath) / name
            st = p.stat()
            rows.append(
                {
                    "path": str(p),
                    "rel": str(p.relative_to(root)).replace("\\", "/"),
                    "size": int(st.st_size),
                    "mtime_jst": datetime.fromtimestamp(st.st_mtime, JST).isoformat(timespec="seconds"),
                    "sha256": sha256_file(p),
                }
            )
    rows.sort(key=lambda r: str(r["rel"]))
    return rows


def parse_received_at(value: Any) -> Optional[datetime]:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def _in_span(dt: datetime, span: tuple[time, time]) -> bool:
    t = dt.astimezone(JST).time()
    return span[0] <= t <= span[1]


def _finite(v: Any) -> bool:
    try:
        if v is None or v == "":
            return False
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def level_price(level: Any) -> Any:
    if isinstance(level, dict):
        return level.get("price", level.get("Price"))
    return level


def iter_jsonl(path: Path):
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, start=1):
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except Exception:
                yield i, None
                continue
            if isinstance(rec, dict):
                yield i, rec


def live_artifacts_present(layout: dict[str, Path]) -> dict[str, bool]:
    stock_n = 0
    if layout["stock"].is_dir():
        stock_n = sum(1 for _ in layout["stock"].glob("push_part_*.jsonl"))
    flags = {
        "live_manifest": layout["live_manifest"].is_file(),
        "first_push": (layout["root"] / "first_push.json").is_file(),
        "status": layout["status"].is_file(),
        "final_manifest": (layout["root"] / "final_manifest.json").is_file(),
        "nk_jsonl": layout["nk225mini_jsonl"].is_file() and layout["nk225mini_jsonl"].stat().st_size > 0,
        "topix_jsonl": layout["topix_jsonl"].is_file() and layout["topix_jsonl"].stat().st_size > 0,
        "stock_parts": stock_n > 0,
    }
    flags["any"] = any(flags.values())
    return flags


def expected_stock_symbols(layout: dict[str, Path]) -> list[str]:
    prepared = _read_json(layout["prepared_manifest"])
    core = [str(s) for s in (prepared.get("core10") or [])]
    dyn = [str(s) for s in (prepared.get("dynamic38") or [])]
    if core or dyn:
        return core + dyn
    live = _read_json(layout["live_manifest"])
    specs = live.get("registration_specs") or []
    out = []
    for spec in specs:
        if isinstance(spec, (list, tuple)) and len(spec) >= 2 and int(spec[1] or 0) == 1:
            out.append(str(spec[0]).split("@", 1)[0])
    return out


def expected_future_symbols(layout: dict[str, Path]) -> dict[str, str]:
    live = _read_json(layout["live_manifest"])
    out: dict[str, str] = {}
    for row in live.get("futures") or []:
        code = str(row.get("FutureCode") or "")
        sym = str(row.get("resolved_symbol") or "").split("@", 1)[0]
        if code and sym:
            out[code] = sym
    return out


def pid_final_state(pid: int, *, capture_cmdline_needles: tuple[str, ...] = ("run_futures_market_context_capture", "new_info")) -> dict[str, Any]:
    from small_paper.capture_child_cleanup import query_process

    q = query_process(int(pid or 0))
    exists = bool(q.get("exists"))
    cmd = str(q.get("cmdline") or "")
    is_capture = exists and any(n in cmd.lower() for n in capture_cmdline_needles)
    if exists and is_capture:
        return {
            "pid": int(pid),
            "PID_FINAL_STATE": "ALIVE_CAPTURE_FAIL_CLOSED",
            "alive": True,
            "identity": q,
            "kill_attempted": False,
        }
    if exists:
        return {
            "pid": int(pid),
            "PID_FINAL_STATE": "ALIVE_NOT_CAPTURE",
            "alive": True,
            "identity": q,
            "kill_attempted": False,
        }
    return {
        "pid": int(pid),
        "PID_FINAL_STATE": "EXITED_EXPECTED",
        "alive": False,
        "identity": q,
        "kill_attempted": False,
    }


def audit_stock(stock_dir: Path, expected: list[str]) -> dict[str, Any]:
    per: dict[str, dict[str, Any]] = {}
    corrupt = 0
    event_n = 0
    files = sorted(stock_dir.glob("push_part_*.jsonl")) if stock_dir.is_dir() else []
    for fp in files:
        for _i, rec in iter_jsonl(fp):
            if rec is None:
                corrupt += 1
                continue
            event_n += 1
            payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
            sym = str(rec.get("symbol") or payload.get("Symbol") or "").split("@", 1)[0]
            dt = parse_received_at(rec.get("received_at_jst") or rec.get("received_at"))
            rec_s = dt.isoformat() if dt else None
            slot = per.setdefault(sym, {"event_n": 0, "first": rec_s, "last": rec_s})
            slot["event_n"] += 1
            if rec_s and (slot["first"] is None or rec_s < str(slot["first"])):
                slot["first"] = rec_s
            if rec_s and (slot["last"] is None or rec_s > str(slot["last"])):
                slot["last"] = rec_s
    got = {s for s in per if s}
    exp = [str(s) for s in expected]
    firsts = [str(v["first"]) for v in per.values() if v.get("first")]
    lasts = [str(v["last"]) for v in per.values() if v.get("last")]
    missing = [s for s in exp if s not in got]
    extra = sorted(got - set(exp)) if exp else []
    return {
        "event_n": event_n,
        "unique_symbol_n": len(got),
        "expected_n": len(exp),
        "missing": missing,
        "extra": extra,
        "first_received_at": min(firsts) if firsts else None,
        "last_received_at": max(lasts) if lasts else None,
        "per_symbol": per,
        "corrupt_n": corrupt,
        "coverage_expected": bool(exp) and not missing and len(exp) == 48,
    }


def audit_futures(path: Path, expected_symbol: str) -> dict[str, Any]:
    event_n = 0
    corrupt = 0
    missing_clock = 0
    bad_clock = 0
    finite_px = 0
    prices: set[str] = set()
    bid_fin = 0
    ask_fin = 0
    vol_fin = 0
    trade_n = 0
    quote_n = 0
    symbols: dict[str, int] = {}
    times: list[datetime] = []
    windows = {
        "0755_0845": {"event_n": 0, "price_change_n": 0, "quote_change_n": 0, "volume_change_n": 0, "any_change_n": 0},
        "0845_0900": {"event_n": 0, "price_change_n": 0, "quote_change_n": 0, "volume_change_n": 0, "any_change_n": 0},
        "0900_1130": {"event_n": 0, "price_change_n": 0, "quote_change_n": 0, "volume_change_n": 0, "any_change_n": 0},
    }
    prev_w: dict[str, tuple] = {}
    first = None
    last = None
    post_pairs = 0
    post_le = 0
    post_crossed = 0
    pre_crossed = 0
    pre_pairs = 0

    def wkey(dt: datetime) -> Optional[str]:
        t = dt.astimezone(JST).time()
        if EARLY[0] <= t < PREOPEN[0]:
            return "0755_0845"
        if PREOPEN[0] <= t <= PREOPEN[1]:
            return "0845_0900"
        if AM[0] <= t <= AM[1]:
            return "0900_1130"
        return None

    for _i, rec in iter_jsonl(path):
        if rec is None:
            corrupt += 1
            continue
        event_n += 1
        clock_raw = rec.get("received_at")
        dt = parse_received_at(clock_raw)
        if clock_raw in (None, ""):
            missing_clock += 1
        elif dt is None:
            bad_clock += 1
        else:
            times.append(dt)
            iso = dt.isoformat()
            if first is None:
                first = iso
            last = iso
        payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
        sym = str(rec.get("resolved_symbol") or rec.get("Symbol") or payload.get("Symbol") or "").split("@", 1)[0]
        symbols[sym] = symbols.get(sym, 0) + 1
        px = rec.get("CurrentPrice")
        bid = level_price(rec.get("Bid1"))
        ask = level_price(rec.get("Ask1"))
        vol = rec.get("TradingVolume")
        if _finite(px):
            finite_px += 1
            prices.add(str(px))
        if _finite(bid):
            bid_fin += 1
        if _finite(ask):
            ask_fin += 1
        if _finite(vol):
            vol_fin += 1
            trade_n += 1
        if _finite(bid) or _finite(ask):
            quote_n += 1
        if dt is not None and _finite(bid) and _finite(ask):
            t = dt.astimezone(JST).time()
            crossed = float(bid) > float(ask)
            if t >= PREOPEN[0]:
                post_pairs += 1
                if crossed:
                    post_crossed += 1
                else:
                    post_le += 1
            else:
                pre_pairs += 1
                if crossed:
                    pre_crossed += 1
        wk = wkey(dt) if dt else None
        if wk:
            windows[wk]["event_n"] += 1
            tup = (px, bid, ask, vol)
            prev = prev_w.get(wk)
            if prev is not None and tup != prev:
                if tup[0] != prev[0]:
                    windows[wk]["price_change_n"] += 1
                if (tup[1], tup[2]) != (prev[1], prev[2]):
                    windows[wk]["quote_change_n"] += 1
                if tup[3] != prev[3]:
                    windows[wk]["volume_change_n"] += 1
                windows[wk]["any_change_n"] += 1
            prev_w[wk] = tup

    times.sort()

    def gap_rows(span: tuple[time, time]) -> list[dict[str, Any]]:
        scoped = [t for t in times if _in_span(t, span)]
        out = []
        for a, b in zip(scoped, scoped[1:]):
            sec = (b - a).total_seconds()
            if sec > GAP_AUDIT_SEC:
                out.append({"gap_start": a.isoformat(), "gap_end": b.isoformat(), "gap_sec": sec, "class": "UNEXPLAINED_OR_NEEDS_AUDIT"})
        return out

    def max_gap(span: tuple[time, time]) -> Optional[float]:
        scoped = [t for t in times if _in_span(t, span)]
        if len(scoped) < 2:
            return None
        return max((b - a).total_seconds() for a, b in zip(scoped, scoped[1:]))

    wrong_n = 0
    if expected_symbol:
        wrong_n = int(sum(v for k, v in symbols.items() if k and k != expected_symbol))
    post_rate = (post_le / post_pairs) if post_pairs else None
    return {
        "path": str(path),
        "event_n": event_n,
        "corrupt_n": corrupt,
        "missing_received_at_n": missing_clock,
        "bad_received_at_n": bad_clock,
        "first_received_at": first,
        "last_received_at": last,
        "finite_CurrentPrice_n": finite_px,
        "unique_CurrentPrice_n": len(prices),
        "bid_finite_n": bid_fin,
        "ask_finite_n": ask_fin,
        "volume_finite_n": vol_fin,
        "trade_update_n": trade_n,
        "quote_update_n": quote_n,
        "symbols": symbols,
        "wrong_symbol_n": wrong_n,
        "windows": windows,
        "gaps_0845_0900": gap_rows(PREOPEN),
        "gaps_0900_1130": gap_rows(AM),
        "max_gap_sec_0845_0900": max_gap(PREOPEN),
        "max_gap_sec_0900_1130": max_gap(AM),
        "post_0845_finite_quote_pairs": post_pairs,
        "post_0845_bid_le_ask_n": post_le,
        "post_0845_crossed_n": post_crossed,
        "post_0845_bid_le_ask_rate": post_rate,
        "preopen_quote_pairs": pre_pairs,
        "preopen_crossed_n": pre_crossed,
        "lineage_pass": missing_clock == 0 and bad_clock == 0 and event_n > 0,
        "dynamic_0845_0900": int((windows["0845_0900"].get("price_change_n") or 0))
        + int((windows["0845_0900"].get("quote_change_n") or 0))
        + int((windows["0845_0900"].get("volume_change_n") or 0)),
        "dynamic_0900_1130": int((windows["0900_1130"].get("price_change_n") or 0))
        + int((windows["0900_1130"].get("quote_change_n") or 0))
        + int((windows["0900_1130"].get("volume_change_n") or 0)),
        "availability_clock": "received_at",
    }


def parse_stdout_log(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
    txt = path.read_text(encoding="utf-8", errors="replace")
    reached = "LIVE_CAPTURE_RUNNING" in txt and ("11:30" in txt)
    verdict = None
    if "NEW_CAUSAL_INFORMATION_FUTURES_CAPTURE_PROVEN_V1" in txt:
        verdict = "NEW_CAUSAL_INFORMATION_FUTURES_CAPTURE_PROVEN_V1"
        reached = True
    elif "NEW_CAUSAL_INFORMATION_FUTURES_CAPTURE_PARTIAL_V1" in txt:
        verdict = "NEW_CAUSAL_INFORMATION_FUTURES_CAPTURE_PARTIAL_V1"
    elif "FAIL" in txt and "VERDICT" in txt:
        verdict = "FAIL"
    return {
        "exists": True,
        "path": str(path),
        "reached_1130_marker": reached,
        "stdout_verdict": verdict,
        "tail": "\n".join(txt.splitlines()[-12:]),
        "exception_in_body": "Traceback" in txt,
    }


def classify_day(*, native_root: Path, trading_date: str, pid_state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    layout = day_layout(str(trading_date), native_root=native_root)
    artifacts = live_artifacts_present(layout)
    expected = expected_stock_symbols(layout)
    fut_sym = expected_future_symbols(layout)
    stock = audit_stock(layout["stock"], expected)
    nk = audit_futures(layout["nk225mini_jsonl"], str(fut_sym.get("NK225mini") or ""))
    tx = audit_futures(layout["topix_jsonl"], str(fut_sym.get("TOPIX") or ""))
    first_push = _read_json(layout["root"] / "first_push.json")
    live_m = _read_json(layout["live_manifest"])
    final_m = _read_json(layout["root"] / "final_manifest.json")

    def last_dt(fut: dict[str, Any]) -> Optional[datetime]:
        return parse_received_at(fut.get("last_received_at"))

    nk_last = last_dt(nk)
    tx_last = last_dt(tx)
    st_last = parse_received_at(stock.get("last_received_at"))
    lasts = [t for t in (nk_last, tx_last, st_last) if t is not None]
    last_any = max(lasts) if lasts else None
    reached_1130 = bool(last_any and last_any.astimezone(JST).time() >= REACHED_1130)
    if str((final_m.get("finalized_at") or "")).startswith(str(trading_date)[:4]):
        fin_dt = parse_received_at(final_m.get("finalized_at"))
        if fin_dt and fin_dt.astimezone(JST).time() >= time(11, 30, 0):
            reached_1130 = True

    nk_dyn_0845 = int((nk.get("windows") or {}).get("0845_0900", {}).get("any_change_n") or 0)
    nk_dyn_0900 = int((nk.get("windows") or {}).get("0900_1130", {}).get("any_change_n") or 0)
    tx_dyn_0845 = int((tx.get("windows") or {}).get("0845_0900", {}).get("any_change_n") or 0)
    tx_dyn_0900 = int((tx.get("windows") or {}).get("0900_1130", {}).get("any_change_n") or 0)
    # any_change includes identical-field-set changes of None/None; require real price|quote|volume change
    nk_dyn_0845 = max(
        nk_dyn_0845,
        int((nk.get("windows") or {}).get("0845_0900", {}).get("price_change_n") or 0)
        + int((nk.get("windows") or {}).get("0845_0900", {}).get("quote_change_n") or 0)
        + int((nk.get("windows") or {}).get("0845_0900", {}).get("volume_change_n") or 0),
    )
    # Use the sum of the three typed changes as the official dynamic proof.
    def typed_change(fut: dict[str, Any], window: str) -> int:
        w = (fut.get("windows") or {}).get(window) or {}
        return int(w.get("price_change_n") or 0) + int(w.get("quote_change_n") or 0) + int(w.get("volume_change_n") or 0)

    nk_c845 = typed_change(nk, "0845_0900")
    nk_c900 = typed_change(nk, "0900_1130")
    tx_c845 = typed_change(tx, "0845_0900")
    tx_c900 = typed_change(tx, "0900_1130")

    lineage = bool(nk.get("lineage_pass") and tx.get("lineage_pass")) if (nk.get("event_n") or tx.get("event_n")) else False
    if (nk.get("event_n") or 0) == 0 and (tx.get("event_n") or 0) == 0:
        lineage = False
    corrupt = int(stock.get("corrupt_n") or 0) + int(nk.get("corrupt_n") or 0) + int(tx.get("corrupt_n") or 0)

    def sanity_ok(fut: dict[str, Any]) -> bool:
        pairs = int(fut.get("post_0845_finite_quote_pairs") or 0)
        if pairs <= 0:
            return False
        rate = fut.get("post_0845_bid_le_ask_rate")
        if rate is None:
            return False
        crossed_rate = 1.0 - float(rate)
        return crossed_rate <= CROSSED_FAIL_RATE

    stock_cov = bool(expected) and int(stock.get("unique_symbol_n") or 0) == 48 and not stock.get("missing")
    if not expected:
        stock_cov = int(stock.get("unique_symbol_n") or 0) == 48

    gates = {
        "A_stock_48_48": stock_cov,
        "B_nk_present": int(nk.get("event_n") or 0) > 0,
        "C_topix_present": int(tx.get("event_n") or 0) > 0,
        "D_nk_0845_0900_dynamic": nk_c845 > 0,
        "E_nk_0900_1130_dynamic": nk_c900 > 0,
        "F_topix_0845_0900_dynamic": tx_c845 > 0,
        "G_topix_0900_1130_dynamic": tx_c900 > 0,
        "H_timestamp_lineage": lineage and int(nk.get("missing_received_at_n") or 0) == 0 and int(tx.get("missing_received_at_n") or 0) == 0,
        "I_payload_sanity": sanity_ok(nk) and sanity_ok(tx),
        "J_reached_1130": reached_1130,
    }
    invalid_reasons: list[str] = []
    if artifacts.get("any") and int(stock.get("event_n") or 0) == 0 and int(nk.get("event_n") or 0) == 0 and int(tx.get("event_n") or 0) == 0:
        invalid_reasons.append("raw_missing")
    if int(nk.get("wrong_symbol_n") or 0) > 0 or int(tx.get("wrong_symbol_n") or 0) > 0:
        invalid_reasons.append("wrong_future_symbol")
    if (nk.get("event_n") or 0) > 0 and not nk.get("lineage_pass"):
        invalid_reasons.append("timestamp_lineage_broken")
    if (tx.get("event_n") or 0) > 0 and not tx.get("lineage_pass"):
        invalid_reasons.append("timestamp_lineage_broken")
    if (int(nk.get("event_n") or 0) > 0 and not sanity_ok(nk)) or (int(tx.get("event_n") or 0) > 0 and not sanity_ok(tx)):
        invalid_reasons.append("bid_ask_semantics")
    if artifacts.get("any") and (int(nk.get("event_n") or 0) > 0 or int(tx.get("event_n") or 0) > 0) and not reached_1130:
        invalid_reasons.append("capture_early_death")
    if corrupt > 0:
        invalid_reasons.append("writer_corruption")
    if pid_state and pid_state.get("PID_FINAL_STATE") == "ALIVE_CAPTURE_FAIL_CLOSED":
        invalid_reasons.append("capture_process_still_alive")

    full = all(gates.values()) and not invalid_reasons
    if pid_state and pid_state.get("PID_FINAL_STATE") == "ALIVE_CAPTURE_FAIL_CLOSED":
        classification = "INVALID"
        verdict = CASE_INVALID
        nxt = NEXT_INVALID
    elif invalid_reasons:
        classification = "INVALID"
        verdict = CASE_INVALID
        nxt = NEXT_INVALID
    elif full:
        classification = "FULL"
        verdict = CASE_FIRST_FULL
        nxt = NEXT_ACCUMULATE
    elif artifacts.get("any") and not reached_1130 and (pid_state or {}).get("alive"):
        classification = "STARTED"
        verdict = CASE_STARTED
        nxt = NEXT_STARTED
    elif artifacts.get("any") or int(stock.get("event_n") or 0) > 0 or int(nk.get("event_n") or 0) > 0:
        classification = "PARTIAL"
        verdict = CASE_PARTIAL
        nxt = NEXT_PARTIAL
    else:
        classification = "NONE"
        verdict = CASE_MODE_READY
        nxt = "START_FIRST_NEW_INFO_CAPTURE_WINDOW_0755_1130_JST_V1"

    if artifacts.get("any") and verdict == CASE_MODE_READY:
        classification = "PARTIAL"
        verdict = CASE_PARTIAL
        nxt = NEXT_PARTIAL

    alpha_use_forbidden = not gates["I_payload_sanity"] if (nk.get("event_n") or tx.get("event_n")) else True

    return {
        "trading_date": str(trading_date),
        "artifacts": artifacts,
        "stock": {k: v for k, v in stock.items() if k != "per_symbol"},
        "stock_per_symbol": stock.get("per_symbol") or {},
        "nk225mini": nk,
        "topix": tx,
        "first_push": first_push,
        "live_manifest_present": bool(live_m),
        "resolved_live": {
            "nk": (live_m.get("futures") or [None, None])[0] if live_m.get("futures") else None,
            "topix": (live_m.get("futures") or [None, None])[1] if len(live_m.get("futures") or []) > 1 else None,
            "board_nk": ((live_m.get("futures") or [{}])[0] or {}).get("board_preflight") if live_m.get("futures") else None,
            "board_tx": ((live_m.get("futures") or [{}, {}])[1] or {}).get("board_preflight") if len(live_m.get("futures") or []) > 1 else None,
        },
        "gates": gates,
        "invalid_reasons": invalid_reasons,
        "classification": classification,
        "FULL": classification == "FULL",
        "PARTIAL": classification == "PARTIAL",
        "INVALID": classification == "INVALID",
        "VERDICT": verdict,
        "NEXT": nxt,
        "count_as_day1": classification == "FULL",
        "alpha_use_forbidden": bool(alpha_use_forbidden and classification != "FULL"),
        "reached_1130": reached_1130,
        "raw_corrupt_n": corrupt,
        "pid": pid_state or {},
        "orders": {"submit": 0, "cancel": 0, "live": 0, "sendorder_call_n": 0},
        "nk_change_0845_0900": nk_c845,
        "nk_change_0900_1130": nk_c900,
        "topix_change_0845_0900": tx_c845,
        "topix_change_0900_1130": tx_c900,
        "future_gaps_gt_60s": list(nk.get("gaps_0845_0900") or [])
        + list(nk.get("gaps_0900_1130") or [])
        + list(tx.get("gaps_0845_0900") or [])
        + list(tx.get("gaps_0900_1130") or []),
    }


def forbid_ready_downgrade(verdict: str, *, artifacts: dict[str, bool], event_n: int) -> str:
    if (artifacts.get("any") or int(event_n or 0) > 0) and verdict == CASE_MODE_READY:
        return CASE_PARTIAL
    return verdict
