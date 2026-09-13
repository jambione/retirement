"""The pickup runs unattended on a schedule, so the thing that matters most is
that it never imports the same export twice and never gets stuck on one bad
file."""
import pytest

from retirement.core import db
from retirement.modules.finance import pickup, store

CONFIG_BASE = {
    "currency": "USD",
    "liability_hints": ["mortgage", "credit card"],
    "categories": {"cash": ["checking"], "liabilities": ["mortgage"]},
}

EXPORT = (b"Balances as of 08/31/2026\n"
          b"Account,Type,Balance\n"
          b'Joint Checking,Checking,"24,310.00"\n'
          b'Mortgage,Mortgage,"311,250.00"\n')

LATER = (b"Balances as of 09/30/2026\n"
         b"Account,Type,Balance\n"
         b'Joint Checking,Checking,"25,900.00"\n')


@pytest.fixture
def setup(tmp_path):
    conn = db.connect(tmp_path / "p.sqlite3")
    store.migrate(conn)
    pickup.migrate(conn)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    config = {**CONFIG_BASE, "pickup": {
        "folder": str(inbox), "processed": str(tmp_path / "done"),
        "extensions": [".csv", ".xlsx"],
    }}
    return conn, inbox, tmp_path / "done", config


def test_a_dropped_file_is_imported_and_filed_away(setup):
    conn, inbox, processed, config = setup
    (inbox / "networth.csv").write_bytes(EXPORT)

    result = pickup.run(conn, config)
    assert len(result["imported"]) == 1
    assert result["imported"][0]["as_of"] == "2026-08-31"
    assert store.latest(conn)["net_worth"] == pytest.approx(-286940.0)

    assert not list(inbox.glob("*.csv"))            # gone from the inbox
    assert len(list(processed.glob("*networth.csv"))) == 1


def test_the_same_export_twice_makes_one_snapshot(setup):
    conn, inbox, _processed, config = setup
    (inbox / "networth.csv").write_bytes(EXPORT)
    pickup.run(conn, config)

    # Re-dropped under a different name -- identical bytes.
    (inbox / "networth-copy.csv").write_bytes(EXPORT)
    second = pickup.run(conn, config)

    assert second["imported"] == []
    assert second["duplicates"] == 1
    assert len(store.history(conn)) == 1


def test_a_genuinely_newer_export_does_import(setup):
    conn, inbox, _processed, config = setup
    (inbox / "aug.csv").write_bytes(EXPORT)
    pickup.run(conn, config)
    (inbox / "sep.csv").write_bytes(LATER)
    pickup.run(conn, config)

    assert [h["as_of"] for h in store.history(conn)] == ["2026-08-31", "2026-09-30"]


def test_an_unreadable_file_is_reported_and_moved_out_of_the_way(setup):
    conn, inbox, processed, config = setup
    (inbox / "notes.csv").write_bytes(b"no table in here")

    result = pickup.run(conn, config)
    assert result["imported"] == []
    assert result["problems"][0]["status"] == "unreadable"
    # Filed away, so the next run does not keep re-reading it.
    assert not list(inbox.glob("*.csv"))
    assert len(list(processed.glob("*notes.csv"))) == 1


def test_files_of_other_types_are_left_alone(setup):
    conn, inbox, _processed, config = setup
    (inbox / "statement.pdf").write_bytes(b"%PDF-1.4 ...")

    result = pickup.run(conn, config)
    assert result["imported"] == [] and result["problems"] == []
    assert (inbox / "statement.pdf").exists()      # not ours, not touched


def test_dotfiles_are_ignored(setup):
    conn, inbox, _processed, config = setup
    (inbox / ".DS_Store.csv").write_bytes(EXPORT)
    assert pickup.run(conn, config)["imported"] == []


def test_an_empty_inbox_is_a_quiet_no_op(setup):
    conn, _inbox, _processed, config = setup
    result = pickup.run(conn, config)
    assert result == {"checked": True, "imported": [], "duplicates": 0, "problems": []}


def test_the_mailbox_route_stays_off_until_it_is_switched_on(setup, monkeypatch):
    conn, _inbox, _processed, config = setup
    for key in ("IMAP_HOST", "IMAP_USER", "IMAP_PASS"):
        monkeypatch.delenv(key, raising=False)

    config["pickup"]["mailbox"] = {"enabled": True}     # on, but no credentials
    assert pickup.scan_mailbox(conn, config) == []

    config["pickup"]["mailbox"] = {"enabled": False}
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "me")
    monkeypatch.setenv("IMAP_PASS", "x")
    assert pickup.scan_mailbox(conn, config) == []      # credentials, but off


def test_folder_paths_are_created_relative_to_the_project(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "q.sqlite3")
    store.migrate(conn)
    config = {**CONFIG_BASE, "pickup": {"folder": str(tmp_path / "in"),
                                        "processed": str(tmp_path / "out")}}
    pickup.run(conn, config)
    assert (tmp_path / "in").is_dir() and (tmp_path / "out").is_dir()
