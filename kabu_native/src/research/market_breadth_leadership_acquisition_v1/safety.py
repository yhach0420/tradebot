"""Static mutation scan. Collector must not call register / unregister / sendorder."""
from __future__ import annotations

from pathlib import Path
from typing import Any

PKG = Path(__file__).resolve().parent
NEEDLES = (
    'path="/register"',
    "path='/register'",
    'path="/unregister"',
    "path='/unregister'",
    'path="/sendorder"',
    "path='/sendorder'",
    '"/sendorder"',
    "'/sendorder'",
    "sendorder(",
    "put_register",
    "PUT /register",
    "POST /sendorder",
)


def scan_package_source() -> dict[str, Any]:
    hits: list[dict[str, str]] = []
    files: list[str] = []
    for p in sorted(PKG.glob("*.py")):
        if p.name == "safety.py":
            continue
        files.append(p.name)
        txt = p.read_text(encoding="utf-8")
        for needle in NEEDLES:
            if needle in txt:
                hits.append({"file": p.name, "needle": needle})
    register_n = sum(1 for h in hits if "register" in h["needle"] and "unregister" not in h["needle"])
    unregister_n = sum(1 for h in hits if "unregister" in h["needle"])
    sendorder_n = sum(1 for h in hits if "sendorder" in h["needle"].lower() or "sendorder" in h["needle"])
    return {
        "files": files,
        "hits": hits,
        "register_mutation_n": int(register_n),
        "unregister_n": int(unregister_n),
        "sendorder_n": int(sendorder_n),
        "ok": not hits,
    }
