"""Official Italian market values — the Agenzia delle Entrate's OMI
*Quotazioni Immobiliari*: €/m² purchase bands for every micro-zone of every
comune, revised twice a year.

WHY THIS EXISTS. Without it, "is this good value" is answered by comparing a
listing against the other listings currently in the same search radius. That is
a small, self-selecting sample: if three overpriced trulli are listed this week,
the fourth looks like a bargain. OMI is the recorded market value for the zone
-- the number a notary and a mortgage valuer work from -- so the comparison
becomes absolute rather than relative to whatever else happens to be for sale.

WHAT IT IS NOT. It is not a feed and not a valuation of any specific house. It
is a band for a type of property in a zone, and a house can sit legitimately
outside it. Treat a listing far below the band as a question, not a bargain.

GETTING THE DATA. Free, but it needs one registration: the current semester's
file comes from telematici.agenziaentrate.gov.it and is not published at a URL
that can be fetched unattended. So this module imports a file you downloaded,
the same way the finance module imports the eMoney export. Attribution to
"Agenzia Entrate - OMI" is required when the figures are shown.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import unicodedata
from statistics import median
from typing import Any

from retirement.core.db import utcnow

# The residential types worth comparing a house against. OMI also prices
# garages, warehouses and shops in the same file; averaging those in would
# quietly drag every benchmark down.
RESIDENTIAL = (
    "abitazioni civili",
    "abitazioni di tipo economico",
    "abitazioni signorili",
    "ville e villini",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS omi_values (
    comune_key  TEXT NOT NULL,        -- normalised comune name, the join key
    comune      TEXT NOT NULL,        -- as printed in the file
    prov        TEXT NOT NULL DEFAULT '',
    zona        TEXT NOT NULL DEFAULT '',
    tipologia   TEXT NOT NULL DEFAULT '',
    stato       TEXT NOT NULL DEFAULT '',
    compr_min   REAL,
    compr_max   REAL,
    semester    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (comune_key, zona, tipologia, stato, semester)
);
CREATE INDEX IF NOT EXISTS idx_omi_comune ON omi_values(comune_key, prov);

CREATE TABLE IF NOT EXISTS omi_imports (
    filename    TEXT PRIMARY KEY,
    semester    TEXT,
    rows        INTEGER,
    comuni      INTEGER,
    imported_at TEXT NOT NULL
);
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ── names ──────────────────────────────────────────────────────────────────
def normalise(name: str) -> str:
    """A join key that survives the differences between a portal's spelling and
    the Agenzia's: accents, case, apostrophes, the trailing province, and the
    'SAN'/'S.' abbreviation that appears in both directions."""
    text = unicodedata.normalize("NFKD", (name or "").strip())
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).upper()
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text)        # "Locorotondo (BA)"
    text = re.sub(r"\bS\.\s*", "SAN ", text)
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _number(raw: str) -> float | None:
    """OMI writes decimals the Italian way, and thousands separators vary by
    distribution."""
    cell = (raw or "").strip().replace(".", "").replace(",", ".")
    if not cell:
        return None
    try:
        value = float(cell)
    except ValueError:
        return None
    return value if value > 0 else None


def semester_from(filename: str) -> str:
    """QI_294577_1_20182_VALORI_utf8.csv -> 2018-2"""
    match = re.search(r"_(\d{4})([12])_", filename or "")
    return f"{match.group(1)}-{match.group(2)}" if match else ""


# ── parsing ────────────────────────────────────────────────────────────────
def parse(data: bytes, filename: str = "") -> list[dict[str, Any]]:
    """Reads a VALORI export. Tolerant of delimiter and of the preamble line
    some distributions put above the header."""
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()

    header_index = next(
        (i for i, line in enumerate(lines[:10]) if "Comune_descrizione" in line), None
    )
    if header_index is None:
        raise ValueError(
            "That does not look like an OMI VALORI export — no Comune_descrizione "
            "column. The file you want is the one named ..._VALORI_... ."
        )
    body = "\n".join(lines[header_index:])
    delimiter = ";" if body.count(";") > body.count(",") else ","

    semester = semester_from(filename)
    rows: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(body), delimiter=delimiter):
        tipologia = (row.get("Descr_Tipologia") or "").strip()
        if tipologia.lower() not in RESIDENTIAL:
            continue
        low, high = _number(row.get("Compr_min", "")), _number(row.get("Compr_max", ""))
        if low is None or high is None:
            continue
        comune = (row.get("Comune_descrizione") or "").strip()
        if not comune:
            continue
        rows.append({
            "comune_key": normalise(comune),
            "comune": comune,
            "prov": (row.get("Prov") or "").strip(),
            "zona": (row.get("Zona") or "").strip(),
            "tipologia": tipologia,
            "stato": (row.get("Stato") or "").strip(),
            "compr_min": min(low, high),
            "compr_max": max(low, high),
            "semester": semester,
        })
    if not rows:
        raise ValueError("Read the file but found no residential rows in it.")
    return rows


def import_file(conn: sqlite3.Connection, data: bytes, filename: str) -> dict[str, Any]:
    migrate(conn)
    rows = parse(data, filename)
    conn.executemany(
        """INSERT OR REPLACE INTO omi_values
             (comune_key, comune, prov, zona, tipologia, stato,
              compr_min, compr_max, semester)
           VALUES (:comune_key,:comune,:prov,:zona,:tipologia,:stato,
                   :compr_min,:compr_max,:semester)""",
        rows,
    )
    comuni = len({r["comune_key"] for r in rows})
    semester = rows[0]["semester"]
    conn.execute(
        """INSERT OR REPLACE INTO omi_imports (filename, semester, rows, comuni, imported_at)
           VALUES (?,?,?,?,?)""",
        (filename, semester, len(rows), comuni, utcnow()),
    )
    conn.commit()
    return {"filename": filename, "semester": semester, "rows": len(rows), "comuni": comuni}


# ── lookup ─────────────────────────────────────────────────────────────────
def lookup(conn: sqlite3.Connection, municipality: str, province: str = "") -> dict[str, Any] | None:
    """The benchmark band for a comune: the median of its zones, plus the
    spread across them. A comune with one zone and one with twelve both give an
    honest middle; the spread is what tells you how much the zone matters."""
    key = normalise(municipality)
    if not key:
        return None
    try:
        rows = conn.execute(
            """SELECT compr_min, compr_max, zona, semester FROM omi_values
               WHERE comune_key = ? ORDER BY semester DESC""",
            (key,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None            # table not created yet — no import has happened
    if not rows:
        return None

    newest = rows[0]["semester"]
    current = [r for r in rows if r["semester"] == newest] or rows
    mids = [(r["compr_min"] + r["compr_max"]) / 2 for r in current]
    return {
        "source": "omi",
        "per_sqm": round(median(mids)),
        "low": round(min(r["compr_min"] for r in current)),
        "high": round(max(r["compr_max"] for r in current)),
        "zones": len({r["zona"] for r in current}),
        "semester": newest,
        "comune": municipality,
    }


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        rows = conn.execute(
            "SELECT * FROM omi_imports ORDER BY semester DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"imported": False, "files": []}
    return {"imported": bool(rows), "files": [dict(r) for r in rows]}
