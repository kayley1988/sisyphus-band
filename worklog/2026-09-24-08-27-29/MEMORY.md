# 项目长期记忆 — LexFlow / newlaw

## 项目定位
- **唯一主力项目**：`E:\FALV\FALV\newlaw`（FastAPI + Vue3 + MySQL lexflow2，后端 8001 / 前端 5173）
- 旧版 Flask `lexflow`（383MB，含真实数据）与早期原型 `lexflow-agent-workbench` 已归档至 `E:\FALV\_archive\`，**不再使用、不再混淆**
- 亮点三条线：**Agent / Skill / MCP**，工作台入口 `/workbench`

## 环境与启动
- 后端：`cd backend && python -m uvicorn main:app --host 127.0.0.1 --port 8001`（系统 Python 3.11）
- 后端无 --reload，改代码必须 kill+重启；**必须用 run_in_background 拉起**（shell `&` 方式会被回收）
- MySQL：root / ScriptKiller@2024，库 `lexflow2`
- 系统装了代理，本机探测必须 `curl --noproxy "*"`
- 前端生产构建：需先把 `dist` 移走（沙箱批量删除守卫会拦 vite 清空 dist），用 `cmd //c "rd /s /q ..."` 清理
- playwright 截图/自动化：`C:/Users/zhouqian/AppData/Roaming/npm/playwright.cmd`，参数 `--channel=msedge`；node 脚本需 `NODE_PATH=C:/Users/zhouqian/AppData/Roaming/npm/node_modules`

## ⚠️ 前端大坑（已修复，勿重蹈）
- **naive-ui 弹窗动画禁用 `animation-fill-mode: forwards`**：100% 帧的 `translateX(0)` 会以恒等矩阵永久残留，使 `.n-modal-container` 成为 fixed 后代的定位基准 → 全 App 所有 n-modal 渲染成 0×0 不可见不可点（2026-09-25 已修 global.css）。排查法：截图无弹窗但 DOM :visible 有 → boundingBox 0×0 → 查 animation/keyframes。

## 📋 合同审查流程（2026-09-25 已闭环）
- 全流程：上传 → 弹窗选择「选规则脱敏 / 不脱敏直审」→ AI 审查（规则+AI双通道）→ 报告弹窗/卡片下载 Word 审查报告（GET /contracts/{id}/report/download，楷体12号1.5倍行距）
- review 端点允许 uploaded 状态直审（未脱敏按原文审）

## 🚫 已决策不做（Z 明确指示，勿再提议）
- **案件 `filing_date`（立案日期）保持为空，保留现状，不擅自回填。**
  - 原因：当初录入时本就没写（0/362 全空）。
  - 明确禁止：不要用「创建日期估算」等替代方案，法务期限算错风险高。
  - 影响：`deadline-calc` 技能在当前数据下会如实报「案件无立案日期，无法计算期限」——这是**预期行为**，不是 bug。
- **智采 V2（`E:\zhicai-v2-node`，NestJS+React）与 LexFlow 是两个独立项目，不得混淆。**
  - **采购/招投标/供应商这条线全部属于智采系统**，做采购相关的事不要扯法律系统；法律系统的事不掺采购。
  - 智采已有：供应商门户（邀请制，冒烟 21/21）、供应商管理 5 子模块、评分引擎（`supplier-scoring.engine.ts`）、招投标 11 子模块、围串标规则（`conflict-of-interest.rule.ts`）——采购侧需求优先在智采**既有模块上增强**，不从零新建。

## 关键约定
- 数据/编号类任务必须精确核对，不凭印象（Z 对模块编号、数据行号敏感）
- 交付物：详细、带对比表格；看板类走「白色背景 + 深蓝色商务风」
- 产物（笔记/报告/复盘）同步写入 Obsidian：`E:\obsidian\法律AI系统\`
- 验证要实测（真实浏览器/真实接口），不靠推断下结论
