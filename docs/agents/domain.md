# ドメイン文書

エンジニアリング系スキルがコードベースを調べるときに、このリポジトリのドメイン文書をどう読むかを定める。

## 調べる前に読むもの

- リポジトリ直下の **`GLOSSARY.md`**。
- **`docs/adr/`**: これから触る領域に関わる ADR を読む。

これらのファイルがなくても **何も言わずに先へ進む**。ないことを指摘しない。先回りして作るよう提案もしない。`/domain-modeling` スキル（`/grill-with-docs` と `/improve-codebase-architecture` から呼ばれる）が、用語や判断が実際に決まった時点で作る。

## ファイル構成

このリポジトリは single-context。

```
/
├── GLOSSARY.md
├── docs/adr/
│   ├── 0001-idempotency-key-dedup.md
│   └── 0002-rule-based-intent-classification.md
└── backend/src/
```

将来 context が複数に分かれたら、ルートに `GLOSSARY-MAP.md` を置き、context ごとの `GLOSSARY.md` を指す。そのときは `GLOSSARY-MAP.md` から関係する `GLOSSARY.md` をたどって読む。

## glossary の語彙を使う

出力の中でドメインの概念に名前を付けるときは（issue のタイトル、リファクタリング案、仮説、テスト名など）、`GLOSSARY.md` で定義された用語を使う。glossary が避けると明記している同義語に流れない。

必要な概念が glossary にまだないなら、それは合図である。プロジェクトが使っていない言葉を作り出しているか（考え直す）、本当に穴があるか（`/domain-modeling` 用にメモする）のどちらかだ。

## ADR との矛盾は明示する

出力が既存の ADR と矛盾するなら、黙って上書きせずに明示する。

> _ADR-0003（書き込み操作は全件承認）と矛盾するが、次の理由で見直す価値がある…_
