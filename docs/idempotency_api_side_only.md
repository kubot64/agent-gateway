# クライアントを変えずに API 側だけで冪等性を付ける方針（PHP + MySQL 版）

前提:
- クライアント（アプリ / SDK）はすぐには変更できない。`Idempotency-Key` ヘッダは送られてこない
- API は古い PHP（5 系想定。フレームワークなし、エンドポイントごとに 1 スクリプトの構成も想定）。DB は MySQL で、対象テーブルは InnoDB（確認済み）
- 経路は クライアント → (Cloud NAT) → LB → Nginx → Chocon → API → MySQL
- 前回の文書「冪等性のない API に簡単に冪等性を付ける方法」の続き。そちらの「(2) 鍵テーブル方式」を、鍵をどこから取るかだけ変えて再利用する

レビュー状況（5 回目、設計レビュー完了）:
- 設計上のリリースブロッカーなし。**実装レビューへ進める水準**
- 実装時の補足条件 2 点（本版で反映）: 強制モードで変えた `innodb_lock_wait_timeout` も PDO 永続接続なら終了前に元へ戻す / 対象エンドポイントでは `flush()`、`ob_flush()`、ストリーミング応答を禁止する
- 決済の本番適用の最終ゲート: 決済代行の注文 ID 一意性と照会整合性の実機確認、および「時間窓を超えた再送は二重課金になり得る」残存リスクの業務承認

## 0. 結論（先に 6 行）

1. 冪等性に必要なのは「これは同じ操作だ」と分かる目印（鍵）。クライアントが送ってくれないなら、API 側でリクエストの中から **見つける**（方法 A）か、リクエストの内容から **作る**（方法 B）。
2. 方法 A が使えるなら A。使えないときだけ B。どちらも鍵とは別に **リクエストの内容ハッシュ** を保存し、「同じ鍵なのに内容が違う」リクエストは再送ではなく業務エラーとして扱う。
3. いきなり止めない。まず **観測モード** で重複の実態を測る。観測モードは追記専用の記録テーブルに 1 行書くだけで、同じ鍵のリクエストを意図的に待たせることはしない。数字を見てから **強制モード** に切り替える。
4. DB だけを書く API は、鍵テーブル + 業務の書き込み + 応答の保存を **同じトランザクションで確定** する。終わり方は既存処理が完了関数を呼ぶ契約にし、完了関数は必ず `exit` で終わる。自動 COMMIT はしない。
5. 決済 API は外部（クレカ API → 決済代行）を呼ぶので、同じトランザクションでは守れない。**決済前に「試行レコード」と決済代行向けの注文 ID を確定し、状態機械（created → charging → succeeded / failed / unknown）で進める。各状態で「誰が何をしてよいか」を決め、処理権は条件付き UPDATE で 1 人だけが取る。分からなくなったら照会 API で復旧する**。決済の鍵「ユーザー + 商品 + 本文の時刻」は自然キーではなく推測キーで、これは「誤って止めるリスクを伴う重複低減策」であって完全な冪等性ではない。
6. 対象テーブルは InnoDB なので、上のトランザクション設計がそのまま使える。

## 1. 用語

| 用語 | 意味 | 例え |
|---|---|---|
| 冪等性 | 同じリクエストを何回送っても、結果が 1 回送ったときと同じになる性質 | 「受付番号 123 の注文です」と何度言っても注文は 1 件 |
| 再送（リトライ） | タイムアウトや接続切れのあと、クライアントが同じリクエストをもう一度送ること | 電話が切れたのでかけ直す |
| 二重実行 | 再送を新しい注文として処理してしまうこと | 注文が 2 件入る |
| 鍵（キー） | 「これは同じ操作だ」と API が判断するための目印 | 受付番号 |
| 自然キー | リクエストの中に元々あり、その操作を必ず一意に決める値。時間が経っても別の操作にはならない | 注文番号、伝票番号 |
| 推測キー | 「たぶん同じ操作だろう」と推測するための組み合わせ。正当な別操作が同じになる可能性がゼロではない | (ユーザー, 商品, 押した時刻) の組 |
| 内容ハッシュ | リクエストの内容から計算したハッシュ値。内容が同じなら同じ値になる | 手紙の内容から作った指紋 |
| 時間窓 / 期限 | 推測キーと方法 B で「この時刻までに来た同じ鍵は再送」とみなす期限。自然キーには無い | 受付番号の有効期限 |
| 受付時刻 | リクエストが API に届いた時刻。期限判定はこの時刻で行う（処理が終わった時刻ではない） | 消印の日付 |
| トランザクション | 複数の DB 書き込みをひとまとめにし、全部成功（COMMIT）か全部取り消し（ROLLBACK）のどちらかにする仕組み | 伝票を何枚か書いて、最後にまとめて判子を押す。押す前に手を止めたら全部破棄 |
| SAVEPOINT | トランザクションの途中に付ける「しおり」。そこまでだけ取り消せる | 伝票の 3 枚目以降だけ破棄する |
| InnoDB | MySQL のテーブルの種類。トランザクションが使える | 取り消しができる帳簿 |
| テナント | 会社やアカウント単位の区切り。鍵は必ずこの単位で分ける | 別の会社の受付番号 123 とは別物 |
| 観測モード / 強制モード | 観測は「重複を見つけても記録するだけ」、強制は「重複を見つけたら 2 回目を止める」 | 監視カメラだけ置く / 入場を止める |
| 状態機械 | 「作成 → 課金中 → 成功 / 失敗 / 不明」のように、状態と遷移を決めておく設計 | 荷物の追跡ステータス |
| 処理権（リース） | 「いまこの決済を進めてよいのは誰か」を示す印と期限。期限が切れたら他のプロセスが引き継げる | 貸出カードと返却期限 |
| 照会 API | 注文 ID を渡すと、決済代行がその決済の状態（課金済み / 未課金 / 失敗 / 処理中）を返してくれる API | 荷物番号で配達状況を聞く |

## 2. なぜ「クライアントを変えない」と難しいのか

普通のやり方では、クライアントが操作ごとに鍵（UUID など）を作り、`Idempotency-Key` ヘッダに入れて送る。API は鍵を見れば「これは 2 回目だ」と分かる。

クライアントを変えられないと、この目印がない。API に届くのは URL と本文だけなので、そこから「同じ操作かどうか」を **推測** するしかない。推測なので、次の 2 種類の間違いのどちらかは必ず起こり得る。

- **見逃し**: 本当は再送なのに別の操作だと思って、2 回実行してしまう（今と同じ状態）
- **誤検知（誤って止める）**: 本当は別の操作なのに再送だと思って、1 回しか実行しない（対策で新しく生まれる危険）

本当の自然キー（注文番号など）があれば誤検知は起きない。推測キーや内容ハッシュを使う場合は起き得るので、どの API に使うか、何分以内を「再送」とみなすかを慎重に決め、「完全な冪等性ではなく重複低減策」であることを仕様に書く。

### 前提: クライアントは再送してくる

タイムアウトや接続切れのあと、クライアントは同じリクエストをもう一度送ることがある。多くは数秒〜数分以内に、ほぼ同じ内容が同じ利用者から届く。方法 B の「時間窓」はこの性質を利用する。

## 3. API 側で鍵を得る 2 つの方法

どちらの方法でも、鍵とは別に **内容ハッシュ** を必ず計算して保存する。鍵が同じで内容ハッシュが違うリクエストは「再送」ではなく「同じ鍵で別の内容」なので、保存済みの応答を返さず、その API が今使っている業務エラー（400 など）を返す。

### 順序: 入力検証 → 鍵と内容ハッシュ → 冪等処理

内容ハッシュは **入力検証に通ったあとの値** から作る。不正な JSON、JSON のトップレベルがオブジェクトでない（数値や文字列だけ）、桁あふれする整数、型が合わない項目は、検証で 400 を返して終わる。検証前の生データをハッシュすると、壊れたリクエストの鍵が記録に残ったり、同じ意味のリクエストが違うハッシュになったりする。

```php
function parse_and_validate_request() {
    $raw = file_get_contents('php://input');
    if ($raw !== '') {
        $body = json_decode($raw, true, 32, defined('JSON_BIGINT_AS_STRING') ? JSON_BIGINT_AS_STRING : 0);
        if (!is_array($body)) { send_existing_validation_error(); exit; }   // 不正 JSON / スカラー
    } else {
        $body = $_POST;                                                     // フォーム送信
    }
    // ここに既存の入力検証（必須項目、型、範囲）。失敗したら既存の 400 を返して exit
    return array('body' => $body, 'query' => $_GET);
}
```

### 鍵の範囲（スコープ）

鍵の先頭には必ず次を含める。本文の値ではなく、ログイン情報とルーティングから決める。

```php
function idem_scope($auth) {                  // $auth は認証済みの情報。引数で渡す
    return array(
        'tenant_id' => $auth->tenantId,
        'user_id'   => $auth->userId,
        'api'       => 'orders.create',       // API の操作名（スクリプト名やルート名）
        'version'   => 'v1',                  // API バージョン
    );
}
```

### 内容ハッシュ

```php
// 除外する項目はパスで指定する（ネストした可変項目にも対応）。例: array('nonce', 'meta.trace_id')
function normalize_request(array $body, array $query, array $volatilePaths) {
    foreach ($volatilePaths as $path) { unset_path($body, explode('.', $path)); }
    ksort_recursive($body);
    $q = array_intersect_key($query, array_flip(array('mode', 'coupon')));  // 意味のあるクエリだけ
    ksort($q);
    return json_encode(array('body' => $body, 'query' => $q));
}
function unset_path(array &$a, array $path) {
    $k = array_shift($path);
    if (!array_key_exists($k, $a)) { return; }
    if (empty($path)) { unset($a[$k]); return; }
    if (is_array($a[$k])) { unset_path($a[$k], $path); }
}
function ksort_recursive(array &$a) {
    ksort($a);
    foreach ($a as &$v) { if (is_array($v)) { ksort_recursive($v); } }
}
function request_hash(array $scope, array $input, array $volatilePaths) {
    return hash('sha256', implode("\n", $scope) . "\n" . normalize_request($input['body'], $input['query'], $volatilePaths));
}
```

