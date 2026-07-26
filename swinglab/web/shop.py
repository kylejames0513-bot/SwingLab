"""CaddieInsight Gear — training-aid products served from a Shopify store.

Entirely environment-driven and safely inert until configured:

    SHOPIFY_STORE_DOMAIN      yourstore.myshopify.com (or the custom domain)
    SHOPIFY_STOREFRONT_TOKEN  Storefront API access token (read-only, safe to
                              keep on the server; it can only see published
                              catalog data)

With those unset, ``enabled()`` is False: no Shop link in the navigation, no
/shop page, no gear recommendations on finished analyses. Products, prices,
and images live in Shopify — manage them in the Shopify admin, never in code
(the same rule as Stripe prices in billing.py).

Recommendations: tag a product in Shopify with ``swinglab:<flag>`` — where
<flag> is one of the keys produced by :func:`swinglab.coaching.flag_keys`
(``tempo``, ``sway``, ``hip-slide``, ``consistency``) — and it is suggested
whenever an analysis raises that flag. Products tagged ``swinglab:general``
pad out the list (and are what a flag-free swing sees).

The product list is cached in memory for ``shop.cache_minutes`` so browsing
never hammers the Storefront API, and a Shopify outage degrades to the last
cached list (or an empty shop page) instead of an error.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Any

from ..config import Config

# Requesting an unsupported version falls back to the oldest supported one on
# Shopify's side, so a pinned version keeps working after it is sunset.
API_VERSION = "2025-07"

GENERAL_FLAG = "general"

_QUERY = """
{
  products(first: 50, sortKey: BEST_SELLING) {
    edges {
      node {
        title
        handle
        description
        tags
        availableForSale
        onlineStoreUrl
        featuredImage { url altText }
        priceRange { minVariantPrice { amount currencyCode } }
      }
    }
  }
}
"""

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "products": None}


def enabled() -> bool:
    return bool(
        os.environ.get("SHOPIFY_STORE_DOMAIN")
        and os.environ.get("SHOPIFY_STOREFRONT_TOKEN")
    )


def clear_cache() -> None:
    with _cache_lock:
        _cache.update(at=0.0, products=None)


def fetch_products(cfg: Config) -> list[dict]:
    """The store's published products, newest cache first.

    Never raises: a Storefront API failure serves the last cached list, or an
    empty list before the first success — the shop degrades, the app doesn't.
    """
    ttl = float(cfg.shop.get("cache_minutes") or 0) * 60
    with _cache_lock:
        fresh = _cache["products"] is not None and time.monotonic() - _cache["at"] < ttl
        if fresh:
            return _cache["products"]
    try:
        products = _fetch()
    except Exception:
        with _cache_lock:
            return _cache["products"] or []
    with _cache_lock:
        _cache.update(at=time.monotonic(), products=products)
    return products


def recommend(products: list[dict], flags: list[str], cfg: Config) -> list[dict]:
    """Gear matched to an analysis's flags, round-robin so one flag can't
    crowd out the others, padded with ``general`` items up to the limit."""
    prefix = str(cfg.shop.get("tag_prefix") or "swinglab:")
    limit = int(cfg.shop.get("max_recommendations") or 3)

    def tagged(product: dict, flag: str) -> bool:
        return (prefix + flag) in product["tags"]

    picks: list[dict] = []
    queues = [[p for p in products if tagged(p, flag)] for flag in flags]
    added = True
    while added and len(picks) < limit:
        added = False
        for queue in queues:
            while queue:
                product = queue.pop(0)
                if product not in picks:
                    picks.append(product)
                    added = True
                    break
            if len(picks) >= limit:
                break
    for product in products:
        if len(picks) >= limit:
            break
        if tagged(product, GENERAL_FLAG) and product not in picks:
            picks.append(product)
    return picks[:limit]


def _fetch() -> list[dict]:
    domain = (
        os.environ["SHOPIFY_STORE_DOMAIN"]
        .strip()
        .removeprefix("https://")
        .removeprefix("http://")
        .strip("/")
    )
    version = os.environ.get("SHOPIFY_API_VERSION") or API_VERSION
    token = os.environ["SHOPIFY_STOREFRONT_TOKEN"].strip()
    # Classic public tokens (bare hex) use the public header; the newer
    # private tokens Shopify's admin issues for custom apps (atkn_/shpat_
    # prefixes) authenticate through their own header instead.
    if token.startswith(("atkn_", "shpat_")):
        auth_header = {"Shopify-Storefront-Private-Token": token}
    else:
        auth_header = {"X-Shopify-Storefront-Access-Token": token}
    request = urllib.request.Request(
        f"https://{domain}/api/{version}/graphql.json",
        data=json.dumps({"query": _QUERY}).encode("utf-8"),
        headers={"Content-Type": "application/json", **auth_header},
    )
    with urllib.request.urlopen(request, timeout=10) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise RuntimeError(f"Shopify Storefront API error: {body['errors']}")
    return [
        _product(edge["node"], domain)
        for edge in body["data"]["products"]["edges"]
    ]


def _product(node: dict, domain: str) -> dict:
    price = node["priceRange"]["minVariantPrice"]
    amount = float(price["amount"])
    currency = price["currencyCode"]
    image = node.get("featuredImage") or {}
    description = " ".join((node.get("description") or "").split())
    if len(description) > 140:
        description = description[:139].rstrip() + "…"
    return {
        "title": node["title"],
        # onlineStoreUrl is null until the product is published to the Online
        # Store channel; the canonical /products/<handle> URL works either way.
        "url": node.get("onlineStoreUrl")
        or f"https://{domain}/products/{node['handle']}",
        "price_display": (
            f"${amount:,.2f}" if currency == "USD" else f"{amount:,.2f} {currency}"
        ),
        "image": image.get("url"),
        "image_alt": image.get("altText"),
        "description": description,
        "tags": set(node.get("tags") or []),
        "available": bool(node.get("availableForSale", True)),
    }
