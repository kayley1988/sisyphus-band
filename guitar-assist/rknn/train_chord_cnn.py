#!/usr/bin/env python3
"""
train_chord_cnn.py - 和弦识别 CNN 训练脚本 (PC 端, PyTorch)

用途: 训练一个输入端为 8x12 色度图、输出 36 类(12根音 x 3性质)的
小 CNN, 之后导出 ONNX 再转 RKNN 跑在 RK3566 NPU 上。

为什么输出 36 类而不是直接用和弦名:
  和弦名有几百种(各种转位/加音), 样本极不均衡。
  拆成"根音 x 性质"两个维度后, 每个类别的样本量均衡得多,
  而且模型学到的"根音特征"和"性质特征"可以复用。

用法:
  # 从 wav 目录直接生成特征并训练(端到端)
  python3 train_chord_cnn.py --wav-dir recordings/ --epochs 80

  # 已有特征文件
  python3 train_chord_cnn.py --data data/ --epochs 80

依赖: torch numpy scipy
"""

import argparse
import os
import sys
import glob
import json
import numpy as np

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch.utils.data import Dataset, DataLoader
except ImportError:
    print("需要 PyTorch: pip install torch", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- 特征提取
#
# 这里用 numpy 重新实现一遍色度图, 而不是调用 C 代码。
# 原因: 训练时要在 PC 上快速批量处理成千上万个 wav,
# 用 Python 有更好的生态(scipy.io.wavfile 等)。
#
# !! 重要 !! 这份实现必须与 C 版 src/chord/chroma.c 保持算法一致,
# 否则训练和推理的特征分布不同, 精度会崩。
# 不一致的检查方法: 对同一个 wav, 两边算出的色度图差异应 < 1e-3。

FFT_N = 4096
SAMPLE_RATE = 44100
MIN_FREQ = 140.0    # 与 C 版一致: 低于此频率的 bin 丢弃
MAX_FREQ = 1200.0


def _build_bin_map():
    """预计算 fft bin -> pitch class 映射, 与 C 版逻辑一致"""
    n_bins = FFT_N // 2
    bin_to_pc = np.full(n_bins, -1, dtype=np.int32)
    bin_weight = np.zeros(n_bins, dtype=np.float64)

    for k in range(n_bins):
        f = k * SAMPLE_RATE / FFT_N
        if f < MIN_FREQ or f > 5000.0:
            continue
        midi = 69.0 + 12.0 * np.log2(f / 440.0)
        pc = int(round(midi)) % 12
        bin_to_pc[k] = pc

        w = 1.0
        if f > MAX_FREQ:
            w = 1.0 - (f - MAX_FREQ) / (5000.0 - MAX_FREQ) * 0.9
            w = max(w, 0.05)
        bin_weight[k] = w

    return bin_to_pc, bin_weight


BIN_TO_PC, BIN_WEIGHT = _build_bin_map()
WINDOW = np.hanning(FFT_N)


def chroma_frame(pcm_i16: np.ndarray) -> np.ndarray:
    """从一段 int16 PCM(长度 >= FFT_N) 算一帧 12 维色度图"""
    x = pcm_i16[:FFT_N].astype(np.float64) / 32768.0
    x = x * WINDOW
    spec = np.fft.rfft(x)
    mag = np.abs(spec[: FFT_N // 2])

    chroma = np.zeros(12, dtype=np.float64)
    valid = BIN_TO_PC >= 0
    np.add.at(chroma, BIN_TO_PC[valid],
              mag[valid] * BIN_WEIGHT[valid])

    s = chroma.sum()
    if s > 1e-9:
        chroma /= s
    return chroma


def extract_features_from_wav(path: str, n_frames: int = 8) -> np.ndarray:
    """从一个 wav 中提取 (n_frames, 12) 的特征矩阵。

    做法: 在音频中找能量最强的位置作为中心, 前后各取几帧。
    这样能自动跳过开头/结尾的静音。"""
    import scipy.io.wavfile as wavfile

    sr, data = wavfile.read(path)
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.int16)
    if data.dtype != np.int16:
        # 归一化到 int16
        mx = np.abs(data).max()
        if mx > 0:
            data = (data / mx * 32767).astype(np.int16)

    hop = FFT_N // 2
    n_win = (len(data) - FFT_N) // hop + 1
    if n_win < 1:
        return np.zeros((n_frames, 12))

    frames = np.zeros((n_win, 12), dtype=np.float64)
    energies = np.zeros(n_win)
    for i in range(n_win):
        seg = data[i * hop: i * hop + FFT_N]
        frames[i] = chroma_frame(seg)
        energies[i] = np.sqrt(np.mean((seg.astype(np.float64) / 32768.0) ** 2))

    # 取能量最强的一段连续 n_frames 帧
    if n_win <= n_frames:
        out = np.zeros((n_frames, 12))
        out[:n_win] = frames
        return out

    # 用滑动平均找最稳定的区域(能量高 + 变化小)
    win = np.ones(n_frames) / n_frames
    energy_smooth = np.convolve(energies, win, mode="valid")
    center = int(np.argmax(energy_smooth))
    return frames[center: center + n_frames]


# ---------------------------------------------------------------- 数据集

QUALITY_LIST = ["major", "minor", "dominant7"]   # 3 种性质
N_ROOT = 12
N_CLASS = N_ROOT * len(QUALITY_LIST)              # 36


class ChordDataset(Dataset):
    def __init__(self, X, y):
        # X: (N, 8, 12) float32, y: (N,) int64
        self.X = torch.from_numpy(X.astype(np.float32))
        self.y = torch.from_numpy(y.astype(np.int64))

    def __len__(self):
        return len(self.y)

    def __getitem__(self, i):
        return self.X[i].unsqueeze(0), self.y[i]   # (1, 8, 12)


# ---------------------------------------------------------------- 模型

class ChordCNN(nn.Module):
    """参数量约 15K 的小 CNN, 对 RK3566 NPU 友好。

    设计考量:
      - 第一层用 3x3 小卷积核: 在 8x12 这种小输入上, 大核会浪费参数
      - 不用 BatchNorm 的 running stats 之外的东西: 量化友好
      - 全局平均池化之前保留 Flatten: 保留位置信息(半音位置有意义)
    """

    def __init__(self, n_class=N_CLASS):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 16, 3, padding=1)
        self.bn1 = nn.BatchNorm2d(16)
        self.conv2 = nn.Conv2d(16, 16, 3, padding=1)
        self.bn2 = nn.BatchNorm2d(16)
        self.conv3 = nn.Conv2d(16, 32, 3, padding=1)
        self.bn3 = nn.BatchNorm2d(32)
        self.dropout = nn.Dropout(0.3)
        self.fc1 = nn.Linear(32 * 2 * 3, 64)
        self.fc2 = nn.Linear(64, n_class)

    def forward(self, x):
        x = F.relu(self.bn1(self.conv1(x)))
        x = F.relu(self.bn2(self.conv2(x)))
        x = F.max_pool2d(x, 2)                 # 8x12 -> 4x6
        x = F.relu(self.bn3(self.conv3(x)))
        x = F.max_pool2d(x, 2)                 # 4x6 -> 2x3
        x = x.flatten(1)
        x = self.dropout(F.relu(self.fc1(x)))
        return self.fc2(x)


# ---------------------------------------------------------------- 训练

def train(args):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[train] 设备: {device}")

    # ---- 载入数据 ----
    if args.data:
        X = np.load(os.path.join(args.data, "X.npy"))
        y = np.load(os.path.join(args.data, "y.npy"))
        print(f"[data] 载入 {len(y)} 个样本, 特征形状 {X.shape}")
    elif args.wav_dir:
        print(f"[data] 从 {args.wav_dir} 提取特征...")
        X, y = load_from_wav_dir(args.wav_dir)
        print(f"[data] 提取到 {len(y)} 个样本")
    else:
        print("必须提供 --data 或 --wav-dir", file=sys.stderr)
        return 1

    # ---- 划分训练/验证 ----
    n = len(y)
    idx = np.random.permutation(n)
    n_val = max(1, int(n * 0.2))
    val_idx, tr_idx = idx[:n_val], idx[n_val:]

    ds_tr = ChordDataset(X[tr_idx], y[tr_idx])
    ds_val = ChordDataset(X[val_idx], y[val_idx])

    dl_tr = DataLoader(ds_tr, batch_size=args.batch, shuffle=True)
    dl_val = DataLoader(ds_val, batch_size=args.batch)

    # ---- 模型 ----
    model = ChordCNN().to(device)
    n_param = sum(p.numel() for p in model.parameters())
    print(f"[model] 参数量: {n_param:,}")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr,
                            weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)

    # 标签平滑: 和弦边界本身模糊, 硬标签会让模型过度自信
    crit = nn.CrossEntropyLoss(label_smoothing=0.1)

    best_acc = 0.0
    for ep in range(1, args.epochs + 1):
        model.train()
        tot_loss, tot_correct, tot_n = 0.0, 0, 0
        for xb, yb in dl_tr:
            xb, yb = xb.to(device), yb.to(device)
            opt.zero_grad()
            out = model(xb)
            loss = crit(out, yb)
            loss.backward()
            opt.step()

            tot_loss += loss.item() * len(yb)
            tot_correct += (out.argmax(1) == yb).sum().item()
            tot_n += len(yb)
        sched.step()

        tr_loss = tot_loss / max(tot_n, 1)
        tr_acc = tot_correct / max(tot_n, 1)

        # 验证
        model.eval()
        v_correct, v_n = 0, 0
        with torch.no_grad():
            for xb, yb in dl_val:
                xb, yb = xb.to(device), yb.to(device)
                out = model(xb)
                v_correct += (out.argmax(1) == yb).sum().item()
                v_n += len(yb)
        v_acc = v_correct / max(v_n, 1)

        if ep % 5 == 0 or ep == 1:
            print(f"  epoch {ep:3d}  loss {tr_loss:.4f}  "
                  f"train {tr_acc:.3f}  val {v_acc:.3f}")

        if v_acc > best_acc:
            best_acc = v_acc
            torch.save(model.state_dict(), "best.pt")

    print(f"\n[train] 最佳验证准确率: {best_acc:.4f}")
    print("[train] 模型已保存为 best.pt")
    print("[train] 下一步: python3 export_onnx.py --ckpt best.pt")
    return 0


