"""Nested day-cross-fit sequence posteriors. No in-sample meta features."""
from __future__ import annotations

import json
import os
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np

from research.am_entry_information_expansion import TARGET
from research.am_event_sequence_architecture import SEQ_JOINT_SCORE, SEQ_P_LOSS, SEQ_P_NEUTRAL, SEQ_P_WIN
from research.am_event_sequence_architecture.gru import apply_scaler, fit_scaler, predict_proba, train_gru, y_code
from research.canonical_entry_performance_rebase.analyze import row_key

_SEQ_PACK: tuple[str, dict[str, Any]] | None = None


def load_pack(path: str) -> dict[str, Any]:
    global _SEQ_PACK
    if _SEQ_PACK is not None and _SEQ_PACK[0] == path:
        return _SEQ_PACK[1]
    raw = np.load(path, allow_pickle=True)
    keys = [str(k) for k in raw["keys"].tolist()]
    X = np.asarray(raw["X"], dtype=np.float32)
    days = [str(d) for d in raw["days"].tolist()]
    y = np.asarray(raw["y"], dtype=np.int64)
    by_day: dict[str, list[int]] = {}
    key_i = {k: i for i, k in enumerate(keys)}
    for i, d in enumerate(days):
        by_day.setdefault(d, []).append(i)
    pack = {"keys": keys, "X": X, "days": days, "y": y, "by_day": by_day, "key_i": key_i}
    _SEQ_PACK = (path, pack)
    return pack


