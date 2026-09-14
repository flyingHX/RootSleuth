# 演示账号说明（Demo Accounts）

> 适用范围：AIOps 控制台（`/workspace/app`）预览/演示环境。
> 最后更新：2026-09-12（与 `app/backend/routers/auth.py` · `DEMO_LOGIN_ACCOUNTS` 及种子数据逐一对齐）

## 1. 重要说明：无密码机制

控制台**不使用密码登录**，演示账号同样没有密码：

- 正式认证走 Atoms OIDC（`/api/v1/auth/login` → `/api/v1/auth/callback`）或平台令牌交换（`POST /api/v1/auth/token/exchange`）；
- 预览/演示环境无法完成外部 OIDC 跳转，因此提供**免密演示登录** `POST /api/v1/auth/demo-login`：仅需提交邮箱，后端校验邮箱在预设白名单内后，复用既有 JWT 签发链路直接发令牌；
- 请求体 `DemoLoginRequest` 仅有 `email` 一个字段，**不存在任何密码字段**（全仓检索确认无 password 凭证）；
- 邮箱即凭证：不在白名单内的邮箱返回 400「仅允许预设演示账号登录」。

## 2. 演示账号清单

| 邮箱（登录名） | 显示名称 | 控制台角色 | 角色层级 | 典型用途 |
|----------------|----------|------------|----------|----------|
| `demo-operator@atoms.dev` | Demo 值班运维 | `operator` | viewer < **operator** < sre < approver < kb_admin < sys_admin | 告警查看、AI 诊断触发、诊断反馈；规则/未知队列只读 |
| `demo-sre@atoms.dev` | Demo SRE | `sre` | … < **sre** < approver … | 知识库变更集创建、合并提案、未知模板晋升（发起审批） |
| `demo-lead@atoms.dev` | Demo 审批人 | `approver` | … < **approver** < kb_admin … | 审批中心决策（通过/拒绝/撤回）、查看审批内容与 Diff |
| `demo-admin@atoms.dev` | Demo 管理员 | `sys_admin` | 最高层级 | 规则发布/回滚/校验、审计日志、配置中心、用户与角色管理 |

角色来源：配置中心 `role_bindings_json`（email → role 映射），默认值为
`{"demo-operator@atoms.dev":"operator","demo-sre@atoms.dev":"sre","demo-lead@atoms.dev":"approver","demo-admin@atoms.dev":"sys_admin"}`；
未绑定用户按 `default_role`（默认 `viewer`）解析。sys_admin 可在「运维 → 用户与角色」中调整绑定。

## 3. 登录方式

### 3.1 前端（预览环境）

控制台前端在预览环境下自动以 `demo-admin@atoms.dev` 完成演示登录（`ConsoleLayout` 检测到无凭证时自动调用 demo-login）；手动登出后不再自动登录，可通过 URL 携带 `?token=<JWT>` 直达登录态。

### 3.2 API（curl 示例）

```bash
# 以 demo-sre 登录（将 email 替换为上表任意演示邮箱即可切换角色）
curl -s -X POST http://localhost:8000/api/v1/auth/demo-login \
  -H "Content-Type: application/json" \
  -d '{"email": "demo-sre@atoms.dev"}'
# 响应：{"token": "<JWT>"}，后续请求携带 Authorization: Bearer <JWT>
```

## 4. 安全约束（生产环境必读）

- 演示登录端点由环境变量 `ENABLE_DEMO_LOGIN` 控制，默认开启仅为预览便利；
- **生产环境必须设置 `ENABLE_DEMO_LOGIN=false`**，此时该端点返回 404「演示登录未启用」；
- 演示账号仅用于功能演示与验收，请勿在生产库保留其角色绑定。

## 5. 中间件/服务 daemon 账号与默认凭证

各服务以系统进程/容器运行，无控制台登录账号；其服务级（daemon）账号与默认凭证如下，来源 `aiops-rag-system/docker-compose.yml` 与部署手册：

| 组件 | 账号 | 密码 | 说明 |
|------|------|------|------|
| PostgreSQL | Atoms Cloud 托管库由平台注入连接串；自建库建议账号 `aiops_console` | 运维创建时自行设置强密码（不落任何文档明文） | 连接串写入 `DATABASE_URL`：`postgresql+asyncpg://aiops_console:<密码>@<host>:5432/aiops_console` |
| MinIO | `minioadmin` | `minioadmin` | compose 默认值（MINIO_ACCESS_KEY/SECRET_KEY），仅限本地开发 |
| etcd | 无认证 | 无 | 仅容器网络内监听，Milvus 元数据存储 |
| Kafka | 无认证（PLAINTEXT） | 无 | KRaft 单节点，`ALLOW_PLAINTEXT_LISTENER=yes` |
| Redis | 无密码 | 无 | `redis-server --appendonly yes`，未设 requirepass |
| Elasticsearch | 无认证 | 无 | `xpack.security.enabled=false` |
| 控制台前端/后端 | 无 daemon 登录账号 | 无 | 以 OS 进程/容器运行，鉴权统一走 Atoms OIDC + JWT |

> 安全提示：以上默认凭证仅为本地开发/演示便利。生产部署必须：修改 MinIO 访问密钥、为 Kafka/Redis/ES 启用认证并配置强密码、PostgreSQL 使用专用最小权限账号（详见 `docs/OPERATIONS_DEPLOYMENT_GUIDE.md` §15「PostgreSQL 生产化」与 §21「中间件架构总览」）。
