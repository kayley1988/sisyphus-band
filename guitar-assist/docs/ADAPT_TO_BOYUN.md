# 与档3(《拨云》)的对接改造说明

> 目标：把 guitar-assist 从"孤立运行的感知项目"改造成**能驱动档3机械机构**的感知前端。
> 也就是补上原文档里自己承认的那个空缺——
> `src/ui/input.c` 注释写的 *"机构是 GPIO 通断 / 步进电机 / 舵机 / 串口指令板？"*，
> 答案是：**18 路舵机，走 `/dev/ttyS3` @921600，ESP32 中继的 V3 二进制帧协议。**

---

## 一、新增了什么

| 文件 | 职责 |
|---|---|
| `src/mech/frame_codec.{h,c}` | V3 二进制帧编解码：CRC16-CCITT(0x1021/0xFFFF) + `AA 55 LEN TYPE PAYLOAD CRC16(BE)` 帧 + 各帧工厂 |
| `src/mech/chord_angles.{h,c}` | 和弦指法 → 18 路角度：`zero=90`、`FRET_OFFSETS`、弦4-6 机械反转、`null_offset`/`press_offsets`、拨弦钳位 |
| `src/mech/mech_driver.{h,c}` | 串口驱动：打开 `/dev/ttyS3@921600`、**flock 串口排他**、状态追踪、`set_chord`/`strum`/`enable`/`home`/`mode` |
| `src/mech/mech_bridge.{h,c}` | 把上面这层挂到 `ui_app` 的三个回调（`on_chord_changed`/`on_confirm`/`on_brightness`） |
| `tools/mech_selftest.c` | 板端自检：不需要屏/触摸键，先验角度和帧，再可选真发一帧 |
| `scripts/verify_boyun_parity.py` | 与《拨云》**真实实现**逐字节对拍（算法+协议） |
| `scripts/check_c_consistency.py` | 从 C 源码抽常量表比对 + pycparser 语法解析 |

数据流：

```
[和弦识别 / 左手6键选择]
      │  ui_app.on_chord_changed(name, fingering[6])
      ▼
 ga_mech_set_chord()                 ← mech_bridge.c
      │  ga_mech_batch18()           ← chord_angles.c  指法→18路角度
      │  ga_make_servo_batch()       ← frame_codec.c   18角度→AA55帧
      ▼
 write(/dev/ttyS3) → ESP32 → PCA9685/PWM → 18 路舵机压弦
```

---

## 二、协议对齐（逐字节，有对拍保证）

```
帧格式:  SYNC0(0xAA) SYNC1(0x55) LENGTH(1B) TYPE(1B) PAYLOAD(0~247B) CRC16(2B 大端)
CRC:     CRC16-CCITT  poly=0x1021  init=0xFFFF  覆盖 TYPE+PAYLOAD
UART:    921600 8N1
```

| 帧 | TYPE | 载荷 |
|---|---|---|
| `NORMAL_SERVO_BATCH` | `0x02` | 18 字节角度，顺序 `[和弦0..11, 拨弦0..5]` |
| `NORMAL_SERVO_SINGLE` | `0x03` | `ch_type(0=PCA/1=PWM), ch_index, angle` |
| `NORMAL_SERVO_CTRL` | `0x08` | `0x01` 使能 / `0x02` 失能 / `0x03` 复位归位 |
| `NORMAL_CHORD_DEF` | `0x06` | `name[16] + frets[6]` |
| `NORMAL_CONFIG_SAVE` | `0x07` | 空（触发 ESP32 存 NVS） |
| `CTRL_MODE_SWITCH` | `0xF0` | `[mode, 0]`，mode: 0=NORMAL/1=PLAY/2=OTA |

**关键取舍：BATCH 帧只发 18 个角度，物理通道映射（`PWM1/PCA15/...`）是 ESP32 固件的事。**
主机不需要知道哪个舵机接在哪个插座上——这正是原协议的设计，我们照它来。

### 角度口径（三处必须一致）

```
按压零点 zero = 90                    # 硬件真值；不是"装机角 home_chord_angle"
base = zero + null_offset[ch]
angle = base + dir * press_offsets[弦][压深索引]
k = [0,2,4,6,8,10]                    # 每弦 ch0 索引，ch1 = k+1
FRET_OFFSETS / 弦4-6 机械反转 SWAP     # 见 chord_angles.c（与 servo_controller.py 逐项一致）
拨弦 clamp ∈ [55, 125]                # 超出会被 ESP32 静默拒绝，故主机先钳位
```

