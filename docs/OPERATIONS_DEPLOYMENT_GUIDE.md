# AIOps 运营控制台运维部署方案手册

> 适用范围：整个 AIOps 项目——运营控制台 `/workspace/app`（React + Vite 前端 + FastAPI 后端 + Atoms Cloud 托管 PostgreSQL）与 RAG 流水线 `/workspace/aiops-rag-system`（Python，含 Milvus/Kafka/Redis/ES docker-compose），两者独立部署。
> 项目级文档统一存放于 `/workspace/docs`（本手册所在目录）；目录关系见根目录 `README.md`。

## 1. 系统组成

| 组件 | 目录 | 技术 | 默认端口 |
|------|------|------|----------|
| 前端 | `app/frontend` | React 18 + Vite + shadcn/ui + Tailwind | 3000（自动避让） |
| 后端 | `app/backend` | FastAPI + SQLAlchemy async + pydantic-settings | 8000（自动避让） |
| 数据库 | Atoms Cloud 托管 | PostgreSQL（ORM 自动建表） | - |
| 认证 | Atoms 平台 | OIDC + JWT（演示环境另有 demo-login） | - |

## 2. 环境要求

- Node.js ≥ 18，包管理器 pnpm（缺失时脚本回退 npm）。
- Python 3.10+，依赖管理 uv（`uv venv` + `uv pip install`）。
- 后端依赖清单：`app/backend/requirements.txt` 与 `requirements.default`（两份都要安装，脚本已按序处理）。

## 3. 一键启动（开发/预览）

```bash
bash app/start_app_v2.sh
```

脚本行为：
1. 自动探测本机 IP 与可用端口对（后端从 8000、前端从 3000 起步，被占用自动 +1 重试，最多 3 次）。
2. 安装后端依赖（uv）与前端依赖（pnpm install + `@metagptx/web-sdk@latest`）。
3. 启动后端：`uvicorn main:app --host 0.0.0.0 --port $BACKEND_PORT --reload`（排除 *.log、*.pyc）。
4. 启动前端：`pnpm dev --host 0.0.0.0 --port $FRONTEND_PORT`（Vite 将 `/api` 代理到后端端口）。
5. 轮询 `GET http://$LOCAL_IP:$BACKEND_PORT/health` 直到就绪（最多 60 秒）。
6. Ctrl+C 一并停止前后端（trap 清理）。

常用参数：
- `--no-start`：只安装依赖不启动。
- `--local`：本地模式，将 `$$BACKEND_DOMAIN$$`/`$$FRONTEND_DOMAIN$$` 占位符替换为本机地址。
- `--env-filename FILE`：指定环境变量文件（默认 `.env`；未提供 S2S 参数时启用文件模式）。
- 环境变量方式：`S2S_JWT_TOKEN`、`S2S_JWT_BASE_URL`、`CHAT_ID` 提供时改从平台 API 拉取配置（固定 dev 环境，并支持 dirty 信号热刷新）。

## 4. 前后端独立启动

后端（在 `app/backend` 下）：

```bash
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install -r requirements.default
export IS_LAMBDA=false
uvicorn main:app --host 0.0.0.0 --port 8000
```

前端（在 `app/frontend` 下）：

```bash
pnpm install
BACKEND_PORT=8000 pnpm dev        # 开发模式，/api 代理到后端
BACKEND_PORT=8000 pnpm build      # 生产构建，产物在 dist/
```

> 生产构建如需预渲染 `/` 与 `/blog/`，保持 `vite.config.ts` 中现有 prerender 插件配置即可；预渲染数据由 `prerender/` 脚本生成。

## 5. 数据库初始化与种子数据

1. **建表**：后端启动时通过 ORM 自动创建全部业务表（events、kb_cases、kb_change_sets、kb_versions、kb_merge_proposals、approval_requests、approval_steps、rule_versions、unknown_templates、audit_logs、console_configs），无需手工执行 SQL。
2. **演示种子**（预览/演示环境）：

```bash
cd app/backend
python scripts/seed_console_demo.py
```

3. **序列同步（重要）**：种子脚本使用显式 ID 插入，PostgreSQL 自增序列不会自动推进，会导致后续 INSERT 主键冲突（典型症状：审批/晋升接口 500）。执行：

```bash
python scripts/fix_sequences.py
```

