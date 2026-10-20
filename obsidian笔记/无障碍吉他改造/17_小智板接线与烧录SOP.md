# 17 · 小智板（ESP32-S3）接线与烧录 SOP

> 路径：E:\obsidian\项目笔记\无障碍吉他改造\17_小智板接线与烧录SOP.md
> 日期：2026-09-30｜状态：**引脚已定稿（Z 拍板舍摄像头+不接小智屏/麦/功放）**，profile 已改完待烧
> 板型：**GOOUUU ESP32-S3-CAM（模组 ESP32-S3-N16R8，16MB Flash + 8MB PSRAM）** — 引脚详见 [[19_GOOUUU_ESP32-S3-CAM_引脚与资料]]

## 〇、这篇解决什么

硬件到货后，把**你的 ESP32-S3 小智板**变成我们系统的「小脑」——刷入《拨云》改造固件、外接舵机驱动、与泰山派（大脑）串口对接。
固件源码：`E:/音乐/拨云_开源参考/liguanxi_Automatic_guitar/esp32_platformio/`，目标 profile = `XIAOZHI_PROFILE`(board_id=3)。

## 一、两套板子的关系（再确认）

| 角色 | 板子 | 跑什么 | 是不是你的 |
|---|---|---|---|
| 大脑（想） | 泰山派 RK3566 | `src/guitarist/main.py` | 另一块，已下单 |
| 小脑（动） | **ESP32-S3 小智板** | **本固件** | **就是你的** |

关系链：泰山派想 → 串口发二进制帧(921600) → 你的 ESP32-S3 收 → 驱动舵机 → 真琴响。

## 二、硬件清单（到货后）

- 小智 AI 云音盒（ESP32-S3，裸开发板+屏/喇叭/麦）
- PCA9685 蓝板 ×1（采购单已有；提供 16 路 PWM + I2C 地址 0x40）
- 5V20A 电源（舵机供电，采购单已有）
- **MAX471 电压电流传感器模块 ×1~2**（电流采样，见第五节；¥16.3/个）
- CH340 USB-UART（采购单已有，切 **3.3V** 跳线！）或直接用板载 USB-C
- 杜邦线、面包板、1kΩ 电阻 ×1~2
- 18 路舵机（12×MG996R + 6×MG90S，采购单已有）
- 泰山派（大脑，另到）

## 三、固件「死锁」的约束（来自代码，改不了，接线必须遵守）

| 约束 | 值 | 出处 |
|---|---|---|
| I2C 引脚 | **GPIO1(SCL) / GPIO2(SDA) 固定** | `board_pins.h` PIN_I2C_SCL=1/SDA=2，C9 自检出错即编译失败 |
| PCA9685 地址 | `0x40`（A0..A5 接地） | `board_pins.h` PIN_PCA_ADDR |
| ADC 必须在 ADC1 | 脚号只能是 GPIO1..10 | 不变量 C（电流采样用） |
| 避开 strapping 脚 | GPIO0 / 3 / 45 / 46 不能用 | 不变量 C |
| 18 路舵机 = 16(PCA) + 2(LEDC) | PCA9685 16 路 + ESP32 直驱 2 路 | `servo.ch` + `pwm_slot{0,1}` |
| 调试串口波特率 | **115200** | `main.cpp Serial.begin(115200)`；与泰山派的 921600 是**业务帧**波特率，别混 |

⚠️ **2026-09-30 引脚已定稿**（Z 拍板：摄像头不要、小智屏/麦/功放都不接）：
`uart_rx=41 / uart_tx=42 / pca_oe=47 / pwm={40,39} / ain={4,5,6,9,8} / status_led=48（原20改，避开USB_D-）/ sd,btn=PIN_NONE`。
这些脚在「不接摄像头 + 不接小智外设」前提下**全部空闲可用**（摄像头一舍，它占的 4-18 全释放）。`i2c=1/2` 是死锁命门，本板已引出且空闲，**不动**。

## 四、接法 A：MVP 简版（黑客松先用，推荐）

> 只用 PCA9685 蓝板 + 2 路 LEDC，**开环角度压弦，能出声**。电流法自动校准和 EEPROM 自动认板先不做（`FORCE_BOARD_ID=3` 顶上）。

### 4.1 小智 ↔ PCA9685 蓝板接线

| 小智 ESP32-S3 | PCA9685 蓝板 | 说明 |
|---|---|---|
| GPIO1 (SCL，固定) | SCL | I2C 时钟 |
| GPIO2 (SDA，固定) | SDA | I2C 数据 |
| 3.3V | VCC | 蓝板逻辑电源（4.7k 上拉已在板上，拉到 VCC） |
| GND | GND | 共地（与 5V PSU 共地） |
| — | V+ | 接 5V PSU **正极**（舵机电源，别从板子取） |
| — | GND(V+侧) | 接 5V PSU **负极**（共地） |

