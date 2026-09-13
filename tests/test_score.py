from retirement.modules.property.models import Listing
from retirement.modules.property.score import area_medians, hard_filter, score_listing

WEIGHTS = {
    "price_vs_area_median": 0.30, "size_per_euro": 0.15, "airport_access": 0.15,
    "town_character": 0.20, "condition": 0.10, "rental_potential": 0.10,
}
HARD = {"max_airport_km": 90, "require_photos": True}


def make(id_, price, size=None, lat=40.75, lng=17.239, photos=5, condition="good", desc=""):
    return Listing(
        id=id_, source="test", external_id=id_, price=price, size_sqm=size,
        lat=lat, lng=lng, area_id="valle-ditria", photos=photos,
        condition=condition, description=desc,
    )


def test_area_medians():
    listings = [make("a", 200000, 100), make("b", 300000, 100), make("c", 400000, 100)]
    assert area_medians(listings)["valle-ditria"] == 3000


def test_cheaper_per_sqm_scores_higher():
    listings = [make("a", 150000, 100), make("b", 300000, 100), make("c", 450000, 100)]
    medians = area_medians(listings)
    cheap = score_listing(listings[0], medians, WEIGHTS, HARD)
    dear = score_listing(listings[2], medians, WEIGHTS, HARD)
    assert cheap["components"]["price_vs_area_median"] > dear["components"]["price_vs_area_median"]
    assert cheap["total"] > dear["total"]


def test_llm_assessment_moves_the_total():
    listings = [make("a", 300000, 100)]
    medians = area_medians(listings)
    plain = score_listing(listings[0], medians, WEIGHTS, HARD)
    loved = score_listing(
        listings[0], medians, WEIGHTS, HARD,
        assessment={"character_score": 95, "condition_score": 90,
                    "rental_score": 90, "note": "hilltop old town"},
    )
    assert loved["total"] > plain["total"]
    assert loved["note"] == "hilltop old town"


def test_hard_filter_rejects_no_photos():
    assert hard_filter(make("a", 200000, 100, photos=0), HARD, []) == "no photos"


def test_hard_filter_rejects_remote_property():
    # Middle of Sardinia's interior, far from any airport in the table.
    far = make("a", 200000, 100, lat=40.10, lng=9.35)
    assert "km from" in (hard_filter(far, HARD, []) or "")


def test_hard_filter_rejects_excluded_phrase():
    listing = make("a", 200000, 100, desc="Casa da ristrutturare completamente")
    reason = hard_filter(listing, HARD, ["da ristrutturare completamente"])
    assert reason and "excluded phrase" in reason


def test_scores_stay_in_range():
    listings = [make("a", 100000, 300), make("b", 900000, 50)]
    medians = area_medians(listings)
    for listing in listings:
        result = score_listing(listing, medians, WEIGHTS, HARD)
        assert 0 <= result["total"] <= 100
        for value in result["components"].values():
            assert 0 <= value <= 100


# ── benchmarking against the official market value ─────────────────────────
OMI_LOCOROTONDO = {"source": "omi", "per_sqm": 1425, "low": 900, "high": 2000,
                   "semester": "2018-2", "zones": 2}


def test_omi_replaces_the_listing_stock_as_the_benchmark():
    listings = [make("a", 200000, 100)]          # €2000/m²
    medians = area_medians(listings)             # the stock says €2000 is par
    result = score_listing(listings[0], medians, WEIGHTS, HARD, market=OMI_LOCOROTONDO)

    assert result["benchmark"]["source"] == "omi"
    assert result["benchmark"]["per_sqm"] == 1425
    # €2000 against an official €1425 is over the market, not at it.
    assert result["components"]["price_vs_area_median"] < 50


def test_without_omi_it_falls_back_to_the_listing_stock_and_says_so():
    listings = [make("a", 150000, 100), make("b", 250000, 100)]
    medians = area_medians(listings)
    result = score_listing(listings[0], medians, WEIGHTS, HARD)
    assert result["benchmark"]["source"] == "listings"
    assert result["benchmark"]["per_sqm"] == 2000


def test_the_self_selecting_sample_problem_is_what_omi_fixes():
    """Three overpriced houses make the fourth look like a bargain."""
    pricey = [make(str(i), 300000, 100) for i in range(3)]   # €3000/m² each
    listing = make("d", 250000, 100)                         # €2500/m²
    medians = area_medians(pricey + [listing])

    against_stock = score_listing(listing, medians, WEIGHTS, HARD)
    against_omi = score_listing(listing, medians, WEIGHTS, HARD, market=OMI_LOCOROTONDO)

    # Relative to its neighbours it looks cheap; against the recorded value for
    # the comune it is nearly double. The second answer is the useful one.
    assert against_stock["components"]["price_vs_area_median"] > 50
    assert against_omi["components"]["price_vs_area_median"] == 0


def test_a_listing_with_no_size_has_no_benchmark_but_still_scores():
    listing = make("a", 200000, None)
    result = score_listing(listing, {}, WEIGHTS, HARD, market=OMI_LOCOROTONDO)
    assert result["price_per_sqm"] is None
    assert result["components"]["price_vs_area_median"] == 50.0
    assert 0 <= result["total"] <= 100
