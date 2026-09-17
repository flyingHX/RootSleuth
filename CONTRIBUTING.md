# 贡献指南（CONTRIBUTING）

感谢关注 RootSleuth（智能告警 RAG 知识库系统）！欢迎通过 Issue 反馈问题、通过 Pull Request 提交代码。请在参与前先阅读 [行为准则](CODE_OF_CONDUCT.md)。

## 项目组成

仓库包含两个独立部署的子系统：

| 子系统 | 目录 | 技术栈 |
|--------|------|--------|
| RAG 告警诊断流水线 | `aiops-rag-system/` | Python 3.10 + FastAPI + LangChain + Milvus/Kafka/Redis/ES |
| 运营控制台 | `app/frontend/` + `app/backend/` | React + Vite + shadcn/ui；FastAPI + SQLAlchemy + Alembic |

## 环境要求

- Python 3.10
- Node.js 18+ 与 pnpm
- Docker（可选，用于本地编排 RAG 中间件）
- PostgreSQL（控制台后端）

## 本地开发与测试

### RAG 流水线

```bash
cd aiops-rag-system
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # 至少填写 LLM_API_KEY（仅联调时需要）
pytest                          # 无需真实中间件，外部依赖使用 Fake 桩
```

涉及中间件联调时：`docker compose up -d` → `python scripts/init_milvus.py` → `python scripts/seed_cases.py` → `uvicorn src.main:app --port 8001`。

### 控制台后端

```bash
cd app/backend
pip install -r requirements.txt
cp .env.example .env            # 修改 DATABASE_URL / JWT_SECRET_KEY
uvicorn main:app --reload --port 8000
```

### 控制台前端

```bash
cd app/frontend
pnpm install
pnpm run lint                   # ESLint 必须无错误
pnpm run build                  # Vite 生产构建必须通过
```

也可以使用一键启动脚本：`bash app/start_app_v2.sh`（自动分配端口对）。

## 提交规范

- 提交信息使用中文或英文均可，格式建议：`<类型>: <简要描述>`，类型取 `feat` / `fix` / `docs` / `refactor` / `test` / `chore`。
- 一个 Pull Request 聚焦一个主题，避免混杂无关改动。

## Pull Request 要求

1. Fork 仓库并从 `main` 拉出功能分支。
2. 改动涉及前后端时，两侧的 lint / 构建 / pytest 必须全部通过。
3. 新增功能请补充对应测试（RAG 子系统使用 pytest；前端页面改动需通过构建验证）。
4. 禁止提交任何密钥、令牌、`.env` 实际文件、日志与构建产物；环境变量一律通过 `.env.example` 模板声明（见根目录 `.gitignore` 的入库约定）。
5. 提交前自查：`git status` 确认无临时脚本（`*_tmp.py`）、日志、缓存文件混入。

## 安全问题

请勿以公开 Issue 形式报告安全漏洞，请按 [SECURITY.md](SECURITY.md) 中的私密渠道上报。
