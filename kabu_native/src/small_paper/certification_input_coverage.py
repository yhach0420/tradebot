"""Full-Day certification market-input coverage gate (V23).

CERTIFICATION_ONLY_INPUT. Not Strategy performance / PnL / PF evidence.
Fixture metadata anchors_16 is not Runtime proof — inspect the actual stream.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from small_paper.v1r_primary_runtime import CLOCK_GRID

JST = ZoneInfo("Asia/Tokyo")

CERTIFICATION_ONLY_INPUT = "CERTIFICATION_ONLY_INPUT"
CERTIFICATION_INPUT_COVERAGE_FAIL = "CERTIFICATION_INPUT_COVERAGE_FAIL"
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


def _hm(dt: datetime) -> str:
    return f"{dt.hour:02d}:{dt.minute:02d}"


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
        "sequence_gap": 0,
        "sequence_continuity_ok": False,
        "failures": [],
    }
    if not p.is_file():
        out["failures"] = ["stream_missing"]
        return out

    anchors = {f"{h:02d}:{m:02d}" for h, m in CLOCK_GRID}
    seen_anchors: set[str] = set()
    first: Optional[datetime] = None
    last: Optional[datetime] = None
    seqs: list[int] = []
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
            if first is None or dt < first:
                first = dt
            if last is None or dt > last:
                last = dt
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
            seq = obj.get("sequence") or obj.get("seq") or (obj.get("payload") or {}).get("sequence")
            try:
                if seq is not None:
                    seqs.append(int(seq))
            except (TypeError, ValueError):
                pass

    out["rows"] = rows
    out["parse_ok"] = parse_ok
    out["first_event_time"] = first.isoformat(timespec="seconds") if first else None
    out["last_event_time"] = last.isoformat(timespec="seconds") if last else None
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
    gap = 0
    if len(seqs) >= 2:
        ordered = sorted(seqs)
        for a, b in zip(ordered, ordered[1:]):
            if b > a + 1:
                gap += b - a - 1
    out["sequence_gap"] = gap
    out["sequence_continuity_ok"] = parse_ok > 0 and (not seqs or gap == 0)

    failures: list[str] = []
    if parse_ok < 100:
        failures.append("too_few_events")
    if first is None or last is None:
        failures.append("event_time_missing")
    else:
        if first.hour > 9 or (first.hour == 9 and first.minute > 5):
            if not out["pre_am"]:
                failures.append("pre_am_missing")
        if last.hour < 15:
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
    out["failures"] = failures
    out["ok"] = not failures
    if out["ok"]:
        out["code"] = "CERTIFICATION_INPUT_COVERAGE_PASS"
    return out


def evaluate_full_day_input_coverage(path: Path, *, trading_date: str = TARGET_TRADING_DATE) -> dict[str, Any]:
    report = inspect_certification_stream(path, trading_date=trading_date)
    if not report.get("ok"):
        report["code"] = CERTIFICATION_INPUT_COVERAGE_FAIL
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
    """Deterministic Ingress-boundary stream. CERTIFICATION_ONLY_INPUT."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    minute_kept: set[tuple[int, int, int]] = set()
    kept = 0
    scanned = 0
    src_used: list[str] = []
    with dest.open("w", encoding="utf-8") as fout:
        for src in sources:
            path = Path(src)
            files: list[Path] = []
            if path.is_file():
                files = [path]
            elif path.is_dir():
                files = sorted(path.glob("push_part_*.jsonl")) or sorted(path.glob("*.jsonl"))
            if not files:
                continue
            src_used.append(str(path))
            for fp in files:
                try:
                    fh = fp.open("r", encoding="utf-8", errors="replace")
                except OSError:
                    continue
                with fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        scanned += 1
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        rec = _remap_record(obj, trading_date)
                        dt = _parse_event_dt(rec)
                        if dt is None:
                            continue
                        if not _symbol(rec):
                            continue
                        if not _keep_reason(dt, minute_kept=minute_kept):
                            continue
                        fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        kept += 1
    coverage = inspect_certification_stream(dest, trading_date=trading_date)
    manifest = {
        "purpose": CERTIFICATION_ONLY_INPUT,
        "strategy_evaluation_forbidden": True,
        "pnl_pf_must_not_accrue": True,
        "input_boundary": "MARKET_INGRESS_PUSH",
        "native_candidate_fill_exit_direct_inject": False,
        "trading_date": trading_date,
        "sources": src_used,
        "dest": str(dest),
        "scanned": scanned,
        "kept": kept,
        "coverage": coverage,
        "ok": bool(coverage.get("ok")),
        "code": coverage.get("code"),
    }
    man_path = dest.with_suffix(".manifest.json")
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    coverage["manifest_path"] = str(man_path)
    coverage["kept"] = kept
    coverage["scanned"] = scanned
    coverage["sources"] = src_used
    coverage["purpose"] = CERTIFICATION_ONLY_INPUT
    return coverage


def discover_certification_sources(native_root: Path) -> list[Path]:
    """Prefer a complete Full-Day capture; otherwise compose from existing PUSH captures."""
    native = Path(native_root)
    preferred = [
        native / "results" / "cache" / "v1r_v3_full_replay_20260812" / "capture_universe_stream.jsonl",
        native / "results" / "cache" / "v1r_v3_full_replay_20260812",
    ]
    found: list[Path] = []
    for p in preferred:
        if p.is_file() or p.is_dir():
            found.append(p)
    capture_root = native / "data" / "market_capture"
    if capture_root.is_dir():
        days = sorted([d for d in capture_root.iterdir() if d.is_dir() and d.name.isdigit()], reverse=True)
        for day in days:
            sessions = sorted(day.glob("session_*"), reverse=True)
            for sess in sessions:
                if list(sess.glob("push_part_*.jsonl")) or list(sess.glob("*.jsonl")):
                    found.append(sess)
                    if len(found) >= 8:
                        return found
    return found
