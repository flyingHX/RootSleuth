<p align="center">
  <img src="assets/images/rootsleuth-repo-banner-magnifier-ecg.png" alt="RootSleuth — 侦探放大镜洞察告警根因，心电图脉搏象征持续运行的知识闭环" width="920"/>
</p>

# RootSleuth — 智能告警 RAG 知识库系统（项目总览）

面向 SRE 的智能告警诊断与知识运营平台，由两个独立部署的子系统组成：

1. **RAG 告警诊断流水线**（`aiops-rag-system/`，纯 Python 后端）：
   告警 Webhook 接入 → Drain 日志标准化 → Kafka 解耦 → Redis 去重聚合 → Milvus 向量检索 + 业务重排 → LLM 根因诊断 → 人工反馈知识闭环。
2. **运营控制台**（`app/`）：
   React + Vite 前端 + FastAPI 后端，提供告警工作台、AI 诊断、知识库、审批中心、规则管理、Agent 工作台、审计与配置等页面。

两个子系统**独立部署、独立端口**，通过业务语义（事件 / 案例 / 规则 / 审批）衔接：流水线负责诊断与知识生产，控制台负责人工运营与治理。

## 核心功能

### 告警工作台
- 告警事件统一接入与流转：Webhook 告警 → 聚合去重 → 待处理 / 已诊断 / 降级 / 未知状态全景跟踪，支持按服务、错误类型与时间维度检索。
- 一键 AI 深度诊断：ReAct 多轮工具推理 + 完整证据链追溯，诊断结论自动回写事件；重跑诊断时旧结论保留为历史会话，不丢审计线索。
- 质量可视化：每条诊断附置信度与 Trust Index 质量卡，明确区分"模型主观把握度"与"客观证据质量"两个口径。

### Agent 平台（三类运维 Agent）
- **深度诊断 Agent**：ReAct 引擎驱动多轮工具调用（本地知识检索 / RAG 召回 / 日志采样 / CMDB 查询等），支持并行工具、独立格式纠错预算与墙钟预算保护；temperature=0 全链路固定采样并持久化输入/上下文指纹，同一事件重复诊断结果可复现、波动可归因。
- **知识治理 Agent**：自动聚类高频相似告警 → AI 起草知识案例 → 提交审批流 → 生成合并提案，起草质量逐条按 Trust Index 口径评估，低质起草自动拦截。
- **值班报告 Agent**：基于 CMDB 的影响面聚合 + AI ChatOps 值班报告，报告 grounding 质量评估随报告落库，LLM 不可用时确定性降级兜底。
- 三类 Agent 的会话轨迹、工具调用明细与 token usage 全量持久化，可逐轮回放审计。

### 知识库
- 知识案例全生命周期管理：创建、编辑、审批、发布、归档；发布后自动向 RAG 向量索引同步并做 upsert+verify 验证，失败进入补偿队列（指数退避重试 / 死信隔离 / 人工重放）。
- 版本守卫：内容变更而版本更低时返回 409 拒绝，杜绝乱序补偿导致的"知识回退"；同内容重试幂等短路，不堆积无效任务。
- 去重合并：同错误类型 + 同服务且模板相似度 ≥80% 才成组给出合并建议，在途合并提案覆盖的案例自动排除，拒绝/撤回后恢复可扫。

### 规则管理
- 告警聚类与知识召回过滤规则集中管理，规则版本化发布，每次诊断快照所命中规则版本，变更全程留痕审计。
- 规则与流水线联动：RAG 检索 L1 规则硬过滤（过期 / 黑名单 / 差评 / 废弃方案剔除）与治理聚类均基于受管规则执行，规则即代码、可审计、可回滚。

### 知识健康
- 红黄绿风险报表：同步验证、内容安全、新鲜度老化（90 天负反馈口径 + 180 天无条件老化阈值）三维度聚合，Trust Index 与健康率总览一目了然。
- 内容安全卡点：18 类投毒规则（密钥泄露 / PII / 危险命令 / 提示词注入）在知识入库前扫描拦截，扫描量 / 拦截量 / 误报放行留痕均落指标。
- 风险案例闭环处置：同步死信一键重放、未确认同步重试、老化巡检先预览后批量归档（归档同步删除向量索引并写审计）、高风险内容回知识库重审；处置操作需 kb_admin 及以上权限，操作后报表与总览自动刷新。

