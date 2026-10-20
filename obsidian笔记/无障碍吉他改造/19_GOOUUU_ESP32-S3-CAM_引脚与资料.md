---
路径：E:\obsidian\项目笔记\无障碍吉他改造\19_GOOUUU_ESP32-S3-CAM_引脚与资料.md
日期：2026-09-30（23:40 更新：引脚全部敲死）
状态：✅ 引脚已确认（网上权威资料 + 资料包源码 + 原理图裁图三方印证）
关联：[[18_今晚回家测量清单]] · [[17_小智板接线与烧录SOP]] · [[16_泰山派部署与代码改造计划]]
---

# 19 · GOOUUU ESP32-S3-CAM（小智 AI 改造目标板）引脚与资料

> **一句话**：Z 的目标板 = **GOOUUU ESP32-S3-CAM / 模组 ESP32-S3-N16R8**（16MB Flash + 8MB PSRAM，板载 OV2640 + FPC 屏座）。引脚与 **ESP32-S3-WROOM-1(CAM) / AI-Thinker ESP32-S3-CAM** 完全一致，摄像头按 **CAMERA_MODEL_ESP32S3_EYE** 定义。

## 一、🚨 最要命结论：固件引脚必须大改

小智官方面包板方案（本资料包同款）把外设放在：
- **麦克风 INMP441（I2S）→ GPIO 1 / 2 / 42**
- **功放 MAX98357A（I2S）→ GPIO 39 / 40 / 41**
- **SPI 屏 ST7789 → GPIO 21 / 47 / 45 / 40 / 41 / 42**

而《拨云》固件 `XIAOZHI_PROFILE` 的占位是：**I2C=1/2、pwm=40/39、uart=41/42**。
→ **三组全撞车！** 我们若沿用这板（不吃小智外设），必须把 I2C/pwm/uart 全部重排；若想同时保留屏/麦/功放，更是要重排到底。**这就是为什么今晚要拿引脚图。**

## 二、完整引脚表（GOOUUU ESP32-S3-CAM，40 针，绕板一周）

来源：profharris/GOOUUU-Tech-ESP32-S3-CAM（GitHub，同 WROOM-1 CAM 引脚）+ CSDN 引脚详解（AI-Thinker 兼容）+ 资料包源码 `bread-compact-wifi(-lcd)/config.h` + 板原理图裁图，四方印证。

