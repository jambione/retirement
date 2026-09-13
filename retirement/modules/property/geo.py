"""Distance helpers. No API, no key, no rate limit -- just arithmetic."""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

# Italian airports with scheduled international service, plus the handful of
# domestic ones that matter for onward connections. (IATA, name, lat, lng)
AIRPORTS: list[tuple[str, str, float, float]] = [
    ("MXP", "Milan Malpensa", 45.6306, 8.7281),
    ("LIN", "Milan Linate", 45.4451, 9.2767),
    ("BGY", "Milan Bergamo", 45.6739, 9.7042),
    ("TRN", "Turin", 45.2008, 7.6496),
    ("GOA", "Genoa", 44.4133, 8.8375),
    ("VRN", "Verona", 45.3957, 10.8885),
    ("VCE", "Venice Marco Polo", 45.5053, 12.3519),
    ("TSF", "Treviso", 45.6484, 12.1944),
    ("TRS", "Trieste", 45.8275, 13.4722),
    ("BLQ", "Bologna", 44.5354, 11.2887),
    ("PMF", "Parma", 44.8245, 10.2964),
    ("RMI", "Rimini", 44.0203, 12.6117),
    ("FLR", "Florence", 43.8100, 11.2051),
    ("PSA", "Pisa", 43.6839, 10.3927),
    ("PEG", "Perugia", 43.0959, 12.5132),
    ("AOI", "Ancona", 43.6163, 13.3623),
    ("PSR", "Pescara", 42.4317, 14.1811),
    ("FCO", "Rome Fiumicino", 41.8003, 12.2389),
    ("CIA", "Rome Ciampino", 41.7994, 12.5949),
    ("NAP", "Naples", 40.8860, 14.2908),
    ("BRI", "Bari", 41.1389, 16.7606),
    ("BDS", "Brindisi", 40.6576, 17.9470),
    ("SUF", "Lamezia Terme", 38.9054, 16.2423),
    ("REG", "Reggio Calabria", 38.0712, 15.6516),
    ("CTA", "Catania", 37.4668, 15.0664),
    ("PMO", "Palermo", 38.1759, 13.0910),
    ("TPS", "Trapani", 37.9114, 12.4880),
    ("CAG", "Cagliari", 39.2515, 9.0543),
    ("AHO", "Alghero", 40.6321, 8.2908),
    ("OLB", "Olbia", 40.8987, 9.5176),
]

# Straight-line km underestimates driving. In hilly Italian terrain roughly
# 1.3x is a fair rule of thumb; it is a ranking signal, not a routing answer.
ROAD_FACTOR = 1.3


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1, lng1, lat2, lng2 = map(radians, (lat1, lng1, lat2, lng2))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(a))


def nearest_airport(lat: float | None, lng: float | None) -> tuple[str, str, float] | None:
    """(iata, name, approximate road km) for the closest airport, or None."""
    if lat is None or lng is None:
        return None
    best: tuple[str, str, float] | None = None
    for iata, name, alat, alng in AIRPORTS:
        km = haversine_km(lat, lng, alat, alng) * ROAD_FACTOR
        if best is None or km < best[2]:
            best = (iata, name, km)
    return best
