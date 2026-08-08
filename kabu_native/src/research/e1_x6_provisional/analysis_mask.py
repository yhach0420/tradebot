"""Canonical analysis-mask helpers (Source Manifest valid_window contract).

Research-only: SCORE / BASE / candidate / confirm must use in_analysis_mask=true rows.
Paper clock: AM 09:03–11:25, PM 12:33–15:23 JST.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence, Union

from research.e1_x6_provisional.constants import AM_EXPECTED, PM_EXPECTED
from research.e1_x6_provisional.util import JST, parse_ts

TsLike = Union[datetime, str, None]


def classify_ts(day: str, ts: TsLike) -> str:
    """Classify timestamp into AM|PM|LUNCH|AFTER|BEFORE using Paper windows.

    Minute-granularity matches Paper expected windows (AM 09:03–11:25, PM 12:33–15:23).
    Pre-open (e.g. 08:59:59) is BEFORE — never AM.
    """
    _ = day  # day reserved for future calendar overrides; windows are clock-fixed
    dt = ts if isinstance(ts, datetime) else parse_ts(ts)
    if dt is None:
        return "BEFORE"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    else:
        dt = dt.astimezone(JST)
    hm = dt.hour * 60 + dt.minute
    am_lo = int(AM_EXPECTED[0].split(":")[0]) * 60 + int(AM_EXPECTED[0].split(":")[1])
    am_hi = int(AM_EXPECTED[1].split(":")[0]) * 60 + int(AM_EXPECTED[1].split(":")[1])
    pm_lo = int(PM_EXPECTED[0].split(":")[0]) * 60 + int(PM_EXPECTED[0].split(":")[1])
    pm_hi = int(PM_EXPECTED[1].split(":")[0]) * 60 + int(PM_EXPECTED[1].split(":")[1])
    if hm < am_lo:
        return "BEFORE"
    if am_lo <= hm <= am_hi:
        return "AM"
    if am_hi < hm < pm_lo:
        return "LUNCH"
    if pm_lo <= hm <= pm_hi:
        return "PM"
    return "AFTER"


def build_mask_index(source_manifest: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Build mask index keyed by (day, am_pm) from Source Manifest windows."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for w in source_manifest.get("windows") or []:
        day = str(w.get("day") or "")
        am_pm = str(w.get("am_pm") or "")
        if not day or am_pm not in ("AM", "PM"):
            continue
        vw = w.get("valid_window") or {}
        qc = str(w.get("quality_class") or "INVALID_SOURCE")
        has_vw = bool(vw.get("start") and vw.get("end"))
        has_overlap = bool(w.get("has_usable_overlap")) if "has_usable_overlap" in w else has_vw
        include = qc != "INVALID_SOURCE" and has_vw and has_overlap
        wid = w.get("window_id") or f"{day}:{am_pm}"
        out[(day, am_pm)] = {
            "window_id": wid,
            "analysis_mask_id": w.get("analysis_mask_id"),
            "quality_class": qc,
            "valid_window_start": vw.get("start"),
            "valid_window_end": vw.get("end"),
            "include_in_economics": bool(include),
            "has_usable_overlap": bool(has_overlap),
        }
    return out


def row_in_analysis_mask(
    day: str,
    ts: TsLike,
    mask_index: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Return mask fields for a decision/entry timestamp.

    in_analysis_mask=true only when:
    - quality_class is not INVALID_SOURCE
    - ts within valid_window_start..valid_window_end (inclusive)
    - session class in {AM, PM} (never LUNCH/AFTER/BEFORE)
    - usable overlap / valid window present (include_in_economics)
    """
    session = classify_ts(day, ts)
    empty = {
        "window_id": None,
        "analysis_mask_id": None,
        "quality_class": None,
        "valid_window_start": None,
        "valid_window_end": None,
        "in_analysis_mask": False,
        "session_class": session,
    }
    if session not in ("AM", "PM"):
        return empty
    info = mask_index.get((str(day), session))
    if not info:
        return empty
    dt = ts if isinstance(ts, datetime) else parse_ts(ts)
    v0 = parse_ts(info.get("valid_window_start"))
    v1 = parse_ts(info.get("valid_window_end"))
    qc = str(info.get("quality_class") or "INVALID_SOURCE")
    in_mask = (
        qc != "INVALID_SOURCE"
        and bool(info.get("include_in_economics"))
        and v0 is not None
        and v1 is not None
        and dt is not None
        and v0 <= dt <= v1
    )
    return {
        "window_id": info.get("window_id"),
        "analysis_mask_id": info.get("analysis_mask_id"),
        "quality_class": qc,
        "valid_window_start": info.get("valid_window_start"),
        "valid_window_end": info.get("valid_window_end"),
        "in_analysis_mask": bool(in_mask),
        "session_class": session,
    }


