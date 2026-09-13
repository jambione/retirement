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
