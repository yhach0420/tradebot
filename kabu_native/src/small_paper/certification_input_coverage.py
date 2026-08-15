"""Full-Day certification market-input coverage gate (V24).

CERTIFICATION_ONLY_INPUT. Not Strategy performance / PnL / PF evidence.
Delivery SoT is dest file-order + cert_sequence, not source-sequence union.
"""
from __future__ import annotations

import heapq
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.v1r_primary_runtime import CLOCK_GRID

JST = ZoneInfo("Asia/Tokyo")

CERTIFICATION_ONLY_INPUT = "CERTIFICATION_ONLY_INPUT"
CERTIFICATION_INPUT_COVERAGE_FAIL = "CERTIFICATION_INPUT_COVERAGE_FAIL"
CERTIFICATION_INPUT_COVERAGE_PASS = "CERTIFICATION_INPUT_COVERAGE_PASS"
TARGET_TRADING_DATE = "20260812"

ANCHOR_PAD_SEC = 90.0
PRE_AM = ((8, 50), (9, 3))
LUNCH = ((11, 25), (12, 40))
AM_SESSION = ((9, 0), (11, 30))
PM_SESSION = ((12, 30), (15, 10))
FILL_WINDOW = ((9, 5), (14, 50))
EXIT_600 = ((9, 15), (15, 10))
EXIT_750 = ((9, 20), (15, 10))
SESSION_CLOSE = ((14, 50), (15, 35))
DAY_SPAN = ((8, 50), (15, 35))


def _parse_event_dt(obj: Mapping[str, Any]) -> Optional[datetime]:
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    orig = obj.get("original_payload") if isinstance(obj.get("original_payload"), dict) else {}
    raw = str(
        obj.get("received_at")
        or obj.get("__replay_received_at__")
        or payload.get("received_at")
        or orig.get("received_at")
        or obj.get("recorded_at")
        or payload.get("recorded_at")
        or ""
    )
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def _in_span(dt: datetime, start: tuple[int, int], end: tuple[int, int]) -> bool:
    t = dt.hour * 60 + dt.minute
    a = start[0] * 60 + start[1]
    b = end[0] * 60 + end[1]
    return a <= t <= b


def _anchor_label(dt: datetime) -> Optional[str]:
    for h, m in CLOCK_GRID:
        target = dt.replace(hour=h, minute=m, second=0, microsecond=0)
        if abs((dt - target).total_seconds()) <= ANCHOR_PAD_SEC:
            return f"{h:02d}:{m:02d}"
    return None


def _symbol(obj: Mapping[str, Any]) -> str:
    if obj.get("Symbol"):
        return str(obj.get("Symbol") or "").strip()
    if obj.get("symbol"):
        return str(obj.get("symbol") or "").strip()
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    orig = obj.get("original_payload") if isinstance(obj.get("original_payload"), dict) else {}
    return str(payload.get("Symbol") or orig.get("Symbol") or obj.get("raw") or "").strip()


def _source_seq(obj: Mapping[str, Any]) -> Optional[int]:
    seq = obj.get("source_sequence")
    if seq is None:
        seq = obj.get("sequence") or obj.get("seq") or (obj.get("payload") or {}).get("sequence")
    try:
        if seq is None:
            return None
        return int(seq)
    except (TypeError, ValueError):
        return None


def _canonical(path: Path) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(Path(path))


def _expand_source_files(src: Path) -> list[Path]:
    path = Path(src)
    if path.is_file():
        return [path]
    if path.is_dir():
        parts = sorted(path.glob("push_part_*.jsonl"))
        if parts:
            return parts
        return [p for p in sorted(path.glob("*.jsonl")) if p.name != "heartbeat.jsonl"]
    return []


def _orig_date(obj: Mapping[str, Any]) -> str:
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    orig = obj.get("original_payload") if isinstance(obj.get("original_payload"), dict) else {}
    raw = str(
        obj.get("received_at")
        or obj.get("__replay_received_at__")
        or payload.get("received_at")
        or orig.get("received_at")
        or obj.get("recorded_at")
        or ""
    )
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST).strftime("%Y%m%d")
    except Exception:
        return ""


def _sequence_audit(seqs: list[int]) -> dict[str, Any]:
    if not seqs:
        return {
            "n": 0,
            "unique": 0,
            "gap": 0,
            "duplicate": 0,
            "backward": 0,
            "min": None,
            "max": None,
        }
    gap = 0
    ordered = sorted(seqs)
    for a, b in zip(ordered, ordered[1:]):
        if b > a + 1:
            gap += b - a - 1
    backward = 0
    prev = seqs[0]
    for s in seqs[1:]:
        if s < prev:
            backward += 1
        prev = s
    uniq = set(seqs)
    return {
        "n": len(seqs),
        "unique": len(uniq),
        "gap": gap,
        "duplicate": len(seqs) - len(uniq),
        "backward": backward,
        "min": min(seqs),
        "max": max(seqs),
    }


