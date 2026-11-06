# tlu-pi-emulator

Raspberry Pi 5 で momiji-tlu（トリガーボード / ECP5 Versa）を試験するための「偽テレスコープ」です。

- **pulser**：偽 PMT。決まった数のパルスを決まったレートで出します。
- **fake_altai**：偽 ALTAI（EUDET 風 DUT）。trg_o を受けるたびに busy を一定時間上げます。
- **loopback**：Pi だけで動く自己テスト。TLU なしで、取りこぼしと反応時間を確認できます。

追加のパッケージは要りません（Python 3 標準ライブラリのみ）。GPIO は Linux の GPIO キャラクタデバイス（uAPI v2）を直接使います。

> **なぜ lgpio を使わないか**：lgpio のコールバックは、時刻順に並べるためにエッジ通知を 500 µs 遅らせてから呼ばれます（`lgPthAlerts.c` の `emit(count, nowGT-500000)`）。TLU の busy_timeout は最大 0xFFFF clk = 410 µs なので、lgpio だと busy が必ず間に合いません。カーネルのエッジイベントを直接読めば、割り込み駆動で取りこぼしがなく、反応は数十 µs 程度です。

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

## 出力の読み方（fake_altai）

| 行 | 意味 |
|---|---|
| `triggers seen` / `N_PI` | 受けた trg_o の数 |
| `edge -> busy` | trg_o の立ち上がり（カーネルの時刻）から busy を上げるまでの時間 |
| `late (> 410 us)` | busy_timeout に間に合わなかった数。だいたい N_TIMEOUT になるはずです |
| `trg_o arrived while busy was still high` | busy 中に次の trg_o が来た。busy が X4-8 に届いていないか、極性（busy_invert）が違う可能性があります |

## テスト

ハードウェアなしでも、構造体のサイズや ioctl 番号が `<linux/gpio.h>` と合っているかを確認できます。

```
PYTHONPATH=src python3 tests/test_layout.py
```