def _subset(pack: dict[str, Any], days: list[str]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    idx = []
    for d in days:
        idx.extend(pack["by_day"].get(d) or [])
    if not idx:
        z = np.zeros((0, pack["X"].shape[1], pack["X"].shape[2]), dtype=np.float32)
        return z, np.zeros((0,), dtype=np.int64), []
    ii = np.asarray(idx, dtype=int)
    return pack["X"][ii], pack["y"][ii], [pack["keys"][i] for i in ii]


def _to_pred(keys: list[str], proba: np.ndarray) -> dict[str, dict[str, float]]:
    out = {}
    for k, row in zip(keys, proba):
        pw = float(row[0])
        pl = float(row[1])
        pn = float(row[2])
        out[k] = {
            SEQ_P_WIN: pw,
            SEQ_P_LOSS: pl,
            SEQ_P_NEUTRAL: pn,
            SEQ_JOINT_SCORE: pw - pl,
        }
    return out


def process_seq_job(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    cache_path = Path(str(payload.get("cache_path") or ""))
    if cache_path.is_file():
        saved = json.loads(cache_path.read_text(encoding="utf-8"))
        if saved.get("ok") and saved.get("preds"):
            print(f"  seq-cache {payload.get('job_id')}", flush=True)
            return saved
    train_days = [str(d) for d in payload["train_days"]]
    pred_days = [str(d) for d in payload["pred_days"]]
    leak = {
        "SEQ_INNER_HELDOUT_FIT_LEAK_N": 0,
        "SEQ_OUTER_HELDOUT_FIT_LEAK_N": 0,
        "SEQ_SCALER_HELDOUT_USE_N": 0,
        "SEQ_META_IN_SAMPLE_TRAIN_N": 0,
    }
    overlap = set(train_days) & set(pred_days)
    if overlap:
        if str(payload.get("kind") or "") == "seq16":
            leak["SEQ_INNER_HELDOUT_FIT_LEAK_N"] = len(overlap)
        else:
            leak["SEQ_OUTER_HELDOUT_FIT_LEAK_N"] = len(overlap)
        return {"ok": False, "job_id": payload.get("job_id"), "blocker": "HELDOUT_IN_TRAIN", "integrity": leak}
    pack = load_pack(str(payload["pack_path"]))
    Xtr, ytr, _ktr = _subset(pack, train_days)
    scaler = fit_scaler(Xtr)
    Xtr_s = apply_scaler(Xtr, scaler)
    fit = train_gru(Xtr_s, ytr)
    preds: dict[str, dict[str, float]] = {}
    for d in pred_days:
        if d in train_days:
            leak["SEQ_SCALER_HELDOUT_USE_N"] += 1
            leak["SEQ_META_IN_SAMPLE_TRAIN_N"] += 1
            continue
        Xp, _yp, kp = _subset(pack, [d])
        proba = predict_proba(fit, apply_scaler(Xp, scaler))
        preds.update(_to_pred(kp, proba))
    body = {
        "ok": True,
        "job_id": payload.get("job_id"),
        "kind": payload.get("kind"),
        "train_days": train_days,
        "pred_days": pred_days,
        "train_n": int(Xtr.shape[0]),
        "pred_n": len(preds),
        "fit_kind": fit.get("kind"),
        "preds": preds,
        "integrity": leak,
    }
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(body), encoding="utf-8")
    print(
        f"  seq {payload.get('job_id')} kind={payload.get('kind')} train_n={Xtr.shape[0]} "
        f"pred_n={len(preds)} fit={fit.get('kind')}",
        flush=True,
    )
    return body


def seq_jobs(days: list[str], pack_path: Path, cache_dir: Path) -> list[dict[str, Any]]:
    jobs = []
    ds = [str(d) for d in days]
    for a, b in combinations(ds, 2):
        train = [d for d in ds if d not in {a, b}]
        jid = f"seq16|{a}|{b}"
        jobs.append(
            {
                "job_id": jid,
                "kind": "seq16",
                "train_days": train,
                "pred_days": [a, b],
                "pack_path": str(pack_path),
                "cache_path": str(cache_dir / f"seq16_{a}_{b}.json"),
            }
        )
    for outer in ds:
        train = [d for d in ds if d != outer]
        jid = f"seq17|{outer}"
        jobs.append(
            {
                "job_id": jid,
                "kind": "seq17",
                "train_days": train,
                "pred_days": [outer],
                "pack_path": str(pack_path),
                "cache_path": str(cache_dir / f"seq17_{outer}.json"),
            }
        )
    return jobs


def build_stack(seq_got: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    pair: dict[str, dict[str, dict[str, float]]] = {}
    outer: dict[str, dict[str, dict[str, float]]] = {}
    leak = {
        "SEQ_INNER_HELDOUT_FIT_LEAK_N": 0,
        "SEQ_OUTER_HELDOUT_FIT_LEAK_N": 0,
        "SEQ_SCALER_HELDOUT_USE_N": 0,
        "SEQ_META_IN_SAMPLE_TRAIN_N": 0,
    }
    for b in seq_got:
        ig = b.get("integrity") or {}
        for k in leak:
            leak[k] += int(ig.get(k) or 0)
        preds = dict(b.get("preds") or {})
        kind = str(b.get("kind") or "")
        if kind == "seq16":
            pred_days = [str(d) for d in (b.get("pred_days") or [])]
            if len(pred_days) != 2:
                continue
            a, c = sorted(pred_days)
            pair[f"{a}|{c}"] = preds
        elif kind == "seq17":
            outer_day = str((b.get("pred_days") or [""])[0])
            outer[outer_day] = preds
    return {"pair": pair, "outer": outer, "integrity": leak, "days": list(days)}


def pair_key(d1: str, d2: str) -> str:
    a, b = sorted((str(d1), str(d2)))
    return f"{a}|{b}"


def lookup_train(stack: dict[str, Any], outer_day: str, row_day: str, key: str) -> dict[str, float] | None:
    if str(outer_day) == str(row_day):
        return None
    return (stack.get("pair") or {}).get(pair_key(outer_day, row_day), {}).get(key)


def lookup_test(stack: dict[str, Any], outer_day: str, key: str) -> dict[str, float] | None:
    return (stack.get("outer") or {}).get(str(outer_day), {}).get(key)


def attach_seq_fold(
    rows: list[dict[str, Any]],
    stack: dict[str, Any],
    outer_day: str,
    *,
    role: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    leak = {"SEQ_META_IN_SAMPLE_TRAIN_N": 0, "SEQ_LOOKUP_MISS_N": 0}
    out = []
    for r in rows:
        rec = dict(r)
        k = row_key(rec)
        d = str(rec.get("date") or "")
        if role == "train":
            got = lookup_train(stack, outer_day, d, k)
            if d == str(outer_day):
                leak["SEQ_META_IN_SAMPLE_TRAIN_N"] += 1
        else:
            got = lookup_test(stack, outer_day, k)
            if d != str(outer_day):
                leak["SEQ_META_IN_SAMPLE_TRAIN_N"] += 1
        if not got:
            leak["SEQ_LOOKUP_MISS_N"] += 1
            rec[SEQ_P_WIN] = None
            rec[SEQ_P_LOSS] = None
            rec[SEQ_P_NEUTRAL] = None
            rec[SEQ_JOINT_SCORE] = None
        else:
            rec[SEQ_P_WIN] = got.get(SEQ_P_WIN)
            rec[SEQ_P_LOSS] = got.get(SEQ_P_LOSS)
            rec[SEQ_P_NEUTRAL] = got.get(SEQ_P_NEUTRAL)
            rec[SEQ_JOINT_SCORE] = got.get(SEQ_JOINT_SCORE)
        out.append(rec)
    return out, leak


def write_pack(rows: list[dict[str, Any]], X_by_key: dict[str, np.ndarray], path: Path) -> dict[str, Any]:
    keys = []
    xs = []
    days = []
    ys = []
    miss = 0
    for r in rows:
        k = row_key(r)
        x = X_by_key.get(k)
        yi = y_code(r.get(TARGET))
        if x is None or yi is None:
            miss += 1
            continue
        keys.append(k)
        xs.append(x)
        days.append(str(r.get("date") or ""))
        ys.append(int(yi))
    X = np.stack(xs, axis=0) if xs else np.zeros((0, 180, 11), dtype=np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        keys=np.asarray(keys),
        X=X,
        days=np.asarray(days),
        y=np.asarray(ys, dtype=np.int64),
    )
    return {"n": len(keys), "miss": miss, "path": str(path)}
