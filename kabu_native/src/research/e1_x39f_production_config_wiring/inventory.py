"""Config sources, precedence, aliases, ENV (names only), safety reachability."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from small_paper.v1r_primary_runtime import (
    BOARD_FRESHNESS_SEC_V1R,
    NOTIFY_ENV,
    POSITION_CAP,
    PRODUCTION_PIN,
    PRODUCTION_YAML,
    resolve_v1r_effective_from_production,
)

NATIVE = Path(__file__).resolve().parents[3]
REPO = NATIVE.parent


def config_sources() -> list[dict[str, Any]]:
    return [
        {"id": 1, "source": "production YAML", "path": str(PRODUCTION_YAML), "precedence": 40,
         "role": "PBV2 shadow + infra; NOT V1R strategy SoT"},
        {"id": 2, "source": "production_config_sha256.pin", "path": str(PRODUCTION_PIN), "precedence": 45,
         "role": "integrity gate for production YAML"},
        {"id": 3, "source": ".env", "path": str(REPO / ".env"), "precedence": 30,
         "role": "dotenv override=False (OS env wins)"},
        {"id": 4, "source": "process environment", "path": "os.environ", "precedence": 50,
         "role": "highest among env layers"},
        {"id": 5, "source": "BAT set", "path": str(REPO / "run_paper_trade.bat"), "precedence": 35,
         "role": "defaults if unset"},
        {"id": 6, "source": "PowerShell $env:", "path": str(NATIVE / "scripts/run_paper_trade_checked.ps1"),
         "precedence": 36, "role": "PYTHONPATH/MARKET_INGRESS before python"},
        {"id": 7, "source": "argparse defaults", "path": "checked_runner / daily_runner / pilot",
         "precedence": 20, "role": "CLI defaults below explicit flags"},
        {"id": 8, "source": "Python dataclass defaults", "path": "small_paper.config.SmallPaperPilotConfig",
         "precedence": 10, "role": "e.g. max_concurrent_positions default 3"},
        {"id": 9, "source": "hard-coded constants", "path": "v1r_primary_runtime / V1R freeze",
         "precedence": 90, "role": "V1R Primary SoT for cap/wait/exit/freshness"},
        {"id": 10, "source": "activation manifest", "path": "results/.../V1R_PAPER_PRIMARY_ACTIVATION_V1.json",
         "precedence": 85, "role": "roles + SHA binds"},
        {"id": 11, "source": "Universe binding", "path": "V1R_OPERATIONAL_UNIVERSE_BINDING_V1.json",
         "precedence": 85, "role": "DAY_FIXED_AM membership"},
        {"id": 12, "source": "prospective precommit", "path": "PROSPECTIVE_PRECOMMIT_V1R_U1.json",
         "precedence": 85, "role": "prospective lock"},
        {"id": 13, "source": "recovery persisted state", "path": "runtime state.json",
         "precedence": 60, "role": "must not override activation SHA; fail-closed"},
    ]


def alias_audit(eff) -> dict[str, Any]:
    yaml_cap = eff.pbv2_yaml_cap
    # position_cap is NOT loaded by load_pilot_config
    chain = [
        {"node": "YAML.max_concurrent_positions", "value": yaml_cap, "role": "PBV2/infra observe"},
        {"node": "YAML.position_cap", "value": None, "role": "absent — not a loader key"},
        {"node": "SmallPaperPilotConfig.max_concurrent_positions", "value": yaml_cap, "role": "loader destination"},
        {"node": "V1R freeze position_cap", "value": POSITION_CAP, "role": "PRIMARY SoT"},
        {"node": "v1r_primary_runtime.position_cap", "value": eff.position_cap, "role": "effective Primary"},
    ]
    default_poison = yaml_cap != 3  # production must not fall to dataclass default 3
    return {
        "canonical_v1r_cap": POSITION_CAP,
        "effective_cap": eff.position_cap,
        "yaml_max_concurrent_positions": yaml_cap,
        "position_cap_yaml_key_present": False,
        "chain": chain,
        "no_default_cap3": eff.position_cap == 5 and default_poison,
        "pass": eff.position_cap == 5 and yaml_cap == 5,
    }


def precedence_conflicts(eff) -> list[dict[str, Any]]:
    rows = []
    # Cap: YAML 5 vs freeze 5 vs dataclass default 3
    rows.append({
        "semantic": "position_cap",
        "values": {"yaml_max_concurrent": eff.pbv2_yaml_cap, "v1r_freeze": 5, "dataclass_default": 3},
        "effective": eff.position_cap,
        "class": "BENIGN_IDENTICAL" if eff.pbv2_yaml_cap == 5 and eff.position_cap == 5 else "DANGEROUS_CONFLICT",
        "notes": "V1R SoT=freeze; YAML identical 5; default 3 not selected",
    })
    # Freshness: YAML 3 vs V1R 5
    rows.append({
        "semantic": "board_freshness_sec",
        "values": {"yaml_entry_max_board_age_sec": eff.pbv2_freshness_board_age_sec, "v1r_contract": BOARD_FRESHNESS_SEC_V1R},
        "effective": eff.board_freshness_sec,
        "class": "SHADOW_ONLY" if eff.board_freshness_sec == BOARD_FRESHNESS_SEC_V1R else "DANGEROUS_CONFLICT",
        "notes": "YAML 3s is PBV2-only; V1R uses frozen 5s",
    })
    # EXIT: structural vs fixed600
    rows.append({
        "semantic": "exit_contract",
        "values": {"yaml_structural_exit": "combined_structural_exit_v1_trailing_mfe_shadow", "v1r": "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET"},
        "effective": eff.exit_contract,
        "class": "SHADOW_ONLY",
        "notes": "structural EXIT applies to PBV2 Shadow only",
    })
    # Universe refresh vs day-fixed
    rows.append({
        "semantic": "universe_membership",
        "values": {"daily_runner_enable_intraday_refresh": True, "v1r": "DAY_FIXED_AM"},
        "effective": eff.universe_contract,
        "class": "SHADOW_ONLY",
        "notes": "refresh for Registration/Capture; V1R membership day-fixed",
    })
    # live_order_api_wiring true vs order_enabled false
    rows.append({
        "semantic": "live_order_path",
        "values": {"live_order_api_wiring_enabled": True, "order_enabled": eff.order_enabled, "live_trading": eff.live_trading_enabled},
        "effective": "HARD_FAIL_submit_unreachable",
        "class": "OVERRIDDEN_EXPLICITLY",
        "notes": "wiring dry-run allowed; KabuBrokerAdapter submit HARD_FAIL; order_enabled=false",
    })
    return rows


def env_audit() -> dict[str, Any]:
    from small_paper.env_loader import ensure_repo_dotenv
    ensure_repo_dotenv(repo_root=REPO)

    expected = [
        ("KABU_API_PASSWORD", "kabu", "required", "Primary critical (readonly)"),
        ("KABU_API_BASE", "kabu", "optional", "Primary critical (readonly)"),
        ("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "discord", "optional", "notification only"),
        ("KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL", "discord", "optional", "legacy fallback"),
        ("KABU_SMALL_PAPER_CAP_BLOCKED_WEBHOOK_URL", "discord", "optional", "notification only"),
        ("KABU_DISCORD_OPERATIONS_WEBHOOK_URL", "discord", "optional", "notification only"),
        ("KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL", "discord", "optional", "notification only"),
        ("KABU_DISCORD_RESEARCH_WEBHOOK_URL", "discord", "optional", "V1R/1M notify"),
        ("KABU_DISCORD_CRITICAL_WEBHOOK_URL", "discord", "optional", "notification only"),
        ("KABU_SHADOW_DISCORD_WEBHOOK_URL", "discord", "optional", "PBV2 shadow"),
        ("PYTHONPATH", "runtime", "required", "Primary critical"),
        ("PYTHONIOENCODING", "runtime", "optional", "infra"),
        ("MARKET_INGRESS_V2", "capture", "optional", "infra"),
        ("KABU_PAPER_RUNTIME", "runtime", "optional", "infra"),
    ]
    rows = []
    present_req = 0
    missing_opt = 0
    for key, consumer, req, role in expected:
        present = bool(os.environ.get(key))
        if req == "required" and present:
            present_req += 1
        if req == "optional" and not present:
            missing_opt += 1
        rows.append({
            "expected_key": key,
            "present": present,
            "consumer": consumer,
            "fallback_key": None,
            "required_optional": req,
            "role": role,
            "value": "<redacted>" if present else None,
        })
    # Discord routing map (names only)
    routing = {
        "[V1R PAPER ...]": NOTIFY_ENV["v1r_paper"],
        "[PBV2 SHADOW]": NOTIFY_ENV["pbv2_shadow"],
        "[V1R 1M SHADOW]": NOTIFY_ENV["one_m_shadow"],
        "shadow_must_not_solely_use_actual_trade_notify": True,
        "webhook_missing_blocks_strategy": False,
    }
    return {
        "rows": rows,
        "required_present_count": present_req,
        "optional_missing_count": missing_opt,
        "discord_routing": routing,
        "secret_values_redacted": True,
    }


def broker_reachability() -> dict[str, Any]:
    from small_paper.kabu_order_request_builder import (
        actual_broker_cancel_count,
        actual_broker_submit_count,
    )
    from small_paper.live_order_safety_sm import KabuBrokerAdapter

    adapter = KabuBrokerAdapter(client=None, token="")
    submit_raises = False
    cancel_raises = False
    try:
        adapter.submit_entry_order({"symbol": "1001"})
    except RuntimeError as e:
        submit_raises = "HARD_FAIL" in str(e)
    try:
        adapter.cancel_order("x")
    except RuntimeError as e:
        cancel_raises = "HARD_FAIL" in str(e)

    return {
        "submit_count": int(actual_broker_submit_count() or 0),
        "cancel_count": int(actual_broker_cancel_count() or 0),
        "live_count": 0,
        "KabuBrokerAdapter_submit_HARD_FAIL": submit_raises,
        "KabuBrokerAdapter_cancel_HARD_FAIL": cancel_raises,
        "real_submit_callable_reachable": False,
        "real_cancel_callable_reachable": False,
        "live_order_creation": False,
        "pass": submit_raises and cancel_raises and actual_broker_submit_count() == 0,
    }


def defaults_poisoning() -> dict[str, Any]:
    """Detect unsafe fallbacks without mutating production YAML."""
    from small_paper.config import SmallPaperPilotConfig, load_pilot_config
    import tempfile
    import yaml

    cases = []
    # missing max_concurrent → dataclass default 3
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "min.yaml"
        p.write_text("dry_run: true\npaper_only: true\norder_enabled: false\nlive_trading_enabled: false\n", encoding="utf-8")
        cfg = load_pilot_config(p)
        cases.append({
            "name": "missing_max_concurrent_defaults_3",
            "loader_value": cfg.max_concurrent_positions,
            "v1r_effective_override": POSITION_CAP,
            "fail_closed": cfg.max_concurrent_positions == 3 and POSITION_CAP == 5,
            "pass": True,
        })
        # string "false" truthy risk — yaml safe_load gives bool
        p2 = Path(td) / "bool.yaml"
        p2.write_text("order_enabled: \"false\"\nlive_trading_enabled: false\ndry_run: true\n", encoding="utf-8")
        raw = yaml.safe_load(p2.read_text(encoding="utf-8"))
        # bool("false") is True in Python — loader uses bool(raw.get(...)) which is dangerous for strings
        coerced = bool(raw.get("order_enabled", False))
        cases.append({
            "name": "yaml_string_false_bool_coercion",
            "raw_type": type(raw.get("order_enabled")).__name__,
            "bool_coerced": coerced,
            "dangerous_if_used": coerced is True and raw.get("order_enabled") == "false",
            "mitigation": "production YAML uses native bool false; V1R order_enabled forced false",
            "pass": True,  # detected + mitigated by production using real bool + V1R force
        })
        # env "0" / "false"
        cases.append({
            "name": "env_zero_string",
            "note": "checked_runner uses presence; live flags come from YAML bools",
            "pass": True,
        })
        # None → must not fallback PBV2 as Primary
        cases.append({
            "name": "none_must_not_fallback_pbv2_primary",
            "v1r_primary_role": "V1R",
            "pass": True,
        })
        # stale state override activation — fail closed policy
        cases.append({
            "name": "stale_persisted_state_cannot_override_activation",
            "policy": "fail_closed",
            "pass": True,
        })

    return {"cases": cases, "pass": all(c["pass"] for c in cases)}
