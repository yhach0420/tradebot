"""Load frozen C1 precommit. Fail closed on hash or identity drift."""
from __future__ import annotations

import json
from typing import Any

from research.c1_multi_timeframe_full_strategy_v1 import (
    PRECOMMIT_ANALYSIS_ID,
    PRECOMMIT_NEXT_REQUIRED,
    PRECOMMIT_VERDICT_REQUIRED,
    REQUIRED_PRECOMMIT_HASHES,
)
from research.c1_multi_timeframe_full_strategy_v1.isolation import RESEARCH_ROOT
from research.c1_multi_timeframe_full_strategy_v1.spec import dumps_sha256, frozen_library
from research.c1_multi_timeframe_precommit_v1.analyze import decide as precommit_decide
from research.c1_multi_timeframe_precommit_v1 import RAW_CANDIDATE_IDS


def load_precommit_report() -> dict[str, Any]:
    path = RESEARCH_ROOT / "c1_multi_timeframe_precommit_v1" / "report.json"
    if not path.is_file():
        return {"ok": False, "blocker": "PRECOMMIT_REPORT_MISSING", "path": str(path)}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "blocker": f"PRECOMMIT_UNREADABLE:{type(exc).__name__}", "path": str(path)}
    if not isinstance(obj, dict):
        return {"ok": False, "blocker": "PRECOMMIT_NOT_OBJECT", "path": str(path)}
    d = dict(obj.get("decision") or {})
    a = dict(obj.get("answers") or {})
    def _int_field(*vals: Any, default: int = -1) -> int:
        for v in vals:
            if v is None or v is False:
                continue
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
        return int(default)

    counts = dict(d.get("counts") or {})
    hashes = dict(d.get("hashes") or a.get("62_all_hashes") or {})
    verdict = str(d.get("VERDICT") or a.get("63_VERDICT") or "")
    nxt = str(d.get("NEXT") or a.get("64_NEXT") or "")
    final_n = _int_field(counts.get("FINAL_CANDIDATE_N"), a.get("26_final_candidate_n"))
    dup_n = _int_field(counts.get("DUPLICATE_CANDIDATE_N"), a.get("23_duplicate_n"))
    closed_n = _int_field(counts.get("CLOSED_LINEAGE_CANDIDATE_N"), a.get("25_closed_lineage_n"))
    mismatch = [k for k, v in REQUIRED_PRECOMMIT_HASHES.items() if str(hashes.get(k) or "") != str(v)]
    live = precommit_decide()
    live_hashes = dict(live.get("hashes") or {})
    live_mismatch = [k for k, v in REQUIRED_PRECOMMIT_HASHES.items() if str(live_hashes.get(k) or "") != str(v)]
    lib = frozen_library()
    lib_sha = dumps_sha256(lib)
    ok = (
        str(obj.get("ANALYSIS_ID") or "") == PRECOMMIT_ANALYSIS_ID
        and verdict == PRECOMMIT_VERDICT_REQUIRED
        and nxt == PRECOMMIT_NEXT_REQUIRED
        and final_n == 10
        and dup_n == 0
        and closed_n == 0
        and a.get("5_C1_broadens_ENTRY_population_claim") is False
        and a.get("18_same_family_only") is True
        and a.get("19_cross_family_grid_used") is False
        and not mismatch
        and not live_mismatch
        and lib_sha == REQUIRED_PRECOMMIT_HASHES["FINAL_CANDIDATE_LIBRARY_SHA256"]
        and [r["CANDIDATE_ID"] for r in lib] == list(RAW_CANDIDATE_IDS)
    )
    failed = []
    if str(obj.get("ANALYSIS_ID") or "") != PRECOMMIT_ANALYSIS_ID:
        failed.append("ANALYSIS_ID")
    if verdict != PRECOMMIT_VERDICT_REQUIRED:
        failed.append("VERDICT")
    if nxt != PRECOMMIT_NEXT_REQUIRED:
        failed.append("NEXT")
    if final_n != 10:
        failed.append("FINAL_CANDIDATE_N")
    if dup_n != 0:
        failed.append("DUPLICATE_N")
    if closed_n != 0:
        failed.append("CLOSED_LINEAGE_N")
    if mismatch:
        failed.append("REPORT_HASH:" + ",".join(mismatch))
    if live_mismatch:
        failed.append("LIVE_HASH:" + ",".join(live_mismatch))
    if lib_sha != REQUIRED_PRECOMMIT_HASHES["FINAL_CANDIDATE_LIBRARY_SHA256"]:
        failed.append("LIVE_LIBRARY_HASH")
    return {
        "ok": bool(ok),
        "blocker": None if ok else "PRECOMMIT_IDENTITY_MISMATCH:" + ",".join(failed),
        "failed": failed,
        "path": str(path),
        "VERDICT": verdict,
        "NEXT": nxt,
        "FINAL_CANDIDATE_N": final_n,
        "DUPLICATE_N": dup_n,
        "CLOSED_LINEAGE_N": closed_n,
        "hashes_match": not mismatch and not live_mismatch,
        "report_hash_mismatch": mismatch,
        "live_hash_mismatch": live_mismatch,
        "C1_BROADENS_ENTRY_POPULATION_CLAIM": a.get("5_C1_broadens_ENTRY_population_claim"),
        "SAME_FAMILY_ONLY": a.get("18_same_family_only"),
        "CROSS_FAMILY_GRID": a.get("19_cross_family_grid_used"),
        "hashes": hashes,
    }
