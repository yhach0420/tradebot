#!/usr/bin/env python3
"""E1_X5 daily canonical replay for 20260728 + 20260729 from local market capture.

Mandatory gate: 20260727 PM parity must match frozen metrics exactly or abort.
Formal Forward vs reference replay are separated (7/28 EXCLUDED_LAG_RESYNC, 7/29 RETROSPECTIVE).
"""
from __future__ import annotations

import json
import sys
import uuid
from collections import Counter
from copy import deepcopy
from datetime import datetime, time
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

OUT = REPO / "results" / "research" / "e1_x5_daily_replay_20260728_20260729"
VERDICT_OK = "E1_X5_20260728_20260729_REPLAY_COMPLETE"
VERDICT_BLOCKED = "E1_X5_20260728_20260729_SOURCE_BLOCKED"

FROZEN_PM = {
    "events": 547817,
    "evaluated": 17353,
    "no_evaluation": 308,
    "completed": 70,
    "pnl": 45023.825,
    "pf": 1.9226340172410525,
    "wins": 35,
    "losses": 35,
    "draws": 0,
    "ledger_sha256": "b5837b4871273aad64445e76c251a3bc72ff6aa98c41107c04dffaefe04ef2d4",
}

AM_START = time(8, 50)
AM_END = time(11, 30)
PM_START = time(12, 30)
PM_END = time(15, 30)

DAYS = ["20260728", "20260729"]
DAY_LABELS = {
    "20260728": {
        "formal": "EXCLUDED_LAG_RESYNC",
        "replay_class": "REFERENCE_REPLAY_NOT_FORWARD",
        "note": "既存記録どおり正式Forward成績から除外。参考再生値のみ。",
    },
    "20260729": {
        "formal": "RETROSPECTIVE_REFERENCE",
        "replay_class": "RETROSPECTIVE_REFERENCE",
        "note": "後日再生。Forward実績へ遡及加算しない。",
    },
}


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def _norm_sym(s: str) -> str:
    s = str(s or "").strip()
    if not s:
        return ""
    return s if s.endswith(".T") else f"{s}.T"


def _band_of(ts: datetime) -> str:
    t = ts.timetz().replace(tzinfo=None) if hasattr(ts, "timetz") else ts.time()
    # drop tz for compare
    tt = ts.astimezone(JST).time().replace(tzinfo=None)
    if AM_START <= tt < AM_END:
        return "AM"
    if PM_START <= tt <= PM_END:
        return "PM"
    return "OTHER"


def day_capture_dir(day: str) -> Path:
    return REPO / "data" / "market_capture" / day


def load_universe(day: str) -> set[str]:
    trace = day_capture_dir(day) / "ingress_register_api_trace.json"
    if trace.is_file():
        d = json.loads(trace.read_text(encoding="utf-8"))
        syms = d.get("actual_symbols") or []
        return {_norm_sym(x) for x in syms}
    return set()


def inventory_day(day: str) -> dict[str, Any]:
    root = day_capture_dir(day)
    sessions = []
    for sess in sorted(root.glob("session_*")):
        if not sess.is_dir():
            continue
        parts = sorted(sess.glob("push_part_*.jsonl"))
        seal = {}
        completeness = {}
        status = {}
        if (sess / "seal.json").is_file():
            seal = json.loads((sess / "seal.json").read_text(encoding="utf-8"))
        if (sess / "capture_completeness.json").is_file():
            completeness = json.loads((sess / "capture_completeness.json").read_text(encoding="utf-8"))
        if (sess / "status.json").is_file():
            status = json.loads((sess / "status.json").read_text(encoding="utf-8"))
        sessions.append(
            {
                "session_id": sess.name,
                "parts": [p.name for p in parts],
                "part_count": len(parts),
                "part_bytes": sum(p.stat().st_size for p in parts),
                "seal": {
                    "sealed_at": seal.get("sealed_at"),
                    "raw_rows": seal.get("raw_rows"),
                    "first_event_at": seal.get("first_event_at"),
                    "last_event_at": seal.get("last_event_at"),
                    "seal_pass": seal.get("seal_pass"),
                    "completeness_status": (seal.get("completeness") or {}).get("status"),
                    "research_adoptable": (seal.get("completeness") or {}).get("research_adoptable"),
                    "entry_block_reason": (seal.get("state") or {}).get("entry_block_reason")
                    or status.get("entry_block_reason"),
                    "consumer_lag": status.get("paper_consumer_lag"),
                    "recovery_count": status.get("recovery_count")
                    or (seal.get("state") or {}).get("recovery_count"),
                },
                "completeness": completeness,
                "status_snapshot": {
                    "entry_blocked": status.get("entry_blocked"),
                    "entry_block_reason": status.get("entry_block_reason"),
                    "paper_consumer_lag": status.get("paper_consumer_lag"),
                    "raw_last_sequence": status.get("raw_last_sequence"),
                    "paper_consumer_last_ack": status.get("paper_consumer_last_ack"),
                },
            }
        )
    return {
        "day": day,
        "day_dir": str(root),
        "exists": root.is_dir(),
        "sessions": sessions,
        "formal_label": DAY_LABELS[day]["formal"],
        "replay_class": DAY_LABELS[day]["replay_class"],
        "note": DAY_LABELS[day]["note"],
    }


