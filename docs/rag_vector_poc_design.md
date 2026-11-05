# RAG Vector PoC Design

## Goal
`/ask` の文脈取得をキーワード検索依存から段階的に改善するため、ローカル環境で動く最小ベクトル検索を導入する。

## Current PoC
- 実装: `backend/src/tools/docs_search.py`
- 切替: `DOC_SEARCH_BACKEND=keyword|vector|hybrid`
- 対象: Markdown (`README.md`, `docs`, `games`, `travel` など)

### keyword
- 既存方式
- クエリtokenの出現回数でスコアリング

### vector (PoC)
- tokenを固定次元ハッシュベクトルへ射影
- クエリと文書のコサイン類似度でスコアリング
- 依存ライブラリなしで動作可能

### hybrid
- keyword結果とvector結果をマージ
- keyword一致を優先しつつ、vector類似度で順位調整

## Why This Design
- 外部Embedding APIや専用DBなしで検証できる
- 既存API契約（`metadata.sources`）を維持できる
- 段階導入しやすい（env切替でA/B可能）

## Known Limits
- 埋め込み品質は簡易ハッシュ方式のため限定的
- インデックス永続化・増分更新は未実装
- 厳密な意味検索より、軽量近似検索に近い

## Next Steps
1. 埋め込み生成を本番品質へ置換（OpenAI Embeddings等）
2. pgvectorまたは専用ベクトルDBに格納し、増分更新を導入
3. Hybrid rankingの重み最適化（オフライン評価データで調整）
4. 監視追加（検索ヒット率、回答採用率、遅延）
