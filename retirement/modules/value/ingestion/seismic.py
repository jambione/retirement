"""Seismic classification by comune (zona sismica 1-4).

The hazard model is INGV's (MPS04 and its successors); the *classification*
that follows from it — every comune assigned to zone 1 (highest) through 4
(lowest) — is maintained by the Dipartimento della Protezione Civile together
with the regions, and each region publishes its own list. That is why this is a
drop-folder import and not a fetch: there is no single national file at a
single stable URL, there are twenty regional ones.

What the score does with it: zone 1 and 2 are a penalty, not a veto. Half of
Italy is zone 2, including most of what anyone would want to buy, and a 1980s
build in zone 2 with a `sismabonus` retrofit is a different risk from an
unreinforced 1890 stone farmhouse in the same zone. The number narrows the
question; it does not answer it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from retirement.modules.value import store
from retirement.modules.value.ingestion import tabular

SOURCE = "DPC / INGV — classificazione sismica"


def import_file(conn: sqlite3.Connection, data: bytes, filename: str,
                year: str = "") -> dict[str, Any]:
    store.migrate(conn)
    rows = tabular.read_table(data, filename, ("zona sismica", "codice istat"))
    col_code = tabular.require(rows, "codice istat", "codice comune", "istat", "pro com")
    col_zone = tabular.require(rows, "zona sismica", "zona", "classificazione")

    payload, bad = [], 0
    for row in rows:
        istat = tabular.istat_code(row.get(col_code))
        zone = tabular.number(row.get(col_zone))
        # Sub-zones are written 3A/3B in several regions; the leading digit is
        # the classification and the letter is a regional refinement.
        if zone is None:
            raw = str(row.get(col_zone) or "").strip()
            zone = float(raw[0]) if raw[:1].isdigit() else None
        if not istat or zone is None or not 1 <= zone <= 4:
            bad += 1
            continue
        payload.append({"istat": istat, "metric": "seismic_zone", "year": year,
                        "value": float(int(zone)), "source": SOURCE,
                        "note": "1 = highest hazard, 4 = lowest"})

    written = store.put_stats(conn, payload)
    store.record_import(conn, "seismic", Path(filename).name, written, f"year={year}")
    return {"written": written, "skipped": bad, "source": SOURCE}
