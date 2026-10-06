"""Cohen kappa and confusion. No outcome."""
from __future__ import annotations

from collections import Counter
from typing import Any


def confusion(a: list[str], b: list[str]) -> dict[str, Any]:
    labels = sorted(set(a) | set(b))
    mat = {x: {y: 0 for y in labels} for x in labels}
    n = min(len(a), len(b))
    agree = 0
    for i in range(n):
        mat[a[i]][b[i]] += 1
        if a[i] == b[i]:
            agree += 1
    return {
        "labels": labels,
        "matrix": mat,
        "n": n,
        "agree_n": agree,
        "agree_share": float(agree / n) if n else None,
        "disagreement_n": int(n - agree),
    }


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    n = min(len(a), len(b))
    if n <= 0:
        return None
    pa = float(sum(1 for i in range(n) if a[i] == b[i]) / n)
    ca = Counter(a[:n])
    cb = Counter(b[:n])
    pe = 0.0
    keys = set(ca) | set(cb)
    for k in keys:
        pe += (float(ca.get(k, 0)) / n) * (float(cb.get(k, 0)) / n)
    if pe >= 1.0:
        return 1.0 if pa >= 1.0 else 0.0
    return float((pa - pe) / (1.0 - pe))


def layer_agreement(rows: list[dict[str, Any]], fp_key: str, sp_key: str) -> dict[str, Any]:
    pairs = [(str(r.get(fp_key) or ""), str(r.get(sp_key) or "")) for r in rows if r.get(fp_key) and r.get(sp_key)]
    a = [p[0] for p in pairs]
    b = [p[1] for p in pairs]
    conf = confusion(a, b)
    conf["cohen_kappa"] = cohen_kappa(a, b)
    conf["fp_key"] = fp_key
    conf["sp_key"] = sp_key
    return conf