- 蓝板 **OE 脚**：多数蓝板 OE 有跳线帽默认接地（常使能）。若如此，把 profile 里 `pca_oe = PIN_NONE`，不用 ESP32 控 OE。若要控 OE，拆跳线帽、把小智 GPIO47 接 OE。
- 16 路舵机：插满 PCA9685 的 16 个输出（信号/电源/地，电源侧由 V+ 供 5V）。

### 4.2 2 路 LEDC 直驱舵机

- 固件按 `XIAOZHI_PROFILE.servo` 把 2 路分配到 ESP32 `pwm={40,39}`（暂定）。
- 这 2 路舵机：信号线接小智 GPIO40 / GPIO39，V+ 接 5V PSU，GND 共地。
- ⚠️ 具体「哪 2 路物理舵机走 LEDC」由固件 `servo_controller` 路由决定（chord_ch 里两个 `PIN_NONE` 位）。接线时以「PCA9685 插满 16 路 + 余下 2 路信号线接 GPIO40/39」为准；到货后我可帮你从固件里逐路标注。

### 4.3 小智 ↔ 泰山派 串口（业务帧 921600）

| 小智 (ESP32-S3) | 泰山派 RK3566 | 说明 |
|---|---|---|
| GPIO41 (暂定 UART_RX) | 泰山派 TX | 收帧 |
| GPIO42 (暂定 UART_TX) | 泰山派 RX | 发帧 |
| GND | GND | 共地（必须！） |

⚠️ 小智的 41/42 可能已被板载屏/摄像头占用 → 到货查原理图换空闲 GPIO，并同步改 `XIAOZHI_PROFILE.uart_rx/tx`。

### 4.4 电源（关键，别烧板）

- **舵机电源 = 5V20A PSU**，绝不从小智板 5V 取（18 路舵机峰值 10~18A，板子供不起且会反灌烧 MCU）。
- 小智板自身由 **USB-C** 或自带稳压供电；PCA9685 逻辑 VCC 接小智 3.3V。
- **所有 GND 必须连到一起**（小智 / PCA9685 / 5V PSU / 泰山派）。

## 五、电流采样接线（MAX471 模块 · 2026-10-01 Z 拍板「要加」）

目标：恢复《拨云》的「**电流法自动标定压弦深度**」——舵机把弦压到碰到品丝时压不动、电流抬升，
固件抓这个拐点当「压到位」。**不必再接《拨云》整块控制板**：一个 MAX471 模块就够。

### 5.1 器件
- **MAX471 电压电流传感器模块**（优维客 WK，¥16.3，Z 已看中）—— **±3A / AT 脚输出 1V/A / 高边自取电 / 零电流 0V**（与《拨云》换算公式 `V/(shunt×gain)` 天然兼容）。
- 建议 **2 个**：和弦组 1 个（→ AIN0 = GPIO4）+ 拨弦组 1 个（→ AIN1 = GPIO5）。
  **只做压弦标定买 1 个就够**（拨弦那路 AIN1 接 GND）。

### 5.2 接线（串在舵机电源**高边**）

| 从 | 到 | 说明 |
|---|---|---|
| 5V20A 电源 **正极** | 模块 **RS+ / VIN** | 电源先进模块 |
| 模块 **RS- / VOUT** | PCA9685 **V+** | 出来再喂舵机总线（= 全部舵机电流都流过模块） |
| 模块 **AT** | **1kΩ 串阻** → 小智 **GPIO4**（AIN0） | 电流信号；1kΩ 是限流保险，护 ADC 脚 |
| 模块 **GND** | 共地 | 与 PSU / 小智 / PCA9685 同地 |
| （拨弦组）模块 **AT** | → 小智 **GPIO5**（AIN1） | 只有 1 个模块时，AIN1 接 GND |

- ⚠️ **接「AT」不是「VT」**——AT = 电流输出（1V/A），VT = 电压输出（1/5 分压）。接错读不到电流。
- ⚠️ 模块**自取电于被测线路**（无需额外供电），所以它必须串在 5V 上才能工作。

### 5.3 标度（固件 + 上位机**都已改好**）

| 位置 | 值 | 说明 |
|---|---|---|
| `XIAOZHI_PROFILE.scale`：`chord_shunt_ohm × chord_gain` | **1.0 × 1.0 = 1.0 V/A** | 对上 MAX471 标度 |
| 同上：`strum_shunt_ohm × strum_gain` | **1.0 × 1.0** | 拨弦组同款 |
| `power_monitor.py` `_BOARD_PROFILE[3]` | 同值（新增 board 3 分支） | 泰山派侧换算同步，不再误用 V5.1 旧增益 |

**到货标定（一次）**：加载已知电流 I（舵机堵转即可）→ 读 AIN0 电压 V ⇒ 把乘积改成 `V/I`。

