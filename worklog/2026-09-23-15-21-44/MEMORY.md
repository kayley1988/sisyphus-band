# PathFind 助视系统 · 长期项目记忆

## 产品定位（Z 拍板，勿再偏离）
- 目标用户是**白化病低视力人群**（中心视力低 + 眼球震颤 + 畏光），不是全盲。
- 主交互 = **聚焦行模式**：取景框代理注视点 → 左屏高亮当前行在全页的位置 → 右屏 TTF 矢量放大该行 → 默认自动朗读当前行（读完停住）。
- 全文朗读/全文视图只是兜底选项，不是主线。
- 设计硬约束：高亮要有**迟滞**（眼震防抖，0.8s 稳定才切行）；畏光 → 不做满屏高亮、避免高饱和色；常用动作必须有鼠标/触摸入口（手眼配合吃力）。
- 真眼动追踪留待硬件到位，当前一律「取景框代理」。

## 技术要点（E:\pathfind_project\pathfind_main_pipeline.py）
- 运行解释器：系统 Python 3.11.9（`C:\Users\zhouqian\AppData\Local\Programs\Python\Python311\python.exe`），命令行跑必须 `-E`（PYTHONPATH 里有坏 PIL）。
- PaddleOCR 3.x 结果键名：`rec_texts` / `rec_scores` / **`rec_polys`**(N,4,2)；模型必须 `enable_mkldnn=False`。
- TTS 用自包含 `Speaker` 类（pyttsx3，工作线程内初始化引擎）；勿用 tts_module.py（它会把 cp314 的 win_packages 插进 sys.path 污染依赖）。
- 关键模块：FocusTracker（迟滞锁定）、_group_lines（字块聚行）、静止判定（96×54 灰度帧间差）+ 静止时显示 OCR 帧保证框文对齐。
- 自测脚本：`verify_focus.py`（18 项，改完必跑）。

## 电子书 / 电子试题模式（Z 拍板，09-23）
- **两种内容源并列**：摄像头 OCR（纸上/屏幕上的字）⇄ 文件（txt/md/html/docx/pdf/epub，`ebook_module.py`）。文件源没有识别错字、放大不失真，是考试看试题的主路径。
- 解析库本机 3.11 全有（python-docx/PyMuPDF/ebooklib/bs4），零安装；扫描版 PDF 无文本层 → 明确提示转摄像头模式，不做静默兜底。
- 试题结构化：选项≥4 且题号≥1 才判试题；整题朗读=题干+「A、xxx。」；J 标记存疑 / K 逐个回看；进度/书签/标记存书旁 `.read.json` 续读。
- 布局三态（X 键）：左目录+右大字 / 左实景+右大字 / 全屏文字（右屏吃满 1280，字最大）。
- 键位：O 打开 / E 摄像头⇄阅读 / 空格 朗读当前单元 / Y 连续朗读 / , . 上下章（试题=上下题）；阅读模式**不自动读**（课堂场景）。
- 自测：`ebook_module.py --selftest`（37 项）+ `verify_ebook.py`（24 项，改完必跑）。

