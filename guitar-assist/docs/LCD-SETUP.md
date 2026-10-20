# ST7789 屏幕点亮手册（泰山派 RK3566 + 1.54寸 240×240 IPS）

你手上的屏：**1.54 寸 IPS / ST7789 / SPI 接口 / 240×240 / 8 个引脚**。

这份手册按"从零到屏幕亮起"的顺序写，每一步都有验证方法。
**不要跳步**——嵌入式点屏最忌讳一次改一堆，出错了不知道是哪里的问题。

---

## 第 0 步：认清你的屏

从你的照片，引脚丝印是（从左到右）：

```
BLK  CS  DC  RES  SDA  SCL  VCC  GND
```

| 丝印 | 含义 | 接到泰山派 |
|---|---|---|
| **BLK** | 背光控制 | 接 3.3V（常亮）或空闲 GPIO（可控） |
| **CS** | 片选 | SPI3_CS0 |
| **DC** | 数据/命令选择 | **任意空闲 GPIO（必须接）** |
| **RES** | 复位 | 任意空闲 GPIO（强烈建议接） |
| **SDA** | SPI 数据（MOSI） | SPI3_MOSI |
| **SCL** | SPI 时钟 | SPI3_CLK |
| **VCC** | 电源 | **3.3V，不要接 5V** ⚠️ |
| **GND** | 地 | GND |

> ⚠️ **VCC 接 5V 会烧屏**。ST7789 是 3.3V 逻辑，模组上虽然有 LDO，
> 但很多廉价模组只有 3.3V 直连，接 5V 必坏。泰山派排针上有 3.3V 脚。

另外背面有 8 个电阻焊盘（J1/J2 之类的选焊位），那是配置 4 线 SPI 模式的。
**出厂已经是 4 线 SPI，不用动。**

---

## 第 1 步：确定接哪几个引脚

泰山派 40pin 排针上，SPI3 有**两套可选的引脚复用（M0 和 M1）**，
物理上是不同的引脚，选错了屏幕就是全黑。

### M0 组（RK3566 通用 40pin 标准）

| 屏幕引脚 | 泰山派 | GPIO |
|---|---|---|
| SDA | 物理脚 19 | GPIO4_B2 = 138 |
| SCL | 物理脚 23 | GPIO4_B3 = 139 |
| CS | 物理脚 24 | GPIO4_A6 = 134 |

### M1 组

| 屏幕引脚 | GPIO |
|---|---|
| SDA | GPIO3_B6 |
| SCL | GPIO3_B5 |
| CS | GPIO3_B7 |

### DC / RES / BLK

这三个是普通 GPIO，随便挑空闲的。建议挑**不相邻、有丝印标注**的，方便接线。

### ⚠️ 一定要做的事：查你的板子原理图

上面是 RK3566 的通用定义。**泰山派不同版本（V1.0 / V1.1 / V1.2）的引脚复用可能不同**，
丝印也可能不一样。

**验证方法**：

```bash
# 方法1: 看板子丝印，找 SPI3_CLK / SPI3_MOSI 的标注
# 方法2: 上电后查引脚当前功能
gpioinfo | head -60

# 方法3: 如果是官方系统，看已有设备树
ls /boot/*.dtb
dtc -I dtb -O dts /boot/你的.dtb | grep -A5 spi3
```

**实在不确定？** 用最笨但最有效的办法：**M0 和 M1 都试一遍**。
改一行设备树 → 重编译 → 重启 → 看是否亮。两次就能确定。

---

## 第 2 步：选驱动方案（三条路）

我查了社区资料，**Rockchip 官方内核里没有 ST7789 的 SPI panel 驱动**。
所以有三条路：

| 方案 | 工作量 | 刷新率 | 改动范围 | 推荐场景 |
|---|---|---|---|---|
| **A. fbtft 驱动** | 中（改配置+设备树） | 高 | 要重编内核 | 追求正规、长期用 |
| **B. 自写内核驱动** | 大（2~4周） | 最高 | 要重编内核 | 有特殊需求 |
| **C. 用户态驱动**（我已写好） | **小** | 中（够用） | **只编设备树** | 先跑起来、快速验证 |

### 我的建议：**先用方案 C 跑通，再考虑转 A**

理由很实际：

1. **方案 C 不用重编内核**，只编设备树（几十秒），改 GPIO 编号也是改一行
2. **方案 C 能立刻验证接线对不对**——如果 C 能亮，说明接线、SPI、DC/RES 全对，
   这时再转 A 只是"换个驱动实现"，没有硬件风险
