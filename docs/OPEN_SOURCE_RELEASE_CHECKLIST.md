# 开源发布检查清单（Open Source Release Checklist）

本文档记录将本仓库发布为公开仓库前的必做事项。安全审计结论与文件清单见下。

## 一、安全审计结论（2026-09-13）

| 类别 | 文件 / 内容 | 结论 |
|------|-------------|------|
| 硬编码密钥 | `app/backend/core/mask_crypto.py` 内置默认 `MASK_KEY` 回退 | 已在 [SECURITY.md](SECURITY.md) 声明为已知设计注意事项，生产必须显式设置 `MASK_KEY`；后续版本计划移除回退 |
| 演示令牌 | `.demo_admin_token.tmp` | **未进入 Git 跟踪与历史**，本地文件可删除 |
| 运行日志 | `restart.log`、`app/backend/logs/` | 未被跟踪；`.gitignore` 已覆盖 `*.log`、`logs/` |
| 安装包 | `aiops-suite-1.0.0.tar.gz`（1.2M） | **仍被 Git 跟踪**，需解除跟踪（见下），改走 GitHub Release 附件 |
| 平台上传图片 | `uploads/*.png`（2 张） | **仍被 Git 跟踪**，且存在于历史提交；未发现隐私敏感内容，但默认不入库 |
| 临时脚本 | 根目录 12 个 `*_tmp.py`（verify/debug/fix/restart） | **仍被 Git 跟踪**；为历史轮次一次性验证脚本，默认不开源 |
| JWT 字面量 | 全仓 `eyJ...` 扫描 | 未发现任何真实 JWT 字面量 |
| 云厂商密钥 | `sk-`/`AKIA`/`ghp_` 扫描 | 仅 `.env.example` 占位符命中，真实密钥未入库 |
| Git 历史 | `git log --all --name-only` 扫描 | 敏感文件（令牌/日志/env）**均未进入历史**；仅 `.wiki.md` 与 2 张 uploads 图片存在于历史 |

## 二、发布前必做（需在具备 Git 写权限的环境执行）

本沙箱中 Git 索引由平台托管（`/run/gitdata` 为只读文件系统），以下命令需推送到 GitHub 前在可写环境中执行：

```bash
# 1. 解除已跟踪但不开源的文件（保留本地文件，仅移出索引）
git rm -r --cached aiops-suite-*.tar.gz
git rm -r --cached uploads
git rm --cached *_tmp.py
git commit -m "chore: 移除安装包、上传暂存与临时脚本的开源跟踪"

# 2. 全仓密钥复扫（任选其一）
gitleaks detect --source . --report-path gitleaks-report.json
trufflehog filesystem .

# 3. 确认忽略规则生效（应列出文件）
git check-ignore -v aiops-suite-1.0.0.tar.gz uploads/x.png verify_agent_tmp.py .demo_admin_token.tmp

# 4. 若需从历史中彻底抹除 uploads 图片（可选，默认无需执行——未含敏感内容）
# git filter-repo --path uploads --invert-paths

# 5. 创建公开仓库并推送，安装包以 Release 附件分发
gh release create v1.0.0 aiops-suite-1.0.0.tar.gz --notes "..."
```

> 说明：`*_tmp.py` 为历史验收脚本。若个别脚本有复用价值（如 `verify_agent_tmp.py` 的三 Agent 链路验证），可整理进 `scripts/` 并去掉 `_tmp` 后缀后再入库。

## 三、本次已补充的开源文件

| 文件 | 内容 |
|------|------|
| `LICENSE` | Apache-2.0 |
| `CONTRIBUTING.md` | 双子系统本地开发、测试、提交规范、PR 要求 |
| `SECURITY.md` | 漏洞上报渠道、SLA、范围说明、部署安全要点 |
| `CODE_OF_CONDUCT.md` | Contributor Covenant 2.1 中文版 |
| `.gitignore`（根） | 补齐 `*_tmp.py`、`uploads/`、`aiops-suite-*.tar.gz`、`.demo_admin_token.tmp`、`.pkg_stage/`、`.pytest_cache/`、`.mgx/` 等；**移除了对 `pnpm-lock.yaml` 的误忽略**（锁文件应入库） |
| `aiops-rag-system/.gitignore` | 补齐 `.env.*`、`.pytest_cache/`、`.mypy_cache/` 等 |
| `app/backend/.env.example` | 控制台后端全部环境变量模板（DATABASE_URL/JWT/MASK_KEY/连接池等） |
| `app/frontend/.env.example` | 前端环境变量模板（VITE_API_BASE_URL 等） |
| `.github/workflows/ci.yml` | 三作业 CI：RAG pytest（Fake 桩免中间件）、控制台后端 compileall、前端 lint+build |

