"""The two colour rules, enforced.

The INSTRUMENT palette is near-monochrome plus exactly two signals, and its
whole value is that each one means one thing:

    amber (--sl-orange / --sl-accent)  a value the ENGINE MEASURED
    cyan  (--sl-trace)                 the LIVE READOUT

The rules are easy to state and easy to erode, because both colours are the
most attractive thing on a near-black page — so every time something wants
emphasis, they are the obvious reach. Three separate passes over this
codebase spent them on: the primary call-to-action in the app header, the
`.sl-eyebrow` label in every section, and the hover fill of 25 buttons across
16 files. Each looked good in isolation. Together they would have taught a
reader that amber means "important", at which point it no longer means
"measured" and the palette has no rules at all.

These gates are deliberately narrow. They do not police where a signal MAY
appear — that needs a human — only the two places it demonstrably must not,
both of which are mechanically detectable and both of which have already
happened.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SOURCES = sorted(
    [
        *(ROOT / "storefront-theme").rglob("*.liquid"),
        ROOT / "storefront-theme" / "assets" / "base.css",
        *(ROOT / "swinglab" / "templates").glob("*.j2"),
    ]
)

SIGNAL = re.compile(r"var\(--sl-(?:accent|orange)\)|var\(--sl-accent-rgb\)")
TRACE = re.compile(r"var\(--sl-trace\)")
# A CSS rule: everything up to the brace is the selector, the braces are the
# body. Nested at-rules are not matched, which is fine — the declarations
# inside them are still caught, because their inner rules match too.
RULE = re.compile(r"([^{}]*)\{([^{}]*)\}", re.S)


def _rules():
    for path in SOURCES:
        source = path.read_text(encoding="utf-8")
        for match in RULE.finditer(source):
            selector = " ".join(match.group(1).split())
            yield path.relative_to(ROOT).as_posix(), selector, match.group(2)


def test_the_live_read_colour_is_never_interaction_feedback():
    """Cyan is a reading, and a pointer resting on a button is not one.

    This is the rule that eroded hardest: filling a bone button with cyan on
    hover looks genuinely good, which is why it reached 25 rules across 16
    files before anyone said so. --sl-ink-hi exists for exactly this job.
    """
    offenders = [
        f"{source}: {selector}"
        for source, selector, body in _rules()
        if ":hover" in selector
        and TRACE.search(body)
        and re.search(r"(?:^|\s)(?:background|border-color)\s*:", body)
    ]
    assert offenders == [], (
        "--sl-trace used as interaction feedback; use --sl-ink-hi:\n"
        + "\n".join(offenders)
    )


def test_the_signal_colour_is_never_a_call_to_action():
    """Amber marks a measured value, so it is never a button's fill.

    A bone fill on a near-black field is louder than amber anyway — this
    costs the design nothing and is the reason the rule is affordable.
    """
    # `link` and `is-current` are here because the violation this gate
    # missed on its first run was `.sl-header__link.is-current` — "you are
    # here" painted amber, on every page, which made it the single most
    # repeated wrong use of the signal in the product.
    action = re.compile(
        r"btn|button|cta|__primary|__action|submit|__link|is-current", re.I
    )
    offenders = []
    for source, selector, body in _rules():
        if not action.search(selector):
            continue
        for declaration in body.split(";"):
            name, _, value = declaration.partition(":")
            if name.strip() == "background" and SIGNAL.search(value):
                offenders.append(f"{source}: {selector}")
    assert offenders == [], (
        "the signal colour is filling a control; controls are bone:\n"
        + "\n".join(offenders)
    )


def test_both_surfaces_define_the_signals_identically():
    """One product, so the two signals cannot drift into two brands."""
    base = (ROOT / "storefront-theme" / "assets" / "base.css").read_text(
        encoding="utf-8"
    )
    layout = (ROOT / "swinglab" / "templates" / "web_layout.html.j2").read_text(
        encoding="utf-8"
    )

    def token(source: str, name: str) -> str:
        match = re.search(rf"--{name}:\s*([^;]+);", source)
        assert match is not None, f"missing --{name}"
        return match.group(1).strip()

    for name in ("sl-orange", "sl-accent", "sl-trace", "sl-ink-hi"):
        assert token(base, name) == token(layout, name), name
