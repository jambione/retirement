"""Score one property against the official record.

    from retirement.modules.value import scoring
    scoring.score(conn, lat=40.75, lng=17.24, price=180_000, size_m2=120)

Everything the score rests on is free and official: the band comes from the
Agenzia delle Entrate, the hazard and census figures from ISPRA's IdroGEO, the
income from MEF, the amenities from OpenStreetMap. Every component carries its
own source string, and the composite carries the caveats, so the breakdown can
be printed next to the number without anyone having to remember where it came
from.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from retirement.modules.property.omi import normalise
from retirement.modules.value import geo, store
from retirement.modules.value.ingestion import osm as osm_source
from retirement.modules.value.scoring import bands, components, composite

__all__ = ["score", "bands", "components", "composite"]


def _identify(conn: sqlite3.Connection, lat: float | None, lng: float | None,
              istat: str, municipality: str) -> dict[str, Any]:
    """Which comune is this? Code first, then the zone polygon the pin fell in,
    then the name. Mergers are followed in every case."""
    resolved = None
    if istat:
        resolved = store.resolve_comune(conn, istat=str(istat).zfill(6))
    if resolved is None and lat is not None and lng is not None:
        zone = geo.zone_at(conn, lat, lng)
        if zone and zone.get("istat"):
            resolved = store.resolve_comune(conn, istat=str(zone["istat"]).zfill(6))
    if resolved is None and municipality:
        resolved = store.resolve_comune(conn, name_key=normalise(municipality))
    if resolved is None and lat is not None and lng is not None:
        # Last resort: the comune whose bounding box holds the pin. Approximate
        # by construction, so it is flagged and the caveat follows it out.
        boxed = store.comune_at(conn, lat, lng)
        if boxed:
            resolved = store.resolve_comune(conn, istat=boxed["istat"]) or boxed
            resolved = {**resolved, "approximate": True}
    return resolved or {}


def score(conn: sqlite3.Connection, *, lat: float | None = None, lng: float | None = None,
          price: float | None = None, size_m2: float | None = None,
          typology: str = "", condition: str = "", asking_rent_month: float | None = None,
          municipality: str = "", istat: str = "", province: str = "",
          weights: dict[str, float] | None = None,
          fetch_amenities: bool = False) -> dict[str, Any]:
    """The whole breakdown for one property.

    `fetch_amenities=False` means OpenStreetMap is read from cache only — a
    bulk rescore of a shortlist must not become a burst of Overpass traffic.
    Pass True for a single interactive lookup.
    """
    store.migrate(conn)
    w = {**composite.DEFAULT_WEIGHTS, **(weights or {})}

    comune = _identify(conn, lat, lng, istat, municipality)
    comune_istat = comune.get("istat", "") or (str(istat).zfill(6) if istat else "")
    province = province or comune.get("prov", "")

    band = bands.band_for(conn, lat, lng, municipality=municipality or comune.get("name", ""),
                          istat=comune_istat, typology=typology, condition=condition)
    stats = store.stats_for(conn, comune_istat) if comune_istat else {}

    distances = None
    if lat is not None and lng is not None:
        distances = (osm_source.fetch(conn, lat, lng) if fetch_amenities
                     else osm_source.cached(conn, lat, lng))

    parts = [
        components.price(price, size_m2, band, w["price"]),
        components.gross_yield(price, size_m2, band, asking_rent_month, w["yield"]),
        components.demand(stats, w["demand"]),
        components.liquidity(stats, w["liquidity"]),
        components.amenities(distances, w["amenities"]),
    ]
    risk = components.risk_penalty(stats, w["risk_cap"])

    notes = composite.standing_caveats(band, province)
    if comune.get("approximate"):
        notes.append(
            f"Comune identified as {comune.get('name') or comune_istat} by bounding box, "
            "not by boundary — near an edge that can be the wrong town. Import the OMI "
            "zone polygons, or pass the comune, to remove the guess."
        )
    result = composite.combine(parts, risk, notes)
    result.update({
        "input": {"lat": lat, "lng": lng, "price": price, "size_m2": size_m2,
                  "typology": typology, "condition": condition,
                  "asking_rent_month": asking_rent_month},
        "comune": {"istat": comune_istat, "name": comune.get("name", "") or municipality,
                   "prov": province, "region": comune.get("region", "")},
        "omi_band": band,
        "sources": sorted({c.source for c in parts + [risk] if c.source}),
    })
    return result


def score_listing(conn: sqlite3.Connection, listing: Any,
                  weights: dict[str, float] | None = None,
                  fetch_amenities: bool = False) -> dict[str, Any]:
    """Same thing for a `property.models.Listing`, and it remembers the answer
    so the shortlist page does not recompute on every render."""
    result = score(
        conn,
        lat=getattr(listing, "lat", None), lng=getattr(listing, "lng", None),
        price=getattr(listing, "price", None), size_m2=getattr(listing, "size_sqm", None),
        typology=getattr(listing, "property_type", ""),
        condition=getattr(listing, "condition", ""),
        municipality=getattr(listing, "municipality", ""),
        province=getattr(listing, "province", ""),
        weights=weights, fetch_amenities=fetch_amenities,
    )
    listing_id = getattr(listing, "id", "")
    if listing_id:
        result["listing_id"] = listing_id
        store.save_score(conn, listing_id, result)
    return result
