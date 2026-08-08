"""Reference EXIT ledgers: once per EXIT × once per anchor."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x26_exit_library.exits import ExitSpec, simulate_exit

from . import FRESHNESS_PRIMARY_SEC


def simulate_reference(
    *,
    spec: ExitSpec,
    entry_epoch: float,
    entry_price: float,
    date: str,
    session: str,
    times: np.ndarray,
    prices: np.ndarray,
) -> dict[str, Any]:
    """Wrap simulate_exit with freshness / ledger status."""
    if times.size == 0 or entry_price is None or entry_price <= 0:
        return {"ledger_status": "PATH_UNAVAILABLE", "valid": False}
    sess_end = session_end_epoch(date, session)
    rem = sess_end - entry_epoch
    if rem < 1e-9:
        return {"ledger_status": "HORIZON_CENSORED", "valid": False}

    res = simulate_exit(
        spec=spec,
        entry_epoch=entry_epoch,
        entry_price=entry_price,
        date=date,
        session=session,
        times=times,
        prices=prices,
    )
    if res is None:
        return {"ledger_status": "PATH_UNAVAILABLE", "valid": False}

    exit_t = entry_epoch + float(res["hold_sec"])
    i = int(np.searchsorted(times, exit_t, side="right") - 1)
    if i < 0:
        i = 0
    asof_t = float(times[min(i, times.size - 1)])
    bound = min(entry_epoch + spec.max_hold_sec, sess_end)
    if res["exit_reason"] in ("session_close", "max_hold_exit"):
        price_age = max(0.0, bound - asof_t)
    else:
        price_age = max(0.0, exit_t - asof_t)

    if price_age > FRESHNESS_PRIMARY_SEC:
        return {
            "ledger_status": "REFERENCE_EXIT_PRICE_UNAVAILABLE",
            "valid": False,
            "price_age_sec": price_age,
            "exit_reason": res["exit_reason"],
            "hold_sec": res["hold_sec"],
        }

    ret_bps = float(res["exit_price"] / entry_price - 1.0) * 10000.0
    mfe = float(res.get("MFE_at_exit_bps", np.nan))
    mae = float(res.get("MAE_at_exit_bps", np.nan))
    return {
        "ledger_status": "OK",
        "valid": True,
        "exit_reason": res["exit_reason"],
        "hold_sec": float(res["hold_sec"]),
        "exit_price": float(res["exit_price"]),
        "reference_return_bps": ret_bps,
        "reference_pnl_yen_100": entry_price * (ret_bps / 10000.0) * 100.0,
        "MFE_until_exit_bps": mfe,
        "MAE_until_exit_bps": mae,
        "price_age_sec": price_age,
    }


def _build_one_exit(
    spec: ExitSpec,
    rows: list[dict[str, Any]],
    times_list: list[np.ndarray],
    prices_list: list[np.ndarray],
) -> tuple[str, dict[str, np.ndarray]]:
    n = len(rows)
    valid = np.zeros(n, dtype=bool)
    ret = np.full(n, np.nan)
    pnl = np.full(n, np.nan)
    hold = np.full(n, np.nan)
    mfe = np.full(n, np.nan)
    mae = np.full(n, np.nan)
    reason = np.array([""] * n, dtype=object)
    status = np.array(["PATH_UNAVAILABLE"] * n, dtype=object)
    for i, r in enumerate(rows):
        px0 = r.get("CurrentPrice")
        if px0 is None:
            continue
        rec = simulate_reference(
            spec=spec,
            entry_epoch=float(r["grid_epoch"]),
            entry_price=float(px0),
            date=r["date"],
            session=r["session"],
            times=times_list[i],
            prices=prices_list[i],
        )
        status[i] = rec["ledger_status"]
        if not rec.get("valid"):
            continue
        valid[i] = True
        ret[i] = rec["reference_return_bps"]
        pnl[i] = rec["reference_pnl_yen_100"]
        hold[i] = rec["hold_sec"]
        mfe[i] = rec["MFE_until_exit_bps"]
        mae[i] = rec["MAE_until_exit_bps"]
        reason[i] = rec["exit_reason"]
    return spec.exit_id, {
        "valid": valid, "ret_bps": ret, "pnl": pnl, "hold": hold,
        "mfe": mfe, "mae": mae, "reason": reason, "status": status,
    }


def build_exit_matrices(
    *,
    rows: list[dict[str, Any]],
    times_list: list[np.ndarray],
    prices_list: list[np.ndarray],
    specs: list[ExitSpec],
    max_workers: int = 4,
) -> dict[str, dict[str, np.ndarray]]:
    out: dict[str, dict[str, np.ndarray]] = {}
    n = len(rows)
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(specs)))) as ex:
        futs = [ex.submit(_build_one_exit, spec, rows, times_list, prices_list) for spec in specs]
        for fut in as_completed(futs):
            eid, mat = fut.result()
            out[eid] = mat
            print(f"  exit {eid}: ok={int(mat['valid'].sum())}/{n}", flush=True)
    return out