该脚本将 11 张表的 id 序列重置为 `MAX(id)+1`。**任何使用显式 ID 导入数据之后都必须执行一次。**

## 6. 环境变量说明

后端通过 `core/config.py`（pydantic-settings）读取环境变量，未显式声明的键可用 `settings.<name>` 动态读取（snake_case → 大写环境变量）。

| 变量 | 说明 |
|------|------|
| `IS_LAMBDA` | 固定 `false`（本地/自部署）；true 时走 Lambda 入口 `lambda_handler.py` |
| `ENVIRONMENT` | 环境标识，本地为 `dev` |
| `BACKEND_PORT` / `FRONTEND_PORT` | 启动脚本自动分配并导出，覆盖 .env 同名值 |
| `S2S_JWT_TOKEN` / `S2S_JWT_BASE_URL` / `CHAT_ID` | 平台配置拉取凭证（可选，提供后从 API 拉环境变量） |
| `USERNAME` | 平台调试请求头（可选） |

认证相关密钥由 Atoms 平台注入（OIDC/JWKS），无需手工配置；AI 能力走内置 AIHub，无需自备 API Key。

> **诊断确定性配置（波动治理）**：诊断 Agent 采样温度不在环境变量中管理，而是走控制台配置中心 `console_configs` 的 `diagnose_temperature` 键（默认 `0`）。生产环境保持默认 0 以保证同一事件重复深度诊断输出稳定（模型、检索与工具轨迹可复现）；如需多路径探索再调高。修改即时生效并写 `config_update` 审计，详见《深度诊断 Agent 现状设计说明书》§8。

> **诊断墙钟时间预算（Cloudflare 502 防护）**：诊断整体耗时同样走配置中心 `diagnose_time_budget_seconds` 键（默认 `90` 秒，范围 30~600）。多轮 ReAct 推理、强制收尾与单轮降级的时长叠加受该预算约束：余量不足即停止推理转入收尾，预算耗尽跳过 LLM 降级并返回结构化 502（`diagnose_time_budget_exhausted`），避免 LLM 变慢时请求时长叠加越过边缘代理（如 Cloudflare 默认 100s）或 Nginx 超时。生产部署时预算值应小于链路中最短的超时预算（边缘代理 < Nginx `proxy_read_timeout` < 应用内部超时）；经 Cloudflare 代理的部署建议保持默认 90 或更低。修改即时生效并写 `config_update` 审计。

### 6.1 RAG 流水线环境变量（/workspace/aiops-rag-system）

RAG 服务为独立进程（经 `src/config.py` 读取环境变量），完整模板见 `aiops-rag-system/.env.example`。与服务间鉴权、多租户隔离相关的核心项（P0）：

| 变量 | 说明 |
|------|------|
| `RAG_AUTH_ENABLED` | 鉴权总开关；`false` 或未配置任何 Key 时鉴权关闭（存量部署兼容，匿名按 default 租户放行） |
| `RAG_API_KEYS_JSON` | Key → `{name, tenant_id, scopes}` 映射 JSON（支持简写 `{"<key>": "console"}`）。`read`=检索/验证/观测（kb-search、kb-sync/verify、kb-cache/status、diagnostic）；`write`=kb-sync upsert/delete、kb-cache/invalidate、feedback、cases/close。配置后未携带/错误 Key → 401，缺 scope → 403 |
| `RAG_DEFAULT_TENANT` | 身份缺省租户（默认 `default`）。`default` 为公共知识层：租户 t 可见 `{default, t}`，非 default 租户互相不可见 → 跨租户越权召回率 0% |
| `LLM_API_KEY` / `EMBEDDING_API_KEY` | LLM 与 Embedding 凭证，走平台密钥管理注入 |

生产要求：`RAG_AUTH_ENABLED=true` 并按最小 scope 配置 Key；控制台后端经 `rag_api_key` 配置以 `X-API-Key` 头透传调用 RAG（见 `app/backend/services/rag_sync.py`、`console_agent.py`）；`/api/v1/webhook` 保持免鉴权契约。验证方式：

```bash
# 未携带 Key 应 401；错误 Key 应 401；缺 write scope 的 Key 调写接口应 403
curl -i http://localhost:8080/api/v1/kb-search -X POST -H 'Content-Type: application/json' -d '{}'
curl -i -H "X-API-Key: <console-key>" http://localhost:8080/api/v1/kb-sync/verify/E2E-CASE-1
```