def iter_band_events(
    day: str,
    band: str,
    universe: set[str],
) -> Iterator[dict[str, Any]]:
    root = day_capture_dir(day)
    for sess in sorted(root.glob("session_*")):
        if not sess.is_dir():
            continue
        for part in sorted(sess.glob("push_part_*.jsonl")):
            with part.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    ts = (
                        _parse_ts(rec.get("received_at") or rec.get("event_time") or rec.get("persisted_at"))
                    )
                    if ts is None:
                        continue
                    if _band_of(ts) != band:
                        continue
                    sym = _norm_sym(rec.get("symbol") or "")
                    if universe and sym not in universe:
                        continue
                    op = rec.get("original_payload")
                    if not isinstance(op, dict):
                        continue
                    if not isinstance(op.get("Buy1"), dict) or not isinstance(op.get("Sell1"), dict):
                        continue
                    payload = dict(op)
                    if payload.get("sequence") is None and rec.get("sequence") is not None:
                        payload["sequence"] = rec["sequence"]
                    if not payload.get("CurrentPriceTime"):
                        payload["CurrentPriceTime"] = ts.isoformat()
                    yield {
                        "symbol": sym,
                        "recv_ts": ts,
                        "payload": payload,
                        "sequence": rec.get("sequence"),
                        "event_id": str(
                            rec.get("raw_record_id")
                            or f"{ts.isoformat()}|{sym}|{rec.get('sequence')}"
                        ),
                        "source_part": part.name,
                        "session_id": sess.name,
                    }


def scan_quality(day: str, universe: set[str]) -> dict[str, Any]:
    """Stream scan: counts, gaps, sequence inversions, duplicates per band."""
    root = day_capture_dir(day)
    from small_paper.replay_session_normalizer import normalize_day_capture

    events, report = normalize_day_capture(root, day=day, gap_threshold_sec=120.0)
    # band coverage
    band_counts = Counter()
    for e in events:
        ts = e.ts if hasattr(e, "ts") else _parse_ts(e.event_time)
        if ts is None:
            continue
        band_counts[_band_of(ts)] += 1
    # AM/PM gap largest
    by_band_gaps: dict[str, list] = {"AM": [], "PM": [], "OTHER": []}
    for g in report.gaps:
        # approximate by from time
        ft = _parse_ts(g.get("from") or g.get("from_time") or g.get("to"))
        b = _band_of(ft) if ft else "OTHER"
        by_band_gaps.setdefault(b, []).append(g)

    am_ok = band_counts.get("AM", 0) > 1000
    pm_ok = band_counts.get("PM", 0) > 1000
    seal_status = None
    for sess in root.glob("session_*"):
        if (sess / "seal.json").is_file():
            seal = json.loads((sess / "seal.json").read_text(encoding="utf-8"))
            seal_status = (seal.get("completeness") or {}).get("status")
            break

    # free memory of full event list after counts — caller may re-stream
    n = len(events)
    del events
    return {
        "normalize": report.to_dict(),
        "normalized_rows": report.normalized_rows,
        "raw_rows": report.raw_rows,
        "duplicate_keys": report.duplicate_keys,
        "malformed": report.malformed,
        "timestamp_regressions_in_file_order": report.timestamp_regressions_in_file_order,
        "gaps": report.gaps,
        "gap_count": len(report.gaps),
        "largest_gap_sec": max((float(g.get("gap_sec") or 0) for g in report.gaps), default=0.0),
        "band_event_counts": dict(band_counts),
        "band_gaps": {k: len(v) for k, v in by_band_gaps.items()},
        "am_adoptable_for_replay": am_ok,
        "pm_adoptable_for_replay": pm_ok,
        "seal_completeness_status": seal_status,
        "scanned_event_objects": n,
        "universe_n": len(universe),
    }


