#!/usr/bin/env python
"""8/12 PM V1R native Passive Fill: Frozen research evaluator vs live (no strategy edits).

Uses:
  research.e1_x28_executable_joint.board.load_board_events  (Capture→board SoT loader)
  research.e1_x34a_execution_policy.arms.find_ask_cross_fill (PASSIVE_FILL_ENTRY_V1 SoT)
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "src"))

from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY, load_board_events
from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from small_paper.v1r_primary_runtime import WAIT_SEC

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260812"
OUT = ROOT / "results" / "research" / "v1r_pm_passive_fill_parity_20260812"
OUT.mkdir(parents=True, exist_ok=True)

PM_SESSIONS = [
    "live_session_122528",
    "live_session_125417",
    "live_session_145248",
]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def extract_pm_pendings() -> list[dict[str, Any]]:
    """All PM PENDING paired with live EXPIRED outcome."""
    out: list[dict[str, Any]] = []
    for sess_name in PM_SESSIONS:
        sess = ROOT / "results" / "small_paper" / DAY / sess_name
        native = _load_jsonl(sess / "v1r_native_entry_trace.jsonl")
        pendings = [r for r in native if r.get("kind") == "V1R_ENTRY_PENDING"]
        expired = {
            (str(r.get("symbol")), str(r.get("anchor"))): r
            for r in native
            if r.get("kind") == "V1R_EXPIRED"
        }
        fills = {
            (str(r.get("symbol")), str(r.get("anchor"))): r
            for r in native
            if r.get("kind") == "V1R_FILL"
        }
        for i, p in enumerate(pendings):
            sym = str(p.get("symbol"))
            anchor = str(p.get("anchor"))
            key = (sym, anchor)
            ex = expired.get(key)
            fl = fills.get(key)
            if fl is not None:
                live_result = "FILL"
                live_fill_time = fl.get("fill_time")
                signal_time = float(fl.get("signal_time") or 0)
            elif ex is not None:
                live_result = "EXPIRED"
                live_fill_time = None
                signal_time = float(ex.get("signal_time") or 0)
            else:
                live_result = "UNKNOWN"
                live_fill_time = None
                signal_time = 0.0
                # fallback: parse ts to minute start
                try:
                    dt = datetime.fromisoformat(str(p.get("ts")).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=JST)
                    signal_time = dt.astimezone(JST).replace(second=0, microsecond=0).timestamp()
                except Exception:
                    pass
            expire_time = float(ex.get("expire_time")) if ex and ex.get("expire_time") is not None else (
                signal_time + float(WAIT_SEC) if signal_time else None
            )
            out.append(
                {
                    "idx": len(out) + 1,
                    "session": sess_name,
                    "symbol": sym,
                    "anchor": anchor,
                    "pending_time": p.get("ts"),
                    "limit_price": float(p.get("limit")),
                    "signal_time": signal_time,
                    "expiry_time": expire_time,
                    "live_result": live_result,
                    "live_fill_time": live_fill_time,
                    "live_fill_price": fl.get("fill_price") if fl else None,
                    "score": p.get("score"),
                    "rank": p.get("rank"),
                }
            )
    return out


def window_board_stats(
    board: dict[str, np.ndarray],
    *,
    t0: float,
    wait_sec: float,
    limit: float,
) -> dict[str, Any]:
    """Diagnostics inside [t0, t0+wait] — does not replace find_ask_cross_fill."""
    t = board["t"]
    if t.size == 0:
        return {
            "push_n_in_window": 0,
            "board_empty": True,
            "min_ask": None,
            "any_ask_le_limit": False,
            "any_qty_ge_100": False,
            "any_fresh_ok": False,
            "any_special": False,
            "rows": [],
        }
    lim_t = t0 + float(wait_sec)
    i0 = int(np.searchsorted(t, t0, side="left"))
    rows = []
    min_ask = None
    any_le = False
    any_qty = False
    any_fresh = False
    any_special = False
    for i in range(i0, t.size):
        ti = float(t[i])
        if ti + 1e-12 < t0:
            continue
        if ti > lim_t + 1e-12:
            break
        ask = float(board["ask"][i]) if np.isfinite(board["ask"][i]) else None
        qty = float(board["ask_qty"][i]) if np.isfinite(board["ask_qty"][i]) else None
        fresh = float(board["fresh_sec"][i]) if np.isfinite(board["fresh_sec"][i]) else None
        special = bool(board["special"][i])
        if ask is not None:
            min_ask = ask if min_ask is None else min(min_ask, ask)
            if ask <= limit + 1e-12:
                any_le = True
        if qty is not None and qty >= MIN_QTY:
            any_qty = True
        if fresh is not None and fresh <= BOARD_FRESHNESS_SEC + 1e-12:
            any_fresh = True
        if special:
            any_special = True
        rows.append(
            {
                "t": ti,
                "t_iso": datetime.fromtimestamp(ti, JST).isoformat(timespec="milliseconds"),
                "ask": ask,
                "ask_qty": qty,
                "fresh_sec": fresh,
                "special": special,
                "ask_le_limit": bool(ask is not None and ask <= limit + 1e-12),
                "qty_ok": bool(qty is not None and qty >= MIN_QTY),
                "fresh_ok": bool(fresh is not None and fresh <= BOARD_FRESHNESS_SEC + 1e-12),
            }
        )
    return {
        "push_n_in_window": len(rows),
        "board_empty": False,
        "min_ask": min_ask,
        "any_ask_le_limit": any_le,
        "any_qty_ge_100": any_qty,
        "any_fresh_ok": any_fresh,
        "any_special": any_special,
        "rows": rows,
    }


def replay_one(pend: dict[str, Any], board_cache: dict[str, Any]) -> dict[str, Any]:
    sym = pend["symbol"]
    if sym not in board_cache:
        board_cache[sym] = load_board_events(DAY, sym)
    board = board_cache[sym]
    t0 = float(pend["signal_time"])
    wait = float(WAIT_SEC)
    limit = float(pend["limit_price"])
    sess_end = t0 + 3 * 3600.0

    fill = find_ask_cross_fill(
        board,
        t0=t0,
        wait_sec=wait,
        limit_price=limit,
        sess_end=sess_end,
    )
    stats = window_board_stats(board, t0=t0, wait_sec=wait, limit=limit)

    research_result = "FILL" if fill.get("filled") else "EXPIRED"
    research_fill_time = fill.get("fill_t") if fill.get("filled") else None
    research_fill_price = fill.get("fill_price") if fill.get("filled") else None

    live = pend["live_result"]
    if research_result == live:
        diff = "MATCH"
    elif research_result == "FILL" and live == "EXPIRED":
        diff = "RESEARCH_FILL_LIVE_EXPIRED"
    elif research_result == "EXPIRED" and live == "FILL":
        diff = "RESEARCH_EXPIRED_LIVE_FILL"
    else:
        diff = f"OTHER:{research_result}_vs_{live}"

    # time semantics helpers
    t0_iso = datetime.fromtimestamp(t0, JST).isoformat(timespec="milliseconds")
    exp_iso = (
        datetime.fromtimestamp(float(pend["expiry_time"]), JST).isoformat(timespec="milliseconds")
        if pend.get("expiry_time")
        else None
    )
    wall_minute = datetime.fromtimestamp(t0, JST).strftime("%H:%M:%S")

    return {
        **pend,
        "wait_sec": wait,
        "window_start": t0,
        "window_start_iso": t0_iso,
        "window_end": t0 + wait,
        "window_end_iso": exp_iso,
        "wall_minute": wall_minute,
        "board_n_total": int(board["t"].size),
        "research_result": research_result,
        "research_fill_time": research_fill_time,
        "research_fill_time_iso": (
            datetime.fromtimestamp(float(research_fill_time), JST).isoformat(timespec="milliseconds")
            if research_fill_time is not None
            else None
        ),
        "research_fill_price": research_fill_price,
        "research_reason": fill.get("reason"),
        "research_cross_ask": fill.get("cross_ask"),
        "research_waited_sec": fill.get("waited_sec"),
        "live_result": live,
        "diff": diff,
        "window_stats": {
            k: stats[k]
            for k in (
                "push_n_in_window",
                "board_empty",
                "min_ask",
                "any_ask_le_limit",
                "any_qty_ge_100",
                "any_fresh_ok",
                "any_special",
            )
        },
        "window_rows": stats["rows"],
        "evaluator": "research.e1_x34a_execution_policy.arms.find_ask_cross_fill",
        "board_loader": "research.e1_x28_executable_joint.board.load_board_events",
        "board_source": f"data/push_jsonl/2026-08-12/{sym}.T.jsonl",
    }


def historical_sanity() -> dict[str, Any]:
    x34a = json.loads(
        (ROOT / "results" / "research" / "e1_x34a_execution_policy" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    x36 = json.loads(
        (ROOT / "results" / "research" / "e1_x36_joint_allocator" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    # X34A passive primary wait=1s fill rate
    passive = None
    for key in ("passive", "PASSIVE_BID_CONSERVATIVE", "arms"):
        if key in x34a and isinstance(x34a[key], dict) and "fill_rate" in x34a[key]:
            passive = x34a[key]
            break
    # often nested under selected policy block
    if passive is None:
        # scan for wait_primary fill_rate near policy
        for k, v in x34a.items():
            if isinstance(v, dict) and v.get("arm") in (
                "PASSIVE_BID_CONSERVATIVE",
                "PASSIVE",
            ):
                passive = v
                break
    # explicit known structure
    if "wait_1s" in x34a:
        passive = x34a["wait_1s"]
    # from earlier grep: fill_rate ~0.095 under some block — also policy_manifest wait 1.0
    # Prefer nested full arm if present
    for path in (
        ("passive_bid_conservative",),
        ("results", "passive"),
    ):
        cur: Any = x34a
        ok = True
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False
                break
        if ok and isinstance(cur, dict) and "fill_rate" in cur:
            passive = cur
            break

    # brute: find first dict with fill_rate and signals/fills near wait 1
    candidates = []

    def walk(obj: Any, trail: str = "") -> None:
        if isinstance(obj, dict):
            if "fill_rate" in obj and (
                "fills" in obj or "signals" in obj or "n" in obj or "arm" in obj
            ):
                candidates.append((trail, obj.get("fill_rate"), obj.get("fills"), obj.get("signals") or obj.get("n"), obj.get("arm")))
            for k, v in obj.items():
                walk(v, f"{trail}.{k}" if trail else k)

    walk(x34a)
    # pick passive-looking
    passive_pick = None
    for trail, fr, fills, sig, arm in candidates:
        if arm and "PASSIVE" in str(arm).upper():
            passive_pick = {
                "path": trail,
                "fill_rate": fr,
                "fills": fills,
                "signals": sig,
                "arm": arm,
            }
            break
    if passive_pick is None and candidates:
        # second block often passive in report (~0.095)
        for trail, fr, fills, sig, arm in candidates:
            if fr is not None and 0.05 < float(fr) < 0.2:
                passive_pick = {
                    "path": trail,
                    "fill_rate": fr,
                    "fills": fills,
                    "signals": sig,
                    "arm": arm,
                }
                break

    x36_admitted = None
    walk2: list = []

    def walk36(obj: Any, trail: str = "") -> None:
        if isinstance(obj, dict):
            if "fill_rate_admitted" in obj:
                walk2.append(
                    {
                        "path": trail,
                        "fill_rate_admitted": obj.get("fill_rate_admitted"),
                        "admitted": obj.get("admitted"),
                        "fills": obj.get("fills"),
                    }
                )
            for k, v in obj.items():
                walk36(v, f"{trail}.{k}" if trail else k)

    walk36(x36)
    # preferred overall
    for row in walk2:
        if row["path"].endswith("overall") or "overall" in row["path"] or row["path"] == "":
            x36_admitted = row
            break
    if x36_admitted is None and walk2:
        # largest admitted
        x36_admitted = max(walk2, key=lambda r: float(r.get("admitted") or 0))

    return {
        "x34a_passive_fill": passive_pick,
        "x34a_wait_primary_sec": x34a.get("wait_primary_sec"),
        "x34a_selected_policy": x34a.get("selected_execution_policy"),
        "x34a_fill_evidence_rule": x34a.get("fill_evidence_rule"),
        "x36_fill_rate_admitted": x36_admitted,
        "note": "Historical rates are multi-day research cohorts; not i.i.d. vs single PM day 40 admits",
    }


def main() -> int:
    pendings = extract_pm_pendings()
    assert len(pendings) == 40, f"expected 40 PENDING, got {len(pendings)}"

    board_cache: dict[str, Any] = {}
    rows = [replay_one(p, board_cache) for p in pendings]

    research_fills = sum(1 for r in rows if r["research_result"] == "FILL")
    research_expired = sum(1 for r in rows if r["research_result"] == "EXPIRED")
    live_fills = sum(1 for r in rows if r["live_result"] == "FILL")
    live_expired = sum(1 for r in rows if r["live_result"] == "EXPIRED")
    diffs = Counter(r["diff"] for r in rows)
    board_missing = sum(1 for r in rows if r["board_n_total"] == 0)
    research_fill_live_exp = diffs.get("RESEARCH_FILL_LIVE_EXPIRED", 0)
    match_n = diffs.get("MATCH", 0)

    # verdict
    if board_missing == 40:
        verdict = "ZERO_FILL_CAUSE_NOT_PROVEN"
        verdict_reason = "all 40 boards empty / loader failed"
    elif research_fill_live_exp >= 1:
        # check time semantics: if window has rows but live missed
        verdict = "V1R_NATIVE_PASSIVE_FILL_LIVE_PARITY_BUG"
        verdict_reason = (
            f"research FILL on {research_fill_live_exp} cases while live EXPIRED"
        )
    elif research_fills == 0 and live_fills == 0 and match_n == 40:
        verdict = "PM_ZERO_FILL_CONFIRMED_BY_FROZEN_REPLAY"
        verdict_reason = "frozen find_ask_cross_fill agrees EXPIRED on all 40"
    elif match_n < 40 and research_fills == live_fills == 0:
        # both expired but mismatch flags?
        verdict = "PM_ZERO_FILL_CONFIRMED_BY_FROZEN_REPLAY"
        verdict_reason = f"research fills=0 live fills=0 match={match_n}/40 diffs={dict(diffs)}"
    else:
        # inspect for time semantics: signal_time vs board clock
        # if boards have pushes but all expired for both — still confirmed
        if research_fills == 0 and live_fills == 0:
            verdict = "PM_ZERO_FILL_CONFIRMED_BY_FROZEN_REPLAY"
            verdict_reason = "both sides zero fill"
        else:
            verdict = "ZERO_FILL_CAUSE_NOT_PROVEN"
            verdict_reason = f"unexpected mix diffs={dict(diffs)}"

    # Extra: if research expired but window had ask<=limit with qty/fresh — would indicate evaluator vs diagnostic conflict
    soft_near_miss = [
        r
        for r in rows
        if r["research_result"] == "EXPIRED"
        and r["window_stats"]["any_ask_le_limit"]
        and r["window_stats"]["any_qty_ge_100"]
        and r["window_stats"]["any_fresh_ok"]
    ]

    hist = historical_sanity()

    summary = {
        "day": DAY,
        "day_status": "INVALID / OPERATIONAL_VALIDATION_ONLY",
        "prospective": False,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "wait_sec": WAIT_SEC,
        "evaluator": "find_ask_cross_fill (e1_x34a)",
        "board_loader": "load_board_events (e1_x28) ← data/push_jsonl/2026-08-12",
        "research": {
            "pending": 40,
            "fills": research_fills,
            "expired": research_expired,
            "fill_rate": research_fills / 40.0,
        },
        "live": {
            "pending": 40,
            "fills": live_fills,
            "expired": live_expired,
            "fill_rate": live_fills / 40.0,
        },
        "diffs": dict(diffs),
        "board_missing_n": board_missing,
        "soft_near_miss_n": len(soft_near_miss),
        "soft_near_miss_note": (
            "rows where window had ask<=limit & qty>=100 & fresh OK but evaluator still EXPIRED "
            "(e.g. special quote / ordering) — inspect window_rows"
        ),
        "historical_sanity": hist,
        "submit_cancel_live": "0/0/0",
        "strategy_mutation": False,
    }

    # compact comparison table
    table = [
        {
            "idx": r["idx"],
            "symbol": r["symbol"],
            "anchor": r["anchor"],
            "session": r["session"],
            "limit": r["limit_price"],
            "signal_time_iso": r["window_start_iso"],
            "expiry_iso": r["window_end_iso"],
            "research": r["research_result"],
            "research_fill_time": r["research_fill_time_iso"],
            "research_fill_price": r["research_fill_price"],
            "research_reason": r["research_reason"],
            "live": r["live_result"],
            "live_fill_time": r["live_fill_time"],
            "diff": r["diff"],
            "push_n_in_window": r["window_stats"]["push_n_in_window"],
            "min_ask": r["window_stats"]["min_ask"],
            "any_ask_le_limit": r["window_stats"]["any_ask_le_limit"],
            "any_qty_ge_100": r["window_stats"]["any_qty_ge_100"],
            "any_fresh_ok": r["window_stats"]["any_fresh_ok"],
            "any_special": r["window_stats"]["any_special"],
            "board_n_total": r["board_n_total"],
        }
        for r in rows
    ]

    (OUT / "parity_rows.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "parity_table.json").write_text(
        json.dumps(table, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    # markdown table
    md = [
        "# V1R PM Passive Fill parity — 2026-08-12",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        f"- reason: {verdict_reason}",
        f"- research fills/expired/rate: {research_fills}/{research_expired}/{research_fills/40:.1%}",
        f"- live fills/expired/rate: {live_fills}/{live_expired}/{live_fills/40:.1%}",
        f"- diffs: `{dict(diffs)}`",
        "",
        "| # | symbol | anchor | limit | research | live | diff | push_n | min_ask | ask<=lim | qty>=100 | fresh_ok |",
        "|---|--------|--------|------:|----------|------|------|-------:|--------:|:--------:|:--------:|:--------:|",
    ]
    for r in table:
        md.append(
            f"| {r['idx']} | {r['symbol']} | {r['anchor']} | {r['limit']} | "
            f"{r['research']} | {r['live']} | {r['diff']} | {r['push_n_in_window']} | "
            f"{r['min_ask'] if r['min_ask'] is not None else '—'} | "
            f"{r['any_ask_le_limit']} | {r['any_qty_ge_100']} | {r['any_fresh_ok']} |"
        )
    md.append("")
    md.append("## Historical sanity (not i.i.d.)")
    md.append(f"```json\n{json.dumps(hist, ensure_ascii=False, indent=2)}\n```")
    (OUT / "report.md").write_text("\n".join(md), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print(f"OUT={OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