3. **240×240 的小屏，用户态刷新率完全够**：
   40MHz SPI 下全屏 115KB 约 25ms ≈ 40fps，LVGL 跑 30fps 无压力
4. 如果先搞方案 A，万一点不亮，你分不清是**接线错**还是**驱动配置错**

---

## 第 3 步（方案 C）：用户态驱动跑通

### 3.1 设备树只启用 SPI3 + 加 spidev 节点

在板级设备树末尾（`tspi-rk3566-user-v10-linux.dts`）添加：

```dts
&spi3 {
    status = "okay";
    max-freq = <40000000>;
    pinctrl-names = "default";
    pinctrl-0 = <&spi3m0_pins &spi3m0_cs0>;   /* M0 组，M1 改这里 */

    spidev@0 {
        compatible = "rohm,dh2228fv";          /* Linux 通用 spidev */
        reg = <0>;
        spi-max-frequency = <40000000>;
        status = "okay";
    };
};
```

**注意 `rohm,dh2228fv` 这个字符串**——这是 Linux 内核里 spidev 驱动的
通用兼容字符串（历史遗留，很反直觉但确实这么用）。

### 3.2 编译设备树并部署

```bash
cd <sdk>/kernel/arch/arm64/boot/dts/rockchip/
cp tspi-rk3566-user-v10-linux.dts{,.bak}    # ★ 先备份！
vim tspi-rk3566-user-v10-linux.dts          # 粘贴上面的 &spi3 配置

cd <sdk>/kernel
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
     tspi-rk3566-user-v10-linux.dtb

# 传到板子（具体路径看你的系统，常见 /boot/ 或 /boot/dtb/）
scp arch/arm64/boot/dts/rockchip/tspi-rk3566-user-v10-linux.dtb \
    root@<板子IP>:/boot/
```

### 3.3 验证 spidev 出现了

```bash
# 重启后
ls /dev/spidev*
# 应该看到 /dev/spidev3.0

# 看驱动日志
dmesg | grep -i spi3
```

**如果 `/dev/spidev3.0` 没出现**，按顺序排查：
1. `pinctrl-0` 里的 `spi3m0_pins` 名字对不对（有些内核版本叫 `spi3m0_pins`，
   有的叫 `spi3m0_pins_hs`）
2. SPI3 的 `status` 是否真的是 `okay`
3. `dmesg | grep -i pin` 看引脚是否被其他驱动占用了

### 3.4 编译并跑我们的驱动

```bash
# PC 上交叉编译
./scripts/build-rk3566.sh

# 传到板子
scp build-rk3566/guitar_assist root@<IP>:/root/guitar/
```

运行（GPIO 编号按你的接线改）：

```bash
# 语法: guitar_assist <模式> <SPI设备> 
# 屏幕参数在代码里配置，见 src/ui/st7789.c 的 st7789_open 参数

# 先测试屏幕能否点亮
./guitar_assist --test-display /dev/spidev3.0
```

如果不亮，按这个顺序试（**每次只改一个**）：

| 现象 | 可能原因 | 怎么试 |
|---|---|---|
| 全黑，背光也不亮 | BLK 没接 / 接错 | BLK 直接接 3.3V 试试 |
| 背光亮但全白/全黑 | SPI 没通 / 初始化失败 | 换 M0/M1；降 SPI 时钟到 10MHz |
| 花屏/雪花 | SPI 时钟太快 | 降到 10MHz 试试 |
| 颜色红蓝互换 | RGB/BGR 顺序 | 改 `ST_MADCTL_DEFAULT`（见 st7789.c 注释） |
| 画面偏移/上移 | 显存 offset | 改 `st7789_open` 的 `x_off/y_off`（1.54寸试 `(0,80)`） |
| 黑白颠倒 | 反色设置 | `st7789.c` 里注释掉 `ST_INVON` 那行试试 |

---

## 第 4 步（方案 A）：转 fbtft 内核驱动

等方案 C 跑通了、确认硬件没问题，再转这个。

### 4.1 打开内核配置

```bash
cd <sdk>/kernel
make ARCH=arm64 menuconfig
```

启用这些（路径可能因内核版本略有差异）：

```
Device Drivers →
  Graphics support →
    Frame buffer Devices →
      [*] Support for small TFT LCD display modules      ← FB_TFT
          [*]   FB driver for the ST7789V LCD Controller ← FB_TFT_ST7789V
          [*]   Module to for adding FBTFT devices       ← FBTFT_DEVICE
```

