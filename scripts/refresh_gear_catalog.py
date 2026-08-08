#!/usr/bin/env python3
"""Refresh the committed snapshot of the store's gear catalogue.

``tests/fixtures/gear_catalog.json`` is the source of truth for
``tests/test_gear_coverage.py``, which checks that every drill's ``gear_tag``
reaches a product the app is allowed to recommend. Tests cannot call Shopify,
so the catalogue has to be committed — and a committed catalogue goes stale
unless refreshing it is one command.

Run it after any change to product tags, availability, or the
``swinglab-gear`` collection::

    SHOPIFY_STORE_DOMAIN=caddieinsight.com python scripts/refresh_gear_catalog.py

then commit the diff. If the diff opens a coverage gap the test will say so.

This deliberately goes through :func:`swinglab.web.shop._fetch` — the exact
call the running app makes, against the same public collection, with no Admin
API token. A snapshot taken any other way could disagree with what customers
are served, which would make the coverage test worse than useless.

Only the fields the recommendation path actually reads are stored (``title``,
``tags``, ``available``). Prices, images and descriptions are Shopify-owned
and churn; keeping them here would bury a tag change in noise.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swinglab.web import shop  # noqa: E402

SNAPSHOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "gear_catalog.json"


def main() -> int:
    domain = os.environ.get("SHOPIFY_STORE_DOMAIN")
    if not domain:
        print(
            "SHOPIFY_STORE_DOMAIN is unset — set it to the storefront domain "
            "(e.g. caddieinsight.com) and re-run.",
            file=sys.stderr,
        )
        return 2

    products = shop._fetch()
    if not products:
        # Zero products is never a legitimate snapshot: it would silently
        # blank the catalogue the coverage test checks against, turning a
        # Shopify outage into a green build with no gear in it.
        print(
            "The swinglab-gear collection returned 0 products. Refusing to "
            "write an empty snapshot — check the collection is published to "
            "the Online Store channel.",
            file=sys.stderr,
        )
        return 1

    payload = {
        "source": f"https://{domain}/api/{shop.API_VERSION}/graphql.json",
        "collection": "swinglab-gear",
        "note": (
            "Regenerate with scripts/refresh_gear_catalog.py — do not hand-edit. "
            "Only fields the recommendation path reads are stored."
        ),
        "products": sorted(
            (
                {
                    "title": product["title"],
                    "tags": sorted(product["tags"]),
                    "available": product["available"],
                }
                for product in products
            ),
            key=lambda product: product["title"],
        ),
    }
    SNAPSHOT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(payload['products'])} products to {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
