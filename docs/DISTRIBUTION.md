# 配布について（Phase 10）

## 成果物

`scripts/build_windows.ps1` を実行すると、次ができます。

```
dist/release/
  browser-assistant.exe
  .env.example
```

**実キー入りの `.env` はビルドに含めません。** 利用者が exe と同じフォルダに自分で作ります。

## Chrome / Chromium の方針

| 項目 | 方針 |
|------|------|
| ブラウザ | **システムの Google Chrome を利用**（CDP 接続） |
| 同梱 | Chromium / Chrome は **同梱しない** |
| 指定 | 自動検出。だめなら `.env` の `CHROME_PATH` に `chrome.exe` フルパス |
| プロファイル | exe 横の `user-data/`（`USER_DATA_DIR`）でログイン維持 |

理由: 配布サイズと更新追従を抑え、普段のログイン済み Chrome プロファイル運用に合わせるため。

## 利用者の初回手順

1. `dist/release` フォルダを任意の場所へ置く
2. `.env.example` を `.env` にコピー
3. [Google AI Studio](https://aistudio.google.com/) 等で取得した `GEMINI_API_KEY` を記入
4. Google Chrome がインストールされていることを確認
5. `browser-assistant.exe --ui` または `browser-assistant.exe --check-config`
6. 起動した Chrome に必要なサイトへ**事前ログイン**
7. 指示を実行

## 起動チェック

アプリは起動時に次を確認し、問題があれば案内を出します（サイレントクラッシュしません）。

- `.env` の有無
- `GEMINI_API_KEY` の有無
- Chrome 実行ファイルの検出

## 開発者向けビルド

```powershell
.\.venv\Scripts\Activate.ps1
powershell -File scripts/build_windows.ps1
powershell -File scripts/smoke_exe.ps1
```
