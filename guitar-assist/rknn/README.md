# RKNN 部署指南 (RK3566 NPU)

本文档说明 Stage4 的 **可选增强路径**：把和弦识别从纯 C 模板匹配升级为
NPU 上的 CNN 推理。

## 先说结论：什么时候才该做这件事

**先跑模板匹配版本上线，收集真实数据后再决定。**

理由：
- 模板匹配版本在合成测试上 **11/11 通过**，已经能识别全部开放把位和弦
- 开放把位和弦覆盖流行歌曲约 **80%** 的和弦需求
- RKNN 路径需要：PC 上训练 → 量化转换 → 精度验证 → 板子上调试，
  完整走一遍 **3~6 周**

**该上 RKNN 的信号**（同时满足 2 条以上）：
1. 真实录音测试中，易混淆和弦对（如 C/Cmaj7、Am/A）错误率 > 15%
2. 需要识别 barre chord（大横按和弦）或高把位和弦
3. 需要在有伴奏/人声的环境下识别（CNN 抗干扰能力强得多）
4. 需要识别和弦转位（C/G、C/E 这类低音变化的和弦）

---

## 一、模型设计

### 输入
`8 × 12` 的色度图矩阵（8 个时间帧 × 12 个半音），值域 [0,1]。

**为什么不是只给 12 维**：时间维度让模型能学到"和弦转换"模式，
过滤掉扫弦过程中的过渡音。

### 网络结构（建议）

```
输入 8×12×1
  │
  ├─ Conv2D(16, 3×3, stride=1, pad=1) + BN + ReLU
  ├─ Conv2D(16, 3×3, stride=1, pad=1) + BN + ReLU
  ├─ MaxPool 2×2                      → 4×6×16
  │
  ├─ Conv2D(32, 3×3, stride=1, pad=1) + BN + ReLU
  ├─ MaxPool 2×2                      → 2×3×32
  │
  ├─ Flatten                          → 192
  ├─ Dense(64) + ReLU + Dropout(0.3)
  ├─ Dense(36)                        ← 12 根音 × 3 性质(major/minor/7th)
  └─ Softmax
```

**参数量约 15K，模型 < 100KB**。在 RK3566 的 NPU 上单次推理
**< 3ms**，相对 93ms 的音频帧间隔完全不是瓶颈。

### 输出类别设计建议

不要用"所有和弦名"做分类（样本不均衡严重）。
推荐**两级输出**：
- 第一级：36 类（12 根音 × 3 种性质）
- 第二级：对易混淆的类别（如 major vs maj7）单独做二分类

---

## 二、数据准备

这是整个流程最耗时的部分（占总工作量 60%+）。

### 数据来源

| 方式 | 成本 | 质量 |
|---|---|---|
| **自己录制**（推荐） | 中 | 最贴合你的设备/拾音链路 |
| 公开数据集（GuitarSet、IDMT） | 低 | 好，但录音条件不同 |
| 合成 + 数据增强 | 低 | 作为补充，不能替代真实录音 |

### 录制要求

- **用目标设备录**：必须用 RK3566 + 你选的拾音器录，
  因为部署时的音色一致性直接影响精度
- **每个和弦至少 200 段**，30 秒一段，包含不同的：
  - 力度（轻扫/重扫/单弦拨）
  - 把位（开放/高把位）
  - 时长（长和弦/快速转换）
- **必须包含负样本**：单音、噪声、说话声、静音

### 特征提取

用本项目的 `ga_chroma_frame()` 离线生成 8×12 特征，导出为
`.npy` 或 `.bin`：

```bash
# 用已有的 tuner_demo file 模式批量处理
for f in recordings/*.wav; do
    ./build-pc/tuner_demo file "$f" > "${f%.wav}.pitch.txt"
done

# 或者写个专门的提取工具（见 tools/extract_features.c 的规划）
```

---

## 三、训练（PC 端，PyTorch）

见 `rknn/train_chord_cnn.py`。核心流程：

