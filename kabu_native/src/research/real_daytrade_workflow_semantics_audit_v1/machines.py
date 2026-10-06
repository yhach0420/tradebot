"""Prior machines: economic null vs wrong trader semantics. No PnL reopen."""
from __future__ import annotations

from typing import Any


def prior_machines() -> list[dict[str, Any]]:
    return [
        {
            "machine": "S/R A2 / C1",
            "published": "FACE_VALID_SR_MATCHED_PATH_SEPARATION_FOUND_V1 then SR_PATH_SEPARATION_NOT_COMPLETE_STRATEGY_V1",
            "failure_class": "ECONOMIC_NULL_AS_COMPLETE_STRATEGY",
            "representation": "After FACE_VALID rebuild, detector clutter dropped and zones were closer to discretionary S/R. Representation was repaired before the economic test.",
            "remain_economically_meaningful": True,
            "may_be_representation_failure": False,
            "note": "Location can separate path from matched non-zone moves and still not be a complete strategy. Keep as LOCATION, not as WHY-THIS-STOCK.",
        },
        {
            "machine": "unsigned native context stack R14/T15",
            "published": "R14_DIRECTION_SEMANTICS_INVALID_V1",
            "failure_class": "REPRESENTATION_FAILURE",
            "remain_economically_meaningful": False,
            "may_be_representation_failure": True,
            "note": "r15 and prior5_ret were not direction-normalized. Do not read as 'combined native contexts are exhausted'.",
        },
        {
            "machine": "direction-aligned compact stack",
            "published": "DIRECTION_ALIGNED_CONTEXT_PARTIAL_MECHANISM_V1",
            "failure_class": "ECONOMIC_NULL_VS_MATCHED_SAME_DIR",
            "remain_economically_meaningful": True,
            "may_be_representation_failure": False,
            "note": "DIR semantics were fixed. Leaves still lost to matched same-DIR controls. Compact single-name stack is not a playbook.",
        },
        {
            "machine": "cross-sectional peer propagation",
            "published": "PEER_PROPAGATION_REAL_BUT_EDGE_CONSUMED_V1",
            "failure_class": "ECONOMIC_NULL_AT_EXECUTABLE_HORIZON",
            "remain_economically_meaningful": True,
            "may_be_representation_failure": False,
            "note": "Peer lead is real. Next-open consumes most of it. Keep as SELECTION/BIAS context, not an entry machine.",
        },
        {
            "machine": "1-minute SMA5/25/75 pullback",
            "published": "SMA5_25_75_PULLBACK_PARTIAL_V1",
            "failure_class": "REPRESENTATION_FAILURE",
            "remain_economically_meaningful": False,
            "may_be_representation_failure": True,
            "note": "5/25/75 on 1-minute bars is not the Japanese daily MA convention. Close the 1m architecture, not the MA family.",
        },
        {
            "machine": "5-minute SMA5/25/75 + 1m trigger",
            "published": "MTF_5M_SMA5_25_75_PARTIAL_V1",
            "failure_class": "MIXED: representation first, then weak relative increment",
            "remain_economically_meaningful": False,
            "may_be_representation_failure": True,
            "TESTED_MACHINE_FAILED": True,
            "STANDARD_MA_FAMILY_EXHAUSTED": False,
            "note": "Live-causal 5m SMA was implemented correctly. The object itself is daily-convention numbers on intraday bars. D3 path was incoherent; absolute 10m was ~0. Relative A vs B does not salvage that.",
        },
        {
            "machine": "R11 VWAP reclaim",
            "published": "R11_OFFLINE_CLUSTER_LOOKAHEAD_CONFIRMED_V1; do not repair",
            "failure_class": "REPRESENTATION / CAUSAL KNOWABILITY FAILURE",
            "remain_economically_meaningful": False,
            "may_be_representation_failure": True,
            "note": "VWAP as LOCATION remains face-valid. The R11 machine is not.",
        },
        {
            "machine": "reference-level atlas (OR/PDH/VWAP/gap)",
            "published": "REFERENCE_LEVEL_PLAYBOOKS_FOUND_NOT_PROMOTED_V1",
            "failure_class": "NOT_A_COMPLETE_STRATEGY / no stock-selection layer",
            "remain_economically_meaningful": True,
            "may_be_representation_failure": False,
            "note": "These levels are ordinary daytrade locations. They were tested as 1m events without WHY-THIS-STOCK. Do not throw the locations away.",
        },
    ]
