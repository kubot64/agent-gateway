"""テストの実行全体で使う SQLite を一時ディレクトリに置く。

`gateway.inbound` は import した時点で DB を開く。何もしないと `backend/data/agent.db`（手元の開発用 DB）に
書き込むので、どのテストモジュールよりも先にここで `SQLITE_PATH` を設定する。
このファイルが先に読まれるのは、`tests` をパッケージとして読む実行方法（`discover -t .` や
`python -m unittest tests.test_x`）のときだけである。実行方法は `backend/scripts/check.sh` を正とする。
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="agent-gateway-tests-")
atexit.register(shutil.rmtree, _tmp_dir, ignore_errors=True)
os.environ["SQLITE_PATH"] = os.path.join(_tmp_dir, "agent.db")
