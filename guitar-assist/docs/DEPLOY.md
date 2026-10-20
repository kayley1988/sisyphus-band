# 部署运维手册（泰山派 RK3566）

## 一、硬件准备

### 音频输入方案对比（重要）

调音精度的上限由**采集信噪比**决定，不是算法。

| 方案 | 接线 | 优点 | 缺点 | 推荐度 |
|---|---|---|---|---|
| **USB 声卡 + 接触式拾音器** | 拾音器夹琴头/音孔 → USB 声卡 → 板子 USB | 信噪比最高，抗环境噪声 | 需要额外硬件（约 ¥50） | ★★★★★ |
| USB 麦克风 | 直接插 USB | 免驱，简单 | 环境噪声和串音是问题 | ★★★ |
| 板载 I2S 麦克风 | 焊接到板子 I2S 接口 | 集成度高 | 底噪大，低频易受干扰 | ★★ |

**结论**：如果做产品，优先 USB 声卡 + 接触式拾音器。
吉他低频段（82~330Hz）对噪声敏感，接触式拾音器直接拾取琴体振动，
能拿到最好的信号。

### 振动马达（可选）

如果要做振动反馈，需要一个能通过 Linux 控制的振动马达：

- **最简单**：USB 振动棒（免驱，但控制接口不统一）
- **推荐**：GPIO 控制的振动马达，走 LED 子系统
  ```
  /sys/class/leds/vibrator/brightness   ← 写 1 开，写 0 关
  ```
- **PWM 方案**：可以调节强度，但代码要改 `haptic_pulse()` 写
  `period` / `duty_cycle`

不加振动也能用，只是少了嘈杂环境下的反馈通道。

---

## 二、板子端环境准备

```bash
# 1. 确认系统
uname -a          # 应该是 aarch64
cat /etc/os-release

# 2. 安装运行依赖（通常已有，缺了才装）
apt update
apt install alsa-utils     # 提供 arecord / aplay，调试用

# 3. 如果有 RKNN（可选）
ls /usr/lib/librknnrt.so
# 没有的话从 RK 官方仓库取对应版本
```

### 检查音频设备

```bash
arecord -l        # 列出采集设备
aplay -l          # 列出播放设备
```

典型输出：
```
card 0: rockchiphdmi [rockchip,hdmi], device 0: ...
card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]
                 ↑ 这就是你的 USB 声卡，设备名是 hw:1,0
```

**插拔 USB 声卡前后对比 `arecord -l` 输出的卡号变化**，就能确定设备号。

### 用我们的工具扫描（更可靠）

```bash
./dev_scan
```
它会列出所有设备，并且**逐个尝试打开**，报告实际生效的采样率。
比 `arecord -l` 更实用，因为有些设备列出来了但打不开。

---

## 三、部署

```bash
# PC 上交叉编译后
scp build-rk3566/guitar_assist \
    build-rk3566/tuner_demo \
    build-rk3566/dev_scan \
    root@<板子IP>:/root/guitar/

ssh root@<板子IP>
cd /root/guitar
chmod +x *

./dev_scan                          # 找设备号
./guitar_assist both hw:1,0         # 启动
```

### 开机自启（可选）

创建 `/etc/systemd/system/guitar-assist.service`：

```ini
[Unit]
Description=Accessible Guitar Assistant
After=sound.target
Wants=sound.target

[Service]
Type=simple
ExecStart=/root/guitar/guitar_assist both hw:1,0
Restart=on-failure
RestartSec=3
# 音频设备需要 realtime 权限
Nice=-10
IOSchedulingClass=realtime
IOSchedulingPriority=1
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty1

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now guitar-assist
systemctl status guitar-assist
journalctl -u guitar-assist -f      # 看日志
```

**注意**：`StandardInput=tty` + `TTYPath=/dev/tty1` 是为了让
键盘控制（q/s/r/1-6）能用。如果不需要交互，可以去掉这两行。

---

## 四、性能调优

### 降低音频延迟

当前配置的端到端延迟约 **30ms**（period=256 帧）。

想更低：
```bash
# 检查当前 ALSA 参数
cat /proc/asound/card1/pcm0c/sub0/hw_params

# 在代码里调小 period（alsa_capture.c 的第三个参数）
ga_capture_open("hw:1,0", 44100, 128);   # period 128 = 2.9ms
```

**但别调太小**：period 太小会导致频繁中断，A55 上可能出现 underrun。
256 是个稳妥值，实测没出现过 xrun。

### CPU 占用

实测（估算，未在真机测）：
- YIN 检测：< 1ms / 93ms 周期 → **约 1% 单核**
- 色度图 + 模板匹配：约 1.7ms / 93ms → **约 2% 单核**
- **总计 < 5% 单核占用**

余量极大，可以再加 GUI、网络等功能。

### 用 cgroup 限制（如果需要）

一般不需要。如果同时跑 GUI，可以：
```bash
systemctl set-property guitar-assist.service CPUQuota=50%
```

---

## 五、故障排查

### 打不开音频设备

