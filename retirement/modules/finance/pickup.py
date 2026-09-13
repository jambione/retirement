"""Pick up eMoney exports without anyone having to click Import.

Two routes, both optional, both safe to run on a schedule:

  FOLDER   anything dropped in the inbox folder is imported and filed away
           into processed/. Works with a Finder drag, a Downloads rule, an
           iCloud or Dropbox folder synced from another machine.

  MAILBOX  if eMoney can email you a scheduled report, point a dedicated
           mailbox at it and the attachments are pulled by IMAP.

Both are idempotent. Every file is fingerprinted by content hash before it is
parsed, so the same export arriving twice -- re-dropped, re-sent, or sitting in
a folder that syncs -- produces one snapshot, not two. That matters more than
it sounds: the trend chart is built from snapshots, and a duplicated August
would show a flat month that never happened.
"""
from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from retirement.core import secrets
from retirement.core.config import project_root
from retirement.core.db import utcnow
from retirement.modules.finance import importer, store

log = logging.getLogger("retirement.finance.pickup")

SCHEMA = """
CREATE TABLE IF NOT EXISTS finance_imports (
    fingerprint TEXT PRIMARY KEY,      -- sha256 of the file's bytes
    filename    TEXT NOT NULL,
    route       TEXT NOT NULL,         -- folder | mailbox | upload
    imported_at TEXT NOT NULL,
    snapshot_id INTEGER
);
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def already_imported(conn: sqlite3.Connection, digest: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM finance_imports WHERE fingerprint = ?", (digest,)
    ).fetchone() is not None


def record(conn: sqlite3.Connection, digest: str, filename: str, route: str,
           snapshot_id: int | None) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO finance_imports
             (fingerprint, filename, route, imported_at, snapshot_id)
           VALUES (?,?,?,?,?)""",
        (digest, filename, route, utcnow(), snapshot_id),
    )
    conn.commit()


def ingest(conn: sqlite3.Connection, data: bytes, filename: str, route: str,
           config: dict[str, Any]) -> dict[str, Any]:
    """Parse one file and save it, unless we have seen these exact bytes."""
    migrate(conn)
    digest = fingerprint(data)
    if already_imported(conn, digest):
        return {"filename": filename, "status": "duplicate"}
    try:
        parsed = importer.parse(data, filename, config)
    except importer.ImportError_ as exc:
        return {"filename": filename, "status": "unreadable", "error": str(exc)}
    snapshot_id = store.save_snapshot(conn, parsed)
    record(conn, digest, filename, route, snapshot_id)
    return {
        "filename": filename, "status": "imported", "as_of": parsed["as_of"],
        "net_worth": parsed["net_worth"], "warnings": parsed["warnings"],
    }


# ── folder ─────────────────────────────────────────────────────────────────
def _resolve(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else project_root() / path


def folder_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    pickup = config.get("pickup") or {}
    inbox = _resolve(pickup.get("folder", "var/inbox"))
    processed = _resolve(pickup.get("processed", "var/inbox/processed"))
    return inbox, processed


def scan_folder(conn: sqlite3.Connection, config: dict[str, Any]) -> list[dict[str, Any]]:
    pickup = config.get("pickup") or {}
    extensions = tuple(pickup.get("extensions", [".csv", ".tsv", ".xlsx", ".xlsm"]))
    inbox, processed = folder_paths(config)
    inbox.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)

    results = []
    for path in sorted(inbox.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if not path.name.lower().endswith(extensions):
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            results.append({"filename": path.name, "status": "unreadable", "error": str(exc)})
            continue

        result = ingest(conn, data, path.name, "folder", config)
        results.append(result)

        # File it away whatever happened, so the next run does not keep
        # re-reading a file it has already decided about. An unreadable one
        # keeps its name so you can see what went wrong.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        destination = processed / f"{stamp}-{path.name}"
        try:
            shutil.move(str(path), str(destination))
        except OSError as exc:
            log.warning("could not file away %s: %s", path.name, exc)
    return results


# ── mailbox ────────────────────────────────────────────────────────────────
def mailbox_configured() -> bool:
    return secrets.has("imap_host", "imap_user", "imap_pass")


def scan_mailbox(conn: sqlite3.Connection, config: dict[str, Any]) -> list[dict[str, Any]]:
    pickup = (config.get("pickup") or {}).get("mailbox") or {}
    if not pickup.get("enabled") or not mailbox_configured():
        return []

    extensions = tuple(
        (config.get("pickup") or {}).get("extensions", [".csv", ".tsv", ".xlsx", ".xlsm"])
    )
    host = secrets.get("imap_host")
    port = secrets.get_int("imap_port", 993)
    folder = pickup.get("folder") or secrets.get("imap_folder", "INBOX")

    results: list[dict[str, Any]] = []
    try:
        client = imaplib.IMAP4_SSL(host, port, timeout=30)
    except Exception as exc:
        return [{"filename": "-", "status": "mailbox_error", "error": str(exc)}]

    try:
        client.login(secrets.get("imap_user"), secrets.get("imap_pass"))
        client.select(folder)

        criteria = ["UNSEEN"]
        if pickup.get("from_contains"):
            criteria += ["FROM", f'"{pickup["from_contains"]}"']
        if pickup.get("subject_contains"):
            criteria += ["SUBJECT", f'"{pickup["subject_contains"]}"']

        ok, data = client.search(None, *criteria)
        if ok != "OK":
            return [{"filename": "-", "status": "mailbox_error", "error": "search failed"}]

        for message_id in (data[0] or b"").split():
            ok, payload = client.fetch(message_id, "(RFC822)")
            if ok != "OK" or not payload or not payload[0]:
                continue
            message = email.message_from_bytes(payload[0][1])
            found_one = False
            for part in message.walk():
                filename = part.get_filename()
                if not filename or not filename.lower().endswith(extensions):
                    continue
                body = part.get_payload(decode=True)
                if not body:
                    continue
                found_one = True
                results.append(ingest(conn, body, filename, "mailbox", config))
            # Only mark a message read once we have taken something from it;
            # otherwise a mail that arrived before its attachment finished
            # uploading would be silently skipped forever.
            if found_one and pickup.get("mark_seen", True):
                client.store(message_id, "+FLAGS", "\\Seen")
    except Exception as exc:
        results.append({"filename": "-", "status": "mailbox_error", "error": str(exc)})
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return results


def run(conn: sqlite3.Connection, config: dict[str, Any]) -> dict[str, Any]:
    store.migrate(conn)
    results = scan_folder(conn, config) + scan_mailbox(conn, config)
    return {
        "checked": True,
        "imported": [r for r in results if r["status"] == "imported"],
        "duplicates": sum(1 for r in results if r["status"] == "duplicate"),
        "problems": [r for r in results if r["status"] in ("unreadable", "mailbox_error")],
    }
