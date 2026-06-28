"""
Phase592: Read-only kabusapi endpoints for wallet / positions / orders.

No sendorder — order placement is forbidden in this phase.
"""

from __future__ import annotations

import time
from typing import Any, Optional, Sequence

import statistics

from api.rest_client import KabuNativeApiError, KabuNativeRestClient

PRODUCT_MARGIN = 2


class KabuOrderReadClient(KabuNativeRestClient):
    """kabusapi read endpoints with optional round-trip timing."""

    def timed_get_json(
        self,
        path: str,
        *,
        token: str,
        op: str,
        params: Optional[dict[str, Any]] = None,
    ) -> tuple[dict[str, Any] | list[Any], float]:
        url = f"{self.base_url}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"
        t0 = time.perf_counter()
        response = self._request("GET", url, token=token, op=op)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        try:
            payload = response.json()
        except Exception as e:
            raise KabuNativeApiError(f"{op} response is not JSON: {e}") from e
        return payload, elapsed_ms

    def get_wallet_cash(self, *, token: str) -> tuple[dict[str, Any], float]:
        data, ms = self.timed_get_json("/wallet/cash", token=token, op="wallet/cash")
        return dict(data) if isinstance(data, dict) else {"raw": data}, ms

    def get_wallet_margin(self, *, token: str) -> tuple[dict[str, Any], float]:
        data, ms = self.timed_get_json("/wallet/margin", token=token, op="wallet/margin")
        return dict(data) if isinstance(data, dict) else {"raw": data}, ms

    def get_positions(
        self,
        *,
        token: str,
        product: int = PRODUCT_MARGIN,
    ) -> tuple[list[dict[str, Any]], float]:
        data, ms = self.timed_get_json(
            "/positions",
            token=token,
            op="positions",
            params={"product": product},
        )
        if isinstance(data, list):
            return [dict(x) for x in data if isinstance(x, dict)], ms
        return [], ms

    def get_orders(
        self,
        *,
        token: str,
        product: int = PRODUCT_MARGIN,
    ) -> tuple[list[dict[str, Any]], float]:
        data, ms = self.timed_get_json(
            "/orders",
            token=token,
            op="orders",
            params={"product": product},
        )
        if isinstance(data, list):
            return [dict(x) for x in data if isinstance(x, dict)], ms
        return [], ms

    @staticmethod
    def extract_executions(orders: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for order in orders:
            for detail in order.get("Details") or []:
                if not isinstance(detail, dict):
                    continue
                if detail.get("ExecutionID") or detail.get("RecType") in (8, "8"):
                    rows.append(dict(detail))
        return rows

    def get_executions_via_orders(
        self,
        *,
        token: str,
        product: int = PRODUCT_MARGIN,
    ) -> tuple[list[dict[str, Any]], float]:
        orders, ms = self.get_orders(token=token, product=product)
        return self.extract_executions(orders), ms

    def measure_rtt(
        self,
        *,
        token: str,
        samples: int = 5,
        reissue_token: bool = False,
        api_password: str = "",
        delay_sec: float = 0.35,
    ) -> dict[str, dict[str, Any]]:
        """Measure RTT stats per endpoint. Never sends orders."""

        def _stats(vals: list[float]) -> dict[str, Any]:
            if not vals:
                return {"count": 0, "avg_ms": None, "median_ms": None, "p95_ms": None, "max_ms": None}
            s = sorted(vals)
            n = len(s)
            p95_i = min(n - 1, int(n * 0.95))
            return {
                "count": n,
                "avg_ms": round(statistics.mean(s), 3),
                "median_ms": round(statistics.median(s), 3),
                "p95_ms": round(s[p95_i], 3),
                "max_ms": round(max(s), 3),
            }

        out: dict[str, dict[str, Any]] = {}
        active_token = token
        if reissue_token and api_password:
            token_vals: list[float] = []
            for _ in range(max(1, samples)):
                t0 = time.perf_counter()
                active_token = self.issue_token(api_password)
                token_vals.append((time.perf_counter() - t0) * 1000.0)
            out["token"] = {**_stats(token_vals), "ok": True}
        elif api_password:
            t0 = time.perf_counter()
            try:
                active_token = self.issue_token(api_password)
                out["token"] = {**_stats([(time.perf_counter() - t0) * 1000.0]), "ok": True}
            except KabuNativeApiError as e:
                out["token"] = {"count": 0, "error": str(e), "ok": False}
        else:
            out["token"] = {"count": 0, "ok": True, "avg_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}

        tok = active_token
        for name, fn in (
            ("wallet", lambda: self.get_wallet_cash(token=tok)),
            ("wallet_margin", lambda: self.get_wallet_margin(token=tok)),
            ("positions", lambda: self.get_positions(token=tok)),
            ("orders", lambda: self.get_orders(token=tok)),
            ("executions", lambda: self.get_executions_via_orders(token=tok)),
        ):
            vals: list[float] = []
            err = ""
            for i in range(max(1, samples)):
                try:
                    _, ms = fn()
                    vals.append(ms)
                    if delay_sec > 0 and i + 1 < samples:
                        time.sleep(delay_sec)
                except KabuNativeApiError as e:
                    err = str(e)
                    break
            row = _stats(vals)
            if err:
                row["error"] = err
                row["ok"] = False
            else:
                row["ok"] = True
            out[name] = row
        return out

    def probe_all(self, *, token: str) -> dict[str, Any]:
        """Run all read probes; never sends orders."""
        out: dict[str, Any] = {"ok": True, "probes": {}}
        for name, fn in (
            ("wallet_cash", lambda: self.get_wallet_cash(token=token)),
            ("wallet_margin", lambda: self.get_wallet_margin(token=token)),
            ("positions", lambda: self.get_positions(token=token)),
            ("orders", lambda: self.get_orders(token=token)),
        ):
            try:
                payload, ms = fn()
                count = len(payload) if isinstance(payload, list) else 1
                out["probes"][name] = {
                    "ok": True,
                    "latency_ms": round(ms, 2),
                    "count": count,
                }
            except KabuNativeApiError as e:
                out["ok"] = False
                out["probes"][name] = {"ok": False, "error": str(e)}
        return out
