"""Frozen small causal 1D TCN. Architecture locked. No LSTM/GRU/Transformer/Attention."""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F

from research.temporal_model_probe import (
    CONV1_OUT,
    CONV2_OUT,
    DILATION_1,
    DILATION_2,
    DROPOUT,
    HEAD_HIDDEN,
    KERNEL,
    SEQ_C,
    STATIC_HIDDEN,
    STATIC_N,
)


class CausalConv1d(nn.Module):
    def __init__(self, cin: int, cout: int, kernel_size: int, dilation: int) -> None:
        super().__init__()
        self.left_pad = int((kernel_size - 1) * dilation)
        self.conv = nn.Conv1d(cin, cout, kernel_size=kernel_size, dilation=dilation, padding=0, bias=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(F.pad(x, (self.left_pad, 0)))


class SmallCausalTCN(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.conv1 = CausalConv1d(SEQ_C, CONV1_OUT, KERNEL, DILATION_1)
        self.conv2 = CausalConv1d(CONV1_OUT, CONV2_OUT, KERNEL, DILATION_2)
        self.static = nn.Linear(STATIC_N, STATIC_HIDDEN)
        self.head = nn.Linear(CONV2_OUT + STATIC_HIDDEN, HEAD_HIDDEN)
        self.drop = nn.Dropout(DROPOUT)
        self.out = nn.Linear(HEAD_HIDDEN, 1)

    def forward(self, seq: torch.Tensor, static: torch.Tensor) -> torch.Tensor:
        # seq: (B, C=7, T=37) oldest -> t0. Channel 6 = valid_mask.
        mask = seq[:, 6, :]
        h = F.relu(self.conv1(seq))
        h = F.relu(self.conv2(h))
        w = mask.unsqueeze(1)
        den = w.sum(dim=-1).clamp_min(1e-8)
        pooled = (h * w).sum(dim=-1) / den
        s = F.relu(self.static(static))
        z = torch.cat([pooled, s], dim=1)
        z = F.relu(self.head(z))
        z = self.drop(z)
        return self.out(z).squeeze(-1)
