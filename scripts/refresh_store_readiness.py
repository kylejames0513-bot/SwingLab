#!/usr/bin/env python3
"""Refresh the committed snapshot of the storefront's ad-readiness surface.

``tests/fixtures/store_readiness.json`` is what ``tests/test_store_ad_readiness.py``
checks. It records two public things:

* every policy page a paid campaign needs, and whether it actually resolves;
* every published product's variants, and whether they can be bought.

Both are read from surfaces a customer can reach — the rendered
``/policies/*`` pages and the public Storefront API — with no Admin API token.
That is deliberate. The Admin API knows what the operator *configured*; a
customer only ever meets what the storefront *serves*, and it is the second
one that a Facebook reviewer, a chargeback, or a regulator looks at. A policy
that exists in admin but 404s in public is exactly the failure this is here to
catch, and an admin-side audit could not see it.

Run it after any policy edit, product change, or theme publish::

    python scripts/refresh_store_readiness.py --domain caddieinsight.com

then commit the diff. Anything that regresses fails the build.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

SNAPSHOT = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "store_readiness.json"
)

API_VERSION = "2026-07"

# Every policy slug Shopify can serve. Which of these a store is REQUIRED to
# have is a judgement encoded in the test, not here — this script only records
# what is there, so the snapshot stays a measurement rather than an opinion.
POLICY_SLUGS = (
    "refund-policy",
    "shipping-policy",
    "terms-of-service",
    "terms-of-sale",
    "privacy-policy",
    "subscription-policy",
    "contact-information",
    "legal-notice",
)

_PRODUCTS = """
query Readiness {
  products(first: 100) {
    edges {
      node {
        title
        handle
        availableForSale
        variants(first: 25) {
          edges {
            node { title availableForSale currentlyNotInStock }
          }
        }
      }
    }
  }
}
"""


def strip_html(markup: str) -> str:
    """Rendered page -> the words a customer actually reads."""
    without_blocks = re.sub(
        r"<(script|style)\b.*?</\1>", " ", markup, flags=re.S | re.I
    )
    text = re.sub(r"<[^>]+>", " ", without_blocks)
    return " ".join(html.unescape(text).split())


def extract_links(markup: str) -> list[dict]:
    """Anchor text paired with its target.

    Stripping a page to its words loses the one thing a deceptive link is made
    of. A mailto whose visible text reads inquiry@caddieinsight.com and whose
    href points at a private inbox is invisible to any text-only audit, and it
    is precisely the defect worth catching — the customer is told one address
    and writes to another.
    """
    links = []
    for href, inner in re.findall(
        r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        markup,
        flags=re.S | re.I,
    ):
        links.append(
            {"href": html.unescape(href).strip(), "text": strip_html(inner)}
        )
    return links


def fetch_policy(domain: str, slug: str) -> dict:
    url = f"https://{domain}/policies/{slug}"
    request = urllib.request.Request(url, headers={"User-Agent": "caddieinsight-readiness"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
            return {
                "slug": slug,
                "status": response.status,
                "text": strip_html(body),
                "links": extract_links(body),
            }
    except urllib.error.HTTPError as error:
        # A 404 is a finding, not a failure: it is how a missing policy looks
        # to a customer and to an ad reviewer.
        return {"slug": slug, "status": error.code, "text": "", "links": []}


def fetch_products(domain: str) -> list[dict]:
    request = urllib.request.Request(
        f"https://{domain}/api/{API_VERSION}/graphql.json",
        data=json.dumps({"query": _PRODUCTS}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError(f"Storefront API error: {payload['errors']}")
    products = []
    for edge in payload["data"]["products"]["edges"]:
        node = edge["node"]
        products.append(
            {
                "title": node["title"],
                "handle": node["handle"],
                "available": bool(node["availableForSale"]),
                "variants": [
                    {
                        "title": variant["node"]["title"],
                        "available": bool(variant["node"]["availableForSale"]),
                    }
                    for variant in node["variants"]["edges"]
                ],
            }
        )
    return sorted(products, key=lambda product: product["title"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="caddieinsight.com")
    args = parser.parse_args()

    products = fetch_products(args.domain)
    if not products:
        print(
            "The Storefront API returned 0 products. Refusing to write an "
            "empty snapshot — an outage committed as 'nothing to check' would "
            "turn every assertion green.",
            file=sys.stderr,
        )
        return 1

    payload = {
        "domain": args.domain,
        "note": (
            "Regenerate with scripts/refresh_store_readiness.py — do not "
            "hand-edit. Read from public surfaces only: what the storefront "
            "serves, not what the admin holds."
        ),
        "policies": [fetch_policy(args.domain, slug) for slug in POLICY_SLUGS],
        "products": products,
    }
    SNAPSHOT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    served = sum(1 for p in payload["policies"] if p["status"] == 200)
    print(
        f"Wrote {served}/{len(POLICY_SLUGS)} served policies and "
        f"{len(products)} products to {SNAPSHOT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