### 方法 A: リクエストの中の項目で一意にする（最優先）

本文に注文番号や伝票番号のような「その操作を必ず一意に決める値」が入っているなら、それが鍵。自然キーには **期限がない**。注文番号 123 は 1 年後も注文番号 123 で、別の操作にはならない。

やることは 3 つ。

1. テーブルに UNIQUE 制約を張り、同じ鍵の行が 2 つ入らないようにする
2. 行には内容ハッシュも保存する
3. INSERT が「重複しています」エラー（MySQL のエラー番号 1062）になったら、**自然キーで** 既にある行を読む。見つからなければ別の UNIQUE 制約による 1062 なので元のエラーとして扱う。見つかって内容ハッシュが同じなら 1 回目と同じ応答を返す。違えば業務エラーを返す

```sql
ALTER TABLE orders
  ADD COLUMN request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,   -- 導入前の行は NULL のまま
  ADD UNIQUE KEY uq_orders_natural (tenant_id, order_no);
```

```php
try {
    $stmt = $pdo->prepare('INSERT INTO orders (tenant_id, order_no, amount, request_hash) VALUES (?, ?, ?, ?)');
    $stmt->execute(array($tenantId, $orderNo, $amount, $reqHash));
    $orderId = $pdo->lastInsertId();
    $isNew = true;
} catch (PDOException $e) {
    if ($e->errorInfo[1] != 1062) { throw $e; }
    $stmt = $pdo->prepare('SELECT id, request_hash FROM orders WHERE tenant_id = ? AND order_no = ?');
    $stmt->execute(array($tenantId, $orderNo));       // 書き込み側の DB に投げる（複製側は反映が遅れる）
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if ($row === false) { throw $e; }                 // 自然キーでは見つからない = 別の UNIQUE 制約の重複。元のエラー
    if ($row['request_hash'] === null || $row['request_hash'] !== $reqHash) {
        // 導入前の行（照合できない）、または同じ注文番号なのに内容が違う: 再送ではない
        send_existing_business_error();               // 今の API の「注文番号が重複しています」エラーをそのまま返す
        exit;
    }
    $orderId = $row['id'];
    $isNew = false;                                     // 既にある行を使って 1 回目と同じ応答を返す
}
```

導入前に作られた行は `request_hash` が NULL で内容を照合できない。そうした行に同じ注文番号のリクエストが来ても、それは導入より前の操作の再送ではあり得ない（再送は数分以内に来る）ので、再送扱いにはせず今まで通りの重複エラーを返す。元のリクエスト本文がログなどに残っていて埋められるなら埋めてもよいが、必須ではない。

`INSERT IGNORE` や `ON DUPLICATE KEY UPDATE` は使わない。`INSERT IGNORE` は重複以外のデータ異常（桁あふれ、NOT NULL 違反など）も警告に格下げして通してしまう。`ON DUPLICATE KEY UPDATE` は UNIQUE が 2 つ以上あるテーブルで「どの行を更新するか予測できない」と公式文書にある。普通の `INSERT` で 1062 だけを捕まえるのが安全。

- 向く: 「1 件作る」型の API で、本文に必ず一意になる値がある
- 向かない: 本文に一意になる値がない API（自由文のコメント投稿、「残高に +100」）。複数項目の組み合わせで「たぶん一意」にできる場合もあるが、それは推測キーであり誤検知が起き得る（6 章の決済の例を参照）。推測キーには期限を付ける
- 組み合わせに使う項目は「再送されても同じ値で届く」ものに限る。送るたびに変わる値（送信時刻など）を混ぜると、再送が別物に見えてしまう

### 方法 B: 内容のハッシュ + 時間窓（A が使えないときだけ）

「同じ利用者から、同じ API に、同じ内容が、N 秒以内に来たら再送とみなす」。鍵は内容ハッシュそのもの。

```php
$key    = 'fp:' . $reqHash;      // 鍵 = 内容ハッシュ（スコープ込み）
$window = 300;                   // 時間窓（秒）。API ごとに設定。観測モードの実測で決める
```

時間窓は **鍵テーブルの `expires_at` 列で実装する**（5 章）。鍵の行に「いつまで有効か」を持たせ、同じ鍵が来たら **受付時刻** が期限内なら再送、期限切れなら新しい操作として行を使い回す。時刻を丸めて鍵に混ぜる方法（「10 分単位のバケット」など）は、境界をまたいだ再送を見逃すので使わない。

- 向く: 間違えても実害が小さい API、または「同じ内容が続けて来たら 1 回でよい」API。例: 同じ本文のコメント二重投稿防止、同じ内容の申請フォーム
- 向かない: 同じ内容でも別の操作として数えたい API。例: 「カートに 1 個追加」「+100 ポイント」。2 回目が正当なのに 1 回にまとめられてしまう
- 時間窓の決め方: 再送はふつう「クライアントのタイムアウト × 試行回数 + 待ち時間」の範囲に収まる。観測モードで実測し、再送の 99% が入る長さに少し余裕を足す。目安は数分〜10 分。長すぎると正当な再操作まで止めてしまう

方法 B は推測なので、A が使えるなら必ず A を使う。

### 3 種類の鍵と期限の違い

| 鍵の種類 | `key_type` | 期限（`expires_at`） | 期限切れの行に同じ鍵が来たら |
|---|---|---|---|
| 自然キー（注文番号など） | `natural` | なし（NULL） | 起きない。UNIQUE が恒久的に守り、内容ハッシュで照合する |
| 推測キー（ユーザー + 商品 + 時刻など） | `guessed` | あり | 新しい操作として行を使い回す |
| 内容ハッシュ（方法 B） | `fingerprint` | あり | 新しい操作として行を使い回す |

これとは別に「保存した応答をいつまで持つか」（応答の保存期間）がある。自然キーの行は保存期間を過ぎたら鍵テーブルから消してよい。消えたあとに同じ注文番号が来ても、業務テーブルの UNIQUE が 1062 を返し、方法 A の分岐で既存の注文から応答を作り直せる。ただし作り直した応答は 1 回目の保存済み応答と完全に同じとは限らない（ヘッダや本文の細部が違い得る）。「1 回目と完全に同じ応答」を保証するのは応答の保存期間内だけ、と仕様に書く。

## 4. 方法の選び方

```
本文に、その操作を必ず一意に決める値（注文番号など）があるか？
  ├─ はい → 方法 A（自然キー + UNIQUE + 内容ハッシュ照合。期限なし）
  └─ いいえ
      複数項目の組み合わせで「たぶん一意」にできるか？（再送でも同じ値で届く項目だけ。観測ログで確認）
        ├─ はい → 方法 A の推測キー版（期限あり）。「誤って止めるリスクを伴う重複低減策」と仕様に明記する
        └─ いいえ
            同じ内容が数分以内に続けて来たとき、1 回にまとめてよい API か？
              ├─ はい → 方法 B（内容ハッシュ + 時間窓）
              └─ いいえ → API 側だけでは安全に冪等にできない。
                          クライアントが鍵（Idempotency-Key）を送れるようになるのを待つ。
                          それまでは観測モードで重複の件数だけ記録する
```

外部（決済代行、メール送信、他の API）を呼ぶ API は、上のどれを選んでも 6 章の状態機械が追加で必要。

## 5. 共通の実装: DB だけを書く API（PHP + MySQL）

方法 B のとき、または方法 A でも「複数のテーブルを更新する」「1 回目の応答をそのまま返したい」ときは、鍵テーブルで既存処理を包む。外部を呼ぶ API（決済など）はこの章ではなく 6 章。

### 鍵テーブルと観測テーブル

```sql
-- 強制モードで使う鍵テーブル
CREATE TABLE idempotency_keys (
  id               BIGINT       NOT NULL AUTO_INCREMENT,
  tenant_id        VARCHAR(64)  CHARACTER SET ascii COLLATE ascii_bin NOT NULL,   -- ログイン情報から
  idem_key         VARCHAR(191) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,   -- 'fp:<sha256>' や 'order:123'。大文字小文字を区別
  key_type         ENUM('natural', 'guessed', 'fingerprint') NOT NULL,
  request_hash     CHAR(64)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL,   -- 内容ハッシュ。鍵と同じでも必ず持つ
  status           ENUM('in_progress', 'done') NOT NULL,
  response_code    SMALLINT     NULL,
  response_headers TEXT         NULL,       -- JSON の [[名前, 値], ...]。同名ヘッダを複数持てる
  response_body    MEDIUMTEXT   NULL,       -- 5.7 未満は JSON 型がないので TEXT 系
  expires_at       DATETIME     NULL,       -- 期限。natural は NULL（期限なし）
  created_at       DATETIME     NOT NULL,   -- 受付時刻（DB の時計）
  PRIMARY KEY (id),
  UNIQUE KEY uq_key (tenant_id, idem_key),
  KEY idx_expires_at (expires_at),
  KEY idx_created_at (created_at)
) ENGINE=InnoDB;
-- 掃除（cron で 1 日 1 回など）。応答の保存期間を過ぎた行だけ消す:
-- DELETE FROM idempotency_keys WHERE created_at < NOW() - INTERVAL 7 DAY LIMIT 10000;

-- 観測モードで使う追記専用テーブル。消さない（別途アーカイブ）
CREATE TABLE idempotency_observations (
  id            BIGINT      NOT NULL AUTO_INCREMENT,
  tenant_id     VARCHAR(64)  CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  idem_key      VARCHAR(191) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  request_hash  CHAR(64)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  request_id    VARCHAR(64)  NULL,          -- アクセスログと突き合わせる用
  observed_at   DATETIME     NOT NULL,
  PRIMARY KEY (id),
  KEY idx_key_time (tenant_id, idem_key, observed_at)
) ENGINE=InnoDB;
```

