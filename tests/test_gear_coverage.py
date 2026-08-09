"""Every drill's ``gear_tag`` must reach a product the store can sell.

The flywheel the product is built on — measure, name one priority, prescribe
one drill, sell the aid that drill needs, re-film against the pass mark — has
a seam at the tag layer. :mod:`swinglab.drills` gives each drill a
``gear_tag``; :mod:`swinglab.web.shop` matches that tag to Shopify products.
Nothing connected the two, so a drill could prescribe a tag no product
carries and the report would simply go quiet at the exact moment the coaching
got specific. That failure is silent by construction: no error, no empty
state, just a missing recommendation nobody counted.

This module counts. It checks the drill library against
``fixtures/gear_catalog.json`` — a committed snapshot of the live
``swinglab-gear`` collection, refreshed by ``scripts/refresh_gear_catalog.py``.

Coverage is checked at two layers, because a tag can fail at either and the
fixes are completely different:

1. **Stocked** — does an available product in the collection carry the tag at
   all? A gap here is a *sourcing* problem.
2. **Recommendable** — does :func:`swinglab.web.shop.recommend`, running the
   shipped ``config.yaml``, actually return that product? A gap here is a
   *configuration* problem: ``shop.first_sale_catalog_only`` additionally
   requires a fulfillment-verification tag and membership of an operator
   allowlist, so a correctly tagged, in-stock product can still be withheld.

Both layers ratchet in both directions. A tag that loses coverage fails. A
tag listed as a known gap that *regains* coverage also fails, so the ledgers
below shrink as the holes are closed instead of quietly outliving them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swinglab.config import Config
from swinglab.drills import build_drills
from swinglab.web import shop

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO / "tests" / "fixtures" / "gear_catalog.json"
SHIPPED_CONFIG = REPO / "config.yaml"

# swinglab:general is a shop-browsing tag, not a coaching flag — no measured
# issue produces it, so recommend() is never called with it. It still has to
# be stocked, because the maintenance drills name it and the /shop page and
# the report's "Matched training aids" link both surface the collection.
NOT_A_COACHING_FLAG = "swinglab:general"


# -- known gaps -------------------------------------------------------------
#
# Each entry is a gap that exists in the live store today, with the reason it
# exists. An entry is a record of a decision, not permission to add another:
# a new uncovered tag fails the build, and closing a gap listed here also
# fails the build until the entry is deleted.

UNSTOCKED = {
    "swinglab:sway": (
        "No anti-sway aid in the live collection. The Alignment Stick Set and "
        "the Full-Length Swing Mirror both carried swinglab:sway and were "
        "archived in the 2026-08-03 CaddieInsight restock, which replaced the "
        "SwingLab-era dropship candidates with branded CI-* SKUs. Sourcing "
        "decision — see docs/runbooks/gear-coverage.md."
    ),
    "swinglab:hip-slide": (
        "Same restock: the Anti-Sway Hip Resistance Band carried "
        "swinglab:hip-slide and is archived. No replacement stocked."
    ),
    "swinglab:head-dip": (
        "Never stocked. The chair and head-window drills are deliberately "
        "gear-free; a posture mirror is the aid that would serve them."
    ),
    "swinglab:balance": (
        "Never stocked. Feet-together and hold-the-finish need no equipment, "
        "so this has always been a category with drills and no product."
    ),
    "swinglab:sequence": (
        "New category — the downswing-sequence drills arrived with the "
        "kinematic-sequence wiring and the store has never carried gear for "
        "them. An impact bag or a weighted club is the aid the pump drill is "
        "built around; neither is stocked. Both drills work with no equipment "
        "at all, so this is a sourcing opportunity rather than a broken "
        "prescription."
    ),
}

UNRECOMMENDABLE = {
    tag: (
        "Blocked by the shop.first_sale_catalog_only gate, not by tagging. "
        "The shipped allowlist (shop.first_sale_candidate_tags) names three "
        "products — the clip-on swing metronome, the anti-sway hip resistance "
        "band and the alignment-stick set — that were all archived in the "
        "2026-08-03 restock, and no live product carries "
        "shop.first_sale_verified_tag. So the gate filters the entire "
        "catalogue to nothing and the app recommends no gear for any measured "
        "flag. Closing this needs an operator decision about which current "
        "SKUs hold real fulfillment evidence — see "
        "docs/runbooks/gear-coverage.md."
    )
    for tag in (
        "swinglab:tempo",
        "swinglab:consistency",
        "swinglab:arm-extension",
        "swinglab:sway",
        "swinglab:hip-slide",
        "swinglab:head-dip",
        "swinglab:balance",
        "swinglab:sequence",
    )
}


# -- fixtures ---------------------------------------------------------------

def shipped_config() -> Config:
    """The config the deployed app runs, not the permissive code default.

    Loading config.yaml is the whole point: shop.first_sale_catalog_only
    defaults to False in swinglab/config.py and is True as shipped, so a test
    built on Config() would exercise a gate the running app does not have.
    """
    return Config.load(SHIPPED_CONFIG)


def catalogue() -> list[dict]:
    """The snapshot, rehydrated into the shape the shop functions read."""
    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return [
        {
            "title": product["title"],
            "tags": set(product["tags"]),
            "available": product["available"],
        }
        for product in payload["products"]
    ]


def drill_gear_tags() -> dict[str, list[str]]:
    """Every gear_tag the drill library can prescribe -> the drills using it."""
    drills = build_drills(shipped_config().coaching)
    tags: dict[str, list[str]] = {}
    for drill_set in drills.values():
        for drill in drill_set:
            tags.setdefault(drill.gear_tag, []).append(drill.name)
    return tags


GEAR_TAGS = drill_gear_tags()
COACHING_TAGS = sorted(tag for tag in GEAR_TAGS if tag != NOT_A_COACHING_FLAG)
ALL_TAGS = sorted(GEAR_TAGS)


def stocked_for(tag: str) -> list[str]:
    """Titles of available products carrying ``tag``, ignoring the sale gate."""
    return [
        product["title"]
        for product in catalogue()
        if product["available"] and tag in product["tags"]
    ]


def recommended_for(tag: str) -> list[str]:
    """Titles the app would actually recommend for ``tag``'s coaching flag."""
    flag = tag.split(":", 1)[1]
    return [
        product["title"]
        for product in shop.recommend(catalogue(), [flag], shipped_config())
    ]


