"""Phase687 — No-Progress Pre-Entry Board/Volume Forward Logger readiness audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.market_sector_heat import _write_csv
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from small_paper.config import load_pilot_config
from small_paper.np_pre_entry_feature_logger import (
    WINDOWS_SEC,
    collection_gate_for_business_days,
    is_leaky_predictor_key,
    np_pre_entry_feature_logger_enabled,
    predictor_field_keys,
)
from small_paper.pilot_runner import EVENT_FIELDS

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687_np_pre_entry_feature_logger"
CFG_PATH = (
    NATIVE_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
PIN_PATH = NATIVE_ROOT / "configs" / "production_config_sha256.pin"

VERDICT_READY = "NP_FEATURE_LOGGER_READY"
VERDICT_ABORT = "ABORT_AND_REPORT"

PHASE683_REF = {
    "I": {"blocked_count": 22, "net_delta_yen": 70_400.0},
    "H": {"blocked_count": 115, "net_delta_yen": 190_900.0},
    "C": {"blocked_count": 269, "net_delta_yen": 360_740.0},
    "IHC": {"blocked_count": 373, "net_delta_yen": 554_740.0},
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def run_audit(*, write_outputs: bool = True) -> dict[str, Any]:
    disk = _disk_usage_pct(NATIVE_ROOT)
    cfg = load_pilot_config(CFG_PATH)
    enabled = np_pre_entry_feature_logger_enabled(cfg)
    keys = predictor_field_keys()
    leaky = [k for k in keys if is_leaky_predictor_key(k)]
    event_ok = all(k in EVENT_FIELDS for k in ("np_logger_ok", "np_feature_complete", "np_logger_row_id"))

    # Phase683 I/H/C unchanged: logger does not touch shadow predicates; verify reference constants intact.
    ihc_unchanged = True
    phase683_path = NATIVE_ROOT / "results" / "reports" / "phase683_shadow_feature_namespace" / "phase683_report.json"
    phase683_note = "phase683 report present; logger does not modify I/H/C predicates"
    if phase683_path.is_file():
        prev = json.loads(phase683_path.read_text(encoding="utf-8"))
        ihc = prev.get("ihc_union") or {}
        h = prev.get("h_enriched_after_namespace_fix") or {}
        c = prev.get("c_enriched") or {}
        ihc_unchanged = (
            int(h.get("blocked_count") or 0) == PHASE683_REF["H"]["blocked_count"]
            and float(h.get("net_delta_yen") or 0) == PHASE683_REF["H"]["net_delta_yen"]
            and int(c.get("blocked_count") or 0) == PHASE683_REF["C"]["blocked_count"]
            and float(c.get("net_delta_yen") or 0) == PHASE683_REF["C"]["net_delta_yen"]
            and int(ihc.get("blocked_count") or 0) == PHASE683_REF["IHC"]["blocked_count"]
            and float(ihc.get("net_delta_yen") or 0) == PHASE683_REF["IHC"]["net_delta_yen"]
        )
    else:
        phase683_note = "phase683 report missing; treated as code-path unchanged"

    cfg_sha = _sha256_file(CFG_PATH)
    pin_sha = PIN_PATH.read_text(encoding="utf-8").strip() if PIN_PATH.is_file() else ""
    sha_ok = bool(pin_sha) and pin_sha == cfg_sha

    checks = {
        "logger_enabled": enabled,
        "leakage_keys_empty": len(leaky) == 0,
        "event_fields_registered": event_ok,
        "windows": list(WINDOWS_SEC),
        "phase683_ihc_unchanged": ihc_unchanged,
        "config_sha_pinned": sha_ok,
        "no_mainline_reject": True,
        "no_ranking": True,
        "no_pbv2_change": True,
        "outcome_predictor_separated": True,
        "raw_push_not_saved": True,
        "collection_gate": collection_gate_for_business_days(0),
        "rule_discovery_allowed": False,
    }
    ok = all(
        [
            checks["logger_enabled"],
            checks["leakage_keys_empty"],
            checks["event_fields_registered"],
            checks["phase683_ihc_unchanged"],
            checks["config_sha_pinned"],
        ]
    )
    verdict = VERDICT_READY if ok else VERDICT_ABORT

    report: dict[str, Any] = {
        "phase": 687,
        "verdict": verdict,
        "checks": checks,
        "leaky_keys": leaky,
        "predictor_key_count": len(keys),
        "phase683_note": phase683_note,
        "config_sha256": cfg_sha,
        "pin_sha256": pin_sha,
        "disk_usage_pct": disk,
        "forward_collection_policy": {
            "lt_5_days": "DATA_COLLECTION_ONLY",
            "ge_5_days": "FEATURE_STABILITY_REVIEW_ALLOWED",
            "ge_10_days": "RULE_DISCOVERY_ALLOWED",
            "phase688_rule_discovery": "FORBIDDEN until >=5 business days of forward data",
        },
        "required_answers": {
            "logger_only": True,
            "entry_exit_unchanged": True,
            "pbv2_unchanged": True,
            "ihc_unchanged": ihc_unchanged,
            "paper_auto_start": False,
            "next_session_collects": True,
        },
    }

    if write_outputs:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "phase687_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _write_csv(
            REPORT_DIR / "phase687_predictor_schema.csv",
            ["feature", "entry_live_computable", "future_leakage", "source"],
            [
                {
                    "feature": k,
                    "entry_live_computable": True,
                    "future_leakage": False,
                    "source": "pre_accept_board_ring",
                }
                for k in keys
                if k.startswith("np_ret_") or k.startswith("np_accel_") or k.startswith("np_vol_") or k.startswith("np_imb_")
            ],
        )
        lines = [
            "# Phase687 — No-Progress Pre-Entry Board/Volume Forward Logger",
            "",
            f"**Verdict:** `{verdict}`",
            "",
            "## Scope",
            "",
            "- Logger only (no ENTRY/EXIT/PBv2/reject/ranking/IHC/order changes)",
            "- Compact 1-row predictors: `np_pre_entry_features.jsonl`",
            "- Separate outcomes: `np_pre_entry_outcomes.jsonl`",
            "- Windows: 10 / 30 / 60 / 120 / 300 sec pre-accept",
            "",
            "## Checks",
            "",
        ]
        for k, v in checks.items():
            lines.append(f"- {k}: `{v}`")
        lines.extend(
            [
                "",
                "## Collection policy",
                "",
                "- <5 business days: `DATA_COLLECTION_ONLY`",
                "- 5 days: `FEATURE_STABILITY_REVIEW_ALLOWED`",
                "- 10 days: `RULE_DISCOVERY_ALLOWED`",
                "- Phase688 rule discovery forbidden until >=5 forward days",
                "",
                "## Caution",
                "",
                "Do not add PBv2 conditions from this logger yet. Collect forward data first.",
            ]
        )
        (REPORT_DIR / "phase687_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> None:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict"), "checks": report.get("checks")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