鍵の列は `ascii_bin`（大文字小文字を区別）にする。将来クライアントが `Idempotency-Key` ヘッダを送るようになったとき、`abc` と `ABC` を別の鍵として扱えるようにするため。索引の長さ制限（MySQL 5.6 以前は 1 列 767 バイト）にも当たらない。

### 受付時刻を最初に固定する

期限の判定は「受付時刻が期限内か」で行う。「ロックを取れた時刻が期限内か」ではない。2 本目が期限内に届いても、1 本目の確定を待っている間に期限を過ぎることがあり、そのとき処理開始時刻で判定すると「新しい操作」として実行してしまう。

```php
$receivedAt = $pdo->query('SELECT NOW()')->fetchColumn();   // DB の時計で受付時刻を固定。以後の期限判定はすべてこの値と比較する
```

PHP の時計ではなく DB の時計を使うのは、`expires_at` も DB の時計で書くため。時計を 1 つにそろえる。

### 観測モード: 記録するだけ、同じ鍵を待たせない

```php
function idem_observe(PDO $pdo, array $scope, $key, $reqHash, $window, $receivedAt) {
    if ($pdo->inTransaction()) { return; }     // 既存処理のトランザクションの中では何もしない
    $origTimeout = null;
    try {
        $origTimeout = $pdo->query('SELECT @@session.innodb_lock_wait_timeout')->fetchColumn();   // 元の値を保存
        $pdo->exec('SET SESSION innodb_lock_wait_timeout = 2');   // 観測のために長く待たない
        // 追記（自動コミット）。行ロックや AUTO_INCREMENT の競合は普通の INSERT と同じ程度に起こり得るが、同じ鍵を意図的に直列化することはしない
        $ins = $pdo->prepare('INSERT INTO idempotency_observations
            (tenant_id, idem_key, request_hash, request_id, observed_at) VALUES (?, ?, ?, ?, ?)');
        $ins->execute(array($scope['tenant_id'], $key, $reqHash, request_id(), $receivedAt));
        // 直近の同じ鍵を見る（普通の SELECT。FOR UPDATE は付けない）
        $sel = $pdo->prepare('SELECT observed_at, request_hash FROM idempotency_observations
            WHERE tenant_id = ? AND idem_key = ? AND observed_at >= ? - INTERVAL ? SECOND
            ORDER BY id DESC LIMIT 1 OFFSET 1');
        $sel->execute(array($scope['tenant_id'], $key, $receivedAt, $window === null ? 86400 : $window));
        if ($prev = $sel->fetch(PDO::FETCH_ASSOC)) {
            error_log(sprintf('idempotency observe: duplicate candidate key=%s age=%ds same_hash=%d',
                $key, strtotime($receivedAt) - strtotime($prev['observed_at']), $prev['request_hash'] === $reqHash ? 1 : 0));
        }
    } catch (Exception $e) {                   // 観測の失敗で本処理を止めない。ログとメトリクスだけ
        error_log('idempotency observe: skipped due to ' . get_class($e) . ': ' . $e->getMessage());
        metrics_increment('idempotency.observe.error');
    }
    try {                                      // 復元も例外を外に出さない（PHP 5.4 には finally がないので別の try/catch）
        if ($origTimeout !== null) {           // DEFAULT ではなく保存した元の値に戻す（DEFAULT は元のセッション設定と同じとは限らない）
            $pdo->exec('SET SESSION innodb_lock_wait_timeout = ' . (int)$origTimeout);
        }
    } catch (Exception $e) {
        error_log('idempotency observe: failed to restore lock_wait_timeout: ' . $e->getMessage());
    }
}
```

観測モードでは鍵テーブルに触らず、トランザクションも SAVEPOINT も作らない。記録して `return` したあと、既存処理は今まで通り動く。**観測処理の中で起きた失敗（テーブル障害、容量不足、ロック待ち、接続障害）はすべて捕まえてログとメトリクスに落とし、本処理は続ける。** 観測モードなのに API が失敗しては「既存の動きを変えない」に反するため。ただし DB が遅いだけの場合は例外では防げないので、観測用の待ち時間は短くし、それでも足りなければ観測をログファイルへの追記（非同期に集計）に切り替える。INSERT が 1 本増えるぶんの負荷は、本番投入前に負荷試験で確認する。

### 強制モード: 包み方（各エンドポイントの先頭で `require` する共通ファイル）

既存処理との契約（包む前に既存処理側を直す）:

1. DB は `$pdo`（同じ接続）だけを使う。自分で `COMMIT` / `ROLLBACK` / `beginTransaction` しない
2. 終わるときは必ず `idem_ok()` / `idem_business_error()` / `idem_retryable_error()` のどれかを呼ぶ。**完了関数は戻ってこない**（中で `exit` する）ので、呼んだあとに処理を書かない
3. `ob_end_clean()` を呼ばない
4. 応答ヘッダのうち `Set-Cookie` は保存も再送もしない。再送に対してもクッキーを返す必要がある API は、この方式の対象にしない
5. `flush()`、`ob_flush()`、ストリーミング応答（少しずつ送る応答）を使わない。途中まで送ってしまうと、`idem_guard` が 503 に差し替えられなくなる

