"""内蔵デフォルトのサイト辞書・結果タイプ言い回し."""

from __future__ import annotations

from typing import Any

DEFAULT_SITES: list[dict[str, Any]] = [
    {
        "id": "google",
        "keywords": ["google", "グーグル", "ぐーぐる"],
        "url": "https://www.google.com",
        "enabled": True,
    },
    {
        "id": "yahoo_jp",
        "keywords": ["yahoo", "ヤフー", "yahoo japan", "yahoo.co.jp"],
        "url": "https://www.yahoo.co.jp",
        "enabled": True,
    },
    {
        "id": "bing",
        "keywords": ["bing", "ビング"],
        "url": "https://www.bing.com",
        "enabled": True,
    },
]

# 優先度の目安: 最終成果の言い回しを見る（url単独より「内容を教えて」を優先）
DEFAULT_RESULT_PHRASES: dict[str, list[str]] = {
    "urls": ["urlリスト", "URLリスト", "url一覧", "URL一覧", "リンク一覧", "リンクリスト", "アドレス一覧"],
    "title": ["タイトル", "見出し", "title"],
    "text": ["内容", "本文", "テキスト", "要約", "抜粋", "記事", "教えて", "説明して"],
}

FETCH_HINTS = ("取得", "収集", "抽出", "取って", "とって", "ピックアップ", "スクレイピング")
