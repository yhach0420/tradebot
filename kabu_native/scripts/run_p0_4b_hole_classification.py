#!/usr/bin/env python
"""P0-4B: classify 20260810 native_ingest_sequence_holes=38. No full-period recalc."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"

from research.anchor_vs_event_driven.run_comparison import (  # noqa: E402
    _bare,
    capture_event_epoch,
    historical_universe,
    record_event_stamp,
)
from small_paper.v1r_live_dual_lane import canonical_symbol_key  # noqa: E402

CAP10 = (
    ROOT
    / "data"
    / "market_capture"
    / "20260810"
    / "session_ing_20260810_36744_1786315725_0d209efe"
)
CAP20 = (
    ROOT
    / "data"
    / "market_capture"
    / "20260820"
    / "session_ing_20260820_8372_1787179836_fab7382b"
)
OUT = ROOT / "results" / "research" / "native_ingest_hole_classification_p0_4b"

CONTROL_KINDS = {
    "heartbeat",
    "entry_block",
    "gap",
    "control",
    "session",
    "ack",
    "resync",
    "warmup",
    "status",
}


def _iter_all(capture_dir: Path):
    for part in sorted(capture_dir.glob("push_part_*.jsonl")):
        if part.stat().st_size <= 0:
            continue
        with part.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    yield {
                        "sequence": None,
                        "kind": "MALFORMED_JSON",
                        "symbol": "",
                        "part": part.name,
                        "line_no": line_no,
                        "et_ok": False,
                        "streamable": False,
                        "malformed": True,
                        "rec": None,
                    }
                    continue
                seq = rec.get("sequence")
                try:
                    seq_i = int(seq) if seq is not None and seq != "" else None
                except (TypeError, ValueError):
                    seq_i = None
                kind = str(rec.get("kind") or "") or "market_push"
                pay = rec.get("payload") or rec.get("original_payload") or {}
                if not isinstance(pay, dict):
                    pay = {}
                sym = canonical_symbol_key(rec.get("symbol") or pay.get("Symbol") or pay.get("symbol"))
                et = capture_event_epoch(rec, pay)
                streamable = kind in (None, "", "market_push") or kind == "market_push"
                yield {
                    "sequence": seq_i,
                    "kind": kind,
                    "symbol": sym,
                    "part": part.name,
                    "line_no": line_no,
                    "et_ok": et is not None,
                    "streamable": kind in (None, "", "market_push") or kind == "market_push",
                    "malformed": False,
                    "rec": rec,
                    "pay": pay,
                    "et": et,
                }


def classify_record(row: dict[str, Any], universe: set[str]) -> str:
    if row.get("malformed") or row.get("sequence") is None:
        return "MALFORMED_PAYLOAD"
    kind = str(row.get("kind") or "market_push")
    if kind.lower() in CONTROL_KINDS:
        return "SESSION_CONTROL"
    if kind not in ("market_push", "", "None"):
        return "NON_MARKET_EVENT"
    if not row.get("symbol"):
        return "NO_SYMBOL"
    if not row.get("et_ok"):
        return "MALFORMED_PAYLOAD"
    if universe and row["symbol"] not in universe:
        return "NON_UNIVERSE_SYMBOL"
    pay = row.get("pay") if isinstance(row.get("pay"), dict) else {}
    if not pay:
        return "MALFORMED_PAYLOAD"
    return "MARKET_PUSH_VALID"


def analyze(day: str, capture: Path) -> dict[str, Any]:
    universe_list, uni_src = historical_universe(day, capture)
    universe = {canonical_symbol_key(s) for s in universe_list}
    by_seq: dict[int, dict[str, Any]] = {}
    dups = 0
    max_seq = 0
    n_all = 0
    kinds: Counter[str] = Counter()
    for row in _iter_all(capture):
        n_all += 1
        kinds[str(row.get("kind") or "")] += 1
        seq = row.get("sequence")
        if seq is None:
            continue
        seq = int(seq)
        max_seq = max(max_seq, seq)
        if seq in by_seq:
            dups += 1
        else:
            # drop heavy rec after classify fields copied
            by_seq[seq] = {
                "sequence": seq,
                "kind": row["kind"],
                "symbol": row["symbol"],
                "part": row["part"],
                "line_no": row["line_no"],
                "et_ok": row["et_ok"],
                "streamable": row["streamable"],
                "malformed": row["malformed"],
                "pay_empty": not bool(row.get("pay")),
                "class": classify_record(row, universe),
            }

    capture_holes = 0
    capture_missing: list[int] = []
    for s in range(1, max_seq + 1):
        if s not in by_seq:
            capture_holes += 1
            capture_missing.append(s)
            if len(capture_missing) > 50:
                break

    # Simulate _stream_day + ingest_push hole counter (accepted universe only).
    last_accepted: Optional[int] = None
    last_seen: Optional[int] = None
    accepted_holes = 0
    raw_holes = 0
    skip_uni = 0
    skip_dup: set[int] = set()
    ingested_n = 0
    streamed_n = 0
    seen_seqs: set[int] = set()
    gaps: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []

    # Replay in sequence order 1..max (capture order == seq order for COMPLETE)
    for seq in range(1, max_seq + 1):
        row = by_seq.get(seq)
        if row is None:
            continue
        # iter_push filter
        if not row["streamable"] or row["kind"] not in ("market_push", "", "None"):
            continue
        if not row["et_ok"]:
            continue
        streamed_n += 1
        # ingest_push
        seq_i = seq
        if last_seen is not None and seq_i > last_seen + 1:
            raw_holes += 1
        if last_seen is None or seq_i > last_seen:
            last_seen = seq_i
        if seq_i in seen_seqs:
            skip_dup.add(seq_i)
            continue
        sym = row["symbol"]
        if universe and sym not in universe:
            skip_uni += 1
            # last_accepted NOT updated — current Runtime
            continue
        # accepted
        if last_accepted is not None and seq_i > last_accepted + 1:
            accepted_holes += 1
            gap_start = last_accepted + 1
            gap_end = seq_i - 1
            gap_size = gap_end - gap_start + 1
            members = []
            for ms in range(gap_start, gap_end + 1):
                mrow = by_seq.get(ms)
                if mrow is None:
                    klass = "CAPTURE_SEQUENCE_PROBLEM"
                    members.append(
                        {
                            "sequence": ms,
                            "class": klass,
                            "in_capture": False,
                            "streamed": False,
                            "process_market_push_called": False,
                            "ingest_push_called": False,
                            "feature_state_applied": False,
                        }
                    )
                else:
                    klass = mrow["class"]
                    streamed = bool(mrow["streamable"] and mrow["kind"] in ("market_push", "", "None") and mrow["et_ok"])
                    ingest_called = streamed  # _stream_day calls process/ingest iff streamed
                    feature = ingest_called and klass == "MARKET_PUSH_VALID"
                    members.append(
                        {
                            "sequence": ms,
                            "class": klass,
                            "in_capture": True,
                            "kind": mrow["kind"],
                            "symbol": mrow["symbol"],
                            "part": mrow["part"],
                            "line_no": mrow["line_no"],
                            "et_ok": mrow["et_ok"],
                            "streamed": streamed,
                            "process_market_push_called": ingest_called,
                            "ingest_push_called": ingest_called,
                            "feature_state_applied": feature,
                        }
                    )
            gaps.append(
                {
                    "previous_sequence": last_accepted,
                    "current_sequence": seq_i,
                    "gap_start": gap_start,
                    "gap_end": gap_end,
                    "gap_size": gap_size,
                    "current_symbol": sym,
                    "members": members,
                }
            )
            missing_rows.extend(members)
        seen_seqs.add(seq_i)
        last_accepted = seq_i
        ingested_n += 1

    class_counts = Counter(m["class"] for m in missing_rows)
    valid_not_applied = [
        m
        for m in missing_rows
        if m["class"] == "MARKET_PUSH_VALID" and not m.get("feature_state_applied")
    ]
    # MARKET_PUSH_VALID in a hole: if streamed, ingest was called but then it wouldn't be a hole
    # unless universe skip — which would classify NON_UNIVERSE. So VALID in hole means
    # not streamed (et missing) OR not applied. If class is VALID, et_ok and in universe,
    # streamed should be true, feature_state_applied true in our member logic...
    # Contradiction: VALID + in hole means they were skipped by universe? Then class wouldn't be VALID.
    # OR they were not streamable despite market_push — et_ok false would be MALFORMED.
    # REAL gap: VALID and feature_state_applied False.
    real_gap = len(valid_not_applied)

    capture_cont = capture_holes == 0 and dups == 0 and max_seq == n_all
    # n_all may include malformed without seq; COMPLETE 0810 n==max_seq
    capture_cont = capture_holes == 0 and len(by_seq) == max_seq

    return {
        "date": day,
        "capture": str(capture),
        "universe_n": len(universe),
        "universe_source": uni_src,
        "n_all": n_all,
        "max_seq": max_seq,
        "indexed": len(by_seq),
        "capture_holes": capture_holes,
        "capture_missing_head": capture_missing[:20],
        "duplicate_seq": dups,
        "kinds": dict(kinds),
        "streamed_n": streamed_n,
        "ingested_n": ingested_n,
        "skip_universe": skip_uni,
        "skip_duplicate": len(skip_dup),
        "accepted_holes_counter": accepted_holes,
        "raw_seen_holes": raw_holes,
        "gap_groups": gaps,
        "gap_group_count": len(gaps),
        "missing_sequence_count": sum(g["gap_size"] for g in gaps),
        "classification_counts": dict(class_counts),
        "valid_market_not_applied": valid_not_applied,
        "valid_not_applied_n": real_gap,
        "capture_continuity": capture_cont,
        "last_accepted": last_accepted,
    }


def p02_detector_proof(cap20: Path, universe: set[str]) -> dict[str, Any]:
    """If 318783..318790 were valid 285A ticks, skipping them must classify as REAL gap."""
    want = set(range(318783, 318791))
    found = []
    for row in _iter_all(cap20):
        seq = row.get("sequence")
        if seq in want:
            found.append(
                {
                    "sequence": seq,
                    "class": classify_record(row, universe),
                    "symbol": row.get("symbol"),
                    "kind": row.get("kind"),
                    "et_ok": row.get("et_ok"),
                }
            )
        if seq and seq > 318791:
            break
    by = {f["sequence"]: f for f in found}
    would_detect = all(
        by.get(s, {}).get("class") == "MARKET_PUSH_VALID" for s in sorted(want) if s in by
    ) and len(by) == len(want)
    return {
        "window": "318783-318790",
        "found": found,
        "all_present": len(by) == len(want),
        "would_classify_as_valid_market": would_detect,
        "P0_2_318791_REAL_DROP_STILL_DETECTED": "PASS" if would_detect else "FAIL",
    }


def main() -> int:
    print("P0-4B analyze 20260810", flush=True)
    a10 = analyze("20260810", CAP10)
    print(
        f"  gaps={a10['gap_group_count']} missing={a10['missing_sequence_count']} "
        f"skip_uni={a10['skip_universe']} accepted_holes={a10['accepted_holes_counter']} "
        f"raw_holes={a10['raw_seen_holes']} classes={a10['classification_counts']}",
        flush=True,
    )
    if a10["capture_holes"]:
        print("CAPTURE_SEQUENCE_PROBLEM", a10["capture_missing_head"], flush=True)
        return 2
    print("P0-4B analyze 20260820", flush=True)
    a20 = analyze("20260820", CAP20)
    print(
        f"  gaps={a20['gap_group_count']} accepted_holes={a20['accepted_holes_counter']} "
        f"raw_holes={a20['raw_seen_holes']}",
        flush=True,
    )
    uni20 = {_bare(s) for s in historical_universe("20260820", CAP20)[0]}
    print("P0-4B P0-2 detector window", flush=True)
    p02 = p02_detector_proof(CAP20, uni20)
    print("  ", p02["P0_2_318791_REAL_DROP_STILL_DETECTED"], "n", len(p02["found"]), flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    payload = {"a10": a10, "a20": a20, "p02": p02}
    (OUT / "_payload.json").write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    print("wrote", OUT / "_payload.json", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
