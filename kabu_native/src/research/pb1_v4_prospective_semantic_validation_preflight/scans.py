"""Static scans: no PnL/MFE/MAE compute path, no real-order path. V4 is read-only."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from research.pb1_v4_prospective_semantic_validation_preflight.isolation import NATIVE, V4_SRC

ALLOW_NAMES = {
    "pnl_used",
    "mfe_mae_used",
    "future_outcome_used",
    "future_economic_outcome_used",
    "pnl_optimization",
    "pnl_tokens",
    "order_tokens",
}
DENY_NAMES = {"pnl", "mfe", "mae", "profit_factor", "win_rate", "best_exit", "future_return", "pf"}
ORDER_CALLS = {"place_order", "send_order", "submit_order", "cancel_order", "live_order"}
ORDER_ATTR = {"kabusapi", "sendorder", "cancelorder"}


def _names(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            out.add(node.func.id)
    return out


def _scan_file(path: Path) -> dict[str, list[str]]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    names = {n.lower() for n in _names(tree)}
    pnl = sorted((names & DENY_NAMES) - ALLOW_NAMES)
    order = sorted(n for n in names if n in ORDER_CALLS or n in ORDER_ATTR)
    return {"pnl": pnl, "order": order}


def scan_v4_and_harness(harness_src: Path) -> dict[str, Any]:
    files = sorted(p for p in V4_SRC.glob("*.py") if p.is_file())
    files.extend(sorted(p for p in harness_src.glob("*.py") if p.is_file() and p.name != "scans.py"))
    pnl_hits: list[dict[str, Any]] = []
    order_hits: list[dict[str, Any]] = []
    for p in files:
        hits = _scan_file(p)
        rel = str(p.relative_to(NATIVE)).replace("\\", "/")
        if hits["pnl"]:
            pnl_hits.append({"path": rel, "tokens": hits["pnl"]})
        if hits["order"]:
            order_hits.append({"path": rel, "tokens": hits["order"]})
    return {
        "ok": not pnl_hits and not order_hits,
        "pnl_ok": not pnl_hits,
        "order_ok": not order_hits,
        "pnl_hits": pnl_hits,
        "order_hits": order_hits,
        "files_n": len(files),
    }
