"""Repo-root `.env` loader (cwd-independent).

SoT: ``<repo>/.env`` where repo is resolved from this file's location
(``kabu_native/src/small_paper/env_loader.py`` → parents[3]).

OS environment variables always win (``load_dotenv(..., override=False)``).
Never log webhook URL values — only configured bools.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

log = logging.getLogger("kabu_native.env_loader")

# Discord webhook keys checked by readiness / operators (Phase687W10+)
DISCORD_WEBHOOK_ENV_KEYS: tuple[str, ...] = (
    "KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL",
    "KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL",
    "KABU_V1R_ENTRY_WEBHOOK_URL",
    "KABU_DISCORD_OPERATIONS_WEBHOOK_URL",
    "KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL",
    "KABU_DISCORD_RESEARCH_WEBHOOK_URL",
    "KABU_DISCORD_CRITICAL_WEBHOOK_URL",
)

_LOADED_PATH: Optional[str] = None


@dataclass(frozen=True)
class RepoEnvLoadStatus:
    repo_root: Path
    dotenv_path: Path
    dotenv_exists: bool
    dotenv_loaded: bool
    webhook_configured: Mapping[str, bool] = field(default_factory=dict)

    def as_public_dict(self) -> dict:
        """Safe for logs / readiness JSON — no secret values."""
        return {
            "repo_root": str(self.repo_root),
            "dotenv_path": str(self.dotenv_path),
            "dotenv_exists": self.dotenv_exists,
            "dotenv_loaded": self.dotenv_loaded,
            "webhook_configured": dict(self.webhook_configured),
        }


def resolve_repo_root() -> Path:
    """``tradebotfile`` root from ``kabu_native/src/small_paper/env_loader.py``."""
    return Path(__file__).resolve().parents[3]


def webhook_configured_map(*, keys: tuple[str, ...] = DISCORD_WEBHOOK_ENV_KEYS) -> dict[str, bool]:
    """Return {env_key: bool} — never the URL string."""
    return {k: bool((os.environ.get(k) or "").strip()) for k in keys}


def load_repo_dotenv(
    *,
    repo_root: Optional[Path] = None,
    override: bool = False,
) -> RepoEnvLoadStatus:
    """
    Load ``repo_root/.env`` into ``os.environ``.

    - Path is ``__file__``-based by default (not cwd).
    - ``override=False`` (default): existing OS env wins.
    - Missing ``.env`` or missing ``python-dotenv`` is non-fatal.
    """
    global _LOADED_PATH
    root = Path(repo_root).resolve() if repo_root is not None else resolve_repo_root()
    dotenv_path = root / ".env"
    exists = dotenv_path.is_file()
    loaded = False
    try:
        from dotenv import load_dotenv

        if exists:
            load_dotenv(dotenv_path=dotenv_path, override=override)
            loaded = True
            _LOADED_PATH = str(dotenv_path)
        else:
            _LOADED_PATH = str(dotenv_path)
    except ImportError:
        log.warning("python-dotenv not installed; skipping .env load path=%s", dotenv_path)
        loaded = False

    status = RepoEnvLoadStatus(
        repo_root=root,
        dotenv_path=dotenv_path,
        dotenv_exists=exists,
        dotenv_loaded=loaded,
        webhook_configured=webhook_configured_map(),
    )
    return status


def ensure_repo_dotenv(*, repo_root: Optional[Path] = None) -> RepoEnvLoadStatus:
    """Idempotent convenience: load repo ``.env`` with OS priority."""
    return load_repo_dotenv(repo_root=repo_root, override=False)


def log_webhook_configured(
    status: Optional[RepoEnvLoadStatus] = None,
    *,
    logger: Optional[logging.Logger] = None,
) -> None:
    """Log configured=true/false only — never URL values."""
    lg = logger or log
    st = status or ensure_repo_dotenv()
    lg.info(
        "repo env: dotenv_exists=%s dotenv_loaded=%s path=%s",
        st.dotenv_exists,
        st.dotenv_loaded,
        st.dotenv_path,
    )
    for key, configured in st.webhook_configured.items():
        lg.info("webhook %s configured=%s", key, configured)


def reset_env_loader_for_tests() -> None:
    """Test helper only."""
    global _LOADED_PATH
    _LOADED_PATH = None