| 针 | GPIO | 别名 / 功能 | ADC | 本项目可用性 |
|---|---|---|---|---|
| 1 | 3V3 | 电源输出(800mA) | — | 给 PCA9685 逻辑供电 |
| 2 | EN | 复位 | — | — |
| 3 | **IO4** | **CAM_SIOD**（摄像头 I2C-SDA） | ADC1_CH3 | ❌ 摄像头占用 |
| 4 | **IO5** | **CAM_SIOC**（摄像头 I2C-SCL） | ADC1_CH4 | ❌ 摄像头占用 |
| 5 | **IO6** | **CAM_VSYNC** | ADC1_CH5 | ❌ 摄像头占用 |
| 6 | **IO7** | **CAM_HREF** | ADC1_CH6 | ❌ 摄像头占用 |
| 7 | **IO15** | **CAM_XCLK** | ADC2_CH4 | ❌ 摄像头占用 |
| 8 | **IO16** | CAM_Y9/D7 | ADC2_CH5 | ❌ 摄像头占用 |
| 9 | **IO17** | CAM_Y8/D6 | ADC2_CH6 | ❌ 摄像头占用 |
| 10 | **IO18** | CAM_Y7/D5 | ADC2_CH7 | ❌ 摄像头占用 |
| 11 | **IO8** | CAM_Y4/D2 | ADC1_CH7 | ❌ 摄像头占用 |
| 12 | IO3 | JTAG_EN / strapping | ADC1_CH2 | ⚠️ strapping，避免 |
| 13 | IO46 | CAM_RESET / 日志 | — | ⚠️ strapping，避免 |
| 14 | **IO9** | CAM_Y3/D1 | ADC1_CH8 | ❌ 摄像头占用 |
| 15 | **IO10** | CAM_Y5/D3 | ADC1_CH9 | ❌ 摄像头占用 |
| 16 | **IO11** | CAM_Y2/D0 | ADC2_CH0 | ❌ 摄像头占用 |
| 17 | **IO12** | CAM_Y6/D4 | ADC2_CH1 | ❌ 摄像头占用 |
| 18 | **IO13** | CAM_PCLK | ADC2_CH2 | ❌ 摄像头占用 |
| 19 | IO14 | 通用 GPIO | ADC2_CH3 | ✅ 可作 ADC（ADC2 局限见下） |
| 20 | 5V | 电源输入 | — | 外接 5V20A 电源汇入 |
| 21 | IO43 | U0TXD | — | ⚠️ 串口0（日志），慎用 |
| 22 | IO44 | U0RXD | — | ⚠️ 串口0（日志），慎用 |
| 23 | **IO1** | ADC1_CH0 | ADC1_CH0 | ✅ **最佳 ADC 候选**（小智用于麦 WS） |
| 24 | **IO2** | ADC1_CH1 / LED_ON | ADC1_CH1 | ✅ **最佳 ADC 候选**（小智用于麦 SCK） |
| 25 | IO42 | MTMS | — | ⚠️ JTAG 复用（小智用于麦 SD） |
| 26 | IO41 | MTDI | — | ⚠️ JTAG（小智用于功放 LRC） |
| 27 | IO40 | SD_DATA / MTDO | — | ⚠️ JTAG / SD卡（小智用于功放 BCLK） |
| 28 | IO39 | SD_CLK / MTCK | — | ⚠️ JTAG / SD卡（小智用于功放 DIN） |
| 29 | IO38 | SD_CMD | — | ⚠️ SD 卡 |
| 30-32 | IO37/36/35 | **PSRAM 专用** | — | ❌ 8MB PSRAM 占用，别碰 |
| 33 | IO0 | BOOT / strapping | — | ⚠️ strapping，避免 |
| 34 | IO45 | VSPI / strapping | — | ⚠️ strapping，避免 |
| 35 | IO48 | WS2812 RGB LED | — | ✅ 可作 status_led |
| 36 | IO47 | 通用 GPIO | — | ✅ 空闲候选 |
| 37 | IO21 | 通用 GPIO | — | ✅ 空闲候选 |
| 38 | IO20 | USB_D- / ADC2_CH9 | — | ⚠️ 原生 USB（OTG 口占） |
| 39 | IO19 | USB_D+ / ADC2_CH8 | — | ⚠️ 原生 USB（OTG 口占） |
| 40 | GND | 地 | — | — |

### 摄像头专用（板载 OV2640，硬连 FPC，不可另作他用）
SIOD=4, SIOC=5, VSYNC=6, HREF=7, XCLK=15, PCLK=13, Y2/D0=11, Y3/D1=9, Y4/D2=8, Y5/D3=10, Y6/D4=12, Y7/D5=18, Y8/D6=17, Y9/D7=16, RESET=46(PWDN 常接 GND)。
> 即 **GPIO 4,5,6,7,8,9,10,11,12,13,15,16,17,18,46** 全被摄像头吃。

## 三、ADC 约束（决定 ain 怎么排）

- **ADC1 可用**（含摄像头已占的）：IO1, IO2（自由）；IO4-IO10 属 ADC1 但**已被摄像头占**。
- **ADC2 不可用于电机/舵机场景**：ADC2 与 WiFi 冲突（ESP32 通病），且 IO19/20 是 USB。
- → 固件 `ain[0..4]`（V6.0 用的 4/5/6/9/8 全被摄像头占）**在 CAM 板上几乎无 ADC1 脚可用**。
  **对策**：本项目只用 1 路 ADC（舵机电流采样，用于《拨云》的压弦深度校准）。若舍摄像头 → IO4/5/6/9/8 全部释放，直接沿用占位；若保摄像头 → ain 只能挤 IO1 或 IO2 的 ADC1，且需重排 I2C。**建议：先舍摄像头**（我们不用视觉，小智 AI 视觉也非主线）。

## 四、固件侧新方案（建议，待 Z 拍板）

假设**舍摄像头**（复用它释放的脚），新 `XIAOZHI_PROFILE` 候选：

| 字段 | 原占位 | 新建议 | 理由 |
|---|---|---|---|
| `i2c_scl`/`sda` | 1 / 2 | **1 / 2** ✅ 保留 | 摄像头不用后 1/2 本就空闲，I2C 命门不变最省事 |
| `ain[0..4]` | 4,5,6,9,8 | **4,5,6,9,8** ✅ 保留 | 舍摄像头即释放，且全在 ADC1；只用 ain0 |
| `uart_rx`/`tx` | 41 / 42 | **41 / 42** ✅ 保留 | 不接小智功放即可用；与泰山派对接 |
| `pwm[0..1]` | 40 / 39 | **40 / 39** ✅ 保留 | 不接小智功放即可用 |
| `pca_oe` | 47 | **47** ✅ | 空闲 |
| `status_led` | 20 | **48**（WS2812）建议改 | 20 是 USB_D-，改 48 更稳 |
| `sd`/`btn` | 10-14 / 7,15,16,17 | 改 **PIN_NONE** | 钉子全被摄像头占，且我们不用 SD/按键 |

