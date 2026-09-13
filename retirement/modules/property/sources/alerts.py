"""Saved-search alert emails as a listing feed.

Every Italian portal offers the same thing: save a search on the site, and it
emails you new matches. That is the portals' own intended mechanism, so this
needs no API key, no approval and no scraping -- and after Idealista's
developer form goes unanswered, it is the feed that actually exists.

What it does: poll a mailbox, pull listing URLs out of whatever the portals
sent, and queue them for the manual source to fetch and score. Coverage becomes
"whatever your saved searches match", which for three named areas in one price
band is most of what an API would have given you.

Two details that decide whether this works in practice:

  * Portals wrap links in click-tracking redirects, so the real listing URL is
    often sitting URL-encoded inside a query parameter. Both forms are read.
  * A message is only marked read once a URL has been taken from it. A digest
    that arrives while the mailbox is being polled, or one whose format changes,
    is then retried rather than silently consumed.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import sqlite3
from email.header import decode_header, make_header
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from retirement.core import secrets
from retirement.modules.property.models import Listing
from retirement.modules.property.sources.base import Source
from retirement.modules.property.sources.manual import ManualSource

log = logging.getLogger("retirement.property.alerts")

# One pattern per portal. Deliberately anchored on the listing path so a link
# to a search results page or an unsubscribe footer is not mistaken for a house.
PORTALS: dict[str, re.Pattern[str]] = {
    "idealista": re.compile(r"https?://(?:www\.)?idealista\.it/(?:en/)?immobile/\d+/?", re.I),
    "immobiliare": re.compile(r"https?://(?:www\.)?immobiliare\.it/annunci/\d+/?", re.I),
    "casa": re.compile(r"https?://(?:www\.)?casa\.it/immobili/\d+[\w.-]*", re.I),
    "gate-away": re.compile(r"https?://(?:www\.)?gate-away\.com/[\w-]*propert[\w/-]*\d+[\w/-]*", re.I),
}

ANY_URL = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)


def extract_listing_urls(text: str) -> list[str]:
    """Direct links first, then anything hiding inside a tracking redirect."""
    found: list[str] = []

    def keep(url: str) -> None:
        cleaned = url.rstrip(".,);'\"")
        for pattern in PORTALS.values():
            match = pattern.match(cleaned) or pattern.search(cleaned)
            if match and match.group(0) not in found:
                found.append(match.group(0))
                return

    for url in ANY_URL.findall(text or ""):
        keep(unquote(url))
        # click.idealista.it/...?url=https%3A%2F%2Fwww.idealista.it%2Fimmobile%2F123
        try:
            query = parse_qs(urlparse(url).query)
        except ValueError:
            continue
        for values in query.values():
            for value in values:
                if "http" in value:
                    keep(unquote(value))
    return found


def _body_text(message: email.message.Message) -> str:
    """Both halves of a multipart alert — some portals put the links only in
    the HTML part, some only in the plain-text one."""
    chunks: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() != "text":
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if payload:
            chunks.append(payload.decode(part.get_content_charset() or "utf-8",
                                         errors="replace"))
    return "\n".join(chunks)


class AlertsSource(Source):
    """Not a fetcher: it fills the manual queue, which does the fetching."""

    name = "alerts"

    def available(self) -> bool:
        cfg = (self.config.get("alerts") or {})
        return bool(cfg.get("enabled")) and secrets.has("imap_host", "imap_user", "imap_pass")

    def fetch(self, area: dict[str, Any]) -> list[Listing]:
        _ = area
        self.collect()
        return []          # the manual source picks the queue up in the same run

    def collect(self) -> dict[str, Any]:
        cfg = (self.config.get("alerts") or {})
        if not self.available():
            return {"queued": 0, "messages": 0, "skipped": "not configured"}

        host = secrets.get("imap_host")
        port = secrets.get_int("imap_port", 993)
        folder = cfg.get("folder") or secrets.get("imap_folder", "INBOX")
        senders = cfg.get("from_contains") or list(PORTALS)
        manual = ManualSource(self.conn, self.config)
        manual.migrate()

        queued, seen_messages, problems = 0, 0, []
        try:
            client = imaplib.IMAP4_SSL(host, port, timeout=30)
        except Exception as exc:
            return {"queued": 0, "messages": 0, "error": str(exc)}

        try:
            client.login(secrets.get("imap_user"), secrets.get("imap_pass"))
            client.select(folder)
            for sender in senders:
                ok, data = client.search(None, "UNSEEN", "FROM", f'"{sender}"')
                if ok != "OK":
                    continue
                for message_id in (data[0] or b"").split():
                    ok, payload = client.fetch(message_id, "(RFC822)")
                    if ok != "OK" or not payload or not payload[0]:
                        continue
                    message = email.message_from_bytes(payload[0][1])
                    subject = str(make_header(decode_header(message.get("Subject", ""))))
                    urls = extract_listing_urls(_body_text(message))
                    seen_messages += 1
                    for url in urls:
                        manual.add(url, cfg.get("area_id", "alerts"))
                        queued += 1
                    if urls and cfg.get("mark_seen", True):
                        client.store(message_id, "+FLAGS", "\\Seen")
                    elif not urls:
                        # Left unread on purpose: a format change should be
                        # visible in the mailbox, not silently consumed.
                        problems.append(subject[:80] or "(no subject)")
        except Exception as exc:
            problems.append(str(exc))
        finally:
            try:
                client.logout()
            except Exception:
                pass

        return {"queued": queued, "messages": seen_messages, "no_links": problems}
