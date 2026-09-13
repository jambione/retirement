"""End-to-end: fake source in, ranked shortlist and rendered digest out."""
import pytest

from retirement.core import db

from retirement.modules.property import pipeline
from retirement.modules.property.models import Listing
from retirement.modules.property.sources.base import Source

CONFIG = {
    "prompt": "small town, authentic, near an airport",
    "budget": {"min": 100000, "max": 500000},
    "property": {"types": ["homes"], "operation": "sale", "exclude_keywords": ["rudere"]},
    "areas": [{"id": "valle-ditria", "label": "Valle d'Itria",
               "center": [40.75, 17.239], "radius_km": 30}],
    "scoring": {
        "weights": {"price_vs_area_median": 0.30, "size_per_euro": 0.15,
                    "airport_access": 0.15, "town_character": 0.20,
                    "condition": 0.10, "rental_potential": 0.10},
        "hard_filters": {"max_airport_km": 90, "require_photos": True},
    },
    "digest": {"max_listings": 5, "include_near_misses": True, "only_send_if_new": True},
}


def make(id_, price, size, photos=4, desc="", lat=40.75, lng=17.239):
    return Listing(
        id=f"fake:{id_}", source="fake", external_id=id_, url=f"https://x.test/{id_}",
        title=f"House {id_}", description=desc, price=price, size_sqm=size,
        lat=lat, lng=lng, municipality="Locorotondo", area_id="valle-ditria",
        photos=photos, condition="good",
    )


class FakeSource(Source):
    name = "fake"
    payload: list[Listing] = []

    def fetch(self, area):
        return list(self.payload)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("RETIREMENT_DB", str(tmp_path / "t.sqlite3"))
    return db.connect(tmp_path / "t.sqlite3")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Neither real source may be constructed during these tests."""
    monkeypatch.setattr(pipeline, "IdealistaSource", lambda conn, cfg: FakeSource(conn, cfg))
    monkeypatch.setattr(pipeline, "ManualSource", _NullManual)


class _NullManual(Source):
    name = "manual"

    def migrate(self):
        pass

    def fetch(self, area):
        return []


def test_run_ranks_and_persists(conn, monkeypatch):
    FakeSource.payload = [
        make("cheap", 180000, 150),          # best value per m2
        make("mid", 300000, 120),
        make("dear", 480000, 100),
        make("nophotos", 200000, 130, photos=0),
        make("ruin", 150000, 200, desc="Antico rudere da recuperare"),
    ]
    module = pipeline.PropertyModule(CONFIG, conn)
    summary = module.run(dry_run=True)

    assert summary["scanned"] == 5
    assert summary["new"] == 5
    assert summary["rejected"] == 2            # no photos + excluded phrase
    assert summary["email"] == "skipped (dry run)"

    from retirement.modules.property import store

    shortlist = store.latest_shortlist(conn)
    assert [item["id"] for item in shortlist][0] == "fake:cheap"
    assert len(shortlist) == 3
    assert shortlist[0]["detail"]["airport"]["iata"] == "BRI"


def test_second_run_sees_nothing_new_and_catches_a_price_drop(conn):
    FakeSource.payload = [make("a", 300000, 120)]
    module = pipeline.PropertyModule(CONFIG, conn)
    module.run(dry_run=True)

    FakeSource.payload = [make("a", 270000, 120)]
    summary = module.run(dry_run=True)
    assert summary["new"] == 0
    assert summary["price_drops"] == 1


def test_listing_that_disappears_is_marked_gone(conn):
    FakeSource.payload = [make("a", 300000, 120), make("b", 310000, 120)]
    module = pipeline.PropertyModule(CONFIG, conn)
    module.run(dry_run=True)

    FakeSource.payload = [make("a", 300000, 120)]
    summary = module.run(dry_run=True)
    assert summary["gone"] == 1


def test_digest_renders_without_an_llm(conn):
    from retirement.modules.property import digest, store

    FakeSource.payload = [make("a", 200000, 140), make("b", 400000, 90)]
    pipeline.PropertyModule(CONFIG, conn).run(dry_run=True)

    shortlist = store.latest_shortlist(conn)
    subject, html, text = digest.render(shortlist, [], {"scanned": 2, "areas": 1})
    assert "Italy property" in subject
    assert "Locorotondo" in html
    assert html.count("class=\"card\"") == 2
    assert "https://x.test/a" in text


# ── the second score, from the official record ─────────────────────────────
def test_run_also_scores_against_the_official_record(conn):
    """The nightly cycle writes both scores. With nothing imported the value
    score is honestly absent — and the run says so rather than printing a
    number nothing stands behind."""
    from retirement.modules.property import store
    from retirement.modules.value import store as value_store

    FakeSource.payload = [make("a", 180000, 150)]
    summary = pipeline.PropertyModule(CONFIG, conn).run(dry_run=True)

    assert summary["value_scored"] == 0 and summary["value_no_data"] == 1
    assert "DATA_SOURCES" in summary["warnings_value"]
    # a breakdown is still stored, so the page can explain the absence
    assert value_store.coverage(conn)["scored"] == 1
    assert store.latest_shortlist(conn)[0]["value"]["score"] is None


def test_comune_figures_alone_do_not_make_a_verdict(conn):
    from retirement.modules.property import store
    from retirement.modules.value import store as value_store

    value_store.migrate(conn)
    value_store.put_extent(conn, "072026", [[17.20, 40.68], [17.40, 40.82]], "test")
    conn.execute("INSERT INTO comuni (istat, name, name_key, prov) "
                 "VALUES ('072026','Locorotondo','LOCOROTONDO','BA')")
    value_store.put_stats(conn, [
        {"istat": "072026", "metric": "population", "year": "2021", "value": 14000,
         "source": "ISPRA — IdroGEO"},
        {"istat": "072026", "metric": "population_2011", "year": "2011", "value": 14100,
         "source": "ISPRA — IdroGEO"},
        {"istat": "072026", "metric": "flood_area_p3_pct", "year": "", "value": 0.4,
         "source": "ISPRA — IdroGEO"},
    ])
    conn.commit()

    FakeSource.payload = [make("a", 180000, 150)]
    summary = pipeline.PropertyModule(CONFIG, conn).run(dry_run=True)

    # Demand and hazard now have real figures, but the OMI band does not, so
    # there is nothing comparing the asking price to what the zone is worth.
    # The arithmetic is kept; the verdict is withheld and explains itself.
    assert summary["value_scored"] == 0 and summary["value_no_data"] == 1
    item = store.latest_shortlist(conn)[0]
    assert item["value"]["score"] is None
    assert item["value"]["partial_score"] is not None
    assert "no OMI band" in item["value"]["why_not"]
    assert "Price vs OMI band" in item["value"]["missing"]


def test_digest_prints_both_scores(conn):
    from retirement.modules.property import digest

    card = digest._card({
        "title": "House a", "url": "https://x.test/a", "price": 180000,
        "size_sqm": 150, "municipality": "Locorotondo",
        "detail": {"total": 71.2, "components": {}},
        "value": {"score": 64.0, "band": "fair", "confidence": 0.7},
    })
    assert "official 64" in card and "70% of weight had data" in card

    absent = digest._card({
        "title": "House b", "detail": {"total": 50.0},
        "value": {"score": None, "band": None, "confidence": 0.0},
    })
    assert "no official data" in absent
