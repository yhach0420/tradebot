#!/usr/bin/env python
"""P0-3: 20260820 Exact Runtime Replay via current V1RNativeEntryLive path.

Reuses Capture → payload normalize → process_market_push → dual.on_tick.
Does not invent a new ENTRY/EXIT/Anchor simulator. Does not sleep.
Does not rewrite 20260820 Actual. Does not start Fast Replay.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

for _k in (
    "KABU_V1R_ENTRY_WEBHOOK_URL",
    "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
    "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
    "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
    "KABU_SHADOW_DISCORD_WEBHOOK_URL",
    "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
    "KABU_MARKET_CAPTURE_WEBHOOK_URL",
):
    os.environ.pop(_k, None)
os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from research.anchor_vs_event_driven.run_comparison import (  # noqa: E402
    _bare,
    _boot,
    _stream_day,
    extract_trades,
    historical_universe,
)
from small_paper.v1r_exit_v2_activation_gate import STRATEGY_SHA  # noqa: E402
from small_paper.v1r_exit_v2_contract import EXIT_V2_CANDIDATE_SHA  # noqa: E402
from small_paper.v1r_live_dual_lane import canonical_symbol_key  # noqa: E402
from small_paper.v1r_native_entry_live import (  # noqa: E402
    ANCHOR_SHA,
    ENTRY_SHA,
    V1RNativeEntryLive,
)
from small_paper.v1r_primary_runtime import CLOCK_GRID  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260820"
CAPTURE = (
    ROOT
    / "data"
    / "market_capture"
    / DAY
    / "session_ing_20260820_8372_1787179836_fab7382b"
)
A_FIXED_CACHE = ROOT / "results" / "research" / "anchor_vs_event_driven_v1r" / "day_cache" / f"{DAY}.json"


def _iso(ts: Optional[float]) -> str:
    if ts is None:
        return ""
    return datetime.fromtimestamp(float(ts), JST).isoformat(timespec="milliseconds")


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception:
        return ""


def _file_sha(rel: str) -> str:
    p = ROOT / rel
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _hm_epoch(day: str, h: int, m: int) -> float:
    return datetime(
        int(day[:4]), int(day[4:6]), int(day[6:]), h, m, tzinfo=JST
    ).timestamp()


def _anchor_from_fill_t(day: str, fill_t: float) -> str:
    last = ""
    for h, m in CLOCK_GRID:
        t0 = _hm_epoch(day, h, m)
        if t0 <= float(fill_t) + 1e-9:
            last = f"{h:02d}:{m:02d}"
        else:
            break
    return last


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(x for x in pnls if x > 0)
    losses = sum(-x for x in pnls if x < 0)
    if losses <= 1e-12:
        return None if wins <= 1e-12 else float("inf")
    return wins / losses


def _maxdd(trades: list[dict[str, Any]]) -> float:
    ordered = sorted(
        trades,
        key=lambda t: (float(t.get("exit_time") or t.get("fill_time") or 0.0), str(t.get("symbol") or "")),
    )
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for t in ordered:
        eq += float(t.get("pnl_yen_100") or 0.0)
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return round(dd, 2)


def _sess_stats(trades: list[dict[str, Any]], sess: str) -> dict[str, Any]:
    rows = [t for t in trades if t.get("session") == sess]
    pnl = round(sum(float(t.get("pnl_yen_100") or 0.0) for t in rows), 2)
    return {"trades": len(rows), "pnl": pnl}


def _canonical_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in trades:
        out.append(
            {
                "symbol": t.get("symbol"),
                "session": t.get("session"),
                "anchor_time": t.get("anchor_time"),
                "fill_time": round(float(t.get("fill_time") or 0.0), 6),
                "fill_price": t.get("fill_price"),
                "exit_time": round(float(t.get("exit_time") or 0.0), 6),
                "exit_price": t.get("exit_price"),
                "exit_reason": t.get("exit_reason"),
                "pnl_yen_100": round(float(t.get("pnl_yen_100") or 0.0), 4),
            }
        )
    out.sort(key=lambda r: (float(r["fill_time"]), str(r["symbol"])))
    return out


def _ledger_sha(trades: list[dict[str, Any]]) -> str:
    blob = json.dumps(_canonical_trades(trades), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def run_exact_once() -> dict[str, Any]:
    class ExactEngine(V1RNativeEntryLive):
        """Collector only — trading path is V1RNativeEntryLive._run_anchor / process_market_push."""

        def __init__(self, *a: Any, **k: Any) -> None:
            super().__init__(*a, **k)
            self.a_candidates: list[dict[str, Any]] = []
            self.a_admits: list[dict[str, Any]] = []
            self.a_fills: list[dict[str, Any]] = []
            self.a_expired: list[dict[str, Any]] = []
            self.snap_0940: Optional[dict[str, Any]] = None
            self.snapshots: dict[tuple[str, str], dict[str, Any]] = {}
            self.cap_blocked = 0

        def _harvest(self, events: list[dict[str, Any]], *, default_anchor: str = "") -> None:
            for ev in events:
                kind = str(ev.get("kind") or "")
                an = str(ev.get("anchor") or default_anchor)
                if kind == "ANCHOR_SYMBOL_SNAPSHOT":
                    row = {
                        "symbol": _bare(ev.get("symbol")),
                        "anchor": an,
                        "t0": ev.get("anchor_t0"),
                        "snapshot_sequence": ev.get("snapshot_sequence"),
                        "score": ev.get("model_score"),
                        "bid": (ev.get("Buy1") or {}).get("Price") if isinstance(ev.get("Buy1"), dict) else None,
                        "admitted": bool(ev.get("admitted")),
                        "rank": ev.get("rank"),
                    }
                    self.snapshots[(an, row["symbol"])] = row
                    if ev.get("model_score") is not None:
                        self.a_candidates.append(row)
                    if row["symbol"] == "285A" and an == "09:40":
                        self.snap_0940 = {
                            "snapshot_seq": ev.get("snapshot_sequence"),
                            "bid": row["bid"],
                            "score": ev.get("model_score"),
                            "admitted": bool(ev.get("admitted")),
                            "rank": ev.get("rank"),
                        }
                elif kind == "V1R_ENTRY_PENDING":
                    self.a_admits.append(
                        {
                            "symbol": _bare(ev.get("symbol")),
                            "anchor": an,
                            "t0": ev.get("signal_time") or ev.get("t0"),
                            "score": ev.get("score"),
                            "rank": ev.get("rank"),
                            "limit": ev.get("limit"),
                        }
                    )
                elif kind == "V1R_FILL":
                    self.a_fills.append(
                        {
                            "symbol": _bare(ev.get("symbol")),
                            "anchor": an,
                            "score": ev.get("score"),
                            "limit": ev.get("limit"),
                            "fill_price": ev.get("fill_price"),
                            "fill_time": ev.get("fill_time"),
                        }
                    )
                elif kind == "V1R_EXPIRED":
                    self.a_expired.append(
                        {
                            "symbol": _bare(ev.get("symbol")),
                            "anchor": an,
                            "limit": ev.get("limit"),
                            "signal_time": ev.get("signal_time"),
                        }
                    )
                elif kind == "CAP_BLOCKED":
                    self.cap_blocked += 1

        def _run_anchor(self, *, anchor: str, t0: float, day: str, session: str) -> list[dict[str, Any]]:
            self._harvest(self.events)
            self.events.clear()
            out = super()._run_anchor(anchor=anchor, t0=t0, day=day, session=session)
            self._harvest(self.events, default_anchor=anchor)
            self.events.clear()
            return out

    t_wall = time.perf_counter()
    universe, uni_src = historical_universe(DAY, CAPTURE)
    eng, dual = _boot(universe, ExactEngine)
    if dual is None or not eng.ready:
        return {
            "ok": False,
            "blocker": getattr(eng, "fail_reason", "dual_unavailable"),
            "universe": universe,
            "universe_source": uni_src,
        }
    events_n, last_et = _stream_day(DAY, CAPTURE, eng, dual)
    eng._harvest(eng.events)
    raw_trades = extract_trades(dual)
    exact_trades: list[dict[str, Any]] = []
    for i, tr in enumerate(raw_trades, start=1):
        fill_t = float(tr.get("entry_time") or 0.0)
        an = _anchor_from_fill_t(DAY, fill_t)
        sym = canonical_symbol_key(tr.get("symbol"))
        snap = eng.snapshots.get((an, sym), {})
        fill_row = next((f for f in eng.a_fills if f.get("symbol") == sym and f.get("anchor") == an), None)
        admit_row = next((a for a in eng.a_admits if a.get("symbol") == sym and a.get("anchor") == an), None)
        exact_trades.append(
            {
                "trade_id": f"{DAY}|{tr.get('session')}|{an}|{sym}|{i}",
                "symbol": sym,
                "session": tr.get("session"),
                "anchor_time": an,
                "snapshot_sequence": snap.get("snapshot_sequence"),
                "score": (fill_row or admit_row or snap).get("score") if (fill_row or admit_row or snap) else None,
                "candidate_rank": (admit_row or snap).get("rank") if (admit_row or snap) else None,
                "admission": True,
                "pending_time": _iso(fill_t),
                "limit": (fill_row or admit_row or {}).get("limit") or tr.get("entry_price"),
                "fill_time": fill_t,
                "fill_time_iso": _iso(fill_t),
                "fill_price": tr.get("entry_price"),
                "exit_time": tr.get("exit_time"),
                "exit_time_iso": _iso(tr.get("exit_time")),
                "exit_price": tr.get("exit_price"),
                "exit_reason": tr.get("reason"),
                "pnl_yen_100": float(tr.get("pnl_yen_100") or 0.0),
            }
        )
    pnls = [float(t.get("pnl_yen_100") or 0.0) for t in exact_trades]
    win = sum(1 for x in pnls if x > 0)
    loss = sum(1 for x in pnls if x < 0)
    draw = sum(1 for x in pnls if abs(x) <= 1e-12)
    snap = eng.snap_0940 or {}
    exp_0940 = next((e for e in eng.a_expired if e.get("symbol") == "285A" and e.get("anchor") == "09:40"), None)
    fill_0940 = next((f for f in eng.a_fills if f.get("symbol") == "285A" and f.get("anchor") == "09:40"), None)
    outcome_0940 = "FILL" if fill_0940 else ("EXPIRE" if exp_0940 else "NO_PENDING")
    snap["outcome"] = outcome_0940
    snap["limit"] = (fill_0940 or exp_0940 or {}).get("limit") if (fill_0940 or exp_0940) else snap.get("bid")
    return {
        "ok": True,
        "blocker": "",
        "universe": universe,
        "universe_n": len(universe),
        "universe_source": uni_src,
        "events_n": events_n,
        "last_et": last_et,
        "sequence_holes": int(eng.native_ingest_sequence_holes),
        "native_ingest_count": int(eng.native_ingest_count),
        "native_ingest_skip_duplicate": int(eng.native_ingest_skip_duplicate),
        "native_ingest_skip_universe": int(eng.native_ingest_skip_universe),
        "native_admitted": int(eng.primary_admitted),
        "native_fills": int(eng.primary_fills),
        "native_expired": int(eng.primary_expired),
        "anchor_fires": int(eng.anchor_fires),
        "cap_blocked": int(eng.cap_blocked),
        "candidates": eng.a_candidates,
        "admits": eng.a_admits,
        "fills": eng.a_fills,
        "expired": eng.a_expired,
        "trades": exact_trades,
        "win": win,
        "loss": loss,
        "draw": draw,
        "pnl": round(sum(pnls), 2),
        "PF": _pf(pnls),
        "maxDD": _maxdd(exact_trades),
        "AM": _sess_stats(exact_trades, "AM"),
        "PM": _sess_stats(exact_trades, "PM"),
        "elapsed_sec": round(time.perf_counter() - t_wall, 3),
        "snap_0940": snap,
        "engine_ready": bool(eng.ready),
        "fail_reason": eng.fail_reason,
        "last_ingested_sequence": eng.last_ingested_sequence,
    }


def compare_to_a_fixed(exact: dict[str, Any], cache: dict[str, Any]) -> dict[str, Any]:
    A = cache.get("A") or {}
    a_trades = list(A.get("trades") or [])
    a_norm = []
    for tr in a_trades:
        fill_t = float(tr.get("entry_time") or 0.0)
        a_norm.append(
            {
                "symbol": canonical_symbol_key(tr.get("symbol")),
                "session": tr.get("session"),
                "anchor_time": _anchor_from_fill_t(DAY, fill_t),
                "fill_time": fill_t,
                "fill_price": tr.get("entry_price"),
                "exit_time": tr.get("exit_time"),
                "exit_price": tr.get("exit_price"),
                "exit_reason": tr.get("reason"),
                "pnl_yen_100": float(tr.get("pnl_yen_100") or 0.0),
            }
        )
    e_norm = [
        {
            "symbol": t.get("symbol"),
            "session": t.get("session"),
            "anchor_time": t.get("anchor_time"),
            "fill_time": t.get("fill_time"),
            "fill_price": t.get("fill_price"),
            "exit_time": t.get("exit_time"),
            "exit_price": t.get("exit_price"),
            "exit_reason": t.get("exit_reason"),
            "pnl_yen_100": t.get("pnl_yen_100"),
        }
        for t in exact.get("trades") or []
    ]

    def key(r: dict[str, Any]) -> tuple:
        return (str(r.get("symbol")), str(r.get("session")), str(r.get("anchor_time")))

    a_map: dict[tuple, list[dict[str, Any]]] = {}
    e_map: dict[tuple, list[dict[str, Any]]] = {}
    for r in a_norm:
        a_map.setdefault(key(r), []).append(r)
    for r in e_norm:
        e_map.setdefault(key(r), []).append(r)
    rows = []
    used_e: set[tuple] = set()
    used_a: set[tuple] = set()
    for k, alist in a_map.items():
        elist = e_map.get(k) or []
        n = max(len(alist), len(elist))
        for i in range(n):
            a = alist[i] if i < len(alist) else None
            e = elist[i] if i < len(elist) else None
            if a and e:
                used_a.add(k)
                used_e.add(k)
                fill_eq = abs(float(a.get("fill_price") or 0) - float(e.get("fill_price") or 0)) < 1e-9
                exit_eq = abs(float(a.get("exit_price") or 0) - float(e.get("exit_price") or 0)) < 1e-9
                pnl_eq = abs(float(a.get("pnl_yen_100") or 0) - float(e.get("pnl_yen_100") or 0)) < 1e-6
                reason_eq = str(a.get("exit_reason") or "") == str(e.get("exit_reason") or "")
                if fill_eq and exit_eq and pnl_eq and reason_eq:
                    klass = "MATCHED"
                elif fill_eq and (not exit_eq or not reason_eq or not pnl_eq):
                    klass = "EXIT_MISMATCH"
                else:
                    klass = "TIME_PRICE_MISMATCH"
                rows.append({"class": klass, "a": a, "exact": e})
            elif a and not e:
                rows.append({"class": "A_FIXED_ONLY", "a": a, "exact": None})
            else:
                rows.append({"class": "EXACT_ONLY", "a": None, "exact": e})
    for k, elist in e_map.items():
        if k in used_e:
            continue
        for e in elist:
            rows.append({"class": "EXACT_ONLY", "a": None, "exact": e})

    counts = {
        "matched": sum(1 for r in rows if r["class"] == "MATCHED"),
        "a_fixed_only": sum(1 for r in rows if r["class"] == "A_FIXED_ONLY"),
        "exact_only": sum(1 for r in rows if r["class"] == "EXACT_ONLY"),
        "mismatched": sum(1 for r in rows if r["class"] in ("TIME_PRICE_MISMATCH", "EXIT_MISMATCH")),
        "time_price_mismatch": sum(1 for r in rows if r["class"] == "TIME_PRICE_MISMATCH"),
        "exit_mismatch": sum(1 for r in rows if r["class"] == "EXIT_MISMATCH"),
    }
    first = next((r for r in rows if r["class"] != "MATCHED"), None)
    stage = "NONE"
    dclass = "NONE"
    if first:
        if first["class"] == "EXIT_MISMATCH":
            stage = "EXIT"
            dclass = "EXIT_DIFFERENCE"
        elif first["class"] == "TIME_PRICE_MISMATCH":
            stage = "FILL"
            dclass = "FILL_DIFFERENCE"
        elif first["class"] in ("A_FIXED_ONLY", "EXACT_ONLY"):
            a = first.get("a") or {}
            e = first.get("exact") or {}
            stage = f"TRADE_SET {first['class']} {(a or e).get('symbol')} {(a or e).get('anchor_time')}"
            dclass = "OTHER"
    a_admits = {(x.get("symbol"), x.get("anchor")): x for x in (A.get("a_admits") or [])}
    e_admits = {(x.get("symbol"), x.get("anchor")): x for x in (exact.get("admits") or [])}
    if counts["matched"] != len(a_norm) or counts["mismatched"] or counts["a_fixed_only"] or counts["exact_only"]:
        # refine first divergence using admits/scores if trade-set still matched but we want earlier stage
        pass
    # If trades fully match, check 09:40 score/bid vs A_FIXED admit
    snap = exact.get("snap_0940") or {}
    a0940 = a_admits.get(("285A", "09:40"))
    if a0940 and snap:
        score_a = float(a0940.get("score") or 0)
        score_e = float(snap.get("score") or 0)
        if abs(score_a - score_e) > 1e-6 and counts["matched"] == len(a_norm):
            stage = "FEATURE_STATE @ 09:40 285A"
            dclass = "FEATURE_STATE_DIFFERENCE"
    return {
        "counts": counts,
        "rows": rows,
        "FIRST_DIVERGENCE_STAGE": stage,
        "DIVERGENCE_CLASS": dclass,
        "a_trades": len(a_norm),
        "a_pnl": round(sum(float(t.get("pnl_yen_100") or 0) for t in a_norm), 2),
        "a_admitted": A.get("native_admitted"),
        "a_fills": A.get("native_fills"),
        "a_expired": A.get("native_expired"),
        "a_events_n": A.get("events_n"),
    }


def main() -> int:
    print("P0-3 Exact Runtime Replay 20260820 run1", flush=True)
    r1 = run_exact_once()
    if not r1.get("ok"):
        print("BLOCKED", r1.get("blocker"), flush=True)
        return 2
    print(
        f"run1 trades={len(r1['trades'])} pnl={r1['pnl']} holes={r1['sequence_holes']} "
        f"0940_seq={r1['snap_0940'].get('snapshot_seq')} elapsed={r1['elapsed_sec']}",
        flush=True,
    )
    print("P0-3 Exact Runtime Replay 20260820 run2", flush=True)
    r2 = run_exact_once()
    sha1 = _ledger_sha(r1["trades"])
    sha2 = _ledger_sha(r2["trades"])
    det = (
        sha1 == sha2
        and len(r1["trades"]) == len(r2["trades"])
        and r1["pnl"] == r2["pnl"]
        and _canonical_trades(r1["trades"]) == _canonical_trades(r2["trades"])
    )
    cache = json.loads(A_FIXED_CACHE.read_text(encoding="utf-8")) if A_FIXED_CACHE.is_file() else {}
    cmp_ = compare_to_a_fixed(r1, cache)
    print(
        f"run2 trades={len(r2['trades'])} pnl={r2['pnl']} det={det} "
        f"cmp matched={cmp_['counts']['matched']} mismatch={cmp_['counts']['mismatched']}",
        flush=True,
    )
    out = {
        "r1": r1,
        "sha1": sha1,
        "sha2": sha2,
        "determinism": det,
        "comparison": cmp_,
        "identity": {
            "strategy_sha": STRATEGY_SHA,
            "entry_sha": ENTRY_SHA,
            "exit_sha": EXIT_V2_CANDIDATE_SHA,
            "anchor_sha": ANCHOR_SHA,
            "runtime_commit": _git_commit(),
            "universe_source": r1.get("universe_source"),
            "universe_n": r1.get("universe_n"),
            "file_sha": {
                "v1r_native_entry_live.py": _file_sha("src/small_paper/v1r_native_entry_live.py"),
                "v1r_live_dual_lane.py": _file_sha("src/small_paper/v1r_live_dual_lane.py"),
                "v1r_exit_v2_contract.py": _file_sha("src/small_paper/v1r_exit_v2_contract.py"),
            },
        },
    }
    tmp = ROOT / "results" / "research" / "exact_runtime_replay_20260820_p0_3" / "_run_payload.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    print("wrote", tmp, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