## 7. 健康检查与验证

```bash
curl http://localhost:8000/health          # 200 即后端就绪
curl http://localhost:8000/docs            # OpenAPI 文档
# 控制台 API 抽样验证（需登录态）
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/console/permissions
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/console/dashboard
```

未携带 Token 访问控制台 API 应返回 401（RBAC 生效）。前端打开 `http://localhost:3000` 应出现登录页；演示账号见《使用手册》§1。

## 8. 日志

| 路径 | 内容 |
|------|------|
| `app/backend/logs/app_YYYYMMDD.log` | 后端启动与请求日志（按日滚动） |
| `app/backend/logs/restart.log` | 重启与异常堆栈记录 |

uvicorn `--reload` 热重载已排除 `*.log`，日志写入不会触发重启风暴。

## 9. 生产部署要点

1. **前端静态化**：`pnpm build` 产出 `dist/`，由 Nginx/网关直接托管静态文件。
2. **后端常驻**：生产去掉 `--reload`，用 systemd / supervisor / 容器守护：

```ini
# systemd 示例（/etc/systemd/system/aiops-console.service）
[Service]
WorkingDirectory=/opt/app/backend
Environment=IS_LAMBDA=false
ExecStart=/opt/app/backend/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
```

3. **反向代理（Nginx 示例）**：

```nginx
server {
    listen 443 ssl;
    server_name console.example.com;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    location / {
        root /opt/app/frontend/dist;
        try_files $uri /index.html;   # SPA 路由回退
    }
}
```

- 回调域名需与 Atoms OIDC 应用配置一致；统一走 HTTPS。

## 10. 备份与恢复

```bash
# 备份（业务库）
pg_dump "$DATABASE_URL" -Fc -f console_backup_$(date +%F).dump

# 恢复
pg_restore --clean --if-exists -d "$DATABASE_URL" console_backup_2026-09-11.dump
python scripts/fix_sequences.py   # 恢复后同步序列
```

- 审计日志（audit_logs）为合规数据，建议纳入每日备份且保留 ≥ 180 天。
- 知识案例历史版本（kb_versions）与规则版本（rule_versions）均只增不改，备份后可随时追溯。

## 11. 升级与回滚

1. **升级**：拉取新代码 → 安装依赖（后端 uv / 前端 pnpm）→ 重启后端（ORM 自动补齐新表/列）→ 前端重新 build 替换 dist。升级前执行 §10 备份。
2. **应用回滚**：切回上一版本代码并重启；数据库结构仅向后兼容新增时无需回滚数据库。
3. **数据回滚**：业务语义回滚优先使用控制台内置能力（知识案例版本回滚、规则版本回滚），比直接改库安全且自带审计。
4. **序列修复**：任何显式 ID 导入/恢复后执行 `scripts/fix_sequences.py`。

## 12. 故障排查

| 现象 | 排查与处置 |
|------|-----------|
| 后端起不来 | 查看 `logs/restart.log` 与 `logs/app_*.log`；确认依赖两份 requirements 均已安装、`.venv` 已激活 |
| 端口被占 | 脚本自动 +1 避让；手动启动时确认 `BACKEND_PORT`/`FRONTEND_PORT` |
| 审批/晋升 500 主键冲突 | 执行 `python scripts/fix_sequences.py`（显式 ID 种子导致序列未同步） |
| 前端 401 / 登录循环 | 确认走 SDK 登录（localStorage `token`）；检查回调路由 `/auth/callback` 与 OIDC 域名配置 |
| AI 诊断全部超时 | 配置中心调大 `llm_timeout_seconds`；确认平台 AIHub 可用 |
| RAG 全部无召回 | 知识库为空或案例均归档；执行种子脚本或补充活跃案例 |
| 规则发布后未生效 | 查看规则版本列表确认目标版本 status=active；审计有 `rule_reload` 记录即热加载成功 |
| 前端 /api 404 | 开发模式确认 Vite 代理目标端口 = 后端实际端口（`BACKEND_PORT`） |

## 13. 安全注意事项

