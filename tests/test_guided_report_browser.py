from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from tests.report_view_fixtures import (
    LOCKED_REPLAY_SENTINEL,
    report_document_fixture,
)
from tests.test_guided_report_html import render_fixture_path


def _open_report(
    page: Page,
    tmp_path: Path,
    name: str = "coaching-improve-clear",
    *,
    sample_banner: dict | None = None,
) -> Path:
    path = render_fixture_path(
        tmp_path, name, sample_banner=sample_banner
    ).resolve()
    page.goto(path.as_uri(), wait_until="load")
    return path


def _assert_no_horizontal_page_scroll(page: Page) -> None:
    assert page.evaluate(
        "document.documentElement.scrollWidth <= "
        "document.documentElement.clientWidth"
    )


def test_long_copy_opening_fold_and_320px_reflow(tmp_path: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        _open_report(page, tmp_path, "coaching-improve-clear-long-copy")

        cue_box = page.locator('[data-field="cue"]').bounding_box()
        assert cue_box is not None
        assert cue_box["y"] + cue_box["height"] <= 844
        page.set_viewport_size({"width": 320, "height": 844})
        _assert_no_horizontal_page_scroll(page)
        browser.close()


def test_local_report_makes_no_network_requests(tmp_path: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        requested: list[str] = []
        page.on("request", lambda request: requested.append(request.url))
        _open_report(page, tmp_path, "pro-unlocked")

        assert requested
        assert all(url.startswith("file:") for url in requested)
        browser.close()


def test_200_percent_text_reflows_and_keeps_later_steps_reachable(
    tmp_path: Path,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 320, "height": 844})
        _open_report(page, tmp_path)
        page.add_style_tag(content=":root { font-size: 200% !important; }")

        _assert_no_horizontal_page_scroll(page)
        assert page.locator("#practice").is_visible()
        assert page.locator("#refilm").is_visible()
        page.locator("#refilm").scroll_into_view_if_needed()
        assert page.locator("#refilm").bounding_box() is not None
        browser.close()


def test_controls_have_44px_targets_and_visible_keyboard_focus(
    tmp_path: Path,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        _open_report(page, tmp_path)

        controls = page.locator(".report-control, details > summary")
        assert controls.count() > 0
        for index in range(controls.count()):
            box = controls.nth(index).bounding_box()
            assert box is not None
            assert box["width"] >= 44
            assert box["height"] >= 44

        page.keyboard.press("Tab")
        skip_link = page.locator(".skip-link")
        assert skip_link.evaluate("element => element === document.activeElement")
        skip_outline = float(
            skip_link.evaluate(
                "element => parseFloat(getComputedStyle(element).outlineWidth)"
            )
        )
        skip_box = skip_link.bounding_box()
        assert skip_outline >= 2
        assert skip_box is not None
        assert 0 <= skip_box["x"] < 390 and 0 <= skip_box["y"] < 844

        first_summary = page.locator("details > summary").first
        for _ in range(12):
            page.keyboard.press("Tab")
            if first_summary.evaluate("element => element === document.activeElement"):
                break
        summary_outline = float(
            first_summary.evaluate(
                "element => parseFloat(getComputedStyle(element).outlineWidth)"
            )
        )
        assert summary_outline >= 2
        assert first_summary.evaluate("element => element === document.activeElement")
        page.wait_for_function(
            "selector => { const rect = document.querySelector(selector)"
            ".getBoundingClientRect(); return rect.top >= 0 && "
            "rect.bottom <= innerHeight; }",
            arg="details > summary",
        )
        summary_box = first_summary.evaluate(
            "element => { const rect = element.getBoundingClientRect(); "
            "return { top: rect.top, bottom: rect.bottom }; }"
        )
        assert 0 <= summary_box["top"] < 844
        assert summary_box["bottom"] <= 844
        browser.close()


def test_reading_order_and_native_disclosure_keyboard_behavior(
    tmp_path: Path,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        _open_report(page, tmp_path)

        fields = ["priority", "observation", "cue", "drill-name", "pass-mark"]
        assert page.evaluate(
            "fields => fields.every((field, index) => index === 0 || "
            "document.querySelector(`[data-field=\"${fields[index - 1]}\"]`)"
            ".compareDocumentPosition(document.querySelector(`[data-field=\"${field}\"]`)) "
            "& Node.DOCUMENT_POSITION_FOLLOWING)",
            fields,
        )

        summary = page.locator("details > summary").first
        summary.focus()
        was_open = summary.evaluate("element => element.parentElement.open")
        page.keyboard.press("Space")
        assert summary.evaluate("element => element === document.activeElement")
        assert summary.evaluate("element => element.parentElement.open") is not was_open
        assert summary.evaluate(
            "element => Boolean(element.nextElementSibling) && "
            "element.nextElementSibling.parentElement === element.parentElement"
        )
        browser.close()


def test_reduced_motion_removes_smooth_scroll_and_long_transitions(
    tmp_path: Path,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.emulate_media(reduced_motion="reduce")
        _open_report(page, tmp_path)

        assert page.locator("html").evaluate(
            "element => getComputedStyle(element).scrollBehavior"
        ) == "auto"
        violations = page.locator("*").evaluate_all(
            "elements => elements.filter(element => {"
            " const style = getComputedStyle(element);"
            " const seconds = value => Math.max(...value.split(',').map(item => {"
            "   item = item.trim();"
            "   return item.endsWith('ms') ? parseFloat(item) / 1000 : parseFloat(item) || 0;"
            " }));"
            " return seconds(style.animationDuration) > 0.01 ||"
            "        seconds(style.transitionDuration) > 0.01;"
            "}).length"
        )
        assert violations == 0
        browser.close()


def test_report_remains_readable_and_disclosures_operable_without_javascript(
    tmp_path: Path,
) -> None:
    document = report_document_fixture("coaching-improve-clear")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            java_script_enabled=False,
        )
        page = context.new_page()
        _open_report(page, tmp_path)

        body_text = page.locator("body").text_content() or ""
        assert document.view.next_move.title in body_text
        assert document.view.next_move.cue in body_text
        assert document.view.practice.name in body_text
        assert document.view.refilm.target.text in body_text
        for phase in document.view.phases:
            assert phase.label in body_text
        assert page.locator(".focused-evidence img").get_attribute("alt") == (
            document.view.visual_evidence.alt_text
        )
        summaries = page.locator("details > summary")
        assert summaries.count() == page.locator("details").count()
        for index in range(summaries.count()):
            summary = summaries.nth(index)
            assert summary.is_visible()
            was_open = summary.evaluate("element => element.parentElement.open")
            summary.click()
            assert summary.evaluate("element => element.parentElement.open") is not was_open
        context.close()
        browser.close()


def test_print_expands_content_uses_posters_and_hides_screen_controls(
    tmp_path: Path,
) -> None:
    sample = {
        "text": "Synthetic sample — not a real golfer",
        "cta_label": "Analyze your swing",
        "cta_url": "/",
    }
    unlocked = report_document_fixture("pro-unlocked")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        _open_report(page, tmp_path, "pro-unlocked", sample_banner=sample)
        swing = unlocked.depth.swings[0]
        poster_path = unlocked.media_by_key[
            swing.video_poster_media_key
        ].relative_path
        screen_videos = page.locator("video")
        assert screen_videos.count() == 2
        assert all(
            screen_videos.nth(index).get_attribute("poster") == poster_path
            for index in range(screen_videos.count())
        )
        page.emulate_media(media="print")

        closed_details = page.locator("details:not([open])")
        assert closed_details.count() > 0
        for index in range(closed_details.count()):
            details = closed_details.nth(index)
            assert details.evaluate(
                "element => getComputedStyle(element, '::details-content')"
                ".contentVisibility === 'visible'"
            )
            printable_children = details.locator(
                ":scope > :not(summary):not(.screen-only)"
            )
            assert printable_children.count() > 0
            assert any(
                printable_children.nth(child).is_visible()
                for child in range(printable_children.count())
            )
        assert page.locator("video:visible").count() == 0
        assert page.locator(".screen-only:visible").count() == 0
        assert page.get_by_text(sample["text"], exact=True).is_visible()
        assert not page.get_by_role("link", name=sample["cta_label"]).is_visible()

        print_figures = page.locator(".playback-print:visible")
        assert print_figures.count() == 2
        for index in range(print_figures.count()):
            figure = print_figures.nth(index)
            assert figure.locator("img").get_attribute("alt") == swing.video_poster_alt_text
            caption = figure.locator("figcaption").text_content() or ""
            assert swing.print_playback_reference in caption
        assert swing.slow_motion_caption in page.locator("body").text_content()
        assert swing.coach_replay_caption in page.locator("body").text_content()

        pdf = page.pdf(print_background=True)
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 1000

        locked_page = browser.new_page(viewport={"width": 390, "height": 844})
        _open_report(locked_page, tmp_path, "free-locked")
        locked_page.emulate_media(media="print")
        locked_swing = report_document_fixture("free-locked").depth.swings[0]
        locked_text = locked_page.locator("body").text_content() or ""
        assert locked_swing.locked_replay_explanation in locked_text
        assert locked_page.locator("video").count() == 0
        assert locked_page.locator('[src*="locked"], [poster*="locked"]').count() == 0
        assert LOCKED_REPLAY_SENTINEL not in locked_page.content()
        browser.close()
