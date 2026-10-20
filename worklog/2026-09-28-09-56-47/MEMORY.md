# 动态剧本杀播放器 — 项目长期约定

> 仓库：`E:\剧情化知识内容生产系统\prod-system` ｜ 笔记：`E:\obsidian\项目笔记\动态剧本杀播放器\`
> 权威文档：仓库 `ARCHITECTURE.md` 文首两节。

## 数据源边界（三条，改动前必读）

| 数据源 | 负责 | 红线 |
|---|---|---|
| 文件系统 | 只读内容资产（剧本、物证）——**权威来源** | 不要为内容资产建库表 |
| MySQL | 用户产生的跨设备数据 + 系统级学习数据 | 7 张表：`User`/`Favorite`/`GameProgress`/`NpcMemory`/`AiScript`/`GenerationRecord`/`EvolutionMemory`（末者**刻意不带 userId**，是全体共享的全局学习） |
| localStorage | 登录用户的**本地缓存层**（读写热路径）+ 设备偏好 | 用户内容必须经 `user-scope` 加作用域；写后经 `cloud-mirror` 异步上云 |

## 本地存储必须走 `src/lib/user-scope.ts`

- **用户内容**（NPC 记忆、AI 剧本、存档、学习进度、手写标注、阅读书签与配图、线索板布局、用户填的 AI 密钥）→ 物理键 `<base>::u:<uid>`
- **设备偏好**（深浅色/面板宽度/TTS 音色）与**成本缓存**（OCR）→ **刻意不隔离**，不要"顺手"加作用域
- **禁止直接调 `localStorage.*`** —— 绕过作用域 = 静默串号
- `UserScopeProvider` 在 render 阶段同步作用域，并用 `key` 强制重挂载子树
  （组件在 `useState` 初始化里读 localStorage，不重挂载会沿用上个账号的数据）
- 遗留裸键归「本机第一个登录的人」，首次读取时认领并删除；模块级单例（如 `getMysteryProgress()`）需 `ensureScope()` 守卫

## 云端镜像（2026-09-29 起）

- NPC 记忆 / AI 剧本 = **本地优先 + 云端镜像**：本地写立即返回，`cloud-mirror.ts` 异步上云；游客不发请求
- 合并**唯一触发点**是 `UserScopeProvider` 登录态就绪：记忆按 drawer.id 并集、剧本按 id+updatedAt 新者胜、本地独有回推
- `NpcMemory` 整键空数组 = 云端删行（防已删记忆复活）；客户端语义是**整键状态上报**，不是条数累加

## 内容门禁 + 闭环（2026-09-30 起）

- 生成前一律走 `src/lib/content-gate.ts` 三层：硬门槛（零 token）→ 适配度评估（1 次 LLM，**可判"不适合"**）→ 分流
- `gateMode`：`'enforce'`（默认，REJECT 阻断 / REVIEW 抛 `GateReviewRequiredError` 交 UI 确认）｜`'warn'`｜`'off'`
- 用户确认后重跑带 `gateConfirmed: true`，第二次不再拦。阈值集中在 `GATE_THRESHOLDS`，调节点只此一处
- **禁止静默兜底**：模型不可用 → `evaluated:false` + `unevaluatedReason`，必须让用户知道，不能假装成功
- 生成后 `checkOutputQuality()` 自检，不阻断但标 `lowQuality`
- **每次尝试上报一条 `GenerationRecord`**（只追加事件流）：`reportGenerationRecord()` 唯一入口、
  **fire-and-forget**（不 await / 异常静默 / `keepalive`），埋点失败绝不影响生成；游客也报（`userId=null`），
  否则拿不到完整拦截率。客户端**不能传 userId**
- `gate-tuning.ts` 判据是 `overridden × qualityOk` **组合**：被拦下+强行生成+产出合格 ⇒ 过严；放行+不合格 ⇒ 过松
- **不做自动改阈值**（硬规矩）：只出建议报告由人 review 后手改；`MIN_SAMPLE_SIZE=20` 才给建议
- 低质量自动重试在**创意生成后立刻**（结构缺陷全来自创意输出，此处 ~20s），**只有更好才采用** + **上限 1 次**
- `checkCreativeQuality` 阻断判据：`WEAK_TRACEABILITY`(<60%) / `FEW_CLUES`(<3) / `NO_REASONING`(<2)；`NO_HOLDER` 只提示

## 进化经验库（「进化」= 系统自己越跑越好）

- 核心：把「无状态生成」改成「有记忆生成」。`src/lib/evolution-memory.ts` 纯逻辑（不碰 DB，可直测）
- 两类：`paradigm_hint`（内容指纹→验证过的范式+权重，命中则**跳过 LLM 适配度评估**）｜
  `pitfall`（缺陷 code→累计次数，≥3 激活后**生成前**注入避坑，60 天 TTL）
- **三条硬约束**：① 经验只影响范式选择与 prompt，**绝不放松硬门槛**（REJECT 永远拦）
  ② **只有门禁 `PASS` 的生成才回写范式经验**（防误伤被学成放行）③ 样本/权重不足一律不采纳
- **界线**：能自动学「内容→策略的映射」，**不能自动改安全线**
- 指纹只用**客观统计特征**（长度/中文占比/重复率/目录占比/行长），**不用标题**；格式校验唯一来源 `isValidSignature()`
- 权重由**服务端**算（Laplace `(ok+1)/(ok+bad+2)`），不信前端；采集与回写全 fire-and-forget
- `/admin/data` 的「进化经验」Tab 会**亮出当前注入给模型的原文** —— 必须能看见往 prompt 塞了什么

## LLM 客户端（唯一入口 `src/lib/llm-provider.ts`）

- **全项目只有 `getLLMClient()` 一处判断密钥**。改造前散落 **9 份**且只有 `book-extractor` 那份认 `.env`。
  **新增 LLM 调用一律 import `getLLMClient()`**，不要再写 `new OpenAI(...)`，也不要读 localStorage 判断密钥
- **三分支**：浏览器有个人密钥→SDK 直连；**浏览器无密钥→服务端代理**（用 `.env` key）；服务端/node→env key。
  浏览器端**永不返回 `null`**（最差是代理）—— 这就是「打开就能用」的支点
- 代理 `serverProxyClient` 伪装成 OpenAI 客户端（同 `chat.completions.create` 签名），内部 `fetch('/api/llm/chat')` ⇒ 调用点零改动
- `/api/llm/chat` **必须透传 `responseFormat`(`json_object`) 与 `maxTokens`**：主链路靠 `response_format: json_object` + 裸 `JSON.parse`，
  默认值 600 会把 JSON 腰斩（主链路要 8000）。上限 8192，超限裁剪而非报错
- `getLLMModel()` 浏览器无个人密钥时**返回空串**（交服务端决定），不要硬编码 `'deepseek-chat'`
- `probeServerLLM()`：生成前预检服务端有无 key（GET，零 token）。抽屉已去掉「无 key 直接拦」，
  改为「前端**与服务端都**没配」才拦（`hasOwnKey` 用 useEffect 同步，SSR 默认 true 防水合不一致）
- **模型侧故障（2026-10-04）**：`402 Insufficient Balance` **不许再原文甩给用户**（含 `request_id`）。
  统一走 `isLLMBalanceError()` / `isLLMAuthError()` / `explainLLMError()`（状态码+文本双判；
  代理由 `/api/llm/chat` **原样透出 402/401/429**，改造前统一 500 ⇒ 欠费被当成「服务器错误」）。
  `probeLLMBalance()` 生成前 1 token 预检；**`kind==='error'`（超时抖动）不拦**，只拦 balance/auth/unavailable
- **模型不可用时禁止给「确认范式继续生成」**：门禁置 `blockedByModel`，UI 只给充值/换密钥/重试 ——
  确认了必然在提取阶段再报一次同样的错（实测踩到）。`unavailableKind` 区分 balance/auth/no_key/other

## PDF 解析（「电子版不需要 OCR」）

- **两条路由必须同口径**：`/api/ocr/pdf`（上传即解析）与 `/api/mystery-scripts/[id]/ocr/[materialId]`（剧本材料）。
  **都走 `parsePdf()` 自动分流**（有文本层→`pdf-parse` 零依赖；无文本层→MinerU），不得直接调 `ocrPdf`；`maxDuration` 均须 1800
- 全项目**只有 MinerU 一套 OCR**。「用 OCR 还是 MinerU」是伪命题——MinerU 就是 OCR。用户唯一要决定的是**机器装不装 MinerU**
- `getMineruPath()`：`MINERU_PATH` → 常见安装位置 → PATH（**不再硬编码绝对路径**）；`isMineruAvailable()` 供降级判断；
  缺 MinerU 抛 `MINERU_MISSING_HINT`（含 "pip install mineru"），不要甩 `ENOENT`。日志打印 `engine: pdf-parse | mineru`，排查先看它
- **分流判据必须是「每页字符密度」**：`hasRealTextLayer(textLength, pageCount)`（`MIN_CHARS_PER_PAGE = 50`）。
  改造前是 `text.length > 100` —— 几百页扫描件只要带页码层（300 页×10 字）就被判「电子版」⇒ **MinerU 根本不会被调用**。别改回绝对阈值
- **MinerU 超时必须可配**：`getMineruTimeoutMs()`（默认 20 分钟，`MINERU_TIMEOUT_MS` 覆盖）。改造前写死 5 分钟 ⇒ 几百页必然超时
  且只报 `killed`。`isLargeOcrJob()`（≥200 页或 ≥40MB）供前端提示「跑得久是正常的」
- **OCR 缓存键只能按文件内容**：`computeOcrCacheKey()`。改造前掺 `filePath + mtimeMs`，而临时文件名是**随机 UUID** ⇒ **缓存永不命中**
- `parsePDFBuffer` 的 `forceOcr` 默认 **false**
- **教训**：同一判断散落多处时，修完必须问「还有没有第二处」；找第二处靠**编译器**（改签名让 tsc 报错），
  不要靠字面量 grep——字符串拼接会让 grep 全扫空

### ✅ OCR 后台任务：进度 / 取消 / 回来取结果（2026-10-02）

- **语义变更（必记）**：**断开 SSE / 关页面 ≠ 取消** —— 那只是「不看进度」，任务继续在服务端跑。
  **取消要显式 `DELETE /api/ocr/jobs/[jobId]`**。HTTP 连接是**观看窗口**，不是任务生命周期
- 结构：`POST /api/ocr/jobs` **秒回** jobId → 后台跑 ｜ `.../[id]/stream` 订阅 ｜ `.../[id]` 取结果（含正文）
  ｜ `GET /api/ocr/jobs` 列我的任务
- ⚠️ **可变状态必须挂 `globalThis` 单例**（不能 `const jobs = new Map()`）：Next App Router 在 dev 下
  **每个 route handler 单独实例化模块**，创建/详情/订阅各持一份 ⇒ 详情路由看不见创建路由的任务
  （实测现象：DELETE 返回 `canceled:false` + 状态 `interrupted`）。**多进程部署已配 sticky session（2026-10-03，见下）**
- **✅ 多进程部署：sticky session 已落地（2026-10-03）**：
  `npm run start:cluster`（`server.js`，零依赖）＝ master `pauseOnConnect` 收 TCP 连接，
  按**客户端 IP** djb2 哈希把整个 socket 发给 worker，worker `emit('connection', socket)`；
  `WEB_CONCURRENCY=N`（默认 1 = 等价 `next start`）。`/api/__worker` 探针返回命中 pid。
  PM2 必须 **fork 模式**跑 server.js（`instances:1`，**别开 PM2 cluster**——两层 cluster 前层就打散了）；
  反代后面 remoteAddress 全是代理 IP ⇒ 用 nginx `ip_hash`（nginx.conf 有注释示例）。
  ⚠️ **nginx `proxy_read_timeout 60s` 会掐断 OCR SSE**（MinerU 20 分钟）——已加 `/api/ocr/` 专用
  location（`proxy_buffering off` + 1800s），**别删**。验证：`verify:sticky`（15 项，
  需先 `NEXT_CUSTOM_DIST_DIR=.nextsticky npx next build`，跑完还原 tsconfig）
- 存储口径（照 `in-memory-state-persistence`）：内存 Map 为主 + **创建即落盘** `.ocr_jobs/<id>.json`；
  **进程重启**时扫盘把 `queued|running` 如实标成 `interrupted`（绝不挂着 running 让人干等）；24h 陈旧清理
- **外发一律白名单投影**（`toSummary`/`toDetail`）：记录上挂着 `filePath`/`listeners`/`abort`，
  「解构删掉 text」的写法会把它们**一起序列化出去**（实测泄漏过）
- **`.ocr_jobs/` 必须排除 webpack watch**（`next.config.js` 的 `watchOptions.ignored`），
  否则任务运行期写盘会重启 dev server ⇒ 内存任务全丢
- **上传上限 500MB**（2026-10-04）：唯一常量 `OCR_MAX_UPLOAD_BYTES`（`ocr-jobs.ts`），
  `OCR_MAX_UPLOAD_MB` 可覆盖；两个上传入口共享，别再写死 `100*1024*1024`
  （实测 122MB 的《天机觉醒》曾被硬拦；App Router route handler 无请求体限制）
- 进度口径：只认 `Processing pages` +「第 i/n 个窗口」加权 `(i-1+frac)/n`
  （tqdm 是**各阶段各自百分比**，直接当总进度会 100%→0% 横跳）
- 取消要杀进程树（MinerU 会拉起 `mineru-api` 子进程）：Windows `taskkill /pid X /T /F`
- **三条入口同一执行器同一事件契约**（progress/result/error/ping + `X-OCR-Job-Id` 响应头）：
  `/api/ocr/pdf`（生成上传）、`/api/mystery-scripts/[id]/ocr/[materialId]`（朗读材料）、`/api/ocr/jobs`
- 顺带修的真 bug：`parsePdf` OCR 分支 `useCache` 写死 `true`；`pageCount` 硬编码 `0`；
  **重订阅已结束任务会 500**（`jobEventStream` 里 `onAbort` 的 TDZ）
- 回归：`verify-ocr-jobs`（**40**，含纯逻辑判据）+ `verify-ocr-progress`（**19**）+ `verify-mineru-parse`（13）

## ✅ 长文本读取（自动档，`fe5ef1e`）

- **演进三步**：纯截断（前 3 万字）→ 均匀采样（`6af8ba3`）→ **自动档**（`fe5ef1e`）
- **改造前**：`book-extractor` 的 `maxTextLength = 30000` 纯截断（`text.slice(0,30000)`）—— 500 页 OCR 出 40 万字只送 7.5%；
  且 prompt 的 `{textLength}` 填的是**未截断的原文长度**（= 对模型谎报「全书 40 万字」却只给 3 万字）
- **自动档三档路由**（`extractBookElements()`）：
  - ≤ `FULL_TEXT_MAX`(8 万字，≈100 页) → **全文直送，不采样**（`mode='full'`）｜100% 覆盖
  - > 8 万字 → 按 `DEEP_READ_CHUNK_CHARS`(4 万字) 切块，逐块提取 → `book-element-merge` 合并去重｜100%
  - 切出 > `MAX_DEEP_READ_CHUNKS`(20) 块（≈80 万字+）→ 均匀采样（成本护栏）
- **绝不静默降级**：成本护栏 / 单块失败都写进 `extractionMeta`
  （`mode`/`originalLength`/`sentLength`/`chunks`/`failedChunks`/`note`）并透出给用户
- ⚠️ **`PER_PART_LIMITS`**：逐段精读时单块各类条数必须**单独收紧**，否则单块输出被 `max_tokens=8000`
  截断 ⇒ **JSON 解析失败**。最终数量由合并阶段决定
- **成本**：40 万字一本 ≈10 次调用、约 **$0.1（¥0.7）**、并发 3~4 路 3~5 分钟 —— **瓶颈是时间不是钱**
- ⚠️ **`text-sampler.ts` / `book-element-merge.ts` 必须保持零运行时 import**（只能 `import type`）：
  一旦 import `pdf-chunker` 这类服务端模块（顶层 `import pdf-parse` → 依赖 Node `fs`），
  会经 `book-extractor → case-paradigms → ScriptGeneratorDrawer`（**客户端组件**）拽进浏览器 bundle
  ⇒ `Module not found: Can't resolve 'fs'` ⇒ **生成页整页 500**（已实际踩到）。B11h / B13n 已加守卫。
  **tsc 与单测抓不到这类 bundle 泄漏，必须真跑 dev server 访问页面才暴露**
