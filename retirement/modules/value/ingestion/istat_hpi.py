"""ISTAT IPAB — the quarterly house price index, and what it is for here.

OMI publishes a band twice a year. Between releases the app is frozen at
whatever semester was last imported, and comparing a 2026 asking price to a
2018 band is not a small error: existing-dwelling prices since 2018-Q1 are up
**8%** in Sud e Isole and **23%** in the Nord-est. Reading one as the other
either invents a bargain or hides one, depending on which end of the country
you are standing in.

So this fetches the index and stores it, and `bands.band_for` uses it to carry
a stale band forward — never silently: the factor, the two quarters and the
area it came from are printed with the answer, and the original band is kept
beside the adjusted one.

    GET https://esploradati.istat.it/SDMXWS/rest/data/IT1,143_497_DF_DCSP_IPAB_1,1.0/ALL/
        ?startPeriod=2016-Q1&format=csv

No key, no login (verified 2026-09-13). The dimensions that matter:

  MEASURE=4               the index level — 6 and 7 are period changes
  PURCHASES_DWELLINGS     ALL | EXST_DW (existing) | NEW_DW
  REF_AREA                IT, ITC (Nord-ovest), ITD (Nord-est),
                          ITE (Centro), ITFG (Sud e Isole), plus a few metros

EXST_DW is the series the score uses. You are buying an existing house, and
new-build prices in Italy move on a different curve — over this period, quite
a lot faster.

Licence: ISTAT, attribution "ISTAT — IPAB".
"""
from __future__ import annotations

import csv
import io
import sqlite3
from typing import Any

import httpx

from retirement.modules.value import store

DATAFLOW = "IT1,143_497_DF_DCSP_IPAB_1,1.0"
BASE = f"https://esploradati.istat.it/SDMXWS/rest/data/{DATAFLOW}/ALL/"
SOURCE = "ISTAT — IPAB"

INDEX_MEASURE = "4"
SERIES = {"EXST_DW": "hpi_existing", "NEW_DW": "hpi_new", "ALL": "hpi_all"}

# Region -> the NUTS area this dataflow publishes. Sud and Isole are one series
# here (ITFG), which is why Puglia and Sicilia share a number.
AREA_BY_REGION = {
    "piemonte": "ITC", "valle d aosta": "ITC", "valle d'aosta": "ITC",
    "liguria": "ITC", "lombardia": "ITC",
    "trentino-alto adige": "ITD", "trentino alto adige": "ITD", "veneto": "ITD",
    "friuli-venezia giulia": "ITD", "friuli venezia giulia": "ITD",
    "emilia-romagna": "ITD", "emilia romagna": "ITD",
    "toscana": "ITE", "umbria": "ITE", "marche": "ITE", "lazio": "ITE",
    "abruzzo": "ITFG", "molise": "ITFG", "campania": "ITFG", "puglia": "ITFG",
    "basilicata": "ITFG", "calabria": "ITFG", "sicilia": "ITFG", "sardegna": "ITFG",
}


def area_for(region: str) -> str:
    """The index area for a region, falling back to the national series.

    National is a real fallback rather than a wrong one — it is the same
    measurement, just wider — but it is recorded as `IT` so the answer can say
    which was used.
    """
    return AREA_BY_REGION.get((region or "").strip().lower(), "IT")


def fetch(start: str = "2015-Q1", timeout: float = 90.0) -> str:
    response = httpx.get(BASE, params={"startPeriod": start, "format": "csv"},
                         timeout=timeout, follow_redirects=True,
                         headers={"Accept": "text/csv"})
    response.raise_for_status()
    if "TIME_PERIOD" not in response.text[:400]:
        raise ValueError("That did not come back as the IPAB table — no TIME_PERIOD column.")
    return response.text


def parse(text: str) -> list[dict[str, Any]]:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("MEASURE") != INDEX_MEASURE:
            continue                       # 6 and 7 are changes, not levels
        series = SERIES.get(row.get("PURCHASES_DWELLINGS", ""))
        value = row.get("OBS_VALUE")
        if not series or not value:
            continue
        try:
            number = float(value)
        except ValueError:
            continue
        rows.append({"series": series, "area": row["REF_AREA"],
                     "period": row["TIME_PERIOD"], "value": number, "source": SOURCE})
    if not rows:
        raise ValueError("Read the table but found no index levels in it.")
    return rows


def ingest(conn: sqlite3.Connection, start: str = "2015-Q1",
           timeout: float = 90.0) -> dict[str, Any]:
    store.migrate(conn)
    rows = parse(fetch(start, timeout=timeout))
    written = store.put_index(conn, rows)
    periods = sorted({r["period"] for r in rows})
    store.record_import(conn, "istat_hpi", f"{periods[0]}..{periods[-1]}", written)
    return {
        "rows": written,
        "from": periods[0], "to": periods[-1],
        "areas": sorted({r["area"] for r in rows}),
        "series": sorted({r["series"] for r in rows}),
        "source": SOURCE,
    }
