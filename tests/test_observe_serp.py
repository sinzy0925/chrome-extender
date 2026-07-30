"""SERP 観察: h3 オーガニックを候補上位に載せる."""

from __future__ import annotations

import pytest

from browser_assistant.observe import observe_page

SERP_HTML = """
<!doctype html>
<html><head><title>aaa - Google 検索</title></head>
<body>
  <a href="https://www.google.co.jp/intl/ja/about/products">Google アプリ</a>
  <button type="submit">検索</button>
  <textarea aria-label="検索">aaa</textarea>
  <div role="button">設定</div>
  <div role="button">ツール</div>
  <div role="button">この結果について</div>
  <div role="button">共有</div>
  <div role="button">音声で検索</div>
  <div role="button">画像で検索</div>
  <div role="button">もっと見る</div>
  <div id="rso">
    <a href="https://avex.jp/aaa/"><h3>AAA（トリプル・エー）OFFICIAL WEBSITE</h3></a>
    <a href="https://ja.wikipedia.org/wiki/AAA"><h3>AAA (音楽グループ)</h3></a>
    <a href="https://open.spotify.com/artist/xxx"><h3>AAA</h3></a>
  </div>
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


def test_serp_h3_organic_are_prioritized_in_candidates(page) -> None:
    page.set_content(SERP_HTML)
    # わざと上限を小さくして、優先なしだと結果リンクが落ちる状況を作る
    obs = observe_page(page, max_candidates=5)
    hrefs = [c.href for c in obs.candidates if c.href]
    assert "https://avex.jp/aaa/" in hrefs
    assert any("wikipedia.org" in (h or "") for h in hrefs)
    assert any("SERPオーガニック" in n for n in obs.notes)
    # 先頭付近にオーガニックがある
    assert obs.candidates[0].href and "http" in obs.candidates[0].href
