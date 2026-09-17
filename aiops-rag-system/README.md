# RootSleuth — 智能告警 RAG 知识库系统

基于 **LangChain 架构** 的 AIOps 告警诊断系统：告警接入 → 日志标准化（Drain 模板）→ Kafka 解耦 → Redis 去重聚合 → Milvus 向量检索 + 业务重排 → LLM 生成根因分析 → 人工反馈闭环自进化。

> 本目录是 RootSleuth 的 **RAG 告警诊断流水线** 子系统；运营控制台位于 `../app`。项目总览与目录关系见仓库根目录 `README.md`，项目级文档见 `../docs/`（使用手册、FAQ、运维部署与 Runbook）。

## 系统架构

```
Alertmanager/Grafana Webhook
        │
        ▼
┌──────────────────┐    Kafka: standardized-events
│ FastAPI Webhook  │────────────────────────────┐
│ (标准化引擎)      │                            ▼
└──────────────────┘                  ┌─────────────────────┐
        │                             │ Dedup Consumer      │
        │ 事件暂存 (60s)               │ Redis 指纹去重/聚合   │──▶ ChatOps 推送
        ▼                             └─────────────────────┘
┌──────────────┐                              │ (可通过诊断接口补触发)
│ Redis Client │                              ▼
└──────────────┘                  ┌─────────────────────┐
                                  │ RAG Consumer        │
                                  │ Embedding → Milvus  │
                                  │ 双路召回 → 重排       │
                                  │ → Few-shot → LLM    │
                                  └─────────────────────┘
                                              │
        ┌─────────────────────────────────────┤
        ▼                                     ▼
┌──────────────┐                    ┌─────────────────────┐
│ ES Consumer  │                    │ 人工诊断 / 反馈 API   │
│ 冷存储/审计   │                    │ 案例闭环 (score±1)   │
└──────────────┘                    └─────────────────────┘
```

## 核心特性

| 特性 | 说明 |
|------|------|
| 日志标准化 | YAML 规则热加载 + Drain 模板提取 + LRU 缓存 + 插件机制（突发检测等） |
| 指纹去重 | `md5(source\|service\|error_type)` 指纹，Redis TTL 防抖 + 5 分钟窗口计数，突发自动升级 severity |
| RAG 检索 | BGE-M3 Embedding → Milvus HNSW 检索（按月分区）→ 四层业务重排漏斗压缩为 Top-3 |
| LLM 诊断 | LangChain ChatOpenAI 兼容客户端，Few-shot Prompt 强制 JSON 输出，超时熔断降级 |
| 知识闭环 | 告警解决后案例写入知识库；人工反馈 ±1 分影响重排序；季度近重复清理 |
| 可观测性 | Prometheus 指标（标准化/RAG/去重）、/metrics 暴露、结构化日志、健康检查 |

## 项目结构

```
aiops-rag-system/
├── config/
│   ├── rules.yaml            # 告警分类规则（支持热更新）
│   ├── drain_patterns.yaml   # Drain 模板变量替换规则
│   └── logging.yaml          # 统一日志配置
├── scripts/
│   ├── init_milvus.py        # 幂等初始化 Milvus 集合/索引/分区
│   ├── seed_cases.py         # 灌入 10 条种子知识案例
│   └── cleanup_cases.py      # 季度近重复案例清理（支持 --dry-run）
├── src/
│   ├── config.py             # 配置加载（.env / 环境变量）
│   ├── runtime.py            # 运行时依赖容器
│   ├── main.py               # FastAPI 入口（lifespan 管理消费者启停）
│   ├── kafka_producer.py     # Kafka 事件生产者
│   ├── kafka_consumers/      # dedup / rag / storage 三类消费者
│   ├── standardization/      # 规则加载、Drain 提取、插件、标准化引擎
│   ├── rag_pipeline/         # embedder / milvus / reranker / prompt / llm / pipeline
│   ├── storage/              # redis_client / es_client
│   ├── models/               # RawAlert / StandardizedEvent / KnowledgeCase / 响应模型
│   └── utils/                # logger / metrics
├── tests/                    # 标准化 / RAG / API / 消费者 / 存储测试
├── docker-compose.yml        # Milvus / Kafka / Redis / ES / etcd / MinIO
├── requirements.txt
├── .env.example
└── pytest.ini
```