或者直接改 `arch/arm64/configs/你的_defconfig`，加：

```
CONFIG_FB=y
CONFIG_FB_TFT=y
CONFIG_FB_TFT_ST7789V=y
CONFIG_FB_TFT_FBTFT_DEVICE=y
CONFIG_SPI=y
CONFIG_SPI_ROCKCHIP=y
```

### 4.2 改设备树（属性名换成 fbtft 的）

```dts
&spi3 {
    status = "okay";
    max-freq = <40000000>;
    pinctrl-names = "default";
    pinctrl-0 = <&spi3m0_pins &spi3m0_cs0>;

    panel@0 {
        compatible = "sitronix,st7789v";
        reg = <0>;
        spi-max-frequency = <40000000>;

        /* fbtft 用的属性名 */
        dc-gpios    = <&gpio3 5 GPIO_ACTIVE_HIGH>;   /* !!!按接线填!!! */
        reset-gpios = <&gpio3 4 GPIO_ACTIVE_LOW>;    /* !!!按接线填!!! */

        rotate   = <0>;
        fps      = <30>;
        buswidth = <8>;
        width    = <240>;
        height   = <240>;
        debug    = <0>;

        /* 可选: 覆盖默认 init 序列 */
        /* init = <0x1000001 0x10000FA ...>; */
    };
};
```

**关键差异**：fbtft 用 `rotate/fps/buswidth/width/height`，
不是 `rotation/bits-per-pixel/offset-x`。这些属性名搞错会静默失败（不报错但不工作）。

### 4.3 编译部署

```bash
cd <sdk>/kernel
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j$(nproc)
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
     tspi-rk3566-user-v10-linux.dtb
```

**⚠️ 这一步会重编译整个内核**，第一次可能 20~60 分钟。
内核镜像（`Image` 或 `boot.img`）和设备树（`.dtb`）都要部署。

### 4.4 验证

```bash
dmesg | grep -i -E "st7789|fbtft|fb1"
ls /dev/fb*
cat /proc/fb
# 期望看到: 1 st7789v 之类的行

# 刷屏测试（最直接的验证）
dd if=/dev/urandom of=/dev/fb1 bs=240 count=240 2>/dev/null
# 屏幕出现彩色噪点 = 成功！
```

---

## 第 5 步：LVGL 接上屏

无论走哪个方案，LVGL 只需要一个 `flush_cb`：

**方案 C（用户态）**：直接接我写的 `st7789_flush()`
**方案 A（fbtft）**：接 `display_drv.c` 里的 framebuffer 路径（自动探测 `/dev/fb*`）

代码里已经做好了自动切换——`ga_display_open()` 会先找 `/dev/fb*`，
找到就用 framebuffer（方案 A），找不到再用 SPI（方案 C）。

---

## 常见坑速查

| 坑 | 症状 | 解决 |
|---|---|---|
| VCC 接 5V | 屏烧了/不亮 | 接 3.3V |
| DC 引脚没接 | 全黑或全白 | DC 必须接，不能省 |
| M0/M1 选错 | 全黑 | 换另一个 |
| SPI 时钟过高 | 花屏/雪花 | 先降到 10MHz |
| 设备树改错位置 | 系统起不来 | 改前必须备份 dts |
| `&spi3` 放错文件 | 编译报"符号未定义" | 只能放板级 dts，不能放 include 的 dtsi |
| 花屏但能认到设备 | SPI 模式不对 | 试 mode 3（加 spi-cpol/spi-cpha） |
| 颜色怪异 | RGB/BGR 顺序 | 改 MADCTL（用户态）或加 `bgr` 属性（fbtft） |

---

## 附：为什么要开 DMA

社区实测数据（RK3566 + ST7789）：

| 配置 | 240×240 全屏刷新耗时 |
|---|---|
| 10MHz，无 DMA | ~55 ms（18 fps） |
| 40MHz，无 DMA | ~14 ms（70 fps） |
| 80MHz，开 DMA | ~12 ms（83 fps） |

**结论**：SPI 时钟从 10MHz 提到 40MHz，收益最大（4 倍）。
DMA 的收益在高速时更明显。

对 LVGL 来说，只要有 **30fps（33ms）** 就完全够流畅了。
所以 **40MHz + DMA** 是性价比最高的配置。

如果只能跑 10MHz（比如飞线质量差），也能用，只是指针动画会有点顿。
但调音的**听觉反馈不受刷新率影响**——你的核心功能是稳的。
