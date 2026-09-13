"""The individual signals. Each returns a Component, and a Component whose
score is None means "no data" — never zero.

That distinction is the whole design. A zero is a claim: it says this place
scored badly. None says we do not know, and the composite responds by dropping
the component and renormalising the remaining weights, then telling the reader
which components were dropped. Filling a gap with a neutral 50 would quietly
pull every score toward the middle and make a thin-data listing look
comparable to a well-covered one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Component:
    key: str
    label: str
    score: float | None          # 0-100, or None for "no data"
    weight: float
    detail: str = ""
    value: Any = None
    source: str = ""
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "score": self.score,
                "weight": self.weight, "detail": self.detail, "value": self.value,
                "source": self.source, "flags": self.flags}


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def lerp(value: float, worst: float, best: float) -> float:
    """0 at `worst`, 100 at `best`, linear between, clamped outside."""
    if best == worst:
        return 50.0
    return clamp((value - worst) / (best - worst) * 100.0)


# ── A. price against the OMI band ──────────────────────────────────────────
def price(ask: float | None, size_m2: float | None, band: dict[str, Any],
          weight: float = 30.0) -> Component:
    source = band.get("source", "")
    if not ask or not size_m2 or size_m2 <= 0:
        return Component("price", "Price vs OMI band", None, weight,
                         "Needs both an asking price and a floor area.", source=source)
    if not band.get("available") or not band.get("sale_mid"):
        return Component("price", "Price vs OMI band", None, weight,
                         band.get("reason", "No OMI sale band for this location."),
                         source=source)

    per_sqm = ask / size_m2
    mid, low, high = band["sale_mid"], band["sale_min"], band["sale_max"]
    discount = (mid - per_sqm) / mid                       # +ve = below the middle

    flags: list[str] = []
    if low and per_sqm < low:
        flags.append("below_band")
    if high and per_sqm > high:
        flags.append("above_band")

    # -20% (asking a fifth over the zone middle) to +30% (a third under) covers
    # the range where the number is still informative; beyond that it is a
    # question about the property, not about the price.
    score = lerp(discount, -0.20, 0.30)
    detail = (f"€{per_sqm:,.0f}/m² asked against an OMI middle of €{mid:,.0f}/m² "
              f"({'−' if discount < 0 else ''}{abs(discount) * 100:.0f}% "
              f"{'over' if discount < 0 else 'under'}), band €{low:,.0f}–{high:,.0f}.")
    if "below_band" in flags:
        detail += (" Under the bottom of the band — that is either a real discount "
                   "or something the listing is not saying.")
    return Component("price", "Price vs OMI band", round(score, 1), weight, detail,
                     {"per_sqm": round(per_sqm), "omi_mid": mid, "omi_min": low,
                      "omi_max": high, "discount_pct": round(discount * 100, 1)},
                     source, flags)


# ── B. gross yield ─────────────────────────────────────────────────────────
def gross_yield(ask: float | None, size_m2: float | None, band: dict[str, Any],
                asking_rent_month: float | None = None, weight: float = 20.0) -> Component:
    """OMI rent bands are €/m² per MONTH. Twelve of them make a year."""
    source = band.get("source", "")
    if not ask or ask <= 0:
        return Component("yield", "Gross rental yield", None, weight,
                         "Needs an asking price.", source=source)

    if asking_rent_month:
        annual = asking_rent_month * 12
        basis = "the rent in the listing"
    else:
        if not band.get("rent_mid") or not size_m2:
            return Component(
                "yield", "Gross rental yield", None, weight,
                "No OMI rent band for this zone" if not band.get("rent_mid")
                else "Needs a floor area.", source=source)
        annual = band["rent_mid"] * size_m2 * 12
        basis = f"OMI rent middle €{band['rent_mid']:.1f}/m²/month"

    yield_pct = annual / ask * 100
    # 2% is a bad Italian residential yield, 9% is an excellent one before
    # costs; IMU, agency and vacancy all come off the top of whatever this says.
    score = lerp(yield_pct, 2.0, 9.0)
    return Component("yield", "Gross rental yield", round(score, 1), weight,
                     f"{yield_pct:.1f}% gross from {basis} — before IMU, agency, "
                     "vacancy and maintenance.",
                     {"gross_yield_pct": round(yield_pct, 2),
                      "annual_rent_eur": round(annual), "basis": basis},
                     source)


# ── C. demand: who lives there, who visits, what they earn ─────────────────
def demand(stats: dict[str, dict[str, Any]], weight: float = 20.0) -> Component:
    """Four sub-signals, each optional. The component is the mean of whichever
    are present, and the detail says which ones those were."""
    parts: list[tuple[str, float, str]] = []
    sources: set[str] = set()

    pop, pop_2011 = stats.get("population"), stats.get("population_2011")
    if pop and pop_2011 and (pop_2011.get("value") or 0) > 0:
        change = (pop["value"] - pop_2011["value"]) / pop_2011["value"] * 100
        parts.append(("population", lerp(change, -15.0, 10.0),
                      f"population {change:+.1f}% 2011→2021"))
        sources.add(pop.get("source", ""))

    young = stats.get("pop_young_pct")
    if young and young.get("value") is not None:
        parts.append(("age mix", lerp(young["value"], 8.0, 18.0),
                      f"{young['value']:.1f}% under 15"))
        sources.add(young.get("source", ""))

    income = stats.get("income_avg")
    if income and income.get("value"):
        parts.append(("income", lerp(income["value"], 12000.0, 30000.0),
                      f"average declared income €{income['value']:,.0f}"))
        sources.add(income.get("source", ""))

    beds, population = stats.get("tourism_beds"), stats.get("population")
    if beds and beds.get("value") and population and population.get("value"):
        per_100 = beds["value"] / population["value"] * 100
        parts.append(("tourism", lerp(per_100, 1.0, 40.0),
                      f"{per_100:.0f} tourist beds per 100 residents"))
        sources.add(beds.get("source", ""))

    if not parts:
        return Component("demand", "Demand & demographics", None, weight,
                         "No comune statistics loaded for this place.")
    score = sum(p[1] for p in parts) / len(parts)
    return Component("demand", "Demand & demographics", round(score, 1), weight,
                     "; ".join(p[2] for p in parts) + ".",
                     {name: round(value, 1) for name, value, _ in parts},
                     " · ".join(sorted(s for s in sources if s)))


# ── D. liquidity ───────────────────────────────────────────────────────────
def liquidity(stats: dict[str, dict[str, Any]], weight: float = 10.0) -> Component:
    """NTN — the Agenzia's count of normalised transactions — is the only
    honest measure of how often anything sells here. Without it this component
    is absent: population is a proxy for size, not for turnover, and dressing
    one up as the other is how a dead market scores as a liquid one."""
    ntn, stock = stats.get("ntn"), stats.get("dwellings")
    if not ntn or ntn.get("value") is None:
        return Component("liquidity", "Market liquidity", None, weight,
                         "No NTN transaction count loaded for this comune "
                         "(Agenzia Entrate publishes it per comune, yearly).")
    if stock and stock.get("value"):
        rate = ntn["value"] / stock["value"] * 100
        return Component("liquidity", "Market liquidity", round(lerp(rate, 0.3, 2.5), 1),
                         weight, f"{ntn['value']:.0f} sales against {stock['value']:,.0f} "
                                 f"dwellings — {rate:.2f}% of stock turning over in the year.",
                         {"ntn": ntn["value"], "turnover_pct": round(rate, 2)},
                         ntn.get("source", ""))
    return Component("liquidity", "Market liquidity", round(lerp(ntn["value"], 20, 400), 1),
                     weight, f"{ntn['value']:.0f} normalised transactions in the year "
                             "(no dwelling count loaded, so this is volume, not turnover).",
                     {"ntn": ntn["value"]}, ntn.get("source", ""))


# ── E. amenities ───────────────────────────────────────────────────────────
# what matters, and the distance at which it stops mattering
AMENITY_SCALE = {
    "supermarket": (0.4, 8.0), "pharmacy": (0.4, 10.0), "school": (0.5, 12.0),
    "restaurant": (0.3, 8.0), "hospital": (3.0, 35.0), "station": (1.0, 25.0),
}


def amenities(distances: dict[str, Any] | None, weight: float = 10.0) -> Component:
    if not distances:
        return Component("amenities", "What is within reach", None, weight,
                         "No OpenStreetMap lookup for this point yet.",
                         source="© OpenStreetMap contributors")
    parts, described = [], []
    for kind, (best, worst) in AMENITY_SCALE.items():
        item = distances.get(kind)
        if not item or item.get("km") is None:
            continue
        parts.append(lerp(item["km"], worst, best))
        described.append(f"{kind} {item['km']:.1f} km")
    if not parts:
        return Component("amenities", "What is within reach", None, weight,
                         "Nothing of the kinds we look for inside the search radius.",
                         source="© OpenStreetMap contributors")
    return Component("amenities", "What is within reach",
                     round(sum(parts) / len(parts), 1), weight,
                     ", ".join(described) + " (straight line, not drive time).",
                     {k: v.get("km") for k, v in distances.items()},
                     "© OpenStreetMap contributors")


# ── F. risk, as a penalty rather than a score ──────────────────────────────
def risk_penalty(stats: dict[str, dict[str, Any]], cap: float = 25.0) -> Component:
    """Hazard subtracts from the composite instead of averaging into it: a
    place can be cheap, liquid, popular AND on a landslide, and averaging lets
    the good news hide the bad."""
    penalty, reasons, sources = 0.0, [], set()

    flood = stats.get("flood_area_p3_pct")
    if flood and flood.get("value") is not None:
        contribution = min(10.0, flood["value"] / 2.0)     # 20% of the comune at P3 = full 10
        if contribution >= 0.5:
            reasons.append(f"{flood['value']:.1f}% of the comune in a high flood hazard area")
        penalty += contribution
        sources.add(flood.get("source", ""))

    slide = stats.get("landslide_area_p3p4_pct")
    if slide and slide.get("value") is not None:
        contribution = min(10.0, slide["value"] / 2.0)
        if contribution >= 0.5:
            reasons.append(f"{slide['value']:.1f}% in a high/very high landslide hazard area")
        penalty += contribution
        sources.add(slide.get("source", ""))

    seismic = stats.get("seismic_zone")
    if seismic and seismic.get("value"):
        zone = int(seismic["value"])
        contribution = {1: 5.0, 2: 2.5, 3: 1.0, 4: 0.0}.get(zone, 0.0)
        if contribution:
            reasons.append(f"seismic zone {zone}")
        penalty += contribution
        sources.add(seismic.get("source", ""))

    if not sources:
        return Component("risk", "Hazard penalty", None, cap,
                         "No hazard data loaded for this comune — this is not the "
                         "same as no hazard.")
    penalty = round(min(cap, penalty), 1)
    detail = ("; ".join(reasons) + "." if reasons
              else "Nothing above the reporting threshold for flood, landslide or seismic hazard.")
    return Component("risk", "Hazard penalty", penalty, cap, detail,
                     {"penalty_points": penalty},
                     " · ".join(sorted(s for s in sources if s)))
