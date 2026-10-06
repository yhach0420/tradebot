"""Contract-correct Phase 2 DEV rerun. C1 sealed until corrected candidate freeze."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

import numpy as np

from research.causal_driver_pb1 import (
    C1_LAST,
    DEV_LAST,
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_V4_MACHINE_SHA256,
    FV_FIRST,
    PROSPECTIVE_FROM,
)
from research.causal_driver_pb1.contracts.enums import AccessKind, DatasetRole, RunMode
from research.causal_driver_pb1.contracts.errors import FirewallDenied
from research.causal_driver_pb1.datasets.firewall import AccessLedger, request_dataset
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase2_precommit import (
    FX_LOOKBACKS_MIN,
    MKT105_MIN_VALID_SYMBOLS,
    RESPONSE_HORIZONS_MIN,
    SECTOR_CLOCK_COVERAGE_MIN,
)
from research.causal_driver_pb1.phase2_precommit.contamination import contamination_ledger
from research.causal_driver_pb1.phase2_discovery import OFFSETS_MIN, TARGET_SCOPES
from research.causal_driver_pb1.phase2_discovery.access import StageLedger
from research.causal_driver_pb1.phase2_discovery.bind import bind_precommit
from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, N_CLOCK, bar_index_for_available_t
from research.causal_driver_pb1.phase2_discovery.infer import (
    apply_d_gates,
    basket_maps,
    bh_qvalues,
    date_minute_ids,
    fit_sample,
    fx_lookback_cube,
    point_b_fx,
    q5_minus_q1,
    quintile_bounds,
)
from research.causal_driver_pb1.phase2_discovery.panels import load_fx_panel, load_stock_panel
from research.causal_driver_pb1.phase2_discovery.placebos import (
    c1_gates,
    concentration_mkt,
    concentration_sector,
    lead_gates,
    shuffle_pass,
    verify_shuffle,
)
from research.causal_driver_pb1.phase2_corrected_rerun import (
    BACKUP_DRIVER,
    CASE_BLOCKED,
    CASE_FOUND,
    CASE_INVALIDATED,
    CASE_NOT_FOUND_V2,
    EXPECTED_PRECOMMIT_SHA256,
    FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256,
    INDEX_FUTURES_READY,
    INVALID_RUN_CANDIDATE_SHA256,
    LEADER_LAGGARD_READY,
    NEXT_FINALIZE_LL,
    NEXT_PHASE3,
    NEXT_RESOLVE,
    OLD_PHASE2_CLASSIFICATION,
    OLD_PHASE2_VERDICT,
    PROVISIONAL_SELECTION,
    RCA_AFFECTED_CLOCKS,
    RCA_AFFECTED_FRACTION,
    REASON_CONTRACT,
    REASON_DEV_V2,
    USDJPY_STOPPED_V2,
)
from research.causal_driver_pb1.phase2_corrected_rerun.coverage import reconstruct_coverage
from research.causal_driver_pb1.phase2_corrected_rerun.freeze import freeze_corrected
from research.causal_driver_pb1.phase2_corrected_rerun.isolation import CACHE, PHASE2_INVALID_OUT
from research.causal_driver_pb1.response.asof import (
    age_sec_at_decision,
    locf_same_session,
    target_return_and_lag_asof,
)
from research.causal_driver_pb1.response.cases import run_resolver_tests

READY_FOR_FINALIZE = "READY_FOR_FINALIZE_NEXT_DRIVER_SELECTION_V1"

GI_CLOCK = np.array([bar_index_for_available_t(t) for t in CLOCK_MINS], dtype=np.int32)


def _firewall(stage: str) -> dict[str, Any]:
    ledger = AccessLedger(run_id=f"PHASE2_CORRECTED_{stage}", run_mode=RunMode.DRIVER_DISCOVERY)
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="RESEARCH_OBSERVATION_UNIVERSE_105",
        dataset_role=DatasetRole.DEVELOPMENT,
        requested_fields=("symbol", "tse33_code", "tse33_name"),
        access_kind=AccessKind.METADATA,
    )
    role = DatasetRole.DEVELOPMENT if stage == "STAGE_A" else DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id=f"EQUITY_MINUTE_{stage}",
        dataset_role=role,
        requested_fields=("date", "time_label", "close"),
        access_kind=AccessKind.PAYLOAD,
    )
    fv = pr = False
    try:
        request_dataset(
            ledger=ledger,
            run_mode=RunMode.DRIVER_DISCOVERY,
            dataset_id="EQUITY_MINUTE_FV",
            dataset_role=DatasetRole.FROZEN_VALIDATION,
            requested_fields=("close",),
            access_kind=AccessKind.PAYLOAD,
        )
    except FirewallDenied:
        fv = True
    try:
        request_dataset(
            ledger=ledger,
            run_mode=RunMode.DRIVER_DISCOVERY,
            dataset_id="EQUITY_MINUTE_PROSPECTIVE",
            dataset_role=DatasetRole.PROSPECTIVE,
            requested_fields=("close",),
            access_kind=AccessKind.PAYLOAD,
        )
    except FirewallDenied:
        pr = True
    date_ok = True
    for day in (FV_FIRST, PROSPECTIVE_FROM):
        try:
            assert_ingest_date_allowed(day)
            date_ok = False
        except IngestDateDenied:
            pass
    return {"fv_denied": fv, "prospective_denied": pr, "pass": fv and pr and date_ok}


def _pack_controls(scope: str, self_lag: np.ndarray, mkt_lag: np.ndarray) -> list[np.ndarray]:
    if scope == "MKT105_EQW":
        return [mkt_lag]
    return [self_lag, mkt_lag]


def _date_mask(dates: list[str], wanted: set[str]) -> np.ndarray:
    m = np.array([d in wanted for d in dates], dtype=np.bool_)
    return np.repeat(m, N_CLOCK)


def _fx_clock(cube_w: np.ndarray) -> np.ndarray:
    return cube_w[:, GI_CLOCK]


def _build_asof_cache(asof: np.ndarray, bmap: dict[str, np.ndarray]) -> dict[int, dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]]:
    cache: dict[int, dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
    for h in RESPONSE_HORIZONS_MIN:
        cache[int(h)] = {}
        print(f"ASOF_BASKETS horizon={h}", flush=True)
        for scope in TARGET_SCOPES:
            cache[int(h)][scope] = target_return_and_lag_asof(
                asof=asof,
                idx=bmap[scope],
                horizon=int(h),
                is_mkt=scope == "MKT105_EQW",
                min_frac=SECTOR_CLOCK_COVERAGE_MIN,
                min_n=MKT105_MIN_VALID_SYMBOLS,
            )
    return cache


def _subset_point(y, fx, ctr, minute_ids, mask) -> float | None:
    if int(mask.sum()) < 40:
        return None
    return point_b_fx(y[mask], fx[mask], [c[mask] for c in ctr], minute_ids[mask])


def _price_age_summary(src_idx: np.ndarray) -> dict[str, Any]:
    ages = []
    for t in CLOCK_MINS:
        a = age_sec_at_decision(src_idx, int(t))
        v = a[np.isfinite(a)]
        if v.size:
            ages.append(v.astype(np.float64, copy=False))
    if not ages:
        return {"n": 0, "filtered_on_age": False, "stale_threshold_invented": False}
    cat = np.concatenate(ages)
    edges = np.array([0.0, 60.0, 120.0, 180.0, 300.0, 600.0, 1800.0, 3600.0, 1e12], dtype=np.float64)
    counts, _ = np.histogram(cat, bins=edges)
    hist = []
    labels = ["0-60s", "60-120s", "120-180s", "180-300s", "300-600s", "600-1800s", "1800-3600s", "3600s+"]
    for lab, n in zip(labels, counts):
        hist.append({"bucket": lab, "n": int(n)})
    return {
        "n": int(cat.size),
        "mean_sec": float(cat.mean()),
        "p50_sec": float(np.percentile(cat, 50)),
        "p90_sec": float(np.percentile(cat, 90)),
        "p99_sec": float(np.percentile(cat, 99)),
        "max_sec": float(cat.max()),
        "histogram": hist,
        "filtered_on_age": False,
        "stale_threshold_invented": False,
    }


def _invalidation_record() -> dict[str, Any]:
    return {
        "old_phase2_verdict": OLD_PHASE2_VERDICT,
        "old_phase2_classification": OLD_PHASE2_CLASSIFICATION,
        "technical_verdict": CASE_INVALIDATED,
        "reason": REASON_CONTRACT,
        "contract_expected": "last_completed_close available_at<=T to available_at<=T+h",
        "actual_invalid_implementation": "exact grid bar at T-1m and T+h-1m",
        "affected_clock_n": RCA_AFFECTED_CLOCKS,
        "affected_fraction": RCA_AFFECTED_FRACTION,
        "invalid_run_preserved_path": str(PHASE2_INVALID_OUT).replace("\\", "/"),
        "invalid_candidate_list_sha256": INVALID_RUN_CANDIDATE_SHA256,
        "invalid_run_not_overwritten": True,
        "invalid_run_betas_not_used": True,
        "d3_near_misses_not_used": True,
    }


def evaluate() -> dict[str, Any]:
    blockers: list[str] = []
    resolver_tests = run_resolver_tests()
    if not all(r.get("pass") for r in resolver_tests):
        blockers.append("RESOLVER_UNIT_TEST_FAIL")
    identity = bind_identities()
    pre_hashes = frozen_source_hashes()
    if identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256:
        blockers.append("V4_CHANGED")
    if identity.get("COMPLETE_STRATEGY_SHA256") != EXPECTED_COMPLETE_STRATEGY_SHA256:
        blockers.append("COMPLETE_STRATEGY_CHANGED")
    bound = bind_precommit()
    blockers.extend(bound.get("blockers") or [])
    if bound.get("precommit_sha256") != EXPECTED_PRECOMMIT_SHA256:
        blockers.append("PRECOMMIT_SHA_MISMATCH")
    if bound.get("precommit_sha256") == FORBIDDEN_SUPERSEDED_PRECOMMIT_SHA256:
        blockers.append("SUPERSEDED_V1_PRECOMMIT_USED")
    fw_a = _firewall("STAGE_A")
    if not fw_a.get("pass"):
        blockers.append("FIREWALL")
    stage = StageLedger()
    stage.record("STAGE_A_START", precommit_sha256=EXPECTED_PRECOMMIT_SHA256, contract="last_completed_asof")
    inv = _invalidation_record()
    if blockers:
        return _blocked(blockers, bound, identity, stage, fw_a, inv, extra={"resolver_tests": resolver_tests})

    sectors = bound["sectors"]
    dev_dates = list(bound["development_dates"])
    c1_dates = list(bound["c1_dates"])
    eligible = list(bound["eligible_dates"])
    folds = bound["folds"]
    symbols = tuple(r["symbol"] for r in (sectors.get("rows") or []))
    bmap = basket_maps(sectors)

    print("STAGE_A FX", flush=True)
    fx_dev = load_fx_panel(dates=dev_dates, utc_first="20240916", utc_last=DEV_LAST, jst_lo="20240917", jst_hi=DEV_LAST)
    if int(fx_dev.get("conflicting_duplicate_n") or 0) > 0:
        blockers.append("FX_CONFLICTING_DUPLICATE")
    print("STAGE_A STOCK", flush=True)
    stock_dev = load_stock_panel(symbols=symbols, dates=dev_dates, date_lo="20240917", date_hi=DEV_LAST, ledger=stage, stage="STAGE_A")
    print("COVERAGE_BEFORE_RETURNS", flush=True)
    cov = reconstruct_coverage(close=stock_dev["close"], valid=stock_dev["valid"], sectors=sectors)
    asof = cov.pop("asof")
    src_idx = cov.pop("src_idx")
    age = _price_age_summary(src_idx)
    if not cov.get("proceed_to_dev_rerun"):
        blockers.append("COVERAGE_PARITY_FAIL")
        return _blocked(
            blockers,
            bound,
            identity,
            stage,
            fw_a,
            inv,
            extra={"coverage": cov, "resolver_tests": resolver_tests, "price_age": age},
        )
    if blockers:
        return _blocked(
            blockers,
            bound,
            identity,
            stage,
            fw_a,
            inv,
            extra={"coverage": cov, "resolver_tests": resolver_tests, "price_age": age},
        )

    cube = fx_lookback_cube(fx_dev)
    qbounds = {int(w): quintile_bounds(_fx_clock(cube[int(w)]).reshape(-1)) for w in FX_LOOKBACKS_MIN}
    cache = _build_asof_cache(asof, bmap)
    n_d = len(dev_dates)
    date_ids, minute_ids = date_minute_ids(n_d)
    mod5 = np.tile(np.array([t % 5 for t in CLOCK_MINS], dtype=np.int32), n_d)
    months = [d[:6] for d in dev_dates]
    month_u = sorted(set(months))
    month_id = np.repeat(np.array([month_u.index(m) for m in months], dtype=np.int32), N_CLOCK)
    early_set = set(folds.get("DEV_EARLY") or [])
    late_set = set(folds.get("DEV_LATE") or [])
    m_early = _date_mask(dev_dates, early_set)
    m_late = _date_mask(dev_dates, late_set)

    family: list[dict[str, Any]] = []
    print("STAGE_A 288 ASOF", flush=True)
    k = 0
    for h in RESPONSE_HORIZONS_MIN:
        _mkt_y, mkt_lag, _ = cache[int(h)]["MKT105_EQW"]
        mkt_lag_f = mkt_lag.reshape(-1)
        for scope in TARGET_SCOPES:
            y2, self_lag, _cov = cache[int(h)][scope]
            y = y2.reshape(-1)
            self_f = self_lag.reshape(-1)
            for w in FX_LOOKBACKS_MIN:
                fx = _fx_clock(cube[int(w)]).reshape(-1)
                ctr = _pack_controls(scope, self_f, mkt_lag_f)
                pooled = fit_sample(y, fx, ctr, date_ids, minute_ids, n_d)
                pooled.pop("beta", None)
                eb = _subset_point(y, fx, ctr, minute_ids, m_early)
                lb = _subset_point(y, fx, ctr, minute_ids, m_late)
                early = {"ok": eb is not None, "b_fx": eb}
                late = {"ok": lb is not None, "b_fx": lb}
                sub_signs = []
                for r in range(5):
                    pb = _subset_point(y, fx, ctr, minute_ids, mod5 == r)
                    sub_signs.append(0 if pb is None else int(np.sign(pb)))
                same_m = tot_m = 0
                for mi, _month in enumerate(month_u):
                    pb = _subset_point(y, fx, ctr, minute_ids, month_id != mi)
                    tot_m += 1
                    if pooled.get("ok") and pb is not None and int(np.sign(pb)) == int(np.sign(pooled["b_fx"])):
                        same_m += 1
                lomo = (same_m / tot_m) if tot_m else None
                q51 = q5_minus_q1(y, fx, qbounds[int(w)]) if pooled.get("ok") else None
                rec = {
                    "target_scope": scope,
                    "fx_lookback": int(w),
                    "response_horizon": int(h),
                    **pooled,
                    "quintile_boundaries": {**qbounds[int(w)], "lookback": int(w)},
                }
                family.append(apply_d_gates(rec=rec, early=early, late=late, q51=q51, sub_signs=sub_signs, lomo_frac=lomo, qval=1.0))
                k += 1
                if k % 24 == 0:
                    print(f"STAGE_A tests {k}/288", flush=True)

    pvals = np.array([r["p_boot"] if r.get("ok") else 1.0 for r in family], dtype=np.float64)
    qvals = bh_qvalues(pvals)
    slim = []
    for rec, qv in zip(family, qvals):
        rec["q"] = float(qv)
        rec["D3"] = bool(qv <= 0.05)
        rec["candidate"] = bool(rec["D1"] and rec["D2"] and rec["D3"] and rec["D4"] and rec["D5"] and rec["D6"])
        rec["direction"] = ("POS" if rec["b_fx"] > 0 else "NEG") if rec.get("ok") and rec.get("b_fx") is not None else None
        slim.append(rec)

    cands = [r for r in slim if r.get("candidate")]
    freeze_rows = [{k: r[k] for k in r if not str(k).startswith("_")} for r in cands]
    payload, cand_sha = freeze_corrected(freeze_rows)
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "candidate_list.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
        encoding="utf-8",
    )
    stage.freeze_candidates(cand_sha, len(payload["candidates"]))
    stage.record("STAGE_A_COMPLETE", candidate_n=len(payload["candidates"]), candidate_list_sha256=cand_sha)

    miss = _missingness(cube, cache)
    if miss.get("block"):
        blockers.append("MISSINGNESS_FX_STATE_DEPENDENCE")
    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    if stage.c1_rows_read_before_candidate_freeze > 0:
        blockers.append("C1_READ_BEFORE_FREEZE")
    if blockers:
        return _blocked(
            blockers,
            bound,
            identity,
            stage,
            fw_a,
            inv,
            extra={
                "candidate_list_sha256": cand_sha,
                "coverage": cov,
                "resolver_tests": resolver_tests,
                "price_age": age,
            },
        )

    selection = {
        "status": PROVISIONAL_SELECTION,
        "INDEX_FUTURES_READY": INDEX_FUTURES_READY,
        "LEADER_LAGGARD_READY": LEADER_LAGGARD_READY,
        "BACKUP_DRIVER": BACKUP_DRIVER,
        "leader_laggard_precommit_started": False,
    }

    if not payload["candidates"]:
        selection["status"] = READY_FOR_FINALIZE
        return _finish(
            verdict=CASE_NOT_FOUND_V2,
            nxt=NEXT_FINALIZE_LL,
            reason=REASON_DEV_V2,
            bound=bound,
            identity=identity,
            stage=stage,
            family=slim,
            payload=payload["candidates"],
            freeze_obj=payload,
            cand_sha=cand_sha,
            c1_rows=[],
            final=[],
            miss=miss,
            fw_a=fw_a,
            fw_b=None,
            opened_c1=False,
            inv=inv,
            cov=cov,
            age=age,
            selection=selection,
            usdjpy_status=USDJPY_STOPPED_V2,
            resolver_tests=resolver_tests,
        )

    selection["status"] = "USDJPY_REMAINS_ACTIVE_C1_REQUIRED"
    fw_b = _firewall("STAGE_B")
    if not fw_b.get("pass"):
        return _blocked(
            ["FIREWALL_STAGE_B"],
            bound,
            identity,
            stage,
            fw_a,
            inv,
            extra={"candidate_list_sha256": cand_sha, "resolver_tests": resolver_tests, "coverage": cov},
        )
    stage.enter_stage_b()
    print("STAGE_B FX", flush=True)
    fx_c1 = load_fx_panel(dates=c1_dates, utc_first="20251126", utc_last=C1_LAST, jst_lo="20251127", jst_hi=C1_LAST)
    print("STAGE_B STOCK", flush=True)
    stock_c1 = load_stock_panel(symbols=symbols, dates=c1_dates, date_lo="20251127", date_hi=C1_LAST, ledger=stage, stage="STAGE_B")
    if stage.c1_rows_read_before_candidate_freeze > 0:
        return _blocked(
            ["C1_READ_BEFORE_FREEZE"],
            bound,
            identity,
            stage,
            fw_a,
            inv,
            extra={"candidate_list_sha256": cand_sha, "resolver_tests": resolver_tests, "coverage": cov},
        )
    asof_c1, _src_c1 = locf_same_session(np.where(stock_c1["valid"] & (stock_c1["close"] > 0), stock_c1["close"], np.nan))
    cube_c1 = fx_lookback_cube(fx_c1)
    cache_c1 = _build_asof_cache(asof_c1, bmap)
    n_c = len(c1_dates)
    d_c, m_c = date_minute_ids(n_c)
    c1_month_u = sorted({d[:6] for d in c1_dates})
    c1_month_id = np.repeat(np.array([c1_month_u.index(d[:6]) for d in c1_dates], dtype=np.int32), N_CLOCK)
    fold_masks = {
        "C1_EARLY": _date_mask(c1_dates, set(folds.get("C1_EARLY") or [])),
        "C1_MIDDLE": _date_mask(c1_dates, set(folds.get("C1_MIDDLE") or [])),
        "C1_LATE": _date_mask(c1_dates, set(folds.get("C1_LATE") or [])),
    }
    fx_by_date: dict[str, dict[int, np.ndarray]] = {}
    for w in FX_LOOKBACKS_MIN:
        for i, d in enumerate(dev_dates):
            fx_by_date.setdefault(d, {})[int(w)] = cube[int(w)][i]
        for i, d in enumerate(c1_dates):
            fx_by_date.setdefault(d, {})[int(w)] = cube_c1[int(w)][i]
    sh_pack = verify_shuffle(eligible)
    maps = list(sh_pack.get("maps") or [])
    unsh = set(sh_pack.get("unshufflable") or [])
    asof_valid_c1 = np.isfinite(asof_c1) & (asof_c1 > 0)

    c1_out = []
    for locked in payload["candidates"]:
        scope = locked["target_scope"]
        w = int(locked["fx_lookback"])
        h = int(locked["response_horizon"])
        dev_b = float(locked["DEV_b_fx"])
        y2, self_lag, _ = cache_c1[h][scope]
        _, mkt_lag, _ = cache_c1[h]["MKT105_EQW"]
        y = y2.reshape(-1)
        fx = _fx_clock(cube_c1[w]).reshape(-1)
        ctr = _pack_controls(scope, self_lag.reshape(-1), mkt_lag.reshape(-1))
        pooled = fit_sample(y, fx, ctr, d_c, m_c, n_c)
        pooled.pop("beta", None)
        fold_recs = {}
        for name, mask in fold_masks.items():
            pb = _subset_point(y, fx, ctr, m_c, mask)
            fold_recs[name] = {"ok": pb is not None, "b_fx": pb}
        q51 = q5_minus_q1(y, fx, locked["DEV_quintile_boundaries"])
        same_m = tot_m = 0
        for mi, _month in enumerate(c1_month_u):
            pb = _subset_point(y, fx, ctr, m_c, c1_month_id != mi)
            tot_m += 1
            if pooled.get("ok") and pb is not None and int(np.sign(pb)) == int(np.sign(dev_b)):
                same_m += 1
        gated = c1_gates(rec=pooled, fold_recs=fold_recs, q51=q51, lomo_frac=(same_m / tot_m) if tot_m else None, dev_b=dev_b)
        gated.update({"target_scope": scope, "fx_lookback": w, "response_horizon": h, "direction": locked["direction"], "DEV_b_fx": dev_b})
        if gated.get("c1_confirmed"):
            off_b: dict[int, float | None] = {}
            for off in OFFSETS_MIN:
                fx_off = _offset_fx(cube_c1[w], int(off)).reshape(-1)
                off_b[int(off)] = point_b_fx(y, fx_off, ctr, m_c)
            lead = lead_gates(off_b, dev_b=dev_b, c1_confirmed=True)
            gated["lead"] = lead
            if lead.get("lead_pass"):
                mask = np.isfinite(y) & np.isfinite(fx)
                for c in ctr:
                    mask = mask & np.isfinite(c)
                date_str = np.repeat(np.array(c1_dates), N_CLOCK)[mask]
                clock_idx = np.tile(np.arange(N_CLOCK), n_c)[mask]
                ctrl_m = np.column_stack([c[mask] for c in ctr])
                sh = shuffle_pass(
                    y=y[mask],
                    fx_by_date={d: v[w] for d, v in fx_by_date.items()},
                    date_str=date_str,
                    clock_idx=clock_idx,
                    controls=ctrl_m,
                    minute_ids=m_c[mask],
                    maps=maps,
                    unshufflable=unsh,
                    real_abs=abs(float(gated["b_fx"])),
                )
                gated["shuffle"] = sh
                if sh.get("ok"):
                    dates_sel = np.repeat(np.arange(n_c), N_CLOCK)[mask]
                    if scope == "MKT105_EQW":
                        conc = concentration_mkt(
                            close=asof_c1,
                            valid=asof_valid_c1,
                            horizon=h,
                            fx=fx[mask],
                            controls=ctrl_m,
                            minute_ids=m_c[mask],
                            dates_sel=dates_sel,
                            clock_sel=clock_idx,
                            dev_sign=int(np.sign(dev_b)),
                        )
                    else:
                        conc = concentration_sector(
                            close=asof_c1,
                            valid=asof_valid_c1,
                            idx=bmap[scope],
                            horizon=h,
                            fx=fx[mask],
                            controls=ctrl_m,
                            minute_ids=m_c[mask],
                            dates_sel=dates_sel,
                            clock_sel=clock_idx,
                            dev_sign=int(np.sign(dev_b)),
                        )
                    gated["concentration"] = conc
                    gated["final_pass"] = bool(conc.get("ok"))
                else:
                    gated["final_pass"] = False
            else:
                gated["final_pass"] = False
        else:
            gated["final_pass"] = False
        c1_out.append(gated)

    final = [r for r in c1_out if r.get("final_pass")]
    if final:
        verdict, nxt, reason, status = CASE_FOUND, NEXT_PHASE3, None, "USDJPY_ACTIVE_LEAD_FOUND"
        selection["status"] = "USDJPY_REMAINS_ACTIVE_DO_NOT_SWITCH_TO_LEADER_LAGGARD"
    else:
        verdict, nxt, reason, status = CASE_NOT_FOUND_V2, NEXT_FINALIZE_LL, "NO_CANDIDATE_SURVIVED_C1_OR_PLACEBO", USDJPY_STOPPED_V2
        selection["status"] = READY_FOR_FINALIZE
    return _finish(
        verdict=verdict,
        nxt=nxt,
        reason=reason,
        bound=bound,
        identity=identity,
        stage=stage,
        family=slim,
        payload=payload["candidates"],
        freeze_obj=payload,
        cand_sha=cand_sha,
        c1_rows=c1_out,
        final=final,
        miss=miss,
        fw_a=fw_a,
        fw_b=fw_b,
        opened_c1=True,
        inv=inv,
        cov=cov,
        age=age,
        selection=selection,
        usdjpy_status=status,
        resolver_tests=resolver_tests,
    )


def _offset_fx(cube_w: np.ndarray, offset: int) -> np.ndarray:
    out = np.full((cube_w.shape[0], N_CLOCK), np.nan, dtype=np.float64)
    for ci, t in enumerate(CLOCK_MINS):
        gi = bar_index_for_available_t(int(t) + int(offset))
        if 0 <= gi < cube_w.shape[1]:
            out[:, ci] = cube_w[:, gi]
    return out


def _missingness(cube: dict[int, np.ndarray], cache: dict[int, dict[str, Any]]) -> dict[str, Any]:
    fx5 = _fx_clock(cube[5]).reshape(-1)
    y = cache[5]["MKT105_EQW"][0].reshape(-1)
    ok_fx = np.isfinite(fx5)
    miss_y = ~np.isfinite(y)
    rate_fx = float(1.0 - ok_fx.mean()) if fx5.size else 1.0
    rate_y = float(miss_y.mean()) if y.size else 1.0
    st = None
    block = False
    if int(ok_fx.sum()) > 100:
        v = np.abs(fx5[ok_fx])
        q20, q80 = np.percentile(v, [20, 80])
        lo = miss_y[ok_fx][v <= q20]
        hi = miss_y[ok_fx][v >= q80]
        st = {"q1_y_missing": float(lo.mean()) if lo.size else None, "q5_y_missing": float(hi.mean()) if hi.size else None}
        if lo.size and hi.size and (float(hi.mean()) - float(lo.mean()) > 0.25) and float(hi.mean()) > 0.40:
            block = True
    if rate_fx > 0.50:
        block = True
    return {"fx_feature_missing_rate": rate_fx, "stock_target_missing_rate": rate_y, "by_fx_strength": st, "block": block, "status": "BLOCK" if block else "PASS"}


def _blocked(blockers, bound, identity, stage, fw_a, inv, extra=None) -> dict[str, Any]:
    extra = extra or {}
    return {
        "ok": False,
        "VERDICT": CASE_BLOCKED,
        "NEXT": NEXT_RESOLVE,
        "blockers": list(dict.fromkeys(blockers)),
        "reason": "DATA_OR_IDENTITY_OR_SEMANTIC_BLOCK",
        "precommit_sha256": EXPECTED_PRECOMMIT_SHA256,
        "technical_invalidation": inv,
        "old_phase2_classification": OLD_PHASE2_CLASSIFICATION,
        "coverage": extra.get("coverage"),
        "DEV_candidate_n": 0,
        "candidate_list_sha256": extra.get("candidate_list_sha256"),
        "C1_rows_read_before_candidate_freeze": stage.c1_rows_read_before_candidate_freeze,
        "C1_rows_read_before_corrected_candidate_freeze": stage.c1_rows_read_before_candidate_freeze,
        "identity": {k: v for k, v in identity.items() if k != "source_inventory"},
        "firewall": fw_a,
        "access": stage.snapshot(),
        "resolver_tests": extra.get("resolver_tests") or [],
        "price_age": extra.get("price_age"),
        "leader_laggard_selection_status": PROVISIONAL_SELECTION,
        "selection": {
            "status": PROVISIONAL_SELECTION,
            "INDEX_FUTURES_READY": INDEX_FUTURES_READY,
            "LEADER_LAGGARD_READY": LEADER_LAGGARD_READY,
            "BACKUP_DRIVER": BACKUP_DRIVER,
            "leader_laggard_precommit_started": False,
        },
        "USDJPY_DRIVER_FAMILY_STATUS": None,
        "C1_opened": False,
        "PHASE2_OUTCOMES_OPENED": False,
        "FROZEN_VALIDATION_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "V4_CHANGED": "V4_CHANGED" in blockers,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "family": [],
        "c1": [],
        "final": [],
    }


def _public_family(family: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = (
        "target_scope",
        "fx_lookback",
        "response_horizon",
        "direction",
        "ok",
        "n",
        "b_fx",
        "ci_lo",
        "ci_hi",
        "p_boot",
        "q",
        "q5_minus_q1",
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "D6",
        "candidate",
        "early_b_fx",
        "late_b_fx",
        "subgrid_same_sign_n",
        "lomo_same_sign_frac",
        "ci_excludes_0",
    )
    return [{k: r.get(k) for k in keep} for r in family]


def _top_failed(family: list[dict[str, Any]], c1_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for r in family:
        if r.get("candidate"):
            continue
        failed_at = next((k for k in ("D1", "D2", "D3", "D4", "D5", "D6") if not r.get(k)), "DEV")
        scored.append((sum(bool(r.get(k)) for k in ("D1", "D2", "D3", "D4", "D5", "D6")), abs(r.get("b_fx") or 0.0), r, failed_at, "DEV"))
    for r in c1_rows:
        if r.get("final_pass"):
            continue
        if not r.get("c1_confirmed"):
            failed_at = next((k for k in ("C1", "C2", "C3", "C4", "C5", "C6") if not r.get(k)), "C1")
            scored.append((6, abs(r.get("b_fx") or 0.0), r, failed_at, "C1"))
        elif not (r.get("lead") or {}).get("lead_pass"):
            scored.append((8, abs(r.get("b_fx") or 0.0), r, "LEAD", "LEAD"))
        elif not (r.get("shuffle") or {}).get("ok"):
            scored.append((9, abs(r.get("b_fx") or 0.0), r, "DAY_SHUFFLE", "SHUFFLE"))
        else:
            scored.append((10, abs(r.get("b_fx") or 0.0), r, "CONCENTRATION", "CONC"))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    top = []
    for _g, _ab, r, failed_at, stg in scored[:10]:
        top.append(
            {
                "candidate": f"{r.get('target_scope')}|{r.get('fx_lookback')}|{r.get('response_horizon')}",
                "failed_at": failed_at,
                "stage": stg,
                "DEV_b_fx": r.get("DEV_b_fx", r.get("b_fx")),
                "DEV_CI": [r.get("ci_lo"), r.get("ci_hi")] if stg == "DEV" else None,
                "DEV_q": r.get("q"),
            }
        )
    return top


def _finish(**kw) -> dict[str, Any]:
    family = kw["family"]
    payload = kw["payload"]
    c1_rows = kw["c1_rows"]
    final = kw["final"]
    stage: StageLedger = kw["stage"]
    pub_family = _public_family(family)
    c1_pub = []
    for r in c1_rows:
        c1_pub.append(
            {
                "target_scope": r.get("target_scope"),
                "fx_lookback": r.get("fx_lookback"),
                "response_horizon": r.get("response_horizon"),
                "direction": r.get("direction"),
                "DEV_b_fx": r.get("DEV_b_fx"),
                "b_fx": r.get("b_fx"),
                "ci_lo": r.get("ci_lo"),
                "ci_hi": r.get("ci_hi"),
                "C1": r.get("C1"),
                "C2": r.get("C2"),
                "C3": r.get("C3"),
                "C4": r.get("C4"),
                "C5": r.get("C5"),
                "C6": r.get("C6"),
                "c1_confirmed": r.get("c1_confirmed"),
                "lead": r.get("lead"),
                "shuffle": r.get("shuffle"),
                "concentration": r.get("concentration"),
                "final_pass": r.get("final_pass"),
            }
        )
    fail_counts = Counter()
    for r in pub_family:
        if not r.get("candidate"):
            fail_counts[next((k for k in ("D1", "D2", "D3", "D4", "D5", "D6") if not r.get(k)), "DEV_OTHER")] += 1
    bound = kw["bound"]
    return {
        "ok": kw["verdict"] != CASE_BLOCKED,
        "VERDICT": kw["verdict"],
        "NEXT": kw["nxt"],
        "reason": kw.get("reason"),
        "technical_invalidation": kw.get("inv"),
        "old_phase2_classification": OLD_PHASE2_CLASSIFICATION,
        "precommit_sha256": EXPECTED_PRECOMMIT_SHA256,
        "coverage": kw.get("cov"),
        "price_age": kw.get("age"),
        "DEV_all_288_n": len(pub_family),
        "DEV_candidate_n": len(payload),
        "candidate_list_sha256": kw["cand_sha"],
        "corrected_freeze": kw.get("freeze_obj"),
        "C1_rows_read_before_candidate_freeze": stage.c1_rows_read_before_candidate_freeze,
        "C1_rows_read_before_corrected_candidate_freeze": stage.c1_rows_read_before_candidate_freeze,
        "C1_confirmed_n": sum(1 for r in c1_pub if r.get("c1_confirmed")),
        "lead_gate_pass_n": sum(1 for r in c1_pub if (r.get("lead") or {}).get("lead_pass")),
        "day_shuffle_pass_n": sum(1 for r in c1_pub if (r.get("shuffle") or {}).get("ok")),
        "concentration_pass_n": sum(1 for r in c1_pub if (r.get("concentration") or {}).get("ok")),
        "final_pass_n": len(final),
        "final_candidates": [
            {
                "target_scope": r.get("target_scope"),
                "fx_lookback": r.get("fx_lookback"),
                "response_horizon": r.get("response_horizon"),
                "direction": r.get("direction"),
                "DEV_b_fx": r.get("DEV_b_fx"),
                "C1_b_fx": r.get("b_fx"),
            }
            for r in c1_pub
            if r.get("final_pass")
        ],
        "top_failed_candidates": _top_failed(family, c1_rows),
        "failure_stage_counts": dict(fail_counts),
        "eligible_dev_n": len(bound.get("development_dates") or []),
        "eligible_c1_n": len(bound.get("c1_dates") or []),
        "missingness_status": (kw.get("miss") or {}).get("status"),
        "missingness": kw.get("miss"),
        "family": pub_family,
        "candidate_payload": payload,
        "c1": c1_pub,
        "access": stage.snapshot(),
        "firewall_a": kw.get("fw_a"),
        "firewall_b": kw.get("fw_b"),
        "contamination": contamination_ledger(),
        "leader_laggard_selection_status": (kw.get("selection") or {}).get("status"),
        "selection": kw.get("selection"),
        "USDJPY_DRIVER_FAMILY_STATUS": kw.get("usdjpy_status"),
        "C1_opened": bool(kw.get("opened_c1")),
        "PHASE2_OUTCOMES_OPENED": True,
        "FROZEN_VALIDATION_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "identity": {k: v for k, v in (kw.get("identity") or {}).items() if k != "source_inventory"},
        "research_only": True,
        "response_contract": "last_completed_close_available_at_le_T",
        "same_session_only": True,
        "resolver_tests": kw.get("resolver_tests") or [],
    }
