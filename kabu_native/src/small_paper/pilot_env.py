"""
Shared .env loading for small paper pilot scripts (repo-root based, cwd-independent).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PilotEnvStatus:
    repo_root: Path
    cwd: str
    dotenv_path: Path
    dotenv_exists: bool
    dotenv_loaded: bool
    kabu_api_password_set: bool
    discord_webhook_env: str
    discord_webhook_set: bool


def load_pilot_environment(
    *,
    repo_root: Path,
    discord_webhook_env: str = "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL",
) -> PilotEnvStatus:
    """
    Load repository-root `.env` before safety checks or live pilot.

    Delegates to ``small_paper.env_loader`` (cwd-independent, OS env wins).
    """
    from small_paper.env_loader import load_repo_dotenv

    root = Path(repo_root).resolve()
    st = load_repo_dotenv(repo_root=root, override=False)
    env_name = (discord_webhook_env or "KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL").strip()
    return PilotEnvStatus(
        repo_root=st.repo_root,
        cwd=os.getcwd(),
        dotenv_path=st.dotenv_path,
        dotenv_exists=st.dotenv_exists,
        dotenv_loaded=st.dotenv_loaded,
        kabu_api_password_set=bool(os.environ.get("KABU_API_PASSWORD", "").strip()),
        discord_webhook_env=env_name,
        discord_webhook_set=bool(os.environ.get(env_name, "").strip()),
    )


def log_pilot_env_status(status: PilotEnvStatus, *, stream: Optional[object] = None) -> None:
    out = stream or sys.stderr
    print("[small_paper_env] pilot environment", file=out)
    print(f"  cwd={status.cwd}", file=out)
    print(f"  repo_root={status.repo_root}", file=out)
    print(f"  dotenv_path={status.dotenv_path}", file=out)
    print(f"  dotenv_exists={status.dotenv_exists}", file=out)
    print(f"  dotenv_loaded={status.dotenv_loaded}", file=out)
    print(f"  KABU_API_PASSWORD_set={status.kabu_api_password_set}", file=out)
    print(f"  discord_webhook_env={status.discord_webhook_env}", file=out)
    print(f"  discord_webhook_set={status.discord_webhook_set}", file=out)
