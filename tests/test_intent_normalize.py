"""意図正規化（サイト辞書・結果タイプ）の単体テスト."""

from __future__ import annotations

import json
from pathlib import Path

from browser_assistant.intent.normalize import intent_result_satisfied, normalize_intent
from browser_assistant.intent.result_type import estimate_result_type
from browser_assistant.intent.sites import extract_explicit_url, load_sites, match_site


def test_explicit_url_beats_alias() -> None:
    intent = normalize_intent("https://example.com を開いてタイトルを取得")
    assert intent.url_source == "explicit"
    assert intent.target_url and intent.target_url.startswith("https://example.com")
    assert intent.result_type == "title"


def test_google_alias_and_urls() -> None:
    intent = normalize_intent("googleでgoogleと検索して、結果のurlリストを取得して")
    assert intent.url_source == "alias"
    assert intent.site_alias == "google"
    assert intent.target_url == "https://www.google.com"
    assert intent.result_type == "urls"
    assert intent.result_type_source == "phrase"
    assert intent.query_hint == "google"
    assert "https://www.google.com" in intent.normalized_instruction


def test_yahoo_alias() -> None:
    intent = normalize_intent("ヤフーを開いて")
    assert intent.site_alias == "yahoo_jp"
    assert intent.target_url == "https://www.yahoo.co.jp"


def test_result_type_defaults_on_fetch_word() -> None:
    intent = normalize_intent("このページから情報を取得して")
    assert intent.result_type == "urls"
    assert intent.result_type_source == "default"


def test_result_type_none_for_click_only() -> None:
    intent = normalize_intent("送信ボタンをクリックして")
    assert intent.result_type == "none"


def test_title_and_text_phrases() -> None:
    assert estimate_result_type("ページのタイトルを取って")[0] == "title"
    assert estimate_result_type("本文の要約を取得")[0] == "text"


def test_open_url_then_content_is_text_not_urls() -> None:
    """「urlを開いて内容を教えて」は urls ではなく text。"""
    intent = normalize_intent(
        "googleでaaaと検索して、最初のurlを開いて、内容を教えて"
    )
    assert intent.result_type == "text"
    assert intent.query_hint == "aaa"
    assert estimate_result_type(
        "最初のurlを開いて、内容を教えて"
    )[0] == "text"


def test_url_list_still_urls() -> None:
    assert estimate_result_type("結果のurlリストを取得して")[0] == "urls"
    assert estimate_result_type("リンク一覧を取って")[0] == "urls"


def test_intent_result_satisfied_text_requires_opened_page() -> None:
    intent = normalize_intent(
        "googleでaaaと検索して、最初のurlを開いて、内容を教えて"
    )
    assert intent.result_type == "text"
    # SERP 上の本文だけでは不合格
    assert not intent_result_satisfied(
        intent,
        [
            {
                "text": "検索結果のプレビュー",
                "url": "https://www.google.com/search?q=aaa",
                "title": "aaa - Google 検索",
            }
        ],
    )
    # 開いた先のページなら合格
    assert intent_result_satisfied(
        intent,
        [
            {
                "text": "アーティスト情報の本文です",
                "url": "https://avex.jp/aaa/",
                "title": "AAA",
            }
        ],
    )


def test_sites_json_load(tmp_path: Path) -> None:
    path = tmp_path / "sites.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "sites": [
                    {
                        "id": "custom",
                        "keywords": ["みょうサイト"],
                        "url": "https://custom.example/",
                        "enabled": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sites = load_sites(path)
    site, kw = match_site("みょうサイトを開く", sites)
    assert site is not None
    assert site.id == "custom"
    assert kw == "みょうサイト"


def test_intent_result_satisfied_urls() -> None:
    intent = normalize_intent("url一覧を取得")
    assert intent.result_type == "urls"
    assert not intent_result_satisfied(intent, [])
    assert not intent_result_satisfied(intent, [{"urls": [], "count": 0}])
    assert intent_result_satisfied(
        intent,
        [{"formatted": "[1]https://a.example", "items": [{"n": 1, "url": "https://a.example"}], "count": 1}],
    )


def test_search_intent_requires_serp_page() -> None:
    intent = normalize_intent("googleでgoogleと検索して、結果のurlリストを取得して")
    assert intent.query_hint == "google"
    # トップページ上の1件は不合格
    assert not intent_result_satisfied(
        intent,
        [
            {
                "count": 1,
                "page_url": "https://www.google.com/",
                "formatted": "[1]https://about.google/",
                "items": [{"n": 1, "url": "https://about.google/"}],
            }
        ],
    )
    # 検索結果ページなら合格
    assert intent_result_satisfied(
        intent,
        [
            {
                "count": 1,
                "page_url": "https://www.google.com/search?q=google",
                "formatted": "[1]https://youtube.com/x",
                "items": [{"n": 1, "url": "https://youtube.com/x"}],
            }
        ],
    )


def test_repo_aliases_file_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "aliases" / "sites.json").is_file()
    assert (root / "aliases" / "result_phrases.json").is_file()
    assert extract_explicit_url("go https://x.test/a へ") == "https://x.test/a"
