"""The value model.

"Best value" is not "cheapest". A listing scores well when it is cheap *for
what it is and where it is*, reachable, in condition you would accept, and
reads like the kind of town in the brief. Each component is 0-100 and the
weights in config/property.yaml decide how much each one matters.

Every component is returned alongside the total so the digest can show why a
house ranked where it did -- a score you cannot interrogate is not useful when
you are deciding where to spend a week next spring.
"""
from __future__ import annotations

from statistics import median
from typing import Any

from retirement.modules.property.geo import nearest_airport
from retirement.modules.property.models import Listing

CONDITION_SCORES = {
    "newdevelopment": 95.0,
    "good": 85.0,
    "renew": 35.0,
    "": 55.0,
}

RENTAL_HINTS = (
    "piscina", "pool", "vista lago", "lake view", "centro storico", "terrazza",
    "giardino", "trullo", "masseria", "mare", "sea view", "vista mare",
)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _lerp_score(value: float, best: float, worst: float) -> float:
    """100 at `best`, 0 at `worst`, linear in between (works either direction)."""
    if best == worst:
        return 50.0
    return _clamp(100.0 * (worst - value) / (worst - best))


def area_medians(listings: list[Listing]) -> dict[str, float]:
    """Median price per m2 for each configured area, from this run's stock."""
    buckets: dict[str, list[float]] = {}
    for item in listings:
        pps = item.price_per_sqm
        if pps:
            buckets.setdefault(item.area_id, []).append(pps)
    return {area: median(values) for area, values in buckets.items() if values}


def score_listing(
    listing: Listing,
    medians: dict[str, float],
    weights: dict[str, float],
    hard: dict[str, Any],
    assessment: dict[str, Any] | None = None,
    size_per_euro_range: tuple[float, float] | None = None,
    market: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assessment = assessment or {}
    components: dict[str, float] = {}

    # 1. Price against the market. OMI's recorded value for the comune when we
    # have it, the current listing stock when we do not -- the stock is a small
    # and self-selecting sample, so it is the fallback rather than the default.
    pps = listing.price_per_sqm
    area_median = medians.get(listing.area_id)
    benchmark: dict[str, Any] | None = None
    if market and market.get("per_sqm"):
        benchmark = {
            "source": "omi", "per_sqm": market["per_sqm"],
            "low": market.get("low"), "high": market.get("high"),
            "semester": market.get("semester"), "zones": market.get("zones"),
        }
    elif area_median:
        benchmark = {"source": "listings", "per_sqm": round(area_median)}

    if pps and benchmark:
        ratio = pps / benchmark["per_sqm"]
        components["price_vs_area_median"] = _lerp_score(ratio, best=0.60, worst=1.40)
    else:
        components["price_vs_area_median"] = 50.0

    # 2. Raw square metres per euro, normalised across the candidate set.
    if listing.price and listing.size_sqm and size_per_euro_range:
        spe = listing.size_sqm / listing.price
        low, high = size_per_euro_range
        components["size_per_euro"] = (
            _clamp(100.0 * (spe - low) / (high - low)) if high > low else 50.0
        )
    else:
        components["size_per_euro"] = 50.0

    # 3. Airport access.
    airport = nearest_airport(listing.lat, listing.lng)
    max_km = float(hard.get("max_airport_km", 90))
    if airport:
        components["airport_access"] = _lerp_score(airport[2], best=20.0, worst=max_km * 1.6)
    else:
        components["airport_access"] = 50.0

    # 4-6. Qualitative: the LLM's read where available, heuristics otherwise.
    components["town_character"] = float(assessment.get("character_score", 50.0))

    if "condition_score" in assessment:
        components["condition"] = float(assessment["condition_score"])
    else:
        components["condition"] = CONDITION_SCORES.get(
            (listing.condition or "").lower(), 55.0
        )

    if "rental_score" in assessment:
        components["rental_potential"] = float(assessment["rental_score"])
    else:
        blob = f"{listing.title} {listing.description}".lower()
        hits = sum(1 for hint in RENTAL_HINTS if hint in blob)
        components["rental_potential"] = _clamp(40.0 + 12.0 * hits)

    total_weight = sum(weights.get(k, 0.0) for k in components) or 1.0
    total = sum(components[k] * weights.get(k, 0.0) for k in components) / total_weight

    return {
        "total": round(total, 1),
        "components": {k: round(v, 1) for k, v in components.items()},
        "airport": (
            {"iata": airport[0], "name": airport[1], "km": round(airport[2])}
            if airport
            else None
        ),
        "price_per_sqm": round(pps) if pps else None,
        "area_median_per_sqm": round(area_median) if area_median else None,
        "benchmark": benchmark,
        "note": assessment.get("note", ""),
        "concerns": assessment.get("concerns", []),
    }


def hard_filter(listing: Listing, hard: dict[str, Any], excludes: list[str]) -> str | None:
    """Returns a reason string when the listing fails, else None."""
    if hard.get("require_photos") and not listing.photos:
        return "no photos"
    max_km = hard.get("max_airport_km")
    if max_km:
        airport = nearest_airport(listing.lat, listing.lng)
        if airport and airport[2] > float(max_km):
            return f"{airport[2]:.0f} km from {airport[0]} (limit {max_km})"
    blob = f"{listing.title} {listing.description}".lower()
    for phrase in excludes or []:
        if phrase.lower() in blob:
            return f"excluded phrase: {phrase}"
    return None
