"""Catasto — deliberately not implemented yet, and here is exactly what it is.

The Agenzia delle Entrate publishes the national cadastral cartography — every
`particella` in Italy, as GML, CC-BY 4.0 — plus a WFS/WMS for the same layer.
It is the right long-term join: parcel geometry is what turns "this listing
claims 130 m²" into "this parcel is 118 m² and the building on it is category
A/3, class 2".

Why it is not in this deliverable:

  * **Volume.** The national GML set is tens of gigabytes; a province is
    hundreds of megabytes. Loading that into SQLite to answer a question that
    the OMI zone polygon already answers at the right grain would be paying a
    lot for a little. The plan of record is: zone-level now, parcel-level when
    the score demonstrably needs it, and PostGIS on that day.
  * **Two provinces are missing by design.** Trento and Bolzano run the
    *sistema tavolare* (Grundbuch) and are not in the national cadastral
    release; parts of Gorizia and Trieste have the same history. Any national
    rollout has to special-case them rather than show them as empty.
  * **Rendita catastale is not a market value.** It is a fiscal figure, often
    decades stale, and the temptation to divide by it and call the result a
    yield is exactly the sort of plausible wrong number this project refuses to
    print.

What you can already do without it: the OMI zone polygon gives the band, and
ANNCSU (the national street-and-civic-number register, also free) is the
geocoding source when Nominatim is too coarse for a rural address.

When this file grows a real implementation it should:
  1. take a province GML/GeoPackage from `data/catasto/`,
  2. store particella geometry with its `foglio`/`particella` identifiers,
  3. expose `parcel_at(lat, lng)` the way `geo.zone_at` does today.
"""
from __future__ import annotations

SOURCES = {
    "cartografia": (
        "Agenzia delle Entrate — Cartografia catastale (particelle), GML, CC-BY 4.0. "
        "Bulk download per province, plus WFS/WMS."
    ),
    "anncsu": (
        "ANNCSU — Archivio Nazionale dei Numeri Civici e delle Strade Urbane. "
        "Streets and civic numbers, for geocoding rural addresses Nominatim misses."
    ),
    "excluded": "Trento and Bolzano (sistema tavolare), parts of Gorizia and Trieste.",
}


def parcel_at(lat: float, lng: float):
    raise NotImplementedError(
        "Parcel matching is not implemented. The OMI zone polygon (geo.zone_at) "
        "is the grain this score works at; see the module docstring for what a "
        "cadastral implementation would involve and why it waits."
    )
