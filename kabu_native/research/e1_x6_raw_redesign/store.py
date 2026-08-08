"""Run store + checkpoints for the raw-redesign research (fully outside repo/temp).

Everything (bundles, cache, checkpoints, publish) lives under
C:\\Users\\<user>\\e1x6_research_store\\raw_feature_redesign\\<run_id>\\ only.
Never writes into repo results/, OS temp, or Paper output dirs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional


def research_store_root() -> Path:
    return Path.home() / "e1x6_research_store"


def run_root(run_id: str) -> Path:
    p = research_store_root() / "raw_feature_redesign" / run_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def sha256_file(fp: Path) -> str:
    h = hashlib.sha256()
    with fp.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def write_json(fp: Path, obj: Any) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(fp.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(fp)


def read_json(fp: Path) -> Any:
    return json.loads(fp.read_text(encoding="utf-8"))


def save_checkpoint(run_id: str, name: str, payload: dict[str, Any], *, binding: dict[str, str]) -> Path:
    """Checkpoint bound to source-manifest/P0 SHAs; resume verifies the binding."""
    fp = run_root(run_id) / "checkpoints" / f"{name}.json"
    body = {"binding": dict(binding), "payload": payload}
    body["checkpoint_sha256"] = sha256_obj(body)
    write_json(fp, body)
    return fp


def load_checkpoint(run_id: str, name: str, *, binding: dict[str, str]) -> Optional[dict[str, Any]]:
    """Return payload iff checkpoint exists, hash verifies and binding matches.

    A binding mismatch (e.g. source manifest SHA changed) raises: resume with a
    different P1/source is forbidden, silently recomputing would hide it.
    """
    fp = run_root(run_id) / "checkpoints" / f"{name}.json"
    if not fp.is_file():
        return None
    body = read_json(fp)
    expect = body.get("checkpoint_sha256")
    actual = sha256_obj({k: v for k, v in body.items() if k != "checkpoint_sha256"})
    if expect != actual:
        raise SystemExit(f"FAIL: checkpoint {name} corrupted (sha mismatch)")
    if body.get("binding") != dict(binding):
        raise SystemExit(
            f"FAIL: checkpoint {name} binding mismatch (P1/source manifest changed); "
            "resume forbidden"
        )
    return body["payload"]