## 快速开始

### 1. 启动基础设施

```bash
docker compose up -d
# 包含：etcd、minio、milvus、kafka(zookeeper)、redis、elasticsearch
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env：LLM_API_KEY 必填（OpenAI 兼容接口，如 DeepSeek）
```

### 3. 初始化知识库并灌入种子案例

```bash
pip install -r requirements.txt
python scripts/init_milvus.py      # 创建集合/索引/分区
python scripts/seed_cases.py       # 灌入 10 条典型故障 SOP
```

### 4. 启动服务

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000
# 启动时自动拉起 3 个 Kafka 消费者，关闭时优雅停止
```

### 5. 发送测试告警

```bash
curl -X POST http://localhost:8000/api/v1/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "source": "apm",
    "raw_message": "redis.clients.jedis.exceptions.JedisConnectionException: connection timeout",
    "labels": {"service": "order-service", "cluster": "prod", "severity": "3"},
    "timestamp": 1700000000000
  }'
```

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/webhook` | 告警接入：标准化 → Kafka → Redis 暂存 |
| POST | `/api/v1/diagnostic` | 人工诊断：按 event_id 触发 RAG 全链路 |
| POST | `/api/v1/feedback` | 案例反馈评分（+1 有用 / -1 无用），影响重排 |
| POST | `/api/v1/cases/close` | 告警解决闭环：写入知识库案例 |
| GET  | `/healthz` | 存活探针 |
| GET  | `/readyz` | 就绪探针（Kafka/Redis/Milvus 状态） |
| GET  | `/metrics` | Prometheus 指标 |

## 运维脚本

```bash
python scripts/cleanup_cases.py --dry-run   # 预览近重复案例（余弦 > 0.99）
python scripts/cleanup_cases.py             # 执行删除（保留 feedback 分高者）
```

## 测试

```bash
python -m pytest tests/ -v
```

## 四层业务重排

召回 Top-20 经四层漏斗压缩为 Top-3（实现于 `src/rag_pipeline/`，层间数据载体为 LangChain `Document`）：

| 层 | 组件 | 漏斗 | 机制 |
|----|------|------|------|
| L1 | `reranker_l1.RuleFilter` | 20 → ~15 | 规则硬过滤：过期 / 黑名单 / 低反馈 / 废弃关键词案例剔除 |
| L2 | `reranker_l2.BusinessReranker` | ~15 → 10 | 七特征加权融合：语义、拓扑、时间、反馈（贝叶斯平滑）、模板 Jaccard、新鲜度、命中率 |
| L3 | `reranker_l3.BGEReranker` | 10 → 5 | BGE-Reranker Cross-Encoder 逐对精排；FlagEmbedding 不可用时自动透传降级 |
| L4 | `reranker_l4.LLMListwiseReranker` | 5 → 3 | LLM Listwise 全局排序（LCEL 链）：幻觉 ID 校验 + 遗漏补全 + rank→score 归一化 |
| 融合 | `rerank_pipeline.fuse_final_scores` | — | 可用层权重重归一化（0.2/0.4/0.4），任一层失效不塌缩 |