- 生产环境禁用演示登录依赖的快捷通道前先评估（`/api/v1/auth/demo-login` 仅应存在于预览环境）。
- 默认角色 `viewer` 最低权限；`role_bindings_json` 中按邮箱精确绑定。
- 全部写操作（审批/发布/回滚/配置）强制审计，禁止绕过控制台直改数据库。
- 数据库连接串、平台密钥仅存于环境变量/平台密钥管理，禁止提交到仓库。

## 14. 资源规划与端口清单

### 14.1 控制台（/workspace/app）
| 环境 | CPU | 内存 | 磁盘 | 网络 |
|------|-----|------|------|------|
| 最低（开发/预览） | 2 核 | 4 GB | 40 GB SSD | 出网访问 Atoms 平台（认证/AIHub/托管 DB） |
| 生产推荐 | 4 核 | 8 GB | 100 GB SSD（日志+备份另计） | 内网千兆，仅开放 80/443 |

### 14.2 RAG 流水线（/workspace/aiops-rag-system，含 Milvus 全家桶）
| 环境 | CPU | 内存 | 磁盘 | 说明 |
|------|-----|------|------|------|
| 最低 | 4 核 | 16 GB | 100 GB SSD | 单机 compose，ES 堆 512MB |
| 生产推荐 | 8 核 | 32 GB | 500 GB SSD | Milvus 数据随案例数线性增长；Kafka/ES 按告警吞吐评估 |

### 14.3 端口清单
| 端口 | 服务 | 协议 | 暴露建议 |
|------|------|------|----------|
| 443/80 | Nginx/Ingress（前端 + /api 反代） | HTTP(S) | 对外 |
| 8000 | 控制台 FastAPI 后端 | HTTP | 仅内网/127.0.0.1 |
| 8001 | RAG 流水线 API（独立进程） | HTTP | 仅内网 |
| 5432 | PostgreSQL（托管或自建） | TCP | 仅内网 |
| 19530 | Milvus gRPC | gRPC | 仅内网 |
| 9091 | Milvus metrics/healthz | HTTP | 仅内网 |
| 2379 | etcd（Milvus 元数据） | HTTP | 容器网络内，不对外 |
| 9000 | MinIO（Milvus 对象存储） | HTTP | 容器网络内，不对外 |
| 9092 | Kafka | TCP | 仅内网 |
| 6379 | Redis | TCP | 仅内网 |
| 9200 | Elasticsearch | HTTP | 仅内网 |

## 15. PostgreSQL 生产化

### 15.1 账号与权限（自建库）
```sql
CREATE ROLE aiops_console LOGIN PASSWORD '<强密码>';
CREATE DATABASE aiops_console OWNER aiops_console;
GRANT ALL ON SCHEMA public TO aiops_console;   -- ORM 启动自动建表需要 DDL
```
- 连接串写入 `DATABASE_URL`（`postgresql+asyncpg://aiops_console:<密码>@<host>:5432/aiops_console`）。
- Atoms Cloud 托管库由平台注入连接串，无需手工授权。

### 15.2 Alembic 迁移
```bash
cd app/backend
alembic current              # 当前版本（应为 b8f2c1d4e5a6：users.status）
alembic upgrade head         # 升级到最新
alembic history --verbose    # 迁移历史
alembic downgrade -1         # 回退一版（执行前先备份，见 §10）
```
- ORM 自动建表与 Alembic 并存：新表由启动时 ORM 创建，结构性变更走迁移。

### 15.3 验证命令
```bash
psql "$DATABASE_URL" -c "\dt"                                          # 表清单
psql "$DATABASE_URL" -c "SELECT count(*) FROM audit_logs;"             # 行数抽查
psql "$DATABASE_URL" -c "SELECT version_num FROM alembic_version;"     # 迁移版本
python scripts/fix_sequences.py                                        # 显式 ID 导入/恢复后必执行
```

## 16. Milvus 向量库部署（etcd + MinIO + Milvus）

### 16.1 拓扑与连接参数
- 组件：etcd v3.5.14（元数据）+ MinIO RELEASE.2024-05-01（对象存储）+ Milvus v2.4.4 standalone，编排见 `aiops-rag-system/docker-compose.yml`，数据卷 `etcd_data`/`minio_data`/`milvus_data`。
- 连接参数（`aiops-rag-system/.env`）：`MILVUS_HOST=localhost`、`MILVUS_PORT=19530`、`MILVUS_COLLECTION=aiops_knowledge_base`、`MILVUS_NPROBE=32`。

