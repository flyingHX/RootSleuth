# AIOps 运营控制台运维手册（Runbook）

> 面向值班与 SRE 的应急处置手册。部署细节见《OPERATIONS_DEPLOYMENT_GUIDE.md》。

## 1. 服务巡检（每日）

| 检查项 | 命令/入口 | 期望 |
|--------|-----------|------|
| 后端健康 | `curl http://localhost:8000/health` | 200 |
| 前端可达 | 浏览器打开控制台首页 | 登录页/总览正常渲染 |
| 待办积压 | 总览「待办」卡片 或 `GET /api/v1/console/dashboard` | pending_approvals / pending_unknowns 无持续增长 |
| LLM 健康 | 总览「依赖健康」llm 项 | healthy；degraded 时查诊断降级原因 |
| 审计连续性 | 审计日志页按时间倒序 | 当日关键操作均有对应审计记录 |

## 2. 常见告警场景与处置

### 2.1 审批/晋升接口 500（主键冲突）
- **根因**：显式 ID 种子/导入后 PostgreSQL 序列未同步。
- **处置**：
  ```bash
  cd app/backend && python scripts/fix_sequences.py
  ```
- **验证**：重新发起晋升或审批，返回 200；审计出现对应记录。

### 2.2 AI 诊断批量超时（llm_timeout）
- **处置**：sys_admin → 配置中心 → `llm_timeout_seconds` 调大（如 45 → 90，上限 300）→ 重试一次诊断。
- **仍失败**：检查平台 AIHub 状态；期间告警查看不受影响，可先用 RAG 召回案例处置。

### 2.3 紧急变更需要免审
- **处置**：sys_admin → 配置中心 → `approval_mode=OFF` → 提交变更直接发布。
- **恢复**：事件处置完成后切回 `SINGLE_REVIEW`，并核查 OFF 期间的 `kb_publish` 审计记录。

### 2.4 误发布知识/规则
- **知识案例**：知识库 → 案例详情 → 版本历史 → 回滚到上一版本（生成新版本，秒级生效）。
- **规则**：规则管理 → 版本列表 → 回滚到上一个版本（重新激活并热加载）。
- 两类回滚均写审计，无需数据库操作。

### 2.5 RAG 无召回 / 未知率升高
- 查看「规则管理 → 未知模板」队列：样本量大的模板及时晋升（填写目标 error_type → 审批）。
- 知识库确认目标错误类型存在 `active` 案例；被合并归档的案例不再召回，可在案例列表切「已归档」核实。

### 2.6 前端访问 401/白屏
- 401：登录态过期，重新登录；确认浏览器 localStorage `token` 存在。
- /api 404：Vite 代理目标端口与后端实际端口不一致（`BACKEND_PORT` 环境变量）。
- 白屏：确认后端 `/health` 正常、前端构建产物完整。

### 2.7 诊断质量跌破 0.85 告警线（P1-2）
- **口径**：Trust Index `T = 0.4×Faithfulness + 0.35×Citation Accuracy + 0.25×(1−幻觉率)`，7 天滚动均值 < **0.85** 触发质量告警；Golden Set 门禁评测（P@1/MRR/HitRate）跌破门禁同样视为破线。
- **观测入口**：总览「诊断质量态势」卡片、`GET /api/v1/console/dashboard` 的 `quality` 聚合、RAG 侧 `/metrics` 的 Golden Set / 生成质量 / Trust Index 指标（`rag_golden_*`、`rag_generation_*` 等，以实际输出为准；Prometheus 告警规则阈值按 0.85 配置）。
- **处置**：
  1. Events 页定位低分诊断（Faithfulness / 引用准确性），核对其候选案例是否过时或含错误方案；
  2. 知识治理：Kb 页修正或归档问题案例，必要时在 Ops 页「知识健康」报表确认红黄绿分布与老化风险；
  3. 检索质量排查：复跑 `aiops-rag-system/evaluation/` Golden Set 评测，确认 P@1 / MRR 回落原因（Embedding 变更、Milvus 索引重建、租户过滤调整）；
  4. 处置后观察 24h，Trust Index 回到 ≥ 0.85 视为闭环；关键处置记审计。

## 3. 数据维护

