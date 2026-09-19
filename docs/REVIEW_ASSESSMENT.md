# RootSleuth 项目评审意见评估报告

> 评估基准：2026-09-18 代码主分支；评审意见来源：第三方架构评审。
> 结论口径：**采纳**（本轮已落地）/ **分期**（列入路线图，明确优先级）/ **暂不采纳**（保留现状，附理由与重启条件）。
> 配套落地说明：`docs/OPERATIONS_DEPLOYMENT_GUIDE.md` §24（混合 LLM 路由与合规审计）、§25（数据分级与入站脱敏）。

## 一、评估总览

| # | 评审意见 | 结论 | 本轮状态 |
|---|----------|------|----------|
| 1 | 混合 LLM 分级路由（敏感数据不出域） | 采纳 | ✅ 本轮落地 |
| 2 | LLM 调用合规审计哈希链 | 采纳 | ✅ 本轮落地 |
| 3 | 入站数据脱敏管道 | 采纳 | ✅ 本轮落地 |
| 4 | 中间件精简（Milvus/Kafka/ES 替换） | 仅评估，暂不动代码 | 📋 评估结论见 §三 |
| 5 | 配置统一配置中心（去 pydantic-settings 化） | 暂不采纳 | 📋 评估结论见 §四 |
| 6 | LangChain 替换（原生 SDK 化） | 暂不采纳，持续观望 | 📋 评估结论见 §五 |
| 7 | 高可用 / K8s 化 / API 网关 | 分期（P1/P2） | 📋 路线图见 §六 |

## 二、本轮采纳项（已落地并固证）

### 2.1 混合 LLM 分级路由 ✅

**评审意见**：金融行业场景下，敏感告警数据不得出境/出域，远程 LLM 调用需合规管控。

**采纳理由**：与项目定位（金融行业·本地分布式·混合 LLM）直接一致，属于合规硬需求而非可选项。

**落地位置**：

- 路由决策模块：`app/backend/services/llm_routing.py`（三维决策：数据敏感度 → 告警等级 → 服务等级）；
- 单轮诊断挂载：`app/backend/services/console_ai.py`（每次 LLM 调用前路由，含确定性降级与 `llm_invocation` 审计）；
- 深度诊断 Agent 挂载：`app/backend/services/console_agent.py`（ReAct 全链路 route 透传）；
- 配置中心键：`llm_local_base_url` / `llm_local_model` / `llm_local_api_key` / `llm_routing_policy` / `llm_remote_approval_id`（`app/backend/services/console_common.py`）；
- 本地 LLM 经 OpenAI 兼容接口接入（Ollama / vLLM），不引入新 SDK 依赖。

**红线语义**（专项测试固证：`tests/test_llm_routing_compliance.py`）：

- 敏感数据强制本地；本地不可用时降级为确定性结论（复用知识库候选），**绝不回退远程**；
- `llm_remote_approval_id` 留空时任何策略下的远程路由都被拒绝（双保险在 `decide_llm_route` 末尾）；
- `remote_only` 策略下敏感数据仍强制本地（红线优先于策略）。

### 2.2 LLM 调用合规审计哈希链 ✅

**评审意见**：LLM 调用需可追溯、防篡改，满足金融审计要求。

**采纳理由**：哈希链以极低成本（无新表、无新中间件）满足防篡改与可追溯诉求，性价比高。

**落地位置**：

- 复用现有 `audit_logs` 表，链字段存 `after_json.chain`（`prev_hash` / `hash`），**不改表结构**；
- 链构造：`hash = sha256(prev_hash | actor | action | target_type | target_id | after 规范化 JSON)`；
- 覆盖动作：`llm_route_decision`（路由决策）与 `llm_invocation`（LLM 调用），常量 `COMPLIANCE_CHAIN_ACTIONS`（`console_common.py`）；
- 防篡改校验端点：`GET /api/v1/console/audit-logs/chain-verify`（sys_admin），返回 `ok/total/head_hash/broken_id`，篡改时定位首条断链记录；
- 审计内容仅含敏感类别标签与路由元信息，不含原始告警文本。

### 2.3 入站数据脱敏管道 ✅

**评审意见**：外部推送告警含 PII/凭据，原文落库存在合规风险。

**采纳理由**：脱敏应在数据入口完成（入口治理优于出口补救）；默认开启符合金融行业"默认安全"原则。

**落地位置**：

