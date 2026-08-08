"""P0 Source Manifest builders (day × AM/PM × selected window)."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x6_provisional.constants import (
    DAY27_PREFERRED_SESSION,
    DAYS,
    DEDUP_KEY_RULE,
    FINAL_BANNER,
    PROVISIONAL_BANNER,
)
from research.e1_x6_provisional.util import (
    coverage_ratio,
    expected_window_iso,
    native_root,
    norm_sym,
    parse_ts,
    progress,
    read_json,
    sha256_obj,
    sha256_text,
)


def _universe_for_day(day: str) -> dict[str, Any]:
    root = native_root()
    symbols: list[str] = []
    source = "UNKNOWN"
    # Prefer registration_manifest under day root (legacy)
    reg = root / "data" / "market_capture" / day / "registration_manifest.json"
    if reg.is_file():
        rm = read_json(reg)
        raw = rm.get("actual_symbols") or rm.get("registered_symbols") or []
        symbols = sorted({norm_sym(x) for x in raw})
        source = "registration_manifest"
    if not symbols:
        # universe CSV used by canonical replay
        csv_p = root / "results" / "reports" / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
        if csv_p.is_file():
            import pandas as pd

            df = pd.read_csv(csv_p)
            col = "symbol" if "symbol" in df.columns else df.columns[0]
            symbols = sorted({norm_sym(x) for x in df[col].tolist()})
            source = str(csv_p.name)
    uni_sha = sha256_text("\n".join(symbols) + ("\n" if symbols else ""))
    return {
        "symbols": symbols,
        "symbol_count": len(symbols),
        "universe_sha256": uni_sha,
        "universe_source": source,
    }


def _empty_meta(day: str, *, error: str, excluded: Optional[list] = None) -> dict[str, Any]:
    return {
        "layout": "MISSING",
        "session_id": None,
        "session_dir": f"data/market_capture/{day}",
        "parts": [],
        "seal_pass": False,
        "capture_status": error,
        "raw_event_count": "UNKNOWN",
        "symbols_seen_count": "UNKNOWN",
        "duplicate_payload_count": "UNKNOWN",
        "malformed_payload_count": "FIELD_ABSENT",
        "dropped_event_count": "UNKNOWN",
        "disconnect_count": "UNKNOWN",
        "reconnect_count": "UNKNOWN",
        "first_event_at": None,
        "last_event_at": None,
        "actual_submit": 0,
        "actual_cancel": 0,
        "first_dt": None,
        "last_dt": None,
        "source_priority": "NONE",
        "completeness_status": error,
        "entry_block_reason": None,
        "research_adoptable": False,
        "excluded_sessions": excluded or [],
        "error": error,
    }


def _legacy_capture_meta(day: str) -> dict[str, Any]:
    day_dir = native_root() / "data" / "market_capture" / day
    summary_p = day_dir / "capture_summary.json"
    seal_p = day_dir / "capture_seal.json"
    if not summary_p.is_file() or not seal_p.is_file():
        return _empty_meta(day, error="LEGACY_CAPTURE_ARTIFACTS_MISSING")
    summary = read_json(summary_p)
    seal = read_json(seal_p)
    parts = sorted(p.name for p in day_dir.glob("push_part_*.jsonl"))
    first = parse_ts(summary.get("first_event_at"))
    last = parse_ts(summary.get("last_event_at"))
    return {
        "layout": "LEGACY_FLAT_CAPTURE",
        "session_id": seal.get("capture_session_id") or f"legacy_{day}",
        "session_dir": f"data/market_capture/{day}",
        "parts": parts,
        "seal_pass": bool(seal.get("seal_pass")),
        "capture_status": summary.get("capture_status"),
        "raw_event_count": summary.get("total_events", "UNKNOWN"),
        "symbols_seen_count": summary.get("symbols_seen_count", "UNKNOWN"),
        "duplicate_payload_count": summary.get("duplicate_payload_count", "UNKNOWN"),
        "malformed_payload_count": summary.get("malformed_payload_count", "FIELD_ABSENT")
        if "malformed_payload_count" in summary
        else "FIELD_ABSENT",
        "dropped_event_count": summary.get("dropped_event_count", "UNKNOWN"),
        "disconnect_count": summary.get("disconnect_count", "UNKNOWN"),
        "reconnect_count": summary.get("reconnect_count", "UNKNOWN"),
        "first_event_at": summary.get("first_event_at"),
        "last_event_at": summary.get("last_event_at"),
        "actual_submit": summary.get("actual_submit"),
        "actual_cancel": summary.get("actual_cancel"),
        "first_dt": first,
        "last_dt": last,
        "source_priority": "LEGACY_FLAT",
        "completeness_status": summary.get("capture_status") or "UNKNOWN",
        "entry_block_reason": None,
        "research_adoptable": True if seal.get("seal_pass") else False,
        "excluded_sessions": [],
    }


def _ingress_selected(day: str) -> dict[str, Any]:
    day_dir = native_root() / "data" / "market_capture" / day
    sessions = sorted(day_dir.glob("session_*"))
    excluded: list[dict[str, Any]] = []
    candidates: list[tuple[int, Any, dict[str, Any]]] = []
    for sid in sessions:
        seal_p = sid / "seal.json"
        if not seal_p.is_file():
            st = {}
            if (sid / "status.json").is_file():
                st = read_json(sid / "status.json")
            excluded.append(
                {
                    "session": sid.name,
                    "reason": "INCOMPLETE_NO_SEAL",
                    "status": st.get("state") or st.get("status"),
                }
            )
            continue
        seal = read_json(seal_p)
        comp = seal.get("completeness") or {}
        raw = int(seal.get("raw_rows") or 0)
        # Prefer sealed PARTIAL preferred session for 27; else highest raw with seal present
        prio = 0
        if day == "20260727" and sid.name == DAY27_PREFERRED_SESSION:
            prio = 1_000_000_000 + raw
        elif comp.get("status") == "COMPLETE_CAPTURE" and seal.get("seal_pass"):
            prio = 100_000_000 + raw
        elif comp.get("research_windows_allowed"):
            prio = 10_000_000 + raw
        else:
            prio = raw
        candidates.append((prio, sid, seal))
    if not candidates:
        return _empty_meta(day, error="NO_SEALED_SESSION", excluded=excluded)
    candidates.sort(key=lambda x: (-x[0], x[1].name))
    _prio, sid, seal = candidates[0]
    for _p, other, _s in candidates[1:]:
        excluded.append({"session": other.name, "reason": "NOT_SELECTED_LOWER_PRIORITY"})
    comp = seal.get("completeness") or {}
    state = seal.get("state") or {}
    parts = sorted(p.name for p in sid.glob("push_part_*.jsonl"))
    first = parse_ts(seal.get("first_event_at") or comp.get("actual_first_event_at"))
    last = parse_ts(seal.get("last_event_at") or comp.get("actual_last_event_at"))
    return {
        "layout": "INGRESS_SESSION",
        "session_id": seal.get("ingress_session_id") or sid.name.replace("session_", ""),
        "session_dir": f"data/market_capture/{day}/{sid.name}",
        "session_folder": sid.name,
        "parts": parts,
        "seal_pass": bool(seal.get("seal_pass")),
        "capture_status": comp.get("status") or comp.get("label"),
        "raw_event_count": seal.get("raw_rows") if seal.get("raw_rows") is not None else "UNKNOWN",
        "symbols_seen_count": comp.get("registration_coverage", "UNKNOWN"),
        "duplicate_payload_count": comp.get("duplicate_key_count", "UNKNOWN"),
        "malformed_payload_count": "FIELD_ABSENT",
        "dropped_event_count": comp.get("dropped_event_count", "UNKNOWN"),
        "disconnect_count": comp.get("disconnect_count", "UNKNOWN"),
        "reconnect_count": comp.get("reconnect_success", "UNKNOWN"),
        "first_event_at": seal.get("first_event_at"),
        "last_event_at": seal.get("last_event_at"),
        "actual_submit": 0,
        "actual_cancel": 0,
        "first_dt": first,
        "last_dt": last,
        "source_priority": "INGRESS_SEALED_PREFERRED",
        "completeness_status": comp.get("status"),
        "entry_block_reason": state.get("entry_block_reason"),
        "research_adoptable": comp.get("research_adoptable"),
        "research_windows_allowed": comp.get("research_windows_allowed"),
        "coverage_am_flag": comp.get("coverage_am"),
        "coverage_pm_flag": comp.get("coverage_pm"),
        "largest_gap_sec": comp.get("largest_gap_sec"),
        "timestamp_regression_count": comp.get("timestamp_regression_count", "UNKNOWN"),
        "storage_errors": (seal.get("writer") or {}).get("storage_errors", "FIELD_ABSENT"),
        "excluded_sessions": excluded,
        "completeness_reasons": comp.get("reasons") or [],
    }


def _quality_class(
    day: str,
    meta: dict[str, Any],
    am_pm: str,
    *,
    has_usable_overlap: bool,
    coverage: Optional[float],
    valid_window: dict[str, Any],
) -> tuple[str, list[str], str]:
    """Return (quality_class, exclusion_reasons, research_label).

    INVALID_SOURCE when no usable overlap / empty valid_window / coverage==0.
    INVALID_SOURCE is excluded from ALL_USABLE. Do not invent UNKNOWN as 0.
    """
    reasons: list[str] = []
    if meta.get("session_id") is None:
        return "INVALID_SOURCE", ["NO_SESSION"], "INVALID"

    empty_vw = not (valid_window.get("start") and valid_window.get("end"))
    if (not has_usable_overlap) or empty_vw or coverage == 0.0:
        reasons.append("NO_USABLE_OVERLAP_OR_ZERO_COVERAGE")
        if day == "20260721" and am_pm == "AM":
            reasons.append("20260721_AM_INVALID_SOURCE")
        return "INVALID_SOURCE", reasons, "INVALID_SOURCE"

    lag = meta.get("entry_block_reason")
    seal_pass = bool(meta.get("seal_pass"))
    status = str(meta.get("capture_status") or "")

    if day == "20260728":
        reasons.append("EXCLUDED_LAG_RESYNC_CANONICAL")
        reasons.append("EXCLUDE_STRATEGY_PNL_DAYS")
        return "STRESS_RECOVERABLE", reasons, "STRESS_RECOVERABLE_EXCLUDED_FROM_CORE_BASE"

    if day in ("20260729", "20260730", "20260731"):
        if lag == "CONSUMER_LAG":
            reasons.append("CONSUMER_LAG")
            # Capture seal may be complete, but research quality is not USABLE_COMPLETE
            return "STRESS_RECOVERABLE", reasons, "COMPLETE_CAPTURE_WITH_LAG"
        if seal_pass and status == "COMPLETE_CAPTURE":
            return "CORE_VALID", reasons, "CORE_VALID"
        return "STRESS_RECOVERABLE", reasons, "STRESS_RECOVERABLE"

    if day == "20260727":
        reasons.append("PARTIAL_CAPTURE_seal_pass_false")
        reasons.append("prefer_sealed_PARTIAL_session")
        if am_pm == "AM":
            reasons.append("AM_coverage_incomplete")
        return "PARTIAL_VALID_WINDOW", reasons, "PARTIAL_VALID_WINDOW"

    # legacy 21-24: PARTIAL if usable overlap else INVALID (INVALID already handled above)
    reasons.append("LEGACY_PARTIAL_OR_BOUNDED_WINDOW")
    return "PARTIAL_VALID_WINDOW", reasons, "PARTIAL_VALID_WINDOW"


def _analysis_mask_id(
    *,
    day: str,
    am_pm: str,
    session_id: str,
    parts: list[str],
    valid_window: dict[str, Any],
    universe_sha: str,
    exclusion_rules: list[str],
    quality_class: str,
) -> str:
    payload = {
        "day": day,
        "am_pm": am_pm,
        "session_id": session_id,
        "parts": parts,
        "valid_window": valid_window,
        "universe_sha": universe_sha,
        "dedup_rule": DEDUP_KEY_RULE,
        "exclusion_rules": exclusion_rules,
        "quality_class": quality_class,
    }
    return sha256_obj(payload)


def _window_row(
    day: str,
    am_pm: str,
    meta: dict[str, Any],
    uni: dict[str, Any],
    *,
    banner: str,
) -> dict[str, Any]:
    expected = expected_window_iso(day, am_pm)
    first = meta.get("first_dt")
    last = meta.get("last_dt")
    # valid window = intersection of capture actual with expected paper window
    e0 = parse_ts(expected["start"])
    e1 = parse_ts(expected["end"])
    v0 = max(first, e0) if first and e0 else first
    v1 = min(last, e1) if last and e1 else last
    # If no overlap, still record empty valid window
    if v0 and v1 and v1 <= v0:
        v0, v1 = None, None
    cov = coverage_ratio(first, last, expected)
    valid_window = {
        "start": v0.isoformat() if v0 else None,
        "end": v1.isoformat() if v1 else None,
    }
    excl_extra: list[str] = []
    # For 7/27 AM with incomplete coverage, fixed mask uses continuous usable PM-heavy span
    if day == "20260727" and am_pm == "AM" and (meta.get("coverage_am_flag") is False):
        if first and e0 and e1 and first < e1 and (first <= e1):
            if first > e0:
                valid_window = {
                    "start": first.isoformat(),
                    "end": (min(last, e1).isoformat() if last else e1.isoformat()),
                }
                if parse_ts(valid_window["end"]) and parse_ts(valid_window["start"]):
                    if parse_ts(valid_window["end"]) <= parse_ts(valid_window["start"]):
                        valid_window = {"start": None, "end": None}
                        excl_extra.append("AM_NO_USABLE_CONTINUOUS")
    has_overlap = bool(valid_window.get("start") and valid_window.get("end"))
    qclass, excl, label = _quality_class(
        day,
        meta,
        am_pm,
        has_usable_overlap=has_overlap,
        coverage=cov,
        valid_window=valid_window,
    )
    if excl_extra:
        excl = list(excl) + excl_extra
    mask = _analysis_mask_id(
        day=day,
        am_pm=am_pm,
        session_id=str(meta.get("session_id") or ""),
        parts=list(meta.get("parts") or []),
        valid_window=valid_window,
        universe_sha=uni["universe_sha256"],
        exclusion_rules=excl,
        quality_class=qclass,
    )
    session_raw = meta.get("raw_event_count")
    return {
        "day": day,
        "am_pm": am_pm,
        "selected_session_id": meta.get("session_id"),
        "session_dir": meta.get("session_dir"),
        "layout": meta.get("layout"),
        "source_priority": meta.get("source_priority"),
        "parts": meta.get("parts") or [],
        "seal_pass": meta.get("seal_pass"),
        "capture_status": meta.get("capture_status"),
        "research_label": label,
        "quality_class": qclass,
        "exclusion_reasons": excl,
        "expected_window": expected,
        "actual_window": {
            "start": first.isoformat() if first else None,
            "end": last.isoformat() if last else None,
        },
        "valid_window": valid_window,
        "coverage": cov,
        "has_usable_overlap": has_overlap,
        # Do NOT duplicate full-day/session raw count into both AM and PM as window count
        "session_raw_event_count": session_raw,
        "window_raw_event_count": "UNKNOWN",
        "raw_event_count": "UNKNOWN",  # deprecated alias; use session_/window_ fields
        "normalized_event_count": "UNKNOWN",
        "symbols_seen_count": meta.get("symbols_seen_count"),
        "universe_symbols": uni["symbols"],
        "universe_count": uni["symbol_count"],
        "universe_sha256": uni["universe_sha256"],
        "universe_source": uni["universe_source"],
        "gap_largest_sec": meta.get("largest_gap_sec", "UNKNOWN"),
        "duplicate_count": meta.get("duplicate_payload_count"),
        "timestamp_regression_count": meta.get("timestamp_regression_count", "FIELD_ABSENT"),
        "decode_proxy_malformed_payload_count": meta.get("malformed_payload_count"),
        "storage_errors": meta.get("storage_errors", "FIELD_ABSENT"),
        "disconnect_count": meta.get("disconnect_count"),
        "reconnect_count": meta.get("reconnect_count"),
        "entry_block_reason": meta.get("entry_block_reason"),
        "lag_resync_recovery": {
            "entry_block_reason": meta.get("entry_block_reason"),
            "note": "CONSUMER_LAG => not USABLE_COMPLETE; seal_complete != research CORE_VALID",
        },
        "dedup_key_rule": DEDUP_KEY_RULE,
        "analysis_mask_id": mask,
        "actual_submit": meta.get("actual_submit"),
        "actual_cancel": meta.get("actual_cancel"),
        "excluded_sessions": meta.get("excluded_sessions") or [],
        "banner": banner,
        "include_in_core_base": qclass == "CORE_VALID",
        "seal_ok_for_f1_complete_claim": False
        if day == "20260727"
        else bool(meta.get("seal_pass") and meta.get("capture_status") == "COMPLETE_CAPTURE"),
    }


def build_source_manifest(
    *,
    banner: Optional[str] = None,
    status: Optional[str] = None,
    final: bool = False,
) -> dict[str, Any]:
    progress("P0: building source manifest")
    use_banner = banner or (FINAL_BANNER if final else PROVISIONAL_BANNER)
    use_status = status or ("P0_FINAL_COMPLETE" if final else "P0_COMPLETE_PROVISIONAL")
    windows: list[dict[str, Any]] = []
    days_out: dict[str, Any] = {}
    for day in DAYS:
        day_dir = native_root() / "data" / "market_capture" / day
        if not day_dir.is_dir():
            days_out[day] = {"error": "DAY_DIR_MISSING"}
            # Still emit INVALID window rows for audit completeness
            meta = _empty_meta(day, error="DAY_DIR_MISSING")
            uni = _universe_for_day(day)
            am_row = _window_row(day, "AM", meta, uni, banner=use_banner)
            pm_row = _window_row(day, "PM", meta, uni, banner=use_banner)
            windows.extend([am_row, pm_row])
            continue
        if any(day_dir.glob("session_*")):
            meta = _ingress_selected(day)
        elif (day_dir / "capture_summary.json").is_file():
            meta = _legacy_capture_meta(day)
        else:
            meta = _empty_meta(day, error="NO_CAPTURE_ARTIFACTS")
        uni = _universe_for_day(day)
        am_row = _window_row(day, "AM", meta, uni, banner=use_banner)
        pm_row = _window_row(day, "PM", meta, uni, banner=use_banner)
        # Drop empty-overlap windows from economic usable set but keep audit rows
        windows.append(am_row)
        windows.append(pm_row)
        days_out[day] = {
            "layout": meta.get("layout"),
            "selected_session_id": meta.get("session_id"),
            "excluded_sessions": meta.get("excluded_sessions") or [],
            "AM": {
                "quality_class": am_row["quality_class"],
                "analysis_mask_id": am_row["analysis_mask_id"],
                "has_usable_overlap": am_row["has_usable_overlap"],
            },
            "PM": {
                "quality_class": pm_row["quality_class"],
                "analysis_mask_id": pm_row["analysis_mask_id"],
                "has_usable_overlap": pm_row["has_usable_overlap"],
            },
            "universe_sha256": uni["universe_sha256"],
            "universe_count": uni["symbol_count"],
        }
    from research.e1_x6_provisional.quality_layers import window_quality_counts

    manifest = {
        "banner": use_banner,
        "phase": "P0",
        "status": use_status,
        "dedup_key_rule": DEDUP_KEY_RULE,
        "days": DAYS,
        "day_summary": days_out,
        "windows": windows,
        "quality_window_counts": window_quality_counts(windows),
        "notes": [
            "7/27 seal_pass=false PARTIAL_CAPTURE → PARTIAL_VALID_WINDOW; do not claim seal_ok COMPLETE for F1",
            "7/28–31: Capture seal complete ≠ research USABLE_COMPLETE when CONSUMER_LAG",
            "7/28 EXCLUDED_LAG_RESYNC from CORE economic BASE (STRESS_RECOVERABLE)",
            "has_usable_overlap=false OR empty valid_window OR coverage==0 → INVALID_SOURCE (esp 20260721 AM)",
            "INVALID_SOURCE excluded from ALL_USABLE",
            "CORE_VALID windows==0 => CORE_VALID metrics NOT_EVALUABLE (do not relabel PARTIAL as CORE)",
            "analysis_mask_id derived from stable JSON, not seal field",
            "session_raw_event_count is session-level; AM/PM must not duplicate it as window_raw_event_count",
            "raw/normalized counts: NEVER invent 0 if unknown → UNKNOWN",
        ],
    }
    manifest["source_manifest_sha256"] = sha256_obj(
        {k: v for k, v in manifest.items() if k != "source_manifest_sha256"}
    )
    return manifest