### 3.1 演示数据重置
```bash
cd app/backend
python scripts/seed_console_demo.py   # 追加演示数据
python scripts/fix_sequences.py       # 必须执行
```

### 3.2 备份（建议每日）
```bash
pg_dump "$DATABASE_URL" -Fc -f console_backup_$(date +%F).dump
```
审计日志保留 ≥ 180 天。

### 3.3 恢复
```bash
pg_restore --clean --if-exists -d "$DATABASE_URL" <dump 文件>
python scripts/fix_sequences.py
```

## 4. 升级窗口流程

1. 通知用户 + 冻结审批操作。
2. 执行数据库备份（§3.2）。
3. 拉取新版本代码，安装依赖（后端 uv / 前端 pnpm）。
4. 重启后端（生产去 `--reload`），前端重新 build。
5. 巡检（§1）全部通过后解除冻结。
6. 回滚预案：切回上一版本代码重启；数据层面优先用控制台内置版本回滚。

## 5. 升级/值班联系矩阵

| 事项 | 责任角色 | 操作入口 |
|------|----------|----------|
| 审批积压催办 | 审批人（approver+） | 审批中心 |
| 审批模式/阈值调整 | sys_admin | 配置中心 |
| 规则发布/回滚 | sys_admin | 规则管理 |
| 案例回滚/合并 | kb_admin+ | 知识库 |
| 数据库/序列/部署 | 运维 | 服务器 + scripts/ |
| 平台登录/AIHub 异常 | 平台方 | Atoms 平台支持 |

## 6. 应急速查卡

```
序列冲突   -> python scripts/fix_sequences.py
紧急免审   -> 配置中心 approval_mode=OFF（事后切回）
误发布     -> 控制台内版本回滚（知识/规则均支持）
诊断超时   -> 配置中心 llm_timeout_seconds 调大
后端起不来 -> 看 logs/restart.log + logs/app_YYYYMMDD.log
前端 /api 404 -> 核对 BACKEND_PORT 与 Vite 代理端口
账号封禁   -> 用户管理页禁用，即时 403 生效
密钥泄露   -> 见 §10.1 立即轮换 JWT_SECRET_KEY
Milvus 挂了 -> 诊断自动降级；docker compose restart milvus + healthz
```

## 7. 日志定位

| 服务 | 位置 | 内容 |
|------|------|------|
| 控制台后端 | `app/backend/logs/app_YYYYMMDD.log` | 启动、请求、异常堆栈（按日滚动） |
| 控制台重启 | `app/backend/logs/restart.log` | 重启与崩溃记录 |
| systemd 守护 | `journalctl -u aiops-console -f` | uvicorn stdout/stderr |
| 容器化后端 | `docker logs -f <container>` | 同上 |
| 前端/Nginx | `/var/log/nginx/access.log`、`/var/log/nginx/error.log` | 访问与代理错误 |
| RAG 流水线 | `docker compose logs -f <svc>` 或 uvicorn stdout | Webhook/消费者日志 |
| Milvus | `cd aiops-rag-system && docker compose logs milvus` | 集合加载、检索错误 |
| etcd / MinIO | `docker compose logs etcd` / `logs minio` | 元数据、对象存储错误 |
| Kafka / Redis / ES | `docker compose logs kafka` / `redis` / `elasticsearch` | 各自运行日志 |

日志保留建议：后端应用日志 ≥ 30 天，审计相关查询记录 ≥ 180 天；容器 stdout 接入宿主轮转（json-file max-size 50m × 5 或日志采集）。

## 8. 依赖故障处置矩阵

> 设计原则：RAG 流水线所有外部依赖静默降级（fail-open），单点故障不丢告警，仅降低诊断质量。各中间件的架构角色、数据流与完整部署/验证指令见《OPERATIONS_DEPLOYMENT_GUIDE.md》§21~§22。

