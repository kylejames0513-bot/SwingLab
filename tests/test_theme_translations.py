"""Every `| t` key a section asks for must exist in the locale.

Shopify does not fail a render on a missing translation key — it prints
`Translation missing: en.some.key` into the page, in place of the copy. That
is a defect that ships silently: theme-check does not catch it, the theme zip
builds, the preview looks fine to anyone who does not read the exact line,
and the first person to notice is a customer.

The risk is highest during a redesign, when sections are rewritten and it is
tempting to invent a key rather than find the existing one. This gate makes
inventing one fail here instead of on the storefront.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "storefront-theme"
LOCALE = json.loads(
    (THEME / "locales" / "en.default.json").read_text(encoding="utf-8")
)

# `'homepage.hero.club_label' | t` — a quoted dotted key piped to the t filter.
KEY = re.compile(r"'([a-z0-9_]+(?:\.[a-z0-9_]+)+)'\s*\|\s*t\b")


def _resolves(key: str) -> bool:
    node = LOCALE
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


def _keys_in_use() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(THEME.rglob("*.liquid")):
        for match in KEY.finditer(path.read_text(encoding="utf-8")):
            found.setdefault(path.relative_to(THEME).as_posix(), set()).add(
                match.group(1)
            )
    return found


def test_every_translation_key_a_section_uses_exists():
    missing = {
        source: sorted(k for k in keys if not _resolves(k))
        for source, keys in _keys_in_use().items()
    }
    missing = {source: keys for source, keys in missing.items() if keys}

    assert not missing, "missing translation keys:\n" + "\n".join(
        f"  {source}: {', '.join(keys)}" for source, keys in sorted(missing.items())
    )


def test_the_locale_is_a_flat_tree_of_strings():
    """A key that resolves to an object renders as nothing, not as an error.

    `{{ 'homepage.hero' | t }}` where `hero` is a group prints an empty
    string, which is the same silent-blank failure mode the test above
    guards, reached from the other direction.
    """

    def walk(node: object, path: str) -> list[str]:
        if isinstance(node, str):
            return []
        if isinstance(node, dict):
            out: list[str] = []
            for name, child in node.items():
                out += walk(child, f"{path}.{name}" if path else name)
            return out
        return [f"{path} is {type(node).__name__}"]

    offenders = walk(LOCALE, "")
    assert not offenders, "non-string leaves:\n" + "\n".join(offenders)
