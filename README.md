# MilluminSync

Millumin どうしの **Column の起動を同期**するための中継ソフトです。Chataigne で Column を同期していた使い方を、これ1本で置き換えられます。

現場での設定手順・トラブルシューティングは [取り扱い説明書.md](取り扱い説明書.md) を見てください。

Master の Millumin で Column を起動すると、Millumin は `/millumin/board/launchedColumn <番号> <名前>` を OSC で送ります。MilluminSync はそれを受け取り、`/millumin/action/launchColumn` に変換して Slave の Millumin に送ります。

```
PC1 (Master) 192.168.0.100                 PC2 (Slave) 192.168.0.200
┌──────────────┐  /millumin/board/      ┌──────────────┐  /millumin/action/    ┌──────────────┐
│  Millumin    │  launchedColumn 3 "A"  │ MilluminSync │  launchColumn 3       │  Millumin    │
│  OSC Sender  │ ─────────────────────▶ │  受信 :12000   │ ────────────────────▶ │ OSC Listener │
│              │  → 192.168.0.200:12000 │  (裏で常駐)   │  → 127.0.0.1:12001    │  :12001      │
└──────────────┘                        └──────────────┘                       └──────────────┘
```

## 同期するもの・しないもの

| | 動き |
|---|---|
| Column の起動 | Master と同じ Column を Slave でも起動します |
| Column の停止 | 「Column の停止も同期する」をオンにした場合だけ同期します |
| 動画の再生位置・残り時間 | **同期しません**。Master で再生位置を動かしても、Slave は変わりません |

Millumin は再生中、動画の時間情報を毎フレーム OSC で送り続けます。MilluminSync はこれを Slave に転送せず、ログにも1通ずつは出しません。ログの下に「転送しない受信 ○○ 通/秒」として件数だけを表示します。

## セットアップ (上の図の構成)

1. **PC2 (Slave)** で MilluminSync を起動します。
   - 受信ポート: `12000`
   - Slave の Millumin: `127.0.0.1:12001` (同じPCの Millumin の受信ポート)
   - 「アプリを開いたら自動で開始する」は初期状態でオンです。
2. **PC2 の Millumin**: OSC の **Listener (入力)** を有効にし、ポートを `12001` にします。
3. **PC1 (Master) の Millumin**: OSC の **Sender (出力)** を有効にします。送信先には、MilluminSync の画面に大きく出ている「Master の Millumin の OSC 送信先に入力」の IP とポートを入れます。
4. PC1 で Column を起動します。MilluminSync の「最後の同期」に Column 番号が出れば、PC2 の Millumin も同じ Column を起動しています。

> 受信ポートと Millumin の受信ポートは、同じPC上では別の番号にしてください。同じ番号だとどちらかがポートを開けません。

- **同期方法**
  - 「番号で合わせる」: Master と同じ番号の Column を起動します。両方の Column の並び順を揃えてください。
  - 「名前で合わせる」: 同じ名前の Column を起動します。並び順が違っても使えます。
  - Millumin は存在しない番号や名前を指定されると、再生中の Column を止めます。
- **テスト**: 「Slave だけでテスト」に Column 番号か名前を入れて「起動」を押すと、Master がなくても Slave に直接送れます。
- **送信先を複数にする**: Slave が複数台あるときは、送信先をカンマ区切りで書けます。例: `127.0.0.1:12001, 192.168.0.201:12001`
- **コントローラーから送る**: `/millumin/action/...` を MilluminSync に送ると、そのまま Slave に転送されます。

設定は自動で保存されます。

- macOS: `~/Library/Application Support/MilluminSync/config.json`
- Windows: `%APPDATA%\MilluminSync\config.json`

## 本番中 (裏で常駐) について

- 本番中は Millumin が全画面になり、MilluminSync は裏で動き続けます。画面を見なくても中継は止まりません。
- 中継処理は画面の表示とは別の流れで動いています。ウィンドウが隠れていたり描画が遅れたりしても、ログ表示を省略するだけで、OSC の受信と送信は止まりません。
- Mac 版では App Nap (裏にあるアプリを macOS が省電力のために間引く機能) を無効にしてあります。
- 負荷テストの結果、毎秒 3,000 通（8レイヤー × 60fps のおよそ6倍）の時間情報を受けても、取りこぼしはありませんでした。

## Mac での起動

GitHub Actions がビルドした `MilluminSync-macOS-arm64.zip` を展開して、`MilluminSync.app` を起動します。Apple Silicon 用です。

このアプリは Apple の開発者署名をしていないので、初回は macOS にブロックされます。次のどちらかで開けます。

- `MilluminSync.app` を右クリックして「開く」を選ぶ (macOS 15 以降は「システム設定 → プライバシーとセキュリティ」の「このまま開く」)
- ターミナルで隔離属性を外す:

```bash
xattr -dr com.apple.quarantine /Applications/MilluminSync.app
```

起動後に「ローカルネットワーク上のデバイスの検出を許可しますか？」と聞かれたら、**許可**してください。許可しないと、別のPCからの OSC を受信できません。あとから変更する場合は、システム設定 → プライバシーとセキュリティ → ローカルネットワーク で設定します。

## GitHub Actions での動作確認

`.github/workflows/build.yml` は、push のたびに次のことを行います。

1. **macOS と Windows** でテストを実行します。実際の UDP ソケットで「偽 Master → MilluminSync → 偽 Slave」と流して、次のことを確認します。
   - Column 起動の変換
   - 時間情報を転送しないこと
   - 順序、複数送信先、bundle、不正パケット
2. **macOS 上で実際に GUI を起動**して、自己テスト (`--selftest`) を実行します。時間情報と Column 起動を流し、Slave には Column 起動だけが届き、画面にも表示されることを確かめます。
3. `MilluminSync.app` をビルドし、**ビルドしたアプリ本体でも `--selftest`** を実行してから zip にします。
4. Actions の実行ページの **Artifacts** から `MilluminSync-macOS` (と `MilluminSync-Windows`) をダウンロードできます。`v0.2.0` のようなタグを push すると、GitHub Release にも添付されます。

本物の Millumin との接続は CI では確認できません。最後は実機で確認してください。

## 開発

Python 3.10 以上と、[CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) が必要です。

```bash
python -m pip install -r requirements.txt
```

```bash
python run_milluminsync.py
```

```bash
python -m unittest discover -s tests -v
```

画面なしでも動かせます。

```bash
python run_milluminsync.py --headless --port 12000 --target 127.0.0.1:12001
```
