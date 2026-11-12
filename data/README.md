# data

問い合わせ一次対応AIの開発と評価に使うデータです。すべて架空のもので、実在の会社・規約・個人情報は含みません。

## 置き場所

| パス | 中身 | RAG の検索対象 |
| --- | --- | --- |
| `company/profile.md` | 架空の会社・製品・ヘルプデスクの設定（データを作るための前提） | 対象外 |
| `docs/<doc_id>.md` | 回答の根拠になる資料（料金表・規約・マニュアル・FAQ など） | 対象 |
| `sheets/inquiries.csv` | Google スプレッドシート「問い合わせ管理表」のスナップショット（開発用） | 対応履歴の行が対象 |
| `labels/history.jsonl` | 対応履歴の質の正解 | 対象外 |
| `labels/dev.jsonl` | 新しい問い合わせ（開発用）の正解 | 対象外 |
| `eval/sheets/inquiries_eval.csv` | 「問い合わせ管理表（評価用）」のスナップショット | 対象外（処理する入力） |
| `eval/labels/eval.jsonl` | 評価用の問い合わせの正解 | 対象外 |

## 評価用データの扱い

`eval/` の30件は最終評価まで開発で使いません。

- アプリのコード・テスト・プロンプトの調整・開発中の評価では `eval/` を読まない（パスを書かない）
- 検証スクリプトも `--eval` を付けたときだけ `eval/` を読む
- 根拠（`sources`）には、開発用の管理表の対応履歴と `docs/` の資料を使ってよい（評価のときも同じものを検索対象にするため）
- 受付番号は開発用の管理表と重ならない連番にする

## 問い合わせ管理表（`sheets/inquiries.csv`）

ヘルプデスクが電話・チャット・メールで受けた問い合わせと回答を記録する管理表です。
CSV は UTF-8（BOM なし）、1行目が列名で、Google スプレッドシートと同じ列にします。

| 列 | 形式 | 説明 |
| --- | --- | --- |
| 受付番号 | `INQ-0001` | 必須。正解データとの紐付けキー。重複させない |
| 受付日時 | `YYYY-MM-DD HH:MM` | |
| チャネル | 電話 / チャット / メール | |
| 受付者 | | 受け付けて問い合わせメモを書いたヘルプデスクの担当 |
| 会社名 | | 問い合わせてきた顧客の会社 |
| 担当者名 | | 顧客側の担当者 |
| 連絡先 | | 電話番号・メールアドレス（架空） |
| 問い合わせメモ | 自由記述 | 受付者が書いたメモ。略語・誤字・表記ゆれ・複数の用件を含む |
| 回答内容 | 自由記述 | 埋まっていれば過去の対応履歴、空なら新しい問い合わせ |
| 回答者 | | |
| 回答日 | `YYYY-MM-DD` | |
| 対応状況 | 未対応 / 対応中 / 回答済み / 引き継ぎ | |
| 備考 | 自由記述 | |

- 受付番号以外は空欄を許します（現場の管理表らしさを残すため）。書いた値は上の形式で検証します
- **回答内容が埋まった行**が過去の対応履歴（RAG の検索対象）、**空の行**が新しい問い合わせ（処理対象）です
- 対応履歴の回答は古い・誤り・担当者ごとの食い違いを含みます。資料と食い違うときは資料を正とします。質の正解は管理表に書かず `labels/history.jsonl` に持ちます

## 対応履歴の質の正解（`labels/history.jsonl`）

管理表の対応履歴（回答内容が埋まった行）と1対1で、1行に1件の JSON を書きます。

```json
{"id": "INQ-0012", "quality": "outdated", "note": "旧料金（ライトプラン 200円）のまま回答している。現行は pricing-2026"}
```

| キー | 型 | 値 |
| --- | --- | --- |
| `id` | 文字列 | 受付番号 |
| `quality` | 文字列 | `ok`（正しい）/ `outdated`（古い情報）/ `wrong`（誤り）/ `inconsistent`（担当者ごとに食い違う） |
| `note` | 文字列 | どこが古い・誤りか。`ok` 以外では必須 |