⚠️ **取整语义**：Python `round()` 是"四舍六入五取偶"，C `lround()` 是"五入"，
恰好 `.5` 时会差 1°。`chord_angles.c` 用 `ga_round_half_even()` 复刻 Python 语义——
这一处不改对，逐字节对拍必挂。

---

## 三、与档3主程序共存（串口排他）

档3主程序 `guitar.service` 也独占 `/dev/ttyS3`，两个进程同时写会出**交错帧**。
`ga_mech_open()` 用 **`flock(LOCK_EX|LOCK_NB)`** 对串口加排他锁：

- 档3的 `servo_panel.py`（已加固版）也用同一把文件锁 → **互斥真的生效**
- 串口被占用时，本程序**放弃驱动**并打印警告，绝不乱写、不静默
- 生产部署三选一：
  1. 档3主程序停掉时跑 guitar-assist（互斥模式，推荐先这样验证）
  2. 让档3主程序让出串口，由 guitar-assist 统一驱动
  3. guitar-assist 只做感知（识别/UI），把和弦通过档3的 HTTP API（`:5000`）下达

---

## 四、验证（本机没 C 编译器，所以做了三层）

**第 1 层 — 与《拨云》真实实现逐字节对拍**（`scripts/verify_boyun_parity.py`，**22/22 通过**）
直接 `import` 拨云的 `frame_codec.py` / `servo_controller.py` 当标准答案：

- CRC16：500 组随机 + 边界全等
- 11 种帧字节流全等（含 CRC）
- 12 路和弦角度：14 个常用和弦 + 2000 组随机指法 + 换一组非默认参数后仍全等
- 6 路拨弦：200 组随机序列，**含 flip_state 翻转**一致
- 端到端：和弦→18 路→完整帧→ESP32 端反解析，14/14 一致

**第 2 层 — C 源码静态核对**（`scripts/check_c_consistency.py`，**16/16 通过**）
从 `chord_angles.c` 里抽出 `FRET_OFF`/`SWAP456`/`K`/三组默认参数，与拨云基准**逐项比对**；
再用 pycparser 对 6 个编译单元做语法解析。

**第 3 层 — 板上自检**（`mech_selftest`，待板子接上后跑）
内置断言：`C 和弦 [0,1,0,2,3,0] → [90,98,132,88,90,88,52,85,90,130,95,85]`（拨云实测值），
外加帧头/CRC 断言。离线跑不需要硬件；带 `/dev/ttyS3` 参数跑会真发一帧。

> 诚实说明：本机（Windows）**没有 C 编译器**，C 代码未做完整编译链接。
> 上面第 1、2 层覆盖了"算法/协议/常量/语法"，语义级验证请在第 3 层（板上 gcc）完成。

---

## 五、上板步骤

> 带屏版的完整运行/排障说明见 **`docs/RUN-ON-BOARD.md`**（含接线表、点屏自检、参数、四种"不亮"的判读）。

```bash
# PC: 交叉编译（脚本已把 guitar_assist_ui / mech_selftest 加进产物）
export SYSROOT=/path/to/tspi-sdk/sysroot      # 或按脚本注释手动给 ALSA
./scripts/build-rk3566.sh

# 传到板子
scp build-rk3566/{guitar_assist_ui,mech_selftest} root@<IP>:/root/guitar/

# 板上 ① 先离线自检机构（不碰硬件）
cd /root/guitar && ./mech_selftest
#  期望: "全部通过 (0 项失败)"

# 板上 ② 点屏（这一步过了，说明 SPI/DC/RES/接线全对）
./guitar_assist_ui --test-display
#  期望: 红绿蓝白黑依次整屏

# 板上 ③ 跑完整界面
./guitar_assist_ui
#  期望: 屏出演奏页(顶部 6 个和弦槽位 + 大写和弦名 + 指法图)
#        左手 6 键直接选和弦并驱动机构; Set 键切调音页; 长按 Set 进设置

# 板上 ④ 真发一帧给机构（先确保档3主程序没在跑，否则会被 flock 挡下）
systemctl stop guitar
./mech_selftest /dev/ttyS3
#  期望: 机构把 C 和弦压到位
```

---

## 六、与档3"不符合"的地方（已知清单）

改造只动了"软件接口"，**硬件口径的差异仍在那里**，逐条列出以免误合体：

