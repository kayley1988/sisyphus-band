
## LexFlow 开发约定（2026-09-29 起）
- **改后端代码后必跑回归**：`cd E:/FALV/FALV/newlaw/backend && python tests/smoke.py`（fast ~1s）；涉及合同审查链路或上线前跑 `--full`（~22s，调LLM）。支持 `--port 8002`
- 项目根 E:/FALV/FALV/newlaw；后端用系统 Python311；登录 admin/lexflow2026（app/core/auth.py 默认）；正式测试放 backend/tests/（.gitignore 已加白名单）
- 正式改动一律 git 提交（出问题可回滚）；NSSM 服务改码后需重启才生效 —— **重启唯一通路：在 PowerShell 工具里 `Start-Process -FilePath 'E:\FALV\tools\nssm\nssm.exe' -ArgumentList 'restart','LexFlowBackend' -Verb RunAs -Wait`（弹 UAC 让用户点「是」）**；`nssm restart` / `taskkill` 在普通权限下均"拒绝访问"（服务以 LocalSystem 运行），`sc.exe` 在沙箱黑名单不可绕过；Bash 里调 powershell.exe 会被安全策略拒绝。重启后验证：PID 变化 + `/openapi.json` 路由比对 + `assets/index-*.js` 与 dist-prod 一致
- 路径一律走 backend/app/core/paths.py（PROJECT_ROOT 自动推导/env 覆盖），禁止在业务代码写死绝对路径；.env 路径类键经 paths.py 注入（项目无 load_dotenv，os.getenv 读不到 .env）
- **确定性规则与 LLM 结论必须分权**：凡是"拦不拦 / 放不放行"这类判定，只能由可复现的固定规则给出结论；LLM 有随机性，其结论一律降级为 advisory（只展示、不参与拦截），且 LLM 调用要锁 temperature=0。否则同一份输入两次结论不同，用户立刻不再信任该闸门
- 前端 Vue 文件改完要查插值：`{ xxx }`（单花括号）不会生效，必须是 `{{ xxx }}`，否则页面原样显示变量名
- **闸门要放在数据落库的那一侧**：草稿类内容在页面上可编辑，只在"生成时/前端"做校验等于没做 —— 校验必须在 `install/save` 这类写库接口里用**提交时的内容**再跑一次，前端只负责弹窗确认
