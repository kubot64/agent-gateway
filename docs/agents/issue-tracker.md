# Issue tracker: GitHub

このリポジトリの issue と spec は GitHub Issues（`kubot64/agent-gateway`）に置く。操作はすべて `gh` CLI で行う。

## 約束事

- **issue を作る**: `gh issue create --title "..." --body "..."`。複数行の本文は heredoc で渡す。
- **issue を読む**: `gh issue view <number> --json number,title,body,labels,comments`。
- **issue を一覧する**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`。必要に応じて `--label` と `--state` で絞る。
- **親 issue のサブイシューにする**: `gh` 2.94 以上なら `gh issue create --parent <parent> ...` か、作成後に `gh issue edit <parent> --add-sub-issue <child>`。それより古い `gh` では `gh api --method POST repos/kubot64/agent-gateway/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>` を使う。`<child-db-id>` は下の **ブロッキング** と同じ database id。サブイシューが使えない場合は、子の本文の先頭に `Part of #<parent>` と書く。
- **ブロッキング**: GitHub ネイティブの issue 依存関係を正とする。UI にも表示される。`gh` 2.94 以上なら `gh issue create --blocked-by <n>,<n> ...` か `gh issue edit <child> --add-blocked-by <blocker>`。それより古い `gh` では `gh api --method POST repos/kubot64/agent-gateway/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>` を使う。`<blocker-db-id>` はブロッカーの数値 **database id** で、`gh api repos/kubot64/agent-gateway/issues/<n> --jq .id` で取れる。`#number` や `node_id` ではない。依存関係が使えない場合は、子の本文の先頭に `Blocked by: #<n>, #<n>` と書く。ブロッカーがすべて close されたら、その ticket は着手できる。
- **未解決のブロッカーを数える**: `gh api repos/kubot64/agent-gateway/issues/<n> --jq .issue_dependencies_summary.blocked_by`。open のブロッカーだけを数え、0 なら着手できる。`gh issue view <n> --json blockedBy` は close 済みも含むので、`--jq '[.blockedBy.nodes[] | select(.state == "OPEN")] | length'` で絞る。
- **コメントする**: `gh issue comment <number> --body "..."`
- **ラベルを付ける・外す**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **close する**: 先に `gh issue comment <number> --body "..."` で理由を書き、次に `gh issue close <number>` を実行する。close 済みの issue に `gh issue close --comment` を使うと、コメントは捨てられるのに終了コードは 0 になる。

リポジトリは `git remote -v` から推定する。clone の中で実行すれば `gh` が自動で判断する。

issue はこのリポジトリにだけ起票する。スキルの配布元である `mattpocock/skills` には起票しない。

## プルリクエストを triage の対象にするか

**PRs as a request surface: no.** _（外部からの PR を機能要望として扱うなら `yes` にする。`/triage` がこのフラグを読む。）_

`yes` の場合、PR も issue と同じラベルと状態で扱う。操作は `gh pr` の対応コマンドを使う。

- **PR を読む**: `gh pr view <number>`、続けて `gh pr view <number> --comments`。TTY でないと `--comments` はコメントだけを出し、本文は出さない。差分は `gh pr diff <number>`。
- **triage 対象の外部 PR を一覧する**: `gh api --paginate 'repos/{owner}/{repo}/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`。
- **コメント・ラベル・close**: `gh pr comment`、`gh pr edit --add-label`/`--remove-label`、`gh pr close`。

GitHub では issue と PR が番号空間を共有するので、`#42` だけではどちらか分からない。`gh pr view 42` で確かめ、だめなら `gh issue view 42` を試す。

## スキルが「issue tracker に公開する」と言ったとき

GitHub issue を作る。

## スキルが「関連する ticket を取得する」と言ったとき

上の **issue を読む** のとおりに読む。

## Wayfinding の操作

`/wayfinder` が使う。**map** は 1 つの issue で、ticket はその **子** issue として作る。

- **Map**: `wayfinder:map` ラベルを付けた 1 つの issue。本文に Notes / Decisions-so-far / Fog を持つ。作成は `gh issue create --label wayfinder:map`。
- **子 ticket**: map のサブイシューとして紐づけた issue（**親 issue のサブイシューにする** を参照）。サブイシューが使えない場合は、map 本文のタスクリストに子を追加し、子の本文の先頭に `Part of #<map>` と書く。ラベルは `wayfinder:<type>`（`research`/`prototype`/`grilling`/`task`）。着手したら担当者を自分にする。
- **ブロッキング**: 約束事の **ブロッキング** を参照。
- **frontier の求め方**: map の open な子を一覧する（`gh issue list --state open` を map のサブイシューかタスクリストに絞る）。open なブロッカーがあるもの（**未解決のブロッカーを数える** か、`Blocked by` 行の open な issue で判断）と、担当者がいるものを除く。残りのうち map で先に並ぶものを選ぶ。
- **着手**: `gh issue edit <n> --add-assignee @me`。セッションの最初の書き込みとして行う。
- **解決**: `gh issue comment <n> --body "<answer>"`、`gh issue close <n>` の順に実行する。最後に map の Decisions-so-far へ要旨とリンクを追記する。
