"""Outgoing email over SMTP — standard library only, inert until configured.

Entirely environment-driven, the same rule as Stripe and Shopify:

    SWINGLAB_SMTP_URL   where and how to send, e.g.
                        smtp+starttls://user:pass@smtp.example.com:587
                        Schemes: smtp (plain, e.g. a localhost relay),
                        smtp+starttls (explicit TLS — what most providers
                        want on port 587), smtps (implicit TLS, port 465).
                        Username/password are optional and URL-encoded.
    SWINGLAB_MAIL_FROM  the From address, e.g. "SwingLab <no-reply@x.com>"

With either unset, ``enabled()`` is False and every caller keeps its
no-email behavior: account claims work without verification (exactly as
before) and password reset is unavailable. When both are set, claiming an
email that already has value attached requires a 6-digit emailed code, and
password reset works from the login page — see app.py and users.py for the
code storage (hashed, 10-minute expiry, single-use, rate-limited).

Only smtplib and email.message are used — no new dependencies.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import unquote, urlsplit

_SCHEMES = {
    "smtp": 25,
    "smtp+starttls": 587,
    "smtps": 465,
}


def enabled() -> bool:
    return bool(
        os.environ.get("SWINGLAB_SMTP_URL") and os.environ.get("SWINGLAB_MAIL_FROM")
    )


def _parse_url(url: str) -> tuple[str, str, int, str | None, str | None]:
    """SWINGLAB_SMTP_URL -> (scheme, host, port, username, password)."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _SCHEMES:
        raise ValueError(
            f"SWINGLAB_SMTP_URL scheme must be one of {sorted(_SCHEMES)},"
            f" got {scheme!r}"
        )
    host = parts.hostname
    if not host:
        raise ValueError("SWINGLAB_SMTP_URL is missing a host")
    port = parts.port or _SCHEMES[scheme]
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None
    return scheme, host, port, username, password


def send(to: str, subject: str, body: str) -> None:
    """Send one plain-text message. Raises RuntimeError when email isn't
    configured — callers must check ``enabled()`` and keep their no-email
    behavior instead of calling blind."""
    if not enabled():
        raise RuntimeError(
            "Email isn't configured — set SWINGLAB_SMTP_URL and"
            " SWINGLAB_MAIL_FROM."
        )
    scheme, host, port, username, password = _parse_url(
        os.environ["SWINGLAB_SMTP_URL"]
    )
    message = EmailMessage()
    message["From"] = os.environ["SWINGLAB_MAIL_FROM"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    client_cls = smtplib.SMTP_SSL if scheme == "smtps" else smtplib.SMTP
    with client_cls(host, port, timeout=30) as server:
        if scheme == "smtp+starttls":
            server.starttls(context=ssl.create_default_context())
        if username:
            server.login(username, password or "")
        server.send_message(message)
