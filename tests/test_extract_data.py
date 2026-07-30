"""extract_by_result_type / オーガニックURLフィルタのテスト."""

from __future__ import annotations

import pytest

from browser_assistant.export import CollectionStore
from browser_assistant.extract_data import (
    extract_by_result_type,
    filter_organic_urls,
    format_numbered_urls,
    is_organic_candidate,
)
from browser_assistant.intent.normalize import intent_result_satisfied, normalize_intent


PAGE = """
<!doctype html>
<html><head><title>Extract Me</title></head>
<body>
  <p>Hello body</p>
  <div id="rso">
    <a href="https://a.example/x"><h3>Result A</h3></a>
    <a href="https://b.example/y"><h3>Result B</h3></a>
  </div>
  <a href="https://www.google.com/search?q=x">nav</a>
  <a href="https://accounts.google.com/login">skip</a>
</body></html>
"""


@pytest.fixture
def page():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Chromium 起動不可: {exc}")
        pg = browser.new_page()
        try:
            yield pg
        finally:
            browser.close()


def test_format_numbered_urls() -> None:
    assert format_numbered_urls(["https://a", "https://b"]) == "[1]https://a [2]https://b"


def test_filter_drops_google_ui() -> None:
    raw = [
        "https://www.google.com/search?q=google",
        "https://www.youtube.com/watch?v=abc",
        "https://maps.google.com/maps?q=x",
        "https://example.com/page",
    ]
    out = filter_organic_urls(
        raw,
        page_url="https://www.google.com/search?q=google",
        limit=10,
    )
    assert out == ["https://www.youtube.com/watch?v=abc", "https://example.com/page"]
    assert not is_organic_candidate("https://www.google.com/webhp")


def test_extract_organic_and_formatted(page) -> None:
    page.set_content(PAGE)
    data = extract_by_result_type(page, "urls", url_limit=10)
    assert data["count"] == 2
    assert "urls" not in data
    assert data["formatted"] == "[1]https://a.example/x [2]https://b.example/y"
    assert data["items"][0] == {"n": 1, "url": "https://a.example/x"}

    title = extract_by_result_type(page, "title")
    assert title["title"] == "Extract Me"

    text = extract_by_result_type(page, "text")
    assert "Hello" in (text.get("text") or "")


def test_preview_and_satisfied_use_formatted() -> None:
    intent = normalize_intent("urlリストを取得")
    data = {
        "count": 2,
        "formatted": "[1]https://a.example [2]https://b.example",
        "items": [{"n": 1, "url": "https://a.example"}, {"n": 2, "url": "https://b.example"}],
    }
    assert intent_result_satisfied(intent, [data])
    store = CollectionStore()
    store.add(data)
    preview = store.preview()
    assert "[1] https://a.example" in preview
    assert "[2] https://b.example" in preview
    assert "urls" not in preview
