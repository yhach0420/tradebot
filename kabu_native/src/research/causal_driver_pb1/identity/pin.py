"""Pin Frozen V4 / Complete Strategy / design / universe. Read-only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.causal_driver_pb1 import (
    COMPLETE_STRATEGY_IDENTITY,
    DESIGN_ID,
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_DESIGN_NEXT,
    EXPECTED_DESIGN_VERDICT,
    EXPECTED_SOURCE_INVENTORY_SHA256,
    EXPECTED_UNIVERSE_MANIFEST_SHA256,
    EXPECTED_V4_MACHINE_SHA256,
    FROZEN_ENTRY_IDENTITY,
    SPEC_ID,
    V5_CREATED,
)
from research.causal_driver_pb1.contracts.errors import IdentityError
from research.causal_driver_pb1.datasets.firewall import firewall_config_sha256
from research.causal_driver_pb1.datasets.roles import dataset_role_config_sha256
from research.causal_driver_pb1.identity.ids import sha256_bytes, sha256_obj
from research.causal_driver_pb1.isolation import CS_SRC, DESIGN_MANIFEST, DESIGN_MD, PAPER_RUNNER, UNIVERSE_RUNTIME, V4_SRC


def _file_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    return sha256_bytes(path.read_bytes())


def _dir_file_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*.py")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        out[rel] = sha256_bytes(p.read_bytes())
    return out


def frozen_source_hashes() -> dict[str, Any]:
    return {
        "v4": _dir_file_hashes(V4_SRC),
        "complete_strategy": _dir_file_hashes(CS_SRC),
        "paper_trade_checked_runner": _file_sha(PAPER_RUNNER),
        "core10_dynamic40": _file_sha(UNIVERSE_RUNTIME),
    }


def contract_schema_sha256() -> str:
    root = Path(__file__).resolve().parents[1]
    files = []
    for folder in ("contracts", "datasets", "transport", "identity"):
        d = root / folder
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.py")):
            files.append((str(p.relative_to(root)).replace("\\", "/"), p.read_bytes()))
    h_input = [(n, sha256_bytes(b)) for n, b in files]
    return sha256_obj(h_input)


def bind_identities() -> dict[str, Any]:
    from research.pb1_v4_clarified_machine_correction_v4.definitions import machine_sha256 as v4_sha
    from research.pb1_v4_complete_strategy_build_and_economic_validation.freeze import complete_strategy_sha256

    v4 = v4_sha()
    cs = complete_strategy_sha256(
        machine_sha=EXPECTED_V4_MACHINE_SHA256,
        source_inventory_sha=EXPECTED_SOURCE_INVENTORY_SHA256,
    )
    if v4 != EXPECTED_V4_MACHINE_SHA256:
        raise IdentityError("v4_machine_sha_mismatch")
    if cs != EXPECTED_COMPLETE_STRATEGY_SHA256:
        raise IdentityError("complete_strategy_sha_mismatch")
    if V5_CREATED:
        raise IdentityError("v5_must_not_exist")
    design = {}
    if DESIGN_MANIFEST.is_file():
        design = json.loads(DESIGN_MANIFEST.read_text(encoding="utf-8"))
    if str(design.get("VERDICT") or "") != EXPECTED_DESIGN_VERDICT:
        raise IdentityError("design_verdict_mismatch")
    if str(design.get("NEXT") or "") != EXPECTED_DESIGN_NEXT:
        raise IdentityError("design_next_mismatch")
    md_sha = _file_sha(DESIGN_MD)
    expected_md = str(design.get("DESIGN_MD_SHA256") or "")
    if expected_md and md_sha != expected_md:
        raise IdentityError("design_md_sha_mismatch")
    return {
        "ok": True,
        "DESIGN_ID": DESIGN_ID,
        "SPEC_ID": SPEC_ID,
        "FROZEN_ENTRY_IDENTITY": FROZEN_ENTRY_IDENTITY,
        "V4_MACHINE_SHA256": v4,
        "COMPLETE_STRATEGY_IDENTITY": COMPLETE_STRATEGY_IDENTITY,
        "COMPLETE_STRATEGY_SHA256": cs,
        "UNIVERSE_MANIFEST_SHA256": EXPECTED_UNIVERSE_MANIFEST_SHA256,
        "dataset_role_config_sha256": dataset_role_config_sha256(),
        "firewall_config_sha256": firewall_config_sha256(),
        "contract_schema_sha256": contract_schema_sha256(),
        "design_md_sha256": md_sha,
        "PB1_identity_unchanged": True,
        "complete_strategy_identity_unchanged": True,
        "V5_CREATED": False,
        "source_inventory": frozen_source_hashes(),
    }
