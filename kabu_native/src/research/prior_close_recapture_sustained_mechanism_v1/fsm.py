"""Below-regime accumulators. Feature freeze at recapture INGRESS only."""
from __future__ import annotations

from typing import Any, Optional

from research.post_open_prior_close_recapture_full_strategy_v1.fields import _f
from research.post_open_prior_close_recapture_full_strategy_v1.fsm import PriorCloseFsm
from research.prior_close_recapture_sustained_mechanism_v1.features import freeze_features


class FeatureFsm(PriorCloseFsm):
    def __init__(self, *, symbol: str, date: str, flatten_t: float, am_start: float) -> None:
        super().__init__(symbol=symbol, date=date, flatten_t=flatten_t, am_start=am_start)
        self.acc: dict[str, dict[str, Any]] = {}
        self.frozen: list[dict[str, Any]] = []

    def process(self, *, ingress_t: float, payload: dict[str, Any], rec: dict[str, Any] | None = None) -> None:
        n_sig = len(self.signals)
        super().process(ingress_t=ingress_t, payload=payload)
        eid = str(self.ep["episode_id"]) if self.ep is not None else None
        new_sig = len(self.signals) > n_sig
        if eid and (not new_sig) and (not self.ep.get("signaled")):
            bucket = self.acc.setdefault(
                eid,
                {
                    "below_t": self.ep.get("below_t"),
                    "vol0": _f(payload.get("TradingVolume")),
                    "val0": _f(payload.get("TradingValue")),
                    "min_px": None,
                    "prices": set(),
                    "otu_n": 0,
                    "below_otu_n": 0,
                },
            )
            if self.otus and abs(float(self.otus[-1]["t"]) - float(ingress_t)) < 1e-9:
                px = float(self.otus[-1]["px"])
                bucket["otu_n"] = int(bucket.get("otu_n") or 0) + 1
                bucket["prices"].add(round(px, 6))
                pc = self.prev_close
                if pc is not None and px < float(pc):
                    bucket["below_otu_n"] = int(bucket.get("below_otu_n") or 0) + 1
                    prev = bucket.get("min_px")
                    bucket["min_px"] = px if prev is None else min(float(prev), px)
        if new_sig:
            sig = dict(self.signals[-1])
            sig["below_t"] = self.ep.get("below_t") if self.ep is not None else None
            acc = dict(self.acc.get(sig["episode_id"]) or {})
            prior_n = max(0, len(self.signals) - 1)
            time_since: Optional[float] = None
            if prior_n >= 1:
                time_since = float(sig["t0"]) - float(self.signals[-2]["t0"])
            feat = freeze_features(
                sig=sig,
                acc=acc,
                payload=payload,
                rec=rec,
                ingress_t=float(ingress_t),
                am_start=self.am_start,
                prior_n=prior_n,
                time_since=time_since,
            )
            row = {**sig, **feat, "recapture_px": sig.get("px")}
            self.frozen.append(row)
