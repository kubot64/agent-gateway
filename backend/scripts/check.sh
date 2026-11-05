#!/usr/bin/env bash
# CI の unit-tests・lint・typecheck ジョブと同じ検査を流す。CI もこのスクリプトを呼ぶので、検査の定義はここだけにある。
#
#   bash backend/scripts/check.sh            # すべて（contracts → test → lint → typecheck）
#   bash backend/scripts/check.sh test       # 1 つだけ
#   bash backend/scripts/check.sh test tests.test_read_tools   # テストを絞る
#
# 依存は事前に入れておく: cd backend && python3 -m pip install -e ".[dev]"
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${BACKEND_DIR}"
export PYTHONPATH=src
PYTHON="${PYTHON:-python3}"

run_contracts() {
  echo "==> contracts (lint-imports)"
  lint-imports
}

run_test() {
  echo "==> test (unittest)"
  # -t . で tests をパッケージとして読み、tests/__init__.py に SQLite を一時ディレクトリへ向けさせる。
  if [[ $# -gt 0 ]]; then
    "${PYTHON}" -m unittest -v "$@"
  else
    "${PYTHON}" -m unittest discover -s tests -t . -p 'test_*.py' -v
  fi
}

run_lint() {
  echo "==> lint (ruff)"
  ruff check .
}

run_typecheck() {
  echo "==> typecheck (mypy)"
  mypy
}

step="${1:-all}"
[[ $# -gt 0 ]] && shift

case "${step}" in
  contracts) run_contracts ;;
  test) run_test "$@" ;;
  lint) run_lint ;;
  typecheck) run_typecheck ;;
  all)
    run_contracts
    run_test
    run_lint
    run_typecheck
    ;;
  *)
    echo "usage: $0 [all|contracts|test|lint|typecheck] [unittest args...]" >&2
    exit 2
    ;;
esac
