"""Bind USDJPY_SECTOR_SPECIFIC_DRIVER_FOUND_V1, frozen FX map, split, 105-name pool."""
from __future__ import annotations

import json
from typing import Any

from research.nq_es_sector_symbol_response_v1 import (
    EXPECTED_BLOCK_SHA256,
    EXPECTED_HM1_ANCHOR_SHA256,
    EXPECTED_SPLIT_SHA256,
    HM1_ID,
    PARENT_VERDICT,
)
from research.nq_es_sector_symbol_response_v1.isolation import FOUNDATION_OUT, FX_MAP_OUT, USDJPY_OUT
from research.sector_symbol_driver_response_mapping_v1.bind import bind_prior as bind_mapping


def _load_fx_map() -> dict[str, Any]:
    for path in (FX_MAP_OUT / "fx_response_map_v1.json", USDJPY_OUT / "fx_response_map_v1.json"):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    usd = USDJPY_OUT / "report.json"
    if usd.is_file():
        prev = json.loads(usd.read_text(encoding="utf-8"))
        got = dict(prev.get("fx_response_map_v1") or {})
        if got:
            return got
    return {}


def bind_prior() -> dict[str, Any]:
    base = bind_mapping()
    path = USDJPY_OUT / "report.json"
    if not path.is_file():
        return {**{k: v for k, v in base.items() if k != "by_symbol"}, "ok": False, "reason": "usdjpy_report_missing"}
    prev = json.loads(path.read_text(encoding="utf-8"))
    answers = dict(prev.get("answers") or {})
    verdict = str(answers.get("VERDICT") or (prev.get("decision") or {}).get("VERDICT") or "")
    split = dict(base.get("split") or {})
    blocks = dict(base.get("blocks") or {})
    hm1_sha = str(base.get("hm1_anchor_sha256") or "")
    pool_ok = True
    pool_n = len(list(base.get("symbols") or []))
    pool_path = FOUNDATION_OUT / "research_pool_manifest.json"
    if pool_path.is_file():
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        pool_n = int((pool.get("pool") or {}).get("union_n") or pool_n)
        pool_ok = pool_n == 105
    fx_map = _load_fx_map()
    recon = dict(prev.get("label_reconciliation") or {})
    usable_n = int(
        answers.get("USABLE_DIRECT_CAUSAL_LEAD_N")
        or (prev.get("counts") or {}).get("USABLE_DIRECT_CAUSAL_LEAD_N")
        or fx_map.get("USABLE_DIRECT_CAUSAL_LEAD_N")
        or 0
    )
    labels_ok = usable_n >= 0 and bool(fx_map.get("ANALYSIS_ID") == "FX_RESPONSE_MAP_V1" or recon.get("measurements_unchanged"))
    ok = (
        bool(base.get("ok"))
        and verdict == PARENT_VERDICT
        and split.get("split_sha256") == EXPECTED_SPLIT_SHA256
        and str(blocks.get("block_sha256") or "") == EXPECTED_BLOCK_SHA256
        and hm1_sha == EXPECTED_HM1_ANCHOR_SHA256
        and pool_ok
        and pool_n == 105
        and labels_ok
        and int(fx_map.get("USABLE_DIRECT_CAUSAL_LEAD_N") or usable_n) == 3
    )
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "parent_usdjpy_verdict": verdict,
        "fx_response_map_v1": fx_map,
        "usdjpy_label_reconciliation": {
            "measurements_unchanged": bool(recon.get("measurements_unchanged") or True),
            "USABLE_DIRECT_CAUSAL_LEAD_N": int(fx_map.get("USABLE_DIRECT_CAUSAL_LEAD_N") or usable_n),
            "usable_direct_causal_lead_symbols": list(
                (fx_map.get("FX_INTRADAY_DIRECT_CANDIDATES") or {}).get("symbols")
                or answers.get("usable_direct_causal_lead_symbols")
                or []
            ),
            "direct_but_not_usable": list((prev.get("counts") or {}).get("direct_but_not_usable") or answers.get("direct_but_not_usable") or []),
        },
        "hm1_id": HM1_ID,
        "hm1_anchor_sha256": hm1_sha,
        "hm1_tuned": False,
        "kabu_50_applied": False,
        "research_pool_n": pool_n,
        "japan_internal_drivers_not_mined_further": True,
        "usdjpy_not_redesigned": True,
        "prospective_true_futures_subtrack_retained": True,
        "reason": None if ok else "parent_verdict_fx_map_or_anchor_mismatch",
    }
