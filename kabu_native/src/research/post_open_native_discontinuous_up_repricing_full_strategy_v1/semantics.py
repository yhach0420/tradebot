"""Trusted local CurrentPriceStatus / CurrentPriceChangeStatus codes. No web. No guessed UP."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from research.e1_x34a_execution_policy.executable_board import (
    STATUS_CURRENT,
    STATUS_DISCONTINUOUS_PRINT,
    STATUS_SOURCE,
)


def _native() -> Path:
    return Path(__file__).resolve().parents[3]


def _read(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _trusted_files() -> list[Path]:
    native = _native()
    repo = native.parent
    out = [
        native / "src" / "research" / "e1_x34a_execution_policy" / "executable_board.py",
        native / "docs" / "board_data_inventory.md",
        repo / "docs" / "kabu_response_mapping.md",
        native / "scripts" / "check_api.py",
        repo / "scripts" / "kabu_api_check.py",
    ]
    for root in (native, repo, native / "docs", repo / "docs", repo / "reference"):
        if not root.is_dir():
            continue
        for p in root.glob("*STATION*.yaml"):
            out.append(p)
        for p in root.glob("*STATION*.yml"):
            out.append(p)
        for p in root.glob("*kabusapi*.yaml"):
            out.append(p)
    # de-dupe while preserving order
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        k = str(p.resolve()) if p.exists() else str(p)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return uniq


def _status_from_executable_board() -> dict[str, Any]:
    path = _native() / "src" / "research" / "e1_x34a_execution_policy" / "executable_board.py"
    text = _read(path)
    current = None
    discont = None
    m1 = re.search(r"STATUS_CURRENT\s*=\s*(\d+)\s*#\s*(.+)", text)
    m2 = re.search(r"STATUS_DISCONTINUOUS_PRINT\s*=\s*(\d+)\s*#\s*(.+)", text)
    if m1:
        current = {"code": int(m1.group(1)), "label": m1.group(2).strip()}
    if m2:
        discont = {"code": int(m2.group(1)), "label": m2.group(2).strip()}
    live_ok = int(STATUS_CURRENT) == 1 and int(STATUS_DISCONTINUOUS_PRINT) == 2
    source_ok = "kabu_STATION_API.yaml" in STATUS_SOURCE and "CurrentPriceStatus" in STATUS_SOURCE
    normal_ok = bool(current and current["code"] == 1 and "現値" in current["label"] and "不連続" not in current["label"])
    disc_ok = bool(discont and discont["code"] == 2 and "不連続歩み" in discont["label"])
    return {
        "FILE": str(path),
        "STATUS_SOURCE": STATUS_SOURCE,
        "SOURCE_CITES_OPENAPI": source_ok,
        "LIVE_CONSTANTS_MATCH_FILE": live_ok
        and current is not None
        and discont is not None
        and int(STATUS_CURRENT) == int(current["code"])
        and int(STATUS_DISCONTINUOUS_PRINT) == int(discont["code"]),
        "NORMAL": current,
        "DISCONTINUOUS": discont,
        "NORMAL_PROVEN": bool(normal_ok and live_ok and source_ok),
        "DISCONTINUOUS_PROVEN": bool(disc_ok and live_ok and source_ok),
    }


def _mapping_labels() -> dict[str, Any]:
    path = _native().parent / "docs" / "kabu_response_mapping.md"
    text = _read(path)
    return {
        "FILE": str(path),
        "EXISTS": path.is_file(),
        "STATUS_FIELD_LABEL": "現値ステータス" if ("CurrentPriceStatus" in text and "ステータス" in text) else None,
        "CHANGE_FIELD_LABEL": "現値前値比較"
        if ("CurrentPriceChangeStatus" in text and ("騰落区分" in text or "現値前値比較" in text))
        else None,
        "HAS_STATUS_CODE_TABLE": "不連続歩み" in text,
        "HAS_CHANGE_CODE_TABLE": bool(
            re.search(r"CurrentPriceChangeStatus[\s\S]{0,800}(?:上昇|UP)", text)
            and re.search(r"CurrentPriceChangeStatus[\s\S]{0,800}\d{4}", text)
        ),
        "NOTE": "mapping.md names the fields (ステータス / 騰落区分) but does not tabulate ChangeStatus codes.",
    }


def _extract_up_from_blob(blob: str, source: str) -> Optional[dict[str, Any]]:
    """Extract UP code only from an explicit CurrentPriceChangeStatus mapping, not a generic 上昇."""
    if "CurrentPriceChangeStatus" not in blob:
        return None
    # Restrict to a window around the field name to avoid unrelated 上昇 / 0056 hits.
    idx = blob.find("CurrentPriceChangeStatus")
    windows = []
    start = max(0, idx - 200)
    windows.append(blob[start : idx + 2500])
    for m in re.finditer(r"CurrentPriceChangeStatus", blob):
        i = m.start()
        windows.append(blob[max(0, i - 200) : i + 2500])
    patterns = (
        r"['\"]?(\d{4})['\"]?\s*[:：]\s*(?:UP|上昇)\b",
        r"(?:UP|上昇)\b\s*[:：]\s*['\"]?(\d{4})['\"]?",
        r"-\s*['\"]?(\d{4})['\"]?\s*[^\n]{0,80}(?:UP|上昇)",
    )
    for w in windows:
        if "CurrentPriceChangeStatus" not in w:
            continue
        if "上昇" not in w and not re.search(r"\bUP\b", w):
            continue
        for pat in patterns:
            got = re.search(pat, w)
            if got:
                code = str(got.group(1)).strip("'\"")
                if len(code) == 4 and code.isdigit():
                    return {"code": code, "label": "UP/上昇", "source": source, "pattern": pat}
    return None


def _find_change_up() -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    scanned: list[str] = []
    yaml_found: list[str] = []
    for p in _trusted_files():
        scanned.append(str(p))
        text = _read(p)
        if not text:
            continue
        if p.suffix.lower() in {".yaml", ".yml"}:
            yaml_found.append(str(p))
        hit = _extract_up_from_blob(text, str(p))
        if hit:
            hits.append(hit)
    return {
        "YAML_FOUND": yaml_found,
        "SCANNED": scanned,
        "HITS": hits,
        "UP_PROVEN": bool(hits),
        "UP": hits[0] if hits else None,
    }


def prove_price_status_semantics() -> dict[str, Any]:
    status = _status_from_executable_board()
    mapping = _mapping_labels()
    change = _find_change_up()
    normal = (status.get("NORMAL") or {}).get("code")
    discont = (status.get("DISCONTINUOUS") or {}).get("code")
    up = (change.get("UP") or {}).get("code") if change.get("UP") else None
    status_fields_named = bool(mapping.get("CHANGE_FIELD_LABEL") or mapping.get("STATUS_FIELD_LABEL"))
    proven = bool(
        status.get("NORMAL_PROVEN")
        and status.get("DISCONTINUOUS_PROVEN")
        and change.get("UP_PROVEN")
        and normal is not None
        and discont is not None
        and up is not None
    )
    return {
        "CURRENT_PRICE_STATUS_OFFICIAL_MEANING": "現値ステータス (BoardSuccess CurrentPriceStatus)",
        "CURRENT_PRICE_CHANGE_STATUS_OFFICIAL_MEANING": "現値前値比較 / 騰落区分 (BoardSuccess CurrentPriceChangeStatus)",
        "STATUS_FIELD_PROVEN": bool(status.get("NORMAL_PROVEN") and status.get("DISCONTINUOUS_PROVEN")),
        "CHANGE_FIELD_NAME_PROVEN": status_fields_named,
        "NORMAL_STATUS_CODE": int(normal) if status.get("NORMAL_PROVEN") else None,
        "NORMAL_STATUS_LABEL": (status.get("NORMAL") or {}).get("label"),
        "DISCONTINUOUS_STATUS_CODE": int(discont) if status.get("DISCONTINUOUS_PROVEN") else None,
        "DISCONTINUOUS_STATUS_LABEL": (status.get("DISCONTINUOUS") or {}).get("label"),
        "UP_CHANGE_STATUS_CODE": up if change.get("UP_PROVEN") else None,
        "UP_CHANGE_STATUS_LABEL": (change.get("UP") or {}).get("label") if change.get("UP_PROVEN") else None,
        "TRUSTED_SOURCE_STATUS": STATUS_SOURCE,
        "TRUSTED_SOURCE_CHANGE": None
        if not change.get("UP_PROVEN")
        else (change.get("UP") or {}).get("source"),
        "STATUS_DETAIL": status,
        "MAPPING": mapping,
        "CHANGE_DETAIL": {
            "YAML_FOUND": change.get("YAML_FOUND"),
            "UP": change.get("UP"),
            "UP_PROVEN": change.get("UP_PROVEN"),
            "SCANNED_N": len(change.get("SCANNED") or []),
            "NOTE": (
                "No local kabu_STATION_API.yaml. mapping.md does not tabulate ChangeStatus codes. "
                "executable_board.py maps CurrentPriceStatus only. UP/上昇 exact code is not proven."
            ),
        },
        "WEB_USED": False,
        "GUESSED_UP_CODE": False,
        "SEMANTICS_PROVEN": proven,
        "BLOCKER": None
        if proven
        else "UP_CHANGE_STATUS_CODE_NOT_IN_TRUSTED_LOCAL_SOURCE",
    }
