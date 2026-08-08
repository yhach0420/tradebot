"""Fixed constants for E1_X6 provisional P0→P2 (locked before economics)."""
from __future__ import annotations

from pathlib import Path

PROVISIONAL_BANNER = "PROVISIONAL_NOT_FOR_SELECTION"
FINAL_BANNER = "FINAL_9DAY_INTERNAL_RESEARCH_NOT_FORWARD"
PLAN_REL = Path("kabu_native/kabu_native/docs/e1_x6_validation_plan.md")
ARTIFACT_DIR_REL = Path("kabu_native/results/research/e1_x6_redesign_20260721_20260731")

DAYS = (
    "20260721",
    "20260722",
    "20260723",
    "20260724",
    "20260727",
    "20260728",
    "20260729",
    "20260730",
    "20260731",
)

# Paper expected windows (JST clock)
AM_EXPECTED = ("09:03", "11:25")
PM_EXPECTED = ("12:33", "15:23")

DEDUP_KEY_RULE = "session_id|sequence|symbol|event_time"

# From e1_x5_forward_shadow — copied into P1 lock, not re-derived after lock
from small_paper.e1_x5_forward_shadow import (  # noqa: E402
    STOP_BPS,
    TARGET_BPS,
    THRESHOLD,
)
from research.e1_x6_provisional.cost_contract import (  # noqa: E402
    COST_RATE,
    LOT,
    ROUNDTRIP_COST_BPS,
)

PRIMARY_HORIZON_SEC = 300
# Alias: single 5bps round-trip (NOT COST_RATE*2)
COST_BPS_ROUNDTRIP = ROUNDTRIP_COST_BPS

# Quantile grid for threshold generation (build window only)
QUANTILE_GRID = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
CANDIDATE_CAP = 200
INTERACTION_MAX = 2
OPTIONAL_THREE_PLUS = "DISABLED"

PREDICTOR_FEATURES = ("score", "spread_bps", "score_vs_threshold_gap")
# symbol_norm is whitelist for identity/join only — NOT a predictor
WHITELIST_FIELDS = (
    "score",
    "spread_bps",
    "bid",
    "ask",
    "mid",
    "sample_reason",
    "symbol_norm",
    "score_vs_threshold_gap",
)

FOLD_DEFS = {
    "F1": {"build": ["20260721", "20260722", "20260723", "20260724"], "confirm": ["20260727"]},
    "F2": {
        "build": ["20260721", "20260722", "20260723", "20260724", "20260727"],
        "confirm": ["20260728"],
    },
    "F3": {
        "build": ["20260721", "20260722", "20260723", "20260724", "20260727", "20260728"],
        "confirm": ["20260729"],
    },
    "F4": {
        "build": [
            "20260721",
            "20260722",
            "20260723",
            "20260724",
            "20260727",
            "20260728",
            "20260729",
        ],
        "confirm": ["20260730"],
    },
    "F5": {
        "build": [
            "20260721",
            "20260722",
            "20260723",
            "20260724",
            "20260727",
            "20260728",
            "20260729",
            "20260730",
        ],
        "confirm": ["20260731"],
        "note": "F5 confirm enabled for final 9-day Source Manifest (20260731)",
    },
}

# Preferred sealed PARTIAL session for 20260727 (ingress)
DAY27_PREFERRED_SESSION = "session_ing_20260727_11752_1785113581_4db3b030"

SUPERSEDED_PRIOR_RUN = {
    "run_id": "e1x6_prov_20260730_205446_fc7b050c",
    "reason": "SUPERSEDED_PRE_ECONOMICS — prior scaffold lacked real BASE/dataset/candidate economics",
    "shas": {
        "p1_lock_sha256": "109644b3ab5c0a0337866700ff7560a025d0f9995813c5bfb0f868ca9767201d",
    },
}