## 实时字幕放大 · SubtitleLens（已实现 PC 版，09-23）
- **场景拍板**：RK3588（8G+64G）躺着看网课+字幕，给学习人群用；「摄像头拍外部屏幕」Z 明确排除不考虑。
- 形态 = **独立置顶浮窗**（不接主程序）：`subtitle_lens.py` 同机抓屏 → 字幕条直接图像放大 + 高对比增强，**不做 OCR**（零延迟零错字秒开，与纸质书场景相反）。
- 默认 **auto 模式**（黑底黄字）：分位拉伸 + gamma1.9 压暗背景；「字是少数派像素」自动判黑白字方向；hi<110 判无字幕输出全黑（防噪声拉爆）。
- 浮窗 TOPMOST + WDA_EXCLUDEFROMCAPTURE（防抓到自己反馈循环）；S 框选 / +/- 缩放 / C 换模式 / P 冻结 / F 全屏；配置存 subtitle_lens.json。
- 自测 verify_subtitle.py 33/33；实测窗口 TOPMOST+排除截屏生效。
- **翻译模式（T 键，09-23 深夜）**：Z 需求「英文电影/网课不听英文只看中文翻译大字」。链路=后台线程抓字幕条→帧差检测（变了才 OCR）→PaddleOCR lang='ch'（中英混合无需另下模型）→MyMemory 免费在线翻译（en|zh-CN，1.1s 限速+句子缓存，实测连通）→render_zh 黑底黄字 TTF 大字自适应（长句自动缩字号换行，wrap_text 词级 token 防英文单词被拆）。懒加载 OCR（T 键才加载，不拖慢秒开）。
- **遗留**：MyMemory 免费版有量限制（2 小时电影可能超）→ 离线 opus-mt-en-zh 是最终方案（transformers/sentencepiece 已就绪，模型未下载）；无字幕网课需 ASR（第二阶段）。
- **64 位 ctypes 大坑**：SetWindowPos 必须 WinDLL(use_last_error=True)+argtypes=[c_void_p,...]，否则 HWND 当 32 位传**静默失败**；cv2 窗口句柄要等消息线程建窗后 EnumWindows 模糊匹配拿。**排查时注意**：GetWindowLongW 查 WS_EX_TOPMOST 用 GWL_EXSTYLE(-20) 不是 -16；GetWindowTextW 配 create_unicode_buffer（c_buffer 读 W 版 API 中文变乱码）；一切 user32 函数不声明 argtypes 在 64 位都可能静默出错。
- RK3588 端侧路径（后续）：设备自播视频 → 从解码帧取字幕（不用抓屏），NPU OCR 转 RKNN 可到 100ms 级（要朗读时才用）；双目微显示**不能左右眼分屏**（双眼竞争）。

## KR3588 板子现状（09-26 已部署，勿再当"未到货"）
- 卖家叫 **KR3588**，实为**香橙派 5 Plus 兼容板**；SSH `orangepi@192.168.1.57`（sudo 密码 `Kr3588@2026`），静态 IP 已固定。
- 板端已跑 **PathFind v1.2**：OpenCV 5.0.0 / onnxruntime / **RapidOCR（PP-OCRv4 ONNX，板上不用 PaddleOCR）** / 21 色彩模式 / 1-20x 变焦 / Vosk 离线中文语音。
- **超分已 NPU 化**：`sr_model.rknn` 三核并发（全屏 1280×720 147ms，ROI 档 39ms，x4 模型已部署）；systemd 自启+崩溃自愈 `pathfind.service`。
- 边界：WSL/Docker 被拦 → RKNN 模型转换走 **GitHub Actions 云端**（可靠路线：pb 抽权重→PyTorch 重建→纯净 NCHW ONNX→RKNN，不用 tf2onnx）。
- **平台边界结论**：代码只绑三处——`cv2.imshow` 显示 / `mss` 抓屏 / `cv2.waitKey` 输入，算法层纯跨平台；**不需要为安卓重写**。
- 刷机硬约束：必须通电+联网+MASKROM 按键+HDMI 确认，**远程做不了物理动作**——物理归 Z、命令归我。
- 路线图文档：`E:\obsidian\PathFind\硬件\硬件_RK3588系统移植路线图.md`

## 待办
- [ ] TBSRN 假超分：接真模型或明确移除
- [ ] ROI 裁剪提速（现在整帧 OCR 4s，可到 1s 级）
- [ ] 表格/结构化识别、案号字段抽取（法务场景刚需）
- [ ] 多帧融合降错字
- [ ] SoC 已定 **RK3588**（Z 09-23 拍板）；成本口径（¥600-800 / ¥2466-3166 / ¥1380-1970 三说）仍待拍板
- [ ] 电子书：tkinter 文件对话框待 Z 亲测（O 键）；epub 章节可改用 toc 层级
- [ ] 字幕放大：PC 浮窗版+翻译模式已跑，待 Z 实测（T 翻译 / S 框选真实播放器字幕区）；RK3588 解码帧取字幕待端侧实施；离线翻译模型 opus-mt 待接入
