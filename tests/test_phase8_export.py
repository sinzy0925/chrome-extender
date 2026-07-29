"""Phase 8: 収集・エクスポートのテスト."""

from __future__ import annotations

from pathlib import Path

import pytest

from browser_assistant.export import CollectionStore, ExportError, export_collection


def test_preview_shows_rows() -> None:
    store = CollectionStore()
    store.add({"title": "Example", "url": "https://example.com"})
    store.add({"title": "Second", "url": "https://example.com/2"})
    text = store.preview()
    assert "収集件数: 2" in text
    assert "Example" in text
    assert "Second" in text


def test_json_export_matches_preview_data(tmp_path: Path) -> None:
    store = CollectionStore()
    rows = [
        {"title": "A", "url": "https://a.example"},
        {"title": "B", "url": "https://b.example"},
    ]
    store.extend(rows)
    path = store.save_json(tmp_path / "out.json")
    loaded = path.read_text(encoding="utf-8")
    assert '"title": "A"' in loaded
    assert '"title": "B"' in loaded
    assert store.to_json_text() == loaded


def test_csv_has_utf8_bom_and_rows(tmp_path: Path) -> None:
    store = CollectionStore()
    store.extend(
        [
            {"タイトル": "日本語", "url": "https://example.com"},
            {"タイトル": "二件目", "url": "https://example.com/2"},
        ]
    )
    path = store.save_csv(tmp_path / "out.csv", with_bom=True)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = path.read_text(encoding="utf-8-sig")
    assert "タイトル" in text
    assert "日本語" in text
    assert "二件目" in text


def test_empty_export_raises() -> None:
    store = CollectionStore()
    with pytest.raises(ExportError, match="0 件"):
        store.save_json(Path("unused.json"))
    with pytest.raises(ExportError, match="0 件"):
        export_collection(store, json_path="x.json")


def test_multi_step_accumulation_exported(tmp_path: Path) -> None:
    store = CollectionStore()
    store.add({"step": 1, "title": "one"})
    store.add({"step": 2, "title": "two"})
    store.add({"step": 3, "title": "three", "extra": "x"})
    assert len(store) == 3

    saved = export_collection(
        store,
        json_path=tmp_path / "all.json",
        csv_path=tmp_path / "all.csv",
    )
    json_text = Path(saved["json"]).read_text(encoding="utf-8")
    csv_text = Path(saved["csv"]).read_text(encoding="utf-8-sig")
    assert "one" in json_text and "two" in json_text and "three" in json_text
    assert "one" in csv_text and "two" in csv_text and "three" in csv_text
    assert "extra" in csv_text