### 16.2 集合与索引规格
- 集合 `aiops_knowledge_base`：主键 `case_id VARCHAR(64)`；向量字段 `embedding FLOAT_VECTOR dim=1024`（BGE-M3）；shards_num=2。
- 索引：向量 `IVF_SQ8 / COSINE / nlist=4096`；标量 TRIE 索引：`fingerprint`、`service_name`、`error_type`、`cluster`。
- 分区：按月 `p_YYYYMM`，检索固定近 3 个月分区。

### 16.3 初始化与验证
```bash
cd aiops-rag-system
docker compose up -d etcd minio milvus
docker compose ps                        # 三容器 Up
curl -s http://localhost:9091/healthz    # -> ok
python scripts/init_milvus.py            # 幂等创建集合/索引/近 3 月分区
python scripts/seed_cases.py             # 种子案例（演示环境可选）
```
- 风险操作：`python scripts/init_milvus.py --overwrite` 会删除并重建集合，生产环境禁止。

## 17. Docker Compose 一键部署（RAG 流水线）
```bash
cd aiops-rag-system
cp .env.example .env          # 至少填写 LLM_API_KEY
docker compose up -d          # etcd/minio/milvus/kafka/redis/es 全量
docker compose ps
python scripts/init_milvus.py
uvicorn src.main:app --host 0.0.0.0 --port 8001   # lifespan 自动启停 3 个消费者
```

## 18. Kubernetes 部署（控制台）

### 18.1 Secret 与 ConfigMap
```yaml
apiVersion: v1
kind: Secret
metadata: {name: aiops-console-secret}
stringData:
  DATABASE_URL: postgresql+asyncpg://aiops_console:<pwd>@pg-host:5432/aiops_console
  JWT_SECRET_KEY: "<随机 64 位字符串>"
---
apiVersion: v1
kind: ConfigMap
metadata: {name: aiops-console-config}
data:
  IS_LAMBDA: "false"
  ENVIRONMENT: "prod"
```

### 18.2 后端 Deployment（含健康检查与滚动策略）
```yaml
apiVersion: apps/v1
kind: Deployment
metadata: {name: aiops-console-backend}
spec:
  replicas: 2
  strategy: {type: RollingUpdate, rollingUpdate: {maxSurge: 1, maxUnavailable: 0}}
  selector: {matchLabels: {app: aiops-console-backend}}
  template:
    metadata: {labels: {app: aiops-console-backend}}
    spec:
      containers:
      - name: backend
        image: registry.example.com/aiops-console-backend:<tag>
        ports: [{containerPort: 8000}]
        envFrom:
        - secretRef: {name: aiops-console-secret}
        - configMapRef: {name: aiops-console-config}
        readinessProbe: {httpGet: {path: /health, port: 8000}, initialDelaySeconds: 5, periodSeconds: 10}
        livenessProbe:  {httpGet: {path: /health, port: 8000}, initialDelaySeconds: 15, periodSeconds: 20}
        resources: {requests: {cpu: 250m, memory: 512Mi}, limits: {cpu: "1", memory: 1Gi}}
```
前端 Deployment 同理（镜像内 Nginx 托管 `dist/`，SPA 回退 `try_files $uri /index.html`）。

### 18.3 Service 与 Ingress
```yaml
apiVersion: v1
kind: Service
metadata: {name: aiops-console-backend}
spec:
  selector: {app: aiops-console-backend}
  ports: [{port: 80, targetPort: 8000}]
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata: {name: aiops-console}
spec:
  ingressClassName: nginx
  tls: [{hosts: [console.example.com], secretName: console-tls}]
  rules:
  - host: console.example.com
    http:
      paths:
      - {path: /api, pathType: Prefix, backend: {service: {name: aiops-console-backend, port: {number: 80}}}}
      - {path: /, pathType: Prefix, backend: {service: {name: aiops-console-frontend, port: {number: 80}}}}
```

### 18.4 滚动升级与回滚
```bash
kubectl set image deploy/aiops-console-backend backend=registry.example.com/aiops-console-backend:<新tag>
kubectl rollout status deploy/aiops-console-backend   # 观察滚动进度，卡住即阻塞
kubectl rollout history deploy/aiops-console-backend  # 版本历史
kubectl rollout undo deploy/aiops-console-backend     # 一键回滚上一版本
```
- 升级前置：pg_dump 备份（§10）→ 确认迁移仅向后兼容新增（新增列/表无需 DB 回滚）。

