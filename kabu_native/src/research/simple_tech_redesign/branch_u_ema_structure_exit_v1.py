"""Offline UNPROVEN_EMA_STRUCTURE_LOSS_V1 full causal EXIT evaluation. Research only."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.am_entry_profit_improvement import CANCEL_N, ELIGIBLE_DAYS, LIVE_ORDER_N, SESSION, SUBMIT_N
from research.simple_tech_entry_family.indicators import self_check
from research.simple_tech_entry_family.spec import spec_sha256 as v1_spec_sha256
from research.simple_tech_entry_family.v13_analyze import reporting_semantics
from research.simple_tech_redesign.branch_u_bb_spec import PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_causal_harvest import _add_arm, _empty_arm
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.exit_composition_shift_rca_analyze import load_residual_rows
from research.simple_tech_redesign.entry_anchored_floor_break_candidate_analyze import residual_index
from research.simple_tech_redesign.exit_lifecycle_spec import EXIT_POLICY_CREATED as LIFECYCLE_EXIT_POLICY_CREATED
from research.simple_tech_redesign.isolation import (
    BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT,
    BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT,
    BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT,
    BRANCH_U_OUT,
    E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT,
    LIFECYCLE_OUT,
    POST_BE_SWING_FLOOR_EXIT_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT,
    PRECAP_T3_SETUP_SEQUENCE_V1_OUT,
    PRECAP_PROSPECTIVE_V1_OUT,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.simple_tech_redesign.branch_u_ema_structure_exit_v1_analyze import decide, evaluate_cohort
from research.simple_tech_redesign.branch_u_ema_structure_exit_v1_harvest import harvest_day, recover_lifecycle_u_ema_predicate
from research.simple_tech_redesign.branch_u_ema_structure_exit_v1_publish import (
    build_markdown,
    build_sheets,
    write_artifacts,
)
from research.simple_tech_redesign.branch_u_ema_structure_exit_v1_spec import (
    ANALYSIS_ID,
    AND_OR_CLOSED_EXIT,
    AUTO_HOP_U_HH_HL,
    AUTO_HOP_U_PULLBACK,
    AUTO_HOP_U_RCI,
    AUTO_HOP_U_TREND,
    AUTO_HOP_U_VWAP,
    CANDIDATE_ID,
    CONTROL_EXIT,
    E4_PRIOR_CANDIDATE,
    E4_PRIOR_CASE,
    E4_PRIOR_VERDICT,
    EMA_PERIODS,
    EMA_TF,
    EXECUTION,
    FIRST_PROSPECTIVE_DAY,
    FORBIDDEN_INPUT_DAYS,
    LIFECYCLE_PRIMITIVE,
    LIFECYCLE_U_EMA_ADDED_FAIL_HIT_RATE,
    LIFECYCLE_U_EMA_ADDED_KEEP_HIT_RATE,
    LIFECYCLE_U_EMA_BAD_KEEP_RATE_RATIO,
    LIFECYCLE_U_EMA_DAY_AGREE,
    LIFECYCLE_U_EMA_DAY_DISAGREE,
    LIFECYCLE_U_EMA_DAY_USABLE,
    LIFECYCLE_U_EMA_FAIL_HIT_N,
    LIFECYCLE_U_EMA_FAIL_HIT_RATE,
    LIFECYCLE_U_EMA_FAIL_N,
    LIFECYCLE_U_EMA_KEEP_HIT_N,
    LIFECYCLE_U_EMA_KEEP_HIT_RATE,
    LIFECYCLE_U_EMA_KEEP_N,
    LIFECYCLE_U_SUPPORTED,
    MAX_RESEARCH_DATE,
    NEW_ENTRY_FILTER,
    NEW_EXIT_RULE,
    PERSISTENCE_SEARCH,
    PROSPECTIVE_HARVEST_SUSPENDED,
    SIGNAL,
    STATE_MACHINE,
    THRESHOLD_SEARCH,
    TIE_RESOLUTION_RULE,
    TIE_RESOLUTION_SOURCE,
    TRUE_OOS,
    canonical_candidate_spec,
    candidate_identity_hash,
    source_sha256_candidate,
    spec_sha256_candidate,
)
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_harvest import assert_research_day
from research.simple_tech_redesign.precap_entry_quality_existing_data_mechanism_v1_r1_analyze import capture_reporting_state

INTEGRITY_ZERO = (
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "RESEARCH_WRITE_PATH_OVERLAP_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "INPUT_20260903_N",
    "INPUT_20260904_N",
    "FUTURE_DATA_N",
    "PROSPECTIVE_HARVESTER_RUN_N",
    "PRIOR_ARTIFACT_MUTATED_N",
    "FUTURE_QUOTE_CARRYBACK_N",
    "FUTURE_TIMESTAMP_CARRYBACK_N",
    "PRE_FILL_EXIT_N",
    "POST_BE_U_TRIGGER_N",
    "PREDICATE_DRIFT_N",
    "TF1_MISSING_N",
    "FILL_PACK_INCOMPLETE_N",
    "THRESHOLD_SEARCH_N",
    "PERSISTENCE_SEARCH_N",
    "NEW_ENTRY_FILTER_N",
    "NEW_EXIT_RULE_N",
    "AUTO_HOP_N",
)
PRIOR_OUTS = (
    LIFECYCLE_OUT,
    BRANCH_U_OUT,
    BRANCH_P_VWAP_CLOSE_EXIT_V1_OUT,
    BRANCH_P_BB_STRUCTURE_EXIT_V1_OUT,
    E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT,
    POST_BE_SWING_FLOOR_EXIT_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_OUT,
    PRECAP_EXISTING_MECHANISM_V1_R1_OUT,
    PRECAP_T3_SETUP_SEQUENCE_V1_OUT,
    PRECAP_PROSPECTIVE_V1_OUT,
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def _prior_hashes() -> dict[str, str]:
    out = {}
    for folder in PRIOR_OUTS:
        for name in ("report.json", "report.md", "audit.xlsx"):
            out[f"{folder.name}/{name}"] = _sha_file(folder / name)
    return out


def _merge(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    ctrl = _empty_arm()
    treat = _empty_arm()
    rows: list[dict[str, Any]] = []
    causes: list[dict[str, Any]] = []
    leftover_ok = True
    control_sot_ok = True
    treatment_sot_ok = True
    for body in bodies:
        _add_arm(ctrl, dict(body.get("control") or {}))
        _add_arm(treat, dict(body.get("treatment") or {}))
        rows.extend(list(body.get("rows") or []))
        causes.extend(list(body.get("incremental_causes") or []))
        leftover_ok = leftover_ok and bool(body.get("leftover_ok"))
        control_sot_ok = control_sot_ok and bool(body.get("control_sot_ok"))
        treatment_sot_ok = treatment_sot_ok and bool(body.get("treatment_sot_ok"))
    return {
        "control": ctrl,
        "treatment": treat,
        "rows": rows,
        "causes": causes,
        "leftover_ok": leftover_ok,
        "control_sot_ok": control_sot_ok,
        "treatment_sot_ok": treatment_sot_ok,
    }


def _n(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.4g}" if abs(v) < 1 else f"{v:.2f}"
    return str(v)


def _public_pred(pred: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pred.items() if k not in {"pred", "primitive_true_probe"}}


def build_answers(report: dict[str, Any]) -> dict[str, str]:
    dec = dict(report.get("decision") or {})
    dev = dict(report.get("development") or {})
    fwd = dict(report.get("forward") or {})
    ident_d = dict(dev.get("identity") or {})
    ident_f = dict(fwd.get("identity") or {})
    pred = dict(report.get("predicate") or {})
    conc_d = dict(dev.get("concentration") or {})
    incr = dict(dev.get("incremental") or {})
    gates = dict(dec.get("gates") or {})
    return {
        "1": (
            "objective alignment = keep fill coverage; tolerate some ENTRY quality dilution; "
            "process failure trades with Technical EXIT; improve Full Causal Portfolio PnL/PF/MaxDD; then Sizing. "
            "This run does not return to ENTRY precision. E4_THEN_ASK_CROSS_W5 is held. Only EXIT changes."
        ),
        "2": (
            f"prior E4 candidate structural-degeneracy = {E4_PRIOR_VERDICT} CASE {E4_PRIOR_CASE} "
            f"id={E4_PRIOR_CANDIDATE} CANDIDATE_FROZEN=false family CLOSE. "
            "Fallback is reached only after E4 fails to find Ask<=E4 limit in [t0,t0+5]; "
            "requiring Ask<=E4 limit again at W5 made accept set empty (DEV fallback 180, accepted 0, rejected 180)."
        ),
        "3": "why execution retune stopped = NO_ADVERSE candidate is not retuned. Forbidden: +1tick/+2tick/bps/wait/W1/W3/W10/price threshold search.",
        "4": (
            f"why U_EMA selected = Lifecycle {LIFECYCLE_PRIMITIVE} in U_SUPPORTED={list(LIFECYCLE_U_SUPPORTED)}; "
            f"fail_n={LIFECYCLE_U_EMA_FAIL_N} keep_n={LIFECYCLE_U_EMA_KEEP_N} "
            f"fail_hit_n={LIFECYCLE_U_EMA_FAIL_HIT_N} fail_hit_rate={LIFECYCLE_U_EMA_FAIL_HIT_RATE:.4f} "
            f"keep_hit_n={LIFECYCLE_U_EMA_KEEP_HIT_N} keep_hit_rate={LIFECYCLE_U_EMA_KEEP_HIT_RATE:.4f} "
            f"bad_keep_rate_ratio={LIFECYCLE_U_EMA_BAD_KEEP_RATE_RATIO:.2f} "
            f"day usable/agree/disagree={LIFECYCLE_U_EMA_DAY_USABLE}/{LIFECYCLE_U_EMA_DAY_AGREE}/{LIFECYCLE_U_EMA_DAY_DISAGREE} "
            f"added fail/keep={LIFECYCLE_U_EMA_ADDED_FAIL_HIT_RATE:.4f}/{LIFECYCLE_U_EMA_ADDED_KEEP_HIT_RATE:.4f}; "
            f"EXIT_POLICY_CREATED={bool(LIFECYCLE_EXIT_POLICY_CREATED)}; mechanism evidence exists, actual Full Causal EXIT not yet run."
        ),
        "5": (
            "this is not V27 persistence reentry = one-shot Lifecycle U_EMA_STRUCTURE_LOSS event only. "
            "No K, bar-count, duration, 3m/5m persistence. V27 family remains CLOSED."
        ),
        "6": (
            f"exact U_EMA source FILE={pred.get('SOURCE_FILE')} FN={pred.get('SOURCE_FUNCTION')} "
            f"LINE={pred.get('SOURCE_LINE_OR_SYMBOL')} SHA={pred.get('SOURCE_SHA')}"
        ),
        "7": f"exact predicate TEXT={pred.get('EXACT_U_EMA_PREDICATE_TEXT')} DEF={pred.get('EMA_DEFINITION')}",
        "8": (
            f"exact timeframe/bar TF={pred.get('TIMEFRAME')} PERIODS={pred.get('EMA_PERIODS') or EMA_PERIODS} "
            f"BAR={pred.get('BAR_CONSTRUCTION')} FINALIZE={pred.get('BAR_FINALIZATION')} EVENT={pred.get('EVENT_TIMESTAMP')}"
        ),
        "9": f"BE semantics={pred.get('BE_SEMANTICS')} BE is not EXIT.",
        "10": (
            "event ordering = after fill, UNPROVEN race of first net executable BE vs first U_EMA_STRUCTURE_LOSS "
            "on completed 1m bars. Future classification is not used to pick U trades."
        ),
        "11": (
            f"tie semantics SOURCE={pred.get('TIE_RESOLUTION_SOURCE') or TIE_RESOLUTION_SOURCE} "
            f"RULE={pred.get('TIE_RESOLUTION_RULE') or TIE_RESOLUTION_RULE}"
        ),
        "12": f"Control DEV fill_n={ident_d.get('fill_n')} CORE/ADDED={ident_d.get('core_n')}/{ident_d.get('added_n')} PnL={ident_d.get('pnl')} ok={ident_d.get('ok')}",
        "13": f"Control Burned fill_n={ident_f.get('fill_n')} CORE/ADDED={ident_f.get('core_n')}/{ident_f.get('added_n')} PnL={ident_f.get('pnl')} ok={ident_f.get('ok')}",
        "14": f"EMA_FIRST/BE_FIRST/tie N DEV={dev.get('EMA_FIRST_N')}/{dev.get('BE_FIRST_N')}/{dev.get('SAME_TIMESTAMP_N')} Burned={fwd.get('EMA_FIRST_N')}/{fwd.get('BE_FIRST_N')}/{fwd.get('SAME_TIMESTAMP_N')}",
        "15": f"technical EXIT N DEV={dev.get('technical_exit_n')} common_trigger={dev.get('common_trigger_n')} Burned={fwd.get('technical_exit_n')}",
        "16": f"U_EARLY direct delta={_n(dev.get('U_EARLY_NEVER_BE_DIRECT_DELTA'))}",
        "17": f"P_EARLY direct delta={_n(dev.get('P_EARLY_DIRECT_DELTA'))}",
        "18": f"PTF direct delta={_n(dev.get('PTF_DIRECT_DELTA'))}",
        "19": f"GOOD direct delta={_n(dev.get('GOOD_DIRECT_DELTA'))}",
        "20": f"DIP direct delta={_n(dev.get('DIP_DIRECT_DELTA'))}",
        "21": f"protected combined delta GOOD+DIP={_n(dev.get('PROTECTED_KEEP_DIRECT_DELTA'))}",
        "22": f"CORE/ADDED direct={_n(dev.get('CORE_DIRECT_DELTA'))}/{_n(dev.get('ADDED_DIRECT_DELTA'))}",
        "23": f"direct EXIT delta A={_n(dev.get('DIRECT_EXIT_DELTA'))}",
        "24": (
            f"incremental fill N/PnL/PF/win/median/CORE/ADDED/paths="
            f"{incr.get('incremental_fill_n')}/{_n(incr.get('incremental_pnl'))}/{incr.get('PF')}/"
            f"{_n(incr.get('win_rate'))}/{_n(incr.get('median_pnl'))}/"
            f"{incr.get('CORE_n')}/{incr.get('ADDED_n')} "
            f"GOOD/EARLY/DIP/PTF/OTHER={incr.get('GOOD')}/{incr.get('EARLY')}/{incr.get('DIP')}/{incr.get('PTF')}/{incr.get('OTHER')}"
        ),
        "25": f"downstream delta B={_n(dev.get('SLOT_RELEASE_DOWNSTREAM_DELTA'))}",
        "26": f"displaced delta C={_n(dev.get('DISPLACED_TRADE_DELTA'))}",
        "27": f"total causal delta={_n(dev.get('TOTAL_CAUSAL_DELTA'))} decomp_ok={dev.get('decomp_ok')}",
        "28": f"DEV Control/Treatment PnL={_n((dev.get('control') or {}).get('total_pnl'))}/{_n((dev.get('treatment') or {}).get('total_pnl'))}",
        "29": f"DEV PF Control/Treatment={(dev.get('control') or {}).get('PF')}/{(dev.get('treatment') or {}).get('PF')}",
        "30": f"DEV MaxDD Control/Treatment={_n((dev.get('control') or {}).get('max_drawdown'))}/{_n((dev.get('treatment') or {}).get('max_drawdown'))}",
        "31": f"fill count Control/Treatment={(dev.get('control') or {}).get('fill_n')}/{(dev.get('treatment') or {}).get('fill_n')} change={dev.get('fill_n_change')}",
        "32": f"day concentration DEV={conc_d.get('day')} warn={conc_d.get('single_day_contribution_gt_50pct')}",
        "33": f"symbol concentration DEV={conc_d.get('symbol')} warn={conc_d.get('single_symbol_contribution_gt_50pct')} 285A_excluded={conc_d.get('symbol_285A_excluded')}",
        "34": f"Burned U_EARLY delta={_n(fwd.get('U_EARLY_NEVER_BE_DIRECT_DELTA'))}",
        "35": f"Burned protected delta={_n(fwd.get('PROTECTED_KEEP_DIRECT_DELTA'))}",
        "36": f"Burned total causal delta={_n(fwd.get('TOTAL_CAUSAL_DELTA'))}",
        "37": f"Burned PF Control/Treatment={(fwd.get('control') or {}).get('PF')}/{(fwd.get('treatment') or {}).get('PF')}",
        "38": f"Burned MaxDD Control/Treatment={_n((fwd.get('control') or {}).get('max_drawdown'))}/{_n((fwd.get('treatment') or {}).get('max_drawdown'))}",
        "39": f"all gates={gates} all_dev={dec.get('all_development_gates')} burned_ok={dec.get('burned_direction_ok')}",
        "40": f"verdict={dec.get('VERDICT')} CASE {dec.get('CASE')}",
        "41": f"candidate frozen? {dec.get('CANDIDATE_FROZEN')} id={CANDIDATE_ID}",
        "42": f"family closed? {dec.get('FAMILY_CLOSED')}",
        "43": "ENTRY changed? false",
        "44": "execution changed? false E4_THEN_ASK_CROSS_W5 held",
        "45": "EXIT Runtime changed? false research-only",
        "46": "CAP changed? false",
        "47": "sizing changed? false",
        "48": "future data used? false",
        "49": f"MAX_RESEARCH_DATE={MAX_RESEARCH_DATE}",
        "50": f"prospective harvest suspended? {PROSPECTIVE_HARVEST_SUSPENDED}",
        "51": "TRUE_OOS=false",
        "52": "CERTIFIED=false",
        "53": f"submit/cancel/live={report.get('submit_cancel_live')}",
        "54": f"next={dec.get('NEXT')}",
    }


def _stop(msg: str, *, pre: dict[str, Any], leak: dict[str, Any], sha: str, source_sha: str, prior: dict[str, str], pred: dict[str, Any]) -> int:
    decision = {
        "CASE": "E",
        "VERDICT": "SIMPLE_TECH_BRANCH_U_EMA_INTEGRITY_FAILED",
        "NEXT": msg,
        "CANDIDATE_FROZEN": False,
        "FAMILY_CLOSED": False,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "gates": {"1_integrity": False},
    }
    report = {
        "analysis_id": ANALYSIS_ID,
        "blocker": msg,
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "VERDICT": decision["VERDICT"],
            "CASE": "E",
            "CANDIDATE_FROZEN": False,
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CANDIDATE_SPEC_SHA256": sha,
            "SOURCE_SHA256": source_sha,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "FUTURE_DATA_USED": False,
        },
        "decision": decision,
        "predicate": _public_pred(pred),
        "development": {},
        "forward": {},
        "leak": leak,
        "preflight": pre,
        "prior_hashes": prior,
        "answers": {},
        "_markdown": "",
    }
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(msg, flush=True)
    print("STOP.", flush=True)
    return 2


def main() -> int:
    set_research_priority_below_normal()
    sha = spec_sha256_candidate()
    source_sha = source_sha256_candidate()
    ident_hash = candidate_identity_hash(spec_sha=sha, source_sha=source_sha)
    pre = snapshot(phase="PRE")
    prior = _prior_hashes()
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak.update({"SUBMIT_N": int(SUBMIT_N), "CANCEL_N": int(CANCEL_N), "LIVE_ORDER_N": int(LIVE_ORDER_N)})
    leak["NEW_EXIT_RULE_N"] = int(bool(NEW_EXIT_RULE))
    leak["NEW_ENTRY_FILTER_N"] = int(bool(NEW_ENTRY_FILTER))
    leak["THRESHOLD_SEARCH_N"] = int(bool(THRESHOLD_SEARCH))
    leak["PERSISTENCE_SEARCH_N"] = int(bool(PERSISTENCE_SEARCH))
    leak["AUTO_HOP_N"] = int(bool(AUTO_HOP_U_TREND or AUTO_HOP_U_PULLBACK or AUTO_HOP_U_RCI or AUTO_HOP_U_VWAP or AUTO_HOP_U_HH_HL))
    leak["PROSPECTIVE_HARVESTER_RUN_N"] = 0
    leak["FUTURE_DATA_N"] = 0
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    print(
        f"PREFLIGHT candidate={CANDIDATE_ID} spec={sha[:12]} today={TODAY} signal={SIGNAL} exec={EXECUTION} tf={EMA_TF}",
        flush=True,
    )

    pred_pack = recover_lifecycle_u_ema_predicate()
    if not pred_pack.get("ok"):
        leak["PREDICATE_DRIFT_N"] = 1
        return _stop(
            f"STOP. Exact frozen U_EMA predicate restore failed: {pred_pack.get('blocker')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )

    body_life = _load(LIFECYCLE_OUT / "report.json")
    u_supported = list((body_life.get("required") or {}).get("U_SUPPORTED") or [])
    if LIFECYCLE_PRIMITIVE not in u_supported and LIFECYCLE_PRIMITIVE not in list(LIFECYCLE_U_SUPPORTED):
        return _stop(
            "STOP. Lifecycle RCA does not list U_EMA_STRUCTURE_LOSS as supported.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )
    e4_req = dict(_load(E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT / "report.json").get("required") or {})
    if str(e4_req.get("VERDICT") or "") != E4_PRIOR_VERDICT or bool(e4_req.get("CANDIDATE_FROZEN")):
        return _stop(
            "STOP. Prior E4 no-adverse-price result is not CASE B CLOSED / unfrozen.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )

    if v1_spec_sha256() != PARENT_SPEC_SHA256_EXPECTED or not self_check().get("ok"):
        return _stop("STOP. Preflight failed.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if SESSION != "AM" or NEW_EXIT_RULE or THRESHOLD_SEARCH or PERSISTENCE_SEARCH or bool(TRUE_OOS) or NEW_ENTRY_FILTER:
        return _stop("STOP. Forbidden flags.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if AUTO_HOP_U_TREND or AUTO_HOP_U_PULLBACK or AUTO_HOP_U_RCI or AUTO_HOP_U_VWAP or AUTO_HOP_U_HH_HL or AND_OR_CLOSED_EXIT or not PROSPECTIVE_HARVEST_SUSPENDED:
        return _stop("STOP. Auto-hop or AND/OR closed family or prospective flag.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if TODAY in set(ELIGIBLE_DAYS) or TODAY in set(LOCKED_SERIES_DAYS):
        return _stop("STOP. Eligible days include today.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if any(str(d) >= "20260903" for d in list(ELIGIBLE_DAYS) + list(LOCKED_SERIES_DAYS)):
        return _stop("STOP. Day list includes 20260903+.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _stop("STOP. Research write path overlaps live paths.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n(
        [str(LIFECYCLE_OUT / "report.json"), str(E4_FALLBACK_NO_ADVERSE_PRICE_V1_OUT / "report.json")],
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["ACTIVE_CAPTURE_INPUT_N"]):
        return _stop("STOP. Active capture input.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    try:
        residual_dev, residual_fwd, _residual_body = load_residual_rows()
    except (AssertionError, FileNotFoundError) as exc:
        return _stop(f"STOP. Residual SoT: {exc}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)

    harvested_ok = True
    dev_bodies: list[dict[str, Any]] = []
    fwd_bodies: list[dict[str, Any]] = []
    for day in list(ELIGIBLE_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["FUTURE_DATA_N"] = 1
            return _stop(f"STOP. Forbidden DEV day: {day} {blocker}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
        body = harvest_day(day, cohort="DEVELOPMENT", spec_sha=sha, pred_pack=pred_pack, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            return _stop(
                f"STOP. Harvest fail DEVELOPMENT {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                prior=prior,
                pred=pred_pack,
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        dev_bodies.append(body)
    for day in list(LOCKED_SERIES_DAYS):
        blocker = assert_research_day(day, today=TODAY)
        if blocker or str(day) in FORBIDDEN_INPUT_DAYS:
            leak["FUTURE_DATA_N"] = 1
            return _stop(f"STOP. Forbidden BURNED day: {day} {blocker}.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
        body = harvest_day(day, cohort="FORWARD_BURNED", spec_sha=sha, pred_pack=pred_pack, today=TODAY)
        if not body.get("ok"):
            harvested_ok = False
            return _stop(
                f"STOP. Harvest fail BURNED_STRESS {day}: {body.get('blocker')}.",
                pre=pre,
                leak=leak,
                sha=sha,
                source_sha=source_sha,
                prior=prior,
                pred=pred_pack,
            )
        for k, v in dict(body.get("leak") or {}).items():
            leak[k] = int(leak.get(k, 0) or 0) + int(v or 0)
        fwd_bodies.append(body)

    after = _prior_hashes()
    if after != prior:
        leak["PRIOR_ARTIFACT_MUTATED_N"] = 1
        return _stop("STOP. Prior closed-family artifacts mutated.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)
    if (
        int(leak.get("FUTURE_QUOTE_CARRYBACK_N") or 0)
        + int(leak.get("PRE_FILL_EXIT_N") or 0)
        + int(leak.get("POST_BE_U_TRIGGER_N") or 0)
        + int(leak.get("PREDICATE_DRIFT_N") or 0)
        + int(leak.get("TF1_MISSING_N") or 0)
    ):
        return _stop("STOP. Causal integrity leak != 0.", pre=pre, leak=leak, sha=sha, source_sha=source_sha, prior=prior, pred=pred_pack)

    dev_m = _merge(dev_bodies)
    fwd_m = _merge(fwd_bodies)
    ident_dev = evaluate_cohort(
        cohort="DEVELOPMENT",
        days=list(ELIGIBLE_DAYS),
        harvest_rows=list(dev_m.get("rows") or []),
        ctrl_arm=dict(dev_m.get("control") or {}),
        treat_arm=dict(dev_m.get("treatment") or {}),
        residual=residual_index(residual_dev),
        causes=list(dev_m.get("causes") or []),
        leftover_ok=bool(dev_m.get("leftover_ok")),
        control_sot_ok=bool(dev_m.get("control_sot_ok")),
        treatment_sot_ok=bool(dev_m.get("treatment_sot_ok")),
    )
    if not bool(ident_dev.get("identity", {}).get("ok")):
        return _stop(
            f"STOP. DEV CONTROL identity mismatch: {ident_dev.get('identity')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )
    ident_fwd = evaluate_cohort(
        cohort="FORWARD_BURNED",
        days=list(LOCKED_SERIES_DAYS),
        harvest_rows=list(fwd_m.get("rows") or []),
        ctrl_arm=dict(fwd_m.get("control") or {}),
        treat_arm=dict(fwd_m.get("treatment") or {}),
        residual=residual_index(residual_fwd),
        causes=list(fwd_m.get("causes") or []),
        leftover_ok=bool(fwd_m.get("leftover_ok")),
        control_sot_ok=bool(fwd_m.get("control_sot_ok")),
        treatment_sot_ok=bool(fwd_m.get("treatment_sot_ok")),
    )
    if not bool(ident_fwd.get("identity", {}).get("ok")):
        return _stop(
            f"STOP. BURNED CONTROL identity mismatch: {ident_fwd.get('identity')}.",
            pre=pre,
            leak=leak,
            sha=sha,
            source_sha=source_sha,
            prior=prior,
            pred=pred_pack,
        )

    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)
    decision = decide(ident_dev, ident_fwd, leak_ok=leak_ok, harvested_ok=harvested_ok, pred_ok=True)
    post = snapshot(phase="POST")
    report = {
        "analysis_id": ANALYSIS_ID,
        "candidate_id": CANDIDATE_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "candidate_identity_hash": ident_hash,
        "canonical_spec": canonical_candidate_spec(),
        "state_machine": STATE_MACHINE,
        "predicate": _public_pred(pred_pack),
        "e4_prior_closeout": {
            "VERDICT": E4_PRIOR_VERDICT,
            "CASE": E4_PRIOR_CASE,
            "CANDIDATE_ID": E4_PRIOR_CANDIDATE,
            "CANDIDATE_FROZEN": False,
            "family_close": True,
            "retune_forbidden": True,
        },
        "lifecycle_prior": {
            "U_SUPPORTED": list(LIFECYCLE_U_SUPPORTED),
            "EXIT_POLICY_CREATED": bool(LIFECYCLE_EXIT_POLICY_CREATED),
            "fail_hit_rate": LIFECYCLE_U_EMA_FAIL_HIT_RATE,
            "keep_hit_rate": LIFECYCLE_U_EMA_KEEP_HIT_RATE,
            "day_agree": LIFECYCLE_U_EMA_DAY_AGREE,
            "day_disagree": LIFECYCLE_U_EMA_DAY_DISAGREE,
            "not_v27_persistence": True,
            "not_u_bb": True,
        },
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "CANDIDATE_FROZEN": decision.get("CANDIDATE_FROZEN"),
            "FAMILY_CLOSED": decision.get("FAMILY_CLOSED"),
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "CANDIDATE_SPEC_SHA256": sha,
            "SOURCE_SHA256": source_sha,
            "CANDIDATE_IDENTITY_HASH": ident_hash,
            "PARENT_SPEC_SHA256": PARENT_SPEC_SHA256_EXPECTED,
            "first_eligible_prospective_date": FIRST_PROSPECTIVE_DAY,
            "ENTRY_CHANGED": False,
            "EXIT_CHANGED": False,
            "EXECUTION_CHANGED": False,
            "CAP_CHANGED": False,
            "SIZING_CHANGED": False,
            "NEW_EXIT_RULE": False,
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "CONTROL_EXIT": CONTROL_EXIT,
            "AUTO_HOP": False,
            "TIE_RESOLUTION_SOURCE": TIE_RESOLUTION_SOURCE,
            "TIE_RESOLUTION_RULE": TIE_RESOLUTION_RULE,
        },
        "decision": decision,
        "development": ident_dev,
        "forward": ident_fwd,
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "reporting_semantics": reporting_semantics(pre, post),
        "capture_reporting_state": capture_reporting_state(pre, post),
        "submit_cancel_live": {"submit": int(SUBMIT_N), "cancel": int(CANCEL_N), "live_order": int(LIVE_ORDER_N)},
        "prior_hashes": prior,
        "answers": {},
        "_markdown": "",
    }
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    print(
        f"FINAL ANALYSIS_ID={ANALYSIS_ID} CASE={decision.get('CASE')} VERDICT={decision.get('VERDICT')} "
        f"FROZEN={decision.get('CANDIDATE_FROZEN')} DEV_TOTAL={ident_dev.get('TOTAL_CAUSAL_DELTA')} "
        f"out={BRANCH_U_EMA_STRUCTURE_EXIT_V1_OUT}",
        flush=True,
    )
    print("STOP.", flush=True)
    return 0 if harvested_ok and leak_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
