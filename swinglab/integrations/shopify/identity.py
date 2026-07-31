"""Canonical Shopify customer and store identity helpers.

Shopify's Admin GraphQL API returns global IDs such as
``gid://shopify/Customer/123`` while the existing webhook payloads and
CaddieInsight database use the numeric customer ID.  Keep one representation
at rest and translate only at integration boundaries.  Store domains use one
normalizer across the inbound webhook and outbound Admin bridges so a split
store configuration cannot be hidden by formatting differences.
"""

from __future__ import annotations

import re


_CUSTOMER_ID = re.compile(r"^(?:gid://shopify/Customer/)?([0-9]+)$")


def normalize_shop_domain(value: object | None) -> str | None:
    """Return a normalized Shopify hostname without accepting a path.

    This intentionally permits a custom storefront hostname for the inbound
    bridge.  The outbound Admin client applies its stricter
    ``*.myshopify.com`` rule separately, and the sync coordinator requires the
    two normalized values to be exactly equal before any outbound work.
    """

    domain = (
        str(value or "")
        .strip()
        .lower()
        .removeprefix("https://")
        .removeprefix("http://")
        .strip("/")
    )
    if not domain or "/" in domain or len(domain) > 255:
        return None
    return domain


def normalize_customer_id(value: object | None) -> str | None:
    """Return the canonical decimal customer ID.

    ``None`` and blank strings represent no linked customer.  Decimal IDs and
    Shopify Customer GIDs are accepted; all other resource types and opaque
    values are rejected without echoing the supplied value into the error.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Invalid Shopify customer ID.")
    raw = str(value).strip()
    if not raw:
        return None
    matched = _CUSTOMER_ID.fullmatch(raw)
    if matched is None:
        raise ValueError("Invalid Shopify customer ID.")
    customer_id = int(matched.group(1))
    if customer_id <= 0:
        raise ValueError("Invalid Shopify customer ID.")
    return str(customer_id)


def customer_gid(value: object | None) -> str | None:
    """Return a Shopify Customer GID for a supported customer ID."""

    customer_id = normalize_customer_id(value)
    if customer_id is None:
        return None
    return f"gid://shopify/Customer/{customer_id}"