def inspect_certification_stream(path: Path, *, trading_date: str = TARGET_TRADING_DATE) -> dict[str, Any]:
    """Inspect an actual Ingress-boundary jsonl (not fixture metadata)."""
    p = Path(path)
    out: dict[str, Any] = {
        "ok": False,
        "code": CERTIFICATION_INPUT_COVERAGE_FAIL,
        "purpose": CERTIFICATION_ONLY_INPUT,
        "strategy_evaluation_forbidden": True,
        "path": str(p),
        "trading_date": trading_date,
        "rows": 0,
        "parse_ok": 0,
        "first_event_time": None,
        "last_event_time": None,
        "file_first_event_time": None,
        "file_last_event_time": None,
        "coverage_am": False,
        "coverage_pm": False,
        "pre_am": False,
        "lunch_boundary": False,
        "fill_window": False,
        "coverage_600s": False,
        "coverage_750s": False,
        "session_close": False,
        "anchors_seen": [],
        "anchors_16": False,
        "file_time_order_ok": False,
        "file_time_backward_count": 0,
        "cert_sequence_continuity_ok": False,
        "cert_sequence_first": None,
        "cert_sequence_last": None,
        "cert_sequence_gap": 0,
        "cert_sequence_duplicate": 0,
        "cert_sequence_backward": 0,
        "source_sequence_audit": {},
        "sequence_gap": 0,
        "sequence_continuity_ok": False,
        "unique_source_scan": True,
        "duplicate_source_count": 0,
        "delivery_manifest_valid": True,
        "stream_is_complete_market_tape": False,
        "certification_stream_is_deterministic": True,
        "failures": [],
    }
    if not p.is_file():
        out["failures"] = ["stream_missing"]
        return out

    anchors = {f"{h:02d}:{m:02d}" for h, m in CLOCK_GRID}
    seen_anchors: set[str] = set()
    file_first: Optional[datetime] = None
    file_last: Optional[datetime] = None
    min_dt: Optional[datetime] = None
    max_dt: Optional[datetime] = None
    src_seqs: list[int] = []
    cert_seqs: list[int] = []
    time_backward = 0
    prev_dt: Optional[datetime] = None
    n_am = n_pm = n_pre = n_lunch = n_fill = n_600 = n_750 = n_close = 0
    parse_ok = 0
    rows = 0
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            rows += 1
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            dt = _parse_event_dt(obj)
            if dt is None:
                continue
            parse_ok += 1
            if file_first is None:
                file_first = dt
            file_last = dt
            if min_dt is None or dt < min_dt:
                min_dt = dt
            if max_dt is None or dt > max_dt:
                max_dt = dt
            if prev_dt is not None and dt < prev_dt:
                time_backward += 1
            prev_dt = dt
            lab = _anchor_label(dt)
            if lab:
                seen_anchors.add(lab)
            if _in_span(dt, *AM_SESSION):
                n_am += 1
            if _in_span(dt, *PM_SESSION):
                n_pm += 1
            if _in_span(dt, *PRE_AM):
                n_pre += 1
            if _in_span(dt, *LUNCH):
                n_lunch += 1
            if _in_span(dt, *FILL_WINDOW):
                n_fill += 1
            if _in_span(dt, *EXIT_600):
                n_600 += 1
            if _in_span(dt, *EXIT_750):
                n_750 += 1
            if _in_span(dt, *SESSION_CLOSE):
                n_close += 1
            src = _source_seq(obj)
            if src is not None:
                src_seqs.append(src)
            try:
                if obj.get("cert_sequence") is not None:
                    cert_seqs.append(int(obj.get("cert_sequence")))
            except (TypeError, ValueError):
                pass

    out["rows"] = rows
    out["parse_ok"] = parse_ok
    out["file_first_event_time"] = file_first.isoformat(timespec="seconds") if file_first else None
    out["file_last_event_time"] = file_last.isoformat(timespec="seconds") if file_last else None
    out["first_event_time"] = out["file_first_event_time"]
    out["last_event_time"] = out["file_last_event_time"]
    out["min_event_time"] = min_dt.isoformat(timespec="seconds") if min_dt else None
    out["max_event_time"] = max_dt.isoformat(timespec="seconds") if max_dt else None
    out["coverage_am"] = n_am > 0
    out["coverage_pm"] = n_pm > 0
    out["pre_am"] = n_pre > 0
    out["lunch_boundary"] = n_lunch > 0
    out["fill_window"] = n_fill > 0
    out["coverage_600s"] = n_600 > 0
    out["coverage_750s"] = n_750 > 0
    out["session_close"] = n_close > 0
    out["anchors_seen"] = sorted(seen_anchors)
    out["anchors_16"] = seen_anchors == anchors
    out["counts"] = {
        "am": n_am,
        "pm": n_pm,
        "pre_am": n_pre,
        "lunch": n_lunch,
        "fill": n_fill,
        "exit_600": n_600,
        "exit_750": n_750,
        "session_close": n_close,
    }
    out["file_time_backward_count"] = time_backward
    out["file_time_order_ok"] = parse_ok > 0 and time_backward == 0
    src_audit = _sequence_audit(src_seqs)
    out["source_sequence_audit"] = src_audit
    out["sequence_gap"] = src_audit["gap"]
    cert_gap = 0
    cert_dup = 0
    cert_back = 0
    if cert_seqs:
        seen: set[int] = set()
        prev = None
        for s in cert_seqs:
            if s in seen:
                cert_dup += 1
            seen.add(s)
            if prev is not None:
                if s < prev:
                    cert_back += 1
                elif s > prev + 1:
                    cert_gap += s - prev - 1
            prev = s
        out["cert_sequence_first"] = cert_seqs[0]
        out["cert_sequence_last"] = cert_seqs[-1]
    out["cert_sequence_gap"] = cert_gap
    out["cert_sequence_duplicate"] = cert_dup
    out["cert_sequence_backward"] = cert_back
    cert_ok = (
        parse_ok > 0
        and len(cert_seqs) == parse_ok
        and cert_seqs
        and cert_seqs[0] == 1
        and cert_seqs[-1] == len(cert_seqs)
        and cert_gap == 0
        and cert_dup == 0
        and cert_back == 0
    )
    out["cert_sequence_continuity_ok"] = cert_ok
    out["sequence_continuity_ok"] = cert_ok

    failures: list[str] = []
    if parse_ok < 100:
        failures.append("too_few_events")
    if file_first is None or file_last is None:
        failures.append("event_time_missing")
    else:
        if file_first.hour > 9 or (file_first.hour == 9 and file_first.minute > 5):
            if not out["pre_am"]:
                failures.append("pre_am_missing")
        if file_last.hour < 15:
            failures.append("last_event_before_session_close")
    if not out["coverage_am"]:
        failures.append("am_coverage_missing")
    if not out["coverage_pm"]:
        failures.append("pm_coverage_missing")
    if not out["lunch_boundary"]:
        failures.append("lunch_boundary_missing")
    if not out["fill_window"]:
        failures.append("fill_window_missing")
    if not out["coverage_600s"]:
        failures.append("600s_window_missing")
    if not out["coverage_750s"]:
        failures.append("750s_window_missing")
    if not out["session_close"]:
        failures.append("session_close_missing")
    missing_anchors = sorted(anchors - seen_anchors)
    if missing_anchors:
        failures.append("anchors_incomplete:" + ",".join(missing_anchors))
    if not out["file_time_order_ok"]:
        failures.append("file_time_order_ok")
    if not out["cert_sequence_continuity_ok"]:
        failures.append("cert_sequence_continuity")
    out["failures"] = failures
    out["ok"] = not failures
    if out["ok"]:
        out["code"] = CERTIFICATION_INPUT_COVERAGE_PASS
    return out


