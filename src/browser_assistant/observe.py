"""ページ観察層 — 操作候補の要約（全HTMLは送らない）.

選定方針（仕様）:
devtools-mcp 同梱は必須ではない。Playwright 上で「見える操作可能要素」を
role / name / text 中心に絞って返すことで同等の観察品質を確保する。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("browser_assistant.observe")

SCHEMA_VERSION = "1"
DEFAULT_MAX_CANDIDATES = 40
TEXT_PREVIEW_MAX = 500

# page.evaluate に渡す抽出スクリプト（ブラウザ側）
_EXTRACT_JS = r"""
() => {
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (!style || style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const cssPath = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 4) {
      let part = cur.tagName.toLowerCase();
      if (cur.classList && cur.classList.length) {
        const cls = Array.from(cur.classList).slice(0, 2).map(c => CSS.escape(c)).join('.');
        if (cls) part += '.' + cls;
      }
      const parent = cur.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(cur) + 1})`;
        }
      }
      parts.unshift(part);
      cur = parent;
      if (cur && cur.id) {
        parts.unshift(`#${CSS.escape(cur.id)}`);
        break;
      }
    }
    return parts.join(' > ');
  };

  const interactiveSelector = [
    'a[href]',
    'button',
    'input',
    'select',
    'textarea',
    '[role="button"]',
    '[role="link"]',
    '[role="textbox"]',
    '[role="searchbox"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="combobox"]',
    '[contenteditable="true"]',
    'summary',
  ].join(',');

  const nodes = Array.from(document.querySelectorAll(interactiveSelector));
  const bodyText = (document.body && (document.body.innerText || '')) || '';

  const items = [];
  for (const el of nodes) {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || null;
    const type = el.getAttribute('type') || null;
    const nameAttr = el.getAttribute('name') || null;
    const id = el.id || null;
    const placeholder = el.getAttribute('placeholder') || null;
    const ariaLabel = el.getAttribute('aria-label') || null;
    const title = el.getAttribute('title') || null;
    const href = el.getAttribute('href') || null;
    const text = ((el.innerText || el.textContent || '') + '').replace(/\s+/g, ' ').trim().slice(0, 120);
    const value = (el.value != null ? String(el.value) : '').slice(0, 80);
    const visible = isVisible(el);
    const rect = el.getBoundingClientRect();
    const accessibleName = (ariaLabel || placeholder || title || text || nameAttr || value || id || '').trim().slice(0, 120);

    let score = 0;
    if (visible) score += 50;
    if (accessibleName) score += 20;
    if (tag === 'button' || role === 'button') score += 10;
    if (tag === 'a') score += 8;
    if (tag === 'input' || tag === 'textarea' || tag === 'select') score += 12;
    if (type === 'submit') score += 5;
    if (type === 'hidden') score -= 100;

    items.push({
      tag,
      role,
      type,
      name_attr: nameAttr,
      element_id: id,
      placeholder,
      href: href ? href.slice(0, 200) : null,
      text,
      value: value || null,
      accessible_name: accessibleName || null,
      visible,
      selector: cssPath(el),
      bbox: {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        w: Math.round(rect.width),
        h: Math.round(rect.height),
      },
      score,
    });
  }

  items.sort((a, b) => b.score - a.score);

  return {
    url: location.href,
    title: document.title || '',
    body_text_preview: bodyText.replace(/\s+/g, ' ').trim().slice(0, 500),
    items,
  };
}
"""


@dataclass
class BBox:
    x: int
    y: int
    w: int
    h: int


@dataclass
class Candidate:
    id: str
    tag: str
    role: str | None
    name: str | None
    text: str | None
    input_type: str | None
    placeholder: str | None
    href: str | None
    selector_hints: list[str]
    bbox: BBox | None
    visible: bool


@dataclass
class PageSnapshot:
    url: str
    title: str
    text_preview: str
    observed_at: str


@dataclass
class Observation:
    """Gemini に渡す観察結果（固定スキーマ）."""

    schema_version: str
    url: str
    title: str
    observed_at: str
    candidate_count: int
    truncated: bool
    max_candidates: int
    candidates: list[Candidate]
    snapshot: PageSnapshot
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _selector_hints(item: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    sel = item.get("selector")
    if sel:
        hints.append(sel)
    name = item.get("accessible_name") or ""
    tag = item.get("tag") or "*"
    if name:
        safe = name.replace("'", "\\'")
        hints.append(f"text={name}")
        hints.append(f"{tag}:has-text('{safe}')")
    eid = item.get("element_id")
    if eid:
        hints.append(f"#{eid}")
    # 重複除去（順序維持）
    seen: set[str] = set()
    out: list[str] = []
    for h in hints:
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out[:4]


def observe_page(
    page: Any,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> Observation:
    """Playwright Page から操作候補の要約を取得する."""
    if max_candidates < 1:
        raise ValueError("max_candidates は 1 以上である必要があります")

    raw = page.evaluate(_EXTRACT_JS)
    items = list(raw.get("items") or [])
    truncated = len(items) > max_candidates
    selected = items[:max_candidates]

    candidates: list[Candidate] = []
    for idx, item in enumerate(selected, start=1):
        bbox_raw = item.get("bbox") or {}
        bbox = BBox(
            x=int(bbox_raw.get("x", 0)),
            y=int(bbox_raw.get("y", 0)),
            w=int(bbox_raw.get("w", 0)),
            h=int(bbox_raw.get("h", 0)),
        )
        candidates.append(
            Candidate(
                id=f"e{idx}",
                tag=item.get("tag") or "unknown",
                role=item.get("role"),
                name=item.get("accessible_name"),
                text=item.get("text") or None,
                input_type=item.get("type"),
                placeholder=item.get("placeholder"),
                href=item.get("href"),
                selector_hints=_selector_hints(item),
                bbox=bbox,
                visible=bool(item.get("visible")),
            )
        )

    observed_at = _now_iso()
    url = raw.get("url") or getattr(page, "url", "") or ""
    title = raw.get("title") or ""
    preview = raw.get("body_text_preview") or ""

    notes = [
        "全HTMLではなく操作可能要素の要約のみ",
        "取得方法: Playwright page.evaluate（devtools-mcp 相当の観察）",
    ]
    if truncated:
        notes.append(f"候補を score 上位 {max_candidates} 件に制限しました（元 {len(items)} 件）")

    observation = Observation(
        schema_version=SCHEMA_VERSION,
        url=url,
        title=title,
        observed_at=observed_at,
        candidate_count=len(candidates),
        truncated=truncated,
        max_candidates=max_candidates,
        candidates=candidates,
        snapshot=PageSnapshot(
            url=url,
            title=title,
            text_preview=preview[:TEXT_PREVIEW_MAX],
            observed_at=observed_at,
        ),
        notes=notes,
    )
    logger.info(
        "観察完了: url=%s candidates=%s truncated=%s",
        url,
        observation.candidate_count,
        truncated,
    )
    return observation


def take_snapshot(page: Any) -> PageSnapshot:
    """実行前後比較用の簡易スナップショット."""
    data = page.evaluate(
        """() => ({
          url: location.href,
          title: document.title || '',
          text_preview: ((document.body && document.body.innerText) || '')
            .replace(/\\s+/g, ' ').trim().slice(0, 500)
        })"""
    )
    return PageSnapshot(
        url=data.get("url") or "",
        title=data.get("title") or "",
        text_preview=(data.get("text_preview") or "")[:TEXT_PREVIEW_MAX],
        observed_at=_now_iso(),
    )


def snapshots_differ(before: PageSnapshot, after: PageSnapshot) -> bool:
    return (
        before.url != after.url
        or before.title != after.title
        or before.text_preview != after.text_preview
    )


def save_observation_json(observation: Observation, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(observation.to_json(), encoding="utf-8")
    return path


def get_active_page(browser: Any) -> Any:
    """CDP 接続ブラウザから操作対象ページを返す（無ければ新規）."""
    for context in browser.contexts:
        for page in context.pages:
            if not page.is_closed():
                return page
    if browser.contexts:
        return browser.contexts[0].new_page()
    context = browser.new_context()
    return context.new_page()


_HTML_TAG_RE = re.compile(r"<html|<body|<div", re.I)


def looks_like_full_html(payload: str | dict[str, Any]) -> bool:
    """観察結果が全HTML投げになっていないかの簡易チェック."""
    if isinstance(payload, dict):
        text = json.dumps(payload, ensure_ascii=False)
    else:
        text = payload
    if "schema_version" in text and "candidates" in text:
        # 要約JSONでもタグ名は含むので、巨大な raw html キーが無いか見る
        if isinstance(payload, dict) and any(
            k in payload for k in ("html", "outerHTML", "innerHTML", "raw_html")
        ):
            return True
        return False
    return bool(_HTML_TAG_RE.search(text)) and len(text) > 5000
