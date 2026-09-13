"""MEF — Dipartimento delle Finanze: declared income by comune.

Why income and not something more direct: a comune's average declared income is
the best free proxy available for whether a place can support its own property
prices, and `reddito da fabbricati` — income declared FROM buildings — is the
only free, official signal of how much of the local housing stock actually
earns rent rather than sitting empty. Both are published per comune, yearly,
from the IRPEF declarations.

Two honest limits, both printed in the UI: declared income understates real
income in Italy unevenly by region, and a comune below the disclosure threshold
has its cells suppressed rather than zeroed — `tabular.number` returns None for
those, and a None is skipped, never stored as a zero that would read as
destitution.

Cite as "MEF — Dipartimento delle Finanze". Download from the Finanze open-data
pages; DATA_SOURCES.md has the click-path.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from retirement.modules.value import store
from retirement.modules.value.ingestion import tabular

SOURCE = "MEF — Dipartimento delle Finanze"

# metric -> the column fragments MEF has used for it across releases
WANTED: dict[str, tuple[str, ...]] = {
    "taxpayers": ("numero contribuenti",),
    "income_total": ("reddito imponibile ammontare", "reddito complessivo ammontare"),
    "income_returns": ("reddito imponibile frequenza", "reddito complessivo frequenza"),
    "income_from_buildings": ("reddito da fabbricati ammontare",),
    "income_from_buildings_n": ("reddito da fabbricati frequenza",),
}


def import_file(conn: sqlite3.Connection, data: bytes, filename: str,
                year: str = "") -> dict[str, Any]:
    store.migrate(conn)
    rows = tabular.read_table(data, filename, ("codice istat", "numero contribuenti"))
    if not rows:
        raise ValueError("No rows in that file.")
    col_code = tabular.require(rows, "codice istat comune", "codice istat", "codice comune")

    columns = {metric: tabular.column(rows[0], *fragments)
               for metric, fragments in WANTED.items()}
    present = {m: c for m, c in columns.items() if c}
    if not present:
        raise ValueError(
            "None of the IRPEF columns are in this file. Found: "
            + ", ".join(list(rows[0].keys())[:12])
        )

    payload: list[dict[str, Any]] = []
    for row in rows:
        istat = tabular.istat_code(row.get(col_code))
        if not istat:
            continue
        values = {m: tabular.number(row.get(c)) for m, c in present.items()}
        for metric, value in values.items():
            if value is not None:
                payload.append({"istat": istat, "metric": metric, "year": year,
                                "value": value, "source": SOURCE, "note": present[metric]})
        # Derived here rather than at read time so the UI never divides by a
        # suppressed cell it cannot see.
        total, people = values.get("income_total"), values.get("taxpayers")
        if total and people:
            payload.append({"istat": istat, "metric": "income_avg", "year": year,
                            "value": round(total / people, 2), "source": SOURCE,
                            "note": "income_total / taxpayers"})

    written = store.put_stats(conn, payload)
    store.record_import(conn, "mef_irpef", Path(filename).name, written, f"year={year}")
    return {"written": written, "metrics": sorted(present) + ["income_avg"],
            "year": year, "source": SOURCE}
