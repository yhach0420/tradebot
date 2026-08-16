"""Test isolation for Paper Runtime env that must not leak across cases."""
from __future__ import annotations

import os

import pytest

from small_paper.auth_lifecycle import ENV_AUTH_PHASE


@pytest.fixture(autouse=True)
def _isolate_auth_lifecycle_phase() -> None:
    os.environ.pop(ENV_AUTH_PHASE, None)
    os.environ.pop("TRADEBOT_ACTIVATION_SELECTOR", None)
    yield
    os.environ.pop(ENV_AUTH_PHASE, None)
    os.environ.pop("TRADEBOT_ACTIVATION_SELECTOR", None)