### 5.4 ⚠️ 三个必须知道的限制（诚实告知）

1. **量程 ±3A**：和弦总线正常演奏**实测峰值 3.12A**（`adc_monitor_task.cpp` 注释实测值）⇒ 3A 以上读数**饱和**。
   **标定段（单舵机、<1.5A）准；全程演奏时的读数不可信。**
2. **过流保护失效**：固件阈值 `chord=5.0A / strum=4.0A`，而模块 >3A 就测不到 ⇒ 这层软保护形同虚设。
   **兜底改用 5V20A 电源自身限流 + 串一个保险丝。**
3. **EEPROM 认板仍靠 `FORCE_BOARD_ID=3`**（模块与认板无关）。

> 若将来想要「常在线 + 量程够(≈7A)」：改用 **5mΩ/5W 采样电阻 + 高边电流检测运放**（复刻《拨云》原设计，连固件常量都不用改）。

### 5.5 到货开箱验证（3 分钟 · 先验模块再上车）

Z 已下单 **2 个**（2026-10-01）。**不要直接串进舵机总线**——先按这三步验一遍，避免"白测一晚"或反接烧模块：

| 步 | 操作 | 期望 | 若不符 |
|---|---|---|---|
| 1 | 断电，万用表**电阻**档测 `RS+ ↔ RS-` | **近短路**（几十 mΩ 级，内部就是采样电阻+芯片通路） | 开路/几百 kΩ → 模块坏或丝印错，换货 |
| 2 | 串已知负载：5V 电源 + **10Ω/5W 电阻**（= 0.5A），表笔测 **AT 对 GND** | ≈ **0.50V**（1V/A × 0.5A） | 0V → 接错脚（**错接成 VT**）或模块不工作 |
| 3 | 同一线路**反接** RS+/RS- | 读数约 0 / 负向 | 正常现象；**正式接必须按丝印**，反接会向 ADC 灌反压 |

- 第 2 步通过 = **1V/A 标度现场坐实**，固件 `shunt×gain=1.0` 直接可用；若读数偏差（如 0.48V），把固件乘积改成实测 `V/I` 即可（见 5.3）。
- ⚠️ **务必接 AT 不接 VT**：VT 是 1/5 分压的"被测电压"输出，接它读到的永远是电源电压、与电流无关。


## 六、烧录流程

### 0. ⚠️ 先备份原厂固件（重要，不可逆）
刷机会**覆盖小智 AI 原固件**（把消费 AI 音箱变成纯 ESP32-S3 跑我们的程序，这是预期用途）。若以后想还原小智 AI，先备份：
```bash
# 用系统 Python（已装 esptool）：先 detect 真实 flash 大小
python -m esptool --port COMx --baud 921600 flash_id
# 读取整片（8MB 示例；16MB 把 0x800000 换成 0x1000000）
python -m esptool --port COMx --baud 921600 read_flash 0x0 0x800000 backup_xiaozhi_original.bin
```

### 1. ✅ 核对并改 `XIAOZHI_PROFILE` 引脚（已完成，到货只需照丝印复核）
文件：`esp32_platformio/src/config/board_profiles.cpp`（约 L195 `XIAOZHI_PROFILE`）。
- **已定稿**（见第三节表），无需再改代码；到货只需拿排针丝印对一眼 41/42/40/39 是否与表一致（属"看图即知"，不用万用表）。
- 若你的板实际排针与 GOOUUU 标准不同，才需要改——告诉我丝印我再调。
- 改完保存，编译时 `check_profiles.py` 会自动复核 C1..C17 不变量（已跑：3 profile 全过）。

### 2. ✅ 开 `FORCE_BOARD_ID`（无 EEPROM 必须）
文件：`esp32_platformio/platformio.ini`（**已默认启用**）：
```ini
build_flags = ... -D FORCE_BOARD_ID=3
```
> 若走接法 B 且控制板带 EEPROM，可**不**开此项，让固件自动认板。

### 3. 编译 + 烧录
```bash
cd E:/音乐/拨云_开源参考/liguanxi_Automatic_guitar/esp32_platformio
# 编译（env 默认 esp32-s3-devkitm-1）
python -m platformio run
# 烧录（COMx 换成小智实际端口；板载 USB-C 或外接 CH340 3.3V）
python -m platformio run -t upload --upload-port COMx
```
- ✅ **flash 大小已定**：本板 = 16MB（N16R8 丝印），`platformio.ini` 已改 `board_build.flash_size = 16MB` / `board_upload.flash_size = 16MB`。
- 烧录口：插板上的 **TTL** 口（另一 C 口 OTG 是原生 USB，非烧录口）。
- 进下载模式：多数板自动（DTR/RTS）；若卡住，手动「按住 BOOT(GPIO0)→点 RST→松开 BOOT」。