```php
<?php
// idempotency.php
$mode       = getenv('IDEMPOTENCY_MODE') ?: 'observe';   // 'observe'（観測） | 'enforce'（強制）
$receivedAt = $pdo->query('SELECT NOW()')->fetchColumn();
$input      = parse_and_validate_request();               // 3 章。失敗したらここで 400 を返して終わる
$scope      = idem_scope($auth);
list($key, $keyType, $window) = derive_key($scope, $input);   // 方法 A / B。keyType は 'natural' | 'guessed' | 'fingerprint'。natural は window = null。対象外なら key = null
if ($key === null) { return; }
$reqHash    = request_hash($scope, $input, array('nonce'));

if ($mode !== 'enforce') {
    idem_observe($pdo, $scope, $key, $reqHash, $window, $receivedAt);
    return;                                                // 既存処理はそのまま動く
}

$IDEM = array('pdo' => $pdo, 'tenant' => $scope['tenant_id'], 'key' => $key, 'finished' => false,
              'origTimeout' => $pdo->query('SELECT @@session.innodb_lock_wait_timeout')->fetchColumn());   // 終了時に戻すため元の値を保存
$expiresAt = ($window === null) ? null : idem_add_seconds($pdo, $receivedAt, $window);   // 受付時刻 + 時間窓（DB の時計で計算）
$pdo->exec('SET SESSION innodb_lock_wait_timeout = 10');   // 待ち時間の上限。PHP / Chocon / Nginx のタイムアウトより短く
register_shutdown_function('idem_restore_timeout');        // 保険: どの経路で終わっても（例外で落ちても）元の値へ戻す。各分岐でも明示的に戻す
$pdo->beginTransaction();
try {
    $ins = $pdo->prepare('INSERT INTO idempotency_keys
        (tenant_id, idem_key, key_type, request_hash, status, expires_at, created_at)
        VALUES (?, ?, ?, ?, "in_progress", ?, ?)');
    $ins->execute(array($scope['tenant_id'], $key, $keyType, $reqHash, $expiresAt, $receivedAt));
} catch (PDOException $e) {
    if ($e->errorInfo[1] == 1205) {                   // 待ち時間切れ: 先に来た同じ鍵の処理がまだ終わっていない
        $pdo->rollBack();
        idem_restore_timeout();
        idem_send(503, array(), '');                  // 今まで通りの 5xx。クライアントは今まで通り再送してくる
        exit;
    }
    if ($e->errorInfo[1] != 1062) { $pdo->rollBack(); idem_restore_timeout(); throw $e; }

    // 同じ鍵の行が既にある。行をロックして読む（先に来た処理は確定済みなので待たない）
    $sel = $pdo->prepare('SELECT id, key_type, request_hash, status, response_code, response_headers, response_body,
                                 (expires_at IS NOT NULL AND expires_at <= ?) AS expired      -- 受付時刻で判定
                          FROM idempotency_keys WHERE tenant_id = ? AND idem_key = ? FOR UPDATE');
    $sel->execute(array($receivedAt, $scope['tenant_id'], $key));
    $prev = $sel->fetch(PDO::FETCH_ASSOC);

    if ($prev['expired'] && $prev['key_type'] !== 'natural') {
        // 期限切れの推測キー / 内容ハッシュ: 新しい操作として使い回す（自然キーはここに来ない）
        $upd = $pdo->prepare('UPDATE idempotency_keys
            SET request_hash = ?, status = "in_progress", response_code = NULL, response_headers = NULL,
                response_body = NULL, expires_at = ?, created_at = ?
            WHERE id = ?');
        $upd->execute(array($reqHash, $expiresAt, $receivedAt, $prev['id']));
    } elseif ($prev['request_hash'] !== $reqHash) {
        $pdo->rollBack();                             // 同じ鍵で別の内容: 再送ではない
        idem_restore_timeout();
        send_existing_business_error();               // 今の API の業務エラー（400 など）をそのまま返す
        exit;
    } elseif ($prev['status'] === 'done') {
        $pdo->rollBack();                             // 再送: 1 回目と同じ応答を返す
        idem_restore_timeout();
        idem_send((int)$prev['response_code'], json_decode($prev['response_headers'], true), $prev['response_body'], true);
        exit;
    } else {
        // 期限内の in_progress。同じトランザクションで確定する設計ではここに来ない
        // （先の処理が終わるまで INSERT が待つため）。来たら想定外なので安全側に倒す
        $pdo->rollBack();
        idem_restore_timeout();
        idem_send(503, array(), '');
        exit;
    }
}

$pdo->exec('SAVEPOINT idem_business');               // 業務エラーのときはここまで戻す
ob_start();                                          // 既存処理が echo する応答を捕まえる
register_shutdown_function('idem_guard');           // 完了関数が呼ばれずに終わったら「取り消しだけ」する

// ---- 既存処理が呼ぶ完了関数。すべて exit で終わり、戻ってこない ----
function idem_ok($code = null) {                     // 正常終了: 業務の書き込みと応答を一緒に確定
    idem_finish($code, false);
}
function idem_business_error($code = null) {         // 業務エラー（在庫なし等）: 業務の書き込みは取り消し、エラー応答は保存
    idem_finish($code, true);
}
function idem_retryable_error($code = 503) {         // 一時的なエラー（DB / 外部 API 障害等）: 何も残さない。再送で普通に実行させる
    global $IDEM;
    ob_end_clean();
    $IDEM['pdo']->rollBack();
    $IDEM['finished'] = true;
    idem_restore_timeout();
    idem_send($code, array(), '');
    exit;
}

function idem_finish($code, $rollbackBusiness) {
    global $IDEM;
    $pdo  = $IDEM['pdo'];
    $body = ob_get_clean();
    if ($code === null) { $code = http_response_code(); }        // PHP 5.4 以上
    $headers = idem_pick_headers(headers_list());                 // Content-Type, Location, Cache-Control。Set-Cookie は除外
    if ($rollbackBusiness) { $pdo->exec('ROLLBACK TO SAVEPOINT idem_business'); }
    $u = $pdo->prepare('UPDATE idempotency_keys
        SET status = "done", response_code = ?, response_headers = ?, response_body = ?
        WHERE tenant_id = ? AND idem_key = ?');
    $u->execute(array($code, json_encode($headers), $body, $IDEM['tenant'], $IDEM['key']));
    $pdo->commit();                                               // 業務の書き込みと鍵の確定が同時に決まる
    $IDEM['finished'] = true;
    idem_restore_timeout();
    http_response_code($code);                                    // 1 回目: ヘッダは既存処理が設定済みなので本文だけ出す
    echo $body;
    exit;                                                         // ここで終わる。呼び出し元には戻らない
}

function idem_guard() {                              // 完了関数なしで終了 = 想定外。取り消して、成功らしい応答を流さない
    global $IDEM;
    if (!empty($IDEM['finished'])) { return; }
    if (ob_get_level() > 0) { ob_end_clean(); }       // 既存処理が出した本文（200 かもしれない）を捨てる
    if ($IDEM['pdo']->inTransaction()) { $IDEM['pdo']->rollBack(); }
    error_log('idempotency: finished without completion call key=' . $IDEM['key']);
    idem_remove_headers(array('Content-Type', 'Location', 'Cache-Control', 'Set-Cookie'));   // 既存処理が付けた成功用ヘッダを消す
    idem_restore_timeout();
    idem_send(503, existing_5xx_headers(), '');       // DB は取り消したので、クライアントにも「失敗」を返す。ヘッダは既存の 5xx と同じものに
}
function idem_restore_timeout() {                    // 永続接続だと次のリクエストに設定が残るので、終わる前に元の値へ戻す。失敗しても外に出さない
    global $IDEM;
    if (!empty($IDEM['timeoutRestored'])) { return; }  // 明示呼び出しと shutdown の両方から呼ばれても 1 回だけ実行
    try {
        if (isset($IDEM['origTimeout']) && $IDEM['origTimeout'] !== null) {
            if ($IDEM['pdo']->inTransaction()) { $IDEM['pdo']->rollBack(); }   // shutdown 経由で TX が残っていれば取り消してから戻す
            $IDEM['pdo']->exec('SET SESSION innodb_lock_wait_timeout = ' . (int)$IDEM['origTimeout']);
            $IDEM['timeoutRestored'] = true;
        }
    } catch (Exception $e) {
        error_log('idempotency: failed to restore lock_wait_timeout: ' . $e->getMessage());
    }
}

function idem_send($code, array $headers, $body, $replayed = false) {   // 保存済み応答の再送、または空の 5xx にだけ使う
    http_response_code($code);
    $names = array();
    foreach ($headers as $pair) { $names[$pair[0]] = true; }
    idem_remove_headers(array_keys($names));          // 認証処理などが先に付けた同名ヘッダを消してから、保存した順に付け直す
    foreach ($headers as $pair) { header($pair[0] . ': ' . $pair[1], false); }   // false = 同名ヘッダを上書きしない
    if ($replayed) { header('Idempotent-Replayed: true'); }
    echo $body;
}
function idem_remove_headers(array $names) {         // header_remove は PHP 5.3 以上
    if (headers_sent()) { return; }
    foreach ($names as $n) { header_remove($n); }
}
function idem_pick_headers(array $list) {            // "Name: value" の配列から、保存する分だけ [名前, 値] の並びで選ぶ
    $keep = array('content-type', 'location', 'cache-control');
    $out = array();
    foreach ($list as $line) {
        list($name, $value) = explode(':', $line, 2);
        if (in_array(strtolower(trim($name)), $keep)) { $out[] = array(trim($name), trim($value)); }
    }
    return $out;
}
function idem_add_seconds(PDO $pdo, $datetime, $seconds) {   // 期限の計算も DB の時計と関数で行う（PHP のタイムゾーンで解釈しない）
    $q = $pdo->prepare('SELECT DATE_ADD(?, INTERVAL ? SECOND)');
    $q->execute(array($datetime, (int)$seconds));
    return $q->fetchColumn();
}
// この下に既存処理が続く（require 元のスクリプト本体）。最後に idem_ok() などを呼ぶ
```

待ち時間の復元は二重にしてある。`SET SESSION` の直後に `idem_restore_timeout` を shutdown に登録し（保険。例外で落ちても戻る）、早期終了の 5 経路（待ち時間切れの 503、1062 以外の例外、内容不一致の業務エラー、保存済み応答の再送、想定外の `in_progress`）と完了関数・ガードでも ROLLBACK 直後に明示的に呼ぶ。復元関数は 1 回しか実行されないので、両方から呼ばれても無害。PDO の永続接続（`PDO::ATTR_PERSISTENT`）を使っていると、セッション変数は接続と一緒に次のリクエストへ引き継がれるため、どの経路で終わっても戻す必要がある。

完了関数の呼び忘れや「呼んだあとに処理を書いている」箇所は、コードレビューに加えて簡単な静的チェック（各エンドポイントに完了関数の呼び出しがあるか、呼び出しの直後に `exit` 以外の文が続いていないか）を CI で回す。

### 動きの説明

1. 受付時刻を DB の時計で固定する。入力検証を通し、鍵と内容ハッシュを作る
2. 「受付簿」（鍵テーブル）に「処理中」と書く。同じ鍵が既にあれば 1062 になる
3. 1062 のとき、その行をロックして読み、受付時刻で分ける。期限切れ（自然キー以外）なら新しい操作として使い回す。内容が違えば業務エラー。処理済みなら 1 回目と同じ応答を返す
4. 新しい行が入ったら SAVEPOINT を置き、本来の処理を実行し、応答を出力バッファで捕まえる
5. 既存処理が完了関数を呼ぶ。正常なら「処理済み + 応答」を書いて業務の書き込みと **同時に COMMIT**。業務エラーなら SAVEPOINT まで戻してから、エラー応答だけ保存して COMMIT。一時的なエラーなら全部 ROLLBACK して何も残さない。どの完了関数も `exit` で終わる
6. 完了関数を呼ばずに終わった（想定外）ときは、出力バッファを捨てて ROLLBACK し、503 を返す。DB を取り消したのにクライアントへ 200 が流れる、という食い違いを作らない。自動で COMMIT はしない
7. 同じ鍵が **同時に** 2 本来た場合、InnoDB は 2 本目の INSERT を 1 本目の確定まで待たせる。待ち時間の上限（`innodb_lock_wait_timeout`）を超えるとエラー 1205 になるので、今まで通りの 5xx を返す

### クライアントが変わらないからこそ守ること

- **新しいステータスコードを増やさない。** 前回文書の 409（処理中）や 422（同じ鍵で別の内容）は、クライアントがそれを理解して動く前提だった。今のクライアントは 409 を知らない。再送には **1 回目と同じ応答**（ステータス、Content-Type や Location などのヘッダ、本文）をそのまま返す
- 同じ鍵で内容が違うときは、その API が今使っている業務エラーを返す。保存済みの応答は返さない
- **処理中に 2 通目が来たら待つ。** 待ちきれなければ今まで通りの 5xx。クライアントは今まで通り再送し、次は保存済みの応答が返る
- **鍵・業務の書き込み・応答の保存は同じトランザクションで確定する。** Redis や memcached に先に予約する構成は、どこで落ちたかによって「二重実行」か「長時間ブロック」のどちらかが起きる（前回文書 2 章）。例外は 6 章の外部呼び出しで、そちらは照会で復旧できることを前提に別設計にする
- 保存して返す応答は「処理を始めたあとの応答」すべて。業務エラー（在庫なしなど）も保存して同じものを返さないと、再送で結果が変わる。一時的なエラーは鍵行ごと消えるので、自然に再実行される
- 鍵テーブルの掃除は応答の保存期間（`created_at`）を基準にする。期限（`expires_at`）は「同じ鍵を別の操作とみなしてよいか」の判定にだけ使い、掃除の基準にはしない

## 6. 外部を呼ぶ API: 決済の状態機械と照会復旧

決済 API はクレカ API → 決済代行を呼ぶ。この呼び出しは DB トランザクションの外にあるので、5 章の「同じトランザクションで確定」では守れない。外部通信の間ずっとトランザクションを開けておくのも、ロックが長引き接続が枯渇するのでやらない。

### 決済の鍵: 推測キーであることを明記する

**採用: 「テナント + ユーザー ID + 商品 ID（並べ替えたハッシュ）+ 本文の時刻」の組み合わせ（`key_type = guessed`、期限あり）。** クライアントは再送のときも同じ時刻を送ってくる想定で、これは観測モードで裏取りする（同じユーザー・同じ商品が数秒差で 2 通来た組の時刻を比べる）。

ただしこれは自然キーではなく **推測キー**。「再送なら同じ値になる」ことは観測で確かめられるが、「別の操作なら必ず違う値になる」ことは証明できない。正当な購入が同じ鍵になり得るケース:

- 同じ秒に 2 回連続で購入した
- 複数の端末で同時に同じ商品を買った
- クライアントが時刻を分単位などに丸めている
- 端末の時計が巻き戻った

