"""E1X5-FWD unit tests."""
from __future__ import annotations

import os
from typing import Any

from small_paper.e1_x5_forward_shadow import (
    ENV_KEY,
    THRESHOLD,
    e1_x5_forward_shadow_enabled,
    econ,
    resolve_e1_x5_forward_shadow_enabled,
)
from small_paper.forward_observer_defaults import PAPER_RUNTIME_ENV


def run_tests() -> dict[str, Any]:
    rows = []
    passed = failed = 0

    def check(name: str, cond: bool, detail: str = ""):
        nonlocal passed, failed
        rows.append({"name": name, "ok": bool(cond), "detail": detail})
        if cond:
            passed += 1
        else:
            failed += 1

    prev = os.environ.pop(ENV_KEY, None)
    prev_paper = os.environ.pop(PAPER_RUNTIME_ENV, None)

    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value=None)
    check("paper_default_on", d.enabled is True and d.reason == "PAPER_DEFAULT_ON")
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value="")
    check("paper_empty_on", d.enabled is True)
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value="1")
    check("paper_env_on", d.enabled is True and d.reason == "PAPER_ENV_ON")
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value="0")
    check("paper_env_off", d.enabled is False and d.reason == "PAPER_ENV_OFF")
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=True, env_value="bogus")
    check("paper_invalid_off", d.enabled is False and d.reason == "INVALID_ENV_FORCED_OFF")
    d = resolve_e1_x5_forward_shadow_enabled(is_paper_runtime=False, env_value="1")
    check("non_paper_forced_off", d.enabled is False and d.reason == "NON_PAPER_FORCED_OFF")

    # Runtime helper: non-paper unset → OFF
    check("runtime_non_paper_default_off", e1_x5_forward_shadow_enabled() is False)
    os.environ[PAPER_RUNTIME_ENV] = "1"
    check("runtime_paper_default_on", e1_x5_forward_shadow_enabled() is True)

    if prev is None:
        os.environ.pop(ENV_KEY, None)
    else:
        os.environ[ENV_KEY] = prev
    if prev_paper is None:
        os.environ.pop(PAPER_RUNTIME_ENV, None)
    else:
        os.environ[PAPER_RUNTIME_ENV] = prev_paper

    check("threshold", abs(THRESHOLD - 0.48256067040851486) < 1e-15)
    e = econ(1000.0, 1010.0)
    check("econ_net", abs(e["net_pnl_yen_100"] - 950.0) < 1e-9)
    check("submit0", True)
    return {"passed": failed == 0, "n_passed": passed, "n_failed": failed, "rows": rows}