# Do not adopt; do not overwrite its 3 artifacts. Disposition recorded separately.
SUPERSEDED_SEMANTIC_CONTRACT_RUN = {
    "run_id": "e1x6_prov_20260731_002641_4f16e112",
    "disposition": "SUPERSEDED_SEMANTIC_CONTRACT_ERROR",
    "artifact_internal_integrity": "PASS",
    "determinism": "PASS_ON_CURRENT_IMPLEMENTATION",
    "P0": "HASH_VALID_BUT_WINDOW_AUDIT_INCOMPLETE",
    "P1": "LOCK_HASH_VALID_BUT_COST_CONTRACT_INVALID",
    "P2": "BASE_REPLAY_EXECUTED / FOLD_ROW_LEVEL_ECONOMICS_ONLY",
    "reason": (
        "cost used COST_RATE*2 (10bps dual) while plan/E1_X5 require 5bps once; "
        "PARTIAL aggregate mislabeled CORE; fold confirm used row-level label PnL "
        "instead of portfolio replay; audit.xlsx sampled 5k rows"
    ),
    "shas": {
        "p1_lock_sha256": "bbe8d1383fdb2eb3aa16d9da5584230a76308b493c025b0c73f11bafc96b8416",
        "source_manifest_sha256": "4b822c1bf7ac919a746b8f7f25b1634d7ac1a0ddcb54067e0c763990bc7a88cc",
        "dataset_sha256": "a34d80b381e10227f64c167b682da130d702c89a4b0f248c2bbcb3f6f21b2497",
        "label_sha256": "22395c9540d9241154cc6521cac20e98bfec3792d7be9cc2d13367937e0a5da2",
        "candidate_registry_sha256": "96fbd6daaf0b66406121b448edcb5dbd9084ab43428d8a72bb1c06acd5876be1",
        "ALL_USABLE_ledger_sha256": "7dbe0e24a2cce0305d2ca84ea3fbb3a2d14445cde19ad36c295105059858e1c4",
        # Published artifact SHAs of the superseded semantic-contract run (immutable; do not adopt)
        "report.json": "83acd483e5d179fc10a8740ee8e372449fd737dee95fd800e18ffefcdeddead7",
        "report.md": "655b95c5318f77b9722ea43883092a21d55eeee5b8ca7efb2e95f6e342b258da",
        "audit.xlsx": "96886709aadd3b2281e7310d562ca6e340a163bdb944ffcfd77ff98f8340b901",
    },
    "artifacts_immutable": True,
    "candidate_economics_not_for_selection": True,
}

# Do not adopt; do not overwrite its 3 published artifacts. Disposition only.
SUPERSEDED_ANALYSIS_MASK_RUN = {
    "run_id": "e1x6_final_20260801_021116_9e461544",
    "disposition": "SUPERSEDED_ANALYSIS_MASK_CONTRACT_ERROR",
    "artifact_internal_integrity": "PASS",
    "determinism": "PASS_ON_THEN_CURRENT_IMPLEMENTATION",
    "P0": "HASH_VALID_BUT_ANALYSIS_MASK_NOT_ENFORCED",
    "P1": "LOCK_HASH_VALID",
    "P2": "ECONOMICS_EXECUTED_WITH_OUT_OF_MASK_EVENTS",
    "reason": (
        "events outside Source Manifest valid_window entered SCORE/BASE/confirm "
        "(pre-open 08:59, LUNCH, AFTER, INVALID_SOURCE 7/21 AM); "
        "coalesced ingress streams were not clipped to AM/PM valid_window bounds"
    ),
    "shas": {
        "report.json": "0684aff12e8bd938a8baef2ee8f5f65b28afd091af445d11e59ea1e4b2999e4a",
        "report.md": "c88b3f2f85d2c278e58906a694c8f064d094d03a4f5b5c1ceeac18e535f1bcb4",
        "audit.xlsx": "08739b805633cf98a4750c4dea22beb70a8c26b29642f7febdc58abecfd725b0",
    },
    "artifacts_immutable": True,
    "candidate_economics_not_for_selection": True,
}

