"""The two feeds that need no approval, and the one that needs no verification
of its shape because it refuses to guess."""
import pytest

from retirement.core import db
from retirement.modules.property.sources import alerts, rapidapi


# ── alert emails ───────────────────────────────────────────────────────────
def test_direct_listing_links_are_found():
    found = alerts.extract_listing_urls(
        "New match in Locorotondo: https://www.idealista.it/immobile/12345678/ "
        "and https://www.immobiliare.it/annunci/98765432/"
    )
    assert found == ["https://www.idealista.it/immobile/12345678/",
                     "https://www.immobiliare.it/annunci/98765432/"]


def test_links_hiding_inside_a_click_tracker_are_recovered():
    # Portals almost never link straight to the listing in an alert.
    html = ('<a href="https://click.idealista.it/t?url=https%3A%2F%2Fwww.idealista.it'
            '%2Fimmobile%2F42424242%2F&u=9&s=abc">See the property</a>')
    assert alerts.extract_listing_urls(html) == ["https://www.idealista.it/immobile/42424242/"]


@pytest.mark.parametrize("noise", [
    "https://www.idealista.it/vendita-case/locorotondo/",     # a search page
    "https://www.immobiliare.it/unsubscribe?id=9",            # the footer
    "https://www.idealista.it/en/help/contact/",              # support
    "https://example.com/whatever",                           # not a portal
])
def test_things_that_are_not_listings_are_ignored(noise):
    assert alerts.extract_listing_urls(noise) == []


def test_the_same_listing_twice_in_one_mail_is_queued_once():
    text = ("https://www.idealista.it/immobile/12345678/ ... see photos: "
            "https://www.idealista.it/immobile/12345678/")
    assert alerts.extract_listing_urls(text) == ["https://www.idealista.it/immobile/12345678/"]


def test_the_source_stays_off_until_configured(tmp_path, monkeypatch):
    for key in ("IMAP_HOST", "IMAP_USER", "IMAP_PASS"):
        monkeypatch.delenv(key, raising=False)
    conn = db.connect(tmp_path / "a.sqlite3")

    assert alerts.AlertsSource(conn, {"alerts": {"enabled": True}}).available() is False
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "me")
    monkeypatch.setenv("IMAP_PASS", "x")
    assert alerts.AlertsSource(conn, {"alerts": {"enabled": False}}).available() is False
    assert alerts.AlertsSource(conn, {"alerts": {"enabled": True}}).available() is True


# ── rapidapi: read defensively, refuse to guess ────────────────────────────
@pytest.mark.parametrize("payload", [
    {"elementList": [{"propertyCode": "1"}]},
    {"results": [{"id": "1"}]},
    {"data": {"results": [{"propertyId": "1"}]}},
    [{"code": "1"}],
])
def test_the_listing_array_is_found_wherever_the_wrapper_put_it(payload):
    assert len(rapidapi.find_elements(payload)) == 1


def test_an_unrecognised_shape_names_the_keys_and_points_at_the_probe():
    with pytest.raises(rapidapi.UnrecognisedShape) as err:
        rapidapi.find_elements({"message": "quota exceeded", "status": 429})
    text = str(err.value)
    assert "message" in text and "status" in text     # what it actually got
    assert "probe rapidapi" in text                   # and what to do about it


def test_fields_are_read_through_the_spellings_these_wrappers_use():
    listing = rapidapi.to_listing({
        "propertyCode": "77", "url": "https://x.test/77", "address": "Trullo, Contrada",
        "price": "268000", "size": "142", "rooms": 3, "latitude": 40.75,
        "municipality": "Locorotondo", "numPhotos": 14,
    }, "valle-ditria")
    assert listing.id == "rapidapi:77"
    assert listing.price == 268000.0 and listing.size_sqm == 142.0
    assert listing.municipality == "Locorotondo" and listing.photos == 14
    assert listing.title == "Trullo, Contrada"       # fell through to `address`


def test_a_row_with_no_identifier_is_dropped_rather_than_guessed():
    assert rapidapi.to_listing({"price": 1}, "area") is None


def test_the_source_is_off_without_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    monkeypatch.setenv("RETIREMENT_SECRETS", str(tmp_path / "none.json"))
    assert rapidapi.RapidApiSource(db.connect(tmp_path / "r.sqlite3"), {}).available() is False
