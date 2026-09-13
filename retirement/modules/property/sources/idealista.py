"""Idealista Search API client.

Italy has no MLS. Idealista is the one major Italian portal that publishes a
real developer API, so it is the source of record here. Request a key at
https://developers.idealista.com/access-request -- you get an API key and a
secret, and the free allowance is small, so this client:

  * caches the OAuth token until it expires,
  * refuses to exceed RETIREMENT_IDEALISTA_MONTHLY_QUOTA calls per month,
  * counts every call it makes in the api_usage table.

Budget arithmetic: one call returns at most 50 listings. With three search
areas, a nightly run costs 3-6 calls, so ~100-180 a month. If your approved
allowance is 100/month, either run weekly (`--weekly` in the launchd plist) or
narrow the areas. The quota guard will stop the run rather than fail silently.
"""
from __future__ import annotations

import base64
import time
from typing import Any

import httpx

from retirement.core import db
from retirement.core.config import env, env_int
from retirement.modules.property.models import Listing
from retirement.modules.property.sources.base import Source

TOKEN_URL = "https://api.idealista.com/oauth/token"
SEARCH_URL = "https://api.idealista.com/3.5/{country}/search"
MAX_ITEMS = 50


class QuotaExceeded(RuntimeError):
    pass


class IdealistaSource(Source):
    name = "idealista"

    def __init__(self, conn, config):
        super().__init__(conn, config)
        self._token: str | None = None
        self._token_expires: float = 0.0
        self.country = (config.get("country") or "it").lower()

    def available(self) -> bool:
        return bool(env("IDEALISTA_API_KEY") and env("IDEALISTA_SECRET"))

    # -- auth ---------------------------------------------------------------
    def _authenticate(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        key, secret = env("IDEALISTA_API_KEY"), env("IDEALISTA_SECRET")
        if not (key and secret):
            raise RuntimeError("IDEALISTA_API_KEY / IDEALISTA_SECRET are not set")
        basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
        resp = httpx.post(
            TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            },
            data={"grant_type": "client_credentials", "scope": "read"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    # -- quota --------------------------------------------------------------
    def _check_quota(self) -> None:
        quota = env_int("RETIREMENT_IDEALISTA_MONTHLY_QUOTA", 100)
        used = db.usage_this_month(self.conn, self.name)
        if used >= quota:
            raise QuotaExceeded(
                f"idealista: {used}/{quota} calls used this month. "
                "Raise RETIREMENT_IDEALISTA_MONTHLY_QUOTA or wait for the reset."
            )

    # -- search -------------------------------------------------------------
    def _search_page(self, params: dict[str, Any]) -> dict[str, Any]:
        self._check_quota()
        token = self._authenticate()
        resp = httpx.post(
            SEARCH_URL.format(country=self.country),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=params,
            timeout=60,
        )
        db.record_api_call(self.conn, self.name, 1)
        if resp.status_code == 429:
            raise QuotaExceeded("idealista returned 429: rate limit hit")
        resp.raise_for_status()
        time.sleep(1.1)  # the API is documented as roughly one request per second
        return resp.json()

    def fetch(self, area: dict[str, Any]) -> list[Listing]:
        budget = self.config.get("budget", {})
        prop = self.config.get("property", {})
        lat, lng = area["center"]

        params: dict[str, Any] = {
            "operation": prop.get("operation", "sale"),
            "propertyType": (prop.get("types") or ["homes"])[0],
            "center": f"{lat},{lng}",
            "distance": int(float(area.get("radius_km", 25)) * 1000),
            "maxItems": MAX_ITEMS,
            "numPage": 1,
            "order": "priceDown",
            "sort": "asc",
            "language": "it",
        }
        if budget.get("min"):
            params["minPrice"] = int(budget["min"])
        if budget.get("max"):
            params["maxPrice"] = int(budget["max"])
        if prop.get("min_size_sqm"):
            params["minSize"] = int(prop["min_size_sqm"])

        listings: list[Listing] = []
        max_pages = int(self.config.get("max_pages_per_area", 2))
        for page in range(1, max_pages + 1):
            params["numPage"] = page
            payload = self._search_page(dict(params))
            elements = payload.get("elementList") or []
            listings.extend(self._to_listing(el, area["id"]) for el in elements)
            if page >= int(payload.get("totalPages", 1)) or len(elements) < MAX_ITEMS:
                break
        return listings

    # -- normalisation ------------------------------------------------------
    @staticmethod
    def _to_listing(el: dict[str, Any], area_id: str) -> Listing:
        code = str(el.get("propertyCode", ""))
        return Listing(
            id=f"idealista:{code}",
            source="idealista",
            external_id=code,
            url=el.get("url", ""),
            title=" ".join(
                part
                for part in [
                    el.get("propertyType", ""),
                    el.get("address", ""),
                    el.get("municipality", ""),
                ]
                if part
            ).strip(),
            description=el.get("description", "") or "",
            price=_as_float(el.get("price")),
            currency="EUR",
            size_sqm=_as_float(el.get("size")),
            rooms=_as_int(el.get("rooms")),
            bathrooms=_as_int(el.get("bathrooms")),
            lat=_as_float(el.get("latitude")),
            lng=_as_float(el.get("longitude")),
            municipality=el.get("municipality", "") or "",
            province=el.get("province", "") or "",
            region=el.get("region", "") or "",
            area_id=area_id,
            property_type=el.get("propertyType", "") or "",
            condition=el.get("status", "") or "",
            photos=_as_int(el.get("numPhotos")) or 0,
            thumbnail=el.get("thumbnail", "") or "",
            raw=el,
        )


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
