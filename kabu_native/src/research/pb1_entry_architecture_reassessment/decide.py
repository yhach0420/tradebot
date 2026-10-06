"""Accept only fold-stable causal features that produce Complete Strategy OOF edge."""
from __future__ import annotations

from typing import Any

from research.pb1_entry_architecture_reassessment import MIN_FOLDS_SAME_SIGN
from research.pb1_entry_architecture_reassessment.precommit import FAMILIES, FEATURES

TIME_OF_DAY = {"minutes_from_open"}


def informative(summary: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    feat = str(summary.get("feature") or "")
    if int(summary.get("n_aligned_folds") or 0) < int(MIN_FOLDS_SAME_SIGN):
        reasons.append("FOLDS_NOT_STABLE")
    if not summary.get("has_dev"):
        reasons.append("NO_DEV_FOLD")
    if not summary.get("has_c1"):
        reasons.append("NO_C1_FOLD")
    if summary.get("price_proxy"):
        reasons.append("PRICE_PROXY")
    if feat in TIME_OF_DAY:
        reasons.append("TIME_OF_DAY_BLACKLIST_RISK")
    ok = not reasons
    family = None
    for fam, feats in FAMILIES.items():
        if feat in feats:
            family = fam
            break
    return {"feature": feat, "family": family, "ok": ok, "reasons": reasons, "direction": summary.get("direction")}


def decide(*, summaries: list[dict[str, Any]], architectures: list[dict[str, Any]], market_context: dict[str, Any]) -> dict[str, Any]:
    info = [informative(s) for s in summaries]
    supported = [x for x in info if x.get("ok")]
    rejected = {str(x["feature"]): x.get("reasons") for x in info if not x.get("ok")}
    found = False
    for arch in architectures:
        if arch.get("c1_positive_edge") and not arch.get("price_proxy") and arch.get("feature") not in TIME_OF_DAY:
            found = True
            break
    families_supported = sorted({str(x.get("family")) for x in supported if x.get("family")})
    return {
        "found": found,
        "supported_univariate_features": supported,
        "rejected_univariate_features": rejected,
        "supported_families": families_supported,
        "unsupported_families": [f for f in FAMILIES if f not in families_supported],
        "market_context": market_context,
        "architectures": architectures,
        "joint_search_ran": False,
        "V5_CREATED": False,
        "note": "Joint 2-4 feature search runs only if univariate features are informative. V5 is not created in this task.",
        "feature_inventory": list(FEATURES),
    }
