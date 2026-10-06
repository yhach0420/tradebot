"""V1.2 contract: V1.1 freeze plus feasible MKT_EX control gate. No outcomes."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.contract import frozen_contract_v1_1
from research.causal_driver_pb1.sector_breadth_precommit_v1_2 import (
    FAIL_NEXT_CORRECTED,
    FAIL_REASON_CORRECTED,
    FAIL_VERDICT_CORRECTED,
    PRECOMMIT_ID,
    SUPERSEDES_PRECOMMIT_ID,
    SUPERSEDES_PRECOMMIT_SHA256,
)
from research.causal_driver_pb1.sector_breadth_precommit_v1_2.control_gate import control_gate_spec


def frozen_contract_v1_2(
    *,
    stock: dict[str, Any],
    sectors: dict[str, Any],
    days: dict[str, Any],
    inference: dict[str, Any],
) -> dict[str, Any]:
    base = frozen_contract_v1_1(stock=stock, sectors=sectors, days=days, inference=inference)
    base.pop("precommit_sha256", None)
    base["precommit_id"] = PRECOMMIT_ID
    base["supersedes_precommit_id"] = SUPERSEDES_PRECOMMIT_ID
    base["supersedes_precommit_sha256"] = SUPERSEDES_PRECOMMIT_SHA256
    spec = control_gate_spec()
    controls = dict(base.get("controls") or {})
    controls["mkt_ex_sector_past_5m"] = (
        "EQW 105 excluding target sector S; "
        "REQUIRED_EX_SECTOR_VALID_N = 80 if N_EX_SECTOR_PIT>=80 else ceil(0.80*N_EX_SECTOR_PIT); "
        "always valid_n/N_EX_SECTOR_PIT >= 0.80"
    )
    controls["mkt_ex_required_valid_n_rule"] = spec
    controls["not_unconditional_80pct_relaxation"] = True
    base["controls"] = controls
    gates = dict(base.get("dev_gates") or {})
    gates["zero_candidates"] = (
        f"{FAIL_VERDICT_CORRECTED}; reason {FAIL_REASON_CORRECTED}; "
        f"do not open C1; NEXT {FAIL_NEXT_CORRECTED}"
    )
    base["dev_gates"] = gates
    base["fail_verdict"] = FAIL_VERDICT_CORRECTED
    base["fail_next"] = FAIL_NEXT_CORRECTED
    base["fail_reason_if_zero_dev_candidates"] = FAIL_REASON_CORRECTED
    base["old_discovery_not_decision_valid"] = True
    base["corrected_discovery_must_rerun_all_384"] = True
    base["do_not_rerun_only_the_32"] = True
    base["bh_m_remains_384"] = True
    base["do_not_set_m_to_352"] = True
    base["precommit_sha256"] = sha256_obj(base)
    return base
