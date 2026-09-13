from retirement.modules.property.geo import haversine_km, nearest_airport


def test_haversine_known_distance():
    # Rome to Milan is about 477 km as the crow flies.
    km = haversine_km(41.9028, 12.4964, 45.4642, 9.1900)
    assert 460 < km < 500


def test_nearest_airport_puglia():
    # Locorotondo, Valle d'Itria -> Bari, not Brindisi.
    iata, _, km = nearest_airport(40.7500, 17.2390)
    assert iata == "BRI"
    assert 60 < km < 110


def test_nearest_airport_garda():
    iata, _, km = nearest_airport(45.4400, 10.5300)
    assert iata == "VRN"
    assert km < 60


def test_nearest_airport_handles_missing_coords():
    assert nearest_airport(None, None) is None