| 依赖 | 典型症状 | 诊断命令 | 处置 |
|------|----------|----------|------|
| PostgreSQL | 后端 500、连接拒绝 | `pg_isready -d "$DATABASE_URL"`；`psql -c "SELECT pid, state, wait_event FROM pg_stat_activity;"` | 连接池耗尽→重启后端；长事务→`SELECT pg_terminate_backend(<pid>)`；磁盘满→清理 WAL/扩容 |
| Milvus | 检索超时/无召回、诊断降级 | `curl http://localhost:9091/healthz`；`docker compose ps milvus` | `docker compose restart milvus`；仍失败→检查 etcd/MinIO 后 `docker compose restart etcd minio milvus` |
| etcd | Milvus 起不来，日志报 mvcc/quota | `docker compose logs etcd \| grep -i quota` | 配额满（4GB）→ 紧急压缩+碎片整理；数据损坏→恢复卷后重启 |
| MinIO | Milvus 报对象存储错误 | `docker compose logs minio`；`curl http://localhost:9000/minio/health/live` | 磁盘满→扩容/清理；凭证错误→核对 compose 环境 |
| Redis | 去重失效、告警重复 | `redis-cli ping`；`redis-cli info memory` | 内存满→调 maxmemory/LRU；拒绝连接→`docker compose restart redis` |
| Kafka | 事件停滞、消费积压 | `docker compose logs kafka`；消费组 lag 脚本 | 积压→扩消费者/临时跳过；broker 挂→`restart kafka`（KRaft 单节点） |
| Elasticsearch | 冷存储缓冲失败 | `curl http://localhost:9200/_cluster/health` | 磁盘 watermark 触发只读→清旧索引/扩盘后 `_settings` 解除只读；heap 85%→调 ES_JAVA_OPTS |

**通用步骤**：先 `docker compose ps` 看容器状态 → 看对应日志定位根因 → 单服务重启 → 全链路健康验证（§9）→ 复盘写审计。

## 9. 健康检查、资源耗尽与网络异常

### 9.1 健康检查清单
```bash
curl http://localhost:8000/health                          # 控制台后端 -> 200
curl http://localhost:8001/health                          # RAG 流水线 API
curl http://localhost:9091/healthz                         # Milvus -> ok
curl http://localhost:9200/_cluster/health                 # ES -> status green/yellow
redis-cli ping                                             # Redis -> PONG
pg_isready -h <pg-host> -p 5432                            # PostgreSQL -> accepting
```
K8s 环境由 readiness/liveness probe 自动执行（部署手册 §18.2）；Nginx 侧可加 `/health` 被动探测。

### 9.2 资源耗尽
| 资源 | 判断 | 处置 |
|------|------|------|
| CPU 持续 >90% | `top`/`kubectl top pods` | 扩副本/扩容；定位热点（多为 LLM 等待与重排） |
| 内存 OOM | `dmesg \| grep -i oom`；容器重启次数 | 调大 limits；ES/Milvus 堆内控制 |
| 磁盘 >85% | `df -h`；PG/Milvus 数据卷 | 清日志、清旧备份、WAL/索引扩容（PG 磁盘满是常见根因） |
| 文件描述符 | `lsof -p <pid> \| wc -l` vs `ulimit -n` | systemd `LimitNOFILE=65535` |

### 9.3 网络异常
1. 前端 502/504：Nginx → 后端端口（`BACKEND_PORT`）是否存活，`curl 127.0.0.1:8000/health` 直连验证。
2. 后端连不上托管 DB：`telnet <pg-host> 5432` 验证出网/内网策略，核对 `DATABASE_URL`。
3. OIDC 登录跳转失败：核对回调域名与 Atoms OIDC 应用配置一致，HTTPS 证书有效（`openssl s_client`）。
4. Milvus gRPC 不通：容器网络内 `nc -zv localhost 19530`；确认未被防火墙拦截。

## 10. 安全应急

### 10.1 密钥轮换
| 密钥 | 步骤 | 影响 |
|------|------|------|
| JWT_SECRET_KEY | ① K8s Secret/环境变量更新为新值 → ② 滚动重启后端 | 全部在线会话失效，用户重新登录（约分钟级） |
| 数据库密码 | ① PG `ALTER ROLE aiops_console PASSWORD '<新>'` → ② 更新 Secret → ③ 滚动重启后端 | 短暂连接失败，回滚用旧密码重启 |
| LLM_API_KEY | 控制台配置中心更新（Fernet 加密存储）+ 保存后点「连通性测试」 | 即时生效，无重启 |

