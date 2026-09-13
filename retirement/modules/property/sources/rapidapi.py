"""Unofficial Idealista data via RapidAPI.

A self-service key, issued in seconds, against a third-party wrapper around
idealista. What that buys and what it costs:

  + listings today, without waiting on an access request that may never be
    answered
  - unofficial: it scrapes the portal, so it breaks when the portal changes,
    and it is the activity idealista's own error responses discourage

THE SHAPE OF THE RESPONSE IS NOT VERIFIED. RapidAPI does not publish it without
a key, and guessing a JSON shape and shipping it is how you get a source that
returns nothing and blames the network. So the normaliser reads defensively --
it tries the spellings these wrappers commonly use -- and when it recognises
nothing it says so and points at `./retire probe rapidapi`, which makes one
real call and writes the raw response out. One round trip and this file can be
made exact rather than hopeful.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from retirement.core import secrets
from retirement.modules.property.models import Listing
from retirement.modules.property.sources.base import Source

DEFAULT_HOST = "idealista2.p.rapidapi.com"

# Key spellings seen across these wrappers. Order is preference.
LIST_KEYS = ("elementList", "elements", "results", "listings", "data", "items")
FIELDS = {
    "external_id": ("propertyCode", "id", "propertyId", "code"),
    "url": ("url", "link", "detailUrl", "propertyUrl"),
    "title": ("title", "suggestedTexts.title", "address", "name"),
    "description": ("description", "summary", "descriptionText"),
    "price": ("price", "priceAmount", "amount"),
    "size_sqm": ("size", "sizeM2", "surface", "area"),
    "rooms": ("rooms", "roomsNumber", "bedrooms"),
    "bathrooms": ("bathrooms", "bathroomsNumber", "baths"),
    "lat": ("latitude", "lat"),
    "lng": ("longitude", "lng", "lon"),
    "municipality": ("municipality", "city", "town", "locationName"),
    "province": ("province", "region", "state"),
    "condition": ("status", "condition", "propertyStatus"),
    "photos": ("numPhotos", "photosCount", "imagesCount"),
    "thumbnail": ("thumbnail", "image", "mainImage", "photo"),
}


class UnrecognisedShape(RuntimeError):
    pass


def _dig(payload: dict[str, Any], path: str) -> Any:
    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _first(payload: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = _dig(payload, name)
        if value not in (None, ""):
            return value
    return None


def _num(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("€", "").strip())
    except (TypeError, ValueError):
        return None


def find_elements(payload: Any) -> list[dict[str, Any]]:
    """The list of listings, wherever this wrapper decided to put it."""
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if not isinstance(payload, dict):
        raise UnrecognisedShape("the response was neither a list nor an object")
    for key in LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
        # one level of nesting: {"data": {"results": [...]}}
        if isinstance(value, dict):
            for inner in LIST_KEYS:
                nested = value.get(inner)
                if isinstance(nested, list) and nested and isinstance(nested[0], dict):
                    return nested
    raise UnrecognisedShape(
        "no list of listings found in the response. Top-level keys were: "
        + ", ".join(sorted(payload)[:12])
        + ". Run `./retire probe rapidapi` and the raw response will be written "
          "out so the field mapping can be corrected."
    )


def to_listing(element: dict[str, Any], area_id: str) -> Listing | None:
    external_id = _first(element, FIELDS["external_id"])
    if external_id in (None, ""):
        return None
    code = str(external_id)
    return Listing(
        id=f"rapidapi:{code}",
        source="rapidapi",
        external_id=code,
        url=str(_first(element, FIELDS["url"]) or ""),
        title=str(_first(element, FIELDS["title"]) or "").strip(),
        description=str(_first(element, FIELDS["description"]) or ""),
        price=_num(_first(element, FIELDS["price"])),
        size_sqm=_num(_first(element, FIELDS["size_sqm"])),
        rooms=int(_num(_first(element, FIELDS["rooms"])) or 0) or None,
        bathrooms=int(_num(_first(element, FIELDS["bathrooms"])) or 0) or None,
        lat=_num(_first(element, FIELDS["lat"])),
        lng=_num(_first(element, FIELDS["lng"])),
        municipality=str(_first(element, FIELDS["municipality"]) or ""),
        province=str(_first(element, FIELDS["province"]) or ""),
        area_id=area_id,
        condition=str(_first(element, FIELDS["condition"]) or ""),
        photos=int(_num(_first(element, FIELDS["photos"])) or 0),
        thumbnail=str(_first(element, FIELDS["thumbnail"]) or ""),
        raw=element,
    )


class RapidApiSource(Source):
    name = "rapidapi"

    def available(self) -> bool:
        return bool(secrets.get("rapidapi_key"))

    def _host(self) -> str:
        return secrets.get("rapidapi_host") or DEFAULT_HOST

    def request(self, area: dict[str, Any], page: int = 1) -> Any:
        budget = self.config.get("budget", {})
        prop = self.config.get("property", {})
        lat, lng = area["center"]
        params = {
            "country": "it",
            "operation": prop.get("operation", "sale"),
            "propertyType": (prop.get("types") or ["homes"])[0],
            "locationName": area.get("label", ""),
            "latitude": lat,
            "longitude": lng,
            "radius": int(float(area.get("radius_km", 25)) * 1000),
            "numPage": page,
            "maxItems": 40,
            "sort": "asc",
        }
        if budget.get("min"):
            params["minPrice"] = int(budget["min"])
        if budget.get("max"):
            params["maxPrice"] = int(budget["max"])
        if prop.get("min_size_sqm"):
            params["minSize"] = int(prop["min_size_sqm"])

        response = httpx.get(
            f"https://{self._host()}/properties/list",
            params=params,
            headers={
                "X-RapidAPI-Key": secrets.get("rapidapi_key"),
                "X-RapidAPI-Host": self._host(),
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def fetch(self, area: dict[str, Any]) -> list[Listing]:
        payload = self.request(area)
        listings = []
        for element in find_elements(payload):
            listing = to_listing(element, area["id"])
            if listing:
                listings.append(listing)
        if not listings:
            raise UnrecognisedShape(
                "found a list in the response but no recognisable listing fields. "
                "Run `./retire probe rapidapi` to dump one and correct the mapping."
            )
        return listings
