"""What the storefront must be before a dollar of paid traffic reaches it.

Everything here is checked against ``fixtures/store_readiness.json`` — a
snapshot of what ``caddieinsight.com`` actually *serves*, taken by
``scripts/refresh_store_readiness.py`` from public URLs and the public
Storefront API. Not from the Admin API: admin knows what was configured, and a
customer only meets what is served. A policy that exists in admin and 404s in
public is the exact failure this module exists to catch.

These are commerce-trust invariants, not style rules. A refund policy that
promises something the business cannot do is worse than a missing one, because
the missing one only loses a sale while the unworkable one loses an argument
with a card issuer.

Every gap is recorded in a ledger with the reason it is still open and what
would close it. The ledgers ratchet in both directions: a new gap fails the
build, and a gap that gets fixed also fails the build until its entry is
deleted. A waiver list that only grows is a list of excuses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "store_readiness.json"

# Slugs a paid campaign needs resolving before it runs. Shipping and terms are
# not housekeeping: a store that takes money for physical goods has to say
# when they arrive, and one whose membership auto-renews has to say so in
# writing somewhere a customer can find after the fact.
REQUIRED_POLICIES = (
    "refund-policy",
    "privacy-policy",
    "shipping-policy",
    "terms-of-service",
)

# Phrases that commit the business to something it has not built. Matched
# against the served text, lowercased.
UNWORKABLE_PROMISES = {
    "return shipping label": (
        "Promises a prepaid return label. On a $11.99–$34.99 dropshipped "
        "training aid the label can cost more than the item, so this is a "
        "promise the margin cannot honour — and a promise on a live policy "
        "page is what a chargeback is judged against."
    ),
    "free returns": (
        "Same problem, stated more broadly."
    ),
}

# A personal address on a public policy page is both a privacy leak and a
# trust signal pointing the wrong way.
PERSONAL_EMAIL_MARKERS = ("icloud.com", "gmail.com", "outlook.com", "yahoo.com")


# -- known gaps --------------------------------------------------------------

MISSING_POLICIES = {
    "shipping-policy": (
        "Not written. Blocked on real values, not on effort: "
        "docs/first-sale-launch.md forbids promising delivery dates a "
        "supplier has not demonstrated, and no supplier SLA has been "
        "measured yet. The store also ships to the US AND 21 Asian "
        "countries, so the single-region draft in "
        "docs/runbooks/store-manual-actions.md would be wrong if pasted as "
        "is. Needs measured transit times per zone."
    ),
    "terms-of-service": (
        "Not written, and the most legally exposed gap: Pro carries real "
        "recurring selling plans (monthly and yearly), so the store "
        "auto-renews charges with no published terms governing it. Blocked "
        "on values only the operator has — the legal entity name, the "
        "business address to publish (the billing address on file is "
        "residential), and the governing state."
    ),
}

POLICY_TEXT_GAPS = {
    ("refund-policy", "return shipping label"): (
        "The live refund policy is still Shopify's default template and "
        "promises a prepaid return label. Replacement copy is written and "
        "reviewed in docs/runbooks/store-policies.md; applying it needs an "
        "operator paste in Shopify admin, because writing shop policies is "
        "blocked from this session."
    ),
    ("privacy-policy", "personal-email"): (
        "Shopify's stock privacy template renders the shop contact email, "
        "which is still the owner's personal iCloud address. Closing this "
        "means changing the store's contact email in Settings to the business "
        "address — not editing the policy, which only echoes it."
    ),
    ("refund-policy", "personal-email"): (
        "The same default template has a mailto pointing at a personal "
        "iCloud address while the visible link text reads "
        "inquiry@caddieinsight.com — the label and the target disagree, so a "
        "customer clicking 'email us' silently writes to a private inbox. "
        "Fixed in the same replacement copy."
    ),
}

SOLD_OUT_VARIANTS = {
    ("Swing Path Mat", "Outdoor Use"): (
        "Out of stock with inventory policy DENY while the product is live, "
        "so paid traffic can land on a variant it cannot buy. The product "
        "itself still reports availableForSale=true because the Indoor "
        "variant has stock, which is why nothing surfaced this. Needs either "
        "a restock or the variant retired."
    ),
}


# -- fixtures ----------------------------------------------------------------

def snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def policies() -> dict[str, dict]:
    return {policy["slug"]: policy for policy in snapshot()["policies"]}


SERVED = policies()
PRODUCTS = snapshot()["products"]


def text_of(slug: str) -> str:
    return SERVED.get(slug, {}).get("text", "").lower()


def links_of(slug: str) -> list[dict]:
    return SERVED.get(slug, {}).get("links", [])


def addresses_in(slug: str) -> str:
    """Everything a customer could reach: the words AND the link targets.

    Reading only the rendered words is how a mailto pointing somewhere other
    than the address it displays stays invisible.
    """
    hrefs = " ".join(link.get("href", "") for link in links_of(slug))
    return f"{text_of(slug)} {hrefs}".lower()


# -- the policies exist ------------------------------------------------------

@pytest.mark.parametrize("slug", REQUIRED_POLICIES)
def test_every_required_policy_is_served(slug):
    status = SERVED.get(slug, {}).get("status")
    if slug in MISSING_POLICIES:
        assert status != 200, (
            f"/policies/{slug} now resolves. Delete its MISSING_POLICIES "
            "entry so the page is protected from here on."
        )
        return
    assert status == 200, (
        f"/policies/{slug} returns {status}. A paid campaign pointing at this "
        "store needs it to resolve. Write it, or add it to MISSING_POLICIES "
        "with the reason and what unblocks it."
    )


# -- the policies do not overpromise -----------------------------------------

@pytest.mark.parametrize("phrase", sorted(UNWORKABLE_PROMISES))
def test_no_policy_promises_something_the_business_cannot_do(phrase):
    offenders = sorted(
        slug
        for slug in SERVED
        if phrase in text_of(slug) and (slug, phrase) not in POLICY_TEXT_GAPS
    )
    assert not offenders, (
        f"{offenders} promise {phrase!r}. {UNWORKABLE_PROMISES[phrase]} "
        "Rewrite the policy, or record it in POLICY_TEXT_GAPS with the reason."
    )


def test_no_policy_publishes_a_personal_email_address():
    offenders = sorted(
        slug
        for slug in SERVED
        if any(marker in addresses_in(slug) for marker in PERSONAL_EMAIL_MARKERS)
        and (slug, "personal-email") not in POLICY_TEXT_GAPS
    )
    assert not offenders, (
        f"{offenders} expose a personal inbox. Customer-facing policies "
        "should route to the business address, or the gap belongs in "
        "POLICY_TEXT_GAPS."
    )


@pytest.mark.parametrize("key", sorted(POLICY_TEXT_GAPS))
def test_recorded_policy_text_gaps_are_still_real(key):
    """A fixed gap must be deleted from the ledger, not left to rot."""
    slug, marker = key
    if marker == "personal-email":
        still_there = any(m in addresses_in(slug) for m in PERSONAL_EMAIL_MARKERS)
    else:
        still_there = marker in text_of(slug)
    assert still_there, (
        f"POLICY_TEXT_GAPS still lists {marker!r} in /policies/{slug}, but the "
        "served page no longer contains it. Delete the entry."
    )


MISLEADING_MAILTO = {
    ("refund-policy", "inquiry@caddieinsight.com"): (
        "The link reads inquiry@caddieinsight.com and posts to the owner's "
        "personal iCloud address. Fixed by the replacement refund copy in "
        "docs/runbooks/store-policies.md."
    ),
}


@pytest.mark.parametrize("slug", sorted(SERVED))
def test_a_mailto_goes_where_it_says_it_goes(slug):
    """Shown address == address written to.

    This is a stronger claim than "no personal inbox appears", and a different
    one. A policy can name the right address in visible text, pass every
    text-based check, and still send the customer's email somewhere else. The
    gap between the label and the target is the whole defect.
    """
    mismatches = []
    for link in links_of(slug):
        href = link.get("href", "")
        if not href.lower().startswith("mailto:"):
            continue
        target = href.split(":", 1)[1].split("?")[0].strip().lower()
        shown = link.get("text", "").strip().lower()
        if "@" not in shown or shown == target:
            continue
        if (slug, shown) in MISLEADING_MAILTO:
            continue
        mismatches.append(f"shows {shown!r} but mails {target!r}")
    assert not mismatches, (
        f"/policies/{slug}: {mismatches}. A customer told one address and "
        "silently writing to another is a trust defect, not a typo. Fix the "
        "link, or record it in MISLEADING_MAILTO."
    )


@pytest.mark.parametrize("key", sorted(MISLEADING_MAILTO))
def test_recorded_mailto_mismatches_are_still_real(key):
    slug, shown = key
    still_there = any(
        link.get("text", "").strip().lower() == shown
        and link.get("href", "").lower().startswith("mailto:")
        and link["href"].split(":", 1)[1].split("?")[0].strip().lower() != shown
        for link in links_of(slug)
    )
    assert still_there, (
        f"MISLEADING_MAILTO still lists {shown!r} on /policies/{slug}, but the "
        "link now points where it says. Delete the entry."
    )


# -- nothing live is unbuyable -----------------------------------------------

def variant_pairs() -> list[tuple[str, str, bool]]:
    return [
        (product["title"], variant["title"], variant["available"])
        for product in PRODUCTS
        for variant in product["variants"]
    ]


@pytest.mark.parametrize(
    "product_title,variant_title",
    [(p, v) for p, v, _ in variant_pairs()],
)
def test_no_published_variant_is_sold_out(product_title, variant_title):
    available = next(
        available
        for title, variant, available in variant_pairs()
        if (title, variant) == (product_title, variant_title)
    )
    key = (product_title, variant_title)
    if key in SOLD_OUT_VARIANTS:
        assert not available, (
            f"{product_title} / {variant_title} is back in stock. Delete its "
            "SOLD_OUT_VARIANTS entry."
        )
        return
    assert available, (
        f"{product_title} / {variant_title} is published but cannot be "
        "bought, so an ad can land on a dead option. Restock it, retire the "
        "variant, or record it in SOLD_OUT_VARIANTS with the reason."
    )


# -- the ledgers themselves --------------------------------------------------

def test_missing_policy_ledger_only_names_required_policies():
    stray = sorted(set(MISSING_POLICIES) - set(REQUIRED_POLICIES))
    assert not stray, (
        f"MISSING_POLICIES waives {stray}, which nothing requires. Either the "
        "slug is wrong or the waiver is dead weight."
    )


def test_sold_out_ledger_only_names_products_that_exist():
    known = {
        (product["title"], variant["title"])
        for product in PRODUCTS
        for variant in product["variants"]
    }
    stray = sorted(set(SOLD_OUT_VARIANTS) - known)
    assert not stray, (
        f"SOLD_OUT_VARIANTS names {stray}, which the storefront does not "
        "publish. A retired variant's waiver should retire with it."
    )


def test_snapshot_is_not_empty():
    """An empty snapshot would make every assertion above vacuously agree."""
    data = snapshot()
    assert data["products"], "No products in the snapshot — regenerate it."
    assert any(
        policy["status"] == 200 for policy in data["policies"]
    ), "No policy resolved at all — that is an outage, not a store state."
