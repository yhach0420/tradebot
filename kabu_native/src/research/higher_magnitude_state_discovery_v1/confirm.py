"""One-shot Confirmation after candidate freeze. Not design. Not certification. Does not open Frozen Validation."""
from __future__ import annotations

from typing import Any

from research.higher_magnitude_state_discovery_v1.candidates import replay


def secondary_confirmation(*, episodes: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    any_pass = False
    for c in candidates:
        spec = dict(c.get("spec") or {})
        disc = dict(c.get("discovery_economics") or {})
        econ = replay(episodes=episodes, spec=spec, date_to_block=None, mode="ranked_top3")
        disc_x0 = disc.get("mean_x0_bps")
        conf_x0 = econ.get("mean_x0_bps")
        conf_x1 = econ.get("mean_x1_bps")
        sign_ok = disc_x0 is not None and conf_x0 is not None and float(disc_x0) > 0 and float(conf_x0) > 0
        x1_ok = conf_x1 is not None and float(conf_x1) > 0
        n_ok = int(econ.get("trade_n") or 0) >= 15
        mag_ok = conf_x0 is not None and float(conf_x0) > 6.0
        passed = bool(econ.get("ok") and sign_ok and x1_ok and n_ok and mag_ok)
        if passed:
            any_pass = True
        else:
            if "promotion" in c:
                c["promotion"] = dict(c["promotion"])
                c["promotion"]["promoted"] = False
                c["promotion"]["secondary_rejected"] = True
        rows.append(
            {
                "candidate_id": spec.get("candidate_id"),
                "spec_sha256": spec.get("spec_sha256"),
                "label": "SECONDARY_CONFIRMATION_NOT_PRISTINE",
                "passed": passed,
                "certifies_candidate": False,
                "parameter_repair_after_result": False,
                "discovery_mean_x0_bps": disc_x0,
                "confirmation_mean_x0_bps": conf_x0,
                "confirmation_mean_x1_bps": conf_x1,
                "confirmation_trade_n": econ.get("trade_n"),
                "economics": econ,
            }
        )
    return {
        "ran": True,
        "label": "SECONDARY_CONFIRMATION_NOT_PRISTINE",
        "old_confirmation_used_to_design": False,
        "any_passed": any_pass,
        "certifies": False,
        "frozen_validation_opened": False,
        "rows": rows,
    }