- **逐层降级**：LLM 失败回退 L3 顺序、Cross-Encoder 失败回退 L2、全部失效仅保留 L1 结果；L4 在后台线程执行并施加独立超时（`RERANK_L4_TIMEOUT_SECONDS`），超时不阻塞主流水线。
- **LangChain 生态**：L2/L3 实现为 `BaseDocumentCompressor`（可直接挂入 `ContextualCompressionRetriever`），检索侧包装为 `BaseRetriever`（`retriever.MilvusEventRetriever`），L4 与诊断为 LCEL 链（`ChatPromptTemplate | ChatOpenAI | JsonOutputParser`）。
- **反馈闭环**：`/api/v1/feedback` 维护 upvotes/downvotes（派生 feedback_score 兼容字段），召回时回写 hit_count/recall_count，分别驱动 L2 的 f4 反馈分与 f7 命中率。
- **可观测性**：`/metrics` 暴露层级延迟 `rerank_layer_latency_seconds{layer}`、各层降级计数 `rerank_degraded_total{layer}` 与最终输出数 `rerank_final_total`。

## 关键设计

- **降级策略**：Milvus 不可用 → LLM 无上下文直答；LLM 超时 → 返回 Top 相似案例；Embedding 失败 → 确定性降级向量；Redis 故障 → 去重 fail-open（保告警不丢）；ES 故障 → 内存缓冲后丢弃（仅审计）。
- **规则热更新**：修改 `config/rules.yaml` 后自动检测 mtime 热加载，坏配置回退内存快照。
- **Prompt 安全**：用户日志经模板化变量替换后注入，LLM 输出强制 JSON Schema 解析，解析失败自动降级。

## P99 长尾治理

### 分阶段延迟监控

`/metrics` 暴露 `rag_stage_latency_seconds`（Histogram，按 `stage` 标签），覆盖
`embedding` / `milvus_search` / `rerank` / `llm` 四个阶段；结合端到端
`rag_search_latency_seconds` 与 `rag_search_total{status}`（success/llm_timeout/fallback 等）
可将长尾定位到具体阶段。推荐 PromQL（P99）：

```promql
histogram_quantile(0.99, sum(rate(rag_stage_latency_seconds_bucket[5m])) by (stage, le))
```

### 已实施的优化

| 环节 | 风险 | 优化措施 |
|------|------|----------|
| Embedding | API 慢/挂导致每请求等满超时（默认 10s） | 连续失败熔断（`EMBEDDING_FAIL_THRESHOLD`/`EMBEDDING_CIRCUIT_SECONDS`），窗口内直接走确定性降级向量；结果 LRU 缓存；本地模型后台预热，冷启动不阻塞请求 |
| Milvus | 每次检索重复 has_collection RPC；ANN 无超时 | 集合句柄 TTL 缓存（`MILVUS_COLLECTION_CACHE_TTL`）；`search` 超时（`MILVUS_SEARCH_TIMEOUT`）+ `nprobe` 可配置 |
| 重排 | 候选过多拖慢业务重排 | `RAG_TOP_K` 召回数与 `RAG_FINAL_K` 送 LLM 案例数可配置收敛 |
| LLM | 底层重试放大阻塞（timeout×(retries+1)）；线程池排队 | 默认 `LLM_MAX_RETRIES=0`，端到端预算统一由 `RAG_LLM_TIMEOUT_SECONDS` 熔断；线程池容量 `RAG_LLM_MAX_WORKERS` 可调 |
| 人工诊断 | async 路由内同步 RAG 阻塞事件循环 | 改为同步 `def` 路由，FastAPI 自动放线程池执行 |
| Kafka 消费 | dict 事件传入 `pipeline.search` 静默失败 | `search` 兼容 Pydantic 模型与 dict 两种输入 |

超时降级语义保持不变：LLM 超时/异常时立即返回 Top1 相似案例（`is_fallback=true`），不丢弃已就绪的检索结果。

### 压测验证步骤

1. 启动基础设施与真实 Embedding/LLM 服务，灌入种子案例；
2. 以并发梯度（如 1/5/20/50）压 `/api/v1/diagnostic`，采集 `/metrics`；
3. 比对各阶段 P50/P95/P99 与端到端 P99，确认长尾集中在预期阶段；
4. 按上表调参（优先 `RAG_TOP_K`、`RAG_LLM_MAX_WORKERS`、`MILVUS_NPROBE`）后复测对比。