そのため決済の仕様には「安全な冪等性」ではなく **「誤って止めるリスクを伴う重複低減策」** と書く。内容ハッシュ（数量・金額・支払い方法を含む）の照合で「同じ鍵で内容が違う」ケースは業務エラーとして表に出せる。同じ秒に完全に同じ内容で 2 回買うケースだけが 1 件にまとまり、これは受け入れる。まとまった件は突き合わせバッチ（後述）で件数を見る。

リスクは反対側にもある。**時間窓を超えて届いた本当の再送は、期限切れの試行として退避され、新しい `psp_order_id` で再課金される。** これは推測方式では避けられない見逃しで、二重課金になり得る。時間窓を長くすれば減るが、正当な再購入を止める誤検知が増える。観測で得た再送の時間差の分布（何秒以内に何 % か）と業務判断に基づいて時間窓を決め、**この残存リスクを受容することを業務側が承認する** のを導入条件とする。発生した二重課金は突き合わせバッチで検出し、返金で対処する。

方法 B（時刻を除いた内容ハッシュ + 時間窓）に切り替える場合も、以下の状態機械はそのまま使う。

### テーブル: 決済の試行レコード

決済前に「これから課金する」という記録と、決済代行に渡す注文 ID をここで確定する。

```sql
CREATE TABLE payment_attempts (
  id               BIGINT       NOT NULL AUTO_INCREMENT,
  tenant_id        VARCHAR(64)  CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  user_id          BIGINT       NOT NULL,
  idem_key         VARCHAR(191) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,   -- 'a:<商品ハッシュ>:<本文の時刻>' または 'fp:<内容ハッシュ>'
  key_type         ENUM('guessed', 'fingerprint') NOT NULL,
  request_hash     CHAR(64)     CHARACTER SET ascii COLLATE ascii_bin NOT NULL,   -- 数量・金額・支払い方法を含む内容ハッシュ
  psp_order_id     VARCHAR(27)  CHARACTER SET ascii COLLATE ascii_bin NULL,       -- 決済代行に渡す注文 ID。INSERT 直後に同じトランザクションで設定
  status           ENUM('created', 'charging', 'succeeded', 'failed', 'unknown', 'superseded') NOT NULL,   -- superseded = 期限切れで退避済み。課金も照会もしない
  owner_token      CHAR(32)     NULL,        -- いま処理権を持つプロセスの印
  lease_until      DATETIME     NULL,        -- 処理権の期限。過ぎたら他のプロセスが引き継いでよい
  dispatch_claim_count INT      NOT NULL DEFAULT 0,   -- 処理権を取った回数（外部呼び出し前に落ちても増える。実際の呼び出し回数ではない）
  superseded_at    DATETIME     NULL,        -- 退避した時刻。NULL なら有効な試行
  order_id         BIGINT       NULL,        -- 成功時に作った注文
  response_code    SMALLINT     NULL,
  response_headers TEXT         NULL,
  response_body    MEDIUMTEXT   NULL,
  expires_at       DATETIME     NOT NULL,    -- 推測キー / 内容ハッシュの期限（受付時刻 + 時間窓）
  created_at       DATETIME     NOT NULL,
  updated_at       DATETIME     NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_attempt_key (tenant_id, user_id, idem_key),
  UNIQUE KEY uq_psp_order (psp_order_id),                 -- NULL は複数許される
  KEY idx_status_lease (status, lease_until)
) ENGINE=InnoDB;

-- 注文は 1 つの試行から 1 件だけ作れる。複数プロセスが同時に「成功」を確定しようとしても二重にならない
ALTER TABLE orders
  ADD COLUMN payment_attempt_id BIGINT NULL,
  ADD UNIQUE KEY uq_orders_attempt (payment_attempt_id);
```

`psp_order_id` は AUTO_INCREMENT の `id` から作る（例: 接頭辞 + `id` をゼロ埋め）。接頭辞には **環境（本番 / ステージング）・加盟店・DB シャード** を識別する文字を含め、別環境や DB 再作成で `id` が 1 から振り直されても決済代行側で衝突しないことを確認する。`id` は INSERT するまで分からないので、**いったん NULL で INSERT し、`lastInsertId()` を取ってから同じトランザクションの中で UPDATE し、それから COMMIT** する。決済代行が許す長さと文字種に合わせる（27 文字程度に制限している代行会社が多い）。INSERT 前に決めたいなら乱数（UUID を短縮したもの）でもよい。

### 状態ごとの責務

| 状態 | 意味 | 再送や復旧バッチがこの状態を見たらやること |
|---|---|---|
| `created` | 試行レコードはあるが、まだ外部へ送っていない（送る前に落ちた場合もここ） | 照会しても見つからないので照会しない。**処理権を取って課金を始める**（条件付き UPDATE で 1 人だけ） |
| `charging` | 送ったかもしれない | **まず照会する**。確定できたら確定。「見つからない」かつ処理権の期限切れなら、処理権を引き継いで同じ `psp_order_id` で課金し直す。「処理中」なら待つ（5xx） |
| `unknown` | 送った。結果が分からない | 照会と再照合だけで確定させる。**課金し直さない** |
| `superseded` | 期限切れで退避された古い試行（外部へ送っていない） | **何もしない。** 課金も照会もしない。バッチの対象外 |
| `succeeded` / `failed` | 確定済み | 保存した応答を返す |

```
created ──(処理権取得)──> charging ──┬──> succeeded   （注文を書く。応答を保存）
                                     ├──> failed      （代行が「失敗」と確定）
                                     └──> unknown     （タイムアウト、接続切れ、代行の「重複」エラー、応答が読めない）
                                              │
                                              └── 照会 API ──> succeeded / failed
                                                            └─> unknown のまま（再照合バッチが繰り返す）
```

### 処理権（`owner_token`）の使い方

外部呼び出しを実行したプロセスが状態を変えるときは、必ず `owner_token = 自分の印` を条件に含める（比較して一致したときだけ更新する）。対象は「処理権の取得」「処理権の延長」「`charging` → `unknown`」。処理権の期限が切れたあとに別のプロセスが引き継いでいると、古い所有者の更新は件数 0 になり、古い結果で新しい状態を上書きできない。

例外は、照会 API の結果で `succeeded` / `failed` に確定するとき。これは決済代行が確定した事実なので、所有権に関係なく、4 の確定処理（再ロック + 旧状態条件 + 注文の UNIQUE）を通せば誰が書いてもよい。

### 流れ

1. **試行レコードを作る（短いトランザクション）**: `payment_attempts` に `status='created'`、`psp_order_id=NULL` で INSERT → `lastInsertId()` から `psp_order_id` を作って UPDATE → COMMIT。INSERT が 1062 なら「再送の扱い」へ
2. **処理権を取る（自動コミットの UPDATE 1 本）**: 更新件数が 1 の処理だけが先へ進む。0 なら誰かが先に取ったので「再送の扱い」へ

   ```sql
   UPDATE payment_attempts
   SET status = 'charging', owner_token = ?, lease_until = NOW() + INTERVAL 120 SECOND,
       dispatch_claim_count = dispatch_claim_count + 1, updated_at = NOW()
   WHERE id = ? AND status = 'created' AND superseded_at IS NULL;
   ```

   `lease_until` は「クレカ API のタイムアウト + 決済代行のタイムアウト + 余裕」より長くする。ここから先、DB トランザクションは開けない
3. **クレカ API を呼ぶ**: `psp_order_id` を渡す。クレカ API はそれをそのまま決済代行の加盟店注文 ID として送り、中で再送するときも同じ ID を使う
4. **結果を確定する（短いトランザクション。「確定処理」として 1 つの関数にまとめ、再送や復旧バッチからも同じ関数を呼ぶ）**:
   1. 試行レコードを `SELECT ... FOR UPDATE` で再ロックする
   2. すでに `succeeded` / `failed` なら、誰かが先に確定している。自分の結果は捨てて、保存済みの応答を返す
   3. 成功なら注文行を `payment_attempt_id` 付きで INSERT（UNIQUE が二重作成を防ぐ）。`UPDATE ... SET status='succeeded', ... WHERE id = ? AND status IN ('charging', 'unknown')` のように **期待する旧状態を条件に含める**。更新件数が 0 なら ROLLBACK して 2 に戻る
   4. 代行が「失敗」と確定（カード拒否など）なら `failed` + 応答を保存
   5. COMMIT して応答を返す
5. **分からなかったとき**: タイムアウト、接続切れ、代行の「注文 ID 重複」エラー、応答が読めない場合は `UPDATE ... SET status='unknown', updated_at=NOW() WHERE id = ? AND status='charging' AND owner_token = ?`（自動コミット）。**自分が処理権を持っているときだけ更新する。** 処理権の期限が切れて他のプロセスが引き継いでいれば更新件数は 0 になり、自分の結果は捨てて「再送の扱い」に回る。**「重複」エラーは成功ではない。** 先の試行が課金したかどうかは、照会しないと分からない。そのあと **ロックを持たずに** 1 回照会し、確定できれば 4 の確定処理を呼ぶ。確定できなければ今まで通りの 5xx を返す（クライアントは再送してくる）
6. **再照合バッチ（数分おき）**: `unknown` の試行、`charging` のまま `lease_until` を過ぎた試行、そして `created` のまま一定時間（例: 2 分）経った試行を拾う。どの対象も `superseded_at IS NULL` を条件に含め、退避済みの行は拾わない。`unknown` と `charging` はロックを持たずに照会し、確定できたら 4 の確定処理を呼ぶ。`charging` で「見つからない」なら処理権を引き継いで 3 からやり直す（下記）。`created` は照会せず、2 の条件付き UPDATE で処理権を取って 3 から進める。バッチが複数同時に動いても、確定処理の再ロックと旧状態条件と注文の UNIQUE で二重にならない

### 再送の扱い（1 の INSERT が 1062 になったとき）

