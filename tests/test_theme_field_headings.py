"""A heading on the green field must state its own colour.

base.css styles `h1, h2, h3` as element selectors, and an element rule beats
inheriting `color` from an ancestor. So a section that paints
`background: var(--sl-field)` and then drops an `<h1>` inside it gets the ink
colour meant for paper — #1d1f20 on #070f0b, about 1.2:1, which is not "low
contrast" but *invisible*.

It has happened twice. CLAUDE.md records the 2026-08-11 overhaul where "all 70
design-gate tests passed while the app's hero rendered ink-on-near-black",
because the tokens were right and the grounds were not. It happened again on
sections/founders.liquid, and it did not read as a colour bug from the
outside — the eye sees a gap where the headline should be and concludes the
layout is off centre.

Neither a token test nor a contrast test catches this, because every value
involved is correct in isolation. What is wrong is which of two correct values
wins the cascade. Hence a structural check: if a section paints the field, its
title rules must not leave `color` to inheritance.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SECTIONS = Path(__file__).resolve().parent.parent / "storefront-theme" / "sections"

# The grounds that reverse the page. A heading over any of these inherits the
# wrong colour from base.css unless it says otherwise.
FIELD_GROUNDS = ("var(--sl-field)", "var(--sl-field-lift)")

# Rules whose selector names a heading. Kickers, captions and body copy are
# excluded deliberately: they are not element-level headings, so they inherit
# cleanly and are already correct.
#
# The selector is captured alongside the declarations because a title is
# normally styled by several rules — a base one and its responsive overrides —
# and only ONE of them has to carry the colour. Requiring it of every rule
# would flag `@media { .sl-hero__title { max-width: 100% } }`, which is both
# correct and unavoidable.
TITLE_RULE = re.compile(r"([^{}]*__(?:title|heading)\b[^{}]*)\{([^}]*)\}")


def _stylesheet(path: Path) -> str:
    body = path.read_text(encoding="utf-8")
    block = re.search(r"{%-?\s*stylesheet\s*-?%}(.*?){%-?\s*endstylesheet\s*-?%}", body, re.S)
    if not block:
        return ""
    # Comments go first. This file is heavily commented, and a comment sitting
    # above a rule otherwise lands inside the captured selector text — which
    # makes one selector look like two, one of them carrying the colour and one
    # not, and the check fails on a file that is entirely correct.
    return re.sub(r"/\*.*?\*/", " ", block.group(1), flags=re.S)


def _paints_the_field(css: str) -> bool:
    return any(
        re.search(r"background(?:-color)?\s*:[^;}]*" + re.escape(ground), css)
        for ground in FIELD_GROUNDS
    )


FIELD_SECTIONS = sorted(
    p for p in SECTIONS.glob("*.liquid") if _paints_the_field(_stylesheet(p))
)


def test_some_section_actually_paints_the_field():
    """Guard the guard — a bad matcher would make every case below vacuous."""
    assert FIELD_SECTIONS, "no section paints the field; the detector is broken"


@pytest.mark.parametrize("path", FIELD_SECTIONS, ids=lambda p: p.name)
def test_titles_on_the_field_declare_their_own_colour(path: Path):
    css = _stylesheet(path)

    declares_colour: dict[str, bool] = {}
    for selector, decls in TITLE_RULE.findall(css):
        # One rule may list several selectors; each is judged on its own.
        for name in (s.strip() for s in selector.split(",")):
            if "__title" not in name and "__heading" not in name:
                continue
            declares_colour[name] = declares_colour.get(name, False) or "color:" in decls

    offenders = sorted(name for name, ok in declares_colour.items() if not ok)
    assert not offenders, (
        f"{path.name} paints the field but leaves a title's colour to "
        f"inheritance, which base.css's h1/h2/h3 rule overrides with paper "
        f"ink. Set color explicitly on: {offenders}"
    )
