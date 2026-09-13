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
            "score": None, "band": None, "confidence": 0.0,
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
    if risk is None or risk.score is None:
        notes.append("No hazard data loaded — the risk penalty is 0 because nothing "
                     "was checked, not because nothing was found.")

    return {
        "score": round(final, 1),
        "band": band_label(final),
        "base_before_risk": round(base, 1),
        "risk_penalty": round(penalty, 1),
        "confidence": round(used / nominal, 2),
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
