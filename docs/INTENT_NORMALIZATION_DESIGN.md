# あいまい指示の明確化設計（サイト辞書・結果タイプ）

実装前の設計メモ。  
目的は、日本語指示のあいまいさを **LLMの推測だけに頼らず**、事前ルールで具体化してから Flash 分解・実行に渡すこと。

ステータス: **実装済み（Step A〜E）**  
関連: [MVP_SPEC.md](MVP_SPEC.md)

設定ファイルは設計当初の YAML 案から、依存追加を避けるため **JSON**（`aliases/sites.json` / `aliases/result_phrases.json`）に変更。

---

## 1. 背景と狙い

現状の失敗例:

- URLが指示に無いと、Flashが現在ページを見て「もう足りる」と判断し、検索を省略する
- 「結果のURLリストを取得」でも `extract` が title / 要約中心になり、成果物がブレる

狙うこと:

1. **サイト補完** — URL未記入時、よく使うキーワードから正規URLを決める
2. **結果タイプ推定** — 取得したいものが `urls` / `title` / `text` かを決める
3. 上記を **plan への入力に明示**し、実行・完了判定まで一貫させる

やらないこと（本設計の範囲外）:

- 全サイトの巨大辞書
- 任意サイトの高精度スクレイピング保証
- CAPTCHA / ログイン突破

---

## 2. 全体フロー（変更後）

```
日本語指示（生）
  → IntentNormalizer（ルール）
       - 明示URLの抽出
       - サイトキーワード → 正規URL（明示URLが無いときのみ）
       - 結果タイプ推定（urls | title | text | none）
       - 検索クエリ候補の抽出（任意）
  → 正規化済みインテント（構造化）
  → Flash: 手順分解（インテントを入力に含める）
  → 既存ループ（観察 → Lite → 1手実行）
  → extract は結果タイプに応じて実装を切替
  → 完了判定（タイプに応じた最低条件）
```

ルール層は **決定的**（同じ指示なら同じ補完）。  
Flashは「どう操作するか」に集中させ、「何を取りたいか／どこへ行くか」はルールが先に決める。

優先順位（衝突時）:

1. 指示文中の **明示URL**（`https://...`）
2. **サイト辞書**ヒット
3. 現在のブラウザURL（参考情報として plan に渡すが、指示の必須操作は省略禁止）

---

## 3. データモデル

### 3.1 `NormalizedIntent`

| フィールド | 型 | 説明 |
|------------|----|------|
| `raw_instruction` | str | 元の日本語指示 |
| `normalized_instruction` | str | 補完を反映した指示文（ログ・plan用） |
| `target_url` | str \| null | 最初に開く／基準にするURL |
| `url_source` | `explicit` \| `alias` \| `none` | URLの由来 |
| `site_alias` | str \| null | ヒットしたエイリアス（例: `google`） |
| `result_type` | `urls` \| `title` \| `text` \| `none` | 取得したい成果物 |
| `result_type_source` | `phrase` \| `default` \| `none` | 推定の根拠 |
| `query_hint` | str \| null | 「〜で検索」のクエリ候補（任意） |
| `notes` | list[str] | 補完ログ用の短い説明 |

### 3.2 サイト辞書エントリ `SiteAlias`

| フィールド | 型 | 説明 |
|------------|----|------|
| `id` | str | 安定ID（例: `google`） |
| `keywords` | list[str] | マッチ語（小文字化して比較。日本語はそのまま） |
| `url` | str | 正規URL |
| `enabled` | bool | 無効化可能 |

マッチ規則（案）:

- 指示を簡易正規化（全角英数→半角、連続空白圧縮）
- **明示URLが無い**ときだけ辞書を見る
- キーワードは「単語境界っぽい部分一致」または「含む」——MVPは **含む** で開始し、誤爆が出たら境界ルールを強化
- 複数ヒット時は **より長いキーワード優先**、それでも同点なら辞書定義順
- 「googleと検索」と「googleで検索」の区別:
  - `で` / `を開` / `へ行` などがサイト指定寄り
  - `と検索` / `を検索` はクエリ寄り
  - MVPでは厳密パースは最小限とし、`query_hint` はヒューリスティック、サイトは「サイト系キーワードが指示前半または『で』の直前」を優先（実装詳細はテストで固定）

### 3.3 結果タイプ `ResultType`

