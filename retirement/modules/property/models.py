"""The one shape every source has to produce."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Listing(BaseModel):
    id: str                      # "{source}:{external_id}"
    source: str
    external_id: str
    url: str = ""
    title: str = ""
    description: str = ""
    price: float | None = None
    currency: str = "EUR"
    size_sqm: float | None = None
    rooms: int | None = None
    bathrooms: int | None = None
    lat: float | None = None
    lng: float | None = None
    municipality: str = ""
    province: str = ""
    region: str = ""
    area_id: str = ""            # which configured search area found it
    property_type: str = ""
    condition: str = ""          # "good", "renew", "newdevelopment", ...
    photos: int = 0
    thumbnail: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def price_per_sqm(self) -> float | None:
        if self.price and self.size_sqm and self.size_sqm > 0:
            return self.price / self.size_sqm
        return None
