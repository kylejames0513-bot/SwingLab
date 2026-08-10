"""Every key in config.yaml must be a key the code can read.

``Config.load`` deep-merges config.yaml over ``DEFAULTS`` and accepts unknown
keys silently. That is the right behavior for forward compatibility and the
wrong behavior for typos: misspell ``shopify_variant_ids`` and every checkout
deep-link on /pricing silently falls back to the plain product page — no
error, no log line, no failing test. The 2026-08-10 scan found exactly one
key living outside DEFAULTS (``billing.shopify_variant_ids``), which meant a
typo in the money path's configuration was undetectable.

This test closes that class: a config.yaml key with no DEFAULTS entry fails
the build, naming the key. Adding a legitimate new setting therefore means
adding its DEFAULTS entry first — which is where its documentation and
bare-code default belong anyway.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from swinglab.config import DEFAULTS

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"

# Mappings whose KEYS are data, not schema: SKUs and plan names come from the
# store, so config.yaml legitimately writes keys DEFAULTS cannot know. The
# walk checks these exist and are dicts, but does not descend into them.
OPEN_MAPPINGS = {
    ("billing", "shopify_skus"),
    ("billing", "shopify_sku_tiers"),
    ("billing", "shopify_variant_ids"),
}


def _unknown_keys(node: dict, defaults: dict, path: tuple[str, ...]) -> list[str]:
    unknown: list[str] = []
    for key, value in node.items():
        here = path + (key,)
        if key not in defaults:
            unknown.append(".".join(here))
            continue
        if (
            isinstance(value, dict)
            and isinstance(defaults[key], dict)
            and here not in OPEN_MAPPINGS
        ):
            unknown.extend(_unknown_keys(value, defaults[key], here))
    return unknown


def test_every_config_yaml_key_exists_in_defaults():
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)

    unknown = _unknown_keys(loaded, DEFAULTS, ())

    assert unknown == [], (
        "config.yaml sets keys the code never reads (typo, or a setting "
        "missing its DEFAULTS entry): " + ", ".join(unknown)
    )


def test_open_mappings_are_still_real_settings():
    """The allowlist above must not paper over a deleted setting."""
    for path in OPEN_MAPPINGS:
        node = DEFAULTS
        for key in path:
            assert key in node, f"OPEN_MAPPINGS names a dead path: {'.'.join(path)}"
            node = node[key]
        assert isinstance(node, dict)


def test_the_typo_this_exists_to_catch_is_caught():
    """A misspelled money-path key must be named, not merged."""
    broken = {"billing": {"shopify_varient_ids": {"monthly": "1"}}}

    unknown = _unknown_keys(broken, DEFAULTS, ())

    assert unknown == ["billing.shopify_varient_ids"]