- prompt 的 `{text}` 用**函数式替换** `.replace('{text}', () => …)`（正文里的 `$&`/`$1` 不会被展开污染）
- **读取方式必须「真透出」**（`7041271`）：`describeReadMeta()` 是**唯一文案来源**，
  pipeline 生成中发一条、结果区持久展示同一条。⚠️ 别退回「连发两条同 progress 的 message」
  （后一条顶掉前一条，降级提示白做）；别只放在生成过程中（几秒就没了）。
  验证 B13o~B13q，UI 层用 `E2E_FULL=1` 的 E3（真渲染才算数）
- **下次有人抱怨「几百页跑出来不对/不完整」，先看这里**

## 🧟 死代码

- **已清空（2026-10-02）**。整簇删除 6 个（~4200 行）：`ImmersiveGame.tsx`(1001)、`EnhancedImmersiveGame.tsx`(796)、
  `/api/game/chat/route.ts`(164)、`translator.ts`(448)、`/api/test-generate/route.ts`(123)、
  `data/scripts.ts`(1707 / 126KB，因引用者被删成孤儿)；
  另删零引用模块 `mineru-integration.ts`、`pdf-parser-mineru.ts`、`pdf-chunker.ts`、`deepseek.ts`、`src/lib/index.ts`
- **判据**：**调用方死了但 HTTP 端点仍注册** → 无鉴权、烧服务端密钥的公开入口（本身就是安全洞）
- ⚠️ **`pdf-chunker.ts` 不可复用**：顶层 `import pdf-parse`（依赖 Node `fs`），被客户端链路 import 即整页 500。
  纯切块逻辑的正确去处是 `text-sampler.ts` 的 `splitIntoBlocks()`（零依赖）
