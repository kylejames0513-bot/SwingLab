"""The first-sale gate must never empty the shop quietly.

``shop.first_sale_catalog_only`` is a deliberate ethical brake: the app does
not promote a training aid until the operator has real sample and fulfillment
evidence for it. That is worth keeping. What is not worth keeping is the way
it fails — an empty ``/shop`` and silent gear recommendations look identical
whether the cause is an empty store, an expired Storefront token, products
unpublished from the channel, or an allowlist naming products that were
archived months ago.

The first three already log. These tests pin the fourth, and pin the
config-integrity check that would have caught the staleness on the day the
products were archived rather than in an audit later.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from swinglab.config import Config
from swinglab.web import shop


def product(title, *tags, available=True):
    return {"title": title, "handle": title.lower(), "tags": list(tags),
            "available": available}


@pytest.fixture(autouse=True)
def _reset_warning_state():
    shop._gate_warning_state["signature"] = None
    yield
    shop._gate_warning_state["signature"] = None


@pytest.fixture()
def gated_config():
    cfg = Config()
    cfg.shop["first_sale_catalog_only"] = True
    cfg.shop["first_sale_verified_tag"] = "caddieinsight:fulfillment-verified"
    cfg.shop["first_sale_candidate_tags"] = ["caddieinsight:tempo-trainer"]
    return cfg


def test_the_gate_says_why_it_emptied_the_catalogue(gated_config, caplog):
    """The exact production situation: live products, nothing tagged."""
    live = [
        product("Tempo Rope", "swinglab:tempo"),
        product("Connection Ball", "swinglab:arm-extension"),
    ]
    with caplog.at_level(logging.WARNING, logger="swinglab.web.shop"):
        assert shop.first_sale_products(live, gated_config) == []

    message = caplog.text
    assert "filtered 2 live product(s)" in message
    assert "caddieinsight:fulfillment-verified" in message
    assert "caddieinsight:tempo-trainer" in message
    # It must name the escape hatches, because the operator reading this log
    # is deciding between two very different actions.
    assert "sample-tested" in message
    assert "first_sale_catalog_only" in message


def test_a_stale_allowlist_is_reported_as_such(gated_config, caplog):
    """Verified tag present, allowlist stale — the 2026-08-03 restock case."""
    live = [
        product(
            "Tempo Rope",
            "caddieinsight:fulfillment-verified",
            "swinglab:tempo",
        )
    ]
    with caplog.at_level(logging.WARNING, logger="swinglab.web.shop"):
        assert shop.first_sale_products(live, gated_config) == []

    assert "match no product in the catalogue" in caplog.text
    assert "caddieinsight:tempo-trainer" in caplog.text


def test_a_properly_tagged_product_passes_and_stays_quiet(
    gated_config, caplog
):
    live = [
        product(
            "Tempo Rope",
            "caddieinsight:fulfillment-verified",
            "caddieinsight:tempo-trainer",
            "swinglab:tempo",
        ),
        product("Untested Aid", "swinglab:sway"),
    ]
    with caplog.at_level(logging.WARNING, logger="swinglab.web.shop"):
        kept = shop.first_sale_products(live, gated_config)

    assert [p["title"] for p in kept] == ["Tempo Rope"]
    assert caplog.text == "", "a working gate must not warn"


def test_an_empty_catalogue_does_not_blame_the_gate(gated_config, caplog):
    """Nothing to filter is a different problem, already logged upstream."""
    with caplog.at_level(logging.WARNING, logger="swinglab.web.shop"):
        assert shop.first_sale_products([], gated_config) == []
    assert caplog.text == ""


def test_the_warning_does_not_repeat_per_request(gated_config, caplog):
    """This runs on every /shop view and every finished analysis."""
    live = [product("Tempo Rope", "swinglab:tempo")]
    with caplog.at_level(logging.WARNING, logger="swinglab.web.shop"):
        for _ in range(5):
            shop.first_sale_products(live, gated_config)
    assert caplog.text.count("filtered 1 live product(s)") == 1


def test_the_gate_off_promotes_everything_without_warning(caplog):
    cfg = Config()
    cfg.shop["first_sale_catalog_only"] = False
    live = [product("Tempo Rope", "swinglab:tempo")]
    with caplog.at_level(logging.WARNING, logger="swinglab.web.shop"):
        assert shop.first_sale_products(live, cfg) == live
    assert caplog.text == ""


def test_the_app_never_claims_verification_it_is_not_doing(tmp_path, monkeypatch):
    """With the gate off, no surface may assert fulfillment evidence.

    The empty-shop copy carries the only sample-tested claim in the product.
    It is correct while the gate is on and a lie the moment it is off, and
    the two are one config line apart — which is exactly the kind of pairing
    that rots. docs/strategy/positioning-and-growth.md §4 treats fake
    verification signals as the thing this brand must never emit.
    """
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from swinglab.web.app import create_app

    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    shop.clear_cache()
    monkeypatch.setattr(shop, "_fetch", lambda: [])

    cfg = Config()
    cfg.shop["enabled"] = True
    cfg.shop["first_sale_catalog_only"] = False

    html = TestClient(create_app(cfg, sessions_dir=tmp_path / "s")).get(
        "/shop"
    ).text
    assert "sample-tested" not in html
    assert "restocked" in html


# -- config integrity --------------------------------------------------------

def test_the_shipped_allowlist_names_tags_the_catalogue_actually_has():
    """Catch a stale allowlist the day the products leave, not in an audit.

    The shipped allowlist names three aids — the clip-on swing metronome,
    the anti-sway hip resistance band and the alignment-stick set — that were
    archived in the 2026-08-03 restock. Nothing failed when they went, so the
    gate quietly began filtering the whole catalogue to nothing.

    This is deliberately a check against the CATALOGUE FIXTURE rather than a
    waiver ledger: tests/fixtures/gear_catalog.json is refreshed from the
    live store by scripts/refresh_gear_catalog.py, so this goes red on the
    next refresh after a product is archived.
    """
    import json

    root = Path(__file__).resolve().parents[1]
    cfg = Config.load(root / "config.yaml")
    if not cfg.shop.get("first_sale_catalog_only"):
        pytest.skip("gate is off; the allowlist is inert")

    catalogue = json.loads(
        (root / "tests" / "fixtures" / "gear_catalog.json").read_text(
            encoding="utf-8"
        )
    )
    present = set()
    for entry in catalogue["products"]:
        present.update(entry.get("tags") or ())

    candidates = {
        str(t) for t in (cfg.shop.get("first_sale_candidate_tags") or ())
    }
    stale = sorted(candidates - present)
    verified = str(cfg.shop.get("first_sale_verified_tag") or "")

    assert not stale or verified not in present, (
        "shop.first_sale_candidate_tags names tags no live product carries "
        f"({', '.join(stale)}), and no product carries "
        f"{verified!r} either — so the gate filters the entire catalogue to "
        "nothing and the app recommends no gear for any measured flag. "
        "Either tag the SKUs you have sample-tested in Shopify and refresh "
        "the fixture (scripts/refresh_gear_catalog.py), or set "
        "shop.first_sale_catalog_only false. This assertion is written to "
        "pass ONLY while the gap is total: the moment one product is "
        "correctly tagged, tighten it to `assert not stale`."
    )
