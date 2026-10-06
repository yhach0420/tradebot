"""V1.1 contract: V1 research freeze plus explicit bootstrap CI/p/BH. No outcomes."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.identity.ids import sha256_obj
from research.causal_driver_pb1.sector_breadth_precommit.contract import frozen_contract as v1_frozen_contract
from research.causal_driver_pb1.sector_breadth_precommit_v1_1 import (
    PRECOMMIT_ID,
    SUPERSEDES_PRECOMMIT_ID,
    SUPERSEDES_PRECOMMIT_SHA256,
)


def frozen_contract_v1_1(
    *,
    stock: dict[str, Any],
    sectors: dict[str, Any],
    days: dict[str, Any],
    inference: dict[str, Any],
) -> dict[str, Any]:
    base = v1_frozen_contract(stock=stock, sectors=sectors, days=days)
    base.pop("precommit_sha256", None)
    base["precommit_id"] = PRECOMMIT_ID
    base["supersedes_precommit_id"] = SUPERSEDES_PRECOMMIT_ID
    base["supersedes_precommit_sha256"] = SUPERSEDES_PRECOMMIT_SHA256
    base["inference"] = inference
    d2 = "pooled DEV BOOTSTRAP_PERCENTILE_CI 95% (2.5, 97.5 linear) excludes 0; FAIL if lower==0 or upper==0"
    d3 = "BH monotone q_value <= 0.05 on 384 BOOTSTRAP_TWO_SIDED_SIGN_TAIL_PLUS_ONE p-values; equality passes; no subgroup BH"
    d7 = base.get("dev_gates") or {}
    d7 = dict(d7)
    d7["D2"] = d2
    d7["D3"] = d3
    d7["D7"] = {
        **dict(d7.get("D7") or {}),
        "ci_method": "BOOTSTRAP_PERCENTILE_CI",
        "ci_n": 2000,
        "no_bh_retest": True,
        "pass_requires_all": (
            "sign(beta_60) = sign(beta_primary)",
            "BOOTSTRAP_PERCENTILE_CI 95% for beta_60 excludes 0 (same method as D2; FAIL if bound == 0)",
            "abs(beta_60) >= 0.50 * abs(beta_primary)",
        ),
    }
    base["dev_gates"] = d7
    c1 = dict(base.get("c1_gates") or {})
    c1["C2"] = "C1 BOOTSTRAP_PERCENTILE_CI 95% excludes 0; same method as D2; no BH rerun"
    c7 = dict(c1.get("C7") or {})
    c7["ci_method"] = "BOOTSTRAP_PERCENTILE_CI"
    c7["ci_n"] = 2000
    c7["no_bh_retest"] = True
    c7["pass_requires_all"] = (
        "sign(beta_C1_60) = sign(beta_DEV)",
        "C1 BOOTSTRAP_PERCENTILE_CI 95% for beta_C1_60 excludes 0 (same method; FAIL if bound == 0)",
        "abs(beta_C1_60) >= 0.50 * abs(beta_C1_primary)",
    )
    c1["C7"] = c7
    c1["no_c1_exploratory_family"] = True
    c1["no_bh_rerun_on_c1"] = True
    base["c1_gates"] = c1
    base["precommit_sha256"] = sha256_obj(base)
    return base