| 値 | 意味 | extract の出力 |
|----|------|----------------|
| `urls` | リンクURLの一覧 | `{ "urls": ["https://...", ...], "count": N }` |
| `title` | ページタイトル中心 | `{ "title": "...", "url": "..." }` |
| `text` | 本文・要約 | `{ "text": "...", "url": "...", "title": "..." }` |
| `none` | 取得指定なし（操作のみ等） | 既存どおり／extract dual不要ならスキップ可 |

言い回し辞書（初期案・ユーザー拡張可）:

| タイプ | キーワード例 |
|--------|----------------|
| `urls` | URL, url, リンク, 一覧, リスト, アドレス |
| `title` | タイトル, 見出し, title |
| `text` | 内容, 本文, テキスト, 要約, 抜粋 |

推定規則:

1. 指示にタイプ語が含まれる → 対応タイプ（複数なら優先度 `urls` > `title` > `text`）
2. 「取得」「収集」「抽出」がありタイプ語が無い → デフォルト `urls`（情報収集用途が多いため。設定で変更可）
3. 操作のみ（「クリックして」「入力して」だけで取得語が無い）→ `none`

---

## 4. 設定ファイル

### 4.1 置き場所

アプリ同階層（`.env` と同じ基準ディレクトリ）:

```
aliases/
  sites.json            # サイト辞書（ユーザー編集可）
  result_phrases.json   # 結果タイプ言い回し（任意。無ければ内蔵デフォルト）
```

リポジトリには **デフォルト例** を同梱し、初回はコピーまたは内蔵フォールバック。

### 4.2 `sites.json` 例

```json
{
  "version": 1,
  "sites": [
    {
      "id": "google",
      "keywords": ["google", "グーグル"],
      "url": "https://www.google.com",
      "enabled": true
    }
  ]
}
```

### 4.3 環境変数（任意）

| キー | 既定 | 意味 |
|------|------|------|
| `INTENT_SITES_PATH` | `aliases/sites.json` | サイト辞書パス |
| `INTENT_RESULT_PHRASES_PATH` | `aliases/result_phrases.json` | 言い回し辞書 |
| `INTENT_DEFAULT_RESULT_TYPE` | `urls` | 「取得」系でタイプ不明時 |
| `INTENT_EXTRACT_URL_LIMIT` | `50` | urls 抽出の上限 |

実APIキーと同様、秘密情報は載せない（URL辞書は秘密ではない）。

---

## 5. Flash / 実行への渡し方

### 5.1 plan 入力

`plan_steps` の user JSON を拡張:

```json
{
  "user_instruction": "<raw>",
  "normalized_instruction": "<補完後の文>",
  "current_url": "<ブラウザ現在URL>",
  "intent": {
    "target_url": "https://www.google.com",
    "url_source": "alias",
    "site_alias": "google",
    "result_type": "urls",
    "query_hint": "google"
  },
  "planner_rules": [
    "intent.target_url がある場合、手順の早い段階で goto を入れる",
    "current_url が似ていても、指示に検索・再取得があるなら省略しない",
    "result_type=urls の場合、最後の extract はURLリスト取得を目的にする",
    "完了(done)は result_type の成果が取れてから"
  ]
}
```

システムプロンプトにも短い固定ルールを追加（上記 `planner_rules` と重複可）。

### 5.2 `normalized_instruction` の作り方（例）

生: `googleでgoogleと検索して、結果のurlリストを取得して`

補完後の例:

```
サイト https://www.google.com （alias=google）を開き、
クエリ「google」で検索し、
結果タイプ urls（リンクURL一覧）を取得する。
元指示: googleでgoogleと検索して、結果のurlリストを取得して
```

ログの起動バナーまたは plan 直前に `intent` を INFO 出力する。

### 5.3 extract 実装の切替

| `result_type` | 実装方針 |
|---------------|----------|
| `urls` | ページ内の `a[href]` から http(s) を収集。同一ドメインのノイズ（ログイン等）は簡易フィルタ。上限 N 件（設定可、例 50） |
| `title` | `document.title` + 現在URL |
| `text` | 既存の text_preview 相当（長さ上限あり） |
| `none` | 従来の汎用 extract、または extract ステップ自体を非推奨 |

Google検索結果などサイト固有のセレクタ最適化は **後続**（本設計の必須ではない）。まずは汎用リンク収集。

### 5.4 完了判定

