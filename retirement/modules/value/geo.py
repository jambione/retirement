"""Point-in-polygon against the OMI zones, without a spatial database.

The whole geometry problem here is one question — *which OMI zone is this pin
in* — asked a few thousand times at most. That does not need PostGIS: a
bounding-box filter in SQL narrows it to a handful of candidates, and a ray
cast in Python decides between them. Rings are stored as GeoJSON-order
coordinate pairs, `[lng, lat]`, because that is what every source hands us and
silently flipping them is the classic way to put a house in the sea.

Holes are handled the GeoJSON way: ring 0 is the outer boundary, rings 1+ are
holes, and a point inside a hole is outside the polygon.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Sequence

Ring = Sequence[Sequence[float]]


def point_in_ring(lng: float, lat: float, ring: Ring) -> bool:
    """Crossing-number ray cast. A point exactly on an edge is not worth
    special-casing: zone borders run down the middle of streets, and either
    answer is defensible there."""
    inside = False
    count = len(ring)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_at = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            if lng < x_at:
                inside = not inside
        j = i
    return inside


def point_in_rings(lng: float, lat: float, rings: Sequence[Ring]) -> bool:
    if not rings:
        return False
    if not point_in_ring(lng, lat, rings[0]):
        return False
    return not any(point_in_ring(lng, lat, hole) for hole in rings[1:])


def bounds_of(rings: Sequence[Ring]) -> tuple[float, float, float, float]:
    """(min_lat, min_lng, max_lat, max_lng) over every ring."""
    lats = [pt[1] for ring in rings for pt in ring]
    lngs = [pt[0] for ring in rings for pt in ring]
    if not lats or not lngs:
        raise ValueError("polygon has no coordinates")
    return min(lats), min(lngs), max(lats), max(lngs)


def zone_at(conn: sqlite3.Connection, lat: float, lng: float,
            semester: str = "") -> dict[str, Any] | None:
    """The OMI zone containing this point, or None.

    None is a real answer and is reported as one: OMI zone coverage is not
    complete, and a pin in an unmapped comune must fall back to the comune-wide
    band with the fallback said out loud, not be quietly assigned to the
    nearest zone.
    """
    sql = ("SELECT * FROM omi_zones WHERE min_lat <= ? AND max_lat >= ? "
           "AND min_lng <= ? AND max_lng >= ?")
    args: list[Any] = [lat, lat, lng, lng]
    if semester:
        sql += " AND semester = ?"
        args.append(semester)
    try:
        candidates = conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return None                      # no zones imported yet
    for row in candidates:
        try:
            rings = json.loads(row["rings"])
        except (TypeError, ValueError):
            continue
        if point_in_rings(lng, lat, rings):
            return dict(row)
    return None


def zones_in_bbox(conn: sqlite3.Connection, min_lat: float, min_lng: float,
                  max_lat: float, max_lng: float, limit: int = 500,
                  semester: str = "") -> list[dict[str, Any]]:
    """Zones whose own bbox overlaps the viewport — what the map asks for."""
    sql = ("SELECT semester, linkzona, istat, comune, zona, min_lat, min_lng, "
           "max_lat, max_lng, rings FROM omi_zones "
           "WHERE max_lat >= ? AND min_lat <= ? AND max_lng >= ? AND min_lng <= ?")
    args: list[Any] = [min_lat, max_lat, min_lng, max_lng]
    if semester:
        sql += " AND semester = ?"
        args.append(semester)
    sql += " LIMIT ?"
    args.append(limit)
    try:
        rows = conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["rings"] = json.loads(item["rings"])
        except (TypeError, ValueError):
            item["rings"] = []
        out.append(item)
    return out
