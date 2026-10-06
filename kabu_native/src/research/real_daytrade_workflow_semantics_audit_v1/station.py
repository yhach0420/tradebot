"""Recover kabu STATION chart settings from this machine. Evidence, not inference."""
from __future__ import annotations

import collections
import re
from pathlib import Path
from typing import Any


def _find_current() -> Path | None:
    root = Path.home() / "AppData" / "Roaming" / "KabuS"
    if not root.is_dir():
        return None
    hits = list(root.glob("*/CurrentAppSettings/MyPages/MyPages.xml"))
    if not hits:
        return None
    return hits[0].parent.parent


def _cdata_titles(text: str) -> list[str]:
    return re.findall(r"<Title><!\[CDATA\[(.*?)\]\]></Title>", text)


def _terms(text: str) -> collections.Counter:
    raw = re.findall(r"<Term><!\[CDATA\[(.*?)\]\]>", text)
    cleaned = []
    for x in raw:
        s = str(x).replace("]", "").strip()
        cleaned.append(s)
    return collections.Counter(cleaned)


def inspect_station() -> dict[str, Any]:
    cur = _find_current()
    out: dict[str, Any] = {
        "recovered": False,
        "path": str(cur) if cur else None,
        "source": "kabu STATION CurrentAppSettings on this machine",
    }
    if cur is None:
        out["reason"] = "KabuS CurrentAppSettings not found"
        return out
    pages = cur / "MyPages" / "MyPages.xml"
    views = cur / "Settings" / "ViewsSettings.xml"
    news = cur.parent / "NewsData" / "NewsData.xml"
    text = pages.read_text(encoding="utf-8", errors="replace") if pages.is_file() else ""
    vtext = views.read_text(encoding="utf-8", errors="replace") if views.is_file() else ""
    ntext = news.read_text(encoding="utf-8", errors="replace") if news.is_file() else ""
    terms = _terms(text)
    titles = _cdata_titles(text)
    active = re.findall(r'<MyPage id="([^"]+)" active="([^"]+)"', text)
    out.update(
        {
            "recovered": True,
            "pages_file": str(pages),
            "titles": titles,
            "active_pages": [{"id": a, "active": b == "true"} for a, b in active],
            "chart_term_counts": dict(terms),
            "one_minute_chart_n": int(terms.get("1分足", 0) or text.count("1分足")),
            "five_minute_chart_n": int(text.count("5分足")),
            "fifteen_minute_chart_n": int(text.count("15分足")),
            "daily_chart_n": int(text.count("日足")),
            "weekly_chart_n": int(text.count("週足")),
            "average_overlay_true_n": int(text.count("<AverageN>true</AverageN>")),
            "average_overlay_false_n": int(text.count("<AverageN>false</AverageN>")),
            "volume_overlay_true_n": int(text.count("<Volume>true</Volume>")),
            "volume_overlay_false_n": int(text.count("<Volume>false</Volume>")),
            "vwap_as_watch_column": "VWAP" in vtext or "VWAP" in text,
            "news_items_empty": "<NewsItems />" in ntext or "<NewsItems/>" in ntext,
            "layout_is_vendor_sample": any("サンプル" in t for t in titles),
            "ma_periods_in_saved_layout": "NOT_RECOVERABLE",
            "interpretation": (
                "Saved kabu STATION layout is vendor sample pages. Mini-charts are 1-minute. "
                "Moving-average overlay is off. Volume overlay is off. VWAP appears as a quote column, "
                "not as a recovered chart overlay period set. Numeric SMA 5/25/75 periods are not stored "
                "in this layout XML."
            ),
        }
    )
    return out