| `result_type` | done 許可条件（案） |
|---------------|---------------------|
| `urls` | 直近 extract で `count >= 1` |
| `title` | `title` が非空 |
| `text` | `text` が非空 |
| `none` | 従来どおり（ステップ上の done） |

未達なら再計画 1 回（既存 `REPLAN_ON_FAILURE` と整合）または `ask_user`。

---

## 6. モジュール構成（実装時）

| パス | 役割 |
|------|------|
| `src/browser_assistant/intent/normalize.py` | 正規化エントリ |
| `src/browser_assistant/intent/sites.py` | サイト辞書ロード・マッチ |
| `src/browser_assistant/intent/result_type.py` | 結果タイプ推定 |
| `src/browser_assistant/intent/defaults.py` | 内蔵デフォルト辞書 |
| `aliases/sites.yaml` | ユーザー向けデフォルト例 |
| `tests/test_intent_normalize.py` | ルールの単体テスト |

呼び出し点:

- `AgentLoop.run` の `plan_steps` 前
- CLI `--plan` / `--run` / UI 実行の共通経路

---

## 7. ログ・UI

ログ（必須）:

```
[intent] target_url=https://www.google.com source=alias site=google
[intent] result_type=urls source=phrase
[intent] query_hint=google
```

UI（任意・後続可）:

- 実行前に「補完結果」を1行表示（URL・結果タイプ）
- ユーザーが訂正できるのは Phase 2 以降でもよい

---

## 8. テスト観点

### サイト辞書

- [ ] 明示URLあり → 辞書を使わない
- [ ] `googleで…` → google.com
- [ ] 複数キーワード同点 → 定義順
- [ ] 辞書ファイル無し → 内蔵デフォルトまたは補完なし（挙動を固定）

### 結果タイプ

- [ ] 「urlリスト」→ `urls`
- [ ] 「タイトルを取得」→ `title`
- [ ] 「内容を要約」→ `text`
- [ ] 「取得して」のみ → デフォルト `urls`
- [ ] 「ボタンをクリック」のみ → `none`

### 結合（モック可）

- [ ] 正規化後の plan 入力に `intent` が含まれる
- [ ] `result_type=urls` の extract が `urls` 配列を返す
- [ ] 空の urls では done しない（または再計画）

---

## 9. 段階的実装案

| Step | 内容 | 完了条件 |
|------|------|----------|
| A | `NormalizedIntent` + サイト辞書 + 単体テスト | 明示URL優先・alias補完がテストで通る |
| B | 結果タイプ推定 + 言い回し辞書 | 上記フレーズ表がテストで通る |
| C | `plan_steps` へ intent 注入 + プロンプトルール | `--plan` で goto/extract 方針が安定 |
| D | extract 切替（urls/title/text） | `--run` で urls が配列として取れる |
| E | 完了判定・ログ整備 | 空結果で安易に done しない |

各 Step は実装後にテストを通してから次へ。

---

## 10. リスクと制約

- キーワード「含む」マッチは誤爆しうる → 辞書を小さく保つ、ログで `site_alias` を必ず出す
- Google等の結果DOMは変わりやすい → 汎用 `a[href]` はノイズ混入あり（フィルタで緩和、完璧は目指さない）
- デフォルト `result_type=urls` は操作系指示を誤分類しうる → 「取得・収集・抽出」語が無いなら `none`
- 利用規約・自動化禁止サイトは従来どおり自己責任（辞書追加はユーザー責任）

---

## 11. 決定事項（この設計で固定）

1. あいまいさの一次処理は **ルール（辞書）**、二次が Flash
2. サイト辞書は **少数・ユーザー編集可（YAML）**
3. 結果タイプは **`urls` / `title` / `text` / `none` の4値**
4. 明示URLは常に辞書より優先
5. `current_url` は参考であり、指示上の必須操作の省略許可には使わない（プロンプト＋完了判定で担保）

---

## 12. 未決（実装時に短く決める）

- [ ] Google検索クエリの取り出し精度（「AでBと検索」の A/B 分離）をどこまでヒューリスティックにするか
- [ ] urls 抽出時のドメインフィルタ初期値（検索結果ページでは外部リンク優先、など）
- [ ] UIへの補完表示を同梱するか、ログのみにするか

---

改訂履歴

- 2026-07-30: 初版（実装前設計）
- 2026-07-30: Step A〜E 実装。設定形式を JSON に変更
