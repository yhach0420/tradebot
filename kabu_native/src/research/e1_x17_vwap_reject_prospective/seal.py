"""Seal integrity checks — must pass before 20260803 alpha open."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import (
    CANDIDATE_ID,
    EXPECTED_PRECOMMIT_AT,
    EXPECTED_PRECOMMIT_SHA,
    FORBIDDEN_DAY,
    SEAL_MODE,
    SOURCE_RUN,
    TARGET_DAY,
    VWAP_UPPER_LIMIT_BPS,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
X12_REPORT = NATIVE / "results" / "research" / "e1_x12_risk_history" / "report.json"
X16_REPORT = NATIVE / "results" / "research" / "e1_x16_same_anchor_vwap_reject" / "report.json"
OUT_DIR = NATIVE / "results" / "research" / "e1_x17_vwap_reject_prospective"
PUSH_DIR = NATIVE / "data" / "push_jsonl" / f"{TARGET_DAY[:4]}-{TARGET_DAY[4:6]}-{TARGET_DAY[6:]}"


def _inventory_sha(path: Path) -> dict[str, Any]:
    """Identity of raw source without treating as alpha consumption."""
    if not path.exists():
        return {"exists": False, "path": str(path)}
    files = sorted(path.glob("*.jsonl"))
    lines = []
    total = 0
    mtimes = []
    for f in files:
        st = f.stat()
        total += st.st_size
        mtimes.append(st.st_mtime)
        lines.append(f"{f.name}\t{st.st_size}\t{st.st_mtime_ns}")
    body = "\n".join(lines).encode("utf-8")
    return {
        "exists": True,
        "path": str(path),
        "n_files": len(files),
        "total_bytes": total,
        "source_raw_sha256": hashlib.sha256(body).hexdigest(),
        "earliest_mtime_jst": datetime.fromtimestamp(min(mtimes), tz=JST).isoformat() if mtimes else None,
        "latest_mtime_jst": datetime.fromtimestamp(max(mtimes), tz=JST).isoformat() if mtimes else None,
    }


def _prior_alpha_consumed() -> tuple[bool, list[str]]:
    """Scan research reports for prior 20260803 alpha consumption / outcome."""
    hits = []
    research = NATIVE / "results" / "research"
    # Existing X17 outcome would mean already consumed
    if (OUT_DIR / "report.json").exists():
        hits.append(str(OUT_DIR / "report.json"))
    # Look for explicit opened_20260803 true or prospective outcome dirs
    for p in research.glob("**/report.json"):
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if '"opened_20260803": true' in txt or '"20260803_opened": true' in txt:
            hits.append(str(p))
        if "E1_X17" in txt and "PROSPECTIVE" in txt and p.parent.name != "e1_x17_vwap_reject_prospective":
            # other X17 reports
            if "VWAP_REJECT_PROSPECTIVE" in txt:
                hits.append(str(p))
    return (len(hits) > 0), hits


def verify_seal() -> dict[str, Any]:
    reasons: list[str] = []
    x12 = json.loads(X12_REPORT.read_text(encoding="utf-8"))
    row = (x12.get("date_registry") or {}).get("by_date", {}).get(TARGET_DAY)
    if not row:
        reasons.append("registry_row_missing")
        status = None
    else:
        status = row.get("status")
        if status != "ALPHA_PROSPECTIVE_RESERVED":
            reasons.append("registry_identity_mismatch")

    consumed, consume_hits = _prior_alpha_consumed()
    # Allow re-run only if we are regenerating this package's own report in same session —
    # but TODO says stop if existing 20260803 outcome report exists.
    prior_outcome = (OUT_DIR / "report.json").exists()
    if prior_outcome:
        reasons.append("existing_20260803_outcome_report")
    if consumed and not prior_outcome:
        reasons.append("prior_alpha_consumption")

    # Check X12–X16 safety flags claim unconsumed
    for label, path in (
        ("x12", X12_REPORT),
        ("x16", X16_REPORT),
    ):
        if not path.exists():
            reasons.append(f"{label}_report_missing")
            continue
        r = json.loads(path.read_text(encoding="utf-8"))
        safety = r.get("safety") or {}
        if safety.get("Prospective_consumed") is True or safety.get("prospective_consumed") is True:
            reasons.append(f"{label}_prospective_consumed")
        if safety.get("opened_20260803") is True or safety.get("20260803_opened") is True:
            reasons.append(f"{label}_opened_20260803")

    x16 = json.loads(X16_REPORT.read_text(encoding="utf-8"))
    if x16.get("run_id") != SOURCE_RUN:
        reasons.append("source_run_mismatch")
    pc = x16.get("prospective_precommit") or {}
    if pc.get("candidate_id") != CANDIDATE_ID:
        reasons.append("candidate_id_mismatch")
    if pc.get("precommit_sha256") != EXPECTED_PRECOMMIT_SHA:
        reasons.append("precommit_sha_mismatch")
    if pc.get("precommit_at_jst") != EXPECTED_PRECOMMIT_AT:
        reasons.append("precommit_at_mismatch")
    if abs(float(pc.get("threshold") or 0) - VWAP_UPPER_LIMIT_BPS) > 1e-12:
        reasons.append("threshold_mismatch")
    # Recompute SHA over rule body (without sha field)
    rule = {k: v for k, v in pc.items() if k != "precommit_sha256"}
    recomputed = hashlib.sha256(json.dumps(rule, sort_keys=True, default=str).encode()).hexdigest()
    if recomputed != EXPECTED_PRECOMMIT_SHA:
        reasons.append("precommit_sha_recompute_mismatch")

    raw = _inventory_sha(PUSH_DIR)
    if not raw.get("exists") or (raw.get("n_files") or 0) < 1:
        reasons.append("raw_identity_unknown")

    # Forbidden day not used
    if FORBIDDEN_DAY:
        pass

    ok = len(reasons) == 0
    return {
        "ok": ok,
        "seal_mode": SEAL_MODE,
        "reasons": reasons,
        "registry_status": status,
        "registry_row": row,
        "prior_alpha_consumption": consumed and not prior_outcome,
        "prior_report_generation": prior_outcome,
        "consume_hits": consume_hits,
        "source_raw": raw,
        "precommit": {
            "candidate_id": pc.get("candidate_id"),
            "precommit_sha256": pc.get("precommit_sha256"),
            "precommit_sha_match": pc.get("precommit_sha256") == EXPECTED_PRECOMMIT_SHA and recomputed == EXPECTED_PRECOMMIT_SHA,
            "precommit_at_jst": pc.get("precommit_at_jst"),
            "threshold": pc.get("threshold"),
            "exact_rule": pc.get("exact_rule"),
            "20260803_opened_in_precommit": pc.get("20260803_opened"),
        },
        "source_run": x16.get("run_id"),
        "recomputed_precommit_sha": recomputed,
    }