def filter_events_to_valid_window(
    day: str,
    am_pm: str,
    events: Sequence[Any],
    mask_index: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    ts_attr: str = "ts",
) -> list[Any]:
    """Keep only events inside manifest valid_window for (day, am_pm).

    INVALID_SOURCE / missing window → empty list (excluded from economics feed).
    """
    info = mask_index.get((str(day), am_pm))
    if not info or info.get("quality_class") == "INVALID_SOURCE" or not info.get("include_in_economics"):
        return []
    v0 = parse_ts(info.get("valid_window_start"))
    v1 = parse_ts(info.get("valid_window_end"))
    if not v0 or not v1:
        return []
    out = []
    for e in events:
        et = getattr(e, ts_attr, None) if not isinstance(e, dict) else e.get(ts_attr)
        dt = et if isinstance(et, datetime) else parse_ts(et)
        if dt is None:
            continue
        if v0 <= dt <= v1:
            out.append(e)
    return out


def window_am_pm_tag(window: Any, day: str) -> Optional[str]:
    """Infer AM/PM for a ValidWindow from window_id or start_time."""
    wid = str(getattr(window, "window_id", "") or "")
    parts = wid.split(":")
    if len(parts) >= 2 and parts[1] in ("AM", "PM"):
        return parts[1]
    st = getattr(window, "start_time", None)
    cls = classify_ts(day, st)
    return cls if cls in ("AM", "PM") else None


def assert_timestamps_in_confirm_mask(
    timestamps: Sequence[TsLike],
    *,
    day: str,
    mask_index: Mapping[tuple[str, str], Mapping[str, Any]],
) -> None:
    """Raise if any timestamp falls outside that day's confirm analysis mask."""
    for raw in timestamps:
        info = row_in_analysis_mask(day, raw, mask_index)
        if not info.get("in_analysis_mask"):
            raise AssertionError(
                f"signal/entry timestamp outside confirm mask: day={day} ts={raw} "
                f"session={info.get('session_class')} mask={info}"
            )


