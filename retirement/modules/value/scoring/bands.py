"""Choosing which OMI band a listing should be judged against, and saying so.

OMI prices a *typology* in a *condition* in a *zone*. A listing arrives with a
portal's own vocabulary ("chalet", "newdevelopment") and often without a zone,
so something has to choose — and whatever it chooses has to be visible, because
a band chosen by fallback is a weaker claim than one chosen exactly and the
reader is entitled to know which they are looking at.

The order is: exact typology + exact condition, then the same typology in
NORMALE condition, then any residential typology in the zone, then the same
ladder at comune grain. Each step down is recorded in `match` and turned into a
caveat by the composite.
"""
from __future__ import annotations

import sqlite3
from statistics import median
from typing import Any, Sequence

from retirement.modules.property.omi import normalise
from retirement.modules.value import geo, store

SOURCE = "Agenzia Entrate — OMI"

# Portal vocabulary -> OMI typology. Anything unrecognised falls through to the
# whole residential set rather than being forced into one.
TYPOLOGY = {
    "villa": "ville e villini", "villas": "ville e villini",
    "chalet": "ville e villini", "countryhouse": "ville e villini",
    "detached": "ville e villini", "trullo": "ville e villini",
    "homes": "abitazioni civili", "home": "abitazioni civili",
    "apartment": "abitazioni civili", "flat": "abitazioni civili",
    "appartamento": "abitazioni civili", "casa": "abitazioni civili",
    "economic": "abitazioni di tipo economico",
    "signorili": "abitazioni signorili", "luxury": "abitazioni signorili",
}

# Portal condition -> OMI stato conservativo.
CONDITION = {
    "good": "NORMALE", "normale": "NORMALE", "buono": "NORMALE", "": "NORMALE",
    "newdevelopment": "OTTIMO", "new": "OTTIMO", "ottimo": "OTTIMO",
    "restored": "OTTIMO", "ristrutturato": "OTTIMO",
    "renew": "SCADENTE", "torenovate": "SCADENTE", "scadente": "SCADENTE",
    "daristrutturare": "SCADENTE",
}


def omi_typology(raw: str) -> str:
    return TYPOLOGY.get((raw or "").strip().lower().replace(" ", ""), "")


def omi_condition(raw: str) -> str:
    return CONDITION.get((raw or "").strip().lower().replace(" ", ""), "NORMALE")


def _aggregate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    sale_mids = [(r["compr_min"] + r["compr_max"]) / 2
                 for r in rows if r.get("compr_min") and r.get("compr_max")]
    rent_mids = [(r["loc_min"] + r["loc_max"]) / 2
                 for r in rows if r.get("loc_min") and r.get("loc_max")]
    sale_lows = [r["compr_min"] for r in rows if r.get("compr_min")]
    sale_highs = [r["compr_max"] for r in rows if r.get("compr_max")]
    return {
        "sale_mid": round(median(sale_mids)) if sale_mids else None,
        "sale_min": round(min(sale_lows)) if sale_lows else None,
        "sale_max": round(max(sale_highs)) if sale_highs else None,
        "rent_mid": round(median(rent_mids), 2) if rent_mids else None,
        "rent_min": round(min(r["loc_min"] for r in rows if r.get("loc_min")), 2)
                    if any(r.get("loc_min") for r in rows) else None,
        "rent_max": round(max(r["loc_max"] for r in rows if r.get("loc_max")), 2)
                    if any(r.get("loc_max") for r in rows) else None,
        "rows_used": len(rows),
    }


def _pick(rows: Sequence[dict[str, Any]], typology: str,
          condition: str) -> tuple[list[dict[str, Any]], str]:
    if not rows:
        return [], "none"
    typed = [r for r in rows if (r.get("tipologia") or "").strip().lower() == typology] if typology else []
    exact = [r for r in typed if (r.get("stato") or "").upper().startswith(condition[:3])]
    if exact:
        return exact, "typology+condition"
    normale = [r for r in typed if (r.get("stato") or "").upper().startswith("NOR")]
    if normale:
        return normale, "typology, condition→normale"
    if typed:
        return typed, "typology, any condition"
    any_normale = [r for r in rows if (r.get("stato") or "").upper().startswith("NOR")]
    if any_normale:
        return any_normale, "all residential typologies, normale"
    return list(rows), "all residential rows in the zone"


def band_for(conn: sqlite3.Connection, lat: float | None, lng: float | None,
             municipality: str = "", istat: str = "", typology: str = "",
             condition: str = "") -> dict[str, Any]:
    """The band to judge a listing against, plus how it was found.

    Returns `available: False` rather than a number when OMI has nothing for
    this place — the score then has no price component at all, which is the
    honest outcome and not a zero.
    """
    semester = store.latest_semester(conn)
    typ, cond = omi_typology(typology), omi_condition(condition)
    zone = geo.zone_at(conn, lat, lng, semester) if (lat is not None and lng is not None) else None

    rows: list[dict[str, Any]] = []
    grain, zone_info = "", None
    if zone:
        rows = store.zone_bands(conn, zone["linkzona"], semester)
        if rows:
            grain = "zone"
            zone_info = {"linkzona": zone["linkzona"], "zona": zone.get("zona", ""),
                         "comune": zone.get("comune", "")}
    if not rows:
        rows = store.comune_bands(conn, istat=istat,
                                  comune_key=normalise(municipality), semester=semester)
        grain = "comune" if rows else ""

    if not rows:
        return {"available": False, "semester": semester, "source": SOURCE,
                "reason": "No OMI quotation loaded for this comune.",
                "grain": None, "match": "none"}

    chosen, match = _pick(rows, typ, cond)
    band = _aggregate(chosen)
    band.update({
        "available": band["sale_mid"] is not None or band["rent_mid"] is not None,
        "semester": semester, "source": SOURCE, "grain": grain, "match": match,
        "zone": zone_info, "typology": typ or "any residential",
        "condition": cond,
        "zones_in_comune": len({r.get("linkzona") for r in rows}) if grain == "comune" else 1,
    })
    return band
