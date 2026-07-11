"""
Markdown -> PDF (PyMuPDF Story).

Uses @font-face with fitz.Archive so Japanese glyphs embed reliably (Windows:
YuGothR.ttc + consola.ttf). Override font directory with DESIGN_PDF_FONT_DIR.

Usage: python tools/md_to_pdf.py <input.md> [output.pdf]
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import fitz
import markdown

# Windows 標準ゴシック + 等幅（リポジトリにフォントを同梱しない）
_DEFAULT_WIN_FONT_DIR = Path(r"C:\Windows\Fonts")
_BODY_FONT = "YuGothR.ttc"
_MONO_FONT = "consola.ttf"


def _font_dir() -> Path | None:
    env = os.environ.get("DESIGN_PDF_FONT_DIR", "").strip()
    if env:
        p = Path(env)
        if p.is_dir() and (p / _BODY_FONT).is_file() and (p / _MONO_FONT).is_file():
            return p
    if _DEFAULT_WIN_FONT_DIR.is_dir() and (_DEFAULT_WIN_FONT_DIR / _BODY_FONT).is_file():
        if (_DEFAULT_WIN_FONT_DIR / _MONO_FONT).is_file():
            return _DEFAULT_WIN_FONT_DIR
    return None


def _user_css() -> str:
    return """
@font-face { font-family: DocBody; src: url(YuGothR.ttc); }
@font-face { font-family: DocMono; src: url(consola.ttf); }

body {
  font-family: DocBody, "Yu Gothic UI", Meiryo, sans-serif;
  font-size: 10.8pt;
  line-height: 1.52;
  color: #1a1a1a;
}
h1 {
  font-size: 17pt;
  font-weight: 700;
  margin: 0 0 0.4em 0;
  padding-bottom: 0.25em;
  border-bottom: 1.5pt solid #2c5282;
  color: #1a365d;
}
h2 {
  font-size: 13pt;
  font-weight: 700;
  margin: 1.15em 0 0.5em 0;
  padding: 0.3em 0.55em;
  background: #edf2f7;
  color: #1a365d;
  border-left: 4pt solid #3182ce;
  page-break-after: avoid;
}
h3 {
  font-size: 11.3pt;
  font-weight: 700;
  margin: 0.9em 0 0.4em 0;
  color: #2d3748;
  page-break-after: avoid;
}
h4 {
  font-size: 10.6pt;
  font-weight: 600;
  margin: 0.7em 0 0.3em 0;
  color: #374151;
}
p { margin: 0.48em 0; }
ul, ol { margin: 0.4em 0 0.6em 1.15em; padding-left: 0.2em; }
li { margin: 0.22em 0; }

table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.65em 0;
  font-size: 9.85pt;
  line-height: 1.42;
  border: 0.35pt solid #c5cdd6;
  background: #ffffff;
}
th, td {
  padding: 6pt 9pt;
  vertical-align: top;
  border: none;
  border-bottom: 0.3pt solid #e8ecf1;
}
/* 列の縦線（隣接セル間のみ＝二重線にならない） */
td + td,
th + th,
th + td,
td + th {
  border-left: 0.3pt solid #e8ecf1;
}
tr:first-child th {
  border-bottom: 0.45pt solid #b0bcc8;
}
tbody tr:last-child td,
tbody tr:last-child th {
  border-bottom: none;
}
th {
  background: #eef2f6;
  font-weight: 700;
  color: #1e293b;
}
tbody tr:nth-child(even) td {
  background: #fafbfd;
}
tbody tr:nth-child(even) th {
  background: #e8edf3;
}

