# 回家怎么把项目拉到本地

云端沙箱里的代码不会自动出现在你电脑上。这里给三条路，**按顺序试，第一条不行再看第二条**。

---

## 路线 ①：git bundle（推荐）

**为什么推荐**：只传一个文件，但带完整提交历史。以后想回退到任何一版都能做到。

### 步骤

1. **把 `guitar-assist.bundle` 下到本地**
   - 在对话里点这个文件的下载按钮，存到比如 `~/Downloads/`

2. **在电脑上打开终端**，cd 到你想放项目的地方：

   ```bash
   cd ~/projects          # 或者你想放的任何目录
   git clone guitar-assist.bundle guitar-assist
   cd guitar-assist
   ```

   > 如果 bundle 在 Downloads：
   > `git clone ~/Downloads/guitar-assist.bundle guitar-assist`

3. **验证拉下来了**：

   ```bash
   git log --oneline
   ```

   应该看到 5 条提交记录，最后一条是"应用控制器: 6键直达..."。

4. **（可选）设成本地仓库，方便以后加自己的改动**：

   ```bash
   git remote remove origin      # bundle 克隆出来的 origin 指向那个文件，没用了
   git remote add origin <将来你的远程仓库地址>
   ```

### 以后云端有更新怎么办

我会再给你一个新的 bundle，你在本地：

```bash
cd guitar-assist
git pull /path/to/新下载的.bundle master
```

---

## 路线 ②：直接解压 zip

**适合**：不熟 git，只想要当前这份代码。

1. 下载 `guitar-assist-src.zip`
2. 解压到任意目录
3. 进去就是完整项目（`src/` `docs/` `dts/` `scripts/` 等）

> ⚠️ 区别：zip **没有**提交历史，只是当前快照。
> 如果你以后想在本地用 git 管理，在解压后的目录里 `git init` 即可。

---

## 路线 ③：推到 GitHub（想长期同步用）

如果家里和公司两边都要改，建议走 GitHub。告诉我你的仓库地址，我可以：
- 在云端把代码推上去（需要你给一个有写权限的 token，或者你自己在本地推）
- 或者你自己在本地推：`git remote add origin <你的仓库>` 然后 `git push -u origin master`

---

## 拉到本地之后：编译跑起来

### PC 上先验证（不需要板子）

```bash
# 1. 装依赖（Ubuntu/Debian）
sudo apt install build-essential cmake libasound2-dev

# 2. 构建（产物落在 build-pc/）
chmod +x scripts/*.sh
./scripts/build-pc.sh
```

脚本会自动构建并跑一遍调音自测，末尾会打印：

```
结果: 19 / 19 通过
```

**再跑完整测试套件**（7 项，含界面和交互链）：

```bash
cd build-pc
ctest --output-on-failure
```

**期望结果**：`100% tests passed, 0 tests failed out of 7`

如果 ALSA 没装，会看到一条黄色警告说"实时采集功能不可用"，
但**测试仍然全过**（因为采集走的是 WAV 文件回放）—— 这不影响开发。

> 注：`scripts/build-pc.sh` 的产物在 `build-pc/`。
> 而你之前如果直接 `cmake -B build`，产物在 `build/`。
> 两个目录互不干扰，认准你用的是哪个就行。

### 不想接屏，先看界面长什么样

```bash
./build-pc/ui_preview /tmp/ui_out
./build-pc/ui_demo /tmp/ui_demo_out
```

生成的 `.ppm` 文件是图片，用 ImageMagick 或 Python 转成 PNG 就能看：

```bash
# 方法 A: ImageMagick
convert /tmp/ui_out/play_1_C.ppm /tmp/ui_out/play_1_C.png

# 方法 B: Python
python3 -c "from PIL import Image;import glob;[Image.open(p).save(p[:-4]+'.png') for p in glob.glob('/tmp/ui_out/*.ppm')]"
```

`docs/ui-preview/` 里已经有渲染好的 PNG，可以直接看，不用自己跑。

### 交叉编译到泰山派

```bash
# 用泰山派 SDK 的 sysroot（推荐）
./scripts/build-rk3566.sh /path/to/sysroot
```

**⚠️ 这一步我没法在云端验证**（沙箱里没有 aarch64 工具链），
`scripts/build-rk3566.sh` 需要你在本地调。具体见 `docs/DEPLOY.md`。

---

## 到家后的建议顺序

按这个顺序做，出问题最好定位：

| 顺序 | 做什么 | 为什么 |
|---|---|---|
| 1 | **PC 上 `ctest`，确认 7/7 通过** | 先排除环境问题，和板子无关 |
| 2 | **看 `docs/ui-preview/` 的图** | 确认界面是你要的样子，不满意现在改成本最低 |
| 3 | **点屏**：按 `docs/LCD-SETUP.md` 接线上电 | 最大的未知数，早验证早踏实 |
| 4 | **交叉编译，跑通一个最小程序** | 确认工具链没问题 |
| 5 | 接按键，填 GPIO 编号到 `src/ui/input.c` | 需要你先告诉我接线 |

---

## 两个还会再改的地方（先知道一下）

1. **`src/ui/input.c` 里的 GPIO 编号是空的**（都是 `-1`）。
   等你定下 10 个触摸键接哪几个引脚，把编号填进 `ga_input_cfg_t` 就行。
2. **机械机构的驱动没写**。现在是回调 `on_chord_changed`，接的是打印日志。
   等你告诉我机构是用什么控制的（GPIO 通断 / 步进电机 / 舵机 / 串口指令板），
   我写对应驱动。

---

## 万一拉不下来

常见问题：

| 现象 | 原因 | 解决 |
|---|---|---|
| `git clone` 报 `repository not found` | bundle 路径写错了 | 用绝对路径，或先 `cd` 到文件所在目录 |
| 解压后 `src/ui/font/` 是空的 | zip 没下完 | 重新下载，正常应该 272KB 左右 |
| `ctest` 报找不到 `libasound` | 缺开发库 | `sudo apt install libasound2-dev` |
| `build-pc.sh` 没执行权限 | 权限位没保留 | `chmod +x scripts/*.sh` |
