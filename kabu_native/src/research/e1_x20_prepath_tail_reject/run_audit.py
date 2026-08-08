"""E1_X20 pre-path tail-rejection calibration runner."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    CONFIRMATION,
    DIRECTIONS,
    DISCOVERY,
    DOCUMENT_ID,
    FEATURES,
    FORBIDDEN_DAY,
    FORBIDDEN_RISK_FROM,
    SOURCE_RUN,
    STRESS_DAY,
    STRESS_ROLE,
    VARIANTS,
    VERDICT_MIXED,
    VERDICT_NO_MONO,
    VERDICT_SINGLE,
    VERDICT_TWO,
)
from .evaluate import (
    candidate_gate,
    class_composition,
    compare_vs_b0,
    daily_direction,
    matched_compare,
    monotonicity,
    period_slice,
    rejected_ok,
    select,
    stability,
    support_gate,
    threshold_transport,
    variant_metrics,
)
from .load import assign_variants, discovery_thresholds, load_prepared
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x20_prepath_tail_reject"
X12_REPORT = NATIVE / "results" / "research" / "e1_x12_risk_history" / "report.json"
X12_INIT = NATIVE / "src" / "research" / "e1_x12_risk_history" / "__init__.py"


def _kv(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": json.dumps(v, default=str)[:12000]})
        else:
            rows.append({"key": k, "value": v})
    return rows


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x20_prepath_tail_reject.py"
    import os
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src")}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-q", "--tb=line"],
        cwd=str(NATIVE), capture_output=True, text=True, env=env,
    )
    out = (p.stdout or "") + (p.stderr or "")
    passed = failed = 0
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m2 = re.search(r"(\d+) failed", out)
    if m2:
        failed = int(m2.group(1))
    return {
        "exit_code": p.returncode, "passed": passed, "failed": failed,
        "total": passed + failed or 1,
        "rows": [{"test": "pytest_suite",
                  "outcome": "PASSED" if p.returncode == 0 else "FAILED",
                  "detail": out[-2500:]}],
    }


def _period_ok(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    disc = period_slice(rows, "DISCOVERY")
    conf = period_slice(rows, "CONFIRMATION")
    stress = period_slice(rows, "STRESS")

    def dir_ok(sub):
        b0 = variant_metrics(sub, "B0")
        bv = variant_metrics(sub, variant)
        cmp_ = compare_vs_b0(bv, b0)
        return bool(cmp_.get("odds_improved") and cmp_.get("stop_share_down")), cmp_

    d_ok, d_cmp = dir_ok(disc)
    c_ok, c_cmp = dir_ok(conf)
    s_ok, s_cmp = dir_ok(stress)
    daily = daily_direction(conf, variant)
    conf_days_ok = sum(1 for x in daily if x.get("expected_direction"))
    conf_days_pass = conf_days_ok >= 3
    return {
        "discovery_ok": d_ok,
        "confirmation_aggregate_ok": c_ok,
        "confirmation_days_ok_n": conf_days_ok,
        "confirmation_days_pass": conf_days_pass,
        "stress_ok": s_ok,
        "pass": d_ok and c_ok and conf_days_pass and s_ok,
        "discovery_cmp": d_cmp,
        "confirmation_cmp": c_cmp,
        "stress_cmp": s_cmp,
        "confirmation_daily": daily,
        "stress_role": STRESS_ROLE,
    }


def _update_registry_20260804() -> str:
    """Mark 20260804 as ALPHA_PROSPECTIVE_RESERVED without opening raw."""
    # Update constant
    text = X12_INIT.read_text(encoding="utf-8")
    old = 'ALPHA_RESERVED_DAYS = ("20260803",)'
    new = 'ALPHA_RESERVED_DAYS = ("20260803", "20260804")'
    if old in text:
        X12_INIT.write_text(text.replace(old, new), encoding="utf-8")
    elif new not in text:
        # already updated or different format
        pass

    # Patch report.json by_date row
    rep = json.loads(X12_REPORT.read_text(encoding="utf-8"))
    reg = rep.get("date_registry") or {}
    by = reg.get("by_date") or {}
    row = by.get("20260804")
    if row:
        row["status"] = "ALPHA_PROSPECTIVE_RESERVED"
        row["raw_open_allowed"] = False
        row["alpha_use_allowed"] = True
        row["risk_use_allowed"] = False
        row["classification_reason"] = (
            "E1_X20 precommit reserved for sealed historical/prospective; raw not opened"
        )
        row["assigned_by"] = "E1_X20"
        by["20260804"] = row
        # also update rows list if present
        for r in reg.get("rows") or []:
            if r.get("date") == "20260804":
                r.update(row)
        rep["date_registry"] = reg
        # summary status counts refresh lightly
        X12_REPORT.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    return "ALPHA_PROSPECTIVE_RESERVED"


def run() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    run_id = f"e1x20_tailrej_{now.strftime('%Y%m%d_%H%M%S')}_A"

    rows, meta = load_prepared()
    thr = discovery_thresholds(rows)
    rows = assign_variants(rows, thr)

    # Monotonicity Discovery primary
    mono = {}
    for feat in FEATURES:
        qs = thr["by_feature"][feat]
        mono[feat] = {
            "DISCOVERY": monotonicity(period_slice(rows, "DISCOVERY"), feat, qs, "DISCOVERY"),
            "CONFIRMATION": monotonicity(period_slice(rows, "CONFIRMATION"), feat, qs, "CONFIRMATION"),
            "STRESS": monotonicity(period_slice(rows, "STRESS"), feat, qs, "STRESS"),
        }
    mono_ok = not any(
        mono[f]["DISCOVERY"]["NON_MONOTONIC_MECHANISM"] for f in FEATURES
    )

    transport = {
        "slope_60s": threshold_transport(rows, "slope_60s", thr["SLOPE_UPPER_LIMIT"]),
        "rebound_from_recent_low_bps": threshold_transport(
            rows, "rebound_from_recent_low_bps", thr["REBOUND_UPPER_LIMIT_BPS"]
        ),
    }
    transport_ok = not any(v["THRESHOLD_TRANSPORT_UNSTABLE"] for v in transport.values())

    # Primary universe: Discovery+Confirmation (stress separate)
    main = [r for r in rows if r["date"] != STRESS_DAY]
    stress_rows = period_slice(rows, "STRESS")

    metrics_main = {v: variant_metrics(main, v) for v in VARIANTS}
    metrics_stress = {v: variant_metrics(stress_rows, v) for v in VARIANTS}
    metrics_all = {v: variant_metrics(rows, v) for v in VARIANTS}

    b0 = metrics_main["B0"]
    single = {
        "B1_vs_B0": compare_vs_b0(metrics_main["B1"], b0),
        "B2_vs_B0": compare_vs_b0(metrics_main["B2"], b0),
    }
    rej = {
        "B1_Rejected": variant_metrics(main, "B1_Rejected"),
        "B2_Rejected": variant_metrics(main, "B2_Rejected"),
    }
    # assign rejected flags already on rows
    for r in main:
        pass
    rej_flags = {
        "B1": rejected_ok(class_composition(select(main, "in_B1_Rejected")), metrics_main["B1"]),
        "B2": rejected_ok(class_composition(select(main, "in_B2_Rejected")), metrics_main["B2"]),
    }
    rej["B1_Rejected"] = class_composition(select(main, "in_B1_Rejected"))
    rej["B2_Rejected"] = class_composition(select(main, "in_B2_Rejected"))

    incr = {
        "B3_vs_B1": compare_vs_b0(metrics_main["B3"], metrics_main["B1"]),
        "B3_vs_B2": compare_vs_b0(metrics_main["B3"], metrics_main["B2"]),
    }
    # B3 incremental: must improve odds and stop vs both parents; winner not down
    b3_incr_ok = bool(
        incr["B3_vs_B1"].get("pass_single") and incr["B3_vs_B2"].get("pass_single")
    )
    # also reject if only support shrinks without class improvement
    if (metrics_main["B3"]["support"] < metrics_main["B1"]["support"] * 0.9
            and not incr["B3_vs_B1"].get("odds_improved")):
        b3_incr_ok = False

    periods = {v: _period_ok(rows, v) for v in ("B1", "B2", "B3")}
    supports = {
        v: {
            "main": support_gate(metrics_main[v], b0),
            "stress": support_gate(metrics_stress[v], metrics_stress["B0"], stress=True),
        }
        for v in ("B1", "B2", "B3")
    }
    # rejected complement support
    rej_support_ok = {
        "B1": (rej["B1_Rejected"]["support"] >= 100
               and class_composition(select(stress_rows, "in_B1_Rejected"))["support"] >= 10),
        "B2": (rej["B2_Rejected"]["support"] >= 100
               and class_composition(select(stress_rows, "in_B2_Rejected"))["support"] >= 10),
    }

    stabs = {v: stability(main, v) for v in ("B1", "B2", "B3")}
    matched = {v: matched_compare(main, v) for v in ("B1", "B2", "B3")}

    gate_pass = {}
    for v in ("B1", "B2"):
        ok, reasons = candidate_gate(
            mono_ok=mono_ok,
            transport_ok=transport_ok,
            support_ok=supports[v]["main"]["pass"] and supports[v]["stress"]["pass"] and rej_support_ok[v],
            period_ok=periods[v]["pass"],
            single_cmp=single[f"{v}_vs_B0"],
            rej_ok=rej_flags[v],
            stab=stabs[v],
            incremental_ok=True,
        )
        gate_pass[v] = {"pass": ok, "reasons": reasons}

    ok3, reasons3 = candidate_gate(
        mono_ok=mono_ok,
        transport_ok=transport_ok,
        support_ok=supports["B3"]["main"]["pass"] and supports["B3"]["stress"]["pass"],
        period_ok=periods["B3"]["pass"],
        single_cmp=compare_vs_b0(metrics_main["B3"], b0),
        rej_ok=True,  # complement via parents
        stab=stabs["B3"],
        incremental_ok=b3_incr_ok,
    )
    gate_pass["B3"] = {"pass": ok3, "reasons": reasons3}

    # Selection
    selected = None
    selected_variant = None
    if gate_pass["B1"]["pass"] or gate_pass["B2"]["pass"]:
        # prefer simpler; if both, pick better odds improvement
        cands = []
        if gate_pass["B1"]["pass"]:
            cands.append(("B1", single["B1_vs_B0"].get("odds_delta") or 0))
        if gate_pass["B2"]["pass"]:
            cands.append(("B2", single["B2_vs_B0"].get("odds_delta") or 0))
        cands.sort(key=lambda x: -x[1])
        selected_variant = cands[0][0]
    if gate_pass["B3"]["pass"] and b3_incr_ok:
        # only if clearly better than selected single
        if selected_variant is None:
            selected_variant = "B3"
        else:
            parent = metrics_main[selected_variant]
            if (incr[f"B3_vs_{selected_variant}"].get("pass_single")
                    and (metrics_main["B3"].get("winner_stop_odds") or 0) > (parent.get("winner_stop_odds") or 0)):
                selected_variant = "B3"

    # Verdict
    if not mono_ok:
        verdict = VERDICT_NO_MONO
    elif selected_variant == "B3":
        verdict = VERDICT_TWO
    elif selected_variant in ("B1", "B2"):
        verdict = VERDICT_SINGLE
    else:
        verdict = VERDICT_MIXED

    precommit = None
    precommit_status = "NOT_CREATED"
    registry_status = "UNCLASSIFIED_DO_NOT_OPEN"
    if selected_variant in ("B1", "B2", "B3") and verdict in (VERDICT_SINGLE, VERDICT_TWO):
        if selected_variant == "B1":
            selected = "PREPATH_SLOPE_UPPER_TAIL_REJECT_V1"
            rule = (
                f"same X19 cluster anchor AND slope_60s evaluable "
                f"AND slope_60s <= {thr['SLOPE_UPPER_LIMIT']}"
            )
            feat = "slope_60s"
            threshold = thr["SLOPE_UPPER_LIMIT"]
        elif selected_variant == "B2":
            selected = "PREPATH_REBOUND_UPPER_TAIL_REJECT_V1"
            rule = (
                f"same X19 cluster anchor AND rebound_from_recent_low_bps evaluable "
                f"AND rebound_from_recent_low_bps <= {thr['REBOUND_UPPER_LIMIT_BPS']}"
            )
            feat = "rebound_from_recent_low_bps"
            threshold = thr["REBOUND_UPPER_LIMIT_BPS"]
        else:
            selected = "PREPATH_SLOPE_REBOUND_UPPER_TAIL_REJECT_V1"
            rule = (
                f"same X19 cluster anchor AND slope_60s <= {thr['SLOPE_UPPER_LIMIT']} "
                f"AND rebound_from_recent_low_bps <= {thr['REBOUND_UPPER_LIMIT_BPS']}"
            )
            feat = "slope_60s+rebound_from_recent_low_bps"
            threshold = {
                "SLOPE_UPPER_LIMIT": thr["SLOPE_UPPER_LIMIT"],
                "REBOUND_UPPER_LIMIT_BPS": thr["REBOUND_UPPER_LIMIT_BPS"],
            }
        body = {
            "candidate_id": selected,
            "variant": selected_variant,
            "exact_rule": rule,
            "exact_feature": feat,
            "exact_threshold": threshold,
            "same_anchor_rule": "X19 cluster_id / grid_epoch unchanged",
            "missing_behavior": "feature not evaluable → not in B0 universe / not retained",
            "episode_rule": "X14/X19 one representative anchor per cluster",
            "label_rule": "X19 outcome_class (WINNER/STOP/NOPROGRESS/TWO_SIDED/UNCLASSIFIED)",
            "precommit_at_jst": now.isoformat(),
            "20260804_raw_opened": False,
        }
        body["precommit_sha256"] = hashlib.sha256(
            json.dumps({k: v for k, v in body.items() if k != "precommit_sha256"},
                       sort_keys=True, default=str).encode()
        ).hexdigest()
        precommit = body
        precommit_status = "CREATED"
        registry_status = _update_registry_20260804()

    det = {
        "ab_match": True,
        "hash_a": sha256_obj(metrics_main),
        "hash_b": sha256_obj({v: variant_metrics(main, v) for v in VARIANTS}),
    }
    det["ab_match"] = det["hash_a"] == det["hash_b"]

    interim = {
        "run_id": run_id,
        "source_run": SOURCE_RUN,
        "population_n": len(rows),
        "thresholds": {
            "SLOPE_UPPER_LIMIT": thr["SLOPE_UPPER_LIMIT"],
            "REBOUND_UPPER_LIMIT_BPS": thr["REBOUND_UPPER_LIMIT_BPS"],
        },
        "features": list(FEATURES),
        "variants": list(VARIANTS),
        "directions": DIRECTIONS,
        "no_retune": True,
        "same_anchor": True,
        "selected_variant": selected_variant,
        "selected_candidate": selected,
        "verdict": verdict,
        "mono_ok": mono_ok,
        "transport_ok": transport_ok,
        "gate_pass": gate_pass,
        "opened_20260804_raw": False,
        "forbidden_risk_from": FORBIDDEN_RISK_FROM,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    tests = _run_tests()
    safety = {
        "submit_cancel_live": "0/0/0",
        "mainline_changed": False,
        "production_yaml_changed": False,
        "ENTRY_changed": False,
        "EXIT_changed": False,
        "Universe_changed": False,
        "20260804_raw_opened": False,
        "Shadow": False,
        "Forward": False,
        "Paper_connection": False,
        "Discord": False,
        "paper_trade_only": True,
    }

    sheets = {
        "SourceIdentity": _kv({"source_run": SOURCE_RUN, "population_n": len(rows), **meta}),
        "FeatureContract": _kv({"features": list(FEATURES), "directions": DIRECTIONS, "forbidden_extras": True}),
        "ThresholdContract": _kv(thr),
        "QuantileBins": [
            b
            for feat in FEATURES
            for b in mono[feat]["DISCOVERY"]["bins"]
        ],
        "Monotonicity": [
            {"feature": f, "period": p, **{k: v for k, v in mono[f][p].items() if k != "bins"}}
            for f in FEATURES for p in ("DISCOVERY", "CONFIRMATION", "STRESS")
        ],
        "ThresholdTransport": list(transport.values()),
        "VariantDefinitions": [
            {"variant": "B0", "rule": "both features evaluable"},
            {"variant": "B1", "rule": f"slope_60s <= {thr['SLOPE_UPPER_LIMIT']}"},
            {"variant": "B2", "rule": f"rebound <= {thr['REBOUND_UPPER_LIMIT_BPS']}"},
            {"variant": "B3", "rule": "B1 AND B2"},
        ],
        "ClassComposition": [{"set": "main", **metrics_main[v]} for v in VARIANTS]
        + [{"set": "stress", **metrics_stress[v]} for v in VARIANTS],
        "SingleMechanism": [
            {"contrast": "B1_vs_B0", **single["B1_vs_B0"], "rejected_ok": rej_flags["B1"]},
            {"contrast": "B2_vs_B0", **single["B2_vs_B0"], "rejected_ok": rej_flags["B2"]},
            {"rejected_B1": rej["B1_Rejected"]},
            {"rejected_B2": rej["B2_Rejected"]},
        ],
        "TwoMechanismIncrement": [
            {"contrast": "B3_vs_B1", **incr["B3_vs_B1"]},
            {"contrast": "B3_vs_B2", **incr["B3_vs_B2"]},
            {"b3_incremental_ok": b3_incr_ok},
        ],
        "PeriodResults": [{"variant": v, **periods[v]} for v in ("B1", "B2", "B3")],
        "DailyResults": [
            row for v in ("B1", "B2", "B3") for row in periods[v]["confirmation_daily"]
        ],
        "MatchedGroups": list(matched.values()),
        "SymbolResults": [{"variant": v, **stabs[v]} for v in ("B1", "B2", "B3")],
        "LODO": [{"variant": v, "lodo_major_flips": stabs[v]["lodo_major_flips"], "ok": stabs[v]["lodo_ok"]} for v in ("B1", "B2", "B3")],
        "LOSO": [{"variant": v, "exclude_2354_odds": stabs[v]["exclude_2354_odds"], "exclude_285A_odds": stabs[v]["exclude_285A_odds"]} for v in ("B1", "B2", "B3")],
        "CandidateSelection": _kv({
            "gate_pass": gate_pass,
            "selected_variant": selected_variant,
            "selected_candidate": selected,
            "verdict": verdict,
        }),
        "ProspectivePrecommit": _kv(precommit or {"status": precommit_status}),
        "ChangeLog": [{"at": now.isoformat(), "note": "E1_X20 pre-path tail rejection calibration"}],
    }

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "source_run": SOURCE_RUN,
        "verdict": verdict,
        "population_n": len(rows),
        "thresholds": thr,
        "monotonicity": {f: {p: {k: v for k, v in mono[f][p].items() if k != "bins"} for p in mono[f]} for f in FEATURES},
        "threshold_transport": transport,
        "supports": {v: metrics_main[v]["support"] for v in VARIANTS},
        "metrics_main": metrics_main,
        "metrics_stress": metrics_stress,
        "single_mechanism": single,
        "rejected_complements": rej,
        "two_mechanism_increment": incr,
        "period_gates": periods,
        "stability": stabs,
        "matched": matched,
        "gate_pass": gate_pass,
        "selected_variant": selected_variant,
        "selected_candidate": selected,
        "prospective_precommit_status": precommit_status,
        "prospective_precommit": precommit,
        "registry_20260804_status": registry_status,
        "safety": safety,
        "_sheets": sheets,
    }
    shas = publish(report, tests, det, OUT)
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "thresholds": {
            "slope_q80": thr["SLOPE_UPPER_LIMIT"],
            "rebound_q80": thr["REBOUND_UPPER_LIMIT_BPS"],
        },
        "supports": {v: metrics_main[v]["support"] for v in VARIANTS},
        "selected": selected,
        "precommit": precommit_status,
        "registry_20260804": registry_status,
        "tests": f"{tests['passed']}/{tests['total']}",
        "ab": det["ab_match"],
    }, indent=2, default=str))
    return report


if __name__ == "__main__":
    run()
