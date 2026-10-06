"""Ranking GET client. No /register, /unregister, or sendorder."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from api.rest_client import KabuNativeRestClient, default_base_url
from research.market_breadth_leadership_acquisition_v1 import EXCHANGE_DIVISION, RANKING_TYPES

JST = ZoneInfo("Asia/Tokyo")
REGISTER_MUTATION_N = 0
UNREGISTER_N = 0
SENDORDER_N = 0
POST_TOKEN_N = 0
LAST_TOKEN_SOURCE = ""


def now_received_at() -> str:
    return datetime.now(JST).isoformat(timespec="milliseconds")


def get_json_status(
    *,
    rest: KabuNativeRestClient,
    token: str,
    path: str,
    params: Optional[dict[str, Any]] = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """GET with status capture. Does not PUT register. Does not POST sendorder."""
    url = f"{rest.base_url}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    received_at = now_received_at()
    headers = {"Content-Type": "application/json", "X-API-KEY": token}
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        return {
            "received_at": received_at,
            "url_path": path,
            "params": dict(params or {}),
            "http_status": None,
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
            "raw": None,
        }
    raw: Any = None
    parse_error = ""
    try:
        raw = response.json()
    except Exception as exc:
        parse_error = f"{type(exc).__name__}:{exc}"
        raw = response.text[:4000]
    return {
        "received_at": received_at,
        "url_path": path,
        "params": dict(params or {}),
        "http_status": int(response.status_code),
        "ok": bool(response.ok),
        "error": parse_error,
        "raw": raw,
    }


def get_apisoftlimit(*, rest: KabuNativeRestClient, token: str) -> dict[str, Any]:
    return get_json_status(rest=rest, token=token, path="/apisoftlimit")


def get_ranking(
    *,
    rest: KabuNativeRestClient,
    token: str,
    ranking_type: int,
    exchange_division: str = EXCHANGE_DIVISION,
) -> dict[str, Any]:
    if int(ranking_type) not in RANKING_TYPES:
        raise ValueError(f"ranking type {ranking_type} is not in the frozen first set")
    got = get_json_status(
        rest=rest,
        token=token,
        path="/ranking",
        params={"Type": int(ranking_type), "ExchangeDivision": str(exchange_division)},
    )
    got["requested_type"] = int(ranking_type)
    got["ExchangeDivision"] = str(exchange_division)
    return got


def mutation_counts() -> dict[str, int]:
    return {
        "register_mutation_n": int(REGISTER_MUTATION_N),
        "unregister_n": int(UNREGISTER_N),
        "sendorder_n": int(SENDORDER_N),
        "post_token_n": int(POST_TOKEN_N),
    }


def station_owner_live() -> dict[str, Any]:
    from small_paper.kabu_token_authority import _pid_alive, load_station_owner

    owner = load_station_owner() or {}
    pid = int(owner.get("pid") or 0)
    return {"owner_pid": pid, "live": bool(_pid_alive(pid))}


def _post_ephemeral_token(*, rest: KabuNativeRestClient) -> str:
    """In-memory token for ranking GET. Does not publish owner. Does not register."""
    from api.rest_client import require_kabu_password

    password = require_kabu_password()
    url = f"{rest.base_url}/token"
    response = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={"APIPassword": password},
        timeout=15.0,
    )
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"token response not JSON http={response.status_code}:{exc}") from exc
    token = str((payload or {}).get("Token") or "").strip()
    if response.status_code != 200 or not token:
        raise RuntimeError(f"ephemeral token failed http={response.status_code} code={(payload or {}).get('Code')}")
    return token


def issue_readonly_token(*, native_root) -> tuple[KabuNativeRestClient, str]:
    """Prefer the published token. Ephemeral POST /token only if owner is dead/missing.

    Never mutates the websocket register set. Never claims ingress ownership.
    """
    global LAST_TOKEN_SOURCE, POST_TOKEN_N
    from pathlib import Path

    from api.rest_client import load_kabu_env
    from small_paper.kabu_token_authority import read_shared_token

    root = Path(native_root)
    load_kabu_env(repo_root=root.parent)
    load_kabu_env(repo_root=root)
    rest = KabuNativeRestClient(default_base_url())
    owner = station_owner_live()
    reused = str(read_shared_token(root) or "").strip()
    if reused:
        soft = get_apisoftlimit(rest=rest, token=reused)
        if soft.get("http_status") == 200:
            LAST_TOKEN_SOURCE = "reused_shared"
            POST_TOKEN_N = 0
            return rest, reused
        if owner.get("live"):
            LAST_TOKEN_SOURCE = "reused_shared_stale_owner_live"
            POST_TOKEN_N = 0
            return rest, reused
    if owner.get("live"):
        LAST_TOKEN_SOURCE = "blocked_owner_live_no_usable_token"
        POST_TOKEN_N = 0
        raise RuntimeError("live token owner is active; ranking collector will not POST /token")
    LAST_TOKEN_SOURCE = "ephemeral_post_token_owner_dead"
    token = _post_ephemeral_token(rest=rest)
    POST_TOKEN_N = 1
    return rest, token
