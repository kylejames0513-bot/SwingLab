"""CaddieInsight Gear — training-aid products served from a Shopify store.

Entirely environment-driven and safely inert until configured:

    SHOPIFY_STORE_DOMAIN  yourstore.myshopify.com (or the custom domain)

With that unset, ``enabled()`` is False: no Shop link in the navigation, no
/shop page, no gear recommendations on finished analyses. Products, prices,
and images live in Shopify — manage them in the Shopify admin, never in code
(the same rule as Stripe prices in billing.py).

The catalog reads Shopify's public ``swinglab-gear`` collection without an
access token. This keeps an unrelated, stale Admin API token from poisoning a
query that Shopify already exposes to the public storefront, and it keeps the
Pro membership product out of the Gear page.

The shipped CaddieInsight configuration additionally keeps unproven products
out of the app catalog: a product must carry one of three candidate tags *and*
the explicit fulfillment-verification tag.  That source-of-truth evidence is
set in Shopify only after a US sample and supplier/return/tracking review; the
bare-code default remains open for white-label compatibility.

Recommendations: tag a product in Shopify with ``swinglab:<flag>`` — where
<flag> is one of the keys produced by :func:`swinglab.coaching.flag_keys`.
Only available products explicitly matched to a measured issue appear in a
session recommendation.  A clean or unreadable session receives no product
recommendation; the coaching must earn the commerce.  The standalone shop
continues to expose the broader Shopify catalog.

The product list is cached in memory for ``shop.cache_minutes`` so browsing
never hammers the Storefront API, and a Shopify outage degrades to the last
cached list (or an empty shop page) instead of an error.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from typing import Any

from ..config import Config

logger = logging.getLogger("swinglab.web.shop")

# Pin a currently supported schema so a retired version cannot silently fall
# forward to a different contract.
API_VERSION = "2026-07"

_QUERY = """
query CaddieInsightGear {
  collection(handle: "swinglab-gear") {
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
}
"""

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "products": None}


def first_sale_products(products: list[dict], cfg: Config) -> list[dict]:
    """Return only supplier-proven launch aids when the explicit gate is on.

    Product titles, prices, and stock remain Shopify-owned.  Tags merely keep
    the app from promoting a candidate before the operator has real sample
    and fulfillment evidence; they do not claim the product corrects a swing.
    """

    if not bool(cfg.shop.get("first_sale_catalog_only")):
        return products
    verified_tag = str(
        cfg.shop.get("first_sale_verified_tag")
        or "caddieinsight:fulfillment-verified"
    )
    candidate_tags = {
        str(tag)
        for tag in (cfg.shop.get("first_sale_candidate_tags") or ())
        if str(tag).strip()
    }
    if not candidate_tags:
        if products:
            _warn_gate_emptied(products, verified_tag, candidate_tags)
        return []
    kept = [
        product
        for product in products
        if product.get("available") is True
        and verified_tag in set(product.get("tags") or ())
        and candidate_tags.intersection(set(product.get("tags") or ()))
    ]
    if products and not kept:
        _warn_gate_emptied(products, verified_tag, candidate_tags)
    return kept


_gate_warning_state: dict[str, Any] = {"signature": None}


def _warn_gate_emptied(
    products: list[dict], verified_tag: str, candidate_tags: set[str]
) -> None:
    """Say out loud that the gate, not the catalogue, emptied the shop.

    Without this the operator sees the same thing for four different causes:
    a store with nothing in it, an expired Storefront token, products not
    published to the channel, and a stale allowlist. The first three already
    log; this is the fourth, and it is the one that looks most like "we just
    haven't stocked anything yet" — /shop says "check back soon" while a
    dozen live products sit behind a tag nobody applied.

    Logged once per distinct cause rather than per request: this runs on
    every /shop view and every finished analysis, and a per-request warning
    would bury the thing it is trying to surface.
    """
    available = [p for p in products if p.get("available") is True]
    present = set()
    for product in products:
        present.update(product.get("tags") or ())
    missing_verified = verified_tag not in present
    unmatched = sorted(candidate_tags - present)

    signature = f"{len(products)}|{missing_verified}|{','.join(unmatched)}"
    if _gate_warning_state["signature"] == signature:
        return
    _gate_warning_state["signature"] = signature

    reasons = []
    if missing_verified:
        reasons.append(
            f"no product carries the verification tag {verified_tag!r}"
        )
    if unmatched:
        reasons.append(
            "these shop.first_sale_candidate_tags match no product in the "
            f"catalogue: {', '.join(unmatched)}"
        )
    if not reasons:
        reasons.append(
            "no product carries BOTH the verification tag and an allowlisted "
            "candidate tag"
        )
    logger.warning(
        "shop.first_sale_catalog_only filtered %d live product(s) (%d "
        "in stock) down to none, so /shop is empty and no analysis will "
        "recommend gear. Cause: %s. Tag the products you have actually "
        "sample-tested in Shopify, or set shop.first_sale_catalog_only "
        "false to promote the whole catalogue.",
        len(products),
        len(available),
        "; and ".join(reasons),
    )


def enabled() -> bool:
    return bool(os.environ.get("SHOPIFY_STORE_DOMAIN"))


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
            return first_sale_products(_cache["products"], cfg)
    try:
        products = _fetch()
    except Exception:
        # Log the real error, then degrade. Without this a bad/expired
        # token, a missing scope, a rejected API version, or products not
        # published to the token's channel all look identical to an empty
        # catalog — the /shop page just says "restocking" forever.
        logger.exception("Shopify Storefront fetch failed — serving cached/empty catalog.")
        with _cache_lock:
            return first_sale_products(_cache["products"] or [], cfg)
    if not products:
        # A clean call that returns zero products is a DIFFERENT problem
        # (nothing published to this token's sales channel) than an
        # exception — separate them so the operator knows which to fix.
        logger.warning(
            "Shopify Storefront returned 0 products — check the products are "
            "published in the public swinglab-gear collection."
        )
    with _cache_lock:
        _cache.update(at=time.monotonic(), products=products)
    return first_sale_products(products, cfg)


def recommend(
    products: list[dict],
    flags: list[str],
    cfg: Config,
    *,
    limit: int | None = None,
) -> list[dict]:
    """Gear matched to an analysis's flags, round-robin so one flag can't
    crowd out the others.  No measured flag means no recommendation."""
    products = first_sale_products(products, cfg)
    prefix = str(cfg.shop.get("tag_prefix") or "swinglab:")
    max_items = (
        int(limit)
        if limit is not None
        else int(cfg.shop.get("max_recommendations") or 3)
    )

    def tagged(product: dict, flag: str) -> bool:
        return (
            product.get("available") is True
            and (prefix + flag) in set(product.get("tags") or ())
        )

    picks: list[dict] = []
    queues = [[p for p in products if tagged(p, flag)] for flag in flags]
    added = True
    while added and len(picks) < max_items:
        added = False
        for queue in queues:
            while queue:
                product = queue.pop(0)
                if product not in picks:
                    picks.append(product)
                    added = True
                    break
            if len(picks) >= max_items:
                break
    return picks[:max_items]


def _fetch() -> list[dict]:
    domain = (
        os.environ["SHOPIFY_STORE_DOMAIN"]
        .strip()
        .removeprefix("https://")
        .removeprefix("http://")
        .strip("/")
    )
    version = os.environ.get("SHOPIFY_API_VERSION") or API_VERSION
    request = urllib.request.Request(
        f"https://{domain}/api/{version}/graphql.json",
        data=json.dumps({"query": _QUERY}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise RuntimeError(f"Shopify Storefront API error: {body['errors']}")
    collection = (body.get("data") or {}).get("collection")
    if collection is None:
        return []
    return [
        _product(edge["node"], domain)
        for edge in collection["products"]["edges"]
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
