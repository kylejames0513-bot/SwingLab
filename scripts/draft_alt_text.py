#!/usr/bin/env python3
"""Draft alt text for the brand and campaign imagery, using Claude Fable 5.

Where this is allowed to run, and where it is not
-------------------------------------------------
`docs/strategy/claude-build-brief.md` draws a hard line: coaching comes from
measured pose and deterministic rules, and no model goes between the
measurement and the recommendation. Prose and presentation are explicitly
fair game, and alt text is exactly that — a description of a picture, written
for a screen reader.

So this script only ever sees image files from `store-assets/` and
`storefront-theme/assets/`. It never reads a report, a metrics payload, a
session directory, or anything else a golfer produced. If you find yourself
wanting to point it at `sessions/`, that is the line.

The prompt also carries the campaign-imagery rule forward: these are
AI-generated or generated-from-geometry scenes, not customer photographs, so
the alt text may describe what is visibly in the frame and may not imply a
real customer, a real round, or a measured result.

Usage
-----
    python scripts/draft_alt_text.py                       # the bound assets
    python scripts/draft_alt_text.py path/to/image.png ...
    python scripts/draft_alt_text.py --effort medium

Output is a Markdown table on stdout — review it, then paste what survives
into `store-assets/alt-text.md`. Nothing is written automatically: alt text is
customer-facing copy, and a draft is a draft.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import sys
from pathlib import Path

try:
    import anthropic
except ModuleNotFoundError:  # pragma: no cover - dependency is optional
    sys.exit(
        "The anthropic SDK is not installed. `pip install anthropic`, then "
        "authenticate with `ant auth login` (or export ANTHROPIC_API_KEY)."
    )

REPO = Path(__file__).resolve().parent.parent

# The images the storefront and the app actually bind. Kept explicit rather
# than globbed: a glob would sweep in work-in-progress exports and the
# retired swinglab-* marks, and alt text for a file nothing renders is waste.
DEFAULT_IMAGES = (
    "storefront-theme/assets/caddieinsight-range-hero-desktop.webp",
    "storefront-theme/assets/caddieinsight-range-hero-mobile.webp",
    "storefront-theme/assets/caddieinsight-logo.png",
    "storefront-theme/assets/caddieinsight-free-card-v2.png",
    "storefront-theme/assets/caddieinsight-pro-card-v2.png",
    "storefront-theme/assets/caddieinsight-founders-card-v2.png",
    "storefront-theme/assets/og-caddieinsight.png",
)

SYSTEM = """\
You write alt text for CaddieInsight, a golf swing analysis product.

Alt text is read aloud in place of the image, so it describes what is visibly \
in the frame for someone who cannot see it. It is not a caption, not marketing \
copy, and not a restatement of nearby text.

Three rules specific to this brand:

1. These are campaign scenes and generated brand marks, never customer \
photographs. Never write anything implying a real customer, a real round, or \
a real result — no "a golfer improving his swing", no "after using \
CaddieInsight".
2. Never describe or invent a measurement. If a number, dial, or readout is \
not legibly present in the image, it does not go in the alt text. If one is \
present, describe that it is shown without asserting the value is a real \
golfer's result.
3. Decorative brand geometry gets short, plain alt text. A logo is "CaddieInsight \
logo", not a description of its tick marks — unless the mark itself is the \
subject of the page.

Length: one sentence, usually under 125 characters. Lead with the subject. No \
"image of" or "picture of" — a screen reader already says that.\
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "alt": {
            "type": "string",
            "description": "The alt text itself. One sentence.",
        },
        "decorative": {
            "type": "boolean",
            "description": (
                "True when the image carries no information a reader needs — "
                "pure decoration, which should ship as alt=\"\" instead."
            ),
        },
        "note": {
            "type": "string",
            "description": (
                "One short line for the human reviewer: anything ambiguous in "
                "the frame, or a rule that constrained the wording. Empty when "
                "there is nothing to flag."
            ),
        },
    },
    "required": ["alt", "decorative", "note"],
    "additionalProperties": False,
}


def image_block(path: Path) -> dict:
    media_type, _ = mimetypes.guess_type(path.name)
    if media_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
        raise ValueError(f"{path.name}: unsupported image type {media_type!r}")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(path.read_bytes()).decode("ascii"),
        },
    }


def draft(client: "anthropic.Anthropic", path: Path, effort: str) -> dict:
    """One image in, one reviewed-ready draft out.

    Streaming rather than a plain create: thinking is always on for Fable 5
    and counts against max_tokens alongside the response, so the budget has to
    be generous — and a generous non-streaming budget is how you collect an
    HTTP timeout instead of an answer.
    """
    with client.beta.messages.stream(
        model="claude-fable-5",
        max_tokens=16000,
        # No `thinking` parameter at all. Fable 5 thinks unconditionally;
        # {"type": "disabled"} and {"type": "enabled", budget_tokens: N} are
        # both rejected with a 400. Depth is controlled by effort instead.
        #
        # No temperature / top_p / top_k either — removed on this model.
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        # Fable 5's safety classifiers can decline a request outright. Opting
        # into server-side fallbacks means a false positive is re-run on
        # Anthropic's recommended substitute inside the same call rather than
        # coming back as a dead end. If the installed SDK does not type
        # `fallbacks` yet, move it to extra_body={"fallbacks": "default"}.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    image_block(path),
                    {
                        "type": "text",
                        "text": (
                            f"Filename: {path.name}\n"
                            "Write the alt text for this image."
                        ),
                    },
                ],
            }
        ],
    ) as stream:
        message = stream.get_final_message()

    # Check the stop reason before touching content. On a refusal `content` is
    # empty (declined before any output) or partial (declined mid-stream) —
    # indexing into it first is how this crashes on the one input that most
    # needed a clear error.
    if message.stop_reason == "refusal":
        category = getattr(message.stop_details, "category", None)
        raise RuntimeError(
            f"{path.name}: declined by safety classifiers "
            f"(category={category!r}), and the fallback chain declined too."
        )

    text = next((b.text for b in message.content if b.type == "text"), "")
    if not text:
        raise RuntimeError(f"{path.name}: no text in response (stop_reason={message.stop_reason})")
    return json.loads(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="*", help="Image paths; defaults to the bound assets.")
    parser.add_argument(
        "--effort",
        default="low",
        choices=["low", "medium", "high", "xhigh", "max"],
        help=(
            "Reasoning depth. Default low — describing a picture is routine "
            "work, and Fable 5 at low is not a weak model."
        ),
    )
    args = parser.parse_args()

    paths = [Path(p) for p in args.images] or [REPO / p for p in DEFAULT_IMAGES]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        return _fail(f"Not found: {', '.join(str(p) for p in missing)}")

    client = anthropic.Anthropic()
    rows, failures = [], []
    for path in paths:
        try:
            result = draft(client, path, args.effort)
        except Exception as error:  # keep going; one bad image is not a run
            failures.append(f"{path.name}: {error}")
            continue
        alt = '""  (decorative)' if result["decorative"] else result["alt"]
        rows.append((path.name, alt, result.get("note", "")))
        print(f"  drafted {path.name}", file=sys.stderr)

    print("\n| File | Alt text | Reviewer note |")
    print("| --- | --- | --- |")
    for name, alt, note in rows:
        print(f"| `{name}` | {alt} | {note} |")
    print(
        "\n_Drafts. Read every line before it ships — alt text is "
        "customer-facing copy, and this is a model's first pass at it._"
    )

    for failure in failures:
        print(f"FAILED {failure}", file=sys.stderr)
    return 1 if failures else 0


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
