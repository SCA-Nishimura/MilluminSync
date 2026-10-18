# MilluminSync

Millumin どうしを OSC で同期させるための小さな中継ソフトです。Millumin の同期だけなら Chataigne の代わりに使えます。

Master の Millumin で Column を起動すると、Millumin は `/millumin/board/launchedColumn <番号> <名前>` を OSC で送ります。MilluminSync はそれを受け取り、`/millumin/action/launchColumn <番号>` に変換して Slave の Millumin に送ります。

```
PC1 (Master) 192.168.0.100                 PC2 (Slave) 192.168.0.200
┌──────────────┐  /millumin/board/      ┌──────────────┐  /millumin/action/    ┌──────────────┐
│  Millumin    │  launchedColumn 3 "A"  │ MilluminSync │  launchColumn 3       │  Millumin    │
│  OSC Sender  │ ─────────────────────▶ │  受信 :8000   │ ────────────────────▶ │ OSC Listener │
│              │  → 192.168.0.200:8000  │  送信先 127.0.0.1:5000              │  :5000       │
└──────────────┘                        └──────────────┘                       └──────────────┘
```

## セットアップ (上の図の構成)

1. **PC2 (Slave)** で MilluminSync を起動します。
   - 受信ポート: `8000`
   - 送信先: `127.0.0.1:5000` (同じPCの Millumin の受信ポート)
   - 「開始」を押します。
2. **PC2 の Millumin**: Interactions → OSC で **Listener (入力) を有効**にし、ポートを `5000` にします。
3. **PC1 (Master) の Millumin**: OSC の **Sender (出力)** を有効にして、送信先を `192.168.0.200` ポート `8000` にします。
4. PC1 で Column を起動します。PC2 の MilluminSync のログに `IN /millumin/board/launchedColumn ...` と `OUT /millumin/action/launchColumn ...` が出れば、PC2 の Millumin も同じ Column を起動します。

> 受信ポートと Millumin の受信ポートは、同じPC上では別の番号にしてください。同じ番号だとどちらかがポートを開けません。

- 送信先はカンマ区切りで複数指定できます (例: `127.0.0.1:5000, 192.168.0.201:5000`)。Slave が複数あるときは、MilluminSync を1台だけ動かして全部に送ることもできます。
- 「テスト送信」ボタンは、Master がなくても送信先に `launchColumn <番号>` を直接送ります。PC2 の Millumin が反応するかをこれで確認できます。

## ルール

ルールは上から順に全部評価されます。受信したメッセージに一致したルールごとに、1通ずつ送信します。

| 初期ルール | 受信 | 送信 | 初期状態 |
|---|---|---|---|
| Column launch (by index) | `/millumin/board/launchedColumn` | `/millumin/action/launchColumn` 番号 (int) | 有効 |
| Column launch (by name) | `/millumin/board/launchedColumn` | `/millumin/action/launchColumn` 名前 (string) | 無効 |
| Column stop | `/millumin/board/stoppedColumn` | `/millumin/action/stopColumn` | 無効 |
| Pass through | `/millumin/action/**` | そのまま転送 | 有効 |

- Master と Slave で Column の並び順が違う場合は「by index」を無効にして、「by name」を有効にします。Column 名で一致させる方式です。
- Pass through ルールがあるので、コントローラーや別のソフトから `/millumin/action/...` を MilluminSync に送ると、そのまま Slave に届きます。
- Millumin は存在しない番号や名前の Column を指定されると、再生中の Column を止めます。by name を使うなら、両方の Millumin で Column 名を揃えてください。

### ルールの書き方

| 項目 | 説明 |
|---|---|
| 受信アドレス | `*` は `/` 以外の任意の文字列、`**` は何にでも一致します。例: `/millumin/layer:*/media/time` |
| 送信アドレス | 空欄なら受信と同じアドレスです。`{1}` には1つ目の `*` に一致した文字列が入ります。 |
| 引数 (JSON) | 空欄なら引数をそのまま転送します。`"$0"` は受信の1番目の引数です。`"$0:i"` / `":f"` / `":s"` を付けると int / float / string に変換します。`"$*"` は全引数です。数値や文字列を書くとその値を固定で送ります。`[]` なら引数なしで送ります。 |
| 間引き (ms) | 指定した間隔より短い間に来たメッセージを捨てます。メディア時間など、高頻度の値を転送するときに使います。 |

ログの受信行をダブルクリックすると、そのアドレスを入れた状態でルール作成画面が開きます。知らないアドレスもログで確認してから、そのままルールにできます。ルール不一致のメッセージは「ルール不一致を表示」をオンにするとログに出ます。

設定は自動で保存されます。

- macOS: `~/Library/Application Support/MilluminSync/config.json`
- Windows: `%APPDATA%\MilluminSync\config.json`

## Mac での起動

GitHub Actions がビルドした `MilluminSync-macOS-arm64.zip` を展開して、`MilluminSync.app` を起動します。Apple Silicon 用です。

このアプリは署名していないので、初回は macOS にブロックされます。次のどちらかで開けます。

- `MilluminSync.app` を右クリックして「開く」を選ぶ (macOS 15 以降は「システム設定 → プライバシーとセキュリティ」の「このまま開く」)
- ターミナルで隔離属性を外す:

```bash
xattr -dr com.apple.quarantine /Applications/MilluminSync.app
```

起動後に「ローカルネットワーク上のデバイスの検出を許可しますか？」と聞かれたら、**許可**してください。許可しないと、別のPCからの OSC を受信できません。あとから変更する場合は、システム設定 → プライバシーとセキュリティ → ローカルネットワーク で設定します。

## GitHub Actions での動作確認

`.github/workflows/build.yml` は、push のたびに次のことを行います。

1. **macOS と Windows** でユニットテストを実行します。実際の UDP ソケットで「偽 Master → MilluminSync → 偽 Slave」と流して、変換結果・順序・複数送信先・bundle・不正パケットを確認します。
2. **macOS 上で実際に GUI を起動**して、OSC を1通中継できるかを確かめる自己テスト (`--selftest`) を実行します。
3. PyInstaller で `MilluminSync.app` をビルドします。**ビルドしたアプリ本体でも `--selftest` を実行**してから zip にします。
4. Actions の実行ページの **Artifacts** から `MilluminSync-macOS` (と `MilluminSync-Windows`) をダウンロードできます。`v0.1.0` のようなタグを push すると、GitHub Release にも添付されます。

本物の Millumin との接続は CI では確認できません。最後は実機で確認してください。

## 開発

依存ライブラリは使っていません。Python 3.10 以上と、標準の tkinter だけで動きます。

```bash
python run_milluminsync.py
```

```bash
python -m unittest discover -s tests -v
```

```bash
python run_milluminsync.py --headless --port 8000 --target 127.0.0.1:5000
```