## 新しい問い合わせの正解（`labels/dev.jsonl`、`eval/labels/eval.jsonl`）

管理表の新しい問い合わせ（回答内容が空の行）と1対1で、1行に1件の JSON を書きます。

```json
{"id": "INQ-0131", "case_type": "answerable", "category": "cancellation", "action": "answer", "sources": ["cancellation-guide", "INQ-0045"], "must_include": ["解約する月の前月20日までに申請", "管理画面の 設定 > 契約 > 解約の申請 から契約の管理者が申請"], "note": ""}
```

| キー | 型 | 値 |
| --- | --- | --- |
| `id` | 文字列 | 受付番号 |
| `case_type` | 文字列 | 下の表 |
| `category` | 文字列 | 下の表 |
| `action` | 文字列 | `answer`（回答案を作る）/ `ask_back`（足りない情報を聞き返す）/ `escalate`（人に引き継ぐ）/ `reject`（対応しない） |
| `sources` | 文字列の配列 | 根拠にすべき資料の `doc_id` か、対応履歴の受付番号。`answerable` では1件以上 |
| `must_include` | 文字列の配列 | 回答に含めるべき要素。`answerable` では1件以上 |
| `note` | 文字列 | 補足（紛らわしい点、混ざっている用件など）。空でもよい |

### case_type

| 値 | 意味 | 件数の目安（全100件） |
| --- | --- | --- |
| `answerable` | 資料で答えられる | 40 |
| `no_answer` | 資料に答えが無い | 15 |
| `insufficient` | 情報が足りず答えられない | 15 |
| `needs_human` | 人の判断が要る（労務・法令の個別判断、返金・例外対応など） | 15 |
| `confusing` | 紛らわしい（旧情報・似た機能・似た履歴に引っ張られやすい） | 10 |
| `malicious` | 悪意のある入力（プロンプトインジェクション、情報の聞き出しなど） | 5 |

### category

| 値 | 意味 |
| --- | --- |
| `pricing_contract` | 料金・契約・請求 |
| `cancellation` | 解約 |
| `how_to` | 操作方法 |
| `incident` | 障害・不具合 |
| `labor_law` | 労務・法令 |
| `other` | その他 |

### 1行に複数の用件があるメモ

正解は1件のまま持ちます。

- `case_type`・`category`・`action` は**主な用件**（いちばん重い対応が要る用件）で決めます。重さは `reject` > `escalate` > `ask_back` > `answer`
- `sources` と `must_include` には**全用件分**を入れます
- どの用件が混ざっているかは `note` に書きます

### sources に入れてよいもの

- `status: current` の資料の `doc_id`（`obsolete` の資料は入れない）
- `quality: ok` の対応履歴の受付番号（古い・誤り・食い違いの履歴や、新しい問い合わせの行は入れない）

## 資料（`docs/*.md`）

ファイル名は `<doc_id>.md`。先頭に frontmatter を付けます。

```yaml
---
doc_id: pricing-2026
title: 料金表
category: pricing        # pricing / contract / guide / manual / support / faq
version: "2.0"
valid_from: 2026-04-01
valid_until:             # 無期限は空
status: current          # current / obsolete
---
```

## 検証

```bash
python3 scripts/validate_dataset.py          # 開発用
python3 scripts/validate_dataset.py --eval   # 評価用も含める（最終評価のときだけ）
```

まだ無いファイルは飛ばします。確かめること:

- 受付番号の形式と重複（開発用と評価用をまたいで）
- 管理表の列名、日時・チャネル・対応状況の値
- 管理表と正解データが1対1で対応する（対応履歴 ↔ `history.jsonl`、新しい問い合わせ ↔ `dev.jsonl`）
- 正解データのキーと値が決めた範囲に収まる
- `sources` が実在する資料・対応履歴を指し、`obsolete` の資料や `quality` が `ok` 以外の履歴を含まない
- 資料の frontmatter がそろい、`doc_id` がファイル名と一致する