| # | guitar-assist 现状 | 档3 实际 | 处理建议 |
|---|---|---|---|
| 1 | 屏是 **1.54" 240×240**（ST7789） | ~~顶盖 v7.3 按 1.83" 开窗~~ **已定案（2026-10-12）**：实物屏就是 **1.54" 240×240**（用户照片 PCB 丝印 `IPS_1.54' LCD / ST7789 / SPI / 240*240`），**与本项目代码完全匹配，软件零改动**。顶盖改出 **v8**（窗 28.7×28.7 @ 47.5,29.5，见 `_gen_top_v8.py` / `泰山派顶盖_v8_1.54彩屏.STL`） | ✅ 已解决——软件不动，打印顶盖 v8 替换 v7.3 即可 |
| 2 | 10 个触摸键 GPIO **全是 -1** | ~~档3 面板用触摸条~~ **已定案（2026-10-12）**：档3 笔记 48/29 有**实机验收通过**的完整引脚表 —— 右手 4 键（机械，脚11/13/15/16）+ 左手 6 键（TTP223 触摸，脚7/12/18/22/26/32）。已全部写进 `src/ui_main.c` 顶部 `UI_DEF_*`，并用 `scripts/check_ui_main.py` 做"GPIO 名→全局号"反算核对 | ✅ 已解决 |
| 3 | 采集用 **USB 声卡 hw:1,0** | 档3 没有音频输入（它是演奏机，不需要听） | 需要装拾音器/麦克风；否则调音、和弦识别起不来 |
| 4 | 部署到 `/root/guitar/` + `guitar-assist.service` | 档3 在 `/root/guitarist_app/` + `guitar.service` | 两套并存，或用 flock 让串口互斥（已实现） |
| 5 | 与档3**抢 `/dev/ttyS3`** | 档3 独占该串口 | 已用 flock 兜；生产上二选一 |
| 6 | 屏按 240×240 布局（`UI_W/UI_H`） | 若换 1.83" 屏要改分辨率 | 布局是按比例的，改 `ui_draw.h` 两个宏即可 |

**结论**：软件桥已经打通（和弦→机构），屏幕规格也已定案（**1.54" 240×240**，实物丝印为准）——
**合体不再有硬前置**，打印顶盖 v8 替换 v7.3 即可开装。

---

## 七、还没做的（别当成已完成）

- **ESP32 未上报"机构到位"事件**，所以 UI 的"压弦中→就绪"仍是**乐观置位**：
  现在由 `ui_main.c` 主循环按 `--settle`（默认 400ms）定时推进。
  要精确的话应等固件加一个到位事件，届时替换主循环那一段即可，UI 侧不用改。
- **机构和弦槽位**已接 `chord_db`（`load_quick_from_db()`，不再是写死的 6 条），
  但**槽位顺序仍固定**为档3 实机的 C/G/Am/F/Dm/Em；自定义和弦组/换组还没做。
- OTA / 日志链路（`0xFB/0xFC/0xFD`）没接——本项目用不到。
- **触摸条去抖**：档3 笔记 29 记过一个真实现象——TTP223 手指"接近但未碰到"时
  会 0.3~0.5s 级跳变（半触发）。本项目的左手键用了 20ms 消抖 + 按下沿单次上报，
  比档3 原来的"轮询间隔当去抖"稳，但**实机仍需验证**是否需要加大到 40~50ms。

### 已补上的（2026-10-12 这一轮）

- ✅ **主程序接入 240×240 UI**：新增 `src/ui_main.c` → 可执行 `guitar_assist_ui`，
  把屏（`st7789`）+ 右手 4 键 + 左手 6 键 + 音频线程 + `ui_app` + `mech_bridge`
  串成一个进程，30fps 脏矩形刷屏。**这块屏第一次真正有了上板可跑的载体**。
  同时把 `display_drv.c`/`st7789.c` 接进构建——它们此前**没有被任何目标引用**，
  等价于从未编译过。
- ✅ **和弦槽位接 `chord_db`**：不再写死 6 条，从库按名字取指法 + 自动算指法图范围。
- ✅ **左手 6 键（一键直达和弦）**：`input.c` 新增 `ga_leftkey_*` 一套 API，
  与右手 4 键的"焦点导航"分开（两套交互模型 + **相反的极性**）。
- ✅ 顺带修掉两个**真机必现**的缺陷（见 `docs/RUN-ON-BOARD.md` §5）：
  按键极性双重反转；`mech_bridge` 与 `ui_app` 争着写机构状态导致 UI 永远卡"压弦中"。
