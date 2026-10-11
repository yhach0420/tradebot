"""EXIT structure study. Research replay only. The sealed executor is not edited."""
from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
from openpyxl import Workbook

from research.current50_v16_historical_economic_expansion_v2.expand import _attach_text
from research.low_price_risk_review import jpx_tick_size_yen
from research.v16_sub10s_support_failure_causal_rca_v1.rca import (
    NEW_ACTUAL,
    BidAwarePolicyB,
    _bind,
    _historical_days,
)
from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import (
    _capture_dir,
    _symbol,
)
from research.event_time_impulse_complete_strategy_v2.rules import support_failure

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "v16_exit_structural_failure_research_v1"
BUSY = (
    "paper_trade_checked_runner",
    "market_ingress_service",
    "run_core10_dynamic40_am_pm_daily_runner",
    "run_small_paper_pilot",
    "v1r_paper_primary_launcher",
)
UNFILLED = (
    {"day": "20260817", "symbol": "6265.T", "entry_px": 3345.0, "notional": 334500.0},
    {"day": "20260825", "symbol": "6138.T", "entry_px": 2080.0, "notional": 208000.0},
    {"day": "20260902", "symbol": "7376.T", "entry_px": 823.0, "notional": 82300.0},
    {"day": "20260909", "symbol": "4052.T", "entry_px": 1021.0, "notional": 102100.0},
)


