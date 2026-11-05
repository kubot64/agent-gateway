# Pull requests

main への直接 push は禁止で、変更はすべて PR を通す。

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
