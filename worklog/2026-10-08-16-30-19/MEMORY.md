# 项目长期备忘（造物集拼豆）

## 项目位置
- 造物集手工门店系统（拼豆项目）：`E:/zaowuji/`
  - 后端：`backend/`（FastAPI + SQLAlchemy + MySQL，入口 `backend/main.py`）
  - 前端：`frontend/`（Vue3 + Element Plus，`frontend/dist` 是构建产物需重新 build）
- 审计报告 Obsidian 笔记：`E:/obsidian/LexFlow/造物集拼豆/`（00总览/01 P0/02 P1/03 P2/04修复记录）

## 关键约定
- 用户明确「不需要微信支付」——支付模块（payment.py/payments.py/PaymentDialog.vue）已删除，勿再恢复。
- pydantic-settings 用 2.x：`.env` 加载必须写 `model_config = SettingsConfigDict(env_file=".env")`，`class Config` 不生效。
- 密钥配置键名：`secret_key` 字段对应 `.env` 的 `SECRET_KEY`（不是 JWT_SECRET）。
- WS 实时通道（/ws/timer）已加 token 鉴权 + 按 store_id 隔离；H5 顾客走 `/customer-portal/ws-token` 短时效 token。

## 待办（未做）
- P1-8 手机号 Fernet 加密；P1-9 HTTP 接口全量补 require_store；P2-11 库存原子扣减、P2-13 金额精度、P2-15 会员卡风控。
- 上线前：`.env` 的 `ENV` 改 `production`、`DB_PASSWORD` 换强密码、`CORS_ORIGINS` 改真实域名、前端重新 build。
