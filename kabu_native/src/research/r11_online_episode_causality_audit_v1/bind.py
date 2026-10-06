"""Bind frozen R11, split/blocks. Discovery only. Do not open Confirmation or Frozen Validation."""
from __future__ import annotations

import json
from typing import Any

from research.native_1m_path_state_strategy_freeze_v1.bind import bind_prior as bind_freeze
from research.r11_online_episode_causality_audit_v1 import PARENT_VERDICT
from research.r11_online_episode_causality_audit_v1.isolation import R11_FREEZE_OUT


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def bind_prior() -> dict[str, Any]:
    base = bind_freeze()
    freeze = _load_json(R11_FREEZE_OUT / "report.json")
    ans = dict(freeze.get("answers") or {})
    verd = str(ans.get("VERDICT") or (freeze.get("decision") or {}).get("VERDICT") or "")
    freeze_ok = verd == PARENT_VERDICT
    identity_ok = bool(ans.get("trade_identity_908_908"))
    med = dict((freeze.get("manifest") or {}).get("d1_nan_med_frozen") or freeze.get("d1_med") or {})
    ok = bool(base.get("ok")) and freeze_ok and identity_ok
    reason = None
    if not base.get("ok"):
        reason = "freeze_bind_failed"
    elif not freeze_ok:
        reason = f"parent_verdict_{verd}"
    elif not identity_ok:
        reason = "freeze_identity_not_908"
    return {
        **{k: v for k, v in base.items() if k != "by_symbol"},
        "ok": ok,
        "symbols": list(base.get("symbols") or []),
        "by_symbol": dict(base.get("by_symbol") or {}),
        "freeze_verdict": verd,
        "freeze_identity_908": identity_ok,
        "d1_med": med,
        "reason": reason,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
    }
