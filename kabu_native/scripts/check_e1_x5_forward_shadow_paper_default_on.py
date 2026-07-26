"""Safe mock preflight: Paper default ON / explicit OFF (no market session, no orders)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from small_paper.e1_x5_forward_shadow import (  # noqa: E402
    ENV_KEY,
    E1X5ForwardShadowSession,
    emit_e1_x5_forward_shadow_startup_once,
    resolve_e1_x5_forward_shadow_from_runtime,
)
from small_paper.forward_observer_defaults import PAPER_RUNTIME_ENV  # noqa: E402


def _reset_emit_flag() -> None:
    import small_paper.e1_x5_forward_shadow as mod

    mod._startup_emitted = False


def main() -> int:
    os.environ[PAPER_RUNTIME_ENV] = "1"
    os.environ.pop(ENV_KEY, None)
    _reset_emit_flag()

    with tempfile.TemporaryDirectory() as td:
        on_path = Path(td) / "e1x5_on.txt"
        d_on = resolve_e1_x5_forward_shadow_from_runtime()
        emit_e1_x5_forward_shadow_startup_once(d_on, save_path=on_path, force=True)
        sess_on = E1X5ForwardShadowSession.maybe_create(emit_startup=False)
        print("--- mock Paper (env unset) ---")
        print(on_path.read_text(encoding="utf-8").rstrip())
        ok_on = (
            sess_on.enabled
            and d_on.reason == "PAPER_DEFAULT_ON"
            and "order_api: disabled" in on_path.read_text(encoding="utf-8")
            and "pbv2_cap_impact: none" in on_path.read_text(encoding="utf-8")
        )
        print(f"check_on: {'PASS' if ok_on else 'FAIL'}")

        os.environ[ENV_KEY] = "0"
        _reset_emit_flag()
        off_path = Path(td) / "e1x5_off.txt"
        d_off = resolve_e1_x5_forward_shadow_from_runtime()
        emit_e1_x5_forward_shadow_startup_once(d_off, save_path=off_path, force=True)
        sess_off = E1X5ForwardShadowSession.maybe_create(emit_startup=False)
        print("--- mock Paper (E1_X5_FORWARD_SHADOW=0) ---")
        print(off_path.read_text(encoding="utf-8").rstrip())
        ok_off = (
            (not sess_off.enabled)
            and d_off.reason == "PAPER_ENV_OFF"
            and "DISABLED" in off_path.read_text(encoding="utf-8")
        )
        print(f"check_off: {'PASS' if ok_off else 'FAIL'}")

    # non-paper + env=1 must stay OFF
    os.environ.pop(PAPER_RUNTIME_ENV, None)
    os.environ[ENV_KEY] = "1"
    d_np = resolve_e1_x5_forward_shadow_from_runtime()
    ok_np = (not d_np.enabled) and d_np.reason == "NON_PAPER_FORCED_OFF"
    print(f"check_non_paper_env1: {'PASS' if ok_np else 'FAIL'} reason={d_np.reason}")

    if ok_on and ok_off and ok_np:
        print("E1_X5_PAPER_DEFAULT_ON_PREFLIGHT_PASS")
        return 0
    print("E1_X5_PAPER_DEFAULT_ON_PREFLIGHT_FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
