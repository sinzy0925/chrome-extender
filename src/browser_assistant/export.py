"""収集結果の蓄積・プレビュー・CSV/JSON エクスポート."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ExportError(Exception):
    """エクスポート不可（空結果など）."""


@dataclass
class CollectionStore:
    """extract 結果を構造化リストとして蓄積する."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def is_empty(self) -> bool:
        return len(self.rows) == 0

    def clear(self) -> None:
        self.rows.clear()

    def add(self, row: dict[str, Any]) -> None:
        if not isinstance(row, dict):
            raise TypeError("row は dict である必要があります")
        self.rows.append(dict(row))

    def extend(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.add(row)

    def preview(self, *, max_rows: int = 50) -> str:
        """コンソール向けの簡易プレビュー."""
        if self.is_empty:
            return "(収集結果なし)"

        # URL一覧（formatted / items）を優先して読みやすく出す
        for row in self.rows:
            formatted = str(row.get("formatted") or "").strip()
            items = row.get("items")
            if formatted:
                lines = [f"収集URL: {row.get('count', '')} 件".strip()]
                # [1]u1 [2]u2 → 1行ずつ
                parts = re.findall(r"\[(\d+)\](\S+)", formatted)
                if parts:
                    for n, u in parts[:max_rows]:
                        lines.append(f"[{n}] {u}")
                    if len(parts) > max_rows:
                        lines.append(f"... 他 {len(parts) - max_rows} 件")
                else:
                    lines.append(formatted)
                return "\n".join(lines)
            if isinstance(items, list) and items:
                lines = [f"収集URL: {len(items)} 件"]
                for it in items[:max_rows]:
                    if isinstance(it, dict):
                        lines.append(f"[{it.get('n')}] {it.get('url')}")
                if len(items) > max_rows:
                    lines.append(f"... 他 {len(items) - max_rows} 件")
                return "\n".join(lines)

        lines = [f"収集件数: {len(self.rows)}"]
        show = self.rows[: max(1, max_rows)]
        for i, row in enumerate(show, start=1):
            compact = json.dumps(row, ensure_ascii=False)
            if len(compact) > 200:
                compact = compact[:197] + "..."
            lines.append(f"  [{i}] {compact}")
        if len(self.rows) > max_rows:
            lines.append(f"  ... 他 {len(self.rows) - max_rows} 件")
        return "\n".join(lines)

    def to_json_text(self, *, indent: int | None = 2) -> str:
        self._ensure_exportable()
        # URL一覧は items（番号付き）を優先して見やすくする
        payload = _flatten_rows_for_export(self.rows)
        return json.dumps(payload, ensure_ascii=False, indent=indent)

    def save_json(self, path: Path) -> Path:
        self._ensure_exportable()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json_text(), encoding="utf-8")
        return path

    def to_csv_text(self) -> str:
        """Excel 向けに UTF-8 BOM 付き CSV 文字列を返す."""
        self._ensure_exportable()
        flat_rows = _flatten_rows_for_export(self.rows)
        fieldnames = _collect_fieldnames(flat_rows)
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in flat_rows:
            writer.writerow({k: _cell_value(row.get(k)) for k in fieldnames})
        # BOM はファイル保存時に付与（文字列比較しやすいようここでは本文のみ）
        return buf.getvalue()

    def save_csv(self, path: Path, *, with_bom: bool = True) -> Path:
        """CSV 保存。デフォルトは UTF-8 BOM 付き（Excel 文字化け対策）."""
        self._ensure_exportable()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = self.to_csv_text()
        encoding = "utf-8-sig" if with_bom else "utf-8"
        path.write_text(text, encoding=encoding)
        return path

    def _ensure_exportable(self) -> None:
        if self.is_empty:
            raise ExportError(
                "収集結果が 0 件のためダウンロードできません。"
                " extract ステップでデータが取れたか確認してください。"
            )


def _flatten_rows_for_export(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """items があれば 1URL=1行に展開する."""
    flat: list[dict[str, Any]] = []
    for row in rows:
        items = row.get("items")
        if isinstance(items, list) and items:
            for it in items:
                if isinstance(it, dict) and it.get("url"):
                    flat.append({"n": it.get("n"), "url": it.get("url")})
            continue
        flat.append(dict(row))
    return flat or list(rows)


def _collect_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                names.append(str(key))
    return names or ["value"]


def _cell_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def export_collection(
    store: CollectionStore,
    *,
    json_path: str | Path | None = None,
    csv_path: str | Path | None = None,
) -> dict[str, str]:
    """指定された形式だけ保存し、保存先パスを返す."""
    if store.is_empty:
        raise ExportError(
            "収集結果が 0 件のためダウンロードできません。"
            " extract ステップでデータが取れたか確認してください。"
        )
    if not json_path and not csv_path:
        raise ExportError("保存先（JSON または CSV）を指定してください。")

    saved: dict[str, str] = {}
    if json_path:
        saved["json"] = str(store.save_json(Path(json_path)))
    if csv_path:
        saved["csv"] = str(store.save_csv(Path(csv_path), with_bom=True))
    return saved
