# 小智板固件烧录包 — 2026-10-02

> **状态**：✅ 已全量重编译（clean + build 全绿），三件套版本严格一致
> **来源**：`E:\音乐\拨云_开源参考\liguanxi_Automatic_guitar\esp32_platformio`
> **构建**：PlatformIO 6.2.0 / espressif32 7.1.3 / Arduino 4.20017 / ESP32-S3-N16R8

---

## 一、三件套与烧录地址

| 文件 | 大小 (B) | 烧录地址 | 说明 |
|---|---|---|---|
| `bootloader.bin` | 14,032 | `0x0` | 引导程序 |
| `partitions.bin` | 3,072 | `0x8000` | 分区表（OTA 双分区） |
| `firmware.bin` | 442,832 | `0x10000` | 主固件（含 her 模式守护层 + 两种模式） |

**资源占用**：RAM 47.9% (156,932/327,680) · Flash 22.5% (442,473/1,966,080)

**校验和**（`SHA256SUMS.txt`）：
```
1033730a6df733f53a7a347353c1c5450547f76e98e0746da633079310a563b9  bootloader.bin
a4dc23d368667246ba6a7ac1cbce976d320a1764b881652d1b2eec47e090ae40  firmware.bin
1ae446228d79cf83a4b83de41c33d1e01dc21a3557149d7da90fbcbfc303311d  partitions.bin
```

---

## 二、⚠️ 烧录前必做：备份原厂固件

**顺序绝对不能颠倒。** 先把小智板原厂固件完整 dump 出来，再刷我们的。

```bash
# 1. 先找到串口（Windows：设备管理器 → 端口，形如 COM5）
# 2. dump 全部 16MB Flash（原厂是 16MB，必须整片读）
esptool.py --port COM5 --baud 921600 read_flash 0x0 0x1000000 original_16MB.bin

# 3. 校验读回来的完整性（与读出的 md5 对比）
esptool.py --port COM5 verify_flash 0x0 original_16MB.bin
```

**把 `original_16MB.bin` 存到 `E:\音乐\小智原厂固件备份\`**（当前是空目录）。

---

## 三、烧录

```bash
# 一次性烧三件套（地址别写错）
esptool.py --chip esp32s3 --port COM5 --baud 921600 write_flash \
  0x0     bootloader.bin \
  0x8000  partitions.bin \
  0x10000 firmware.bin
```

**或直接用 PlatformIO**（在 `esp32_platformio/` 目录）：
```bash
pio run -t upload --upload-port COM5
```

---

## 四、烧完怎么验证成功

```bash
# 打开串口监视器（固件里 monitor_speed = 115200）
pio device monitor --port COM5 --baud 115200
```

**期望看到**：
1. 板子重启 → 启动日志
2. `[BOARD]` 小智板 profile 加载（board_id = 3）
3. 三路 profile 校验通过信息
4. **UART1 就绪**（业务帧口，921600 bps — 注意这跟监视器的 115200 是两个不同的口）

**看到启动日志 = 烧录成功。** 这时候再进行「泰山派 ↔ 小智板」的链路打通。

---

## 五、已知注意事项

| 项 | 说明 |
|---|---|
| **Flash 是 16MB** | 实拍确认小智板模组 = ESP32-S3-N16R8，不是 4MB。分区表按 16MB 配的 |
| **有两个串口别搞混** | 调试串口 115200（`pio device monitor` 用）≠ 业务帧 UART 921600（泰山派对接用） |
| **build_dir 在 C 盘** | `platformio.ini` 特意设 `build_dir = C:/pio_build/boyun_esp32`，因为中文路径下 MinGW 的 ld.exe 写 firmware.map 会失败。**别改成项目内路径** |
| **三条 profile 都过了** | V51 / V60 / XIAOZHI 的 16 路 PCA 通道校验 + 签名窗口校验全 ok |
| **V51 有个 WARN** | `AIN3=GPIO18 在 ADC2` — 只影响旧版 V5.1 板子，小智板不受影响 |

---

## 六、回滚

刷挂了就用备份的原厂固件：
```bash
esptool.py --chip esp32s3 --port COM5 --baud 921600 write_flash 0x0 original_16MB.bin
```

旧版本（10-01 16:30 那次编译）也留了档：`E:\音乐\档3实现\firmware_backup_20261002_0953\`