def load_from_wav_dir(dirpath):
    """约定: 文件名格式为 <和弦名>_<序号>.wav, 例如 C_001.wav, Am_012.wav"""
    files = sorted(glob.glob(os.path.join(dirpath, "*.wav")))
    if not files:
        raise RuntimeError(f"{dirpath} 下没有 wav 文件")

    X_list, y_list = [], []
    unknown = 0
    for f in files:
        base = os.path.basename(f).rsplit(".", 1)[0]
        chord_name = base.split("_")[0]
        label = chord_name_to_label(chord_name)
        if label < 0:
            unknown += 1
            continue
        feat = extract_features_from_wav(f, n_frames=8)
        X_list.append(feat)
        y_list.append(label)

    if unknown:
        print(f"[data] 跳过 {unknown} 个无法识别的和弦名")

    X = np.stack(X_list)
    y = np.array(y_list, dtype=np.int64)
    return X, y


def chord_name_to_label(name: str) -> int:
    """和弦名 -> 36 类标签。支持 C, Am, G7, Cmaj7 等"""
    roots = ["C", "C#", "D", "D#", "E", "F",
             "F#", "G", "G#", "A", "A#", "B"]
    # 也接受降号写法
    enharmonic = {"Db": "C#", "Eb": "D#", "Gb": "F#",
                  "Ab": "G#", "Bb": "A#"}

    root = None
    rest = name
    for r in sorted(roots + list(enharmonic), key=len, reverse=True):
        if name.startswith(r):
            root = enharmonic.get(r, r)
            rest = name[len(r):]
            break
    if root is None:
        return -1

    if rest in ("", "maj", "M"):
        quality = "major"
    elif rest in ("m", "min"):
        quality = "minor"
    elif rest in ("7", "dom7"):
        quality = "dominant7"
    elif rest in ("maj7", "M7"):
        # 大七和弦归到 major 类(简化为 3 类时的取舍)
        quality = "major"
    elif rest in ("m7", "min7"):
        quality = "minor"
    else:
        return -1

    return roots.index(root) * len(QUALITY_LIST) + QUALITY_LIST.index(quality)


def main():
    ap = argparse.ArgumentParser(description="和弦识别 CNN 训练")
    ap.add_argument("--data", help="特征目录(含 X.npy/y.npy)")
    ap.add_argument("--wav-dir", help="原始 wav 目录")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    args = ap.parse_args()
    return train(args)


if __name__ == "__main__":
    sys.exit(main())