def evaluate_full_day_input_coverage(path: Path, *, trading_date: str = TARGET_TRADING_DATE) -> dict[str, Any]:
    report = inspect_certification_stream(path, trading_date=trading_date)
    man_path = Path(path).with_suffix(".manifest.json")
    if man_path.is_file():
        try:
            man = json.loads(man_path.read_text(encoding="utf-8"))
        except Exception:
            man = {}
        report["unique_source_scan"] = bool(man.get("unique_source_scan", True))
        report["duplicate_source_count"] = int(man.get("duplicate_source_count") or 0)
        report["delivery_manifest_valid"] = bool(man.get("delivery_manifest_valid", True))
        report["stream_is_complete_market_tape"] = bool(man.get("stream_is_complete_market_tape", False))
        if not report["unique_source_scan"]:
            report["failures"] = list(report.get("failures") or []) + ["duplicate_source_scan"]
        if not report["delivery_manifest_valid"]:
            report["failures"] = list(report.get("failures") or []) + ["delivery_manifest_invalid"]
        report["ok"] = not report["failures"]
    if not report.get("ok"):
        report["code"] = CERTIFICATION_INPUT_COVERAGE_FAIL
    else:
        report["code"] = CERTIFICATION_INPUT_COVERAGE_PASS
    return report


