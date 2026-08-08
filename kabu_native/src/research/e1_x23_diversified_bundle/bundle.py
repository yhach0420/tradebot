"""Phase C–D: Diversified bundle construction + audit."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import ACTUAL_EXITS, FAMILY_ORDER, REQUIRED_SIGNATURES


def _tech_ok(p: dict[str, Any]) -> bool:
    m = p.get("metrics") or {}
    if (m.get("trades") or 0) < 100:
        return False
    if (m.get("days") or 0) < 5:
        return False
    if (m.get("symbols") or 0) < 10:
        return False
    ev = (p.get("period") or {}).get("EVALUATION") or {}
    if (ev.get("trades") or 0) < 20:
        return False
    reasons = m.get("exit_reason_counts") or {}
    if not reasons:
        return False
    if p.get("retention_band") == "TAIL_SELECT":
        if (m.get("trades") or 0) < 100 or (m.get("days") or 0) < 5:
            return False
    return True


def _perf_ok(p: dict[str, Any]) -> bool:
    vs = p.get("vs_baseline") or {}
    checks = [
        (vs.get("avg_yen_delta_vs_same_exit_baseline") or 0) > 0,
        (vs.get("day_balanced_delta_vs_same_exit_baseline") or 0) > 0,
        (vs.get("symbol_balanced_delta_vs_same_exit_baseline") or 0) > 0,
        (vs.get("price_band_balanced_delta_vs_same_exit_baseline") or 0) > 0,
        (vs.get("PF_delta") or 0) > 0,
        (vs.get("worst_trade_delta") or 0) > 0,
        (vs.get("max_drawdown_delta") or 0) > 0,  # less negative DD is improvement? dd is negative; higher (closer to 0) is better
        (vs.get("hard_stop_rate_delta") or 0) < 0,
    ]
    # max_drawdown_delta: if delta > 0, max_dd is less severe (e.g. -100 vs -200 → +100)
    return any(checks)


def _score(p: dict[str, Any]) -> float:
    return (p.get("metrics") or {}).get("avg_return_bps") or -1e18


def construct_diversified_bundle(pair_rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [p for p in pair_rows if _tech_ok(p) and _perf_ok(p)]
    eligible.sort(key=_score, reverse=True)

    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str]] = set()
    mask_exit_count: dict[str, int] = defaultdict(int)  # per mask, max 2 exits for singles

    def add(p: dict[str, Any], reason: str) -> bool:
        key = (p["candidate_id"], p["actual_exit_id"])
        if key in selected_keys:
            return False
        if p["logic_depth"] == "SINGLE" and mask_exit_count[p["candidate_id"]] >= 2:
            return False
        selected_keys.add(key)
        mask_exit_count[p["candidate_id"]] += 1
        row = dict(p)
        row["bundle_select_reason"] = reason
        selected.append(row)
        return True

    notes = []

    # C-2 single family coverage: >=4 masks per family, up to 2 exits each
    for fam in FAMILY_ORDER:
        fam_pairs = [p for p in eligible if p["logic_depth"] == "SINGLE" and p["component_family_signature"] == fam]
        by_mask: dict[str, list] = defaultdict(list)
        for p in fam_pairs:
            by_mask[p["candidate_id"]].append(p)
        masks_added = 0
        for mid, plist in by_mask.items():
            plist = sorted(plist, key=_score, reverse=True)
            if not plist:
                continue
            # best + different comparison exit
            if add(plist[0], f"single_family_{fam}_best"):
                masks_added += 1
            if len(plist) > 1:
                # pick different exit if possible
                alt = next((x for x in plist[1:] if x["actual_exit_id"] != plist[0]["actual_exit_id"]), None)
                if alt:
                    add(alt, f"single_family_{fam}_comparison_exit")
            if masks_added >= 4 and sum(1 for s in selected if s["logic_depth"] == "SINGLE" and s["component_family_signature"] == fam) >= 4:
                # ensure at least 4 distinct masks
                pass
            if len({s["candidate_id"] for s in selected if s["logic_depth"] == "SINGLE" and s["component_family_signature"] == fam}) >= 4:
                break
        n_masks = len({s["candidate_id"] for s in selected if s["logic_depth"] == "SINGLE" and s["component_family_signature"] == fam})
        if n_masks < 4:
            # relax: take any single tech-ok even without perf
            relax = [p for p in pair_rows if p["logic_depth"] == "SINGLE" and p["component_family_signature"] == fam and _tech_ok(p)]
            relax.sort(key=_score, reverse=True)
            for p in relax:
                if len({s["candidate_id"] for s in selected if s["logic_depth"] == "SINGLE" and s["component_family_signature"] == fam}) >= 4:
                    break
                add(p, f"single_family_{fam}_relaxed_tech")
            n_masks = len({s["candidate_id"] for s in selected if s["logic_depth"] == "SINGLE" and s["component_family_signature"] == fam})
            if n_masks < 4:
                notes.append({"type": "single_family_shortfall", "family": fam, "masks": n_masks})

    # C-3 two-feature signatures: >=2 masks each
    for sig in REQUIRED_SIGNATURES:
        sig_pairs = [p for p in eligible if p["logic_depth"] == "TWO_FEATURE" and p["component_family_signature"] == sig]
        by_mask = defaultdict(list)
        for p in sig_pairs:
            by_mask[p["candidate_id"]].append(p)
        n = 0
        for mid, plist in sorted(by_mask.items(), key=lambda kv: -_score(kv[1][0])):
            plist = sorted(plist, key=_score, reverse=True)
            if add(plist[0], f"two_feature_{sig}"):
                n += 1
            if n >= 2:
                break
        if n < 2:
            relax = [p for p in pair_rows if p["logic_depth"] == "TWO_FEATURE" and p["component_family_signature"] == sig and _tech_ok(p)]
            relax.sort(key=_score, reverse=True)
            for p in relax:
                if len({s["candidate_id"] for s in selected if s["component_family_signature"] == sig}) >= 2:
                    break
                if add(p, f"two_feature_{sig}_relaxed"):
                    n += 1
            if len({s["candidate_id"] for s in selected if s["component_family_signature"] == sig}) < 2:
                notes.append({"type": "signature_shortfall", "signature": sig, "masks": n})

    # C-4 EXIT diversity: >=10 pairs each
    for eid in ACTUAL_EXITS:
        n = sum(1 for s in selected if s["actual_exit_id"] == eid)
        if n >= 10:
            continue
        need = 10 - n
        pool = [p for p in eligible if p["actual_exit_id"] == eid]
        for p in pool:
            if need <= 0:
                break
            if add(p, f"exit_coverage_{eid}"):
                need -= 1
        n2 = sum(1 for s in selected if s["actual_exit_id"] == eid)
        if n2 < 10:
            notes.append({"type": "exit_shortfall", "exit_id": eid, "count": n2})

    # C-5 retention bands
    for band in ("HIGH_RETENTION", "MID_RETENTION", "LOW_RETENTION", "TAIL_SELECT"):
        n = sum(1 for s in selected if s["retention_band"] == band)
        if n >= 8:
            continue
        pool = [p for p in eligible if p["retention_band"] == band]
        for p in pool:
            if sum(1 for s in selected if s["retention_band"] == band) >= 8:
                break
            add(p, f"retention_{band}")
        n2 = sum(1 for s in selected if s["retention_band"] == band)
        if n2 < 4:
            notes.append({"type": "retention_shortfall", "band": band, "count": n2})

    # C-6 period diversity
    for tag in ("ALL_PERIOD_POSITIVE", "EVALUATION_REVERSED", "STRESS_REVERSED", "EXIT_SENSITIVE"):
        n = sum(1 for s in selected if (s.get("period_tags") or {}).get("bundle_tag") == tag)
        if n >= 10:
            continue
        pool = [p for p in eligible if (p.get("period_tags") or {}).get("bundle_tag") == tag]
        for p in pool:
            if sum(1 for s in selected if (s.get("period_tags") or {}).get("bundle_tag") == tag) >= 10:
                break
            add(p, f"period_{tag}")
        n2 = sum(1 for s in selected if (s.get("period_tags") or {}).get("bundle_tag") == tag)
        if n2 < 5:
            notes.append({"type": "period_tag_shortfall", "tag": tag, "count": n2})

    # Fill toward 160–240 with top remaining diversified
    target_lo, target_hi = 160, 240
    for p in eligible:
        if len(selected) >= target_hi:
            break
        add(p, "fill_diversified_top")
    if len(selected) < target_lo:
        # more fill from tech-ok
        for p in pair_rows:
            if len(selected) >= target_lo:
                break
            if _tech_ok(p):
                add(p, "fill_tech_ok")

    audit = audit_bundle(selected, notes)
    return {"pairs": selected, "notes": notes, "audit": audit}


def audit_bundle(selected: list[dict[str, Any]], notes: list[dict[str, Any]]) -> dict[str, Any]:
    singles = [s for s in selected if s["logic_depth"] == "SINGLE"]
    twos = [s for s in selected if s["logic_depth"] == "TWO_FEATURE"]
    fam_masks = {
        fam: len({s["candidate_id"] for s in singles if s["component_family_signature"] == fam})
        for fam in FAMILY_ORDER
    }
    sig_masks = {
        sig: len({s["candidate_id"] for s in twos if s["component_family_signature"] == sig})
        for sig in REQUIRED_SIGNATURES
    }
    exit_counts = {eid: sum(1 for s in selected if s["actual_exit_id"] == eid) for eid in ACTUAL_EXITS}
    ret_counts = defaultdict(int)
    tag_counts = defaultdict(int)
    for s in selected:
        ret_counts[s["retention_band"]] += 1
        tag_counts[(s.get("period_tags") or {}).get("bundle_tag") or "NA"] += 1

    single_family_ok = all(fam_masks[f] >= 4 for f in FAMILY_ORDER)
    return {
        "bundle_pair_count": len(selected),
        "unique_entry_masks": len({s["candidate_id"] for s in selected}),
        "unique_entry_candidates": len({s["candidate_id"] for s in selected}),
        "single_pair_count": len(singles),
        "two_feature_pair_count": len(twos),
        "family_mask_counts": fam_masks,
        "component_signature_mask_counts": sig_masks,
        "exit_counts": exit_counts,
        "retention_band_counts": dict(ret_counts),
        "period_tag_counts": dict(tag_counts),
        "single_family_coverage_ok": single_family_ok,
        "notes": notes,
        "precommit_allowed": single_family_ok and 160 <= len(selected) <= 300,
    }