```bash
# 1. 准备数据: data/X.npy (N, 8, 12), data/y.npy (N,)
# 2. 训练
python3 rknn/train_chord_cnn.py --data data/ --epochs 80

# 3. 导出 ONNX
python3 rknn/export_onnx.py --ckpt best.pt --out chord_cnn.onnx
```

训练要点：
- 用 **标签平滑**（label_smoothing=0.1），因为和弦的边界本身模糊
- 用 **混响增强**（加随机卷积），模拟真实房间
- 数据增强：时间轴轻微拉伸、半音随机偏移（移调增强）

---

## 四、ONNX → RKNN 转换

**必须在 x86 Linux 上做**（rknn-toolkit2 不支持 ARM 运行）。

```bash
# 安装（需要 Python 3.8~3.10，注意版本限制）
pip install rknn-toolkit2==2.0.0

# 转换 + 量化
python3 rknn/convert_rknn.py \
    --onnx chord_cnn.onnx \
    --dataset data/quant/ \
    --out chord_cnn.rknn \
    --target rk3566
```

**关键：量化校准数据要真实**
- 准备 200~500 个真实录音样本做校准
- 用随机数做校准，量化后精度会崩
- 量化后必须做**逐层精度对比**（`convert_rknn.py` 里有实现）

---

## 五、板子上运行

### 依赖

```bash
# 板子上需要 librknnrt.so (RKNN 运行时)
# 泰山派 SDK 里通常已有, 检查:
ls /usr/lib/librknnrt.so
# 没有的话从 RK 官方仓库取匹配版本
```

### 编译

```bash
cd /path/to/guitar-assist
mkdir build-rk3566 && cd build-rk3566

cmake .. \
    -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
    -DGA_HAVE_RKNN=ON \
    -DRKNN_INCLUDE_DIR=/path/to/rknpu2/runtime/Linux/librknn_api/include \
    -DRKNN_LIB_DIR=/path/to/rknpu2/runtime/Linux/librknn_api/aarch64 \
    -DCMAKE_BUILD_TYPE=Release
make -j4
```

### 运行

```c
ga_chord_recog_t cr;
/* 传入模型路径即启用 NPU, 传 NULL 自动回退模板匹配 */
ga_chord_recog_init(&cr, GA_BACKEND_RKNN, "/root/models/chord_cnn.rknn");
```

**代码里已经做了降级保护**：模型加载失败会自动回退到模板匹配，
不会因为 NPU 出问题导致整个功能不可用。

---

## 六、性能预期

| 环节 | 耗时 | 说明 |
|---|---|---|
| ALSA 采集一帧 | 93 ms | 4096 样本 @ 44.1kHz，这是节奏 |
| 色度图提取 | ~1.5 ms | 4096 点自写 FFT |
| **RKNN 推理** | **~3 ms** | 15K 参数的小模型 |
| 模板匹配（对比） | ~0.2 ms | 72 次余弦相似度 |
| **合计** | **< 5 ms** | 相对 93ms 帧间隔，余量充足 |

**结论：延迟完全不是问题，NPU 也不是瓶颈。** 真正的瓶颈永远是
音频采集和特征质量。这也是我建议先跑模板匹配的原因 —— 快、稳、够用。

---

## 七、常见坑

1. **rknn-toolkit2 版本必须和板子上的 librknnrt 版本匹配**
   大版本不一致会加载失败，报错信息还很不友好。

2. **量化后精度可能掉 10~20%**
   如果掉太多，改用混合量化（`quantized_dtype='w8a16'`）
   或者对第一层和最后一层不量化。

3. **NPU 输入必须是 NHWC + uint8/int8**
   浮点输入会走 CPU 回退，慢 10 倍。转换时确认 dtype。

4. **色度图的归一化必须和训练时一致**
   训练用"和为 1"，推理也必须"和为 1"。这是最常见的精度不一致来源。

5. **别忘了模板匹配这个 fallback**
   NPU 被其他进程占用、模型文件损坏时，用户至少还能用。
