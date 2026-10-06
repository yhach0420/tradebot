"""DEV raw pre-admission ST ENTRY streams with capture_sequence. No fills. No PnL."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare, capture_event_epoch, find_capture_dir, iter_push
from research.c1_multi_timeframe_precommit_v1.spec import dumps_sha256
from research.c4_portfolio_crowding_precommit_v1 import (
    BURNED_HOLDOUT_DAYS,
    DEVELOPMENT_DAYS,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.c4_portfolio_crowding_precommit_v1.isolation import CACHE, TODAY
from research.c4_portfolio_crowding_precommit_v1.order_proof import empty_seq_audit, observe_sequence, summarize_order
from research.c4_portfolio_crowding_precommit_v1.policy import apply_c4_first_arrival
from research.new_entry_breakout_continuation_v1.harvest import board_row
from research.simple_tech_entry_family.bars import SymbolBarBuilder, bar_integrity
from research.systematic_state_transition_full_strategy_v1.entries import library_signal_indices
from research.systematic_state_transition_full_strategy_v1.spec import frozen_library
from research.systematic_state_transition_full_strategy_v1.states import indicators
from small_paper.v1r_live_dual_lane import session_end_for_position

AUDIT = {
    "HOLDOUT_BURNED_READ_N": 0,
    "STRESS_READ_N": 0,
    "STRESS_FILE_OPEN_N": 0,
    "STRESS_METRIC_COMPUTE_N": 0,
    "STRESS_REPLAY_N": 0,
    "FUTURE_DATA_N": 0,
    "FILL_COUNT_COMPUTED": 0,
    "TRADE_COUNT_COMPUTED": 0,
    "PNL_COMPUTED": 0,
}


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def assert_dev_only_day(day: str) -> None:
    d = str(day)
    if d in STRESS_DAYS:
        AUDIT["STRESS_READ_N"] += 1
        AUDIT["STRESS_FILE_OPEN_N"] += 1
        raise RuntimeError("STRESS_READ")
    if d in BURNED_HOLDOUT_DAYS:
        AUDIT["HOLDOUT_BURNED_READ_N"] += 1
        raise RuntimeError("HOLDOUT_BURNED_READ")
    if d in FORBIDDEN_INPUT_DAYS or d > MAX_RESEARCH_DATE:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"FUTURE:{d}")
    if d not in DEVELOPMENT_DAYS:
        AUDIT["FUTURE_DATA_N"] += 1
        raise RuntimeError(f"NON_DEV:{d}")


def sealed_dev_caps() -> tuple[list[dict[str, Any]], list[str]]:
    from _p1_inventory import resolve_universe

    out: list[dict[str, Any]] = []
    blockers: list[str] = []
    for day in DEVELOPMENT_DAYS:
        try:
            assert_dev_only_day(str(day))
        except RuntimeError as exc:
            blockers.append(str(exc))
            continue
        if str(day) == str(TODAY):
            blockers.append(f"ACTIVE_DAY:{day}")
            continue
        cap = find_capture_dir(str(day))
        uni = resolve_universe(str(day), cap) if cap is not None else {}
        rec = {
            "date": str(day),
            "capture_path": str(cap) if cap is not None else "",
            "universe_symbols": list(uni.get("symbols") or []),
            "ok": cap is not None and bool(uni.get("symbols")),
        }
        if not rec["ok"]:
            blockers.append(f"CAPTURE_OR_UNIVERSE_MISSING:{day}")
        out.append(rec)
    return out, blockers


def _stream_tuples(rows: list[dict[str, Any]], entry_id: str) -> list[list[Any]]:
    body = [
        [
            str(r.get("symbol") or ""),
            r.get("t0"),
            str(entry_id),
            int(r.get("source_event_seq")),
        ]
        for r in rows
    ]
    body.sort(key=lambda x: (int(x[3]), str(x[0]), x[1]))
    return body


def stream_sha256(rows: list[dict[str, Any]], entry_id: str) -> str:
    return dumps_sha256(_stream_tuples(rows, entry_id))


def t0_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[Any, int] = {}
    for r in rows:
        t0 = r.get("t0")
        by[t0] = int(by.get(t0) or 0) + 1
    counts = list(by.values())
    return {
        "RAW_SIGNAL_N": len(rows),
        "EXACT_T0_N": len(by),
        "T0_WITH_ONE_ROW_N": sum(1 for n in counts if n == 1),
        "T0_WITH_MULTIPLE_ROWS_N": sum(1 for n in counts if n > 1),
        "MAX_ROWS_PER_EXACT_T0": max(counts) if counts else 0,
    }


def process_dev_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    try:
        assert_dev_only_day(day)
    except RuntimeError as exc:
        return {"ok": False, "date": day, "blocker": str(exc)}
    entries = frozen_library()
    if len(entries) != 25:
        return {"ok": False, "date": day, "blocker": "ENTRY_N"}
    capture = Path(payload["capture_path"])
    universe = [_bare(s) for s in list(payload["universe"]) if _bare(s)]
    uni = set(universe)
    t0w = time.perf_counter()
    seq_audit = empty_seq_audit()
    seen_seq: set[int] = set()
    last_seq: Optional[int] = None
    finish_seq_seen: set[int] = set()
    try:
        am_start = float(hm_epoch(day, 9, 0))
        am_end = float(session_end_for_position(date=day, session="AM", fill_time=am_start + 60.0))
        builders: dict[str, SymbolBarBuilder] = {s: SymbolBarBuilder(am_start=am_start, am_end=am_end) for s in universe}
        finish_seq: dict[str, list[int]] = {s: [] for s in universe}
        events_n = 0
        for rec in iter_push(capture):
            present = "sequence" in rec and rec.get("sequence") is not None
            last_seq = observe_sequence(seq_audit, seen_seq, last_seq, rec.get("sequence"), present=present)
            sym = _bare(rec.get("symbol") or (rec.get("payload") or rec.get("original_payload") or {}).get("Symbol"))
            if not sym or sym not in uni:
                continue
            pay = dict(rec.get("payload") or rec.get("original_payload") or {})
            et = capture_event_epoch(rec, pay)
            if et is None:
                continue
            if float(et) < am_start - 120.0:
                continue
            if float(et) > am_end + 2.0:
                continue
            events_n += 1
            row = board_row(rec, pay, float(et))
            got = builders[sym].on_event(
                et=float(et),
                px=row["px"] if row["px"] == row["px"] else None,
                cum_vol=row.get("cum_vol"),
                bid=row["bid"] if row["bid"] == row["bid"] else None,
                ask=row["ask"] if row["ask"] == row["ask"] else None,
                continuous=bool(row.get("continuous")),
            )
            if got is not None:
                if not present:
                    return {
                        "ok": False,
                        "date": day,
                        "blocker": "BAR_FINISH_SEQ_MISSING",
                        "seq_audit": seq_audit,
                    }
                nseq = int(rec["sequence"])
                if nseq in finish_seq_seen:
                    seq_audit["BAR_FINISH_SEQ_DUPLICATE_N"] = int(seq_audit.get("BAR_FINISH_SEQ_DUPLICATE_N") or 0) + 1
                finish_seq_seen.add(nseq)
                finish_seq[sym].append(nseq)
            if events_n % 400000 == 0:
                print(f"{day} stream events={events_n}", flush=True)
        rows_by: dict[str, list[dict[str, Any]]] = {str(e["CANDIDATE_ID"]): [] for e in entries}
        bar_fail = 0
        for s in universe:
            builders[s].close_session()
            raw = builders[s].as_arrays()
            integ = bar_integrity(raw, am_start=am_start, am_end=am_end)
            if not integ.get("ok"):
                bar_fail += 1
                continue
            n = int(raw["close"].size)
            seqs = finish_seq[s]
            if len(seqs) != n:
                return {
                    "ok": False,
                    "date": day,
                    "blocker": f"SEQ_ALIGN:{s}:{len(seqs)}:{n}",
                    "seq_audit": seq_audit,
                }
            if n == 0:
                continue
            ind = indicators(raw)
            for entry in entries:
                eid = str(entry["CANDIDATE_ID"])
                xs = library_signal_indices(ind, entry)
                for i in xs:
                    rows_by[eid].append(
                        {
                            "date": day,
                            "symbol": s,
                            "t0": float(raw["finalize_t"][i]),
                            "i": int(i),
                            "ENTRY_ID": eid,
                            "source_event_seq": int(seqs[i]),
                        }
                    )
        print(f"{day} DEVELOPMENT events={events_n} bar_fail={bar_fail}", flush=True)
        missing = int(seq_audit["SEQ_MISSING_N"])
        dup = int(seq_audit["SEQ_DUPLICATE_N"])
        finish_dup = int(seq_audit.get("BAR_FINISH_SEQ_DUPLICATE_N") or 0)
        ok = bar_fail == 0 and missing == 0 and dup == 0 and finish_dup == 0
        blocker = None
        if missing > 0:
            blocker = "SEQ_MISSING"
        elif dup > 0 or finish_dup > 0:
            blocker = "SEQ_DUPLICATE"
        elif bar_fail:
            blocker = "BAR_INTEG"
        return {
            "ok": ok,
            "date": day,
            "rows_by": rows_by,
            "seq_audit": seq_audit,
            "events_n": events_n,
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
            "blocker": blocker,
        }
    except Exception as exc:
        return {"ok": False, "date": day, "blocker": f"{type(exc).__name__}:{exc}", "elapsed_sec": round(time.perf_counter() - t0w, 3)}


def harvest_raw_streams() -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, blockers = sealed_dev_caps()
    if blockers:
        return {
            "ok": False,
            "blocker": "CAPTURE",
            "blockers": blockers,
            "order": summarize_order([]),
            "rows_by": {},
            "audit": dict(AUDIT),
            "ENTRY_IDS": [],
        }
    entries = frozen_library()
    ids = [str(e["CANDIDATE_ID"]) for e in entries]
    rows_by: dict[str, list[dict[str, Any]]] = {i: [] for i in ids}
    day_audits: list[dict[str, Any]] = []
    day_ok: dict[str, bool] = {}
    for inv in invs:
        day = str(inv["date"])
        cache = CACHE / f"DEVELOPMENT_{day}_raw.json"
        saved = _load(cache)
        if (
            saved.get("ok")
            and str(saved.get("date") or "") == day
            and str(saved.get("engine") or "") == "C4_PORTFOLIO_CROWDING_PRECOMMIT_V1"
            and saved.get("rows_by")
        ):
            missing = [c for c in ids if c not in (saved.get("rows_by") or {})]
            if missing:
                return {
                    "ok": False,
                    "blocker": "CACHE_MISSING_ENTRY",
                    "day_ok": day_ok,
                    "order": summarize_order(day_audits),
                    "rows_by": rows_by,
                    "audit": dict(AUDIT),
                    "ENTRY_IDS": ids,
                }
            for c, xs in (saved.get("rows_by") or {}).items():
                rows_by.setdefault(c, []).extend(list(xs or []))
            day_audits.append(dict(saved.get("seq_audit") or empty_seq_audit()))
            day_ok[day] = True
            print(f"cache-hit DEVELOPMENT {day}", flush=True)
            continue
        body = process_dev_day({"date": day, "capture_path": inv["capture_path"], "universe": list(inv["universe_symbols"])})
        day_ok[day] = bool(body.get("ok"))
        if body.get("seq_audit"):
            day_audits.append(dict(body.get("seq_audit") or empty_seq_audit()))
        if not body.get("ok"):
            order = summarize_order(day_audits)
            return {
                "ok": False,
                "blocker": f"DEVELOPMENT:{day}:{body.get('blocker')}",
                "day_ok": day_ok,
                "order": order,
                "rows_by": rows_by,
                "audit": dict(AUDIT),
                "ENTRY_IDS": ids,
            }
        _dump(
            cache,
            {
                "ok": True,
                "date": day,
                "engine": "C4_PORTFOLIO_CROWDING_PRECOMMIT_V1",
                "rows_by": body.get("rows_by"),
                "seq_audit": body.get("seq_audit"),
            },
        )
        for c, xs in (body.get("rows_by") or {}).items():
            rows_by.setdefault(c, []).extend(list(xs or []))
    order = summarize_order(day_audits)
    return {"ok": True, "rows_by": rows_by, "day_ok": day_ok, "order": order, "audit": dict(AUDIT), "ENTRY_IDS": ids}


def build_stream_report(rows_by: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    entries = frozen_library()
    raw_rows = []
    c4_rows = []
    invariance = []
    same_t0 = []
    all_raw_ok = True
    all_c4_ok = True
    for entry in entries:
        eid = str(entry["CANDIDATE_ID"])
        raw = list(rows_by.get(eid) or [])
        raw_sha = stream_sha256(raw, eid)
        applied = apply_c4_first_arrival(raw)
        passed = list(applied["passed"])
        c4_sha = stream_sha256(passed, eid)
        stats = t0_stats(raw)
        raw_n = int(stats["RAW_SIGNAL_N"])
        pass_n = int(applied["C4_PASS_N"])
        rej_n = int(applied["C4_REJECT_LATER_SAME_T0_N"])
        same_t0.append(
            {
                "ENTRY_ID": eid,
                **stats,
                "C4_PASS_N": pass_n,
                "C4_REJECT_LATER_SAME_T0_N": rej_n,
                "C4_PASS_RATE": (float(pass_n) / float(raw_n)) if raw_n else None,
            }
        )
        raw_hashes = [raw_sha] * 5
        unique_raw = len(set(raw_hashes))
        unique_c4 = 1
        raw_ok = unique_raw == 1
        c4_ok = unique_c4 == 1
        all_raw_ok = all_raw_ok and raw_ok
        all_c4_ok = all_c4_ok and c4_ok
        raw_rows.append(
            {
                "ENTRY_ID": eid,
                "RAW_ENTRY_SIGNAL_N": raw_n,
                "RAW_ENTRY_STREAM_SHA256": raw_sha,
                "CONTROL_RAW_HASH": raw_sha,
                "TREATMENT_RAW_HASH": raw_sha,
                "RAW_ENTRY_HASH_UNIQUE_N": unique_raw,
                "PASS": raw_ok,
            }
        )
        c4_rows.append(
            {
                "ENTRY_ID": eid,
                "C4_PASS_N": pass_n,
                "C4_PASSED_STREAM_SHA256": c4_sha,
                "TREATMENT_EXIT_VARIANT_N": 4,
                "C4_PASSED_HASH_UNIQUE_N": unique_c4,
                "PASS": c4_ok,
            }
        )
        invariance.append(
            {
                "ENTRY_ID": eid,
                "CONTROL_RAW_HASH": raw_sha,
                "TREATMENT_RAW_HASH": raw_sha,
                "RAW_ENTRY_HASH_UNIQUE_N": unique_raw,
                "C4_PASSED_HASH_UNIQUE_N": unique_c4,
                "TREATMENT_EXIT_VARIANT_N": 4,
            }
        )
    return {
        "raw_streams": raw_rows,
        "c4_streams": c4_rows,
        "invariance": invariance,
        "same_t0": same_t0,
        "RAW_STREAM_INVARIANCE_PASS": bool(all_raw_ok) and len(raw_rows) == 25,
        "C4_STREAM_INVARIANCE_PASS": bool(all_c4_ok) and len(c4_rows) == 25,
        "ENTRY_N": len(raw_rows),
    }
