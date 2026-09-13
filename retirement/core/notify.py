"""Outbound email, modelled on trading-helper's email_service.

Deliberately the same configuration shape as the trading desk — secrets.json
or environment, the same key names — so SMTP is set up once the same way in
both projects. What is NOT copied is the trading-specific message templates;
each project writes its own, and this module only knows how to deliver one.

stdlib only.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from retirement.core import secrets

log = logging.getLogger("retirement.notify")

FROM_NAME = "Brasfield Retirement"


class EmailNotConfigured(RuntimeError):
    pass


def _to() -> list[str]:
    raw = secrets.get("digest_to") or secrets.get("notify_to")
    return [address.strip() for address in raw.split(",") if address.strip()]


def _bcc() -> list[str]:
    raw = secrets.get("digest_bcc") or secrets.get("notify_bcc")
    return [address.strip() for address in raw.split(",") if address.strip()]


def configured() -> bool:
    return bool(secrets.has("smtp_host", "smtp_user", "smtp_pass") and _to())


def status() -> dict:
    """Safe diagnostics for the UI and `./retire status` — never a password."""
    return {
        "configured": configured(),
        "smtp_host": secrets.get("smtp_host") or None,
        "smtp_port": secrets.get_int("smtp_port", 587),
        "smtp_user": secrets.get("smtp_user") or None,
        "smtp_from": secrets.get("smtp_from") or secrets.get("smtp_user") or None,
        "digest_to": _to(),
        "digest_bcc": _bcc() or None,
        "has_password": bool(secrets.get("smtp_pass")),
        "secrets_file": str(secrets.secrets_path()),
    }


def send(subject: str, html: str, text: str = "") -> None:
    """Raises EmailNotConfigured or the underlying SMTP error.

    The pipeline catches and records it in the run summary rather than failing
    the scan -- a digest that could not be delivered is worth knowing about,
    but it is not a reason to throw away an hour of scored listings.
    """
    if not configured():
        raise EmailNotConfigured(
            "Set smtp_host, smtp_user, smtp_pass and digest_to in "
            f"{secrets.secrets_path()} or in .env"
        )

    host = secrets.get("smtp_host")
    port = secrets.get_int("smtp_port", 587)
    user = secrets.get("smtp_user")
    password = secrets.get("smtp_pass")
    sender = secrets.get("smtp_from") or user
    recipients = _to()
    everyone = recipients + _bcc()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{sender}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text or "This digest is best viewed as HTML.", "plain"))
    msg.attach(MIMEText(html, "html"))

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as server:
            server.login(user, password)
            server.sendmail(sender, everyone, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(user, password)
            server.sendmail(sender, everyone, msg.as_string())
    log.info("digest sent to %s", ", ".join(everyone))