- 掩码与检测：`app/backend/services/console_common.py`（`mask_alert_text` / `detect_sensitivity`）；
- 接入点：`app/backend/routers/ingest.py`（`POST /api/v1/ingest/alerts` 落库前掩码）；
- 开关：`data_masking_enabled`（默认 `true`，**原始内容不落库**；显式关闭时 `event_ingest` 审计记录该选择以供追溯）；
- 覆盖类别：身份证 / 手机号 / 银行卡（15~19 位长数字）/ 内网 IP / 密钥赋值 / Bearer Token / 邮箱 / 长令牌（≥32 位），共 8 类；
- 与路由联动：掩码占位符保留可识别格式，`detect_sensitivity` 可对已落库文本二次判定并驱动 LLM 路由敏感度维度；
- 保留项：公网 IP 与主机名不掩码（诊断与 CMDB 关联需要）。

### 2.4 前端与测试配套 ✅

- 前端 OpsPage 新增「混合 LLM 路由」「数据分级与脱敏」两个配置分组，i18n 双语文案（zh-CN / en-US），`console-api.ts` 类型同步（`DiagnosisResult.diagnosis.route`）；
- 专项测试 `app/backend/tests/test_llm_routing_compliance.py` 15 项：路由决策矩阵 / 敏感不出域（零 LLM 调用与走本地）/ 脱敏逐类与二次识别 / 哈希链写入与防篡改 / ingest 默认脱敏与关闭保留原文 / 诊断失败路由审计仍落库；
- 既有测试适配：`tests/test_agent_repeat_consistency.py` 按新语义调整（种子 IP 改公网、critical 强制本地后的 model 期望）。

## 三、中间件精简：仅评估，暂不动代码 📋

**评审意见**：为降低运维成本，建议替换重中间件：Milvus → Qdrant（可移除 etcd + MinIO）、Kafka → Redis Streams、Elasticsearch → Loki。

**评估结论**：方向正确、收益真实，但替换是全局性改造，本轮仅输出评估结论，**不改代码**。重启条件：完成 P1 高可用改造或单独立项。

### 3.1 Milvus → Qdrant

| 维度 | 评估 |
|------|------|
| 运维收益 | 高：可移除 etcd + MinIO 两个依赖组件（Qdrant 单二进制/单容器），组件数从 3 → 1，故障域显著缩小 |
| 改造成本 | 中高：pymilvus 调用面较大（集合管理/双路召回/upsert/反馈统计/租户 expr 过滤/Schema 交集兼容），需整体重写 `milvus_client.py` 与 `documents.py`；标量过滤 expr 语法需逐条改写为 Qdrant filter |
| 兼容风险 | 中：租户隔离（tenant_id expr）、Schema 交集降级、kb_version 版本守卫等语义需在 Qdrant payload 过滤上等价重建并重新专项测试 |
| 性能 | 检索规模（万级案例）下两者均足够；Milvus 在更大规模与 GPU 索引上有优势，本项目当前用不到 |
| 结论 | **列入 P2 路线图**。触发条件：P1 完成后或运维痛点（etcd/MinIO 故障）实际发生时立项。改造时保留 `documents.py` 的 row↔Document 桥接层，仅替换底层客户端，四层重排与检索接口契约不变 |

### 3.2 Kafka → Redis Streams

| 维度 | 评估 |
|------|------|
| 运维收益 | 中高：告警流水线仅用 Kafka 做事件解耦，吞吐要求低（每秒个位数事件），Redis Streams + 消费组即可满足；若 Redis 已在栈内则为净减组件 |
| 改造成本 | 中：生产者/消费者语义映射直接（XADD/XREADGROUP/XACK），需重做偏移管理与重试语义 |
| 兼容风险 | 低：事件链路无事务/乱序强需求，at-least-once + 幂等去重（event_id）已具备 |
| 结论 | **列入 P2 路线图**。注意：Redis 需开启 AOF 持久化避免事件丢失；现有 Kafka 部署可继续使用，无强制迁移时间表 |

### 3.3 Elasticsearch → Loki

| 维度 | 评估 |
|------|------|
| 运维收益 | 中：Loki 资源占用远低于 ES（仅索引标签不索引全文），但本项目日志检索依赖低——控制台审计走 PostgreSQL，诊断上下文走知识库 |
| 改造成本 | 低：仅影响部署模板与日志采集侧（Promtail），应用代码几乎不动 |
| 兼容风险 | 低 |
| 结论 | **列入 P2 路线图（优先级低于 3.1/3.2）**。若部署方已有 ES 且运行稳定，不建议为替换而替换 |

### 3.4 总体权衡

- 三个替换的共性收益是**降低自托管运维成本**，与用户"关注运维成本"的诉求一致；
- 共性代价是**一次性改造与回归成本**，且替换期间双写/灰度会短暂抬高复杂度；
- 决策：本轮保持中间件现状（功能正确性优先），替换项全部进入路线图并写明触发条件，避免无收益期的提前改造。

## 四、配置统一配置中心：暂不采纳 📋

