"""Read-only rolling harvester for the frozen prospective V1 protocol. Does not change spec/hash."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
os.environ["OMP_NUM_THREADS"] = "1"

from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_timing_robustness.grid import hm_epoch
from research.simple_tech_entry_family.harvest import load_day_cache, sealed_day_caps
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.branch_u_holdout_harvest import _am_complete, _parse_iso, inspect_capture_day
from research.am_c0_indicator_exit.isolation import advanced
from research.simple_tech_redesign.isolation import (
    PRECAP_PROSPECTIVE_V1_OUT,
    PRECAP_TIMING_RCA_OUT,
    RESEARCH_CACHE,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_analyze import decide, evaluate
from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_harvest import DAY_CACHE, harvest_day
from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.precap_marginal_quality_prospective_v1_spec import (
    ANALYSIS_ID,
    CANDIDATE_FROZEN,
    FIRST_ELIGIBLE_DATE,
    FORBIDDEN_INPUT_DAYS,
    INTRINSIC_MECHANISM_CONFIRMED,
    MIN_BLOCKED_HYP_N,
    OBSERVED_ECONOMIC_BOTTLENECK,
    OBSERVATION_DAY1_LABEL,
    OUTLIER_SYMBOL,
    PRIMARY_MECHANISM_FROZEN,
    PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN,
    WINDOW_DAYS,
    source_sha256_prospective,
    spec_sha256_prospective,
)
from small_paper.v1r_live_dual_lane import session_end_for_position

JST = ZoneInfo("Asia/Tokyo")
FROZEN_SPEC_SHA256 = "1fbb3955daf359a7f297b12ebe958e5e6c19b15c67d213506f4c524c91614681"
FROZEN_SOURCE_SHA256 = "68e00d578a78e587c27cdb7822a76a77108d78b8d28d654468ab2a763d99baa4"
DIGEST_PATH = DAY_CACHE / "day_digests.json"
STATUS_NOT_SEALED = "NOT_READY_SESSION_NOT_SEALED"
STATUS_DAY_MUTATED = "FAIL_CLOSED_PROSPECTIVE_DAY_MUTATED"
STATUS_FORBIDDEN = "FAIL_CLOSED_FORBIDDEN_INPUT"
OBSERVATION_PROTOCOL_ARMED = True
STRATEGY_CANDIDATE_ARMED = False

assert ANALYSIS_ID == "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_PROSPECTIVE_V1"
assert spec_sha256_prospective() == FROZEN_SPEC_SHA256
assert source_sha256_prospective() == FROZEN_SOURCE_SHA256
assert PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN is True
assert CANDIDATE_FROZEN is False
assert INTRINSIC_MECHANISM_CONFIRMED is False
assert OBSERVED_ECONOMIC_BOTTLENECK == "MARGINAL_ENTRY_QUALITY"
assert PRIMARY_MECHANISM_FROZEN == "OUTLIER_DOMINATED"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _norm(path: str) -> str:
    return str(path or "").replace("\\", "/")


def _iso_day(iso: str) -> Optional[str]:
    t = _parse_iso(iso)
    if t is None:
        return None
    return datetime.fromtimestamp(float(t), tz=JST).strftime("%Y%m%d")


def frozen_identity() -> dict[str, Any]:
    report = _load(PRECAP_PROSPECTIVE_V1_OUT / "report.json")
    req = dict(report.get("required") or {})
    checks = {
        "ANALYSIS_ID": ANALYSIS_ID == "SIMPLE_TECH_PRECAP_MARGINAL_QUALITY_PROSPECTIVE_V1",
        "spec_sha256": spec_sha256_prospective() == FROZEN_SPEC_SHA256 == str(report.get("spec_sha256") or ""),
        "source_sha256": source_sha256_prospective() == FROZEN_SOURCE_SHA256 == str(report.get("source_sha256") or ""),
        "PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN": PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN is True
        and bool(req.get("PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN") is True),
        "CANDIDATE_FROZEN": CANDIDATE_FROZEN is False and req.get("CANDIDATE_FROZEN") is False,
        "INTRINSIC_MECHANISM_CONFIRMED": INTRINSIC_MECHANISM_CONFIRMED is False
        and req.get("INTRINSIC_MECHANISM_CONFIRMED") is False,
        "OBSERVED_ECONOMIC_BOTTLENECK": OBSERVED_ECONOMIC_BOTTLENECK == "MARGINAL_ENTRY_QUALITY"
        and str(req.get("OBSERVED_ECONOMIC_BOTTLENECK") or "") == "MARGINAL_ENTRY_QUALITY",
        "PRIMARY_MECHANISM_FROZEN": PRIMARY_MECHANISM_FROZEN == "OUTLIER_DOMINATED"
        and str(req.get("PRIMARY_MECHANISM_FROZEN") or "") == "OUTLIER_DOMINATED",
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "spec_sha256": FROZEN_SPEC_SHA256,
        "source_sha256": FROZEN_SOURCE_SHA256,
        "ANALYSIS_ID": ANALYSIS_ID,
    }


def runtime_arming_status() -> dict[str, Any]:
    return {
        "prospective_armed_means": "OBSERVATION PROTOCOL ARMED only; not a strategy candidate",
        "OBSERVATION_PROTOCOL_ARMED": True,
        "STRATEGY_CANDIDATE_ARMED": False,
        "CANDIDATE_FROZEN": False,
        "first_eligible_date": FIRST_ELIGIBLE_DATE,
        "observation_day1_label": OBSERVATION_DAY1_LABEL,
        "observation_day1_is_floor_break_day1": False,
        "observation_day1_is_strategy_validation_day1": False,
    }


def questions_display(*, blocked_n: int, questions: dict[str, Any] | None) -> dict[str, Any]:
    evaluable = int(blocked_n) >= int(MIN_BLOCKED_HYP_N)
    human = {}
    for k in (
        "Q1_future_blocked_weaker",
        "Q2_weakness_on_multiple_days",
        "Q3_single_day_explains",
        "Q4_single_symbol_explains",
        "Q5_one_role_only",
        "Q6_late_arrival_only",
        "Q7_285A_type_outlier_reproduced",
    ):
        if not evaluable:
            human[k] = "NOT_EVALUABLE"
        else:
            human[k] = "TRUE" if bool((questions or {}).get(k)) else "FALSE"
    return {
        "questions_evaluable": evaluable,
        "questions_status": human,
        "status_class": None if evaluable else "NOT_EVALUABLE_INSUFFICIENT",
        "canonical_booleans_unchanged": True,
    }


def prove_sealed_am_session(
    day: str,
    *,
    today: str = TODAY,
    active_capture_path: str = "",
    active_paper_session: str = "",
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "date": str(day),
        "ok": False,
        "status": STATUS_NOT_SEALED,
        "trading_date_match": False,
        "am_complete": False,
        "capture_path": "",
        "capture_not_active_write": False,
        "session_close_boundary_ok": False,
        "source_input_that_day_only": False,
        "observation_label": OBSERVATION_DAY1_LABEL if str(day) == FIRST_ELIGIBLE_DATE else None,
    }
    if str(day) in FORBIDDEN_INPUT_DAYS:
        rec["status"] = STATUS_FORBIDDEN
        rec["reason"] = "FORBIDDEN_INPUT"
        return rec
    if str(day) == str(today) or str(day) > str(today):
        rec["reason"] = "ACTIVE_OR_FUTURE"
        return rec
    insp = inspect_capture_day(str(day), today=str(today))
    rec["capture_path"] = str(insp.get("capture_path") or "")
    rec["first_event_at"] = insp.get("first_event_at")
    rec["last_event_at"] = insp.get("last_event_at")
    rec["capture_bytes"] = insp.get("capture_bytes")
    if not insp.get("complete"):
        rec["reason"] = str(insp.get("reason") or STATUS_NOT_SEALED)
        return rec
    cap_path = _norm(rec["capture_path"])
    act = _norm(active_capture_path)
    if not cap_path:
        rec["reason"] = "CAPTURE_PATH_UNRESOLVED"
        return rec
    if str(today) in cap_path or (act and (cap_path == act or cap_path.startswith(act.rstrip("/") + "/"))):
        rec["reason"] = "ACTIVE_CAPTURE"
        return rec
    if str(day) not in cap_path:
        rec["reason"] = "CAPTURE_PATH_DAY_MISMATCH"
        return rec
    rec["capture_not_active_write"] = True
    first_day = _iso_day(str(insp.get("first_event_at") or ""))
    last_day = _iso_day(str(insp.get("last_event_at") or ""))
    rec["trading_date_match"] = first_day == str(day) and last_day == str(day)
    if not rec["trading_date_match"]:
        rec["reason"] = "TRADING_DATE_MISMATCH"
        return rec
    am_start = float(hm_epoch(str(day), 9, 0))
    am_end = float(session_end_for_position(date=str(day), session="AM", fill_time=am_start + 60.0))
    rec["am_open"] = am_start
    rec["am_end"] = am_end
    rec["am_complete"] = bool(_am_complete(str(day), str(insp.get("last_event_at") or "")))
    rec["session_close_boundary_ok"] = bool(rec["am_complete"] and am_end > am_start)
    if not rec["session_close_boundary_ok"]:
        rec["reason"] = "AM_SESSION_INCOMPLETE"
        return rec
    active_n = input_active_file_n([rec["capture_path"]], active_capture_path, active_paper_session, today)
    rec["source_input_that_day_only"] = int(active_n) == 0 and all(
        tok not in cap_path for tok in FORBIDDEN_INPUT_DAYS if tok != str(day)
    )
    if not rec["source_input_that_day_only"]:
        rec["reason"] = "SOURCE_INPUT_NOT_THAT_DAY"
        return rec
    caps = sealed_day_caps([str(day)], str(today))
    cap = dict(caps[0] if caps else {})
    if not cap.get("ok"):
        rec["reason"] = "UNIVERSE_INCOMPLETE"
        return rec
    rec["ok"] = True
    rec["status"] = "SEALED"
    rec["reason"] = "COMPLETE_SEALED_AM"
    rec["capture_path"] = str(cap.get("capture_path") or rec["capture_path"])
    rec["universe_symbols"] = list(cap.get("universe_symbols") or [])
    rec["universe_n"] = int(cap.get("universe_n") or 0)
    rec["universe_source"] = cap.get("universe_source")
    return rec


def _ids(rows: list[dict[str, Any]], flag: str) -> list[str]:
    return sorted(str(r.get("trade_id") or "") for r in rows if r.get(flag))


def _pnl_pairs(rows: list[dict[str, Any]], flag: str) -> list[list[Any]]:
    out = []
    for r in rows:
        if not r.get(flag):
            continue
        out.append([str(r.get("trade_id") or ""), r.get("session_close_pnl")])
    return sorted(out, key=lambda x: str(x[0]))


def day_identity_payload(body: dict[str, Any], *, capture_path: str) -> dict[str, Any]:
    cands = list(body.get("candidates") or [])
    return json_sanitize(
        {
            "date": str(body.get("date") or ""),
            "spec_sha": str(body.get("spec_sha") or ""),
            "capture_path": _norm(capture_path),
            "executable_n": int(body.get("executable_n") or 0),
            "admitted_n": int(body.get("admitted_n") or 0),
            "hyp_n": int(body.get("hyp_n") or 0),
            "cap_only_n": int((body.get("cap_inventory") or {}).get("cap_only_n") or 0),
            "admitted_ids": _ids(cands, "control_admitted"),
            "cap_only_ids": _ids(cands, "cap_only_blocked"),
            "hyp_ids": _ids(cands, "hypothetical_fill"),
            "admitted_pnl": _pnl_pairs(cands, "control_admitted"),
            "hyp_pnl": _pnl_pairs(cands, "hypothetical_fill"),
        }
    )


def digest_sha(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def load_digests() -> dict[str, Any]:
    body = _load(DIGEST_PATH)
    days = dict(body.get("days") or {})
    return days


def save_digest(day: str, payload: dict[str, Any], audit: dict[str, Any]) -> None:
    DAY_CACHE.mkdir(parents=True, exist_ok=True)
    store = _load(DIGEST_PATH)
    days = dict(store.get("days") or {})
    days[str(day)] = {"digest_sha256": digest_sha(payload), "payload": payload, "audit": audit}
    store["days"] = days
    store["spec_sha256"] = FROZEN_SPEC_SHA256
    DIGEST_PATH.write_text(json.dumps(json_sanitize(store), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def assert_day_immutable(day: str, payload: dict[str, Any]) -> None:
    prev = load_digests().get(str(day))
    if not prev:
        return
    got = digest_sha(payload)
    exp = str(prev.get("digest_sha256") or "")
    if got != exp:
        raise RuntimeError(f"{STATUS_DAY_MUTATED}:{day}")


def day_audit(body: dict[str, Any], proof: dict[str, Any]) -> dict[str, Any]:
    from research.simple_tech_redesign.slot_release_marginal_admission_quality_rca_analyze import quality_pack

    cands = list(body.get("candidates") or [])
    admitted = [c for c in cands if c.get("control_admitted") and c.get("session_close_pnl") is not None]
    blocked = [c for c in cands if c.get("hypothetical_fill") and c.get("session_close_pnl") is not None]
    cap_only = [c for c in cands if c.get("cap_only_blocked")]
    adm_p = quality_pack([float(c["session_close_pnl"]) for c in admitted])
    blk_p = quality_pack([float(c["session_close_pnl"]) for c in blocked])
    by_sym: dict[str, float] = {}
    for c in blocked:
        by_sym[str(c.get("symbol") or "")] = by_sym.get(str(c.get("symbol") or ""), 0.0) + float(c.get("session_close_pnl") or 0.0)
    top_sym = max(by_sym, key=lambda k: abs(by_sym[k])) if by_sym else None
    abs_den = sum(abs(v) for v in by_sym.values())
    rows_285 = [c for c in cands if str(c.get("symbol") or "") == OUTLIER_SYMBOL]
    def _t3_note(c: dict[str, Any], key: str) -> Any:
        if c.get(key) is not None:
            return c.get(key)
        return "MISSING_NOT_CAPTURED"

    t3_sample = {}
    if cands:
        c0 = cands[0]
        t3_sample = {
            "EMA9": _t3_note(c0, "t3_ema9"),
            "EMA21": _t3_note(c0, "t3_ema21"),
            "EMA21_lag3_slope_input": _t3_note(c0, "t3_ema21_lag3"),
            "RCI9_current": _t3_note(c0, "t3_rci9"),
            "RCI9_previous": _t3_note(c0, "t3_rci9_prev"),
            "BB_LOWER": _t3_note(c0, "t3_bb_lower"),
            "Close": _t3_note(c0, "t3_close"),
            "ema_gap_bps": _t3_note(c0, "t3_ema_gap_bps"),
            "close_ema9_bps": _t3_note(c0, "t3_close_ema9_bps"),
            "P2_bar0": {"low": _t3_note(c0, "p2_bar0_low"), "close": _t3_note(c0, "p2_bar0_close")},
            "P2_bar1": {"low": _t3_note(c0, "p2_bar1_low"), "close": _t3_note(c0, "p2_bar1_close")},
            "P2_bar2": {"low": _t3_note(c0, "p2_bar2_low"), "close": _t3_note(c0, "p2_bar2_close")},
            "SETUP_LOW": _t3_note(c0, "p2_setup_low"),
            "SETUP_HIGH": _t3_note(c0, "p2_setup_high"),
            "new_runtime_instrumentation": False,
        }
    return {
        "date": body.get("date"),
        "capture_path": proof.get("capture_path"),
        "session_identity": {
            "first_event_at": proof.get("first_event_at"),
            "last_event_at": proof.get("last_event_at"),
            "am_end": proof.get("am_end"),
            "status": proof.get("status"),
        },
        "control_candidate_n": int(body.get("executable_n") or 0),
        "control_admitted_n": int(body.get("admitted_n") or 0),
        "cap_only_blocked_n": int((body.get("cap_inventory") or {}).get("cap_only_n") or 0),
        "cap_only_execution_evaluable_n": len(cap_only),
        "hypothetical_fill_n": int(body.get("hyp_n") or 0),
        "CORE_admitted_n": sum(1 for c in admitted if str(c.get("fill_role") or "") == "CORE"),
        "ADDED_admitted_n": sum(1 for c in admitted if str(c.get("fill_role") or "") == "ADDED"),
        "CORE_blocked_n": sum(1 for c in blocked if str(c.get("fill_role") or "") == "CORE"),
        "ADDED_blocked_n": sum(1 for c in blocked if str(c.get("fill_role") or "") == "ADDED"),
        "admitted": adm_p,
        "blocked": blk_p,
        "arrival_rank_admitted": [c.get("candidate_arrival_rank") for c in admitted],
        "seconds_from_open_admitted": [c.get("seconds_from_session_open") for c in admitted],
        "seconds_to_close_admitted": [c.get("seconds_to_session_close") for c in admitted],
        "active_positions": [c.get("active_positions") for c in cands[:20]],
        "free_slots": [c.get("free_slots") for c in cands[:20]],
        "top_symbol": top_sym,
        "top_symbol_pnl": by_sym.get(top_sym) if top_sym else None,
        "top_symbol_share_abs": (abs(by_sym[top_sym]) / abs_den) if top_sym and abs_den else None,
        "symbol_285A": {
            "present": bool(rows_285),
            "admitted": any(c.get("control_admitted") for c in rows_285),
            "blocked": any(c.get("cap_only_blocked") for c in rows_285),
            "hyp_fill": any(c.get("hypothetical_fill") for c in rows_285),
            "pnl": float(sum(float(c.get("session_close_pnl") or 0.0) for c in rows_285 if c.get("session_close_pnl") is not None)),
        },
        "t3_capture_sample": t3_sample,
        "feature_discovery": False,
    }


def capture_day_input_n(paths: list[str], day: str) -> int:
    n = 0
    for raw in paths:
        ps = _norm(raw)
        if f"market_capture/{day}/" in ps or ps.endswith(f"market_capture/{day}") or f"/{day}/session" in ps:
            n += 1
    return n


def _window_and_extensions(*, today: str, harvested: list[str], blocked_n: int) -> dict[str, Any]:
    original = list(WINDOW_DAYS)
    ext: list[str] = []
    if int(blocked_n) < int(MIN_BLOCKED_HYP_N) and str(today) > original[-1]:
        from research.simple_tech_redesign.branch_u_holdout_harvest import list_capture_days_after

        for d in list_capture_days_after(original[-1]):
            if d in FORBIDDEN_INPUT_DAYS or d == str(today) or d > str(today):
                continue
            if d not in harvested:
                ext.append(d)
    return {
        "window_original": original,
        "window_original_span": f"{original[0]}-{original[-1]}",
        "extension_days": ext,
        "window_not_rewritten": True,
    }


def _count_inputs(paths: list[str], pre: dict[str, Any]) -> dict[str, Any]:
    return {
        "active_20260904_input_n": input_active_file_n(
            paths, str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""), TODAY
        )
        + capture_day_input_n(paths, "20260904"),
        "input_20260903_n": capture_day_input_n(paths, "20260903"),
    }


def run_harvester(*, requested_days: list[str] | None = None, write_artifacts_enabled: bool | None = None) -> dict[str, Any]:
    set_research_priority_below_normal()
    ident = frozen_identity()
    if not ident.get("ok"):
        return {"ok": False, "status": "FAIL_CLOSED_FROZEN_IDENTITY", "identity": ident}
    if requested_days:
        bad = [d for d in requested_days if d in FORBIDDEN_INPUT_DAYS]
        if bad:
            return {"ok": False, "status": STATUS_FORBIDDEN, "days": bad, "identity": ident}
    pre = snapshot(phase="PRE")
    leak = {
        "SUBMIT_N": int(SUBMIT_N),
        "CANCEL_N": int(CANCEL_N),
        "LIVE_ORDER_N": int(LIVE_ORDER_N),
        "RESEARCH_WRITE_PATH_OVERLAP_N": write_overlap_n(
            str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
        ),
    }
    input_paths = [
        str(PRECAP_PROSPECTIVE_V1_OUT / "report.json"),
        str(PRECAP_TIMING_RCA_OUT / "report.json"),
    ]
    counts = _count_inputs(input_paths, pre)
    leak["ACTIVE_CAPTURE_INPUT_N"] = int(counts["active_20260904_input_n"])
    leak["INPUT_20260903_N"] = int(counts["input_20260903_n"])
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]) or int(leak["ACTIVE_CAPTURE_INPUT_N"]) or int(leak["INPUT_20260903_N"]):
        post = snapshot(phase="POST")
        return {
            "ok": False,
            "status": "FAIL_CLOSED_ACTIVE_OR_FORBIDDEN_INPUT",
            "leak": leak,
            "counts": counts,
            "identity": ident,
            "arming": runtime_arming_status(),
            "pid": advanced(pre, post),
        }

    proofs: list[dict[str, Any]] = []
    usable: list[dict[str, Any]] = []
    not_ready: list[dict[str, Any]] = []
    target_days = list(requested_days) if requested_days else list(WINDOW_DAYS)
    for day in target_days:
        if day in FORBIDDEN_INPUT_DAYS:
            return {"ok": False, "status": STATUS_FORBIDDEN, "date": day, "identity": ident}
        proof = prove_sealed_am_session(
            day,
            today=TODAY,
            active_capture_path=str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
            active_paper_session=str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        )
        proofs.append(proof)
        if proof.get("ok"):
            usable.append(proof)
        elif proof.get("status") == STATUS_FORBIDDEN:
            return {"ok": False, "status": STATUS_FORBIDDEN, "proof": proof, "identity": ident}
        else:
            not_ready.append(proof)

    prior = load_digests()
    harvested_days = sorted(prior.keys())
    new_days = [p for p in usable if str(p.get("date") or "") not in prior]
    bodies: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    sha = FROZEN_SPEC_SHA256
    try:
        for day in harvested_days:
            cached = load_day_cache(DAY_CACHE / f"day_{day}.json", sha)
            if not cached or not cached.get("ok"):
                raise RuntimeError(f"{STATUS_DAY_MUTATED}:{day}:cache_missing")
            cap_path = str((prior.get(day) or {}).get("payload", {}).get("capture_path") or "")
            payload = day_identity_payload(cached, capture_path=cap_path)
            assert_day_immutable(day, payload)
            bodies.append(cached)
            audits.append(dict((prior.get(day) or {}).get("audit") or {"date": day}))
        for proof in new_days:
            cap = {
                "date": proof["date"],
                "ok": True,
                "capture_path": proof.get("capture_path"),
                "universe_symbols": list(proof.get("universe_symbols") or []),
            }
            input_paths.append(str(proof.get("capture_path") or ""))
            if capture_day_input_n([str(proof.get("capture_path") or "")], "20260903") or capture_day_input_n(
                [str(proof.get("capture_path") or "")], "20260904"
            ):
                return {"ok": False, "status": STATUS_FORBIDDEN, "proof": proof}
            body = harvest_day(cap, spec_sha=sha, today=TODAY)
            if not body.get("ok"):
                return {"ok": False, "status": f"HARVEST_FAIL:{body.get('blocker')}", "body": {"date": body.get("date"), "blocker": body.get("blocker")}}
            payload = day_identity_payload(body, capture_path=str(proof.get("capture_path") or ""))
            assert_day_immutable(str(proof["date"]), payload)
            audit = day_audit(body, proof)
            save_digest(str(proof["date"]), payload, audit)
            bodies.append(body)
            audits.append(audit)
    except RuntimeError as exc:
        post = snapshot(phase="POST")
        return {
            "ok": False,
            "status": str(exc),
            "identity": ident,
            "pid": advanced(pre, post),
        }

    pack = evaluate(bodies)
    blocked_n = int(pack.get("blocked_n") or 0)
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in ("SUBMIT_N", "CANCEL_N", "LIVE_ORDER_N", "RESEARCH_WRITE_PATH_OVERLAP_N", "ACTIVE_CAPTURE_INPUT_N", "INPUT_20260903_N"))
    discovery = {
        "completed_days": sorted({str(b.get("date") or "") for b in bodies}),
        "window_days": list(WINDOW_DAYS),
    }
    decision = decide(pack, leak_ok=leak_ok, discovery=discovery)
    qdisp = questions_display(blocked_n=blocked_n, questions=dict(decision.get("questions") or {}))
    win = _window_and_extensions(today=TODAY, harvested=discovery["completed_days"], blocked_n=blocked_n)
    should_write = bool(new_days) if write_artifacts_enabled is None else bool(write_artifacts_enabled)
    post = snapshot(phase="POST")
    pid = advanced(pre, post)
    counts = _count_inputs(input_paths, pre)
    status = {
        "ok": True,
        "status": "DRY_RUN_NO_SEALED_DAYS" if not bodies else ("ROLLING_UPDATED" if should_write else "EVALUATED_NO_WRITE"),
        "verdict": decision.get("VERDICT"),
        "CASE": decision.get("CASE"),
        "blocked_hyp_n": blocked_n,
        "admitted_n": pack.get("admitted_n"),
        "eligible_sealed_prospective_days": len(usable),
        "completed_days": discovery["completed_days"],
        "new_days": [p.get("date") for p in new_days],
        "not_ready": [{"date": r.get("date"), "reason": r.get("reason"), "status": r.get("status")} for r in not_ready],
        "proofs": [{"date": p.get("date"), "status": p.get("status"), "reason": p.get("reason")} for p in proofs],
        "identity": ident,
        "arming": runtime_arming_status(),
        "questions_overlay": qdisp,
        "window": win,
        "day_audits": audits,
        "leak": leak,
        "counts": counts,
        "pid": pid,
        "submit_cancel_live": [int(SUBMIT_N), int(CANCEL_N), int(LIVE_ORDER_N)],
        "command_after_20260907_sealed": "python -m research.simple_tech_redesign.precap_marginal_quality_prospective_harvester",
        "artifacts_written": False,
        "SEALED_gate": True,
        "active_capture_protection": True,
        "immutable_day_digest": True,
        "rolling_ready": True,
        "minimum_n_gate": int(MIN_BLOCKED_HYP_N),
    }
    if not bodies:
        status["status"] = STATUS_NOT_SEALED if any(r.get("status") == STATUS_NOT_SEALED for r in not_ready) else "DRY_RUN_NO_SEALED_DAYS"
        post2 = snapshot(phase="POST")
        status["pid"] = advanced(pre, post2)
        return status
    if should_write:
        slim = {k: v for k, v in pack.items() if k not in {"candidates", "admitted", "blocked"}}
        report = {
            "analysis_id": ANALYSIS_ID,
            "spec_sha256": FROZEN_SPEC_SHA256,
            "source_sha256": FROZEN_SOURCE_SHA256,
            "required": {
                "ANALYSIS_ID": ANALYSIS_ID,
                "VERDICT": decision.get("VERDICT"),
                "CASE": decision.get("CASE"),
                "OBSERVED_ECONOMIC_BOTTLENECK": OBSERVED_ECONOMIC_BOTTLENECK,
                "INTRINSIC_MECHANISM_CONFIRMED": False,
                "PRIMARY_MECHANISM_FROZEN": PRIMARY_MECHANISM_FROZEN,
                "PROSPECTIVE_OBSERVATION_PROTOCOL_FROZEN": True,
                "CANDIDATE_FROZEN": False,
                "TRUE_OOS": False,
                "CERTIFIED": False,
                "first_eligible_prospective_date": FIRST_ELIGIBLE_DATE,
                "prospective_armed": True,
                "NEW_ENTRY_FILTER": False,
                "CAP_CHANGED": False,
                "NEW_EXIT_RULE": False,
            },
            "decision": decision,
            "evaluation": slim,
            "discovery": {**discovery, **win},
            "harvester_status": {
                "OBSERVATION_PROTOCOL_ARMED": True,
                "STRATEGY_CANDIDATE_ARMED": False,
                "questions_evaluable": qdisp["questions_evaluable"],
                "questions_status": qdisp["questions_status"],
                "status_class": qdisp["status_class"],
                "day_audits": audits,
                "window_original": win["window_original"],
                "extension_days": win["extension_days"],
            },
            "leak": leak,
            "preflight": pre,
            "postflight": post,
            "reporting_semantics": reporting_semantics(pre, post),
            "_markdown": "",
        }
        md = build_markdown(report)
        extra = [
            "",
            "## Harvester status (does not change frozen spec/hash)",
            f"OBSERVATION_PROTOCOL_ARMED=true STRATEGY_CANDIDATE_ARMED=false questions_evaluable={qdisp['questions_evaluable']}",
            f"Q status={qdisp['questions_status']}",
            "",
            "STOP.",
        ]
        if md.rstrip().endswith("STOP."):
            md = md.rstrip()[: -len("STOP.")] + "\n".join(extra) + "\n"
        else:
            md = md + "\n".join(extra) + "\n"
        report["_markdown"] = md
        sheets_src = {**report, "evaluation": {**slim, "candidates": list(pack.get("candidates") or [])}}
        write_artifacts(report, build_sheets(sheets_src))
        status["artifacts_written"] = True
        status["status"] = "ROLLING_UPDATED"
    return status


def main() -> int:
    args = [a for a in sys.argv[1:] if a]
    requested = None
    write_en = None
    if "--dry-run" in args:
        write_en = False
        args = [a for a in args if a != "--dry-run"]
    if args:
        requested = [a for a in args if a.isdigit() and len(a) == 8]
    print(
        f"HARVESTER {ANALYSIS_ID} spec={FROZEN_SPEC_SHA256[:12]} OBSERVATION_PROTOCOL_ARMED=true STRATEGY_CANDIDATE_ARMED=false today={TODAY}",
        flush=True,
    )
    out = run_harvester(requested_days=requested, write_artifacts_enabled=write_en)
    print(json.dumps(json_sanitize({k: v for k, v in out.items() if k != "day_audits"}), ensure_ascii=False, default=str), flush=True)
    if out.get("day_audits"):
        print(f"day_audits_n={len(out['day_audits'])}", flush=True)
    print("STOP.", flush=True)
    if not out.get("ok"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