# Do not adopt; do not overwrite its 3 published artifacts. Disposition only.
# Replay boundary / AM→PM carry / post-mask EXIT grace contract error.
SUPERSEDED_REPLAY_BOUNDARY_RUN = {
    "run_id": "e1x6_final_20260801_024352_97202b28",
    "disposition": "SUPERSEDED_REPLAY_BOUNDARY_CONTRACT_ERROR",
    "artifact_internal_integrity": "PASS",
    "determinism": "PASS_ON_THEN_CURRENT_IMPLEMENTATION",
    "P0": "HASH_VALID",
    "P1": "LOCK_HASH_VALID_BUT_REPLAY_LIFECYCLE_INCOMPLETE",
    "P2": "ECONOMICS_EXECUTED_WITH_CROSS_PARTITION_AND_POST_MASK_EXITS",
    "reason": (
        "AM_PM_CARRY / WINDOW_END_OPEN / EXIT_EVENT_SCOPE not locked to analysis_mask "
        "partition: orphan opens completed via post-valid_end MAX_HOLD (e.g. 5242.T "
        "EXIT 11:29 when mask ends 11:25); fold ledgers included AM→PM carry trades; "
        "candidate confirm used PORTFOLIO_REPLAY_ON_LABELED_SCORE_ROWS approximation"
    ),
    "shas": {
        "report.json": "a22508f49f99ce1b63ee749f62a9e69db7167e03c0e725d26b395d58fdb00b7a",
        "report.md": "3a8099cd201ce369bed32be9b07e68adada12023b425650aeeebab4747e690df",
        "audit.xlsx": "d4205ab2014afee3ea2b7c58d19f40e61d40f9851200da642b00e47df0e13982",
    },
    "artifacts_immutable": True,
    "candidate_economics_not_for_selection": True,
}

# Failed Plan 1.3 first attempt (MASKCLIP bug: flat days windows=0). Do not adopt.
FAILED_PLAN13_MASKCLIP_RUN = {
    "run_id": "e1x6_final_20260801_065245_e1cf469f",
    "disposition": "ABORTED_MASKCLIP_WINDOWS_ZERO",
    "published": False,
    "reason": (
        "Plan 1.3 first full A/B aborted: legacy flat days 20260722-24 produced "
        "window_am_pm_tag=None and were excluded (windows=0) before MASKCLIP split-clip fix"
    ),
    "artifacts": ["20260722", "20260723", "20260724"],
    "artifacts_immutable": True,
    "candidate_economics_not_for_selection": True,
}

# Smoke / fixture history (audit trail; not published 3-artifact finals)
RUN_HISTORY_NOTES = [
    {
        "kind": "SMOKE",
        "id": "20260723_AM_partition_smoke",
        "day": "20260723",
        "am_pm": "AM",
        "result": "PASS",
        "note": "FULL_CANONICAL_EVENT_REPLAY smoke; exit_after_1125=0; null_cost=0",
    },
    {
        "kind": "FIXTURE",
        "id": "MASKCLIP_plus_38_fixtures",
        "result": "PASS",
        "tests_n": 38,
        "note": "After MASKCLIP fix: 20260722-24 each AM+PM MASKCLIP windows; pytest 38 passed",
    },
]

# Do not adopt; do not overwrite its 3 published artifacts. Disposition only.
# Final audit contract incomplete (P1 nulls / empty SignalLedger / registry SoT / LODO ledgers).
SUPERSEDED_FINAL_AUDIT_RUN = {
    "run_id": "e1x6_final_20260801_071154_331521a3",
    "disposition": "SUPERSEDED_FINAL_AUDIT_CONTRACT_ERROR",
    "artifact_internal_integrity": "PASS",
    "determinism": "PASS_ON_THEN_CURRENT_IMPLEMENTATION",
    "P0": "HASH_VALID",
    "P1": "LOCK_HASH_VALID_BUT_AUDIT_FIELDS_INCOMPLETE",
    "P2": "ECONOMICS_EXECUTED_BUT_SIGNAL_LEDGER_AND_AUDIT_CONTRACT_INCOMPLETE",
    "reason": (
        "Version 1.3 economics path executed but Stage-1 audit contract incomplete: "
        "P1 config_fingerprint/deps/schema SHA/test code SHA null or missing; "
        "SignalLedger not persisted (F1-F5/final/LODO); CandidateRegistry SoT SHA "
        "inconsistent across report vs Excel Index; RefitLODO missing per-day ledgers; "
        "fold ledgers lacked full ENTRY/EXIT lineage columns"
    ),
    "shas": {
        "report.json": "f071aa5e0f6d34bd1a43bc2bb8c97af893397ece2f222ed04c4ef043bdf8f122",
        "report.md": "98f8d4567a684c447354c587f56dac6b5ef56266b729cefc076bda99fe925969",
        "audit.xlsx": "d462d92f0ec9bd35273ad9f0ca448781df355c900346cc7473e92412c7c0c043",
    },
    "artifacts_immutable": True,
    "candidate_economics_not_for_selection": True,
}

