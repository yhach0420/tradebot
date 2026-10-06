"""Official J-Quants paginated fetch with V2 reference cache. Never stores API key."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from research.daytrade_historical_research_foundation_v2.isolation import REF
from research.fixed_daytrade_universe_v1.jquants_client import request_json

SLEEP_SEC = 0.15
PAGE_GUARD = 400


def _iso(yyyymmdd: str) -> str:
    s = str(yyyymmdd)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def fetch_pages(*, path: str, params: dict[str, str], cache_path: Path) -> dict[str, Any]:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path = cache_path.with_suffix(cache_path.suffix + ".meta.json")
    if cache_path.is_file() and meta_path.is_file():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            rows = list(payload.get("data") or [])
        else:
            rows = list(payload)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["from_cache"] = True
        return {"ok": True, "rows": rows, "from_cache": True, "meta": meta, "status": 200}
    rows: list[dict[str, Any]] = []
    page_params = dict(params)
    pages = 0
    last_status = None
    sample = None
    while pages < PAGE_GUARD:
        pages += 1
        got = request_json(path=path, params=page_params)
        last_status = got.get("status")
        if not got.get("ok"):
            return {"ok": False, "rows": rows, "status": last_status, "reason": got.get("reason"), "error_snippet": got.get("error_snippet"), "from_cache": False}
        payload = got.get("payload") or {}
        chunk = list(payload.get("data") or [])
        for item in chunk:
            if isinstance(item, dict):
                if sample is None:
                    sample = item
                rows.append(item)
        nxt = payload.get("pagination_key")
        if not nxt:
            break
        page_params = dict(params)
        page_params["pagination_key"] = str(nxt)
        time.sleep(SLEEP_SEC)
    envelope = {"data": rows}
    cache_path.write_text(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    meta = {
        "endpoint": path,
        "params": {k: v for k, v in params.items() if k != "pagination_key"},
        "row_n": len(rows),
        "pages": pages,
        "sample_keys": list(sample.keys()) if sample else [],
        "api_key_recorded": False,
        "from_cache": False,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    time.sleep(SLEEP_SEC)
    return {"ok": True, "rows": rows, "status": last_status, "from_cache": False, "meta": meta, "sample": sample}


assert _iso("20260911") == "2026-09-11"