### 4. 串口监视验证
```bash
python -m platformio device monitor -b 115200 --port COMx
```
应看到（大意）：
- `board_id=3 (XIAOZHI)` 被 publish
- `selfCheck pass` / 无 `FATAL ... profile INVALID`
- 无 `FORCE_BOARD_ID points to unknown` 之类报错

## 七、到货后「先核对清单」（填 profile 前必做）

- [ ] 查小智 AI 云音盒**引脚图/原理图**：确认 GPIO1/2 空闲可用作 I2C；找出空闲 GPIO 用于 UART/RWM/OE/AIN/LED
- [ ] 查**烧录方式**：板载 USB-C 是否直连 ESP32-S3（原生下载）还是需外接 CH340；确认 COM 口
- [ ] 查 flash 大小（`flash_id`），据此改 `platformio.ini` 的 flash_size
- [ ] 查板载是否占用 GPIO41/42（屏/摄像头），若占用换 UART 脚
- [ ] 确认 PCA9685 蓝板 OE 默认状态（跳线帽接地=常使能→`pca_oe=PIN_NONE`）

## 八、已知取舍与风险

| 项 | 说明 |
|---|---|
| 覆盖原厂固件 | 刷机后小智 AI 功能消失，板子变纯 ESP32-S3；想还原需第六步.0 备份 |
| MVP 无电流校准 | ~~裸 PCA9685 蓝板没有运放电流采样~~ → **2026-10-01 起用外接 MAX471 模块补上**（见第五节）：标定段可用；但 ±3A 量程 < 总线上限，过流保护不可信 |
| 无 EEPROM | 必须 `FORCE_BOARD_ID=3`（已留编译开关）；接法 B 带 EEPROM 后可去掉 |
| flash 大小 | 4MB 默认可能与小智板不符，烧录前必查 |
| I2C 死锁 1/2 | 小智必须腾出 GPIO1/2 给 I2C，冲突则需换板或飞线 |

## 九、验收判据（烧录后）

- [x] ✅ **监视口看到 `XIAOZHI` profile publish + selfCheck pass**（2026-10-01 实测：`[HW] XIAOZHI (OK) board_id=3 ... safe=0`）
- [ ] 无舵机空跑：泰山派 `main.py` 发 `send_config()`+`send_chord_defs()` → 小智收帧（用上位机 `frame_codec` 反解验证）
- [ ] PCA9685 上 16 路 + GPIO40/39 上 2 路舵机能被帧驱动（接假负载或轻推舵机臂观察）
- [ ] 泰山派触摸条按下 → 小智收到对应压弦帧 → 舵机动作（真琴响）

---

## 十、✅ 实测刷机记录（2026-10-01 完成）

| 项 | 结果 |
|---|---|
| 端口 | **COM3**（USB-SERIAL CH340，VID 1A86:7523） |
| 芯片 | ESP32-S3 (QFN56) rev v0.2，**8MB PSRAM**，**16MB Flash**（flash_id 实测） |
| 烧录方式 | `esptool write-flash 0x0 bootloader.bin 0x8000 partitions.bin 0x10000 firmware.bin`（无 boot_app0） |
| 结果 | `Hash of data verified`，启动 `XIAOZHI (OK)` / `safe=0` |
| 启动关键行 | `I2C bus ready SDA=2 SCL=1 @400kHz` / `UART1 921600 RX=41 TX=42` / `LEDC ch0=40 ch1=39` / `AIN=4/5/6/9/8` |

### ⚠️ 踩坑记录：SAFE MODE（C5 duplicate button pin）
- 现象：首次烧录后启动进 `SAFE MODE: PROFILE INVALID`，detail = `C5 duplicate button pin`。
- 根因：`board_profiles.cpp` 的 C5 重复脚校验，**`btn`/`sd` 组未排除 `PIN_NONE`**（`ain` 组排除了）。我们把 btn/sd 全置 PIN_NONE 即触发误报。
- 修复：C5 循环加 `!= PIN_NONE` 前置（与 ain 一致）。已落盘。
- 教训：**构建期校验（check_profiles.py）与运行期自检（board_selfCheck）行为必须一致** —— Python 侧本就排除了 PIN_NONE，C++ 侧漏了，才会"编译过、启动挂"。

### 烧录命令备忘
```bash
# 编译
pio run            # 或 ~/.workbuddy/binaries/python/envs/default/Scripts/pio run
# 烧录（推荐 esptool 直烧，避开 pio 的 SCons 收尾权限问题）
python -m esptool --chip esp32s3 --port COM3 --baud 921600 write-flash -z \
  0x0 bootloader.bin 0x8000 partitions.bin 0x10000 firmware.bin
# 读启动日志：串口 115200，用 RTS 拉低再放开触发复位后抓取
```