- 复用任何「现成实现」前，**先看它的顶层 import**，别只看功能

## 🔐 开源前安全清理（2026-09-30 **已处理**，`a578c20`；**Git 历史已重写**）

用户问「能不能推 GitHub」。定为**私有仓库**，阻塞项与该处理项已全部完成。

**根因教训（会重犯）**：`.gitignore` 逐条列 `.env` / `.env.*.local`，**漏了 `.env.production`**
⇒ 真实 `DEEPSEEK_API_KEY` + `NEXTAUTH_SECRET` 随首个提交进了 31 个提交。**只能用 `.env.*` 通配 + `!.env.example`**。
清历史：无 filter-repo/java ⇒ 内置 `git filter-branch --index-filter` → 删 `refs/original`（用 `update-ref -d`，**别 `rm -rf`**）
→ `reflog expire` → `gc --prune=now`（**顺序不能反**）。31 提交 82 秒、hash 全变、`.git` 104M→**77M**。
**备份在** `E:\剧情化知识内容生产系统\prod-system-git-backup-20260930`

- **版权（私有也别留）**：`scripts/epub_result*.json` = **同一本商业出版书的完整正文**（2×936K，md5 相同），
  `hemingway-extract.txt`(158K) 同书摘录 —— 已删 + 清历史
