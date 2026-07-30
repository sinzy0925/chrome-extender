"""サイト辞書のロードとマッチ."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from browser_assistant.intent.defaults import DEFAULT_SITES

logger = logging.getLogger("browser_assistant.intent.sites")

_EXPLICIT_URL_RE = re.compile(r"https?://[^\s　]+", re.I)


@dataclass(frozen=True)
class SiteAlias:
    id: str
    keywords: tuple[str, ...]
    url: str
    enabled: bool = True


def normalize_text(text: str) -> str:
    """全角英数を半角にし、空白を圧縮する."""
    out: list[str] = []
    for ch in text or "":
        code = ord(ch)
        if code == 0x3000:  # ideographic space
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    collapsed = re.sub(r"\s+", " ", "".join(out)).strip()
    return collapsed


def extract_explicit_url(instruction: str) -> str | None:
    m = _EXPLICIT_URL_RE.search(instruction or "")
    if not m:
        return None
    return m.group(0).rstrip(")、。,.]」』\"'")


def _parse_site_dicts(raw: list[Any]) -> list[SiteAlias]:
    sites: list[SiteAlias] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        url = str(item.get("url") or "").strip()
        kws = item.get("keywords") or []
        if not sid or not url or not isinstance(kws, list):
            continue
        keywords = tuple(str(k).strip() for k in kws if str(k).strip())
        if not keywords:
            continue
        enabled = bool(item.get("enabled", True))
        sites.append(SiteAlias(id=sid, keywords=keywords, url=url, enabled=enabled))
    return sites


def load_sites(path: Path | None = None) -> list[SiteAlias]:
    """YAML相当の JSON 辞書を読む。無ければ内蔵デフォルト."""
    if path is not None and path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sites_raw = data.get("sites") if isinstance(data, dict) else None
            if isinstance(sites_raw, list):
                parsed = _parse_site_dicts(sites_raw)
                if parsed:
                    logger.debug("サイト辞書を読み込みました: %s (%s件)", path, len(parsed))
                    return parsed
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("サイト辞書の読込に失敗したため内蔵を使います: %s", exc)
    return _parse_site_dicts(DEFAULT_SITES)


def match_site(instruction: str, sites: list[SiteAlias]) -> tuple[SiteAlias | None, str | None]:
    """指示からサイトを1つ選ぶ。戻り値: (site, matched_keyword).

    複数ヒット時はより長いキーワード優先。同点なら辞書定義順。
    「Xで」の直前にキーワードがある場合はそのサイトを優先。
    """
    text = normalize_text(instruction)
    lower = text.lower()
    enabled = [s for s in sites if s.enabled]
    if not enabled:
        return None, None

    # 「keywordで」パターンを最優先
    for site in enabled:
        for kw in sorted(site.keywords, key=len, reverse=True):
            kn = normalize_text(kw)
            if not kn:
                continue
            # 英数字は大小無視、日本語はそのまま
            pattern = re.escape(kn) + r"で"
            if re.search(pattern, text, flags=re.I):
                return site, kw

    best: tuple[int, int, SiteAlias, str] | None = None  # (-len, index, site, kw)
    for idx, site in enumerate(enabled):
        for kw in site.keywords:
            kn = normalize_text(kw)
            if not kn:
                continue
            hay = lower if kn.isascii() else text
            needle = kn.lower() if kn.isascii() else kn
            if needle in hay:
                key = (-len(kn), idx)
                if best is None or key < (best[0], best[1]):
                    best = (-len(kn), idx, site, kw)
    if best is None:
        return None, None
    return best[2], best[3]


def extract_query_hint(instruction: str, *, site_keyword: str | None = None) -> str | None:
    """検索クエリ候補をヒューリスティックに取る."""
    text = normalize_text(instruction)
    # 「でXXXと検索」「でXXXを検索」
    m = re.search(r"で\s*(.+?)\s*[とを]検索", text)
    if m:
        q = m.group(1).strip(" 「」『』\"'")
        if q and (site_keyword is None or normalize_text(q).lower() != normalize_text(site_keyword).lower()):
            # 「googleでgoogleと検索」→ クエリは google（サイトと同じでも可）
            return q or None
        if q:
            return q
    m = re.search(r"[「『\"](.+?)[」』\"]\s*[とを]?検索", text)
    if m:
        return m.group(1).strip() or None
    m = re.search(r"(.+?)\s*[とを]検索", text)
    if m:
        q = m.group(1).strip()
        # 先頭のサイト語を落とす
        if site_keyword:
            sk = normalize_text(site_keyword)
            q2 = re.sub(re.escape(sk) + r"で\s*", "", q, count=1, flags=re.I).strip()
            if q2:
                return q2
        return q or None
    return None
