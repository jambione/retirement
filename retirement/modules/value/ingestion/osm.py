"""OpenStreetMap — how far the nearest useful thing is.

One Overpass query per pin returns the nearest station, supermarket, school,
pharmacy, hospital, restaurant cluster and coastline within a radius, and those
distances are the amenity component of the score. OSM is ODbL: attribution
"© OpenStreetMap contributors" is required wherever these distances are shown.

Two rules this file exists to keep:

  * **Overpass is a donated shared resource.** One request per rounded cell,
    cached in SQLite for a month, and a minimum gap between calls. Two houses on
    the same street ask the same question — they must not ask it twice.
  * **Straight-line distance is not travel time.** A supermarket 800 m away
    across a gorge is not 800 m away. The score treats these as a coarse
    "is there anything here at all" signal, which is what they can support.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

import httpx

from retirement.core.db import utcnow
from retirement.modules.property.geo import haversine_km
from retirement.modules.value import store

ENDPOINT = "https://overpass-api.de/api/interpreter"
SOURCE = "© OpenStreetMap contributors (ODbL)"
CACHE_DAYS = 30
MIN_GAP_SECONDS = 2.0
_last_call = 0.0

# what we look for -> the Overpass filter for it
KINDS: dict[str, str] = {
    "station": 'node["railway"="station"]',
    "bus": 'node["highway"="bus_stop"]',
    "supermarket": 'node["shop"~"supermarket|convenience"]',
    "school": 'node["amenity"~"school|kindergarten"]',
    "pharmacy": 'node["amenity"="pharmacy"]',
    "hospital": 'node["amenity"~"hospital|clinic"]',
    "restaurant": 'node["amenity"~"restaurant|cafe|bar"]',
    "doctor": 'node["amenity"="doctors"]',
}
DEFAULT_RADIUS_M = 15000


def _cell(lat: float, lng: float) -> str:
    """~1 km buckets. Fine enough that the answer is still true for the pin,
    coarse enough that a street does not generate ten queries."""
    return f"{lat:.2f},{lng:.2f}"


def _query(lat: float, lng: float, radius_m: int) -> str:
    parts = "".join(
        f'{filt}(around:{radius_m},{lat},{lng});' for filt in KINDS.values()
    )
    return f"[out:json][timeout:25];({parts});out body center;"


def cached(conn: sqlite3.Connection, lat: float, lng: float) -> dict[str, Any] | None:
    try:
        rows = conn.execute(
            "SELECT kind, dist_km, name, fetched_at FROM osm_cache WHERE cell = ?",
            (_cell(lat, lng),),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    if not rows:
        return None
    age_days = (
        (time.time() - time.mktime(time.strptime(rows[0]["fetched_at"][:10], "%Y-%m-%d")))
        / 86400
    )
    if age_days > CACHE_DAYS:
        return None
    return {r["kind"]: {"km": r["dist_km"], "name": r["name"]} for r in rows}


def fetch(conn: sqlite3.Connection, lat: float, lng: float,
          radius_m: int = DEFAULT_RADIUS_M, timeout: float = 40.0) -> dict[str, Any]:
    """Nearest of each kind, in km. Kinds with nothing inside the radius are
    absent from the result rather than present with a made-up large number."""
    global _last_call
    store.migrate(conn)
    hit = cached(conn, lat, lng)
    if hit is not None:
        return hit

    gap = time.monotonic() - _last_call
    if gap < MIN_GAP_SECONDS:
        time.sleep(MIN_GAP_SECONDS - gap)
    _last_call = time.monotonic()

    response = httpx.post(ENDPOINT, data={"data": _query(lat, lng, radius_m)},
                          timeout=timeout, headers={"User-Agent": "retirement-value-finder"})
    response.raise_for_status()
    elements = response.json().get("elements", [])

    nearest: dict[str, dict[str, Any]] = {}
    for element in elements:
        tags = element.get("tags") or {}
        point = element.get("center") or element
        plat, plng = point.get("lat"), point.get("lon")
        if plat is None or plng is None:
            continue
        km = haversine_km(lat, lng, plat, plng)
        for kind, filt in KINDS.items():
            if not _matches(kind, tags):
                continue
            if kind not in nearest or km < nearest[kind]["km"]:
                nearest[kind] = {"km": round(km, 2), "name": tags.get("name", "")}

    conn.executemany(
        """INSERT OR REPLACE INTO osm_cache (cell, kind, dist_km, name, fetched_at)
           VALUES (?,?,?,?,?)""",
        [(_cell(lat, lng), kind, item["km"], item["name"], utcnow())
         for kind, item in nearest.items()],
    )
    conn.commit()
    return nearest


def _matches(kind: str, tags: dict[str, str]) -> bool:
    shop, amenity, railway, highway = (tags.get("shop", ""), tags.get("amenity", ""),
                                       tags.get("railway", ""), tags.get("highway", ""))
    return {
        "station": railway == "station",
        "bus": highway == "bus_stop",
        "supermarket": shop in ("supermarket", "convenience"),
        "school": amenity in ("school", "kindergarten"),
        "pharmacy": amenity == "pharmacy",
        "hospital": amenity in ("hospital", "clinic"),
        "restaurant": amenity in ("restaurant", "cafe", "bar"),
        "doctor": amenity == "doctors",
    }.get(kind, False)