```
[alsa] 打开 hw:1,0 失败: Device or resource busy
```

**原因**：设备被其他进程占用（如 PulseAudio、其他录音程序）。

```bash
fuser -v /dev/snd/*          # 看谁占着
systemctl stop pulseaudio    # 如果装了 PulseAudio
```

嵌入式系统上建议**不装 PulseAudio**，直接用 ALSA，
省一层缓冲、省一堆麻烦。

### 提示音爆音/破音

如果听到"咔咔"声：
1. 检查播放设备是否支持单声道 16bit（我们的合成器只生成这种格式）
2. 试试改 `ga_feedback_init()` 的第二个参数为具体设备名
   （如 `hw:0,0`），而不是 `default`

### 调音读数跳动

**原因排查顺序**：
1. **信号太弱** → 提高拾音器位置/增益，或换接触式拾音器
2. **环境噪声大** → 换接触式拾音器
3. **置信度门限太低** → `main.c` 里 `if (conf < 0.5) f = 0.0;` 调到 0.6
4. **低音弦跳动** → 这是 FFT/YIN 窗口的物理限制，
   可以把 `GA_ANALYSIS_SIZE` 调到 4096（延迟增加到 93ms）

**判断方法**：看输出的置信度。如果 conf 在 0.5~0.7 之间抖动，
说明信号质量不足，是采集问题不是算法问题。

### 和弦识别不准

**先确认特征提取是否正常**：
```bash
# PC 上跑，看色度图
./test_chroma
# 正确的输出应该是: 期望的和弦音在 top3，非和弦音能量 < 1/3
```

如果特征正常但识别错，是决策层问题，可以：
1. 调整 `chord_recog.c` 的判定阈值（默认 0.70）
2. 累积真实录音数据，考虑上 CNN
3. 检查是否有和弦外音干扰（扫弦时的闷音）

### 振动不工作

```bash
# 确认路径存在
ls /sys/class/leds/
# 手动测
echo 1 > /sys/class/leds/vibrator/brightness
sleep 1
echo 0 > /sys/class/leds/vibrator/brightness
```

如果路径不同，在 `main.c` 里改
`ga_feedback_init(&fb, NULL, "/your/path")`。

---

## 六、交叉编译注意事项

### ALSA 库的准备

交叉编译需要 aarch64 版本的 head 和库。三个办法：

**办法 A：用泰山派 SDK 的 sysroot（最推荐）**
```bash
export SYSROOT=/path/to/tspi-sdk/sysroot
./scripts/build-rk3566.sh
```

**办法 B：从板子拷库文件**
```bash
# 从板子拷过来
scp root@<IP>:/usr/lib/aarch64-linux-gnu/libasound.so.2 .
# 建符号链接（编译需要 .so 而不只是 .so.2）
ln -s libasound.so.2 libasound.so
# 头文件从 PC 的 /usr/include/alsa 拷（ALSA 头文件与架构无关）
cp -r /usr/include/alsa /path/to/alsa/include/
export ALSA_LIB=$PWD/libasound.so
export ALSA_INC=/path/to/alsa/include
./scripts/build-rk3566.sh
```

**办法 C：Buildroot/Yocto 交叉编译 ALSA**
最麻烦，不推荐。

### 工具链

```bash
sudo apt install gcc-aarch64-linux-gnu

# 或者用泰山派 SDK 自带的工具链
export CROSS_PREFIX=/path/to/sdk/prebuilts/gcc/linux-x86/aarch64/\
aarch64-none-linux-gnu/bin/aarch64-none-linux-gnu-
```

注意 SDK 的工具链前缀可能是 `aarch64-none-linux-gnu-` 而不是
`aarch64-linux-gnu-`，用 `CROSS_PREFIX` 环境变量指定。

---

## 七、实测清单（部署后逐项确认）

上板子后建议按这个顺序验证，每步都确认再往下：

| # | 检查项 | 命令 | 预期 |
|---|---|---|---|
| 1 | 二进制能跑 | `./guitar_assist --help` | 打印用法 |
| 2 | 和弦库正常 | `./guitar_assist chords` | 列出 28 个和弦 |
| 3 | 算法自测 | `./test_yin` | 19/19 通过 |
| 4 | 设备扫描 | `./dev_scan` | 找到采集设备 |
| 5 | 静音不误报 | `./guitar_assist tuner hw:1,0` 不说话 | 显示"等待拨弦" |
| 6 | 单弦调音 | 弹 6 弦 | 显示 E2，音分接近 0 |
| 7 | 六弦全覆盖 | 依次弹六根弦 | 都正确识别 |
| 8 | 听觉反馈 | 故意调偏 | 提示音变化 |
| 9 | 振动反馈 | 同 #8 | 振动变化 |
| 10 | 和弦识别 | 按 C 和弦 | 显示 C |
| 11 | 语音播报 | 按 s | 播报当前状态 |
| 12 | 长时间稳定 | 跑 30 分钟 | 无崩溃/内存增长 |

**第 5 步特别重要**：静音不误报是可用性的底线。
如果静音时读数乱跳，用户会完全失去信任。