code {
  font-family: DocMono, Consolas, monospace;
  font-size: 9.05pt;
  background: #f1f5f9;
  padding: 0.12em 0.32em;
  border: 0.25pt solid #e2e8f0;
}
pre {
  font-family: DocMono, Consolas, monospace;
  font-size: 8.95pt;
  line-height: 1.42;
  background: #f8fafc;
  color: #0f172a;
  padding: 8pt 10pt;
  margin: 0.6em 0;
  border: 0.5pt solid #cbd5e1;
  white-space: pre-wrap;
  word-wrap: break-word;
}
pre code {
  font-family: inherit;
  background: transparent;
  border: none;
  padding: 0;
  font-size: inherit;
}
hr {
  border: none;
  border-top: 0.75pt solid #cbd5e0;
  margin: 1.15em 0;
}
a { color: #2563eb; }
strong { font-weight: 700; color: #111827; }
blockquote {
  margin: 0.55em 0;
  padding: 0.15em 0 0.15em 0.75em;
  border-left: 3pt solid #94a3b8;
  color: #475569;
}

/* 改ページ: 巨大な表・h3 ブロックに avoid を付けると Story が収束しないため、分割を許可 */
div.table-keep {
  margin: 0;
}
div.h3-block {
  margin: 0;
}
"""


def _fallback_css() -> str:
    """No local font files: generic names (glyph quality depends on MuPDF)."""
    return _user_css().replace(
        "@font-face { font-family: DocBody; src: url(YuGothR.ttc); }\n"
        "@font-face { font-family: DocMono; src: url(consola.ttf); }\n\n",
        "",
    )


def _wrap_tables(html: str) -> str:
    """1 表ごとに keep 用 div で囲む（ネストした table は想定しない）。"""

    def repl(m: re.Match[str]) -> str:
        return f'<div class="table-keep">{m.group(1)}</div>'

    return re.sub(
        r"(<table\b[^>]*>.*?</table>)",
        repl,
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )


def _wrap_h3_sections(html: str) -> str:
    """各 <h3>〜次の <h1>/<h2>/<h3> 手前までを 1 ブロックにし、まとめて改ページを避ける。"""
    out: list[str] = []
    i = 0
    n = len(html)
    h3_open = re.compile(r"<h3\b", re.IGNORECASE)
    next_hdr = re.compile(r"<h[123]\b", re.IGNORECASE)
    while i < n:
        m = h3_open.search(html, i)
        if not m:
            out.append(html[i:])
            break
        start = m.start()
        out.append(html[i:start])
        m2 = next_hdr.search(html, start + 1)
        end = m2.start() if m2 else n
        out.append('<div class="h3-block">')
        out.append(html[start:end])
        out.append("</div>")
        i = end
    return "".join(out)


def _wrap_html(body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8"/>
</head>
<body>
{body}
</body>
</html>
"""


def md_to_pdf(md_path: Path, pdf_path: Path) -> None:
    raw = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(
        raw,
        extensions=["extra", "tables", "fenced_code", "nl2br", "sane_lists"],
    )
    # table-keep div は PyMuPDF Story が収束しないため付けない（大規模 MD で 500+ ページループ）
    body = _wrap_h3_sections(body)
    html = _wrap_html(body)

    font_dir = _font_dir()
    if font_dir is not None:
        user_css = _user_css()
        archive: fitz.Archive | None = fitz.Archive(str(font_dir))
    else:
        user_css = _fallback_css()
        archive = None

    mediabox = fitz.paper_rect("a4")
    where = mediabox + (50, 58, -50, -58)

    story = fitz.Story(html, user_css=user_css, em=11, archive=archive)
    writer = fitz.DocumentWriter(str(pdf_path))
    pno = 0
    more = 1
    while more:
        dev = writer.begin_page(mediabox)
        more, _filled = story.place(where)
        story.draw(dev)
        writer.end_page()
        pno += 1
        if pno > 500:
            raise RuntimeError("abort: too many pages (possible Story loop)")
    writer.close()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python tools/md_to_pdf.py <input.md> [output.pdf]")
        return 2
    inp = Path(sys.argv[1]).resolve()
    if len(sys.argv) >= 3:
        out = Path(sys.argv[2]).resolve()
    else:
        out = inp.with_suffix(".pdf")
    if not inp.is_file():
        print(f"not found: {inp}")
        return 2
    md_to_pdf(inp, out)
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
