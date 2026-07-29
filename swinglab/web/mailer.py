"""Outgoing email — standard library only, inert until configured.

Entirely environment-driven, the same rule as Stripe and Shopify:

    RESEND_API_KEY      preferred HTTPS transport (works on hosts that block
                        outbound SMTP, including Railway Hobby)
    SWINGLAB_SMTP_URL   where and how to send, e.g.
                        smtp+starttls://user:pass@smtp.example.com:587
                        Schemes: smtp (plain, e.g. a localhost relay),
                        smtp+starttls (explicit TLS — what most providers
                        want on port 587), smtps (implicit TLS, port 465).
                        Username/password are optional and URL-encoded.
    SWINGLAB_MAIL_FROM  the From address, e.g. "CaddieInsight <no-reply@x.com>"
    SWINGLAB_MAIL_TRANSPORT
                        optional: auto (default), resend, or smtp

Email is enabled when ``SWINGLAB_MAIL_FROM`` and at least one transport are
set. Resend's HTTPS API is preferred when both transports exist. An existing
official Resend SMTP URL is upgraded to HTTPS automatically, so deployments
on SMTP-blocking hosts do not need to copy the embedded API credential into a
second variable. SMTP remains available for other providers and local relays.

No third-party runtime dependency is required.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import unquote, urlsplit

_SCHEMES = {
    "smtp": 25,
    "smtp+starttls": 587,
    "smtps": 465,
}
_RESEND_API_URL = "https://api.resend.com/emails"
_DELIVERY_TIMEOUT_S = 15
_USER_AGENT = "CaddieInsight/1.0"
_TRANSPORTS = {"auto", "resend", "smtp"}


class EmailDeliveryError(RuntimeError):
    """A safe-to-report transport failure with no credentials or email body."""


class EmailDeliveryRejected(EmailDeliveryError):
    """The provider definitively did not accept the message."""


class EmailDeliveryUncertain(EmailDeliveryError):
    """The request may have been accepted before the connection failed."""


def _transport_mode() -> str:
    mode = (
        os.environ.get("SWINGLAB_MAIL_TRANSPORT", "").strip().lower()
        or "auto"
    )
    return mode if mode in _TRANSPORTS else "invalid"


def enabled() -> bool:
    if not os.environ.get("SWINGLAB_MAIL_FROM", "").strip():
        return False
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    smtp_url = os.environ.get("SWINGLAB_SMTP_URL", "").strip()
    # This is intentionally a configuration-intent check, not a connectivity
    # probe. A bad mode or malformed URL must keep inbox verification gated on
    # and fail closed at send time; reporting "disabled" would let a password
    # signup claim Shopify-linked value without proving inbox ownership.
    return bool(api_key or smtp_url)


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


def _resend_key_from_smtp_url(url: str) -> str | None:
    """Return the credential from an official Resend SMTP URL, if present."""
    _, host, _, username, password = _parse_url(url)
    if (
        host.lower() == "smtp.resend.com"
        and (username or "").lower() == "resend"
        and password
    ):
        return password
    return None


def _send_resend(
    api_key: str,
    mail_from: str,
    to: str,
    subject: str,
    body: str,
    html: bool,
) -> None:
    payload = {
        "from": mail_from,
        "to": [to],
        "subject": subject,
        "html" if html else "text": body,
    }
    idempotency_material = "\0".join(
        (mail_from, to, subject, body, "html" if html else "text")
    ).encode("utf-8")
    idempotency_key = "caddie-" + hmac.new(
        api_key.encode("utf-8"), idempotency_material, hashlib.sha256
    ).hexdigest()
    req = urllib_request.Request(
        _RESEND_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
            "Idempotency-Key": idempotency_key,
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=_DELIVERY_TIMEOUT_S) as response:
            status = getattr(response, "status", None) or response.getcode()
            if not 200 <= status < 300:
                error_cls = (
                    EmailDeliveryUncertain
                    if status >= 500 or status in {408, 409}
                    else EmailDeliveryRejected
                )
                raise error_cls(f"Resend returned HTTP {status}.")
    except urllib_error.HTTPError as exc:
        error_cls = (
            EmailDeliveryUncertain
            if exc.code >= 500 or exc.code in {408, 409}
            else EmailDeliveryRejected
        )
        raise error_cls(f"Resend returned HTTP {exc.code}.") from None
    except EmailDeliveryError:
        raise
    except (urllib_error.URLError, TimeoutError, OSError):
        raise EmailDeliveryUncertain(
            "Resend delivery could not be confirmed."
        ) from None


def _send_smtp(
    smtp_url: str,
    mail_from: str,
    to: str,
    subject: str,
    body: str,
    html: bool,
) -> None:
    try:
        scheme, host, port, username, password = _parse_url(smtp_url)
    except ValueError:
        raise EmailDeliveryRejected(
            "SWINGLAB_SMTP_URL is invalid."
        ) from None
    message = EmailMessage()
    message["From"] = mail_from
    message["To"] = to
    message["Subject"] = subject
    if html:
        message.set_content(body, subtype="html")
    else:
        message.set_content(body)

    client_cls = smtplib.SMTP_SSL if scheme == "smtps" else smtplib.SMTP
    stage = "connect"
    try:
        with client_cls(host, port, timeout=_DELIVERY_TIMEOUT_S) as server:
            if scheme == "smtp+starttls":
                server.starttls(context=ssl.create_default_context())
            if username:
                server.login(username, password or "")
            stage = "send"
            server.send_message(message)
            stage = "accepted"
    except (smtplib.SMTPException, OSError) as exc:
        if stage == "accepted":
            # QUIT/connection cleanup failed after send_message received the
            # provider's success response. The message was already accepted.
            return
        if isinstance(
            exc,
            (
                smtplib.SMTPRecipientsRefused,
                smtplib.SMTPSenderRefused,
                smtplib.SMTPResponseException,
            ),
        ):
            raise EmailDeliveryRejected("SMTP rejected the message.") from None
        if stage == "send":
            raise EmailDeliveryUncertain(
                "SMTP delivery could not be confirmed."
            ) from None
        raise EmailDeliveryRejected("SMTP delivery failed.") from None


def send(to: str, subject: str, body: str, html: bool = False) -> None:
    """Send one message, preferring Resend HTTPS and falling back to SMTP."""
    if not enabled():
        raise EmailDeliveryRejected(
            "Email isn't configured — set SWINGLAB_MAIL_FROM and either"
            " RESEND_API_KEY or SWINGLAB_SMTP_URL."
        )
    mail_from = os.environ["SWINGLAB_MAIL_FROM"].strip()
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    smtp_url = os.environ.get("SWINGLAB_SMTP_URL", "").strip()
    mode = _transport_mode()
    if mode == "invalid":
        raise EmailDeliveryRejected(
            "SWINGLAB_MAIL_TRANSPORT must be auto, resend, or smtp."
        )
    if mode == "smtp":
        _send_smtp(smtp_url, mail_from, to, subject, body, html)
        return
    if api_key and mode in {"auto", "resend"}:
        _send_resend(api_key, mail_from, to, subject, body, html)
        return
    try:
        resend_key = (
            _resend_key_from_smtp_url(smtp_url)
            if mode in {"auto", "resend"}
            else None
        )
    except ValueError:
        raise EmailDeliveryRejected(
            "SWINGLAB_SMTP_URL is invalid."
        ) from None
    if resend_key:
        _send_resend(resend_key, mail_from, to, subject, body, html)
        return
    if mode == "resend":
        raise EmailDeliveryRejected(
            "Resend delivery requires RESEND_API_KEY or an official Resend"
            " SWINGLAB_SMTP_URL."
        )
    _send_smtp(
        smtp_url,
        mail_from,
        to,
        subject,
        body,
        html,
    )