## 19. 路径与目录规范
| 类别 | 路径 | 说明 |
|------|------|------|
| 后端应用 | /opt/app/backend | main.py、services、alembic、scripts |
| 前端产物 | /opt/app/frontend/dist | pnpm build 产物，Nginx 托管 |
| 运行配置 | /opt/app/backend/.env | IS_LAMBDA=false、DATABASE_URL 等（chmod 600） |
| 后端日志 | /opt/app/backend/logs/app_YYYYMMDD.log、restart.log | 按日滚动，保留 30 天 |
| 数据库 | Atoms Cloud 托管或自建 PG | 备份落 /var/backups/aiops/ |
| Milvus/etcd/MinIO 数据 | compose named volumes：milvus_data/etcd_data/minio_data | `docker volume inspect` 查实际挂载点 |
| 规则/模板配置 | aiops-rag-system/config/rules.yaml、drain_patterns.yaml | Git 管理可回溯 |
| 备份目录 | /var/backups/aiops/ | 每日 pg_dump + 配置快照，建议异地同步 |

## 20. 备份与恢复（全量矩阵）
| 对象 | 备份 | 恢复 |
|------|------|------|
| 业务数据库 | `pg_dump "$DATABASE_URL" -Fc -f /var/backups/aiops/console_$(date +%F).dump` | `pg_restore --clean --if-exists -d "$DATABASE_URL" <dump>` + `python scripts/fix_sequences.py` |
| 控制台配置 | console_configs 随库备份；另导出 `psql "$DATABASE_URL" -c "\copy console_configs TO 'configs.csv' CSV HEADER"` | 随库恢复；单配置走配置中心改回 |
| 规则/日志模板 | Git 仓库（rules.yaml、drain_patterns.yaml、logging.yaml） | `git checkout <tag>` 后规则页重发布或重启流水线 |
| 向量数据 | 方案 A：`milvus-backup` 全量备份；方案 B：案例源数据在 kb_cases 表，重建后重灌 embedding | 重建集合 `init_milvus.py` → 种子/重灌脚本；近 3 月分区自动创建 |
| 对象存储 | `mc mirror local/minio_data /var/backups/aiops/minio/` | `mc mirror` 反向回灌 |
| 每日定时 | `10 2 * * * cd /opt/app/backend && pg_dump ... && find /var/backups/aiops -mtime +30 -delete` | — |

- RPO ≤ 24h（每日全量），RTO ≤ 1h（脚本化恢复 + 序列修复 + 巡检）。审计日志（audit_logs）保留 ≥ 180 天。

## 21. 中间件架构总览（RAG 流水线）

### 21.1 架构图

```text
                 ┌────────────────── 控制台平面（/workspace/app）──────────────────┐
                 │ React 前端(3000) ──/api 代理──▶ FastAPI 后端(8000) ──▶ PostgreSQL │
                 │        认证 OIDC/JWT、AIHub 由 Atoms Cloud 托管                   │
                 └─────────────────────────────────────────────────────────────────┘
                       （业务语义衔接：事件 / 案例 / 规则 / 审批，两平面独立部署）

外部监控源（Alertmanager / Zabbix / APM / 日志平台）
        │  POST /api/v1/webhook
        ▼
┌────────────────────┐   标准化事件    ┌───────────────┐
│ FastAPI 流水线 API  │ ──────────────▶│ Kafka 9092     │
│ Drain 模板+规则分类 │   事件暂存 7 天 │ standardized-  │
│ （默认 8080/示例    │ ──▶ Redis 6379 │ events 主题    │
│   8001）            │                └───────┬───────┘
└────────────────────┘                        │ 三消费者并行
                          ┌───────────────────┼───────────────────┐
                          ▼                   ▼                   ▼
                 ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
                 │ Dedup Consumer  │  │ RAG Consumer    │  │ Storage        │
                 │ Redis 指纹去重  │  │ Milvus 双路召回 │  │ Consumer       │
                 │ 5min 窗口聚合   │  │ +综合重排       │  │ ES 9200 冷存储 │
                 │ 突发升级+ChatOps│  │ → Few-shot →LLM │  │ /审计          │
                 └────────────────┘  └────────────────┘  └────────────────┘

Milvus 支撑组件：etcd 2379（元数据） + MinIO 9000（对象存储）
控制台业务库：PostgreSQL 5432（Atoms Cloud 托管或自建，见 §15）
```