## 四、分发与第三方说明

- **安装包**：`aiops-suite-*.tar.gz` 不入 Git，发布时作为 GitHub Release 附件；每次重建后以最新 SHA-256 为准（当前 `2a6324f035c0b0ca032f239cd0326a4a940d785de2da47ccce237c9500b5e2b7`）。
- **演示凭证**：`docs/DEMO_ACCOUNTS.md` 保留入库——内容为演示环境专用账号与中间件默认值，文档内已声明生产必须全部修改；已在 SECURITY.md 上报范围中显式豁免。
- **第三方模型/API**：RAG 流水线通过 OpenAI 兼容接口接入 LLM（默认示例 DeepSeek），密钥仅存 `.env`；控制台内置 AIHub 的模型配置加密存储于数据库。开源分发不含任何模型权重与 API 配额。
- **依赖许可**：Python 依赖见各 `requirements.txt`（均为 MIT/Apache/BSD 系宽松许可）；前端依赖见 `app/frontend/package.json` 与 `pnpm-lock.yaml`。

## 五、Markdown 文档审计（2026-09-13）

| 文档 | 审计结论 | 处理建议 |
|------|----------|----------|
| `.atoms/` 全部 md（ATOMS/PROGRESS/ARCHITECTURE/SKILLS/skills） | 平台协作上下文与内部技能文档 | **不入库**——未被 Git 跟踪且已被 `.gitignore` 排除，无需处理 |
| `app/backend/skills_docs/`（4 篇） | Atoms 平台模板内置的能力文档（web-sdk/AIHub/对象存储/自定义 API、mgx-pycheck），对开源用户无意义且暴露平台内部工具链 | **不入库**——已加入 `.gitignore`；可写环境执行 `git rm -r --cached app/backend/skills_docs` |
| `app/backend/README.md`、`app/frontend/README.md` | 平台模板 README，含 `mgx-pycheck`、`data-mgx-overview`、"Welcome to Atoms" 等平台指令内容 | 发布前**改写**为面向使用者的后端/前端说明（技术栈、目录、启动方式），移除平台工作流段落 |
| 根 `README.md` | 目录关系表引用 `.atoms/`、`.mgx/config.yaml` 与「Atoms 平台托管」字样，而 `.atoms/`、`.mgx/` 实际不入库，链接语义悬空 | 发布前将目录表中两行改为「平台协作目录（不入库）」或直接删除该两行 |
| `docs/OPEN_SOURCE_RELEASE_CHECKLIST.md` | 含沙箱内部细节（`/run/gitdata` 只读文件系统等） | 发布前将「本沙箱中 Git 索引由平台托管」一句改为通用表述（如「若 Git 索引只读/由托管环境管理」） |
| `docs/DEMO_ACCOUNTS.md` | 演示邮箱与中间件默认凭证（minioadmin 等），均已在文中声明生产必改；无真实密钥 | 保留入库（见「四、分发与第三方说明」）；如希望零凭证文档可改为不入库 |
| `docs/CONSOLE_FAQ.md`、`docs/CONSOLE_USER_GUIDE.md` | 提及预览环境演示账号（与 DEMO_ACCOUNTS 同源），无密码字段 | 保留 |
| `.wiki.md`、`docs/OPERATIONS_*.md`、`CONTRIBUTING/SECURITY/CODE_OF_CONDUCT`、`aiops-rag-system/README.md` | 全文核查无真实凭证、无内网 IP（仅 0.0.0.0/127.0.0.1 示例地址） | 保留 |
| `aiops-rag-system/.pytest_cache/README.md` | pytest 自动生成缓存 | 已被 `.gitignore` 排除，未跟踪 |

## 六、发布后维护

- CI（`.github/workflows/ci.yml`）推送/PR 自动运行三套检查。
- 新增密钥类环境变量时同步更新对应 `.env.example` 与本清单。
- 定期（如每季度）重跑 `gitleaks` 复扫。