**评审意见**：配置管理应全部收敛到配置中心，去除散落的 pydantic-settings / .env。

**评估结论**：**暂不采纳**，维持"配置中心运行时下发 + .env/pydantic-settings 启动引导"的双层现状。

理由：

1. **职责不同**：.env 承载的是启动引导（数据库连接串、Redis/Kafka/Milvus 地址、RAG 鉴权 Key 等基础设施参数），配置中心承载的是运行时业务参数（诊断温度/预算/路由策略/脱敏开关/审批模式等）。前者变更需重启进程，后者要求"修改即时生效"，二者本就该分层；
2. **RAG 子系统独立部署**：`aiops-rag-system/` 是独立 FastAPI 进程，可能部署在无控制台数据库可达的网络分区，强制其运行时配置走控制台配置中心会引入反向依赖与单点；
3. **改造成本与收益不成比**：收敛需要迁移全部 .env 读取点、重做 fail-open 默认值语义（如 `RAG_AUTH_ENABLED=false` 的兼容逻辑）并回归全部测试，而当前双层模型没有产生实际的配置漂移问题；
4. **重启条件**：当出现"同一参数两处配置且语义冲突"的真实事故，或配置中心需要反向下发基础设施参数时，再立项统一。

## 五、LangChain 替换：暂不采纳，持续观望 📋

**评审意见**：LangChain 抽象层重、版本变动快，建议替换为各厂商原生 SDK 直调。

**评估结论**：**暂不采纳**（用户明确要求观望，与团队判断一致）。

理由：

1. **抽象收益仍在兑现**：四层重排的 `BaseRetriever` / `BaseDocumentCompressor` / LCEL 链让 L1-L4 可独立组合、独立降级、独立测试；L4 Listwise 与诊断链的 LCEL 管道（`ChatPromptTemplate | ChatOpenAI | JsonOutputParser`）代码量约为原生实现的一半；
2. **迁移面大且无紧迫痛点**：`src/rag_pipeline/` 内 llm_client / prompt_builder / retriever / reranker_l4 / pipeline 五个模块深度耦合 LCEL；替换后需重写提示词编排、流式与 JSON 解析、超时熔断语义，并回归 132 项 RAG 测试，而当前版本（0.3.x 固定上限）运行稳定；
3. **风险对冲已就位**：`requirements` 已固定 LangChain 0.3.x 上限；`BaseDocumentCompressor` 做了双位置兼容导入（`documents.compressor` 优先、`documents.base` 回退），历史上未因上游 minor 升级破坏；
4. **观望触发条件**（满足其一时重新评估）：a) 升级到新 major 版本导致兼容层失效；b) 出现仅原生 SDK 可用的关键能力（如官方侧的确定性结构化输出/缓存语义）；c) LCEL 在高并发下的性能瓶颈被实测证实。

## 六、分期路线图（高可用 / K8s / API 网关）📋

| 优先级 | 事项 | 内容要点 | 前置条件 |
|--------|------|----------|----------|
| P1 | 高可用改造 | PostgreSQL 主备 + 读写分离；Redis 哨兵；Kafka 多副本；控制台/RAG 多副本 + 无状态化（会话外置）；诊断任务去重锁 | 现有功能稳定运行 |
| P1 | API 网关 | 统一入口（路由/限流/鉴权卸载/审计头注入）；ingest 与 web 链路隔离限流；Graylog/审计日志汇聚 | P1 高可用完成或多副本就绪 |
| P2 | K8s 化 | Helm Chart / Kustomize；HPA（诊断 Worker 按 Kafka lag 扩缩容）；PodDisruptionBudget；探针对齐 /healthz /readyz | P1 完成 |
| P2 | 中间件替换 | 按本文 §三评估结论逐项立项（Milvus→Qdrant 优先） | K8s 化完成后收益最大 |
| P2 | 多租户增强 | 租户级配置覆盖（在配置中心键之上叠加租户维度默认值） | 多租户真实需求出现 |

## 七、验证与固证

- 专项测试：`app/backend/tests/test_llm_routing_compliance.py` 15 项全过；后端全量 pytest 53 passed；`python -m py_compile` 通过；
- 前端回归：eslint + vite build 零错误；
- E2E：敏感告警（卡号/手机号）经 ingest 推送 → `raw_log` 仅存掩码占位符 → 诊断触发 `llm_route_decision` 审计（route=local、sensitive=true）→ `chain-verify` 校验 `ok=true`；
- 双语文档：`docs/OPERATIONS_DEPLOYMENT_GUIDE.md(.en.md)` §24/§25、`docs/CONSOLE_USER_GUIDE.md(.en.md)` 配置表与新章节同步。
