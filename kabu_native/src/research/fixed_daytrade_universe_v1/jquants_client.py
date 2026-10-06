"""J-Quants V2 HTTP client. Caches official JSON. Never writes API key."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research.fixed_daytrade_universe_v1 import JQUANTS_BASE
from research.fixed_daytrade_universe_v1.isolation import REF_JQUANTS
from research.fixed_daytrade_universe_v1.jquants_auth import api_key_value

JST = ZoneInfo("Asia/Tokyo")
TIMEOUT_SEC = 60
SLEEP_SEC = 0.15
USER_AGENT = "kabu_native-fixed-universe-freeze/1"


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def cache_paths(kind: str, stem: str) -> dict[str, Any]:
    root = REF_JQUANTS / kind
    root.mkdir(parents=True, exist_ok=True)
    body = root / f"{stem}.json"
    meta = root / f"{stem}.meta.json"
    return {"dir": root, "body": body, "meta": meta}


def _safe_error(exc: BaseException) -> str:
    text = str(exc)
    key = api_key_value()
    if key:
        text = text.replace(key, "[REDACTED]")
    return text[:500]


def request_json(*, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    """HTTP GET that returns status instead of raising on 4xx. Never logs the API key."""
    key = api_key_value()
    if not key:
        return {"ok": False, "status": None, "reason": "jquants_api_key_missing", "payload": None}
    q = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None and v != ""})
    url = f"{JQUANTS_BASE}{path}"
    if q:
        url = f"{url}?{q}"
    req = urllib.request.Request(
        url,
        headers={
            "x-api-key": key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            raw = resp.read()
            status = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        snippet = ""
        try:
            snippet = _safe_error(exc.read().decode("utf-8", errors="replace")[:240])
        except Exception:
            snippet = _safe_error(exc)
        return {
            "ok": False,
            "status": int(exc.code),
            "reason": f"jquants_http_{exc.code}",
            "payload": None,
            "error_snippet": snippet,
        }
    except Exception as exc:
        return {"ok": False, "status": None, "reason": f"jquants_http_error:{_safe_error(exc)}", "payload": None}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return {"ok": False, "status": status, "reason": "jquants_json_decode", "payload": None}
    if status >= 400:
        return {"ok": False, "status": status, "reason": f"jquants_http_{status}", "payload": None}
    return {"ok": True, "status": status, "reason": None, "payload": payload}


def _get_json(path: str, params: dict[str, str]) -> dict[str, Any]:
    got = request_json(path=path, params=params)
    if not got.get("ok"):
        raise RuntimeError(str(got.get("reason") or "jquants_http_error"))
    payload = got.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("jquants_response_not_object")
    return payload


def fetch_paginated(*, path: str, params: dict[str, str], kind: str, stem: str) -> dict[str, Any]:
    cached = try_load_cache(kind=kind, stem=stem)
    if cached is not None:
        return cached
    rows: list[dict[str, Any]] = []
    page_params = dict(params)
    pages = 0
    sample: dict[str, Any] | None = None
    while True:
        pages += 1
        payload = _get_json(path, page_params)
        data = payload.get("data")
        if not isinstance(data, list):
            raise RuntimeError("jquants_data_not_list")
        for item in data:
            if isinstance(item, dict):
                if sample is None:
                    sample = item
                rows.append(item)
        nxt = payload.get("pagination_key")
        if not nxt:
            break
        page_params["pagination_key"] = str(nxt)
        time.sleep(SLEEP_SEC)
        if pages > 200:
            raise RuntimeError("jquants_pagination_guard")
    envelope = {"data": rows}
    raw = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    meta = {
        "downloaded_at": datetime.now(JST).isoformat(timespec="seconds"),
        "endpoint": path,
        "request_period": {k: v for k, v in params.items() if k != "pagination_key"},
        "response_row_count": len(rows),
        "schema_raw_keys": list(sample.keys()) if sample else [],
        "SHA256": _sha_bytes(raw),
        "pages": pages,
        "api_key_recorded": False,
        "cache_kind": kind,
        "cache_stem": stem,
        "from_cache": False,
    }
    paths = cache_paths(kind, stem)
    paths["body"].write_bytes(raw)
    paths["meta"].write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    time.sleep(SLEEP_SEC)
    return {"rows": rows, "sample": sample, "meta": meta, "from_cache": False}


def try_load_cache(*, kind: str, stem: str) -> dict[str, Any] | None:
    paths = cache_paths(kind, stem)
    if not paths["body"].is_file() or not paths["meta"].is_file():
        return None
    raw = paths["body"].read_bytes()
    meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
    if str(meta.get("SHA256") or "") != _sha_bytes(raw):
        return None
    payload = json.loads(raw.decode("utf-8"))
    rows = list(payload.get("data") or [])
    sample = rows[0] if rows and isinstance(rows[0], dict) else None
    meta = dict(meta)
    meta["from_cache"] = True
    meta["api_key_recorded"] = False
    return {"rows": rows, "sample": sample, "meta": meta, "from_cache": True}


def cache_complete_for_freeze(*, expected_bar_stems: list[str] | None = None) -> bool:
    cal = list((REF_JQUANTS / "calendar").glob("*.json"))
    master = list((REF_JQUANTS / "listed_master").glob("*.json"))
    bars = list((REF_JQUANTS / "daily_bars").glob("*.json"))
    if not cal or not master or not bars:
        return False
    if expected_bar_stems:
        bodies = {p.stem for p in (REF_JQUANTS / "daily_bars").glob("*.json") if ".meta." not in p.name}
        return set(expected_bar_stems) <= bodies
    return True


assert USER_AGENT.startswith("kabu_native")