### 21.2 中间件角色与参数

| 中间件 | 镜像 / 版本 | 端口 | 架构角色 | 关键参数 | 故障影响（降级策略） |
|--------|-------------|------|----------|----------|----------------------|
| etcd | quay.io/coreos/etcd:v3.5.14 | 2379（容器网络内） | Milvus 元数据存储 | 配额 4GB（`ETCD_QUOTA_BACKEND_BYTES`）、自动压缩 | Milvus 不可用 → 诊断降级为无上下文直答 |
| MinIO | minio/minio:RELEASE.2024-05-01 | 9000（容器网络内） | Milvus 对象存储（向量数据落盘） | 默认凭证 minioadmin（内网部署，生产建议修改） | 同上 |
| Milvus | milvusdb/milvus:v2.4.4 standalone | 19530 gRPC / 9091 healthz | 向量检索：HNSW/IVF_SQ8、dim=1024（BGE-M3）、按月分区、近 3 月分区检索 | 集合 `aiops_knowledge_base`，规格见 §16.2 | 诊断降级（is_fallback=true，LLM 直答） |
| Kafka | bitnami/kafka:3.4（KRaft 单节点） | 9092 | 告警事件解耦缓冲，标准化与三消费者解耦 | 主题 `standardized-events`；PLAINTEXT 明文（内网） | 事件暂存 Redis 不丢；消费停滞需扩容/跳过 |
| Redis | redis:7.0-alpine（AOF） | 6379 | 指纹去重（TTL 防抖）、事件暂存（TTL 7 天）、CMDB 拓扑缓存（1h）、5min 窗口聚合 | `REDIS_URL=redis://localhost:6379/0` | 去重 fail-open：告警不丢但可能重复 |
| Elasticsearch | elasticsearch:8.12.2 单节点 | 9200 | 冷存储与审计缓冲（标准化事件归档） | heap 512MB（`ES_JAVA_OPTS`）、无安全插件（内网） | 仅冷存储/审计缺失，主链路不受影响 |
| PostgreSQL | Atoms Cloud 托管或自建 | 5432 | 控制台业务库（11 张业务表 + users） | 连接串 `DATABASE_URL`，见 §15 | 控制台不可用；流水线不受影响 |

### 21.3 数据流说明

1. 外部监控系统调用 `POST /api/v1/webhook`，标准化引擎完成 Drain 模板提取 + 规则分类（目标 P99 < 10ms），生成 `event_id` 与指纹。
2. 事件同步写入 Redis（TTL 7 天，供人工诊断 / 告警闭环查询）并投递 Kafka `standardized-events`。
3. **Dedup Consumer**：Redis 指纹 SETNX 防抖 + 5 分钟窗口计数，突发自动升级 severity 后推送 ChatOps。
4. **RAG Consumer**：BGE-M3 向量化 → Milvus 近 3 月分区双路召回 → 余弦/拓扑/时间衰减/反馈综合重排 → Few-shot Prompt → LLM 强制 JSON 输出（超时/异常自动降级）。
5. **Storage Consumer**：事件写入 ES 冷存储索引（ES 故障时内存缓冲后丢弃，仅审计）。
6. 人工反馈（`POST /api/v1/feedback`）回写 Milvus 案例分数影响重排；告警关闭（`POST /api/v1/cases/close`）将根因/方案写入知识库形成闭环。
7. 控制台平面通过自身 PostgreSQL 与 AIHub 提供人工运营、审批、规则治理；与流水线中间件相互独立。

## 22. 中间件部署指令速查（RAG 流水线）

### 22.1 一键部署

```bash
cd /workspace/aiops-rag-system
cp .env.example .env                 # 至少填写 LLM_API_KEY（OpenAI 兼容接口）
docker compose up -d                 # etcd / minio / milvus / kafka / redis / elasticsearch
docker compose ps                    # 6 个容器全部 Up
```

### 22.2 分步启动（首次部署建议，便于定位问题）

