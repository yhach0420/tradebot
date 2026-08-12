#!/usr/bin/env python
"""Create V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V2 (runtime-only supersede)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
OUT = NATIVE / "results/research/v1r_exit_v2_prospective_activation"
RUNTIME_COMMIT = "68a915ad55bc455f028b25103db40405cf7f89a9"


def _sha(obj: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            {k: v for k, v in obj.items() if k != "sha256"},
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()


def file_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


DEPS = [
    "src/small_paper/v1r_paper_primary_launcher.py",
    "src/small_paper/v1r_exit_v2_activation_gate.py",
    "src/small_paper/v1r_native_entry_live.py",
    "src/small_paper/v1r_live_dual_lane.py",
    "src/small_paper/v1r_pbv2_shadow_discord_digest.py",
    "src/small_paper/v1r_pbv2_notification_routing.py",
    "src/small_paper/v1r_pbv2_duplicate_runtime.py",
    "src/small_paper/v1r_prospective_day_gate.py",
    "src/small_paper/pilot_runner.py",
    "src/small_paper/paper_market_bus_consumer.py",
    "src/small_paper/paper_trade_checked_runner.py",
    "src/small_paper/market_ingress_spawn.py",
    "src/small_paper/discord_notifier.py",
    "src/small_paper/v1r_primary_runtime.py",
    "src/small_paper/v1r_exit_v2_contract.py",
    "src/small_paper/v1r_primary_activation_gate.py",
    "src/notify/v1r_discord_routing.py",
    "src/notify/v1r_discord_embeds.py",
    "src/research/e1_x34a_execution_policy/arms.py",
]


def main() -> None:
    runtime_file_sha256 = {}
    for rel in DEPS:
        p = NATIVE / rel
        assert p.exists(), rel
        runtime_file_sha256[rel.replace("\\", "/")] = file_sha256(p)

    for name, exp in [
        (
            "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY.json",
            "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547",
        ),
        (
            "PROSPECTIVE_PRECOMMIT_V1R_EXIT_V2_U1.json",
            "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100",
        ),
        (
            "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V1.json",
            "29cbc5933421319ffcb1ed24d9be517d35e74c1027ebe67df431657c6997ada1",
        ),
    ]:
        obj = json.loads((OUT / name).read_text(encoding="utf-8"))
        assert obj["sha256"] == exp == _sha(obj), (name, obj["sha256"], _sha(obj))

    activation = {
        "manifest_id": "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V2",
        "parent_activation_id": "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V1",
        "parent_activation_sha": "29cbc5933421319ffcb1ed24d9be517d35e74c1027ebe67df431657c6997ada1",
        "parent_activation_status": "SUPERSEDED_RUNTIME_ONLY_BEFORE_VALID_PROSPECTIVE_START",
        "supersede_reason": (
            "Not a Strategy change. 2026-08-12 discovered Runtime wiring defects; "
            "Prospective must use corrected Runtime identity (Ingress received_at fill clock, "
            "v1r_native Primary ENTRY, dual-lane, Discord/PBv2 isolation)."
        ),
        "runtime_roles": {
            "primary": "PAPER_PRIMARY",
            "strategy": "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY",
            "strategy_sha": "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547",
            "entry_source": "v1r_native",
            "entry_manifest": "PASSIVE_FILL_ENTRY_V1",
            "control_fixed600": "SHADOW_CONTROL",
            "control_strategy": "PASSIVE_FIXED600_FULL_STRATEGY_V1R",
            "control_strategy_sha": "dfd311d4dc32a802b8e55f6d28d75a2db12d4192a71fb53b48d5308573a58e0a",
            "pbv2": "SHADOW_ONLY",
            "capital_1m": "SHADOW_ONLY_DIAGNOSTIC",
        },
        "strategy_sha": "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547",
        "precommit_sha": "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100",
        "entry_strategy_sha": "dfd311d4dc32a802b8e55f6d28d75a2db12d4192a71fb53b48d5308573a58e0a",
        "entry_sha": "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29",
        "execution_sha": "040fa4b061e575d3f6cdb2a11ffd3f862da5351b298567b31363de923a590869",
        "model_sha": "f63f7f88e9ff6ea5b84a89b0949baa76166d697525620287a7c230f821e7356b",
        "universe_binding_sha": "45b2fb20d02abbe7d557a55fecc87da3e7c19126eb7415ce9bdc4579aca39fee",
        "universe_contract": "DAY_FIXED_AM_RUNTIME_UNIVERSE_V1",
        "anchor_sha": "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2",
        "exit_v2_candidate_sha": "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255",
        "exit_contract_sha": "9e3494c38e8040acb47ecbf057d6b0bbaa25682492308e7335ae74c1d47d4b19",
        "control_strategy_sha": "dfd311d4dc32a802b8e55f6d28d75a2db12d4192a71fb53b48d5308573a58e0a",
        "control_exit_sha": "2c9fcc6e92971c252c8df93716066dda515fcbff0283d748b03293379c5eb62c",
        "guard": {
            "id": "IMB_p5_t-10",
            "persist_sec": 5.0,
            "imbalance_lte": -0.1,
            "monitor_to_sec": 120.0,
        },
        "continuation": {
            "id": "MFE60_IMB10",
            "mfe_gte_bps": 60.0,
            "imbalance_gte": 0.1,
        },
        "cap": 5,
        "qty": 100,
        "wait_sec": 1.0,
        "freshness_sec": 5.0,
        "runtime_code_git_commit": RUNTIME_COMMIT,
        "runtime_file_sha256": runtime_file_sha256,
        "runtime_contracts": {
            "live_residency": {
                "launcher_stub_forbidden": True,
                "path": "launcher -> daily_runner/pilot -> MarketBus consumer",
                "premature_exit_fail": True,
            },
            "primary_entry_source": {
                "source": "v1r_native",
                "pbv2_hitchhike_forbidden": True,
                "non_v1r_native_primary_admission_forbidden": True,
                "pbv2_fallback_forbidden": True,
                "native_unavailable_fail_closed": True,
            },
            "universe_contract": {
                "id": "DAY_FIXED_AM_RUNTIME_UNIVERSE_V1",
                "am_core10_dynamic40": 50,
                "same_membership_all_16_anchors": True,
                "pm_refresh_membership_change_forbidden": True,
                "prior_day_fallback_forbidden": True,
                "ingress_registration_50_50_required": True,
            },
            "native_trace": {
                "trace_dir": "writer.output_dir",
                "anchor_no_candidate_disk_record": True,
            },
            "symbol_contract": {
                "canonical_format": "bare_code",
                "equivalence": ["6098", "6098.T", "6098"],
                "single_canonical_function_required": True,
            },
            "fill_snapshot_bind": [
                "event_time",
                "Buy1.price",
                "Buy1.qty",
                "Sell1.price",
                "Sell1.qty",
                "CurrentPrice",
                "freshness",
                "special_quote",
                "imbalance",
            ],
            "dual_lane": {
                "primary": "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY / PAPER_PRIMARY",
                "control": "PASSIVE_FIXED600_FULL_STRATEGY_V1R / SHADOW_CONTROL",
                "same_v1r_native_fill_start": True,
                "independent_occupancy_after_fill": True,
                "slot_release_on_actual_exit_only": True,
                "trigger_time_release_forbidden": True,
            },
            "timestamp_contract": {
                "canonical_live_board_fill_clock": "Ingress received_at",
                "t0": "fixed_anchor_minute_floor_signal_time",
                "window": "[t0, t0+1.0s] inclusive",
                "order": "find_ask_cross_fill first; EXPIRE only if still pending and t_now >= lim_t",
                "consumer_time_time_as_fill_clock_forbidden": True,
                "wait_price_qty_fresh_special_same_session_unchanged": True,
                "lineage_verdict": "V1R_PASSIVE_FILL_TIMESTAMP_LINEAGE_RECONCILED_RUNTIME_ONLY",
                "known_limitation_14d_capture_remap": "NOT_PROVEN",
            },
            "discord_contract": {
                "v1r_pending_expired": "trade-entry",
                "v1r_fill_exit": "trade-notify",
                "pbv2": "trade-research / SHADOW_ONLY",
                "silent_exception_forbidden": True,
                "delivery_errors_audit_required": True,
            },
            "pbv2_digest_contract": {
                "internal_eval_continues": True,
                "per_push_discord_forbidden": True,
                "digest_cadence": "5m_or_fixed_anchor",
                "primary_occupancy_mutation": 0,
            },
            "fail_closed_contract": {
                "native_not_ready_blocks_primary": True,
                "activation_sha_mismatch_blocks_primary": True,
                "duplicate_ingress_spawn_rejected": True,
            },
        },
        "evidence_status": {
            "overall_e2e_evidence": "V1R_PAPER_TRADE_E2E_RUNTIME_PARTIALLY_PROVEN",
            "LIVE_PROVEN": [
                "Market Ingress",
                "Capture",
                "Universe 50",
                "AM/PM same membership",
                "Fixed Anchor",
                "candidate/admission",
                "PENDING",
                "EXPIRED",
                "Discord PENDING/EXPIRED",
                "PBv2 isolation",
                "PBv2 digest",
                "heartbeat",
                "AM->PM lifecycle",
                "session close",
                "submit/cancel/live=0/0/0",
            ],
            "LIVE_REPLAY_PARITY_PROVEN": {
                "passive_fill_corrected_semantics": True,
                "day": "20260812_PM",
                "research": {"pending": 40, "fills": 7, "expired": 33},
                "corrected_runtime": {"pending": 40, "fills": 7, "expired": 33},
                "match": "40/40",
                "fill_time_price_match": True,
            },
            "DEMO_REGRESSION_PROVEN": [
                "Guard EXIT",
                "Primary 600 EXIT",
                "Primary 750 extension->EXIT",
                "FIXED600 Control EXIT",
                "actual EXIT slot release",
                "Discord FILL",
                "Discord EXIT",
                "6098/6098.T canonical regression",
            ],
            "NOT_YET_LIVE_PROVEN_AFTER_ALL_FIXES": [
                "one healthy real-market trade: PENDING->FILL->Discord FILL->Primary+Control->Guard/600/750->EXIT->Discord EXIT->slot release"
            ],
            "MANDATORY_FIRST_LIVE_FILL_EXIT_WATCH": True,
        },
        "known_limitations": [
            "full_14_day_Capture_remap=NOT_PROVEN",
            "early_Capture_coverage_incomplete_or_strict_join_unavailable_on_some_days",
            "no_post_fix_healthy_live_FILL_EXIT_chain_yet",
        ],
        "invalid_prospective_days": {
            "20260810": {
                "status": "RETROSPECTIVE_REFERENCE",
                "prospective_count_forbidden": True,
            },
            "20260811": {
                "status": "INVALID_RUNTIME_NOT_ACTUALLY_PROSPECTIVE",
                "reason": (
                    "Old V1 live launcher/runtime did not process live market correctly; "
                    "not a valid Prospective day."
                ),
                "supersedes_prior_label": "20260811_CLEAN_PROSPECTIVE_ELIGIBLE",
                "prior_label_note": (
                    "Prior label was pre-check (unused market data); later runtime-stub "
                    "findings invalidate Prospective credit. Original report not mutated."
                ),
            },
            "20260812": {
                "status": "INVALID / OPERATIONAL_VALIDATION_ONLY",
                "reasons": [
                    "PBv2 Primary contamination",
                    "empty native universe",
                    "symbol key EXIT mismatch",
                    "Discord notification bug",
                    "PBv2 Discord flood",
                    "Passive Fill consumer-wall-clock parity bug",
                    "runtime fixes applied during the day",
                ],
                "late_day_recovery_not_prospective": True,
            },
        },
        "prospective_evidence_days": 0,
        "day1_rule": {
            "id": "FIRST_FULLY_UNSEEN_TRADING_DAY_AFTER_ACTIVATION_V2_FREEZE",
            "requirements": [
                "Activation V2 freeze complete",
                "market data for that day not used in prior research",
                "no Strategy change",
                "no Runtime change after this freeze",
                "startup identity exact match to V2",
                "submit/cancel/live=0/0/0",
                "activation parity PASS before first anchor",
            ],
            "fixed_calendar_date_not_speculated": True,
        },
        "paper_only": True,
        "order_enabled": False,
        "live_trading_enabled": False,
        "submit_cancel_live": "0/0/0",
        "no_fallback_to_fixed600_primary": True,
        "no_fallback_to_pbv2_primary": True,
        "created_at": datetime.now(JST).isoformat(),
    }
    activation["sha256"] = _sha(activation)
    path = OUT / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V2.json"
    path.write_text(json.dumps(activation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["sha256"] == _sha(loaded)

    report = {
        "verdict": "V1R_EXIT_V2_RUNTIME_ACTIVATION_V2_FROZEN",
        "ready_label": "PROSPECTIVE_DAY1_READY_WITH_MANDATORY_FIRST_FILL_EXIT_WATCH",
        "activation_id": "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V2",
        "activation_sha": loaded["sha256"],
        "parent_activation_sha": loaded["parent_activation_sha"],
        "runtime_code_git_commit": RUNTIME_COMMIT,
        "runtime_file_hash_count": len(runtime_file_sha256),
        "strategy_sha_unchanged": True,
        "precommit_sha_unchanged": True,
        "old_activation_v1_unchanged": True,
        "prospective_evidence_days": 0,
        "created_at": loaded["created_at"],
    }
    (OUT / "report_activation_v2.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    md_lines = [
        "# V1R EXIT V2 Paper Primary Activation V2",
        "",
        f"**Verdict:** `{report['verdict']}`",
        "",
        f"**Ready:** `{report['ready_label']}`",
        "",
        "| Field | Value |",
        "|--|--|",
        f"| Activation ID | {report['activation_id']} |",
        f"| Activation SHA | {report['activation_sha']} |",
        f"| Parent Activation SHA | {report['parent_activation_sha']} |",
        f"| Runtime git commit | {RUNTIME_COMMIT} |",
        f"| Runtime file hashes | {len(runtime_file_sha256)} |",
        "| Prospective evidence days | 0 |",
        "| Day1 rule | FIRST_FULLY_UNSEEN_TRADING_DAY_AFTER_ACTIVATION_V2_FREEZE |",
        "| submit/cancel/live | 0/0/0 |",
        "",
        "Strategy / Precommit / Activation V1 SHAs verified unchanged.",
        "",
    ]
    (OUT / "report_activation_v2.md").write_text("\n".join(md_lines), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
