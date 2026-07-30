"""結果タイプ（urls / title / text / none）の推定."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from browser_assistant.intent.defaults import DEFAULT_RESULT_PHRASES, FETCH_HINTS
from browser_assistant.intent.sites import normalize_text

logger = logging.getLogger("browser_assistant.intent.result_type")

ResultType = Literal["urls", "title", "text", "none"]
ResultTypeSource = Literal["phrase", "default", "none"]

_PRIORITY = ("urls", "title", "text")

# 「内容を教えて」系は、途中の「urlを開いて」より最終成果を優先する
_CONTENT_GOAL_HINTS = (
    "内容",
    "本文",
    "要約",
    "抜粋",
    "記事",
    "教えて",
    "説明して",
    "何が書いて",
    "どんな内容",
    "ページの内容",
)

# URL一覧が最終成果であることの強い手がかり
_URL_LIST_HINTS = (
    "urlリスト",
    "url一覧",
    "urlのリスト",
    "urlの一覧",
    "リンク一覧",
    "リンクリスト",
    "リンクの一覧",
    "リンクのリスト",
    "アドレス一覧",
)


def load_result_phrases(path: Path | None = None) -> dict[str, list[str]]:
    if path is not None and path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "phrases" in data and isinstance(data["phrases"], dict):
                out: dict[str, list[str]] = {}
                for key in _PRIORITY:
                    vals = data["phrases"].get(key) or []
                    if isinstance(vals, list):
                        out[key] = [str(v) for v in vals if str(v).strip()]
                if out:
                    return out
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("結果タイプ辞書の読込に失敗したため内蔵を使います: %s", exc)
    return {k: list(v) for k, v in DEFAULT_RESULT_PHRASES.items()}


def _has_any(haystack: str, needles: tuple[str, ...] | list[str]) -> bool:
    lower = haystack.lower()
    for n in needles:
        nn = normalize_text(n)
        if not nn:
            continue
        if nn.isascii():
            if nn.lower() in lower:
                return True
        elif nn in haystack:
            return True
    return False


def _looks_like_url_list_goal(text: str) -> bool:
    if _has_any(text, _URL_LIST_HINTS):
        return True
    lower = text.lower()
    # 「結果のurl」「urlを取得/収集」など一覧目的
    if ("url" in lower or "リンク" in text) and any(
        w in text for w in ("一覧", "リスト", "取得", "収集", "抽出")
    ):
        # 「urlを開いて」だけの取得は除外（開く＋内容が別にある場合は下で text 優先）
        if "開いて" in text or "開く" in text:
            return False
        return True
    return False


def estimate_result_type(
    instruction: str,
    *,
    phrases: dict[str, list[str]] | None = None,
    default_when_fetch: ResultType = "urls",
) -> tuple[ResultType, ResultTypeSource]:
    """言い回しから結果タイプを決める.

    「最初のurlを開いて、内容を教えて」のように url と内容が同居する場合は
    最終成果（内容=text）を優先する。
    """
    text = normalize_text(instruction)
    table = phrases or load_result_phrases()

    content_goal = _has_any(text, _CONTENT_GOAL_HINTS) or _has_any(
        text, table.get("text") or []
    )
    url_list_goal = _looks_like_url_list_goal(text)

    if content_goal and not url_list_goal:
        return "text", "phrase"
    if url_list_goal and not content_goal:
        return "urls", "phrase"
    # 両方ある場合: 「開いて…内容」なら text、「urlリストと内容」なら urls を優先しないで text
    if content_goal and url_list_goal:
        if any(w in text for w in ("開いて", "開く", "をクリック")):
            return "text", "phrase"
        return "urls", "phrase"

    hits: list[str] = []
    lower = text.lower()
    for rtype in _PRIORITY:
        for phrase in table.get(rtype) or []:
            p = normalize_text(phrase)
            if not p:
                continue
            # 単独の "url" は「urlを開いて」用途が多いので、一覧語なしでは urls ヒットにしない
            if rtype == "urls" and p.lower() in {"url"} and not url_list_goal:
                continue
            hay = lower if p.isascii() else text
            needle = p.lower() if p.isascii() else p
            if needle in hay:
                hits.append(rtype)
                break
    if hits:
        for rtype in _PRIORITY:
            if rtype in hits:
                return rtype, "phrase"  # type: ignore[return-value]

    if any(h in text for h in FETCH_HINTS):
        return default_when_fetch, "default"

    return "none", "none"
