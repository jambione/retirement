"""Email delivery for module digests."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from retirement.core.config import env, env_int


class EmailNotConfigured(RuntimeError):
    pass


def configured() -> bool:
    return all(env(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "DIGEST_TO"))


def send(subject: str, html: str, text: str = "") -> None:
    if not configured():
        raise EmailNotConfigured(
            "Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD and DIGEST_TO in .env"
        )
    sender = env("DIGEST_FROM") or env("SMTP_USER")
    recipients = [addr.strip() for addr in (env("DIGEST_TO") or "").split(",") if addr.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(text or "This digest is best viewed as HTML.")
    msg.add_alternative(html, subtype="html")

    host = env("SMTP_HOST")
    port = env_int("SMTP_PORT", 587)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(env("SMTP_USER"), env("SMTP_PASSWORD"))
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            server.login(env("SMTP_USER"), env("SMTP_PASSWORD"))
            server.send_message(msg)
