"""HTTP surface for the value finder.

    POST /api/value/score              one property, full breakdown
    GET  /api/value/search             scored listings, filterable
    GET  /api/value/listing/{id}       the stored breakdown for one listing
    GET  /api/value/zones              OMI zone polygons in a bbox (for the map)
    GET  /api/value/stats/comune/{code}  every official figure held for a comune
    GET  /api/value/coverage           what data is actually loaded
    POST /api/value/listings/upload    CSV or GeoJSON of your own listings

Every response that carries a price comparison carries its OMI attribution in
the same payload. That is a licence condition, and putting it in the response
rather than only in the page means an API consumer cannot drop it by accident.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Any

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from retirement.core import db
from retirement.modules.property.models import Listing
from retirement.modules.property import store as listing_store
from retirement.modules.value import geo, store
from retirement.modules.value import scoring

router = APIRouter(prefix="/api/value", tags=["value"])

ATTRIBUTION = [
    "Agenzia Entrate — OMI (Quotazioni Immobiliari)",
    "ISPRA — IdroGEO (CC-BY 4.0)",
    "MEF — Dipartimento delle Finanze",
    "ISTAT",
    "© OpenStreetMap contributors (ODbL)",
]


def _conn() -> sqlite3.Connection:
    conn = db.connect()
    store.migrate(conn)
    return conn


@router.post("/score")
async def score_one(request: Request):
    """Body: {lat, lon|lng, price, size_m2, typology?, condition?, rent_month?,
    comune?, istat?, fetch_amenities?}"""
    payload = await request.json()
    lat = payload.get("lat")
    lng = payload.get("lng", payload.get("lon"))
    try:
        result = scoring.score(
            _conn(),
            lat=float(lat) if lat is not None else None,
            lng=float(lng) if lng is not None else None,
            price=_float(payload.get("price")),
            size_m2=_float(payload.get("size_m2", payload.get("size"))),
            typology=str(payload.get("typology", "")),
            condition=str(payload.get("condition", "")),
            asking_rent_month=_float(payload.get("rent_month")),
            municipality=str(payload.get("comune", payload.get("municipality", ""))),
            istat=str(payload.get("istat", "")),
            province=str(payload.get("province", "")),
            weights=payload.get("weights"),
            fetch_amenities=bool(payload.get("fetch_amenities")),
            # Scored means kept: without a stored row there is nothing for a
            # reference to point at.
            persist=payload.get("persist", True),
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    result["attribution"] = ATTRIBUTION
    return JSONResponse(result)


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@router.get("/search")
def search(min_score: float = Query(0, ge=0, le=100),
           max_price: float | None = None, min_yield: float | None = None,
           max_risk: float | None = None, region: str = "", province: str = "",
           limit: int = Query(50, ge=1, le=500), rescore: bool = False):
    """Scored listings from the shortlist, filtered the way an investor filters.

    `rescore=true` recomputes from the current data instead of reading the
    stored breakdown — use it after an import, not on every page load.
    """
    conn = _conn()
    rows = conn.execute(
        """SELECT * FROM listings WHERE active = 1
           AND (? = '' OR region = ?) AND (? = '' OR province = ?)
           AND (? IS NULL OR price <= ?)
           ORDER BY COALESCE(price, 1e12) LIMIT ?""",
        (region, region, province, province, max_price, max_price, limit * 3),
    ).fetchall()

    out = []
    for row in rows:
        listing = dict(row)
        breakdown = None
        if not rescore:
            stored = conn.execute(
                "SELECT breakdown FROM value_scores WHERE listing_id = ?", (listing["id"],)
            ).fetchone()
            if stored:
                breakdown = json.loads(stored["breakdown"])
        if breakdown is None:
            breakdown = scoring.score_listing(conn, Listing(**_listing_fields(listing)))
        if breakdown.get("score") is None or breakdown["score"] < min_score:
            continue
        if min_yield is not None:
            y = _component(breakdown, "yield")
            if not y or (y.get("value") or {}).get("gross_yield_pct", 0) < min_yield:
                continue
        if max_risk is not None and (breakdown.get("risk_penalty") or 0) > max_risk:
            continue
        out.append({"listing": {k: listing[k] for k in
                                ("id", "url", "title", "price", "size_sqm", "municipality",
                                 "province", "lat", "lng", "thumbnail")},
                    "value": breakdown})
        if len(out) >= limit:
            break

    out.sort(key=lambda item: item["value"]["score"], reverse=True)
    return JSONResponse({"count": len(out), "results": out, "attribution": ATTRIBUTION})


def _listing_fields(row: dict[str, Any]) -> dict[str, Any]:
    keep = {k: row.get(k) for k in Listing.model_fields if k in row}
    keep["raw"] = json.loads(row["raw"]) if row.get("raw") else {}
    keep["id"] = row["id"]
    return keep


def _component(breakdown: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((c for c in breakdown.get("components", []) if c["key"] == key), None)


@router.get("/listing/{listing_id}")
def one_listing(listing_id: str, rescore: bool = False):
    conn = _conn()
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if row is None:
        return JSONResponse({"error": f"no listing {listing_id}"}, status_code=404)
    listing = dict(row)
    stored = None if rescore else conn.execute(
        "SELECT breakdown, scored_at FROM value_scores WHERE listing_id = ?", (listing_id,)
    ).fetchone()
    breakdown = (json.loads(stored["breakdown"]) if stored
                 else scoring.score_listing(conn, Listing(**_listing_fields(listing))))
    return JSONResponse({"listing": listing, "value": breakdown,
                         "attribution": ATTRIBUTION})


@router.get("/ref/{reference}")
def by_reference(reference: str):
    """Look a property up by its reference — CIS1001 — or by the portal's own
    code, the listing id, or a fragment of its URL."""
    found = store.by_ref(_conn(), reference)
    if found is None:
        return JSONResponse(
            {"error": f"Nothing on record for {reference!r}. References look like "
                      "CIS1001: three letters of the comune, then a number."},
            status_code=404,
        )
    return JSONResponse({**found, "attribution": ATTRIBUTION})


@router.get("/zones")
def zones(min_lat: float, min_lng: float, max_lat: float, max_lng: float,
          limit: int = Query(300, ge=1, le=2000)):
    """GeoJSON for the map. Empty is a real answer: zone polygons are imported
    per comune, so most of Italy has none until someone downloads them."""
    conn = _conn()
    found = geo.zones_in_bbox(conn, min_lat, min_lng, max_lat, max_lng, limit)
    features = [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": z["rings"]},
        "properties": {"linkzona": z["linkzona"], "comune": z["comune"],
                       "zona": z["zona"], "semester": z["semester"]},
    } for z in found]
    return JSONResponse({"type": "FeatureCollection", "features": features,
                         "count": len(features),
                         "attribution": "Agenzia Entrate — OMI"})


@router.get("/stats/comune/{code}")
def comune_stats(code: str):
    conn = _conn()
    comune = store.resolve_comune(conn, istat=str(code).zfill(6))
    if comune is None:
        return JSONResponse({"error": f"no comune {code} on record"}, status_code=404)
    stats = store.stats_for(conn, comune["istat"])
    return JSONResponse({
        "comune": comune,
        "stats": stats,
        "series": {metric: store.stats_series(conn, comune["istat"], metric)
                   for metric in ("population", "income_avg", "tourism_beds", "ntn")},
        "omi": store.comune_bands(conn, istat=comune["istat"])[:50],
        "attribution": ATTRIBUTION,
    })


@router.get("/coverage")
def coverage():
    return JSONResponse({**store.coverage(_conn()), "attribution": ATTRIBUTION})


# ── listings in ────────────────────────────────────────────────────────────
UPLOAD_FIELDS = ("id", "url", "title", "price", "size_m2", "lat", "lon", "lng",
                 "comune", "province", "typology", "condition", "rent_month")


@router.post("/listings/upload")
async def upload(file: UploadFile = File(...)):
    """CSV or GeoJSON of your own listings — the provider that needs no portal.

    Columns (header row, any order): url, title, price, size_m2, lat, lon,
    comune, province, typology, condition, rent_month. Everything but price and
    one of (lat/lon | comune) is optional.
    """
    conn = _conn()
    raw = await file.read()
    name = (file.filename or "upload").lower()
    try:
        rows = (_from_geojson(raw) if name.endswith((".geojson", ".json"))
                else _from_csv(raw))
    except Exception as exc:
        return JSONResponse({"error": f"could not read that file: {exc}"}, status_code=400)

    listing_store.migrate(conn)
    saved, scored, problems = 0, [], []
    for index, row in enumerate(rows, start=1):
        price = _float(row.get("price"))
        lat, lng = _float(row.get("lat")), _float(row.get("lng", row.get("lon")))
        if price is None or (lat is None and not row.get("comune")):
            problems.append(f"row {index}: needs a price and either lat/lon or a comune")
            continue
        listing = Listing(
            id=f"upload:{row.get('id') or row.get('url') or index}",
            source="upload", external_id=str(row.get("id") or index),
            url=str(row.get("url") or ""), title=str(row.get("title") or ""),
            price=price, size_sqm=_float(row.get("size_m2")),
            lat=lat, lng=lng, municipality=str(row.get("comune") or ""),
            province=str(row.get("province") or ""), area_id="upload",
            property_type=str(row.get("typology") or ""),
            condition=str(row.get("condition") or ""), raw=dict(row),
        )
        listing_store.upsert(conn, listing)
        saved += 1
        breakdown = scoring.score_listing(conn, listing)
        scored.append({"id": listing.id, "score": breakdown.get("score"),
                       "band": breakdown.get("band"),
                       "confidence": breakdown.get("confidence")})
    return JSONResponse({"saved": saved, "scored": scored, "problems": problems,
                         "attribution": ATTRIBUTION})


def _from_csv(raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8-sig", errors="replace")
    delimiter = ";" if text[:2000].count(";") > text[:2000].count(",") else ","
    return [{(k or "").strip().lower(): v for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(text), delimiter=delimiter)]


def _from_geojson(raw: bytes) -> list[dict[str, Any]]:
    payload = json.loads(raw.decode("utf-8-sig", errors="replace"))
    out = []
    for feature in payload.get("features", []):
        props = {str(k).lower(): v for k, v in (feature.get("properties") or {}).items()}
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) >= 2:
            props.setdefault("lng", coords[0])
            props.setdefault("lat", coords[1])
        out.append(props)
    return out