- 密钥泄露应急：视同轮换流程执行，并在审计日志核查泄露窗口内异常操作；禁止把密钥写入代码/仓库。

### 10.2 账号封禁与权限恢复
- **即时封禁**：sys_admin → 用户管理 → 选中账号 → 禁用。禁用后所有在途请求立即 403（依赖层每次请求查库校验，不等 JWT 过期）。
- **解禁**：同一入口启用即可恢复，无需重置 Token。
- **权限回收/变更**：用户管理编辑角色绑定（如 sys_admin → approver），保存即时生效；`role_bindings_json` 按邮箱精确匹配。
- **最后管理员保护**：仅剩 1 名 sys_admin 时禁用/降级均返回 400——这是预期防护，先提升新的 sys_admin 再操作。
- **越权配置防护**：`default_role` 已强制拒绝 `sys_admin`（后端校验 400 + 角色解析回退 viewer），历史危险配置无需人工排查。
- **审计核查**：审计日志页按操作者/时间过滤；或 `psql "$DATABASE_URL" -c "SELECT created_at, user_email, action, detail FROM audit_logs WHERE created_at > now() - interval '24 hours' ORDER BY created_at DESC;"`。重点动作：`user_disable`、`user_role_change`、`kb_publish`、`rule_reload`、`config_update`。

## 11. 备份验证与灾难恢复

### 11.1 备份验证（每月）
```bash
# 1) 恢复到临时库演练
createdb aiops_restore_test
pg_restore --clean --if-exists -d aiops_restore_test console_backup_<latest>.dump
# 2) 抽查关键表行数与最新审计时间
psql aiops_restore_test -c "SELECT count(*) FROM kb_cases; SELECT max(created_at) FROM audit_logs;"
# 3) 清理
dropdb aiops_restore_test
```
- 检查备份文件非空（`ls -lh /var/backups/aiops/`）且最近 7 天连续；向量库备份用 `milvus-backup list` 验证快照链。

### 11.2 灾难恢复流程（目标 RTO ≤ 1h，RPO ≤ 24h）
1. 判定范围：单服务故障走 §8 矩阵重启；数据损坏/整库丢失才执行本节。
2. 隔离故障环境（停止写入，防止脏数据扩大）。
3. 重建基础设施：`docker compose up -d` → `python scripts/init_milvus.py`（幂等）。
4. 恢复数据库：`pg_restore --clean --if-exists -d "$DATABASE_URL" <最新dump>` → `python scripts/fix_sequences.py`。
5. 对象存储回灌：`mc mirror /var/backups/aiops/minio/ local/minio_data`。
6. 启动后端/流水线，执行 §9.1 全量健康检查 + 控制台人工抽检（登录、总览、审批、诊断）。
7. 在审计日志补记一条 `config_update`（备注灾难恢复时间与恢复点），复盘并更新本手册。

### 11.3 数据回滚
- 优先级 1：控制台内置版本回滚（知识案例/规则版本），秒级生效且自带审计。
- 优先级 2：单表级误操作——从最近 dump 中仅导出该表比对修复（`pg_restore -t <表>`），避免整库覆盖。
- 优先级 3：整库回滚 `pg_restore --clean`（冻结操作窗口，通知用户）。
- 禁止：在无备份的情况下手工 UPDATE/DELETE 生产表。

## 12. 应急联系人矩阵

| 场景 | 第一响应 | 升级路径 | 联系入口 |
|------|----------|----------|----------|
| 控制台不可用/登录异常 | 值班 SRE | 运维负责人 | 值班群 / oncall 表 |
| 数据库故障/数据丢失 | 运维 | DBA + 平台方（Atoms Cloud 托管库） | 值班群 + 平台工单 |
| RAG 流水线/Milvus 故障 | 值班 SRE | 运维负责人 | 值班群 |
| 安全事件（越权/泄露/异常审计） | 值班 SRE | 安全负责人（1h 内上报） | 安全应急群 |
| AIHub/平台能力异常 | 值班 SRE | Atoms 平台支持 | 平台工单 |
| 审批积压/业务规则 | 审批人（approver+） | sys_admin 调整模式 | 控制台审批中心 |