# -- layer 1: is the tag stocked at all? ------------------------------------

@pytest.mark.parametrize("tag", ALL_TAGS)
def test_every_drill_gear_tag_is_stocked(tag):
    titles = stocked_for(tag)
    drills = GEAR_TAGS[tag]
    if tag in UNSTOCKED:
        assert not titles, (
            f"{tag} is listed in UNSTOCKED but {titles} now carries it. "
            "The gap has closed — delete the UNSTOCKED entry so the tag is "
            "protected from here on."
        )
        return
    assert titles, (
        f"No available product in the swinglab-gear collection carries {tag}, "
        f"so the {len(drills)} drill(s) that prescribe it "
        f"({', '.join(drills)}) recommend nothing. Either tag/stock a "
        f"product and re-run scripts/refresh_gear_catalog.py, or add {tag!r} "
        "to UNSTOCKED with the reason."
    )


# -- layer 2: would the app actually recommend it? --------------------------

@pytest.mark.parametrize("tag", COACHING_TAGS)
def test_every_drill_gear_tag_is_recommendable(tag):
    titles = recommended_for(tag)
    drills = GEAR_TAGS[tag]
    if tag in UNRECOMMENDABLE:
        assert not titles, (
            f"{tag} is listed in UNRECOMMENDABLE but the app now recommends "
            f"{titles} for it. Delete the UNRECOMMENDABLE entry."
        )
        return
    assert titles, (
        f"shop.recommend returns nothing for {tag} under the shipped "
        f"config.yaml, so the {len(drills)} drill(s) that prescribe it "
        f"({', '.join(drills)}) reach a silent store even though a product "
        "carries the tag. Check the shop.first_sale_* gate, or add "
        f"{tag!r} to UNRECOMMENDABLE with the reason."
    )


# -- the ledgers themselves --------------------------------------------------

@pytest.mark.parametrize("ledger_name", ["UNSTOCKED", "UNRECOMMENDABLE"])
def test_known_gap_ledgers_name_real_drill_tags(ledger_name):
    """A waiver for a tag no drill prescribes is dead weight that hides a typo."""
    ledger = {"UNSTOCKED": UNSTOCKED, "UNRECOMMENDABLE": UNRECOMMENDABLE}[ledger_name]
    unknown = sorted(set(ledger) - set(GEAR_TAGS))
    assert not unknown, (
        f"{ledger_name} waives {unknown}, which no drill prescribes. Either "
        "the tag is misspelled or the drill was removed — delete the entry."
    )


def test_unstocked_tags_are_also_unrecommendable():
    """A tag no product carries cannot be recommendable; the ledgers must agree.

    Without this, deleting an UNSTOCKED entry while leaving UNRECOMMENDABLE in
    place would leave the pair describing an impossible store.
    """
    contradiction = sorted(
        tag
        for tag in UNSTOCKED
        if tag in COACHING_TAGS and tag not in UNRECOMMENDABLE
    )
    assert not contradiction, (
        f"{contradiction} are listed as unstocked but not as unrecommendable. "
        "An unstocked tag can never be recommended — the ledgers disagree."
    )


def test_snapshot_carries_the_fields_the_shop_reads():
    """Guard the snapshot's shape against a rename in shop._product().

    first_sale_products() and recommend() read exactly ``tags`` and
    ``available``. If either were dropped from the snapshot every product
    would look unavailable, every tag would look uncovered, and the ledgers
    above would fill up with gaps that do not exist.
    """
    node = {
        "title": "Probe",
        "handle": "probe",
        "description": "",
        "tags": ["swinglab:tempo"],
        "availableForSale": True,
        "onlineStoreUrl": None,
        "featuredImage": None,
        "priceRange": {"minVariantPrice": {"amount": "1.00", "currencyCode": "USD"}},
    }
    live_shape = shop._product(node, "example.myshopify.com")
    required = {"title", "tags", "available"}
    assert required <= set(live_shape), (
        f"shop._product() no longer returns {sorted(required - set(live_shape))}; "
        "scripts/refresh_gear_catalog.py and this module need updating together."
    )
    assert required <= set(catalogue()[0]), (
        "The committed snapshot is missing fields the recommendation path "
        "reads — regenerate it with scripts/refresh_gear_catalog.py."
    )


def test_snapshot_is_not_empty():
    """An empty snapshot would make every assertion above vacuously agree."""
    assert catalogue(), (
        "tests/fixtures/gear_catalog.json has no products. A Shopify outage "
        "during a refresh must not be committed — regenerate it."
    )
