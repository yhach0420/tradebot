"""Date-parallel capital scaling. The frozen one-tick rule is not edited."""
from __future__ import annotations

import hashlib
import json
import statistics
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import psutil
from openpyxl import Workbook

from research.current50_v16_capital_risk_policy_comparison_v1.compare import ResearchGrossCapExecutor
from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import _capture_dir, _symbol
from research.current50_v16_historical_economic_expansion_v2.expand import _attach_text
from research.v16_one_tick_exit_complete_strategy_v1 import evaluate as frozen
from research.v16_one_tick_exit_complete_strategy_v1.evaluate import OneTickExit
from small_paper.paper_session_executor import bind_outer_registration

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "v16_one_tick_exit_capital_scaling_v2"
PRIOR = NATIVE / "results" / "research" / "v16_one_tick_exit_complete_strategy_v1" / "report.json"
RULE = NATIVE / "src" / "research" / "v16_one_tick_exit_complete_strategy_v1" / "evaluate.py"
FROZEN_SHA = "e87713f714541f7a7fe2d98b1d1bb6aadb1b082d4e76b4211832dd9fdf8c4c91"
PRIOR_WALL_SEC = 25179.391
CASES = (
    (250_000.0, 500_000.0),
    (500_000.0, 1_000_000.0),
    (1_000_000.0, 2_000_000.0),
    (2_000_000.0, 4_000_000.0),
)
BENCH_DAYS = ("20260728", "20260812", "20260910")
BUSY = (
    "paper_trade_checked_runner",
    "market_ingress_service",
    "run_core10_dynamic40_am_pm_daily_runner",
    "run_small_paper_pilot",
    "v1r_paper_primary_launcher",
)
RAM_FRACTION = 0.75


def _frozen_sha() -> str:
    raw = RULE.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return hashlib.sha256(raw).hexdigest()


def _runner_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _busy() -> list[str]:
    found = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = str(proc.info.get("name") or "").lower()
            if name not in ("python.exe", "pythonw.exe"):
                continue
            text = " ".join(proc.info.get("cmdline") or [])
        except (psutil.Error, OSError):
            return ["PROCESS_CHECK_FAILED"]
        found.extend(item for item in BUSY if item in text)
    return sorted(set(found))


def _window(now: datetime) -> bool:
    return now.weekday() < 5 and 7 <= now.hour < 16


def _historical_days() -> list[str]:
    body = json.loads(frozen.PRIOR.read_text(encoding="utf-8"))
    return [row["date"] for row in body.get("eligibility") or [] if row.get("class") == "RETROSPECTIVE_V16_COUNTERFACTUAL"]


def _expected() -> dict[str, dict[str, dict[str, float]]]:
    body = json.loads(PRIOR.read_text(encoding="utf-8"))
    out: dict[str, dict[str, dict[str, float]]] = {}
    for row in body.get("daily") or []:
        out.setdefault(row["day"], {})[row["policy"]] = {"trades": float(row["trades"]), "gross": float(row["gross"])}
    return out


