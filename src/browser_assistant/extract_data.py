"""extract 結果タイプ別のページからのデータ取得."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from browser_assistant.intent.result_type import ResultType
from browser_assistant.observe import take_snapshot

logger = logging.getLogger("browser_assistant.extract")

DEFAULT_URL_LIMIT = 50

# Google SERP のオーガニック結果（h3 付きリンク）を優先
_EXTRACT_ORGANIC_JS = """
(limit) => {
  const out = [];
  const seen = new Set();
  const push = (href) => {
    if (!href || (!href.startsWith("http://") && !href.startsWith("https://"))) return;
    if (seen.has(href)) return;
    seen.add(href);
    out.push(href);
  };

  const roots = [
    document.querySelector("#rso"),
    document.querySelector("#search"),
    document.querySelector("#center_col"),
  ].filter(Boolean);

  const scopes = roots.length ? roots : [document];
  for (const root of scopes) {
    for (const a of root.querySelectorAll("a[href]")) {
      if (!a.querySelector("h3")) continue;
      push(a.href);
      if (out.length >= limit) return out;
    }
  }

  // フォールバック: 一般リンク（後段 Python でフィルタ）
  if (out.length === 0) {
    for (const a of document.querySelectorAll("a[href]")) {
      push(a.href);
      if (out.length >= limit * 3) break;
    }
  }
  return out;
}
"""

_SEARCH_HOST_RE = re.compile(
    r"(^|\.)(google\.[^/]+|google\.co\.[^/]+|bing\.com|yahoo\.co\.jp|yahoo\.com|duckduckgo\.com)$",
    re.I,
)

_SKIP_PATH_HINTS = (
    "/search",
    "/webhp",
    "/imghp",
    "/maps",
    "/travel/",
    "/url?",
    "/preferences",
    "/advanced_search",
    "/setprefs",
    "/intl/",
    "accounts.",
    "support.",
    "policies.",
    "login.",
    "signin.",
    "takeout.",
)


def format_numbered_urls(urls: list[str]) -> str:
    """[1]url1 [2]url2 形式（スペース区切り）."""
    return " ".join(f"[{i}]{u}" for i, u in enumerate(urls, start=1))


def format_numbered_urls_lines(urls: list[str]) -> str:
    """1行1件の番号付き（ログ・プレビュー向け）."""
    return "\n".join(f"[{i}] {u}" for i, u in enumerate(urls, start=1))


def _unwrap_google_redirect(url: str) -> str:
    try:
        parsed = urlparse(url)
        if "google." in parsed.netloc and parsed.path.startswith("/url"):
            qs = parse_qs(parsed.query)
            for key in ("q", "url"):
                vals = qs.get(key) or []
                if vals and str(vals[0]).startswith("http"):
                    return unquote(str(vals[0]))
    except Exception:  # noqa: BLE001
        pass
    return url


def _is_search_engine_host(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    if not host:
        return True
    if host in {"google.com", "www.google.com", "google.co.jp", "www.google.co.jp"}:
        return True
    if host.endswith(".google.com") or host.endswith(".google.co.jp"):
        return True
    return bool(_SEARCH_HOST_RE.search(host))


def is_organic_candidate(url: str, *, page_host: str | None = None) -> bool:
    """検索結果として残す外部リンクか."""
    url = _unwrap_google_redirect((url or "").strip())
    if not url.startswith("http"):
        return False
    lower = url.lower()
    for hint in _SKIP_PATH_HINTS:
        if hint in lower:
            # /url? は unwrap 後に再判定
            if hint == "/url?":
                continue
            if _is_search_engine_host(urlparse(url).netloc):
                return False
    try:
        host = urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return False
    if _is_search_engine_host(host):
        return False
    # ページと同じ検索エンジンホストは除外済み。同一ページの自サイトは許容。
    return True


def filter_organic_urls(
    raw_urls: list[str],
    *,
    page_url: str,
    limit: int,
) -> list[str]:
    """オーガニック寄りのURLだけに絞る."""
    page_host = urlparse(page_url).netloc if page_url else None
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_urls:
        url = _unwrap_google_redirect(str(raw))
        if url in seen:
            continue
        if not is_organic_candidate(url, page_host=page_host):
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def extract_by_result_type(
    page: Any,
    result_type: ResultType | str | None,
    *,
    extract_fields: list[str] | None = None,
    url_limit: int = DEFAULT_URL_LIMIT,
) -> dict[str, Any]:
    """result_type に応じた構造化データを返す."""
    snap = take_snapshot(page)
    rtype = (result_type or "none").strip().lower()

    if rtype == "urls":
        raw_urls: list[str] = []
        try:
            raw = page.evaluate(_EXTRACT_ORGANIC_JS, max(1, url_limit))
            if isinstance(raw, list):
                raw_urls = [str(u) for u in raw]
        except Exception as exc:  # noqa: BLE001
            logger.warning("URL一覧の取得に失敗: %s", exc)

        urls = filter_organic_urls(raw_urls, page_url=snap.url, limit=max(1, url_limit))
        items = [{"n": i, "url": u} for i, u in enumerate(urls, start=1)]
        formatted = format_numbered_urls(urls)
        logger.info("URL一覧(オーガニック): count=%s %s", len(urls), formatted[:500])
        return {
            "count": len(urls),
            "page_url": snap.url,
            "title": snap.title,
            "formatted": formatted,
            "items": items,
        }

    if rtype == "title":
        return {"title": snap.title, "url": snap.url}

    if rtype == "text":
        body = snap.text_preview
        try:
            raw = page.evaluate(
                """() => {
                  const t = ((document.body && document.body.innerText) || '')
                    .replace(/\\s+/g, ' ').trim();
                  return t.slice(0, 3000);
                }"""
            )
            if isinstance(raw, str) and raw.strip():
                body = raw.strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("本文取得に失敗したためプレビューを使います: %s", exc)
        logger.info("本文抽出: title=%s chars=%s", snap.title, len(body))
        return {
            "text": body,
            "url": snap.url,
            "title": snap.title,
        }

    # none / 従来互換
    data: dict[str, Any] = {
        "url": snap.url,
        "title": snap.title,
        "text_preview": snap.text_preview,
    }
    fields = extract_fields or ["url", "title", "text_preview"]
    filtered = {k: data.get(k) for k in fields if k in data}
    return filtered or data
