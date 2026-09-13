"""ISTAT — the comune registry every other source is keyed on, and a generic
importer for the indicator files.

TWO JOBS.

**The registry.** ISTAT's *Elenco dei comuni italiani* is the spine: six-digit
comune code, name, province, region, and the Belfiore cadastral code that the
Catasto and OMI's `Comune_cat` use. The companion *elenco dei comuni soppressi*
records mergers, and that is not a detail — several hundred comuni have been
merged since 2014, so a 2019 income file and a 2026 map disagree about which
codes exist unless something follows the trail. `superseded_by` is that trail;
`store.resolve_comune` walks it.

**The indicators.** Population, age structure, households, housing stock,
building permits, tourist beds, arrivals and presences are each published as
their own table with their own layout, and ISTAT reorganises them. Rather than
hard-code a dozen parsers that rot, this takes any table with a comune-code
column and a value column and stores it as (comune, metric, year, source):

    ./retire value istat beds.csv --metric tourism_beds --year 2024 \\
        --value-column "posti letto"

The cost is that you name the metric; the benefit is that a new ISTAT release
never needs a code change, and nothing is ever guessed from a column the file
does not actually have.

Files come from the drop folder, not from a fetch: ISTAT's download URLs are
versioned per release and change, and a hard-coded one that silently 404s is
worse than a documented click-path. DATA_SOURCES.md has the click-path.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from retirement.modules.property.omi import normalise
from retirement.modules.value import store
from retirement.modules.value.ingestion import tabular

SOURCE = "ISTAT"

REGISTRY_HINTS = ("codice comune formato alfanumerico", "denominazione in italiano",
                  "codice istat", "denominazione comune")


def import_registry(conn: sqlite3.Connection, data: bytes, filename: str) -> dict[str, Any]:
    """The Elenco comuni file. Tolerant about which of its many code columns is
    present, because the national file and the regional extracts differ."""
    store.migrate(conn)
    rows = tabular.read_table(data, filename, REGISTRY_HINTS)
    if not rows:
        raise ValueError("No rows in that file.")

    col_code = tabular.require(rows, "codice comune formato alfanumerico",
                               "codice istat del comune", "codice istat", "pro com")
    col_name = tabular.require(rows, "denominazione in italiano", "denominazione comune",
                               "denominazione")
    col_prov = tabular.column(rows[0], "sigla automobilistica", "sigla provincia")
    col_prov_name = tabular.column(rows[0], "denominazione dell unita territoriale",
                                   "denominazione provincia")
    col_region = tabular.column(rows[0], "denominazione regione")
    col_cad = tabular.column(rows[0], "codice catastale")

    payload = []
    for row in rows:
        istat = tabular.istat_code(row.get(col_code))
        name = str(row.get(col_name) or "").strip()
        if not istat or not name:
            continue
        payload.append((
            istat, name, normalise(name),
            str(row.get(col_prov) or "").strip() if col_prov else "",
            str(row.get(col_prov_name) or "").strip() if col_prov_name else "",
            str(row.get(col_region) or "").strip() if col_region else "",
            str(row.get(col_cad) or "").strip() if col_cad else "",
        ))
    if not payload:
        raise ValueError("Found the columns but no rows carried a usable comune code.")

    conn.executemany(
        """INSERT INTO comuni (istat, name, name_key, prov, prov_name, region, cadastral)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(istat) DO UPDATE SET
             name=excluded.name, name_key=excluded.name_key, prov=excluded.prov,
             prov_name=excluded.prov_name, region=excluded.region,
             cadastral=excluded.cadastral""",
        payload,
    )
    conn.commit()
    store.record_import(conn, "istat_registry", Path(filename).name, len(payload))
    return {"comuni": len(payload)}


def import_mergers(conn: sqlite3.Connection, data: bytes, filename: str) -> dict[str, Any]:
    """The soppressi file: which retired code became which live one."""
    store.migrate(conn)
    rows = tabular.read_table(data, filename, ("codice istat", "soppress"))
    col_old = tabular.require(rows, "codice istat del comune soppresso",
                              "codice comune soppresso", "codice istat")
    col_new = tabular.require(rows, "codice istat del nuovo comune", "codice nuovo comune",
                              "codice comune associato", "nuovo codice")
    pairs = []
    for row in rows:
        old, new = tabular.istat_code(row.get(col_old)), tabular.istat_code(row.get(col_new))
        if old and new and old != new:
            pairs.append((new, old))
    conn.executemany(
        """INSERT INTO comuni (istat, name, name_key, superseded_by)
           VALUES (?, '', '', ?)
           ON CONFLICT(istat) DO UPDATE SET superseded_by = excluded.superseded_by""",
        [(old, new) for new, old in pairs],
    )
    conn.commit()
    store.record_import(conn, "istat_mergers", Path(filename).name, len(pairs))
    return {"mergers": len(pairs)}


def import_metric(conn: sqlite3.Connection, data: bytes, filename: str, metric: str,
                  year: str = "", value_column: str = "", code_column: str = "",
                  source: str = SOURCE, note: str = "") -> dict[str, Any]:
    """Any table with a comune code and a number, stored under a metric name
    you choose. Rows whose value is blank or suppressed are skipped rather than
    stored as zero."""
    store.migrate(conn)
    rows = tabular.read_table(data, filename, (code_column or "codice", value_column or metric))
    col_code = (code_column if code_column and code_column in rows[0]
                else tabular.require(rows, code_column or "codice istat", "codice comune",
                                     "pro com", "codice"))
    col_value = (value_column if value_column and value_column in rows[0]
                 else tabular.require(rows, value_column or metric))

    payload, skipped = [], 0
    for row in rows:
        istat = tabular.istat_code(row.get(col_code))
        value = tabular.number(row.get(col_value))
        if not istat or value is None:
            skipped += 1
            continue
        payload.append({"istat": istat, "metric": metric, "year": year,
                        "value": value, "source": source,
                        "note": note or f"{col_value} — {Path(filename).name}"})
    written = store.put_stats(conn, payload)
    store.record_import(conn, f"metric:{metric}", Path(filename).name, written,
                        f"year={year} column={col_value}")
    return {"metric": metric, "year": year, "written": written, "skipped": skipped,
            "column": col_value, "source": source}