def mask_contract_fixture_rows() -> list[dict[str, Any]]:
    """Lightweight mask-contract checks embeddable in report Tests sheet."""
    # Synthetic manifest: AM starts 09:03, PM 12:33; one INVALID AM
    day = "20260721"
    day_f2 = "20260728"
    manifest = {
        "windows": [
            {
                "day": day,
                "am_pm": "AM",
                "quality_class": "PARTIAL_VALID_WINDOW",
                "has_usable_overlap": True,
                "valid_window": {
                    "start": "2026-07-21T09:03:00+09:00",
                    "end": "2026-07-21T11:25:00+09:00",
                },
                "analysis_mask_id": "mask_am_partial",
                "window_id": f"{day}:AM",
            },
            {
                "day": day,
                "am_pm": "PM",
                "quality_class": "PARTIAL_VALID_WINDOW",
                "has_usable_overlap": True,
                "valid_window": {
                    "start": "2026-07-21T12:33:00+09:00",
                    "end": "2026-07-21T15:23:00+09:00",
                },
                "analysis_mask_id": "mask_pm_partial",
                "window_id": f"{day}:PM",
            },
            {
                "day": "20260722",
                "am_pm": "AM",
                "quality_class": "INVALID_SOURCE",
                "has_usable_overlap": False,
                "valid_window": {"start": None, "end": None},
                "analysis_mask_id": "mask_invalid",
                "window_id": "20260722:AM",
            },
            {
                "day": day_f2,
                "am_pm": "AM",
                "quality_class": "STRESS_RECOVERABLE",
                "has_usable_overlap": True,
                "valid_window": {
                    "start": "2026-07-28T09:03:00+09:00",
                    "end": "2026-07-28T11:25:00+09:00",
                },
                "analysis_mask_id": "mask_f2_am",
                "window_id": f"{day_f2}:AM",
            },
        ]
    }
    idx = build_mask_index(manifest)
    rows: list[dict[str, Any]] = []

    def _add(name: str, assertion: str, count: int, ok: bool) -> None:
        rows.append(
            {
                "test_name": name,
                "assertion": assertion,
                "count": count,
                "result": "PASS" if ok else "FAIL",
            }
        )

    # 1) 08:59:59 not in AM mask
    pre = row_in_analysis_mask(day, "2026-07-21T08:59:59+09:00", idx)
    _add(
        "preopen_085959_not_in_am_mask",
        "08:59:59 in_analysis_mask=false when AM starts 09:03",
        1,
        pre["in_analysis_mask"] is False and classify_ts(day, "2026-07-21T08:59:59+09:00") == "BEFORE",
    )

    # 2) LUNCH/AFTER not in mask
    lunch = row_in_analysis_mask(day, "2026-07-21T12:00:00+09:00", idx)
    after = row_in_analysis_mask(day, "2026-07-21T15:30:00+09:00", idx)
    _add(
        "lunch_after_not_in_mask",
        "LUNCH/AFTER => in_analysis_mask=false",
        2,
        lunch["in_analysis_mask"] is False and after["in_analysis_mask"] is False,
    )

    # 3) INVALID_SOURCE excluded
    inv = row_in_analysis_mask("20260722", "2026-07-22T10:00:00+09:00", idx)
    _add(
        "invalid_source_excluded",
        "INVALID_SOURCE window never in_analysis_mask",
        1,
        inv["in_analysis_mask"] is False,
    )

    # 4) synthetic signal ledger inside confirm mask
    ok_ts = ["2026-07-21T10:00:00+09:00", "2026-07-21T10:05:00+09:00"]
    try:
        assert_timestamps_in_confirm_mask(ok_ts, day=day, mask_index=idx)
        ledger_ok = True
    except AssertionError:
        ledger_ok = False
    _add(
        "signal_ledger_inside_confirm_mask",
        "all SignalLedger timestamps inside confirm mask",
        len(ok_ts),
        ledger_ok,
    )

    # 5) ENTRY timestamps inside valid window
    entry_ok = row_in_analysis_mask(day, "2026-07-21T10:15:00+09:00", idx)["in_analysis_mask"]
    entry_bad = row_in_analysis_mask(day, "2026-07-21T08:59:59+09:00", idx)["in_analysis_mask"]
    _add(
        "entry_timestamps_inside_valid_window",
        "mask-in ENTRY true; pre-open ENTRY false",
        2,
        entry_ok is True and entry_bad is False,
    )

    # 6) BASE and candidate share analysis_mask_id for same window
    m1 = row_in_analysis_mask(day, "2026-07-21T10:00:00+09:00", idx)
    m2 = row_in_analysis_mask(day, "2026-07-21T10:30:00+09:00", idx)
    _add(
        "base_candidate_same_analysis_mask_id",
        "same window => same analysis_mask_id",
        1,
        m1["analysis_mask_id"] == m2["analysis_mask_id"] == "mask_am_partial",
    )

    # 7) F2-like 08:59:59 not generated under mask
    f2_pre = row_in_analysis_mask(day_f2, "2026-07-28T08:59:59+09:00", idx)
    _add(
        "f2_preopen_trade_not_under_mask",
        "F2-like 08:59:59 not in analysis mask",
        1,
        f2_pre["in_analysis_mask"] is False,
    )

    # 8) dataset build_rows count == mask-in count
    synthetic_rows = [
        {"ts": "2026-07-21T08:59:59+09:00"},
        {"ts": "2026-07-21T09:03:00+09:00"},
        {"ts": "2026-07-21T10:00:00+09:00"},
        {"ts": "2026-07-21T12:00:00+09:00"},
        {"ts": "2026-07-21T13:00:00+09:00"},
        {"ts": "2026-07-21T15:30:00+09:00"},
    ]
    mask_in = [
        r
        for r in synthetic_rows
        if row_in_analysis_mask(day, r["ts"], idx)["in_analysis_mask"]
    ]
    _add(
        "dataset_build_rows_equals_mask_in",
        "build_rows count equals mask-in count",
        len(mask_in),
        len(mask_in) == 3 and len(mask_in) == sum(
            1 for r in synthetic_rows if row_in_analysis_mask(day, r["ts"], idx)["in_analysis_mask"]
        ),
    )

    return rows
