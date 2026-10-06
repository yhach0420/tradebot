"""Occupied Paper identities and the frozen M3 constituent set. No price rows."""
from __future__ import annotations

import json
from typing import Any

from api.kabu_register import normalize_symbol_code
from research.causal_driver_pb1 import PROSPECTIVE_FROM
from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_state_alpha_shadow import M3_TARGETS
from research.causal_driver_pb1.sector_state_alpha_shadow_registration import M3_SHA256, SOURCE_DAY
from research.causal_driver_pb1.sector_state_alpha_shadow_registration.isolation import NATIVE
from research.causal_driver_pb1.sector_state_transmission_precommit.targets import bind_targets


def _norm(symbol: str) -> str:
    return normalize_symbol_code(symbol)


def _load_json(path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_identities() -> dict[str, Any]:
    blockers: list[str] = []
    if SOURCE_DAY >= PROSPECTIVE_FROM:
        raise RuntimeError("source_day_is_prospective")
    capture = NATIVE / "data" / "market_context_capture" / SOURCE_DAY
    prepared = _load_json(capture / "prepared_manifest.json")
    live = _load_json(capture / "live_manifest.json")
    registered = _load_json(capture / "station_state" / "runtime" / "paper_register_state.json")
    if str(prepared.get("trading_date")) != SOURCE_DAY or str(live.get("trading_date")) != SOURCE_DAY:
        blockers.append("SOURCE_DAY_MISMATCH")
    if str(registered.get("trading_date")) >= PROSPECTIVE_FROM:
        raise RuntimeError("prospective_register_state_refused")
    targets = bind_targets()
    blockers.extend(targets.get("blockers") or [])
    m3 = [_norm(s) for s in (targets.get("sector3650_symbols") or [])]
    m3_sha = str(targets.get("sector3650_target_set_sha256") or "")
    if len(m3) != 30 or len(set(m3)) != 30:
        blockers.append("M3_N")
    if m3_sha != M3_SHA256:
        blockers.append("M3_SHA_MISMATCH")
    core = [_norm(s) for s in (prepared.get("core10") or [])]
    dynamic = [_norm(s) for s in (prepared.get("dynamic38") or [])]
    futures = []
    future_codes = []
    for row in live.get("futures") or []:
        future_codes.append(str(row.get("FutureCode") or ""))
        futures.append(_norm(str(row.get("resolved_symbol") or "")))
    occupied = [_norm(s) for s in (registered.get("symbol_codes") or [])]
    if len(core) != 10 or len(set(core)) != 10:
        blockers.append("CORE_N")
    if len(dynamic) != 38 or len(set(dynamic)) != 38:
        blockers.append("DYNAMIC_N")
    if len(futures) != 2 or len(set(futures)) != 2 or "" in futures:
        blockers.append("FUTURES_N")
    if set(core) & set(dynamic):
        blockers.append("CORE_DYNAMIC_OVERLAP")
    standard = set(core) | set(dynamic) | set(futures)
    if standard != set(occupied) or len(occupied) != 50:
        blockers.append("OCCUPIED_PROFILE_IDENTITY_MISMATCH")
    target_codes = [_norm(s) for s in M3_TARGETS]
    targets_inside = all(s in set(m3) for s in target_codes)
    if not targets_inside:
        blockers.append("M3_TARGETS_NOT_SUBSET")
    return {
        "pass": not blockers,
        "blockers": blockers,
        "source_day": SOURCE_DAY,
        "m3": m3,
        "m3_sha256": m3_sha,
        "targets": target_codes,
        "targets_subset": targets_inside,
        "core": core,
        "dynamic": dynamic,
        "futures": futures,
        "future_codes": future_codes,
        "occupied": occupied,
        "previous_owner": "PAPER_STANDARD_REGISTER_PROFILE",
        "previous_plan_sha256": sha256_obj({
            "namespace": "PAPER_STANDARD_REGISTER_PROFILE_V1",
            "symbols": sorted(set(occupied)),
        }),
    }
