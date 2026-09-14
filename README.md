# AIOps 智能告警 RAG 知识库系统（项目总览）

面向 SRE 的智能告警诊断与知识运营平台，由两个独立部署的子系统组成：

1. **RAG 告警诊断流水线**（`aiops-rag-system/`，纯 Python 后端）：
   告警 Webhook 接入 → Drain 日志标准化 → Kafka 解耦 → Redis 去重聚合 → Milvus 向量检索 + 业务重排 → LLM 根因诊断 → 人工反馈知识闭环。
2. **运营控制台**（`app/`，Atoms 平台托管项目）：
   React + Vite 前端 + FastAPI 后端 + Atoms Cloud 托管 PostgreSQL/认证/AIHub，提供告警工作台、AI 诊断、知识库、审批中心、规则管理、Agent 工作台、审计与配置等页面。

两个子系统**独立部署、独立端口**，通过业务语义（事件 / 案例 / 规则 / 审批）衔接：流水线负责诊断与知识生产，控制台负责人工运营与治理。

## 目录与文件关系

> 结论（2026-09-12 确认）：**各目录保持现状，不做迁移**；`docs/` 已上移至仓库根目录，作为整个项目的文档目录。

| 目录 / 文件 | 角色 | 说明 |
|-------------|------|------|
| `aiops-rag-system/` | RAG 流水线子系统 | Python 3.10 + FastAPI + LangChain；自带 `docker-compose.yml`（etcd/MinIO/Milvus/Kafka/Redis/ES）、pytest 测试（30 passed）、初始化与种子脚本 |
| `app/` | 运营控制台子系统（Atoms 平台托管） | 平台预览服务、受保护入口（`backend/main.py` 等）与 `.mgx/config.yaml` 绑定此路径，不可迁移；一键启动 `bash app/start_app_v2.sh` |
| `docs/` | 项目级文档（整个项目共用） | 控制台使用手册、FAQ、运维部署方案手册、运维 Runbook |
| `.atoms/` | 平台团队协作上下文 | `ATOMS.md`（决策/约束）、`ARCHITECTURE.md`（架构）、`PROGRESS.md`（进度）+ 平台 Skills；由平台与团队共同维护 |
| `uploads/` | 平台文件上传暂存区 | 用户上传的临时文件，平台管理 |
| `.wiki.md` | 平台 Wiki 摘要 | 自动维护的项目概览/模块/目录树/**接口文档**（面向外部系统调用方） |
| `.git` | Git 仓库指针 | 指向平台 gitdata，由平台管理 |
| 根目录 `verify_*.py`、`debug_*.py`、`*_tmp.py` | 验收 / 调试工作脚本 | 历史轮次的一次性验证脚本（API 回归、权限、Agent、LLM 配置等），保留在根目录 |
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
| `.wiki.md`「API Documentation」 | 接口文档：RAG 流水线对外接口（外部监控系统调用）与控制台后端接口 |
| `aiops-rag-system/README.md` | 流水线架构、快速开始、API 一览、关键设计 |
| `app/backend/README.md` / `app/frontend/README.md` | 控制台前后端开发规范（平台模板） |

## 开源与安全

本仓库按开源标准维护，公开发布前的敏感文件审计结论与操作步骤见 [docs/OPEN_SOURCE_RELEASE_CHECKLIST.md](docs/OPEN_SOURCE_RELEASE_CHECKLIST.md)。

- **许可证**：[Apache-2.0](LICENSE)；贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，行为准则见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- **安全**：安全漏洞请勿公开上报，按 [SECURITY.md](SECURITY.md) 的私密渠道提交；部署必改项（`DATABASE_URL`、`JWT_SECRET_KEY`、`MASK_KEY` 等）同文档。
- **环境变量**：仓库仅保留脱敏模板——`aiops-rag-system/.env.example`（RAG 流水线）、`app/backend/.env.example` 与 `app/frontend/.env.example`（控制台）；真实 `.env`、密钥、日志、安装包（`aiops-suite-*.tar.gz`）与临时脚本一律不入库（规则见根 `.gitignore`）。
- **CI**：`.github/workflows/ci.yml` 在 push/PR 时自动运行 RAG pytest（Fake 桩免中间件）、控制台后端语法检查、前端 ESLint + 生产构建。
