# Pull requests

main への直接 push は禁止で、変更はすべて PR を通す。

## PR を作る前

`/code-review` を `main` に対して回し、指摘を直してから PR を作る。Standards 軸はルートの `CODING_STANDARDS.md` を読む。issue から始まっていない変更では、spec の場所を聞かれたら「ない」と答え、Spec 軸を飛ばす。

## 本文

見出しは `.github/pull_request_template.md` のとおり、「背景・変更・検証方法・Merge Danger」にする。`/pr` スキルの要素は次のように各見出しへ収める。

- **Summary の図**: 「変更」に置く。
- **Evidence の Before / After**: 「検証方法」に置く。
- **Merge Danger**: 見出しをそのまま使う。

## マージ

- スカッシュマージで入れる。マージするとリモートのブランチは自動で消える。
- main のブランチ保護には 2 つの条件がある。`unit-tests` と `e2e-postgres-redis` が通っていること、そして main に追いついていることだ。
- main に遅れていてマージが弾かれたら、`gh pr update-branch <n>` で追いつかせてから `gh pr merge <n> --squash --auto` を実行する。CI が通った時点でマージされる。
- スカッシュマージしたローカルブランチは、git からは未マージに見える。PR が MERGED になっていることを確かめてから `git branch -D` で消す。
