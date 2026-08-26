#!/usr/bin/env python
"""P1 inventory only: Capture + universe binding. No replay."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
JST = ZoneInfo("Asia/Tokyo")

from research.anchor_vs_event_driven.run_comparison import (  # noqa: E402
    _bare,
    _load_json,
    _parse_iso,
    find_capture_dir,
)
from small_paper.day_fixed_am_registration import (  # noqa: E402
    frozen_universe_path,
    load_frozen_am_universe,
)
from small_paper.v1r_live_dual_lane import canonical_symbol_key  # noqa: E402

CAP_ROOT = ROOT / "data" / "market_capture"
AM_CSV = ROOT / "results" / "reports"


def _syms_json(path: Path, keys: tuple[str, ...]) -> list[str]:
    body = _load_json(path)
    for k in keys:
        raw = body.get(k)
        if isinstance(raw, list) and raw:
            return list(dict.fromkeys(_bare(s) for s in raw if _bare(s)))
    return []


def _syms_csv(path: Path) -> list[str]:
    import csv

    if not path.is_file():
        return []
    out: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            s = _bare(row.get("symbol") or row.get("code") or "")
            if s:
                out.append(s)
    return list(dict.fromkeys(out))


def resolve_universe(day: str, capture: Optional[Path]) -> dict[str, Any]:
    frozen_path = frozen_universe_path(ROOT, day)
    frozen = load_frozen_am_universe(ROOT, day)
    frozen_syms = [canonical_symbol_key(s) for s in (frozen.get("canonical_symbols") or [])]
    frozen_syms = [s for s in frozen_syms if s]
    frozen_ok = bool(frozen.get("present") and frozen_syms and not frozen.get("reason"))

    reg_syms: list[str] = []
    reg_src = ""
    if capture is not None:
        for cand in (capture / "registration_manifest.json", capture.parent / "registration_manifest.json"):
            if cand.is_file():
                reg_syms = _syms_json(cand, ("registered_symbols", "actual_symbols", "symbols", "canonical_symbols"))
                if reg_syms:
                    reg_src = str(cand)
                    break
    am_path = AM_CSV / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    am_syms = _syms_csv(am_path)

    sources: list[dict[str, Any]] = []
    if frozen_ok:
        sources.append({"name": "frozen", "n": len(frozen_syms), "path": str(frozen_path)})
    if reg_syms:
        sources.append({"name": "registration", "n": len(reg_syms), "path": reg_src})
    if am_syms:
        sources.append({"name": "am_csv", "n": len(am_syms), "path": str(am_path)})

    if frozen_ok:
        conflict = False
        notes = []
        if reg_syms and set(reg_syms) != set(frozen_syms):
            notes.append("registration_differs_from_frozen")
        if am_syms and set(am_syms) != set(frozen_syms):
            notes.append("am_csv_differs_from_frozen")
        return {
            "resolved": True,
            "reason": "",
            "source": f"frozen:{frozen_path.name}",
            "symbols": frozen_syms,
            "universe_n": len(frozen_syms),
            "notes": notes,
            "conflict": conflict,
            "sources": sources,
        }

    # No frozen: unique same-day source only. Do not invent.
    candidates: list[tuple[str, list[str]]] = []
    if reg_syms:
        candidates.append((f"registration:{Path(reg_src).name}", reg_syms))
    if am_syms:
        candidates.append((f"am_csv:{am_path.name}", am_syms))
    if not candidates:
        return {
            "resolved": False,
            "reason": "UNIVERSE_BINDING_UNRESOLVED",
            "source": "",
            "symbols": [],
            "universe_n": 0,
            "notes": ["no_frozen_registration_or_am_csv"],
            "sources": sources,
        }
    if len(candidates) == 2 and set(candidates[0][1]) != set(candidates[1][1]):
        return {
            "resolved": False,
            "reason": "UNIVERSE_BINDING_UNRESOLVED",
            "source": "",
            "symbols": [],
            "universe_n": 0,
            "notes": ["registration_and_am_csv_disagree", f"reg_n={len(candidates[0][1])}", f"am_n={len(candidates[1][1])}"],
            "sources": sources,
        }
    src, syms = candidates[0]
    return {
        "resolved": True,
        "reason": "",
        "source": src,
        "symbols": syms,
        "universe_n": len(syms),
        "notes": ["no_frozen"] + (["registration_equals_am_csv"] if len(candidates) == 2 else []),
        "sources": sources,
    }


def _first_last_seq(capture: Path) -> dict[str, Any]:
    parts = sorted(p for p in capture.glob("push_part_*.jsonl") if p.stat().st_size > 0)
    if not parts:
        return {"first_seq": None, "last_seq": None, "line_count": 0, "size_bytes": 0, "contiguous_hint": False}
    size = sum(p.stat().st_size for p in parts)
    n = 0
    first_seq = None
    last_seq = None
    first_t = ""
    last_t = ""
    kinds: dict[str, int] = {}
    # Count lines + peek seq from first/last non-empty of first/last files. Full seq scan is replay.
    for i, part in enumerate(parts):
        with part.open("rb") as fh:
            for raw in fh:
                if not raw.strip():
                    continue
                n += 1
                if first_seq is None:
                    try:
                        rec = json.loads(raw)
                        first_seq = int(rec.get("sequence") or 0) or None
                        first_t = str(rec.get("received_at") or rec.get("event_time") or rec.get("persisted_at") or "")
                        kinds[str(rec.get("kind") or "market_push")] = kinds.get(str(rec.get("kind") or "market_push"), 0) + 1
                    except Exception:
                        pass
    with parts[-1].open("rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        fh.seek(max(0, pos - 262144))
        chunk = fh.read().decode("utf-8", errors="replace")
    for line in reversed(chunk.splitlines()):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            last_seq = int(rec.get("sequence") or 0) or last_seq
            last_t = str(rec.get("received_at") or rec.get("event_time") or rec.get("persisted_at") or last_t)
            break
        except Exception:
            continue
    contiguous_hint = (
        first_seq == 1 and last_seq is not None and n == last_seq
    )
    return {
        "first_seq": first_seq,
        "last_seq": last_seq,
        "line_count": n,
        "size_bytes": size,
        "first_event": first_t,
        "last_event": last_t,
        "contiguous_hint": contiguous_hint,
        "n_parts": len(parts),
    }


def classify(day: str, capture: Optional[Path], uni: dict[str, Any], seq: dict[str, Any]) -> dict[str, Any]:
    wd = datetime(int(day[:4]), int(day[4:6]), int(day[6:8])).weekday()
    jpx = wd < 5
    # Japan holidays 2026 in this window (do not guess other holidays).
    if day in {"20260811"}:
        jpx = False
    if capture is None:
        return {
            "capture_class": "MISSING",
            "jpx_trading_day": jpx,
            "exclusion_reason": "NO_CAPTURE",
            "usable": False,
            "full": False,
        }
    comp = _load_json(capture / "capture_completeness.json") or _load_json(capture.parent / "capture_completeness.json")
    summary = _load_json(capture / "capture_summary.json") or _load_json(capture.parent / "capture_summary.json")
    seal = _load_json(capture / "seal.json")
    status = str(comp.get("status") or comp.get("label") or "")
    dropped = int(comp.get("dropped_event_count") or 0)
    mixing = bool(comp.get("session_mixing"))
    seal_pass = comp.get("seal_pass")
    first = str(seq.get("first_event") or summary.get("first_event_at") or summary.get("first_event_time") or comp.get("actual_first_event_at") or "")
    last = str(seq.get("last_event") or summary.get("last_event_at") or summary.get("last_event_time") or comp.get("actual_last_event_at") or "")
    am = comp.get("coverage_am")
    pm = comp.get("coverage_pm")
    if am is None or pm is None:
        ft, lt = _parse_iso(first), _parse_iso(last)
        if am is None and ft is not None:
            dt = datetime.fromtimestamp(ft, JST)
            am = (dt.hour * 60 + dt.minute) <= 9 * 60 + 15
        if pm is None and lt is not None:
            dt = datetime.fromtimestamp(lt, JST)
            pm = (dt.hour * 60 + dt.minute) >= 14 * 60 + 50
        am = bool(am)
        pm = bool(pm)
    reasons: list[str] = []
    st = status.upper()
    if mixing:
        reasons.append("SESSION_MIXING")
    if dropped > 0:
        reasons.append(f"dropped_event_count={dropped}")
    if seq.get("line_count", 0) <= 0:
        return {
            "capture_class": "INVALID",
            "jpx_trading_day": jpx,
            "exclusion_reason": "EMPTY_PUSH",
            "usable": False,
            "full": False,
            "status": status,
            "dropped_event_count": dropped,
            "first_event": first,
            "last_event": last,
            "am_coverage": am,
            "pm_coverage": pm,
        }
    if mixing or "INVALID" in st:
        klass, usable, full = "INVALID", False, False
        reasons.append(status or "INVALID")
    elif dropped > 0 or "DEGRADED" in st:
        klass, usable, full = "DEGRADED", True, False
        reasons.append(status or "DEGRADED")
    elif "PARTIAL" in st:
        klass, usable, full = "PARTIAL", True, False
        reasons.append(status or "PARTIAL_WINDOW")
    elif status == "COMPLETE_CAPTURE" and am and pm and dropped == 0:
        klass, usable, full = "FULL", True, True
    elif am and pm and dropped == 0:
        klass, usable, full = "FULL", True, True
        if not status:
            reasons.append("WINDOW_OBSERVED_NO_COMPLETENESS_FILE")
    elif am or pm:
        klass, usable, full = "PARTIAL", True, False
        reasons.append("PARTIAL_WINDOW" if not (am and pm) else (status or "PARTIAL"))
    else:
        klass, usable, full = "INVALID", False, False
        reasons.append("NO_SESSION_COVERAGE")
    return {
        "capture_class": klass,
        "jpx_trading_day": jpx,
        "exclusion_reason": ";".join(reasons),
        "usable": usable,
        "full": full,
        "status": status,
        "dropped_event_count": dropped,
        "first_event": first,
        "last_event": last,
        "am_coverage": am,
        "pm_coverage": pm,
        "seal_pass": seal_pass,
        "research_adoptable": comp.get("research_adoptable"),
        "event_hint": int(summary.get("total_events") or seal.get("raw_rows") or seq.get("line_count") or 0),
    }


def main() -> int:
    days = sorted(p.name for p in CAP_ROOT.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 8)
    rows = []
    for day in days:
        cap = find_capture_dir(day)
        print(f"INV {day} cap={cap.name if cap else None}", flush=True)
        uni = resolve_universe(day, cap)
        seq = _first_last_seq(cap) if cap else {}
        klass = classify(day, cap, uni, seq)
        row = {
            "date": day,
            "capture_path": str(cap) if cap else "",
            **klass,
            "event_count": seq.get("line_count") or klass.get("event_hint") or 0,
            "first_seq": seq.get("first_seq"),
            "last_seq": seq.get("last_seq"),
            "sequence_continuity_hint": seq.get("contiguous_hint"),
            "size_bytes": seq.get("size_bytes") or 0,
            "universe_resolved": uni["resolved"],
            "universe_source": uni.get("source"),
            "universe_n": uni.get("universe_n"),
            "universe_reason": uni.get("reason"),
            "universe_notes": uni.get("notes"),
            "universe_sources": uni.get("sources"),
        }
        row["replay_eligible"] = bool(
            uni["resolved"] and klass.get("usable") and klass.get("capture_class") in {"FULL", "PARTIAL", "DEGRADED"}
        )
        rows.append(row)
        print(
            f"  class={row['capture_class']} uni={row['universe_source']} n={row['universe_n']} "
            f"events={row['event_count']} contig={row['sequence_continuity_hint']} elig={row['replay_eligible']}",
            flush=True,
        )
    out = ROOT / "results" / "research" / "current_runtime_full_capture_recalc_p1" / "_inventory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"days": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out, "n", len(rows), "eligible", sum(1 for r in rows if r["replay_eligible"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