# Stage-1 published final (Version 1.3/2.0 lineage). Verdict E1_X6_INSUFFICIENT_EVIDENCE.
# NOTE 2026-08-02: published artifact FILES were lost from disk together with all
# temp work (OS temp cleanup); SHAs below remain the immutable record. The loss is
# an environment incident, NOT an audit deficiency of the run itself. Economics
# must be regenerated from raw capture for any later stage.
STAGE1_FINAL_RUN_20260801 = {
    "run_id": "e1x6_final_20260801_215129_3ef3736e",
    "disposition": "ENTRY_HYPOTHESIS_ONLY / RETROSPECTIVE_REFERENCE",
    "verdict": "E1_X6_INSUFFICIENT_EVIDENCE",
    "shas": {
        "report.json": "df5e90db82325b8b406e62a5cc4d7e2fa8cfac7be079f39d3221a32383096481",
        "report.md": "af18aee13f800ea0a112ff77c4c7aed7a40ff7c752eb61d431a729af7171b0c8",
        "audit.xlsx": "a191ca3b67cbb614eaa637952f143ae2c4727ef95fb2ad9c49dda695a635cbfb",
    },
    "artifact_files_lost_from_disk": True,
    "artifact_loss_cause": "OS_TEMP_AND_RESULTS_CLEANUP_20260802",
    "candidate_economics_not_for_selection": True,
}

# Plan 2.0 joint EXIT sweep (run e1x6_joint_exit_20260802_105508, registry SHA
# 44ad006fe9928c9077cd12c0b69b8253e938b4101d55ac60e6a0bc85b8e2bb34):
# ALL four evaluated packages are REJECTED as day-concentrated. Never present as
# candidate / best strategy / Shadow candidate regardless of total PnL or PF.
REJECTED_DAY_CONCENTRATED_20260802 = {
    "joint_run_id": "e1x6_joint_exit_20260802_105508",
    "disposition": "REJECTED_DAY_CONCENTRATED / FAILURE_ANALYSIS_ONLY",
    "packages": [
        {
            "exit_family_id": "X5_FROZEN",
            "reason": "ex-best-2-days (7/22,7/31 removed) PnL -279,774 yen",
        },
        {
            "exit_family_id": "X5_TIGHTER_STOP",
            "reason": "ex-best-2-days PnL -281,612 yen",
        },
        {
            "exit_family_id": "X5_WIDER_TARGET",
            "reason": "ex-best-2-days PnL -281,174 yen",
        },
        {
            "exit_family_id": "X5_SHORTER_HOLD",
            "reason": "negative on all days",
        },
    ],
    "shared_failure": "top-2 profit days carry 98.6-99.9% of gross positive day PnL",
    "note": (
        "report.json non-attachment in chat was a transmission error only; "
        "not an audit deficiency"
    ),
    "candidate_economics_not_for_selection": True,
}

