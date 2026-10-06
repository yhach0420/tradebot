"""New 200-chart sample. Prior 60 daily bars + selected levels. No future intraday outcome."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np

from research.support_resistance_face_valid_first_interaction_rebuild_v1 import SAMPLE_N, SAMPLE_SEED
from research.support_resistance_face_valid_first_interaction_rebuild_v1.isolation import OUT
from research.support_resistance_face_valid_first_interaction_rebuild_v1.select import select_salient
from research.support_resistance_face_valid_first_interaction_rebuild_v1.zones import snapshot_zones
from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def choose_sample(
    rows: list[dict[str, Any]],
    *,
    exclude: set[tuple[str, str]],
    n: int = SAMPLE_N,
    seed: int = SAMPLE_SEED,
) -> list[tuple[str, str]]:
    rng = np.random.default_rng(int(seed))
    eligible = [
        r
        for r in rows
        if r.get("block") in {"D1", "D2", "D3", "D4"}
        and _finite(r.get("atr"))
        and (str(r["symbol"]), str(r["date"])) not in exclude
    ]
    by_block: dict[str, list[dict[str, Any]]] = {"D1": [], "D2": [], "D3": [], "D4": []}
    for r in eligible:
        by_block[str(r["block"])].append(r)
    per = max(1, n // 4)
    picked: list[tuple[str, str]] = []
    used: set[tuple[str, str]] = set()
    for b in ("D1", "D2", "D3", "D4"):
        pool = list(by_block.get(b) or [])
        cells: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in pool:
            cells.setdefault((str(r.get("vol_regime") or "unknown"), str(r.get("sector") or "")[:24]), []).append(r)
        cell_list = list(cells.values())
        rng.shuffle(cell_list)
        take: list[dict[str, Any]] = []
        while len(take) < min(per, len(pool)):
            progressed = False
            for cell in cell_list:
                if not cell or len(take) >= min(per, len(pool)):
                    continue
                i = int(rng.integers(0, len(cell)))
                take.append(cell.pop(i))
                progressed = True
            if not progressed:
                break
        for r in take:
            key = (str(r["symbol"]), str(r["date"]))
            if key not in used:
                used.add(key)
                picked.append(key)
    if len(picked) < n:
        rest = [r for r in eligible if (str(r["symbol"]), str(r["date"])) not in used]
        rng.shuffle(rest)
        for r in rest:
            if len(picked) >= n:
                break
            picked.append((str(r["symbol"]), str(r["date"])))
    return picked[:n]


def _candle(ax, i: int, o: float, h: float, l: float, c: float) -> None:
    color = "#2E7D32" if c >= o else "#C62828"
    ax.plot([i, i], [l, h], color="#333333", lw=0.7, zorder=2)
    body = max(abs(c - o), (h - l) * 0.02 if h > l else 1e-6)
    ax.add_patch(Rectangle((i - 0.32, min(o, c)), 0.64, body, facecolor=color, edgecolor=color, lw=0, zorder=3))


def render_sample(sample_id: int, pack: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    row = dict(pack["row"])
    snap = pack["snap"]
    sel = pack["sel"]
    hist = list(pack["hist"] or [])
    rx = list(pack["rx"] or [])
    fig, ax = plt.subplots(figsize=(11.2, 5.4), dpi=110)
    idx = {str(d["date"]): i for i, d in enumerate(hist)}
    for i, d in enumerate(hist):
        _candle(ax, i, float(d["open"]), float(d["high"]), float(d["low"]), float(d["close"]))
    for r in rx:
        j = idx.get(str(r.get("pivot_date") or ""))
        if j is None:
            continue
        y = float(r.get("pivot_price") or 0)
        if str(r.get("role")) == "RESISTANCE":
            ax.scatter([j], [y], marker="v", c="#C62828", s=18, zorder=4)
        else:
            ax.scatter([j], [y], marker="^", c="#1565C0", s=18, zorder=4)
    for z in list(snap.get("resistance_active") or []) + list(snap.get("support_active") or []):
        col = "#C62828" if z["role"] == "RESISTANCE" else "#1565C0"
        ax.axhspan(float(z["lo"]), float(z["hi"]), color=col, alpha=0.06, zorder=1)
    for z in list(snap.get("resistance_broken") or []) + list(snap.get("support_broken") or []):
        col = "#C62828" if z["role"] == "RESISTANCE" else "#1565C0"
        ax.axhline(float(z["center"]), color=col, lw=0.6, ls="--", alpha=0.35, zorder=1)
    if sel.get("resistance"):
        z = sel["resistance"]
        ax.axhspan(float(z["lo"]), float(z["hi"]), color="#C62828", alpha=0.22, zorder=1)
        ax.axhline(float(z["center"]), color="#C62828", lw=1.6, zorder=5)
    if sel.get("support"):
        z = sel["support"]
        ax.axhspan(float(z["lo"]), float(z["hi"]), color="#1565C0", alpha=0.22, zorder=1)
        ax.axhline(float(z["center"]), color="#1565C0", lw=1.6, zorder=5)
    if _finite(row.get("open")):
        ax.axhline(float(row["open"]), color="#333333", lw=0.8, ls=":", zorder=5)
    n = len(hist)
    ax.set_xlim(-1, max(n, 1))
    ax.set_title(
        f"{row['symbol']} {row['date']} {row.get('block')} vol={row.get('vol_regime')} "
        f"selR={int(bool(sel.get('resistance')))} selS={int(bool(sel.get('support')))} "
        f"actR={len(snap.get('resistance_active') or [])} actS={len(snap.get('support_active') or [])} "
        f"(prior daily + open; no session outcome)",
        fontsize=8,
    )
    ax.set_xlabel("prior completed sessions")
    ax.set_ylabel("price")
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    rel = f"charts/new_200_only/{int(sample_id):03d}_{row['symbol']}_{row['date']}.png"
    path = out_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return {
        "sample_id": sample_id,
        "symbol": row["symbol"],
        "date": row["date"],
        "block": row.get("block"),
        "sector": row.get("sector"),
        "vol_regime": row.get("vol_regime"),
        "chart_path": rel.replace("\\", "/"),
        "n_res_active": len(snap.get("resistance_active") or []),
        "n_sup_active": len(snap.get("support_active") or []),
        "n_res_broken": len(snap.get("resistance_broken") or []),
        "n_sup_broken": len(snap.get("support_broken") or []),
        "selected_resistance": bool(sel.get("resistance")),
        "selected_support": bool(sel.get("support")),
        "no_level": bool(sel.get("no_salient_resistance") and sel.get("no_salient_support")),
        "open": row.get("open"),
        "atr": row.get("atr"),
        "selected_res_center": (sel.get("resistance") or {}).get("center"),
        "selected_sup_center": (sel.get("support") or {}).get("center"),
        "selected_res_lo": (sel.get("resistance") or {}).get("lo"),
        "selected_res_hi": (sel.get("resistance") or {}).get("hi"),
        "selected_sup_lo": (sel.get("support") or {}).get("lo"),
        "selected_sup_hi": (sel.get("support") or {}).get("hi"),
        "selected_res_pivots": ",".join(sorted({str(m.get("pivot_date") or "") for m in list((sel.get("resistance") or {}).get("members") or [])})),
        "selected_res_confirms": ",".join(sorted({str(m.get("confirmation_date") or "") for m in list((sel.get("resistance") or {}).get("members") or [])})),
        "selected_sup_pivots": ",".join(sorted({str(m.get("pivot_date") or "") for m in list((sel.get("support") or {}).get("members") or [])})),
        "future_intraday_shown": False,
        "excluded_prior_audit_sample": True,
    }


def build_sample_packs(
    *,
    keys: list[tuple[str, str]],
    rows: list[dict[str, Any]],
    hist: dict[str, list[dict[str, Any]]],
    reactions: dict[str, list[dict[str, Any]]],
    disc: list[str],
) -> dict[tuple[str, str], dict[str, Any]]:
    by = {(str(r["symbol"]), str(r["date"])): r for r in rows}
    disc_set = set(disc)
    packs = {}
    for key in keys:
        row = by.get(key)
        if not row:
            continue
        symbol, date = key
        lookback = disc[: disc.index(date)] if date in disc_set else disc
        h = [d for d in list(hist.get(symbol) or []) if str(d["date"]) < date]
        atr = atr20(h)
        rx = [r for r in list(reactions.get(symbol) or []) if str(r.get("confirmation_date") or "") < date]
        snap = snapshot_zones(symbol=symbol, reactions=rx, atr=atr, session_date=date, lookback_dates=lookback, hist=h)
        opn = row.get("open")
        sel = select_salient(snap=snap, open_px=float(opn) if _finite(opn) else float("nan"))
        packs[key] = {"row": row, "snap": snap, "sel": sel, "hist": h[-60:], "rx": rx, "atr": atr}
    return packs


def render_all(keys: list[tuple[str, str]], packs: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    index = []
    for i, key in enumerate(keys, start=1):
        pack = packs.get(key)
        if not pack:
            continue
        index.append(render_sample(i, pack, OUT))
        if i % 40 == 0:
            print(f"CHART {i}/{len(keys)}", flush=True)
    return index