def _busy() -> list[str]:
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        "| ForEach-Object { $_.CommandLine }"
    )
    try:
        text = subprocess.check_output(["powershell", "-NoProfile", "-Command", script], text=True, errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return ["PROCESS_CHECK_FAILED"]
    return sorted({marker for marker in BUSY if marker in text})


def _paper_window(now: datetime) -> bool:
    return now.weekday() < 5 and 7 <= now.hour < 16


def _first_bid(st: Any, target: float, sess_end: float) -> Optional[float]:
    lo = 0
    hi = len(st.bids)
    while lo < hi:
        mid = (lo + hi) // 2
        if float(st.bids[mid][0]) < target - 1e-9:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(st.bids):
        return None
    event_t, price = st.bids[lo]
    if float(event_t) > sess_end + 1e-9 or not (price == price and price > 0):
        return None
    return float(price)


def _scan(st: Any, trade: dict[str, Any], sess_end: float) -> dict[str, Any]:
    index = int(trade["signal_index"])
    entry_t = float(st.t[index])
    support = float(trade["initial_support"])
    current = float(st.px[index])
    tick = float(jpx_tick_size_yen(current))
    lo = int(np.searchsorted(st.t[: index + 1], entry_t - 60.0, side="left"))
    lo10 = int(np.searchsorted(st.t[: index + 1], entry_t - 10.0, side="left"))
    highs = 0
    run_high = -1e300
    max_pull = 0.0
    min_step = None
    previous = None
    finite_n = 0
    for probe in range(lo, index + 1):
        price = float(st.px[probe])
        if not (price == price and price > 0):
            continue
        finite_n += 1
        if price > run_high + 1e-9:
            highs += 1
            run_high = price
        else:
            max_pull = max(max_pull, run_high - price)
        if previous is not None and abs(price - previous) > 1e-9:
            diff = abs(price - previous)
            min_step = diff if min_step is None else min(min_step, diff)
        previous = price
    base = float(st.px[lo10]) if lo10 <= index else float("nan")
    ret10 = current - base if base == base and base > 0 else None
    trigger = None
    second = None
    one_tick = None
    seen_t = None
    breach = support - tick
    horizon = min(entry_t + 60.0, sess_end)
    end = int(np.searchsorted(st.t[: st.n], horizon, side="right"))
    min_px = current
    for probe in range(index + 1, end):
        price = float(st.px[probe])
        if not (price == price and price > 0):
            continue
        min_px = min(min_px, price)
        if trigger is None and support_failure(price, support):
            trigger = probe
            seen_t = float(st.t[probe])
        elif trigger is not None and second is None and support_failure(price, support) and abs(float(st.t[probe]) - seen_t) > 1e-6:
            second = probe
        if one_tick is None and support_failure(price, breach):
            one_tick = probe
    recovered = False
    if trigger is not None:
        limit = min(float(st.t[trigger]) + 60.0, sess_end)
        stop = int(np.searchsorted(st.t[: st.n], limit, side="right"))
        for probe in range(trigger + 1, stop):
            price = float(st.px[probe])
            if price == price and price > support + 1e-9:
                recovered = True
                break
    delayed_bid = None if one_tick is None else _first_bid(st, float(st.t[one_tick]), sess_end)
    actual_pnl = float(trade["pnl_yen"])
    delayed_pnl = None if delayed_bid is None else (delayed_bid - float(trade["entry_px"])) * 100.0
    return {
        "symbol": trade["symbol"],
        "reason": trade["reason"],
        "pnl": actual_pnl,
        "hold_sec": float(trade["hold_sec"]),
        "entry_t": entry_t,
        "entry_px": float(trade["entry_px"]),
        "exit_px": float(trade["exit_px"]),
        "support": support,
        "current": current,
        "tick": tick,
        "margin": current - support,
        "margin_ticks": (current - support) / tick if tick else None,
        "prints_60": finite_n,
        "high_updates_60": highs,
        "max_pullback": max_pull,
        "min_step": min_step,
        "ret_10": ret10,
        "min_px_60": min_px,
        "adverse_60_yen": (float(trade["entry_px"]) - min_px) * 100.0,
        "trigger_delay": None if trigger is None else float(st.t[trigger]) - entry_t,
        "recovered_60": recovered,
        "one_tick_hit": one_tick is not None,
        "one_tick_delay": None if one_tick is None else float(st.t[one_tick]) - entry_t,
        "delayed_pnl": delayed_pnl,
        "second_print": second is not None,
        "sub10": trade["reason"] == "BREAK_SUPPORT_FAILURE" and float(trade["hold_sec"]) < 10.0,
    }


def _feed(day: str, kind: str) -> dict[str, Any]:
    capture = _capture_dir(day)
    if capture is None:
        return {"error": "NO_CAPTURE"}
    book = BidAwarePolicyB()
    _bind(book, day, kind)
    if len(book.admission_membership) != 50:
        return {"error": f"ADMISSION_{len(book.admission_membership)}"}
    attach = _attach_text(day, kind)
    fed = 0
    for part in sorted(capture.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("kind") not in (None, "market_push"):
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
                book.on_market_event({"symbol": symbol, "payload": payload})
                fed += 1
                if fed % 200000 == 0:
                    print(f"fed {day} {kind} {fed}", flush=True)
                if book._closed:
                    break
        if book._closed:
            break
    if not book._closed:
        book.close_at_runtime_boundary(day=day, kind="pm" if book.flatten_deadline is not None else "am")
    rows = []
    pnls_exit_order = []
    for trade in book.trades:
        rows.append(_scan(book.syms[str(trade["symbol"])], trade, float(book.sess_end)))
        pnls_exit_order.append(float(trade["pnl_yen"]))
    pnls_entry_order = [float(row["pnl"]) for row in sorted(rows, key=lambda item: float(item["entry_t"]))]
    print(f"PATH {day} {kind} trades {len(rows)} gross {round(sum(pnls_exit_order), 2)}", flush=True)
    return {
        "day": day,
        "kind": kind,
        "trades": len(rows),
        "gross": round(sum(pnls_exit_order), 6),
        "dd_exit_order": _dd(pnls_exit_order),
        "dd_entry_order": _dd(pnls_entry_order),
        "rows": rows,
    }


def _dd(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _pct(rows: list[dict[str, Any]], key: str, q: float) -> Optional[float]:
    values = sorted(float(row[key]) for row in rows if row.get(key) is not None)
    if not values:
        return None
    pos = min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))
    return values[pos]


def _rate(rows: list[dict[str, Any]], fn) -> Optional[float]:
    if not rows:
        return None
    return sum(1 for row in rows if fn(row)) / len(rows)


def _cluster(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["day"], row["symbol"])].append(row)
    compared = 0
    sub_smaller = 0
    for items in groups.values():
        sub = [row["margin_ticks"] for row in items if row["sub10"] and row.get("margin_ticks") is not None]
        impulse = [row["margin_ticks"] for row in items if row["reason"] == "IMPULSE_EXHAUSTED" and row.get("margin_ticks") is not None]
        if not sub or not impulse:
            continue
        compared += 1
        if sorted(sub)[len(sub) // 2] + 1e-9 < sorted(impulse)[len(impulse) // 2]:
            sub_smaller += 1
    return {"symbol_days_with_both": compared, "symbol_days_sub10_margin_smaller": sub_smaller}


def _save(report: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    temporary = OUT / "report.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    temporary.replace(OUT / "report.json")


def _workbook(report: dict[str, Any]) -> None:
    book = Workbook()
    sheets = {
        "Phase0": ["item", "value"],
        "Path": ["group", "trades", "median_margin_ticks", "median_pullback", "recover_rate"],
        "Adverse": ["rule", "hit_rate", "p95_extra_loss", "missing_bid_rate"],
        "Economics": ["item", "value"],
    }
    first = True
    for name, header in sheets.items():
        sheet = book.active if first else book.create_sheet(name)
        first = False
        sheet.title = name
        sheet.append(header)
    for key, value in (report.get("phase0") or {}).items():
        book["Phase0"].append([key, str(value)])
    for row in report.get("path_rows") or []:
        book["Path"].append([row.get("group"), row.get("trades"), row.get("median_margin_ticks"), row.get("median_pullback"), row.get("recover_rate")])
    for row in report.get("adverse_rows") or []:
        book["Adverse"].append([row.get("rule"), row.get("hit_rate"), row.get("p95_extra_loss"), row.get("missing_bid_rate")])
    for key, value in (report.get("economics") or {}).items():
        book["Economics"].append([key, str(value)])
    book.save(OUT / "audit.xlsx")


def _finish(report: dict[str, Any]) -> None:
    rows = [row for session in report["sessions"] for row in session["rows"]]
    for session in report["sessions"]:
        session.pop("rows", None)
    sub = [row for row in rows if row["sub10"]]
    impulse = [row for row in rows if row["reason"] == "IMPULSE_EXHAUSTED"]
    longer = [row for row in rows if row["reason"] == "BREAK_SUPPORT_FAILURE" and not row["sub10"]]

    def pack(name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        margins = sorted(float(row["margin_ticks"]) for row in items if row.get("margin_ticks") is not None)
        pulls = sorted(float(row["max_pullback"]) for row in items)
        return {
            "group": name,
            "trades": len(items),
            "median_margin_ticks": margins[len(margins) // 2] if margins else None,
            "median_pullback": pulls[len(pulls) // 2] if pulls else None,
            "recover_rate": _rate(items, lambda row: row["recovered_60"]),
            "one_tick_hit_rate": _rate(items, lambda row: row["one_tick_hit"]),
            "gross": round(sum(float(row["pnl"]) for row in items), 6),
        }

    path_rows = [pack(name, items) for name, items in (
        ("SUB10_BREAK_SUPPORT", sub),
        ("LONGER_BREAK_SUPPORT", longer),
        ("IMPULSE_EXHAUSTED", impulse),
    )]
    delayed = [row for row in sub if row.get("delayed_pnl") is not None]
    extra = [float(row["delayed_pnl"]) - float(row["pnl"]) for row in delayed]
    missing_bid = _rate(sub, lambda row: row["one_tick_hit"] and row.get("delayed_pnl") is None)
    p95 = None
    if extra:
        ordered = sorted(extra)
        p95 = ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))]
    worse_5000 = _rate(delayed, lambda row: float(row["delayed_pnl"]) - float(row["pnl"]) <= -5000.0)
    recover_without_tick = _rate(sub, lambda row: row["recovered_60"] and not row["one_tick_hit"])
    structural = bool(sub and (recover_without_tick or 0) >= 0.50)
    unsafe = bool(
        (p95 is not None and p95 <= -5000.0)
        or (worse_5000 or 0) > 0.25
        or (missing_bid or 0) > 0.01
    )
    report.update(_decision(rows, path_rows, structural, unsafe, p95, worse_5000, missing_bid, recover_without_tick, extra, delayed, sub))
    text = _markdown(report)
    report["markdown"] = text
    (OUT / "report.md").write_text(text, encoding="utf-8")
    slim = dict(report)
    slim.pop("sessions", None)
    _save(slim)
    _workbook(slim)
    print(report["verdict"], report["entry_time_separation"], report["exit_repair_economics"], flush=True)


def _decision(rows, path_rows, structural, unsafe, p95, worse_5000, missing_bid, recover_without_tick, extra, delayed, sub) -> dict[str, Any]:
    cluster = _cluster(rows)
    comparable = cluster["symbol_days_with_both"]
    separated = bool(
        comparable
        and cluster["symbol_days_sub10_margin_smaller"] / comparable >= 0.70
    )
    phase0 = {
        "dd_gap_yen": 200.0,
        "dd_cause": "Prior DD walks ledger entry order. The RCA DD walks exit-completion order. Gross is the same +712000.",
        "cost_wording_error": "At 50 and 100 yen per side, B+M loses less than B because it has 712 trades rather than 9818. At 0 and 10 yen per side, B remains far ahead.",
        "b_only_trades": 9513,
        "shared_trades": 305,
        "bm_only_trades": 407,
        "bm_only_meaning": "The 407 trades exist only in the independent B+M state machine. They were not created by deleting B rows.",
        "unfilled_n": 4,
        "unfilled_entry_notional_yen": 726900.0,
        "unfilled_booked_pnl": False,
        "unfilled_residual": "Four PM positions had no bid before 15:25 and left the book with no closing price. Their entry notionals sum to 726900 yen. That is unresolved exposure, not an assigned loss.",
    }
    return {
        "verdict": "V16_EXIT_STRUCTURAL_FAILURE_CONFIRMED_V1" if structural else "V16_EXIT_STRUCTURAL_FAILURE_NOT_CONFIRMED_V1",
        "next": "DO_NOT_PROMOTE_AN_EXIT_DELAY_WITHOUT_A_LATER_SAMPLE" if unsafe or not structural else "FREEZE_ONE_TICK_CONFIRMATION_AND_REPLAY_THE_FULL_STATE_MACHINE_ON_A_LATER_SAMPLE",
        "entry_time_separation": "SUPPORTED" if separated else "NOT_SUPPORTED",
        "exit_repair_economics": "NOT_EVALUABLE",
        "cost_robustness": "B_GROSS_FAILS_AT_50_YEN_PER_SIDE_AND_NO_REPAIR_WAS_PROMOTED",
        "later_sample_validation": "PENDING",
        "paper_runtime_unchanged": True,
        "policy_adopted": False,
        "submit_cancel_live": "0/0/0",
        "phase0": phase0,
        "path_rows": path_rows,
        "adverse_rows": [
            {
                "rule": "ONE_JPX_TICK_BELOW_FROZEN_HIGH",
                "hit_rate": _rate(sub, lambda row: row["one_tick_hit"]),
                "p95_extra_loss": p95,
                "missing_bid_rate": missing_bid,
                "share_at_least_5000_worse": worse_5000,
                "unsafe": unsafe,
            }
        ],
        "cluster": cluster,
        "recover_without_one_tick": recover_without_tick,
        "economics": {
            "retrospective_trades": len(rows),
            "retrospective_gross": round(sum(float(row["pnl"]) for row in rows), 6),
            "exit_candidate": None,
            "reason": "The one-tick confirmation fails the predeclared safety gate." if unsafe else "No complete-strategy replay was run.",
        },
        "running": False,
    }


def _markdown(report: dict[str, Any]) -> str:
    phase0 = report["phase0"]
    lines = [
        f"VERDICT: {report['verdict']}",
        f"NEXT: {report['next']}",
        f"ENTRY_TIME_SEPARATION: {report['entry_time_separation']}",
        f"EXIT_REPAIR_ECONOMICS: {report['exit_repair_economics']}",
        f"COST_ROBUSTNESS: {report['cost_robustness']}",
        f"LATER_SAMPLE_VALIDATION: {report['later_sample_validation']}",
        "PAPER_RUNTIME_UNCHANGED: true",
        "",
        "Policy BもEXIT候補も採用していません。Paper V16 と Runtime は変更していません。submit/cancel/live = 0/0/0。",
        "",
        "## Phase 0",
        "前回のGross +712,000円は同じ取引集合です。最大DDの200円差は、前回がENTRY順、RCAがEXIT約定順で累積したためです。",
        "片道0円と10円ではBがB+Mより高い損益です。片道50円ではBが -269,800円、B+Mが -56,660円、片道100円ではBが -1,251,600円、B+Mが -127,860円です。高コストでB+Mの赤字が小さいのは712件と9,818件の件数差であり、優位性が増えたという意味ではありません。前回の「すべての費用でB+MがB以下」という文は、50円と100円については誤りでした。",
        f"Bだけにある取引は {phase0['b_only_trades']}件、両方にある取引は {phase0['shared_trades']}件、B+Mだけで生まれた取引は {phase0['bm_only_trades']}件です。これはENTRY拒否後の状態遷移で、事後削除ではありません。",
        "未約定4件のENTRY金額は 334,500円、208,000円、82,300円、102,100円で、合計726,900円です。EXIT価格も損益も空で、Grossには入っていません。15:25前のBidが無かった未解消の建玉であり、損失額は割り当てていません。",
        "",
        "## 経路",
        json.dumps(report.get("path_rows"), ensure_ascii=False),
        json.dumps(report.get("adverse_rows"), ensure_ascii=False),
        json.dumps(report.get("cluster"), ensure_ascii=False),
        f"支持線へ戻ったあと60秒以内に支持線上へ回復し、かつ1呼値も抜けなかった割合は {report.get('recover_without_one_tick')} です。",
        "M2は、Bidが支持線以下だったという観測です。それだけでスプレッドがEXITを起こしたとは確定しません。EXITを発火させるのは、その後の現値が凍結高値以下になることです。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    now = datetime.now().astimezone()
    if _busy() or _paper_window(now):
        print("DEFERRED", flush=True)
        return
    report: dict[str, Any] = {"running": True, "done": [], "sessions": []}
    if (OUT / "report.json").is_file():
        prior = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
        if prior.get("running"):
            report = prior
    done = set(report.get("done") or [])
    for day in _historical_days():
        for kind in ("am", "pm"):
            token = f"{day}:{kind}"
            if token in done:
                continue
            if _busy() or _paper_window(datetime.now().astimezone()):
                _save(report)
                print("DEFERRED", flush=True)
                return
            result = _feed(day, kind)
            if result.get("error"):
                report.setdefault("errors", []).append({"day": day, "kind": kind, "error": result["error"]})
            else:
                for row in result["rows"]:
                    row["day"] = day
                    row["kind"] = kind
                report["sessions"].append(result)
            done.add(token)
            report["done"] = sorted(done)
            _save(report)
    _finish(report)


if __name__ == "__main__":
    main()