短いトランザクションで既存の試行を `SELECT ... FOR UPDATE` し、読んだら **すぐ COMMIT してロックを離す**。照会 API はロックを持ったまま呼ばない。判断は次の表で、状態を変えるときは必ず 4 の確定処理（再ロック + 旧状態条件）か、2 の条件付き UPDATE を通す。

| 既存の状態 | 内容ハッシュ | やること |
|---|---|---|
| どれでも | 違う | 業務エラー（今の API の 400 など）。再送ではない |
| どれでも | 同じ、かつ受付時刻が `expires_at` を過ぎている | 古い行を退避してから、新しい試行として 1 からやり直す。退避 = `idem_key` を固定長の退避キー `'x:' + sha256(元の鍵 + ':' + id)` に書き換え（191 文字を超えない）、`superseded_at = NOW()` を入れ、旧状態が `created` なら `status = 'superseded'` にも変える（旧状態を条件にした UPDATE）。**`created` を退避したまま残すと、再照合バッチが拾って後から課金してしまう** ため。旧状態が `succeeded` / `failed` はそのまま（確定済み）。旧状態が `charging` / `unknown` なら退避せず、下の行に従う |
| `succeeded` / `failed` | 同じ | 保存した応答をそのまま返す（`Idempotent-Replayed: true`） |
| `created` | 同じ | 2 の条件付き UPDATE で処理権を取り、3 から進める。取れなければ `charging` として扱う |
| `charging`（処理権の期限内） | 同じ | ロックを離してから照会。確定できたら 4 の確定処理。「見つからない」「処理中」なら今まで通りの 5xx（相手がまだ動いている） |
| `charging`（処理権の期限切れ） | 同じ | ロックを離してから照会。確定できたら 4。「見つからない」なら処理権を引き継ぐ: `UPDATE ... SET owner_token = ?, lease_until = ..., dispatch_claim_count = dispatch_claim_count + 1 WHERE id = ? AND status = 'charging' AND lease_until < NOW()`。更新件数 1 なら同じ `psp_order_id` で 3 から進める。0 なら誰かが引き継いだので 5xx |
| `unknown` | 同じ | ロックを離してから照会。確定できたら 4。できなければ今まで通りの 5xx。課金し直さない |

### 後ろの段に必要なこと

| 壊れる場所 | 何が起きるか | 誰が守るか |
|---|---|---|
| クライアント → 決済 API の再送（時間窓内） | 同じ決済が 2 回届く | 試行レコードの UNIQUE + 処理権の条件付き UPDATE（本章） |
| クライアント → 決済 API の再送（時間窓を超えた遅延） | 新しい購入として再課金される | 防げない。残存リスクとして業務承認し、突き合わせバッチで検出して返金 |
| 試行を作ったあと、処理権を取る前に PHP が落ちる | `created` のまま残る | 再送か再照合バッチが処理権を取って課金する |
| 期限切れの `created` を退避したあと、バッチが古い行を拾う | 新しい試行と古い試行の両方が課金される | 退避時に `superseded` へ変え、バッチは `superseded_at IS NULL` だけを対象にする |
| 決済 API → クレカ API のタイムアウト | 課金されたか分からない | `unknown` + 照会（本章） |
| クレカ API 成功後、DB 書き込み前に PHP が落ちる | 課金済みだが `charging` のまま | 再照合バッチが照会して確定処理で `succeeded` にする |
| 再送と再照合バッチが同時に「成功」を見つける | 注文が 2 件できそうになる | 確定処理の再ロック + 旧状態条件 + `orders.payment_attempt_id` の UNIQUE |
| クレカ API → 決済代行のタイムアウト、クレカ API 内の再送 | 決済代行側で二重になり得る | 同じ `psp_order_id` を通す + 決済代行の重複拒否 |

決済代行に確認すること: 同じ加盟店注文 ID を送ったとき「重複エラーになる」のか「同じ結果を返す」のか、照会 API があるか、照会が「処理中」と「見つからない」を区別できるか、注文 ID の長さと文字種。重複エラーになるだけの代行会社でも二重課金は防げるが、API 側はそのエラーを成功として扱えないので、照会は必須。

クレカ API はサーバ側なので「クライアントを変えられない」制約の外にある。外部から注文 ID を受け取って決済代行に渡す改修と、照会を中継するエンドポイントが必要。

最後に **日次の突き合わせバッチ** を用意する。DB の試行 / 注文と決済代行の取引一覧を `psp_order_id` で照合し、片方にしかないもの、状態が食い違うものを一覧にする。上の仕組みが正しく動いているかの確認にもなり、漏れた事故を翌日には見つけられる。決済系では定番の安全網。

## 7. 導入手順（観測 → 強制）

1. **テーブルを作り、既存処理を棚卸しする**: 5 章の 2 テーブル（決済は 6 章の試行テーブルと注文の列）を InnoDB で作る。包む対象の既存処理について、DB 接続の張り方と `COMMIT` / `exit()` / `ob_end_clean()` / `Set-Cookie` の場所を grep しておく（8 章 1〜3）
2. **観測モード** (`IDEMPOTENCY_MODE=observe`): 鍵と内容ハッシュを計算し、観測テーブルに追記してログを出すだけ。本番投入前に負荷試験で INSERT 1 本分の影響を確認する
3. **1〜2 週間測る**: 重複候補の件数、時間差の分布（何秒以内に何 % か）、同じ鍵で内容ハッシュが違う件数、「同じ内容だが正当な別操作」が混ざっていないか。決済は「再送で本文の時刻が同じか」を確認する。時間差の分布から時間窓を決める
4. **既存処理に完了関数を入れる**: 正常終了・業務エラー・一時的なエラーの 3 か所で `idem_ok()` / `idem_business_error()` / `idem_retryable_error()` を呼ぶ。呼んだあとに処理が続いていないことを静的チェックで確認する。決済は 6 章の状態機械に合わせて処理を分ける
5. **強制モード** (`IDEMPOTENCY_MODE=enforce`): 影響が小さい API 1 本から切り替える。戻すときは環境変数を戻すだけ。決済は再照合バッチと突き合わせバッチが動いてから切り替える
6. **見る数字**: `Idempotent-Replayed: true` の件数（≒ 防げた二重実行）、内容ハッシュ不一致の件数、完了関数なしで終わった件数（ゼロであるべき）、決済は `created` / `charging` / `unknown` の滞留数と突き合わせの差分
7. **クライアントが変えられるようになったら**: `derive_key` を「`Idempotency-Key` ヘッダがあればそれを優先、なければ今までの作り方」に変える。順番は必ず API 側が先、クライアントのヘッダ付与が後。ヘッダを付けると HTTP ライブラリが自動で再送を始めることがあり、API 側が冪等になる前に付けると二重実行を招く（前回文書参照）

### 運用要件（保存するデータの扱い）

- 保存する応答本文、観測テーブルの鍵、内容ハッシュの元になる本文に、カード番号・セキュリティコード・氏名や住所などの個人情報が含まれないことを確認する。含まれる API では、応答は ID と状態だけを保存して返すときに組み立て直す
- 内容ハッシュの入力から個人情報を **単純に除かない**。除くと「同じ鍵で住所だけ違う」ような内容不一致を検出できなくなる。住所など業務上意味のある値は、非機密の ID（住所帳の ID など）に置き換えるか、サーバ側の秘密鍵で HMAC を取った値に置き換えてからハッシュに入れる。カード番号はトークンに置き換える
- セキュリティコード（CVV）は、ハッシュや HMAC を含めどんな形でも保存しない。ハッシュの入力にも入れない
- 保存期間を決める（鍵テーブルは応答の保存期間、観測テーブルはアーカイブまでの期間、決済の試行レコードは会計・監査の要件に合わせる）
- 鍵テーブル・観測テーブル・試行レコードの閲覧権限を、本番 DB の他のテーブルと同じ基準で制限する

## 8. 落とし穴（古い PHP + MySQL 固有のものを含む）