def _remap_dt_text(raw: str, trading_date: str) -> str:
    if not raw:
        return raw
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    dt = dt.astimezone(JST)
    y, m, d = int(trading_date[:4]), int(trading_date[4:6]), int(trading_date[6:8])
    dt = dt.replace(year=y, month=m, day=d)
    return dt.isoformat(timespec="milliseconds")


def _remap_record(obj: dict[str, Any], trading_date: str) -> dict[str, Any]:
    out = dict(obj)
    for key in ("received_at", "recorded_at", "__replay_received_at__"):
        if out.get(key):
            out[key] = _remap_dt_text(str(out.get(key)), trading_date)
    for nested_key in ("payload", "original_payload"):
        nested = out.get(nested_key)
        if isinstance(nested, dict):
            child = dict(nested)
            for key in ("received_at", "recorded_at"):
                if child.get(key):
                    child[key] = _remap_dt_text(str(child.get(key)), trading_date)
            out[nested_key] = child
    return out


def _keep_reason(dt: datetime, *, minute_kept: set[tuple[int, int, int]]) -> bool:
    if _anchor_label(dt):
        return True
    if _in_span(dt, *PRE_AM) or _in_span(dt, *LUNCH) or _in_span(dt, *SESSION_CLOSE):
        return True
    if not _in_span(dt, *DAY_SPAN):
        return False
    mk = (dt.hour, dt.minute, dt.second // 5)
    if mk in minute_kept:
        return False
    if sum(1 for x in minute_kept if x[0] == dt.hour and x[1] == dt.minute) >= 12:
        return False
    minute_kept.add(mk)
    return True


def build_full_day_certification_stream(
    sources: Iterable[Path],
    dest: Path,
    *,
    trading_date: str = TARGET_TRADING_DATE,
) -> dict[str, Any]:
    """Deterministic chronological Ingress-boundary stream. CERTIFICATION_ONLY_INPUT."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    requested: list[Path] = []
    for src in sources:
        requested.extend(_expand_source_files(Path(src)))
    seen_canon: set[str] = set()
    unique_files: list[Path] = []
    duplicate_source_count = 0
    for fp in requested:
        key = _canonical(fp)
        if key in seen_canon:
            duplicate_source_count += 1
            continue
        seen_canon.add(key)
        unique_files.append(fp)

    minute_kept: set[tuple[int, int, int]] = set()
    scanned = 0
    source_audits: list[dict[str, Any]] = []
    streams: list[list[tuple[datetime, int, str, int, int, dict[str, Any]]]] = []

    for priority, fp in enumerate(unique_files):
        source_id = _canonical(fp)
        kept_rows: list[tuple[datetime, int, str, int, int, dict[str, Any]]] = []
        src_scanned = 0
        src_kept = 0
        orig_dates: dict[str, int] = {}
        src_seqs: list[int] = []
        symbols: set[str] = set()
        first_dt = last_dt = None
        row_index = 0
        remap_n = 0
        try:
            fh = fp.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if not line.strip():
                    continue
                scanned += 1
                src_scanned += 1
                row_index += 1
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                od = _orig_date(obj)
                if od:
                    orig_dates[od] = orig_dates.get(od, 0) + 1
                    if od != trading_date:
                        remap_n += 1
                rec = _remap_record(obj, trading_date)
                dt = _parse_event_dt(rec)
                if dt is None:
                    continue
                sy = _symbol(rec)
                if not sy:
                    continue
                symbols.add(sy)
                if not _keep_reason(dt, minute_kept=minute_kept):
                    continue
                src_seq = _source_seq(obj)
                if src_seq is not None:
                    src_seqs.append(src_seq)
                rec["source_id"] = source_id
                rec["source_path"] = str(fp)
                rec["source_original_date"] = od or None
                rec["source_row_index"] = row_index
                rec["source_sequence"] = src_seq
                src_kept += 1
                if first_dt is None:
                    first_dt = dt
                last_dt = dt
                kept_rows.append((dt, priority, source_id, src_seq if src_seq is not None else 0, row_index, rec))
        source_audits.append(
            {
                "source_id": source_id,
                "path": str(fp),
                "original_date": sorted(orig_dates.keys()),
                "scanned": src_scanned,
                "kept": src_kept,
                "first": first_dt.isoformat(timespec="seconds") if first_dt else None,
                "last": last_dt.isoformat(timespec="seconds") if last_dt else None,
                "sequence_available": bool(src_seqs),
                "original_sequence": _sequence_audit(src_seqs),
                "time_backward": _sequence_audit([]),
                "symbol_count": len(symbols),
                "remap_rule": "replace_YMD_keep_clock_JST" if remap_n else "none",
                "remap_from_non_target_rows": remap_n,
            }
        )
        tb = 0
        prev = None
        for dt, *_rest in kept_rows:
            if prev is not None and dt < prev:
                tb += 1
            prev = dt
        source_audits[-1]["time_backward"] = tb
        streams.append(kept_rows)

    heap: list[tuple[datetime, int, str, int, int, int, dict[str, Any]]] = []
    indexes = [0] * len(streams)
    for i, rows in enumerate(streams):
        if rows:
            dt, pr, sid, seq, rid, rec = rows[0]
            heapq.heappush(heap, (dt, pr, sid, seq, rid, i, rec))
            indexes[i] = 1

    kept = 0
    with dest.open("w", encoding="utf-8") as fout:
        while heap:
            dt, pr, sid, seq, rid, i, rec = heapq.heappop(heap)
            kept += 1
            rec = dict(rec)
            rec["cert_sequence"] = kept
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            nxt = indexes[i]
            if nxt < len(streams[i]):
                ndt, npr, nsid, nseq, nrid, nrec = streams[i][nxt]
                heapq.heappush(heap, (ndt, npr, nsid, nseq, nrid, i, nrec))
                indexes[i] = nxt + 1

    unique_source_scan = duplicate_source_count == 0
    coverage = inspect_certification_stream(dest, trading_date=trading_date)
    coverage["unique_source_scan"] = unique_source_scan
    coverage["duplicate_source_count"] = duplicate_source_count
    coverage["delivery_manifest_valid"] = True
    coverage["stream_is_complete_market_tape"] = False
    coverage["certification_stream_is_deterministic"] = True
    if not unique_source_scan:
        coverage["failures"] = list(coverage.get("failures") or []) + ["duplicate_source_scan"]
        coverage["ok"] = False
        coverage["code"] = CERTIFICATION_INPUT_COVERAGE_FAIL
    manifest = {
        "purpose": CERTIFICATION_ONLY_INPUT,
        "strategy_evaluation_forbidden": True,
        "pnl_pf_must_not_accrue": True,
        "input_boundary": "MARKET_INGRESS_PUSH",
        "native_candidate_fill_exit_direct_inject": False,
        "trading_date": trading_date,
        "sources": [str(p) for p in unique_files],
        "requested_files": [str(p) for p in requested],
        "dest": str(dest),
        "scanned": scanned,
        "kept": kept,
        "unique_source_count": len(unique_files),
        "unique_source_scan": unique_source_scan,
        "duplicate_source_count": duplicate_source_count,
        "stream_is_complete_market_tape": False,
        "certification_stream_is_deterministic": True,
        "delivery_manifest_valid": True,
        "remap_rule": "replace_YMD_keep_clock_JST",
        "source_audit": source_audits,
        "coverage": coverage,
        "ok": bool(coverage.get("ok")),
        "code": coverage.get("code"),
    }
    man_path = dest.with_suffix(".manifest.json")
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    coverage["manifest_path"] = str(man_path)
    coverage["kept"] = kept
    coverage["scanned"] = scanned
    coverage["sources"] = manifest["sources"]
    coverage["source_audit"] = source_audits
    coverage["unique_source_count"] = len(unique_files)
    coverage["purpose"] = CERTIFICATION_ONLY_INPUT
    return coverage


def discover_certification_sources(native_root: Path) -> list[Path]:
    """Unique physical PUSH sources. File+parent-dir of the same jsonl is not duplicated."""
    native = Path(native_root)
    preferred_file = (
        native / "results" / "cache" / "v1r_v3_full_replay_20260812" / "capture_universe_stream.jsonl"
    )
    found: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        for fp in _expand_source_files(p):
            key = _canonical(fp)
            if key in seen:
                continue
            seen.add(key)
            found.append(fp)

    if preferred_file.is_file():
        _add(preferred_file)
    capture_root = native / "data" / "market_capture"
    if capture_root.is_dir():
        days = sorted([d for d in capture_root.iterdir() if d.is_dir() and d.name.isdigit()], reverse=True)
        for day in days:
            sessions = sorted(day.glob("session_*"), reverse=True)
            for sess in sessions:
                if list(sess.glob("push_part_*.jsonl")) or list(sess.glob("*.jsonl")):
                    _add(sess)
                    if len(found) >= 8:
                        return found
    return found
