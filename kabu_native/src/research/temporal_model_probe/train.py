"""Train-only scaler + 30-epoch AdamW. Held-out day never used for fit or epoch choice."""
from __future__ import annotations

from typing import Any

import numpy as np
import torch
from torch.nn import functional as F

from research.entry_sequence_representation import SEQ_FEATURES
from research.temporal_model_probe import (
    BATCH_SIZE,
    EPOCHS,
    LR,
    SEQ_C,
    SEQ_T,
    STATIC_FEATURES,
    STATIC_N,
    WEIGHT_DECAY,
)
from research.temporal_model_probe.model import SmallCausalTCN


def seed_all(seed: int) -> None:
    np.random.seed(int(seed))
    torch.manual_seed(int(seed))


def rows_to_arrays(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(rows)
    seq = np.zeros((n, SEQ_C, SEQ_T), dtype=np.float32)
    static = np.zeros((n, STATIC_N), dtype=np.float32)
    y = np.zeros((n,), dtype=np.float32)
    for i, r in enumerate(rows):
        flat = [float(r.get(name) or 0.0) for name in SEQ_FEATURES]
        seq[i] = np.asarray(flat, dtype=np.float32).reshape(SEQ_T, SEQ_C).T
        for j, name in enumerate(STATIC_FEATURES):
            static[i, j] = float(r.get(name) or 0.0)
        lab = r.get("joint_label")
        y[i] = float(lab) if lab in (0, 1) else 0.0
    return seq, static, y


def fit_scaler(seq: np.ndarray, static: np.ndarray) -> dict[str, np.ndarray]:
    mask = seq[:, 6, :] > 0.5
    seq_mean = np.zeros((SEQ_C,), dtype=np.float64)
    seq_std = np.ones((SEQ_C,), dtype=np.float64)
    for c in range(6):
        vals = seq[:, c, :][mask]
        if vals.size:
            seq_mean[c] = float(vals.mean())
            sd = float(vals.std())
            seq_std[c] = sd if sd > 1e-8 else 1.0
    st_mean = static.mean(axis=0).astype(np.float64)
    st_std = static.std(axis=0).astype(np.float64)
    st_std = np.where(st_std > 1e-8, st_std, 1.0)
    return {
        "seq_mean": seq_mean.astype(np.float32),
        "seq_std": seq_std.astype(np.float32),
        "static_mean": st_mean.astype(np.float32),
        "static_std": st_std.astype(np.float32),
    }


def apply_scaler(seq: np.ndarray, static: np.ndarray, scaler: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    out_s = seq.copy()
    mask = seq[:, 6, :] > 0.5
    for c in range(6):
        z = (seq[:, c, :] - scaler["seq_mean"][c]) / scaler["seq_std"][c]
        out_s[:, c, :] = np.where(mask, z, 0.0).astype(np.float32)
    out_s[:, 6, :] = seq[:, 6, :]
    st = ((static - scaler["static_mean"]) / scaler["static_std"]).astype(np.float32)
    return out_s, st


def train_tcn(
    *,
    seq: np.ndarray,
    static: np.ndarray,
    y: np.ndarray,
    seed: int,
) -> SmallCausalTCN:
    seed_all(int(seed))
    device = torch.device("cpu")
    model = SmallCausalTCN().to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    x_seq = torch.from_numpy(np.ascontiguousarray(seq))
    x_st = torch.from_numpy(np.ascontiguousarray(static))
    yt = torch.from_numpy(np.ascontiguousarray(y))
    n = int(y.shape[0])
    g = torch.Generator().manual_seed(int(seed))
    model.train()
    for _epoch in range(int(EPOCHS)):
        perm = torch.randperm(n, generator=g)
        for start in range(0, n, int(BATCH_SIZE)):
            idx = perm[start : start + int(BATCH_SIZE)]
            opt.zero_grad(set_to_none=True)
            logit = model(x_seq[idx], x_st[idx])
            loss = F.binary_cross_entropy_with_logits(logit, yt[idx])
            loss.backward()
            opt.step()
    model.eval()
    return model


@torch.no_grad()
def predict_proba(model: SmallCausalTCN, seq: np.ndarray, static: np.ndarray) -> np.ndarray:
    model.eval()
    x_seq = torch.from_numpy(np.ascontiguousarray(seq))
    x_st = torch.from_numpy(np.ascontiguousarray(static))
    logit = model(x_seq, x_st)
    return torch.sigmoid(logit).cpu().numpy().astype(np.float64)
