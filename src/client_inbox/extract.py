"""Turn attachments, Drive files and uploads into plain text Claude can read."""

from __future__ import annotations

import io
import re
from html.parser import HTMLParser

TEXT_SUFFIXES = (".txt", ".md", ".csv", ".tsv", ".json", ".log", ".xml", ".yaml", ".yml", ".ics")


class _HTMLText(HTMLParser):
    _SKIP = {"script", "style", "head"}
    _BREAK = {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "table", "blockquote"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data):
        if not self._skipping:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n\s*", "\n\n", text).strip()


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages[:60])


def _docx(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    lines = [para.text for para in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(lines)


def _xlsx(data: bytes) -> str:
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines: list[str] = []
    for sheet in book.worksheets[:10]:
        lines.append(f"# Sheet: {sheet.title}")
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            if index >= 500:
                lines.append("(more rows not shown)")
                break
            if any(cell is not None for cell in row):
                lines.append(" | ".join("" if cell is None else str(cell) for cell in row))
    return "\n".join(lines)


def extract_text(filename: str, data: bytes, mime: str = "") -> str:
    """Best-effort text for a file. Returns '' for types we cannot read (images, zips)."""
    name = filename.lower()
    try:
        if name.endswith(".pdf") or mime == "application/pdf":
            return _pdf(data).strip()
        if name.endswith(".docx"):
            return _docx(data).strip()
        if name.endswith((".xlsx", ".xlsm")):
            return _xlsx(data).strip()
        if name.endswith((".html", ".htm")) or mime == "text/html":
            return html_to_text(data.decode("utf-8", errors="replace"))
        if name.endswith(TEXT_SUFFIXES) or mime.startswith("text/"):
            return data.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    return ""


def chunks(text: str, size: int = 1500) -> list[str]:
    """Split on paragraph breaks into pieces of roughly `size` characters."""
    pieces: list[str] = []
    current = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > size:
            pieces.append(para[:size])
            para = para[size:]
        if len(current) + len(para) + 2 > size and current:
            pieces.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        pieces.append(current)
    return pieces
