"""日本語指示 → NormalizedIntent."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from browser_assistant.intent.result_type import (
    ResultType,
    ResultTypeSource,
    estimate_result_type,
    load_result_phrases,
)
from browser_assistant.intent.sites import (
    extract_explicit_url,
    extract_query_hint,
    load_sites,
    match_site,
    normalize_text,
)

logger = logging.getLogger("browser_assistant.intent")

UrlSource = Literal["explicit", "alias", "none"]


@dataclass
class NormalizedIntent:
    raw_instruction: str
    normalized_instruction: str
    target_url: str | None = None
    url_source: UrlSource = "none"
    site_alias: str | None = None
    result_type: ResultType = "none"
    result_type_source: ResultTypeSource = "none"
    query_hint: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def log_lines(self) -> list[str]:
        return [
            f"target_url={self.target_url} source={self.url_source} site={self.site_alias}",
            f"result_type={self.result_type} source={self.result_type_source}",
            f"query_hint={self.query_hint}",
        ]


def _build_normalized_instruction(intent: NormalizedIntent) -> str:
    parts: list[str] = []
    if intent.target_url:
        alias = f"（alias={intent.site_alias}）" if intent.site_alias else ""
        parts.append(f"サイト {intent.target_url} {alias}を開き、".replace("  ", " "))
    if intent.query_hint:
        parts.append(f"クエリ「{intent.query_hint}」で検索し、")
    if intent.result_type != "none":
        label = {
            "urls": "リンクURL一覧（result_type=urls）",
            "title": "ページタイトル（result_type=title）",
            "text": "ページ本文・要約（result_type=text）",
        }.get(intent.result_type, intent.result_type)
        parts.append(f"結果として {label} を取得する。")
    parts.append(f"元指示: {intent.raw_instruction}")
    return "".join(parts) if len(parts) > 1 else intent.raw_instruction


def normalize_intent(
    instruction: str,
    *,
    app_dir: Path | None = None,
    sites_path: Path | None = None,
    phrases_path: Path | None = None,
    default_result_type: ResultType = "urls",
) -> NormalizedIntent:
    """ルールで指示を明確化する."""
    raw = (instruction or "").strip()
    notes: list[str] = []

    if sites_path is None and app_dir is not None:
        sites_path = app_dir / "aliases" / "sites.json"
    if phrases_path is None and app_dir is not None:
        phrases_path = app_dir / "aliases" / "result_phrases.json"

    sites = load_sites(sites_path)
    phrases = load_result_phrases(phrases_path)

    explicit = extract_explicit_url(raw)
    target_url: str | None = None
    url_source: UrlSource = "none"
    site_alias: str | None = None
    matched_kw: str | None = None

    if explicit:
        target_url = explicit
        url_source = "explicit"
        notes.append(f"明示URLを採用: {explicit}")
    else:
        site, matched_kw = match_site(raw, sites)
        if site is not None:
            target_url = site.url
            url_source = "alias"
            site_alias = site.id
            notes.append(f"サイト辞書ヒット: {site.id} keyword={matched_kw}")

    result_type, result_source = estimate_result_type(
        raw,
        phrases=phrases,
        default_when_fetch=default_result_type,
    )

    query_hint = extract_query_hint(raw, site_keyword=matched_kw)

    intent = NormalizedIntent(
        raw_instruction=raw,
        normalized_instruction=raw,  # 後で上書き
        target_url=target_url,
        url_source=url_source,
        site_alias=site_alias,
        result_type=result_type,
        result_type_source=result_source,
        query_hint=query_hint,
        notes=notes,
    )
    intent.normalized_instruction = _build_normalized_instruction(intent)

    for line in intent.log_lines():
        logger.info("[intent] %s", line)
    for note in notes:
        logger.info("[intent] %s", note)

    return intent


def intent_result_satisfied(intent: NormalizedIntent, extracts: list[dict[str, Any]]) -> bool:
    """result_type に対して十分な extract があるか."""
    if intent.result_type == "none":
        return True
    if not extracts:
        return False
    last = extracts[-1]
    if intent.result_type == "urls":
        page_url = str(last.get("page_url") or last.get("url") or "")
        # 検索クエリ付き指示なら、検索結果ページ上の抽出であること
        if intent.query_hint and not looks_like_search_results_page(page_url):
            return False
        items = last.get("items")
        if isinstance(items, list) and len(items) >= 1:
            return True
        formatted = str(last.get("formatted") or "").strip()
        if formatted and looks_like_search_results_page(page_url):
            return True
        if formatted and not intent.query_hint:
            return True
        urls = last.get("urls")
        count = last.get("count")
        if isinstance(urls, list) and len(urls) >= 1:
            return True
        if isinstance(count, int) and count >= 1 and (
            not intent.query_hint or looks_like_search_results_page(page_url)
        ):
            return True
        return False
    if intent.result_type == "title":
        return bool(str(last.get("title") or "").strip())
    if intent.result_type == "text":
        body = str(last.get("text") or last.get("text_preview") or "").strip()
        if not body:
            return False
        # 「最初のurlを開いて内容を…」なら、検索結果ページ上の抽出だけでは未達
        raw = intent.raw_instruction or ""
        if intent.query_hint and any(w in raw for w in ("開いて", "開く")):
            page_url = str(last.get("url") or last.get("page_url") or "")
            if looks_like_search_results_page(page_url):
                return False
        return True
    return True


def looks_like_search_results_page(url: str) -> bool:
    """Google/Bing 等の検索結果URLか."""
    u = (url or "").lower()
    if not u:
        return False
    if "/search" in u:
        return True
    if any(h in u for h in ("google.", "bing.", "duckduckgo.", "yahoo.")) and (
        "q=" in u or "p=" in u or "query=" in u
    ):
        return True
    return False


PLANNER_RULES = [
    "intent.target_url がある場合、手順の早い段階でその URL へ goto する",
    "current_url が似ていても、指示に検索・再取得・再オープンがあるなら省略しない",
    "query_hint がある場合、検索送信は type のあと Enter を使うか、"
    "goto で https://www.google.com/search?q=クエリ のように直接結果へ行ってよい",
    "result_type=urls の場合、最後の extract は検索結果ページ上でリンクURL一覧を取得する",
    "result_type=title の場合、extract はタイトル取得を目的にする",
    "result_type=text の場合、指示どおりページを開いたあと、そのページの本文・内容を extract する"
    "（検索結果ページのURL一覧は取らない）",
    "「最初のurlを開いて内容を教えて」は click → extract(text) → done の流れにする",
    "完了(done)は result_type の成果が取れてからにする",
    "1ステップに複数操作を詰め込まない",
]
