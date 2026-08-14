"""V1R Activation binding — selector / inventory / hash SoT (no Strategy semantics).

Breaks the freeze cycle:
  hardcoded gate ACTIVATION_SHA ↔ manifest.runtime_file_sha256[gate]

SoT:
  A) active_v1r_activation.json  → expected activation_id / activation_sha
  B) activation manifest         → self sha256
  C) manifest.runtime_file_sha256 → working-tree Path.read_bytes() SHA256
  D) Strategy / Precommit / roles from manifest (verified by gate)

Hash policy (single SoT): actual working-tree bytes via Path.read_bytes().
No LF/CRLF normalization on either freeze or startup path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results/research/v1r_exit_v2_prospective_activation"
SELECTOR_NAME = "active_v1r_activation.json"
SELECTOR_PATH = OUT / SELECTOR_NAME
SELECTOR_SCHEMA = "V1R_ACTIVE_ACTIVATION_SELECTOR_V1"

# Strategy Runtime dependency inventory (binding selector is NOT included).
# Gate is included: after binding refactor it must not hard-code activation SHA,
# so selector bind does not mutate inventory bytes.
RUNTIME_DEPENDENCY_RELS: tuple[str, ...] = (
    "src/small_paper/v1r_activation_binding.py",
    "src/small_paper/v1r_paper_primary_launcher.py",
    "src/small_paper/v1r_exit_v2_activation_gate.py",
    "src/small_paper/v1r_native_entry_live.py",
    "src/small_paper/v1r_live_dual_lane.py",
    "src/small_paper/v1r_pbv2_shadow_discord_digest.py",
    "src/small_paper/v1r_pbv2_notification_routing.py",
    "src/small_paper/v1r_pbv2_duplicate_runtime.py",
    "src/small_paper/v1r_prospective_day_gate.py",
    "src/small_paper/runtime_clock.py",
    "src/small_paper/paper_full_day_certification.py",
    "src/small_paper/pilot_runner.py",
    "src/small_paper/paper_market_bus_consumer.py",
    "src/small_paper/paper_trade_checked_runner.py",
    "src/small_paper/market_ingress_spawn.py",
    "src/small_paper/ingress_run_identity.py",
    "src/small_paper/derived_artifact_contract.py",
    "src/small_paper/discord_notifier.py",
    "src/small_paper/v1r_primary_runtime.py",
    "src/small_paper/v1r_exit_v2_contract.py",
    "src/small_paper/v1r_primary_activation_gate.py",
    "src/notify/v1r_discord_routing.py",
    "src/notify/v1r_discord_embeds.py",
    "src/research/e1_x34a_execution_policy/arms.py",
    "src/small_paper/day_fixed_am_registration.py",
    "src/small_paper/kabu_registration_authority.py",
    "src/small_paper/kabu_token_authority.py",
    "src/small_paper/kabu_readonly_readiness.py",
    "src/api/rest_client.py",
    "src/small_paper/ingress_control_channel.py",
    "src/small_paper/market_capture_registration.py",
    "src/small_paper/market_ingress_service.py",
    "src/small_paper/consumer_ack_state.py",
    "src/small_paper/evaluation_reachability.py",
    "src/small_paper/live_writer.py",
    "src/small_paper/consumer_push_telemetry.py",
    "src/api/kabu_register.py",
    "src/small_paper/safety.py",
    "src/small_paper/registration_lifetime.py",
    "src/runner/am_pm_daily_runner.py",
    "src/universe/core10_dynamic40_price_risk.py",
)

# Forbidden in runtime inventory (would reintroduce selector↔manifest cycles).
FORBIDDEN_INVENTORY_NAMES: frozenset[str] = frozenset(
    {
        SELECTOR_NAME,
        "active_v1r_activation.json",
        "runtime_pins.json",
    }
)


def file_sha256(path: Path) -> str:
    """SoT: SHA256 of actual working-tree bytes (Path.read_bytes). No newline normalize."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_content_sha(obj: Mapping[str, Any]) -> str:
    """Canonical activation/strategy body SHA (excludes sha256 field)."""
    payload = {k: v for k, v in obj.items() if k != "sha256"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def collect_runtime_inventory(
    *,
    native_root: Optional[Path] = None,
    rels: Optional[Sequence[str]] = None,
) -> dict[str, str]:
    root = Path(native_root or NATIVE)
    out: dict[str, str] = {}
    for rel in rels or RUNTIME_DEPENDENCY_RELS:
        rel_n = rel.replace("\\", "/")
        name = Path(rel_n).name
        if name in FORBIDDEN_INVENTORY_NAMES or rel_n.endswith(SELECTOR_NAME):
            raise RuntimeError(f"selector/pins must not enter runtime inventory: {rel_n}")
        p = root / rel_n
        if not p.is_file():
            raise FileNotFoundError(rel_n)
        out[rel_n] = file_sha256(p)
    return out


def load_active_selector(*, path: Optional[Path] = None) -> dict[str, Any]:
    p = Path(path or SELECTOR_PATH)
    if not p.is_file():
        raise FileNotFoundError(f"active activation selector missing: {p}")
    body = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError("selector must be a JSON object")
    aid = str(body.get("activation_id") or "").strip()
    ash = str(body.get("activation_sha") or "").strip()
    if not aid or not ash:
        raise ValueError("selector requires activation_id and activation_sha")
    # Selector is identity-only — reject trading-condition keys if smuggled in.
    forbidden_econ = {
        "strategy_sha",
        "precommit_sha",
        "guard",
        "continuation",
        "wait_sec",
        "cap",
        "qty",
        "runtime_file_sha256",
    }
    bad = sorted(k for k in forbidden_econ if k in body)
    if bad:
        raise ValueError(f"selector must not carry economic/runtime fields: {bad}")
    return body


def resolve_manifest_path(
    selector: Mapping[str, Any],
    *,
    out_dir: Optional[Path] = None,
) -> Path:
    base = Path(out_dir or OUT)
    rel = str(selector.get("manifest_relpath") or "").strip()
    if rel:
        p = base / rel
    else:
        p = base / f"{selector['activation_id']}.json"
    return p


def load_activation_manifest(
    *,
    selector: Optional[Mapping[str, Any]] = None,
    path: Optional[Path] = None,
    out_dir: Optional[Path] = None,
) -> dict[str, Any]:
    if path is not None:
        p = Path(path)
    else:
        sel = selector if selector is not None else load_active_selector()
        p = resolve_manifest_path(sel, out_dir=out_dir)
    if not p.is_file():
        raise FileNotFoundError(f"activation manifest missing: {p}")
    body = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError("activation manifest must be a JSON object")
    return body


def verify_manifest_self_sha(manifest: Mapping[str, Any]) -> tuple[bool, str, str]:
    got = str(manifest.get("sha256") or "")
    calc = manifest_content_sha(manifest)
    return got == calc and bool(got), got, calc


def verify_runtime_inventory(
    manifest: Mapping[str, Any],
    *,
    native_root: Optional[Path] = None,
) -> dict[str, Any]:
    inv = manifest.get("runtime_file_sha256") or {}
    if not isinstance(inv, dict) or not inv:
        return {
            "ok": False,
            "reason": "runtime_file_sha256_missing",
            "expected_n": 0,
            "matched": 0,
            "mismatches": [],
        }
    # Reject selector/pins self-reference inside inventory
    for rel in inv:
        name = Path(str(rel)).name
        if name in FORBIDDEN_INVENTORY_NAMES:
            return {
                "ok": False,
                "reason": f"forbidden_inventory_entry:{rel}",
                "expected_n": len(inv),
                "matched": 0,
                "mismatches": [str(rel)],
            }
    root = Path(native_root or NATIVE)
    mismatches: list[dict[str, str]] = []
    matched = 0
    for rel, exp in inv.items():
        rel_n = str(rel).replace("\\", "/")
        p = root / rel_n
        if not p.is_file():
            mismatches.append({"path": rel_n, "reason": "missing", "expected": str(exp), "got": ""})
            continue
        got = file_sha256(p)
        if got != str(exp):
            mismatches.append(
                {"path": rel_n, "reason": "hash_mismatch", "expected": str(exp), "got": got}
            )
        else:
            matched += 1
    return {
        "ok": len(mismatches) == 0 and matched == len(inv),
        "reason": "" if not mismatches else "runtime_inventory_mismatch",
        "expected_n": len(inv),
        "matched": matched,
        "mismatches": mismatches,
    }


def verify_selector_binding(
    selector: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, bool]:
    mid = str(manifest.get("manifest_id") or manifest.get("activation_id") or "")
    return {
        "activation_id_match": str(selector.get("activation_id") or "") == mid,
        "activation_sha_match": str(selector.get("activation_sha") or "")
        == str(manifest.get("sha256") or ""),
    }
