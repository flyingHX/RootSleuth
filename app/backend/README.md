# RootSleuth 运营控制台后端

[English](README.en.md) | 简体中文

> 项目定位：本目录是 RootSleuth 运营控制台的 FastAPI 后端。项目总览与目录关系见仓库根目录 `README.md`，使用手册见 `docs/CONSOLE_USER_GUIDE.md`，运维部署见 `docs/OPERATIONS_DEPLOYMENT_GUIDE.md`。

## 技术栈

- Python 3.10+ / FastAPI / SQLAlchemy（async）/ pydantic-settings
- 数据库：PostgreSQL（Alembic 迁移管理）
- 认证：OIDC + JWT（预览/演示环境提供免密 demo-login）
- 测试：pytest

## 目录结构

```
app/backend/
├── main.py            # 后端启动入口
├── routers/           # API 路由（自动发现，统一挂载 /api/v1/ 前缀）
├── services/          # 业务逻辑（Agent、知识库、规则、审批、审计等）
├── models/            # ORM 模型
├── schemas/           # Pydantic 请求/响应模型
├── dependencies/      # 依赖注入（鉴权、数据库会话等）
├── middlewares/       # 中间件
├── core/              # 配置与运行时基础设施
├── alembic/           # 数据库迁移
├── tests/             # pytest 测试
├── scripts/           # 运维与验证脚本
└── requirements.txt
```

## 本地开发

```bash
cd app/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 按注释填写 DATABASE_URL、JWT_SECRET_KEY、MASK_KEY 等
uvicorn main:app --host 0.0.0.0 --port 8000
```

一键启动前后端（自动分配端口）：仓库根目录执行 `bash app/start_app_v2.sh`。

## 运行测试

```bash
python -m pytest tests/ -v
```

## API 约定

- 业务路由统一挂载在 `/api/v1/` 前缀下，`routers/` 目录自动发现注册。
- 健康检查：`/health`。
- 接口清单与调用方式见 `docs/CONSOLE_USER_GUIDE.md` 与部署手册。