- **本机路径全 env 化**：`MYSTERY_SCRIPTS_ROOT` / `AGENT_BROWSE_ROOTS` / `SD_API_URL`；
  `preset-scripts.ts` 的 `materialDir`（9 条网盘路径、**零消费方**）整字段删；`LICENSE` 原署名 `wxnacy`（抄的模板）
- **⚠️ 顺手修掉一个真越权**：`browse-files` 的 `isPathAllowed()` 末尾原「在 USERPROFILE 下就放行」把白名单架空 ——
  实测 `?dir=C:\Users\<me>\` 旧 **200** / 现 **403**。同类：`.env.example` 的 `ALLOWED_FILE_DIRS` 是**零引用假配置**
- **⚠️ 用户须自查**：旧 DeepSeek key 已进过历史 ⇒ **必须去后台吊销**（清历史救不了已暴露的 key）
- 未动：`public/sounds/**`(~85M) 无授权说明需用户自行确认；`chi_sim.traineddata`+`tesseract.js` 零引用可删；
  README 仍旧叙事；门槛 MySQL+MinerU(pip)+可选 SD/Ollama
- 细节见 Obsidian《开源前安全清理-密钥版权与硬编码路径》

## 身份

- 唯一来源：服务端 `getSessionUser()`（`src/lib/auth-guard.ts`）
- API **不接受前端传入的 userId**；未登录写操作返 `401 { code: 'UNAUTHENTICATED' }`
- 管理员 = `User.role === 'ADMIN'`（不拆表；首位注册者自动 ADMIN）；管理 API 走 `requireAdmin()`
  + 自保规则：不能改自己角色 / 不能删自己 / 管理员不可归零

## 验证手法（改完必跑）

- `npx tsc --noEmit` → 0 错误
- `node scripts/verify-{user-isolation,cloud-mirror,admin-data}.mjs` → 16 / 24 / 36
- `node scripts/verify-{content-gate,gate-loop,evolution}.mjs` → 51 / 69 / 80
- `node scripts/verify-mineru-parse.mjs` → **13**（OCR 进度纯逻辑：真实 MinerU 输出解析 + 300 页/5 窗口单调性 + 噪声零误报）
- `node --experimental-strip-types scripts/verify-ocr-progress.mjs` → **20 通过 / 1 跳过**（真进度：响应契约 +
  进度单调 + 参数校验仍 JSON + **后台任务语义**（断开≠取消 / 显式 DELETE / 重订阅补发终态）；
  `tasklist` 那句在沙箱里会被拦所以跳过；**必须带 flag**）
- `node --experimental-strip-types scripts/verify-ocr-jobs.mjs` → **40**（后台任务全套：纯逻辑判据（重启对账 /
  陈旧清理 / 白名单投影 / 越权）+ 生命周期 + 落盘 + 越权隔离 + **断开≠取消** + 显式取消 + 材料型入口 + /api/ocr/pdf 契约）
- `node --experimental-strip-types scripts/verify-{rate-limit,docx}.mjs` → 29 / 19（**同样必须带 flag**）
- `node scripts/verify-suitability-live.mjs` → 8（真实 LLM，需 .env key）
- `node scripts/verify-deep-read-live.mjs` → **10**（真实 LLM 跑逐段精读：喂 9.3 万字 → 3 块并发 3，
  实测 **14.3 秒 / ¥0.168**，人物合并 24→9。**这是 deep-read 分支唯一的真模型端到端证据** ——
  B13 段是桩、E 段走的是 mode=full，都覆盖不到它。`DRY=1` 只算长度不烧钱）
- `node scripts/verify-player-cleanup.mjs` → **6**（删死代码后播放器链路仍无 JS 异常）
- `node scripts/verify-zero-config.mjs` → **71 + 1 跳过**（`E2E_FULL=1` 时 72）（代理契约 / PDF 分流含**扫描件→mineru 实证** / 密度判据 7 边界 /
  超时可配 / 缓存键 / **两条 OCR 路由同口径 B8b~B8d** / **长文本采样 B11a~B11h** /
  **长文档自动档 B13a~B13q（本地 HTTP 桩，零真实 token）** / 调用点收敛 /
  **清空 localStorage 模拟全新用户** / 端到端真实生成；`E2E_FULL=1` 追加 E3
  「结果区真的显示读取方式」；`SKIP_E2E=1` 跳过 E 段）
- 跑脚本前先 `HTTP_PROXY= HTTPS_PROXY= node ...`（本机系统代理会干扰 localhost）；自定义端口 `BASE_URL=http://localhost:3100`
- **`NEXT_CUSTOM_DIST_DIR` 会被 Next.js 写进 `tsconfig.json` 的 include** → 提交前必须 `git checkout -- tsconfig.json`
- 沙箱 safe-delete 会拦 dev server 启动清理 ⇒ 用**全新目录名**（`.nextsample1` → 重启换 `.nextsample2`）

## 已知缺口（改动前先看）

用户原始诉求：「上传后系统自己判断通过，模型区分性质，生成一整套情节。」

- ~~零配置可用性断裂~~ **✅ 已修**。但边界要记住：修的是「用户不必自己配密钥」，**不是「不需要密钥」**——
  服务端 `.env` 必须配，否则代理 500；且所有未配用户调用都经你的服务端，**上线前必须限流**
- `next.config.js` 不向前端注入 env ⇒ 浏览器端 `process.env.DEEPSEEK_API_KEY` 恒为 undefined，
  这是「浏览器只能靠个人密钥或服务端代理」的**根因**，别以为 env 兜底能帮到浏览器
- **生成全程跑在浏览器端**（`'use client'` + 直接 import pipeline）⇒ 关页/刷新 = 60~90s 全废；
  生成完需**手动点「播放」**。**这两条是现在最大的体验缺口，未改**
- **封面依赖本地 SD**（`image-generator.ts` 硬编码 `localhost:7860`）⇒ 没跑 SD 就没封面（有 try-catch 不阻断，观感差）
- **`assignedVoices` 未接进播放器** ⇒ 每角色音色没用上，TTS 走全局 voiceProfile
- 上传 **PDF/DOCX/TXT/MD** 单文件（DOCX 走零依赖 `docx-parser.ts`；旧 `.doc` 明确报错让另存为 docx）
  —— 仍无 URL 抓取 / 批量 / pptx / xlsx
- ~~几百页 OCR 同步等待、无进度、不能取消~~ **✅ 已修**（真进度条 + 真取消，SSE）。仍**同步等待**，未后台任务化（`largeJob` 已返回但没用上）
- **长文档（>8 万字）现在真的会被读完，代价是时间**：几百页 = OCR 15 分钟 + 逐块精读 3~5 分钟。
  「读得完」与「等得短」是权衡，别为了快把 `FULL_TEXT_MAX`/分块逻辑改回截断
- `.env` 的 DeepSeek key **未轮换** ← 它现在是**所有未配用户的实际凭证**，优先级已升高
- 待办排序：生成搬服务端（根治关页/刷新全废，面大）> 上传批量 + URL 抓取 > 无 SD 封面兜底 >
  OCR 后台任务化（现在能取消，但仍占着页面）> 播放器接 `assignedVoices`
  （~~自动进播放器~~ ✅ 已做 ｜ ~~代理限流~~ ✅ 已做 ｜ ~~删死代码~~ ✅ 已删）
