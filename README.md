# tlu-pi-emulator

Raspberry Pi 5 で momiji-tlu（トリガーボード / ECP5 Versa）を試験するための「偽テレスコープ」です。

- **pulser**：偽 PMT。決まった数のパルスを決まったレートで出します。
- **fake_altai**：偽 ALTAI（EUDET 風 DUT、ALPIDE DAQ ボードの動きに合わせています）。ラン外は busy を High のままにし、アーム中だけ下げます。trg_o を受けるたびにトリガー番号を振り、busy を一定時間上げます。busy 中に来たトリガーはキャンセルします。
- **loopback**：Pi だけで動く自己テスト。TLU なしで、取りこぼしと反応時間を確認できます。

追加のパッケージは要りません（Python 3 標準ライブラリのみ）。GPIO は Linux の GPIO キャラクタデバイス（uAPI v2）を直接使います。

## 配線（3.3 V 直結、GND 共通）

| Pi（BCM / ピン番号） | 向き | Versa X4 | 信号 |
|---|---|---|---|
| GPIO17 / 11 | → | X4-7 | trg_i[0]（偽 PMT） |
| GPIO27 / 13 | ← | X4-3 | trg_o（DUT へのトリガー） |
| GPIO22 / 15 | → | X4-8 | busy_i[0]（偽 ALTAI の busy） |
| GND / 9 | — | X4-2 | GND |

ピン番号は `src/emulator/pins.py` にまとめてあり、各コマンドの `--pin` などでも変えられます。

## 使い方

インストールせずに使う場合：

```
cd tlu-pi-emulator
export PYTHONPATH=$PWD/src
python3 -m emulator.loopback --help
```

または `python3 -m venv .venv && .venv/bin/pip install -e .` とすると、`tlu-pulser`、`tlu-fake-altai`、`tlu-loopback` というコマンドとして使えます。

`/dev/gpiochip*` は `gpio` グループで読めるので、通常のユーザー（Pi OS の初期ユーザー）で動きます。`--rt` を付けると SCHED_FIFO と CPU 固定を試します。反応時間のばらつきが減りますが、sudo が必要です。

### 0. Pi だけの自己テスト

GPIO17（ピン 11）と GPIO27（ピン 13）をジャンパでつないで、次を実行します。

```
python3 -m emulator.loopback --n 1000 --width-us 1
```

`sent 1000, seen 1000 -> OK` と出れば、エッジの取りこぼしはありません。`edge -> Python` の max が、偽 ALTAI の反応時間のおおよその目安になります（410 µs より十分小さいこと）。

### ステージ A：パルスだけ（busy なし）

PC（`momiji-tlu/python/`）で、DUT を使わない設定にして run を始めます。

```
python3 tlu_run.py --time 20 --dut-mask 0
```

run が始まったら、Pi でパルスを出します。

```
python3 -m emulator.pulser --n 1000 --rate 100
```

**合格条件**：Pi の `N_PULSES` ＝ イベント数 ＝ N_ACCEPT、ID が 0 から欠けずに連番（`tlu_run.py` が OK を出す）、N_DROP ＝ 0。

`fake_altai --count-only` を同時に動かしておくと、trg_o がトリガーごとに 1 回出ていることも確認できます。

### ステージ B：偽 ALTAI（busy ハンドシェイク）

Pi のターミナル 1 で偽 ALTAI を起動しておきます。

```
python3 -m emulator.fake_altai --busy-us 1000
```

PC で次を実行します（trg_o を 255 clk＝1.6 µs に広げて、ちゃんと見えるようにします）。

```
python3 tlu_run.py --time 20 --dut-mask 0x01 --trg-o-width 255
```

Pi のターミナル 2 でパルスを出します。

```
python3 -m emulator.pulser --n 1000 --rate 100
```

run が終わったらターミナル 1 で Ctrl-C を押すと、`N_PI <数>` が出ます。その数を `tlu_run.py --n-pi <数>` に渡すと、自動で照合されます（次の run から使えます）。

**合格条件（busy 1 ms、100 Hz）**：Pi の N_PI ＝ N_ACCEPT ＝ イベント数、N_TIMEOUT ＝ 0（`late` も 0）、N_DROP ＝ 0。

**レートを上げると**：`--rate 2000` のように busy の時間（1 ms）より速くすると、busy 中に来たパルスが N_DROP として数えられます。N_ACCEPT ＋ N_DROP ＝ `N_PULSES` になれば正しいです。

## fake_altai の動き（ALTAI テレスコープ仕様に合わせたもの）

| 状態 | busy | トリガー |
|---|---|---|
| 停止中（起動直後、`--arm-after` の待ち、ラン終了後） | High（強制 busy） | 無視（`ignored` として数えるだけ） |
| アーム中・待機 | Low | 受理して番号を振り、busy を `--busy-us` だけ High にする |
| アーム中・busy 中 | High | キャンセル（番号を使わない）。`--count-during-busy` にすると番号だけ進める（replica の動きが分かったとき用） |

- トリガー番号は、アームのたびに `--first-id`（既定 0）から振り直します。
- アームと停止の切り替え：`--arm-after <s>`（その秒数だけ busy を High にしてからアーム）、`--run-time <s>`（その秒数で停止して終了）、または `--manual`（Enter でアームと停止を切り替え、`q` ＋ Enter で終了）。
- プログラムを終了すると busy のピンは解放されます。Versa の入力はプルダウンなので、Low になります。

例：5 秒は busy を High のままにして（その間に PC で run を始める）、30 秒のランを 1 回行う。

```
python3 -m emulator.fake_altai --busy-us 1000 --arm-after 5 --run-time 30
```

## 出力の読み方（fake_altai）

ランの終わりごとに、次の行が出ます。

| 行 | 意味 |
|---|---|
| `accepted` / `N_PI` | 受理したトリガーの数（`tlu_run.py --n-pi` に渡す） |
| `trigger IDs` | 振った番号の範囲 |
| `during busy` | busy 中に来てキャンセルしたトリガーの数。TLU 相手なら通常 0 です。0 でなければ、busy が X4-8 に届いていないか、極性（busy_invert）が違う可能性があります |
| `edge -> busy` | trg_o の立ち上がり（カーネルの時刻）から busy を上げるまでの時間 |
| `late (> 410 us)` | busy_timeout に間に合わなかった数。だいたい N_TIMEOUT になるはずです |
| `edges while stopped` | 停止中に来て無視したエッジの数 |

## テスト

ハードウェアなしでも、構造体のサイズや ioctl 番号が `<linux/gpio.h>` と合っているかを確認できます。

```
PYTHONPATH=src python3 tests/test_layout.py
```