def _input_id(day: str) -> dict[str, Any]:
    capture = _capture_dir(day)
    if capture is None:
        return {"error": "NO_CAPTURE"}
    parts = []
    for path in sorted(capture.glob("push_part_*.jsonl")):
        stat = path.stat()
        parts.append({"name": path.name, "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)})
    complete = capture / "capture_completeness.json"
    return {"capture": capture.name, "parts": parts, "completeness_size": int(complete.stat().st_size) if complete.is_file() else 0}


def _prepare(day: str, kind: str, cap: float, cls: type) -> Any:
    book = cls()
    book.notifier = None
    book.ledger_path = None
    bind_outer_registration(book, native_root=NATIVE, trading_date=day, universe=[])
    if kind == "pm":
        book.bind_safe_pm_flatten(day=day)
    else:
        book.bind_production_schedule(day=day, kind="am")
    book.gross_cap = float(cap)
    return book


def _exposure(rows: list[dict[str, Any]], cap: float) -> tuple[float, int]:
    open_px: dict[str, float] = {}
    peak = 0.0
    breaches = 0
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if row.get("event") == "ENTRY":
            open_px[symbol] = float(row["fill_price"]) * 100.0
            if sum(open_px.values()) > cap + 1e-6:
                breaches += 1
        elif row.get("event") == "EXIT":
            open_px.pop(symbol, None)
        peak = max(peak, sum(open_px.values()))
    return peak, breaches


def _trades(book: Any) -> list[dict[str, Any]]:
    rows = []
    for trade in book.trades:
        rows.append(
            {
                "uid": trade["signal_uid"],
                "symbol": trade["symbol"],
                "reason": trade["reason"],
                "pnl": float(trade["pnl_yen"]),
                "entry_t": float(trade["entry_t"]),
                "exit_t": float(trade["exit_t"]),
                "entry_px": float(trade["entry_px"]),
                "exit_px": float(trade["exit_px"]),
                "hold": float(trade["hold_sec"]),
                "trigger_t": float(trade.get("exit_trigger_t") or trade["exit_t"]),
                "notional": float(trade["entry_px"]) * 100.0,
                "reentry": bool(trade["reentry"]),
            }
        )
    return rows


def _unfilled(book: Any) -> list[dict[str, Any]]:
    rows = []
    for row in book.unfilled:
        px = row.get("entry_px")
        rows.append(
            {
                "uid": row.get("signal_uid"),
                "symbol": row.get("symbol"),
                "notional": None if px is None else float(px) * 100.0,
                "pnl_yen": row.get("pnl_yen"),
                "exit_px": row.get("exit_px"),
                "reason": row.get("reason"),
            }
        )
    return rows


def _compact(book: Any, cap: float) -> dict[str, Any]:
    perf = book.x1_performance()
    trades = _trades(book)
    peak, breaches = _exposure(book.ledger, cap)
    return {
        "trades_n": len(trades),
        "gross": round(sum(row["pnl"] for row in trades), 6),
        "wins": sum(row["pnl"] > 0 for row in trades),
        "profit": round(sum(row["pnl"] for row in trades if row["pnl"] > 0), 6),
        "loss": round(sum(row["pnl"] for row in trades if row["pnl"] < 0), 6),
        "max_loss": min((row["pnl"] for row in trades), default=None),
        "yen_deny": int(book.gross_cap_denied_n),
        "reentry": int(book.counts.get("reentry") or 0),
        "cap_blocked": int(book.counts.get("cap") or 0),
        "max_gross": peak,
        "breaches": breaches,
        "above_g": sum(row["notional"] > cap + 1e-6 for row in trades),
        "final_ok": bool(perf["final_ok"]),
        "open_n": int(perf["open_n"]),
        "pending_n": int(perf["exit_pending_n"]),
        "slot_release_n": int(perf["slot_release_n"]),
        "unfilled": _unfilled(book),
        "exits": [[row["exit_t"], row["pnl"], row["uid"]] for row in trades],
        "holds": [row["hold"] for row in trades],
        "trades": trades,
    }


def _session(day: str, kind: str) -> dict[str, Any]:
    capture = _capture_dir(day)
    if capture is None:
        return {"error": "NO_CAPTURE"}
    made = []
    for capital, cap in CASES:
        for policy, cls in (("B", ResearchGrossCapExecutor), ("E", OneTickExit)):
            book = _prepare(day, kind, cap, cls)
            made.append({"capital": capital, "policy": policy, "cap": cap, "book": book})
    if any(len(item["book"].admission_membership) != 50 for item in made):
        return {"error": "ADMISSION"}
    attach = _attach_text(day, kind)
    fed = 0
    stopped = False
    for part in sorted(capture.glob("push_part_*.jsonl")):
        if stopped:
            break
        with part.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("kind") not in (None, "market_push"):
                    continue
                if int(rec.get("sequence") or 0) <= 0:
                    continue
                received = str(rec.get("received_at") or "")
                if not received or received < attach:
                    continue
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else {}
                if not payload:
                    continue
                payload = dict(payload)
                payload.setdefault("received_at", received)
                payload.setdefault("recorded_at", received)
                symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
                if not symbol:
                    continue
                for item in made:
                    book = item["book"]
                    if not book._closed:
                        book.on_market_event({"symbol": symbol, "payload": dict(payload)})
                fed += 1
                if fed % 200000 == 0:
                    print(f"fed {day} {kind} {fed}", flush=True)
                if all(item["book"]._closed for item in made):
                    stopped = True
                    break
    for item in made:
        book = item["book"]
        if not book._closed:
            book.close_at_runtime_boundary(day=day, kind="pm" if book.flatten_deadline is not None else "am")
    cases = []
    for item in made:
        packed = _compact(item["book"], item["cap"])
        packed["capital"] = item["capital"]
        packed["policy"] = item["policy"]
        packed["g"] = item["cap"]
        cases.append(packed)
        item["book"] = None
    print(
        f"SCALE {day} {kind} "
        + " ".join(f"{int(row['capital'])}{row['policy']} {row['trades_n']} {row['gross']}" for row in cases if row["capital"] == 500_000.0),
        flush=True,
    )
    return {"fed": fed, "cases": cases}


def run_day(day: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        am = _session(day, "am")
        pm = _session(day, "pm")
        if am.get("error") or pm.get("error"):
            return {"day": day, "error": am.get("error") or pm.get("error"), "elapsed_sec": time.perf_counter() - started}
        return {
            "day": day,
            "elapsed_sec": time.perf_counter() - started,
            "input": _input_id(day),
            "am": am,
            "pm": pm,
            "runner_sha": _runner_sha(),
            "frozen_sha": _frozen_sha(),
        }
    except Exception:
        return {"day": day, "error": traceback.format_exc(), "elapsed_sec": time.perf_counter() - started}


def _case(result: dict[str, Any], kind: str, capital: float, policy: str) -> dict[str, Any]:
    for row in result[kind]["cases"]:
        if row["capital"] == capital and row["policy"] == policy:
            return row
    raise KeyError((kind, capital, policy))


def _same_trade(old: dict[str, Any], new: dict[str, Any]) -> bool:
    keys = ("uid", "symbol", "reason", "pnl", "entry_t", "exit_t", "hold", "trigger_t", "notional")
    return all(old.get(key) == new.get(key) for key in keys)


def _derived_reentry(rows: list[dict[str, Any]]) -> dict[str, bool]:
    seen: set[str] = set()
    flags = {}
    for row in sorted(rows, key=lambda item: (float(item["entry_t"]), str(item["uid"]))):
        flags[str(row["uid"])] = str(row["symbol"]) in seen
        seen.add(str(row["symbol"]))
    return flags


def prove_day(result: dict[str, Any]) -> dict[str, Any]:
    day = result["day"]
    mismatches = []
    for kind in ("am", "pm"):
        old = frozen._feed(day, kind, 0, _attach_text(day, kind))
        if old.get("error"):
            return {"day": day, "ok": False, "error": old["error"]}
        for policy, old_rows, old_unfilled, old_deny, old_max in (
            ("B", old["rows_b"], old["unfilled_b"], old["yen_deny_b"], old["max_b"]),
            ("E", old["rows_e"], old["unfilled_e"], old["yen_deny_e"], old["max_e"]),
        ):
            got = _case(result, kind, 500_000.0, policy)
            new_rows = got["trades"]
            if len(old_rows) != len(new_rows):
                mismatches.append({"kind": kind, "policy": policy, "field": "trades_n", "old": len(old_rows), "new": len(new_rows)})
            old_by = {row["uid"]: row for row in old_rows}
            new_by = {row["uid"]: row for row in new_rows}
            for uid in sorted(set(old_by) | set(new_by)):
                if uid not in old_by or uid not in new_by or not _same_trade(old_by[uid], new_by[uid]):
                    mismatches.append({"kind": kind, "policy": policy, "uid": uid})
                    if len(mismatches) >= 5:
                        return {"day": day, "ok": False, "mismatches": mismatches}
            flags = _derived_reentry(old_rows)
            for row in new_rows:
                if bool(row["reentry"]) != bool(flags.get(row["uid"], False)):
                    mismatches.append({"kind": kind, "policy": policy, "uid": row["uid"], "field": "reentry"})
            old_ids = sorted(str(row["uid"]) for row in old_unfilled)
            new_ids = sorted(str(row["uid"]) for row in got["unfilled"])
            if old_ids != new_ids or int(got["yen_deny"]) != int(old_deny) or abs(float(got["max_gross"]) - float(old_max)) > 1e-6:
                mismatches.append(
                    {
                        "kind": kind,
                        "policy": policy,
                        "unfilled": [old_ids, new_ids],
                        "yen_deny": [old_deny, got["yen_deny"]],
                        "max_gross": [old_max, got["max_gross"]],
                    }
                )
    return {"day": day, "ok": not mismatches, "mismatches": mismatches[:5]}


def _fingerprint(result: dict[str, Any]) -> str:
    lines = []
    for kind in ("am", "pm"):
        for case in result[kind]["cases"]:
            for row in case["trades"]:
                lines.append(
                    (
                        kind,
                        case["capital"],
                        case["policy"],
                        row["uid"],
                        row["reason"],
                        row["pnl"],
                        row["entry_t"],
                        row["exit_t"],
                        row["entry_px"],
                        row["exit_px"],
                        row["reentry"],
                    )
                )
            for row in case["unfilled"]:
                lines.append(("U", kind, case["capital"], case["policy"], row["uid"], row["notional"], row["pnl_yen"]))
    return hashlib.sha256(repr(lines).encode("utf-8")).hexdigest()


def _sample(root: psutil.Process) -> dict[str, float]:
    rss = 0
    read_bytes = 0
    write_bytes = 0
    procs = [root]
    try:
        procs.extend(root.children(recursive=True))
    except psutil.Error:
        pass
    for proc in procs:
        try:
            rss += int(proc.memory_info().rss)
            io = proc.io_counters()
            read_bytes += int(io.read_bytes)
            write_bytes += int(io.write_bytes)
        except (psutil.Error, OSError, AttributeError):
            continue
    return {"rss": float(rss), "read": float(read_bytes), "write": float(write_bytes)}


def _run_parallel(days: list[str], workers: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = psutil.Process()
    psutil.cpu_percent(interval=None)
    before = _sample(root)
    disk0 = psutil.disk_io_counters()
    started = time.perf_counter()
    cpu = []
    peak = before["rss"]
    results: list[dict[str, Any]] = []
    aborted = False
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_day, day): day for day in days}
        pending = set(futures)
        while pending:
            if _busy() or _window(datetime.now().astimezone()):
                aborted = True
                pool.shutdown(wait=False, cancel_futures=True)
                break
            done, pending = wait(pending, timeout=5)
            point = _sample(root)
            peak = max(peak, point["rss"])
            cpu.append(psutil.cpu_percent(interval=None))
            if peak > psutil.virtual_memory().total * RAM_FRACTION:
                aborted = True
                pool.shutdown(wait=False, cancel_futures=True)
                break
            for future in done:
                results.append(future.result())
    after = _sample(root)
    disk1 = psutil.disk_io_counters()
    wall = time.perf_counter() - started
    return results, {
        "workers": workers,
        "days": list(days),
        "wall_sec": wall,
        "sec_per_day": wall / len(days) if days else None,
        "cpu_percent_avg": (sum(cpu) / len(cpu)) if cpu else None,
        "cpu_percent_max": max(cpu) if cpu else None,
        "peak_rss_bytes": peak,
        "process_read_bytes": after["read"] - before["read"],
        "process_write_bytes": after["write"] - before["write"],
        "system_read_bytes": None if disk0 is None or disk1 is None else disk1.read_bytes - disk0.read_bytes,
        "system_write_bytes": None if disk0 is None or disk1 is None else disk1.write_bytes - disk0.write_bytes,
        "aborted": aborted,
        "ram_total_bytes": int(psutil.virtual_memory().total),
    }


def _watch(stop: threading.Event, sink: dict[str, Any]) -> None:
    root = psutil.Process()
    while not stop.wait(2):
        point = _sample(root)
        sink["peak"] = max(float(sink.get("peak") or 0.0), point["rss"])
        sink["cpu"].append(psutil.cpu_percent(interval=None))


def _run_sequential(days: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = psutil.Process()
    psutil.cpu_percent(interval=None)
    before = _sample(root)
    disk0 = psutil.disk_io_counters()
    started = time.perf_counter()
    sink: dict[str, Any] = {"peak": before["rss"], "cpu": []}
    stop = threading.Event()
    watcher = threading.Thread(target=_watch, args=(stop, sink), daemon=True)
    watcher.start()
    results = []
    aborted = False
    try:
        for day in days:
            if _busy() or _window(datetime.now().astimezone()):
                aborted = True
                break
            results.append(run_day(day))
            if float(sink["peak"]) > psutil.virtual_memory().total * RAM_FRACTION:
                aborted = True
                break
    finally:
        stop.set()
        watcher.join(timeout=3)
    after = _sample(root)
    peak = max(float(sink["peak"]), after["rss"])
    cpu = list(sink["cpu"])
    disk1 = psutil.disk_io_counters()
    wall = time.perf_counter() - started
    return results, {
        "workers": 1,
        "days": list(days),
        "wall_sec": wall,
        "sec_per_day": wall / len(results) if results else None,
        "cpu_percent_avg": (sum(cpu) / len(cpu)) if cpu else None,
        "cpu_percent_max": max(cpu) if cpu else None,
        "peak_rss_bytes": peak,
        "process_read_bytes": after["read"] - before["read"],
        "process_write_bytes": after["write"] - before["write"],
        "system_read_bytes": None if disk0 is None or disk1 is None else disk1.read_bytes - disk0.read_bytes,
        "system_write_bytes": None if disk0 is None or disk1 is None else disk1.write_bytes - disk0.write_bytes,
        "aborted": aborted,
        "ram_total_bytes": int(psutil.virtual_memory().total),
        "mode": "SEQUENTIAL_IN_PROCESS",
    }


def _save(report: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    temporary = OUT / "report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    temporary.replace(OUT / "report.json")


def _slim_case(case: dict[str, Any]) -> dict[str, Any]:
    kept = dict(case)
    kept.pop("trades", None)
    return kept


def _store_day(result: dict[str, Any]) -> dict[str, Any]:
    stored = {
        "day": result["day"],
        "elapsed_sec": result["elapsed_sec"],
        "input": result["input"],
        "runner_sha": result["runner_sha"],
        "frozen_sha": result["frozen_sha"],
        "fingerprint": _fingerprint(result),
        "am": {"fed": result["am"]["fed"], "cases": [_slim_case(row) for row in result["am"]["cases"]]},
        "pm": {"fed": result["pm"]["fed"], "cases": [_slim_case(row) for row in result["pm"]["cases"]]},
    }
    return stored


def _dd(exits: list[list[Any]]) -> float:
    ordered = sorted(exits, key=lambda row: (float(row[0]), str(row[2])))
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for _exit_t, pnl, _uid in ordered:
        equity += float(pnl)
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _bundle(days: list[dict[str, Any]], capital: float, policy: str) -> dict[str, Any]:
    exits: list[list[Any]] = []
    holds: list[float] = []
    unfilled = []
    gross = trades = wins = profit = loss = yen = reentry = cap = breaches = above = slot = 0
    max_loss = None
    max_gross = 0.0
    for day in days:
        for kind in ("am", "pm"):
            case = _case(day, kind, capital, policy)
            exits.extend(case["exits"])
            holds.extend(float(value) for value in case["holds"])
            unfilled.extend(case["unfilled"])
            gross += float(case["gross"])
            trades += int(case["trades_n"])
            wins += int(case["wins"])
            profit += float(case["profit"])
            loss += float(case["loss"])
            yen += int(case["yen_deny"])
            reentry += int(case["reentry"])
            cap += int(case["cap_blocked"])
            breaches += int(case["breaches"])
            above += int(case["above_g"])
            slot += int(case["slot_release_n"])
            max_gross = max(max_gross, float(case["max_gross"]))
            if case["max_loss"] is not None:
                max_loss = float(case["max_loss"]) if max_loss is None else min(max_loss, float(case["max_loss"]))
    return {
        "trades": trades,
        "gross": round(gross, 6),
        "pf": None if loss >= 0 else profit / abs(loss),
        "win_rate": (wins / trades) if trades else None,
        "max_dd": _dd(exits),
        "max_loss": max_loss,
        "median_hold": None if not holds else float(statistics.median(holds)),
        "max_gross": max_gross,
        "breaches": breaches,
        "above_g": above,
        "yen_deny": yen,
        "reentry": reentry,
        "cap_blocked": cap,
        "slot_release_n": slot,
        "unfilled_n": len(unfilled),
        "unfilled_notional": round(sum(float(row["notional"] or 0) for row in unfilled), 6),
        "unfilled": unfilled,
        "costs": [
            {"per_side": cost, "pnl": round(gross - cost * 2 * trades, 6), "label": "SEALED_NO_FEE" if cost == 0 else "ASSUMED_NOT_OFFICIAL_NET"}
            for cost in (0, 10, 50, 100)
        ],
    }


def _daily_rows(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for day in sorted(days, key=lambda item: item["day"]):
        for capital, _cap in CASES:
            totals = {}
            for policy in ("B", "E"):
                trades = gross = 0.0
                for kind in ("am", "pm"):
                    case = _case(day, kind, capital, policy)
                    trades += int(case["trades_n"])
                    gross += float(case["gross"])
                totals[policy] = {"trades": int(trades), "gross": round(gross, 6)}
            rows.append(
                {
                    "day": day["day"],
                    "capital": capital,
                    "b_trades": totals["B"]["trades"],
                    "b_gross": totals["B"]["gross"],
                    "e_trades": totals["E"]["trades"],
                    "e_gross": totals["E"]["gross"],
                    "delta": round(totals["E"]["gross"] - totals["B"]["gross"], 6),
                }
            )
    return rows


def _book(report: dict[str, Any]) -> None:
    book = Workbook()
    names = ("Summary", "Speed", "Parity", "Capitals", "Daily", "Costs", "Unfilled")
    first = True
    for name in names:
        sheet = book.active if first else book.create_sheet(name)
        first = False
        sheet.title = name
        sheet.append(["item", "value"])
    for key, value in (report.get("summary") or {}).items():
        book["Summary"].append([key, str(value)])
    for row in report.get("speed") or []:
        book["Speed"].append([f"{row.get('workers')} {row.get('mode', 'POOL')}", row.get("wall_sec")])
    for row in report.get("parity") or []:
        book["Parity"].append([row.get("day"), row.get("ok")])
    for row in report.get("capitals") or []:
        book["Capitals"].append([row["capital"], f"{row['b_gross']} {row['e_gross']} {row['delta']} {row['judgment']}"])
    for row in report.get("daily") or []:
        book["Daily"].append([f"{int(row['capital'])} {row['day']}", f"{row['b_trades']} {row['b_gross']} {row['e_trades']} {row['e_gross']} {row['delta']}"])
    for row in report.get("costs") or []:
        book["Costs"].append([f"{int(row['capital'])} {row['policy']} {row['per_side']}", row["pnl"]])
    for row in report.get("unfilled") or []:
        book["Unfilled"].append([f"{int(row['capital'])} {row['policy']} {row['uid']}", row.get("notional")])
    book.save(OUT / "audit.xlsx")


def _markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"VERDICT: {report['verdict']}",
            f"NEXT: {report['next']}",
            "",
            report.get("body") or "",
            "",
            "Paper V16、Runtime、Selector、ENTRY、EXIT、Captureは変更していません。",
            "submit/cancel/live = 0/0/0。",
            "この29日は独立holdoutではありません。",
            "Policy Bも1呼値EXITもPaperへは採用していません。",
        ]
    ) + "\n"


def _finish(report: dict[str, Any], blocked: Optional[str] = None) -> None:
    days = list((report.get("days") or {}).values())
    expected = _expected()
    daily_mismatch = []
    if len(days) == 29:
        for day in days:
            got = {}
            for policy in ("B", "E"):
                trades = gross = 0.0
                for kind in ("am", "pm"):
                    case = _case(day, kind, 500_000.0, policy)
                    trades += int(case["trades_n"])
                    gross += float(case["gross"])
                got[policy] = (int(trades), round(gross, 6))
            exp = expected.get(day["day"], {})
            for policy, label in (("B", "B"), ("E", "E")):
                old = exp.get(label, {"trades": 0.0, "gross": 0.0})
                if got[policy][0] != int(old["trades"]) or abs(got[policy][1] - float(old["gross"])) >= 0.05:
                    daily_mismatch.append({"day": day["day"], "policy": policy, "got": got[policy], "expected": [old["trades"], old["gross"]]})
    complete = len(days) == 29 and not daily_mismatch and not blocked and all(row.get("ok") for row in report.get("parity") or [])
    capitals = []
    costs = []
    unfilled = []
    if complete:
        for capital, cap in CASES:
            left = _bundle(days, capital, "B")
            right = _bundle(days, capital, "E")
            improved = (
                right["gross"] > left["gross"]
                and right["max_dd"] >= left["max_dd"] - 1e-6
                and right["max_gross"] <= cap + 1e-6
                and right["breaches"] == 0
                and right["unfilled_n"] <= left["unfilled_n"]
                and right["unfilled_notional"] <= left["unfilled_notional"] + 1e-6
            )
            capitals.append(
                {
                    "capital": capital,
                    "g": cap,
                    "b_gross": left["gross"],
                    "e_gross": right["gross"],
                    "delta": round(right["gross"] - left["gross"], 6),
                    "b_cost50": left["costs"][2]["pnl"],
                    "e_cost50": right["costs"][2]["pnl"],
                    "b_dd": left["max_dd"],
                    "e_dd": right["max_dd"],
                    "b_dd_ratio": left["max_dd"] / capital,
                    "e_dd_ratio": right["max_dd"] / capital,
                    "judgment": "IMPROVED" if improved else "NOT_IMPROVED",
                    "b": left,
                    "e": right,
                }
            )
            for policy, bundle in (("B", left), ("E", right)):
                for row in bundle["costs"]:
                    costs.append({"capital": capital, "policy": policy, **row})
                for row in bundle["unfilled"]:
                    unfilled.append({"capital": capital, "policy": policy, **row})
    verdict = "V16_ONE_TICK_EXIT_CAPITAL_SCALING_EVALUATED_V2" if complete else "V16_ONE_TICK_EXIT_CAPITAL_SCALING_BLOCKED_V2"
    nxt = "KEEP_THE_FROZEN_RULE_AND_WAIT_FOR_AN_INDEPENDENT_CAPTURE" if complete else "DO_NOT_USE_THE_CAPITAL_RESULTS"
    speed = report.get("speed") or []
    chosen = report.get("chosen_workers")
    before = PRIOR_WALL_SEC
    after = next((row.get("full_wall_sec") for row in [report] if report.get("full_wall_sec")), None)
    body = "\n".join(
        [
            "最適化前は、1日のCaptureをBとB+Eの2状態機械へ逐次供給していました。29日の実測は "
            f"{before:.3f}秒です。8条件をその経路のまま4回繰り返すと、同じ実測から約 {before * 4:.3f}秒です。",
            "変更したのは研究ランナーだけです。日付ごとにCaptureを1回解析し、同じイベント列を8つの独立した状態機械へ供給します。"
            "日付だけをプロセス分割し、日付の中のイベント順序は維持します。凍結した1呼値規則のファイルは変更していません。",
            f"採用Worker数は {chosen} です。計測は {speed} です。",
            "CaptureのJSON解析は、1セッションにつき1回です。資金条件ごとに繰り返した場合の4回から1回へ減らしています。",
            f"50万円の日別不一致は {daily_mismatch} です。逐次と並列の一致は {report.get('parallel_match')} です。",
            f"資金額別の結果は {[{'capital': row['capital'], 'b': row['b_gross'], 'e': row['e_gross'], 'delta': row['delta'], 'cost50_e': row['e_cost50'], 'dd_e': row['e_dd'], 'judgment': row['judgment']} for row in capitals]} です。",
            "未約定にpnlは入れていません。AskとBidのスプレッドは再加算していません。10円以上の費用は仮定であり公式Netではありません。",
            f"全期間の壁時間は {after} 秒です。" if after is not None else "全期間の壁時間は未完了です。",
        ]
    )
    report.update(
        {
            "verdict": verdict,
            "next": nxt,
            "blocked": blocked,
            "daily_mismatch": daily_mismatch,
            "capitals": [
                {key: value for key, value in row.items() if key not in ("b", "e")}
                | {
                    "b_trades": row["b"]["trades"],
                    "e_trades": row["e"]["trades"],
                    "b_pf": row["b"]["pf"],
                    "e_pf": row["e"]["pf"],
                    "b_win_rate": row["b"]["win_rate"],
                    "e_win_rate": row["e"]["win_rate"],
                    "b_max_loss": row["b"]["max_loss"],
                    "e_max_loss": row["e"]["max_loss"],
                    "b_max_gross": row["b"]["max_gross"],
                    "e_max_gross": row["e"]["max_gross"],
                    "b_breaches": row["b"]["breaches"],
                    "e_breaches": row["e"]["breaches"],
                    "b_yen_deny": row["b"]["yen_deny"],
                    "e_yen_deny": row["e"]["yen_deny"],
                    "b_unfilled": row["b"]["unfilled_n"],
                    "e_unfilled": row["e"]["unfilled_n"],
                    "b_unfilled_notional": row["b"]["unfilled_notional"],
                    "e_unfilled_notional": row["e"]["unfilled_notional"],
                    "b_median_hold": row["b"]["median_hold"],
                    "e_median_hold": row["e"]["median_hold"],
                    "b_reentry": row["b"]["reentry"],
                    "e_reentry": row["e"]["reentry"],
                    "b_slot_release": row["b"]["slot_release_n"],
                    "e_slot_release": row["e"]["slot_release_n"],
                    "better_days": sum(1 for item in _daily_rows(days) if item["capital"] == row["capital"] and item["delta"] > 1e-6),
                    "worse_days": sum(1 for item in _daily_rows(days) if item["capital"] == row["capital"] and item["delta"] < -1e-6),
                }
                for row in capitals
            ],
            "daily": _daily_rows(days) if days else [],
            "costs": costs,
            "unfilled": unfilled,
            "body": body,
            "summary": {
                "chosen_workers": chosen,
                "days": len(days),
                "parallel_match": report.get("parallel_match"),
                "full_wall_sec": after,
                "prior_two_book_wall_sec": before,
            },
            "policy_adopted": False,
            "paper_runtime_unchanged": True,
            "submit_cancel_live": "0/0/0",
            "independent_validation": "NOT_DONE",
            "running": False,
        }
    )
    text = _markdown(report)
    report["markdown"] = text
    (OUT / "report.md").write_text(text, encoding="utf-8")
    slim_days = {}
    for day, value in (report.get("days") or {}).items():
        copied = dict(value)
        for kind in ("am", "pm"):
            copied[kind] = {
                "fed": value[kind]["fed"],
                "cases": [{key: item[key] for key in item if key not in ("exits", "holds", "trades")} for item in value[kind]["cases"]],
            }
        slim_days[day] = copied
    report["days"] = slim_days
    _save(report)
    _book(report)
    print(verdict, flush=True)


def _load() -> dict[str, Any]:
    path = OUT / "report.json"
    if not path.is_file():
        return {}
    prior = json.loads(path.read_text(encoding="utf-8"))
    if prior.get("runner_sha") != _runner_sha() or prior.get("frozen_sha") != _frozen_sha():
        return {}
    return prior


def _matches_published(result: dict[str, Any]) -> bool:
    expected = _expected().get(result["day"], {})
    for policy in ("B", "E"):
        trades = 0
        gross = 0.0
        for kind in ("am", "pm"):
            case = _case(result, kind, 500_000.0, policy)
            trades += int(case["trades_n"])
            gross += float(case["gross"])
        old = expected.get(policy, {"trades": 0.0, "gross": 0.0})
        if trades != int(old["trades"]) or abs(gross - float(old["gross"])) >= 0.05:
            return False
    return True


def _blocked(reason: str, report: dict[str, Any]) -> None:
    report["running"] = False
    _finish(report, blocked=reason)


def main() -> None:
    if _frozen_sha() != FROZEN_SHA:
        print("FROZEN_SHA_MISMATCH", flush=True)
        return
    if _busy() or _window(datetime.now().astimezone()):
        _blocked("PAPER_OR_WINDOW", {"speed": [], "parity": [], "days": {}, "parallel_match": None})
        return
    report = _load()
    report.setdefault("speed", [])
    report.setdefault("parity", [])
    report.setdefault("days", {})
    report["runner_sha"] = _runner_sha()
    report["frozen_sha"] = _frozen_sha()
    report["running"] = True
    bench_days = [day for day in BENCH_DAYS if day not in report["days"]]
    if bench_days or not report.get("chosen_workers"):
        sequential, speed1 = _run_sequential(list(BENCH_DAYS))
        report["speed"].append(speed1)
        if speed1.get("aborted") or any(row.get("error") for row in sequential):
            _blocked("SEQUENTIAL_BENCH_FAILED", report)
            return
        proofs = [prove_day(row) for row in sequential]
        report["parity"] = proofs
        if not all(row["ok"] for row in proofs):
            _blocked("TRADE_PARITY_FAILED", report)
            return
        if not all(_matches_published(row) for row in sequential):
            _blocked("PUBLISHED_DAILY_MISMATCH", report)
            return
        base = {row["day"]: _fingerprint(row) for row in sequential}
        for row in sequential:
            report["days"][row["day"]] = _store_day(row)
        del sequential
        chosen = 1
        best = speed1["wall_sec"]
        ram = psutil.virtual_memory().total
        parallel_match = True
        for workers in (2, 4):
            if speed1["peak_rss_bytes"] * workers > ram * RAM_FRACTION:
                report["speed"].append({"workers": workers, "skipped": "RSS_BUDGET", "projected_rss": speed1["peak_rss_bytes"] * workers})
                continue
            parallel, speed_n = _run_parallel(list(BENCH_DAYS), workers)
            if speed_n.get("aborted") or any(row.get("error") for row in parallel) or len(parallel) != len(BENCH_DAYS):
                speed_n["match"] = False
                report["speed"].append(speed_n)
                continue
            fingerprints = {row["day"]: _fingerprint(row) for row in parallel}
            matched = fingerprints == base
            speed_n["match"] = matched
            report["speed"].append(speed_n)
            del parallel
            if not matched:
                parallel_match = False
                break
            if speed_n["wall_sec"] < best:
                best = speed_n["wall_sec"]
                chosen = workers
        report["chosen_workers"] = chosen
        report["parallel_match"] = parallel_match
        _save(report)
        if not parallel_match:
            _blocked("PARALLEL_MISMATCH", report)
            return
    remaining = [day for day in _historical_days() if day not in report["days"]]
    started = time.perf_counter()
    if remaining:
        if report["chosen_workers"] == 1:
            produced, _speed = _run_sequential(remaining)
            if _speed.get("aborted"):
                _blocked("PAPER_OR_MEMORY", report)
                return
        else:
            produced, _speed = _run_parallel(remaining, int(report["chosen_workers"]))
            if _speed.get("aborted"):
                for row in produced:
                    if not row.get("error"):
                        report["days"][row["day"]] = _store_day(row)
                _save(report)
                _blocked("PAPER_OR_MEMORY", report)
                return
        for row in produced:
            if row.get("error"):
                report.setdefault("errors", []).append({"day": row.get("day"), "error": row.get("error")})
                _save(report)
                _blocked("DAY_FAILED", report)
                return
            if not _matches_published(row):
                report.setdefault("errors", []).append({"day": row.get("day"), "error": "PUBLISHED_DAILY_MISMATCH"})
                _save(report)
                _blocked("PUBLISHED_DAILY_MISMATCH", report)
                return
            report["days"][row["day"]] = _store_day(row)
            _save(report)
            if _busy() or _window(datetime.now().astimezone()):
                _blocked("PAPER_OR_WINDOW", report)
                return
    report["full_wall_sec"] = time.perf_counter() - started
    _finish(report)


if __name__ == "__main__":
    main()
