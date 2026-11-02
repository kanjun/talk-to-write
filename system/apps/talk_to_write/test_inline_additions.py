from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

SOURCE = (Path(__file__).parent / "src/talk_to_write/static/app.js").read_text()
HELPERS = "function additionOf(" + SOURCE.split("function additionOf(", 1)[1].split("\nfunction suggestionSpots(", 1)[0]
PLACE_EDIT = "function placeEdit(" + SOURCE.split("function placeEdit(", 1)[1].split("\nfunction openPop(", 1)[0]
STUBS = """
    const art = document.querySelector('article');
    const chosenOption = {}, CHECK = 'Accept', CROSS = 'Skip';
    const mdText = text => text;
    const esc = text => text;
    const decide = (...args) => window.decision = args;
"""


def _page(playwright):
    browser = playwright.chromium.launch(executable_path="/opt/fortress/tilion-fortress/tilion")
    page = browser.new_page()
    page.set_content("<article><p>Original.</p><h2>Next slide</h2></article>")
    page.add_script_tag(content=STUBS + HELPERS + PLACE_EDIT)
    return browser, page


@pytest.mark.browser
@pytest.mark.parametrize(
    "replacement,deleted,shown",
    [
        ("Original. And more words.", 0, " And more words."),
        ("Lead in. Original.", 0, "Lead in. "),
        ("Changed.", 1, "Changed."),
    ],
)
def test_inline_proposal_keeps_its_anchor_only_when_it_adds(replacement, deleted, shown):
    with sync_playwright() as playwright:
        browser, page = _page(playwright)
        page.evaluate(
            """replacement => {
            const range = document.createRange();
            range.selectNodeContents(document.querySelector('p'));
            placeEdit({id: 'proposal', source: 'chat', find: 'Original.', replace: replacement}, range);
        }""",
            replacement,
        )
        suggestion = page.locator('[data-sid="proposal"]')
        assert suggestion.locator("del").count() == deleted
        assert suggestion.locator("ins").text_content() == shown
        assert page.locator("article").inner_text().count("Original.") == 1
        suggestion.locator(".ok").click()
        assert page.evaluate("window.decision") == ["proposal", "accept", 0]
        browser.close()


@pytest.mark.browser
def test_paragraph_sized_changes_are_blocks_and_additions_edit_only_the_new_words():
    with sync_playwright() as playwright:
        browser, page = _page(playwright)
        kinds = page.evaluate(
            """() => [
            blockKind({source: 'chat', find: 'A.', replace: 'A.\\n\\nNew paragraph.'}),
            blockKind({source: 'chat', find: 'A.', replace: 'New paragraph.\\n\\nA.'}),
            blockKind({source: 'chat', find: 'A.\\n\\nB.', replace: 'C.'}),
            blockKind({source: 'chat', find: 'A.', replace: 'A. More.'}),
            blockKind({source: 'chat', find: 'A.', replace: 'Changed.'}),
            blockKind({source: undefined, find: 'A.\\n\\nB.', replace: 'C.'}),
        ]"""
        )
        assert kinds == ["after", "before", "replace", None, None, None]
        added = page.evaluate(
            """() => {
            const s = {source: 'chat', find: 'A.', replace: 'A.\\n\\nNew paragraph.'};
            return [newPart(s), fullText(s, 'Their words.')];
        }"""
        )
        assert added == ["New paragraph.", "A.\n\nTheir words."]
        browser.close()
