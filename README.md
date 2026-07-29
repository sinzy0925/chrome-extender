# 日本語指示ブラウザ助手

Windows用の日本語指示ブラウザ助手です。  
URLを開いて情報収集したり、画面操作を支援します。  
AIが手順を分解し、1手ずつ要素を確認してから実行します。

> **ステータス:** MVP（Phase 0〜11）完了  
> 詳細仕様: [docs/MVP_SPEC.md](docs/MVP_SPEC.md)  
> 配布手順: [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md)

---

## 製品定義（MVP）

- Windows用の日本語指示ブラウザ助手
- URLを開いて情報収集したり、画面操作を支援します
- AIが手順を分解し、1手ずつ要素を確認してから実行します
- ログインが必要なサイトは、**起動した Chrome に先にログイン**してください
- **事前ログインがない状態では、ログイン必須ページの操作・情報収集はできません**
- 削除・購入・送信などは自動では確定せず確認します
- すべてのサイトでの成功は保証しません

---

## 注意事項・自己責任

**サイトによっては自動化・スクレイピングを禁止しています。**  
利用規約・法令を確認のうえ、**自己責任でご利用ください。**

補足:

- 本ツールはブラウザ操作の支援であり、サイト利用規約の遵守は利用者の責任です
- CAPTCHA・二段階認証の突破はしません
- 公開・商用利用の前に、対象サイトの規約を確認してください
- Chrome拡張連携はMVP後の予定です（本アプリが本体）

免責・自己責任の詳細は [docs/DISCLAIMER.md](docs/DISCLAIMER.md) を参照してください。  
ライセンスはリポジトリ直下の [LICENSE](LICENSE) です。

---

## 保証範囲（要約）

| 区分 | 内容 |
|------|------|
| 条件付きで目指す | 事前ログイン済み Chrome 上での分解→観察→1手実行、収集と CSV/JSON 出力、危険操作の確認、Stop |
| 保証しない | あらゆるサイトでの高精度成功、複雑な SPA / iframe 等 |
| 対象外 | CAPTCHA/2FA突破、未ログインでの会員ページ取得、危険操作の全自動、Mac/Linux版 |

---

## 初回の使い方（推奨順）

### A. exe で使う場合

1. `dist/release/`（または配布ZIP）を任意の場所に置く
2. `.env.example` を `.env` にコピーし、自分で取得した `GEMINI_API_KEY` を記入する
3. Google Chrome がインストール済みであることを確認する（**Chromeは同梱しません**）
4. `browser-assistant.exe --startup-check` で設定を確認する
5. `browser-assistant.exe --ui`（または `--start-browser`）で起動する
6. **起動した Chrome** に必要なサイトへ事前ログインする
7. UI または `--run "..."` で日本語指示を出す
8. 収集結果はプレビュー後、JSON / CSV で保存できる

### B. ソースから使う場合

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
copy .env.example .env
# .env に GEMINI_API_KEY を記入
python -m browser_assistant --startup-check
python -m browser_assistant --ui
```

### よく使うコマンド

```powershell
python -m browser_assistant --startup-check
python -m browser_assistant --start-browser
python -m browser_assistant --ui
python -m browser_assistant --run "https://example.com を開いてタイトルを収集して" --collect-mode --export-json exports/out.json --export-csv exports/out.csv
python -m browser_assistant --start-browser --close-browser
```

中断は **Stop ボタン** または **Ctrl+C** です。  
危険操作は確認ダイアログ（実行 / スキップ / 中止）が出ます。

APIキーはアプリ（または exe）と同じディレクトリの `.env` に置きます。

```env
# .env.example を参照
GEMINI_API_KEY=your_api_key_here
```

---

## 開発・ビルド

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

python -m browser_assistant --check-config
python -m pytest -m "not integration"

powershell -File scripts/build_windows.ps1
powershell -File scripts/smoke_exe.ps1
```

Gemini API は公式の最新 SDK（`google-genai`）を使います。レガシーの `google-generativeai` は使いません。

**終了方針:** デフォルトでは Chrome を閉じず残します（事前ログイン維持）。閉じるときは `--close-browser` または `KEEP_BROWSER_OPEN=false`。

**Chrome方針:** システムの Google Chrome を CDP で利用します（同梱しません）。詳細は [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md)。

---

## 技術概要

| 項目 | 内容 |
|------|------|
| 言語・配布 | Python / Windows exe |
| ブラウザ | Playwright + CDP（システム Chrome） |
| LLM | Gemini API（**最新の公式 SDK**） |
| モデル分担 | Flash: ステップ分解 / Flash-Lite: 要素確定 |
| 安全 | 危険操作は確認必須、収集モードで書き込み抑制可 |

---

## ドキュメント

- [docs/MVP_SPEC.md](docs/MVP_SPEC.md) — 仕様・実装チェックリスト・受け入れ基準
- [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md) — Windows exe 配布
- [docs/DISCLAIMER.md](docs/DISCLAIMER.md) — 自己責任・免責
- [LICENSE](LICENSE) — ライセンス
