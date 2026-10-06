"""Frozen CAUSAL_GRU16. No architecture / epoch / hidden search."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import torch
import torch.nn as nn
from torch.nn.utils import clip_grad_norm_

from research.am_entry_information_expansion import CLASS_LOSS, CLASS_NEUTRAL, CLASS_WIN
from research.am_event_sequence_architecture import (
    ADAMW_LR,
    ADAMW_WD,
    AVAIL_IDX,
    GRAD_CLIP,
    GRU_BATCH,
    GRU_BIDIRECTIONAL,
    GRU_EPOCHS,
    GRU_HIDDEN,
    GRU_LAYERS,
    GRU_SEED,
    SEQUENCE_CHANNEL_N,
    SEQUENCE_LEN,
    STATE_IDX,
)

CLASS_INDEX = {CLASS_WIN: 0, CLASS_LOSS: 1, CLASS_NEUTRAL: 2}
INDEX_CLASS = {0: CLASS_WIN, 1: CLASS_LOSS, 2: CLASS_NEUTRAL}


class CausalGRU16(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.gru = nn.GRU(
            input_size=int(SEQUENCE_CHANNEL_N),
            hidden_size=int(GRU_HIDDEN),
            num_layers=int(GRU_LAYERS),
            batch_first=True,
            bidirectional=bool(GRU_BIDIRECTIONAL),
        )
        self.ln = nn.LayerNorm(int(GRU_HIDDEN))
        self.head = nn.Linear(int(GRU_HIDDEN), 3)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        _out, h = self.gru(x)
        h_last = h[-1]
        return self.head(self.ln(h_last))


def _seed_all(seed: int) -> None:
    np.random.seed(int(seed))
    torch.manual_seed(int(seed))


def fit_scaler(X: np.ndarray) -> dict[str, np.ndarray]:
    n, t, c = X.shape
    med = np.zeros(c, dtype=np.float64)
    iqr = np.ones(c, dtype=np.float64)
    avail = X[:, :, AVAIL_IDX] > 0.5
    for ci in range(c):
        if ci == AVAIL_IDX:
            continue
        if ci in STATE_IDX:
            vals = X[:, :, ci][avail]
        else:
            vals = X[:, :, ci].reshape(-1)
        if vals.size == 0:
            med[ci] = 0.0
            iqr[ci] = 1.0
            continue
        med[ci] = float(np.median(vals))
        q75 = float(np.percentile(vals, 75))
        q25 = float(np.percentile(vals, 25))
        span = q75 - q25
        iqr[ci] = float(span) if span > 0 else 1.0
    return {"median": med.astype(np.float32), "iqr": iqr.astype(np.float32)}


def apply_scaler(X: np.ndarray, scaler: dict[str, np.ndarray]) -> np.ndarray:
    out = np.array(X, dtype=np.float32, copy=True)
    med = scaler["median"]
    iqr = scaler["iqr"]
    avail = out[:, :, AVAIL_IDX] > 0.5
    for ci in range(out.shape[2]):
        if ci == AVAIL_IDX:
            continue
        scale = float(iqr[ci]) if float(iqr[ci]) > 0 else 1.0
        if ci in STATE_IDX:
            scaled = np.zeros(out[:, :, ci].shape, dtype=np.float32)
            scaled[avail] = (out[:, :, ci][avail] - float(med[ci])) / scale
            out[:, :, ci] = scaled
        else:
            out[:, :, ci] = (out[:, :, ci] - float(med[ci])) / scale
    return out


def balanced_weights(y: np.ndarray) -> torch.Tensor:
    n = int(y.size)
    w = np.zeros(3, dtype=np.float64)
    for i in range(3):
        c = int(np.sum(y == i))
        w[i] = n / (3.0 * float(max(c, 1)))
    return torch.tensor(w, dtype=torch.float32)


def train_gru(X: np.ndarray, y: np.ndarray, *, seed: int = GRU_SEED) -> dict[str, Any]:
    leak = {"SEQ_META_IN_SAMPLE_TRAIN_N": 0, "HYPERPARAMETER_SEARCH_N": 0, "MODEL_SEARCH_N": 0}
    if X.shape[0] < 40 or len(set(int(v) for v in y.tolist())) < 2:
        return {"kind": "fail", "leak": leak, "n": int(X.shape[0])}
    _seed_all(int(seed))
    torch.set_num_threads(1)
    model = CausalGRU16()
    model.train()
    opt = torch.optim.AdamW(model.parameters(), lr=float(ADAMW_LR), weight_decay=float(ADAMW_WD))
    w = balanced_weights(y)
    loss_fn = nn.CrossEntropyLoss(weight=w)
    xt = torch.from_numpy(np.asarray(X, dtype=np.float32))
    yt = torch.from_numpy(np.asarray(y, dtype=np.int64))
    n = int(xt.shape[0])
    rng = np.random.RandomState(int(seed))
    for _epoch in range(int(GRU_EPOCHS)):
        order = rng.permutation(n)
        for start in range(0, n, int(GRU_BATCH)):
            sl = order[start : start + int(GRU_BATCH)]
            xb = xt[sl]
            yb = yt[sl]
            opt.zero_grad(set_to_none=True)
            logits = model(xb)
            loss = loss_fn(logits, yb)
            loss.backward()
            clip_grad_norm_(model.parameters(), float(GRAD_CLIP))
            opt.step()
    model.eval()
    return {"kind": "gru", "model": model, "leak": leak, "n": n}


def predict_proba(fit: dict[str, Any], X: np.ndarray) -> np.ndarray:
    n = int(X.shape[0])
    if not n:
        return np.zeros((0, 3), dtype=np.float64)
    if str(fit.get("kind") or "") != "gru":
        return np.full((n, 3), 1.0 / 3.0, dtype=np.float64)
    model: CausalGRU16 = fit["model"]
    model.eval()
    xt = torch.from_numpy(np.asarray(X, dtype=np.float32))
    out = np.zeros((n, 3), dtype=np.float64)
    with torch.no_grad():
        for start in range(0, n, int(GRU_BATCH)):
            xb = xt[start : start + int(GRU_BATCH)]
            logits = model(xb)
            prob = torch.softmax(logits, dim=1).cpu().numpy()
            out[start : start + int(GRU_BATCH)] = prob
    return out


def y_code(label: Any) -> Optional[int]:
    return CLASS_INDEX.get(str(label or ""))