1. **既存処理が別の DB 接続を張る、または自分で COMMIT する**: 古い PHP は関数ごとに `mysql_connect` を呼んだり、`mysql_query('COMMIT')` を書いていることがある。別の接続からの書き込みは鍵のトランザクションの外になるので、二重実行を防げない。包む前に既存処理の接続と COMMIT の場所を grep する
2. **`mysql_pconnect`（永続接続）**: 途中で落ちたときトランザクションが開いたまま次のリクエストに引き継がれることがある。包む前に `ROLLBACK` を打つか、永続接続をやめる
3. **既存処理が完了関数を呼ばずに `exit()` / `die()` する、または呼んだあとに処理を続ける**: 呼ばない場合は `idem_guard` が取り消すので二重実行にはならないが、鍵が残らず再送のたびに実行される。呼んだあとの処理は完了関数が `exit` するので走らないが、意図しない書き方なので静的チェックで見つける。`ob_end_clean()` を既存処理が呼んでいると応答を捕まえられないので確認する
4. **既存テーブルへの列追加と UNIQUE**: 古い MySQL（5.6 より前）の `ALTER TABLE` はテーブル全体をロックする。手順は「NULL 可で列を足す → 既存行の重複を調べる → 段階的に埋める → UNIQUE を張る」。MySQL の UNIQUE は NULL を複数許すので、古い行は NULL のままでよい。大きいテーブルは `pt-online-schema-change` などを検討する
5. **ハッシュに「送るたびに変わる項目」が混ざる**: `nonce`、trace id などが本文にあると、再送のたびにハッシュが変わってまったく効かない。パス指定で除いてからハッシュし、観測モードの検知件数で効いているか確認する。逆に、決済の「押した時刻」のように再送でも同じ値なら除かない
6. **正当な同じ内容のリクエストを止めてしまう**: 「+1」「+100」型の API に方法 B を使うと、2 回目の正当な操作が消える。方法 B を使う API は「同じ内容が続いたら 1 回でよい」ものに限る
7. **テナント・ユーザーの分離**: 鍵の先頭は必ずログイン情報から取ったテナント / ユーザー ID。本文の値を使うと、別のユーザーに保存済みの応答を返してしまう事故になる
8. **外部への副作用は 5 章では守れない**: 決済、メール送信、他の API への POST は 6 章の状態機械で扱う。「送信内容を DB に書いてバッチが送る」（Outbox）も、送信成功と送信済み記録の間で落ちれば二重送信になる。Outbox は「少なくとも 1 回」であって「ちょうど 1 回」ではなく、送信先の冪等性（同じ ID の重複拒否）が別途必要
9. **`CLIENT_FOUND_ROWS`**: PHP は既定で無効だが、`PDO::MYSQL_ATTR_FOUND_ROWS => true` を付けた接続や、フレームワークが有効にしている場合は、更新行数が「条件に一致した行数」になり、処理権の条件付き UPDATE や旧状態条件の判定が崩れる
10. **読み取り専用の複製 DB（リードレプリカ）**: 重複を見つけたあとの「既にある行を SELECT」は書き込み側の DB に投げる。複製側だと反映が遅れて見つからず、二重実行に戻る
11. **応答の保存サイズ**: 大きな応答を `MEDIUMTEXT` に保存すると鍵テーブルが太る。保存するのは同じ応答を作り直すのに必要な最小（ID と状態、必要なヘッダ）にして、返すときに組み立て直してもよい
12. **経路に隠れた再送を足さない**: Nginx の `proxy_next_upstream` に `non_idempotent` や `http_502` を足すと、鍵の有無に関係なく全部の POST が再送される層になる
13. **「応答を送り終えたか」で判定しない**: `connection_aborted()` や Nginx の `$request_completion` は、クライアントが応答を受け取った証拠にならない（送り終えた直後にクライアント側でタイムアウトしていることがある）。特に決済では使わない
14. **期限判定を処理時刻で行わない**: ロック待ちのあとの時刻で判定すると、期限内に届いた再送を新しい操作として実行してしまう。受付時刻を最初に固定し、すべての判定でそれと比べる
15. **`flush()` / `ob_flush()` / ストリーミング応答**: 途中まで送ると、完了関数の呼び忘れ時に `idem_guard` が 503 に差し替えられず、再送時にも保存した応答を丸ごと返せない。対象エンドポイントでは禁止し、grep で確認する
16. **セッション変数が永続接続に残る**: `SET SESSION innodb_lock_wait_timeout` は接続に付く設定なので、PDO 永続接続では次のリクエストにも効いてしまう。観測モード・強制モードとも、終わる前に保存した元の値へ戻す
17. **PHP と DB の時計を混ぜない**: 受付時刻も `expires_at` も DB の `NOW()` で作る。PHP の `time()` と比べると、時計のずれの分だけ判定が狂う

## 9. テスト観点（チェックリスト）

DB だけを書く API（5 章）:

- [ ] 同じリクエストを 2 回続けて送ると、2 回目は 1 回目と同じステータス・ヘッダ（Content-Type, Location。同名ヘッダが複数あっても全部）・本文で、`Idempotent-Replayed: true` が付く。業務レコードは 1 件
- [ ] 同じ鍵で内容が違うリクエストは、保存済み応答ではなく業務エラーになる
- [ ] 導入前の行（`request_hash` が NULL）に同じ自然キーが来ると、今まで通りの重複エラーになる
- [ ] 方法 A で別の UNIQUE 制約による 1062 が起きたとき、自然キーで行が見つからず元のエラーが返る
- [ ] 時間窓（`expires_at`）を過ぎてからの同じ内容は、新規として処理される
- [ ] **期限をまたぐ長い処理**: 1 本目を `sleep()` で時間窓より長く引き延ばし、その間（期限内）に 2 本目を送る。2 本目は 1 本目の確定後に「再送」として保存済み応答を返し、新しい操作として実行されない
- [ ] 自然キーの行は期限がなく、何日経っても同じ鍵は再送扱い（内容が同じなら保存済み応答、違えば業務エラー）
- [ ] 同じリクエストを **同時に** 2 本送ると（`ab -c 2 -n 2` や `curl` を `&` で 2 本）、片方が待ってから保存済みの応答を返す。レコードは 1 件
- [ ] 先に来た処理を `sleep()` で長引かせると、後の処理が待ち時間切れで 503 になり、先の COMMIT 後の再送で保存済みの応答が返る
- [ ] 業務エラー（在庫なしなど）で `idem_business_error()` を呼ぶと、業務の書き込みは残らず、2 回目も同じエラー応答が返る
- [ ] `idem_retryable_error()` を呼ぶと鍵行が残らず、2 回目は普通に実行される
- [ ] 完了関数のあとに `echo` や DB 更新を書いても、実行されない（完了関数が `exit` する）
- [ ] 完了関数を呼ばずに `exit()` すると、鍵行も業務の書き込みも残らず、ログに記録され、クライアントには（既存処理が 200 の本文を出していても）503 が返る
- [ ] PHP の致命的エラーで終わったとき、鍵行も業務の書き込みも残らない
- [ ] 別のテナント / ユーザーが同じ本文を送っても、別扱いになる
- [ ] 不正な JSON、トップレベルがオブジェクトでない JSON、桁あふれする整数は、冪等処理に入る前に 400 で終わる
- [ ] 観測モードでは、同時に 2 本送っても両方が待たずに今まで通り処理され、観測テーブルに 2 行入り、ログに重複候補が出る
- [ ] 観測テーブルを DROP / ロック / 容量不足にしても、観測モードの API は今まで通り成功し、ログとメトリクスに観測失敗が出る
- [ ] 1 回目の応答で `Content-Type` などのヘッダが二重に付かない。再送時は保存したヘッダが付く

決済 API（6 章）:

- [ ] 同じ内容の再送で試行レコードは 1 件、`psp_order_id` は 1 つ、決済代行への課金は 1 回
- [ ] 試行を作った直後（処理権を取る前）に PHP を落とすと `created` で残り、再送が処理権を取って課金し、注文が 1 件できる
- [ ] 同じ `created` の試行に 2 本同時に再送しても、処理権を取れるのは 1 本だけ。もう 1 本は 5xx か保存済み応答
- [ ] クレカ API をタイムアウトさせると試行が `unknown` になり、照会で `succeeded` / `failed` に確定する。確定できない間は 5xx が返り、再照合バッチが後で確定させる。`unknown` からは課金し直さない
- [ ] 決済代行から「注文 ID 重複」エラーが返っても成功扱いにならず、照会で確定する
- [ ] クレカ API 成功後・DB 書き込み前に PHP を落とすと `charging` のまま残り、再照合バッチが照会して `succeeded` にして注文を作る
- [ ] `charging` で処理権の期限が切れ、照会が「見つからない」のとき、処理権を引き継いで同じ `psp_order_id` で課金し直す。決済代行側の課金は 1 回
- [ ] 処理権を引き継がれた古いプロセスが `unknown` へ更新しようとしても件数 0 で、新しい所有者の状態を上書きしない
- [ ] `created` のまま一定時間経った試行を再照合バッチが拾い、処理権を取って課金する
- [ ] **期限切れ `created` の退避**: 試行 A を `created` で作って PHP を止め、期限切れ後に再送して試行 B が課金されたあと、再照合バッチを動かしても A（`superseded`）は課金されない。決済代行側の課金は 1 回
- [ ] 観測モードで DB 接続を切っても、待ち時間の復元処理で例外が外に出ず、既存処理の失敗は今までと同じ形になる
- [ ] 完了関数を呼ばずに終わったとき、既存処理が付けた `Location` や `Set-Cookie` が 503 応答に残らない
- [ ] 再送時、認証処理が先に付けた `Content-Type` と保存済みの `Content-Type` が重複しない
- [ ] 永続接続で強制モードのリクエストを処理したあと、同じ接続の次のリクエストで `@@session.innodb_lock_wait_timeout` が元の値に戻っている。正常終了だけでなく、待ち時間切れの 503、内容不一致、保存済み応答の再送、1062 以外の例外、完了関数の呼び忘れ、の各経路で確認する
- [ ] 対象エンドポイントに `flush()` / `ob_flush()` / ストリーミング応答がない（grep）
- [ ] 時間窓を超えて届いた同じ内容の再送は新しい試行として再課金され、突き合わせバッチが二重を検出する（受容した残存リスクの確認）
- [ ] **再送と再照合バッチが同時に「成功」を確定しようとしても**、注文は 1 件で、後から来たほうは保存済み応答を返す（`orders.payment_attempt_id` の UNIQUE と旧状態条件で守られる）
- [ ] 照会 API を呼んでいる間、`information_schema.INNODB_TRX` にその試行行をロックしたトランザクションがない
- [ ] 同じ鍵で数量や金額が違うリクエストは業務エラーになる
- [ ] 突き合わせバッチが、DB にだけある試行・決済代行にだけある取引・状態の食い違いを一覧にする

## 10. 前回文書との関係と参考

- 前回文書「冪等性のない API に簡単に冪等性を付ける方法」の (1) DB ユニーク制約 = 本書の方法 A。(2) 鍵テーブル方式 = 本書 5 章。本書は「鍵をクライアント以外から得る」部分、観測モードの分離、明示的な完了処理、内容ハッシュの照合、外部を呼ぶ API の状態機械と処理権を追加したもの
- 経路の事実（Nginx / Chocon は POST を再送しない、Go の HTTP ライブラリの自動再送の条件、ヘッダは改修なしで API に届く）は前回文書 6 章の検証済み表を参照
- 本書で置いた前提（未検証）: InnoDB が同じ鍵の 2 本目の INSERT を 1 本目の確定まで待たせる動きと、待ち時間切れでエラー 1205 になる動き。一般に知られた挙動だが、対象の MySQL バージョンで動作確認する
- 参考: https://brandur.org/idempotency-keys（(user_id, idempotency_key) UNIQUE + 同じトランザクションで確定する設計、外部呼び出しを含む場合の復旧ポイントの考え方）、https://docs.stripe.com/api/idempotent_requests（どの応答を保存して返すか）、https://dev.mysql.com/doc/refman/8.0/en/insert-on-duplicate.html（更新行数と「予測できない」の注記）、https://dev.mysql.com/doc/refman/8.0/en/innodb-locks-set.html（重複キー検査時のロック）