def _norm_exits(exits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for x in exits:
        row = dict(x)
        if "holding_sec" not in row:
            et, xt = row.get("entry_time"), row.get("exit_time")
            if hasattr(et, "timestamp") and hasattr(xt, "timestamp"):
                row["holding_sec"] = (xt - et).total_seconds()
            else:
                row["holding_sec"] = 0.0
        out.append(row)
    return out


def replay_events(events: list[dict[str, Any]], *, day: str) -> dict[str, Any]:
    from small_paper.e1_x5_artifact_sot import canonical_ledger_hash
    from small_paper.e1_x5_decision_core import process_e1_x5_event
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession

    sess = E1X5ForwardShadowSession(enabled=True)
    provider = DMidD4H6ScoreProvider.maybe_create()
    for i, ev in enumerate(events, 1):
        process_e1_x5_event(
            provider=provider,
            session=sess,
            symbol=ev["symbol"],
            payload=ev["payload"],
            day=day,
            event_sequence=ev.get("sequence"),
            event_id=ev["event_id"],
            decision_time=ev["recv_ts"],
        )
        if i % 200000 == 0:
            print(f"    ... {day} n={i} exits={len(sess.exits)} eval={sess.evaluated_count}", flush=True)
    # orphans = open at end
    orphans = [
        {
            "symbol": p.symbol,
            "entry_time": p.entry_time.isoformat() if hasattr(p.entry_time, "isoformat") else str(p.entry_time),
            "reason": "SESSION_END_OPEN",
        }
        for p in sess.positions.values()
    ]
    exits = _norm_exits(list(sess.exits))
    s = sess.summary()
    sha = canonical_ledger_hash(exits, version="v1")
    noe = s.get("no_evaluation_breakdown") if isinstance(s.get("no_evaluation_breakdown"), dict) else {}
    return {
        "events_fed": len(events),
        "summary": s,
        "exits": exits,
        "entries_n": len(sess.entries),
        "orphans": orphans,
        "orphan_n": len(orphans),
        "ledger_sha256": sha,
        "evaluated": int(s.get("evaluated_count") or 0),
        "no_evaluation": int(s.get("no_evaluation_count") or 0),
        "no_evaluation_breakdown": noe,
        "completed": int(s.get("trades") or 0),
        "open": int(s.get("open_positions") or 0),
        "net_pnl": float(s.get("total_pnl_yen_100") or 0.0),
        "pf": s.get("profit_factor_yen_100"),
        "wins": int(s.get("wins") or 0),
        "losses": int(s.get("losses") or 0),
        "draws": int(s.get("draws") or 0),
        "win_rate": (int(s.get("wins") or 0) / int(s.get("trades") or 0)) if int(s.get("trades") or 0) else None,
        "exit_reasons": dict(s.get("exit_reasons") or {}),
        "cap_blocked": int(s.get("cap_blocked") or 0),
        "same_symbol_blocked": int(s.get("same_symbol_blocked") or 0),
        "submit_cancel_live": [0, 0, 0],
    }


def exit_pnl_by_reason(exits: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for x in exits:
        r = str(x.get("exit_reason") or "")
        bucket = out.setdefault(r, {"n": 0, "pnl": 0.0})
        bucket["n"] += 1
        bucket["pnl"] += float(x.get("net_pnl_yen_100") or 0)
    return out


def combine_bands(am: dict[str, Any], pm: dict[str, Any]) -> dict[str, Any]:
    from small_paper.e1_x5_artifact_sot import canonical_ledger_hash

    exits = list(am.get("exits") or []) + list(pm.get("exits") or [])
    pnls = [float(x.get("net_pnl_yen_100") or 0) for x in exits]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    draws = sum(1 for p in pnls if p == 0)
    gp = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p < 0)
    pf = (gp / gl) if gl > 0 else None
    reasons: Counter[str] = Counter()
    for x in exits:
        reasons[str(x.get("exit_reason") or "")] += 1
    return {
        "events_fed": int(am.get("events_fed") or 0) + int(pm.get("events_fed") or 0),
        "evaluated": int(am.get("evaluated") or 0) + int(pm.get("evaluated") or 0),
        "no_evaluation": int(am.get("no_evaluation") or 0) + int(pm.get("no_evaluation") or 0),
        "ENTRY": int(am.get("entries_n") or 0) + int(pm.get("entries_n") or 0),
        "completed": len(exits),
        "open": int(am.get("open") or 0) + int(pm.get("open") or 0),
        "orphan_n": int(am.get("orphan_n") or 0) + int(pm.get("orphan_n") or 0),
        "net_pnl": float(sum(pnls)),
        "pf": pf,  # recomputed from combined ledger — NOT average of AM/PM PF
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "win_rate": (wins / len(exits)) if exits else None,
        "exit_reasons": dict(reasons),
        "exit_pnl_by_reason": exit_pnl_by_reason(exits),
        "cap_blocked": int(am.get("cap_blocked") or 0) + int(pm.get("cap_blocked") or 0),
        "same_symbol_blocked": int(am.get("same_symbol_blocked") or 0)
        + int(pm.get("same_symbol_blocked") or 0),
        "ledger_sha256": canonical_ledger_hash(exits, version="v1") if exits else canonical_ledger_hash([], version="v1"),
        "exits": exits,
        "submit_cancel_live": [0, 0, 0],
    }


def run_parity_20260727_pm() -> dict[str, Any]:
    from run_e1_x5_pm_replay_root_cause_20260727 import iter_pm_events, load_universe
    from small_paper.e1_x5_artifact_sot import canonical_ledger_hash
    from small_paper.e1_x5_decision_core import process_e1_x5_event
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider
    from small_paper.e1_x5_forward_shadow import E1X5ForwardShadowSession

    print("[parity] loading 20260727 PM events ...", flush=True)
    events = list(iter_pm_events(load_universe()))
    print(f"[parity] events={len(events)}", flush=True)
    sess = E1X5ForwardShadowSession(enabled=True)
    provider = DMidD4H6ScoreProvider.maybe_create()
    for i, ev in enumerate(events, 1):
        process_e1_x5_event(
            provider=provider,
            session=sess,
            symbol=ev["symbol"],
            payload=ev["payload"],
            day="20260727",
            event_sequence=ev.get("sequence"),
            event_id=ev["event_id"],
            decision_time=ev["recv_ts"],
        )
        if i % 100000 == 0:
            print(f"[parity] n={i} exits={len(sess.exits)}", flush=True)
    exits = _norm_exits(list(sess.exits))
    s = sess.summary()
    sha = canonical_ledger_hash(exits, version="v1")
    actual = {
        "events": len(events),
        "evaluated": int(s.get("evaluated_count") or 0),
        "no_evaluation": int(s.get("no_evaluation_count") or 0),
        "completed": len(exits),
        "pnl": float(s.get("total_pnl_yen_100") or 0.0),
        "pf": float(s.get("profit_factor_yen_100") or 0.0) if s.get("profit_factor_yen_100") is not None else None,
        "wins": int(s.get("wins") or 0),
        "losses": int(s.get("losses") or 0),
        "draws": int(s.get("draws") or 0),
        "ledger_sha256": sha,
    }
    mismatches = []
    if actual["events"] != FROZEN_PM["events"]:
        mismatches.append(f"events {actual['events']}!={FROZEN_PM['events']}")
    if actual["evaluated"] != FROZEN_PM["evaluated"]:
        mismatches.append(f"evaluated {actual['evaluated']}!={FROZEN_PM['evaluated']}")
    if actual["no_evaluation"] != FROZEN_PM["no_evaluation"]:
        mismatches.append(f"no_evaluation {actual['no_evaluation']}!={FROZEN_PM['no_evaluation']}")
    if actual["completed"] != FROZEN_PM["completed"]:
        mismatches.append(f"completed {actual['completed']}!={FROZEN_PM['completed']}")
    if abs(actual["pnl"] - FROZEN_PM["pnl"]) > 1e-6:
        mismatches.append(f"pnl {actual['pnl']}!={FROZEN_PM['pnl']}")
    if actual["pf"] is None or abs(float(actual["pf"]) - FROZEN_PM["pf"]) > 1e-12:
        mismatches.append(f"pf {actual['pf']}!={FROZEN_PM['pf']}")
    if (actual["wins"], actual["losses"], actual["draws"]) != (
        FROZEN_PM["wins"],
        FROZEN_PM["losses"],
        FROZEN_PM["draws"],
    ):
        mismatches.append("WLD mismatch")
    if actual["ledger_sha256"] != FROZEN_PM["ledger_sha256"]:
        mismatches.append(f"sha {actual['ledger_sha256']}!={FROZEN_PM['ledger_sha256']}")
    return {"ok": not mismatches, "actual": actual, "expected": FROZEN_PM, "mismatches": mismatches}


def load_runtime_reference(day: str, band: str) -> Optional[dict[str, Any]]:
    root = REPO / "results" / "small_paper" / day
    if not root.is_dir():
        return None
    # Prefer dedicated am/pm summary
    suffix = f"small_paper_summary_{band.lower()}.json"
    cands = sorted(root.rglob(suffix), key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands and band == "PM":
        cands = sorted(root.rglob("small_paper_summary_pm.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        return None
    p = cands[0]
    d = json.loads(p.read_text(encoding="utf-8"))
    e1 = d.get("e1_x5_forward_shadow") if isinstance(d.get("e1_x5_forward_shadow"), dict) else {}
    return {
        "path": str(p),
        "trades": int(d.get("e1_x5_forward_shadow_trades") or e1.get("trades") or 0),
        "pnl": d.get("e1_x5_forward_shadow_total_pnl_yen_100", e1.get("total_pnl_yen_100")),
        "evaluated": int(d.get("e1_x5_forward_shadow_evaluated_count") or e1.get("evaluated_count") or 0),
        "pf": d.get("e1_x5_forward_shadow_profit_factor_yen_100", e1.get("profit_factor_yen_100")),
        "wins": e1.get("wins"),
        "losses": e1.get("losses"),
        "draws": e1.get("draws"),
        "exit_reasons": e1.get("exit_reasons"),
        "cap_blocked": e1.get("cap_blocked"),
        "same_symbol_blocked": e1.get("same_symbol_blocked"),
    }


def compare_runtime(offline: dict[str, Any], runtime: Optional[dict[str, Any]]) -> dict[str, Any]:
    if runtime is None:
        return {"available": False, "mismatch_count": None, "mismatches": ["runtime_ledger_absent"]}
    mismatches = []
    if int(offline.get("completed") or 0) != int(runtime.get("trades") or 0):
        mismatches.append(f"trades {offline.get('completed')}!={runtime.get('trades')}")
    if abs(float(offline.get("net_pnl") or 0) - float(runtime.get("pnl") or 0)) > 1e-3:
        mismatches.append(f"pnl {offline.get('net_pnl')}!={runtime.get('pnl')}")
    if int(offline.get("evaluated") or 0) != int(runtime.get("evaluated") or 0):
        mismatches.append(f"evaluated {offline.get('evaluated')}!={runtime.get('evaluated')}")
    return {
        "available": True,
        "runtime_path": runtime.get("path"),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "runtime": runtime,
    }


def slim_result(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in r.items() if k not in ("exits", "summary", "orphans")}
    out["exit_pnl_by_reason"] = exit_pnl_by_reason(r.get("exits") or [])
    out["no_evaluation_breakdown"] = r.get("no_evaluation_breakdown")
    return out


def write_xlsx(path: Path, sheets: dict[str, Any]) -> None:
    from openpyxl import Workbook

    def cell(v: Any) -> Any:
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        return json.dumps(v, ensure_ascii=False, default=str)

    wb = Workbook()
    wb.remove(wb.active)
    for name, data in sheets.items():
        ws = wb.create_sheet(title=str(name)[:31])
        if isinstance(data, list):
            if not data:
                ws.append(["empty"])
                continue
            if isinstance(data[0], dict):
                keys: list[str] = []
                for row in data:
                    for k in row:
                        if k not in keys:
                            keys.append(str(k))
                ws.append(keys)
                for row in data:
                    ws.append([cell(row.get(k)) for k in keys])
            else:
                for v in data:
                    ws.append([cell(v)])
        elif isinstance(data, dict):
            ws.append(["key", "value"])
            for k, v in data.items():
                ws.append([str(k), cell(v)])
        else:
            ws.append(["value", cell(data)])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = f"e1x5_daily_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    print(f"[run] {run_id}", flush=True)

    parity = run_parity_20260727_pm()
    print(f"[parity] ok={parity['ok']} mismatches={parity['mismatches']}", flush=True)
    if not parity["ok"]:
        report = {
            "verdict": VERDICT_BLOCKED,
            "run_id": run_id,
            "reason": "mandatory_20260727_pm_parity_failed",
            "parity_20260727_pm": parity,
            "failed_tests": ["parity_20260727_pm"],
            "safety": {"submit": 0, "cancel": 0, "live_order": 0},
        }
        (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (OUT / "report.md").write_text(
            f"# {VERDICT_BLOCKED}\n\nparity mismatches: {parity['mismatches']}\n", encoding="utf-8"
        )
        write_xlsx(OUT / "audit.xlsx", {"Summary": report, "Parity": parity, "Tests": [], "Safety": report["safety"]})
        print(json.dumps({"verdict": VERDICT_BLOCKED, "parity": parity}, ensure_ascii=False, indent=2))
        return 2

    inventories = {d: inventory_day(d) for d in DAYS}
    for d, inv in inventories.items():
        if not inv["exists"] or not inv["sessions"]:
            report = {
                "verdict": VERDICT_BLOCKED,
                "run_id": run_id,
                "reason": f"source_missing:{d}",
                "parity_20260727_pm": parity,
                "source": inventories,
                "safety": {"submit": 0, "cancel": 0, "live_order": 0},
            }
            (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (OUT / "report.md").write_text(f"# {VERDICT_BLOCKED}\n\nsource missing: {d}\n", encoding="utf-8")
            write_xlsx(OUT / "audit.xlsx", {"Summary": report, "Source": inventories, "Safety": report["safety"]})
            return 2

    day_results: dict[str, Any] = {}
    all_trades_rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = [
        {"name": "parity_20260727_pm", "ok": True, "detail": parity["actual"]}
    ]

    for day in DAYS:
        print(f"[day] {day} inventory/quality ...", flush=True)
        universe = load_universe(day)
        quality = scan_quality(day, universe)
        print(
            f"  rows={quality['normalized_rows']} gaps={quality['gap_count']} "
            f"AM={quality['band_event_counts'].get('AM')} PM={quality['band_event_counts'].get('PM')}",
            flush=True,
        )

        bands: dict[str, Any] = {}
        for band in ("AM", "PM"):
            print(f"[replay] {day} {band} pass1 ...", flush=True)
            evs = list(iter_band_events(day, band, universe))
            print(f"  events={len(evs)}", flush=True)
            r1 = replay_events(evs, day=day)
            print(f"[replay] {day} {band} pass2 (determinism) ...", flush=True)
            r2 = replay_events(evs, day=day)
            det_ok = r1["ledger_sha256"] == r2["ledger_sha256"]
            tests.append(
                {
                    "name": f"double_replay_{day}_{band}",
                    "ok": det_ok,
                    "detail": {"h1": r1["ledger_sha256"], "h2": r2["ledger_sha256"]},
                }
            )
            rt = load_runtime_reference(day, band)
            cmp_rt = compare_runtime(r1, rt)
            tests.append(
                {
                    "name": f"runtime_offline_{day}_{band}",
                    "ok": (not cmp_rt["available"]) or cmp_rt["mismatch_count"] == 0,
                    "detail": cmp_rt,
                }
            )
            bands[band] = {
                **slim_result(r1),
                "determinism_ok": det_ok,
                "runtime_compare": cmp_rt,
                "adoptable_for_replay": bool(
                    quality["am_adoptable_for_replay"] if band == "AM" else quality["pm_adoptable_for_replay"]
                ),
            }
            window_rows.append(
                {
                    "day": day,
                    "band": band,
                    "events": r1["events_fed"],
                    "evaluated": r1["evaluated"],
                    "no_evaluation": r1["no_evaluation"],
                    "completed": r1["completed"],
                    "pnl": r1["net_pnl"],
                    "pf": r1["pf"],
                    "WLD": f"{r1['wins']}/{r1['losses']}/{r1['draws']}",
                    "ledger_sha256": r1["ledger_sha256"],
                    "formal_label": DAY_LABELS[day]["formal"],
                    "runtime_mismatch_count": cmp_rt.get("mismatch_count"),
                }
            )
            for x in r1.get("exits") or []:
                row = dict(x)
                row["day"] = day
                row["band"] = band
                # serialize datetimes
                for k in ("entry_time", "exit_time"):
                    if hasattr(row.get(k), "isoformat"):
                        row[k] = row[k].isoformat()
                all_trades_rows.append(row)
            del evs, r2

        day_total = combine_bands(
            {**bands["AM"], "exits": [t for t in all_trades_rows if t["day"] == day and t["band"] == "AM"]},
            {**bands["PM"], "exits": [t for t in all_trades_rows if t["day"] == day and t["band"] == "PM"]},
        )
        # restore band exits into combine already done via all_trades_rows filter
        day_results[day] = {
            "inventory": inventories[day],
            "quality": quality,
            "formal_label": DAY_LABELS[day]["formal"],
            "replay_class": DAY_LABELS[day]["replay_class"],
            "note": DAY_LABELS[day]["note"],
            "AM": bands["AM"],
            "PM": bands["PM"],
            "DAY": {k: v for k, v in day_total.items() if k != "exits"},
            "forward_official": {
                "included": False,
                "reason": DAY_LABELS[day]["formal"],
                "pnl": None,
                "completed": None,
            },
            "reference_replay": {
                "AM": {k: bands["AM"][k] for k in bands["AM"] if k != "exits"},
                "PM": {k: bands["PM"][k] for k in bands["PM"] if k != "exits"},
                "DAY": {k: v for k, v in day_total.items() if k != "exits"},
            },
        }

    # 2-day reference total (NOT Forward official)
    two_day = combine_bands(
        {
            "exits": [t for t in all_trades_rows if t["day"] == "20260728"],
            "events_fed": day_results["20260728"]["DAY"]["events_fed"],
            "evaluated": day_results["20260728"]["DAY"]["evaluated"],
            "no_evaluation": day_results["20260728"]["DAY"]["no_evaluation"],
            "entries_n": day_results["20260728"]["DAY"]["ENTRY"],
            "open": day_results["20260728"]["DAY"]["open"],
            "orphan_n": day_results["20260728"]["DAY"]["orphan_n"],
            "cap_blocked": day_results["20260728"]["DAY"]["cap_blocked"],
            "same_symbol_blocked": day_results["20260728"]["DAY"]["same_symbol_blocked"],
        },
        {
            "exits": [t for t in all_trades_rows if t["day"] == "20260729"],
            "events_fed": day_results["20260729"]["DAY"]["events_fed"],
            "evaluated": day_results["20260729"]["DAY"]["evaluated"],
            "no_evaluation": day_results["20260729"]["DAY"]["no_evaluation"],
            "entries_n": day_results["20260729"]["DAY"]["ENTRY"],
            "open": day_results["20260729"]["DAY"]["open"],
            "orphan_n": day_results["20260729"]["DAY"]["orphan_n"],
            "cap_blocked": day_results["20260729"]["DAY"]["cap_blocked"],
            "same_symbol_blocked": day_results["20260729"]["DAY"]["same_symbol_blocked"],
        },
    )
    two_day_slim = {k: v for k, v in two_day.items() if k != "exits"}

    failed = [t["name"] for t in tests if not t.get("ok")]
    # Double-replay failures block; runtime mismatches are reported but do not block COMPLETE
    hard_failed = [n for n in failed if n.startswith("double_replay") or n == "parity_20260727_pm"]
    verdict = VERDICT_OK if not hard_failed else VERDICT_BLOCKED

    table_rows = []
    for day in DAYS:
        for band in ("AM", "PM", "DAY"):
            src = day_results[day][band]
            table_rows.append(
                {
                    "scope": f"{day} {band}",
                    "class": DAY_LABELS[day]["formal"],
                    "events": src.get("events_fed"),
                    "evaluated": src.get("evaluated"),
                    "no_evaluation": src.get("no_evaluation"),
                    "ENTRY": src.get("entries_n") or src.get("ENTRY"),
                    "completed": src.get("completed"),
                    "open": src.get("open"),
                    "orphan": src.get("orphan_n"),
                    "pnl": src.get("net_pnl"),
                    "pf": src.get("pf"),
                    "WLD": f"{src.get('wins')}/{src.get('losses')}/{src.get('draws')}",
                    "win_rate": src.get("win_rate"),
                    "cap": src.get("cap_blocked"),
                    "same": src.get("same_symbol_blocked"),
                    "ledger_sha256": src.get("ledger_sha256"),
                    "runtime_mismatch": (src.get("runtime_compare") or {}).get("mismatch_count")
                    if band != "DAY"
                    else None,
                }
            )
    table_rows.append(
        {
            "scope": "2DAY_REFERENCE_TOTAL",
            "class": "NOT_FORWARD_OFFICIAL",
            "events": two_day_slim.get("events_fed"),
            "evaluated": two_day_slim.get("evaluated"),
            "no_evaluation": two_day_slim.get("no_evaluation"),
            "ENTRY": two_day_slim.get("ENTRY"),
            "completed": two_day_slim.get("completed"),
            "open": two_day_slim.get("open"),
            "orphan": two_day_slim.get("orphan_n"),
            "pnl": two_day_slim.get("net_pnl"),
            "pf": two_day_slim.get("pf"),
            "WLD": f"{two_day_slim.get('wins')}/{two_day_slim.get('losses')}/{two_day_slim.get('draws')}",
            "win_rate": two_day_slim.get("win_rate"),
            "cap": two_day_slim.get("cap_blocked"),
            "same": two_day_slim.get("same_symbol_blocked"),
            "ledger_sha256": two_day_slim.get("ledger_sha256"),
            "runtime_mismatch": None,
        }
    )

    report = {
        "verdict": verdict,
        "run_id": run_id,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "parity_20260727_pm": parity,
        "path": "process_e1_x5_event + DMidD4H6ScoreProvider (same as feed_e1_x5_from_runtime_state)",
        "g1_adopted": False,
        "forward_official_total": {
            "note": "7/28 EXCLUDED_LAG_RESYNC / 7/29 RETROSPECTIVE — Forward合計へ加算しない",
            "completed": 0,
            "pnl": 0.0,
            "included_days": [],
        },
        "reference_replay": day_results,
        "two_day_reference_total": two_day_slim,
        "table": table_rows,
        "tests": tests,
        "failed_tests": failed,
        "hard_failed_tests": hard_failed,
        "safety": {"submit": 0, "cancel": 0, "live_order": 0},
    }

    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    def fmt_row(r: dict[str, Any]) -> str:
        return (
            f"| {r['scope']} | {r['class']} | {r.get('completed')} | {r.get('pnl')} | {r.get('pf')} | "
            f"{r.get('WLD')} | {r.get('evaluated')}/{r.get('no_evaluation')} | `{str(r.get('ledger_sha256') or '')[:12]}…` |"
        )

    md = [
        f"# {verdict}",
        "",
        f"run_id: `{run_id}`",
        "",
        "## 必須Parity 20260727 PM",
        f"- ok: {parity['ok']}",
        f"- actual: `{json.dumps(parity['actual'], ensure_ascii=False)}`",
        "",
        "## AM / PM / 日計 / 2日合計（参考再生）",
        "",
        "| scope | class | completed | pnl | pf | W/L/D | eval/no_eval | ledger |",
        "|---|---|---:|---:|---:|---|---|---|",
        *[fmt_row(r) for r in table_rows],
        "",
        "## 注意",
        "- 7/28 は EXCLUDED_LAG_RESYNC：正式Forward成績に含めない（参考再生のみ）。",
        "- 7/29 は RETROSPECTIVE_REFERENCE：Forward実績へ遡及加算しない。",
        "- PFは各取引ledgerから再計算（AM/PM PFの平均ではない）。",
        "- 旧SHADOW OBSERVATIONの対象件数/block/deltaは未使用。",
        "",
        f"failed_tests: {failed}",
        f"safety: submit/cancel/live = 0/0/0",
        "",
    ]
    (OUT / "report.md").write_text("\n".join(md), encoding="utf-8")

    counters_rows = []
    for day in DAYS:
        for band in ("AM", "PM"):
            b = day_results[day][band]
            counters_rows.append(
                {
                    "day": day,
                    "band": band,
                    "evaluated": b.get("evaluated"),
                    "no_evaluation": b.get("no_evaluation"),
                    "cap_blocked": b.get("cap_blocked"),
                    "same_symbol_blocked": b.get("same_symbol_blocked"),
                    "orphan_n": b.get("orphan_n"),
                    "no_evaluation_breakdown": b.get("no_evaluation_breakdown"),
                    "exit_pnl_by_reason": b.get("exit_pnl_by_reason"),
                }
            )

    write_xlsx(
        OUT / "audit.xlsx",
        {
            "Summary": {
                "verdict": verdict,
                "run_id": run_id,
                "forward_official": report["forward_official_total"],
                "two_day_reference": two_day_slim,
            },
            "Source": [inventories[d] for d in DAYS],
            "Windows": window_rows,
            "Trades": all_trades_rows,
            "Counters": counters_rows,
            "Parity": {"parity_20260727_pm": parity, "table": table_rows},
            "Tests": tests,
            "Safety": report["safety"],
        },
    )

    # Console table
    print("\n=== AM / PM / DAY / 2DAY REFERENCE TABLE ===", flush=True)
    hdr = f"{'scope':<28} {'class':<24} {'n':>5} {'pnl':>12} {'pf':>8} {'W/L/D':>10} {'eval/noe':>14}"
    print(hdr, flush=True)
    for r in table_rows:
        print(
            f"{r['scope']:<28} {str(r['class']):<24} {str(r.get('completed')):>5} "
            f"{float(r.get('pnl') or 0):>12.3f} {str(r.get('pf')):>8} {str(r.get('WLD')):>10} "
            f"{str(r.get('evaluated'))}/{str(r.get('no_evaluation')):>6}",
            flush=True,
        )
    print(f"\nverdict={verdict} hard_failed={hard_failed} failed={failed}", flush=True)
    return 0 if verdict == VERDICT_OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
