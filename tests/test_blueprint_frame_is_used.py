"""The blueprint frame has to be ON something.

INDUSTRY's signature is the wireframe object: a square hairline box with four
`+` registration marks straddling its corners. It shipped twice without ever
being visible, and neither failure produced a red test or a warning:

  1. The frame was defined in base.css as `.sl-blueprint` plus four
     `.sl-corner` children, and used in ZERO markup. Sixteen rules of CSS, no
     appearances. Every design gate was green — they check tokens, breakpoints
     and contrast, and none of them can tell whether a device is used.
  2. The fix listed the app's panel classes in base.css. The app does not load
     base.css; it carries its own copy of the sheet in web_layout.html.j2. The
     selectors matched nothing, on either surface, and again reported nothing.

Unmatched CSS is not an error in any part of this toolchain — not in
theme-check, not in the browser, not in a screenshot anybody glances at. So
the gate has to assert the join between the rule and the thing it styles,
which is what this file does and what nothing else did.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "storefront-theme" / "assets" / "base.css"
LAYOUT = ROOT / "swinglab" / "templates" / "web_layout.html.j2"
SECTIONS = ROOT / "storefront-theme" / "sections"
SNIPPETS = ROOT / "storefront-theme" / "snippets"
TEMPLATES = ROOT / "swinglab" / "templates"

# The eight-bar pseudo-element that draws the four marks. Both sheets carry
# their own copy; this is the fingerprint that says a rule is the frame.
MARK_FINGERPRINT = "left 5px top 0, left 0 top 5px"


def _classes_in_markup(paths) -> set[str]:
    """Every class name a page can actually end up carrying.

    Two emission paths, and missing the second one produces a false alarm that
    reads exactly like a real orphan: Liquid's own tags take the class as an
    ARGUMENT rather than an attribute — `{% form 'contact', class: 'x' %}` —
    so a scanner that only reads class="..." declares a live class dead.
    """
    found: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8")
        attrs = re.findall(r'class="([^"]*)"', text)
        attrs += re.findall(r"class:\s*'([^']*)'", text)
        attrs += re.findall(r'class:\s*"([^"]*)"', text)
        for attr in attrs:
            # Liquid/Jinja interpolation inside an attribute is common; the
            # literal words around it are still real class names.
            for token in re.split(r"[\s{}%|]+", attr):
                if re.fullmatch(r"[a-z][a-z0-9_-]*", token):
                    found.add(token)
    return found


def _framed_selectors(sheet: str) -> list[str]:
    """Every class whose ::after draws the marks.

    Collected from ALL rules carrying the fingerprint, not the first one. Both
    sheets also define a generic `.sl-blueprint::after`, and an earlier version
    of this helper split on that and never saw the panel list at all — the
    same class of mistake the file exists to catch, one level up.
    """
    rules = re.findall(
        r"([^{}]*)\{[^{}]*" + re.escape(MARK_FINGERPRINT) + r"[^{}]*\}", sheet
    )
    found: set[str] = set()
    for selector_list in rules:
        found |= set(re.findall(r"\.([a-z][a-z0-9_-]*)::after", selector_list))
    return sorted(found)


def test_the_frame_is_drawn_by_the_box_not_by_four_child_spans():
    """A device that costs markup surgery to apply does not get applied.

    The original encoding needed four <i class="sl-corner"> children per card.
    Every card on both surfaces is emitted by a section or a Jinja loop, so
    that is 60 files of edits — which is why it shipped on nothing. Eight bars
    on one pseudo-element draws the same thing for the price of a selector.
    """
    for sheet in (BASE.read_text(encoding="utf-8"), LAYOUT.read_text(encoding="utf-8")):
        assert MARK_FINGERPRINT in sheet
        assert "background-repeat: no-repeat" in sheet


@pytest.mark.parametrize(
    ("sheet_path", "markup_globs", "surface"),
    (
        (BASE, ((SECTIONS, "*.liquid"), (SNIPPETS, "*.liquid")), "storefront"),
        (LAYOUT, ((TEMPLATES, "*.j2"),), "app"),
    ),
)
def test_every_framed_selector_matches_something_that_exists(
    sheet_path, markup_globs, surface
):
    """The join. A framed class that appears in no markup is dead CSS.

    This is the assertion both previous attempts would have failed, and it is
    deliberately about the SURFACE: base.css is checked against the theme's
    markup and web_layout.html.j2 against the app's, because the sheets are
    separate and a selector in the wrong one matches nothing at all.
    """
    sheet = sheet_path.read_text(encoding="utf-8")
    framed = _framed_selectors(sheet)
    assert framed, f"{surface}: no framed selectors found at all"

    markup = set()
    for directory, pattern in markup_globs:
        markup |= _classes_in_markup(sorted(directory.glob(pattern)))

    # A class the sheet itself defines a rule for is not an orphan: base.css
    # authors .sl-drill-card, .sl-pcard and .sl-stat-band as shared components
    # that snippets and app pages render.
    defined_in_sheet = set(re.findall(r"\.([a-z][a-z0-9_-]*)\s*\{", sheet))
    orphans = [c for c in framed if c not in markup and c not in defined_in_sheet]
    assert orphans == [], (
        f"{surface}: framed classes that appear in no markup and no rule — "
        f"dead selectors render nothing and report nothing:\n  "
        + "\n  ".join(orphans)
    )


def test_the_frame_reaches_a_meaningful_number_of_panels():
    """A grammar needs enough instances to read as one.

    Not a style preference: one framed card on one page is indistinguishable
    from an accident. The counts here are the derived panel lists — every
    entry has both a hairline-plus-padding rule and a real appearance in
    markup — and they are pinned low enough to allow curation and high enough
    to fail if the device is quietly dropped again.
    """
    assert len(_framed_selectors(BASE.read_text(encoding="utf-8"))) >= 10
    assert len(_framed_selectors(LAYOUT.read_text(encoding="utf-8"))) >= 20


def test_the_frame_is_not_given_to_things_that_are_not_drawings():
    """Marks belong to cards, figures and plates.

    Notices, logs, chips, badges, menus, form fields and buttons carry the
    same hairline. Marking those turns a grammar into a texture, and the
    system stops meaning anything by it.
    """
    banned = re.compile(r"chip|badge|pill|menu|dropdown|notice|log$|error|btn|button")
    for sheet_path, surface in ((BASE, "storefront"), (LAYOUT, "app")):
        offenders = [
            c
            for c in _framed_selectors(sheet_path.read_text(encoding="utf-8"))
            if banned.search(c)
        ]
        assert offenders == [], f"{surface}: not drawings — {offenders}"