## 付録: レビュー指摘への対応表

### 1 回目

| 指摘 | 対応箇所 |
|---|---|
| 方法 B の時間窓が実装されていない | 鍵テーブルに `expires_at` を追加し、1062 時に期限切れなら行を使い回す（5 章）。時刻のバケット化は不採用 |
| 観測モードが観測だけになっていない | 追記専用の観測テーブルに自動コミットで 1 行書くだけに変更。鍵テーブル・トランザクション・ロック待ちの設定を使わない（5 章） |
| 決済の鍵は自然キーではなく推測キー | 用語に「推測キー」を追加し、決済は「誤って止めるリスクを伴う重複低減策」と明記。衝突するケースを列挙（6 章） |
| shutdown 時の自動 COMMIT は危険 | 完了関数（正常 / 業務エラー / 一時的エラー）を既存処理が明示的に呼ぶ契約に変更。shutdown は ROLLBACK 専用。業務エラーは SAVEPOINT で業務の書き込みだけ取り消す（5 章） |
| 決済代行の重複仕様への依存が強すぎる | 試行レコードと `psp_order_id` を決済前に確定し、状態機械 + 照会 + 再照合バッチで復旧。「重複」エラーを成功扱いしない。外部呼び出し中はトランザクションを開けない（6 章） |
| ハッシュのスコープが tenant だけ | ユーザー ID、API 操作名、バージョン、意味のあるクエリを含める（3 章） |
| 1062 時に内容を照合していない | `request_hash` を保存し、違えば業務エラー（3 章、5 章、6 章） |
| `INSERT IGNORE` | 普通の `INSERT` + 1062 のみ捕捉に変更（3 章、5 章） |
| 応答の保存がステータスと本文だけ | `response_headers` を保存し、同じ応答を作り直す（5 章） |
| `connection_aborted()` / `$request_completion` | 判定案を削除し、落とし穴 13 として「使わない」を明記 |
| AUTO_INCREMENT 列がない | `id BIGINT AUTO_INCREMENT` を追加（5 章、6 章） |
| `idem_key` の照合順序 | `ascii_bin` に変更（5 章、6 章） |
| 既存テーブルへの NOT NULL + UNIQUE 追加 | NULL 可で追加 → 重複調査 → 段階的に埋める → UNIQUE の手順を落とし穴 4 に記載 |
| Outbox は at-least-once | 落とし穴 8 に明記し、送信先の冪等性が別途必要とした |

### 2 回目

| 指摘 | 対応箇所 |
|---|---|
| 完了関数が呼び出し元へ戻ってしまう | 3 つの完了関数すべてが `exit` で終わる契約に変更。「呼んだあとに処理を書かない」を契約に明記し、静的チェックを導入手順 4 に追加（5 章、7 章） |
| 決済の `created` が永久に取り残される | 状態ごとの責務表を追加。`created` は照会せず条件付き UPDATE で処理権を取って課金。`owner_token` / `lease_until` / `attempt_count` を追加し、`charging` の期限切れは照会後に引き継ぎ（6 章） |
| 時間窓の期限判定がロック待ち後の時刻になる | 受付時刻を処理冒頭で DB の `NOW()` から固定し、`expires_at <= 受付時刻` で判定。期限をまたぐ長い処理のテストを追加（5 章、9 章） |
| 自然キーと時間窓の期限を同じ意味で扱っている | `key_type` を追加。自然キーは `expires_at` NULL で期限切れ再利用の分岐に入らない。応答の保存期間は別概念として掃除の基準にだけ使う（3 章、5 章） |
| `FOR UPDATE` 中に照会 API を呼ぶように読める | 読んだらすぐ COMMIT してロックを離し、照会後に確定処理で再ロックする流れを明記（6 章） |
| 複数の再照合処理が同時に成功を検出したときの二重作成 | `orders.payment_attempt_id` に UNIQUE、確定処理で試行行を再ロック、既に `succeeded` なら既存を返す、状態更新は旧状態を条件に含める（6 章） |
| `psp_order_id` を AUTO_INCREMENT から作れない | NULL で INSERT → `lastInsertId()` → 同じトランザクションで UPDATE → COMMIT の手順を明記。乱数の代替も記載（6 章） |
| 方法 A の 1062 が別の UNIQUE によるかもしれない | 自然キーで SELECT して見つからなければ元のエラーとして再送出（3 章） |
| `idem_scope()` の `$auth` が参照できない | 引数で渡す形に変更（3 章） |
| 不正 JSON・スカラー・巨大整数・ネストした可変項目 | 入力検証 → 鍵とハッシュ → 冪等処理の順序を明記。`JSON_BIGINT_AS_STRING`、除外項目のパス指定（3 章） |
| 観測テーブルも「ロックなし」ではない | 「同じ鍵を意図的に直列化しない」に表現を修正し、負荷試験を導入手順に追加（5 章、7 章） |
| 応答ヘッダの連想配列では同名複数を保持できない、`Set-Cookie` の扱い | `[[名前, 値], ...]` の並びで保存し `header(..., false)` で復元。`Set-Cookie` は保存も再送もしないと契約に明記（5 章） |
| 既存行の `request_hash = NULL` に再送が来ると必ず不一致 | 導入前の行は「照合できない = 再送扱いしない」と決め、今まで通りの重複エラーを返す規則を明記（3 章） |

### 3 回目

| 指摘 | 対応箇所 |
|---|---|
| 完了関数を呼び忘れると DB は ROLLBACK、クライアントには成功応答 | `idem_guard` で出力バッファを捨て、ROLLBACK し、503 を返す（5 章） |
| 観測用 DB 書き込みの失敗が本処理を止める | `idem_observe` 全体を try/catch で囲み、失敗はログとメトリクスだけにして本処理を続ける。観測用の待ち時間を短くし、DB 遅延にはログファイル追記への切り替えを用意（5 章） |
| `owner_token` が状態遷移条件に使われていない | 「処理権の使い方」を追加。取得・延長・`charging` → `unknown` は `owner_token` を条件に含める。照会結果による確定は再ロック + 旧状態条件で所有権に関係なく可（6 章） |
| 遅延再送による二重課金が見えにくい | 「時間窓を超えた遅延再送は新しい購入として再課金され得る。観測値と業務判断で受容し、業務承認を導入条件にする」を明記。後ろの段の表にも行を追加（6 章） |
| 再照合バッチが `created` を対象にしていない | 一定時間経った `created` も拾い、処理権を取って課金する（6 章） |
| `idem_send` が初回応答でヘッダを二重に付ける | 初回は本文だけ出し、保存ヘッダの再設定は再送時だけにした（5 章） |
| `idem_add_seconds` が DB 由来の時刻を PHP のタイムゾーンで解釈 | SQL の `DATE_ADD(?, INTERVAL ? SECOND)` で計算するよう変更（5 章） |
| `:expired:<id>` で `idem_key` が 191 文字を超え得る | 固定長の退避キー `'x:' + sha256(...)` に変更（6 章） |
| 自然キーの鍵行削除後は再構成した応答になる | 「完全に同じ応答を保証するのは応答の保存期間内だけ」と明記（3 章） |
| 保存データの個人情報・保存期間・閲覧権限 | 7 章末に「運用要件」を追加 |

### 4 回目

| 指摘 | 対応箇所 |
|---|---|
| 期限切れの `created` を退避したあと再照合バッチが拾って課金する（二重課金経路） | `superseded` 状態と `superseded_at` 列を追加。退避時に `created` は `superseded` へ変え、処理権の取得とバッチの対象は `superseded_at IS NULL` に限定。責務表・後ろの段の表・テストに追加（6 章、9 章） |
| 観測の待ち時間復元が catch の外で、`DEFAULT` も元の値と限らない | 元の値を `@@session.innodb_lock_wait_timeout` から保存し、復元を別の try/catch で行う（PHP 5.4 に finally がないため）（5 章） |
| ガード時に既存処理のヘッダーが残る | `idem_guard` で保存対象ヘッダーと `Set-Cookie` を `header_remove` し、既存の 5xx 用ヘッダーに置き換える（5 章） |
| 再送時に先に設定された同名ヘッダーと重複する | `idem_send` で対象名を `header_remove` してから保存順に追加（5 章） |
| 個人情報をハッシュ入力から除くと不一致検出力を失う | 非機密 ID への置換またはサーバ秘密鍵の HMAC に変更。CVV はいかなる形でも保存しない（7 章 運用要件） |
| `attempt_count` の名称が不正確 | `dispatch_claim_count`（処理権を取った回数）に改名（6 章） |
| `psp_order_id` が別環境や DB 再作成で衝突し得る | 環境・加盟店・シャードの接頭辞を含め、衝突しないことの確認を明記（6 章） |

### 5 回目（設計レビュー完了）

| 指摘 | 対応箇所 |
|---|---|
| 強制モードで変えた `innodb_lock_wait_timeout` も PDO 永続接続なら終了前に元へ戻す | 元の値を保存し、すべての終了経路で `idem_restore_timeout()` を呼ぶ。落とし穴 16（5 章、8 章） |
| 対象エンドポイントでは `flush()`、`ob_flush()`、ストリーミング応答を禁止 | 契約 5 と落とし穴 15 に追加。grep をテストに追加（5 章、8 章、9 章） |

### 6 回目

| 指摘 | 対応箇所 |
|---|---|
| 強制モードの早期終了 5 経路（1205 の 503 / 内容ハッシュ不一致 / 保存済み応答の再送 / 想定外の `in_progress` / 1062 以外の例外）で待ち時間を復元していない | `SET SESSION` 直後に `idem_restore_timeout` を shutdown へ登録（保険）し、各分岐でも ROLLBACK 直後に明示的に呼ぶ。復元関数は 1 回だけ実行され、残った TX があれば取り消してから戻す。テストに 5 経路を追加（5 章、9 章） |