**核心洞察**：**只要放弃摄像头 + 不接小智的麦/功放/屏，占位脚 1/2/4/5/6/9/8/39/40/41/42/47 几乎全部可用**（这些脚本来就被摄像头占用，摄像头不用就空出来）。→ **profile 改动极小，甚至能原样用**（仅 status_led 20→48 建议改）。

## 五、✅ 已拍板（2026-09-30 23:52，Z 确认）

> **Z 拍板：「同意 摄像头不要、小智的屏/麦/功放都不接」**

据此 `XIAOZHI_PROFILE` **已定稿并落地**（`esp32_platformio/src/config/board_profiles.cpp`）：

| 字段 | 定稿值 | 说明 |
|---|---|---|
| `i2c_scl`/`sda` | **1 / 2** | I2C 命门，本板已引出且空闲，✅ 不动 |
| `ain[0..4]` | **4,5,6,9,8** | 摄像头舍去后释放，全在 ADC1，✅ 沿用 V6.0 |
| `uart_rx`/`tx` | **41 / 42** | 不接小智功放即空闲，✅ 沿用 |
| `pwm[0..1]` | **40 / 39** | 同上，✅ 沿用 |
| `pca_oe` | **47** | 空闲，✅ 沿用 |
| `status_led` | **48**（改，原 20） | 20=USB_D-（原生 USB 口占），改板载 WS2812 |
| `sd` / `btn` | **全 PIN_NONE** | 不用 SD/按键，且原脚被摄像头占 |

同步改动（已落地）：
- `platformio.ini`：`flash_size` 4MB→**16MB**（N16R8）；**取消注释 `-D FORCE_BOARD_ID=3`**（正式启用，无需再等）
- `tools/check_profiles.py`：`ADDR` 已含 `XIAOZHI`，构建期自动复核

**验证**：`check_profiles.py` → 3 profile 全过；`platformio run` 重编中（16MB + FORCE_BOARD_ID=3）。

**✅ 电流采样（2026-10-01 更新）**：原计划「不接控制板运放 → 电流法不可用、只能手动标定」已被
**Z 拍板「电流采样要加」推翻**——改用**外接 MAX471 模块**（¥16.3，±3A / AT 脚 1V/A）串在 5V 舵机
总线高边，输出接 AIN0=GPIO4。固件 `XIAOZHI_PROFILE.scale` 与泰山派 `power_monitor._BOARD_PROFILE[3]`
**均已改为 1.0 V/A**。《拨云》的**电流法自动标定压弦深度复活**。
限制：±3A 量程 < 总线正常峰值 3.12A ⇒ 标定段准、过流保护(阈值5A)不可信。详见 [[17_小智板接线与烧录SOP]] 第五节。

**⚠️ 仍需到货实测确认**（软/硬件边界）：
- 泰山派↔小智 `uart_rx/tx = 41/42` 是否与排针实际引出一致（对一下丝印即可）
- LEDC 直驱舵机 `pwm = 40/39` 是否与排针一致
- 以上两点属"看图即知"，无需万用表；到货照丝印核对即可。


## 六、附：资料包内容清单（Z 已下载）

`E:\BaiduNetdiskDownload\面包板DIY教程 ESP32-S3-CAM摄像头小智资料包\`
- `CAM开发板引脚图片.png` — 核心板原理图（本文件数据源）
- `CAM开发板接线图1.jpg` / `CAM开发接线图2.jpg` — 实拍面包板接线
- `ESP32-S3-CAM固件源码xiaozhi-esp32-main/` — 小智官方固件源码（板级 config.h 在此）
- `固件/ESP32-S3-CAM-V2-ST7789V2_0V203_OV3660.bin` — 原厂合并固件（**备份用**）
- `第1步开发板双击安装驱动/CH341SER.EXE` — CH341 串口驱动
- `flash_download_tool.zip` — 乐鑫烧录工具
- `配网说明文档.docx` / `小智AI烧录固件视频.mp4`
