"""Resolve J-Quants V2 API key from environment only. Never log or return the secret."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from research.fixed_daytrade_universe_v1.isolation import NATIVE

# V2 uses API Key in x-api-key. Refresh-token / mail-password are V1 and are not used.
V2_API_KEY_ENV_NAMES = ("JQUANTS_API_KEY", "JQUANTS_APIKEY", "JQ_API_KEY")
V1_NOT_USED = ("JQUANTS_REFRESH_TOKEN", "JQUANTS_MAILADDRESS", "JQUANTS_PASSWORD")


def _load_dotenv_into_environ() -> dict[str, Any]:
    loaded_paths: list[str] = []
    exists: list[str] = []
    try:
        from small_paper.env_loader import ensure_repo_dotenv

        st = ensure_repo_dotenv()
        loaded_paths.append(str(st.dotenv_path))
        if st.dotenv_exists:
            exists.append(str(st.dotenv_path))
    except Exception:
        pass
    native_env = NATIVE / ".env"
    if native_env.is_file():
        exists.append(str(native_env))
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path=native_env, override=False)
            loaded_paths.append(str(native_env))
        except Exception:
            pass
    return {
        "dotenv_paths_checked": loaded_paths,
        "dotenv_exists": exists,
        "values_logged": False,
    }


def resolve_api_key_meta() -> dict[str, Any]:
    """Public credential status. Contains env var NAME only, never the key."""
    dotenv = _load_dotenv_into_environ()
    chosen_name: str | None = None
    for name in V2_API_KEY_ENV_NAMES:
        if str(os.environ.get(name) or "").strip():
            chosen_name = name
            break
    v1_present = [n for n in V1_NOT_USED if str(os.environ.get(n) or "").strip()]
    return {
        "present": chosen_name is not None,
        "env_var_name": chosen_name,
        "preferred_env_var": V2_API_KEY_ENV_NAMES[0],
        "v1_credential_present_but_not_used": bool(v1_present),
        "v1_env_names_present": v1_present,
        "dotenv": dotenv,
        "source": "environment_variable_only",
        "yfinance_fallback": False,
        "kabu_board_fallback": False,
        "scraping_fallback": False,
    }


def api_key_value() -> str | None:
    """In-memory secret for HTTP only. Caller must not write this to disk or stdout."""
    _load_dotenv_into_environ()
    for name in V2_API_KEY_ENV_NAMES:
        v = str(os.environ.get(name) or "").strip()
        if v:
            return v
    return None


def secret_needles() -> list[str]:
    """Non-empty secret strings to scan out of artifacts. Empty if no key."""
    v = api_key_value()
    if not v:
        return []
    needles = [v]
    if len(v) >= 12:
        needles.append(v[:12])
    return needles


assert V2_API_KEY_ENV_NAMES[0] == "JQUANTS_API_KEY"
assert "JQUANTS_REFRESH_TOKEN" not in V2_API_KEY_ENV_NAMES