# Plan 2.1 day-robust final result (2026-08-02). Stage-1 = full JointRegistry 200
# via oracle full canonical replay (17/17 partition parity exact vs session).
# Stage-2 = ENTRY structural redesign (as-of features, 49 entries x 4 exits = 196).
# BOTH stages: passers = 0. Nothing is presented as candidate / best / Shadow.
PLAN21_DAY_ROBUST_FINAL_20260802 = {
    "verdict": "E1_X6_NO_ROBUST_JOINT_STRATEGY",
    "stage1_run_id": "e1x6_p21_20260802_204337_49eabae8",
    "stage1_registry_sha256": (
        "44ad006fe9928c9077cd12c0b69b8253e938b4101d55ac60e6a0bc85b8e2bb34"
    ),
    "stage2_run_id": "e1x6_p21s2_20260802_223952_a09569ef",
    "stage2_registry_sha256": (
        "598bcbf3eb1b896dac183145e86babc8f2c3efb77345231905d08166ba76cfdc"
    ),
    "shas": {
        "report.json": "a007b1d760cdffc284ef54d45f95edb95f4b93883c922814fd5cb5021c2938cf",
        "report.md": "c94e347cc09263a3df55c3f7bf5fb3a0ccb4cf9fd52fc3476e708fb93ad0220f",
        "audit.xlsx": "89a33dc4e4785bb974c87367886817fde0d24c49c63cc4e63ae7ef6416bc5bf7",
    },
    "structural_failure": (
        "all 396 packages depend on the two mechanically-computed best days "
        "(20260722/20260731) for essentially all gross positive day PnL; "
        "stage-2 best package reached median day +12,205 yen (6/9 days positive) "
        "but ex-best-2-days stayed -121,097 yen"
    ),
    "gate_relaxation": "FORBIDDEN",
    "candidate_economics_not_for_selection": True,
}

# CandidateRegistry SoT namespace (full 200 registry). Selected spec is a separate namespace.
CANDIDATE_REGISTRY_SOT_NAMESPACE = "CandidateRegistry_FULL_CAP200"
SELECTED_SPEC_NAMESPACE = "SelectedSpec_BY_CANDIDATE_ID"

ACCEPTANCE_GATES_11_1 = [
    {
        "gate": "Source",
        "condition": "7/27 PM existing Parity evidence confirmed, or re-run pass; no unresolved material data inconsistency",
    },
    {
        "gate": "Leakage",
        "condition": "all features asof_time <= decision_time; future leakage count == 0",
    },
    {
        "gate": "Determinism",
        "condition": "dual-run dataset/decision/trade ledger SHA exact match",
    },
    {"gate": "Safety", "condition": "submit/cancel/live = 0/0/0"},
    {
        "gate": "Fold completeness",
        "condition": "F1–F5 confirm days each have pre-fixed deterministic analysis_mask_id",
    },
    {"gate": "Support", "condition": "learning support n >= 30 per fold for adoption basis"},
    {
        "gate": "Trade support",
        "condition": "CORE_VALID full period and 7/22-excluded both completed trades n >= 30",
    },
    {
        "gate": "Procedure stability",
        "condition": "same candidate family + feature direction selected in >=3/5 folds; no direction flip",
    },
    {"gate": "ALL_USABLE", "condition": "post-5bps PnL > 0 and PF >= 1.10"},
    {"gate": "CORE_VALID", "condition": "post-5bps PnL > 0 and PF >= 1.10"},
    {"gate": "7/22-excluded", "condition": "PnL > 0 and PF > 1.00"},
    {
        "gate": "Rolling-origin",
        "condition": ">=3/5 folds confirm-day PnL > 0 and median confirm PnL > 0",
    },
    {
        "gate": "Day dependence",
        "condition": "FIXED_SPEC_DAY_DELETION residual period PnL >= 0 for all cases",
    },
    {
        "gate": "Concentration",
        "condition": "top-1-trade-excluded PnL > 0 and top-1-symbol-excluded PnL > 0",
    },
    {
        "gate": "BASE compare",
        "condition": "PF improve, STOP loss improve, STOP-loss/completed improve, max DD improve",
    },
    {"gate": "Implementation", "condition": "reproducible on canonical path alone"},
    {
        "gate": "Complexity",
        "condition": "no symbol/date-specific rules; final candidate is one explainable spec",
    },
]