```bash
cd /workspace/aiops-rag-system

# 1) Milvus 三件套：先起元数据与对象存储，再起 Milvus
docker compose up -d etcd minio
docker compose up -d milvus
docker compose ps milvus             # State=Up
curl -s http://localhost:9091/healthz   # 期望输出：ok

# 2) 消息与缓存
docker compose up -d kafka redis elasticsearch
redis-cli ping                                        # 期望：PONG
curl -s http://localhost:9200/_cluster/health         # status: green/yellow

# 3) 初始化向量库并灌入种子案例
pip install -r requirements.txt
python scripts/init_milvus.py        # 幂等：集合/索引/近 3 月分区（生产禁止 --overwrite）
python scripts/seed_cases.py         # 10 条典型故障 SOP（演示环境）

# 4) 启动流水线 API（lifespan 自动拉起/停止 3 个 Kafka 消费者）
uvicorn src.main:app --host 0.0.0.0 --port 8001
```

### 22.3 部署后验证

```bash
curl -s http://localhost:8001/healthz                       # 存活探针 -> {"status":"ok"}
curl -s http://localhost:8001/readyz                        # -> {"status":"ok","checks":{"kafka":true,"redis":true,"milvus":true}}；任一依赖不可用对应 checks 为 false，fail-open 不摘除实例

# Webhook 冒烟（告警接入 → 标准化 → Kafka）
curl -s -X POST http://localhost:8001/api/v1/webhook \
  -H "Content-Type: application/json" \
  -d '{"source":"apm","raw_message":"redis.clients.jedis.exceptions.JedisConnectionException: connection timeout","labels":{"service":"order-service","cluster":"prod","severity":"3"},"timestamp":1700000000000}'
# 期望：{"status":"success","event_id":"evt_...","error_type":"...","confidence":0.xx}

# Kafka 主题与消费组
docker compose exec kafka kafka-topics.sh --list --bootstrap-server localhost:9092
docker compose exec kafka kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --all-groups

# 指标与日志
curl -s http://localhost:8001/metrics | head            # Prometheus 指标
docker compose logs -f milvus kafka redis elasticsearch  # 各中间件日志
```

### 22.4 停止与清理

```bash
docker compose stop            # 停止容器（保留数据卷）
docker compose start           # 重新启动
docker compose down            # 删除容器与网络（保留数据卷）
docker compose down -v         # ⚠️ 连同 etcd_data/minio_data/milvus_data 数据卷一并删除，生产禁止
```

### 22.5 流水线 API 常驻（systemd）

```ini
# /etc/systemd/system/aiops-pipeline.service
[Unit]
Description=AIOps RAG Pipeline API
After=docker.service network-online.target
Requires=docker.service

[Service]
WorkingDirectory=/opt/aiops-rag-system
EnvironmentFile=/opt/aiops-rag-system/.env
ExecStart=/opt/aiops-rag-system/.venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8001
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now aiops-pipeline
journalctl -u aiops-pipeline -f
```

### 22.6 环境变量（`.env`，完整模板见 `aiops-rag-system/.env.example`）

| 变量 | 说明 | 默认/示例 |
|------|------|-----------|
| `LLM_API_KEY` | **必填**，OpenAI 兼容接口密钥 | `sk-xxxx` |
| `LLM_BASE_URL` / `LLM_MODEL` | LLM 服务地址与模型 | DeepSeek 示例见模板 |
| `MILVUS_HOST` / `MILVUS_PORT` / `MILVUS_COLLECTION` | 向量库连接 | localhost / 19530 / aiops_knowledge_base |
| `EMBEDDING_MODEL_PATH` / `EMBEDDING_DIM` | Embedding 模型（BGE-M3），留空走降级向量 | BAAI/bge-m3 / 1024 |
| `KAFKA_BOOTSTRAP_SERVERS` / `KAFKA_TOPIC_STANDARDIZED` | Kafka 连接与主题 | localhost:9092 / standardized-events |
| `REDIS_URL` | Redis 连接 | redis://localhost:6379/0 |
| `ES_HOST` / `ES_INDEX` | ES 冷存储 | http://localhost:9200 / aiops-events |
| `SERVICE_PORT` | 流水线 API 端口 | 8080（部署示例 8001，避开控制台 8000） |
