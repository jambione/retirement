"""Reading the files Italian public bodies actually publish.

Every source in this module's family ships a table, and no two ship it the same
way: ISTAT writes UTF-8 CSV with semicolons, MEF writes Windows-1252 with a
title row above the header, the seismic classification arrives as .xlsx per
region, and column names carry accents, line breaks and the year in the header.

So: sniff the encoding and the delimiter, find the header row rather than
assuming it is the first one, and match columns by a normalised fragment
("codice istat", "reddito imponibile") instead of an exact string. When nothing
matches, say which columns WERE found — the difference between a two-minute fix
and an afternoon is knowing what the file actually contains.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from typing import Any, Iterable, Sequence

ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def _decode(data: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def key(name: str) -> str:
    """Header -> comparable token: accents stripped, punctuation gone, lowered."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def number(raw: Any) -> float | None:
    """Italian decimals, thousands separators, and the '..' and '-' that these
    files use for "not disclosed". A suppressed cell is None, never zero."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    cell = str(raw).strip().replace("€", "").replace("%", "").replace("\xa0", "")
    if cell in ("", "-", "--", "..", "n.d.", "N.D.", "nd", "ND"):
        return None
    cell = cell.replace(" ", "")
    if "," in cell and "." in cell:
        cell = cell.replace(".", "").replace(",", ".")
    elif "," in cell:
        cell = cell.replace(",", ".")
    try:
        return float(cell)
    except ValueError:
        return None


def read_table(data: bytes, filename: str = "",
               header_hints: Sequence[str] = ()) -> list[dict[str, Any]]:
    """CSV or XLSX in, list of dicts out."""
    if filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        return _read_xlsx(data, header_hints)
    return _read_csv(data, header_hints)


def _read_csv(data: bytes, hints: Sequence[str]) -> list[dict[str, Any]]:
    text = _decode(data)
    lines = text.splitlines()
    start = 0
    if hints:
        for i, line in enumerate(lines[:25]):
            probe = key(line)
            if any(key(h) in probe for h in hints):
                start = i
                break
    body = "\n".join(lines[start:])
    sample = body[:4096]
    delimiter = max((";", ",", "\t"), key=sample.count)
    return [dict(row) for row in csv.DictReader(io.StringIO(body), delimiter=delimiter)]


def _read_xlsx(data: bytes, hints: Sequence[str]) -> list[dict[str, Any]]:
    from openpyxl import load_workbook           # already a dependency, for eMoney

    book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheet = book.active
    rows = list(sheet.iter_rows(values_only=True))
    header_at = 0
    if hints:
        for i, row in enumerate(rows[:25]):
            probe = key(" ".join(str(c) for c in row if c is not None))
            if any(key(h) in probe for h in hints):
                header_at = i
                break
    header = [str(c).strip() if c is not None else "" for c in rows[header_at]]
    out = []
    for row in rows[header_at + 1:]:
        if all(c is None for c in row):
            continue
        out.append({header[i]: row[i] for i in range(min(len(header), len(row)))})
    return out


def column(row: dict[str, Any], *fragments: str) -> str | None:
    """The first column whose normalised header contains one of these
    fragments. Returns the column NAME so a caller can reuse it across rows."""
    keyed = {key(name): name for name in row}
    for fragment in fragments:
        target = key(fragment)
        for normalised, original in keyed.items():
            if target and target in normalised:
                return original
    return None


def require(rows: Iterable[dict[str, Any]], *fragments: str) -> str:
    rows = list(rows)
    if not rows:
        raise ValueError("That file has no data rows.")
    found = column(rows[0], *fragments)
    if found:
        return found
    raise ValueError(
        f"No column matching {' / '.join(fragments)!r}. The file has: "
        + ", ".join(list(rows[0].keys())[:15])
    )


def istat_code(raw: Any) -> str:
    """ISTAT comune codes are six digits and lose their leading zero the moment
    a spreadsheet touches them. Pad, and reject anything that is not a code."""
    if raw is None:
        return ""
    cell = re.sub(r"[^0-9]", "", str(raw).strip())
    if not cell or len(cell) > 6:
        return ""
    return cell.zfill(6)
