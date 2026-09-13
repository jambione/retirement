"""Turning components into one number without pretending the number is more
than it is.

Three rules:

  * **Absent components are dropped, and their weight is redistributed** over
    the ones that answered. A listing scored on price alone and a listing
    scored on all five are both on a 0-100 scale, so `confidence` reports what
    share of the nominal weight actually had data behind it. Read the two
    together or not at all.
  * **Risk is subtracted afterwards**, capped, so hazard cannot be averaged
    away by good news elsewhere.
  * **Every caveat that applies is carried with the score**, not kept in a
    footnote somewhere else in the codebase. If the band came from the comune
    rather than the zone, the reader sees that next to the number.
"""
from __future__ import annotations

from typing import Any, Sequence

from retirement.modules.value.scoring.components import Component, clamp

DEFAULT_WEIGHTS = {"price": 30.0, "yield": 20.0, "demand": 20.0,
                   "liquidity": 10.0, "amenities": 10.0, "risk_cap": 25.0}

BANDS = ((80, "exceptional"), (65, "strong"), (50, "fair"), (35, "weak"), (0, "poor"))

# A score is only a verdict when it rests on enough to be one.
#
# PRICE IS REQUIRED, not merely weighted. Every other component modifies a
# judgement about the asking price -- a good town, a liquid market and a dry
# hillside change what a price is worth paying, they do not substitute for
# knowing whether the price is high. Without the OMI band there is nothing for
# them to modify, and an arithmetic mean of what is left is a number about the
# PLACE wearing the clothes of a number about the DEAL.
#
# The floor on top of that is ordinary: half the intended weight has to have
# had data behind it. Below either line the arithmetic is still returned, as
# `partial_score`, but `score` is None and the caller shows the reason instead.
REQUIRED_COMPONENTS = ("price",)
MIN_CONFIDENCE = 0.5

# What to do about each gap, in the order they matter.
REMEDIES = {
    "price": "Import the OMI quotations: docs/DATA_SOURCES.md §1, then `./retire value omi --scan`.",
    "yield": "The OMI rent band comes in the same file as the sale band.",
    "demand": "Fetch the comune figures: `./retire value ispra --prov <code> --stats` (free).",
    "liquidity": "Load NTN transaction counts (Agenzia Entrate publishes them per comune, yearly).",
    "amenities": "Tick 'look up amenities', or pass `--osm`, to ask OpenStreetMap for this pin.",
}

# Provinces where the cadastral and transaction record is the Austrian
# tavolare system and OMI volumes are incomplete. Scores here are thinner than
# they look, and say so.
TAVOLARE_PROV = {"TN", "BZ", "GO", "TS"}


def band_label(score: float) -> str:
    return next(label for floor, label in BANDS if score >= floor)


def combine(components: Sequence[Component], risk: Component | None,
            caveats: Sequence[str] = ()) -> dict[str, Any]:
    scored = [c for c in components if c.score is not None and c.weight > 0]
    missing = [c for c in components if c.score is None]
    nominal = sum(c.weight for c in components if c.weight > 0) or 1.0

    notes = list(caveats)
    if not scored:
        return {
            "score": None, "partial_score": None, "band": None, "confidence": 0.0,
            "reportable": False,
            "why_not": "Nothing could be scored: no OMI band and no comune statistics "
                       "for this location.",
            "needs": [REMEDIES["price"], REMEDIES["demand"]],
            "components": [c.as_dict() for c in components],
            "risk": risk.as_dict() if risk else None,
            "caveats": notes + ["Nothing could be scored: no OMI band and no comune "
                                "statistics for this location."],
            "weights_used": {},
        }

    used = sum(c.weight for c in scored)
    base = sum(c.score * c.weight for c in scored) / used
    penalty = risk.score if (risk and risk.score is not None) else 0.0
    final = clamp(base - penalty)

    if missing:
        notes.append(
            "Scored on " + ", ".join(c.label.lower() for c in scored)
            + f"; no data for {', '.join(c.label.lower() for c in missing)}. "
            "Their weight was redistributed over the rest."
        )

    # Is this a verdict, or just arithmetic over whatever happened to be loaded?
    confidence = used / nominal
    absent = {c.key for c in missing}
    missing_required = [key for key in REQUIRED_COMPONENTS if key in absent]
    if missing_required:
        why = ("There is no OMI band for this location, so nothing compares the asking "
               "price to what the zone is worth. The other components describe the "
               "place, not the deal.")
    elif confidence < MIN_CONFIDENCE:
        why = (f"Only {confidence * 100:.0f}% of the intended weight had data behind it "
               f"— below the {MIN_CONFIDENCE * 100:.0f}% this needs to be worth reading "
               "as a verdict.")
    else:
        why = ""
    if risk is None or risk.score is None:
        notes.append("No hazard data loaded — the risk penalty is 0 because nothing "
                     "was checked, not because nothing was found.")

    reportable = not why
    if not reportable:
        notes.insert(0, why)

    return {
        # None until it means something. Every consumer already renders the
        # absent case, so one rule here disables the headline everywhere at once.
        "score": round(final, 1) if reportable else None,
        "partial_score": round(final, 1),
        "band": band_label(final) if reportable else None,
        "reportable": reportable,
        "why_not": why,
        "needs": [REMEDIES[c.key] for c in missing if c.key in REMEDIES],
        "base_before_risk": round(base, 1),
        "risk_penalty": round(penalty, 1),
        "confidence": round(confidence, 2),
        "components": [c.as_dict() for c in components],
        "risk": risk.as_dict() if risk else None,
        "weights_used": {c.key: round(c.weight / used * 100, 1) for c in scored},
        "caveats": notes,
    }


def standing_caveats(band: dict[str, Any], province: str = "") -> list[str]:
    """The ones that are true of the method itself, plus whatever this
    particular lookup had to settle for."""
    notes = [
        "OMI publishes a band for a zone and a typology, not an appraisal of a "
        "building. A house can sit outside its band for good reasons.",
        "Listing prices are asking prices. The Agenzia's own figures are what "
        "was actually paid, and the two differ by a negotiation.",
    ]
    if band.get("grain") == "comune":
        zones = band.get("zones_in_comune") or 0
        notes.append(
            "No OMI zone polygon covered this point, so the band is the whole comune"
            + (f" — {zones} zones averaged together." if zones > 1 else ".")
        )
    if band.get("match") and band["match"] not in ("typology+condition", "none"):
        notes.append(f"Band matched by fallback: {band['match']}.")
    if (province or "").upper() in TAVOLARE_PROV:
        notes.append(
            f"{province.upper()} is a tavolare province: OMI transaction volumes there "
            "are incomplete, so liquidity and trend signals are weaker than elsewhere."
        )
    if band.get("semester"):
        notes.append(f"OMI semester {band['semester']} — revised twice a year.")
    return notes