### Agent 评测
- 生成质量评测：faithfulness / citation / 幻觉率启发式比对，Trust Index = 0.4×Faithfulness + 0.35×Citation + 0.25×(1−幻觉率)，0.85 为告警线。
- 检索质量评测：Golden Set v1（50 条标注）实测 P@1 / P@5 / R@10 / R@20 / MRR / HitRate，四层业务重排漏斗效果可量化回归。
- 全链路可观测：质量指标经 Prometheus Gauges 暴露趋势，诊断 / 治理 / 值班三类 Agent 会话统一质量口径，长期漂移可追踪。

## 目录与文件关系

| 目录 / 文件 | 角色 | 说明 |
|-------------|------|------|
| `aiops-rag-system/` | RAG 流水线子系统 | Python 3.10 + FastAPI + LangChain；自带 `docker-compose.yml`（etcd/MinIO/Milvus/Kafka/Redis/ES）、pytest 测试（30 passed）、初始化与种子脚本 |
| `app/` | 运营控制台子系统 | React + Vite 前端 + FastAPI 后端；一键启动 `bash app/start_app_v2.sh` |
| `docs/` | 项目级文档（整个项目共用） | 控制台使用手册、FAQ、运维部署方案手册、运维 Runbook |
| `assets/` | 仓库视觉素材 | README Banner 与 Logo（放大镜 + 心电图脉搏主题） |
| `.github/` | CI 工作流 | push / PR 自动运行 RAG pytest、后端语法检查、前端 lint + build |
| `app/start_app_v2.sh` | 控制台一键启动脚本 | 自动分配端口对（后端 8000 起、前端 3000 起）并安装依赖 |

## 快速开始

```bash
# ① 控制台（前端 3000 + 后端 8000，自动避让）
bash app/start_app_v2.sh

# ② RAG 流水线（中间件编排 + API，默认端口取 .env SERVICE_PORT，部署示例 8001）
cd aiops-rag-system
cp .env.example .env          # 至少填写 LLM_API_KEY
docker compose up -d          # etcd / minio / milvus / kafka / redis / elasticsearch
python scripts/init_milvus.py # 幂等创建集合/索引/分区
python scripts/seed_cases.py  # 灌入种子知识案例
uvicorn src.main:app --host 0.0.0.0 --port 8001
```

## 文档索引

| 文档 | 内容 |
|------|------|
| `docs/CONSOLE_USER_GUIDE.md` | 控制台使用手册（角色、页面、操作流程） |
| `docs/CONSOLE_FAQ.md` | 控制台常见问题 |
| `docs/OPERATIONS_DEPLOYMENT_GUIDE.md` | 运维部署方案手册（含中间件架构 §21、部署指令 §22） |
| `docs/OPERATIONS_RUNBOOK.md` | 值班应急手册（故障处置矩阵、备份恢复、安全应急） |
| `docs/DEMO_ACCOUNTS.md` | 演示账号说明（各角色演示账号、免密登录方式、中间件默认凭证与生产安全约束） |
| `aiops-rag-system/README.md` | 流水线架构、快速开始、API 一览、关键设计 |
| `app/backend/README.md` / `app/frontend/README.md` | 控制台前后端开发规范 |

## 开源与安全

本仓库按开源标准维护，公开发布前的敏感文件审计结论与操作步骤见 [docs/OPEN_SOURCE_RELEASE_CHECKLIST.md](docs/OPEN_SOURCE_RELEASE_CHECKLIST.md)。

- **许可证**：[Apache-2.0](LICENSE)；贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，行为准则见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- **安全**：安全漏洞请勿公开上报，按 [SECURITY.md](SECURITY.md) 的私密渠道提交；部署必改项（`DATABASE_URL`、`JWT_SECRET_KEY`、`MASK_KEY` 等）同文档。
- **环境变量**：仓库仅保留脱敏模板——`aiops-rag-system/.env.example`（RAG 流水线）、`app/backend/.env.example` 与 `app/frontend/.env.example`（控制台）；真实 `.env`、密钥、日志、安装包（`aiops-suite-*.tar.gz`）与临时脚本一律不入库（规则见根 `.gitignore`）。
- **CI**：`.github/workflows/ci.yml` 在 push/PR 时自动运行 RAG pytest（Fake 桩免中间件）、控制台后端语法检查、前端 ESLint + 生产构建。
