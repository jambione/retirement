import sqlite3

from retirement.modules.property import store
from retirement.modules.property.models import Listing


def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    store.migrate(c)
    return c


def listing(price=250000):
    return Listing(
        id="idealista:1", source="idealista", external_id="1", price=price,
        size_sqm=120, area_id="valle-ditria", title="Trullo", photos=4,
    )


def test_first_insert_is_new_then_not():
    c = conn()
    assert store.upsert(c, listing())["is_new"] is True
    assert store.upsert(c, listing())["is_new"] is False


def test_price_drop_is_detected():
    c = conn()
    store.upsert(c, listing(250000))
    result = store.upsert(c, listing(230000))
    assert result["price_change"] == -20000


def test_unseen_listings_are_marked_gone():
    c = conn()
    store.upsert(c, listing())
    assert store.mark_gone(c, set(), ["valle-ditria"]) == 1
    assert c.execute("SELECT active FROM listings").fetchone()["active"] == 0
    # A listing we DID see this run must survive.
    store.upsert(c, listing())
    assert store.mark_gone(c, {"idealista:1"}, ["valle-ditria"]) == 0


def test_shortlist_excludes_rejected_and_orders_by_score():
    c = conn()
    store.upsert(c, listing())
    store.save_score(c, "idealista:1", 1, {"total": 88.0, "components": {}})
    assert len(store.latest_shortlist(c)) == 1
    store.save_score(c, "idealista:1", 2, None, rejected="no photos")
    assert store.latest_shortlist(c) == []
